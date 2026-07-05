"""Tests for the demo payments service.

These are demo_repo FIXTURE tests, not part of IncidentPilot's main pytest
suite. They intentionally include a failing reproduction of the
``broken_api_route`` incident (the unknown-user case). Do NOT fix the bug to
make the failing test pass.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_payment_succeeds_for_known_user():
    """Happy path: a known user can create a payment (HTTP 201)."""
    response = client.post(
        "/payments",
        json={"user_id": "user_123", "amount": 1000, "currency": "USD"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["user_id"] == "user_123"
    assert body["amount"] == 1000


def test_create_payment_with_unknown_user_reproduces_incident():
    """Unknown user reproduces the production incident.

    ``get_user`` returns ``None`` for an unknown id and the route then
    dereferences ``user.id``, raising::

        AttributeError: 'NoneType' object has no attribute 'id'

    The CORRECT behavior would be a 404. This test asserts that intended
    behavior and therefore FAILS today with the AttributeError above. It is the
    grounded reproduction for ``broken_api_route``; do not weaken the assertion
    to make it green.
    """
    response = client.post(
        "/payments",
        json={"user_id": "does_not_exist", "amount": 1000, "currency": "USD"},
    )

    assert response.status_code == 404
