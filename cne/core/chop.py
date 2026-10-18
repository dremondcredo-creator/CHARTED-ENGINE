"""Chop detector — structure cleanliness score 0–100."""
from __future__ import annotations

from datetime import datetime, timezone
from cne.collectors import prices


def _atr(df, n=14):
    if len(df) < n + 1: return None
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    trs = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
           for i in range(1, len(df))]
    return sum(trs[-n:]) / len(trs[-n:])


def _wick_ratio(df, n=30):
    if len(df) < n: n = len(df)
    tail = df.tail(n)
    rng = (tail["High"] - tail["Low"]).sum()
    body = (tail["Close"] - tail["Open"]).abs().sum()
    if rng == 0: return 0.0
    return 1.0 - (body / rng)


def _flip_rate(df, n=40):
    if len(df) < 3: return 0.0
    closes = df.tail(n)["Close"].values
    changes = [1 if closes[i] > closes[i-1] else (-1 if closes[i] < closes[i-1] else 0)
               for i in range(1, len(closes))]
    if len(changes) < 2: return 0.0
    flips = sum(1 for i in range(1, len(changes))
                if changes[i] and changes[i-1] and changes[i] != changes[i-1])
    return flips / (len(changes) - 1)


def _trend_r2(df, n=40):
    if len(df) < 5: return 0.0
    ys = df.tail(n)["Close"].values
    n = len(ys)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    ss_tot = sum((y - my) ** 2 for y in ys)
    if ss_tot == 0: return 1.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den else 0
    intercept = my - slope * mx
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return max(0.0, 1.0 - ss_res / ss_tot)


def chop_score(df):
    if df is None or len(df) < 20: return None
    price = float(df["Close"].iloc[-1])
    atr = _atr(df)
    atr_norm = (atr / price * 100) if (atr and price) else 0.0
    wick = _wick_ratio(df)
    flip = _flip_rate(df)
    r2 = _trend_r2(df)

    s_atr = min(100.0, atr_norm * 100.0)
    s_wick = min(100.0, wick / 0.8 * 100.0)
    s_flip = min(100.0, flip / 0.6 * 100.0)
    s_r2 = max(0.0, (1.0 - r2) * 100.0)
    chop = 0.25 * s_atr + 0.30 * s_wick + 0.25 * s_flip + 0.20 * s_r2

    return {
        "chop_score": round(chop, 2),
        "atr_norm": round(atr_norm, 4),
        "wick_ratio": round(wick, 4),
        "dir_change_rate": round(flip, 4),
        "trend_r2": round(r2, 4),
        "n_bars": len(df),
    }


def label(chop):
    if chop is None: return "UNKNOWN"
    s = chop["chop_score"]
    if s <= 30: return "CLEAN"
    if s <= 60: return "MIXED"
    return "CHOPPY"


def evaluate_symbol(conn, symbol, timeframe="5m", period="5d"):
    try:
        df = prices.fetch_ohlc(symbol, period=period, interval=timeframe)
    except Exception:
        return None
    result = chop_score(df)
    if result is None: return None
    result["symbol"] = symbol
    result["timeframe"] = timeframe
    result["label"] = label(result)
    conn.execute("""
        INSERT INTO chop_snapshots
          (ts_utc, symbol, timeframe, chop_score, chop_label,
           atr_norm, wick_ratio, dir_change_rate, trend_r2, n_bars)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(), symbol, timeframe,
          result["chop_score"], result["label"], result["atr_norm"],
          result["wick_ratio"], result["dir_change_rate"],
          result["trend_r2"], result["n_bars"]))
    conn.commit()
    return result


def prefer_instrument(conn, gold=None, eurusd=None):
    if gold is None:
        gold = evaluate_symbol(conn, "GOLD")
    if eurusd is None:
        eurusd = evaluate_symbol(conn, "EURUSD")

    if gold is None and eurusd is None:
        return None, "no data"
    if gold is None:
        return "EURUSD", "Gold data unavailable"
    if eurusd is None:
        return "GOLD", "EURUSD data unavailable"

    diff = gold["chop_score"] - eurusd["chop_score"]
    if abs(diff) < 5:
        return None, f"tie (Gold {gold['label']} vs EURUSD {eurusd['label']})"
    if diff < 0:
        return "GOLD", f"Gold cleaner ({gold['label']} vs {eurusd['label']})"
    return "EURUSD", f"EURUSD cleaner ({eurusd['label']} vs {gold['label']})"