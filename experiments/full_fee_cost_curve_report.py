#!/usr/bin/env python3
"""Validate and combine measured Full TRAIL endpoints and fee midpoints."""

import csv
import hashlib
import json
import statistics
from pathlib import Path

from run_experiments import ROOT
from ersoy_participation_report import interval, write_csv
import matplotlib.pyplot as plt


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    fees = [.0001, .0005, .001, .005, .01]
    seeds = [200, 201]
    rows, input_hashes, binary_hashes = [], {}, set()
    maximum_stake_error, independent_checks = 0.0, 0
    for cost in [5, 10]:
        for suite, batch_name, expected_runs in [
            ('trail_fee_participation_cost_probe', 'fixed-g-cost-probe-v1', 16),
            ('trail_full_fee_cost_midpoints', 'fixed-g-full-fee-midpoints-v1', 6),
        ]:
            directory = ROOT / f'results/processed/{suite}_cost{cost}'
            audit = json.loads((directory / 'audit.json').read_text())
            assert audit['valid_runs'] == audit['expected_runs'] == expected_runs
            batch = ROOT / f'results/batches/{batch_name}/cost{cost}'
            manifest = json.loads((batch / 'manifest.json').read_text())
            assert manifest['status'] == 'complete'
            binary_hashes.add(manifest['binary_sha256'])
            source = directory / 'runs.csv'
            input_hashes[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
            sample = [r for r in read_csv(source) if r['protocol'] == 'trail']
            expected_fees = {.0001, .01} if expected_runs == 16 else {.0005, .001, .005}
            assert {float(r['transaction_fee']) for r in sample} == expected_fees
            assert len(sample) == len(expected_fees) * len(seeds)
            rows.extend(sample)
    assert len(binary_hashes) == 1
    assert len(rows) == 20
    assert len({(float(r['cost_multiplier']), float(r['transaction_fee']), int(r['seed'])) for r in rows}) == 20
    assert len({(r['git_commit_sha'], r['git_source_diff_sha256']) for r in rows}) == 1

    ignore = {'transaction_fee', 'metrics_prefix', 'run_id', 'output_dir'}
    for cost in [5, 10]:
        for seed in seeds:
            paired = [r for r in rows if float(r['cost_multiplier']) == cost and int(r['seed']) == seed]
            assert len(paired) == len(fees)
            assert {float(r['transaction_fee']) for r in paired} == set(fees)
            for key in ['initial_design_sha256', 'graph_sha256', 'background_workload_hash', 'generated_tx']:
                assert len({r[key] for r in paired}) == 1, (cost, seed, key)
            configs = [json.loads((ROOT / r['raw_source'] / 'run_config.json').read_text())['config'] for r in paired]
            assert all({k: v for k, v in cfg.items() if k not in ignore} ==
                       {k: v for k, v in configs[0].items() if k not in ignore} for cfg in configs)
    for seed in seeds:
        paired = [r for r in rows if int(r['seed']) == seed]
        for key in ['graph_sha256', 'background_workload_hash', 'generated_tx']:
            assert len({r[key] for r in paired}) == 1, (seed, key)

    for row in rows:
        raw = ROOT / row['raw_source']
        adaptive = read_csv(raw / 'adaptive_relay_metrics.csv')
        assert [int(r['epoch']) for r in adaptive] == list(range(200))
        mean = 100 * statistics.mean(float(r['active_stake_share']) for r in adaptive[100:])
        assert abs(mean - float(row['active_stake_pct'])) < 1e-9
        totals, active = {}, {}
        for node in read_csv(raw / 'node_epoch_metrics.csv'):
            epoch = int(node['epoch'])
            if epoch < 101:
                continue
            stake = float(node['economic_stake'])
            totals[epoch] = totals.get(epoch, 0.0) + stake
            if node['relay_profile'] == 'active':
                active[epoch] = active.get(epoch, 0.0) + stake
        for epoch in range(100, 199):
            share = active.get(epoch + 1, 0.0) / totals[epoch + 1]
            error = abs(share - float(adaptive[epoch]['active_stake_share']))
            assert error < 2 * 100 * .5e-6 / totals[epoch + 1] + .5e-9
            maximum_stake_error = max(maximum_stake_error, error)
            independent_checks += 1
        window = adaptive[100:]
        row['first_half_active_stake_pct'] = 100 * statistics.mean(float(r['active_stake_share']) for r in window[:50])
        row['second_half_active_stake_pct'] = 100 * statistics.mean(float(r['active_stake_share']) for r in window[50:])
        row['negative_decision_windows'] = sum(r['update_applied'] == 'true' and
            bool(r['smoothed_benefit_per_forward']) and float(r['smoothed_benefit_per_forward']) < 0 for r in window)

    destination = ROOT / 'results/processed/trail_full_fee_cost_curve'
    destination.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (float(r['cost_multiplier']), float(r['transaction_fee']), int(r['seed'])))
    write_csv(destination / 'runs.csv', rows)
    means = []
    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    for cost, color, marker in [(5, '#D55E00', 'o'), (10, '#0072B2', 's')]:
        sample = [r for r in rows if float(r['cost_multiplier']) == cost]
        for seed in seeds:
            values = [next(float(r['active_stake_pct']) for r in sample
                           if float(r['transaction_fee']) == fee and int(r['seed']) == seed) for fee in fees]
            ax.plot(fees, values, color=color, alpha=.22, linewidth=.9, linestyle='--')
        values = []
        for fee in fees:
            selected = [r for r in sample if float(r['transaction_fee']) == fee]
            mean, ci = interval(float(r['active_stake_pct']) for r in selected)
            means.append(dict(cost_multiplier=cost, transaction_fee=fee, n_seeds=2,
                              active_stake_pct_mean=mean, active_stake_pct_ci95=ci))
            values.append(mean)
        ax.plot(fees, values, color=color, marker=marker, markersize=7, markerfacecolor='white',
                linewidth=2, label=f'{cost}x forwarding cost')
    ax.set_xscale('log')
    ax.set_xticks(fees, [f'{fee:g}' for fee in fees])
    ax.minorticks_off()
    ax.set(xlabel='Transaction fee F (log scale)', ylabel='Active-relay stake (%)',
           title='Full TRAIL: fee sensitivity at two forwarding costs', ylim=(0, 100))
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.grid(axis='y', alpha=.2)
    ax.spines[['top', 'right']].set_visible(False)
    ax.legend(loc='upper left', frameon=False)
    fig.supxlabel('eta=0.5; theta=0.5; g=0.0001; analysis epochs 100-199.\n'
                  'Thick: means of two seeds; thin: individual seeds. Markers are measured fee levels.', fontsize=9)
    for extension in ['png', 'pdf']:
        fig.savefig(destination / f'full_fee_by_cost.{extension}', dpi=220)
    plt.close(fig)
    write_csv(destination / 'groups.csv', means)
    (destination / 'audit.json').write_text(json.dumps(dict(
        expected_runs=20, valid_runs=len(rows), new_runs=12, reused_endpoint_runs=8,
        fees=fees, cost_multipliers=[5, 10], seeds=seeds,
        binary_sha256=next(iter(binary_hashes)), input_csv_sha256=input_hashes,
        raw_adaptive_means_checked=20, independent_node_stake_checks=independent_checks,
        max_stake_share_abs_error=maximum_stake_error,
        checks='Full configurations differ only in fee and output metadata within each cost/seed; matched initial designs and workloads; source and binary match; raw participation agrees within CSV rounding.',
        uncertainty='Two-seed pilot; individual seed trajectories shown. Across-seed t intervals retained in CSV.',
        scope='Unchanged group-benefit behavioral model; finite-window results do not establish individual causal incentives or equilibrium.'
    ), indent=2) + '\n')
    print(json.dumps(means, indent=2), flush=True)
    print(destination, flush=True)


if __name__ == '__main__':
    main()
