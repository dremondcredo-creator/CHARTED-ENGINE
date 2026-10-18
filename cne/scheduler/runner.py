"""APScheduler-based release scheduler."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from cne.collectors import pipeline as collection_pipeline
from cne.collectors import snapshots as snap_mod
from cne.config import settings
from cne.core.events import seed
from cne.db.database import connect, init_db
from cne.scheduler.planner import build_preparation_schedule, build_schedule
from cne.scheduler.preparation import capture_preparation


def _log(conn, job, release_id, status, detail=""):
    conn.execute("""
        INSERT INTO scheduler_log (ts_utc, job, release_id, status, detail)
        VALUES (?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(), job, release_id, status, detail))
    conn.commit()


def _job_pre(release_id):
    conn = connect()
    try:
        snap_mod.capture_live(conn, release_id, "PRE")
        _log(conn, "PRE", release_id, "ok")
    except Exception as e:
        _log(conn, "PRE", release_id, "error", str(e))
    finally:
        conn.close()
    try:
        from cne.alerts import dispatcher
        print(f"[PRE] {dispatcher.dispatch_pre(release_id)['status']}")
    except Exception as e:
        print(f"[PRE] failed: {e}")


def _job_release_alert(release_id):
    conn = connect()
    try:
        try:
            snap_mod.capture_live(conn, release_id, "T+5M")
            _log(conn, "T+5M", release_id, "ok")
        except Exception as exc:
            _log(conn, "T+5M", release_id, "error", str(exc))
        runs = collection_pipeline.sync_release_data(conn)
        failures = [run for run in runs if run["status"] == "ERROR"]
        from cne.core import market as market_mod

        market_mod.run_for_release(conn, release_id, "T+5M")
        _log(
            conn,
            "RELEASE_SYNC",
            release_id,
            "error" if failures else "ok",
            "; ".join(run["error"] for run in failures if run["error"]),
        )
    except Exception as exc:
        _log(conn, "RELEASE_SYNC", release_id, "error", str(exc))
        print(f"[REL] refresh failed: {exc}")
    finally:
        conn.close()
    try:
        from cne.alerts import dispatcher
        print(f"[REL] {dispatcher.dispatch_release(release_id)['status']}")
    except Exception as e:
        print(f"[REL] failed: {e}")


def _job_snapshot(release_id, phase):
    conn = connect()
    try:
        snap_mod.capture_live(conn, release_id, phase)
        _log(conn, phase, release_id, "ok")
    except Exception as e:
        _log(conn, phase, release_id, "error", str(e))
    finally:
        conn.close()


def _job_prepare(event_key, window_minutes):
    conn = connect()
    job = f"PREPARE_{window_minutes}M"
    try:
        result = capture_preparation(conn, event_key, window_minutes)
        _log(
            conn,
            job,
            result.get("release_id"),
            "ok" if result["status"] == "COMPLETE" else "incomplete",
            (
                f"{event_key}: {', '.join(result['missing_fields'])}"
                if result["missing_fields"]
                else event_key
            ),
        )
    except Exception as exc:
        _log(conn, job, None, "error", f"{event_key}: {exc}")
        print(f"[{job}] failed for {event_key}: {exc}")
    finally:
        conn.close()


def _job_finalize(release_id):
    conn = connect()
    try:
        from cne.core import market as mm
        from cne.core import structure as sm
        mm.run_for_release(conn, release_id)
        for sym in ("GOLD", "EURUSD"):
            r = sm.classify_release(conn, release_id, sym)
            if r: sm.persist(conn, r)
        _log(conn, "FINALIZE", release_id, "ok")
    except Exception as e:
        _log(conn, "FINALIZE", release_id, "error", str(e))
    finally:
        conn.close()
    try:
        from cne.alerts import dispatcher
        print(f"[FIN] {dispatcher.dispatch_final(release_id)['status']}")
    except Exception as e:
        print(f"[FIN] failed: {e}")


def _queue_release_jobs(sched, conn):
    jobs = build_schedule(conn)
    for job in jobs:
        release_id = job["release_id"]
        timed_jobs = [
            ("pre", job["pre_at"], _job_pre, [release_id]),
            ("rel", job["release_alert_at"], _job_release_alert, [release_id]),
            ("fin", job["finalize_at"], _job_finalize, [release_id]),
        ]
        timed_jobs.extend(
            (
                f"snap_{phase.replace('+', 'plus').replace('M', 'm').replace('H', 'h')}",
                when,
                _job_snapshot,
                [release_id, phase],
            )
            for phase, when in job["snapshot_times"].items()
            if phase != "T+5M"
        )
        for name, when, callback, args in timed_jobs:
            sched.add_job(
                callback,
                DateTrigger(run_date=when),
                args=args,
                id=f"{name}_{release_id}",
                replace_existing=True,
            )
    return jobs


def _queue_preparation_jobs(sched, conn):
    entries = build_preparation_schedule(conn)
    for entry in entries:
        safe_id = hashlib.sha256(entry["event_key"].encode("utf-8")).hexdigest()[:16]
        for window_minutes, when in entry["preparation_times"].items():
            sched.add_job(
                _job_prepare,
                DateTrigger(run_date=when),
                args=[entry["event_key"], window_minutes],
                id=f"prepare_{safe_id}_{window_minutes}m",
                replace_existing=True,
            )
    return entries


def _job_collect(sched):
    conn = init_db()
    try:
        seed(conn)
        runs = collection_pipeline.collect_all(conn)
        errors = [run for run in runs if run["status"] == "ERROR"]
        jobs = _queue_release_jobs(sched, conn)
        preparations = _queue_preparation_jobs(sched, conn)
        _log(
            conn,
            "COLLECT",
            None,
            "error" if errors else "ok",
            f"{len(jobs)} upcoming releases; "
            f"{'; '.join(run['source'] + ': ' + str(run['error']) for run in errors)}",
        )
        print(
            f"[COLLECT] {len(runs) - len(errors)}/{len(runs)} providers succeeded; "
            f"{len(jobs)} upcoming release lifecycles and "
            f"{len(preparations)} preparation events queued; "
        )
    except Exception as exc:
        _log(conn, "COLLECT", None, "error", str(exc))
        print(f"[COLLECT] failed: {exc}")
    finally:
        conn.close()


def run():
    conn = init_db()
    seed(conn)

    sched = BackgroundScheduler(timezone="UTC")
    initial_jobs = _queue_release_jobs(sched, conn)
    initial_preparations = _queue_preparation_jobs(sched, conn)
    conn.close()

    sched.add_job(
        _job_collect,
        IntervalTrigger(minutes=settings.collection_interval_minutes),
        args=[sched],
        id="continuous_collection",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _job_collect(sched)
    print(
        f"\nScheduler polling every {settings.collection_interval_minutes} minutes; "
        f"{len(initial_jobs)} release lifecycles and "
        f"{len(initial_preparations)} preparation events initially queued. Ctrl-C to stop."
    )
    try:
        import time
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
        print("\nStopped.")