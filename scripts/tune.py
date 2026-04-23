#!/usr/bin/env python3
"""
Parameter tuner with train/test split.

  In-sample (tune on):     2024-04-20 → 2025-04-20  (Year 1)
  Out-of-sample (validate): 2025-04-20 → 2026-04-20  (Year 2)

A variation is real alpha only if it beats baseline on BOTH windows.
A win on in-sample only = overfit, discard.

Downloads OHLCV once and runs many parameter combos against the same data.
"""

import warnings
warnings.filterwarnings("ignore")

from datetime import date
import pandas as pd
import yfinance as yf

from universe import load_universe
from indicators import compute
import signals as sg
from backtest import simulate_trade

IN_START   = date(2024, 4, 20)
IN_END     = date(2025, 4, 20)
OUT_START  = date(2025, 4, 20)
OUT_END    = date(2026, 4, 20)

CAPITAL_INIT = 10_000.0
TIME_STOP_DAYS = 40
MAX_ATR_PCT = 4.0
BENCHMARK   = "SPY"


def simulate(raw, tickers, all_dates, bt_dates):
    """Run one backtest pass. Reads sg.MIN_QUALITY_SCORE dynamically."""
    capital   = CAPITAL_INIT
    trades    = []
    active    = None
    scan_from = bt_dates[0]

    for today in bt_dates:
        if active and today >= active["exit_date"]:
            pnl_usd = capital * (active["pnl_pct"] / 100)
            capital += pnl_usd
            trades.append(active)
            active = None
            scan_from = today

        if active is None and today >= scan_from:
            today_ts = pd.Timestamp(today)
            candidates = []

            for ticker in tickers:
                try:
                    df = pd.DataFrame({
                        "Open":   raw["Open"][ticker],
                        "High":   raw["High"][ticker],
                        "Low":    raw["Low"][ticker],
                        "Close":  raw["Close"][ticker],
                        "Volume": raw["Volume"][ticker],
                    }).loc[:today_ts].dropna()
                except (KeyError, TypeError):
                    continue
                if len(df) < 200:
                    continue

                ind = compute(df)
                if not ind:
                    continue
                if ind.get("atr_pct", 0) > MAX_ATR_PCT:
                    continue

                for s in sg.score(ind):
                    q = sg.quality({**ind, **s})
                    candidates.append({"ticker": ticker, "quality": q, **ind, **s})

            if not candidates:
                continue

            best = max(candidates, key=lambda x: x["quality"])
            if best["quality"] < sg.MIN_QUALITY_SCORE:
                continue

            future = [d for d in all_dates if d > today]
            if not future:
                continue
            entry_date = future[0]

            result = simulate_trade(
                ticker=best["ticker"], direction=best["direction"],
                entry=best["entry"], sl=best["sl"], tp=best["tp"],
                entry_date=entry_date, raw=raw, all_dates=all_dates,
            )
            if not result:
                continue
            active = result

    if active:
        last_date = bt_dates[-1]
        try:
            ep = float(raw["Close"][active["ticker"]].loc[pd.Timestamp(last_date)])
        except Exception:
            ep = active["entry"]
        pnl_pct = ((ep - active["entry"]) / active["entry"] * 100
                   if active["direction"] == "LONG"
                   else (active["entry"] - ep) / active["entry"] * 100)
        capital += capital * (pnl_pct / 100)
        active["pnl_pct"] = pnl_pct
        trades.append(active)

    return capital, trades


def apply_overrides(overrides: dict):
    for k, v in overrides.items():
        setattr(sg, k, v)


def snapshot_baseline() -> dict:
    return {k: getattr(sg, k) for k in dir(sg)
            if (k.startswith(("Q_", "L1_", "L2_", "L3_", "L4_"))
                or k == "MIN_QUALITY_SCORE")}


def spy_return(raw, bt_dates):
    spy = raw["Close"][BENCHMARK].dropna()
    spy_w = spy[(spy.index.date >= bt_dates[0]) & (spy.index.date <= bt_dates[-1])]
    return (spy_w.iloc[-1] / spy_w.iloc[0] - 1) * 100


