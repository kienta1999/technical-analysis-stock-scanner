#!/usr/bin/env python3
"""
Paper trade simulator — runs daily, mirrors the live algo's rules.

  Start: $10,000 on the first run.
  Slots: MAX_SLOTS independent sub-accounts (signals.MAX_SLOTS, currently 1).
  Entries: take the top-quality pick from sma200_filter.scan() that isn't
           already held; buy at the scan's entry price (= last bar close).
  Exits:   per-position, advance through every new bar since last run; check
           SL → TP → trailing-to-BE (50% to TP) → 40-day time stop, in that
           order — same as backtest.simulate_trade.

Persists state across runs in logs/paper_state.json. Appends every day to:
  - logs/paper_trades.csv      (BUY / BE_MOVE / SELL_SL / SELL_TP / SELL_TIME)
  - logs/paper_portfolio.csv   (one row per run: cash, mark-to-market value, regime)
"""

import argparse
import csv
import json
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd

import signals as sg
from signals import MIN_QUALITY_SCORE
from sma200_filter import scan

REPO_ROOT      = Path(__file__).resolve().parent.parent
LOGS_DIR       = REPO_ROOT / "logs"
STATE_FILE     = LOGS_DIR / "paper_state.json"
TRADES_CSV     = LOGS_DIR / "paper_trades.csv"
PORTFOLIO_CSV  = LOGS_DIR / "paper_portfolio.csv"

STARTING_CAPITAL = 10_000.0
TIME_STOP_DAYS   = 40

TRADE_COLS = [
    "run_date", "scan_date", "action", "slot", "ticker", "setup", "quality",
    "price", "shares", "sl", "tp", "be_trigger",
    "pnl_pct", "pnl_usd", "slot_cash_after", "portfolio_value",
    "days_held", "note",
]

PORTFOLIO_COLS = [
    "run_date", "scan_date", "cash", "positions_value", "total_value",
    "return_pct", "open_count",
    "gate_open", "spy_price", "spy_ma", "vix",
    "scan_picks_count", "top_pick", "open_tickers",
]


# ─────────────────────────────────────────────────────────────────────────────
# State persistence
# ─────────────────────────────────────────────────────────────────────────────

def load_state(today_iso: str) -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open() as f:
            state = json.load(f)
        if state.get("max_slots") != sg.MAX_SLOTS:
            print(f"WARN: state max_slots={state.get('max_slots')} != "
                  f"signals.MAX_SLOTS={sg.MAX_SLOTS}. Keeping state value to avoid "
                  f"corrupting per-slot cash buckets.", flush=True)
        return state
    n = sg.MAX_SLOTS
    return {
        "starting_capital": STARTING_CAPITAL,
        "starting_date":    today_iso,
        "max_slots":        n,
        "slots":            [{"idx": i, "cash": STARTING_CAPITAL / n, "position": None}
                              for i in range(n)],
        "last_run_date":    None,
    }


