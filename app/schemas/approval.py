"""Human approval and GitHub issue request/response schemas.

No external write action is performed without an explicit, approved decision.
GitHub issue handling defaults to a dry run; real creation requires safety,
approval, complete configuration, and ``GITHUB_DRY_RUN=false``. These models are
the API contracts for the ``/approve`` and ``/github/issue`` endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ApprovalAction = Literal["create_github_issue", "create_pr", "post_comment"]

# The set of actions a human may approve. Used by the service layer to reject an
# unknown action before any state is touched (defense in depth on top of the
# ``ApprovalAction`` Literal that FastAPI enforces at the API boundary).
APPROVAL_ACTIONS: frozenset[str] = frozenset(
    {"create_github_issue", "create_pr", "post_comment"}
)

# Lifecycle of one action's approval. Default is always ``pending``: an action is
# blocked until a human explicitly approves it, and an explicit ``rejected`` is a
# distinct, sticky decision (not the same as "not yet decided").
ApprovalStatus = Literal["pending", "approved", "rejected"]


class ApprovalRequest(BaseModel):
    """Explicit human approval gate for a sensitive action."""

    model_config = ConfigDict(extra="forbid", strict=True)

    incident_id: str
    action: ApprovalAction
    approved_by: str
    approved: bool = False
    note: str | None = None


class GitHubIssueRequest(BaseModel):
    """Payload for creating a GitHub issue, dry-run by default."""

    model_config = ConfigDict(extra="forbid", strict=True)

    incident_id: str
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    dry_run: bool = True


# ---------------------------------------------------------------------------
# Phase 4 control-plane contracts
# ---------------------------------------------------------------------------


class ApprovalDecision(BaseModel):
    """Body for ``POST /incidents/{id}/approve``.

    Every field has a default so the demo can approve with an empty body. The
    action is fixed to ``create_github_issue`` — the only sensitive action this
    phase supports.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    action: ApprovalAction = "create_github_issue"
    approved: bool = True
    approved_by: str = "demo-operator"
    note: str | None = None


class ApprovalRecord(BaseModel):
    """A stored, auditable approval decision for one incident + action.

    ``status`` is the rich lifecycle state (``pending`` / ``approved`` /
    ``rejected``); ``approved`` is kept as a legacy convenience boolean and is
    always reconciled to ``status == "approved"`` by the validator below, so the
    two can never disagree.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    incident_id: str
    action: ApprovalAction = "create_github_issue"
    status: ApprovalStatus = "pending"
    approved: bool = False
    approved_by: str = "demo-operator"
    note: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _reconcile_status(self) -> "ApprovalRecord":
        """Keep ``status`` and the legacy ``approved`` boolean consistent.

        ``approved`` is treated as authoritative for legacy callers that only set
        the boolean: ``approved=True`` implies ``status="approved"``; a non-True
        ``approved`` can never leave ``status`` claiming ``"approved"``. Explicit
        ``status="rejected"`` (with ``approved=False``) is preserved.
        """
        if self.approved and self.status != "approved":
            object.__setattr__(self, "status", "approved")
        elif not self.approved and self.status == "approved":
            object.__setattr__(self, "status", "pending")
        return self


class ApprovalResponse(BaseModel):
    """Response for ``POST /incidents/{id}/approve``."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    action: ApprovalAction
    status: ApprovalStatus
    approved: bool
    approved_by: str
    message: str


class GitHubIssueOptions(BaseModel):
    """Body for ``POST /incidents/{id}/github/issue``.

    The title and body are generated from the grounded, redacted report — never
    taken from the caller — so an issue can only ever contain verified content.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    dry_run: bool | None = None
    labels: list[str] = Field(default_factory=list)


class GitHubIssueResult(BaseModel):
    """Response for ``POST /incidents/{id}/github/issue``.

    ``body_preview`` is the redacted Markdown that is previewed in dry-run mode
    and submitted in real mode. Real creation only occurs after safety approval,
    human approval, complete GitHub configuration, and ``GITHUB_DRY_RUN=false``.
    """

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    created: bool
    dry_run: bool
    title: str
    body_preview: str
    labels: list[str] = Field(default_factory=list)
    issue_url: str | None = None
    issue_number: int | None = None
    message: str
