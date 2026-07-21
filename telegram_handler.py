"""Handle incoming Telegram text commands and coin queries."""

from __future__ import annotations

import os

from insights import build_coin_insight
from symbol_resolver import resolve_user_symbol


def _allowed_chat_ids() -> set[str]:
    raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_chat_allowed(chat_id: int | str) -> bool:
    allowed = _allowed_chat_ids()
    if not allowed:
        return True
    return str(chat_id) in allowed


def handle_text_message(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return "请输入币种，例如: SOL 或 ETH"

    lowered = cleaned.lower()
    if lowered in {"/start", "start", "帮助", "help", "/help"}:
        return (
            "ZE Crypto Daily Bot\n\n"
            "直接输入币种代号即可查询，例如:\n"
            "- SOL\n"
            "- AVAX\n"
            "- BTCUSDT\n\n"
            "我会返回技术分析、短期预测、SL/TP 参考同相关新闻。\n"
            "每日早上亦会自动发送市场日报。"
        )

    symbol = resolve_user_symbol(cleaned)
    if not symbol:
        return f"搵唔到 '{cleaned}' 对应嘅 Binance USDT 交易对。请试 SOL、ETH、AVAX 等。"

    return build_coin_insight(symbol)
