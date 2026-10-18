"""Path helpers — everything resolves through settings."""
from cne.config import settings

DATA_DIR = settings.data_dir
DB_PATH = settings.db_path
CALENDAR_CSV = settings.calendar_csv
SCHEMA_SQL = settings.base_dir / "cne" / "db" / "schema.sql"
DASHBOARD_APP = settings.base_dir / "dashboard" / "app.py"