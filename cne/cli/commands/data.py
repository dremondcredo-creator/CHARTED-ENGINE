"""Data pipeline commands."""
from __future__ import annotations

from cne.collectors import pipeline as collection_pipeline
from cne.core import events as events_mod
from cne.core import surprise as surprise_mod
from cne.db.database import init_db
from cne.report import terminal as term


def cmd_init(_args):
    conn = init_db()
    events_mod.seed(conn)
    print(f"✅ DB initialised · {len(events_mod.EVENTS)} event definitions")


def cmd_collect(_args):
    conn = init_db()
    try:
        events_mod.seed(conn)
        failures = []
        for result in collection_pipeline.collect_all(conn):
            print(
                f"{result['source']}: {result['status']} "
                f"({result['records']} records)"
            )
            if result["error"]:
                print(f"   {result['error']}")
            if result["status"] in ("ERROR", "PARTIAL"):
                failures.append(result["source"])
        if failures:
            raise SystemExit(f"Collection failed for: {', '.join(failures)}")
    finally:
        conn.close()


def cmd_classify(_args):
    conn = init_db()
    print(f"✅ {surprise_mod.recompute_all(conn)} releases classified")


def cmd_snapshots(args):
    from cne.collectors import snapshots as snap_mod
    conn = init_db()
    sub = args[0] if args else "backfill"
    if sub == "backfill":
        print(f"✅ recorded {snap_mod.backfill_all(conn)} release price snapshots")
    else:
        print("usage: cne snapshots [backfill|pre|post]")


def cmd_status(_args):
    conn = init_db()
    print(term.render_status(conn))