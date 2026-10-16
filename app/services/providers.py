"""Payment providers.

razorpay: a Razorpay Payment Link per payment. The customer pays on Razorpay's page (UPI, cards,
          netbanking) and Razorpay sends them back to /payments/return/<id> with a signature.
mock:     a test checkout page on this service with "Pay" and "Fail" buttons (development only).
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

import httpx
from fastapi import HTTPException

from app.core.config import get_settings
from app.models import Payment


@dataclass
class Created:
    ref: str
    url: str


def _hmac(secret: str, message: str | bytes) -> str:
    data = message.encode() if isinstance(message, str) else message
    return hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()


def return_url(p: Payment) -> str:
    return f"{get_settings().public_url.rstrip('/')}/payments/return/{p.id}"


# ---------- Razorpay ----------

async def razorpay_create(p: Payment, customer_name: str | None, phone: str, expires_at: datetime) -> Created:
    s = get_settings()
    body = {
        "amount": p.amount_paise,
        "currency": p.currency,
        "accept_partial": False,
        "reference_id": p.id,
        "description": f"Vandi Ottunar booking {p.booking_reference} confirmation",
        "customer": {"name": customer_name or "Customer", "contact": phone},
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "callback_url": return_url(p),
        "callback_method": "get",
        "expire_by": int(expires_at.timestamp()),
        "notes": {"booking_id": p.booking_id, "booking_reference": p.booking_reference},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.razorpay.com/v1/payment_links", json=body, auth=(s.razorpay_key_id, s.razorpay_key_secret)
            )
    except httpx.HTTPError:
        raise HTTPException(503, "The payment page is unavailable right now. Please try again.")
    if r.status_code >= 300:
        raise HTTPException(502, f"Payment provider error: {r.json().get('error', {}).get('description', r.status_code)}")
    data = r.json()
    return Created(ref=data["id"], url=data["short_url"])


def razorpay_return_ok(params: dict[str, str]) -> bool:
    """Signature Razorpay adds when it redirects back after a payment link is paid."""
    s = get_settings()
    try:
        message = "|".join(
            params[k]
            for k in (
                "razorpay_payment_link_id",
                "razorpay_payment_link_reference_id",
                "razorpay_payment_link_status",
                "razorpay_payment_id",
            )
        )
        given = params["razorpay_signature"]
    except KeyError:
        return False
    return hmac.compare_digest(_hmac(s.razorpay_key_secret, message), given)


def razorpay_webhook_ok(raw: bytes, signature: str | None) -> bool:
    secret = get_settings().razorpay_webhook_secret
    return bool(secret and signature) and hmac.compare_digest(_hmac(secret, raw), signature)


async def razorpay_link_status(ref: str) -> tuple[str, str | None]:
    """(status, payment id) of a payment link, for when the customer comes back without parameters."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.razorpay.com/v1/payment_links/{ref}", auth=(s.razorpay_key_id, s.razorpay_key_secret))
    r.raise_for_status()
    data = r.json()
    payments = data.get("payments") or []
    paid = next((x.get("payment_id") for x in payments if x.get("status") == "captured"), None)
    return data.get("status", ""), paid


# ---------- Mock (development) ----------

def mock_create(p: Payment) -> Created:
    return Created(ref=f"mock_{p.id[:8]}", url=f"{get_settings().public_url.rstrip('/')}/mock-checkout/{p.id}")


def mock_signature(payment_id: str, outcome: str) -> str:
    return _hmac("mock:" + get_settings().jwt_secret, f"{payment_id}|{outcome}")
