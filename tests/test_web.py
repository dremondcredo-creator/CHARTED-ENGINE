import json
import http.client
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cne import web, web_auth
from cne.core import structure
from cne.core.events import seed
from cne.db import database
from cne.scheduler.planner import build_schedule


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_settings = database.settings
        self.original_web_settings = web.settings
        self.original_auth_settings = web_auth.settings
        test_root = Path(self.temp_dir.name)
        database.settings = replace(
            database.settings,
            data_dir=test_root,
            db_path=test_root / "test.db",
            calendar_csv=test_root / "calendar.csv",
        )
        web.settings = database.settings
        web_auth.settings = database.settings
        conn = database.init_db()
        seed(conn)
        conn.close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.DashboardHandler)
        self.server.daemon_threads = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        database.settings = self.original_db_settings
        web.settings = self.original_web_settings
        web_auth.settings = self.original_auth_settings
        self.temp_dir.cleanup()

    def get_json(self, path):
        with urlopen(f"{self.base_url}{path}", timeout=3) as response:
            return json.load(response)

    def post_json(self, path, payload):
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": self.base_url,
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def test_dashboard_reads_initialized_engine_database(self):
        dashboard = self.get_json("/api/dashboard")

        self.assertEqual(dashboard["system"]["database"], "SQLite WAL")
        self.assertEqual(dashboard["counts"]["releases"], 0)
        self.assertGreater(dashboard["counts"]["event_definitions"], 0)
        self.assertEqual(len(dashboard["layers"]), 5)
        self.assertEqual(dashboard["calendar"], [])
        self.assertEqual(dashboard["market_data"], [])
        self.assertNotIn("TRADING_ECONOMICS_API_KEY", dashboard["system"]["credentials"])
        self.assertNotIn("TWELVE_DATA_API_KEY", dashboard["system"]["credentials"])
        self.assertFalse(dashboard["system"]["credentials"]["ALPHA_VANTAGE_API_KEY"])
        self.assertFalse(dashboard["system"]["credentials"]["FRED_API_KEY"])
        self.assertFalse(dashboard["system"]["credentials"]["BEA_API_KEY"])
        self.assertEqual(
            [source["id"] for source in dashboard["collection"]["sources"]],
            [
                "biquote_calendar", "financecalendar", "calendar_csv", "bls", "treasury", "eurostat",
                "ecb_policy", "ecb_fx", "gold_quote", "alpha_vantage", "bank_news",
                "biquote", "fed_policy", "fred", "bea", "intraday_yields",
            ],
        )
        gold_source = next(
            source
            for source in dashboard["collection"]["sources"]
            if source["id"] == "gold_quote"
        )
        self.assertIn("unverified", gold_source["limitation"])
        biquote_source = next(
            source
            for source in dashboard["collection"]["sources"]
            if source["id"] == "biquote"
        )
        self.assertEqual(biquote_source["latest_run"]["status"], "NOT_COLLECTED")
        fed_policy_source = next(
            source
            for source in dashboard["collection"]["sources"]
            if source["id"] == "fed_policy"
        )
        self.assertEqual(fed_policy_source["latest_run"]["status"], "NOT_CONFIGURED")
        fred_source = next(
            source
            for source in dashboard["collection"]["sources"]
            if source["id"] == "fred"
        )
        self.assertEqual(fred_source["latest_run"]["status"], "NOT_CONFIGURED")
        alpha_source = next(
            source
            for source in dashboard["collection"]["sources"]
            if source["id"] == "alpha_vantage"
        )
        self.assertEqual(alpha_source["latest_run"]["status"], "NOT_CONFIGURED")
        bea_source = next(
            source
            for source in dashboard["collection"]["sources"]
            if source["id"] == "bea"
        )
        self.assertEqual(bea_source["latest_run"]["status"], "NOT_CONFIGURED")
        self.assertEqual(dashboard["collection"]["calendar_events"], [])

    def test_google_oauth_protects_dashboard_and_authenticates_allowlisted_user(self):
        web.settings = replace(
            web.settings,
            auth_enabled=True,
            google_oauth_client_id="client-id",
            google_oauth_client_secret="client-secret",
            oauth_cookie_secret="test-cookie-secret-" + ("x" * 40),
            oauth_redirect_uri="https://engine.example.com/auth/callback",
            oauth_allowed_emails=("dremondcredo20@gmail.com",),
        )
        web_auth.settings = web.settings

        health = urlopen(f"{self.base_url}/api/health", timeout=3)
        self.assertEqual(health.status, 200)
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(f"{self.base_url}/api/dashboard", timeout=3)
        self.assertEqual(unauthorized.exception.code, 401)

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request("GET", "/auth/login")
        login_response = connection.getresponse()
        self.assertEqual(login_response.status, 302)
        authorization = urlsplit(login_response.getheader("Location"))
        query = parse_qs(authorization.query)
        self.assertEqual(
            authorization.netloc,
            "accounts.google.com",
        )
        self.assertEqual(query["client_id"], ["client-id"])
        self.assertEqual(query["redirect_uri"], ["https://engine.example.com/auth/callback"])
        flow_cookie = next(
            value.split(";", 1)[0]
            for name, value in login_response.getheaders()
            if name.lower() == "set-cookie" and value.startswith(web_auth.FLOW_COOKIE)
        )
        connection.close()

        with (
            patch(
                "cne.web_auth.requests.post",
                return_value=Mock(
                    json=lambda: {"id_token": "verified-token"},
                    raise_for_status=lambda: None,
                ),
            ),
            patch(
                "cne.web_auth.id_token.verify_oauth2_token",
                return_value={
                    "email": "dremondcredo20@gmail.com",
                    "email_verified": True,
                    "nonce": query["nonce"][0],
                },
            ),
        ):
            callback_query = urlencode(
                {"code": "authorization-code", "state": query["state"][0]}
            )
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_port, timeout=3
            )
            with self.assertLogs(web.log, level="INFO") as request_logs:
                connection.request(
                    "GET",
                    f"/auth/callback?{callback_query}",
                    headers={"Cookie": flow_cookie},
                )
                callback_response = connection.getresponse()
            self.assertEqual(callback_response.status, 302)
            self.assertEqual(callback_response.getheader("Location"), "/")
            self.assertTrue(
                all("authorization-code" not in line for line in request_logs.output)
            )
            session_cookie = next(
                value.split(";", 1)[0]
                for name, value in callback_response.getheaders()
                if name.lower() == "set-cookie"
                and value.startswith(web_auth.SESSION_COOKIE)
            )
            connection.close()

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request("GET", "/api/dashboard", headers={"Cookie": session_cookie})
        authenticated = connection.getresponse()
        self.assertEqual(authenticated.status, 200)
        connection.close()

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request(
            "POST",
            "/api/collect",
            headers={
                "Cookie": session_cookie,
                "Origin": self.base_url,
                "Content-Length": "0",
            },
        )
        csrf_rejection = connection.getresponse()
        self.assertEqual(csrf_rejection.status, 403)
        connection.close()

    def test_google_oauth_fails_closed_when_provider_credentials_are_missing(self):
        web.settings = replace(web.settings, auth_enabled=True)
        web_auth.settings = web.settings

        with self.assertRaises(HTTPError) as protected:
            urlopen(f"{self.base_url}/", timeout=3)
        self.assertEqual(protected.exception.code, 503)
        self.assertEqual(self.get_json("/api/health")["status"], "ok")

    def test_dashboard_exposes_biquote_quote_provenance_and_closed_status(self):
        conn = database.connect()
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            database.record_observation(
                conn,
                domain="market",
                indicator="EURUSD_BIQUOTE_SPOT",
                value_numeric=1.1239,
                unit="USD per EUR",
                source="BiQuote indicative broker feed",
                source_url="https://biquote.io/api/latest?symbols=EURUSD",
                observed_utc=timestamp,
                publication_status="STALE",
                observation_period=timestamp,
                metadata={
                    "source_timestamp_utc": timestamp,
                    "retrieved_at_utc": timestamp,
                    "data_status": "STALE",
                    "is_delayed": True,
                    "market_state": "closed",
                    "quote_age_seconds": 7200,
                    "bid": 1.1238,
                    "ask": 1.1240,
                    "spread": 0.0002,
                    "day_diff_percent": 0.4,
                },
            )
            row = next(
                item
                for item in web._latest_market_data(conn)
                if item["symbol"] == "EURUSD"
            )
        finally:
            conn.close()

        self.assertEqual(row["phase"], "STALE")
        self.assertEqual(row["market_state"], "closed")
        self.assertEqual(row["quote_age_seconds"], 7200)
        self.assertIsNone(row["change_pct"])
        self.assertEqual(row["bid"], 1.1238)
        self.assertEqual(row["ask"], 1.124)

    def test_collect_endpoint_runs_providers_and_returns_source_coverage(self):
        runs = [
            {
                "source": "financecalendar",
                "status": "OK",
                "records": 2,
                "error": None,
                "finished_utc": datetime.now(timezone.utc).isoformat(),
            }
        ]
        with patch.object(web, "collect_all", return_value=runs):
            response = self.post_json("/api/collect", {})

        self.assertEqual(response["runs"], runs)
        self.assertEqual(
            [source["id"] for source in response["collection"]["sources"]],
            [
                "biquote_calendar", "financecalendar", "calendar_csv", "bls", "treasury", "eurostat",
                "ecb_policy", "ecb_fx", "gold_quote", "alpha_vantage", "bank_news",
                "biquote", "fed_policy", "fred", "bea", "intraday_yields",
            ],
        )

    def test_decision_brief_follows_the_five_questions_and_labels_missing_evidence(self):
        conn = database.connect()
        try:
            brief = web._decision_brief(
                conn,
                {
                    "id": 1,
                    "event_code": "US_CPI_YOY",
                    "name": "US CPI YoY",
                    "country": "US",
                    "period": "2026-11",
                    "forecast": None,
                    "previous": 2.5,
                    "actual": None,
                    "impact": None,
                },
                {"label": "NEUTRAL", "total": 0, "n_releases": 0, "window_days": 30},
            )
        finally:
            conn.close()

        questions = brief["questions"]
        self.assertEqual([question["id"] for question in questions], [1, 2, 3, 4, 5])
        self.assertTrue(questions[0]["answer"].startswith("No actual-versus-forecast"))
        self.assertEqual(questions[2]["status"], "AWAITING DATA")
        self.assertEqual(questions[3]["status"], "INSUFFICIENT")
        self.assertEqual(questions[4]["status"], "INSUFFICIENT")
        self.assertTrue(questions[4]["evidence"]["uncertainties"])

    def test_us_release_interpretation_distinguishes_actual_fed_evidence(self):
        conn = database.connect()
        try:
            brief = web._decision_brief(
                conn,
                {
                    "id": 1,
                    "event_code": "US_CPI_YOY",
                    "name": "US CPI YoY",
                    "country": "US",
                    "period": "2026-10",
                    "forecast": 3.0,
                    "previous": 2.9,
                    "actual": 3.2,
                    "raw_surprise": 0.2,
                    "direction": "UPSIDE",
                    "magnitude": "LARGE",
                    "impact": "BULLISH",
                    "data_quality_status": "VERIFIED",
                },
                {"label": "HAWKISH", "total": 4.5, "n_releases": 4, "window_days": 30},
            )
        finally:
            conn.close()

        self.assertIn("Actual 3.2 % vs 3 % forecast", brief["questions"][0]["answer"])
        self.assertIn("does not establish a Fed policy shift", brief["questions"][1]["answer"])
        self.assertIn("USD-supportive", brief["questions"][1]["answer"])

    def test_non_us_release_does_not_infer_a_fed_or_usd_signal(self):
        conn = database.connect()
        try:
            brief = web._decision_brief(
                conn,
                {
                    "id": 1,
                    "event_code": "EU_CPI_YOY",
                    "name": "Eurozone CPI YoY",
                    "country": "EU",
                    "period": "2026-10",
                    "forecast": 2.0,
                    "previous": 2.2,
                    "actual": 1.8,
                    "raw_surprise": -0.2,
                    "direction": "DOWNSIDE",
                    "magnitude": "MODERATE",
                    "impact": "BEARISH",
                    "data_quality_status": "VERIFIED",
                },
                {"label": "HAWKISH", "total": 4.5, "n_releases": 4, "window_days": 30},
            )
        finally:
            conn.close()

        self.assertIn("does not establish a policy shift by ECB", brief["questions"][1]["answer"])
        self.assertIn("or a direct Federal Reserve/USD signal", brief["questions"][1]["answer"])
        self.assertEqual(brief["questions"][2]["status"], "MODEL LIMITATION")
        self.assertIn("Not scored", brief["questions"][2]["answer"])

    def test_structure_verdicts_can_be_saved_for_both_instruments(self):
        conn = database.connect()
        try:
            database.upsert_release(conn, event_code="US_CPI_YOY", period="2026-10")
            release_id = conn.execute(
                "SELECT id FROM releases WHERE event_code=? AND period=?",
                ("US_CPI_YOY", "2026-10"),
            ).fetchone()["id"]
            for symbol, event_class, held, score in (
                ("GOLD", "BREAK_UP", 1, 1.5),
                ("EURUSD", "NO_TEST", None, -0.5),
            ):
                structure.persist(conn, {
                    "release_id": release_id,
                    "symbol": symbol,
                    "phase": "T+30M",
                    "nearest_level": None,
                    "nearest_kind": None,
                    "distance_atr": None,
                    "structure_before": "RANGE",
                    "structure_after": "BULLISH",
                    "event_class": event_class,
                    "break_pct": 0.2 if event_class == "BREAK_UP" else None,
                    "held_retest": held,
                    "confirmation": "CONFIRMS",
                    "confirmation_score": score,
                    "notes": "held level",
                })
            verdicts = conn.execute(
                "SELECT symbol FROM structure_verdicts WHERE release_id=? ORDER BY symbol",
                (release_id,),
            ).fetchall()
            decision = web._structure_evidence(conn, release_id, "BULLISH")
        finally:
            conn.close()

        self.assertEqual([row["symbol"] for row in verdicts], ["EURUSD", "GOLD"])
        self.assertEqual(decision["preferred"], "GOLD")
        self.assertEqual(decision["confidence"], "EVENT STRUCTURE")

    def test_existing_single_instrument_schema_migrates_and_preserves_verdict(self):
        conn = database.connect()
        try:
            database.upsert_release(conn, event_code="US_CPI_YOY", period="2026-10")
            release_id = conn.execute(
                "SELECT id FROM releases WHERE event_code=? AND period=?",
                ("US_CPI_YOY", "2026-10"),
            ).fetchone()["id"]
            conn.execute("DROP TABLE structure_verdicts")
            conn.execute("""
                CREATE TABLE structure_verdicts (
                    release_id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    phase TEXT,
                    nearest_level REAL,
                    nearest_kind TEXT,
                    distance_atr REAL,
                    structure_before TEXT,
                    structure_after TEXT,
                    event_class TEXT,
                    break_pct REAL,
                    held_retest INTEGER,
                    confirmation TEXT,
                    confirmation_score REAL,
                    notes TEXT,
                    computed_utc TEXT NOT NULL,
                    FOREIGN KEY(release_id) REFERENCES releases(id)
                )
            """)
            conn.execute("""
                INSERT INTO structure_verdicts
                (release_id, symbol, phase, event_class, confirmation,
                 confirmation_score, notes, computed_utc)
                VALUES (?, 'GOLD', 'T+30M', 'BREAK_UP', 'CONFIRMS', 1.5,
                        'legacy verdict', '2026-10-18T00:00:00+00:00')
            """, (release_id,))
            conn.commit()
        finally:
            conn.close()

        conn = database.init_db()
        try:
            structure.persist(conn, {
                "release_id": release_id,
                "symbol": "EURUSD",
                "phase": "T+30M",
                "nearest_level": None,
                "nearest_kind": None,
                "distance_atr": None,
                "structure_before": "RANGE",
                "structure_after": "BEARISH",
                "event_class": "BREAK_DOWN",
                "break_pct": -0.2,
                "held_retest": 1,
                "confirmation": "CONFIRMS",
                "confirmation_score": -1.5,
                "notes": "held level",
            })
            verdicts = conn.execute(
                "SELECT symbol, notes FROM structure_verdicts WHERE release_id=? ORDER BY symbol",
                (release_id,),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual([(row["symbol"], row["notes"]) for row in verdicts], [
            ("EURUSD", "held level"),
            ("GOLD", "legacy verdict"),
        ])

    def test_schedule_plans_release_lifecycle_offsets(self):
        conn = database.connect()
        try:
            release_time = (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).isoformat()
            database.upsert_release(
                conn,
                event_code="US_CPI_YOY",
                period="2026-11",
                release_time_utc=release_time,
            )
            job = build_schedule(conn)[0]
            self.assertEqual((job["pre_at"] - job["ts"]).total_seconds(), -15 * 60)
            self.assertEqual((job["release_alert_at"] - job["ts"]).total_seconds(), 5 * 60)
            self.assertEqual(
                (job["snapshot_times"]["T+5M"] - job["ts"]).total_seconds(),
                5 * 60,
            )
            self.assertEqual(
                (job["snapshot_times"]["T+2H"] - job["ts"]).total_seconds(),
                2 * 60 * 60,
            )
            self.assertEqual((job["finalize_at"] - job["ts"]).total_seconds(), 2 * 60 * 60 + 10 * 60)
        finally:
            conn.close()

    def test_dashboard_serializes_nested_schedule_timestamps(self):
        conn = database.connect()
        try:
            release_time = (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).isoformat()
            database.upsert_release(
                conn,
                event_code="US_CPI_YOY",
                period="2026-11",
                release_time_utc=release_time,
            )
        finally:
            conn.close()

        schedule = self.get_json("/api/dashboard")["schedule"]
        self.assertEqual(len(schedule), 1)
        self.assertIsInstance(schedule[0]["ts"], str)
        self.assertIsInstance(schedule[0]["snapshot_times"]["T0"], str)
        self.assertIsInstance(schedule[0]["snapshot_times"]["T+2H"], str)

    def test_calendar_rejects_invalid_filters(self):
        for query in ("tier=not-a-tier", "country=ZZ", "tier=9"):
            with self.subTest(query=query), self.assertRaises(HTTPError) as error:
                urlopen(f"{self.base_url}/api/calendar?{query}", timeout=3)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

    def test_simulation_uses_engine_calculator_and_can_be_saved(self):
        result = self.post_json(
            "/api/simulate",
            {
                "symbol": "EURUSD",
                "direction": "LONG",
                "balance": 10000,
                "risk_pct": 1,
                "entry": 1.1,
                "stop": 1.09,
                "target": 1.12,
                "save": True,
            },
        )

        self.assertTrue(result["saved"])
        self.assertEqual(result["result"]["lot_size"], 0.1)
        self.assertGreater(result["result"]["simulation_id"], 0)
        self.assertEqual(len(self.get_json("/api/simulations")["simulations"]), 1)

    def test_simulation_rejects_invalid_field_types(self):
        with self.assertRaises(HTTPError) as error:
            self.post_json(
                "/api/simulate",
                {
                    "symbol": 123,
                    "balance": 10000,
                    "risk_pct": 1,
                    "entry": 1.1,
                    "stop": 1.09,
                },
            )

        self.assertEqual(error.exception.code, 400)
        self.assertIn("symbol must be a string", error.exception.read().decode())
        error.exception.close()

    def test_server_sent_events_notify_on_engine_writes(self):
        stream = urlopen(f"{self.base_url}/api/events", timeout=3)
        try:
            self.assertIn(b"event: connected", stream.readline())
            self.assertIn(b"data:", stream.readline())
            self.post_json(
                "/api/simulate",
                {
                    "symbol": "EURUSD",
                    "direction": "LONG",
                    "balance": 10000,
                    "risk_pct": 1,
                    "entry": 1.1,
                    "stop": 1.09,
                    "save": True,
                },
            )
            lines = []
            for _ in range(5):
                line = stream.readline()
                lines.append(line)
                if b"event: engine_update" in line:
                    break
            self.assertIn(b"event: engine_update", b"".join(lines))
        finally:
            stream.close()


if __name__ == "__main__":
    unittest.main()
