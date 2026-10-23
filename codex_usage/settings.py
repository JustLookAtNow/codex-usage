"""Validated preferences shared by the GUI and one-shot query."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_ACCENT = "#278054"
PROXY_MODES = ("system", "direct", "manual")


def settings_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexUsage" / "settings.json"


def validate_accent(value: str) -> str:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        raise ValueError("主题色请填写 #RRGGBB，例如 #278054")
    return value.strip().lower()


def validate_proxy(mode: str, value: str) -> str:
    if mode not in PROXY_MODES:
        raise ValueError("请选择有效的代理模式")
    if mode != "manual":
        return ""
    value = value.strip()
    try:
        url = urlsplit(value)
        if (url.scheme.lower() not in ("http", "https", "socks5", "socks5h") or
                not url.hostname or url.port is None or not 1 <= url.port <= 65535 or
                url.username is not None or url.password is not None or
                url.path not in ("", "/") or url.query or url.fragment or
                any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError
    except ValueError:
        raise ValueError("请填写代理地址和端口，如 http://127.0.0.1:7890；不支持用户名密码") from None
    # SOCKS5 always resolves target host names through the proxy.
    return ("socks5h" if url.scheme.lower().startswith("socks5") else url.scheme.lower()) + "://" + url.netloc.rstrip("/")


@dataclass
class Settings:
    x: int | None = None
    y: int | None = None
    accent: str = DEFAULT_ACCENT
    proxy_mode: str = "system"
    proxy_url: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        result = cls()
        try:
            data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return result
            if all(type(data.get(k)) is int for k in ("x", "y")):
                result.x, result.y = data["x"], data["y"]
            try:
                result.accent = validate_accent(data.get("accent", DEFAULT_ACCENT))
            except (ValueError, AttributeError, TypeError):
                pass
            try:
                mode = data.get("proxy_mode", "system")
                address = validate_proxy(mode, data.get("proxy_url", ""))
                result.proxy_mode, result.proxy_url = mode, address
            except (ValueError, AttributeError, TypeError):
                pass
        except (OSError, ValueError, UnicodeError):
            pass
        return result

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def readable_accent(color: str) -> str:
    """Keep custom colors readable against white (WCAG 4.5:1 text contrast)."""
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    for _ in range(40):
        linear = [v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in rgb]
        luminance = sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))
        if 1.05 / (luminance + 0.05) >= 4.5:
            break
        rgb = [int(v * 0.92) for v in rgb]
    return "#" + "".join(f"{v:02x}" for v in rgb)
