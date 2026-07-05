"""Incident intake schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TriggerType = Literal[
    "github_actions_failure",
    "api_error",
    "manual",
    "test_failure",
    "unknown",
]


class IncidentIntake(BaseModel):
    """Normalized representation of an incoming incident to investigate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    incident_id: str
    scenario: str
    service: str
    trigger_type: TriggerType
    summary: str
    repo_owner: str | None = None
    repo_name: str | None = None
    repo_path: str | None = None
    endpoint: str | None = None
    repo_mode: Literal["local", "github"] = "local"
    signals: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IncidentTriggerRequest(BaseModel):
    """API request body for triggering an incident investigation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    scenario: str = Field(..., examples=["broken_api_route"])


class IncidentTriggerResponse(BaseModel):
    """API response returned when an incident investigation is created."""

    model_config = ConfigDict(extra="forbid", strict=True)

    incident_id: str
    status: str
    scenario: str
