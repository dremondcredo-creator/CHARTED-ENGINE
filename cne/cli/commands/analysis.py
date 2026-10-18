"""Analysis commands."""
from __future__ import annotations
from cne.db.database import init_db
from cne.core import events as events_mod
from cne.core import macro as macro_mod
from cne.core import trend as trend_mod
from cne.core import labour_report as labour_mod
from cne.core import market as market_mod
from cne.core import chop as chop_mod
from cne.core import chop_history as chop_hist_mod
from cne.core import levels as levels_mod
from cne.core import structure as struct_mod
from cne.core import lights as lights_mod
from cne.report import terminal as term

WATCHED = ("US_CPI_YOY", "US_CORE_CPI_MOM", "US_PPI_YOY",
           "US_NFP", "US_UNEMPLOYMENT", "US_AHE_MOM", "US_RETAIL_MOM")


def _latest(conn):
    return conn.execute("""
        SELECT r.event_code, r.period FROM releases r
        JOIN surprises s ON s.release_id = r.id
        JOIN event_definitions e ON e.code = r.event_code
        WHERE e.tier = 1 AND e.country = 'US'
        ORDER BY r.release_time_utc DESC NULLS LAST, r.period DESC LIMIT 1
    """).fetchone()


def cmd_macro(_args):
    conn = init_db(); events_mod.seed(conn)
    s = macro_mod.compute_macro_score(conn, "US", 30)
    macro_mod.persist_snapshot(conn, s)
    print(term.render_macro_score(s))


def cmd_trend(_args):
    conn = init_db(); events_mod.seed(conn)
    for code in WATCHED:
        hist = trend_mod.event_history(conn, code, 12)
        if hist:
            t = trend_mod.detect_trend(hist)
            trend_mod.persist_trend(conn, code, t)
    print(term.render_data_stack(conn, WATCHED))


def cmd_labour(_args):
    conn = init_db(); events_mod.seed(conn)
    rep = labour_mod.build_labour_report(conn)
    if rep:
        labour_mod.persist(conn, rep)
        print(term.render_labour_report(rep))


def cmd_market(_args):
    conn = init_db(); events_mod.seed(conn)
    print(f"✅ market verdicts for {market_mod.run_all(conn)} releases")


def cmd_structure(args):
    conn = init_db(); events_mod.seed(conn)
    sym = (args[0] if args else "GOLD").upper()
    print(f"✅ {struct_mod.classify_all(conn, symbol=sym)} releases for {sym}")


def cmd_levels(args):
    conn = init_db(); events_mod.seed(conn)
    sym = (args[0] if args else "GOLD").upper()
    print(term.render_levels(levels_mod.detect_levels(conn, sym), sym))


def cmd_chop(_args):
    conn = init_db()
    g = chop_mod.evaluate_symbol(conn, "GOLD")
    e = chop_mod.evaluate_symbol(conn, "EURUSD")
    p, r = chop_mod.prefer_instrument(conn, g, e)
    print(term.render_chop(g, e, p, r))


def cmd_chop_history(_args):
    conn = init_db()
    for s in ("GOLD", "EURUSD"):
        chop_hist_mod.snapshot(conn, s)
    g = chop_hist_mod.trend(conn, "GOLD"); g["symbol"] = "GOLD"
    e = chop_hist_mod.trend(conn, "EURUSD"); e["symbol"] = "EURUSD"
    print(term.render_chop_trend(g, e))


def cmd_light(_args):
    conn = init_db(); events_mod.seed(conn)
    row = _latest(conn)
    if not row: print("No Tier-1 release yet."); return
    s = macro_mod.compute_macro_score(conn, "US", 30)
    print(term.render_full_light(lights_mod.full_light(conn, row["event_code"], row["period"], s)))


def cmd_a_plus(_args):
    conn = init_db(); events_mod.seed(conn)
    row = _latest(conn)
    if not row: print("No Tier-1 release yet."); return
    s = macro_mod.compute_macro_score(conn, "US", 30)
    light = lights_mod.a_plus_light(conn, row["event_code"], row["period"], s, "GOLD")
    print(term.render_a_plus(light))


def cmd_v3(_args):
    conn = init_db(); events_mod.seed(conn)
    s = macro_mod.compute_macro_score(conn, "US", 30)
    macro_mod.persist_snapshot(conn, s)
    market_mod.run_all(conn)
    print(term.render_v3_dashboard(conn, s))


def cmd_v4(_args):
    conn = init_db(); events_mod.seed(conn)
    s = macro_mod.compute_macro_score(conn, "US", 30)
    macro_mod.persist_snapshot(conn, s)
    print(term.render_macro_score(s))
    for sym in ("GOLD", "EURUSD"):
        print(term.render_levels(levels_mod.detect_levels(conn, sym), sym))
    market_mod.run_all(conn)
    for sym in ("GOLD", "EURUSD"):
        struct_mod.classify_all(conn, symbol=sym)
    row = _latest(conn)
    if row:
        for sym in ("GOLD", "EURUSD"):
            srow = conn.execute("""
                SELECT * FROM structure_verdicts WHERE release_id = (
                    SELECT id FROM releases WHERE event_code=? AND period=?
                ) AND symbol=?
            """, (row["event_code"], row["period"], sym)).fetchone()
            if srow:
                print(term.render_structure(dict(srow)))
    for sym in ("GOLD", "EURUSD"):
        chop_hist_mod.snapshot(conn, sym)
    g = chop_hist_mod.trend(conn, "GOLD"); g["symbol"] = "GOLD"
    e = chop_hist_mod.trend(conn, "EURUSD"); e["symbol"] = "EURUSD"
    print(term.render_chop_trend(g, e))
    g_now = chop_mod.evaluate_symbol(conn, "GOLD")
    e_now = chop_mod.evaluate_symbol(conn, "EURUSD")
    pref, reason = chop_hist_mod.prefer_with_history(conn, g_now, e_now)
    print(f"\n🎯 PREFERRED INSTRUMENT: {pref or 'TIE'}  ({reason})\n")
    if row:
        target = "GOLD" if (pref or "GOLD") == "GOLD" else "EURUSD"
        light = lights_mod.a_plus_light(conn, row["event_code"], row["period"], s, target)
        print(term.render_a_plus(light))