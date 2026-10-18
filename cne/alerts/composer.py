"""Alert message templates."""
from __future__ import annotations

from cne.alerts.html import esc
from cne.core import chop as chop_mod
from cne.core import chop_history as chop_hist_mod
from cne.core import levels as levels_mod
from cne.core import market as market_mod
from cne.core.events import BY_CODE
from cne.db.database import connect

DIR_ARROW = {"RISING": "▲", "FALLING": "▼", "FLAT": "＝",
             "UPSIDE": "▲", "DOWNSIDE": "▼", "INLINE": "＝"}
IMPACT_EMOJI = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
ALIGN_TONE = {"CONFIRMS": "🟢", "CONTRADICTS": "🔴", "NEUTRAL": "🟡"}

CB_BY_COUNTRY = {"US": "Fed", "EU": "ECB", "UK": "BoE",
                 "JP": "BoJ", "CA": "BoC", "AU": "RBA"}


def _fmt(v, digits=2):
    return "—" if v is None else f"{v:.{digits}f}"


def _load(conn, release_id):
    return conn.execute("""
        SELECT r.id, r.event_code, r.period, r.release_time_utc,
               r.previous, r.forecast, r.actual,
               e.name, e.tier, e.country, e.unit, e.category,
               s.raw_surprise, s.direction, s.impact, s.magnitude
        FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.id = ?
    """, (release_id,)).fetchone()


def _economic(direction, category):
    if category == "inflation":
        if direction == "UPSIDE": return "🔴 Hotter", "🟠 Hawkish"
        if direction == "DOWNSIDE": return "🟢 Cooler", "🟢 Dovish"
    if category == "labour":
        if direction == "UPSIDE": return "🟢 Stronger", "🟠 Hawkish"
        if direction == "DOWNSIDE": return "🔴 Weaker", "🟢 Dovish"
    if category == "growth":
        if direction == "UPSIDE": return "🟢 Stronger", "🟠 Hawkish"
        if direction == "DOWNSIDE": return "🔴 Weaker", "🟢 Dovish"
    return "＝ In line", "⚪ Neutral"


def _watch(higher_is):
    if higher_is == "BULLISH":
        return ("hotter → hawkish → USD↑ Yields↑ → Gold↓",
                "cooler → dovish → USD↓ Yields↓ → Gold↑")
    return ("hotter → USD↓ Yields↓ → Gold↑",
            "cooler → USD↑ Yields↑ → Gold↓")


def compose_pre(release_id):
    conn = connect()
    try:
        r = _load(conn, release_id)
        if not r: return None
        ev = BY_CODE.get(r["event_code"])
        if not ev: return None

        tier_e = {1: "🔴", 2: "🟠", 3: "🟡"}.get(r["tier"], "🟡")
        header = (f"<b>⏰ UPCOMING · {tier_e} T{r['tier']}</b>\n"
                  f"<b>{esc(r['name'])}</b> ({esc(r['period'])})")
        hot, cool = _watch(ev.higher_is)
        lines = ["", f"<b>Release:</b> <code>{esc(r['release_time_utc'] or '—')}</code>",
                 f"<b>Forecast:</b> <code>{_fmt(r['forecast'])}</code>{esc(ev.unit)}",
                 f"<b>Previous:</b> <code>{_fmt(r['previous'])}</code>{esc(ev.unit)}",
                 "", "<b>Watch:</b>", f"  • {esc(hot)}", f"  • {esc(cool)}", "",
                 "<b>Levels:</b>"]
        for sym in ("GOLD", "EURUSD"):
            try:
                lv = levels_mod.detect_levels(conn, sym)[:3]
            except Exception:
                lv = []
            if lv:
                bits = " · ".join(f"{l['center']:.4f} {l['kind'][:3]}" for l in lv)
                lines.append(f"  {sym}: {bits}")
        return header + "\n" + "\n".join(lines)
    finally:
        conn.close()


