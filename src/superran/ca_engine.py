"""Coordinate existing experience state machines; no second PHY or KPI model."""
from __future__ import annotations

from dataclasses import asdict, replace
import numpy as np

from . import experience as ex
from . import rng as rg
from .ca import CaLinkTables, CaSchedulerConfig, UeCarrierState, split_buffer, strict_json_value


class _CarrierBook:
    def __init__(self, book, carrier_id):
        self.book, self.carrier_id = book, carrier_id

    def generator(self, name):
        return self.book.namespaced_generator(name, f"ca/{self.carrier_id}")

    def __getattr__(self, name):
        return getattr(self.book, name)

    def as_dict(self):
        return {**self.book.as_dict(), "carrier_id": self.carrier_id}


class _TrafficView:
    def __init__(self, shared, lane):
        self.shared, self.lane = shared, lane

    def __getattr__(self, name):
        return getattr(self.shared, name)

    def step(self, tti):
        # The coordinator advanced the one shared source at the start barrier.
        if tti != self.lane.tti:
            raise RuntimeError("CA traffic barrier out of order")

    def bytes_left(self, u):
        return min(int(self.lane.quota[u]), self.shared.bytes_left(u))

    def has_data(self, u):
        return self.lane.active[u] and (self.bytes_left(u) > 0 or u in self.lane.retx)

    def transmit(self, u, tti, scheduled_bytes, payload_bytes, *, ack, is_retx=False):
        if not is_retx and payload_bytes > self.bytes_left(u):
            raise RuntimeError("CA grant exceeds frozen quota")
        sent = self.shared.transmit(u, tti, scheduled_bytes, payload_bytes, ack=ack, is_retx=is_retx)
        if not is_retx:
            self.lane.quota[u] -= sent
            self.lane.newtx[u] += sent
        return sent


class _Lane:
    def __init__(self, member, shared, active):
        self.member, self.active = member, np.array(active, dtype=bool)
        self.traffic = _TrafficView(shared, self)
        self.global_average = np.full(len(active), 1e-6)
        self.ue_order = None
        self.states = [UeCarrierState(member.carrier_id,member.is_pcc,scell_active=bool(a)) for a in active]
        self.reset(0)

    def reset(self, tti):
        self.tti = tti
        self.quota = np.zeros(len(self.active), dtype=np.int64)
        self.credit = np.zeros(len(self.active))
        self.newtx = np.zeros(len(self.active), dtype=np.int64)
        self.retx = {}
        self.payload, self.bursts = {}, {}

    def record_tick(self, payload, bursts):
        self.payload, self.bursts = dict(payload), dict(bursts)


