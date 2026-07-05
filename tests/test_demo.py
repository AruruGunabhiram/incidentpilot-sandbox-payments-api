"""Tests for ``make demo`` / ``scripts/run_demo.py``.

Covers the Phase 5 audit requirements: the demo runs from a clean repo state,
produces a schema-valid JSON report plus a readable Markdown report, never
leaks a raw fake secret to the console or to disk, and needs no API keys and no
internet. Fine-grained behavior is exercised by calling ``run_demo`` directly
with an isolated ``reports_dir``; the true ``make demo`` path (a bare
subprocess, default output location) is exercised end-to-end with the
canonical ``app/storage/reports/`` location.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.schemas.report import IncidentReport
from scripts.run_demo import DEFAULT_SCENARIO, build_summary, run_demo

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_demo.py"
CANONICAL_REPORTS = REPO_ROOT / "app" / "storage" / "reports"

# The fake secrets seeded into demo/incidents/secret_in_logs. None may ever
# appear raw on the console or in a saved report.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


def _env_without_secrets_or_network() -> dict[str, str]:
    """A subprocess env with API keys stripped and proxies pointed at a dead port.

    Any accidental outbound HTTP would fail fast against 127.0.0.1:9, so a clean
    exit proves the demo path makes no network calls.
    """
    env = dict(os.environ)
    for key in ("GEMINI_API_KEY", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    dead = "http://127.0.0.1:9"
    env.update(
        http_proxy=dead, https_proxy=dead, HTTP_PROXY=dead, HTTPS_PROXY=dead, NO_PROXY=""
    )
    return env


# ---- direct run_demo: isolated reports_dir ---------------------------------


def test_run_demo_writes_reports_from_clean_state(tmp_path):
    rc = run_demo(DEFAULT_SCENARIO, reports_dir=tmp_path, stream=io.StringIO())
    assert rc == 0
    assert (tmp_path / "inc_001.json").is_file()
    assert (tmp_path / "inc_001.md").is_file()


def test_demo_report_json_validates_against_schema(tmp_path):
    run_demo(DEFAULT_SCENARIO, reports_dir=tmp_path, stream=io.StringIO())
    text = (tmp_path / "inc_001.json").read_text(encoding="utf-8")

    # Strict schema validation (JSON mode handles the ISO datetimes).
    report = IncidentReport.model_validate_json(text)
    assert report.incident_id == "inc_001"
    assert report.severity == "SEV2"
    assert report.root_cause is not None
    # Confidence is evidence-based and above the automation threshold here.
    assert report.confidence >= 0.6
    assert report.evidence, "expected grounded evidence"


def test_demo_markdown_exists_and_is_readable(tmp_path):
    run_demo(DEFAULT_SCENARIO, reports_dir=tmp_path, stream=io.StringIO())
    md = (tmp_path / "inc_001.md").read_text(encoding="utf-8")
    assert md.startswith("# ")
    assert "## Summary" in md
    assert "## Evidence" in md


def test_demo_summary_has_all_required_fields(tmp_path):
    buf = io.StringIO()
    run_demo(DEFAULT_SCENARIO, reports_dir=tmp_path, stream=buf)
    out = buf.getvalue()

    for label in (
        "incident_id",
        "scenario",
        "severity",
        "primary_error",
        "root cause summary",
        "confidence",
        "needs_human_review",
        "JSON report path",
        "Markdown report path",
    ):
        assert label in out, f"summary missing field: {label}"
    assert "inc_001" in out
    assert "broken_api_route" in out


def test_demo_returns_nonzero_on_unknown_scenario(tmp_path):
    rc = run_demo("does_not_exist", reports_dir=tmp_path, stream=io.StringIO())
    assert rc != 0
    assert not list(tmp_path.glob("*.json"))


def test_demo_no_raw_secret_on_console_or_disk(tmp_path):
    # The secret scenario must never surface a raw secret, even on the console.
    buf = io.StringIO()
    rc = run_demo("secret_in_logs", reports_dir=tmp_path, stream=buf)
    assert rc == 0

    console = buf.getvalue()
    blob = (tmp_path / "inc_002.json").read_text(encoding="utf-8")
    for secret in RAW_FAKE_SECRETS:
        assert secret not in console, f"raw secret on console: {secret}"
        assert secret not in blob, f"raw secret on disk: {secret}"


def test_build_summary_redacts_planted_secret(tmp_path):
    # Even if a secret reached a report field, the console summary is re-redacted.
    report = IncidentReport.model_validate(
        {
            "incident_id": "inc_001",
            "title": "demo",
            "severity": "SEV2",
            "affected_service": "payments-api",
            "status": "awaiting_human_approval",
            "primary_error": "Leaked ghp_fakeTokenForDemoOnly1234567890",
        }
    )
    summary = build_summary(report, "broken_api_route", tmp_path / "x.json", tmp_path / "x.md")
    assert "ghp_fakeTokenForDemoOnly1234567890" not in summary
    assert "REDACTED_SECRET" in summary


# ---- end-to-end: the real `make demo` path (subprocess, default location) ---


def test_make_demo_subprocess_from_clean_state():
    # Simulate a clean repo state for the canonical artifacts. Best-effort: some
    # CI/sandbox mounts forbid unlink, and run_demo overwrites these anyway, so a
    # delete failure must not fail the test.
    for name in ("inc_001.json", "inc_001.md"):
        try:
            (CANONICAL_REPORTS / name).unlink(missing_ok=True)
        except OSError:
            pass

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert (CANONICAL_REPORTS / "inc_001.json").is_file()
    assert (CANONICAL_REPORTS / "inc_001.md").is_file()
    # The saved JSON validates against the schema.
    IncidentReport.model_validate_json(
        (CANONICAL_REPORTS / "inc_001.json").read_text(encoding="utf-8")
    )
    assert "inc_001" in result.stdout


def test_make_demo_needs_no_api_keys_or_internet(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--reports-dir", str(tmp_path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env=_env_without_secrets_or_network(),
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "inc_001.json").is_file()
    assert (tmp_path / "inc_001.md").is_file()
