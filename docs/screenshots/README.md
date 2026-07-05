# Screenshots

This folder holds screenshots used by the README, the demo script, and the hackathon
submission. The committed PNGs below were captured from the local demo flow and live API
responses. Refresh them whenever endpoint output, safety behavior, or eval results change.

Keep the seven shots below with the same filenames so README/demo links remain stable.

## How to refresh the screens

```bash
make dev      # serve the API at http://127.0.0.1:8000 (Swagger UI at /docs)
# in another terminal, run the curl commands from docs/demo_script.md
make eval     # regenerates evals/results/evaluation_results.md
```

## Committed shots

1. **`01-swagger-endpoints.png` — Swagger endpoint list.**
   Open `http://127.0.0.1:8000/docs`. Show all routes: `/health`,
   `/incidents/trigger`, `/incidents/{id}/investigate`, `/incidents/{id}/report`,
   `/incidents/{id}/approve`, `/incidents/{id}/github/issue`.

2. **`02-trigger-response.png` — Incident trigger response.**
   `POST /incidents/trigger` with `{"scenario":"broken_api_route"}`. Capture the
   `{"incident_id":"inc_001","status":"created","scenario":"broken_api_route"}` response.

3. **`03-investigation-report.png` — Investigation report.**
   `GET /incidents/inc_001/report` (after investigate). Show severity `SEV2`,
   confidence `0.90`, the grounded evidence list, and the root cause at
   `app/routes/payments.py:81`.

4. **`04-redacted-secret.png` — Redacted secret example.**
   Investigate `secret_in_logs` (`inc_002`) and show the report/log containing
   `[REDACTED_SECRET:type=github_token]`, `:type=bearer_token`, `:type=database_url`,
   `:type=api_key` — with **no raw secret values** visible.

5. **`05-approval-gate.png` — Approval gate response.**
   `POST /incidents/inc_001/github/issue` *before* approval, showing
   **HTTP 403** with `"reason":"approval_required"`.

6. **`06-github-issue-dry-run.png` — GitHub issue dry-run.**
   After approving, `POST /incidents/inc_001/github/issue` showing the dry-run preview:
   `"created": false`, `"dry_run": true`, the generated title, and `issue_url: null`.
   (If you later enable real creation against a throwaway test repo, a screenshot of the
   created issue may replace this — label it `06-github-issue-created.png`.)

7. **`07-evaluation-results.png` — Evaluation results table.**
   The rendered `evals/results/evaluation_results.md` (or the terminal summary from
   `make eval`) showing `5 cases run, 4 passed, 0 failed, 1 expected safe failure passed`
   and the per-check ✓ table.

## Conventions

- PNG, readable at full width; crop to the relevant panel.
- Never capture a screen that contains a real token. Use the demo fixtures only, which
  carry fake, already-redactable values.
