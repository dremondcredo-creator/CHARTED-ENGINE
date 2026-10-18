"""All terminal renderers."""
from __future__ import annotations

DIR_ARROW = {"RISING": "▲", "FALLING": "▼", "FLAT": "＝",
             "VOLATILE": "⇅", "INSUFFICIENT": "·", "UP": "▲", "DOWN": "▼"}
IMPACT_EMOJI = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}

LABEL_TONE = {
    "STRONG HAWKISH": "🔴", "HAWKISH": "🟠", "MILD HAWKISH": "🟡",
    "NEUTRAL": "⚪", "MILD DOVISH": "🟡", "DOVISH": "🟠",
    "STRONG DOVISH": "🟢",
}
CHOP_TONE = {"CLEAN": "🟢", "MIXED": "🟡", "CHOPPY": "🔴", "UNKNOWN": "⚪"}


def _fmt(v, digits=None):
    if v is None: return "—"
    if digits is not None: return f"{v:.{digits}f}"
    if abs(v) >= 1000: return f"{v:,.0f}"
    if abs(v) >= 100: return f"{v:.1f}"
    if abs(v) >= 1: return f"{v:.2f}"
    return f"{v:.3f}".rstrip("0").rstrip(".")


def render_status(conn):
    tables = ("event_definitions", "releases", "surprises", "macro_snapshots",
              "market_verdicts", "structure_verdicts", "price_snapshots",
              "edge_stats", "outcome_labels", "backtest_runs",
              "trade_simulations", "alerts_log")
    lines = ["", "📊 DATABASE STATUS", ""]
    for t in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            lines.append(f"  {t:<24} {n:>8}")
        except Exception:
            lines.append(f"  {t:<24} {'—':>8}")
    lines.append("")
    return "\n".join(lines)


def render_macro_score(score):
    tone = LABEL_TONE.get(score["label"], "⚪")
    lines = ["", "═" * 60,
             f"  🇺🇸 {score['country']} MACRO BIAS — {tone} {score['label']}",
             f"     score {score['total']:+.2f} · {score['n_releases']} releases · {score['window_days']}d",
             f"     inflation {score['inflation']:+.2f} · labour {score['labour']:+.2f} · growth {score['growth']:+.2f}",
             "═" * 60, ""]
    return "\n".join(lines)


def render_data_stack(conn, codes):
    from cne.core.trend import event_history, detect_trend
    from cne.core.events import BY_CODE
    lines = ["", "📊 DATA STACK", ""]
    for code in codes:
        hist = event_history(conn, code, 6)
        if not hist: continue
        ev = BY_CODE.get(code)
        tail = " → ".join(_fmt(r["actual"]) for r in hist)
        trend = detect_trend(hist)
        arrow = DIR_ARROW.get(trend["direction"], "·")
        streak = ""
        if trend["streak"] and trend["streak_dir"]:
            sign = "+" if trend["streak_dir"] == "UPSIDE" else "-"
            streak = f" · {sign}{trend['streak']} streak"
        lines.append(f"  {ev.name if ev else code:<28} {tail}   {arrow} {trend['direction']}{streak}")
    lines.append("")
    return "\n".join(lines)


def render_labour_report(rep):
    if not rep: return "\n👷 No labour report.\n"
    lines = ["", f"👷 US LABOUR REPORT ({rep['period']})", ""]
    from cne.core.events import BY_CODE
    for code, c in rep["components"].items():
        if not c: continue
        ev = BY_CODE.get(code)
        arrow = DIR_ARROW.get(c["direction"], "·")
        em = IMPACT_EMOJI.get(c["impact"], "⚪")
        lines.append(f"  {(ev.name if ev else code):<28} "
                     f"{_fmt(c['forecast'])} → {_fmt(c['actual'])}  "
                     f"{arrow} {c['raw_surprise']:+.2f}  {em} {c['impact']}")
    colour = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(rep["color"], "⚪")
    lines += ["", f"  → {colour} {rep['verdict']} · {rep['implication']}", ""]
    return "\n".join(lines)


def render_levels(levels, symbol):
    if not levels: return f"\n📐 {symbol} — none\n"
    lines = ["", f"📐 {symbol} KEY LEVELS", ""]
    for l in levels[:6]:
        lines.append(f"  {l['center']:>12.4f}  {l['kind'][:4]:<4}  "
                     f"{l['touches']}×  {l['dist_atr']:>5.2f} ATR  "
                     f"str {l['strength']:.2f}")
    lines.append("")
    return "\n".join(lines)


