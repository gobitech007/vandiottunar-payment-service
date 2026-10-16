import html
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Caller, admin_only, current_caller, customer_only
from app.core.config import get_settings
from app.core.orders import OrderService, get_orders
from app.db import get_session
from app.models import Payment
from app.schemas import PaymentOut, StartPaymentIn
from app.services import payments as svc
from app.services import providers

logger = logging.getLogger("vandi.payments")
router = APIRouter(tags=["payments"])


async def _payment(session: AsyncSession, payment_id: str) -> Payment:
    p = await session.get(Payment, payment_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return p


@router.post("/payments/booking/{booking_id}", response_model=PaymentOut)
async def pay_for_booking(
    booking_id: str,
    body: StartPaymentIn,
    caller: Caller = Depends(customer_only),
    session: AsyncSession = Depends(get_session),
    orders: OrderService = Depends(get_orders),
):
    """Start the ₹1 booking confirmation. Open `pay_url`; the customer comes back to their booking."""
    return await svc.start_booking_payment(session, caller, booking_id, body.return_to, orders)


@router.post("/payments/booking/{booking_id}/cancel", response_model=PaymentOut)
async def pay_to_cancel(
    booking_id: str,
    body: StartPaymentIn,
    caller: Caller = Depends(customer_only),
    session: AsyncSession = Depends(get_session),
    orders: OrderService = Depends(get_orders),
):
    """Start the cancellation-fee payment. When it's paid the booking is cancelled."""
    return await svc.start_booking_payment(session, caller, booking_id, body.return_to, orders, svc.CANCELLATION)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(
    payment_id: str,
    caller: Caller = Depends(current_caller),
    session: AsyncSession = Depends(get_session),
    orders: OrderService = Depends(get_orders),
):
    p = await _payment(session, payment_id)
    if caller.role != "admin" and p.customer_user_id != caller.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    if p.status == "created" and p.provider == "razorpay" and p.provider_ref:
        try:  # webhook may be late: ask Razorpay directly
            link_status, pay_id = await providers.razorpay_link_status(p.provider_ref)
            if link_status == "paid":
                await svc.mark_paid(session, p, pay_id, orders)
            elif link_status in ("expired", "cancelled"):
                await svc.mark_failed(session, p, f"Payment link {link_status}")
        except Exception:
            logger.exception("Could not check payment link %s", p.provider_ref)
    await svc.notify_booking(session, p, orders)
    return p


@router.get("/payments/return/{payment_id}", include_in_schema=False)
async def payment_return(
    payment_id: str, request: Request, session: AsyncSession = Depends(get_session), orders: OrderService = Depends(get_orders)
):
    """The payment page sends the customer here; we check the result and send them back to their booking."""
    p = await _payment(session, payment_id)
    params = dict(request.query_params)
    if p.provider == "razorpay":
        if params.get("razorpay_payment_link_reference_id") == p.id and providers.razorpay_return_ok(params):
            if params.get("razorpay_payment_link_status") == "paid":
                await svc.mark_paid(session, p, params.get("razorpay_payment_id"), orders)
            else:
                await svc.mark_failed(session, p, f"Payment {params.get('razorpay_payment_link_status')}")
        elif p.status == "created":
            await svc.mark_failed(session, p, "Payment was not completed")
    else:
        outcome = params.get("mock_outcome", "")
        if params.get("mock_sig") == providers.mock_signature(p.id, outcome):
            if outcome == "paid":
                await svc.mark_paid(session, p, f"mockpay_{p.id[:8]}", orders)
            else:
                await svc.mark_failed(session, p, "Test payment failed")
    return RedirectResponse(svc.back_to_booking(p), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/payments/webhooks/razorpay", include_in_schema=False)
async def razorpay_webhook(request: Request, session: AsyncSession = Depends(get_session), orders: OrderService = Depends(get_orders)):
    """Razorpay webhook (events: payment_link.paid). Backs up the redirect if the customer closes the page."""
    raw = await request.body()
    if not providers.razorpay_webhook_ok(raw, request.headers.get("X-Razorpay-Signature")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    event = await request.json()
    if event.get("event") == "payment_link.paid":
        link = event["payload"]["payment_link"]["entity"]
        p = await session.scalar(select(Payment).where(Payment.provider_ref == link["id"]))
        if p is not None:
            pay = event["payload"].get("payment", {}).get("entity", {})
            if pay.get("amount", p.amount_paise) >= p.amount_paise:
                await svc.mark_paid(session, p, pay.get("id"), orders)
    return {"ok": True}


@router.get("/admin/payments", response_model=list[PaymentOut])
async def admin_list(booking_id: str | None = None, _: Caller = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    q = select(Payment).order_by(Payment.created_at.desc()).limit(200)
    if booking_id:
        q = q.where(Payment.booking_id == booking_id)
    return (await session.scalars(q)).all()


# ---------- Test checkout (PAYMENT_PROVIDER=mock) ----------

def _mock_only() -> None:
    s = get_settings()
    if s.is_production or s.payment_provider != "mock":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/mock-checkout/{payment_id}", response_class=HTMLResponse, include_in_schema=False)
async def mock_checkout(payment_id: str, session: AsyncSession = Depends(get_session)):
    _mock_only()
    p = await _payment(session, payment_id)
    amount = f"₹{p.amount_paise / 100:g}"
    ref = html.escape(p.booking_reference)
    what = "Cancel booking" if p.purpose == "cancellation_fee" else "Confirm booking"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Test payment · Vandi Ottunar</title>
<style>body{{font-family:system-ui,sans-serif;background:#f7f1e3;margin:0;display:grid;place-items:center;min-height:100vh;color:#1d2420}}
.card{{background:#fffdf8;border:1px solid #e2dccb;border-radius:14px;padding:28px;max-width:360px;width:calc(100% - 32px)}}
.tag{{display:inline-block;background:#f2b705;color:#0e3b2e;font-weight:800;border-radius:999px;padding:2px 10px;font-size:12px}}
h1{{font-size:20px;margin:12px 0 4px}} .amt{{font-size:40px;font-weight:900;color:#0e3b2e;margin:12px 0}}
button{{font:inherit;font-weight:800;width:100%;min-height:48px;border-radius:10px;border:0;margin-top:10px;cursor:pointer}}
.pay{{background:#0e3b2e;color:#f7f1e3}} .fail{{background:#fff;border:1.5px solid #a3361a;color:#a3361a}}</style></head>
<body><form class="card" method="post"><span class="tag">TEST MODE · no real money</span>
<h1>{what} {ref}</h1><div class="amt">{amount}</div>
<button class="pay" name="outcome" value="paid">Pay {amount} (test)</button>
<button class="fail" name="outcome" value="failed">Make the payment fail</button></form></body></html>"""


@router.post("/mock-checkout/{payment_id}", include_in_schema=False)
async def mock_checkout_submit(payment_id: str, outcome: str = Form(...), session: AsyncSession = Depends(get_session)):
    _mock_only()
    p = await _payment(session, payment_id)
    outcome = "paid" if outcome == "paid" else "failed"
    target = f"{providers.return_url(p)}?mock_outcome={outcome}&mock_sig={providers.mock_signature(p.id, outcome)}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
