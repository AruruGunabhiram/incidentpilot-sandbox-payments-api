"""Code Context agent: restate the deterministic, verified code finding.

Runs after the Log Investigator. The repo search already grounded stack frames
to real files and read each cited line back from disk. This agent only restates
which verified files/symbols are implicated. Its core guard: **every file it
names must already exist in the tool evidence**. An invented or "corrected" path
makes the step invalid, which forces a deterministic fallback or human review.
"""

from __future__ import annotations

from typing import Any

from app.agents.base import (
    AgentOutcome,
    EvidenceIndex,
    load_prompt,
    parse_agent_json,
)
from app.agents.model_client import ModelClient
from app.schemas.report import IncidentReport

NAME = "code_context"


class CodeContextAgent:
    """Thin wrapper around the deterministic, path-verified code finding."""

    name = NAME

    def __init__(self) -> None:
        self.prompt = load_prompt(NAME)

    def proposal(self, report: IncidentReport) -> dict[str, Any]:
        finding = report.code_finding
        if finding is None:
            return {}
        return {
            "matched_files": list(finding.matched_files),
            "suspected_symbols": list(finding.suspected_symbols),
            "missing_files": list(finding.missing_files),
            "summary": finding.summary,
            "evidence_ids": [item.id for item in finding.evidence],
            "needs_human_review": finding.needs_human_review,
        }

    def run(
        self, report: IncidentReport, index: EvidenceIndex, model: ModelClient
    ) -> AgentOutcome:
        if report.code_finding is None:
            # No grounded code context exists; decline rather than invent one.
            return AgentOutcome(
                self.name, "insufficient_evidence", needs_human_review=True
            )

        payload = {
            "proposal": self.proposal(report),
            "allowed_evidence_ids": sorted(index.ids),
            "allowed_paths": sorted(index.paths),
        }
        data = parse_agent_json(
            model.complete(agent=self.name, prompt=self.prompt, payload=payload)
        )
        if data is None:
            return AgentOutcome(self.name, "invalid", notes=["output was not valid JSON"])

        matched = data.get("matched_files") or []
        if not isinstance(matched, list):
            return AgentOutcome(self.name, "invalid", notes=["matched_files not a list"])

        # The decisive guard: a named file that is not in the tool evidence is,
        # by definition, invented. Reject it.
        if not index.paths_grounded(matched):
            return AgentOutcome(
                self.name,
                "invalid",
                notes=["matched_files contains a path not found in tool evidence"],
            )
        if not index.evidence_ids_grounded(data.get("evidence_ids")):
            return AgentOutcome(self.name, "invalid", notes=["cited ungrounded evidence id"])

        return AgentOutcome(
            self.name,
            "ok",
            data=data,
            needs_human_review=bool(data.get("needs_human_review", True)),
        )
