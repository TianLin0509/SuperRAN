"""CA behavioral acceptance; python tests/test_ca.py or pytest tests/test_ca.py."""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import json
import os
import sys
import tempfile
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from superran import system as s, ca, ca_engine as ce, experience as ex
from superran.carrier import CarrierGrid


def raises(call):
    try: call()
    except (ValueError,TypeError): return
    raise AssertionError("invalid CA input was accepted")


@lru_cache(None)
def fixture(n_cc=2, mu=False):
    members = [ca.CarrierMember.profile('pcc',100e6,is_pcc=True),
               ca.CarrierMember.profile('scc',20e6),ca.CarrierMember.profile('third',100e6)][:n_cc]
    carriers=ca.CarrierSet(tuple(members))
    rng=np.random.default_rng(4)
    inputs={m.carrier_id:dict(h_users=[rng.normal(size=(2,m.total_prb,4,2))+1j*rng.normal(size=(2,m.total_prb,4,2)) for _ in range(2)],
        geo_sinr_db=[15.,20.],power_constraint='nebf',num_snapshots=2,mu_enabled=mu,csi=s.ca.CsiConfig(enabled=False),
        geometry={'ue_positions':[[0,0,1.5],[10,0,1.5]],'snapshot_times_ms':[0,5]}) for m in members}
    return ca.build_ca_link_tables(carriers,inputs,config=ca.CaSchedulerConfig(mode='independent',split_threshold_bits=0))


def run(bundle=None, mode='independent', model='ftp3', mu=False, **kwargs):
    b=deepcopy(bundle or fixture(mu=mu))
    b.config=replace(b.config,mode=mode)
    return s.simulate(b,sys_cfg=kwargs.pop('sys_cfg',s.SystemConfig(duration_s=.03,seed=0)),
        traffic=s.TrafficConfig(model=model,file_bytes=9001,arrival_rate_hz=300),
        sched=kwargs.pop('sched',s.SchedulerConfig(mu_enabled=mu)),
        kpi=kwargs.pop('kpi',s.KpiConfig(warmup_s=0,tti_trace_mode='full')),**kwargs)


def test_contracts_and_integer_splits():
    c=fixture().carriers
    cfg=ca.CaSchedulerConfig(mode='independent')
    caps={'pcc':2.,'scc':1.}
    assert ca.split_buffer(9000,c,cfg,capacities=caps)[0]=={'pcc':9000,'scc':0}
    assert ca.split_buffer(9001,c,cfg,capacities=caps)[0]=={'pcc':4501,'scc':4500}
    assert ca.split_buffer(9003,c,replace(cfg,method='bandwidth'),capacities=caps)[0]=={'pcc':6002,'scc':3001}
    assert ca.split_buffer(9001,c,cfg,average_bytes=10000,capacities=caps)[0]['scc']==0
    assert ca.split_buffer(9001,c,replace(cfg,split_threshold_bits=0),average_bytes=10000,capacities=caps)[0]['scc']==4500
    assert ca.split_buffer(100,c,replace(cfg,method='rbnum'),capacities={'pcc':1,'scc':3})[0]['scc']==100
    assert ca.split_buffer(100,c,cfg,capacities={'pcc':0,'scc':3})[1]=='pcc_unavailable'
    assert ca.split_buffer(100,c,cfg,capacities={})[1]=='no_service'
    p20=ca.CarrierSet((replace(c.pcc,is_pcc=False),replace(c.scc_list[0],is_pcc=True)))
    assert ca.split_buffer(1800,p20,cfg,capacities=caps)[0]['scc']==1800
    assert ca.split_buffer(1801,p20,cfg,capacities=caps)[1]=='split'
    assert c.scc_list[0].total_prb==51 and c.scc_list[0].rbg_prb_sizes[-1]==3
    raises(lambda: CarrierGrid.company_tdd({'bandwidth_hz':20e6,'subcarrier_spacing':30000},num_rb=51))
    for kw in ({'mode':'bad'},{'pcc_delay_tti':1},{'scc_delay_tti':1},{'scell_activation_enabled':True},{'scell_activation_thld_bps':1},{'split_threshold_bits':False}):
        raises(lambda kw=kw:ca.CaSchedulerConfig(**kw))
    for members in ((c.pcc,c.pcc),(replace(c.pcc,is_pcc=False),),
                    (c.pcc,replace(c.scc_list[0],cell_id=1)),(c.pcc,replace(c.scc_list[0],scs_khz=15))):
        raises(lambda members=members:ca.CarrierSet(members))


