"""Reconcile published results with scheduled releases and frozen forecasts."""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone

from cne.config import settings
from cne.core.events import BY_CODE
from cne.core.surprise import compute_surprise, persist

_OFFICIAL_INDICATORS = {
    "US_CPI_YOY": ("US_CPI_YOY",),
    "US_CPI_MOM": ("US_CPI_MOM",),
    "US_CORE_CPI_YOY": ("US_CORE_CPI_YOY",),
    "US_CORE_CPI_MOM": ("US_CORE_CPI_MOM",),
    "US_PPI_YOY": ("US_PPI_YOY",),
    "US_CORE_PPI_YOY": ("US_CORE_PPI_YOY",),
    "US_NFP": ("US_NFP",),
    "US_UNEMPLOYMENT": ("US_UNEMPLOYMENT",),
    "US_AHE_MOM": ("US_AHE_MOM",),
    "US_GDP_QOQ": ("US_GDP_QOQ", "US_GDP_QOQ_BEA"),
    "EU_CPI_YOY": ("EU_CPI_YOY",),
    "EU_GDP_QOQ": ("EU_GDP_QOQ",),
    "EU_EMPLOYMENT_QOQ": ("EU_EMPLOYMENT_QOQ",),
}
_OFFICIAL_SOURCES = (
    "BLS Public Data API",
    "U.S. Bureau of Economic Analysis",
    "Eurostat public data API",
    "Federal Reserve Bank of St. Louis",
)
_FORECAST_SOURCES = ("BiQuote economic calendar", "FinanceCalendar.com")
_MAX_PRE_RELEASE_FORECAST_AGE = timedelta(minutes=15)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _official_actual(conn, event_code: str, period: str) -> dict | None:
    indicators = _OFFICIAL_INDICATORS.get(event_code, ())
    if not indicators:
        return None
    placeholders = ",".join("?" for _ in indicators)
    rows = conn.execute(
        f"""
        SELECT id, indicator, value_numeric, unit, source, source_url,
               observed_utc, published_utc, fetched_utc, publication_status,
               metadata_json
        FROM data_observations
        WHERE domain = 'economic' AND indicator IN ({placeholders})
          AND observation_period = ? AND value_numeric IS NOT NULL
          AND publication_status IN ('PUBLISHED', 'RELEASED', 'REVISED')
        ORDER BY fetched_utc DESC, id DESC
        """,
        (*indicators, period),
    ).fetchall()
    for row in rows:
        if row["source"].startswith(_OFFICIAL_SOURCES):
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            return item
    return None


