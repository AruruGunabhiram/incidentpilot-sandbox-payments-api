"""Shared, dependency-light helpers for the optional agent layer.

The agent layer is a *thin* reasoning shell around the deterministic
investigation service. It never reads logs or files itself: it only restates
findings the deterministic tools already produced and verified. This module
holds the small pieces every agent needs — prompt loading, strict JSON parsing,
the set of facts an agent is allowed to cite, and the per-agent result type —
so each agent file stays minimal.

Nothing here imports an LLM or an agent framework. The layer is fully importable
and testable offline; a real model is injected at the boundary (see
``model_client``) and is always re-validated and grounded by the orchestrator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.schemas.report import IncidentReport

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# The JSON sentinel an agent must emit when it has no grounded evidence to act
# on. Mirrors the project rule: missing evidence -> say so, never fabricate.
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

# Every prompt must contain these rules verbatim. Tests assert their presence in
# each prompt file, and the agents inherit the same contract, so a real model
# swapped in at the boundary is bound by identical rules.
REQUIRED_PROMPT_RULES: tuple[str, ...] = (
    "Only cite evidence provided by tools.",
    "If evidence is missing, say insufficient evidence.",
    "Do not claim a root cause without file/log support.",
    "Return valid JSON only.",
)

# Ordered names of the agents in the required sequential flow.
AGENT_SEQUENCE: tuple[str, ...] = (
    "triage",
    "log_investigator",
    "code_context",
    "fix_planner",
    "safety_reviewer",
)

AgentStatus = Literal["ok", "insufficient_evidence", "invalid"]


def load_prompt(name: str) -> str:
    """Return the text of ``prompts/{name}.md``.

    Raises ``FileNotFoundError`` if the prompt is missing, surfacing a
    misconfigured agent loudly instead of silently running prompt-less.
    """
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def parse_agent_json(text: str) -> dict[str, Any] | None:
    """Strictly parse an agent's raw output into a JSON object.

    Returns ``None`` for anything that is not a JSON object (invalid JSON, a
    bare list, a string, ``None``). The orchestrator treats ``None`` as an
    invalid agent step and falls back to deterministic mode, honoring the rule
    "if agent output is invalid JSON, fall back to deterministic mode".
    """
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _norm_path(path: str) -> str:
    return path.strip().lstrip("./").rstrip("/")


@dataclass(frozen=True)
class EvidenceIndex:
    """The closed set of facts an agent is allowed to cite.

    Built only from the deterministic report's grounded evidence and verified
    code finding. An agent may reference an evidence id only if it is in
    :attr:`ids`, and may name a file only if it is in :attr:`paths`. Anything
    else is, by definition, invented — and is rejected.
    """

    ids: frozenset[str]
    paths: frozenset[str]

    @classmethod
    def from_report(cls, report: IncidentReport) -> "EvidenceIndex":
        ids = {item.id for item in report.evidence}
        paths: set[str] = set()
        for item in report.evidence:
            if item.path:
                paths.add(_norm_path(item.path))
            if item.source:
                paths.add(_norm_path(item.source))
        # The deterministic code finding lists already-verified repo files; allow
        # the bare frame path it uses in addition to the (possibly repo-prefixed)
        # evidence paths.
        if report.code_finding is not None:
            for matched in report.code_finding.matched_files:
                paths.add(_norm_path(matched))
        return cls(ids=frozenset(ids), paths=frozenset(paths))

    def evidence_ids_grounded(self, ids: list[str] | None) -> bool:
        """True if every id is one the tools actually produced (empty is fine)."""
        if not ids:
            return True
        return all(isinstance(i, str) and i in self.ids for i in ids)

    def path_grounded(self, path: str) -> bool:
        """True if ``path`` names a file the tools actually verified.

        Tolerates a repo-prefix mismatch (e.g. the bare frame path
        ``app/routes/payments.py`` vs the evidence's repo-prefixed
        ``demo/demo_repo/app/routes/payments.py``) but requires a path-segment
        boundary, so an unrelated invented path can never sneak through.
        """
        if not isinstance(path, str) or not path:
            return False
        candidate = _norm_path(path)
        if candidate in self.paths:
            return True
        for known in self.paths:
            if known.endswith("/" + candidate) or candidate.endswith("/" + known):
                return True
        return False

    def paths_grounded(self, paths: list[str] | None) -> bool:
        if not paths:
            return True
        return all(self.path_grounded(p) for p in paths)


@dataclass
class AgentOutcome:
    """Result of a single agent step — thin, inspectable, and not yet trusted.

    ``status`` is ``"ok"`` (validated and grounded), ``"insufficient_evidence"``
    (the agent correctly declined for lack of evidence; not a failure, but forces
    human review), or ``"invalid"`` (unparseable, schema-invalid, or ungrounded —
    triggers deterministic fallback).
    """

    name: str
    status: AgentStatus
    data: dict[str, Any] = field(default_factory=dict)
    needs_human_review: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def invalid(self) -> bool:
        return self.status == "invalid"
