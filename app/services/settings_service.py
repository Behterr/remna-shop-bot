"""Runtime-configurable settings persisted in PostgreSQL with .env fallback."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import AppSetting


@dataclass(slots=True)
class TrialRuntimeConfig:
    enabled: bool
    duration_days: int
    traffic_gb: int
    device_limit: int


@dataclass(slots=True)
class ReferralRuntimeConfig:
    enabled: bool
    reward_type: str  # "percent" or "days"
    reward_percent: int
    reward_days: int
    first_payment_only: bool


class SettingsService:
    """Reads and updates runtime trial and referral parameters."""

    KEY_TRIAL_ENABLED = "trial_enabled"
    KEY_TRIAL_DURATION_DAYS = "trial_duration_days"
    KEY_TRIAL_TRAFFIC_GB = "trial_traffic_gb"
    KEY_TRIAL_DEVICE_LIMIT = "trial_device_limit"

    KEY_REFERRAL_ENABLED = "referral_enabled"
    KEY_REFERRAL_REWARD_TYPE = "referral_reward_type"
    KEY_REFERRAL_REWARD_PERCENT = "referral_reward_percent"
    KEY_REFERRAL_REWARD_DAYS = "referral_reward_days"
    KEY_REFERRAL_FIRST_PAYMENT_ONLY = "referral_first_payment_only"

    @staticmethod
    def _parse_bool(raw: str | None, default: bool) -> bool:
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_int(raw: str | None, default: int) -> int:
        if raw is None:
            return default
        try:
            return int(raw.strip())
        except ValueError:
            return default

    @classmethod
    async def _load_map(cls, session: AsyncSession) -> dict[str, str]:
        result = await session.execute(select(AppSetting))
        return {row.key: row.value for row in result.scalars().all()}

    @classmethod
    async def set_value(cls, session: AsyncSession, key: str, value: str) -> None:
        existing = await session.get(AppSetting, key)
        if existing is None:
            session.add(AppSetting(key=key, value=str(value)))
        else:
            existing.value = str(value)
        await session.flush()

    @classmethod
    async def get_trial_config(
        cls,
        session: AsyncSession,
        settings: Settings,
    ) -> TrialRuntimeConfig:
        values = await cls._load_map(session)
        return TrialRuntimeConfig(
            enabled=cls._parse_bool(values.get(cls.KEY_TRIAL_ENABLED), settings.TRIAL_ENABLED),
            duration_days=max(
                1,
                cls._parse_int(
                    values.get(cls.KEY_TRIAL_DURATION_DAYS),
                    settings.TRIAL_DURATION_DAYS,
                ),
            ),
            traffic_gb=max(
                0,
                cls._parse_int(values.get(cls.KEY_TRIAL_TRAFFIC_GB), settings.TRIAL_TRAFFIC_GB),
            ),
            device_limit=max(
                0,
                cls._parse_int(
                    values.get(cls.KEY_TRIAL_DEVICE_LIMIT),
                    settings.TRIAL_DEVICE_LIMIT,
                ),
            ),
        )

    @classmethod
    async def get_referral_config(
        cls,
        session: AsyncSession,
        settings: Settings,
    ) -> ReferralRuntimeConfig:
        values = await cls._load_map(session)
        reward_type = values.get(cls.KEY_REFERRAL_REWARD_TYPE, settings.REFERRAL_REWARD_TYPE)
        if reward_type not in {"percent", "days"}:
            reward_type = "percent"
        return ReferralRuntimeConfig(
            enabled=cls._parse_bool(
                values.get(cls.KEY_REFERRAL_ENABLED),
                settings.REFERRAL_ENABLED,
            ),
            reward_type=reward_type,
            reward_percent=max(
                0,
                cls._parse_int(
                    values.get(cls.KEY_REFERRAL_REWARD_PERCENT),
                    settings.REFERRAL_REWARD_PERCENT,
                ),
            ),
            reward_days=max(
                0,
                cls._parse_int(
                    values.get(cls.KEY_REFERRAL_REWARD_DAYS),
                    settings.REFERRAL_REWARD_DAYS,
                ),
            ),
            first_payment_only=cls._parse_bool(
                values.get(cls.KEY_REFERRAL_FIRST_PAYMENT_ONLY),
                settings.REFERRAL_FIRST_PAYMENT_ONLY,
            ),
        )
