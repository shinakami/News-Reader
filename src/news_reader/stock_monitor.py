#!/usr/bin/env python3
"""Monitor four major Taiwan stock indices in the terminal."""

from __future__ import annotations

import argparse
import atexit
import contextlib
import io
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


TWSE_API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

INDICES = {
    "tse_t00.tw": "加權指數",
    "otc_o00.tw": "櫃買指數",
    "tse_t13.tw": "電子類指數",
    "tse_t17.tw": "金融保險類指數",
}

TOP_ETFS = {
    "tse_0050.tw": "元大台灣50",
    "tse_0056.tw": "元大高股息",
    "tse_00878.tw": "國泰永續高股息",
    "tse_00919.tw": "群益台灣精選高息",
    "tse_006208.tw": "富邦台50",
}


@dataclass(frozen=True)
class IndexQuote:
    name: str
    price: float | None
    previous_close: float | None
    open_price: float | None
    high: float | None
    low: float | None
    market_time: str

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
class EtfQuote:
    code: str
    name: str
    price: float | None
    previous_close: float | None
    bid: float | None
    ask: float | None
    high: float | None
    low: float | None
    volume: int | None
    market_time: str

    @property
    def display_price(self) -> float | None:
        if self.price is not None:
            return self.price
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.previous_close

    @property
    def change(self) -> float | None:
        if self.display_price is None or self.previous_close is None:
            return None
        return self.display_price - self.previous_close

    @property
    def change_percent(self) -> float | None:
        if self.change is None or not self.previous_close:
            return None
        return self.change / self.previous_close * 100


def to_float(value: str | None) -> float | None:
    if not value or value in {"-", "--"}:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def to_int(value: str | None) -> int | None:
    if not value or value in {"-", "--"}:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None


def first_level(value: str | None) -> float | None:
    if not value:
        return None
    return to_float(value.split("_", 1)[0])


def signed(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:+,.{digits}f}"


def number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:,.{digits}f}"


def whole_number(value: int | None) -> str:
    if value is None:
        return "--"
    return f"{value:,}"


def trend_icon(current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return " "
    if current > previous:
        return "^"
    if current < previous:
        return "v"
    return "-"


def add_history(
    history: dict[str, list[float]],
    quotes: list[IndexQuote],
    max_points: int,
) -> dict[str, list[float]]:
    for quote in quotes:
        if quote.price is None:
            continue
        points = history.setdefault(quote.name, [])
        points.append(quote.price)
        del points[:-max_points]
    return history


def render_line_chart(points: list[float], width: int = 60, height: int = 8) -> list[str]:
    if len(points) < 2:
        return ["資料點不足，等待下一次更新。"]

    points = points[-width:]
    low = min(points)
    high = max(points)
    span = high - low
    if span == 0:
        span = 1

    rows = [[" " for _ in points] for _ in range(height)]
    for column, value in enumerate(points):
        scaled = (value - low) / span
        row = height - 1 - round(scaled * (height - 1))
        rows[row][column] = "*"

    lines = []
    for index, row in enumerate(rows):
        if index == 0:
            label = f"{high:,.2f}"
        elif index == height - 1:
            label = f"{low:,.2f}"
        else:
            label = ""
        lines.append(f"{label:>12} | {''.join(row)}")
    lines.append(f"{'':>12} + {'-' * len(points)}")
    return lines


def chart_panel(name: str, points: list[float], width: int, height: int) -> list[str]:
    latest = f"{points[-1]:,.2f}" if points else "--"
    title = f"{name} 最新:{latest} 點:{len(points)}"
    lines = [title]
    lines.extend(render_line_chart(points, width=width, height=height))
    return lines


def print_chart_row(left: list[str], right: list[str], gap: int = 4) -> None:
    row_height = max(len(left), len(right))
    left_width = max(len(line) for line in left)
    for index in range(row_height):
        left_line = left[index] if index < len(left) else ""
        right_line = right[index] if index < len(right) else ""
        print(f"{left_line:<{left_width}}{' ' * gap}{right_line}")


def print_charts(history: dict[str, list[float]], width: int, height: int) -> None:
    print("\n走勢圖")
    print("-" * 104)
    panel_width = min(width, 32)
    names = list(INDICES.values())
    panels = [
        chart_panel(name, history.get(name, []), panel_width, height)
        for name in names
    ]
    for row_start in range(0, len(panels), 2):
        if row_start:
            print()
        left = panels[row_start]
        right = panels[row_start + 1] if row_start + 1 < len(panels) else [""]
        print_chart_row(left, right)


def make_context(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def fetch_quotes(timeout: int, verify_ssl: bool) -> tuple[list[IndexQuote], str]:
    symbols = "|".join(INDICES)
    params = urllib.parse.urlencode({"ex_ch": symbols, "json": "1", "delay": "0"})
    url = f"{TWSE_API_URL}?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "News Reader Stock Monitor/1.0",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        },
    )

    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=make_context(verify_ssl)
        ) as response:
            payload = response.read().decode("utf-8-sig")
    except urllib.error.URLError as exc:
        if verify_ssl and "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return fetch_quotes(timeout, verify_ssl=False)
        raise

    data = json.loads(payload.strip())
    if data.get("rtcode") != "0000":
        raise RuntimeError(data.get("rtmessage", "TWSE API returned an error"))

    quotes = []
    for item in data.get("msgArray", []):
        code = f"{item.get('ex')}_{item.get('ch')}"
        display_name = INDICES.get(code, item.get("n", code))
        quotes.append(
            IndexQuote(
                name=display_name,
                price=to_float(item.get("z")),
                previous_close=to_float(item.get("y")),
                open_price=to_float(item.get("o")),
                high=to_float(item.get("h")),
                low=to_float(item.get("l")),
                market_time=item.get("t") or item.get("%") or "--",
            )
        )

    ordered = sorted(quotes, key=lambda quote: list(INDICES.values()).index(quote.name))
    query_time = data.get("queryTime", {})
    api_time = f"{query_time.get('sysDate', '')} {query_time.get('sysTime', '')}".strip()
    return ordered, api_time


