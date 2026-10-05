"""RSI 觀察名單篩選，邏輯同 rsi-crypto-watchlist 一致。

日內（intraday）：日線 RSI >= 50、1 小時向上、15 分 RSI 分級。
波段（interday）：日線 RSI >= 50、日線向上、4 小時 RSI 分級。
呢度只負責掃描同格式化，發送由 rsi_alert.py / Telegram bot 做。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

BINANCE = "https://data-api.binance.vision"
HK = timezone(timedelta(hours=8))

EXCLUDED_BASES = {
    "USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "USTC", "USDE",
    "USD1", "BFUSD", "XUSD", "EURI", "AEUR", "EUR", "GBP", "TRY",
    "BRL", "AUD", "RUB", "PAXG",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


@dataclass(frozen=True)
class Candle:
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class LongPlan:
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    wait_pullback: bool
    risk_reward: float
    risk_pct: float
    reward_pct: float
    tp1_basis: str = ""
    tp2_basis: str = ""
    risk_reward_2: float = 0.0
    reward_pct_2: float = 0.0


@dataclass(frozen=True)
class ScanRow:
    symbol: str
    last_price: float
    daily_rsi: float
    hourly_rsi: float
    rsi_15m: float
    volume_ratio: float
    grade: str
    note: str
    second_signal: bool
    plan: LongPlan | None
    rank: str


@dataclass(frozen=True)
class SwingRow:
    symbol: str
    last_price: float
    daily_rsi: float
    rsi_4h: float
    volume_ratio: float
    grade: str
    note: str
    second_signal: bool
    plan: LongPlan | None
    rank: str


def configured_top(override: int | None = None) -> int:
    """同網頁版一樣，預設掃成交額最高 100 隻。"""
    if override is None:
        raw = os.getenv("RSI_TOP", "100").strip()
        try:
            top = int(raw)
        except ValueError:
            return 100
    else:
        top = override
    return min(100, max(20, top))


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    values: list[float] = []
    for end in range(period + 1, len(closes) + 1):
        value = rsi(closes[:end], period)
        if value is not None:
            values.append(value)
    return values


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    smoothing = 2.0 / (period + 1)
    average = sum(values[:period]) / period
    for value in values[period:]:
        average = value * smoothing + average * (1.0 - smoothing)
    return average


def hourly_trend_up(closes: list[float]) -> bool:
    if len(closes) < 26:
        return False
    average = ema(closes, 20)
    if average is None:
        return False
    return closes[-1] > average and closes[-1] > closes[-7]


def volume_ratio(volumes: list[float], lookback: int = 20) -> float | None:
    if len(volumes) < lookback + 1:
        return None
    baseline = volumes[-(lookback + 1):-1]
    average = sum(baseline) / len(baseline)
    if average <= 0:
        return None
    return volumes[-1] / average


def session_note(rsi_values: list[float]) -> tuple[str, bool]:
    if len(rsi_values) < 8:
        return "15分數據不足", False
    current = rsi_values[-1]
    previous = rsi_values[-2]
    history = rsi_values[:-1]
    push_indexes = [index for index, value in enumerate(history) if value >= 60]
    if current < 50:
        return "15分RSI跌破50，今日剔除", False
    if not push_indexes:
        if current >= 60:
            return "第一波企上60，未回拉，只睇唔追", False
        return "未企過60，未到入場窗", False

    after_push = history[push_indexes[-1]:] + [current]
    trough = min(after_push)
    if trough <= 45:
        return "回拉探到45，今日剔除", False
    held_above_50 = trough >= 50
    dipped_under_60 = trough < 60
    reclaimed = previous < 60 <= current
    if held_above_50 and dipped_under_60 and reclaimed:
        return "回拉守住後再企上60，第二次訊號", True
    if current >= 60 and trough >= 60:
        return "一直企喺60上，未回拉，唔追第一波", False
    if 54 <= current <= 59:
        return "回拉到55附近，暫時守住，繼續睇", False
    if trough < 50 <= current and current >= previous:
        return "跌破50後企返，繼續觀察，未算訊號", False
    if 50 <= current < 54:
        return "回到50附近，再睇會唔會企返", False
    return "觀察中", False


def day_rank(
    listed: bool,
    grade: str | None,
    second_signal: bool,
    plan: LongPlan | None,
) -> str:
    """同網頁：過關先 B 以上；第二次訊號 + A + 有倉位先係 S。"""
    if not listed:
        return "C"
    if second_signal and grade == "A" and plan is not None:
        return "S"
    if grade == "A":
        return "A"
    return "B"


def grade_bar(rsi_15m: float, rel_volume: float) -> str | None:
    if rsi_15m < 50:
        return None
    if rsi_15m >= 60 and rel_volume >= 1.5:
        return "A"
    return "B"


def closed_candles(candles: list[Candle]) -> list[Candle]:
    if len(candles) < 3:
        return candles
    return candles[:-1]


def parse_klines(payload: list) -> list[Candle]:
    candles: list[Candle] = []
    for row in payload:
        candles.append(
            Candle(
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        )
    return candles


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous_close = candles[index - 1].close
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    window = true_ranges[-period:]
    if len(window) < period:
        return None
    value = sum(window) / period
    if value <= 0:
        return None
    return value


def build_long_plan(
    level_candles: list[Candle],
    last_price: float,
    second_signal: bool,
    max_risk_pct: float = 0.08,
    min_rr: float = 2.0,
    target_rr: float = 2.2,
) -> LongPlan | None:
    """做多參考位。TP 優先結構／Fib／ATR；R/R 必須 > min_rr。"""
    if len(level_candles) < 26 or last_price <= 0:
        return None
    closes = [candle.close for candle in level_candles]
    average = ema(closes, 20)
    volatility = atr(level_candles)
    if average is None or volatility is None:
        return None

    wait_pullback = not second_signal and last_price > average * 1.005
    entry = average if wait_pullback else last_price
    if entry <= 0:
        return None

    recent = level_candles[-20:]
    wider = level_candles[-40:] if len(level_candles) >= 40 else level_candles
    recent_high = max(candle.high for candle in recent)
    wider_high = max(candle.high for candle in wider)
    swing_low = min(candle.low for candle in level_candles[-8:])
    structural = swing_low * 0.998
    atr_stop = entry - volatility * 1.5
    widest_atr = entry - volatility * 3.0
    if structural < entry and entry - structural >= volatility * 0.8:
        stop_loss = max(structural, widest_atr)
    else:
        stop_loss = atr_stop
    risk_floor = entry * (1.0 - max_risk_pct)
    if stop_loss < risk_floor:
        stop_loss = risk_floor
    if stop_loss <= 0 or stop_loss >= entry:
        stop_loss = entry * 0.97
    risk = entry - stop_loss
    if risk <= 0:
        return None

    impulse = max(recent_high - swing_low, risk)
    fib_1272 = swing_low + impulse * 1.272
    fib_1618 = swing_low + impulse * 1.618
    atr_tp1 = entry + volatility * 2.0
    atr_tp2 = entry + volatility * 3.5
    rr_floor = entry + risk * (min_rr + 0.05)
    rr_target = entry + risk * target_rr
    rr3 = entry + risk * 3.0

    tp1_candidates: list[tuple[float, str]] = []
    if recent_high > entry * 1.001:
        tp1_candidates.append((recent_high, "近20根高位"))
    if fib_1272 > entry * 1.001:
        tp1_candidates.append((fib_1272, "Fib 1.272 延伸"))
    if atr_tp1 > entry * 1.001:
        tp1_candidates.append((atr_tp1, "ATR×2"))
    tp1_candidates.append((rr_target, f"目標 R/R {target_rr}"))
    tp1_candidates.append((rr_floor, f"最低 R/R {min_rr}+"))

    take_profit_1 = None
    tp1_basis = ""
    for price, basis in sorted(tp1_candidates, key=lambda item: item[0]):
        if (price - entry) / risk > min_rr:
            take_profit_1 = price
            tp1_basis = basis
            break
    if take_profit_1 is None:
        needed_risk = (rr_target - entry) / target_rr
        tighter = entry - needed_risk
        if tighter > stop_loss and tighter < entry and tighter >= risk_floor:
            stop_loss = tighter
            risk = entry - stop_loss
            take_profit_1 = entry + risk * target_rr
            tp1_basis = f"收窄SL後 R/R {target_rr}"
        else:
            take_profit_1 = entry + risk * target_rr
            tp1_basis = f"目標 R/R {target_rr}"
            if (take_profit_1 - entry) / risk <= min_rr:
                return None

    reward = take_profit_1 - entry
    risk_reward = reward / risk if risk > 0 else 0.0
    if risk_reward <= min_rr:
        return None

    tp2_candidates: list[tuple[float, str]] = []
    if wider_high > take_profit_1 * 1.002:
        tp2_candidates.append((wider_high, "近40根高位"))
    if recent_high * 1.02 > take_profit_1 * 1.002:
        tp2_candidates.append((recent_high * 1.02, "近高×1.02"))
    if fib_1618 > take_profit_1 * 1.002:
        tp2_candidates.append((fib_1618, "Fib 1.618 延伸"))
    if atr_tp2 > take_profit_1 * 1.002:
        tp2_candidates.append((atr_tp2, "ATR×3.5"))
    if rr3 > take_profit_1 * 1.002:
        tp2_candidates.append((rr3, "R/R 3.0"))
    tp2_candidates.append((take_profit_1 + risk, "TP1+1R"))

    take_profit_2, tp2_basis = min(tp2_candidates, key=lambda item: item[0])
    if take_profit_2 <= take_profit_1:
        take_profit_2 = take_profit_1 + risk
        tp2_basis = "TP1+1R"

    reward_2 = take_profit_2 - entry
    return LongPlan(
        entry=entry,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        wait_pullback=wait_pullback,
        risk_reward=round(risk_reward, 2),
        risk_pct=round((risk / entry) * 100.0, 2),
        reward_pct=round((reward / entry) * 100.0, 2),
        tp1_basis=tp1_basis,
        tp2_basis=tp2_basis,
        risk_reward_2=round(reward_2 / risk, 2),
        reward_pct_2=round((reward_2 / entry) * 100.0, 2),
    )


def fetch_json(url: str, timeout: int = 20, attempts: int = 3):
    request = urllib.request.Request(url, headers={"User-Agent": "ze-crypto-daily-rsi"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise RuntimeError(f"請求被拒：{url}") from error
            last_error = error
            time.sleep(0.4 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"攞唔到數據：{url}") from last_error


def tradable_usdt_symbols() -> set[str]:
    info = fetch_json(f"{BINANCE}/api/v3/exchangeInfo")
    allowed: set[str] = set()
    for item in info.get("symbols", []):
        if item.get("status") != "TRADING" or item.get("quoteAsset") != "USDT":
            continue
        if not item.get("isSpotTradingAllowed", True):
            continue
        base = item.get("baseAsset", "")
        if base in EXCLUDED_BASES or base.endswith(LEVERAGED_SUFFIXES):
            continue
        symbol = item.get("symbol", "")
        if symbol.endswith("USDT"):
            allowed.add(symbol)
    return allowed


def top_symbols(limit: int) -> list[str]:
    allowed = tradable_usdt_symbols()
    tickers = fetch_json(f"{BINANCE}/api/v3/ticker/24hr")
    ranked = []
    for ticker in tickers:
        symbol = ticker.get("symbol", "")
        if symbol not in allowed:
            continue
        try:
            quote_volume = float(ticker.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        ranked.append((quote_volume, symbol))
    ranked.sort(reverse=True)
    return [symbol for _, symbol in ranked[:limit]]


def fetch_klines(symbol: str, interval: str, limit: int) -> list[Candle]:
    url = (
        f"{BINANCE}/api/v3/klines?symbol={symbol}"
        f"&interval={interval}&limit={limit}"
    )
    return parse_klines(fetch_json(url))


def analyse(symbol: str) -> ScanRow | None:
    daily = closed_candles(fetch_klines(symbol, "1d", 120))
    hourly = fetch_klines(symbol, "1h", 120)
    quarter = closed_candles(fetch_klines(symbol, "15m", 120))
    if len(daily) < 20 or len(hourly) < 30 or len(quarter) < 40:
        return None

    daily_rsi = rsi([candle.close for candle in daily])
    if daily_rsi is None or daily_rsi < 50:
        return None
    hourly_closes = [candle.close for candle in hourly]
    if not hourly_trend_up(hourly_closes):
        return None

    quarter_closes = [candle.close for candle in quarter]
    quarter_volumes = [candle.volume for candle in quarter]
    rsi_15m_values = rsi_series(quarter_closes)
    rsi_15m = rsi_15m_values[-1] if rsi_15m_values else None
    rel_volume = volume_ratio(quarter_volumes)
    hourly_rsi = rsi(hourly_closes)
    if rsi_15m is None or rel_volume is None or hourly_rsi is None:
        return None

    note, second_signal = session_note(rsi_15m_values[-32:])
    grade = grade_bar(rsi_15m, rel_volume)
    if grade is None or "今日剔除" in note:
        return None
    if second_signal and rel_volume < 1.3:
        note = "第二次訊號，但量未跟上，縮注或者再確認"
    elif second_signal:
        note = "第二次訊號，量跟得上"
    elif grade == "B" and rsi_15m >= 60:
        note = f"{note}；力度有但量未放大，降做B"

    plan = build_long_plan(closed_candles(hourly), quarter_closes[-1], second_signal, 0.08)

    return ScanRow(
        symbol=symbol.removesuffix("USDT"),
        last_price=quarter_closes[-1],
        daily_rsi=daily_rsi,
        hourly_rsi=hourly_rsi,
        rsi_15m=rsi_15m,
        volume_ratio=rel_volume,
        grade=grade,
        note=note,
        second_signal=second_signal,
        plan=plan,
        rank=day_rank(True, grade, second_signal, plan),
    )


def daily_trend_up(closes: list[float]) -> bool:
    if len(closes) < 25:
        return False
    average = ema(closes, 20)
    if average is None:
        return False
    return closes[-1] > average and closes[-1] > closes[-6]


def grade_swing(rsi_4h: float, daily_volume_ratio: float) -> str | None:
    """同日內力度規則：RSI≥50 先入；A 要 RSI≥60 且量比≥1.5。"""
    if rsi_4h < 50:
        return None
    if rsi_4h >= 60 and daily_volume_ratio >= 1.5:
        return "A"
    return "B"


def swing_note(rsi_values: list[float]) -> tuple[str, bool]:
    note, second_signal = session_note(rsi_values)
    translated = {
        "15分RSI跌破50，今日剔除": "4小時跌破50，呢幾日剔除",
        "回拉探到45，今日剔除": "4小時回拉探到45，呢幾日剔除",
        "第一波企上60，未回拉，只睇唔追": "4小時剛企上60，未回拉，唔好追住入",
        "一直企喺60上，未回拉，唔追第一波": "4小時一直企喺60上，未回拉，唔好追住入",
        "回拉守住後再企上60，第二次訊號": "回拉守住後再企上60，可以考慮持有幾日",
        "回拉到55附近，暫時守住，繼續睇": "4小時回拉到55附近，守住就可以繼續睇",
        "未企過60，未到入場窗": "4小時未企過60，未到入場",
        "跌破50後企返，繼續觀察，未算訊號": "4小時跌破50後企返，繼續觀察",
        "回到50附近，再睇會唔會企返": "4小時回到50附近，再睇會唔會企返",
        "15分數據不足": "4小時數據不足",
    }
    return translated.get(note, note), second_signal


def analyse_swing(symbol: str) -> SwingRow | None:
    daily = closed_candles(fetch_klines(symbol, "1d", 120))
    four_hour = closed_candles(fetch_klines(symbol, "4h", 120))
    if len(daily) < 25 or len(four_hour) < 40:
        return None

    daily_closes = [candle.close for candle in daily]
    daily_rsi = rsi(daily_closes)
    if daily_rsi is None or daily_rsi < 50 or not daily_trend_up(daily_closes):
        return None

    four_closes = [candle.close for candle in four_hour]
    if not hourly_trend_up(four_closes):
        return None

    rsi_4h_values = rsi_series(four_closes)
    rsi_4h = rsi_4h_values[-1] if rsi_4h_values else None
    daily_volume = volume_ratio([candle.volume for candle in daily])
    if rsi_4h is None or daily_volume is None:
        return None

    note, second_signal = swing_note(rsi_4h_values[-18:])
    grade = grade_swing(rsi_4h, daily_volume)
    if grade is None or "剔除" in note:
        return None
    if second_signal and daily_volume < 1.3:
        note = "回拉後企穩，但日線量未放大，縮注或者再確認"
    elif second_signal:
        note = "回拉後企穩，量跟得上"
    elif grade == "B" and rsi_4h >= 60:
        note = f"{note}；力度有但日線量未放大，降做B"

    plan = build_long_plan(four_hour, four_closes[-1], second_signal, 0.08)

    return SwingRow(
        symbol=symbol.removesuffix("USDT"),
        last_price=four_closes[-1],
        daily_rsi=daily_rsi,
        rsi_4h=rsi_4h,
        volume_ratio=daily_volume,
        grade=grade,
        note=note,
        second_signal=second_signal,
        plan=plan,
        rank=day_rank(True, grade, second_signal, plan),
    )


def scan(limit: int, workers: int = 6) -> tuple[list[ScanRow], int]:
    symbols = top_symbols(limit)
    rows: list[ScanRow] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyse, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                row = future.result()
            except Exception as error:
                print(f"跳過 {symbol}：{error}", file=sys.stderr)
                continue
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda row: ({"S": 0, "A": 1, "B": 2}.get(row.rank, 9), not row.second_signal, -row.rsi_15m))
    return rows, len(symbols)


def scan_swing(limit: int, workers: int = 6) -> tuple[list[SwingRow], int]:
    symbols = top_symbols(limit)
    rows: list[SwingRow] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyse_swing, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                row = future.result()
            except Exception as error:
                print(f"跳過 {symbol}：{error}", file=sys.stderr)
                continue
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda row: ({"S": 0, "A": 1, "B": 2}.get(row.rank, 9), not row.second_signal, -row.rsi_4h))
    return rows, len(symbols)


def format_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.6f}"


def _row_rank(row) -> str:
    if isinstance(row, dict):
        return str(row.get("rank") or row.get("grade") or "B")
    return str(getattr(row, "rank", None) or getattr(row, "grade", "B"))


def _rank_counts(rows) -> str:
    counts = {"S": 0, "A": 0, "B": 0}
    for row in rows:
        rank = _row_rank(row)
        if rank in counts:
            counts[rank] += 1
    return f"S {counts['S']} · A {counts['A']} · B {counts['B']}"


def _sort_passed(rows):
    """只留 S/A/B，按排名排。"""
    passed = [row for row in rows if _row_rank(row) in {"S", "A", "B"}]
    passed.sort(
        key=lambda row: (
            {"S": 0, "A": 1, "B": 2}.get(_row_rank(row), 9),
            not (row.get("second_signal") if isinstance(row, dict) else row.second_signal),
        )
    )
    return passed


def _format_rows(rows, line_for_row) -> list[str]:
    rows = _sort_passed(rows)
    if not rows:
        return ["今次冇標的過關。"]
    s_rows = [row for row in rows if _row_rank(row) == "S"]
    a_rows = [row for row in rows if _row_rank(row) == "A"]
    b_rows = [row for row in rows if _row_rank(row) == "B"]
    lines: list[str] = []
    if s_rows:
        lines.append(f"S（第二次訊號 + A）：{len(s_rows)} 隻")
        lines.extend(line_for_row(row) for row in s_rows)
    else:
        lines.append("S：暫時冇。")
    if a_rows:
        lines.append("")
        lines.append(f"A：{len(a_rows)} 隻")
        lines.extend(line_for_row(row) for row in a_rows)
    if b_rows:
        lines.append("")
        lines.append(f"B：{len(b_rows)} 隻")
        lines.extend(line_for_row(row) for row in b_rows)
    return lines


def _pct(entry: float, price: float) -> float:
    return ((price - entry) / entry) * 100.0


def _plan_lines(plan: LongPlan | None) -> str:
    if plan is None:
        return "   暫無建議倉位"
    label = "建議買入（等回拉）" if plan.wait_pullback else "建議買入"
    tp1_tag = f"（{plan.tp1_basis}）" if plan.tp1_basis else ""
    tp2_tag = f"（{plan.tp2_basis}）" if plan.tp2_basis else ""
    return (
        f"   {label}: {format_price(plan.entry)}\n"
        f"   SL: {format_price(plan.stop_loss)} ({_pct(plan.entry, plan.stop_loss):+.2f}%)\n"
        f"   TP1{tp1_tag}: {format_price(plan.take_profit_1)} ({_pct(plan.entry, plan.take_profit_1):+.2f}%)\n"
        f"   TP2{tp2_tag}: {format_price(plan.take_profit_2)} ({_pct(plan.entry, plan.take_profit_2):+.2f}%)\n"
        f"   R/R {plan.risk_reward}→{plan.risk_reward_2} · 風險 {plan.risk_pct}% · "
        f"目標 +{plan.reward_pct}% / +{plan.reward_pct_2}%"
    )


def _day_line(row: ScanRow) -> str:
    return (
        f"{row.symbol}  {row.rank}  ${format_price(row.last_price)}\n"
        f"   日RSI {row.daily_rsi:.1f} | 1h {row.hourly_rsi:.1f} | "
        f"15m {row.rsi_15m:.1f} | 量比 {row.volume_ratio:.2f}\n"
        f"   {row.note}\n"
        f"{_plan_lines(row.plan)}"
    )


def _swing_line(row: SwingRow) -> str:
    return (
        f"{row.symbol}  {row.rank}  ${format_price(row.last_price)}\n"
        f"   日RSI {row.daily_rsi:.1f} | 4h {row.rsi_4h:.1f} | 量比 {row.volume_ratio:.2f}\n"
        f"   {row.note}\n"
        f"{_plan_lines(row.plan)}"
    )


def format_day_alert(rows: list[ScanRow], scanned: int) -> str:
    passed = _sort_passed(rows)
    now = datetime.now(HK).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"日內 RSI 觀察  {now} HKT",
        f"掃描成交額最高 {scanned} 隻，過關 {len(passed)} 隻。{_rank_counts(passed)}",
        "只發過關。S＝第二次訊號+力度A；A＝15分RSI>=60且量比>=1.5；B＝其餘過關。",
        "條件：日線RSI>=50、1小時向上、15分RSI>=50。",
        "買入/SL/TP 係參考位。第二次訊號先用現價，否則等回拉到 1 小時 EMA20。",
        "觀察名單，唔係買賣指令。",
        "",
    ]
    lines.extend(_format_rows(passed, _day_line))
    return "\n".join(lines)


def format_swing_alert(rows: list[SwingRow], scanned: int) -> str:
    passed = _sort_passed(rows)
    now = datetime.now(HK).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"波段 RSI 觀察  {now} HKT",
        f"掃描成交額最高 {scanned} 隻，過關 {len(passed)} 隻。{_rank_counts(passed)}",
        "只發過關。S＝第二次訊號+力度A；A＝4小時RSI>=60且日線量比>=1.5；B＝其餘過關。",
        "條件：日線RSI>=50、日線同4小時向上。",
        "買入/SL/TP 係參考位。第二次訊號先用現價，否則等回拉到 4 小時 EMA20。",
        "觀察名單，唔係買賣指令。",
        "",
    ]
    lines.extend(_format_rows(passed, _swing_line))
    return "\n".join(lines)


def watchlist_base() -> str:
    return os.getenv(
        "WATCHLIST_URL",
        "https://rsi-crypto-watchlist-production.up.railway.app",
    ).rstrip("/")


def fetch_site_scan(kind: str, top: int) -> dict:
    """讀網頁正在顯示嘅同一份快取，避免 Telegram 另計一份。"""
    path = "watchlist" if kind == "day" else "swing"
    payload = fetch_json(f"{watchlist_base()}/api/{path}?top={top}", timeout=25, attempts=2)
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise RuntimeError("網頁名單格式唔啱")
    return payload


def _listed_site_rows(payload: dict) -> list[dict]:
    """網頁過關名單；C／未過關唔發。"""
    rows = [
        row for row in payload["rows"]
        if row.get("listed") and str(row.get("rank") or "B") in {"S", "A", "B"}
    ]
    return _sort_passed(rows)


def _api_plan_lines(plan: dict | None) -> str:
    if not plan:
        return "   暫無建議倉位"
    label = "建議買入（等回拉）" if plan.get("wait_pullback") else "建議買入"
    tp1_tag = f"（{plan.get('tp1_basis')}）" if plan.get("tp1_basis") else ""
    tp2_tag = f"（{plan.get('tp2_basis')}）" if plan.get("tp2_basis") else ""
    entry = float(plan["entry"])
    stop_loss = float(plan["stop_loss"])
    take_profit_1 = float(plan["take_profit_1"])
    take_profit_2 = float(plan["take_profit_2"])
    return (
        f"   {label}: {plan.get('entry_text') or format_price(entry)}\n"
        f"   SL: {plan.get('stop_loss_text') or format_price(stop_loss)} "
        f"({_pct(entry, stop_loss):+.2f}%)\n"
        f"   TP1{tp1_tag}: {plan.get('take_profit_1_text') or format_price(take_profit_1)} "
        f"({_pct(entry, take_profit_1):+.2f}%)\n"
        f"   TP2{tp2_tag}: {plan.get('take_profit_2_text') or format_price(take_profit_2)} "
        f"({_pct(entry, take_profit_2):+.2f}%)\n"
        f"   R/R {plan.get('risk_reward')}→{plan.get('risk_reward_2')} · "
        f"風險 {plan.get('risk_pct')}% · "
        f"目標 +{plan.get('reward_pct')}% / +{plan.get('reward_pct_2')}%"
    )


def _site_day_line(row: dict) -> str:
    return (
        f"{row['symbol']}  {row.get('rank') or row.get('grade')}  ${row.get('price_text')}\n"
        f"   日RSI {float(row['daily_rsi']):.1f} | 1h {float(row['hourly_rsi']):.1f} | "
        f"15m {float(row['rsi_15m']):.1f} | 量比 {float(row['volume_ratio']):.2f}\n"
        f"   {row.get('note') or ''}\n"
        f"{_api_plan_lines(row.get('plan'))}"
    )


def _site_swing_line(row: dict) -> str:
    return (
        f"{row['symbol']}  {row.get('rank') or row.get('grade')}  ${row.get('price_text')}\n"
        f"   日RSI {float(row['daily_rsi']):.1f} | 4h {float(row['rsi_4h']):.1f} | "
        f"量比 {float(row['volume_ratio']):.2f}\n"
        f"   {row.get('note') or ''}\n"
        f"{_api_plan_lines(row.get('plan'))}"
    )


def format_day_from_site(payload: dict) -> str:
    rows = _listed_site_rows(payload)
    when = payload.get("generated_at") or datetime.now(HK).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"日內 RSI 觀察  {when} HKT",
        f"同網頁同一份。掃描 {payload.get('scanned')} 隻，過關 {len(rows)} 隻。{_rank_counts(rows)}",
        "只發過關（C 唔發）。S＝第二次訊號+力度A；A＝15分RSI>=60且量比>=1.5；B＝其餘過關。",
        "買入/SL/TP 係參考位。觀察名單，唔係買賣指令。",
        "",
    ]
    lines.extend(_format_rows(rows, _site_day_line))
    return "\n".join(lines)


def format_swing_from_site(payload: dict) -> str:
    rows = _listed_site_rows(payload)
    when = payload.get("generated_at") or datetime.now(HK).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"波段 RSI 觀察  {when} HKT",
        f"同網頁同一份。掃描 {payload.get('scanned')} 隻，過關 {len(rows)} 隻。{_rank_counts(rows)}",
        "只發過關（C 唔發）。S＝第二次訊號+力度A；A＝4小時RSI>=60且日線量比>=1.5；B＝其餘過關。",
        "買入/SL/TP 係參考位。觀察名單，唔係買賣指令。",
        "",
    ]
    lines.extend(_format_rows(rows, _site_swing_line))
    return "\n".join(lines)


def build_day_alert(top: int | None = None) -> str:
    limit = configured_top(top)
    try:
        return format_day_from_site(fetch_site_scan("day", limit))
    except Exception as error:
        print(f"網頁快取攞唔到，改用本地計算：{error}", file=sys.stderr)
        rows, scanned = scan(limit)
        return format_day_alert(rows, scanned)


def build_swing_alert(top: int | None = None) -> str:
    limit = configured_top(top)
    try:
        return format_swing_from_site(fetch_site_scan("swing", limit))
    except Exception as error:
        print(f"網頁快取攞唔到，改用本地計算：{error}", file=sys.stderr)
        rows, scanned = scan_swing(limit)
        return format_swing_alert(rows, scanned)


def self_test() -> None:
    rising = [float(price) for price in range(1, 40)]
    falling = [float(price) for price in range(40, 1, -1)]
    assert rsi(rising) == 100.0
    assert rsi(falling) == 0.0
    assert rsi([10.0] * 30) == 50.0
    assert hourly_trend_up(rising[-30:])
    assert not hourly_trend_up(falling[-30:])

    first_wave = [52, 55, 58, 61, 64, 66, 68, 70, 72, 74]
    hold = [50, 61, 66, 63, 58, 56, 57, 56, 57, 57]
    second = [52, 55, 61, 66, 63, 58, 56, 57, 59, 61]
    broken = [62, 64, 60, 55, 52, 48, 46, 44, 47, 49]
    assert session_note(first_wave)[1] is False
    assert "唔追" in session_note(first_wave)[0]
    assert "守住" in session_note(hold)[0]
    assert session_note(second) == ("回拉守住後再企上60，第二次訊號", True)
    assert "剔除" in session_note(broken)[0]
    assert grade_bar(64, 1.8) == "A"
    assert grade_bar(64, 1.1) == "B"
    assert grade_bar(55, 2.0) == "B"
    assert grade_bar(45, 3.0) is None
    assert daily_trend_up(rising[-30:])
    assert not daily_trend_up(falling[-30:])
    assert grade_swing(64, 1.5) == "A"
    assert grade_swing(64, 1.4) == "B"
    assert grade_swing(64, 1.0) == "B"
    assert grade_swing(45, 2.0) is None
    assert swing_note(second)[1] is True
    assert "剔除" in swing_note(broken)[0]

    rising_candles = [
        Candle(high=price + 0.4, low=max(price - 0.4, 0.1), close=price, volume=1.0)
        for price in rising
    ]
    chased = build_long_plan(rising_candles, rising_candles[-1].close, False)
    assert chased is not None
    assert chased.wait_pullback
    assert chased.stop_loss < chased.entry < chased.take_profit_1 <= chased.take_profit_2
    assert chased.risk_reward > 2.0
    signal = build_long_plan(rising_candles, rising_candles[-1].close, True)
    assert signal is not None
    assert not signal.wait_pullback
    assert signal.entry == rising_candles[-1].close
    assert signal.stop_loss < signal.entry
    assert signal.risk_reward > 2.0
    assert day_rank(True, "A", True, signal) == "S"
    assert day_rank(True, "A", False, signal) == "A"
    assert day_rank(True, "B", True, signal) == "B"
    assert day_rank(False, "A", True, signal) == "C"
    sample = [
        {"rank": "B", "second_signal": False},
        {"rank": "S", "second_signal": True},
        {"rank": "A", "second_signal": False},
        {"rank": "C", "second_signal": False},
    ]
    ordered = _sort_passed(sample)
    assert [row["rank"] for row in ordered] == ["S", "A", "B"]
    assert "S（第二次訊號 + A）" in "\n".join(_format_rows(sample, lambda row: row["rank"]))
    print("self-test ok")


if __name__ == "__main__":
    self_test()
