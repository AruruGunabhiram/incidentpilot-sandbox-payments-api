"""The model boundary for the agent layer.

Every agent calls a :class:`ModelClient` to turn its prompt + grounded payload
into a JSON string. Keeping this behind a tiny protocol is what makes the layer
*explainable instead of magical*:

* The default :class:`GroundedDeterministicClient` performs no network call and
  no LLM inference. It returns the agent's pre-grounded ``proposal`` — derived
  entirely from the deterministic, tool-backed findings — as JSON. Agent mode is
  therefore reproducible and offline by default, and exercises the orchestrator's
  validation/fallback machinery exactly as a real model would.
* A real model (e.g. Google ADK + Gemini) can be dropped in by implementing
  ``complete``. Whatever it returns is still parsed, schema-validated, and
  grounded against tool evidence by the orchestrator, so a model can never make
  the report less grounded, less testable, or less safe — at worst it triggers a
  deterministic fallback.

This module intentionally has no third-party dependency, so importing the agent
layer never requires an agent framework to be installed.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelClient(Protocol):
    """Boundary between orchestration and whatever produces an agent's JSON."""

    def complete(self, *, agent: str, prompt: str, payload: dict[str, Any]) -> str:
        """Return the agent's output as a JSON string.

        ``agent`` is the agent name, ``prompt`` its loaded instructions, and
        ``payload`` the grounded, already-redacted inputs (notably
        ``payload["proposal"]``). Implementations must return a string; the
        orchestrator re-validates and grounds everything it returns.
        """
        ...


class GroundedDeterministicClient:
    """Default, offline model client: grounded by construction.

    Returns ``payload["proposal"]`` serialized as JSON. The "reasoning" is the
    deterministic projection of tool-backed findings, so the output is always
    grounded and reproducible. This is the honest default for a safety-critical
    incident tool: agent mode adds structure and review gates, not invention.
    """

    def complete(self, *, agent: str, prompt: str, payload: dict[str, Any]) -> str:
        proposal = payload.get("proposal", {})
        return json.dumps(proposal)
