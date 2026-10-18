"""Learning engine commands."""
from cne.db.database import init_db
from cne.core import events as events_mod
from cne.learning import outcomes, features, aggregates, lookup, report


def cmd_learn_refresh(_args):
    conn = init_db(); events_mod.seed(conn)
    print(f"→ {outcomes.refresh_all(conn)} outcomes")
    print(f"→ {features.refresh_all(conn)} features")
    print(f"→ {aggregates.refresh(conn)} edges")


def cmd_learn_reset(_args):
    conn = init_db()
    for t in ("outcome_labels", "setup_features", "edge_stats"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    print("✅ cleared")


def cmd_edges(args):
    conn = init_db()
    sym = (args[0] if args else "GOLD").upper()
    lvl = (args[1] if len(args) > 1 else "L2").upper()
    rows = lookup.top_edges(conn, symbol=sym, level=lvl, min_n=15, limit=20)
    print(report.table(rows, symbol=sym, level=lvl))


def cmd_edge_for(args):
    if not args: print("usage: cne edge-for <release_id> [SYM]"); return
    conn = init_db()
    rid = int(args[0]); sym = (args[1] if len(args) > 1 else "GOLD").upper()
    print(report.block(lookup.historical_edge_for_release(conn, rid, sym)))