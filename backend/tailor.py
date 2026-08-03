import json
import re
from datetime import date
from google import genai
from google.genai import types
from config import get_gemini_api_key


def format_cover_letter_date(value: date) -> str:
    """Format dates without platform-specific strftime day directives."""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def finalize_cover_letter(cover_letter: str, letter_date: date | None = None) -> str:
    """Resolve date tokens so unfinished template text is never delivered."""
    text = (cover_letter or "").strip()
    if not text:
        raise ValueError("Gemini returned an empty cover letter.")

    concrete_date = format_cover_letter_date(letter_date or date.today())
    text = re.sub(r"(?i)\[\s*date\s*\]", concrete_date, text)
    text = re.sub(r"(?i)\{\s*date\s*\}", concrete_date, text)
    text = re.sub(r"(?i)<\s*date\s*>", concrete_date, text)
    if re.search(r"(?i)\[\s*(?:insert\s+)?date\s*(?:here)?\s*\]", text):
        raise ValueError("The generated cover letter contains an unresolved date placeholder.")
    return text

def get_client(api_key: str = None) -> genai.Client:
    """
    Initializes and returns a Google GenAI client using the provided API key
    or falling back to the configured default API key.
    
    Args:
        api_key (str, optional): The Gemini API key. Defaults to None.
        
    Raises:
        ValueError: If no Gemini API key is configured.
    """
    key = api_key or get_gemini_api_key()
    if not key:
        raise ValueError("Gemini API key is not configured. Please set GEMINI_API_KEY environment variable or enter it in settings.")
    return genai.Client(api_key=key)

RESUME_MODE_GUIDANCE = {
    "it": "Emphasize technical depth, architecture, delivery outcomes, platforms, and relevant tools without keyword stuffing.",
    "technical_executive": "Lead with enterprise scope, transformation outcomes, operating model, governance, budgets, and leadership impact.",
    "general_professional": "Use broadly applicable accomplishment-focused language and a conventional reverse-chronological structure.",
    "federal": "Use detailed duty scope, month/year dates, hours where known, and qualification evidence; do not invent federal-specific facts.",
    "healthcare": "Prioritize licenses, patient or operational outcomes, compliance, safety, and domain systems when evidenced.",
    "education": "Prioritize teaching, curriculum, learner outcomes, credentials, research, and service when evidenced.",
    "sales": "Prioritize quota, revenue, pipeline, territory, retention, and customer outcomes when evidenced.",
    "trades_operations": "Prioritize licenses, safety, equipment, throughput, quality, scheduling, and hands-on scope when evidenced.",
    "academic_cv": "Use an academic CV structure and retain relevant publications, research, teaching, grants, and service; the two-page resume limit does not apply.",
    "cover_letter": "Keep the resume conventional while placing extra emphasis on a specific, persuasive cover letter.",
}

RESUME_SECTION_TEMPLATES = {
    "it": ("Summary", "Technical Skills", "Professional Experience", "Projects", "Certifications", "Education"),
    "technical_executive": ("Executive Summary", "Leadership Competencies", "Professional Experience", "Board & Advisory", "Education", "Certifications"),
    "general_professional": ("Professional Summary", "Core Skills", "Professional Experience", "Projects", "Certifications", "Education"),
    "federal": ("Summary of Qualifications", "Core Competencies", "Professional Experience", "Education", "Certifications"),
    "healthcare": ("Professional Summary", "Licenses & Certifications", "Professional Experience", "Skills", "Education"),
    "education": ("Professional Summary", "Credentials", "Teaching Experience", "Education", "Research & Publications", "Service"),
    "sales": ("Professional Summary", "Sales Competencies", "Professional Experience", "Achievements", "Education"),
    "trades_operations": ("Summary", "Licenses & Certifications", "Skills", "Professional Experience", "Education"),
    "academic_cv": ("Education", "Academic Appointments", "Research", "Publications", "Teaching", "Grants & Awards", "Service", "Professional Experience"),
    "cover_letter": ("Professional Summary", "Core Skills", "Professional Experience", "Projects", "Certifications", "Education"),
}


def _canonical_section(title: str, template: tuple[str, ...]) -> str | None:
    normalized = re.sub(r"[^a-z& ]", "", title.lower()).strip()
    exact = {section.lower(): section for section in template}
    if normalized in exact:
        return exact[normalized]
    semantic = (
        (("summary", "profile", "objective", "qualifications"), ("summary", "qualifications")),
        (("skill", "competenc", "expertise", "technolog"), ("skill", "competenc")),
        (("experience", "employment", "career"), ("experience", "appointments")),
        (("project",), ("project",)),
        (("certif", "license", "credential"), ("certif", "license", "credential")),
        (("education",), ("education",)),
        (("publication",), ("publication",)),
        (("research",), ("research",)),
        (("teaching",), ("teaching",)),
        (("grant", "award", "achievement"), ("grant", "award", "achievement")),
        (("board", "advisory", "service"), ("board", "advisory", "service")),
    )
    for source_terms, target_terms in semantic:
        if any(term in normalized for term in source_terms):
            match = next((section for section in template if any(term in section.lower() for term in target_terms)), None)
            if match:
                return match
    return None


