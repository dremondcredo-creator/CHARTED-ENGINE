import { useCallback, useEffect, useMemo, useState } from "react";

const navigation = [
  { id: "overview", label: "News event dashboard", icon: "▦" },
  { id: "collection", label: "01 · Data collection", icon: "↓" },
  { id: "pipeline", label: "Pipeline", icon: "⌁" },
  { id: "calendar", label: "Release calendar", icon: "▣" },
  { id: "analysis", label: "Analysis", icon: "⌁" },
  { id: "composition", label: "Composition", icon: "◈" },
  { id: "learning", label: "Learning & backtests", icon: "⤴" },
  { id: "simulator", label: "Sizing & simulator", icon: "⊞" },
  { id: "alerts", label: "Alerts & schedule", icon: "◉" },
  { id: "system", label: "System status", icon: "⊙" },
];

const layers = [
  { id: "persistence", number: "01", name: "Persistence", components: ["SQLite · WAL", "Single source of truth"] },
  { id: "collection", number: "02", name: "Collection", components: ["Public calendar · consensus", "BLS · Treasury · Eurostat · ECB", "Gold quote · central-bank RSS", "No-key feeds · explicit gaps"] },
  { id: "analysis", number: "03", name: "Analysis", components: ["Surprise · Trend · Macro", "Market · Structure · Chop", "Labour · Levels · Pairs"] },
  { id: "composition", number: "04", name: "Composition", components: ["Three-light verdict", "Learning · Regime · Backtest", "Kelly sizing · Simulator"] },
  { id: "interface", number: "05", name: "Interface", components: ["React · HTTP + SSE", "CLI · Telegram"] },
];

const emptyForm = {
  symbol: "EURUSD",
  direction: "LONG",
  balance: "10000",
  risk_pct: "1",
  entry: "",
  stop: "",
  target: "",
  save: false,
};

