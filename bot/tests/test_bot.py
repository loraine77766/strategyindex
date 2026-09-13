"""Pruebas deterministas (sin red). Regla: velas cerradas = decisiones.
Ningún test depende de Binance (desconectado del flujo principal)."""
import asyncio
import json
import os
import tempfile
import unittest
from bot.brokers import DerivBroker, SimulatedBroker
from bot.candles import CandleBuilder
from bot.config import Settings
from bot.main import Orchestrator
from bot.market_data import DerivClient
from bot.providers import DerivProvider, MarketDataProvider
from bot.strategies import Ema30_72_150, Ema72_150


class FakeWS:
    """WebSocket falso con respuestas programadas (sin red)."""

    def __init__(self, script):
        self.script = script
        self.i = 0
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    async def recv(self):
        r = self.script[self.i]
        self.i += 1
        if isinstance(r, Exception):
            raise r
        return json.dumps(r)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def rising(n_flat=200, start=100.0, step=2.0, n_up=30):
    return [start] * n_flat + [start + i * step for i in range(n_up)]


def falling(n_flat=200, start=200.0, step=2.0, n_dn=30):
    return [start] * n_flat + [start - i * step for i in range(n_dn)]


def first_signals(strat, series):
    return [(i, strat.entry(series[:i + 1])) for i in range(len(series))
            if strat.entry(series[:i + 1])]


class TestCandles(unittest.TestCase):
    def test_01_build_from_ticks(self):
        b = CandleBuilder("X", "5m", 300)
        self.assertEqual(b.on_tick(10.0, 600), [])
        self.assertEqual(b.on_tick(12.0, 700), [])
        self.assertEqual(b.current.high, 12.0)
        self.assertEqual(len(b.closed), 0)

    def test_02_close_detection(self):
        b = CandleBuilder("X", "5m", 300)
        b.on_tick(10.0, 600)
        closed = b.on_tick(11.0, 900)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].open_epoch, 600)
        self.assertTrue(closed[0].closed)
        self.assertEqual(closed[0].uid, "X|5m|600")


class TestCrosses(unittest.TestCase):
    def test_03_bull_72150(self):
        self.assertTrue([d for _, d in first_signals(Ema72_150(), rising()) if d == "BUY"])

    def test_04_bear_72150(self):
        self.assertTrue([d for _, d in first_signals(Ema72_150(), falling()) if d == "SELL"])

    def test_05_entry_30_150_up(self):
        self.assertTrue([d for _, d in first_signals(Ema30_72_150(), rising()) if d == "BUY"])

    def test_06_entry_30_150_down(self):
        self.assertTrue([d for _, d in first_signals(Ema30_72_150(), falling()) if d == "SELL"])

    def test_07_exit_30_72(self):
        s = Ema30_72_150()
        up = rising()
        series = up + [up[-1] - i * 3 for i in range(60)]
        self.assertTrue([i for i in range(len(series)) if s.exit(series[:i + 1], "LONG")])

    def test_intrabar_revert_no_signal_then_exactly_one(self):
        """Cruce intrabar que revierte -> 0 señales; cruce al cierre -> 1."""
        s = Ema30_72_150()
        b = CandleBuilder("T", "1m", 60)
        b.seed([{"epoch": i * 60, "open": 100, "high": 100, "low": 100, "close": 100}
                for i in range(200)])
        t0 = 200 * 60
        for t, p in [(t0, 100.0), (t0 + 20, 160.0), (t0 + 40, 160.0), (t0 + 59, 100.0)]:
            self.assertEqual(b.on_tick(p, t), [])  # vela abierta: nada que evaluar
        closed = b.on_tick(100.0, t0 + 60)
        self.assertEqual(len(closed), 1)
        self.assertIsNone(s.entry(b.closes()))  # revertida: sin señal
        # Ahora cruce sostenido al cierre -> exactamente una señal
        n_sig = 0
        for k in range(1, 40):
            cs = b.on_tick(150.0, t0 + k * 60)
            for _ in cs:
                if s.entry(b.closes()) == "BUY":
                    n_sig += 1
        self.assertEqual(n_sig, 1)


