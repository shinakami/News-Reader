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
from news_reader.stock_dynamic import (
    parse_args as parse_dynamic_args,
    row_from_mis,
    security_quote_from_yahoo_tw_item,
)
from news_reader.stock_market_dashboard import main as dashboard_main
from news_reader.stock_market_dashboard import parse_args as parse_dashboard_args
from news_reader.stock_monitor import EtfQuote, first_level, parse_args as parse_stock_args


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


class DailyPriceLimitTests(unittest.TestCase):
    def test_mis_order_book_skips_zero_placeholder(self) -> None:
        self.assertEqual(first_level("0.0000_1085.0000_1080.0000_"), 1085.0)

    def test_terminal_etf_uses_single_sided_limit_quote(self) -> None:
        quote = EtfQuote(
            code="TEST",
            name="Test ETF",
            price=None,
            previous_close=100.0,
            bid=110.0,
            ask=None,
            high=110.0,
            low=110.0,
            volume=1,
            market_time="09:01:00",
        )

        self.assertEqual(quote.display_price, 110.0)
        self.assertEqual(quote.change_percent, 10.0)

    def test_mis_limit_up_uses_bid_instead_of_previous_close(self) -> None:
        quote = row_from_mis(
            {
                "c": "2308",
                "n": "台達電",
                "z": "-",
                "y": "990.0000",
                "b": "0.0000_1085.0000_1080.0000_",
                "a": "-",
                "u": "1085.0000",
                "w": "891.0000",
            }
        )

        self.assertEqual(quote.price, 1085.0)
        self.assertTrue(quote.is_limit_up)
        self.assertEqual(quote.limit_status, "漲停")

    def test_mis_limit_down_uses_ask_instead_of_previous_close(self) -> None:
        quote = row_from_mis(
            {
                "c": "2314",
                "n": "台揚",
                "z": "-",
                "y": "2.8900",
                "b": "-",
                "a": "2.6100_2.6200_",
                "u": "3.1700",
                "w": "2.6100",
            }
        )

        self.assertEqual(quote.price, 2.61)
        self.assertTrue(quote.is_limit_down)
        self.assertEqual(quote.limit_status, "跌停")

    def test_yahoo_limit_flag_supplies_missing_price(self) -> None:
        quote = security_quote_from_yahoo_tw_item(
            "2308.TW",
            "台達電",
            {
                "chart": {
                    "meta": {},
                    "quote": {
                        "price": "-",
                        "previousClose": "990",
                        "limitUp": True,
                        "limitDown": False,
                        "limitUpPrice": "1085",
                        "limitDownPrice": "891",
                    },
                }
            },
        )

        self.assertEqual(quote.price, 1085.0)
        self.assertTrue(quote.is_limit_up)
        self.assertEqual(quote.limit_status, "漲停")


if __name__ == "__main__":
    unittest.main()
