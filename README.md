# Charted News Engine

Event-driven news-trading decision support. The React dashboard follows the
engine's five layers and reads live records from the same database as the CLI,
scheduler, collectors and learning pipeline. Local development uses SQLite;
Railway can use PostgreSQL. The engine does not display fabricated market
values or submit trades.

## Install the engine

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Run `cne help` for the available commands. Start with `cne init`, then use the
Workflow 01 collection page or `cne collect` to ingest public no-key sources.

## Deploy on Railway

The Railway Docker deployment builds the React/Vite frontend and runs the HTTP
dashboard together with the periodic collection and release-preparation
scheduler in one service. The app uses the Railway Postgres `DATABASE_URL`;
the original SQLite volume remains attached at `/data` as a migration backup.
The service listens on Railway's injected `PORT` and responds to `/api/health`.

1. Install the Railway CLI, authenticate with `railway login`, and link or
   create a Railway project and service.
2. Add a Postgres service and reference its `DATABASE_URL` from the app service.
3. To import an existing SQLite database once, set
   `CNE_SQLITE_IMPORT_PATHS=/data/cne.db` before starting the app. The importer
   takes a consistent SQLite backup, validates it, and imports all tables
   transactionally with an idempotent migration record.
4. Deploy the repository root with `railway up`.
5. Set optional provider credentials in Railway Variables, never in Git or
   `.env.example`. Public dashboard access is not authenticated; do not create a
   public domain until access is protected or public exposure is explicitly
   intended.

The deployment config uses a Dockerfile build, an HTTP health check, and a
restart-on-failure policy. Local development and tests continue to use SQLite
unless `DATABASE_URL` is set.

## Workflow 01 — Data collection

The collector stores source observations, normalized calendar events, and
provider-run outcomes in `data/cne.db`. Records retain source, endpoint, period,
units, source and retrieval timestamps, delay/data status, and revision
metadata. Missing timestamps stay empty. Identical observations are
deduplicated; changed values are retained as revisions.

