from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from backend.database import init_db, ingest_from_csv, close_pool
from backend.state import get_simulator
from backend.routes.health import router as health_router
from backend.routes.presets import router as presets_router
from backend.routes.schedule import router as schedule_router
from backend.routes.simulate import router as simulate_router
from backend.routes.ws import router as ws_router


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            obj["exc"] = str(record.exc_info[1])
        return json.dumps(obj, default=str)


class _StdoutHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            s = self.format(record) + "\n"
            os.write(1, s.encode("utf-8"))
        except Exception:
            self.handleError(record)


_json_handler = _StdoutHandler()
_json_handler.setFormatter(_JSONFormatter())

logger = logging.getLogger("ccs.backend")
logger.addHandler(_json_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Backend starting — DB init runs in background")

    async def _connect_db():
        for attempt in range(30):
            try:
                await init_db()
                logger.info("DB pool ready")
                await ingest_from_csv()
                logger.info("CSV ingestion done")
                sim = await get_simulator()
                logger.info("Simulator loaded (%d cases)", len(sim.available_cases))
                return
            except Exception as e:
                logger.warning("DB not ready (attempt %d/30): %s", attempt + 1, e)
                await asyncio.sleep(2)
        logger.error("DB never became available after 30 attempts")

    _db_task = asyncio.create_task(_connect_db())

    yield

    _db_task.cancel()
    try:
        await _db_task
    except asyncio.CancelledError:
        pass
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

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
