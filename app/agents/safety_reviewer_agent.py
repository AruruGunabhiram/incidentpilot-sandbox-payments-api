"""Safety Reviewer agent: the last gate before the Final Report Builder.

The deterministic safety review already scanned for secrets, checked that the
root cause is grounded, and decided GitHub-issue eligibility. This agent can
only *tighten* that decision — raise the need for human review, lower an
approval, or raise risk. It can never approve an action the deterministic review
blocked, clear a secret detection, or enable any external write. By contract it
runs last so its (possibly stricter) verdict is what reaches the report.
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

NAME = "safety_reviewer"
_VALID_RISK = {"low", "medium", "high", "critical"}


class SafetyReviewerAgent:
    """Thin wrapper that can only make the deterministic safety review stricter."""

    name = NAME

    def __init__(self) -> None:
        self.prompt = load_prompt(NAME)

    def proposal(self, report: IncidentReport) -> dict[str, Any]:
        review = report.safety_review
        if review is None:
            return {}
        return {
            "approved_for_display": review.approved_for_display,
            "approved_for_github_issue": review.approved_for_github_issue,
            "approved_for_pr": review.approved_for_pr,
            "risk_level": review.risk_level,
            "secrets_detected": review.secrets_detected,
            "redactions_applied": review.redactions_applied,
            "summary": review.summary,
            "needs_human_review": review.needs_human_review,
        }

    def run(
        self,
        report: IncidentReport,
        index: EvidenceIndex,
        model: ModelClient,
        *,
        prior_review_flags: list[bool] | None = None,
    ) -> AgentOutcome:
        if report.safety_review is None:
            return AgentOutcome(self.name, "insufficient_evidence", needs_human_review=True)

        det = report.safety_review
        payload = {
            "proposal": self.proposal(report),
            "prior_review_flags": list(prior_review_flags or []),
        }
        data = parse_agent_json(
            model.complete(agent=self.name, prompt=self.prompt, payload=payload)
        )
        if data is None:
            return AgentOutcome(self.name, "invalid", notes=["output was not valid JSON"])

        risk = data.get("risk_level", det.risk_level)
        if risk not in _VALID_RISK:
            return AgentOutcome(self.name, "invalid", notes=[f"invalid risk_level {risk!r}"])

        # Safety may only tighten. Any attempt to loosen a deterministic decision
        # is rejected outright (the orchestrator then falls back to deterministic).
        if data.get("approved_for_github_issue") and not det.approved_for_github_issue:
            return AgentOutcome(
                self.name, "invalid", notes=["tried to approve a blocked GitHub issue"]
            )
        if data.get("approved_for_pr"):
            return AgentOutcome(self.name, "invalid", notes=["PR approval is never allowed"])
        if det.secrets_detected and data.get("secrets_detected") is False:
            return AgentOutcome(
                self.name, "invalid", notes=["tried to clear a secret detection"]
            )

        # Human review can only be added by this stage, never removed: if any
        # earlier agent or the deterministic review wanted a human, keep it.
        prior = any(prior_review_flags or [])
        needs_review = (
            bool(data.get("needs_human_review", True))
            or det.needs_human_review
            or prior
        )
        return AgentOutcome(self.name, "ok", data=data, needs_human_review=needs_review)
