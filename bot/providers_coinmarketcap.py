"""CoinMarketCapProvider: MarketDataProvider sobre CMC (keyless hoy, key-ready).

Evidencia viva:
- Keyless verificado: /v1/simple/price, /v3/.../listings/latest,
  /v1/cryptocurrency/map, /v1/dex/search.
- CEX OHLC keyless → 403 (no existe en catálogo keyless).
- K-line keyless = arrays [open,high,low,close,volume,ts,traders] para
  pools DEX (intervalos 1min..4h); con pools de juguete devuelve vacío.
- Sin streaming keyless → POLLING (nunca "stream").
- NO hay CMC_API_KEY configurada: modo keyless. Todo lo keyed
  (CEX OHLC con key) = PENDIENTE_DE_CONFIRMAR.

Las velas CEX se agregan en CandleEngine desde los polls; solo velas
CERRADAS llegan a estrategias. Sin pools: history() lo dice, no inventa.
"""
import asyncio
import time
import urllib.parse
from .cmc_client import CMCClient
from .log import get_logger
from .providers import MarketDataProvider

log = get_logger("cmc")

DEFAULT_IDS = {"BTC": 1, "ETH": 1027, "SOL": 5426}
# Bot-TF -> intervalo K-line CMC (docs oficiales).
KLINE_INTERVALS = {"1m": "1min", "5m": "5min", "15m": "15min",
                   "30m": "30min", "1h": "1h", "4h": "4h"}


def parse_ids(spec: str) -> dict:
    out = {}
    for chunk in (spec or "").split(","):
        if ":" in chunk:
            sym, cid = chunk.split(":", 1)
            sym, cid = sym.strip().upper(), cid.strip()
            if sym and cid.isdigit():
                out[sym] = int(cid)
    return out or dict(DEFAULT_IDS)


def _quote_usd(item: dict) -> dict:
    """Acepta quote como dict {USD:{...}} (v1/v2) o lista [{...}] (v3)."""
    q = item.get("quote", {})
    if isinstance(q, list):
        for e in q:
            if isinstance(e, dict) and e.get("symbol", "USD") == "USD":
                return e
        return q[0] if q and isinstance(q[0], dict) else {}
    if isinstance(q, dict):
        return q.get("USD", q)
    return {}


