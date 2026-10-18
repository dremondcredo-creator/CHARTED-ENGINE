"""Local HTTP and server-sent event interface for the dashboard."""
from __future__ import annotations

import json
import logging
import math
import mimetypes
import select
import socket
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from cne import __version__
from cne.collectors import pipeline as collection_pipeline
from cne.collectors.pipeline import collect_all
from cne.config import settings
from cne.core import fundamentals, lights, macro, market, simulator
from cne.core.events import BY_CODE, seed
from cne.db.database import DB_ERRORS, connect, data_version, init_db
from cne.learning.lookup import historical_edge_for_release
from cne.scheduler.planner import build_schedule
from cne import web_auth

log = logging.getLogger(__name__)

TABLES = (
    "meta",
    "event_definitions",
    "releases",
    "data_observations",
    "collection_runs",
    "surprises",
    "release_monitor_results",
    "price_snapshots",
    "market_verdicts",
    "macro_scores",
    "macro_snapshots",
    "trend_snapshots",
    "labour_reports",
    "pivots",
    "structure_verdicts",
    "key_levels",
    "chop_snapshots",
    "chop_history",
    "macro_regimes",
    "release_regimes",
    "outcome_labels",
    "setup_features",
    "edge_stats",
    "edge_stats_regime",
    "backtest_rules",
    "backtest_runs",
    "backtest_trades",
    "sizing_config",
    "sizing_decisions",
    "trade_simulations",
    "alerts_log",
    "scheduler_log",
)
POST_RELEASE_PHASES = ("T0", "T+5M", "T+30M", "T+2H")
COUNTRIES = ("US", "EU", "UK", "JP", "CA", "AU")
STATIC_ROOT = settings.base_dir / "frontend" / "dist"


def _rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _row(conn: sqlite3.Connection, query: str, params: tuple = ()) -> dict | None:
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def _format_number(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _signed(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}"


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in TABLES
    }


def _release_query(
    conn: sqlite3.Connection,
    *,
    country: str | None = None,
    tier: int | None = None,
    query: str = "",
    limit: int = 50,
) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    clauses = ["1=1"]
    values: list[object] = []
    if country:
        clauses.append("e.country = ?")
        values.append(country)
    if tier is not None:
        clauses.append("e.tier = ?")
        values.append(tier)
    if query:
        clauses.append("(e.name LIKE ? OR e.code LIKE ? OR r.period LIKE ?)")
        term = f"%{query}%"
        values.extend((term, term, term))
    values.extend((now, now, max(1, min(limit, 200))))
    return _rows(
        conn,
        f"""
        SELECT r.id, r.event_code, e.name, e.country, e.category, e.tier, e.unit,
               r.period, r.release_time_utc,
               CASE WHEN r.actual IS NULL THEN r.previous
                    ELSE COALESCE(result.previous_value, r.previous) END AS previous,
               CASE WHEN r.actual IS NULL THEN r.forecast
                    ELSE result.forecast_at_release END AS forecast,
               r.actual,
               r.revised_previous, r.actual_source, r.forecast_source,
               s.raw_surprise, s.normalized_surprise, s.normalization_status,
               s.data_status AS surprise_data_status, s.direction, s.magnitude, s.impact,
               result.data_status AS data_quality_status,
               result.publication_timestamp_utc, result.retrieved_at_utc,
               CASE WHEN r.actual IS NULL THEN 'UPCOMING' ELSE 'RELEASED' END AS state
        FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        LEFT JOIN surprises s ON s.release_id = r.id
        LEFT JOIN release_monitor_results result ON result.release_id = r.id
        WHERE {' AND '.join(clauses)}
        ORDER BY
          CASE WHEN r.release_time_utc >= ? THEN 0 ELSE 1 END,
          CASE WHEN r.release_time_utc >= ? THEN r.release_time_utc END ASC,
          r.release_time_utc DESC
        LIMIT ?
        """,
        tuple(values),
    )


def _serialize_schedule_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_schedule_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_schedule_value(item) for item in value]
    return value


def _schedule(conn: sqlite3.Connection) -> list[dict]:
    return [_serialize_schedule_value(job) for job in build_schedule(conn)]


def _release_lights(conn: sqlite3.Connection, release: dict, macro_score: dict) -> dict:
    fundamental = lights.fundamental_light(
        conn, release["event_code"], release["period"], macro_score
    )
    market_row = _row(
        conn, "SELECT * FROM market_verdicts WHERE release_id = ?", (release["id"],)
    )
    structure = _row(
        conn,
        "SELECT * FROM structure_verdicts WHERE release_id = ? AND symbol = 'GOLD'",
        (release["id"],),
    )
    market_score = market_row["market_score"] if market_row else 0.0
    structure_score = structure["confirmation_score"] if structure else 0.0
    combined = round(
        fundamental.get("score", 0.0) + market_score + structure_score, 2
    )
    if combined >= 4.0:
        label = "A+ SETUP — fully aligned"
        light = "🟢"
    elif combined >= 2.0:
        label = "STRONG — all three lean same way"
        light = "🟢"
    elif combined <= -2.0:
        label = "INVALIDATED — dimensions contradict"
        light = "🔴"
    else:
        label = "MIXED — no clean trade"
        light = "🟡"
    return {
        "fundamental": fundamental,
        "market": market_row,
        "structure": structure,
        "combined_score": combined,
        "light": light,
        "label": label,
    }


