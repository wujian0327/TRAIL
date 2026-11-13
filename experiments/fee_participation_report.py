#!/usr/bin/env python3
"""Validate and plot fee sensitivity with independently fixed irrecoverable cost."""

import argparse
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

from ersoy_participation_report import STYLES, aggregate, summarize, write_csv
from run_experiments import ROOT, expand_runs, load_yaml
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output-name')
    args = parser.parse_args()
    spec = load_yaml(args.config)
    runs = expand_runs(spec)
    start, end = spec['defaults']['warmup_epochs'], spec['defaults']['max_epochs']
    show_confidence_intervals = len(spec['seeds']) >= 5
    rows, by_fee, generation_audit = [], defaultdict(list), {}
    for run in runs:
        row, _ = summarize(run)
        fee = run['transaction_fee']
        row['transaction_fee'] = fee
        row['irrecoverable_cost'] = run['irrecoverable_cost']
        rows.append(row)
        by_fee[fee].append(row)
        directory = Path(run['output_dir'])
        with (directory / 'node_epoch_metrics.csv').open() as handle:
            nodes = list(csv.DictReader(handle))
        final = [r for r in nodes if int(r['epoch']) == run['max_epochs']-1]
        max_spending = max(float(r['fee_spent']) for r in final)
        assert max_spending < run['initial_liquid_balance'], 'originator budget is binding'
        assert min(float(r['balance']) for r in nodes) > 0, 'observed liquid balance exhausted'
        cumulative = sum(float(r['fee_spent']) for r in final)
        expected = row['generated_tx'] * (fee + run['irrecoverable_cost'])
        assert abs(cumulative-expected) <= run['node_num']*.5e-6+1e-7, 'F+g ledger mismatch'
        initial_stake = {r['validator_id']: r['economic_stake'] for r in nodes if int(r['epoch']) == 0}
        assert all(r['economic_stake'] == initial_stake[r['validator_id']] for r in nodes)
        with (directory / 'generation_samples.csv').open() as handle:
            recorded = sum(1 for _ in csv.DictReader(handle))
        generation_audit[row['run_id']] = dict(submitted=row['generated_tx'], recorded=recorded,
                                               max_originator_spending=max_spending)
    assert len({r['run_id'] for r in rows}) == len(rows)
    assert len({(r['git_commit_sha'], r['git_source_diff_sha256']) for r in rows}) == 1
    protocols = list(dict.fromkeys(r['protocol'] for r in rows))
    cost_multipliers = sorted({r['cost_multiplier'] for r in rows})
    seeds = spec['seeds']
    fees = sorted(by_fee)
    assert len(rows) == len(fees)*len(protocols)*len(seeds)
    assert len({(r['transaction_fee'], r['protocol'], r['seed']) for r in rows}) == len(rows)
    for seed in seeds:
        paired = [r for r in rows if r['seed'] == seed]
        for field in ['initial_design_sha256', 'graph_sha256', 'background_workload_hash', 'generated_tx']:
            assert len({r[field] for r in paired}) == 1, (seed, field)
    groups, contrasts = [], []
    for fee in fees:
        summary, paired = aggregate(by_fee[fee])
        groups.extend(dict(transaction_fee=fee, **r) for r in summary)
        contrasts.extend(dict(transaction_fee=fee, **r) for r in paired)
    destination = ROOT / 'results/processed' / (args.output_name or spec['suite'])
    destination.mkdir(parents=True, exist_ok=True)
    write_csv(destination / 'runs.csv', rows)
    write_csv(destination / 'groups.csv', groups)
    if contrasts:
        write_csv(destination / 'paired_contrasts.csv', contrasts)
    else:
        with (destination / 'paired_contrasts.csv').open('w', newline='') as handle:
            csv.DictWriter(handle, fieldnames=['transaction_fee','comparison','cost_multiplier',
                           'metric','n_pairs','mean_difference','ci95']).writeheader()
    (destination / 'audit.json').write_text(json.dumps(dict(
        expected_runs=len(runs), valid_runs=len(rows), seeds=seeds, fees=fees,
        irrecoverable_cost=spec['defaults']['irrecoverable_cost'],
        initial_liquid_balance=spec['defaults']['initial_liquid_balance'],
        initial_liquid_balance_by_fee={str(f):sorted({r['initial_liquid_balance'] for r in runs if r['transaction_fee']==f}) for f in fees},
        git_commit_sha=rows[0]['git_commit_sha'], git_source_diff_sha256=rows[0]['git_source_diff_sha256'],
        config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
        report_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        generation_audit=generation_audit,
        inclusion_clock='First global generation of each hash to first inclusion; duplicate copy birth metadata does not reset latency.',
        checks='Matched initial stake/profile/cost, graph and input workload across fees and mechanisms; constant economic stake; nonbinding originator funding; F+g fee ledger and reward budgets reconciled.',
        window=f'Log epochs {start}-{end-1}: {end-start} post-warm-up epochs per seed.',
        uncertainty=('Across-seed 95% t intervals are shown on the comparison plot.' if show_confidence_intervals else 'CSV contains across-seed 95% t intervals. Pilot plot shows means and individual seed lines, not confidence bands.'),
        scope='Exploratory shared-propagation PoS adaptation; finite observation window does not establish steady state.'
    ), indent=2)+'\n')

    fig, ax = plt.subplots(figsize=(7.1, 4.7), constrained_layout=True)
    for protocol in protocols:
        color, marker, line = STYLES[protocol]
        batch = [r for r in rows if r['protocol'] == protocol]
        if not show_confidence_intervals:
            for seed in seeds:
                sample = sorted((r for r in batch if r['seed'] == seed), key=lambda r:r['transaction_fee'])
                ax.plot([r['transaction_fee'] for r in sample], [r['active_stake_pct'] for r in sample],
                        color=color, alpha=.22, linewidth=.85, linestyle=line)
        means = [statistics.mean(r['active_stake_pct'] for r in batch if r['transaction_fee'] == fee) for fee in fees]
        ax.plot(fees, means, color=color, marker=marker, linestyle=line,
                linewidth=2, markerfacecolor='white', label=batch[0]['mechanism'])
        if show_confidence_intervals:
            ci = [next(r['active_stake_pct_ci95'] for r in groups if r['transaction_fee']==fee and r['mechanism']==batch[0]['mechanism']) for fee in fees]
            ax.errorbar(fees, means, yerr=ci, fmt='none', color=color, capsize=3, linewidth=1)
    ax.set_xscale('log')
    ax.set_xticks(fees, [f'{fee:.4f}' for fee in fees])
    ax.minorticks_off()
    ax.set(xlabel='Transaction fee F (log scale)', ylabel='Active-relay stake (%)', ylim=(0,100))
    ax.grid(axis='y', alpha=.2)
    ax.spines[['top','right']].set_visible(False)
    label = 'comparison' if show_confidence_intervals else 'pilot'
    fig.suptitle(f'(b) Fee sensitivity: {label} with {len(seeds)} paired seeds', fontsize=12)
    ax.legend(fontsize=8, ncol=4, loc='lower center', bbox_to_anchor=(.5, 1.01))
    uncertainty = 'Error bars: across-seed 95% t intervals.' if show_confidence_intervals else 'Thick lines: means; thin lines: individual seeds.'
    cost_label = ', '.join(f'{cost:g}x' for cost in cost_multipliers)
    fig.supxlabel(f"Fixed g={spec['defaults']['irrecoverable_cost']:g}; forward cost={cost_label}; subsidy={spec['defaults']['base_reward']:g}.\nAnalysis epochs {start}-{end-1}. {uncertainty}", fontsize=9)
    for extension in ['png','pdf']:
        fig.savefig(destination / f'fig5b_fee_participation.{extension}', dpi=220)
    plt.close(fig)
    for r in groups:
        print(f"F={r['transaction_fee']:.4f} {r['mechanism']:18s} active={r['active_stake_pct_mean']:.2f}%")
    print(destination)


if __name__ == '__main__':
    main()
