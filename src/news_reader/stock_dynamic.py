#!/usr/bin/env python3
"""Tkinter stock quote window with live cards and OHLC candlestick charts."""

from __future__ import annotations

import argparse
import csv
import io
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Sequence
import urllib.request
import urllib.parse
import urllib.error
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageColor, ImageOps, ImageTk

from news_reader.cli_types import positive_int
from news_reader.market_ai import MarketAiWindow
from news_reader.stock_monitor import (
    INDICES,
    YAHOO_INDICES,
    EtfQuote,
    IndexQuote,
    fetch_market_data,
    fetch_twse_json,
    first_level,
    make_context,
    to_float,
    to_int,
)


CHART_COLORS = {
    "加權指數": "#2563eb",
    "櫃買指數": "#7c3aed",
    "電子類指數": "#0891b2",
    "金融保險類指數": "#ca8a04",
    "S&P 500": "#2563eb",
    "道瓊工業": "#7c3aed",
    "NASDAQ": "#0891b2",
    "費城半導體": "#ca8a04",
    "台指近期": "#dc2626",
    "小型台指近期": "#2563eb",
    "電子近期": "#0d9488",
    "金融近期": "#d97706",
}

YAHOO_SYMBOL_BY_NAME = {name: symbol for symbol, name in YAHOO_INDICES.items()}
MA_PERIODS = (5, 10, 20)

US_INDEX_SYMBOLS = {
    "S&P 500": "^GSPC",
    "道瓊工業": "^DJI",
    "NASDAQ": "^IXIC",
    "費城半導體": "^SOX",
}
TAIFEX_FRONT_MONTHS = {
    "台指近期": "TX",
    "小型台指近期": "MTX",
    "電子近期": "TE",
    "金融近期": "TF",
}
YAHOO_TW_FUTURE_SYMBOLS = {
    "台指近期": "WTX&",
    "小型台指近期": "WMT&",
    "電子近期": "WTE&",
    "金融近期": "WTF&",
}
US_CHART_NAMES = list(US_INDEX_SYMBOLS)
TAIWAN_FUTURE_CHART_NAMES = list(YAHOO_TW_FUTURE_SYMBOLS)
TAIWAN_ADR_SYMBOLS = {
    "TSM": "台積電 ADR",
    "UMC": "聯電 ADR",
    "ASX": "日月光投控 ADR",
    "CHT": "中華電信 ADR",
    "AUOTY": "友達 ADR",
    "BLTE": "Belite Bio ADS",
    "IMOS": "南茂科技 ADS",
    "HIMX": "奇景光電 ADS",
    "SIMO": "慧榮科技 ADS",
    "VITCY": "威盛科技 ADR（OTC）",
}
DEFAULT_US_STOCKS = {
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "TSLA": "Tesla",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "NFLX": "Netflix",
    "COST": "Costco",
}
DEFAULT_TAIWAN_STOCKS = {
    "2330": "台積電",
    "2317": "鴻海",
    "2454": "聯發科",
    "2308": "台達電",
    "2382": "廣達",
    "2303": "聯電",
    "3711": "日月光投控",
    "2412": "中華電",
    "2881": "富邦金",
    "2882": "國泰金",
    "2891": "中信金",
    "3231": "緯創",
}
TAIFEX_HISTORY_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
YAHOO_TW_CHART_URL = (
    "https://tw.stock.yahoo.com/_td-stock/api/resource/"
    "FinanceChartService.ApacLibraCharts"
)

ETF_LIST_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
ETF_PRODUCTS_URL = "https://www.twse.com.tw/zh/ETFortune/ajaxProductsResult"
INSTITUTION_TRADES_URL = "https://www.twse.com.tw/fund/BFI82U?response=json"
TWSE_BREADTH_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=MS"
TPEX_BREADTH_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainborad_highlight"
ETF_CODE_PATTERN = re.compile(r"^00[0-9A-Z]+$")
ETF_BATCH_SIZE = 45

THEMES = {
    "night": {
        "label": "深夜",
        "bg": "#111827",
        "surface": "#1f2937",
        "surface_alt": "#111827",
        "text": "#f8fafc",
        "muted": "#a6b0c3",
        "line": "#334155",
        "grid": "#2b3545",
        "up": "#f87171",
        "down": "#34d399",
        "up_fill": "#3f1f26",
        "down_fill": "#14362b",
        "accent": "#60a5fa",
        "ma5": "#38bdf8",
        "ma10": "#a78bfa",
        "ma20": "#fb923c",
        "selected": "#334155",
    },
}

DASHBOARD_BACKGROUND_MODES = {
    "填滿": "fill",
    "符合": "fit",
    "延伸": "stretch",
    "並排": "tile",
    "置中": "center",
}


@dataclass(frozen=True)
class MarketCandle:
    timestamp: int
    label: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class SecurityQuote:
    symbol: str
    name: str
    price: float | None
    previous_close: float | None
    open_price: float | None
    high: float | None
    low: float | None
    volume: int | None
    market_time: str
    market_timestamp: int | None = None
    source: str = "Yahoo Finance"

    @property
    def change(self) -> float | None:
        if self.price is None or self.previous_close is None:
            return None
        return self.price - self.previous_close

    @property
    def change_percent(self) -> float | None:
        if self.change is None or not self.previous_close:
            return None
        return self.change / self.previous_close * 100


def moving_average(candles: list[MarketCandle], period: int) -> list[float | None]:
    values: list[float | None] = []
    total = 0.0
    closes: list[float] = []
    for candle in candles:
        closes.append(candle.close)
        total += candle.close
        if len(closes) > period:
            total -= closes[-period - 1]
        values.append(total / period if len(closes) >= period else None)
    return values


def parse_yahoo_candles(result: dict, limit: int, intraday: bool) -> list[MarketCandle]:
    timestamps = result.get("timestamp") or []
    quote_sets = result.get("indicators", {}).get("quote") or []
    if not quote_sets:
        return []
    quote = quote_sets[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    candles: list[MarketCandle] = []
    for index, timestamp in enumerate(timestamps):
        try:
            values = (
                float(opens[index]),
                float(highs[index]),
                float(lows[index]),
                float(closes[index]),
            )
        except (IndexError, TypeError, ValueError):
            continue
        local_time = datetime.fromtimestamp(timestamp)
        label = local_time.strftime("%m/%d %H:%M" if intraday else "%Y-%m-%d")
        candles.append(MarketCandle(int(timestamp), label, *values))
    return candles[-max(2, limit):]


def aggregate_daily_candles(
    candles: list[MarketCandle],
    limit: int,
) -> list[MarketCandle]:
    grouped: dict[str, list[MarketCandle]] = {}
    for candle in candles:
        date_label = datetime.fromtimestamp(candle.timestamp).strftime("%Y-%m-%d")
        grouped.setdefault(date_label, []).append(candle)
    daily: list[MarketCandle] = []
    for date_label, rows in grouped.items():
        rows.sort(key=lambda item: item.timestamp)
        daily.append(
            MarketCandle(
                rows[0].timestamp,
                date_label,
                rows[0].open,
                max(item.high for item in rows),
                min(item.low for item in rows),
                rows[-1].close,
            )
        )
    daily.sort(key=lambda item: item.timestamp)
    return daily[-max(2, limit):]


def is_taiwan_market_open(now: datetime | None = None) -> bool:
    current = now or datetime.now()
    return (
        current.weekday() < 5
        and clock_time(9, 0) <= current.time() <= clock_time(13, 35)
    )


def chart_mode_for_time(now: datetime | None = None) -> str:
    return "intraday" if is_taiwan_market_open(now) else "daily"


def chart_mode_for_market_time(
    api_time: str,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now()
    mode = chart_mode_for_time(current)
    if mode != "intraday":
        return mode
    date_match = re.search(r"\b(\d{8})\b", api_time)
    if date_match and date_match.group(1) != current.strftime("%Y%m%d"):
        return "daily"
    return mode


def us_eastern_timezone(current: datetime) -> timezone | ZoneInfo:
    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        current_utc = current.astimezone(timezone.utc)
        year = current_utc.year

        march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
        first_march_sunday = 1 + (6 - march_first.weekday()) % 7
        second_march_sunday = first_march_sunday + 7
        dst_start = datetime(
            year,
            3,
            second_march_sunday,
            7,
            tzinfo=timezone.utc,
        )

        november_first = datetime(year, 11, 1, tzinfo=timezone.utc)
        first_november_sunday = 1 + (6 - november_first.weekday()) % 7
        dst_end = datetime(
            year,
            11,
            first_november_sunday,
            6,
            tzinfo=timezone.utc,
        )
        offset_hours = -4 if dst_start <= current_utc < dst_end else -5
        return timezone(timedelta(hours=offset_hours))


def is_us_market_open(now: datetime | None = None) -> bool:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    eastern = current.astimezone(us_eastern_timezone(current))
    return (
        eastern.weekday() < 5
        and clock_time(9, 30) <= eastern.time().replace(tzinfo=None) <= clock_time(16, 5)
    )


def chart_mode_for_us_time(now: datetime | None = None) -> str:
    return "intraday" if is_us_market_open(now) else "daily"


def chart_mode_for_us_quotes(
    quotes: list[SecurityQuote],
    now: datetime | None = None,
) -> str:
    current = now or datetime.now().astimezone()
    mode = chart_mode_for_us_time(current)
    if mode != "intraday":
        return mode
    eastern_zone = us_eastern_timezone(current)
    expected_date = current.astimezone(eastern_zone).date()
    quote_dates = {
        datetime.fromtimestamp(quote.market_timestamp, eastern_zone).date()
        for quote in quotes
        if quote.market_timestamp
    }
    if quote_dates and expected_date not in quote_dates:
        return "daily"
    return mode


def fetch_yahoo_candles(
    symbol: str,
    timeout: int,
    retries: int,
    limit: int,
    prefer_intraday: bool | None = None,
) -> tuple[list[MarketCandle], str]:
    encoded = urllib.parse.quote(symbol, safe="")
    attempts = max(0, retries) + 1
    last_error: Exception | None = None
    intraday = is_taiwan_market_open() if prefer_intraday is None else prefer_intraday
    plans = (
        [("5d", "5m", "5 分 K", 8, False)]
        if intraday
        else [
            ("6mo", "1d", "日 K", min(20, limit), False),
            ("1mo", "30m", "日 K", min(10, limit), True),
        ]
    )
    for range_name, interval, grain, minimum, aggregate_daily in plans:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{encoded}?range={range_name}&interval={interval}"
        )
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "close"},
        )
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                results = payload.get("chart", {}).get("result") or []
                if not results:
                    raise RuntimeError(
                        payload.get("chart", {}).get("error")
                        or "Yahoo historical chart returned no result"
                    )
                candles = parse_yahoo_candles(
                    results[0],
                    limit * 20 if aggregate_daily else limit,
                    intraday=interval != "1d",
                )
                if aggregate_daily:
                    candles = aggregate_daily_candles(candles, limit)
                if len(candles) >= minimum:
                    return candles, grain
            except (OSError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.35 * (attempt + 1))
    if last_error:
        raise RuntimeError(f"Yahoo 歷史 K 線資料抓取失敗：{last_error}")
    raise RuntimeError("Yahoo 歷史 K 線資料不足")


def fetch_yahoo_tw_chart_items(
    symbols: Sequence[str],
    timeout: int,
    retries: int,
    period: str = "1m",
    range_name: str = "1d",
) -> dict[str, dict]:
    """Fetch Yahoo Taiwan charts in bounded batches and return them by symbol."""
    unique_symbols = list(dict.fromkeys(symbols))
    if not unique_symbols:
        return {}
    attempts = max(0, retries) + 1
    chunks = [
        unique_symbols[index:index + 10]
        for index in range(0, len(unique_symbols), 10)
    ]

    def fetch_chunk(chunk: list[str]) -> list[dict]:
        encoded_symbols = urllib.parse.quote(
            json.dumps(chunk, ensure_ascii=False, separators=(",", ":")),
            safe="",
        )
        resource = (
            f"{YAHOO_TW_CHART_URL};autoRefresh={int(time.time() * 1000)}"
            f";period={period};range={range_name}"
            f";symbols={encoded_symbols};type=null"
        )
        query = urllib.parse.urlencode(
            {
                "device": "desktop",
                "ecma": "modern",
                "intl": "tw",
                "lang": "zh-Hant-TW",
                "region": "TW",
                "site": "finance",
                "tz": "Asia/Taipei",
                "returnMeta": "true",
            }
        )
        request = urllib.request.Request(
            f"{resource}?{query}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://tw.stock.yahoo.com/",
                "Cache-Control": "no-cache",
                "Connection": "close",
            },
        )
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return payload.get("data") or []
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in {400, 404}:
                    break
            except (OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.3 * (attempt + 1))
        raise RuntimeError(f"Yahoo 台灣批次行情載入失敗：{last_error}")

    def fetch_resilient_chunk(chunk: list[str]) -> list[dict]:
        try:
            return fetch_chunk(chunk)
        except RuntimeError:
            if len(chunk) <= 1:
                return []
            middle = len(chunk) // 2
            return [
                *fetch_resilient_chunk(chunk[:middle]),
                *fetch_resilient_chunk(chunk[middle:]),
            ]

    items: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as executor:
        futures = [
            executor.submit(fetch_resilient_chunk, chunk)
            for chunk in chunks
        ]
        for future in as_completed(futures):
            rows = future.result()
            for item in rows:
                symbol = item.get("symbol")
                if symbol:
                    items[symbol] = item
    if not items:
        raise RuntimeError("Yahoo 台灣批次行情未回傳任何資料")
    return items


def security_quote_from_yahoo_tw_item(
    requested_symbol: str,
    display_name: str,
    item: dict | None,
) -> SecurityQuote:
    chart = (item or {}).get("chart") or {}
    meta = chart.get("meta") or {}
    quote = chart.get("quote") or {}
    price = to_float(str(quote.get("price") or ""))
    refreshed_text = str(quote.get("refreshedTs") or "")
    timestamp: int | None = None
    if refreshed_text and refreshed_text != "-":
        try:
            timestamp = int(
                datetime.fromisoformat(
                    refreshed_text.replace("Z", "+00:00")
                ).timestamp()
            )
        except ValueError:
            pass
    if timestamp is None and meta.get("regularMarketTime"):
        timestamp = int(meta["regularMarketTime"])
    market_time = (
        datetime.fromtimestamp(timestamp).strftime("%m/%d %H:%M:%S")
        if timestamp
        else "--"
    )
    symbol = requested_symbol
    if "." in requested_symbol:
        base = requested_symbol.split(".", 1)[0]
        exchange = str(meta.get("exchange") or "")
        if exchange == "TWO":
            symbol = f"{base}.TWO"
        elif exchange in {"TAI", "TWSE"}:
            symbol = f"{base}.TW"
    return SecurityQuote(
        symbol=symbol,
        name=display_name or str(meta.get("name") or requested_symbol),
        price=price,
        previous_close=to_float(
            str(
                quote.get("previousClose")
                or meta.get("chartPreviousClose")
                or meta.get("previousClose")
                or ""
            )
        ),
        open_price=to_float(str(quote.get("openPrice") or "")),
        high=to_float(str(quote.get("dayHighPrice") or "")),
        low=to_float(str(quote.get("dayLowPrice") or "")),
        volume=to_int(str(quote.get("volume") or "")),
        market_time=market_time,
        market_timestamp=timestamp,
        source="Yahoo 股市" if price is not None else "Yahoo 股市（暫無資料）",
    )


def fetch_yahoo_security_quote(
    symbol: str,
    display_name: str,
    timeout: int,
    retries: int,
) -> SecurityQuote:
    items = fetch_yahoo_tw_chart_items([symbol], timeout, retries)
    quote = security_quote_from_yahoo_tw_item(
        symbol,
        display_name,
        items.get(symbol),
    )
    if quote.price is None:
        raise RuntimeError(f"{symbol} Yahoo 台灣行情暫無資料")
    return quote


def fetch_yahoo_security_quotes(
    symbols: dict[str, str],
    timeout: int,
    retries: int,
) -> list[SecurityQuote]:
    if not symbols:
        return []
    items = fetch_yahoo_tw_chart_items(list(symbols), timeout, retries)
    rows = {
        symbol: security_quote_from_yahoo_tw_item(
            symbol,
            name,
            items.get(symbol),
        )
        for symbol, name in symbols.items()
    }
    return [rows[symbol] for symbol in symbols]


def fetch_taiwan_security_quote(
    code: str,
    display_name: str,
    timeout: int,
    retries: int,
) -> SecurityQuote:
    requested_name = "" if display_name == code else display_name
    symbol = f"{code}.TW"
    items = fetch_yahoo_tw_chart_items([symbol], timeout, retries)
    return security_quote_from_yahoo_tw_item(
        symbol,
        requested_name,
        items.get(symbol),
    )


def fetch_taiwan_security_quotes(
    symbols: dict[str, str],
    timeout: int,
    retries: int,
) -> dict[str, SecurityQuote]:
    if not symbols:
        return {}
    requested = {
        f"{code}.TW": (code, "" if name == code else name)
        for code, name in symbols.items()
    }
    items = fetch_yahoo_tw_chart_items(list(requested), timeout, retries)
    return {
        code: security_quote_from_yahoo_tw_item(
            symbol,
            display_name,
            items.get(symbol),
        )
        for symbol, (code, display_name) in requested.items()
    }


