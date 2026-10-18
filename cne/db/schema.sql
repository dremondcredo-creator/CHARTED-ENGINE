-- ═══════════════════════════════════════════════════════════════════════
--  CHARTED NEWS ENGINE — Unified schema (V1 → V10 + simulator)
-- ═══════════════════════════════════════════════════════════════════════

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ─── Metadata ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ─── Event dictionary ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_definitions (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL,
    category    TEXT NOT NULL,
    unit        TEXT,
    higher_is   TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    inline_tol  REAL DEFAULT 0.05,
    large_thr   REAL DEFAULT 0.20,
    notes       TEXT
);

-- ─── Releases & surprises ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS releases (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_code        TEXT NOT NULL,
    period            TEXT NOT NULL,
    release_time_utc  TEXT,
    previous          REAL,
    forecast          REAL,
    actual            REAL,
    revised_previous  REAL,
    actual_source     TEXT,
    forecast_source   TEXT,
    first_seen_utc    TEXT NOT NULL,
    updated_utc       TEXT NOT NULL,
    UNIQUE(event_code, period),
    FOREIGN KEY(event_code) REFERENCES event_definitions(code)
);
CREATE INDEX IF NOT EXISTS idx_releases_time
    ON releases(release_time_utc DESC);

-- ─── Workflow 01: source observations and collection history ──────────
CREATE TABLE IF NOT EXISTS data_observations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    domain              TEXT NOT NULL,
    indicator           TEXT NOT NULL,
    observation_period  TEXT,
    value_numeric       REAL,
    value_text          TEXT,
    unit                TEXT NOT NULL,
    source              TEXT NOT NULL,
    source_url          TEXT,
    observed_utc        TEXT NOT NULL,
    published_utc       TEXT,
    fetched_utc         TEXT NOT NULL,
    publication_status  TEXT NOT NULL,
    metadata_json       TEXT
);

