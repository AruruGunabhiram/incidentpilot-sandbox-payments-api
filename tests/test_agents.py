"""Tests for the optional agent layer (Phase 6).

The agent layer wraps the deterministic investigation service; the deterministic
service stays the source of truth. These tests assert the layer is safe by
construction: prompts carry the grounding rules, the flow is sequential with the
Safety Reviewer last, the flagship ``broken_api_route`` scenario still resolves
to the same repo file and root cause, and any bad/weaker/ungrounded agent output
falls back to deterministic mode without leaking secrets or enabling a write.

Each test injects a stub :class:`ModelClient` to simulate model behavior; the
default :class:`GroundedDeterministicClient` is offline and deterministic, so no
network or LLM is needed.
"""

from __future__ import annotations

import json

import pytest

from app.agents import (
    AGENT_SEQUENCE,
    GroundedDeterministicClient,
    run_agent_investigation,
)
from app.agents.base import EvidenceIndex, PROMPTS_DIR, REQUIRED_PROMPT_RULES
from app.agents.code_context_agent import CodeContextAgent
from app.agents.log_investigator_agent import LogInvestigatorAgent
from app.config import Settings
from app.schemas.report import IncidentReport
from app.services import investigation_service as svc
from app.storage import incident_store as store
from app.tools.redactor import REDACTION_MARKER

# Same fake secrets seeded into demo/incidents/secret_in_logs as the
# deterministic service test uses. None may ever appear raw in agent output.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]

INVENTED_PATH = "app/totally/made_up.py"


@pytest.fixture(autouse=True)
def _reset_store():
    """Every test starts and ends from a clean in-memory control plane."""
    store.reset_store()
    yield
    store.reset_store()


def _deterministic(scenario: str) -> IncidentReport:
    """Return the deterministic report for ``scenario`` from a clean store."""
    store.reset_store()
    report = svc.investigate_incident(scenario, persist=False)
    store.reset_store()
    return report


# ---------------------------------------------------------------------------
# Stub model clients
# ---------------------------------------------------------------------------


class InvalidJSONClient(GroundedDeterministicClient):
    """Returns text that is not valid JSON for every agent."""

    def complete(self, *, agent, prompt, payload):  # noqa: ANN001
        return "this is not valid json {{{"


class InventedPathClient(GroundedDeterministicClient):
    """Grounds every agent except Code Context, which names a fabricated file."""

    def complete(self, *, agent, prompt, payload):  # noqa: ANN001
        if agent == "code_context":
            return json.dumps(
                {
                    "matched_files": [INVENTED_PATH],
                    "suspected_symbols": ["create_payment"],
                    "missing_files": [],
                    "evidence_ids": [],
                    "needs_human_review": False,
                    "summary": "fabricated",
                }
            )
        return super().complete(agent=agent, prompt=prompt, payload=payload)


class LowConfidenceClient(GroundedDeterministicClient):
    """Grounds every agent but reports an implausibly low triage confidence."""

    def complete(self, *, agent, prompt, payload):  # noqa: ANN001
        if agent == "triage":
            proposal = dict(payload.get("proposal", {}))
            proposal["confidence"] = 0.1
            return json.dumps(proposal)
        return super().complete(agent=agent, prompt=prompt, payload=payload)


