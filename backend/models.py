from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from backend.config import GEOLOGICAL_LIMITS


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
