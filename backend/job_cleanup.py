"""Conservative preview and bulk cleanup operations for discovered jobs."""

import hashlib
import sqlite3
from typing import Iterable

from job_suppressions import record_job_suppression


ACTIVE_UNTOUCHED_SQL = """
    status = 'matched'
    AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)
"""

ARCHIVED_UNTOUCHED_SQL = """
    status = 'archived'
    AND COALESCE(archived_from_status, 'matched') = 'matched'
    AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = jobs.id)
"""


def _ids_for(connection: sqlite3.Connection, where_sql: str) -> list[int]:
    return [row[0] for row in connection.execute(f"SELECT id FROM jobs WHERE {where_sql} ORDER BY id")]


def _preview_token(action: str, ids: Iterable[int]) -> str:
    payload = f"{action}:" + ",".join(str(job_id) for job_id in ids)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def cleanup_candidates(connection: sqlite3.Connection, action: str) -> list[int]:
    """Return the exact protected candidate set for a cleanup action."""
    if action == "archive":
        return _ids_for(connection, ACTIVE_UNTOUCHED_SQL)
    if action == "delete":
        return _ids_for(connection, f"({ACTIVE_UNTOUCHED_SQL}) OR ({ARCHIVED_UNTOUCHED_SQL})")
    if action == "restore":
        return _ids_for(connection, ARCHIVED_UNTOUCHED_SQL)
    raise ValueError(f"Unsupported cleanup action: {action}")


def cleanup_preview(connection: sqlite3.Connection) -> dict:
    """Describe cleanup effects without changing any records."""
    action_ids = {action: cleanup_candidates(connection, action) for action in ("archive", "delete", "restore")}
    total = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    protected = total - len(action_ids["delete"])
    sample_rows = connection.execute(
        f"""
        SELECT id, company, title, date_found
        FROM jobs
        WHERE {ACTIVE_UNTOUCHED_SQL}
        ORDER BY match_score DESC, id
        LIMIT 5
        """
    ).fetchall()
    return {
        "definition": "Matched jobs with no application record or generated materials.",
        "actions": {
            action: {"count": len(ids), "preview_token": _preview_token(action, ids)}
            for action, ids in action_ids.items()
        },
        "protected_count": protected,
        "sample": [dict(row) for row in sample_rows],
    }


def apply_cleanup(connection: sqlite3.Connection, action: str, preview_token: str, now: str) -> int:
    """Apply exactly the candidate set represented by a fresh preview token."""
    ids = cleanup_candidates(connection, action)
    if _preview_token(action, ids) != preview_token:
        raise ValueError("The job list changed after preview. Refresh the preview before continuing.")
    if not ids:
        return 0

    placeholders = ",".join("?" for _ in ids)
    if action == "archive":
        cursor = connection.execute(
            f"""
            UPDATE jobs
            SET archived_from_status = status, status = 'archived', archived_at = ?
            WHERE id IN ({placeholders})
            """,
            (now, *ids),
        )
    elif action == "restore":
        cursor = connection.execute(
            f"""
            UPDATE jobs
            SET status = COALESCE(archived_from_status, 'matched'),
                archived_from_status = NULL, archived_at = NULL
            WHERE id IN ({placeholders})
            """,
            ids,
        )
    elif action == "delete":
        rows = connection.execute(
            f"SELECT url, company, title FROM jobs WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        for row in rows:
            record_job_suppression(
                connection,
                url=row["url"],
                company=row["company"],
                title=row["title"],
                deleted_at=now,
                deletion_source="bulk_cleanup",
            )
        cursor = connection.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", ids)
    else:
        raise ValueError(f"Unsupported cleanup action: {action}")
    return cursor.rowcount
