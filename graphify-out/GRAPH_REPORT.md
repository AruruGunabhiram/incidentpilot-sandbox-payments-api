# Graph Report - IncidentPilot  (2026-06-28)

## Corpus Check
- 76 files · ~42,848 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 925 nodes · 2133 edges · 51 communities (45 shown, 6 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 142 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c9d5dfd9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]

## God Nodes (most connected - your core abstractions)
1. `IncidentReport` - 94 edges
2. `EvidenceIndex` - 32 edges
3. `run_agent_investigation()` - 30 edges
4. `_investigate()` - 28 edges
5. `redact_secrets()` - 28 edges
6. `create_github_issue()` - 26 edges
7. `resolve_safe_path()` - 26 edges
8. `AgentOutcome` - 25 edges
9. `IncidentNotFound` - 24 edges
10. `read_ci_log()` - 24 edges

## Surprising Connections (you probably didn't know these)
- `test_render_markdown_has_sections()` --calls--> `render_markdown()`  [INFERRED]
  tests/test_report_writer.py → evals/run_evals.py
- `CodeContextObjectShapeClient` --uses--> `EvidenceIndex`  [INFERRED]
  tests/test_agents.py → app/agents/base.py
- `DocumentedShapeClient` --uses--> `EvidenceIndex`  [INFERRED]
  tests/test_agents.py → app/agents/base.py
- `InvalidJSONClient` --uses--> `EvidenceIndex`  [INFERRED]
  tests/test_agents.py → app/agents/base.py
- `InventedPathClient` --uses--> `EvidenceIndex`  [INFERRED]
  tests/test_agents.py → app/agents/base.py

## Import Cycles
- None detected.

## Communities (51 total, 6 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.11
Nodes (29): CodeFinding, FixPlan, LogFinding, Evidence and agent finding schemas.  Every agent output carries grounded ``Evide, Base for any agent output that must be safety-reviewable., Output of the Log Investigator agent., Output of the Code Context agent., A single grounded root-cause hypothesis. (+21 more)

### Community 1 - "Community 1"
Cohesion: 0.15
Nodes (19): build_summary(), main(), Return the ``(json_path, markdown_path)`` for ``incident_id``., Return a clean, redacted console summary of the investigation., Run the demo investigation. Return ``0`` on success, non-zero on failure.      O, _report_paths(), run_demo(), _env_without_secrets_or_network() (+11 more)

### Community 2 - "Community 2"
Cohesion: 0.28
Nodes (10): create_github_issue(), GitHub issue route — approval- and safety-gated, dry-run by default., Preview or create the GitHub issue for an approved, grounded report., get_settings(), Settings, settings_dependency(), BaseSettings, GitHubIssueResult (+2 more)

### Community 3 - "Community 3"
Cohesion: 0.14
Nodes (25): test_empty_log_needs_human_review(), test_extracts_assertion_error(), test_extracts_failing_pytest_test(), test_extracts_primary_attribute_error(), test_extracts_stack_trace_block(), test_loads_fixture_and_numbers_lines(), test_missing_log_file_raises(), test_no_failed_line_returns_none() (+17 more)

### Community 4 - "Community 4"
Cohesion: 0.19
Nodes (11): create_payment(), get_user(), Payment, PaymentRequest, Demo payments router for IncidentPilot's demo_repo fixture.  Simulates a tiny Fa, Minimal user record returned by the fake data layer., Return a ``User`` for a known id, or ``None`` when no such user exists.      NOT, A payment being created. ``user_id`` is filled in from the looked-up user. (+3 more)

