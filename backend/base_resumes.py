"""Named base-resume storage and non-destructive version history."""

from datetime import datetime
import sqlite3


RESUME_MODES = {
    "it",
    "technical_executive",
    "general_professional",
    "federal",
    "healthcare",
    "education",
    "sales",
    "trades_operations",
    "academic_cv",
    "cover_letter",
}
MAX_BASE_RESUME_NAME = 120
MAX_BASE_RESUME_CHARS = 200_000


class BaseResumeNotFound(ValueError):
    """Raised when a requested resume or version does not exist."""


class LastBaseResumeError(ValueError):
    """Raised when deletion would remove the user's only base resume."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate(name: str, resume_mode: str, content: str) -> tuple[str, str, str]:
    normalized_name = " ".join((name or "").split())
    if not normalized_name:
        raise ValueError("Resume name is required.")
    if len(normalized_name) > MAX_BASE_RESUME_NAME:
        raise ValueError(f"Resume names must be {MAX_BASE_RESUME_NAME} characters or fewer.")
    if resume_mode not in RESUME_MODES:
        raise ValueError("Resume mode is not supported.")
    normalized_content = (content or "").strip()
    if len(normalized_content) > MAX_BASE_RESUME_CHARS:
        raise ValueError("Base resume content is too large.")
    return normalized_name, resume_mode, normalized_content


def _resume_row(connection: sqlite3.Connection, resume_id: int):
    row = connection.execute(
        "SELECT * FROM base_resumes WHERE id = ?",
        (resume_id,),
    ).fetchone()
    if not row:
        raise BaseResumeNotFound("Base resume was not found.")
    return row


def _version_number(connection: sqlite3.Connection, resume_id: int) -> int:
    return connection.execute(
        "SELECT COALESCE(MAX(version_number), 0) FROM base_resume_versions WHERE base_resume_id = ?",
        (resume_id,),
    ).fetchone()[0]


def _insert_version(
    connection: sqlite3.Connection,
    resume_id: int,
    name: str,
    resume_mode: str,
    content: str,
    created_at: str,
) -> int:
    version_number = _version_number(connection, resume_id) + 1
    connection.execute(
        """
        INSERT INTO base_resume_versions (
            base_resume_id, version_number, name, resume_mode, content, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (resume_id, version_number, name, resume_mode, content, created_at),
    )
    return version_number


def save_base_resume(
    connection: sqlite3.Connection,
    resume_id: int | None,
    name: str,
    resume_mode: str,
    content: str,
) -> dict:
    """Create or update a resume and snapshot only meaningful changes."""
    name, resume_mode, content = _validate(name, resume_mode, content)
    changed = True
    now = _now()
    if resume_id is None:
        cursor = connection.execute(
            """
            INSERT INTO base_resumes (name, resume_mode, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, resume_mode, content, now, now),
        )
        resume_id = cursor.lastrowid
        version_number = _insert_version(
            connection, resume_id, name, resume_mode, content, now
        )
    else:
        current = _resume_row(connection, resume_id)
        changed = any((current["name"] != name, current["resume_mode"] != resume_mode, current["content"] != content))
        if changed:
            connection.execute(
                """
                UPDATE base_resumes
                SET name = ?, resume_mode = ?, content = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, resume_mode, content, now, resume_id),
            )
            version_number = _insert_version(
                connection, resume_id, name, resume_mode, content, now
            )
        else:
            version_number = _version_number(connection, resume_id)

    result = dict(_resume_row(connection, resume_id))
    result["version_number"] = version_number
    result["version_created"] = changed
    return result


def activate_base_resume(connection: sqlite3.Connection, resume_id: int) -> dict:
    """Select a resume and synchronize the legacy profile fields used by tailoring."""
    row = _resume_row(connection, resume_id)
    connection.execute(
        """
        UPDATE profile
        SET active_base_resume_id = ?, base_resume_text = ?, resume_mode = ?, suggested_keywords = ''
        WHERE id = 1
        """,
        (resume_id, row["content"], row["resume_mode"]),
    )
    result = dict(row)
    result["version_number"] = _version_number(connection, resume_id)
    return result


def list_base_resumes(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT r.id, r.name, r.resume_mode, r.created_at, r.updated_at,
               COUNT(v.id) AS version_count,
               CASE WHEN p.active_base_resume_id = r.id THEN 1 ELSE 0 END AS active
        FROM base_resumes r
        CROSS JOIN profile p
        LEFT JOIN base_resume_versions v ON v.base_resume_id = r.id
        WHERE p.id = 1
        GROUP BY r.id
        ORDER BY active DESC, r.updated_at DESC, r.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_base_resume(connection: sqlite3.Connection, resume_id: int) -> dict:
    result = dict(_resume_row(connection, resume_id))
    result["version_number"] = _version_number(connection, resume_id)
    active_id = connection.execute(
        "SELECT active_base_resume_id FROM profile WHERE id = 1"
    ).fetchone()[0]
    result["active"] = active_id == resume_id
    return result


def list_versions(connection: sqlite3.Connection, resume_id: int) -> list[dict]:
    _resume_row(connection, resume_id)
    rows = connection.execute(
        """
        SELECT id, base_resume_id, version_number, name, resume_mode, created_at,
               LENGTH(content) AS character_count
        FROM base_resume_versions
        WHERE base_resume_id = ?
        ORDER BY version_number DESC
        """,
        (resume_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_version(connection: sqlite3.Connection, resume_id: int, version_number: int) -> dict:
    _resume_row(connection, resume_id)
    row = connection.execute(
        """
        SELECT * FROM base_resume_versions
        WHERE base_resume_id = ? AND version_number = ?
        """,
        (resume_id, version_number),
    ).fetchone()
    if not row:
        raise BaseResumeNotFound("Base resume version was not found.")
    return dict(row)


def restore_version(connection: sqlite3.Connection, resume_id: int, version_number: int) -> dict:
    """Restore old content by creating a new version, preserving the full audit trail."""
    version = get_version(connection, resume_id, version_number)
    now = _now()
    connection.execute(
        """
        UPDATE base_resumes
        SET name = ?, resume_mode = ?, content = ?, updated_at = ?
        WHERE id = ?
        """,
        (version["name"], version["resume_mode"], version["content"], now, resume_id),
    )
    new_version = _insert_version(
        connection,
        resume_id,
        version["name"],
        version["resume_mode"],
        version["content"],
        now,
    )
    restored = dict(_resume_row(connection, resume_id))
    restored["version_number"] = new_version
    restored["version_created"] = True
    active_id = connection.execute(
        "SELECT active_base_resume_id FROM profile WHERE id = 1"
    ).fetchone()[0]
    if active_id == resume_id:
        activate_base_resume(connection, resume_id)
    restored["restored_from_version"] = version_number
    return restored


def delete_base_resume(connection: sqlite3.Connection, resume_id: int) -> dict | None:
    _resume_row(connection, resume_id)
    count = connection.execute("SELECT COUNT(*) FROM base_resumes").fetchone()[0]
    if count <= 1:
        raise LastBaseResumeError("Keep at least one base resume. Create another resume before deleting this one.")

    active_id = connection.execute(
        "SELECT active_base_resume_id FROM profile WHERE id = 1"
    ).fetchone()[0]
    connection.execute("DELETE FROM base_resume_versions WHERE base_resume_id = ?", (resume_id,))
    connection.execute("DELETE FROM base_resumes WHERE id = ?", (resume_id,))
    if active_id != resume_id:
        return None

    replacement = connection.execute(
        "SELECT id FROM base_resumes ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return activate_base_resume(connection, replacement["id"])
