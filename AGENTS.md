# Repository Guidelines

## Project Structure & Module Organization

IncidentPilot is a FastAPI backend skeleton for incident investigation. Source code lives in `app/`: routes in `app/api/`, schemas in `app/schemas/`, deterministic tools in `app/tools/`, workflow code in `app/services/`, and local storage in `app/storage/`. Demo fixtures live in `demo/`, tests in `tests/`, docs in `docs/`, and shareable Graphify artifacts in `graphify-out/`.

## MVP Scope Rules

Keep the MVP focused on local incident intake, CI log reading, secret redaction, repo search, path safety, and structured placeholder reports. Do not add autonomous production changes, a frontend, vector DB, Slack/PagerDuty integrations, Kubernetes, Celery, Redis, or GitHub write actions unless explicitly requested. Future GitHub writes must stay behind dry-run, safety review, and human approval.

## Graphify Usage

Use Graphify before broad architecture work, cross-file debugging, or refactors. Inspect:

- `graphify-out/GRAPH_REPORT.md`
- `graphify-out/graph.html`
- `graphify-out/graph.json`

Prefer targeted queries such as `graphify query "which modules handle redaction?"` before reading many files. Root Graphify report artifacts are useful and safe to commit; local cache, analysis, manifest, and cost files should stay ignored.

## Build, Test, and Development Commands

- `make setup`: create `.venv` and install dependencies.
- `make dev`: run `uvicorn app.main:app --reload`.
- `make test`: run the pytest suite.
- `.venv/bin/pytest tests/test_redactor.py`: run one focused test file.
- `make clean`: remove Python and pytest caches plus generated reports.

## Coding Style & Naming Conventions

Use Python 3.11+, four-space indentation, and type hints for public helpers. Use `snake_case` for functions and modules, `PascalCase` for Pydantic models and exception classes, and uppercase constants such as `REDACTION`. Keep FastAPI routes thin; put business flow in services, deterministic evidence work in tools, and API contracts in schemas.

## Testing Guidelines

Tests use `pytest` and FastAPI’s `TestClient`. Put tests in `tests/` with `test_*.py` filenames. Add or update tests when changing redaction, repo search, path guards, incident API contracts, report structure, or safety behavior. Demo repo tests under `demo/demo_repo/` are fixtures and are not part of the main pytest suite.

## Security & Configuration Rules

Never commit `.env`, real API keys, tokens, local databases, caches, or generated cost files. Keep `.env.example` as the only committed environment template. Redact secrets before displaying logs or sending text to an LLM. Do not invent file paths, line numbers, functions, or evidence in incident reports.

## Commit & Pull Request Guidelines

Use short, descriptive commit messages that summarize the completed change. Pull requests should include the scope, relevant issue links, test results such as `make test`, and notes about safety-sensitive behavior or generated Graphify artifact changes.