class TestBroker(unittest.TestCase):
    def test_08_no_duplicate_signals(self):
        br = SimulatedBroker()
        r1 = br.place_order("X", "BUY", 1.0, 100.0, "X|s|5m")
        r2 = br.place_order("X", "BUY", 1.0, 100.0, "X|s|5m")
        self.assertTrue(r1["ok"] and not r2["ok"])
        self.assertEqual(len(br.positions), 1)

    def test_12_sim_positions_unique_ids(self):
        br = SimulatedBroker()
        a = br.place_order("X", "BUY", 1.0, 100.0, "k1")
        b = br.place_order("X", "SELL", 1.0, 100.0, "k2")
        self.assertNotEqual(a["id"], b["id"])

    def test_13_pnl_commission_spread(self):
        br = SimulatedBroker(balance=1000.0, commission=0.001, spread=0.0, qty=1.0)
        br.place_order("X", "BUY", 1.0, 100.0, "k")
        r = br.close_by_ref("k", 110.0, "t")
        self.assertAlmostEqual(r["pnl"], 10.0 - 0.11, places=6)  # fee cierre
        self.assertAlmostEqual(br.get_balance(), 1000.0 - 0.1 + r["pnl"], places=6)  # fee apertura en balance

    def test_15_state_recovery(self):
        tmp = tempfile.mkdtemp()
        from bot.database import Store
        db = Store(os.path.join(tmp, "r.db"))
        br = SimulatedBroker(db=db)
        br.place_order("X", "BUY", 1.0, 100.0, "X|s|5m")
        br2 = SimulatedBroker(db=db)
        br2.restore(db.open_trades())
        self.assertIn("X|s|5m", br2.positions)
        self.assertEqual(br2.positions["X|s|5m"].entry, 100.0)

    def test_deriv_stub_has_no_fake_calls(self):
        d = DerivBroker()
        for m in ("connect", "get_account", "get_balance", "get_positions"):
            with self.assertRaises(NotImplementedError):
                getattr(d, m)()
        with self.assertRaises(NotImplementedError):
            d.place_order("X", "BUY", 1, 1, "r")


class TestSystem(unittest.TestCase):
    # Migrados de `assignments` internos a la API de bots (Fase 2):
    # el modelo assignments fue sustituido por BotInstance/BotRuntime.
    def _orch(self):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        s.symbols = ["frxXAUUSD"]
        return Orchestrator(s)

    def _bot(self, o):
        return next(rt for rt in o.bots.values() if rt.bot.symbol == "frxXAUUSD")

    def test_03_no_double_process(self):
        o = self._orch()
        rt = self._bot(o)
        rt.builder.seed([{"epoch": 600 + i * 300, "open": 100, "high": 101,
                          "low": 99, "close": 100} for i in range(200)])
        from bot.candles import Candle
        c = Candle(rt.bot.symbol, rt.bot.timeframe, 60600, 100, 101, 99, 100, True)
        o._on_closed_candle(rt, c)
        o._on_closed_candle(rt, c)  # duplicada
        self.assertTrue(o.db.is_processed(c.uid))
        self.assertLessEqual(len(o.db.signals(10)), 1)

    def test_09_multi_symbols(self):
        o = self._orch()
        syms = {rt.bot.symbol for rt in o.bots.values()}
        self.assertIn("frxXAUUSD", syms)

    def test_10_multi_timeframes(self):
        o = self._orch()
        tfs = {rt.bot.timeframe for rt in o.bots.values()
               if rt.bot.symbol == "frxXAUUSD"}
        self.assertTrue({"5m", "15m"} <= tfs)

    def test_11_multi_strategies(self):
        o = self._orch()
        names = {rt.bot.strategy_key for rt in o.bots.values()}
        self.assertTrue({"irk", "irk_plus"} <= names)
        self.assertEqual(len({id(rt.builder) for rt in o.bots.values()}),
                         len(o.bots))

    def test_14_reconnect_no_signal(self):
        o = self._orch()
        rt = self._bot(o)
        from bot.candles import Candle
        c = Candle(rt.bot.symbol, rt.bot.timeframe, 99999, 1, 1, 1, 1, True)
        o.db.mark_processed(c.uid)  # ya vista antes del "reconnect"
        o._on_closed_candle(rt, c)
        self.assertEqual(len(o.db.signals(10)), 0)

    def test_provider_interface(self):
        self.assertTrue(issubclass(DerivProvider, MarketDataProvider))
        self.assertEqual(DerivProvider().name, "deriv")


