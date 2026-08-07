import os
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import date, datetime, timedelta

import config
from database import get_db_connection
from tailor import analyze_job_match, apply_resume_section_template, finalize_cover_letter, tailor_resume_and_cover_letter
from searcher import (
    MAX_JOB_DESCRIPTION_CHARS,
    canonicalize_job_url,
    inspect_job_posting,
    provider_for_url,
    run_job_search_and_matching,
    validate_public_http_url,
)
from utils import generate_resume_pdf, find_us_headquarters
from lifecycle import undo_latest_lifecycle_change, update_lifecycle
from job_cleanup import apply_cleanup, cleanup_preview
from materials import material_download_name, persist_cover_letter, resolve_output_file
from analytics import (
    capture_event,
    duration_bucket,
    initialize_analytics,
    shutdown_analytics,
    source_category,
)

APP_BUILD = "20260807.3"
MAX_RESUME_UPLOAD_BYTES = 2 * 1024 * 1024
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_HTTP_METHODS = {"GET", "HEAD", "OPTIONS"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start optional analytics and stop it within a bounded shutdown budget."""
    initialize_analytics(APP_BUILD)
    try:
        yield
    finally:
        shutdown_analytics(timeout_seconds=0.5)


app = FastAPI(title="AI Job Applier Agent API", lifespan=lifespan)


def _local_authority(authority: str, scheme: str = "http") -> Optional[tuple[str, int]]:
    """Return a normalized loopback host and port, or None for an unsafe authority."""
    if not authority or any(character in authority for character in "/\\@"):
        return None
    try:
        parsed = urlsplit(f"//{authority}")
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    if hostname not in LOCAL_HOSTS:
        return None
    return hostname, port


def _same_local_origin(value: str, request_authority: tuple[str, int], scheme: str) -> bool:
    """Validate an Origin or Referer value against the exact local request origin."""
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    return (
        parsed.scheme == scheme
        and hostname == request_authority[0]
        and port == request_authority[1]
        and parsed.username is None
        and parsed.password is None
    )


@app.middleware("http")
async def protect_local_browser_boundary(request: Request, call_next):
    """Enforce the loopback-only browser trust boundary and prevent cross-site writes."""
    request_authority = _local_authority(request.headers.get("host", ""), request.url.scheme)
    if request_authority is None:
        return JSONResponse({"detail": "Invalid local Host header."}, status_code=400)

    if request.method.upper() not in SAFE_HTTP_METHODS:
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site in {"cross-site", "same-site"}:
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        source = origin or referer
        if source and not _same_local_origin(source, request_authority, request.url.scheme):
            return JSONResponse({"detail": "Request origin does not match this local app."}, status_code=403)

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
    prefer_us_headquarters: bool = True


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


class ManualJobPreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ManualJobSaveRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(min_length=2, max_length=180)
    company: str = Field(min_length=2, max_length=180)
    description: str = Field(min_length=20, max_length=MAX_JOB_DESCRIPTION_CHARS)
    location: str = Field(default="", max_length=240)
    work_arrangement: Literal["", "remote", "hybrid", "on_site"] = ""
    employment_type: Literal["", "full_time", "part_time", "contract", "temporary", "internship"] = ""
    compensation: str = Field(default="", max_length=160)

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
        base_resume_text = ?, resume_mode = ?, prefer_us_headquarters = ?, suggested_keywords = ''
    WHERE id = 1
    """, (profile.name, profile.email, profile.phone, profile.github, profile.linkedin, profile.website, profile.base_resume_text, profile.resume_mode, int(profile.prefer_us_headquarters)))
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
    if not (file.filename or "").lower().endswith(('.txt', '.md')):
        raise HTTPException(status_code=400, detail="Only .txt and .md text files are supported for resume upload.")

    try:
        content = bytearray()
        while chunk := await file.read(64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_RESUME_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Resume files must be 2 MB or smaller.",
                )
        try:
            resume_text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Resume files must use UTF-8 text encoding.") from exc

        # Save to database
        conn = get_db_connection()
        try:
            conn.execute("UPDATE profile SET base_resume_text = ?, suggested_keywords = '' WHERE id = 1", (resume_text,))
            conn.commit()
        finally:
            conn.close()

        return {"success": True, "resume_text": resume_text}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read the resume file.") from exc
    finally:
        await file.close()

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
        SELECT j.*, a.date_applied, a.application_method, a.notes, a.follow_up_date,
               CASE WHEN a.tailored_resume_path IS NOT NULL OR a.cover_letter IS NOT NULL
                    THEN 1 ELSE 0 END AS has_materials
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        {where_clause}
        ORDER BY j.match_score DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/jobs/import/preview")
