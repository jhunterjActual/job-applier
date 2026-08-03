import os
import json
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import date, datetime, timedelta

import config
from database import get_db_connection
from tailor import apply_resume_section_template, finalize_cover_letter, tailor_resume_and_cover_letter
from searcher import run_job_search_and_matching, scrape_job_details
from applier import fill_application_form
from utils import generate_resume_pdf, generate_cover_letter_pdf, find_us_headquarters
from lifecycle import PIPELINE_STATUSES, status_from_automation, undo_latest_lifecycle_change, update_lifecycle
from job_cleanup import apply_cleanup, cleanup_preview

APP_BUILD = "20260803.8"

app = FastAPI(title="AI Job Applier Agent API")


@app.middleware("http")
async def prevent_stale_local_ui(request, call_next):
    """Keep the local dashboard HTML and JavaScript from drifting out of sync."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

class ProfileUpdate(BaseModel):
    name: str
    email: str
    phone: str
    github: str
    linkedin: str
    website: str
    base_resume_text: str
    resume_mode: Literal["it", "technical_executive", "general_professional", "federal", "healthcare", "education", "sales", "trades_operations", "academic_cv", "cover_letter"] = "general_professional"


class ProfileSecretsUpdate(BaseModel):
    gemini_api_key: Optional[str] = None
    google_maps_api_key: Optional[str] = None

class SearchRequest(BaseModel):
    keywords: str
    location: Optional[str] = ""
    save_search: bool = False
    saved_search_name: Optional[str] = None
    saved_search_id: Optional[int] = None
    schedule_frequency: Literal["none", "daily", "weekly"] = "none"

class CleanupRequest(BaseModel):
    action: Literal["archive", "delete", "restore"]
    preview_token: str


class LifecycleUpdateRequest(BaseModel):
    status: Literal["matched", "tailored", "form_filled", "submitted", "applied", "interview", "offer", "rejected", "withdrawn", "closed"]
    applied_on: Optional[date] = None
    method: Optional[Literal["company_site", "job_board", "email", "recruiter", "referral", "other"]] = None
    notes: Optional[str] = None
    follow_up_on: Optional[date] = None


class MaterialsUpdateRequest(BaseModel):
    tailored_resume: str
    cover_letter: str


@app.get("/api/version")
def get_app_version() -> dict:
    """Identify the running backend so launchers never open a stale instance."""
    return {"build": APP_BUILD}

# Profile endpoints
@app.get("/api/profile")
def get_profile() -> dict:
    """
    Retrieve the current candidate profile settings from the database.
    
    Returns:
        dict: Profile details plus secret-presence flags; never plaintext keys.
    """
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
    conn.close()
    if row:
        result = dict(row)
        result["gemini_api_key_configured"] = bool(result.pop("gemini_api_key", ""))
        result["google_maps_api_key_configured"] = bool(result.pop("google_maps_api_key", ""))
        return result
    return {}

@app.post("/api/profile")
def update_profile(profile: ProfileUpdate) -> dict:
    """
    Update non-secret candidate profile settings.
    
    Args:
        profile (ProfileUpdate): The validated profile details.
        
    Returns:
        dict: Success status and feedback message.
    """
    conn = get_db_connection()
    conn.execute("""
    UPDATE profile
    SET name = ?, email = ?, phone = ?, github = ?, linkedin = ?, website = ?,
        base_resume_text = ?, resume_mode = ?, suggested_keywords = ''
    WHERE id = 1
    """, (profile.name, profile.email, profile.phone, profile.github, profile.linkedin, profile.website, profile.base_resume_text, profile.resume_mode))
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Profile updated successfully."}


@app.put("/api/profile/secrets")
def update_profile_secrets(secrets: ProfileSecretsUpdate) -> dict:
    """Update only explicitly supplied local API secrets without returning them."""
    updates = []
    values = []
    if secrets.gemini_api_key is not None:
        updates.append("gemini_api_key = ?")
        values.append(secrets.gemini_api_key.strip())
    if secrets.google_maps_api_key is not None:
        updates.append("google_maps_api_key = ?")
        values.append(secrets.google_maps_api_key.strip())
    if updates:
        conn = get_db_connection()
        conn.execute(f"UPDATE profile SET {', '.join(updates)} WHERE id = 1", values)
        conn.commit()
        conn.close()

    if secrets.gemini_api_key is not None:
        config.GEMINI_API_KEY = secrets.gemini_api_key.strip()
        os.environ["GEMINI_API_KEY"] = config.GEMINI_API_KEY
    if secrets.google_maps_api_key is not None:
        config.GOOGLE_MAPS_API_KEY = secrets.google_maps_api_key.strip()
        os.environ["GOOGLE_MAPS_API_KEY"] = config.GOOGLE_MAPS_API_KEY
    conn = get_db_connection()
    configured = conn.execute(
        "SELECT gemini_api_key, google_maps_api_key FROM profile WHERE id = 1"
    ).fetchone()
    conn.close()
    return {
        "success": True,
        "gemini_api_key_configured": bool(configured["gemini_api_key"]),
        "google_maps_api_key_configured": bool(configured["google_maps_api_key"]),
    }

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
LAST_SEARCH_RESULT = None

def run_search_wrapper(keywords: str, location: str) -> None:
    """
    A background helper task that triggers the crawling and matching of jobs,
    ensuring that the searching status flag is reset once completed.
    
    Args:
        keywords (str): Semi-colon or space-separated job keywords.
        location (str): Semicolon-separated target locations.
    """
    global IS_SEARCHING, LAST_SEARCH_RESULT
    try:
        LAST_SEARCH_RESULT = run_job_search_and_matching(keywords, location)
    except Exception as exc:
        LAST_SEARCH_RESULT = {"success": False, "error": str(exc), "provider_alerts": []}
    finally:
        IS_SEARCHING = False

# Job listings endpoints
@app.get("/api/jobs")
def get_jobs(include_archived: bool = False) -> list[dict]:
    """
    Retrieve all job postings stored in the database, ordered by match score descending.
    
    Returns:
        list[dict]: A list of job listing records.
    """
    conn = get_db_connection()
    where_clause = "" if include_archived else "WHERE j.status != 'archived'"
    rows = conn.execute(f"""
        SELECT j.*, a.date_applied, a.application_method, a.notes, a.follow_up_date
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        {where_clause}
        ORDER BY j.match_score DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.patch("/api/jobs/{job_id}/lifecycle")