class TestDerivClient(unittest.TestCase):
    """Cliente Deriv con transporte falso: símbolos válidos/inválidos, OHLC,
    suscripciones y errores de WebSocket (evidencia: RESULTADOS.md).
    Usa el pump real (único lector) como en producción."""

    def _run_pumped(self, ws, fn):
        async def go():
            c = DerivClient()
            c._inbox = asyncio.Queue()
            c._lock = asyncio.Lock()
            pump = asyncio.ensure_future(c._pump(ws))
            try:
                return await fn(c)
            finally:
                pump.cancel()
        return run(go())

    def test_valid_symbol_history_ohlc(self):
        ws = FakeWS([{"history": {"prices": [4390.0], "times": [1000]},
                      "pip_size": 2, "msg_type": "history", "req_id": 1}])
        price, epoch = self._run_pumped(ws, lambda c: c.latest_tick(ws, "frxXAUUSD"))
        self.assertEqual((price, epoch), (4390.0, 1000))

    def test_invalid_symbols_rejected(self):
        for sym in ("XAUUSD", "USTEC", "US100", "NAS100", "US500", "TSLA"):
            ws = FakeWS([{"error": {"code": "InvalidSymbol", "message": "no"},
                          "msg_type": "tick", "req_id": 1}])
            with self.assertRaises(RuntimeError, msg=sym):
                self._run_pumped(ws, lambda c, s=sym: c.latest_tick(ws, s))

    def test_candles_parse(self):
        ws = FakeWS([{"candles": [{"epoch": 1, "open": 2, "high": 3, "low": 1,
                                   "close": 2.5}],
                      "msg_type": "candles", "req_id": 1}])
        cs = self._run_pumped(ws, lambda c: c.history_candles(ws, "R_100", "1m", 1))
        self.assertEqual(cs[0]["close"], 2.5)

    def test_malformed_history_rejected(self):
        ws = FakeWS([{"msg_type": "history", "req_id": 1}])  # sin history
        with self.assertRaises(RuntimeError):
            self._run_pumped(ws, lambda c: c.latest_tick(ws, "frxXAUUSD"))

    def test_pong_does_not_steal_response(self):
        """Regresión real: el pong del keepalive robaba la respuesta."""
        ws = FakeWS([
            {"echo_req": {"ping": 1}, "msg_type": "ping", "ping": "pong",
             "req_id": 99},
            {"history": {"prices": [4390.0], "times": [1000]},
             "msg_type": "history", "req_id": 1}])
        price, epoch = self._run_pumped(ws, lambda c: c.latest_tick(ws, "frxXAUUSD"))
        self.assertEqual((price, epoch), (4390.0, 1000))

    def test_subscribe_rejected_goes_poll(self):
        async def go():
            c = DerivClient()
            c._inbox = asyncio.Queue()
            c._lock = asyncio.Lock()
            c._subs = {"frxXAUUSD"}
            ws = FakeWS([{"error": {"code": "InvalidSymbol", "message": "no"},
                          "msg_type": "tick", "req_id": 1}])
            pump = asyncio.ensure_future(c._pump(ws))
            try:
                await c._subscribe_all(ws)
                return c.mode
            finally:
                pump.cancel()
        self.assertEqual(run(go()), "poll")

    def test_subscribe_accepted_goes_stream(self):
        async def go():
            c = DerivClient()
            c._inbox = asyncio.Queue()
            c._lock = asyncio.Lock()
            c._subs = {"R_100"}
            ws = FakeWS([{"echo_req": {"ticks": "R_100", "subscribe": 1},
                          "tick": {"symbol": "R_100"}, "subscription": {"id": "x"},
                          "msg_type": "tick", "req_id": 1}])
            pump = asyncio.ensure_future(c._pump(ws))
            try:
                await c._subscribe_all(ws)
                return c.mode
            finally:
                pump.cancel()
        self.assertEqual(run(go()), "stream")

    def test_websocket_error_propagates(self):
        async def go():
            c = DerivClient()
            c._inbox = asyncio.Queue()
            c._lock = asyncio.Lock()
            ws = FakeWS([ConnectionError("corte")])
            pump = asyncio.ensure_future(c._pump(ws))
            try:
                await c._call(ws, {"ping": 1}, timeout=2)
            finally:
                pump.cancel()
        with self.assertRaises(asyncio.TimeoutError):
            run(go())


