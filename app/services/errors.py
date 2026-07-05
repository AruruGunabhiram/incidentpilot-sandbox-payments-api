"""Domain errors for the incident control plane.

Services raise these plain exceptions instead of importing FastAPI. ``app.main``
registers a single handler that turns each one into a clean JSON response with
the right HTTP status, so route handlers stay thin and never build error
responses by hand.
"""

from __future__ import annotations


class IncidentError(Exception):
    """Base class for control-plane errors. Carries an HTTP status + detail.

    ``reason`` is a stable, machine-readable code (e.g. ``approval_required``)
    that makes a blocked response explicit for clients and tests, independent of
    the human-readable ``detail`` wording.
    """

    status_code: int = 400
    reason: str | None = None

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ScenarioNotFound(IncidentError):
    """The requested demo scenario has no intake fixture."""

    status_code = 404


class IncidentNotFound(IncidentError):
    """No incident exists for the given id (it was never triggered)."""

    status_code = 404


class ReportNotReady(IncidentError):
    """The incident exists but has not been investigated yet."""

    status_code = 400


class SafetyBlocked(IncidentError):
    """The safety review does not permit the requested external action."""

    status_code = 403
    reason = "safety_review_failed"


class ApprovalRequired(IncidentError):
    """A required human approval is missing (still ``pending``) for the action."""

    status_code = 403
    reason = "approval_required"


class ApprovalRejected(IncidentError):
    """A human explicitly rejected the requested action; it stays blocked."""

    status_code = 403
    reason = "approval_rejected"


class InvalidAction(IncidentError):
    """The requested approval action is not a recognized, approvable action."""

    status_code = 422
    reason = "invalid_action"
