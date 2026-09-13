"""Cliente HTTP compartido de CoinMarketCap (una sola instancia, una caché).

- Sin key: base keyless .../public-api, sin headers sensibles.
- Con key (CMC_API_KEY en entorno, nunca en código): base pro + header
  X-CMC_PRO_API_KEY. Hoy NO hay key configurada: todo lo keyed queda
  PENDIENTE_DE_CONFIRMAR y el cliente opera en modo keyless.
- Lleva estadísticas propias: llamadas, 429, último error/ok (para admin).
"""
import json
import time
import urllib.error
import urllib.request
from .log import get_logger

log = get_logger("cmc")
KEYLESS_BASE = "https://pro-api.coinmarketcap.com/public-api"
KEYED_BASE = "https://pro-api.coinmarketcap.com"


class CMCClient:
    def __init__(self, api_key="", timeout=10.0, max_retries=4):
        self.api_key = (api_key or "").strip()
        self.base = KEYED_BASE if self.api_key else KEYLESS_BASE
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls = 0
        self.errors_429 = 0
        self.last_error = ""
        self.last_ok = 0

    def _sleep(self, s):
        time.sleep(s)

    @property
    def mode(self):
        return "keyed" if self.api_key else "keyless"

    def headers(self):
        h = {"Accept": "application/json"}
        if self.api_key:
            h["X-CMC_PRO_API_KEY"] = self.api_key
        return h

    def get(self, path: str):
        url = self.base + path
        last = None
        for i in range(self.max_retries):
            try:
                req = urllib.request.Request(url, headers=self.headers())
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    try:
                        data = json.loads(r.read().decode("utf-8"))
                    except Exception as e:
                        raise RuntimeError(f"JSON inválido en {path}: {e}")
                if isinstance(data, dict) and str(data.get("status", {}).get("error_code", "0")) != "0":
                    raise RuntimeError(f"CMC error {data['status'].get('error_code')}: "
                                       f"{data['status'].get('error_message')}")
                self.calls += 1
                self.last_ok = int(time.time())
                self.last_error = ""
                return data
            except urllib.error.HTTPError as e:
                last = RuntimeError(f"HTTP {e.code} en {path}")
                if e.code == 429 or e.code >= 500:
                    if e.code == 429:
                        self.errors_429 += 1
                    if i < self.max_retries - 1:
                        wait = 2 ** i
                        log.warning("CMC %d: backoff %ss (intento %d)", e.code, wait, i + 1)
                        self._sleep(wait)
                        continue
                self.last_error = str(last)
                raise last
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = RuntimeError(f"red/timeout en {path}: {e}")
                if i < self.max_retries - 1:
                    self._sleep(2 ** i)
                    continue
                self.last_error = str(last)
                raise last
        self.last_error = str(last)
        raise last

    def stats(self):
        return {"mode": self.mode, "calls": self.calls,
                "errors_429": self.errors_429, "last_ok": self.last_ok,
                "last_error": self.last_error,
                "has_key": bool(self.api_key)}
