"""Run the dashboard and scheduled collection in one persistent-volume service."""
from __future__ import annotations

import os
from threading import Thread

from cne.scheduler.runner import run as run_scheduler
from cne.web import serve


def main() -> None:
    scheduler = Thread(
        target=run_scheduler,
        name="charted-news-scheduler",
        daemon=True,
    )
    scheduler.start()
    serve(int(os.environ.get("PORT", "8000")), host="0.0.0.0")


if __name__ == "__main__":
    main()
