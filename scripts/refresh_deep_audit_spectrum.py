"""Refresh the spectrum block after inference/reporting logic changes."""
from __future__ import annotations

import json

from run_deep_simulation_audit import OUT, jsonable, run_spectrum


def main() -> None:
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    payload["spectrum"] = run_spectrum()
    OUT.write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    primary = payload["spectrum"]["primary"]
    print(json.dumps({
        "output": str(OUT),
        "independence_unit": primary["independence_unit"],
        "raw_n": primary["n_raw_observations"],
        "inference": primary["paired"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
