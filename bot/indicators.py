"""Indicadores. Solo se calculan sobre velas CERRADAS."""


class EMA:
    def __init__(self, period: int):
        self.period = period
        self.k = 2.0 / (period + 1)
        self.value: float | None = None
        self._warm: list[float] = []

    def update(self, close: float) -> float | None:
        if self.value is None:
            self._warm.append(close)
            if len(self._warm) < self.period:
                return None
            self.value = sum(self._warm) / self.period  # semilla SMA
            self._warm = []
            return self.value
        self.value = close * self.k + self.value * (1 - self.k)
        return self.value


def ema_series(closes: list[float], period: int) -> list[float | None]:
    e = EMA(period)
    return [e.update(c) for c in closes]
