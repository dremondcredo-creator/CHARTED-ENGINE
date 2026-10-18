"""Sizing + simulator commands."""
from cne.db.database import init_db
from cne.core import sizing as sz_mod
from cne.core import simulator as sim_mod
from cne.report import sim_report


def _ask(prompt, default=None, cast=float):
    s = input(f"{prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
    if not s and default is not None: return default
    try: return cast(s)
    except ValueError: return default


def cmd_size_config(args):
    conn = init_db(); sz_mod._ensure_config(conn)
    if args:
        kw = {}
        for a in args:
            if "=" in a:
                k, v = a.split("=", 1)
                try: kw[k] = float(v)
                except ValueError: kw[k] = v
        sz_mod.update_config(conn, **kw)
    cfg = conn.execute("SELECT * FROM sizing_config WHERE id=1").fetchone()
    for k in cfg.keys(): print(f"  {k:<24} {cfg[k]}")


def cmd_size(args):
    if not args: print("usage: cne size <release_id> [SYM]"); return
    conn = init_db()
    rid = int(args[0]); sym = (args[1] if len(args) > 1 else "GOLD").upper()
    r = sz_mod.size_for_release(conn, rid, sym)
    if not r.get("sized"):
        print(f"⚪ NOT SIZED — {r.get('reason')}"); return
    print(f"\n💰 release {rid} · {sym}")
    print(f"  Edge: {r['edge_level']} (n={r['n_samples']}) · win {r['win_rate']*100:.1f}%")
    print(f"  Risk: {r['risk_pct']:.2f}%  =  {r['risk_amount']:.2f}")


def cmd_simulate(args):
    conn = init_db()
    save = "--save" in args; args = [a for a in args if a != "--save"]
    rid = None
    if "--release" in args:
        i = args.index("--release"); rid = int(args[i+1]); args = args[:i] + args[i+2:]
    direction = None
    if "--long" in args: direction = "LONG"; args.remove("--long")
    if "--short" in args: direction = "SHORT"; args.remove("--short")

    if len(args) >= 5:
        sym = args[0].upper()
        bal = float(args[1]); risk = float(args[2])
        entry = float(args[3]); stop = float(args[4])
        target = float(args[5]) if len(args) >= 6 else None
    else:
        sym = input("Symbol: ").strip().upper()
        bal = _ask("Balance", 10000.0); risk = _ask("Risk %", 1.0)
        entry = _ask("Entry"); stop = _ask("Stop")
        t = input("Target [blank=none]: ").strip()
        target = float(t) if t else None

    result = sim_mod.calculate(sym, bal, risk, entry, stop, target, direction=direction)
    hint = sim_mod.suggest_from_kelly(conn, rid, sym, bal) if rid else None
    print(sim_report.render(result, release_hint=hint))
    if save and not result.get("error"):
        print(f"  ✅ saved #{sim_mod.save(conn, result, release_id=rid)}\n")


def cmd_sim_history(args):
    conn = init_db()
    sym = args[0].upper() if args and args[0].upper() in sim_mod.INSTRUMENTS else None
    limit = int(args[-1]) if args and args[-1].isdigit() else 30
    print(sim_report.render_history(sim_mod.history(conn, sym, limit)))


def cmd_sim_specs(_args):
    print(f"\n{'symbol':<10}{'contract':>12}{'pip':>10}{'pip val':>12}{'quote':>8}")
    for s, spec in sorted(sim_mod.INSTRUMENTS.items()):
        print(f"{s:<10}{spec['contract_size']:>12,.0f}{spec['pip_size']:>10} "
              f"{spec['pip_value_quote']:>12,.2f}{spec['quote_ccy']:>8}")