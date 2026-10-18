"""Simulator rendering."""
LINE = "─" * 62


def _decimals(v):
    if v >= 1000: return 2
    if v >= 100: return 2
    if v >= 10: return 3
    if v >= 1: return 4
    if v >= 0.01: return 5
    return 6


def render(result, *, release_hint=None):
    if result.get("error"):
        extra = ""
        if "raw_lot" in result:
            extra = (f"\n  requested risk: {result['risk_amount']:.2f}"
                     f"\n  stop: {result['pips_stop']} pips")
        return f"\n❌ {result['error']}\n{extra}\n"

    d = result["direction"]
    arrow = "▲ LONG" if d == "LONG" else "▼ SHORT"
    out = ["", f"╔{LINE}╗",
           f"  📋 {result['symbol']}  {arrow}", f"╠{LINE}╣", "",
           "  ACCOUNT",
           f"    Balance:       {result['balance']:>12,.2f} {result['account_ccy']}",
           f"    Risk target:   {result['risk_pct']:>12.3f} %",
           f"    Risk amount:   {result['risk_amount']:>12,.2f} {result['account_ccy']}", "",
           "  LEVELS",
           f"    Entry:         {result['entry']:>12.{_decimals(result['entry'])}f}",
           f"    Stop:          {result['stop']:>12.{_decimals(result['stop'])}f}   "
           f"({result['pips_stop']} pips · {result['stop_pct']:.3f}%)"]
    if result.get("target") is not None:
        out.append(f"    Target:        {result['target']:>12.{_decimals(result['target'])}f}   "
                   f"({result['pips_target']} pips · {result['target_pct']:.3f}%)")
    out += ["", "  POSITION",
            f"    Lot size:      {result['lot_size']:>12.2f}",
            f"    Units:         {result['units']:>12,.0f}",
            f"    Pip value:     {result['pip_value_per_lot']:>12.2f} {result['account_ccy']}", ""]
    if result.get("potential_profit") is not None:
        out += [f"    Potential loss:   {result['potential_loss']:>10,.2f}",
                f"    Potential profit: {result['potential_profit']:>10,.2f}",
                f"    R:R:              {result['reward_risk']:>10.2f}", ""]
    if release_hint and release_hint.get("found"):
        out += [f"  KELLY (release {release_hint['release_id']})",
                f"    Suggested:    {release_hint['suggested_risk_pct']:>12.3f} %",
                f"    Amount:       {release_hint['suggested_risk_amount']:>12,.2f}",
                f"    R:R:          {release_hint['suggested_rr']:>12.2f}", ""]
    if result["warnings"]:
        out.append("  ⚠️  WARNINGS")
        for w in result["warnings"]: out.append(f"    · {w}")
        out.append("")
    if result["suggestions"]:
        out.append("  💡 SUGGESTIONS")
        for s in result["suggestions"]: out.append(f"    · {s}")
        out.append("")
    out.append(f"╚{LINE}╝")
    out.append("")
    return "\n".join(out)


def render_history(rows):
    if not rows: return "\nNo simulations yet.\n"
    out = ["", f"  {'when':<20} {'symbol':<8} {'dir':<6} {'bal':>10} "
           f"{'risk%':>6} {'lots':>7} {'pips':>6} {'R:R':>5} {'P/L':>9}",
           "  " + "─" * 95]
    for r in rows:
        when = (r["ts_utc"] or "")[:19].replace("T", " ")
        arrow = "L" if r["direction"] == "LONG" else "S"
        rr = f"{r['reward_risk']:.2f}" if r["reward_risk"] else "—"
        pl = f"{r['potential_profit']:+.1f}" if r["potential_profit"] is not None else "—"
        out.append(f"  {when:<20} {r['symbol']:<8} {arrow:<6} "
                   f"{r['account_balance']:>10,.0f} {r['risk_pct']:>6.2f} "
                   f"{r['lot_size']:>7.2f} {r['pips_stop']:>6.1f} "
                   f"{rr:>5} {pl:>9}")
    out.append("")
    return "\n".join(out)