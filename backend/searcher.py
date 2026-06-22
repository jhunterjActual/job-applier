import re
import urllib.parse
import urllib.request
from playwright.sync_api import sync_playwright
from google import genai
from config import get_gemini_api_key
from database import get_db_connection
from tailor import analyze_job_match, analyze_job_matches_batch
from datetime import datetime

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
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s]
        
        if "greenhouse.io" in parsed.netloc:
            # Greenhouse specific postings must contain 'jobs' in segments and have at least 3 segments (e.g., /company/jobs/id)
            return "jobs" in segments and len(segments) >= 3
            
        elif "jobs.lever.co" in parsed.netloc:
            # Lever specific postings must have exactly 2 segments (e.g., /company/job-uuid)
            if len(segments) == 2:
                # Avoid standard routing endpoints if any
                return segments[1] not in ["careers", "login", "logo", "about"]
            return False
            
        elif "ashbyhq.com" in parsed.netloc:
            # Ashby postings look like jobs.ashbyhq.com/company/job-id or ashbyhq.com/company/jobs/job-id
            return len(segments) >= 2 and segments[-1] not in ["careers", "login", "about"]
            
        elif "smartrecruiters.com" in parsed.netloc:
            # SmartRecruiters postings look like jobs.smartrecruiters.com/company/job-id
            return len(segments) == 2 and segments[1] not in ["careers", "login"]
            
        return False
    except Exception:
        return False

def search_yahoo_jobs(keywords: str, location: str = "") -> list:
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
    domains = ["greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com"]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
    }
    
    jobs = []
    seen_urls = set()
    
    for domain in domains:
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
                            if is_specific_job_url(decoded):
                                matching_urls.append(decoded)
                
                # Deduplicate and format results for this domain
                for actual_url in matching_urls:
                    if actual_url not in seen_urls:
                        seen_urls.add(actual_url)
                        jobs.append({
                            "title": "Job Posting",
                            "url": actual_url,
                            "snippet": ""
                        })
        except Exception as e:
            print(f"Error during Yahoo search for {domain}: {e}")
            
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

def scrape_job_details(url: str) -> dict:
    """
    Launches a headless browser via Playwright and scrapes the job details
    (title, company name, description, and link) from the target job board.
    
    Args:
        url (str): The specific job posting URL.
        
    Returns:
        dict: A dictionary containing title, company, description, and URL,
              or an empty dictionary if the posting is closed or invalid.
    """
    details = {
        "title": "",
        "company": "",
        "description": "",
        "url": url
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        try:
            page.goto(url, timeout=20000)
            
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
                
            elif "greenhouse.io" in url:
                title_elem = page.query_selector("h1.app-title")
                if not title_elem:
                    return {} # Discard if no job title element is found
                    
                company_elem = page.query_selector("span.company-name")
                company_name = ""
                if company_elem:
                    company_name = company_elem.inner_text().replace("at ", "").strip()
                
                if not company_name:
                    p_title = page.title()
                    if "at" in p_title:
                        company_name = p_title.split("at")[-1].strip()
                        
                desc_elem = page.query_selector("#content")
                desc_text = desc_elem.inner_text() if desc_elem else ""
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name if company_name else "Unknown Company"
                details["description"] = desc_text
                
            elif "ashbyhq.com" in url or "smartrecruiters.com" in url:
                title_elem = page.query_selector("h1")
                if not title_elem:
                    return {} # Discard if no job title element is found
                    
                parsed_url = urllib.parse.urlparse(url)
                url_path = parsed_url.path.strip("/")
                url_segments = [s for s in url_path.split("/") if s]
                
                p_title = page.title()
                company_name = "Unknown Company"
                if " - " in p_title:
                    company_name = p_title.split(" - ")[0].strip()
                elif " | " in p_title:
                    company_name = p_title.split(" | ")[0].strip()
                elif url_segments:
                    company_name = url_segments[0].capitalize()
                    
                desc_elem = page.query_selector("section, div[class*='description'], div[class*='job'], #job-description")
                desc_text = desc_elem.inner_text() if desc_elem else page.inner_text("body")
                
                details["title"] = title_elem.inner_text().strip()
                details["company"] = company_name
                details["description"] = desc_text
                
        except Exception as e:
            print(f"Error scraping details from {url}: {e}")
            return {}
        finally:
            browser.close()
            
    # Final check: if title is empty, is generic, or indicates a closed posting, discard
    t_lower = details["title"].lower()
    if not details["title"] or t_lower.startswith("jobs at") or "job not found" in t_lower or "no longer active" in t_lower or "no longer available" in t_lower or "position closed" in t_lower or "job is closed" in t_lower:
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

    # 4. Search Yahoo for matching Greenhouse/Lever postings
    results = []
    seen_urls = set()
    for kw in search_keywords:
        for loc in locs:
            loc_str = f" in '{loc}'" if loc else ""
            print(f"Crawling Yahoo jobs for keyword: '{kw}'{loc_str}...")
            kw_results = search_yahoo_jobs(kw, loc)
            for item in kw_results:
                url = item["url"]
                if url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
    
    # 4. Scrape all job descriptions first
    scraped_jobs = []
    for item in results:
        url = item["url"]
        
        # Check if already in db to avoid duplicates
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
        if existing:
            continue
            
        job_details = scrape_job_details(url)
        if not job_details or not job_details.get("title"):
            continue
            
        scraped_jobs.append(job_details)
        
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
        
        # Get score and analysis from batch results or fallback
        match_info = batch_results.get(idx, {"score": 50, "analysis": "Analysis not available"})
        score = match_info["score"]
        analysis_text = match_info["analysis"]
        
        try:
            conn.execute("""
            INSERT INTO jobs (title, company, description, url, match_score, match_analysis, date_found, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'matched')
            """, (title, company, desc, url, score, analysis_text, datetime.now().strftime("%Y-%m-%d")))
            conn.commit()
            matches_added += 1
        except Exception as e:
            print(f"Error inserting job {url}: {e}")
            
    conn.close()
    return {"success": True, "jobs_added": matches_added}
