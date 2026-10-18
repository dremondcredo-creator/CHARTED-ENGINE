"""Three-light system: fundamental + market + structure."""
from __future__ import annotations

from cne.core.trend import detect_trend, reinforcement_status, event_history
from cne.core import market as market_mod


def _bias_dir(label):
    if "HAWKISH" in label: return 1
    if "DOVISH" in label: return -1
    return 0


def fundamental_light(conn, event_code, period, macro_score):
    from cne.core.events import BY_CODE
    if event_code not in BY_CODE:
        return {"light": "🟡", "label": "UNKNOWN", "score": 0}

    row = conn.execute("""
        SELECT r.id, r.actual, r.forecast, s.direction, s.impact
        FROM releases r LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.event_code = ? AND r.period = ?
    """, (event_code, period)).fetchone()
    if not row or not row["impact"]:
        return {"light": "🟡", "label": "NO RELEASE", "score": 0}

    hist = event_history(conn, event_code, 12)
    trend = detect_trend(hist)
    reinf = reinforcement_status(trend, row["direction"])

    imp_sign = {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0}[row["impact"]]
    mac_sign = _bias_dir(macro_score["label"])

    score = 0.0
    if imp_sign and mac_sign:
        score += 1.0 if imp_sign == mac_sign else -1.0
    if reinf == "REINFORCES":   score += 1.0
    elif reinf == "CONTRADICTS": score -= 1.0

    if score >= 1.5:   light = "🟢"
    elif score <= -1.5: light = "🔴"
    else: light = "🟡"

    return {
        "light": light, "score": round(score, 2),
        "latest_impact": row["impact"], "latest_direction": row["direction"],
        "trend_direction": trend["direction"], "reinforcement": reinf,
        "macro_label": macro_score["label"],
        "label": f"fundamental score {score:+.1f}",
        "release_id": row["id"],
    }


def full_light(conn, event_code, period, macro_score):
    fund = fundamental_light(conn, event_code, period, macro_score)
    rid = fund.get("release_id")
    market = market_mod.run_for_release(conn, rid) if rid else None

    if market is None:
        return {"light": fund["light"], "fundamental": fund,
                "market": None,
                "label": f"fundamental only ({fund['label']})",
                "notes": "market data missing"}

    combined = fund["score"] + market["score"]
    if combined >= 2.5:
        light, label = "🟢", "GREEN — fundamental + market aligned"
    elif combined <= -2.5:
        light, label = "🔴", "RED — fundamental + market contradict"
    elif combined >= 1.0:
        light, label = "🟡", "LEANING GREEN — mostly aligned"
    elif combined <= -1.0:
        light, label = "🟡", "LEANING RED — mostly contradicting"
    else:
        light, label = "🟡", "YELLOW — mixed"

    return {"light": light, "label": label, "combined_score": round(combined, 2),
            "fundamental": fund, "market": market}


def a_plus_light(conn, event_code, period, macro_score, symbol="GOLD"):
    from cne.core import structure as struct_mod
    fund = fundamental_light(conn, event_code, period, macro_score)

    row = conn.execute("""
        SELECT id FROM releases WHERE event_code=? AND period=?
    """, (event_code, period)).fetchone()
    if not row:
        return {"light": "🟡", "label": "NO RELEASE", "fundamental": fund,
                "market": None, "structure": None, "combined_score": 0}
    rid = row["id"]

    mkt = market_mod.run_for_release(conn, rid)
    struct = None
    srow = conn.execute("""
        SELECT * FROM structure_verdicts WHERE release_id=? AND symbol=?
    """, (rid, symbol)).fetchone()
    if srow:
        struct = dict(srow)
    else:
        live = struct_mod.classify_release(conn, rid, symbol)
        if live:
            struct_mod.persist(conn, live)
            struct = live

    f_score = fund.get("score", 0.0)
    m_score = mkt["score"] if mkt else 0.0
    s_score = struct["confirmation_score"] if struct else 0.0
    combined = f_score + m_score + s_score

    if combined >= 4.0:
        light, label = "🟢", "A+ SETUP — fully aligned"
    elif combined >= 2.0:
        light, label = "🟢", "STRONG — all three lean same way"
    elif combined <= -2.0:
        light, label = "🔴", "INVALIDATED — dimensions contradict"
    else:
        light, label = "🟡", "MIXED — no clean trade"

    return {"light": light, "label": label,
            "combined_score": round(combined, 2),
            "fundamental": fund, "market": mkt, "structure": struct,
            "symbol": symbol}