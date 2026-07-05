"""Triage agent: classify an already-investigated incident.

Runs first. Restates the deterministic triage view (severity, affected service,
primary error) without inventing anything. It can only keep or *raise* the need
for human review; it can never claim an error the logs did not surface.
"""

from __future__ import annotations

from typing import Any

from app.agents.base import (
    INSUFFICIENT_EVIDENCE,
    AgentOutcome,
    EvidenceIndex,
    load_prompt,
    parse_agent_json,
)
from app.agents.model_client import ModelClient
from app.schemas.report import IncidentReport

NAME = "triage"
_VALID_SEVERITY = {"SEV1", "SEV2", "SEV3", "UNKNOWN"}


class TriageAgent:
    """Thin wrapper around the deterministic triage classification."""

    name = NAME

    def __init__(self) -> None:
        self.prompt = load_prompt(NAME)

    def proposal(self, report: IncidentReport) -> dict[str, Any]:
        return {
            "severity": report.severity,
            "affected_service": report.affected_service,
            "primary_error": report.primary_error,
            "confidence": report.confidence,
            "needs_human_review": report.needs_human_review,
            "summary": report.summary,
        }

    def run(
        self, report: IncidentReport, index: EvidenceIndex, model: ModelClient
    ) -> AgentOutcome:
        payload = {
            "proposal": self.proposal(report),
            "allowed_evidence_ids": sorted(index.ids),
        }
        data = parse_agent_json(
            model.complete(agent=self.name, prompt=self.prompt, payload=payload)
        )
        if data is None:
            return AgentOutcome(self.name, "invalid", notes=["output was not valid JSON"])

        if data.get("primary_error") == INSUFFICIENT_EVIDENCE or report.primary_error is None:
            return AgentOutcome(
                self.name, "insufficient_evidence", data=data, needs_human_review=True
            )

        severity = data.get("severity")
        if severity not in _VALID_SEVERITY:
            return AgentOutcome(
                self.name, "invalid", notes=[f"invalid severity {severity!r}"]
            )

        # An agent may never invent an error the deterministic logs did not show.
        if data.get("primary_error") not in (None, report.primary_error):
            return AgentOutcome(
                self.name, "invalid", notes=["primary_error not grounded in logs"]
            )

        return AgentOutcome(
            self.name,
            "ok",
            data=data,
            needs_human_review=bool(data.get("needs_human_review", True)),
        )
