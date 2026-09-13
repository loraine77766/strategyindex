"""BotInstance: cada bot es una simulación independiente.

- timeframe FIJO por bot (no existe "cambiar timeframe": se crea otro bot).
- Nombre público auto: "Index Range Killer 5m" / "Index Range Killer + 15m".
- Capital, lote, max_positions y broker propios por bot.
"""
import time
from dataclasses import dataclass
from .brokers import SimulatedBroker
from .candles import CandleBuilder
from .config import TIMEFRAMES
from .strategies import STRATEGIES

# strategy_key -> (nombre público base, clase, params fijos)
CATALOG = {
    "irk": ("Index Range Killer", "ema72_150", {"fast": 75, "slow": 150}),
    "irk_plus": ("Index Range Killer +", "ema30_72_150",
                 {"entry_fast": 30, "mid": 75, "slow": 150}),
}


@dataclass
class BotInstance:
    bot_id: str
    strategy_key: str
    symbol: str
    provider: str
    timeframe: str
    capital: float = 50.0
    lot: float = 0.01
    max_positions: int = 1
    status: str = "running"  # running | stopped
    created: int = 0
    spread: float = 0.0001
    commission: float = 0.0005
    costs_assumed: bool = True  # spread/comisión supuestos, no datos reales
    provider_ref: str = ""  # identificador interno del proveedor (ej. CMC ID)

    @property
    def base_name(self):
        return CATALOG[self.strategy_key][0]

    @property
    def display_name(self):
        return f"{self.base_name} {self.timeframe}"

    def to_dict(self, public=False):
        d = {"bot_id": self.bot_id, "name": self.display_name,
             "symbol": self.symbol, "timeframe": self.timeframe,
             "capital": self.capital, "lot": self.lot,
             "max_positions": self.max_positions, "status": self.status,
             "created": self.created}
        if not public:
            d.update({"strategy_key": self.strategy_key,
                      "strategy": self.base_name,
                      "params": CATALOG[self.strategy_key][2],
                      "provider": self.provider, "spread": self.spread,
                      "commission": self.commission,
                      "costs_assumed": self.costs_assumed})
        else:
            d["strategy"] = "Private"
        return d


class BotRuntime:
    """Estado vivo de un bot: builder + estrategia + broker propios.

    state (efímero, real): STARTING → WAITING_FOR_MARKET_DATA → RUNNING;
    RECONNECTING si el proveedor cae; ERROR ante excepción; STOPPED si el
    bot está detenido. Nunca RUNNING sin procesar datos.
    """

    def __init__(self, bot: BotInstance, db, on_event=None):
        self.bot = bot
        self.builder = CandleBuilder(bot.symbol, bot.timeframe,
                                     TIMEFRAMES[bot.timeframe])
        _, cls_key, params = CATALOG[bot.strategy_key]
        self.strategy = STRATEGIES[cls_key](**params)
        self.strategy_name = CATALOG[bot.strategy_key][0]
        self.broker = SimulatedBroker(
            balance=bot.capital, commission=bot.commission,
            spread=bot.spread, qty=bot.lot, price_fn=None, db=db,
            on_event=on_event)
        self.db = db
        self.on_event = on_event
        self._seq = 0
        self.state = "STOPPED" if bot.status != "running" else "STARTING"
        self.last_tick = 0
        self.last_candle = 0

    def open_count(self):
        return len(self.broker.positions)

    def can_open(self):
        return self.bot.status == "running" and self.open_count() < self.bot.max_positions

    def open_ref(self):
        self._seq += 1
        return f"{self.bot.bot_id}:{self._seq}"

    def snapshot_extra(self):
        return {"bot_id": self.bot.bot_id, "lot": self.bot.lot,
                "spread": self.bot.spread, "commission": self.bot.commission,
                "params": CATALOG[self.bot.strategy_key][2],
                "strategy": self.strategy_name, "provider": self.bot.provider,
                "costs_assumed": self.bot.costs_assumed}
