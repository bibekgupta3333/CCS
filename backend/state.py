from __future__ import annotations

from typing import Optional

from backend.simulator import CCSSimulator

_simulator: Optional[CCSSimulator] = None


async def get_simulator() -> CCSSimulator:
    global _simulator
    if _simulator is None:
        _simulator = CCSSimulator()
        await _simulator.load_from_db(None)
    return _simulator
