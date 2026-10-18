"""Event dictionary — the economic-meaning layer."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EventDef:
    code: str
    name: str
    country: str
    category: str          # inflation | labour | growth | policy
    unit: str
    higher_is: str         # BULLISH | BEARISH | NEUTRAL for domestic currency
    tier: int
    inline_tol: float
    large_thr: float
    notes: str = ""


EVENTS = [
    # ─── US inflation ───
    EventDef("US_CPI_YOY",      "US CPI YoY",              "US", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("US_CPI_MOM",      "US CPI MoM",              "US", "inflation", "%", "BULLISH", 1, 0.05, 0.15),
    EventDef("US_CORE_CPI_YOY", "US Core CPI YoY",         "US", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("US_CORE_CPI_MOM", "US Core CPI MoM",         "US", "inflation", "%", "BULLISH", 1, 0.05, 0.15),
    EventDef("US_PPI_YOY",      "US PPI YoY",              "US", "inflation", "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("US_CORE_PPI_YOY", "US Core PPI YoY",         "US", "inflation", "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("US_PCE_YOY",      "US Core PCE YoY",         "US", "inflation", "%", "BULLISH", 1, 0.05, 0.15),

    # ─── US labour ───
    EventDef("US_NFP",          "US Nonfarm Payrolls",     "US", "labour",    "K", "BULLISH", 1, 10.0, 50.0),
    EventDef("US_UNEMPLOYMENT", "US Unemployment Rate",    "US", "labour",    "%", "BEARISH", 1, 0.05, 0.20),
    EventDef("US_AHE_MOM",      "US Avg Hourly Earnings MoM", "US", "labour", "%", "BULLISH", 1, 0.05, 0.15),
    EventDef("US_JOBLESS_CLAIMS", "US Initial Claims",     "US", "labour",    "K", "BEARISH", 2, 5.0, 20.0),
    EventDef("US_CONTINUING_CLAIMS", "US Continuing Claims", "US", "labour", "K", "BEARISH", 2, 10.0, 50.0),

    # ─── US growth ───
    EventDef("US_RETAIL_MOM",   "US Retail Sales MoM",     "US", "growth",    "%", "BULLISH", 2, 0.05, 0.30),
    EventDef("US_GDP_QOQ",      "US GDP QoQ (annualised)","US", "growth",    "%", "BULLISH", 2, 0.10, 0.50),
    EventDef("US_ISM_PMI",      "US ISM Manufacturing PMI","US", "growth",   "index", "BULLISH", 2, 0.30, 1.00),

    # ─── EU ───
    EventDef("EU_CPI_YOY",      "Eurozone CPI YoY",        "EU", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("EU_CORE_CPI_YOY", "Eurozone Core CPI YoY",   "EU", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("EU_PPI_YOY",      "Eurozone PPI YoY",        "EU", "inflation", "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("EU_GDP_QOQ",      "Eurozone GDP QoQ",        "EU", "growth",    "%", "BULLISH", 2, 0.05, 0.30),
    EventDef("EU_EMPLOYMENT_QOQ", "Eurozone Employment QoQ", "EU", "labour",  "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("EU_UNEMPLOYMENT", "Eurozone Unemployment",   "EU", "labour",    "%", "BEARISH", 2, 0.05, 0.15),
    EventDef("EU_RETAIL_MOM",   "Eurozone Retail Sales MoM","EU", "growth",   "%", "BULLISH", 3, 0.05, 0.30),
    EventDef("EU_PMI_MFG",      "Eurozone Mfg PMI",        "EU", "growth",    "index", "BULLISH", 2, 0.30, 1.00),

    # ─── UK ───
    EventDef("UK_CPI_YOY",      "UK CPI YoY",              "UK", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("UK_CORE_CPI_YOY", "UK Core CPI YoY",         "UK", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("UK_GDP_QOQ",      "UK GDP QoQ",              "UK", "growth",    "%", "BULLISH", 2, 0.05, 0.30),
    EventDef("UK_UNEMPLOYMENT", "UK Unemployment Rate",    "UK", "labour",    "%", "BEARISH", 2, 0.05, 0.15),
    EventDef("UK_AVG_EARNINGS", "UK Avg Weekly Earnings",  "UK", "labour",    "%", "BULLISH", 1, 0.05, 0.15),

    # ─── Japan ───
    EventDef("JP_CPI_YOY",      "Japan CPI YoY",           "JP", "inflation", "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("JP_CORE_CPI_YOY", "Japan Core CPI YoY",      "JP", "inflation", "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("JP_GDP_QOQ",      "Japan GDP QoQ",           "JP", "growth",    "%", "BULLISH", 2, 0.05, 0.30),
    EventDef("JP_TANKAN",       "Japan Tankan Large Mfg",  "JP", "growth",    "index", "BULLISH", 2, 0.30, 1.00),

    # ─── Australia ───
    EventDef("AU_CPI_YOY",      "Australia CPI YoY",       "AU", "inflation", "%", "BULLISH", 2, 0.05, 0.20),
    EventDef("AU_EMPLOYMENT",   "Australia Employment Chg","AU", "labour",    "K", "BULLISH", 2, 5.0, 25.0),
    EventDef("AU_UNEMPLOYMENT", "Australia Unemployment",  "AU", "labour",    "%", "BEARISH", 2, 0.05, 0.20),

    # ─── Canada ───
    EventDef("CA_CPI_MOM",      "Canada CPI MoM",          "CA", "inflation", "%", "BULLISH", 1, 0.05, 0.20),
    EventDef("CA_EMPLOYMENT",   "Canada Employment Change","CA", "labour",    "K", "BULLISH", 1, 5.0, 25.0),
    EventDef("CA_UNEMPLOYMENT", "Canada Unemployment Rate","CA", "labour",    "%", "BEARISH", 1, 0.05, 0.20),
]

BY_CODE = {e.code: e for e in EVENTS}


def seed(conn):
    """Upsert all event definitions."""
    for e in EVENTS:
        conn.execute("""
            INSERT INTO event_definitions
              (code, name, country, category, unit, higher_is,
               tier, inline_tol, large_thr, notes)
            VALUES (:code, :name, :country, :category, :unit, :higher_is,
                    :tier, :inline_tol, :large_thr, :notes)
            ON CONFLICT(code) DO UPDATE SET
              name=excluded.name, country=excluded.country,
              category=excluded.category, unit=excluded.unit,
              higher_is=excluded.higher_is, tier=excluded.tier,
              inline_tol=excluded.inline_tol,
              large_thr=excluded.large_thr,
              notes=excluded.notes
        """, asdict(e))
    conn.commit()