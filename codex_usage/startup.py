"""Current-user logon startup. Only our named Run value is ever changed."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    import winreg
else:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CodexUsage"


class StartupError(OSError):
    """Safe startup-registration error for the preferences page."""


def startup_command(executable=None, frozen=None, entry=None):
    executable = Path(executable or sys.executable).resolve()
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        arguments = [str(executable)]
    else:
        windowed = executable.with_name("pythonw.exe")
        if windowed.is_file():
            executable = windowed
        arguments = [str(executable), str(Path(entry or Path(__file__).resolve().parents[1] / "main.py").resolve())]
    command = subprocess.list2cmdline(arguments)
    if len(command) > 260:
        raise StartupError("程序路径过长，无法注册自启动；请移到较短的路径后重试。")
    return command


class StartupManager:
    def __init__(self, registry=None, command=None):
        self.registry = winreg if registry is None else registry
        self._command = command

    @property
    def command(self):
        return self._command if self._command is not None else startup_command()

    def read(self):
        r = self.registry
        if r is None:
            raise StartupError("此平台不支持 Windows 开机自启动。")
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, RUN_KEY, 0, r.KEY_QUERY_VALUE) as key:
                value, _ = r.QueryValueEx(key, VALUE_NAME)
                return value if isinstance(value, str) else None
        except FileNotFoundError:
            return None
        except OSError:
            raise StartupError("无法读取开机启动项，请检查当前用户权限。") from None

    def write(self, command):
        r = self.registry
        if r is None:
            raise StartupError("此平台不支持 Windows 开机自启动。")
        if command is not None and (not isinstance(command, str) or len(command) > 260):
            raise StartupError("自启动命令无效或程序路径过长。")
        try:
            if command is None:
                try:
                    with r.OpenKey(r.HKEY_CURRENT_USER, RUN_KEY, 0, r.KEY_SET_VALUE) as key:
                        r.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
            else:
                with r.CreateKeyEx(r.HKEY_CURRENT_USER, RUN_KEY, 0, r.KEY_SET_VALUE) as key:
                    r.SetValueEx(key, VALUE_NAME, 0, r.REG_SZ, command)
        except OSError:
            raise StartupError("无法更新开机启动项，请检查当前用户权限。") from None


class MemoryStartup:
    """Side-effect-free implementation for previews and UI tests."""
    command = '"C:\\Demo\\CodexUsage.exe"'

    def __init__(self):
        self.value = None

    def read(self):
        return self.value

    def write(self, command):
        self.value = command
