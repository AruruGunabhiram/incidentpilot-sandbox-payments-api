"""Deterministic repository search over an explicitly allowed root.

The Code Context agent needs *grounded* code evidence: real file paths, real
line numbers, real snippets. This module provides exactly that and nothing
more — keyword search, single-file snippet reads, and a file listing — all
confined to one allowed root via :mod:`app.tools.path_guard`.

Design rules (see ``AGENTS.md`` and ``CLAUDE.md``):

* Never invent a path. Outputs only ever name files that exist inside ``root``,
  and every result carries ``path_verified`` so a caller can assert it.
* All paths in results are *relative* to ``root`` — internal absolute paths are
  never leaked.
* Untrusted input (a query string, a requested file path) cannot escape the
  root. Path resolution goes through ``path_guard``, which blocks ``..``
  traversal, absolute paths, and symlinks that point outside the root.
* Text files only. Binary files, caches, virtualenvs, ``.git``, ``__pycache__``,
  ``.pytest_cache``, and ``node_modules`` are skipped.
* No vector DB, no embeddings — plain deterministic substring matching.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.tools.path_guard import (
    resolve_safe_path,
    verify_directory_exists,
    verify_file_exists,
)

# Extensions treated as searchable text. Anything else is ignored as binary.
DEFAULT_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".log"}
)

# Directory names that are never searched: VCS metadata, caches, dependency and
# virtualenv trees. Matched against any component of a file's relative path.
IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
    }
)


@dataclass(frozen=True)
class RepoSearchResult:
    """A single keyword match, grounded in a real file inside the root.

    ``line_start``/``line_end`` bound the returned ``snippet`` (the matched line
    plus context). ``matched_text`` is the exact line that matched the query.
    ``path`` is always relative to the repo root and ``path_verified`` is true
    only when that file actually exists inside the root.
    """

    path: str
    line_start: int
    line_end: int
    snippet: str
    matched_text: str
    path_verified: bool


@dataclass(frozen=True)
class RepoSnippet:
    """An exact, verified slice of one file.

    Returned by :func:`read_file_snippet`. ``line_start``/``line_end`` are the
    actual 1-based bounds of the returned text (``line_end`` is clamped to the
    last line of the file). ``path`` is relative to the root.
    """

    path: str
    line_start: int
    line_end: int
    snippet: str
    path_verified: bool


def _is_ignored(relative_path: Path) -> bool:
    """True if any path component is an ignored directory name."""
    return any(part in IGNORED_DIR_NAMES for part in relative_path.parts)


def _iter_text_files(root: Path, allowed_extensions: frozenset[str] | set[str]):
    """Yield ``(safe_path, relative_path)`` for searchable text files in ``root``.

    Files in ignored directories, or whose extension is not allowed, are
    skipped. Each yielded path is re-resolved through ``path_guard`` so callers
    never touch anything outside the root.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if _is_ignored(relative_path):
            continue
        if path.suffix.lower() not in allowed_extensions:
            continue
        # Re-resolve from the relative path: rejects symlinks escaping the root.
        safe_path = resolve_safe_path(root, relative_path)
        yield safe_path, relative_path


def search_repo(
    root: Path | str,
    query: str,
    *,
    max_results: int = 10,
    context_lines: int = 2,
) -> list[RepoSearchResult]:
    """Search ``root`` for lines containing ``query`` (case-insensitive).

    Returns up to ``max_results`` :class:`RepoSearchResult` objects, each with a
    snippet of ``context_lines`` lines on either side of the match. Only text
    files inside ``root`` are searched; caches, virtualenvs, and binaries are
    skipped. The same file+line is never returned twice.

    Raises :class:`ValueError` if ``query`` is empty, ``context_lines`` is
    negative, or ``max_results`` is not positive. Raises
    :class:`~app.tools.path_guard.PathGuardError` / :class:`FileNotFoundError`
    if ``root`` is unsafe or not a directory.
    """
    if not query:
        raise ValueError("query must be a non-empty string.")
    if context_lines < 0:
        raise ValueError("context_lines must be >= 0.")
    if max_results <= 0:
        raise ValueError("max_results must be a positive integer.")

    root_resolved = verify_directory_exists(root)
    needle = query.lower()

    results: list[RepoSearchResult] = []
    seen: set[tuple[str, int]] = set()

    for safe_path, relative_path in _iter_text_files(
        root_resolved, DEFAULT_TEXT_EXTENSIONS
    ):
        try:
            lines = safe_path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            # Undecodable or unreadable file: treat as binary and skip.
            continue

        relative_str = relative_path.as_posix()

        for index, line in enumerate(lines):
            if needle not in line.lower():
                continue

            line_number = index + 1
            key = (relative_str, line_number)
            if key in seen:
                continue
            seen.add(key)

            start = max(1, line_number - context_lines)
            end = min(len(lines), line_number + context_lines)
            snippet = "\n".join(lines[start - 1 : end])

            results.append(
                RepoSearchResult(
                    path=relative_str,
                    line_start=start,
                    line_end=end,
                    snippet=snippet,
                    matched_text=line,
                    path_verified=safe_path.is_file(),
                )
            )

            if len(results) >= max_results:
                return results

    return results


def read_file_snippet(
    root: Path | str,
    file_path: str,
    line_start: int,
    line_end: int,
) -> RepoSnippet:
    """Return the exact ``line_start``..``line_end`` slice of ``file_path``.

    Line numbers are 1-based and inclusive. ``line_end`` is clamped to the last
    line of the file. ``path_verified`` is always ``True`` because the file is
    confirmed to exist before reading.

    Raises :class:`ValueError` for an invalid range,
    :class:`~app.tools.path_guard.PathGuardError` if ``file_path`` escapes the
    root, or :class:`FileNotFoundError` if the file does not exist.
    """
    if line_start < 1:
        raise ValueError("line_start must be >= 1.")
    if line_end < line_start:
        raise ValueError("line_end must be >= line_start.")

    safe_path = verify_file_exists(root, file_path)
    root_resolved = Path(root).resolve()
    relative_str = safe_path.relative_to(root_resolved).as_posix()

    lines = safe_path.read_text(encoding="utf-8").splitlines()
    clamped_end = min(line_end, len(lines))
    snippet = "\n".join(lines[line_start - 1 : clamped_end])

    return RepoSnippet(
        path=relative_str,
        line_start=line_start,
        line_end=clamped_end,
        snippet=snippet,
        path_verified=True,
    )


def list_repo_files(
    root: Path | str,
    allowed_extensions: set[str] | None = None,
) -> list[str]:
    """List relative paths of searchable text files inside ``root``.

    Honors the same ignore rules as :func:`search_repo`. Pass
    ``allowed_extensions`` to override :data:`DEFAULT_TEXT_EXTENSIONS`. Raises
    :class:`~app.tools.path_guard.PathGuardError` / :class:`FileNotFoundError`
    if ``root`` is unsafe or not a directory.
    """
    root_resolved = verify_directory_exists(root)
    extensions = (
        DEFAULT_TEXT_EXTENSIONS
        if allowed_extensions is None
        else {ext.lower() for ext in allowed_extensions}
    )
    return [
        relative_path.as_posix()
        for _, relative_path in _iter_text_files(root_resolved, extensions)
    ]