def fetch_yahoo_tw_futures(
    symbols: dict[str, str],
    timeout: int,
    retries: int,
    limit: int,
) -> tuple[
    dict[str, list[MarketCandle]],
    dict[str, str],
    dict[str, float | None],
    list[str],
    list[str],
]:
    """Fetch Taiwan futures quotes and 5-minute candles in one Yahoo TW batch."""
    encoded_symbols = urllib.parse.quote(
        json.dumps(list(symbols.values()), ensure_ascii=False, separators=(",", ":")),
        safe="",
    )
    resource = (
        f"{YAHOO_TW_CHART_URL};autoRefresh={int(time.time() * 1000)}"
        f";period=5m;range=1d;symbols={encoded_symbols};type=null"
    )
    query = urllib.parse.urlencode(
        {
            "device": "desktop",
            "ecma": "modern",
            "intl": "tw",
            "lang": "zh-Hant-TW",
            "region": "TW",
            "site": "finance",
            "tz": "Asia/Taipei",
            "returnMeta": "true",
        }
    )
    request = urllib.request.Request(
        f"{resource}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://tw.stock.yahoo.com/future/",
            "Cache-Control": "no-cache",
            "Connection": "close",
        },
    )
    attempts = max(0, retries) + 1
    payload: dict | None = None
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(0.35 * (attempt + 1))
    if payload is None:
        raise RuntimeError(f"Yahoo 台灣期貨行情載入失敗：{last_error}")

    by_symbol = {
        item.get("symbol"): item
        for item in payload.get("data") or []
        if item.get("symbol")
    }
    histories: dict[str, list[MarketCandle]] = {}
    grains: dict[str, str] = {}
    previous_closes: dict[str, float | None] = {}
    refreshed_times: list[str] = []
    errors: list[str] = []

    for name, symbol in symbols.items():
        item = by_symbol.get(symbol)
        if not item:
            histories[name] = []
            grains[name] = "Yahoo 5 分 K（暫無資料）"
            previous_closes[name] = None
            errors.append(f"{name}：Yahoo 未回傳資料")
            continue
        chart = item.get("chart") or {}
        meta = chart.get("meta") or {}
        quote = chart.get("quote") or {}
        candles = parse_yahoo_candles(chart, limit, intraday=True)
        price = to_float(str(quote.get("price") or ""))
        previous_close = to_float(
            str(
                quote.get("previousClose")
                or meta.get("chartPreviousClose")
                or meta.get("previousClose")
                or ""
            )
        )
        refreshed_text = str(quote.get("refreshedTs") or "")
        timestamp = meta.get("regularMarketTime")
        if refreshed_text and refreshed_text != "-":
            refreshed_times.append(refreshed_text)
            try:
                timestamp = int(
                    datetime.fromisoformat(
                        refreshed_text.replace("Z", "+00:00")
                    ).timestamp()
                )
            except ValueError:
                pass

        if timestamp and candles and abs(candles[-1].timestamp - timestamp) > 43_200:
            latest_bucket = int(timestamp) - int(timestamp) % 300
            shift = latest_bucket - candles[-1].timestamp
            candles = [
                MarketCandle(
                    candle.timestamp + shift,
                    datetime.fromtimestamp(candle.timestamp + shift).strftime(
                        "%m/%d %H:%M"
                    ),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                )
                for candle in candles
            ]

        if price is not None and candles:
            latest = candles[-1]
            candles[-1] = MarketCandle(
                latest.timestamp,
                latest.label,
                latest.open,
                max(latest.high, price),
                min(latest.low, price),
                price,
            )
        elif price is not None:
            open_price = to_float(str(quote.get("openPrice") or "")) or price
            high = to_float(str(quote.get("dayHighPrice") or "")) or price
            low = to_float(str(quote.get("dayLowPrice") or "")) or price
            moment = datetime.fromtimestamp(timestamp) if timestamp else datetime.now()
            candles = [
                MarketCandle(
                    int(moment.timestamp()),
                    moment.strftime("%m/%d %H:%M"),
                    open_price,
                    max(high, open_price, price),
                    min(low, open_price, price),
                    price,
                )
            ]

        histories[name] = candles[-max(2, limit):]
        grains[name] = (
            "Yahoo 5 分 K"
            if candles
            else "Yahoo 5 分 K（暫無成交）"
        )
        previous_closes[name] = previous_close
        if not candles:
            errors.append(f"{name}：目前沒有盤中成交資料")

    return histories, grains, previous_closes, refreshed_times, errors


def fetch_taifex_front_month_candles(
    contract: str,
    timeout: int,
    retries: int,
    verify_ssl: bool,
    limit: int,
) -> tuple[list[MarketCandle], str]:
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=30)
    form = urllib.parse.urlencode(
        {
            "down_type": "1",
            "queryStartDate": start_date.strftime("%Y/%m/%d"),
            "queryEndDate": end_date.strftime("%Y/%m/%d"),
            "commodity_id": contract,
            "commodity_id2": "",
        }
    ).encode()
    request = urllib.request.Request(
        TAIFEX_HISTORY_URL,
        data=form,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.taifex.com.tw/cht/3/futDailyMarketView",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    attempts = max(0, retries) + 1
    raw = b""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=make_context(verify_ssl),
            ) as response:
                raw = response.read()
            break
        except OSError as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(0.35 * (attempt + 1))
    if not raw:
        raise RuntimeError(f"TAIFEX {contract} 近月資料下載失敗：{last_error}")
    text = raw.decode("cp950", errors="replace")
    rows = csv.DictReader(io.StringIO(text))
    grouped: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        if (row.get("契約") or "").strip() != contract:
            continue
        date_text = (row.get("交易日期") or "").strip()
        month = (row.get("到期月份(週別)") or "").strip()
        values = [
            to_float(row.get("開盤價")),
            to_float(row.get("最高價")),
            to_float(row.get("最低價")),
            to_float(row.get("收盤價")),
        ]
        if not date_text or not re.fullmatch(r"\d{6}", month) or any(
            value is None for value in values
        ):
            continue
        grouped[date_text][month].append(row)

    candles: list[MarketCandle] = []
    latest_month = ""
    for date_text in sorted(grouped):
        months = sorted(grouped[date_text])
        if not months:
            continue
        front_month = months[0]
        sessions = grouped[date_text][front_month]
        night = [row for row in sessions if "盤後" in (row.get("交易時段") or "")]
        regular = [row for row in sessions if "一般" in (row.get("交易時段") or "")]
        ordered = [*night, *regular] or sessions
        opens = [to_float(row.get("開盤價")) for row in ordered]
        highs = [to_float(row.get("最高價")) for row in ordered]
        lows = [to_float(row.get("最低價")) for row in ordered]
        closes = [to_float(row.get("收盤價")) for row in ordered]
        if any(value is None for value in [*opens, *highs, *lows, *closes]):
            continue
        local_date = datetime.strptime(date_text, "%Y/%m/%d")
        candles.append(
            MarketCandle(
                int(local_date.timestamp()),
                local_date.strftime("%Y-%m-%d"),
                opens[0],
                max(highs),
                min(lows),
                closes[-1],
            )
        )
        latest_month = front_month
    if len(candles) < 2:
        raise RuntimeError(f"TAIFEX {contract} 近月歷史資料不足")
    return candles[-max(2, limit):], f"{latest_month} 近月日 K"


