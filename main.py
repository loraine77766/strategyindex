"""StrategyIndex — FastAPI entrypoint (Faable + RunYour.App).

Faable:  uvicorn main:app --host 0.0.0.0 --port $PORT
RunYour.App: detecta app/main.py con app = FastAPI()
"""
import asyncio
import os

from fastapi import FastAPI

from bot.api import create_app as _create_api
from bot.config import Settings
from bot.log import get_logger
from bot.main import Orchestrator

log = get_logger("entry")

app = FastAPI(title="StrategyIndex")

_settings = Settings()
_orch = Orchestrator(_settings)
_create_api(_orch, app=app)

_provider_task = None


async def _run_provider():
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
    providers = list(_orch._providers.keys())
    log.info("startup: providers=%s, bots=%d, port=%s",
             providers, len(_orch.bots), _settings.port)
    _provider_task = asyncio.create_task(_run_provider())


@app.on_event("shutdown")
async def _shutdown():
    global _provider_task
    log.info("shutdown")
    if _provider_task and not _provider_task.done():
        _provider_task.cancel()
        try:
            await _provider_task
        except asyncio.CancelledError:
            pass
    for prov in _orch._providers.values():
        try:
            prov.stop()
        except Exception:
            pass
