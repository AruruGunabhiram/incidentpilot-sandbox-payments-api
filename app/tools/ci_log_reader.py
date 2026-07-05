"""Deterministic CI log reader and evidence extractor.

Loads a ``ci.log`` from an allowed root only, redacts secrets *before* anything
else touches the text, preserves the original 1-based line numbers, and pulls
out grounded failure evidence: the failing pytest test, the primary error, and
the stack-trace block.

Everything here is deterministic and regex-only. It never invents a test name,
error, or stack trace — if the text does not contain it, the corresponding
field is ``None`` and ``needs_human_review`` is raised. Logs are untrusted
input, so extraction always runs on the *redacted* text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.tools.path_guard import verify_file_exists
from app.tools.redactor import redact_with_count

# --- pytest / traceback patterns --------------------------------------------

# A pytest node id, e.g. tests/test_payments.py::test_create_payment_success
_NODE_ID = re.compile(r"([\w./-]+\.py::[\w\[\].:/-]+)")

# CI logs (e.g. GitHub Actions) prefix every line with an ISO timestamp and
# sometimes a log tag like ``[ERROR] ``. This optional prefix is consumed before
# matching an error so anchored patterns still work.
_PREFIX = r"(?:\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+)?(?:\[[^\]]*\]\s*)*"

# Pytest-rendered exception line: ``E   AttributeError: <message>``
_E_ERROR = re.compile(
    rf"^{_PREFIX}E\s+([A-Za-z_]\w*(?:Error|Exception|Warning)):\s*(.*)$"
)

# A bare exception line, optionally prefixed by a log tag like ``[ERROR] ``:
# ``ConnectionError: billing upstream timeout``
_BARE_ERROR = re.compile(
    rf"^{_PREFIX}([A-Za-z_]\w*(?:Error|Exception)):\s*(.*)$"
)

_TRACEBACK_START = "Traceback (most recent call last):"


@dataclass(frozen=True)
class LogLine:
    line_number: int
    text: str


@dataclass(frozen=True)
class ErrorFinding:
    error_type: str
    message: str
    line_number: int
    raw_line: str


@dataclass(frozen=True)
class StackTraceBlock:
    line_start: int
    line_end: int
    text: str


@dataclass(frozen=True)
class CILogResult:
    source_path: str
    lines: list[LogLine] = field(default_factory=list)
    failing_test: str | None = None
    primary_error: ErrorFinding | None = None
    stack_trace: StackTraceBlock | None = None
    redactions_applied: int = 0
    needs_human_review: bool = True


# --- pure extraction helpers (operate on already-redacted text) -------------


def number_lines(text: str) -> list[LogLine]:
    """Split ``text`` into 1-based numbered lines. Empty text yields ``[]``."""
    return [
        LogLine(line_number=index, text=line)
        for index, line in enumerate(text.splitlines(), start=1)
    ]


def extract_failing_pytest_test(text: str) -> str | None:
    """Return the first failing pytest node id, or ``None``.

    Looks only at lines that mention ``FAILED`` so a passing node id is never
    misreported as failing.
    """
    for line in text.splitlines():
        if "FAILED" in line:
            match = _NODE_ID.search(line)
            if match:
                return match.group(1)
    return None


def extract_primary_error(text: str) -> ErrorFinding | None:
    """Return the first error finding, or ``None`` if no error line is present.

    Prefers the pytest ``E   <Error>: <msg>`` form; falls back to a bare
    ``<Error>: <msg>`` line (optionally log-tagged). Line numbers are original.
    """
    lines = number_lines(text)

    for line in lines:
        match = _E_ERROR.match(line.text)
        if match:
            return ErrorFinding(
                error_type=match.group(1),
                message=match.group(2).strip(),
                line_number=line.line_number,
                raw_line=line.text,
            )

    for line in lines:
        match = _BARE_ERROR.match(line.text)
        if match:
            return ErrorFinding(
                error_type=match.group(1),
                message=match.group(2).strip(),
                line_number=line.line_number,
                raw_line=line.text,
            )

    return None


def extract_stack_trace_block(text: str) -> StackTraceBlock | None:
    """Return the traceback block, or ``None`` if no ``Traceback`` marker exists.

    The block spans from the ``Traceback (most recent call last):`` line through
    the first following exception line (its end). If no closing exception line
    is found, the block runs to the end of the log.
    """
    lines = number_lines(text)

    start: LogLine | None = None
    for line in lines:
        if _TRACEBACK_START in line.text:
            start = line
            break
    if start is None:
        return None

    end = lines[-1]
    for line in lines:
        if line.line_number <= start.line_number:
            continue
        if _BARE_ERROR.match(line.text):
            end = line
            break

    block_text = "\n".join(
        line.text
        for line in lines
        if start.line_number <= line.line_number <= end.line_number
    )
    return StackTraceBlock(
        line_start=start.line_number,
        line_end=end.line_number,
        text=block_text,
    )


# --- main entry point -------------------------------------------------------


def read_ci_log(root: Path | str, log_path: Path | str = "ci.log") -> CILogResult:
    """Load, redact, and extract evidence from a CI log.

    Resolves ``log_path`` safely under ``root`` (raising ``PathGuardError`` on
    traversal and ``FileNotFoundError`` if missing), redacts secrets, preserves
    original line numbers, and extracts the failing test, primary error, and
    stack trace. ``needs_human_review`` is ``True`` when no primary error is
    found (including empty logs).
    """
    safe_path = verify_file_exists(root, log_path)
    redacted, redactions = redact_with_count(safe_path.read_text(encoding="utf-8"))

    lines = number_lines(redacted)
    primary_error = extract_primary_error(redacted)

    root_resolved = Path(root).resolve()
    try:
        source_path = str(safe_path.relative_to(root_resolved))
    except ValueError:
        source_path = str(safe_path)

    return CILogResult(
        source_path=source_path,
        lines=lines,
        failing_test=extract_failing_pytest_test(redacted),
        primary_error=primary_error,
        stack_trace=extract_stack_trace_block(redacted),
        redactions_applied=redactions,
        needs_human_review=primary_error is None,
    )
