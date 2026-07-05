# Sample IncidentPilot Report

This is a realistic IncidentPilot report produced by the **real deterministic pipeline**
from the `broken_api_route` demo fixture (`demo/incidents/broken_api_route/`). It is the
rendered form of the report the API returns from `POST /incidents/inc_001/investigate` and
persists to `app/storage/reports/inc_001.{json,md}`.

This is a representative sample based on the deterministic `broken_api_route` demo.
Running `make demo` generates the current report under `app/storage/reports/`.

```bash
make demo            # or: python scripts/run_demo.py --scenario broken_api_route
```

A second, contrasting example — the **secret-redaction / blocked** case — is included at
the end so you can see how the same pipeline handles a dangerous input.

---

# Incident Report — `inc_001`

**Title:** POST /payments failing: AttributeError in app/routes/payments.py

## Incident summary

`tests/test_payments.py::test_create_payment_success` fails in CI with
`AttributeError: 'NoneType' object has no attribute 'id'`. The failure is grounded at
`app/routes/payments.py:81` in `create_payment` (category: `null_dereference`). The
`POST /payments` endpoint returns HTTP 500.

| Field | Value |
| --- | --- |
| Incident ID | `inc_001` |
| Severity | **SEV2** |
| Affected service | `payments-api` |
| Status | `awaiting_human_approval` |
| Primary error | `AttributeError: 'NoneType' object has no attribute 'id'` |
| Confidence | `0.90` |
| Needs human review | `false` |
| Trigger | `github_actions_failure` |

## Evidence (file / log line references)

Every item below was returned by a deterministic tool. CI-log items cite a real line in
the redacted log; `repo_file` items were re-resolved under the repo root and the cited
line was read back from disk.

| ID | Source | Location | What it shows |
| --- | --- | --- | --- |
| `ev_ci_test` | CI log | `demo/incidents/broken_api_route/ci.log:8` | `tests/test_payments.py::test_create_payment_success FAILED` |
| `ev_ci_error` | CI log | `demo/incidents/broken_api_route/ci.log:26` | `E   AttributeError: 'NoneType' object has no attribute 'id'` |
| `ev_code_1` | repo file | `demo/demo_repo/tests/test_payments.py:11` | `from fastapi.testclient import TestClient` |
| `ev_code_2` | repo file | `demo/demo_repo/app/routes/payments.py:80` | `user = get_user(request.user_id)   # may return None for unknown ids` |
| `ev_code_3` | repo file | `demo/demo_repo/app/routes/payments.py:81` | `payment.user_id = user.id          # BUG: user can be None here` |
| `ev_api` | api_response | `demo/incidents/broken_api_route/api_response.json` | `POST /payments returned HTTP 500` |

## Root-cause hypothesis

**Category:** `null_dereference`

`create_payment` at `app/routes/payments.py:81` dereferences a value that can be `None`,
raising `AttributeError: 'NoneType' object has no attribute 'id'`. The lookup on line 80
(`get_user(request.user_id)`) can return `None` for an unknown id, and line 81 uses
`user.id` without a guard.

**Alternative considered:** an upstream lookup returning no record — which still requires
the same `None` guard at this call site.

## Fix plan

Guard the value before dereferencing it and return an explicit error instead of crashing.

1. At `app/routes/payments.py:81`, check the looked-up value for `None` before using it.
2. Only dereference once the value is confirmed non-`None`; otherwise return an explicit
   error (e.g. HTTP 404) at the call site near `app/routes/payments.py:80`.

- **Patch strategy:** `patch` (local guard clause at the failing call site)
- **Rollback plan:** revert the guard in `demo/demo_repo/app/routes/payments.py`
- **Risk:** low — the change is a local guard clause, not a behavioral rewrite.

## Regression test plan

- `tests/test_payments.py::test_create_payment_success` (the existing failing test).
- Add a test asserting the missing/`None` user case returns a handled error (e.g. 404),
  not a 500.

## Safety review

Secret scan passed: no credential-like values detected. Report is safe to display. GitHub
issue is eligible **after human approval**.

| Safety field | Value |
| --- | --- |
| Approved for display | `true` |
| Approved for GitHub issue | `true` (eligible — still requires human approval to file) |
| Approved for PR | `false` (never allowed) |
| Risk level | `low` |
| Secret scan passed | `true` |
| Secrets detected | `false` |
| Redactions applied | `0` |
| Human approval required | `true` |
| Required human action | Review the grounded report, then approve GitHub issue creation. |

## Approval status

`pending` until a human approves. With no approval on file,
`POST /incidents/inc_001/github/issue` returns **HTTP 403 `approval_required`**. After
`POST /incidents/inc_001/approve`, the same call returns a redacted **dry-run preview**
(`created: false`, `dry_run: true`) — a real GitHub write requires
`GITHUB_DRY_RUN=false` plus full GitHub configuration against a throwaway test repo.

## Limitations / human-review notes

- This report is high-confidence (0.90) and grounded, so `needs_human_review` is `false`,
  but the issue still cannot be filed without an explicit human approval — by design.
- Evidence is limited to the provided CI log, API fixture, and the local
  `demo/demo_repo` snapshot. The fix plan is a recommendation for a human to apply;
  IncidentPilot does not edit code or open a PR.

---

# Contrasting example — `inc_002` (secret-bearing source, blocked)

Produced from `demo/incidents/secret_in_logs/` via
`python scripts/run_demo.py --scenario secret_in_logs`. This shows redaction and the
safety block in action.

- **Title:** Sensitive values exposed in CI log for payments-api
- **Severity:** SEV2 · **Confidence:** `0.40` · **Needs human review:** `true`
- **Primary error:** `ConnectionError: billing upstream timeout`
- **Root cause category:** `secret_exposure` — 4 credential-like values were exposed in
  the CI log (now redacted) and must be treated as compromised and rotated.

**Redacted evidence** (the raw secret values never appear — only their type and line):

| ID | Location | Redacted content |
| --- | --- | --- |
| `ev_secret_1` | `…/secret_in_logs/ci.log:3` | `Using GitHub token [REDACTED_SECRET:type=github_token]` |
| `ev_secret_2` | `…/secret_in_logs/ci.log:4` | `Auth header: [REDACTED_SECRET:type=bearer_token]` |
| `ev_secret_3` | `…/secret_in_logs/ci.log:5` | `[REDACTED_SECRET:type=database_url]` |
| `ev_secret_4` | `…/secret_in_logs/ci.log:6` | `Calling billing service with [REDACTED_SECRET:type=api_key]` |

**Safety review:** risk level `high`, `secrets_detected: true`, `redactions_applied: 4`,
`approved_for_github_issue: false`. Blocked reasons include "Repository paths were not
verified", "Confidence below threshold", and "Report is derived from a source that
contained secrets; external sharing requires human review". The GitHub-issue endpoint
returns **HTTP 403 `safety_review_failed`** both before *and* after approval — an approval
cannot override an unsafe report.

See [security.md](security.md) for the full safety model and
[demo_script.md](demo_script.md) for the runnable walkthrough.
