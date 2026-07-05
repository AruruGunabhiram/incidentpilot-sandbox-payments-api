# IncidentPilot

**An approval-gated AI Incident Commander for backend failures.**

IncidentPilot takes a simulated backend incident — CI logs, an API-error fixture, a stack
trace, and a local repository snapshot — and produces a single grounded, human-reviewable
incident report. Every claim is tied to evidence a deterministic tool actually returned;
nothing is invented. Secrets are redacted before anything is displayed or stored, and no
GitHub issue is ever created without a clean safety review **and** explicit human
approval — with dry-run on by default.

Built for the Kaggle **Agents for Business** hackathon track.

> Positioning: IncidentPilot is an incident **investigation assistant**, not an autonomous
> production fixer. It never edits code, opens a PR, pushes a branch, or changes
> infrastructure.

## Problem

When a backend service fails in CI, the first 20 minutes of on-call are the same manual
triage every time: read the log, find the failing test, trace the error to a line of
code, rule out leaked secrets, and write it all up. Doing this by hand is slow and
error-prone, and naively handing raw logs to an LLM is dangerous — logs routinely contain
credentials, stack traces reference files that may not exist, and a confident-but-wrong
diagnosis can send people down the wrong path or leak a secret into a ticket.

IncidentPilot automates that first pass **safely**. It extracts only grounded evidence,
verifies every file and line it cites, redacts secrets before they are ever shown, scores
its own confidence, and refuses to take any external action unless the evidence is clean,
verified, confident, and explicitly approved by a human.

## Architecture

A thin FastAPI control plane over a set of deterministic tools, with two safety gates in
front of the single external action.

```
  Client ──▶ FastAPI control plane (trigger · investigate · report · approve · github/issue)
                     │
                     ▼
        investigation_service  ── deterministic, source of truth
          │   ├─ redactor        (scrub secrets)
          │   ├─ ci_log_reader   (path-guarded log read)
          │   ├─ path_guard      (no traversal, verified paths only)
          │   ├─ repo_search     (re-read & verify cited code lines)
          │   └─ report_writer   (redacted JSON + Markdown)
          ▼
        IncidentReport ──▶ incident_store (in-memory + redacted on disk)
                     │
        external action gates (service layer, safety first then approval):
          safety_gate  ──▶ approval_service ──▶ github_issue_service (dry-run by default)

  Optional, not on the HTTP path:
        agents/orchestrator: Triage ▸ Log Investigator ▸ Code Context ▸ Fix Planner ▸
                             Safety Reviewer ▸ Final Report Builder  (deterministic fallback)
```

Full detail, including Mermaid diagrams of the component map and the request sequence, is
in **[docs/architecture.md](docs/architecture.md)**.

## Tech stack

