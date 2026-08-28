"""Holdings monitor — technical view, short-term outlook, and headlines."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

from analysis import candles_to_frame, ema, pct_change_over

HOLDINGS = [
    {
        "symbol": "AVAXUSDT",
        "pair": "AVAX/USDT",
        "name": "AVAX",
        "news_query": "Avalanche AVAX cryptocurrency",
    },
    {
        "symbol": "INJUSDT",
        "pair": "INJ/USDT",
        "name": "INJ",
        "news_query": "Injective INJ cryptocurrency",
        "entry_price": 5.5,
        "cost_usdt": 77.0,
    },
    {
        "symbol": "TAOUSDT",
        "pair": "TAO/USDT",
        "name": "TAO",
        "news_query": "Bittensor TAO cryptocurrency",
    },
]

NEWS_SESSION = requests.Session()
NEWS_SESSION.headers.update({"User-Agent": "ze-crypto-daily/1.0"})


@dataclass
class HoldingReport:
    symbol: str
    pair: str
    name: str
    last_price: float
    perf_1d: float
    perf_7d: float
    rs_7d: float
    trend_1d: str
    trend_4h: str
    support: float
    resistance: float
    outlook: str
    action: str
    prediction: str
    watch_levels: str
    news: list[str]
    entry_price: float | None = None
    cost_usdt: float | None = None


def _trend_label(close: float, ema20: float, ema50: float) -> str:
    if close > ema20 > ema50:
        return "多頭"
    if close < ema20 < ema50:
        return "空頭"
    return "橫行"


def _build_outlook(
    trend_1d: str,
    trend_4h: str,
    rs_7d: float,
    perf_1d: float,
) -> tuple[str, str, str]:
    if trend_1d == "多頭" and trend_4h == "多頭" and rs_7d > 0:
        outlook = "短期偏多"
        action = "持有 / 回踩 support 可小幅加仓"
        prediction = "未来 1-3 日偏向延续上行，留意 BTC 同步方向"
    elif trend_1d == "空頭" and trend_4h == "空頭" and rs_7d < 0:
        outlook = "短期偏空"
        action = "减仓或设置止损，避免逆势加仓"
        prediction = "未来 1-3 日偏弱，反弹至 resistance 可能受阻"
    elif trend_4h == "多頭" and trend_1d != "空頭":
        outlook = "短线反弹"
        action = "持有为主，突破 resistance 才考虑加仓"
        prediction = "未来 1-3 日可能区间震荡后尝试上攻"
    elif trend_4h == "空頭" and trend_1d != "多頭":
        outlook = "短线回调"
        action = "持有但收紧止损，未企稳前不加仓"
        prediction = "未来 1-3 日可能回测 support，关注能否守住"
    else:
        outlook = "横盘整理"
        action = "持有观望，等待 4H 方向明朗"
        prediction = "未来 1-3 日大概率维持区间波动"

    if perf_1d > 3:
        prediction += "；今日动能偏强"
    elif perf_1d < -3:
        prediction += "；今日抛压较重"

    return outlook, action, prediction


def fetch_headlines(query: str, limit: int = 2) -> list[str]:
    url = (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        response = NEWS_SESSION.get(url, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        headlines: list[str] = []
        for item in root.findall(".//item"):
            title = item.findtext("title")
            if title:
                headlines.append(title.strip())
            if len(headlines) >= limit:
                break
        return headlines
    except (requests.RequestException, ET.ParseError):
        return ["暂时无法获取新闻（请稍后重试）"]


def build_holding_report(
    symbol: str,
    pair: str,
    name: str,
    news_query: str,
    candles_1d: list[dict[str, float]],
    candles_4h: list[dict[str, float]],
    btc_perf_7d: float,
) -> HoldingReport | None:
    if len(candles_1d) < 60 or len(candles_4h) < 60:
        return None

    daily = candles_to_frame(candles_1d)
    h4 = candles_to_frame(candles_4h)

    daily["ema20"] = ema(daily["close"], 20)
    daily["ema50"] = ema(daily["close"], 50)
    h4["ema20"] = ema(h4["close"], 20)
    h4["ema50"] = ema(h4["close"], 50)

    last = float(h4["close"].iloc[-1])
    d_close = float(daily["close"].iloc[-1])
    d_ema20 = float(daily["ema20"].iloc[-1])
    d_ema50 = float(daily["ema50"].iloc[-1])
    h_ema20 = float(h4["ema20"].iloc[-1])
    h_ema50 = float(h4["ema50"].iloc[-1])

    perf_1d = pct_change_over(daily["close"], 1)
    perf_7d = pct_change_over(daily["close"], 7)
    rs_7d = perf_7d - btc_perf_7d

    trend_1d = _trend_label(d_close, d_ema20, d_ema50)
    trend_4h = _trend_label(last, h_ema20, h_ema50)

    support = round(min(h_ema50, float(h4["low"].tail(20).min())), 6)
    resistance = round(max(h_ema20, float(h4["high"].tail(20).max())), 6)

    outlook, action, prediction = _build_outlook(trend_1d, trend_4h, rs_7d, perf_1d)
    watch_levels = f"Support {_fmt(support)} / Resistance {_fmt(resistance)}"
    news = fetch_headlines(news_query, limit=2)

    return HoldingReport(
        symbol=symbol,
        pair=pair,
        name=name,
        last_price=round(last, 6),
        perf_1d=round(perf_1d, 2),
        perf_7d=round(perf_7d, 2),
        rs_7d=round(rs_7d, 2),
        trend_1d=trend_1d,
        trend_4h=trend_4h,
        outlook=outlook,
        action=action,
        prediction=prediction,
        support=support,
        resistance=resistance,
        watch_levels=watch_levels,
        news=news,
    )


def build_all_holding_reports(
    btc_perf_7d: float,
    daily_map: dict[str, list[dict[str, float]]],
    h4_map: dict[str, list[dict[str, float]]],
) -> list[HoldingReport]:
    reports: list[HoldingReport] = []
    for holding in HOLDINGS:
        symbol = holding["symbol"]
        if symbol not in daily_map or symbol not in h4_map:
            continue
        report = build_holding_report(
            symbol=symbol,
            pair=holding["pair"],
            name=holding["name"],
            news_query=holding["news_query"],
            candles_1d=daily_map[symbol],
            candles_4h=h4_map[symbol],
            btc_perf_7d=btc_perf_7d,
        )
        if report:
            report.entry_price = holding.get("entry_price")
            report.cost_usdt = holding.get("cost_usdt")
            reports.append(report)
    return reports


def _fmt(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.6f}"
