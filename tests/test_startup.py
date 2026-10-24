import tempfile
import unittest
from pathlib import Path

from codex_usage.startup import RUN_KEY, VALUE_NAME, StartupManager, StartupError, startup_command


class Registry:
    """Exercise the winreg contract without changing any Windows registry keys."""
    HKEY_CURRENT_USER = object()
    KEY_QUERY_VALUE, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.entries = {"OtherApp": "unrelated.exe"}
        self.denied = False

    def OpenKey(self, root, path, reserved, access):
        assert root is self.HKEY_CURRENT_USER and path == RUN_KEY
        if self.denied:
            raise PermissionError
        return self

    CreateKeyEx = OpenKey

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def QueryValueEx(self, key, name):
        if name not in self.entries:
            raise FileNotFoundError
        return self.entries[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, command):
        assert name == VALUE_NAME and kind == self.REG_SZ
        self.entries[name] = command

    def DeleteValue(self, key, name):
        if name not in self.entries:
            raise FileNotFoundError
        del self.entries[name]


class StartupTests(unittest.TestCase):
    def test_enable_update_and_disable_touch_only_our_entry(self):
        registry = Registry()
        manager = StartupManager(registry, command='"C:\\Apps\\CodexUsage.exe"')
        self.assertIsNone(manager.read())
        manager.write(manager.command)
        self.assertEqual(manager.read(), manager.command)
        newer = '"C:\\Apps\\CodexUsage-0.3.0.exe"'
        manager.write(newer)
        self.assertEqual(manager.read(), newer)
        self.assertEqual(len(registry.entries), 2)
        manager.write(None)
        manager.write(None)
        self.assertEqual(registry.entries, {"OtherApp": "unrelated.exe"})

    def test_frozen_command_quotes_path_with_spaces(self):
        command = startup_command(Path.cwd() / "Program Files" / "CodexUsage.exe", frozen=True)
        self.assertEqual(command, '"' + str(Path.cwd() / "Program Files" / "CodexUsage.exe") + '"')
        self.assertNotIn("_MEI", command)

    def test_source_command_uses_pythonw_and_absolute_main_path(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            (folder / "pythonw.exe").touch()
            entry = folder / "project files" / "main.py"
            command = startup_command(folder / "python.exe", frozen=False, entry=entry)
            self.assertIn(str(folder / "pythonw.exe"), command)
            self.assertIn('"' + str(entry) + '"', command)

    def test_permission_errors_are_safe_and_do_not_look_successful(self):
        registry = Registry()
        registry.denied = True
        manager = StartupManager(registry)
        with self.assertRaisesRegex(StartupError, "无法读取"):
            manager.read()
        with self.assertRaisesRegex(StartupError, "无法更新"):
            manager.write("program.exe")
        self.assertEqual(registry.entries, {"OtherApp": "unrelated.exe"})

    def test_too_long_commands_are_rejected_without_writing(self):
        registry = Registry()
        with self.assertRaisesRegex(StartupError, "路径过长"):
            StartupManager(registry).write("x" * 261)
        self.assertNotIn(VALUE_NAME, registry.entries)


if __name__ == "__main__":
    unittest.main()
