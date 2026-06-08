from __future__ import annotations

from backend.simulator import RESERVOIR_PROPERTIES

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