class OrderSpyClient(GroundedDeterministicClient):
    """Records the order agents are invoked in."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, agent, prompt, payload):  # noqa: ANN001
        self.calls.append(agent)
        return super().complete(agent=agent, prompt=prompt, payload=payload)


# ---------------------------------------------------------------------------
# Prompt files
# ---------------------------------------------------------------------------


def test_all_agent_prompt_files_exist():
    for name in AGENT_SEQUENCE:
        path = PROMPTS_DIR / f"{name}.md"
        assert path.is_file(), f"missing prompt file: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"empty prompt: {path}"


def test_prompt_files_include_required_grounding_rules():
    expected = [
        "Only cite evidence provided by tools.",
        "If evidence is missing, say insufficient evidence.",
        "Do not claim a root cause without file/log support.",
        "Return valid JSON only.",
    ]
    # The constant and the spec list must stay in sync.
    assert list(REQUIRED_PROMPT_RULES) == expected

    for name in AGENT_SEQUENCE:
        text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        for rule in expected:
            assert rule in text, f"{name}.md is missing required rule: {rule!r}"


# ---------------------------------------------------------------------------
# Sequential flow
# ---------------------------------------------------------------------------


def test_agent_sequence_is_the_required_order():
    assert AGENT_SEQUENCE == (
        "triage",
        "log_investigator",
        "code_context",
        "fix_planner",
        "safety_reviewer",
    )


def test_orchestrator_preserves_sequential_flow():
    spy = OrderSpyClient()
    result = run_agent_investigation("broken_api_route", model=spy)

    # Agents are invoked in the required order...
    assert spy.calls == list(AGENT_SEQUENCE)
    # ...and the recorded outcomes preserve that order.
    assert [o.name for o in result.outcomes] == list(AGENT_SEQUENCE)
    # The Safety Reviewer is always the last agent before report building.
    assert result.outcomes[-1].name == "safety_reviewer"


# ---------------------------------------------------------------------------
# broken_api_route parity: same repo file + same root cause
# ---------------------------------------------------------------------------


def test_broken_api_route_matches_deterministic_file_and_root_cause():
    det = _deterministic("broken_api_route")
    result = run_agent_investigation("broken_api_route")

    assert result.mode == "agent"
    assert result.fallback_reasons == []

    # Same verified repo file as deterministic mode (never invented).
    assert det.code_finding is not None and result.report.code_finding is not None
    assert result.report.code_finding.matched_files == det.code_finding.matched_files
    assert "app/routes/payments.py" in result.report.code_finding.matched_files

    # Same root cause.
    assert det.root_cause is not None and result.report.root_cause is not None
    assert result.report.root_cause.category == det.root_cause.category == "null_dereference"

    # Same classification, and confidence is not weaker.
    assert result.report.primary_error == det.primary_error
    assert result.report.severity == det.severity == "SEV2"
    assert result.report.confidence >= det.confidence


def test_broken_api_route_evidence_is_only_tool_produced():
    """No agent-created evidence: every evidence id traces to deterministic mode."""
    det = _deterministic("broken_api_route")
    result = run_agent_investigation("broken_api_route")

    det_ids = {e.id for e in det.evidence}
    agent_ids = {e.id for e in result.report.evidence}
    assert agent_ids == det_ids
    # Root cause cites only ids that exist in the report's evidence.
    assert result.report.root_cause is not None
    assert set(result.report.root_cause.supporting_evidence_ids).issubset(agent_ids)


# ---------------------------------------------------------------------------
# Fallback: invalid JSON -> deterministic
# ---------------------------------------------------------------------------


def test_invalid_agent_json_falls_back_to_deterministic():
    det = _deterministic("broken_api_route")
    result = run_agent_investigation("broken_api_route", model=InvalidJSONClient())

    assert result.mode == "deterministic_fallback"
    assert result.fallback_reasons  # at least one reason recorded

    # The returned report is exactly the deterministic one (same grounded facts).
    assert result.report.code_finding is not None
    assert result.report.code_finding.matched_files == det.code_finding.matched_files
    assert result.report.root_cause.category == det.root_cause.category
    assert result.report.confidence == det.confidence
    assert result.report.primary_error == det.primary_error


# ---------------------------------------------------------------------------
# Grounding: invented file path is rejected / forces human review
# ---------------------------------------------------------------------------


def test_invented_file_path_is_rejected_and_not_in_report():
    det = _deterministic("broken_api_route")
    result = run_agent_investigation("broken_api_route", model=InventedPathClient())

    # The fabricated path never reaches the report...
    matched = result.report.code_finding.matched_files if result.report.code_finding else []
    assert INVENTED_PATH not in matched
    # ...the only files named are the verified deterministic ones.
    assert matched == det.code_finding.matched_files

    # It was rejected (deterministic fallback) AND the code agent is flagged.
    assert result.mode == "deterministic_fallback"
    code_outcome = next(o for o in result.outcomes if o.name == "code_context")
    assert code_outcome.status == "invalid"
    # The chosen report still requires a human (deterministic-or-stricter).
    assert result.report.needs_human_review == det.needs_human_review


# ---------------------------------------------------------------------------
# Quality: low-confidence agent output never produces a worse report
# ---------------------------------------------------------------------------


def test_low_confidence_agent_output_does_not_worsen_report():
    det = _deterministic("broken_api_route")
    result = run_agent_investigation("broken_api_route", model=LowConfidenceClient())

    # Confidence is never lowered below the deterministic baseline.
    assert result.report.confidence >= det.confidence
    # A weaker candidate is rejected in favor of deterministic mode.
    assert result.mode == "deterministic_fallback"
    assert result.report.root_cause.category == det.root_cause.category
    assert result.report.code_finding.matched_files == det.code_finding.matched_files


# ---------------------------------------------------------------------------
# Secret redaction is unchanged under agent mode
# ---------------------------------------------------------------------------


def test_secret_redaction_behavior_unchanged_in_agent_mode():
    det = _deterministic("secret_in_logs")
    result = run_agent_investigation("secret_in_logs")

    blob = result.report.model_dump_json()
    for secret in RAW_FAKE_SECRETS:
        assert secret not in blob, f"raw secret leaked under agent mode: {secret}"
    assert REDACTION_MARKER in blob

    assert result.report.safety_review is not None
    assert result.report.safety_review.secrets_detected is True
    assert result.report.needs_human_review is True
    # The redaction count is identical to deterministic mode (agents cannot
    # change it).
    assert (
        result.report.safety_review.redactions_applied
        == det.safety_review.redactions_applied
    )


def test_secret_scenario_blocks_github_issue_in_agent_mode():
    result = run_agent_investigation("secret_in_logs")
    assert result.report.safety_review is not None
    # A secret-bearing incident is never eligible for a GitHub issue.
    assert result.report.safety_review.approved_for_github_issue is False


# ---------------------------------------------------------------------------
# Ambiguous incident: agents must not fabricate a confident diagnosis
# ---------------------------------------------------------------------------


def test_ambiguous_incident_stays_escalated_in_agent_mode():
    det = _deterministic("ambiguous_error")
    result = run_agent_investigation("ambiguous_error")

    # Escalation is preserved and never downgraded.
    assert result.report.needs_human_review is True
    assert result.report.confidence <= det.confidence + 1e-9
    assert result.report.confidence < 0.6
    assert result.report.fix_plan is None
    # The fix planner correctly declines for lack of a grounded root cause.
    fix_outcome = next(o for o in result.outcomes if o.name == "fix_planner")
    assert fix_outcome.status == "insufficient_evidence"


# ---------------------------------------------------------------------------
# Agent mode is optional and writes nothing external
# ---------------------------------------------------------------------------


def test_agent_mode_is_off_by_default_in_settings():
    assert Settings().agent_mode is False


def test_agent_mode_never_enables_a_pr_or_external_write():
    for scenario in ("broken_api_route", "secret_in_logs", "ambiguous_error"):
        store.reset_store()
        result = run_agent_investigation(scenario)
        assert result.report.safety_review is not None
        # No phase-6 path may approve a PR or any external write.
        assert result.report.safety_review.approved_for_pr is False


def test_deterministic_service_is_unchanged_when_agents_unused():
    """Importing/using the agent layer does not alter deterministic output."""
    before = svc.investigate_incident("broken_api_route", persist=False)
    store.reset_store()
    run_agent_investigation("broken_api_route")
    store.reset_store()
    after = svc.investigate_incident("broken_api_route", persist=False)

    # Compare everything except the wall-clock timestamps, which are expected to
    # differ between two independent investigations.
    skip = {"created_at", "updated_at"}
    assert before.model_dump(exclude=skip) == after.model_dump(exclude=skip)


# ---------------------------------------------------------------------------
# Phase 6.1: the JSON each prompt documents must be accepted by its parser
#
# These tests pin the prompt/parser contract. A stub client emits the *exact*
# shape documented in each prompt's "Output JSON shape" section (field names and
# types written out explicitly, with grounded values from the proposal). If a
# parser ever stops accepting the documented shape — or a prompt drifts back to
# the old object shapes — these fail loudly instead of silently degrading agent
# mode into a permanent deterministic fallback.
# ---------------------------------------------------------------------------


class DocumentedShapeClient(GroundedDeterministicClient):
    """Emit each agent's output in the exact JSON shape its prompt documents."""

    def complete(self, *, agent, prompt, payload):  # noqa: ANN001
        p = dict(payload.get("proposal", {}))
        if agent == "triage":
            return json.dumps(
                {
                    "severity": p.get("severity"),
                    "affected_service": p.get("affected_service"),
                    "primary_error": p.get("primary_error"),
                    "confidence": p.get("confidence"),
                    "needs_human_review": p.get("needs_human_review", True),
                    "summary": p.get("summary"),
                }
            )
        if agent == "log_investigator":
            return json.dumps(
                {
                    "primary_error": p.get("primary_error"),
                    "failing_test": p.get("failing_test"),
                    "stack_trace_summary": p.get("stack_trace_summary"),
                    "redactions_applied": p.get("redactions_applied", 0),
                    "evidence_ids": list(p.get("evidence_ids", [])),
                    "needs_human_review": p.get("needs_human_review", True),
                    "summary": p.get("summary"),
                }
            )
        if agent == "code_context":
            return json.dumps(
                {
                    "matched_files": list(p.get("matched_files", [])),
                    "suspected_symbols": list(p.get("suspected_symbols", [])),
                    "missing_files": list(p.get("missing_files", [])),
                    "evidence_ids": list(p.get("evidence_ids", [])),
                    "needs_human_review": p.get("needs_human_review", True),
                    "summary": p.get("summary"),
                }
            )
        if agent == "fix_planner":
            # The documented shape is the nested root_cause/fix_plan objects the
            # proposal already carries, with a top-level needs_human_review.
            return json.dumps(p)
        if agent == "safety_reviewer":
            return json.dumps(
                {
                    "approved_for_display": p.get("approved_for_display"),
                    "approved_for_github_issue": p.get("approved_for_github_issue"),
                    "approved_for_pr": p.get("approved_for_pr", False),
                    "risk_level": p.get("risk_level"),
                    "secrets_detected": p.get("secrets_detected", False),
                    "redactions_applied": p.get("redactions_applied", 0),
                    "needs_human_review": p.get("needs_human_review", True),
                    "summary": p.get("summary"),
                }
            )
        return super().complete(agent=agent, prompt=prompt, payload=payload)


