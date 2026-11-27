"""Promo code and activation tracking ORM models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class PromoType(StrEnum):
    """Supported promo code reward modes."""

    DISCOUNT_PERCENT = "DISCOUNT_PERCENT"
    BALANCE_BONUS = "BALANCE_BONUS"
    BONUS_DAYS = "BONUS_DAYS"


class PromoCode(TimestampMixin, Base):
    """Promotional code for discounts, balance top-up, or bonus days."""

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    promo_type: Mapped[PromoType] = mapped_column(
        SAEnum(PromoType, native_enum=False, length=32),
        nullable=False,
    )
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        index=True,
        nullable=False,
    )

    activations: Mapped[list[PromoActivation]] = relationship(
        "PromoActivation",
        back_populates="promo_code",
        cascade="all, delete-orphan",
    )


class PromoActivation(Base):
    """Tracks single-use promo code activations per user."""

    __tablename__ = "promo_activations"
    __table_args__ = (
        UniqueConstraint("promo_code_id", "user_id", name="uq_promo_activation_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    promo_code_id: Mapped[int] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    promo_code: Mapped[PromoCode] = relationship("PromoCode", back_populates="activations")
