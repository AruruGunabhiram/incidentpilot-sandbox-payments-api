"""Final incident report schema assembled by the Final Report Builder."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from app.schemas.findings import (
    CodeFinding,
    FixPlan,
    LogFinding,
    ReviewedOutput,
    RootCauseHypothesis,
)
from app.schemas.safety import SafetyReview


class IncidentReport(ReviewedOutput):
    """Grounded, human-reviewable incident report."""

    incident_id: str
    title: str
    severity: Literal["SEV1", "SEV2", "SEV3", "UNKNOWN"]
    affected_service: str
    status: Literal[
        "created",
        "investigating",
        "awaiting_human_approval",
        "approved",
        "blocked",
        "closed",
    ]
    primary_error: str | None = None
    log_finding: LogFinding | None = None
    code_finding: CodeFinding | None = None
    root_cause: RootCauseHypothesis | None = None
    fix_plan: FixPlan | None = None
    safety_review: SafetyReview | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