def draw_candlestick_plot(
    canvas: tk.Canvas,
    theme: dict[str, str],
    candles: list[MarketCandle],
    previous_close: float | None,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> None:
    plot_w = max(1.0, right - left)
    plot_h = max(1.0, bottom - top)
    if not candles:
        canvas.create_text(
            (left + right) / 2,
            (top + bottom) / 2,
            text="正在載入歷史 K 線…",
            fill=theme["muted"],
            font=("Microsoft JhengHei UI", 10),
        )
        return

    averages = {
        period: moving_average(candles, period)
        for period in MA_PERIODS
    }
    scale_values = [value for candle in candles for value in (candle.high, candle.low)]
    scale_values.extend(
        value
        for values in averages.values()
        for value in values
        if value is not None
    )
    if previous_close is not None:
        scale_values.append(previous_close)
    low = min(scale_values)
    high = max(scale_values)
    padding = (high - low) * 0.08 or max(abs(high) * 0.001, 0.1)
    low -= padding
    high += padding
    span = high - low or 1.0

    def y_for(value: float) -> float:
        return top + (high - value) / span * plot_h

    for band in range(4):
        band_left = left + band * plot_w / 4
        band_right = left + (band + 1) * plot_w / 4
        if band % 2 == 0:
            canvas.create_rectangle(
                band_left,
                top,
                band_right,
                bottom,
                fill=theme["surface"],
                outline="",
            )

    for index in range(4):
        y = top + index * plot_h / 3
        value = high - index * span / 3
        canvas.create_line(left, y, right, y, fill=theme["grid"])
        canvas.create_text(
            right + 7,
            y,
            text=fmt_number(value),
            fill=theme["muted"],
            anchor="w",
            font=("Segoe UI", 8),
        )
    canvas.create_line(left, bottom, right, bottom, fill=theme["line"])

    if previous_close is not None and low <= previous_close <= high:
        baseline_y = y_for(previous_close)
        canvas.create_line(
            left,
            baseline_y,
            right,
            baseline_y,
            fill=theme["muted"],
            dash=(4, 4),
        )
        canvas.create_text(
            left + 4,
            baseline_y - 8,
            text=f"昨收 {fmt_number(previous_close)}",
            fill=theme["muted"],
            anchor="w",
            font=("Microsoft JhengHei UI", 8),
        )

    step = plot_w / max(1, len(candles))
    body_width = max(2.0, min(9.0, step * 0.64))
    for index, candle in enumerate(candles):
        x = left + (index + 0.5) * step
        rising = candle.close >= candle.open
        candle_color = theme["up"] if rising else theme["down"]
        canvas.create_line(
            x,
            y_for(candle.high),
            x,
            y_for(candle.low),
            fill=candle_color,
            width=1,
        )
        open_y = y_for(candle.open)
        close_y = y_for(candle.close)
        body_top = min(open_y, close_y)
        body_bottom = max(open_y, close_y)
        if body_bottom - body_top < 1.5:
            body_bottom = body_top + 1.5
        canvas.create_rectangle(
            x - body_width / 2,
            body_top,
            x + body_width / 2,
            body_bottom,
            fill=candle_color,
            outline=candle_color,
        )

    for period in MA_PERIODS:
        coords: list[float] = []
        for index, value in enumerate(averages[period]):
            if value is None:
                continue
            coords.extend(
                [
                    left + (index + 0.5) * step,
                    y_for(value),
                ]
            )
        if len(coords) >= 4:
            canvas.create_line(
                *coords,
                fill=theme[f"ma{period}"],
                width=1.6,
                smooth=True,
            )

    label_indices = sorted({0, len(candles) // 3, len(candles) * 2 // 3, len(candles) - 1})
    for index in label_indices:
        x = left + (index + 0.5) * step
        canvas.create_text(
            x,
            bottom + 12,
            text=(
                candles[index].label[5:]
                if "-" in candles[index].label
                else candles[index].label
            ),
            fill=theme["muted"],
            anchor="n",
            font=("Segoe UI", 7),
        )

    high_index = max(range(len(candles)), key=lambda index: candles[index].high)
    high_candle = candles[high_index]
    high_x = left + (high_index + 0.5) * step
    high_y = y_for(high_candle.high)
    canvas.create_text(
        high_x,
        max(top + 8, high_y - 9),
        text=fmt_number(high_candle.high),
        fill=theme["up"],
        anchor="s",
        font=("Segoe UI", 8, "bold"),
    )

    latest = candles[-1].close
    latest_y = y_for(latest)
    latest_color = theme["up"] if latest >= candles[-1].open else theme["down"]
    label = fmt_number(latest)
    label_width = max(48, 7 * len(label))
    canvas.create_rectangle(
        right + 4,
        latest_y - 9,
        right + 4 + label_width,
        latest_y + 9,
        fill=latest_color,
        outline=latest_color,
    )
    canvas.create_text(
        right + 8,
        latest_y,
        text=label,
        fill="#ffffff",
        anchor="w",
        font=("Segoe UI", 8, "bold"),
    )


def fmt_number(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:,.{digits}f}"


def fmt_signed(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:+,.{digits}f}"


def trend_color(theme: dict[str, str], latest: float | None, previous_close: float | None) -> str:
    if latest is None or previous_close is None:
        return theme["accent"]
    return theme["up"] if latest >= previous_close else theme["down"]


def trend_fill(theme: dict[str, str], latest: float | None, previous_close: float | None) -> str:
    if latest is None or previous_close is None:
        return theme["selected"]
    return theme["up_fill"] if latest >= previous_close else theme["down_fill"]


def pct_text(latest: float | None, previous_close: float | None) -> str:
    if latest is None or not previous_close:
        return "--"
    change = latest - previous_close
    pct = change / previous_close * 100
    return f"{fmt_signed(change)} ({fmt_signed(pct)}%)"


@dataclass(frozen=True)
class EtfMarketRow:
    code: str
    name: str
    price: float | None
    previous_close: float | None
    open_price: float | None
    high: float | None
    low: float | None
    volume: int | None
    trade_value: int | None
    transaction: int | None
    market_time: str
    source: str
    market_value: float | None = None
    listing_date: str = ""
    index_name: str = ""
    issuer: str = ""
    holders: int | None = None
    value_ytd: float | None = None
    volume_ytd: int | None = None

    @property
    def change(self) -> float | None:
        if self.price is None or self.previous_close is None:
            return None
        return self.price - self.previous_close

    @property
    def change_percent(self) -> float | None:
        if self.change is None or not self.previous_close:
            return None
        return self.change / self.previous_close * 100


def security_quote_to_market_row(
    code: str,
    quote: SecurityQuote,
) -> EtfMarketRow:
    return EtfMarketRow(
        code=code,
        name=quote.name,
        price=quote.price,
        previous_close=quote.previous_close,
        open_price=quote.open_price,
        high=quote.high,
        low=quote.low,
        volume=quote.volume,
        trade_value=None,
        transaction=None,
        market_time=quote.market_time,
        source=quote.source,
    )


@dataclass(frozen=True)
class InstitutionTrade:
    name: str
    buy_amount: int
    sell_amount: int
    net_amount: int


@dataclass(frozen=True)
class InstitutionSnapshot:
    market_date: str
    rows: list[InstitutionTrade]


@dataclass(frozen=True)
class MarketBreadth:
    twse_date: str
    tpex_date: str
    twse_up: int
    twse_flat: int
    twse_down: int
    tpex_up: int
    tpex_flat: int
    tpex_down: int

    @property
    def totals(self) -> tuple[int, int, int]:
        return (
            self.twse_up + self.tpex_up,
            self.twse_flat + self.tpex_flat,
            self.twse_down + self.tpex_down,
        )


def parse_amount(value: str | int | None) -> int:
    if value is None:
        return 0
    return int(str(value).replace(",", ""))


def fetch_json(url: str, timeout: int, verify_ssl: bool) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=make_context(verify_ssl),
    ) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def fetch_institution_trades(timeout: int, verify_ssl: bool) -> InstitutionSnapshot:
    payload = fetch_json(INSTITUTION_TRADES_URL, timeout, verify_ssl)
    if not isinstance(payload, dict):
        raise RuntimeError("TWSE 三大法人資料格式錯誤")

    if payload.get("stat") != "OK":
        raise RuntimeError(payload.get("stat") or "TWSE 三大法人資料格式錯誤")

    raw_rows = {
        row[0]: tuple(parse_amount(value) for value in row[1:4])
        for row in payload.get("data", [])
        if len(row) >= 4
    }
    foreign = raw_rows.get("外資及陸資(不含外資自營商)", (0, 0, 0))
    trust = raw_rows.get("投信", (0, 0, 0))
    dealer_parts = [
        raw_rows.get("自營商(自行買賣)", (0, 0, 0)),
        raw_rows.get("自營商(避險)", (0, 0, 0)),
    ]
    dealer = tuple(sum(part[index] for part in dealer_parts) for index in range(3))
    rows = [
        InstitutionTrade("外資及陸資", *foreign),
        InstitutionTrade("投信", *trust),
        InstitutionTrade("自營商", *dealer),
    ]
    return InstitutionSnapshot(payload.get("date", ""), rows)


def parse_breadth_count(value: str | int | None) -> int:
    text = str(value or "0").split("(", 1)[0]
    return parse_amount(text)


def roc_date_to_iso(value: str) -> str:
    if len(value) != 7 or not value.isdigit():
        return value
    return f"{int(value[:3]) + 1911:04d}{value[3:]}"


def fetch_market_breadth(timeout: int, verify_ssl: bool) -> MarketBreadth:
    twse_payload = fetch_json(TWSE_BREADTH_URL, timeout, verify_ssl)
    tpex_payload = fetch_json(TPEX_BREADTH_URL, timeout, verify_ssl)
    if not isinstance(twse_payload, dict) or twse_payload.get("stat") != "OK":
        raise RuntimeError("TWSE 上市漲跌家數載入失敗")
    if not isinstance(tpex_payload, list) or not tpex_payload:
        raise RuntimeError("TPEx 上櫃漲跌家數載入失敗")

    breadth_table = next(
        (
            table
            for table in twse_payload.get("tables", [])
            if table.get("title") == "漲跌證券數合計"
        ),
        None,
    )
    if not breadth_table:
        raise RuntimeError("TWSE 上市漲跌家數格式錯誤")
    twse_rows = {row[0]: row for row in breadth_table.get("data", []) if len(row) >= 3}
    tpex = tpex_payload[0]
    return MarketBreadth(
        twse_date=twse_payload.get("date", ""),
        tpex_date=roc_date_to_iso(tpex.get("Date", "")),
        twse_up=parse_breadth_count(twse_rows.get("上漲(漲停)", ["", "", 0])[2]),
        twse_flat=parse_breadth_count(twse_rows.get("持平", ["", "", 0])[2]),
        twse_down=parse_breadth_count(twse_rows.get("下跌(跌停)", ["", "", 0])[2]),
        tpex_up=parse_amount(tpex.get("PriceRiseCompanyNumbers")),
        tpex_flat=parse_amount(tpex.get("PriceFlatCompanyNumbers")),
        tpex_down=parse_amount(tpex.get("PriceDeclineCompanyNumbers")),
    )


def fetch_etf_list(timeout: int, verify_ssl: bool) -> list[dict[str, str]]:
    request = urllib.request.Request(ETF_LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=make_context(verify_ssl),
    ) as response:
        rows = json.loads(response.read().decode("utf-8-sig"))
    return [
        row
        for row in rows
        if ETF_CODE_PATTERN.match(row.get("Code", ""))
    ]


def parse_float_text(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def fetch_etf_profile_map(timeout: int, verify_ssl: bool) -> dict[str, dict[str, str]]:
    body = urllib.parse.urlencode({}).encode()
    request = urllib.request.Request(
        ETF_PRODUCTS_URL,
        data=body,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.twse.com.tw/zh/ETFortune/products",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=timeout,
        context=make_context(verify_ssl),
    ) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    if payload.get("status") != "success":
        return {}
    return {row.get("stockNo", ""): row for row in payload.get("data", [])}


def enrich_etf_row(row: EtfMarketRow, profile: dict[str, str] | None) -> EtfMarketRow:
    if not profile:
        return row
    return EtfMarketRow(
        code=row.code,
        name=profile.get("stockName") or row.name,
        price=row.price,
        previous_close=row.previous_close,
        open_price=row.open_price,
        high=row.high,
        low=row.low,
        volume=row.volume,
        trade_value=row.trade_value,
        transaction=row.transaction,
        market_time=row.market_time,
        source=row.source,
        market_value=parse_float_text(profile.get("totalAv")),
        listing_date=profile.get("listingDate", ""),
        index_name=profile.get("indexName", ""),
        issuer=profile.get("issuer", ""),
        holders=to_int(profile.get("holders")),
        value_ytd=parse_float_text(profile.get("valueYTD")),
        volume_ytd=to_int(profile.get("volumeYTD")),
    )


def row_from_openapi(row: dict[str, str]) -> EtfMarketRow:
    price = to_float(row.get("ClosingPrice"))
    change = to_float(row.get("Change"))
    previous_close = price - change if price is not None and change is not None else None
    return EtfMarketRow(
        code=row.get("Code", ""),
        name=row.get("Name", ""),
        price=price,
        previous_close=previous_close,
        open_price=to_float(row.get("OpeningPrice")),
        high=to_float(row.get("HighestPrice")),
        low=to_float(row.get("LowestPrice")),
        volume=to_int(row.get("TradeVolume")),
        trade_value=to_int(row.get("TradeValue")),
        transaction=to_int(row.get("Transaction")),
        market_time=row.get("Date", "--"),
        source="TWSE OpenAPI",
    )


def row_from_mis(item: dict[str, str], fallback: EtfMarketRow | None = None) -> EtfMarketRow:
    price = to_float(item.get("z"))
    bid = first_level(item.get("b"))
    ask = first_level(item.get("a"))
    if price is None and bid is not None and ask is not None:
        price = (bid + ask) / 2
    previous_close = to_float(item.get("y"))
    return EtfMarketRow(
        code=item.get("c", fallback.code if fallback else ""),
        name=item.get("n", fallback.name if fallback else ""),
        price=price if price is not None else (fallback.price if fallback else None),
        previous_close=previous_close if previous_close is not None else (fallback.previous_close if fallback else None),
        open_price=to_float(item.get("o")) or (fallback.open_price if fallback else None),
        high=to_float(item.get("h")) or (fallback.high if fallback else None),
        low=to_float(item.get("l")) or (fallback.low if fallback else None),
        volume=to_int(item.get("v")) or (fallback.volume if fallback else None),
        trade_value=fallback.trade_value if fallback else None,
        transaction=fallback.transaction if fallback else None,
        market_time=item.get("t") or item.get("%") or (fallback.market_time if fallback else "--"),
        source="TWSE MIS",
    )


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def fetch_all_etf_quotes(timeout: int, retries: int, verify_ssl: bool) -> list[EtfMarketRow]:
    openapi_rows = fetch_etf_list(timeout, verify_ssl)
    try:
        profiles = fetch_etf_profile_map(timeout, verify_ssl)
    except Exception:
        profiles = {}
    fallback = {row["Code"]: row_from_openapi(row) for row in openapi_rows}
    codes = list(fallback)
    quotes: dict[str, EtfMarketRow] = {}

    for group in chunked(codes, ETF_BATCH_SIZE):
        symbols = "|".join(f"tse_{code}.tw" for code in group)
        try:
            data = fetch_twse_json(symbols, timeout, verify_ssl, retries)
            for item in data.get("msgArray", []):
                code = item.get("c", "")
                quotes[code] = row_from_mis(item, fallback.get(code))
        except RuntimeError:
            for code in group:
                quotes[code] = fallback[code]

    rows = [
        enrich_etf_row(quotes.get(code, fallback[code]), profiles.get(code))
        for code in codes
    ]
    return sorted(
        rows,
        key=lambda row: (
            row.market_value is not None,
            row.market_value or 0,
            row.trade_value or 0,
        ),
        reverse=True,
    )


def fetch_one_etf_quote(code: str, timeout: int, retries: int, verify_ssl: bool) -> EtfMarketRow:
    fallback = None
    profile = None
    try:
        profile = fetch_etf_profile_map(timeout, verify_ssl).get(code)
    except Exception:
        profile = None
    for row in fetch_etf_list(timeout, verify_ssl):
        if row.get("Code") == code:
            fallback = enrich_etf_row(row_from_openapi(row), profile)
            break

    try:
        data = fetch_twse_json(f"tse_{code}.tw", timeout, verify_ssl, retries)
        items = data.get("msgArray", [])
        if items:
            return enrich_etf_row(row_from_mis(items[0], fallback), profile)
    except RuntimeError:
        pass
    if fallback:
        return fallback
    raise RuntimeError(f"找不到 ETF：{code}")


class StockDynamicApp:
    def __init__(
        self,
        root: tk.Tk,
        interval: int,
        source: str,
        timeout: int,
        retries: int,
        include_etfs: bool,
        verify_ssl: bool,
        history_limit: int,
    ) -> None:
        self.root = root
        self.interval = interval
        self.source = source
        self.timeout = timeout
        self.retries = retries
        self.include_etfs = include_etfs
        self.verify_ssl = verify_ssl
        self.history_limit = history_limit
        self.candles: dict[str, list[MarketCandle]] = defaultdict(list)
        self.candle_grains: dict[str, str] = {}
        self.chart_mode = ""
        self.index_previous_close: dict[str, float | None] = {}
        self.etf_rows: dict[str, EtfMarketRow] = {}
        self.tw_stock_symbols = dict(DEFAULT_TAIWAN_STOCKS)
        self.tw_stock_rows: dict[str, SecurityQuote] = {}
        self.active_page = "taiwan"
        self.us_candles: dict[str, list[MarketCandle]] = defaultdict(list)
        self.us_candle_grains: dict[str, str] = {}
        self.us_chart_mode = ""
        self.us_previous_close: dict[str, float | None] = {}
        self.us_stock_symbols = dict(DEFAULT_US_STOCKS)
        self.us_index_rows: dict[str, SecurityQuote] = {}
        self.adr_rows: dict[str, SecurityQuote] = {}
        self.us_stock_rows: dict[str, SecurityQuote] = {}
        self.us_history_loaded_at = 0.0
        self.market_window: MarketOverviewWindow | None = None
        self.futures_window: TaiwanFuturesWindow | None = None
        self.ai_market_window: MarketAiWindow | None = None
        self.last_quote_status = "正在載入行情"
        self.last_quote_source = source
        self.background_settings_window: tk.Toplevel | None = None
        self.background_image: Image.Image | None = None
        self.background_image_path = ""
        self.background_transparency = tk.DoubleVar(value=65.0)
        self.background_transparency_text = tk.StringVar(value="圖片透明度：65%")
        self.background_file_text = tk.StringVar(value="尚未載入背景圖片")
        self.background_mode_text = tk.StringVar(value="填滿")
        self.background_photo_refs: dict[tk.Canvas, ImageTk.PhotoImage] = {}
        self.background_frame_labels: dict[ttk.Frame, tk.Label] = {}
        self.background_frame_photos: dict[ttk.Frame, ImageTk.PhotoImage] = {}
        self.background_render_cache: dict[tuple[object, ...], Image.Image] = {}
        self.background_bound_hosts: set[str] = set()
        self.background_redraw_job: str | None = None
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loading = False
        self.loading_page = ""
        self.refresh_job: str | None = None
        self.closed = False
        self.theme_name = "night"
        self.theme = THEMES[self.theme_name]

        self.root.title("Stock Dynamic")
        self.root.geometry("1240x840")
        self.root.minsize(760, 620)
        self.root.configure(bg=self.theme["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status_text = tk.StringVar(value="正在載入行情...")
        self.source_text = tk.StringVar(value=f"來源：{source}")
        self.subtitle_text = tk.StringVar(value="台股四大指數、ETF 與個股即時看板")
        self.market_page_button_text = tk.StringVar(value="切換美股")
        self.style = ttk.Style()
        self.card_widgets: list[ttk.Frame] = []
        self.current_layout: tuple[int, bool] | None = None
        self.build_ui()
        self.register_global_background(self.root)
        self.apply_theme()
        self.root.bind("<Configure>", self.on_root_resize)
        self.refresh()
        self.root.after(200, self.process_results)

    def build_ui(self) -> None:
        self.style.theme_use("clam")
        self.style.configure("Flat.TButton", padding=(14, 7))

        header = ttk.Frame(self.root, style="TFrame")
        header.pack(fill="x")
        header_inner = ttk.Frame(header, style="Header.TFrame", padding=(22, 16))
        header_inner.pack(fill="x", padx=16, pady=(16, 10))

        title = ttk.Label(
            header_inner,
            text="Stock Dynamic",
            style="Header.TLabel",
            font=("Segoe UI", 22, "bold"),
        )
        title.grid(row=0, column=0, sticky="w")
        subtitle = ttk.Label(
            header_inner,
            textvariable=self.subtitle_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 10),
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(
            header_inner,
            textvariable=self.status_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 10),
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            header_inner,
            textvariable=self.source_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 10),
        ).grid(row=2, column=1, sticky="e", pady=(8, 0))
        actions = ttk.Frame(header_inner, style="Header.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(
            actions,
            text="市場籌碼",
            style="Flat.TButton",
            command=self.open_market_overview,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="台指期貨",
            style="Flat.TButton",
            command=self.open_taiwan_futures,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            textvariable=self.market_page_button_text,
            style="Flat.TButton",
            command=self.switch_market_page,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="AI 盤勢分析",
            style="Flat.TButton",
            command=self.open_ai_market,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="背景設定",
            style="Flat.TButton",
            command=self.open_background_settings,
        ).pack(side="left", padx=(0, 8))
        refresh_button = ttk.Button(
            actions,
            text="立即更新",
            style="Flat.TButton",
            command=self.refresh_all,
        )
        refresh_button.pack(side="left")
        header_inner.columnconfigure(0, weight=1)

        self.page_host = ttk.Frame(self.root, style="TFrame")
        self.page_host.pack(fill="both", expand=True)
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)
        self.taiwan_page = ttk.Frame(self.page_host, style="TFrame")
        self.taiwan_page.grid(row=0, column=0, sticky="nsew")

        self.card_frame = ttk.Frame(self.taiwan_page, style="TFrame")
        self.card_frame.pack(fill="x", padx=16, pady=8)
        self.cards: dict[str, dict[str, ttk.Label]] = {}
        for idx, name in enumerate(INDICES.values()):
            frame = ttk.Frame(self.card_frame, style="Card.TFrame", padding=16)
            frame.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 10, 0))
            self.card_widgets.append(frame)
            accent = tk.Frame(frame, height=4, bg=CHART_COLORS.get(name, self.theme["accent"]))
            accent.pack(fill="x", side="top", pady=(0, 12))
            ttk.Label(
                frame,
                text=name,
                style="CardTitle.TLabel",
                font=("Microsoft JhengHei UI", 11, "bold"),
            ).pack(anchor="w")
            value = ttk.Label(
                frame,
                text="--",
                style="CardValue.TLabel",
                font=("Segoe UI", 25, "bold"),
            )
            value.pack(anchor="w", pady=(10, 2))
            change = ttk.Label(
                frame,
                text="--",
                style="Muted.TLabel",
                font=("Segoe UI", 11, "bold"),
            )
            change.pack(anchor="w")
            time_label = ttk.Label(frame, text="--", style="Muted.TLabel")
            time_label.pack(anchor="w", pady=(8, 0))
            self.cards[name] = {"value": value, "change": change, "time": time_label, "accent": accent}
            self.card_frame.columnconfigure(idx, weight=1, uniform="cards")

        self.body = ttk.Frame(self.taiwan_page, style="TFrame")
        self.body.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        self.body.columnconfigure(0, weight=3)
        self.body.columnconfigure(1, weight=2)
        self.body.rowconfigure(0, weight=1)

        self.main_pane = tk.PanedWindow(
            self.body,
            orient="horizontal",
            sashwidth=8,
            sashrelief="raised",
            showhandle=True,
            bd=0,
            opaqueresize=True,
        )
        self.main_pane.grid(row=0, column=0, columnspan=2, sticky="nsew")

        self.chart_panel = ttk.Frame(self.main_pane, style="Card.TFrame", padding=16)
        ttk.Label(
            self.chart_panel,
            text="四大指數 K 線與均線",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).pack(anchor="w")
        self.chart_pane = tk.PanedWindow(
            self.chart_panel,
            orient="vertical",
            sashwidth=7,
            sashrelief="raised",
            showhandle=True,
            bd=0,
            opaqueresize=True,
        )
        self.chart_pane.pack(fill="both", expand=True, pady=(8, 0))
        self.chart_rows: list[tk.PanedWindow] = []
        self.index_canvases: dict[str, tk.Canvas] = {}
        index_names = list(INDICES.values())
        for row_index in range(2):
            row_pane = tk.PanedWindow(
                self.chart_pane,
                orient="horizontal",
                sashwidth=7,
                sashrelief="raised",
                showhandle=True,
                bd=0,
                opaqueresize=True,
            )
            self.chart_rows.append(row_pane)
            self.chart_pane.add(row_pane, stretch="always", minsize=150)
            for col_index in range(2):
                name = index_names[row_index * 2 + col_index]
                canvas = tk.Canvas(
                    row_pane,
                    bg=self.theme["surface"],
                    highlightthickness=0,
                    height=200,
                    width=280,
                )
                self.index_canvases[name] = canvas
                row_pane.add(canvas, stretch="always", minsize=180)
                canvas.bind("<Configure>", lambda _event, n=name: self.draw_one_index_chart(n))

        self.right_panel = ttk.Frame(self.main_pane, style="TFrame")
        self.right_panel.rowconfigure(0, weight=1)
        self.right_panel.columnconfigure(0, weight=1)

        self.taiwan_tables_pane = tk.PanedWindow(
            self.right_panel,
            orient="vertical",
            sashwidth=7,
            sashrelief="raised",
            showhandle=True,
            bd=0,
            opaqueresize=True,
        )
        self.taiwan_tables_pane.grid(row=0, column=0, sticky="nsew")

        self.etf_panel = ttk.Frame(
            self.taiwan_tables_pane,
            style="Card.TFrame",
            padding=16,
        )
        ttk.Label(
            self.etf_panel,
            text="台灣上市 ETF",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).pack(anchor="w")
        columns = ("code", "name", "market_value", "price", "change", "percent", "volume", "time", "source")
        table_frame = ttk.Frame(self.etf_panel, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.etf_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=16,
        )
        etf_scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.etf_tree.yview)
        etf_xscrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=self.etf_tree.xview)
        self.etf_tree.configure(yscrollcommand=etf_scrollbar.set, xscrollcommand=etf_xscrollbar.set)
        self.etf_tree.tag_configure("up", foreground=self.theme["up"])
        self.etf_tree.tag_configure("down", foreground=self.theme["down"])
        headings = {
            "code": "代號",
            "name": "名稱",
            "market_value": "資產規模(億)",
            "price": "現價",
            "change": "漲跌",
            "percent": "%",
            "volume": "量",
            "time": "時間",
            "source": "來源",
        }
        widths = {
            "code": 64,
            "name": 160,
            "market_value": 95,
            "price": 80,
            "change": 80,
            "percent": 70,
            "volume": 95,
            "time": 74,
            "source": 90,
        }
        for column in columns:
            self.etf_tree.heading(column, text=headings[column])
            self.etf_tree.column(column, width=widths[column], anchor="e")
        self.etf_tree.column("code", anchor="w")
        self.etf_tree.column("name", anchor="w")
        self.etf_tree.grid(row=0, column=0, sticky="nsew")
        etf_scrollbar.grid(row=0, column=1, sticky="ns")
        etf_xscrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.etf_tree.bind("<Double-1>", self.open_selected_etf)

        self.tw_stock_panel = ttk.Frame(
            self.taiwan_tables_pane,
            style="Card.TFrame",
            padding=16,
        )
        tw_stock_header = ttk.Frame(self.tw_stock_panel, style="Card.TFrame")
        tw_stock_header.pack(fill="x")
        ttk.Label(
            tw_stock_header,
            text="台股個股",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).pack(side="left")
        self.tw_stock_text = tk.StringVar()
        ttk.Entry(
            tw_stock_header,
            textvariable=self.tw_stock_text,
            width=10,
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            tw_stock_header,
            text="加入代號",
            style="Flat.TButton",
            command=self.add_taiwan_stock_symbol,
        ).pack(side="right")
        ttk.Label(
            self.tw_stock_panel,
            text="預載大型個股；可輸入上市或上櫃代號，雙擊開啟 K 線",
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 8),
        ).pack(anchor="w", pady=(2, 6))
        self.tw_stock_tree = self.create_taiwan_stock_tree(self.tw_stock_panel)
        self.tw_stock_tree.bind("<Double-1>", self.open_selected_taiwan_stock)

        self.taiwan_tables_pane.add(
            self.etf_panel,
            stretch="always",
            minsize=210,
        )
        self.taiwan_tables_pane.add(
            self.tw_stock_panel,
            stretch="always",
            minsize=190,
        )
        self.main_pane.add(self.chart_panel, stretch="always", minsize=360)
        self.main_pane.add(self.right_panel, stretch="always", minsize=360)
        self.build_us_page()
        self.taiwan_page.tkraise()
        self.layout_for_width(self.root.winfo_width() or 1240)

    def create_taiwan_stock_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        table_frame = ttk.Frame(parent, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True)
        columns = (
            "code",
            "name",
            "price",
            "change",
            "percent",
            "volume",
            "time",
            "source",
        )
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=8,
        )
        scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=tree.yview,
        )
        xscrollbar = ttk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=tree.xview,
        )
        tree.configure(
            yscrollcommand=scrollbar.set,
            xscrollcommand=xscrollbar.set,
        )
        headings = {
            "code": "代號",
            "name": "名稱",
            "price": "現價",
            "change": "漲跌",
            "percent": "%",
            "volume": "成交量",
            "time": "時間",
            "source": "來源",
        }
        widths = {
            "code": 68,
            "name": 140,
            "price": 82,
            "change": 82,
            "percent": 70,
            "volume": 100,
            "time": 92,
            "source": 110,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="e")
        tree.column("code", anchor="w")
        tree.column("name", anchor="w")
        tree.column("source", anchor="w")
        tree.tag_configure("up", foreground=self.theme["up"])
        tree.tag_configure("down", foreground=self.theme["down"])
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        return tree

    def build_us_page(self) -> None:
        self.us_page = ttk.Frame(self.page_host, style="TFrame")
        self.us_page.grid(row=0, column=0, sticky="nsew")

        self.us_card_frame = ttk.Frame(self.us_page, style="TFrame")
        self.us_card_frame.pack(fill="x", padx=16, pady=8)
        self.us_cards: dict[str, dict[str, ttk.Label | tk.Frame]] = {}
        self.us_card_widgets: list[ttk.Frame] = []
        for index, name in enumerate(US_INDEX_SYMBOLS):
            frame = ttk.Frame(self.us_card_frame, style="Card.TFrame", padding=16)
            frame.grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 10, 0),
            )
            self.us_card_widgets.append(frame)
            accent = tk.Frame(
                frame,
                height=4,
                bg=CHART_COLORS.get(name, self.theme["accent"]),
            )
            accent.pack(fill="x", side="top", pady=(0, 12))
            ttk.Label(
                frame,
                text=name,
                style="CardTitle.TLabel",
                font=("Microsoft JhengHei UI", 11, "bold"),
            ).pack(anchor="w")
            value = ttk.Label(
                frame,
                text="--",
                style="CardValue.TLabel",
                font=("Segoe UI", 25, "bold"),
            )
            value.pack(anchor="w", pady=(10, 2))
            change = ttk.Label(
                frame,
                text="--",
                style="Muted.TLabel",
                font=("Segoe UI", 11, "bold"),
            )
            change.pack(anchor="w")
            time_label = ttk.Label(frame, text="--", style="Muted.TLabel")
            time_label.pack(anchor="w", pady=(8, 0))
            self.us_cards[name] = {
                "value": value,
                "change": change,
                "time": time_label,
                "accent": accent,
            }
            self.us_card_frame.columnconfigure(index, weight=1, uniform="us_cards")

        self.us_main_pane = tk.PanedWindow(
            self.us_page,
            orient="horizontal",
            sashwidth=8,
            sashrelief="raised",
            showhandle=True,
            bd=0,
            opaqueresize=True,
        )
        self.us_main_pane.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        self.us_chart_panel = ttk.Frame(
            self.us_main_pane,
            style="Card.TFrame",
            padding=16,
        )
        ttk.Label(
            self.us_chart_panel,
            text="美股四大指數 K 線",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self.us_chart_panel,
            text="美股開盤顯示 5 分 K，休市後自動切換日 K",
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).pack(anchor="w", pady=(2, 6))
        self.us_chart_grid = ttk.Frame(self.us_chart_panel, style="Card.TFrame")
        self.us_chart_grid.pack(fill="both", expand=True)
        self.us_chart_canvases: dict[str, tk.Canvas] = {}
        for index, name in enumerate(US_CHART_NAMES):
            row = index // 2
            column = index % 2
            canvas = tk.Canvas(
                self.us_chart_grid,
                bg=self.theme["surface"],
                highlightthickness=0,
                height=120,
                width=280,
            )
            canvas.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 4, 4 if column == 0 else 0),
                pady=(0 if row == 0 else 4, 0),
            )
            canvas.bind(
                "<Configure>",
                lambda _event, chart_name=name: self.draw_one_us_chart(chart_name),
            )
            self.us_chart_canvases[name] = canvas
        for column in range(2):
            self.us_chart_grid.columnconfigure(column, weight=1, uniform="us_charts")
        for row in range(2):
            self.us_chart_grid.rowconfigure(row, weight=1, uniform="us_charts")

        self.us_tables_pane = tk.PanedWindow(
            self.us_main_pane,
            orient="vertical",
            sashwidth=7,
            sashrelief="raised",
            showhandle=True,
            bd=0,
            opaqueresize=True,
        )
        adr_panel = ttk.Frame(self.us_tables_pane, style="Card.TFrame", padding=14)
        ttk.Label(
            adr_panel,
            text="台灣企業 ADR／ADS（完整清單）",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            adr_panel,
            text="美國交易所與 OTC 市場共 10 檔；無即時行情時仍保留代號",
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 8),
        ).pack(anchor="w", pady=(2, 6))
        self.adr_tree = self.create_security_tree(adr_panel)

        stocks_panel = ttk.Frame(
            self.us_tables_pane,
            style="Card.TFrame",
            padding=14,
        )
        stock_header = ttk.Frame(stocks_panel, style="Card.TFrame")
        stock_header.pack(fill="x")
        ttk.Label(
            stock_header,
            text="美國個股（常用＋自訂）",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 12, "bold"),
        ).pack(side="left")
        self.us_symbol_text = tk.StringVar()
        ttk.Entry(
            stock_header,
            textvariable=self.us_symbol_text,
            width=12,
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            stock_header,
            text="加入代號",
            style="Flat.TButton",
            command=self.add_us_stock_symbol,
        ).pack(side="right")
        ttk.Label(
            stocks_panel,
            text="預載大型個股；可加入任一 Yahoo 美股代號，例如 PLTR、BRK-B",
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 8),
        ).pack(anchor="w", pady=(2, 6))
        self.us_stock_tree = self.create_security_tree(stocks_panel)

        self.us_tables_pane.add(adr_panel, stretch="always", minsize=170)
        self.us_tables_pane.add(stocks_panel, stretch="always", minsize=210)
        self.us_main_pane.add(self.us_chart_panel, stretch="always", minsize=440)
        self.us_main_pane.add(self.us_tables_pane, stretch="always", minsize=380)

    def create_security_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        table_frame = ttk.Frame(parent, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True)
        columns = (
            "symbol",
            "name",
            "price",
            "change",
            "percent",
            "volume",
            "time",
        )
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=8,
        )
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        xscrollbar = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(
            yscrollcommand=scrollbar.set,
            xscrollcommand=xscrollbar.set,
        )
        headings = {
            "symbol": "代號",
            "name": "名稱",
            "price": "現價(USD)",
            "change": "漲跌",
            "percent": "%",
            "volume": "成交量",
            "time": "台北時間",
        }
        widths = {
            "symbol": 70,
            "name": 155,
            "price": 90,
            "change": 82,
            "percent": 70,
            "volume": 100,
            "time": 98,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="e")
        tree.column("symbol", anchor="w")
        tree.column("name", anchor="w")
        tree.tag_configure("up", foreground=self.theme["up"])
        tree.tag_configure("down", foreground=self.theme["down"])
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        xscrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        return tree

    def on_root_resize(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self.layout_for_width(event.width)
            self.schedule_background_redraw()

    def layout_for_width(self, width: int, force: bool = False) -> None:
        card_columns = 4
        if width < 620:
            card_columns = 1
        elif width < 980:
            card_columns = 2

        stacked_body = width < 1120
        layout_key = (card_columns, stacked_body)
        if self.current_layout == layout_key and not force:
            return
        self.current_layout = layout_key

        for column in range(4):
            self.card_frame.columnconfigure(column, weight=0)
        for row in range(4):
            self.card_frame.rowconfigure(row, weight=0)
        for idx, frame in enumerate(self.card_widgets):
            frame.grid_forget()
            row = idx // card_columns
            column = idx % card_columns
            padx = (0 if column == 0 else 10, 0)
            pady = (0 if row == 0 else 10, 0)
            frame.grid(row=row, column=column, sticky="nsew", padx=padx, pady=pady)
        for column in range(card_columns):
            self.card_frame.columnconfigure(column, weight=1, uniform="cards")
        for row in range((len(self.card_widgets) + card_columns - 1) // card_columns):
            self.card_frame.rowconfigure(row, weight=1)

        for column in range(4):
            self.us_card_frame.columnconfigure(column, weight=0)
        for row in range(4):
            self.us_card_frame.rowconfigure(row, weight=0)
        for index, frame in enumerate(self.us_card_widgets):
            frame.grid_forget()
            row = index // card_columns
            column = index % card_columns
            frame.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 10, 0),
                pady=(0 if row == 0 else 10, 0),
            )
        for column in range(card_columns):
            self.us_card_frame.columnconfigure(column, weight=1, uniform="us_cards")
        for row in range(
            (len(self.us_card_widgets) + card_columns - 1) // card_columns
        ):
            self.us_card_frame.rowconfigure(row, weight=1)

        for index in range(2):
            self.body.columnconfigure(index, weight=0)
            self.body.rowconfigure(index, weight=0)
        self.body.grid_columnconfigure(0, minsize=0)
        self.body.grid_columnconfigure(1, minsize=0)
        self.body.grid_rowconfigure(0, minsize=0)
        self.body.grid_rowconfigure(1, minsize=0)

        if stacked_body:
            self.body.columnconfigure(0, weight=1)
            self.body.rowconfigure(0, weight=3)
            self.main_pane.configure(orient="vertical")
            self.us_main_pane.configure(orient="vertical")
            self.main_pane.grid(row=0, column=0, sticky="nsew")
        else:
            self.body.columnconfigure(0, weight=3)
            self.body.columnconfigure(1, weight=2)
            self.body.rowconfigure(0, weight=1)
            self.main_pane.configure(orient="horizontal")
            self.us_main_pane.configure(orient="horizontal")
            self.main_pane.grid(row=0, column=0, columnspan=2, sticky="nsew")

        self.draw_chart()
        self.draw_us_charts()

    def open_background_settings(self) -> None:
        if self.background_settings_window and self.background_settings_window.winfo_exists():
            self.background_settings_window.deiconify()
            self.background_settings_window.lift()
            self.background_settings_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.background_settings_window = window
        window.title("Dashboard 背景設定")
        window.geometry("520x315")
        window.minsize(460, 295)
        window.transient(self.root)
        window.configure(bg=self.theme["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close_background_settings)

        panel = ttk.Frame(window, style="Card.TFrame", padding=20)
        panel.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Label(
            panel,
            text="自訂 Dashboard 背景",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            panel,
            textvariable=self.background_file_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 14))

        ttk.Button(
            panel,
            text="載入圖片",
            style="Flat.TButton",
            command=self.load_background_image,
        ).grid(row=2, column=0, sticky="w")
        ttk.Button(
            panel,
            text="清除背景",
            style="Flat.TButton",
            command=self.clear_background_image,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0))

        ttk.Label(
            panel,
            textvariable=self.background_transparency_text,
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 10, "bold"),
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(18, 4))
        transparency_scale = ttk.Scale(
            panel,
            from_=0,
            to=100,
            variable=self.background_transparency,
            command=self.on_background_transparency_changed,
        )
        transparency_scale.grid(row=4, column=0, columnspan=3, sticky="ew")
        ttk.Label(
            panel,
            text="0% 完全顯示圖片；100% 完全隱藏圖片",
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(
            panel,
            text="圖片配置",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 10, "bold"),
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(16, 5))
        mode_picker = ttk.Combobox(
            panel,
            state="readonly",
            textvariable=self.background_mode_text,
            values=list(DASHBOARD_BACKGROUND_MODES),
            width=14,
        )
        mode_picker.grid(row=7, column=0, columnspan=2, sticky="w")
        mode_picker.bind("<<ComboboxSelected>>", self.on_background_mode_changed)
        panel.columnconfigure(2, weight=1)
        self.register_global_background(window)
        self.redraw_global_backgrounds()

    def close_background_settings(self) -> None:
        if self.background_settings_window:
            self.background_settings_window.destroy()
            self.background_settings_window = None

    def load_background_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.background_settings_window or self.root,
            title="選擇 Dashboard 背景圖片",
            filetypes=[
                ("圖片檔案", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("所有檔案", "*.*"),
            ],
        )
        if not path:
            return
        try:
            with Image.open(path) as source_image:
                self.background_image = ImageOps.exif_transpose(source_image).convert("RGB").copy()
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "無法載入背景圖片",
                f"圖片格式無法讀取：\n{exc}",
                parent=self.background_settings_window or self.root,
            )
            return

        self.background_image_path = path
        self.background_file_text.set(f"已載入：{Path(path).name}")
        self.status_text.set(f"Dashboard 背景已更新：{Path(path).name}")
        self.background_render_cache.clear()
        self.redraw_background_charts()

    def clear_background_image(self) -> None:
        self.background_image = None
        self.background_image_path = ""
        self.background_file_text.set("尚未載入背景圖片")
        self.background_photo_refs.clear()
        self.background_frame_photos.clear()
        self.background_render_cache.clear()
        self.status_text.set("Dashboard 背景圖片已清除")
        self.redraw_background_charts()

    def on_background_transparency_changed(self, value: str) -> None:
        transparency = max(0, min(100, round(float(value))))
        self.background_transparency_text.set(f"圖片透明度：{transparency}%")
        self.background_render_cache.clear()
        self.schedule_background_redraw()

    def on_background_mode_changed(self, _event: tk.Event | None = None) -> None:
        mode_label = self.background_mode_text.get()
        if mode_label not in DASHBOARD_BACKGROUND_MODES:
            self.background_mode_text.set("填滿")
            mode_label = "填滿"
        self.background_render_cache.clear()
        self.status_text.set(f"Dashboard 背景配置：{mode_label}")
        self.schedule_background_redraw()

    def schedule_background_redraw(self, delay_ms: int = 70) -> None:
        if self.background_redraw_job:
            try:
                self.root.after_cancel(self.background_redraw_job)
            except tk.TclError:
                pass
        self.background_redraw_job = self.root.after(delay_ms, self.redraw_background_charts)

    def redraw_background_charts(self) -> None:
        self.background_redraw_job = None
        self.redraw_global_backgrounds()
        self.draw_chart()
        self.draw_us_charts()
        if self.futures_window and not self.futures_window.closed:
            self.futures_window.draw_charts()

    def register_global_background(self, container: tk.Misc) -> None:
        """Place synchronized wallpaper slices behind every ttk frame."""
        children = list(container.winfo_children())
        for child in children:
            if isinstance(child, ttk.Frame) and child not in self.background_frame_labels:
                self.background_frame_labels[child] = tk.Label(
                    child,
                    bd=0,
                    highlightthickness=0,
                )
            self.register_global_background(child)

        host = container.winfo_toplevel()
        host_key = str(host)
        if host_key not in self.background_bound_hosts:
            host.bind("<Configure>", self.on_background_host_resize, add="+")
            self.background_bound_hosts.add(host_key)

    def on_background_host_resize(self, event: tk.Event) -> None:
        if event.widget is event.widget.winfo_toplevel():
            self.background_render_cache.clear()
            self.schedule_background_redraw(90)

    def background_mode(self) -> str:
        return DASHBOARD_BACKGROUND_MODES.get(self.background_mode_text.get(), "fill")

    def build_host_background(
        self,
        host: tk.Misc,
        theme: dict[str, str],
    ) -> Image.Image | None:
        if self.background_image is None:
            return None
        image_visibility = 1.0 - (float(self.background_transparency.get()) / 100.0)
        if image_visibility <= 0:
            return None

        width = max(1, int(host.winfo_width()))
        height = max(1, int(host.winfo_height()))
        mode = self.background_mode()
        cache_key = (
            str(host),
            width,
            height,
            mode,
            round(image_visibility, 3),
            id(self.background_image),
            theme["bg"],
        )
        cached = self.background_render_cache.get(cache_key)
        if cached is not None:
            return cached

        base_color = ImageColor.getrgb(theme["bg"])
        base = Image.new("RGB", (width, height), base_color)
        layout = Image.new("RGB", (width, height), base_color)
        source = self.background_image
        if mode == "stretch":
            layout = source.resize((width, height), Image.Resampling.LANCZOS)
        elif mode == "fill":
            layout = ImageOps.fit(
                source,
                (width, height),
                method=Image.Resampling.LANCZOS,
            )
        elif mode == "fit":
            fitted = ImageOps.contain(
                source,
                (width, height),
                method=Image.Resampling.LANCZOS,
            )
            layout.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
        elif mode == "tile":
            for x in range(0, width, source.width):
                for y in range(0, height, source.height):
                    layout.paste(source, (x, y))
        else:  # center
            layout.paste(source, ((width - source.width) // 2, (height - source.height) // 2))

        rendered = Image.blend(base, layout, image_visibility)
        self.background_render_cache = {
            key: value
            for key, value in self.background_render_cache.items()
            if key[0] != str(host)
        }
        self.background_render_cache[cache_key] = rendered
        return rendered

    @staticmethod
    def crop_widget_background(
        host_background: Image.Image,
        widget: tk.Misc,
        host: tk.Misc,
        width: int,
        height: int,
    ) -> Image.Image:
        x = widget.winfo_rootx() - host.winfo_rootx()
        y = widget.winfo_rooty() - host.winfo_rooty()
        return host_background.crop((x, y, x + width, y + height))

    def redraw_global_backgrounds(self) -> None:
        if self.background_image is None or self.background_transparency.get() >= 100:
            for label in self.background_frame_labels.values():
                try:
                    label.place_forget()
                except tk.TclError:
                    pass
            self.background_frame_photos.clear()
            return

        invalid_frames: list[ttk.Frame] = []
        for frame, label in list(self.background_frame_labels.items()):
            try:
                if not frame.winfo_exists():
                    invalid_frames.append(frame)
                    continue
                host = frame.winfo_toplevel()
                frame_width = max(1, frame.winfo_width())
                frame_height = max(1, frame.winfo_height())
                host_background = self.build_host_background(host, self.theme)
                if host_background is None:
                    label.place_forget()
                    continue
                crop = self.crop_widget_background(
                    host_background,
                    frame,
                    host,
                    frame_width,
                    frame_height,
                )
                photo = ImageTk.PhotoImage(crop, master=label)
                self.background_frame_photos[frame] = photo
                label.configure(image=photo)
                label.place(x=0, y=0, relwidth=1, relheight=1)
                label.lower()
            except tk.TclError:
                invalid_frames.append(frame)
        for frame in invalid_frames:
            self.background_frame_labels.pop(frame, None)
            self.background_frame_photos.pop(frame, None)

    def draw_canvas_background(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        theme: dict[str, str] | None = None,
    ) -> bool:
        if self.background_image is None:
            self.background_photo_refs.pop(canvas, None)
            return False

        target_width = max(1, int(width))
        target_height = max(1, int(height))
        active_theme = theme or self.theme
        host = canvas.winfo_toplevel()
        host_background = self.build_host_background(host, active_theme)
        if host_background is None:
            self.background_photo_refs.pop(canvas, None)
            return False
        crop = self.crop_widget_background(
            host_background,
            canvas,
            host,
            target_width,
            target_height,
        )
        photo = ImageTk.PhotoImage(crop, master=canvas)
        self.background_photo_refs[canvas] = photo
        canvas.create_image(0, 0, image=photo, anchor="nw", tags=("dashboard_background",))
        return True

    def apply_theme(self) -> None:
        theme = self.theme
        self.root.configure(bg=theme["bg"])

        self.style.configure("TFrame", background=theme["bg"])
        self.style.configure("Header.TFrame", background=theme["surface"])
        self.style.configure("Header.TLabel", background=theme["surface"], foreground=theme["text"])
        self.style.configure("Muted.TLabel", background=theme["surface"], foreground=theme["muted"])
        self.style.configure("PageMuted.TLabel", background=theme["bg"], foreground=theme["muted"])
        self.style.configure("Card.TFrame", background=theme["surface"], relief="flat")
        self.style.configure("CardTitle.TLabel", background=theme["surface"], foreground=theme["text"])
        self.style.configure("CardValue.TLabel", background=theme["surface"], foreground=theme["text"])
        self.style.configure("Up.TLabel", background=theme["surface"], foreground=theme["up"])
        self.style.configure("Down.TLabel", background=theme["surface"], foreground=theme["down"])
        self.style.configure(
            "Treeview",
            rowheight=32,
            background=theme["surface"],
            fieldbackground=theme["surface"],
            foreground=theme["text"],
            bordercolor=theme["line"],
        )
        self.style.configure(
            "Treeview.Heading",
            background=theme["surface_alt"],
            foreground=theme["muted"],
            font=("Microsoft JhengHei UI", 9, "bold"),
        )
        self.style.map(
            "Treeview",
            background=[("selected", theme["selected"])],
            foreground=[("selected", theme["text"])],
        )

        for pane in [
            self.main_pane,
            self.chart_pane,
            *self.chart_rows,
            self.taiwan_tables_pane,
            self.us_main_pane,
            self.us_tables_pane,
        ]:
            pane.configure(bg=theme["line"])
        for canvas in [
            *self.index_canvases.values(),
            *self.us_chart_canvases.values(),
        ]:
            canvas.configure(bg=theme["surface"])
        self.etf_tree.tag_configure("up", foreground=theme["up"])
        self.etf_tree.tag_configure("down", foreground=theme["down"])
        for tree in (self.tw_stock_tree, self.adr_tree, self.us_stock_tree):
            tree.tag_configure("up", foreground=theme["up"])
            tree.tag_configure("down", foreground=theme["down"])
        for name, labels in self.cards.items():
            labels["accent"].configure(bg=CHART_COLORS.get(name, theme["accent"]))
        for name, labels in self.us_cards.items():
            labels["accent"].configure(
                bg=CHART_COLORS.get(name, theme["accent"])
            )
        self.draw_chart()
        self.draw_us_charts()

    def switch_market_page(self) -> None:
        self.active_page = "us" if self.active_page == "taiwan" else "taiwan"
        if self.active_page == "us":
            self.us_page.tkraise()
            self.market_page_button_text.set("返回台股")
            self.subtitle_text.set("美股四大指數、ADR 與美國個股")
        else:
            self.taiwan_page.tkraise()
            self.market_page_button_text.set("切換美股")
            self.subtitle_text.set("台股四大指數、ETF 與個股即時看板")
        self.current_layout = None
        self.layout_for_width(self.root.winfo_width() or 1240, force=True)
        self.schedule_refresh(0)

    def add_taiwan_stock_symbol(self) -> None:
        code = self.tw_stock_text.get().strip().upper()
        if not re.fullmatch(r"[0-9A-Z]{4,6}", code):
            self.status_text.set("台股代號格式錯誤，請輸入例如 2330、6488")
            return
        if code not in self.tw_stock_symbols:
            self.tw_stock_symbols[code] = code
        self.tw_stock_text.set("")
        self.status_text.set(f"已加入 {code}，正在更新台股個股行情...")
        self.schedule_refresh(0)

    def add_us_stock_symbol(self) -> None:
        symbol = self.us_symbol_text.get().strip().upper()
        if not re.fullmatch(r"[A-Z0-9.^=-]{1,15}", symbol):
            self.status_text.set("美股代號格式錯誤，請輸入例如 PLTR、BRK-B")
            return
        if symbol not in self.us_stock_symbols:
            self.us_stock_symbols[symbol] = symbol
        self.us_symbol_text.set("")
        self.status_text.set(f"已加入 {symbol}，正在更新行情...")
        self.schedule_refresh(0)

    def open_market_overview(self) -> None:
        if self.market_window and not self.market_window.closed:
            self.market_window.window.deiconify()
            self.market_window.window.lift()
            self.market_window.window.focus_force()
            return
        self.market_window = MarketOverviewWindow(
            parent=self.root,
            timeout=self.timeout,
            verify_ssl=self.verify_ssl,
            refresh_interval=max(60, self.interval),
            theme_getter=lambda: self.theme,
        )
        self.register_global_background(self.market_window.window)
        self.redraw_global_backgrounds()

    def open_taiwan_futures(self) -> None:
        if self.futures_window and not self.futures_window.closed:
            self.futures_window.window.deiconify()
            self.futures_window.window.lift()
            self.futures_window.window.focus_force()
            self.futures_window.refresh()
            return
        self.futures_window = TaiwanFuturesWindow(
            parent=self.root,
            timeout=self.timeout,
            retries=self.retries,
            refresh_interval=self.interval,
            history_limit=self.history_limit,
            theme_getter=lambda: self.theme,
            background_renderer=self.draw_canvas_background,
        )
        self.register_global_background(self.futures_window.window)
        self.redraw_background_charts()

    def open_ai_market(self) -> None:
        if self.ai_market_window and not self.ai_market_window.closed:
            self.ai_market_window.window.deiconify()
            self.ai_market_window.window.lift()
            self.ai_market_window.window.focus_force()
            return
        self.ai_market_window = MarketAiWindow(
            parent=self.root,
            snapshot_loader=self.collect_ai_market_snapshot,
            theme_getter=lambda: self.theme,
        )
        self.register_global_background(self.ai_market_window.window)
        self.redraw_global_backgrounds()

    @staticmethod
    def current_market_session() -> str:
        now = datetime.now()
        if now.weekday() >= 5:
            return "休市日"
        current = now.time()
        if current < clock_time(8, 30):
            return "盤前"
        if current <= clock_time(13, 35):
            return "盤中"
        return "收盤後"

    @staticmethod
    def ai_quote_row(
        name: str,
        price: float | None,
        previous_close: float | None,
        market_time: str,
    ) -> dict[str, object]:
        change_percent = None
        if price is not None and previous_close:
            change_percent = (price - previous_close) / previous_close * 100
        return {
            "name": name,
            "price": price,
            "previous_close": previous_close,
            "change_percent": change_percent,
            "market_time": market_time,
        }

    def collect_ai_market_snapshot(self) -> dict[str, object]:
        """Collect a compact public-data snapshot outside Tk's UI thread."""
        def safe_items(mapping) -> list[tuple[object, object]]:
            for _attempt in range(3):
                try:
                    return list(mapping.items())
                except RuntimeError:
                    time.sleep(0.01)
            return []

        errors: list[str] = []
        institutions: InstitutionSnapshot | None = None
        breadth: MarketBreadth | None = None
        futures_payload = None
        us_market_quotes: list[SecurityQuote] | None = None
        requested_us_symbols = {
            **dict(safe_items(self.us_stock_symbols)),
            **TAIWAN_ADR_SYMBOLS,
            **{
                symbol: name
                for name, symbol in US_INDEX_SYMBOLS.items()
            },
        }
        with ThreadPoolExecutor(max_workers=4) as executor:
            institution_future = executor.submit(
                fetch_institution_trades, self.timeout, self.verify_ssl
            )
            breadth_future = executor.submit(
                fetch_market_breadth, self.timeout, self.verify_ssl
            )
            futures_future = executor.submit(
                fetch_yahoo_tw_futures,
                YAHOO_TW_FUTURE_SYMBOLS,
                self.timeout,
                self.retries,
                self.history_limit,
            )
            us_market_future = executor.submit(
                fetch_yahoo_security_quotes,
                requested_us_symbols,
                self.timeout,
                self.retries,
            )
            try:
                institutions = institution_future.result()
            except Exception as exc:
                errors.append(f"三大法人資料：{exc}")
            try:
                breadth = breadth_future.result()
            except Exception as exc:
                errors.append(f"市場漲跌家數：{exc}")
            try:
                futures_payload = futures_future.result()
            except Exception as exc:
                errors.append(f"台指近月期貨：{exc}")
            try:
                us_market_quotes = us_market_future.result()
            except Exception as exc:
                errors.append(f"美股／ADR 行情：{exc}")

        candles = {name: list(rows) for name, rows in safe_items(self.candles)}
        previous_closes = dict(self.index_previous_close)
        grains = dict(self.candle_grains)
        indices = []
        for name in INDICES.values():
            rows = candles.get(name, [])
            latest = rows[-1] if rows else None
            item = self.ai_quote_row(
                name,
                latest.close if latest else None,
                previous_closes.get(name),
                latest.label if latest else "--",
            )
            item["grain"] = grains.get(name, "--")
            indices.append(item)

        def quote_row(name: str, quote) -> dict[str, object]:
            return self.ai_quote_row(
                name,
                quote.price,
                quote.previous_close,
                quote.market_time,
            )

        stocks = [
            quote_row(f"{code} {quote.name}", quote)
            for code, quote in safe_items(self.tw_stock_rows)
            if quote.price is not None
        ]
        stocks.sort(key=lambda row: abs(float(row["change_percent"] or 0)), reverse=True)
        etfs = [
            quote_row(f"{code} {quote.name}", quote)
            for code, quote in safe_items(self.etf_rows)
            if quote.price is not None
        ]
        etfs.sort(key=lambda row: abs(float(row["change_percent"] or 0)), reverse=True)

        fresh_us_by_symbol = {
            quote.symbol: quote
            for quote in (us_market_quotes or [])
        }
        cached_us_indices = dict(safe_items(self.us_index_rows))
        cached_adrs = dict(safe_items(self.adr_rows))
        cached_us_stocks = dict(safe_items(self.us_stock_rows))
        us_indices = []
        for name, symbol in US_INDEX_SYMBOLS.items():
            quote = fresh_us_by_symbol.get(symbol) or cached_us_indices.get(symbol)
            if quote and quote.price is not None:
                us_indices.append(quote_row(name, quote))
        adrs = []
        for symbol, name in TAIWAN_ADR_SYMBOLS.items():
            quote = fresh_us_by_symbol.get(symbol) or cached_adrs.get(symbol)
            if quote and quote.price is not None:
                adrs.append(quote_row(f"{symbol} {name}", quote))
        us_stocks = []
        for symbol, name in safe_items(self.us_stock_symbols):
            quote = fresh_us_by_symbol.get(symbol) or cached_us_stocks.get(symbol)
            if quote and quote.price is not None:
                us_stocks.append(quote_row(f"{symbol} {name}", quote))
        adrs.sort(key=lambda row: abs(float(row["change_percent"] or 0)), reverse=True)
        us_stocks.sort(key=lambda row: abs(float(row["change_percent"] or 0)), reverse=True)

        futures: list[dict[str, object]] = []
        if futures_payload:
            (
                future_histories,
                future_grains,
                future_previous_closes,
                _future_refreshed_times,
                future_errors,
            ) = futures_payload
            for name in TAIWAN_FUTURE_CHART_NAMES:
                rows = future_histories.get(name, [])
                latest = rows[-1] if rows else None
                if latest:
                    item = self.ai_quote_row(
                        name,
                        latest.close,
                        future_previous_closes.get(name),
                        latest.label,
                    )
                    item["grain"] = future_grains.get(name, "Yahoo 5 分 K")
                    futures.append(item)
            available_future_names = {
                str(item.get("name")) for item in futures
            }
            for name, contract in TAIFEX_FRONT_MONTHS.items():
                if name in available_future_names:
                    continue
                try:
                    rows, grain = fetch_taifex_front_month_candles(
                        contract,
                        self.timeout,
                        self.retries,
                        self.verify_ssl,
                        self.history_limit,
                    )
                except Exception as exc:
                    errors.append(f"台指近月期貨：{name} 備援失敗：{exc}")
                    continue
                latest = rows[-1] if rows else None
                if not latest:
                    continue
                previous_close = rows[-2].close if len(rows) >= 2 else None
                item = self.ai_quote_row(
                    name,
                    latest.close,
                    previous_close,
                    latest.label,
                )
                item["grain"] = f"TAIFEX {grain}（Yahoo 暫無成交備援）"
                futures.append(item)
                available_future_names.add(name)
            for error in future_errors:
                error_name = error.split("：", 1)[0]
                if error_name not in available_future_names:
                    errors.append(f"台指近月期貨：{error}")
        if not futures_payload:
            with ThreadPoolExecutor(max_workers=4) as executor:
                fallback_jobs = {
                    name: executor.submit(
                        fetch_taifex_front_month_candles,
                        contract,
                        self.timeout,
                        self.retries,
                        self.verify_ssl,
                        self.history_limit,
                    )
                    for name, contract in TAIFEX_FRONT_MONTHS.items()
                }
                for name, future in fallback_jobs.items():
                    try:
                        rows, grain = future.result()
                    except Exception as exc:
                        errors.append(
                            f"台指近月期貨：{name} TAIFEX 備援失敗：{exc}"
                        )
                        continue
                    latest = rows[-1] if rows else None
                    if not latest:
                        continue
                    previous_close = rows[-2].close if len(rows) >= 2 else None
                    item = self.ai_quote_row(
                        name,
                        latest.close,
                        previous_close,
                        latest.label,
                    )
                    item["grain"] = f"TAIFEX {grain}（Yahoo 連線備援）"
                    futures.append(item)
        futures_window = self.futures_window
        if not futures and futures_window and not futures_window.closed:
            future_candles = {
                name: list(rows)
                for name, rows in safe_items(futures_window.candles)
            }
            for name in TAIWAN_FUTURE_CHART_NAMES:
                rows = future_candles.get(name, [])
                latest = rows[-1] if rows else None
                if latest:
                    futures.append(
                        self.ai_quote_row(
                            name,
                            latest.close,
                            futures_window.previous_close.get(name),
                            latest.label,
                        )
                    )

        institution_rows = []
        if institutions:
            institution_rows = [
                {
                    "name": row.name,
                    "buy": row.buy_amount / 100_000_000,
                    "sell": row.sell_amount / 100_000_000,
                    "net": row.net_amount / 100_000_000,
                    "date": institutions.market_date or "--",
                }
                for row in institutions.rows
            ]
        breadth_row = None
        if breadth:
            up, flat, down = breadth.totals
            breadth_row = {
                "up": up,
                "flat": flat,
                "down": down,
                "twse_date": breadth.twse_date or "--",
                "tpex_date": breadth.tpex_date or "--",
            }
        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session": self.current_market_session(),
            "active_page": "美股" if self.active_page == "us" else "台股",
            "quote_source": self.last_quote_source,
            "quote_status": self.last_quote_status,
            "indices": indices,
            "breadth": breadth_row,
            "institutions": institution_rows,
            "stocks": stocks[:12],
            "etfs": etfs[:10],
            "futures": futures,
            "us_indices": us_indices,
            "adrs": adrs[:10],
            "us_stocks": us_stocks[:12],
            "errors": errors,
        }

    def close(self) -> None:
        self.closed = True
        if self.background_redraw_job:
            try:
                self.root.after_cancel(self.background_redraw_job)
            except tk.TclError:
                pass
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        self.root.destroy()

    def schedule_refresh(self, delay_ms: int | None = None) -> None:
        if self.closed:
            return
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        delay = self.interval * 1000 if delay_ms is None else max(0, delay_ms)
        self.refresh_job = self.root.after(delay, self.run_scheduled_refresh)

    def run_scheduled_refresh(self) -> None:
        self.refresh_job = None
        self.refresh()

    def refresh_all(self) -> None:
        """Refresh the active dashboard and an open futures window together."""
        self.refresh()
        if self.futures_window and not self.futures_window.closed:
            self.futures_window.refresh()

    def refresh(self) -> None:
        if self.loading or self.closed:
            return
        if self.refresh_job:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
            self.refresh_job = None
        self.loading = True
        self.loading_page = self.active_page
        self.status_text.set("正在更新行情...")
        target = self.fetch_us_worker if self.active_page == "us" else self.fetch_worker
        worker = threading.Thread(target=target, daemon=True)
        worker.start()

    def fetch_worker(self) -> None:
        try:
            quotes, api_time, data_source, _etfs = fetch_market_data(
                self.timeout,
                self.verify_ssl,
                self.retries,
                self.source,
                False,
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                stock_future = executor.submit(
                    fetch_taiwan_security_quotes,
                    dict(self.tw_stock_symbols),
                    self.timeout,
                    self.retries,
                )
                etf_future = (
                    executor.submit(
                        fetch_all_etf_quotes,
                        self.timeout,
                        self.retries,
                        self.verify_ssl,
                    )
                    if self.include_etfs
                    else None
                )
                taiwan_stocks = stock_future.result()
                all_etfs = etf_future.result() if etf_future else None
            desired_mode = chart_mode_for_market_time(api_time)
            histories: dict[str, list[MarketCandle]] = {}
            grains: dict[str, str] = {}
            if desired_mode != self.chart_mode:
                for name in INDICES.values():
                    symbol = YAHOO_SYMBOL_BY_NAME.get(name)
                    if not symbol:
                        continue
                    try:
                        candles, grain = fetch_yahoo_candles(
                            symbol,
                            self.timeout,
                            self.retries,
                            self.history_limit,
                            prefer_intraday=desired_mode == "intraday",
                        )
                        histories[name] = candles
                        grains[name] = grain
                    except RuntimeError:
                        if desired_mode == "intraday":
                            histories[name] = []
                            grains[name] = "5 分 K（即時建立）"
                        else:
                            histories[name] = []
                            grains[name] = "日 K（即時建立）"
            self.result_queue.put(
                (
                    "data",
                    (
                        quotes,
                        api_time,
                        data_source,
                        all_etfs,
                        taiwan_stocks,
                        histories,
                        grains,
                        desired_mode,
                    ),
                )
            )
        except Exception as exc:
            self.result_queue.put(("error", exc))

    def fetch_us_worker(self) -> None:
        try:
            index_symbols = {
                symbol: name for name, symbol in US_INDEX_SYMBOLS.items()
            }
            requested_symbols = {
                **self.us_stock_symbols,
                **TAIWAN_ADR_SYMBOLS,
                **index_symbols,
            }
            all_quotes = fetch_yahoo_security_quotes(
                requested_symbols,
                self.timeout,
                self.retries,
            )
            quote_by_symbol = {quote.symbol: quote for quote in all_quotes}
            index_quotes = [
                quote_by_symbol[symbol] for symbol in index_symbols
            ]
            adr_quotes = [
                quote_by_symbol[symbol] for symbol in TAIWAN_ADR_SYMBOLS
            ]
            stock_quotes = [
                quote_by_symbol[symbol] for symbol in self.us_stock_symbols
            ]

            desired_mode = chart_mode_for_us_quotes(index_quotes)
            histories: dict[str, list[MarketCandle]] = {}
            grains: dict[str, str] = {}
            reload_history = (
                desired_mode != self.us_chart_mode
                or not self.us_history_loaded_at
                or time.monotonic() - self.us_history_loaded_at >= 300
            )
            if reload_history:
                with ThreadPoolExecutor(max_workers=len(US_CHART_NAMES)) as executor:
                    futures = {}
                    for name, symbol in US_INDEX_SYMBOLS.items():
                        future = executor.submit(
                            fetch_yahoo_candles,
                            symbol,
                            self.timeout,
                            self.retries,
                            self.history_limit,
                            desired_mode == "intraday",
                        )
                        futures[future] = (name, "美股")
                    for future in as_completed(futures):
                        name, market_type = futures[future]
                        try:
                            candles, grain = future.result()
                        except (OSError, RuntimeError, ValueError):
                            histories[name] = []
                            grains[name] = (
                                "5 分 K（暫無資料）"
                                if market_type == "美股"
                                and desired_mode == "intraday"
                                else "日 K（暫無資料）"
                            )
                        else:
                            histories[name] = candles
                            grains[name] = grain

            self.result_queue.put(
                (
                    "us_data",
                    (
                        index_quotes,
                        adr_quotes,
                        stock_quotes,
                        histories,
                        grains,
                        desired_mode,
                        reload_history,
                    ),
                )
            )
        except Exception as exc:
            self.result_queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                completed_page = self.loading_page
                self.loading = False
                self.loading_page = ""
                if kind == "data":
                    (
                        quotes,
                        api_time,
                        data_source,
                        etfs,
                        taiwan_stocks,
                        histories,
                        grains,
                        desired_mode,
                    ) = payload
                    self.update_market(
                        quotes,
                        api_time,
                        data_source,
                        etfs,
                        taiwan_stocks,
                        histories,
                        grains,
                        desired_mode,
                    )
                elif kind == "us_data":
                    self.update_us_market(*payload)
                else:
                    self.status_text.set(f"更新失敗：{payload}")
                delay = 0 if completed_page != self.active_page else self.interval * 1000
                self.schedule_refresh(delay)
        except queue.Empty:
            pass

        if not self.closed:
            self.root.after(200, self.process_results)

    def update_market(
        self,
        quotes: list[IndexQuote],
        api_time: str,
        data_source: str,
        etfs: list[EtfMarketRow] | None,
        taiwan_stocks: dict[str, SecurityQuote],
        histories: dict[str, list[MarketCandle]] | None = None,
        grains: dict[str, str] | None = None,
        desired_mode: str | None = None,
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_quote_status = f"本機時間：{now}｜行情時間：{api_time or '--'}"
        self.last_quote_source = data_source
        self.status_text.set(f"本機時間：{now}｜行情時間：{api_time or '--'}")
        self.source_text.set(f"來源：{data_source}｜更新頻率：{self.interval} 秒")

        if histories is not None:
            for name, candles in histories.items():
                self.candles[name] = candles[-self.history_limit:]
            self.candle_grains.update(grains or {})
        if desired_mode:
            self.chart_mode = desired_mode

        market_date = api_time[:8] if len(api_time) >= 8 and api_time[:8].isdigit() else ""
        for quote in quotes:
            self.index_previous_close[quote.name] = quote.previous_close
            if quote.price is not None:
                self.update_live_candle(quote, market_date)

            labels = self.cards.get(quote.name)
            if not labels:
                continue
            labels["value"].configure(text=fmt_number(quote.price))
            change_style = "Up.TLabel" if (quote.change or 0) >= 0 else "Down.TLabel"
            labels["change"].configure(
                text=f"{fmt_signed(quote.change)} / {fmt_signed(quote.change_percent)}%",
                style=change_style,
            )
            labels["time"].configure(text=f"時間 {quote.market_time}")

        self.update_etfs(etfs)
        self.update_taiwan_stocks(taiwan_stocks)
        self.draw_chart()

    def update_us_market(
        self,
        index_quotes: list[SecurityQuote],
        adr_quotes: list[SecurityQuote],
        stock_quotes: list[SecurityQuote],
        histories: dict[str, list[MarketCandle]],
        grains: dict[str, str],
        desired_mode: str,
        history_reloaded: bool,
    ) -> None:
        available_times = [
            quote.market_time
            for quote in index_quotes
            if quote.market_time != "--"
        ]
        market_time = max(available_times) if available_times else "--"
        self.last_quote_status = f"美股行情時間：{market_time}"
        self.last_quote_source = "Yahoo 股市批次報價／Yahoo Finance K 線"
        self.us_index_rows = {quote.symbol: quote for quote in index_quotes}
        self.adr_rows = {quote.symbol: quote for quote in adr_quotes}
        self.us_stock_rows = {quote.symbol: quote for quote in stock_quotes}
        if history_reloaded:
            for name in US_CHART_NAMES:
                self.us_candles[name] = histories.get(name, [])[-self.history_limit:]
            self.us_candle_grains.update(grains)
            self.us_history_loaded_at = time.monotonic()
        self.us_chart_mode = desired_mode

        for quote in index_quotes:
            self.us_previous_close[quote.name] = quote.previous_close
            if quote.price is not None:
                self.update_us_live_candle(quote.name, quote)
            labels = self.us_cards.get(quote.name)
            if not labels:
                continue
            labels["value"].configure(text=fmt_number(quote.price))
            labels["change"].configure(
                text=(
                    f"{fmt_signed(quote.change)} / "
                    f"{fmt_signed(quote.change_percent)}%"
                ),
                style=(
                    "Up.TLabel"
                    if (quote.change or 0) >= 0
                    else "Down.TLabel"
                ),
            )
            labels["time"].configure(text=f"時間 {quote.market_time}")

        self.update_security_tree(self.adr_tree, adr_quotes)
        self.update_security_tree(self.us_stock_tree, stock_quotes)
        mode_text = "盤中 5 分 K" if desired_mode == "intraday" else "日 K"
        self.status_text.set(
            f"美股行情時間：{market_time}｜K 線模式：{mode_text}"
        )
        self.source_text.set(
            f"來源：Yahoo 股市批次報價／Yahoo Finance K 線｜更新頻率：{self.interval} 秒"
        )
        self.draw_us_charts()

    def update_security_tree(
        self,
        tree: ttk.Treeview,
        quotes: list[SecurityQuote],
    ) -> None:
        for item in tree.get_children():
            tree.delete(item)
        for quote in quotes:
            tag = ""
            if quote.change is not None:
                tag = "up" if quote.change >= 0 else "down"
            tree.insert(
                "",
                "end",
                iid=quote.symbol,
                values=(
                    quote.symbol,
                    quote.name,
                    fmt_number(quote.price),
                    fmt_signed(quote.change),
                    f"{fmt_signed(quote.change_percent)}%",
                    fmt_number(quote.volume, 0),
                    quote.market_time,
                ),
                tags=(tag,) if tag else (),
            )

    def update_us_live_candle(
        self,
        name: str,
        quote: SecurityQuote,
    ) -> None:
        if quote.price is None:
            return
        candles = self.us_candles[name]
        grain = self.us_candle_grains.get(name, "即時 K")
        moment = (
            datetime.fromtimestamp(quote.market_timestamp)
            if quote.market_timestamp
            else datetime.now()
        )
        if grain.startswith("日 K"):
            label = moment.strftime("%Y-%m-%d")
            open_value = quote.open_price or (
                candles[-1].close if candles else quote.price
            )
            live = MarketCandle(
                int(moment.timestamp()),
                label,
                open_value,
                max(quote.high or quote.price, open_value, quote.price),
                min(quote.low or quote.price, open_value, quote.price),
                quote.price,
            )
            if candles and candles[-1].label == label:
                candles[-1] = live
            else:
                candles.append(live)
        else:
            interval_match = re.search(r"(\d+)\s*分", grain)
            interval_minutes = int(interval_match.group(1)) if interval_match else 5
            minute = moment.minute - moment.minute % interval_minutes
            bucket = moment.replace(minute=minute, second=0, microsecond=0)
            label = bucket.strftime("%m/%d %H:%M")
            if candles and candles[-1].label == label:
                current = candles[-1]
                candles[-1] = MarketCandle(
                    current.timestamp,
                    current.label,
                    current.open,
                    max(current.high, quote.price),
                    min(current.low, quote.price),
                    quote.price,
                )
            else:
                open_value = (
                    candles[-1].close
                    if candles
                    else quote.open_price or quote.price
                )
                candles.append(
                    MarketCandle(
                        int(bucket.timestamp()),
                        label,
                        open_value,
                        max(open_value, quote.price),
                        min(open_value, quote.price),
                        quote.price,
                    )
                )
            self.us_candle_grains.setdefault(name, f"{interval_minutes} 分 K")
        del candles[:-self.history_limit]

    def update_live_candle(self, quote: IndexQuote, market_date: str) -> None:
        if quote.price is None:
            return
        candles = self.candles[quote.name]
        grain = self.candle_grains.get(quote.name, "即時 K")
        if grain.startswith("日 K"):
            label = (
                f"{market_date[:4]}-{market_date[4:6]}-{market_date[6:8]}"
                if len(market_date) == 8
                else datetime.now().strftime("%Y-%m-%d")
            )
            open_value = quote.open_price or (candles[-1].close if candles else quote.price)
            high_value = quote.high or max(open_value, quote.price)
            low_value = quote.low or min(open_value, quote.price)
            live = MarketCandle(
                int(datetime.now().timestamp()),
                label,
                open_value,
                max(high_value, open_value, quote.price),
                min(low_value, open_value, quote.price),
                quote.price,
            )
            if candles and candles[-1].label == label:
                candles[-1] = live
            else:
                candles.append(live)
        else:
            now = datetime.now()
            interval_match = re.search(r"(\d+)\s*分", grain)
            interval_minutes = int(interval_match.group(1)) if interval_match else 5
            minute = now.minute - now.minute % interval_minutes
            label = now.replace(minute=minute, second=0).strftime("%m/%d %H:%M")
            if candles and candles[-1].label == label:
                current = candles[-1]
                candles[-1] = MarketCandle(
                    current.timestamp,
                    current.label,
                    current.open,
                    max(current.high, quote.price),
                    min(current.low, quote.price),
                    quote.price,
                )
            else:
                open_value = candles[-1].close if candles else quote.open_price or quote.price
                candles.append(
                    MarketCandle(
                        int(now.timestamp()),
                        label,
                        open_value,
                        max(open_value, quote.price),
                        min(open_value, quote.price),
                        quote.price,
                    )
                )
            self.candle_grains.setdefault(quote.name, f"{interval_minutes} 分 K")
        del candles[:-self.history_limit]

    def update_taiwan_stocks(
        self,
        stocks: dict[str, SecurityQuote],
    ) -> None:
        for item in self.tw_stock_tree.get_children():
            self.tw_stock_tree.delete(item)
        self.tw_stock_rows = dict(stocks)
        for code in self.tw_stock_symbols:
            quote = stocks.get(code)
            if not quote:
                continue
            if self.tw_stock_symbols[code] == code and quote.name != code:
                self.tw_stock_symbols[code] = quote.name
            tag = ""
            if quote.change is not None:
                tag = "up" if quote.change >= 0 else "down"
            source = (
                quote.source
                if quote.price is not None
                else "Yahoo 股市（暫無資料）"
            )
            self.tw_stock_tree.insert(
                "",
                "end",
                iid=code,
                values=(
                    code,
                    quote.name,
                    fmt_number(quote.price),
                    fmt_signed(quote.change),
                    f"{fmt_signed(quote.change_percent)}%",
                    fmt_number(quote.volume, 0),
                    quote.market_time,
                    source,
                ),
                tags=(tag,) if tag else (),
            )

    def open_selected_taiwan_stock(self, _event: tk.Event) -> None:
        selection = self.tw_stock_tree.selection()
        if not selection:
            return
        code = selection[0]
        quote = self.tw_stock_rows.get(code)
        if not quote:
            return
        TaiwanStockDetailWindow(
            parent=self.root,
            code=code,
            initial=quote,
            interval=self.interval,
            timeout=self.timeout,
            retries=self.retries,
            theme_getter=lambda: self.theme,
        )

    def update_etfs(self, etfs: list[EtfMarketRow] | None) -> None:
        for item in self.etf_tree.get_children():
            self.etf_tree.delete(item)
        self.etf_rows.clear()
        if not etfs:
            return
        for etf in etfs:
            self.etf_rows[etf.code] = etf
            tag = "up" if (etf.change or 0) >= 0 else "down"
            self.etf_tree.insert(
                "",
                "end",
                iid=etf.code,
                values=(
                    etf.code,
                    etf.name,
                    fmt_number(etf.market_value, 0),
                    fmt_number(etf.price),
                    fmt_signed(etf.change),
                    f"{fmt_signed(etf.change_percent)}%",
                    fmt_number(etf.volume, 0),
                    etf.market_time,
                    etf.source,
                ),
                tags=(tag,),
            )

    def open_selected_etf(self, _event: tk.Event) -> None:
        selection = self.etf_tree.selection()
        if not selection:
            return
        code = selection[0]
        row = self.etf_rows.get(code)
        if not row:
            return
        EtfDetailWindow(
            parent=self.root,
            initial=row,
            interval=self.interval,
            timeout=self.timeout,
            retries=self.retries,
            verify_ssl=self.verify_ssl,
            theme_getter=lambda: self.theme,
        )

    def draw_chart(self) -> None:
        for name in INDICES.values():
            self.draw_one_index_chart(name)

    def draw_one_index_chart(self, name: str) -> None:
        canvas = self.index_canvases.get(name)
        if not canvas:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 160)
        has_background = self.draw_canvas_background(canvas, width, height)
        self.draw_index_panel(canvas, name, 0, 0, width, height, has_background)

    def draw_index_panel(
        self,
        canvas: tk.Canvas,
        name: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        has_background: bool = False,
    ) -> None:
        theme = self.theme
        margin = 4
        x0 += margin
        y0 += margin
        x1 -= margin
        y1 -= margin
        all_candles = self.candles.get(name, [])
        previous_close = self.index_previous_close.get(name)
        latest_value = all_candles[-1].close if all_candles else None
        color = trend_color(theme, latest_value, previous_close)
        canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            fill="" if has_background else theme["surface_alt"],
            outline=theme["line"],
        )
        canvas.create_text(
            x0 + 12,
            y0 + 18,
            text=name,
            fill=theme["text"],
            anchor="w",
            font=("Microsoft JhengHei UI", 10, "bold"),
        )
        canvas.create_text(
            x0 + 12,
            y0 + 36,
            text=(
                f"{pct_text(latest_value, previous_close)}  "
                f"{self.candle_grains.get(name, 'K 線')}"
            ),
            fill=color,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )

        latest = fmt_number(latest_value) if latest_value is not None else "--"
        canvas.create_text(
            x1 - 12,
            y0 + 18,
            text=latest,
            fill=color,
            anchor="e",
            font=("Segoe UI", 12, "bold"),
        )
        canvas.create_text(
            x1 - 100,
            y0 + 36,
            text="MA5",
            fill=theme["ma5"],
            anchor="e",
            font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            x1 - 58,
            y0 + 36,
            text="MA10",
            fill=theme["ma10"],
            anchor="e",
            font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            x1 - 12,
            y0 + 36,
            text="MA20",
            fill=theme["ma20"],
            anchor="e",
            font=("Segoe UI", 8, "bold"),
        )

        plot_left = x0 + 10
        plot_right = x1 - 72
        plot_top = y0 + 54
        plot_bottom = y1 - 28
        plot_w = max(1, plot_right - plot_left)
        max_visible = max(12, min(self.history_limit, int(plot_w / 7)))
        candles = all_candles[-max_visible:]
        draw_candlestick_plot(
            canvas,
            theme,
            candles,
            previous_close,
            plot_left,
            plot_top,
            plot_right,
            plot_bottom,
        )

    def draw_us_charts(self) -> None:
        for name in US_CHART_NAMES:
            self.draw_one_us_chart(name)

    def draw_one_us_chart(self, name: str) -> None:
        canvas = self.us_chart_canvases.get(name)
        if not canvas:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 125)
        theme = self.theme
        has_background = self.draw_canvas_background(canvas, width, height, theme)
        x0, y0, x1, y1 = 4, 4, width - 4, height - 4
        all_candles = self.us_candles.get(name, [])
        previous_close = self.us_previous_close.get(name)
        latest_value = all_candles[-1].close if all_candles else None
        color = trend_color(theme, latest_value, previous_close)

        canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            fill="" if has_background else theme["surface_alt"],
            outline=theme["line"],
        )
        canvas.create_text(
            x0 + 10,
            y0 + 16,
            text=name,
            fill=theme["text"],
            anchor="w",
            font=("Microsoft JhengHei UI", 9, "bold"),
        )
        canvas.create_text(
            x0 + 10,
            y0 + 33,
            text=(
                f"{pct_text(latest_value, previous_close)}  "
                f"{self.us_candle_grains.get(name, 'K 線')}"
            ),
            fill=color,
            anchor="w",
            font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            x1 - 10,
            y0 + 16,
            text=fmt_number(latest_value),
            fill=color,
            anchor="e",
            font=("Segoe UI", 11, "bold"),
        )
        for x, period in (
            (x1 - 92, 5),
            (x1 - 52, 10),
            (x1 - 10, 20),
        ):
            canvas.create_text(
                x,
                y0 + 33,
                text=f"MA{period}",
                fill=theme[f"ma{period}"],
                anchor="e",
                font=("Segoe UI", 7, "bold"),
            )

        plot_left = x0 + 8
        plot_right = x1 - 68
        plot_top = y0 + 48
        plot_bottom = y1 - 24
        plot_width = max(1, plot_right - plot_left)
        max_visible = max(12, min(self.history_limit, int(plot_width / 7)))
        draw_candlestick_plot(
            canvas,
            theme,
            all_candles[-max_visible:],
            previous_close,
            plot_left,
            plot_top,
            plot_right,
            plot_bottom,
        )


