#!/usr/bin/env python3
"""Observational composition checks and a matched 500-epoch duration probe."""

import copy
import csv
import hashlib
import json
import statistics
from pathlib import Path

import numpy as np

from run_experiments import ROOT
from adaptive_estimator_audit import audit_run

SOURCE = ROOT/'results/processed/trail_four_mechanisms_10seeds_v2/source_runs.csv'
DESTINATION = ROOT/'results/processed/trail_fee_only_duration_500_v2'
OFFLINE = ROOT/'results/diagnostics/fee_only_high_fee_10seeds'
SEEDS, FEES = [203,209], [.005,.01]


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def save_csv(path,rows):
    with path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def originals():
    cases={(int(r['seed']),float(r['transaction_fee'])):r for r in read_csv(SOURCE)
           if r['protocol']=='trail_eta0' and float(r['cost_multiplier'])==5
           and int(r['seed']) in SEEDS and float(r['transaction_fee']) in FEES}
    assert set(cases)=={(s,f) for s in SEEDS for f in FEES}
    return cases


def panel(raw):
    config=json.loads((raw/'run_config.json').read_text())['config']
    n,h=config['node_num'],config['max_epochs']
    assert h%5==0 and config['reward_reinvestment_rate']==0
    assert config['trail_config']['eta']==0 and config['trail_config']['proposer_fee_ratio']==.25
    active=np.zeros((n,h//5));reward=np.zeros_like(active);work=np.zeros_like(active)
    counts=np.zeros_like(active,dtype=int)
    stakes=np.zeros(n);costs=np.zeros(n);degrees=np.zeros(n)
    for row in read_csv(raw/'node_epoch_metrics.csv'):
        i,e=int(row['validator_id']),int(row['epoch']);t=e//5
        a=int(row['relay_profile']=='active')
        if counts[i,t]:
            assert active[i,t]==a
        active[i,t]=a
        counts[i,t]+=1
        reward[i,t]+=float(row['relay_reward'])
        work[i,t]+=int(row['relay_forward_attempts'])
        if e==0:
            stakes[i]=float(row['economic_stake'])
            costs[i]=float(row['relay_cost_per_forward'])
            degrees[i]=int(row['degree'])
    assert np.all(counts==5) and np.all(stakes>0)
    return dict(config=config,active=active,reward=reward,work=work,
                stakes=stakes,costs=costs,degrees=degrees)


def composition(data,start,end,verify=False):
    """Stake-weighted two-way FE association; not a causal treatment effect."""
    assert start%5==end%5==0
    indices=slice(start//5,end//5)
    a=data['active'][:,indices];r=data['reward'][:,indices];work=data['work'][:,indices]
    stake=data['stakes'];w=stake/stake.sum();exposure=5*stake[:,None]
    ra,rl=(r*a).sum(),(r*(1-a)).sum()
    sa,sl=(exposure*a).sum(),(exposure*(1-a)).sum()
    dr=ra/sa-rl/sl if sa>0 and sl>0 else None
    dw=(work*a).sum()/sa-(work*(1-a)).sum()/sl if sa>0 and sl>0 else None
    pooled=dr/dw if dw is not None and dw>0 else None
    def demean(v):
        return v-v.mean(axis=1,keepdims=True)-(w[:,None]*v).sum(axis=0,keepdims=True)+(w[:,None]*v).sum()/v.shape[1]
    x=demean(a);y=demean(r/exposure);z=demean(work/exposure)
    denominator=(w[:,None]*x*x).sum()
    br=bw=within=None
    if denominator>1e-18:
        br=float((w[:,None]*x*y).sum()/denominator)
        bw=float((w[:,None]*x*z).sum()/denominator)
        within=br/bw if bw>0 else None
        if verify:
            # Independently fit explicit weighted node/time dummies.
            n,t=a.shape
            design=np.column_stack([np.ones(n*t),np.repeat(np.eye(n)[:,1:],t,axis=0),
                                    np.tile(np.eye(t)[:,1:],(n,1)),a.ravel()])
            scale=np.repeat(np.sqrt(w),t)
            coefficients=np.linalg.lstsq(design*scale[:,None],
                np.column_stack([(r/exposure).ravel(),(work/exposure).ravel()])*scale[:,None],rcond=None)[0]
            assert np.allclose(coefficients[-1],[br,bw],rtol=1e-8,atol=1e-10)
    return dict(start_epoch=start,end_epoch_inclusive=end-1,
        pooled_relay_reward_gap_per_stake=None if dr is None else float(dr),
        pooled_forward_gap_per_stake=None if dw is None else float(dw),
        pooled_benefit_per_forward=None if pooled is None else float(pooled),
        within_node_window_reward_effect=br,within_node_window_work_effect=bw,
        within_node_window_benefit_per_forward=within,
        switching_nodes=int(np.sum(np.ptp(a,axis=1)>0)),observations=int(a.size))


def prepare():
    OFFLINE.mkdir(parents=True,exist_ok=True)
    records=[]
    for (seed,fee),case in originals().items():
        data=panel(ROOT/case['raw_source'])
        for start,end in [(0,125),(0,200)]:
            result=composition(data,start,end,verify=seed==209 and fee==.01 and end==125)
            records.append(dict(seed=seed,fee=fee,raw_source=case['raw_source'],**result))
    save_csv(OFFLINE/'composition_checks.csv',records)
    (OFFLINE/'composition_method.json').write_text(json.dumps(dict(
        source_csv=str(SOURCE.relative_to(ROOT)),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        method='Balanced five-epoch validator panels. Outcomes are relay reward and forwarding attempts per stake exposure. Compare pooled Active/Lazy differences with stake-weighted node and window fixed-effect associations; divide the adjusted reward coefficient by the adjusted work coefficient.',
        expected_proposer_component='With eta=0 and constant economic stake, expected proposer reward per stake is common within a window and cancels; window effects also absorb it.',
        verification='For seed209/F=0.01/epochs0-124, within transformations agree with an independent explicit weighted dummy-variable least-squares fit.',
        limitations='Observational sensitivity analysis only. Strategies depend on past rewards; network spillovers remain. Fixed effects do not identify counterfactual rewards for nodes that never switch. No causal claim and no replacement of the production estimator or main figures.'),indent=2)+'\n')
    for row in records:
        if row['seed']==209:
            print(json.dumps(row))


def compare_inputs(old,new):
    def normalize(config):
        c=copy.deepcopy(config)
        for field in ['max_epochs','run_id','output_dir','metrics_prefix']:
            c.pop(field,None)
        return c
    assert normalize(old['config'])==normalize(new['config'])
    for field in ['stakes','costs','degrees']:
        assert np.array_equal(old[field],new[field]),field
    assert np.array_equal(old['active'][:,0],new['active'][:,0])


def compare_prefix(old,new):
    assert len(old)==200 and len(new)==500
    differences=[abs(float(a['active_stake_share'])-float(b['active_stake_share'])) for a,b in zip(old,new)]
    first=next((i for i,v in enumerate(differences) if v>1e-8),None)
    return dict(first_active_stake_divergence_epoch=first,
        first199_epochs_reproduced=all(v<=1e-8 for v in differences[:199]),
        all200_epochs_reproduced=first is None,
        max_prefix_active_stake_difference_pp=100*max(differences))


def draw(trajectories,old_trajectories,destination):
    import os
    os.environ.setdefault('MPLCONFIGDIR','/tmp/trail-fee-only-duration')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'serif','font.serif':['Times New Roman','Nimbus Roman','DejaVu Serif'],
                         'mathtext.fontset':'stix','font.size':9,'legend.fontsize':8,'pdf.fonttype':42})
    fig,axes=plt.subplots(1,2,figsize=(7.6,3.6))
    fig.subplots_adjust(left=.075,right=.985,bottom=.25,top=.88,wspace=.25)
    for ax,seed in zip(axes,SEEDS):
        for fee,color,marker in [(.005,'#0072B2','s'),(.01,'#D55E00','o')]:
            old=old_trajectories[(seed,fee)];new=trajectories[(seed,fee)]
            ax.plot([int(r['epoch']) for r in old],[100*float(r['active_stake_share']) for r in old],
                    color=color,linestyle=':',alpha=.35,linewidth=1)
            ax.plot([int(r['epoch']) for r in new],[100*float(r['active_stake_share']) for r in new],
                    color=color,marker=marker,markevery=50,markerfacecolor='white',markersize=3.5,
                    linewidth=1.2,label=f'F={fee:g}')
        for start,end in [(100,200),(400,500)]:
            ax.axvspan(start,end,color='#888888',alpha=.08,zorder=0)
        ax.set(title=f'Fee-only, seed {seed}',xlabel='Epoch',ylabel='Active relay stake (%)',
               xlim=(0,500),ylim=(0,100),xticks=[0,100,200,300,400,500])
        ax.grid(axis='y',color='#E6E6E6',linewidth=.7)
        ax.spines[['top','right']].set_visible(False)
        ax.legend(loc='best',framealpha=.9)
    fig.text(.5,.035,'Solid: new 500-epoch runs; faint dotted: original 200-epoch runs.\n'
             'Gray bands: epochs 100-199 and 400-499. Cost=5x; theta=0.25; g=0.0001.\n'
             'Selected diagnostic seeds only; prefix reproduction is checked, not assumed.',ha='center',fontsize=7.5)
    for extension in ['png','pdf']:
        fig.savefig(destination/f'convergence.{extension}',dpi=300)
    plt.close(fig)


def complete():
    source=ROOT/'results/processed/trail_fee_only_duration_500_v2_cost5/runs.csv'
    runs={(int(r['seed']),float(r['transaction_fee'])):r for r in read_csv(source)}
    refs=originals()
    assert set(runs)==set(refs)
    DESTINATION.mkdir(parents=True,exist_ok=True)
    windows=[];prefixes=[];effects=[];audits=[];trajectories={};old_trajectories={}
    for key,run in sorted(runs.items()):
        seed,fee=key;raw=ROOT/run['raw_source'];reference=ROOT/refs[key]['raw_source']
        data=panel(raw);old_data=panel(reference)
        assert data['config']['max_epochs']==500 and data['config']['warmup_epochs']==100
        compare_inputs(old_data,data)
        edges=lambda path:sorted(tuple(sorted(e)) for e in json.loads((path/'graph.json').read_text()))
        assert edges(raw)==edges(reference)
        audits.append(audit_run(raw))
        new=read_csv(raw/'adaptive_relay_metrics.csv');old=read_csv(reference/'adaptive_relay_metrics.csv')
        trajectories[key],old_trajectories[key]=new,old
        prefixes.append(dict(seed=seed,fee=fee,**compare_prefix(old,new)))
        for start,end in [(100,200),(200,300),(300,400),(400,500)]:
            sample=new[start:end];values=[100*float(r['active_stake_share']) for r in sample]
            half=len(values)//2
            windows.append(dict(seed=seed,fee=fee,start_epoch=start,end_epoch_inclusive=end-1,
                active_stake_mean_pct=statistics.mean(values),minimum_pct=min(values),maximum_pct=max(values),
                second50_minus_first50_pp=statistics.mean(values[half:])-statistics.mean(values[:half]),
                negative_decision_windows=sum(bool(r['decision_benefit_per_forward']) and float(r['decision_benefit_per_forward'])<0 for r in sample),
                final_active_stake_pct=values[-1],raw_source=run['raw_source']))
        for start,end in [(0,125),(0,200),(400,500)]:
            effects.append(dict(seed=seed,fee=fee,**composition(data,start,end)))
    save_csv(DESTINATION/'windows.csv',windows)
    save_csv(DESTINATION/'prefix_comparison.csv',prefixes)
    save_csv(DESTINATION/'composition_checks.csv',effects)
    draw(trajectories,old_trajectories,DESTINATION)
    reproduced=all(r['first199_epochs_reproduced'] for r in prefixes)
    summary=dict(runs=4,max_epochs=500,seeds=SEEDS,fees=FEES,cost_multiplier=5,
        binary_sha256=hashlib.sha256((ROOT/'target/release/trail').read_bytes()).hexdigest(),
        new_source_csv_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        reference_source_csv_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        prefix_reproduced_first199_all=reproduced,prefix_checks=prefixes,estimator_checks=audits,
        interpretation=('The original first199-epoch active-stake prefixes reproduced; compare the subsequent windows and tail drift.' if reproduced else
                        'At least one original prefix did not reproduce. These are same-configuration long-horizon reruns, not checkpoint continuations. Interpret within-run recovery and tail drift; horizon-only causality is not established.'),
        scope='Selected-seed convergence and observational composition diagnostics. No automatic claim of convergence or causal individual incentive, no filtering or replacement of the original ten-seed figures.')
    (DESTINATION/'audit.json').write_text(json.dumps(summary,indent=2)+'\n')
    for row in windows:
        print(f"seed={row['seed']} F={row['fee']} epochs={row['start_epoch']}-{row['end_epoch_inclusive']} mean={row['active_stake_mean_pct']:.3f}% tail_drift={row['second50_minus_first50_pp']:+.3f}pp")
    print('Original prefixes reproduced:',reproduced)
    print(DESTINATION)


if __name__=='__main__':
    prepare()
