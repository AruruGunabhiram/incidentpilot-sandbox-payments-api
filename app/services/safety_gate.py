"""Deterministic, authoritative safety gate (Phase 7).

This module is the single source of truth for whether an incident report may be
displayed, turned into a GitHub issue, or used for a pull request. It is pure
Python: same inputs always yield the same verdict, with no LLM, no network, and
no I/O. The Safety Reviewer *agent* is advisory only — it may tighten this
verdict (raise risk, require a human, drop an approval) but can never loosen it
and is never consulted to authorize an action.

The gate evaluates six invariants (see :class:`~app.schemas.safety.SafetyChecks`)
plus one extra hard rule (a secret-bearing source is never auto-eligible for an
external write). Every failing invariant maps to a stable, exact blocked-reason
string so tests and humans can rely on the wording.

Confidence policy (issue eligibility threshold = 0.75):

* confidence >= 0.75
    - report allowed (display)
    - GitHub issue allowed *only after* explicit human approval
    - PR remains blocked
* 0.50 <= confidence < 0.75
    - report allowed
    - human review required
    - GitHub issue and PR blocked ("Confidence below threshold")
* confidence < 0.50
    - report allowed only as a low-confidence diagnostic
    - GitHub issue and PR blocked
    - human review required; the low confidence is stated in blocked_reasons
"""

from __future__ import annotations

from app.schemas.report import IncidentReport
from app.schemas.safety import SafetyChecks, SafetyReview
from app.services.errors import ApprovalRequired, SafetyBlocked
from app.tools.redactor import redact_secrets

# A report must reach this confidence before a GitHub issue is even eligible
# (human approval is still required on top of it). This is intentionally stricter
# than the investigation-level CONFIDENCE_THRESHOLD (0.60) used for severity and
# status, so "good enough to triage" is not the same bar as "safe to file".
SAFETY_ISSUE_CONFIDENCE_THRESHOLD = 0.75

# Exact, stable blocked-reason strings. Tests assert on these verbatim, so do not
# reword them without updating the tests and the Phase 7 contract.
REASON_SECRETS_NOT_REDACTED = "Secrets were not fully redacted"
REASON_REPO_PATHS_UNVERIFIED = "Repository paths were not verified"
REASON_CONFIDENCE_BELOW_THRESHOLD = "Confidence below threshold"
REASON_HUMAN_APPROVAL_REQUIRED = "Human approval is required"
REASON_DIRECT_PRODUCTION_CHANGE = "Direct production change is not allowed"
REASON_UNVERIFIED_FILE_REFERENCE = "Root cause references unverified file path"

# Extra invariant beyond the six structured checks: a report derived from a
# source that contained secrets is untrusted and never auto-eligible for an
# external write, even after every secret has been redacted.
REASON_SECRET_BEARING_SOURCE = (
    "Report is derived from a source that contained secrets; "
    "external sharing requires human review"
)


def checks_blocked_reasons(checks: SafetyChecks) -> list[str]:
    """Return the exact blocked-reason strings for every failing check.

    Deterministic and order-stable. An empty list means all six invariants hold.
    """
    reasons: list[str] = []
    if not checks.secrets_redacted:
        reasons.append(REASON_SECRETS_NOT_REDACTED)
    if not checks.repo_paths_verified:
        reasons.append(REASON_REPO_PATHS_UNVERIFIED)
    if not checks.confidence_above_threshold:
        reasons.append(REASON_CONFIDENCE_BELOW_THRESHOLD)
    if not checks.no_unverified_file_references:
        reasons.append(REASON_UNVERIFIED_FILE_REFERENCE)
    if not checks.no_direct_production_change:
        reasons.append(REASON_DIRECT_PRODUCTION_CHANGE)
    # ``human_approval_required`` is a posture: it must be True (a human must
    # approve). A False here would mean a human was removed from the loop, which
    # is itself unsafe.
    if not checks.human_approval_required:
        reasons.append(REASON_HUMAN_APPROVAL_REQUIRED)
    return reasons