def test_documented_prompt_shapes_are_accepted_and_keep_parity():
    """Every prompt-documented shape is accepted; broken_api_route parity holds."""
    det = _deterministic("broken_api_route")
    result = run_agent_investigation("broken_api_route", model=DocumentedShapeClient())

    # No parser rejected its own documented contract -> every step is ok, and
    # the agent report (not deterministic fallback) is chosen.
    assert [o.status for o in result.outcomes] == ["ok"] * len(AGENT_SEQUENCE)
    assert result.mode == "agent"
    assert result.fallback_reasons == []

    # Same verified repo file and same root cause as deterministic mode.
    assert result.report.code_finding.matched_files == det.code_finding.matched_files
    assert "app/routes/payments.py" in result.report.code_finding.matched_files
    assert result.report.root_cause.category == det.root_cause.category
    assert result.report.severity == det.severity
    assert result.report.confidence >= det.confidence


# ---- code_context.md: matched_files is a list of strings, never objects -----


class CodeContextObjectShapeClient(GroundedDeterministicClient):
    """Regression guard: matched_files as objects (the pre-6.1 prompt drift)."""

    def complete(self, *, agent, prompt, payload):  # noqa: ANN001
        if agent == "code_context":
            p = dict(payload.get("proposal", {}))
            return json.dumps(
                {
                    "matched_files": [
                        {"path": path, "path_verified": True}
                        for path in p.get("matched_files", [])
                    ],
                    "suspected_symbols": [],
                    "missing_files": [],
                    "evidence_ids": [],
                    "needs_human_review": False,
                    "summary": "object-shaped matched_files",
                }
            )
        return super().complete(agent=agent, prompt=prompt, payload=payload)


