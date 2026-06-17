#!/usr/bin/env python3
"""Fetch current online news headlines and print them line by line."""

from __future__ import annotations

import argparse
import html
import sys
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Sequence


DEFAULT_QUERIES = ["台灣", "國際", "科技", "商業"]


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published: str
    link: str


def build_feed_url(query: str, language: str, region: str) -> str:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "hl": language,
            "gl": region,
            "ceid": f"{region}:{language.split('-')[0]}",
        }
    )
    return f"https://news.google.com/rss/search?{params}"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(html.unescape(value).split())


def format_date(value: str) -> str:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def fetch_feed(url: str, timeout: int) -> ET.Element:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "News Reader/1.0 (+https://news.google.com/rss)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    return ET.fromstring(data)


def read_news(query: str, language: str, region: str, limit: int, timeout: int) -> list[NewsItem]:
    root = fetch_feed(build_feed_url(query, language, region), timeout)
    items: list[NewsItem] = []

    for item in root.findall("./channel/item")[:limit]:
        source_node = item.find("source")
        items.append(
            NewsItem(
                title=clean_text(item.findtext("title")),
                source=clean_text(source_node.text if source_node is not None else ""),
                published=format_date(clean_text(item.findtext("pubDate"))),
                link=clean_text(item.findtext("link")),
            )
        )

    return items


def print_news(query: str, items: list[NewsItem], show_links: bool) -> None:
    print(f"\n=== {query} ===")
    if not items:
        print("目前沒有抓到新聞。")
        return

    for index, item in enumerate(items, start=1):
        meta = " | ".join(part for part in [item.source, item.published] if part)
        suffix = f" ({meta})" if meta else ""
        print(f"{index:02d}. {item.title}{suffix}")
        if show_links and item.link:
            print(textwrap.indent(item.link, "    "))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抓取即時網路新聞，並用一行一則的方式列出標題。"
    )
    parser.add_argument(
        "queries",
        nargs="*",
        help="要搜尋的新聞關鍵字；未提供時會抓台灣、國際、科技、商業。",
    )
    parser.add_argument("-n", "--limit", type=int, default=5, help="每個分類列出幾則新聞。")
    parser.add_argument("--language", default="zh-TW", help="新聞語言，預設 zh-TW。")
    parser.add_argument("--region", default="TW", help="新聞地區，預設 TW。")
    parser.add_argument("--timeout", type=int, default=15, help="網路逾時秒數。")
    parser.add_argument("--links", action="store_true", help="同時列出新聞連結。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    queries = args.queries or DEFAULT_QUERIES

    print(f"News Reader - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("資料來源：Google News RSS")

    try:
        for query in queries:
            items = read_news(query, args.language, args.region, args.limit, args.timeout)
            print_news(query, items, args.links)
    except (OSError, ET.ParseError) as exc:
        print(f"\n抓取新聞時發生錯誤：{exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