def _forecast_at_release(conn, release: dict) -> dict | None:
    event_time = _parse_time(release["release_time_utc"])
    if event_time is None:
        return None
    frozen = conn.execute(
        """
        SELECT forecast_at_release, forecast_source, forecast_retrieved_at_utc
        FROM release_monitor_results
        WHERE release_id = ? AND forecast_at_release IS NOT NULL
        """,
        (release["id"],),
    ).fetchone()
    if frozen:
        timestamp = _parse_time(frozen["forecast_retrieved_at_utc"])
        return {
            "value": frozen["forecast_at_release"],
            "source": frozen["forecast_source"],
            "retrieved_at": frozen["forecast_retrieved_at_utc"],
            "age_at_release": event_time - timestamp if timestamp else None,
        }

    candidates = []
    snapshots = conn.execute(
        """
        SELECT forecast, source, source_retrieved_at_utc, captured_at_utc
        FROM pre_release_preparations
        WHERE release_id = ? AND forecast_available_pre_release = 1
          AND forecast IS NOT NULL AND captured_at_utc < ?
        ORDER BY captured_at_utc DESC LIMIT 1
        """,
        (release["id"], event_time.isoformat()),
    ).fetchall()
    for snapshot in snapshots:
        timestamp_text = (
            snapshot["source_retrieved_at_utc"] or snapshot["captured_at_utc"]
        )
        timestamp = _parse_time(timestamp_text)
        if (
            timestamp
            and timestamp < event_time
            and str(snapshot["source"] or "").startswith(_FORECAST_SOURCES)
        ):
            candidates.append(
                {
                    "value": snapshot["forecast"],
                    "source": snapshot["source"],
                    "retrieved_at": timestamp_text,
                    "age_at_release": event_time - timestamp,
                }
            )

    row = conn.execute(
        """
        SELECT value_numeric, source, fetched_utc, metadata_json
        FROM data_observations
        WHERE domain = 'economic' AND indicator = ?
          AND observation_period = ? AND publication_status = 'PRE_RELEASE_FORECAST'
          AND value_numeric IS NOT NULL AND fetched_utc < ?
        ORDER BY fetched_utc DESC, id DESC LIMIT 1
        """,
        (
            f"{release['event_code']}_FORECAST",
            release["period"],
            event_time.isoformat(),
        ),
    ).fetchone()
    if row:
        metadata = json.loads(row["metadata_json"] or "{}")
        timestamp = _parse_time(row["fetched_utc"])
        if (
            metadata.get("forecast_known_pre_release") is not False
            and str(row["source"] or "").startswith(_FORECAST_SOURCES)
            and timestamp
            and timestamp < event_time
        ):
            candidates.append(
                {
                    "value": row["value_numeric"],
                    "source": row["source"],
                    "retrieved_at": row["fetched_utc"],
                    "age_at_release": event_time - timestamp,
                }
            )
    return max(
        candidates,
        key=lambda item: _parse_time(item["retrieved_at"]) or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )


def _previous_at_release(conn, release: dict) -> dict | None:
    event_time = _parse_time(release["release_time_utc"])
    if event_time is None:
        return None
    snapshot = conn.execute(
        """
        SELECT previous, revised_previous, is_revision, source,
               source_retrieved_at_utc, captured_at_utc
        FROM pre_release_preparations
        WHERE release_id = ? AND captured_at_utc < ?
        ORDER BY captured_at_utc DESC LIMIT 1
        """,
        (release["id"], event_time.isoformat()),
    ).fetchone()
    if snapshot:
        return {
            **dict(snapshot),
            "retrieved_at": snapshot["source_retrieved_at_utc"]
            or snapshot["captured_at_utc"],
        }
    row = conn.execute(
        """
        SELECT value_numeric, source, fetched_utc, metadata_json
        FROM data_observations
        WHERE domain = 'economic' AND indicator = ?
          AND observation_period = ? AND publication_status = 'PUBLISHED'
          AND value_numeric IS NOT NULL AND fetched_utc < ?
        ORDER BY fetched_utc DESC, id DESC LIMIT 1
        """,
        (
            f"{release['event_code']}_PREVIOUS",
            release["period"],
            event_time.isoformat(),
        ),
    ).fetchone()
    if not row:
        return None
    metadata = json.loads(row["metadata_json"] or "{}")
    return {
        "previous": row["value_numeric"],
        "revised_previous": metadata.get("revised_previous"),
        "is_revision": metadata.get("is_revision", False),
        "source": row["source"],
        "retrieved_at": row["fetched_utc"],
    }


def _historical_scale(conn, event_code: str, release_time_utc: str | None) -> tuple[float | None, int]:
    minimum = settings.surprise_normalization_min_samples
    rows = conn.execute(
        """
        SELECT result.raw_surprise
        FROM release_monitor_results AS result
        JOIN releases AS release ON release.id = result.release_id
        WHERE release.event_code = ? AND result.data_status = 'VERIFIED'
          AND result.raw_surprise IS NOT NULL AND release_time_utc < ?
        ORDER BY release_time_utc DESC LIMIT 120
        """,
        (event_code, release_time_utc or ""),
    ).fetchall()
    values = [row["raw_surprise"] for row in rows]
    if len(values) < minimum:
        return None, len(values)
    scale = statistics.stdev(values)
    return (scale if scale > 1e-12 else None), len(values)


