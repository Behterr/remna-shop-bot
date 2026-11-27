#!/usr/bin/env bash
set -Eeuo pipefail

echo "[entrypoint] Waiting for PostgreSQL and Redis..."
python - <<'PY'
import asyncio
import os
import sys
import asyncpg
from redis.asyncio import Redis

async def wait_services() -> None:
    db_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    redis_url = os.environ["REDIS_URL"]

    for attempt in range(1, 31):
        try:
            conn = await asyncpg.connect(db_url, timeout=3)
            await conn.execute("SELECT 1")
            await conn.close()
            break
        except Exception as exc:
            if attempt == 30:
                print(f"[entrypoint] PostgreSQL unavailable after 30 attempts: {exc}", file=sys.stderr)
                sys.exit(1)
            await asyncio.sleep(2)

    for attempt in range(1, 31):
        r = Redis.from_url(redis_url)
        try:
            await r.ping()
            await r.aclose()
            break
        except Exception as exc:
            await r.aclose()
            if attempt == 30:
                print(f"[entrypoint] Redis unavailable after 30 attempts: {exc}", file=sys.stderr)
                sys.exit(1)
            await asyncio.sleep(2)

asyncio.run(wait_services())
PY

echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Starting remna-shop-bot..."
exec python -m app.main
