# ai-stock-investment

Long-only, rules-based swing-trading strategy for the top 100 S&P 500 stocks by market cap. Designed to be run through Claude Code — the `/technical-analysis` slash command executes the scanner and interprets the output.

Backtested 2024-04-20 → 2026-04-20 (2 years):

```
$10,000 → $19,571   (+95.7%)
SPY B&H: +45.6%
Alpha:   +50.1 pp    [BEAT ✓]
33 trades, 16W / 17L, 48% win rate
```

---

## The full rulebook

### 1. Universe

- Top 100 S&P 500 companies by market cap, re-queried from Wikipedia + yfinance, cached locally in `data/universe_top100.csv`.
- Refreshed automatically if the cache is older than 7 days, or on demand with `--refresh`.

### 2. Direction rule (the one that makes everything work)

- **price > SMA200 → consider LONG setups only.**
- **price < SMA200 → no trade.** Never short, never fight the dominant trend.
- Do not "all in on the cross" in a bear regime. Market breadth <30% = prefer cash over forced longs.

### 3. The four long setups

All setups require a **green candle** AND **volume > 20-day volume MA** on the trigger bar. If volume is weak, skip and wait.

| #   | Setup            | Key conditions                                                                                 | SL           | TP         |
| --- | ---------------- | ---------------------------------------------------------------------------------------------- | ------------ | ---------- |
| L1  | Ride Uptrend     | SMA50 > SMA200, price ≥3/5d above BB mid, RSI > 50, recent pullback to SMA50                   | **2× ATR**   | **4× ATR** |
| L2  | MACD Cross       | MACD histogram just crossed zero up, RSI 50–70, price > SMA50 but not touching upper BB        | **2.5× ATR** | **5× ATR** |
| L3  | VWAP Support     | SMA50 > SMA200, price 0–1.5% above VWAP (dipping toward it as support), RSI > 45               | **2× ATR**   | **3× ATR** |
| L4  | Pre-Golden Cross | SMA50 approaching SMA200 from below (<2% gap), price already > SMA200, RSI > 45, vol > 1.2× MA | **2× ATR**   | **4× ATR** |

### 4. Volatility guardrail

**Skip any entry where ATR% > 4.0.** Extreme-vol names (high-beta tech on news days, etc.) blow through wide stops before the setup works. Enforced by both the live scanner and the backtest.

### 5. Trade management — this is where most of the alpha comes from

1. **Initial stop** at the SL shown in the table (2× or 2.5× ATR below entry).
2. **Trailing-to-breakeven**: once price travels **50% of the way from entry to TP**, raise SL to entry. One-way ratchet — SL only moves up, never down, and only once.
    - Worked example: entry $130.52, TP $144.48. Midpoint = $137.50. When price tags $137.50, SL moves from $123.54 → $130.52. From then on, worst case is $0, best case is still +10.7%.
    - This rule alone converts several would-be losers into $0 scratches (AVGO, NEE ×2 in the 2-year backtest).
3. **Time stop**: exit at the close after **40 trading days** if neither TP nor SL has been hit — free up capital for fresh setups.
4. **One trade at a time.** Only scan for a new setup after the current trade exits. If the best setup on a given day scores below quality threshold (25), sit on hands.

### 6. Setup quality score (used by the backtest to pick the cleanest entry when multiple trigger)

Up to 100 points:

- Volume conviction: up to 35 pts (higher vol ratio = more pts)
- RSI sweet spot: up to 30 pts (55–65 for longs = 30 pts, 50–70 = 15 pts)
- ATR range: up to 20 pts (1.5–3.5% = 20 pts, 1–5% = 10 pts, else 0)
- Trend alignment: +10 pts if golden cross confirmed
- MACD histogram aligned with direction: +5 pts

### 7. Indicators computed per ticker

- SMA 50, SMA 200 (trend direction)
- Bollinger Bands (20, 2σ): upper / mid / lower
- RSI (14, EWM)
- ATR (14, EWM) — drives SL/TP sizing
- MACD (12, 26, 9): line, signal, histogram
- Volume + 20-day volume MA + vol ratio
- Rolling VWAP (20-day proxy)

