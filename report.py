"""Format daily digest for Telegram."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from analysis import BtcPlan, TradeSetup
from holdings import HoldingReport

HKT = timezone(timedelta(hours=8))


def _fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.4f}"


def _format_setup(index: int, setup: TradeSetup) -> str:
    reason_text = "；".join(setup.reasons)

    def _pct_from_entry(entry: float, price: float, direction: str) -> float:
        # LONG：price 相对入场的涨跌百分比
        # SHORT：用 (entry - price)/entry，让 TP 为正、SL 为负
        if direction == "LONG":
            return ((price - entry) / entry) * 100.0
        return ((entry - price) / entry) * 100.0

    sl_pct = _pct_from_entry(setup.entry, setup.stop_loss, setup.direction)
    tp1_pct = _pct_from_entry(setup.entry, setup.take_profit_1, setup.direction)
    tp2_pct = _pct_from_entry(setup.entry, setup.take_profit_2, setup.direction)

    return (
        f"{index}. {setup.pair}（強度 {setup.score}/100）\n"
        f"   現價: {_fmt_price(setup.last_price)}\n"
        f"   入場: {_fmt_price(setup.entry)}\n"
        f"   SL: {_fmt_price(setup.stop_loss)} ({sl_pct:+.2f}%)\n"
        f"   TP1: {_fmt_price(setup.take_profit_1)} ({tp1_pct:+.2f}%) | TP2: {_fmt_price(setup.take_profit_2)} ({tp2_pct:+.2f}%)\n"
        f"   理由: {reason_text}"
    )


def _format_holding(report: HoldingReport) -> str:
    news_lines = "\n".join(f"   - {headline}" for headline in report.news)
    position_line = ""
    if report.entry_price is not None and report.quantity is not None:
        pnl_pct = ((report.last_price - report.entry_price) / report.entry_price) * 100.0
        cost_basis = report.quantity * report.entry_price
        current_value = report.quantity * report.last_price
        pnl_usdt = current_value - cost_basis
        qty_label = f"{report.quantity:g}"
        position_line = (
            f"   持仓: {qty_label} {report.name} @ 入场 {_fmt_price(report.entry_price)}"
            f" | 现值 ${current_value:,.2f}"
            f" | 浮盈 {pnl_pct:+.2f}% (${pnl_usdt:+,.2f})\n"
        )
    elif report.entry_price is not None:
        pnl_pct = ((report.last_price - report.entry_price) / report.entry_price) * 100.0
        position_line = (
            f"   持仓: 入场 {_fmt_price(report.entry_price)}"
            f" | 浮盈 {pnl_pct:+.2f}%\n"
        )

    return (
        f"{report.name} ({report.pair})\n"
        f"{position_line}"
        f"   现价: {_fmt_price(report.last_price)} | "
        f"1D {report.perf_1d:+.1f}% | 7D {report.perf_7d:+.1f}% | "
        f"vs BTC 7D {report.rs_7d:+.1f}%\n"
        f"   趋势: 1D {report.trend_1d} / 4H {report.trend_4h}\n"
        f"   短期展望: {report.outlook}\n"
        f"   操作建议: {report.action}\n"
        f"   1-3日预测: {report.prediction}\n"
        f"   关键位: {report.watch_levels}\n"
        f"   相关新闻:\n{news_lines}"
    )


def build_daily_report(
    market_bias: str,
    btc_plan: BtcPlan,
    long_setups: list[TradeSetup],
    short_setups: list[TradeSetup],
    holdings: list[HoldingReport] | None = None,
) -> str:
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")

    lines = [
        f"Crypto 每日交易日報",
        f"時間: {now}",
        f"數據來源: Binance 實時 API",
        "",
        "--------------------------------",
        "[1] 今日市场方向",
        "--------------------------------",
        f"今日適合: {market_bias}",
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
            "--------------------------------",
            "[4] 持仓监控 (AVAX / INJ / TAO)",
            "--------------------------------",
        ]
    )

    if holdings:
        for index, holding in enumerate(holdings, start=1):
            lines.append(_format_holding(holding))
            if index < len(holdings):
                lines.append("")
    else:
        lines.append("暂时无法获取持仓数据。")

    lines.extend(
        [
            "",
            "风险提示: 以上為量化篩選結果，唔係投資建議。",
            "入場前請用 TradingView 4H/1D 圖再確認。",
        ]
    )

    return "\n".join(lines)
