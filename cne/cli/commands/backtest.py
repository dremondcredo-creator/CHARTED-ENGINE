"""Backtesting commands."""
from datetime import datetime, timedelta, timezone
from cne.db.database import init_db
from cne.backtest import rules as bt_rules, engine as bt_engine, report as bt_report


def cmd_seed(_args):
    conn = init_db()
    bt_rules.seed_default_rules(conn)
    for r in bt_rules.list_rules(conn):
        print(f"  · {r['name']}")


def cmd_run(args):
    if not args: print("usage: cne backtest-run <rule|all> [SYM] [days]"); return
    rule = args[0]
    sym = (args[1] if len(args) > 1 else "GOLD").upper()
    days = int(args[2]) if len(args) > 2 else 365
    conn = init_db(); bt_rules.seed_default_rules(conn)
    end = datetime.now(timezone.utc).isoformat()
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if rule == "all":
        print(bt_report.table(bt_engine.run_all_default(conn, sym, days)))
    else:
        print(bt_report.summary(bt_engine.run(conn, rule, sym, start, end)))


def cmd_list(_args):
    conn = init_db()
    for r in conn.execute("SELECT * FROM backtest_runs ORDER BY computed_utc DESC LIMIT 20").fetchall():
        print(f"  #{r['id']} {r['rules_name']:<28} {r['symbol']:<8} n={r['n_trades']} win={r['win_rate']*100:.1f}%")


def cmd_show(args):
    if not args: print("usage: cne backtest-show <run_id>"); return
    conn = init_db()
    r = conn.execute("SELECT * FROM backtest_runs WHERE id=?", (int(args[0]),)).fetchone()
    if r: print(bt_report.summary(dict(r)))