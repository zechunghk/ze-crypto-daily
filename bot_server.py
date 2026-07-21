"""Long-polling Telegram bot for on-demand coin insights."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from telegram_bot import send_telegram_message
from telegram_handler import handle_text_message, is_chat_allowed

TELEGRAM_UPDATES_API = "https://api.telegram.org/bot{token}/getUpdates"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(Path(__file__).resolve().parent / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN")
        return 1

    print("Telegram query bot started (long polling)...")
    offset = 0
    session = requests.Session()

    while True:
        try:
            response = session.get(
                TELEGRAM_UPDATES_API.format(token=token),
                params={"timeout": 30, "offset": offset},
                timeout=35,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                print(f"getUpdates error: {payload}")
                time.sleep(3)
                continue

            for update in payload.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                if not is_chat_allowed(chat_id):
                    continue

                text = message.get("text", "").strip()
                if not text:
                    continue

                print(f"Query from {chat_id}: {text}")
                try:
                    reply = handle_text_message(text)
                    send_telegram_message(token, str(chat_id), reply)
                except Exception as error:
                    print(f"Failed to handle message: {error}")
                    send_telegram_message(
                        token,
                        str(chat_id),
                        "分析失败，请稍后再试。",
                    )
        except requests.RequestException as error:
            print(f"Polling error: {error}")
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
