#!/usr/bin/env python3
"""Tkinter stock quote window with live price cards and line charts."""

from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from tkinter import ttk
from typing import Sequence
import urllib.request
import urllib.parse

from news_reader.stock_monitor import (
    INDICES,
    EtfQuote,
    IndexQuote,
    fetch_market_data,
    fetch_twse_json,
    first_level,
    to_float,
    to_int,
)


CHART_COLORS = {
    "加權指數": "#2563eb",
    "櫃買指數": "#7c3aed",
    "電子類指數": "#0891b2",
    "金融保險類指數": "#ca8a04",
}

ETF_LIST_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
ETF_PRODUCTS_URL = "https://www.twse.com.tw/zh/ETFortune/ajaxProductsResult"
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
        "selected": "#334155",
    },
}


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


def fetch_etf_list(timeout: int) -> list[dict[str, str]]:
    request = urllib.request.Request(ETF_LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
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


def fetch_etf_profile_map(timeout: int) -> dict[str, dict[str, str]]:
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
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
    openapi_rows = fetch_etf_list(timeout)
    try:
        profiles = fetch_etf_profile_map(timeout)
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
        profile = fetch_etf_profile_map(timeout).get(code)
    except Exception:
        profile = None
    for row in fetch_etf_list(timeout):
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
        self.history: dict[str, list[float]] = defaultdict(list)
        self.index_previous_close: dict[str, float | None] = {}
        self.etf_rows: dict[str, EtfMarketRow] = {}
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
            text="四大指數曲線圖",
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
        finally:
            self.applying_theme = False
        self.root.after_idle(lambda: self.layout_for_width(self.root.winfo_width(), force=True))

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
            self.result_queue.put(("data", (quotes, api_time, data_source, all_etfs)))
        except Exception as exc:
            self.result_queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                self.loading = False
                if kind == "data":
                    quotes, api_time, data_source, etfs = payload
                    self.update_market(quotes, api_time, data_source, etfs)
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
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status_text.set(f"本機時間：{now}｜行情時間：{api_time or '--'}")
        self.source_text.set(f"來源：{data_source}｜更新頻率：{self.interval} 秒")

        for quote in quotes:
            self.index_previous_close[quote.name] = quote.previous_close
            if quote.price is not None:
                points = self.history[quote.name]
                points.append(quote.price)
                del points[:-self.history_limit]

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
        points = self.history.get(name, [])
        previous_close = self.index_previous_close.get(name)
        latest_value = points[-1] if points else None
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
            text=pct_text(latest_value, previous_close),
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
            x1 - 12,
            y0 + 36,
            text=f"昨收 {fmt_number(previous_close)}",
            fill=theme["muted"],
            anchor="e",
            font=("Segoe UI", 8),
        )

        plot_left = x0 + 52
        plot_right = x1 - 18
        plot_top = y0 + 54
        plot_bottom = y1 - 26
        plot_w = max(1, plot_right - plot_left)
        plot_h = max(1, plot_bottom - plot_top)

        if len(points) < 2:
            canvas.create_text(
                (x0 + x1) / 2,
                (plot_top + plot_bottom) / 2,
                text="等待下一次更新後開始繪製",
                fill=theme["muted"],
                font=("Microsoft JhengHei UI", 10),
            )
            return

        values_for_scale = points + ([previous_close] if previous_close is not None else [])
        low = min(values_for_scale)
        high = max(values_for_scale)
        padding = (high - low) * 0.12 or max(abs(high) * 0.001, 1)
        low -= padding
        high += padding
        span = high - low or 1

        for i in range(3):
            y = plot_top + i * plot_h / 2
            value = high - i * span / 2
            canvas.create_line(plot_left, y, plot_right, y, fill=theme["grid"])
            canvas.create_text(
                plot_right + 6,
                y,
                text=fmt_number(value),
                fill=theme["muted"],
                anchor="w",
                font=("Segoe UI", 7),
            )

        canvas.create_line(plot_left, plot_bottom, plot_right, plot_bottom, fill=theme["line"])
        if previous_close is not None:
            baseline_y = plot_top + (high - previous_close) / span * plot_h
            canvas.create_line(plot_left, baseline_y, plot_right, baseline_y, fill=theme["muted"], dash=(3, 4))
            canvas.create_text(
                plot_left + 4,
                baseline_y - 8,
                text="昨收",
                fill=theme["muted"],
                anchor="w",
                font=("Microsoft JhengHei UI", 8),
            )

        step = plot_w / max(1, len(points) - 1)
        coords: list[float] = []
        for idx, value in enumerate(points):
            coords.extend([plot_left + idx * step, plot_top + (high - value) / span * plot_h])
        area_coords = coords + [plot_right, plot_bottom, plot_left, plot_bottom]
        canvas.create_polygon(*area_coords, fill=trend_fill(theme, latest_value, previous_close), outline="")
        canvas.create_line(*coords, fill=color, width=2.4, smooth=True)
        canvas.create_oval(
            coords[-2] - 3,
            coords[-1] - 3,
            coords[-2] + 3,
            coords[-1] + 3,
            fill=color,
            outline=color,
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
        self.history: list[float] = []
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
            self.queue.put(("data", fetch_one_etf_quote(self.code, self.timeout, self.retries, self.verify_ssl)))
        except Exception as exc:
            self.queue.put(("error", exc))

    def process_results(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.loading = False
                if kind == "data":
                    self.update_quote(payload)
                else:
                    self.detail_text.set(f"更新失敗：{payload}")
        except queue.Empty:
            pass

        if not self.closed:
            self.window.after(200, self.process_results)

    def update_quote(self, quote: EtfMarketRow) -> None:
        theme = self.theme_getter()
        self.previous_close = quote.previous_close
        if quote.price is not None:
            self.history.append(quote.price)
            del self.history[:-160]

        self.title_text.set(f"{quote.code}  {quote.name}")
        self.price_text.set(fmt_number(quote.price))
        self.change_text.set(f"{fmt_signed(quote.change)} / {fmt_signed(quote.change_percent)}%")
        self.change_label.configure(fg=theme["up"] if (quote.change or 0) >= 0 else theme["down"])
        self.source_text.set(f"來源：{quote.source}｜時間：{quote.market_time}")
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

    def draw_chart(self) -> None:
        theme = self.theme_getter()
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 420)
        height = max(canvas.winfo_height(), 260)
        left, right, top, bottom = 62, 28, 28, 44
        plot_w = width - left - right
        plot_h = height - top - bottom
        canvas.create_rectangle(left, top, width - right, height - bottom, fill=theme["surface_alt"], outline=theme["line"])

        if len(self.history) < 2:
            canvas.create_text(width / 2, height / 2, text="等待下一次更新後開始繪製 ETF 曲線", fill=theme["muted"], font=("Microsoft JhengHei UI", 12))
            return

        low = min(self.history)
        values_for_scale = self.history + ([self.previous_close] if self.previous_close is not None else [])
        low = min(values_for_scale)
        high = max(values_for_scale)
        padding = (high - low) * 0.12 or max(abs(high) * 0.001, 0.1)
        low -= padding
        high += padding
        span = high - low or 1
        color = trend_color(theme, self.history[-1], self.previous_close)
        for i in range(4):
            y = top + i * plot_h / 3
            value = high - i * span / 3
            canvas.create_line(left, y, width - right, y, fill=theme["grid"])
            canvas.create_text(width - right + 8, y, text=fmt_number(value), fill=theme["muted"], anchor="w", font=("Segoe UI", 8))

        if self.previous_close is not None:
            baseline_y = top + (high - self.previous_close) / span * plot_h
            canvas.create_line(left, baseline_y, width - right, baseline_y, fill=theme["muted"], dash=(3, 4))
            canvas.create_text(left + 4, baseline_y - 8, text="昨收", fill=theme["muted"], anchor="w", font=("Microsoft JhengHei UI", 8))

        step = plot_w / max(1, len(self.history) - 1)
        coords: list[float] = []
        for idx, value in enumerate(self.history):
            coords.extend([left + idx * step, top + (high - value) / span * plot_h])
        area_coords = coords + [width - right, height - bottom, left, height - bottom]
        canvas.create_polygon(*area_coords, fill=trend_fill(theme, self.history[-1], self.previous_close), outline="")
        canvas.create_line(*coords, fill=color, width=2.5, smooth=True)
        canvas.create_oval(coords[-2] - 4, coords[-1] - 4, coords[-2] + 4, coords[-1] + 4, fill=color, outline=color)
        canvas.create_text(width - right, coords[-1] - 10, text=fmt_number(self.history[-1]), fill=color, anchor="e", font=("Segoe UI", 9, "bold"))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="啟動股票動態視窗介面。")
    parser.add_argument("-i", "--interval", type=int, default=10, help="更新間隔秒數，預設 10。")
    parser.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto", help="資料來源。")
    parser.add_argument("--timeout", type=int, default=15, help="網路逾時秒數，預設 15。")
    parser.add_argument("--retries", type=int, default=2, help="連線失敗時重試次數，預設 2。")
    parser.add_argument("--history", type=int, default=80, help="曲線圖保留資料點數，預設 80。")
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
