import hashlib
import hmac
import json

import pytest

from app.core.config import get_settings
from tests.conftest import ADMIN, CUSTOMER, OTHER

pytestmark = pytest.mark.asyncio


async def start(client, return_to="web", headers=CUSTOMER):
    return await client.post("/payments/booking/b-1", json={"return_to": return_to}, headers=headers)


async def test_mock_payment_confirms_booking_and_returns_to_it(client, orders):
    r = await start(client)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["amount_paise"] == 100 and p["status"] == "created"
    assert p["pay_url"] == f"http://pay.test/mock-checkout/{p['id']}"
    assert (await start(client)).json()["id"] == p["id"]  # tapping Pay again reuses the page

    page = await client.get(f"/mock-checkout/{p['id']}")
    assert page.status_code == 200 and "VOABC123" in page.text and "₹1" in page.text

    r = await client.post(f"/mock-checkout/{p['id']}", data={"outcome": "paid"}, follow_redirects=False)
    back = await client.get(r.headers["location"], follow_redirects=False)
    assert back.status_code == 303
    assert back.headers["location"] == "http://web.test/dashboard?booking=b-1&payment=paid"
    assert orders.paid == [("b-1", p["id"], 100)]
    got = (await client.get(f"/payments/{p['id']}", headers=CUSTOMER)).json()
    assert got["status"] == "paid" and got["paid_at"]

    # Paid bookings can't be paid again; other customers can't see the payment
    assert (await start(client)).status_code == 409
    assert (await client.get(f"/payments/{p['id']}", headers=OTHER)).status_code == 404


async def test_failed_payment_and_app_return(client, orders):
    p = (await start(client, "app")).json()
    r = await client.post(f"/mock-checkout/{p['id']}", data={"outcome": "failed"}, follow_redirects=False)
    back = await client.get(r.headers["location"], follow_redirects=False)
    assert back.headers["location"] == "vandiottunar://payment?booking=b-1&payment=failed"
    assert orders.paid == []
    # A forged "paid" return is ignored
    forged = await client.get(f"/payments/return/{p['id']}?mock_outcome=paid&mock_sig=abc", follow_redirects=False)
    assert forged.headers["location"].endswith("payment=failed")


async def test_rules(client, orders):
    assert (await start(client, headers=OTHER)).status_code == 404
    orders.bookings["b-1"].status = "pending"
    assert (await start(client)).status_code == 409
    orders.bookings["b-1"].status = "assigned"
    assert (await client.post("/payments/booking/b-1", json={}, headers=ADMIN)).status_code == 403


async def test_order_service_down_is_retried(client, orders):
    orders.down = True
    p = (await start(client)).json()
    r = await client.post(f"/mock-checkout/{p['id']}", data={"outcome": "paid"}, follow_redirects=False)
    await client.get(r.headers["location"], follow_redirects=False)
    assert orders.paid == []
    orders.down = False
    await client.get(f"/payments/{p['id']}", headers=CUSTOMER)  # the app checks the payment -> retried
    assert orders.paid == [("b-1", p["id"], 100)]


async def test_razorpay_return_and_webhook(client, orders, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "payment_provider", "razorpay")
    from app.services import providers

    async def fake_create(p, name, phone, expires):
        return providers.Created(ref="plink_123", url="https://rzp.io/i/abc")

    monkeypatch.setattr(providers, "razorpay_create", fake_create)
    p = (await start(client)).json()
    assert p["pay_url"] == "https://rzp.io/i/abc"

    params = {
        "razorpay_payment_id": "pay_999",
        "razorpay_payment_link_id": "plink_123",
        "razorpay_payment_link_reference_id": p["id"],
        "razorpay_payment_link_status": "paid",
    }
    msg = "|".join([params["razorpay_payment_link_id"], p["id"], "paid", "pay_999"])
    bad = {**params, "razorpay_signature": "nope"}
    r = await client.get(f"/payments/return/{p['id']}", params=bad, follow_redirects=False)
    assert r.headers["location"].endswith("payment=failed") and orders.paid == []

    # Webhook (customer closed the page): signed with the webhook secret
    body = json.dumps(
        {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": "plink_123"}}, "payment": {"entity": {"id": "pay_999", "amount": 100}}}}
    ).encode()
    assert (await client.post("/payments/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": "x"})).status_code == 401
    sig = hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    r = await client.post("/payments/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})
    assert r.status_code == 200 and orders.paid == [("b-1", p["id"], 100)]

    # A correctly signed return after that is fine too (idempotent)
    good = {**params, "razorpay_signature": hmac.new(b"rzp-secret", msg.encode(), hashlib.sha256).hexdigest()}
    r = await client.get(f"/payments/return/{p['id']}", params=good, follow_redirects=False)
    assert r.headers["location"].endswith("payment=paid") and len(orders.paid) == 1
    assert len((await client.get("/admin/payments?booking_id=b-1", headers=ADMIN)).json()) == 1
