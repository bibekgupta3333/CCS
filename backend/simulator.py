import asyncio
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ccs"
INJECTION_CSV = DATA_DIR / "ccs_injection_daily_v1.0.csv"
PRESETS_CSV = DATA_DIR / "ccs_full_dataset_v1.0.csv"

RESERVOIR_PROPERTIES = {
    "Basalt":            {"porosity": 0.12, "thickness_m": 55, "swi": 0.25, "k_inj": 2.2e-5, "k_diss": 0.005},
    "Saline aquifer":    {"porosity": 0.20, "thickness_m": 40, "swi": 0.35, "k_inj": 1.2e-5, "k_diss": 0.007},
    "Depleted gas field":{"porosity": 0.25, "thickness_m": 35, "swi": 0.20, "k_inj": 6.0e-6, "k_diss": 0.010},
}


def _co2_density(pressure_mpa: float) -> float:
    """Approximate supercritical CO2 density (kg/m3) at reservoir conditions."""
    if pressure_mpa < 7.4:
        return max(50, 50 + 20 * pressure_mpa)
    rho = 200 + 35 * (pressure_mpa - 7.4)
    return min(850, max(150, rho))


@dataclass
class SimulatorConfig:
    case_id: str = "CCS-A"
    days: int = 90
    leak_probability: float = 0.003
    leak_fraction_range: tuple[float, float] = (0.02, 0.12)
    noise_pressure_pct: float = 0.02
    noise_plume_pct: float = 0.04
    noise_injection_pct: float = 0.01
    speed_multiplier: float = 1.0
    injection_rate_multiplier: float = 1.0
    reservoir_type: Optional[str] = None
    p_init_override: Optional[float] = None
    random_seed: Optional[int] = None


@dataclass
class SimulationState:
    day: int = 0
    pressure_mpa: float = 0.0
    co2_injected_tonnes: float = 0.0
    co2_cumulative_tonnes: float = 0.0
    plume_radius_m: float = 0.0
    leak_kg: float = 0.0
    leak_total_kg: float = 0.0
    leak_events: int = 0
    co2_density_kg_m3: float = 650.0
    raw_pressure_mpa: float = 0.0


