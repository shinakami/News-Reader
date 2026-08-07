from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_PATH))

from news_reader.cli_types import positive_int
from news_reader.market_ai import render_snapshot
from news_reader.news import parse_args as parse_news_args
from news_reader.stock_dynamic import parse_args as parse_dynamic_args
from news_reader.stock_market_dashboard import main as dashboard_main
from news_reader.stock_market_dashboard import parse_args as parse_dashboard_args
from news_reader.stock_monitor import parse_args as parse_stock_args


class PositiveIntegerTests(unittest.TestCase):
    def test_rejects_zero_and_negative_values(self) -> None:
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_int(value)

    def test_accepts_positive_value(self) -> None:
        self.assertEqual(positive_int("15"), 15)

    def test_all_timeout_arguments_reject_negative_values(self) -> None:
        parsers = (
            parse_news_args,
            parse_stock_args,
            parse_dashboard_args,
            parse_dynamic_args,
        )
        for parser in parsers:
            with self.subTest(parser=parser.__module__), self.assertRaises(SystemExit) as caught:
                parser(["--timeout", "-1"])
            self.assertEqual(caught.exception.code, 2)

    def test_news_limit_rejects_negative_value(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            parse_news_args(["--limit", "-1"])
        self.assertEqual(caught.exception.code, 2)


class DashboardOutputTests(unittest.TestCase):
    def test_creates_missing_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "dashboard.html"
            with (
                patch(
                    "news_reader.stock_market_dashboard.fetch_market_data",
                    return_value=([], "now", "test", []),
                ),
                patch(
                    "news_reader.stock_market_dashboard.render_dashboard",
                    return_value="<html></html>",
                ),
            ):
                result = dashboard_main(["--output", str(output), "--no-etf"])

            self.assertEqual(result, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "<html></html>")


class MarketSnapshotTests(unittest.TestCase):
    def test_missing_breadth_dates_use_placeholders(self) -> None:
        text = render_snapshot(
            {"breadth": {"up": 1, "flat": 2, "down": 3}}
        )

        self.assertIn("資料日 上市 --／上櫃 --", text)


if __name__ == "__main__":
    unittest.main()
