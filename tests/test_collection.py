import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from cne.collectors import (
    alpha_vantage,
    bea,
    biquote,
    biquote_calendar,
    bls,
    calendar,
    ecb_public,
    eurostat,
    finance_calendar,
    fred,
    gold_api,
    news,
    observations,
    pipeline,
    prices,
    snapshots,
    treasury,
)
from cne.core import market
from cne.core.events import seed
from cne.db import database
from cne.scheduler import preparation
from cne.scheduler.planner import build_preparation_schedule


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_settings = database.settings
        self.original_calendar_settings = calendar.settings
        self.original_alpha_vantage_settings = alpha_vantage.settings
        self.original_fred_settings = fred.settings
        self.original_bea_settings = bea.settings
        root = Path(self.temp_dir.name)
        self.settings = replace(
            database.settings,
            data_dir=root,
            db_path=root / "test.db",
            calendar_csv=root / "calendar.csv",
        )
        database.settings = self.settings
        calendar.settings = self.settings
        self.conn = database.init_db()
        seed(self.conn)

    def tearDown(self):
        self.conn.close()
        database.settings = self.original_db_settings
        calendar.settings = self.original_calendar_settings
        alpha_vantage.settings = self.original_alpha_vantage_settings
        fred.settings = self.original_fred_settings
        bea.settings = self.original_bea_settings
        self.temp_dir.cleanup()

    def test_calendar_forecast_records_source_unit_and_publication_status(self):
        self.settings.calendar_csv.write_text(
            "event_code,period,forecast,previous,release_time_utc,consensus_source\n"
            "US_CPI_YOY,2026-09,3.1,2.9,2026-10-20T12:30:00Z,Survey consensus\n"
        )

        self.assertEqual(calendar.collect(self.conn), 2)

        observation = self.conn.execute(
            """
            SELECT value_numeric, unit, source, observed_utc, published_utc,
                   fetched_utc, publication_status
            FROM data_observations
            WHERE indicator = 'US_CPI_YOY_FORECAST'
            """
        ).fetchone()
        self.assertEqual(observation["value_numeric"], 3.1)
        self.assertEqual(observation["unit"], "%")
        self.assertEqual(observation["source"], "Survey consensus")
        self.assertEqual(observation["published_utc"], None)
        self.assertEqual(observation["publication_status"], "USER_ENTERED_FORECAST")
        self.assertTrue(observation["observed_utc"].startswith("2026-10-20"))
        self.assertIsNotNone(observation["fetched_utc"])

    def test_calendar_rejects_unknown_events_and_invalid_numeric_values(self):
        self.settings.calendar_csv.write_text(
            "event_code,period,forecast\nUNKNOWN,2026-09,3.1\n"
        )
        with self.assertRaisesRegex(ValueError, "unknown event_code"):
            calendar.collect(self.conn)
        self.settings.calendar_csv.write_text(
            "event_code,period,forecast\nUS_CPI_YOY,2026-09,high\n"
        )
        with self.assertRaisesRegex(ValueError, "invalid forecast"):
            calendar.collect(self.conn)
        self.settings.calendar_csv.write_text(
            "event_code,period,forecast\nUS_CPI_YOY,2026-09,inf\n"
        )
        with self.assertRaisesRegex(ValueError, "must be finite"):
            calendar.collect(self.conn)

    def test_bls_monthly_parser_skips_annual_average_period(self):
        rows = bls._to_monthly([
            {"year": "2025", "period": "M01", "value": "100"},
            {"year": "2025", "period": "M13", "value": "150"},
        ])
        self.assertEqual(rows, [("2025-01", 100.0)])
        self.assertEqual(
            observations.period_timestamp("2026-Q1"),
            "2026-01-01T00:00:00+00:00",
        )

    def test_bls_public_request_does_not_send_registration_key(self):
        response = Mock(
            json=Mock(
                return_value={
                    "status": "REQUEST_SUCCEEDED",
                    "Results": {"series": []},
                }
            )
        )
        with patch.object(bls.http, "request", return_value=response) as request:
            self.assertEqual(bls._post(["CUSR0000SA0"], 2025, 2026), [])
        self.assertEqual(request.call_args.args[:2], ("POST", bls.BLS_URL))
        self.assertNotIn("registrationkey", request.call_args.kwargs["json_body"])

    def test_alpha_vantage_stores_quotes_and_daily_ohlc_without_claiming_intraday(self):
        key = "test-secret"
        alpha_vantage.settings = replace(
            alpha_vantage.settings,
            alpha_vantage_api_key=key,
        )
        gold = {
            "symbol": "XAU",
            "price": "3850.25",
            "currency": "USD",
            "last_updated": "2026-10-18 09:00:00",
        }
        exchange = {
            "Realtime Currency Exchange Rate": {
                "5. Exchange Rate": "1.1650",
                "6. Last Refreshed": "2026-10-18 09:05:00",
                "7. Time Zone": "UTC",
                "8. Bid Price": "1.1649",
                "9. Ask Price": "1.1651",
            }
        }
        candles = {
            "Time Series FX (Daily)": {
                "2026-10-16": {
                    "1. open": "1.1600",
                    "2. high": "1.1700",
                    "3. low": "1.1550",
                    "4. close": "1.1650",
                }
            }
        }
        with patch.object(
            alpha_vantage,
            "_request",
            side_effect=[gold, exchange, candles],
        ) as request:
            self.assertEqual(alpha_vantage.collect(self.conn), 3)

        self.assertEqual(request.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            ["GOLD_SILVER_SPOT", "CURRENCY_EXCHANGE_RATE", "FX_DAILY"],
        )
        gold_observation = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='XAUUSD_ALPHA_SPOT'"
        ).fetchone()
        self.assertEqual(gold_observation["value_numeric"], 3850.25)
        self.assertNotIn(key, gold_observation["source_url"])
        self.assertNotIn(key, gold_observation["metadata_json"])

        quote = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='EURUSD_ALPHA_SPOT'"
        ).fetchone()
        self.assertEqual(quote["value_numeric"], 1.165)
        self.assertNotIn(key, quote["source_url"])
        self.assertAlmostEqual(json.loads(quote["metadata_json"])["spread"], 0.0002)
        daily = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='EURUSD_ALPHA_DAILY_CLOSE'"
        ).fetchone()
        daily_metadata = json.loads(daily["metadata_json"])
        self.assertEqual(daily_metadata["timeframe"], "daily")
        self.assertFalse(daily_metadata["intraday_confirmation_eligible"])
        self.assertEqual(daily_metadata["high"], 1.17)

    def test_alpha_vantage_api_key_is_redacted_from_provider_errors_and_not_in_coverage(self):
        key = "alpha-secret"
        alpha_vantage.settings = replace(
            alpha_vantage.settings,
            alpha_vantage_api_key=key,
        )
        with patch.object(
            alpha_vantage.http,
            "request",
            side_effect=RuntimeError(f"failed https://example.invalid/?apikey={key}"),
        ):
            result = pipeline._run_source(
                self.conn,
                "alpha_vantage",
                alpha_vantage.collect,
            )
        self.assertEqual(result["status"], "ERROR")
        self.assertNotIn(key, result["error"])
        self.assertIn("[REDACTED]", result["error"])
        coverage = pipeline.coverage(self.conn)
        source = next(item for item in coverage["sources"] if item["id"] == "alpha_vantage")
        self.assertNotIn(key, json.dumps(source))

    def test_alpha_vantage_collection_is_scheduled_only_when_configured(self):
        with (
            patch.object(
                pipeline,
                "settings",
                replace(pipeline.settings, alpha_vantage_api_key="configured"),
            ),
            patch.object(biquote_calendar, "collect", return_value=0),
            patch.object(pipeline.finance_calendar, "collect", return_value=0),
            patch.object(pipeline.bls, "collect", return_value=0),
            patch.object(pipeline.treasury, "collect", return_value=0),
            patch.object(pipeline.eurostat, "collect", return_value=0),
            patch.object(pipeline.ecb_public, "collect_policy_rate", return_value=0),
            patch.object(pipeline.ecb_public, "collect_fx_reference", return_value=0),
            patch.object(pipeline.gold_api, "collect", return_value=0),
            patch.object(pipeline.news, "collect", return_value=0),
            patch.object(biquote, "collect", return_value=3) as collect_biquote,
            patch.object(pipeline.alpha_vantage, "collect", return_value=3) as collect_alpha,
            patch.object(pipeline, "refresh_analysis", return_value=0),
        ):
            runs = pipeline.collect_all(self.conn)

        collect_alpha.assert_called_once_with(self.conn)
        collect_biquote.assert_called_once_with(self.conn)
        biquote_run = next(run for run in runs if run["source"] == "biquote")
        self.assertEqual(biquote_run["status"], "OK")
        self.assertEqual(biquote_run["records"], 3)
        alpha_run = next(run for run in runs if run["source"] == "alpha_vantage")
        self.assertEqual(alpha_run["status"], "OK")
        self.assertEqual(alpha_run["records"], 3)

    def test_calendar_api_failure_uses_financecalendar_fallback(self):
        with (
            patch.object(
                biquote_calendar,
                "collect",
                side_effect=RuntimeError("calendar endpoint unavailable"),
            ),
            patch.object(pipeline.finance_calendar, "collect", return_value=4) as fallback,
            patch.object(pipeline.bls, "collect", return_value=0),
            patch.object(pipeline.treasury, "collect", return_value=0),
            patch.object(pipeline.eurostat, "collect", return_value=0),
            patch.object(pipeline.ecb_public, "collect_policy_rate", return_value=0),
            patch.object(pipeline.ecb_public, "collect_fx_reference", return_value=0),
            patch.object(pipeline.gold_api, "collect", return_value=0),
            patch.object(pipeline.news, "collect", return_value=0),
            patch.object(biquote, "collect", return_value=0),
            patch.object(pipeline, "refresh_analysis", return_value=0),
        ):
            runs = pipeline.collect_all(self.conn)

        fallback.assert_called_once_with(self.conn)
        primary = next(run for run in runs if run["source"] == "biquote_calendar")
        finance = next(run for run in runs if run["source"] == "financecalendar")
        self.assertEqual(primary["status"], "ERROR")
        self.assertEqual(finance["status"], "FALLBACK")
        self.assertEqual(finance["records"], 4)

    def test_biquote_fetches_and_persists_quotes_with_freshness_and_provenance(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        quote = {
            "symbol": "EURUSD",
            "bid": 1.1,
            "ask": 1.1002,
            "mid": 1.1001,
            "spread": 0.0002,
            "high": 1.11,
            "low": 1.09,
            "timestamp": timestamp,
            "quoteAgeSeconds": 30,
            "marketState": "open",
            "stale": False,
            "dayDiffPercent": 0.12,
            "description": "Euro vs US Dollar",
            "exchange": "FOREX",
            "source": "Broker feed",
        }
        response = Mock(json=Mock(return_value={"EURUSD": quote}))
        with patch.object(biquote.http, "request", return_value=response) as request:
            self.assertEqual(biquote.collect(self.conn), 1)

        self.assertEqual(request.call_args.args[:2], ("GET", biquote.LATEST_URL))
        self.assertEqual(
            request.call_args.kwargs["params"]["symbols"],
            ["DXY", "XAUUSD", "EURUSD"],
        )
        self.assertEqual(
            request.call_args.kwargs["params"]["allowStale"],
            "true",
        )
        observation = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='EURUSD_BIQUOTE_SPOT'"
        ).fetchone()
        self.assertEqual(observation["value_numeric"], 1.1001)
        self.assertEqual(observation["observed_utc"], timestamp)
        metadata = json.loads(observation["metadata_json"])
        self.assertEqual(metadata["source_timestamp_utc"], timestamp)
        self.assertEqual(metadata["data_status"], "LIVE")
        self.assertTrue(metadata["intraday_confirmation_eligible"])
        self.assertEqual(metadata["bid"], 1.1)
        self.assertEqual(metadata["ask"], 1.1002)
        self.assertIsNone(metadata["volume"])

    def test_biquote_marks_closed_old_quotes_not_eligible_for_confirmation(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        body = {
            "DXY": {
                "symbol": "DXY",
                "bid": 100.0,
                "ask": 100.1,
                "mid": 100.05,
                "spread": 0.1,
                "high": 101.0,
                "low": 99.0,
                "timestamp": timestamp,
                "quoteAgeSeconds": 7200,
                "marketState": "closed",
                "stale": True,
            }
        }
        with patch.object(
            biquote.http,
            "request",
            return_value=Mock(json=Mock(return_value=body)),
        ):
            self.assertEqual(biquote.collect(self.conn), 1)

        row = self.conn.execute(
            "SELECT publication_status, metadata_json FROM data_observations "
            "WHERE indicator='DXY_BIQUOTE_SPOT'"
        ).fetchone()
        self.assertEqual(row["publication_status"], "STALE")
        metadata = json.loads(row["metadata_json"])
        self.assertFalse(metadata["intraday_confirmation_eligible"])
        self.assertTrue(metadata["is_delayed"])

    def test_biquote_ohlc_normalizes_closed_bars_and_omits_in_progress_candle(self):
        closed_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(
            second=0, microsecond=0
        )
        open_time = closed_time + timedelta(minutes=5)
        response = Mock(
            json=Mock(
                return_value={
                    "symbol": "EURUSD",
                    "interval": "5m",
                    "bars": [
                        {
                            "openTime": open_time.isoformat(),
                            "open": 1.1,
                            "high": 1.2,
                            "low": 1.0,
                            "close": 1.15,
                            "volume": 0,
                            "tickVolume": 5,
                            "isOpen": True,
                        },
                        {
                            "openTime": closed_time.isoformat(),
                            "open": 1.1,
                            "high": 1.2,
                            "low": 1.0,
                            "close": 1.15,
                            "volume": 0,
                            "tickVolume": 7,
                            "isOpen": False,
                        },
                    ],
                }
            )
        )
        with patch.object(biquote.http, "request", return_value=response) as request:
            frame = biquote.fetch_ohlc("EURUSD", period="1h", interval="5m")

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["Close"], 1.15)
        self.assertEqual(frame.iloc[0]["TickVolume"], 7)
        self.assertEqual(request.call_args.kwargs["params"]["interval"], "5m")
        self.assertEqual(request.call_args.kwargs["params"]["limit"], "1000")

    def test_biquote_does_not_claim_intraday_treasury_yields(self):
        with self.assertRaisesRegex(RuntimeError, "does not provide a verified"):
            prices.fetch_ohlc("US2Y", interval="5m")

    def test_fred_requests_use_api_key_parameter_without_persisting_it(self):
        key = "fred-test-secret"
        fred.settings = replace(fred.settings, fred_api_key=key)
        body = {
            "observations": [
                {
                    "date": "2026-04-01",
                    "value": "2.1",
                    "realtime_start": "2026-07-30",
                    "realtime_end": "9999-12-31",
                },
                {
                    "date": "2026-07-01",
                    "value": ".",
                    "realtime_start": "2026-10-30",
                    "realtime_end": "9999-12-31",
                },
            ]
        }
        response = Mock(json=Mock(return_value=body))
        with patch.object(fred.http, "request", return_value=response) as request:
            result, url = fred._request("A191RL1Q225SBEA")

        self.assertEqual(result, body)
        self.assertEqual(request.call_args.args[:2], ("GET", fred.OBSERVATIONS_URL))
        self.assertEqual(request.call_args.kwargs["params"]["api_key"], key)
        self.assertNotIn(key, url)
        self.assertEqual(request.call_args.kwargs["params"]["series_id"], "A191RL1Q225SBEA")
        self.assertEqual(request.call_args.kwargs["params"]["offset"], "0")
        self.assertIn("observation_end", request.call_args.kwargs["params"])

    def test_fred_observations_follow_provider_pagination(self):
        key = "fred-test-secret"
        fred.settings = replace(fred.settings, fred_api_key=key)
        with (
            patch.object(fred, "PAGE_LIMIT", 2),
            patch.object(
                fred.http,
                "request",
                side_effect=[
                    Mock(json=Mock(return_value={
                        "count": 3,
                        "offset": 0,
                        "observations": [{"date": "2026-01-01", "value": "1"}] * 2,
                    })),
                    Mock(json=Mock(return_value={
                        "count": 3,
                        "offset": 2,
                        "observations": [{"date": "2026-01-03", "value": "3"}],
                    })),
                ],
            ) as request,
        ):
            body, _ = fred._request("DGS2")

        self.assertEqual(len(body["observations"]), 3)
        self.assertEqual(
            [call.kwargs["params"]["offset"] for call in request.call_args_list],
            ["0", "2"],
        )

    def test_bea_real_gdp_response_is_normalized_without_leaking_userid(self):
        key = "bea-test-secret"
        bea.settings = replace(bea.settings, bea_api_key=key)
        body = {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {
                            "TableName": "T10101",
                            "LineNumber": "1",
                            "LineDescription": "Gross domestic product",
                            "TimePeriod": "2026Q1",
                            "DataValue": "2.4",
                        },
                        {
                            "TableName": "T10101",
                            "LineNumber": "2",
                            "LineDescription": "Personal consumption expenditures",
                            "TimePeriod": "2026Q1",
                            "DataValue": "1.8",
                        },
                    ]
                }
            }
        }
        response = Mock(json=Mock(return_value=body))
        with patch.object(bea.http, "request", return_value=response) as request:
            self.assertEqual(bea.collect(self.conn), 1)

        self.assertEqual(request.call_args.args[:2], ("GET", bea.DATA_URL))
        params = request.call_args.kwargs["params"]
        self.assertEqual(params["UserID"], key)
        self.assertEqual(params["method"], "GetData")
        self.assertEqual(params["datasetname"], "NIPA")
        self.assertEqual(params["TableName"], "T10101")
        self.assertNotIn(key, self.conn.execute(
            "SELECT source_url FROM data_observations WHERE indicator='US_GDP_QOQ_BEA'"
        ).fetchone()["source_url"])
        observation = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='US_GDP_QOQ_BEA'"
        ).fetchone()
        self.assertEqual(observation["observation_period"], "2026-Q1")
        self.assertEqual(observation["value_numeric"], 2.4)
        self.assertIsNone(observation["published_utc"])
        release = self.conn.execute(
            "SELECT actual, actual_source FROM releases "
            "WHERE event_code='US_GDP_QOQ' AND period='2026-Q1'"
        ).fetchone()
        self.assertEqual(release["actual"], 2.4)
        self.assertEqual(release["actual_source"], bea.SOURCE)

    def test_bea_provider_errors_are_reported(self):
        key = "bea-test-secret"
        bea.settings = replace(bea.settings, bea_api_key=key)
        response = Mock(
            json=Mock(
                return_value={
                    "BEAAPI": {
                        "Results": {
                            "Error": {
                                "APIErrorCode": "4",
                                "APIErrorDescription": "This UserId is not active.",
                            }
                        }
                    }
                }
            )
        )
        with (
            patch.object(bea.http, "request", return_value=response),
            self.assertRaisesRegex(ValueError, "This UserId is not active"),
        ):
            bea._request()

    def test_fred_collector_saves_macro_rates_yields_and_gdp_actuals(self):
        key = "fred-test-secret"
        fred.settings = replace(fred.settings, fred_api_key=key)
        responses = {
            "A191RL1Q225SBEA": [
                {
                    "date": "2026-04-01",
                    "value": "2.1",
                    "realtime_start": "2026-07-30",
                    "realtime_end": "9999-12-31",
                }
            ],
            "DFF": [
                {
                    "date": "2026-10-15",
                    "value": "3.75",
                    "realtime_start": "2026-10-16",
                    "realtime_end": "9999-12-31",
                }
            ],
            "DFEDTARL": [
                {
                    "date": "2026-10-15",
                    "value": "3.50",
                    "realtime_start": "2026-10-16",
                    "realtime_end": "9999-12-31",
                }
            ],
            "DFEDTARU": [
                {
                    "date": "2026-10-15",
                    "value": "3.75",
                    "realtime_start": "2026-10-16",
                    "realtime_end": "9999-12-31",
                }
            ],
            "DGS2": [
                {
                    "date": "2026-10-15",
                    "value": "3.45",
                    "realtime_start": "2026-10-16",
                    "realtime_end": "9999-12-31",
                }
            ],
            "DGS10": [
                {
                    "date": "2026-10-15",
                    "value": "4.05",
                    "realtime_start": "2026-10-16",
                    "realtime_end": "9999-12-31",
                }
            ],
            "DFII10": [
                {
                    "date": "2026-10-15",
                    "value": "1.75",
                    "realtime_start": "2026-10-16",
                    "realtime_end": "9999-12-31",
                }
            ],
        }
        with patch.object(
            fred,
            "_request",
            side_effect=[
                ({"observations": rows}, f"{fred.OBSERVATIONS_URL}?series_id={series}&file_type=json")
                for series, rows in responses.items()
            ],
        ) as request:
            self.assertEqual(fred.collect(self.conn), 7)

        self.assertEqual(request.call_count, 7)
        gdp = self.conn.execute(
            "SELECT * FROM releases WHERE event_code='US_GDP_QOQ' AND period='2026-Q2'"
        ).fetchone()
        self.assertEqual(gdp["actual"], 2.1)
        self.assertEqual(gdp["actual_source"], fred.SOURCE)
        self.assertEqual(
            self.conn.execute(
                "SELECT value_numeric FROM data_observations "
                "WHERE indicator='FEDERAL_FUNDS_EFFECTIVE_RATE'"
            ).fetchone()["value_numeric"],
            3.75,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT value_numeric FROM data_observations "
                "WHERE indicator='FEDERAL_FUNDS_TARGET_LOWER'"
            ).fetchone()["value_numeric"],
            3.5,
        )
        yield_observation = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='US2Y_FRED_DAILY'"
        ).fetchone()
        self.assertEqual(yield_observation["value_numeric"], 3.45)
        self.assertNotIn(key, yield_observation["source_url"])
        self.assertNotIn(key, yield_observation["metadata_json"])
        self.assertFalse(
            json.loads(yield_observation["metadata_json"])["intraday_confirmation_eligible"]
        )
        missing = self.conn.execute(
            "SELECT COUNT(*) FROM data_observations WHERE indicator='US_GDP_QOQ' "
            "AND observation_period='2026-Q3'"
        ).fetchone()[0]
        self.assertEqual(missing, 0)

    def test_fred_rate_limit_errors_are_recorded_without_exposing_key(self):
        key = "fred-secret"
        fred.settings = replace(fred.settings, fred_api_key=key)
        with patch.object(
            fred.http,
            "request",
            side_effect=RuntimeError(f"https://api.stlouisfed.org/?api_key={key}"),
        ):
            result = pipeline._run_source(self.conn, "fred", fred.collect)
        self.assertEqual(result["status"], "ERROR")
        self.assertNotIn(key, result["error"])
        self.assertIn("[REDACTED]", result["error"])

    def test_fred_and_bea_collection_are_scheduled_only_when_configured(self):
        with (
            patch.object(
                pipeline,
                "settings",
                replace(
                    pipeline.settings,
                    fred_api_key="configured",
                    bea_api_key="configured",
                ),
            ),
            patch.object(fred, "settings", replace(fred.settings, fred_api_key="configured")),
            patch.object(bea, "settings", replace(bea.settings, bea_api_key="configured")),
            patch.object(biquote_calendar, "collect", return_value=0),
            patch.object(pipeline.finance_calendar, "collect", return_value=0),
            patch.object(pipeline.bls, "collect", return_value=0),
            patch.object(pipeline.treasury, "collect", return_value=0),
            patch.object(pipeline.eurostat, "collect", return_value=0),
            patch.object(pipeline.ecb_public, "collect_policy_rate", return_value=0),
            patch.object(pipeline.ecb_public, "collect_fx_reference", return_value=0),
            patch.object(pipeline.gold_api, "collect", return_value=0),
            patch.object(pipeline.news, "collect", return_value=0),
            patch.object(biquote, "collect", return_value=3),
            patch.object(pipeline.alpha_vantage, "collect", return_value=3),
            patch.object(fred, "collect", return_value=5) as collect_fred,
            patch.object(bea, "collect", return_value=2) as collect_bea,
            patch.object(pipeline, "refresh_analysis", return_value=0),
        ):
            runs = pipeline.collect_all(self.conn)

        collect_fred.assert_called_once_with(self.conn)
        collect_bea.assert_called_once_with(self.conn)
        fred_run = next(run for run in runs if run["source"] == "fred")
        bea_run = next(run for run in runs if run["source"] == "bea")
        self.assertEqual(fred_run["status"], "OK")
        self.assertEqual(fred_run["records"], 5)
        self.assertEqual(bea_run["status"], "OK")
        self.assertEqual(bea_run["records"], 2)

    def test_bls_observations_include_source_and_derived_release_records(self):
        year = datetime.now(timezone.utc).year
        data = []
        for series_id in bls.SERIES.values():
            values = []
            for offset in range(13):
                month_date = datetime(year - 1, 1, 1) + timedelta(days=offset * 30)
                value = 100 + offset
                if series_id == bls.SERIES["UNEMP_RATE"]:
                    value = 4 + offset / 100
                values.append({
                    "year": str(month_date.year),
                    "period": f"M{month_date.month:02}",
                    "value": str(value),
                })
            data.append({"seriesID": series_id, "data": values})

        with patch.object(bls, "_post", return_value=data):
            written = bls.collect(self.conn)

        self.assertGreater(written, 0)
        raw = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='UNEMP_RATE' LIMIT 1"
        ).fetchone()
        derived = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='US_NFP' LIMIT 1"
        ).fetchone()
        self.assertEqual(raw["source"], "BLS Public Data API")
        self.assertEqual(raw["unit"], "%")
        self.assertEqual(raw["publication_status"], "PUBLISHED")
        self.assertEqual(derived["unit"], "K")
        self.assertTrue(derived["source"].endswith("(derived)"))
        self.assertIsNotNone(derived["observed_utc"])
        self.assertIsNone(derived["published_utc"])

    def test_news_feed_records_publication_time_and_deduplicates_items(self):
        feed = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><item>
        <title>Policy statement</title>
        <link>https://example.test/statement</link>
        <guid>policy-1</guid>
        <pubDate>Sat, 17 Oct 2026 07:00:00 +0000</pubDate>
        <category>Monetary Policy</category>
        </item></channel></rss>"""
        response = Mock(content=feed)
        with patch.object(news.http, "request", return_value=response):
            self.assertEqual(news.collect(self.conn), 3)
            self.assertEqual(news.collect(self.conn), 3)

        rows = self.conn.execute(
            "SELECT * FROM data_observations WHERE domain='news'"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["published_utc"] == "2026-10-17T07:00:00+00:00" for row in rows))
        self.assertTrue(all(row["publication_status"] == "PUBLISHED" for row in rows))
        self.assertTrue(all(json.loads(row["metadata_json"])["category"] == "Monetary Policy" for row in rows))

    def test_market_ohlc_uses_biquote_for_supported_symbols(self):
        expected = pd.DataFrame({"Close": [1.1]})
        with patch.object(biquote, "fetch_ohlc", return_value=expected) as fetch:
            result = prices.fetch_ohlc("EURUSD", interval="5m")
        self.assertIs(result, expected)
        fetch.assert_called_once_with(
            "EURUSD",
            period="5d",
            interval="5m",
            end=None,
        )

    def test_release_snapshot_uses_fresh_closed_biquote_bars(self):
        release_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        database.upsert_release(
            self.conn,
            event_code="US_CPI_YOY",
            period="2026-10",
            release_time_utc=release_time,
        )
        release_id = self.conn.execute(
            "SELECT id FROM releases WHERE event_code='US_CPI_YOY' AND period='2026-10'"
        ).fetchone()["id"]
        target = datetime.fromisoformat(release_time) - timedelta(minutes=15)
        bar_time = (target - timedelta(minutes=5)).replace(second=0, microsecond=0)
        frame = pd.DataFrame(
            {
                "Open": [1.1],
                "High": [1.2],
                "Low": [1.0],
                "Close": [1.15],
                "Volume": [0],
                "TickVolume": [7],
            },
            index=pd.DatetimeIndex([bar_time]),
        )
        with patch.object(prices, "fetch_ohlc", return_value=frame):
            self.assertEqual(snapshots.capture_live(self.conn, release_id, "PRE"), 3)

        stored = self.conn.execute(
            "SELECT symbol, price, change_pct, source FROM price_snapshots "
            "WHERE release_id=? ORDER BY symbol",
            (release_id,),
        ).fetchall()
        self.assertEqual([row["symbol"] for row in stored], ["DXY", "EURUSD", "GOLD"])
        self.assertTrue(all(row["price"] == 1.15 for row in stored))
        self.assertTrue(all(row["change_pct"] == 0 for row in stored))
        self.assertTrue(all(row["source"] == biquote.SOURCE for row in stored))
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM price_snapshots WHERE release_id=?",
                (release_id,),
            ).fetchone()[0],
            3,
        )

    def test_release_snapshot_refuses_to_store_a_future_phase_early(self):
        release_time = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        database.upsert_release(
            self.conn,
            event_code="US_CPI_YOY",
            period="2026-10",
            release_time_utc=release_time,
        )
        release_id = self.conn.execute(
            "SELECT id FROM releases WHERE event_code='US_CPI_YOY' AND period='2026-10'"
        ).fetchone()["id"]

        with self.assertRaisesRegex(RuntimeError, "Cannot capture T\\+2H before"):
            snapshots.capture_live(self.conn, release_id, "T+2H")

        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM price_snapshots WHERE release_id=? AND phase='T+2H'",
                (release_id,),
            ).fetchone()
        )

    def test_release_snapshot_rejects_stale_biquote_bars(self):
        release_time = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        database.upsert_release(
            self.conn,
            event_code="US_CPI_YOY",
            period="2026-10",
            release_time_utc=release_time,
        )
        release_id = self.conn.execute(
            "SELECT id FROM releases WHERE event_code='US_CPI_YOY' AND period='2026-10'"
        ).fetchone()["id"]
        target = datetime.fromisoformat(release_time) - timedelta(minutes=15)
        bar_time = (target - timedelta(minutes=25)).replace(second=0, microsecond=0)
        frame = pd.DataFrame(
            {
                "Open": [1.1],
                "High": [1.2],
                "Low": [1.0],
                "Close": [1.15],
                "Volume": [0],
                "TickVolume": [7],
            },
            index=pd.DatetimeIndex([bar_time]),
        )
        with (
            patch.object(prices, "fetch_ohlc", return_value=frame),
            self.assertRaisesRegex(RuntimeError, "no sufficiently fresh closed 5-minute bars"),
        ):
            snapshots.capture_live(self.conn, release_id, "PRE")

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM price_snapshots WHERE release_id=?",
                (release_id,),
            ).fetchone()[0],
            0,
        )

    def test_preparation_windows_include_every_upcoming_calendar_event(self):
        release_time = datetime.now(timezone.utc) + timedelta(hours=3)
        period = release_time.strftime("%Y-%m")
        database.upsert_release(
            self.conn,
            event_code="US_CPI_YOY",
            period=period,
            release_time_utc=release_time.isoformat(),
            forecast=3.1,
        )
        self.conn.executemany(
            """
            INSERT INTO calendar_events
              (event_id, event_name, currency, country, release_time_utc, period,
               forecast, source, retrieved_at_utc, release_status, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', '{}')
            """,
            [
                (
                    "biquote:known-cpi",
                    "CPI y/y",
                    "USD",
                    "United States",
                    release_time.isoformat(),
                    period,
                    3.1,
                    biquote_calendar.SOURCE,
                    datetime.now(timezone.utc).isoformat(),
                ),
                (
                    "biquote:unmapped",
                    "Consumer Confidence",
                    "USD",
                    "United States",
                    release_time.isoformat(),
                    period,
                    102.0,
                    biquote_calendar.SOURCE,
                    datetime.now(timezone.utc).isoformat(),
                ),
            ],
        )
        self.conn.commit()

        entries = build_preparation_schedule(
            self.conn,
            windows_minutes=(60, 15, 1),
        )

        self.assertEqual(
            {entry["event_key"] for entry in entries},
            {"biquote:known-cpi", "biquote:unmapped"},
        )
        self.assertEqual(
            set(entries[0]["preparation_times"]),
            {60, 15, 1},
        )
        for entry in entries:
            self.assertEqual(
                {
                    window: (
                        entry["release_time_utc"] - scheduled
                    ).total_seconds() / 60
                    for window, scheduled in entry["preparation_times"].items()
                },
                {60: 60, 15: 15, 1: 1},
            )

    def test_pre_release_preparation_snapshots_are_complete_and_immutable(self):
        release_time = datetime.now(timezone.utc) + timedelta(hours=2)
        period = release_time.strftime("%Y-%m")
        event_id = "biquote:preparation-cpi"
        database.upsert_release(
            self.conn,
            event_code="US_CPI_YOY",
            period=period,
            release_time_utc=release_time.isoformat(),
            forecast=3.1,
            previous=3.0,
        )
        retrieved = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO calendar_events
              (event_id, event_name, currency, country, release_time_utc, period,
               forecast_text, forecast, previous_text, previous, unit, importance,
               source, source_url, retrieved_at_utc, release_status, raw_json)
            VALUES (?, 'CPI y/y', 'USD', 'United States', ?, ?, '3.1', 3.1,
                    '3.0', 3.0, '%', 'HIGH', ?, 'https://www.bls.gov/', ?,
                    'SCHEDULED', '{}')
            """,
            (
                event_id,
                release_time.isoformat(),
                period,
                biquote_calendar.SOURCE,
                retrieved,
            ),
        )
        observations.save_observation(
            self.conn,
            domain="central_bank",
            indicator="FEDERAL_FUNDS_TARGET_LOWER",
            value_numeric=3.5,
            unit="%",
            source="FRED",
            source_url="https://api.stlouisfed.org/fred/series/observations",
            observed_utc=retrieved,
            publication_status="PUBLISHED",
        )
        for indicator, value in (
            ("US2Y_DAILY", 3.6),
            ("US10Y_DAILY", 4.0),
            ("REAL10Y_DAILY", 1.7),
        ):
            observations.save_observation(
                self.conn,
                domain="market",
                indicator=indicator,
                value_numeric=value,
                unit="%",
                source="US Treasury",
                source_url="https://home.treasury.gov/",
                observed_utc=retrieved,
                publication_status="DAILY",
            )
        quote_time = datetime.now(timezone.utc).isoformat()
        quotes = {
            symbol: {
                "mid": mid,
                "bid": mid - 0.01,
                "ask": mid + 0.01,
                "spread": 0.02,
                "is_stale": False,
                "market_state": "open",
                "source_timestamp_utc": quote_time,
                "quote_age_seconds": 0,
                "provider_source": "test feed",
                "exchange": "test",
            }
            for symbol, mid in (("GOLD", 2600), ("EURUSD", 1.1), ("DXY", 103))
        }
        with patch.object(biquote, "fetch_quotes", return_value=quotes):
            result = preparation.capture_preparation(self.conn, event_id, 60)

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["market_snapshots"], 6)
        snapshot = self.conn.execute(
            "SELECT * FROM pre_release_preparations WHERE event_key=?",
            (event_id,),
        ).fetchone()
        self.assertEqual(snapshot["forecast"], 3.1)
        self.assertEqual(snapshot["previous"], 3.0)
        self.assertTrue(snapshot["forecast_available_pre_release"])
        self.assertLess(
            datetime.fromisoformat(snapshot["captured_at_utc"]),
            datetime.fromisoformat(snapshot["scheduled_release_utc"]),
        )
        scenarios = json.loads(snapshot["scenarios_json"])
        self.assertEqual(set(scenarios), {
            "actual_above_forecast",
            "actual_near_forecast",
            "actual_below_forecast",
        })
        self.assertTrue(
            all(scenario["final_signal"] is None for scenario in scenarios.values())
        )
        stored_markets = self.conn.execute(
            "SELECT instrument, source_symbol, price, timeframe, data_status, source_quality "
            "FROM pre_release_market_snapshots WHERE event_key=? ORDER BY instrument",
            (event_id,),
        ).fetchall()
        self.assertEqual(len(stored_markets), 6)
        yield_symbols = {
            row["instrument"]: row["source_symbol"]
            for row in stored_markets
            if row["timeframe"] == "daily"
        }
        self.assertEqual(
            yield_symbols,
            {"US02Y": "2 Yr", "US10Y": "10 Yr", "US_REAL_YIELD": "10 YR"},
        )
        self.assertEqual(
            {row["instrument"] for row in stored_markets if row["timeframe"] == "tick"},
            {"XAUUSD", "EURUSD", "DXY"},
        )
        self.assertTrue(
            all(
                row["data_status"] == "DAILY"
                for row in stored_markets
                if row["timeframe"] == "daily"
            )
        )

        self.conn.execute(
            "UPDATE calendar_events SET forecast=3.4, forecast_text='3.4' WHERE event_id=?",
            (event_id,),
        )
        with patch.object(biquote, "fetch_quotes", return_value=quotes):
            duplicate = preparation.capture_preparation(self.conn, event_id, 60)
        self.assertTrue(duplicate["already_captured"])
        self.assertEqual(
            self.conn.execute(
                "SELECT forecast FROM pre_release_preparations WHERE event_key=?",
                (event_id,),
            ).fetchone()["forecast"],
            3.1,
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.conn.execute(
                "UPDATE pre_release_preparations SET forecast=9 WHERE event_key=?",
                (event_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            self.conn.execute(
                "DELETE FROM pre_release_market_snapshots WHERE event_key=?",
                (event_id,),
            )

    def test_pre_release_preparation_marks_missing_forecast_and_feeds_incomplete(self):
        event_id = "biquote:incomplete-cpi"
        release_time = datetime.now(timezone.utc) + timedelta(hours=2)
        self.conn.execute(
            """
            INSERT INTO calendar_events
              (event_id, event_name, currency, country, release_time_utc, period,
               source, retrieved_at_utc, release_status, raw_json)
            VALUES (?, 'CPI y/y', 'USD', 'United States', ?, '2026-10',
                    ?, ?, 'SCHEDULED', '{}')
            """,
            (
                event_id,
                release_time.isoformat(),
                biquote_calendar.SOURCE,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        with patch.object(biquote, "fetch_quotes", return_value={}):
            result = preparation.capture_preparation(self.conn, event_id, 15)

        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIn("CONSENSUS_FORECAST", result["missing_fields"])
        self.assertIn("DXY_FEED", result["missing_fields"])
        row = self.conn.execute(
            "SELECT forecast, forecast_available_pre_release, data_quality_status, "
            "missing_fields_json, scenarios_json FROM pre_release_preparations "
            "WHERE event_key=?",
            (event_id,),
        ).fetchone()
        self.assertIsNone(row["forecast"])
        self.assertFalse(row["forecast_available_pre_release"])
        self.assertEqual(row["data_quality_status"], "INCOMPLETE")
        scenarios = json.loads(row["scenarios_json"])
        self.assertTrue(
            all(scenario["status"] == "UNAVAILABLE_FORECAST" for scenario in scenarios.values())
        )

    def test_collection_failure_is_persisted_and_tokens_are_redacted(self):
        result = pipeline._run_source(
            self.conn,
            "biquote_calendar",
            lambda _conn: (_ for _ in ()).throw(
                RuntimeError("request failed?api_key=secret-key")
            ),
        )

        self.assertEqual(result["status"], "ERROR")
        self.assertNotIn("secret-key", result["error"])
        saved = self.conn.execute(
            "SELECT status, error FROM collection_runs WHERE source='biquote_calendar'"
        ).fetchone()
        self.assertEqual(saved["status"], "ERROR")
        self.assertIn("[REDACTED]", saved["error"])

    def test_calendar_coverage_prefers_biquote_for_duplicate_fallback_events(self):
        release_time = "2026-11-05T13:30:00+00:00"
        retrieved = datetime.now(timezone.utc).isoformat()
        for event_id, name, source, currency, country in (
            ("biquote:test-cpi", "CPI y/y", "BiQuote economic calendar · mql5", "USD", "United States"),
            ("finance:test-cpi", "US CPI", finance_calendar.SOURCE, None, None),
            ("finance:test-nfp", "US Nonfarm Payrolls", finance_calendar.SOURCE, None, None),
        ):
            self.conn.execute(
                """
                INSERT INTO calendar_events
                  (event_id, event_name, currency, country, release_time_utc,
                   source, retrieved_at_utc, release_status, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'SCHEDULED', '{}')
                """,
                (event_id, name, currency, country, release_time, source, retrieved),
            )
        self.conn.commit()

        visible = pipeline.coverage(self.conn)["calendar_events"]

        self.assertEqual(
            {event["event_id"] for event in visible},
            {"biquote:test-cpi", "finance:test-nfp"},
        )

    def test_biquote_calendar_normalizes_fields_and_preserves_values_when_missing(self):
        release_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        event = {
            "id": "mql5:calendar-101",
            "eventId": "mql5:series-101",
            "time": release_time,
            "period": "2026-10-01T00:00:00Z",
            "countryCode": "US",
            "currency": "USD",
            "name": "Initial Jobless Claims",
            "importance": "high",
            "unit": "people",
            "multiplier": "thousands",
            "actual": None,
            "forecast": 225,
            "previous": 220,
            "revisedPrevious": None,
            "revision": 0,
            "timeMode": "exact",
            "sourceUrl": "https://www.dol.gov/",
            "source": "mql5",
        }
        with patch.object(
            biquote_calendar.http,
            "request",
            return_value=Mock(json=Mock(return_value=[event])),
        ) as request:
            self.assertEqual(biquote_calendar.collect(self.conn), 1)

        self.assertEqual(request.call_args.args[:2], ("GET", biquote_calendar.CALENDAR_URL))
        self.assertEqual(request.call_args.kwargs["params"]["countries"], "US,EU")
        stored = self.conn.execute(
            "SELECT * FROM calendar_events WHERE event_id='biquote:mql5:calendar-101'"
        ).fetchone()
        self.assertEqual(stored["currency"], "USD")
        self.assertEqual(stored["country"], "United States")
        self.assertEqual(stored["period"], "2026-10")
        self.assertEqual(stored["forecast"], 225)
        self.assertEqual(stored["previous"], 220)
        self.assertIsNone(stored["actual"])
        self.assertEqual(stored["unit"], "K")
        self.assertIsNone(stored["source_timestamp_utc"])
        self.assertEqual(stored["release_status"], "SCHEDULED")
        self.assertTrue(stored["retrieved_at_utc"])
        release = self.conn.execute(
            "SELECT * FROM releases WHERE event_code='US_JOBLESS_CLAIMS'"
        ).fetchone()
        self.assertEqual(release["forecast"], 225)
        self.assertEqual(release["previous"], 220)
        observation = self.conn.execute(
            """
            SELECT metadata_json FROM data_observations
            WHERE indicator='US_JOBLESS_CLAIMS_FORECAST'
            """
        ).fetchone()
        metadata = json.loads(observation["metadata_json"])
        self.assertEqual(metadata["source_record_id"], "mql5:calendar-101")
        self.assertEqual(metadata["upstream_source"], "mql5")
        self.assertIsNone(metadata["source_timestamp_utc"])
        self.assertTrue(metadata["forecast_known_pre_release"])

        revised = {
            **event,
            "actual": 230,
            "previous": 223,
            "revisedPrevious": 220,
            "revision": 1,
        }
        with patch.object(
            biquote_calendar.http,
            "request",
            return_value=Mock(json=Mock(return_value=[revised])),
        ):
            self.assertEqual(biquote_calendar.collect(self.conn), 1)

        stored = self.conn.execute(
            "SELECT * FROM calendar_events WHERE event_id='biquote:mql5:calendar-101'"
        ).fetchone()
        self.assertEqual(stored["release_status"], "RELEASED")
        self.assertEqual(stored["actual"], 230)
        self.assertEqual(stored["previous"], 223)
        self.assertEqual(stored["revised_previous"], 220)
        self.assertEqual(stored["is_revision"], 1)

        missing = {
            **event,
            "forecast": None,
            "previous": None,
            "actual": None,
            "revisedPrevious": None,
        }
        with patch.object(
            biquote_calendar.http,
            "request",
            return_value=Mock(json=Mock(return_value=[missing])),
        ):
            self.assertEqual(biquote_calendar.collect(self.conn), 1)
        stored = self.conn.execute(
            "SELECT * FROM calendar_events WHERE event_id='biquote:mql5:calendar-101'"
        ).fetchone()
        self.assertEqual(stored["forecast"], 225)
        self.assertEqual(stored["previous"], 223)
        self.assertEqual(stored["actual"], 230)
        self.assertEqual(stored["revised_previous"], 220)
        self.assertEqual(stored["release_status"], "RELEASED")

    def test_biquote_calendar_does_not_use_a_post_release_forecast_as_consensus(self):
        event = {
            "id": "mql5:calendar-post-release",
            "time": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "period": "2026-10-01T00:00:00Z",
            "countryCode": "US",
            "currency": "USD",
            "name": "CPI y/y",
            "unit": "percent",
            "multiplier": "none",
            "forecast": 3.1,
            "previous": 3.0,
            "actual": 3.2,
            "source": "mql5",
            "sourceUrl": "https://www.bls.gov/",
        }
        with patch.object(
            biquote_calendar.http,
            "request",
            return_value=Mock(json=Mock(return_value=[event])),
        ):
            self.assertEqual(biquote_calendar.collect(self.conn), 1)

        release = self.conn.execute(
            "SELECT forecast, actual FROM releases WHERE event_code='US_CPI_YOY'"
        ).fetchone()
        self.assertIsNone(release["forecast"])
        self.assertEqual(release["actual"], 3.2)
        forecast = self.conn.execute(
            """
            SELECT publication_status, metadata_json FROM data_observations
            WHERE indicator='US_CPI_YOY_FORECAST'
            """
        ).fetchone()
        self.assertEqual(forecast["publication_status"], "POST_RELEASE_FORECAST")
        self.assertFalse(json.loads(forecast["metadata_json"])["forecast_known_pre_release"])

    def test_biquote_calendar_rejects_malformed_event_and_uses_event_specific_periods(self):
        self.assertEqual(biquote_calendar._event_code("US", "CPI y/y"), "US_CPI_YOY")
        self.assertEqual(biquote_calendar._event_code("US", "PPI y/y"), "US_PPI_YOY")
        self.assertEqual(
            biquote_calendar._event_code("US", "Core CPI m/m"),
            "US_CORE_CPI_MOM",
        )
        self.assertEqual(
            biquote_calendar._event_code("EU", "GDP q/q"),
            "EU_GDP_QOQ",
        )
        self.assertEqual(
            biquote_calendar._period("2026-10-01T00:00:00Z", "GDP q/q"),
            "2026-Q4",
        )
        with (
            patch.object(
                biquote_calendar.http,
                "request",
                return_value=Mock(json=Mock(return_value=[{"name": "CPI"}])),
            ),
            self.assertRaisesRegex(ValueError, "missing its id or name"),
        ):
            biquote_calendar.collect(self.conn)

    def test_biquote_calendar_splits_ranges_when_provider_limit_is_reached(self):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        end = datetime(2026, 10, 2, tzinfo=timezone.utc)
        responses = [
            Mock(json=Mock(return_value=[{"id": str(index)} for index in range(500)])),
            Mock(json=Mock(return_value=[{"id": "first-day"}])),
            Mock(json=Mock(return_value=[{"id": "second-day"}])),
        ]
        with patch.object(
            biquote_calendar.http, "request", side_effect=responses
        ) as request:
            events = biquote_calendar._fetch_events(start, end)

        self.assertEqual([event["id"] for event in events], ["first-day", "second-day"])
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[1].kwargs["params"]["from"], "2026-10-01")
        self.assertEqual(request.call_args_list[1].kwargs["params"]["to"], "2026-10-01")
        self.assertEqual(request.call_args_list[2].kwargs["params"]["from"], "2026-10-02")
        self.assertEqual(request.call_args_list[2].kwargs["params"]["to"], "2026-10-02")

    def test_public_calendar_persists_only_available_fields_and_revisions(self):
        upcoming = {
            "date": "2026-11-05",
            "time_utc": "2026-11-05T13:30:00+00:00",
            "all_day": False,
            "name": "US CPI",
            "title": "US CPI report October 2026",
            "impact": "high",
            "category": "economic-indicators",
            "consensus": "3.1%",
            "prior": "3.0%",
            "actual": None,
            "url": "https://www.financecalendar.com/event/us-cpi-report-october-2026/",
        }
        response = Mock(json=Mock(return_value={"events": [upcoming], "attribution": {}}))
        with patch.object(finance_calendar.http, "request", return_value=response):
            self.assertEqual(finance_calendar.collect(self.conn), 1)
            event_observation_count = self.conn.execute(
                "SELECT COUNT(*) FROM data_observations"
            ).fetchone()[0]
            self.assertEqual(finance_calendar.collect(self.conn), 1)

        event = self.conn.execute("SELECT * FROM calendar_events").fetchone()
        self.assertEqual(event["currency"], None)
        self.assertEqual(event["country"], None)
        self.assertIsNone(event["period"])
        self.assertIsNone(event["unit"])
        self.assertEqual(event["forecast"], 3.1)
        self.assertEqual(event["previous"], 3.0)
        self.assertIsNone(event["source_timestamp_utc"])
        self.assertEqual(event["release_status"], "SCHEDULED")
        metadata = json.loads(
            self.conn.execute(
                "SELECT metadata_json FROM data_observations WHERE indicator='CALENDAR_EVENT'"
            ).fetchone()["metadata_json"]
        )
        self.assertEqual(
            metadata["validation_errors"],
            ["currency", "country", "period", "unit", "source_timestamp_utc"],
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0],
            event_observation_count,
        )
        release = self.conn.execute("SELECT * FROM releases").fetchone()
        self.assertEqual(release["forecast"], 3.1)
        self.assertTrue(release["period"].startswith("event:"))

        revised = {**upcoming, "actual": "3.2%", "revised_previous": "2.9%"}
        with patch.object(
            finance_calendar.http,
            "request",
            return_value=Mock(json=Mock(return_value={"events": [revised], "attribution": {}})),
        ):
            finance_calendar.collect(self.conn)
        event = self.conn.execute("SELECT * FROM calendar_events").fetchone()
        self.assertEqual(event["release_status"], "RELEASED")
        self.assertEqual(event["actual"], 3.2)
        self.assertEqual(event["revised_previous"], 2.9)
        self.assertEqual(event["is_revision"], 1)

        incomplete = {**upcoming, "consensus": None, "prior": None, "actual": None,
                      "revised_previous": None}
        with patch.object(
            finance_calendar.http,
            "request",
            return_value=Mock(json=Mock(return_value={"events": [incomplete], "attribution": {}})),
        ):
            finance_calendar.collect(self.conn)
        event = self.conn.execute("SELECT * FROM calendar_events").fetchone()
        self.assertEqual(event["forecast"], 3.1)
        self.assertEqual(event["previous"], 3.0)
        self.assertEqual(event["actual"], 3.2)
        self.assertEqual(event["revised_previous"], 2.9)
        self.assertEqual(event["release_status"], "RELEASED")

    def test_calendar_event_mapping_does_not_assume_non_us_cpi_or_payrolls(self):
        self.assertIsNone(finance_calendar._event_code({"name": "UK CPI", "title": "UK CPI"}))
        self.assertIsNone(
            finance_calendar._event_code(
                {"name": "Nonfarm Payrolls", "title": "Nonfarm Payrolls"}
            )
        )
        self.assertEqual(
            finance_calendar._event_code({"name": "US CPI", "title": "CPI"}),
            "US_CPI_YOY",
        )

    def test_treasury_curve_observations_are_daily_and_not_intraday_eligible(self):
        year = datetime.now(timezone.utc).year
        response = Mock(
            content=(
                b'Date,"2 Yr","10 Yr"\r\n10/16/2026,4.74,5.09\r\n'
                if year == 2026
                else f'Date,"2 Yr","10 Yr"\r\n01/02/{year},4.74,5.09\r\n'.encode()
            )
        )
        real_response = Mock(
            content=(
                b'Date,"10 YR"\r\n10/16/2026,2.74\r\n'
                if year == 2026
                else f'Date,"10 YR"\r\n01/02/{year},2.74\r\n'.encode()
            )
        )
        with patch.object(treasury.http, "request", side_effect=[response, real_response]):
            self.assertEqual(treasury.collect(self.conn), 3)
        observation = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='US2Y_DAILY'"
        ).fetchone()
        metadata = json.loads(observation["metadata_json"])
        self.assertEqual(observation["publication_status"], "DAILY")
        self.assertEqual(metadata["timeframe"], "daily")
        self.assertFalse(metadata["intraday_confirmation_eligible"])

    def test_eurostat_json_stat_values_normalize_quarterly_series(self):
        gdp = {
            "updated": "2026-10-16T23:00:00+02:00",
            "id": ["freq", "unit", "s_adj", "na_item", "geo", "time"],
            "size": [1, 1, 1, 1, 1, 2],
            "dimension": {
                "freq": {"category": {"index": {"Q": 0}}},
                "unit": {"category": {"index": {"CLV_PCH_PRE": 0}}},
                "s_adj": {"category": {"index": {"SCA": 0}}},
                "na_item": {"category": {"index": {"B1GQ": 0}}},
                "geo": {"category": {"index": {"EA21": 0}}},
                "time": {"category": {"index": {"2026-Q1": 0, "2026-Q2": 1}}},
            },
            "value": {"0": 0.2, "1": 0.4},
        }
        employment = {
            **gdp,
            "unit": "THS_PER",
            "size": [1, 1, 1, 1, 1, 1, 2],
            "id": ["freq", "unit", "nace_r2", "s_adj", "na_item", "geo", "time"],
            "dimension": {
                **gdp["dimension"],
                "unit": {"category": {"index": {"THS_PER": 0}}},
                "nace_r2": {"category": {"index": {"TOTAL": 0}}},
                "s_adj": {"category": {"index": {"SCA": 0}}},
                "na_item": {"category": {"index": {"EMP_DC": 0}}},
            },
            "value": {"0": 1000.0, "1": 1010.0},
        }
        hicp = {
            "updated": "2026-10-16T23:00:00+02:00",
            "id": ["freq", "unit", "coicop", "geo", "time"],
            "size": [1, 1, 1, 1, 2],
            "dimension": {
                "freq": {"category": {"index": {"M": 0}}},
                "unit": {"category": {"index": {"RCH_A": 0}}},
                "coicop": {"category": {"index": {"CP00": 0}}},
                "geo": {"category": {"index": {"EA20": 0}}},
                "time": {"category": {"index": {"2026-01": 0, "2026-02": 1}}},
            },
            "value": {"0": 2.3, "1": 2.2},
        }
        with patch.object(eurostat, "_fetch", side_effect=[gdp, employment, hicp]):
            self.assertEqual(eurostat.collect(self.conn), 7)
        self.assertEqual(
            self.conn.execute(
                "SELECT actual FROM releases WHERE event_code='EU_GDP_QOQ' AND period='2026-Q2'"
            ).fetchone()["actual"],
            0.4,
        )
        employment_change = self.conn.execute(
            "SELECT actual FROM releases WHERE event_code='EU_EMPLOYMENT_QOQ' AND period='2026-Q2'"
        ).fetchone()
        self.assertAlmostEqual(employment_change["actual"], 1.0)
        eurozone_inflation = self.conn.execute(
            "SELECT actual FROM releases WHERE event_code='EU_CPI_YOY' AND period='2026-02'"
        ).fetchone()
        self.assertEqual(eurozone_inflation["actual"], 2.2)

    def test_ecb_policy_and_reference_rates_are_daily_not_intraday(self):
        policy_rows = [
            {"TIME_PERIOD": "2026-10-16", "OBS_VALUE": "2.5"},
        ]
        fx_rows = [
            {
                "TIME_PERIOD": "2026-10-16",
                "OBS_VALUE": "1.1253",
                "TITLE_COMPL": "ECB reference exchange rate, US dollar/Euro, 2.15 pm",
            }
        ]
        with patch.object(
            ecb_public,
            "_fetch_csv",
            side_effect=[(policy_rows, "https://data-api.ecb.europa.eu/policy"), (fx_rows, "https://data-api.ecb.europa.eu/fx")],
        ):
            self.assertEqual(ecb_public.collect_policy_rate(self.conn), 1)
            self.assertEqual(ecb_public.collect_fx_reference(self.conn), 1)
        fx = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='EURUSD_ECB_REFERENCE'"
        ).fetchone()
        metadata = json.loads(fx["metadata_json"])
        self.assertEqual(fx["publication_status"], "DAILY_REFERENCE")
        self.assertTrue(metadata["is_delayed"])
        self.assertFalse(metadata["intraday_confirmation_eligible"])

    def test_gold_quote_never_claims_bid_ask_or_verified_latency(self):
        timestamp = datetime.now(timezone.utc).isoformat()
        response = Mock(
            json=Mock(
                return_value={
                    "symbol": "XAU",
                    "currency": "USD",
                    "price": 4000.0,
                    "updatedAt": timestamp,
                }
            )
        )
        with patch.object(gold_api.http, "request", return_value=response):
            self.assertEqual(gold_api.collect(self.conn), 1)
        observation = self.conn.execute(
            "SELECT * FROM data_observations WHERE indicator='XAUUSD_SPOT_QUOTE'"
        ).fetchone()
        metadata = json.loads(observation["metadata_json"])
        self.assertEqual(observation["publication_status"], "LATENCY_UNVERIFIED")
        self.assertIsNone(metadata["is_delayed"])
        self.assertFalse(metadata["has_bid"])
        self.assertFalse(metadata["has_ohlc"])

    def test_market_verdict_does_not_call_missing_market_data_a_contradiction(self):
        alignment, score, note = market.align_with_fundamentals(
            {"usd_dir": "UNKNOWN", "yield_dir": "UNKNOWN"},
            "BULLISH",
        )

        self.assertEqual(alignment, "INSUFFICIENT_DATA")
        self.assertEqual(score, 0.0)
        self.assertIn("unavailable", note)


if __name__ == "__main__":
    unittest.main()