def compose_release(release_id, phase="T+5M"):
    conn = connect()
    try:
        r = _load(conn, release_id)
        if not r or r["actual"] is None: return None

        tier_e = {1: "🔴", 2: "🟠", 3: "🟡"}.get(r["tier"], "🟡")
        header = (f"<b>🚨 RELEASED · {tier_e} T{r['tier']}</b>\n"
                  f"<b>{esc(r['name'])}</b> ({esc(r['period'])})")

        dir_a = DIR_ARROW.get(r["direction"], "·")
        imp = IMPACT_EMOJI.get(r["impact"], "⚪")
        val, cb = _economic(r["direction"], r["category"])
        cbl = CB_BY_COUNTRY.get(r["country"], "Central Bank")

        lines = ["", f"<b>Actual:</b> <code>{_fmt(r['actual'])}</code>",
                 f"<b>Forecast:</b> <code>{_fmt(r['forecast'])}</code>",
                 f"<b>Surprise:</b> <code>{r['raw_surprise']:+.2f}</code> {dir_a}"
                 if r["raw_surprise"] is not None else "<b>Surprise:</b> —",
                 "", f"→ <b>Inflation/data:</b> {val}",
                 f"→ <b>{cbl}:</b> {cb}",
                 f"→ <b>Impact:</b> {imp} {esc(r['impact'] or '—')}", ""]

        mv = market_mod.run_for_release(conn, release_id, phase)
        if mv:
            v = mv["verdict"]
            lines.append(f"<b>Market ({esc(phase)}):</b>")
            for name, chg, unit in (("DXY", v.get("usd_change_pct"), "%"),
                                    ("US 2Y", v.get("yield_change_bp"), "bp"),
                                    ("Gold", v.get("gold_change_pct"), "%"),
                                    ("EURUSD", v.get("eurusd_change_pct"), "%")):
                if chg is not None:
                    lines.append(f"  <code>{name:<6}</code> {chg:+.3f}{unit}")
            lines.append(f"\nAlignment: {ALIGN_TONE.get(mv['alignment'],'⚪')} "
                         f"<b>{esc(mv['alignment'])}</b> ({esc(mv['notes'])})")
        return header + "\n" + "\n".join(lines)
    finally:
        conn.close()


def compose_final(release_id):
    conn = connect()
    try:
        r = _load(conn, release_id)
        if not r: return None

        tier_e = {1: "🔴", 2: "🟠", 3: "🟡"}.get(r["tier"], "🟡")
        header = (f"<b>🏆 FINAL · {tier_e} T{r['tier']}</b>\n"
                  f"<b>{esc(r['name'])}</b> ({esc(r['period'])})")

        try:
            g_now = chop_mod.evaluate_symbol(conn, "GOLD")
            e_now = chop_mod.evaluate_symbol(conn, "EURUSD")
            pref, pref_reason = chop_hist_mod.prefer_with_history(conn, g_now, e_now)
        except Exception:
            pref, pref_reason = "GOLD", "default"

        target = pref or "GOLD"

        from cne.core import lights as lights_mod
        from cne.core import macro as macro_mod
        score = macro_mod.compute_macro_score(conn, "US", 30)
        light = lights_mod.a_plus_light(conn, r["event_code"], r["period"],
                                        score, symbol=target)

        f = light.get("fundamental") or {}
        m = light.get("market")
        s = light.get("structure")

        lines = [""]
        lines.append(f"<b>FUNDAMENTAL</b> {f.get('light','⚪')} score {f.get('score',0):+.1f}")
        if m:
            lines.append(f"\n<b>MARKET</b> {ALIGN_TONE.get(m['alignment'],'⚪')} "
                         f"{esc(m['alignment'])} {m['score']:+.1f}")
            lines.append(f"  {esc(m['notes'])}")
        if s:
            tone = {"A_PLUS": "🟢", "CONFIRMS": "🟢", "NEUTRAL": "🟡",
                    "CONTRADICTS": "🔴"}.get(s["confirmation"], "⚪")
            lines.append(f"\n<b>STRUCTURE · {esc(target)}</b> {tone} "
                         f"{esc(s['event_class'])} {s['confirmation_score']:+.1f}")
            lines.append(f"  {esc(s['notes'])}")

        combined = light.get("combined_score", 0)
        lines += ["", f"<b>COMBINED: {combined:+.1f}</b>",
                  f"→ {light['light']} <b>{esc(light['label'])}</b>", "",
                  f"🎯 <b>Preferred: {esc(target)}</b>",
                  f"<i>{esc(pref_reason or '—')}</i>"]
        return header + "\n" + "\n".join(lines)
    finally:
        conn.close()