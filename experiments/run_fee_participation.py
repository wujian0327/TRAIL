#!/usr/bin/env python3
"""Archive, run and validate a fixed-g fee-participation comparison."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run_experiments import ROOT, expand_runs, load_yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--batch-dir', type=Path, required=True)
    parser.add_argument('--max-parallel', type=int, default=3)
    args = parser.parse_args()
    batch = (ROOT / args.batch_dir).resolve()
    batch.mkdir(parents=True, exist_ok=False)
    spec = load_yaml(ROOT / args.config)
    binary = batch / 'trail'
    shutil.copy2(ROOT / spec['binary'], binary)
    spec['binary'] = str(binary)
    spec['max_parallel'] = args.max_parallel
    config = batch / 'config.yaml'
    config.write_text(json.dumps(spec, indent=2)+'\n')
    shutil.copytree(ROOT / 'src', batch / 'source/src')
    for name in ['Cargo.toml','Cargo.lock']:
        shutil.copy2(ROOT / name, batch / 'source' / name)
    shutil.copytree(ROOT / 'experiments/configs', batch / 'source/experiments/configs')
    shutil.copytree(ROOT / 'experiments/golden', batch / 'source/experiments/golden')
    for name in ['run_experiments.py','fee_participation_report.py','ersoy_participation_report.py',
                 'adaptive_participation_report.py','adaptive_estimator_audit.py',
                 'trail_compat.py','run_fee_participation.py']:
        shutil.copy2(ROOT / 'experiments' / name, batch / 'source/experiments' / name)
    diff = subprocess.check_output(['git','diff','--binary','HEAD','--','src'], cwd=ROOT)
    (batch / 'source.patch').write_bytes(diff)
    timestamp = lambda: datetime.now(timezone.utc).isoformat()
    runs = expand_runs(spec)
    fees = sorted({r['transaction_fee'] for r in runs})
    state = dict(status='running', pid=os.getpid(), expected_runs=len(runs),
                 seeds=spec['seeds'], fees=fees,
                 irrecoverable_cost=spec['defaults']['irrecoverable_cost'],
                 initial_liquid_balance=spec['defaults']['initial_liquid_balance'],
                 initial_liquid_balance_by_fee={str(f):sorted({r['initial_liquid_balance'] for r in runs if r['transaction_fee']==f}) for f in fees},
                 config=str(config), output=str(ROOT / 'results/processed' / spec['suite']),
                 binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                 source_patch_sha256=hashlib.sha256(diff).hexdigest(), started_at_utc=timestamp())
    def save():
        temporary = batch / 'manifest.tmp'
        temporary.write_text(json.dumps(state,indent=2)+'\n')
        temporary.replace(batch / 'manifest.json')
    save()
    try:
        subprocess.run([sys.executable,'-u',str(ROOT / 'experiments/run_experiments.py'),
                        '--config',str(config),'--no-build','--max-parallel',str(args.max_parallel)], cwd=ROOT, check=True)
        state['status'] = 'validating'
        save()
        current = subprocess.check_output(['git','diff','--binary','HEAD','--','src'], cwd=ROOT)
        if current != diff:
            raise RuntimeError('simulator source changed during batch')
        subprocess.run([sys.executable,str(ROOT / 'experiments/fee_participation_report.py'),
                        '--config',str(config)], cwd=ROOT, check=True)
        state['status'] = 'complete'
    except BaseException as error:
        state.update(status='failed', error=str(error))
        raise
    finally:
        state['updated_at_utc'] = timestamp()
        save()


if __name__ == '__main__':
    main()