def _latest_market_data(conn: sqlite3.Connection) -> list[dict]:
    snapshots = _rows(
        conn,
        """
        SELECT p.symbol, p.price, p.change_pct, p.change_abs, p.phase,
               p.bar_ts_utc, p.captured_utc, p.source, r.event_code, r.period
        FROM price_snapshots p
        JOIN releases r ON r.id = p.release_id
        JOIN (
            SELECT symbol, MAX(captured_utc) AS latest_utc
            FROM price_snapshots
            GROUP BY symbol
        ) latest ON latest.symbol = p.symbol AND latest.latest_utc = p.captured_utc
        ORDER BY p.symbol
        """,
    )
    quote_rows = _rows(
        conn,
        """
        SELECT d.indicator, d.value_numeric, d.observed_utc, d.fetched_utc,
               d.source, d.publication_status, d.metadata_json
        FROM data_observations d
        JOIN (
            SELECT indicator, MAX(id) AS latest_id
            FROM data_observations
            WHERE indicator IN (
                'DXY_BIQUOTE_SPOT', 'GOLD_BIQUOTE_SPOT', 'EURUSD_BIQUOTE_SPOT'
            )
            GROUP BY indicator
        ) latest ON latest.latest_id = d.id
        """,
    )
    symbol_by_indicator = {
        "DXY_BIQUOTE_SPOT": "DXY",
        "GOLD_BIQUOTE_SPOT": "GOLD",
        "EURUSD_BIQUOTE_SPOT": "EURUSD",
    }
    for quote in quote_rows:
        metadata = json.loads(quote.pop("metadata_json") or "{}")
        current = (
            metadata.get("data_status") == "LIVE"
            and metadata.get("market_state") == "open"
            and not metadata.get("is_delayed")
        )
        snapshots.append(
            {
                "symbol": symbol_by_indicator[quote["indicator"]],
                "price": quote["value_numeric"],
                "change_pct": metadata.get("day_diff_percent") if current else None,
                "change_abs": None,
                "phase": metadata.get("data_status", quote["publication_status"]),
                "data_status": metadata.get("data_status", quote["publication_status"]),
                "bar_ts_utc": metadata.get("source_timestamp_utc") or quote["observed_utc"],
                "captured_utc": metadata.get("retrieved_at_utc") or quote["fetched_utc"],
                "source": quote["source"],
                "event_code": None,
                "period": None,
                "bid": metadata.get("bid"),
                "ask": metadata.get("ask"),
                "spread": metadata.get("spread"),
                "market_state": metadata.get("market_state"),
                "quote_age_seconds": metadata.get("quote_age_seconds"),
                "is_delayed": metadata.get("is_delayed"),
            }
        )
    return sorted(snapshots, key=lambda row: row["captured_utc"] or "", reverse=True)


def _market_evidence(conn: sqlite3.Connection, release: dict) -> dict:
    stored = _row(
        conn, "SELECT * FROM market_verdicts WHERE release_id = ?", (release["id"],)
    )
    if release.get("country") not in (None, "US"):
        snapshot_count = conn.execute(
            "SELECT COUNT(*) FROM price_snapshots WHERE release_id = ? AND phase != 'PRE'",
            (release["id"],),
        ).fetchone()[0]
        return {
            "status": "MODEL LIMITATION",
            "final": False,
            "phase": market.DEFAULT_PHASE if stored else None,
            "alignment": None,
            "score": stored["market_score"] if stored else None,
            "notes": (
                "The current market-alignment model compares USD and US yields with a US "
                "fundamental signal. This non-US release needs currency-pair-specific "
                "interpretation, so no confirm/contradict verdict is inferred."
            ),
            "verdict": stored,
            "snapshot_count": snapshot_count,
        }
    if stored:
        return {
            "status": "CONFIRMED",
            "final": True,
            "phase": market.DEFAULT_PHASE,
            "alignment": stored["market_alignment"],
            "score": stored["market_score"],
            "notes": stored["notes"],
            "verdict": stored,
        }

    observed = {
        row["phase"]
        for row in _rows(
            conn, "SELECT DISTINCT phase FROM price_snapshots WHERE release_id = ?",
            (release["id"],)
        )
    }
    phase = next((phase for phase in reversed(POST_RELEASE_PHASES) if phase in observed), None)
    if phase is None:
        return {
            "status": "AWAITING DATA",
            "final": False,
            "phase": None,
            "alignment": None,
            "score": None,
            "notes": None,
            "verdict": None,
            "snapshot_count": 0,
        }

    verdict = market.compute_verdict(conn, release["id"], phase)
    if verdict is None:
        return {
            "status": "AWAITING DATA",
            "final": False,
            "phase": phase,
            "alignment": None,
            "score": None,
            "notes": "No USD, yield, Gold, or EURUSD post-release price reaction was captured.",
            "verdict": None,
        }
    impact = release.get("impact")
    if impact is None:
        signal = "UNCLASSIFIED"
        score = None
        notes = "The release surprise is not classified; there is no fundamental direction to confirm."
    else:
        signal, score, notes = market.align_with_fundamentals(verdict, impact)
    count = conn.execute(
        "SELECT COUNT(*) FROM price_snapshots WHERE release_id = ? AND phase = ?",
        (release["id"], phase),
    ).fetchone()[0]
    return {
        "status": signal,
        "final": False,
        "phase": phase,
        "alignment": signal,
        "score": score,
        "notes": notes,
        "verdict": verdict,
        "snapshot_count": count,
    }


