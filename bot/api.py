"""StrategyIndex API: pública (solo lectura, sin estrategia) vs admin (token).

- Público: nombre, estado, capital, balance, equity, P&L, rendimiento,
  operaciones, win rate, drawdown, abiertas, historial, gráficos. NUNCA
  estrategia, EMAs, parámetros, señales internas ni provider.
- Admin (`X-Admin-Token: $ADMIN_TOKEN`): todo lo técnico + controles.
  Rate-limit: ADMIN_RATE_LIMIT req/ADMIN_RATE_WINDOW s por IP (default 60/60).
Sin token configurado, lo admin responde 403.
"""
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

FRONT = Path(__file__).parent / "frontend"


class _RateLimiter:
    """Sliding-window per IP. Solo para endpoints admin."""

    def __init__(self):
        self._windows: dict[str, deque] = defaultdict(deque)

    def check(self, ip: str, limit: int, window: int):
        now = time.time()
        dq = self._windows[ip]
        while dq and dq[0] < now - window:
            dq.popleft()
        if len(dq) >= limit:
            retry = int(dq[0] + window - now) + 1
            raise HTTPException(
                status_code=429,
                detail=f"rate limit: {limit}/{window}s (retry {retry}s)")
        dq.append(now)


_rate = _RateLimiter()


def _admin_token():
    return os.environ.get("ADMIN_TOKEN", "")


def require_admin(request: Request, x_admin_token: str = Header(default="")):
    expected = _admin_token()
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=403, detail="admin only")
    ip = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        ip = fwd.split(",")[0].strip()
    limit = int(os.environ.get("ADMIN_RATE_LIMIT", "60"))
    window = int(os.environ.get("ADMIN_RATE_WINDOW", "60"))
    _rate.check(ip, limit, window)
    return True


def _public_bot(orch, bid):
    rt = orch.bots.get(bid)
    if not rt:
        raise HTTPException(status_code=404, detail="bot inexistente")
    st = orch.bot_stats(bid)
    d = rt.bot.to_dict(public=True)
    d.update({"status": rt.bot.status, "state": rt.state,
              "last_tick": rt.last_tick, "last_candle": rt.last_candle,
              "balance": st["balance"], "equity": st["equity"],
              "pnl": st["pnl"], "pnl_pct": st["pnl_pct"],
              "total": st["total"], "win_rate": st["win_rate"],
              "max_drawdown": st["max_drawdown"]})
    opens = []
    for p in rt.broker.positions.values():
        cur = (orch.prices.get(p.symbol) or {}).get("price", p.entry)
        upnl = ((cur - p.entry) if p.side == "LONG" else (p.entry - cur)) * p.qty
        opens.append({"id": p.id, "symbol": p.symbol,
                      "direction": "BUY" if p.side == "LONG" else "SELL",
                      "entry": p.entry, "current": cur,
                      "pnl": round(upnl, 4),
                      "pnl_pct": round(upnl / (p.entry * p.qty) * 100, 2) if p.entry and p.qty else 0.0,
                      "open_epoch": p.open_epoch, "status": "open"})
    d["open_position"] = opens[0] if opens else None
    d["open_count"] = len(opens)
    return d


def _public_trade(t):
    return {"id": t.get("id"), "bot_id": t.get("bot_id"), "symbol": t.get("symbol"),
            "timeframe": t.get("timeframe"), "direction": t.get("direction"),
            "entry": t.get("entry"), "exit": t.get("exit"),
            "open_epoch": t.get("open_epoch"), "close_epoch": t.get("close_epoch"),
            "pnl": t.get("pnl"), "pnl_pct": t.get("pnl_pct"),
            "duration": t.get("duration")}


