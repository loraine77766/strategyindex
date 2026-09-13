"""StrategyIndex — RunYour.App entrypoint.

FastAPI app detectable como `main:app`.
Reutiliza 100% la lógica de bot/ (Orchestrator, API, provider, etc).
Lifecycle: startup → seed + provider background | shutdown → stop provider.

Uso local:
    uvicorn main:app --host 0.0.0.0 --port 8000

RunYour.App:
    Detecta main:app automáticamente (Railpack o Dockerfile).
"""
import asyncio
import os

from fastapi import FastAPI

from bot.api import create_app as _create_api
from bot.config import Settings
from bot.log import get_logger
from bot.main import Orchestrator

log = get_logger("runyourapp")

# ---------------------------------------------------------------------------
# Orchestrator singleton (created once, shared across all requests)
# ---------------------------------------------------------------------------
_settings = Settings()
_orch = Orchestrator(_settings)

# Build the FastAPI app with all routes from bot.api
app = _create_api(_orch)

# ---------------------------------------------------------------------------
# Background provider task
# ---------------------------------------------------------------------------
_provider_task = None


async def _run_provider():
    """Run the data provider (Deriv/CMC) in the background."""
    try:
        await _orch.seed_history()
        await _orch.run_client()
    except asyncio.CancelledError:
        log.info("provider task cancelado")
    except Exception as e:
        log.warning("provider fallo: %s", str(e)[:200])


@app.on_event("startup")
async def _startup():
    global _provider_task
    log.info("RunYour.App startup: provider=%s, bots=%d, port=%s",
             _orch.provider.name, len(_orch.bots), _settings.port)
    _provider_task = asyncio.create_task(_run_provider())


@app.on_event("shutdown")
async def _shutdown():
    global _provider_task
    log.info("RunYour.App shutdown")
    if _provider_task and not _provider_task.done():
        _provider_task.cancel()
        try:
            await _provider_task
        except asyncio.CancelledError:
            pass
    _orch.provider.stop()