class TaiwanFuturesWindow:
    """Yahoo-powered live view for four Taiwan near-month index futures."""

    def __init__(
        self,
        parent: tk.Tk,
        timeout: int,
        retries: int,
        refresh_interval: int,
        history_limit: int,
        theme_getter,
        background_renderer=None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.refresh_interval = refresh_interval
        self.history_limit = history_limit
        self.theme_getter = theme_getter
        self.background_renderer = background_renderer
        self.candles: dict[str, list[MarketCandle]] = defaultdict(list)
        self.grains: dict[str, str] = {}
        self.previous_close: dict[str, float | None] = {}
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loading = False
        self.closed = False
        self.refresh_job: str | None = None

        self.window = tk.Toplevel(parent)
        self.window.title("台指近月期貨｜四大指數")
        self.window.geometry("1040x720")
        self.window.minsize(760, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.status_text = tk.StringVar(value="正在載入 Yahoo 台灣期貨行情...")
        self.build_ui()
        self.apply_theme()
        self.refresh()
        self.window.after(200, self.process_results)

    def build_ui(self) -> None:
        self.frame = ttk.Frame(self.window, style="TFrame", padding=16)
        self.frame.pack(fill="both", expand=True)

        header = ttk.Frame(self.frame, style="Header.TFrame", padding=(18, 14))
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header,
            text="台指近月期貨｜四大指數",
            style="Header.TLabel",
            font=("Microsoft JhengHei UI", 20, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text=(
                "台指、小型台指、電子、金融近月 5 分 K｜"
                f"四圖同步更新：每 {self.refresh_interval} 秒｜來源：Yahoo 股市"
            ),
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            header,
            textvariable=self.status_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            header,
            text="同步更新四圖",
            style="Flat.TButton",
            command=self.refresh,
        ).grid(row=0, column=1, rowspan=3, sticky="e")
        header.columnconfigure(0, weight=1)

        self.chart_panel = ttk.Frame(
            self.frame,
            style="Card.TFrame",
            padding=12,
        )
        self.chart_panel.pack(fill="both", expand=True)
        self.canvases: dict[str, tk.Canvas] = {}
        for index, name in enumerate(TAIWAN_FUTURE_CHART_NAMES):
            row, column = divmod(index, 2)
            canvas = tk.Canvas(
                self.chart_panel,
                highlightthickness=0,
                height=240,
                width=440,
            )
            canvas.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 5, 5 if column == 0 else 0),
                pady=(0 if row == 0 else 5, 5 if row == 0 else 0),
            )
            canvas.bind(
                "<Configure>",
                lambda _event, chart_name=name: self.draw_one_chart(chart_name),
            )
            self.canvases[name] = canvas
        for index in range(2):
            self.chart_panel.columnconfigure(index, weight=1, uniform="futures")
            self.chart_panel.rowconfigure(index, weight=1, uniform="futures")

    def apply_theme(self) -> None:
        theme = self.theme_getter()
        self.window.configure(bg=theme["bg"])
        for canvas in self.canvases.values():
            canvas.configure(bg=theme["surface"])
        self.draw_charts()

    def close(self) -> None:
        self.closed = True
        if self.refresh_job:
            try:
                self.window.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        self.window.destroy()

    def schedule_refresh(self) -> None:
        if self.closed:
            return
        if self.refresh_job:
            try:
                self.window.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        self.refresh_job = self.window.after(
            self.refresh_interval * 1000,
            self.refresh,
        )

    def refresh(self) -> None:
        if self.closed or self.loading:
            return
        if self.refresh_job:
            try:
                self.window.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
            self.refresh_job = None
        self.loading = True
        self.status_text.set("正在同步更新四張 Yahoo 台灣期貨 5 分 K...")
        threading.Thread(target=self.fetch_worker, daemon=True).start()

    def fetch_worker(self) -> None:
        try:
            payload = fetch_yahoo_tw_futures(
                YAHOO_TW_FUTURE_SYMBOLS,
                self.timeout,
                self.retries,
                self.history_limit,
            )
            self.queue.put(("data", payload))
        except Exception as exc:
            self.queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.loading = False
                if kind == "data":
                    self.update_data(*payload)
                else:
                    self.status_text.set(f"更新失敗：{payload}")
                    self.schedule_refresh()
        except queue.Empty:
            pass
        if not self.closed:
            self.window.after(200, self.process_results)

    def update_data(
        self,
        histories: dict[str, list[MarketCandle]],
        grains: dict[str, str],
        previous_closes: dict[str, float | None],
        refreshed_times: list[str],
        errors: list[str],
    ) -> None:
        for name in TAIWAN_FUTURE_CHART_NAMES:
            candles = histories.get(name, [])
            if candles:
                self.candles[name] = candles[-self.history_limit:]
            self.grains[name] = grains.get(name, "Yahoo 5 分 K")
            previous_close = previous_closes.get(name)
            if previous_close is not None:
                self.previous_close[name] = previous_close
            if candles:
                self.previous_close.setdefault(
                    name,
                    candles[-2].close if len(candles) >= 2 else candles[-1].open,
                )
        available_times = [
            candles[-1].label
            for candles in self.candles.values()
            if candles
        ]
        latest_candle = max(available_times) if available_times else "--"
        yahoo_time = "--"
        if refreshed_times:
            latest_refresh = max(refreshed_times)
            try:
                yahoo_time = datetime.fromisoformat(
                    latest_refresh.replace("Z", "+00:00")
                ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                yahoo_time = latest_refresh
        suffix = f"｜{len(errors)} 項暫無資料" if errors else ""
        self.status_text.set(
            f"Yahoo 行情時間：{yahoo_time}｜最新 K 線：{latest_candle}｜"
            f"本機更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f"｜下次自動更新：約 {self.refresh_interval} 秒後{suffix}"
        )
        self.draw_charts()
        self.schedule_refresh()

    def draw_charts(self) -> None:
        for name in TAIWAN_FUTURE_CHART_NAMES:
            self.draw_one_chart(name)

    def draw_one_chart(self, name: str) -> None:
        canvas = self.canvases.get(name)
        if not canvas:
            return
        canvas.delete("all")
        theme = self.theme_getter()
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 220)
        has_background = bool(
            self.background_renderer
            and self.background_renderer(canvas, width, height, theme)
        )
        x0, y0, x1, y1 = 4, 4, width - 4, height - 4
        all_candles = self.candles.get(name, [])
        previous_close = self.previous_close.get(name)
        latest_value = all_candles[-1].close if all_candles else None
        color = trend_color(theme, latest_value, previous_close)

        canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            fill="" if has_background else theme["surface_alt"],
            outline=theme["line"],
        )
        canvas.create_text(
            x0 + 12,
            y0 + 18,
            text=name,
            fill=theme["text"],
            anchor="w",
            font=("Microsoft JhengHei UI", 10, "bold"),
        )
        canvas.create_text(
            x0 + 12,
            y0 + 38,
            text=(
                f"{pct_text(latest_value, previous_close)}  "
                f"{self.grains.get(name, '日 K')}"
            ),
            fill=color,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )
        canvas.create_text(
            x1 - 12,
            y0 + 18,
            text=fmt_number(latest_value),
            fill=color,
            anchor="e",
            font=("Segoe UI", 12, "bold"),
        )
        for x, period in (
            (x1 - 102, 5),
            (x1 - 58, 10),
            (x1 - 12, 20),
        ):
            canvas.create_text(
                x,
                y0 + 38,
                text=f"MA{period}",
                fill=theme[f"ma{period}"],
                anchor="e",
                font=("Segoe UI", 8, "bold"),
            )

        plot_left = x0 + 10
        plot_right = x1 - 72
        plot_top = y0 + 54
        plot_bottom = y1 - 28
        if not all_candles:
            canvas.create_text(
                (plot_left + plot_right) / 2,
                (plot_top + plot_bottom) / 2,
                text=self.grains.get(name, "正在載入 Yahoo 期貨行情…"),
                fill=theme["muted"],
                font=("Microsoft JhengHei UI", 10),
            )
            return
        plot_width = max(1, plot_right - plot_left)
        max_visible = max(12, min(self.history_limit, int(plot_width / 7)))
        draw_candlestick_plot(
            canvas,
            theme,
            all_candles[-max_visible:],
            previous_close,
            plot_left,
            plot_top,
            plot_right,
            plot_bottom,
        )


