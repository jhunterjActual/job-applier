import os
import shutil
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

import config
from database import get_db_connection
from tailor import tailor_resume_and_cover_letter
from searcher import run_job_search_and_matching
from applier import fill_application_form
from utils import generate_resume_pdf, generate_cover_letter_pdf, find_us_headquarters

app = FastAPI(title="AI Job Applier Agent API")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ProfileUpdate(BaseModel):
    name: str
    email: str
    phone: str
    github: str
    linkedin: str
    website: str
    base_resume_text: str
    gemini_api_key: str
    google_maps_api_key: Optional[str] = ""

class SearchRequest(BaseModel):
    keywords: str
    location: Optional[str] = ""

# Profile endpoints
@app.get("/api/profile")
def get_profile() -> dict:
    """
    Retrieve the current candidate profile settings from the database.
    
    Returns:
        dict: The user's profile details including name, email, API keys, etc.
    """
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
    conn.close()
    if row:
        return dict(row)
    return {}

@app.post("/api/profile")
def update_profile(profile: ProfileUpdate) -> dict:
    """
    Update the candidate's profile settings, upload API keys, and update runtime environment variables.
    
    Args:
        profile (ProfileUpdate): The validated profile details.
        
    Returns:
        dict: Success status and feedback message.
    """
    conn = get_db_connection()
    conn.execute("""
    UPDATE profile
    SET name = ?, email = ?, phone = ?, github = ?, linkedin = ?, website = ?, base_resume_text = ?, gemini_api_key = ?, suggested_keywords = '', google_maps_api_key = ?
    WHERE id = 1
    """, (profile.name, profile.email, profile.phone, profile.github, profile.linkedin, profile.website, profile.base_resume_text, profile.gemini_api_key, profile.google_maps_api_key))
    conn.commit()
    conn.close()
    
    # Also set API keys in environment for current process
    if profile.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = profile.gemini_api_key
        config.GEMINI_API_KEY = profile.gemini_api_key
    if profile.google_maps_api_key:
        os.environ["GOOGLE_MAPS_API_KEY"] = profile.google_maps_api_key
        config.GOOGLE_MAPS_API_KEY = profile.google_maps_api_key
        
    return {"success": True, "message": "Profile updated successfully."}

@app.post("/api/profile/upload-resume")
async def upload_resume(file: UploadFile = File(...)) -> dict:
    """
    Handle uploading a base resume in text or markdown format, extract its text content,
    and save it to the profile settings table.
    
    Args:
        file (UploadFile): The uploaded file resource.
        
    Returns:
        dict: Success status and the parsed resume text.
    """
    if not file.filename.endswith(('.txt', '.md')):
        raise HTTPException(status_code=400, detail="Only .txt and .md text files are supported for resume upload.")
        
    try:
        content = await file.read()
        resume_text = content.decode("utf-8")
        
        # Save to database
        conn = get_db_connection()
        conn.execute("UPDATE profile SET base_resume_text = ?, suggested_keywords = '' WHERE id = 1", (resume_text,))
        conn.commit()
        conn.close()
        
        return {"success": True, "resume_text": resume_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")

IS_SEARCHING = False

def run_search_wrapper(keywords: str, location: str) -> None:
    """
    A background helper task that triggers the crawling and matching of jobs,
    ensuring that the searching status flag is reset once completed.
    
    Args:
        keywords (str): Semi-colon or space-separated job keywords.
        location (str): Semicolon-separated target locations.
    """
    global IS_SEARCHING
    try:
        run_job_search_and_matching(keywords, location)
    finally:
        IS_SEARCHING = False

# Job listings endpoints
@app.get("/api/jobs")
def get_jobs() -> list[dict]:
    """
    Retrieve all job postings stored in the database, ordered by match score descending.
    
    Returns:
        list[dict]: A list of job listing records.
    """
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM jobs ORDER BY match_score DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int) -> dict:
    """
    Delete a specific job posting from the database, along with its associated application record.
    
    Args:
        job_id (int): The unique ID of the job posting to delete.
        
    Returns:
        dict: Success status and feedback message.
    """
    conn = get_db_connection()
    try:
        # Check if exists
        job = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
            
        # Delete corresponding application first to prevent foreign key issues
        conn.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
        # Delete job
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return {"success": True, "message": "Job posting deleted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete job: {str(e)}")
    finally:
        conn.close()

@app.get("/api/jobs/status")
def get_search_status() -> dict:
    """
    Get the status of the background job search process.
    
    Returns:
        dict: Containing a boolean key 'searching' indicating search status.
    """
    global IS_SEARCHING
    return {"searching": IS_SEARCHING}

