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
    plan: LongPlan


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
    plan: LongPlan


def configured_top(override: int | None = None) -> int:
    """同網頁版一樣，只掃 10–40 隻，避免打爆 Binance。"""
    if override is None:
        raw = os.getenv("RSI_TOP", "30").strip()
        try:
            top = int(raw)
        except ValueError:
            return 30
    else:
        top = override
    return min(40, max(10, top))


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
    """做多參考位。只喺 R/R > min_rr 先建議；唔夠就重算 TP／SL。"""
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
    recent_high = max(candle.high for candle in recent)
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

    take_profit_1 = entry + risk * target_rr
    take_profit_2 = entry + risk * max(target_rr + 1.0, 3.0)
    if recent_high > entry:
        take_profit_2 = max(take_profit_1, min(take_profit_2, recent_high * 1.02))
    if take_profit_2 < take_profit_1:
        take_profit_2 = take_profit_1

    reward = take_profit_1 - entry
    risk_reward = reward / risk if risk > 0 else 0.0
    if risk_reward <= min_rr:
        needed_risk = reward / target_rr if reward > 0 else risk
        tighter = entry - needed_risk
        if tighter <= stop_loss or tighter >= entry:
            return None
        if tighter < risk_floor:
            take_profit_1 = entry + risk * target_rr
            take_profit_2 = max(take_profit_1, entry + risk * (target_rr + 1.0))
        else:
            stop_loss = tighter
            risk = entry - stop_loss
            take_profit_1 = entry + risk * target_rr
            take_profit_2 = entry + risk * max(target_rr + 1.0, 3.0)
        reward = take_profit_1 - entry
        risk_reward = reward / risk if risk > 0 else 0.0

    if risk <= 0 or risk_reward <= min_rr:
        return None

    return LongPlan(
        entry=entry,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        wait_pullback=wait_pullback,
        risk_reward=round(risk_reward, 2),
        risk_pct=round((risk / entry) * 100.0, 2),
        reward_pct=round((reward / entry) * 100.0, 2),
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
    if plan is None:
        return None

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
    )


def daily_trend_up(closes: list[float]) -> bool:
    if len(closes) < 25:
        return False
    average = ema(closes, 20)
    if average is None:
        return False
    return closes[-1] > average and closes[-1] > closes[-6]


def grade_swing(rsi_4h: float, daily_volume_ratio: float) -> str | None:
    if rsi_4h < 50:
        return None
    if rsi_4h >= 60 and daily_volume_ratio >= 1.2:
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
    if second_signal and daily_volume < 1.2:
        note = "回拉後企穩，但日線量未放大，縮注或者再確認"
    elif grade == "B" and rsi_4h >= 60:
        note = f"{note}；力度有但日線量未放大，降做B"

    plan = build_long_plan(four_hour, four_closes[-1], second_signal, 0.12)
    if plan is None:
        return None

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
    rows.sort(key=lambda row: (not row.second_signal, row.grade != "A", -row.rsi_15m))
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
    rows.sort(key=lambda row: (not row.second_signal, row.grade != "A", -row.rsi_4h))
    return rows, len(symbols)


def format_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.6f}"


def _format_rows(rows: list[ScanRow] | list[SwingRow], line_for_row) -> list[str]:
    if not rows:
        return ["今次冇標的過關。"]
    signal_rows = [row for row in rows if row.second_signal]
    watch_rows = [row for row in rows if not row.second_signal]
    lines = [f"第二次訊號：{len(signal_rows)} 隻"]
    if signal_rows:
        lines.extend(line_for_row(row) for row in signal_rows)
    else:
        lines.append("暫時冇。下面只係強勢觀察，未過回拉再企上 60。")
    if watch_rows:
        lines.append("")
        lines.append(f"觀察：{len(watch_rows)} 隻")
        lines.extend(line_for_row(row) for row in watch_rows)
    return lines


def _pct(entry: float, price: float) -> float:
    return ((price - entry) / entry) * 100.0


def _plan_lines(plan: LongPlan) -> str:
    label = "建議買入（等回拉）" if plan.wait_pullback else "建議買入"
    return (
        f"   {label}: {format_price(plan.entry)}\n"
        f"   SL: {format_price(plan.stop_loss)} ({_pct(plan.entry, plan.stop_loss):+.2f}%)\n"
        f"   TP1: {format_price(plan.take_profit_1)} ({_pct(plan.entry, plan.take_profit_1):+.2f}%) | "
        f"TP2: {format_price(plan.take_profit_2)} ({_pct(plan.entry, plan.take_profit_2):+.2f}%)\n"
        f"   R/R {plan.risk_reward} · 風險 {plan.risk_pct}% · 目標 +{plan.reward_pct}%"
    )


def _day_line(row: ScanRow) -> str:
    return (
        f"{row.symbol}  {row.grade}級  ${format_price(row.last_price)}\n"
        f"   日RSI {row.daily_rsi:.1f} | 1h {row.hourly_rsi:.1f} | "
        f"15m {row.rsi_15m:.1f} | 量比 {row.volume_ratio:.2f}\n"
        f"   {row.note}\n"
        f"{_plan_lines(row.plan)}"
    )


def _swing_line(row: SwingRow) -> str:
    return (
        f"{row.symbol}  {row.grade}級  ${format_price(row.last_price)}\n"
        f"   日RSI {row.daily_rsi:.1f} | 4h {row.rsi_4h:.1f} | 量比 {row.volume_ratio:.2f}\n"
        f"   {row.note}\n"
        f"{_plan_lines(row.plan)}"
    )


def format_day_alert(rows: list[ScanRow], scanned: int) -> str:
    now = datetime.now(HK).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"日內 RSI 觀察  {now} HKT",
        f"掃描成交額最高 {scanned} 隻，過關 {len(rows)} 隻。",
        "條件：日線RSI>=50、1小時向上、15分RSI>=50。A＝15分RSI>=60 且量比>=1.5。",
        "買入/SL/TP 係參考位。第二次訊號先用現價，否則等回拉到 1 小時 EMA20。",
        "觀察名單，唔係買賣指令。",
        "",
    ]
    lines.extend(_format_rows(rows, _day_line))
    return "\n".join(lines)


def format_swing_alert(rows: list[SwingRow], scanned: int) -> str:
    now = datetime.now(HK).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"波段 RSI 觀察  {now} HKT",
        f"掃描成交額最高 {scanned} 隻，過關 {len(rows)} 隻。",
        "條件：日線RSI>=50、日線同4小時向上。A＝4小時RSI>=60 且日線量比>=1.2。",
        "買入/SL/TP 係參考位。第二次訊號先用現價，否則等回拉到 4 小時 EMA20。",
        "觀察名單，唔係買賣指令。",
        "",
    ]
    lines.extend(_format_rows(rows, _swing_line))
    return "\n".join(lines)


def build_day_alert(top: int | None = None) -> str:
    limit = configured_top(top)
    rows, scanned = scan(limit)
    return format_day_alert(rows, scanned)


def build_swing_alert(top: int | None = None) -> str:
    limit = configured_top(top)
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
    assert grade_swing(64, 1.3) == "A"
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
    print("self-test ok")


if __name__ == "__main__":
    self_test()