class CCSSimulator:
    """CO2 injection reservoir simulator.

    Models pressure buildup, plume migration, and stochastic leakage
    on a day-by-day timestep, driven by a real SCCS-MRV injection schedule.
    """

    def __init__(self) -> None:
        self._injection_df: Optional[pd.DataFrame] = None
        self._presets_df: Optional[pd.DataFrame] = None
        self._rng: Optional[np.random.Generator] = None

    # ------------------------------------------------------------------
    # data loading
    # ------------------------------------------------------------------

    def load_data(self) -> None:
        self._injection_df = pd.read_csv(INJECTION_CSV, parse_dates=["date"])
        self._presets_df = pd.read_csv(PRESETS_CSV)

    def _ensure_loaded(self) -> None:
        if self._injection_df is None or self._presets_df is None:
            self.load_data()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @property
    def available_cases(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._injection_df["case_id"].unique().tolist())

    def get_schedule(self, case_id: str) -> pd.DataFrame:
        self._ensure_loaded()
        mask = self._injection_df["case_id"] == case_id
        return self._injection_df.loc[mask].sort_values("date").reset_index(drop=True)

    def get_preset(self, case_id: str) -> Dict:
        self._ensure_loaded()
        row = self._presets_df[self._presets_df["case_id"] == case_id]
        if row.empty:
            raise ValueError(f"Unknown case_id: {case_id}")
        r = row.iloc[0]
        return {
            "case_id": r["case_id"],
            "reservoir_type": r["reservoir_type"],
            "p_init_MPa": float(r["avg_reservoir_pressure_MPa"]),
            "temp_C": float(r["avg_reservoir_temp_C"]),
            "total_injected_tonnes": float(r["co2_injected_tonnes"]),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "capture_tech": r["capture_tech"],
            "transport_mode": r["transport_mode"],
        }

    # ------------------------------------------------------------------
    # reservoir physics
    # ------------------------------------------------------------------

    def _reservoir_params(self, reservoir_type: str) -> Dict:
        base = RESERVOIR_PROPERTIES.get(reservoir_type, RESERVOIR_PROPERTIES["Basalt"])
        return {
            "porosity": base["porosity"],
            "thickness_m": base["thickness_m"],
            "swi": base["swi"],
            "k_inj": base["k_inj"],
            "k_diss": base["k_diss"],
        }

    def _pressure(self, p_init: float, v_cum: float, day: int,
                  k_inj: float, k_diss: float) -> tuple[float, float]:
        """Pressure model: P(t) = P_init + k_inj * V_cum - dissipation(t)"""
        buildup = k_inj * v_cum
        dissipation = k_diss * math.sqrt(max(day - 1, 0)) * buildup
        raw = p_init + buildup - dissipation
        return max(p_init * 0.9, raw), buildup - dissipation

    def _plume_radius(self, v_cum_tonnes: float, pressure_mpa: float,
                      porosity: float, thickness_m: float, swi: float) -> float:
        """Nordbotten-Celio style analytical plume radius."""
        if v_cum_tonnes <= 0:
            return 0.0
        rho_co2 = _co2_density(pressure_mpa)
        v_m3 = v_cum_tonnes * 1000.0 / rho_co2
        effective_porosity = porosity * (1.0 - swi)
        denominator = math.pi * effective_porosity * thickness_m
        if denominator <= 0:
            return 0.0
        radius = math.sqrt(v_m3 / denominator)
        return min(radius, 5000.0)

    # ------------------------------------------------------------------
    # noise
    # ------------------------------------------------------------------

    def _jitter(self, value: float, pct_std: float) -> float:
        if pct_std <= 0 or value == 0:
            return value
        return value * (1.0 + pct_std * float(self._rng.normal(0, 1)))

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------

    def run_sync(self, config: SimulatorConfig) -> list[Dict]:
        """Run simulation synchronously, return full trace."""
        return list(self.run(config))

    def run(self, config: SimulatorConfig):
        """Generator yielding one SimulationState dict per timestep."""
        self._ensure_loaded()
        self._rng = np.random.default_rng(config.random_seed)

        schedule = self.get_schedule(config.case_id)
        preset = self.get_preset(config.case_id)
        reservoir = self._reservoir_params(
            config.reservoir_type or preset["reservoir_type"]
        )

        p_init = config.p_init_override if config.p_init_override is not None else preset["p_init_MPa"]

        total_available = len(schedule)
        sim_days = min(config.days, total_available)

        k_inj = reservoir["k_inj"]
        k_diss = reservoir["k_diss"]
        porosity = reservoir["porosity"]
        thickness_m = reservoir["thickness_m"]
        swi = reservoir["swi"]

        cumulative_tonnes = 0.0
        leak_total_kg = 0.0
        leak_event_count = 0

        for day_idx in range(sim_days):
            day = day_idx + 1
            row = schedule.iloc[day_idx]
            injected_tonnes = row["co2_injected_tonnes"] * config.injection_rate_multiplier
            injected_tonnes = max(0.0, injected_tonnes)

            cumulative_tonnes += injected_tonnes

            pressure_mpa, _ = self._pressure(
                p_init, cumulative_tonnes, day, k_inj, k_diss
            )

            raw_pressure = pressure_mpa

            rho_co2 = _co2_density(pressure_mpa)

            leak_kg = 0.0
            leak_occurred = self._rng.random() < config.leak_probability
            if leak_occurred and injected_tonnes > 0:
                leak_frac = self._rng.uniform(*config.leak_fraction_range)
                leak_kg = injected_tonnes * leak_frac * 1000.0
                leak_total_kg += leak_kg
                leak_event_count += 1
                pressure_loss = k_inj * (leak_kg / 1000.0)
                pressure_mpa = max(p_init * 0.85, pressure_mpa - pressure_loss)

            plume_radius = self._plume_radius(
                cumulative_tonnes, pressure_mpa, porosity, thickness_m, swi
            )

            pressure_mpa = self._jitter(pressure_mpa, config.noise_pressure_pct)
            injected_noisy = self._jitter(injected_tonnes, config.noise_injection_pct)
            plume_radius = self._jitter(plume_radius, config.noise_plume_pct)
            leak_noisy = self._jitter(leak_kg, config.noise_injection_pct * 2)

            state = SimulationState(
                day=day,
                pressure_mpa=round(pressure_mpa, 2),
                co2_injected_tonnes=round(injected_noisy, 2),
                co2_cumulative_tonnes=round(cumulative_tonnes, 2),
                plume_radius_m=round(plume_radius, 1),
                leak_kg=round(leak_noisy, 2),
                leak_total_kg=round(leak_total_kg, 2),
                leak_events=leak_event_count,
                co2_density_kg_m3=round(rho_co2, 1),
                raw_pressure_mpa=round(raw_pressure, 2),
            )

            yield {
                "day": state.day,
                "pressure_MPa": state.pressure_mpa,
                "co2_injected": state.co2_injected_tonnes,
                "co2_cumulative": state.co2_cumulative_tonnes,
                "plume_radius_m": state.plume_radius_m,
                "leak_kg": state.leak_kg,
                "leak_total_kg": state.leak_total_kg,
                "leak_events": state.leak_events,
            }

    async def run_async(self, config: SimulatorConfig) -> AsyncIterator[Dict]:
        """Async generator that yields with configurable delay between steps."""
        for state in self.run(config):
            yield state
            delay = 0.1 / max(config.speed_multiplier, 0.1)
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------

    @staticmethod
    def summarize(trace: list[Dict]) -> Dict:
        """Produce a summary from a completed simulation trace."""
        if not trace:
            return {}
        final = trace[-1]
        pressures = [s["pressure_MPa"] for s in trace]
        return {
            "total_days": final["day"],
            "max_pressure_MPa": round(max(pressures), 2),
            "final_pressure_MPa": final["pressure_MPa"],
            "total_co2_injected_tonnes": final["co2_cumulative"],
            "final_plume_radius_m": final["plume_radius_m"],
            "total_leak_kg": final["leak_total_kg"],
            "leak_event_count": final["leak_events"],
            "avg_daily_injection_tonnes": round(
                final["co2_cumulative"] / final["day"], 2
            ),
        }
