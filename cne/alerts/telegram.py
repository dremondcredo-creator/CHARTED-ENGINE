"""Telegram Bot API sender."""
from __future__ import annotations

import hashlib
import requests
from cne.config import settings

API_BASE = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000


def _truncate(text, limit=MAX_LEN):
    return text if len(text) <= limit else text[:limit - 20] + "\n\n[...]"


def message_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def is_configured():
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send(text, chat_id=None, *, disable_preview=True, silent=False):
    text = _truncate(text)
    mhash = message_hash(text)

    if not is_configured():
        return {"ok": False, "status": "SKIPPED_NO_TOKEN",
                "error": "token/chat not configured", "message_hash": mhash}
    if settings.alert_dry_run:
        print("\n─── DRY RUN ───")
        print(text)
        print("───────────────\n")
        return {"ok": True, "status": "DRY_RUN", "error": None,
                "message_hash": mhash}

    url = API_BASE.format(token=settings.telegram_bot_token, method="sendMessage")
    try:
        r = requests.post(url, json={
            "chat_id": chat_id or settings.telegram_chat_id,
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
            "disable_notification": silent,
        }, timeout=15)
        body = r.json()
        if body.get("ok"):
            return {"ok": True, "status": "SENT", "error": None, "message_hash": mhash}
        return {"ok": False, "status": "FAILED",
                "error": body.get("description", "unknown"), "message_hash": mhash}
    except Exception as e:
        return {"ok": False, "status": "FAILED", "error": str(e),
                "message_hash": mhash}