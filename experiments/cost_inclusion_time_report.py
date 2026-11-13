#!/usr/bin/env python3
"""Plot inclusion-time cost sensitivity from existing transaction-level logs."""

import csv
import hashlib
import json
import math
import statistics

from run_experiments import ROOT
from ersoy_participation_report import STYLES, interval, write_csv
import matplotlib.pyplot as plt


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    destination = ROOT/'results/processed/trail_cost_participation_estimator_v2'
    source = destination/'runs.csv'
    runs = read_csv(source)
    assert len(runs) == 12
    records = []
    maximum_mean_error = maximum_logged_delay_error = 0.0
    for run in runs:
        raw = ROOT/run['raw_source']
        config = json.loads((raw/'run_config.json').read_text())['config']
        meta = json.loads((raw/'experiment_meta.json').read_text())
        slots = config['slot_per_epoch']
        duration = config['slot_duration']
        followup_slots = meta['adaptive_followup_epochs']*slots
        earliest = config['warmup_epochs']*slots
        latest = config['max_epochs']*slots-1-followup_slots
        horizon = followup_slots*duration
        assert (earliest,latest,horizon) == (500,949,50)
        generated, duplicates = {}, 0
        for row in read_csv(raw/'generation_samples.csv'):
            created = int(row['created_epoch'])*slots+int(row['created_slot'])
            if earliest <= created <= latest:
                duplicates += row['tx_hash'] in generated
                generated.setdefault(row['tx_hash'],created)
        included, reported_delays = {}, {}
        for row in read_csv(raw/'inclusion_samples.csv'):
            tx = row['tx_hash']
            if tx not in generated:
                continue
            slot = int(row['included_slot'])
            if tx not in included or slot < included[tx]:
                included[tx] = slot
                reported_delays[tx] = float(row['latency_s'])
        bounded, completed = [], []
        timeout_count = 0
        for tx,created in generated.items():
            if tx in included and included[tx]-created <= followup_slots:
                delay = max(included[tx]-created,0)*duration
                maximum_logged_delay_error = max(maximum_logged_delay_error,abs(delay-reported_delays[tx]))
                assert abs(delay-reported_delays[tx]) <= .5e-6
                bounded.append(min(delay,horizon))
                completed.append(min(delay,horizon))
            else:
                bounded.append(horizon)
                timeout_count += 1
        mean = statistics.mean(bounded)
        error = abs(mean-float(run['restricted_mean_s']))
        maximum_mean_error = max(maximum_mean_error,error)
        assert error < 1e-12
        assert len(generated) == int(run['generated_cohort'])
        records.append(dict(protocol=run['protocol'],mechanism=run['mechanism'],
            cost_multiplier=float(run['cost_multiplier']),seed=int(run['seed']),
            restricted_mean_s=mean,successful_only_mean_s=statistics.mean(completed),
            cohort_transactions=len(generated),not_included_within_horizon=timeout_count,
            inclusion_rate=1-timeout_count/len(generated),horizon_s=horizon,
            duplicate_generation_rows=duplicates,raw_source=run['raw_source']))
    costs, seeds = [1,2,5], [200,201]
    assert len({(r['protocol'],r['cost_multiplier'],r['seed']) for r in records}) == 12
    groups = []
    fig,ax = plt.subplots(figsize=(7.6,4.9),constrained_layout=True)
    for protocol in ['trail','ersoy_fee']:
        sample = [r for r in records if r['protocol']==protocol]
        color,marker,line = STYLES[protocol]
        for seed in seeds:
            values = [next(r['restricted_mean_s'] for r in sample if r['cost_multiplier']==cost and r['seed']==seed) for cost in costs]
            ax.plot(costs,values,color=color,alpha=.22,linestyle=line,linewidth=.8)
        means = []
        for cost in costs:
            selected = [r for r in sample if r['cost_multiplier']==cost]
            mean,ci = interval(r['restricted_mean_s'] for r in selected)
            means.append(mean)
            groups.append(dict(protocol=protocol,mechanism=sample[0]['mechanism'],cost_multiplier=cost,
                               n_seeds=2,restricted_mean_s_mean=mean,restricted_mean_s_ci95=ci))
        ax.plot(costs,means,color=color,marker=marker,markerfacecolor='white',linestyle=line,
                linewidth=2,label=sample[0]['mechanism'])
    ax.set_xscale('log')
    ax.set_xticks(costs,[f'{cost}x' for cost in costs])
    ax.minorticks_off()
    ymax = math.ceil(10*1.15*max(r['restricted_mean_s'] for r in records))/10
    ax.set(title='Transaction inclusion time vs forwarding cost',
           xlabel='Forwarding cost multiplier (log scale)',
           ylabel='Restricted mean inclusion time (s)',ylim=(0,ymax))
    ax.grid(axis='y',alpha=.2)
    ax.spines[['top','right']].set_visible(False)
    ax.legend(loc='lower left',frameon=False)
    fig.supxlabel('F=0.001; Full eta=0.5, theta=0.25; Ersoy C=0.25.\n'
                  'Transactions generated in epochs 100-189; late/missing inclusions count as 50 s.\n'
                  'Thick: two-seed means; thin: individual seeds. Seconds are logical time.',fontsize=8.5)
    for extension in ['png','pdf']:
        fig.savefig(destination/f'cost_inclusion_time.{extension}',dpi=220)
    plt.close(fig)
    write_csv(destination/'inclusion_time_runs.csv',records)
    write_csv(destination/'inclusion_time_groups.csv',groups)
    (destination/'inclusion_time_audit.json').write_text(json.dumps(dict(
        input_csv=str(source.relative_to(ROOT)),input_csv_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        runs_checked=12,cohort_created_slots_inclusive=[500,949],horizon_s=50,
        max_restricted_mean_reconstruction_error_s=maximum_mean_error,
        max_logged_delay_reconstruction_error_s=maximum_logged_delay_error,
        duplicate_handling='Each transaction hash uses its first eligible generation and first inclusion record.',
        estimand='Per-seed mean of min(first-inclusion delay, 50 s), assigning 50 s to missing inclusions; then equal-weight mean across seeds.',
        scope='Existing pilot logs only; no new simulations. Two seeds do not establish a stable population-level difference.'
    ),indent=2)+'\n')
    for row in groups:
        print(f"cost={row['cost_multiplier']}x {row['mechanism']} restricted_mean={row['restricted_mean_s_mean']:.6f}s")
    print(destination/'cost_inclusion_time.png')


if __name__ == '__main__':
    main()
