from pathlib import Path

import pytest

from app.tools.ci_log_reader import (
    extract_failing_pytest_test,
    extract_primary_error,
    extract_stack_trace_block,
    number_lines,
    read_ci_log,
)
from app.tools.path_guard import PathGuardError
from app.tools.redactor import REDACTION_MARKER

BROKEN = Path("demo/incidents/broken_api_route")
SECRET = Path("demo/incidents/secret_in_logs")

RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]

CLASSIC_TRACEBACK = """[INFO] starting job
Traceback (most recent call last):
  File "app/services/billing.py", line 42, in charge
    raise ConnectionError("billing upstream timeout")
ConnectionError: billing upstream timeout
[INFO] job finished
"""

ASSERTION_LOG = """tests/test_math.py::test_adds FAILED
    def test_adds():
>       assert add(1, 1) == 3
E       AssertionError: assert 2 == 3
"""


# 1. Loads fixture log and returns numbered lines. ---------------------------
def test_loads_fixture_and_numbers_lines():
    result = read_ci_log(BROKEN, "ci.log")

    assert result.lines
    assert result.lines[0].line_number == 1
    assert [ln.line_number for ln in result.lines] == list(
        range(1, len(result.lines) + 1)
    )
    assert result.source_path == "ci.log"


# 2. Extracts the failing pytest test from a FAILED line. --------------------
def test_extracts_failing_pytest_test():
    result = read_ci_log(BROKEN, "ci.log")
    assert result.failing_test == "tests/test_payments.py::test_create_payment_success"


# 3. Extracts the primary AttributeError. ------------------------------------
def test_extracts_primary_attribute_error():
    result = read_ci_log(BROKEN, "ci.log")

    assert result.primary_error is not None
    assert result.primary_error.error_type == "AttributeError"
    assert "object has no attribute 'id'" in result.primary_error.message
    assert result.primary_error.line_number == 26
    assert "AttributeError" in result.primary_error.raw_line
    assert result.needs_human_review is False


# 4. Extracts an AssertionError. ---------------------------------------------
def test_extracts_assertion_error():
    finding = extract_primary_error(ASSERTION_LOG)

    assert finding is not None
    assert finding.error_type == "AssertionError"
    assert "assert 2 == 3" in finding.message


# 5. Extracts the stack-trace block from Traceback to final error line. ------
def test_extracts_stack_trace_block():
    block = extract_stack_trace_block(CLASSIC_TRACEBACK)

    assert block is not None
    # Traceback marker is line 2, final error line is line 5.
    assert block.line_start == 2
    assert block.line_end == 5
    assert "Traceback (most recent call last):" in block.text
    assert "ConnectionError: billing upstream timeout" in block.text


# 6. Empty log: no primary error and needs_human_review=True. ----------------
def test_empty_log_needs_human_review(tmp_path: Path):
    empty = tmp_path / "ci.log"
    empty.write_text("", encoding="utf-8")

    result = read_ci_log(tmp_path, "ci.log")
    assert result.lines == []
    assert result.primary_error is None
    assert result.failing_test is None
    assert result.stack_trace is None
    assert result.needs_human_review is True


# 7. Log containing a fake secret is redacted. -------------------------------
def test_secret_log_is_redacted():
    result = read_ci_log(SECRET, "ci.log")
    joined = "\n".join(ln.text for ln in result.lines)

    for secret in RAW_FAKE_SECRETS:
        assert secret not in joined, f"raw secret leaked: {secret}"
    assert REDACTION_MARKER in joined
    assert result.redactions_applied >= 4


# 8. Path traversal to the log is blocked. -----------------------------------
def test_path_traversal_is_blocked():
    with pytest.raises(PathGuardError):
        read_ci_log(BROKEN, "../../../etc/passwd")


# 9. Missing log file fails clearly. -----------------------------------------
def test_missing_log_file_raises():
    with pytest.raises(FileNotFoundError):
        read_ci_log(BROKEN, "does_not_exist.log")


# Helper-level sanity: number_lines is 1-based and safe on empty input.
def test_number_lines_basics():
    assert number_lines("") == []
    lines = number_lines("a\nb")
    assert [(ln.line_number, ln.text) for ln in lines] == [(1, "a"), (2, "b")]


# Helper-level sanity: no FAILED line means no invented test name.
def test_no_failed_line_returns_none():
    assert extract_failing_pytest_test("everything passed\n2 passed in 0.1s") is None