def _structure_evidence(conn: sqlite3.Connection, release_id: int, impact: str | None) -> dict:
    now = datetime.now(timezone.utc)
    rows = _rows(
        conn,
        """
        SELECT * FROM structure_verdicts
        WHERE release_id = ? AND symbol IN ('GOLD', 'EURUSD')
        ORDER BY symbol
        """,
        (release_id,),
    )
    latest_chop = _rows(
        conn,
        """
        SELECT c.symbol, c.timeframe, c.chop_score, c.chop_label, c.ts_utc, c.n_bars
        FROM chop_snapshots c
        JOIN (
          SELECT symbol, MAX(ts_utc) AS latest_ts FROM chop_snapshots
          WHERE symbol IN ('GOLD', 'EURUSD') AND timeframe = '5m' GROUP BY symbol
        ) latest ON latest.symbol = c.symbol AND latest.latest_ts = c.ts_utc
        ORDER BY c.symbol
        """,
    )
    chop_by_symbol = {row["symbol"]: row for row in latest_chop}
    levels = _rows(
        conn,
        """
        SELECT symbol, kind, COUNT(*) AS n_levels, MAX(touches) AS max_touches,
               MAX(strength) AS max_strength, MAX(detected_utc) AS latest_detection
        FROM key_levels WHERE symbol IN ('GOLD', 'EURUSD')
        GROUP BY symbol, kind
        ORDER BY symbol, kind
        """,
    )
    levels_by_symbol: dict[str, list[dict]] = {"GOLD": [], "EURUSD": []}
    for row in levels:
        levels_by_symbol[row["symbol"]].append(row)
    structures_by_symbol = {row["symbol"]: row for row in rows}
    observations = []
    for symbol in ("GOLD", "EURUSD"):
        observation = {
            "symbol": symbol,
            "event_structure": structures_by_symbol.get(symbol),
            "chop": chop_by_symbol.get(symbol),
            "levels": levels_by_symbol[symbol],
            "historical_edge": None,
        }
        if impact and conn.execute(
            """
            SELECT 1 FROM releases r
            JOIN surprises s ON s.release_id = r.id
            JOIN release_monitor_results result ON result.release_id = r.id
            WHERE r.id = ? AND result.data_status = 'VERIFIED'
              AND r.actual IS NOT NULL
            """,
            (release_id,),
        ).fetchone():
            observation["historical_edge"] = historical_edge_for_release(
                conn, release_id, symbol
            )
        observations.append(observation)

    observed_structures = [item for item in rows if item["confirmation_score"] is not None]
    preferred = None
    method = "No stored post-release structure or fresh price-cleanliness observations."
    reason = "Run post-release snapshots and event structure classification before choosing an instrument."
    confidence = "INSUFFICIENT"
    if len(observed_structures) == 2:

        def structure_rank(item):
            event_class = item["event_class"]
            if event_class in ("BREAK_UP", "BREAK_DOWN"):
                if item["held_retest"] == 1:
                    return 3
                if item["held_retest"] == 0:
                    return 2
                return 1
            return 1 if event_class in ("FADE_UP", "FADE_DOWN") else 0

        ranked = sorted(observed_structures, key=structure_rank, reverse=True)
        method = (
            "Compares stored event-level break/fade classifications and whether the "
            "two-hour retest held; a clean retest outranks an unconfirmed move."
        )
        if structure_rank(ranked[0]) > structure_rank(ranked[1]):
            preferred = ranked[0]["symbol"]
            confidence = "EVENT STRUCTURE"
            reason = (
                f"{preferred} has the stronger event-level break/retest structure "
                f"({ranked[0]['event_class']}, retest "
                f"{'held' if ranked[0]['held_retest'] == 1 else 'not held' if ranked[0]['held_retest'] == 0 else 'not tested'}; "
                f"{ranked[1]['symbol']} {ranked[1]['event_class']})."
            )
        else:
            confidence = "EVENT STRUCTURE"
            reason = (
                f"No clear structural leader: both instruments have equal-strength "
                f"event classes ({ranked[0]['symbol']} {ranked[0]['event_class']}; "
                f"{ranked[1]['symbol']} {ranked[1]['event_class']})."
            )
    else:
        observed_chop = []
        for item in observations:
            chop = item["chop"]
            timestamp = _parse_utc(chop["ts_utc"]) if chop else None
            if (
                chop
                and chop["chop_score"] is not None
                and timestamp
                and 0 <= (now - timestamp).total_seconds() <= 24 * 60 * 60
            ):
                observed_chop.append(chop)
        if len(observed_chop) == 2:
            scores = sorted(observed_chop, key=lambda item: item["chop_score"])
            if scores[1]["chop_score"] - scores[0]["chop_score"] >= 5:
                preferred = scores[0]["symbol"]
                confidence = "CLEANLINESS PROXY"
                method = "Compares fresh 5-minute chop scores; lower scores mean cleaner tape, not confirmed event structure."
                reason = (
                    f"{preferred} currently has the cleaner 5-minute tape "
                    f"(chop {scores[0]['chop_score']:.1f} vs "
                    f"{scores[1]['chop_score']:.1f}); this is not a release-level "
                    "break/retest verdict."
                )
            else:
                confidence = "CLEANLINESS PROXY"
                method = "Compares fresh 5-minute chop scores; lower scores mean cleaner tape, not confirmed event structure."
                reason = (
                    f"No clear cleanliness leader: fresh chop scores differ by less "
                    f"than 5 points ({scores[0]['symbol']} {scores[0]['chop_score']:.1f}; "
                    f"{scores[1]['symbol']} {scores[1]['chop_score']:.1f})."
                )
        elif len(observed_structures) == 1:
            only = observed_structures[0]
            confidence = "PARTIAL EVENT STRUCTURE"
            method = "Only one instrument has a persisted release-level structure verdict."
            reason = (
                f"{only['symbol']} is the only instrument with a stored event-level "
                f"verdict ({only['event_class']} · {only['confirmation']}); "
                "the other instrument cannot yet be compared."
            )

    if not preferred and not observed_structures:
        now_levels = [
            symbol for symbol, symbol_levels in levels_by_symbol.items() if symbol_levels
        ]
        if now_levels:
            confidence = "LEVEL MAP ONLY"
            method = "Stored support/resistance levels exist but no matched post-release break/retest verdict does."
            reason = (
                "Key levels are mapped for "
                + " and ".join(now_levels)
                + "; there is not enough stored post-release structure data to rank them."
            )

    return {
        "preferred": preferred,
        "reason": reason,
        "method": method,
        "confidence": confidence,
        "instruments": observations,
    }


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def _decision_brief(
    conn: sqlite3.Connection, release: dict | None, macro_score: dict | None = None
) -> dict | None:
    if release is None:
        return None
    meta = BY_CODE.get(release["event_code"])
    if meta is None:
        return None

    surprise = {
        "raw_surprise": release.get("raw_surprise")
        if release.get("data_quality_status") == "VERIFIED"
        else None,
        "direction": release.get("direction")
        if release.get("data_quality_status") == "VERIFIED"
        else None,
        "magnitude": release.get("magnitude")
        if release.get("data_quality_status") == "VERIFIED"
        else None,
        "impact": release.get("impact")
        if release.get("data_quality_status") == "VERIFIED"
        else None,
    }

    question_1 = "No actual-versus-forecast result is recorded yet."
    if release.get("data_quality_status") == "VERIFIED":
        actual = _format_number(release["actual"])
        forecast = _format_number(release["forecast"])
        previous = _format_number(release.get("previous"))
        raw = surprise.get("raw_surprise")
        question_1 = (
            f"Actual {actual} {meta.unit} vs {forecast} {meta.unit} forecast "
            f"(previous {previous} {meta.unit}); surprise {_signed(raw, 4)}. "
            f"{surprise.get('direction') or 'Direction unknown'} · "
            f"{surprise.get('magnitude') or 'unclassified'} · "
            f"{surprise.get('impact') or 'unclassified'} impact."
        )
    elif release.get("actual") is not None:
        question_1 = (
            "An actual value was reported, but an approved actual source and/or the "
            "forecast preserved before release could not be verified; no surprise was calculated."
        )

    impact = surprise.get("impact")
    if impact == "BULLISH":
        currency_bias = "constructive"
    elif impact == "BEARISH":
        currency_bias = "softer"
    else:
        currency_bias = "neutral or unclassified"
    label = (macro_score or {}).get("label")
    if meta.country == "US":
        if impact == "BULLISH":
            policy = "The event-specific interpretation is USD-supportive; one release alone does not establish a Fed policy shift."
        elif impact == "BEARISH":
            policy = "The event-specific interpretation is USD-negative; one release alone does not establish a Fed policy shift."
        elif impact == "NEUTRAL":
            policy = "The verified surprise is inside its configured neutral band; assess it with other releases and market-implied policy expectations."
        else:
            policy = "The Fed/USD read remains incomplete until the actual source and pre-release forecast are both verified."
        if label:
            policy += f" The existing 30-day US macro score is {label} ({_signed((macro_score or {}).get('total'))})."
        if not macro_score or not macro_score.get("n_releases"):
            policy += " No classified 30-day US macro releases are available to check the broader backdrop."
    else:
        central_banks = {
            "EU": "ECB", "UK": "BoE", "JP": "BoJ", "CA": "BoC", "AU": "RBA"
        }
        policy = (
            f"The event-specific interpretation is {currency_bias} for {meta.country}. "
            f"This single release does not establish a policy shift by "
            f"{central_banks.get(meta.country, 'the central bank')} or a direct "
            "Federal Reserve/USD signal."
        )

    market_result = _market_evidence(conn, {**release, "impact": impact})
    if market_result["alignment"]:
        phase_note = (
            "finalized from persisted T+30M verdict"
            if market_result["final"]
            else f"provisional from recorded {market_result['phase']} snapshots"
        )
        market_answer = (
            f"{market_result['alignment']} ({phase_note}). "
            f"{market_result['notes'] or ''}".strip()
        )
    elif market_result["status"] == "MODEL LIMITATION":
        market_answer = (
            f"Not scored: {market_result['notes']} "
            f"{market_result['snapshot_count']} post-release price snapshots are stored."
        )
    else:
        market_answer = (
            "Not yet measurable: no post-release price snapshots are stored. "
            "Missing market data is not evidence of confirmation or contradiction."
        )

    structure_result = _structure_evidence(conn, release["id"], impact)
    evidence_rows = [
        item["historical_edge"]
        for item in structure_result["instruments"]
        if item.get("historical_edge") and item["historical_edge"].get("found")
    ]
    best_edge = max(
        evidence_rows,
        key=lambda edge: (edge.get("n_samples") or 0),
        default=None,
    )
    final_structure_count = sum(
        1
        for item in structure_result["instruments"]
        if item.get("event_structure") is not None
    )
    has_classified_surprise = bool(
        release.get("actual") is not None
        and release.get("forecast") is not None
        and impact
    )
    complete_market = market_result["alignment"] is not None
    if (
        has_classified_surprise
        and market_result["final"]
        and final_structure_count == 2
        and best_edge
        and best_edge.get("n_samples", 0) >= 60
    ):
        evidence_rating = "HIGH"
    elif has_classified_surprise and complete_market and final_structure_count >= 1:
        evidence_rating = "MEDIUM"
    elif has_classified_surprise:
        evidence_rating = "LOW"
    else:
        evidence_rating = "INSUFFICIENT"

    evidence_reasons = []
    if has_classified_surprise:
        evidence_reasons.append(
            f"Actual and forecast are present; the engine's surprise is {surprise.get('impact')}."
        )
    else:
        evidence_reasons.append("A complete, classified actual-versus-forecast observation is not available.")
    if market_result["final"]:
        evidence_reasons.append("A finalized market verdict is persisted for this release.")
    elif market_result["phase"]:
        evidence_reasons.append(f"Only provisional {market_result['phase']} market evidence is persisted.")
    else:
        evidence_reasons.append("No post-release market snapshots are persisted.")
    evidence_reasons.append(
        f"{final_structure_count} of 2 instruments have persisted event-level structure verdicts."
    )
    if best_edge:
        evidence_reasons.append(
            f"Best historical {best_edge.get('level')} edge: n={best_edge['n_samples']}, "
            f"{(best_edge.get('win_rate') or 0) * 100:.1f}% wins, "
            f"{best_edge.get('confidence') or 'unrated'} confidence; "
            "historical estimates are not guarantees."
        )
    else:
        evidence_reasons.append(
            "No matching historical edge meets the engine's minimum n=15 sample threshold."
        )

    uncertainties = []
    if release.get("actual") is None:
        uncertainties.append("The actual has not been recorded; surprise and policy direction remain open.")
    elif release.get("forecast") is None:
        uncertainties.append("The forecast is missing; no actual-minus-forecast surprise can be verified.")
    if not market_result["final"]:
        if market_result["phase"]:
            uncertainties.append(
                f"The {market_result['phase']} market read is provisional; the engine's finalized verdict uses T+30M data."
            )
        else:
            if market_result["status"] == "MODEL LIMITATION":
                uncertainties.append(
                    "The USD-only market-alignment model cannot grade a non-US release; "
                    "currency-pair-specific reaction data is needed."
                )
            else:
                uncertainties.append(
                    "Post-release USD, yields, Gold and EURUSD snapshots are missing."
                )
    if final_structure_count < 2:
        uncertainties.append(
            "Both Gold and EURUSD need persisted release-level break/fade/retest verdicts for a direct structural comparison."
        )
    if structure_result["confidence"] == "CLEANLINESS PROXY":
        uncertainties.append(
            "The instrument comparison is based on recent chop scores; a clean tape does not prove a level breakout or retest."
        )
    if not best_edge:
        uncertainties.append(
            "There are fewer than 15 matching historical examples or no comparable feature group; probability is uncalibrated."
        )
    elif best_edge.get("n_samples", 0) < 60:
        uncertainties.append(
            f"Historical evidence uses only {best_edge['n_samples']} matched examples; the engine labels that "
            f"{best_edge.get('confidence', 'lower')} confidence."
        )
    if meta.country != "US":
        uncertainties.append("This is not a US release; its USD and Fed effects cannot be inferred from this number alone.")
    else:
        uncertainties.append(
            "A single indicator cannot determine the Federal Reserve's decision; other releases and policy communication can disagree."
        )

    macro_summary = None
    if macro_score:
        macro_summary = {
            "label": macro_score["label"],
            "total": macro_score["total"],
            "n_releases": macro_score["n_releases"],
            "window_days": macro_score["window_days"],
            "agrees_with_release": None
            if impact not in ("BULLISH", "BEARISH")
            else (
                ("HAWKISH" in macro_score["label"])
                == (impact == "BULLISH")
            ),
        }

    return {
        "release": {
            "id": release["id"],
            "name": release.get("name", meta.name),
            "country": release.get("country", meta.country),
            "period": release.get("period"),
            "status": "RELEASED" if release.get("actual") is not None else "UPCOMING",
        },
        "questions": [
            {
                "id": 1,
                "question": "What happened in the economic data?",
                "status": "OBSERVED" if has_classified_surprise else "AWAITING DATA",
                "answer": question_1,
                "evidence": {
                    "actual": release.get("actual"),
                    "forecast": release.get("forecast"),
                    "previous": release.get("previous"),
                    **surprise,
                    "unit": meta.unit,
                    "category": meta.category,
                },
            },
            {
                "id": 2,
                "question": "What does it mean for the Federal Reserve and the USD?",
                "status": "INTERPRETED" if has_classified_surprise else "AWAITING DATA",
                "answer": policy,
                "evidence": {
                    "country": meta.country,
                    "impact": impact,
                    "macro_context": macro_summary,
                },
            },
            {
                "id": 3,
                "question": "Is the market confirming that interpretation?",
                "status": market_result["status"],
                "answer": market_answer,
                "evidence": market_result,
            },
            {
                "id": 4,
                "question": "Which instrument has the clearest market structure?",
                "status": structure_result["confidence"],
                "answer": structure_result["reason"],
                "evidence": structure_result,
            },
            {
                "id": 5,
                "question": "How strong is the evidence, and what remains uncertain?",
                "status": evidence_rating,
                "answer": " ".join(evidence_reasons),
                "evidence": {
                    "historical_edges": evidence_rows,
                    "market_final": market_result["final"],
                    "structure_verdicts": final_structure_count,
                    "reasons": evidence_reasons,
                    "uncertainties": uncertainties,
                },
            },
        ],
    }


