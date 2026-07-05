"""Phase 8: human approval gate before GitHub issue creation.

Covers the nine non-negotiable rules end-to-end (FastAPI TestClient) and at the
service layer (so the gate cannot be bypassed by avoiding the API route):

1. No approval  -> no GitHub issue.
2. Rejected     -> no GitHub issue.
3. Failed safety review -> no GitHub issue (even when approved).
4. Low confidence       -> no GitHub issue (even when approved).
5. Dry-run     -> preview only, never a real write.
6. Approval is action-specific.
7. Unknown incident id -> 404-style error.
8. Invalid action value -> rejected.
9. The gate is enforced in the service layer, not only the API route.

No agents, no LLM, no network, no real GitHub writes.
"""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.approval import GitHubIssueOptions
from app.services import investigation_service as svc
from app.services.approval_service import ApprovalService, approval_service
from app.services.errors import (
    ApprovalRejected,
    ApprovalRequired,
    IncidentNotFound,
    InvalidAction,
    ReportNotReady,
    SafetyBlocked,
)
from app.storage import incident_store


@pytest.fixture()
def client() -> TestClient:
    incident_store.reset_store()
    return TestClient(app)


@pytest.fixture()
def store_reset():
    incident_store.reset_store()
    yield
    incident_store.reset_store()


# --- Rule 1: no approval means no GitHub issue ------------------------------


