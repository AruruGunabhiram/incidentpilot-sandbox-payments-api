"""Phase 1 schema contract tests.

Only data contracts are exercised here: no agents, no services, no API.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# 1 + 2. Import check / minimum model imports.
from app.schemas.incident import IncidentIntake
from app.schemas.findings import (
    CodeFinding,
    EvidenceItem,
    FixPlan,
    LogFinding,
    RootCauseHypothesis,
)
from app.schemas.safety import SafetyReview
from app.schemas.report import IncidentReport
from app.schemas.approval import ApprovalRequest, GitHubIssueRequest


def _evidence() -> EvidenceItem:
    return EvidenceItem(
        id="ev_1",
        source="ci",
        source_type="ci_log",
        summary="pytest run failed in CI",
    )


def test_models_are_importable() -> None:
    """All Phase 1 models import and are real Pydantic model classes."""
    for model in (
        IncidentIntake,
        EvidenceItem,
        LogFinding,
        CodeFinding,
        RootCauseHypothesis,
        FixPlan,
        SafetyReview,
        IncidentReport,
        ApprovalRequest,
        GitHubIssueRequest,
    ):
        assert hasattr(model, "model_validate")


def test_incident_report_minimal_realistic_instantiation() -> None:
    """A valid IncidentReport builds from minimal realistic nested data."""
    evidence = _evidence()
    report = IncidentReport(
        incident_id="inc_001",
        title="Checkout API returning 500s",
        severity="SEV2",
        affected_service="checkout-api",
        status="investigating",
        summary="Checkout endpoint fails after the latest deploy.",
        confidence=0.6,
        evidence=[evidence],
        log_finding=LogFinding(
            summary="AssertionError in test_checkout",
            confidence=0.7,
            evidence=[evidence],
            primary_error="AssertionError",
            failing_test="tests/test_checkout.py::test_total",
            redactions_applied=1,
        ),
        code_finding=CodeFinding(
            summary="Suspect total() helper",
            evidence=[evidence],
            matched_files=["app/checkout.py"],
            suspected_symbols=["total"],
        ),
        root_cause=RootCauseHypothesis(
            summary="Off-by-one in total computation",
            category="logic_error",
            supporting_evidence_ids=["ev_1"],
        ),
        fix_plan=FixPlan(
            summary="Correct the total computation",
            patch_strategy="patch",
            steps=["Fix total()", "Add regression test"],
            regression_tests=["tests/test_checkout.py::test_total"],
        ),
        safety_review=SafetyReview(
            summary="Display only; human approval required.",
            approved_for_display=True,
        ),
    )

    assert report.incident_id == "inc_001"
    assert report.severity == "SEV2"
    assert report.status == "investigating"
    assert report.log_finding is not None
    assert report.log_finding.primary_error == "AssertionError"
    assert report.fix_plan is not None and report.fix_plan.patch_strategy == "patch"
    assert report.safety_review is not None
    assert report.safety_review.human_approval_required is True


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.1])
def test_confidence_rejects_out_of_range(bad_confidence: float) -> None:
    """confidence must stay within [0, 1] on every reviewed model."""
    with pytest.raises(ValidationError):
        LogFinding(summary="x", confidence=bad_confidence)
    with pytest.raises(ValidationError):
        IncidentReport(
            incident_id="inc_001",
            title="t",
            severity="UNKNOWN",
            affected_service="svc",
            status="created",
            summary="s",
            confidence=bad_confidence,
        )


def test_confidence_accepts_boundaries() -> None:
    """0.0 and 1.0 are valid confidence values."""
    assert LogFinding(summary="x", confidence=0.0).confidence == 0.0
    assert LogFinding(summary="x", confidence=1.0).confidence == 1.0


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (EvidenceItem, dict(id="e", source="s", source_type="ci_log", summary="x")),
        (LogFinding, dict(summary="x")),
        (SafetyReview, dict()),
        (GitHubIssueRequest, dict(incident_id="i", title="t", body="b")),
        (
            IncidentReport,
            dict(
                incident_id="i",
                title="t",
                severity="UNKNOWN",
                affected_service="svc",
                status="created",
                summary="s",
            ),
        ),
    ],
)
def test_extra_unknown_fields_are_rejected(model: type, kwargs: dict) -> None:
    """Schemas forbid unexpected fields (untrusted input hardening)."""
    with pytest.raises(ValidationError):
        model(**{**kwargs, "definitely_not_a_field": "nope"})


def test_evidence_defaults_to_empty_list() -> None:
    """evidence defaults to [] on reviewed models when omitted."""
    assert LogFinding(summary="x").evidence == []
    assert CodeFinding(summary="x").evidence == []
    assert RootCauseHypothesis(category="logic_error").evidence == []
    assert FixPlan(patch_strategy="patch").evidence == []
    assert SafetyReview().evidence == []
    report = IncidentReport(
        incident_id="i",
        title="t",
        severity="UNKNOWN",
        affected_service="svc",
        status="created",
        summary="s",
    )
    assert report.evidence == []


def test_github_issue_request_defaults_dry_run_true() -> None:
    """GitHubIssueRequest must default to a dry run (no write without approval)."""
    request = GitHubIssueRequest(incident_id="inc_001", title="t", body="b")
    assert request.dry_run is True
    assert request.labels == []


def test_incident_intake_minimal() -> None:
    """IncidentIntake builds from its required fields and defaults the rest."""
    intake = IncidentIntake(
        incident_id="inc_001",
        scenario="broken_api_route",
        service="checkout-api",
        trigger_type="api_error",
        summary="500s on checkout",
    )
    assert intake.signals == []
    assert intake.repo_owner is None
    assert intake.created_at is not None
