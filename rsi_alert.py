"""Push intraday / interday RSI screens to Telegram.

--mode both   兩份都發（手動預設）
--mode day    只發日內
--mode swing  只發波段
--mode auto   每次發日內；香港時間 08:00 先加發波段（Railway cron 用）
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from rsi_screen import HK, build_day_alert, build_swing_alert
from telegram_bot import send_telegram_message


def _chat_ids() -> list[str]:
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _modes(mode: str) -> list[str]:
    if mode == "both":
        return ["day", "swing"]
    if mode == "auto":
        if datetime.now(HK).hour == 8:
            return ["day", "swing"]
        return ["day"]
    return [mode]


def _build(mode: str, top: int | None) -> str:
    if mode == "day":
        return build_day_alert(top)
    if mode == "swing":
        return build_swing_alert(top)
    raise ValueError(f"未知 mode：{mode}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(Path(__file__).resolve().parent / ".env")

    parser = argparse.ArgumentParser(description="將 RSI 觀察名單發去 Telegram")
    parser.add_argument(
        "--mode",
        choices=["day", "swing", "both", "auto"],
        default=os.getenv("RSI_ALERT_MODE", "both").strip().lower() or "both",
    )
    parser.add_argument("--top", type=int, default=None, help="掃描成交額最高幾多隻，10–40")
    args = parser.parse_args()
    if args.mode not in {"day", "swing", "both", "auto"}:
        print("RSI_ALERT_MODE 只可以係 day、swing、both、auto")
        return 2

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = _chat_ids()
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"

    if not dry_run and (not token or not chat_ids):
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")
        return 1

    failures = 0
    for mode in _modes(args.mode):
        print(f"Scanning RSI {mode}...")
        try:
            text = _build(mode, args.top)
        except Exception as error:
            print(f"{mode} scan failed: {error}")
            failures += 1
            continue

        if dry_run:
            print(text)
            print()
            continue

        for chat_id in chat_ids:
            try:
                send_telegram_message(token, chat_id, text)
            except RuntimeError as error:
                print(str(error))
                failures += 1

    if failures:
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
