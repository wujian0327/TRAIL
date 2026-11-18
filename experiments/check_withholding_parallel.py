#!/usr/bin/env python3
"""Verify real node concurrency and exact outcomes with two vs eight workers."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments'))
from run_experiments import expand_runs,load_yaml


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    destination=args.output_dir or ROOT/'results/batches'/('withholding-parallel-check-'+stamp)
    destination=destination.resolve();destination.mkdir(parents=True,exist_ok=False)
    base=load_yaml(ROOT/'experiments/configs/trail_withholding_pilot.yaml')
    binary=(args.binary or ROOT/base['binary']).resolve()
    assert binary.is_file()
    groups={};peaks={};durations={}
    env=os.environ.copy();env.update(CARGO_NET_OFFLINE='true',TRAIL_LOG_LEVEL='off',OPENBLAS_NUM_THREADS='1')
    for workers in [2,8]:
        spec=json.loads(json.dumps(base));spec['build_command']=[];spec['binary']=str(binary)
        spec['suite']='withholding_parallel_check_'+stamp+f'_w{workers}';spec['seeds']=[0,1]
        spec['max_parallel']=4
        spec['defaults'].update(run_revision='parallel-check-'+stamp,node_num=12,stake_gini=0.,
            tx_rate=5,max_epochs=4,warmup_epochs=1,withholding_start_epoch=1,withholding_end_epoch=3,
            withholding_node_workers=workers)
        config=destination/f'workers{workers}.yaml';config.write_text(json.dumps(spec,indent=2)+'\n')
        for stage,command in [
            ('runs',[sys.executable,'scripts/task.py','run-config','--config',str(config)]),
            ('audit',[sys.executable,'experiments/trail_withholding_report.py','--config',str(config)])]:
            with (destination/f'workers{workers}_{stage}.log').open('w') as log:
                result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            if result.returncode:raise RuntimeError(f'{workers} workers: {stage} failed; see {destination}')
        groups[workers]={};peaks[workers]=[];durations[workers]=[]
        for run in expand_runs(spec):
            key=run['adversary_placement'],run['seed_value'],run['withholding_probability']
            directory=Path(run['output_dir'])
            summary=json.loads((directory/'run_summary.json').read_text())
            assert 1 < summary['parallel_node_peak'] <= workers
            peaks[workers].append(summary['parallel_node_peak'])
            durations[workers].append(json.loads((directory/'runner_status.json').read_text())['duration_seconds'])
            groups[workers][key]=directory
        print(f'{workers}-worker runs and full audit passed; observed concurrency peaks: {peaks[workers]}',flush=True)
    evidence=[]
    for key in sorted(groups[2]):
        a,b=groups[2][key],groups[8][key]
        for name in ['withholding_epochs.jsonl','withholding_epochs.csv','withholding_states.jsonl',
                     'withholding_slots.jsonl','blocks.jsonl','background_transactions.jsonl',
                     'withholding_decisions.jsonl','graph.json','initial_stakes.json','coalition.json']:
            h=digest(a/name)
            if h!=digest(b/name):raise ValueError(f'parallelism changed outcome: {key}, {name}')
            evidence.append(dict(placement=key[0],seed=key[1],probability=key[2],file=name,sha256=h))
    validation=dict(passed=True,concurrent_workers=[2,8],runs=24,matched_scenarios=12,
        identical_core_files=len(evidence),exact_outcomes_across_parallelism=True,
        observed_concurrency_peaks=peaks,run_durations_seconds=durations,
        binary=str(binary),binary_sha256=digest(binary),evidence=evidence)
    (destination/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    print('PASS: actual concurrent node processing and byte-identical core outcomes across worker counts.',flush=True)
    print(destination,flush=True)


if __name__=='__main__':main()