function formatDate(value, options = {}) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function signed(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function Icon({ children }) {
  return <span className="small-icon" aria-hidden="true">{children}</span>;
}

function Pill({ children, tone = "neutral" }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function Panel({ eyebrow, title, action, className = "", children }) {
  return (
    <section className={`panel ${className}`}>
      {(eyebrow || title || action) && (
        <div className="panel-heading">
          <div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}{title && <h2>{title}</h2>}</div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function State({ value }) {
  const tone = value === "ready" || value === "ok" || value === "OK" || value === "sent" || value === "WIN" || value === "PUBLISHED" || value === "VERIFIED"
    ? "good"
    : value === "attention" || value === "queued" || value === "FLAT" || value === "DELAYED" || value === "STALE" || value === "FALLBACK" || value === "NOT_CONFIGURED" || value === "NOT_COLLECTED" || value === "NOT_RUN" || value === "INCOMPLETE"
      ? "warn"
      : value === "error" || value === "ERROR" || value === "LOSS"
        ? "bad"
        : "neutral";
  return <Pill tone={tone}>{value || "waiting"}</Pill>;
}

function Empty({ children = "Nothing recorded yet." }) {
  return <div className="empty-state">{children}</div>;
}

function Metric({ label, value, hint, tone }) {
  return (
    <article className="stat-card">
      <div className="stat-top"><span>{label}</span><span className={`metric-mark ${tone || ""}`} /></div>
      <div className="stat-value">{value ?? "—"}</div>
      {hint && <div className="stat-foot">{hint}</div>}
    </article>
  );
}

function LayerCards({ data, onOpen }) {
  const statusById = Object.fromEntries((data?.layers || []).map((layer) => [layer.id, layer]));
  return (
    <div className="layer-flow">
      {layers.map((layer, index) => {
        const detail = statusById[layer.id];
        return (
          <div className="layer-flow-item" key={layer.id}>
            <button className="layer-card" onClick={() => onOpen?.(layer.id)}>
              <div className="layer-card-top"><span className="layer-number">LAYER {layer.number}</span><State value={detail?.status || "waiting"} /></div>
              <h3>{layer.name}</h3>
              <div className="layer-card-components">{layer.components.map((item) => <span key={item}>{item}</span>)}</div>
              {detail && <p>{detail.detail}</p>}
            </button>
            {index < layers.length - 1 && <span className="layer-arrow" aria-hidden="true">→</span>}
          </div>
        );
      })}
    </div>
  );
}

function ReleaseTable({ rows = [], onSelect, compact = false }) {
  if (!rows.length) return <Empty>No releases are stored yet. Run <code>cne collect</code> to fetch the public calendar; <code>data/calendar.csv</code> is an optional fallback only.</Empty>;
  return (
    <div className="table-scroll">
      <table>
        <thead><tr><th>RELEASE</th><th>COUNTRY</th><th>TIME (UTC)</th><th>TIER</th><th>PREVIOUS</th><th>FORECAST</th><th>ACTUAL</th><th>SURPRISE</th><th>STATUS</th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr key={row.id} className={onSelect ? "clickable-row" : ""} onClick={() => onSelect?.(row.id)}>
            <td><div className="table-primary">{row.name || row.event_name || row.event_code}</div><div className="table-secondary">{row.event_code} · {row.period}</div></td>
            <td>{row.country || "—"}</td>
            <td>{formatDate(row.release_time_utc, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td>
            <td><Pill tone={row.tier === 1 ? "bad" : row.tier === 2 ? "warn" : "neutral"}>T{row.tier ?? "—"}</Pill></td>
            <td>{formatNumber(row.previous, 4)}</td>
            <td>{formatNumber(row.forecast, 4)}</td>
            <td className={row.actual == null ? "muted-cell" : "actual-cell"}>{formatNumber(row.actual, 4)}</td>
            <td className={row.raw_surprise == null ? "muted-cell" : "actual-cell"}>{signed(row.raw_surprise, 3)}{row.normalized_surprise != null && <small>normalized {signed(row.normalized_surprise, 2)}σ</small>}</td>
            <td><State value={row.data_quality_status || row.state || (row.actual == null ? "UPCOMING" : "RELEASED")} /></td>
          </tr>
        ))}</tbody>
      </table>
      {compact && <div className="table-caption">Showing the latest engine records, ordered around the next release.</div>}
    </div>
  );
}

function DecisionAnswers({ decision }) {
  if (!decision) {
    return <Empty>Waiting for collected release data. The five questions use published values when available, and keep missing market evidence explicitly uncertain.</Empty>;
  }
  const instruments = decision.questions.find((item) => item.id === 4)?.evidence?.instruments || [];
  const uncertainty = decision.questions.find((item) => item.id === 5)?.evidence?.uncertainties || [];
  return (
    <div className="decision-answers">
      <div className="decision-release-strip">
        <div><span className="eyebrow">{decision.release.country} · {decision.release.status} · RELEASE #{decision.release.id}</span><strong>{decision.release.name}</strong><span>{decision.release.period}</span></div>
        <State value={decision.release.status} />
      </div>
      {decision.questions.map((item) => (
        <article className={`decision-answer decision-answer-${item.id}`} key={item.id}>
          <div className="question-number">{String(item.id).padStart(2, "0")}</div>
          <div className="answer-content">
            <div className="answer-heading"><h3>{item.question}</h3><State value={item.status} /></div>
            <p>{item.answer}</p>
            {item.id === 4 && instruments.length > 0 && <div className="instrument-evidence">{instruments.map((instrument) => {
              const structure = instrument.event_structure;
              const chop = instrument.chop;
              return <span key={instrument.symbol} className="instrument-chip"><strong>{instrument.symbol}</strong>{structure ? `${structure.event_class} · ${structure.confirmation}` : chop ? `${chop.chop_label} · chop ${formatNumber(chop.chop_score, 1)}` : "No stored verdict"}{instrument.historical_edge?.found && ` · n=${instrument.historical_edge.n_samples}`}</span>;
            })}</div>}
            {item.id === 5 && uncertainty.length > 0 && <details className="uncertainty-list"><summary>{uncertainty.length} known limitation{uncertainty.length === 1 ? "" : "s"} · What could change this reading?</summary><ul>{uncertainty.map((entry) => <li key={entry}>{entry}</li>)}</ul></details>}
          </div>
        </article>
      ))}
    </div>
  );
}

function DecisionBrief({ decision, action }) {
  return (
    <Panel eyebrow="LIVE DECISION BRIEF · PERSISTED ENGINE EVIDENCE" title="Five questions before any trade" action={action} className="decision-panel">
      <DecisionAnswers decision={decision} />
      <div className="decision-disclaimer">Analysis and historical frequencies are decision support—not forecasts, recommendations, or guarantees.</div>
    </Panel>
  );
}

function EventDetail({ detail, onClose }) {
  if (!detail) return null;
  const release = detail.release;
  const composition = detail.composition;
  return (
    <div className="detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="detail-drawer" role="dialog" aria-modal="true" aria-label={`${release.name} release details`}>
        <div className="detail-header"><div><div className="eyebrow">RELEASE #{release.id} · {release.event_code}</div><h2>{release.name}</h2><p>{release.country} · {release.period} · Tier {release.tier}</p></div><button className="icon-button" onClick={onClose} aria-label="Close details">×</button></div>
        <div className="detail-values">
          {[["Previous", release.previous], ["Forecast", release.forecast], ["Actual", release.actual], ["Surprise", release.raw_surprise]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{label === "Surprise" ? signed(value, 3) : formatNumber(value, 4)}</strong></div>)}
        </div>
        <div className="feed-row"><div><strong>Release verification · {release.data_quality_status || "NOT CHECKED"}</strong><span>{release.verified_actual_source || release.actual_source || "Actual source unavailable"} · actual retrieved {release.retrieved_at_utc ? formatDate(release.retrieved_at_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) : "timestamp unavailable"} UTC</span><small>Forecast source: {release.forecast_source_at_release || "unavailable"} · forecast captured {release.forecast_retrieved_at_utc ? formatDate(release.forecast_retrieved_at_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) : "timestamp unavailable"} UTC · previous source: {release.previous_source || "unavailable"} · published {release.publication_timestamp_utc ? formatDate(release.publication_timestamp_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) : "source did not provide a publication timestamp"}</small>{release.is_revision ? <small>Revision detected · revised previous value {formatNumber(release.verified_revised_previous, 4)}</small> : null}{release.data_quality_status === "INCOMPLETE" && <small>No surprise is calculated until official actual and pre-release forecast are verified.</small>}</div><State value={release.data_quality_status || "NOT CHECKED"} /></div>
        <DecisionAnswers decision={detail.decision} />
        {composition && <div className="detail-verdict"><span className="verdict-light">{composition.light}</span><div><strong>{composition.label}</strong><span>Combined score {signed(composition.combined_score, 2)}</span></div></div>}
        <h3>Release lifecycle · observed snapshots</h3>
        <div className="phase-grid">{["PRE", "T0", "T+5M", "T+30M", "T+2H"].map((phase) => {
          const count = detail.snapshots.filter((snapshot) => snapshot.phase === phase).length;
          return <div className={`phase-card ${count ? "phase-done" : ""}`} key={phase}><strong>{phase}</strong><span>{count} instrument{count === 1 ? "" : "s"} captured</span></div>;
        })}</div>
        {detail.preparations?.length > 0 && <><h3>Immutable pre-release preparations</h3><div className="feed-list">{detail.preparations.map((preparation) => <article className="feed-row" key={`${preparation.event_key}-${preparation.window_minutes}`}><div><strong>T−{preparation.window_minutes}m · {preparation.data_quality_status}</strong><span>Captured {formatDate(preparation.captured_at_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC · Forecast {formatNumber(preparation.forecast, 4)} · Previous {formatNumber(preparation.previous, 4)}</span>{preparation.missing_fields.length > 0 && <small>Incomplete: {preparation.missing_fields.join(", ")}</small>}<small>{preparation.market_snapshots.map((snapshot) => `${snapshot.instrument}: ${snapshot.data_status}${snapshot.price == null ? "" : ` ${formatNumber(snapshot.price, 5)}`}`).join(" · ")}</small></div><State value={preparation.data_quality_status} /></article>)}</div></>}
        {detail.snapshots.length ? <div className="table-scroll detail-table"><table><thead><tr><th>PHASE</th><th>INSTRUMENT</th><th>PRICE</th><th>CHANGE</th><th>CAPTURED (UTC)</th></tr></thead><tbody>{detail.snapshots.map((snapshot, index) => <tr key={`${snapshot.phase}-${snapshot.symbol}-${index}`}><td>{snapshot.phase}</td><td>{snapshot.symbol}</td><td>{formatNumber(snapshot.price, 5)}</td><td>{signed(snapshot.change_pct, 3)}%</td><td>{formatDate(snapshot.captured_utc, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td></tr>)}</tbody></table></div> : <Empty>Snapshot collection runs around each release when the scheduler is active.</Empty>}
        {detail.outcomes.length > 0 && <><h3>Learning outcomes</h3><div className="outcome-chips">{detail.outcomes.map((outcome) => <Pill key={outcome.symbol} tone={outcome.outcome === "WIN" ? "good" : outcome.outcome === "LOSS" ? "bad" : "warn"}>{outcome.symbol} · {outcome.outcome || "pending"}</Pill>)}</div></>}
      </section>
    </div>
  );
}

function Overview({ data, onNavigate, onSelect }) {
  const count = data?.counts || {};
  const learning = data?.learning || {};
  const macroScore = data?.macro;
  const [selectedUpcomingId, setSelectedUpcomingId] = useState(null);
  const [clock, setClock] = useState(Date.now());
  const releases = data?.calendar || [];
  const upcoming = useMemo(
    () => releases
      .filter((release) => {
        if (release.actual != null) return false;
        const releaseTime = release.release_time_utc ? new Date(release.release_time_utc).getTime() : NaN;
        return !Number.isFinite(releaseTime) || releaseTime >= clock;
      })
      .sort((a, b) => {
        const timeA = a.release_time_utc ? new Date(a.release_time_utc).getTime() : Infinity;
        const timeB = b.release_time_utc ? new Date(b.release_time_utc).getTime() : Infinity;
        return timeA - timeB;
      }),
    [releases, clock],
  );
  const selectedUpcoming = upcoming.find((release) => release.id === selectedUpcomingId) || upcoming[0] || null;
  const selectedPreparations = (data?.collection?.preparations || [])
    .filter((preparation) => preparation.release_id === selectedUpcoming?.id)
    .sort((a, b) => b.window_minutes - a.window_minutes);
  const configuredPreparationWindows = data?.collection?.preparation_windows_minutes || [60, 15, 1];
  const recent = useMemo(
    () => releases
      .filter((release) => release.actual != null)
      .sort((a, b) => new Date(b.release_time_utc || 0) - new Date(a.release_time_utc || 0))
      .slice(0, 6),
    [releases],
  );
  const now = new Date(clock);
  const nextDay = clock + 24 * 60 * 60 * 1000;
  const nextDayReleases = upcoming.filter((release) => {
    const timestamp = release.release_time_utc ? new Date(release.release_time_utc).getTime() : NaN;
    return Number.isFinite(timestamp) && timestamp < nextDay;
  });
  const laterReleases = upcoming.filter((release) => !nextDayReleases.includes(release));
  const remaining = selectedUpcoming?.release_time_utc
    ? new Date(selectedUpcoming.release_time_utc).getTime() - clock
    : NaN;
  const countdown = Number.isFinite(remaining)
    ? remaining <= 0
      ? "Release time reached"
      : `${Math.floor(remaining / 86400000)}d ${String(Math.floor((remaining % 86400000) / 3600000)).padStart(2, "0")}h ${String(Math.floor((remaining % 3600000) / 60000)).padStart(2, "0")}m`
    : "Time not supplied";
  const releaseDate = selectedUpcoming?.release_time_utc
    ? new Date(selectedUpcoming.release_time_utc)
    : null;
  const hoursUntilRelease = Number.isFinite(remaining) ? remaining / 3600000 : null;
  const publishedCount = releases.filter((release) => release.actual != null).length;

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <>
      <div className="page-heading event-dashboard-heading">
        <div>
          <div className="eyebrow">NEWS EVENT CONTROL ROOM · {formatDate(now, { dateStyle: "full", timeZone: "UTC" })} UTC</div>
          <h1>Upcoming news events</h1>
          <p>Keep expectations, the release schedule, and published results in separate, traceable views.</p>
        </div>
        <div className="heading-actions"><State value={data?.system?.database_ready ? "ready" : "offline"} /><span className="updated-label">Updated {formatDate(data?.generated_at, { timeStyle: "short", timeZone: "UTC" })} UTC</span></div>
      </div>

      <nav className="event-partition-nav" aria-label="Event data partitions">
        <a href="#pre-release"><span>01</span> Pre-release data <b>{selectedUpcoming ? "READY" : "WAITING"}</b></a>
        <a href="#upcoming-schedule"><span>02</span> Upcoming schedule <b>{count.upcoming ?? 0}</b></a>
        <a href="#published-results"><span>03</span> Published results <b>{publishedCount}</b></a>
      </nav>

      <section className="next-event-banner" aria-label="Next scheduled economic release">
        <div className="next-event-copy">
          <div className="next-event-status"><i className="online-dot" /> NEXT SCHEDULED RELEASE <span>{selectedUpcoming ? `TIER ${selectedUpcoming.tier ?? "—"}` : "NO EVENT SELECTED"}</span></div>
          {selectedUpcoming ? <>
            <h2>{selectedUpcoming.name || selectedUpcoming.event_code}</h2>
            <p>{selectedUpcoming.country || "Country not supplied"} · {selectedUpcoming.event_code} · {selectedUpcoming.period || "Period not supplied"}</p>
            <button className="event-inline-link" onClick={() => onSelect(selectedUpcoming.id)}>Open full event record <span>↗</span></button>
          </> : <><h2>No scheduled release found</h2><p>Collect the calendar feed to populate the event dashboard.</p></>}
        </div>
        <div className="next-event-clock">
          <span>COUNTDOWN · UTC</span>
          <strong>{countdown}</strong>
          <small>{releaseDate && Number.isFinite(releaseDate.getTime()) ? formatDate(releaseDate, { weekday: "short", month: "short", day: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "Scheduled release time unavailable"}</small>
        </div>
      </section>

      <section className="event-partition" id="pre-release">
        <div className="partition-heading">
          <div><span className="partition-number">01</span><div><div className="eyebrow">BEFORE THE NUMBER PRINTS</div><h2>Pre-release data</h2><p>Preserved expectations and previous values for the selected event. Actual remains blank until published.</p></div></div>
          <Pill tone={selectedUpcoming ? "good" : "warn"}>{selectedUpcoming ? "PRE-RELEASE" : "AWAITING CALENDAR"}</Pill>
        </div>
        {selectedUpcoming ? (
          <>
            {hoursUntilRelease !== null && hoursUntilRelease <= 24 && hoursUntilRelease >= 0 && <div className="pre-release-alert"><span>!</span><div><strong>Release within 24 hours</strong><small>Review the source consensus and previous reading. This page does not predict the actual result.</small></div><b>{countdown}</b></div>}
            <div className="pre-release-grid">
              <article className="pre-release-event-card">
                <div className="pre-release-card-top"><span>SELECTED EVENT</span><Pill tone={selectedUpcoming.tier === 1 ? "bad" : selectedUpcoming.tier === 2 ? "warn" : "neutral"}>TIER {selectedUpcoming.tier ?? "—"}</Pill></div>
                <h3>{selectedUpcoming.name || selectedUpcoming.event_code}</h3>
                <p>{selectedUpcoming.country || "Country not supplied"} · {selectedUpcoming.period || "Period not supplied"}</p>
                <div className="pre-release-datetime"><span>Scheduled release · UTC</span><strong>{releaseDate && Number.isFinite(releaseDate.getTime()) ? formatDate(releaseDate, { weekday: "long", month: "long", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "Time not supplied"} UTC</strong><small>Your local time · {releaseDate && Number.isFinite(releaseDate.getTime()) ? formatDate(releaseDate, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "Unavailable"}</small></div>
                <div className="pre-release-source"><span>Consensus source</span><strong>{selectedUpcoming.forecast_source || "Provider not supplied"}</strong></div>
                <button className="text-link" onClick={() => onSelect(selectedUpcoming.id)}>View source and event details →</button>
              </article>
              <div className="pre-release-values">
                <article><span>CONSENSUS FORECAST</span><strong>{selectedUpcoming.forecast == null ? "Not supplied" : `${formatNumber(selectedUpcoming.forecast, 4)}${selectedUpcoming.unit ? ` ${selectedUpcoming.unit}` : ""}`}</strong><small>{selectedUpcoming.forecast_source || "Source not supplied"}</small></article>
                <article><span>PREVIOUS VALUE</span><strong>{selectedUpcoming.previous == null ? "Not supplied" : `${formatNumber(selectedUpcoming.previous, 4)}${selectedUpcoming.unit ? ` ${selectedUpcoming.unit}` : ""}`}</strong><small>Prior published reading</small></article>
                <article className="revision-value"><span>REVISED PREVIOUS</span><strong>{selectedUpcoming.revised_previous == null ? "No revision supplied" : `${formatNumber(selectedUpcoming.revised_previous, 4)}${selectedUpcoming.unit ? ` ${selectedUpcoming.unit}` : ""}`}</strong><small>Displayed only when a revision is reported</small></article>
                <article className="actual-pending"><span>ACTUAL RELEASE</span><strong>Awaiting publication</strong><small>No actual value has been recorded</small></article>
              </div>
            </div>
            <div className="pre-release-footnote"><span>i</span><span>Forecasts are provider consensus, not engine estimates. Missing values stay unavailable; no surprise or directional interpretation is calculated before a verified actual is present.</span></div>
            <Panel eyebrow="AUTOMATED PRE-RELEASE ENGINE" title="Preparation windows">
              {!selectedPreparations.length ? <Empty>Configured windows: {configuredPreparationWindows.map((window) => `T−${window}m`).join(", ")}. Immutable event and market snapshots are captured when the scheduler reaches each window.</Empty> : <div className="feed-list">{selectedPreparations.map((preparation) => <article className="feed-row" key={`${preparation.event_key}-${preparation.window_minutes}`}><div><strong>T−{preparation.window_minutes}m · {preparation.data_quality_status}</strong><span>Captured {formatDate(preparation.captured_at_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC · {preparation.market_snapshots.length} instrument snapshots · Forecast {formatNumber(preparation.forecast, 4)}</span>{preparation.missing_fields.length > 0 && <small>Incomplete: {preparation.missing_fields.join(", ")}</small>}<small>Conditional scenarios only · no final signal before the actual and market reaction.</small></div><State value={preparation.data_quality_status} /></article>)}</div>}
            </Panel>
          </>
        ) : <Empty>No future release is currently available in the collected calendar. Refresh the feed or check provider status in <button className="text-link" onClick={() => onNavigate("collection")}>Data collection</button>.</Empty>}
      </section>

      <section className="event-partition" id="upcoming-schedule">
        <div className="partition-heading">
          <div><span className="partition-number">02</span><div><div className="eyebrow">SCHEDULED · ACTUAL NOT YET PUBLISHED</div><h2>Upcoming schedule</h2><p>Choose an event to load its pre-release values into the panel above.</p></div></div>
          <button className="text-link" onClick={() => onNavigate("calendar")}>Open full calendar →</button>
        </div>
        {!nextDayReleases.length && <div className="schedule-window-empty">No releases are scheduled in the next 24 hours.</div>}
        <div className={`schedule-partitions ${nextDayReleases.length ? "" : "schedule-partitions-single"}`}>
          {nextDayReleases.length > 0 && <div className="schedule-column">
            <div className="schedule-column-heading"><strong>Next 24 hours</strong><span>{nextDayReleases.length} event{nextDayReleases.length === 1 ? "" : "s"}</span></div>
            {nextDayReleases.map((release) => <UpcomingEventCard key={release.id} release={release} selected={release.id === selectedUpcoming?.id} onClick={() => setSelectedUpcomingId(release.id)} now={clock} />)}
          </div>}
          <div className="schedule-column">
            <div className="schedule-column-heading"><strong>Later / time not supplied</strong><span>{laterReleases.length} event{laterReleases.length === 1 ? "" : "s"}</span></div>
            {laterReleases.length ? laterReleases.map((release) => <UpcomingEventCard key={release.id} release={release} selected={release.id === selectedUpcoming?.id} onClick={() => setSelectedUpcomingId(release.id)} now={clock} />) : <Empty>No later scheduled events are stored.</Empty>}
          </div>
        </div>
      </section>

      <section className="event-partition" id="published-results">
        <div className="partition-heading">
          <div><span className="partition-number partition-number-published">03</span><div><div className="eyebrow">ACTUAL VALUES · SURPRISES ONLY WHEN INPUTS EXIST</div><h2>Published results</h2><p>Released figures are kept apart from upcoming consensus. Missing actuals and forecasts are never inferred.</p></div></div>
          <Pill tone={recent.length ? "good" : "neutral"}>{recent.length} RECENT</Pill>
        </div>
        {recent.length ? (
          <div className="published-results-list">{recent.map((release) => <article className="published-result-row" key={release.id}>
            <div className="published-result-event"><span>{release.country || "—"} · {release.event_code} · {release.period || "Period unavailable"}</span><strong>{release.name || release.event_code}</strong><small>{formatDate(release.release_time_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC{release.actual_source ? ` · ${release.actual_source}` : ""}</small></div>
            <div><span>PREVIOUS</span><strong>{formatNumber(release.previous, 4)}</strong></div>
            <div><span>FORECAST</span><strong>{formatNumber(release.forecast, 4)}</strong></div>
            <div><span>ACTUAL</span><strong className="published-actual">{formatNumber(release.actual, 4)}</strong></div>
            <div><span>SURPRISE</span><strong className={release.raw_surprise == null ? "" : "published-actual"}>{signed(release.raw_surprise, 3)}</strong></div>
            <button className="text-link" onClick={() => onSelect(release.id)}>Details →</button>
          </article>)}</div>
        ) : <Empty>No published event results are available yet.</Empty>}
      </section>

      <div className="event-dashboard-footer">
        <div><span className="online-dot" /><strong>{data?.system?.database_ready ? "Engine connected" : "Engine unavailable"}</strong><span>{count.releases ?? 0} records · {count.surprises ?? 0} classified · {count.price_snapshots ?? 0} market snapshots</span></div>
        <div><span>Macro context</span><strong>{macroScore?.label || "Awaiting classified releases"} {macroScore ? `· ${signed(macroScore.total)}` : ""}</strong></div>
        <div><span>Learned outcomes</span><strong>{learning.outcomes ?? 0}</strong></div>
        <button className="text-link" onClick={() => onNavigate("system")}>System health →</button>
      </div>
    </>
  );
}

function UpcomingEventCard({ release, selected, onClick, now }) {
  const timestamp = release.release_time_utc ? new Date(release.release_time_utc) : null;
  const validTimestamp = timestamp && Number.isFinite(timestamp.getTime());
  const hours = validTimestamp ? (timestamp.getTime() - now) / 3600000 : null;
  return (
    <button className={`upcoming-event-card ${selected ? "upcoming-event-selected" : ""}`} onClick={onClick}>
      <span className="upcoming-event-date">{validTimestamp ? formatDate(timestamp, { month: "short", day: "2-digit", timeZone: "UTC" }) : "TIME N/A"}<strong>{validTimestamp ? formatDate(timestamp, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "UTC —"}</strong></span>
      <span className="upcoming-event-main"><strong>{release.name || release.event_code}</strong><small>{release.country || "Country unavailable"} · {release.event_code} · {release.period || "Period unavailable"}</small><span className="upcoming-event-values">Forecast {formatNumber(release.forecast, 4)} <i>·</i> Previous {formatNumber(release.previous, 4)}</span></span>
      <span className="upcoming-event-meta"><Pill tone={release.tier === 1 ? "bad" : release.tier === 2 ? "warn" : "neutral"}>T{release.tier ?? "—"}</Pill><small>{hours !== null && hours >= 0 ? hours < 24 ? `${Math.max(1, Math.floor(hours))}h` : `${Math.floor(hours / 24)}d` : "Select"}</small></span>
    </button>
  );
}

function Pipeline({ data, onNavigate }) {
  const counts = data?.counts || {};
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">SYSTEM ARCHITECTURE · LAYERS 1–5</div><h1>Engine pipeline</h1><p>Each layer reads and writes through the same SQLite source of truth.</p></div></div>
      <Panel eyebrow="DATA FLOW" title="Collection → analysis → decisions → delivery" className="layer-panel"><LayerCards data={data} onOpen={onNavigate} /><div className="source-of-truth"><Icon>▤</Icon><span><strong>SQLite WAL · data/cne.db</strong>Every stage persists results here; downstream stages consume those records.</span></div></Panel>
      <Panel eyebrow="RELEASE LIFECYCLE" title="The event clock">
        <div className="lifecycle-grid">{[
          ["Continuous", "Collect public data", "Calendar consensus, official statistics, policy publications and available market context", "collection"],
          ["Before release", "Preserve expectations", "Store the forecast and prior values before comparing a newly published actual", "collection"],
          ["Release time", "Validate the actual", "Use numeric actual and forecast only when supplied; retain missing values as unavailable", "surprises"],
          ["After release", "Check market evidence", "No live reaction verdict without a verified intraday feed and timestamped snapshots", "market"],
          ["After release", "Assess structure", "Break, fade and retest require valid OHLC history; unavailable feeds do not count as confirmation", "structure_verdicts"],
          ["Ongoing", "Report uncertainty", "Show provider status, freshness, revisions and source limitations with the analysis", "collection"],
          ["After outcome", "Learn from labels", "Recompute historical edges only after an outcome is explicitly recorded", "outcome_labels"],
        ].map(([time, title, detail, table]) => <article className="lifecycle-step" key={time}><div className="lifecycle-time">{time}</div><span className="timeline-node" /><div><strong>{title}</strong><p>{detail}</p><code>{counts[table] ?? 0} records</code></div></article>)}</div>
      </Panel>
      <div className="content-grid">
        <Panel eyebrow="DATABASE COUNTS" title="Persisted state"><div className="count-grid">{Object.entries(counts).filter(([name]) => name !== "upcoming").map(([name, value]) => <div key={name}><span>{name.replaceAll("_", " ")}</span><strong>{value}</strong></div>)}</div></Panel>
        <Panel eyebrow="COLLECTION INPUTS" title="Source coverage"><div className="feed-list">{(data?.collection?.sources || []).map((source) => <div className="feed-row" key={source.id}><div><strong>{source.name}</strong><span>{source.latest_run.last_successful_sync_utc ? `Last successful sync ${formatDate(source.latest_run.last_successful_sync_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC` : source.frequency}</span></div><State value={source.latest_run.status} /></div>)}</div></Panel>
      </div>
    </>
  );
}

function CalendarView({ rows, loading, error, query, setQuery, country, setCountry, tier, setTier, onSelect }) {
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 2 · COLLECTION · LAYER 3 · SURPRISES</div><h1>Release calendar</h1><p>Forecasts, actuals and classifications read directly from the engine database.</p></div></div>
      <Panel eyebrow="EVENT DEFINITIONS + RELEASES" title="Economic calendar">
        <div className="filter-bar"><input aria-label="Search releases" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search release, code, or period…" /><select aria-label="Filter by country" value={country} onChange={(event) => setCountry(event.target.value)}><option value="">All countries</option>{["US", "EU", "UK", "JP", "CA", "AU"].map((code) => <option key={code}>{code}</option>)}</select><select aria-label="Filter by tier" value={tier} onChange={(event) => setTier(event.target.value)}><option value="">All tiers</option><option value="1">Tier 1</option><option value="2">Tier 2</option><option value="3">Tier 3</option></select></div>
        {error && <div className="inline-error">{error}</div>}{loading ? <Empty>Loading records from SQLite…</Empty> : <ReleaseTable rows={rows} onSelect={onSelect} />}
      </Panel>
      <div className="source-of-truth"><Icon>↗</Icon><span><strong>Calendar consensus is provider-sourced.</strong> FinanceCalendar is collected automatically; <code>data/calendar.csv</code> is an optional fallback only if the public feed fails. Surprise = actual − forecast only when both values are available and numeric.</span></div>
    </>
  );
}

function CollectionView({ collection, onCollected }) {
  const [collecting, setCollecting] = useState(false);
  const [error, setError] = useState("");
  const sources = collection?.sources || [];
  const observations = collection?.observations || [];
  const calendarEvents = collection?.calendar_events || [];
  const runs = collection?.runs || [];
  const availableSources = sources.filter((source) => !source.unavailable);
  const successfulSources = availableSources.filter((source) => ["OK", "FALLBACK"].includes(source.latest_run.status));
  const delayedSources = availableSources.filter((source) => ["STALE", "DELAYED"].includes(source.latest_run.status));
  const unavailableSources = sources.filter((source) => source.unavailable);

  const collect = async () => {
    setCollecting(true);
    setError("");
    try {
      await request("/api/collect", { method: "POST" });
      await onCollected();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setCollecting(false);
    }
  };

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">WORKFLOW 01 · SOURCE DATA BEFORE ANALYSIS</div>
          <h1>Data collection</h1>
          <p>Public no-key sources · official statistics · central banks · timestamped market context</p>
        </div>
        <button className="refresh-button" onClick={collect} disabled={collecting}>
          {collecting ? "Collecting…" : "↓ Collect now"}
        </button>
      </div>
      <div className="collection-note">
        <strong>Evidence and provenance stay together.</strong>
        <span>Data feeds use public endpoints without API keys. Each observation records its source, unit, observation time, retrieval time, delay class, and publication status when the provider supplies them.</span>
      </div>
      {error && <div className="inline-error">{error}</div>}
      <div className="stats-grid collection-stats">
        <Metric label="SUCCESSFUL SOURCES" value={`${successfulSources.length} / ${availableSources.length}`} hint="Latest provider run completed successfully" tone="lime" />
        <Metric label="RECENT OBSERVATIONS" value={observations.length} hint="Most recent persisted records" tone="blue" />
        <Metric label="STALE SOURCE RUNS" value={delayedSources.length} hint="Provider freshness limit exceeded" tone="orange" />
        <Metric label="UNAVAILABLE FEEDS" value={unavailableSources.length} hint="No verified compliant endpoint" tone="purple" />
      </div>
      <div className="workflow-steps" aria-label="Collection workflow">
        {[
          ["01", "Collect", "Public calendars, official data, policy releases and available market context"],
          ["02", "Validate", "Normalize, timestamp, deduplicate and retain revisions"],
          ["03", "Analyze", "Calculate surprises and bias only when required inputs exist"],
          ["04", "Report", "Show source status, freshness and uncertainty; never execute trades"],
        ].map(([number, title, detail]) => <article key={number}><span>{number}</span><div><strong>{title}</strong><p>{detail}</p></div></article>)}
      </div>
      <div className="collection-source-grid">
        {sources.map((source) => (
          <article className="collection-source-card" key={source.id}>
            <div className="collection-source-top">
              <div><span className="eyebrow">{source.domain.replace("_", " ")}</span><h2>{source.name}</h2></div>
              <State value={source.latest_run.status} />
            </div>
            <strong className="collection-provider">{source.provider}</strong>
            <p>{source.limitation}</p>
            <p><strong>Cadence:</strong> {source.frequency}</p>
            {source.fields_collected?.length > 0 && <p><strong>Collected fields:</strong> {source.fields_collected.join("; ")}</p>}
            {source.access_requirements && <p><strong>Access:</strong> {source.access_requirements}</p>}
            <div className="collection-run-detail">
              <span>{source.latest_run.records} records · {source.latest_run.finished_utc ? formatDate(source.latest_run.finished_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) : "not run yet"}</span>
              <span>Last successful update: {source.latest_run.last_successful_sync_utc ? formatDate(source.latest_run.last_successful_sync_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) : "none yet"}{source.latest_run.freshness_minutes != null ? ` · ${source.latest_run.freshness_minutes} min ago` : ""}</span>
              {source.latest_run.error && <span className={source.latest_run.status === "ERROR" ? "collection-error" : ""}>{source.latest_run.error}</span>}
              {source.missing_fields?.length > 0 && <span><strong>Unavailable / not supplied:</strong> {source.missing_fields.join("; ")}</span>}
            </div>
            <div className="collection-source-links">
              {source.source_url?.startsWith("https://")
                ? <a href={source.source_url} target="_blank" rel="noreferrer">Endpoint ↗</a>
                : <code>{source.source_url || "Endpoint not supplied"}</code>}
              {source.docs_url?.startsWith("https://") && <a href={source.docs_url} target="_blank" rel="noreferrer">Provider docs ↗</a>}
              {source.terms_url?.startsWith("https://") && <a href={source.terms_url} target="_blank" rel="noreferrer">Terms ↗</a>}
            </div>
          </article>
        ))}
      </div>
      <div className="collection-callouts">
        <div><strong>Calendar source and fallback</strong><span><a href="https://biquote.io/docs/" target="_blank" rel="noreferrer">BiQuote's public calendar</a> is primary for US and Euro Area events, with <a href="https://www.financecalendar.com/api/" target="_blank" rel="noreferrer">FinanceCalendar</a> and the optional <code>data/calendar.csv</code> used only when the preceding source fails. Provider values retain their source and retrieval time; missing forecasts, actuals, revisions, and publication timestamps remain unavailable rather than being invented.</span></div>
        <div><strong>Daily references are not intraday evidence</strong><span>Treasury 2Y/10Y and real yields, Eurostat GDP/employment, and the ECB EUR/USD fixing are daily or quarterly observations. They are never treated as minute-by-minute release confirmation. The Gold API quote has no independently verified delay, bid/ask, OHLC, or spread and is context only.</span></div>
        <div><strong>Market feeds and limits</strong><span>BiQuote supplies keyless, indicative XAU/USD, EUR/USD, and DXY quotes plus intraday OHLC; closed or stale quotes are shown with their age and excluded from live confirmation. Fresh completed bars can populate release snapshots and structure analysis. US 2Y/10Y and real-yield intraday feeds remain unavailable; daily Treasury yields are context only.</span></div>
      </div>
      <Panel eyebrow={`PUBLIC CALENDAR · ${calendarEvents.length} MOST RECENT`} title="Scheduled events and consensus">
        {calendarEvents.length ? (
          <div className="table-scroll"><table className="collection-table">
            <thead><tr><th>EVENT</th><th>RELEASE UTC</th><th>FORECAST</th><th>PREVIOUS</th><th>ACTUAL</th><th>IMPORTANCE · STATUS</th><th>PROVIDER</th></tr></thead>
            <tbody>{calendarEvents.slice(0, 30).map((event) => (
              <tr key={event.event_id}>
                <td><strong>{event.event_name}</strong><div className="table-secondary">{event.country || "Country not supplied"} · {event.currency || "Currency not supplied"}</div></td>
                <td>{event.release_time_utc ? formatDate(event.release_time_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) : "Time not supplied"}</td>
                <td>{event.forecast_text || "Not supplied"}</td><td>{event.previous_text || "Not supplied"}</td><td>{event.actual_text || "Not released / not supplied"}</td>
                <td>{event.importance || "Not supplied"} · {event.release_status}{event.is_revision ? " · revised" : ""}</td>
                <td><a href={event.source_url} target="_blank" rel="noreferrer">{event.source}</a></td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <Empty>No calendar events have been collected yet.</Empty>}
      </Panel>
      <Panel eyebrow={`PRE-RELEASE ENGINE · ${(collection?.preparations || []).length} CAPTURES`} title="Preparation records · immutable">
        {(collection?.preparations || []).length ? <div className="table-scroll"><table className="collection-table">
          <thead><tr><th>EVENT</th><th>UTC · LOCAL RELEASE</th><th>WINDOW</th><th>FORECAST · PREVIOUS</th><th>FEEDS</th><th>QUALITY · CAPTURED UTC</th></tr></thead>
          <tbody>{collection.preparations.map((preparation) => (
            <tr key={`${preparation.event_key}-${preparation.window_minutes}`}>
              <td><strong>{preparation.event_name}</strong><div className="table-secondary">{preparation.country || "Country unavailable"} · {preparation.currency || "Currency unavailable"} · {preparation.period || "Period unavailable"}</div></td>
              <td>{formatDate(preparation.scheduled_release_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC<div className="table-secondary">{formatDate(preparation.scheduled_release_utc, { dateStyle: "medium", timeStyle: "short" })} local</div></td>
              <td>T−{preparation.window_minutes}m</td>
              <td>{formatNumber(preparation.forecast, 4)} · {formatNumber(preparation.previous, 4)}{preparation.is_revision ? " · revised" : ""}</td>
              <td>{preparation.market_snapshots.map((snapshot) => `${snapshot.instrument} ${snapshot.data_status}${snapshot.timeframe ? `/${snapshot.timeframe}` : ""}`).join(" · ")}</td>
              <td><State value={preparation.data_quality_status} /><div className="table-secondary">{formatDate(preparation.captured_at_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC</div>{preparation.missing_fields.length > 0 && <div className="table-secondary">Missing: {preparation.missing_fields.join(", ")}</div>}</td>
            </tr>
          ))}</tbody>
        </table></div> : <Empty>Preparation snapshots appear here as the scheduler reaches the configured pre-release windows ({(collection?.preparation_windows_minutes || [60, 15, 1]).map((window) => `T−${window}m`).join(", ")}).</Empty>}
      </Panel>
      <Panel eyebrow={`WORKFLOW 01 · ${observations.length} MOST RECENT`} title="Collected observations">
        {observations.length ? (
          <div className="table-scroll">
            <table className="collection-table">
              <thead><tr><th>DOMAIN · INDICATOR</th><th>PERIOD</th><th>VALUE · UNIT</th><th>SOURCE</th><th>OBSERVED UTC</th><th>PUBLISHED UTC</th><th>FETCHED UTC</th><th>STATUS</th></tr></thead>
              <tbody>{observations.map((row) => (
                <tr key={row.id}>
                  <td><strong>{row.domain.replace("_", " ")}</strong><div className="table-secondary">{row.indicator}</div></td>
                  <td>{row.observation_period || "—"}</td>
                  <td className="collection-value">{row.value_text || formatNumber(row.value_numeric, 5)} <span>{row.unit}</span></td>
                  <td>{row.source}</td>
                  <td>{formatDate(row.observed_utc, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td>
                  <td>{row.published_utc ? formatDate(row.published_utc, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "Not provided"}</td>
                  <td>{formatDate(row.fetched_utc, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td>
                  <td><State value={row.publication_status} /></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <Empty>Nothing has been collected yet. Select Collect now or run <code>cne collect</code> from the engine environment.</Empty>}
      </Panel>
      <Panel eyebrow="PROVIDER EXECUTION HISTORY" title="Collection runs">
        {runs.length ? <div className="feed-list">{runs.map((run, index) => <div className="feed-row" key={`${run.source}-${run.finished_utc}-${index}`}><div><strong>{run.source}</strong><span>{run.records} records · {formatDate(run.finished_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })}</span>{run.error && <small className="collection-error">{run.error}</small>}</div><State value={run.status} /></div>)}</div> : <Empty>Provider outcomes are stored here, including unavailable credentials and collection errors.</Empty>}
      </Panel>
    </>
  );
}

function MacroChart({ history }) {
  if (!history?.length) return <Empty>Run <code>cne macro</code> after releases are classified to store a score history.</Empty>;
  const width = 640;
  const height = 160;
  const scores = history.map((row) => Number(row.total_score || 0));
  const min = Math.min(-4, ...scores);
  const max = Math.max(4, ...scores);
  const yFor = (score) => height - ((score - min) / (max - min || 1)) * height;
  const points = history.map((row, index) => `${(index / Math.max(history.length - 1, 1)) * width},${yFor(Number(row.total_score || 0))}`).join(" ");
  return <div className="macro-chart-wrap"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Persisted macro score history"><line x1="0" x2={width} y1={yFor(0)} y2={yFor(0)} className="chart-grid" /><polyline points={points} className="chart-line" /></svg><div className="chart-caption"><span>{formatDate(history[0].ts_utc, { dateStyle: "medium" })}</span><span>{formatDate(history.at(-1).ts_utc, { dateStyle: "medium" })}</span></div></div>;
}

function Analysis({ data }) {
  const macro = data?.macro;
  const assessments = data?.fundamental_assessments;
  const categories = [
    ["Inflation", macro?.inflation],
    ["Labour", macro?.labour],
    ["Growth", macro?.growth],
    ["Policy", macro?.policy],
  ];
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 3 · ANALYSIS</div><h1>Fundamental & market analysis</h1><p>Surprise, trend, macro, market, chop, levels, structure and relative country bias.</p></div></div>
      <div className="stats-grid"><Metric label="US MACRO SCORE · 30D" value={macro ? signed(macro.total) : "—"} hint={macro?.label || "Awaiting classified releases"} tone="lime" /><Metric label="RELEASES IN WINDOW" value={macro?.n_releases ?? 0} hint="Actual releases in the rolling macro window" tone="blue" /><Metric label="MARKET VERDICTS" value={data?.counts?.market_verdicts ?? 0} hint="Persisted cross-asset alignment decisions" tone="purple" /><Metric label="STRUCTURE VERDICTS" value={data?.counts?.structure_verdicts ?? 0} hint="Break · fade · retest analysis records" tone="orange" /></div>
      <Panel eyebrow="VERIFIED DATA · PROVISIONAL WEIGHTS" title={`Fundamental assessment · ${assessments?.window_days ?? 30}d window`}>
        <p className="panel-note">{assessments?.methodology || "Waiting for the verified release monitor."} Scores are not probabilities. Weight configuration is provisional until tested against historical outcomes.</p>
        <div className="content-grid">
          {["USD", "GOLD", "EURUSD"].map((asset) => {
            const assessment = assessments?.[asset];
            return <article className="stat-card" key={asset}>
              <div className="stat-top"><span>{asset} · {assessment?.strength_of_evidence || "INSUFFICIENT"}</span><Pill tone={assessment?.direction === "BULLISH" ? "good" : assessment?.direction === "BEARISH" ? "bad" : "neutral"}>{assessment?.direction || "UNAVAILABLE"}</Pill></div>
              <div className="stat-value">{assessment ? signed(assessment.score, 2) : "—"}</div>
              <div className="stat-foot">Evidence {assessment?.evidence_quality?.rating || "unavailable"} · {assessment?.evidence_quality?.verified_events ?? 0} verified events · {assessment?.evidence_quality?.verified_groups ?? 0} independent groups</div>
              {(assessment?.main_drivers || []).slice(0, 3).map((driver) => <div className="feed-row" key={driver.group}><div><strong>{driver.group}</strong><span>{driver.events.map((event) => event.event_code).join(", ")}</span></div><strong>{signed(driver.points, 2)}</strong></div>)}
              {assessment?.conflicting_evidence?.length > 0 && <p className="panel-note">Conflicts: {assessment.conflicting_evidence.join(" ")}</p>}
              {assessment?.missing_information?.length > 0 && <p className="panel-note">Missing: {assessment.missing_information.join(" ")}</p>}
            </article>;
          })}
        </div>
      </Panel>
      <div className="content-grid">
        <Panel eyebrow="V2 · WEIGHTED SURPRISES" title={`Macro bias · ${macro?.label || "no signal"}`}><div className="macro-total">{signed(macro?.total)} <span>points</span></div><div className="driver-list">{categories.map(([name, value]) => <div className="driver" key={name}><div className="driver-top"><span>{name}</span><strong>{signed(value)}</strong></div><div className="driver-track"><span className="lime" style={{ width: `${Math.min(100, Math.abs(Number(value || 0)) / 8 * 100)}%` }} /></div></div>)}</div><p className="panel-note">{macro?.n_releases || 0} actual releases · {macro?.window_days || 30}-day window · Tier-weighted category contributions</p></Panel>
        <Panel eyebrow="V2 · HISTORY" title="Persisted macro snapshots"><MacroChart history={data?.macro_history} /></Panel>
      </div>
      <div className="content-grid">
        <Panel eyebrow="V3 · MARKET" title="Latest captured cross-asset prices"><MarketData rows={data?.market_data} /></Panel>
        <Panel eyebrow="V3–V4 · CHOP + LEVELS" title="Instrument context"><div className="feed-list">{["GOLD", "EURUSD"].map((symbol) => { const snapshot = (data?.market_data || []).find((item) => item.symbol === symbol); const detail = snapshot ? (snapshot.event_code ? `${snapshot.phase} · ${snapshot.event_code} ${snapshot.period}` : `${snapshot.phase} · ${snapshot.source}`) : "No captured prices yet"; return <div className="feed-row" key={symbol}><div><strong>{symbol}</strong><span>{detail}</span></div><strong>{formatNumber(snapshot?.price, 5)}</strong></div>; })}</div><p className="panel-note">Level detection and chop scoring use market history; run <code>cne levels GOLD</code> or <code>cne chop</code> to refresh.</p></Panel>
      </div>
    </>
  );
}

function MarketData({ rows }) {
  if (!rows?.length) return <Empty>Market quotes appear after collection; release snapshots are captured around scheduled events.</Empty>;
  return <div className="table-scroll"><table><thead><tr><th>SYMBOL</th><th>PRICE · BID / ASK</th><th>CHANGE</th><th>STATUS / PHASE</th><th>SOURCE / RELEASE</th><th>CAPTURED UTC</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.symbol}-${row.captured_utc}`}><td>{row.symbol}</td><td>{formatNumber(row.price, 5)}{row.bid != null && row.ask != null ? <small>Bid {formatNumber(row.bid, 5)} · Ask {formatNumber(row.ask, 5)} · Spread {formatNumber(row.spread, 5)}</small> : null}</td><td>{row.change_pct == null ? "—" : `${signed(row.change_pct, 3)}%`}</td><td>{row.data_status || row.phase}{row.quote_age_seconds != null ? <small>{Math.round(row.quote_age_seconds / 60)}m quote age</small> : null}</td><td>{row.event_code ? `${row.event_code} · ${row.period}` : row.source || "—"}</td><td>{formatDate(row.captured_utc, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td></tr>)}</tbody></table></div>;
}

function Composition({ data, onSelect }) {
  const composition = data?.composition;
  const latest = data?.latest_release;
  const parts = composition ? [
    ["Fundamental", composition.fundamental?.light, composition.fundamental?.label, composition.fundamental?.score],
    ["Market", composition.market?.market_light, composition.market?.market_alignment || "Not yet scored", composition.market?.market_score],
    ["Structure", composition.structure?.confirmation, composition.structure?.event_class || "Awaiting structure", composition.structure?.confirmation_score],
  ] : [];
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 4 · COMPOSITION · V7–V10</div><h1>Decision composition</h1><p>Three-light verdicts combine event fundamentals, market alignment and price structure.</p></div></div>
      <Panel eyebrow="LATEST RELEASE" title={latest ? `${latest.name} · ${latest.period}` : "Waiting for the first released event"}>
        {composition ? <><div className="composition-score"><span>{composition.light}</span><div><strong>{composition.label}</strong><small>Combined score {signed(composition.combined_score, 2)} · preferred instrument {composition.structure?.symbol || "—"}</small></div></div><div className="light-grid">{parts.map(([name, light, label, score]) => <div className="light-card" key={name}><span>{name}</span><strong>{light || "⚪"} {label}</strong><small>Score {signed(score, 2)}</small></div>)}</div><button className="text-link" onClick={() => onSelect(latest.id)}>Inspect source release →</button></> : <Empty>Three-light output appears after a release has an actual and the engine has evaluated its market and structure response.</Empty>}
      </Panel>
      <div className="content-grid"><Panel eyebrow="V7 · V7.5 · V8" title="Learning, regimes & backtests"><div className="composition-modules">{[["Learning", "Outcome → feature → historical edge"], ["Regime", "Macro score history → regime-aware edge"], ["Backtest", "Rules → trades → risk-adjusted statistics"]].map(([name, detail]) => <div className="module-row" key={name}><span className="module-symbol">◈</span><div><strong>{name}</strong><span>{detail}</span></div><Pill tone={(data?.learning?.edges?.length && name === "Learning") || (data?.learning?.backtests?.length && name === "Backtest") ? "good" : "neutral"}>{name === "Learning" ? `${data?.counts?.edge_stats ?? 0} edges` : name === "Regime" ? `${data?.counts?.macro_regimes ?? 0} regimes` : `${data?.counts?.backtest_runs ?? 0} runs`}</Pill></div>)}</div><button className="text-link" onClick={() => onSelect && onSelect(latest?.id)}>Inspect release inputs →</button></Panel><Panel eyebrow="V9 · V10" title="Position sizing & trade simulation"><p className="panel-note">Kelly raw × fractional Kelly × edge confidence. Simulator converts risk budget into lots, pip exposure, potential P&amp;L and warnings.</p><div className="count-grid"><div><span>SIMULATIONS</span><strong>{data?.counts?.trade_simulations ?? 0}</strong></div><div><span>SAVED SIZING DECISIONS</span><strong>{data?.counts?.sizing_decisions ?? 0}</strong></div><div><span>ACCOUNT CONFIG</span><strong>{data?.sizing_config ? `$${formatNumber(data.sizing_config.account_size, 0)}` : "defaults"}</strong></div></div></Panel></div>
    </>
  );
}

function Learning({ data }) {
  const learning = data?.learning || {};
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 4 · V7 LEARNING · V7.5 REGIMES · V8 BACKTESTING</div><h1>Historical edge & backtests</h1><p>Observed outcomes feed feature groups; test rules against the persisted event history.</p></div></div>
      <div className="stats-grid"><Metric label="LABELED OUTCOMES" value={learning.outcomes ?? 0} hint={`${learning.wins ?? 0} wins · ${learning.losses ?? 0} losses`} tone="lime" /><Metric label="WIN RATE" value={learning.win_rate == null ? "—" : `${(learning.win_rate * 100).toFixed(1)}%`} hint="Across completed wins and losses" tone="blue" /><Metric label="EDGE FEATURES" value={data?.counts?.edge_stats ?? 0} hint={`${data?.counts?.setup_features ?? 0} setup feature records`} tone="purple" /><Metric label="BACKTEST RUNS" value={data?.counts?.backtest_runs ?? 0} hint={`${data?.counts?.backtest_rules ?? 0} available rule sets`} tone="orange" /></div>
      <Panel eyebrow="EDGE STATS · REGIME AWARE" title="Learned historical edges">{learning.edges?.length ? <div className="table-scroll"><table><thead><tr><th>SYMBOL</th><th>FEATURE LEVEL</th><th>FEATURE KEY</th><th>SAMPLES</th><th>WIN RATE</th><th>AVG MFE</th><th>AVG MAE</th><th>CONFIDENCE</th><th>HORIZON</th></tr></thead><tbody>{learning.edges.map((edge, index) => <tr key={`${edge.symbol}-${edge.feature_hash}-${index}`}><td>{edge.symbol}</td><td>{edge.feature_level}</td><td className="feature-key">{edge.feature_key}</td><td>{edge.n_samples}</td><td>{edge.win_rate == null ? "—" : `${(edge.win_rate * 100).toFixed(1)}%`}</td><td>{signed(edge.avg_mfe_pct, 3)}%</td><td>{signed(edge.avg_mae_pct, 3)}%</td><td><State value={edge.confidence} /></td><td>{edge.horizon}</td></tr>)}</tbody></table></div> : <Empty>No learned edges yet. Run <code>cne learn-refresh</code> after outcomes have been labeled.</Empty>}</Panel>
      <Panel eyebrow="V8 · SIMULATION RESULTS" title="Backtest runs">{learning.backtests?.length ? <div className="table-scroll"><table><thead><tr><th>RULE</th><th>SYMBOL</th><th>TRADES</th><th>WIN RATE</th><th>EXPECTANCY</th><th>PROFIT FACTOR</th><th>MAX DRAWDOWN</th><th>TOTAL RETURN</th></tr></thead><tbody>{learning.backtests.map((run) => <tr key={run.id}><td>{run.rules_name}</td><td>{run.symbol}</td><td>{run.n_trades ?? 0}</td><td>{run.win_rate == null ? "—" : `${(run.win_rate * 100).toFixed(1)}%`}</td><td>{signed(run.expectancy_pct, 3)}%</td><td>{formatNumber(run.profit_factor, 2)}</td><td>{signed(run.max_drawdown_pct, 2)}%</td><td>{signed(run.total_return_pct, 2)}%</td></tr>)}</tbody></table></div> : <Empty>No backtest results yet. Seed or run rules with <code>cne backtest-seed</code> and <code>cne backtest-run</code>.</Empty>}</Panel>
    </>
  );
}

function Simulator({ data, onSaved }) {
  const [form, setForm] = useState(emptyForm);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await request("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: form.symbol,
          direction: form.direction,
          balance: Number(form.balance),
          risk_pct: Number(form.risk_pct),
          entry: Number(form.entry),
          stop: Number(form.stop),
          target: form.target === "" ? null : Number(form.target),
          save: form.save,
        }),
      });
      setResult(response.result);
      if (response.saved) onSaved();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 4 · V9 SIZING · V10 SIMULATOR</div><h1>Risk & trade simulator</h1><p>Position size is derived from account risk, stop distance and the engine's instrument specifications.</p></div></div>
      <div className="content-grid simulator-grid">
        <Panel eyebrow="PRE-TRADE CALCULATOR" title="Size a hypothetical trade"><form className="sim-form" onSubmit={submit}>
          <label>Instrument<select value={form.symbol} onChange={(event) => update("symbol", event.target.value)}>{Object.keys(data?.instrument_specs || {}).sort().map((symbol) => <option key={symbol}>{symbol}</option>)}</select></label>
          <label>Direction<select value={form.direction} onChange={(event) => update("direction", event.target.value)}><option>LONG</option><option>SHORT</option></select></label>
          <label>Account balance<input type="number" min="0" step="any" required value={form.balance} onChange={(event) => update("balance", event.target.value)} /></label>
          <label>Risk (%)<input type="number" min="0.01" max="100" step="any" required value={form.risk_pct} onChange={(event) => update("risk_pct", event.target.value)} /></label>
          <label>Entry price<input type="number" min="0" step="any" required value={form.entry} onChange={(event) => update("entry", event.target.value)} /></label>
          <label>Stop price<input type="number" min="0" step="any" required value={form.stop} onChange={(event) => update("stop", event.target.value)} /></label>
          <label>Target price <span className="optional-label">optional</span><input type="number" min="0" step="any" value={form.target} onChange={(event) => update("target", event.target.value)} /></label>
          <label className="checkbox-field"><input type="checkbox" checked={form.save} onChange={(event) => update("save", event.target.checked)} /> Save this simulation to SQLite</label>
          <button className="refresh-button sim-submit" type="submit" disabled={busy}>{busy ? "Calculating…" : "Calculate position"}</button>
        </form>{error && <div className="inline-error">{error}</div>}{result && <SimulationResult result={result} />}</Panel>
        <div className="sim-side"><Panel eyebrow="CALCULATION OUTPUT" title="Lot size · pips · P&amp;L"><p className="panel-note">Based on the engine's configured contract size and pip value for the selected instrument. It does not place or route trades.</p>{result ? <div className="simulation-results">{[["Position size", `${formatNumber(result.lot_size, 2)} lots`], ["Units", formatNumber(result.units, 0)], ["Risk amount", `$${formatNumber(result.risk_amount)}`], ["Stop distance", `${formatNumber(result.pips_stop)} pips`], ["Potential profit", result.potential_profit == null ? "No target" : `$${formatNumber(result.potential_profit)}`], ["Reward / risk", formatNumber(result.reward_risk, 2)]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div> : <Empty>Enter a trade to calculate position size, potential loss and reward / risk.</Empty>}</Panel><Panel eyebrow="SAVED SIMULATIONS" title={`${data?.simulations?.length || 0} recent records`}>{data?.simulations?.length ? <div className="feed-list">{data.simulations.slice(0, 6).map((item) => <div className="feed-row" key={item.id}><div><strong>{item.symbol} · {item.direction}</strong><span>{formatDate(item.ts_utc, { dateStyle: "medium", timeStyle: "short" })} · {formatNumber(item.lot_size, 2)} lots</span></div><strong>${formatNumber(item.risk_amount)}</strong></div>)}</div> : <Empty>Saved trade calculations are shown here.</Empty>}</Panel></div>
      </div>
    </>
  );
}

function SimulationResult({ result }) {
  return <div className="simulation-inline"><strong>{formatNumber(result.lot_size, 2)} lots</strong><span>{result.pips_stop} pip stop · risk ${formatNumber(result.risk_amount)}</span>{result.warnings?.map((warning) => <p className="risk-warning" key={warning}>Warning: {warning}</p>)}{result.suggestions?.map((suggestion) => <p className="risk-suggestion" key={suggestion}>Note: {suggestion}</p>)}</div>;
}

function Alerts({ data }) {
  const alerts = data?.alerts || [];
  const schedule = data?.schedule || [];
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 5 · TELEGRAM · PRE / REL / FIN</div><h1>Alerts & scheduler</h1><p>Scheduled event jobs, delivery records and recent scheduler activity.</p></div></div>
      <div className="stats-grid"><Metric label="QUEUED RELEASES" value={schedule.length} hint="Within the scheduler's 14-day window" tone="lime" /><Metric label="ALERT RECORDS" value={alerts.length} hint={`${alerts.filter((item) => item.status === "error").length} recorded errors`} tone="blue" /><Metric label="LAST SCHEDULER JOB" value={data?.system?.latest_scheduler_job?.job || "—"} hint={data?.system?.latest_scheduler_job?.ts_utc ? formatDate(data.system.latest_scheduler_job.ts_utc, { dateStyle: "medium", timeStyle: "short" }) : "No job history stored"} tone="purple" /><Metric label="TELEGRAM" value={data?.system?.credentials?.TELEGRAM_BOT_TOKEN ? "Configured" : "Not configured"} hint="Secrets remain on the engine host" tone="orange" /></div>
      <Panel eyebrow="SCHEDULER · NEXT 14 DAYS" title="Release job timeline">{schedule.length ? <div className="table-scroll"><table><thead><tr><th>EVENT</th><th>PERIOD</th><th>PRE · −15M</th><th>RELEASE ALERT · +5M</th><th>SNAPSHOT</th><th>FINAL · +2H10M</th></tr></thead><tbody>{schedule.map((job) => <tr key={job.release_id}><td>{job.code}</td><td>{job.period}</td><td>{formatDate(job.pre_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td><td>{formatDate(job.release_alert_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td><td>{formatDate(job.post_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td><td>{formatDate(job.finalize_at, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}</td></tr>)}</tbody></table></div> : <Empty>No future releases in the next 14 days. The scheduler queues releases with a timestamp after forecasts are collected.</Empty>}</Panel>
      <div className="content-grid"><Panel eyebrow="TELEGRAM DELIVERY" title="Alert delivery log">{alerts.length ? <div className="feed-list">{alerts.map((alert) => <div className="feed-row" key={alert.id}><div><strong>{alert.kind} · {alert.event_name || `release ${alert.release_id ?? "—"}`}</strong><span>{alert.period || "—"} · {alert.symbol || "all instruments"} · {formatDate(alert.ts_utc, { dateStyle: "medium", timeStyle: "short" })}</span>{alert.error && <small className="risk-warning">{alert.error}</small>}</div><State value={alert.status} /></div>)}</div> : <Empty>No Telegram delivery records yet. Configure the bot in .env and run <code>cne schedule</code>.</Empty>}</Panel><Panel eyebrow="SCHEDULER ACTIVITY" title="Recent job outcomes">{data?.system?.latest_scheduler_job ? <div className="detail-verdict"><div><strong>{data.system.latest_scheduler_job.job} · {data.system.latest_scheduler_job.status}</strong><span>{data.system.latest_scheduler_job.detail || "No additional detail recorded."} · {formatDate(data.system.latest_scheduler_job.ts_utc, { dateStyle: "medium", timeStyle: "short" })}</span></div></div> : <Empty>Scheduler jobs are written when <code>cne schedule</code> processes an event.</Empty>}<p className="panel-note">Run <code>cne schedule</code> in the engine process to poll providers at the configured interval, refresh upcoming releases, capture each release phase and emit alerts. This dashboard does not start the scheduler implicitly.</p></Panel></div>
    </>
  );
}

function SystemStatus({ data, stream }) {
  const sources = data?.collection?.sources || [];
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">LAYER 5 · SERVICE HEALTH</div><h1>System status</h1><p>Local HTTP API, server-sent database updates, persistence health and configured integrations.</p></div></div>
      <div className="stats-grid"><Metric label="HTTP API" value="Connected" hint={`CNE v${data?.system?.version || "—"}`} tone="lime" /><Metric label="DATABASE" value={data?.system?.database || "—"} hint={`${formatNumber(data?.system?.database_bytes, 0)} bytes · ${data?.counts?.releases ?? 0} releases`} tone="blue" /><Metric label="SERVER-SENT EVENTS" value={stream ? "Live" : "Reconnecting"} hint="SQLite data_version change notifications" tone="purple" /><Metric label="LATEST DATA UPDATE" value={data?.system?.latest_data_update ? formatDate(data.system.latest_data_update, { dateStyle: "medium" }) : "—"} hint={data?.system?.latest_data_update ? formatDate(data.system.latest_data_update, { timeStyle: "short", timeZone: "UTC" }) + " UTC" : "No release update recorded"} tone="orange" /></div>
      <Panel eyebrow="ARCHITECTURE · PER-LAYER COUNTS" title="Live component status"><LayerCards data={data} /></Panel>
      <div className="content-grid">
        <Panel eyebrow="PROVIDER HEALTH" title="Public source connections">
          {sources.length ? <div className="feed-list">{sources.map((source) => <div className="feed-row" key={source.id}><div><strong>{source.name}</strong><span>{source.latest_run.last_successful_sync_utc ? `Last successful update ${formatDate(source.latest_run.last_successful_sync_utc, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC` : source.frequency}</span></div><State value={source.latest_run.status} /></div>)}</div> : <Empty>Source health is not available yet.</Empty>}
        </Panel>
        <Panel eyebrow="DATABASE" title="Persisted components"><div className="count-grid">{Object.entries(data?.counts || {}).filter(([name]) => name !== "upcoming").map(([name, count]) => <div key={name}><span>{name.replaceAll("_", " ")}</span><strong>{count}</strong></div>)}</div></Panel>
      </div>
      <div className="source-of-truth"><Icon>◉</Icon><span><strong>Optional Telegram alerts</strong> {data?.system?.credentials?.TELEGRAM_BOT_TOKEN ? "Bot credentials are configured; values remain on the engine host." : "Not configured. Data collection and analysis do not require alert credentials."}</span></div>
    </>
  );
}

function App() {
  const [active, setActive] = useState("overview");
  const [data, setData] = useState(null);
  const [pageError, setPageError] = useState("");
  const [loading, setLoading] = useState(true);
  const [streamConnected, setStreamConnected] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [calendarRows, setCalendarRows] = useState([]);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarError, setCalendarError] = useState("");
  const [query, setQuery] = useState("");
  const [country, setCountry] = useState("");
  const [tier, setTier] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [releaseDetail, setReleaseDetail] = useState(null);
  const [detailError, setDetailError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);
  const [instrumentSpecs, setInstrumentSpecs] = useState({});

  const loadDashboard = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    try {
      const dashboard = await request("/api/dashboard");
      setData(dashboard);
      setPageError("");
      setLastUpdated(new Date());
    } catch (error) {
      setPageError(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDashboard();
    const stream = new EventSource("/api/events");
    stream.onopen = () => setStreamConnected(true);
    stream.onerror = () => setStreamConnected(false);
    stream.addEventListener("engine_update", () => loadDashboard({ quiet: true }));
    return () => stream.close();
  }, [loadDashboard]);

  useEffect(() => {
    if (active !== "calendar") return undefined;
    const controller = new AbortController();
    const timeout = window.setTimeout(async () => {
      setCalendarLoading(true);
      setCalendarError("");
      const params = new URLSearchParams();
      if (query.trim()) params.set("q", query.trim());
      if (country) params.set("country", country);
      if (tier) params.set("tier", tier);
      try {
        const response = await request(`/api/calendar?${params}`, { signal: controller.signal });
        setCalendarRows(response.events);
      } catch (error) {
        if (error.name !== "AbortError") setCalendarError(error.message);
      } finally {
        if (!controller.signal.aborted) setCalendarLoading(false);
      }
    }, 180);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [active, query, country, tier]);

  useEffect(() => {
    if (active !== "simulator") return undefined;
    const controller = new AbortController();
    request("/api/simulations", { signal: controller.signal })
      .then((response) => setInstrumentSpecs(response.specs))
      .catch((error) => { if (error.name !== "AbortError") setPageError(error.message); });
    return () => controller.abort();
  }, [active]);

  useEffect(() => {
    if (selectedId == null) {
      setReleaseDetail(null);
      return undefined;
    }
    const controller = new AbortController();
    setDetailError("");
    request(`/api/releases/${selectedId}`, { signal: controller.signal })
      .then(setReleaseDetail)
      .catch((error) => { if (error.name !== "AbortError") setDetailError(error.message); });
    return () => controller.abort();
  }, [selectedId]);

  const alertsData = useMemo(() => ({ alerts: data?.alerts || [], schedule: data?.schedule || [], system: data?.system }), [data]);
  const navigate = (id) => { setActive(id); setMenuOpen(false); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const openRelease = (id) => setSelectedId(id);
  const refresh = () => loadDashboard();
  const title = navigation.find((item) => item.id === active)?.label || "Engine overview";

  const page = (() => {
    switch (active) {
      case "collection": return <CollectionView collection={data?.collection} onCollected={() => loadDashboard({ quiet: true })} />;
      case "pipeline": return <Pipeline data={data} onNavigate={(section) => navigate(section === "interface" ? "system" : section === "collection" ? "collection" : section === "analysis" ? "analysis" : section === "composition" ? "composition" : "pipeline")} />;
      case "calendar": return <CalendarView rows={calendarRows} loading={calendarLoading} error={calendarError} query={query} setQuery={setQuery} country={country} setCountry={setCountry} tier={tier} setTier={setTier} onSelect={openRelease} />;
      case "analysis": return <Analysis data={data} />;
      case "composition": return <Composition data={data} onSelect={openRelease} />;
      case "learning": return <Learning data={data} />;
      case "simulator": return <Simulator data={{ ...data, instrument_specs: instrumentSpecs }} onSaved={() => loadDashboard({ quiet: true })} />;
      case "alerts": return <Alerts data={alertsData} />;
      case "system": return <SystemStatus data={data} stream={streamConnected} />;
      default: return <Overview data={data} onNavigate={navigate} onSelect={openRelease} />;
    }
  })();

  return (
    <div className="app-shell">
      {menuOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
      <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}>
        <div className="brand"><div className="brand-mark"><span /><span /><span /></div><div className="brand-copy"><strong>charted</strong><span>NEWS ENGINE</span></div><button className="sidebar-close icon-button" onClick={() => setMenuOpen(false)} aria-label="Close navigation">×</button></div>
        <div className="workspace-switcher"><div className="workspace-avatar">C</div><div><strong>Control room</strong><span>Event-driven decision support</span></div></div>
        <div className="nav-label">SYSTEM MAP</div>
        <nav className="main-nav" aria-label="Main navigation">{navigation.map((item) => <button key={item.id} className={`nav-item ${active === item.id ? "active" : ""}`} onClick={() => navigate(item.id)}><Icon>{item.icon}</Icon><span>{item.label}</span>{item.id === "overview" && data?.counts?.upcoming > 0 && <span className="nav-count">{data.counts.upcoming}</span>}</button>)}</nav>
        <div className="sidebar-bottom"><div className="plan-card"><div className="plan-icon"><Icon>↗</Icon></div><div><strong>Live engine link</strong><span><i className={`online-dot ${streamConnected ? "" : "offline-dot"}`} /> {streamConnected ? "SSE connected" : "Connecting to local API"}</span></div></div><div className="profile"><div className="profile-avatar">C</div><div><strong>Local engine</strong><span>{data?.system?.database || "SQLite"} · localhost</span></div></div></div>
      </aside>
      <main className="main-content">
        <header className="topbar"><button className="mobile-menu icon-button" onClick={() => setMenuOpen(true)} aria-label="Open navigation">☰</button><div className="breadcrumbs"><span>Charted News Engine</span><span>›</span><strong>{title}</strong></div><div className="topbar-actions"><span className={`market-open ${streamConnected ? "" : "api-offline"}`}><i className={`online-dot ${streamConnected ? "" : "offline-dot"}`} />{streamConnected ? "Engine stream live" : "API reconnecting"}</span><button className="refresh-button" onClick={refresh} disabled={loading}>{loading ? "Loading…" : "↻ Refresh"}</button></div></header>
        <div className="page">
          {pageError && <div className="connection-error"><strong>Engine data is unavailable.</strong><span>{pageError}</span><span>Start the Python API with <code>cne dashboard</code>; the Vite development server proxies its HTTP and SSE endpoints.</span><button className="refresh-button" onClick={refresh}>Retry connection</button></div>}
          {!pageError && loading && !data ? <div className="loading-card">Connecting to the engine's SQLite database…</div> : page}
          <footer className="page-footer"><span>CHARTED NEWS ENGINE <span className="footer-separator">·</span> DECISION SUPPORT, NOT FINANCIAL ADVICE</span><span>{lastUpdated ? `Updated ${formatDate(lastUpdated, { timeStyle: "short" })}` : "Waiting for engine"}</span></footer>
        </div>
      </main>
      {selectedId != null && (releaseDetail ? <EventDetail detail={releaseDetail} onClose={() => setSelectedId(null)} /> : <div className="detail-backdrop"><section className="detail-drawer"><div className="detail-header"><strong>{detailError || "Loading release lifecycle…"}</strong><button className="icon-button" onClick={() => setSelectedId(null)} aria-label="Close details">×</button></div></section></div>)}
    </div>
  );
}

export default App;
