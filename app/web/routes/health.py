"""Health check endpoint verifying PostgreSQL and Redis readiness."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    database = request.app.state.database
    redis = request.app.state.redis

    pg_ok = False
    redis_ok = redis is None

    try:
        pg_ok = await database.ping()
    except Exception:
        pg_ok = False

    if redis is not None:
        try:
            redis_ok = bool(await redis.ping())
        except Exception:
            redis_ok = False

    healthy = pg_ok and redis_ok
    payload = {
        "status": "ok" if healthy else "degraded",
        "postgres": "ok" if pg_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload,
    )
