"""Formatting, localization, and safe deep-link signing utilities."""

from __future__ import annotations

import hashlib
import hmac
import html
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

CONNECT_APP_SCHEMES: dict[str, tuple[str, str]] = {
    "happ": ("Happ", "happ://add/{url}"),
    "v2raytun": ("v2rayTUN", "v2raytun://import/{url}"),
    "hiddify": ("Hiddify", "hiddify://import/{url}"),
    "streisand": ("Streisand", "streisand://import/{url}"),
}


def escape_html(text: str | None) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return html.escape(text or "", quote=False)


def format_bytes(size_bytes: int) -> str:
    """Format byte count into human-readable Russian units."""
    if size_bytes <= 0:
        return "0 Б"
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    value = float(size_bytes)
    unit_idx = 0
    while value >= 1024.0 and unit_idx < len(units) - 1:
        value /= 1024.0
        unit_idx += 1
    if unit_idx == 0:
        return f"{int(value)} {units[unit_idx]}"
    return f"{value:.2f} {units[unit_idx]}"


def format_traffic_limit(limit_bytes: int) -> str:
    """Format traffic limit or unlimited label."""
    if limit_bytes <= 0:
        return "Безлимит"
    return format_bytes(limit_bytes)


def format_device_limit(limit: int) -> str:
    """Format HWID device limit."""
    if limit <= 0:
        return "Безлимит"
    return str(limit)


def format_money(amount: Decimal | float | int, currency: str = "₽") -> str:
    """Format decimal monetary amount cleanly."""
    dec = Decimal(str(amount)).quantize(Decimal("0.01"))
    if dec == dec.to_integral_value():
        return f"{int(dec)} {currency}"
    return f"{dec:.2f} {currency}"


def pluralize_days(days: int) -> str:
    """Return Russian plural form for days."""
    n = abs(days) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return f"{days} дней"
    if n1 == 1:
        return f"{days} день"
    if 2 <= n1 <= 4:
        return f"{days} дня"
    return f"{days} дней"


def format_dt_utc(dt: datetime | None) -> str:
    """Format datetime into readable DD.MM.YYYY HH:MM UTC string."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%d.%m.%Y %H:%M UTC")


def remaining_days(expires_at: datetime | None, now: datetime | None = None) -> int:
    """Calculate remaining whole days until expiration (minimum 0)."""
    if expires_at is None:
        return 0
    current = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    delta = expires_at - current
    if delta.total_seconds() <= 0:
        return 0
    return max(1, int((delta.total_seconds() + 86399) // 86400))


def sign_connect_url(secret: str, app_slug: str, sub_url: str) -> str:
    """Compute HMAC signature for /connect/{app} redirect protection."""
    payload = f"{app_slug}:{sub_url}".encode("utf-8")
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:24]


def verify_connect_signature(secret: str, app_slug: str, sub_url: str, sig: str) -> bool:
    """Verify HMAC signature for /connect/{app} redirect."""
    expected = sign_connect_url(secret, app_slug, sub_url)
    return hmac.compare_digest(expected, sig)


def build_connect_redirect_url(
    webhook_domain: str,
    secret: str,
    app_slug: str,
    subscription_url: str,
) -> str | None:
    """Build an HTTPS redirect URL accepted by Telegram Bot API url buttons."""
    base = webhook_domain.strip().rstrip("/")
    if not base or not base.startswith("https://") or not subscription_url:
        return None
    if app_slug not in CONNECT_APP_SCHEMES:
        return None
    sig = sign_connect_url(secret, app_slug, subscription_url)
    encoded_url = quote(subscription_url, safe="")
    return f"{base}/connect/{app_slug}?url={encoded_url}&sig={sig}"
