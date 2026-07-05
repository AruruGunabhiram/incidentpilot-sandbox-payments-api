"""Fix Planner agent: restate the grounded root cause and fix plan.

Runs after Code Context. The deterministic service already formed a root-cause
hypothesis and fix plan anchored to verified files and the failing test. This
agent restates them only when they are supported by file/log evidence: it keeps
the deterministic category, cites only tool-produced evidence ids, and never
proposes a fix without a grounded root cause.
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


NAME = "fix_planner"


class FixPlannerAgent:
    """Thin wrapper around the deterministic root cause + fix plan."""

    name = NAME

    def __init__(self) -> None:
        self.prompt = load_prompt(NAME)

    def proposal(self, report: IncidentReport) -> dict[str, Any]:
        root = report.root_cause
        fix = report.fix_plan
        # Top-level review flag, grounded in the deterministic findings, so the
        # agent does not spuriously default it to True for a clean incident.
        needs_review = bool(root.needs_human_review) if root is not None else True
        if fix is not None:
            needs_review = needs_review or bool(fix.needs_human_review)
        proposal: dict[str, Any] = {"needs_human_review": needs_review}
        if root is not None:
            proposal["root_cause"] = {
                "category": root.category,
                "summary": root.summary,
                "supporting_evidence_ids": list(root.supporting_evidence_ids),
                "alternatives": list(root.alternatives),
                "needs_human_review": root.needs_human_review,
            }
        if fix is not None:
            proposal["fix_plan"] = {
                "summary": fix.summary,
                "patch_strategy": fix.patch_strategy,
                "steps": list(fix.steps),
                "regression_tests": list(fix.regression_tests),
                "rollback_plan": list(fix.rollback_plan),
                "risks": list(fix.risks),
                "needs_human_review": fix.needs_human_review,
            }
        return proposal

    def run(
        self, report: IncidentReport, index: EvidenceIndex, model: ModelClient
    ) -> AgentOutcome:
        # No grounded root cause -> decline. Never propose a fix without one.
        if report.root_cause is None or report.root_cause.category == "undetermined":
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

        root = data.get("root_cause")
        if root == INSUFFICIENT_EVIDENCE:
            return AgentOutcome(
                self.name, "insufficient_evidence", data=data, needs_human_review=True
            )
        if not isinstance(root, dict):
            return AgentOutcome(self.name, "invalid", notes=["root_cause missing/!dict"])

        # The category is the grounded diagnosis; it may not be swapped out.
        if root.get("category") != report.root_cause.category:
            return AgentOutcome(self.name, "invalid", notes=["root cause category changed"])
        if not index.evidence_ids_grounded(root.get("supporting_evidence_ids")):
            return AgentOutcome(self.name, "invalid", notes=["root cause cites ungrounded evidence"])
        # A claimed root cause must rest on at least one real evidence id.
        if not root.get("supporting_evidence_ids"):
            return AgentOutcome(
                self.name, "invalid", notes=["root cause has no supporting evidence ids"]
            )

        return AgentOutcome(
            self.name,
            "ok",
            data=data,
            needs_human_review=bool(data.get("needs_human_review", True)),
        )
