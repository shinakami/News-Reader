import sys
import unittest
from threading import Event
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from news_reader.stock_dynamic import StockDynamicApp, fetch_all_etf_quotes
from news_reader.stock_monitor import YAHOO_INDICES, fetch_market_data


class TaiwanRefreshTests(unittest.TestCase):
    def test_etf_quotes_appear_before_profile_completes(self):
        received_live = Event()
        previews = []
        def preview(rows):
            previews.append(rows[0])
            if rows[0].source == "TWSE MIS":
                received_live.set()
        def slow_profile(*_):
            if received_live.wait(2):
                return {"0050": {"totalAv": "1234"}}
            return {}
        with patch("news_reader.stock_dynamic.fetch_etf_profile_map", side_effect=slow_profile), patch(
                "news_reader.stock_dynamic.fetch_etf_list", return_value=[
                    {"Code": "0050", "ClosingPrice": "15", "Date": "20261125"}]), patch(
                "news_reader.stock_dynamic.fetch_twse_json", return_value={
                    "msgArray": [{"c": "0050", "z": "16", "d": "20261126", "t": "09:10:00"}]}):
            final = fetch_all_etf_quotes(5, 0, True, preview)
        self.assertEqual([r.price for r in previews], [15, 16])
        self.assertEqual(final[0].market_value, 1234)
        self.assertEqual(final[0].market_time, "20261126 09:10:00")

    def test_active_section_does_not_block_other_refreshes(self):
        app = Mock()
        app.loading = app.closed = app.refresh_job = False
        app.active_page = "taiwan"
        app.interval = 10
        app.tw_pending = {"ETF"}
        app.vix_panels = {}
        app.vix_history_due = {"taiwan": float("inf")}
        app.tw_vix_next = float("inf")
        app.tw_stock_symbols = {}
        app.tw_api_time = ""
        app.tw_history_key = None
        started = []
        def thread(target, args, daemon):
            return SimpleNamespace(start=lambda: started.append(args[0]))
        with patch("news_reader.stock_dynamic.threading.Thread", thread):
            StockDynamicApp.refresh(app)
            StockDynamicApp.refresh(app)
        self.assertCountEqual(started, ["指數", "個股", "K 線"])

    def test_missing_stock_keeps_price_and_time_and_failed_etf_is_untouched(self):
        app = Mock()
        app.tw_pending = {"ETF", "個股"}
        app.tw_errors = {}
        old = SimpleNamespace(price=12, market_time="09:01:00")
        new = SimpleNamespace(price=17, market_time="09:03:02")
        app.tw_stock_rows = {"A": old}
        app.tw_stock_symbols = {"A": "A", "B": "B"}
        StockDynamicApp.apply_tw_section(app, "ETF", None, "timeout")
        StockDynamicApp.apply_tw_section(app, "個股", {"A": SimpleNamespace(price=None), "B": new}, None)
        app.update_taiwan_stocks.assert_called_once_with({"A": old, "B": new})
        app.update_etfs.assert_not_called()
        self.assertEqual(app.tw_pending, set())

    def test_auto_fallback_uses_yahoo_taiwan_batch(self):
        items = {s: {"chart": {"quote": {"price": "12", "refreshedTs": "2026-11-26T01:10:00Z"}}}
                 for s in YAHOO_INDICES}
        with patch("news_reader.stock_monitor.fetch_twse_json", side_effect=RuntimeError), patch(
                "news_reader.stock_monitor.fetch_yahoo_tw_chart_items", return_value=items) as batch:
            quotes, stamp, source, _ = fetch_market_data(5, True, 0, "auto", False)
        batch.assert_called_once_with(list(YAHOO_INDICES), 5, 0)
        self.assertEqual((len(quotes), source), (4, "Yahoo"))
