"""Real Tk widgets with synthetic quota data; no network or shared credentials."""
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from codex_usage.app import MonitorApp, countdown
from codex_usage.core import Outcome, PollResult, parse_credits, parse_usage
from codex_usage.settings import Settings
from codex_usage.startup import MemoryStartup, StartupError
from tests.test_core import usage_payload


def sample_result():
    return PollResult(Outcome(parse_usage(usage_payload())), Outcome(parse_credits({
        "available_count": 2, "credits": [
            {"title": "额度重置", "status": "available", "expires_at": "2027-01-01T00:00:00Z"},
            {"title": "额度重置", "status": "available", "expires_at": "2027-02-01T00:00:00Z"}]})),
        datetime.now(timezone.utc))


class FakeClient:
    def close(self):
        pass


class UiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.app = MonitorApp(self.root, FakeClient(), Path(self.temp.name) / "settings.json",
                              start_tray=False, auto_poll=False, startup=MemoryStartup())
        self.addCleanup(self.app.quit)
        self.root.update()

    def test_default_collapse_and_click_toggle_preserves_state(self):
        self.app.apply_result(sample_result())
        self.assertFalse(self.app.expanded)
        self.assertFalse(self.app.credit_details.winfo_ismapped())
        self.assertFalse(self.app.details_visible)
        capsule_height = self.root.winfo_height()
        self.app._show_details()
        self.root.update()
        collapsed_height = self.app.popover.winfo_height()
        self.app.credits_button.invoke()
        self.root.update()
        self.assertTrue(self.app.credit_details.winfo_ismapped())
        self.assertGreater(self.app.popover.winfo_height(), collapsed_height)
        self.assertEqual(self.root.winfo_height(), capsule_height)
        self.app.apply_result(sample_result())
        self.assertTrue(self.app.expanded)
        self.app.credits_button.invoke()
        self.assertFalse(self.app.expanded)

    def test_failure_retains_data_and_success_recovers(self):
        result = sample_result()
        self.app.apply_result(result)
        self.app.apply_result(PollResult(Outcome(error="网络连接超时"), Outcome(error="访问受限"), result.at))
        self.assertEqual(self.app.usage_at, result.at)
        self.assertEqual(self.app.usage, result.usage.value)
        self.assertEqual(self.app.credits, result.credits.value)
        self.assertIn("已过期", self.app.status_label.cget("text"))
        self.app.apply_result(result)
        self.assertIsNone(self.app.usage_error)
        self.assertEqual(self.app.status_label.cget("text"), "")

    def test_position_and_topmost(self):
        self.app._position = (100, 110)
        self.app.save_position()
        self.assertEqual(self.app.load_position(), (100, 110))
        self.app.toggle_topmost()
        self.assertFalse(self.app.topmost)
        self.app.toggle_topmost()
        self.assertTrue(self.app.topmost)

    def test_tray_hide_and_queued_restore(self):
        self.app.tray_ready = True
        self.app.close_button.invoke()
        self.assertEqual(self.root.state(), "withdrawn")
        self.app.events.put(("show", None))
        self.app._drain()
        self.assertEqual(self.root.state(), "normal")

    def test_refresh_is_single_flight_and_queue_returns_to_tk(self):
        gate = threading.Event()
        started = threading.Event()
        calls = []
        def poll():
            calls.append(1)
            started.set()
            gate.wait(3)
            return sample_result()
        self.app.client.poll = poll
        self.app.refresh()
        self.assertTrue(started.wait(1))
        self.app.refresh()
        self.assertEqual(len(calls), 1)
        gate.set()
        deadline = time.monotonic() + 3
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.busy)
        self.assertIsNotNone(self.app.usage)

    def test_countdown_and_missing_reset(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(countdown(None), "未提供")
        self.assertIn("等待更新", countdown(now - timedelta(seconds=1), now))
        self.assertIn("2小时", countdown(now + timedelta(hours=2), now))

    def test_hover_opens_and_only_closes_outside_both_surfaces(self):
        self.app._hover_enter()
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(self.app.details_visible)
        with patch.object(self.app, "_pointer_inside", return_value=True):
            self.app._maybe_hide_details()
            self.assertTrue(self.app.details_visible)
        with patch.object(self.app, "_pointer_inside", return_value=False):
            self.app._maybe_hide_details()
            self.assertFalse(self.app.details_visible)

    def test_context_menu_settings_command_remains_usable(self):
        # Windows native menus run a modal message loop; don't block a unit test on it.
        with patch.object(tk.Menu, "tk_popup") as popup:
            self.app._context_menu(SimpleNamespace(x_root=100, y_root=100))
            popup.assert_called_once_with(100, 100)
        self.assertTrue(self.app._menu.winfo_exists())
        self.app._menu.invoke(0)
        self.assertIsNotNone(self.app.preferences)
        self.app.preferences.close()

    def test_exit_from_context_menu_cleans_up_without_tcl_error(self):
        with patch.object(tk.Menu, "tk_popup", side_effect=lambda *args: self.app.quit()):
            self.app._context_menu(SimpleNamespace(x_root=100, y_root=100))
        self.assertTrue(self.app.closed)

    def test_summary_is_only_ordinary_5h_and_weekly(self):
        data = usage_payload()
        data["rate_limit"]["primary_window"] = None
        data["additional_rate_limits"] = [{"limit_name": "Other model", "rate_limit": {
            "primary_window": {"used_percent": 1, "limit_window_seconds": 18000}}}]
        self.app.apply_result(PollResult(Outcome(parse_usage(data)), Outcome(), datetime.now(timezone.utc)))
        self.assertEqual(self.app.summary_values[0], ("5h", "—", "—"))
        self.assertEqual(self.app.summary_values[1][0:2], ("Weekly", "38%"))

    def test_settings_validate_save_theme_proxy_and_cancel(self):
        self.app.open_settings()
        dialog = self.app.preferences
        dialog.color.set("#3377cc")
        dialog.mode.set("manual")
        dialog.address.set("bad")
        dialog.save_button.invoke()
        self.assertIs(self.app.preferences, dialog)
        self.assertIn("代理地址", dialog.message.get())
        dialog.address.set("socks5://127.0.0.1:1080")
        dialog.save_button.invoke()
        self.assertIsNone(self.app.preferences)
        saved = Settings.load(self.app.settings_path)
        self.assertEqual(saved.accent, "#3377cc")
        self.assertEqual(saved.proxy_url, "socks5h://127.0.0.1:1080")
        self.app.save_position()
        self.assertEqual(Settings.load(self.app.settings_path).proxy_url, saved.proxy_url)
        self.app.open_settings()
        self.app.preferences.color.set("#aa7824")
        self.app.preferences.close()
        self.assertEqual(self.app.settings.accent, "#3377cc")

    def test_failed_settings_save_does_not_change_live_settings(self):
        with patch.object(Settings, "save", side_effect=OSError):
            with self.assertRaises(OSError):
                self.app.apply_preferences("#8561c5", "direct", "")
        self.assertEqual(self.app.settings.proxy_mode, "system")

    def test_proxy_change_waits_for_inflight_poll_and_discards_old_result(self):
        self.app.busy = True
        self.app.apply_preferences("#278054", "manual", "http://127.0.0.1:7890")
        self.assertTrue(self.app._pending_refresh)
        self.app.events.put(("result", sample_result()))
        with patch.object(self.app, "refresh") as refresh:
            self.app._drain()
            refresh.assert_called_once()
        self.assertIsNone(self.app.usage)
        self.assertFalse(self.app._pending_refresh)

    def test_flat_proxy_segments_update_entry_state_and_keyboard_selection(self):
        self.app.open_settings()
        dialog = self.app.preferences
        self.assertEqual(dialog.proxy_entry.cget("state"), "disabled")
        dialog.proxy_modes.select("manual")
        self.assertEqual(dialog.proxy_entry.cget("state"), "normal")
        dialog.proxy_modes._step(-1)
        self.assertEqual(dialog.mode.get(), "direct")
        self.assertEqual(dialog.proxy_entry.cget("state"), "disabled")
        dialog.close()

    def test_startup_switch_is_only_committed_on_save_and_can_be_disabled(self):
        self.app.open_settings()
        dialog = self.app.preferences
        self.assertFalse(dialog.autostart.get())
        dialog.startup_toggle.invoke()
        self.assertTrue(dialog.autostart.get())
        self.assertIsNone(self.app.startup.read())
        dialog.close()
        self.assertIsNone(self.app.startup.read())
        self.app.open_settings()
        self.app.preferences.startup_toggle.invoke()
        self.app.preferences.save_button.invoke()
        self.assertEqual(self.app.startup.read(), self.app.startup.command)
        self.app.open_settings()
        self.assertTrue(self.app.preferences.autostart.get())
        self.app.preferences.startup_toggle.invoke()
        self.app.preferences.save_button.invoke()
        self.assertIsNone(self.app.startup.read())

    def test_failed_config_save_restores_exact_previous_startup_registration(self):
        self.app.startup.write('"C:\\Old Version\\CodexUsage.exe"')
        before = self.app.startup.read()
        with patch.object(Settings, "save", side_effect=OSError):
            with self.assertRaises(OSError):
                self.app.apply_preferences("#8561c5", "direct", "", False)
        self.assertEqual(self.app.startup.read(), before)
        self.assertEqual(self.app.settings.proxy_mode, "system")

    def test_startup_registration_error_keeps_dialog_open_and_config_unchanged(self):
        self.app.open_settings()
        dialog = self.app.preferences
        dialog.startup_toggle.invoke()
        with patch.object(self.app.startup, "write", side_effect=StartupError("无法更新开机启动项")):
            dialog.save_button.invoke()
        self.assertIs(self.app.preferences, dialog)
        self.assertIn("无法更新", dialog.message.get())
        self.assertFalse(self.app.settings_path.exists())
        dialog.close()

    def test_unreadable_startup_state_disables_only_the_switch(self):
        with patch.object(self.app.startup, "read", side_effect=StartupError("无法读取")):
            self.app.open_settings()
            dialog = self.app.preferences
            self.assertFalse(dialog.startup_available)
            dialog.startup_toggle.invoke()
            self.assertFalse(dialog.autostart.get())
            dialog.color.set("#3377cc")
            dialog.save_button.invoke()
        self.assertIsNone(self.app.preferences)
        self.assertEqual(self.app.settings.accent, "#3377cc")


if __name__ == "__main__":
    unittest.main()
