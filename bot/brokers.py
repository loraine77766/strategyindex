"""BrokerInterface + SimulatedBroker + stub DerivBroker.

La estrategia NO conoce el broker: Strategy -> Signal -> BrokerInterface.
DerivBroker queda como stub SIN llamadas ficticias; se implementará cuando
existent App ID + credenciales (ver deriv_prueba/RESULTADOS.md).
"""
import itertools
import time as _time
from dataclasses import dataclass, field
from .log import get_logger

log = get_logger("broker")
_ids = itertools.count(1)


class BrokerInterface:
    name = "base"

    def connect(self): ...
    def get_account(self) -> dict: ...
    def get_balance(self) -> float: ...
    def get_positions(self) -> list: ...
    def place_order(self, symbol, side, qty, price, ref) -> dict: ...
    def close_position(self, order_id, price, reason) -> dict: ...
    def get_order_status(self, order_id) -> dict: ...


@dataclass
class SimPosition:
    id: str
    symbol: str
    strategy: str
    timeframe: str
    side: str  # LONG | SHORT
    qty: float
    entry: float
    open_epoch: int
    ref: str = ""
    bot_id: str = ""
    commission: float = 0.0
    spread: float = 0.0


class SimulatedBroker(BrokerInterface):
    """Broker 100% simulado. Recibe precios del MarketDataProvider.
    NUNCA envía órdenes reales."""
    name = "simulated"

    def __init__(self, balance=10000.0, currency="USDT", commission=0.0,
                 spread=0.0, qty=0.001, price_fn=None, db=None, on_event=None):
        self._balance = balance
        self.currency = currency
        self.commission = commission  # fracción por lado (p.ej. 0.0005)
        self.spread = spread          # fracción (p.ej. 0.0001)
        self.qty = qty
        self.price_fn = price_fn      # fn(symbol)->precio actual para P/L
        self.db = db
        self.on_event = on_event
        self.positions: dict[str, SimPosition] = {}
        self.realized_pl = 0.0

    # -- interfaz --
    def connect(self):
        return {"ok": True, "broker": self.name}

    def get_account(self):
        return {"loginid": "simulated", "currency": self.currency,
                "balance": round(self._balance, 2),
                "equity": round(self.equity(), 2),
                "realized_pl": round(self.realized_pl, 2),
                "is_virtual": True, "mode": "simulation"}

    def get_balance(self):
        return self._balance

    def get_positions(self):
        return list(self.positions.values())

    def _fill(self, side, price, qty, commission, spread):
        adj = price * (1 + spread if side == "BUY" else 1 - spread)
        fee = adj * qty * commission
        return adj, fee

    def place_order(self, symbol, side, qty, price, ref, bot_id="", extra=None):
        key = ref
        if key in self.positions:
            return {"ok": False, "error": "duplicate"}
        oid = f"SIM-{next(_ids):06d}"
        q = qty or self.qty
        fill, fee = self._fill(side, price, q, self.commission, self.spread)
        self._balance -= fee
        parts = (ref or "").split("|")
        strat = parts[1] if len(parts) > 1 else ""
        tf = parts[2] if len(parts) > 2 else ""
        pos = SimPosition(oid, symbol, strat, tf, "LONG" if side == "BUY" else "SHORT",
                          q, fill, int(_time.time()), ref, bot_id, self.commission, self.spread)
        self.positions[key] = pos
        if self.db:
            self.db.open_trade(_TradeShim(pos, fill), fill, extra)
        log.info("[SIMULATED] %s %s %s @ %s (fee %.4f)", side, symbol, oid, fill, fee)
        self._emit("open", {"id": oid, "symbol": symbol, "side": side})
        return {"ok": True, "id": oid, "fill": fill, "fee": fee}

    def close_position(self, order_id, price, reason):
        key = next((k for k, p in self.positions.items() if p.id == order_id), None)
        if not key:
            return {"ok": False, "error": "not-found"}
        p = self.positions.pop(key)
        side = "SELL" if p.side == "LONG" else "BUY"
        fill, fee = self._fill(side, price, p.qty, p.commission, p.spread)
        gross = (fill - p.entry) * p.qty if p.side == "LONG" else (p.entry - fill) * p.qty
        pnl = gross - fee
        self.realized_pl += pnl
        self._balance += pnl
        if self.db:
            self.db.close_trade(_TradeShim(p, price), fill, int(_time.time()), reason, pnl)
        log.info("[SIMULATED] CLOSE %s %s P/L=%.4f (%s)", p.side, p.symbol, pnl, reason)
        self._emit("close", {"id": order_id, "pnl": pnl})
        return {"ok": True, "pnl": pnl}

    def close_by_ref(self, ref, price, reason):
        p = self.positions.get(ref)
        if not p:
            return {"ok": False, "error": "not-found"}
        return self.close_position(p.id, price, reason)

    def get_order_status(self, order_id):
        p = next((p for p in self.positions.values() if p.id == order_id), None)
        return {"open": p is not None}

    def unrealized(self, ref):
        p = self.positions.get(ref)
        if not p or not self.price_fn:
            return 0.0
        cur = self.price_fn(p.symbol) or p.entry
        return ((cur - p.entry) if p.side == "LONG" else (p.entry - cur)) * p.qty

    def equity(self):
        return self._balance + sum(self.unrealized(k) for k in self.positions)

    def restore(self, open_trades):
        """Recupera posiciones abiertas tras reinicio (desde DB)."""
        for t in open_trades:
            p = SimPosition(t.get("id") or f"SIM-R{next(_ids):06d}", t["symbol"],
                            t["strategy"], t["timeframe"], t["direction"],
                            t.get("lot") or self.qty, t["entry"],
                            t.get("open_epoch", 0), t["tkey"],
                            t.get("bot_id", ""),
                            t.get("commission", self.commission),
                            t.get("spread", self.spread))
            self.positions[t["tkey"]] = p
        if self.positions:
            log.info("posiciones simuladas restauradas: %d", len(self.positions))

    def _emit(self, kind, data):
        if self.db:
            self.db.event(kind, data)
        if self.on_event:
            self.on_event(kind, data)


