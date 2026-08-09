"""Optional, privacy-conscious Sentry error and performance monitoring."""

import os
import re
from typing import Any, Optional

import sentry_sdk


SAFE_SYMBOL = re.compile(r"^[A-Za-z_<>][A-Za-z0-9_.<>:-]{0,127}$")
SAFE_TRACE_ID = re.compile(r"^[a-fA-F0-9]{32}$")
SAFE_SPAN_ID = re.compile(r"^[a-fA-F0-9]{16}$")


def _sample_rate(name: str, default: float) -> float:
    """Read a Sentry sample rate, falling back for invalid or unsafe values."""
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if 0.0 <= value <= 1.0 else default


def _base_event(event: dict[str, Any]) -> dict[str, Any]:
    """Retain only non-user event metadata required for Sentry processing."""
    return {
        key: event[key]
        for key in (
            "event_id", "timestamp", "start_timestamp", "type", "level",
            "platform", "release", "environment",
        )
        if key in event
    }


def _safe_symbol(value: Any) -> Optional[str]:
    """Accept only bounded code/SDK identifiers, never arbitrary user text."""
    return value if isinstance(value, str) and SAFE_SYMBOL.fullmatch(value) else None


def _safe_stacktrace(stacktrace: Any) -> Optional[dict[str, list[dict[str, Any]]]]:
    """Reduce stack frames to application symbols and line numbers."""
    if not isinstance(stacktrace, dict) or not isinstance(stacktrace.get("frames"), list):
        return None
    frames = []
    for frame in stacktrace["frames"]:
        if not isinstance(frame, dict):
            continue
        safe_frame = {}
        for key in ("module", "function"):
            safe_value = _safe_symbol(frame.get(key))
            if safe_value:
                safe_frame[key] = safe_value
        if isinstance(frame.get("lineno"), int) and 0 < frame["lineno"] <= 10_000_000:
            safe_frame["lineno"] = frame["lineno"]
        if isinstance(frame.get("in_app"), bool):
            safe_frame["in_app"] = frame["in_app"]
        if safe_frame:
            frames.append(safe_frame)
    return {"frames": frames} if frames else None


def sanitize_error_event(event: dict[str, Any], _hint: Any = None) -> Optional[dict[str, Any]]:
    """Allowlist an exception event without messages, request data, or breadcrumbs."""
    exception = event.get("exception")
    values = exception.get("values") if isinstance(exception, dict) else None
    if not isinstance(values, list):
        return None

    safe_values = []
    for value in values:
        if not isinstance(value, dict):
            continue
        safe_value = {}
        for key in ("type", "module"):
            safe_symbol = _safe_symbol(value.get(key))
            if safe_symbol:
                safe_value[key] = safe_symbol
        stacktrace = _safe_stacktrace(value.get("stacktrace"))
        if stacktrace:
            safe_value["stacktrace"] = stacktrace
        if safe_value:
            safe_values.append(safe_value)
    if not safe_values:
        return None

    safe_event = _base_event(event)
    safe_event["exception"] = {"values": safe_values}
    return safe_event


def sanitize_transaction_event(event: dict[str, Any], _hint: Any = None) -> dict[str, Any]:
    """Keep timing and trace identity while discarding routes, URLs, spans, and request data."""
    safe_event = _base_event(event)
    safe_event["type"] = "transaction"
    safe_event["transaction"] = "http.server"

    contexts = event.get("contexts")
    trace = contexts.get("trace") if isinstance(contexts, dict) else None
    if isinstance(trace, dict):
        safe_trace = {}
        for key, pattern in (
            ("trace_id", SAFE_TRACE_ID),
            ("span_id", SAFE_SPAN_ID),
            ("parent_span_id", SAFE_SPAN_ID),
        ):
            value = trace.get(key)
            if isinstance(value, str) and pattern.fullmatch(value):
                safe_trace[key] = value
        for key in ("op", "status", "origin"):
            safe_value = _safe_symbol(trace.get(key))
            if safe_value:
                safe_trace[key] = safe_value
        if safe_trace:
            safe_event["contexts"] = {"trace": safe_trace}
    return safe_event


def initialize_sentry(release: str) -> bool:
    """Enable Sentry when a DSN is supplied without collecting default PII."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False

    options = {
        "dsn": dsn,
        "environment": os.environ.get("SENTRY_ENVIRONMENT", "development").strip() or "development",
        "release": release,
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "before_send": sanitize_error_event,
        "before_send_transaction": sanitize_transaction_event,
        "traces_sample_rate": _sample_rate("SENTRY_TRACES_SAMPLE_RATE", 0.0),
    }

    try:
        sentry_sdk.init(**options)
    except Exception:
        # Monitoring must never prevent this local-first application from starting.
        return False
    return True


def sentry_debug_enabled() -> bool:
    """Return whether the temporary local verification endpoint is enabled."""
    return bool(os.environ.get("SENTRY_DSN", "").strip()) and (
        os.environ.get("SENTRY_DEBUG_ROUTE", "").strip().lower() in {"1", "true", "yes"}
    )
