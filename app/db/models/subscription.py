"""VPN Subscription ORM model."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.tariff import Tariff
    from app.db.models.user import User


class SubscriptionStatus(StrEnum):
    """Subscription lifecycle status mirrored with Remnawave."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    DISABLED = "DISABLED"
    LIMITED = "LIMITED"


class Subscription(TimestampMixin, Base):
    """User's active or historical VPN subscription synced with Remnawave."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    tariff_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    remnawave_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    subscription_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(SubscriptionStatus, native_enum=False, length=32),
        default=SubscriptionStatus.ACTIVE,
        index=True,
        nullable=False,
    )
    is_trial: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)

    traffic_limit_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
        nullable=False,
    )
    used_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default="0",
        nullable=False,
    )
    device_limit: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    notified_3d: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    notified_1d: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    notified_expired: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    notified_traffic_85: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    notified_traffic_100: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="subscription")
    tariff: Mapped[Tariff | None] = relationship("Tariff")

    def reset_expiry_notifications(self) -> None:
        """Reset expiration notification flags upon any subscription extension."""
        self.notified_3d = False
        self.notified_1d = False
        self.notified_expired = False

    def reset_traffic_notifications(self) -> None:
        """Reset traffic threshold notification flags upon reset or tariff update."""
        self.notified_traffic_85 = False
        self.notified_traffic_100 = False
