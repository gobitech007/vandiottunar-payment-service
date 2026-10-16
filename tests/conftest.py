import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="vandi-payments-test-")
os.environ.update(
    {
        "ENVIRONMENT": "development",
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmp}/test.db"),
        "JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-0123456789",
        "PAYMENT_PROVIDER": "mock",
        "PUBLIC_URL": "http://pay.test",
        "WEB_URL": "http://web.test",
        "RAZORPAY_KEY_ID": "rzp_test_x",
        "RAZORPAY_KEY_SECRET": "rzp-secret",
        "RAZORPAY_WEBHOOK_SECRET": "hook-secret",
    }
)

from datetime import datetime, timedelta, timezone  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.orders import BookingInfo, OrderService, get_orders  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


def token(user_id: str, role: str) -> dict:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "role": role, "typ": "access", "iat": now, "exp": now + timedelta(hours=1)}
    return {"Authorization": f"Bearer {jwt.encode(payload, os.environ['JWT_SECRET'], algorithm='HS256')}"}


CUSTOMER = token("cust-1", "customer")
OTHER = token("cust-2", "customer")
ADMIN = token("admin-1", "admin")


class FakeOrders(OrderService):
    def __init__(self):
        self.bookings: dict[str, BookingInfo] = {}
        self.paid: list[tuple[str, str, int]] = []
        self.down = False

    async def booking(self, booking_id):
        return self.bookings.get(booking_id)

    async def mark_paid(self, booking_id, payment_id, amount_paise, purpose="booking_confirmation"):
        if self.down:
            return False
        self.paid.append((booking_id, payment_id, amount_paise))
        self.bookings[booking_id].payment_status = "paid"
        return True


@pytest.fixture
def orders():
    fake = FakeOrders()
    fake.bookings["b-1"] = BookingInfo(
        id="b-1", reference="VOABC123", customer_user_id="cust-1", customer_name="Priya", customer_phone="+919876600001",
        status="assigned", payment_status="unpaid", fee_paise=100, driver_name="Murugan",
    )
    app.dependency_overrides[get_orders] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_orders, None)


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://pay.test") as c:
        yield c