@dataclass
class _TradeShim:
    """Adapta SimPosition a lo que Store.open_trade/close_trade espera."""
    p: SimPosition
    price: float
    key: str = field(init=False)
    symbol: str = field(init=False)
    strategy_id: str = field(init=False)
    timeframe: str = field(init=False)
    direction: str = field(init=False)
    entry: float = field(init=False)
    epoch: int = field(init=False)
    qty: float = field(init=False)

    def __post_init__(self):
        self.key = self.p.ref
        self.symbol = self.p.symbol
        self.strategy_id = self.p.strategy
        self.timeframe = self.p.timeframe
        self.direction = self.p.side
        self.entry = self.p.entry    # precio de apertura real, NO el de cierre
        self.epoch = self.p.open_epoch
        self.qty = self.p.qty


class DerivBroker(BrokerInterface):
    """NOT IMPLEMENTED / WAITING FOR DEMO AUTH VALIDATION.
    No implementar órdenes reales hasta validar auth DEMO con token clásico.
    NO contiene llamadas ficticias a Deriv: todos los métodos lanzan
    NotImplementedError. Ver deriv_prueba/RESULTADOS.md (InvalidToken con PAT)."""
    name = "deriv"

    def _todo(self):
        raise NotImplementedError(
            "DerivBroker pendiente de App ID + credenciales (ver deriv_prueba/RESULTADOS.md)")

    def connect(self): return self._todo()
    def get_account(self): return self._todo()
    def get_balance(self): return self._todo()
    def get_positions(self): return self._todo()
    def place_order(self, *a, **k): return self._todo()
    def close_position(self, *a, **k): return self._todo()
    def get_order_status(self, *a, **k): return self._todo()