| Provider / exact endpoint | Collected fields | Update frequency | Access restrictions | Required fields unavailable |
| --- | --- | --- | --- | --- |
| [BiQuote economic calendar](https://biquote.io/api/calendar?from={UTC_DATE}&to={UTC_DATE}&countries=US,EU&limit=500) · [docs](https://biquote.io/docs/) · [terms](https://biquote.io/terms.html) | Occurrence and recurring event IDs, name, USD/EUR currency and country, scheduled UTC time, period, forecast, previous, actual, revised previous, unit, importance, upstream source and release URL | Every 5 min; provider edge cache is 5 min | No key or signup. Provider terms permit use and storage inside a product; no SLA and data may be delayed or incomplete. The 500-row cap is handled by splitting full date ranges into smaller requests. | Exact publication timestamp; forecasts, actuals and revisions can be null. Consensus is upstream calendar data, not an official release. Missing values remain blank. |
| [FinanceCalendar fallback](https://www.financecalendar.com/wp-json/fc/v1/calendar?from={UTC_DATE}&to={UTC_DATE}&limit=500) · [API docs](https://www.financecalendar.com/api/) | Event name, scheduled UTC time when supplied, consensus, previous/actual, impact, category and event URL | Only when the BiQuote calendar request fails | No key. Free/commercial use requires visible attribution. Maximum date range 92 days. | Currency, country, period, units, revision and publication timestamp are often absent; forecasts/actuals can be null. Fallback records retain FinanceCalendar provenance. |
| [BLS Public Data API v2](https://api.bls.gov/publicAPI/v2/timeseries/data/) (POST; JSON `seriesid`, `startyear`, `endyear`) | CPI/core CPI, PPI/core PPI, payrolls, unemployment and hourly earnings | Daily, one grouped request | No key; subject to reduced unauthenticated quota. | Forecast consensus, exact publication timestamps, initial/continuing jobless claims and retail sales are not supplied by this connector. |
| [Treasury nominal yield CSV](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv) | Daily 2Y and 10Y indicative closing bid yields | Checked every 6 h; source is daily | Public CSV; no authentication. | Intraday quotes, bid/ask, spread and volume. Never used for minute-level confirmation. |
| [Treasury real yield CSV](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_real_yield_curve&field_tdr_date_value={year}&page&_format=csv) | Daily 10Y real yield | Checked every 6 h; source is daily | Public CSV; no authentication. | Intraday real yields, bid/ask, spread and volume. |
| [Eurostat GDP](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/namq_10_gdp?freq=Q&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ&geo=EA21&lastTimePeriod=12), [employment](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/namq_10_a10_e?freq=Q&unit=THS_PER&nace_r2=TOTAL&s_adj=SCA&na_item=EMP_DC&geo=EA21&lastTimePeriod=13), and [HICP](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_manr?freq=M&unit=RCH_A&coicop=CP00&geo=EA20&lastTimePeriod=12) | GDP/employment for EA21 and annual HICP inflation for EA20 | Checked every 6 h; source series quarterly/monthly | Official keyless JSON-stat API. | Consensus and exact publication times. HICP endpoint returned EA20 data through 2025, not EA21/current inflation; context only. |
| [ECB deposit facility rate](https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata&lastNObservations=60) | Daily ECB policy rate | Checked every 6 h | Official ECB Data Portal; no key. | Market-implied expectations and intraday repricing. |
| [ECB EUR/USD reference rate](https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata&lastNObservations=90) | Daily USD per EUR reference fixing | Checked every 6 h; source is daily | Official ECB Data Portal; no key; fixing around 14:15 CET/CEST. | Intraday quotes, bid/ask, OHLC, spread and volume. Excluded from release-window confirmation. |
| [Gold API XAU quote](https://api.gold-api.com/price/XAU) · [docs](https://gold-api.com/docs) | One XAU/USD quote and provider update timestamp | Every 5 min | No key; terms permit commercial use, disclaim accuracy, and prohibit abuse (including multiple requests per second). | Bid/ask, OHLC, spread, volume and independently verified latency. Context only, never confirmation. |
| [BiQuote latest quotes](https://biquote.io/api/latest?symbols=EURUSD&symbols=XAUUSD&symbols=DXY) and [5-minute OHLC](https://biquote.io/api/EURUSD/ohlc?interval=5m&limit=100) · [OpenAPI](https://biquote.io/swagger/v1/swagger.json) · [terms](https://biquote.io/terms.html) | XAU/USD, EUR/USD, DXY bid/ask/mid/spread, provider timestamp, market state, quote age, daily high/low and 1m–1d OHLC/tick-volume | Latest quotes every 5 min; OHLC retrieved on demand for snapshots, levels, structure and chop | **No key, signup, or authentication.** Terms permit storage and display in a product; raw feed resale/public redistribution is prohibited. Prices are indicative broker quotes, not exchange/official rates; no SLA. | US 2Y/10Y and real yields; real exchange volume (provider `volume` is always zero); guaranteed freshness. Closed/stale quotes are retained with status metadata and excluded from event confirmation. |
| [Alpha Vantage GOLD_SILVER_SPOT](https://www.alphavantage.co/query?function=GOLD_SILVER_SPOT&symbol=XAU) | XAU/USD spot quote | Every 6 h when configured; 1 request per sync | API key required; standard free access limited to 25 requests/day. Store `ALPHA_VANTAGE_API_KEY` in the local `.env`, never in source control. | Intraday OHLC, event-aligned snapshots and independently verified quote latency. |
| [Alpha Vantage CURRENCY_EXCHANGE_RATE](https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=EUR&to_currency=USD) | EUR/USD reference quote, bid/ask, provider timestamp when supplied | Every 6 h when configured; 1 request per sync | API key required; shares the 25 requests/day standard free limit. | Intraday OHLC; periodic quote samples are not release-window confirmation. |
| [Alpha Vantage FX_DAILY](https://www.alphavantage.co/query?function=FX_DAILY&from_symbol=EUR&to_symbol=USD&outputsize=compact) | EUR/USD daily OHLC candles (up to 100) | Every 6 h when configured; 1 request per sync | API key required; shares the 25 requests/day standard free limit. | Intraday bars, volume and exact publication timestamp. |
| Alpha Vantage `FX_INTRADAY` | Not connected | Unavailable on standard free tier | Official documentation labels the endpoint Premium. | Intraday EUR/USD candles. No premium-only endpoint is called. |
| [Federal Reserve press RSS](https://www.federalreserve.gov/feeds/press_all.xml), [Fed speeches RSS](https://www.federalreserve.gov/feeds/speeches.xml) and [ECB press RSS](https://www.ecb.europa.eu/rss/press.html) | Official titles, URLs, publication dates when supplied, categories | Hourly | Public RSS; no authentication. | RSS is not a structured policy-rate or expectations feed; policy tone is not inferred from event names. FRED target-rate bounds are listed separately. |
| [FRED observations API](https://api.stlouisfed.org/fred/series/observations) · [series-observations docs](https://fred.stlouisfed.org/docs/api/fred/series_observations.html) | Real US GDP QoQ annualized (`A191RL1Q225SBEA`), effective funds rate (`DFF`), target lower/upper (`DFEDTARL`, `DFEDTARU`), daily 2Y/10Y yields (`DGS2`, `DGS10`) and 10Y real yield (`DFII10`); offset pagination and vintage metadata | Every 6 h when configured; 7 series, paginated | Registered FRED API key required. Store `FRED_API_KEY` in local `.env`; never put it in source control. | Consensus forecasts, exact publication times, meeting probabilities and intraday yields. Daily/quarterly data is not event-window confirmation. |
| [BEA NIPA API](https://apps.bea.gov/api/data/) · `GetData`, table `T10101`, frequency `Q` | Real GDP quarterly percentage change (`LineNumber=1`), period, source line and retrieval timestamp | Every 6 h when configured | Registered, activated BEA API UserID stored locally as `BEA_API_KEY`. Live invalid-ID probe returned provider error `This UserId is not active`; not operational until a valid-key data response is tested. | Forecast consensus, exact release-publication timestamp and separate historical vintages. |
| Intraday Treasury yields | No verified no-key endpoint selected | Unavailable | Daily Treasury/FRED series are contextual only. | Intraday US 2Y/10Y and real-yield quotes, bid/ask and spread. |

FinanceCalendar records are retained with their raw payload. Absent country,
currency, period, unit, source time, forecast, actual, or revision fields stay
empty. Numeric conversion is limited to unambiguous values; source text remains
available when it cannot safely enter the surprise calculation. Provider
attribution is shown in the dashboard. `data/calendar.csv` is used only when
the public calendar fails.

`cne schedule` polls the calendar, Gold API quote and BiQuote latest quotes
every 5 minutes, BLS daily, Treasury/Eurostat/ECB every 6 hours, and official
RSS hourly. BiQuote requires no API key. It supplies indicative DXY, EUR/USD
and XAU/USD quotes plus closed 5-minute OHLC for release snapshots and
structure analysis; the engine rejects stale bars and marks closed/stale quotes
as non-confirming. The provider's data is not treated as official exchange or
benchmark pricing. When
`ALPHA_VANTAGE_API_KEY` is configured in the engine's local `.env`, its three
endpoints are polled together every 6 hours (up to 12 requests/day, below the
documented 25/day standard free limit). When `FRED_API_KEY` is configured,
FRED's seven configured series are polled every 6 hours; large daily series
may require multiple paginated requests per sync.
When `BEA_API_KEY` is configured, its quarterly NIPA GDP observations are also
collected every 6 hours.
Provider keys stay in the Python process and are never returned to the
dashboard. GET responses are briefly cached in memory; transient failures and
HTTP 429/5xx responses are retried. HTTP status codes and bounded provider
error messages are recorded with credentials redacted. Failures, last
successful sync, freshness and missing fields appear under **01 · Data
collection**. Collection automatically refreshes analysis.

Configure provider keys locally as `ALPHA_VANTAGE_API_KEY=...`,
`FRED_API_KEY=...`, and `BEA_API_KEY=...` in the project `.env` file, then
restart the engine. Do not paste keys into source files or commit them. Alpha
Vantage's standard free plan does not include its
documented `FX_INTRADAY` endpoint. Its six-hourly live/reference quotes and
daily EUR/USD candles do **not** provide event-aligned snapshots; BiQuote is
the configured intraday source for Gold, EUR/USD and DXY. Intraday Treasury
yields and real yields remain unavailable. FRED's `DFF` series is the effective federal funds rate, not the Federal
Reserve's target range. FRED daily/quarterly rates and yields are contextual
only. Release-window market evidence can use fresh BiQuote bars for Gold,
EUR/USD and DXY, but yield confirmation remains unavailable.

## Run the React + Vite dashboard

Start the local HTTP/SSE API from the engine environment:

```powershell
cne dashboard
```

For frontend development, open a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL printed in the terminal. Its `/api` proxy connects to the
Python service on `127.0.0.1:8000`. To build and serve the production interface
from the Python HTTP service instead:

```powershell
cd frontend
npm run build
cd ..
cne dashboard
```

Then open `http://127.0.0.1:8000`. The HTTP service binds to loopback only.
The frontend displays a connection error when the engine API is stopped; it
does not replace missing database records with sample data.

The dashboard exposes event records, collection source status and freshness,
macro and market analysis, three-light composition, historical edges and
backtests, the trade simulator, alert history, scheduler jobs and system
status. Its server-sent event stream watches SQLite for engine writes. Telegram
secrets remain in the Python process.

The overview answers five questions for the latest release: what the actual
was versus forecast, what the surprise means for central-bank policy and the
USD, whether captured market data confirms it, which instrument has the
clearest recorded structure, and how strong the historical evidence is. With
the currently verified public feeds, release-window market confirmation and
structure are explicitly unavailable until a compliant intraday source exists.

## Event workflow

1. Run `cne collect`; the BiQuote public calendar is primary for US/Euro Area
   events, FinanceCalendar is the API fallback, and `data/calendar.csv` is the
   final optional fallback when both APIs fail.
2. Keep `cne schedule` running to queue release, finalization and collection
   jobs.
3. The engine calculates `actual − forecast` only when both numeric values and
   an event mapping are present.
4. Analysis and composition use the same database for macro scores, any valid
   market evidence, three-light verdicts and simulations.
5. Label outcomes and run `cne learn-refresh` to rebuild historical edges;
   run `cne backtest-run` to evaluate seeded backtest rules.
6. Use the dashboard's sizing page to calculate a trade simulation. Saving a
   calculation is explicit; the simulator never submits a trade.
