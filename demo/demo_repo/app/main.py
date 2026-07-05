"""Demo FastAPI payments service (IncidentPilot demo_repo fixture).

This is NOT part of the IncidentPilot service itself. It is a tiny, runnable
app so incident scenarios can point evidence at real routes and line numbers.
No database or external services are used.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.routes import payments

app = FastAPI(title="Demo Payments Service")
app.include_router(payments.router)


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}
