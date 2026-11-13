#!/usr/bin/env python3
"""Validate fixed-fee cost sensitivity, reusing only matched repaired 5x runs."""

import copy
import csv
import hashlib
import json
import math
import statistics

from run_experiments import ROOT
from ersoy_participation_report import METRICS, STYLES, aggregate, write_csv
from adaptive_estimator_audit import audit_run, ESTIMATOR_VERSION
import matplotlib.pyplot as plt


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    costs, seeds, fee = [1, 2, 5], [200, 201], .001
    reference = ROOT/'results/processed/trail_full_theta025_cost5_estimator_v2'
    reference_audit = json.loads((reference/'audit.json').read_text())
    assert reference_audit['valid_runs'] == 30
    ref_path = reference/'runs.csv'
    reused = [r for r in read_csv(ref_path) if float(r['transaction_fee'])==fee
              and r['series'] in ['full_theta025','ersoy_fee']]
    assert len(reused) == 4
    rows = list(reused)
    input_hashes = {str(ref_path.relative_to(ROOT)):hashlib.sha256(ref_path.read_bytes()).hexdigest()}
    binary_hashes = {reference_audit['binary_sha256']}
    for cost in [1, 2]:
        directory = ROOT/f'results/processed/trail_cost_participation_estimator_v2_cost{cost}'
        audit = json.loads((directory/'audit.json').read_text())
        assert audit['expected_runs'] == audit['valid_runs'] == 4
        manifest = json.loads((ROOT/f'results/batches/fixed-fee-cost-trend-v2/cost{cost}/manifest.json').read_text())
        assert manifest['status'] == 'complete'
        binary_hashes.add(manifest['binary_sha256'])
        source = directory/'runs.csv'
        input_hashes[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
        sample = read_csv(source)
        assert len(sample) == 4 and {float(r['cost_multiplier']) for r in sample} == {cost}
        rows.extend(sample)
    assert len(binary_hashes) == 1
    assert len(rows) == len({(r['protocol'],float(r['cost_multiplier']),int(r['seed'])) for r in rows}) == 12
    assert len({(r['git_commit_sha'],r['git_source_diff_sha256']) for r in rows}) == 1
    configs, initial_nodes, checks = {}, {}, []
    for row in rows:
        row['seed'] = int(row['seed'])
        row['cost_multiplier'] = float(row['cost_multiplier'])
        row['transaction_fee'] = float(row['transaction_fee'])
        assert row['transaction_fee'] == fee
        assert row['cost_multiplier'] in costs and row['seed'] in seeds
        assert row['protocol'] in ['trail','ersoy_fee']
        row['series'] = row['protocol']
        row['mechanism'] = 'Full TRAIL' if row['protocol']=='trail' else 'Ersoy (C=0.25)'
        row['estimator'] = ESTIMATOR_VERSION
        raw = ROOT/row['raw_source']
        config = json.loads((raw/'run_config.json').read_text())['config']
        assert config['adaptive_relay']['cost_median_multiplier'] == row['cost_multiplier']
        assert config['trail_config']['eta'] == (.5 if row['protocol']=='trail' else 0)
        assert config['trail_config']['reward_policy'] == ('trail' if row['protocol']=='trail' else 'ersoy')
        assert config['trail_config']['ersoy_c'] == .25
        if row['protocol']=='trail':
            assert config['trail_config']['proposer_fee_ratio'] == .25
        configs[(row['protocol'],row['cost_multiplier'],row['seed'])] = config
        checks.append(audit_run(raw))
        adaptive = read_csv(raw/'adaptive_relay_metrics.csv')
        window = adaptive[100:]
        assert abs(float(row['active_stake_pct'])-100*statistics.mean(float(r['active_stake_share']) for r in window)) < 1e-9
        row['first_half_active_stake_pct'] = 100*statistics.mean(float(r['active_stake_share']) for r in window[:50])
        row['second_half_active_stake_pct'] = 100*statistics.mean(float(r['active_stake_share']) for r in window[50:])
        row['negative_decision_windows'] = sum(bool(r['decision_benefit_per_forward']) and float(r['decision_benefit_per_forward'])<0 for r in window)
        for metric in METRICS:
            row[metric] = float(row[metric])
        initial_nodes[(row['protocol'],row['cost_multiplier'],row['seed'])] = {
            r['validator_id']:r for r in read_csv(raw/'node_epoch_metrics.csv') if r['epoch']=='0'}
    ignore = {'metrics_prefix','run_id','output_dir'}
    node_cost_checks = 0
    for seed in seeds:
        paired = [r for r in rows if r['seed']==seed]
        assert len(paired) == 6
        for field in ['graph_sha256','background_workload_hash','generated_tx']:
            assert len({r[field] for r in paired}) == 1, (seed,field)
        common_configs = []
        baseline_nodes = initial_nodes[('trail',5.0,seed)]
        for row in paired:
            key = (row['protocol'],row['cost_multiplier'],seed)
            config = {k:copy.deepcopy(v) for k,v in configs[key].items() if k not in ignore}
            config['adaptive_relay'].pop('cost_median_multiplier')
            config['trail_config'].pop('eta')
            config['trail_config'].pop('reward_policy')
            # Ersoy does not use the TRAIL theta field. Its reused reference
            # serializes theta=0.5; all Ersoy payouts depend on C instead.
            config['trail_config'].pop('proposer_fee_ratio')
            common_configs.append(config)
            nodes = initial_nodes[key]
            assert nodes.keys() == baseline_nodes.keys()
            for address,node in nodes.items():
                base = baseline_nodes[address]
                for field in ['economic_stake','relay_profile','degree']:
                    assert node[field] == base[field], (key,address,field)
                assert math.isclose(float(node['relay_cost_per_forward'])/row['cost_multiplier'],
                                    float(base['relay_cost_per_forward'])/5,rel_tol=1e-12,abs_tol=1e-20)
                node_cost_checks += 1
        assert all(c==common_configs[0] for c in common_configs)

    groups, contrasts = aggregate(rows)
    destination = ROOT/'results/processed/trail_cost_participation_estimator_v2'
    destination.mkdir(parents=True,exist_ok=True)
    rows.sort(key=lambda r:(r['protocol'],r['cost_multiplier'],r['seed']))
    write_csv(destination/'runs.csv',rows)
    write_csv(destination/'groups.csv',groups)
    write_csv(destination/'paired_contrasts.csv',contrasts)
    fig,ax = plt.subplots(figsize=(7.6,4.9),constrained_layout=True)
    for protocol in ['trail','ersoy_fee']:
        sample = [r for r in rows if r['protocol']==protocol]
        color,marker,line = STYLES[protocol]
        for seed in seeds:
            values = [next(r['active_stake_pct'] for r in sample if r['cost_multiplier']==cost and r['seed']==seed) for cost in costs]
            ax.plot(costs,values,color=color,alpha=.22,linewidth=.8,linestyle=line)
        values = [statistics.mean(r['active_stake_pct'] for r in sample if r['cost_multiplier']==cost) for cost in costs]
        ax.plot(costs,values,color=color,marker=marker,markerfacecolor='white',linestyle=line,
                linewidth=2,label=sample[0]['mechanism'])
    ax.set_xscale('log')
    ax.set_xticks(costs,[f'{c}x' for c in costs])
    ax.minorticks_off()
    ax.set(xlabel='Forwarding cost multiplier (log scale)',ylabel='Active-relay stake (%)',
           title='Forwarding cost sensitivity at fixed transaction fee',ylim=(0,100))
    ax.grid(axis='y',alpha=.2)
    ax.spines[['top','right']].set_visible(False)
    ax.legend(loc='lower left',frameon=False)
    fig.supxlabel('F=0.001; g=0.0001; Full eta=0.5, theta=0.25; Ersoy C=0.25.\n'
                  'Analysis epochs 100-199. Thick: two-seed means; thin: individual seeds.\n'
                  '1x distribution median = 1.40557e-7 token per outbound forwarding attempt.',fontsize=8.5)
    for extension in ['png','pdf']:
        fig.savefig(destination/f'cost_participation.{extension}',dpi=220)
    plt.close(fig)
    (destination/'audit.json').write_text(json.dumps(dict(
        expected_runs=12,valid_runs=12,new_runs=8,reused_cost5_runs=4,
        costs=costs,seeds=seeds,transaction_fee=fee,estimator=ESTIMATOR_VERSION,
        binary_sha256=next(iter(binary_hashes)),input_csv_sha256=input_hashes,
        normalized_node_cost_checks=node_cost_checks,estimator_reconstruction=checks,
        checks='Same binary/source and background workloads; same initial stake/profiles/degree; each node cost scales by the declared multiplier; fixed fee and incentive settings; EMA decisions independently reconstructed.',
        inactive_parameter_note='Ersoy ignores TRAIL proposer_fee_ratio. The reused Ersoy 5x reference stores 0.5, new Ersoy configs store 0.25; C and the Ersoy fee-sharing code are unchanged.',
        scope='Two-seed finite-window cost sensitivity; no monotonicity or equilibrium assumed.'
    ),indent=2)+'\n')
    for row in groups:
        print(f"cost={row['cost_multiplier']:g}x {row['mechanism']} active={row['active_stake_pct_mean']:.2f}%",flush=True)
    print(destination,flush=True)


if __name__ == '__main__':
    main()