class TestMultiProvider(unittest.TestCase):
    """Abstracción multi-proveedor (offline). Deriv intacto y operativo."""

    def test_factory_returns_deriv_by_default(self):
        from bot.providers import create_provider
        s = Settings()
        s.provider = "deriv"
        p = create_provider(s)
        self.assertIsInstance(p, DerivProvider)
        self.assertIsInstance(p, MarketDataProvider)

    def test_factory_rejects_binance_explicitly(self):
        from bot.providers import create_provider
        s = Settings()
        s.provider = "binance"
        with self.assertRaises(RuntimeError):
            create_provider(s)

    def test_future_providers_not_implemented(self):
        from bot.providers import BybitProvider, KrakenProvider, OKXProvider, create_provider
        s = Settings()
        for cls, name in ((KrakenProvider, "kraken"), (OKXProvider, "okx"),
                          (BybitProvider, "bybit")):
            self.assertTrue(issubclass(cls, MarketDataProvider))
            with self.assertRaises(NotImplementedError):
                run(cls().history("X", "1m"))
            s.provider = name
            with self.assertRaises(NotImplementedError):
                create_provider(s)

    def test_deriv_capabilities_honest(self):
        caps = DerivProvider().capabilities().as_dict()
        self.assertTrue(caps["ticks"] and caps["ohlc"] and caps["history"])
        self.assertFalse(caps["streaming"])  # PENDIENTE_DE_CONFIRMAR
        self.assertFalse(caps["volume"])

    def test_supported_timeframes(self):
        self.assertIn("5m", DerivProvider.supported_timeframes)

    def test_pair_scanner_stub(self):
        from bot.providers import PairScanner
        with self.assertRaises(NotImplementedError):
            PairScanner(DerivProvider()).score("frxXAUUSD")


class TestCoinMarketCap(unittest.TestCase):
    """Proveedor CMC con HTTP falso (sin Internet)."""

    def _fake(self, payload=None, raw=None, exc=None):
        import urllib.error

        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls = {"n": 0}

        def opener(req, timeout=None):
            calls["n"] += 1
            e = exc[calls["n"] - 1] if isinstance(exc, list) else exc
            if e == "429":
                raise urllib.error.HTTPError(req.full_url, 429, "retry", {}, None)
            if e == "500":
                raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)
            if e == "timeout":
                raise TimeoutError("t")
            body = raw if raw is not None else json.dumps(payload).encode()
            return Resp(body)

        return opener, calls

    def _prov(self, opener):
        import bot.providers_coinmarketcap as m
        orig = m.urllib.request.urlopen
        m.urllib.request.urlopen = opener
        self.addCleanup(setattr, m.urllib.request, "urlopen", orig)
        p = m.CoinMarketCapProvider(ids={"BTC": 1})
        p._sleep = lambda s: None
        return p

    def test_quotes_ok(self):
        op, _ = self._fake({"data": [{"id": 1, "price": 50000.0}],
                            "status": {"error_code": "0"}})
        q = self._prov(op).quotes()
        self.assertEqual(q["BTC"][0], 50000.0)

    def test_listings_volume_mcap(self):
        op, _ = self._fake({"data": [{"id": 1, "symbol": "BTC", "name": "Bitcoin",
                                      "cmc_rank": 1, "num_market_pairs": 10,
                                      "quote": [{"symbol": "USD", "price": 5.0,
                                                 "volume_24h": 7.0, "market_cap": 9.0,
                                                 "last_updated": "t"}]}],
                            "status": {"error_code": "0"}})
        lst = self._prov(op).listings(limit=1)
        self.assertEqual((lst[0]["volume_24h"], lst[0]["market_cap"]), (7.0, 9.0))

    def test_http_error(self):
        op, _ = self._fake(exc="500")
        with self.assertRaises(RuntimeError):
            self._prov(op).quotes()

    def test_429_then_success_backoff(self):
        import bot.providers_coinmarketcap as m
        op, _ = self._fake({"data": [{"id": 1, "price": 1.0}],
                            "status": {"error_code": "0"}}, exc=["429", None])
        p = self._prov(op)
        waits = []
        p.client._sleep = waits.append
        self.assertEqual(p.quotes()["BTC"][0], 1.0)
        self.assertEqual(waits, [1])  # backoff 2**0

    def test_timeout(self):
        op, _ = self._fake(exc=["timeout"] * 4)
        with self.assertRaises(RuntimeError):
            self._prov(op).quotes()

    def test_invalid_json(self):
        op, _ = self._fake(raw=b"no-json{{{")
        with self.assertRaises(RuntimeError):
            self._prov(op).quotes()

    def test_unknown_asset(self):
        op, _ = self._fake({"data": [], "status": {"error_code": "0"}})
        with self.assertRaises(RuntimeError):
            self._prov(op).quotes()

    def test_history_cex_unavailable(self):
        p = self._prov(self._fake({})[0])
        with self.assertRaises(RuntimeError):
            run(p.history("BTC", "1h"))

    def test_kline_valid_and_incomplete(self):
        p = self._prov(self._fake({})[0])
        p.pools = {"T": ("ethereum", "0xabc")}
        import bot.providers_coinmarketcap as m
        orig = m.CoinMarketCapProvider._get
        m.CoinMarketCapProvider._get = lambda self, path: {
            "data": [[1, 2, 0.5, 1.5, 10, 3600, 3],  # [o,h,l,c,vol,ts,tr]
                     [1.5],                           # incompleta: se descarta
                     {"bogus": True}],                # inválida: se descarta
            "status": {"error_code": "0"}}
        self.addCleanup(setattr, m.CoinMarketCapProvider, "_get", orig)
        cs = run(p.history("T", "1h"))
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["close"], 1.5)

    def test_capabilities_polling_not_stream(self):
        caps = self._prov(self._fake({})[0]).capabilities().as_dict()
        self.assertFalse(caps["streaming"])
        self.assertFalse(caps["websocket"])
        self.assertTrue(caps["volume"])

    def test_run_emits_and_stops_clean(self):
        import asyncio
        op, _ = self._fake({"data": [{"id": 1, "price": 42.0}],
                            "status": {"error_code": "0"}})
        p = self._prov(op)
        p.poll_seconds = 0.01
        got = []
        p.on_tick = lambda s, pr, e: (got.append((s, pr)), p.stop())

        async def go():
            await p.run(["BTC"])
        run(go())
        self.assertEqual(got[0][:2], ("BTC", 42.0))

    def test_factory_cmc(self):
        from bot.providers import create_provider
        s = Settings()
        s.provider = "coinmarketcap"
        s.cmc_ids = "BTC:1"
        p = create_provider(s)
        self.assertEqual(p.name, "coinmarketcap")


