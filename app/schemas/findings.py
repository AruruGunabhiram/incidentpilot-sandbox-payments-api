"""Evidence and agent finding schemas.

Every agent output carries grounded ``EvidenceItem`` references plus the shared
review fields (confidence, human-review flag, blocked reasons) so the workflow
can enforce the project's safety rules. No business logic lives here.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Confidence is always a probability in [0, 1].
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

EvidenceSourceType = Literal[
    "ci_log",
    "api_response",
    "repo_file",
    "test_output",
    "github_issue",
    "unknown",
]


class EvidenceItem(BaseModel):
    """A single grounded piece of evidence returned by a deterministic tool."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    source: str
    source_type: EvidenceSourceType
    summary: str
    snippet: str | None = None
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ReviewedOutput(BaseModel):
    """Base for any agent output that must be safety-reviewable."""

    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str = ""
    confidence: Confidence = 0.0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    needs_human_review: bool = True
    blocked_reasons: list[str] = Field(default_factory=list)


class LogFinding(ReviewedOutput):
    """Output of the Log Investigator agent."""

    primary_error: str | None = None
    failing_test: str | None = None
    stack_trace_summary: str | None = None
    redactions_applied: int = Field(default=0, ge=0)


class CodeFinding(ReviewedOutput):
    """Output of the Code Context agent."""

    matched_files: list[str] = Field(default_factory=list)
    suspected_symbols: list[str] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)


class RootCauseHypothesis(ReviewedOutput):
    """A single grounded root-cause hypothesis."""

    category: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class FixPlan(ReviewedOutput):
    """Output of the Fix Planner agent."""

    patch_strategy: str
    steps: list[str] = Field(default_factory=list)
    regression_tests: list[str] = Field(default_factory=list)
    rollback_plan: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
