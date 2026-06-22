import os
import re
from playwright.sync_api import sync_playwright
from google import genai
from config import get_gemini_api_key, get_google_maps_api_key

def markdown_to_html(md_text: str) -> str:
    """
    Converts basic Markdown formatting (headers, bold text, lists, paragraphs)
    into standard semantic HTML strings for PDF rendering.
    
    Args:
        md_text (str): The raw Markdown text to format.
        
    Returns:
        str: The generated HTML body string.
    """
    html = md_text
    
    # Escape some basic HTML characters if present
    # (Optional, but let's keep it simple for resume text)
    
    # Headers
    html = re.sub(r'^#\s+(.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    html = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^###\s+(.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    
    # Bold
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)
    
    # Bullet points (lists)
    # Wrap multiple list items in <ul>...</ul>
    lines = html.split('\n')
    in_list = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            content = stripped[2:]
            if not in_list:
                new_lines.append('<ul>')
                in_list = True
            new_lines.append(f'<li>{content}</li>')
        else:
            if in_list:
                new_lines.append('</ul>')
                in_list = False
            new_lines.append(line)
    if in_list:
        new_lines.append('</ul>')
    html = '\n'.join(new_lines)
    
    # Paragraphs (blocks separated by double newlines, excluding tags)
    paragraphs = html.split('\n\n')
    new_paragraphs = []
    for p in paragraphs:
        p_strip = p.strip()
        if not p_strip:
            continue
        if p_strip.startswith('<h') or p_strip.startswith('<ul') or p_strip.startswith('<li') or p_strip.startswith('</ul'):
            new_paragraphs.append(p_strip)
        else:
            # Replace single newlines within paragraph with <br/>
            p_formatted = p_strip.replace('\n', '<br/>')
            new_paragraphs.append(f'<p>{p_formatted}</p>')
            
    return '\n'.join(new_paragraphs)

def generate_pdf_from_html(html_content: str, output_pdf_path: str, is_resume: bool = True) -> None:
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
        body {
            font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
            color: #333333;
            line-height: 1.5;
            margin: 0;
            padding: 40px;
            font-size: 14px;
        }
        h1 {
            font-size: 26px;
            color: #1a365d;
            margin-top: 0;
            margin-bottom: 5px;
            border-bottom: 2px solid #2b6cb0;
            padding-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        h2 {
            font-size: 18px;
            color: #2b6cb0;
            margin-top: 20px;
            margin-bottom: 10px;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 3px;
        }
        h3 {
            font-size: 15px;
            color: #4a5568;
            margin-top: 12px;
            margin-bottom: 6px;
        }
        p {
            margin-top: 0;
            margin-bottom: 10px;
        }
        ul {
            margin-top: 0;
            margin-bottom: 12px;
            padding-left: 20px;
        }
        li {
            margin-bottom: 5px;
        }
        strong {
            color: #2d3748;
        }
        .contact-info {
            text-align: center;
            margin-bottom: 20px;
            font-size: 13px;
            color: #718096;
        }
        .contact-info a {
            color: #2b6cb0;
            text-decoration: none;
        }
    </style>
    """
    
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        {style}
    </head>
    <body>
        {html_content}
    </body>
    </html>
    """
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(full_html)
        # Set margin options for PDF
        page.pdf(
            path=output_pdf_path,
            format="Letter",
            margin={"top": "0.6in", "bottom": "0.6in", "left": "0.6in", "right": "0.6in"}
        )
        browser.close()

def generate_resume_pdf(markdown_text: str, output_pdf_path: str) -> None:
    """
    Converts a Markdown tailored resume to HTML and compiles it to a PDF.
    
    Args:
        markdown_text (str): The raw Markdown tailored resume.
        output_pdf_path (str): The path to save the generated PDF.
    """
    html_body = markdown_to_html(markdown_text)
    
    # We can perform some custom structural cleanup if needed, but a clean markdown conversion is usually enough
    generate_pdf_from_html(html_body, output_pdf_path, is_resume=True)

def generate_cover_letter_pdf(cover_letter_text: str, output_pdf_path: str) -> None:
    """
    Converts a plain text cover letter into a styled HTML and prints to PDF.
    
    Args:
        cover_letter_text (str): The plain text cover letter content.
        output_pdf_path (str): The path to save the generated PDF.
    """
    # Convert newlines to paragraphs/breaks
    formatted_body = cover_letter_text.replace('\n', '<br/>')
    html_body = f"<div class='cover-letter'>{formatted_body}</div>"
    
    generate_pdf_from_html(html_body, output_pdf_path, is_resume=False)

def find_us_headquarters(company_name: str, api_key: str = None, google_maps_key: str = None) -> str:
    """
    Resolves the company's full headquarters street address.
    Checks the Google Places API first if a key is provided,
    otherwise falls back to the Gemini API.
    
    Args:
        company_name (str): The name of the company to look up.
        api_key (str, optional): The Gemini API key (for fallback).
        google_maps_key (str, optional): The Google Maps/Places API key.
        
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
            query = f"{company_name} headquarters"
            encoded_query = urllib.parse.quote_plus(query)
            url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={encoded_query}&inputtype=textquery&fields=formatted_address,name&key={g_key}"
            
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                candidates = res_data.get("candidates", [])
                if candidates:
                    addr = candidates[0].get("formatted_address")
                    if addr:
                        return addr
        except Exception as e:
            print(f"Error querying Google Places API for '{company_name}': {e}")
            
    # 2. Fallback to Gemini API
    key = api_key or get_gemini_api_key()
    if not key:
        return "Unknown"
        
    try:
        client = genai.Client(api_key=key)
        prompt = f"""
        What is the full U.S. headquarters street address (including street number, street name, city, state, and zip code) for the company: '{company_name}'?
        If the company is headquartered outside the U.S., provide its full global headquarters street address and country, and mention it is international (e.g. "1-7-1 Konan, Minato-ku, Tokyo, 108-0075, Japan (Global HQ)").
        Respond with ONLY the full address. Do not include any additional commentary, introductory text, markdown formatting, or HTML. If you are not sure of the exact street address, make your best guess or provide the city, state, and zip code.
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        location = response.text.strip()
        if len(location) > 250: # Sanitization in case of verbose response
            location = location[:250]
        return location
    except Exception as e:
        print(f"Error resolving headquarters: {e}")
        return "Unknown"