def preview_manual_job(req: ManualJobPreviewRequest) -> dict:
    """Validate a public posting URL and extract editable details without saving it."""
    canonical_url = canonicalize_job_url(req.url)
    is_public, reason = validate_public_http_url(canonical_url)
    if not is_public:
        raise HTTPException(status_code=422, detail=reason)

    conn = get_db_connection()
    existing = conn.execute(
        "SELECT id, title, company, status FROM jobs WHERE url = ?",
        (canonical_url,),
    ).fetchone()
    conn.close()
    if existing:
        return {
            "success": True,
            "duplicate": True,
            "existing_job": dict(existing),
            "job": {"url": canonical_url},
        }

    outcome = inspect_job_posting(canonical_url, allow_partial=True)
    details = outcome["details"]
    resolved_url = canonicalize_job_url(details.get("url") or canonical_url)
    if resolved_url != canonical_url:
        conn = get_db_connection()
        existing = conn.execute(
            "SELECT id, title, company, status FROM jobs WHERE url = ?",
            (resolved_url,),
        ).fetchone()
        conn.close()
        if existing:
            return {
                "success": True,
                "duplicate": True,
                "existing_job": dict(existing),
                "job": {"url": resolved_url},
            }

    provider = provider_for_url(resolved_url)
    source = provider if provider != "unknown" else (urlsplit(resolved_url).hostname or "manual")
    job = {
        "url": resolved_url,
        "title": str(details.get("title") or "")[:180],
        "company": str(details.get("company") or "")[:180],
        "description": str(details.get("description") or "")[:MAX_JOB_DESCRIPTION_CHARS],
        "location": str(details.get("location") or "")[:240],
        "work_arrangement": details.get("work_arrangement") or "",
        "employment_type": details.get("employment_type") or "",
        "compensation": str(details.get("compensation") or "")[:160],
        "source": source,
    }
    extraction_succeeded = all(job.get(key) for key in ("title", "company", "description"))
    extraction_status = outcome.get("status", "provider_error")
    messages = {
        "ok": "Review the extracted details before saving.",
        "partial": "Some details were extracted. Complete the missing required fields before saving.",
        "stale": "The source reports that this posting is no longer published. Save only if you have verified the details elsewhere.",
        "access_challenge": "The site blocked automated reading. Enter the posting details manually before saving.",
        "embedded_content": "The posting is inside a protected embedded frame. Enter the posting details manually before saving.",
        "format_drift": "The page loaded but its job data was not in a recognized format. Enter the required fields manually.",
        "provider_error": "The posting could not be retrieved. You can still enter the required fields manually.",
    }
    return {
        "success": True,
        "duplicate": False,
        "extraction_succeeded": extraction_succeeded,
        "extraction_status": extraction_status,
        "message": messages.get(extraction_status, "Complete the required fields before saving."),
        "job": job,
    }


