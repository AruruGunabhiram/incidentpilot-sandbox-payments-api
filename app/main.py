"""IncidentPilot FastAPI application (Phase 4 control plane).

Registers the deterministic control-plane routers and a single handler that
turns domain errors from the services into clean JSON responses, so route
handlers never build error payloads by hand.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes_github import router as github_router
from app.api.routes_health import router as health_router
from app.api.routes_incidents import router as incidents_router
from app.api.routes_reports import router as reports_router
from app.services.errors import IncidentError

app = FastAPI(title="IncidentPilot", version="0.1.0")


@app.exception_handler(IncidentError)
async def handle_incident_error(request: Request, exc: IncidentError) -> JSONResponse:
    """Map control-plane domain errors to their HTTP status + detail.

    Includes the stable ``reason`` code when the error carries one, so a blocked
    response is explicit (e.g. ``approval_required``) without parsing prose.
    """
    content: dict[str, str] = {"detail": exc.detail}
    if exc.reason is not None:
        content["reason"] = exc.reason
    return JSONResponse(status_code=exc.status_code, content=content)


app.include_router(health_router)
app.include_router(incidents_router)
app.include_router(reports_router)
app.include_router(github_router)
