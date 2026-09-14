"""StrategyIndex: orquestador multibot multi-proveedor.

TICK -> CandleEngine -> CANDLE CLOSED -> Indicators -> Strategy ->
Signal -> BrokerInterface (SimulatedBroker por bot).

Cada bot indica su provider. El orchestrator corre un provider por tipo
(CMC, Yahoo, etc.) en paralelo. Un fallo en un bot no detiene a los demas.
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


def default_bots(provider="deriv"):
    return []


class Orchestrator:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = Store(settings.db_path, database_url=settings.database_url)
        self._seed_db()
        self.bots: dict[str, BotRuntime] = {}
        self._load_bots()
        self.prices: dict[str, dict] = {}
        self.conn: dict[str, dict] = {}
        self.ui: asyncio.Queue = asyncio.Queue()
        self._providers: dict = {}
        self._provider_tasks: list = []
        self._build_providers()

    # ---- providers ----
    def _build_providers(self):
        seen = {}
        for rt in self.bots.values():
            pname = rt.bot.provider
            if pname not in seen:
                prov = create_provider(self._make_settings(pname))
                prov.on_tick = self._tick
                prov.on_status = lambda info, pn=pname: self._status(pn, info)
                self._providers[pname] = prov
                self.conn[pname] = {"connected": False, "mode": "unknown",
                                     "provider": pname}
                seen[pname] = prov

    def _make_settings(self, provider_name):
        s = Settings()
        s.provider = provider_name
        return s

    def _get_provider(self, provider_name):
        if provider_name not in self._providers:
            prov = create_provider(self._make_settings(provider_name))
            prov.on_tick = self._tick
            prov.on_status = lambda info, pn=provider_name: self._status(pn, info)
            self._providers[provider_name] = prov
            self.conn[provider_name] = {"connected": False, "mode": "unknown",
                                         "provider": provider_name}
        return self._providers[provider_name]

    def _provider_for_bot(self, bot):
        return self._get_provider(bot.provider)

    # ---- bots ----
    def _seed_db(self):
        pass

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
        if getattr(self, "_cmc_catalog", None) is None:
            from .cmc_client import CMCClient
            from .cmc_catalog import AssetCatalog
            client = CMCClient(api_key=self.s.cmc_api_key)
            self._cmc_catalog = AssetCatalog(
                client, self.db, limit=self.s.cmc_catalog_limit)
        return self._cmc_catalog

    def _sync_cmc_ids(self):
        prov = self._providers.get("coinmarketcap")
        if prov is None:
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
        elif provider == "yahoo":
            symbol = symbol.upper()
        b = BotInstance(f"bot-{uuid.uuid4().hex[:8]}", strategy_key, symbol, provider,
                        timeframe, capital, lot, max_positions, "running",
                        int(time.time()), spread, commission, True, ref)
        self.db.save_bot(b)
        rt = BotRuntime(b, self.db, on_event=self._push)
        rt.broker.price_fn = lambda sym: (self.prices.get(sym) or {}).get("price")
        self.bots[b.bot_id] = rt
        self.db.upsert_market(symbol, 1, 0)
        self._sync_cmc_ids()
        asyncio.create_task(self.seed_bot(b.bot_id))
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

    # ---- seeding ----
    async def seed_history(self):
        for bid in list(self.bots):
            await self.seed_bot(bid)

    async def seed_bot(self, bot_id):
        rt = self.bots.get(bot_id)
        if not rt:
            raise KeyError("bot inexistente")
        if rt.bot.status != "running":
            rt.state = "STOPPED"
            return 0
        if rt.builder.closed:
            return len(rt.builder.closed)
        prov = self._provider_for_bot(rt.bot)
        try:
            cs = await prov.history(rt.bot.symbol, rt.bot.timeframe, 400)
            rt.builder.seed(cs)
            rt.state = "WAITING_FOR_MARKET_DATA"
            log.info("seed %s %s: %d velas (bot %s en espera de mercado)",
                     rt.bot.symbol, rt.bot.timeframe, len(cs), rt.bot.bot_id)
            return len(cs)
        except Exception as e:
            msg = str(e)[:120]
            rt.state = "WAITING_FOR_MARKET_DATA"
            log.info("seed %s %s: sin historico (%s); acumula desde polls",
                     rt.bot.symbol, rt.bot.timeframe, msg)
            return 0

    # ---- provider status ----
    def _status(self, provider_name, info):
        was = self.conn.get(provider_name, {}).get("connected")
        if provider_name not in self.conn:
            self.conn[provider_name] = {"connected": False, "mode": "unknown",
                                         "provider": provider_name}
        self.conn[provider_name].update(
            {k: v for k, v in info.items() if k in ("connected", "mode", "error")})
        self._push("status", {"conn": self.conn})

    async def _resync(self):
        pass

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
            return
        closes = rt.builder.closes()
        for ref in list(rt.broker.positions):
            p = rt.broker.positions.get(ref)
            if p and rt.strategy.exit(closes, p.side):
                self._signal(rt, candle, "EXIT", f"cierre {rt.strategy_name}")
                log.info("%s %s %s EXIT %s @ %.5f", rt.bot.bot_id, rt.bot.symbol,
                         rt.bot.timeframe, p.side, candle.close)
                rt.broker.close_position(p.id, candle.close, f"{rt.strategy_name} exit")
        direction = rt.strategy.entry(closes)
        if direction:
            if rt.can_open():
                self._signal(rt, candle, direction, f"cruce {rt.strategy_name}")
                log.info("%s %s %s SENAL %s @ %.5f", rt.bot.bot_id, rt.bot.symbol,
                         rt.bot.timeframe, direction, candle.close)
                rt.broker.place_order(rt.bot.symbol, direction, rt.bot.lot,
                                      candle.close, rt.open_ref(),
                                      bot_id=rt.bot.bot_id, extra=rt.snapshot_extra())
            else:
                log.info("vela %s %s: senal %s omitida (max_positions=%d)",
                         rt.bot.symbol, rt.bot.timeframe, direction, rt.bot.max_positions)
        else:
            log.info("[%s] %s %s %s vela cerrada: %s sin senal (cierre=%.5f)",
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

    # ---- stats ----
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
        providers_seen = set()
        for rt in self.bots.values():
            pname = rt.bot.provider
            if pname not in providers_seen:
                providers_seen.add(pname)
                prov = self._get_provider(pname)
                syms = [r.bot.symbol for r in self.bots.values()
                        if r.bot.provider == pname and r.bot.status == "running"]
                if syms:
                    self._provider_tasks.append(
                        asyncio.create_task(self._run_one_provider(prov, syms, pname)))
        if self._provider_tasks:
            await asyncio.gather(*self._provider_tasks, return_exceptions=True)
        else:
            log.info("sin bots activos, provider no arranca")

    async def _run_one_provider(self, prov, syms, pname):
        try:
            log.info("arrancando provider %s para %d bots: %s", pname, len(syms), syms)
            await prov.run(syms)
        except asyncio.CancelledError:
            log.info("provider %s cancelado", pname)
        except Exception as e:
            log.warning("provider %s fallo: %s", pname, str(e)[:200])
