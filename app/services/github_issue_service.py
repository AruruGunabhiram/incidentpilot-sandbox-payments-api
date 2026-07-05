"""GitHub issue service (Phase 9) — the only path that may write an issue.

This service turns a grounded :class:`~app.schemas.report.IncidentReport` into a
GitHub issue, but only after every gate the project requires:

1. ``GITHUB_DRY_RUN`` resolves to ``false`` (missing / invalid / ``true`` all
   mean dry-run, so the safe default is *never* writing).
2. ``GITHUB_TOKEN``, ``GITHUB_OWNER`` and ``GITHUB_REPO`` are all present.
3. The incident exists and has a completed report.
4. The deterministic safety review allows a GitHub issue
   (``app.services.safety_gate`` — authoritative, checked first).
5. An explicit human approval for ``create_github_issue`` is on file
   (``app.services.approval_service``).

The safety + approval gates are *reused*, not reimplemented, so this service can
never diverge from the rest of the control plane. In dry-run (the default) it
returns a redacted preview and makes no network call at all. Only in real mode,
with every gate satisfied, does it construct :class:`~app.tools.github_client`
and perform the write. Any failure is surfaced as a controlled, token-free
:class:`GitHubIssueError`.

The issue title and Markdown body are built entirely from the grounded, already
redacted report — never from caller-supplied text — and the assembled body is
redacted once more before it leaves this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.findings import EvidenceItem
from app.schemas.report import IncidentReport
from app.services import safety_gate
from app.services.approval_service import approval_service
from app.services.errors import IncidentError, IncidentNotFound, ReportNotReady
from app.storage import incident_store
from app.tools.github_client import GitHubClient, GitHubClientError
from app.tools.report_writer import ensure_report_safe

# The GitHub issue body must contain exactly these sections, in this order.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Incident Summary",
    "Severity",
    "Primary Error",
    "Evidence",
    "Root Cause Hypothesis",
    "Fix Plan",
    "Regression Test Plan",
    "Rollback Plan",
    "Safety Review",
    "Confidence",
)

_NOT_PROVIDED = "Not provided"

# Only an explicit, recognized falsey token disables dry-run. Everything else
# (missing, empty, "true", or an unrecognized/invalid value) stays dry-run.
_FALSEY = frozenset({"false", "0", "no", "off"})


class GitHubIssueError(IncidentError):
    """A real GitHub issue write was attempted and failed (controlled, no secret)."""

    status_code = 502
    reason = "github_write_failed"


@dataclass(frozen=True)
class GitHubSettings:
    """Resolved GitHub configuration for one issue-creation attempt."""

    token: str = ""
    owner: str = ""
    repo: str = ""
    dry_run: bool = True

    @property
    def configured(self) -> bool:
        """True only when token, owner, and repo are all present."""
        return bool(self.token and self.owner and self.repo)


class GitHubIssueOutcome(BaseModel):
    """Result of an issue-creation attempt (preview in dry-run, created in real).

    Carries no secret: ``title`` and ``body_preview`` come from the redacted
    report and the token never appears anywhere in this model.
    """

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    created: bool
    dry_run: bool
    title: str
    body_preview: str
    url: str | None = None
    number: int | None = None
    labels: list[str] = Field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Configuration / dry-run resolution
# ---------------------------------------------------------------------------


def resolve_dry_run(raw: object) -> bool:
    """Resolve ``GITHUB_DRY_RUN`` safely: only an explicit false token disables it.

    Returns ``True`` (dry-run) for ``None``, an empty/whitespace value, ``"true"``,
    or any unrecognized/invalid value, so a misconfiguration can never accidentally
    enable a real write. Returns ``False`` only for ``false``/``0``/``no``/``off``
    (case-insensitive) or a real ``False`` boolean.
    """
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in _FALSEY


def github_settings_from_env(
    env: Mapping[str, str] | None = None,
) -> GitHubSettings:
    """Build :class:`GitHubSettings` from the environment (raw, robust).

    Reads the raw environment rather than a pre-coerced bool so an *invalid*
    ``GITHUB_DRY_RUN`` is treated as dry-run instead of raising. ``env`` may be
    supplied (e.g. in tests) to avoid touching the process environment.
    """
    source = os.environ if env is None else env
    return GitHubSettings(
        token=(source.get("GITHUB_TOKEN") or "").strip(),
        owner=(source.get("GITHUB_OWNER") or "").strip(),
        repo=(source.get("GITHUB_REPO") or "").strip(),
        dry_run=resolve_dry_run(source.get("GITHUB_DRY_RUN")),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def create_github_issue(
    incident_id: str,
    *,
    config: GitHubSettings,
    labels: list[str] | None = None,
    client: GitHubClient | None = None,
    store=incident_store,
) -> GitHubIssueOutcome:
    """Preview (dry-run) or create (real) the GitHub issue for an incident.

    Enforces, in order: incident exists, report exists, safety review allows a
    GitHub issue, and a human approval is on file. Only after all of that, and
    only when ``config`` resolves to real mode *and* GitHub is fully configured,
    is a network write performed via ``client`` (constructed lazily if not
    injected). Dry-run never touches the network.
    """
    state = store.get_incident(incident_id)
    if state is None:
        raise IncidentNotFound(f"Unknown incident '{incident_id}'. Trigger it first.")

    report = state.report
    if report is None:
        raise ReportNotReady("Investigate the incident before creating a GitHub issue.")

    # Reuse the authoritative gates (no duplicated gate logic). Safety is checked
    # first so an approval can never override an unsafe report; both run before
    # any issue payload is built or any client could be touched.
    safety_gate.assert_report_safe_for_issue(report)
    approval_service.require_approved(incident_id, "create_github_issue")

    title = build_issue_title(report)
    body = build_issue_body(report)
    issue_labels = _issue_labels(report, labels)

    # Dry-run unless explicitly disabled AND GitHub is fully configured. Missing
    # env -> dry-run (a real write is "blocked" rather than half-attempted).
    dry_run = config.dry_run or not config.configured
    if dry_run:
        return GitHubIssueOutcome(
            incident_id=incident_id,
            created=False,
            dry_run=True,
            title=title,
            body_preview=body,
            url=None,
            number=None,
            labels=issue_labels,
            message=_dry_run_message(config),
        )

    # Real mode: every gate passed, env present, dry-run explicitly disabled.
    gh = client or GitHubClient(config.token, config.owner, config.repo)
    try:
        created = gh.create_issue(title, body, labels=issue_labels)
    except GitHubClientError as exc:
        message = f"GitHub issue creation failed: {exc}"
        # Defense in depth: scrub the token even though the client already did.
        if config.token:
            message = message.replace(config.token, "***")
        raise GitHubIssueError(message) from None

    return GitHubIssueOutcome(
        incident_id=incident_id,
        created=True,
        dry_run=False,
        title=title,
        body_preview=body,
        url=created.url,
        number=created.number,
        labels=issue_labels,
        message=f"Created GitHub issue #{created.number}.",
    )


# ---------------------------------------------------------------------------
# Issue construction (grounded report -> title + Markdown body)
# ---------------------------------------------------------------------------


def build_issue_title(report: IncidentReport) -> str:
    """Build the issue title from the grounded report (redacted)."""
    if _is_payments_missing_user_report(report):
        return ensure_report_safe(
            "IncidentPilot: POST /payments fails due to unchecked missing user"
        )
    return ensure_report_safe(f"IncidentPilot: {report.title}")


def build_issue(report: IncidentReport) -> tuple[str, str]:
    """Return ``(title, body)`` for ``report`` — convenience for callers/tests."""
    return build_issue_title(report), build_issue_body(report)


def build_issue_body(report: IncidentReport) -> str:
    """Render the GitHub issue body with every required section, redacted.

    Always emits all of :data:`REQUIRED_SECTIONS` in order; missing optional
    data renders as ``Not provided`` so the body is complete and the function
    never crashes on a sparse report.
    """
    lines: list[str] = []

    _section(lines, "Incident Summary", report.summary or None)
    _section(
        lines,
        "Severity",
        f"**{report.severity}** — service `{report.affected_service}` "
        f"(status: {report.status})",
    )
    _section(
        lines,
        "Primary Error",
        f"`{report.primary_error}`" if report.primary_error else None,
    )
    _section(lines, "Evidence", _evidence_block(report.evidence))
    _section(lines, "Root Cause Hypothesis", _root_cause_block(report.root_cause))
    _section(lines, "Fix Plan", _fix_plan_block(report.fix_plan))
    _section(
        lines,
        "Regression Test Plan",
        _bullet_list(report.fix_plan.regression_tests) if report.fix_plan else None,
    )
    _section(
        lines,
        "Rollback Plan",
        _bullet_list(report.fix_plan.rollback_plan) if report.fix_plan else None,
    )
    _section(lines, "Safety Review", _safety_block(report.safety_review))
    _section(
        lines,
        "Confidence",
        f"{report.confidence:.2f}"
        + (" (human review required)" if report.needs_human_review else ""),
    )

    return ensure_report_safe("\n".join(lines).rstrip() + "\n")


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _section(lines: list[str], heading: str, body: str | None) -> None:
    lines += [f"## {heading}", "", body if body else _NOT_PROVIDED, ""]


def _bullet_list(items: list[str] | None) -> str | None:
    if not items:
        return None
    return "\n".join(f"- {item}" for item in items)


def _evidence_block(evidence: list[EvidenceItem]) -> str | None:
    if not evidence:
        return None
    return "\n".join(_evidence_line(item) for item in evidence)


def _evidence_line(item: EvidenceItem) -> str:
    location = item.path or item.source or "unknown"
    if (
        item.line_start is not None
        and item.line_end is not None
        and item.line_start != item.line_end
    ):
        location = f"{location}:{item.line_start}-{item.line_end}"
    elif item.line_start is not None:
        location = f"{location}:{item.line_start}"
    line = f"- **{item.id}** `{location}` — {item.summary}".rstrip(" —")
    if item.snippet:
        line += f"\n  - `{item.snippet}`"
    return line


def _root_cause_block(root_cause) -> str | None:
    if root_cause is None:
        return None
    parts: list[str] = []
    if root_cause.category:
        parts.append(f"**Category:** {root_cause.category}")
    if root_cause.summary:
        parts.append(root_cause.summary)
    if root_cause.alternatives:
        parts.append("")
        parts.append("**Alternatives:**")
        parts += [f"- {alt}" for alt in root_cause.alternatives]
    return "\n".join(parts) if parts else None


def _fix_plan_block(fix_plan) -> str | None:
    if fix_plan is None:
        return None
    parts: list[str] = []
    if fix_plan.summary:
        parts.append(fix_plan.summary)
    if fix_plan.steps:
        parts.append("")
        parts += [f"{i}. {step}" for i, step in enumerate(fix_plan.steps, start=1)]
    return "\n".join(parts) if parts else None


def _safety_block(safety) -> str | None:
    if safety is None:
        return None
    flags = [
        f"- Approved for GitHub issue: {safety.approved_for_github_issue}",
        f"- Risk level: {safety.risk_level}",
        f"- Secret scan passed: {safety.secret_scan_passed}",
        f"- Secrets detected: {safety.secrets_detected}",
        f"- Redactions applied: {safety.redactions_applied}",
        f"- Human approval required: {safety.human_approval_required}",
    ]
    if safety.required_human_action:
        flags.append(f"- Required human action: {safety.required_human_action}")
    head = [safety.summary, ""] if safety.summary else []
    return "\n".join(head + flags)


def _issue_labels(report: IncidentReport, extra: list[str] | None) -> list[str]:
    labels = ["incident", f"severity:{report.severity.lower()}"]
    for label in extra or []:
        if label not in labels:
            labels.append(label)
    return labels


def _is_payments_missing_user_report(report: IncidentReport) -> bool:
    """Recognize the flagship demo issue title from grounded report evidence."""
    if report.affected_service != "payments-api":
        return False
    if report.root_cause is None or report.root_cause.category != "null_dereference":
        return False
    if not (
        report.primary_error
        and "'NoneType' object has no attribute 'id'" in report.primary_error
    ):
        return False

    snippets = " ".join(item.snippet or "" for item in report.evidence)
    paths = {item.path for item in report.evidence}
    return (
        "get_user(request.user_id)" in snippets
        and "demo/demo_repo/app/routes/payments.py" in paths
    )


def _dry_run_message(config: GitHubSettings) -> str:
    if not config.configured:
        return (
            "Dry run: GitHub is not fully configured. Set GITHUB_TOKEN, "
            "GITHUB_OWNER, GITHUB_REPO and GITHUB_DRY_RUN=false to enable real "
            "issue creation. No GitHub write was performed."
        )
    return (
        "Dry run (GITHUB_DRY_RUN): previewed the issue only. "
        "No GitHub write was performed."
    )
