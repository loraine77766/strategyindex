"""Candle Builder multi-timeframe.

REGLA CRITICA: las decisiones solo usan velas CERRADAS.
Una vela [T, T+D) se cierra cuando llega un tick con epoch >= T+D.
Cada vela cerrada se identifica por: simbolo + timeframe + open_epoch.
"""
from dataclasses import dataclass, field


@dataclass
class Candle:
    symbol: str
    timeframe: str
    open_epoch: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    closed: bool = False

    @property
    def uid(self) -> str:
        return f"{self.symbol}|{self.timeframe}|{self.open_epoch}"


def candle_id(symbol: str, timeframe: str, open_epoch: int) -> str:
    return f"{symbol}|{timeframe}|{open_epoch}"


class CandleBuilder:
    """Construye velas de UN simbolo+timeframe a partir de ticks."""

    def __init__(self, symbol: str, timeframe: str, tf_seconds: int):
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf = tf_seconds
        self.current: Candle | None = None
        self.closed: list[Candle] = []  # historial de cerradas (ordenadas)

    def bucket(self, epoch: int) -> int:
        return epoch - (epoch % self.tf)

    def seed(self, candles: list[dict]) -> None:
        """Siembra con velas históricas estilo Deriv {epoch,open,high,low,close}."""
        for c in sorted(candles, key=lambda x: x["epoch"]):
            self.closed.append(Candle(self.symbol, self.timeframe, c["epoch"],
                                      c["open"], c["high"], c["low"], c["close"],
                                      c.get("volume", 0.0), True))
        # La vela en curso arranca vacía; se crea con el primer tick.

    def on_tick(self, price: float, epoch: int) -> list[Candle]:
        """Procesa un tick. Devuelve la lista de velas que se CERRARON con él."""
        b = self.bucket(epoch)
        if self.current is None:
            self.current = Candle(self.symbol, self.timeframe, b, price, price, price, price)
            return []
        if b < self.current.open_epoch:
            return []  # tick tardío/desordenado: se ignora
        if b == self.current.open_epoch:
            c = self.current
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            return []
        # b > actual: se cierran la actual (y se deja el hueco si hubo salto)
        out = []
        cur = self.current
        cur.closed = True
        self.closed.append(cur)
        out.append(cur)
        self.current = Candle(self.symbol, self.timeframe, b, price, price, price, price)
        return out

    def closes(self) -> list[float]:
        return [c.close for c in self.closed]
