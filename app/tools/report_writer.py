"""Deterministic incident report serializer.

Turns an :class:`~app.schemas.report.IncidentReport` (or a plain dict with the
same shape) into JSON and Markdown. Every string value is passed through the
redactor first, so no raw secret can appear in either output even if upstream
evidence accidentally carried one. Output is deterministic for a given input
and never invents a field — missing optional data renders as ``Not provided``
or an empty section rather than crashing.

This module is pure and LLM-free.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from app.tools.path_guard import resolve_safe_path
from app.tools.redactor import redact_secrets

JSON_FILENAME = "report.json"
MARKDOWN_FILENAME = "report.md"

NOT_PROVIDED = "Not provided"


class WrittenReport(TypedDict):
    json_path: str
    markdown_path: str


# ---------------------------------------------------------------------------
# Normalization + redaction
# ---------------------------------------------------------------------------


def _to_plain_dict(report: Any) -> dict[str, Any]:
    """Normalize a report into a JSON-safe dict.

    Accepts a Pydantic model (``model_dump``) or an already-plain dict.
    """
    if hasattr(report, "model_dump"):
        return report.model_dump(mode="json")
    if isinstance(report, dict):
        # Round-trip through json to coerce any non-JSON-native values
        # (e.g. datetimes) deterministically.
        return json.loads(json.dumps(report, default=str))
    raise TypeError("report must be a Pydantic model or a dict")


def _redact_tree(value: Any) -> Any:
    """Recursively redact every string in a nested JSON-like structure."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_tree(item) for item in value]
    return value


def build_report_dict(report: Any) -> dict[str, Any]:
    """Return a fully redacted, JSON-safe dict for ``report``."""
    return _redact_tree(_to_plain_dict(report))


def ensure_report_safe(report_text: str) -> str:
    """Return ``report_text`` with every known secret redacted.

    A final belt-and-suspenders pass over already-rendered report text. The
    redactor is idempotent, so calling this on text built from an
    already-redacted dict does not change it.
    """
    return redact_secrets(report_text)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def render_json(report: Any) -> str:
    """Return a deterministic, redacted, pretty-printed JSON string."""
    text = json.dumps(build_report_dict(report), indent=2, ensure_ascii=False)
    return ensure_report_safe(text)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _evidence_line(item: dict[str, Any]) -> str:
    """Render one evidence item: id, source path, line range, and snippet."""
    path = item.get("path") or item.get("source") or "unknown"
    start = item.get("line_start")
    end = item.get("line_end")
    if start is not None and end is not None and start != end:
        location = f"{path}:{start}-{end}"
    elif start is not None:
        location = f"{path}:{start}"
    else:
        location = str(path)

    ev_id = item.get("id")
    prefix = f"**{ev_id}** " if ev_id else ""
    summary = item.get("summary", "")
    line = f"- {prefix}`{location}` — {summary}".rstrip(" —")

    snippet = item.get("snippet")
    if snippet:
        line += f"\n  - `{snippet}`"
    return line


def _section(lines: list[str], heading: str, body: str | None) -> None:
    """Append a ``## heading`` section, falling back to ``Not provided``."""
    lines += [f"## {heading}", "", body if body else NOT_PROVIDED, ""]


