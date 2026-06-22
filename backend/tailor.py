import json
from google import genai
from google.genai import types
from config import get_gemini_api_key

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

def tailor_resume_and_cover_letter(base_resume_text: str, job_title: str, company_name: str, job_description: str, api_key: str = None) -> dict:
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

    # Combine resume tailoring and cover letter writing into a single prompt
    # and use JSON output to reduce daily Gemini API requests from 2 to 1.
    prompt = f"""
    You are an expert technical resume writer and career coach. Your task is to perform two actions for a candidate applying to a job:
    1. Tailor their base resume to match the job requirements, emphasizing matching skills, projects, and work experience. Maintain absolute factual accuracy: DO NOT invent fake jobs, fake degrees, or fake companies. Keep a clean, professional, and well-structured Markdown format.
    2. Write a customized, compelling, and professional cover letter (under 350 words, 3-4 paragraphs) addressed to the hiring team at {company_name}.
    
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
        cover_letter = res_data.get("cover_letter", "").strip()

        # Clean up markdown tags if the model still wrapped them (rare for JSON output)
        if tailored_resume.startswith("```markdown"):
            tailored_resume = tailored_resume[11:]
        elif tailored_resume.startswith("```"):
            tailored_resume = tailored_resume[3:]
        if tailored_resume.endswith("```"):
            tailored_resume = tailored_resume[:-3]
        tailored_resume = tailored_resume.strip()

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
