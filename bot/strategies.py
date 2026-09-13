"""Estrategias 100% deterministas sobre velas CERRADAS.

Cruce confirmado (sin look-ahead, sin intrabar):
  alcista: prev_fast <= prev_slow AND curr_fast > curr_slow
  bajista: prev_fast >= prev_slow AND curr_fast < curr_slow
Un simple `fast > slow` NUNCA es señal.
"""
from dataclasses import dataclass
from .indicators import ema_series


@dataclass
class Signal:
    strategy_id: str
    symbol: str
    timeframe: str
    direction: str  # BUY | SELL | EXIT_LONG | EXIT_SHORT
    candle_open_epoch: int
    price: float
    reason: str


def _last_two(closes: list[float], fast: int, slow: int):
    """Devuelve (pf, ps, cf, cs) de las dos últimas velas cerradas o None."""
    need = slow + 2
    if len(closes) < need:
        return None
    f = ema_series(closes, fast)
    s = ema_series(closes, slow)
    if f[-1] is None or f[-2] is None or s[-1] is None or s[-2] is None:
        return None
    return f[-2], s[-2], f[-1], s[-1]


def _eps(x: float) -> float:
    return 1e-9 * max(1.0, abs(x))


def cross_up(closes: list[float], fast: int, slow: int) -> bool:
    v = _last_two(closes, fast, slow)
    if not v:
        return False
    pf, ps, cf, cs = v
    return bool(pf <= ps + _eps(ps) and cf > cs + _eps(cs))


def cross_down(closes: list[float], fast: int, slow: int) -> bool:
    v = _last_two(closes, fast, slow)
    if not v:
        return False
    pf, ps, cf, cs = v
    return bool(pf >= ps - _eps(ps) and cf < cs - _eps(cs))


class Strategy:
    name = "base"

    def entry(self, closes: list[float]) -> str | None:
        raise NotImplementedError

    def exit(self, closes: list[float], position: str) -> bool:
        """position: LONG|SHORT. True = cerrar. Por defecto: solo señal contraria."""
        return False


class Ema72_150(Strategy):
    """Estrategia 1: cruce EMA72/EMA150. Entrada + cierre en cruce contrario."""
    name = "EMA 72/150"

    def __init__(self, fast: int = 72, slow: int = 150):
        self.fast, self.slow = fast, slow

    def entry(self, closes):
        if cross_up(closes, self.fast, self.slow):
            return "BUY"
        if cross_down(closes, self.fast, self.slow):
            return "SELL"
        return None

    def exit(self, closes, position):
        if position == "LONG":
            return cross_down(closes, self.fast, self.slow)
        if position == "SHORT":
            return cross_up(closes, self.fast, self.slow)
        return False


class Ema30_72_150(Strategy):
    """Estrategia 2: entrada por cruce EMA30/EMA150; cierre por cruce EMA30/EMA72."""
    name = "EMA 30/72/150"

    def __init__(self, entry_fast: int = 30, mid: int = 72, slow: int = 150):
        self.entry_fast, self.mid, self.slow = entry_fast, mid, slow

    def entry(self, closes):
        if cross_up(closes, self.entry_fast, self.slow):
            return "BUY"
        if cross_down(closes, self.entry_fast, self.slow):
            return "SELL"
        return None

    def exit(self, closes, position):
        if position == "LONG":
            return cross_down(closes, self.entry_fast, self.mid)
        if position == "SHORT":
            return cross_up(closes, self.entry_fast, self.mid)
        return False


STRATEGIES = {"ema72_150": Ema72_150, "ema30_72_150": Ema30_72_150}


@dataclass
class Assignment:
    """Combinación mercado + estrategia + timeframe con estado propio."""
    id: str
    strategy_key: str
    strategy_name: str
    params: dict
    symbol: str
    timeframe: str
    on: bool = True

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.id}|{self.timeframe}"
