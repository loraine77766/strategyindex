"""Cliente Deriv: WebSocket clásico + flujo nuevo PAT→REST→OTP→WS.

Honestidad comprobada (deriv_prueba/RESULTADOS.md):
- Sin auth: active_symbols=[] y `ticks+subscribe` -> InvalidSymbol.
  Funciona one-shot: ticks_history (ticks y candles).
- Con PAT en WS clásico: InvalidToken. La vía nueva requiere App-ID
  registrado (DERIV_APP_ID) para REST/OTP.
Modos resultantes: STREAM (ticks push) o POLL (one-shot periódico).
Nunca se finge streaming.
"""
import asyncio
import json
import urllib.request
from .log import get_logger

log = get_logger("market")
CLASSIC_WS = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"
REST_BASE = "https://api.derivws.com"
# Granularidades que Deriv demostró entregar (ticks_history, style candles).
GRAN_TF = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
GRAN_ALL = [60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400]
GRAN = GRAN_TF


class DerivClient:
    def __init__(self, app_id="1089", token="", poll_seconds=5.0):
        self.app_id = app_id
        self.token = token
        self.poll_seconds = poll_seconds
        self.on_tick = None          # cb(symbol, price, epoch)
        self.on_status = None        # cb(dict)
        self.mode = "unknown"        # stream | poll
        self.symbols: list[str] = []
        self._subs: set[str] = set()
        self._stop = False
        self._req = 0
        self._lock = None    # asyncio.Lock por conexión
        self._inbox = None   # asyncio.Queue por conexión
        self._pump_task = None

    def _rid(self):
        self._req += 1
        return self._req

    def _rest(self, method, path):
        req = urllib.request.Request(
            REST_BASE + path, method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Deriv-App-ID": self.app_id})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    # ---- flujo nuevo PAT -> REST -> OTP ----
    def otp_connect_url(self):
        """Devuelve (ws_url, account_id) usando PAT+App-ID. Lanza si falla."""
        accs = self._rest("GET", "/trading/v1/options/accounts")
        items = accs.get("data", accs if isinstance(accs, list) else [])
        if isinstance(accs, dict) and "data" in accs:
            items = accs["data"]
        demo = [a for a in items if a.get("is_demo") or "demo" in str(a).lower()]
        acct = (demo or items)[0]
        aid = acct.get("id") or acct.get("accountId") or acct.get("loginid")
        otp = self._rest("POST", f"/trading/v1/options/accounts/{aid}/otp")
        url = (otp.get("data") or {}).get("url") or otp.get("url")
        return url, aid

    async def _ensure_pump(self, ws):
        """Crea inbox/lock/pump perezosamente (seed/resync usan conexiones
        propias fuera de run())."""
        if self._inbox is None or self._lock is None:
            self._inbox = asyncio.Queue()
            self._lock = asyncio.Lock()
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump(ws))

    async def _call(self, ws, payload, timeout=15):
        """Request/response apareados por req_id con un único lector.
        Sin esto, el pong del keepalive le roba la respuesta al poll."""
        await self._ensure_pump(ws)
        payload = dict(payload)
        payload["req_id"] = self._rid()
        async with self._lock:
            await ws.send(json.dumps(payload))
            t_end = asyncio.get_event_loop().time() + timeout
            while True:
                rest = t_end - asyncio.get_event_loop().time()
                if rest <= 0:
                    raise asyncio.TimeoutError(f"sin respuesta a {payload.get('ticks_history') or payload.get('ticks') or payload}")
                m = await asyncio.wait_for(self._inbox.get(), rest)
                if self._is_live_tick(m):
                    self._route_tick(m)
                    continue
                if (m.get("req_id") == payload["req_id"]
                        or m.get("echo_req", {}).get("req_id") == payload["req_id"]):
                    return m
                # respuesta ajena (no debería ocurrir con lock): se ignora
                log.warning("respuesta inesperada: %s", str(m)[:150])
            await ws.send(json.dumps(payload))
            t_end = asyncio.get_event_loop().time() + timeout
            while True:
                rest = t_end - asyncio.get_event_loop().time()
                if rest <= 0:
                    raise asyncio.TimeoutError(f"sin respuesta a {payload.get('ticks_history') or payload.get('ticks') or payload}")
                m = await asyncio.wait_for(self._inbox.get(), rest)
                if self._is_live_tick(m):
                    self._route_tick(m)
                    continue
                if (m.get("req_id") == payload["req_id"]
                        or m.get("echo_req", {}).get("req_id") == payload["req_id"]):
                    return m
                # respuesta ajena (no debería ocurrir con lock): se ignora
                log.warning("respuesta inesperada: %s", str(m)[:150])

    @staticmethod
    def _is_live_tick(m):
        return (m.get("msg_type") == "tick" and "tick" in m
                and "echo_req" not in m)

    def _route_tick(self, m):
        t = m.get("tick", {})
        if self.on_tick and t.get("symbol") in self._subs:
            self.on_tick(t["symbol"], t["quote"], t["epoch"])

    async def _pump(self, ws):
        """Único lector del socket: todo mensaje va al inbox."""
        try:
            while not self._stop:
                self._inbox.put_nowait(json.loads(await ws.recv()))
        except Exception:
            pass

    async def active_symbols(self, ws):
        try:
            r = await self._call(ws, {"active_symbols": "brief"})
            return [s.get("symbol") or s.get("underlying_symbol")
                    for s in r.get("active_symbols", [])]
        except Exception:
            return []

    async def history_candles(self, ws, symbol, timeframe, count=300):
        r = await self._call(ws, {"ticks_history": symbol, "end": "latest",
                                  "count": count, "style": "candles",
                                  "granularity": GRAN[timeframe]})
        if "error" in r:
            raise RuntimeError(r["error"].get("code"))
        return r.get("candles", [])

    async def latest_tick(self, ws, symbol):
        r = await self._call(ws, {"ticks_history": symbol, "end": "latest",
                                  "count": 2, "style": "ticks"})
        if "error" in r:
            raise RuntimeError(r["error"].get("code"))
        # Deriv a veces responde sin historial (mercado cerrado/hipo):
        # no asumir forma; validar antes de indexar.
        h = r.get("history") or {}
        px, tm = h.get("prices") or [], h.get("times") or []
        if not px or not tm:
            raise RuntimeError(f"sin datos para {symbol} (respuesta: "
                               f"{str(r)[:150]})")
        return px[-1], tm[-1]

    async def run(self, symbols, otp_url=None):
        """Bucle principal con reconexión+backoff+ping+resubscripción."""
        import websockets
        self._subs = set(symbols)
        backoff = 5
        while not self._stop:
            try:
                url = otp_url or CLASSIC_WS.format(app_id=self.app_id)
                async with websockets.connect(url, max_size=2 ** 22) as ws:
                    backoff = 5
                    self._inbox = asyncio.Queue()
                    self._lock = asyncio.Lock()
                    pump = asyncio.create_task(self._pump(ws))
                    try:
                        self._emit({"connected": True, "url": url.split("?")[0]})
                        asyncio.create_task(self._ping(ws))
                        syms = await self.active_symbols(ws) if not otp_url else []
                        if syms:
                            self.symbols = syms
                        await self._subscribe_all(ws)
                        if self.mode == "stream":
                            await self._read_loop(ws)
                        else:
                            await self._poll_loop(ws)
                    finally:
                        pump.cancel()
            except Exception as e:
                self._emit({"connected": False, "error": str(e)[:200]})
                log.warning("desconectado (%s). Reintento en %ss", str(e)[:120], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    async def _ping(self, ws):
        try:
            while not self._stop:
                await asyncio.sleep(30)
                await self._call(ws, {"ping": 1})  # apareado por req_id
        except Exception:
            pass

    async def _subscribe_all(self, ws):
        ok = 0
        for s in self._subs:
            try:
                r = await self._call(ws, {"ticks": s, "subscribe": 1})
                if "error" in r:
                    log.warning("subscribe %s rechazado: %s", s, r["error"].get("code"))
                else:
                    ok += 1
            except Exception as e:
                log.warning("subscribe %s fallo: %s", s, e)
        self.mode = "stream" if ok else "poll"
        self._emit({"mode": self.mode, "streamed": ok, "total": len(self._subs)})
        log.info("modo datos: %s (%d/%d suscripciones)", self.mode, ok, len(self._subs))

    async def _read_loop(self, ws):
        while not self._stop:
            m = await self._inbox.get()
            if self._is_live_tick(m):
                self._route_tick(m)

    async def _poll_loop(self, ws):
        while not self._stop:
            for s in list(self._subs):
                try:
                    price, epoch = await self.latest_tick(ws, s)
                    if self.on_tick:
                        self.on_tick(s, price, epoch)
                except Exception as e:
                    log.warning("poll %s: %s", s, str(e)[:120])
            await asyncio.sleep(self.poll_seconds)

    def _emit(self, info):
        if self.on_status:
            self.on_status(info)

    def stop(self):
        self._stop = True