class MarketOverviewWindow:
    def __init__(
        self,
        parent: tk.Tk,
        timeout: int,
        verify_ssl: bool,
        refresh_interval: int,
        theme_getter,
    ) -> None:
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.refresh_interval = refresh_interval
        self.theme_getter = theme_getter
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loading = False
        self.closed = False
        self.breadth: MarketBreadth | None = None

        self.window = tk.Toplevel(parent)
        self.window.title("市場籌碼｜三大法人與漲跌家數")
        self.window.geometry("900x680")
        self.window.minsize(700, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.status_text = tk.StringVar(value="正在載入最新資料...")
        self.institution_date_text = tk.StringVar(value="三大法人：載入中")
        self.breadth_date_text = tk.StringVar(value="市場漲跌：載入中")
        self.build_ui()
        self.apply_theme()
        self.refresh()
        self.window.after(200, self.process_results)

    def build_ui(self) -> None:
        self.frame = ttk.Frame(self.window, style="TFrame", padding=16)
        self.frame.pack(fill="both", expand=True)

        header = ttk.Frame(self.frame, style="Header.TFrame", padding=(18, 14))
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header,
            text="市場籌碼",
            style="Header.TLabel",
            font=("Microsoft JhengHei UI", 20, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            textvariable=self.status_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            header,
            text="立即更新",
            style="Flat.TButton",
            command=self.refresh,
        ).grid(row=0, column=1, rowspan=2, sticky="e")
        header.columnconfigure(0, weight=1)

        institution_panel = ttk.Frame(self.frame, style="Card.TFrame", padding=16)
        institution_panel.pack(fill="x", pady=(0, 10))
        institution_header = ttk.Frame(institution_panel, style="Card.TFrame")
        institution_header.pack(fill="x")
        ttk.Label(
            institution_header,
            text="三大法人買賣金額",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).pack(side="left")
        ttk.Label(
            institution_header,
            textvariable=self.institution_date_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).pack(side="right")

        columns = ("institution", "buy", "sell", "net")
        self.institution_tree = ttk.Treeview(
            institution_panel,
            columns=columns,
            show="headings",
            height=3,
        )
        headings = {
            "institution": "法人",
            "buy": "買進金額（億）",
            "sell": "賣出金額（億）",
            "net": "買賣超（億）",
        }
        widths = {"institution": 150, "buy": 170, "sell": 170, "net": 170}
        for column in columns:
            self.institution_tree.heading(column, text=headings[column])
            self.institution_tree.column(column, width=widths[column], anchor="e")
        self.institution_tree.column("institution", anchor="w")
        self.institution_tree.pack(fill="x", pady=(8, 0))

        breadth_panel = ttk.Frame(self.frame, style="Card.TFrame", padding=16)
        breadth_panel.pack(fill="both", expand=True)
        breadth_header = ttk.Frame(breadth_panel, style="Card.TFrame")
        breadth_header.pack(fill="x")
        ttk.Label(
            breadth_header,
            text="全台上市＋上櫃漲跌家數",
            style="CardTitle.TLabel",
            font=("Microsoft JhengHei UI", 13, "bold"),
        ).pack(side="left")
        ttk.Label(
            breadth_header,
            textvariable=self.breadth_date_text,
            style="Muted.TLabel",
            font=("Microsoft JhengHei UI", 9),
        ).pack(side="right")
        self.breadth_canvas = tk.Canvas(
            breadth_panel,
            height=280,
            highlightthickness=0,
        )
        self.breadth_canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.breadth_canvas.bind("<Configure>", lambda _event: self.draw_breadth_chart())

    def apply_theme(self) -> None:
        theme = self.theme_getter()
        self.window.configure(bg=theme["bg"])
        self.institution_tree.tag_configure("up", foreground=theme["up"])
        self.institution_tree.tag_configure("down", foreground=theme["down"])
        self.breadth_canvas.configure(bg=theme["surface"])
        self.draw_breadth_chart()

    def close(self) -> None:
        self.closed = True
        self.window.destroy()

    def refresh(self) -> None:
        if self.closed or self.loading:
            return
        self.loading = True
        self.status_text.set("正在更新三大法人與市場漲跌家數...")
        threading.Thread(target=self.fetch_worker, daemon=True).start()

    def fetch_worker(self) -> None:
        try:
            institutions = fetch_institution_trades(self.timeout, self.verify_ssl)
            breadth = fetch_market_breadth(self.timeout, self.verify_ssl)
            self.queue.put(("data", (institutions, breadth)))
        except Exception as exc:
            self.queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.loading = False
                if kind == "data":
                    institutions, breadth = payload
                    self.update_data(institutions, breadth)
                else:
                    self.status_text.set(f"更新失敗：{payload}")
        except queue.Empty:
            pass
        if not self.closed:
            self.window.after(200, self.process_results)

    @staticmethod
    def format_date(value: str) -> str:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else value or "--"

    def update_data(
        self,
        institutions: InstitutionSnapshot,
        breadth: MarketBreadth,
    ) -> None:
        self.status_text.set(
            f"本機更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜每 {self.refresh_interval} 秒更新"
        )
        self.institution_date_text.set(
            f"最新公布：{self.format_date(institutions.market_date)}｜單位：億元"
        )
        for item in self.institution_tree.get_children():
            self.institution_tree.delete(item)
        for row in institutions.rows:
            tag = "up" if row.net_amount >= 0 else "down"
            self.institution_tree.insert(
                "",
                "end",
                values=(
                    row.name,
                    fmt_number(row.buy_amount / 100_000_000),
                    fmt_number(row.sell_amount / 100_000_000),
                    fmt_signed(row.net_amount / 100_000_000),
                ),
                tags=(tag,),
            )

        self.breadth = breadth
        twse_date = self.format_date(breadth.twse_date)
        tpex_date = self.format_date(breadth.tpex_date)
        if twse_date == tpex_date:
            self.breadth_date_text.set(f"資料日：{twse_date}")
        else:
            self.breadth_date_text.set(f"上市：{twse_date}｜上櫃：{tpex_date}")
        self.apply_theme()
        self.window.after(self.refresh_interval * 1000, self.refresh)

    def draw_breadth_chart(self) -> None:
        canvas = self.breadth_canvas
        canvas.delete("all")
        theme = self.theme_getter()
        width = max(canvas.winfo_width(), 620)
        height = max(canvas.winfo_height(), 260)
        left, right, top, bottom = 58, 28, 26, 72
        plot_width = width - left - right
        plot_height = height - top - bottom
        canvas.create_rectangle(
            left,
            top,
            width - right,
            height - bottom,
            fill=theme["surface_alt"],
            outline=theme["line"],
        )
        if self.breadth is None:
            canvas.create_text(
                width / 2,
                height / 2,
                text="正在載入市場漲跌家數...",
                fill=theme["muted"],
                font=("Microsoft JhengHei UI", 11),
            )
            return

        breadth = self.breadth
        totals = breadth.totals
        labels = ("上漲", "持平", "下跌")
        colors = (theme["up"], theme["muted"], theme["down"])
        splits = (
            (breadth.twse_up, breadth.tpex_up),
            (breadth.twse_flat, breadth.tpex_flat),
            (breadth.twse_down, breadth.tpex_down),
        )
        maximum = max(totals) or 1
        group_width = plot_width / 3
        bar_width = min(120, group_width * 0.48)
        for index, (label, total, color, split) in enumerate(zip(labels, totals, colors, splits)):
            center_x = left + group_width * (index + 0.5)
            bar_height = plot_height * total / maximum
            y0 = top + plot_height - bar_height
            canvas.create_rectangle(
                center_x - bar_width / 2,
                y0,
                center_x + bar_width / 2,
                top + plot_height,
                fill=color,
                outline="",
            )
            canvas.create_text(
                center_x,
                max(top + 12, y0 - 12),
                text=f"{total:,}",
                fill=color,
                font=("Segoe UI", 13, "bold"),
            )
            canvas.create_text(
                center_x,
                height - bottom + 20,
                text=label,
                fill=theme["text"],
                font=("Microsoft JhengHei UI", 11, "bold"),
            )
            canvas.create_text(
                center_x,
                height - bottom + 43,
                text=f"上市 {split[0]:,}｜上櫃 {split[1]:,}",
                fill=theme["muted"],
                font=("Microsoft JhengHei UI", 9),
            )


class EtfDetailWindow:
    def __init__(
        self,
        parent: tk.Tk,
        initial: EtfMarketRow,
        interval: int,
        timeout: int,
        retries: int,
        verify_ssl: bool,
        theme_getter,
    ) -> None:
        self.code = initial.code
        self.interval = interval
        self.timeout = timeout
        self.retries = retries
        self.verify_ssl = verify_ssl
        self.theme_getter = theme_getter
        self.history_limit = 80
        self.candles: list[MarketCandle] = []
        self.candle_grain = (
            "5 分 K（即時建立）"
            if is_taiwan_market_open()
            else "日 K（即時建立）"
        )
        self.chart_mode = ""
        self.previous_close: float | None = initial.previous_close
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loading = False
        self.closed = False
        self.detail_layout_stacked: bool | None = None

        self.window = tk.Toplevel(parent)
        self.window.title(f"{initial.code} {initial.name}")
        self.window.geometry("760x560")
        self.window.minsize(520, 420)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.title_text = tk.StringVar(value=f"{initial.code}  {initial.name}")
        self.price_text = tk.StringVar(value="--")
        self.change_text = tk.StringVar(value="--")
        self.detail_text = tk.StringVar(value="正在載入 ETF 詳細資訊...")
        self.source_text = tk.StringVar(value="")

        self.build_ui()
        self.apply_theme()
        self.window.bind("<Configure>", self.on_resize)
        self.update_quote(initial)
        self.refresh()
        self.window.after(200, self.process_results)

    def build_ui(self) -> None:
        self.frame = tk.Frame(self.window, padx=18, pady=16)
        self.frame.pack(fill="both", expand=True)

        self.top_frame = tk.Frame(self.frame)
        self.top_frame.pack(fill="x")
        self.left_frame = tk.Frame(self.top_frame)
        self.left_frame.grid(row=0, column=0, sticky="nsew")
        self.title_label = tk.Label(self.left_frame, textvariable=self.title_text, font=("Microsoft JhengHei UI", 17, "bold"), anchor="w")
        self.title_label.pack(anchor="w")
        self.source_label = tk.Label(self.left_frame, textvariable=self.source_text, font=("Microsoft JhengHei UI", 10), anchor="w")
        self.source_label.pack(anchor="w", pady=(4, 0))

        self.right_frame = tk.Frame(self.top_frame)
        self.right_frame.grid(row=0, column=1, sticky="ne")
        self.price_label = tk.Label(self.right_frame, textvariable=self.price_text, font=("Segoe UI", 28, "bold"), anchor="e")
        self.price_label.pack(anchor="e")
        self.change_label = tk.Label(self.right_frame, textvariable=self.change_text, font=("Segoe UI", 12, "bold"), anchor="e")
        self.change_label.pack(anchor="e")

        self.detail_label = tk.Label(
            self.frame,
            textvariable=self.detail_text,
            font=("Microsoft JhengHei UI", 10),
            anchor="w",
            justify="left",
        )
        self.detail_label.pack(fill="x", pady=(14, 10))

        self.canvas = tk.Canvas(self.frame, height=310, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw_chart())
        self.top_frame.columnconfigure(0, weight=1)

    def on_resize(self, event: tk.Event) -> None:
        if event.widget is self.window:
            self.layout_for_width(event.width)

    def layout_for_width(self, width: int) -> None:
        stacked = width < 620
        if self.detail_layout_stacked == stacked:
            return
        self.detail_layout_stacked = stacked
        self.left_frame.grid_forget()
        self.right_frame.grid_forget()
        if stacked:
            self.left_frame.grid(row=0, column=0, sticky="ew")
            self.right_frame.grid(row=1, column=0, sticky="w", pady=(10, 0))
            self.price_label.configure(anchor="w")
            self.change_label.configure(anchor="w")
        else:
            self.left_frame.grid(row=0, column=0, sticky="nsew")
            self.right_frame.grid(row=0, column=1, sticky="ne")
            self.price_label.configure(anchor="e")
            self.change_label.configure(anchor="e")
        self.top_frame.columnconfigure(0, weight=1)
        self.draw_chart()

    def apply_theme(self) -> None:
        theme = self.theme_getter()
        self.window.configure(bg=theme["bg"])
        for widget in [
            self.frame,
            self.top_frame,
            self.left_frame,
            self.right_frame,
            self.title_label,
            self.source_label,
            self.price_label,
            self.change_label,
            self.detail_label,
        ]:
            widget.configure(bg=theme["surface"])
        self.frame.configure(bg=theme["surface"])
        self.title_label.configure(fg=theme["text"])
        self.source_label.configure(fg=theme["muted"])
        self.price_label.configure(fg=theme["text"])
        self.detail_label.configure(fg=theme["muted"])
        self.canvas.configure(bg=theme["surface"])
        self.draw_chart()

    def close(self) -> None:
        self.closed = True
        self.window.destroy()

    def refresh(self) -> None:
        if self.closed or self.loading:
            return
        self.loading = True
        threading.Thread(target=self.fetch_worker, daemon=True).start()

    def fetch_worker(self) -> None:
        try:
            quote = fetch_one_etf_quote(
                self.code,
                self.timeout,
                self.retries,
                self.verify_ssl,
            )
            desired_mode = chart_mode_for_time()
            candles: list[MarketCandle] | None = None
            grain: str | None = None
            if desired_mode != self.chart_mode:
                for symbol in (f"{self.code}.TW", f"{self.code}.TWO"):
                    try:
                        candles, grain = fetch_yahoo_candles(
                            symbol,
                            self.timeout,
                            self.retries,
                            self.history_limit,
                            prefer_intraday=desired_mode == "intraday",
                        )
                        break
                    except RuntimeError:
                        continue
                if candles is None:
                    candles = []
                    grain = (
                        "5 分 K（即時建立）"
                        if desired_mode == "intraday"
                        else "日 K（即時建立）"
                    )
            self.queue.put(
                ("data", (quote, candles, grain, desired_mode))
            )
        except Exception as exc:
            self.queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.loading = False
                if kind == "data":
                    quote, candles, grain, desired_mode = payload
                    self.update_quote(quote, candles, grain, desired_mode)
                else:
                    self.detail_text.set(f"更新失敗：{payload}")
        except queue.Empty:
            pass

        if not self.closed:
            self.window.after(200, self.process_results)

    def update_quote(
        self,
        quote: EtfMarketRow,
        candles: list[MarketCandle] | None = None,
        grain: str | None = None,
        desired_mode: str | None = None,
    ) -> None:
        theme = self.theme_getter()
        self.previous_close = quote.previous_close
        if candles is not None:
            self.candles = candles[-self.history_limit:]
        if grain:
            self.candle_grain = grain
        if desired_mode:
            self.chart_mode = desired_mode
        if quote.price is not None:
            self.update_live_candle(quote)

        self.title_text.set(f"{quote.code}  {quote.name}")
        self.price_text.set(fmt_number(quote.price))
        self.change_text.set(f"{fmt_signed(quote.change)} / {fmt_signed(quote.change_percent)}%")
        self.change_label.configure(fg=theme["up"] if (quote.change or 0) >= 0 else theme["down"])
        self.source_text.set(
            f"來源：{quote.source}｜時間：{quote.market_time}｜{self.candle_grain}"
        )
        self.detail_text.set(
            "｜".join(
                [
                    f"資產規模 {fmt_number(quote.market_value, 0)} 億",
                    f"開盤 {fmt_number(quote.open_price)}",
                    f"最高 {fmt_number(quote.high)}",
                    f"最低 {fmt_number(quote.low)}",
                    f"成交量 {fmt_number(quote.volume, 0)}",
                    f"成交值 {fmt_number(quote.trade_value, 0)}",
                    f"成交筆數 {fmt_number(quote.transaction, 0)}",
                    f"受益人 {fmt_number(quote.holders, 0)}",
                    f"發行人 {quote.issuer or '--'}",
                    f"標的 {quote.index_name or '--'}",
                ]
            )
        )
        self.apply_theme()
        self.window.after(self.interval * 1000, self.refresh)

    def update_live_candle(self, quote: EtfMarketRow) -> None:
        if quote.price is None:
            return
        now = datetime.now()
        if self.candle_grain.startswith("日 K"):
            label = now.strftime("%Y-%m-%d")
            open_value = quote.open_price or (
                self.candles[-1].close if self.candles else quote.price
            )
            live = MarketCandle(
                int(now.timestamp()),
                label,
                open_value,
                max(quote.high or quote.price, open_value, quote.price),
                min(quote.low or quote.price, open_value, quote.price),
                quote.price,
            )
            if self.candles and self.candles[-1].label == label:
                self.candles[-1] = live
            else:
                self.candles.append(live)
        else:
            interval_match = re.search(r"(\d+)\s*分", self.candle_grain)
            interval_minutes = int(interval_match.group(1)) if interval_match else 5
            minute = now.minute - now.minute % interval_minutes
            label = now.replace(minute=minute, second=0).strftime("%m/%d %H:%M")
            if self.candles and self.candles[-1].label == label:
                current = self.candles[-1]
                self.candles[-1] = MarketCandle(
                    current.timestamp,
                    current.label,
                    current.open,
                    max(current.high, quote.price),
                    min(current.low, quote.price),
                    quote.price,
                )
            else:
                open_value = (
                    self.candles[-1].close
                    if self.candles
                    else quote.open_price or quote.price
                )
                self.candles.append(
                    MarketCandle(
                        int(now.timestamp()),
                        label,
                        open_value,
                        max(open_value, quote.price),
                        min(open_value, quote.price),
                        quote.price,
                    )
                )
        del self.candles[:-self.history_limit]

    def draw_chart(self) -> None:
        theme = self.theme_getter()
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        height = max(canvas.winfo_height(), 260)
        left, plot_right, top, bottom = 10, width - 76, 34, height - 32
        canvas.create_rectangle(
            left,
            top,
            plot_right,
            bottom,
            fill=theme["surface_alt"],
            outline=theme["line"],
        )
        canvas.create_text(
            left,
            16,
            text=self.candle_grain,
            fill=theme["muted"],
            anchor="w",
            font=("Microsoft JhengHei UI", 9, "bold"),
        )
        for x, period in [
            (plot_right - 104, 5),
            (plot_right - 58, 10),
            (plot_right - 8, 20),
        ]:
            canvas.create_text(
                x,
                16,
                text=f"MA{period}",
                fill=theme[f"ma{period}"],
                anchor="e",
                font=("Segoe UI", 9, "bold"),
            )
        plot_w = max(1, plot_right - left)
        max_visible = max(20, min(self.history_limit, int(plot_w / 8)))
        draw_candlestick_plot(
            canvas,
            theme,
            self.candles[-max_visible:],
            self.previous_close,
            left,
            top,
            plot_right,
            bottom,
        )


class TaiwanStockDetailWindow(EtfDetailWindow):
    def __init__(
        self,
        parent: tk.Tk,
        code: str,
        initial: SecurityQuote,
        interval: int,
        timeout: int,
        retries: int,
        theme_getter,
    ) -> None:
        self.security_symbol = initial.symbol
        super().__init__(
            parent=parent,
            initial=security_quote_to_market_row(code, initial),
            interval=interval,
            timeout=timeout,
            retries=retries,
            verify_ssl=False,
            theme_getter=theme_getter,
        )

    def fetch_worker(self) -> None:
        try:
            quote = fetch_taiwan_security_quote(
                self.code,
                "",
                self.timeout,
                self.retries,
            )
            self.security_symbol = quote.symbol
            desired_mode = chart_mode_for_time()
            candles: list[MarketCandle] | None = None
            grain: str | None = None
            if desired_mode != self.chart_mode:
                try:
                    candles, grain = fetch_yahoo_candles(
                        self.security_symbol,
                        self.timeout,
                        self.retries,
                        self.history_limit,
                        prefer_intraday=desired_mode == "intraday",
                    )
                except RuntimeError:
                    candles = []
                    grain = (
                        "5 分 K（即時建立）"
                        if desired_mode == "intraday"
                        else "日 K（即時建立）"
                    )
            self.queue.put(
                (
                    "data",
                    (
                        security_quote_to_market_row(self.code, quote),
                        candles,
                        grain,
                        desired_mode,
                    ),
                )
            )
        except Exception as exc:
            self.queue.put(("error", exc))

    def update_quote(
        self,
        quote: EtfMarketRow,
        candles: list[MarketCandle] | None = None,
        grain: str | None = None,
        desired_mode: str | None = None,
    ) -> None:
        super().update_quote(quote, candles, grain, desired_mode)
        self.detail_text.set(
            "｜".join(
                [
                    f"開盤 {fmt_number(quote.open_price)}",
                    f"最高 {fmt_number(quote.high)}",
                    f"最低 {fmt_number(quote.low)}",
                    f"昨收 {fmt_number(quote.previous_close)}",
                    f"成交量 {fmt_number(quote.volume, 0)}",
                ]
            )
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="啟動股票動態視窗介面。")
    parser.add_argument("-i", "--interval", type=int, default=10, help="更新間隔秒數，預設 10。")
    parser.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto", help="資料來源。")
    parser.add_argument("--timeout", type=positive_int, default=15, help="網路逾時秒數，預設 15。")
    parser.add_argument("--retries", type=int, default=2, help="連線失敗時重試次數，預設 2。")
    parser.add_argument("--history", type=int, default=80, help="K 線圖保留資料點數，預設 80。")
    parser.add_argument("--no-etf", action="store_true", help="不顯示 ETF 資料。")
    parser.add_argument("--verify-ssl", action="store_true", help="強制驗證 TWSE SSL 憑證。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    root = tk.Tk()
    StockDynamicApp(
        root=root,
        interval=max(1, args.interval),
        source=args.source,
        timeout=args.timeout,
        retries=max(0, args.retries),
        include_etfs=not args.no_etf,
        verify_ssl=args.verify_ssl,
        history_limit=max(2, args.history),
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
