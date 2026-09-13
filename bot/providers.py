"""MarketDataProvider: interfaz + DerivProvider (fuente oficial del proyecto).

DerivProvider usa el cliente Deriv probado (bot/market_data.py, evidencia en
deriv_prueba/RESULTADOS.md): ticks_history (ticks y velas OHLC) one-shot,
suscripción `ticks` cuando el servidor la acepta (tras auth), reconexión con
backoff, ping/keepalive, resuscripción y cierre limpio.
Sin auth el servidor rechaza suscripciones (InvalidSymbol) y el proveedor
opera en modo POLL degradado y documentado. Streaming autenticado:
PENDIENTE_DE_CONFIRMAR (falta token clásico DEMO).
"""
from .log import get_logger
from .market_data import DerivClient

log = get_logger("data")


class MarketDataProvider:
    name = "base"
    # Timeframes que el proveedor declara soportar (labels genéricos).
    supported_timeframes: tuple = ("1m", "5m", "15m", "30m", "1h", "4h")

    async def history(self, symbol, timeframe, limit=400) -> list[dict]:
        """Velas históricas [{epoch,open,high,low,close,volume?}]."""
        raise NotImplementedError

    async def active_symbols(self) -> list[str]:
        raise NotImplementedError

    async def run(self, symbols: list[str], otp_url=None):
        """Bucle de ticks con reconexión. Llama on_tick/on_status."""
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def capabilities(self) -> "ProviderCapabilities":
        """Capacidades verificadas (nunca inventadas: lo no probado = False)."""
        return ProviderCapabilities()

    on_tick = None    # cb(symbol, price, epoch)
    on_status = None  # cb(dict)


class ProviderCapabilities:
    """Lo que un proveedor puede ofrecer (verificado, no asumido)."""

    def __init__(self, ticks=False, ohlc=False, history=False, streaming=False,
                 reconnect=False, volume=False, spread=False, websocket=False,
                 historical_candles=False, credentials_required=True):
        self.ticks = ticks
        self.ohlc = ohlc
        self.history = history
        self.streaming = streaming          # push continuo verificado
        self.reconnect = reconnect
        self.volume = volume                # volumen real disponible
        self.spread = spread                # spread consultable
        self.websocket = websocket
        self.historical_candles = historical_candles
        self.credentials_required = credentials_required

    def as_dict(self):
        return dict(self.__dict__)


class DerivProvider(MarketDataProvider):
    name = "deriv"

    def __init__(self, app_id="1089", token="", poll_seconds=5.0):
        self.client = DerivClient(app_id, token, poll_seconds)
        self.on_tick = None
        self.on_status = None

    def stop(self):
        self.client.stop()

    @property
    def mode(self):
        return self.client.mode

    def capabilities(self) -> "ProviderCapabilities":
        # Verificado en deriv_prueba/RESULTADOS.md + arranques vivos.
        return ProviderCapabilities(
            ticks=True, ohlc=True, history=True,
            streaming=False,  # PENDIENTE_DE_CONFIRMAR (requiere auth)
            reconnect=True, volume=False, spread=False,
            websocket=True, historical_candles=True,
            credentials_required=False,  # datos públicos con app_id de prueba
        )

    async def history(self, symbol, timeframe, limit=400) -> list[dict]:
        import websockets
        from .market_data import CLASSIC_WS
        url = CLASSIC_WS.format(app_id=self.client.app_id)
        async with websockets.connect(url, max_size=2 ** 22) as ws:
            return await self.client.history_candles(ws, symbol, timeframe, limit)

    async def active_symbols(self) -> list[str]:
        import websockets
        from .market_data import CLASSIC_WS
        url = CLASSIC_WS.format(app_id=self.client.app_id)
        async with websockets.connect(url, max_size=2 ** 22) as ws:
            return await self.client.active_symbols(ws)

    async def run(self, symbols, otp_url=None):
        self.client.on_tick = self.on_tick
        self.client.on_status = self.on_status
        await self.client.run(symbols, otp_url)


class PairScanner:
    """Punto de extensión futuro: analizar pares por volumen, liquidez,
    historial, volatilidad, spread, WS y velas disponibles.

    NO implementado (un scanner completo no hace falta aún). Define solo la
    interfaz para no inventar métricas. Recibirá un MarketDataProvider.
    """

    def __init__(self, provider: MarketDataProvider):
        self.provider = provider

    def score(self, symbol) -> dict:
        raise NotImplementedError("PairScanner pendiente (ver PROJECT_MEMORY §17)")


class KrakenProvider(MarketDataProvider):
    """PREPARADO, NO IMPLEMENTADO. No conectar hasta orden explícita."""

    name = "kraken"

    def _todo(self):
        raise NotImplementedError("KrakenProvider no implementado")

    async def history(self, *a, **k): return self._todo()
    async def active_symbols(self): return self._todo()
    async def run(self, *a, **k): return self._todo()
    def stop(self): return self._todo()


class OKXProvider(MarketDataProvider):
    """PREPARADO, NO IMPLEMENTADO. No conectar hasta orden explícita."""

    name = "okx"

    def _todo(self):
        raise NotImplementedError("OKXProvider no implementado")

    async def history(self, *a, **k): return self._todo()
    async def active_symbols(self): return self._todo()
    async def run(self, *a, **k): return self._todo()
    def stop(self): return self._todo()


class BybitProvider(MarketDataProvider):
    """PREPARADO, NO IMPLEMENTADO. No conectar hasta orden explícita."""

    name = "bybit"

    def _todo(self):
        raise NotImplementedError("BybitProvider no implementado")

    async def history(self, *a, **k): return self._todo()
    async def active_symbols(self): return self._todo()
    async def run(self, *a, **k): return self._todo()
    def stop(self): return self._todo()


def create_provider(settings):
    """Factoría: la estrategia nunca sabe qué proveedor hay detrás.
    'deriv' es el principal y operativo; 'coinmarketcap' es alternativo
    experimental (polling keyless). El resto lanza error explícito."""
    kind = (settings.provider or "deriv").lower()
    if kind == "deriv":
        return DerivProvider(settings.deriv_app_id, settings.deriv_token,
                             settings.poll_seconds)
    if kind == "coinmarketcap":
        from .providers_coinmarketcap import CoinMarketCapProvider, parse_ids
        return CoinMarketCapProvider(
            ids=parse_ids(getattr(settings, "cmc_ids", "")),
            poll_seconds=float(getattr(settings, "cmc_poll_seconds", 60.0)),
            api_key=getattr(settings, "cmc_api_key", ""))
    if kind == "binance":
        raise RuntimeError("Binance descartado del flujo "
                           "(ver bot/providers_binance_discarded.py)")
    raise NotImplementedError(f"Proveedor '{kind}' no implementado")
