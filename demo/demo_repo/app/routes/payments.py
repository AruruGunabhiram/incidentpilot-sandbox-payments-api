"""Demo payments router for IncidentPilot's demo_repo fixture.

Simulates a tiny FastAPI payments service. Contains an INTENTIONAL bug that the
``broken_api_route`` incident scenario investigates. Do NOT fix the bug here.

The bug: ``get_user`` can return ``None`` for an unknown user id, but the route
dereferences ``user.id`` without checking, raising::

    AttributeError: 'NoneType' object has no attribute 'id'
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


# --- Fake data layer -------------------------------------------------------

# Pretend user store. Unknown user ids are simply absent (no row).
_FAKE_USERS = {
    "user_123": {"id": "user_123", "name": "Ada Lovelace"},
}


class User:
    """Minimal user record returned by the fake data layer."""

    def __init__(self, id: str, name: str) -> None:
        self.id = id
        self.name = name


def get_user(user_id: str) -> User | None:
    """Return a ``User`` for a known id, or ``None`` when no such user exists.

    NOTE: This can return ``None``. Every caller must handle the missing-user
    case before using the result.
    """
    record = _FAKE_USERS.get(user_id)
    if record is None:
        return None
    return User(id=record["id"], name=record["name"])


# --- Domain object ---------------------------------------------------------

class Payment:
    """A payment being created. ``user_id`` is filled in from the looked-up user."""

    def __init__(self, amount: int, currency: str) -> None:
        self.amount = amount
        self.currency = currency
        self.user_id: str | None = None


# --- Request model ---------------------------------------------------------

class PaymentRequest(BaseModel):
    """Incoming POST /payments body."""

    user_id: str
    amount: int
    currency: str = "USD"


# --- Route -----------------------------------------------------------------

@router.post("/payments", status_code=201)
def create_payment(request: PaymentRequest) -> dict:
    """Create a payment for the requesting user.

    Steps are kept on separate lines so the incident report can cite exact line
    numbers for the failure.
    """
    payment = Payment(amount=request.amount, currency=request.currency)

    user = get_user(request.user_id)          # may return None for unknown ids
    payment.user_id = user.id                 # BUG: user can be None here

    return {
        "status": "created",
        "user_id": payment.user_id,
        "amount": payment.amount,
        "currency": payment.currency,
    }
