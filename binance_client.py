"""Binance public API client — no API key required for market data."""

from __future__ import annotations

import time
from typing import Any

import requests

BASE_URL = "https://api.binance.com"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "crypto-daily-alerts/1.0"})

EXCLUDED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
EXCLUDED_SYMBOLS = {
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "USDPUSDT",
    "DAIUSDT",
    "EURUSDT",
    "AEURUSDT",
}


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    response = SESSION.get(f"{BASE_URL}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_usdt_pairs(min_quote_volume: float = 5_000_000) -> list[dict[str, Any]]:
    tickers = _get("/api/v3/ticker/24hr")
    pairs: list[dict[str, Any]] = []

    for ticker in tickers:
        symbol = ticker["symbol"]
        if not symbol.endswith("USDT"):
            continue
        if symbol in EXCLUDED_SYMBOLS:
            continue
        if any(symbol.replace("USDT", "").endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
            continue

        quote_volume = float(ticker["quoteVolume"])
        if quote_volume < min_quote_volume:
            continue

        pairs.append(
            {
                "symbol": symbol,
                "pair": symbol.replace("USDT", "/USDT"),
                "last_price": float(ticker["lastPrice"]),
                "price_change_pct": float(ticker["priceChangePercent"]),
                "quote_volume": quote_volume,
            }
        )

    pairs.sort(key=lambda item: item["quote_volume"], reverse=True)
    return pairs


def get_klines(symbol: str, interval: str, limit: int = 120) -> list[dict[str, float]]:
    raw = _get(
        "/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    candles: list[dict[str, float]] = []
    for row in raw:
        candles.append(
            {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return candles


def get_klines_batch(
    symbols: list[str],
    interval: str,
    limit: int = 120,
    pause_seconds: float = 0.08,
) -> dict[str, list[dict[str, float]]]:
    result: dict[str, list[dict[str, float]]] = {}
    for symbol in symbols:
        result[symbol] = get_klines(symbol, interval, limit)
        time.sleep(pause_seconds)
    return result