def _latest_release(conn: sqlite3.Connection, *, released_only: bool = False) -> dict | None:
    where = "AND r.actual IS NOT NULL" if released_only else ""
    return _row(
        conn,
        f"""
        SELECT r.id, r.event_code, e.name, e.country, e.category, e.tier, e.unit,
               r.period, r.release_time_utc,
               CASE WHEN r.actual IS NULL THEN r.previous
                    ELSE COALESCE(result.previous_value, r.previous) END AS previous,
               CASE WHEN r.actual IS NULL THEN r.forecast
                    ELSE result.forecast_at_release END AS forecast,
               r.actual,
               s.raw_surprise, s.normalized_surprise, s.normalization_status,
               s.direction, s.magnitude, s.impact,
               s.data_status AS surprise_data_status,
               result.data_status AS data_quality_status,
               result.publication_timestamp_utc, result.retrieved_at_utc
        FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        LEFT JOIN surprises s ON s.release_id = r.id
        LEFT JOIN release_monitor_results result ON result.release_id = r.id
        WHERE 1=1 {where}
        ORDER BY r.release_time_utc DESC, r.id DESC
        LIMIT 1
        """,
    )


def _layer_status(counts: dict[str, int], config: dict) -> list[dict]:
    return [
        {
            "id": "persistence",
            "name": "Persistence",
            "version": "SQLite · WAL",
            "detail": f"{counts['releases']} releases · {counts['event_definitions']} events",
            "status": "ready",
        },
        {
            "id": "collection",
            "name": "Collection",
            "version": "FinanceCalendar · BLS · Treasury · Eurostat/ECB · public quotes",
            "detail": (
                f"Calendar {'ready' if config['calendar_present'] else 'missing'} · "
                f"{counts['data_observations']} attributed observations"
            ),
            "status": "ready" if config["calendar_present"] else "attention",
        },
        {
            "id": "analysis",
            "name": "Analysis",
            "version": "Surprise · Trend · Macro · Market · Structure",
            "detail": (
                f"{counts['surprises']} classified · {counts['market_verdicts']} market "
                f"· {counts['structure_verdicts']} structure"
            ),
            "status": "ready" if counts["surprises"] else "waiting",
        },
        {
            "id": "composition",
            "name": "Composition",
            "version": "Three-light · Learning · Sizing · Simulator",
            "detail": (
                f"{counts['edge_stats']} edges · {counts['backtest_runs']} backtests "
                f"· {counts['trade_simulations']} simulations"
            ),
            "status": "ready" if counts["edge_stats"] else "waiting",
        },
        {
            "id": "interface",
            "name": "Interface",
            "version": "CLI · HTTP + SSE · Telegram",
            "detail": (
                f"{counts['scheduler_log']} scheduler jobs · "
                f"{counts['alerts_log']} alert records"
            ),
            "status": "ready",
        },
    ]


