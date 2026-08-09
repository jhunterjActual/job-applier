"""Password-encrypted, portable full-workspace backup and guarded restore."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


BACKUP_FORMAT = "career-trellis-encrypted-backup"
BACKUP_SCHEMA_VERSION = 1
MAGIC = b"CTBACKUP\x00"
PORTABLE_OUTPUT_PREFIX = "__CAREERTRELLIS_OUTPUT__/"
MINIMUM_PASSWORD_CHARACTERS = 12
MAXIMUM_PASSWORD_BYTES = 1024
MAXIMUM_HEADER_BYTES = 16 * 1024
MAXIMUM_BACKUP_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_MATERIAL_FILES = 5_000
MAXIMUM_MANIFEST_BYTES = 2 * 1024 * 1024
STREAM_CHUNK_BYTES = 1024 * 1024
GCM_TAG_BYTES = 16
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1

Checkpoint = Callable[[], None]


class BackupRestoreError(Exception):
    """Safe user-facing backup or restore failure."""


class BackupPasswordError(BackupRestoreError):
    """The supplied password is missing or too weak for a new backup."""


class BackupAuthenticationError(BackupRestoreError):
    """The password is wrong or encrypted backup authentication failed."""


class BackupCompatibilityError(BackupRestoreError):
    """The backup cannot safely be restored by this application build."""


@dataclass(frozen=True)
class BackupSummary:
    created_at: str
    application_build: str
    material_file_count: int
    total_plaintext_bytes: int
    warning_count: int


@dataclass(frozen=True)
class RestoreSummary:
    backup_created_at: str
    backup_application_build: str
    material_file_count: int
    recovery_directory: str


def _checkpoint(callback: Checkpoint | None) -> None:
    if callback:
        callback()


def _password_bytes(password: str, *, creating: bool) -> bytes:
    if not isinstance(password, str):
        raise BackupPasswordError("Enter a backup password.")
    encoded = password.encode("utf-8")
    if not encoded:
        raise BackupPasswordError("Enter a backup password.")
    if len(encoded) > MAXIMUM_PASSWORD_BYTES:
        raise BackupPasswordError("The backup password is too long.")
    if creating and len(password) < MINIMUM_PASSWORD_CHARACTERS:
        raise BackupPasswordError(
            f"Use at least {MINIMUM_PASSWORD_CHARACTERS} characters for the backup password."
        )
    return encoded


def _derive_key(password: bytes, salt: bytes) -> bytes:
    return Scrypt(
        salt=salt,
        length=32,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).derive(password)


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_material_files(output_dir: Path, checkpoint: Checkpoint | None) -> list[tuple[Path, str]]:
    root = output_dir.resolve()
    if not output_dir.exists():
        return []
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise BackupRestoreError("The generated-materials folder is not a safe local directory.")
    files: list[tuple[Path, str]] = []
    total_bytes = 0
    for candidate in sorted(output_dir.rglob("*")):
        _checkpoint(checkpoint)
        if candidate.is_symlink():
            raise BackupRestoreError("Generated-material symlinks cannot be included in a backup.")
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise BackupRestoreError("A generated material resolves outside its local folder.") from exc
        if len(files) >= MAXIMUM_MATERIAL_FILES:
            raise BackupRestoreError("The generated-materials folder contains too many files to back up safely.")
        size = resolved.stat().st_size
        total_bytes += size
        if total_bytes > MAXIMUM_EXPANDED_BYTES:
            raise BackupRestoreError("Generated materials exceed the supported full-backup size.")
        files.append((resolved, PurePosixPath("materials", *relative.parts).as_posix()))
    return files


def _snapshot_database(source_path: Path, snapshot_path: Path, output_dir: Path, checkpoint: Checkpoint | None) -> int:
    if not source_path.is_file():
        raise BackupRestoreError("The local CareerTrellis database is missing.")
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(snapshot_path))

    def progress(_status: int, _remaining: int, _total: int) -> None:
        _checkpoint(checkpoint)

    try:
        source.backup(target, pages=256, progress=progress)
    finally:
        target.close()
        source.close()

    warning_count = 0
    root = output_dir.resolve()
    connection = sqlite3.connect(str(snapshot_path))
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'applications'"
        ).fetchone()
        if table:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
            for column in ("tailored_resume_path", "cover_letter_path"):
                if column not in columns:
                    continue
                rows = connection.execute(
                    f"SELECT id, {column} FROM applications WHERE {column} IS NOT NULL AND {column} != ''"
                ).fetchall()
                for application_id, raw_path in rows:
                    _checkpoint(checkpoint)
                    try:
                        candidate = Path(raw_path).resolve()
                        relative = candidate.relative_to(root)
                    except (OSError, ValueError, TypeError):
                        relative = None
                    if relative is None or not candidate.is_file():
                        connection.execute(
                            f"UPDATE applications SET {column} = NULL WHERE id = ?", (application_id,)
                        )
                        warning_count += 1
                    else:
                        portable = PORTABLE_OUTPUT_PREFIX + PurePosixPath(*relative.parts).as_posix()
                        connection.execute(
                            f"UPDATE applications SET {column} = ? WHERE id = ?", (portable, application_id)
                        )
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupRestoreError("The local database did not pass an integrity check.")
        connection.commit()
    finally:
        connection.close()
    return warning_count


def _write_archive_member(
    archive: zipfile.ZipFile,
    source_path: Path,
    archive_path: str,
    checkpoint: Checkpoint | None,
) -> dict:
    digest = hashlib.sha256()
    size = 0
    with source_path.open("rb") as source, archive.open(archive_path, "w") as destination:
        while chunk := source.read(STREAM_CHUNK_BYTES):
            _checkpoint(checkpoint)
            size += len(chunk)
            if size > MAXIMUM_EXPANDED_BYTES:
                raise BackupRestoreError("A backup member exceeds the supported size.")
            digest.update(chunk)
            destination.write(chunk)
    return {"path": archive_path, "size": size, "sha256": digest.hexdigest()}


def _build_plaintext_archive(
    archive_path: Path,
    database_path: Path,
    output_dir: Path,
    application_build: str,
    created_at: str,
    checkpoint: Checkpoint | None,
) -> BackupSummary:
    material_files = _safe_material_files(output_dir, checkpoint)
    with tempfile.TemporaryDirectory(prefix="career-trellis-db-") as temp_directory:
        snapshot_path = Path(temp_directory) / "database.sqlite3"
        warning_count = _snapshot_database(database_path, snapshot_path, output_dir, checkpoint)
        records: list[dict] = []
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            records.append(_write_archive_member(archive, snapshot_path, "database.sqlite3", checkpoint))
            for source_path, member_path in material_files:
                records.append(_write_archive_member(archive, source_path, member_path, checkpoint))
            manifest = {
                "format": BACKUP_FORMAT,
                "schema_version": BACKUP_SCHEMA_VERSION,
                "database_schema_version": BACKUP_SCHEMA_VERSION,
                "minimum_restore_build": application_build,
                "application_build": application_build,
                "created_at": created_at,
                "database_path": "database.sqlite3",
                "material_file_count": len(material_files),
                "warning_count": warning_count,
                "files": records,
            }
            archive.writestr("manifest.json", _json_bytes(manifest))
    total_plaintext_bytes = sum(record["size"] for record in records)
    if total_plaintext_bytes > MAXIMUM_EXPANDED_BYTES:
        archive_path.unlink(missing_ok=True)
        raise BackupRestoreError("The workspace exceeds the supported full-backup size.")
    return BackupSummary(created_at, application_build, len(material_files), total_plaintext_bytes, warning_count)


def _encrypt_archive(
    archive_path: Path,
    destination_path: Path,
    password: str,
    application_build: str,
    created_at: str,
    checkpoint: Checkpoint | None,
) -> None:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = {
        "format": BACKUP_FORMAT,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "application_build": application_build,
        "created_at": created_at,
    }
    header_bytes = _json_bytes(header)
    prefix = MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
    key = _derive_key(_password_bytes(password, creating=True), salt)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(prefix)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive_path.open("rb") as source, destination_path.open("wb") as destination:
            destination.write(prefix)
            while chunk := source.read(STREAM_CHUNK_BYTES):
                _checkpoint(checkpoint)
                destination.write(encryptor.update(chunk))
                if destination.tell() > MAXIMUM_BACKUP_BYTES:
                    raise BackupRestoreError("The encrypted backup exceeds the supported size.")
            destination.write(encryptor.finalize())
            destination.write(encryptor.tag)
        _checkpoint(checkpoint)
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise


def create_encrypted_backup(
    destination_path: str | Path,
    password: str,
    *,
    database_path: str | Path,
    output_dir: str | Path,
    application_build: str,
    created_at: str | None = None,
    checkpoint: Checkpoint | None = None,
) -> BackupSummary:
    """Create an authenticated encrypted database-and-materials backup."""
    _password_bytes(password, creating=True)
    created = created_at or datetime.now().astimezone().isoformat(timespec="seconds")
    destination = Path(destination_path)
    with tempfile.TemporaryDirectory(prefix="career-trellis-backup-") as temp_directory:
        archive_path = Path(temp_directory) / "workspace.zip"
        summary = _build_plaintext_archive(
            archive_path,
            Path(database_path),
            Path(output_dir),
            application_build,
            created,
            checkpoint,
        )
        _encrypt_archive(archive_path, destination, password, application_build, created, checkpoint)
    return summary


def _read_header(source, password: str) -> tuple[dict, bytes, int]:
    prefix = source.read(len(MAGIC))
    if prefix != MAGIC:
        raise BackupAuthenticationError("This is not a valid CareerTrellis encrypted backup.")
    length_bytes = source.read(4)
    if len(length_bytes) != 4:
        raise BackupAuthenticationError("The encrypted backup is incomplete.")
    header_length = struct.unpack(">I", length_bytes)[0]
    if header_length < 2 or header_length > MAXIMUM_HEADER_BYTES:
        raise BackupAuthenticationError("The encrypted backup header is invalid.")
    header_bytes = source.read(header_length)
    if len(header_bytes) != header_length:
        raise BackupAuthenticationError("The encrypted backup is incomplete.")
    try:
        header = json.loads(header_bytes)
        if not isinstance(header, dict):
            raise ValueError
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
        if len(salt) != 16 or len(nonce) != 12:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupAuthenticationError("The encrypted backup header is invalid.") from exc
    aad = prefix + length_bytes + header_bytes
    return header, _derive_key(_password_bytes(password, creating=False), salt), len(aad)


def _decrypt_backup(
    backup_path: Path,
    archive_path: Path,
    password: str,
    checkpoint: Checkpoint | None,
) -> dict:
    size = backup_path.stat().st_size
    if size > MAXIMUM_BACKUP_BYTES:
        raise BackupRestoreError("The encrypted backup exceeds the supported size.")
    with backup_path.open("rb") as source:
        header, key, ciphertext_offset = _read_header(source, password)
        ciphertext_bytes = size - ciphertext_offset - GCM_TAG_BYTES
        if ciphertext_bytes <= 0:
            raise BackupAuthenticationError("The encrypted backup is incomplete.")
        source.seek(-GCM_TAG_BYTES, os.SEEK_END)
        tag = source.read(GCM_TAG_BYTES)
        source.seek(ciphertext_offset)
        nonce = base64.b64decode(header["nonce"], validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        source.seek(0)
        aad = source.read(ciphertext_offset)
        decryptor.authenticate_additional_data(aad)
        source.seek(ciphertext_offset)
        remaining = ciphertext_bytes
        try:
            with archive_path.open("wb") as destination:
                while remaining:
                    _checkpoint(checkpoint)
                    chunk = source.read(min(STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise BackupAuthenticationError("The encrypted backup is incomplete.")
                    remaining -= len(chunk)
                    destination.write(decryptor.update(chunk))
                    if destination.tell() > MAXIMUM_EXPANDED_BYTES:
                        raise BackupRestoreError("The decrypted backup exceeds the supported size.")
                destination.write(decryptor.finalize())
        except InvalidTag as exc:
            archive_path.unlink(missing_ok=True)
            raise BackupAuthenticationError(
                "The backup password is incorrect, or the backup file was changed or damaged."
            ) from exc
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
    if (
        header.get("format") != BACKUP_FORMAT
        or header.get("schema_version") != BACKUP_SCHEMA_VERSION
        or header.get("cipher") != "AES-256-GCM"
        or header.get("kdf") != "scrypt"
    ):
        archive_path.unlink(missing_ok=True)
        raise BackupCompatibilityError("This backup format is not supported by this CareerTrellis version.")
    return header


def _safe_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise BackupRestoreError("The backup contains an unsafe file path.")
    path = PurePosixPath(name)
    unsafe_part = any(
        part in {"", "."}
        or ":" in part
        or part.endswith((" ", "."))
        for part in path.parts
    )
    if path.is_absolute() or ".." in path.parts or unsafe_part:
        raise BackupRestoreError("The backup contains an unsafe file path.")
    return path.as_posix()


def _build_number(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except (AttributeError, ValueError):
        return ()


def _read_manifest(archive: zipfile.ZipFile, current_build: str) -> dict:
    infos = archive.infolist()
    if len(infos) > MAXIMUM_MATERIAL_FILES + 2:
        raise BackupRestoreError("The backup contains too many files.")
    names = []
    for info in infos:
        normalized = _safe_member_name(info.filename)
        if (
            info.is_dir()
            or info.filename != normalized
            or info.flag_bits & 0x1
            or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
        ):
            raise BackupRestoreError("The backup contains an unsupported archive entry.")
        names.append(normalized)
    if len(names) != len(set(name.casefold() for name in names)):
        raise BackupRestoreError("The backup contains duplicate file paths.")
    if "manifest.json" not in names:
        raise BackupRestoreError("The backup manifest is missing.")
    manifest_info = archive.getinfo("manifest.json")
    if manifest_info.file_size > MAXIMUM_MANIFEST_BYTES:
        raise BackupRestoreError("The backup manifest is too large.")
    if sum(info.file_size for info in infos) > MAXIMUM_EXPANDED_BYTES:
        raise BackupRestoreError("The backup expands beyond the supported size.")
    try:
        manifest = json.loads(archive.read(manifest_info))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupRestoreError("The backup manifest is invalid.") from exc
    if not isinstance(manifest, dict):
        raise BackupRestoreError("The backup manifest is invalid.")
    if manifest.get("format") != BACKUP_FORMAT:
        raise BackupCompatibilityError("The backup manifest format is not supported.")
    if manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupCompatibilityError("This backup requires a different CareerTrellis backup format.")
    if manifest.get("database_schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupCompatibilityError("This backup uses an incompatible database schema.")
    minimum_build = manifest.get("minimum_restore_build", "")
    if not _build_number(minimum_build) or _build_number(minimum_build) > _build_number(current_build):
        raise BackupCompatibilityError(
            f"Update CareerTrellis to build {minimum_build or 'listed by the backup'} or later before restoring."
        )
    if manifest.get("database_path") != "database.sqlite3":
        raise BackupRestoreError("The backup database location is invalid.")
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise BackupRestoreError("The backup file inventory is invalid.")
    inventory: dict[str, dict] = {}
    for record in records:
        try:
            path = _safe_member_name(record["path"])
            size = record["size"]
            digest = record["sha256"]
        except (KeyError, TypeError) as exc:
            raise BackupRestoreError("The backup file inventory is invalid.") from exc
        if path == "manifest.json" or path in inventory:
            raise BackupRestoreError("The backup file inventory is invalid.")
        if (
            not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise BackupRestoreError("The backup file inventory is invalid.")
        inventory[path] = record
    if set(names) != set(inventory) | {"manifest.json"}:
        raise BackupRestoreError("The backup files do not match its manifest.")
    material_paths = [name for name in inventory if name.startswith("materials/")]
    if len(material_paths) != manifest.get("material_file_count"):
        raise BackupRestoreError("The backup material count does not match its manifest.")
    manifest["_inventory"] = inventory
    return manifest


def _extract_verified_archive(
    archive_path: Path,
    stage_directory: Path,
    current_build: str,
    output_dir: Path,
    checkpoint: Checkpoint | None,
) -> dict:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except zipfile.BadZipFile as exc:
        raise BackupRestoreError("The decrypted backup archive is damaged.") from exc
    with archive:
        manifest = _read_manifest(archive, current_build)
        inventory = manifest.pop("_inventory")
        for name, record in inventory.items():
            _checkpoint(checkpoint)
            if name == "database.sqlite3":
                destination = stage_directory / "database.sqlite3"
            elif name.startswith("materials/"):
                relative = PurePosixPath(name).relative_to("materials")
                destination = stage_directory / "output" / Path(*relative.parts)
            else:
                raise BackupRestoreError("The backup contains an unsupported file.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(name, "r") as source, destination.open("wb") as target:
                while chunk := source.read(STREAM_CHUNK_BYTES):
                    _checkpoint(checkpoint)
                    written += len(chunk)
                    if written > record["size"]:
                        raise BackupRestoreError("A backup file exceeds its declared size.")
                    digest.update(chunk)
                    target.write(chunk)
            if written != record["size"] or digest.hexdigest() != record["sha256"]:
                raise BackupRestoreError("A backup file failed its integrity check.")
    (stage_directory / "output").mkdir(exist_ok=True)
    _rewrite_portable_material_paths(stage_directory / "database.sqlite3", output_dir)
    _validate_database(stage_directory / "database.sqlite3")
    return manifest


def _rewrite_portable_material_paths(database_path: Path, output_dir: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'applications'"
        ).fetchone()
        if not table:
            return
        columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
        root = output_dir.resolve()
        for column in ("tailored_resume_path", "cover_letter_path"):
            if column not in columns:
                continue
            rows = connection.execute(
                f"SELECT id, {column} FROM applications WHERE {column} IS NOT NULL AND {column} != ''"
            ).fetchall()
            for application_id, raw_path in rows:
                if not isinstance(raw_path, str) or not raw_path.startswith(PORTABLE_OUTPUT_PREFIX):
                    continue
                relative_text = raw_path[len(PORTABLE_OUTPUT_PREFIX):]
                safe = _safe_member_name(relative_text)
                relative = PurePosixPath(safe)
                destination = root.joinpath(*relative.parts)
                connection.execute(
                    f"UPDATE applications SET {column} = ? WHERE id = ?",
                    (str(destination), application_id),
                )
        connection.commit()
    finally:
        connection.close()


def _validate_database(database_path: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupRestoreError("The restored database failed an integrity check.")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        required = {"profile", "jobs", "applications"}
        if not required.issubset(tables):
            raise BackupCompatibilityError("The backup database is missing required CareerTrellis tables.")
    finally:
        connection.close()


def _checkpoint_live_database(database_path: Path) -> None:
    """Flush a live WAL before the short replacement boundary or fail without mutation."""
    if not database_path.exists():
        return
    connection = sqlite3.connect(str(database_path), timeout=1.0)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal_mode).lower() == "wal":
            checkpoint = connection.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
            if checkpoint and checkpoint[0]:
                raise BackupRestoreError(
                    "The current database is busy. Finish other CareerTrellis work and try the restore again."
                )
    except sqlite3.OperationalError as exc:
        raise BackupRestoreError(
            "The current database is busy. Finish other CareerTrellis work and try the restore again."
        ) from exc
    finally:
        connection.close()


def _recovery_marker(recovery_directory: Path, payload: dict) -> None:
    (recovery_directory / "recovery.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def restore_encrypted_backup(
    backup_path: str | Path,
    password: str,
    *,
    database_path: str | Path,
    output_dir: str | Path,
    recovery_root: str | Path,
    application_build: str,
    checkpoint: Checkpoint | None = None,
    post_restore_check: Callable[[], None] | None = None,
) -> RestoreSummary:
    """Validate fully, then atomically replace the workspace and retain recovery data."""
    _password_bytes(password, creating=False)
    backup = Path(backup_path)
    database = Path(database_path)
    output = Path(output_dir)
    database.parent.mkdir(parents=True, exist_ok=True)
    recovery_base = Path(recovery_root)
    recovery_base.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=database.parent))
    encrypted_archive = stage / "workspace.zip"
    recovery_directory: Path | None = None
    moved_old_database = False
    moved_old_output = False
    installed_database = False
    installed_output = False
    try:
        header = _decrypt_backup(backup, encrypted_archive, password, checkpoint)
        manifest = _extract_verified_archive(encrypted_archive, stage, application_build, output, checkpoint)
        if header.get("application_build") != manifest.get("application_build"):
            raise BackupRestoreError("The backup metadata does not match its encrypted manifest.")
        if header.get("created_at") != manifest.get("created_at"):
            raise BackupRestoreError("The backup timestamps do not match.")
        _checkpoint(checkpoint)  # Last cancellable boundary; replacement below is short and atomic.
        _checkpoint_live_database(database)

        recovery_directory = recovery_base / (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        )
        recovery_directory.mkdir(parents=False)
        _recovery_marker(recovery_directory, {
            "status": "restore-started",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "backup_created_at": manifest["created_at"],
            "backup_application_build": manifest["application_build"],
        })

        if database.exists():
            os.replace(database, recovery_directory / "database.sqlite3")
            moved_old_database = True
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists():
                os.replace(sidecar, recovery_directory / ("database.sqlite3" + suffix))
        if output.exists():
            os.replace(output, recovery_directory / "output")
            moved_old_output = True
        os.replace(stage / "database.sqlite3", database)
        installed_database = True
        os.replace(stage / "output", output)
        installed_output = True
        _validate_database(database)
        if post_restore_check:
            post_restore_check()
        _recovery_marker(recovery_directory, {
            "status": "restore-complete",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "backup_created_at": manifest["created_at"],
            "backup_application_build": manifest["application_build"],
            "message": "This directory contains the workspace from immediately before the restore.",
        })
    except Exception as exc:
        if recovery_directory:
            try:
                if installed_database and database.exists():
                    os.replace(database, recovery_directory / "failed-restored-database.sqlite3")
                if installed_output and output.exists():
                    os.replace(output, recovery_directory / "failed-restored-output")
                if moved_old_database and (recovery_directory / "database.sqlite3").exists():
                    os.replace(recovery_directory / "database.sqlite3", database)
                if moved_old_output and (recovery_directory / "output").exists():
                    os.replace(recovery_directory / "output", output)
                _recovery_marker(recovery_directory, {
                    "status": "restore-rolled-back",
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "message": "The restore failed and the previous workspace was put back.",
                })
            except Exception as rollback_exc:
                raise BackupRestoreError(
                    f"Restore recovery needs attention. Keep the recovery directory: {recovery_directory}"
                ) from rollback_exc
        raise exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    return RestoreSummary(
        manifest["created_at"],
        manifest["application_build"],
        manifest["material_file_count"],
        str(recovery_directory),
    )
