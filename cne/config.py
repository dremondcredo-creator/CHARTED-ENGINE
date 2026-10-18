"""Central configuration — reads from env, loaded once."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(k: str, d: str = "") -> str:
    return os.environ.get(k, d).strip()


def _env_bool(k: str, d: bool = False) -> bool:
    v = _env(k)
    return v.lower() in ("1", "true", "yes", "on") if v else d


def _env_int(k: str, d: int) -> int:
    try:
        return int(_env(k) or d)
    except ValueError:
        return d


def _env_float(k: str, d: float) -> float:
    try:
        return float(_env(k) or d)
    except ValueError:
        return d


def _preparation_windows() -> tuple[int, ...]:
    raw = _env("CNE_PREPARATION_WINDOWS_MINUTES", "60,15,1")
    try:
        windows = tuple(
            sorted(
                {
                    int(value.strip())
                    for value in raw.split(",")
                    if value.strip()
                },
                reverse=True,
            )
        )
    except ValueError as exc:
        raise ValueError(
            "CNE_PREPARATION_WINDOWS_MINUTES must be comma-separated positive integers."
        ) from exc
    if not windows or any(window <= 0 or window > 10080 for window in windows):
        raise ValueError(
            "CNE_PREPARATION_WINDOWS_MINUTES must contain values from 1 to 10080."
        )
    return windows


def _try_load_dotenv() -> None:
    for p in (Path.cwd() / ".env",
              Path(__file__).resolve().parent.parent / ".env"):
        if not p.exists():
            continue
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
        break


_try_load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    data_dir: Path
    db_path: Path
    calendar_csv: Path

    telegram_bot_token: str
    telegram_chat_id: str
    alpha_vantage_api_key: str
    fred_api_key: str
    bea_api_key: str

    alert_tier_min: int
    alert_dry_run: bool

    log_level: str
    log_file: str
    dashboard_host: str

    lookback_years: int
    snapshot_days_back: int
    learn_days_back: int
    collection_interval_minutes: int
    preparation_windows_minutes: tuple[int, ...]
    fundamental_event_weights_json: str
    fundamental_decay_half_life_days: float
    fundamental_window_days: int
    surprise_normalization_min_samples: int

    account_size: float
    kelly_fraction: float
    max_risk_pct: float

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def _load() -> Settings:
    base = Path(__file__).resolve().parent.parent
    data_env = _env("CNE_DATA_DIR")
    data_dir = Path(data_env).expanduser().resolve() if data_env else (base / "data")
    db_env = _env("CNE_DB_PATH")
    db_path = Path(db_env).expanduser().resolve() if db_env else (data_dir / "cne.db")

    s = Settings(
        base_dir=base,
        data_dir=data_dir,
        db_path=db_path,
        calendar_csv=data_dir / "calendar.csv",
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        alpha_vantage_api_key=_env("ALPHA_VANTAGE_API_KEY"),
        fred_api_key=_env("FRED_API_KEY"),
        bea_api_key=_env("BEA_API_KEY"),
        alert_tier_min=_env_int("ALERT_TIER_MIN", 1),
        alert_dry_run=_env_bool("ALERT_DRY_RUN", False),
        log_level=_env("CNE_LOG_LEVEL", "INFO").upper(),
        log_file=_env("CNE_LOG_FILE"),
        dashboard_host=_env("CNE_DASHBOARD_HOST", "127.0.0.1"),
        lookback_years=_env_int("CNE_LOOKBACK_YEARS", 3),
        snapshot_days_back=_env_int("CNE_SNAPSHOT_DAYS_BACK", 45),
        learn_days_back=_env_int("CNE_LEARN_DAYS_BACK", 120),
        collection_interval_minutes=max(1, _env_int("CNE_COLLECTION_INTERVAL_MINUTES", 5)),
        preparation_windows_minutes=_preparation_windows(),
        fundamental_event_weights_json=_env(
            "CNE_FUNDAMENTAL_EVENT_WEIGHTS_JSON", "{}"
        ),
        fundamental_decay_half_life_days=max(
            1.0, _env_float("CNE_FUNDAMENTAL_DECAY_HALF_LIFE_DAYS", 14.0)
        ),
        fundamental_window_days=max(1, _env_int("CNE_FUNDAMENTAL_WINDOW_DAYS", 30)),
        surprise_normalization_min_samples=max(
            3, _env_int("CNE_SURPRISE_NORMALIZATION_MIN_SAMPLES", 12)
        ),
        account_size=_env_float("CNE_ACCOUNT_SIZE", 10000.0),
        kelly_fraction=_env_float("CNE_KELLY_FRACTION", 0.5),
        max_risk_pct=_env_float("CNE_MAX_RISK_PCT", 2.0),
    )
    s.ensure_dirs()
    return s


settings = _load()