"""Pre-trade calculator: lot size, pips, profit, warnings."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

INSTRUMENTS = {
    "EURUSD": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "USD"},
    "GBPUSD": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "USD"},
    "AUDUSD": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "USD"},
    "NZDUSD": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "USD"},
    "USDCHF": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "CHF"},
    "USDCAD": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "CAD"},
    "USDJPY": {"contract_size": 100_000, "pip_size": 0.01,   "pip_value_quote": 1000.0, "quote_ccy": "JPY"},
    "EURGBP": {"contract_size": 100_000, "pip_size": 0.0001, "pip_value_quote": 10.0, "quote_ccy": "GBP"},
    "EURJPY": {"contract_size": 100_000, "pip_size": 0.01,   "pip_value_quote": 1000.0, "quote_ccy": "JPY"},
    "XAUUSD": {"contract_size": 100,     "pip_size": 0.01,   "pip_value_quote": 1.0,  "quote_ccy": "USD"},
    "XAGUSD": {"contract_size": 5000,    "pip_size": 0.001,  "pip_value_quote": 5.0,  "quote_ccy": "USD"},
    "US500":  {"contract_size": 1,       "pip_size": 1.0,    "pip_value_quote": 1.0,  "quote_ccy": "USD"},
    "US30":   {"contract_size": 1,       "pip_size": 1.0,    "pip_value_quote": 1.0,  "quote_ccy": "USD"},
    "NAS100": {"contract_size": 1,       "pip_size": 1.0,    "pip_value_quote": 1.0,  "quote_ccy": "USD"},
}

FX_TO_USD = {"USD": 1.0, "JPY": 1/150.0, "CHF": 1.13, "CAD": 0.73,
             "GBP": 1.27, "EUR": 1.08}
LOT_STEP = 0.01
MAX_REASONABLE_LOT = 100.0


def _pip_value_in_account(spec, account_ccy="USD"):
    quote = spec["quote_ccy"]
    if quote == account_ccy:
        return spec["pip_value_quote"]
    if account_ccy == "USD":
        return spec["pip_value_quote"] * FX_TO_USD.get(quote, 1.0)
    if quote == "USD":
        return spec["pip_value_quote"] / FX_TO_USD.get(account_ccy, 1.0)
    return spec["pip_value_quote"] * FX_TO_USD.get(quote, 1.0) / FX_TO_USD.get(account_ccy, 1.0)


def _round_lot(lot, step=LOT_STEP):
    if lot <= 0: return 0.0
    return math.floor(lot / step + 1e-9) * step


def calculate(symbol, balance, risk_pct, entry, stop, target=None,
              direction=None, *, account_ccy="USD"):
    symbol = symbol.upper()
    spec = INSTRUMENTS.get(symbol)
    if not spec: return {"error": f"unknown symbol {symbol}"}
    if balance <= 0: return {"error": "balance must be positive"}
    if not (0 < risk_pct <= 100): return {"error": "risk_pct must be in (0,100]"}
    if entry <= 0 or stop <= 0: return {"error": "entry/stop must be positive"}
    if stop == entry: return {"error": "stop cannot equal entry"}

    if direction is None:
        direction = "LONG" if stop < entry else "SHORT"
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return {"error": "direction must be LONG or SHORT"}
    if direction == "LONG" and stop >= entry:
        return {"error": "LONG stop must be below entry"}
    if direction == "SHORT" and stop <= entry:
        return {"error": "SHORT stop must be above entry"}
    if target is not None:
        if direction == "LONG" and target <= entry:
            return {"error": "LONG target must be above entry"}
        if direction == "SHORT" and target >= entry:
            return {"error": "SHORT target must be below entry"}

    pip = spec["pip_size"]
    pip_value = _pip_value_in_account(spec, account_ccy)
    contract = spec["contract_size"]

    stop_dist = abs(entry - stop)
    pips_stop = stop_dist / pip
    target_dist = abs(target - entry) if target is not None else None
    pips_target = target_dist / pip if target_dist is not None else None
    risk_amount = balance * risk_pct / 100.0

    if pips_stop <= 0: return {"error": "stop distance is zero"}
    raw_lot = risk_amount / (pips_stop * pip_value)
    lot = _round_lot(raw_lot)
    if lot <= 0:
        return {"error": "stop too wide for account",
                "raw_lot": round(raw_lot, 4),
                "risk_amount": round(risk_amount, 2),
                "pips_stop": round(pips_stop, 2),
                "pip_value_per_lot": round(pip_value, 4)}

    actual_risk = lot * pips_stop * pip_value
    units = lot * contract
    profit = lot * pips_target * pip_value if pips_target is not None else None
    rr = profit / actual_risk if (profit and actual_risk) else None
    stop_pct = stop_dist / entry * 100.0
    target_pct = (target_dist / entry * 100.0) if target_dist is not None else None

    warnings, suggestions = _advise(balance, risk_pct, actual_risk, lot,
                                    raw_lot, rr, stop_pct, pips_stop, profit)

    return {
        "symbol": symbol, "direction": direction, "account_ccy": account_ccy,
        "balance": round(balance, 2), "risk_pct": round(risk_pct, 4),
        "risk_amount": round(actual_risk, 2),
        "requested_risk_amount": round(risk_amount, 2),
        "entry": entry, "stop": stop, "target": target,
        "pips_stop": round(pips_stop, 2),
        "pips_target": round(pips_target, 2) if pips_target is not None else None,
        "pip_size": pip, "pip_value_per_lot": round(pip_value, 4),
        "contract_size": contract, "lot_size": round(lot, 2),
        "units": round(units, 2),
        "potential_profit": round(profit, 2) if profit is not None else None,
        "potential_loss": round(actual_risk, 2),
        "reward_risk": round(rr, 3) if rr is not None else None,
        "stop_pct": round(stop_pct, 3),
        "target_pct": round(target_pct, 3) if target_pct is not None else None,
        "warnings": warnings, "suggestions": suggestions,
    }


def _advise(balance, risk_pct, risk_amount, lot, raw_lot, rr, stop_pct, pips_stop, profit):
    warnings, suggestions = [], []
    if risk_pct > 5:
        warnings.append(f"risk {risk_pct:.2f}% is aggressive — pros cap at 1–2%")
    elif risk_pct > 2:
        suggestions.append(f"risk {risk_pct:.2f}% is above 2% — consider 1%")
    if stop_pct < 0.10:
        warnings.append(f"stop is only {stop_pct:.3f}% — very tight, noise-prone")
    if lot > 10:
        warnings.append(f"lot {lot:.2f} is large — verify broker accepts")
    if lot > MAX_REASONABLE_LOT:
        warnings.append(f"lot {lot:.2f} exceeds retail limits")
    if stop_pct > 5:
        suggestions.append(f"stop {stop_pct:.2f}% away — consider tighter level")
    if raw_lot and lot and raw_lot - lot > 0.5 * LOT_STEP:
        suggestions.append(f"raw lot {raw_lot:.3f} rounded to {lot:.2f}")
    if rr is not None:
        if rr < 1.0:
            warnings.append(f"R:R is {rr:.2f} — risking more than reward")
        elif rr < 1.5:
            suggestions.append(f"R:R is {rr:.2f} — below 1.5 benchmark")
        elif rr >= 3.0:
            suggestions.append(f"R:R is {rr:.2f} — excellent if target is real")
    return warnings, suggestions


def save(conn, result, *, release_id=None, rules_name=None, notes=None):
    if result.get("error"): return None
    conn.execute("""
        INSERT INTO trade_simulations
          (ts_utc, symbol, direction, account_balance, risk_pct, risk_amount,
           entry, stop, target, pips_stop, pips_target, pip_value,
           lot_size, units, potential_profit, potential_loss, reward_risk,
           stop_pct, target_pct, warnings, suggestions,
           release_id, rules_name, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(),
          result["symbol"], result["direction"], result["balance"],
          result["risk_pct"], result["risk_amount"],
          result["entry"], result["stop"], result.get("target"),
          result["pips_stop"], result.get("pips_target"),
          result["pip_value_per_lot"], result["lot_size"], result["units"],
          result.get("potential_profit"), result["potential_loss"],
          result.get("reward_risk"), result["stop_pct"],
          result.get("target_pct"),
          json.dumps(result["warnings"]), json.dumps(result["suggestions"]),
          release_id, rules_name, notes))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def history(conn, symbol=None, limit=30):
    if symbol:
        rows = conn.execute("""
            SELECT * FROM trade_simulations WHERE symbol=?
            ORDER BY ts_utc DESC LIMIT ?
        """, (symbol.upper(), limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM trade_simulations ORDER BY ts_utc DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def suggest_from_kelly(conn, release_id, symbol="GOLD", balance=10000.0):
    try:
        from cne.core import sizing as sz
        r = sz.size_for_release(conn, release_id, symbol)
    except Exception as e:
        return {"found": False, "reason": str(e)}
    if not r.get("sized"):
        return {"found": False, "reason": r.get("reason", "no edge")}
    return {"found": True, "release_id": release_id, "symbol": symbol,
            "suggested_risk_pct": r["risk_pct"],
            "suggested_risk_amount": balance * r["risk_pct"] / 100.0,
            "suggested_rr": round(r["avg_win_pct"] / r["avg_loss_pct"], 2),
            "edge_level": r["edge_level"], "n_samples": r["n_samples"],
            "win_rate": r["win_rate"], "confidence": r["confidence"],
            "notes": r["notes"]}