#!/usr/bin/env python3
"""Validate the estimator repair and compare rerun Full/Ersoy curves with legacy data."""

import copy
import csv
import hashlib
import json
import statistics

from run_experiments import ROOT
from ersoy_participation_report import METRICS, STYLES, aggregate, interval, write_csv
from adaptive_estimator_audit import audit_run, ESTIMATOR_VERSION
import matplotlib.pyplot as plt


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    fees = [.0001, .0005, .001, .005, .01]
    seeds = [200, 201]
    old_root = ROOT / 'results/processed/trail_ersoy_full_cost5_curve'
    new_root = ROOT / 'results/processed/trail_ersoy_full_cost5_estimator_v2_cost5'
    destination = ROOT / 'results/processed/trail_ersoy_full_cost5_estimator_v2'
    old_audit = json.loads((old_root / 'audit.json').read_text())
    new_audit = json.loads((new_root / 'audit.json').read_text())
    assert old_audit['valid_runs'] == new_audit['valid_runs'] == 20
    new_manifest = json.loads((ROOT / 'results/batches/fixed-g-cost5-estimator-v2/cost5/manifest.json').read_text())
    assert new_manifest['status'] == 'complete'
    assert new_manifest['binary_sha256'] != old_audit['binary_sha256']
    all_rows, audits = [], []
    source_hashes = {}
    for label, root in [('legacy_ratio_ema', old_root), (ESTIMATOR_VERSION, new_root)]:
        path = root / 'runs.csv'
        source_hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = read_csv(path)
        assert len(rows) == 20
        assert len({(r['protocol'], float(r['transaction_fee']), int(r['seed'])) for r in rows}) == 20
        assert {r['protocol'] for r in rows} == {'trail', 'ersoy_fee'}
        assert {float(r['transaction_fee']) for r in rows} == set(fees)
        assert len({(r['git_commit_sha'], r['git_source_diff_sha256']) for r in rows}) == 1
        for row in rows:
            row['seed'] = int(row['seed'])
            row['transaction_fee'] = float(row['transaction_fee'])
            row['cost_multiplier'] = float(row['cost_multiplier'])
            assert row['cost_multiplier'] == 5
            row['estimator'] = label
            for metric in METRICS:
                row[metric] = float(row[metric])
            raw = ROOT / row['raw_source']
            adaptive = read_csv(raw / 'adaptive_relay_metrics.csv')
            assert [int(r['epoch']) for r in adaptive] == list(range(200))
            window = adaptive[100:]
            assert abs(row['active_stake_pct'] - 100*statistics.mean(float(r['active_stake_share']) for r in window)) < 1e-9
            row['first_half_active_stake_pct'] = 100*statistics.mean(float(r['active_stake_share']) for r in window[:50])
            row['second_half_active_stake_pct'] = 100*statistics.mean(float(r['active_stake_share']) for r in window[50:])
            decisions = [r for r in window if r['update_applied'] == 'true']
            row['negative_decision_windows'] = sum(bool(r['decision_benefit_per_forward']) and float(r['decision_benefit_per_forward']) < 0 for r in decisions) if label == ESTIMATOR_VERSION else sum(bool(r['smoothed_benefit_per_forward']) and float(r['smoothed_benefit_per_forward']) < 0 for r in decisions)
            if label == ESTIMATOR_VERSION:
                audits.append(audit_run(raw))
        all_rows.extend(rows)

    ignore = {'metrics_prefix', 'run_id', 'output_dir'}
    for protocol in ['trail', 'ersoy_fee']:
        for fee in fees:
            for seed in seeds:
                pair = [r for r in all_rows if r['protocol']==protocol and r['transaction_fee']==fee and r['seed']==seed]
                assert len(pair) == 2
                for key in ['initial_design_sha256', 'graph_sha256', 'background_workload_hash', 'generated_tx']:
                    assert pair[0][key] == pair[1][key], (protocol, fee, seed, key)
                configs = [json.loads((ROOT/r['raw_source']/'run_config.json').read_text())['config'] for r in pair]
                assert {k:v for k,v in configs[0].items() if k not in ignore} == {k:v for k,v in configs[1].items() if k not in ignore}
    for seed in seeds:
        paired = [r for r in all_rows if r['seed']==seed]
        for key in ['initial_design_sha256', 'graph_sha256', 'background_workload_hash', 'generated_tx']:
            assert len({r[key] for r in paired}) == 1, (seed, key)

    groups, contrasts, changes = [], [], []
    for label in ['legacy_ratio_ema', ESTIMATOR_VERSION]:
        for fee in fees:
            summary, paired = aggregate([r for r in all_rows if r['estimator']==label and r['transaction_fee']==fee])
            groups.extend(dict(estimator=label, transaction_fee=fee, **r) for r in summary)
            contrasts.extend(dict(estimator=label, transaction_fee=fee, **r) for r in paired)
    for protocol in ['trail', 'ersoy_fee']:
        for fee in fees:
            for metric in METRICS:
                differences = []
                for seed in seeds:
                    pair = {r['estimator']:r for r in all_rows if r['protocol']==protocol and r['transaction_fee']==fee and r['seed']==seed}
                    differences.append(pair[ESTIMATOR_VERSION][metric]-pair['legacy_ratio_ema'][metric])
                mean, ci = interval(differences)
                changes.append(dict(protocol=protocol, transaction_fee=fee, metric=metric,
                                    n_pairs=2, repaired_minus_legacy_mean=mean, ci95=ci))
    destination.mkdir(parents=True, exist_ok=True)
    repaired = [r for r in all_rows if r['estimator']==ESTIMATOR_VERSION]
    write_csv(destination/'runs.csv', repaired)
    write_csv(destination/'groups.csv', [r for r in groups if r['estimator']==ESTIMATOR_VERSION])
    write_csv(destination/'comparison_runs.csv', all_rows)
    write_csv(destination/'comparison_groups.csv', groups)
    write_csv(destination/'paired_contrasts.csv', contrasts)
    write_csv(destination/'estimator_differences.csv', changes)

    def draw(ax, label, title):
        for protocol in ['trail', 'ersoy_fee']:
            sample = [r for r in all_rows if r['estimator']==label and r['protocol']==protocol]
            color, marker, line = STYLES[protocol]
            for seed in seeds:
                values = [next(r['active_stake_pct'] for r in sample if r['transaction_fee']==fee and r['seed']==seed) for fee in fees]
                ax.plot(fees, values, color=color, alpha=.22, linewidth=.8, linestyle=line)
            values = [statistics.mean(r['active_stake_pct'] for r in sample if r['transaction_fee']==fee) for fee in fees]
            ax.plot(fees, values, color=color, marker=marker, markerfacecolor='white', linestyle=line,
                    linewidth=2, label=sample[0]['mechanism'])
        ax.set_xscale('log')
        ax.set_xticks(fees, [f'{fee:g}' for fee in fees])
        ax.minorticks_off()
        ax.set(title=title, xlabel='Transaction fee F (log scale)', ylim=(0,100))
        ax.grid(axis='y', alpha=.2)
        ax.spines[['top','right']].set_visible(False)
    note = '5x forwarding cost; g=0.0001; Full eta=0.5; Ersoy C=0.25; analysis epochs 100-199.\nThick: two-seed means; thin: individual seeds. Only the shared behavioral estimator changed.'
    fig, ax = plt.subplots(figsize=(8,5), constrained_layout=True)
    draw(ax, ESTIMATOR_VERSION, 'Full TRAIL and Ersoy: repaired behavioral estimator')
    ax.set_ylabel('Active-relay stake (%)')
    ax.legend(loc='upper left', frameon=False)
    fig.supxlabel(note, fontsize=8.5)
    for ext in ['png','pdf']:
        fig.savefig(destination/f'ersoy_vs_full.{ext}', dpi=220)
    plt.close(fig)
    fig, axes = plt.subplots(1,2,figsize=(11,4.8),sharey=True,constrained_layout=True)
    draw(axes[0], 'legacy_ratio_ema', 'Original estimator')
    draw(axes[1], ESTIMATOR_VERSION, 'EMA of premiums before division')
    axes[0].set_ylabel('Active-relay stake (%)')
    fig.legend(*axes[0].get_legend_handles_labels(), loc='outside upper center', ncol=2, frameon=False)
    fig.supxlabel(note, fontsize=8.5)
    for ext in ['png','pdf']:
        fig.savefig(destination/f'before_after.{ext}', dpi=220)
    plt.close(fig)
    (destination/'audit.json').write_text(json.dumps(dict(
        expected_runs=20, valid_runs=20, valid_new_runs=20, valid_legacy_runs=20,
        new_estimator=ESTIMATOR_VERSION,
        new_binary_sha256=new_manifest['binary_sha256'], old_binary_sha256=old_audit['binary_sha256'],
        inputs_sha256=source_hashes, estimator_reconstruction=audits,
        checks='Matched full configurations and input designs for every old/new pair; all repaired EMA moments, decision gates and invalid-window exploration independently reconstructed.',
        scope='Numerical robustness repair of a shared group estimator; does not establish individual counterfactual incentives or equilibrium.'
    ),indent=2)+'\n')
    for row in groups:
        if row['estimator']==ESTIMATOR_VERSION:
            print(f"F={row['transaction_fee']:g} {row['mechanism']} active={row['active_stake_pct_mean']:.2f}%",flush=True)
    print(destination,flush=True)


if __name__ == '__main__':
    main()
