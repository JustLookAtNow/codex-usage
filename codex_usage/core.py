"""Credential loading, read-only HTTP access, and tolerant response parsing.

Never log credentials, server response bodies, or exception strings from requests.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://chatgpt.com/backend-api/wham/"
MAX_BYTES = 1024 * 1024


class MonitorError(Exception):
    """Contains only a safe, user-facing message."""


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)


def auth_path() -> Path:
    # Deliberately use the Windows user profile, not CODEX_HOME or WSL.
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".codex" / "auth.json"


def load_credentials(path: Path | None = None) -> Credentials:
    try:
        with (path or auth_path()).open("rb") as handle:
            raw = handle.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise MonitorError("凭据文件过大")
        document = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError:
        raise MonitorError("未找到 Windows Codex 的 auth.json") from None
    except (OSError, UnicodeError, ValueError):
        raise MonitorError("凭据文件暂不可读或格式不正确") from None
    tokens = document.get("tokens") if isinstance(document, dict) else None
    if not isinstance(tokens, dict):
        raise MonitorError("未找到 ChatGPT 登录凭据")
    access, account = tokens.get("access_token"), tokens.get("account_id")
    if not all(isinstance(v, str) and v.strip() and "\r" not in v and "\n" not in v
               for v in (access, account)):
        raise MonitorError("凭据缺少 access_token 或 account_id")
    return Credentials(access, account)


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def timestamp(value: Any) -> datetime | None:
    try:
        if number(value) is not None:
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        pass
    return None


@dataclass(frozen=True)
class UsageWindow:
    label: str
    remaining: float | None
    reset_at: datetime | None
    seconds: int | None
    additional: bool = False


@dataclass(frozen=True)
class Usage:
    plan: str | None
    windows: tuple[UsageWindow, ...]


@dataclass(frozen=True)
class ResetCredit:
    title: str
    status: str
    expires_at: datetime | None


@dataclass(frozen=True)
class Credits:
    available: int | None
    items: tuple[ResetCredit, ...]


def duration_label(seconds: int | None, fallback: str) -> str:
    if not seconds:
        return fallback
    for unit, suffix in ((604800, "周"), (86400, "天"), (3600, "小时"), (60, "分钟")):
        if seconds % unit == 0:
            return f"{seconds // unit} {suffix}"
    return f"{seconds} 秒"


def parse_usage(data: Any) -> Usage:
    if not isinstance(data, dict):
        raise MonitorError("用量响应格式不正确")
    windows: list[UsageWindow] = []

    def append_limits(limits: Any, prefix: str = "") -> None:
        if not isinstance(limits, dict):
            return
        for key, fallback in (("primary_window", "主周期"), ("secondary_window", "次周期")):
            window = limits.get(key)
            if not isinstance(window, dict):
                continue
            raw_seconds = number(window.get("limit_window_seconds"))
            seconds = int(raw_seconds) if raw_seconds and raw_seconds > 0 else None
            used = number(window.get("used_percent"))
            remaining = min(100.0, max(0.0, 100.0 - used)) if used is not None else None
            windows.append(UsageWindow(prefix + duration_label(seconds, fallback), remaining,
                                       timestamp(window.get("reset_at")), seconds, bool(prefix)))

    append_limits(data.get("rate_limit"))
    # Additional model limits are independent; never merge them into ordinary quota.
    additional = data.get("additional_rate_limits")
    if isinstance(additional, list):
        for item in additional:
            if isinstance(item, dict):
                name = item.get("limit_name") or item.get("normal_model_slug") or "附加额度"
                append_limits(item.get("rate_limit"), str(name)[:48] + " · ")
    if not windows:
        raise MonitorError("接口未提供可识别的用量周期")
    plan = data.get("plan_type")
    return Usage(plan if isinstance(plan, str) else None, tuple(windows))


def parse_credits(data: Any) -> Credits:
    if not isinstance(data, dict):
        raise MonitorError("重置点响应格式不正确")
    count = number(data.get("available_count"))
    available = int(count) if count is not None and count >= 0 and count.is_integer() else None
    raw_items = data.get("credits")
    if raw_items is not None and not isinstance(raw_items, list):
        raise MonitorError("重置点明细格式不正确")
    if available is None and raw_items is None:
        raise MonitorError("接口未提供重置点信息")
    items = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("reset_type") or "重置点"
        status = item.get("status")
        items.append(ResetCredit(str(title)[:80], str(status)[:40] if status else "未提供",
                                 timestamp(item.get("expires_at"))))
    items.sort(key=lambda item: item.expires_at or datetime.max.replace(tzinfo=timezone.utc))
    return Credits(available, tuple(items))


def retry_delay(value: str | None, now: datetime | None = None) -> float:
    if value:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return max(1.0, seconds)
        except ValueError:
            pass
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(1.0, (target - (now or datetime.now(timezone.utc))).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass
    return 60.0


@dataclass(frozen=True)
class Outcome:
    value: Usage | Credits | None = None
    error: str | None = None


@dataclass(frozen=True)
class PollResult:
    usage: Outcome
    credits: Outcome
    at: datetime


class QuotaClient:
    def __init__(self, path: Path | None = None, session: Any = None):
        self.path = path
        self.session = session or requests.Session()
        self._not_before: dict[str, float] = {}

    def configure_network(self, mode: str, address: str = "") -> None:
        """Call only between polls; explicit modes never fall back to system proxies."""
        from .settings import validate_proxy
        address = validate_proxy(mode, address)
        self.session.trust_env = mode == "system"
        self.session.proxies = {"http": address, "https": address} if mode == "manual" else {}

    def close(self) -> None:
        self.session.close()

    def fetch(self, endpoint: str, credentials: Credentials, parser: Any) -> Outcome:
        remaining = self._not_before.get(endpoint, 0) - time.monotonic()
        if remaining > 0:
            return Outcome(error=f"请求受限，约 {math.ceil(remaining)} 秒后重试")
        headers = {"Authorization": f"Bearer {credentials.access_token}",
                   "ChatGPT-Account-Id": credentials.account_id,
                   "Accept": "application/json", "User-Agent": "codex-usage-monitor/0.1",
                   "originator": "codex_cli_rs"}
        try:
            with self.session.get(BASE_URL + endpoint, headers=headers, timeout=(4, 10),
                                  allow_redirects=False, stream=True) as response:
                status = response.status_code
                if status == 401:
                    return Outcome(error="登录凭据已失效，等待 auth.json 更新")
                if status == 403:
                    return Outcome(error="访问受限（403），请检查网络或账号权限")
                if status == 429:
                    delay = retry_delay(response.headers.get("Retry-After"))
                    self._not_before[endpoint] = time.monotonic() + delay
                    return Outcome(error=f"请求受限，约 {math.ceil(delay)} 秒后重试")
                if 300 <= status < 400:
                    return Outcome(error="服务地址发生跳转，已停止请求")
                if status != 200:
                    return Outcome(error=f"服务暂不可用（HTTP {status}）")
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        return Outcome(error="接口响应过大")
                return Outcome(value=parser(json.loads(body)))
        except requests.Timeout:
            return Outcome(error="网络连接超时，将自动重试")
        except requests.RequestException:
            return Outcome(error="网络连接失败，请检查网络或代理")
        except (ValueError, UnicodeError):
            return Outcome(error="接口返回了无法解析的数据")
        except MonitorError as error:
            return Outcome(error=str(error))

    def poll(self) -> PollResult:
        try:
            credentials = load_credentials(self.path)
        except MonitorError as error:
            outcome = Outcome(error=str(error))
            return PollResult(outcome, outcome, datetime.now(timezone.utc))
        usage = self.fetch("usage", credentials, parse_usage)
        credits = self.fetch("rate-limit-reset-credits", credentials, parse_credits)
        return PollResult(usage, credits, datetime.now(timezone.utc))
