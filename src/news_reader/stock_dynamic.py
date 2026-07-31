#!/usr/bin/env python3
"""Tkinter stock quote window with live cards and OHLC candlestick charts."""

from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time as clock_time
from tkinter import ttk
from typing import Sequence
import urllib.request
import urllib.parse

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
}

YAHOO_SYMBOL_BY_NAME = {name: symbol for symbol, name in YAHOO_INDICES.items()}
MA_PERIODS = (5, 10, 20)

ETF_LIST_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
ETF_PRODUCTS_URL = "https://www.twse.com.tw/zh/ETFortune/ajaxProductsResult"
INSTITUTION_TRADES_URL = "https://www.twse.com.tw/fund/BFI82U?response=json"
TWSE_BREADTH_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=MS"
TPEX_BREADTH_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainborad_highlight"
ETF_CODE_PATTERN = re.compile(r"^00[0-9A-Z]+$")
ETF_BATCH_SIZE = 45

THEMES = {
    "day": {
        "label": "白天",
        "next_label": "切換深夜",
        "bg": "#eef2f6",
        "surface": "#ffffff",
        "surface_alt": "#f8fafc",
        "text": "#1d252d",
        "muted": "#64748b",
        "line": "#d7dee8",
        "grid": "#e8edf3",
        "up": "#b42318",
        "down": "#067647",
        "up_fill": "#fee2e2",
        "down_fill": "#dcfce7",
        "accent": "#2563eb",
        "ma5": "#38bdf8",
        "ma10": "#7c3aed",
        "ma20": "#ea580c",
        "selected": "#dbeafe",
    },
    "night": {
        "label": "深夜",
        "next_label": "切換白天",
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


@dataclass(frozen=True)
class MarketCandle:
    timestamp: int
    label: str
    open: float
    high: float
    low: float
    close: float


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
        self.market_window: MarketOverviewWindow | None = None
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loading = False
        self.closed = False
        self.applying_theme = False
        self.theme_name = "day"
        self.theme = THEMES[self.theme_name]

        self.root.title("Stock Dynamic")
        self.root.geometry("1240x840")
        self.root.minsize(760, 620)
        self.root.configure(bg=self.theme["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.status_text = tk.StringVar(value="正在載入行情...")
        self.source_text = tk.StringVar(value=f"來源：{source}")
        self.theme_button_text = tk.StringVar(value=self.theme["next_label"])
        self.style = ttk.Style()
        self.card_widgets: list[ttk.Frame] = []
        self.current_layout: tuple[int, bool] | None = None
        self.build_ui()
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
            text="台股四大指數與 ETF 即時看板",
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
        ttk.Button(actions, textvariable=self.theme_button_text, style="Flat.TButton", command=self.toggle_theme).pack(side="left", padx=(0, 8))
        refresh_button = ttk.Button(actions, text="立即更新", style="Flat.TButton", command=self.refresh)
        refresh_button.pack(side="left")
        header_inner.columnconfigure(0, weight=1)

        self.card_frame = ttk.Frame(self.root, style="TFrame")
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

        self.body = ttk.Frame(self.root, style="TFrame")
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

        self.etf_panel = ttk.Frame(self.right_panel, style="Card.TFrame", padding=16)
        self.etf_panel.grid(row=0, column=0, sticky="nsew")
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
        self.main_pane.add(self.chart_panel, stretch="always", minsize=360)
        self.main_pane.add(self.right_panel, stretch="always", minsize=360)
        self.layout_for_width(self.root.winfo_width() or 1240)

    def on_root_resize(self, event: tk.Event) -> None:
        if event.widget is self.root and not self.applying_theme:
            self.layout_for_width(event.width)

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
            self.main_pane.grid(row=0, column=0, sticky="nsew")
        else:
            self.body.columnconfigure(0, weight=3)
            self.body.columnconfigure(1, weight=2)
            self.body.rowconfigure(0, weight=1)
            self.main_pane.configure(orient="horizontal")
            self.main_pane.grid(row=0, column=0, columnspan=2, sticky="nsew")

        self.draw_chart()

    def apply_theme(self) -> None:
        theme = self.theme
        self.root.configure(bg=theme["bg"])
        self.theme_button_text.set(theme["next_label"])

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

        for pane in [self.main_pane, self.chart_pane, *self.chart_rows]:
            pane.configure(bg=theme["line"])
        for canvas in self.index_canvases.values():
            canvas.configure(bg=theme["surface"])
        self.etf_tree.tag_configure("up", foreground=theme["up"])
        self.etf_tree.tag_configure("down", foreground=theme["down"])
        for name, labels in self.cards.items():
            labels["accent"].configure(bg=CHART_COLORS.get(name, theme["accent"]))
        self.draw_chart()

    def toggle_theme(self) -> None:
        self.theme_name = "night" if self.theme_name == "day" else "day"
        self.theme = THEMES[self.theme_name]
        self.applying_theme = True
        try:
            self.apply_theme()
            if self.market_window and not self.market_window.closed:
                self.market_window.apply_theme()
        finally:
            self.applying_theme = False
        self.root.after_idle(lambda: self.layout_for_width(self.root.winfo_width(), force=True))

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

    def close(self) -> None:
        self.closed = True
        self.root.destroy()

    def refresh(self) -> None:
        if self.loading or self.closed:
            return
        self.loading = True
        self.status_text.set("正在更新行情...")
        worker = threading.Thread(target=self.fetch_worker, daemon=True)
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
            all_etfs = (
                fetch_all_etf_quotes(self.timeout, self.retries, self.verify_ssl)
                if self.include_etfs
                else None
            )
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
                        histories,
                        grains,
                        desired_mode,
                    ),
                )
            )
        except Exception as exc:
            self.result_queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                self.loading = False
                if kind == "data":
                    (
                        quotes,
                        api_time,
                        data_source,
                        etfs,
                        histories,
                        grains,
                        desired_mode,
                    ) = payload
                    self.update_market(
                        quotes,
                        api_time,
                        data_source,
                        etfs,
                        histories,
                        grains,
                        desired_mode,
                    )
                else:
                    self.status_text.set(f"更新失敗：{payload}")
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
        histories: dict[str, list[MarketCandle]] | None = None,
        grains: dict[str, str] | None = None,
        desired_mode: str | None = None,
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        self.draw_chart()
        self.root.after(self.interval * 1000, self.refresh)

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
        self.draw_index_panel(canvas, name, 0, 0, width, height)

    def draw_index_panel(
        self,
        canvas: tk.Canvas,
        name: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
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
        canvas.create_rectangle(x0, y0, x1, y1, fill=theme["surface_alt"], outline=theme["line"])
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="啟動股票動態視窗介面。")
    parser.add_argument("-i", "--interval", type=int, default=10, help="更新間隔秒數，預設 10。")
    parser.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto", help="資料來源。")
    parser.add_argument("--timeout", type=int, default=15, help="網路逾時秒數，預設 15。")
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
