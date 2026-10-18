"""Rendering for edge stats."""
CONF_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠", "INSUFFICIENT": "⚪"}


def _fmt(v, digits=2, signed=False):
    if v is None: return "—"
    return f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"


def one_line(edge):
    if not edge or not edge.get("found"):
        return "📊 Historical edge: <i>insufficient data</i>"
    return (f"📊 Historical edge ({edge['level']}): "
            f"<b>n={edge['n_samples']}</b> · "
            f"win {edge['win_rate']*100:.0f}% · "
            f"avg {edge['avg_change_pct']:+.2f}% · {edge['confidence']}")


def block(edge):
    if not edge or not edge.get("found"):
        return f"\n📊 HISTORICAL EDGE — not available  ({(edge or {}).get('reason','?')})\n"
    e = CONF_EMOJI.get(edge["confidence"], "⚪")
    lines = ["",
             f"📊 HISTORICAL EDGE  ({edge['level']} · {e} {edge['confidence']})",
             "",
             f"   feature:  {edge['feature_key']}",
             f"   samples:  n={edge['n_samples']} (W {edge['wins']} · "
             f"L {edge['losses']} · F {edge['flats']})",
             f"   win rate: {edge['win_rate']*100:.1f}%",
             f"   avg move: {_fmt(edge['avg_change_pct'], signed=True)} ±"
             f"{_fmt(edge['standard_error'])}",
             f"   MFE/MAE:  +{_fmt(edge['avg_mfe_pct'])} / -{_fmt(edge['avg_mae_pct'])}"]
    if edge.get("fallback_note"):
        lines.append(f"   note:     {edge['fallback_note']}")
    lines.append("")
    return "\n".join(lines)


def table(rows, symbol="GOLD", level="L2"):
    if not rows:
        return f"\nNo edge stats for {symbol} {level}.\n"
    out = ["", f"📊 TOP EDGES — {symbol} · {level}", "",
           f"   {'win%':>5}  {'n':>4}  {'avg':>8}  {'MFE':>7}  {'MAE':>7}  "
           f"{'conf':<7}  feature",
           "   " + "─" * 100]
    for r in rows:
        wr = (r["win_rate"] or 0) * 100
        out.append(
            f"   {wr:>5.1f}  {r['n_samples']:>4}  "
            f"{_fmt(r['avg_change_pct'], signed=True):>8}  "
            f"{_fmt(r['avg_mfe_pct']):>7}  "
            f"{_fmt(r['avg_mae_pct']):>7}  "
            f"{r['confidence']:<7}  {r['feature_key']}")
    out.append("")
    return "\n".join(out)