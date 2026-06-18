#!/usr/bin/env python3
"""Command-line entry point for News Reader."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from news_reader.dashboard import main as dashboard_main  # noqa: E402
from news_reader.news import main as news_main  # noqa: E402
from news_reader.stock_dynamic import main as dynamic_main  # noqa: E402
from news_reader.stock_monitor import main as stock_main  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="News Reader 專案入口：讀取即時新聞或監控台股指數與 ETF。"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["news", "stocks", "dashboard", "dynamic"],
        default="news",
        help="要執行的功能：news 抓新聞；stocks 監控台股；dashboard 產生 HTML Dashboard；dynamic 啟動股票視窗。預設 news。",
    )
    parser.add_argument("args", nargs=argparse.REMAINDER, help="傳給子功能的參數。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "stocks":
        return stock_main(args.args)
    if args.command == "dashboard":
        return dashboard_main(args.args)
    if args.command == "dynamic":
        return dynamic_main(args.args)
    return news_main(args.args)


if __name__ == "__main__":
    raise SystemExit(main())
