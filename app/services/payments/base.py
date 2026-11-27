"""Abstract base class and DTOs for all payment providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.db.models import PaymentProviderType


class PaymentError(Exception):
    """Base exception for payment creation or fulfillment."""


class PaymentWebhookError(PaymentError):
    """Raised when incoming webhook signature or payload is invalid."""


class InsufficientBalanceError(PaymentError):
    """Raised when user balance is insufficient for purchase."""


@dataclass(slots=True)
class PaymentCreationRequest:
    """Parameters passed to a payment provider to initiate a payment."""

    payment_id: str
    user_telegram_id: int
    amount_rub: Decimal
    description: str
    stars_amount: int | None = None
    return_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PaymentCreationResult:
    """Normalized result returned after creating an invoice or payment."""

    provider: PaymentProviderType
    payment_id: str
    amount: Decimal
    currency: str
    external_id: str | None = None
    payment_url: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookPaymentEvent:
    """Normalized payment event parsed and verified from a webhook."""

    provider: PaymentProviderType
    payment_id: str
    external_id: str | None
    status: str  # "succeeded", "canceled", "pending"
    amount: Decimal
    currency: str
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)


class BasePaymentProvider(ABC):
    """Abstract interface implemented by all payment providers."""

    provider_type: PaymentProviderType

    @abstractmethod
    async def create_payment(self, request: PaymentCreationRequest) -> PaymentCreationResult:
        """Create a payment or invoice in the provider."""

    @abstractmethod
    async def verify_and_parse_webhook(
        self,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookPaymentEvent | None:
        """Verify authenticity of an incoming webhook and extract payment event."""
