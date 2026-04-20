#!/usr/bin/env python3
"""
Score each stock against the trading playbook strategies.

Rule: price > SMA200 → LONG setups only. price < SMA200 → SHORT setups only.
Never bet against the dominant SMA200 direction.
"""


def score(ind: dict) -> list[dict]:
    """
    Evaluate all strategy setups for a single ticker's indicator dict.
    Returns a list of triggered setup dicts (may be empty).
    """
    setups = []
    p = ind["price"]
    atr = ind["atr"]
    above = ind["price_above_sma200"]

    if above:
        setups += _long_setups(ind, p, atr)
    # Short side disabled — long-only strategy.
    return setups


# ─────────────────────────────────────────────────────────────────────────────
# LONG SETUPS  (price > SMA200)
# ─────────────────────────────────────────────────────────────────────────────

def _long_setups(ind: dict, p: float, atr: float) -> list[dict]:
    setups = []

    # ── L1: Ride the Uptrend ─────────────────────────────────────────────────
    # Consistent above BB mid → pullback to SMA50 → green vol spike
    if (
        ind["golden_cross"]               # SMA50 > SMA200
        and ind["above_mid_5d"] >= 3      # price mostly above BB mid
        and ind["rsi"] > 50
        and ind["near_sma50_recently"]    # recent pullback to SMA50
        and ind["is_green"]
        and ind["vol_ratio"] > 1.0
    ):
        entry = p
        sl = round(p - 2.0 * atr, 2)
        tp = round(p + 4.0 * atr, 2)
        setups.append(_setup("Ride Uptrend", "LONG", entry, sl, tp, "1:2",
                             "1D → 3-6 wk options",
                             f"RSI {ind['rsi']:.0f}, vol {ind['vol_ratio']:.1f}x MA"))

    # ── L2: MACD Bullish Cross ───────────────────────────────────────────────
    # Hist just crossed above zero, RSI 50-70, not at upper BB
    if (
        ind["macd_crossed_up"]
        and 50 <= ind["rsi"] <= 70
        and p > ind["sma50"]
        and p < ind["bb_upper"] * 0.99    # not touching upper BB
        and ind["is_green"]
        and ind["vol_ratio"] > 1.0
    ):
        entry = p
        sl = round(p - 2.5 * atr, 2)
        tp = round(p + 5.0 * atr, 2)
        setups.append(_setup("MACD Cross", "LONG", entry, sl, tp, "1:2",
                             "1D → 3-6 wk options",
                             f"MACD hist {ind['macd_hist']:+.3f}, RSI {ind['rsi']:.0f}"))

    # ── L3: VWAP Support Long ────────────────────────────────────────────────
    # Price just above VWAP (dipping toward it as support)
    vd = ind["price_vs_vwap_pct"]
    if (
        ind["golden_cross"]
        and 0 < vd < 1.5
        and ind["rsi"] > 45
        and ind["above_mid_5d"] > 0
        and ind["is_green"]
        and ind["vol_ratio"] > 1.0
    ):
        entry = p
        sl = round(p - 2.0 * atr, 2)
        tp = round(p + 3.0 * atr, 2)
        setups.append(_setup("VWAP Support", "LONG", entry, sl, tp, "1:1.5",
                             "1H/4H → 2-3 wk options",
                             f"Price {vd:+.1f}% vs VWAP, RSI {ind['rsi']:.0f}"))

    # ── L4: Pre-Golden Cross Reversal ────────────────────────────────────────
    # SMA50 approaching SMA200 from below, price already above SMA200
    if (
        not ind["golden_cross"]
        and ind["cross_recent"]           # SMA50/200 gap < 2%
        and ind["rsi"] > 45
        and ind["is_green"]
        and ind["vol_ratio"] > 1.2
    ):
        entry = p
        sl = round(p - 2.0 * atr, 2)
        tp = round(p + 4.0 * atr, 2)
        setups.append(_setup("Pre-Golden Cross", "LONG", entry, sl, tp, "1:2",
                             "1D → 3-6 wk options",
                             f"SMA50/200 gap <2%, vol {ind['vol_ratio']:.1f}x"))

    return setups


# ─────────────────────────────────────────────────────────────────────────────
# SHORT SETUPS  (price < SMA200)
# ─────────────────────────────────────────────────────────────────────────────

