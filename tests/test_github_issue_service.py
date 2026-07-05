"""Phase 9: GitHub issue creation — service + REST client (strict).

Covers the Phase 9 contract end-to-end with no network access:

* Dry-run (the default) returns a preview (created=False, dry_run=True, title,
  body_preview) and never touches the network.
* Real creation is blocked without human approval, blocked when the safety
  review disallows it, and blocked (falls back to dry-run) when required env
  values are missing.
* The GitHub token never appears in a response, a repr/log, an exception, or the
  body preview.
* The issue body contains every required section.
* No PR / branch / commit capability exists in the new implementation.
* The REST client is exercised via httpx.MockTransport (success + failure),
  proving create_issue() works without any real network call.

These tests are hermetic: the only network surface is the GitHub client, and it
is always driven by an injected fake or an httpx.MockTransport.
"""

from __future__ import annotations

import json
import pathlib
import socket

import httpx
import pytest

from app.services import github_issue_service as gh_svc
from app.services import investigation_service as inv
from app.services.approval_service import approval_service
from app.services.errors import (
    ApprovalRejected,
    ApprovalRequired,
    IncidentNotFound,
    ReportNotReady,
    SafetyBlocked,
)
from app.services.github_issue_service import (
    REQUIRED_SECTIONS,
    GitHubIssueError,
    GitHubIssueOutcome,
    GitHubSettings,
    build_issue_body,
    build_issue_title,
    create_github_issue,
    github_settings_from_env,
    resolve_dry_run,
)
from app.storage import incident_store
from app.tools.github_client import CreatedIssue, GitHubClient, GitHubClientError

# A unique, obviously-fake token. It must never show up in any output.
SENTINEL_TOKEN = "ghp_SENTINELtoken_DO_NOT_LEAK_0001"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def store_reset():
    incident_store.reset_store()
    yield
    incident_store.reset_store()


# --- test doubles -----------------------------------------------------------


class _ExplodingClient:
    """A GitHub client stand-in that fails loudly if any write is attempted.

    Injected into dry-run / blocked paths to prove no network write happens.
    """

    def __init__(self) -> None:
        self.calls = 0

    def create_issue(self, *args, **kwargs):  # pragma: no cover - asserts on misuse
        self.calls += 1
        raise AssertionError(
            "create_issue must not be called in a dry-run or gate-blocked path"
        )


class _RecordingClient:
    """A fake client that records the call and returns a successful issue."""

    def __init__(self, number: int = 123) -> None:
        self.calls: list[dict] = []
        self._number = number

    def create_issue(self, title, body, labels=None) -> CreatedIssue:
        self.calls.append({"title": title, "body": body, "labels": labels})
        return CreatedIssue(
            number=self._number,
            url=f"https://github.com/o/r/issues/{self._number}",
        )


def _dry_config() -> GitHubSettings:
    return GitHubSettings(token=SENTINEL_TOKEN, owner="o", repo="r", dry_run=True)


def _real_config() -> GitHubSettings:
    return GitHubSettings(token=SENTINEL_TOKEN, owner="o", repo="r", dry_run=False)


def _approve(incident_id: str) -> None:
    approval_service.approve_action(
        incident_id, "create_github_issue", approved_by="tester"
    )


# ===========================================================================
# Dry-run resolution: missing / invalid / true all stay dry-run
# ===========================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("banana", True),  # invalid -> dry-run
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        (True, True),
        (False, False),
    ],
)
def test_resolve_dry_run(raw, expected) -> None:
    assert resolve_dry_run(raw) is expected


def test_settings_from_env_missing_is_dry_run() -> None:
    cfg = github_settings_from_env(env={})
    assert cfg.dry_run is True
    assert cfg.configured is False


def test_settings_from_env_full_and_disabled_is_real() -> None:
    cfg = github_settings_from_env(
        env={
            "GITHUB_TOKEN": SENTINEL_TOKEN,
            "GITHUB_OWNER": "o",
            "GITHUB_REPO": "r",
            "GITHUB_DRY_RUN": "false",
        }
    )
    assert cfg.dry_run is False
    assert cfg.configured is True


def test_settings_from_env_invalid_dry_run_is_dry_run() -> None:
    cfg = github_settings_from_env(
        env={
            "GITHUB_TOKEN": SENTINEL_TOKEN,
            "GITHUB_OWNER": "o",
            "GITHUB_REPO": "r",
            "GITHUB_DRY_RUN": "not-a-bool",
        }
    )
    # Invalid value must never enable a real write.
    assert cfg.dry_run is True


# ===========================================================================
# Dry-run: preview shape + no network
# ===========================================================================


