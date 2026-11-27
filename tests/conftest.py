"""Shared pytest fixtures: in-memory async SQLite database, settings, and Remnawave fake."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.services.remnawave import RemnawaveHealth, RemnawaveSquad, RemnawaveUser


class FakeRemnawaveClient:
    """In-memory fake Remnawave client for deterministic business logic tests."""

    def __init__(self) -> None:
        self.users_by_uuid: dict[str, RemnawaveUser] = {}
        self.calls: list[str] = []

    async def create_user(
        self,
        *,
        telegram_id: int,
        expire_at: datetime,
        traffic_limit_bytes: int = 0,
        hwid_device_limit: int = 0,
        active_internal_squads: list[str] | None = None,
        traffic_limit_strategy: str = "NO_RESET",
        user_uuid: str | None = None,
        description: str | None = None,
        tag: str | None = None,
    ) -> RemnawaveUser:
        self.calls.append("create_user")
        uid = user_uuid or str(uuid4())
        user = RemnawaveUser(
            uuid=uid,
            username=f"tg_{telegram_id}",
            status="ACTIVE",
            traffic_limit_bytes=traffic_limit_bytes,
            traffic_limit_strategy=traffic_limit_strategy,
            used_traffic_bytes=0,
            expire_at=expire_at,
            hwid_device_limit=hwid_device_limit,
            telegram_id=telegram_id,
            subscription_url=f"https://sub.example.com/{uid}",
            active_internal_squads=list(active_internal_squads or []),
        )
        self.users_by_uuid[uid] = user
        return user

    async def get_user_by_telegram_id(self, telegram_id: int) -> RemnawaveUser | None:
        self.calls.append("get_user_by_telegram_id")
        for user in self.users_by_uuid.values():
            if user.telegram_id == telegram_id:
                return user
        return None

    async def get_user_by_uuid(self, user_uuid: str) -> RemnawaveUser | None:
        self.calls.append("get_user_by_uuid")
        return self.users_by_uuid.get(user_uuid)

    async def iter_all_users(self, batch_size: int = 100) -> list[RemnawaveUser]:
        self.calls.append("iter_all_users")
        return list(self.users_by_uuid.values())

    async def extend_subscription(
        self,
        user_uuid: str,
        days: int,
        *,
        reset_traffic: bool = False,
    ) -> RemnawaveUser:
        self.calls.append("extend_subscription")
        user = self.users_by_uuid[user_uuid]
        now = datetime.now(timezone.utc)
        base = user.expire_at if user.expire_at > now else now
        user.expire_at = base + timedelta(days=days)
        user.status = "ACTIVE"
        if reset_traffic:
            user.used_traffic_bytes = 0
        return user

    async def change_tariff(
        self,
        user_uuid: str,
        *,
        expire_at: datetime,
        traffic_limit_bytes: int,
        hwid_device_limit: int,
        active_internal_squads: list[str],
        traffic_limit_strategy: str = "NO_RESET",
        reset_traffic: bool = True,
    ) -> RemnawaveUser:
        self.calls.append("change_tariff")
        user = self.users_by_uuid[user_uuid]
        user.expire_at = expire_at
        user.traffic_limit_bytes = traffic_limit_bytes
        user.hwid_device_limit = hwid_device_limit
        user.active_internal_squads = list(active_internal_squads)
        user.traffic_limit_strategy = traffic_limit_strategy
        user.status = "ACTIVE"
        if reset_traffic:
            user.used_traffic_bytes = 0
        return user

    async def reset_traffic(self, user_uuid: str) -> RemnawaveUser:
        self.calls.append("reset_traffic")
        user = self.users_by_uuid[user_uuid]
        user.used_traffic_bytes = 0
        user.status = "ACTIVE"
        return user

    async def enable_user(self, user_uuid: str) -> RemnawaveUser:
        self.calls.append("enable_user")
        user = self.users_by_uuid[user_uuid]
        user.status = "ACTIVE"
        return user

    async def disable_user(self, user_uuid: str) -> RemnawaveUser:
        self.calls.append("disable_user")
        user = self.users_by_uuid[user_uuid]
        user.status = "DISABLED"
        return user

    async def delete_user(self, user_uuid: str) -> bool:
        self.calls.append("delete_user")
        return self.users_by_uuid.pop(user_uuid, None) is not None

    async def get_internal_squads(self) -> list[RemnawaveSquad]:
        return [RemnawaveSquad(uuid="squad-1", name="Default")]

    async def health_check(self) -> RemnawaveHealth:
        return RemnawaveHealth(healthy=True, status_code=200, latency_ms=12.5, detail="OK")


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        BOT_TOKEN=SecretStr("123456:TEST_BOT_TOKEN_SECRET"),
        BOT_MODE="polling",
        ADMIN_IDS=[999999],
        WEBHOOK_DOMAIN="https://bot.example.com",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://localhost:6379/0",
        REMNAWAVE_URL="http://remnawave:3000",
        REMNAWAVE_API_TOKEN=SecretStr("rw_test_token"),
        REMNAWAVE_DEFAULT_SQUADS=["squad-1"],
        TRIAL_ENABLED=True,
        TRIAL_DURATION_DAYS=3,
        TRIAL_TRAFFIC_GB=10,
        TRIAL_DEVICE_LIMIT=2,
        REFERRAL_ENABLED=True,
        REFERRAL_REWARD_TYPE="percent",
        REFERRAL_REWARD_PERCENT=15,
        REFERRAL_REWARD_DAYS=5,
        CRYPTOBOT_TOKEN=SecretStr("99999:CRYPTOBOT_TEST_TOKEN"),
        YOOKASSA_SHOP_ID="100500",
        YOOKASSA_SECRET_KEY=SecretStr("yk_test_secret"),
        YOOKASSA_TRUST_WEBHOOK_WITHOUT_API_CHECK=False,
    )


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database("sqlite+aiosqlite:///:memory:")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def fake_remnawave() -> FakeRemnawaveClient:
    return FakeRemnawaveClient()
