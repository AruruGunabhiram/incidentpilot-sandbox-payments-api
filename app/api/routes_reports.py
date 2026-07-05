"""Report retrieval route."""

from fastapi import APIRouter

from app.schemas.report import IncidentReport
from app.services.errors import IncidentNotFound, ReportNotReady
from app.storage import incident_store

router = APIRouter(prefix="/incidents", tags=["reports"])


@router.get("/{incident_id}/report", response_model=IncidentReport)
def read_incident_report(incident_id: str) -> IncidentReport:
    """Return the stored, grounded incident report (after /investigate)."""
    state = incident_store.get_incident(incident_id)
    if state is None:
        raise IncidentNotFound(f"Unknown incident '{incident_id}'. Trigger it first.")
    if state.report is None:
        raise ReportNotReady("Investigate the incident before fetching its report.")
    return state.report
