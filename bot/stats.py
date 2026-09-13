"""Motor de estadísticas por bot (aislado; solo datos reales de simulación)."""
from collections import defaultdict


def compute_stats(capital, closed, open_positions, prices):
    """closed: lista dicts de trades cerrados del bot (entry,exit,pnl,
    open_epoch,close_epoch,direction). open_positions: [(direction, entry,
    qty, price_actual)]. prices: fn(symbol)->precio (ya aplicado por llamada).
    Devuelve dict con todas las métricas + curvas."""
    n = len(closed)
    wins = [t for t in closed if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl") or 0) <= 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = -sum(t["pnl"] for t in losses)
    pnl = gross_w - gross_l
    balance = capital + pnl
    avg_w = gross_w / len(wins) if wins else 0.0
    avg_l = (-gross_l) / len(losses) if losses else 0.0
    best = max([t["pnl"] for t in closed], default=0.0)
    worst = min([t["pnl"] for t in closed], default=0.0)
    # rachas y duraciones
    max_w = max_l = w = l = 0
    durs = []
    for t in closed:
        durs.append(max(0, (t.get("close_epoch") or 0) - (t.get("open_epoch") or 0)))
        if t["pnl"] > 0:
            w += 1
            l = 0
            max_w = max(max_w, w)
        else:
            l += 1
            w = 0
            max_l = max(max_l, l)
    # curva de balance/equity y drawdown
    eq = capital
    peak = capital
    max_dd = 0.0
    bal_curve, eq_curve, dd_curve = [], [], []
    for t in closed:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = (eq - peak) / peak * 100 if peak else 0.0
        max_dd = min(max_dd, dd)
        bal_curve.append(eq)
        eq_curve.append(eq)
        dd_curve.append(dd)
    # abiertas
    open_pnl = sum(o["pnl"] for o in open_positions)
    equity = balance + open_pnl
    if open_positions:
        peak2 = max(peak, equity)
        dd_now = (equity - peak2) / peak2 * 100 if peak2 else 0.0
        max_dd = min(max_dd, dd_now)
    buys = sum(1 for t in closed if t.get("direction") == "LONG")
    per_trade = [t["pnl"] for t in closed]
    by_period = defaultdict(float)
    for t in closed:
        ep = t.get("close_epoch") or t.get("open_epoch") or 0
        by_period[ep // 2592000] += t["pnl"]  # buckets ~30d
    return {
        "capital": capital, "balance": round(balance, 2),
        "equity": round(equity, 2), "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / capital * 100, 2) if capital else 0.0,
        "total": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 2) if n else 0.0,
        "avg_win": round(avg_w, 4), "avg_loss": round(avg_l, 4),
        "best": round(best, 4), "worst": round(worst, 4),
        "profit_factor": round(gross_w / gross_l, 4) if gross_l else (float("inf") if gross_w else 0.0),
        "expectancy": round(pnl / n, 4) if n else 0.0,
        "max_drawdown": round(max_dd, 2),
        "max_win_streak": max_w, "max_loss_streak": max_l,
        "avg_duration": round(sum(durs) / len(durs), 1) if durs else 0.0,
        "buys": buys, "sells": n - buys,
        "open_count": len(open_positions),
        "open_pnl": round(open_pnl, 4),
        "open_pnl_pct": round(open_pnl / capital * 100, 4) if capital else 0.0,
        "per_trade": [round(x, 4) for x in per_trade],
        "balance_curve": [round(x, 2) for x in bal_curve],
        "equity_curve": [round(x, 2) for x in eq_curve],
        "drawdown_curve": [round(x, 2) for x in dd_curve],
        "by_period": {str(k): round(v, 2) for k, v in sorted(by_period.items())},
    }