def build_markdown_report(report: Any) -> str:
    """Return a deterministic, redacted Markdown report.

    Always emits the full set of incident sections in a fixed order. Missing
    optional fields render as ``Not provided`` (or an empty list) so the
    function never crashes on a sparse report. Evidence always shows its source
    path and ``line_start-line_end`` range.
    """
    data = build_report_dict(report)

    title = data.get("title") or data.get("incident_id") or "Incident Report"
    lines: list[str] = [f"# {title}", ""]

    # Summary
    _section(lines, "Summary", data.get("summary"))

    # Severity
    _section(lines, "Severity", data.get("severity"))

    # Affected Service
    _section(lines, "Affected Service", data.get("affected_service"))

    # Primary Error
    primary_error = data.get("primary_error")
    _section(
        lines,
        "Primary Error",
        f"`{primary_error}`" if primary_error else None,
    )

    # Root Cause Hypothesis
    root_cause = data.get("root_cause") or {}
    rc_lines: list[str] = []
    if root_cause.get("category"):
        rc_lines.append(f"**Category:** {root_cause['category']}")
    if root_cause.get("summary"):
        rc_lines.append(str(root_cause["summary"]))
    if root_cause.get("alternatives"):
        rc_lines.append("")
        rc_lines.append("**Alternatives:**")
        rc_lines += [f"- {alt}" for alt in root_cause["alternatives"]]
    _section(lines, "Root Cause Hypothesis", "\n".join(rc_lines) if rc_lines else None)

    # Evidence
    evidence = data.get("evidence") or []
    if evidence:
        lines += ["## Evidence", ""]
        lines += [_evidence_line(item) for item in evidence]
        lines.append("")
    else:
        _section(lines, "Evidence", None)

    # Fix Plan
    fix_plan = data.get("fix_plan") or {}
    fp_lines: list[str] = []
    if fix_plan.get("summary"):
        fp_lines.append(str(fix_plan["summary"]))
    steps = fix_plan.get("steps") or []
    if steps:
        fp_lines.append("")
        fp_lines += [f"{i}. {step}" for i, step in enumerate(steps, start=1)]
    _section(lines, "Fix Plan", "\n".join(fp_lines) if fp_lines else None)

    # Regression Test Plan
    regression = fix_plan.get("regression_tests") or []
    if regression:
        body = "\n".join(f"- {test}" for test in regression)
    else:
        body = None
    _section(lines, "Regression Test Plan", body)

    # Safety Review
    safety = data.get("safety_review") or {}
    safety_meta = [
        ("Approved for display", safety.get("approved_for_display")),
        ("Approved for GitHub issue", safety.get("approved_for_github_issue")),
        ("Risk level", safety.get("risk_level")),
        ("Secret scan passed", safety.get("secret_scan_passed")),
        ("Secrets detected", safety.get("secrets_detected")),
        ("Redactions applied", safety.get("redactions_applied")),
        ("Human approval required", safety.get("human_approval_required")),
        ("Required human action", safety.get("required_human_action")),
    ]
    safety_lines = [
        f"- **{label}:** {value}"
        for label, value in safety_meta
        if value is not None
    ]
    if safety.get("summary"):
        safety_lines = [str(safety["summary"]), ""] + safety_lines
    _section(lines, "Safety Review", "\n".join(safety_lines) if safety_lines else None)

    # Confidence
    confidence = data.get("confidence")
    _section(
        lines,
        "Confidence",
        str(confidence) if confidence is not None else None,
    )

    # Human Review Status
    needs_review = data.get("needs_human_review")
    if needs_review is None:
        review_body = None
    else:
        review_body = (
            "Human review required" if needs_review else "No human review required"
        )
    _section(lines, "Human Review Status", review_body)

    # Blocked Reasons
    blocked = data.get("blocked_reasons") or []
    if blocked:
        body = "\n".join(f"- {reason}" for reason in blocked)
    else:
        body = "None"
    _section(lines, "Blocked Reasons", body)

    # Single trailing newline, no trailing whitespace lines. Re-run the safety
    # pass over the assembled document as a final guard.
    return ensure_report_safe("\n".join(lines).rstrip() + "\n")


# Backward-compatible alias for earlier callers/tests.
render_markdown = build_markdown_report


# ---------------------------------------------------------------------------
# Writing to disk
# ---------------------------------------------------------------------------


def _safe_output_path(output_path: Path | str, output_root: Path | str | None) -> Path:
    """Resolve ``output_path``, guarding it under ``output_root`` if given.

    When ``output_root`` is provided, ``path_guard`` blocks ``..`` traversal and
    any path that escapes the allowed reports directory, so an untrusted report
    can never overwrite an unrelated file outside it.
    """
    if output_root is not None:
        return resolve_safe_path(output_root, output_path)
    return Path(output_path)


def write_json_report(
    report: Any,
    output_path: Path | str | None = None,
    *,
    output_root: Path | str | None = None,
) -> str:
    """Return the redacted JSON string, optionally writing it to ``output_path``.

    If ``output_root`` is given, the write path is confined to it via
    ``path_guard``. Returns the JSON text regardless of whether it was written.
    """
    text = render_json(report)
    if output_path is not None:
        target = _safe_output_path(output_path, output_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    return text


def write_markdown_report(
    report: Any,
    output_path: Path | str | None = None,
    *,
    output_root: Path | str | None = None,
) -> str:
    """Return the redacted Markdown string, optionally writing it to disk.

    If ``output_root`` is given, the write path is confined to it via
    ``path_guard``. Returns the Markdown text regardless of whether it was
    written.
    """
    text = build_markdown_report(report)
    if output_path is not None:
        target = _safe_output_path(output_path, output_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return text


def write_report(report: Any, output_dir: Path | str) -> WrittenReport:
    """Write redacted ``report.json`` and ``report.md`` into ``output_dir``.

    Returns the two written paths. Creates ``output_dir`` if needed.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / JSON_FILENAME
    markdown_path = out / MARKDOWN_FILENAME
    write_json_report(report, json_path)
    write_markdown_report(report, markdown_path)

    return {"json_path": str(json_path), "markdown_path": str(markdown_path)}