class TestIndexRangeKiller(unittest.TestCase):
    def test_irk_bull_75_150(self):
        from bot.strategies import Ema72_150
        s = Ema72_150(fast=75, slow=150)
        self.assertTrue([d for _, d in first_signals(s, rising()) if d == "BUY"])

    def test_irk_bear_75_150(self):
        from bot.strategies import Ema72_150
        s = Ema72_150(fast=75, slow=150)
        self.assertTrue([d for _, d in first_signals(s, falling()) if d == "SELL"])

    def test_irk_plus_entry_exit(self):
        from bot.strategies import Ema30_72_150
        s = Ema30_72_150(entry_fast=30, mid=75, slow=150)
        self.assertTrue([d for _, d in first_signals(s, rising()) if d == "BUY"])
        up = rising()
        series = up + [up[-1] - i * 3 for i in range(80)]
        self.assertTrue([i for i in range(len(series)) if s.exit(series[:i + 1], "LONG")])

    def test_bot_auto_naming(self):
        from bot.bots import BotInstance
        b = BotInstance("x", "irk", "frxXAUUSD", "deriv", "5m")
        self.assertEqual(b.display_name, "Index Range Killer 5m")
        b2 = BotInstance("y", "irk_plus", "frxXAUUSD", "deriv", "15m")
        self.assertEqual(b2.display_name, "Index Range Killer + 15m")

    def test_bot_defaults(self):
        from bot.bots import BotInstance
        b = BotInstance("x", "irk", "S", "deriv", "1m")
        self.assertEqual((b.capital, b.lot, b.max_positions), (50.0, 0.01, 1))

    def test_timeframe_immutable(self):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        o = Orchestrator(s)
        bid = next(iter(o.bots))
        with self.assertRaises(ValueError):
            o.update_bot(bid, timeframe="1h")

    def test_bot_independence_capital_lot(self):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        o = Orchestrator(s)
        b1 = o.create_bot("irk", "frxXAUUSD", "deriv", "1m", capital=50.0, lot=0.01)
        b2 = o.create_bot("irk", "frxXAUUSD", "deriv", "1h", capital=200.0, lot=0.05,
                          max_positions=3)
        self.assertNotEqual(o.bots[b1.bot_id].broker.get_balance(),
                            o.bots[b2.bot_id].broker.get_balance())
        self.assertEqual(o.bots[b2.bot_id].bot.max_positions, 3)

    def test_max_positions_respected(self):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        o = Orchestrator(s)
        b = o.create_bot("irk", "frxXAUUSD", "deriv", "1m", max_positions=1)
        rt = o.bots[b.bot_id]
        rt.builder.seed([{"epoch": i * 60, "open": 100, "high": 101,
                          "low": 99, "close": 100} for i in range(200)])
        from bot.candles import Candle
        c1 = Candle("frxXAUUSD", "1m", 12000, 100, 101, 99, 100, True)
        rt.broker.place_order("frxXAUUSD", "BUY", 0.01, 100.0, "m:1",
                              bot_id=b.bot_id)
        o._on_closed_candle(rt, c1)
        self.assertEqual(len(rt.broker.positions), 1)  # sin duplicar


