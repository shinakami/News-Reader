import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from news_reader.stock_dynamic import StockDynamicApp, US_INDEX_SYMBOLS


class USRefreshTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock(us_index_rows={}, us_stock_symbols={"A": "A", "B": "B"},
                        us_history_loaded={}, us_pending=set(), us_errors={})

    def test_slow_adr_does_not_block_or_duplicate_other_jobs(self):
        app = self.app
        app.us_pending.add("ADR")
        with patch("news_reader.stock_dynamic.threading.Thread") as thread:
            StockDynamicApp.refresh_us_sections(app)
            StockDynamicApp.refresh_us_sections(app)
        started = [call.kwargs["args"][0] for call in thread.call_args_list]
        self.assertCountEqual(started, ["指數", "美國個股", *US_INDEX_SYMBOLS])

    def test_partial_missing_and_older_quotes_keep_previous_record(self):
        app = self.app
        old = SimpleNamespace(symbol="A", price=11, market_timestamp=150, market_time="09:01")
        fresh = SimpleNamespace(symbol="B", price=20, market_timestamp=200, market_time="09:02")
        app.us_stock_rows = {"A": old}
        for price, timestamp in ((None, 160), (10, 140), (12, None)):
            bad = SimpleNamespace(symbol="A", price=price, market_timestamp=timestamp)
            StockDynamicApp.apply_us_section(app, "美國個股", [bad, fresh], None)
            self.assertIs(app.us_stock_rows["A"], old)
            app.update_security_tree.assert_called_with(app.us_stock_tree, [old, fresh])
            self.assertIn("美國個股", app.us_errors)

    def test_failed_history_keeps_existing_series_and_grain(self):
        app = self.app
        name = next(iter(US_INDEX_SYMBOLS))
        app.us_pending.add(name)
        app.us_candles = {name: ["old"]}
        app.us_candle_grains = {name: "日 K"}
        StockDynamicApp.apply_us_section(app, name, None, "timeout")
        self.assertEqual(app.us_candles[name], ["old"])
        self.assertEqual(app.us_candle_grains[name], "日 K")
        self.assertNotIn(name, app.us_pending)
