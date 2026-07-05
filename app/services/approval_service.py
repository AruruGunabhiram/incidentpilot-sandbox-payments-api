"""Human approval gate (Phase 8).

Single, service-layer source of truth for whether an explicit, *action-specific*
human approval is on file for an incident. The gate is enforced here — not in the
API route — so it cannot be bypassed by calling a different endpoint or service:
``investigation_service.create_github_issue`` asks this service for permission
before it builds any GitHub payload.

State machine, per (incident, action):

    pending  ──approve──▶ approved
       │                     │
       └──reject──▶ rejected ◀┘  (reject is sticky; re-approval requires a new
                                  explicit approve_action call)

The default state is always ``pending``: an action stays blocked until a human
approves it. Approval is recorded against one action only, so approving
``create_github_issue`` never authorizes ``create_pr`` or any other action.

This service owns *only* the approval check. The deterministic safety verdict
(secrets, unverified paths, confidence threshold) is authoritative and is checked
*before* approval by :mod:`app.services.safety_gate`, so a failed safety review or
low confidence still blocks issue creation no matter what is approved here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.approval import (
    APPROVAL_ACTIONS,
    ApprovalAction,
    ApprovalRecord,
    ApprovalStatus,
)
from app.services.errors import (
    ApprovalRejected,
    ApprovalRequired,
    IncidentNotFound,
    InvalidAction,
    ReportNotReady,
)
from app.storage import incident_store


def _validate_action(action: str) -> ApprovalAction:
    """Return ``action`` if it is an approvable action, else raise.

    Defense in depth: the API already constrains the body to the
    ``ApprovalAction`` Literal, but the service re-checks so a direct, in-process
    caller cannot record an approval for an unknown action.
    """
    if action not in APPROVAL_ACTIONS:
        raise InvalidAction(
            f"Unknown approval action {action!r}. "
            f"Valid actions: {', '.join(sorted(APPROVAL_ACTIONS))}."
        )
    return action  # type: ignore[return-value]


class ApprovalService:
    """Records and enforces action-specific human approvals for an incident.

    Stateless aside from the injected store, which holds the approval records.
    The store defaults to the shared in-memory incident store, matching the rest
    of the control plane (no database is introduced).
    """

    def __init__(self, store=incident_store) -> None:
        self._store = store

    # -- decisions -----------------------------------------------------------

    def approve_action(
        self,
        incident_id: str,
        action: str = "create_github_issue",
        *,
        approved_by: str = "demo-operator",
        note: str | None = None,
    ) -> ApprovalRecord:
        """Record an ``approved`` decision for ``(incident_id, action)``.

        Requires an investigated incident (a report must exist) so an approval
        can never be recorded for something that was never analyzed.
        """
        action = _validate_action(action)
        self._require_incident_with_report(incident_id)
        return self._record(
            incident_id, action, status="approved", approved_by=approved_by, note=note
        )

    def reject_action(
        self,
        incident_id: str,
        action: str = "create_github_issue",
        *,
        approved_by: str = "demo-operator",
        note: str | None = None,
    ) -> ApprovalRecord:
        """Record a ``rejected`` decision for ``(incident_id, action)``."""
        action = _validate_action(action)
        self._require_incident_with_report(incident_id)
        return self._record(
            incident_id, action, status="rejected", approved_by=approved_by, note=note
        )

    # -- queries -------------------------------------------------------------

    def get_record(
        self, incident_id: str, action: str = "create_github_issue"
    ) -> ApprovalRecord | None:
        """Return the stored approval record for the action, or ``None``."""
        action = _validate_action(action)
        return self._store.get_approval(incident_id, action)

    def get_approval_status(
        self, incident_id: str, action: str = "create_github_issue"
    ) -> ApprovalStatus:
        """Return the action's status, defaulting to ``pending`` when unrecorded."""
        record = self.get_record(incident_id, action)
        return record.status if record is not None else "pending"

    # -- enforcement ---------------------------------------------------------

    def require_approved(
        self, incident_id: str, action: str = "create_github_issue"
    ) -> ApprovalRecord:
        """Return the approval record, or raise unless the action is approved.

        Raises :class:`ApprovalRequired` when no decision exists yet (``pending``)
        and :class:`ApprovalRejected` when a human explicitly rejected it. This is
        the method gated services call before performing the sensitive action.
        """
        action = _validate_action(action)
        record = self._store.get_approval(incident_id, action)
        if record is None or record.status == "pending":
            raise ApprovalRequired(
                f"Human approval is required for '{action}' on incident "
                f"'{incident_id}'. POST to /incidents/{{id}}/approve first."
            )
        if record.status == "rejected":
            raise ApprovalRejected(
                f"Action '{action}' was rejected for incident '{incident_id}'; "
                f"it stays blocked until a new approval is recorded."
            )
        return record

    # -- internals -----------------------------------------------------------

    def _require_incident_with_report(self, incident_id: str):
        state = self._store.get_incident(incident_id)
        if state is None:
            raise IncidentNotFound(
                f"Unknown incident '{incident_id}'. Trigger it first."
            )
        if state.report is None:
            raise ReportNotReady(
                "Investigate the incident before recording an approval."
            )
        return state

    def _record(
        self,
        incident_id: str,
        action: ApprovalAction,
        *,
        status: ApprovalStatus,
        approved_by: str,
        note: str | None,
    ) -> ApprovalRecord:
        now = datetime.now(timezone.utc)
        record = ApprovalRecord(
            incident_id=incident_id,
            action=action,
            status=status,
            approved=(status == "approved"),
            approved_by=approved_by,
            note=note,
            recorded_at=now,
            updated_at=now,
        )
        self._store.record_approval(incident_id, record)
        return record


# Shared singleton over the in-memory store, mirroring the rest of the control
# plane. Tests can construct their own ApprovalService with a fake store.
approval_service = ApprovalService()
