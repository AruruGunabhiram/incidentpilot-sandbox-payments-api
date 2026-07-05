"""Deterministic filesystem safety layer.

Shared by ``repo_search``, ``ci_log_reader``, and ``report_writer`` to ensure
every file access stays inside an explicitly allowed root. Untrusted input may
ask for ``../../etc/passwd``, an absolute path, or a symlink that escapes the
root; this layer normalizes and verifies the request and raises a clear,
displayable error instead of silently reading the wrong file.

All resolution goes through :meth:`pathlib.Path.resolve`, which also resolves
symlinks, so a symlink inside the root that points outside it is rejected.
"""

from __future__ import annotations

from pathlib import Path


class PathGuardError(ValueError):
    """Raised when a requested path is unsafe or escapes the allowed root.

    The message is intentionally human-readable: the safety reviewer surfaces it
    directly to a person during incident review.
    """


# Backward-compatible alias for earlier callers/tests. Same class, so
# ``except PathGuardError`` and ``except UnsafePathError`` both catch it.
UnsafePathError = PathGuardError


def assert_within_root(root: Path | str, candidate: Path | str) -> Path:
    """Return the resolved ``candidate`` if it lives inside ``root``.

    Both paths are fully resolved (following symlinks) before comparison, so a
    symlink that points outside the root is rejected here. Raises
    :class:`PathGuardError` otherwise.
    """
    root_resolved = Path(root).resolve()
    candidate_resolved = Path(candidate).resolve()

    if (
        candidate_resolved != root_resolved
        and root_resolved not in candidate_resolved.parents
    ):
        raise PathGuardError(
            f"Path '{candidate}' resolves outside the allowed root "
            f"'{root_resolved}'."
        )
    return candidate_resolved


def resolve_safe_path(root: Path | str, requested_path: Path | str) -> Path:
    """Normalize ``requested_path`` against ``root`` and verify it stays inside.

    Blocks ``..`` traversal and absolute or symlinked paths that escape the
    root. Does not require the path to exist — use :func:`verify_file_exists` or
    :func:`verify_directory_exists` for that. Returns the resolved
    :class:`~pathlib.Path`.
    """
    requested = Path(requested_path)

    if ".." in requested.parts:
        raise PathGuardError(
            f"Path traversal ('..') is not allowed: '{requested_path}'."
        )

    root_resolved = Path(root).resolve()
    candidate = requested if requested.is_absolute() else root_resolved / requested

    return assert_within_root(root_resolved, candidate)


def verify_file_exists(root: Path | str, requested_path: Path | str) -> Path:
    """Return a safe, resolved path that is guaranteed to be an existing file.

    Raises :class:`PathGuardError` if the path escapes the root, or
    :class:`FileNotFoundError` if no regular file exists there.
    """
    safe_path = resolve_safe_path(root, requested_path)
    if not safe_path.is_file():
        raise FileNotFoundError(
            f"No file found at '{requested_path}' under root '{root}'."
        )
    return safe_path


def verify_directory_exists(
    root: Path | str, requested_path: Path | str = "."
) -> Path:
    """Return a safe, resolved path that is guaranteed to be an existing dir.

    Raises :class:`PathGuardError` if the path escapes the root, or
    :class:`FileNotFoundError` if no directory exists there.
    """
    safe_path = resolve_safe_path(root, requested_path)
    if not safe_path.is_dir():
        raise FileNotFoundError(
            f"No directory found at '{requested_path}' under root '{root}'."
        )
    return safe_path


# Backward-compatible alias for the original deterministic resolver.
resolve_under_root = resolve_safe_path
