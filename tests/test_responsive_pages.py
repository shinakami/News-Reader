"""Offline Tk geometry regression: resize the real pages, not just breakpoints."""
import sys
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from news_reader.stock_dynamic import StockDynamicApp, update_quote_row, movement_tag


class ResponsivePages(unittest.TestCase):
    def test_high_dpi_compact_menu_and_readable_table(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        old_scale = root.tk.call("tk", "scaling")
        self.addCleanup(root.tk.call, "tk", "scaling", old_scale)
        root.tk.call("tk", "scaling", 2.0)  # 144 DPI (150% desktop scaling)
        with patch.object(StockDynamicApp, "refresh"):
            app = StockDynamicApp(root, 30, "auto", 5, 0, True, True, 80)
            root.geometry("800x600")
            root.update()
            self.assertTrue(app.compact_actions.winfo_ismapped())
            self.assertFalse(app.header_actions.winfo_ismapped())
            self.assertGreaterEqual(int(app.style.lookup("Treeview", "rowheight")), 46)
            self.assertLessEqual(app.compact_actions.winfo_rootx() + app.compact_actions.winfo_width(),
                                 root.winfo_rootx() + root.winfo_width())
            self.assertGreater(app.taiwan_scroll_canvas.winfo_height(), 150)
            self.assertEqual(app.main_pane.cget("orient"), "vertical")

    def test_vix_single_point_gap_and_history_view(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(root.destroy)
        with patch.object(StockDynamicApp, "refresh"):
            app = StockDynamicApp(root, 30, "auto", 5, 0, True, True, 80)
            for market, panel in app.vix_panels.items():
                if app.active_page != market:
                    app.switch_market_page()
                root.update()
                today = panel.today()
                first = datetime.combine(today, datetime.min.time())
                panel.history = [(today-timedelta(days=60-i), i+1) for i in range(60)]
                panel.update([(first, 15), (first+timedelta(minutes=10), 54)])
                self.assertIn("高度緊張", panel.status.get())
                self.assertFalse(any(panel.mini.type(k) == "line" for k in panel.mini.find_all()))
                for moment in (1000, 1100):
                    panel.mini.event_generate("<ButtonPress-1>", x=10, y=10, time=moment)
                    panel.mini.event_generate("<ButtonRelease-1>", x=10, y=10, time=moment+10)
                root.update()
                self.assertTrue(panel.window.winfo_viewable())
                panel.mode.set("近 60 日收盤")
                panel.redraw()
                self.assertEqual(sum(panel.large.type(k) == "oval" for k in panel.large.find_all()), 60)
                root.update()
                chart = panel.large
                x,y,stamp,value = chart.samples["sample59"]
                chart.event_generate("<Button-1>", x=round(x), y=round(y))
                self.assertEqual(chart.selected_time, stamp)
                panel.redraw()
                self.assertTrue(any(str(stamp) in chart.itemcget(k, "text")
                                    for k in chart.find_withtag("inspection") if chart.type(k) == "text"))
                panel.mode.set("今日盤中")
                panel.redraw()
                a,b = chart.samples.values()
                chart.event_generate("<Button-1>", x=round((a[0]+b[0])/2), y=round((a[1]+b[1])/2))
                self.assertFalse(chart.find_withtag("inspection"))
                chart.event_generate("<Button-1>", x=round(a[0]), y=round(a[1]))
                self.assertEqual(chart.selected_time, first)
                panel.window.destroy()

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