def test_input_identity_rejections():
    for mutate in (
        lambda b:b.tables.pop('scc'),
        lambda b:b.tables['scc'].reverse(),
        lambda b:setattr(b.tables['scc'][0],'sinr_tx_rbg_db',None),
        lambda b:setattr(b.tables['scc'][0],'sinr_tx_rbg_db',np.zeros((2,2,8))),
        lambda b:setattr(b.tables['scc'][0],'serving_cell_index',1),
        lambda b:b.source_manifest['scc']['identity'].update(snapshot_times_ms=[0,10]),
        lambda b:setattr(b,'active_mask',{'pcc':(True,True),'scc':(True,)}),
    ):
        b=deepcopy(fixture());mutate(b)
        raises(lambda:run(b))


def test_build_preserves_source_and_power_identity():
    carriers=fixture().carriers
    rng=np.random.default_rng(72)
    inputs={m.carrier_id:dict(h_users=[rng.normal(size=(2,m.total_prb,4,2))+1j*rng.normal(size=(2,m.total_prb,4,2))],
        geo_sinr_db=[18.],num_snapshots=2,power_constraint='nebf',csi=s.ca.CsiConfig(enabled=False),
        geometry={'ue_positions':[[0,0,1.5]],'snapshot_times_ms':[0,5]}) for m in carriers.members}
    source={m.carrier_id:{'dataset_id':'source_'+m.carrier_id,'config':{'center_frequency_hz':3.5e9,'tx_power_dbm':46.}} for m in carriers.members}
    baseline=ca.build_ca_link_tables(carriers,inputs,source_manifest=source)
    assert baseline.source_manifest['scc']['dataset_id']=='source_scc'
    assert baseline.source_manifest['pcc']['link_build_parameters']['power_constraint']=='nebf'
    source['scc']['config']['tx_power_dbm']=43.
    changed=ca.build_ca_link_tables(carriers,inputs,source_manifest=source)
    assert changed.identity()!=baseline.identity(), 'source power provenance was discarded'
    assert baseline.source_manifest['scc']['config']['tx_power_dbm']==46., 'caller mutated an existing source contract'


def test_single_cc_bit_compatibility_and_pcc_identity():
    b=deepcopy(fixture(1))
    cfg=s.SystemConfig(duration_s=.03,seed=0)
    tc=s.TrafficConfig(model='ftp3',file_bytes=9001,arrival_rate_hz=300)
    kc=s.KpiConfig(warmup_s=0,tti_trace_mode='full')
    old=s.simulate(b.tables['pcc'],sys_cfg=cfg,traffic=tc,kpi=kc)
    for mode in ('off','independent','cort'):
        result=run(b,mode=mode)
        for name in ('cell','users','diagnostics','config','notes'):
            assert json.dumps(ca.strict_json_value(getattr(old,name)),sort_keys=True)==json.dumps(ca.strict_json_value(getattr(result,name)),sort_keys=True)
    b=deepcopy(fixture())
    b.carriers=ca.CarrierSet(tuple(reversed(b.carriers.members)))
    off=run(b,mode='off')
    assert ca.strict_json_value(off.cell)==ca.strict_json_value(old.cell)
    assert any('unused carriers: scc' in n for n in off.notes)


