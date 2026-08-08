"""Privacy-safe persistence for job-source health notices."""

import json
import sqlite3


ALLOWED_PROVIDERS = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters"})
ALLOWED_CODES = frozenset({
    "url_format_drift",
    "content_format_drift",
    "access_challenge",
    "provider_error",
    "stale_postings",
    "partial_results",
})
INFORMATIONAL_CODES = frozenset({"stale_postings", "partial_results"})
MAX_COUNTER_VALUE = 1_000_000
MAX_ALERTS_PER_RUN = 20
MAX_HISTORY_RECORDS = 500
DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 500

DIRECT_COUNTERS = (
    "raw_candidates",
    "valid_discovered",
    "new_candidates",
    "accepted",
    "rejected",
    "skipped_active",
    "skipped_archived",
    "skipped_suppressed",
    "api_fallbacks",
)
REJECTION_COUNTERS = (
    "stale",
    "format_drift",
    "access_challenge",
    "embedded_content",
    "provider_error",
)
PARTIAL_COUNTERS = ("oversized_responses", "candidate_limit_hits", "timeouts")
STORED_COUNTERS = DIRECT_COUNTERS + REJECTION_COUNTERS + PARTIAL_COUNTERS + (
    "search_errors",
    "candidate_budget_exhausted",
)


def _bounded_counter(value: object) -> int:
    """Convert an aggregate to a bounded, non-negative integer."""
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_COUNTER_VALUE, numeric))


def safe_provider_counters(health: dict | None) -> dict[str, int]:
    """Select only aggregate counters that cannot reveal search or job data."""
    health = health if isinstance(health, dict) else {}
    reasons = health.get("rejection_reasons")
    reasons = reasons if isinstance(reasons, dict) else {}
    partial = health.get("partial_results")
    partial = partial if isinstance(partial, dict) else {}

    counters = {name: _bounded_counter(health.get(name)) for name in DIRECT_COUNTERS}
    counters.update({name: _bounded_counter(reasons.get(name)) for name in REJECTION_COUNTERS})
    counters.update({name: _bounded_counter(partial.get(name)) for name in PARTIAL_COUNTERS})
    errors = health.get("errors")
    counters["search_errors"] = _bounded_counter(len(errors) if isinstance(errors, list) else 0)
    counters["candidate_budget_exhausted"] = int(bool(health.get("candidate_budget_exhausted")))
    return counters


def persist_source_diagnostics(
    connection: sqlite3.Connection,
    search_result: dict | None,
    recorded_at: str,
) -> int:
    """Persist allowlisted notices and aggregates, then enforce local retention."""
    if not isinstance(search_result, dict):
        return 0
    alerts = search_result.get("provider_alerts")
    health_by_provider = search_result.get("provider_health")
    if not isinstance(alerts, list) or not isinstance(health_by_provider, dict):
        return 0

    inserted = 0
    for alert in alerts[:MAX_ALERTS_PER_RUN]:
        if not isinstance(alert, dict):
            continue
        provider = str(alert.get("provider") or "").lower()
        code = str(alert.get("code") or "").lower()
        if provider not in ALLOWED_PROVIDERS or code not in ALLOWED_CODES:
            continue
        counters = safe_provider_counters(health_by_provider.get(provider))
        connection.execute(
            """
            INSERT INTO source_diagnostics (recorded_at, provider, diagnostic_code, counters_json)
            VALUES (?, ?, ?, ?)
            """,
            (recorded_at, provider, code, json.dumps(counters, sort_keys=True, separators=(",", ":"))),
        )
        inserted += 1

    if inserted:
        connection.execute(
            """
            DELETE FROM source_diagnostics
            WHERE id NOT IN (
                SELECT id FROM source_diagnostics
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
            )
            """,
            (MAX_HISTORY_RECORDS,),
        )
    return inserted


def list_source_diagnostics(connection: sqlite3.Connection, limit: int = DEFAULT_HISTORY_LIMIT) -> dict:
    """Return recent minimized diagnostics without raw errors or source messages."""
    safe_limit = max(1, min(MAX_HISTORY_LIMIT, int(limit)))
    count = connection.execute("SELECT COUNT(*) FROM source_diagnostics").fetchone()[0]
    rows = connection.execute(
        """
        SELECT id, recorded_at, provider, diagnostic_code, counters_json
        FROM source_diagnostics
        ORDER BY recorded_at DESC, id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    items = []
    for row in rows:
        try:
            stored = json.loads(row["counters_json"])
        except (json.JSONDecodeError, TypeError):
            stored = {}
        stored = stored if isinstance(stored, dict) else {}
        counters = {name: _bounded_counter(stored.get(name)) for name in STORED_COUNTERS}
        counters["candidate_budget_exhausted"] = int(bool(counters["candidate_budget_exhausted"]))
        items.append({
            "id": row["id"],
            "recorded_at": row["recorded_at"],
            "provider": row["provider"],
            "code": row["diagnostic_code"],
            "level": "note" if row["diagnostic_code"] in INFORMATIONAL_CODES else "attention",
            "counters": counters,
        })
    return {"count": count, "items": items}
