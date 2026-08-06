import os
import re
import html
from pathlib import Path
from config import get_gemini_api_key, get_google_maps_api_key


def _inline_markdown(text: str) -> str:
    """Render the small, safe inline Markdown subset used in resumes."""
    rendered = html.escape(text.strip(), quote=True)
    rendered = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', rendered)
    rendered = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', rendered)
    rendered = re.sub(r'(?<!_)_([^_]+?)_(?!_)', r'<em>\1</em>', rendered)
    return rendered


def markdown_to_html(md_text: str) -> str:
    """
    Converts basic Markdown formatting (headers, bold text, lists, paragraphs)
    into standard semantic HTML strings for PDF rendering.
    
    Args:
        md_text (str): The raw Markdown text to format.
        
    Returns:
        str: The generated HTML body string.
    """
    source = (md_text or "").replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")
    output: list[str] = []
    paragraph: list[str] = []
    in_list = False
    in_section = False
    in_entry = False

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{' '.join(_inline_markdown(line) for line in paragraph)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    def close_entry() -> None:
        nonlocal in_entry
        if in_entry:
            output.append("</div>")
            in_entry = False

    for raw_line in source.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_paragraph()
            close_list()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            if level == 2:
                close_entry()
                if in_section:
                    output.append("</section>")
                output.append('<section class="resume-section">')
                in_section = True
            elif level == 3:
                close_entry()
                output.append('<div class="resume-entry">')
                in_entry = True
            output.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue

        if re.fullmatch(r"[-*_]{3,}", stripped):
            flush_paragraph()
            close_list()
            output.append("<hr>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{_inline_markdown(bullet.group(1))}</li>")
            continue

        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    close_entry()
    if in_section:
        output.append("</section>")
    return "\n".join(output)

def generate_pdf_from_html(
    html_content: str,
    output_pdf_path: str,
    is_resume: bool = True,
    max_pages: int = 2,
) -> dict:
    """
    Compiles an HTML body string with custom styles and prints it to a PDF
    on disk using Playwright's headless PDF printing capabilities.
    
    Args:
        html_content (str): The semantic HTML content to print.
        output_pdf_path (str): The local file path to write the PDF.
        is_resume (bool, optional): Whether this is a resume. Defaults to True.
    """
    # CSS template
    style = """
    <style>
        @page {
            size: Letter;
            margin: 0.58in 0.65in;
        }
        body {
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            color: #333333;
            line-height: 1.3;
            margin: 0;
            padding: 0;
            font-size: 10.5pt;
        }
        h1 {
            font-size: 20pt;
            color: #1a365d;
            margin-top: 0;
            margin-bottom: 6pt;
            border-bottom: 2px solid #2b6cb0;
            padding-bottom: 5pt;
            text-transform: uppercase;
            letter-spacing: 0.6pt;
        }
        h2 {
            font-size: 13.5pt;
            color: #2b6cb0;
            margin-top: 12pt;
            margin-bottom: 6pt;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 2pt;
            break-after: avoid;
        }
        h3 {
            font-size: 11pt;
            color: #4a5568;
            margin-top: 8pt;
            margin-bottom: 3pt;
            break-after: avoid;
        }
        p {
            margin-top: 0;
            margin-bottom: 5pt;
            orphans: 2;
            widows: 2;
        }
        ul {
            margin-top: 0;
            margin-bottom: 6pt;
            padding-left: 16pt;
        }
        li {
            margin-bottom: 3pt;
            break-inside: avoid;
            orphans: 2;
            widows: 2;
        }
        strong {
            color: #2d3748;
        }
        hr { display: none; }
        .resume-section { break-inside: auto; }
        .resume-entry { break-inside: avoid; }
        body.compact { font-size: 10pt; line-height: 1.22; }
        body.compact h1 { font-size: 18pt; margin-bottom: 4pt; padding-bottom: 3pt; }
        body.compact h2 { font-size: 12.5pt; margin-top: 8pt; margin-bottom: 4pt; }
        body.compact h3 { font-size: 10.5pt; margin-top: 5pt; margin-bottom: 2pt; }
        body.compact p, body.compact li { margin-bottom: 2pt; }
        body.compact ul { margin-bottom: 3pt; }
    </style>
    """
    
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        {style}
    </head>
    <body class="{'resume' if is_resume else 'cover-letter'}">
        {html_content}
    </body>
    </html>
    """
    
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 701, "height": 945})
        page.set_content(full_html, wait_until="load")
        try:
            for compact in (False, True):
                if compact:
                    page.evaluate("document.body.classList.add('compact')")
                pdf_bytes = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
                page_count = len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))
                if page_count <= max_pages:
                    output_path = Path(output_pdf_path)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(pdf_bytes)
                    return {"page_count": page_count, "compact": compact}
            raise ValueError(
                f"Generated document exceeds the {max_pages}-page limit after compact formatting. "
                "Shorten the source content and regenerate it."
            )
        finally:
            browser.close()

def generate_resume_pdf(markdown_text: str, output_pdf_path: str, max_pages: int = 2) -> dict:
    """
    Converts a Markdown tailored resume to HTML and compiles it to a PDF.
    
    Args:
        markdown_text (str): The raw Markdown tailored resume.
        output_pdf_path (str): The path to save the generated PDF.
    """
    html_body = markdown_to_html(markdown_text)
    
    # We can perform some custom structural cleanup if needed, but a clean markdown conversion is usually enough
    return generate_pdf_from_html(html_body, output_pdf_path, is_resume=True, max_pages=max_pages)

def generate_cover_letter_pdf(cover_letter_text: str, output_pdf_path: str) -> dict:
    """
    Converts a plain text cover letter into a styled HTML and prints to PDF.
    
    Args:
        cover_letter_text (str): The plain text cover letter content.
        output_pdf_path (str): The path to save the generated PDF.
    """
    # Convert newlines to paragraphs/breaks
    formatted_body = html.escape(cover_letter_text).replace('\n', '<br/>')
    html_body = f"<div class='cover-letter'>{formatted_body}</div>"
    
    return generate_pdf_from_html(html_body, output_pdf_path, is_resume=False, max_pages=1)

def _looks_like_us_address(address: str) -> bool:
    """Return whether a formatted address explicitly identifies the United States."""
    return bool(re.search(r"\b(?:USA|United States(?: of America)?)\b", address or "", re.IGNORECASE))


def _headquarters_query(company_name: str, prefer_us: bool) -> str:
    """Build a Places query that carries the user's headquarters preference."""
    scope = "United States headquarters" if prefer_us else "global headquarters"
    return f"{company_name} {scope}"


def find_us_headquarters(
    company_name: str,
    api_key: str = None,
    google_maps_key: str = None,
    *,
    prefer_us: bool = True,
    job_location: str = "",
    job_url: str = "",
) -> str:
    """
    Resolves a company headquarters address, preferring a U.S. location when
    the user has selected that preference and the employer has one.
    Checks the Google Places API first if a key is provided,
    otherwise falls back to the Gemini API.
    
    Args:
        company_name (str): The name of the company to look up.
        api_key (str, optional): The Gemini API key (for fallback).
        google_maps_key (str, optional): The Google Maps/Places API key.
        prefer_us (bool): Prefer a U.S. address when the employer has one.
        job_location (str): Posting location used to disambiguate the employer.
        job_url (str): Posting URL used to disambiguate the employer.
        
    Returns:
        str: The resolved full address or 'Unknown'.
    """
    # 1. Try Google Places API first if key is provided
    g_key = google_maps_key or get_google_maps_api_key()
    if g_key:
        import urllib.request
        import urllib.parse
        import json
        try:
            query = _headquarters_query(company_name, prefer_us)
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={encoded_query}&inputtype=textquery&fields=formatted_address,name&key={g_key}"
            
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                candidates = res_data.get("candidates", [])
                if candidates:
                    addr = candidates[0].get("formatted_address")
                    if addr and (not prefer_us or _looks_like_us_address(addr)):
                        return addr
        except Exception as e:
            print(f"Error querying Google Places API for '{company_name}': {e}")
            
    # 2. Fallback to Gemini API
    key = api_key or get_gemini_api_key()
    if not key:
        return "Unknown"
        
    try:
        from google import genai
        client = genai.Client(api_key=key)
        address_scope = (
            "the employer's U.S. headquarters address when it has one; otherwise use its primary global headquarters"
            if prefer_us
            else "the employer's primary global headquarters address"
        )
        prompt = f"""
        Identify {address_scope} for the exact employer '{company_name}'.
        Identity context from the job posting:
        - Job location: {job_location or 'not provided'}
        - Job posting URL: {job_url or 'not provided'}

        Do not substitute a similarly named company. Respond with ONLY the full,
        verified address, including country. If the employer identity or address
        cannot be verified confidently, respond with exactly: Unknown
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        location = (response.text or "").strip()
        if not location or location.lower() == "unknown":
            return "Unknown"
        if len(location) > 250: # Sanitization in case of verbose response
            location = location[:250]
        return location
    except Exception as e:
        print(f"Error resolving headquarters: {e}")
        return "Unknown"