@app.post("/api/jobs/search")
def search_jobs(req: SearchRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Start a background job crawl and match analysis task using the user keywords and locations.
    
    Args:
        req (SearchRequest): The search keywords and locations.
        background_tasks (BackgroundTasks): FastAPI background task manager.
        
    Returns:
        dict: Success status and status message.
    """
    global IS_SEARCHING
    if IS_SEARCHING:
        return {"success": False, "message": "A job search is already in progress."}
    IS_SEARCHING = True
    background_tasks.add_task(run_search_wrapper, req.keywords, req.location)
    return {"success": True, "message": "Job search started in background."}

# Resume tailoring endpoints
@app.post("/api/jobs/{job_id}/tailor")
def tailor_resume_endpoint(job_id: int) -> dict:
    """
    Generate a tailored resume and cover letter for a specific job matching the candidate's profile.
    
    Args:
        job_id (int): The ID of the job listing.
        
    Returns:
        dict: Containing the tailored resume, cover letter, and U.S. HQ location.
    """
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    profile = conn.execute("SELECT base_resume_text, gemini_api_key, google_maps_api_key FROM profile LIMIT 1").fetchone()
    
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found.")
    if not profile or not profile["base_resume_text"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Base resume text is missing. Please setup your profile.")
        
    conn.close()
    
    api_key = profile["gemini_api_key"]
    google_key = profile["google_maps_api_key"]
    res = tailor_resume_and_cover_letter(
        base_resume_text=profile["base_resume_text"],
        job_title=job["title"],
        company_name=job["company"],
        job_description=job["description"],
        api_key=api_key
    )
    
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Tailoring failed."))
        
    # Generate HTML/PDF file paths
    resume_filename = f"tailored_resume_{job_id}.pdf"
    resume_pdf_path = os.path.join(config.OUTPUT_DIR, resume_filename)
    
    # Save the tailored resume PDF
    generate_resume_pdf(res["tailored_resume"], resume_pdf_path)
    
    # Save cover letter as text
    cover_letter_text = res["cover_letter"]
    
    # Update application log or store inside job metadata
    # (For convenience we will store tailored details in the applications table with a tailored/pending status)
    conn = get_db_connection()
    # Check if application record already exists for this job
    existing = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    
    # Find headquarters
    hq = find_us_headquarters(job["company"], api_key, google_key)
    
    if existing:
        conn.execute("""
        UPDATE applications
        SET company = ?, position = ?, us_hq = ?, tailored_resume_path = ?, cover_letter = ?, status = 'tailored'
        WHERE job_id = ?
        """, (job["company"], job["title"], hq, resume_pdf_path, cover_letter_text, job_id))
    else:
        conn.execute("""
        INSERT INTO applications (job_id, company, position, date_applied, us_hq, tailored_resume_path, cover_letter, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'tailored')
        """, (job_id, job["company"], job["title"], datetime.now().strftime("%Y-%m-%d"), hq, resume_pdf_path, cover_letter_text))
        
    # Update job status to tailored
    conn.execute("UPDATE jobs SET status = 'tailored' WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "tailored_resume": res["tailored_resume"],
        "cover_letter": cover_letter_text,
        "us_hq": hq
    }

@app.get("/api/jobs/{job_id}/tailored")
def get_tailored_details(job_id: int) -> dict:
    """
    Retrieve previously generated tailored materials (cover letter, PDF path, HQ address) for a job.
    
    Args:
        job_id (int): The ID of the job listing.
        
    Returns:
        dict: Tailored details or error message.
    """
    conn = get_db_connection()
    app_row = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    if app_row:
        # Load tailored resume markdown text (we can rebuild it or return letter + path)
        return {
            "success": True,
            "cover_letter": app_row["cover_letter"],
            "tailored_resume_path": app_row["tailored_resume_path"],
            "us_hq": app_row["us_hq"],
            "status": app_row["status"]
        }
    return {"success": False, "message": "No tailored details found for this job."}

# Auto-apply endpoint
@app.post("/api/jobs/{job_id}/apply")
def apply_job(job_id: int, headed: bool = True) -> dict:
    """
    Trigger the automated application filler using Playwright browser automation.
    
    Args:
        job_id (int): The ID of the job listing to apply to.
        headed (bool, optional): Whether to run the browser in headed mode so the user can watch. Defaults to True.
        
    Returns:
        dict: Success status and feedback message.
    """
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    app_row = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    profile = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
    conn.close()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not app_row or not app_row["tailored_resume_path"]:
        raise HTTPException(status_code=400, detail="Resume has not been tailored for this job yet.")
        
    # Assemble candidate profile details
    candidate_profile = {
        "name": profile["name"],
        "email": profile["email"],
        "phone": profile["phone"],
        "github": profile["github"],
        "linkedin": profile["linkedin"],
        "website": profile["website"]
    }
    
    # Run browser application filling
    api_key = profile["gemini_api_key"]
    res = fill_application_form(
        url=job["url"],
        candidate_profile=candidate_profile,
        tailored_resume_path=app_row["tailored_resume_path"],
        cover_letter_text=app_row["cover_letter"],
        api_key=api_key,
        headed=headed
    )
    
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Application form filling failed."))
        
    # Log application success
    conn = get_db_connection()
    conn.execute("""
    UPDATE applications
    SET date_applied = ?, status = 'applied'
    WHERE job_id = ?
    """, (datetime.now().strftime("%Y-%m-%d"), job_id))
    
    conn.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": res.get("msg", "Application submitted!"),
        "auto_submitted": res.get("auto_submitted", False)
    }

# Applications logs endpoint
@app.get("/api/applications")
def get_applications() -> list[dict]:
    """
    Retrieve logs of all job applications that have been processed.
    
    Returns:
        list[dict]: A list of application records.
    """
    conn = get_db_connection()
    rows = conn.execute("""
    SELECT a.*, j.url 
    FROM applications a
    LEFT JOIN jobs j ON a.job_id = j.id
    ORDER BY a.date_applied DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Mount output folder to serve tailored resume PDFs
if not os.path.exists(config.OUTPUT_DIR):
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(config.OUTPUT_DIR)), name="output")

# Mount frontend static folder
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def read_index() -> FileResponse:
    """
    Serve the main SPA dashboard index page.
    
    Returns:
        FileResponse: The HTML page response.
    """
    return FileResponse(os.path.join(static_dir, "index.html"))
