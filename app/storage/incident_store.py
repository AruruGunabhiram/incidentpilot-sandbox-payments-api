"""In-memory incident store for the IncidentPilot control plane.

Phase 4 keeps all state in process memory: no database, no disk writes, no
network. Each incident owns its normalized intake, the latest generated report,
and any human approvals, giving the thin FastAPI routes and the deterministic
services a single source of truth.

State is module-global and resets on process restart, which is all the demo and
the test suite require. ``reset_store`` exists so tests can start from a clean
slate.

Phase 5 adds an optional, durable, JSON-first report store at the bottom of this
module. It persists redacted reports to ``app/storage/reports/`` so a generated
report survives a process restart and can be inspected on disk. The in-memory
control plane above is unchanged; the two layers are independent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.approval import ApprovalRecord
from app.schemas.incident import IncidentIntake
from app.schemas.report import IncidentReport
from app.tools.path_guard import resolve_safe_path
from app.tools.redactor import redact_secrets
from app.tools.report_writer import render_json

# Stable ids for the known demo scenarios, so triggering ``broken_api_route``
# always yields ``inc_001`` for the demo regardless of trigger order. Unknown
# scenarios fall back to a sequential id that cannot collide with these.
KNOWN_SCENARIO_IDS: dict[str, str] = {
    "broken_api_route": "inc_001",
    "secret_in_logs": "inc_002",
    "ambiguous_error": "inc_003",
}


@dataclass
class IncidentState:
    """Everything the control plane tracks for a single incident."""

    incident_id: str
    scenario: str
    intake: IncidentIntake
    report: IncidentReport | None = None
    approvals: dict[str, ApprovalRecord] = field(default_factory=dict)


_INCIDENTS: dict[str, IncidentState] = {}
_SCENARIO_INDEX: dict[str, str] = {}
_UNKNOWN_COUNTER = {"next": 900}


def reset_store() -> None:
    """Clear all in-memory state (used by tests)."""
    _INCIDENTS.clear()
    _SCENARIO_INDEX.clear()
    _UNKNOWN_COUNTER["next"] = 900


def _assign_incident_id(scenario: str) -> str:
    """Return a stable id for ``scenario`` (known map first, else sequential)."""
    if scenario in KNOWN_SCENARIO_IDS:
        return KNOWN_SCENARIO_IDS[scenario]
    _UNKNOWN_COUNTER["next"] += 1
    return f"inc_{_UNKNOWN_COUNTER['next']}"


def register_incident(scenario: str, intake: IncidentIntake) -> IncidentState:
    """Create (or refresh) the incident for ``scenario`` and return its state.

    Triggering the same scenario twice is idempotent: it returns the existing
    incident (with refreshed intake) rather than creating a duplicate.
    """
    existing_id = _SCENARIO_INDEX.get(scenario)
    if existing_id is not None and existing_id in _INCIDENTS:
        state = _INCIDENTS[existing_id]
        state.intake = intake
        return state

    incident_id = _assign_incident_id(scenario)
    state = IncidentState(incident_id=incident_id, scenario=scenario, intake=intake)
    _INCIDENTS[incident_id] = state
    _SCENARIO_INDEX[scenario] = incident_id
    return state


def get_incident(incident_id: str) -> IncidentState | None:
    """Return the incident state for ``incident_id``, or ``None`` if unknown."""
    return _INCIDENTS.get(incident_id)


def save_report(incident_id: str, report: IncidentReport) -> None:
    """Attach the latest generated ``report`` to its incident."""
    state = _INCIDENTS.get(incident_id)
    if state is not None:
        state.report = report


def get_report(incident_id: str) -> IncidentReport | None:
    """Return the stored report for ``incident_id``, or ``None`` if not ready."""
    state = _INCIDENTS.get(incident_id)
    return state.report if state is not None else None


def record_approval(incident_id: str, record: ApprovalRecord) -> None:
    """Store a human approval decision, keyed by its action."""
    state = _INCIDENTS.get(incident_id)
    if state is not None:
        state.approvals[record.action] = record


def get_approval(incident_id: str, action: str) -> ApprovalRecord | None:
    """Return the stored approval for ``incident_id``/``action`` if present."""
    state = _INCIDENTS.get(incident_id)
    if state is None:
        return None
    return state.approvals.get(action)


# ---------------------------------------------------------------------------
# Phase 5: durable, JSON-first report storage
# ---------------------------------------------------------------------------
#
# The in-memory control plane above is the live source of truth during a
# request. Phase 5 adds a thin, deterministic persistence layer so a generated
# report survives a process restart and can be inspected on disk. It writes two
# files per incident under ``app/storage/reports/``:
#
#     app/storage/reports/{incident_id}.json
#     app/storage/reports/{incident_id}.md
#
# Design rules honored here:
#   * No database, no ORM, no async — just ``pathlib`` + ``json``.
#   * Reports are validated through the existing ``IncidentReport`` schema
#     before they are written, so malformed data never reaches disk.
#   * Every string is redacted (via the shared ``report_writer`` / ``redactor``)
#     before it is written, so a raw secret can never be persisted even if
#     upstream missed it.
#   * ``incident_id`` is treated as untrusted: it is validated to a strict slug
#     and the final path is confined under the reports root with ``path_guard``,
#     so a crafted id like ``../../etc/passwd`` cannot escape the directory.
#   * Overwriting is explicit. The project convention is "latest report wins"
#     (see the in-memory ``save_report`` above and ``IncidentReport.updated_at``),
#     so the default is ``overwrite=True``; pass ``overwrite=False`` to refuse to
#     clobber an existing file.
#
# Every public function takes an optional keyword-only ``reports_dir`` override
# so tests can target a tmp directory deterministically without monkeypatching.

# Default on-disk location: app/storage/reports/ (this module lives in
# app/storage/). Resolved once, deterministically, from this file's location.
REPORTS_DIR: Path = Path(__file__).resolve().parent / "reports"

# A safe incident id is a single path segment: it must start with an
# alphanumeric and may then contain only ``A-Z a-z 0-9 . _ -``. This excludes
# path separators, ``..`` traversal, leading dots, and the empty string.
_INCIDENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StorageError(Exception):
    """Base class for durable report-storage errors."""


class InvalidIncidentIdError(StorageError, ValueError):
    """Raised when an ``incident_id`` is unsafe to use as a filename."""


class ReportExistsError(StorageError):
    """Raised when a report exists and ``overwrite=False`` was requested."""


def _resolve_reports_dir(reports_dir: Path | str | None) -> Path:
    """Return the reports root to use (override for tests, else the default)."""
    return REPORTS_DIR if reports_dir is None else Path(reports_dir)


def _validate_incident_id(incident_id: str) -> str:
    """Return ``incident_id`` unchanged if it is a safe single path segment.

    Treats the id as untrusted input. Rejects empty strings, path separators,
    and ``..`` traversal with :class:`InvalidIncidentIdError` before any path is
    constructed.
    """
    if not isinstance(incident_id, str) or not incident_id:
        raise InvalidIncidentIdError("incident_id must be a non-empty string.")
    if "/" in incident_id or "\\" in incident_id or ".." in incident_id:
        raise InvalidIncidentIdError(
            f"incident_id may not contain path separators or '..': {incident_id!r}."
        )
    if not _INCIDENT_ID_RE.match(incident_id):
        raise InvalidIncidentIdError(
            f"incident_id must match {_INCIDENT_ID_RE.pattern!r}: {incident_id!r}."
        )
    return incident_id


def _report_path(
    incident_id: str, suffix: str, reports_dir: Path | str | None
) -> Path:
    """Resolve the on-disk path for an incident file, confined to the root.

    Validates the id, then runs the candidate path through ``path_guard`` so a
    crafted id can never resolve outside the reports directory. ``suffix`` is
    ``".json"`` or ``".md"``.
    """
    root = _resolve_reports_dir(reports_dir)
    _validate_incident_id(incident_id)
    candidate = root / f"{incident_id}{suffix}"
    # Defense in depth: even though the id is already a validated slug, confirm
    # the resolved path stays inside the reports root.
    return resolve_safe_path(root, candidate)


def _coerce_report(report: IncidentReport | dict) -> IncidentReport:
    """Validate ``report`` through the ``IncidentReport`` schema.

    Accepts an already-built model (returned as-is) or a plain dict (validated).
    Dicts are validated in JSON mode so ISO datetime strings are accepted while
    every other field stays strict. Raises ``pydantic.ValidationError`` for
    anything that is not a valid report, so malformed data is rejected before it
    can be written.
    """
    if isinstance(report, IncidentReport):
        return report
    if isinstance(report, dict):
        return IncidentReport.model_validate_json(json.dumps(report, default=str))
    raise TypeError("report must be an IncidentReport or a dict.")


def ensure_storage_dirs(*, reports_dir: Path | str | None = None) -> Path:
    """Create the reports directory if needed and return it. Idempotent."""
    root = _resolve_reports_dir(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_report_json(
    report: IncidentReport | dict,
    *,
    reports_dir: Path | str | None = None,
    overwrite: bool = True,
) -> Path:
    """Validate, redact, and persist ``report`` as ``{incident_id}.json``.

    The report is validated through :class:`IncidentReport` and every string is
    redacted before writing, so no raw secret reaches disk. Returns the written
    path. With ``overwrite=False`` an existing file raises
    :class:`ReportExistsError` instead of being replaced.
    """
    model = _coerce_report(report)
    root = ensure_storage_dirs(reports_dir=reports_dir)
    path = _report_path(model.incident_id, ".json", root)
    if not overwrite and path.exists():
        raise ReportExistsError(
            f"Report already exists for '{model.incident_id}' (overwrite=False)."
        )
    # render_json redacts the whole tree and pretty-prints deterministically.
    path.write_text(render_json(model) + "\n", encoding="utf-8")
    return path


def save_report_markdown(
    incident_id: str,
    markdown: str,
    *,
    reports_dir: Path | str | None = None,
    overwrite: bool = True,
) -> Path:
    """Redact and persist a Markdown report as ``{incident_id}.md``.

    ``markdown`` is treated as untrusted text and passed through the redactor
    before writing (the redactor is idempotent, so already-clean text is left
    unchanged). Returns the written path. With ``overwrite=False`` an existing
    file raises :class:`ReportExistsError`.
    """
    if not isinstance(markdown, str):
        raise TypeError("markdown must be a string.")
    root = ensure_storage_dirs(reports_dir=reports_dir)
    path = _report_path(incident_id, ".md", root)
    if not overwrite and path.exists():
        raise ReportExistsError(
            f"Markdown report already exists for '{incident_id}' (overwrite=False)."
        )
    path.write_text(redact_secrets(markdown), encoding="utf-8")
    return path


def load_report_json(
    incident_id: str, *, reports_dir: Path | str | None = None
) -> IncidentReport | None:
    """Return the stored report for ``incident_id``, or ``None`` if absent.

    Mirrors the in-memory ``get_report`` convention: a missing report is not an
    error, it returns ``None``. A present file is re-validated through
    :class:`IncidentReport` so corrupted data is surfaced rather than trusted.
    """
    path = _report_path(incident_id, ".json", reports_dir)
    if not path.is_file():
        return None
    return IncidentReport.model_validate_json(path.read_text(encoding="utf-8"))


def report_exists(
    incident_id: str, *, reports_dir: Path | str | None = None
) -> bool:
    """Return ``True`` if a JSON report is stored for ``incident_id``."""
    path = _report_path(incident_id, ".json", reports_dir)
    return path.is_file()


def list_reports(*, reports_dir: Path | str | None = None) -> list[str]:
    """Return the sorted incident ids that have a stored JSON report.

    Deterministic (sorted) and side-effect free: it never creates the directory
    and returns ``[]`` when no reports exist.
    """
    root = _resolve_reports_dir(reports_dir)
    if not root.is_dir():
        return []
    return sorted(path.stem for path in root.glob("*.json") if path.is_file())