def create_app(orch, app=None):
    if app is None:
        app = FastAPI(title="StrategyIndex")

    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])

    @app.get("/health")
    def health():
        return {"ok": True, "time": int(time.time())}

    # ---------- PÚBLICO ----------
    @app.get("/api/bots")
    def bots(bot: str = "", symbol: str = "", timeframe: str = "",
             periodo: str = "", estado: str = ""):
        out = []
        for bid in orch.bots:
            d = _public_bot(orch, bid)
            if bot and bot.lower() not in d["name"].lower():
                continue
            if symbol and d["symbol"] != symbol:
                continue
            if timeframe and d["timeframe"] != timeframe:
                continue
            if estado and d["status"] != estado:
                continue
            out.append(d)
        return out

    @app.get("/api/bots/{bot_id}")
    def bot_detail(bot_id: str):
        return _public_bot(orch, bot_id)

    @app.get("/api/bots/{bot_id}/stats")
    def bot_stats(bot_id: str):
        if bot_id not in orch.bots:
            raise HTTPException(status_code=404, detail="bot inexistente")
        return orch.bot_stats(bot_id)

    @app.get("/api/bots/{bot_id}/history")
    def bot_history(bot_id: str, limit: int = 200):
        if bot_id not in orch.bots:
            raise HTTPException(status_code=404, detail="bot inexistente")
        return [_public_trade(t) for t in orch.db.trade_history(limit, bot_id)]

    @app.get("/api/markets")
    def markets():
        out = []
        for m in orch.db.markets():
            p = orch.prices.get(m["symbol"], {})
            out.append({"symbol": m["symbol"], "on": bool(m["onoff"]),
                        "price": p.get("price"),
                        "change": round(p.get("change", 0) or 0, 3)})
        return out

    @app.get("/api/candles")
    def candles(symbol: str, timeframe: str, n: int = 120):
        rt = next((r for r in orch.bots.values()
                   if r.bot.symbol == symbol and r.bot.timeframe == timeframe
                   and r.bot.status == "running"), None)
        if not rt:
            return {"candles": [], "markers": []}
        cs = rt.builder.closed[-n:]
        marks = [{"epoch": s["epoch"], "direction": s["direction"]}
                 for s in orch.db.signals(500)
                 if s["symbol"] == symbol and s["timeframe"] == timeframe]
        return {"candles": [{"epoch": c.open_epoch, "open": c.open, "high": c.high,
                             "low": c.low, "close": c.close} for c in cs],
                "markers": marks}

    # ---------- ADMIN ----------
    @app.get("/api/admin/bots")
    def admin_bots(admin: bool = Depends(require_admin)):
        out = []
        for bid, rt in orch.bots.items():
            d = rt.bot.to_dict(public=False)
            d["stats"] = orch.bot_stats(bid)
            d["positions"] = [{"id": p.id, "side": p.side, "entry": p.entry,
                               "qty": p.qty, "open_epoch": p.open_epoch}
                              for p in rt.broker.positions.values()]
            out.append(d)
        return out

    @app.post("/api/admin/bots")
    async def admin_create(body: dict, admin: bool = Depends(require_admin)):
        try:
            b = orch.create_bot(body["strategy_key"], body["symbol"],
                                body.get("provider", "deriv"), body["timeframe"],
                                float(body.get("capital", 50.0)),
                                float(body.get("lot", 0.01)),
                                int(body.get("max_positions", 1)),
                                float(body.get("spread", 0.0001)),
                                float(body.get("commission", 0.0005)))
            seeded = await orch.seed_bot(b.bot_id)
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        d = b.to_dict()
        d["seeded_candles"] = seeded
        return d

    @app.delete("/api/admin/bots/{bot_id}")
    def admin_delete(bot_id: str, admin: bool = Depends(require_admin)):
        rt = orch.bots.get(bot_id)
        if not rt:
            raise HTTPException(status_code=404, detail="bot inexistente")
        orch.db.delete_bot(bot_id)
        del orch.bots[bot_id]
        return {"deleted": bot_id}

    @app.patch("/api/admin/bots/{bot_id}")
    def admin_update(bot_id: str, body: dict, admin: bool = Depends(require_admin)):
        if "timeframe" in body:
            cur = orch.bots.get(bot_id)
            if cur and body["timeframe"] != cur.bot.timeframe:
                raise HTTPException(status_code=400,
                                    detail="timeframe inmutable: crea otro bot")
        try:
            b = orch.update_bot(bot_id, **{k: body[k] for k in
                                           ("capital", "lot", "max_positions",
                                            "status", "spread", "commission")
                                           if k in body})
        except KeyError:
            raise HTTPException(status_code=404, detail="bot inexistente")
        return b.to_dict()

    @app.get("/api/admin/bots/{bot_id}/signals")
    def admin_signals(bot_id: str, limit: int = 100, admin: bool = Depends(require_admin)):
        rt = orch.bots.get(bot_id)
        if not rt:
            raise HTTPException(status_code=404, detail="bot inexistente")
        return [s for s in orch.db.signals(limit)
                if s["symbol"] == rt.bot.symbol and s["timeframe"] == rt.bot.timeframe]

    # ---------- ADMIN: Market Data (CMC) ----------
    @app.get("/api/admin/market")
    def admin_market(admin: bool = Depends(require_admin)):
        cat = orch._cmc()
        return {"provider": orch.provider.name,
                "state": {b: r.state for b, r in orch.bots.items()},
                "catalog_assets": orch.db.asset_count(),
                "last_sync": orch.db.get("cmc_sync_ts", ""),
                "needs_sync": cat.needs_sync(),
                "client": cat.client.stats()}

    @app.post("/api/admin/market/sync")
    def admin_market_sync(body: dict = None, admin: bool = Depends(require_admin)):
        try:
            n, total = orch.sync_catalog((body or {}).get("limit"))
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)[:200])
        return {"synced": n, "total": total}

    @app.get("/api/admin/market/assets")
    def admin_market_assets(q: str = "", limit: int = 20,
                            admin: bool = Depends(require_admin)):
        if not q:
            raise HTTPException(status_code=400, detail="q requerido")
        return orch.db.search_assets(q, limit)

    @app.get("/api/admin/market/resolve")
    def admin_market_resolve(q: str, admin: bool = Depends(require_admin)):
        hit = orch.resolve_asset(q)
        if not hit:
            raise HTTPException(status_code=404,
                                detail=f"activo no disponible: {q}")
        return hit

    @app.get("/api/admin/candles")
    def admin_candles(symbol: str, timeframe: str, n: int = 120,
                      admin: bool = Depends(require_admin)):
        from .indicators import ema_series
        rt = next((r for r in orch.bots.values()
                   if r.bot.symbol == symbol and r.bot.timeframe == timeframe), None)
        if not rt:
            return {"candles": [], "emas": {}, "markers": []}
        cs = rt.builder.closed[-n:]
        closes = [c.close for c in cs]
        return {"candles": [{"epoch": c.open_epoch, "open": c.open, "high": c.high,
                             "low": c.low, "close": c.close} for c in cs],
                "emas": {str(p): ema_series(closes, p)[-len(cs):] for p in (30, 75, 150)},
                "strategy": rt.strategy_name,
                "markers": [{"epoch": s["epoch"], "direction": s["direction"]}
                            for s in orch.db.signals(500)
                            if s["symbol"] == symbol and s["timeframe"] == timeframe]}

    @app.get("/api/admin/status")
    def admin_status(admin: bool = Depends(require_admin)):
        return {"conn": orch.conn, "provider": orch.provider.name,
                "bots": len(orch.bots)}

    @app.websocket("/ws/ui")
    async def ws_ui(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"kind": "status", "data": {"conn": orch.conn}})
        while True:
            msg = await orch.ui.get()
            try:
                await ws.send_json(msg)
            except Exception:
                break

    if FRONT.exists():
        app.mount("/static", StaticFiles(directory=str(FRONT)), name="static")

        @app.get("/")
        def index():
            return FileResponse(str(FRONT / "index.html"))

    return app


def run_api(orch):
    import uvicorn
    uvicorn.run(create_app(orch), host=orch.s.host, port=orch.s.port, log_level="warning")
