import logging
import os
from pathlib import Path

import asyncpg
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ccs"
FACILITIES_CSV = DATA_DIR / "ccs_full_dataset_v1.0.csv"
INJECTION_CSV = DATA_DIR / "ccs_injection_daily_v1.0.csv"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://ccs:ccs_password@localhost:5432/ccs_db",
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------
# schema (idempotent CREATE TABLE IF NOT EXISTS)
# ---------------------------------------------------------------

FACILITIES_DDL = """
CREATE TABLE IF NOT EXISTS facilities (
    record_id              TEXT PRIMARY KEY,
    case_id                TEXT NOT NULL,
    facility_id            TEXT NOT NULL,
    country_code           TEXT NOT NULL,
    lat                    DOUBLE PRECISION NOT NULL,
    lon                    DOUBLE PRECISION NOT NULL,
    capture_tech           TEXT NOT NULL,
    co2_captured_tonnes    BIGINT NOT NULL,
    capture_energy_MWh     DOUBLE PRECISION NOT NULL,
    transport_mode         TEXT NOT NULL,
    pipeline_length_km     DOUBLE PRECISION NOT NULL,
    transport_loss_tonnes  DOUBLE PRECISION NOT NULL,
    well_id                TEXT NOT NULL,
    injection_start_date   TEXT NOT NULL,
    injection_end_date     TEXT NOT NULL,
    co2_injected_tonnes    DOUBLE PRECISION NOT NULL,
    co2_produced_tonnes    DOUBLE PRECISION NOT NULL,
    reservoir_type         TEXT NOT NULL,
    avg_reservoir_pressure_MPa DOUBLE PRECISION NOT NULL,
    avg_reservoir_temp_C   DOUBLE PRECISION NOT NULL,
    mmv_methods            TEXT NOT NULL,
    leak_events_count      INTEGER NOT NULL,
    leak_mass_tonnes       DOUBLE PRECISION NOT NULL,
    ch4_emissions_tonnes   DOUBLE PRECISION NOT NULL,
    ogmp_source_category   TEXT NOT NULL,
    co2_net_stored_tonnes  DOUBLE PRECISION NOT NULL
);
"""

INJECTION_DDL = """
CREATE TABLE IF NOT EXISTS injection_schedule (
    id                  SERIAL PRIMARY KEY,
    case_id             TEXT NOT NULL,
    date                DATE NOT NULL,
    co2_injected_tonnes DOUBLE PRECISION NOT NULL,
    month               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_injection_case_date
    ON injection_schedule (case_id, date);
"""


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(FACILITIES_DDL)
        await conn.execute(INJECTION_DDL)
    logger.info("Database tables initialised")


# ---------------------------------------------------------------
# ingestion (idempotent — skips if data already present)
# ---------------------------------------------------------------

async def ingest_from_csv() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT COUNT(*) FROM facilities")
        if existing > 0:
            logger.info("Data already present (%d facilities), skipping ingestion", existing)
            return

        df_fac = pd.read_csv(FACILITIES_CSV)
        df_fac = df_fac.where(pd.notnull(df_fac), None)

        fac_cols = [
            "record_id", "case_id", "facility_id", "country_code",
            "lat", "lon", "capture_tech", "co2_captured_tonnes",
            "capture_energy_MWh", "transport_mode", "pipeline_length_km",
            "transport_loss_tonnes", "well_id", "injection_start_date",
            "injection_end_date", "co2_injected_tonnes", "co2_produced_tonnes",
            "reservoir_type", "avg_reservoir_pressure_MPa", "avg_reservoir_temp_C",
            "mmv_methods", "leak_events_count", "leak_mass_tonnes",
            "ch4_emissions_tonnes", "ogmp_source_category", "co2_net_stored_tonnes",
        ]

        placeholders = ", ".join(f"${i + 1}" for i in range(len(fac_cols)))
        insert_fac = f"INSERT INTO facilities ({', '.join(fac_cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

        rows = [tuple(r[c] for c in fac_cols) for _, r in df_fac.iterrows()]
        await conn.executemany(insert_fac, rows)
        logger.info("Inserted %d facilities", len(rows))

        df_inj = pd.read_csv(INJECTION_CSV, parse_dates=["date"])
        df_inj = df_inj.where(pd.notnull(df_inj), None)

        inj_stmt = """
            INSERT INTO injection_schedule (case_id, date, co2_injected_tonnes, month)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
        """

        async with conn.transaction():
            for _, row in df_inj.iterrows():
                d = row["date"]
                await conn.execute(
                    inj_stmt,
                    row["case_id"],
                    d.date() if hasattr(d, "date") else d,
                    float(row["co2_injected_tonnes"]),
                    str(row["month"]),
                )

        logger.info("Inserted %d injection records", len(df_inj))


# ---------------------------------------------------------------
# query helpers
# ---------------------------------------------------------------

async def get_available_cases() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT case_id FROM injection_schedule ORDER BY case_id")
        return [r["case_id"] for r in rows]


async def get_preset(case_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM facilities WHERE case_id = $1", case_id)
        if row is None:
            raise ValueError(f"Unknown case_id: {case_id}")
        return dict(row)


async def get_schedule_rows(case_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT case_id, date, co2_injected_tonnes, month "
            "FROM injection_schedule WHERE case_id = $1 "
            "ORDER BY date",
            case_id,
        )
        return [dict(r) for r in rows]
