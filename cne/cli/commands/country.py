"""Multi-country commands."""
from cne.db.database import init_db
from cne.core import events as events_mod
from cne.core import multi_country as mc_mod
from cne.core import pairs as pairs_mod
from cne.learning import regime as regime_mod
from cne.report import terminal as term


def cmd_countries(_args):
    conn = init_db(); events_mod.seed(conn)
    print(term.render_country_grid(mc_mod.all_biases(conn)))


def cmd_pairs(_args):
    conn = init_db(); events_mod.seed(conn)
    print(term.render_pair_directions(pairs_mod.all_pair_directions(conn)))


def cmd_pair_detail(args):
    if not args:
        print("usage: cne pair <EURUSD|GBPUSD|...>"); return
    conn = init_db(); events_mod.seed(conn)
    p = pairs_mod.pair_direction(conn, args[0].upper())
    if not p: print(f"unknown {args[0]}"); return
    print(f"\n🎯 {p['symbol']}")
    print(f"   base: {p['base_country']} ({p['base_total']:+.2f})")
    if p.get("quote_total") is not None:
        print(f"   quote: {p['quote_country']} ({p['quote_total']:+.2f})")
    print(f"   → {p['direction']} — {p['notes']}\n")


def cmd_regime_refresh(_args):
    conn = init_db()
    for c in ("US", "EU", "CA"):
        print(f"  {c}: {regime_mod.refresh(conn, c)}")


def cmd_regime_now(_args):
    conn = init_db()
    for c in ("US", "EU", "CA"):
        r = regime_mod.current_regime(conn, c)
        if r: print(f"  {c}: {r['label']}")


def cmd_regime_history(_args):
    conn = init_db()
    rows = conn.execute("SELECT * FROM macro_regimes WHERE country='US' ORDER BY regime_id").fetchall()
    for r in rows:
        print(f"  #{r['regime_id']}  {r['label']:<14} {r['start_ts_utc'][:19]} → {(r['end_ts_utc'] or 'open')[:19]}")