@app.post("/api/jobs/import")
def save_manual_job(req: ManualJobSaveRequest) -> dict:
    """Save one reviewed posting and score it when the configured AI is available."""
    canonical_url = canonicalize_job_url(req.url)
    is_public, reason = validate_public_http_url(canonical_url)
    if not is_public:
        raise HTTPException(status_code=422, detail=reason)

    title = " ".join(req.title.split())
    company = " ".join(req.company.split())
    description = req.description.strip()
    location = " ".join(req.location.split())
    compensation = " ".join(req.compensation.split())
    provider = provider_for_url(canonical_url)
    source = provider if provider != "unknown" else (urlsplit(canonical_url).hostname or "manual")
    if len(title) < 2 or len(company) < 2 or len(description) < 20:
        raise HTTPException(
            status_code=422,
            detail="Company and title are required, and the description must contain at least 20 characters.",
        )

    conn = get_db_connection()
    try:
        profile = conn.execute(
            "SELECT base_resume_text, gemini_api_key FROM profile LIMIT 1"
        ).fetchone()
        match_score = None
        match_analysis = "Manual import saved without AI match analysis."
        if profile and profile["base_resume_text"] and profile["gemini_api_key"]:
            try:
                match = analyze_job_match(
                    profile["base_resume_text"], title, company, description, profile["gemini_api_key"]
                )
                if match.get("success"):
                    match_score = max(0, min(100, int(match["match_score"])))
                    match_analysis = str(match.get("match_analysis") or "Matched successfully.")
            except (AttributeError, KeyError, TypeError, ValueError):
                match_score = None

        # AI analysis may make a network call, so do it before taking the write
        # lock. The duplicate check stays inside the transaction to close the
        # race between concurrent imports of the same canonical URL.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, title, company, status FROM jobs WHERE url = ?",
            (canonical_url,),
        ).fetchone()
        if existing:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"This posting is already saved as job #{existing['id']}.",
            )

        now = datetime.now().isoformat(timespec="seconds")
        cursor = conn.execute("""
            INSERT INTO jobs (
                title, company, description, url, match_score, match_analysis,
                date_found, status, location, work_arrangement, employment_type,
                compensation, source, last_checked_at, is_expired
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'matched', ?, ?, ?, ?, ?, ?, 0)
        """, (
            title, company, description, canonical_url, match_score, match_analysis,
            date.today().isoformat(), location, req.work_arrangement,
            req.employment_type, compensation, source, now,
        ))
        job_id = cursor.lastrowid
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "success": True,
        "job_id": job_id,
        "match_score": match_score,
        "message": (
            "Job imported and match analysis completed."
            if match_score is not None else
            "Job imported. Match analysis was unavailable, so it is shown as unscored."
        ),
    }


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
        capture_event(
            "job_lifecycle_updated",
            {
                "result": "success",
                "source_category": "manual",
                "from_status": result["previous_status"],
                "to_status": result["status"],
            },
        )
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
    outcome = inspect_job_posting(job["url"])
    details = outcome["details"]
    if not details and outcome.get("status") != "stale":
        raise HTTPException(
            status_code=503,
            detail="The posting could not be verified because its source was unavailable or unreadable. No job status was changed.",
        )
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
                expiration_reason = 'Source reports posting is no longer published'
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
    capture_event(
        "job_search_started",
        {
            "result": "success",
            "source_category": "saved_search" if (req.save_search or req.saved_search_id) else "manual",
        },
    )
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
    started_at = time.monotonic()
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    profile = conn.execute("SELECT base_resume_text, gemini_api_key, google_maps_api_key, resume_mode, prefer_us_headquarters FROM profile LIMIT 1").fetchone()
    
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
    
    # Persist both artifacts before exposing this job as ready for manual application.
    cover_letter_text = res["cover_letter"]
    cover_letter_path = persist_cover_letter(job_id, cover_letter_text)
    
    # Update application log or store inside job metadata
    # (For convenience we will store tailored details in the applications table with a tailored/pending status)
    conn = get_db_connection()
    # Check if application record already exists for this job
    existing = conn.execute("SELECT id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    
    # Find headquarters
    hq = find_us_headquarters(
        job["company"],
        api_key,
        google_key,
        prefer_us=bool(profile["prefer_us_headquarters"]),
        job_location=job["location"] or "",
        job_url=job["url"] or "",
    )
    
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        conn.execute("""
        UPDATE applications
        SET company = ?, position = ?, us_hq = ?, tailored_resume_path = ?, cover_letter_path = ?, tailored_resume_text = ?, cover_letter = ?,
            created_at = COALESCE(created_at, ?), tailored_at = ?,
            status = CASE WHEN status IN ('applied', 'interview', 'offer') THEN status ELSE 'tailored' END
        WHERE job_id = ?
        """, (job["company"], job["title"], hq, resume_pdf_path, cover_letter_path, res["tailored_resume"], cover_letter_text, now, now, job_id))
    else:
        conn.execute("""
        INSERT INTO applications (
            job_id, company, position, date_applied, us_hq, tailored_resume_path,
            cover_letter_path, tailored_resume_text, cover_letter, status, created_at, tailored_at
        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 'tailored', ?, ?)
        """, (job_id, job["company"], job["title"], hq, resume_pdf_path, cover_letter_path, res["tailored_resume"], cover_letter_text, now, now))
        
    # Update job status to tailored
    conn.execute("""
        UPDATE jobs
        SET status = CASE WHEN status IN ('applied', 'interview', 'offer') THEN status ELSE 'tailored' END
        WHERE id = ?
    """, (job_id,))
    conn.commit()
    conn.close()
    capture_event(
        "resume_tailored",
        {
            "result": "success",
            "source_category": source_category(job["source"]),
            "duration_bucket": duration_bucket(time.monotonic() - started_at),
        },
    )
    
    return {
        "success": True,
        "tailored_resume": res["tailored_resume"],
        "cover_letter": cover_letter_text,
        "us_hq": hq,
        "resume_download_url": f"/api/jobs/{job_id}/materials/resume",
        "cover_letter_download_url": f"/api/jobs/{job_id}/materials/cover-letter",
        "manual_application_url": f"/api/jobs/{job_id}/apply-manually",
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
        cover_letter_path = app_row["cover_letter_path"]
        try:
            resolve_output_file(cover_letter_path, ".txt")
        except (ValueError, FileNotFoundError):
            if app_row["cover_letter"]:
                cover_letter_path = persist_cover_letter(job_id, app_row["cover_letter"])
                conn = get_db_connection()
                conn.execute(
                    "UPDATE applications SET cover_letter_path = ? WHERE job_id = ?",
                    (cover_letter_path, job_id),
                )
                conn.commit()
                conn.close()
        # Load tailored resume markdown text (we can rebuild it or return letter + path)
        return {
            "success": True,
            "cover_letter": app_row["cover_letter"],
            "tailored_resume": app_row["tailored_resume_text"] or "",
            "tailored_resume_path": app_row["tailored_resume_path"],
            "cover_letter_path": cover_letter_path,
            "us_hq": app_row["us_hq"],
            "status": app_row["status"],
            "resume_download_url": f"/api/jobs/{job_id}/materials/resume",
            "cover_letter_download_url": f"/api/jobs/{job_id}/materials/cover-letter",
            "manual_application_url": f"/api/jobs/{job_id}/apply-manually",
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

    cover_letter_path = persist_cover_letter(job_id, cover_letter)
    conn = get_db_connection()
    conn.execute("""
        UPDATE applications
        SET tailored_resume_text = ?, tailored_resume_path = ?, cover_letter_path = ?, cover_letter = ?
        WHERE job_id = ?
    """, (resume_text, output_path, cover_letter_path, cover_letter, job_id))
    conn.commit()
    conn.close()
    return {
        "success": True,
        "pdf_page_count": pdf_result["page_count"],
        "pdf_compact": pdf_result["compact"],
        "resume_download_url": f"/api/jobs/{job_id}/materials/resume",
        "cover_letter_download_url": f"/api/jobs/{job_id}/materials/cover-letter",
    }

def _application_materials(job_id: int):
    """Load a job and its generated materials for a guided manual application."""
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT a.*, j.company AS job_company, j.title AS job_title,
               j.url AS job_url, j.source AS job_source, j.status AS job_status
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.job_id = ?
        """,
        (job_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Generated application materials were not found.")
    return row


@app.get("/api/jobs/{job_id}/materials/resume")
def download_tailored_resume(job_id: int) -> FileResponse:
    """Download the generated resume using a readable, filesystem-safe name."""
    material = _application_materials(job_id)
    try:
        path = resolve_output_file(material["tailored_resume_path"], ".pdf")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="The generated resume PDF is unavailable.") from exc
    capture_event(
        "material_downloaded",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "material_type": "resume",
        },
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=material_download_name(
            material["job_company"], material["job_title"], "resume", ".pdf"
        ),
    )


@app.get("/api/jobs/{job_id}/materials/cover-letter")
def download_cover_letter(job_id: int) -> FileResponse:
    """Download the generated cover letter as a plain-text attachment."""
    material = _application_materials(job_id)
    cover_letter_path = material["cover_letter_path"]
    try:
        path = resolve_output_file(cover_letter_path, ".txt")
    except (ValueError, FileNotFoundError):
        if not material["cover_letter"]:
            raise HTTPException(status_code=404, detail="The generated cover letter is unavailable.")
        cover_letter_path = persist_cover_letter(job_id, material["cover_letter"])
        conn = get_db_connection()
        conn.execute(
            "UPDATE applications SET cover_letter_path = ? WHERE job_id = ?",
            (cover_letter_path, job_id),
        )
        conn.commit()
        conn.close()
        path = resolve_output_file(cover_letter_path, ".txt")
    capture_event(
        "material_downloaded",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "material_type": "cover_letter",
        },
    )
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=material_download_name(
            material["job_company"], material["job_title"], "cover-letter", ".txt"
        ),
    )


@app.get("/api/jobs/{job_id}/apply-manually")
def open_manual_application(job_id: int) -> RedirectResponse:
    """Open a verified employer URL after confirming required materials exist."""
    material = _application_materials(job_id)
    try:
        resolve_output_file(material["tailored_resume_path"], ".pdf")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail="Downloadable application materials are not ready.") from exc
    if not material["cover_letter"]:
        raise HTTPException(status_code=400, detail="Downloadable application materials are not ready.")

    parsed_url = urlsplit(material["job_url"] or "")
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise HTTPException(status_code=400, detail="The employer application URL is invalid.")
    from_status = material["status"] or material["job_status"] or "matched"
    if from_status not in {"matched", "tailored", "form_filled", "submitted", "applied", "interview", "offer", "rejected", "withdrawn", "closed"}:
        from_status = "matched"
    capture_event(
        "manual_application_opened",
        {
            "result": "success",
            "source_category": source_category(material["job_source"]),
            "from_status": from_status,
        },
    )
    return RedirectResponse(material["job_url"], status_code=302)

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
