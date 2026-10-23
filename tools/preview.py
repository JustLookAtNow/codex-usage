"""Run the same synthetic-data UI check used for the packaged executable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_usage.diagnostics import run_smoke_test

if __name__ == "__main__":
    output = Path(__file__).resolve().parents[1] / ".cache" / "preview"
    code = run_smoke_test(output)
    print((output / "checks.json").read_text())
    raise SystemExit(code)
