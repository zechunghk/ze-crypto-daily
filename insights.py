"""On-demand single-coin insight for Telegram queries."""

from __future__ import annotations

from analysis import build_trend_setup, candles_to_frame, pct_change_over
from binance_client import get_klines
from holdings import build_holding_report, fetch_headlines


def _fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.4f}"


def _pct_from_entry(entry: float, price: float, direction: str) -> float:
    if direction == "LONG":
        return ((price - entry) / entry) * 100.0
    return ((entry - price) / entry) * 100.0


def _format_trade_setup(setup, label: str) -> str:
    sl_pct = _pct_from_entry(setup.entry, setup.stop_loss, setup.direction)
    tp1_pct = _pct_from_entry(setup.entry, setup.take_profit_1, setup.direction)
    tp2_pct = _pct_from_entry(setup.entry, setup.take_profit_2, setup.direction)
    return (
        f"{label}（强度 {setup.score}/100）\n"
        f"   入场: {_fmt_price(setup.entry)}\n"
        f"   SL: {_fmt_price(setup.stop_loss)} ({sl_pct:+.2f}%)\n"
        f"   TP1: {_fmt_price(setup.take_profit_1)} ({tp1_pct:+.2f}%)\n"
        f"   TP2: {_fmt_price(setup.take_profit_2)} ({tp2_pct:+.2f}%)"
    )


def build_coin_insight(symbol: str) -> str:
    pair = symbol.replace("USDT", "/USDT")
    name = symbol.replace("USDT", "")

    btc_daily = get_klines("BTCUSDT", "1d", 120)
    daily = get_klines(symbol, "1d", 120)
    h4 = get_klines(symbol, "4h", 120)

    btc_perf_7d = pct_change_over(candles_to_frame(btc_daily)["close"], 7)
    holding = build_holding_report(
        symbol=symbol,
        pair=pair,
        name=name,
        news_query=f"{name} cryptocurrency",
        candles_1d=daily,
        candles_4h=h4,
        btc_perf_7d=btc_perf_7d,
    )
    if not holding:
        return f"暂时无法分析 {pair}，数据不足或 K 线未准备好。"

    long_setup = build_trend_setup(symbol, pair, daily, h4, btc_perf_7d, "LONG")
    short_setup = build_trend_setup(symbol, pair, daily, h4, btc_perf_7d, "SHORT")

    lines = [
        f"{pair} 即时分析",
        f"现价: {_fmt_price(holding.last_price)}",
        f"1D {holding.perf_1d:+.1f}% | 7D {holding.perf_7d:+.1f}% | vs BTC 7D {holding.rs_7d:+.1f}%",
        f"趋势: 1D {holding.trend_1d} / 4H {holding.trend_4h}",
        f"短期展望: {holding.outlook}",
        f"操作建议: {holding.action}",
        f"1-3日预测: {holding.prediction}",
        f"关键位: {holding.watch_levels}",
        "",
    ]

    if long_setup:
        lines.append(_format_trade_setup(long_setup, "做多参考"))
        lines.append("")
    else:
        lines.append("做多参考: 今日未达筛选条件")
        lines.append("")

    if short_setup:
        lines.append(_format_trade_setup(short_setup, "做空参考"))
        lines.append("")
    else:
        lines.append("做空参考: 今日未达筛选条件")
        lines.append("")

    lines.append("相关新闻:")
    for headline in fetch_headlines(f"{name} cryptocurrency", limit=2):
        lines.append(f"- {headline}")

    lines.append("")
    lines.append("风险提示: 量化分析结果，唔系投资建议。")
    return "\n".join(lines)
