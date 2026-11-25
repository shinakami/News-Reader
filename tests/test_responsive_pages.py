"""Offline Tk geometry regression: resize the real pages, not just breakpoints."""
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from news_reader.stock_dynamic import StockDynamicApp, update_quote_row, movement_tag


class ResponsivePages(unittest.TestCase):
    def test_height_resize_and_compact_to_wide(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        with patch.object(StockDynamicApp, "refresh"):
            app = StockDynamicApp(root, 30, "auto", 5, 0, True, True, 80)
            # Exercise the reported desktop size even on a smaller test monitor.
            root.maxsize(2400, 1600)
            app.switch_market_page()
            self.assertEqual(app.page_title.get(), "美股行情")
            tree = app.tw_stock_tree
            update_quote_row(tree, "2330", ("2330",), movement_tag(1))
            tree.selection_set("2330")
            tree.focus("2330")
            update_quote_row(tree, "2330", ("2330", "台積電"), movement_tag(0))
            self.assertEqual(tree.selection(), ("2330",))
            self.assertEqual(tree.focus(), "2330")
            self.assertEqual(tree.item("2330", "tags"), ("neutral",))
            self.assertTrue(tree.bind("<Return>"))
            for width, height in [(680, 520), (1912, 750), (1912, 1027), (680, 520)]:
                root.geometry(f"{width}x{height}")
                root.update()
                for canvas, content in [(app.us_scroll_canvas, app.us_content),
                                        (app.taiwan_scroll_canvas, app.taiwan_content)]:
                    self.assertEqual(content.winfo_height(),
                                     max(canvas.winfo_height(), content.winfo_reqheight()))
                charts = list(app.us_chart_canvases.values())
                self.assertAlmostEqual(max(c.winfo_y() + c.winfo_height() for c in charts),
                                       app.us_chart_grid.winfo_height(), delta=2)
                if width == 680:
                    self.assertEqual({int(b.grid_info()["row"]) for b in app.header_action_buttons}, {0, 1})
                    app.us_scroll_canvas.yview_moveto(1)
                    self.assertAlmostEqual(app.us_scroll_canvas.yview()[1], 1)
                elif height == 750:
                    short_chart = charts[0].winfo_height()
                else:
                    self.assertGreater(charts[0].winfo_height(), short_chart)