- **Python 3.11+**
- **FastAPI** + **Uvicorn** — control-plane API and ASGI server
- **Pydantic** + **pydantic-settings** — strict structured outputs and config
- **python-dotenv** — local environment loading
- **pytest** + **httpx** (FastAPI `TestClient`) — 307-test suite
- **PyYAML** — declarative evaluation cases
- **rich** — readable console output
- **Optional sequential multi-agent layer** (`app/agents/`) — a custom orchestrator whose
  default model client is offline and deterministic. No agent-framework or LLM package is
  required to run the project; a real model is a pluggable boundary (see
  [Future work](#future-work)).

Deterministic by design: the served API path uses no LLM, no network, and no database.

## Setup

Use Python 3.11 or newer.

```bash
# Option A: Makefile (creates .venv and installs dependencies)
make setup

# Option B: manual
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

After `make setup`, the other `make` targets (`dev`, `demo`, `eval`, `test`) use this
`.venv` automatically — there is no need to activate it yourself. They fall back to
your system Python only if `.venv` is absent.

Create your local environment file from the template (never commit `.env`):

```bash
cp .env.example .env
```

## Environment variables

All variables are optional for the demo — the default deterministic flow and dry-run mode
run with an empty `.env`. They live in `.env.example`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Free-form environment label. |
| `GITHUB_TOKEN` | _(empty)_ | GitHub token. **Required only** to enable real issue creation. |
| `GITHUB_OWNER` | _(empty)_ | Target repo owner for real issue creation. |
| `GITHUB_REPO` | _(empty)_ | Target repo name — use a **throwaway test repo only**. |
| `GITHUB_DRY_RUN` | `true` | Master safety switch. Anything except an explicit `false`/`0`/`no`/`off` stays dry-run. |
| `GEMINI_API_KEY` | _(empty)_ | Reserved for a future real agent model. **Not consumed** by the current request path. |

A real GitHub write requires **all** of: `GITHUB_DRY_RUN=false`, `GITHUB_TOKEN`,
`GITHUB_OWNER`, and `GITHUB_REPO` set — plus a report that passes safety and a recorded
human approval. Missing or invalid values fail safe to dry-run. Never put a real token in
a screenshot, log, or commit.

> Note: a `AGENT_MODE` setting exists in `app/config.py` (default `false`) as a
> placeholder for the optional agent layer. It is **not** read on the HTTP request path in
> this build and is intentionally omitted from `.env.example`.

## How to run

Start the API (auto-reload):

```bash
make dev
# or: uvicorn app.main:app --reload
```

`make dev` runs a long-lived server in the foreground; stop it with `Ctrl+C`. Once it is
up:

- Swagger UI: `http://127.0.0.1:8000/docs`
- Health: `GET http://127.0.0.1:8000/health` → `{"status":"ok","service":"incidentpilot"}`

Available endpoints:

| Method & path | Purpose |
| --- | --- |
| `GET /health` | Liveness check. |
| `POST /incidents/trigger` | Register a demo scenario as an incident. |
| `POST /incidents/{id}/investigate` | Run the grounded investigation, return the report. |
| `GET /incidents/{id}/report` | Fetch the stored report. |
| `POST /incidents/{id}/approve` | Record a human approval decision. |
| `POST /incidents/{id}/github/issue` | Preview (default) or, when fully unlocked, create the GitHub issue. |

Run the test suite:

```bash
make test        # or: pytest   → 307 passed
```

For a server-free, end-to-end run there is also `make demo` (below).

## How to trigger a demo incident

With the server running:

```bash
# 1. Trigger the flagship failing route (returns inc_001)
curl -X POST http://127.0.0.1:8000/incidents/trigger \
  -H "Content-Type: application/json" -d '{"scenario":"broken_api_route"}'

# 2. Investigate and read the grounded report
curl -X POST http://127.0.0.1:8000/incidents/inc_001/investigate
curl http://127.0.0.1:8000/incidents/inc_001/report

# 3. Try to file the issue before approval -> HTTP 403 approval_required
curl -i -X POST http://127.0.0.1:8000/incidents/inc_001/github/issue \
  -H "Content-Type: application/json" -d '{"dry_run":true}'

# 4. Approve, then file -> redacted dry-run preview (created:false, dry_run:true)
curl -X POST http://127.0.0.1:8000/incidents/inc_001/approve \
  -H "Content-Type: application/json" \
  -d '{"action":"create_github_issue","approved":true,"approved_by":"demo-operator"}'
curl -X POST http://127.0.0.1:8000/incidents/inc_001/github/issue \
  -H "Content-Type: application/json" -d '{"dry_run":true}'
```

Or run the whole investigation **offline, no server** (writes a redacted report to
`app/storage/reports/` and prints a summary):

```bash
make demo                                          # broken_api_route (inc_001)
python scripts/run_demo.py --scenario secret_in_logs
```

Available demo scenarios (`demo/incidents/`): `broken_api_route` (grounded null
dereference — the happy path), `secret_in_logs` (redaction + safety block),
`ambiguous_error` (low confidence, escalate), `wrong_repo_path` (references missing files —
must not hallucinate), `path_traversal`, and `low_confidence_report`.

A timed 3-minute walkthrough is in **[docs/demo_script.md](docs/demo_script.md)**, and a
rendered sample report in **[docs/sample_report.md](docs/sample_report.md)**. Demo
screenshots are committed in **[docs/screenshots](docs/screenshots/README.md)**.

## How to run evals

```bash
make eval        # or: python evals/run_evals.py
```

The suite drives the **real** app flow
(`trigger → investigate → github/issue → approve → github/issue`) for each case in
`evals/evaluation_cases.yaml`, with GitHub writes forced to dry-run and the GitHub env
scrubbed so no real issue can be created. It independently re-verifies every cited repo
path and asserts no secret leaks. Results are written to
`evals/results/evaluation_results.md`. Current status:

```
5 cases run, 4 passed, 0 failed, 1 expected safe failure passed
```

The single "expected safe failure" is the `wrong_repo_path` case, where the system
correctly **refuses** to diagnose an un-groundable incident instead of hallucinating a
file path. Pass `--no-write` to run without updating the results file.

## Safety design

IncidentPilot treats logs, stack traces, GitHub text, and repo content as untrusted input
and enforces safety by construction, not by trusting a model:

- **Secret redaction** before any display, report, or model context — layered across the
  log reader, the investigation service, the on-disk store, and the issue builder.
  Findings never carry more than a 4-character preview of a secret.
- **Path-traversal protection** and **verified paths only** — every file access goes
  through a path guard; a stack frame is cited only after the file is confirmed to exist
  and the line is re-read. Unverifiable references are recorded as missing, never faked.
- **Confidence thresholds** — an investigation bar (0.60) and a stricter issue-eligibility
  bar (0.75). Low confidence forces human review and blocks external action.
- **Authoritative safety gate** — six deterministic invariants plus a hard rule that a
  secret-bearing source is never auto-eligible for an external write, each with a stable
  blocked-reason string.
- **Human-in-the-loop approval gate** — recorded per `(incident, action)`, default
  `pending`, evaluated **after** safety so an approval can never override an unsafe report.
- **Dry-run by default** — a live GitHub write needs explicit configuration *and* both
  gates to pass; any misconfiguration fails safe to a redacted preview. The token never
  appears in any response or error.
- **No autonomous production changes** — `approved_for_pr` is hard-wired off; the only
  possible external write is a GitHub *issue*.

Full threat model and guarantees: **[docs/security.md](docs/security.md)**.

## Limitations

- **Regex-based redaction** covers common credential shapes but is not a guarantee against
  every possible secret format.
- **In-memory control plane** — incident state resets on restart; only redacted reports
  persist. No database, auth, TLS, or multi-user isolation (single-operator demo).
- **Fixture-driven inputs** — incidents come from local `demo/incidents/*` fixtures; there
  is no live CI/GitHub ingestion yet.
- **Live GitHub creation is minimally exercised** — the demo runs entirely in dry-run.
- **The agent layer is not wired to an endpoint** — it is implemented and unit-tested but
  invoked only programmatically, and its default model client performs no real inference.

## Future work

- Wire the optional agent layer (`run_agent_investigation`) behind an endpoint/flag and
  drop in a real model (e.g. a Gemini/ADK client) at the existing `ModelClient` boundary —
  re-validated and grounded by the same machinery, so it can only ever trigger a
  deterministic fallback, never loosen safety.
- Live ingestion from real CI/GitHub Actions instead of local fixtures.
- Real, opt-in GitHub issue creation against a configured test repository, kept behind the
  same dry-run + safety + approval gates.
- Durable, multi-incident storage and a lightweight review UI for the human approval step.
- Broader redaction (entropy-based detection) and richer root-cause categories.

## Repository layout

```
app/
  api/        FastAPI routers (health, incidents, reports, github)
  services/   investigation, safety_gate, approval, github_issue, errors
  tools/      redactor, path_guard, ci_log_reader, repo_search, report_writer, github_client
  schemas/    Pydantic models (incident, findings, report, safety, approval)
  storage/    in-memory + on-disk redacted report store
  agents/     optional sequential agent layer (offline by default)
demo/         incident fixtures + searched demo_repo snapshot
evals/        evaluation runner, cases, and results
docs/         architecture, security, demo script, sample report, screenshots
scripts/      run_demo.py (server-free end-to-end demo)
tests/        pytest suite (307 tests)
```

See **[AGENTS.md](AGENTS.md)** for contributor conventions and MVP scope rules.
