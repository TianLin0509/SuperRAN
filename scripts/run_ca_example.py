"""Run a CA JSON request; optional synthetic contract fixtures are not channel evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def make_fixture(request):
    from superran.paths import dataset_dir
    from superran.ca import CarrierMember
    paths=[dataset_dir(row['dataset_id']) for row in request['ca_config']['carriers']]
    if any(p.exists() for p in paths):
        raise ValueError('fixture dataset already exists; reuse it without --make-fixture or use fresh IDs')
    positions=np.tile([[0.,0.,1.5],[10.,0.,1.5]],(4,1))
    for row,path in zip(request['ca_config']['carriers'],paths):
        member=CarrierMember.profile(row['carrier_id'],row['bandwidth_hz'])
        from superran.rng import RngBook
        rng=RngBook(17).namespaced_generator('channel','ca-example/'+row['carrier_id'])
        shape=(8,1,member.total_prb,8,4)
        h=(rng.normal(size=shape)+1j*rng.normal(size=shape)).astype(np.complex64)
        path.mkdir()
        np.savez(path/'channels.npz',h_true=h,h_est=h*.99,ue_position=positions,
            scalar__sinr_dB=np.full(8,18.),scalar__sir_dB=np.full(8,30.),scalar__snr_dB=np.full(8,20.),
            meta__ue_id=np.arange(8)%2,meta__serving_cell_index=np.zeros(8),
            metastr__precoding_csi_source=np.array(['ul_srs_estimate']*8))
        summary={'dataset_id':row['dataset_id'],'source':'ca_contract_fixture',
            'notice':'Synthetic input to exercise loader/CSI contracts; not a simulated or measured SRS performance dataset.',
            'shape':{'N':8,'T':1,'RB':member.total_prb,'BS_ant':8,'UE_ant':4},'num_samples':8,'cells_configured':1,
            'antenna_model':{'mode':'effective_subarray','port_order':'pol_h_v','vertical_index_order':'top_to_bottom'},
            'config':{'bandwidth_hz':member.bandwidth_hz,'subcarrier_spacing':30000.,'num_rb':member.total_prb,'num_ues':2,
                'srs_periodicity':20,'csirs_periodicity':20,'mobility_mode':'static','scenario':'UMa_NLOS','ue_speed_kmh':3.}}
        (path/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request',type=Path)
    parser.add_argument('--make-fixture',action='store_true',help='explicitly create synthetic functional-test datasets')
    parser.add_argument('--output',type=Path,default=ROOT/'output'/'ca-example.result.json')
    args=parser.parse_args()
    request=json.loads(args.request.read_text(encoding='utf-8'))
    if args.make_fixture: make_fixture(request)
    from superran.server import sr_system_sim
    from superran.ca import strict_json_value
    result=sr_system_sim(**request)
    if 'error' in result: raise RuntimeError(result['error'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(strict_json_value(result),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    json.loads(args.output.read_text(encoding='utf-8'))
    print(args.output.resolve())
    print(result['kpi_view'])


if __name__=='__main__':main()
