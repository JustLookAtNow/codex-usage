"""Flat, non-modal preferences with a segmented selector and logon switch."""
import tkinter as tk
from tkinter import colorchooser

from .settings import validate_accent, readable_accent
from .startup import StartupError
from .widgets import FlatButton, SegmentedControl, ToggleSwitch

BG, TEXT, MUTED = "#ffffff", "#26313d", "#788391"


class PreferencesDialog:
    def __init__(self, app):
        self.app = app
        window = self.window = tk.Toplevel(app.root)
        window.title("Codex 余量 · 设置")
        window.configure(bg=BG)
        window.resizable(False, False)
        window.attributes("-topmost", app.topmost)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())
        self.color = tk.StringVar(window, app.settings.accent)
        self.mode = tk.StringVar(window, app.settings.proxy_mode)
        self.address = tk.StringVar(window, app.settings.proxy_url)
        self.message = tk.StringVar(window)
        self.autostart = tk.BooleanVar(window, False)
        self.startup_available = True
        try:
            self.autostart.set(app.startup.read() is not None)
        except OSError:
            self.startup_available = False
            self.message.set("无法读取启动项，自启动开关暂不可用。其他设置可以正常保存。")
        content = tk.Frame(window, bg=BG, padx=app.px(20), pady=app.px(18))
        content.pack(fill="both", expand=True)

        def label(parent, text, size=12, bold=False, color=TEXT, **kwargs):
            return tk.Label(parent, text=text, bg=BG, fg=color, padx=0, pady=0,
                            font=("Microsoft YaHei UI", -app.px(size), "bold" if bold else "normal"), **kwargs)

        def entry(parent, variable, width=None):
            border = tk.Frame(parent, bg=BG, highlightbackground="#dde3e7", highlightcolor=app.accent, highlightthickness=1)
            field = tk.Entry(border, textvariable=variable, font=("Segoe UI", -app.px(12)),
                             bg=BG, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                             disabledbackground="#f6f7f8", disabledforeground=MUTED, width=width or 20)
            field.pack(fill="x", padx=app.px(9), pady=app.px(7))
            field.bind("<FocusIn>", lambda event: border.configure(highlightbackground=app.accent))
            field.bind("<FocusOut>", lambda event: border.configure(highlightbackground="#dde3e7"))
            return border, field

        label(content, "设置", 18, True).pack(anchor="w")
        label(content, "外观、网络与启动 · 保存后生效", 11, color=MUTED).pack(anchor="w", pady=(app.px(4), app.px(18)))
        label(content, "主题色", 12, True).pack(anchor="w")
        presets = tk.Frame(content, bg=BG)
        presets.pack(fill="x", pady=(app.px(8), app.px(9)))
        self.color_buttons = []
        for name, color in (("松绿", "#278054"), ("晴蓝", "#3377cc"), ("浅紫", "#8561c5"), ("暖金", "#aa7824")):
            button = FlatButton(presets, name, lambda c=color: self.color.set(c), app.px, width=76, height=28, accent=color)
            button.pack(side="left", padx=(0, app.px(7)))
            self.color_buttons.append((color, button))
        custom = tk.Frame(content, bg=BG)
        custom.pack(fill="x")
        self.swatch = tk.Frame(custom, bg=app.settings.accent, width=app.px(28), height=app.px(28))
        self.swatch.pack(side="left", padx=(0, app.px(8)))
        color_border, self.color_entry = entry(custom, self.color, 11)
        color_border.pack(side="left")
        FlatButton(custom, "自定义…", self.choose_color, app.px, width=78, height=30).pack(side="left", padx=app.px(7))
        label(content, "浅色背景保持不变，主题色用于强调文字与进度条。", 10, color=MUTED).pack(anchor="w", pady=(app.px(7), app.px(18)))
        label(content, "网络代理", 12, True).pack(anchor="w")
        self.proxy_modes = SegmentedControl(content, self.mode,
                                            (("跟随系统", "system"), ("直连", "direct"), ("手动代理", "manual")),
                                            app.px, width=380, height=34, accent=app.accent)
        self.proxy_modes.pack(fill="x", pady=(app.px(8), app.px(9)))
        proxy_border, self.proxy_entry = entry(content, self.address)
        proxy_border.pack(fill="x")
        label(content, "例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:10808", 10, color=MUTED).pack(anchor="w", pady=(app.px(7), app.px(3)))
        label(content, "支持 HTTP / HTTPS / SOCKS5；不修改系统代理。", 10, color=MUTED).pack(anchor="w")
        tk.Frame(content, bg="#edf0f2", height=1).pack(fill="x", pady=(app.px(18), app.px(14)))
        startup_row = tk.Frame(content, bg=BG)
        startup_row.pack(fill="x")
        startup_copy = tk.Frame(startup_row, bg=BG)
        startup_copy.pack(side="left")
        label(startup_copy, "开机自启动", 12, True).pack(anchor="w")
        label(startup_copy, "登录 Windows 后自动显示余量浮窗", 10, color=MUTED).pack(anchor="w", pady=(app.px(4), 0))
        self.startup_toggle = ToggleSwitch(startup_row, self.autostart, app.px, app.accent, enabled=self.startup_available)
        self.startup_toggle.pack(side="right")
        self.error_label = tk.Label(content, textvariable=self.message, bg=BG, fg="#ac3b3b", padx=0, pady=0,
                                    wraplength=app.px(380), justify="left", anchor="w", font=("Microsoft YaHei UI", -app.px(10)))
        self.error_label.pack(fill="x", pady=(app.px(12), 0))
        buttons = tk.Frame(content, bg=BG)
        buttons.pack(fill="x", pady=(app.px(12), 0))
        self.save_button = FlatButton(buttons, "保存", self.save, app.px, width=80, height=32, accent=app.accent, primary=True)
        self.save_button.pack(side="right")
        FlatButton(buttons, "取消", self.close, app.px, width=72, height=32).pack(side="right", padx=app.px(8))
        self._color_trace = self.color.trace_add("write", self._preview)
        self._mode_trace = self.mode.trace_add("write", self._mode_changed)
        self._mode_changed()
        self._preview()
        window.update_idletasks()
        w, h = app.px(420), window.winfo_reqheight()
        left, top, sw, sh = app._bounds()
        x = max(left, min(app._position[0], left + sw - w))
        y = max(top, min(app._position[1] + app.height + app.px(12), top + sh - h))
        window.geometry(f"{w}x{h}{x:+d}{y:+d}")
        window.lift()

    def _preview(self, *args):
        try:
            color = validate_accent(self.color.get())
        except ValueError:
            return
        self.swatch.configure(bg=color)
        for preset, button in self.color_buttons:
            button.selected = preset == color
            button.draw()
        accent = readable_accent(color)
        for control in (self.save_button, self.proxy_modes, self.startup_toggle):
            control.accent = accent
            control.draw()

    def choose_color(self):
        try:
            initial = validate_accent(self.color.get())
        except ValueError:
            initial = self.app.settings.accent
        chosen = colorchooser.askcolor(initialcolor=initial, parent=self.window, title="选择主题色")[1]
        if chosen:
            self.color.set(chosen)

    def _mode_changed(self, *args):
        self.proxy_entry.configure(state="normal" if self.mode.get() == "manual" else "disabled")

    def save(self):
        try:
            self.app.apply_preferences(self.color.get(), self.mode.get(), self.address.get(),
                                       self.autostart.get() if self.startup_available else None)
        except (ValueError, StartupError) as error:
            self.message.set(str(error))
            return
        except OSError:
            self.message.set("无法保存设置，请检查配置目录的写入权限。")
            return
        self.close()

    def close(self):
        self.color.trace_remove("write", self._color_trace)
        self.mode.trace_remove("write", self._mode_trace)
        self.app.preferences = None
        self.window.destroy()
