#!/usr/bin/env python3
"""Fetch current online news headlines and print them line by line."""

from __future__ import annotations

import argparse
import html
import sys
import textwrap
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Sequence


DEFAULT_QUERIES = ["台灣", "國際", "科技", "商業"]
DEFAULT_DASHBOARD_OUTPUT = "news_dashboard.html"


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published: str
    link: str
    source_url: str = ""

    @property
    def image_url(self) -> str:
        if not self.source_url:
            return ""
        params = urllib.parse.urlencode({"domain_url": self.source_url, "sz": "256"})
        return f"https://www.google.com/s2/favicons?{params}"


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
                source_url=clean_text(
                    source_node.get("url") if source_node is not None else ""
                ),
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


def render_news_dashboard(
    grouped_news: list[tuple[str, list[NewsItem]]],
    generated_at: datetime,
) -> str:
    total = sum(len(items) for _, items in grouped_news)
    category_buttons = [
        '<button class="filter active" type="button" data-category="all">全部</button>'
    ]
    cards: list[str] = []
    for query, items in grouped_news:
        safe_query = html.escape(query, quote=True)
        category_buttons.append(
            f'<button class="filter" type="button" data-category="{safe_query}">{safe_query}</button>'
        )
        for item in items:
            title = html.escape(item.title)
            source = html.escape(item.source or "未知來源")
            published = html.escape(item.published or "時間未提供")
            link = html.escape(item.link, quote=True)
            search_text = html.escape(
                f"{query} {item.title} {item.source}".lower(), quote=True
            )
            if item.image_url:
                image = (
                    f'<img src="{html.escape(item.image_url, quote=True)}" '
                    f'alt="{source} 新聞來源圖片" loading="lazy" '
                    'onerror="this.hidden=true;this.nextElementSibling.hidden=false">'
                    f'<span class="image-fallback" hidden>{safe_query}</span>'
                )
            else:
                image = f'<span class="image-fallback">{safe_query}</span>'
            cards.append(
                f"""
                <article class="news-card" data-category="{safe_query}" data-search="{search_text}">
                  <a class="thumbnail" href="{link}" target="_blank" rel="noopener noreferrer" aria-label="閱讀：{title}">
                    {image}
                    <span class="category-badge">{safe_query}</span>
                  </a>
                  <div class="card-content">
                    <div class="meta"><span>{source}</span><time>{published}</time></div>
                    <h2><a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a></h2>
                    <a class="read-more" href="{link}" target="_blank" rel="noopener noreferrer">閱讀新聞 <span aria-hidden="true">→</span></a>
                  </div>
                </article>
                """
            )

    generated_text = generated_at.strftime("%Y-%m-%d %H:%M:%S")
    buttons_html = "".join(category_buttons)
    cards_html = "".join(cards)
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>News Reader Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f6fa;
      --surface: #ffffff;
      --text: #182230;
      --muted: #667085;
      --line: #e3e8ef;
      --accent: #2457d6;
      --accent-soft: #e9efff;
      --shadow: 0 12px 32px rgba(24, 34, 48, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font-family: "Microsoft JhengHei UI", "Noto Sans TC", system-ui, sans-serif; }}
    a {{ color: inherit; }}
    .page {{ width: min(1240px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    header {{ padding: 28px; border-radius: 22px; color: #fff; background: linear-gradient(125deg, #173f98, #2670dd 60%, #25a6b8); box-shadow: var(--shadow); }}
    .eyebrow {{ margin: 0 0 8px; font-size: 13px; letter-spacing: .12em; opacity: .78; text-transform: uppercase; }}
    h1 {{ margin: 0; font-size: clamp(28px, 5vw, 48px); line-height: 1.1; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 12px 24px; margin-top: 18px; color: rgba(255,255,255,.86); font-size: 14px; }}
    .toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin: 18px 0; padding: 14px; border: 1px solid var(--line); border-radius: 16px; background: rgba(243,246,250,.94); backdrop-filter: blur(12px); }}
    .filters {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .filter {{ padding: 9px 15px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface); color: var(--muted); cursor: pointer; font: inherit; }}
    .filter:hover, .filter.active {{ border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }}
    .search {{ flex: 1 1 240px; min-width: 180px; padding: 11px 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--text); font: inherit; outline: none; }}
    .search:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }}
    .result-count {{ margin-left: auto; color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .news-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
    .news-card {{ overflow: hidden; display: flex; flex-direction: column; min-height: 350px; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: 0 6px 20px rgba(24,34,48,.05); transition: transform .18s ease, box-shadow .18s ease; }}
    .news-card:hover {{ transform: translateY(-3px); box-shadow: var(--shadow); }}
    .thumbnail {{ position: relative; display: grid; place-items: center; height: 174px; overflow: hidden; text-decoration: none; background: radial-gradient(circle at 25% 20%, #fff 0 8%, transparent 9%), linear-gradient(135deg, #dce7ff, #bdd5ff 48%, #bce8eb); }}
    .thumbnail img {{ width: 96px; height: 96px; border-radius: 22px; object-fit: contain; padding: 12px; background: rgba(255,255,255,.92); box-shadow: 0 10px 26px rgba(23,63,152,.16); transition: transform .2s ease; }}
    .thumbnail:hover img {{ transform: scale(1.06); }}
    .image-fallback {{ display: grid; place-items: center; width: 96px; height: 96px; border-radius: 24px; color: #fff; background: linear-gradient(135deg, #2457d6, #25a6b8); font-size: 20px; font-weight: 800; box-shadow: 0 10px 26px rgba(23,63,152,.2); }}
    .category-badge {{ position: absolute; left: 14px; bottom: 14px; padding: 5px 10px; border-radius: 999px; color: #fff; background: rgba(24,34,48,.78); font-size: 12px; }}
    .card-content {{ display: flex; flex: 1; flex-direction: column; padding: 18px; }}
    .meta {{ display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12px; }}
    .meta span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    h2 {{ margin: 14px 0 18px; font-size: 18px; line-height: 1.55; }}
    h2 a {{ text-decoration: none; }}
    h2 a:hover {{ color: var(--accent); }}
    .read-more {{ margin-top: auto; color: var(--accent); font-size: 14px; font-weight: 700; text-decoration: none; }}
    .empty {{ display: none; padding: 48px; border: 1px dashed var(--line); border-radius: 18px; background: var(--surface); color: var(--muted); text-align: center; }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 12px; text-align: center; }}
    @media (max-width: 920px) {{ .news-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 620px) {{ .page {{ width: min(100% - 20px, 1240px); padding-top: 10px; }} header {{ padding: 22px; border-radius: 16px; }} .news-grid {{ grid-template-columns: 1fr; }} .toolbar {{ top: 6px; }} .result-count {{ width: 100%; margin-left: 0; }} }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <p class="eyebrow">Google News RSS</p>
      <h1>News Reader</h1>
      <div class="summary"><span>共 {total} 則新聞</span><span>{len(grouped_news)} 個分類</span><span>更新時間：{generated_text}</span></div>
    </header>
    <section class="toolbar" aria-label="新聞篩選工具">
      <div class="filters">{buttons_html}</div>
      <input id="search" class="search" type="search" placeholder="搜尋標題或新聞來源" aria-label="搜尋新聞">
      <span id="result-count" class="result-count">顯示 {total} 則</span>
    </section>
    <section id="news-grid" class="news-grid">{cards_html}</section>
    <div id="empty" class="empty">找不到符合條件的新聞。</div>
    <footer>點擊新聞圖片、標題或「閱讀新聞」即可在新分頁開啟內容。</footer>
  </main>
  <script>
    const buttons = [...document.querySelectorAll('.filter')];
    const cards = [...document.querySelectorAll('.news-card')];
    const search = document.getElementById('search');
    const count = document.getElementById('result-count');
    const empty = document.getElementById('empty');
    let category = 'all';
    function applyFilters() {{
      const term = search.value.trim().toLowerCase();
      let visible = 0;
      cards.forEach(card => {{
        const matchesCategory = category === 'all' || card.dataset.category === category;
        const matchesSearch = !term || card.dataset.search.includes(term);
        card.hidden = !(matchesCategory && matchesSearch);
        if (!card.hidden) visible += 1;
      }});
      count.textContent = `顯示 ${{visible}} 則`;
      empty.style.display = visible ? 'none' : 'block';
    }}
    buttons.forEach(button => button.addEventListener('click', () => {{
      category = button.dataset.category;
      buttons.forEach(item => item.classList.toggle('active', item === button));
      applyFilters();
    }}));
    search.addEventListener('input', applyFilters);
  </script>
</body>
</html>
"""


def write_news_dashboard(
    grouped_news: list[tuple[str, list[NewsItem]]],
    output: str,
    open_browser: bool,
) -> Path:
    output_path = Path(output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_news_dashboard(grouped_news, datetime.now())
    output_path.write_text(html_text, encoding="utf-8")
    print(f"\n新聞 Dashboard 已產生：{output_path.resolve()}")
    if open_browser:
        webbrowser.open(output_path.resolve().as_uri())
    return output_path


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
    parser.add_argument("--dashboard", action="store_true", help="同時產生新聞 HTML Dashboard。")
    parser.add_argument("--open", action="store_true", help="產生 Dashboard 後用預設瀏覽器開啟。")
    parser.add_argument("-o", "--output", default=DEFAULT_DASHBOARD_OUTPUT, help="Dashboard 輸出路徑。")
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
        grouped_news: list[tuple[str, list[NewsItem]]] = []
        for query in queries:
            items = read_news(query, args.language, args.region, args.limit, args.timeout)
            grouped_news.append((query, items))
            print_news(query, items, args.links)
        if args.dashboard or args.open:
            write_news_dashboard(grouped_news, args.output, args.open)
    except (OSError, ET.ParseError) as exc:
        print(f"\n抓取新聞時發生錯誤：{exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