class TestStats(unittest.TestCase):
    def _closed(self):
        return [
            {"pnl": 10.0, "direction": "LONG", "open_epoch": 0, "close_epoch": 60},
            {"pnl": -4.0, "direction": "SHORT", "open_epoch": 60, "close_epoch": 120},
            {"pnl": 6.0, "direction": "LONG", "open_epoch": 120, "close_epoch": 240},
        ]

    def test_metrics(self):
        from bot.stats import compute_stats
        st = compute_stats(50.0, self._closed(), [], None)
        self.assertEqual(st["balance"], 62.0)
        self.assertEqual(st["pnl_pct"], 24.0)
        self.assertEqual((st["total"], st["wins"], st["losses"]), (3, 2, 1))
        self.assertAlmostEqual(st["win_rate"], 66.67, places=1)
        self.assertEqual(st["profit_factor"], 4.0)
        self.assertEqual(st["max_win_streak"], 1)
        self.assertEqual(st["max_loss_streak"], 1)
        self.assertEqual(st["buys"], 2)
        self.assertEqual(st["avg_duration"], 80.0)
        self.assertTrue(st["max_drawdown"] <= 0)

    def test_history_immutable(self):
        tmp = tempfile.mkdtemp()
        from bot.database import Store
        db = Store(os.path.join(tmp, "i.db"))
        br = SimulatedBroker()
        br.db = db
        br.place_order("X", "BUY", 0.01, 100.0, "b:1", bot_id="b",
                       extra={"bot_id": "b", "lot": 0.01, "spread": 0.1,
                              "commission": 0.2, "params": {"fast": 75},
                              "strategy": "Index Range Killer",
                              "provider": "deriv", "costs_assumed": True})
        br.close_by_ref("b:1", 110.0, "t")
        h = db.trade_history(10, "b")[0]
        self.assertEqual((h["lot"], h["spread"], h["commission"]), (0.01, 0.1, 0.2))
        self.assertEqual(h["strategy"], "Index Range Killer")
        self.assertIn("fast", h["params"])
        self.assertIsNotNone(h["pnl_pct"])
        self.assertIsNotNone(h["duration"])


class TestAPIAuth(unittest.TestCase):
    def _client(self, token="secret-admin"):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        s.symbols = ["frxXAUUSD"]
        o = Orchestrator(s)
        from bot.api import create_app
        from fastapi.testclient import TestClient
        os.environ["ADMIN_TOKEN"] = token
        self.addCleanup(os.environ.pop, "ADMIN_TOKEN", None)
        return TestClient(create_app(o)), o

    def test_1_public_no_strategy(self):
        c, _ = self._client()
        for b in c.get("/api/bots").json():
            self.assertNotIn("strategy_key", b)
            self.assertNotIn("params", b)
            self.assertEqual(b.get("strategy"), "Private")

    def test_2_public_no_ema_params(self):
        c, _ = self._client()
        body = c.get("/api/bots").json()
        self.assertNotIn("EMA75", str(body))
        self.assertNotIn("EMA150", str(body))
        self.assertNotIn("EMA30", str(body))

    def test_3_public_no_internal_signals(self):
        c, _ = self._client()
        r = c.get("/api/admin/bots/qualcosa/signals")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(c.get("/api/candles?symbol=X&timeframe=5m").json()["markers"], [])

    def test_4_public_cannot_modify(self):
        c, o = self._client()
        bid = next(iter(o.bots))
        self.assertEqual(c.patch(f"/api/admin/bots/{bid}", json={"lot": 9}).status_code, 403)
        self.assertEqual(c.post("/api/admin/bots", json={}).status_code, 403)

    def test_5_public_cannot_change_timeframe(self):
        c, o = self._client()
        bid = next(iter(o.bots))
        # sin token ni siquiera llega a la validación de timeframe
        r = c.patch(f"/api/admin/bots/{bid}", json={"timeframe": "1h"})
        self.assertEqual(r.status_code, 403)
        # con token, el backend lo rechaza explícitamente
        r2 = c.patch(f"/api/admin/bots/{bid}", json={"timeframe": "1h"},
                     headers={"X-Admin-Token": "secret-admin"})
        self.assertEqual(r2.status_code, 400)

    def test_6_admin_sees_internals(self):
        c, o = self._client()
        h = {"X-Admin-Token": "secret-admin"}
        bots = c.get("/api/admin/bots", headers=h).json()
        self.assertTrue(bots)
        self.assertIn("strategy_key", bots[0])
        self.assertIn("params", bots[0])
        bid = next(iter(o.bots))
        cd = c.get(f"/api/admin/candles?symbol={o.bots[bid].bot.symbol}"
                   f"&timeframe={o.bots[bid].bot.timeframe}", headers=h).json()
        self.assertIn("emas", cd)
        self.assertIn("strategy", cd)