---

## How to use it

### Run the scanner (live)

```bash
.venv/bin/python3 scripts/sma200_filter.py
```

Or in Claude Code: `/technical-analysis`.

Output: market-breadth regime, LONG universe table, and any triggered setups with exact Entry / SL / TP / breakeven-trigger prices.

### Run the backtest

```bash
.venv/bin/python3 scripts/backtest.py
```

Configurable at the top of `scripts/backtest.py`:

```python
START_DATE        = date(2024, 4, 20)   # 2-year window by default
END_DATE          = date(2026, 4, 20)
CAPITAL_INIT      = 10_000.0
TIME_STOP_DAYS    = 40
MIN_QUALITY_SCORE = 25
MAX_ATR_PCT       = 4.0
BENCHMARK         = "SPY"
```

### First-time setup

```bash
uv venv .venv -q
uv pip install yfinance pandas lxml requests --python .venv/bin/python3 -q
```

---

## Code layout

```
scripts/
  universe.py       # Fetches + caches top 100 S&P 500 by market cap
  indicators.py     # Computes SMA/BB/RSI/ATR/MACD/VWAP per ticker
  signals.py        # Four long setups L1-L4 (short side disabled)
  sma200_filter.py  # Live scanner — prints breadth + triggered setups
  backtest.py       # 1-trade-at-a-time engine with trailing-BE stop + SPY benchmark
.claude/
  commands/
    technical-analysis.md   # Slash command for Claude Code
  settings.json             # Project default model: claude-opus-4-7
```

---

## Why these specific rules — a short history

The first version of this strategy had **both long and short setups**, **1×/1.5× ATR stops**, and a **20-day time stop**. Over the 1-year window 2025-04-20 → 2026-04-20 it lost **-25.6% against SPY +39.8%** (alpha -65.4 pp). Four iterations fixed it:

| Change                                | Rationale                                                                       |
| ------------------------------------- | ------------------------------------------------------------------------------- |
| Disable SHORT setups                  | Shorts kept getting clipped by the dominant bull tape before any move developed |
| SL 2× ATR, TP 4× ATR (from 1× / 1.5×) | Tight stops get eaten by daily noise before the setup resolves                  |
| Time stop 20 → 40 days                | Winners like AVGO (+17.8% in 40 days) need room to run                          |
| Trailing-to-breakeven at 50% of TP    | Protects profits without capping upside — the single biggest alpha source       |

Result: flipped from losing -65 pp to beating +50 pp over 2 years.

Things that were tried and reverted (didn't help):

- Earlier BE trigger (40% of TP): scratches too many trades before they reach target
- Later BE trigger (60% of TP): more trades revert to full SL before lock
- Stricter volume requirement (>1.3× MA): cuts winners faster than losers

---

## MCP Servers (optional tooling)

### Prerequisites

- [uv](https://github.com/astral-sh/uv) — for `uvx` (Python MCP servers)
- [Node.js](https://nodejs.org) — for `npx` (Node MCP servers)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### yfinance-mcp

Fetches stock market data — prices, financials, historical OHLCV.

```bash
uvx yfinance-mcp
claude mcp add yfinance-mcp uvx yfinance-mcp
```

### playwright

Browser automation — scraping news, financial sites, charts.

```bash
npx -y @playwright/mcp --browser chromium
claude mcp add playwright -- npx -y @playwright/mcp --browser chromium
```

### Verify MCP servers are active

```bash
claude mcp list
```

---

## Disclaimer

This is backtested on historical data across a 2-year window. Past performance does not guarantee future results. The strategy was tuned against its own evaluation window, so the +50pp alpha number has some degree of in-sample fit — treat it as a reasoned starting point, not a promise.

## Claude session

claude --resume 42f69484-bc88-40bb-b701-fd0e2a251dd0 --dangerously-skip-permissions
