from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Vandi Ottunar Payment Service"
    environment: str = "development"  # development | production
    auto_create_tables: bool = False

    database_url: str = "postgresql+asyncpg://postgres:admin@localhost:5432/vandi_payments"

    # Must be the SAME secret as the user-service: this service trusts its login tokens
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"

    # The order-service: reads bookings and is told when the fee is paid (same SERVICE_TOKEN everywhere)
    order_service_url: str = "http://localhost:8001"
    service_token: str = ""
    order_service_timeout_s: float = 5.0

    # mock = a test checkout page served by this service (development); razorpay = real payments
    payment_provider: str = "mock"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    link_expiry_minutes: int = 20  # Razorpay needs at least 15

    # Where this service is reachable from browsers (Razorpay sends the customer back here)
    public_url: str = "http://localhost:8002"
    # Where customers land after paying: the website's dashboard, or the app (deep link)
    web_url: str = "http://localhost:5173"
    app_return_url: str = "vandiottunar://payment"

    cors_origins: str = "http://localhost:5173"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_runtime(self) -> None:
        if self.is_production:
            if self.jwt_secret in ("", "change-me") or len(self.jwt_secret) < 32:
                raise RuntimeError("JWT_SECRET must match the user-service's long random secret in production")
            if len(self.service_token) < 32:
                raise RuntimeError("SERVICE_TOKEN must be set (same value as the other services) in production")
            if self.payment_provider != "razorpay":
                raise RuntimeError("PAYMENT_PROVIDER must be razorpay in production")
            if not (self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_webhook_secret):
                raise RuntimeError("RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET are required")


@lru_cache
def get_settings() -> Settings:
    return Settings()