def _short_setups(ind: dict, p: float, atr: float) -> list[dict]:
    setups = []

    # ── S1: Ride the Downtrend ───────────────────────────────────────────────
    # Consistent below BB mid → bounce to SMA50 (resistance) → red vol spike
    if (
        not ind["golden_cross"]           # SMA50 < SMA200 (death cross)
        and ind["below_mid_5d"] >= 3      # price mostly below BB mid
        and ind["rsi"] < 50
        and ind["near_sma50_recently"]    # bounce up to SMA50 (resistance)
        and not ind["is_green"]           # red candle
        and ind["vol_ratio"] > 1.0
    ):
        entry = p
        sl = round(p + 1.0 * atr, 2)
        tp = round(p - 1.5 * atr, 2)
        setups.append(_setup("Ride Downtrend", "SHORT", entry, sl, tp, "1:1.5",
                             "1D → 3-6 wk options",
                             f"RSI {ind['rsi']:.0f}, vol {ind['vol_ratio']:.1f}x MA"))

    # ── S2: MACD Bearish Cross ───────────────────────────────────────────────
    # Hist just crossed below zero, RSI 30-50, not at lower BB
    if (
        ind["macd_crossed_down"]
        and 30 <= ind["rsi"] <= 50
        and p < ind["sma50"]
        and p > ind["bb_lower"] * 1.01    # not touching lower BB
        and not ind["is_green"]
        and ind["vol_ratio"] > 1.0
    ):
        entry = p
        sl = round(p + 1.5 * atr, 2)
        tp = round(p - 3.0 * atr, 2)
        setups.append(_setup("MACD Cross", "SHORT", entry, sl, tp, "1:2",
                             "1D → 3-6 wk options",
                             f"MACD hist {ind['macd_hist']:+.3f}, RSI {ind['rsi']:.0f}"))

    # ── S3: VWAP Resistance Short ────────────────────────────────────────────
    # Price just below VWAP (bouncing up toward it as resistance)
    vd = ind["price_vs_vwap_pct"]
    if (
        not ind["golden_cross"]
        and -1.5 < vd < 0
        and ind["rsi"] < 55
        and ind["below_mid_5d"] > 0
        and not ind["is_green"]
        and ind["vol_ratio"] > 1.0
    ):
        entry = p
        sl = round(p + 1.0 * atr, 2)
        tp = round(p - 1.5 * atr, 2)
        setups.append(_setup("VWAP Resistance", "SHORT", entry, sl, tp, "1:1.5",
                             "1H/4H → 2-3 wk options",
                             f"Price {vd:+.1f}% vs VWAP, RSI {ind['rsi']:.0f}"))

    # ── S4: Pre-Death Cross Reversal ─────────────────────────────────────────
    # SMA50 approaching SMA200 from above, price already below SMA200
    if (
        ind["golden_cross"]               # SMA50 still above SMA200 ...
        and ind["cross_recent"]           # ... but gap < 2% (death cross imminent)
        and ind["rsi"] < 55
        and not ind["is_green"]
        and ind["vol_ratio"] > 1.2
    ):
        entry = p
        sl = round(p + 1.0 * atr, 2)
        tp = round(p - 2.0 * atr, 2)   # target 2 ATR below entry
        setups.append(_setup("Pre-Death Cross", "SHORT", entry, sl, tp, "1:2",
                             "1D → 3-6 wk options",
                             f"SMA50/200 gap <2%, vol {ind['vol_ratio']:.1f}x"))

    return setups


# ─────────────────────────────────────────────────────────────────────────────

def _setup(name, direction, entry, sl, tp, rr, timeframe, notes) -> dict:
    return {
        "setup": name,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "timeframe": timeframe,
        "notes": notes,
    }


def market_regime(long_count: int, total: int) -> str:
    rate = long_count / total
    short_count = total - long_count
    base = f"{long_count}L / {short_count}S of {total}"
    if rate >= 0.70:
        return f"STRONG BULL ({base}) — broad uptrend, favour longs"
    elif rate >= 0.50:
        return f"MIXED ({base}) — selective longs, tighter stops"
    elif rate >= 0.30:
        return f"WEAKENING ({base}) — more shorts than longs, reduce long size"
    else:
        return f"BEAR ({base}) — majority below SMA200, favour shorts"
