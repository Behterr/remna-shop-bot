"""Subscription lifecycle, trial issuance, and Remnawave synchronization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Subscription, SubscriptionStatus, Tariff, User
from app.services.remnawave import RemnawaveClient, RemnawaveUser
from app.services.settings_service import SettingsService

GIB = 1024 * 1024 * 1024


class SubscriptionError(Exception):
    """Domain error for subscription operations."""


class TrialAlreadyUsedError(SubscriptionError):
    """Raised when a user tries to activate a trial more than once."""


class TrialDisabledError(SubscriptionError):
    """Raised when trial issuance is disabled in settings."""


def _map_status(raw_status: str, expires_at: datetime, now: datetime) -> SubscriptionStatus:
    upper = (raw_status or "").upper()
    if upper == "DISABLED":
        return SubscriptionStatus.DISABLED
    if upper == "LIMITED":
        return SubscriptionStatus.LIMITED
    if upper == "EXPIRED" or expires_at <= now:
        return SubscriptionStatus.EXPIRED
    return SubscriptionStatus.ACTIVE


async def _lock_user(session: AsyncSession, user_id: int) -> User:
    stmt = select(User).where(User.id == user_id)
    if session.bind and session.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise SubscriptionError(f"User id={user_id} not found")
    return user


async def _get_user_subscription(session: AsyncSession, user_id: int) -> Subscription | None:
    stmt = select(Subscription).where(Subscription.user_id == user_id)
    if session.bind and session.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _sync_subscription_fields(
    sub: Subscription,
    rw_user: RemnawaveUser,
    *,
    tariff_id: int | None,
    is_trial: bool,
    now: datetime,
) -> None:
    sub.tariff_id = tariff_id
    sub.remnawave_uuid = rw_user.uuid
    sub.subscription_url = rw_user.subscription_url
    sub.status = _map_status(rw_user.status, rw_user.expire_at, now)
    sub.is_trial = is_trial
    sub.expires_at = rw_user.expire_at
    sub.traffic_limit_bytes = rw_user.traffic_limit_bytes
    sub.used_traffic_bytes = rw_user.used_traffic_bytes
    sub.device_limit = rw_user.hwid_device_limit


class SubscriptionService:
    """Coordinates VPN subscription state across PostgreSQL and Remnawave."""

    @staticmethod
    async def activate_trial(
        session: AsyncSession,
        user: User,
        remnawave: RemnawaveClient,
        settings: Settings,
    ) -> Subscription:
        """Issue a one-time trial subscription for a user."""
        locked_user = await _lock_user(session, user.id)
        trial_cfg = await SettingsService.get_trial_config(session, settings)
        if not trial_cfg.enabled:
            raise TrialDisabledError("Пробный период временно отключён.")
        if locked_user.trial_used:
            raise TrialAlreadyUsedError("Пробный период уже был использован.")

        existing_sub = await _get_user_subscription(session, locked_user.id)
        if existing_sub is not None and existing_sub.status == SubscriptionStatus.ACTIVE:
            raise TrialAlreadyUsedError("У вас уже есть активная подписка.")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=trial_cfg.duration_days)
        traffic_bytes = trial_cfg.traffic_gb * GIB if trial_cfg.traffic_gb > 0 else 0
        squads = list(settings.REMNAWAVE_DEFAULT_SQUADS)

        rw_existing: RemnawaveUser | None = None
        if locked_user.remnawave_uuid:
            rw_existing = await remnawave.get_user_by_uuid(locked_user.remnawave_uuid)
        if rw_existing is None:
            rw_existing = await remnawave.get_user_by_telegram_id(locked_user.telegram_id)

        if rw_existing is not None:
            rw_user = await remnawave.change_tariff(
                rw_existing.uuid,
                expire_at=expires_at,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=trial_cfg.device_limit,
                active_internal_squads=squads,
                traffic_limit_strategy=settings.REMNAWAVE_TRAFFIC_STRATEGY,
                reset_traffic=True,
            )
        else:
            rw_user = await remnawave.create_user(
                telegram_id=locked_user.telegram_id,
                expire_at=expires_at,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=trial_cfg.device_limit,
                active_internal_squads=squads,
                traffic_limit_strategy=settings.REMNAWAVE_TRAFFIC_STRATEGY,
                description=f"Trial user @{locked_user.username or locked_user.telegram_id}",
            )

        locked_user.trial_used = True
        locked_user.remnawave_uuid = rw_user.uuid

        if existing_sub is None:
            sub = Subscription(
                user_id=locked_user.id,
                tariff_id=None,
                remnawave_uuid=rw_user.uuid,
                subscription_url=rw_user.subscription_url,
                status=_map_status(rw_user.status, rw_user.expire_at, now),
                is_trial=True,
                started_at=now,
                expires_at=rw_user.expire_at,
                traffic_limit_bytes=rw_user.traffic_limit_bytes,
                used_traffic_bytes=rw_user.used_traffic_bytes,
                device_limit=rw_user.hwid_device_limit,
            )
            session.add(sub)
        else:
            sub = existing_sub
            sub.started_at = now
            _sync_subscription_fields(sub, rw_user, tariff_id=None, is_trial=True, now=now)

        sub.reset_expiry_notifications()
        sub.reset_traffic_notifications()
        await session.flush()
        return sub

    @staticmethod
    async def apply_tariff_purchase(
        session: AsyncSession,
        user: User,
        tariff: Tariff,
        remnawave: RemnawaveClient,
        settings: Settings,
        *,
        reset_traffic: bool = True,
    ) -> Subscription:
        """Create or extend/upgrade subscription for a purchased tariff."""
        locked_user = await _lock_user(session, user.id)
        existing_sub = await _get_user_subscription(session, locked_user.id)

        now = datetime.now(timezone.utc)
        traffic_bytes = tariff.traffic_limit_gb * GIB if tariff.traffic_limit_gb > 0 else 0
        squads = list(tariff.squad_uuids) if tariff.squad_uuids else list(settings.REMNAWAVE_DEFAULT_SQUADS)
        strategy = tariff.traffic_limit_strategy or settings.REMNAWAVE_TRAFFIC_STRATEGY

        rw_existing: RemnawaveUser | None = None
        target_uuid = (
            existing_sub.remnawave_uuid
            if existing_sub
            else locked_user.remnawave_uuid
        )
        if target_uuid:
            rw_existing = await remnawave.get_user_by_uuid(target_uuid)
        if rw_existing is None:
            rw_existing = await remnawave.get_user_by_telegram_id(locked_user.telegram_id)

        if rw_existing is not None:
            current_expire = rw_existing.expire_at
            if existing_sub and existing_sub.expires_at:
                exp = existing_sub.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp > current_expire:
                    current_expire = exp

            # When upgrading from trial, start fresh from now + tariff duration or extend from current
            base_dt = current_expire if current_expire > now else now
            new_expire = base_dt + timedelta(days=tariff.duration_days)

            rw_user = await remnawave.change_tariff(
                rw_existing.uuid,
                expire_at=new_expire,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=tariff.device_limit,
                active_internal_squads=squads,
                traffic_limit_strategy=strategy,
                reset_traffic=reset_traffic,
            )
        else:
            new_expire = now + timedelta(days=tariff.duration_days)
            rw_user = await remnawave.create_user(
                telegram_id=locked_user.telegram_id,
                expire_at=new_expire,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=tariff.device_limit,
                active_internal_squads=squads,
                traffic_limit_strategy=strategy,
                description=f"User @{locked_user.username or locked_user.telegram_id}",
            )

        locked_user.remnawave_uuid = rw_user.uuid

        if existing_sub is None:
            sub = Subscription(
                user_id=locked_user.id,
                tariff_id=tariff.id,
                remnawave_uuid=rw_user.uuid,
                subscription_url=rw_user.subscription_url,
                status=_map_status(rw_user.status, rw_user.expire_at, now),
                is_trial=False,
                started_at=now,
                expires_at=rw_user.expire_at,
                traffic_limit_bytes=rw_user.traffic_limit_bytes,
                used_traffic_bytes=rw_user.used_traffic_bytes,
                device_limit=rw_user.hwid_device_limit,
            )
            session.add(sub)
        else:
            sub = existing_sub
            if sub.expires_at:
                sub_exp = (
                    sub.expires_at
                    if sub.expires_at.tzinfo is not None
                    else sub.expires_at.replace(tzinfo=timezone.utc)
                )
                if sub_exp <= now:
                    sub.started_at = now
            _sync_subscription_fields(sub, rw_user, tariff_id=tariff.id, is_trial=False, now=now)

        # Reset both expiry and traffic notification flags on renewal/tariff change
        sub.reset_expiry_notifications()
        sub.reset_traffic_notifications()
        await session.flush()
        return sub

    @staticmethod
    async def grant_bonus_days(
        session: AsyncSession,
        user: User,
        days: int,
        remnawave: RemnawaveClient,
        settings: Settings,
    ) -> Subscription:
        """Extend existing subscription by N days or create a new subscription with default bounds."""
        if days <= 0:
            raise SubscriptionError("Количество бонусных дней должно быть больше нуля.")

        locked_user = await _lock_user(session, user.id)
        existing_sub = await _get_user_subscription(session, locked_user.id)
        now = datetime.now(timezone.utc)

        rw_existing: RemnawaveUser | None = None
        target_uuid = (
            existing_sub.remnawave_uuid
            if existing_sub
            else locked_user.remnawave_uuid
        )
        if target_uuid:
            rw_existing = await remnawave.get_user_by_uuid(target_uuid)
        if rw_existing is None:
            rw_existing = await remnawave.get_user_by_telegram_id(locked_user.telegram_id)

        if rw_existing is not None:
            rw_user = await remnawave.extend_subscription(
                rw_existing.uuid,
                days=days,
                reset_traffic=False,
            )
        else:
            trial_cfg = await SettingsService.get_trial_config(session, settings)
            traffic_bytes = trial_cfg.traffic_gb * GIB if trial_cfg.traffic_gb > 0 else 0
            rw_user = await remnawave.create_user(
                telegram_id=locked_user.telegram_id,
                expire_at=now + timedelta(days=days),
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=trial_cfg.device_limit,
                active_internal_squads=list(settings.REMNAWAVE_DEFAULT_SQUADS),
                traffic_limit_strategy=settings.REMNAWAVE_TRAFFIC_STRATEGY,
                description=f"Bonus days @{locked_user.username or locked_user.telegram_id}",
            )

        locked_user.remnawave_uuid = rw_user.uuid

        if existing_sub is None:
            sub = Subscription(
                user_id=locked_user.id,
                tariff_id=None,
                remnawave_uuid=rw_user.uuid,
                subscription_url=rw_user.subscription_url,
                status=_map_status(rw_user.status, rw_user.expire_at, now),
                is_trial=False,
                started_at=now,
                expires_at=rw_user.expire_at,
                traffic_limit_bytes=rw_user.traffic_limit_bytes,
                used_traffic_bytes=rw_user.used_traffic_bytes,
                device_limit=rw_user.hwid_device_limit,
            )
            session.add(sub)
        else:
            sub = existing_sub
            _sync_subscription_fields(
                sub,
                rw_user,
                tariff_id=existing_sub.tariff_id,
                is_trial=existing_sub.is_trial,
                now=now,
            )

        sub.reset_expiry_notifications()
        await session.flush()
        return sub

    @staticmethod
    async def reset_user_traffic(
        session: AsyncSession,
        user: User,
        remnawave: RemnawaveClient,
    ) -> Subscription:
        """Reset user's consumed traffic on Remnawave and clear traffic warning flags."""
        sub = await _get_user_subscription(session, user.id)
        if sub is None:
            raise SubscriptionError("У пользователя нет активной подписки.")

        rw_user = await remnawave.reset_traffic(sub.remnawave_uuid)
        sub.used_traffic_bytes = rw_user.used_traffic_bytes
        if sub.status == SubscriptionStatus.LIMITED:
            sub.status = SubscriptionStatus.ACTIVE
        sub.reset_traffic_notifications()
        await session.flush()
        return sub

    @staticmethod
    async def revoke_subscription(
        session: AsyncSession,
        user: User,
        remnawave: RemnawaveClient,
    ) -> Subscription | None:
        """Disable a user's subscription on Remnawave and in DB."""
        sub = await _get_user_subscription(session, user.id)
        if sub is None:
            return None
        await remnawave.disable_user(sub.remnawave_uuid)
        sub.status = SubscriptionStatus.DISABLED
        await session.flush()
        return sub

    @staticmethod
    async def enable_subscription(
        session: AsyncSession,
        user: User,
        remnawave: RemnawaveClient,
    ) -> Subscription | None:
        """Re-enable a disabled subscription on Remnawave and in DB."""
        sub = await _get_user_subscription(session, user.id)
        if sub is None:
            return None
        rw_user = await remnawave.enable_user(sub.remnawave_uuid)
        now = datetime.now(timezone.utc)
        sub.status = _map_status(rw_user.status, rw_user.expire_at, now)
        await session.flush()
        return sub

    @staticmethod
    async def sync_single_subscription(
        session: AsyncSession,
        sub: Subscription,
        remnawave: RemnawaveClient,
    ) -> Subscription:
        """Refresh a single subscription state from Remnawave."""
        rw_user = await remnawave.get_user_by_uuid(sub.remnawave_uuid)
        if rw_user is None:
            return sub
        now = datetime.now(timezone.utc)
        _sync_subscription_fields(
            sub,
            rw_user,
            tariff_id=sub.tariff_id,
            is_trial=sub.is_trial,
            now=now,
        )
        await session.flush()
        return sub
