from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.config import GEOLOGICAL_LIMITS, VALID_RESERVOIR_TYPES
from backend.models import (
    SimulateRequest,
    SimulateResponse,
    ValidateRequest,
    ValidationIssue,
    ValidateResponse,
)
from backend.simulator import SimulatorConfig
from backend.simulator import CCSSimulator
from backend.state import get_simulator

router = APIRouter(tags=["Simulation"])


@router.post("/simulate", response_model=SimulateResponse)
async def simulate(req: SimulateRequest):
    """Run a simulation synchronously and return the full trace + summary."""
    sim = await get_simulator()

    if req.case_id not in sim.available_cases:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown case_id '{req.case_id}'. Valid: {sim.available_cases}",
        )

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


@router.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest):
    """Validate simulation input ranges against geological limits."""
    issues: list[ValidationIssue] = []

    if req.case_id is not None:
        sim = await get_simulator()
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
            preset = (await get_simulator()).get_preset(req.case_id)
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
