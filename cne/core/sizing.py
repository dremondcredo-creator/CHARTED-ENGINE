"""Kelly position sizing."""
from __future__ import annotations

from datetime import datetime, timezone
from cne.learning import lookup as learn_lookup

DEFAULT_CONFIG = {
    "account_size": 10000.0,
    "kelly_fraction": 0.5,
    "max_risk_pct": 2.0,
    "min_risk_pct": 0.10,
    "confidence_penalty_low": 0.5,
    "confidence_penalty_med": 0.8,
    "confidence_penalty_high": 1.0,
}


def _ensure_config(conn):
    row = conn.execute("SELECT * FROM sizing_config WHERE id=1").fetchone()
    if row: return dict(row)
    conn.execute("""
        INSERT INTO sizing_config
          (id, account_size, kelly_fraction, max_risk_pct, min_risk_pct,
           confidence_penalty_low, confidence_penalty_med,
           confidence_penalty_high, updated_utc)
        VALUES (1,?,?,?,?,?,?,?,?)
    """, (DEFAULT_CONFIG["account_size"], DEFAULT_CONFIG["kelly_fraction"],
          DEFAULT_CONFIG["max_risk_pct"], DEFAULT_CONFIG["min_risk_pct"],
          DEFAULT_CONFIG["confidence_penalty_low"],
          DEFAULT_CONFIG["confidence_penalty_med"],
          DEFAULT_CONFIG["confidence_penalty_high"],
          datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM sizing_config WHERE id=1").fetchone())


def update_config(conn, **kwargs):
    _ensure_config(conn)
    fields, values = [], []
    for k, v in kwargs.items():
        if k in DEFAULT_CONFIG:
            fields.append(f"{k}=?")
            values.append(v)
    if not fields: return
    fields.append("updated_utc=?")
    values.append(datetime.now(timezone.utc).isoformat())
    values.append(1)
    conn.execute(f"UPDATE sizing_config SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()


def _conf_mult(conf, cfg):
    return {"HIGH": cfg["confidence_penalty_high"],
            "MEDIUM": cfg["confidence_penalty_med"],
            "LOW": cfg["confidence_penalty_low"]}.get(conf or "LOW",
                                                     cfg["confidence_penalty_low"])


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def size_for_release(conn, release_id, symbol="GOLD", rules_name=None):
    cfg = _ensure_config(conn)
    edge = learn_lookup.historical_edge_for_release(conn, release_id, symbol)
    if not edge.get("found"):
        return {"sized": False, "reason": edge.get("reason", "no edge"), "config": cfg}

    p = edge["win_rate"]
    avg_w = edge["avg_mfe_pct"] or 0.0
    avg_l = edge["avg_mae_pct"] or 0.01
    if avg_l <= 0 or p <= 0:
        return {"sized": False, "reason": "degenerate stats", "edge": edge, "config": cfg}

    b = avg_w / avg_l
    q = 1.0 - p
    kelly_raw = p - (q / b) if b > 0 else -1.0
    if kelly_raw <= 0:
        return {"sized": False, "reason": f"non-positive Kelly ({kelly_raw:+.3f})",
                "edge": edge, "config": cfg}

    c_mult = _conf_mult(edge.get("confidence"), cfg)
    kelly_adj = kelly_raw * cfg["kelly_fraction"] * c_mult
    risk_pct = _clamp(kelly_adj * 100.0,
                      cfg["min_risk_pct"], cfg["max_risk_pct"])
    risk_amount = cfg["account_size"] * risk_pct / 100.0

    result = {"sized": True, "release_id": release_id, "symbol": symbol,
              "rules_name": rules_name, "edge_level": edge["level"],
              "n_samples": edge["n_samples"], "win_rate": p,
              "avg_win_pct": avg_w, "avg_loss_pct": avg_l,
              "kelly_raw": round(kelly_raw, 4),
              "kelly_adjusted": round(kelly_adj, 4),
              "confidence": edge.get("confidence"),
              "confidence_mult": c_mult,
              "risk_pct": round(risk_pct, 4),
              "risk_amount": round(risk_amount, 2),
              "notes": f"half-Kelly {cfg['kelly_fraction']} × {edge.get('confidence','LOW')}",
              "config": cfg}

    conn.execute("""
        INSERT INTO sizing_decisions
          (release_id, symbol, rules_name, edge_level, n_samples,
           win_rate, avg_win_pct, avg_loss_pct, kelly_raw, kelly_adjusted,
           confidence, confidence_mult, risk_pct, risk_amount, notes, computed_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (release_id, symbol, rules_name, edge["level"], edge["n_samples"],
          p, avg_w, avg_l, kelly_raw, kelly_adj,
          edge.get("confidence"), c_mult, risk_pct, risk_amount,
          result["notes"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return result