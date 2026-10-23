import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from codex_usage.core import QuotaClient
from codex_usage.settings import Settings, readable_accent, validate_accent, validate_proxy


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def test_migrates_old_position_and_round_trips_preferences(self):
        self.path.write_text('{"x":100,"y":200}')
        settings = Settings.load(self.path)
        self.assertEqual((settings.x, settings.y, settings.proxy_mode), (100, 200, "system"))
        settings.accent = "#3377cc"
        settings.proxy_mode, settings.proxy_url = "manual", "http://127.0.0.1:7890"
        settings.save(self.path)
        self.assertEqual(Settings.load(self.path), settings)
        self.assertNotIn("token", self.path.read_text())

    def test_corrupt_fields_fall_back_individually(self):
        for data in (None, [], "invalid"):
            self.path.write_text(json.dumps(data))
            self.assertEqual(Settings.load(self.path), Settings())
        self.path.write_text('{"x":40,"y":50,"accent":null,"proxy_mode":"manual","proxy_url":"bad"}')
        settings = Settings.load(self.path)
        self.assertEqual((settings.x, settings.y), (40, 50))
        self.assertEqual(settings.accent, "#278054")
        self.assertEqual(settings.proxy_mode, "system")

    def test_manual_proxy_validation_and_remote_dns(self):
        self.assertEqual(validate_proxy("manual", "socks5://127.0.0.1:1080/"), "socks5h://127.0.0.1:1080")
        self.assertEqual(validate_proxy("manual", "http://[::1]:7890"), "http://[::1]:7890")
        for value in ("", "127.0.0.1:7890", "ftp://host:80", "http://host:0", "http://host:99999",
                      "http://host", "http://host:80/path", "http://name:secret@host:80", "http://host:80#frag",
                      "http://host:80?query", "http://host:80\nInjected: x"):
            with self.assertRaises(ValueError, msg=value):
                validate_proxy("manual", value)

    def test_colors_are_validated_and_light_colors_become_readable(self):
        self.assertEqual(validate_accent("#3377CC"), "#3377cc")
        for value in ("red", "#FFF", "#zzzzzz"):
            with self.assertRaises(ValueError):
                validate_accent(value)
        self.assertNotEqual(readable_accent("#ffffff"), "#ffffff")
        self.assertEqual(readable_accent("#278054"), "#278054")

    def test_direct_and_manual_ignore_environment_system_mode_uses_it(self):
        client = QuotaClient()
        self.addCleanup(client.close)
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:9999", "NO_PROXY": "*"}, clear=True):
            client.configure_network("manual", "http://127.0.0.1:7890")
            merged = client.session.merge_environment_settings("https://chatgpt.com/", {}, None, None, None)
            self.assertEqual(merged["proxies"], {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
            client.configure_network("direct")
            self.assertEqual(client.session.merge_environment_settings("https://chatgpt.com/", {}, None, None, None)["proxies"], {})
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:9999"}, clear=True):
            client.configure_network("system")
            self.assertEqual(client.session.merge_environment_settings("https://chatgpt.com/", {}, None, None, None)["proxies"]["https"], "http://127.0.0.1:9999")


if __name__ == "__main__":
    unittest.main()
