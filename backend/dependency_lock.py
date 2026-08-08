"""Track whether the local environment matches the reviewed dependency lock."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import re
import sys
from pathlib import Path
from typing import Iterable


FINGERPRINT_FORMAT = b"jobapplier-dependency-lock-v1\0"
BOOTSTRAP_PACKAGES = {"pip", "setuptools", "wheel"}
LOCK_ENTRY = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)\s*\\?$")


def normalize_package_name(name: str) -> str:
    """Normalize a distribution name using Python package-index rules."""
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_requirements(lock_path: Path) -> dict[str, str]:
    """Read the exact name/version manifest from a compiled requirements lock."""
    expected: dict[str, str] = {}
    for line in Path(lock_path).read_text(encoding="utf-8").splitlines():
        match = LOCK_ENTRY.match(line)
        if not match:
            continue
        name = normalize_package_name(match.group(1))
        if name in expected:
            raise ValueError(f"Duplicate locked package: {name}")
        expected[name] = match.group(2)
    if not expected:
        raise ValueError("The dependency lock contains no exact requirements.")
    return expected


def installed_requirements(distributions: Iterable | None = None) -> dict[str, str]:
    """Return installed application distributions, excluding venv bootstrap tools."""
    installed: dict[str, str] = {}
    source = distributions if distributions is not None else importlib.metadata.distributions()
    for distribution in source:
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            continue
        name = normalize_package_name(raw_name)
        if name not in BOOTSTRAP_PACKAGES:
            installed[name] = distribution.version
    return installed


def environment_matches_lock(lock_path: Path, distributions: Iterable | None = None) -> bool:
    """Return whether the venv contains exactly the reviewed locked packages."""
    try:
        return installed_requirements(distributions) == locked_requirements(lock_path)
    except (OSError, UnicodeError, ValueError):
        return False


def dependency_fingerprint(paths: list[Path]) -> str:
    """Return a stable digest for the named dependency policy and lock files."""
    digest = hashlib.sha256(FINGERPRINT_FORMAT)
    for raw_path in paths:
        path = Path(raw_path)
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def stamp_is_current(stamp_path: Path, dependency_paths: list[Path]) -> bool:
    """Return whether a readable stamp matches the current dependency files."""
    try:
        recorded = Path(stamp_path).read_text(encoding="ascii").strip().lower()
    except (FileNotFoundError, OSError, UnicodeError):
        return False
    return recorded == dependency_fingerprint(dependency_paths)


def write_stamp(stamp_path: Path, dependency_paths: list[Path]) -> None:
    """Atomically record a successful dependency reconciliation."""
    destination = Path(stamp_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(dependency_fingerprint(dependency_paths) + "\n", encoding="ascii")
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "write"))
    parser.add_argument("--stamp", required=True, type=Path)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("dependency_files", nargs="+", type=Path)
    args = parser.parse_args()

    if args.action == "check":
        current = stamp_is_current(args.stamp, args.dependency_files)
        return 0 if current and environment_matches_lock(args.lock) else 1
    if not environment_matches_lock(args.lock):
        print("The installed environment does not exactly match the reviewed dependency lock.", file=sys.stderr)
        return 2
    write_stamp(args.stamp, args.dependency_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
