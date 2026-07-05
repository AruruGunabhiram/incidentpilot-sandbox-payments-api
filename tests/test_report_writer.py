import json
from pathlib import Path

from app.tools.redactor import REDACTION_MARKER
import pytest

from app.tools.path_guard import PathGuardError
from app.tools.report_writer import (
    build_markdown_report,
    build_report_dict,
    ensure_report_safe,
    render_json,
    render_markdown,
    write_json_report,
    write_markdown_report,
    write_report,
)

EXPECTED_REPORT = Path("demo/incidents/broken_api_route/expected_report.json")

# A report dict carrying a planted secret in an evidence snippet, to prove the
# writer scrubs untrusted text even if it slips through upstream.
REPORT_WITH_SECRET = {
    "incident_id": "inc_test",
    "title": "Test incident",
    "severity": "SEV2",
    "affected_service": "payments-api",
    "status": "awaiting_human_approval",
    "summary": "Deploy logged api_key=fake-api-key-12345 by mistake.",
    "confidence": 0.5,
    "needs_human_review": True,
    "blocked_reasons": [],
    "primary_error": "ConnectionError: billing upstream timeout",
    "evidence": [
        {
            "id": "ev1",
            "source": "demo/incidents/secret_in_logs/ci.log",
            "source_type": "ci_log",
            "summary": "Deploy log line with a token.",
            "snippet": "Using GitHub token ghp_fakeTokenForDemoOnly1234567890",
            "path": "demo/incidents/secret_in_logs/ci.log",
            "line_start": 3,
            "line_end": 3,
            "metadata": {},
        }
    ],
}


# ---- happy paths -----------------------------------------------------------


def test_render_json_is_valid_and_deterministic():
    first = render_json(REPORT_WITH_SECRET)
    second = render_json(REPORT_WITH_SECRET)

    assert first == second  # deterministic
    parsed = json.loads(first)
    assert parsed["incident_id"] == "inc_test"


def test_render_markdown_has_sections():
    md = render_markdown(REPORT_WITH_SECRET)

    assert md.startswith("# Test incident")
    assert "## Summary" in md
    assert "## Evidence" in md
    assert "demo/incidents/secret_in_logs/ci.log:3" in md


def test_write_report_creates_json_and_markdown(tmp_path):
    paths = write_report(REPORT_WITH_SECRET, tmp_path)

    json_path = Path(paths["json_path"])
    md_path = Path(paths["markdown_path"])
    assert json_path.is_file() and md_path.is_file()
    # JSON on disk parses.
    json.loads(json_path.read_text(encoding="utf-8"))


# ---- safety path: no raw secrets ------------------------------------------


def test_no_raw_secrets_in_any_output():
    raw_secrets = [
        "ghp_fakeTokenForDemoOnly1234567890",
        "fake-api-key-12345",
    ]
    json_out = render_json(REPORT_WITH_SECRET)
    md_out = render_markdown(REPORT_WITH_SECRET)

    for secret in raw_secrets:
        assert secret not in json_out
        assert secret not in md_out
    assert REDACTION_MARKER in json_out
    assert REDACTION_MARKER in md_out


def test_real_expected_report_renders_without_leaking_secrets():
    data = json.loads(EXPECTED_REPORT.read_text(encoding="utf-8"))
    redacted = build_report_dict(data)

    # Round-trips and stays JSON-safe.
    json.dumps(redacted)
    md = render_markdown(data)
    assert md.startswith("# ")
    # This clean report needs no redaction, so none should appear.
    assert REDACTION_MARKER not in md


# ---- new spec API: write_*/build_markdown_report/ensure_report_safe --------


def test_markdown_includes_summary_evidence_confidence():
    md = build_markdown_report(REPORT_WITH_SECRET)

    assert "## Summary" in md
    assert "## Evidence" in md
    assert "## Confidence" in md
    # Evidence shows source path + line range.
    assert "demo/incidents/secret_in_logs/ci.log:3" in md


def test_write_json_report_returns_valid_json():
    out = write_json_report(REPORT_WITH_SECRET)
    parsed = json.loads(out)  # valid JSON
    assert parsed["incident_id"] == "inc_test"
    # Pretty-printed with indent=2.
    assert "\n  " in out


def test_secrets_redacted_in_markdown():
    md = write_markdown_report(REPORT_WITH_SECRET)
    assert "ghp_fakeTokenForDemoOnly1234567890" not in md
    assert "fake-api-key-12345" not in md
    assert REDACTION_MARKER in md


def test_secrets_redacted_in_json():
    out = write_json_report(REPORT_WITH_SECRET)
    assert "ghp_fakeTokenForDemoOnly1234567890" not in out
    assert "fake-api-key-12345" not in out
    assert REDACTION_MARKER in out


def test_missing_optional_fields_do_not_crash():
    sparse = {"incident_id": "inc_min", "title": "Minimal"}

    md = build_markdown_report(sparse)
    js = write_json_report(sparse)

    # Every mandated section renders, with "Not provided" fallbacks.
    for heading in (
        "## Summary",
        "## Severity",
        "## Affected Service",
        "## Primary Error",
        "## Root Cause Hypothesis",
        "## Evidence",
        "## Fix Plan",
        "## Regression Test Plan",
        "## Safety Review",
        "## Confidence",
        "## Human Review Status",
        "## Blocked Reasons",
    ):
        assert heading in md
    assert "Not provided" in md
    assert json.loads(js)["incident_id"] == "inc_min"


def test_ensure_report_safe_redacts_text():
    redacted = ensure_report_safe("token api_key=fake-secret-value-123 here")
    assert "fake-secret-value-123" not in redacted
    assert REDACTION_MARKER in redacted


def test_write_to_disk_with_output_root_blocks_traversal(tmp_path):
    # Writing inside the allowed root works.
    safe_target = tmp_path / "report.json"
    write_json_report(REPORT_WITH_SECRET, safe_target, output_root=tmp_path)
    assert safe_target.is_file()
    json.loads(safe_target.read_text(encoding="utf-8"))

    # Escaping the allowed root is blocked.
    with pytest.raises(PathGuardError):
        write_json_report(
            REPORT_WITH_SECRET, "../escape.json", output_root=tmp_path
        )