class TestCMCCatalog(unittest.TestCase):
    def _db(self):
        tmp = tempfile.mkdtemp()
        from bot.database import Store
        return Store(os.path.join(tmp, "c.db"))

    def _cat(self, payload):
        import bot.cmc_catalog as cm
        import bot.cmc_client as cc

        body = json.dumps(payload).encode()

        class Resp:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = cc.urllib.request.urlopen
        cc.urllib.request.urlopen = lambda req, timeout=None: Resp()
        self.addCleanup(setattr, cc.urllib.request, "urlopen", orig)
        return cm.AssetCatalog(cc.CMCClient(), self._db())

    def _listing(self, n=3):
        return {"data": [
            {"id": i, "symbol": s, "name": n_, "slug": s.lower(),
             "cmc_rank": i, "num_market_pairs": 10,
             "quote": [{"symbol": "USD", "price": 100.0 + i,
                        "volume_24h": 1000.0 * i, "market_cap": 10 ** 9 * i,
                        "last_updated": "t"}]}
            for i, (s, n_) in enumerate(
                [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("PAXG", "PAX Gold")], 1)],
            "status": {"error_code": "0"}}

    def test_sync_and_search(self):
        cat = self._cat(self._listing())
        n, total = cat.sync(limit=3)
        self.assertEqual((n, total), (3, 3))
        self.assertFalse(cat.needs_sync())
        hits = cat.db.search_assets("btc")
        self.assertEqual(hits[0]["symbol"], "BTC")

    def test_resolve_symbol_name_slug_id(self):
        cat = self._cat(self._listing())
        cat.sync(limit=3)
        self.assertEqual(cat.resolve("BTC")["cmc_id"], 1)
        self.assertEqual(cat.resolve("pax gold")["symbol"], "PAXG")
        self.assertEqual(cat.resolve("ethereum")["cmc_id"], 2)
        self.assertEqual(cat.resolve("3")["symbol"], "PAXG")

    def test_resolve_unknown_returns_none(self):
        cat = self._cat({"data": [], "status": {"error_code": "0"}})
        self.assertIsNone(cat.resolve("NOEXISTE123"))

    def test_persist_restart(self):
        import bot.cmc_catalog as cm
        import bot.cmc_client as cc
        tmp = tempfile.mkdtemp()
        from bot.database import Store
        path = os.path.join(tmp, "c.db")
        cat = cm.AssetCatalog(cc.CMCClient(), Store(path))
        body = json.dumps(self._listing()).encode()

        class Resp:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        orig = cc.urllib.request.urlopen
        cc.urllib.request.urlopen = lambda req, timeout=None: Resp()
        try:
            cat.sync(limit=3)
        finally:
            cc.urllib.request.urlopen = orig
        cat2 = cm.AssetCatalog(cc.CMCClient(), Store(path))  # reinicio
        self.assertEqual(cat2.db.asset_count(), 3)
        self.assertFalse(cat2.needs_sync())
        self.assertEqual(cat2.resolve("BTC")["cmc_id"], 1)

    def test_bot_creation_with_resolution(self):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        o = Orchestrator(s)
        import bot.cmc_catalog as cm
        import bot.cmc_client as cc
        body = json.dumps(self._listing()).encode()

        class Resp:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = cc.urllib.request.urlopen
        cc.urllib.request.urlopen = lambda req, timeout=None: Resp()
        try:
            o._cmc_catalog = cm.AssetCatalog(cc.CMCClient(), o.db)
            o._cmc_catalog.sync(limit=3)
        finally:
            cc.urllib.request.urlopen = orig
        b = o.create_bot("irk", "Gold 1m", "coinmarketcap", "5m")
        self.assertEqual(b.symbol, "PAXG")
        self.assertEqual(b.provider_ref, "3")
        self.assertEqual(o.provider.name, "deriv")  # Deriv intacto

    def test_seed_history_and_strategy(self):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        o = Orchestrator(s)
        b = o.create_bot("irk", "BTC 5m", "coinmarketcap", "5m")
        rt = o.bots[b.bot_id]
        rt.builder.seed([{"epoch": i * 300, "open": 100, "high": 101,
                          "low": 99, "close": 100} for i in range(200)])
        from bot.candles import Candle
        c = Candle("BTC", "5m", 60000, 100, 101, 99, 100, True)
        o._on_closed_candle(rt, c)
        self.assertTrue(o.db.is_processed(c.uid))


