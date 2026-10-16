from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StartPaymentIn(BaseModel):
    # Where to send the customer afterwards: the website, or back into the Android app
    return_to: Literal["web", "app"] = "web"


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    booking_id: str
    booking_reference: str
    amount_paise: int
    currency: str
    provider: str
    status: str
    pay_url: str | None
    failure_reason: str | None
    created_at: datetime
    paid_at: datetime | None
