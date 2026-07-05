"""Sequential agent orchestrator with deterministic fallback.

This is the optional agent layer's single entry point. It does **not** replace
the deterministic investigation service — it wraps it:

    deterministic investigate (source of truth)
      -> Triage -> Log Investigator -> Code Context -> Fix Planner
      -> Safety Reviewer (last)
      -> Final Report Builder (existing IncidentReport schema)
      -> quality gate vs. deterministic
      -> agent report  ⟂  deterministic report (whichever is safe + not weaker)

The deterministic report is always computed first and kept. Agents only restate
its grounded, already-redacted findings; the orchestrator re-validates every
agent step and assembles a candidate report by overlaying *narrative* fields
onto a copy of the deterministic report — evidence, line numbers, verified file
lists, redaction counts, and approval gates stay deterministic and authoritative.

A candidate is used only if every agent step was valid and the candidate is no
weaker than deterministic (same severity, same grounded file(s), same root-cause
category, confidence not lower, human review not dropped, safety not loosened).
Otherwise the deterministic report wins. No GitHub/PR/branch/commit write is ever
performed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.agents.base import AGENT_SEQUENCE, AgentOutcome, EvidenceIndex
from app.agents.code_context_agent import CodeContextAgent
from app.agents.fix_planner_agent import FixPlannerAgent
from app.agents.log_investigator_agent import LogInvestigatorAgent
from app.agents.model_client import GroundedDeterministicClient, ModelClient
from app.agents.safety_reviewer_agent import SafetyReviewerAgent
from app.agents.triage_agent import TriageAgent
from app.schemas.report import IncidentReport
from app.services import investigation_service
from app.storage import incident_store
from app.tools.redactor import redact_secrets
from app.tools.report_writer import build_markdown_report

InvestigationMode = Literal["agent", "deterministic_fallback"]


@dataclass
class AgentInvestigationResult:
    """Outcome of an agent-mode investigation.

    ``report`` is the chosen report (agent or deterministic). ``mode`` says which
    won. ``deterministic_report`` is always the source-of-truth report, so a
    caller can compare. ``outcomes`` are the per-agent results in execution order
    (useful for the demo and for asserting the sequential flow).
    """

    report: IncidentReport
    mode: InvestigationMode
    outcomes: list[AgentOutcome] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)
    deterministic_report: IncidentReport | None = None

    @property
    def used_agents(self) -> bool:
        return self.mode == "agent"


def run_agent_investigation(
    scenario_or_incident_id: str,
    *,
    model: ModelClient | None = None,
    persist: bool = False,
    reports_dir: Path | str | None = None,
) -> AgentInvestigationResult:
    """Run the optional agent pipeline over a deterministic investigation.

    The deterministic report is computed first and is the source of truth. The
    agents (using ``model``, default :class:`GroundedDeterministicClient`) reason
    only over its grounded findings. If anything goes wrong — invalid JSON, an
    ungrounded claim, or a weaker result — the deterministic report is returned
    unchanged. Persistence (when ``persist`` is true) writes the *chosen* report
    through the same redacting store the deterministic path uses.
    """
    client = model or GroundedDeterministicClient()

    # 1. Deterministic investigation: the source of truth (never persisted here;
    #    we persist the chosen report once, below).
    det_report = investigation_service.investigate_incident(
        scenario_or_incident_id, persist=False
    )
    index = EvidenceIndex.from_report(det_report)

    # 2. Sequential agent flow. Order is fixed by AGENT_SEQUENCE; the Safety
    #    Reviewer is constructed last and runs last.
    triage = TriageAgent()
    log_investigator = LogInvestigatorAgent()
    code_context = CodeContextAgent()
    fix_planner = FixPlannerAgent()
    safety_reviewer = SafetyReviewerAgent()

    outcomes: list[AgentOutcome] = []
    outcomes.append(triage.run(det_report, index, client))
    outcomes.append(log_investigator.run(det_report, index, client))
    outcomes.append(code_context.run(det_report, index, client))
    outcomes.append(fix_planner.run(det_report, index, client))
    prior_flags = [o.needs_human_review for o in outcomes]
    outcomes.append(
        safety_reviewer.run(det_report, index, client, prior_review_flags=prior_flags)
    )

    by_name = {outcome.name: outcome for outcome in outcomes}

    # 3. Decide whether the agent output is usable.
    fallback_reasons: list[str] = [
        f"{o.name}: {'; '.join(o.notes) or 'invalid output'}"
        for o in outcomes
        if o.invalid
    ]

    candidate: IncidentReport | None = None
    if not fallback_reasons:
        candidate = _build_candidate(det_report, by_name)
        if candidate is None:
            fallback_reasons.append("final report builder: could not assemble a valid report")
        else:
            fallback_reasons.extend(_quality_gate(candidate, det_report))

    if candidate is not None and not fallback_reasons:
        chosen, mode = candidate, "agent"
    else:
        chosen, mode = det_report, "deterministic_fallback"

    # 4. Record + optionally persist the chosen report via the shared, redacting
    #    store (same path the deterministic service uses). No external write.
    incident_store.save_report(chosen.incident_id, chosen)
    if persist:
        incident_store.save_report_json(chosen, reports_dir=reports_dir)
        incident_store.save_report_markdown(
            chosen.incident_id, build_markdown_report(chosen), reports_dir=reports_dir
        )

    return AgentInvestigationResult(
        report=chosen,
        mode=mode,
        outcomes=outcomes,
        fallback_reasons=fallback_reasons,
        deterministic_report=det_report,
    )


# ---------------------------------------------------------------------------
# Final Report Builder
# ---------------------------------------------------------------------------


def _build_candidate(
    det: IncidentReport, by_name: dict[str, AgentOutcome]
) -> IncidentReport | None:
    """Assemble a candidate report by overlaying validated agent narrative.

    Starts from a deep copy of the deterministic report (so the candidate is
    always a valid :class:`IncidentReport` with authoritative evidence, verified
    file lists, and redaction counts) and overlays only the *narrative* fields an
    agent validly produced. Human-review flags can only be raised. Returns
    ``None`` if assembly fails for any reason.
    """
    try:
        candidate = det.model_copy(deep=True)

        triage = by_name.get("triage")
        if triage is not None and triage.ok:
            candidate.severity = triage.data.get("severity", candidate.severity)
            candidate.affected_service = triage.data.get(
                "affected_service", candidate.affected_service
            )
            candidate.summary = redact_secrets(
                str(triage.data.get("summary") or candidate.summary)
            )
            candidate.confidence = _as_confidence(
                triage.data.get("confidence"), candidate.confidence
            )
            if triage.data.get("primary_error"):
                candidate.primary_error = triage.data["primary_error"]
        candidate.needs_human_review = candidate.needs_human_review or _raised(triage)

        log = by_name.get("log_investigator")
        if log is not None and log.ok and candidate.log_finding is not None:
            if log.data.get("stack_trace_summary") is not None:
                candidate.log_finding.stack_trace_summary = redact_secrets(
                    str(log.data["stack_trace_summary"])
                )
            candidate.log_finding.summary = redact_secrets(
                str(log.data.get("summary") or candidate.log_finding.summary)
            )
            candidate.log_finding.needs_human_review = (
                candidate.log_finding.needs_human_review or _raised(log)
            )

        code = by_name.get("code_context")
        if code is not None and code.ok and candidate.code_finding is not None:
            candidate.code_finding.summary = redact_secrets(
                str(code.data.get("summary") or candidate.code_finding.summary)
            )
            candidate.code_finding.needs_human_review = (
                candidate.code_finding.needs_human_review or _raised(code)
            )

        fix = by_name.get("fix_planner")
        if fix is not None and fix.ok:
            root = fix.data.get("root_cause")
            if isinstance(root, dict) and candidate.root_cause is not None:
                candidate.root_cause.summary = redact_secrets(
                    str(root.get("summary") or candidate.root_cause.summary)
                )
                candidate.root_cause.needs_human_review = (
                    candidate.root_cause.needs_human_review
                    or bool(root.get("needs_human_review"))
                )
            plan = fix.data.get("fix_plan")
            if isinstance(plan, dict) and candidate.fix_plan is not None:
                candidate.fix_plan.summary = redact_secrets(
                    str(plan.get("summary") or candidate.fix_plan.summary)
                )
                candidate.fix_plan.needs_human_review = (
                    candidate.fix_plan.needs_human_review
                    or bool(plan.get("needs_human_review"))
                )

        safety = by_name.get("safety_reviewer")
        if safety is not None and safety.ok and candidate.safety_review is not None:
            review = candidate.safety_review
            # Safety only tightens: AND the approvals so they can only drop.
            review.approved_for_github_issue = review.approved_for_github_issue and bool(
                safety.data.get("approved_for_github_issue", review.approved_for_github_issue)
            )
            review.approved_for_display = review.approved_for_display and bool(
                safety.data.get("approved_for_display", review.approved_for_display)
            )
            review.approved_for_pr = False
            review.summary = redact_secrets(
                str(safety.data.get("summary") or review.summary)
            )
            review.needs_human_review = review.needs_human_review or _raised(safety)
            candidate.needs_human_review = candidate.needs_human_review or _raised(safety)

        return candidate
    except Exception:  # noqa: BLE001 - any assembly error => deterministic fallback
        return None


def _raised(outcome: AgentOutcome | None) -> bool:
    """True if this agent step asks for (more) human review."""
    if outcome is None:
        return False
    return outcome.needs_human_review or outcome.status != "ok"


def _as_confidence(value: object, default: float) -> float:
    """Coerce an agent-provided confidence into [0, 1], else keep ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0.0, min(1.0, float(value)))


