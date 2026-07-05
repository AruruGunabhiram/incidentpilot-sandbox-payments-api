"""Deterministic secret redaction.

Logs, GitHub text, stack traces, repo snippets, reports, and markdown are all
untrusted input. Secrets must be scrubbed before any of that text is displayed,
persisted to a report, or handed to an agent/LLM.

This module is pure, deterministic, and regex-only (sufficient for Phase 3):
the same input always yields the same output, the same count, and the same
findings. Replacements are typed, e.g. ``[REDACTED_SECRET:type=github_token]``,
and redaction is idempotent — running it twice does not re-redact or change the
count, and findings never contain a full secret value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Public marker shared by every replacement, so callers can cheaply check
# "was anything redacted?" without knowing the type taxonomy.
REDACTION_MARKER = "REDACTED_SECRET"
_PREVIEW_CHARS = 4


def _replacement(secret_type: str) -> str:
    return f"[{REDACTION_MARKER}:type={secret_type}]"


# Ordered (type, pattern) pairs. Order matters: more specific / greedier
# patterns (e.g. DATABASE_URL=...) run before the generic connection-string
# pattern so a value is attributed to a single, most-specific type.
#
# Key/value patterns require an actual ``=`` or ``:`` separator followed by a
# non-empty value, so bare words like ``passwordless`` or ``api_key_missing``
# are left untouched.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github_fine_grained_token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai_token", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")),
    (
        "database_url",
        re.compile(r"(?i)\bDATABASE_URL\s*[=:]\s*\S+"),
    ),
    (
        "connection_string",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:[^\s/@]+@\S+"),
    ),
    (
        "api_key",
        re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*[^\s,&'\"]+"),
    ),
    (
        "password",
        re.compile(r"(?i)\bpassword\s*[=:]\s*[^\s,&'\"]+"),
    ),
]


@dataclass(frozen=True)
class RedactionFinding:
    """A single redacted secret. Never carries the full original value."""

    type: str
    original_preview: str
    replacement: str


@dataclass(frozen=True)
class RedactionResult:
    """Result of :func:`redact_text`."""

    redacted_text: str
    redactions_applied: int = 0
    findings: list[RedactionFinding] = field(default_factory=list)


def _preview(secret: str) -> str:
    """Return a safe preview: first 4 chars + '...'. Never the full secret."""
    head = secret[:_PREVIEW_CHARS]
    return f"{head}..."


def redact_text(text: str) -> RedactionResult:
    """Redact every known secret in ``text`` and report what was removed.

    Deterministic and idempotent. The returned ``findings`` only ever contain a
    short, safe preview of each secret (first 4 characters), never the value.
    """
    findings: list[RedactionFinding] = []
    redacted = text

    for secret_type, pattern in _PATTERNS:
        replacement = _replacement(secret_type)

        def _sub(match: re.Match[str], _type: str = secret_type, _repl: str = replacement) -> str:
            findings.append(
                RedactionFinding(
                    type=_type,
                    original_preview=_preview(match.group(0)),
                    replacement=_repl,
                )
            )
            return _repl

        redacted = pattern.sub(_sub, redacted)

    return RedactionResult(
        redacted_text=redacted,
        redactions_applied=len(findings),
        findings=findings,
    )


def contains_secret(text: str) -> bool:
    """Return True if ``text`` contains at least one detectable secret."""
    return any(pattern.search(text) for _type, pattern in _PATTERNS)


# ---------------------------------------------------------------------------
# Backward-compatible helpers used by ci_log_reader and report_writer.
# ---------------------------------------------------------------------------


def redact_secrets(text: str) -> str:
    """Return ``text`` with every known secret replaced (string only)."""
    return redact_text(text).redacted_text


def redact_with_count(text: str) -> tuple[str, int]:
    """Return ``(redacted_text, redactions_applied)``."""
    result = redact_text(text)
    return result.redacted_text, result.redactions_applied


def count_secrets(text: str) -> int:
    """Return how many secrets :func:`redact_text` would remove from ``text``."""
    return redact_text(text).redactions_applied
