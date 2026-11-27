"""Isolated async client for Remnawave Panel REST API."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RemnawaveError(Exception):
    """Base exception for Remnawave integration."""


class RemnawaveAPIError(RemnawaveError):
    """HTTP or API-level error returned by Remnawave."""

    def __init__(self, status_code: int, message: str, detail: Any = None) -> None:
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(f"Remnawave API error {status_code}: {message}")


class RemnawaveNotFoundError(RemnawaveAPIError):
    """Raised when a Remnawave resource does not exist."""


class RemnawaveUser(BaseModel):
    """Normalized Remnawave user representation."""

    uuid: str
    username: str
    status: str = "ACTIVE"
    traffic_limit_bytes: int = Field(default=0, alias="trafficLimitBytes")
    traffic_limit_strategy: str = Field(default="NO_RESET", alias="trafficLimitStrategy")
    used_traffic_bytes: int = 0
    lifetime_used_traffic_bytes: int = 0
    expire_at: datetime = Field(alias="expireAt")
    hwid_device_limit: int = Field(default=0, alias="hwidDeviceLimit")
    telegram_id: int | None = Field(default=None, alias="telegramId")
    subscription_url: str = Field(default="", alias="subscriptionUrl")
    active_internal_squads: list[str] = Field(default_factory=list)
    short_uuid: str = Field(default="", alias="shortUuid")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @classmethod
    def from_api_payload(cls, data: dict[str, Any]) -> RemnawaveUser:
        """Parse Remnawave user JSON handling nested userTraffic and squad objects."""
        raw_traffic = data.get("userTraffic")
        if isinstance(raw_traffic, dict):
            used_bytes = int(raw_traffic.get("usedTrafficBytes") or 0)
            lifetime_bytes = int(raw_traffic.get("lifetimeUsedTrafficBytes") or 0)
        else:
            used_bytes = int(data.get("usedTrafficBytes") or data.get("trafficUsed") or 0)
            lifetime_bytes = int(data.get("lifetimeUsedTrafficBytes") or 0)

        raw_squads = data.get("activeInternalSquads") or []
        squad_uuids: list[str] = []
        for item in raw_squads:
            if isinstance(item, dict) and item.get("uuid"):
                squad_uuids.append(str(item["uuid"]))
            elif isinstance(item, str) and item:
                squad_uuids.append(item)

        raw_expire = data.get("expireAt")
        if isinstance(raw_expire, str):
            expire_dt = datetime.fromisoformat(raw_expire.replace("Z", "+00:00"))
        elif isinstance(raw_expire, datetime):
            expire_dt = raw_expire
        else:
            expire_dt = datetime.now(timezone.utc)
        if expire_dt.tzinfo is None:
            expire_dt = expire_dt.replace(tzinfo=timezone.utc)

        return cls(
            uuid=str(data.get("uuid") or data.get("id") or ""),
            username=str(data.get("username") or ""),
            status=str(data.get("status") or "ACTIVE").upper(),
            traffic_limit_bytes=int(data.get("trafficLimitBytes") or 0),
            traffic_limit_strategy=str(data.get("trafficLimitStrategy") or "NO_RESET"),
            used_traffic_bytes=used_bytes,
            lifetime_used_traffic_bytes=lifetime_bytes,
            expire_at=expire_dt,
            hwid_device_limit=int(data.get("hwidDeviceLimit") or 0),
            telegram_id=int(data["telegramId"]) if data.get("telegramId") is not None else None,
            subscription_url=str(data.get("subscriptionUrl") or ""),
            active_internal_squads=squad_uuids,
            short_uuid=str(data.get("shortUuid") or ""),
        )


class RemnawaveSquad(BaseModel):
    """Internal squad summary from Remnawave."""

    uuid: str
    name: str


class RemnawaveHealth(BaseModel):
    """Panel connectivity and health diagnostics."""

    healthy: bool
    status_code: int
    latency_ms: float
    version: str | None = None
    detail: str = ""


class RemnawaveClient:
    """Async REST client for Remnawave Panel API with retries and timeouts."""

    _RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout: float = 15.0,
        max_retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers=self._build_headers(),
        )

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def aclose(self) -> None:
        """Close underlying httpx.AsyncClient if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def format_utc_iso(dt: datetime) -> str:
        """Format a datetime strictly as UTC ISO-8601 string with Z suffix."""
        if dt.tzinfo is None:
            utc_dt = dt.replace(tzinfo=timezone.utc)
        else:
            utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    @staticmethod
    def make_username(telegram_id: int, suffix: str | None = None) -> str:
        """Generate a valid Remnawave username bound to a Telegram ID."""
        base = f"tg_{telegram_id}"
        if suffix:
            return f"{base}_{suffix}"[:36]
        return base[:36]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        url = path if self._client.base_url else f"{self.base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    headers=self._build_headers(),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue
                raise RemnawaveError(f"Network error contacting Remnawave: {exc}") from exc

            if response.status_code == 404 and allow_404:
                return None

            if response.status_code in self._RETRYABLE_STATUS_CODES:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue

            if response.status_code >= 400:
                detail: Any = None
                message = response.reason_phrase or "Request failed"
                try:
                    payload = response.json()
                    detail = payload
                    if isinstance(payload, dict):
                        message = str(payload.get("message") or payload.get("error") or message)
                except ValueError:
                    detail = response.text

                if response.status_code == 404:
                    raise RemnawaveNotFoundError(response.status_code, message, detail)
                raise RemnawaveAPIError(response.status_code, message, detail)

            if response.status_code == 204 or not response.content:
                return {}

            try:
                return response.json()
            except ValueError as exc:
                raise RemnawaveError(f"Invalid JSON from Remnawave: {exc}") from exc

        raise RemnawaveError(f"Remnawave request failed after retries: {last_exc}")

    @staticmethod
    def _unwrap_response(payload: Any) -> Any:
        if isinstance(payload, dict) and "response" in payload:
            return payload["response"]
        return payload

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
        """Create a new user in Remnawave bound to Telegram ID."""
        generated_uuid = user_uuid or str(uuid4())
        squads = [str(s) for s in (active_internal_squads or []) if str(s).strip()]

        payload: dict[str, Any] = {
            "uuid": generated_uuid,
            "username": self.make_username(telegram_id),
            "status": "ACTIVE",
            "trafficLimitBytes": max(0, int(traffic_limit_bytes)),
            "trafficLimitStrategy": traffic_limit_strategy or "NO_RESET",
            "expireAt": self.format_utc_iso(expire_at),
            "hwidDeviceLimit": max(0, int(hwid_device_limit)),
            "telegramId": int(telegram_id),
            "activeInternalSquads": squads,
        }
        if description:
            payload["description"] = description
        if tag:
            payload["tag"] = tag

        try:
            raw = await self._request("POST", "/api/users", json_body=payload)
        except RemnawaveAPIError as exc:
            # Handle rare username collision by retrying with a short random suffix
            if exc.status_code == 409:
                payload["username"] = self.make_username(telegram_id, uuid4().hex[:4])
                raw = await self._request("POST", "/api/users", json_body=payload)
            else:
                raise

        data = self._unwrap_response(raw)
        if not isinstance(data, dict):
            raise RemnawaveError("Unexpected response format on user creation")
        return RemnawaveUser.from_api_payload(data)

    async def get_user_by_telegram_id(self, telegram_id: int) -> RemnawaveUser | None:
        """Fetch user from Remnawave by Telegram ID; returns None if not found (404)."""
        raw = await self._request(
            "GET",
            f"/api/users/by-telegram-id/{int(telegram_id)}",
            allow_404=True,
        )
        if raw is None:
            return None

        data = self._unwrap_response(raw)
        if data is None:
            return None
        if isinstance(data, list):
            if not data:
                return None
            users = [RemnawaveUser.from_api_payload(item) for item in data if isinstance(item, dict)]
            if not users:
                return None
            for u in users:
                if u.status == "ACTIVE":
                    return u
            return users[0]
        if isinstance(data, dict):
            return RemnawaveUser.from_api_payload(data)
        return None

    async def get_user_by_uuid(self, user_uuid: str) -> RemnawaveUser | None:
        """Fetch user from Remnawave by UUID; returns None if not found (404)."""
        raw = await self._request(
            "GET",
            f"/api/users/by-uuid/{user_uuid}",
            allow_404=True,
        )
        if raw is None:
            return None
        data = self._unwrap_response(raw)
        if not isinstance(data, dict):
            return None
        return RemnawaveUser.from_api_payload(data)

    async def get_subscription_url(self, user_uuid: str) -> str | None:
        """Return subscriptionUrl for a Remnawave user UUID."""
        user = await self.get_user_by_uuid(user_uuid)
        return user.subscription_url if user else None

    async def get_users_paginated(
        self,
        *,
        start: int = 0,
        size: int = 100,
    ) -> tuple[list[RemnawaveUser], int]:
        """Fetch a single paginated slice of Remnawave users."""
        raw = await self._request(
            "GET",
            "/api/users",
            params={"start": max(0, start), "size": max(1, size)},
        )
        data = self._unwrap_response(raw)
        if not isinstance(data, dict):
            return [], 0

        raw_users = data.get("users")
        if raw_users is None and isinstance(data.get("items"), list):
            raw_users = data["items"]
        raw_users = raw_users or []

        users = [RemnawaveUser.from_api_payload(item) for item in raw_users if isinstance(item, dict)]
        total = int(data.get("total") if data.get("total") is not None else len(users))
        return users, total

    async def iter_all_users(self, batch_size: int = 100) -> list[RemnawaveUser]:
        """Fetch all users from Remnawave using paginated GET /api/users batches."""
        collected: list[RemnawaveUser] = []
        start = 0
        while True:
            page, total = await self.get_users_paginated(start=start, size=batch_size)
            if not page:
                break
            collected.extend(page)
            start += len(page)
            if start >= total or len(page) < batch_size:
                break
        return collected

    async def update_user(self, user_uuid: str, **fields: Any) -> RemnawaveUser:
        """Update user attributes in Remnawave via PATCH /api/users."""
        body: dict[str, Any] = {"uuid": user_uuid}
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, datetime):
                body[key] = self.format_utc_iso(value)
            else:
                body[key] = value

        raw = await self._request("PATCH", "/api/users", json_body=body)
        data = self._unwrap_response(raw)
        if not isinstance(data, dict):
            raise RemnawaveError("Unexpected response format on user update")
        return RemnawaveUser.from_api_payload(data)

    async def extend_subscription(
        self,
        user_uuid: str,
        days: int,
        *,
        reset_traffic: bool = False,
    ) -> RemnawaveUser:
        """Extend user expiration by given days from max(current_expire, now_utc)."""
        current = await self.get_user_by_uuid(user_uuid)
        if current is None:
            raise RemnawaveNotFoundError(404, f"User {user_uuid} not found in Remnawave")

        now = datetime.now(timezone.utc)
        base = current.expire_at if current.expire_at > now else now
        new_expire = base + timedelta(days=days)

        updated = await self.update_user(
            user_uuid,
            expireAt=new_expire,
            status="ACTIVE",
        )
        if reset_traffic:
            updated = await self.reset_traffic(user_uuid)
        return updated

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
        """Apply new tariff bounds and squads to an existing Remnawave user."""
        updated = await self.update_user(
            user_uuid,
            status="ACTIVE",
            expireAt=expire_at,
            trafficLimitBytes=max(0, int(traffic_limit_bytes)),
            trafficLimitStrategy=traffic_limit_strategy or "NO_RESET",
            hwidDeviceLimit=max(0, int(hwid_device_limit)),
            activeInternalSquads=[str(s) for s in active_internal_squads if str(s).strip()],
        )
        if reset_traffic:
            updated = await self.reset_traffic(user_uuid)
        return updated

    async def reset_traffic(self, user_uuid: str) -> RemnawaveUser:
        """Reset consumed traffic counter for a user."""
        raw = await self._request("POST", f"/api/users/{user_uuid}/actions/reset-traffic")
        data = self._unwrap_response(raw)
        if isinstance(data, dict) and data.get("uuid"):
            return RemnawaveUser.from_api_payload(data)
        user = await self.get_user_by_uuid(user_uuid)
        if user is None:
            raise RemnawaveNotFoundError(404, f"User {user_uuid} not found after reset-traffic")
        return user

    async def enable_user(self, user_uuid: str) -> RemnawaveUser:
        """Enable user access across Remnawave nodes."""
        raw = await self._request("POST", f"/api/users/{user_uuid}/actions/enable")
        data = self._unwrap_response(raw)
        if isinstance(data, dict) and data.get("uuid"):
            return RemnawaveUser.from_api_payload(data)
        user = await self.get_user_by_uuid(user_uuid)
        if user is None:
            raise RemnawaveNotFoundError(404, f"User {user_uuid} not found after enable")
        return user

    async def disable_user(self, user_uuid: str) -> RemnawaveUser:
        """Disable user access across Remnawave nodes."""
        raw = await self._request("POST", f"/api/users/{user_uuid}/actions/disable")
        data = self._unwrap_response(raw)
        if isinstance(data, dict) and data.get("uuid"):
            return RemnawaveUser.from_api_payload(data)
        user = await self.get_user_by_uuid(user_uuid)
        if user is None:
            raise RemnawaveNotFoundError(404, f"User {user_uuid} not found after disable")
        return user

    async def delete_user(self, user_uuid: str) -> bool:
        """Delete user from Remnawave panel."""
        raw = await self._request("DELETE", f"/api/users/{user_uuid}", allow_404=True)
        if raw is None:
            return False
        data = self._unwrap_response(raw)
        if isinstance(data, dict) and "isDeleted" in data:
            return bool(data["isDeleted"])
        return True

    async def get_internal_squads(self) -> list[RemnawaveSquad]:
        """Fetch list of configured internal squads from Remnawave."""
        raw = await self._request("GET", "/api/internal-squads")
        data = self._unwrap_response(raw)
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [i for i in data if isinstance(i, dict)]
        elif isinstance(data, dict):
            squads = data.get("internalSquads") or data.get("squads") or data.get("items") or []
            items = [i for i in squads if isinstance(i, dict)]

        return [
            RemnawaveSquad(uuid=str(item["uuid"]), name=str(item.get("name") or item["uuid"]))
            for item in items
            if item.get("uuid")
        ]

    async def health_check(self) -> RemnawaveHealth:
        """Verify Remnawave panel availability and measure response latency."""
        start = time.monotonic()
        try:
            raw = await self._request("GET", "/api/system/health", allow_404=True)
            if raw is None:
                raw = await self._request("GET", "/api/system/stats", allow_404=True)
            if raw is None:
                # Fallback lightweight authenticated check
                await self.get_users_paginated(start=0, size=1)
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                return RemnawaveHealth(
                    healthy=True,
                    status_code=200,
                    latency_ms=latency_ms,
                    detail="OK (users API)",
                )

            latency_ms = round((time.monotonic() - start) * 1000, 1)
            data = self._unwrap_response(raw)
            version = None
            if isinstance(data, dict):
                version = data.get("version") or data.get("panelVersion")
            return RemnawaveHealth(
                healthy=True,
                status_code=200,
                latency_ms=latency_ms,
                version=str(version) if version else None,
                detail="OK",
            )
        except RemnawaveAPIError as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return RemnawaveHealth(
                healthy=False,
                status_code=exc.status_code,
                latency_ms=latency_ms,
                detail=exc.message,
            )
        except RemnawaveError as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return RemnawaveHealth(
                healthy=False,
                status_code=0,
                latency_ms=latency_ms,
                detail=str(exc),
            )
