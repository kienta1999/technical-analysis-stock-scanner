#!/usr/bin/env python3
"""Fetch and cache the top 100 stocks by market cap from the S&P 500 universe.

Cache lives at data/universe_top100.csv and is refreshed every 30 days.
"""

import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")

import io
import requests
import pandas as pd
import yfinance as yf

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(_ROOT, "data", "universe_top100.csv")
CACHE_MAX_AGE_DAYS = 30
WORKERS = 20


def _cache_is_fresh() -> bool:
    if not os.path.exists(CACHE_FILE):
        return False
    age_days = (time.time() - os.path.getmtime(CACHE_FILE)) / 86400
    return age_days < CACHE_MAX_AGE_DAYS


def _get_sp500_tickers() -> list[str]:
    """Pull current S&P 500 constituent list from Wikipedia."""
    print("Fetching S&P 500 constituent list from Wikipedia...", flush=True)
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; stock-screener/1.0)"}
    html = requests.get(url, headers=headers, timeout=15).text
    df = pd.read_html(io.StringIO(html))[0]
    # Yahoo Finance uses '-' not '.' for class shares (e.g. BRK.B -> BRK-B)
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()


def _fetch_market_cap(ticker: str) -> tuple[str, float | None]:
    try:
        mc = yf.Ticker(ticker).fast_info.market_cap
        return ticker, mc if mc else None
    except Exception:
        return ticker, None


def _build_top100(tickers: list[str]) -> pd.DataFrame:
    print(f"Fetching market caps for {len(tickers)} tickers ({WORKERS} parallel workers)...", flush=True)
    caps: dict[str, float] = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch_market_cap, t): t for t in tickers}
        done = 0
        for future in as_completed(futures):
            ticker, mc = future.result()
            if mc:
                caps[ticker] = mc
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(tickers)} tickers processed...", flush=True)

    df = (
        pd.DataFrame(caps.items(), columns=["Ticker", "MarketCap"])
        .sort_values("MarketCap", ascending=False)
        .head(100)
        .reset_index(drop=True)
    )
    df["MarketCapB"] = (df["MarketCap"] / 1e9).round(1)
    return df


def load_universe(force_refresh: bool = False) -> pd.DataFrame:
    """Return the top 100 S&P 500 stocks by market cap, using cache when fresh."""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

    if not force_refresh and _cache_is_fresh():
        df = pd.read_csv(CACHE_FILE)
        age_days = (time.time() - os.path.getmtime(CACHE_FILE)) / 86400
        print(f"Loaded universe from cache (age: {age_days:.0f} days). Next refresh in {CACHE_MAX_AGE_DAYS - age_days:.0f} days.", flush=True)
        return df

    tickers = _get_sp500_tickers()
    df = _build_top100(tickers)
    df.to_csv(CACHE_FILE, index=False)
    print(f"Universe cached to {CACHE_FILE} ({len(df)} stocks).", flush=True)
    return df


if __name__ == "__main__":
    import sys
    force = "--refresh" in sys.argv
    df = load_universe(force_refresh=force)
    print(f"\nTop 100 S&P 500 by market cap:")
    print(df[["Ticker", "MarketCapB"]].to_string(index=False))
