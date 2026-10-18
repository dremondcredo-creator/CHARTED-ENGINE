"""System commands."""
import shutil
import subprocess
import sys

from cne import __version__
from cne.config import settings
from cne.db.database import init_db
from cne.paths import DASHBOARD_APP


def cmd_version(_args):
    print(f"charted-news-engine v{__version__}")


def cmd_ui(_args):
    if not DASHBOARD_APP.exists():
        print(f"❌ missing {DASHBOARD_APP}"); sys.exit(1)
    subprocess.run(["streamlit", "run", str(DASHBOARD_APP)], check=False)


def cmd_dashboard(args):
    from cne.web import serve

    if len(args) > 1:
        raise SystemExit("usage: cne dashboard [port]")
    try:
        port = int(args[0]) if args else 8000
    except ValueError as exc:
        raise SystemExit("dashboard port must be an integer") from exc
    serve(port)


def cmd_schedule(_args):
    from cne.scheduler.runner import run
    run()


def cmd_doctor(_args):
    print(f"\n🩺 CNE v{__version__} — doctor\n")
    ok, warn, fail = "✅", "⚠️ ", "❌"
    v = sys.version_info
    print(f"  {ok if v >= (3, 10) else fail} Python {v.major}.{v.minor}.{v.micro}")
    print(f"  {ok} data dir: {settings.data_dir}")
    print(f"  {ok if settings.calendar_csv.exists() else warn} calendar.csv")
    print(
        f"  {ok if settings.alpha_vantage_api_key else warn} "
        "ALPHA_VANTAGE_API_KEY"
    )
    print(f"  {ok if settings.fred_api_key else warn} FRED_API_KEY")
    print(f"  {ok if settings.bea_api_key else warn} BEA_API_KEY")
    try:
        conn = init_db()
        n = conn.execute("SELECT COUNT(*) FROM event_definitions").fetchone()[0]
        print(f"  {ok} DB ready ({n} events)")
    except Exception as e:
        print(f"  {fail} DB error: {e}")
    print(f"  {ok} public and optional keyed data connectors")
    print(f"  {ok if settings.telegram_bot_token else warn} TELEGRAM_BOT_TOKEN")
    for pkg in ("requests", "streamlit", "apscheduler"):
        try:
            __import__(pkg); print(f"  {ok} {pkg}")
        except ImportError:
            print(f"  {fail} {pkg}")
    print(f"  {ok if shutil.which('streamlit') else fail} streamlit on PATH")
    print()