### Community 5 - "Community 5"
Cohesion: 0.10
Nodes (30): A secret-bearing CI log is redacted while evidence is still extracted.      The, test_phase3_redacts_secrets_from_ci_log(), test_clean_text_returns_same_text_and_zero_redactions(), test_findings_do_not_leak_full_secret(), test_redaction_is_idempotent(), test_redacts_api_key_key_value(), test_redacts_bearer_token(), test_redacts_database_url() (+22 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (57): Path, The same guard protects the real demo repo root used by the pipeline., An allowed root directory with one nested file., root(), test_allows_nested_valid_file(), test_allows_normal_file_inside_root(), test_blocks_absolute_path_outside_root(), test_blocks_deep_parent_traversal() (+49 more)

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (44): _build_minimal_report(), Phase 3 deterministic toolchain integration check.  Proves the Phase 3 tools wor, End-to-end deterministic run over the broken_api_route fixtures., Assemble a minimal, fully grounded report dict for the serializer.      Every ev, test_phase3_toolchain_end_to_end(), test_ensure_report_safe_redacts_text(), test_markdown_includes_summary_evidence_confidence(), test_missing_optional_fields_do_not_crash() (+36 more)

### Community 16 - "Community 16"
Cohesion: 0.15
Nodes (32): EvidenceItem, A single grounded piece of evidence returned by a deterministic tool., _api_evidence(), _build_code_finding(), _build_fix_plan(), _build_log_finding(), _build_root_cause(), _build_safety_review() (+24 more)

### Community 17 - "Community 17"
Cohesion: 0.11
Nodes (12): client(), Phase 8: human approval gate before GitHub issue creation.  Covers the nine non-, POST /approve returns approved AND persists the decision for that action.      C, test_approve_create_github_issue_action(), test_dry_run_returns_preview_only(), test_failed_safety_review_blocks_even_after_approval(), test_invalid_action_rejected_by_api(), test_low_confidence_blocks_even_after_approval() (+4 more)

### Community 18 - "Community 18"
Cohesion: 0.10
Nodes (19): Tests for the deterministic investigation service (Phase 5).  These exercise :fu, The scenario name alone is enough; it is auto-registered., Secrets are redacted everywhere, yet the report is still persisted., An ungrounded failure must escalate, not fabricate a confident diagnosis., If the repo cannot be read, no code evidence may be invented., The on-disk JSON re-validates straight through the IncidentReport schema., The Markdown report is written and never leaks a raw secret., A crafted scenario id cannot escape demo/incidents/. (+11 more)

### Community 19 - "Community 19"
Cohesion: 0.08
Nodes (33): _assign_incident_id(), _coerce_report(), ensure_storage_dirs(), get_approval(), get_incident(), list_reports(), load_report_json(), In-memory incident store for the IncidentPilot control plane.  Phase 4 keeps all (+25 more)

### Community 20 - "Community 20"
Cohesion: 0.16
Nodes (22): create_github_issue(), Preview (dry-run) or create (real) the GitHub issue for an incident.      Enforc, _approve(), _dry_config(), _ExplodingClient, Even with a real (uninjected) client path, dry-run opens no socket., secret_in_logs is safety-blocked even when a human approved it., dry_run=False but no token/owner/repo -> falls back to dry-run, no write. (+14 more)

### Community 21 - "Community 21"
Cohesion: 0.26
Nodes (8): Phase 2 demo fixture sanity tests.  These verify the demo incident fixtures exis, _read(), test_ambiguous_error_report_is_low_confidence(), test_broken_api_route_ci_log_has_attribute_error(), test_broken_api_route_report_mentions_root_cause_and_file(), test_json_files_parse(), test_secret_in_logs_ci_log_has_fake_secrets(), test_secret_in_logs_report_has_no_raw_secrets()

### Community 22 - "Community 22"
Cohesion: 0.12
Nodes (19): ApprovalAction, ApprovalStatus, ApprovalRecord, A stored, auditable approval decision for one incident + action.      ``status``, Keep ``status`` and the legacy ``approved`` boolean consistent.          ``appro, ApprovalService, Human approval gate (Phase 8).  Single, service-layer source of truth for whethe, Record a ``rejected`` decision for ``(incident_id, action)``. (+11 more)

### Community 23 - "Community 23"
Cohesion: 0.33
Nodes (5): Tests for the demo payments service.  These are demo_repo FIXTURE tests, not par, Happy path: a known user can create a payment (HTTP 201)., Unknown user reproduces the production incident.      ``get_user`` returns ``Non, test_create_payment_succeeds_for_known_user(), test_create_payment_with_unknown_user_reproduces_incident()

### Community 26 - "Community 26"
Cohesion: 0.24
Nodes (9): _as_confidence(), _build_candidate(), _quality_gate(), _raised(), Sequential agent orchestrator with deterministic fallback.  This is the optional, Assemble a candidate report by overlaying validated agent narrative.      Starts, True if this agent step asks for (more) human review., Coerce an agent-provided confidence into [0, 1], else keep ``default``. (+1 more)

### Community 27 - "Community 27"
Cohesion: 0.12
Nodes (26): Run the optional agent pipeline over a deterministic investigation.      The det, run_agent_investigation(), _deterministic(), Tests for the optional agent layer (Phase 6).  The agent layer wraps the determi, No agent-created evidence: every evidence id traces to deterministic mode., Importing/using the agent layer does not alter deterministic output., Every prompt-documented shape is accepted; broken_api_route parity holds., Every test starts and ends from a clean in-memory control plane. (+18 more)

### Community 28 - "Community 28"
Cohesion: 0.10
Nodes (17): LogInvestigatorAgent, Thin wrapper around the deterministic log finding., GroundedDeterministicClient, The model boundary for the agent layer.  Every agent calls a :class:`ModelClient, Default, offline model client: grounded by construction.      Returns ``payload[, CodeContextObjectShapeClient, DocumentedShapeClient, InvalidJSONClient (+9 more)

### Community 29 - "Community 29"
Cohesion: 0.11
Nodes (22): AgentOutcome, load_prompt(), parse_agent_json(), Result of a single agent step — thin, inspectable, and not yet trusted.      ``s, Return the text of ``prompts/{name}.md``.      Raises ``FileNotFoundError`` if t, Strictly parse an agent's raw output into a JSON object.      Returns ``None`` f, Code Context agent: restate the deterministic, verified code finding.  Runs afte, FixPlannerAgent (+14 more)

### Community 30 - "Community 30"
Cohesion: 0.11
Nodes (25): approve_incident(), investigate_incident(), Incident control-plane routes: trigger, investigate, approve.  Handlers stay thi, Load the scenario's intake fixture and register a new incident., Run the deterministic investigation and return the grounded report., Record a human approval decision for an action (empty body = approve).      The, trigger_incident(), BaseModel (+17 more)

### Community 31 - "Community 31"
Cohesion: 0.15
Nodes (20): build_issue(), build_issue_body(), build_issue_title(), _bullet_list(), _evidence_block(), _evidence_line(), _fix_plan_block(), _is_payments_missing_user_report() (+12 more)

### Community 32 - "Community 32"
Cohesion: 0.17
Nodes (9): EvidenceIndex, _norm_path(), Shared, dependency-light helpers for the optional agent layer.  The agent layer, True if every id is one the tools actually produced (empty is fine)., True if ``path`` names a file the tools actually verified.          Tolerates a, The closed set of facts an agent is allowed to cite.      Built only from the de, Optional, grounded agent layer for IncidentPilot.  This package adds an *optiona, AgentInvestigationResult (+1 more)

### Community 33 - "Community 33"
Cohesion: 0.07
Nodes (56): The deterministic safety gate's structured pass/fail invariants.      Every fiel, SafetyChecks, assert_github_issue_allowed(), assert_report_safe_for_issue(), checks_blocked_reasons(), _default_summary(), evaluate_safety(), github_issue_block_reasons() (+48 more)

### Community 34 - "Community 34"
Cohesion: 0.10
Nodes (21): client(), _investigated(), Phase 4 control-plane API audit (strict).  Exercises the FastAPI control plane e, Investigating must not create or require any on-disk incident state., The whole trigger->investigate->approve->issue flow is deterministic., Fresh client over a reset, in-memory store (no cross-test leakage)., Trigger + investigate a scenario; return (incident_id, report json)., test_approve_stores_approval_and_unlocks_issue() (+13 more)

### Community 35 - "Community 35"
Cohesion: 0.20
Nodes (17): TestClient, client(), Phase 4 control-plane API tests.  Exercise the deterministic FastAPI routes end-, test_all_endpoints_in_openapi(), test_ambiguous_scenario_is_low_confidence_and_blocked(), test_github_issue_dry_run_preview_after_approval(), test_github_issue_requires_approval(), test_github_issue_requires_report() (+9 more)

### Community 36 - "Community 36"
Cohesion: 0.12
Nodes (17): GitHubIssueOptions, Body for ``POST /incidents/{id}/github/issue``.      The title and body are gene, Calling the service directly (bypassing the route) still blocks., Safety is checked before approval: an approved unsafe report still blocks., The approved dry-run path must never open an outbound network connection.      T, test_no_github_network_write_even_when_approved(), test_reject_then_reapprove_unlocks(), test_service_layer_blocks_after_rejection() (+9 more)

### Community 37 - "Community 37"
Cohesion: 0.29
Nodes (4): CodeContextAgent, Thin wrapper around the deterministic, path-verified code finding., Return the agent's output as a JSON string.          ``agent`` is the agent name, Any

### Community 38 - "Community 38"
Cohesion: 0.14
Nodes (14): Response, If GitHub's error body echoed the token, the client must scrub it., test_client_create_issue_http_error_is_controlled(), test_client_create_issue_success(), test_client_unconfigured_raises_without_network(), test_token_never_appears_in_client_repr(), test_token_scrubbed_even_when_github_echoes_it(), CreatedIssue (+6 more)

### Community 39 - "Community 39"
Cohesion: 0.50
Nodes (4): investigate_incident(), _persist_report(), Run the deterministic investigation for an incident or demo scenario.      ``sce, Write the redacted report to disk as JSON + Markdown; return both paths.      De

### Community 40 - "Community 40"
Cohesion: 0.33
Nodes (5): handle_incident_error(), IncidentPilot FastAPI application (Phase 4 control plane).  Registers the determ, Map control-plane domain errors to their HTTP status + detail.      Includes the, JSONResponse, Request

### Community 41 - "Community 41"
Cohesion: 0.14
Nodes (22): Report retrieval route., Return the stored, grounded incident report (after /investigate)., read_incident_report(), ApprovalRequired, IncidentError, IncidentNotFound, Domain errors for the incident control plane.  Services raise these plain except, Base class for control-plane errors. Carries an HTTP status + detail.      ``rea (+14 more)

### Community 42 - "Community 42"
Cohesion: 0.12
Nodes (3): eval_run(), Lock the Phase 10 evaluation runner's behavior.  These tests pin the *contract*, Run the full suite once (no file write) and share the result.

### Community 43 - "Community 43"
Cohesion: 0.18
Nodes (10): Tests for the Phase 5 durable, JSON-first report storage.  Every test targets an, Return a minimal, schema-valid IncidentReport dict for tests., test_invalid_incident_id_cannot_escape(), test_list_reports_is_sorted_and_filtered(), test_no_raw_secret_in_saved_json(), test_overwrite_is_explicit(), test_save_and_load_report_json_round_trips(), test_save_report_json_accepts_model_and_dict() (+2 more)

### Community 44 - "Community 44"
Cohesion: 0.18
Nodes (13): github_settings_from_env(), Resolve ``GITHUB_DRY_RUN`` safely: only an explicit false token disables it., Build :class:`GitHubSettings` from the environment (raw, robust).      Reads the, resolve_dry_run(), Phase 9: GitHub issue creation — service + REST client (strict).  Covers the Pha, Body builder is safe even for a secret-bearing report (defense in depth)., The new implementation may only create issues — nothing else.      Scans for wri, test_body_never_leaks_secret_for_secret_scenario() (+5 more)

### Community 45 - "Community 45"
Cohesion: 0.20
Nodes (9): BaseTransport, GitHubIssueError, GitHubIssueOutcome, A real GitHub issue write was attempted and failed (controlled, no secret)., Result of an issue-creation attempt (preview in dry-run, created in real)., A fake client that records the call and returns a successful issue., _RecordingClient, GitHubClientError (+1 more)

### Community 46 - "Community 46"
Cohesion: 0.13
Nodes (18): datetime, IncidentIntake, Incident intake schemas., Normalized representation of an incoming incident to investigate., _grounded_frames(), _load_intake(), _no_evidence_report(), Resolve an incident id or demo scenario name to an ``IncidentState``.      Deter (+10 more)

### Community 47 - "Community 47"
Cohesion: 0.29
Nodes (7): Exception, InvalidIncidentIdError, Base class for durable report-storage errors., Raised when an ``incident_id`` is unsafe to use as a filename., Return ``incident_id`` unchanged if it is a safe single path segment.      Treat, StorageError, _validate_incident_id()

### Community 48 - "Community 48"
Cohesion: 0.12
Nodes (28): build_summary(), CaseResult, CheckResult, _compute_checks(), EvalRun, extract_secret_literals(), _hard_fail(), _intake_repo_root() (+20 more)

### Community 49 - "Community 49"
Cohesion: 0.40
Nodes (4): _dry_run_message(), GitHubSettings, Resolved GitHub configuration for one issue-creation attempt., True only when token, owner, and repo are all present.

### Community 50 - "Community 50"
Cohesion: 0.50
Nodes (4): Invalid env dry-run values must not 500 or enable a real write., Route can return a real-created issue shape without real GitHub access., test_github_issue_route_real_create_uses_mocked_client(), test_invalid_github_dry_run_env_fails_safe_to_preview()

## Knowledge Gaps
- **6 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `IncidentReport` connect `Community 29` to `Community 0`, `Community 1`, `Community 16`, `Community 18`, `Community 19`, `Community 26`, `Community 27`, `Community 28`, `Community 30`, `Community 31`, `Community 32`, `Community 33`, `Community 37`, `Community 39`, `Community 41`, `Community 43`, `Community 45`, `Community 46`, `Community 47`, `Community 48`, `Community 49`?**
  _High betweenness centrality (0.247) - this node is a cross-community bridge._
- **Why does `redact_secrets()` connect `Community 16` to `Community 1`, `Community 33`, `Community 3`, `Community 5`, `Community 38`, `Community 7`, `Community 46`, `Community 19`, `Community 26`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **Why does `read_ci_log()` connect `Community 3` to `Community 16`, `Community 5`, `Community 6`, `Community 7`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 30 inferred relationships involving `IncidentReport` (e.g. with `AgentOutcome` and `EvidenceIndex`) actually correct?**
  _`IncidentReport` has 30 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `EvidenceIndex` (e.g. with `IncidentReport` and `CodeContextAgent`) actually correct?**
  _`EvidenceIndex` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `IncidentPilot backend package.`, `Optional, grounded agent layer for IncidentPilot.  This package adds an *optiona`, `Shared, dependency-light helpers for the optional agent layer.  The agent layer` to the rest of the system?**
  _281 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.11174242424242424 - nodes in this community are weakly interconnected._