# Technical Analysis Scanner

Run the full technical analysis scanner across the top 100 S&P 500 stocks by market cap.

## Step 1 — Run scanner

```bash
cd /home/talekien1710/personal_project/ai-stock-investment && .venv/bin/python3 scripts/sma200_filter.py
```

Force-refresh universe (if it's been >30 days or you want latest market cap ranking):

```bash
cd /home/talekien1710/personal_project/ai-stock-investment && .venv/bin/python3 scripts/sma200_filter.py --refresh
```

First-time setup (if .venv missing):

```bash
cd /home/talekien1710/personal_project/ai-stock-investment && uv venv .venv -q && uv pip install yfinance pandas lxml requests --python .venv/bin/python3 -q
```

## Step 2 — Interpret output

The scanner outputs three sections. Interpret each:

### Market breadth
- **>70%**: Strong bull — favour longs broadly, full size
- **50–70%**: Mixed market — be selective, reduce size 10–20%
- **30–50%**: Weakening — high caution, smaller positions, tighter stops
- **<30%**: Bear regime — majority below SMA200, avoid new longs, consider hedges or cash

### Triggered setups table

Each setup includes: Ticker | Setup type | Direction | Entry | SL | TP | R:R | RSI | Vol ratio | ATR% | Timeframe

**Setup types and their logic:**

| Setup | Bias | Key conditions | SL | TP | Timeframe |
|---|---|---|---|---|---|
| Ride Uptrend | Long | SMA50>SMA200, price ≥3/5d above BB mid, RSI>50, pullback to SMA50, green vol>MA | 1× ATR | 1.5× ATR | 1D → 3–6 wk options |
| MACD Cross | Long | MACD hist just crossed zero↑, RSI 50–70, price above SMA50 not touching upper BB, green vol>MA | 1.5× ATR | 3× ATR | 1D → 3–6 wk options |
| VWAP Support Long | Long | SMA50>SMA200, price 0–1.5% above VWAP (dipping toward it), RSI>45, green vol>MA | 1× ATR | 1.5× ATR | 1H/4H → 2–3 wk options |
| Reversal Pre-Golden Cross | Long | SMA50 approaching SMA200 (<2% gap), price already>SMA200, RSI>45, strong vol>1.2× | 1× ATR | 2× ATR or SMA50 | 1D → 3–6 wk options |

**Volume confirmation rule (always applies):**
- Only long when: green candle AND volume > vol MA20
- Only short when: red candle AND volume > vol MA20
- If volume < MA20 on the trigger bar → skip, wait for confirmation

### Setup detail section
For each triggered setup, show the full indicator dashboard and cross-check against the playbook:

1. **Trend direction**: Is SMA50 > SMA200? (golden cross = confirmed uptrend)
2. **BB position**: Price vs upper/mid/lower BB — not extended above upper BB?
3. **RSI**: In the healthy range for the setup (50–70 for longs)?
4. **MACD**: Histogram direction and zero-line position?
5. **Volume**: Green/red bar AND above vol MA20?
6. **VWAP**: Price relative to rolling VWAP — consistent side?

## Step 3 — Present findings

Summarise clearly:
1. Market regime (breadth %)
2. Best 3–5 setups ranked by setup quality (confluence of signals)
3. For each top setup: exact entry, SL, TP, which option expiry to target, and what to watch for invalidation
4. Any setups to skip despite triggering (e.g. MACD negative histogram, volume weak, too extended)

## Playbook reference

### Indicators computed per ticker
- SMA 50, SMA 200 (trend direction)
- Bollinger Bands (20, 2σ): upper / mid / lower
- RSI (14, EWM)
- ATR (14, EWM) — used for SL/TP sizing
- MACD (12, 26, 9): line, signal, histogram
- Volume + 20-day volume MA + vol ratio
- Rolling VWAP (20-day proxy — for anchored VWAP, anchor to a major turning point manually)

### Bear market note
When price < SMA50 AND price < SMA200 for a stock: **skip it entirely**.
When market breadth drops below 30% (majority of top 100 below SMA200):
- Do NOT "all in on cross" — crosses fail frequently in bear regimes
- Instead: wait for golden cross (SMA50 > SMA200) CONFIRMED by price pulling back to SMA50 (now support) + RSI recovering above 50 + green volume > MA
- Scale in: 1/3 position on golden cross signal, add 1/3 on first confirmed pullback to SMA50, final 1/3 when RSI sustains above 50
- Choppy VWAP (price crossing VWAP repeatedly) = no trade