def render_structure(s):
    if not s: return "\n🏗️  no structure data\n"
    lines = ["", f"🏗️  STRUCTURE — {s.get('symbol','?')}", "",
             f"  nearest: {s.get('nearest_level')} ({s.get('nearest_kind')}, {s.get('distance_atr')} ATR)",
             f"  before → after: {s.get('structure_before')} → {s.get('structure_after')}",
             f"  event class: {s.get('event_class')} ({_fmt(s.get('break_pct'), 2)}%)"]
    if s.get("held_retest") is not None:
        lines.append(f"  retest held: {'YES' if s['held_retest'] else 'NO'}")
    lines.append(f"  confirmation: {s.get('confirmation')} ({s.get('confirmation_score'):+.1f})")
    lines.append("")
    return "\n".join(lines)


def render_chop(g, e, preferred, reason):
    lines = ["", "🔍 CHOP", ""]
    for r in (g, e):
        if r:
            lines.append(f"  {r['symbol']:<8} chop {r['chop_score']:>5.1f} [{r['label']:<7}]")
    lines.append(f"  → {preferred or 'TIE'} ({reason})")
    lines.append("")
    return "\n".join(lines)


def render_chop_trend(g, e):
    lines = ["", "🔍 CHOP 3-DAY", ""]
    for h in (g, e):
        if h and h.get("n_snapshots", 0):
            lines.append(f"  {h['symbol']:<8} mean {h.get('mean_chop','—')} · "
                         f"{h.get('choppy_pct','—')}% choppy · {h['trend_label']}")
    lines.append("")
    return "\n".join(lines)


def render_full_light(light):
    lines = ["", "🚦 COHERENCE LIGHT", ""]
    f = light.get("fundamental") or {}
    lines.append(f"  Fundamental: {f.get('light','?')} {f.get('label','—')}")
    m = light.get("market")
    if m:
        lines.append(f"  Market:      {m['alignment']} ({m['score']:+.1f}) — {m['notes']}")
    if "combined_score" in light:
        lines.append(f"  Combined: {light['combined_score']:+.1f}")
    lines += ["", f"  → {light['light']} {light['label']}", ""]
    return "\n".join(lines)


def render_a_plus(light):
    lines = ["", "═" * 60, "🏆 A+ CONFIRMATION", "═" * 60, ""]
    f = light.get("fundamental") or {}
    m = light.get("market")
    s = light.get("structure")
    lines.append(f"  FUNDAMENTAL   {f.get('light','?')} score {f.get('score',0):+.1f}")
    if m: lines.append(f"  MARKET        {m['alignment']} {m['score']:+.1f}  ({m['notes']})")
    if s: lines.append(f"  STRUCTURE     {s.get('event_class','—')} {s.get('confirmation_score',0):+.1f}  ({s.get('notes','')})")
    lines.append("")
    lines.append(f"  COMBINED: {light.get('combined_score', 0):+.1f}")
    lines.append("")
    lines.append(f"  → {light['light']} {light['label']}")
    lines.append("═" * 60)
    lines.append("")
    return "\n".join(lines)


def render_v3_dashboard(conn, macro):
    from cne.core.lights import full_light
    from cne.core.chop import evaluate_symbol, prefer_instrument
    row = conn.execute("""
        SELECT r.event_code, r.period FROM releases r
        JOIN surprises s ON s.release_id = r.id
        JOIN event_definitions e ON e.code = r.event_code
        WHERE e.tier = 1 AND e.country = 'US'
        ORDER BY r.release_time_utc DESC NULLS LAST, r.period DESC LIMIT 1
    """).fetchone()
    if row:
        light = full_light(conn, row["event_code"], row["period"], macro)
        return render_full_light(light)
    return "\nNo Tier-1 releases yet.\n"


def render_country_grid(biases):
    lines = ["", "🌍 COUNTRY MACRO BIAS", "",
             f"  {'ctry':<6} {'label':<16} {'score':>7}",
             "  " + "─" * 40]
    for c, s in biases.items():
        tone = LABEL_TONE.get(s["label"], "⚪")
        lines.append(f"  {c:<6} {tone} {s['label']:<14} {s['total']:>+7.2f}")
    lines.append("")
    return "\n".join(lines)


def render_pair_directions(pairs):
    lines = ["", "🎯 PAIRS", "",
             f"  {'sym':<8} {'dir':<10} {'base':>7} {'quote':>7} {'diff':>7}"]
    for sym, p in pairs.items():
        if not p: continue
        diff = p.get("diff")
        diff_s = f"{diff:+.2f}" if diff is not None else "—"
        q = f"{p['quote_total']:+.2f}" if p.get("quote_total") is not None else "—"
        arrow = {"UP": "▲", "DOWN": "▼", "NEUTRAL": "＝",
                 "BULLISH": "▲", "BEARISH": "▼"}.get(p["direction"], "·")
        lines.append(f"  {sym:<8} {arrow} {p['direction']:<8} "
                     f"{p['base_total']:>+7.2f} {q:>7} {diff_s:>7}")
    lines.append("")
    return "\n".join(lines)