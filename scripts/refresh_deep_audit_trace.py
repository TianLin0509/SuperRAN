"""Refresh only the representative experience-TTI trace in the audit JSON.

The paired Monte-Carlo arrays are intentionally left untouched.  This helper
reruns replication zero after diagnostic-schema changes so the report can show
a recent, post-warm-up TTI with both traffic classes and at least one ACK.
"""
from __future__ import annotations

import json

from run_deep_simulation_audit import (
    EXPERIENCE_DATASET,
    OUT,
    build_experience_tables,
    jsonable,
    run_one_experience,
    select_tti_trace,
    table_trace,
)

from superwireless import load


def main() -> None:
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    tables, _ = build_experience_tables(load(EXPERIENCE_DATASET))
    arm_a = run_one_experience(tables, 0, "scheduled_tbs")
    arm_b = run_one_experience(tables, 0, "legacy_fullband")
    trace_a = select_tti_trace(arm_a)
    trace_b = select_tti_trace(arm_b)
    ue_snap_pairs = {
        (int(row["ue"]), int(row["snapshot"]))
        for row in trace_a["allocations"] + trace_b["allocations"]
    }
    payload["experience"]["trace"] = {
        "a": trace_a,
        "b": trace_b,
        "link_tables": [
            table_trace(tables[ue], snapshot)
            for ue, snapshot in sorted(ue_snap_pairs)
        ],
        "tbs_lookup": arm_a.diagnostics.get("tbs_lookup"),
        "crn_event_mapping": arm_a.diagnostics.get("crn_event_mapping"),
        "byte_conservation": arm_a.diagnostics.get("byte_conservation"),
    }
    OUT.write_text(
        json.dumps(jsonable(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(OUT),
        "arm_a_tti": trace_a["tti"],
        "arm_b_tti": trace_b["tti"],
        "arm_a_allocations": len(trace_a["allocations"]),
        "arm_b_allocations": len(trace_b["allocations"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
