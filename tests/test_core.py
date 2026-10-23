import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from codex_usage.core import (Credentials, MonitorError, QuotaClient, auth_path,
                              load_credentials, parse_credits, parse_usage, retry_delay)


def usage_payload():
    return {"plan_type": "plus", "rate_limit": {
        "primary_window": {"used_percent": 0.4, "limit_window_seconds": 18000,
                           "reset_at": 1800000000},
        "secondary_window": {"used_percent": 62, "limit_window_seconds": 604800,
                             "reset_at": 1800500000}}}


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass


class ParsingTests(unittest.TestCase):
    def test_percent_fields_are_not_ratios(self):
        usage = parse_usage(usage_payload())
        self.assertAlmostEqual(usage.windows[0].remaining, 99.6)
        self.assertEqual(usage.windows[1].remaining, 38)
        self.assertEqual([w.label for w in usage.windows], ["5 小时", "1 周"])

    def test_primary_window_may_be_weekly(self):
        usage = parse_usage({"rate_limit": {"primary_window": {
            "used_percent": 100, "limit_window_seconds": 604800}}})
        self.assertEqual(usage.windows[0].label, "1 周")
        self.assertEqual(usage.windows[0].remaining, 0)
        self.assertIsNone(usage.windows[0].reset_at)

    def test_missing_and_invalid_values_are_unknown(self):
        for value in (None, True, "20", float("nan"), float("inf")):
            window = parse_usage({"rate_limit": {"primary_window": {"used_percent": value}}}).windows[0]
            self.assertIsNone(window.remaining)
            self.assertEqual(window.label, "主周期")

    def test_clamps_percent_and_keeps_additional_limits_separate(self):
        data = usage_payload()
        data["rate_limit"]["primary_window"]["used_percent"] = 110
        data["additional_rate_limits"] = [{"limit_name": "Example", "rate_limit": {
            "primary_window": {"used_percent": -10, "limit_window_seconds": 3600}}}]
        usage = parse_usage(data)
        self.assertEqual(usage.windows[0].remaining, 0)
        self.assertEqual(usage.windows[2].remaining, 100)
        self.assertEqual(usage.windows[2].label, "Example · 1 小时")

    def test_bad_usage_does_not_become_zero(self):
        for data in (None, [], {}, {"rate_limit": None}):
            with self.assertRaises(MonitorError):
                parse_usage(data)

    def test_zero_missing_counts_and_multiple_expirations(self):
        self.assertEqual(parse_credits({"available_count": 0, "credits": []}).available, 0)
        self.assertIsNone(parse_credits({"credits": []}).available)
        credits = parse_credits({"available_count": 2, "credits": [
            {"title": "later", "status": "available", "expires_at": "2027-02-02T00:00:00Z"},
            {"title": "unknown", "status": "available", "expires_at": None},
            {"title": "sooner", "status": "expired", "expires_at": "2027-01-01T00:00:00+00:00"}]})
        self.assertEqual([c.title for c in credits.items], ["sooner", "later", "unknown"])
        with self.assertRaises(MonitorError):
            parse_credits({"credits": "wrong"})

    def test_retry_after_seconds_dates_and_invalid(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(retry_delay("120"), 120)
        self.assertEqual(retry_delay("Thu, 01 Jan 2026 00:03:00 GMT", now), 180)
        self.assertEqual(retry_delay("invalid"), 60)
        self.assertEqual(retry_delay("nan"), 60)


class CredentialAndHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "auth.json"
        self.write_auth("test-token-1")

    def write_auth(self, token):
        self.path.write_text(json.dumps({"tokens": {"access_token": token, "account_id": "test-account"}}))

    def test_credential_read_is_non_mutating_and_repr_safe(self):
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        credentials = load_credentials(self.path)
        self.assertNotIn("test-token", repr(credentials))
        self.assertNotIn("test-account", repr(credentials))
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_missing_corrupt_and_api_key_only_files(self):
        self.path.unlink()
        with self.assertRaisesRegex(MonitorError, "未找到"):
            load_credentials(self.path)
        for content in ('{', '{"OPENAI_API_KEY":"dummy"}', '{"tokens":{}}'):
            self.path.write_text(content)
            with self.assertRaises(MonitorError):
                load_credentials(self.path)

    def test_path_uses_windows_profile_not_codex_home(self):
        with patch.dict("os.environ", {"USERPROFILE": str(self.path.parent), "CODEX_HOME": "unused"}):
            self.assertEqual(auth_path(), self.path.parent / ".codex" / "auth.json")

    def test_each_poll_rereads_updated_credentials(self):
        session = Session(Response(body=usage_payload()), Response(body={"available_count": 0}),
                          Response(body=usage_payload()), Response(body={"available_count": 2}))
        client = QuotaClient(self.path, session)
        client.poll()
        self.write_auth("test-token-2")
        result = client.poll()
        self.assertEqual(session.calls[2][1]["headers"]["Authorization"], "Bearer test-token-2")
        self.assertEqual(result.credits.value.available, 2)
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(session.calls[0][0].startswith("https://chatgpt.com/"))

    def test_auth_failure_recovers_and_credits_failure_is_independent(self):
        session = Session(Response(401), Response(401), Response(body=usage_payload()), Response(403))
        client = QuotaClient(self.path, session)
        self.assertIn("凭据已失效", client.poll().usage.error)
        result = client.poll()
        self.assertIsNotNone(result.usage.value)
        self.assertIn("访问受限", result.credits.error)

    def test_retry_after_is_respected_even_on_manual_refresh(self):
        session = Session(Response(429, headers={"Retry-After": "120"}), Response(body=usage_payload()))
        client = QuotaClient(self.path, session)
        credentials = load_credentials(self.path)
        with patch("codex_usage.core.time.monotonic", return_value=100):
            self.assertIn("120", client.fetch("usage", credentials, parse_usage).error)
        with patch("codex_usage.core.time.monotonic", return_value=160):
            self.assertIn("60", client.fetch("usage", credentials, parse_usage).error)
            self.assertEqual(len(session.calls), 1)
        with patch("codex_usage.core.time.monotonic", return_value=221):
            self.assertIsNotNone(client.fetch("usage", credentials, parse_usage).value)

    def test_timeout_network_html_and_server_failures_are_safe(self):
        cases = [(requests.Timeout("sensitive"), "超时"),
                 (requests.ConnectionError("sensitive"), "连接失败"),
                 (Response(200, b"<html>sensitive</html>"), "无法解析"),
                 (Response(503), "503"), (Response(302), "跳转"),
                 (Response(200, b"x" * (1024 * 1024 + 1)), "过大")]
        for response, expected in cases:
            result = QuotaClient(self.path, Session(response)).fetch("usage", load_credentials(self.path), parse_usage)
            self.assertIn(expected, result.error)
            self.assertNotIn("sensitive", result.error)


if __name__ == "__main__":
    unittest.main()