def check_water(result):
    trace=result.diagnostics['ca']['tti_trace']
    average=np.full(2,1e-6)
    for row in trace:
        water=np.array(row['original_buffer_bits'])
        reserved=sum(np.array(c['reserved_payload_bits']) for c in row['carriers'].values())
        sent=sum(np.array(c['newtx_payload_bits']) for c in row['carriers'].values())
        assert np.all(sent<=reserved) and np.all(reserved<=water)
        if result.config['ca']['mode']=='independent':
            assert all(np.all(np.array(c['newtx_payload_bits'])<=c['split_buffer_bits']) for c in row['carriers'].values())
        assert all(all(x%8==0 for x in c['newtx_payload_bits']) for c in row['carriers'].values())
        if row['slot']=='U':assert not np.any(sent)
        if row['slot'] in 'DS':
            credit=sum(np.array(c['cort_scheduled_bits'])/8 for c in row['carriers'].values())
            average=.99*average+.01*credit
        np.testing.assert_allclose(row['global_pf_average_bytes'],average,rtol=0,atol=1e-9)
    b=result.diagnostics['byte_conservation']
    if b['arrived'] is not None: assert b['arrived']==b['acked']+b['queued']
    assert result.diagnostics['measurement_window']['balance_error_bytes'] in (None,0)
    assert len(result.users)==2
    assert all(u['sched_tti']<=len(trace) for u in result.users)


def test_queue_kpi_harq_and_partial_prb():
    for n_cc in (2,3):
        for mode in ('independent','cort'):
            for model in ('ftp3','full_buffer'):
                result=run(fixture(n_cc),mode=mode,model=model,
                    kpi=s.KpiConfig(warmup_s=.005,tti_trace_mode='full'))
                check_water(result)
                if model=='full_buffer':
                    assert result.cell['cell_experienced_mbps'] is None
                per=result.diagnostics['ca']['per_carrier']
                assert sum(sum(v['served_measured_bytes']) for v in per.values())==result.diagnostics['measurement_window']['acked_bytes']
                for cid,cc in per.items():
                    grants=cc['allocations']
                    for a in grants:
                        assert a['n_prb']==sum(cc['carrier']['rbg_prb_sizes'][i] for i in a['rbg_indices'])
                    for u in range(2):
                        keys=[a['tti'] for a in grants if a['ue']==u]
                        assert len(keys)==len(set(keys)), 'one grant per UE/CC/TTI'


def test_static_mask_and_order_independence():
    b=deepcopy(fixture())
    baseline=run(b,mode='cort')
    b.tables=dict(reversed(list(b.tables.items())))
    reordered=run(b,mode='cort')
    assert baseline.diagnostics['ca']==reordered.diagnostics['ca']
    b.active_mask={'pcc':(True,True),'scc':(False,False)}
    masked=run(b)
    assert not masked.diagnostics['ca']['per_carrier']['scc']['allocations']
    assert all(c['split_buffer_bits']==[0,0] for t in masked.diagnostics['ca']['tti_trace'] for cid,c in t['carriers'].items() if cid=='scc')
    b.active_mask={'pcc':(False,False),'scc':(False,False)}
    idle=run(b)
    assert idle.cell['cell_served_mbps']==0 and idle.cell['backlog_bytes']>0


def test_same_tti_busy_period_event_aggregation():
    summarize=ex._summarize_experience
    observed=[]
    def capture(state):
        if not isinstance(state['book'],ce._CarrierBook):
            for q in state['tr'].queues:
                for burst in q.done:
                    ttis=[e.tti for e in burst.tx_events]
                    assert len(ttis)==len(set(ttis))
                    assert burst.tx_attempts==len(ttis)
                    assert sum(e.payload_bytes for e in burst.tx_events)==burst.bytes_sent
                    observed.append(burst)
        return summarize(state)
    with patch.object(ex,'_summarize_experience',capture):
        result=run(mode='cort')
    assert observed and any(len(b.tx_events)==1 for b in observed)
    first=result.diagnostics['ca']['tti_trace'][0]
    assert all(c['newtx_payload_bits'][1]>0 for c in first['carriers'].values())


def test_cort_expansion_counterfactual():
    original=ce.correct_cort_plan
    good=run(mode='cort')
    with patch.object(ce,'correct_cort_plan',lambda context,targets:original(context,targets,expand=False)):
        reverted=run(mode='cort')
    assert good.diagnostics['ca']['expanded_rbg_total']>0
    assert reverted.diagnostics['ca']['expanded_rbg_total']==0
    good_trace=good.diagnostics['ca']['tti_trace'];bad_trace=reverted.diagnostics['ca']['tti_trace']
    improved=[(g,b) for g,b in zip(good_trace,bad_trace) if g['original_buffer_bits']==b['original_buffer_bits'] and
              sum(sum(v['newtx_payload_bits']) for v in g['carriers'].values()) > sum(sum(v['newtx_payload_bits']) for v in b['carriers'].values())]
    assert improved,'disabled expansion must lose actual same-TTI payload, not just a diagnostic flag'
    assert any(sum(v['reserved_payload_bits'])>0 for g,_ in improved for cid,v in g['carriers'].items() if cid=='scc')
    check_water(good);check_water(reverted)