def apply_resume_section_template(markdown: str, resume_mode: str) -> str:
    """Normalize generated level-two sections into a deterministic mode order."""
    template = RESUME_SECTION_TEMPLATES.get(resume_mode, RESUME_SECTION_TEMPLATES["general_professional"])
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown))
    if not matches:
        raise ValueError("Tailored resume did not contain the required Markdown sections.")
    preamble = markdown[:matches[0].start()].rstrip()
    grouped: dict[str, list[str]] = {}
    unknown = []
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end():body_end].strip()
        canonical = _canonical_section(match.group(1), template)
        if not canonical:
            unknown.append(match.group(1).strip())
            continue
        if body:
            grouped.setdefault(canonical, []).append(body)
    if unknown:
        raise ValueError(f"Tailored resume used unsupported section(s) for {resume_mode}: {', '.join(unknown)}")
    if not grouped:
        raise ValueError("Tailored resume sections were empty.")
    sections = [f"## {section}\n\n" + "\n\n".join(grouped[section]) for section in template if section in grouped]
    return "\n\n".join(([preamble] if preamble else []) + sections).strip()


def tailor_resume_and_cover_letter(base_resume_text: str, job_title: str, company_name: str, job_description: str, api_key: str = None, resume_mode: str = "general_professional") -> dict:
    """
    Consolidates resume tailoring and cover letter drafting into a single Gemini request
    to conserve daily API limits, returning the customized materials as JSON.
    
    Args:
        base_resume_text (str): The candidate's raw base resume.
        job_title (str): The target job title.
        company_name (str): The name of the hiring company.
        job_description (str): The description/requirements of the job.
        api_key (str, optional): The Gemini API key.
        
    Returns:
        dict: A dictionary containing success status, tailored resume, and cover letter text.
    """
    try:
        client = get_client(api_key)
    except Exception as e:
        return {"error": str(e)}

    letter_date = date.today()
    formatted_letter_date = format_cover_letter_date(letter_date)
    mode_guidance = RESUME_MODE_GUIDANCE.get(resume_mode, RESUME_MODE_GUIDANCE["general_professional"])
    section_template = RESUME_SECTION_TEMPLATES.get(resume_mode, RESUME_SECTION_TEMPLATES["general_professional"])
    page_contract = "The academic CV may exceed two pages when the evidenced content requires it." if resume_mode == "academic_cv" else "The resume must fit within two US Letter pages at 10-point type."

    # Combine resume tailoring and cover letter writing into a single prompt
    # and use JSON output to reduce daily Gemini API requests from 2 to 1.
    prompt = f"""
    You are an expert resume writer and career coach for both technical and non-technical professions. Your task is to perform two actions for a candidate applying to a job:
    1. Tailor their base resume to match the job requirements, emphasizing only relevant, evidenced skills, projects, and work experience. Maintain absolute factual accuracy: DO NOT invent jobs, dates, degrees, credentials, technologies, metrics, or companies.
    2. Write a customized, compelling, and professional cover letter (under 350 words, 3-4 paragraphs) addressed to the hiring team at {company_name}.

    Cover-letter contract:
    - Use the concrete date "{formatted_letter_date}" in the letter heading.
    - Never emit [Date], {{Date}}, <Date>, or any other placeholder token.
    - When a hiring manager's name is unknown, address the letter to "Hiring Team" without a placeholder.

    Resume formatting contract:
    - Selected resume mode: {resume_mode}. Mode guidance: {mode_guidance}
    - Use only the applicable level-two sections from this exact ordered template, omitting empty sections: {"; ".join(section_template)}.
    - {page_contract}
    - Use only standard Markdown headings (#, ##, ###), paragraphs, bold/italics, and flat bullet lists.
    - Do not use horizontal rules, tables, columns, images, badges, skill ratings, or decorative symbols.
    - Use conventional section names appropriate to the profession, such as Summary, Skills, Experience, Projects, Licenses, Certifications, and Education.
    - Limit the summary to 3-4 lines. Use at most 4-6 concise bullets for the most recent role and 2-3 for older roles. Each bullet should normally fit in 1-2 lines.
    - Prefer demonstrated outcomes over keyword repetition. Remove or compress older and irrelevant material instead of shrinking the text.
    
    Job Details:
    - Job Title: {job_title}
    - Company: {company_name}
    - Job Description:
    \"\"\"{job_description}\"\"\"
    
    Candidate's Base Resume:
    \"\"\"{base_resume_text}\"\"\"
    
    Output a JSON object with exactly two keys:
    - "tailored_resume": The complete markdown-formatted tailored resume.
    - "cover_letter": The complete plain-text cover letter.
    
    Return ONLY valid JSON.
    """

    try:
        # Use gemini-2.5-flash for speed and efficiency
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        res_data = json.loads(response.text.strip(), strict=False)
        tailored_resume = res_data.get("tailored_resume", "").strip()
        cover_letter = finalize_cover_letter(res_data.get("cover_letter", ""), letter_date)

        # Clean up markdown tags if the model still wrapped them (rare for JSON output)
        if tailored_resume.startswith("```markdown"):
            tailored_resume = tailored_resume[11:]
        elif tailored_resume.startswith("```"):
            tailored_resume = tailored_resume[3:]
        if tailored_resume.endswith("```"):
            tailored_resume = tailored_resume[:-3]
        tailored_resume = tailored_resume.strip()
        tailored_resume = apply_resume_section_template(tailored_resume, resume_mode)

        return {
            "success": True,
            "tailored_resume": tailored_resume,
            "cover_letter": cover_letter
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to call Gemini API: {str(e)}"
        }

