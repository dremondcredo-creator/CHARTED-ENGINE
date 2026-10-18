"""Outcome labelling for each release."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from cne.collectors import prices

MIN_MOVE_PCT = {"GOLD": 0.15, "EURUSD": 0.05}


def _parse(s):
    if not s: return None
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _snap(conn, release_id, phase, symbol):
    r = conn.execute("""
        SELECT price FROM price_snapshots
        WHERE release_id=? AND phase=? AND symbol=?
    """, (release_id, phase, symbol)).fetchone()
    return r["price"] if r else None


def implied_direction(impact, symbol):
    if impact == "NEUTRAL": return "NEUTRAL"
    if symbol in ("GOLD", "EURUSD"):
        return "DOWN" if impact == "BULLISH" else "UP"
    return "NEUTRAL"


def _mfe_mae(df, entry, direction, start, end):
    window = df[(df.index > start) & (df.index <= end)]
    if window.empty: return None, None
    highs, lows = window["High"].values, window["Low"].values
    if direction == "UP":
        best = (max(highs) - entry) / entry * 100
        worst = (min(lows) - entry) / entry * 100
    else:
        best = (entry - min(lows)) / entry * 100
        worst = (entry - max(highs)) / entry * 100
    return round(max(0.0, best), 4), round(max(0.0, -worst), 4)


def _classify(change, direction, min_move):
    if change is None: return "FLAT", 0.0
    signed = change if direction == "UP" else -change
    if signed >= min_move: return "WIN", round(signed, 4)
    if signed <= -min_move: return "LOSS", round(signed, 4)
    return "FLAT", round(signed, 4)


def compute_for(conn, release_id, symbol):
    rel = conn.execute("""
        SELECT r.release_time_utc, s.impact
        FROM releases r LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.id = ?
    """, (release_id,)).fetchone()
    if not rel or not rel["impact"]: return None

    ts = _parse(rel["release_time_utc"])
    if not ts: return None
    direction = implied_direction(rel["impact"], symbol)
    if direction == "NEUTRAL": return None

    price_t30 = _snap(conn, release_id, "T+30M", symbol)
    if price_t30 is None: return None

    entry_ts = ts + timedelta(minutes=30)
    end_2h = entry_ts + timedelta(hours=2)
    end_1d = entry_ts + timedelta(hours=24)

    try:
        df = prices.fetch_ohlc(symbol, period="60d", interval="5m")
    except Exception:
        df = None

    price_t2h = price_t1d = None
    mfe = mae = None
    if df is not None and not df.empty:
        r2 = df[df.index <= end_2h]
        if not r2.empty: price_t2h = float(r2["Close"].iloc[-1])
        r1 = df[df.index <= end_1d]
        if not r1.empty: price_t1d = float(r1["Close"].iloc[-1])
        mfe, mae = _mfe_mae(df, price_t30, direction, entry_ts, end_2h)

    pre = _snap(conn, release_id, "PRE", symbol)
    chg30 = ((price_t30 - pre) / pre * 100) if pre else None
    chg2h = ((price_t2h - price_t30) / price_t30 * 100) if price_t2h else None
    chg1d = ((price_t1d - price_t30) / price_t30 * 100) if price_t1d else None

    outcome, magnitude = _classify(chg2h, direction, MIN_MOVE_PCT.get(symbol, 0.10))

    return {"release_id": release_id, "symbol": symbol,
            "implied_direction": direction,
            "entry_price": price_t30, "price_t30m": price_t30,
            "price_t2h": price_t2h, "price_t1d": price_t1d,
            "change_t30m_pct": round(chg30, 4) if chg30 is not None else None,
            "change_t2h_pct": round(chg2h, 4) if chg2h is not None else None,
            "change_t1d_pct": round(chg1d, 4) if chg1d is not None else None,
            "mfe_2h_pct": mfe, "mae_2h_pct": mae,
            "outcome": outcome, "outcome_magnitude": magnitude,
            "assessment_horizon": "T+2H"}


def persist(conn, result):
    if not result: return
    conn.execute("""
        INSERT INTO outcome_labels
          (release_id, symbol, implied_direction, entry_price,
           price_t30m, price_t2h, price_t1d,
           change_t30m_pct, change_t2h_pct, change_t1d_pct,
           mfe_2h_pct, mae_2h_pct, outcome, outcome_magnitude,
           assessment_horizon, computed_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id, symbol) DO UPDATE SET
          implied_direction=excluded.implied_direction,
          entry_price=excluded.entry_price, price_t30m=excluded.price_t30m,
          price_t2h=excluded.price_t2h, price_t1d=excluded.price_t1d,
          change_t30m_pct=excluded.change_t30m_pct,
          change_t2h_pct=excluded.change_t2h_pct,
          change_t1d_pct=excluded.change_t1d_pct,
          mfe_2h_pct=excluded.mfe_2h_pct, mae_2h_pct=excluded.mae_2h_pct,
          outcome=excluded.outcome, outcome_magnitude=excluded.outcome_magnitude,
          assessment_horizon=excluded.assessment_horizon,
          computed_utc=excluded.computed_utc
    """, (result["release_id"], result["symbol"], result["implied_direction"],
          result["entry_price"], result["price_t30m"],
          result["price_t2h"], result["price_t1d"],
          result["change_t30m_pct"], result["change_t2h_pct"],
          result["change_t1d_pct"], result["mfe_2h_pct"], result["mae_2h_pct"],
          result["outcome"], result["outcome_magnitude"],
          result["assessment_horizon"], datetime.now(timezone.utc).isoformat()))
    conn.commit()


def refresh_all(conn, symbols=("GOLD", "EURUSD"), days_back=90):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    rows = conn.execute("""
        SELECT DISTINCT r.id FROM releases r
        JOIN surprises s ON s.release_id = r.id
        JOIN price_snapshots p ON p.release_id = r.id AND p.phase='T+30M'
        WHERE r.release_time_utc >= ?
    """, (cutoff,)).fetchall()
    n = 0
    for r in rows:
        for sym in symbols:
            res = compute_for(conn, r["id"], sym)
            if res:
                persist(conn, res)
                n += 1
    return n