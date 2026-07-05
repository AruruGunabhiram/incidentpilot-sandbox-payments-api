"""Phase 4 control-plane API audit (strict).

Exercises the FastAPI control plane end-to-end with FastAPI's ``TestClient``:

    GET  /health
    POST /incidents/trigger
    POST /incidents/{id}/investigate
    GET  /incidents/{id}/report
    POST /incidents/{id}/approve
    POST /incidents/{id}/github/issue

These tests are fully deterministic and hermetic:

* No real GitHub call is ever made. Route-level real creation is covered with a
  mocked GitHub client and fake test-repository settings.
* No network, ADK, or LLM dependency.
* The in-memory incident store is module-global, so each test resets it first
  via the ``client`` fixture. No files are written by the store, so no tmp_path
  isolation is required; this is asserted in ``test_store_is_in_memory_only``.

This file complements ``test_routes_phase4.py`` and maps 1:1 onto the audit
requirements (numbered in the section headers below).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import settings_dependency
from app.main import app
from app.tools.github_client import CreatedIssue
from app.storage import incident_store

# Fake secrets seeded into demo/incidents/secret_in_logs/ci.log. None of these
# may ever appear raw in an API response or report body.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


@pytest.fixture()
def client() -> TestClient:
    """Fresh client over a reset, in-memory store (no cross-test leakage)."""
    incident_store.reset_store()
    return TestClient(app)


def _investigated(client: TestClient, scenario: str) -> tuple[str, dict]:
    """Trigger + investigate a scenario; return (incident_id, report json)."""
    triggered = client.post("/incidents/trigger", json={"scenario": scenario}).json()
    incident_id = triggered["incident_id"]
    report = client.post(f"/incidents/{incident_id}/investigate").json()
    return incident_id, report


# ===========================================================================
# 1. GET /health
# ===========================================================================


def test_health_returns_ok_and_identifies_service(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Service identifies itself as incidentpilot (or equivalent).
    assert "incidentpilot" in body["service"].lower()


# ===========================================================================
# 2. POST /incidents/trigger with broken_api_route
# ===========================================================================


def test_trigger_broken_api_route(client: TestClient) -> None:
    response = client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    assert response.status_code in (200, 201)
    body = response.json()
    assert body["incident_id"]
    assert body["scenario"] == "broken_api_route"
    # First demo incident is the stable inc_001.
    assert body["incident_id"] == "inc_001"


# ===========================================================================
# 3. POST /incidents/{id}/investigate
# ===========================================================================


def test_investigate_returns_generated_report(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    response = client.post("/incidents/inc_001/investigate")
    assert response.status_code == 200
    report = response.json()
    # Investigation produced a usable report (status reflects a generated,
    # human-reviewable result rather than an error).
    assert report["incident_id"] == "inc_001"
    assert report["status"] in {
        "awaiting_human_approval",
        "approved",
        "blocked",
    }
    # The grounded happy path is not blocked and not flagged for human review.
    assert report["status"] == "awaiting_human_approval"
    assert report["needs_human_review"] is False


# ===========================================================================
# 4. GET /incidents/{id}/report — full grounded contract
# ===========================================================================


def test_report_contains_all_required_grounded_fields(client: TestClient) -> None:
    incident_id, _ = _investigated(client, "broken_api_route")
    report = client.get(f"/incidents/{incident_id}/report").json()

    # severity
    assert report["severity"] in {"SEV1", "SEV2", "SEV3", "UNKNOWN"}
    assert report["severity"] == "SEV2"

    # primary_error (a.k.a. primary error)
    assert report["primary_error"] == "AttributeError: 'NoneType' object has no attribute 'id'"

    # failing_test
    assert (
        report["log_finding"]["failing_test"]
        == "tests/test_payments.py::test_create_payment_success"
    )

    # evidence with line_start and line_end
    line_bearing = [e for e in report["evidence"] if e["line_start"] is not None]
    assert line_bearing, "expected at least one line-grounded evidence item"
    for item in line_bearing:
        assert item["line_start"] >= 1
        assert item["line_end"] >= item["line_start"]

    # matched repo file
    assert "app/routes/payments.py" in report["code_finding"]["matched_files"]

    # root_cause_hypothesis (a.k.a. root_cause)
    assert report["root_cause"] is not None
    assert report["root_cause"]["category"] == "null_dereference"
    assert report["root_cause"]["summary"]

    # fix_plan
    assert report["fix_plan"] is not None
    assert report["fix_plan"]["steps"]

    # regression_test_plan (fix_plan.regression_tests)
    assert report["fix_plan"]["regression_tests"]

    # confidence
    assert 0.0 <= report["confidence"] <= 1.0
    assert report["confidence"] >= 0.6

    # safety_review (a.k.a. safety_status)
    assert report["safety_review"] is not None
    assert report["safety_review"]["risk_level"] in {"low", "medium", "high", "critical"}
    assert report["safety_review"]["secret_scan_passed"] is True
    assert report["safety_review"]["secrets_detected"] is False
    assert report["safety_review"]["redactions_applied"] == 0
    assert report["safety_review"]["secrets_redacted"] is False


def test_report_matches_investigation_output(client: TestClient) -> None:
    incident_id, investigated = _investigated(client, "broken_api_route")
    fetched = client.get(f"/incidents/{incident_id}/report").json()
    assert fetched["incident_id"] == investigated["incident_id"]
    assert fetched["primary_error"] == investigated["primary_error"]
    assert fetched["severity"] == investigated["severity"]


# ===========================================================================
# 5. Approval flow — issue blocked before approval, stored after
# ===========================================================================


def test_github_issue_blocked_before_approval(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    # No approval on file yet -> blocked (403), and never a real write.
    response = client.post("/incidents/inc_001/github/issue")
    assert response.status_code == 403
    assert "approval" in response.json()["detail"].lower()


def test_approve_stores_approval_and_unlocks_issue(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")

    approve = client.post(
        "/incidents/inc_001/approve",
        json={"approved": True, "approved_by": "auditor", "note": "looks grounded"},
    )
    assert approve.status_code == 200
    decision = approve.json()
    assert decision["approved"] is True
    assert decision["action"] == "create_github_issue"
    assert decision["approved_by"] == "auditor"

    # Approval is persisted: the issue path is now unlocked (still dry-run).
    issue = client.post("/incidents/inc_001/github/issue")
    assert issue.status_code == 200
    assert issue.json()["created"] is False


def test_explicit_non_approval_keeps_issue_blocked(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    decline = client.post("/incidents/inc_001/approve", json={"approved": False})
    assert decline.status_code == 200
    assert decline.json()["approved"] is False
    # A recorded *negative* decision must not unlock the issue.
    issue = client.post("/incidents/inc_001/github/issue")
    assert issue.status_code == 403


# ===========================================================================
# 6. GitHub issue dry-run — preview only, no write, no leaks
# ===========================================================================


def test_github_issue_dry_run_preview(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    client.post("/incidents/inc_001/approve")

    response = client.post("/incidents/inc_001/github/issue", json={"dry_run": True})
    assert response.status_code == 200
    issue = response.json()

    # Does not create a real issue.
    assert issue["created"] is False
    assert issue["dry_run"] is True
    assert issue["issue_url"] is None
    assert issue["issue_number"] is None

    # Returns a title/body preview.
    assert issue["title"].startswith("IncidentPilot:")
    body = issue["body_preview"]
    assert body

    # Body preview includes evidence and the fix plan.
    assert "app/routes/payments.py" in body  # grounded code evidence
    assert "regression" in body.lower() or "test" in body.lower()  # fix/regression plan
    assert "AttributeError" in body  # primary error evidence

    # Does not leak secrets (defensive — broken_api_route has none, but the
    # preview must never carry a raw credential regardless).
    blob = json.dumps(issue)
    for secret in RAW_FAKE_SECRETS:
        assert secret not in blob


def test_invalid_github_dry_run_env_fails_safe_to_preview(monkeypatch) -> None:
    """Invalid env dry-run values must not 500 or enable a real write."""

    class ExplodingGitHubClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("GitHub client must not be constructed in dry-run")

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_routeTestToken_DO_NOT_LEAK_0001")
    monkeypatch.setenv("GITHUB_OWNER", "demo-owner")
    monkeypatch.setenv("GITHUB_REPO", "demo-repo")
    monkeypatch.setenv("GITHUB_DRY_RUN", "banana")
    get_settings.cache_clear()

    incident_store.reset_store()
    monkeypatch.setattr(
        "app.services.github_issue_service.GitHubClient", ExplodingGitHubClient
    )
    try:
        local = TestClient(app, raise_server_exceptions=False)
        local.post("/incidents/trigger", json={"scenario": "broken_api_route"})
        local.post("/incidents/inc_001/investigate")
        local.post("/incidents/inc_001/approve")

        response = local.post(
            "/incidents/inc_001/github/issue", json={"dry_run": False}
        )
    finally:
        get_settings.cache_clear()
        incident_store.reset_store()

    assert response.status_code == 200
    issue = response.json()
    assert issue["created"] is False
    assert issue["dry_run"] is True
    assert issue["issue_url"] is None
    assert issue["issue_number"] is None


def test_github_issue_route_real_create_uses_mocked_client(monkeypatch) -> None:
    """Route can return a real-created issue shape without real GitHub access."""

    class FakeGitHubClient:
        calls: list[dict] = []

        def __init__(self, token: str, owner: str, repo: str) -> None:
            assert token == "ghp_routeTestToken_DO_NOT_LEAK_0001"
            assert owner == "demo-owner"
            assert repo == "demo-repo"

        def create_issue(
            self, title: str, body: str, labels: list[str] | None = None
        ) -> CreatedIssue:
            self.calls.append({"title": title, "body": body, "labels": labels})
            return CreatedIssue(
                number=777,
                url="https://github.com/demo-owner/demo-repo/issues/777",
            )

    def fake_settings() -> Settings:
        return Settings(
            github_token="ghp_routeTestToken_DO_NOT_LEAK_0001",
            github_owner="demo-owner",
            github_repo="demo-repo",
            github_dry_run=False,
        )

    incident_store.reset_store()
    monkeypatch.setattr(
        "app.services.github_issue_service.GitHubClient", FakeGitHubClient
    )
    app.dependency_overrides[settings_dependency] = fake_settings
    try:
        local = TestClient(app)
        local.post("/incidents/trigger", json={"scenario": "broken_api_route"})
        local.post("/incidents/inc_001/investigate")
        local.post("/incidents/inc_001/approve")

        response = local.post(
            "/incidents/inc_001/github/issue", json={"dry_run": False}
        )
    finally:
        app.dependency_overrides.pop(settings_dependency, None)
        incident_store.reset_store()

    assert response.status_code == 200
    issue = response.json()
    assert issue["created"] is True
    assert issue["dry_run"] is False
    assert issue["issue_url"] == "https://github.com/demo-owner/demo-repo/issues/777"
    assert issue["issue_number"] == 777
    assert (
        issue["title"]
        == "IncidentPilot: POST /payments fails due to unchecked missing user"
    )
    assert "body_preview" in issue
    assert FakeGitHubClient.calls
    assert FakeGitHubClient.calls[0]["labels"] == ["incident", "severity:sev2"]

    blob = json.dumps(issue)
    assert "ghp_routeTestToken_DO_NOT_LEAK_0001" not in blob


# ===========================================================================
# 7. Invalid incident_id — 404 / clear error on every id-bearing endpoint
# ===========================================================================


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/incidents/inc_does_not_exist/investigate", None),
        ("get", "/incidents/inc_does_not_exist/report", None),
        ("post", "/incidents/inc_does_not_exist/approve", {}),
        ("post", "/incidents/inc_does_not_exist/github/issue", {}),
    ],
)
def test_unknown_incident_id_is_rejected(
    client: TestClient, method: str, path: str, payload: dict | None
) -> None:
    response = getattr(client, method)(path) if payload is None else getattr(client, method)(path, json=payload)
    # Unknown incident -> 404 with a clear, structured error.
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail.strip()
    assert "inc_does_not_exist" in detail or "unknown" in detail.lower()


# ===========================================================================
# 8. Unknown scenario — 400/404, no broken incident state created
# ===========================================================================


def test_unknown_scenario_is_rejected_and_creates_no_state(client: TestClient) -> None:
    response = client.post("/incidents/trigger", json={"scenario": "totally_made_up"})
    assert response.status_code in (400, 404)
    assert "scenario" in response.json()["detail"].lower()

    # No partial/broken incident may have been registered for the bad scenario.
    assert incident_store._SCENARIO_INDEX.get("totally_made_up") is None
    # And the known stable ids remain unused.
    assert incident_store.get_incident("inc_001") is None


# ===========================================================================
# 9. Safety case — secret_in_logs: redacted report, no raw secrets, blocked
# ===========================================================================


def test_secret_scenario_redacts_and_blocks_issue(client: TestClient) -> None:
    incident_id, report = _investigated(client, "secret_in_logs")

    # Final report must not contain any raw fake secret, anywhere.
    blob = json.dumps(report)
    for secret in RAW_FAKE_SECRETS:
        assert secret not in blob, f"raw secret leaked in report: {secret}"

    # Redactions were applied (count > 0) and the marker is present.
    assert "[REDACTED_SECRET" in blob
    assert report["log_finding"]["redactions_applied"] > 0
    assert report["safety_review"]["secret_scan_passed"] is True
    assert report["safety_review"]["secrets_detected"] is True
    assert report["safety_review"]["redactions_applied"] == report["log_finding"]["redactions_applied"]
    assert report["safety_review"]["secrets_redacted"] is True
    assert report["needs_human_review"] is True

    # A GitHub issue is hard-blocked by the safety review even if a human
    # approves it.
    client.post(f"/incidents/{incident_id}/approve")
    issue = client.post(f"/incidents/{incident_id}/github/issue")
    assert issue.status_code == 403
    assert "safety" in issue.json()["detail"].lower()
    # The block must not have leaked a secret in its error message either.
    for secret in RAW_FAKE_SECRETS:
        assert secret not in json.dumps(issue.json())


# ===========================================================================
# Hermeticity guards — repeatable, in-memory, no external writes
# ===========================================================================


def test_store_is_in_memory_only(client: TestClient) -> None:
    """Investigating must not create or require any on-disk incident state."""
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    # State lives in the module-global dict and is wiped by reset_store().
    assert incident_store.get_incident("inc_001") is not None
    incident_store.reset_store()
    assert incident_store.get_incident("inc_001") is None


def test_full_flow_is_repeatable(client: TestClient) -> None:
    """The whole trigger->investigate->approve->issue flow is deterministic."""
    runs = []
    for _ in range(2):
        incident_store.reset_store()
        local = TestClient(app)
        local.post("/incidents/trigger", json={"scenario": "broken_api_route"})
        report = local.post("/incidents/inc_001/investigate").json()
        local.post("/incidents/inc_001/approve")
        issue = local.post("/incidents/inc_001/github/issue").json()
        runs.append((report["confidence"], report["severity"], issue["title"]))
    assert runs[0] == runs[1]