def test_dry_run_returns_preview(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    _approve("inc_001")

    exploding = _ExplodingClient()
    out = create_github_issue("inc_001", config=_dry_config(), client=exploding)

    assert isinstance(out, GitHubIssueOutcome)
    assert out.created is False
    assert out.dry_run is True
    assert out.url is None
    assert out.number is None
    assert out.title.startswith("IncidentPilot:")
    assert out.body_preview  # non-empty preview
    # The injected client was never called -> no network write in dry-run.
    assert exploding.calls == 0


def test_dry_run_does_not_touch_the_network(store_reset, monkeypatch) -> None:
    """Even with a real (uninjected) client path, dry-run opens no socket."""

    def _no_network(*args, **kwargs):  # pragma: no cover - only on regression
        raise AssertionError("network access attempted during a dry-run preview")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    inv.investigate_incident("broken_api_route", persist=False)
    _approve("inc_001")

    # No client injected: dry-run must still avoid constructing/calling one.
    out = create_github_issue("inc_001", config=_dry_config())
    assert out.created is False
    assert out.dry_run is True


# ===========================================================================
# Real creation is gate-blocked
# ===========================================================================


def test_real_creation_blocked_without_approval(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)  # not approved
    exploding = _ExplodingClient()
    with pytest.raises(ApprovalRequired):
        create_github_issue("inc_001", config=_real_config(), client=exploding)
    assert exploding.calls == 0


def test_real_creation_blocked_after_rejection(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    approval_service.reject_action(
        "inc_001", "create_github_issue", approved_by="tester"
    )
    exploding = _ExplodingClient()
    with pytest.raises(ApprovalRejected):
        create_github_issue("inc_001", config=_real_config(), client=exploding)
    assert exploding.calls == 0


def test_real_creation_blocked_by_failed_safety_review(store_reset) -> None:
    """secret_in_logs is safety-blocked even when a human approved it."""
    inv.investigate_incident("secret_in_logs", persist=False)
    _approve("inc_002")
    exploding = _ExplodingClient()
    with pytest.raises(SafetyBlocked):
        create_github_issue("inc_002", config=_real_config(), client=exploding)
    assert exploding.calls == 0


def test_real_creation_blocked_by_low_confidence(store_reset) -> None:
    inv.investigate_incident("ambiguous_error", persist=False)
    _approve("inc_003")
    exploding = _ExplodingClient()
    with pytest.raises(SafetyBlocked):
        create_github_issue("inc_003", config=_real_config(), client=exploding)
    assert exploding.calls == 0


def test_real_creation_blocked_when_env_missing(store_reset) -> None:
    """dry_run=False but no token/owner/repo -> falls back to dry-run, no write."""
    inv.investigate_incident("broken_api_route", persist=False)
    _approve("inc_001")

    cfg = GitHubSettings(token="", owner="", repo="", dry_run=False)
    exploding = _ExplodingClient()
    out = create_github_issue("inc_001", config=cfg, client=exploding)

    assert out.created is False
    assert out.dry_run is True  # blocked from a real write
    assert exploding.calls == 0
    assert "not fully configured" in out.message.lower()


def test_unknown_incident_raises(store_reset) -> None:
    with pytest.raises(IncidentNotFound):
        create_github_issue("inc_404", config=_dry_config())


def test_report_not_ready_raises(store_reset) -> None:
    inv.create_incident("broken_api_route")  # registered, not investigated
    with pytest.raises(ReportNotReady):
        create_github_issue("inc_001", config=_dry_config())


# ===========================================================================
# Real creation success path (no network: injected client)
# ===========================================================================


def test_real_creation_success_returns_url_and_number(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    _approve("inc_001")

    recording = _RecordingClient(number=4242)
    out = create_github_issue("inc_001", config=_real_config(), client=recording)

    assert out.created is True
    assert out.dry_run is False
    assert out.number == 4242
    assert out.url.endswith("/issues/4242")
    assert len(recording.calls) == 1
    # The body actually filed carries the grounded report content.
    assert recording.calls[0]["title"].startswith("IncidentPilot:")
    assert "incident" in recording.calls[0]["labels"]


def test_real_creation_failure_raises_controlled_error(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    _approve("inc_001")

    class _FailingClient:
        def create_issue(self, title, body, labels=None):
            raise GitHubClientError("GitHub API error 401: Bad credentials")

    with pytest.raises(GitHubIssueError) as excinfo:
        create_github_issue("inc_001", config=_real_config(), client=_FailingClient())

    # Controlled error, mapped to a 5xx, with no token leaked into the message.
    assert excinfo.value.status_code == 502
    assert excinfo.value.reason == "github_write_failed"
    assert SENTINEL_TOKEN not in str(excinfo.value)


# ===========================================================================
# Token never leaks
# ===========================================================================


def test_token_never_appears_in_outcome(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    _approve("inc_001")

    out = create_github_issue("inc_001", config=_dry_config(), client=_ExplodingClient())
    blob = json.dumps(out.model_dump())
    assert SENTINEL_TOKEN not in blob
    assert SENTINEL_TOKEN not in repr(out)
    assert SENTINEL_TOKEN not in out.body_preview
    assert SENTINEL_TOKEN not in out.title


def test_token_never_appears_in_client_repr() -> None:
    client = GitHubClient(SENTINEL_TOKEN, "o", "r")
    assert SENTINEL_TOKEN not in repr(client)
    assert SENTINEL_TOKEN not in str(client)


def test_token_scrubbed_even_when_github_echoes_it(store_reset) -> None:
    """If GitHub's error body echoed the token, the client must scrub it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": f"leaked {SENTINEL_TOKEN}"})

    client = GitHubClient(
        SENTINEL_TOKEN, "o", "r", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GitHubClientError) as excinfo:
        client.create_issue("t", "b")
    assert SENTINEL_TOKEN not in str(excinfo.value)
    assert "422" in str(excinfo.value)


# ===========================================================================
# Body contains every required section + grounded content
# ===========================================================================


def test_body_includes_all_required_sections(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    report = incident_store.get_report("inc_001")
    body = build_issue_body(report)

    for heading in REQUIRED_SECTIONS:
        assert f"## {heading}" in body, f"missing required section: {heading}"

    # Grounded content is present.
    assert "app/routes/payments.py" in body
    assert "AttributeError" in body


def test_title_is_grounded_and_prefixed(store_reset) -> None:
    inv.investigate_incident("broken_api_route", persist=False)
    report = incident_store.get_report("inc_001")
    title = build_issue_title(report)
    assert title == "IncidentPilot: POST /payments fails due to unchecked missing user"


def test_body_never_leaks_secret_for_secret_scenario(store_reset) -> None:
    """Body builder is safe even for a secret-bearing report (defense in depth)."""
    inv.investigate_incident("secret_in_logs", persist=False)
    report = incident_store.get_report("inc_002")
    body = build_issue_body(report)
    for secret in (
        "ghp_fakeTokenForDemoOnly1234567890",
        "postgres://user:password@example.com:5432/payments",
        "fake-api-key-12345",
    ):
        assert secret not in body
    assert "[REDACTED_SECRET" in body  # redaction marker survives


# ===========================================================================
# GitHub REST client via httpx.MockTransport (no real network)
# ===========================================================================


def test_client_create_issue_success() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            201, json={"number": 7, "html_url": "https://github.com/o/r/issues/7"}
        )

    client = GitHubClient(
        SENTINEL_TOKEN, "o", "r", transport=httpx.MockTransport(handler)
    )
    created = client.create_issue("My title", "My body", labels=["incident"])

    assert isinstance(created, CreatedIssue)
    assert created.number == 7
    assert created.url == "https://github.com/o/r/issues/7"
    # The token only ever travels in the Authorization header.
    assert captured["auth"] == f"Bearer {SENTINEL_TOKEN}"
    assert captured["path"] == "/repos/o/r/issues"
    assert captured["payload"]["title"] == "My title"
    assert captured["payload"]["labels"] == ["incident"]


def test_client_create_issue_http_error_is_controlled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Resource not accessible"})

    client = GitHubClient(
        SENTINEL_TOKEN, "o", "r", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GitHubClientError) as excinfo:
        client.create_issue("t", "b")
    assert excinfo.value.status_code == 403
    assert "403" in str(excinfo.value)
    assert SENTINEL_TOKEN not in str(excinfo.value)


def test_client_unconfigured_raises_without_network() -> None:
    # Missing token -> the client refuses before any request is built.
    client = GitHubClient("", "o", "r")
    with pytest.raises(GitHubClientError):
        client.create_issue("t", "b")


# ===========================================================================
# Scope guard: no PR / branch / commit capability in the new code
# ===========================================================================


def test_no_pr_branch_or_commit_capability() -> None:
    """The new implementation may only create issues — nothing else.

    Scans for write-capability markers (API endpoint paths and method names) so
    a PR/branch/commit write cannot be added without this test failing. Prose in
    docstrings is unaffected because every marker is slash- or ``def``-prefixed.
    """
    sources = {
        "github_client": (REPO_ROOT / "app/tools/github_client.py").read_text(),
        "github_issue_service": (
            REPO_ROOT / "app/services/github_issue_service.py"
        ).read_text(),
    }
    forbidden = (
        "/pulls",
        "/git/",
        "/branches",
        "/merges",
        "/commits",
        "/contents/",
        "def create_pr",
        "def create_pull",
        "def create_branch",
        "def create_commit",
        "def merge",
    )
    for name, text in sources.items():
        for marker in forbidden:
            assert marker not in text, f"{name} must not reference {marker!r}"

    # The client's only write endpoint is the issues endpoint.
    assert "/issues" in sources["github_client"]