def test_code_context_object_matched_files_shape_is_rejected():
    det = _deterministic("broken_api_route")
    result = run_agent_investigation(
        "broken_api_route", model=CodeContextObjectShapeClient()
    )

    code = next(o for o in result.outcomes if o.name == "code_context")
    assert code.status == "invalid"
    assert result.mode == "deterministic_fallback"
    # The real verified file still wins; no object shape leaks into the report.
    assert result.report.code_finding.matched_files == det.code_finding.matched_files


def test_code_context_string_matched_files_shape_is_accepted():
    det = _deterministic("broken_api_route")
    index = EvidenceIndex.from_report(det)
    documented = json.dumps(
        {
            "matched_files": list(det.code_finding.matched_files),
            "suspected_symbols": list(det.code_finding.suspected_symbols),
            "missing_files": [],
            "evidence_ids": [e.id for e in det.code_finding.evidence],
            "needs_human_review": False,
            "summary": "string-shaped matched_files",
        }
    )

    class _Client(GroundedDeterministicClient):
        def complete(self, *, agent, prompt, payload):  # noqa: ANN001
            return documented

    outcome = CodeContextAgent().run(det, index, _Client())
    assert outcome.status == "ok"
    assert outcome.data["matched_files"] == list(det.code_finding.matched_files)


