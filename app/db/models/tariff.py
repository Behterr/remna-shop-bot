"""Tariff plan ORM model."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PortableJSON, TimestampMixin


class Tariff(TimestampMixin, Base):
    """VPN subscription tariff plan stored in PostgreSQL."""

    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    price_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    traffic_limit_gb: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    traffic_limit_strategy: Mapped[str] = mapped_column(
        String(32),
        default="NO_RESET",
        server_default="NO_RESET",
        nullable=False,
    )
    device_limit: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    squad_uuids: Mapped[list[str]] = mapped_column(
        PortableJSON,
        default=list,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        index=True,
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        index=True,
        nullable=False,
    )
