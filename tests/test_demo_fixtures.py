"""Phase 2 demo fixture sanity tests.

These verify the demo incident fixtures exist, parse, and carry the expected
evidence for each scenario. They do not exercise agents, the API, or GitHub.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INCIDENTS = REPO_ROOT / "demo" / "incidents"
DEMO_REPO = REPO_ROOT / "demo" / "demo_repo"

REQUIRED_FILES = {
    "broken_api_route": ["intake.json", "ci.log", "api_response.json", "expected_report.json"],
    "secret_in_logs": ["intake.json", "ci.log", "expected_report.json"],
    "ambiguous_error": ["intake.json", "ci.log", "expected_report.json"],
}

RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


def _read(scenario: str, filename: str) -> str:
    return (INCIDENTS / scenario / filename).read_text(encoding="utf-8")


# 1. All required fixture directories exist.
@pytest.mark.parametrize("scenario", sorted(REQUIRED_FILES))
def test_scenario_directory_exists(scenario: str) -> None:
    assert (INCIDENTS / scenario).is_dir()


# 2. Each required file exists.
@pytest.mark.parametrize(
    ("scenario", "filename"),
    [(s, f) for s, files in REQUIRED_FILES.items() for f in files],
)
def test_required_file_exists(scenario: str, filename: str) -> None:
    assert (INCIDENTS / scenario / filename).is_file()


# 3. Each JSON file parses.
@pytest.mark.parametrize(
    ("scenario", "filename"),
    [(s, f) for s, files in REQUIRED_FILES.items() for f in files if f.endswith(".json")],
)
def test_json_files_parse(scenario: str, filename: str) -> None:
    json.loads(_read(scenario, filename))


# 4. demo/demo_repo/app/routes/payments.py exists.
def test_demo_repo_payments_module_exists() -> None:
    assert (DEMO_REPO / "app" / "routes" / "payments.py").is_file()


# 5. broken_api_route ci.log contains the AttributeError.
def test_broken_api_route_ci_log_has_attribute_error() -> None:
    assert "AttributeError: 'NoneType' object has no attribute 'id'" in _read(
        "broken_api_route", "ci.log"
    )


# 6. broken_api_route expected_report mentions the missing None check and the file.
def test_broken_api_route_report_mentions_root_cause_and_file() -> None:
    report = _read("broken_api_route", "expected_report.json")
    assert "missing none check" in report.lower()
    assert "app/routes/payments.py" in report


# 7. secret_in_logs ci.log contains fake secret-like values.
def test_secret_in_logs_ci_log_has_fake_secrets() -> None:
    ci_log = _read("secret_in_logs", "ci.log")
    for secret in RAW_FAKE_SECRETS:
        assert secret in ci_log, f"expected fake secret in ci.log: {secret}"


# 8. secret_in_logs expected_report does NOT expose raw fake secret values.
def test_secret_in_logs_report_has_no_raw_secrets() -> None:
    report = _read("secret_in_logs", "expected_report.json")
    leaked = [secret for secret in RAW_FAKE_SECRETS if secret in report]
    assert leaked == [], f"raw fake secrets exposed in report: {leaked}"
    assert "[REDACTED_SECRET]" in report


# 9. ambiguous_error expected_report is low-confidence and needs human review.
def test_ambiguous_error_report_is_low_confidence() -> None:
    report = json.loads(_read("ambiguous_error", "expected_report.json"))
    assert report["confidence"] < 0.5
    assert report["needs_human_review"] is True
    assert report["blocked_reasons"]
