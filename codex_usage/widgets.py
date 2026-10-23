"""Small flat controls, drawn at native DPI. No third-party UI runtime."""
import tkinter as tk


def rounded(canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def tint(color, fraction=0.9):
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(v + (255 - v) * fraction):02x}" for v in rgb)


class FlatButton(tk.Canvas):
    def __init__(self, parent, text, command, px, width=76, height=30, accent="#278054", primary=False):
        super().__init__(parent, width=px(width), height=px(height), bg=parent.cget("bg"),
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.text, self.command, self.px = text, command, px
        self.accent, self.primary = accent, primary
        self.selected = self.hovered = False
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<Enter>", lambda event: self._hover(True))
        self.bind("<Leave>", lambda event: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<space>", lambda event: self.invoke())
        self.bind("<Return>", lambda event: self.invoke())
        self.bind("<FocusIn>", lambda event: self.draw())
        self.bind("<FocusOut>", lambda event: self.draw())

    def _hover(self, value):
        self.hovered = value
        self.draw()

    def _click(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.focus_set()
            self.invoke()

    def invoke(self):
        return self.command()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        fill = self.accent if self.primary else tint(self.accent) if self.selected else "#f0f3f4" if self.hovered else "#f6f7f8"
        fg = "#ffffff" if self.primary else self.accent if self.selected else "#26313d"
        outline = self.accent if self.selected or self.focus_get() == self else fill
        rounded(self, 1, 1, w - 1, h - 1, self.px(7), fill=fill, outline=outline)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg,
                         font=("Microsoft YaHei UI", -self.px(12)))


class SegmentedControl(tk.Canvas):
    def __init__(self, parent, variable, options, px, width=380, height=34, accent="#278054"):
        super().__init__(parent, width=px(width), height=px(height), bg=parent.cget("bg"),
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.variable, self.options, self.px, self.accent = variable, options, px, accent
        self._trace = variable.trace_add("write", lambda *args: self.draw())
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Left>", lambda event: self._step(-1))
        self.bind("<Right>", lambda event: self._step(1))
        self.bind("<FocusIn>", lambda event: self.draw())
        self.bind("<FocusOut>", lambda event: self.draw())
        self.bind("<Destroy>", self._destroyed)

    def _destroyed(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._trace)

    def select(self, value):
        if value not in [key for _, key in self.options]:
            raise ValueError("Unknown segment")
        self.variable.set(value)

    def _click(self, event):
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.focus_set()
            index = min(len(self.options) - 1, int(event.x / self.winfo_width() * len(self.options)))
            self.select(self.options[index][1])

    def _step(self, delta):
        values = [key for _, key in self.options]
        index = values.index(self.variable.get()) if self.variable.get() in values else 0
        self.select(values[(index + delta) % len(values)])
        return "break"

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        rounded(self, 1, 1, w - 1, h - 1, self.px(8), fill="#f0f3f4", outline="#f0f3f4")
        cell = w / len(self.options)
        for index, (label, key) in enumerate(self.options):
            selected = self.variable.get() == key
            if selected:
                outline = self.accent if self.focus_get() == self else "#dce3e4"
                rounded(self, index * cell + self.px(3), self.px(3), (index + 1) * cell - self.px(3),
                        h - self.px(3), self.px(6), fill="#ffffff", outline=outline)
            self.create_text((index + 0.5) * cell, h / 2, text=label,
                             fill=self.accent if selected else "#687581",
                             font=("Microsoft YaHei UI", -self.px(12), "bold" if selected else "normal"))


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent, variable, px, accent="#278054", enabled=True):
        super().__init__(parent, width=px(40), height=px(24), bg=parent.cget("bg"),
                         highlightthickness=0, takefocus=enabled, cursor="hand2" if enabled else "arrow")
        self.variable, self.px, self.accent, self.enabled = variable, px, accent, enabled
        self._trace = variable.trace_add("write", lambda *args: self.draw())
        self.bind("<Configure>", lambda event: self.draw())
        self.bind("<ButtonRelease-1>", lambda event: self.invoke())
        self.bind("<space>", lambda event: self.invoke())
        self.bind("<Return>", lambda event: self.invoke())
        self.bind("<FocusIn>", lambda event: self.draw())
        self.bind("<FocusOut>", lambda event: self.draw())
        self.bind("<Destroy>", self._destroyed)

    def _destroyed(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._trace)

    def invoke(self):
        if self.enabled:
            self.focus_set()
            self.variable.set(not self.variable.get())

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        fill = self.accent if self.variable.get() and self.enabled else "#dce2e5"
        rounded(self, 1, 1, w - 1, h - 1, h / 2, fill=fill,
                outline=self.accent if self.focus_get() == self else fill)
        diameter = h - self.px(6)
        x = w - diameter - self.px(3) if self.variable.get() else self.px(3)
        self.create_oval(x, self.px(3), x + diameter, self.px(3) + diameter, fill="#ffffff", outline="#ffffff")
