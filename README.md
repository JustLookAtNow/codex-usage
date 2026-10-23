# Codex 余量浮窗

Windows 上的 Python 桌面小工具。直接读取本机 Codex 凭据并向 ChatGPT 查询额度，不启动或调用 Codex App、CLI、app-server。

## 使用

当前版本为 `dist/CodexUsage-0.3.0.exe`：增加开机自启动开关，设置页采用扁平分段选择器、滑动开关和浅色圆角按钮。胶囊约 272×28（随 Windows DPI 缩放，必要时按字体宽度扩展），左右两端为完整半圆。标签、倒计时为黑色 Segoe UI Semibold，百分比保留主题色。运行新版前请从旧版托盘菜单退出，已有主题色、代理和位置配置会沿用。

双击 `dist/CodexUsage.exe`，无需安装 Python。默认是 A 版浅色圆角胶囊：只显示 **5h / Weekly 的剩余百分比和额度重置倒计时**。拖动胶囊移动窗口，默认置顶。

- 鼠标悬停约 160 毫秒后展开详情，显示额度进度、准确恢复时间、账号套餐和更新时间。可把鼠标移进面板操作；移开胶囊和面板后约 280 毫秒收起。
- **重置点每次启动默认折叠**，在详情中点击“重置点”展开数量、状态与各项过期时间。没有消耗重置点的功能。
- 每 60 秒自动查询；详情中点击“刷新”手动查询。服务器限流期间，手动刷新也会遵守 `Retry-After`。
- 详情右上角 `×` 隐藏至托盘。双击托盘图标恢复。胶囊右键菜单和托盘菜单提供设置、刷新、切换置顶和退出。
- 网络或接口错误时保留上次成功数据，并标记过期。重置点查询错误不会清除正常额度数据。
- 时间按 Windows 当前本地时区显示。未知字段显示“未提供”，不会误显示为零。

凭据固定读取 `%USERPROFILE%\.codex\auth.json` 的 `tokens.access_token` 和 `tokens.account_id`。不会读取 WSL，也不采用 `CODEX_HOME`。如果凭据文件缺失或已失效，需要在外部更新这份登录凭据；程序下一轮查询会重新读取并恢复。不自动续期，不复制或修改凭据。仅有 API Key 的登录不支持订阅额度查询。

窗口位置、主题色、代理模式和代理地址保存在 `%LOCALAPPDATA%\CodexUsage\settings.json`。凭据与额度不会写入此文件。默认不创建日志，也不会上传遥测数据。旧版只有坐标的设置会自动兼容。

## 主题和代理设置

在悬停面板、胶囊右键菜单或托盘中点击 **设置**；所有改动点击保存后生效，取消不会改变启动项：

- **主题色**：松绿、晴蓝、浅紫、暖金预设，也可输入 `#RRGGBB` 或使用颜色选择器。背景保持浅色；过浅的颜色会自动加深以保证文字清晰。保存后立即应用，无需重启。
- **跟随系统**（默认）：采用 Requests 可读取的系统及环境变量代理；系统代理未启用时通常直接连接。不提供 PAC 脚本解析。
- **直连**：忽略系统及环境变量代理。
- **手动代理**：例如 `http://127.0.0.1:7890`、`https://proxy.example:8443` 或 `socks5://127.0.0.1:1080`。SOCKS5 会规范化为 `socks5h://`，由代理解析目标域名。需要填写端口，不支持用户名密码、PAC 或订阅链接。

保存代理后自动重查。如果旧查询仍在进行，会等待其结束，丢弃旧结果，再用新代理查询；不会在同一次查询中混用代理。手动代理失败时不会自动绕过代理直连。所有模式都保留 HTTPS 证书验证，不修改 Windows 系统设置。

### 开机自启动

默认关闭。在设置页打开滑动开关并保存，当前用户下次登录 Windows 时会自动运行当前 EXE，显示余量胶囊。关闭开关并保存即可移除本工具的启动项，不影响其他应用。一般不需要管理员权限。

启动状态直接读取当前用户注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `CodexUsage` 值；启动命令引用当前 EXE 的绝对路径。移动 EXE 或更换版本后，请打开新版设置并保存一次，使启动项指向当前程序。源码模式会使用项目的 Python/Pythonw 与 main.py 绝对路径。

启动项写入失败时设置页会显示错误；如果配置保存失败，会尝试恢复原启动项。测试和预览使用模拟注册表或内存启动项，不开启真实自启动。实现参考 [Microsoft Run 注册表文档](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys)。Windows 可以延迟登录启动，也可以在“启动应用”中禁用它。

代理分段选择器支持左右方向键，滑动开关和扁平按钮支持空格或回车，Tab 可切换焦点。

## 源码运行

建议在 Windows 上使用 Python 3.14（本项目已验证的版本），从项目目录运行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\pythonw.exe main.py
```

仅查询一次并在终端输出解析后的额度和安全错误信息：

```powershell
.venv\Scripts\python.exe main.py --check
```

查询成功返回退出码 0，用量查询失败返回 1。输出不含凭据、请求头或原始接口响应。

## 测试与打包

```powershell
.venv\Scripts\python.exe -m unittest discover -v
powershell -ExecutionPolicy Bypass -File build.ps1
```

如旧版 EXE 正在运行，可使用 `powershell -ExecutionPolicy Bypass -File build.ps1 -Name CodexUsage-0.2.1` 另存新版，避免覆盖被占用的文件。

生成 `dist/CodexUsage.exe`，采用 PyInstaller 单文件、无控制台模式。测试使用临时假凭据和脱敏响应，不读取实际账号；UI 测试会短暂创建真实 Tk 窗口。依赖版本固定在 requirements 文件中。

打包后也可运行不联网、不读真实凭据的窗口与托盘冒烟检查：

```powershell
$p = Start-Process .\dist\CodexUsage.exe -ArgumentList '--smoke-test', '.cache/packaged-smoke' -WindowStyle Hidden -PassThru -Wait
Get-Content .cache/packaged-smoke/checks.json
```

这会展示示例额度，自动验证胶囊、悬停面板、重置点折叠、扁平配置页、主题和代理保存、模拟自启动开关、SOCKS 支持、托盘隐藏恢复、置顶与退出，并保存窗口截图。只写指定测试目录，不修改真实启动项，完成后自动退出。

## 网络与接口

只请求以下两个 HTTPS 地址，拒绝自动跳转，保留 TLS 验证：

- `GET https://chatgpt.com/backend-api/wham/usage`
- `GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits`

请求头使用 `Authorization: Bearer …` 和 `ChatGPT-Account-Id`。接口来自 [Codex 官方源码](https://github.com/openai/codex/blob/d6817454d76885e9d0e118bbf82e507aed6f715a/codex-rs/backend-client/src/client/rate_limit_resets.rs)，属于客户端后端接口，未来可能变更；解析集中在 `codex_usage/core.py` 中。

连接超时或失败时，请在配置页选择可用代理，或在“跟随系统”模式下使用 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量。403 表示访问受限，401 表示登录凭据已失效。默认胶囊在用量错误时以琥珀色标记旧数据，详细错误在悬停面板中显示。

当前版本不包含多账号或历史统计。
