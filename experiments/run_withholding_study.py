#!/usr/bin/env python3
"""Run pilot, gate on its audit, then run the full independent withholding study."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments'))
from run_experiments import expand_runs,load_yaml


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir',type=Path)
    args=parser.parse_args()
    batch=args.batch_dir or ROOT/'results/batches'/('selective-withholding-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    batch=batch.resolve();batch.mkdir(parents=True,exist_ok=True)
    manifest=read(batch/'manifest.json')
    if manifest.get('stage')=='rejected_serial_transport' or manifest.get('automatic_main_disabled'):
        raise RuntimeError('This serial-transport batch was rejected by the user and cannot be resumed')
    if not manifest:
        binary=ROOT/load_yaml(ROOT/'experiments/configs/trail_withholding_pilot.yaml')['binary']
        if not binary.exists():raise RuntimeError('Build and test the withholding binary first')
        shutil.copy2(binary,batch/'trail')
        for phase in ['pilot','main']:
            spec=load_yaml(ROOT/f'experiments/configs/trail_withholding_{phase}.yaml')
            spec['binary']=str(batch/'trail');spec['build_command']=[]
            (batch/(phase+'.yaml')).write_text(json.dumps(spec,indent=2)+'\n')
        shutil.copy2(ROOT/'experiments/trail_withholding_report.py',batch/'report.py')
        sources=[ROOT/'Cargo.toml',ROOT/'Cargo.lock',ROOT/'experiments/run_experiments.py',
                 ROOT/'experiments/trail_withholding_report.py',ROOT/'experiments/run_withholding_study.py',
                 ROOT/'experiments/check_withholding_parallel.py',
                 ROOT/'experiments/configs/protocol_frozen_v1.yaml',ROOT/'docs/SELECTIVE_WITHHOLDING.md']
        sources+=sorted((ROOT/'src').rglob('*.rs'))
        with tarfile.open(batch/'source_snapshot.tar.gz','w:gz') as archive:
            for path in sources:archive.add(path,arcname=str(path.relative_to(ROOT)))
        legacy={str(p):sha(p) for p in [ROOT/'target/release/trail',ROOT/'target/coalition-favoritism-v2/release/trail'] if p.exists()}
        manifest=dict(created_at=datetime.now().astimezone().isoformat(),status='prepared',stage='prepared',
            batch_dir=str(batch),binary=str(batch/'trail'),binary_sha256=sha(batch/'trail'),
            pinned_report_sha256=sha(batch/'report.py'),legacy_binaries=legacy,
            source_snapshot=str(batch/'source_snapshot.tar.gz'),source_snapshot_sha256=sha(batch/'source_snapshot.tar.gz'),
            runner_sha256=sha(ROOT/'experiments/run_experiments.py'),max_parallel=4,
            planned_pilot_runs=18,planned_main_runs=120,total_planned_runs=138)
    specs={phase:load_yaml(batch/(phase+'.yaml')) for phase in ['pilot','main']}
    manifest['max_parallel']=max(spec['max_parallel'] for spec in specs.values())
    manifest['node_workers_per_run']=specs['pilot']['defaults']['withholding_node_workers']
    manifest['execution_model']='parallel-node-inboxes-v2'
    matrices={phase:expand_runs(spec) for phase,spec in specs.items()}
    started=time.time();child=None
    def save(status=None,stage=None,**extra):
        if status:manifest['status']=status
        if stage:manifest['stage']=stage
        manifest.update(updated_at=datetime.now().astimezone().isoformat(),session_elapsed_seconds=round(time.time()-started,1),**extra)
        progress={}
        for phase,runs in matrices.items():
            records=[]
            for run in runs:
                directory=Path(run['output_dir']);state=read(directory/'runner_status.json');summary=read(directory/'run_summary.json')
                records.append(dict(run_id=run['run_id'],seed=run['seed_value'],placement=run['adversary_placement'],
                    probability=run['withholding_probability'],status=state.get('status','pending'),
                    completed_epochs=min(run['max_epochs'],summary.get('completed_epochs',0)),expected_epochs=run['max_epochs'],
                    node_workers=run['withholding_node_workers'],parallel_node_peak=summary.get('parallel_node_peak',0),
                    duration_seconds=state.get('duration_seconds'),output_dir=str(directory)))
            counts=Counter(r['status'] for r in records)
            durations=[r['duration_seconds'] for r in records if r['status']=='ok' and r['duration_seconds']]
            average=sum(durations)/len(durations) if durations else None
            remaining=sum(1-r['completed_epochs']/r['expected_epochs'] if r['status']=='running' else 1
                          for r in records if r['status']!='ok')
            progress[phase]=dict(total=len(records),completed=counts['ok'],running=counts['running'],pending=counts['pending'],
                failed=counts['failed']+counts['timeout'],average_run_seconds=average,
                estimated_remaining_seconds=round(average*remaining/specs[phase]['max_parallel']) if average is not None else None,runs=records)
        manifest['progress']=progress
        manifest['completed_runs']=sum(p['completed'] for p in progress.values())
        temp=batch/'manifest.tmp';temp.write_text(json.dumps(manifest,indent=2)+'\n');temp.replace(batch/'manifest.json')
    def command(label,argv):
        nonlocal child
        save('running',label,command=argv,log=str(batch/(label+'.log')))
        env=os.environ.copy();env.update(CARGO_NET_OFFLINE='true',PYTHONUNBUFFERED='1',
            PYTHONPATH=str(ROOT/'experiments'),TRAIL_LOG_LEVEL='off',OPENBLAS_NUM_THREADS='1')
        with (batch/(label+'.log')).open('w') as log:
            child=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            while child.poll() is None:
                save(child_pid=child.pid);time.sleep(3)
            code=child.returncode;child=None
        if code:raise RuntimeError(f'{label} exited {code}; see {batch/(label+".log")}')
        save()
    try:
        assert sha(batch/'trail')==manifest['binary_sha256']
        assert sha(batch/'report.py')==manifest['pinned_report_sha256']
        save('running','starting',supervisor_pid=os.getpid())
        check_dir=batch/'parallel_preflight'
        checked=read(check_dir/'validation.json')
        if not (checked.get('passed') and checked.get('binary_sha256')==manifest['binary_sha256']):
            if check_dir.exists():
                archived=batch/('parallel_preflight_interrupted_'+datetime.now().strftime('%Y%m%d-%H%M%S'))
                check_dir.rename(archived)
            command('parallel_preflight',[sys.executable,'experiments/check_withholding_parallel.py',
                '--binary',str(batch/'trail'),'--output-dir',str(check_dir)])
            checked=read(check_dir/'validation.json')
        assert checked['passed'] and checked['exact_outcomes_across_parallelism']
        manifest['parallel_preflight']=str(check_dir/'validation.json')
        for phase in ['pilot','main']:
            assert sha(ROOT/'experiments/run_experiments.py')==manifest['runner_sha256'],'runner changed after source freeze'
            all_complete=all(read(Path(run['output_dir'])/'runner_status.json').get('status')=='ok'
                and read(Path(run['output_dir'])/'run_summary.json').get('completed_epochs')==run['max_epochs']
                and (Path(run['output_dir'])/'run_config.json').exists() for run in matrices[phase])
            if all_complete:
                manifest.setdefault('completed_stages_reused',{})[phase]=len(matrices[phase])
                save('running',phase+'_audit_existing_runs')
            else:
                # Preserve interrupted evidence before a fresh replay, never --force.
                for run in matrices[phase]:
                    directory=Path(run['output_dir']);state=read(directory/'runner_status.json')
                    if directory.exists() and state.get('status')!='ok':
                        archive=batch/'interrupted'/datetime.now().strftime('%Y%m%d-%H%M%S');archive.mkdir(parents=True,exist_ok=True)
                        directory.rename(archive/directory.name)
                command(phase+'_dry_run',[sys.executable,'scripts/task.py','run-config','--config',str(batch/(phase+'.yaml')),'--dry-run'])
                command(phase+'_runs',[sys.executable,'scripts/task.py','run-config','--config',str(batch/(phase+'.yaml'))])
            command(phase+'_report',[sys.executable,str(batch/'report.py'),'--config',str(batch/(phase+'.yaml'))])
            processed=ROOT/'results/processed'/specs[phase]['suite']
            validation=read(processed/'validation.json')
            assert validation['runs']==len(matrices[phase]) and validation['warmup_exact'] and not validation['validation_errors']
            if phase=='pilot':
                assert validation['bound_violations']==0 and validation['stake_premise_violating_epochs']==0,'pilot safety diagnostic requires investigation'
            manifest[phase+'_processed_dir']=str(processed)
            manifest[phase+'_validation']=validation
            # Standalone two-column appendix preview; the paper source is untouched.
            wrapper=batch/(phase+'_appendix_preview.tex')
            wrapper.write_text('\\documentclass[twocolumn]{article}\n\\begin{document}\n\\input{'+str(processed/'appendix_table.tex')+'}\n\\end{document}\n')
            command(phase+'_table_preview',['pdflatex','-interaction=nonstopmode','-halt-on-error',
                '-output-directory',str(processed),str(wrapper)])
            save()
        assert all(sha(Path(path))==digest for path,digest in manifest['legacy_binaries'].items())
        save('complete','complete',legacy_binaries_unchanged=True,completed_at=datetime.now().astimezone().isoformat())
    except BaseException as error:
        if child is not None:child.terminate()
        save('failed',error=str(error))
        raise


if __name__=='__main__':main()
