#!/usr/bin/env python3
"""IncidentPilot — Phase 10 evaluation runner.

Runs a small, deterministic, offline evaluation suite over the REAL application
flow (FastAPI routes + deterministic services), not a mock pipeline. For each
case declared in ``evals/evaluation_cases.yaml`` it drives::

    POST /incidents/trigger            -> register the demo incident
    investigation_service.investigate_incident(...)
                                       -> grounded IncidentReport written to a
                                          throwaway reports dir
    POST /incidents/{id}/github/issue  -> attempt issue BEFORE approval (dry-run)
    POST /incidents/{id}/approve       -> record explicit human approval
    POST /incidents/{id}/github/issue  -> attempt issue AFTER approval (dry-run)

and then computes six checks per case:

    file_path_verified, line_evidence_present, confidence_reasonable,
    no_secret_leak, safe_action_policy_passed, expected_blocking_behavior

Design guarantees:

* Deterministic and fully offline. No network call is ever made; GitHub issue
  creation is forced to dry-run AND the GitHub env is scrubbed, so no real issue
  can be created during evaluation.
* Hermetic. Each case triggers, approves, and checks GitHub behavior through the
  real FastAPI routes, but calls ``investigation_service.investigate_incident``
  directly with a throwaway reports directory. This keeps tracked reports under
  ``app/storage/reports/`` untouched while still exercising the real
  investigation and persistence code.
* Independent verification. ``file_path_verified`` / ``line_evidence_present``
  do NOT trust the report's own claims — every cited repo path is re-resolved
  through ``app.tools.path_guard`` and re-read through ``app.tools.repo_search``.
* Secret leak detection is independent of the redactor under test: secret-like
  values are extracted straight from the raw CI log and asserted absent from the
  persisted report and any issue preview.

Exit code is non-zero ONLY on an UNEXPECTED failure. An ``expected_safe_failure``
case (the system correctly refusing an un-groundable incident) is counted and
reported separately, never hidden as a normal pass and never failing the run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Make ``app`` importable when run as a bare script (``python evals/run_evals.py``).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.report import IncidentReport  # noqa: E402
from app.services import investigation_service  # noqa: E402
from app.services import safety_gate  # noqa: E402
from app.storage import incident_store  # noqa: E402
from app.tools.path_guard import PathGuardError  # noqa: E402
from app.tools.repo_search import read_file_snippet  # noqa: E402

CASES_PATH = REPO_ROOT / "evals" / "evaluation_cases.yaml"
RESULTS_PATH = REPO_ROOT / "evals" / "results" / "evaluation_results.md"
INCIDENTS_DIR = REPO_ROOT / "demo" / "incidents"

CHECK_NAMES = (
    "file_path_verified",
    "line_evidence_present",
    "confidence_reasonable",
    "no_secret_leak",
    "safe_action_policy_passed",
    "expected_blocking_behavior",
)


# ---------------------------------------------------------------------------
# Independent secret detection (deliberately NOT importing the redactor)
# ---------------------------------------------------------------------------

# Whole-value secret shapes. Each whole match is a literal that must never appear
# in any output.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    # connection string carrying user:password@host
    re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@]+:[^\s/@]+@\S+"),
]

# key=value / key: value shapes; group(1) is the secret value to track.
_SECRET_KV_PATTERNS = [
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*([^\s,&'\"]+)"),
    re.compile(r"(?i)\bpassword\s*[=:]\s*([^\s,&'\"]+)"),
    re.compile(r"(?i)\bDATABASE_URL\s*[=:]\s*(\S+)"),
    re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]+)"),
]

_REDACTION_MARKER_RE = re.compile(r"\[REDACTED_SECRET:type=\w+\]")
_MIN_SECRET_LEN = 6


def extract_secret_literals(raw_text: str) -> set[str]:
    """Pull concrete secret-like values out of raw (un-redacted) text.

    These literals are the ground truth for the leak check: none of them may
    survive into a persisted report or issue preview.
    """
    literals: set[str] = set()
    for pattern in _SECRET_VALUE_PATTERNS:
        for match in pattern.finditer(raw_text):
            literals.add(match.group(0))
    for pattern in _SECRET_KV_PATTERNS:
        for match in pattern.finditer(raw_text):
            literals.add(match.group(0))  # whole "key=value"
            if match.groups():
                literals.add(match.group(1))  # the value alone
    return {s for s in literals if len(s) >= _MIN_SECRET_LEN}


def scan_for_leaks(text: str, literals: set[str]) -> list[str]:
    """Return secret-like findings in ``text`` (literals + residual patterns).

    Redaction markers are stripped first so the markers themselves can never be
    mistaken for a leaked secret.
    """
    if not text:
        return []
    cleaned = _REDACTION_MARKER_RE.sub("", text)
    found: list[str] = []
    for literal in literals:
        if literal in cleaned:
            found.append(f"literal:{literal[:6]}...")
    for pattern in _SECRET_VALUE_PATTERNS:
        for match in pattern.finditer(cleaned):
            found.append(f"pattern:{match.group(0)[:6]}...")
    for pattern in _SECRET_KV_PATTERNS:
        for match in pattern.finditer(cleaned):
            value = match.group(1) if match.groups() else match.group(0)
            if value and len(value) >= _MIN_SECRET_LEN:
                found.append(f"kv:{match.group(0)[:8]}...")
    # De-duplicate while preserving order.
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    scenario: str
    category: str
    status: str  # PASS | EXPECTED SAFE FAILURE | FAIL
    confidence: float
    checks: list[CheckResult] = field(default_factory=list)
    notes: str = ""

    @property
    def check_map(self) -> dict[str, bool]:
        return {c.name: c.passed for c in self.checks}

    @property
    def all_checks_passed(self) -> bool:
        return all(c.passed for c in self.checks)


@dataclass
class EvalRun:
    results: list[CaseResult]
    summary: str
    passed: int
    failed: int
    expected_safe_failures: int
    exit_code: int


# ---------------------------------------------------------------------------
# Per-case verification helpers
# ---------------------------------------------------------------------------


def _intake_repo_root(scenario: str) -> Path | None:
    """Return the resolved repo root for a scenario's intake, or None."""
    intake_path = INCIDENTS_DIR / scenario / "intake.json"
    try:
        data = json.loads(intake_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("repo_mode") != "local" or not data.get("repo_path"):
        return None
    root = (REPO_ROOT / data["repo_path"]).resolve()
    return root if root.is_dir() else None


def verify_grounding(report: IncidentReport) -> dict:
    """Independently re-verify every cited repo file path + line.

    Trusts nothing the report claims: each ``repo_file`` evidence item is
    re-resolved under the repo root and the cited line is re-read. Returns the
    independently confirmed grounding signals.
    """
    repo_file_evidence = [e for e in report.evidence if e.source_type == "repo_file"]

    verified_paths: list[str] = []
    line_verified: list[str] = []
    hallucinated: list[str] = []

    for ev in repo_file_evidence:
        path = ev.path or ev.source
        if not path:
            hallucinated.append("<missing path>")
            continue
        try:
            start = ev.line_start or 1
            end = ev.line_end or start
            snippet = read_file_snippet(REPO_ROOT, path, start, end)
        except (FileNotFoundError, PathGuardError, ValueError):
            # Cited as a verified repo file but cannot be re-read => hallucination.
            hallucinated.append(path)
            continue
        if not snippet.snippet.strip():
            hallucinated.append(path)
            continue
        verified_paths.append(path)
        if ev.line_start is not None:
            line_verified.append(f"{path}:{ev.line_start}")

    missing_files: list[str] = []
    if report.code_finding is not None:
        missing_files = list(report.code_finding.missing_files)

    return {
        "repo_file_count": len(repo_file_evidence),
        "verified_paths": verified_paths,
        "line_verified": line_verified,
        "hallucinated": hallucinated,
        "missing_files": missing_files,
    }


def _post_issue(client: TestClient, incident_id: str) -> dict:
    """Attempt GitHub issue creation, forced to dry-run; return a compact dict."""
    resp = client.post(
        f"/incidents/{incident_id}/github/issue", json={"dry_run": True}
    )
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return {"status": resp.status_code, "body": body, "text": resp.text}


def _outcome_matches(outcome: dict, expected_code: str) -> bool:
    status = outcome["status"]
    body = outcome["body"]
    if expected_code == "blocked_approval":
        return status == 403 and body.get("reason") == "approval_required"
    if expected_code == "blocked_safety":
        return status == 403 and body.get("reason") == "safety_review_failed"
    if expected_code in ("dry_run", "allowed_dry_run"):
        return (
            status == 200
            and body.get("created") is False
            and body.get("dry_run") is True
        )
    return False


# ---------------------------------------------------------------------------
# Run a single case
# ---------------------------------------------------------------------------


def run_case(case: dict) -> CaseResult:
    case_id = case["id"]
    scenario = case["scenario"]
    category = case.get("category", "clean_actionable")
    expect = case["expect"]

    # Hermetic, deterministic slate for this case.
    incident_store.reset_store()
    client = TestClient(app)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"eval_{case_id}_"))

    notes: list[str] = []
    try:
        # 1. Trigger via the real HTTP route.
        trig = client.post("/incidents/trigger", json={"scenario": scenario})
        if trig.status_code != 200:
            return _hard_fail(case_id, scenario, category,
                              f"trigger failed: HTTP {trig.status_code} {trig.text[:120]}")
        incident_id = trig.json()["incident_id"]

        # 2. Investigate via the real deterministic service, persisting the
        #    redacted report to a THROWAWAY dir. This intentionally avoids the
        #    HTTP investigate route so evals can validate persistence without
        #    touching tracked app/storage/reports artifacts.
        report = investigation_service.investigate_incident(
            incident_id, persist=True, reports_dir=tmp_dir
        )

        json_path = tmp_dir / f"{report.incident_id}.json"
        md_path = tmp_dir / f"{report.incident_id}.md"
        persisted_ok = json_path.is_file() and md_path.is_file()
        if not persisted_ok:
            notes.append("persisted report artifacts missing")
        report_json_text = json_path.read_text(encoding="utf-8") if json_path.is_file() else ""
        report_md_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""

        # 3. Independent grounding re-verification (trusts the filesystem, not the report).
        grounding = verify_grounding(report)

        # 4. GitHub issue BEFORE approval (forced dry-run, no network).
        before = _post_issue(client, incident_id)

        # 5. Record explicit human approval via the real route.
        approve = client.post(f"/incidents/{incident_id}/approve", json={})
        if approve.status_code != 200:
            notes.append(f"approve route HTTP {approve.status_code}")

        # 6. GitHub issue AFTER approval (still forced dry-run).
        after = _post_issue(client, incident_id)

        # Safety net: a real write must NEVER happen during evals.
        for label, outcome in (("before", before), ("after", after)):
            if outcome["status"] == 200 and outcome["body"].get("created") is True:
                notes.append(f"FATAL: real GitHub issue created ({label} approval)")

        # Collect every text surface a secret could leak into.
        issue_preview = ""
        for outcome in (before, after):
            body = outcome["body"]
            issue_preview += (body.get("title") or "") + "\n" + (body.get("body_preview") or "")

        literals = extract_secret_literals(_raw_ci_log(scenario))
        leak_targets = [report_json_text, report_md_text, issue_preview]
        leaks: list[str] = []
        for target in leak_targets:
            leaks.extend(scan_for_leaks(target, literals))
        leaks = list(dict.fromkeys(leaks))

        # ----- Compute the six checks ---------------------------------------
        checks = _compute_checks(
            expect=expect,
            report=report,
            grounding=grounding,
            leaks=leaks,
            before=before,
            after=after,
            notes=notes,
        )

        # Persisted-artifacts result folds into no_secret_leak's spirit; record it.
        if persisted_ok:
            notes.append("persisted JSON+MD verified on disk (temp)")

        all_passed = all(c.passed for c in checks)
        if not all_passed:
            status = "FAIL"
        elif category == "expected_safe_failure":
            status = "EXPECTED SAFE FAILURE"
        else:
            status = "PASS"

        return CaseResult(
            case_id=case_id,
            scenario=scenario,
            category=category,
            status=status,
            confidence=float(report.confidence),
            checks=checks,
            notes="; ".join(notes),
        )
    except Exception as exc:  # noqa: BLE001 - surface any unexpected error as FAIL
        return _hard_fail(case_id, scenario, category, f"runner exception: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _raw_ci_log(scenario: str) -> str:
    path = INCIDENTS_DIR / scenario / "ci.log"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _hard_fail(case_id, scenario, category, detail) -> CaseResult:
    checks = [CheckResult(name, False, "not evaluated") for name in CHECK_NAMES]
    return CaseResult(
        case_id=case_id,
        scenario=scenario,
        category=category,
        status="FAIL",
        confidence=0.0,
        checks=checks,
        notes=detail,
    )


def _compute_checks(
    *, expect, report, grounding, leaks, before, after, notes
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    safety = report.safety_review

    # --- 1. file_path_verified -----------------------------------------------
    want_verified = bool(expect.get("file_path_verified"))
    has_verified = len(grounding["verified_paths"]) > 0
    hallucinated = grounding["hallucinated"]
    if hallucinated:
        fpv = False
        detail = f"hallucinated/unverifiable paths cited: {hallucinated}"
    elif want_verified:
        fpv = has_verified
        detail = f"verified={grounding['verified_paths']}"
    else:
        fpv = not has_verified
        detail = "no grounded repo file (expected)"
        if expect.get("expect_missing_files"):
            has_missing = len(grounding["missing_files"]) > 0
            fpv = fpv and has_missing
            detail += f"; missing_files recorded={grounding['missing_files']}"
    checks.append(CheckResult("file_path_verified", fpv, detail))

    # --- 2. line_evidence_present --------------------------------------------
    want_lines = bool(expect.get("line_evidence_present"))
    has_lines = len(grounding["line_verified"]) > 0
    if want_lines:
        lep = has_lines and not hallucinated
        detail = f"line evidence={grounding['line_verified']}"
    else:
        lep = not has_lines
        detail = "no code line evidence (expected)"
    checks.append(CheckResult("line_evidence_present", lep, detail))

    # --- 3. confidence_reasonable --------------------------------------------
    band = expect.get("confidence", {}) or {}
    lo = float(band.get("min", 0.0))
    hi = float(band.get("max", 1.0))
    in_band = lo <= float(report.confidence) <= hi
    nhr_expected = expect.get("needs_human_review")
    nhr_ok = nhr_expected is None or report.needs_human_review == bool(nhr_expected)
    cr = in_band and nhr_ok
    detail = f"confidence={report.confidence:.2f} in [{lo:.2f},{hi:.2f}]={in_band}, needs_human_review={report.needs_human_review}"
    checks.append(CheckResult("confidence_reasonable", cr, detail))

    # --- 4. no_secret_leak ----------------------------------------------------
    no_leak = len(leaks) == 0
    detail = "no raw secret in report/preview"
    if leaks:
        detail = f"LEAKS: {leaks}"
    if expect.get("secrets_detected"):
        red_min = int(expect.get("redactions_min", 1))
        secrets_ok = bool(safety and safety.secrets_detected) and (
            safety.redactions_applied >= red_min if safety else False
        )
        no_leak = no_leak and secrets_ok
        detail += f"; secrets_detected={getattr(safety, 'secrets_detected', None)}, redactions={getattr(safety, 'redactions_applied', 0)}"
    checks.append(CheckResult("no_secret_leak", no_leak, detail))

    # --- 5. safe_action_policy_passed ----------------------------------------
    want_eligible = bool(expect.get("issue_eligible"))
    block_reasons = safety_gate.github_issue_block_reasons(report)
    eligible_actual = bool(safety and safety.approved_for_github_issue) and not block_reasons
    consistency_ok = bool(safety) and (
        safety.approved_for_github_issue == (len(block_reasons) == 0)
    )
    pr_never_ok = bool(safety) and (safety.approved_for_pr is False)
    sapp = (eligible_actual == want_eligible) and consistency_ok and pr_never_ok
    detail = (
        f"issue_eligible actual={eligible_actual} expected={want_eligible}; "
        f"block_reasons={len(block_reasons)}; pr_approved={getattr(safety, 'approved_for_pr', None)}"
    )
    checks.append(CheckResult("safe_action_policy_passed", sapp, detail))

    # --- 6. expected_blocking_behavior ---------------------------------------
    blocking = expect.get("blocking", {}) or {}
    before_exp = blocking.get("before_approval")
    after_exp = blocking.get("after_approval")
    before_ok = _outcome_matches(before, before_exp) if before_exp else True
    after_ok = _outcome_matches(after, after_exp) if after_exp else True
    # No real write may ever occur.
    no_write = not (
        before["status"] == 200 and before["body"].get("created") is True
    ) and not (after["status"] == 200 and after["body"].get("created") is True)
    ebb = before_ok and after_ok and no_write
    detail = (
        f"before: HTTP {before['status']}/{before['body'].get('reason') or before['body'].get('dry_run')} "
        f"(want {before_exp}); after: HTTP {after['status']}/{after['body'].get('reason') or after['body'].get('dry_run')} "
        f"(want {after_exp})"
    )
    checks.append(CheckResult("expected_blocking_behavior", ebb, detail))

    return checks


# ---------------------------------------------------------------------------
# Orchestration + reporting
# ---------------------------------------------------------------------------


def _scrub_github_env() -> None:
    """Remove any GitHub config so issue creation can never go live in evals."""
    for key in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO"):
        os.environ.pop(key, None)
    os.environ["GITHUB_DRY_RUN"] = "true"
    # Settings are cached; rebuild them from the scrubbed environment.
    get_settings.cache_clear()


def load_cases(cases_path: Path | str = CASES_PATH) -> list[dict]:
    data = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8"))
    cases = data.get("cases", []) if isinstance(data, dict) else []
    if not cases:
        raise ValueError(f"No cases found in {cases_path}.")
    return cases


def build_summary(results: list[CaseResult]) -> tuple[str, int, int, int]:
    passed = sum(1 for r in results if r.status == "PASS")
    esf = sum(1 for r in results if r.status == "EXPECTED SAFE FAILURE")
    failed = sum(1 for r in results if r.status == "FAIL")
    total = len(results)
    if failed == 0 and esf == 0:
        line = f"{total} cases run, {passed} passed, 0 failed"
    elif failed == 0:
        noun = "expected safe failure" if esf == 1 else "expected safe failures"
        line = f"{total} cases run, {passed} passed, 0 failed, {esf} {noun} passed"
    else:
        noun = "expected safe failure" if esf == 1 else "expected safe failures"
        line = (
            f"{total} cases run, {passed} passed, {failed} failed, "
            f"{esf} {noun}"
        )
    return line, passed, failed, esf


def _mark(passed: bool) -> str:
    return "✓" if passed else "✗"


def render_markdown(results: list[CaseResult], summary: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = []
    lines.append("# IncidentPilot — Evaluation Results")
    lines.append("")
    lines.append(f"_Generated: {now}_")
    lines.append("")
    lines.append(f"**Summary:** {summary}")
    lines.append("")
    lines.append(
        "Each case is driven through the real app flow "
        "(`trigger route -> investigation service with temp reports -> "
        "github/issue route -> approve route -> github/issue route`). "
        "GitHub writes are forced to dry-run; no real issue is created. "
        "`✓` = check met its expectation for that case."
    )
    lines.append("")

    header = (
        "| case_id | status | file_path_verified | line_evidence_present | "
        "confidence_reasonable | no_secret_leak | safe_action_policy_passed | "
        "expected_blocking_behavior | confidence | notes |"
    )
    sep = "|" + "|".join(["---"] * 10) + "|"
    lines.append(header)
    lines.append(sep)

    for r in results:
        cm = r.check_map
        note = (r.notes or "").replace("|", "\\|")
        if len(note) > 160:
            note = note[:157] + "..."
        row = (
            f"| {r.case_id} | {r.status} | {_mark(cm['file_path_verified'])} | "
            f"{_mark(cm['line_evidence_present'])} | {_mark(cm['confidence_reasonable'])} | "
            f"{_mark(cm['no_secret_leak'])} | {_mark(cm['safe_action_policy_passed'])} | "
            f"{_mark(cm['expected_blocking_behavior'])} | {r.confidence:.2f} | {note} |"
        )
        lines.append(row)

    lines.append("")
    lines.append("## Per-case detail")
    lines.append("")
    for r in results:
        lines.append(f"### {r.case_id} — {r.status}")
        lines.append("")
        lines.append(f"- scenario: `{r.scenario}` · category: `{r.category}` · confidence: `{r.confidence:.2f}`")
        for c in r.checks:
            detail = (c.detail or "").replace("|", "\\|")
            lines.append(f"- {_mark(c.passed)} **{c.name}** — {detail}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Legend: `PASS` = clean/grounded case meeting every expectation; "
        "`EXPECTED SAFE FAILURE` = the system correctly refused an un-groundable "
        "incident (counted separately, not a regression); `FAIL` = an unexpected "
        "result that fails the run."
    )
    lines.append("")
    return "\n".join(lines)


def run_all(
    cases_path: Path | str = CASES_PATH,
    results_path: Path | str = RESULTS_PATH,
    *,
    write: bool = True,
) -> EvalRun:
    """Run every case, optionally write the Markdown report, return the run."""
    _scrub_github_env()
    cases = load_cases(cases_path)
    results = [run_case(case) for case in cases]

    summary, passed, failed, esf = build_summary(results)
    markdown = render_markdown(results, summary)

    if write:
        out = Path(results_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")

    exit_code = 1 if failed > 0 else 0
    return EvalRun(
        results=results,
        summary=summary,
        passed=passed,
        failed=failed,
        expected_safe_failures=esf,
        exit_code=exit_code,
    )


def _print_terminal_summary(run: EvalRun, results_path: Path) -> None:
    print("IncidentPilot evaluation suite")
    print("=" * 60)
    for r in run.results:
        marks = " ".join(
            f"{name.split('_')[0][:4]}:{'✓' if r.check_map[name] else '✗'}"
            for name in CHECK_NAMES
        )
        print(f"  [{r.status:<20}] {r.case_id:<18} conf={r.confidence:.2f}  {marks}")
    print("-" * 60)
    print(f"  {run.summary}")
    print(f"  Markdown report: {results_path}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the IncidentPilot evaluation suite.")
    parser.add_argument("--cases", default=str(CASES_PATH), help="Path to evaluation_cases.yaml")
    parser.add_argument("--out", default=str(RESULTS_PATH), help="Path to write the Markdown results")
    parser.add_argument("--no-write", action="store_true", help="Do not write the Markdown file")
    args = parser.parse_args(argv)

    run = run_all(args.cases, args.out, write=not args.no_write)
    _print_terminal_summary(run, Path(args.out))
    return run.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
