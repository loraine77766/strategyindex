"""Yahoo Finance provider: stocks, forex, commodities via yfinance.

Polling-based (no streaming). Free, no API key needed.
Timeframes: 1m, 5m, 15m, 30m, 1h, 4h (yfinance interval mapping).
"""
import asyncio
import time

from .providers import MarketDataProvider, ProviderCapabilities
from .log import get_logger

log = get_logger("yahoo")

YAHOO_SYMBOLS = {
    "TSLA": "TSLA",
    "AAPL": "AAPL",
    "NVDA": "NVDA",
    "AMZN": "AMZN",
    "GOOG": "GOOG",
    "MSFT": "MSFT",
    "META": "META",
    "NDX": "^NDX",
    "SPX": "^GSPC",
    "DJI": "^DJI",
    "IXIC": "^IXIC",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "OIL": "CL=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}

TF_MAP = {
    "1m": ("1m", "1d"),
    "5m": ("5m", "5d"),
    "15m": ("15m", "1mo"),
    "30m": ("30m", "1mo"),
    "1h": ("1h", "3mo"),
    "4h": ("1h", "6mo"),
}


class YahooFinanceProvider(MarketDataProvider):
    name = "yahoo"
    supported_timeframes = ("1m", "5m", "15m", "30m", "1h", "4h")

    def __init__(self, poll_seconds=30.0):
        self.poll_seconds = poll_seconds
        self.on_tick = None
        self.on_status = None
        self._stop = False
        self._active_ids = {}

    def stop(self):
        self._stop = True

    def resolve_symbol(self, symbol):
        upper = symbol.upper()
        if upper in YAHOO_SYMBOLS:
            return YAHOO_SYMBOLS[upper]
        if upper.startswith("^"):
            return upper
        return symbol

    async def history(self, symbol, timeframe, limit=400) -> list[dict]:
        import yfinance as yf
        interval, period = TF_MAP.get(timeframe, ("1h", "3mo"))
        yahoo_sym = self.resolve_symbol(symbol)
        loop = __import__("asyncio").get_running_loop()

        def _fetch():
            tk = yf.Ticker(yahoo_sym)
            df = tk.history(period=period, interval=interval)
            if df.empty:
                return []
            out = []
            for idx, row in df.iterrows():
                out.append({
                    "epoch": int(idx.timestamp()),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row.get("Volume", 0)),
                })
            return out[-limit:]

        return await loop.run_in_executor(None, _fetch)

    async def active_symbols(self) -> list[str]:
        return sorted(YAHOO_SYMBOLS.keys())

    def capabilities(self):
        return ProviderCapabilities(
            ticks=True, ohlc=True, history=True,
            streaming=False, reconnect=True, volume=True,
            spread=False, websocket=False,
            historical_candles=True, credentials_required=False,
        )

    async def run(self, symbols):
        import yfinance as yf
        wanted = {}
        for s in symbols:
            upper = s.upper()
            yahoo = YAHOO_SYMBOLS.get(upper)
            if yahoo:
                wanted[upper] = yahoo
        if not wanted:
            log.warning("Yahoo: sin symbols válidos de %s", symbols)
            return
        if self.on_status:
            self.on_status({"connected": True, "mode": "polling",
                            "provider": self.name})
        log.info("Yahoo polling cada %ss: %s", self.poll_seconds,
                 list(wanted.keys()))
        while not self._stop:
            try:
                loop = asyncio.get_running_loop()
                for display, yahoo_sym in wanted.items():
                    if self._stop:
                        break
                    try:
                        def _price(sym=yahoo_sym):
                            tk = yf.Ticker(sym)
                            info = tk.fast_info
                            return getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                        price = await loop.run_in_executor(None, _price)
                        if price and float(price) > 0:
                            epoch = int(time.time())
                            if self.on_tick:
                                self.on_tick(display, float(price), epoch)
                    except Exception as e:
                        log.warning("Yahoo poll %s: %s", display, str(e)[:100])
            except Exception as e:
                log.warning("Yahoo run: %s", str(e)[:150])
            for _ in range(int(self.poll_seconds * 10)):
                if self._stop:
                    break
                await asyncio.sleep(0.1)
        if self.on_status:
            self.on_status({"connected": False, "mode": "polling",
                            "provider": self.name})