def change_job_lifecycle(job_id: int, req: LifecycleUpdateRequest) -> dict:
    """Record a manual application or pipeline correction with audit history."""
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = update_lifecycle(
            conn,
            job_id,
            req.status,
            applied_on=req.applied_on,
            method=req.method,
            notes=req.notes,
            follow_up_on=req.follow_up_on,
            source="manual",
        )
        conn.commit()
        return {"success": True, **result}
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/lifecycle/undo")
def undo_job_lifecycle(job_id: int) -> dict:
    """Undo the latest recorded manual lifecycle correction."""
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = undo_latest_lifecycle_change(conn, job_id)
        conn.commit()
        return {"success": True, **result}
    except LookupError as exc:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/jobs/cleanup-preview")
def preview_job_cleanup() -> dict:
    """Preview protected archive/delete/restore candidate sets without mutation."""
    conn = get_db_connection()
    try:
        return cleanup_preview(conn)
    finally:
        conn.close()


@app.post("/api/jobs/cleanup")
def cleanup_jobs(req: CleanupRequest) -> dict:
    """Apply a previously previewed bulk cleanup operation transactionally."""
    global IS_SEARCHING
    if IS_SEARCHING:
        raise HTTPException(status_code=409, detail="Wait for the current job search to finish before cleanup.")

    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        affected = apply_cleanup(
            conn,
            req.action,
            req.preview_token,
            datetime.now().isoformat(timespec="seconds"),
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verbs = {"archive": "archived", "delete": "permanently deleted", "restore": "restored"}
    return {
        "success": True,
        "action": req.action,
        "affected": affected,
        "message": f"{affected} untouched job(s) {verbs[req.action]}.",
    }

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
    global IS_SEARCHING, LAST_SEARCH_RESULT
    return {"searching": IS_SEARCHING, "last_result": LAST_SEARCH_RESULT}


@app.get("/api/saved-searches")
def get_saved_searches() -> list[dict]:
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM saved_searches WHERE enabled = 1 ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.get("/api/saved-searches/due")
def get_due_saved_searches() -> list[dict]:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, name, keywords, location, schedule_frequency, next_alert_at
        FROM saved_searches
        WHERE enabled = 1 AND schedule_frequency != 'none'
          AND next_alert_at IS NOT NULL AND next_alert_at <= ?
        ORDER BY next_alert_at
    """, (now,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.delete("/api/saved-searches/{search_id}")
def delete_saved_search(search_id: int) -> dict:
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
    conn.commit()
    conn.close()
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="Saved search not found.")
    return {"success": True}


@app.post("/api/jobs/{job_id}/verify")
def verify_job_posting(job_id: int) -> dict:
    """Re-scrape one listing and mark it expired without deleting history."""
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    checked_at = datetime.now().isoformat(timespec="seconds")
    details = scrape_job_details(job["url"])
    conn = get_db_connection()
    if details:
        conn.execute("""
            UPDATE jobs SET title = ?, company = ?, description = ?, location = ?,
                work_arrangement = ?, employment_type = ?, compensation = ?, source = ?,
                last_checked_at = ?, is_expired = 0, expiration_reason = NULL
            WHERE id = ?
        """, (
            details["title"], details["company"], details["description"], details.get("location", ""),
            details.get("work_arrangement", ""), details.get("employment_type", ""),
            details.get("compensation", ""), details.get("source", ""), checked_at, job_id,
        ))
        result = {"success": True, "expired": False, "message": "Listing is still active."}
    else:
        next_status = "closed" if job["status"] in {"matched", "tailored", "form_filled", "submitted"} else job["status"]
        conn.execute("""
            UPDATE jobs SET status = ?, last_checked_at = ?, is_expired = 1,
                expiration_reason = 'Posting could not be validated'
            WHERE id = ?
        """, (next_status, checked_at, job_id))
        if next_status == "closed":
            conn.execute("UPDATE applications SET status = 'closed' WHERE job_id = ?", (job_id,))
        result = {"success": True, "expired": True, "message": "Listing appears closed or unavailable."}
    conn.commit()
    conn.close()
    return result

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
    global IS_SEARCHING, LAST_SEARCH_RESULT
    if IS_SEARCHING:
        return {"success": False, "message": "A job search is already in progress."}
    LAST_SEARCH_RESULT = None
    if req.save_search:
        search_name = (req.saved_search_name or req.keywords or "Resume-suggested search").strip()
        now = datetime.now().isoformat(timespec="seconds")
        conn = get_db_connection()
        interval = timedelta(days=1 if req.schedule_frequency == "daily" else 7)
        next_alert = (datetime.now() + interval).isoformat(timespec="seconds") if req.schedule_frequency != "none" else None
        conn.execute("""
            INSERT INTO saved_searches (
                name, keywords, location, created_at, last_run_at,
                schedule_frequency, next_alert_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(keywords, location) DO UPDATE SET
                name = excluded.name, last_run_at = excluded.last_run_at,
                schedule_frequency = excluded.schedule_frequency,
                next_alert_at = excluded.next_alert_at, enabled = 1
        """, (
            search_name, req.keywords.strip(), (req.location or "").strip(), now, now,
            req.schedule_frequency, next_alert,
        ))
        conn.commit()
        conn.close()
    elif req.saved_search_id:
        conn = get_db_connection()
        saved = conn.execute("SELECT schedule_frequency FROM saved_searches WHERE id = ?", (req.saved_search_id,)).fetchone()
        if saved:
            frequency = saved["schedule_frequency"] or "none"
            interval = timedelta(days=1 if frequency == "daily" else 7)
            next_alert = (datetime.now() + interval).isoformat(timespec="seconds") if frequency != "none" else None
            conn.execute(
                "UPDATE saved_searches SET last_run_at = ?, next_alert_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), next_alert, req.saved_search_id),
            )
            conn.commit()
        conn.close()
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
    profile = conn.execute("SELECT base_resume_text, gemini_api_key, google_maps_api_key, resume_mode FROM profile LIMIT 1").fetchone()
    
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
        api_key=api_key,
        resume_mode=profile["resume_mode"] or "general_professional",
    )
    
    if not res.get("success"):
        raise HTTPException(status_code=500, detail=res.get("error", "Tailoring failed."))
        
    # Generate HTML/PDF file paths
    resume_filename = f"tailored_resume_{job_id}.pdf"
    resume_pdf_path = os.path.join(config.OUTPUT_DIR, resume_filename)
    
    # Save the tailored resume PDF only when it satisfies the two-page contract.
    try:
        page_limit = 6 if (profile["resume_mode"] or "") == "academic_cv" else 2
        pdf_result = generate_resume_pdf(res["tailored_resume"], resume_pdf_path, max_pages=page_limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    
    # Save cover letter as text
    cover_letter_text = res["cover_letter"]
    
    # Update application log or store inside job metadata
    # (For convenience we will store tailored details in the applications table with a tailored/pending status)
    conn = get_db_connection()
    # Check if application record already exists for this job
    existing = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    
    # Find headquarters
    hq = find_us_headquarters(job["company"], api_key, google_key)
    
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        conn.execute("""
        UPDATE applications
        SET company = ?, position = ?, us_hq = ?, tailored_resume_path = ?, tailored_resume_text = ?, cover_letter = ?,
            created_at = COALESCE(created_at, ?), tailored_at = ?,
            status = CASE WHEN status IN ('applied', 'interview', 'offer') THEN status ELSE 'tailored' END
        WHERE job_id = ?
        """, (job["company"], job["title"], hq, resume_pdf_path, res["tailored_resume"], cover_letter_text, now, now, job_id))
    else:
        conn.execute("""
        INSERT INTO applications (
            job_id, company, position, date_applied, us_hq, tailored_resume_path,
            tailored_resume_text, cover_letter, status, created_at, tailored_at
        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 'tailored', ?, ?)
        """, (job_id, job["company"], job["title"], hq, resume_pdf_path, res["tailored_resume"], cover_letter_text, now, now))
        
    # Update job status to tailored
    conn.execute("""
        UPDATE jobs
        SET status = CASE WHEN status IN ('applied', 'interview', 'offer') THEN status ELSE 'tailored' END
        WHERE id = ?
    """, (job_id,))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "tailored_resume": res["tailored_resume"],
        "cover_letter": cover_letter_text,
        "us_hq": hq,
        "pdf_page_count": pdf_result["page_count"],
        "pdf_compact": pdf_result["compact"],
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
            "tailored_resume": app_row["tailored_resume_text"] or "",
            "tailored_resume_path": app_row["tailored_resume_path"],
            "us_hq": app_row["us_hq"],
            "status": app_row["status"]
        }
    return {"success": False, "message": "No tailored details found for this job."}


@app.patch("/api/jobs/{job_id}/tailored")
def update_tailored_details(job_id: int, req: MaterialsUpdateRequest) -> dict:
    """Save reviewed material edits and regenerate the attached resume PDF."""
    resume_text = req.tailored_resume.strip()
    if not resume_text:
        raise HTTPException(status_code=422, detail="Tailored resume text cannot be empty.")
    try:
        cover_letter = finalize_cover_letter(req.cover_letter)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conn = get_db_connection()
    application = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    profile = conn.execute("SELECT resume_mode FROM profile LIMIT 1").fetchone()
    conn.close()
    if not application:
        raise HTTPException(status_code=404, detail="No tailored materials found for this job.")

    output_path = application["tailored_resume_path"] or os.path.join(config.OUTPUT_DIR, f"tailored_resume_{job_id}.pdf")
    resume_mode = profile["resume_mode"] if profile and profile["resume_mode"] else "general_professional"
    try:
        resume_text = apply_resume_section_template(resume_text, resume_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    page_limit = 6 if resume_mode == "academic_cv" else 2
    try:
        pdf_result = generate_resume_pdf(resume_text, output_path, max_pages=page_limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    conn = get_db_connection()
    conn.execute("""
        UPDATE applications
        SET tailored_resume_text = ?, tailored_resume_path = ?, cover_letter = ?
        WHERE job_id = ?
    """, (resume_text, output_path, cover_letter, job_id))
    conn.commit()
    conn.close()
    return {"success": True, "pdf_page_count": pdf_result["page_count"], "pdf_compact": pdf_result["compact"]}

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
        
    # Record what the automation actually proved. Clicking a submit button is
    # only a submission attempt; it is not confirmation that the site accepted it.
    lifecycle_status = status_from_automation(res)
    now = datetime.now().isoformat(timespec="seconds")
    date_applied = datetime.now().strftime("%Y-%m-%d") if lifecycle_status == "applied" else None
    evidence = res.get("submission_evidence", "")
    review_fields = res.get("fields_needing_review", [])
    if review_fields:
        evidence = json.dumps({"confirmation": evidence, "review_fields": review_fields})

    conn = get_db_connection()
    conn.execute("""
    UPDATE applications
    SET date_applied = ?, status = ?, form_filled_at = ?,
        submitted_at = CASE WHEN ? IN ('submitted', 'applied') THEN ? ELSE submitted_at END,
        confirmed_at = CASE WHEN ? = 'applied' THEN ? ELSE confirmed_at END,
        application_method = 'automated', submission_evidence = ?
    WHERE job_id = ?
    """, (date_applied, lifecycle_status, now, lifecycle_status, now, lifecycle_status, now, evidence, job_id))
    
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (lifecycle_status, job_id))
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "message": res.get("msg", "Application submitted!"),
        "auto_submitted": res.get("auto_submitted", False),
        "status": lifecycle_status,
        "submission_confirmed": res.get("submission_confirmed", False),
        "fields_needing_review": review_fields,
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
    return FileResponse(
        os.path.join(static_dir, "index.html"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )
