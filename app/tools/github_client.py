"""Minimal GitHub REST client (Phase 9) — issue creation only.

Scope is deliberately tiny and safe:

* The *only* write this client can perform is creating an issue
  (``POST /repos/{owner}/{repo}/issues``). It never opens pull requests, never
  creates refs, never commits, and never mutates repository contents.
* The auth token is held privately and is never logged, never returned, never
  placed in a ``repr``/``str``, and is scrubbed out of any error text before it
  is raised. The token only ever leaves this process inside the ``Authorization``
  request header.
* All failures are surfaced as a single controlled :class:`GitHubClientError`
  with a sanitized message, so callers never have to handle raw ``httpx`` errors
  and a secret can never ride out on an exception.

This module performs the network write itself; the gating (report exists, safety
review passes, human approval on file, dry-run resolution) lives in
``app.services.github_issue_service`` and is always applied *before* this client
is constructed or called. No agent/LLM code imports this client.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.tools.redactor import redact_secrets

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class CreatedIssue:
    """The minimal, non-sensitive result of a successful issue creation."""

    number: int
    url: str


class GitHubClientError(Exception):
    """A controlled GitHub client failure with a sanitized, token-free message."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Tiny GitHub REST client that can only create issues.

    The ``transport`` argument exists so tests can inject an
    :class:`httpx.MockTransport` and exercise the client with zero network
    access; production callers leave it ``None``.
    """

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        *,
        base_url: str = GITHUB_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # The token is private and intentionally never exposed via an attribute
        # that a logger or ``repr`` would pick up.
        self._token = token or ""
        self.owner = owner or ""
        self.repo = repo or ""
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def __repr__(self) -> str:  # never include the token
        return f"GitHubClient(owner={self.owner!r}, repo={self.repo!r})"

    __str__ = __repr__

    # -- public API ----------------------------------------------------------

    def create_issue(
        self, title: str, body: str, labels: list[str] | None = None
    ) -> CreatedIssue:
        """Create a GitHub issue and return its number + html URL.

        Raises :class:`GitHubClientError` (sanitized, token-free) if the client
        is not fully configured, GitHub is unreachable, GitHub returns a non-201
        status, or the response shape is unexpected.
        """
        if not (self._token and self.owner and self.repo):
            raise GitHubClientError(
                "GitHub client is not fully configured; token, owner, and repo "
                "are all required to create an issue."
            )

        url = f"{self._base_url}/repos/{self.owner}/{self.repo}/issues"
        payload: dict[str, object] = {"title": title, "body": body}
        if labels:
            payload["labels"] = list(labels)

        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            # Drop the original exception (``from None``) so nothing it carries
            # can surface a secret; the sanitized type/message is enough to act
            # on without leaking the token.
            raise GitHubClientError(
                f"Failed to reach GitHub: {type(exc).__name__}: "
                f"{self._sanitize(str(exc))}"
            ) from None

        if response.status_code != 201:
            raise GitHubClientError(
                self._sanitize(
                    f"GitHub API error {response.status_code}: "
                    f"{self._extract_message(response)}"
                ),
                status_code=response.status_code,
            )

        data = self._safe_json(response)
        number = data.get("number")
        html_url = data.get("html_url")
        if number is None or not html_url:
            raise GitHubClientError(
                "GitHub returned an unexpected response shape for issue creation.",
                status_code=response.status_code,
            )
        return CreatedIssue(number=int(number), url=str(html_url))

    # -- internals -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "IncidentPilot",
        }

    def _sanitize(self, text: str) -> str:
        """Scrub the token (defense in depth) and redact any other secrets."""
        if not isinstance(text, str):
            text = str(text)
        if self._token:
            text = text.replace(self._token, "***")
        return redact_secrets(text)

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _extract_message(self, response: httpx.Response) -> str:
        data = self._safe_json(response)
        message = data.get("message")
        if isinstance(message, str) and message:
            return message
        # Fall back to a short, bounded slice of the raw body so a huge HTML
        # error page cannot dominate the exception text.
        return (response.text or "no response body")[:200]
