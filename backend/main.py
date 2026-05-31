from __future__ import annotations

import json
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.simulator import (
    CCSSimulator,
    SimulatorConfig,
    RESERVOIR_PROPERTIES,
)

_simulator: Optional[CCSSimulator] = None


def get_simulator() -> CCSSimulator:
    global _simulator
    if _simulator is None:
        _simulator = CCSSimulator()
        _simulator.load_data()
    return _simulator


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_simulator()
    yield


app = FastAPI(
    title="CCS Realtime Injection Simulator",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------

from pydantic import BaseModel, Field, model_validator

VALID_CASE_IDS = [f"CCS-{c}" for c in "ABCDEFGHIJ"]
VALID_RESERVOIR_TYPES = list(RESERVOIR_PROPERTIES.keys())
MAX_SIMULATION_DAYS = 366
GEOLOGICAL_LIMITS = {
    "pressure_MPa": (1.0, 100.0),
    "temp_C": (0.0, 200.0),
    "days": (0, MAX_SIMULATION_DAYS),
    "injection_rate_multiplier": (0.1, 5.0),
    "leak_probability": (0.0, 0.5),
}


class SimulateRequest(BaseModel):
    case_id: str = Field(
        default="CCS-A",
        description="Facility identifier",
        examples=["CCS-A"],
    )
    reservoir_type: Optional[str] = Field(
        default=None,
        description="Override reservoir type",
        examples=["Basalt"],
    )
    pressure_init: Optional[float] = Field(
        default=None,
        ge=GEOLOGICAL_LIMITS["pressure_MPa"][0],
        le=GEOLOGICAL_LIMITS["pressure_MPa"][1],
        description="Override initial reservoir pressure (MPa)",
    )
    temp_init: Optional[float] = Field(
        default=None,
        ge=GEOLOGICAL_LIMITS["temp_C"][0],
        le=GEOLOGICAL_LIMITS["temp_C"][1],
        description="Override initial reservoir temperature (°C)",
    )
    days: int = Field(
        default=90,
        ge=GEOLOGICAL_LIMITS["days"][0],
        le=GEOLOGICAL_LIMITS["days"][1],
        description="Number of simulation timesteps (1 day per step)",
    )
    injection_rate_multiplier: float = Field(
        default=1.0,
        ge=GEOLOGICAL_LIMITS["injection_rate_multiplier"][0],
        le=GEOLOGICAL_LIMITS["injection_rate_multiplier"][1],
        description="Scale injection rate (0.5x to 2.0x)",
    )
    leak_probability: float = Field(
        default=0.003,
        ge=GEOLOGICAL_LIMITS["leak_probability"][0],
        le=GEOLOGICAL_LIMITS["leak_probability"][1],
        description="Daily leak probability (0.0 to 0.5)",
    )
    random_seed: Optional[int] = Field(default=None, description="RNG seed for reproducibility")
    speed_multiplier: float = Field(default=1.0, ge=0.1, le=50.0)

    @model_validator(mode="after")
    def check_case_id(self):
        sim = get_simulator()
        if self.case_id not in sim.available_cases:
            raise ValueError(f"Unknown case_id '{self.case_id}'. Valid: {sim.available_cases}")
        return self


class SimulateResponse(BaseModel):
    status: str = "completed"
    days_simulated: int
    summary: dict
    trace: list[dict]


class ValidateRequest(BaseModel):
    case_id: Optional[str] = None
    reservoir_type: Optional[str] = None
    pressure_init: Optional[float] = None
    temp_init: Optional[float] = None
    days: Optional[int] = None
    injection_rate_multiplier: Optional[float] = None
    leak_probability: Optional[float] = None


class ValidationIssue(BaseModel):
    field: str
    value: object
    message: str
    severity: str = "error"


class ValidateResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = []


class PresetItem(BaseModel):
    case_id: str
    reservoir_type: str
    p_init_MPa: float
    temp_C: float
    total_injected_tonnes: float
    lat: float
    lon: float
    capture_tech: str
    transport_mode: str


class ScheduleDay(BaseModel):
    day: int
    date: str
    co2_injected_tonnes: float
    month: str


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------


@app.get("/presets", response_model=list[PresetItem])
async def get_presets():
    """Return all 10 CCS facility presets from the full dataset."""
    sim = get_simulator()
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


@app.get("/schedule/{case_id}", response_model=list[ScheduleDay])
async def get_schedule(case_id: str):
    """Return daily injection schedule for a facility."""
    sim = get_simulator()
    if case_id not in sim.available_cases:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown case_id '{case_id}'. Valid: {sim.available_cases}",
        )
    schedule = sim.get_schedule(case_id)
    result = []
    for idx, row in schedule.iterrows():
        result.append(
            ScheduleDay(
                day=idx + 1,
                date=str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"]),
                co2_injected_tonnes=round(float(row["co2_injected_tonnes"]), 2),
                month=str(row["month"]),
            )
        )
    return result


