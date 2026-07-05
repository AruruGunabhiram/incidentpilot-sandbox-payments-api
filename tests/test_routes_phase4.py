"""Phase 4 control-plane API tests.

Exercise the deterministic FastAPI routes end-to-end with FastAPI's TestClient:
trigger -> investigate -> report -> approve -> github/issue, plus the negative
and safety-gated paths. No agents, no LLM, no network, no real GitHub writes.

The in-memory store is module-global, so every test resets it first.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import incident_store

# Fake secrets seeded into demo/incidents/secret_in_logs; none may ever appear
# raw in an API response.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


@pytest.fixture()
def client() -> TestClient:
    incident_store.reset_store()
    return TestClient(app)


# --- health -----------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "incidentpilot"}


# --- trigger ----------------------------------------------------------------


def test_trigger_assigns_stable_inc_001(client: TestClient) -> None:
    response = client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    assert response.status_code == 200
    body = response.json()
    assert body == {"incident_id": "inc_001", "status": "created", "scenario": "broken_api_route"}


def test_trigger_is_idempotent_per_scenario(client: TestClient) -> None:
    first = client.post("/incidents/trigger", json={"scenario": "broken_api_route"}).json()
    second = client.post("/incidents/trigger", json={"scenario": "broken_api_route"}).json()
    assert first["incident_id"] == second["incident_id"] == "inc_001"


def test_trigger_unknown_scenario_404(client: TestClient) -> None:
    response = client.post("/incidents/trigger", json={"scenario": "does_not_exist"})
    assert response.status_code == 404
    assert "scenario" in response.json()["detail"].lower()


# --- report / investigate ordering ------------------------------------------


def test_report_before_investigate_is_400(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    response = client.get("/incidents/inc_001/report")
    assert response.status_code == 400


def test_investigate_unknown_incident_404(client: TestClient) -> None:
    response = client.post("/incidents/inc_999/investigate")
    assert response.status_code == 404


# --- broken_api_route: the happy, grounded path -----------------------------


def test_investigate_broken_api_route_is_grounded(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    report = client.post("/incidents/inc_001/investigate").json()

    # Required report fields are present and correct.
    assert report["severity"] == "SEV2"
    assert report["primary_error"] == "AttributeError: 'NoneType' object has no attribute 'id'"
    assert report["log_finding"]["failing_test"] == "tests/test_payments.py::test_create_payment_success"
    assert "app/routes/payments.py" in report["code_finding"]["matched_files"]
    assert report["root_cause"]["category"] == "null_dereference"
    assert report["fix_plan"]["regression_tests"]  # regression test plan present
    assert report["needs_human_review"] is False
    assert report["confidence"] >= 0.6
    assert report["safety_review"]["secret_scan_passed"] is True
    assert report["safety_review"]["secrets_detected"] is False
    assert report["safety_review"]["redactions_applied"] == 0
    assert report["safety_review"]["secrets_redacted"] is False

    # Every cited evidence item carries a real, positive line range (except the
    # optional API-response item, which has no single line).
    line_bearing = [e for e in report["evidence"] if e["line_start"] is not None]
    assert line_bearing, "expected line-grounded evidence"
    for item in line_bearing:
        assert item["line_start"] >= 1
        assert item["line_end"] >= item["line_start"]


def test_report_matches_investigation(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    investigated = client.post("/incidents/inc_001/investigate").json()
    fetched = client.get("/incidents/inc_001/report").json()
    assert fetched["incident_id"] == investigated["incident_id"] == "inc_001"
    assert fetched["primary_error"] == investigated["primary_error"]


# --- approval + github issue gating -----------------------------------------


def test_github_issue_requires_report(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    response = client.post("/incidents/inc_001/github/issue")
    assert response.status_code == 400


def test_github_issue_requires_approval(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    response = client.post("/incidents/inc_001/github/issue")
    assert response.status_code == 403
    assert "approval" in response.json()["detail"].lower()


def test_github_issue_dry_run_preview_after_approval(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    approve = client.post("/incidents/inc_001/approve")
    assert approve.status_code == 200
    assert approve.json()["approved"] is True

    issue = client.post("/incidents/inc_001/github/issue").json()
    assert issue["created"] is False
    assert issue["dry_run"] is True
    assert issue["title"].startswith("IncidentPilot:")
    assert issue["body_preview"]  # redacted markdown preview
    assert issue["issue_url"] is None
    assert issue["issue_number"] is None


# --- secret_in_logs: redaction + hard safety block --------------------------


def test_secret_scenario_redacts_and_blocks_issue(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "secret_in_logs"})
    report = client.post("/incidents/inc_002/investigate").json()

    # No raw secret survives anywhere in the serialized report.
    blob = json.dumps(report)
    for secret in RAW_FAKE_SECRETS:
        assert secret not in blob, f"raw secret leaked: {secret}"
    assert "[REDACTED_SECRET" in blob
    assert report["safety_review"]["secret_scan_passed"] is True
    assert report["safety_review"]["secrets_detected"] is True
    assert report["safety_review"]["redactions_applied"] == report["log_finding"]["redactions_applied"]
    assert report["safety_review"]["secrets_redacted"] is True
    assert report["needs_human_review"] is True

    # Even after approval, the safety review blocks a GitHub issue.
    client.post("/incidents/inc_002/approve")
    issue = client.post("/incidents/inc_002/github/issue")
    assert issue.status_code == 403
    assert "safety" in issue.json()["detail"].lower()


# --- ambiguous_error: low confidence, blocked -------------------------------


def test_ambiguous_scenario_is_low_confidence_and_blocked(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "ambiguous_error"})
    report = client.post("/incidents/inc_003/investigate").json()

    assert report["severity"] == "UNKNOWN"
    assert report["status"] == "blocked"
    assert report["confidence"] < 0.6
    assert report["needs_human_review"] is True
    assert report["fix_plan"] is None  # no fix without a grounded root cause
    assert report["blocked_reasons"]

    client.post("/incidents/inc_003/approve")
    issue = client.post("/incidents/inc_003/github/issue")
    assert issue.status_code == 403


# --- swagger ----------------------------------------------------------------


def test_all_endpoints_in_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/health",
        "/incidents/trigger",
        "/incidents/{incident_id}/investigate",
        "/incidents/{incident_id}/report",
        "/incidents/{incident_id}/approve",
        "/incidents/{incident_id}/github/issue",
    ):
        assert path in paths, f"missing from Swagger: {path}"