def _dashboard(conn: sqlite3.Connection) -> dict:
    now = datetime.now(timezone.utc)
    counts = _table_counts(conn)
    releases = _release_query(conn, limit=200)
    score = macro.compute_macro_score(conn, "US", 30)
    latest_release = _latest_release(conn, released_only=True)
    selected_release = latest_release or (
        releases[0] if releases else _latest_release(conn)
    )
    composition = (
        _release_lights(conn, latest_release, score) if latest_release else None
    )
    decision = (
        _decision_brief(conn, selected_release, score) if selected_release else None
    )
    macro_history = _rows(
        conn,
        """
        SELECT ts_utc, total_score, bias_label, n_releases
        FROM macro_snapshots WHERE country = 'US'
        ORDER BY ts_utc DESC LIMIT 30
        """,
    )
    macro_history.reverse()
    latest_scheduler = _row(
        conn,
        "SELECT ts_utc, job, release_id, status, detail FROM scheduler_log "
        "ORDER BY ts_utc DESC, id DESC LIMIT 1",
    )
    latest_calendar = _row(
        conn,
        "SELECT MAX(updated_utc) AS latest_update FROM releases",
    )
    upcoming_count = conn.execute(
        "SELECT COUNT(*) FROM releases WHERE release_time_utc >= ? AND actual IS NULL",
        (now.isoformat(),),
    ).fetchone()[0]
    counts["upcoming"] = upcoming_count
    system = {
        "version": __version__,
        "database_ready": Path(settings.db_path).exists(),
        "database_bytes": Path(settings.db_path).stat().st_size
        if Path(settings.db_path).exists()
        else 0,
        "database": "SQLite WAL",
        "latest_scheduler_job": latest_scheduler,
        "latest_data_update": latest_calendar["latest_update"],
        "calendar_present": bool(
            conn.execute("SELECT 1 FROM calendar_events LIMIT 1").fetchone()
        ) or settings.calendar_csv.exists(),
        "credentials": {
            "TELEGRAM_BOT_TOKEN": bool(settings.telegram_bot_token),
            "TELEGRAM_CHAT_ID": bool(settings.telegram_chat_id),
            "ALPHA_VANTAGE_API_KEY": bool(settings.alpha_vantage_api_key),
            "FRED_API_KEY": bool(settings.fred_api_key),
            "BEA_API_KEY": bool(settings.bea_api_key),
        },
    }
    layers = _layer_status(counts, system)
    wins = conn.execute(
        "SELECT COUNT(*) FROM outcome_labels WHERE outcome = 'WIN'"
    ).fetchone()[0]
    losses = conn.execute(
        "SELECT COUNT(*) FROM outcome_labels WHERE outcome = 'LOSS'"
    ).fetchone()[0]
    finished = wins + losses
    return {
        "generated_at": now.isoformat(),
        "system": system,
        "counts": counts,
        "layers": layers,
        "calendar": releases,
        "macro": score,
        "fundamental_assessments": fundamentals.assess_all(conn, now),
        "macro_history": macro_history,
        "latest_release": latest_release,
        "decision": decision,
        "composition": composition,
        "market_data": _latest_market_data(conn),
        "collection": collection_pipeline.coverage(conn),
        "learning": {
            "outcomes": wins + losses + conn.execute(
                "SELECT COUNT(*) FROM outcome_labels WHERE outcome = 'FLAT'"
            ).fetchone()[0],
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / finished, 4) if finished else None,
            "edges": _rows(
                conn,
                """
                SELECT symbol, feature_level, feature_key, n_samples, wins, losses,
                       flats, win_rate, avg_mfe_pct, avg_mae_pct, confidence, horizon,
                       updated_utc
                FROM edge_stats ORDER BY n_samples DESC, updated_utc DESC LIMIT 10
                """,
            ),
            "backtests": _rows(
                conn,
                """
                SELECT id, rules_name, symbol, start_ts_utc, end_ts_utc, n_trades,
                       n_wins, n_losses, win_rate, expectancy_pct, profit_factor,
                       sharpe_like, max_drawdown_pct, total_return_pct, computed_utc
                FROM backtest_runs ORDER BY computed_utc DESC LIMIT 10
                """,
            ),
        },
        "alerts": _rows(
            conn,
            """
            SELECT a.id, a.release_id, a.kind, a.symbol, a.status, a.error, a.ts_utc,
                   e.name AS event_name, r.period
            FROM alerts_log a
            LEFT JOIN releases r ON r.id = a.release_id
            LEFT JOIN event_definitions e ON e.code = r.event_code
            ORDER BY a.ts_utc DESC, a.id DESC LIMIT 25
            """,
        ),
        "schedule": _schedule(conn),
        "simulations": _rows(
            conn,
            """
            SELECT id, ts_utc, symbol, direction, account_balance, risk_pct,
                   risk_amount, entry, stop, target, lot_size, reward_risk,
                   potential_profit, potential_loss, release_id, notes
            FROM trade_simulations ORDER BY ts_utc DESC LIMIT 10
            """,
        ),
        "instrument_specs": simulator.INSTRUMENTS,
        "sizing_config": _row(
            conn, "SELECT * FROM sizing_config WHERE id = 1"
        ),
    }


