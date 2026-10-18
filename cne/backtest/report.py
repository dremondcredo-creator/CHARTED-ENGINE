"""Backtest rendering."""

def _fmt(v, digits=2, signed=False):
    if v is None: return "—"
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def summary(run):
    if not run or run.get("error"):
        return f"\n⚠️  {(run or {}).get('error', 'unknown')}\n"
    return "\n".join([
        "", f"═══ {run['rules_name']} · {run['symbol']} ═══",
        f"  window:  {run['start_ts_utc'][:10]} → {run['end_ts_utc'][:10]}",
        f"  trades:  {run['n_trades']} (W {run['n_wins']} · L {run['n_losses']})",
        f"  win:     {run['win_rate']*100:.1f}%",
        f"  expect:  {_fmt(run['expectancy_pct'], signed=True)}%",
        f"  PF:      {_fmt(run['profit_factor'])}",
        f"  Sharpe:  {_fmt(run['sharpe_like'])}",
        f"  maxDD:   {_fmt(run['max_drawdown_pct'])}%",
        f"  total:   {_fmt(run['total_return_pct'], signed=True)}%",
        ""])


def table(runs):
    lines = ["", f"  {'rule':<28} {'n':>4} {'win%':>6} {'expect':>8} "
             f"{'PF':>5} {'Sh':>6} {'maxDD':>7} {'total':>8}",
             "  " + "─" * 85]
    for r in runs:
        if r.get("error"):
            lines.append(f"  {r.get('rules_name', '?'):<28} {r['error'][:45]}")
            continue
        lines.append(
            f"  {r['rules_name']:<28} {r['n_trades']:>4} "
            f"{r['win_rate']*100:>5.1f} "
            f"{_fmt(r['expectancy_pct'], signed=True):>8} "
            f"{_fmt(r['profit_factor']):>5} "
            f"{_fmt(r['sharpe_like']):>6} "
            f"{_fmt(r['max_drawdown_pct']):>6}% "
            f"{_fmt(r['total_return_pct'], signed=True):>7}%")
    lines.append("")
    return "\n".join(lines)