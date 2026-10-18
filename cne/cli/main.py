"""Unified CLI dispatcher."""
from __future__ import annotations

import sys
import traceback

from cne import __version__
from cne.cli.commands import (
    alerts as cmd_alerts,
)
from cne.cli.commands import (
    analysis as cmd_analysis,
)
from cne.cli.commands import (
    backtest as cmd_backtest,
)
from cne.cli.commands import (
    country as cmd_country,
)
from cne.cli.commands import (
    data as cmd_data,
)
from cne.cli.commands import (
    learning as cmd_learning,
)
from cne.cli.commands import (
    sizing as cmd_sizing,
)
from cne.cli.commands import (
    system as cmd_system,
)
from cne.config import settings
from cne.logging_setup import configure, get_logger

GROUPS = {
    "Data pipeline": [
        ("init",      cmd_data.cmd_init,      "create / migrate database"),
        ("collect",   cmd_data.cmd_collect,   "gather calendar, statistics, markets + bank news"),
        ("classify",  cmd_data.cmd_classify,  "recompute surprises"),
        ("snapshots", cmd_data.cmd_snapshots, "backfill | pre | post"),
        ("status",    cmd_data.cmd_status,    "DB row counts"),
    ],
    "Analysis": [
        ("macro",        cmd_analysis.cmd_macro,        "US macro bias score"),
        ("trend",        cmd_analysis.cmd_trend,        "per-event trend snapshots"),
        ("labour",       cmd_analysis.cmd_labour,       "combined labour report"),
        ("market",       cmd_analysis.cmd_market,       "market verdict"),
        ("structure",    cmd_analysis.cmd_structure,    "levels + break/fade"),
        ("levels",       cmd_analysis.cmd_levels,       "detect key levels"),
        ("chop",         cmd_analysis.cmd_chop,         "instant chop score"),
        ("chop-history", cmd_analysis.cmd_chop_history, "chop persistence"),
        ("light",        cmd_analysis.cmd_light,        "three-light"),
        ("a+",           cmd_analysis.cmd_a_plus,       "A+ confirmation"),
        ("v3",           cmd_analysis.cmd_v3,           "V3 dashboard"),
        ("v4",           cmd_analysis.cmd_v4,           "V4 dashboard"),
    ],
    "Multi-country": [
        ("countries",      cmd_country.cmd_countries,      "macro bias per country"),
        ("pairs",          cmd_country.cmd_pairs,          "pair directions"),
        ("pair",           cmd_country.cmd_pair_detail,    "single-pair detail"),
        ("regime-refresh", cmd_country.cmd_regime_refresh, "rebuild regimes"),
        ("regime-now",     cmd_country.cmd_regime_now,     "current regime"),
        ("regime-history", cmd_country.cmd_regime_history, "regime timeline"),
    ],
    "Learning": [
        ("learn-refresh", cmd_learning.cmd_learn_refresh, "rebuild learning tables"),
        ("learn-reset",   cmd_learning.cmd_learn_reset,   "wipe learning tables"),
        ("edges",         cmd_learning.cmd_edges,         "top edges"),
        ("edge-for",      cmd_learning.cmd_edge_for,      "edge for one release"),
    ],
    "Backtesting": [
        ("backtest-seed", cmd_backtest.cmd_seed, "seed default rules"),
        ("backtest-run",  cmd_backtest.cmd_run,  "run backtest"),
        ("backtest-list", cmd_backtest.cmd_list, "list runs"),
        ("backtest-show", cmd_backtest.cmd_show, "details"),
    ],
    "Sizing": [
        ("size-config", cmd_sizing.cmd_size_config, "view/update sizing config"),
        ("size",        cmd_sizing.cmd_size,        "Kelly size"),
        ("simulate",    cmd_sizing.cmd_simulate,    "pre-trade calculator"),
        ("sim-history", cmd_sizing.cmd_sim_history, "simulated trades"),
        ("sim-specs",   cmd_sizing.cmd_sim_specs,   "instrument specs"),
    ],
    "Alerts": [
        ("alert-test",    cmd_alerts.cmd_test,    "test Telegram"),
        ("alert-preview", cmd_alerts.cmd_preview, "print alerts"),
        ("alert-send",    cmd_alerts.cmd_send,    "send one alert"),
        ("alerts-log",    cmd_alerts.cmd_log,     "recent alerts"),
        ("alerts-reset",  cmd_alerts.cmd_reset,   "clear dedup"),
    ],
    "System": [
        ("ui",       cmd_system.cmd_ui,       "launch dashboard"),
        ("dashboard", cmd_system.cmd_dashboard, "React dashboard HTTP + SSE API"),
        ("schedule", cmd_system.cmd_schedule, "start scheduler"),
        ("doctor",   cmd_system.cmd_doctor,   "health check"),
        ("version",  cmd_system.cmd_version,  "version"),
    ],
}

COMMANDS = {}
for _g, _items in GROUPS.items():
    for _name, _fn, _desc in _items:
        COMMANDS[_name] = (_fn, _g)


def _print_help():
    print(f"\n🚨 CHARTED NEWS ENGINE  v{__version__}\n")
    print("usage:  cne <command> [args...]\n")
    for group, items in GROUPS.items():
        print(f"  ── {group} ──")
        for name, _fn, desc in items:
            print(f"    {name:<18} {desc}")
        print()
    print("  help                show this message")
    print("  all                 run: init → collect → classify → report\n")


def _run(command: str, args: list[str]) -> int:
    if command in ("help", "-h", "--help", ""):
        _print_help()
        return 0
    if command == "all":
        for c in ("init", "collect", "classify"):
            if _run(c, []) != 0:
                return 1
        return 0
    entry = COMMANDS.get(command)
    if entry is None:
        print(f"unknown command: {command!r}  (try `cne help`)")
        return 2
    fn, _ = entry
    try:
        fn(args)
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    except Exception:
        print(f"\n❌ command {command!r} failed:\n")
        traceback.print_exc()
        return 1


def main(argv: list[str] | None = None) -> int:
    configure()
    get_logger(__name__).debug("settings: %s", settings)
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        _print_help()
        return 0
    return _run(argv[0], argv[1:])


if __name__ == "__main__":
    sys.exit(main())