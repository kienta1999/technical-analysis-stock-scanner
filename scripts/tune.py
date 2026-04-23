#!/usr/bin/env python3
"""
Parameter tuner with train/test split and grid search.

  In-sample (tune on):     2024-04-20 → 2025-04-20  (Year 1)
  Out-of-sample (validate): 2025-04-20 → 2026-04-20  (Year 2)

Generates a Cartesian-product grid of ~108 variations across MIN_QUALITY_SCORE,
L1/L4 SL/TP multipliers, and L1 volume threshold.

OHLCV data is cached to data/raw_ohlcv_2y.pkl (7-day TTL) to avoid yfinance
rate-limit hits on repeated runs. Delete the file or pass --refresh to bust.

Output is intentionally minimal: only variations with alpha >=10pp on BOTH
windows are printed in the final table (plus baseline as reference). This
keeps the output cheap to read in long sessions.
"""

import sys
import time
import pickle
import itertools
import warnings
from pathlib import Path
from datetime import date

warnings.filterwarnings("ignore")

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
MAX_ATR_PCT = 4.0
BENCHMARK   = "SPY"

CACHE_PATH = Path(__file__).parent.parent / "data" / "raw_ohlcv_2y.pkl"
CACHE_TTL_DAYS = 7

ALPHA_REPORT_FLOOR = 10.0  # only print variations with alpha >= this on BOTH windows


# ─────────────────────────────────────────────────────────────────────────────
# Data caching
# ─────────────────────────────────────────────────────────────────────────────

def fetch_or_load_data(tickers, start, end, refresh=False):
    if not refresh and CACHE_PATH.exists():
        age_days = (time.time() - CACHE_PATH.stat().st_mtime) / 86400
        if age_days < CACHE_TTL_DAYS:
            print(f"Loaded OHLCV from cache ({age_days:.1f}d old). "
                  f"Pass --refresh to bust.", flush=True)
            with open(CACHE_PATH, "rb") as f:
                return pickle.load(f)
        else:
            print(f"Cache stale ({age_days:.1f}d > {CACHE_TTL_DAYS}d TTL), "
                  f"refreshing...", flush=True)

    print(f"Downloading OHLCV ({start} → {end})...", flush=True)
    raw = yf.download(
        tickers, start=start, end=end, interval="1d",
        auto_adjust=True, progress=False,
    )
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(raw, f)
    print(f"Cached to {CACHE_PATH}", flush=True)
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Backtest simulation
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Grid generation
# ─────────────────────────────────────────────────────────────────────────────

GRID = {
    "MIN_QUALITY_SCORE":  [25, 50, 60],
    "L1_SL_ATR":          [1.5, 2.0, 2.5],
    "L1_TP_ATR":          [4.0, 5.0],
    "L1_MIN_VOL_RATIO":   [1.0, 1.3],
    "L4_TP_ATR":          [3.0, 4.0, 5.0],
}
# = 3 × 3 × 2 × 2 × 3 = 108 variations


def build_variations():
    variations = [("Baseline", {})]
    keys = list(GRID.keys())
    for combo in itertools.product(*GRID.values()):
        overrides = dict(zip(keys, combo))
        # Compact name: MQ50_L1SL2.0_L1TP5.0_L1V1.3_L4TP4.0
        short = (
            f"MQ{overrides['MIN_QUALITY_SCORE']}"
            f"_L1SL{overrides['L1_SL_ATR']}"
            f"_L1TP{overrides['L1_TP_ATR']}"
            f"_L1V{overrides['L1_MIN_VOL_RATIO']}"
            f"_L4TP{overrides['L4_TP_ATR']}"
        )
        variations.append((short, overrides))
    return variations


# ─────────────────────────────────────────────────────────────────────────────

def main():
    refresh = "--refresh" in sys.argv

    universe = load_universe()
    tickers = universe["Ticker"].tolist()
    raw = fetch_or_load_data(
        tickers + [BENCHMARK],
        start="2023-07-01", end=OUT_END.isoformat(),
        refresh=refresh,
    )
    all_dates = sorted(set(raw.index.date))
    in_dates  = [d for d in all_dates if IN_START  <= d <= IN_END]
    out_dates = [d for d in all_dates if OUT_START <= d <= OUT_END]

    in_bench  = spy_return(raw, in_dates)
    out_bench = spy_return(raw, out_dates)
    print(f"\nIn-sample  ({IN_START} → {IN_END}):    {len(in_dates)} days, SPY {in_bench:+.1f}%")
    print(f"Out-sample ({OUT_START} → {OUT_END}):  {len(out_dates)} days, SPY {out_bench:+.1f}%")

    baseline = snapshot_baseline()
    variations = build_variations()
    n = len(variations)

    print(f"\nRunning {n} variations (each tested on both windows)...")
    print("Progress: ", end="", flush=True)

    results = []
    t0 = time.time()
    for i, (name, overrides) in enumerate(variations):
        apply_overrides(baseline)
        apply_overrides(overrides)
        in_res  = run_one(raw, tickers, all_dates, in_dates, in_bench)
        out_res = run_one(raw, tickers, all_dates, out_dates, out_bench)
        results.append({"name": name, "overrides": overrides,
                        "in": in_res, "out": out_res})
        print(".", end="", flush=True)
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f" [{i+1}/{n}, {elapsed/60:.1f}min]", flush=True)
            print("Progress: ", end="", flush=True)
    print(f"\nDone in {(time.time()-t0)/60:.1f}min.\n")

    base = results[0]
    base_in_a, base_out_a = base["in"]["alpha"], base["out"]["alpha"]

    print("="*88)
    print(f"  BASELINE: in α={base_in_a:+.1f}pp  |  out α={base_out_a:+.1f}pp  "
          f"|  in trades={base['in']['trades']}, out trades={base['out']['trades']}")
    print("="*88)

    qualifying = [r for r in results[1:]
                  if r["in"]["alpha"] >= ALPHA_REPORT_FLOOR
                  and r["out"]["alpha"] >= ALPHA_REPORT_FLOOR]

    print(f"\n{len(qualifying)} of {n-1} variations cleared "
          f"the +{ALPHA_REPORT_FLOOR:.0f}pp floor on BOTH windows:\n")

    if not qualifying:
        # Fall back: show the top-5 by min(in, out) alpha so we still learn something
        ranked = sorted(results[1:],
                        key=lambda r: min(r["in"]["alpha"], r["out"]["alpha"]),
                        reverse=True)
        print("  (none qualified — showing top-5 by min-of-two-alphas as fallback)\n")
        print(f"  {'variation':<55} {'in α':>9} {'out α':>9} {'min':>9}")
        print("  " + "-"*86)
        for r in ranked[:5]:
            in_a, out_a = r["in"]["alpha"], r["out"]["alpha"]
            print(f"  {r['name']:<55} {in_a:>+8.1f}p {out_a:>+8.1f}p "
                  f"{min(in_a, out_a):>+8.1f}p")
        return

    # Sort by sum of alphas (proxy for both-window strength)
    qualifying.sort(key=lambda r: r["in"]["alpha"] + r["out"]["alpha"], reverse=True)
    print(f"  {'variation':<55} {'in α':>9} {'out α':>9} {'sum':>9}")
    print("  " + "-"*86)
    for r in qualifying:
        in_a, out_a = r["in"]["alpha"], r["out"]["alpha"]
        print(f"  {r['name']:<55} {in_a:>+8.1f}p {out_a:>+8.1f}p "
              f"{in_a + out_a:>+8.1f}p")


if __name__ == "__main__":
    main()