def assert_cort_first_tti_drains_original_water():
    """Ratchet: disabling expansion must fail on bytes, not API existence."""
    result=run(mode='cort')
    first=result.diagnostics['ca']['tti_trace'][0]
    arrived=sum(first['original_buffer_bits'])
    sent=sum(sum(c['newtx_payload_bits']) for c in first['carriers'].values())
    assert arrived==216024, 'fixture identity changed; re-establish the physical counterexample'
    assert first['carriers']['scc']['reserved_payload_bits'][0]>0
    assert sent==arrived, f'CORT did not expand into free RBGs: {sent}/{arrived} bits sent'


def test_rng_feedback_isolation_and_causal_prediction():
    from superran import rng as rg
    before=rg.RngBook(99).as_dict()
    book=rg.RngBook(99)
    x=book.namespaced_generator('harq','ca/pcc').random((10,2))
    y=book.namespaced_generator('harq','ca/scc').random((10,2))
    assert not np.array_equal(x,y)
    assert np.array_equal(x,book.namespaced_generator('harq','ca/pcc').random((10,2)))
    a=run(mode='cort')
    assert before==rg.RngBook(99).as_dict()
    b=deepcopy(fixture())
    for t in b.tables['scc']:
        t.sinr_rbg_db=np.full_like(t.sinr_rbg_db,-20)
        t.sinr_db=np.full_like(t.sinr_db,-20)
        t.best_se=np.full_like(t.best_se,1e6)
    degraded=run(b,mode='cort')
    # Before feedback, oracle truth must not affect quotas/order/final grant sizes.
    for cid in ('pcc','scc'):
        for key in ('split_buffer_bits','reserved_payload_bits','cort_scheduled_bits'):
            assert a.diagnostics['ca']['tti_trace'][0]['carriers'][cid][key]==degraded.diagnostics['ca']['tti_trace'][0]['carriers'][cid][key]
    per=degraded.diagnostics['ca']['per_carrier']
    assert per['pcc']['olla_state_final']['su_mcs']!=per['scc']['olla_state_final']['su_mcs']
    retx=[(cid,t,g) for cid,c in per.items() for t in c['tti_trace'].values() for g in t['grants'] if g['harq_tx_mode']=='retx']
    assert retx
    for cid,t,g in retx:
        row=degraded.diagnostics['ca']['tti_trace'][t['tti']]
        assert row['carriers'][cid]['newtx_payload_bits'][g['ue']]==0
    assert_cort_first_tti_drains_original_water()


def assert_ca_ready_retx_keeps_priority():
    """Competing UEs: verify actual TX, not just the candidate ordering."""
    b=deepcopy(fixture())
    for table in b.tables['pcc']:
        table.sinr_rbg_db[:]=-40
        table.sinr_db[:]=-40
    original=ce.correct_cort_plan
    ready=[]
    def capture(context,targets):
        pending=context['harq_pending']
        if pending:
            ready.append((context['lane'].member.carrier_id,context['tti'],
                context['sys_cfg'].num_rbg,
                [(u,tb.first_tti,tb.n_rbg) for u,tb in sorted(pending.items(),
                    key=lambda item:(item[1].first_tti,context['ordered_users'].index(item[0])))],
                list(context['ordered_users'])))
        return original(context,targets)
    with patch.object(ce,'correct_cort_plan',capture):
        result=run(b,mode='cort',model='full_buffer',sys_cfg=s.SystemConfig(duration_s=.06,seed=0))
    checked=0
    for cid,tti,available,pending,order in ready:
        grants=result.diagnostics['ca']['per_carrier'][cid]['tti_trace'][str(tti)]['grants']
        sent={(g['ue'],g['original_tb_tti']) for g in grants if g['harq_tx_mode']=='retx'}
        for u,first,size in pending:
            if size<=available:
                assert (u,first) in sent, f'{cid} TTI {tti}: ready TB from {first} was displaced by new TX'
                available-=size
                checked+=1
        assert order[:len(pending)]==[u for u,_,_ in pending], 'ready HARQ order must follow first_tti (ties retain local order)'
    assert checked>10, 'fixture must exercise repeated cross-UE HARQ competition'


