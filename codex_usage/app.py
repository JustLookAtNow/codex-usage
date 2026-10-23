"""Light capsule, hover popover and tray. Only Tk's thread touches widgets."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from tkinter import font as tkfont

from .core import Credits, Outcome, PollResult, QuotaClient, Usage
from .settings import Settings, readable_accent, settings_path as default_settings_path, validate_accent, validate_proxy
from .startup import StartupManager, StartupError

BG, TEXT, MUTED = "#ffffff", "#26313d", "#788391"
BORDER, TRACK, WARNING = "#e4e8eb", "#edf0f2", "#a16d22"
TRANSPARENT = "#f1f2f3"
WIDTH, HEIGHT = 272, 28
PANEL_WIDTH = 286
SUMMARY_TEXT = "#000000"


def enable_dpi_awareness():
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass


def local_time(value):
    return value.astimezone().strftime("%m-%d %H:%M") if value else "未提供"


def countdown(value, now=None, compact=False):
    if value is None:
        return "—" if compact else "未提供"
    seconds = int((value - (now or datetime.now(timezone.utc))).total_seconds())
    if seconds <= 0:
        return "待更新" if compact else "已到重置时间，等待更新"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if compact:
        return f"{days}d{hours:02d}h" if days else f"{hours}h{minutes:02d}m"
    if days:
        return f"{days}天 {hours}小时后重置"
    if hours:
        return f"{hours}小时 {minutes}分后重置"
    return f"{minutes}分 {rest % 60}秒后重置"


def tray_image(accent="#278054"):
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=18, fill="#ffffff", outline="#d8dfdf", width=2)
    draw.arc((16, 16, 48, 48), 35, 320, fill=accent, width=7)
    return image


def round_box(canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=32, **kwargs)


class MonitorApp:
    def __init__(self, root: tk.Tk, client=None, settings_path: Path | None = None,
                 start_tray=True, auto_poll=True, startup=None):
        self.root = root
        self.scale = max(1, root.winfo_fpixels("1i") / 96)
        # Pixel-sized fonts avoid point-to-pixel inflation on the tiny capsule.
        families = set(tkfont.families(root))
        text_family = "Segoe UI Semibold" if "Segoe UI Semibold" in families else "Segoe UI"
        text_weight = "normal" if text_family == "Segoe UI Semibold" else "bold"
        self.summary_font = tkfont.Font(root=root, family=text_family, size=-self.px(12), weight=text_weight)
        self.summary_value_font = tkfont.Font(root=root, family="Segoe UI", size=-self.px(14), weight="bold")
        self.summary_time_font = tkfont.Font(root=root, family=text_family, size=-self.px(12), weight=text_weight)
        self._value_width = self.summary_value_font.measure("100%")
        self._time_width = max(self.summary_time_font.measure(t) for t in ("23h59m", "6d23h", "待更新"))
        self._group_widths = tuple(self.summary_font.measure(label) + self.px(8) + self._value_width + self._time_width
                                   for label in ("5h", "Weekly"))
        self.width, self.height = self.px(WIDTH), self.px(HEIGHT)
        self.width = max(self.width, sum(self._group_widths) + self.px(32))
        self.panel_width = self.px(PANEL_WIDTH)
        self.settings_path = settings_path or default_settings_path()
        self.settings = Settings.load(self.settings_path)
        self.accent = readable_accent(self.settings.accent)
        self.client = client or QuotaClient()
        self.startup = startup if startup is not None else StartupManager()
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = self.closed = self.tray_ready = False
        self.tray = None
        self.topmost, self.expanded = True, False
        self.preferences = None
        self.usage: Usage | None = None
        self.credits: Credits | None = None
        self.usage_at = self.credits_at = None
        self.usage_error = self.credits_error = None
        self.window_rows = []
        self._timers = set()
        self._hover_timer = self._leave_timer = None
        self._drag = None
        self._pending_refresh = False
        self._auto_poll = auto_poll
        self._next_poll = time.monotonic() + 60
        self._position = self.load_position()
        root.title("Codex 余量")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        self._transparent(root)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        self.summary = tk.Canvas(root, width=self.width, height=self.height,
                                 bg=root.cget("bg"), highlightthickness=0, cursor="hand2")
        self.summary.pack()
        self.summary.bind("<ButtonPress-1>", self._drag_start)
        self.summary.bind("<B1-Motion>", self._drag_move)
        self.summary.bind("<ButtonRelease-1>", self._drag_end)
        self.summary.bind("<Button-3>", self._context_menu)
        root.bind("<Enter>", self._hover_enter)
        root.bind("<Leave>", self._hover_leave)
        self._make_popover()
        self._layout()
        self._render_summary()
        self._schedule(100, self._drain)
        self._schedule(1000, self._tick)
        if start_tray:
            threading.Thread(target=self._tray_worker, daemon=True, name="codex-tray").start()
        if auto_poll:
            self._schedule(200, self.refresh)

    def px(self, value):
        return round(value * self.scale)

    def _transparent(self, window):
        window.configure(bg=TRANSPARENT if os.name == "nt" else BG)
        if os.name == "nt":
            window.attributes("-transparentcolor", TRANSPARENT)

    def _schedule(self, delay, callback):
        if self.closed:
            return None
        holder = []
        def call():
            self._timers.discard(holder[0])
            if not self.closed:
                callback()
        holder.append(self.root.after(delay, call))
        self._timers.add(holder[0])
        return holder[0]

    def _cancel(self, timer):
        if timer in self._timers:
            self.root.after_cancel(timer)
            self._timers.discard(timer)

    def load_position(self):
        saved = Settings.load(self.settings_path)
        if saved.x is not None and saved.y is not None:
            return saved.x, saved.y
        return max(20, self.root.winfo_screenwidth() - self.width - 32), 64

    def save_position(self):
        self.settings.x, self.settings.y = self._position
        try:
            self.settings.save(self.settings_path)
        except OSError:
            pass

    def _bounds(self):
        if os.name == "nt":
            import ctypes
            return tuple(ctypes.windll.user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _layout(self):
        x, y = self._position
        left, top, sw, sh = self._bounds()
        x, y = max(left, min(x, left + sw - self.width)), max(top, min(y, top + sh - self.height))
        self._position = x, y
        self.root.geometry(f"{self.width}x{self.height}{x:+d}{y:+d}")
        if self.details_visible:
            self._place_details()

    def _label(self, parent, text="", fg=TEXT, size=8, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=BG, fg=fg,
                        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"), padx=0, pady=0, **kwargs)

    def _button(self, parent, text, command, **kwargs):
        return tk.Button(parent, text=text, command=command, bg=BG, fg=MUTED,
                         activebackground=TRACK, activeforeground=TEXT, relief="flat", bd=0,
                         cursor="hand2", font=("Microsoft YaHei UI", 8), padx=2, pady=0, **kwargs)

    def _summary_windows(self):
        ordinary = [w for w in self.usage.windows if not w.additional] if self.usage else []
        return tuple(next((w for w in ordinary if w.seconds == seconds), None) for seconds in (18000, 604800))

    def _render_summary(self):
        c = self.summary
        c.delete("all")
        # True circular end caps, joined by straight top and bottom edges.
        x1, y1, x2, y2 = 1, 1, self.width - 1, self.height - 1
        diameter = y2 - y1
        radius = diameter / 2
        c.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=BG, outline="")
        for left, start in ((x1, 90), (x2 - diameter, 270)):
            c.create_arc(left, y1, left + diameter, y2, start=start, extent=180,
                         style="pieslice", fill=BG, outline="")
            c.create_arc(left, y1, left + diameter, y2, start=start, extent=180,
                         style="arc", outline=BORDER, width=1)
        for y in (y1, y2):
            c.create_line(x1 + radius, y, x2 - radius, y, fill=BORDER)
        starts = (self.px(10), self.width - self.px(10) - self._group_widths[1])
        middle = (starts[0] + self._group_widths[0] + starts[1]) / 2
        c.create_line(middle, self.px(6), middle, self.height - self.px(6), fill=BORDER)
        self.summary_values = []
        for index, (name, window) in enumerate(zip(("5h", "Weekly"), self._summary_windows())):
            x = starts[index]
            y = round(self.height / 2)
            c.create_text(x, y, text=name, anchor="w", fill=SUMMARY_TEXT, font=self.summary_font)
            x += self.summary_font.measure(name) + self.px(4)
            percentage = f"{window.remaining:.0f}%" if window and window.remaining is not None else "—"
            color = WARNING if self.usage_error else self.accent
            c.create_text(x, y, text=percentage, anchor="w", fill=color, font=self.summary_value_font)
            x += self._value_width + self.px(4)
            timer = countdown(window.reset_at if window else None, compact=True)
            c.create_text(x, y, text=timer, anchor="w", fill=SUMMARY_TEXT, font=self.summary_time_font)
            self.summary_values.append((name, percentage, timer))

    def _make_popover(self):
        self.popover = tk.Toplevel(self.root)
        self.popover.withdraw()
        self.popover.overrideredirect(True)
        self.popover.attributes("-topmost", self.topmost)
        self._transparent(self.popover)
        self.popover.bind("<Enter>", self._hover_enter)
        self.popover.bind("<Leave>", self._hover_leave)
        self.popover.bind("<Escape>", lambda event: self._hide_details())
        self.tip_canvas = tk.Canvas(self.popover, bg=self.popover.cget("bg"), highlightthickness=0)
        self.tip_canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self.tip_canvas, bg=BG)
        self._content_id = self.tip_canvas.create_window(self.px(10), self.px(13), window=self.content, anchor="nw", width=self.panel_width - self.px(20))
        header = tk.Frame(self.content, bg=BG)
        header.pack(fill="x", pady=(0, self.px(6)))
        self._label(header, "Codex 余量", size=9, bold=True).pack(side="left")
        self.plan_label = self._label(header, "", size=7)
        self.plan_label.pack(side="left", padx=4)
        self.close_button = self._button(header, "×", self.hide, width=2)
        self.close_button.pack(side="right")
        self.settings_button = self._button(header, "设置", self.open_settings)
        self.settings_button.pack(side="right", padx=2)
        viewport = tk.Frame(self.content, bg=BG)
        viewport.pack(fill="x")
        self.body_canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
        self.body_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(viewport, command=self.body_canvas.yview)
        self.body_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.body_canvas, bg=BG)
        self._body_id = self.body_canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body_canvas.bind("<Configure>", lambda event: self.body_canvas.itemconfigure(self._body_id, width=event.width))
        self.popover.bind("<MouseWheel>", lambda event: self.body_canvas.yview_scroll(-int(event.delta / 120), "units"))
        self.windows_frame = tk.Frame(self.body, bg=BG)
        self.windows_frame.pack(fill="x")
        self.status_label = self._label(self.body, "正在读取用量…", MUTED, wraplength=self.panel_width - self.px(28), justify="left", anchor="w")
        self.status_label.pack(fill="x", pady=(0, 4))
        self.separator = tk.Frame(self.body, bg=BORDER, height=1)
        self.separator.pack(fill="x")
        self.credits_button = self._button(self.body, "▸  重置点", self.toggle_credits, anchor="w")
        self.credits_button.pack(fill="x", pady=(self.px(3), self.px(2)))
        self.credit_details = tk.Frame(self.body, bg=BG)
        self.credit_content = self.credit_details
        footer = tk.Frame(self.content, bg=BG)
        footer.pack(fill="x", pady=(self.px(3), 0))
        self.updated_label = self._label(footer, "每 60 秒更新", MUTED, 7)
        self.updated_label.pack(side="left")
        self.refresh_button = self._button(footer, "刷新 ↻", self.refresh)
        self.refresh_button.pack(side="right")
        self._render_credits()

    @property
    def details_visible(self):
        return self.popover.state() != "withdrawn"

    def _hover_enter(self, event=None):
        self._cancel(self._leave_timer)
        if not self._drag and self.preferences is None and not self.details_visible:
            self._cancel(self._hover_timer)
            self._hover_timer = self._schedule(160, self._show_details)

    def _hover_leave(self, event=None):
        self._cancel(self._hover_timer)
        self._cancel(self._leave_timer)
        self._leave_timer = self._schedule(280, self._maybe_hide_details)

    def _pointer_inside(self):
        x, y = self.root.winfo_pointerxy()
        for window in (self.root, self.popover):
            if window.state() != "withdrawn":
                wx, wy = window.winfo_rootx(), window.winfo_rooty()
                if wx <= x < wx + window.winfo_width() and wy <= y < wy + window.winfo_height():
                    return True
        return False

    def _maybe_hide_details(self):
        if not self._pointer_inside():
            self._hide_details()

    def _show_details(self):
        if self.closed or self._drag or self.preferences is not None or self.root.state() == "withdrawn":
            return
        self._place_details()
        self.popover.deiconify()
        self.popover.lift()
        self._update_times()

    def _hide_details(self):
        self._cancel(self._hover_timer)
        self._cancel(self._leave_timer)
        self.popover.withdraw()

    def _place_details(self):
        self.content.update_idletasks()
        natural = max(self.px(20), self.body.winfo_reqheight())
        maximum = min(self.px(320), int(self.root.winfo_screenheight() * 0.55))
        if natural > maximum:
            self.scrollbar.pack(side="right", fill="y")
        else:
            self.scrollbar.pack_forget()
        self.body_canvas.configure(height=min(natural, maximum))
        self.content.update_idletasks()
        self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))
        h = self.content.winfo_reqheight() + self.px(22)
        x, ry = self._position
        x += (self.width - self.panel_width) // 2
        left, top, sw, sh = self._bounds()
        x = max(left, min(x, left + sw - self.panel_width))
        y = ry + self.height + self.px(3)
        above = y + h > top + sh
        if above:
            y = max(top, ry - h - self.px(3))
        self.popover.geometry(f"{self.panel_width}x{h}{x:+d}{y:+d}")
        c = self.tip_canvas
        c.delete("backdrop")
        arrow = self.px(5)
        round_box(c, 1, 1 if above else arrow, self.panel_width - 1, h - arrow if above else h - 1,
                  self.px(8), fill=BG, outline=BORDER, tags="backdrop")
        mid = max(self.px(20), min(self._position[0] + self.width / 2 - x, self.panel_width - self.px(20)))
        base, tip = (h - arrow - 1, h - 1) if above else (arrow + 1, 0)
        c.create_polygon(mid - arrow, base, mid, tip, mid + arrow, base,
                         fill=BG, outline=BORDER, tags="backdrop")
        c.create_line(mid - arrow + 1, base, mid + arrow - 1, base, fill=BG, tags="backdrop")
        c.tag_lower("backdrop")
        c.coords(self._content_id, self.px(10), self.px(8 if above else 13))

    def _drag_start(self, event):
        self._drag = event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y()
        self._hide_details()

    def _drag_move(self, event):
        if self._drag:
            self._position = event.x_root - self._drag[0], event.y_root - self._drag[1]
            x, y = self._position
            self.root.geometry(f"{x:+d}{y:+d}")

    def _drag_end(self, event):
        self._drag = None
        self._layout()
        self.save_position()
        self._hover_enter()

    def _context_menu(self, event):
        self._hide_details()
        previous = getattr(self, "_menu", None)
        if previous is not None:
            previous.destroy()
        menu = tk.Menu(self.root, tearoff=False, bg=BG, fg=TEXT)
        self._menu = menu
        menu.add_command(label="设置", command=self.open_settings)
        menu.add_command(label="刷新", command=self.refresh)
        menu.add_command(label="取消置顶" if self.topmost else "置顶", command=self.toggle_topmost)
        menu.add_separator()
        menu.add_command(label="隐藏到托盘", command=self.hide)
        menu.add_command(label="退出", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass  # The Exit command may already have destroyed the root.

    def toggle_topmost(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)
        self.popover.attributes("-topmost", self.topmost)
        if self.preferences:
            self.preferences.window.attributes("-topmost", self.topmost)

    def toggle_credits(self):
        self.expanded = not self.expanded
        self.credits_button.configure(text="▾  重置点" if self.expanded else "▸  重置点")
        if self.expanded:
            self.credit_details.pack(fill="x", pady=(0, 3))
        else:
            self.credit_details.pack_forget()
        if self.details_visible:
            self._place_details()

    def open_settings(self):
        self._hide_details()
        if self.preferences:
            self.preferences.window.deiconify()
            self.preferences.window.lift()
            return
        from .preferences import PreferencesDialog
        self.preferences = PreferencesDialog(self)

    def apply_preferences(self, accent, mode, address, autostart=None):
        accent, address = validate_accent(accent), validate_proxy(mode, address)
        changed_network = (mode, address) != (self.settings.proxy_mode, self.settings.proxy_url)
        proposed = replace(self.settings, accent=accent, proxy_mode=mode, proxy_url=address,
                           x=self._position[0], y=self._position[1])
        before = self.startup.read() if autostart is not None else None
        target = self.startup.command if autostart else None
        startup_changed = autostart is not None and target != before
        if startup_changed:
            self.startup.write(target)
        try:
            proposed.save(self.settings_path)
        except OSError:
            if startup_changed:
                try:
                    self.startup.write(before)
                except OSError:
                    raise StartupError("设置未保存，且无法恢复启动项；请在 Windows 启动应用中检查 CodexUsage。") from None
            raise
        self.settings = proposed
        self.accent = readable_accent(accent)
        self._render_windows()
        self._update_times()
        if self.tray and self.tray_ready:
            self.tray.icon = tray_image(self.accent)
        if changed_network:
            if self.busy:
                self._pending_refresh = True
            elif self._auto_poll:
                self.refresh()

    def refresh(self):
        if self.closed or self.busy:
            return
        if hasattr(self.client, "configure_network"):
            self.client.configure_network(self.settings.proxy_mode, self.settings.proxy_url)
        self.busy = True
        self.refresh_button.configure(text="查询中…", state="disabled")
        self._next_poll = time.monotonic() + 60
        threading.Thread(target=self._poll_worker, daemon=True, name="codex-http").start()

    def _poll_worker(self):
        try:
            result = self.client.poll()
        except Exception:
            error = Outcome(error="查询出现异常，将自动重试")
            result = PollResult(error, error, datetime.now(timezone.utc))
        self.events.put(("result", result))

    def _drain(self):
        for _ in range(100):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "result":
                self.busy = False
                self.refresh_button.configure(text="刷新 ↻", state="normal")
                if self._pending_refresh:
                    self._pending_refresh = False
                    self.refresh()
                else:
                    self.apply_result(value)
            elif event == "show":
                self.root.deiconify()
                self.root.lift()
            elif event == "settings":
                self.root.deiconify()
                self.open_settings()
            elif event == "refresh":
                self.refresh()
            elif event == "pin":
                self.toggle_topmost()
            elif event == "exit":
                self.quit()
                return
            elif event == "tray_ready":
                self.tray_ready = True
            elif event == "tray_failed":
                self.tray_ready = False
                self.root.deiconify()
        self._schedule(100, self._drain)

    def apply_result(self, result: PollResult):
        self.usage_error, self.credits_error = result.usage.error, result.credits.error
        if isinstance(result.usage.value, Usage):
            self.usage, self.usage_at = result.usage.value, result.at
        if isinstance(result.credits.value, Credits):
            self.credits, self.credits_at = result.credits.value, result.at
        self.plan_label.configure(text=self.usage.plan.upper() if self.usage and self.usage.plan else "")
        self._render_windows()
        self._render_credits()
        message = self.usage_error or ("重置点暂不可用，点击展开查看" if self.credits_error else "")
        if self.usage_error and self.usage:
            message = "数据已过期 · " + message
        self.status_label.configure(text=message, fg=WARNING)
        if message:
            self.status_label.pack(fill="x", pady=(0, 4), before=self.separator)
        else:
            self.status_label.pack_forget()
        self._update_times()
        if self.details_visible:
            self._place_details()

    def _render_windows(self):
        for child in self.windows_frame.winfo_children():
            child.destroy()
        self.window_rows.clear()
        if not self.usage:
            self._label(self.windows_frame, "用量未提供", MUTED).pack(anchor="w", pady=4)
            return
        for window in self.usage.windows:
            card = tk.Frame(self.windows_frame, bg=BG)
            card.pack(fill="x", pady=(0, self.px(8)))
            line = tk.Frame(card, bg=BG)
            line.pack(fill="x")
            label = window.label
            if not window.additional:
                label = {18000: "5h", 604800: "Weekly"}.get(window.seconds, label)
            self._label(line, label, bold=True, wraplength=self.px(98), justify="left").pack(side="left")
            value = f"{window.remaining:g}%" if window.remaining is not None else "未提供"
            self._label(line, value, self.accent, bold=True).pack(side="left", padx=(self.px(6), 0))
            timer = self._label(line, "", MUTED, 7, anchor="e")
            timer.pack(side="right")
            bar = tk.Canvas(card, height=self.px(3), bg=TRACK, highlightthickness=0)
            bar.pack(fill="x", pady=(self.px(4), 0))
            bar.bind("<Configure>", lambda event, c=bar, w=window: self._draw_bar(c, w.remaining))
            self.window_rows.append((window, timer))

    def _draw_bar(self, canvas, remaining):
        canvas.delete("all")
        if remaining is not None:
            canvas.create_rectangle(0, 0, canvas.winfo_width() * remaining / 100,
                                    self.px(4), fill=self.accent, outline="")

    def _render_credits(self):
        for child in self.credit_content.winfo_children():
            child.destroy()
        if self.credits_error:
            self._label(self.credit_content, ("数据已过期 · " if self.credits else "") + self.credits_error,
                        WARNING, wraplength=self.panel_width - self.px(28), justify="left").pack(anchor="w", pady=2)
        if self.credits:
            count = self.credits.available if self.credits.available is not None else "未提供"
            self._label(self.credit_content, f"可用重置点  {count}", bold=True).pack(anchor="w", pady=(2, 3))
            names = {"available": "可用", "consumed": "已使用", "used": "已使用", "expired": "已过期"}
            for item in self.credits.items:
                self._label(self.credit_content, f"{item.title} · {names.get(item.status, item.status)}",
                            wraplength=self.panel_width - self.px(28), justify="left").pack(anchor="w", pady=(3, 0))
                self._label(self.credit_content, "过期 " + local_time(item.expires_at), MUTED, 7).pack(anchor="w")
            if not self.credits.items:
                self._label(self.credit_content, "暂无重置点明细", MUTED).pack(anchor="w")
        elif not self.credits_error:
            self._label(self.credit_content, "尚未获取重置点", MUTED).pack(anchor="w")

    def _update_times(self):
        self._render_summary()
        for window, label in self.window_rows:
            label.configure(text="重置 " + local_time(window.reset_at))
        if self.usage_at:
            suffix = " · 已过期" if self.usage_error else " · 60s 更新"
            self.updated_label.configure(text=self.usage_at.astimezone().strftime("%H:%M:%S") + suffix)

    def _tick(self):
        self._update_times()
        if self._auto_poll and time.monotonic() >= self._next_poll and not self.busy:
            self.refresh()
        self._schedule(1000, self._tick)

    def _tray_worker(self):
        try:
            import pystray
            def send(name):
                return lambda icon, item: self.events.put((name, None))
            menu = pystray.Menu(pystray.MenuItem("显示浮窗", send("show"), default=True),
                                pystray.MenuItem("设置", send("settings")),
                                pystray.MenuItem("刷新", send("refresh")),
                                pystray.MenuItem("切换置顶", send("pin")),
                                pystray.Menu.SEPARATOR, pystray.MenuItem("退出", send("exit")))
            icon = pystray.Icon("codex-usage", tray_image(self.accent), "Codex 余量", menu)
            self.tray = icon
            def setup(icon):
                if self.stop.is_set():
                    icon.stop()
                else:
                    icon.visible = True
                    self.events.put(("tray_ready", None))
            icon.run(setup)
            if not self.stop.is_set():
                self.events.put(("tray_failed", None))
        except Exception:
            self.events.put(("tray_failed", None))

    def hide(self):
        self._hide_details()
        self.save_position()
        if self.tray_ready:
            if self.preferences:
                self.preferences.close()
            self.root.withdraw()
        else:
            self.usage_error = "托盘尚未就绪；右键胶囊可退出"
            self.status_label.configure(text=self.usage_error, fg=WARNING)
            self.status_label.pack(fill="x", pady=(0, 8), before=self.separator)
            self._show_details()

    def quit(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        self.save_position()
        for timer in self._timers:
            self.root.after_cancel(timer)
        self._timers.clear()
        if self.tray:
            threading.Thread(target=self.tray.stop, daemon=True).start()
        if not self.busy:
            self.client.close()
        self.root.destroy()


def run():
    enable_dpi_awareness()
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()
