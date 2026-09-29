from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.cache import get_cached, set_cached
from backend.models import ScheduleDay
from backend.state import get_simulator

router = APIRouter(tags=["Schedule"])


@router.get("/schedule/{case_id}", response_model=list[ScheduleDay])
async def get_schedule(case_id: str):
    cache_key = f"ccs:schedule:{case_id}"
    cached = await get_cached(cache_key)
    if cached is not None:
        return [ScheduleDay(**d) for d in cached]

    sim = await get_simulator()
    if case_id not in sim.available_cases:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown case_id '{case_id}'. Valid: {sim.available_cases}",
        )
    schedule = sim.get_schedule(case_id)
    result = []
    for idx, row in enumerate(schedule):
        d = row["date"]
        date_str = str(d.date()) if hasattr(d, "date") else str(d)[:10]
        result.append(
            ScheduleDay(
                day=idx + 1,
                date=date_str,
                co2_injected_tonnes=round(float(row["co2_injected_tonnes"]), 2),
                month=str(row["month"]),
            )
        )
    await set_cached(cache_key, [r.model_dump(mode="json") for r in result])
    return result
