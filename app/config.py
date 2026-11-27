"""Application configuration powered by pydantic-settings."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_str_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            parsed = json.loads(raw)
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(value).strip()]


def _parse_int_list(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            parsed = json.loads(raw)
            return [int(item) for item in parsed]
        return [int(part.strip()) for part in raw.split(",") if part.strip()]
    return [int(value)]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram Bot
    BOT_TOKEN: SecretStr = Field(default=SecretStr("000000:TEST_TOKEN"))
    BOT_MODE: Literal["polling", "webhook"] = "polling"
    BOT_WEBHOOK_SECRET: str = ""
    BOT_USERNAME: str = ""
    ADMIN_IDS: Annotated[list[int], NoDecode] = Field(default_factory=list)
    THROTTLE_RATE_LIMIT: float = 0.6

    # Web Server & Webhooks
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080
    WEBHOOK_DOMAIN: str = ""

    # PostgreSQL & Redis
    POSTGRES_DB: str = "remnabot"
    POSTGRES_USER: str = "remnabot"
    POSTGRES_PASSWORD: str = "remnabot"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = "postgresql+asyncpg://remnabot:remnabot@postgres:5432/remnabot"
    REDIS_URL: str = "redis://redis:6379/0"

    # Remnawave Panel
    REMNAWAVE_URL: str = "http://remnawave:3000"
    REMNAWAVE_API_TOKEN: SecretStr = Field(default=SecretStr(""))
    REMNAWAVE_API_TIMEOUT: float = 15.0
    REMNAWAVE_API_RETRIES: int = 3
    REMNAWAVE_TRAFFIC_STRATEGY: str = "NO_RESET"
    REMNAWAVE_DEFAULT_SQUADS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Trial Defaults
    TRIAL_ENABLED: bool = True
    TRIAL_DURATION_DAYS: int = 3
    TRIAL_TRAFFIC_GB: int = 10
    TRIAL_DEVICE_LIMIT: int = 2

    # Referral Program Defaults
    REFERRAL_ENABLED: bool = True
    REFERRAL_REWARD_TYPE: Literal["percent", "days"] = "percent"
    REFERRAL_REWARD_PERCENT: int = 15
    REFERRAL_REWARD_DAYS: int = 5
    REFERRAL_FIRST_PAYMENT_ONLY: bool = False

    # Payments
    STARS_RUB_RATE: float = 1.8
    CRYPTOBOT_TOKEN: SecretStr = Field(default=SecretStr(""))
    CRYPTOBOT_TESTNET: bool = False
    CRYPTOBOT_ACCEPTED_ASSETS: str = "USDT,TON,BTC,ETH,LTC,TRX,USDC"
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: SecretStr = Field(default=SecretStr(""))
    YOOKASSA_RETURN_URL: str = ""
    YOOKASSA_TRUST_WEBHOOK_WITHOUT_API_CHECK: bool = False

    # Support & Scheduler
    SUPPORT_URL: str = "https://t.me/support"
    SUPPORT_TEXT: str = (
        "Если у вас возникли вопросы по подключению или оплате, напишите в нашу поддержку."
    )
    SCHEDULER_EXPIRY_INTERVAL_MINUTES: int = 5
    SCHEDULER_TRAFFIC_INTERVAL_MINUTES: int = 10
    ENABLE_EXPIRED_CLEANUP: bool = False
    EXPIRED_CLEANUP_DAYS: int = 14

    LOG_LEVEL: str = "INFO"

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def _validate_admin_ids(cls, value: Any) -> list[int]:
        return _parse_int_list(value)

    @field_validator("REMNAWAVE_DEFAULT_SQUADS", mode="before")
    @classmethod
    def _validate_default_squads(cls, value: Any) -> list[str]:
        return _parse_str_list(value)

    @field_validator("WEBHOOK_DOMAIN", "REMNAWAVE_URL", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().rstrip("/")

    @field_validator("REMNAWAVE_TRAFFIC_STRATEGY", mode="before")
    @classmethod
    def _normalize_strategy(cls, value: Any) -> str:
        val = str(value or "NO_RESET").strip().upper()
        return val or "NO_RESET"

    @property
    def is_cryptobot_enabled(self) -> bool:
        return bool(self.CRYPTOBOT_TOKEN.get_secret_value().strip())

    @property
    def is_yookassa_enabled(self) -> bool:
        return bool(
            self.YOOKASSA_SHOP_ID.strip() and self.YOOKASSA_SECRET_KEY.get_secret_value().strip()
        )

    @property
    def webhook_base_url(self) -> str:
        return self.WEBHOOK_DOMAIN.rstrip("/")

    @property
    def telegram_webhook_url(self) -> str:
        if not self.webhook_base_url:
            return ""
        return f"{self.webhook_base_url}/webhooks/telegram"

    @property
    def cryptobot_webhook_url(self) -> str:
        if not self.webhook_base_url:
            return ""
        return f"{self.webhook_base_url}/webhooks/cryptobot"

    @property
    def yookassa_webhook_url(self) -> str:
        if not self.webhook_base_url:
            return ""
        return f"{self.webhook_base_url}/webhooks/yookassa"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
