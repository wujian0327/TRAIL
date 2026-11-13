#!/usr/bin/env python3
"""Run fixed-g fee probes with separate validated reports for each cost scale."""

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from run_experiments import ROOT, expand_runs, load_yaml


def cost_specs(spec):
    if len(spec['experiments']) != 1:
        raise ValueError('cost probe expects one experiment matrix')
    costs = sorted({r['adaptive_cost_median_multiplier'] for r in expand_runs(spec)})
    for cost in costs:
        child = copy.deepcopy(spec)
        label = f"cost{cost:g}".replace('.', 'p')
        child['suite'] = f"{spec['suite']}_{label}"
        child['experiments'][0]['adaptive_cost_median_multiplier'] = [cost]
        fee_points = spec.get('fee_points_by_cost')
        if fee_points is not None:
            child['experiments'][0]['transaction_fee'] = fee_points[f'{cost:g}']
        yield cost, label, child


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('experiments/configs/trail_fee_participation_cost_probe.yaml'))
    parser.add_argument('--batch-dir', type=Path, default=Path('results/batches/fixed-g-cost-probe-v1'))
    parser.add_argument('--max-parallel', type=int, default=3)
    parser.add_argument('--postprocess', type=Path, help='Python report script to run after all cost batches validate')
    args = parser.parse_args()
    if args.max_parallel < 1:
        parser.error('--max-parallel must be positive')
    spec = load_yaml(ROOT / args.config)
    frozen_inputs = spec.get('frozen_inputs', {})
    def check_frozen_inputs():
        for relative,expected in frozen_inputs.items():
            path = ROOT/relative
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise RuntimeError(f'frozen analysis/input changed: {relative}')
    check_frozen_inputs()
    plans = list(cost_specs(spec))
    all_runs = [r for _, _, child in plans for r in expand_runs(child)]
    if len({r['output_dir'] for r in all_runs}) != len(all_runs):
        raise ValueError('duplicate output directory')
    if any(Path(r['output_dir']).exists() for r in all_runs):
        raise ValueError('probe output already exists; use a new suite and batch directory')
    batch = (ROOT / args.batch_dir).resolve()
    batch.mkdir(parents=True, exist_ok=False)
    shutil.copy2(Path(__file__), batch / 'run_fee_cost_probe.py')
    (batch / 'config.yaml').write_text(json.dumps(spec, indent=2)+'\n')
    for relative in frozen_inputs:
        target = batch/'frozen_inputs'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/relative,target)
    source_diff = subprocess.check_output(['git', 'diff', '--binary', 'HEAD', '--', 'src'], cwd=ROOT)
    binary_hash = lambda: hashlib.sha256((ROOT / spec['binary']).read_bytes()).hexdigest()
    timestamp = lambda: datetime.now(timezone.utc).isoformat()
    state = dict(status='running', pid=os.getpid(), expected_runs=len(all_runs),
                 max_parallel=args.max_parallel, completed_runs=0, running_runs=0, failed_runs=0,
                 fees=sorted({r['transaction_fee'] for r in all_runs}),
                 cost_multipliers=[cost for cost, _, _ in plans], seeds=spec['seeds'],
                 binary_sha256=binary_hash(), started_at_utc=timestamp(), children=[],
                 behavior_estimator=spec.get('behavior_estimator', 'legacy_ratio_ema'),
                 scope='Shared group-benefit behavioral model; estimator version recorded separately. Not individual causal incentive validation.')
    state['frozen_input_count'] = len(frozen_inputs)
    for field in ['reused_runs','analysis_seeds','total_analysis_runs','postprocess_output']:
        if field in spec:
            state[field] = spec[field]
    if args.postprocess:
        report = (ROOT / args.postprocess).resolve()
        shutil.copy2(report, batch / 'postprocess.py')
        state['postprocess'] = str(report)
        state['postprocess_sha256'] = hashlib.sha256(report.read_bytes()).hexdigest()

    def save():
        counts = dict(ok=0, running=0, failed=0, timeout=0)
        for run in all_runs:
            try:
                status = json.loads((Path(run['output_dir']) / 'runner_status.json').read_text())['status']
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            counts[status] = counts.get(status, 0)+1
        state.update(completed_runs=counts['ok'], running_runs=counts['running'],
                     failed_runs=counts['failed']+counts['timeout'], updated_at_utc=timestamp())
        temp = batch / 'manifest.tmp'
        temp.write_text(json.dumps(state, indent=2)+'\n')
        temp.replace(batch / 'manifest.json')

    save()
    try:
        for cost, label, child in plans:
            check_frozen_inputs()
            if binary_hash() != state['binary_sha256'] or subprocess.check_output(
                    ['git', 'diff', '--binary', 'HEAD', '--', 'src'], cwd=ROOT) != source_diff:
                raise RuntimeError('simulator binary or source changed during probe')
            config = batch / f'config_{label}.yaml'
            config.write_text(json.dumps(child, indent=2)+'\n')
            info = dict(cost_multiplier=cost, status='running', batch=str(batch / label),
                        output=str(ROOT / 'results/processed' / child['suite']))
            state['children'].append(info)
            state['current_cost_multiplier'] = cost
            save()
            print(f'Starting {label}: {len(expand_runs(child))} runs', flush=True)
            process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'experiments/run_fee_participation.py'),
                                        '--config', str(config), '--batch-dir', str(batch / label),
                                        '--max-parallel', str(args.max_parallel)], cwd=ROOT)
            while process.poll() is None:
                save()
                time.sleep(10)
            if process.returncode:
                info['status'] = 'failed'
                raise RuntimeError(f'{label} runner exited with {process.returncode}')
            info['status'] = 'complete'
            save()
        if state['completed_runs'] != state['expected_runs'] or state['failed_runs']:
            raise RuntimeError('probe did not complete every configured run')
        if args.postprocess:
            state['status'] = 'reporting'
            save()
            check_frozen_inputs()
            if hashlib.sha256(report.read_bytes()).hexdigest() != state['postprocess_sha256']:
                raise RuntimeError('postprocess script changed during probe')
            subprocess.run([sys.executable, str(report)], cwd=ROOT, check=True)
        state['status'] = 'complete'
    except BaseException as error:
        state.update(status='failed', error=str(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
