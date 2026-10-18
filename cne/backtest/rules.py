"""Backtest rule definitions."""
from __future__ import annotations

import json
from datetime import datetime, timezone

DEFAULT_RULES = [
    {"name": "cpi_dovish_clean",
     "description": "CPI downside, market confirms, structure breaks up",
     "entry_filter": {"event_code": "US_CPI_YOY", "surprise_direction": "DOWNSIDE",
                      "market_alignment": "CONFIRMS", "structure_event": "BREAK_UP",
                      "held_retest": 1},
     "entry_offset_min": 30, "exit_horizon_min": 120,
     "stop_pct": 0.30, "target_pct": 0.80,
     "spread_cost_pct": 0.03, "slippage_pct": 0.03},
    {"name": "nfp_hot_reversal",
     "description": "Hot NFP but market fades",
     "entry_filter": {"event_code": "US_NFP", "surprise_direction": "UPSIDE",
                      "market_alignment": "CONTRADICTS", "structure_event": "FADE_UP"},
     "entry_offset_min": 30, "exit_horizon_min": 120,
     "stop_pct": 0.40, "target_pct": 0.60,
     "spread_cost_pct": 0.03, "slippage_pct": 0.03},
    {"name": "any_a_plus_hold_2h",
     "description": "Any A+ alignment — fixed 2h hold",
     "entry_filter": {"structure_confirmation": "A_PLUS"},
     "entry_offset_min": 30, "exit_horizon_min": 120,
     "stop_pct": None, "target_pct": None,
     "spread_cost_pct": 0.03, "slippage_pct": 0.03},
    {"name": "cpi_hot_short",
     "description": "Hot CPI, market confirms",
     "entry_filter": {"event_code": "US_CPI_YOY", "surprise_direction": "UPSIDE",
                      "market_alignment": "CONFIRMS"},
     "entry_offset_min": 30, "exit_horizon_min": 240,
     "stop_pct": 0.35, "target_pct": 0.70,
     "spread_cost_pct": 0.03, "slippage_pct": 0.03},
]


def seed_default_rules(conn):
    for spec in DEFAULT_RULES:
        conn.execute("""
            INSERT INTO backtest_rules
              (name, description, entry_filter, entry_offset_min,
               exit_horizon_min, stop_pct, target_pct, max_hold_min,
               spread_cost_pct, slippage_pct, stake_fraction, created_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
              description=excluded.description,
              entry_filter=excluded.entry_filter,
              entry_offset_min=excluded.entry_offset_min,
              exit_horizon_min=excluded.exit_horizon_min,
              stop_pct=excluded.stop_pct, target_pct=excluded.target_pct,
              spread_cost_pct=excluded.spread_cost_pct,
              slippage_pct=excluded.slippage_pct
        """, (spec["name"], spec["description"], json.dumps(spec["entry_filter"]),
              spec["entry_offset_min"], spec["exit_horizon_min"],
              spec.get("stop_pct"), spec.get("target_pct"), None,
              spec["spread_cost_pct"], spec["slippage_pct"], 1.0,
              datetime.now(timezone.utc).isoformat()))
    conn.commit()


def get_rules(conn, name):
    r = conn.execute("SELECT * FROM backtest_rules WHERE name=?", (name,)).fetchone()
    if not r: return None
    d = dict(r)
    d["entry_filter"] = json.loads(d["entry_filter"])
    return d


def list_rules(conn):
    return [dict(r) for r in conn.execute("""
        SELECT id, name, description FROM backtest_rules ORDER BY name
    """).fetchall()]