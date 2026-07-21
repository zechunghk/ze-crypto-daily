"""Resolve user input like 'SOL' to Binance symbols e.g. SOLUSDT."""

from __future__ import annotations

from binance_client import _get

_cached_usdt_symbols: set[str] | None = None


def get_trading_usdt_symbols() -> set[str]:
    global _cached_usdt_symbols
    if _cached_usdt_symbols is not None:
        return _cached_usdt_symbols

    exchange_info = _get("/api/v3/exchangeInfo")
    _cached_usdt_symbols = {
        item["symbol"]
        for item in exchange_info["symbols"]
        if item.get("status") == "TRADING" and item["symbol"].endswith("USDT")
    }
    return _cached_usdt_symbols


def resolve_user_symbol(user_input: str) -> str | None:
    cleaned = user_input.strip().upper().replace("/", "").replace("-", "")
    if not cleaned or not cleaned.isalnum():
        return None

    symbols = get_trading_usdt_symbols()
    candidates = [cleaned] if cleaned.endswith("USDT") else [f"{cleaned}USDT"]

    for candidate in candidates:
        if candidate in symbols:
            return candidate
    return None
