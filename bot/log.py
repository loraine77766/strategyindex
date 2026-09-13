"""Logs: claros, sin ticks salvo DEBUG."""
import logging
import os

_configured = False


def get_logger(name: str = "bot") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if not _configured:
        level = os.environ.get("BOT_LOG", "INFO").upper()
        logging.basicConfig(
            level=getattr(logging, level, logging.INFO),
            format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        _configured = True
    return logger
