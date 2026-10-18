"""Pair directions — relative bias per instrument."""
from __future__ import annotations

from cne.core import macro as macro_mod

PAIR_MAP = {
    "EURUSD": ("US", "EU"),
    "GBPUSD": ("US", "UK"),
    "AUDUSD": ("US", "AU"),
    "USDJPY": ("US", "JP"),
    "GOLD":   ("US", "GOLD"),
}


def _gold_dir(us_score):
    if us_score["total"] >= 0.5:
        return "BEARISH", "US hawkish — tightening pressure on Gold"
    if us_score["total"] <= -0.5:
        return "BULLISH", "US dovish — easing pressure supports Gold"
    return "NEUTRAL", "US macro neutral"


def _fx_dir(a_total, b_total):
    diff = a_total - b_total
    if diff >= 0.5:
        return "UP", f"base stronger ({diff:+.2f})"
    if diff <= -0.5:
        return "DOWN", f"quote stronger ({diff:+.2f})"
    return "NEUTRAL", f"no differential ({diff:+.2f})"


def pair_direction(conn, symbol, window_days=30):
    if symbol not in PAIR_MAP: return None
    base, quote = PAIR_MAP[symbol]
    a = macro_mod.compute_macro_score(conn, base, window_days)

    if quote == "GOLD":
        d, note = _gold_dir(a)
        return {"symbol": symbol, "base_country": base, "quote_country": "—",
                "base_total": a["total"], "quote_total": None,
                "diff": None, "direction": d, "notes": note}

    b = macro_mod.compute_macro_score(conn, quote, window_days)
    d, note = _fx_dir(a["total"], b["total"])
    return {"symbol": symbol, "base_country": base, "quote_country": quote,
            "base_total": a["total"], "quote_total": b["total"],
            "diff": round(a["total"] - b["total"], 3),
            "direction": d, "notes": f"{base} vs {quote}: {note}"}


def all_pair_directions(conn, window_days=30):
    return {sym: pair_direction(conn, sym, window_days) for sym in PAIR_MAP}