"""CI-covered reproduction for the controlled demo payments route bug."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _load_demo_payments_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "demo"
        / "demo_repo"
        / "app"
        / "routes"
        / "payments.py"
    )
    spec = importlib.util.spec_from_file_location("controlled_demo_payments", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_user_should_return_404() -> None:
    """Unknown users should be rejected instead of raising AttributeError."""
    payments = _load_demo_payments_module()
    app = FastAPI()
    app.include_router(payments.router)
    client = TestClient(app)

    response = client.post(
        "/payments",
        json={"user_id": "does_not_exist", "amount": 1000, "currency": "USD"},
    )

    assert response.status_code == 404
