# IncidentPilot — 3-Minute Demo Script

A timed walkthrough for a live or recorded demo. Total runtime ~3:00. Spoken lines are in
plain text; commands to run are in code blocks. Every output shown below was produced by
the real, deterministic pipeline — nothing here is mocked.

## Before you start (off camera)

```bash
make setup          # one-time: create .venv and install dependencies
make demo           # warms the deterministic pipeline and writes inc_001 report
```

Have two things open: a terminal, and the Swagger UI at
`http://127.0.0.1:8000/docs` (start the server with `make dev`). Committed example
screenshots live in `docs/screenshots/`; refresh them from this script if the demo output
changes.

---

## 0:00 — Problem (25s)

> "When a backend service fails in CI, an on-call engineer burns the first 20 minutes
> doing the same manual triage every time: read the log, find the failing test, trace the
> error to a line of code, rule out leaked secrets, and write it all up. IncidentPilot
> automates that first pass — but safely. It produces a grounded incident report where
> every claim is tied to real evidence, it redacts secrets before anyone sees them, and it
> never files anything or touches production without a human's explicit approval. It's an
> incident *investigator*, not an autonomous fixer."

## 0:25 — Architecture (25s)

> "It's a FastAPI control plane over a set of deterministic tools: a secret redactor, a
> path guard, a CI-log reader, and a repo search. An investigation service stitches those
> into one structured `IncidentReport`. Two gates protect every external action — an
> authoritative safety gate, then a human approval gate — and GitHub issue creation is
> dry-run by default. There's also an optional sequential multi-agent layer that only
> restates the grounded findings; it can never invent a path or loosen a safety verdict."

Show the architecture diagram in `docs/architecture.md` and the Swagger endpoint list.

## 0:50 — Trigger an incident (25s)

> "Let's trigger the flagship incident: a failing `POST /payments` route."

```bash
curl -X POST http://127.0.0.1:8000/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{"scenario":"broken_api_route"}'
```

Expected:

```json
{"incident_id": "inc_001", "status": "created", "scenario": "broken_api_route"}
```

> "The scenario's intake fixture is loaded and registered as `inc_001`."

## 1:15 — Investigation report (40s)

```bash
curl -X POST http://127.0.0.1:8000/incidents/inc_001/investigate
curl http://127.0.0.1:8000/incidents/inc_001/report
```

> "The investigation reads and redacts the CI log, finds the failing test, then grounds
> the error to a real file and line — it actually re-reads `app/routes/payments.py` to
> confirm the code exists before citing it."

Point at these fields in the response (all real output):

- `severity: "SEV2"`, `confidence: 0.9`, `needs_human_review: false`
- `primary_error: "AttributeError: 'NoneType' object has no attribute 'id'"`
- root cause grounded at `app/routes/payments.py:81` in `create_payment` —
  `payment.user_id = user.id   # BUG: user can be None here`
- a fix plan and a regression test plan referencing
  `tests/test_payments.py::test_create_payment_success`

> "Every evidence item names a verified file and line number. Nothing here is invented."

## 1:55 — Safety / redaction case (30s)

> "Now the dangerous input: a CI log with leaked credentials."

```bash
curl -X POST http://127.0.0.1:8000/incidents/trigger \
  -H "Content-Type: application/json" -d '{"scenario":"secret_in_logs"}'
curl -X POST http://127.0.0.1:8000/incidents/inc_002/investigate | \
  grep -o 'REDACTED_SECRET:type=[a-z_]*' | sort -u
```

Expected — the four secrets are caught and typed, never shown:

```
REDACTED_SECRET:type=api_key
REDACTED_SECRET:type=bearer_token
REDACTED_SECRET:type=database_url
REDACTED_SECRET:type=github_token
```

> "A GitHub token, a bearer JWT, a `DATABASE_URL`, and an api_key — all redacted before
> the report is built. The safety review marks this `high` risk, `secrets_detected: true`,
> and blocks it from any external action. A secret-bearing source is never auto-eligible
> for a GitHub issue, even with approval."

## 2:25 — Approval-gated GitHub issue (25s)

> "Back to the clean incident. Watch the human-in-the-loop gate. First, try to file the
> issue *before* approving."

```bash
curl -i -X POST http://127.0.0.1:8000/incidents/inc_001/github/issue \
  -H "Content-Type: application/json" -d '{"dry_run":true}'
# -> HTTP 403  {"detail":"...","reason":"approval_required"}

curl -X POST http://127.0.0.1:8000/incidents/inc_001/approve \
  -H "Content-Type: application/json" \
  -d '{"action":"create_github_issue","approved":true,"approved_by":"demo-operator"}'

curl -X POST http://127.0.0.1:8000/incidents/inc_001/github/issue \
  -H "Content-Type: application/json" -d '{"dry_run":true}'
```

> "Blocked with `approval_required` until a human approves. After approval it returns a
> redacted dry-run preview — `created: false`, `dry_run: true`. No GitHub write happens in
> the demo; a real issue needs a deliberately configured throwaway repo."

```json
{"created": false, "dry_run": true,
 "title": "IncidentPilot: POST /payments fails due to unchecked missing user",
 "issue_url": null, "issue_number": null}
```

## 2:50 — Evaluation results (10s)

```bash
make eval
```

> "And it's measured. The eval suite drives the real routes for five cases and checks path
> verification, line evidence, confidence, secret-leak, and the safety/approval policy."

```
5 cases run, 4 passed, 0 failed, 1 expected safe failure passed
```

> "Four clean passes plus one *expected safe failure* — the case where the system
> correctly refuses to diagnose an un-groundable incident instead of hallucinating. That
> refusal is the whole point. Thanks."

---

## One-shot fallback (no server)

If the live server is unavailable, the deterministic demo runs end-to-end offline and
prints the same grounded summary:

```bash
make demo                                   # broken_api_route (inc_001)
python scripts/run_demo.py --scenario secret_in_logs
```

Real `make demo` output:

```
IncidentPilot demo — deterministic incident report
====================================================
  incident_id          : inc_001
  scenario             : broken_api_route
  severity             : SEV2
  primary_error        : AttributeError: 'NoneType' object has no attribute 'id'
  root cause summary   : create_payment at app/routes/payments.py:81 dereferences a value that can be None, raising AttributeError: 'NoneType' object has no attribute 'id'.
  confidence           : 0.90
  needs_human_review   : False
```
