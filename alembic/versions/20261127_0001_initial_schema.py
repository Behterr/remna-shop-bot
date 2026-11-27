"""Initial database schema.

Revision ID: 20261127_0001
Revises:
Create Date: 2026-11-27 20:52:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261127_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

portable_json = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("promo_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_promo_codes_code"),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=True)
    op.create_index("ix_promo_codes_is_active", "promo_codes", ["is_active"])

    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("price_rub", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("price_stars", sa.Integer(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("traffic_limit_gb", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "traffic_limit_strategy",
            sa.String(length=32),
            server_default="NO_RESET",
            nullable=False,
        ),
        sa.Column("device_limit", sa.Integer(), server_default="0", nullable=False),
        sa.Column("squad_uuids", portable_json, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tariffs_is_active", "tariffs", ["is_active"])
    op.create_index("ix_tariffs_sort_order", "tariffs", ["sort_order"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column(
            "balance",
            sa.Numeric(precision=12, scale=2),
            server_default="0.00",
            nullable=False,
        ),
        sa.Column("is_banned", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("trial_used", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("auto_renew", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "referred_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pending_promo_id",
            sa.Integer(),
            sa.ForeignKey("promo_codes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("remnawave_uuid", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_referred_by_id", "users", ["referred_by_id"])
    op.create_index("ix_users_remnawave_uuid", "users", ["remnawave_uuid"], unique=True)

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tariff_id",
            sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("remnawave_uuid", sa.String(length=64), nullable=False),
        sa.Column("subscription_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("is_trial", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("traffic_limit_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("used_traffic_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("device_limit", sa.Integer(), server_default="0", nullable=False),
        sa.Column("notified_3d", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notified_1d", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notified_expired", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notified_traffic_85", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notified_traffic_100", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=True)
    op.create_index("ix_subscriptions_tariff_id", "subscriptions", ["tariff_id"])
    op.create_index(
        "ix_subscriptions_remnawave_uuid",
        "subscriptions",
        ["remnawave_uuid"],
        unique=True,
    )
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])
    op.create_index("ix_subscriptions_expires_at", "subscriptions", ["expires_at"])

    op.create_table(
        "promo_activations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "promo_code_id",
            sa.Integer(),
            sa.ForeignKey("promo_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_activation_user"),
    )
    op.create_index("ix_promo_activations_promo_code_id", "promo_activations", ["promo_code_id"])
    op.create_index("ix_promo_activations_user_id", "promo_activations", ["user_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("tx_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False, server_default="RUB"),
        sa.Column(
            "tariff_id",
            sa.Integer(),
            sa.ForeignKey("tariffs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "promo_code_id",
            sa.Integer(),
            sa.ForeignKey("promo_codes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("metadata_json", portable_json, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_payment_id", "transactions", ["payment_id"], unique=True)
    op.create_index("ix_transactions_external_id", "transactions", ["external_id"])
    op.create_index("ix_transactions_provider", "transactions", ["provider"])
    op.create_index("ix_transactions_tx_type", "transactions", ["tx_type"])
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("promo_activations")
    op.drop_table("subscriptions")
    op.drop_table("users")
    op.drop_table("tariffs")
    op.drop_table("promo_codes")
    op.drop_table("app_settings")
