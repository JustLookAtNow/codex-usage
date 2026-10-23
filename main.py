"""Windowed entry point; --check prints only safe status and parsed quota data."""
import argparse
import json
from dataclasses import asdict

from codex_usage.core import QuotaClient


def main():
    parser = argparse.ArgumentParser(description="Codex 余量浮窗")
    parser.add_argument("--check", action="store_true", help="只读查询一次，不启动窗口")
    parser.add_argument("--smoke-test", metavar="OUTPUT", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        from codex_usage.diagnostics import run_smoke_test
        return run_smoke_test(args.smoke_test)
    if args.check:
        client = QuotaClient()
        from codex_usage.settings import Settings
        settings = Settings.load()
        client.configure_network(settings.proxy_mode, settings.proxy_url)
        try:
            result = client.poll()
            print(json.dumps(asdict(result), ensure_ascii=True, default=str, indent=2))
            return 0 if result.usage.value is not None else 1
        finally:
            client.close()
    from codex_usage.app import run
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
