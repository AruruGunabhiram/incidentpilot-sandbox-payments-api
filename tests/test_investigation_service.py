"""Tests for the deterministic investigation service (Phase 5).

These exercise :func:`app.services.investigation_service.investigate_incident`
directly — no FastAPI, no agents, no LLM, no network, no GitHub writes. The
service orchestrates the deterministic tools (CI log reader, redactor, repo
search, report writer, durable store) into a single grounded report and persists
a redacted JSON + Markdown copy to disk.

Every persisting test targets an isolated ``tmp_path`` via the keyword-only
``reports_dir`` override, so the real ``app/storage/reports/`` directory is never
touched and runs are deterministic. The in-memory store is module-global, so it
is reset before and after each test.
"""

from __future__ import annotations

import pytest

from app.schemas.report import IncidentReport
from app.services import investigation_service as svc
from app.services.errors import IncidentNotFound
from app.storage import incident_store as store
from app.tools.redactor import REDACTION_MARKER

# Fake secrets seeded into demo/incidents/secret_in_logs. None of these may ever
# appear raw in the report, the saved JSON, or the saved Markdown.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


@pytest.fixture(autouse=True)
def _reset_store():
    """Start and end every test from a clean in-memory control plane."""
    store.reset_store()
    yield
    store.reset_store()


# ---------------------------------------------------------------------------
# broken_api_route: the happy, fully grounded path
# ---------------------------------------------------------------------------


def test_broken_api_route_generates_complete_report():
    """A grounded scenario fills in every required report section."""
    report = svc.investigate_incident("broken_api_route", persist=False)

    assert isinstance(report, IncidentReport)

    # Top-level identity / classification fields.
    assert report.incident_id == "inc_001"
    assert report.title
    assert report.summary
    assert report.severity == "SEV2"
    assert report.affected_service == "payments-api"
    assert report.primary_error == "AttributeError: 'NoneType' object has no attribute 'id'"

    # Log finding (failing test extracted).
    assert report.log_finding is not None
    assert (
        report.log_finding.failing_test
        == "tests/test_payments.py::test_create_payment_success"
    )

    # Code finding grounded to the REAL repo file (never invented).
    assert report.code_finding is not None
    assert "app/routes/payments.py" in report.code_finding.matched_files

    # Root cause is tied to evidence that actually exists in the report.
    assert report.root_cause is not None
    assert report.root_cause.category == "null_dereference"
    assert report.root_cause.supporting_evidence_ids
    evidence_ids = {item.id for item in report.evidence}
    assert set(report.root_cause.supporting_evidence_ids).issubset(evidence_ids)

    # Fix plan with a regression test plan.
    assert report.fix_plan is not None
    assert report.fix_plan.steps
    assert report.fix_plan.regression_tests

    # Safety review: clean, no secrets.
    assert report.safety_review is not None
    assert report.safety_review.secret_scan_passed is True
    assert report.safety_review.secrets_detected is False

    # Scoring: strong log evidence + verified code evidence.
    assert 0.75 <= report.confidence <= 0.90
    assert report.needs_human_review is False
    assert report.blocked_reasons == []

    # Every evidence item is grounded (has an id + source); line-bearing items
    # carry a real, positive range.
    assert report.evidence
    for item in report.evidence:
        assert item.id and item.source
        if item.line_start is not None:
            assert item.line_start >= 1
            assert (item.line_end or item.line_start) >= item.line_start


def test_accepts_scenario_name_without_prior_trigger():
    """The scenario name alone is enough; it is auto-registered."""
    report = svc.investigate_incident("broken_api_route", persist=False)
    assert report.incident_id == "inc_001"
    # The auto-registered incident is now resolvable by id, too.
    assert store.get_incident("inc_001") is not None
    again = svc.investigate_incident("inc_001", persist=False)
    assert again.incident_id == "inc_001"


# ---------------------------------------------------------------------------
# secret_in_logs: redaction + still saved
# ---------------------------------------------------------------------------


