#!/usr/bin/env python3
"""
Technical analysis scanner — long-only strategy.
  Per-ticker rule: only consider LONG setups when price > SMA200.
                   Price < SMA200 → no trade (do not short, do not fight the tape).
  Market-wide gate: SPY > 200DMA AND VIX < 30 required for LONG entries.
                    Gate closed → triggered setups are suppressed (parity with
                    backtest.simulate).
  Skips entries where ATR% > MAX_ATR_PCT (extreme-volatility guardrail).
  Scans all top-100 S&P 500 stocks.

Two entry points:
  - scan(): returns a structured dict with picks, market metadata, and the raw
    OHLCV frame. Used by paper_trade.py.
  - run():  calls scan() and prints the human-readable report. Used by the CLI.
"""

import sys
import warnings
warnings.filterwarnings("ignore")

try:
    import pandas as pd
    import yfinance as yf
    from universe import load_universe
    from indicators import compute, ticker_frame
    import signals as sg
    from signals import (score, quality, market_regime, long_regime_ok,
                         build_regime_series, rs_eligible,
                         MIN_QUALITY_SCORE,
                         MAX_ATR_PCT, VIX_MAX, SPY_MA_PERIOD, BENCHMARK)
except ImportError:
    print("ERROR: Missing dependencies. Run:")
    print("  uv pip install yfinance pandas lxml requests --python .venv/bin/python3")
    sys.exit(1)


def scan(force_refresh: bool = False, verbose: bool = True) -> dict | None:
    """
    Run the EOD scan and return a structured result dict.

    Returns None on data failure. Otherwise returns:
      {
        "scan_date":        ISO date of the last bar in the OHLCV download
        "gate_open":        bool — LONG entries allowed today?
        "gate_detail":      str — human-readable gate status
        "spy_price":        float | None
        "spy_ma":           float | None  (SPY 200DMA)
        "vix":              float | None
        "breadth_long":     int — # tickers above SMA200
        "breadth_short":    int — # tickers below SMA200
        "breadth_total":    int
        "rs_eligible":      set[str] | None — tickers passing RS filter (None if disabled)
        "long_rows":        list[dict] — long-universe table rows
        "short_rows":       list[dict] — short-universe table rows
        "triggered":        list[dict] — every triggered setup (pre-gate)
        "picks":             list[dict] — actionable LONG picks, sorted by quality desc,
                                          regime-allowed and ≥ MIN_QUALITY_SCORE
                                          (each: ticker, setup, direction, quality,
                                           entry, sl, tp, rsi, vol_ratio, atr_pct, rr)
        "raw":              pd.DataFrame — raw OHLCV (universe + SPY + ^VIX)
        "tickers":          list[str] — universe tickers
      }
    """
    universe = load_universe(force_refresh=force_refresh)
    tickers = universe["Ticker"].tolist()

    dl_tickers = tickers + [BENCHMARK, "^VIX"]
    if verbose:
        print(f"Downloading 1-year OHLCV for {len(dl_tickers)} tickers "
              f"(universe + {BENCHMARK} + ^VIX)...", flush=True)
    raw = yf.download(dl_tickers, period="1y", interval="1d",
                      auto_adjust=True, progress=False)
    if raw.empty:
        return None

    long_rows, short_rows, triggered = [], [], []
    rs_set = (rs_eligible(raw, tickers, raw.index[-1])
              if sg.RS_FILTER_ENABLED else None)

    for ticker in tickers:
        df = ticker_frame(raw, ticker)
        if df is None or len(df) < 200:
            continue

        ind = compute(df)
        if not ind:
            continue

        price   = ind["price"]
        sma200  = ind["sma200"]
        sma50   = ind["sma50"]
        pct200  = ind["price_vs_sma200_pct"]
        trend   = "↑" if ind["sma50_above_sma200"] else "↓"

        if ind["price_above_sma200"]:
            long_rows.append({
                "Ticker": ticker, "Price": price,
                "SMA50": sma50, "SMA200": sma200,
                "% vs SMA200": pct200, "RSI": round(ind["rsi"], 1),
                "Trend": trend,
            })
        else:
            short_rows.append({
                "Ticker": ticker, "Price": price,
                "SMA50": sma50, "SMA200": sma200,
                "% vs SMA200": pct200, "RSI": round(ind["rsi"], 1),
                "Trend": trend,
            })

        if ind.get("atr_pct", 0) > MAX_ATR_PCT:
            continue
        if rs_set is not None and ticker not in rs_set:
            continue
        for s in score(ind):
            row = {"Ticker": ticker, **ind, **s}
            row["quality"] = quality(row)
            triggered.append(row)

    spy_close, spy_ma, vix_close = build_regime_series(raw)
    today_ts = raw.index[-1]
    gate_open = True
    spy_px = spy_m = vix_px = None
    gate_detail = ""
    if spy_close is not None:
        gate_open = long_regime_ok(spy_close, spy_ma, vix_close, today_ts)
        try:
            spy_px = float(spy_close.asof(today_ts))
            spy_m  = float(spy_ma.asof(today_ts))
            vix_px = float(vix_close.asof(today_ts))
            gate_detail = (f"SPY {spy_px:.2f} vs {SPY_MA_PERIOD}DMA {spy_m:.2f} | "
                           f"VIX {vix_px:.1f} (limit {VIX_MAX:.0f})")
        except Exception:
            gate_detail = "regime values unavailable"
    else:
        gate_detail = "SPY or ^VIX missing — gate fail-open"

    picks = []
    if gate_open:
        for t in sorted(triggered, key=lambda x: x["quality"], reverse=True):
            if t.get("direction", "LONG") != "LONG":
                continue
            if t["quality"] < MIN_QUALITY_SCORE:
                continue
            picks.append({
                "ticker":     t["Ticker"],
                "setup":      t["setup"],
                "direction":  t["direction"],
                "quality":    t["quality"],
                "entry":      t["entry"],
                "sl":         t["sl"],
                "tp":         t["tp"],
                "rsi":        round(t["rsi"], 1),
                "vol_ratio":  round(t["vol_ratio"], 2),
                "atr_pct":    round(t["atr_pct"], 2),
                "rr":         t["rr"],
            })

    return {
        "scan_date":        raw.index[-1].date().isoformat(),
        "gate_open":        gate_open,
        "gate_detail":      gate_detail,
        "spy_price":        spy_px,
        "spy_ma":           spy_m,
        "vix":              vix_px,
        "breadth_long":     len(long_rows),
        "breadth_short":    len(short_rows),
        "breadth_total":    len(long_rows) + len(short_rows),
        "rs_eligible":      rs_set,
        "long_rows":        long_rows,
        "short_rows":       short_rows,
        "triggered":        triggered,
        "picks":            picks,
        "raw":              raw,
        "tickers":          tickers,
    }


