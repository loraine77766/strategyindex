"""Configuración del bot (solo variables de entorno; ningún secreto en código)."""
import os
from dataclasses import dataclass, field

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}

# Símbolos Deriv CONFIRMADOS por la prueba técnica (deriv_prueba/RESULTADOS.md).
# Prohibidos (InvalidSymbol): XAUUSD, USTEC, US100, NAS100, US500, TSLA.
DERIV_SYMBOLS = ["frxXAUUSD", "OTC_NDX", "R_100", "frxXAGUSD", "frxEURUSD",
                 "cryBTCUSD", "cryETHUSD", "R_50", "1HZ100V", "BOOM1000",
                 "CRASH1000", "JD10", "STPRNG"]


@dataclass
class Settings:
    provider: str = os.environ.get("BOT_PROVIDER", "deriv")
    symbols: list = field(default_factory=lambda: [
        s for s in os.environ.get("BOT_SYMBOLS", "frxXAUUSD,OTC_NDX,R_100").split(",") if s])
    timeframes: list = field(default_factory=lambda: [
        t for t in os.environ.get("BOT_TIMEFRAMES", "1m,5m,15m,30m,1h,4h").split(",") if t in TIMEFRAMES])
    broker: str = os.environ.get("BOT_BROKER", "simulated")  # simulated (deriv: NOT IMPLEMENTED)
    balance: float = float(os.environ.get("BOT_BALANCE", "10000"))
    commission: float = float(os.environ.get("BOT_COMMISSION", "0.0005"))
    spread: float = float(os.environ.get("BOT_SPREAD", "0.0001"))
    qty: float = float(os.environ.get("BOT_QTY", "1.0"))
    # Deriv (fuente de datos pública app_id=1089; auth DEMO pendiente token clásico):
    deriv_app_id: str = os.environ.get("DERIV_APP_ID", "1089")
    deriv_token: str = os.environ.get("DERIV_TOKEN", "")
    poll_seconds: float = float(os.environ.get("BOT_POLL_SECONDS", "5"))
    # CoinMarketCap (experimental; key opcional vía entorno, nunca en código):
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
