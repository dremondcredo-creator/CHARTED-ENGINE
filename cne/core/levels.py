"""Pivot detection and key-level clustering."""
from __future__ import annotations

from datetime import datetime, timezone
from cne.collectors import prices


def find_pivots(df, strength=3):
    if df is None or len(df) < 2 * strength + 1:
        return [], []
    highs, lows = [], []
    h = df["High"].values
    l = df["Low"].values
    idx = df.index

    for i in range(strength, len(df) - strength):
        hw = h[i - strength:i + strength + 1]
        lw = l[i - strength:i + strength + 1]
        if h[i] == hw.max() and (hw == h[i]).sum() == 1:
            highs.append((idx[i].isoformat(), float(h[i])))
        if l[i] == lw.min() and (lw == l[i]).sum() == 1:
            lows.append((idx[i].isoformat(), float(l[i])))
    return highs, lows


def _atr(df, n=14):
    if df is None or len(df) < n + 1: return None
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    trs = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
           for i in range(1, len(df))]
    return sum(trs[-n:]) / len(trs[-n:])


def cluster_levels(pivots, tolerance):
    if not pivots: return []
    clusters = []
    for ts, price in sorted(pivots, key=lambda x: x[1]):
        placed = False
        for c in clusters:
            if abs(price - c["center"]) <= tolerance:
                n = c["touches"]
                c["center"] = (c["center"] * n + price) / (n + 1)
                c["touches"] = n + 1
                if ts > c["last_ts"]: c["last_ts"] = ts
                placed = True
                break
        if not placed:
            clusters.append({"center": price, "touches": 1,
                             "first_ts": ts, "last_ts": ts})
    return clusters


def _recency(last_ts_iso, now_ts):
    try:
        ts = datetime.fromisoformat(last_ts_iso)
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return 0.5
    age_h = max(0.0, (now_ts - ts).total_seconds() / 3600.0)
    return max(0.3, 0.5 ** (age_h / 24.0))


def score_levels(clusters, atr, price, now_ts):
    out = []
    for c in clusters:
        dist = abs(c["center"] - price) / atr if atr else 0.0
        if dist > 6: continue
        rec = _recency(c["last_ts"], now_ts)
        strength = c["touches"] * rec / (1.0 + dist)
        c2 = dict(c)
        c2["strength"] = round(strength, 4)
        c2["dist_atr"] = round(dist, 3)
        out.append(c2)
    return sorted(out, key=lambda x: -x["strength"])


def detect_levels(conn, symbol, timeframe="5m", period="5d",
                  pivot_strength=3, atr_tol_frac=0.35):
    df = prices.fetch_ohlc(symbol, period=period, interval=timeframe)
    if df is None or df.empty: return []

    now_ts = datetime.now(timezone.utc)
    atr = _atr(df)
    if not atr: return []

    highs, lows = find_pivots(df, strength=pivot_strength)
    last_price = float(df["Close"].iloc[-1])

    tol = atr * atr_tol_frac
    res = score_levels(cluster_levels(highs, tol), atr, last_price, now_ts)
    sup = score_levels(cluster_levels(lows, tol), atr, last_price, now_ts)

    for ts, price in highs:
        conn.execute("""
            INSERT INTO pivots
              (symbol, timeframe, kind, ts_utc, price, strength, detected_utc)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT (symbol, timeframe, kind, ts_utc) DO NOTHING
        """, (symbol, timeframe, "HIGH", ts, price, pivot_strength, now_ts.isoformat()))
    for ts, price in lows:
        conn.execute("""
            INSERT INTO pivots
              (symbol, timeframe, kind, ts_utc, price, strength, detected_utc)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT (symbol, timeframe, kind, ts_utc) DO NOTHING
        """, (symbol, timeframe, "LOW", ts, price, pivot_strength, now_ts.isoformat()))

    for c, kind in [(c, "RESISTANCE") for c in res] + [(c, "SUPPORT") for c in sup]:
        conn.execute("""
            INSERT INTO key_levels
              (symbol, timeframe, kind, center, touches, first_ts_utc,
               last_ts_utc, atr_at_detect, strength, detected_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol, timeframe, kind, center) DO UPDATE SET
              touches=excluded.touches, first_ts_utc=excluded.first_ts_utc,
              last_ts_utc=excluded.last_ts_utc, atr_at_detect=excluded.atr_at_detect,
              strength=excluded.strength, detected_utc=excluded.detected_utc
        """, (symbol, timeframe, kind, round(c["center"], 6),
              c["touches"], c["first_ts"], c["last_ts"],
              round(atr, 6), c["strength"], now_ts.isoformat()))
    conn.commit()

    tagged = [{"kind": "RESISTANCE", **c} for c in res] + \
             [{"kind": "SUPPORT", **c} for c in sup]
    tagged.sort(key=lambda x: x["dist_atr"])
    return tagged


def nearest_levels_above_below(levels, price):
    above = [l for l in levels if l["center"] > price]
    below = [l for l in levels if l["center"] < price]
    a = min(above, key=lambda x: x["center"]) if above else None
    b = max(below, key=lambda x: x["center"]) if below else None
    return a, b