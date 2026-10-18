"""Provisional, provenance-gated fundamental assessments for USD, Gold and EUR/USD."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from cne.config import settings
from cne.core.events import BY_CODE

_IMPACT_SIGN = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}
_CATEGORY_WEIGHT = {"inflation": 1.0, "labour": 0.8, "growth": 0.55, "policy": 1.0}
_TIER_WEIGHT = {1: 2.0, 2: 1.0, 3: 0.5}
_YIELD_SERIES = {
    "US02Y": ("US2Y_DAILY", "US2Y_FRED_DAILY"),
    "US10Y": ("US10Y_DAILY", "US10Y_FRED_DAILY"),
    "REAL10Y": ("REAL10Y_DAILY", "REAL10Y_FRED_DAILY"),
}


def _event_weights() -> dict[str, float]:
    try:
        raw = json.loads(settings.fundamental_event_weights_json)
    except json.JSONDecodeError as exc:
        raise ValueError("CNE_FUNDAMENTAL_EVENT_WEIGHTS_JSON must be valid JSON.") from exc
    if not isinstance(raw, dict):
        raise TypeError("CNE_FUNDAMENTAL_EVENT_WEIGHTS_JSON must be a JSON object.")
    weights = {}
    for code, value in raw.items():
        if code not in BY_CODE:
            raise ValueError(f"Unknown event code in fundamental weights: {code}.")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"Weight for {code} must be a number from 0 to 10.")
        if not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError(f"Weight for {code} must be a number from 0 to 10.")
        weights[code] = float(value)
    return weights


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _group(code: str) -> str:
    if "CPI" in code:
        suffix = "mom" if "_MOM" in code else "yoy"
        return f"cpi_{suffix}"
    if "PPI" in code:
        suffix = "mom" if "_MOM" in code else "yoy"
        return f"ppi_{suffix}"
    if code in {"US_NFP", "US_UNEMPLOYMENT", "US_AHE_MOM"}:
        return "employment_report"
    if code in {"EU_EMPLOYMENT_QOQ", "EU_UNEMPLOYMENT"}:
        return "euro_employment"
    return code.lower()


def _signal(row: dict) -> float:
    if row["impact"] == "NEUTRAL":
        return 0.0
    sign = _IMPACT_SIGN[row["impact"]]
    normalized = row["normalized_surprise"]
    if normalized is not None:
        return sign * min(1.0, abs(math.tanh(float(normalized) / 2.0)))
    definition = BY_CODE[row["event_code"]]
    scale = max(abs(definition.large_thr), abs(definition.inline_tol), 1e-12)
    return sign * min(1.0, abs(float(row["raw_surprise"] or 0.0)) / scale)


def _verified_groups(conn, country: str, now: datetime) -> tuple[list[dict], list[str]]:
    cutoff = now - timedelta(days=settings.fundamental_window_days)
    rows = conn.execute(
        """
        SELECT r.id AS release_id, r.event_code, r.period, r.release_time_utc,
               r.previous, r.revised_previous, r.actual, r.forecast,
               s.raw_surprise, s.normalized_surprise, s.normalization_status,
               s.impact, s.data_status, result.actual_source,
               result.publication_timestamp_utc, result.retrieved_at_utc,
               result.is_revision
        FROM release_monitor_results AS result
        JOIN releases AS r ON r.id = result.release_id
        JOIN event_definitions AS event ON event.code = r.event_code
        JOIN surprises AS s ON s.release_id = r.id
        WHERE event.country = ? AND result.data_status = 'VERIFIED'
          AND s.data_status = 'VERIFIED' AND result.raw_surprise IS NOT NULL
        ORDER BY r.release_time_utc DESC, r.id DESC
        """,
        (country,),
    ).fetchall()
    missing = []
    grouped: dict[str, list[dict]] = {}
    weights = _event_weights()
    for raw in rows:
        row = dict(raw)
        timestamp = _parse_time(row["release_time_utc"])
        if not timestamp or timestamp < cutoff or timestamp > now:
            continue
        definition = BY_CODE.get(row["event_code"])
        if not definition:
            continue
        age_days = max(0.0, (now - timestamp).total_seconds() / 86400)
        decay = math.exp(-math.log(2) * age_days / settings.fundamental_decay_half_life_days)
        base = weights.get(
            definition.code,
            _CATEGORY_WEIGHT.get(definition.category, 0.0)
            * _TIER_WEIGHT.get(definition.tier, 1.0),
        )
        row.update(
            {
                "name": definition.name,
                "category": definition.category,
                "signal": _signal(row),
                "weight": base,
                "decay": decay,
                "effective_weight": base * decay,
                "group": _group(definition.code),
                "age_days": age_days,
            }
        )
        grouped.setdefault(f"{row['group']}:{row['period']}", []).append(row)

    contributions = []
    for key, members in grouped.items():
        total_weight = sum(member["effective_weight"] for member in members)
        if total_weight <= 0:
            continue
        combined = sum(
            member["signal"] * member["effective_weight"] for member in members
        ) / total_weight
        group_weight = max(member["weight"] for member in members)
        decay = max(member["decay"] for member in members)
        points = combined * group_weight * decay
        contributions.append(
            {
                "group": key,
                "events": [
                    {
                        "event_code": member["event_code"],
                        "name": member["name"],
                        "period": member["period"],
                        "raw_surprise": member["raw_surprise"],
                        "normalized_surprise": member["normalized_surprise"],
                        "normalization_status": member["normalization_status"],
                        "impact": member["impact"],
                        "source": member["actual_source"],
                        "published_at_utc": member["publication_timestamp_utc"],
                        "retrieved_at_utc": member["retrieved_at_utc"],
                        "is_revision": bool(member["is_revision"]),
                        "previous": member["previous"],
                        "revised_previous": member["revised_previous"],
                        "revision_delta": (
                            round(member["revised_previous"] - member["previous"], 4)
                            if member["revised_previous"] is not None
                            and member["previous"] is not None
                            else None
                        ),
                    }
                    for member in members
                ],
                "categories": sorted({member["category"] for member in members}),
                "score": round(combined, 4),
                "weight": round(group_weight, 4),
                "decay": round(decay, 4),
                "points": round(points, 4),
                "conflict": len({math.copysign(1, member["signal"]) for member in members if member["signal"]}) > 1,
            }
        )
    if not contributions:
        missing.append(f"No verified {country} surprises in the configured window.")
    return contributions, missing


def _yield_context(conn, now: datetime) -> tuple[dict, list[str]]:
    context = {}
    missing = []
    for label, indicators in _YIELD_SERIES.items():
        placeholders = ",".join("?" for _ in indicators)
        rows = conn.execute(
            f"""
            SELECT indicator, value_numeric, unit, source, observed_utc, fetched_utc
            FROM data_observations
            WHERE domain = 'market' AND indicator IN ({placeholders})
              AND value_numeric IS NOT NULL
            ORDER BY observed_utc DESC, fetched_utc DESC, id DESC
            """,
            indicators,
        ).fetchall()
        latest_by_period = {}
        for row in rows:
            latest_by_period.setdefault(row["observed_utc"], dict(row))
        observations = sorted(latest_by_period.values(), key=lambda item: item["observed_utc"], reverse=True)
        if not observations:
            missing.append(f"{label} yield observations unavailable.")
            context[label] = {"status": "UNAVAILABLE", "timeframe": "daily"}
            continue
        age = now - (_parse_time(observations[0]["observed_utc"]) or now)
        if age > timedelta(days=3) or age < timedelta(0):
            missing.append(f"{label} yield observation is stale.")
            context[label] = {
                "status": "STALE",
                "value": observations[0]["value_numeric"],
                "unit": observations[0]["unit"],
                "source": observations[0]["source"],
                "observed_at_utc": observations[0]["observed_utc"],
                "timeframe": "daily",
                "change": None,
            }
            continue
        previous = observations[1] if len(observations) > 1 else None
        context[label] = {
            "status": "DAILY_CONTEXT",
            "value": observations[0]["value_numeric"],
            "unit": observations[0]["unit"],
            "source": observations[0]["source"],
            "observed_at_utc": observations[0]["observed_utc"],
            "timeframe": "daily",
            "change": (
                round(observations[0]["value_numeric"] - previous["value_numeric"], 4)
                if previous
                else None
            ),
        }
    return context, missing


def _policy_context(conn) -> tuple[dict, dict, list[str]]:
    expectations = {}
    rates = {}
    missing = []
    for currency, expectation_indicator, rate_indicators in (
        (
            "USD",
            "FED_RATE_EXPECTATIONS",
            (
                "FEDERAL_FUNDS_EFFECTIVE_RATE",
                "FEDERAL_FUNDS_TARGET_LOWER",
                "FEDERAL_FUNDS_TARGET_UPPER",
            ),
        ),
        ("EUR", "ECB_RATE_EXPECTATIONS", ("ECB_DEPOSIT_FACILITY_RATE",)),
    ):
        expectation = conn.execute(
            """
            SELECT value_numeric, value_text, unit, source, observed_utc, fetched_utc
            FROM data_observations
            WHERE domain = 'central_bank' AND indicator = ?
            ORDER BY fetched_utc DESC, id DESC LIMIT 1
            """,
            (expectation_indicator,),
        ).fetchone()
        expectations[currency] = (
            dict(expectation) if expectation else {"status": "UNAVAILABLE"}
        )
        if not expectation:
            missing.append(f"{currency} market-implied policy expectations unavailable.")
        placeholders = ",".join("?" for _ in rate_indicators)
        rate_rows = conn.execute(
            f"""
            SELECT indicator, value_numeric, value_text, unit, source,
                   observed_utc, fetched_utc
            FROM data_observations
            WHERE domain = 'central_bank' AND indicator IN ({placeholders})
            ORDER BY fetched_utc DESC, id DESC
            """,
            rate_indicators,
        ).fetchall()
        latest = {}
        for rate_row in rate_rows:
            latest.setdefault(rate_row["indicator"], dict(rate_row))
        rates[currency] = list(latest.values())
        if not latest:
            missing.append(f"{currency} official policy-rate context unavailable.")
    return expectations, rates, missing


def _market_reaction(conn, now: datetime) -> tuple[dict, list[str]]:
    cutoff = (now - timedelta(days=settings.fundamental_window_days)).isoformat()
    rows = conn.execute(
        """
        SELECT r.event_code, r.period, r.release_time_utc,
               m.usd_dir, m.usd_change_pct, m.yield_dir, m.yield_change_bp,
               m.gold_dir, m.gold_change_pct, m.eurusd_dir, m.eurusd_change_pct,
               m.computed_utc
        FROM market_verdicts AS m
        JOIN releases AS r ON r.id = m.release_id
        WHERE m.computed_utc >= ?
        ORDER BY m.computed_utc DESC LIMIT 1
        """,
        (cutoff,),
    ).fetchone()
    if not rows:
        return {"status": "UNAVAILABLE"}, ["No recent post-release cross-asset reaction is available."]
    result = dict(rows)
    result["status"] = "OBSERVED"
    result["interpretation"] = "Observed market context; not used as a fundamental input."
    return result, []


def _risk_context(conn, now: datetime) -> tuple[dict, list[str]]:
    row = conn.execute(
        """
        SELECT indicator, value_numeric, value_text, unit, source, observed_utc,
               fetched_utc
        FROM data_observations
        WHERE domain = 'market' AND indicator LIKE '%VIX%'
        ORDER BY observed_utc DESC, fetched_utc DESC, id DESC LIMIT 1
        """
    ).fetchone()
    if not row:
        return {"status": "UNAVAILABLE"}, [
            "No sourced VIX/risk-sentiment series is available."
        ]
    item = dict(row)
    observed = _parse_time(item["observed_utc"])
    if not observed or now - observed > timedelta(days=3) or observed > now:
        item["status"] = "STALE"
        return item, ["Risk-sentiment observation is stale."]
    item["status"] = "OBSERVED"
    return item, []


def _direction(score: float, *, inverse: bool = False) -> str:
    adjusted = -score if inverse else score
    if adjusted > 0.35:
        return "BULLISH"
    if adjusted < -0.35:
        return "BEARISH"
    return "NEUTRAL"


def _summarize(
    score: float,
    contributions: list[dict],
    missing: list[str],
    conflicts: list[str],
    *,
    inverse: bool = False,
) -> dict:
    groups = len(contributions)
    magnitude = abs(score)
    if groups == 0:
        direction = "UNAVAILABLE"
        strength = "INSUFFICIENT"
    elif magnitude >= 3 and groups >= 3:
        direction = _direction(score, inverse=inverse)
        strength = "STRONG"
    elif magnitude >= 1.25 and groups >= 2:
        direction = _direction(score, inverse=inverse)
        strength = "MODERATE"
    elif magnitude >= 0.35:
        direction = _direction(score, inverse=inverse)
        strength = "WEAK"
    else:
        direction = _direction(score, inverse=inverse)
        strength = "MIXED"
    quality = "HIGH" if groups >= 4 and not missing else "MODERATE" if groups >= 2 else "LOW"
    drivers = sorted(contributions, key=lambda item: abs(item["points"]), reverse=True)[:5]
    if inverse:
        drivers = [
            {**driver, "points": round(-driver["points"], 4)}
            for driver in drivers
        ]
    return {
        "direction": direction,
        "fundamental_direction": direction,
        "strength_of_evidence": strength,
        "evidence_strength": strength,
        "evidence_quality": {
            "rating": quality,
            "verified_groups": groups,
            "verified_events": sum(len(item["events"]) for item in contributions),
            "calibration_status": "PROVISIONAL_UNVALIDATED",
        },
        "conflicting_evidence": conflicts,
        "missing_information": sorted(set(missing)),
        "main_drivers": drivers,
        "explanation": (
            "Largest recent verified grouped evidence: "
            + (
                ", ".join(
                    f"{driver['group']} ({driver['points']:+.2f})"
                    for driver in drivers[:3]
                )
                if drivers
                else "none available"
            )
            + "."
        ),
        "score": round(-score if inverse else score, 4),
        "score_is_probability": False,
    }


def assess_all(conn, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    usd_groups, usd_missing = _verified_groups(conn, "US", now)
    eur_groups, eur_missing = _verified_groups(conn, "EU", now)
    policy, policy_rates, policy_missing = _policy_context(conn)
    yields, yield_missing = _yield_context(conn, now)
    reaction, reaction_missing = _market_reaction(conn, now)
    risk, risk_missing = _risk_context(conn, now)

    def total(groups: list[dict]) -> float:
        return sum(group["points"] for group in groups)

    usd_score = total(usd_groups)
    eur_score = total(eur_groups)
    usd_conflicts = [
        f"Conflicting indicators within {group['group']}."
        for group in usd_groups + eur_groups
        if group["conflict"]
    ]
    if yields.get("US02Y", {}).get("change") is not None:
        move = yields["US02Y"]["change"]
        if usd_score and math.copysign(1, move) != math.copysign(1, usd_score):
            usd_conflicts.append("Daily US 2-year yield direction conflicts with macro releases.")
    if reaction.get("usd_dir") in {"RISING", "FALLING"}:
        market_usd_sign = 1 if reaction["usd_dir"] == "RISING" else -1
        if usd_score and math.copysign(1, usd_score) != market_usd_sign:
            usd_conflicts.append(
                f"Observed DXY reaction ({reaction['usd_dir'].lower()}) conflicts with the USD macro bias."
            )
    gold_conflicts = list(usd_conflicts)
    if yields.get("REAL10Y", {}).get("change") is not None:
        move = yields["REAL10Y"]["change"]
        if move > 0 and usd_score > 0:
            gold_conflicts.append("Rising daily real yields add pressure against Gold.")
        elif move < 0 and usd_score < 0:
            gold_conflicts.append("Falling daily real yields support Gold against the USD bias.")
    if reaction.get("gold_dir") in {"UP", "DOWN"}:
        expected_gold = "DOWN" if usd_score > 0 else "UP" if usd_score < 0 else None
        if expected_gold and reaction["gold_dir"] != expected_gold:
            gold_conflicts.append(
                f"Observed Gold reaction ({reaction['gold_dir'].lower()}) conflicts with the inverse USD macro bias."
            )
    pair_score = eur_score - usd_score
    pair_conflicts = list(usd_conflicts)
    if reaction.get("eurusd_dir") in {"UP", "DOWN"}:
        market_pair_sign = 1 if reaction["eurusd_dir"] == "UP" else -1
        if pair_score and math.copysign(1, pair_score) != market_pair_sign:
            pair_conflicts.append(
                f"Observed EUR/USD reaction ({reaction['eurusd_dir'].lower()}) conflicts with relative macro data."
            )
    common_missing = policy_missing + yield_missing + reaction_missing + risk_missing

    usd_missing = usd_missing + common_missing
    eur_missing = eur_missing + common_missing
    gold_missing = usd_missing.copy()
    pair_groups = eur_groups + [
        {**item, "points": -item["points"]} for item in usd_groups
    ]
    if yields.get("REAL10Y", {}).get("change") is None:
        gold_missing.append("Real-yield direction unavailable or unchanged; Gold rate channel is incomplete.")
    return {
        "generated_at_utc": now.isoformat(),
        "window_days": settings.fundamental_window_days,
        "decay_half_life_days": settings.fundamental_decay_half_life_days,
        "weight_overrides": _event_weights(),
        "weights_are_provisional": True,
        "methodology": (
            "Verified releases only; recency decays exponentially; correlated indicators "
            "within the same reporting period are grouped and contribute once."
        ),
        "USD": {
            **_summarize(usd_score, usd_groups, usd_missing, usd_conflicts),
            "category_scores": {
                category: round(
                    sum(
                        item["points"]
                        for item in usd_groups
                        if category in item["categories"]
                    ),
                    4,
                )
                for category in ("inflation", "labour", "growth", "policy")
            },
            "policy_direction": "UNAVAILABLE",
            "policy_context_note": (
                "No market-implied Fed rate-expectations series is currently stored; "
                "policy direction is not inferred from a single release."
            ),
            "policy_expectations": policy["USD"],
            "official_policy_rates": policy_rates["USD"],
            "yield_context": yields,
            "risk_sentiment": risk,
            "observed_market_reaction": reaction,
        },
        "GOLD": {
            **_summarize(usd_score, usd_groups, gold_missing, gold_conflicts, inverse=True),
            "policy_direction": "UNAVAILABLE",
            "fed_policy_expectations": policy["USD"],
            "fed_official_policy_rates": policy_rates["USD"],
            "real_yield_context": yields.get("REAL10Y"),
            "observed_market_reaction": reaction,
            "risk_sentiment": risk,
            "explanation": "Gold macro direction inverses verified USD pressure; daily real yields are contextual, not intraday confirmation.",
        },
        "EURUSD": {
            **_summarize(pair_score, pair_groups, eur_missing, pair_conflicts),
            "euro_score": round(eur_score, 4),
            "usd_score": round(usd_score, 4),
            "ecb_policy_expectations": policy["EUR"],
            "fed_policy_expectations": policy["USD"],
            "ecb_official_policy_rates": policy_rates["EUR"],
            "fed_official_policy_rates": policy_rates["USD"],
            "ecb_policy_direction": "UNAVAILABLE",
            "fed_policy_direction": "UNAVAILABLE",
            "yield_context": yields,
            "risk_sentiment": risk,
            "observed_market_reaction": reaction,
            "explanation": "Relative verified Euro-area and US data; policy expectations remain separate unless a sourced market-implied series is available.",
        },
    }
