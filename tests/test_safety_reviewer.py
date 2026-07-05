"""Phase 7 — deterministic safety gate and action gating.

These tests pin the safety reviewer's *deterministic* behavior: the Python gate
in :mod:`app.services.safety_gate` is the single source of truth for whether a
report may be displayed, filed as a GitHub issue, or used for a PR. They cover

* the structured ``SafetyChecks`` shape and the exact, stable blocked-reason
  strings,
* the three-tier confidence policy (>=0.75 eligible, 0.50–0.75 blocked, <0.50
  low-confidence diagnostic),
* secret handling and unverified file references,
* determinism,
* the action gate — unsafe GitHub issue creation is blocked *before* any GitHub
  client call, a PR is never allowed, and no real external write happens, and
* authority — the gate reads the structured checks, so flipping a single
  approval boolean (as a misbehaving LLM/agent might) cannot authorize a blocked
  action.

No LLM, no network, no real GitHub write.
"""

from __future__ import annotations

import pytest

from app.schemas.approval import ApprovalRecord, GitHubIssueOptions
from app.schemas.report import IncidentReport
from app.schemas.safety import SafetyChecks, SafetyReview
from app.services import investigation_service as svc
from app.services import safety_gate
from app.services.errors import ApprovalRequired, SafetyBlocked
from app.services.safety_gate import (
    REASON_CONFIDENCE_BELOW_THRESHOLD,
    REASON_DIRECT_PRODUCTION_CHANGE,
    REASON_HUMAN_APPROVAL_REQUIRED,
    REASON_REPO_PATHS_UNVERIFIED,
    REASON_SECRET_BEARING_SOURCE,
    REASON_SECRETS_NOT_REDACTED,
    REASON_UNVERIFIED_FILE_REFERENCE,
    SAFETY_ISSUE_CONFIDENCE_THRESHOLD,
    assert_github_issue_allowed,
    checks_blocked_reasons,
    evaluate_safety,
    github_issue_block_reasons,
)
from app.storage import incident_store as store

# Fake secrets seeded into demo/incidents/secret_in_logs; none may ever appear
# raw in a safety verdict or a blocked-reason error message.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]

# The six structured checks, by exact field name. Pinned so the required output
# shape can never drift silently.
REQUIRED_CHECK_FIELDS = {
    "secrets_redacted",
    "repo_paths_verified",
    "confidence_above_threshold",
    "human_approval_required",
    "no_direct_production_change",
    "no_unverified_file_references",
}


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset_store()
    yield
    store.reset_store()


def _report(scenario: str) -> IncidentReport:
    """Investigate ``scenario`` from a clean store and return its report."""
    store.reset_store()
    return svc.investigate_incident(scenario, persist=False)


# ---------------------------------------------------------------------------
# Structured checks + exact blocked-reason strings
# ---------------------------------------------------------------------------


def test_safety_checks_fields_match_required_shape():
    """The structured ``checks`` object exposes exactly the six required fields."""
    assert set(SafetyChecks.model_fields) == REQUIRED_CHECK_FIELDS


def test_all_checks_pass_yields_no_blocked_reasons():
    checks = SafetyChecks(
        secrets_redacted=True,
        repo_paths_verified=True,
        confidence_above_threshold=True,
        human_approval_required=True,
        no_direct_production_change=True,
        no_unverified_file_references=True,
    )
    assert checks_blocked_reasons(checks) == []


def test_each_failing_check_maps_to_its_exact_reason():
    cases = [
        ("secrets_redacted", REASON_SECRETS_NOT_REDACTED),
        ("repo_paths_verified", REASON_REPO_PATHS_UNVERIFIED),
        ("confidence_above_threshold", REASON_CONFIDENCE_BELOW_THRESHOLD),
        ("no_unverified_file_references", REASON_UNVERIFIED_FILE_REFERENCE),
        ("no_direct_production_change", REASON_DIRECT_PRODUCTION_CHANGE),
        ("human_approval_required", REASON_HUMAN_APPROVAL_REQUIRED),
    ]
    base = dict(
        secrets_redacted=True,
        repo_paths_verified=True,
        confidence_above_threshold=True,
        human_approval_required=True,
        no_direct_production_change=True,
        no_unverified_file_references=True,
    )
    for field, reason in cases:
        # ``no_*`` and ``*_required`` style fields fail when set to False.
        failing = dict(base)
        failing[field] = False
        reasons = checks_blocked_reasons(SafetyChecks(**failing))
        assert reasons == [reason], f"{field} should map only to {reason!r}"


# ---------------------------------------------------------------------------
# evaluate_safety: the happy, fully-grounded, high-confidence path
# ---------------------------------------------------------------------------