def test_ready_retx_priority():
    assert_ca_ready_retx_keeps_priority()


def assert_ca_runtime_table_identity_is_complete():
    b=deepcopy(fixture(mu=True))
    identity=b.identity()
    reordered=deepcopy(b)
    reordered.tables=dict(reversed(list(reordered.tables.items())))
    for table in reordered.tables['scc']:
        table.mu_links=dict(reversed(list(table.mu_links.items())))
        table.mu_links_by_rank=dict(reversed(list(table.mu_links_by_rank.items())))
    assert reordered.identity()==identity
    mutations=[
        lambda t:setattr(t,'outage',np.ones(t.sinr_db.shape[0],dtype=bool)),
        lambda t:setattr(t,'geo_sinr_db',t.geo_sinr_db+1),
        lambda t:setattr(t,'cqi_filter_lambda',t.cqi_filter_lambda/2),
        lambda t:next(iter(t.mu_links.values())).true_sinr_rbg_db.fill(-40),
        lambda t:next(iter(t.mu_links.values())).predicted_sinr_rbg_db.fill(-40),
        lambda t:next(iter(t.mu_links.values())).correlation.fill(.999),
        lambda t:next(iter(next(iter(t.mu_links_by_rank.values())).values())).corr_loss_tx_rbg_db.fill(17),
        lambda t:t.mu_links.clear(),
    ]
    for index,mutate in enumerate(mutations):
        changed=deepcopy(b);mutate(changed.tables['scc'][0])
        assert changed.identity()!=identity,f'changed runtime input {index} retained the same CA identity'
    changed=deepcopy(b)
    for table in changed.tables['scc']:
        for pair in table.mu_links.values():
            pair.true_sinr_db[:]=-40;pair.true_sinr_rbg_db[:]=-40
    from superran import kpi_compare
    with tempfile.TemporaryDirectory(prefix='superran-ca-identity-') as root,patch.dict(os.environ,{'SUPERRAN_ARTIFACTS':root}):
        ids=[]
        for label,bundle,mode in (('independent',b,'independent'),('cort',b,'cort'),('changed',changed,'cort')):
            bundle=deepcopy(bundle);bundle.config=replace(bundle.config,mode=mode)
            result=s.simulate_replications(bundle,num_replications=2,master_seed=0,
                sys_cfg=s.SystemConfig(duration_s=.015),traffic=s.TrafficConfig(model='full_buffer'),
                sched=s.SchedulerConfig(mu_enabled=True),kpi=s.KpiConfig(warmup_s=0)).as_dict()
            result['algorithm']={'label':label}
            path=kpi_compare._result_path(label);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(ca.strict_json_value(result),allow_nan=False),encoding='utf-8')
            ids.append(label)
        kpi_compare.build_comparison(ids[:2],primary_kpi='cell_served_mbps')
        try:kpi_compare.build_comparison([ids[0],ids[2]],primary_kpi='cell_served_mbps')
        except ValueError as exc:assert 'ca_combination_identity' in str(exc),str(exc)
        else:raise AssertionError('comparison accepted different MU decoder inputs')


def test_complete_runtime_table_identity():
    assert_ca_runtime_table_identity_is_complete()


def test_mu_and_replications():
    result=run(fixture(mu=True),mode='cort',model='full_buffer',mu=True)
    check_water(result)
    assert any(a['transmission_mode']=='MU' for c in result.diagnostics['ca']['per_carrier'].values() for a in c['allocations'])
    b=deepcopy(fixture())
    r=s.simulate_replications(b,num_replications=2,master_seed=8,sys_cfg=s.SystemConfig(duration_s=.01),
        traffic=s.TrafficConfig(model='ftp3',file_bytes=9001,arrival_rate_hz=100),kpi=s.KpiConfig(warmup_s=0))
    json.dumps(ca.strict_json_value(r.as_dict()),allow_nan=False)
    assert len(r.runs)==2 and r.books[0]!=r.books[1]
    assert r.config['ca_combination_identity']==b.identity()


