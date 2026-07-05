"""Log Investigator agent: restate the deterministic log finding.

Runs after Triage. The CI log reader has already parsed the log, redacted
secrets, and extracted the failing test, primary error, and redaction count.
This agent only restates that finding; it never re-reads raw logs, changes the
redaction count, or cites an evidence id the tools did not produce.
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

NAME = "log_investigator"


class LogInvestigatorAgent:
    """Thin wrapper around the deterministic log finding."""

    name = NAME

    def __init__(self) -> None:
        self.prompt = load_prompt(NAME)

    def proposal(self, report: IncidentReport) -> dict[str, Any]:
        finding = report.log_finding
        if finding is None:
            return {}
        return {
            "primary_error": finding.primary_error,
            "failing_test": finding.failing_test,
            "stack_trace_summary": finding.stack_trace_summary,
            "redactions_applied": finding.redactions_applied,
            "summary": finding.summary,
            "evidence_ids": [item.id for item in finding.evidence],
            "needs_human_review": finding.needs_human_review,
        }

    def run(
        self, report: IncidentReport, index: EvidenceIndex, model: ModelClient
    ) -> AgentOutcome:
        if report.log_finding is None:
            return AgentOutcome(
                self.name, "insufficient_evidence", needs_human_review=True
            )

        payload = {
            "proposal": self.proposal(report),
            "allowed_evidence_ids": sorted(index.ids),
        }
        data = parse_agent_json(
            model.complete(agent=self.name, prompt=self.prompt, payload=payload)
        )
        if data is None:
            return AgentOutcome(self.name, "invalid", notes=["output was not valid JSON"])

        if data.get("primary_error") == INSUFFICIENT_EVIDENCE:
            return AgentOutcome(
                self.name, "insufficient_evidence", data=data, needs_human_review=True
            )

        # Grounding: cited evidence must be tool-produced; key facts must match
        # the deterministic finding exactly (no invented test, error, or count).
        if not index.evidence_ids_grounded(data.get("evidence_ids")):
            return AgentOutcome(self.name, "invalid", notes=["cited ungrounded evidence id"])
        if data.get("primary_error") not in (None, report.log_finding.primary_error):
            return AgentOutcome(self.name, "invalid", notes=["primary_error not grounded"])
        if data.get("failing_test") not in (None, report.log_finding.failing_test):
            return AgentOutcome(self.name, "invalid", notes=["failing_test not grounded"])

        return AgentOutcome(
            self.name,
            "ok",
            data=data,
            needs_human_review=bool(data.get("needs_human_review", True)),
        )
