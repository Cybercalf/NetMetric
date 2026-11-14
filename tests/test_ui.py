"""使用内存快照测试 Tk 交互，不调用系统网络写入。"""
from dataclasses import replace
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from models import Iface, Route, assemble_snapshot
from preferences import Preferences
from ui import App, COLUMN_MINIMUMS, COLUMN_WIDTHS


class FakeController:
    def __init__(self):
        self.events = queue.Queue()
        self.applied = []
    def start(self):
        pass
    def stop(self):
        pass
    def apply(self, plan):
        self.applied.append(plan)
    def set_paused(self, paused):
        self.paused = paused
    def refresh(self):
        pass


class UiTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.app = App(controller=self.controller, admin=True, remember_layout=False)
        a = Iface(1, "以太网", "测试网卡", True, 100, False, True, ("10.0.0.2",))
        b = Iface(2, "WLAN", "无线网卡", True, 20, True, True, ("10.1.0.2",))
        c = replace(a, index=3, alias="已断开", connected=False, if_metric=5, ipv4=())
        self.app.last = assemble_snapshot([a, b, c], [
            Route(1, "0.0.0.0", "0.0.0.0", "10.0.0.1", 110),
            Route(2, "0.0.0.0", "0.0.0.0", "10.1.0.1", 40)])
        self.app.render()
        self.app.update()

    def tearDown(self):
        self.app.destroy()

    def test_search_filter_and_numeric_sort(self):
        self.app.sort_by("metric")
        self.assertEqual(self.app.tree.get_children(), ("3", "2", "1"))
        self.app.filter_var.set("已连接")
        self.app.render()
        self.assertEqual(len(self.app.tree.get_children()), 2)
        self.app.search_var.set("10.1.0.2")
        self.assertEqual(self.app.tree.get_children(), ("2",))

    def test_read_only_and_disconnected_disable_editing(self):
        self.app.tree.selection_set("3")
        self.app.update_details()
        self.assertTrue(self.app.edit_button.instate(["disabled"]))
        self.app.tree.selection_set("1")
        self.app.admin = False
        self.app.update_details()
        self.assertTrue(self.app.edit_button.instate(["disabled"]))

    def test_invalid_inline_input_never_submits(self):
        self.app.tree.selection_set("1")
        self.app.edit_selected()
        self.app.update()
        self.assertIsNotNone(self.app.editor)
        self.app.editor.delete(0, "end")
        self.app.editor.insert(0, "99999")
        self.app.editor.event_generate("<Return>")
        self.app.update()
        self.assertEqual(self.controller.applied, [])
        self.assertIsNotNone(self.app.editor)

    def test_cancel_confirmation_never_submits(self):
        with patch("ui.messagebox.askokcancel", return_value=False):
            self.app.request_metric(self.app.last.find(1), 10)
        self.assertFalse(self.controller.applied)

    def test_confirmed_change_is_queued(self):
        with patch("ui.messagebox.askokcancel", return_value=True):
            self.app.request_metric(self.app.last.find(1), 10)
        self.assertEqual(self.controller.applied[0].changes[0].after.metric, 10)
        self.assertTrue(self.app.busy)

    def test_hidden_route_column_keeps_metric_column_identity(self):
        self.app.show_route_metric.set(False)
        self.app.toggle_route_metric()
        self.assertEqual(self.app.tree.column("#5", "id"), "metric")

    def test_route_details_contains_all_routes(self):
        self.app.tree.selection_set("1")
        self.app.show_routes()
        self.assertEqual(len(self.app.route_tree.get_children()), 1)
        self.app.last = assemble_snapshot([], [])
        self.app.render_routes()
        self.assertEqual(len(self.app.route_tree.get_children()), 0)

    def test_render_keeps_selection(self):
        self.app.tree.selection_set("2")
        self.app.render()
        self.assertEqual(self.app.tree.selection(), ("2",))

    def test_all_columns_can_shrink_below_original_width(self):
        for column, original in COLUMN_WIDTHS.items():
            self.assertLess(self.app.tree.column(column, "minwidth"), original)
            self.assertFalse(self.app.tree.column(column, "stretch"))
        self.app.set_column_widths({"description": 140})
        self.app.update()
        self.assertEqual(self.app.tree.column("description", "width"), 140)

    def test_fit_content_expands_long_description_and_refresh_keeps_width(self):
        self.app.tree.set("1", "description", "A very long adapter description " * 6)
        self.app.fit_column("description")
        width = self.app.tree.column("description", "width")
        self.assertGreater(width, COLUMN_WIDTHS["description"])
        self.assertLessEqual(width, 600)
        self.app.render()
        self.assertEqual(self.app.tree.column("description", "width"), width)

    def test_fit_to_window_fits_visible_columns_and_keeps_minimums(self):
        self.app.show_route_metric.set(False)
        self.app.toggle_route_metric()
        self.app.fit_columns_to_window()
        self.app.update()
        widths = {c: self.app.tree.column(c, "width") for c in self.app.visible_columns()}
        self.assertLessEqual(sum(widths.values()), self.app.tree.winfo_width())
        self.assertTrue(all(width >= COLUMN_MINIMUMS[c] for c, width in widths.items()))

    def test_double_click_separator_fits_column_without_opening_details(self):
        x = self.app.tree.column("dot", "width")
        boundary = next(p for p in range(x - 4, x + 5)
                        if self.app.tree.identify_region(p, 10) == "separator")
        with patch.object(self.app, "fit_column") as fit:
            self.assertEqual(self.app.on_double_click(SimpleNamespace(x=boundary, y=10)), "break")
            fit.assert_called_once_with("dot")
        self.assertIsNone(self.app.route_window)

    def test_layout_persists_and_restores_hidden_column_width(self):
        with tempfile.TemporaryDirectory() as folder:
            preferences = Preferences(Path(folder) / "ui.json")
            self.app.preferences = preferences
            self.app.remember_layout = True
            self.app.set_column_widths({"description": 333, "route_metric": 112})
            self.app.show_route_metric.set(False)
            self.app.toggle_route_metric()
            self.app.save_column_layout()
            other = App(controller=FakeController(), admin=False, preferences=preferences)
            try:
                self.assertEqual(other.tree.column("description", "width"), 333)
                self.assertEqual(other.tree.column("route_metric", "width"), 112)
                self.assertFalse(other.show_route_metric.get())
            finally:
                other.destroy()
                self.app.remember_layout = False

    def test_column_width_dialog_opens(self):
        self.app.show_column_settings()
        self.app.update()
        self.assertTrue(self.app.column_dialog.winfo_exists())


if __name__ == "__main__":
    unittest.main()
