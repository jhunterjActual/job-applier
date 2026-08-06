"""Application lifecycle rules shared by the API and regression tests."""

from datetime import date, datetime
import json
import sqlite3


JOB_STATUSES = frozenset({
    "matched",
    "tailored",
    "form_filled",
    "submitted",
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "closed",
    "ignored",
})

PIPELINE_STATUSES = (
    "matched", "tailored", "form_filled", "submitted", "applied",
    "interview", "offer", "rejected", "withdrawn", "closed",
)

APPLIED_OR_LATER = frozenset({"applied", "interview", "offer", "rejected", "withdrawn", "closed"})


def update_lifecycle(
    connection: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    applied_on: date | None = None,
    method: str | None = None,
    notes: str | None = None,
    follow_up_on: date | None = None,
    source: str = "manual",
    record_history: bool = True,
) -> dict:
    """Apply a user-confirmed lifecycle state without discarding materials."""
    if status not in PIPELINE_STATUSES:
        raise ValueError(f"Unsupported lifecycle status: {status}")

    job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise LookupError("Job not found.")
    application = connection.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    previous_status = application["status"] if application else job["status"]
    now = datetime.now().isoformat(timespec="seconds")

    existing_applied_on = application["date_applied"] if application else None
    effective_applied_on = applied_on.isoformat() if applied_on else existing_applied_on
    if status in APPLIED_OR_LATER and not effective_applied_on:
        effective_applied_on = date.today().isoformat()
    if status not in APPLIED_OR_LATER:
        effective_applied_on = None

    evidence = json.dumps({"source": source, "confirmed_by_user": True}) if source == "manual" else None
    supplied_method = f"manual:{method}" if source == "manual" and method else method
    values = {
        "date_applied": effective_applied_on,
        "status": status,
        "application_method": supplied_method or (application["application_method"] if application else None),
        "submission_evidence": evidence or (application["submission_evidence"] if application else None),
        "notes": notes if notes is not None else (application["notes"] if application else None),
        "follow_up_date": follow_up_on.isoformat() if follow_up_on else None,
    }

    if application:
        connection.execute("""
            UPDATE applications
            SET date_applied = ?, status = ?, application_method = ?, submission_evidence = ?,
                notes = ?, follow_up_date = ?,
                submitted_at = CASE WHEN ? IN ('applied','interview','offer','rejected','withdrawn','closed')
                    THEN COALESCE(submitted_at, ?) WHEN ? IN ('matched','tailored','form_filled') THEN NULL ELSE submitted_at END,
                confirmed_at = CASE WHEN ? IN ('applied','interview','offer','rejected','withdrawn','closed')
                    THEN COALESCE(confirmed_at, ?) ELSE NULL END
            WHERE job_id = ?
        """, (
            values["date_applied"], status, values["application_method"], values["submission_evidence"],
            values["notes"], values["follow_up_date"], status, now, status, status, now, job_id,
        ))
    else:
        connection.execute("""
            INSERT INTO applications (
                job_id, company, position, date_applied, status, created_at,
                submitted_at, confirmed_at, application_method, submission_evidence,
                notes, follow_up_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, job["company"], job["title"], values["date_applied"], status, now,
            now if status in APPLIED_OR_LATER else None,
            now if status in APPLIED_OR_LATER else None,
            values["application_method"], values["submission_evidence"], values["notes"], values["follow_up_date"],
        ))

    connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    if record_history and previous_status != status:
        connection.execute("""
            INSERT INTO application_status_history
                (job_id, from_status, to_status, changed_at, source, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (job_id, previous_status, status, now, source, notes))
    return {"previous_status": previous_status, "status": status, "date_applied": effective_applied_on}


def undo_latest_lifecycle_change(connection: sqlite3.Connection, job_id: int) -> dict:
    """Undo the most recent non-undone manual lifecycle change."""
    history = connection.execute("""
        SELECT * FROM application_status_history
        WHERE job_id = ? AND undone_at IS NULL
        ORDER BY id DESC LIMIT 1
    """, (job_id,)).fetchone()
    if not history:
        raise LookupError("No lifecycle change is available to undo.")
    result = update_lifecycle(
        connection, job_id, history["from_status"], source="undo", record_history=False
    )
    if history["from_status"] == "matched":
        application = connection.execute(
            "SELECT tailored_resume_path, cover_letter FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if application and not application["tailored_resume_path"] and not application["cover_letter"]:
            connection.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
    connection.execute(
        "UPDATE application_status_history SET undone_at = ? WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"), history["id"]),
    )
    return result
