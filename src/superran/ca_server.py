"""CA adapter for the existing MCP system simulation preparation pipeline.

A scoped ContextVar requests preparation without running independent traffic.
It is reset even on failure and is never accepted from an external tool caller.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import inspect
import json
import numpy as np

from .ca import CarrierMember, CarrierSet, CaSchedulerConfig, CaLinkTables, strict_json_value

active_preparation = ContextVar("superran_ca_preparation", default=None)


def ca_grid(member, config, num_rb):
    if float(config.get("bandwidth_hz",config.get("bandwidth", 0))) != member.bandwidth_hz:
        raise ValueError("CA bandwidth does not match dataset configuration")
    if float(config.get("subcarrier_spacing", 0)) != member.scs_khz*1000:
        raise ValueError("CA numerology does not match dataset configuration")
    if num_rb != member.total_prb:
        raise ValueError("CA actual channel PRB count does not match declared member")
    out = member.grid().as_dict()
    out["source"] = "explicit CA carrier profile validated against dataset shape"
    out["rbg_size_basis"] = "CA 100 MHz: 17x16; CA 20 MHz: 6x8+3; real PRB widths"
    return out


def _digest(array):
    a = np.ascontiguousarray(array)
    return hashlib.sha256(str((a.shape,a.dtype.str)).encode()+a.tobytes()).hexdigest()


def run_ca_tool(arguments):
    from . import server, system, load, kpi_view
    signature = inspect.signature(server.sr_system_sim)
    params = {k:v for k,v in arguments.items() if k in signature.parameters}
    request = params.pop("ca_config")
    try:
        if not isinstance(request,dict): raise ValueError("ca_config must be an object")
        unknown = set(request)-{"carriers","scheduler","active_mask"}
        if unknown: raise ValueError(f"unknown CA options: {sorted(unknown)}")
        rows = request.get("carriers")
        if not isinstance(rows,list) or not rows: raise ValueError("CA requires carrier descriptors")
        members, source_rows = [], {}
        for row in rows:
            if not isinstance(row,dict) or set(row)-{"carrier_id","dataset_id","bandwidth_hz","cell_id","is_pcc","csi"}:
                raise ValueError("invalid CA carrier descriptor")
            m = CarrierMember.profile(row["carrier_id"],row["bandwidth_hz"],cell_id=row.get("cell_id",0),is_pcc=row.get("is_pcc",False))
            members.append(m)
            source_rows[m.carrier_id] = row
        carriers = CarrierSet(tuple(members))
        config = CaSchedulerConfig(**request.get("scheduler",{}))
        if params["dataset_id"] != source_rows[carriers.pcc.carrier_id]["dataset_id"]:
            raise ValueError("dataset_id must identify the declared PCC dataset")
        if params.get("target_prb_utilization") is not None:
            raise ValueError("CA automatic load calibration is not implemented")
        prepared, manifest, shared_identity = {}, {}, None
        for m in carriers.members:
            row = source_rows[m.carrier_id]
            dataset = load(row["dataset_id"])
            ue = np.asarray(dataset.scalar("ue_id"))
            serving = np.asarray(dataset.scalar("serving_cell_index"))
            positions = np.asarray(dataset.ue_position)
            if not np.all(np.isfinite(positions)):
                raise ValueError("CA geometry must be finite")
            time_info = {key:dataset.config.get(key) for key in ("num_slots_per_sample","sample_interval_ms","ue_speed_kmh","subcarrier_spacing")}
            identity = {"ue_mapping":_digest(ue),"serving_mapping":_digest(serving),
                        "trajectory":_digest(positions),"time":time_info,
                        "snapshot_ms":system.snapshot_interval_ms(dataset.config)}
            if shared_identity is not None and identity != shared_identity:
                raise ValueError("CA carriers must share exact UE mapping, geometry and time axis")
            shared_identity = identity
            overrides = row.get("csi",{})
            allowed = {"csi_aging","srs_hopping","srs_period_ms","srs_resource_allocation",
                       "srs_period_adaptive","srs_pci_mod3","csi_processing_delay_ms",
                       "csi_report_period_ms","cqi_filter_lambda","cqi_filter_domain","runtime_cqi_enabled"}
            if not isinstance(overrides,dict) or set(overrides)-allowed:
                raise ValueError("per-carrier csi contains unsupported overrides")
            effective_hopping = overrides.get("srs_hopping",params["srs_hopping"])
            if m.bandwidth_hz == 20e6 and effective_hopping not in (False,"off","false","0"):
                raise ValueError("20 MHz requires explicit compatible CSI: srs_hopping=False; 17-hop is 272-PRB only")
            call = {**params,**overrides,"dataset_id":row["dataset_id"],"serving_cell":m.cell_id,"ca_config":None}
            token = active_preparation.set(m)
            try:
                result = server.sr_system_sim(**call)
            finally:
                active_preparation.reset(token)
            if "error" in result: raise ValueError(f"CA {m.carrier_id}: {result['error']}")
            prepared[m.carrier_id] = result
            h = dataset.h_true
            manifest[m.carrier_id] = {"dataset_id":row["dataset_id"],"identity":identity,
                                      "raw_channel_sha256":_digest(h),"estimated_channel_sha256":_digest(dataset.h_est),"csi":overrides,
                                      "config":dataset.config}
        p = prepared[carriers.pcc.carrier_id]
        for cid,value in prepared.items():
            for key in ("snapshot_update_ms","tdd_pattern","s_slot_dl_fraction","duration_s","scs_khz"):
                if getattr(value["sys_cfg"],key) != getattr(p["sys_cfg"],key):
                    raise ValueError(f"CA synchronous timeline mismatch: {key}")
        bundle = CaLinkTables(carriers,{cid:v["tables"] for cid,v in prepared.items()},config,
                              request.get("active_mask"),manifest,{cid:v["sys_cfg"] for cid,v in prepared.items()})
        result = system.simulate_replications(bundle,sys_cfg=p["sys_cfg"],traffic=p["traffic"],sched=p["sched"],kpi=p["kpi"],
            num_replications=params["num_replications"],master_seed=params["seed"],replication_workers=params["replication_workers"])
        out = strict_json_value(result.as_dict())
        out.update(dataset_id=params["dataset_id"],analysis_identity={"ca_combination":bundle.identity()},
            algorithm={"label":params.get("algorithm_label") or f"CA {config.mode}/{config.method}"},
            ca={"combination_identity":bundle.identity(),"carriers":[asdict(m) for m in carriers.members],
                "sources":manifest,"replication_diagnostics":[r.diagnostics.get("ca") for r in result.runs]})
        out = strict_json_value(out)
        out["kpi_view"] = kpi_view.write_kpi_report(out,dataset_id=params["dataset_id"],
            kpi_focus=params.get("kpi_focus"),kpi_intent=params.get("kpi_intent", ""))
        return out
    except (ValueError,TypeError,KeyError,OSError) as exc:
        return {"error":str(exc)}
