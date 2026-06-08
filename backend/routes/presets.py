from __future__ import annotations

from fastapi import APIRouter

from backend.models import PresetItem
from backend.state import get_simulator

router = APIRouter(tags=["Presets"])


@router.get("/presets", response_model=list[PresetItem])
async def get_presets():
    """Return all 10 CCS facility presets from the full dataset."""
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
    return presets
