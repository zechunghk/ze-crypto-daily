"""Handle incoming Telegram text commands and coin queries."""

from __future__ import annotations

import os

from insights import build_coin_insight
from rsi_screen import build_day_alert, build_swing_alert
from symbol_resolver import resolve_user_symbol

_DAY_COMMANDS = {"/rsi", "/day", "/intraday", "日內", "日内"}
_SWING_COMMANDS = {"/swing", "/interday", "波段", "隔日"}
_BOTH_COMMANDS = {"/watchlist", "/rsi_both"}


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


def parse_rsi_mode(text: str) -> str | None:
    """day / swing / both。唔係 RSI 指令就返回 None。"""
    parts = text.strip().split()
    if not parts:
        return None
    head = parts[0].split("@", 1)[0].lower()
    rest = " ".join(parts[1:]).lower()
    if head in _BOTH_COMMANDS:
        return "both"
    if head in _DAY_COMMANDS:
        if rest in {"swing", "波段", "interday", "隔日"}:
            return "swing"
        if rest in {"both", "全部"}:
            return "both"
        return "day"
    if head in _SWING_COMMANDS:
        return "swing"
    return None


def _rsi_reply(mode: str) -> str:
    try:
        if mode == "day":
            return build_day_alert()
        if mode == "swing":
            return build_swing_alert()
        return build_day_alert() + "\n\n" + build_swing_alert()
    except Exception as error:
        print(f"RSI scan failed: {error}")
        return "RSI 名單暫時攞唔到，陣間再試。"


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
            "每日早上亦会自动发送市场日报。\n\n"
            "RSI 觀察名單：\n"
            "- /rsi 或 日內 — 日內篩選\n"
            "- /swing 或 波段 — 幾日波段\n"
            "- /watchlist — 兩份一齊"
        )

    rsi_mode = parse_rsi_mode(cleaned)
    if rsi_mode:
        return _rsi_reply(rsi_mode)

    symbol = resolve_user_symbol(cleaned)
    if not symbol:
        return f"搵唔到 '{cleaned}' 对应嘅 Binance USDT 交易对。请试 SOL、ETH、AVAX 等。"

    return build_coin_insight(symbol)
