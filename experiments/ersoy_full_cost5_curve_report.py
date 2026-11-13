#!/usr/bin/env python3
"""Compare measured Ersoy and Full TRAIL fee curves at 5x forwarding cost."""

import copy
import hashlib
import json
import statistics

from run_experiments import ROOT
from ersoy_participation_report import METRICS, STYLES, aggregate, write_csv
from full_fee_cost_curve_report import read_csv
import matplotlib.pyplot as plt


def main():
    fees = [.0001, .0005, .001, .005, .01]
    seeds = [200, 201]
    sources = [
        ('trail_full_fee_cost_curve', 'trail', 20, set(fees)),
        ('trail_fee_participation_cost_probe_cost5', 'ersoy_fee', 16, {.0001, .01}),
        ('trail_ersoy_fee_cost5_midpoints_cost5', 'ersoy_fee', 6, {.0005, .001, .005}),
    ]
    rows, input_hashes, binary_hashes = [], {}, set()
    for suite, protocol, expected, selected_fees in sources:
        directory = ROOT / 'results/processed' / suite
        audit = json.loads((directory / 'audit.json').read_text())
        assert audit['expected_runs'] == audit['valid_runs'] == expected
        if protocol == 'trail':
            binary_hashes.add(audit['binary_sha256'])
        else:
            batch_name = ('fixed-g-cost-probe-v1' if expected == 16 else 'fixed-g-ersoy-cost5-midpoints-v1')
            manifest = json.loads((ROOT / f'results/batches/{batch_name}/cost5/manifest.json').read_text())
            assert manifest['status'] == 'complete'
            binary_hashes.add(manifest['binary_sha256'])
        source = directory / 'runs.csv'
        input_hashes[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
        sample = [r for r in read_csv(source) if r['protocol'] == protocol and float(r['cost_multiplier']) == 5]
        assert {float(r['transaction_fee']) for r in sample} == selected_fees
        assert len(sample) == len(selected_fees) * len(seeds)
        rows.extend(sample)
    assert len(binary_hashes) == 1
    assert len(rows) == len({(r['protocol'], float(r['transaction_fee']), int(r['seed'])) for r in rows}) == 20
    assert len({(r['git_commit_sha'], r['git_source_diff_sha256']) for r in rows}) == 1

    ignore = {'transaction_fee', 'metrics_prefix', 'run_id', 'output_dir'}
    for seed in seeds:
        paired = [r for r in rows if int(r['seed']) == seed]
        assert len(paired) == 10
        for key in ['initial_design_sha256', 'graph_sha256', 'background_workload_hash', 'generated_tx']:
            assert len({r[key] for r in paired}) == 1, (seed, key)
        common_configs = []
        for row in paired:
            config = json.loads((ROOT / row['raw_source'] / 'run_config.json').read_text())['config']
            trail = config['trail_config']
            assert config['adaptive_relay']['cost_median_multiplier'] == 5
            assert config['irrecoverable_cost'] == .0001 and config['initial_liquid_balance'] == 10
            assert trail['ersoy_c'] == .25 and trail['proposer_fee_ratio'] == .5
            assert trail['eta'] == (0 if row['protocol'] == 'ersoy_fee' else .5)
            assert trail['reward_policy'] == ('ersoy' if row['protocol'] == 'ersoy_fee' else 'trail')
            common = {k: copy.deepcopy(v) for k, v in config.items() if k not in ignore}
            common['trail_config'].pop('eta')
            common['trail_config'].pop('reward_policy')
            common_configs.append(common)
        assert all(config == common_configs[0] for config in common_configs)

    for row in rows:
        adaptive = read_csv(ROOT / row['raw_source'] / 'adaptive_relay_metrics.csv')
        assert [int(r['epoch']) for r in adaptive] == list(range(200))
        window = adaptive[100:]
        mean = 100 * statistics.mean(float(r['active_stake_share']) for r in window)
        assert abs(mean - float(row['active_stake_pct'])) < 1e-9
        row['first_half_active_stake_pct'] = 100 * statistics.mean(float(r['active_stake_share']) for r in window[:50])
        row['second_half_active_stake_pct'] = 100 * statistics.mean(float(r['active_stake_share']) for r in window[50:])
        row['negative_decision_windows'] = sum(r['update_applied'] == 'true' and
            bool(r['smoothed_benefit_per_forward']) and float(r['smoothed_benefit_per_forward']) < 0 for r in window)
        row['cost_multiplier'] = float(row['cost_multiplier'])
        row['transaction_fee'] = float(row['transaction_fee'])
        row['seed'] = int(row['seed'])
        for metric in METRICS:
            row[metric] = float(row[metric])
    rows.sort(key=lambda r: (r['protocol'], r['transaction_fee'], r['seed']))
    groups, contrasts = [], []
    for fee in fees:
        summary, paired = aggregate([r for r in rows if r['transaction_fee'] == fee])
        groups.extend(dict(transaction_fee=fee, **r) for r in summary)
        contrasts.extend(dict(transaction_fee=fee, **r) for r in paired)
    destination = ROOT / 'results/processed/trail_ersoy_full_cost5_curve'
    destination.mkdir(parents=True, exist_ok=True)
    write_csv(destination / 'runs.csv', rows)
    write_csv(destination / 'groups.csv', groups)
    write_csv(destination / 'paired_contrasts.csv', contrasts)

    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    for protocol in ['trail', 'ersoy_fee']:
        sample = [r for r in rows if r['protocol'] == protocol]
        color, marker, line = STYLES[protocol]
        for seed in seeds:
            values = [next(r['active_stake_pct'] for r in sample if r['transaction_fee'] == fee and r['seed'] == seed) for fee in fees]
            ax.plot(fees, values, color=color, alpha=.22, linewidth=.9, linestyle=line)
        values = [statistics.mean(r['active_stake_pct'] for r in sample if r['transaction_fee'] == fee) for fee in fees]
        ax.plot(fees, values, color=color, marker=marker, markersize=7, markerfacecolor='white',
                linestyle=line, linewidth=2, label=sample[0]['mechanism'])
    ax.set_xscale('log')
    ax.set_xticks(fees, [f'{fee:g}' for fee in fees])
    ax.minorticks_off()
    ax.set(xlabel='Transaction fee F (log scale)', ylabel='Active-relay stake (%)',
           title='Full TRAIL and Ersoy: 5x forwarding cost', ylim=(0, 100))
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.grid(axis='y', alpha=.2)
    ax.spines[['top', 'right']].set_visible(False)
    ax.legend(loc='upper left', frameon=False)
    fig.supxlabel('Fixed g=0.0001; subsidy=0.1; Full eta=0.5; Ersoy C=0.25.\n'
                  'Analysis epochs 100-199. Thick: two-seed means; thin: individual seeds.', fontsize=9)
    for extension in ['png', 'pdf']:
        fig.savefig(destination / f'ersoy_vs_full.{extension}', dpi=220)
    plt.close(fig)
    (destination / 'audit.json').write_text(json.dumps(dict(
        expected_runs=20, valid_runs=len(rows), new_ersoy_runs=6, reused_ersoy_endpoint_runs=4, reused_full_runs=10,
        fees=fees, cost_multiplier=5, seeds=seeds,
        binary_sha256=next(iter(binary_hashes)), input_csv_sha256=input_hashes,
        checks='Configurations differ only in fee, output metadata, eta and reward policy; matched node designs and input workloads; same binary/source; all 20 raw participation trajectories rechecked.',
        uncertainty='Two-seed exploratory pilot; seed trajectories and paired contrasts retained; finite-window results do not establish equilibrium.',
        scope='Shared-propagation PoS adaptation of Ersoy fee sharing, using the unchanged group-benefit behavior model.'
    ), indent=2) + '\n')
    for row in groups:
        print(f"F={row['transaction_fee']:g} {row['mechanism']} active={row['active_stake_pct_mean']:.2f}%", flush=True)
    print(destination, flush=True)


if __name__ == '__main__':
    main()
