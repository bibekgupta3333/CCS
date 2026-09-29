from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.simulator import SimulatorConfig
from backend.simulator import CCSSimulator
from backend.state import get_simulator

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/simulate/{case_id}")
async def ws_simulate(websocket: WebSocket, case_id: str):
    """
    Stream simulation output in real-time via WebSocket.

    Accepts a JSON config message after connection, then streams state
    updates every timestep. Final message includes summary.
    """
    await websocket.accept()

    sim = await get_simulator()

    if case_id not in sim.available_cases:
        await websocket.send_json({"type": "error", "detail": f"Unknown case_id: {case_id}"})
        await websocket.close()
        return

    try:
        raw = await websocket.receive_text()
        params = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, WebSocketDisconnect):
        params = {}

    config = SimulatorConfig(
        case_id=case_id,
        days=params.get("days", 90),
        leak_probability=params.get("leak_probability", 0.003),
        injection_rate_multiplier=params.get("injection_rate_multiplier", 1.0),
        speed_multiplier=params.get("speed_multiplier", 1.0),
        reservoir_type=params.get("reservoir_type"),
        p_init_override=params.get("pressure_init"),
        random_seed=params.get("random_seed"),
    )

    trace: list[dict] = []
    try:
        async for state in sim.run_async(config):
            trace.append(state)
            await websocket.send_json({"type": "tick", "state": state})
    except WebSocketDisconnect:
        return

    summary = CCSSimulator.summarize(trace)
    await websocket.send_json({"type": "done", "summary": summary})
