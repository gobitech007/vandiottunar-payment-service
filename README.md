# Vandi Ottunar — Payment Service

Takes the **₹1 booking confirmation** payment. Once a driver is assigned, the customer pays ₹1; until then the
order-service keeps the driver's chat link and the trip start code hidden.

FastAPI · async SQLAlchemy · PostgreSQL (`vandi_payments`) · Razorpay Payment Links · port **8002**

## Flow

1. Customer taps **Pay ₹1** → `POST /payments/booking/{booking_id}` (`{"return_to": "web" | "app"}`, customer login token).
   The service checks the booking with the order-service (`GET /internal/bookings/{id}`): it must be theirs, assigned and unpaid.
2. The response has `pay_url`: a Razorpay payment link (UPI, cards, netbanking). Tapping Pay again reuses it.
3. After paying, Razorpay sends the customer to `GET /payments/return/{id}` with a signature. We verify it, then
   tell the order-service (`POST /internal/bookings/{id}/paid`) and **redirect back to the booking**:
   - website: `WEB_URL/dashboard?booking=…&payment=paid|failed`
   - app: `APP_RETURN_URL?booking=…&payment=paid|failed` (deep link `vandiottunar://payment`)
4. The Razorpay webhook (`payment_link.paid`) does the same if the customer closes the page. Both are idempotent.
5. If the order-service was down, the "paid" notice is retried the next time anyone checks `GET /payments/{id}`.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env      # same JWT_SECRET and SERVICE_TOKEN as the other services
# create database "vandi_payments" in pgAdmin, then:
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8002
```

With `PAYMENT_PROVIDER=mock` (default) the pay link opens a **test checkout page** on this service with
*Pay ₹1 (test)* and *Make the payment fail*. No keys, no money.

## Razorpay

1. Razorpay Dashboard → *API Keys*: create **test** keys → `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`; set `PAYMENT_PROVIDER=razorpay`.
2. *Webhooks*: URL `https://<your payment domain>/payments/webhooks/razorpay`, event **payment_link.paid**, secret → `RAZORPAY_WEBHOOK_SECRET`.
3. `PUBLIC_URL` must be reachable by the customer's browser (Razorpay redirects there). Locally, `http://localhost:8002` works for browser tests.
4. Switch to live keys after KYC. `ENVIRONMENT=production` refuses to start with the mock provider or missing keys.

## API

| Method | Path | Who |
|---|---|---|
| POST | `/payments/booking/{booking_id}` | customer: start (or reuse) the ₹1 payment |
| GET | `/payments/{id}` | customer/admin: status (also re-checks Razorpay and retries the order-service notice) |
| GET | `/payments/return/{id}` | browser, from Razorpay/mock checkout → redirect back to the booking |
| POST | `/payments/webhooks/razorpay` | Razorpay |
| GET | `/admin/payments?booking_id=` | admin |
| GET/POST | `/mock-checkout/{id}` | test checkout page (mock provider only) |

## Tests

```bash
python -m pytest -q
```
