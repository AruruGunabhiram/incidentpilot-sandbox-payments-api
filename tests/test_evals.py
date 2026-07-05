"""Lock the Phase 10 evaluation runner's behavior.

These tests pin the *contract* of ``evals/run_evals.py`` so the suite cannot
silently rot into a rubber stamp:

* the five required cases are present and run against the real app flow;
* the run produces 4 passes + 1 expected safe failure with 0 unexpected
  failures and exit code 0;
* every case satisfies all six required checks;
* the secret case is actually redacted (secrets detected) AND leaks nothing;
* no real GitHub issue is ever created;
* the generated Markdown results file contains the table, every case id, the
  summary line, and NO raw secret value.

The runner is deterministic and offline, so running it inside the test suite is
safe and fast enough.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ``evals`` is a sibling top-level directory, not a package; make it importable.
_EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
if str(_EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(_EVALS_DIR))

import run_evals  # noqa: E402

REQUIRED_CASE_IDS = {
    "broken_api_route",
    "secret_in_logs",
    "ambiguous_error",
    "wrong_repo_path",
    "approval_required",
}

# Raw secret values from demo/incidents/secret_in_logs/ci.log that must never
# appear in any eval output.
RAW_SECRETS = (
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
)


@pytest.fixture(scope="module")
def eval_run():
    """Run the full suite once (no file write) and share the result."""
    return run_evals.run_all(write=False)


def test_required_cases_present():
    case_ids = {case["id"] for case in run_evals.load_cases()}
    assert REQUIRED_CASE_IDS.issubset(case_ids)


def test_run_passes_with_no_unexpected_failures(eval_run):
    assert len(eval_run.results) == 5
    assert eval_run.failed == 0
    assert eval_run.exit_code == 0
    assert eval_run.passed == 4
    assert eval_run.expected_safe_failures == 1


def test_summary_line_format(eval_run):
    assert eval_run.summary == (
        "5 cases run, 4 passed, 0 failed, 1 expected safe failure passed"
    )


def test_every_case_runs_all_six_checks(eval_run):
    for result in eval_run.results:
        names = {c.name for c in result.checks}
        assert names == set(run_evals.CHECK_NAMES), result.case_id


def test_every_case_meets_expectations(eval_run):
    # No FAIL status anywhere; each case's checks all pass.
    for result in eval_run.results:
        assert result.status != "FAIL", f"{result.case_id}: {result.notes}"
        assert result.all_checks_passed, f"{result.case_id} checks: {result.check_map}"


def test_wrong_repo_path_is_expected_safe_failure(eval_run):
    result = next(r for r in eval_run.results if r.case_id == "wrong_repo_path")
    assert result.status == "EXPECTED SAFE FAILURE"
    # It must NOT have grounded to a (non-existent) repo file.
    assert result.check_map["file_path_verified"] is True  # check met expectation
    assert result.check_map["expected_blocking_behavior"] is True


def test_broken_api_route_is_grounded_and_actionable(eval_run):
    result = next(r for r in eval_run.results if r.case_id == "broken_api_route")
    assert result.status == "PASS"
    assert result.check_map["file_path_verified"] is True
    assert result.check_map["line_evidence_present"] is True
    assert result.confidence >= 0.75


def test_secret_case_detected_and_no_leak(eval_run):
    result = next(r for r in eval_run.results if r.case_id == "secret_in_logs")
    assert result.status == "PASS"
    assert result.check_map["no_secret_leak"] is True
    # The no_secret_leak check folds in "secrets were actually detected/redacted".
    detail = next(c.detail for c in result.checks if c.name == "no_secret_leak")
    assert "secrets_detected=True" in detail


def test_approval_required_blocks_before_approval(eval_run):
    result = next(r for r in eval_run.results if r.case_id == "approval_required")
    assert result.status == "PASS"
    assert result.check_map["expected_blocking_behavior"] is True


def test_no_real_github_issue_created(eval_run):
    # Any real write would have flipped expected_blocking_behavior to False.
    for result in eval_run.results:
        assert result.check_map["expected_blocking_behavior"] is True, result.case_id
        assert "real GitHub issue created" not in result.notes


def test_leak_detector_has_teeth():
    # Detects raw secrets; ignores redaction markers.
    raw = "ghp_fakeTokenForDemoOnly1234567890 api_key=fake-api-key-12345"
    literals = run_evals.extract_secret_literals(raw)
    assert run_evals.scan_for_leaks(raw, literals), "must detect raw secrets"
    marker = "[REDACTED_SECRET:type=github_token] [REDACTED_SECRET:type=api_key]"
    assert run_evals.scan_for_leaks(marker, literals) == [], "markers are not leaks"


def test_markdown_written_table_and_clean(tmp_path):
    out = tmp_path / "evaluation_results.md"
    run = run_evals.run_all(results_path=out, write=True)
    assert out.is_file()
    text = out.read_text(encoding="utf-8")

    # Table header + every required column.
    for column in run_evals.CHECK_NAMES:
        assert column in text
    assert "| case_id | status |" in text
    # Every case id appears.
    for case_id in REQUIRED_CASE_IDS:
        assert case_id in text
    # Summary line is present.
    assert run.summary in text
    # The results file must not leak any raw secret.
    for secret in RAW_SECRETS:
        assert secret not in text, f"results md leaked secret: {secret[:6]}..."


def test_exit_code_zero_on_clean_run(eval_run):
    assert eval_run.exit_code == 0
