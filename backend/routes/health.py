from __future__ import annotations

from fastapi import APIRouter

from backend.database import get_pool

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    """Liveness probe: process is alive. Does NOT check DB."""
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    """Readiness probe: DB pool has a connection. Fails → remove from Service."""
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
    except Exception:
        return {"status": "not_ready", "db": "disconnected"}
    return {"status": "ready", "db": "connected"}


@router.get("/startup")
async def startup():
    """Startup probe: DB schema exists (tables created). Fails → delay liveness."""
    try:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'facilities')"
        )
        schema_ok = row[0] if row else False
    except Exception:
        return {"status": "starting", "schema": False}
    return {"status": "started" if schema_ok else "starting", "schema": schema_ok}