def run(force_refresh: bool = False) -> dict | None:
    """CLI: run scan and print the human-readable report. Returns the scan dict."""
    result = scan(force_refresh=force_refresh, verbose=True)
    if result is None:
        print("ERROR: No data returned.")
        sys.exit(1)

    long_rows  = result["long_rows"]
    short_rows = result["short_rows"]
    triggered  = result["triggered"]
    rs_set     = result["rs_eligible"]
    gate_open  = result["gate_open"]

    long_count  = len(long_rows)
    short_count = len(short_rows)
    total       = long_count + short_count

    regime = market_regime(long_count, total)
    print(f"\n{'='*66}")
    print(f"  MARKET BREADTH | {regime}")
    status = "OPEN — LONG entries allowed" if gate_open else "BLOCKED — LONG entries suppressed"
    print(f"  REGIME GATE    | {status}")
    print(f"                 | {result['gate_detail']}")
    if rs_set is not None:
        print(f"  RS FILTER      | top {int(sg.RS_TOP_PCT*100)}% by 3M ∩ 6M return — "
              f"{len(rs_set)} eligible of {len(result['tickers'])}")
    print(f"{'='*66}")

    if long_rows:
        ldf = pd.DataFrame(long_rows).sort_values("% vs SMA200", ascending=False).reset_index(drop=True)
        print(f"\n── LONG UNIVERSE  ({long_count} stocks — price > SMA200) ──")
        print(ldf.to_string(index=False))

    if short_rows:
        sdf = pd.DataFrame(short_rows).sort_values("% vs SMA200", ascending=True).reset_index(drop=True)
        print(f"\n── SHORT UNIVERSE  ({short_count} stocks — price < SMA200) ──")
        print(sdf.to_string(index=False))

    if not gate_open:
        n = len(triggered)
        if n:
            sample = ", ".join(f"{t['Ticker']}({t['setup']})" for t in triggered[:5])
            if n > 5:
                sample += f", +{n-5} more"
            print(f"\nREGIME GATE CLOSED — suppressing {n} LONG trigger(s): {sample}")
            print("Do not enter. Wait for SPY > 200DMA AND VIX < 30.")
        else:
            print("\nREGIME GATE CLOSED — no triggers anyway. Stay in cash.")
        return result

    if not triggered:
        print("\nNo setups triggered today.")
        return result

    setup_df = pd.DataFrame(triggered).sort_values("quality", ascending=False)
    longs  = setup_df[setup_df["direction"] == "LONG"]
    shorts = setup_df[setup_df["direction"] == "SHORT"]

    display_cols = ["Ticker", "setup", "quality", "entry", "sl", "tp", "rr",
                    "rsi", "vol_ratio", "atr_pct", "timeframe", "notes"]

    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        if subset.empty:
            continue
        cols = [c for c in display_cols if c in subset.columns]
        print(f"\n{'='*66}")
        print(f"  {label} SETUPS  ({len(subset)} found — sorted by quality desc)")
        print(f"{'='*66}")
        print(subset[cols].to_string(index=False))

    print(f"\n── Setup Detail ──")
    for _, row in setup_df.iterrows():
        arrow = "▲" if row["direction"] == "LONG" else "▼"
        print(f"\n  {arrow} {row['Ticker']} | {row['setup']} | {row['direction']} | Quality: {row['quality']}/100")
        print(f"    Entry: {row['entry']}  SL: {row['sl']}  TP: {row['tp']}  R:R {row['rr']}")
        print(f"    RSI: {row['rsi']:.0f}  |  Vol: {row['vol_ratio']:.1f}x MA  |  ATR: {row['atr_pct']:.1f}%")
        print(f"    MACD hist: {row['macd_hist']:+.3f}  |  BB pos: {row['above_mid_5d']}/5d above mid")
        print(f"    VWAP dist: {row['price_vs_vwap_pct']:+.1f}%  |  SMA50>SMA200: {row['sma50_above_sma200']}")
        print(f"    Timeframe: {row['timeframe']}")
        print(f"    Notes: {row['notes']}")

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Force refresh universe cache")
    args = parser.parse_args()
    run(force_refresh=args.refresh)
