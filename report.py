"""Format daily digest for Telegram."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from analysis import BtcPlan, TradeSetup

HKT = timezone(timedelta(hours=8))


def _fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.6f}"


def _format_setup(index: int, setup: TradeSetup) -> str:
    reason_text = "；".join(setup.reasons)
    return (
        f"{index}. {setup.pair}（強度 {setup.score}/100）\n"
        f"   現價: {_fmt_price(setup.last_price)}\n"
        f"   入場: {_fmt_price(setup.entry)} | SL: {_fmt_price(setup.stop_loss)}\n"
        f"   TP1: {_fmt_price(setup.take_profit_1)} | TP2: {_fmt_price(setup.take_profit_2)}\n"
        f"   理由: {reason_text}"
    )


def build_daily_report(
    market_bias: str,
    btc_plan: BtcPlan,
    long_setups: list[TradeSetup],
    short_setups: list[TradeSetup],
) -> str:
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")

    lines = [
        f"Crypto 每日交易日報",
        f"時間: {now}",
        f"數據來源: Binance 實時 API",
        "",
        "--------------------------------",
        "[1] 今日市場方向",
        "--------------------------------",
        f"建議偏向: {market_bias}",
        f"BTC 判斷: {btc_plan.bias}（信心: {btc_plan.confidence}）",
        f"依據: {'；'.join(btc_plan.reasons)}",
        "",
        "--------------------------------",
        "[2] 強勢做多候選（最多 5）",
        "--------------------------------",
    ]

    if long_setups:
        for idx, setup in enumerate(long_setups, start=1):
            lines.append(_format_setup(idx, setup))
            lines.append("")
    else:
        lines.append("今日未見符合條件嘅強勢做多標的，建議觀望。")
        lines.append("")

    lines.extend(
        [
            "--------------------------------",
            "[2b] 弱勢做空候選（最多 5）",
            "--------------------------------",
        ]
    )

    if short_setups:
        for idx, setup in enumerate(short_setups, start=1):
            lines.append(_format_setup(idx, setup))
            lines.append("")
    else:
        lines.append("今日未見符合條件嘅弱勢做空標的。")
        lines.append("")

    lines.extend(
        [
            "--------------------------------",
            "[3] BTC 今日預期區間 & 交易計劃",
            "--------------------------------",
            f"現價: {_fmt_price(btc_plan.last_price)}",
            f"預期區間: {_fmt_price(btc_plan.range_low)} – {_fmt_price(btc_plan.range_high)}",
            f"Support: {_fmt_price(btc_plan.support_1)} / {_fmt_price(btc_plan.support_2)}",
            f"Resistance: {_fmt_price(btc_plan.resistance_1)} / {_fmt_price(btc_plan.resistance_2)}",
            "",
            "交易計劃:",
        ]
    )
    for bullet in btc_plan.plan_lines:
        lines.append(f"• {bullet}")

    lines.extend(
        [
            "",
            "風險提示: 以上為量化篩選結果，唔係投資建議。",
            "入場前請用 TradingView 4H/1D 圖再確認。",
        ]
    )

    return "\n".join(lines)
