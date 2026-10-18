"""Telegram alert commands."""
import re
from cne.db.database import init_db
from cne.alerts import dispatcher as ad, composer as ac, telegram as tg


def cmd_test(_args):
    if not tg.is_configured():
        print("❌ Telegram not configured"); return
    print(ad.dispatch_test())


def cmd_preview(args):
    if not args: print("usage: cne alert-preview <release_id>"); return
    rid = int(args[0])
    for kind, fn in (("PRE", ac.compose_pre), ("RELEASE", ac.compose_release), ("FINAL", ac.compose_final)):
        print(f"\n═════ {kind} ═════")
        t = fn(rid)
        print(re.sub(r"<[^>]+>", "", t) if t else "(no message)")


def cmd_send(args):
    if len(args) < 2: print("usage: cne alert-send <id> <pre|release|final>"); return
    rid = int(args[0]); kind = args[1].lower()
    fns = {"pre": ad.dispatch_pre, "release": ad.dispatch_release, "final": ad.dispatch_final}
    print(fns[kind](rid) if kind in fns else f"unknown {kind}")


def cmd_log(args):
    n = int(args[0]) if args else 20
    conn = init_db()
    for r in conn.execute("SELECT * FROM alerts_log ORDER BY ts_utc DESC LIMIT ?", (n,)).fetchall():
        print(f"  {r['ts_utc'][:19]}  rel={r['release_id']}  {r['kind']:<9} {r['status']}")


def cmd_reset(args):
    if not args: print("usage: cne alerts-reset <id> [kind]"); return
    conn = init_db()
    rid = int(args[0])
    if len(args) > 1:
        conn.execute("DELETE FROM alerts_log WHERE release_id=? AND kind=?", (rid, args[1].upper()))
    else:
        conn.execute("DELETE FROM alerts_log WHERE release_id=?", (rid,))
    conn.commit()
    print(f"✅ cleared release {rid}")