from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import init_db, ingest_from_csv, close_pool
from backend.state import get_simulator
from backend.routes.health import router as health_router
from backend.routes.presets import router as presets_router
from backend.routes.schedule import router as schedule_router
from backend.routes.simulate import router as simulate_router
from backend.routes.ws import router as ws_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Waiting for database ...")
    for attempt in range(30):
        try:
            await init_db()
            break
        except Exception as e:
            if attempt < 29:
                logger.warning("DB not ready (attempt %d/30): %s", attempt + 1, e)
                await asyncio.sleep(2)
            else:
                raise
    logger.info("Running CSV ingestion (idempotent) ...")
    await ingest_from_csv()
    logger.info("Loading data into simulator ...")
    sim = await get_simulator()
    logger.info("Ready — %d cases loaded", len(sim.available_cases))
    yield
    await close_pool()
    logger.info("Database pool closed")


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

app.include_router(health_router)
app.include_router(presets_router)
app.include_router(schedule_router)
app.include_router(simulate_router)
app.include_router(ws_router)
