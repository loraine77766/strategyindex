"""Catálogo de activos CMC: sync, persistencia, resolución automática.

- sync(): listings/latest (top N por market cap) → tabla cmc_assets.
- resolve(query): por símbolo/nombre/slug/CMC ID, exacto insensible a
  mayúsculas; ante ambigüedad gana el de mejor rank. Sin IDs inventados:
  lo que no está en el catálogo no se resuelve (salvo map on-demand).
- El catálogo sobrevive reinicios (SQLite); sync solo cuando toca (TTL).
"""
import time
from .cmc_client import CMCClient
from .log import get_logger
from .providers_coinmarketcap import _quote_usd

log = get_logger("cmc-cat")
SYNC_TTL = 24 * 3600


class AssetCatalog:
    def __init__(self, client: CMCClient, db, limit=200, ttl=SYNC_TTL):
        self.client = client
        self.db = db
        self.limit = limit
        self.ttl = ttl

    def needs_sync(self):
        last = self.db.get("cmc_sync_ts", "")
        try:
            age = time.time() - float(last or 0)
        except ValueError:
            age = self.ttl + 1
        return (not last) or age > self.ttl or not self.db.asset_count()

    def sync(self, limit=None):
        """Descubre activos reales. Devuelve (n_nuevos, n_total)."""
        import urllib.parse
        lim = limit or self.limit
        qs = urllib.parse.urlencode({"start": 1, "limit": lim, "convert": "USD"})
        data = self.client.get(f"/v3/cryptocurrency/listings/latest?{qs}")
        items = data.get("data", [])
        if not items:
            raise RuntimeError("listings sin datos")
        n = 0
        for it in items:
            q = _quote_usd(it)
            n += self.db.save_asset({
                "cmc_id": it.get("id"), "symbol": (it.get("symbol") or "").upper(),
                "name": it.get("name") or "", "slug": it.get("slug") or "",
                "rank": it.get("cmc_rank"),
                "price": q.get("price"), "volume_24h": q.get("volume_24h"),
                "market_cap": q.get("market_cap"),
                "pairs": it.get("num_market_pairs"),
                "updated": q.get("last_updated")})
        self.db.set("cmc_sync_ts", str(int(time.time())))
        total = self.db.asset_count()
        log.info("catálogo CMC: %d sincronizados, %d en total", n, total)
        return n, total

    def resolve(self, query):
        """Resuelve 'Tesla 5m'/'Gold 1m'/'BTC' sin que el usuario dé IDs.
        Devuelve dict del activo o None (NO inventa)."""
        q = (query or "").strip()
        if not q:
            return None
        # quita timeframe pegado ("Tesla 5m" -> "Tesla")
        first = q.split()[0]
        if first.isdigit():
            return self.db.get_asset_by_id(int(first))
        hit = self.db.search_assets(first)
        if hit:
            return hit[0]
        # on-demand vía map (no persiste basura: solo si hay match exacto)
        import urllib.parse
        try:
            qs = urllib.parse.urlencode({"symbol": first.upper(), "limit": 5})
            data = self.client.get(f"/v1/cryptocurrency/map?{qs}")
        except Exception as e:
            log.warning("resolve map %s: %s", first, str(e)[:120])
            return None
        cands = [it for it in data.get("data", [])
                 if (it.get("symbol") or "").upper() == first.upper()
                 and it.get("is_active")]
        if not cands:
            return None
        cands.sort(key=lambda it: (it.get("rank") or 10 ** 9))
        best = cands[0]
        return {"cmc_id": best.get("id"), "symbol": best.get("symbol"),
                "name": best.get("name"), "slug": best.get("slug"),
                "rank": best.get("rank"), "price": None, "volume_24h": None,
                "market_cap": None, "pairs": None, "updated": None,
                "on_demand": True}
