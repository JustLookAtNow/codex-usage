"""Opt-in packaged UI smoke test. Never reads credentials or accesses the network."""
import json
import importlib.util
import sys
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from .app import MonitorApp, enable_dpi_awareness
from .core import Outcome, PollResult, parse_credits, parse_usage
from .settings import Settings
from .startup import MemoryStartup


def run_smoke_test(directory):
    output = Path(directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    Settings().save(output / "settings.json")
    enable_dpi_awareness()
    root = tk.Tk()
    startup = MemoryStartup()
    app = MonitorApp(root, SimpleNamespace(close=lambda: None), output / "settings.json", auto_poll=False, startup=startup)
    now = datetime.now(timezone.utc)
    app._position = (100, 100)
    app._layout()
    result = PollResult(Outcome(parse_usage({"plan_type": "demo", "rate_limit": {
        "primary_window": {"used_percent": 26.4, "limit_window_seconds": 18000,
                           "reset_at": (now + timedelta(hours=3)).timestamp()},
        "secondary_window": {"used_percent": 62, "limit_window_seconds": 604800,
                             "reset_at": (now + timedelta(days=4)).timestamp()}}})),
        Outcome(parse_credits({"available_count": 2, "credits": [
            {"title": "额度重置", "status": "available", "expires_at": (now + timedelta(days=7)).isoformat()},
            {"title": "额度重置", "status": "available", "expires_at": (now + timedelta(days=14)).isoformat()}]})), now)
    app.apply_result(result)
    checks = {"frozen": bool(getattr(sys, "frozen", False)),
              "socks_available": importlib.util.find_spec("socks") is not None}
    errors = []

    def callback_error(*args):
        errors.append("Tk callback failed")
        finish()
    root.report_callback_exception = callback_error

    def capture(name, windows=None):
        from PIL import Image, ImageGrab
        import ctypes
        root.update()
        windows = windows or [root]
        x = min(w.winfo_rootx() for w in windows)
        y = min(w.winfo_rooty() for w in windows)
        right = max(w.winfo_rootx() + w.winfo_width() for w in windows)
        bottom = max(w.winfo_rooty() + w.winfo_height() for w in windows)
        try:
            # Print only our own windows, including transparent rounded windows.
            images = []
            for w in windows:
                hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
                images.append(ImageGrab.grab(window=hwnd))
            if len(images) == 1:
                images[0].save(output / name)
            else:
                board = Image.new("RGB", (right - x, bottom - y), "#f4f6f7")
                for w, rendered in zip(windows, images):
                    board.paste(rendered, (w.winfo_rootx() - x, w.winfo_rooty() - y))
                board.save(output / name)
            checks["screen_capture_available"] = True
        except OSError:
            checks["screen_capture_available"] = False

    def start():
        app._hide_details()
        checks["collapsed_on_start"] = not app.expanded
        checks["details_hidden_on_start"] = not app.details_visible
        checks["tray_ready"] = app.tray_ready
        capture("collapsed.png")
        app._hover_enter()
        root.after(220, hovered)

    def hovered():
        checks["hover_details"] = app.details_visible
        capture("hover.png", [root, app.popover])
        app.credits_button.invoke()
        root.after(300, expanded)

    def expanded():
        checks["expanded"] = bool(app.credit_details.winfo_ismapped())
        checks["capsule_stays_compact"] = root.winfo_height() == app.height
        capture("expanded.png", [root, app.popover])
        app.open_settings()
        dialog = app.preferences
        dialog.color.set("#3377cc")
        dialog.proxy_modes.select("manual")
        dialog.address.set("http://127.0.0.1:7890")
        dialog.startup_toggle.invoke()
        root.after(300, preferences)

    def preferences():
        capture("settings.png", [app.preferences.window])
        app.preferences.save_button.invoke()
        checks["preferences_saved"] = app.preferences is None and app.settings.proxy_mode == "manual" and app.settings.accent == "#3377cc"
        checks["autostart_saved_simulated"] = startup.read() == startup.command
        app._show_details()
        root.after(250, themed)

    def themed():
        capture("blue-theme.png", [root, app.popover])
        app.hide()
        checks["hidden"] = root.state() == "withdrawn"
        app.events.put(("show", None))
        root.after(300, restored)

    def restored():
        checks["restored"] = root.state() == "normal"
        app.events.put(("pin", None))
        root.after(300, finish)

    def finish():
        checks["pin_toggled"] = not app.topmost
        checks["errors"] = errors
        (output / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
        app.events.put(("exit", None))

    root.after(2500, start)
    root.mainloop()
    checks["exited"] = app.closed
    passed = all(checks.get(k) for k in ("collapsed_on_start", "details_hidden_on_start", "hover_details", "socks_available",
                                       "capsule_stays_compact", "preferences_saved", "autostart_saved_simulated", "tray_ready", "expanded",
                                       "hidden", "restored", "pin_toggled", "exited")) and not errors
    checks["passed"] = passed
    (output / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    return 0 if passed else 1
