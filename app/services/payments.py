import logging
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Caller
from app.core.config import get_settings
from app.core.orders import OrderService
from app.models import Payment
from app.services import providers
from app.timeutil import as_utc, utcnow

logger = logging.getLogger("vandi.payments")
PAYABLE_BOOKING_STATUSES = ("assigned", "in_progress")


async def start_booking_payment(
    session: AsyncSession, caller: Caller, booking_id: str, return_to: str, orders: OrderService
) -> Payment:
    """Create (or reuse) the ₹1 confirmation payment for an assigned booking."""
    booking = await orders.booking(booking_id)
    if booking is None or booking.customer_user_id != caller.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    if booking.payment_status != "unpaid":
        raise HTTPException(status.HTTP_409_CONFLICT, "This booking is already confirmed")
    if booking.status not in PAYABLE_BOOKING_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can pay once a driver is assigned")

    now = utcnow()
    existing = await session.scalar(
        select(Payment)
        .where(Payment.booking_id == booking_id, Payment.status == "created")
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    if existing and existing.expires_at and as_utc(existing.expires_at) > now + timedelta(minutes=3) and existing.return_to == return_to:
        return existing  # the customer tapped Pay again: same page

    s = get_settings()
    p = Payment(
        booking_id=booking.id,
        booking_reference=booking.reference,
        customer_user_id=caller.user_id,
        amount_paise=booking.fee_paise,
        provider=s.payment_provider,
        return_to=return_to,
        expires_at=now + timedelta(minutes=s.link_expiry_minutes),
    )
    session.add(p)
    await session.flush()
    if s.payment_provider == "razorpay":
        created = await providers.razorpay_create(p, booking.customer_name, booking.customer_phone, p.expires_at)
    else:
        created = providers.mock_create(p)
    p.provider_ref, p.pay_url = created.ref, created.url
    await session.commit()
    return p


async def mark_paid(session: AsyncSession, p: Payment, provider_payment_id: str | None, orders: OrderService) -> Payment:
    """Record the payment and confirm the booking. Safe to call again (callback + webhook)."""
    if p.status != "paid":
        p.status = "paid"
        p.paid_at = utcnow()
        p.provider_payment_id = provider_payment_id or p.provider_payment_id
        p.failure_reason = None
        await session.commit()
        logger.info("Payment %s for booking %s paid", p.id, p.booking_reference)
    await notify_booking(session, p, orders)
    return p


async def notify_booking(session: AsyncSession, p: Payment, orders: OrderService) -> None:
    if p.status == "paid" and p.booking_notified_at is None:
        if await orders.mark_paid(p.booking_id, p.id, p.amount_paise):
            p.booking_notified_at = utcnow()
            await session.commit()


async def mark_failed(session: AsyncSession, p: Payment, reason: str) -> Payment:
    if p.status == "created":
        p.status = "failed"
        p.failure_reason = reason[:300]
        await session.commit()
    return p


def back_to_booking(p: Payment) -> str:
    """Where the customer goes after the payment page: their booking on the website or in the app."""
    s = get_settings()
    outcome = "paid" if p.status == "paid" else "failed"
    base = s.app_return_url if p.return_to == "app" else f"{s.web_url.rstrip('/')}/dashboard"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}booking={p.booking_id}&payment={outcome}"