def _release_detail(conn: sqlite3.Connection, release_id: int) -> dict | None:
    release = _row(
        conn,
        """
        SELECT r.id, r.event_code, e.name, e.country, e.category, e.tier, e.unit,
               e.higher_is, r.period, r.release_time_utc,
               CASE WHEN r.actual IS NULL THEN r.previous
                    ELSE COALESCE(result.previous_value, r.previous) END AS previous,
               CASE WHEN r.actual IS NULL THEN r.forecast
                    ELSE result.forecast_at_release END AS forecast,
               r.actual, r.revised_previous, r.actual_source, r.forecast_source,
               r.first_seen_utc, r.updated_utc,
               s.raw_surprise, s.normalized_surprise, s.normalization_status,
               s.data_status AS surprise_data_status,
               s.direction, s.magnitude, s.impact, s.computed_utc,
               result.data_status AS data_quality_status,
               result.actual_value AS verified_actual,
               result.forecast_at_release, result.previous_value AS verified_previous,
               result.revised_previous AS verified_revised_previous,
               result.unit AS verified_unit, result.actual_source AS verified_actual_source,
               result.source_record_id, result.publication_timestamp_utc,
               result.retrieved_at_utc,
               result.forecast_source AS forecast_source_at_release,
               result.forecast_retrieved_at_utc, result.previous_source,
               result.previous_retrieved_at_utc, result.is_revision,
               result.status_details_json, result.historical_scale,
               result.normalization_n, result.economic_direction,
               result.currency_impact
        FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        LEFT JOIN surprises s ON s.release_id = r.id
        LEFT JOIN release_monitor_results result ON result.release_id = r.id
        WHERE r.id = ?
        """,
        (release_id,),
    )
    if release is None:
        return None
    release["monitor_status_details"] = json.loads(
        release.pop("status_details_json") or "{}"
    )
    phases = _rows(
        conn,
        """
        SELECT phase, symbol, price, change_pct, change_abs, bar_ts_utc,
               source, captured_utc
        FROM price_snapshots WHERE release_id = ?
        ORDER BY CASE phase
          WHEN 'PRE' THEN 0 WHEN 'T0' THEN 1 WHEN 'T+5M' THEN 2
          WHEN 'T+30M' THEN 3 WHEN 'T+2H' THEN 4 ELSE 5 END, symbol
        """,
        (release_id,),
    )
    preparations = []
    for row in conn.execute(
        """
        SELECT id, event_key, window_minutes, event_name, currency, period,
               scheduled_release_utc, captured_at_utc, source,
               source_retrieved_at_utc, forecast, previous, revised_previous,
               is_revision, forecast_available_pre_release, data_quality_status,
               missing_fields_json, policy_context_json, fundamental_context_json,
               scenarios_json
        FROM pre_release_preparations
        WHERE release_id = ?
        ORDER BY captured_at_utc DESC
        """,
        (release_id,),
    ).fetchall():
        preparation = dict(row)
        for column, target in (
            ("missing_fields_json", "missing_fields"),
            ("policy_context_json", "policy_context"),
            ("fundamental_context_json", "fundamental_context"),
            ("scenarios_json", "scenarios"),
        ):
            preparation[target] = json.loads(preparation.pop(column))
        preparation["market_snapshots"] = _rows(
            conn,
            """
            SELECT instrument, source_symbol, price, bid, ask, spread, timeframe,
                   source, source_timestamp_utc, captured_at_utc, data_status,
                   is_delayed, source_quality, quality_details_json
            FROM pre_release_market_snapshots
            WHERE event_key = ? AND window_minutes = ?
            ORDER BY instrument
            """,
            (preparation["event_key"], preparation["window_minutes"]),
        )
        for snapshot in preparation["market_snapshots"]:
            snapshot["quality_details"] = json.loads(
                snapshot.pop("quality_details_json")
            )
        preparations.append(preparation)
    score = macro.compute_macro_score(conn, release["country"], 30)
    composition = _release_lights(conn, release, score) if release["actual"] is not None else None
    regime = _row(
        conn, "SELECT country, regime_id, regime_label, tagged_utc "
        "FROM release_regimes WHERE release_id = ?", (release_id,)
    )
    outcomes = _rows(
        conn,
        "SELECT * FROM outcome_labels WHERE release_id = ? ORDER BY symbol",
        (release_id,),
    )
    return {
        "release": release,
        "decision": _decision_brief(conn, release, score),
        "snapshots": phases,
        "preparations": preparations,
        "composition": composition,
        "regime": regime,
        "outcomes": outcomes,
        "sizing_decisions": _rows(
            conn,
            "SELECT * FROM sizing_decisions WHERE release_id = ? ORDER BY computed_utc DESC",
            (release_id,),
        ),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ChartedNewsEngine/1.0"

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            log.debug("Dashboard request closed by client.")

    def log_message(self, fmt: str, *args: object) -> None:
        safe_args = list(args)
        if safe_args:
            request_line = str(safe_args[0]).split()
            if len(request_line) >= 2:
                safe_args[0] = f"{request_line[0]} {urlsplit(request_line[1]).path}"
        log.info("%s - %s", self.address_string(), fmt % tuple(safe_args))

    def _auth_gate(self, path: str) -> bool:
        if path == "/api/health" or not settings.auth_enabled:
            return True
        if not web_auth.is_configured():
            web_auth.reject_unconfigured(self)
            return False
        if not web_auth.is_authenticated(self):
            web_auth.reject_unauthorized(self, api=path.startswith("/api/"))
            return False
        return True

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _get_json(self, path: str, params: dict[str, list[str]]) -> None:
        if path == "/api/health":
            self._json(200, {"status": "ok", "version": __version__})
            return
        conn = connect()
        try:
            if path == "/api/dashboard":
                self._json(200, _dashboard(conn))
            elif path == "/api/calendar":
                country_values = params.get("country", [])
                tier_values = params.get("tier", [])
                country = country_values[0].upper() if country_values else None
                try:
                    tier = int(tier_values[0]) if tier_values and tier_values[0] else None
                except ValueError:
                    self._json(400, {"error": "Tier must be 1, 2, or 3."})
                    return
                if country and country not in COUNTRIES:
                    self._json(400, {"error": "Unsupported country filter."})
                    return
                if tier is not None and tier not in (1, 2, 3):
                    self._json(400, {"error": "Tier must be 1, 2, or 3."})
                    return
                search = params.get("q", [""])[0][:100]
                self._json(
                    200,
                    {
                        "events": _release_query(
                            conn, country=country, tier=tier, query=search, limit=200
                        )
                    },
                )
            elif path == "/api/alerts":
                self._json(
                    200,
                    {
                        "alerts": _rows(
                            conn,
                            """
                            SELECT a.id, a.release_id, a.kind, a.symbol, a.status, a.error,
                                   a.ts_utc, e.name AS event_name, r.period
                            FROM alerts_log a
                            LEFT JOIN releases r ON r.id = a.release_id
                            LEFT JOIN event_definitions e ON e.code = r.event_code
                            ORDER BY a.ts_utc DESC, a.id DESC LIMIT 100
                            """,
                        ),
                        "schedule": _schedule(conn),
                        "scheduler_log": _rows(
                            conn,
                            """
                            SELECT ts_utc, job, release_id, status, detail
                            FROM scheduler_log ORDER BY ts_utc DESC, id DESC LIMIT 100
                            """,
                        ),
                    },
                )
            elif path == "/api/learning":
                self._json(
                    200,
                    {
                        "edges": _rows(
                            conn,
                            "SELECT * FROM edge_stats ORDER BY n_samples DESC LIMIT 100",
                        ),
                        "regime_edges": _rows(
                            conn,
                            "SELECT * FROM edge_stats_regime ORDER BY n_samples DESC LIMIT 100",
                        ),
                        "outcomes": _rows(
                            conn,
                            """
                            SELECT o.*, r.event_code, r.period, e.name AS event_name
                            FROM outcome_labels o JOIN releases r ON r.id = o.release_id
                            JOIN event_definitions e ON e.code = r.event_code
                            ORDER BY o.computed_utc DESC LIMIT 100
                            """,
                        ),
                        "backtests": _rows(
                            conn,
                            "SELECT * FROM backtest_runs ORDER BY computed_utc DESC LIMIT 100",
                        ),
                        "rules": _rows(conn, "SELECT * FROM backtest_rules ORDER BY name"),
                    },
                )
            elif path == "/api/system":
                dashboard = _dashboard(conn)
                self._json(
                    200,
                    {
                        "system": dashboard["system"],
                        "counts": dashboard["counts"],
                        "layers": dashboard["layers"],
                        "schedule": dashboard["schedule"],
                    },
                )
            elif path == "/api/simulations":
                self._json(
                    200,
                    {
                        "specs": simulator.INSTRUMENTS,
                        "simulations": _rows(
                            conn,
                            "SELECT * FROM trade_simulations ORDER BY ts_utc DESC LIMIT 100",
                        ),
                    },
                )
            elif path.startswith("/api/releases/"):
                release_id = path.removeprefix("/api/releases/")
                if not release_id.isdecimal():
                    self._json(400, {"error": "Release id must be a positive integer."})
                    return
                detail = _release_detail(conn, int(release_id))
                if detail is None:
                    self._json(404, {"error": "Release not found."})
                    return
                self._json(200, detail)
            else:
                self._json(404, {"error": "API route not found."})
        finally:
            conn.close()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if settings.auth_enabled and path in (
            "/auth/login",
            "/auth/callback",
            "/auth/logout",
        ):
            try:
                if path == "/auth/login":
                    web_auth.begin_login(self)
                elif path == "/auth/callback":
                    web_auth.finish_login(self, parse_qs(parsed.query))
                else:
                    web_auth.logout(self)
            except web_auth.OAuthError:
                log.exception("Google OAuth flow failed.")
                web_auth.reject_oauth_error(self)
            return
        if not self._auth_gate(path):
            return
        if path == "/api/events":
            self._stream_events()
            return
        if path.startswith("/api/"):
            try:
                self._get_json(path, parse_qs(parsed.query))
            except (ValueError, *DB_ERRORS) as exc:
                log.exception("Dashboard request failed for %s", path)
                self._json(500, {"error": "Dashboard data request failed.", "detail": str(exc)})
            return
        self._serve_frontend(path)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not self._auth_gate(path):
            return
        if path not in ("/api/simulate", "/api/collect"):
            self._json(404, {"error": "API route not found."})
            return
        origin = self.headers.get("Origin")
        origin_host = urlsplit(origin).hostname if origin else None
        request_host = urlsplit(f"//{self.headers.get('Host', '')}").hostname
        if origin and origin_host != request_host:
            self._json(403, {"error": "Cross-origin requests are not allowed."})
            return
        if settings.auth_enabled and (
            not origin or urlsplit(origin).scheme != "https" or origin_host != request_host
        ):
            self._json(403, {"error": "A same-origin HTTPS request is required."})
            return
        if path == "/api/collect":
            conn = connect()
            try:
                seed(conn)
                runs = collect_all(conn)
                self._json(
                    200,
                    {
                        "runs": runs,
                        "collection": collection_pipeline.coverage(conn),
                    },
                )
            finally:
                conn.close()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "Invalid content length."})
            return
        if length < 1 or length > 4096:
            self._json(413, {"error": "Request body must be between 1 and 4096 bytes."})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "Request body must be valid JSON."})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": "Request body must be a JSON object."})
            return

        try:
            symbol = body["symbol"]
            if not isinstance(symbol, str):
                raise TypeError("symbol must be a string")
            numeric_fields = ("balance", "risk_pct", "entry", "stop")
            values = {}
            for field in numeric_fields:
                value = body[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError(f"{field} must be a number")
                if not math.isfinite(value):
                    raise ValueError(f"{field} must be finite")
                values[field] = float(value)
            target = body.get("target")
            if target is not None:
                if isinstance(target, bool) or not isinstance(target, (int, float)):
                    raise TypeError("target must be a number")
                if not math.isfinite(target):
                    raise ValueError("target must be finite")
                target = float(target)
            direction = body.get("direction")
            if direction is not None and not isinstance(direction, str):
                raise TypeError("direction must be a string")
            save = body.get("save", False)
            if not isinstance(save, bool):
                raise TypeError("save must be a boolean")
            release_id = body.get("release_id")
            if release_id is not None and (
                isinstance(release_id, bool) or not isinstance(release_id, int)
            ):
                raise TypeError("release_id must be an integer")
            if release_id is not None and release_id < 1:
                raise ValueError("release_id must be a positive integer")
        except (KeyError, TypeError, ValueError) as exc:
            self._json(400, {"error": f"Invalid simulation input: {exc}"})
            return

        result = simulator.calculate(
            symbol, values["balance"], values["risk_pct"], values["entry"],
            values["stop"], target, direction=direction,
        )
        if result.get("error"):
            self._json(422, {"error": result["error"]})
            return
        if save:
            conn = connect()
            try:
                result["simulation_id"] = simulator.save(
                    conn, result, release_id=release_id
                )
            finally:
                conn.close()
        self._json(200, {"result": result, "saved": save})

    def _stream_events(self) -> None:
        conn = connect()
        try:
            last_data_version = data_version(conn)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(
                f"event: connected\ndata: {json.dumps({'version': __version__})}\n\n".encode()
            )
            self.wfile.flush()
            last_heartbeat = time.monotonic()
            while True:
                readable, _, _ = select.select([self.connection], [], [], 2)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    break
                current_version = data_version(conn)
                if current_version != last_data_version:
                    last_data_version = current_version
                    payload = json.dumps(
                        {"updated_at": datetime.now(timezone.utc).isoformat()}
                    )
                    self.wfile.write(f"event: engine_update\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
                elif time.monotonic() - last_heartbeat >= 15:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_heartbeat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log.debug("Dashboard event stream closed by client.")
        finally:
            conn.close()

    def _serve_frontend(self, path: str) -> None:
        if not STATIC_ROOT.is_dir():
            self._json(
                503,
                {
                    "error": (
                        "The production dashboard has not been built. "
                        "Run `npm run build` in the frontend directory."
                    )
                },
            )
            return
        candidate = (STATIC_ROOT / unquote(path.lstrip("/"))).resolve()
        if not candidate.is_relative_to(STATIC_ROOT.resolve()):
            self._json(404, {"error": "File not found."})
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            candidate = STATIC_ROOT / "index.html"
        if not candidate.is_file():
            self._json(404, {"error": "Dashboard build is incomplete."})
            return
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(body)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int = 8000, host: str | None = None) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("Dashboard port must be between 1 and 65535.")
    conn = init_db()
    try:
        seed(conn)
    finally:
        conn.close()
    bind_host = host or settings.dashboard_host
    server = DashboardServer((bind_host, port), DashboardHandler)
    log.info(
        "Charted News Engine dashboard listening at http://%s:%d",
        bind_host,
        port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Dashboard server stopped.")
    finally:
        server.server_close()
