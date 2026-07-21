"""Daily crypto digest — Binance data -> Telegram."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from analysis import build_btc_plan, build_trend_setup, pick_top_setups
from binance_client import get_klines, get_klines_batch, get_usdt_pairs
from report import build_daily_report
from telegram_bot import send_telegram_message

TOP_N_SCAN = 200
TOP_N_REPORT = 5


def derive_market_bias(btc_plan) -> str:
    if "偏多" in btc_plan.bias:
        return "今日偏向做多 (LONG)，弱勢幣可觀望或小倉做空"
    if "偏空" in btc_plan.bias:
        return "今日偏向做空 (SHORT) 或減倉，強勢幣只做短線"
    return "今日偏向觀望 / 區間交易，唔好重倉追單"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(Path(__file__).resolve().parent / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"

    if not dry_run and (not token or not chat_id):
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")
        print("Copy .env.example to .env and fill in your values.")
        return 1

    print("Fetching Binance market data...")
    pairs = get_usdt_pairs(min_quote_volume=5_000_000)[:TOP_N_SCAN]
    symbols = [item["symbol"] for item in pairs]
    symbol_to_pair = {item["symbol"]: item["pair"] for item in pairs}

    btc_daily = get_klines("BTCUSDT", "1d", 120)
    btc_4h = get_klines("BTCUSDT", "4h", 120)
    btc_plan = build_btc_plan(btc_daily, btc_4h)

    from analysis import pct_change_over, candles_to_frame

    btc_perf_7d = pct_change_over(candles_to_frame(btc_daily)["close"], 7)

    print(f"Scanning {len(symbols)} pairs (1D + 4H)...")
    daily_map = get_klines_batch(symbols, "1d", 120)
    h4_map = get_klines_batch(symbols, "4h", 120)

    long_setups = []
    short_setups = []

    for symbol in symbols:
        if symbol == "BTCUSDT":
            continue
        pair = symbol_to_pair[symbol]

        long_setup = build_trend_setup(
            symbol, pair, daily_map[symbol], h4_map[symbol], btc_perf_7d, "LONG"
        )
        if long_setup:
            long_setups.append(long_setup)

        short_setup = build_trend_setup(
            symbol, pair, daily_map[symbol], h4_map[symbol], btc_perf_7d, "SHORT"
        )
        if short_setup:
            short_setups.append(short_setup)

    top_long = pick_top_setups(long_setups, "LONG", TOP_N_REPORT)
    top_short = pick_top_setups(short_setups, "SHORT", TOP_N_REPORT)
    market_bias = derive_market_bias(btc_plan)

    report = build_daily_report(market_bias, btc_plan, top_long, top_short)

    if dry_run:
        print(report)
        return 0

    print("Sending Telegram message...")
    try:
        send_telegram_message(token, chat_id, report)
    except RuntimeError as error:
        print(str(error))
        print("Hint: confirm TELEGRAM_CHAT_ID is correct and you pressed Start on the bot.")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
