"""Financial transaction and payment idempotency ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PortableJSON

if TYPE_CHECKING:
    from app.db.models.user import User


class PaymentProviderType(StrEnum):
    """Payment or adjustment source."""

    BALANCE = "BALANCE"
    TELEGRAM_STARS = "TELEGRAM_STARS"
    CRYPTOBOT = "CRYPTOBOT"
    YOOKASSA = "YOOKASSA"
    ADMIN = "ADMIN"
    REFERRAL = "REFERRAL"
    PROMO = "PROMO"


class TransactionType(StrEnum):
    """Type of financial or subscription operation."""

    DEPOSIT = "DEPOSIT"
    SUBSCRIPTION_PURCHASE = "SUBSCRIPTION_PURCHASE"
    SUBSCRIPTION_RENEWAL = "SUBSCRIPTION_RENEWAL"
    AUTO_RENEWAL = "AUTO_RENEWAL"
    REFERRAL_BONUS = "REFERRAL_BONUS"
    PROMO_BONUS = "PROMO_BONUS"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"


class TransactionStatus(StrEnum):
    """Transaction lifecycle status."""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class Transaction(Base):
    """Ledger entry with unique payment_id for webhook & checkout idempotency."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    payment_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)

    provider: Mapped[PaymentProviderType] = mapped_column(
        SAEnum(PaymentProviderType, native_enum=False, length=32),
        index=True,
        nullable=False,
    )
    tx_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, native_enum=False, length=32),
        index=True,
        nullable=False,
    )
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, native_enum=False, length=32),
        default=TransactionStatus.PENDING,
        index=True,
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="RUB", nullable=False)

    tariff_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="SET NULL"),
        nullable=True,
    )
    promo_code_id: Mapped[int | None] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="transactions")
