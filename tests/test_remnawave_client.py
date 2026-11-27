"""Unit tests for RemnawaveClient HTTP behavior, pagination, 404 handling, and retries."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.services.remnawave import RemnawaveClient


def _make_rw_payload(uid: str, tg_id: int, used: int = 1024) -> dict:
    return {
        "uuid": uid,
        "username": f"tg_{tg_id}",
        "status": "ACTIVE",
        "trafficLimitBytes": 10737418240,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": "2026-12-31T23:59:59.000Z",
        "hwidDeviceLimit": 2,
        "telegramId": tg_id,
        "subscriptionUrl": f"https://sub.example.com/{uid}",
        "activeInternalSquads": [{"uuid": "squad-1", "name": "EU"}],
        "userTraffic": {"usedTrafficBytes": used, "lifetimeUsedTrafficBytes": used},
    }


@pytest.mark.asyncio
async def test_remnawave_create_user_and_404_lookup() -> None:
    captured_requests: list[tuple[str, str, dict | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        captured_requests.append(
            (request.method, str(request.url), body, request.headers.get("authorization"))
        )
        if request.method == "POST" and request.url.path == "/api/users":
            return httpx.Response(201, json={"response": _make_rw_payload(body["uuid"], body["telegramId"])})
        if request.method == "GET" and request.url.path == "/api/users/by-telegram-id/404040":
            return httpx.Response(404, json={"message": "User not found"})
        if request.method == "GET" and request.url.path == "/api/users/by-telegram-id/12345":
            return httpx.Response(200, json={"response": [_make_rw_payload("u-1", 12345)]})
        return httpx.Response(500, json={"message": "unexpected"})

    async with httpx.AsyncClient(
        base_url="http://remnawave:3000",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = RemnawaveClient("http://remnawave:3000", "secret-tok", http_client=http)
        expire_dt = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        created = await client.create_user(
            telegram_id=12345,
            expire_at=expire_dt,
            traffic_limit_bytes=10737418240,
            hwid_device_limit=2,
            active_internal_squads=["squad-1"],
        )
        assert created.username == "tg_12345"
        assert created.subscription_url.startswith("https://sub.example.com/")

        post_req = captured_requests[0]
        assert post_req[3] == "Bearer secret-tok"
        assert post_req[2]["username"] == "tg_12345"
        assert post_req[2]["trafficLimitStrategy"] == "NO_RESET"
        assert post_req[2]["expireAt"].endswith("Z")

        missing = await client.get_user_by_telegram_id(404040)
        assert missing is None

        found = await client.get_user_by_telegram_id(12345)
        assert found is not None
        assert found.uuid == "u-1"


@pytest.mark.asyncio
async def test_remnawave_paginated_batch_sync_and_retry() -> None:
    attempts = {"health": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/users":
            start = int(request.url.params.get("start", "0"))
            size = int(request.url.params.get("size", "100"))
            all_items = [
                _make_rw_payload("u-1", 101, used=100),
                _make_rw_payload("u-2", 102, used=200),
                _make_rw_payload("u-3", 103, used=300),
            ]
            chunk = all_items[start : start + size]
            return httpx.Response(200, json={"response": {"total": 3, "users": chunk}})
        if request.url.path == "/api/system/health":
            attempts["health"] += 1
            if attempts["health"] == 1:
                return httpx.Response(503, json={"message": "temporarily busy"})
            return httpx.Response(200, json={"response": {"version": "2.7.5"}})
        return httpx.Response(404)

    async with httpx.AsyncClient(
        base_url="https://panel.example.com",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = RemnawaveClient(
            "https://panel.example.com",
            "tok",
            max_retries=3,
            http_client=http,
        )
        users = await client.iter_all_users(batch_size=2)
        assert [u.uuid for u in users] == ["u-1", "u-2", "u-3"]
        assert [u.used_traffic_bytes for u in users] == [100, 200, 300]

        health = await client.health_check()
        assert health.healthy is True
        assert health.version == "2.7.5"
        assert attempts["health"] == 2
