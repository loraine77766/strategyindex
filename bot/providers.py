"""MarketDataProvider: interfaz + provider factory."""
from .log import get_logger

log = get_logger("data")


class MarketDataProvider:
    name = "base"
    supported_timeframes: tuple = ("1m", "5m", "15m", "30m", "1h", "4h")

    async def history(self, symbol, timeframe, limit=400) -> list[dict]:
        raise NotImplementedError

    async def active_symbols(self) -> list[str]:
        raise NotImplementedError

    async def run(self, symbols: list[str], otp_url=None):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def capabilities(self) -> "ProviderCapabilities":
        return ProviderCapabilities()

    on_tick = None
    on_status = None


class ProviderCapabilities:
    def __init__(self, ticks=False, ohlc=False, history=False, streaming=False,
                 reconnect=False, volume=False, spread=False, websocket=False,
                 historical_candles=False, credentials_required=True):
        self.ticks = ticks
        self.ohlc = ohlc
        self.history = history
        self.streaming = streaming
        self.reconnect = reconnect
        self.volume = volume
        self.spread = spread
        self.websocket = websocket
        self.historical_candles = historical_candles
        self.credentials_required = credentials_required

    def as_dict(self):
        return dict(self.__dict__)


def create_provider(settings):
    kind = (settings.provider or "coinmarketcap").lower()
    if kind == "coinmarketcap":
        from .providers_coinmarketcap import CoinMarketCapProvider, parse_ids
        return CoinMarketCapProvider(
            ids=parse_ids(getattr(settings, "cmc_ids", "")),
            poll_seconds=float(getattr(settings, "cmc_poll_seconds", 60.0)),
            api_key=getattr(settings, "cmc_api_key", ""))
    if kind == "yahoo":
        from .providers_yahoo import YahooFinanceProvider
        return YahooFinanceProvider(
            poll_seconds=float(getattr(settings, "poll_seconds", 30.0)))
    raise NotImplementedError(f"Proveedor '{kind}' no implementado")