def test_clean_grounded_high_confidence_is_eligible():
    review = evaluate_safety(
        secrets_present=False,
        unverified_file_reference=False,
        confidence=0.90,
    )
    assert review.approved_for_display is True
    assert review.approved_for_github_issue is True  # eligible (pending approval)
    assert review.approved_for_pr is False
    assert review.risk_level == "low"
    assert review.blocked_reasons == []
    assert review.needs_human_review is False
    assert all(review.checks.model_dump().values())  # every invariant holds


def test_pr_is_never_approved_even_at_max_confidence():
    review = evaluate_safety(
        secrets_present=False,
        unverified_file_reference=False,
        confidence=1.0,
    )
    assert review.approved_for_pr is False


# ---------------------------------------------------------------------------
# Confidence policy — three tiers around the 0.75 issue threshold
# ---------------------------------------------------------------------------


def test_threshold_constant_is_three_quarters():
    assert SAFETY_ISSUE_CONFIDENCE_THRESHOLD == 0.75


def test_confidence_at_threshold_is_eligible():
    review = evaluate_safety(
        secrets_present=False, unverified_file_reference=False, confidence=0.75
    )
    assert review.checks.confidence_above_threshold is True
    assert review.approved_for_github_issue is True


def test_mid_confidence_blocks_issue_but_allows_display():
    # 0.50 <= confidence < 0.75: report shown, human review required, issue blocked.
    review = evaluate_safety(
        secrets_present=False, unverified_file_reference=False, confidence=0.60
    )
    assert review.approved_for_display is True
    assert review.approved_for_github_issue is False
    assert review.approved_for_pr is False
    assert review.needs_human_review is True
    assert REASON_CONFIDENCE_BELOW_THRESHOLD in review.blocked_reasons
    assert review.checks.confidence_above_threshold is False


def test_low_confidence_is_diagnostic_only_and_explains_itself():
    # confidence < 0.50: display allowed as a low-confidence diagnostic, every
    # external action blocked, and the low confidence is stated explicitly.
    review = evaluate_safety(
        secrets_present=False, unverified_file_reference=False, confidence=0.20
    )
    assert review.approved_for_display is True
    assert review.approved_for_github_issue is False
    assert review.approved_for_pr is False
    assert review.needs_human_review is True
    assert REASON_CONFIDENCE_BELOW_THRESHOLD in review.blocked_reasons


# ---------------------------------------------------------------------------
# Secrets + unverified file references
# ---------------------------------------------------------------------------


def test_secret_bearing_source_is_high_risk_and_blocked_even_when_redacted():
    review = evaluate_safety(
        secrets_present=True,
        unverified_file_reference=False,
        confidence=0.90,  # high confidence must NOT rescue a secret-bearing source
        redactions_applied=4,
    )
    assert review.risk_level == "high"
    assert review.approved_for_github_issue is False
    assert review.needs_human_review is True
    assert review.secrets_detected is True
    # The secrets were redacted (no leak), so that invariant still passes...
    assert review.checks.secrets_redacted is True
    # ...but the untrusted source blocks an automated external write.
    assert REASON_SECRET_BEARING_SOURCE in review.blocked_reasons


def test_unredacted_secret_fails_the_secrets_check():
    review = evaluate_safety(
        secrets_present=True,
        unverified_file_reference=False,
        confidence=0.90,
        secrets_fully_redacted=False,
    )
    assert review.checks.secrets_redacted is False
    assert REASON_SECRETS_NOT_REDACTED in review.blocked_reasons
    assert review.approved_for_github_issue is False


def test_unverified_file_reference_fails_path_and_reference_checks():
    review = evaluate_safety(
        secrets_present=False,
        unverified_file_reference=True,
        confidence=0.90,
    )
    assert review.checks.repo_paths_verified is False
    assert review.checks.no_unverified_file_references is False
    assert REASON_REPO_PATHS_UNVERIFIED in review.blocked_reasons
    assert REASON_UNVERIFIED_FILE_REFERENCE in review.blocked_reasons
    assert review.approved_for_github_issue is False


