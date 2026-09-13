"""StrategyIndex: orquestador multibot.

TICK -> CandleEngine -> CANDLE CLOSED -> Indicators -> Strategy ->
Signal -> BrokerInterface (SimulatedBroker por bot).

TICKS = DATOS. VELAS CERRADAS = DECISIONES.
Cada bot: capital, lote, max_positions, broker, builder y stats propios.
Un fallo en un bot no detiene a los demás (try/except por bot).
"""
import asyncio
import time
from .bots import BotInstance, BotRuntime, CATALOG
from .candles import Candle
from .config import Settings
from .database import Store
from .log import get_logger
from .providers import create_provider
from .stats import compute_stats
from .strategies import Signal

log = get_logger("core")


def default_bots():
    now = int(time.time())
    return [
        BotInstance("irk-xau-5m", "irk", "frxXAUUSD", "deriv", "5m",
                    50.0, 0.01, 1, "running", now),
        BotInstance("irkp-xau-15m", "irk_plus", "frxXAUUSD", "deriv", "15m",
                    50.0, 0.01, 1, "running", now),
        BotInstance("irk-ndx-5m", "irk", "OTC_NDX", "deriv", "5m",
                    50.0, 0.01, 1, "running", now),
    ]


class Orchestrator:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = Store(settings.db_path, database_url=settings.database_url)
        self._seed_db()
        self.bots: dict[str, BotRuntime] = {}
        self._load_bots()
        self.provider = create_provider(settings)  # hoy: DerivProvider
        self.provider.on_tick = self._tick
        self.provider.on_status = self._status
        self.prices: dict[str, dict] = {}
        self.conn = {"connected": False, "mode": "unknown",
                     "provider": self.provider.name}
        self.ui: asyncio.Queue = asyncio.Queue()

    # ---- bots ----
    def _seed_db(self):
        for sym in self.s.symbols:
            self.db.upsert_market(sym, 1, 1 if sym == "frxXAUUSD" else 0)
        if not self.db.bots():
            legacy = self.db.assignments()
            if legacy:
                # Migración única: assignments -> bots (ema72_150→irk 75/150,
                # ema30_72_150→irk_plus 30/75/150 según especificación nueva).
                now = int(time.time())
                n = 0
                for r in legacy:
                    if not r["onoff"]:
                        continue
                    key = "irk" if r["strategy"] == "ema72_150" else "irk_plus"
                    b = BotInstance(f"mig-{r['id']}", key, r["symbol"], "deriv",
                                    r["timeframe"], 50.0, 0.01, 1, "running", now)
                    self.db.save_bot(b)
                    n += 1
                log.info("migrados %d assignments a bots", n)
            else:
                for b in default_bots():
                    if b.symbol in self.s.symbols:
                        self.db.save_bot(b)

    def _load_bots(self):
        for r in self.db.bots():
            b = BotInstance(r["bot_id"], r["strategy_key"], r["symbol"], r["provider"],
                            r["timeframe"], r["capital"], r["lot"], r["max_positions"],
                            r["status"], r["created"], r["spread"], r["commission"],
                            bool(r["costs_assumed"]),
                            r.get("provider_ref", "") or "")
            rt = BotRuntime(b, self.db, on_event=self._push)
            rt.broker.price_fn = lambda sym: (self.prices.get(sym) or {}).get("price")
            rt.broker.restore(self.db.open_trades(b.bot_id))
            self.bots[b.bot_id] = rt
        self._sync_cmc_ids()

    def _cmc(self):
        """Una sola instancia CMC compartida (caché y rate-limit únicos)."""
        if getattr(self, "_cmc_catalog", None) is None:
            from .cmc_client import CMCClient
            from .cmc_catalog import AssetCatalog
            client = CMCClient(api_key=self.s.cmc_api_key)
            self._cmc_catalog = AssetCatalog(
                client, self.db, limit=self.s.cmc_catalog_limit)
        return self._cmc_catalog

    def _sync_cmc_ids(self):
        """Resuelve una vez los ids CMC de los bots y los reutiliza."""
        prov = getattr(self, "provider", None)
        if prov is None or prov.name != "coinmarketcap":
            return
        for rt in self.bots.values():
            if rt.bot.provider == "coinmarketcap" and rt.bot.provider_ref:
                try:
                    prov.ids[rt.bot.symbol.upper()] = int(rt.bot.provider_ref)
                except ValueError:
                    pass

    def resolve_asset(self, query):
        return self._cmc().resolve(query)

    def sync_catalog(self, limit=None):
        return self._cmc().sync(limit)

    def create_bot(self, strategy_key, symbol, provider, timeframe, capital=50.0,
                   lot=0.01, max_positions=1, spread=0.0001, commission=0.0005):
        import uuid
        if strategy_key not in CATALOG:
            raise ValueError("estrategia desconocida")
        if timeframe not in ("1m", "5m", "15m", "30m", "1h", "4h"):
            raise ValueError("timeframe no soportado")
        ref = ""
        if provider == "coinmarketcap":
            hit = self._cmc().resolve(symbol)
            if not hit:
                raise ValueError(f"activo CMC no disponible: {symbol}")
            ref = str(hit["cmc_id"])
            symbol = (hit["symbol"] or symbol).upper()
        b = BotInstance(f"bot-{uuid.uuid4().hex[:8]}", strategy_key, symbol, provider,
                        timeframe, capital, lot, max_positions, "running",
                        int(time.time()), spread, commission, True, ref)
        self.db.save_bot(b)
        rt = BotRuntime(b, self.db, on_event=self._push)
        rt.broker.price_fn = lambda sym: (self.prices.get(sym) or {}).get("price")
        self.bots[b.bot_id] = rt
        self.db.upsert_market(symbol, 1, 0)
        self._sync_cmc_ids()
        return b

    def update_bot(self, bot_id, **kw):
        rt = self.bots.get(bot_id)
        if not rt:
            raise KeyError("bot inexistente")
        if "timeframe" in kw and kw["timeframe"] != rt.bot.timeframe:
            raise ValueError("el timeframe de un bot NO puede cambiarse (crea otro bot)")
        for k in ("capital", "lot", "max_positions", "status", "spread", "commission"):
            if k in kw and kw[k] is not None:
                setattr(rt.bot, k, kw[k])
        rt.broker.qty = rt.bot.lot
        rt.broker.commission = rt.bot.commission
        rt.broker.spread = rt.bot.spread
        self.db.save_bot(rt.bot)
        return rt.bot

    # ---- provider ----
    async def seed_history(self):
        # Histórico inicial: prepara velas+indicadores SIN evaluar ni operar.
        # El bot solo evaluará velas cerradas NUEVAS desde este punto.
        for bid in list(self.bots):
            await self.seed_bot(bid)

    async def seed_bot(self, bot_id):
        """Seed bajo demanda (bots creados por API tras el arranque)."""
        rt = self.bots.get(bot_id)
        if not rt:
            raise KeyError("bot inexistente")
        if rt.bot.status != "running":
            rt.state = "STOPPED"
            return 0
        if rt.builder.closed:
            return len(rt.builder.closed)  # ya sembrado: no duplicar
        try:
            cs = await self.provider.history(rt.bot.symbol, rt.bot.timeframe, 400)
            rt.builder.seed(cs)
            rt.state = "WAITING_FOR_MARKET_DATA"
            log.info("seed %s %s: %d velas (bot %s en espera de mercado)",
                     rt.bot.symbol, rt.bot.timeframe, len(cs), rt.bot.bot_id)
            return len(cs)
        except Exception as e:
            msg = str(e)[:120]
            if "OHLC" in msg or "pool" in msg.lower():
                # Sin histórico disponible: se acumula desde polls en vivo.
                rt.state = "WAITING_FOR_MARKET_DATA"
                log.info("seed %s %s: sin histórico (%s); acumula desde polls",
                         rt.bot.symbol, rt.bot.timeframe, msg)
                return 0
            rt.state = "ERROR"
            log.warning("seed %s %s fallo: %s", rt.bot.symbol, rt.bot.timeframe, msg)
            return 0

    def _status(self, info):
        was = self.conn.get("connected")
        self.conn.update({k: v for k, v in info.items() if k in ("connected", "mode", "error")})
        self._push("status", {"conn": self.conn})
        if info.get("connected") and not was:
            # Solo al (re)conectar de verdad: evita resyncs en cada poll con error.
            for rt in self.bots.values():
                if rt.bot.status == "running" and rt.state == "RECONNECTING":
                    rt.state = "WAITING_FOR_MARKET_DATA"
            asyncio.create_task(self._resync())
        elif "connected" in info and not info.get("connected"):
            # Solo desconexión real; los avisos de modo (poll/stream) no cambian estado.
            for rt in self.bots.values():
                if rt.state in ("RUNNING", "WAITING_FOR_MARKET_DATA"):
                    rt.state = "RECONNECTING"
                    log.info("bot %s %s: RECONNECTING", rt.bot.bot_id, rt.bot.timeframe)

    async def _resync(self):
        """Tras reconectar: solo historial (continuidad). Nunca genera señal."""
        for bid, rt in self.bots.items():
            if rt.bot.status != "running":
                continue
            try:
                cs = await self.provider.history(rt.bot.symbol, rt.bot.timeframe, 10)
                b = rt.builder
                last = b.closed[-1].open_epoch if b.closed else 0
                existing = {c.open_epoch for c in b.closed}
                new = [c for c in cs if c["epoch"] > last and c["epoch"] not in existing]
                for c in new:
                    b.closed.append(Candle(rt.bot.symbol, rt.bot.timeframe, c["epoch"],
                                           c["open"], c["high"], c["low"], c["close"],
                                           c.get("volume", 0.0), True))
                    self.db.mark_processed(f"{rt.bot.symbol}|{rt.bot.timeframe}|{c['epoch']}")
                if new:
                    log.info("resync %s %s: %d velas (solo historial)", rt.bot.symbol, rt.bot.timeframe, len(new))
            except Exception as e:
                msg = str(e)[:120] or type(e).__name__
                log.warning("resync %s %s fallo: %s", rt.bot.symbol, rt.bot.timeframe, msg)

    # ---- flujo principal ----
    def _tick(self, symbol, price, epoch):
        try:
            prev = self.prices.get(symbol)
            base = prev.get("base", price) if prev else price
            self.prices[symbol] = {"price": price, "epoch": epoch, "base": base,
                                   "change": (price - base) / base * 100 if base else 0}
            self._push("tick", {"symbol": symbol, "price": price, "epoch": epoch})
            for bid, rt in self.bots.items():
                if rt.bot.symbol != symbol or rt.bot.status != "running":
                    continue
                try:
                    first = rt.state in ("STARTING", "WAITING_FOR_MARKET_DATA")
                    for candle in rt.builder.on_tick(price, epoch):
                        self._on_closed_candle(rt, candle)
                    rt.last_tick = epoch
                    if first:
                        rt.state = "RUNNING"
                        log.info("bot %s %s %s RUNNING (mercado en vivo)",
                                 rt.bot.bot_id, rt.bot.symbol, rt.bot.timeframe)
                    rt.last_candle = (rt.builder.closed[-1].open_epoch
                                      if rt.builder.closed else 0)
                except Exception as e:
                    rt.state = "ERROR"
                    log.warning("bot %s error: %s", bid, str(e)[:150])
        except Exception as e:
            log.warning("tick error: %s", str(e)[:150])

    def _on_closed_candle(self, rt: BotRuntime, candle):
        if self.db.is_processed(candle.uid):
            return  # una vela jamás se procesa dos veces
        closes = rt.builder.closes()
        # salidas: cada posición abierta se evalúa (independientes)
        for ref in list(rt.broker.positions):
            p = rt.broker.positions.get(ref)
            if p and rt.strategy.exit(closes, p.side):
                self._signal(rt, candle, "EXIT", f"cierre {rt.strategy_name}")
                log.info("%s %s %s EXIT %s @ %.5f", rt.bot.bot_id, rt.bot.symbol,
                         rt.bot.timeframe, p.side, candle.close)
                rt.broker.close_position(p.id, candle.close, f"{rt.strategy_name} exit")
        # entrada: como máximo una por vela cerrada y si hay cupo
        direction = rt.strategy.entry(closes)
        if direction:
            if rt.can_open():
                self._signal(rt, candle, direction, f"cruce {rt.strategy_name}")
                log.info("%s %s %s SEÑAL %s @ %.5f", rt.bot.bot_id, rt.bot.symbol,
                         rt.bot.timeframe, direction, candle.close)
                rt.broker.place_order(rt.bot.symbol, direction, rt.bot.lot,
                                      candle.close, rt.open_ref(),
                                      bot_id=rt.bot.bot_id, extra=rt.snapshot_extra())
            else:
                log.info("vela %s %s: señal %s omitida (max_positions=%d)",
                         rt.bot.symbol, rt.bot.timeframe, direction, rt.bot.max_positions)
        else:
            log.info("[%s] %s %s %s vela cerrada: %s sin señal (cierre=%.5f)",
                     candle.open_epoch, rt.bot.bot_id, rt.bot.symbol,
                     rt.bot.timeframe, rt.strategy_name, candle.close)
        self.db.mark_processed(candle.uid)

    def _signal(self, rt, candle, direction, reason):
        if direction in ("BUY", "SELL"):
            s = Signal(rt.bot.bot_id, rt.bot.symbol, rt.bot.timeframe, direction,
                       candle.open_epoch, candle.close, reason)
            self.db.signal(s)
            self._push("signal", {"bot_id": rt.bot.bot_id,
                                  "strategy": rt.strategy_name,
                                  "symbol": rt.bot.symbol,
                                  "timeframe": rt.bot.timeframe,
                                  "direction": direction, "price": candle.close,
                                  "reason": reason})

    def _push(self, kind, data=None):
        try:
            self.ui.put_nowait({"kind": kind, "data": data or {}})
        except Exception:
            pass

    # ---- stats por bot ----
    def bot_stats(self, bot_id):
        rt = self.bots.get(bot_id)
        if not rt:
            raise KeyError("bot inexistente")
        closed = self.db.trade_history(10000, bot_id)
        opens = []
        for p in rt.broker.positions.values():
            cur = (self.prices.get(p.symbol) or {}).get("price", p.entry)
            pnl = ((cur - p.entry) if p.side == "LONG" else (p.entry - cur)) * p.qty
            opens.append({"direction": p.side, "entry": p.entry, "qty": p.qty, "pnl": pnl,
                          "id": p.id, "open_epoch": p.open_epoch})
        return compute_stats(rt.bot.capital, closed, opens, None)

    async def run_client(self):
        # Vía autenticada Deriv (PENDIENTE_DE_CONFIRMAR): requiere App ID
        # registrado + token clásico. Otros proveedores la omiten.
        otp_url = None
        c = getattr(self.provider, "client", None)
        if (self.provider.name == "deriv" and c is not None
                and getattr(c, "token", "")
                and getattr(c, "app_id", "") not in ("", "1089", "1")):
            try:
                loop = asyncio.get_running_loop()
                otp_url, aid = await loop.run_in_executor(None, c.otp_connect_url)
                log.info("OTP OK cuenta %s", aid)
            except Exception as e:
                log.warning("OTP fallo (%s); modo clásico", str(e)[:150])
        if self.provider.name == "deriv":
            await self.provider.run(self.s.symbols, otp_url)
        else:
            await self.provider.run(self.s.symbols)
