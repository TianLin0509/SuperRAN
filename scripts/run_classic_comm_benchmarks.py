"""Run the preregistered classic communication benchmark suite."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from superran import benchmarks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--output", default=str(benchmarks.DEFAULT_OUTPUT))
    args = parser.parse_args()
    result = benchmarks.run_suite(case_ids=args.cases)
    path = benchmarks.write_result(result, args.output)
    print(json.dumps({
        "path": str(path), "status": result["overall_status"],
        "n_pass": result["n_pass"], "n_fail": result["n_fail"],
    }, ensure_ascii=False))
    return 0 if result["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