def fetch_etf_quotes(timeout: int, verify_ssl: bool) -> list[EtfQuote]:
    symbols = "|".join(TOP_ETFS)
    params = urllib.parse.urlencode({"ex_ch": symbols, "json": "1", "delay": "0"})
    url = f"{TWSE_API_URL}?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "News Reader Stock Monitor/1.0",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        },
    )

    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=make_context(verify_ssl)
        ) as response:
            payload = response.read().decode("utf-8-sig")
    except urllib.error.URLError as exc:
        if verify_ssl and "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return fetch_etf_quotes(timeout, verify_ssl=False)
        raise

    data = json.loads(payload.strip())
    if data.get("rtcode") != "0000":
        raise RuntimeError(data.get("rtmessage", "TWSE API returned an error"))

    quotes = []
    for item in data.get("msgArray", []):
        code = item.get("c", "")
        key = f"{item.get('ex')}_{item.get('ch')}"
        quotes.append(
            EtfQuote(
                code=code,
                name=TOP_ETFS.get(key, item.get("n", code)),
                price=to_float(item.get("z")),
                previous_close=to_float(item.get("y")),
                bid=first_level(item.get("b")),
                ask=first_level(item.get("a")),
                high=to_float(item.get("h")),
                low=to_float(item.get("l")),
                volume=to_int(item.get("v")),
                market_time=item.get("t") or item.get("%") or "--",
            )
        )

    order = list(TOP_ETFS)
    return sorted(quotes, key=lambda quote: order.index(f"tse_{quote.code}.tw"))


def restore_cursor() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def setup_live_terminal(disabled: bool) -> None:
    if disabled or not sys.stdout.isatty():
        return
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    atexit.register(restore_cursor)


def draw_frame(content: str, previous_line_count: int) -> int:
    if not sys.stdout.isatty():
        print(content, end="" if content.endswith("\n") else "\n")
        return len(content.splitlines())

    lines = content.splitlines()
    output: list[str] = ["\033[H"]
    output.extend(f"{line}\033[K\n" for line in lines)
    extra_lines = max(0, previous_line_count - len(lines))
    output.extend("\033[K\n" for _ in range(extra_lines))
    output.append("\033[H")
    sys.stdout.write("".join(output))
    sys.stdout.flush()
    return len(lines)


