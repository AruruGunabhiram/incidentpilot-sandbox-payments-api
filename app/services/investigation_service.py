"""Deterministic incident investigation workflow (Phase 4 + Phase 5 persistence).

No ADK, no LLM, no agents, no network, no database. This service orchestrates
the existing deterministic tools into a single grounded :class:`IncidentReport`
and writes that report to the durable on-disk store::

    intake.json
      -> ci_log_reader   (path-guarded load, redact secrets, extract evidence)
      -> stack-frame grounding via repo_search / read_file_snippet (path-guarded)
      -> structured findings (log / code / root-cause / fix / safety)
      -> IncidentReport
      -> redacted {incident_id}.json + {incident_id}.md on disk (Phase 5)

Every claim in the report is tied to evidence a tool actually returned. Nothing
is invented: a file path or line number is only cited after ``path_guard``
confirms the file exists and the snippet is read back from it. Missing or weak
evidence lowers confidence, raises ``needs_human_review``, and blocks the
GitHub-issue path in the safety review.

The single public entry point :func:`investigate_incident` accepts either an
already-triggered incident id or a demo scenario name; a scenario that has not
been triggered yet is auto-registered from its intake fixture, so the service is
usable on its own. Both the JSON and Markdown it persists are redacted by the
shared report writer / store before they reach disk, so no raw secret can be
written even if upstream evidence missed one.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.approval import (
    ApprovalAction,
    ApprovalResponse,
    GitHubIssueOptions,
    GitHubIssueResult,
)
from app.schemas.findings import (
    CodeFinding,
    EvidenceItem,
    FixPlan,
    LogFinding,
    RootCauseHypothesis,
)
from app.schemas.incident import IncidentIntake, IncidentTriggerResponse
from app.schemas.report import IncidentReport
from app.schemas.safety import SafetyReview
from app.services import safety_gate
from app.services.approval_service import approval_service
from app.services.errors import (
    IncidentNotFound,
    ReportNotReady,
    ScenarioNotFound,
)
from app.services.github_issue_service import build_issue_body, build_issue_title
from app.storage import incident_store
from app.tools.ci_log_reader import CILogResult, read_ci_log
from app.tools.path_guard import PathGuardError, resolve_safe_path, verify_file_exists
from app.tools.redactor import REDACTION_MARKER, redact_secrets
from app.tools.report_writer import build_markdown_report
from app.tools.repo_search import read_file_snippet

# Project root: app/services/investigation_service.py -> parents[2] == repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INCIDENTS_DIR = PROJECT_ROOT / "demo" / "incidents"

# Below this, an automated diagnosis is treated as not trustworthy enough to
# act on without a human.
CONFIDENCE_THRESHOLD = 0.6

# Stack-frame patterns, matched against already-redacted log lines.
#   pytest style:    app/routes/payments.py:81: in create_payment
#   traceback style: File "app/services/billing.py", line 42, in charge
_FRAME_COLON = re.compile(r"([\w./-]+\.py):(\d+):\s*in\s+(\w+)")
_FRAME_FILE = re.compile(r'File "([^"]+\.py)", line (\d+), in (\w+)')

# Marker emitted by the redactor, e.g. ``[REDACTED_SECRET:type=github_token]``.
_MARKER_TYPE = re.compile(r"\[REDACTED_SECRET:type=(\w+)\]")

# Frame paths inside these are third-party / tooling, never repo code.
_NON_REPO_HINTS = (
    ".venv/",
    "site-packages",
    "node_modules",
    "/usr/",
    "lib/python",
    "dist-packages",
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def create_incident(scenario: str) -> IncidentTriggerResponse:
    """Load ``demo/incidents/{scenario}/intake.json`` and register the incident.

    Returns the assigned (stable) incident id. Raises :class:`ScenarioNotFound`
    if the scenario has no intake fixture.
    """
    intake = _load_intake(scenario)
    state = incident_store.register_incident(scenario, intake)
    return IncidentTriggerResponse(
        incident_id=state.incident_id, status="created", scenario=scenario
    )


def investigate_incident(
    scenario_or_incident_id: str,
    *,
    persist: bool = True,
    reports_dir: Path | str | None = None,
) -> IncidentReport:
    """Run the deterministic investigation for an incident or demo scenario.

    ``scenario_or_incident_id`` may be either an already-triggered incident id
    (e.g. ``"inc_001"``) or a demo scenario name (e.g. ``"broken_api_route"``).
    A scenario that has not been triggered yet is auto-registered from its
    ``demo/incidents/{scenario}/intake.json`` fixture, so the service is usable
    on its own without a prior ``/trigger`` call.

    The grounded report is stored in the in-memory control plane and, when
    ``persist`` is true (the default), also written to disk as
    ``{incident_id}.json`` and ``{incident_id}.md`` under the durable reports
    store. The store validates the report through :class:`IncidentReport` and
    redacts every string before writing, so no raw secret can reach disk.
    ``reports_dir`` overrides the on-disk location (used by tests).

    Returns the :class:`IncidentReport`. Raises :class:`IncidentNotFound` if the
    argument is neither a known incident nor a demo scenario with an intake
    fixture.
    """
    state = _resolve_incident_state(scenario_or_incident_id)

    incident_dir = INCIDENTS_DIR / state.scenario
    report = _investigate(state.incident_id, state.scenario, state.intake, incident_dir)

    # In-memory control plane: the live source of truth during a request.
    incident_store.save_report(state.incident_id, report)

    # Durable, redacted JSON + Markdown on disk (Phase 5, step 9).
    if persist:
        _persist_report(report, reports_dir=reports_dir)

    return report


def _resolve_incident_state(scenario_or_incident_id: str) -> incident_store.IncidentState:
    """Resolve an incident id or demo scenario name to an ``IncidentState``.

    Deterministic resolution order:

    1. An already-registered incident id is returned as-is.
    2. Otherwise, if a demo scenario fixture exists for the argument, the
       incident is auto-registered from it and returned.
    3. Otherwise :class:`IncidentNotFound` is raised.

    The argument is treated as untrusted: scenario lookup is path-guarded inside
    :func:`_load_intake`, so a crafted value cannot escape ``demo/incidents/``.
    """
    state = incident_store.get_incident(scenario_or_incident_id)
    if state is not None:
        return state

    try:
        intake = _load_intake(scenario_or_incident_id)
    except ScenarioNotFound as exc:
        raise IncidentNotFound(
            f"Unknown incident or scenario '{scenario_or_incident_id}'. Trigger "
            f"the incident first, or pass a demo scenario that has a "
            f"demo/incidents/<scenario>/intake.json fixture."
        ) from exc

    return incident_store.register_incident(scenario_or_incident_id, intake)


def _persist_report(
    report: IncidentReport, *, reports_dir: Path | str | None
) -> tuple[Path, Path]:
    """Write the redacted report to disk as JSON + Markdown; return both paths.

    Delegates to the durable store, which re-validates the report and redacts
    every string before writing. Returns ``(json_path, markdown_path)``.
    """
    json_path = incident_store.save_report_json(report, reports_dir=reports_dir)
    markdown_path = incident_store.save_report_markdown(
        report.incident_id, build_markdown_report(report), reports_dir=reports_dir
    )
    return json_path, markdown_path


def record_github_approval(
    incident_id: str,
    approved: bool,
    approved_by: str,
    note: str | None,
    action: ApprovalAction = "create_github_issue",
) -> ApprovalResponse:
    """Record a human approval/rejection decision for ``action``.

    Delegates to the authoritative :class:`ApprovalService`, which validates the
    action, requires an investigated incident, and persists the decision against
    that action only. Raises :class:`IncidentNotFound` / :class:`ReportNotReady`
    / :class:`InvalidAction` from the service as appropriate.
    """
    if approved:
        record = approval_service.approve_action(
            incident_id, action, approved_by=approved_by, note=note
        )
        message = (
            "Approval recorded; GitHub issue creation is now unlocked (still dry-run)."
        )
        # Reflect the human decision on the stored report for the demo UI.
        state = incident_store.get_incident(incident_id)
        if state is not None and state.report is not None:
            state.report.status = "approved"
            state.report.updated_at = datetime.now(timezone.utc)
    else:
        record = approval_service.reject_action(
            incident_id, action, approved_by=approved_by, note=note
        )
        message = "Decision recorded as rejected; GitHub issue creation stays blocked."

    return ApprovalResponse(
        incident_id=incident_id,
        action=record.action,
        status=record.status,
        approved=record.approved,
        approved_by=record.approved_by,
        message=message,
    )


def create_github_issue(
    incident_id: str,
    options: GitHubIssueOptions,
    *,
    github_configured: bool,
    env_dry_run: bool = True,
) -> GitHubIssueResult:
    """Return a redacted preview of the GitHub issue for this incident.

    Enforces every gate before producing anything: the incident and report must
    exist, the safety review must approve a GitHub issue, and a human approval
    must be on file. Never performs a network write — ``created`` is always
    ``False`` in this phase.
    """
    state = incident_store.get_incident(incident_id)
    if state is None:
        raise IncidentNotFound(f"Unknown incident '{incident_id}'. Trigger it first.")

    report = state.report
    if report is None:
        raise ReportNotReady("Investigate the incident before creating a GitHub issue.")

    # Two-stage, authoritative gate, enforced in the service layer BEFORE any
    # issue payload is built or any GitHub client could be touched:
    #   1. Deterministic safety verdict (secrets, unverified paths, confidence
    #      threshold). A failed safety review or low confidence blocks here,
    #      regardless of any approval on file -> SafetyBlocked.
    #   2. Action-specific human approval. No approval (pending) -> ApprovalRequired;
    #      an explicit rejection -> ApprovalRejected.
    # Safety is checked first so an approval can never override an unsafe report.
    safety_gate.assert_report_safe_for_issue(report)
    approval_service.require_approved(incident_id, "create_github_issue")

    title, body, labels = _build_issue_preview(report, extra_labels=options.labels)

    # Dry run unless explicitly disabled in BOTH the request and the env, AND
    # GitHub is fully configured. Live creation is intentionally not implemented
    # in this phase, so this only ever selects between two preview responses.
    dry_run = options.dry_run or env_dry_run or not github_configured
    if dry_run:
        return GitHubIssueResult(
            incident_id=incident_id,
            created=False,
            dry_run=True,
            title=title,
            body_preview=body,
            labels=labels,
            issue_url=None,
            issue_number=None,
            message="Dry run: previewed the issue only. No GitHub write was performed.",
        )

    return GitHubIssueResult(
        incident_id=incident_id,
        created=False,
        dry_run=False,
        title=title,
        body_preview=body,
        labels=labels,
        issue_url=None,
        issue_number=None,
        message=(
            "Live GitHub issue creation is not enabled in this build; "
            "returned a redacted preview instead."
        ),
    )


# ---------------------------------------------------------------------------
# Intake loading
# ---------------------------------------------------------------------------


def _load_intake(scenario: str) -> IncidentIntake:
    # ``scenario`` is untrusted input: confine the intake path under the
    # incidents root so a crafted value (e.g. "../../etc") cannot escape it.
    try:
        intake_path = resolve_safe_path(INCIDENTS_DIR, f"{scenario}/intake.json")
    except PathGuardError as exc:
        raise ScenarioNotFound(f"Unknown scenario '{scenario}'. {exc}") from exc
    if not intake_path.is_file():
        raise ScenarioNotFound(
            f"Unknown scenario '{scenario}'. No intake.json under demo/incidents/."
        )
    raw = json.loads(intake_path.read_text(encoding="utf-8"))
    # ``created_at`` arrives as an ISO string; the strict schema needs a real
    # datetime, so parse it here before validating.
    created = raw.get("created_at")
    if isinstance(created, str):
        raw["created_at"] = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return IncidentIntake.model_validate(raw)


# ---------------------------------------------------------------------------
# Core investigation
# ---------------------------------------------------------------------------


def _investigate(
    incident_id: str, scenario: str, intake: IncidentIntake, incident_dir: Path
) -> IncidentReport:
    ci_rel = f"demo/incidents/{scenario}/ci.log"
    now = datetime.now(timezone.utc)

    # --- 1. Read + redact the CI log -------------------------------------
    try:
        log = read_ci_log(incident_dir, "ci.log")
    except (FileNotFoundError, PathGuardError) as exc:
        return _no_evidence_report(incident_id, intake, detail=str(exc), now=now)

    secrets_present = log.redactions_applied > 0
    has_error = log.primary_error is not None
    has_test = log.failing_test is not None
    primary_error = _primary_error_str(log)

    # --- 2. Build grounded evidence --------------------------------------
    ci_evidence = _ci_evidence(log, ci_rel)
    secret_evidence = _secret_evidence(log, ci_rel)
    api_evidence = _api_evidence(incident_dir, scenario, intake)

    frames = _grounded_frames(log, intake)
    code_evidence = _code_evidence(frames, intake)
    missing_files = sorted({f["path"] for f in _candidate_frames(log) if f["path"] not in {g["path"] for g in frames}})
    code_grounded = len(frames) > 0

    # --- 3. Score the investigation --------------------------------------
    confidence = _confidence(has_error, has_test, code_grounded)
    above_threshold = confidence >= CONFIDENCE_THRESHOLD
    needs_review = secrets_present or not code_grounded or not above_threshold or not has_error
    severity = _severity(secrets_present, code_grounded, has_error, above_threshold)
    status = _status(has_error, code_grounded, secrets_present, above_threshold)
    category = _category(secrets_present, code_grounded, has_error, log)

    # --- 4. Assemble findings --------------------------------------------
    log_finding = _build_log_finding(
        log, primary_error, ci_evidence, secret_evidence, frames, secrets_present, has_error
    )
    code_finding = _build_code_finding(frames, code_evidence, missing_files, code_grounded)
    root_cause = _build_root_cause(
        category, primary_error, frames, secret_evidence, code_evidence, ci_evidence,
        confidence, needs_review, secrets_present, code_grounded,
    )
    fix_plan = _build_fix_plan(category, frames, intake, log, code_evidence, secret_evidence)
    safety_review = _build_safety_review(
        secrets_present=secrets_present,
        unverified_reference=bool(missing_files),
        confidence=confidence,
        redactions_applied=log.redactions_applied,
    )

    top_evidence = _dedupe_evidence(
        ci_evidence + code_evidence + secret_evidence + api_evidence
    )
    blocked = _report_blocked_reasons(
        secrets_present, code_grounded, above_threshold, has_error, confidence
    )

    return IncidentReport(
        incident_id=incident_id,
        title=redact_secrets(_title(scenario, intake, category, primary_error, frames)),
        severity=severity,
        affected_service=intake.service,
        status=status,
        summary=redact_secrets(
            _summary(category, intake, primary_error, log, frames, secrets_present)
        ),
        confidence=confidence,
        evidence=top_evidence,
        needs_human_review=needs_review,
        blocked_reasons=blocked,
        primary_error=primary_error,
        log_finding=log_finding,
        code_finding=code_finding,
        root_cause=root_cause,
        fix_plan=fix_plan,
        safety_review=safety_review,
        created_at=now,
        updated_at=now,
    )


def _no_evidence_report(
    incident_id: str, intake: IncidentIntake, *, detail: str, now: datetime
) -> IncidentReport:
    """Degrade gracefully when the CI log cannot be read."""
    return IncidentReport(
        incident_id=incident_id,
        title=f"{intake.service}: investigation blocked (no CI log)",
        severity="UNKNOWN",
        affected_service=intake.service,
        status="blocked",
        summary=redact_secrets(
            f"No CI log could be read for this incident, so no evidence was "
            f"extracted. Human investigation is required. Detail: {detail}"
        ),
        confidence=0.0,
        evidence=[],
        needs_human_review=True,
        blocked_reasons=[
            "CI log could not be read; no grounded evidence is available.",
            "GitHub issue creation is blocked: a grounded, confident report is required first.",
        ],
        primary_error=None,
        log_finding=None,
        code_finding=None,
        root_cause=None,
        fix_plan=None,
        safety_review=safety_gate.evaluate_safety(
            secrets_present=False,
            unverified_file_reference=False,
            confidence=0.0,
            summary="No evidence available; nothing is safe to act on automatically.",
            required_human_action=(
                "Locate the CI log / failure source and investigate manually."
            ),
        ),
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Evidence builders (all grounded in tool output)
# ---------------------------------------------------------------------------


def _primary_error_str(log: CILogResult) -> str | None:
    if log.primary_error is None:
        return None
    return f"{log.primary_error.error_type}: {log.primary_error.message}"


def _find_failed_line(log: CILogResult) -> int | None:
    if not log.failing_test:
        return None
    for line in log.lines:
        if "FAILED" in line.text and log.failing_test in line.text:
            return line.line_number
    return None


def _ci_evidence(log: CILogResult, ci_rel: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    fail_line = _find_failed_line(log)
    if log.failing_test and fail_line is not None:
        items.append(
            EvidenceItem(
                id="ev_ci_test",
                source=ci_rel,
                source_type="ci_log",
                summary="Failing test identified in CI.",
                snippet=f"{log.failing_test} FAILED",
                path=ci_rel,
                line_start=fail_line,
                line_end=fail_line,
            )
        )

    if log.primary_error is not None:
        items.append(
            EvidenceItem(
                id="ev_ci_error",
                source=ci_rel,
                source_type="ci_log",
                summary="CI log shows the primary error.",
                snippet=log.primary_error.raw_line.strip(),
                path=ci_rel,
                line_start=log.primary_error.line_number,
                line_end=log.primary_error.line_number,
            )
        )

    return items


def _secret_evidence(log: CILogResult, ci_rel: str) -> list[EvidenceItem]:
    """One grounded evidence item per redacted line (real line + redacted text)."""
    items: list[EvidenceItem] = []
    index = 0
    for line in log.lines:
        if REDACTION_MARKER not in line.text:
            continue
        index += 1
        match = _MARKER_TYPE.search(line.text)
        kind = match.group(1) if match else "unknown"
        items.append(
            EvidenceItem(
                id=f"ev_secret_{index}",
                source=ci_rel,
                source_type="ci_log",
                summary=f"Credential-like value ({kind}) found in CI log and redacted.",
                snippet=line.text.strip(),
                path=ci_rel,
                line_start=line.line_number,
                line_end=line.line_number,
                metadata={"redaction_applied": "true", "secret_kind": kind},
            )
        )
    return items


def _api_evidence(
    incident_dir: Path, scenario: str, intake: IncidentIntake
) -> list[EvidenceItem]:
    """Optional, grounded API-response evidence when the fixture exists."""
    api_path = incident_dir / "api_response.json"
    if not api_path.is_file():
        return []
    try:
        data = json.loads(api_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    response = data.get("response", {})
    status_code = response.get("status_code")
    if status_code is None:
        return []
    endpoint = data.get("endpoint") or intake.endpoint or "endpoint"
    return [
        EvidenceItem(
            id="ev_api",
            source=f"demo/incidents/{scenario}/api_response.json",
            source_type="api_response",
            summary=f"{endpoint} returned HTTP {status_code}.",
            snippet=redact_secrets(json.dumps({"status_code": status_code})),
            path=f"demo/incidents/{scenario}/api_response.json",
            metadata={"status_code": str(status_code)},
        )
    ]


def _candidate_frames(log: CILogResult) -> list[dict]:
    """Parse plausible *repo* stack frames from the redacted log lines.

    Third-party / tooling frames (venv, site-packages, …) are filtered out so we
    never treat a library file as repository code.
    """
    seen: set[tuple[str, int]] = set()
    frames: list[dict] = []
    for line in log.lines:
        for pattern in (_FRAME_COLON, _FRAME_FILE):
            match = pattern.search(line.text)
            if not match:
                continue
            path, raw_line, symbol = match.group(1), int(match.group(2)), match.group(3)
            if any(hint in path for hint in _NON_REPO_HINTS):
                continue
            key = (path, raw_line)
            if key in seen:
                continue
            seen.add(key)
            frames.append({"path": path, "line": raw_line, "symbol": symbol})
    return frames


def _grounded_frames(log: CILogResult, intake: IncidentIntake) -> list[dict]:
    """Keep only frames whose file + line actually exist in the repo.

    Reads each cited line back from the real file via ``read_file_snippet``, so
    the resulting snippet and line number are verified, never invented.
    """
    repo_root = _repo_root(intake)
    if repo_root is None:
        return []

    grounded: list[dict] = []
    for frame in _candidate_frames(log):
        try:
            verify_file_exists(repo_root, frame["path"])
            snippet = read_file_snippet(repo_root, frame["path"], frame["line"], frame["line"])
        except (FileNotFoundError, PathGuardError, ValueError):
            continue
        if not snippet.snippet.strip():
            # Line number is past the end of the file: do not cite it.
            continue
        grounded.append(
            {
                "path": frame["path"],
                "line": snippet.line_start,
                "symbol": frame["symbol"],
                "snippet": snippet.snippet.strip(),
                "verified": snippet.path_verified,
            }
        )
        if len(grounded) >= 4:
            break
    return grounded


def _code_evidence(frames: list[dict], intake: IncidentIntake) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    repo_prefix = (intake.repo_path or "").rstrip("/")
    for i, frame in enumerate(frames, start=1):
        rel = f"{repo_prefix}/{frame['path']}" if repo_prefix else frame["path"]
        items.append(
            EvidenceItem(
                id=f"ev_code_{i}",
                source=rel,
                source_type="repo_file",
                summary=f"Verified code at {frame['path']}:{frame['line']} in {frame['symbol']}.",
                snippet=frame["snippet"],
                path=rel,
                line_start=frame["line"],
                line_end=frame["line"],
                metadata={"path_verified": "true", "symbol": frame["symbol"]},
            )
        )
    return items


def _repo_root(intake: IncidentIntake) -> Path | None:
    if intake.repo_mode != "local" or not intake.repo_path:
        return None
    root = (PROJECT_ROOT / intake.repo_path).resolve()
    return root if root.is_dir() else None


def _dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[str] = set()
    out: list[EvidenceItem] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Scoring (deterministic policy)
# ---------------------------------------------------------------------------


def _confidence(has_error: bool, has_test: bool, code_grounded: bool) -> float:
    score = 0.0
    if has_error:
        score += 0.40
    if has_test:
        score += 0.15
    if code_grounded:
        score += 0.35
    return round(min(score, 0.95), 2)


def _severity(
    secrets_present: bool, code_grounded: bool, has_error: bool, above_threshold: bool
) -> str:
    if secrets_present:
        return "SEV2"
    if code_grounded and has_error:
        return "SEV2"
    if has_error and above_threshold:
        return "SEV3"
    return "UNKNOWN"


def _status(
    has_error: bool, code_grounded: bool, secrets_present: bool, above_threshold: bool
) -> str:
    if not has_error:
        return "blocked"
    if not code_grounded and not secrets_present and not above_threshold:
        return "blocked"
    return "awaiting_human_approval"


def _category(
    secrets_present: bool, code_grounded: bool, has_error: bool, log: CILogResult
) -> str:
    if secrets_present:
        return "secret_exposure"
    if not has_error or not code_grounded:
        return "undetermined"
    error = log.primary_error
    if error is not None and error.error_type == "AttributeError" and "has no attribute" in error.message:
        return "null_dereference"
    return "runtime_error"


# ---------------------------------------------------------------------------
# Finding builders
# ---------------------------------------------------------------------------


def _build_log_finding(
    log: CILogResult,
    primary_error: str | None,
    ci_evidence: list[EvidenceItem],
    secret_evidence: list[EvidenceItem],
    frames: list[dict],
    secrets_present: bool,
    has_error: bool,
) -> LogFinding:
    parts: list[str] = []
    if log.failing_test:
        parts.append(f"{log.failing_test} failed in CI.")
    if primary_error:
        parts.append(f"Primary error: {primary_error}.")
    if secrets_present:
        parts.append(f"{log.redactions_applied} credential-like value(s) were redacted.")
    if not parts:
        parts.append("No primary error could be extracted from the CI log.")

    blocked = []
    if not has_error:
        blocked.append("No primary error line was found in the CI log.")
    if not frames and has_error:
        blocked.append("No application stack frame grounds the failure to a repo file.")

    return LogFinding(
        summary=redact_secrets(" ".join(parts)),
        confidence=round(0.4 * has_error + 0.15 * bool(log.failing_test) + 0.35, 2)
        if has_error
        else 0.2,
        evidence=ci_evidence + secret_evidence,
        needs_human_review=secrets_present or not has_error,
        blocked_reasons=blocked,
        primary_error=primary_error,
        failing_test=log.failing_test,
        stack_trace_summary=_stack_summary(log, frames, primary_error),
        redactions_applied=log.redactions_applied,
    )


def _stack_summary(
    log: CILogResult, frames: list[dict], primary_error: str | None
) -> str | None:
    if frames:
        last = frames[-1]
        return redact_secrets(
            f"{primary_error or 'Error'} surfaced in {last['symbol']} at "
            f"{last['path']}:{last['line']}."
        )
    if log.stack_trace is not None:
        return redact_secrets(
            f"Traceback present (CI log lines {log.stack_trace.line_start}-"
            f"{log.stack_trace.line_end}) but no application frame could be grounded "
            f"to a repo file."
        )
    return None


def _build_code_finding(
    frames: list[dict],
    code_evidence: list[EvidenceItem],
    missing_files: list[str],
    code_grounded: bool,
) -> CodeFinding | None:
    if not frames and not missing_files:
        return None

    matched = sorted({f["path"] for f in frames})
    symbols = sorted({f["symbol"] for f in frames})

    if code_grounded:
        summary = (
            f"Grounded the failure to {', '.join(matched)} "
            f"(symbols: {', '.join(symbols) or 'n/a'})."
        )
    else:
        summary = (
            f"The stack trace references {', '.join(missing_files)}, which is not "
            f"present in the searched repository snapshot."
        )

    return CodeFinding(
        summary=summary,
        confidence=0.85 if code_grounded else 0.3,
        evidence=code_evidence,
        needs_human_review=not code_grounded,
        blocked_reasons=[] if code_grounded else ["Referenced file(s) not found in repo."],
        matched_files=matched,
        suspected_symbols=symbols,
        missing_files=missing_files,
    )


def _build_root_cause(
    category: str,
    primary_error: str | None,
    frames: list[dict],
    secret_evidence: list[EvidenceItem],
    code_evidence: list[EvidenceItem],
    ci_evidence: list[EvidenceItem],
    confidence: float,
    needs_review: bool,
    secrets_present: bool,
    code_grounded: bool,
) -> RootCauseHypothesis:
    if category == "secret_exposure":
        n = len(secret_evidence)
        summary = (
            f"{n} credential-like value(s) were exposed in the CI log (now redacted). "
            f"The exposed secrets must be treated as compromised and rotated"
            + (f"; the run also failed with {primary_error}." if primary_error else ".")
        )
        evidence = secret_evidence[:3]
        alternatives = ["Treat the run failure as a separate, lower-severity ticket from the secret exposure."]
    elif category == "null_dereference" and frames:
        deref = frames[-1]
        summary = (
            f"{deref['symbol']} at {deref['path']}:{deref['line']} dereferences a value "
            f"that can be None, raising {primary_error}."
        )
        evidence = code_evidence[:2] + ci_evidence[-1:]
        alternatives = ["An upstream lookup returning no record (still requires the same None guard at the call site)."]
    elif category == "runtime_error" and frames:
        last = frames[-1]
        summary = (
            f"{primary_error} originates in {last['symbol']} at "
            f"{last['path']}:{last['line']} per the CI traceback."
        )
        evidence = code_evidence[:2] + ci_evidence[-1:]
        alternatives = ["A dependency or input-data issue surfacing at the same line."]
    else:  # undetermined
        summary = (
            "Insufficient grounded evidence to identify a single root cause. The "
            "failure is not tied to any verified repository location, so it is "
            "escalated to human review rather than given a confident diagnosis."
        )
        evidence = ci_evidence
        alternatives = [
            "Possible upstream/dependency outage (unconfirmed).",
            "Possible network/transport timeout unrelated to application code (unconfirmed).",
        ]

    return RootCauseHypothesis(
        summary=redact_secrets(summary),
        confidence=confidence,
        evidence=evidence,
        needs_human_review=needs_review,
        blocked_reasons=[] if (code_grounded or secrets_present) else ["Root cause undetermined from available evidence."],
        category=category,
        supporting_evidence_ids=[item.id for item in evidence],
        alternatives=alternatives,
    )


def _build_fix_plan(
    category: str,
    frames: list[dict],
    intake: IncidentIntake,
    log: CILogResult,
    code_evidence: list[EvidenceItem],
    secret_evidence: list[EvidenceItem],
) -> FixPlan | None:
    repo_prefix = (intake.repo_path or "").rstrip("/")

    if category in {"null_dereference", "runtime_error"} and frames:
        deref = frames[-1]
        # Anchor the guard to the line above the dereference *in the same file*
        # (e.g. the lookup call), not an unrelated test frame.
        same_file = [f for f in frames if f["path"] == deref["path"] and f["line"] < deref["line"]]
        lookup = same_file[-1] if same_file else deref
        regression = []
        if log.failing_test:
            regression.append(log.failing_test)
        regression.append(
            "Add a test asserting the missing/None case returns a handled error, not a 500."
        )
        return FixPlan(
            summary=redact_secrets(
                "Guard the value before dereferencing it and return an explicit error "
                "instead of crashing."
            ),
            confidence=0.8,
            evidence=code_evidence[:2],
            needs_human_review=False,
            blocked_reasons=[],
            patch_strategy="patch",
            steps=[
                f"At {deref['path']}:{deref['line']}, check the looked-up value for None "
                f"before using it.",
                f"Only dereference once the value is confirmed non-None; otherwise return "
                f"an explicit error (e.g. HTTP 404) at the call site near "
                f"{lookup['path']}:{lookup['line']}.",
            ],
            regression_tests=regression,
            rollback_plan=[f"Revert the guard in {repo_prefix}/{deref['path']}."],
            risks=["Low: the change is a local guard clause at the failing call site."],
        )

    if category == "secret_exposure":
        return FixPlan(
            summary=redact_secrets(
                "Rotate the exposed credentials and stop printing secrets to CI logs."
            ),
            confidence=0.75,
            evidence=secret_evidence[:1],
            needs_human_review=True,
            blocked_reasons=[],
            patch_strategy="mitigation",
            steps=[
                "Rotate every exposed credential — treat all redacted values as compromised.",
                "Remove the debug statements that print credentials; reference masked CI "
                "secrets instead.",
            ],
            regression_tests=[],
            rollback_plan=["No code rollback needed for rotation; revert logging changes if the deploy breaks."],
            risks=["Credential rotation may briefly interrupt the pipeline until new secrets propagate."],
        )

    # Undetermined: do not propose a fix without a grounded root cause.
    return None


def _build_safety_review(
    *,
    secrets_present: bool,
    unverified_reference: bool,
    confidence: float,
    redactions_applied: int,
) -> SafetyReview:
    """Delegate to the authoritative deterministic safety gate.

    The gate (``app.services.safety_gate``) is the single source of truth for the
    safety verdict; this wrapper only forwards the grounded signals the
    investigation computed. ``unverified_reference`` is ``True`` when the report
    cites a repo path the tools could not verify (e.g. a stack frame in a file
    that is not present in the searched repo snapshot).
    """
    return safety_gate.evaluate_safety(
        secrets_present=secrets_present,
        unverified_file_reference=unverified_reference,
        confidence=confidence,
        redactions_applied=redactions_applied,
    )


# ---------------------------------------------------------------------------
# Narrative helpers (built only from grounded facts, then redacted)
# ---------------------------------------------------------------------------


def _title(
    scenario: str,
    intake: IncidentIntake,
    category: str,
    primary_error: str | None,
    frames: list[dict],
) -> str:
    if category == "secret_exposure":
        return f"Sensitive values exposed in CI log for {intake.service}"
    if frames:
        where = frames[-1]["path"]
        head = intake.endpoint or intake.service
        error_type = (primary_error or "error").split(":")[0]
        return f"{head} failing: {error_type} in {where}"
    if category == "undetermined":
        return f"{intake.service} CI failure with undetermined root cause"
    return f"{intake.service}: {primary_error or 'CI failure'}"


def _summary(
    category: str,
    intake: IncidentIntake,
    primary_error: str | None,
    log: CILogResult,
    frames: list[dict],
    secrets_present: bool,
) -> str:
    if category == "secret_exposure":
        return (
            f"The {intake.service} CI run exposed {log.redactions_applied} credential-like "
            f"value(s), all redacted before this report was produced"
            + (f", and failed with {primary_error}" if primary_error else "")
            + ". Treat the credentials as compromised and rotate them."
        )
    if frames:
        deref = frames[-1]
        test = f"{log.failing_test} fails: " if log.failing_test else ""
        return (
            f"{test}{primary_error}. The failure is grounded at "
            f"{deref['path']}:{deref['line']} in {deref['symbol']} ({category})."
        )
    if primary_error:
        return (
            f"{log.failing_test or 'A CI check'} failed with {primary_error}, but no "
            f"application stack frame grounds it to a repo file. Escalated to human review."
        )
    return "No primary error could be extracted from the CI log; human investigation required."


def _report_blocked_reasons(
    secrets_present: bool,
    code_grounded: bool,
    above_threshold: bool,
    has_error: bool,
    confidence: float,
) -> list[str]:
    reasons: list[str] = []
    if not has_error:
        reasons.append("No primary error could be extracted from the CI log.")
    if secrets_present:
        reasons.append(
            "Report is derived from CI logs that contained credential-like values; "
            "the source is untrusted and the secrets must be rotated."
        )
    if not code_grounded and has_error:
        reasons.append("No verified repository location grounds the failure.")
    if not above_threshold:
        reasons.append(
            f"Confidence ({confidence:.2f}) is below the {CONFIDENCE_THRESHOLD:.2f} "
            f"threshold for an automated diagnosis."
        )
    if reasons:
        reasons.append(
            "GitHub issue creation requires a clean, grounded, confident report plus "
            "explicit human approval."
        )
    return reasons


# ---------------------------------------------------------------------------
# GitHub issue preview
# ---------------------------------------------------------------------------


def _build_issue_preview(
    report: IncidentReport, *, extra_labels: list[str]
) -> tuple[str, str, list[str]]:
    title = build_issue_title(report)
    body = build_issue_body(report)
    labels = ["incident", f"severity:{report.severity.lower()}"]
    for label in extra_labels:
        if label not in labels:
            labels.append(label)
    return title, body, labels
