"""Backtest simulation engine."""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from cne.backtest import rules as bt_rules


def _matches(feature, flt):
    for k, v in flt.items():
        if feature.get(k) != v: return False
    return True


def _fetch(conn, start, end):
    rows = conn.execute("""
        SELECT f.*, o.implied_direction, o.entry_price,
               o.change_t2h_pct, o.mfe_2h_pct, o.mae_2h_pct,
               o.outcome, o.outcome_magnitude, r.release_time_utc
        FROM setup_features f
        JOIN outcome_labels o ON o.release_id = f.release_id
        JOIN releases r ON r.id = f.release_id
        WHERE r.release_time_utc >= ? AND r.release_time_utc <= ?
    """, (start, end)).fetchall()
    return [dict(r) for r in rows]


def _simulate(feature, side, rule):
    underlying = feature.get("change_t2h_pct")
    if underlying is None: return None, None, "NO_DATA"
    gross = underlying if side == "LONG" else -underlying
    mfe = feature.get("mfe_2h_pct") or 0.0
    mae = feature.get("mae_2h_pct") or 0.0
    reason = "HORIZON"
    if rule["stop_pct"] is not None and mae >= rule["stop_pct"]:
        gross, reason = -rule["stop_pct"], "STOP"
    elif rule["target_pct"] is not None and mfe >= rule["target_pct"]:
        gross, reason = rule["target_pct"], "TARGET"
    cost = (rule["spread_cost_pct"] or 0) + (rule["slippage_pct"] or 0)
    return round(gross, 4), round(gross - cost, 4), reason


def _curve(trades):
    curve, cum = [], 1.0
    for t in trades:
        cum *= (1 + t["net_pct"] / 100)
        curve.append({"ts": t["entry_ts"], "cum_pct": round((cum - 1) * 100, 4)})
    return curve


def _max_dd(curve):
    peak, dd = 0.0, 0.0
    for p in curve:
        v = p["cum_pct"]
        if v > peak: peak = v
        if peak - v > dd: dd = peak - v
    return round(dd, 4)


def run(conn, rule_name, symbol="GOLD", start_ts="2000-01-01", end_ts="2099-01-01"):
    rule = bt_rules.get_rules(conn, rule_name)
    if not rule: return {"error": f"rule '{rule_name}' not found"}

    trades = []
    for f in _fetch(conn, start_ts, end_ts):
        if not _matches(f, rule["entry_filter"]): continue
        side = "LONG" if f["implied_direction"] == "UP" else "SHORT"
        g, n, r = _simulate(f, side, rule)
        if g is None: continue
        try:
            dt = datetime.fromisoformat(f["release_time_utc"].replace("Z", "+00:00"))
            entry = (dt + timedelta(minutes=rule["entry_offset_min"])).isoformat()
        except Exception:
            entry = f["release_time_utc"]
        trades.append({"release_id": f["release_id"], "side": side,
                       "entry_price": f.get("entry_price"), "entry_ts": entry,
                       "gross_pct": g, "net_pct": n, "exit_reason": r,
                       "mfe_pct": f.get("mfe_2h_pct"),
                       "mae_pct": f.get("mae_2h_pct")})

    if not trades: return {"error": "no trades matched", "n_trades": 0}

    wins = [t for t in trades if t["net_pct"] > 0]
    losses = [t for t in trades if t["net_pct"] < 0]
    flats = [t for t in trades if t["net_pct"] == 0]
    gw = sum(t["net_pct"] for t in wins)
    gl = abs(sum(t["net_pct"] for t in losses))
    aw = statistics.mean([t["net_pct"] for t in wins]) if wins else 0
    al = statistics.mean([t["net_pct"] for t in losses]) if losses else 0
    rets = [t["net_pct"] for t in trades]
    mr = statistics.mean(rets)
    sd = statistics.stdev(rets) if len(rets) >= 2 else 0
    sh = (mr / sd) if sd else 0
    exp = (len(wins) / len(trades)) * aw + (len(losses) / len(trades)) * al
    curve = _curve(trades)

    cur = conn.execute("""
        INSERT INTO backtest_runs
          (rules_id, rules_name, symbol, start_ts_utc, end_ts_utc,
           n_trades, n_wins, n_losses, n_flats, win_rate,
           avg_win_pct, avg_loss_pct, expectancy_pct, profit_factor,
           sharpe_like, max_drawdown_pct, total_return_pct,
           equity_curve, notes, computed_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        RETURNING id
    """, (rule["id"], rule_name, symbol, start_ts, end_ts,
          len(trades), len(wins), len(losses), len(flats),
          round(len(wins) / len(trades), 4), round(aw, 4), round(al, 4),
          round(exp, 4), round(gw / gl, 4) if gl else None,
          round(sh, 4), _max_dd(curve),
          round(curve[-1]["cum_pct"], 4) if curve else 0.0,
          json.dumps(curve), None,
          datetime.now(timezone.utc).isoformat()))
    run_id = cur.fetchone()["id"]

    for t in trades:
        conn.execute("""
            INSERT INTO backtest_trades
              (run_id, release_id, symbol, side, entry_price,
               entry_ts_utc, gross_pct, net_pct, exit_reason, mfe_pct, mae_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (run_id, t["release_id"], symbol, t["side"], t["entry_price"],
              t["entry_ts"], t["gross_pct"], t["net_pct"],
              t["exit_reason"], t["mfe_pct"], t["mae_pct"]))
    conn.commit()
    return {"id": run_id, "rules_name": rule_name, "symbol": symbol,
            "n_trades": len(trades), "n_wins": len(wins), "n_losses": len(losses),
            "n_flats": len(flats), "win_rate": round(len(wins) / len(trades), 4),
            "avg_win_pct": round(aw, 4), "avg_loss_pct": round(al, 4),
            "expectancy_pct": round(exp, 4),
            "profit_factor": round(gw / gl, 4) if gl else None,
            "sharpe_like": round(sh, 4), "max_drawdown_pct": _max_dd(curve),
            "total_return_pct": round(curve[-1]["cum_pct"], 4) if curve else 0.0,
            "start_ts_utc": start_ts, "end_ts_utc": end_ts}


def run_all_default(conn, symbol="GOLD", days_back=365):
    end = datetime.now(timezone.utc).isoformat()
    start = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    return [run(conn, r["name"], symbol, start, end)
            for r in bt_rules.list_rules(conn)]