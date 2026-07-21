"""Send messages to Telegram."""

from __future__ import annotations

import time

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
# Telegram counts length in UTF-16 code units (max 4096).
MAX_TELEGRAM_LENGTH = 3800


def _telegram_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _normalize_chat_id(chat_id: str) -> str | int:
    cleaned = chat_id.strip()
    if cleaned.lstrip("-").isdigit():
        return int(cleaned)
    return cleaned


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    if not text.strip():
        raise ValueError("Telegram message is empty")

    normalized_chat_id = _normalize_chat_id(chat_id)
    chunks = _split_message(text)

    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(
            TELEGRAM_API.format(token=token.strip()),
            json={
                "chat_id": normalized_chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {"description": response.text}

        if not response.ok or not payload.get("ok"):
            description = payload.get("description", response.text)
            raise RuntimeError(
                f"Telegram send failed (chunk {index}/{len(chunks)}, "
                f"status {response.status_code}): {description}"
            )

        if index < len(chunks):
            time.sleep(0.3)


def _split_message(text: str) -> list[str]:
    if _telegram_len(text) <= MAX_TELEGRAM_LENGTH:
        return [text]

    parts: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if _telegram_len(line) > MAX_TELEGRAM_LENGTH:
            if current.strip():
                parts.append(current.rstrip())
                current = ""
            parts.extend(_split_long_line(line))
            continue

        candidate = current + line
        if _telegram_len(candidate) > MAX_TELEGRAM_LENGTH:
            if current.strip():
                parts.append(current.rstrip())
            current = line
        else:
            current = candidate

    if current.strip():
        parts.append(current.rstrip())

    return parts or [text[:MAX_TELEGRAM_LENGTH]]


def _split_long_line(line: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for char in line:
        candidate = current + char
        if _telegram_len(candidate) > MAX_TELEGRAM_LENGTH:
            if current:
                chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