def evaluate_safety(
    *,
    secrets_present: bool,
    unverified_file_reference: bool,
    confidence: float,
    redactions_applied: int = 0,
    secrets_fully_redacted: bool = True,
    summary: str | None = None,
    required_human_action: str | None = None,
) -> SafetyReview:
    """Compute the authoritative :class:`SafetyReview` from grounded signals.

    Parameters are deliberately primitive booleans/floats so the gate is trivial
    to unit test in isolation from the investigation pipeline:

    * ``secrets_present`` — secrets were detected in the (untrusted) source.
    * ``unverified_file_reference`` — the report references a repo path that
      could not be verified to exist.
    * ``confidence`` — the report's confidence in [0, 1].
    * ``redactions_applied`` — count of redactions performed (for reporting).
    * ``secrets_fully_redacted`` — no unredacted secret remains (the
      deterministic redactor guarantees this; expose it for completeness/tests).

    The returned review never enables a PR, never approves an external write on
    its own (a GitHub issue stays gated behind explicit human approval), and
    always allows display — even a low-confidence diagnostic is shown, just
    clearly labelled and blocked from any external action.
    """
    confidence_above_threshold = confidence >= SAFETY_ISSUE_CONFIDENCE_THRESHOLD
    repo_paths_verified = not unverified_file_reference
    no_unverified_file_references = not unverified_file_reference

    checks = SafetyChecks(
        secrets_redacted=secrets_fully_redacted,
        repo_paths_verified=repo_paths_verified,
        confidence_above_threshold=confidence_above_threshold,
        human_approval_required=True,
        no_direct_production_change=True,
        no_unverified_file_references=no_unverified_file_references,
    )

    blocked_reasons = checks_blocked_reasons(checks)
    if secrets_present and REASON_SECRET_BEARING_SOURCE not in blocked_reasons:
        blocked_reasons = blocked_reasons + [REASON_SECRET_BEARING_SOURCE]

    # A GitHub issue is *eligible* only when nothing blocks it. Eligibility is
    # not authorization: creation still requires an explicit human approval on
    # file (see assert_github_issue_allowed). A PR is never approved here.
    approved_for_github_issue = not blocked_reasons
    approved_for_pr = False
    approved_for_display = True

    if secrets_present:
        risk_level = "high"
    elif approved_for_github_issue:
        risk_level = "low"
    else:
        risk_level = "medium"

    needs_human_review = secrets_present or not approved_for_github_issue

    if required_human_action is None:
        if approved_for_github_issue:
            required_human_action = (
                "Review the grounded report, then approve GitHub issue creation."
            )
        elif secrets_present:
            required_human_action = (
                "Rotate the exposed credentials, then review the redacted report "
                "before any external sharing."
            )
        else:
            required_human_action = (
                "Review the blocked findings before any GitHub action."
            )

    if summary is None:
        summary = _default_summary(
            secrets_present=secrets_present,
            redactions_applied=redactions_applied,
            approved_for_github_issue=approved_for_github_issue,
            blocked_reasons=blocked_reasons,
        )

    return SafetyReview(
        summary=redact_secrets(summary),
        confidence=confidence,
        evidence=[],
        needs_human_review=needs_human_review,
        blocked_reasons=blocked_reasons,
        approved_for_display=approved_for_display,
        approved_for_github_issue=approved_for_github_issue,
        approved_for_pr=approved_for_pr,
        risk_level=risk_level,
        secrets_detected=secrets_present,
        redactions_applied=redactions_applied,
        secret_scan_passed=True,
        # Legacy detection flag (True when secrets were present and redacted).
        secrets_redacted=secrets_present,
        repo_paths_verified=repo_paths_verified,
        confidence_above_threshold=confidence_above_threshold,
        human_approval_required=True,
        no_direct_production_change=True,
        required_human_action=required_human_action,
        checks=checks,
    )


def github_issue_block_reasons(report: IncidentReport) -> list[str]:
    """Re-derive, deterministically, why a GitHub issue is blocked for ``report``.

    This reads the report's own structured ``checks`` (the authoritative gate
    output) rather than trusting any single boolean, and re-applies the
    secret-bearing-source rule. An empty list means the report is *eligible* for
    a GitHub issue once a human approval is on file.
    """
    safety = report.safety_review
    if safety is None:
        return ["Safety review is missing; the report is not eligible for any action"]

    reasons = list(checks_blocked_reasons(safety.checks))
    if safety.secrets_detected and REASON_SECRET_BEARING_SOURCE not in reasons:
        reasons.append(REASON_SECRET_BEARING_SOURCE)
    return reasons


def assert_report_safe_for_issue(report: IncidentReport) -> None:
    """Raise :class:`SafetyBlocked` unless ``report`` passes every safety check.

    This is the safety half of the GitHub-issue gate, with no notion of human
    approval. It is called *before* the approval check so an unsafe report (failed
    safety review, low confidence, secret-bearing source, unverified path) is
    blocked regardless of any approval on file. The message is built from the
    re-derived, redacted blocked reasons and never leaks a raw secret.
    """
    reasons = github_issue_block_reasons(report)
    if reasons:
        raise SafetyBlocked(
            "Safety review blocked GitHub issue creation: " + "; ".join(reasons) + "."
        )


def assert_github_issue_allowed(report: IncidentReport, approval) -> None:
    """Raise unless a GitHub issue may be created for ``report``.

    The deterministic safety verdict is checked *first* and is authoritative:
    a blocked report raises :class:`SafetyBlocked` before any approval is even
    considered and long before any GitHub client could be called. Only a report
    that passes every safety invariant proceeds to the approval gate, which
    requires an explicit, recorded human approval.

    ``approval`` is the stored :class:`~app.schemas.approval.ApprovalRecord` (or
    ``None``). Kept untyped to avoid a hard import cycle with the store.
    """
    reasons = github_issue_block_reasons(report)
    if reasons:
        raise SafetyBlocked(
            "Safety review blocked GitHub issue creation: " + "; ".join(reasons) + "."
        )

    if approval is None or not getattr(approval, "approved", False):
        raise ApprovalRequired(
            "Human approval is required before creating a GitHub issue. "
            "POST to /incidents/{id}/approve first."
        )


def _default_summary(
    *,
    secrets_present: bool,
    redactions_applied: int,
    approved_for_github_issue: bool,
    blocked_reasons: list[str],
) -> str:
    if secrets_present:
        head = (
            f"Secret scan passed: {redactions_applied} credential-like value(s) "
            "detected and redacted."
        )
    else:
        head = "Secret scan passed: no credential-like values detected."

    if approved_for_github_issue:
        tail = " Report is safe to display. GitHub issue is eligible after human approval."
    else:
        tail = (
            " Report is safe to display. GitHub issue is blocked: "
            + "; ".join(blocked_reasons)
            + "."
        )
    return head + tail
