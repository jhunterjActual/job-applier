import html
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from playwright.sync_api import sync_playwright
from google import genai
from config import get_gemini_api_key
from database import get_db_connection
from tailor import analyze_job_match, analyze_job_matches_batch
from datetime import datetime

MIN_MATCH_SCORE = 40
MAX_JOB_DESCRIPTION_CHARS = 50_000
TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer", "source",
}
PROVIDER_DOMAINS = {
    "greenhouse": "greenhouse.io",
    "lever": "lever.co",
    "ashby": "ashbyhq.com",
    "smartrecruiters": "smartrecruiters.com",
}
INVALID_JOB_TEXT = (
    "internet explorer 11 is no longer supported",
    "consent to cookies",
    "job not found",
    "no longer active",
    "no longer available",
    "position closed",
    "job is closed",
    "page not found",
    "access denied",
)


def canonicalize_job_url(url: str) -> str:
    """Normalize a web posting URL while preserving unknown-site identifiers."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
        default_port = 443 if scheme == "https" else 80
        display_host = f"[{host}]" if ":" in host else host
        netloc = display_host if port in {None, default_port} else f"{display_host}:{port}"
        path = "/" + "/".join(segment for segment in parsed.path.split("/") if segment)
        normalized_path = path.rstrip("/") or "/"

        provider = provider_for_url(urllib.parse.urlunsplit((scheme, netloc, normalized_path, "", "")))
        query = ""
        if provider == "unknown" and parsed.query:
            retained = [
                (key, value)
                for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
            ]
            query = urllib.parse.urlencode(sorted(retained))
        return urllib.parse.urlunsplit((scheme, netloc, normalized_path, query, ""))
    except Exception:
        return ""


def provider_for_url(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "greenhouse.io" or host.endswith(".greenhouse.io"):
        return "greenhouse"
    if host == "jobs.lever.co":
        return "lever"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host == "jobs.smartrecruiters.com":
        return "smartrecruiters"
    return "unknown"


def validate_public_http_url(url: str, dns_cache: dict | None = None) -> tuple[bool, str]:
    """Require an HTTP(S) URL whose complete DNS result stays on the public Internet."""
    canonical_url = canonicalize_job_url(url)
    if not canonical_url:
        return False, "Enter a complete HTTP or HTTPS job-posting URL."
    parsed = urllib.parse.urlsplit(canonical_url)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False, "The URL contains an invalid port."
    if port not in {80, 443}:
        return False, "Job-posting URLs must use the standard HTTP or HTTPS port."

    hostname = parsed.hostname or ""
    cache_key = (hostname, port)
    if dns_cache is not None and cache_key in dns_cache:
        return dns_cache[cache_key]
    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            result = (False, "The job-posting hostname could not be resolved.")
            if dns_cache is not None:
                dns_cache[cache_key] = result
            return result

    result = (
        (True, "")
        if addresses and all(address.is_global for address in addresses)
        else (False, "Local, private-network, and reserved addresses are not allowed.")
    )
    if dns_cache is not None:
        dns_cache[cache_key] = result
    return result


def is_public_http_url(url: str, dns_cache: dict | None = None) -> bool:
    """Boolean convenience wrapper for request-routing checks."""
    return validate_public_http_url(url, dns_cache)[0]


def _protect_browser_network(context) -> None:
    """Block redirects and subresources that leave the public HTTP(S) network."""
    dns_cache = {}

    def route_request(route, request) -> None:
        if is_public_http_url(request.url, dns_cache):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    context.route("**/*", route_request)


def _plain_text(value: object) -> str:
    """Convert JSON-LD or HTML text into a compact human-editable string."""
    if isinstance(value, list):
        value = "\n".join(_plain_text(item) for item in value)
    elif isinstance(value, dict):
        value = next((
            _plain_text(value.get(key))
            for key in ("name", "@value", "text", "value")
            if _plain_text(value.get(key))
        ), "")
    if not isinstance(value, str):
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _job_posting_json_ld(page) -> dict:
    """Return the first JobPosting object found in page JSON-LD."""
    def candidates(value):
        if isinstance(value, list):
            for item in value:
                yield from candidates(item)
        elif isinstance(value, dict):
            posting_types = value.get("@type") or []
            if isinstance(posting_types, str):
                posting_types = [posting_types]
            if "JobPosting" in posting_types:
                yield value
            if "@graph" in value:
                yield from candidates(value["@graph"])

    for element in page.query_selector_all('script[type="application/ld+json"]'):
        try:
            parsed = json.loads(element.text_content() or "")
            posting = next(candidates(parsed), None)
            if posting:
                return posting
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return {}


def _job_location_from_json_ld(posting: dict) -> str:
    def address_part(value: object) -> str:
        if isinstance(value, str):
            return _plain_text(value)
        if isinstance(value, dict):
            for key in ("name", "value", "code"):
                normalized = address_part(value.get(key))
                if normalized:
                    return normalized
        if isinstance(value, list):
            return ", ".join(part for item in value if (part := address_part(item)))
        return ""

    locations = posting.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    values = []
    for location in locations:
        if isinstance(location, str):
            values.append(_plain_text(location))
            continue
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, str):
            values.append(address)
        elif isinstance(address, dict):
            parts = []
            seen_parts = set()
            for key in ("addressLocality", "addressRegion", "postalCode", "addressCountry"):
                component = address_part(address.get(key))
                for part in component.split(","):
                    part = part.strip()
                    normalized = part.casefold()
                    if part and normalized not in seen_parts:
                        parts.append(part)
                        seen_parts.add(normalized)
            values.append(", ".join(parts))
        if not values[-1] and isinstance(location, dict):
            values[-1] = _plain_text(location.get("name"))

    if not any(values):
        requirements = posting.get("applicantLocationRequirements") or []
        if not isinstance(requirements, list):
            requirements = [requirements]
        values = [_plain_text(requirement) for requirement in requirements]
    return "; ".join(value for value in values if value)


def _format_salary_number(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _compensation_from_json_ld(posting: dict) -> str:
    """Normalize common Schema.org salary variants without exposing objects."""
    salary = posting.get("baseSalary") or posting.get("estimatedSalary")
    if isinstance(salary, (int, float)) and not isinstance(salary, bool):
        amount = _format_salary_number(salary)
        currency = _plain_text(posting.get("salaryCurrency"))
        return " ".join(value for value in (currency, amount) if value)
    if not isinstance(salary, dict):
        return ""

    currency = _plain_text(salary.get("currency")) or _plain_text(posting.get("salaryCurrency"))
    value = salary.get("value", salary)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = maximum = _format_salary_number(value)
        unit = ""
    elif isinstance(value, dict):
        minimum = _format_salary_number(value.get("minValue", value.get("minPrice")))
        maximum = _format_salary_number(value.get("maxValue", value.get("maxPrice")))
        exact = _format_salary_number(value.get("value", value.get("price")))
        minimum = minimum or exact
        maximum = maximum or exact
        unit = _plain_text(value.get("unitText"))
    else:
        return ""

    amount = f"{minimum}–{maximum}" if minimum and maximum and minimum != maximum else minimum or maximum
    if not amount:
        return ""
    unit_labels = {"HOUR": "per hour", "DAY": "per day", "WEEK": "per week", "MONTH": "per month", "YEAR": "per year"}
    unit = unit_labels.get(unit.upper(), unit.lower()) if unit else ""
    return " ".join(value for value in (currency, amount, unit) if value)[:160]


def _structured_job_metadata(posting: dict) -> dict:
    location_type = _plain_text(posting.get("jobLocationType")).lower()
    employment = _plain_text(posting.get("employmentType")).lower().replace("_", " ").replace("-", " ")
    employment_aliases = (
        ("full time", "full_time"), ("part time", "part_time"),
        ("contract", "contract"), ("temporary", "temporary"),
        ("intern", "internship"),
    )
    return {
        "work_arrangement": "remote" if "telecommute" in location_type or "remote" in location_type else "",
        "employment_type": next((normalized for marker, normalized in employment_aliases if marker in employment), ""),
        "compensation": _compensation_from_json_ld(posting),
    }


def _largest_text(page, selectors: str) -> str:
    """Return the largest non-empty matching text block, avoiding UI shells."""
    candidates = page.query_selector_all(selectors)
    texts = []
    for candidate in candidates:
        try:
            text = candidate.inner_text().strip()
            if text:
                texts.append(text)
        except Exception:
            continue
    return max(texts, key=len, default="")


def _metadata_from_text(text: str) -> dict:
    compact = " ".join((text or "").split())
    lowered = compact.lower()
    work_arrangement = "remote" if re.search(r"\bremote\b", lowered) else "hybrid" if re.search(r"\bhybrid\b", lowered) else "on_site" if re.search(r"\b(on[- ]site|in[- ]office)\b", lowered) else ""
    employment_type = next((label for label in ("full time", "part time", "contract", "temporary", "internship") if label in lowered), "")
    compensation_match = re.search(r"(?:USD\s*)?\$[\d,]+(?:\.\d+)?(?:\s*[kK])?\s*(?:[-–—]|to)\s*(?:USD\s*)?\$?[\d,]+(?:\.\d+)?(?:\s*[kK])?(?:\s*(?:per|/)\s*(?:year|hour|yr|hr))?", compact)
    return {
        "work_arrangement": work_arrangement,
        "employment_type": employment_type.replace(" ", "_") if employment_type else "",
        "compensation": compensation_match.group(0)[:160] if compensation_match else "",
    }


def provider_alerts_from_health(provider_health: dict) -> list[dict]:
    """Convert abnormal provider outcomes into safe user-facing diagnostics."""
    alerts = []
    for provider, health in provider_health.items():
        attempted = health["new_candidates"]
        if health["raw_candidates"] >= 3 and health["valid_discovered"] == 0:
            alerts.append({
                "provider": provider,
                "code": "url_format_drift",
                "message": f"{provider.title()} search results were found, but none matched the expected job URL format.",
            })
        elif attempted >= 2 and health["accepted"] == 0:
            alerts.append({
                "provider": provider,
                "code": "content_format_drift",
                "message": f"{provider.title()} returned {attempted} new candidate postings, but none matched the expected page format.",
            })
        elif health["errors"]:
            alerts.append({
                "provider": provider,
                "code": "provider_error",
                "message": f"{provider.title()} search encountered an error and may have incomplete results.",
            })
    return alerts


def is_useful_job_details(details: dict) -> bool:
    """Reject error pages and content too thin to support meaningful matching."""
    title = " ".join(str(details.get("title") or "").split())
    company = " ".join(str(details.get("company") or "").split())
    description = " ".join(str(details.get("description") or "").split())
    combined = f"{title}\n{company}\n{description}".lower()
    if not title or not company or company.lower() == "unknown company":
        return False
    if len(title) < 3 or len(title) > 180 or len(description) < 80:
        return False
    return not any(marker in combined for marker in INVALID_JOB_TEXT)


def is_specific_job_url(url: str) -> bool:
    """
    Validates if a Lever, Greenhouse, Ashby, or SmartRecruiters URL is a
    specific job posting rather than a generic boards or careers landing page.
    
    Args:
        url (str): The URL to validate.
        
    Returns:
        bool: True if it matches the specific job posting structure, False otherwise.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s]
        
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            # Greenhouse specific postings must contain 'jobs' in segments and have at least 3 segments (e.g., /company/jobs/id)
            return "jobs" in segments and len(segments) >= 3
            
        elif host == "jobs.lever.co":
            # Lever specific postings must have exactly 2 segments (e.g., /company/job-uuid)
            if len(segments) == 2:
                # Avoid standard routing endpoints if any
                return segments[1] not in ["careers", "login", "logo", "about"]
            return False
            
        elif host == "jobs.ashbyhq.com":
            # Ashby postings look like jobs.ashbyhq.com/company/job-id or ashbyhq.com/company/jobs/job-id
            return len(segments) >= 2 and segments[-1] not in ["careers", "login", "about"]
            
        elif host == "jobs.smartrecruiters.com":
            # SmartRecruiters postings look like jobs.smartrecruiters.com/company/job-id
            return len(segments) == 2 and segments[1] not in ["careers", "login"]
            
        return False
    except Exception:
        return False

