"""Cooperative cancellation for user-started long-running operations."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from uuid import UUID


class OperationCancelled(Exception):
    """Raised at a safe checkpoint after the user requests cancellation."""


@dataclass
class OperationToken:
    """Thread-safe cancellation token shared by an endpoint and its worker."""

    operation_id: str | None = None
    _cancelled: Event = field(default_factory=Event)
    _temporary_files: set[Path] = field(default_factory=set)

    @property
    def cancellation_requested(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def checkpoint(self) -> None:
        if self.cancellation_requested:
            raise OperationCancelled("The operation was stopped by the user.")

    def track_temporary_file(self, path: str | Path) -> None:
        self._temporary_files.add(Path(path))

    def commit_temporary_file(self, path: str | Path) -> None:
        self._temporary_files.discard(Path(path))

    def cleanup_temporary_files(self) -> None:
        for path in self._temporary_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temporary_files.clear()


_operations: dict[str, OperationToken] = {}
_operations_lock = Lock()


def normalized_operation_id(value: object) -> str | None:
    """Accept only canonical UUIDs supplied by the local frontend."""
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def start_operation(operation_id: object = None) -> OperationToken:
    """Register a cancellable operation, or return a no-op token for legacy callers."""
    normalized = normalized_operation_id(operation_id)
    token = OperationToken(normalized)
    if normalized:
        with _operations_lock:
            _operations[normalized] = token
    return token


def finish_operation(token: OperationToken) -> None:
    """Remove a token only when it is still the active instance for its ID."""
    token.cleanup_temporary_files()
    if not token.operation_id:
        return
    with _operations_lock:
        if _operations.get(token.operation_id) is token:
            _operations.pop(token.operation_id, None)


def request_cancellation(operation_id: object) -> bool:
    """Request cancellation and report whether the operation is still active."""
    normalized = normalized_operation_id(operation_id)
    if not normalized:
        return False
    with _operations_lock:
        token = _operations.get(normalized)
    if not token:
        return False
    token.cancel()
    return True


@contextmanager
def operation_scope(operation_id: object = None):
    """Register and reliably retire a token around synchronous work."""
    token = start_operation(operation_id)
    try:
        yield token
    finally:
        finish_operation(token)