class CoinMarketCapProvider(MarketDataProvider):
    name = "coinmarketcap"
    supported_timeframes = ("1m", "5m", "15m", "30m", "1h", "4h")

    def __init__(self, ids=None, poll_seconds=60.0, timeout=10.0,
                 max_retries=4, pools=None, api_key="", client=None):
        self.client = client or CMCClient(api_key=api_key, timeout=timeout,
                                          max_retries=max_retries)
        self.ids = dict(ids or DEFAULT_IDS)  # SYMBOL -> cmc id
        self.pools = dict(pools or {})       # SYMBOL -> (platform, address) DEX
        self.poll_seconds = poll_seconds
        self.on_tick = None
        self.on_status = None
        self._stop = False
        self._last_quotes = {}  # caché compartida: último poll válido

    def stop(self):
        self._stop = True

    def _sleep(self, s):
        self.client._sleep(s)

    def _get(self, path: str):
        return self.client.get(path)

    # ---- datos ----
    def quotes(self) -> dict:
        """{SYMBOL: (price, epoch)} vía /v1/simple/price."""
        ids = ",".join(str(i) for i in self.ids.values())
        qs = urllib.parse.urlencode({"ids": ids, "convert": "USD"})
        data = self._get(f"/v1/simple/price?{qs}")
        items = data.get("data", [])
        if not items:
            raise RuntimeError("simple/price sin datos")
        by_id = {it.get("id"): it for it in items if isinstance(it, dict)}
        epoch = int(time.time())
        out = {}
        for sym, cid in self.ids.items():
            it = by_id.get(cid)
            if not it or it.get("price") is None:
                raise RuntimeError(f"activo inexistente/sin precio: {sym}")
            out[sym] = (float(it["price"]), epoch)
        self._last_quotes = out
        return out

    def listings(self, limit=100) -> list[dict]:
        qs = urllib.parse.urlencode({"start": 1, "limit": limit, "convert": "USD"})
        data = self._get(f"/v3/cryptocurrency/listings/latest?{qs}")
        out = []
        for it in data.get("data", []):
            q = _quote_usd(it)
            out.append({"id": it.get("id"), "symbol": it.get("symbol"),
                        "name": it.get("name"), "rank": it.get("cmc_rank"),
                        "price": q.get("price"), "volume_24h": q.get("volume_24h"),
                        "market_cap": q.get("market_cap"),
                        "pairs": it.get("num_market_pairs"),
                        "updated": q.get("last_updated")})
        return out

    def asset_map(self, symbol: str) -> list[dict]:
        qs = urllib.parse.urlencode({"symbol": symbol.upper(), "limit": 5})
        data = self._get(f"/v1/cryptocurrency/map?{qs}")
        return data.get("data", [])

    async def history(self, symbol, timeframe, limit=400) -> list[dict]:
        # Con key: quotes/historical intradía (5m/15m/30m/1h/4h verificados).
        # Cada punto = primer quote del intervalo → vela o=h=l=c=precio
        # (volume 0: el volume_24h rodante NO es volumen de vela). 1m NO
        # existe en CMC → solo agregación de polls.
        if self.client.api_key:
            return await self._history_keyed(symbol, timeframe, limit)
        if symbol not in self.pools:
            raise RuntimeError(
                f"OHLC no disponible para {symbol} sin key "
                f"(CEX histórico keyless → 403; K-line solo pools DEX). "
                "Use agregación de polls (CandleEngine).")
        if timeframe not in KLINE_INTERVALS:
            raise RuntimeError(f"timeframe {timeframe} sin intervalo K-line")
        platform, address = self.pools[symbol]
        qs = urllib.parse.urlencode({"platform": platform, "address": address,
                                     "interval": KLINE_INTERVALS[timeframe],
                                     "limit": min(limit, 1000)})
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, self._get, f"/v1/k-line/candles?{qs}")
        out = []
        for k in data.get("data", []):
            try:  # formato real docs: [o,h,l,c,vol,ts,traders]
                if not isinstance(k, (list, tuple)) or len(k) < 6:
                    continue
                out.append({"epoch": int(k[5]), "open": float(k[0]),
                            "high": float(k[1]), "low": float(k[2]),
                            "close": float(k[3]), "volume": float(k[4])})
            except (TypeError, ValueError):
                continue  # incompletas: jamás mezclar con cerradas
        return sorted(out, key=lambda c: c["epoch"])

    async def _history_keyed(self, symbol, timeframe, limit=400) -> list[dict]:
        import datetime
        cid = self.ids.get(symbol.upper())
        if not cid:
            raise RuntimeError(f"CMC sin id para {symbol}")
        iv = {"5m": "5m", "15m": "15m", "30m": "30m",
              "1h": "1h", "4h": "4h"}.get(timeframe)
        if not iv:
            raise RuntimeError(f"timeframe {timeframe} no soportado por CMC "
                               f"(1m no existe; verificado en docs)")
        qs = urllib.parse.urlencode({"id": cid, "convert": "USD",
                                     "count": min(limit, 1000),
                                     "interval": iv})
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, self._get, f"/v3/cryptocurrency/quotes/historical?{qs}")
        quotes = ((data.get("data") or {}).get(str(cid)) or {}).get("quotes", [])
        out = []
        for qp in quotes:
            try:
                ts = qp.get("timestamp", "")
                ep = int(datetime.datetime.fromisoformat(
                    ts.replace("Z", "+00:00")).timestamp())
                px = float((qp.get("quote") or {}).get("USD", {}).get("price"))
                out.append({"epoch": ep, "open": px, "high": px, "low": px,
                            "close": px, "volume": 0.0})
            except (TypeError, ValueError):
                continue  # incompletas: jamás mezclar con cerradas
        return sorted(out, key=lambda c: c["epoch"])

    async def active_symbols(self) -> list[str]:
        return sorted(self.ids)

    def capabilities(self):
        from .providers import ProviderCapabilities
        keyed = bool(self.client.api_key)
        return ProviderCapabilities(
            ticks=True, ohlc=False, history=keyed or bool(self.pools), streaming=False,
            reconnect=True, volume=True, spread=False, websocket=False,
            historical_candles=keyed or bool(self.pools),
            credentials_required=keyed)

    async def run(self, symbols):
        wanted = [s.upper() for s in symbols if s.upper() in self.ids]
        if not wanted:
            raise RuntimeError(f"CMC sin ids para {symbols} (catálogo/CMC_IDS)")
        loop = asyncio.get_running_loop()
        if self.on_status:
            self.on_status({"connected": True, "mode": "polling",
                            "provider": self.name})
        log.info("CMC polling cada %ss: %s", self.poll_seconds, wanted)
        while not self._stop:
            try:
                quotes = await loop.run_in_executor(None, self.quotes)
                # Recolectar ticks EN el thread pool, entregar en el event loop
                # (asyncio.Queue.put_nowait NO es thread-safe)
                pending = []
                for s in wanted:
                    if s in quotes:
                        pending.append((s, *quotes[s]))
                for sym, price, epoch in pending:
                    if self.on_tick:
                        self.on_tick(sym, price, epoch)
            except Exception as e:
                log.warning("CMC poll: %s", str(e)[:150])
                if self.on_status:
                    self.on_status({"connected": True, "mode": "polling",
                                    "provider": self.name,
                                    "error": str(e)[:120]})
            for _ in range(int(self.poll_seconds * 10)):
                if self._stop:
                    break
                await asyncio.sleep(0.1)
        if self.on_status:
            self.on_status({"connected": False, "mode": "polling",
                            "provider": self.name})