def render_output(callback, *args) -> tuple[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = callback(*args)
    return buffer.getvalue(), result


def print_etf_quotes(etfs: list[EtfQuote]) -> None:
    print("\n市值前五大 ETF 即時股價")
    print("-" * 104)
    print(
        f"{'代號':<8} {'名稱':<18} {'現價/估價':>10} {'漲跌':>10} {'漲跌幅':>9} "
        f"{'買一':>9} {'賣一':>9} {'最高':>9} {'最低':>9} {'量':>9} {'時間':>9}"
    )
    for etf in etfs:
        price_note = "*" if etf.price is None and etf.display_price is not None else " "
        print(
            f"{etf.code:<8} "
            f"{etf.name:<18} "
            f"{number(etf.display_price):>9}{price_note} "
            f"{signed(etf.change):>10} "
            f"{signed(etf.change_percent):>8}% "
            f"{number(etf.bid):>9} "
            f"{number(etf.ask):>9} "
            f"{number(etf.high):>9} "
            f"{number(etf.low):>9} "
            f"{whole_number(etf.volume):>9} "
            f"{etf.market_time:>9}"
        )
    print("* 現價缺漏時，以買一/賣一中間價估算；若買賣價也缺漏，使用昨收參考。")


def print_quotes(
    quotes: list[IndexQuote],
    api_time: str,
    interval: int,
    previous_prices: dict[str, float | None],
    verify_ssl: bool,
    history: dict[str, list[float]],
    show_chart: bool,
    chart_width: int,
    chart_height: int,
    etfs: list[EtfQuote] | None,
) -> dict[str, float | None]:
    print("台股四大指數動態監控")
    print(f"本機時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"交易所時間：{api_time or '--'}")
    print(f"更新頻率：每 {interval} 秒")
    print(f"SSL 驗證：{'開啟' if verify_ssl else '自動備援'}")
    print("按 Ctrl+C 停止\n")
    print(f"{'指數':<12} {'現價':>12} {'漲跌':>12} {'漲跌幅':>10} {'開盤':>12} {'最高':>12} {'最低':>12} {'時間':>10}  動態")
    print("-" * 104)

    next_prices: dict[str, float | None] = {}
    for quote in quotes:
        previous_price = previous_prices.get(quote.name)
        next_prices[quote.name] = quote.price
        print(
            f"{quote.name:<12} "
            f"{number(quote.price):>12} "
            f"{signed(quote.change):>12} "
            f"{signed(quote.change_percent):>9}% "
            f"{number(quote.open_price):>12} "
            f"{number(quote.high):>12} "
            f"{number(quote.low):>12} "
            f"{quote.market_time:>10}  "
            f"{trend_icon(quote.price, previous_price)}"
        )
    if etfs is not None:
        print_etf_quotes(etfs)
    if show_chart:
        print_charts(history, chart_width, chart_height)
    return next_prices


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="時刻監控台股四大指數動態。")
    parser.add_argument("-i", "--interval", type=int, default=10, help="更新間隔秒數，預設 10。")
    parser.add_argument("--timeout", type=int, default=15, help="網路逾時秒數，預設 15。")
    parser.add_argument("--no-clear", action="store_true", help="不要清除畫面，保留每次更新紀錄。")
    parser.add_argument("--once", action="store_true", help="只抓取一次後結束。")
    parser.add_argument("--history", type=int, default=60, help="圖表保留的資料點數，預設 60。")
    parser.add_argument("--chart-width", type=int, default=60, help="圖表寬度，預設 60。")
    parser.add_argument("--chart-height", type=int, default=8, help="圖表高度，預設 8。")
    parser.add_argument("--no-chart", action="store_true", help="只顯示表格，不顯示走勢圖。")
    parser.add_argument("--no-etf", action="store_true", help="不顯示市值前五大 ETF 股價資訊。")
    parser.add_argument("--verify-ssl", action="store_true", help="強制驗證 TWSE SSL 憑證。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    interval = max(1, args.interval)
    max_history = max(2, args.history)
    chart_width = max(10, args.chart_width)
    chart_height = max(4, args.chart_height)
    previous_prices: dict[str, float | None] = {}
    history: dict[str, list[float]] = {}
    setup_live_terminal(args.no_clear)
    previous_line_count = 0

    try:
        while True:
            try:
                quotes, api_time = fetch_quotes(args.timeout, args.verify_ssl)
                etfs = None if args.no_etf else fetch_etf_quotes(args.timeout, args.verify_ssl)
                history = add_history(history, quotes, max_history)
                if args.no_clear:
                    previous_prices = print_quotes(
                        quotes,
                        api_time,
                        interval,
                        previous_prices,
                        args.verify_ssl,
                        history,
                        not args.no_chart,
                        chart_width,
                        chart_height,
                        etfs,
                    )
                else:
                    frame, next_prices = render_output(
                        print_quotes,
                        quotes,
                        api_time,
                        interval,
                        previous_prices,
                        args.verify_ssl,
                        history,
                        not args.no_chart,
                        chart_width,
                        chart_height,
                        etfs,
                    )
                    previous_prices = next_prices
                    previous_line_count = draw_frame(frame, previous_line_count)
            except (OSError, json.JSONDecodeError, RuntimeError) as exc:
                error_frame = (
                    "台股四大指數動態監控\n"
                    f"本機時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"抓取資料時發生錯誤：{exc}\n"
                    f"{interval} 秒後重試，按 Ctrl+C 停止。\n"
                )
                if args.no_clear:
                    print(error_frame, end="")
                else:
                    previous_line_count = draw_frame(error_frame, previous_line_count)
            if args.once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        restore_cursor()
        print("\n已停止監控。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
