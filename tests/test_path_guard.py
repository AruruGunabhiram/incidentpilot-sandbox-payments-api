from pathlib import Path

import pytest

from app.tools.path_guard import (
    PathGuardError,
    resolve_safe_path,
    verify_directory_exists,
    verify_file_exists,
)
from app.tools.repo_search import read_file_snippet

# The real demo repository root. Path traversal must never escape it to read a
# system file such as /etc/passwd.
DEMO_REPO_ROOT = Path(__file__).resolve().parents[1] / "demo" / "demo_repo"


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """An allowed root directory with one nested file."""
    base = tmp_path / "repo"
    (base / "app" / "routes").mkdir(parents=True)
    (base / "app" / "routes" / "payments.py").write_text("ok", encoding="utf-8")
    (base / "top.txt").write_text("ok", encoding="utf-8")
    return base


# 1. Allows a normal file inside the root. -----------------------------------
def test_allows_normal_file_inside_root(root: Path):
    resolved = resolve_safe_path(root, "top.txt")
    assert resolved == (root / "top.txt").resolve()


# 2. Blocks "../outside.txt". -------------------------------------------------
def test_blocks_single_parent_traversal(root: Path):
    with pytest.raises(PathGuardError):
        resolve_safe_path(root, "../outside.txt")


# 3. Blocks "../../etc/passwd". -----------------------------------------------
def test_blocks_deep_parent_traversal(root: Path):
    with pytest.raises(PathGuardError):
        resolve_safe_path(root, "../../etc/passwd")


# 4. Blocks an absolute path outside the allowed root. ------------------------
def test_blocks_absolute_path_outside_root(root: Path):
    with pytest.raises(PathGuardError):
        resolve_safe_path(root, "/etc/passwd")


# 5. Blocks a symlink that points outside the root. --------------------------
def test_blocks_symlink_escape(root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    link = root / "escape.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    with pytest.raises(PathGuardError):
        resolve_safe_path(root, "escape.txt")


# 6. Verifies an existing file. ----------------------------------------------
def test_verify_file_exists_for_real_file(root: Path):
    resolved = verify_file_exists(root, "app/routes/payments.py")
    assert resolved.is_file()
    assert resolved == (root / "app/routes/payments.py").resolve()


# 7. Fails on a missing file. -------------------------------------------------
def test_verify_file_exists_missing_file_raises(root: Path):
    with pytest.raises(FileNotFoundError):
        verify_file_exists(root, "app/routes/missing.py")


# 8. Allows a nested valid file. ---------------------------------------------
def test_allows_nested_valid_file(root: Path):
    resolved = resolve_safe_path(root, "app/routes/payments.py")
    assert resolved == (root / "app/routes/payments.py").resolve()
    # And the directory check works for the nested directory too.
    assert verify_directory_exists(root, "app/routes").is_dir()


# 9. Returns a resolved Path object. -----------------------------------------
def test_returns_resolved_path_object(root: Path):
    resolved = resolve_safe_path(root, "top.txt")
    assert isinstance(resolved, Path)
    assert resolved.is_absolute()
    assert resolved == resolved.resolve()


# Bonus: the root itself is allowed, and a clear message is raised on escape.
def test_root_itself_is_allowed_and_error_is_readable(root: Path):
    assert verify_directory_exists(root) == root.resolve()
    with pytest.raises(PathGuardError) as excinfo:
        resolve_safe_path(root, "../../etc/passwd")
    assert "traversal" in str(excinfo.value).lower()


# Phase 7: path traversal is refused at resolution time, before any file read,
# so nothing outside the allowed root (here, the demo repo) can ever be read.
def test_path_traversal_is_rejected_before_file_read(root: Path, tmp_path: Path):
    # Plant a real, readable file OUTSIDE the allowed root with known content.
    # If the guard ever leaked, this content could surface; it must not.
    outside = tmp_path / "secret_outside.txt"
    outside.write_text("TOP_SECRET_DO_NOT_READ", encoding="utf-8")

    traversal_paths = [
        "../secret_outside.txt",      # one level up to the planted secret
        "../../etc/passwd",           # classic deep traversal
        "../../../../etc/passwd",     # deeper traversal
        "/etc/passwd",                # absolute path outside the root
    ]

    for bad in traversal_paths:
        # Resolution refuses the path...
        with pytest.raises(PathGuardError):
            resolve_safe_path(root, bad)
        # ...and so do both file-access helpers, which resolve *before* reading.
        with pytest.raises(PathGuardError):
            verify_file_exists(root, bad)
        with pytest.raises(PathGuardError):
            read_file_snippet(root, bad, 1, 1)

    # The planted outside file really exists and is readable, so the guard — not
    # a missing file — is what blocked access.
    assert outside.read_text(encoding="utf-8") == "TOP_SECRET_DO_NOT_READ"


def test_path_traversal_cannot_escape_demo_repo_root():
    """The same guard protects the real demo repo root used by the pipeline."""
    assert DEMO_REPO_ROOT.is_dir()
    for bad in ("../../etc/passwd", "../../../../etc/passwd", "/etc/passwd"):
        with pytest.raises(PathGuardError):
            resolve_safe_path(DEMO_REPO_ROOT, bad)
        with pytest.raises(PathGuardError):
            read_file_snippet(DEMO_REPO_ROOT, bad, 1, 1)
