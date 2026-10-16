import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class Payment(Base):
    """One payment attempt. For now: the ₹1 booking confirmation fee."""

    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    purpose: Mapped[str] = mapped_column(String(32), default="booking_confirmation")
    booking_id: Mapped[str] = mapped_column(String(36), index=True)
    booking_reference: Mapped[str] = mapped_column(String(12))
    customer_user_id: Mapped[str] = mapped_column(String(36), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    provider: Mapped[str] = mapped_column(String(16))  # mock | razorpay
    provider_ref: Mapped[str | None] = mapped_column(String(64), index=True)  # Razorpay payment link id (plink_…)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64))  # Razorpay payment id (pay_…)
    pay_url: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), default="created", index=True)  # created | paid | failed | expired
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    return_to: Mapped[str] = mapped_column(String(8), default="web")  # web | app
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the order-service accepted the "paid" notice (retried until it does)
    booking_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