def test_required_output_shape_serializes_with_checks_block():
    """Mirrors the Phase 7 required SafetyReview shape, including nested checks."""
    review = evaluate_safety(
        secrets_present=False,
        unverified_file_reference=True,
        confidence=0.40,
    )
    dumped = review.model_dump()
    for key in (
        "approved_for_display",
        "approved_for_github_issue",
        "approved_for_pr",
        "risk_level",
        "checks",
        "blocked_reasons",
        "required_human_action",
    ):
        assert key in dumped
    assert set(dumped["checks"]) == REQUIRED_CHECK_FIELDS
    assert REASON_CONFIDENCE_BELOW_THRESHOLD in dumped["blocked_reasons"]
    assert REASON_UNVERIFIED_FILE_REFERENCE in dumped["blocked_reasons"]
    assert dumped["required_human_action"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_evaluate_safety_is_deterministic():
    kwargs = dict(
        secrets_present=True,
        unverified_file_reference=True,
        confidence=0.42,
        redactions_applied=3,
    )
    first = evaluate_safety(**kwargs)
    second = evaluate_safety(**kwargs)
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Integration with the deterministic investigation pipeline
# ---------------------------------------------------------------------------


def test_broken_api_route_report_is_issue_eligible():
    report = _report("broken_api_route")
    review = report.safety_review
    assert review is not None
    assert review.checks.model_dump() == {
        "secrets_redacted": True,
        "repo_paths_verified": True,
        "confidence_above_threshold": True,
        "human_approval_required": True,
        "no_direct_production_change": True,
        "no_unverified_file_references": True,
    }
    assert review.approved_for_github_issue is True
    assert github_issue_block_reasons(report) == []


def test_ambiguous_report_blocked_only_by_confidence():
    report = _report("ambiguous_error")
    assert github_issue_block_reasons(report) == [REASON_CONFIDENCE_BELOW_THRESHOLD]
    assert report.safety_review.approved_for_github_issue is False


def test_secret_report_blocked_and_high_risk():
    report = _report("secret_in_logs")
    review = report.safety_review
    assert review.risk_level == "high"
    assert review.approved_for_github_issue is False
    reasons = github_issue_block_reasons(report)
    assert REASON_SECRET_BEARING_SOURCE in reasons
    assert REASON_CONFIDENCE_BELOW_THRESHOLD in reasons


# ---------------------------------------------------------------------------
# Action gate — assert_github_issue_allowed
# ---------------------------------------------------------------------------


def _approved_record(incident_id: str) -> ApprovalRecord:
    return ApprovalRecord(incident_id=incident_id, approved=True, approved_by="tester")


def test_gate_allows_clean_report_with_approval():
    report = _report("broken_api_route")
    # Should not raise.
    assert_github_issue_allowed(report, _approved_record(report.incident_id))


def test_gate_requires_human_approval_for_clean_report():
    report = _report("broken_api_route")
    with pytest.raises(ApprovalRequired) as exc:
        assert_github_issue_allowed(report, None)
    assert "approval" in str(exc.value).lower()


def test_gate_rejects_unapproved_record():
    report = _report("broken_api_route")
    record = ApprovalRecord(incident_id=report.incident_id, approved=False)
    with pytest.raises(ApprovalRequired):
        assert_github_issue_allowed(report, record)


def test_gate_blocks_unsafe_report_before_approval_is_considered():
    report = _report("secret_in_logs")
    # Even WITH an approval on file, safety blocks first.
    with pytest.raises(SafetyBlocked) as exc:
        assert_github_issue_allowed(report, _approved_record(report.incident_id))
    msg = str(exc.value)
    assert "safety" in msg.lower()
    # The block message must never leak a raw secret.
    for secret in RAW_FAKE_SECRETS:
        assert secret not in msg


def test_gate_is_authoritative_over_a_flipped_approval_boolean():
    """A misbehaving agent/LLM flipping the flat approval cannot authorize a block.

    The gate re-derives its verdict from the structured ``checks``, so tampering
    with the convenience boolean has no effect.
    """
    report = _report("ambiguous_error")
    assert report.safety_review is not None
    report.safety_review.approved_for_github_issue = True  # simulate tampering
    with pytest.raises(SafetyBlocked):
        assert_github_issue_allowed(report, _approved_record(report.incident_id))


def test_missing_safety_review_is_blocked():
    report = _report("broken_api_route")
    report.safety_review = None
    assert github_issue_block_reasons(report)  # non-empty: not eligible
    with pytest.raises(SafetyBlocked):
        assert_github_issue_allowed(report, _approved_record(report.incident_id))


# ---------------------------------------------------------------------------
# End-to-end through investigation_service.create_github_issue (no real write)
# ---------------------------------------------------------------------------


def test_create_github_issue_blocked_for_secret_scenario_even_after_approval():
    svc.investigate_incident("secret_in_logs", persist=False)
    svc.record_github_approval("inc_002", approved=True, approved_by="tester", note=None)
    with pytest.raises(SafetyBlocked):
        svc.create_github_issue(
            "inc_002", GitHubIssueOptions(), github_configured=True, env_dry_run=False
        )


def test_create_github_issue_requires_approval_for_clean_scenario():
    svc.investigate_incident("broken_api_route", persist=False)
    with pytest.raises(ApprovalRequired):
        svc.create_github_issue(
            "inc_001", GitHubIssueOptions(), github_configured=True, env_dry_run=False
        )


def test_create_github_issue_never_writes_even_when_configured_and_not_dry_run():
    """An approved, eligible report still performs no real GitHub write."""
    svc.investigate_incident("broken_api_route", persist=False)
    svc.record_github_approval("inc_001", approved=True, approved_by="tester", note=None)

    result = svc.create_github_issue(
        "inc_001",
        GitHubIssueOptions(dry_run=False),
        github_configured=True,
        env_dry_run=False,
    )
    # No network write occurs in this build: creation is "not_implemented".
    assert result.created is False
    assert result.dry_run is False
    assert result.issue_url is None
    assert result.issue_number is None


def test_create_github_issue_dry_run_preview_after_approval():
    svc.investigate_incident("broken_api_route", persist=False)
    svc.record_github_approval("inc_001", approved=True, approved_by="tester", note=None)

    result = svc.create_github_issue(
        "inc_001", GitHubIssueOptions(), github_configured=True, env_dry_run=True
    )
    assert result.created is False
    assert result.dry_run is True
    assert result.title.startswith("IncidentPilot:")


# ---------------------------------------------------------------------------
# The report explains why an unsafe action was blocked
# ---------------------------------------------------------------------------


def test_blocked_report_explains_itself():
    report = _report("secret_in_logs")
    review = report.safety_review
    assert review.blocked_reasons, "a blocked report must list its reasons"
    assert review.required_human_action
    # The human-facing summary states the issue is blocked and stays secret-free.
    assert "blocked" in review.summary.lower()
    for secret in RAW_FAKE_SECRETS:
        assert secret not in review.summary


# ===========================================================================
# Phase 7 — required safety scenarios (fixture-driven, end-to-end)
#
# Each scenario proves a *different* safety behavior through the real
# deterministic pipeline. Incident ids are read from the returned report
# (never hard-coded) so these stay correct regardless of the store's id map.
# ===========================================================================


# Representative confidences for each policy band. 0.75 and 0.50 pin the exact
# band boundaries; ``issue_eligible`` is True only at/above the 0.75 threshold.
@pytest.mark.parametrize(
    ("confidence", "issue_eligible"),
    [
        (1.0, True),
        (0.95, True),
        (0.80, True),
        (0.75, True),  # boundary: at-threshold is eligible
        (0.74, False),
        (0.60, False),
        (0.50, False),  # boundary: mid band, issue blocked
        (0.49, False),
        (0.30, False),
        (0.0, False),
    ],
)
def test_confidence_bands_match_policy(confidence: float, issue_eligible: bool):
    """The deterministic gate enforces the three-tier confidence policy exactly:

    * confidence >= 0.75  -> issue eligible (still pending human approval)
    * 0.50 <= conf < 0.75 -> report shown, issue blocked, human review required
    * confidence < 0.50   -> low-confidence diagnostic, issue and PR blocked

    A clean report is always displayable and a PR is never auto-approved.
    """
    review = evaluate_safety(
        secrets_present=False, unverified_file_reference=False, confidence=confidence
    )
    assert review.approved_for_display is True
    assert review.approved_for_pr is False
    assert review.approved_for_github_issue is issue_eligible
    assert review.checks.confidence_above_threshold is issue_eligible
    if issue_eligible:
        assert review.blocked_reasons == []
        assert REASON_CONFIDENCE_BELOW_THRESHOLD not in review.blocked_reasons
        assert review.needs_human_review is False
    else:
        assert REASON_CONFIDENCE_BELOW_THRESHOLD in review.blocked_reasons
        assert review.needs_human_review is True


def test_secret_in_logs_redacts_and_does_not_leak_raw_secret():
    """secret_in_logs: secrets are redacted, never leaked, and never auto-shared."""
    report = _report("secret_in_logs")
    review = report.safety_review
    assert review is not None

    # Secrets were detected and redacted (redactions_applied > 0), and no
    # unredacted secret remains anywhere in the report.
    assert review.secrets_detected is True
    assert review.redactions_applied > 0
    assert review.checks.secrets_redacted is True

    # No raw fake secret may appear ANYWHERE in the serialized report tree.
    blob = report.model_dump_json()
    leaked = [secret for secret in RAW_FAKE_SECRETS if secret in blob]
    assert leaked == [], f"raw secret leaked into report: {leaked}"

    # A GitHub issue is only allowed when every gate passes AND a human approves;
    # a secret-bearing source is never auto-eligible, even fully redacted.
    assert review.approved_for_github_issue is False
    assert REASON_SECRET_BEARING_SOURCE in github_issue_block_reasons(report)


def test_ambiguous_error_blocks_github_issue_for_low_confidence():
    """ambiguous_error: weak/vague evidence -> low confidence -> issue blocked."""
    report = _report("ambiguous_error")
    review = report.safety_review
    assert review is not None

    # Confidence is below the issue-eligibility threshold (the system's
    # low-confidence behavior); no secrets and no unverified path are involved.
    assert report.confidence < SAFETY_ISSUE_CONFIDENCE_THRESHOLD
    assert review.approved_for_github_issue is False
    assert REASON_CONFIDENCE_BELOW_THRESHOLD in review.blocked_reasons
    # Confidence is the *only* thing blocking it.
    assert github_issue_block_reasons(report) == [REASON_CONFIDENCE_BELOW_THRESHOLD]


def test_wrong_repo_path_blocks_unverified_file_reference():
    """wrong_repo_path: a stack frame cites a repo file absent from the snapshot."""
    report = _report("wrong_repo_path")
    review = report.safety_review
    assert review is not None

    # The report references repo file(s) that could not be verified to exist.
    assert report.code_finding is not None
    assert report.code_finding.missing_files, "expected unverified repo file(s)"
    assert report.code_finding.matched_files == []

    # Both path invariants fail (flat + structured), and the action is blocked.
    assert review.repo_paths_verified is False
    assert review.checks.repo_paths_verified is False
    assert review.checks.no_unverified_file_references is False
    assert review.approved_for_github_issue is False
    assert REASON_UNVERIFIED_FILE_REFERENCE in review.blocked_reasons
    assert REASON_REPO_PATHS_UNVERIFIED in review.blocked_reasons


def test_path_traversal_scenario_blocks_github_issue():
    """path_traversal: a ``../../`` frame is rejected by the guard, never read.

    The traversal frame cannot be grounded (the path guard refuses it before any
    file is opened), so it surfaces as an unverified reference and the report is
    blocked from any external write. The guard-level proof that nothing outside
    the repo root is read lives in ``test_path_guard.py``.
    """
    report = _report("path_traversal")
    review = report.safety_review
    assert review is not None

    # The traversal path is recorded as a missing/unverified reference, proving
    # it was refused rather than read.
    assert report.code_finding is not None
    assert any(".." in path for path in report.code_finding.missing_files)

    assert review.approved_for_github_issue is False
    assert (
        REASON_REPO_PATHS_UNVERIFIED in review.blocked_reasons
        or REASON_UNVERIFIED_FILE_REFERENCE in review.blocked_reasons
    )


def test_low_confidence_report_blocks_issue_and_pr_actions():
    """low_confidence_report: a clean report under 0.50 — display ok, writes not."""
    report = _report("low_confidence_report")
    review = report.safety_review
    assert review is not None

    assert report.confidence < 0.50
    # Display is allowed (redacted, safe); external writes are blocked.
    assert review.approved_for_display is True
    assert review.approved_for_github_issue is False
    assert review.approved_for_pr is False
    # This report is clean: confidence is the single, exact block reason.
    assert review.blocked_reasons == [REASON_CONFIDENCE_BELOW_THRESHOLD]

    # The action gate also blocks issue creation even after a human approval.
    svc.record_github_approval(
        report.incident_id, approved=True, approved_by="tester", note=None
    )
    with pytest.raises(SafetyBlocked):
        svc.create_github_issue(
            report.incident_id,
            GitHubIssueOptions(),
            github_configured=True,
            env_dry_run=False,
        )


def test_github_issue_service_does_not_call_client_when_safety_blocks(monkeypatch):
    """When safety blocks, no issue payload is built and no external write runs.

    There is no live GitHub client in this build; the issue-preview builder is
    the first side-effecting step *after* the gate, so spying on it proves the
    deterministic safety gate short-circuits before any client could be reached.
    A human approval is recorded first, so only the gate can stop the action.
    """
    report = svc.investigate_incident("secret_in_logs", persist=False)
    svc.record_github_approval(
        report.incident_id, approved=True, approved_by="tester", note=None
    )

    calls: list = []
    real_builder = svc._build_issue_preview

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(svc, "_build_issue_preview", _spy)

    with pytest.raises(SafetyBlocked):
        svc.create_github_issue(
            report.incident_id,
            GitHubIssueOptions(),
            github_configured=True,
            env_dry_run=False,
        )

    assert calls == [], "issue/client work must not run when safety blocks"
