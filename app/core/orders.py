"""Talks to the order-service's /internal/bookings API (X-Service-Token)."""

import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger("vandi.payments")


@dataclass
class BookingInfo:
    id: str
    reference: str
    customer_user_id: str
    customer_name: str | None
    customer_phone: str
    status: str
    payment_status: str
    fee_paise: int
    driver_name: str | None


class OrderService:
    def _client(self) -> httpx.AsyncClient:
        s = get_settings()
        return httpx.AsyncClient(
            base_url=s.order_service_url.rstrip("/"),
            headers={"X-Service-Token": s.service_token},
            timeout=s.order_service_timeout_s,
        )

    async def booking(self, booking_id: str) -> BookingInfo | None:
        try:
            async with self._client() as c:
                r = await c.get(f"/internal/bookings/{booking_id}")
        except httpx.HTTPError:
            logger.exception("Order-service unreachable")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bookings are unavailable right now")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            logger.error("Order-service returned %s: %s", r.status_code, r.text[:300])
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bookings are unavailable right now")
        keys = BookingInfo.__dataclass_fields__.keys()
        return BookingInfo(**{k: v for k, v in r.json().items() if k in keys})

    async def mark_paid(self, booking_id: str, payment_id: str, amount_paise: int) -> bool:
        try:
            async with self._client() as c:
                r = await c.post(f"/internal/bookings/{booking_id}/paid", json={"payment_id": payment_id, "amount_paise": amount_paise})
        except httpx.HTTPError:
            logger.exception("Could not tell the order-service that %s is paid", booking_id)
            return False
        if r.status_code != 200:
            logger.error("Order-service refused paid notice for %s: %s %s", booking_id, r.status_code, r.text[:300])
            return False
        return True


def get_orders() -> OrderService:
    return OrderService()
