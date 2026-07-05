"""Incident control-plane routes: trigger, investigate, approve.

Handlers stay thin — they translate HTTP to a single service call. All workflow
logic lives in :mod:`app.services.investigation_service`; domain errors raised
there are mapped to HTTP responses by the handler registered in ``app.main``.
"""

from fastapi import APIRouter, Body

from app.schemas.approval import ApprovalDecision, ApprovalResponse
from app.schemas.incident import IncidentTriggerRequest, IncidentTriggerResponse
from app.schemas.report import IncidentReport
from app.services import investigation_service

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("/trigger", response_model=IncidentTriggerResponse)
def trigger_incident(request: IncidentTriggerRequest) -> IncidentTriggerResponse:
    """Load the scenario's intake fixture and register a new incident."""
    return investigation_service.create_incident(request.scenario)


@router.post("/{incident_id}/investigate", response_model=IncidentReport)
def investigate_incident(incident_id: str) -> IncidentReport:
    """Run the deterministic investigation and return the grounded report."""
    return investigation_service.investigate_incident(incident_id)


@router.post("/{incident_id}/approve", response_model=ApprovalResponse)
def approve_incident(
    incident_id: str,
    decision: ApprovalDecision | None = Body(default=None),
) -> ApprovalResponse:
    """Record a human approval decision for an action (empty body = approve).

    The action defaults to ``create_github_issue`` and is enforced action-specific
    by the service layer, so approving one action never authorizes another.
    """
    decision = decision or ApprovalDecision()
    return investigation_service.record_github_approval(
        incident_id,
        approved=decision.approved,
        approved_by=decision.approved_by,
        note=decision.note,
        action=decision.action,
    )
