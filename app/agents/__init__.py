"""Optional, grounded agent layer for IncidentPilot.

This package adds an *optional* sequential agent pipeline on top of the
deterministic investigation service. The deterministic service remains the
source of truth; agents only restate its grounded, already-redacted findings and
can never invent file paths, line numbers, evidence, root causes, or confidence.
Agent mode is opt-in via :func:`run_agent_investigation`; default app behavior
stays deterministic. No external write (GitHub issue/PR/branch/commit) is enabled
by this layer.

See ``app/agents/orchestrator.py`` for the flow and fallback rules.
"""

from __future__ import annotations

from app.agents.base import AGENT_SEQUENCE, INSUFFICIENT_EVIDENCE, AgentOutcome, EvidenceIndex
from app.agents.model_client import GroundedDeterministicClient, ModelClient
from app.agents.orchestrator import (
    AgentInvestigationResult,
    InvestigationMode,
    run_agent_investigation,
)

__all__ = [
    "AGENT_SEQUENCE",
    "INSUFFICIENT_EVIDENCE",
    "AgentOutcome",
    "EvidenceIndex",
    "GroundedDeterministicClient",
    "ModelClient",
    "AgentInvestigationResult",
    "InvestigationMode",
    "run_agent_investigation",
]
