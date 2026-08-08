"""Deterministic, local-only job-posting facets for advanced result filters."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


MAX_FILTER_TEXT_CHARS = 50_000
CLEARANCE_RANKS = {
    "none": 0,
    "public_trust": 1,
    "secret": 2,
    "top_secret": 3,
    "ts_sci": 4,
}


def _value(job: Mapping[str, Any], name: str) -> str:
    value = job.get(name, "")
    return str(value or "").strip()


def _posting_text(job: Mapping[str, Any]) -> str:
    content = "\n".join(
        _value(job, name)
        for name in ("title", "description", "location")
    )
    return " ".join(content[:MAX_FILTER_TEXT_CHARS].lower().split())


def _money_values(compensation: str) -> list[float]:
    values: list[float] = []
    for raw, thousands in re.findall(r"(?<![A-Za-z0-9])\$?\s*(\d[\d,]*(?:\.\d+)?)\s*([kK]?)", compensation):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if thousands:
            value *= 1_000
        if value > 0:
            values.append(value)
    return values[:4]


def _compensation_facets(job: Mapping[str, Any]) -> dict:
    compensation = _value(job, "compensation")
    lowered = compensation.lower()
    values = _money_values(compensation)
    if not values:
        return {
            "compensation_period": "unknown",
            "compensation_min": None,
            "compensation_max": None,
        }
    minimum = min(values)
    maximum = max(values)
    if re.search(r"(?:/|per\s+)(?:hour|hr)\b|\bhourly\b", lowered):
        period = "hourly"
    elif re.search(r"(?:/|per\s+)(?:year|yr)\b|\bannual(?:ly)?\b|\bsalary\b", lowered):
        period = "annual"
    elif maximum >= 1_000:
        period = "annual"
    elif _value(job, "employment_type").lower() == "contract":
        period = "hourly"
    else:
        period = "unknown"
    return {
        "compensation_period": period,
        "compensation_min": round(minimum, 2),
        "compensation_max": round(maximum, 2),
    }


def _shift_tags(text: str) -> list[str]:
    patterns = (
        (
            "night",
            r"\b(?:night|overnight|third|3rd)\s+shifts?\b|"
            r"\b(?:night|overnight)\s+(?:work|schedule|coverage)\b|\bwork\s+(?:at\s+)?nights?\b",
        ),
        (
            "evening",
            r"\b(?:evening|second|2nd|swing)\s+shifts?\b|"
            r"\bevening\s+(?:work|schedule|coverage)\b|\bwork\s+(?:in\s+the\s+)?evenings?\b",
        ),
        ("rotating", r"\b(?:rotating|variable)\s+shifts?\b"),
        ("weekend", r"\bweekend\s+(?:shift|availability|coverage|work)\b|\bwork\s+weekends?\b"),
        ("on_call", r"\bon[- ]call\b|\bcall\s+rotation\b"),
        ("day", r"\b(?:day|first|1st)\s+shift\b"),
    )
    return [name for name, pattern in patterns if re.search(pattern, text)]


def _travel_facets(text: str) -> tuple[bool | None, int | None]:
    if re.search(r"\b(?:no|zero)\s+(?:business\s+)?travel\b|\b0\s*%\s*travel\b", text):
        return False, 0
    percentages: list[int] = []
    for pattern in (
        r"\btravel\b[^.\n]{0,45}?\b(?:up to\s+)?(\d{1,3})\s*%",
        r"\b(?:up to\s+)?(\d{1,3})\s*%[^.\n]{0,20}?\btravel\b",
    ):
        percentages.extend(int(value) for value in re.findall(pattern, text))
    percentages = [min(value, 100) for value in percentages]
    if percentages:
        return max(percentages) > 0, max(percentages)
    if re.search(r"\btravel\s+(?:is\s+)?required\b|\bwilling(?:ness)?\s+to\s+travel\b|\brequires?\s+(?:frequent\s+)?travel\b", text):
        return True, None
    return None, None


def _sponsorship(text: str) -> str:
    if re.search(
        r"\b(?:no|without)\s+(?:visa\s+|employment\s+)?sponsorship\b|"
        r"\b(?:unable|not\s+able)\s+to\s+sponsor\b|\b(?:will\s+not|does\s+not|do\s+not)\s+sponsor\b|"
        r"\b(?:do|does|will)\s+not\s+(?:offer|provide)\b[^.]{0,25}\bsponsorship\b|"
        r"\bsponsorship\s+(?:is\s+)?not\s+available\b|\bwithout\s+(?:current\s+or\s+future\s+)?sponsorship\b",
        text,
    ):
        return "unavailable"
    if re.search(
        r"\b(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:available|provided)\b|"
        r"\b(?:will|can)\s+sponsor\b",
        text,
    ):
        return "available"
    if re.search(
        r"\b(?:must\s+be\s+)?authorized\s+to\s+work\b|\bwork\s+authorization\s+(?:is\s+)?required\b|"
        r"\bu\.?s\.?\s+citizens?\s+only\b",
        text,
    ):
        return "authorization_required"
    return "unknown"


def _clearance(text: str) -> tuple[str | None, int | None]:
    if re.search(
        r"\bno\s+(?:(?:public\s+trust|secret|top\s+secret|ts\s*/\s*sci)\s+)?"
        r"(?:security\s+)?clearance\s+(?:is\s+)?required\b",
        text,
    ):
        return "none", CLEARANCE_RANKS["none"]
    if re.search(r"\b(?:ts\s*/\s*sci|top\s+secret\s*/?\s*sci|sci\s+clearance)\b", text):
        level = "ts_sci"
    elif re.search(r"\btop\s+secret\b|\bts\s+clearance\b", text):
        level = "top_secret"
    elif re.search(r"\bsecret\s+(?:security\s+)?clearance\b", text):
        level = "secret"
    elif re.search(r"\bpublic\s+trust\b", text):
        level = "public_trust"
    else:
        return None, None
    return level, CLEARANCE_RANKS[level]


def _license_tags(text: str) -> list[str]:
    patterns = (
        ("registered_nurse", r"\b(?:active\s+)?r\.?n\.?\s+licen[sc]e\b|\blicensed\s+registered\s+nurse\b"),
        ("commercial_driver", r"\bcdl(?:[- ]?[abc])?\b|\bcommercial\s+driver'?s?\s+licen[sc]e\b"),
        ("driver", r"\bvalid\s+(?:state\s+)?driver'?s?\s+licen[sc]e\b"),
        ("cpa", r"\b(?:active\s+)?cpa\b|\bcertified\s+public\s+accountant\b"),
        ("professional_engineer", r"\bprofessional\s+engineer\s+licen[sc]e\b|\bp\.?e\.?\s+licen[sc]e\b"),
        ("teaching", r"\b(?:state\s+)?teaching\s+(?:licen[sc]e|certification)\b"),
        ("legal", r"\b(?:state\s+)?bar\s+(?:admission|membership)\b|\blicen[sc]ed\s+attorney\b"),
        ("medical", r"\b(?:medical|physician|pharmacist)\s+licen[sc]e\b"),
    )
    tags = [name for name, pattern in patterns if re.search(pattern, text)]
    generic_required = re.search(
        r"\blicen[sc]e\s+(?:is\s+)?required\b|\bmust\s+(?:hold|have|possess)\b[^.\n]{0,40}\blicen[sc]e\b",
        text,
    )
    if generic_required and not tags:
        tags.append("other")
    return tags


def _condition_tags(text: str, shift_tags: list[str], travel_required: bool | None) -> list[str]:
    patterns = (
        ("lifting", r"\b(?:lift|lifting)\s+(?:up\s+to\s+)?\d{1,3}\s*(?:lb|lbs|pounds?)\b|\bheavy\s+lifting\b"),
        ("standing", r"\b(?:prolonged|extended)\s+(?:standing|walking)\b|\bstand\s+for\s+extended\s+periods\b"),
        ("outdoors", r"\b(?:work|working)\s+(?:primarily\s+)?outdoors?\b|\boutdoor\s+(?:environment|conditions|work)\b"),
        ("driving", r"\b(?:frequent|regular)\s+driving\b|\boperate\s+(?:a\s+)?(?:company\s+)?vehicle\b"),
        ("hazardous", r"\b(?:hazardous\s+(?:materials|environment)|confined\s+spaces?|personal\s+protective\s+equipment|ppe\s+required)\b"),
    )
    tags = [name for name, pattern in patterns if re.search(pattern, text)]
    if any(tag in shift_tags for tag in ("night", "evening", "rotating", "weekend")):
        tags.append("shift_work")
    if "on_call" in shift_tags:
        tags.append("on_call")
    if travel_required:
        tags.append("travel")
    return tags


def derive_job_filter_facets(job: Mapping[str, Any]) -> dict:
    """Return bounded, non-AI filter signals without mutating or persisting a job."""
    text = _posting_text(job)
    compensation = _compensation_facets(job)
    shifts = _shift_tags(text)
    travel_required, travel_percent = _travel_facets(text)
    clearance_level, clearance_rank = _clearance(text)
    licenses = _license_tags(text)
    arrangement = _value(job, "work_arrangement").lower()
    if arrangement not in {"remote", "hybrid", "on_site"}:
        arrangement = (
            "remote" if re.search(r"\bfully\s+remote\b|\bremote\s+(?:role|position|work)\b", text)
            else "hybrid" if re.search(r"\bhybrid\s+(?:role|position|schedule|work)\b", text)
            else "on_site" if re.search(r"\b(?:on[- ]site|in[- ]office)\b", text)
            else "unknown"
        )
    employment = _value(job, "employment_type").lower()
    if employment not in {"full_time", "part_time", "contract", "temporary", "internship"}:
        employment = "unknown"
    conditions = _condition_tags(text, shifts, travel_required)
    return {
        **compensation,
        "employment_type": employment,
        "commute_requirement": arrangement,
        "shift_tags": shifts,
        "travel_required": travel_required,
        "travel_percent": travel_percent,
        "sponsorship": _sponsorship(text),
        "clearance_level": clearance_level,
        "clearance_rank": clearance_rank,
        "license_required": bool(licenses),
        "license_tags": licenses,
        "physical_conditions": any(tag in conditions for tag in ("lifting", "standing", "outdoors", "driving", "hazardous")),
        "condition_tags": conditions,
    }
