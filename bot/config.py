"""Configuracion del bot (solo variables de entorno; ningun secreto en codigo)."""
import os
from dataclasses import dataclass, field

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}


@dataclass
class Settings:
    provider: str = os.environ.get("BOT_PROVIDER", "coinmarketcap")
    symbols: list = field(default_factory=lambda: [
        s.strip() for s in os.environ.get("BOT_SYMBOLS", "").split(",") if s.strip()])
    timeframes: list = field(default_factory=lambda: [
        t for t in os.environ.get("BOT_TIMEFRAMES", "1m,5m,15m,30m,1h,4h").split(",") if t in TIMEFRAMES])
    broker: str = os.environ.get("BOT_BROKER", "simulated")
    balance: float = float(os.environ.get("BOT_BALANCE", "10000"))
    commission: float = float(os.environ.get("BOT_COMMISSION", "0.0005"))
    spread: float = float(os.environ.get("BOT_SPREAD", "0.0001"))
    qty: float = float(os.environ.get("BOT_QTY", "1.0"))
    poll_seconds: float = float(os.environ.get("BOT_POLL_SECONDS", "30"))
    cmc_ids: str = os.environ.get("CMC_IDS", "BTC:1,ETH:1027,SOL:5426")
    cmc_poll_seconds: float = float(os.environ.get("CMC_POLL_SECONDS", "60"))
    cmc_api_key: str = os.environ.get("CMC_API_KEY", "")
    cmc_catalog_limit: int = int(os.environ.get("CMC_CATALOG_LIMIT", "200"))
    db_path: str = os.environ.get("BOT_DB", "bot/bot.db")
    database_url: str = os.environ.get("DATABASE_URL", "")
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8000"))
    log_level: str = os.environ.get("BOT_LOG", "INFO")
    cors_origins: str = os.environ.get("CORS_ORIGINS", "*")
