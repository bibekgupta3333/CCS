from __future__ import annotations

from fastapi import APIRouter

from backend.cache import get_cached, set_cached
from backend.models import PresetItem
from backend.state import get_simulator

router = APIRouter(tags=["Presets"])

PRESETS_KEY = "ccs:presets"


@router.get("/presets", response_model=list[PresetItem])
async def get_presets():
    cached = await get_cached(PRESETS_KEY)
    if cached is not None:
        return [PresetItem(**p) for p in cached]

    sim = await get_simulator()
    presets = []
    for case_id in sim.available_cases:
        p = sim.get_preset(case_id)
        presets.append(
            PresetItem(
                case_id=p["case_id"],
                reservoir_type=p["reservoir_type"],
                p_init_MPa=p["p_init_MPa"],
                temp_C=p["temp_C"],
                total_injected_tonnes=p["total_injected_tonnes"],
                lat=p["lat"],
                lon=p["lon"],
                capture_tech=p["capture_tech"],
                transport_mode=p["transport_mode"],
            )
        )
    result = [p.model_dump(mode="json") for p in presets]
    await set_cached(PRESETS_KEY, result)
    return presets
