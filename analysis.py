"""Trend scoring and trade level calculation from OHLCV data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class TradeSetup:
    symbol: str
    pair: str
    score: float
    direction: str
    last_price: float
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float
    reasons: list[str]


def candles_to_frame(candles: list[dict[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(candles)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def pct_change_over(closes: pd.Series, periods: int) -> float:
    if len(closes) <= periods:
        return 0.0
    start = closes.iloc[-periods - 1]
    end = closes.iloc[-1]
    if start == 0:
        return 0.0
    return ((end - start) / start) * 100


def build_trend_setup(
    symbol: str,
    pair: str,
    candles_1d: list[dict[str, float]],
    candles_4h: list[dict[str, float]],
    btc_perf_7d: float,
    direction: str,
) -> TradeSetup | None:
    if len(candles_1d) < 60 or len(candles_4h) < 60:
        return None

    daily = candles_to_frame(candles_1d)
    h4 = candles_to_frame(candles_4h)

    daily["ema20"] = ema(daily["close"], 20)
    daily["ema50"] = ema(daily["close"], 50)
    h4["ema20"] = ema(h4["close"], 20)
    h4["ema50"] = ema(h4["close"], 50)
    h4["vol_ma20"] = h4["volume"].rolling(20).mean()

    last = float(h4["close"].iloc[-1])
    d_close = float(daily["close"].iloc[-1])
    d_ema20 = float(daily["ema20"].iloc[-1])
    d_ema50 = float(daily["ema50"].iloc[-1])
    h_ema20 = float(h4["ema20"].iloc[-1])
    h_ema50 = float(h4["ema50"].iloc[-1])
    vol_ratio = float(h4["volume"].iloc[-1] / max(h4["vol_ma20"].iloc[-1], 1e-9))

    perf_7d = pct_change_over(daily["close"], 7)
    rs_7d = perf_7d - btc_perf_7d

    recent_high = float(h4["high"].tail(20).max())
    recent_low = float(h4["low"].tail(20).min())
    swing_buffer = last * 0.02

    reasons: list[str] = []
    score = 0.0

    if direction == "LONG":
        if d_close > d_ema20 > d_ema50:
            score += 25
            reasons.append("1D 價格 > EMA20 > EMA50")
        if last > h_ema20 > h_ema50:
            score += 25
            reasons.append("4H 多頭排列")
        if rs_7d > 0:
            score += min(rs_7d * 2, 20)
            reasons.append(f"7日跑贏 BTC {rs_7d:+.1f}%")
        if vol_ratio > 1.0:
            score += min((vol_ratio - 1) * 10, 10)
            reasons.append(f"4H 成交量 {vol_ratio:.1f}x 均量")
        if last >= recent_high * 0.9:
            score += 10
            reasons.append("接近 20 根 4H 高位（強勢）")

        if score < 35:
            return None

        entry = round(h_ema20, 6)
        stop_loss = round(min(h_ema50, recent_low) - swing_buffer, 6)
        if stop_loss <= 0 or stop_loss >= entry:
            stop_loss = round(entry * 0.97, 6)
        risk = entry - stop_loss
        if risk <= 0:
            return None
        take_profit_1 = round(entry + risk * 2, 6)
        take_profit_2 = round(max(entry + risk * 3, recent_high * 0.98), 6)
    else:
        if d_close < d_ema20 < d_ema50:
            score += 25
            reasons.append("1D 價格 < EMA20 < EMA50")
        if last < h_ema20 < h_ema50:
            score += 25
            reasons.append("4H 空頭排列")
        if rs_7d < 0:
            score += min(abs(rs_7d) * 2, 20)
            reasons.append(f"7日跑輸 BTC {rs_7d:+.1f}%")
        if vol_ratio > 1.0:
            score += min((vol_ratio - 1) * 10, 10)
            reasons.append(f"4H 成交量 {vol_ratio:.1f}x 均量")
        if last <= recent_low * 1.1:
            score += 10
            reasons.append("接近 20 根 4H 低位（弱勢）")

        if score < 35:
            return None

        entry = round(h_ema20, 6)
        stop_loss = round(max(h_ema50, recent_high) + swing_buffer, 6)
        if stop_loss <= entry:
            stop_loss = round(entry * 1.03, 6)
        risk = stop_loss - entry
        if risk <= 0:
            return None
        take_profit_1 = round(entry - risk * 2, 6)
        take_profit_2 = round(max(entry - risk * 3, recent_low * 1.02), 6)
        if take_profit_1 <= 0:
            take_profit_1 = round(entry * 0.95, 6)
        if take_profit_2 <= 0:
            take_profit_2 = round(entry * 0.90, 6)

    risk_reward = 2.0 if risk > 0 else 0.0

    return TradeSetup(
        symbol=symbol,
        pair=pair,
        score=round(score, 1),
        direction=direction,
        last_price=round(last, 6),
        entry=entry,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward=risk_reward,
        reasons=reasons[:4],
    )


def pick_top_setups(setups: Iterable[TradeSetup], direction: str, limit: int = 5) -> list[TradeSetup]:
    filtered = [setup for setup in setups if setup.direction == direction]
    filtered.sort(key=lambda item: item.score, reverse=True)
    return filtered[:limit]


@dataclass
class BtcPlan:
    bias: str
    confidence: str
    last_price: float
    range_low: float
    range_high: float
    support_1: float
    support_2: float
    resistance_1: float
    resistance_2: float
    plan_lines: list[str]
    reasons: list[str]


def build_btc_plan(candles_1d: list[dict[str, float]], candles_4h: list[dict[str, float]]) -> BtcPlan:
    daily = candles_to_frame(candles_1d)
    h4 = candles_to_frame(candles_4h)

    daily["ema20"] = ema(daily["close"], 20)
    daily["ema50"] = ema(daily["close"], 50)

    last = float(daily["close"].iloc[-1])
    ema20 = float(daily["ema20"].iloc[-1])
    ema50 = float(daily["ema50"].iloc[-1])

    prev_high = float(daily["high"].iloc[-2])
    prev_low = float(daily["low"].iloc[-2])
    week_high = float(daily["high"].tail(7).max())
    week_low = float(daily["low"].tail(7).min())

    tr = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - daily["close"].shift(1)).abs(),
            (daily["low"] - daily["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(tr.tail(14).mean())

    range_low = round(max(week_low, last - atr14), 2)
    range_high = round(min(week_high, last + atr14), 2)

    support_1 = round(min(ema20, prev_low), 2)
    support_2 = round(min(ema50, week_low), 2)
    resistance_1 = round(max(ema20, prev_high), 2)
    resistance_2 = round(max(week_high, resistance_1 + atr14 * 0.5), 2)

    bullish_points = 0
    bearish_points = 0
    reasons: list[str] = []

    if last > ema20 > ema50:
        bullish_points += 2
        reasons.append("1D 多頭排列")
    elif last < ema20 < ema50:
        bearish_points += 2
        reasons.append("1D 空頭排列")
    else:
        reasons.append("1D 趨勢橫行/拉鋸")

    h4["ema20"] = ema(h4["close"], 20)
    h4_last = float(h4["close"].iloc[-1])
    h4_ema20 = float(h4["ema20"].iloc[-1])
    if h4_last > h4_ema20:
        bullish_points += 1
        reasons.append("4H 站穩 EMA20 上方")
    else:
        bearish_points += 1
        reasons.append("4H 處於 EMA20 下方")

    perf_3d = pct_change_over(daily["close"], 3)
    if perf_3d > 1:
        bullish_points += 1
        reasons.append(f"近 3 日 +{perf_3d:.1f}%")
    elif perf_3d < -1:
        bearish_points += 1
        reasons.append(f"近 3 日 {perf_3d:.1f}%")

    if bullish_points > bearish_points + 1:
        bias = "偏多 (LONG)"
        confidence = "中高"
        plan_lines = [
            f"回踩 {support_1:,.2f} – {support_2:,.2f} 附近再做多",
            f"突破 {resistance_1:,.2f} 可追多，目標 {resistance_2:,.2f}",
            "止蝕放喺前低或 4H EMA50 下方",
        ]
    elif bearish_points > bullish_points + 1:
        bias = "偏空 (SHORT)"
        confidence = "中高"
        plan_lines = [
            f"反彈 {resistance_1:,.2f} 附近遇阻可做空",
            f"跌穿 {support_1:,.2f} 睇 {support_2:,.2f}",
            "止蝕放喺前高或 4H EMA50 上方",
        ]
    else:
        bias = "觀望 / 區間交易"
        confidence = "中"
        plan_lines = [
            f"區間 {range_low:,.2f} – {range_high:,.2f} 高抛低吸",
            f"突破 {range_high:,.2f} 先轉多",
            f"跌穿 {range_low:,.2f} 先轉空",
        ]

    return BtcPlan(
        bias=bias,
        confidence=confidence,
        last_price=round(last, 2),
        range_low=range_low,
        range_high=range_high,
        support_1=support_1,
        support_2=support_2,
        resistance_1=resistance_1,
        resistance_2=resistance_2,
        plan_lines=plan_lines,
        reasons=reasons,
    )