class TestCMCKeyed(unittest.TestCase):
    def _prov(self, payload):
        import bot.providers_coinmarketcap as m
        body = json.dumps(payload).encode()

        class Resp:
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False

        orig = m.urllib.request.urlopen
        # parcha donde el cliente lo usa (mismo objeto global)
        import urllib.request as u
        u.urlopen = lambda req, timeout=None: Resp()
        self.addCleanup(setattr, u, "urlopen", orig)
        return m.CoinMarketCapProvider(ids={"BTC": 1}, api_key="K")

    def _qh(self):
        return {"data": {"1": {"quotes": [
            {"timestamp": "2026-09-12T20:20:00.000Z",
             "quote": {"USD": {"price": 100.0}}},
            {"timestamp": "2026-09-12T20:25:00.000Z",
             "quote": {"USD": {"price": 101.0}}},
            {"timestamp": "mala", "quote": {}},
        ]}}, "status": {"error_code": "0"}}

    def test_keyed_history_5m(self):
        cs = run(self._prov(self._qh()).history("BTC", "5m", limit=10))
        self.assertEqual(len(cs), 2)  # la mala se descarta
        self.assertEqual(cs[0]["open"], 100.0)
        self.assertEqual(cs[0]["volume"], 0.0)  # rolling-24h NO es volumen
        self.assertLess(cs[0]["epoch"], cs[1]["epoch"])

    def test_keyed_1m_rejected(self):
        with self.assertRaises(RuntimeError):
            run(self._prov(self._qh()).history("BTC", "1m"))

    def test_keyed_capabilities(self):
        caps = self._prov(self._qh()).capabilities().as_dict()
        self.assertTrue(caps["history"] and caps["credentials_required"])
        self.assertFalse(caps["streaming"])


class TestAdminRateLimit(unittest.TestCase):
    def _client(self, limit=3, window=60):
        tmp = tempfile.mkdtemp()
        s = Settings()
        s.db_path = os.path.join(tmp, "t.db")
        s.symbols = ["frxXAUUSD"]
        o = Orchestrator(s)
        os.environ["ADMIN_TOKEN"] = "rl-test"
        os.environ["ADMIN_RATE_LIMIT"] = str(limit)
        os.environ["ADMIN_RATE_WINDOW"] = str(window)
        self.addCleanup(os.environ.pop, "ADMIN_TOKEN", None)
        self.addCleanup(os.environ.pop, "ADMIN_RATE_LIMIT", None)
        self.addCleanup(os.environ.pop, "ADMIN_RATE_WINDOW", None)
        from bot.api import create_app, _rate
        _rate._windows.clear()
        from fastapi.testclient import TestClient
        return TestClient(create_app(o))

    def test_admin_ok_under_limit(self):
        c = self._client(limit=5)
        h = {"X-Admin-Token": "rl-test"}
        for _ in range(5):
            r = c.get("/api/admin/bots", headers=h)
            self.assertEqual(r.status_code, 200)

    def test_admin_429_over_limit(self):
        c = self._client(limit=2)
        h = {"X-Admin-Token": "rl-test"}
        self.assertEqual(c.get("/api/admin/bots", headers=h).status_code, 200)
        self.assertEqual(c.get("/api/admin/bots", headers=h).status_code, 200)
        r = c.get("/api/admin/bots", headers=h)
        self.assertEqual(r.status_code, 429)
        self.assertIn("rate limit", r.json()["detail"])

    def test_admin_403_no_token(self):
        c = self._client()
        r = c.get("/api/admin/bots")
        self.assertEqual(r.status_code, 403)

    def test_public_no_rate_limit(self):
        c = self._client(limit=1)
        for _ in range(10):
            r = c.get("/api/bots")
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
