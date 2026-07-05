"""Phase 3 deterministic toolchain integration check.

Proves the Phase 3 tools work end-to-end on local fixtures with **no agents,
no LLM calls, no GitHub API, and no network access**:

    ci_log_reader  ->  repo_search  ->  report_writer

The flow mirrors what the (later) agents will orchestrate, but here every step
is a plain deterministic function call:

1. Load ``demo/incidents/broken_api_route/ci.log`` safely (path-guarded).
2. Redact secrets before any extraction (done inside ``read_ci_log``; proven
   explicitly against a secret-bearing temp log built with ``tmp_path``).
3. Extract the failing pytest test.
4. Extract the primary error.
5. Search ``demo/demo_repo`` for the relevant function / path.
6. Build a minimal, grounded report dict.
7. Render Markdown and JSON report strings.
8. Assert no raw secret patterns survive into either output.
9. Assert evidence carries real line numbers.
10. Assert every cited repo path is verified.

Per the project rules, demo fixtures are never mutated. Where extra data is
needed (a log that actually contains secrets), it is created inside the test
with ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from app.tools.ci_log_reader import read_ci_log
from app.tools.redactor import REDACTION_MARKER, contains_secret
from app.tools.report_writer import build_markdown_report, build_report_dict, render_json
from app.tools.repo_search import search_repo

# --- fixture locations (absolute, so the test is CWD-independent) ------------

REPO_ROOT = Path(__file__).resolve().parents[1]
INCIDENT_DIR = REPO_ROOT / "demo" / "incidents" / "broken_api_route"
DEMO_REPO = REPO_ROOT / "demo" / "demo_repo"

# Repo-relative paths used only for human-readable evidence citations.
CI_LOG_REL = "demo/incidents/broken_api_route/ci.log"
PAYMENTS_REL = "demo/demo_repo/app/routes/payments.py"

# The fake secrets seeded into demo/incidents/secret_in_logs; none of these
# raw strings may ever appear in a rendered report.
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


def _build_minimal_report(
    *,
    failing_test: str,
    primary_error: str,
    fail_line: int,
    error_line: int,
    code_lookup: dict,
    code_deref: dict,
    repo_paths_verified: bool,
) -> dict:
    """Assemble a minimal, fully grounded report dict for the serializer.

    Every evidence item points at a real file with real 1-based line numbers
    that were returned by the tools above. A fake secret is deliberately
    embedded in one snippet to prove the report writer redacts it.
    """
    return {
        "incident_id": "inc_broken_api_route",
        "title": "POST /payments returns 500 due to unchecked None from get_user",
        "severity": "SEV2",
        "affected_service": "payments-api",
        "summary": (
            "The CI test "
            f"{failing_test} fails with {primary_error}. get_user can return "
            "None and create_payment dereferences user.id without a guard."
        ),
        "primary_error": primary_error,
        "confidence": 0.86,
        "evidence": [
            {
                "id": "ev_ci_test",
                "source": CI_LOG_REL,
                "source_type": "ci_log",
                "summary": "Failing test identified in CI.",
                "snippet": f"{failing_test} FAILED",
                "path": CI_LOG_REL,
                "line_start": fail_line,
                "line_end": fail_line,
            },
            {
                "id": "ev_ci_error",
                "source": CI_LOG_REL,
                "source_type": "ci_log",
                "summary": "CI traceback shows the exact runtime error.",
                "snippet": f"E   {primary_error}",
                "path": CI_LOG_REL,
                "line_start": error_line,
                "line_end": error_line,
            },
            {
                "id": "ev_code_lookup",
                "source": PAYMENTS_REL,
                "source_type": "repo_file",
                "summary": "get_user can return None for an unknown user id.",
                # Deliberately seed a fake secret here to exercise redaction.
                "snippet": code_lookup["snippet"] + "  # token=ghp_fakeTokenForDemoOnly1234567890",
                "path": PAYMENTS_REL,
                "line_start": code_lookup["line_start"],
                "line_end": code_lookup["line_end"],
            },
            {
                "id": "ev_code_deref",
                "source": PAYMENTS_REL,
                "source_type": "repo_file",
                "summary": "user.id is dereferenced without a None check.",
                "snippet": code_deref["snippet"],
                "path": PAYMENTS_REL,
                "line_start": code_deref["line_start"],
                "line_end": code_deref["line_end"],
            },
        ],
        "root_cause": {
            "category": "null_dereference",
            "summary": (
                "get_user returns None for an unknown user and create_payment "
                "accesses user.id without a guard, raising the AttributeError."
            ),
        },
        "fix_plan": {
            "summary": "Guard for a missing user before dereferencing user.id.",
            "steps": [
                "After user = get_user(request.user_id), raise a 404 when user is None.",
                "Only set payment.user_id = user.id once user is confirmed non-None.",
            ],
            "regression_tests": [failing_test],
        },
        "safety_review": {
            "summary": "Grounded in code and CI evidence; GitHub issue still needs approval.",
            "approved_for_display": True,
            "approved_for_github_issue": False,
            "risk_level": "low",
            "secrets_detected": False,
            "redactions_applied": 0,
            "secret_scan_passed": True,
            "secrets_redacted": False,
            "repo_paths_verified": repo_paths_verified,
            "human_approval_required": True,
            "required_human_action": "Review and approve GitHub issue creation.",
        },
        "needs_human_review": False,
        "blocked_reasons": [],
    }


def test_phase3_toolchain_end_to_end() -> None:
    """End-to-end deterministic run over the broken_api_route fixtures."""

    # --- 1. Load ci.log safely (path-guarded, confined to the incident dir) --
    log = read_ci_log(INCIDENT_DIR, "ci.log")
    assert log.lines, "ci.log should load into numbered lines"
    assert log.source_path == "ci.log"

    # --- 2. Secrets redacted before extraction -------------------------------
    # The broken_api_route log is clean, so redaction is a no-op here; we still
    # confirm the counter is wired and that the redactor ran over the text.
    assert log.redactions_applied == 0
    joined_log = "\n".join(ln.text for ln in log.lines)
    assert not contains_secret(joined_log)

    # --- 3. Extract the failing pytest test ----------------------------------
    failing_test = log.failing_test
    assert failing_test == "tests/test_payments.py::test_create_payment_success"

    # --- 4. Extract the primary error ----------------------------------------
    assert log.primary_error is not None
    assert log.primary_error.error_type == "AttributeError"
    assert "object has no attribute 'id'" in log.primary_error.message
    error_line = log.primary_error.line_number
    assert error_line >= 1
    primary_error = f"{log.primary_error.error_type}: {log.primary_error.message}"

    # Locate the FAILED line number deterministically from the loaded lines.
    fail_line = next(
        ln.line_number
        for ln in log.lines
        if "FAILED" in ln.text and failing_test in ln.text
    )

    # --- 5. Search the demo repo for the relevant function / path ------------
    # The exact failing-test name does not exist in demo_repo, so we search for
    # the function under test and the buggy dereference (both real, verifiable).
    lookup_hits = [
        h for h in search_repo(DEMO_REPO, "get_user(request.user_id)")
        if h.path == "app/routes/payments.py"
    ]
    deref_hits = [
        h for h in search_repo(DEMO_REPO, "payment.user_id = user.id")
        if h.path == "app/routes/payments.py"
    ]
    assert lookup_hits, "expected get_user lookup line in payments.py"
    assert deref_hits, "expected user.id dereference line in payments.py"

    code_lookup = lookup_hits[0]
    code_deref = deref_hits[0]

    # --- 10. Repo paths are verified (asserted before they enter the report) --
    assert code_lookup.path_verified is True
    assert code_deref.path_verified is True
    assert code_lookup.path == "app/routes/payments.py"
    assert code_deref.path == "app/routes/payments.py"
    repo_paths_verified = all(h.path_verified for h in (code_lookup, code_deref))
    assert repo_paths_verified is True

    # --- 6. Build a minimal, grounded report dict ----------------------------
    report = _build_minimal_report(
        failing_test=failing_test,
        primary_error=primary_error,
        fail_line=fail_line,
        error_line=error_line,
        code_lookup={
            "snippet": code_lookup.matched_text,
            "line_start": code_lookup.line_start,
            "line_end": code_lookup.line_end,
        },
        code_deref={
            "snippet": code_deref.matched_text,
            "line_start": code_deref.line_start,
            "line_end": code_deref.line_end,
        },
        repo_paths_verified=repo_paths_verified,
    )

    # --- 7. Render Markdown and JSON report strings ---------------------------
    markdown = build_markdown_report(report)
    json_text = render_json(report)
    assert isinstance(markdown, str) and markdown.strip()
    assert isinstance(json_text, str) and json_text.strip()

    # Citations are present in the rendered Markdown.
    assert failing_test in markdown
    assert "AttributeError" in markdown
    assert PAYMENTS_REL in markdown

    # --- 8. No raw secret patterns survive into either output ----------------
    # A fake github token was embedded in one evidence snippet above; it must be
    # redacted out of both rendered forms.
    for raw in RAW_FAKE_SECRETS:
        assert raw not in markdown, f"raw secret leaked into markdown: {raw}"
        assert raw not in json_text, f"raw secret leaked into json: {raw}"
    assert "ghp_fakeTokenForDemoOnly1234567890" not in markdown
    assert REDACTION_MARKER in markdown, "redaction marker should appear after scrubbing"
    assert REDACTION_MARKER in json_text
    assert not contains_secret(markdown)
    assert not contains_secret(json_text)

    # --- 9. Evidence carries real, positive line numbers ---------------------
    redacted = build_report_dict(report)
    evidence = redacted["evidence"]
    assert len(evidence) == 4
    for item in evidence:
        assert isinstance(item["line_start"], int) and item["line_start"] >= 1
        assert isinstance(item["line_end"], int) and item["line_end"] >= item["line_start"]

    # The code evidence line numbers match what repo_search actually returned.
    code_items = {e["id"]: e for e in evidence}
    assert code_items["ev_code_lookup"]["line_start"] == code_lookup.line_start
    assert code_items["ev_code_deref"]["line_start"] == code_deref.line_start
    # CI evidence line numbers match what ci_log_reader actually returned.
    assert code_items["ev_ci_error"]["line_start"] == error_line
    assert code_items["ev_ci_test"]["line_start"] == fail_line


def test_phase3_redacts_secrets_from_ci_log(tmp_path: Path) -> None:
    """A secret-bearing CI log is redacted while evidence is still extracted.

    The demo broken_api_route log is intentionally clean, so to prove the
    load->redact step end-to-end we build a small log (in ``tmp_path``, never
    touching demo fixtures) that carries both a real failure and fake secrets.
    """
    log_text = "\n".join(
        [
            "tests/test_payments.py::test_create_payment_success FAILED",
            "[INFO] using token ghp_fakeTokenForDemoOnly1234567890",
            "[INFO] DATABASE_URL=postgres://user:password@example.com:5432/payments",
            "E   AttributeError: 'NoneType' object has no attribute 'id'",
        ]
    )
    log_file = tmp_path / "ci.log"
    log_file.write_text(log_text, encoding="utf-8")

    result = read_ci_log(tmp_path, "ci.log")

    # Failure evidence is still extracted from the redacted text.
    assert result.failing_test == "tests/test_payments.py::test_create_payment_success"
    assert result.primary_error is not None
    assert result.primary_error.error_type == "AttributeError"

    # Secrets are gone; redaction was applied and counted.
    joined = "\n".join(ln.text for ln in result.lines)
    assert "ghp_fakeTokenForDemoOnly1234567890" not in joined
    assert "postgres://user:password@example.com:5432/payments" not in joined
    assert REDACTION_MARKER in joined
    assert result.redactions_applied >= 2
    assert not contains_secret(joined)
