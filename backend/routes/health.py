from __future__ import annotations

from fastapi import APIRouter

from backend.database import get_pool
from backend.state import get_simulator

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health():
    sim = await get_simulator()
    db_ok = False
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        pass
    return {
        "status": "healthy" if db_ok else "db_unreachable",
        "db": "connected" if db_ok else "disconnected",
        "available_cases": sim.available_cases,
        "case_count": len(sim.available_cases),
    }