def search_yahoo_jobs(keywords: str, location: str = "", diagnostics: dict | None = None) -> list:
    """
    Queries Yahoo Search (which has relaxed bot blockages) across Greenhouse,
    Lever, Ashby, and SmartRecruiters for the given keywords and location filters.
    To avoid search engine query truncation, queries are run per domain.
    
    Args:
        keywords (str): The search phrase / job title keywords.
        location (str, optional): The target location constraint.
        
    Returns:
        list: A list of dictionaries representing the found job posting URLs.
    """
    domains = list(PROVIDER_DOMAINS.values())
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
    }
    
    jobs = []
    seen_urls = set()
    
    for domain in domains:
        provider = next(name for name, value in PROVIDER_DOMAINS.items() if value == domain)
        query = f"site:{domain} {keywords}"
        if location:
            query += f" {location}"
            
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://search.yahoo.com/search?p={encoded_query}"
        
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                html = response.read().decode('utf-8')
                
                # Find all href links
                found_links = re.findall(r'href="([^"]+)"', html)
                matching_urls = []
                for link in found_links:
                    decoded = link
                    # Yahoo search result links are wrapped in redirects starting with r.search.yahoo.com
                    if "r.search.yahoo.com" in link:
                        match = re.search(r'/RU=([^/]+)', link)
                        if match:
                            decoded = urllib.parse.unquote(match.group(1))
                    
                    # Check for direct Lever, Greenhouse, Ashby, or SmartRecruiters job posting boards
                    if any(d in decoded for d in ["boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "ashbyhq.com", "smartrecruiters.com"]):
                        if "yahoo.com" not in decoded and "/embed/" not in decoded:
                            if diagnostics is not None:
                                diagnostics[provider]["raw_candidates"] += 1
                            if is_specific_job_url(decoded):
                                matching_urls.append(canonicalize_job_url(decoded))
                
                # Deduplicate and format results for this domain
                for actual_url in matching_urls:
                    if not actual_url:
                        continue
                    if actual_url not in seen_urls:
                        seen_urls.add(actual_url)
                        jobs.append({
                            "title": "Job Posting",
                            "url": actual_url,
                            "snippet": ""
                        })
                        if diagnostics is not None:
                            diagnostics[provider]["valid_discovered"] += 1
        except Exception as e:
            print(f"Error during Yahoo search for {domain}: {e}")
            if diagnostics is not None:
                diagnostics[provider]["errors"].append(str(e)[:200])
            
    return jobs

def extract_search_keywords_from_resume(resume_text: str, api_key: str = None) -> list:
    """
    Analyzes the candidate's base resume text and extracts the top 3 best
    matching job title search keywords using Gemini.
    
    Args:
        resume_text (str): The candidate's raw base resume.
        api_key (str, optional): The Gemini API key. Defaults to None.
        
    Returns:
        list: A list of extracted job title keyword strings.
    """
    import json
    from google.genai import types
    key = api_key or get_gemini_api_key()
    if not key:
        return ["Software Engineer"]
        
    try:
        client = genai.Client(api_key=key)
        prompt = f"""
        Analyze this candidate's resume and determine the top 3 job title keywords (specific search phrases) they are highly qualified for.
        
        Guidelines:
        1. Keep each title keyword simple and generic for search engines, e.g. "Data Engineer", "Python Developer", "React Developer", "Machine Learning Engineer".
        2. Do not include locations.
        3. Do not include too specific tech stacks unless they are primary, e.g. use "Data Engineer" rather than "PostgreSQL Snowflake PySpark Data Engineer".
        
        Candidate's Resume:
        \"\"\"{resume_text}\"\"\"
        
        Output a JSON list of strings, for example:
        ["Data Engineer", "Software Engineer", "Backend Developer"]
        Return ONLY valid JSON.
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        titles = json.loads(response.text.strip(), strict=False)
        if isinstance(titles, list) and len(titles) > 0:
            return [str(t) for t in titles]
        return ["Software Engineer"]
    except Exception as e:
        print(f"Error extracting keywords from resume: {e}")
        return ["Software Engineer"]

def scrape_job_details(url: str, allow_partial: bool = False) -> dict:
    """
    Launches a headless browser via Playwright and scrapes the job details
    (title, company name, description, and link) from the target job board.
    
    Args:
        url (str): The specific job posting URL.
        
    Returns:
        dict: A dictionary containing title, company, description, and URL,
              or an empty dictionary if the posting is closed or invalid.
    """
    url = canonicalize_job_url(url)
    is_public, _ = validate_public_http_url(url)
    if not is_public:
        return {}
    provider = provider_for_url(url)
    hostname = (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
    details = {
        "title": "",
        "company": "",
        "description": "",
        "url": url,
        "location": "",
        "work_arrangement": "",
        "employment_type": "",
        "compensation": "",
        "source": provider if provider != "unknown" else hostname,
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        _protect_browser_network(context)
        page = context.new_page()
        try:
            response = page.goto(url, timeout=20000, wait_until="domcontentloaded")
            if response and response.status >= 400:
                return {}
            if not is_public_http_url(page.url):
                return {}
            final_url = canonicalize_job_url(page.url)
            if final_url:
                details["url"] = final_url
            
            # Wait for content or title elements to load dynamically
            try:
                page.wait_for_selector("h1, h2, .app-title, .posting-header", timeout=4000)
            except Exception:
                pass
                
            page_title = page.title().lower()
            if "couldn't find" in page_title or "not found" in page_title or "error" in page_title:
                return {}
                
            if "lever.co" in url:
                title_elem = page.query_selector(".posting-header h2, h2")
                if not title_elem:
                    return {} # Discard if no job title element is found
                    
                company_logo = page.query_selector(".main-header-logo img")
                company_name = ""
                if company_logo:
                    company_name = company_logo.get_attribute("alt") or ""
                    company_name = company_name.replace("logo", "").replace("Logo of", "").strip()
                
                if not company_name:
                    p_title = page.title()
                    if "-" in p_title:
                        company_name = p_title.split("-")[0].strip()
                        
                sections = page.query_selector_all(".section")
                desc_text = "\n\n".join([s.inner_text() for s in sections])
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name if company_name else "Unknown Company"
                details["description"] = desc_text
                location_elem = page.query_selector(".posting-categories .location, .sort-by-location, [class*='location']")
                details["location"] = location_elem.inner_text().strip() if location_elem else ""
                
            elif "greenhouse.io" in url:
                title_elem = page.query_selector("h1.app-title, h1")
                if not title_elem:
                    return {} # Discard if no job title element is found
                    
                company_elem = page.query_selector("span.company-name")
                company_name = ""
                if company_elem:
                    company_name = company_elem.inner_text().replace("at ", "").strip()
                
                if not company_name:
                    p_title = page.title()
                    if " at " in p_title:
                        company_name = p_title.rsplit(" at ", 1)[-1].strip()
                    else:
                        segments = [urllib.parse.unquote(s) for s in urllib.parse.urlparse(url).path.split("/") if s]
                        company_name = segments[0].replace("-", " ").title() if segments else ""
                        
                desc_text = _largest_text(page, "#content, div[class*='description'], main")
                location_elem = page.query_selector("[class*='location'], [data-testid*='location']")
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name if company_name else "Unknown Company"
                details["description"] = desc_text
                details["location"] = location_elem.inner_text().strip() if location_elem else ""
                
            elif "ashbyhq.com" in url:
                title_elem = page.query_selector("h1")
                if not title_elem:
                    return {} # Discard if no job title element is found
                    
                parsed_url = urllib.parse.urlparse(url)
                url_path = parsed_url.path.strip("/")
                url_segments = [s for s in url_path.split("/") if s]
                
                p_title = page.title()
                company_name = "Unknown Company"
                if " @ " in p_title:
                    company_name = p_title.rsplit(" @ ", 1)[-1].strip()
                elif " - " in p_title:
                    company_name = p_title.split(" - ")[0].strip()
                elif " | " in p_title:
                    company_name = p_title.split(" | ")[0].strip()
                elif url_segments:
                    company_name = url_segments[0].capitalize()
                    
                desc_text = _largest_text(page, "div[class*='description'], #job-description, main") or page.inner_text("body")
                body_text = page.inner_text("body")
                location_match = re.search(r"(?im)^Location\s*\n+([^\n]+)", body_text)
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name
                details["description"] = desc_text
                details["location"] = location_match.group(1).strip() if location_match else ""

            elif "jobs.smartrecruiters.com" in url:
                title_candidates = page.query_selector_all("h1")
                title_text = next((element.inner_text().strip() for element in title_candidates if element.inner_text().strip() and not any(marker in element.inner_text().lower() for marker in INVALID_JOB_TEXT)), "")
                if not title_text:
                    return {}

                parsed_url = urllib.parse.urlparse(url)
                url_segments = [urllib.parse.unquote(s) for s in parsed_url.path.strip("/").split("/") if s]
                if len(url_segments) < 2:
                    return {}

                # The company slug is stable; the page title can be an error or
                # search-engine title and must never become the company name.
                company_slug = url_segments[0].replace("-", " ").replace("_", " ")
                company_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", company_slug).strip()
                desc_text = _largest_text(page, "div[class*='job'], div[class*='description'], #job-description, main")
                body_text = page.inner_text("body")

                details["title"] = title_text
                details["company"] = company_name
                details["description"] = desc_text
                location_match = re.search(r"(?im)^Location\s*\n+([^\n]+)", body_text)
                details["location"] = location_match.group(1).strip() if location_match else ""

            else:
                posting = _job_posting_json_ld(page)
                organization = posting.get("hiringOrganization") or {}
                title_element = page.query_selector("h1")
                description = _plain_text(posting.get("description"))
                if not description:
                    description = _largest_text(
                        page,
                        "[class*='job-description'], [id*='job-description'], main, article",
                    )
                details["title"] = _plain_text(posting.get("title")) or (
                    title_element.inner_text().strip() if title_element else ""
                )
                details["company"] = _plain_text(organization)
                if not details["company"]:
                    site_name = page.query_selector('meta[property="og:site_name"]')
                    details["company"] = (site_name.get_attribute("content") or "").strip() if site_name else ""
                details["description"] = description
                details["location"] = _job_location_from_json_ld(posting)
                for key, value in _structured_job_metadata(posting).items():
                    details[key] = value or details.get(key, "")
                
        except Exception as e:
            print(f"Error scraping details from {url}: {e}")
            return {}
        finally:
            browser.close()
            
    details["title"] = " ".join(details["title"].split())[:180]
    details["company"] = " ".join(details["company"].split())[:180]
    details["location"] = " ".join(details["location"].split())[:240]
    details["description"] = details["description"].strip()[:MAX_JOB_DESCRIPTION_CHARS]
    metadata = _metadata_from_text(f"{details['title']}\n{details['location']}\n{details['description'][:2500]}")
    for key, value in metadata.items():
        details[key] = details.get(key) or value

    # Reject generic/error pages and content that cannot support a real match.
    if allow_partial and any(details.get(key) for key in ("title", "company", "description")):
        return details
    if not is_useful_job_details(details):
        return {}
        
    return details

def run_job_search_and_matching(keywords: str, location: str = "") -> dict:
    """
    Coordinates the entire job search pipeline: retrieves the resume,
    calculates/suggests keywords if undefined, searches job boards in parallel,
    scrapes detailed job profiles, performs batch AI matching, and stores
    results in the SQLite database.
    
    Args:
        keywords (str): The keywords to search for. If empty, uses cached resume-suggested keywords.
        location (str, optional): Semicolon-separated list of target locations. Defaults to "".
        
    Returns:
        dict: A dictionary containing success status and number of new jobs matched.
    """
    # 1. Fetch user's base resume
    conn = get_db_connection()
    profile = conn.execute("SELECT base_resume_text, gemini_api_key, suggested_keywords FROM profile LIMIT 1").fetchone()
    if not profile or not profile["base_resume_text"]:
        conn.close()
        return {"error": "Profile or base resume text is missing. Please setup your profile first."}
        
    resume_text = profile["base_resume_text"]
    api_key = profile["gemini_api_key"]
    
    # 2. Determine search terms
    search_keywords = []
    stripped_kw = keywords.strip() if keywords else ""
    if not stripped_kw or stripped_kw.lower() == "undefined":
        # Check database cache first
        cached_kw = profile["suggested_keywords"]
        if cached_kw:
            search_keywords = [k.strip() for k in cached_kw.split(",") if k.strip()]
            print(f"Using cached suggested keywords from database: {search_keywords}")
        else:
            search_keywords = extract_search_keywords_from_resume(resume_text, api_key)
            # Save suggestions to cache
            conn.execute("UPDATE profile SET suggested_keywords = ? WHERE id = 1", (",".join(search_keywords),))
            conn.commit()
            print(f"No keywords entered. Extracted and cached keywords: {search_keywords}")
    else:
        search_keywords = [stripped_kw]
        
    # 3. Parse locations (supports multiple semicolon or comma separated locations)
    locs = []
    if location:
        if ";" in location:
            locs = [l.strip() for l in location.split(";") if l.strip()]
        else:
            raw_parts = [p.strip() for p in location.split(",") if p.strip()]
            merged_parts = []
            for part in raw_parts:
                if len(part) == 2 and part.isupper() and merged_parts:
                    merged_parts[-1] = f"{merged_parts[-1]}, {part}"
                else:
                    merged_parts.append(part)
            locs = merged_parts
    if not locs:
        locs = [""]

    provider_health = {
        provider: {
            "raw_candidates": 0, "valid_discovered": 0, "new_candidates": 0,
            "accepted": 0, "rejected": 0, "skipped_active": 0,
            "skipped_archived": 0, "errors": [],
        }
        for provider in PROVIDER_DOMAINS
    }

    # 4. Search Yahoo for matching Greenhouse/Lever postings
    results = []
    seen_urls = set()
    for kw in search_keywords:
        for loc in locs:
            loc_str = f" in '{loc}'" if loc else ""
            print(f"Crawling Yahoo jobs for keyword: '{kw}'{loc_str}...")
            kw_results = search_yahoo_jobs(kw, loc, provider_health)
            for item in kw_results:
                url = canonicalize_job_url(item["url"])
                if url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
    
    # 4. Scrape all job descriptions first
    scraped_jobs = []
    known_urls = {
        canonicalize_job_url(row["url"]): row["status"]
        for row in conn.execute("SELECT url, status FROM jobs").fetchall()
    }
    for item in results:
        url = item["url"]
        provider = provider_for_url(url)
        
        # Compare canonical forms so tracking parameters cannot create duplicates.
        if url in known_urls:
            key = "skipped_archived" if known_urls[url] == "archived" else "skipped_active"
            if provider in provider_health:
                provider_health[provider][key] += 1
            continue

        if provider in provider_health:
            provider_health[provider]["new_candidates"] += 1
            
        job_details = scrape_job_details(url)
        if not job_details or not job_details.get("title"):
            if provider in provider_health:
                provider_health[provider]["rejected"] += 1
            continue
            
        scraped_jobs.append(job_details)
        known_urls[url] = "matched"
        if provider in provider_health:
            provider_health[provider]["accepted"] += 1
        
    print(f"Scraped {len(scraped_jobs)} new specific jobs. Running batch AI matching...")
    
    # 5. Run AI matching in a single batch call to conserve API requests!
    batch_results = {}
    if scraped_jobs:
        try:
            batch_res = analyze_job_matches_batch(resume_text, scraped_jobs, api_key)
            if batch_res.get("success"):
                # Map index to match results
                for match in batch_res.get("matches", []):
                    idx = match.get("index")
                    if idx is not None:
                        batch_results[idx] = {
                            "score": match.get("match_score", 50),
                            "analysis": match.get("match_analysis", "Matched successfully.")
                        }
            else:
                print(f"Batch matching API error: {batch_res.get('error')}")
        except Exception as e:
            print(f"Failed to run batch matching: {e}")
            
    # 6. Insert matches into DB
    matches_added = 0
    for idx, job in enumerate(scraped_jobs):
        title = job["title"]
        company = job["company"]
        desc = job["description"]
        url = job["url"]
        
        # Fail closed when the matcher omits a record; an invented fallback
        # score would put an unanalyzed posting into the user's shortlist.
        match_info = batch_results.get(idx)
        if not match_info:
            continue
        try:
            score = max(0, min(100, int(match_info["score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if score < MIN_MATCH_SCORE:
            continue
        analysis_text = match_info["analysis"]
        
        try:
            conn.execute("""
            INSERT INTO jobs (
                title, company, description, url, match_score, match_analysis,
                date_found, status, location, work_arrangement, employment_type,
                compensation, source, last_checked_at, is_expired
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'matched', ?, ?, ?, ?, ?, ?, 0)
            """, (
                title, company, desc, url, score, analysis_text, datetime.now().strftime("%Y-%m-%d"),
                job.get("location", ""), job.get("work_arrangement", ""), job.get("employment_type", ""),
                job.get("compensation", ""), job.get("source", provider_for_url(url)),
                datetime.now().isoformat(timespec="seconds"),
            ))
            conn.commit()
            matches_added += 1
        except Exception as e:
            print(f"Error inserting job {url}: {e}")
            
    alerts = provider_alerts_from_health(provider_health)

    conn.close()
    return {
        "success": True,
        "jobs_added": matches_added,
        "provider_health": provider_health,
        "provider_alerts": alerts,
    }