def _capacity(context, lane, *, method):
    """Use current reported SINR, actual rank, OLLA and PRB geometry only."""
    table_rows, cfg = context["tables"], context["sys_cfg"]
    tti, slot = context["tti"], context["slot"]
    snap = (tti // context["snap_every"]) % context["n_snap"]
    out = np.zeros(len(table_rows))
    if slot not in "DS":
        return out
    lane.retx = context["_retx_now"](slot)
    for u, table in enumerate(table_rows):
        if not lane.active[u] or not context["_can_send"](u, slot) or u in lane.retx:
            continue
        if table.outage is not None and table.outage[snap]:
            continue
        rank_ctl = context["rank_ctl"]
        # Observation belongs to the original loop exactly once; a prediction
        # must not advance its filter a second time (especially scope='tti').
        rank = rank_ctl.rank_for(u, int(table.best_rank[snap]))
        values = ex._frequency_su_values(table=table, snap=snap, rank=rank,
            indices=tuple(range(cfg.num_rbg)), olla_db=float(context["olla_db"][u]),
            olla_enabled=context["sched"].olla_enabled, lookup=context["lookup"], slot=slot)
        if method == "bandwidth":
            # Fixed bandwidth weight at the currently feasible rank and predicted MCS.
            mcs = values["mcs"]
            out[u] = sum(cfg.rbg_prb_sizes) * ex.la.MCS_TABLES[context["lookup"].mcs_table][mcs].se * rank
        elif method == "rbnum":
            # Each RBG keeps its real PRB width; available resources exclude frozen retx.
            blocked = sum(tb.n_rbg for v, tb in lane.retx.items() if lane.active[v])
            available = max(0, cfg.num_rbg - blocked)
            scores = []
            for rbg in range(cfg.num_rbg):
                v = ex._frequency_su_values(table=table, snap=snap, rank=rank,
                    indices=(rbg,), olla_db=float(context["olla_db"][u]),
                    olla_enabled=context["sched"].olla_enabled, lookup=context["lookup"], slot=slot)
                scores.append(v["tbs"])
            out[u] = sum(sorted(scores, reverse=True)[:available])
        else:
            out[u] = values["tbs"]
    return out


def _revalue(grant, indices, context, targets):
    """Recompute a single unchanged SU/MU identity through existing TBS routines."""
    kw = dict(snap=context["snap"], indices=tuple(indices), lookup=context["lookup"],
              slot=context["slot"], olla_enabled=context["sched"].olla_enabled)
    if grant.mode == "MU":
        pair = context["tables"][grant.users[0]].mu_links[grant.users[1]]
        values = ex._frequency_mu_values(pair_link=pair, users=grant.users,
            tables=context["tables"], ranks=grant.ranks, su_olla_db=context["olla_db"],
            mu_olla_db=context["mu_olla_db"], **kw)
    else:
        u = grant.users[0]
        values = [ex._frequency_su_values(table=context["tables"][u], rank=grant.ranks[0],
                    olla_db=float(context["olla_db"][u]), **kw)]
    useful = tuple(min(targets[u], v["tbs"]) for u, v in zip(grant.users, values))
    return replace(grant, rbg_indices=tuple(indices), n_rbg=len(indices),
        base_tx_sinr_db=tuple(v["base"] for v in values), true_sinr_db=tuple(v["true"] for v in values),
        corr_loss_db=tuple(v.get("corr", 0.) for v in values),
        mcs=tuple(v["mcs"] for v in values), mcs_without_olla=tuple(v["mcs_without_olla"] for v in values),
        tbs_bytes=tuple(v["tbs"] for v in values), useful_bytes=useful,
        reservation_id=None, frequency_selected_source="ca_cort_expansion")


def correct_cort_plan(context, targets, *, expand=True):
    """Expand existing grants only; frozen retransmissions are never rewritten."""
    plan = context["selected_plan"]
    grants = list(plan.grants)
    used = {i for g in grants for i in g.rbg_indices}
    free = set(range(context["sys_cfg"].num_rbg)) - used
    expanded = 0
    for index, old in enumerate(grants):
        if any(u in context["harq_pending"] for u in old.users):
            continue
        current = _revalue(old, old.rbg_indices, context, targets)
        # Need is found by real lookup on each enlarged bitmap, including a changed MCS.
        while expand and free and any(v < targets[u] for u, v in zip(current.users, current.useful_bytes)):
            candidates = [_revalue(current, tuple(sorted((*current.rbg_indices, r))), context, targets) for r in sorted(free)]
            best = max(candidates, key=lambda g: (sum(g.useful_bytes), sum(g.tbs_bytes), tuple(-r for r in g.rbg_indices)))
            if any(a < b for a, b in zip(best.useful_bytes, current.useful_bytes)):
                break
            trial = list(grants)
            trial[index] = best
            admitted = ex._admit_plan_resources(replace(plan, grants=tuple(trial)), budget=context["resource_budget"], tti=context["tti"])
            if len(admitted.grants) != len(trial):
                break
            free.difference_update(best.rbg_indices)
            current = best
            expanded += 1
        # Recompute Need and full-band feasibility against the corrected water.
        # Search the same real TBS/predicted-SINR prefixes as the old scheduler.
        need, fits, potentials, pool_need, pool_fits = [],[],[],[],[]
        for side,u in enumerate(current.users):
            scores = context["tables"][u].sinr_tx_rbg_db[context["snap"],current.ranks[side]-1]
            def evaluate(indices, side=side):
                return _revalue(current,indices,context,targets).tbs_bytes[side]
            full = ex._frequency_pool_audit(tuple(range(context["sys_cfg"].num_rbg)),scores,
                cursor=context["cursor"],evaluate=evaluate,tbs_of=int,queue_bytes=targets[u])
            pool = ex._frequency_pool_audit(tuple(sorted(set(current.rbg_indices)|free)),scores,
                cursor=context["cursor"],evaluate=evaluate,tbs_of=int,queue_bytes=targets[u])
            need.append(full[0]);fits.append(full[1]);potentials.append(full[2])
            pool_need.append(pool[0]);pool_fits.append(pool[1])
        grants[index] = replace(current,required_rbg=tuple(need),fits_in_fullband=tuple(fits),
            potential_fullband_bytes=tuple(potentials),required_rbg_from_remaining_pool=tuple(pool_need),
            fits_in_remaining_pool=tuple(pool_fits))
    updated = replace(plan, grants=tuple(grants), useful_bytes=sum(sum(g.useful_bytes) for g in grants),
                      used_rbg=sum(g.n_rbg for g in grants), clears_all_queues=False)
    admitted = ex._admit_plan_resources(updated, budget=context["resource_budget"], tti=context["tti"])
    if len(admitted.grants) != len(grants):
        raise RuntimeError("CORT final admission lost an existing grant")
    return admitted, expanded


def _coalesce_events(shared, bursts, tti):
    for b in bursts.values():
        tail = []
        while b.tx_events and b.tx_events[-1].tti == tti:
            tail.append(b.tx_events.pop())
        if tail:
            b.tx_attempts -= len(tail) - 1
            b.tx_events.append(replace(tail[0], payload_bytes=sum(e.payload_bytes for e in tail),
                scheduled_bytes=sum(e.scheduled_bytes for e in tail), padding_bytes=sum(e.padding_bytes for e in tail),
                ack=all(e.ack for e in tail)))


def _sum_nested(values):
    first = values[0]
    if isinstance(first, dict):
        keys = set().union(*(v.keys() for v in values))
        return {k: _sum_nested([v.get(k, 0) for v in values]) for k in keys}
    return sum(values)


_ADDITIVE = """served served_measured scheduled_tbs_measured attempted_payload_measured padding_measured
    sched_cnt_measured mcs_sum_measured mcs_first_sum_measured rank_sum_measured tx_count_measured
    nack_count_measured retx_count_measured retx_nack_count_measured acked_payload_measured
    acked_tbs_measured acked_payload_from_pre_window user_grant_prb_equiv user_attributed_prb_equiv
    user_mu_grant_prb_equiv user_mu_tx_measured allocated_rbg allocated_rbg_full allocated_rbg_equiv
    allocated_prb_equiv allocated_logical_prb_equiv available_rbg_equiv available_prb_equiv
    mu_rbg mu_prb_equiv mu_user_tx su_decisions mu_decisions su_forced_clear harq_retx_forced_su
    pf_gain_rejects su_plan_useful mu_plan_useful outage_skips feedback_wait_skips finalizer_grant_count
    frequency_grant_count frequency_quality_selected_count frequency_incremental_useful
    frequency_evaluated_subsets mu_candidate_count mu_candidate_feasible_count mu_candidate_selected_count
    overlap_violations starvation_lifts class_acked class_alloc_rbg class_physical_rbg_share
    adaptation_stats mode_tx_by_ue mode_nack_by_ue mode_expected_bler_by_ue
    resource_rejection_reasons resource_evaluated_rejection_reasons mu_candidate_rejection_reasons""".split()
_CONCAT = """rbg_hist allocation_sample allocation_recent frequency_score_gains
    mu_candidate_selected_scores pf_gain_ratios mixed_edf_medians mixed_epf_medians""".split()


def simulate_ca(bundle: CaLinkTables, *, sys_cfg, traffic, sched, kpi, book, progress=None):
    from .system import SystemResult, simulate
    strict = bundle.config.mode != "off" and bundle.carriers.num_cc > 1
    bundle.validate(sys_cfg, strict_prediction=strict)
    pcc = bundle.carriers.pcc
    if not strict:
        c = pcc
        cfg = replace(bundle.system_configs[c.carrier_id] if bundle.system_configs else sys_cfg,
                      num_rbg=c.num_rbg, rb_per_rbg=max(c.rbg_prb_sizes), rbg_prb_sizes=c.rbg_prb_sizes)
        result = simulate(bundle.tables[c.carrier_id], sys_cfg=cfg, traffic=traffic, sched=sched, kpi=kpi, rng=book, progress=progress)
        if bundle.carriers.num_cc > 1:
            result.notes.append("CA off: only PCC used; unused carriers: " + ",".join(c.carrier_id for c in bundle.carriers.scc_list))
        return result
    if sched.algorithm != "pf":
        raise ValueError("multi-carrier CA currently requires PF scheduling")
    if sched.frequency_selective == "off":
        raise ValueError("multi-carrier CA requires per-RBG frequency selection")
    n, members = len(bundle), bundle.carriers.members
    shared = ex.ExperienceTraffic(traffic, n, sys_cfg.tti_ms, book.generator("traffic"))
    lanes, generators, states = {}, {}, {}
    for c in members:
        lane = _Lane(c, shared, bundle.active_mask[c.carrier_id] if bundle.active_mask else [True] * n)
        cfg = replace(bundle.system_configs[c.carrier_id] if bundle.system_configs else sys_cfg,
                      num_rbg=c.num_rbg, rb_per_rbg=max(c.rbg_prb_sizes), rbg_prb_sizes=c.rbg_prb_sizes)
        gen = ex._experience_steps(bundle.tables[c.carrier_id], sys_cfg=cfg, traffic_cfg=traffic,
            sched=sched, kpi=kpi, book=_CarrierBook(book, c.carrier_id), s_slot_fraction=cfg.s_slot_dl_fraction, lane=lane)
        lanes[c.carrier_id], generators[c.carrier_id] = lane, gen
        states[c.carrier_id] = next(gen)
    global_avg = np.full(n, 1e-6)
    diagnostics = []
    aggregate_trace = {}
    hist = np.zeros(sum(c.num_rbg for c in members)+1, dtype=np.int64)
    counts = dict(busy_tti=0, multi_ue_tti=0, scheduled_ues_sum=0, mu_tti=0, max_rbg_in_tti=0)
    user_tti_counts = np.zeros(n,dtype=np.int64)
    user_retx_tti_counts = np.zeros(n,dtype=np.int64)
    expanded_total = 0
    warmup = kpi.resolve_warmup_tti(sys_cfg.tti_ms)
    for tti in range(sys_cfg.num_tti):
        if any(stage != "start" or state["tti"] != tti for stage, state in states.values()):
            raise RuntimeError("CA start barrier diverged")
        contexts, caps, predicted_bytes = {}, {}, {}
        for cid, lane in lanes.items():
            lane.reset(tti)
            lane.global_average = global_avg
            stage, context = next(generators[cid])
            if stage != "ready": raise RuntimeError("CA feedback barrier diverged")
            contexts[cid] = context
            caps[cid] = _capacity(context, lane, method=bundle.config.method)
            predicted_bytes[cid] = _capacity(context,lane,method="fullband")
        shared.step(tti)
        b0 = np.asarray([shared.bytes_left(u) for u in range(n)], dtype=np.int64)
        order = sorted(lanes, key=lambda cid: (-float(np.sum(predicted_bytes[cid])), not lanes[cid].member.is_pcc, cid))
        score = np.max(list(predicted_bytes.values()), axis=0) / np.maximum(global_avg, 1e-9)
        ue_order = sorted(range(n), key=lambda u: (-score[u], u))
        reasons = []
        for u in range(n):
            split_cfg = bundle.config if bundle.config.mode == "independent" else replace(bundle.config, method="average", split_threshold_bits=0)
            quotas, reason = split_buffer(int(b0[u]), bundle.carriers, split_cfg, average_bytes=float(global_avg[u]),
                capacities={cid: float(v[u]) for cid, v in caps.items()})
            reasons.append(reason)
            for cid in lanes: lanes[cid].quota[u] = quotas[cid]
        frozen_quotas = {cid: lane.quota.copy() for cid,lane in lanes.items()}
        plans, reservations = {}, {cid: np.zeros(n, dtype=np.int64) for cid in lanes}
        for cid in order:
            lane = lanes[cid]
            lane.ue_order = ue_order if bundle.config.mode == "cort" else None
            stage, context = next(generators[cid])
            if stage == "plan":
                plans[cid] = context
                for g in context["selected_plan"].grants:
                    for u,payload in zip(g.users,g.useful_bytes):
                        if u not in context["harq_pending"]: reservations[cid][u] += payload
            elif stage not in ("start", "finished"):
                raise RuntimeError("CA plan barrier diverged")
            states[cid] = (stage, context)
        chosen = {}
        corrections = {}
        for cid in order:
            if cid not in plans: continue
            context, lane = plans[cid], lanes[cid]
            if bundle.config.mode == "cort":
                target = np.maximum(0, b0 - sum((v for key,v in reservations.items() if key != cid), np.zeros(n,dtype=np.int64)))
                for u in context["queue_bytes"]:
                    if u not in context["harq_pending"]:
                        context["queue_bytes"][u] = int(target[u])
                        lane.quota[u] = int(target[u])
                plan, added = correct_cort_plan(context, context["queue_bytes"])
                expanded_total += added
                corrections[cid] = added
                reservations[cid][:] = 0
                for g in plan.grants:
                    for u,payload in zip(g.users,g.useful_bytes):
                        if u not in context["harq_pending"]: reservations[cid][u] += payload
            else: plan = context["selected_plan"]
            chosen[cid] = plan
        if np.any(sum(reservations.values()) > b0):
            raise RuntimeError("CA initial payload reservations exceed original shared buffer")
        occupied = 0
        users = set()
        for cid in order:
            if cid not in chosen: continue
            plan = chosen[cid]
            occupied += sum(g.n_rbg for g in plan.grants)
            users.update(u for g in plan.grants for u in g.users)
            states[cid] = generators[cid].send(plan)
        raw_tbs = {cid: np.zeros(n,dtype=np.int64) for cid in lanes}
        for cid,plan in chosen.items():
            for g in plan.grants:
                for u,tbs in zip(g.users,g.tbs_bytes): raw_tbs[cid][u] += tbs
        for cid,lane in lanes.items():
            for u,carrier_state in enumerate(lane.states):
                carrier_state.split_buffer_bits = int(frozen_quotas[cid][u])*8
                carrier_state.cort_scheduled_bits = int(raw_tbs[cid][u])*8
                carrier_state.cort_reserved_payload_bits = int(reservations[cid][u])*8
                carrier_state.cort_newtx_payload_bits = int(lane.newtx[u])*8
                carrier_state.olla_db = float(contexts[cid]["olla_db"][u])
                carrier_state.harq_inflight = states[cid][1]["harq_inflight"][u]
                if contexts[cid]["slot"] in "DS":
                    a = 1./sched.pf_window_tti
                    carrier_state.r_avg = (1-a)*carrier_state.r_avg+a*lane.credit[u]
        payload, bursts = {}, {}
        for lane in lanes.values():
            for u,p in lane.payload.items(): payload[u] = payload.get(u,0)+p
            bursts.update(lane.bursts)
        _coalesce_events(shared, bursts, tti)
        states[pcc.carrier_id][1]["_hbt_tick_local"](payload, bursts)
        slot = contexts[pcc.carrier_id]["slot"]
        if slot in "DS":
            a = 1. / sched.pf_window_tti
            global_avg = (1-a)*global_avg + a*sum(l.credit for l in lanes.values())
            if tti >= warmup:
                hist[occupied] += 1
                counts["busy_tti"] += bool(users)
                counts["multi_ue_tti"] += len(users)>1
                counts["scheduled_ues_sum"] += len(users)
                for u in users: user_tti_counts[u] += 1
                retx_users = {a.ue for cid in chosen for a in states[cid][1]["tti_allocations"]
                              if a.harq_tx_mode=="retx" and a.original_tb_tti>=warmup}
                for u in retx_users: user_retx_tti_counts[u] += 1
                counts["mu_tti"] += any(g.mode=="MU" for p in chosen.values() for g in p.grants)
                counts["max_rbg_in_tti"] = max(counts["max_rbg_in_tti"],occupied)
        if not shared.unbounded and int(sum(sum(l.newtx) for l in lanes.values())) + int(shared.backlog_bytes) != int(sum(b0)):
            raise RuntimeError("CA per-TTI queue conservation failed")
        if tti >= warmup and slot in "DS" and kpi.tti_trace_mode != "off" and (
                kpi.tti_trace_mode == "full" or len(aggregate_trace)<kpi.tti_trace_max_points):
            allocation_rows, carrier_ids = [], []
            offset = 0
            for member in members:
                cid = member.carrier_id
                if cid in chosen:
                    for allocation in states[cid][1]["tti_allocations"]:
                        allocation_rows.append(replace(allocation,rbg_indices=tuple(i+offset for i in allocation.rbg_indices)))
                        carrier_ids.append(cid)
                offset += member.num_rbg
            row = ex._tti_trace_row(tti=tti,tti_ms=sys_cfg.tti_ms,slot=slot,snapshot=tti//contexts[pcc.carrier_id]["snap_every"],
                sample_reasons=["full" if kpi.tti_trace_mode=="full" else "ca_prefix_sample"],candidates=sorted(users),blocked_ues=0,
                allocations=allocation_rows,backlog_bytes_after=shared.backlog_bytes,pf_average_after=global_avg)
            for allocation,cid in zip(row["grants"],carrier_ids):
                allocation["carrier_id"] = cid
            aggregate_trace[tti] = row
        if len(diagnostics)<256 or kpi.tti_trace_mode == "full":
            diagnostics.append({"tti":tti,"slot":slot,"original_buffer_bits":(b0*8).tolist(),"carrier_order":order,
                "ue_order":ue_order,"split_reason":reasons,"global_pf_average_bytes":global_avg.tolist(),
                "carriers":{cid:{"split_buffer_bits":(frozen_quotas[cid]*8).tolist(),
                    "cort_scheduled_bits":(raw_tbs[cid]*8).tolist(),
                    "reserved_payload_bits":(reservations[cid]*8).tolist(),"newtx_payload_bits":(lane.newtx*8).tolist(),
                    "expanded_rbg":corrections.get(cid,0)} for cid,lane in lanes.items()}})
        if progress and (tti+1)%1000==0: progress(tti+1,sys_cfg.num_tti)
    raw = [state for stage,state in states.values() if stage=="finished"]
    if len(raw)!=len(lanes): raise RuntimeError("CA completion barrier diverged")
    merged = dict(raw[0])
    for key in _ADDITIVE: merged[key] = _sum_nested([s[key] for s in raw])
    for key in _CONCAT: merged[key] = [v for s in raw for v in s[key]]
    merged["grant_full_rbg_limits"] = [s["sys_cfg"].num_rbg for s in raw for _ in s["rbg_hist"]]
    coverage = np.zeros((raw[0]["n_snap"],n),dtype=bool)
    for cid,lane in lanes.items():
        for u,table in enumerate(bundle.tables[cid]):
            coverage[:,u] |= lane.active[u] & (np.ones(coverage.shape[0],dtype=bool)
                if table.outage is None else ~np.asarray(table.outage,dtype=bool))
    merged["ca_coverage_by_ue"] = coverage
    merged.update(counts, tr=shared, book=book, tti_occupied_rbg_counts=hist,tti_trace_rows=aggregate_trace)
    merged["olla_db"] = np.concatenate([s["olla_db"] for s in raw])
    merged["mu_olla_db"] = np.concatenate([s["mu_olla_db"] for s in raw])
    merged["harq_inflight"] = {u: {(i,h):tb for i,s in enumerate(raw) for h,tb in s["harq_inflight"][u].items()} for u in range(n)}
    sizes = tuple(v for c in members for v in c.rbg_prb_sizes)
    merged["sys_cfg"] = replace(sys_cfg,num_rbg=len(sizes),rbg_prb_sizes=sizes,rb_per_rbg=max(sizes))
    merged["resource_budget"] = replace(raw[0]["resource_budget"],num_rbg=len(sizes),rbg_prb_sizes=sizes,
        max_logical_prb=sum(s["resource_budget"].resolved_max_logical_prb for s in raw))
    merged["max_layers_used"] = max(s["max_layers_used"] for s in raw)
    run = ex._summarize_experience(merged)
    per_cc = {}
    for cid,(_,state) in states.items():
        # Reuse the summary only for per-carrier feedback/resource diagnostics;
        # shared-queue KPI remains the aggregate result above.
        local = ex._summarize_experience(state)
        per_cc[cid] = {"carrier":asdict(lanes[cid].member),"served_bytes":state["served"].tolist(),
            "system_config":state["sys_cfg"].as_dict(),
            "ue_carrier_state":[{k:v for k,v in asdict(s).items() if k not in ("harq_inflight","delay_queue")} for s in lanes[cid].states],
            "served_measured_bytes":state["served_measured"].tolist(),
            "olla_state_final":local.diagnostics["olla_state_final"],
            "olla_state_at_measurement_start":local.diagnostics["olla_state_at_measurement_start"],
            "mu_pair_graph":local.diagnostics["mu_pair_graph"],
            "coverage":{"outage_ue":local.cell["outage_ue"],"active_mask":lanes[cid].active.tolist()},
            "geo_sinr_db":[t.geo_sinr_db for t in bundle.tables[cid]],
            "grant_size_hist":local.cell["actual_rbg_size_hist"],
            "resource_ledger":local.cell["resource_ledger"],
            "radio_notes":[note for note in local.notes if "IoT" in note or "全程处于覆盖外" in note or note.startswith("Rank 策略=")],
            "rank_policy":local.diagnostics["rank_policy"],"cqi_report":local.diagnostics["cqi_report"],
            "srs_resource_assignments":local.diagnostics["srs_resource_assignments"],
            "cell_physical_diagnostics":{k:v for k,v in local.cell.items() if any(t in k for t in ("iot","cqi","olla"))},
            "allocations":[a.as_dict() for a in state["allocation_sample"]],
            "tti_trace":state["tti_trace_rows"],
            "harq_inflight":{str(u):{str(i):{"state":tb.state,"first_tti":tb.first_tti,"tbs_bytes":tb.tb_bytes} for i,tb in row.items()} for u,row in state["harq_inflight"].items()}}
        generators[cid].close()
    for key in ("olla_state_final","olla_state_at_measurement_start","rank_policy","cqi_report","srs_resource_assignments","mu_pair_graph"):
        run.diagnostics[key] = {"scope":"per_carrier","carriers":{cid:row[key] for cid,row in per_cc.items()}}
    run.cell["mu_pair_graph"] = run.diagnostics["mu_pair_graph"]
    for key in ("actual_rbg_size_hist","rbg_size_hist"):
        if run.cell[key] is not None:
            run.cell[key]["fullband_scope"] = "grant_count_weighted; each grant compared with its own carrier RBG count"
    run.cell["harq_process_scope"] = "per_UE_per_carrier"
    run.diagnostics["harq_feedback"]["process_scope"] = "per_UE_per_carrier"
    # IoT has no additive cross-frequency meaning. Keep the measured per-CC values.
    for key in list(run.cell):
        if "iot" in key or "cqi" in key:
            run.cell[key] = None
    for u,user in enumerate(run.users):
        user["carrier_grant_count"] = user["sched_tti"]
        user["carrier_retx_count"] = user["retx_tti"]
        user["sched_tti"] = int(user_tti_counts[u])
        user["retx_tti"] = int(user_retx_tti_counts[u])
        user["iot_db"] = None
        user["geo_sinr_db"] = None
        user["srs_resource_assignment"] = None
    run.diagnostics["ca"] = {"combination_identity":bundle.identity(),"per_carrier":per_cc,
        "tti_trace":diagnostics,"expanded_rbg_total":expanded_total,"global_pf_average_bytes":global_avg.tolist(),
        "causal_capacity":"reported SINR and current rank; true SINR is decoder input only",
        "cort_initial_quota":"equal serviceable-CC seed share, corrected using other-CC reserved newtx payload",
        "olla_units":"continuous MCS index"}
    run.diagnostics["ca"]["coverage"] = {"scope":"union of active-carrier coverage over link snapshots",
        "covered_by_snapshot_ue":coverage.tolist(),"outage_skips_scope":"sum of per-carrier UE/TTI skips"}
    run.diagnostics["crn_event_mapping"] = "RngBook stream + SHA256(carrier identity) namespace; fixed [TTI,UE] grid; common traffic stream"
    run.diagnostics["tti_trace"]["ca_rbg_indexing"] = {c.carrier_id:sum(m.num_rbg for m in members[:i]) for i,c in enumerate(members)}
    run.diagnostics["tti_trace"]["sampling_policy"] = "full DL trace" if kpi.tti_trace_mode=="full" else "bounded prefix of DL TTIs; diagnostic, not an unbiased sample"
    run.diagnostics["allocation_sample"] = [dict(a,carrier_id=cid) for cid,row in per_cc.items() for a in row["allocations"]]
    run.diagnostics["allocation_recent_sample"] = [dict(a.as_dict(),carrier_id=cid) for cid,(_,state) in states.items() for a in state["allocation_recent"]]
    run.notes=[note.replace("当前每 UE 上限","当前每 UE、每载波上限") for note in run.notes
               if "IoT" not in note and not note.startswith("Rank 策略=")]
    run.notes.extend(f"载波 {cid}（逐载波诊断）：{note}" for cid,row in per_cc.items() for note in row["radio_notes"])
    run.notes.append("CA: common queue, one arrival and one PF update per TTI; independent per-CC feedback. IoT remains per carrier. Aggregate RBG numbering is for resource accounting, never a cross-carrier TB.")
    run.cell = strict_json_value(run.cell)
    run.users = strict_json_value(run.users)
    run.diagnostics = strict_json_value(run.diagnostics)
    return SystemResult(config={"system":merged["sys_cfg"].as_dict(),"traffic":traffic.as_dict(),"scheduler":sched.as_dict(),
        "kpi":kpi.as_dict(),"rng":book.as_dict(),"ca":asdict(bundle.config),
        "carrier_set":[asdict(c) for c in members],"ca_combination_identity":bundle.identity()},
        cell=run.cell,users=run.users,notes=run.notes,diagnostics=run.diagnostics,elapsed_s=run.elapsed_s)