def run_one(raw, tickers, all_dates, bt_dates, bench_ret):
    end_cap, trades = simulate(raw, tickers, all_dates, bt_dates)
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    ret = (end_cap / CAPITAL_INIT - 1) * 100
    alpha = ret - bench_ret
    return {"trades": n, "win_rate": (wins/n if n else 0),
            "return": ret, "alpha": alpha, "end_cap": end_cap}


def main():
    print("Downloading OHLCV (one-time)...", flush=True)
    universe = load_universe()
    tickers = universe["Ticker"].tolist()
    raw = yf.download(
        tickers + [BENCHMARK],
        start="2023-07-01", end=OUT_END.isoformat(),
        interval="1d", auto_adjust=True, progress=False,
    )
    all_dates = sorted(set(raw.index.date))
    in_dates  = [d for d in all_dates if IN_START  <= d <= IN_END]
    out_dates = [d for d in all_dates if OUT_START <= d <= OUT_END]

    in_bench  = spy_return(raw, in_dates)
    out_bench = spy_return(raw, out_dates)
    print(f"In-sample  ({IN_START} → {IN_END}):    {len(in_dates)} days, SPY {in_bench:+.1f}%")
    print(f"Out-sample ({OUT_START} → {OUT_END}):  {len(out_dates)} days, SPY {out_bench:+.1f}%\n")

    baseline = snapshot_baseline()

    variations = [
        ("Baseline", {}),
        ("B: min_quality=55", {"MIN_QUALITY_SCORE": 55}),
        ("E: heavier vol weight", {"Q_VOL_MAX_PTS": 50, "Q_RSI_SWEET_PTS": 20, "Q_RSI_OK_PTS": 10}),
        ("F: B+E combined",
            {"MIN_QUALITY_SCORE": 55, "Q_VOL_MAX_PTS": 50,
             "Q_RSI_SWEET_PTS": 20, "Q_RSI_OK_PTS": 10}),
        ("G: tighter SL (L1/L3/L4 SL 2.0->1.5)",
            {"L1_SL_ATR": 1.5, "L3_SL_ATR": 1.5, "L4_SL_ATR": 1.5}),
        ("H: min=45 (mid-pickier)", {"MIN_QUALITY_SCORE": 45}),
        ("I: min=50", {"MIN_QUALITY_SCORE": 50}),
        ("J: min=60", {"MIN_QUALITY_SCORE": 60}),
    ]

    results = []
    for name, overrides in variations:
        apply_overrides(baseline)
        apply_overrides(overrides)
        print(f"Running: {name}", flush=True)
        in_res  = run_one(raw, tickers, all_dates, in_dates, in_bench)
        out_res = run_one(raw, tickers, all_dates, out_dates, out_bench)
        results.append({"name": name, "in": in_res, "out": out_res})
        print(f"  in:  trades={in_res['trades']:2d}  ret={in_res['return']:+.1f}%  alpha={in_res['alpha']:+.1f}pp")
        print(f"  out: trades={out_res['trades']:2d}  ret={out_res['return']:+.1f}%  alpha={out_res['alpha']:+.1f}pp\n")

    base_in_alpha  = results[0]["in"]["alpha"]
    base_out_alpha = results[0]["out"]["alpha"]

    print("\n" + "="*100)
    print("  ROBUSTNESS CHECK — does the variation beat baseline on BOTH windows?")
    print("="*100)
    print(f"  {'variation':<48} {'in α':>10} {'out α':>10} {'Δ vs base':>12} {'verdict':>14}")
    print("  " + "-"*98)
    for r in results:
        in_a, out_a = r["in"]["alpha"], r["out"]["alpha"]
        delta_in  = in_a  - base_in_alpha
        delta_out = out_a - base_out_alpha
        if r["name"] == "Baseline":
            verdict = "—"
        elif delta_in > 0 and delta_out > 0:
            verdict = "ROBUST ✓"
        elif delta_in > 0 and delta_out <= 0:
            verdict = "OVERFIT ✗"
        elif delta_in <= 0 and delta_out > 0:
            verdict = "lucky out"
        else:
            verdict = "worse"
        print(f"  {r['name']:<48} {in_a:>+9.1f}p {out_a:>+9.1f}p "
              f"{delta_in:>+5.1f}/{delta_out:>+5.1f}p {verdict:>14}")


if __name__ == "__main__":
    main()
