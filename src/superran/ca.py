"""Synchronous, co-sited carrier aggregation contracts and integer buffer splits.

The 20 MHz profile is deliberately exposed only through this CA entry point.
The existing company_tdd profile remains unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from collections import deque
from copy import deepcopy
from typing import Any, Mapping
import hashlib
import json
import math
import numpy as np

from .carrier import CarrierGrid, _strict_int


def strict_json_value(value):
    """Unknown numeric metrics retain JSON null, including NumPy scalars."""
    if isinstance(value, dict):
        return {str(k): strict_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [strict_json_value(v) for v in value]
    if isinstance(value, np.generic):
        return strict_json_value(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass(frozen=True)
class CarrierMember:
    carrier_id: str
    cell_id: int
    bandwidth_hz: float
    scs_khz: int
    num_rbg: int
    rbg_prb_sizes: tuple[int, ...]
    is_pcc: bool = False

    def __post_init__(self):
        if not isinstance(self.carrier_id, str) or not self.carrier_id.strip():
            raise ValueError("carrier_id must be a nonempty string")
        _strict_int("cell_id", self.cell_id, minimum=0)
        _strict_int("scs_khz", self.scs_khz, minimum=1)
        _strict_int("num_rbg", self.num_rbg, minimum=1)
        sizes = tuple(_strict_int("rbg_prb_size", n, minimum=1) for n in self.rbg_prb_sizes)
        if len(sizes) != self.num_rbg:
            raise ValueError("num_rbg does not match rbg_prb_sizes")
        if isinstance(self.bandwidth_hz, bool) or not math.isfinite(self.bandwidth_hz) or self.bandwidth_hz <= 0:
            raise ValueError("bandwidth_hz must be finite and positive")
        if not isinstance(self.is_pcc, bool):
            raise ValueError("is_pcc must be bool")
        object.__setattr__(self, "rbg_prb_sizes", sizes)

    @property
    def total_prb(self):
        return sum(self.rbg_prb_sizes)

    @property
    def boundaries(self):
        stops = np.cumsum((0,) + self.rbg_prb_sizes)
        return tuple((int(a), int(b)) for a, b in zip(stops[:-1], stops[1:]))

    @classmethod
    def profile(cls, carrier_id: str, bandwidth_hz: float, *, cell_id=0, is_pcc=False):
        if bandwidth_hz == 100e6:
            sizes = (16,) * 17
        elif bandwidth_hz == 20e6:
            sizes = (8,) * 6 + (3,)
        else:
            raise ValueError("CA profiles support 100 MHz / 272 PRB or 20 MHz / 51 PRB")
        return cls(carrier_id, cell_id, bandwidth_hz, 30, len(sizes), sizes, is_pcc)

    def grid(self):
        return CarrierGrid(self.total_prb, self.scs_khz, 2, max(self.rbg_prb_sizes),
                           0, self.boundaries, "superran-ca-v1", False,
                           273 if self.bandwidth_hz==100e6 else self.total_prb)


@dataclass(frozen=True)
class CarrierSet:
    members: tuple[CarrierMember, ...]

    def __post_init__(self):
        members = tuple(self.members)
        if not members or not all(isinstance(c, CarrierMember) for c in members):
            raise ValueError("CarrierSet requires CarrierMember entries")
        if len({c.carrier_id for c in members}) != len(members):
            raise ValueError("duplicate carrier_id")
        if sum(c.is_pcc for c in members) != 1:
            raise ValueError("CarrierSet requires exactly one PCC")
        if len({c.cell_id for c in members}) != 1:
            raise ValueError("cross-site CA is unsupported")
        if any(c.scs_khz != 30 for c in members):
            raise ValueError("CA requires synchronous 30 kHz SCS")
        object.__setattr__(self, "members", tuple(sorted(members, key=lambda c: (not c.is_pcc, c.carrier_id))))

    @property
    def pcc(self):
        return next(c for c in self.members if c.is_pcc)

    @property
    def scc_list(self):
        return tuple(c for c in self.members if not c.is_pcc)

    @property
    def num_cc(self):
        return len(self.members)

    @classmethod
    def single(cls, member: CarrierMember | None = None):
        return cls((replace(member, is_pcc=True) if member else CarrierMember.profile("pcc", 100e6, is_pcc=True),))


@dataclass(frozen=True)
class CaSchedulerConfig:
    mode: str = "off"
    method: str = "average"
    split_threshold_bits: int = 72000
    pcc_delay_tti: int = 0
    scc_delay_tti: int = 0
    scell_activation_enabled: bool = False
    scell_activation_thld_bps: float = 0

    def __post_init__(self):
        if self.mode not in ("off", "independent", "cort"):
            raise ValueError("CA mode must be off / independent / cort")
        if self.method not in ("average", "bandwidth", "rbnum"):
            raise ValueError("CA method must be average / bandwidth / rbnum")
        _strict_int("split_threshold_bits", self.split_threshold_bits, minimum=0)
        for name in ("pcc_delay_tti", "scc_delay_tti"):
            _strict_int(name, getattr(self, name), minimum=0)
            if getattr(self, name):
                raise ValueError("CA delay FIFO is not implemented; delays must be zero")
        if not isinstance(self.scell_activation_enabled, bool):
            raise ValueError("scell_activation_enabled must be bool")
        if self.scell_activation_enabled or self.scell_activation_thld_bps != 0:
            raise ValueError("automatic SCell activation is not implemented; use static active mask")


@dataclass
class UeCarrierState:
    carrier_id: str
    is_pcc: bool
    scell_active: bool = True
    split_buffer_bits: int = 0
    cort_scheduled_bits: int = 0
    delay_queue: deque = field(default_factory=deque)
    r_avg: float = 1e-6
    olla_db: float = 0.0  # Historical field name; unit is continuous MCS index.
    harq_inflight: dict = field(default_factory=dict)
    cort_reserved_payload_bits: int = 0
    cort_newtx_payload_bits: int = 0


def split_buffer(queue_bytes: int, carriers: CarrierSet, config: CaSchedulerConfig,
                 *, average_bytes: float = 0, capacities: Mapping[str, float] | None = None):
    """Freeze integer-byte quotas. Zero predicted service never silently gets water."""
    n = _strict_int("queue_bytes", queue_bytes, minimum=0)
    caps = dict(capacities or {})
    if not math.isfinite(average_bytes) or average_bytes < 0:
        raise ValueError("average_bytes must be finite and nonnegative")
    if any(not math.isfinite(v) or v < 0 for v in caps.values()):
        raise ValueError("capacities must be finite and nonnegative")
    out = {c.carrier_id: 0 for c in carriers.members}
    active = [c for c in carriers.members if caps.get(c.carrier_id, 0) > 0]
    if not active or not n:
        return out, "no_service" if n else "empty"
    threshold = (0 if config.split_threshold_bits == 0 else
                 max(config.split_threshold_bits * carriers.pcc.bandwidth_hz / 100e6, average_bytes * 8))
    if config.mode == "off" or (threshold and n * 8 <= threshold):
        target = (max(active, key=lambda c: caps[c.carrier_id]) if config.method == "rbnum" and config.mode != "off"
                  else carriers.pcc)
        if target in active:
            out[target.carrier_id] = n
            return out, "small_packet"
        return out, "pcc_unavailable"
    weights = [1. if config.method == "average" else float(caps[c.carrier_id]) for c in active]
    # Integer arithmetic for uniform splits, stable largest remainder for weighted.
    from fractions import Fraction
    weights = [Fraction(str(w)) for w in weights]
    exact = [n * w / sum(weights) for w in weights]
    floors = [int(x) for x in exact]
    for i in sorted(range(len(active)), key=lambda i: -(exact[i] - floors[i]))[:n-sum(floors)]:
        floors[i] += 1
    out.update({c.carrier_id: q for c, q in zip(active, floors)})
    return out, "split"


def _table_identity(value):
    """Lossless structural identity, including dataclass fields hidden by as_dict."""
    if is_dataclass(value) and not isinstance(value,type):
        return {"dataclass":type(value).__qualname__,
                "fields":{f.name:_table_identity(getattr(value,f.name)) for f in fields(value)}}
    if isinstance(value,np.ndarray):
        a=np.ascontiguousarray(value)
        if a.dtype.hasobject:
            raise ValueError("CA runtime table arrays cannot contain Python objects")
        return {"array_shape":list(a.shape),"dtype":a.dtype.str,
                "sha256":hashlib.sha256(a.tobytes()).hexdigest()}
    if isinstance(value,np.generic):
        return _table_identity(value.item())
    if isinstance(value,Mapping):
        pairs=[(_table_identity(k),_table_identity(v)) for k,v in value.items()]
        return {"mapping":sorted(pairs,key=lambda pair:json.dumps(pair[0],sort_keys=True,allow_nan=False))}
    if isinstance(value,(tuple,list)):
        return {type(value).__name__:[_table_identity(v) for v in value]}
    if isinstance(value,float) and not math.isfinite(value):
        return {"float":str(value)}
    if isinstance(value,complex):
        return {"complex":[_table_identity(value.real),_table_identity(value.imag)]}
    if value is None or isinstance(value,(str,int,float,bool)):
        return value
    raise TypeError(f"unsupported CA runtime table identity value: {type(value).__name__}")


@dataclass
class CaLinkTables:
    """Explicit identity-keyed input; compatible with the replication transport."""
    carriers: CarrierSet
    tables: dict[str, list[Any]]
    config: CaSchedulerConfig = field(default_factory=CaSchedulerConfig)
    active_mask: dict[str, tuple[bool, ...]] | None = None
    source_manifest: dict[str, Any] = field(default_factory=dict)
    system_configs: dict[str, Any] | None = None

    def __len__(self):
        return len(self.tables[self.carriers.pcc.carrier_id])

    def identity(self):
        runtime_tables = _table_identity(self.tables)
        tables_digest = hashlib.sha256(json.dumps(runtime_tables,sort_keys=True,allow_nan=False).encode())
        payload = {"carriers": [asdict(c) for c in self.carriers.members],
                   "sources": self.source_manifest, "active_mask": self.active_mask,
                   "link_tables_identity_version":2,"link_tables_sha256":tables_digest.hexdigest(),
                   "system_configs":{cid:cfg.as_dict() for cid,cfg in sorted(self.system_configs.items())} if self.system_configs else None}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def validate(self, sys_cfg, *, strict_prediction=True):
        ids = {c.carrier_id for c in self.carriers.members}
        if set(self.tables) != ids:
            raise ValueError("CA table keys must exactly match carrier_id")
        if self.system_configs is not None:
            if set(self.system_configs)!=ids:
                raise ValueError("CA system_configs must cover exactly all carriers")
            for cfg in self.system_configs.values():
                for key in ("duration_s","scs_khz","tdd_pattern","s_slot_dl_fraction","snapshot_update_ms"):
                    if getattr(cfg,key)!=getattr(sys_cfg,key):
                        raise ValueError(f"CA synchronous configuration mismatch: {key}")
        if not len(self):
            raise ValueError("CA requires at least one UE")
        if strict_prediction:
            if set(self.source_manifest) != ids or any("identity" not in m for m in self.source_manifest.values()):
                raise ValueError("CA requires a source manifest with shared geometry/time identity for every CC")
            identities = [m["identity"] for m in self.source_manifest.values()]
            if not identities[0] or any(i != identities[0] for i in identities[1:]):
                raise ValueError("CA source geometry/time identities differ")
            times = identities[0].get("snapshot_times_ms")
            if times is not None and len(times)>1 and not np.allclose(np.diff(times),sys_cfg.snapshot_update_ms,rtol=0,atol=1e-9):
                raise ValueError("CA source snapshot spacing differs from system clock")
        expected = tuple(t.ue for t in self.tables[self.carriers.pcc.carrier_id])
        if expected != tuple(range(len(self))):
            raise ValueError("CA UE mapping must be contiguous and ordered")
        snaps = None
        if self.active_mask is not None:
            if set(self.active_mask) != ids:
                raise ValueError("active_mask must cover all carriers")
            if any(len(row) != len(self) or any(type(v) is not bool for v in row) for row in self.active_mask.values()):
                raise ValueError("active_mask must contain one bool per UE per CC")
        for c in self.carriers.members:
            if sys_cfg.scs_khz != c.scs_khz:
                raise ValueError("mixed numerology is unsupported")
            rows = self.tables[c.carrier_id]
            if tuple(t.ue for t in rows) != expected:
                raise ValueError("CA UE identity/order mismatch")
            for t in rows:
                if t.serving_cell_index != c.cell_id:
                    raise ValueError("CA serving cell mismatch")
                if snaps is None: snaps = t.sinr_db.shape[0]
                if t.sinr_db.shape[0] != snaps:
                    raise ValueError("CA snapshot timeline mismatch")
                if strict_prediction:
                    shape = (*t.sinr_db.shape, c.num_rbg)
                    for name in ("sinr_rbg_db", "sinr_tx_rbg_db"):
                        value = getattr(t, name, None)
                        if value is None or np.shape(value) != shape or not np.all(np.isfinite(value)):
                            raise ValueError(f"CA requires finite {name} with shape {shape}")
                    if t.sinr_tx_db is None:
                        raise ValueError("CA requires explicit transmitter-side SINR")
                    if tuple(t.frequency_rbg_boundaries or ()) != c.boundaries:
                        raise ValueError("CA link-table RBG geometry mismatch")


def _build_input_identity(value):
    """Keep build parameters and exact array fingerprints in the source contract."""
    if isinstance(value, np.ndarray):
        a = np.ascontiguousarray(value)
        if a.dtype.hasobject:
            raise ValueError("CA build arrays must not contain Python objects")
        return {"shape":list(a.shape),"dtype":a.dtype.str,"sha256":hashlib.sha256(a.tobytes()).hexdigest()}
    if hasattr(value, "as_dict"):
        return _build_input_identity(value.as_dict())
    if isinstance(value, Mapping):
        return {str(k):_build_input_identity(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [_build_input_identity(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def build_ca_link_tables(carriers: CarrierSet, inputs: Mapping[str, dict], *,
                         config: CaSchedulerConfig | None = None, active_mask=None,
                         source_manifest=None):
    """Each carrier must supply its own h_users/geo_sinr_db and explicit build kwargs."""
    from .system import build_link_tables
    if set(inputs) != {c.carrier_id for c in carriers.members}:
        raise ValueError("CA input keys must match carrier_id exactly")
    tables = {}
    manifest = deepcopy(dict(source_manifest or {}))
    for c in carriers.members:
        args = dict(inputs[c.carrier_id])
        if carriers.num_cc>1 and "csi" not in args:
            raise ValueError("CA requires an explicit CsiConfig per carrier; CSI is never disabled implicitly")
        for key in ("rb_per_rbg", "rbg_boundaries"):
            if key in args:
                raise ValueError(f"{key} is owned by CarrierMember geometry")
        hs = args.pop("h_users")
        geo = args.pop("geo_sinr_db")
        geometry = args.pop("geometry", None)
        if carriers.num_cc > 1 and geometry is None:
            raise ValueError("each CA input needs geometry with ue_positions and snapshot_times_ms")
        if geometry is not None:
            positions = np.asarray(geometry["ue_positions"],dtype=np.float64)
            times = np.asarray(geometry["snapshot_times_ms"],dtype=np.float64)
            if positions.shape != (len(hs),3) or not np.all(np.isfinite(positions)):
                raise ValueError("ue_positions must contain one finite xyz per UE")
            if times.ndim!=1 or not len(times) or not np.all(np.isfinite(times)) or np.any(np.diff(times)<=0):
                raise ValueError("snapshot_times_ms must be finite and strictly increasing")
            if any(np.ndim(h)!=4 or np.shape(h)[0]!=len(times) for h in hs):
                raise ValueError("CA channel timeline must match snapshot_times_ms")
            identity = {"ue_positions":positions.tolist(),"snapshot_times_ms":times.tolist()}
            supplied = manifest.get(c.carrier_id,{})
            if not isinstance(supplied,dict):
                raise ValueError("CA source manifest entries must be objects")
            if "identity" in supplied and supplied["identity"] != identity:
                raise ValueError("declared CA source geometry/time differs from actual inputs")
            manifest[c.carrier_id] = {**supplied,"identity":identity,
                "link_build_parameters":_build_input_identity({**args,"geo_sinr_db":geo}),
                "csi":args["csi"].as_dict() if args.get("csi") is not None else None,
                "raw_channel_sha256":hashlib.sha256(b"".join(np.ascontiguousarray(h).tobytes() for h in hs)).hexdigest(),
                "estimated_channel_sha256":hashlib.sha256(b"".join(np.ascontiguousarray(h).tobytes() for h in args.get("h_for_precoding_users",hs))).hexdigest()}
        if any(np.shape(h)[1] != c.total_prb for h in hs):
            raise ValueError("CA channel must have one frequency row per actual PRB")
        tables[c.carrier_id] = build_link_tables(hs, geo, rb_per_rbg=max(c.rbg_prb_sizes),
                                                rbg_boundaries=c.boundaries, **args)
        for table in tables[c.carrier_id]:
            if table.serving_cell_index is not None and table.serving_cell_index != c.cell_id:
                raise ValueError("declared CA cell conflicts with built link table")
            table.serving_cell_index = c.cell_id
            if table.frequency_rbg_boundaries is not None and tuple(table.frequency_rbg_boundaries) != c.boundaries:
                raise ValueError("built CA table geometry conflicts with requested boundaries")
            table.frequency_rbg_boundaries = c.boundaries
    return CaLinkTables(carriers, tables, config or CaSchedulerConfig(), active_mask, manifest)
