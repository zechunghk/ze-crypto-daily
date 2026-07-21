"""Send messages to Telegram."""

from __future__ import annotations

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LENGTH = 4000


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    chunks = _split_message(text)
    for chunk in chunks:
        response = requests.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")


def _split_message(text: str) -> list[str]:
    if len(text) <= MAX_LENGTH:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > MAX_LENGTH:
            parts.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        parts.append(current.rstrip())
    return parts