def test_mcp_real_dataset_route():
    from superran import server
    from superran.paths import datasets_dir
    with tempfile.TemporaryDirectory(prefix='superran-ca-') as root, patch.dict(os.environ,{'SUPERRAN_ARTIFACTS':root,'SUPERRAN_NO_BROWSER':'1'}):
        common=np.tile([[0.,0.,1.5],[10.,0.,1.5]],(4,1))
        rows=[]
        for i,(cid,bw,nrb) in enumerate((('pcc',100e6,272),('scc',20e6,51))):
            dsid='ds_ca_accept_'+cid
            d=datasets_dir()/dsid;d.mkdir()
            rng=np.random.default_rng(40+i)
            h=(rng.normal(size=(8,1,nrb,8,4))+1j*rng.normal(size=(8,1,nrb,8,4))).astype(np.complex64)
            np.savez(d/'channels.npz',h_true=h,h_est=h*.99,ue_position=common,
                scalar__sinr_dB=np.full(8,18.),scalar__sir_dB=np.full(8,30.),scalar__snr_dB=np.full(8,20.),
                meta__ue_id=np.arange(8)%2,meta__serving_cell_index=np.zeros(8),
                metastr__precoding_csi_source=np.array(['ul_srs_estimate']*8))
            summary={'dataset_id':dsid,'source':'internal_sim','shape':{'N':8,'T':1,'RB':nrb,'BS_ant':8,'UE_ant':4},'num_samples':8,'cells_configured':1,
                'antenna_model':{'mode':'effective_subarray','port_order':'pol_h_v','vertical_index_order':'top_to_bottom'},
                'config':{'bandwidth_hz':bw,'subcarrier_spacing':30000.,'num_rb':nrb,'num_ues':2,'srs_periodicity':20,'csirs_periodicity':20,
                          'mobility_mode':'static','scenario':'UMa_NLOS','ue_speed_kmh':3.}}
            (d/'summary.json').write_text(json.dumps(summary),encoding='utf-8')
            rows.append({'carrier_id':cid,'dataset_id':dsid,'bandwidth_hz':bw,'is_pcc':i==0,'csi':{'srs_hopping':False}})
        kwargs=dict(dataset_id=rows[0]['dataset_id'],duration_s=.02,warmup_s=0,num_replications=2,ca_config={'carriers':rows,'scheduler':{'mode':'cort'}})
        result=server.sr_system_sim(**kwargs)
        assert 'error' not in result,result
        json.dumps(result,allow_nan=False)
        assert result['ca']['replication_diagnostics'][0]['combination_identity']==result['ca']['combination_identity']
        assert 'error' not in result['kpi_view'],result['kpi_view']
        from superran import kpi_compare
        result_id=result['kpi_view']['result_id']
        changed=deepcopy(result)
        changed['algorithm']['label']='changed carrier source'
        changed['config']['ca_combination_identity']='different-carrier-combination'
        changed_id='ca_mismatch'
        kpi_compare._result_path(changed_id).write_text(json.dumps(changed,allow_nan=False),encoding='utf-8')
        try:
            kpi_compare.build_comparison([result_id,changed_id],primary_kpi='cell_served_mbps')
        except ValueError as exc:
            assert 'ca_combination_identity' in str(exc),str(exc)
        else:raise AssertionError('different carrier combinations passed comparison pairing')
        old=server.sr_system_sim(dataset_id=rows[1]['dataset_id'],duration_s=.02,warmup_s=0,num_replications=1)
        assert 'error' in old
        bad=deepcopy(kwargs);bad['ca_config']['carriers'][1]['csi']={}
        assert 'error' in server.sr_system_sim(**bad)
        from superran.ca_server import active_preparation
        assert active_preparation.get() is None


if __name__=='__main__':
    failed=[]
    for name,test in list(globals().items()):
        if name.startswith('test_') and callable(test):
            try:test();print('PASS',name,flush=True)
            except Exception:
                import traceback
                traceback.print_exc();failed.append(name)
    print(f'CA acceptance: {len([n for n in globals() if n.startswith("test_")])-len(failed)} passed; {len(failed)} failed')
    raise SystemExit(bool(failed))
