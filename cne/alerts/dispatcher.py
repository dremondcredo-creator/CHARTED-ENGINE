"""Alert orchestration + dedup."""
from __future__ import annotations

from datetime import datetime, timezone
from cne.config import settings
from cne.db.database import connect
from cne.alerts import composer, telegram


def _already(conn, release_id, kind, symbol=None):
    r = conn.execute("""
        SELECT status FROM alerts_log
        WHERE release_id=? AND kind=? AND COALESCE(symbol,'')=COALESCE(?,'')
    """, (release_id, kind, symbol)).fetchone()
    return r is not None and r["status"] in ("SENT", "DRY_RUN")


def _log(conn, release_id, kind, symbol, mhash, result):
    conn.execute("""
        INSERT INTO alerts_log
          (release_id, kind, symbol, chat_id, message_hash, status, error, ts_utc)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id, kind, symbol) DO UPDATE SET
          message_hash=excluded.message_hash, status=excluded.status,
          error=excluded.error, ts_utc=excluded.ts_utc
    """, (release_id, kind, symbol, settings.telegram_chat_id, mhash,
          result["status"], result.get("error"),
          datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _tier(conn, release_id):
    r = conn.execute("""
        SELECT e.tier FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        WHERE r.id = ?
    """, (release_id,)).fetchone()
    return r["tier"] if r else 99


def _send(conn, release_id, kind, text, symbol=None):
    if not text:
        return {"status": "SKIPPED_EMPTY"}
    if _already(conn, release_id, kind, symbol):
        return {"status": "SKIPPED_DUP"}
    result = telegram.send(text)
    _log(conn, release_id, kind, symbol, result.get("message_hash", ""), result)
    return result


def dispatch_pre(release_id):
    conn = connect()
    try:
        if _tier(conn, release_id) > settings.alert_tier_min:
            return {"status": "SKIPPED_TIER"}
        return _send(conn, release_id, "PRE", composer.compose_pre(release_id))
    finally:
        conn.close()


def dispatch_release(release_id):
    conn = connect()
    try:
        if _tier(conn, release_id) > settings.alert_tier_min:
            return {"status": "SKIPPED_TIER"}
        return _send(conn, release_id, "RELEASE", composer.compose_release(release_id))
    finally:
        conn.close()


def dispatch_final(release_id):
    conn = connect()
    try:
        if _tier(conn, release_id) > settings.alert_tier_min:
            return {"status": "SKIPPED_TIER"}
        return _send(conn, release_id, "FINAL", composer.compose_final(release_id))
    finally:
        conn.close()


def dispatch_test():
    text = (f"<b>🚨 CNE TEST</b>\n\nTelegram wired up.\n"
            f"<code>{datetime.now(timezone.utc).isoformat()}</code>")
    result = telegram.send(text)
    conn = connect()
    try:
        _log(conn, None, "TEST", None, result.get("message_hash", ""), result)
    finally:
        conn.close()
    return result