import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.simulator import CCSSimulator

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "ccs"


def _load_from_csv(sim: CCSSimulator) -> None:
    presets_csv = DATA_DIR / "ccs_full_dataset_v1.0.csv"
    injection_csv = DATA_DIR / "ccs_injection_daily_v1.0.csv"

    df_fac = pd.read_csv(presets_csv)
    df_inj = pd.read_csv(injection_csv, parse_dates=["date"])

    sim._facility_presets = {}
    for _, r in df_fac.iterrows():
        sim._facility_presets[r["case_id"]] = {
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

    sim._injection_schedules = {}
    sim._case_ids = sorted(df_inj["case_id"].unique().tolist())

    for case_id in sim._case_ids:
        mask = df_inj["case_id"] == case_id
        sim._injection_schedules[case_id] = (
            df_inj.loc[mask]
            .sort_values("date")
            .to_dict(orient="records")
        )


@pytest.fixture(scope="session")
def simulator() -> CCSSimulator:
    sim = CCSSimulator()
    _load_from_csv(sim)
    return sim