# ---- log_investigator.md: evidence is referenced by id, not as objects ------


def test_log_investigator_accepts_documented_evidence_ids():
    det = _deterministic("broken_api_route")
    index = EvidenceIndex.from_report(det)
    grounded_ids = [e.id for e in det.log_finding.evidence]
    documented = json.dumps(
        {
            "primary_error": det.log_finding.primary_error,
            "failing_test": det.log_finding.failing_test,
            "stack_trace_summary": det.log_finding.stack_trace_summary,
            "redactions_applied": det.log_finding.redactions_applied,
            "evidence_ids": grounded_ids,
            "needs_human_review": False,
            "summary": "documented evidence_ids shape",
        }
    )

    class _Client(GroundedDeterministicClient):
        def complete(self, *, agent, prompt, payload):  # noqa: ANN001
            return documented

    outcome = LogInvestigatorAgent().run(det, index, _Client())
    assert outcome.status == "ok"
    assert outcome.data["evidence_ids"] == grounded_ids


def test_log_investigator_rejects_ungrounded_evidence_ids():
    det = _deterministic("broken_api_route")
    index = EvidenceIndex.from_report(det)
    ungrounded = json.dumps(
        {
            "primary_error": det.log_finding.primary_error,
            "failing_test": det.log_finding.failing_test,
            "stack_trace_summary": det.log_finding.stack_trace_summary,
            "redactions_applied": det.log_finding.redactions_applied,
            "evidence_ids": ["ev_not_produced_by_any_tool"],
            "needs_human_review": False,
            "summary": "ungrounded evidence id",
        }
    )

    class _Client(GroundedDeterministicClient):
        def complete(self, *, agent, prompt, payload):  # noqa: ANN001
            return ungrounded

    outcome = LogInvestigatorAgent().run(det, index, _Client())
    assert outcome.status == "invalid"
