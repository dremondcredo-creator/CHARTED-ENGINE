import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cne.config import settings as config_settings
from cne.core import fundamentals, release_monitor, surprise
from cne.core.events import seed
from cne.db import database


class ReleaseAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_settings = database.settings
        self.original_analysis_settings = fundamentals.settings
        root = Path(self.temp_dir.name)
        test_settings = replace(
            config_settings,
            data_dir=root,
            db_path=root / "test.db",
            calendar_csv=root / "calendar.csv",
            surprise_normalization_min_samples=12,
            fundamental_event_weights_json="{}",
            fundamental_window_days=30,
        )
        database.settings = test_settings
        fundamentals.settings = test_settings
        self.conn = database.init_db()
        seed(self.conn)

    def tearDown(self):
        self.conn.close()
        database.settings = self.original_settings
        fundamentals.settings = self.original_analysis_settings
        self.temp_dir.cleanup()

    def _observation(
        self, indicator, period, value, source, fetched, *, status="PUBLISHED",
        unit="%",
    ):
        self.conn.execute(
            """
            INSERT INTO data_observations
              (domain, indicator, observation_period, value_numeric, unit, source,
               source_url, observed_utc, published_utc, fetched_utc,
               publication_status, metadata_json)
            VALUES ('economic', ?, ?, ?, ?, ?, 'https://provider.example/data',
                    ?, NULL, ?, ?, ?)
            """,
            (
                indicator,
                period,
                value,
                unit,
                source,
                fetched,
                fetched,
                status,
                json.dumps({"source_record_id": f"{indicator}:{period}"}),
            ),
        )

    def _release_with_verified_inputs(self, code, period, scheduled, forecast, actual, source):
        database.upsert_release(
            self.conn,
            event_code=code,
            period=period,
            release_time_utc=scheduled.isoformat(),
            forecast=999.0,
            actual=actual,
            actual_source="Calendar provider",
        )
        before_release = (scheduled - timedelta(seconds=30)).isoformat()
        self._observation(
            f"{code}_FORECAST",
            period,
            forecast,
            "BiQuote economic calendar",
            before_release,
            status="PRE_RELEASE_FORECAST",
        )
        self._observation(
            code,
            period,
            actual,
            source,
            datetime.now(timezone.utc).isoformat(),
        )

    def test_monitor_freezes_pre_release_forecast_and_uses_event_interpretation(self):
        scheduled = datetime.now(timezone.utc) - timedelta(minutes=2)
        period = "2026-10"
        self._release_with_verified_inputs(
            "US_UNEMPLOYMENT", period, scheduled, 4.0, 4.2, "BLS Public Data API"
        )
        self._observation(
            "US_UNEMPLOYMENT_FORECAST",
            period,
            3.2,
            "BiQuote economic calendar",
            datetime.now(timezone.utc).isoformat(),
            status="POST_RELEASE_FORECAST",
        )
        self._observation(
            "US_UNEMPLOYMENT_PREVIOUS",
            period,
            4.1,
            "BiQuote economic calendar",
            (scheduled - timedelta(seconds=30)).isoformat(),
        )

        self.assertEqual(release_monitor.process_releases(self.conn), 1)
        result = self.conn.execute(
            "SELECT * FROM release_monitor_results"
        ).fetchone()
        surprise_row = self.conn.execute("SELECT * FROM surprises").fetchone()
        self.assertEqual(result["data_status"], "VERIFIED")
        self.assertEqual(result["actual_value"], 4.2)
        self.assertEqual(result["forecast_at_release"], 4.0)
        self.assertEqual(result["previous_value"], 4.1)
        self.assertIsNone(result["publication_timestamp_utc"])
        self.assertEqual(result["actual_source"], "BLS Public Data API")
        self.assertTrue(result["retrieved_at_utc"])
        self.assertEqual(result["raw_surprise"], 0.2)
        self.assertEqual(result["currency_impact"], "BEARISH")
        self.assertEqual(surprise_row["data_status"], "VERIFIED")
        self.assertIsNone(surprise_row["normalized_surprise"])
        self.assertEqual(surprise_row["normalization_status"], "INSUFFICIENT_HISTORY")
        self.assertEqual(surprise.compute_surprise("US_UNEMPLOYMENT", 4.0, 4.2)["impact"], "BEARISH")

        self.conn.execute(
            "UPDATE release_monitor_results SET is_revision=1"
        )
        self.conn.commit()
        self.assertEqual(release_monitor.process_releases(self.conn), 1)
        updated = self.conn.execute(
            "SELECT is_revision FROM release_monitor_results"
        ).fetchone()
        self.assertEqual(updated["is_revision"], 1)

    def test_unverified_actual_or_post_release_forecast_never_creates_surprise(self):
        scheduled = datetime.now(timezone.utc) - timedelta(minutes=1)
        database.upsert_release(
            self.conn,
            event_code="US_CPI_YOY",
            period="2026-10",
            release_time_utc=scheduled.isoformat(),
            forecast=3.1,
            actual=3.4,
            actual_source="BiQuote economic calendar",
        )
        self._observation(
            "US_CPI_YOY_FORECAST",
            "2026-10",
            3.1,
            "BiQuote economic calendar",
            datetime.now(timezone.utc).isoformat(),
            status="POST_RELEASE_FORECAST",
        )

        release_monitor.process_releases(self.conn)
        result = self.conn.execute(
            "SELECT * FROM release_monitor_results"
        ).fetchone()
        self.assertEqual(result["data_status"], "INCOMPLETE")
        self.assertIsNone(result["raw_surprise"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM surprises").fetchone())
        self.assertIn(
            "OFFICIAL_ACTUAL_SOURCE",
            json.loads(result["status_details_json"])["missing_fields"],
        )
        self.assertIn(
            "FORECAST_KNOWN_BEFORE_RELEASE",
            json.loads(result["status_details_json"])["missing_fields"],
        )

    def test_historical_scale_uses_only_earlier_verified_surprises(self):
        now = datetime.now(timezone.utc)
        for index in range(12):
            period = f"2025-{index + 1:02d}"
            scheduled = datetime(2025, index + 1, 15, tzinfo=timezone.utc)
            self._release_with_verified_inputs(
                "US_UNEMPLOYMENT",
                period,
                scheduled,
                4.0,
                float(index),
                "BLS Public Data API",
            )
        current_period = now.strftime("%Y-%m")
        self._release_with_verified_inputs(
            "US_UNEMPLOYMENT",
            current_period,
            now - timedelta(minutes=1),
            4.0,
            4.2,
            "BLS Public Data API",
        )

        release_monitor.process_releases(self.conn)
        current = self.conn.execute(
            """
            SELECT result.normalized_surprise, result.historical_scale,
                   result.normalization_n, result.normalization_status
            FROM release_monitor_results AS result
            JOIN releases AS release ON release.id = result.release_id
            WHERE release.period = ?
            """,
            (current_period,),
        ).fetchone()
        self.assertEqual(current["normalization_n"], 12)
        self.assertEqual(current["normalization_status"], "AVAILABLE")
        self.assertAlmostEqual(current["historical_scale"], 3.605551275, places=6)
        self.assertAlmostEqual(current["normalized_surprise"], 0.2 / 3.605551275, places=4)

    def test_fundamental_assessment_groups_correlated_data_and_marks_provisional(self):
        now = datetime.now(timezone.utc)
        for code, period, raw, impact in (
            ("US_CPI_YOY", "2026-09", 0.2, "BULLISH"),
            ("US_CORE_CPI_YOY", "2026-09", 0.1, "BULLISH"),
            ("US_NFP", "2026-09", 100.0, "BULLISH"),
            ("US_UNEMPLOYMENT", "2026-09", -0.1, "BULLISH"),
            ("US_AHE_MOM", "2026-09", 0.1, "BULLISH"),
        ):
            database.upsert_release(
                self.conn,
                event_code=code,
                period=period,
                release_time_utc=(now - timedelta(days=1)).isoformat(),
                actual=1.0,
                forecast=1.0 - raw,
            )
            release_id = self.conn.execute(
                "SELECT id FROM releases WHERE event_code=? AND period=?",
                (code, period),
            ).fetchone()["id"]
            self.conn.execute(
                """
                INSERT INTO release_monitor_results
                  (release_id, actual_value, forecast_at_release, reporting_period,
                   actual_source, data_status, status_details_json,
                   raw_surprise, updated_at_utc)
                VALUES (?, 1.0, ?, ?, 'BLS Public Data API', 'VERIFIED', '{}', ?, ?)
                """,
                (release_id, 1.0 - raw, period, raw, now.isoformat()),
            )
            self.conn.execute(
                """
                INSERT INTO surprises
                  (release_id, raw_surprise, data_status, impact, computed_utc)
                VALUES (?, ?, 'VERIFIED', ?, ?)
                """,
                (release_id, raw, impact, now.isoformat()),
            )
        self.conn.commit()

        report = fundamentals.assess_all(self.conn, now)
        usd = report["USD"]
        self.assertEqual(usd["direction"], "BULLISH")
        self.assertTrue(usd["score_is_probability"] is False)
        self.assertEqual(usd["evidence_quality"]["calibration_status"], "PROVISIONAL_UNVALIDATED")
        self.assertEqual(usd["evidence_quality"]["verified_groups"], 2)
        self.assertTrue(any("market-implied policy expectations" in item for item in usd["missing_information"]))
        self.assertTrue(any("VIX/risk-sentiment" in item for item in usd["missing_information"]))
        self.assertEqual(report["GOLD"]["direction"], "BEARISH")


if __name__ == "__main__":
    unittest.main()
