"""Cross-country bias and relative differentials."""
from __future__ import annotations

from cne.core import macro as macro_mod

COUNTRIES = ("US", "EU", "UK", "JP", "CA", "AU")


def all_biases(conn, window_days=30):
    return {c: macro_mod.compute_macro_score(conn, c, window_days) for c in COUNTRIES}


def relative_bias(conn, a, b, window_days=30):
    sa = macro_mod.compute_macro_score(conn, a, window_days)
    sb = macro_mod.compute_macro_score(conn, b, window_days)
    diff = sa["total"] - sb["total"]
    if diff >= 0.5:   favoured = a
    elif diff <= -0.5: favoured = b
    else:              favoured = "NEUTRAL"
    return {"a_country": a, "b_country": b,
            "a_total": sa["total"], "b_total": sb["total"],
            "a_label": sa["label"], "b_label": sb["label"],
            "diff": round(diff, 3), "favoured": favoured,
            "notes": f"{a} {sa['label']} ({sa['total']:+.2f}) vs {b} {sb['label']} ({sb['total']:+.2f})"}