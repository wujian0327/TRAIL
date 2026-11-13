#!/usr/bin/env python3
"""Compare paired Full theta=0.25 reruns with theta=0.5 and repaired Ersoy."""

import copy
import csv
import hashlib
import json
import statistics

from run_experiments import ROOT
from ersoy_participation_report import METRICS, interval, write_csv
from adaptive_estimator_audit import audit_run, ESTIMATOR_VERSION
import matplotlib.pyplot as plt


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    fees = [.0001, .0005, .001, .005, .01]
    seeds = [200, 201]
    baseline = ROOT / 'results/processed/trail_ersoy_full_cost5_estimator_v2'
    new = ROOT / 'results/processed/trail_full_theta025_cost5_estimator_v2_cost5'
    destination = ROOT / 'results/processed/trail_full_theta025_cost5_estimator_v2'
    baseline_audit = json.loads((baseline/'audit.json').read_text())
    new_audit = json.loads((new/'audit.json').read_text())
    assert baseline_audit['valid_runs'] == 20
    assert new_audit['expected_runs'] == new_audit['valid_runs'] == 10
    manifest = json.loads((ROOT/'results/batches/fixed-g-cost5-theta025-v2/cost5/manifest.json').read_text())
    assert manifest['status'] == 'complete'
    assert manifest['binary_sha256'] == baseline_audit['new_binary_sha256']
    rows, checks, input_hashes = [], [], {}
    labels = {'full_theta050':'Full TRAIL, theta=0.5',
              'full_theta025':'Full TRAIL, theta=0.25', 'ersoy_fee':'Ersoy (C=0.25)'}
    for root in [baseline, new]:
        path = root/'runs.csv'
        input_hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        sample = read_csv(path)
        assert len(sample) == (20 if root == baseline else 10)
        if root == new:
            assert {r['protocol'] for r in sample} == {'trail'}
        for row in sample:
            row['seed'] = int(row['seed'])
            row['transaction_fee'] = float(row['transaction_fee'])
            row['cost_multiplier'] = float(row['cost_multiplier'])
            assert row['cost_multiplier'] == 5
            row['series'] = ('ersoy_fee' if row['protocol']=='ersoy_fee' else
                             ('full_theta025' if root==new else 'full_theta050'))
            row['mechanism'] = labels[row['series']]
            row['estimator'] = ESTIMATOR_VERSION
            raw = ROOT/row['raw_source']
            config = json.loads((raw/'run_config.json').read_text())['config']
            assert config['trail_config']['proposer_fee_ratio'] == (.25 if root==new else .5)
            assert config['trail_config']['eta'] == (0 if row['protocol']=='ersoy_fee' else .5)
            assert config['trail_config']['reward_policy'] == ('ersoy' if row['protocol']=='ersoy_fee' else 'trail')
            checks.append(audit_run(raw))
            adaptive = read_csv(raw/'adaptive_relay_metrics.csv')
            window = adaptive[100:]
            assert abs(float(row['active_stake_pct'])-100*statistics.mean(float(r['active_stake_share']) for r in window)) < 1e-9
            row['first_half_active_stake_pct'] = 100*statistics.mean(float(r['active_stake_share']) for r in window[:50])
            row['second_half_active_stake_pct'] = 100*statistics.mean(float(r['active_stake_share']) for r in window[50:])
            row['negative_decision_windows'] = sum(bool(r['decision_benefit_per_forward']) and float(r['decision_benefit_per_forward'])<0 for r in window)
            for metric in METRICS:
                row[metric] = float(row[metric])
            rows.append(row)
    assert len(rows) == len({(r['series'],r['transaction_fee'],r['seed']) for r in rows}) == 30
    assert len({(r['git_commit_sha'],r['git_source_diff_sha256']) for r in rows}) == 1
    ignore = {'metrics_prefix','run_id','output_dir'}
    for fee in fees:
        for seed in seeds:
            pair = [r for r in rows if r['protocol']=='trail' and r['transaction_fee']==fee and r['seed']==seed]
            assert len(pair) == 2
            configs = []
            for row in pair:
                config = json.loads((ROOT/row['raw_source']/'run_config.json').read_text())['config']
                config = {k:copy.deepcopy(v) for k,v in config.items() if k not in ignore}
                config['trail_config'].pop('proposer_fee_ratio')
                configs.append(config)
            assert configs[0] == configs[1], (fee,seed,'unexpected config difference')
    for seed in seeds:
        sample = [r for r in rows if r['seed']==seed]
        assert len(sample) == 15
        for field in ['initial_design_sha256','graph_sha256','background_workload_hash','generated_tx']:
            assert len({r[field] for r in sample}) == 1, (seed,field)

    groups, contrasts, seed_differences = [], [], []
    for fee in fees:
        for series in labels:
            sample = [r for r in rows if r['series']==series and r['transaction_fee']==fee]
            assert {r['seed'] for r in sample} == set(seeds)
            record = dict(series=series,mechanism=labels[series],transaction_fee=fee,n_seeds=2)
            for metric in METRICS:
                mean,ci = interval(r[metric] for r in sample)
                record[metric+'_mean'],record[metric+'_ci95'] = mean,ci
            groups.append(record)
        for first,second in [('full_theta025','full_theta050'),('full_theta025','ersoy_fee'),('full_theta050','ersoy_fee')]:
            a = {r['seed']:r for r in rows if r['series']==first and r['transaction_fee']==fee}
            b = {r['seed']:r for r in rows if r['series']==second and r['transaction_fee']==fee}
            for seed in seeds:
                seed_differences.append(dict(transaction_fee=fee,seed=seed,comparison=first+' minus '+second,
                                             active_stake_difference_pp=a[seed]['active_stake_pct']-b[seed]['active_stake_pct']))
            for metric in METRICS:
                mean,ci = interval(a[seed][metric]-b[seed][metric] for seed in seeds)
                contrasts.append(dict(transaction_fee=fee,comparison=first+' minus '+second,metric=metric,
                                      n_pairs=2,mean_difference=mean,ci95=ci))
    destination.mkdir(parents=True,exist_ok=True)
    rows.sort(key=lambda r:(r['series'],r['transaction_fee'],r['seed']))
    write_csv(destination/'runs.csv',rows)
    write_csv(destination/'groups.csv',groups)
    write_csv(destination/'paired_contrasts.csv',contrasts)
    write_csv(destination/'seed_differences.csv',seed_differences)
    fig,ax = plt.subplots(figsize=(8.5,5),constrained_layout=True)
    styles = [('full_theta050','#D55E00','o','-'),('full_theta025','#0072B2','s','--'),
              ('ersoy_fee','#009E73','D','-.')]
    for series,color,marker,line in styles:
        sample = [r for r in rows if r['series']==series]
        for seed in seeds:
            values = [next(r['active_stake_pct'] for r in sample if r['transaction_fee']==fee and r['seed']==seed) for fee in fees]
            ax.plot(fees,values,color=color,alpha=.2,linewidth=.8,linestyle=line)
        values = [next(r['active_stake_pct_mean'] for r in groups if r['series']==series and r['transaction_fee']==fee) for fee in fees]
        ax.plot(fees,values,color=color,marker=marker,markerfacecolor='white',linestyle=line,
                linewidth=2,label=labels[series])
    ax.set_xscale('log')
    ax.set_xticks(fees,[f'{fee:g}' for fee in fees])
    ax.minorticks_off()
    ax.set(title='Full TRAIL: proposer fee ratio sensitivity at 5x cost',
           xlabel='Transaction fee F (log scale)',ylabel='Active-relay stake (%)',ylim=(0,100))
    ax.grid(axis='y',alpha=.2)
    ax.spines[['top','right']].set_visible(False)
    fig.legend(*ax.get_legend_handles_labels(),loc='outside upper center',ncol=3,frameon=False,fontsize=9)
    fig.supxlabel('5x forwarding cost; g=0.0001; Full eta=0.5; repaired behavior estimator.\n'
                  'Analysis epochs 100-199. Thick: two-seed means; thin: individual seeds.',fontsize=9)
    for extension in ['png','pdf']:
        fig.savefig(destination/f'theta_comparison.{extension}',dpi=220)
    plt.close(fig)
    (destination/'audit.json').write_text(json.dumps(dict(
        expected_runs=30,valid_runs=30,new_theta025_runs=10,reused_theta050_full_runs=10,reused_ersoy_runs=10,
        fees=fees,seeds=seeds,cost_multiplier=5,estimator=ESTIMATOR_VERSION,
        binary_sha256=manifest['binary_sha256'],input_csv_sha256=input_hashes,estimator_reconstruction=checks,
        checks='Same source and binary; paired Full configs differ only in theta and output metadata; matched input designs; all EMA moments and decision gates rechecked.',
        scope='Exploratory two-seed theta sensitivity; no selection of points by outcome or steady-state claim.'
    ),indent=2)+'\n')
    for row in groups:
        print(f"F={row['transaction_fee']:g} {row['mechanism']} active={row['active_stake_pct_mean']:.2f}%",flush=True)
    print(destination,flush=True)


if __name__ == '__main__':
    main()