def save_state(state: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2, default=str)
    tmp.replace(STATE_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# CSV append (auto-headers)
# ─────────────────────────────────────────────────────────────────────────────

def append_row(path: Path, cols: list[str], row: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


# ─────────────────────────────────────────────────────────────────────────────
# Position management — advance one position through new bars
# ─────────────────────────────────────────────────────────────────────────────

def advance_position(pos: dict, raw: pd.DataFrame) -> tuple[list[dict], dict]:
    """Walk pos through every bar > pos['last_processed_date']. Returns
    (events, pos). Events are ordered: BE_MOVE (if any), then exit (if any).
    Updates pos['days_held'], pos['sl'], pos['be_moved'], pos['last_processed_date']."""
    events = []
    ticker = pos["ticker"]
    try:
        h = raw["High"][ticker].dropna()
        l = raw["Low"][ticker].dropna()
        c = raw["Close"][ticker].dropna()
    except KeyError:
        return events, pos

    last_proc = pd.Timestamp(pos["last_processed_date"])
    new_bars = [ts for ts in h.index if ts > last_proc]

    for ts in new_bars:
        bar_date = ts.date().isoformat()
        try:
            hi = float(h.loc[ts]); lo = float(l.loc[ts]); cl = float(c.loc[ts])
        except KeyError:
            continue
        pos["days_held"] += 1
        pos["last_processed_date"] = bar_date

        # Trailing-to-breakeven (one-way ratchet)
        if not pos["be_moved"] and hi >= pos["be_trigger"]:
            old_sl = pos["sl"]
            new_sl = max(pos["sl"], pos["entry"])
            if new_sl != old_sl:
                pos["sl"] = new_sl
                events.append({
                    "kind": "BE_MOVE", "bar_date": bar_date,
                    "old_sl": old_sl, "new_sl": new_sl,
                })
            pos["be_moved"] = True

        # SL → TP → time stop (in that order, mirroring backtest.simulate_trade)
        if lo <= pos["sl"]:
            events.append({"kind": "SELL_SL", "bar_date": bar_date,
                           "exit_price": pos["sl"]})
            return events, pos
        if hi >= pos["tp"]:
            events.append({"kind": "SELL_TP", "bar_date": bar_date,
                           "exit_price": pos["tp"]})
            return events, pos
        if pos["days_held"] >= TIME_STOP_DAYS:
            events.append({"kind": "SELL_TIME", "bar_date": bar_date,
                           "exit_price": cl})
            return events, pos

    return events, pos


# ─────────────────────────────────────────────────────────────────────────────
# Mark-to-market
# ─────────────────────────────────────────────────────────────────────────────

def mark_to_market(pos: dict, raw: pd.DataFrame) -> float:
    """Return shares * latest close (or last available close ≤ scan date)."""
    try:
        c = raw["Close"][pos["ticker"]].dropna()
        return float(pos["shares"]) * float(c.iloc[-1])
    except (KeyError, IndexError):
        return float(pos["shares"]) * float(pos["entry"])


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(run_date_iso: str) -> None:
    print(f"\n=== paper_trade run: {run_date_iso} ===", flush=True)

    result = scan(force_refresh=False, verbose=True)
    if result is None:
        print("ERROR: scan() returned no data — aborting paper trade.", flush=True)
        sys.exit(1)

    raw         = result["raw"]
    scan_date   = result["scan_date"]
    gate_open   = result["gate_open"]
    picks       = result["picks"]
    print(f"scan_date={scan_date}  gate_open={gate_open}  picks={len(picks)}", flush=True)

    state = load_state(run_date_iso)

    # ── 1. Process open positions through new bars ────────────────────────────
    portfolio_value_before = 0.0
    for slot in state["slots"]:
        pos = slot["position"]
        if pos is None:
            portfolio_value_before += slot["cash"]
            continue

        events, pos = advance_position(pos, raw)
        slot["position"] = pos

        for ev in events:
            if ev["kind"] == "BE_MOVE":
                append_row(TRADES_CSV, TRADE_COLS, {
                    "run_date":         run_date_iso,
                    "scan_date":        scan_date,
                    "action":           "BE_MOVE",
                    "slot":             slot["idx"],
                    "ticker":           pos["ticker"],
                    "setup":            pos["setup"],
                    "quality":          pos["quality"],
                    "price":            ev["new_sl"],
                    "shares":           pos["shares"],
                    "sl":               ev["new_sl"],
                    "tp":               pos["tp"],
                    "be_trigger":       pos["be_trigger"],
                    "pnl_pct":          "",
                    "pnl_usd":          "",
                    "slot_cash_after":  round(slot["cash"], 2),
                    "portfolio_value":  "",
                    "days_held":        pos["days_held"],
                    "note":             f"SL raised to entry ({ev['old_sl']} → {ev['new_sl']})",
                })
                print(f"  BE_MOVE S{slot['idx']} {pos['ticker']}: "
                      f"SL {ev['old_sl']} → {ev['new_sl']}", flush=True)
            elif ev["kind"].startswith("SELL"):
                exit_price = float(ev["exit_price"])
                proceeds   = pos["shares"] * exit_price
                pnl_usd    = proceeds - (pos["shares"] * pos["entry"])
                pnl_pct    = (exit_price / pos["entry"] - 1) * 100
                slot["cash"]     = proceeds
                slot["position"] = None

                append_row(TRADES_CSV, TRADE_COLS, {
                    "run_date":         run_date_iso,
                    "scan_date":        scan_date,
                    "action":           ev["kind"],
                    "slot":             slot["idx"],
                    "ticker":           pos["ticker"],
                    "setup":            pos["setup"],
                    "quality":          pos["quality"],
                    "price":            round(exit_price, 4),
                    "shares":           round(pos["shares"], 6),
                    "sl":               pos["sl"],
                    "tp":               pos["tp"],
                    "be_trigger":       pos["be_trigger"],
                    "pnl_pct":          round(pnl_pct, 2),
                    "pnl_usd":          round(pnl_usd, 2),
                    "slot_cash_after":  round(slot["cash"], 2),
                    "portfolio_value":  "",
                    "days_held":        pos["days_held"],
                    "note":             f"exit on {ev['bar_date']}",
                })
                print(f"  {ev['kind']} S{slot['idx']} {pos['ticker']} @ {exit_price:.2f} "
                      f"({pnl_pct:+.2f}%, ${pnl_usd:+.2f}) — slot cash now ${slot['cash']:,.2f}",
                      flush=True)

        if slot["position"] is not None:
            portfolio_value_before += mark_to_market(slot["position"], raw)
        else:
            portfolio_value_before += slot["cash"]

    # ── 2. Try to enter new positions (free slots) ────────────────────────────
    held_tickers = {s["position"]["ticker"] for s in state["slots"]
                    if s["position"] is not None}
    free_slots   = [s for s in state["slots"] if s["position"] is None]

    if gate_open and picks and free_slots:
        used_tickers = set(held_tickers)
        for slot in free_slots:
            pick = next((p for p in picks
                         if p["ticker"] not in used_tickers
                         and p["quality"] >= MIN_QUALITY_SCORE), None)
            if pick is None:
                break
            entry_price = float(pick["entry"])
            if slot["cash"] <= 0 or entry_price <= 0:
                continue
            shares = slot["cash"] / entry_price
            be_trigger = entry_price + (float(pick["tp"]) - entry_price) * 0.5

            slot["position"] = {
                "ticker":               pick["ticker"],
                "direction":            pick["direction"],
                "setup":                pick["setup"],
                "quality":              pick["quality"],
                "entry_date":           run_date_iso,
                "scan_date":            scan_date,
                "entry":                round(entry_price, 4),
                "shares":               round(shares, 6),
                "sl":                   float(pick["sl"]),
                "original_sl":          float(pick["sl"]),
                "tp":                   float(pick["tp"]),
                "be_trigger":           round(be_trigger, 4),
                "be_moved":             False,
                "days_held":            0,
                "last_processed_date":  scan_date,
            }
            cash_used = slot["cash"]
            slot["cash"] = 0.0
            used_tickers.add(pick["ticker"])

            append_row(TRADES_CSV, TRADE_COLS, {
                "run_date":         run_date_iso,
                "scan_date":        scan_date,
                "action":           "BUY",
                "slot":             slot["idx"],
                "ticker":           pick["ticker"],
                "setup":            pick["setup"],
                "quality":          pick["quality"],
                "price":            round(entry_price, 4),
                "shares":           round(shares, 6),
                "sl":               pick["sl"],
                "tp":               pick["tp"],
                "be_trigger":       round(be_trigger, 4),
                "pnl_pct":          "",
                "pnl_usd":          "",
                "slot_cash_after":  0.0,
                "portfolio_value":  "",
                "days_held":        0,
                "note":             f"deployed ${cash_used:,.2f}; RSI {pick['rsi']}, "
                                    f"vol {pick['vol_ratio']}x, ATR {pick['atr_pct']}%",
            })
            print(f"  BUY    S{slot['idx']} {pick['ticker']:6s} {pick['setup']:<18s} "
                  f"Q={pick['quality']:>4}  @${entry_price:.2f}  "
                  f"shares={shares:.4f}  SL={pick['sl']}  TP={pick['tp']}  "
                  f"deployed=${cash_used:,.2f}", flush=True)
    elif gate_open and picks:
        print(f"  HOLD — all {state['max_slots']} slot(s) occupied; "
              f"{len(picks)} pick(s) available but no free slot.", flush=True)
    elif not gate_open:
        print("  HOLD — regime gate CLOSED; no new entries.", flush=True)
    else:
        print("  HOLD — no triggered picks today.", flush=True)

    # ── 3. Mark-to-market + portfolio snapshot ────────────────────────────────
    cash_total       = sum(s["cash"] for s in state["slots"])
    positions_value  = sum(mark_to_market(s["position"], raw)
                           for s in state["slots"] if s["position"] is not None)
    total_value      = cash_total + positions_value
    return_pct       = (total_value / state["starting_capital"] - 1) * 100
    open_count       = sum(1 for s in state["slots"] if s["position"] is not None)
    open_tickers     = ",".join(s["position"]["ticker"] for s in state["slots"]
                                 if s["position"] is not None)
    top_pick         = picks[0]["ticker"] if picks else ""

    append_row(PORTFOLIO_CSV, PORTFOLIO_COLS, {
        "run_date":           run_date_iso,
        "scan_date":          scan_date,
        "cash":               round(cash_total, 2),
        "positions_value":    round(positions_value, 2),
        "total_value":        round(total_value, 2),
        "return_pct":         round(return_pct, 4),
        "open_count":         open_count,
        "gate_open":          gate_open,
        "spy_price":          result["spy_price"],
        "spy_ma":             result["spy_ma"],
        "vix":                result["vix"],
        "scan_picks_count":   len(picks),
        "top_pick":           top_pick,
        "open_tickers":       open_tickers,
    })

    print(f"\n  Portfolio: cash ${cash_total:,.2f}  +  positions ${positions_value:,.2f}  "
          f"=  total ${total_value:,.2f}  ({return_pct:+.2f}% since "
          f"{state['starting_date']})", flush=True)
    if open_count:
        for s in state["slots"]:
            if s["position"]:
                p = s["position"]
                mtm = mark_to_market(p, raw)
                upnl_pct = (mtm / (p["shares"] * p["entry"]) - 1) * 100
                print(f"    S{s['idx']}: {p['ticker']:6s} {p['setup']:<18s}  "
                      f"entry ${p['entry']:.2f}  mark ${mtm/p['shares']:.2f}  "
                      f"shares {p['shares']:.4f}  uPnL {upnl_pct:+.2f}%  "
                      f"days {p['days_held']}/{TIME_STOP_DAYS}  "
                      f"SL {p['sl']}  TP {p['tp']}  BE {'✓' if p['be_moved'] else '·'}",
                      flush=True)

    state["last_run_date"] = run_date_iso
    save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="Run date (YYYY-MM-DD). Defaults to today (UTC).")
    args = parser.parse_args()
    run_date_iso = args.date or date.today().isoformat()
    run(run_date_iso)
