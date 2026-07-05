"""Tests for the deterministic repository search tool.

Each test builds a self-contained fake demo repo under pytest's ``tmp_path`` so
the suite never depends on the real ``demo/`` fixtures and never touches the
filesystem outside the temporary root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.path_guard import PathGuardError
from app.tools.repo_search import (
    RepoSearchResult,
    RepoSnippet,
    list_repo_files,
    read_file_snippet,
    search_repo,
)


def _build_fake_repo(root: Path) -> None:
    """Create a small but realistic repo tree under ``root``."""
    app_dir = root / "app" / "routes"
    app_dir.mkdir(parents=True)
    (app_dir / "payments.py").write_text(
        "def create_payment(request):\n"
        "    payment = Payment(request.amount)\n"
        "    user = get_user(request.user_id)\n"
        "    payment.user_id = user.id\n"
        "    return payment\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Demo\n\nA tiny payments service.\n", encoding="utf-8"
    )

    # Noise that must be ignored: .git, __pycache__, a binary file.
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("user.id = secret\n", encoding="utf-8")

    cache_dir = root / "app" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "payments.cpython-311.pyc").write_text(
        "user.id cached\n", encoding="utf-8"
    )

    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00user.id\x00\xff")


# ---- happy paths -----------------------------------------------------------


def test_finds_query_in_python_file(tmp_path: Path):
    _build_fake_repo(tmp_path)

    results = search_repo(tmp_path, "get_user")

    assert results
    assert all(isinstance(r, RepoSearchResult) for r in results)
    assert any(r.path == "app/routes/payments.py" for r in results)
    match = next(r for r in results if r.path == "app/routes/payments.py")
    assert "get_user" in match.matched_text


def test_returns_correct_relative_path(tmp_path: Path):
    _build_fake_repo(tmp_path)

    results = search_repo(tmp_path, "get_user")
    match = next(r for r in results if "get_user" in r.matched_text)

    # Relative path only, and it really resolves to a file inside the root.
    assert match.path == "app/routes/payments.py"
    assert not Path(match.path).is_absolute()
    assert (tmp_path / match.path).is_file()


def test_returns_correct_line_range(tmp_path: Path):
    _build_fake_repo(tmp_path)

    # context_lines=0 → the range is exactly the matched line.
    results = search_repo(tmp_path, "get_user", context_lines=0)
    match = next(r for r in results if r.path == "app/routes/payments.py")

    # ``get_user`` first appears on line 3 of payments.py.
    assert match.line_start == 3
    assert match.line_end == 3
    assert match.snippet == "    user = get_user(request.user_id)"


def test_includes_context_lines(tmp_path: Path):
    _build_fake_repo(tmp_path)

    results = search_repo(tmp_path, "get_user", context_lines=2)
    match = next(r for r in results if r.path == "app/routes/payments.py")

    # Match on line 3, 2 lines of context each side → lines 1..5.
    assert match.line_start == 1
    assert match.line_end == 5
    assert match.snippet.splitlines() == [
        "def create_payment(request):",
        "    payment = Payment(request.amount)",
        "    user = get_user(request.user_id)",
        "    payment.user_id = user.id",
        "    return payment",
    ]


def test_does_not_invent_nonexistent_files(tmp_path: Path):
    _build_fake_repo(tmp_path)

    results = search_repo(tmp_path, "payment")

    assert results
    for result in results:
        assert result.path_verified is True
        assert (tmp_path / result.path).is_file()


def test_no_duplicate_same_file_same_line(tmp_path: Path):
    _build_fake_repo(tmp_path)

    results = search_repo(tmp_path, "payment")
    # No two results may share the same file + snippet range.
    keys = [(r.path, r.line_start, r.line_end) for r in results]
    assert len(keys) == len(set(keys))


# ---- safety paths ----------------------------------------------------------


def test_blocks_path_traversal_in_read_snippet(tmp_path: Path):
    _build_fake_repo(tmp_path)

    with pytest.raises(PathGuardError):
        read_file_snippet(tmp_path, "../secrets.txt", 1, 1)


def test_ignores_git_and_pycache(tmp_path: Path):
    _build_fake_repo(tmp_path)

    # "user.id" exists in payments.py AND in ignored .git/__pycache__/.png.
    results = search_repo(tmp_path, "user.id")

    assert results
    for result in results:
        parts = Path(result.path).parts
        assert ".git" not in parts
        assert "__pycache__" not in parts
        assert not result.path.endswith(".png")

    files = list_repo_files(tmp_path)
    assert "app/routes/payments.py" in files
    assert "README.md" in files
    assert all(".git" not in f and "__pycache__" not in f for f in files)
    assert "logo.png" not in files


# ---- read_file_snippet -----------------------------------------------------


def test_read_file_snippet_returns_exact_range(tmp_path: Path):
    _build_fake_repo(tmp_path)

    snippet = read_file_snippet(tmp_path, "app/routes/payments.py", 2, 4)

    assert isinstance(snippet, RepoSnippet)
    assert snippet.path == "app/routes/payments.py"
    assert snippet.line_start == 2
    assert snippet.line_end == 4
    assert snippet.path_verified is True
    assert snippet.snippet.splitlines() == [
        "    payment = Payment(request.amount)",
        "    user = get_user(request.user_id)",
        "    payment.user_id = user.id",
    ]


def test_read_file_snippet_blocks_invalid_path(tmp_path: Path):
    _build_fake_repo(tmp_path)

    # Nonexistent file inside the root → FileNotFoundError.
    with pytest.raises(FileNotFoundError):
        read_file_snippet(tmp_path, "app/routes/does_not_exist.py", 1, 1)


# ---- empty query behavior --------------------------------------------------


def test_empty_query_raises_value_error(tmp_path: Path):
    _build_fake_repo(tmp_path)

    with pytest.raises(ValueError):
        search_repo(tmp_path, "")
