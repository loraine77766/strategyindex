"""Arranque: API + proveedor de datos. Shutdown limpio con Ctrl+C."""
import asyncio
import threading
from bot.api import run_api
from bot.config import Settings
from bot.log import get_logger
from bot.main import Orchestrator

log = get_logger("run")


async def _boot(orch: Orchestrator):
    await orch.seed_history()
    await orch.run_client()


def main():
    orch = Orchestrator(Settings())
    threading.Thread(target=run_api, args=(orch,), daemon=True).start()
    log.info("StrategyIndex en http://%s:%s (provider %s, %d bots, broker simulado)",
             orch.s.host, orch.s.port, orch.provider.name, len(orch.bots))
    try:
        asyncio.run(_boot(orch))
    except KeyboardInterrupt:
        log.info("shutdown limpio")
        orch.provider.stop()


if __name__ == "__main__":
    main()