def test_no_approval_blocks_issue_with_reason(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")

    response = client.post("/incidents/inc_001/github/issue")
    assert response.status_code == 403
    body = response.json()
    assert body["reason"] == "approval_required"
    assert "approval" in body["detail"].lower()


# --- Rule 2: rejected approval means no GitHub issue ------------------------


def test_rejected_approval_blocks_issue(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")

    rejected = client.post(
        "/incidents/inc_001/approve",
        json={"action": "create_github_issue", "approved": False, "approved_by": "demo_user"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["approved"] is False

    response = client.post("/incidents/inc_001/github/issue")
    assert response.status_code == 403
    assert response.json()["reason"] == "approval_rejected"


# --- Rule 3: failed safety review means no GitHub issue (even approved) -----


def test_failed_safety_review_blocks_even_after_approval(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "secret_in_logs"})
    client.post("/incidents/inc_002/investigate")
    approve = client.post("/incidents/inc_002/approve")
    assert approve.status_code == 200

    response = client.post("/incidents/inc_002/github/issue")
    assert response.status_code == 403
    assert response.json()["reason"] == "safety_review_failed"


# --- Rule 4: low confidence means no GitHub issue (even approved) -----------


def test_low_confidence_blocks_even_after_approval(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "ambiguous_error"})
    report = client.post("/incidents/inc_003/investigate").json()
    assert report["confidence"] < 0.6

    client.post("/incidents/inc_003/approve")
    response = client.post("/incidents/inc_003/github/issue")
    assert response.status_code == 403
    assert response.json()["reason"] == "safety_review_failed"


# --- Rule 5: dry-run returns preview only, never a real write --------------


def test_dry_run_returns_preview_only(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    client.post("/incidents/inc_001/approve")

    issue = client.post("/incidents/inc_001/github/issue").json()
    assert issue["created"] is False
    assert issue["dry_run"] is True
    assert issue["title"].startswith("IncidentPilot:")
    assert issue["body_preview"]
    assert issue["issue_url"] is None
    assert issue["issue_number"] is None


# --- Rule 6: approval is action-specific -----------------------------------


def test_approval_is_action_specific(store_reset) -> None:
    svc.investigate_incident("broken_api_route", persist=False)
    approval_service.approve_action("inc_001", "create_github_issue", approved_by="demo_user")

    # The approved action reports approved; an unrelated action stays pending.
    assert approval_service.get_approval_status("inc_001", "create_github_issue") == "approved"
    assert approval_service.get_approval_status("inc_001", "create_pr") == "pending"

    # require_approved for the un-approved action still blocks.
    approval_service.require_approved("inc_001", "create_github_issue")  # no raise
    with pytest.raises(ApprovalRequired):
        approval_service.require_approved("inc_001", "create_pr")


# --- Rule 7: unknown incident id -> 404-style error ------------------------


def test_unknown_incident_approve_is_404(client: TestClient) -> None:
    response = client.post("/incidents/inc_404/approve")
    assert response.status_code == 404


def test_unknown_incident_issue_is_404(client: TestClient) -> None:
    response = client.post("/incidents/inc_404/github/issue")
    assert response.status_code == 404


# --- Rule 8: invalid action value is rejected ------------------------------


def test_invalid_action_rejected_by_api(client: TestClient) -> None:
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")
    response = client.post(
        "/incidents/inc_001/approve",
        json={"action": "delete_everything", "approved_by": "demo_user"},
    )
    assert response.status_code == 422


def test_invalid_action_rejected_by_service(store_reset) -> None:
    svc.investigate_incident("broken_api_route", persist=False)
    with pytest.raises(InvalidAction):
        approval_service.approve_action("inc_001", "delete_everything")
    with pytest.raises(InvalidAction):
        approval_service.require_approved("inc_001", "delete_everything")


# --- Rule 9: the gate is enforced in the service layer ---------------------


def test_service_layer_blocks_without_approval(store_reset) -> None:
    """Calling the service directly (bypassing the route) still blocks."""
    svc.investigate_incident("broken_api_route", persist=False)
    with pytest.raises(ApprovalRequired):
        svc.create_github_issue(
            "inc_001", GitHubIssueOptions(), github_configured=True, env_dry_run=False
        )


def test_service_layer_blocks_after_rejection(store_reset) -> None:
    svc.investigate_incident("broken_api_route", persist=False)
    approval_service.reject_action("inc_001", "create_github_issue", approved_by="demo_user")
    with pytest.raises(ApprovalRejected):
        svc.create_github_issue(
            "inc_001", GitHubIssueOptions(), github_configured=True, env_dry_run=False
        )


def test_service_layer_safety_beats_approval(store_reset) -> None:
    """Safety is checked before approval: an approved unsafe report still blocks."""
    svc.investigate_incident("secret_in_logs", persist=False)
    approval_service.approve_action("inc_002", "create_github_issue", approved_by="demo_user")
    with pytest.raises(SafetyBlocked):
        svc.create_github_issue(
            "inc_002", GitHubIssueOptions(), github_configured=True, env_dry_run=False
        )


# --- ApprovalService state machine + guards --------------------------------


def test_default_status_is_pending(store_reset) -> None:
    svc.investigate_incident("broken_api_route", persist=False)
    assert approval_service.get_approval_status("inc_001") == "pending"
    assert approval_service.get_record("inc_001") is None


def test_approve_requires_investigated_incident(store_reset) -> None:
    svc.create_incident("broken_api_route")  # registered but not investigated
    with pytest.raises(ReportNotReady):
        approval_service.approve_action("inc_001", "create_github_issue")


def test_approve_unknown_incident_raises(store_reset) -> None:
    with pytest.raises(IncidentNotFound):
        approval_service.approve_action("inc_404", "create_github_issue")


def test_reject_then_reapprove_unlocks(store_reset) -> None:
    svc.investigate_incident("broken_api_route", persist=False)
    fake_store_service = ApprovalService()  # uses shared store; just exercising API
    fake_store_service.reject_action("inc_001", "create_github_issue")
    assert fake_store_service.get_approval_status("inc_001") == "rejected"

    fake_store_service.approve_action("inc_001", "create_github_issue")
    assert fake_store_service.get_approval_status("inc_001") == "approved"
    # Now the issue path is unlocked (still a dry-run preview, never a real write).
    result = svc.create_github_issue(
        "inc_001", GitHubIssueOptions(), github_configured=True, env_dry_run=True
    )
    assert result.created is False
    assert result.body_preview


# --- Approve endpoint records an auditable, action-specific approval --------


def test_approve_create_github_issue_action(client: TestClient) -> None:
    """POST /approve returns approved AND persists the decision for that action.

    Covers the request shape required by Phase 8: an explicit ``create_github_issue``
    approval with an attributed approver and note must be stored against that
    incident/action so the gate can later read it back.
    """
    client.post("/incidents/trigger", json={"scenario": "broken_api_route"})
    client.post("/incidents/inc_001/investigate")

    response = client.post(
        "/incidents/inc_001/approve",
        json={
            "action": "create_github_issue",
            "approved_by": "demo_user",
            "note": "Approved for demo",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved"] is True
    assert body["action"] == "create_github_issue"
    assert body["approved_by"] == "demo_user"

    # The approval is stored for that incident/action (not just echoed back).
    record = approval_service.get_record("inc_001", "create_github_issue")
    assert record is not None
    assert record.incident_id == "inc_001"
    assert record.action == "create_github_issue"
    assert record.status == "approved"
    assert record.approved is True
    assert record.approved_by == "demo_user"
    assert record.note == "Approved for demo"


# --- No GitHub client / network create call ever happens --------------------


def test_no_github_network_write_even_when_approved(store_reset, monkeypatch) -> None:
    """The approved dry-run path must never open an outbound network connection.

    There is no live GitHub client in this build, but this test fails loudly if
    one is ever wired in without keeping the dry-run guarantee: any attempt to
    open a socket during issue creation raises, so a real ``create_issue`` write
    cannot slip through. We verify the full approved flow stays a redacted
    preview with no network egress.
    """

    def _no_network(*args, **kwargs):  # pragma: no cover - only hit on regression
        raise AssertionError(
            "Outbound network connection attempted during GitHub issue creation; "
            "no real GitHub client call is allowed in this phase."
        )

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    svc.investigate_incident("broken_api_route", persist=False)
    approval_service.approve_action(
        "inc_001", "create_github_issue", approved_by="demo_user"
    )

    result = svc.create_github_issue(
        "inc_001", GitHubIssueOptions(), github_configured=True, env_dry_run=True
    )
    # Approved, but still a preview only — never a real GitHub write.
    assert result.created is False
    assert result.dry_run is True
    assert result.issue_url is None
    assert result.issue_number is None
