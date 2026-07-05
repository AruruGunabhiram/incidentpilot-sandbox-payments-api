"""Tests for the Phase 5 durable, JSON-first report storage.

Every test targets an isolated ``tmp_path`` via the keyword-only ``reports_dir``
override, so the real ``app/storage/reports/`` directory is never touched and
runs are deterministic.
"""

import json

import pytest
from pydantic import ValidationError

from app.schemas.report import IncidentReport
from app.storage import incident_store as store
from app.storage.incident_store import InvalidIncidentIdError, ReportExistsError
from app.tools.redactor import REDACTION_MARKER

SECRET_TOKEN = "ghp_fakeTokenForDemoOnly1234567890"
SECRET_API_KEY = "fake-api-key-12345"


def _valid_report(incident_id: str = "inc_001", **overrides) -> dict:
    """Return a minimal, schema-valid IncidentReport dict for tests."""
    data = {
        "incident_id": incident_id,
        "title": "POST /payments returns 500",
        "severity": "SEV2",
        "affected_service": "payments-api",
        "status": "awaiting_human_approval",
        "summary": "Null dereference in create_payment.",
        "confidence": 0.8,
        "needs_human_review": True,
        "blocked_reasons": [],
    }
    data.update(overrides)
    return data


# ---- directory creation ----------------------------------------------------


def test_ensure_storage_dirs_creates_directory(tmp_path):
    target = tmp_path / "reports"
    assert not target.exists()

    created = store.ensure_storage_dirs(reports_dir=target)
    assert created.is_dir()

    # Idempotent: a second call does not error and the dir still exists.
    again = store.ensure_storage_dirs(reports_dir=target)
    assert again.is_dir()


# ---- JSON save / load round-trip -------------------------------------------


def test_save_and_load_report_json_round_trips(tmp_path):
    path = store.save_report_json(_valid_report(), reports_dir=tmp_path)

    assert path.name == "inc_001.json"
    assert path.is_file()
    # The file on disk is valid JSON.
    json.loads(path.read_text(encoding="utf-8"))

    loaded = store.load_report_json("inc_001", reports_dir=tmp_path)
    assert isinstance(loaded, IncidentReport)
    assert loaded.incident_id == "inc_001"
    assert loaded.severity == "SEV2"
    assert store.report_exists("inc_001", reports_dir=tmp_path)


def test_save_report_json_accepts_model_and_dict(tmp_path):
    model = IncidentReport.model_validate(_valid_report(incident_id="inc_model"))
    path = store.save_report_json(model, reports_dir=tmp_path)
    assert path.name == "inc_model.json"
    assert store.load_report_json("inc_model", reports_dir=tmp_path).incident_id == "inc_model"


# ---- Markdown save ---------------------------------------------------------


def test_save_report_markdown_writes_file(tmp_path):
    path = store.save_report_markdown(
        "inc_001", "# Incident\n\nAll clear.\n", reports_dir=tmp_path
    )
    assert path.name == "inc_001.md"
    assert path.read_text(encoding="utf-8").startswith("# Incident")


# ---- missing report convention: returns None / False -----------------------


def test_missing_report_returns_none_and_false(tmp_path):
    assert store.load_report_json("inc_404", reports_dir=tmp_path) is None
    assert store.report_exists("inc_404", reports_dir=tmp_path) is False


# ---- path-escape safety: incident_id cannot leave the reports dir -----------


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "../../etc/passwd",
        "foo/bar",
        "a/../b",
        "x\\y",
        "a..b",
        ".hidden",
    ],
)
def test_invalid_incident_id_cannot_escape(tmp_path, bad_id):
    with pytest.raises(InvalidIncidentIdError):
        store.load_report_json(bad_id, reports_dir=tmp_path)
    with pytest.raises(InvalidIncidentIdError):
        store.report_exists(bad_id, reports_dir=tmp_path)
    with pytest.raises(InvalidIncidentIdError):
        store.save_report_markdown(bad_id, "x", reports_dir=tmp_path)
    with pytest.raises(InvalidIncidentIdError):
        store.save_report_json(_valid_report(incident_id=bad_id), reports_dir=tmp_path)

    # Nothing leaked outside the reports dir.
    assert list(tmp_path.parent.glob("escape*")) == []
    assert list(tmp_path.parent.glob("passwd*")) == []


# ---- explicit overwrite policy ---------------------------------------------


def test_overwrite_is_explicit(tmp_path):
    store.save_report_json(_valid_report(summary="first"), reports_dir=tmp_path)

    # Default overwrite=True replaces and updates content ("latest report wins").
    store.save_report_json(_valid_report(summary="second"), reports_dir=tmp_path)
    assert store.load_report_json("inc_001", reports_dir=tmp_path).summary == "second"

    # overwrite=False refuses to clobber an existing file.
    with pytest.raises(ReportExistsError):
        store.save_report_json(
            _valid_report(summary="third"), reports_dir=tmp_path, overwrite=False
        )


# ---- deterministic listing -------------------------------------------------


def test_list_reports_is_sorted_and_filtered(tmp_path):
    for inc in ["inc_003", "inc_001", "inc_002"]:
        store.save_report_json(_valid_report(incident_id=inc), reports_dir=tmp_path)
    # A markdown file and a .gitkeep must not appear in the JSON listing.
    store.save_report_markdown("inc_001", "# md\n", reports_dir=tmp_path)
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")

    assert store.list_reports(reports_dir=tmp_path) == ["inc_001", "inc_002", "inc_003"]


def test_list_reports_empty_when_no_dir(tmp_path):
    assert store.list_reports(reports_dir=tmp_path / "missing") == []


# ---- schema validation before writing --------------------------------------


def test_save_report_json_rejects_invalid_schema(tmp_path):
    with pytest.raises(ValidationError):
        store.save_report_json(_valid_report(severity="SEVERE"), reports_dir=tmp_path)
    # Nothing was written.
    assert store.list_reports(reports_dir=tmp_path) == []


# ---- no raw secrets persisted ----------------------------------------------


def test_no_raw_secret_in_saved_json(tmp_path):
    report = _valid_report(
        summary=f"Deploy logged api_key={SECRET_API_KEY} and token {SECRET_TOKEN}.",
    )
    path = store.save_report_json(report, reports_dir=tmp_path)
    raw = path.read_text(encoding="utf-8")

    assert SECRET_TOKEN not in raw
    assert SECRET_API_KEY not in raw
    assert REDACTION_MARKER in raw


def test_no_raw_secret_in_saved_markdown(tmp_path):
    path = store.save_report_markdown(
        "inc_001", f"# Incident\n\nUsing token {SECRET_TOKEN}\n", reports_dir=tmp_path
    )
    raw = path.read_text(encoding="utf-8")

    assert SECRET_TOKEN not in raw
    assert REDACTION_MARKER in raw