def process_releases(conn) -> int:
    """Reconcile known actuals; score only official actuals and pre-release forecasts."""
    now = datetime.now(timezone.utc).isoformat()
    releases = conn.execute(
        """
        SELECT id, event_code, period, release_time_utc, previous, forecast,
               actual, revised_previous, actual_source, forecast_source, updated_utc
        FROM releases
        WHERE actual IS NOT NULL OR release_time_utc <= ?
        ORDER BY release_time_utc, id
        """,
        (now,),
    ).fetchall()
    classified = 0
    for row in releases:
        release = dict(row)
        scheduled_time = _parse_time(release["release_time_utc"])
        if scheduled_time is None or scheduled_time > datetime.now(timezone.utc):
            continue
        event = BY_CODE.get(release["event_code"])
        if event is None:
            continue
        official = _official_actual(conn, release["event_code"], release["period"])
        forecast = _forecast_at_release(conn, release)
        previous = _previous_at_release(conn, release)
        missing = []
        actual = official["value_numeric"] if official else release["actual"]
        if actual is None:
            missing.append("ACTUAL")
        if forecast is None:
            missing.append("FORECAST_KNOWN_BEFORE_RELEASE")
        elif (
            forecast.get("age_at_release") is None
            or forecast["age_at_release"] > _MAX_PRE_RELEASE_FORECAST_AGE
        ):
            missing.append("FRESH_PRE_RELEASE_FORECAST")
        if previous is None or previous.get("previous") is None:
            missing.append("PREVIOUS_VALUE")
        if official is None:
            missing.append("OFFICIAL_ACTUAL_SOURCE")

        actual_source = official["source"] if official else release["actual_source"]
        retrieved = official["fetched_utc"] if official else None
        published = official["published_utc"] if official else None
        metadata = official["metadata"] if official else {}
        is_revision = bool(
            metadata.get("is_revision")
            or release["revised_previous"] is not None
            or (previous and previous.get("is_revision"))
        )
        prior_result = conn.execute(
            "SELECT actual_value FROM release_monitor_results WHERE release_id = ?",
            (release["id"],),
        ).fetchone()
        if prior_result and prior_result["actual_value"] != actual:
            is_revision = True

        forecast_is_fresh = bool(
            forecast
            and forecast.get("age_at_release") is not None
            and forecast["age_at_release"] <= _MAX_PRE_RELEASE_FORECAST_AGE
        )
        status = (
            "VERIFIED"
            if actual is not None and forecast_is_fresh and official
            else "INCOMPLETE"
        )
        result = {
            "raw_surprise": None,
            "normalized_surprise": None,
            "historical_scale": None,
            "normalization_n": 0,
            "normalization_status": "UNAVAILABLE_INPUTS",
            "economic_direction": None,
            "currency_impact": None,
        }
        if status == "VERIFIED":
            scale, sample_count = _historical_scale(
                conn, release["event_code"], release["release_time_utc"]
            )
            result = compute_surprise(
                release["event_code"], forecast["value"], actual, scale
            )
            result["normalization_n"] = sample_count
            result["normalization_status"] = (
                "AVAILABLE" if result["normalized_surprise"] is not None
                else "INSUFFICIENT_HISTORY" if sample_count < settings.surprise_normalization_min_samples
                else "ZERO_HISTORICAL_VARIABILITY"
            )
            result["data_status"] = status
            if "error" in result:
                status = "INCOMPLETE"
                missing.append("SURPRISE_VALIDATION")
                result = {
                    "raw_surprise": None,
                    "normalized_surprise": None,
                    "historical_scale": None,
                    "normalization_n": sample_count,
                    "normalization_status": "UNAVAILABLE_INPUTS",
                    "economic_direction": None,
                    "currency_impact": None,
                }
            else:
                result["currency_impact"] = result["impact"]

        unit = official["unit"] if official else event.unit
        source_record_id = None
        if official:
            source_record_id = metadata.get("source_record_id") or (
                f"data_observations:{official['id']}"
            )
            conn.execute(
                """
                UPDATE releases SET actual = ?, actual_source = ?, updated_utc = ?
                WHERE id = ?
                """,
                (actual, actual_source, now, release["id"]),
            )
        details = {
            "missing_fields": missing,
            "actual_verified": official is not None,
            "forecast_verified_before_release": forecast is not None,
            "publication_timestamp_available": published is not None,
            "normalization_method": (
                "sample standard deviation of earlier verified raw surprises "
                "for this event code"
            ),
        }
        conn.execute(
            """
            INSERT INTO release_monitor_results
              (release_id, actual_value, forecast_at_release, previous_value,
               revised_previous, unit, reporting_period, actual_source,
               source_record_id, publication_timestamp_utc, retrieved_at_utc,
               forecast_source, forecast_retrieved_at_utc, previous_source,
               previous_retrieved_at_utc, is_revision,
               data_status, status_details_json, raw_surprise,
               normalized_surprise, historical_scale, normalization_n,
               normalization_status, economic_direction, currency_impact,
               updated_at_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(release_id) DO UPDATE SET
              actual_value=excluded.actual_value,
              forecast_at_release=COALESCE(release_monitor_results.forecast_at_release,
                                            excluded.forecast_at_release),
              previous_value=COALESCE(release_monitor_results.previous_value,
                                      excluded.previous_value),
              revised_previous=COALESCE(release_monitor_results.revised_previous,
                                         excluded.revised_previous),
              unit=excluded.unit, reporting_period=excluded.reporting_period,
              actual_source=excluded.actual_source,
              source_record_id=excluded.source_record_id,
              publication_timestamp_utc=excluded.publication_timestamp_utc,
              retrieved_at_utc=excluded.retrieved_at_utc,
              forecast_source=COALESCE(release_monitor_results.forecast_source,
                                       excluded.forecast_source),
              forecast_retrieved_at_utc=COALESCE(
                  release_monitor_results.forecast_retrieved_at_utc,
                  excluded.forecast_retrieved_at_utc),
              previous_source=COALESCE(release_monitor_results.previous_source,
                                        excluded.previous_source),
              previous_retrieved_at_utc=COALESCE(
                  release_monitor_results.previous_retrieved_at_utc,
                  excluded.previous_retrieved_at_utc),
              is_revision=CASE
                WHEN release_monitor_results.is_revision > excluded.is_revision
                THEN release_monitor_results.is_revision ELSE excluded.is_revision END,
              data_status=excluded.data_status,
              status_details_json=excluded.status_details_json,
              raw_surprise=excluded.raw_surprise,
              normalized_surprise=excluded.normalized_surprise,
              historical_scale=excluded.historical_scale,
              normalization_n=excluded.normalization_n,
              normalization_status=excluded.normalization_status,
              economic_direction=excluded.economic_direction,
              currency_impact=excluded.currency_impact,
              updated_at_utc=excluded.updated_at_utc
            """,
            (
                release["id"],
                actual,
                forecast["value"] if forecast else None,
                previous.get("previous") if previous else None,
                previous.get("revised_previous") if previous else release["revised_previous"],
                unit,
                release["period"],
                actual_source,
                source_record_id,
                published,
                retrieved,
                forecast["source"] if forecast else None,
                forecast["retrieved_at"] if forecast else None,
                previous.get("source") if previous else None,
                previous.get("retrieved_at") if previous else None,
                int(is_revision),
                status,
                _json(details),
                result["raw_surprise"],
                result["normalized_surprise"],
                result["historical_scale"],
                result["normalization_n"],
                result["normalization_status"],
                result["economic_direction"],
                result["currency_impact"],
                now,
            ),
        )
        if status == "VERIFIED":
            persist(
                conn,
                release["id"],
                {
                    **result,
                    "data_status": status,
                    "normalization_status": result["normalization_status"],
                },
            )
            classified += 1
        else:
            conn.execute("DELETE FROM surprises WHERE release_id = ?", (release["id"],))
    conn.commit()
    return classified