def analyze_job_match(base_resume_text: str, job_title: str, company_name: str, job_description: str, api_key: str = None) -> dict:
    """
    Analyzes how well the candidate's resume matches a single job description
    and returns a match score and structural analysis.
    
    Args:
        base_resume_text (str): The candidate's raw base resume.
        job_title (str): The target job title.
        company_name (str): The name of the hiring company.
        job_description (str): The description/requirements of the job.
        api_key (str, optional): The Gemini API key.
        
    Returns:
        dict: A dictionary containing success status, match score, and analysis markdown.
    """
    try:
        client = get_client(api_key)
    except Exception as e:
        return {"error": str(e)}

    prompt = f"""
    You are an AI Job Matching Assistant. Analyze the match between the candidate's resume and a job description.
    
    Job Title: {job_title}
    Company: {company_name}
    
    Job Description:
    \"\"\"{job_description}\"\"\"
    
    Candidate's Resume:
    \"\"\"{base_resume_text}\"\"\"
    
    Determine:
    1. A match score between 0 and 100.
    2. A concise analysis summarizing:
       - Strengths (why the candidate fits)
       - Gaps (missing key skills or experiences)
       - Recommendations (how to position themselves)
       
    Output a JSON object with keys: "match_score" (integer) and "match_analysis" (string markdown).
    Return ONLY valid JSON.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        data = json.loads(response.text.strip())
        return {
            "success": True,
            "match_score": data.get("match_score", 50),
            "match_analysis": data.get("match_analysis", "No analysis provided.")
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to analyze match: {str(e)}"
        }

def analyze_job_matches_batch(base_resume_text: str, jobs_list: list, api_key: str = None) -> dict:
    """
    Analyzes multiple job postings in a single API call to conserve free-tier
    request quotas and evaluate job compatibility in bulk.
    
    Args:
        base_resume_text (str): The candidate's raw base resume.
        jobs_list (list): A list of dictionaries representing discovered jobs to match.
        api_key (str, optional): The Gemini API key.
        
    Returns:
        dict: A dictionary containing success status and a list of structured match results.
    """
    try:
        client = get_client(api_key)
    except Exception as e:
        return {"success": False, "error": str(e)}
        
    # Format the jobs list compactly to save tokens
    formatted_jobs = []
    for idx, job in enumerate(jobs_list):
        formatted_jobs.append({
            "index": idx,
            "title": job["title"],
            "company": job["company"],
            "description": job["description"][:1000] if job["description"] else ""  # truncate to save tokens
        })
        
    prompt = f"""
    You are an AI Job Matching Assistant. Analyze the match between the candidate's resume and a list of job postings.
    
    Candidate's Resume:
    \"\"\"{base_resume_text}\"\"\"
    
    Job Postings:
    {json.dumps(formatted_jobs, indent=2)}
    
    For each job posting, determine:
    1. A match score between 0 and 100 based on how well the candidate's skills and experience align with the job description.
    2. A concise analysis (1-2 sentences) summarizing why the candidate fits, or what key gaps exist.
    
    Output a JSON object with a single key "matches" containing a list of objects in the exact same order:
    {{
      "matches": [
        {{
          "index": 0,
          "match_score": 85,
          "match_analysis": "Excellent fit for backend skills. Missing Kubernetes experience."
        }},
        ...
      ]
    }}
    Return ONLY valid JSON.
    """
    
    try:
        # Use gemini-2.5-flash for speed
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        data = json.loads(response.text.strip())
        return {
            "success": True,
            "matches": data.get("matches", [])
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to analyze matches in batch: {str(e)}"
        }