-- ─── Normalized economic-calendar feed ────────────────────────────────
CREATE TABLE IF NOT EXISTS calendar_events (
    event_id                 TEXT PRIMARY KEY,
    event_name               TEXT NOT NULL,
    currency                 TEXT,
    country                  TEXT,
    release_time_utc         TEXT,
    period                   TEXT,
    forecast_text            TEXT,
    forecast                 REAL,
    previous_text            TEXT,
    previous                 REAL,
    actual_text              TEXT,
    actual                   REAL,
    revised_previous_text    TEXT,
    revised_previous         REAL,
    unit                     TEXT,
    importance               TEXT,
    source                   TEXT NOT NULL,
    source_url               TEXT,
    source_timestamp_utc     TEXT,
    retrieved_at_utc         TEXT NOT NULL,
    release_status           TEXT NOT NULL,
    is_revision              INTEGER NOT NULL DEFAULT 0,
    raw_json                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_release_time
    ON calendar_events(release_time_utc);

CREATE INDEX IF NOT EXISTS idx_data_observations_domain_time
    ON data_observations(domain, observed_utc DESC);
CREATE INDEX IF NOT EXISTS idx_data_observations_indicator
    ON data_observations(indicator, observation_period, source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_data_observations_news_item
    ON data_observations(source, indicator, observation_period)
    WHERE domain = 'news';

CREATE TABLE IF NOT EXISTS collection_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    finished_utc TEXT NOT NULL,
    status      TEXT NOT NULL,
    records     INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_collection_runs_source_time
    ON collection_runs(source, finished_utc DESC);

CREATE TABLE IF NOT EXISTS surprises (
    release_id   INTEGER PRIMARY KEY,
    raw_surprise REAL,
    normalized_surprise REAL,
    historical_scale REAL,
    normalization_n INTEGER,
    normalization_status TEXT,
    data_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    direction    TEXT,
    magnitude    TEXT,
    impact       TEXT,
    computed_utc TEXT,
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

CREATE TABLE IF NOT EXISTS release_monitor_results (
    release_id              INTEGER PRIMARY KEY,
    actual_value            REAL,
    forecast_at_release     REAL,
    previous_value          REAL,
    revised_previous        REAL,
    unit                    TEXT,
    reporting_period        TEXT NOT NULL,
    actual_source           TEXT,
    source_record_id        TEXT,
    publication_timestamp_utc TEXT,
    retrieved_at_utc        TEXT,
    forecast_source         TEXT,
    forecast_retrieved_at_utc TEXT,
    previous_source         TEXT,
    previous_retrieved_at_utc TEXT,
    is_revision             INTEGER NOT NULL DEFAULT 0,
    data_status             TEXT NOT NULL,
    status_details_json     TEXT NOT NULL,
    raw_surprise            REAL,
    normalized_surprise     REAL,
    historical_scale        REAL,
    normalization_n         INTEGER,
    normalization_status    TEXT,
    economic_direction      TEXT,
    currency_impact         TEXT,
    updated_at_utc          TEXT NOT NULL,
    FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE INDEX IF NOT EXISTS idx_release_monitor_status
    ON release_monitor_results(data_status, updated_at_utc DESC);

-- ─── Market snapshots (V3) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id   INTEGER NOT NULL,
    phase        TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    price        REAL,
    change_pct   REAL,
    change_abs   REAL,
    bar_ts_utc   TEXT,
    source       TEXT,
    captured_utc TEXT NOT NULL,
    UNIQUE(release_id, phase, symbol),
    FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_release
    ON price_snapshots(release_id, phase);

-- ─── Immutable pre-release preparation snapshots ─────────────────────
CREATE TABLE IF NOT EXISTS pre_release_preparations (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key                     TEXT NOT NULL,
    window_minutes                INTEGER NOT NULL,
    release_id                    INTEGER,
    event_name                    TEXT NOT NULL,
    currency                      TEXT,
    country                       TEXT,
    period                        TEXT,
    scheduled_release_utc         TEXT NOT NULL,
    captured_at_utc               TEXT NOT NULL,
    source                        TEXT NOT NULL,
    source_retrieved_at_utc       TEXT,
    source_timestamp_utc          TEXT,
    forecast                      REAL,
    previous                      REAL,
    revised_previous              REAL,
    is_revision                   INTEGER NOT NULL DEFAULT 0,
    forecast_available_pre_release INTEGER NOT NULL,
    data_quality_status           TEXT NOT NULL,
    missing_fields_json           TEXT NOT NULL,
    policy_context_json           TEXT NOT NULL,
    fundamental_context_json      TEXT NOT NULL,
    scenarios_json                TEXT NOT NULL,
    UNIQUE(event_key, window_minutes),
    FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE INDEX IF NOT EXISTS idx_pre_release_preparations_event_time
    ON pre_release_preparations(scheduled_release_utc, captured_at_utc);

CREATE TABLE IF NOT EXISTS pre_release_market_snapshots (
    event_key             TEXT NOT NULL,
    window_minutes        INTEGER NOT NULL,
    instrument            TEXT NOT NULL,
    source_symbol         TEXT NOT NULL,
    price                 REAL,
    bid                   REAL,
    ask                   REAL,
    spread                REAL,
    timeframe             TEXT,
    source                TEXT,
    source_timestamp_utc  TEXT,
    captured_at_utc       TEXT NOT NULL,
    data_status           TEXT NOT NULL,
    is_delayed            INTEGER,
    source_quality        TEXT NOT NULL,
    quality_details_json  TEXT NOT NULL,
    PRIMARY KEY(event_key, window_minutes, instrument),
    FOREIGN KEY(event_key, window_minutes)
        REFERENCES pre_release_preparations(event_key, window_minutes)
);

CREATE TRIGGER IF NOT EXISTS trg_pre_release_preparations_immutable_update
BEFORE UPDATE ON pre_release_preparations
BEGIN
    SELECT RAISE(ABORT, 'pre-release preparation snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_pre_release_preparations_immutable_delete
BEFORE DELETE ON pre_release_preparations
BEGIN
    SELECT RAISE(ABORT, 'pre-release preparation snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_pre_release_market_immutable_update
BEFORE UPDATE ON pre_release_market_snapshots
BEGIN
    SELECT RAISE(ABORT, 'pre-release market snapshots are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_pre_release_market_immutable_delete
BEFORE DELETE ON pre_release_market_snapshots
BEGIN
    SELECT RAISE(ABORT, 'pre-release market snapshots are immutable');
END;

CREATE TABLE IF NOT EXISTS market_verdicts (
    release_id      INTEGER PRIMARY KEY,
    usd_dir         TEXT,
    usd_change_pct  REAL,
    yield_dir       TEXT,
    yield_change_bp REAL,
    gold_dir        TEXT,
    gold_change_pct REAL,
    eurusd_dir      TEXT,
    eurusd_change_pct REAL,
    fundamental_dir TEXT,
    market_alignment TEXT,
    market_score    REAL,
    market_light    TEXT,
    notes           TEXT,
    computed_utc    TEXT NOT NULL,
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

-- ─── Macro scores & snapshots (V2) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS macro_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT NOT NULL,
    country         TEXT NOT NULL,
    inflation_score INTEGER DEFAULT 0,
    labour_score    INTEGER DEFAULT 0,
    growth_score    INTEGER DEFAULT 0,
    policy_score    INTEGER DEFAULT 0,
    yields_score    INTEGER DEFAULT 0,
    total           INTEGER DEFAULT 0,
    label           TEXT
);

CREATE TABLE IF NOT EXISTS macro_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT NOT NULL,
    country         TEXT NOT NULL,
    window_days     INTEGER NOT NULL,
    n_releases      INTEGER NOT NULL,
    inflation_score REAL DEFAULT 0,
    labour_score    REAL DEFAULT 0,
    growth_score    REAL DEFAULT 0,
    total_score     REAL DEFAULT 0,
    bias_label      TEXT,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_macro_snapshots_country_ts
    ON macro_snapshots(country, ts_utc DESC);

-- ─── Trend snapshots (V2) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trend_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    event_code   TEXT NOT NULL,
    direction    TEXT,
    slope        REAL,
    norm_slope   REAL,
    streak       INTEGER,
    streak_dir   TEXT,
    n_obs        INTEGER,
    FOREIGN KEY(event_code) REFERENCES event_definitions(code)
);

CREATE TABLE IF NOT EXISTS labour_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period       TEXT NOT NULL,
    country      TEXT NOT NULL DEFAULT 'US',
    verdict      TEXT,
    color        TEXT,
    implication  TEXT,
    components   TEXT,
    ts_utc       TEXT NOT NULL,
    UNIQUE(period, country)
);

-- ─── Levels & structure (V4) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pivots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    ts_utc       TEXT NOT NULL,
    price        REAL NOT NULL,
    strength     INTEGER,
    detected_utc TEXT NOT NULL,
    UNIQUE(symbol, timeframe, kind, ts_utc)
);

CREATE TABLE IF NOT EXISTS key_levels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    center       REAL NOT NULL,
    touches      INTEGER NOT NULL,
    first_ts_utc TEXT,
    last_ts_utc  TEXT,
    atr_at_detect REAL,
    strength     REAL,
    detected_utc TEXT NOT NULL,
    UNIQUE(symbol, timeframe, kind, center)
);

CREATE TABLE IF NOT EXISTS structure_verdicts (
    release_id        INTEGER NOT NULL,
    symbol            TEXT NOT NULL,
    phase             TEXT,
    nearest_level     REAL,
    nearest_kind      TEXT,
    distance_atr      REAL,
    structure_before  TEXT,
    structure_after   TEXT,
    event_class       TEXT,
    break_pct         REAL,
    held_retest       INTEGER,
    confirmation      TEXT,
    confirmation_score REAL,
    notes             TEXT,
    computed_utc      TEXT NOT NULL,
    PRIMARY KEY(release_id, symbol),
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

-- ─── Chop (V3 + V4) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chop_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    timeframe    TEXT,
    chop_score   REAL,
    chop_label   TEXT,
    atr_norm     REAL,
    wick_ratio   REAL,
    dir_change_rate REAL,
    trend_r2     REAL,
    n_bars       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chop_symbol_ts
    ON chop_snapshots(symbol, ts_utc DESC);

CREATE TABLE IF NOT EXISTS chop_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    chop_score   REAL,
    chop_label   TEXT
);
CREATE INDEX IF NOT EXISTS idx_chop_history_symbol_ts
    ON chop_history(symbol, ts_utc DESC);

-- ─── Regimes (V7.5) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS macro_regimes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    country      TEXT NOT NULL,
    regime_id    INTEGER NOT NULL,
    start_ts_utc TEXT NOT NULL,
    end_ts_utc   TEXT,
    label        TEXT NOT NULL,
    score_at_start REAL,
    score_at_end   REAL,
    n_snapshots  INTEGER,
    detected_utc TEXT NOT NULL,
    UNIQUE(country, regime_id)
);

CREATE TABLE IF NOT EXISTS release_regimes (
    release_id   INTEGER PRIMARY KEY,
    country      TEXT NOT NULL,
    regime_id    INTEGER NOT NULL,
    regime_label TEXT NOT NULL,
    tagged_utc   TEXT NOT NULL,
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

-- ─── Learning (V7) ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outcome_labels (
    release_id          INTEGER NOT NULL,
    symbol              TEXT NOT NULL,
    implied_direction   TEXT NOT NULL,
    entry_price         REAL,
    price_t30m          REAL,
    price_t2h           REAL,
    price_t1d           REAL,
    change_t30m_pct     REAL,
    change_t2h_pct      REAL,
    change_t1d_pct      REAL,
    mfe_2h_pct          REAL,
    mae_2h_pct          REAL,
    outcome             TEXT,
    outcome_magnitude   REAL,
    assessment_horizon  TEXT DEFAULT 'T+2H',
    computed_utc        TEXT NOT NULL,
    PRIMARY KEY(release_id, symbol),
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

CREATE TABLE IF NOT EXISTS setup_features (
    release_id          INTEGER PRIMARY KEY,
    event_code          TEXT NOT NULL,
    tier                INTEGER,
    category            TEXT,
    surprise_direction  TEXT,
    surprise_magnitude  TEXT,
    market_alignment    TEXT,
    market_score        REAL,
    structure_event     TEXT,
    held_retest         INTEGER,
    structure_confirmation TEXT,
    macro_label         TEXT,
    chop_state          TEXT,
    computed_utc        TEXT NOT NULL,
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

CREATE TABLE IF NOT EXISTS edge_stats (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol             TEXT NOT NULL,
    feature_level      TEXT NOT NULL,
    feature_key        TEXT NOT NULL,
    feature_hash       TEXT NOT NULL,
    n_samples          INTEGER NOT NULL,
    wins               INTEGER NOT NULL,
    losses             INTEGER NOT NULL,
    flats              INTEGER NOT NULL,
    win_rate           REAL,
    avg_change_pct     REAL,
    avg_mfe_pct        REAL,
    avg_mae_pct        REAL,
    stddev_change_pct  REAL,
    standard_error     REAL,
    confidence         TEXT,
    horizon            TEXT DEFAULT 'T+2H',
    updated_utc        TEXT NOT NULL,
    UNIQUE(symbol, feature_level, feature_hash, horizon)
);
CREATE INDEX IF NOT EXISTS idx_edge_lookup
    ON edge_stats(symbol, feature_level, feature_hash, horizon);

CREATE TABLE IF NOT EXISTS edge_stats_regime (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol             TEXT NOT NULL,
    country            TEXT NOT NULL,
    regime_label       TEXT NOT NULL,
    feature_level      TEXT NOT NULL,
    feature_key        TEXT NOT NULL,
    feature_hash       TEXT NOT NULL,
    n_samples          INTEGER NOT NULL,
    wins               INTEGER NOT NULL,
    losses             INTEGER NOT NULL,
    flats              INTEGER NOT NULL,
    win_rate           REAL,
    avg_change_pct     REAL,
    avg_mfe_pct        REAL,
    avg_mae_pct        REAL,
    stddev_change_pct  REAL,
    standard_error     REAL,
    confidence         TEXT,
    horizon            TEXT DEFAULT 'T+2H',
    updated_utc        TEXT NOT NULL,
    UNIQUE(symbol, country, regime_label, feature_level, feature_hash, horizon)
);
CREATE INDEX IF NOT EXISTS idx_edge_regime_lookup
    ON edge_stats_regime(symbol, country, regime_label,
                         feature_level, feature_hash, horizon);

-- ─── Backtesting (V8) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS backtest_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    description   TEXT,
    entry_filter  TEXT NOT NULL,
    entry_offset_min INTEGER DEFAULT 30,
    exit_horizon_min INTEGER DEFAULT 120,
    stop_pct         REAL,
    target_pct       REAL,
    max_hold_min     INTEGER,
    spread_cost_pct  REAL DEFAULT 0.02,
    slippage_pct     REAL DEFAULT 0.02,
    stake_fraction   REAL DEFAULT 1.0,
    created_utc      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rules_id        INTEGER NOT NULL,
    rules_name      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    start_ts_utc    TEXT NOT NULL,
    end_ts_utc      TEXT NOT NULL,
    n_trades        INTEGER,
    n_wins          INTEGER,
    n_losses        INTEGER,
    n_flats         INTEGER,
    win_rate        REAL,
    avg_win_pct     REAL,
    avg_loss_pct    REAL,
    expectancy_pct  REAL,
    profit_factor   REAL,
    sharpe_like     REAL,
    max_drawdown_pct REAL,
    total_return_pct REAL,
    equity_curve    TEXT,
    notes           TEXT,
    computed_utc    TEXT NOT NULL,
    FOREIGN KEY(rules_id) REFERENCES backtest_rules(id)
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    release_id      INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    entry_ts_utc    TEXT,
    exit_ts_utc     TEXT,
    gross_pct       REAL,
    net_pct         REAL,
    exit_reason     TEXT,
    mfe_pct         REAL,
    mae_pct         REAL,
    FOREIGN KEY(run_id) REFERENCES backtest_runs(id),
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

-- ─── Sizing (V9) ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sizing_config (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    account_size    REAL DEFAULT 10000.0,
    kelly_fraction  REAL DEFAULT 0.5,
    max_risk_pct    REAL DEFAULT 2.0,
    min_risk_pct    REAL DEFAULT 0.10,
    confidence_penalty_low  REAL DEFAULT 0.5,
    confidence_penalty_med  REAL DEFAULT 0.8,
    confidence_penalty_high REAL DEFAULT 1.0,
    updated_utc     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sizing_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id      INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    rules_name      TEXT,
    edge_level      TEXT,
    n_samples       INTEGER,
    win_rate        REAL,
    avg_win_pct     REAL,
    avg_loss_pct    REAL,
    kelly_raw       REAL,
    kelly_adjusted  REAL,
    confidence      TEXT,
    confidence_mult REAL,
    risk_pct        REAL,
    risk_amount     REAL,
    notes           TEXT,
    computed_utc    TEXT NOT NULL,
    FOREIGN KEY(release_id) REFERENCES releases(id)
);

-- ─── Trade simulator ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_simulations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    account_balance REAL NOT NULL,
    risk_pct        REAL NOT NULL,
    risk_amount     REAL NOT NULL,
    entry           REAL NOT NULL,
    stop            REAL NOT NULL,
    target          REAL,
    pips_stop       REAL,
    pips_target     REAL,
    pip_value       REAL,
    lot_size        REAL NOT NULL,
    units           REAL,
    potential_profit REAL,
    potential_loss   REAL,
    reward_risk      REAL,
    stop_pct         REAL,
    target_pct       REAL,
    warnings        TEXT,
    suggestions     TEXT,
    release_id      INTEGER,
    rules_name      TEXT,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_ts
    ON trade_simulations(ts_utc DESC);
CREATE INDEX IF NOT EXISTS idx_sim_symbol
    ON trade_simulations(symbol, ts_utc DESC);

-- ─── Alerts & scheduler ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id   INTEGER,
    kind         TEXT NOT NULL,
    symbol       TEXT,
    chat_id      TEXT,
    message_hash TEXT,
    status       TEXT NOT NULL,
    error        TEXT,
    ts_utc       TEXT NOT NULL,
    UNIQUE(release_id, kind, symbol),
    FOREIGN KEY(release_id) REFERENCES releases(id)
);
CREATE INDEX IF NOT EXISTS idx_alerts_release
    ON alerts_log(release_id, kind);

CREATE TABLE IF NOT EXISTS scheduler_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    job          TEXT NOT NULL,
    release_id   INTEGER,
    status       TEXT,
    detail       TEXT
);