@app.post("/simulate", response_model=SimulateResponse)
async def simulate(req: SimulateRequest):
    """Run a simulation synchronously and return the full trace + summary."""
    sim = get_simulator()

    config = SimulatorConfig(
        case_id=req.case_id,
        days=req.days,
        leak_probability=req.leak_probability,
        injection_rate_multiplier=req.injection_rate_multiplier,
        speed_multiplier=req.speed_multiplier,
        reservoir_type=req.reservoir_type,
        p_init_override=req.pressure_init,
        random_seed=req.random_seed,
    )

    trace = sim.run_sync(config)
    summary = CCSSimulator.summarize(trace)

    return SimulateResponse(
        status="completed",
        days_simulated=len(trace),
        summary=summary,
        trace=trace,
    )


@app.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest):
    """Validate simulation input ranges against geological limits."""
    issues: list[ValidationIssue] = []

    if req.case_id is not None:
        sim = get_simulator()
        if req.case_id not in sim.available_cases:
            issues.append(
                ValidationIssue(
                    field="case_id",
                    value=req.case_id,
                    message=f"Unknown case_id. Valid options: {sim.available_cases}",
                )
            )

    if req.reservoir_type is not None:
        if req.reservoir_type not in VALID_RESERVOIR_TYPES:
            issues.append(
                ValidationIssue(
                    field="reservoir_type",
                    value=req.reservoir_type,
                    message=f"Unknown reservoir type. Valid: {VALID_RESERVOIR_TYPES}",
                )
            )
        elif req.case_id is not None:
            preset = get_simulator().get_preset(req.case_id)
            if req.reservoir_type != preset["reservoir_type"]:
                issues.append(
                    ValidationIssue(
                        field="reservoir_type",
                        value=req.reservoir_type,
                        message=f"Override differs from dataset value ({preset['reservoir_type']}).",
                        severity="warning",
                    )
                )

    def _check_range(field: str, value: float, limits: tuple[float, float], unit: str = ""):
        lo, hi = limits
        unit_str = f" {unit}" if unit else ""
        if value < lo:
            issues.append(
                ValidationIssue(
                    field=field,
                    value=value,
                    message=f"Below geological minimum ({lo}{unit_str}).",
                )
            )
        if value > hi:
            issues.append(
                ValidationIssue(
                    field=field,
                    value=value,
                    message=f"Above geological maximum ({hi}{unit_str}).",
                )
            )

    if req.pressure_init is not None:
        _check_range("pressure_init", req.pressure_init, GEOLOGICAL_LIMITS["pressure_MPa"], "MPa")

    if req.temp_init is not None:
        _check_range("temp_init", req.temp_init, GEOLOGICAL_LIMITS["temp_C"], "°C")

    if req.days is not None:
        _check_range("days", req.days, GEOLOGICAL_LIMITS["days"])

    if req.injection_rate_multiplier is not None:
        _check_range(
            "injection_rate_multiplier",
            req.injection_rate_multiplier,
            GEOLOGICAL_LIMITS["injection_rate_multiplier"],
        )

    if req.leak_probability is not None:
        _check_range(
            "leak_probability",
            req.leak_probability,
            GEOLOGICAL_LIMITS["leak_probability"],
        )

    return ValidateResponse(
        valid=len([i for i in issues if i.severity == "error"]) == 0,
        issues=issues,
    )


# ---------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------


@app.websocket("/ws/simulate/{case_id}")
async def ws_simulate(websocket: WebSocket, case_id: str):
    """
    Stream simulation output in real-time via WebSocket.

    Accepts a JSON config message after connection, then streams state
    updates every timestep. Final message includes summary.
    """
    await websocket.accept()

    sim = get_simulator()

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


# ---------------------------------------------------------------
# Health check
# ---------------------------------------------------------------


@app.get("/health")
async def health():
    sim = get_simulator()
    return {
        "status": "healthy",
        "available_cases": sim.available_cases,
        "case_count": len(sim.available_cases),
    }
