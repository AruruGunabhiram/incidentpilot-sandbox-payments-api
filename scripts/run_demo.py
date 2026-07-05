#!/usr/bin/env python3
"""Deterministic IncidentPilot demo: investigate a fixture, persist, summarize.

Run with::

    make demo                                   # broken_api_route fixture
    python scripts/run_demo.py                  # same as above
    python scripts/run_demo.py --scenario NAME --reports-dir DIR

No LLM, no ADK agents, no network, no API keys, no database. This driver runs
the existing deterministic investigation service over a local demo fixture,
writes the redacted JSON + Markdown report to ``app/storage/reports/``, prints a
clean console summary, and exits non-zero if the report cannot be generated or
fails to validate against the :class:`IncidentReport` schema when reloaded from
disk.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Make ``app`` importable when this file is run as a bare script (in that case
# ``sys.path[0]`` is the scripts/ directory, not the repository root).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.schemas.report import IncidentReport  # noqa: E402
from app.services import investigation_service  # noqa: E402
from app.storage import incident_store  # noqa: E402
from app.storage.incident_store import REPORTS_DIR  # noqa: E402
from app.tools.redactor import redact_secrets  # noqa: E402

DEFAULT_SCENARIO = "broken_api_route"


def _report_paths(
    incident_id: str, reports_dir: Path | str | None
) -> tuple[Path, Path]:
    """Return the ``(json_path, markdown_path)`` for ``incident_id``."""
    root = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    return root / f"{incident_id}.json", root / f"{incident_id}.md"


def build_summary(
    report: IncidentReport, scenario: str, json_path: Path, md_path: Path
) -> str:
    """Return a clean, redacted console summary of the investigation."""
    root_cause = report.root_cause.summary if report.root_cause else "(none)"
    lines = [
        "IncidentPilot demo — deterministic incident report",
        "=" * 52,
        f"  incident_id          : {report.incident_id}",
        f"  scenario             : {scenario}",
        f"  severity             : {report.severity}",
        f"  primary_error        : {report.primary_error or '(none)'}",
        f"  root cause summary   : {root_cause}",
        f"  confidence           : {report.confidence:.2f}",
        f"  needs_human_review   : {report.needs_human_review}",
        f"  JSON report path     : {json_path}",
        f"  Markdown report path : {md_path}",
    ]
    # Belt-and-suspenders: the service already redacts every report string, but
    # never let the console be the one place a secret could surface.
    return redact_secrets("\n".join(lines))


def run_demo(
    scenario: str = DEFAULT_SCENARIO,
    *,
    reports_dir: Path | str | None = None,
    stream: io.TextIOBase | None = None,
) -> int:
    """Run the demo investigation. Return ``0`` on success, non-zero on failure.

    On success the redacted ``{incident_id}.json`` and ``.md`` are written under
    ``reports_dir`` (defaults to ``app/storage/reports/``) and a summary is
    printed to ``stream`` (defaults to stdout). Any failure to generate the
    report, find the artifacts, or re-validate the saved JSON against the schema
    returns a non-zero code with a message on stderr.
    """
    out = stream if stream is not None else sys.stdout

    # Fresh in-memory slate so the run is deterministic regardless of prior state.
    incident_store.reset_store()

    try:
        report = investigation_service.investigate_incident(
            scenario, reports_dir=reports_dir
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure as an exit code
        print(f"ERROR: investigation failed: {exc}", file=sys.stderr)
        return 1

    json_path, md_path = _report_paths(report.incident_id, reports_dir)

    # Post-condition 1: both artifacts actually exist on disk.
    if not json_path.is_file() or not md_path.is_file():
        print(
            f"ERROR: expected report artifacts missing "
            f"({json_path.name}, {md_path.name}).",
            file=sys.stderr,
        )
        return 1

    # Post-condition 2: the saved JSON re-validates against IncidentReport
    # (load_report_json performs a strict model_validate_json).
    try:
        reloaded = incident_store.load_report_json(
            report.incident_id, reports_dir=reports_dir
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: saved report failed schema validation: {exc}", file=sys.stderr)
        return 1
    if reloaded is None:
        print("ERROR: saved report could not be loaded back.", file=sys.stderr)
        return 1

    print(build_summary(report, scenario, json_path, md_path), file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the IncidentPilot demo.")
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO,
        help="Demo scenario under demo/incidents/ (default: broken_api_route).",
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Override the reports output directory (default: app/storage/reports/).",
    )
    args = parser.parse_args(argv)
    return run_demo(args.scenario, reports_dir=args.reports_dir)


if __name__ == "__main__":
    raise SystemExit(main())
