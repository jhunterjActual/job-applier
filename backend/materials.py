"""Safe persistence and download helpers for generated application materials."""

import re
from pathlib import Path

import config


def cover_letter_output_path(job_id: int, output_dir: Path | None = None) -> Path:
    """Return a deterministic cover-letter path inside the configured output directory."""
    if not isinstance(job_id, int) or job_id < 1:
        raise ValueError("A positive job ID is required.")
    root = Path(output_dir or config.OUTPUT_DIR)
    return root / f"cover_letter_{job_id}.txt"


def persist_cover_letter(job_id: int, text: str, output_dir: Path | None = None) -> str:
    """Persist a generated cover letter as UTF-8 text and return its absolute path."""
    content = (text or "").strip()
    if not content:
        raise ValueError("Cover letter text cannot be empty.")
    path = cover_letter_output_path(job_id, output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    return str(path.resolve())


def resolve_output_file(raw_path: str, expected_suffix: str, output_dir: Path | None = None) -> Path:
    """Resolve a generated file while preventing traversal outside the output directory."""
    if not raw_path:
        raise ValueError("Generated material path is missing.")
    root = Path(output_dir or config.OUTPUT_DIR).resolve()
    candidate = Path(raw_path).resolve()
    if candidate.parent != root or candidate.suffix.lower() != expected_suffix.lower():
        raise ValueError("Generated material path is invalid.")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def material_download_name(company: str, position: str, artifact: str, suffix: str) -> str:
    """Create a recruiter-friendly filename without path or control characters."""
    source = f"{company or 'company'}-{position or 'position'}-{artifact}"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", source).strip("-").lower()
    slug = slug[:100].rstrip("-") or artifact
    return f"{slug}{suffix}"