# ---------------------------------------------------------------------------
# Quality gate: the agent report may never be weaker than deterministic
# ---------------------------------------------------------------------------


def _quality_gate(candidate: IncidentReport, det: IncidentReport) -> list[str]:
    """Return reasons the candidate is weaker/less safe than deterministic.

    An empty list means the candidate is acceptable. Any reason triggers a
    deterministic fallback, enforcing "if agent output is worse, deterministic
    mode wins" — including the explicit ``broken_api_route`` parity guarantees.
    """
    reasons: list[str] = []

    if candidate.severity != det.severity:
        reasons.append("severity diverged from deterministic classification")

    if candidate.confidence < det.confidence - 1e-9:
        reasons.append("confidence lower than deterministic")

    if det.needs_human_review and not candidate.needs_human_review:
        reasons.append("dropped a human review the deterministic report required")

    det_category = det.root_cause.category if det.root_cause else None
    cand_category = candidate.root_cause.category if candidate.root_cause else None
    if det_category != cand_category:
        reasons.append("root-cause category changed from deterministic")

    det_files = set(det.code_finding.matched_files) if det.code_finding else set()
    cand_files = set(candidate.code_finding.matched_files) if candidate.code_finding else set()
    if det_files != cand_files:
        reasons.append("grounded repo file(s) changed from deterministic")

    if det.primary_error != candidate.primary_error:
        reasons.append("primary error changed from deterministic")

    det_safety = det.safety_review
    cand_safety = candidate.safety_review
    if det_safety is not None and cand_safety is not None:
        if cand_safety.approved_for_github_issue and not det_safety.approved_for_github_issue:
            reasons.append("safety approved a GitHub issue deterministic blocked")
        if cand_safety.approved_for_pr:
            reasons.append("safety approved a PR (never allowed)")
        if cand_safety.secrets_detected != det_safety.secrets_detected:
            reasons.append("secret detection diverged from deterministic")
        if cand_safety.redactions_applied != det_safety.redactions_applied:
            reasons.append("redaction count diverged from deterministic")

    return reasons