def test_secret_scenario_redacts_secrets_and_still_saves_report(tmp_path):
    """Secrets are redacted everywhere, yet the report is still persisted."""
    report = svc.investigate_incident("secret_in_logs", reports_dir=tmp_path)

    # No raw secret survives anywhere in the serialized report.
    blob = report.model_dump_json()
    for secret in RAW_FAKE_SECRETS:
        assert secret not in blob, f"raw secret leaked into report: {secret}"
    assert REDACTION_MARKER in blob

    assert report.safety_review is not None
    assert report.safety_review.secrets_detected is True
    assert report.safety_review.secret_scan_passed is True
    assert report.needs_human_review is True

    # Both files were written despite the secret content.
    json_path = tmp_path / "inc_002.json"
    md_path = tmp_path / "inc_002.md"
    assert json_path.is_file()
    assert md_path.is_file()


# ---------------------------------------------------------------------------
# ambiguous_error: low confidence -> human review
# ---------------------------------------------------------------------------


def test_ambiguous_error_sets_needs_human_review():
    """An ungrounded failure must escalate, not fabricate a confident diagnosis."""
    report = svc.investigate_incident("ambiguous_error", persist=False)

    assert report.needs_human_review is True
    assert report.confidence < 0.6
    assert report.severity == "UNKNOWN"
    assert report.status == "blocked"
    # No fix plan without a grounded root cause.
    assert report.fix_plan is None
    assert report.blocked_reasons


# ---------------------------------------------------------------------------
# wrong / missing repo path: no hallucinated code evidence
# ---------------------------------------------------------------------------


def test_missing_repo_path_does_not_hallucinate_code_evidence():
    """If the repo cannot be read, no code evidence may be invented."""
    svc.create_incident("broken_api_route")
    state = store.get_incident("inc_001")
    assert state is not None
    # Point the intake at a repo path that does not exist.
    state.intake.repo_path = "demo/does_not_exist"

    report = svc.investigate_incident("inc_001", persist=False)

    # No repo_file evidence may be fabricated for a repo we cannot read.
    repo_evidence = [e for e in report.evidence if e.source_type == "repo_file"]
    assert repo_evidence == []

    # No code file is claimed as matched/verified.
    if report.code_finding is not None:
        assert report.code_finding.matched_files == []

    # Fails safely instead of pretending to know the root cause.
    assert report.root_cause is None or report.root_cause.category == "undetermined"
    assert report.fix_plan is None
    assert report.needs_human_review is True
    assert report.confidence < 0.6


# ---------------------------------------------------------------------------
# persistence: saved JSON validates, saved Markdown is clean
# ---------------------------------------------------------------------------


def test_saved_json_validates_as_incident_report(tmp_path):
    """The on-disk JSON re-validates straight through the IncidentReport schema."""
    report = svc.investigate_incident("broken_api_route", reports_dir=tmp_path)

    json_path = tmp_path / "inc_001.json"
    assert json_path.is_file()

    reloaded = IncidentReport.model_validate_json(
        json_path.read_text(encoding="utf-8")
    )
    assert reloaded.incident_id == report.incident_id
    assert reloaded.primary_error == report.primary_error
    assert reloaded.severity == report.severity


def test_saved_markdown_exists_and_contains_no_raw_secrets(tmp_path):
    """The Markdown report is written and never leaks a raw secret."""
    svc.investigate_incident("secret_in_logs", reports_dir=tmp_path)

    md_path = tmp_path / "inc_002.md"
    assert md_path.is_file()

    text = md_path.read_text(encoding="utf-8")
    assert text.strip()
    for secret in RAW_FAKE_SECRETS:
        assert secret not in text, f"raw secret leaked into markdown: {secret}"
    assert REDACTION_MARKER in text


# ---------------------------------------------------------------------------
# negative path
# ---------------------------------------------------------------------------


def test_unknown_incident_or_scenario_raises_not_found():
    with pytest.raises(IncidentNotFound):
        svc.investigate_incident("does_not_exist", persist=False)


def test_path_traversal_scenario_is_rejected():
    """A crafted scenario id cannot escape demo/incidents/."""
    with pytest.raises(IncidentNotFound):
        svc.investigate_incident("../../etc", persist=False)
