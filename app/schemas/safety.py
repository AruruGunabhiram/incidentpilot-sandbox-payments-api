"""Safety reviewer schema.

Defaults are conservative: nothing is approved, human approval is required, and
no direct production change is assumed, until the deterministic safety gate
(``app.services.safety_gate``) proves otherwise from grounded evidence.

Two views of the safety verdict are exposed:

* The nested :class:`SafetyChecks` (the ``checks`` field) is the structured,
  test-stable result of the deterministic gate. Each field is a single safety
  invariant that must be ``True`` for an external action to be eligible.
* The flat fields are retained verbatim for backward compatibility with earlier
  phases (the report writer, the agent layer, and existing API tests). They are
  populated from the same gate.

Naming note: the flat ``secrets_redacted`` field is a *detection* flag — it is
``True`` when secrets were present in the source and were redacted (so it is
``False`` for a clean incident that had no secrets). The nested
``checks.secrets_redacted`` is a *safety invariant* — it is ``True`` when no
unredacted secret remains in the report (so it is ``True`` for a clean incident).
They legitimately differ; see ``app.services.safety_gate`` for the authoritative
computation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.findings import ReviewedOutput


class SafetyChecks(BaseModel):
    """The deterministic safety gate's structured pass/fail invariants.

    Every field must be ``True`` for a GitHub issue (or any external write) to be
    eligible. Defaults are conservative (a missing or unproven check reads as
    unsafe) so an under-specified review can never accidentally authorize an
    action.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    # No unredacted secret remains anywhere in the report.
    secrets_redacted: bool = False
    # Every repo path the report relies on was verified to exist.
    repo_paths_verified: bool = False
    # Confidence is at or above the issue-eligibility threshold.
    confidence_above_threshold: bool = False
    # A human must approve before any external write (always required posture).
    human_approval_required: bool = True
    # The pipeline never performs a direct production change (always true here).
    no_direct_production_change: bool = True
    # No finding references a file path that was not verified.
    no_unverified_file_references: bool = False


class SafetyReview(ReviewedOutput):
    """Output of the Safety Reviewer gating display and external writes."""

    approved_for_display: bool = False
    approved_for_github_issue: bool = False
    approved_for_pr: bool = False
    risk_level: Literal["low", "medium", "high", "critical"] = "high"
    secrets_detected: bool = False
    redactions_applied: int = Field(default=0, ge=0)
    secret_scan_passed: bool = False
    secrets_redacted: bool = False
    repo_paths_verified: bool = False
    confidence_above_threshold: bool = False
    human_approval_required: bool = True
    no_direct_production_change: bool = True
    required_human_action: str | None = None
    # Structured, deterministic check results. Conservative by default so a
    # SafetyReview built without an explicit gate run is treated as unsafe.
    checks: SafetyChecks = Field(default_factory=SafetyChecks)
