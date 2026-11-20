#!/usr/bin/env python3
"""Post-measurement offline audits; preserve raw epoch-boundary warnings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def epoch_boundary_only(summary):
    formal=summary['formal_experiment']
    failed=[check for check in formal['acceptance']['checks'] if not check['passed']]
    if len(failed)!=1 or failed[0]['name']!='geth-relay-epoch-current' or not formal['measurement_quality']['passed']:
        return None
    slots=summary['formal_experiment']['spec']['slots_per_epoch']
    head=summary['beacon_after']['head_slot']
    nodes=summary.get('geth_trail_status',[])
    expected=head//slots
    # A deliberately narrow exception: the saved Beacon head is the last slot
    # of an epoch and ALL EL nodes have advanced exactly one epoch together.
    if head%slots!=slots-1 or len(nodes)!=summary['formal_experiment']['spec']['nodes']:
        return None
    if not all(node['relay_epoch']==expected+1 and node['dynamic_relay_epoch'] for node in nodes):
        return None
    return dict(reason='Beacon snapshot at epoch last slot; all Geth nodes sampled later in the next epoch',
                beacon_head_slot=head,expected_epoch=expected,observed_epochs=[n['relay_epoch'] for n in nodes],
                raw_failed_check=failed[0],all_other_acceptance_checks_passed=True,measurement_quality_passed=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--wait',action='store_true',help='Wait for the existing measured batch; never starts any new run')
    args=parser.parse_args()
    manifest=json.loads((args.root/'manifest.json').read_text())
    while args.wait and manifest['status']=='running':
        time.sleep(10)
        manifest=json.loads((args.root/'manifest.json').read_text())
    if any(run['status'] in ['pending','running'] for run in manifest['runs']):
        raise RuntimeError('Run offline audits after ALL timed experiments have finished')
    from sized_devnet_audit import audit_sized_run
    env=dict(os.environ,GOCACHE=str(ROOT/'target/devnet-r1c1/go-cache'),GOTOOLCHAIN='local',GOPROXY='off')
    binary=ROOT/'target/devnet-r1c1/audit-devnet-path-certificates'
    source=ROOT/'experiments/audit_devnet_path_certificates.go'
    subprocess.run(['go','build','-o',str(binary),str(source)],cwd=ROOT/'ethereum-trail/go-ethereum-trail',env=env,check=True)
    results=[]
    for run in manifest['runs']:
        folder=args.root/run['run_id'];path=folder/'summary.json'
        summary=json.loads(path.read_text())
        transaction_audit=folder/'transaction_audit.json'
        if not transaction_audit.exists():
            audit_sized_run(folder,summary,manifest['config']['raw_tx_target_bytes'])
        assert json.loads(transaction_audit.read_text())['passed']
        crypto=True
        if run['variant']=='trail':
            audit=folder/'certificate_offline_audit.json'
            subprocess.run([str(binary),'--summary',str(path),'--registry',str(args.root/'registry_public_n8.json'),'--output',str(audit)],check=True)
            crypto=json.loads(audit.read_text())['passed']
        observation=epoch_boundary_only(summary) if run['status']=='failed' else None
        accepted=crypto and (run['status']=='ok' or observation is not None)
        result=dict(run_id=run['run_id'],raw_runner_status=run['status'],measurement_accepted=accepted,
                    transaction_audit_passed=True,offline_certificate_audit_passed=crypto if run['variant']=='trail' else None,
                    observation_adjudication=observation,summary_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    audit_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        (folder/'post_measurement_validation.json').write_text(json.dumps(result,indent=2)+'\n')
        results.append(result)
    (args.root/'post_measurement_validation.json').write_text(json.dumps(dict(passed=all(r['measurement_accepted'] for r in results),runs=results),indent=2)+'\n')
    subprocess.run([sys.executable,str(ROOT/'experiments/report_r1_1_540b_performance.py'),'--root',str(args.root)],check=True)
    if not all(r['measurement_accepted'] for r in results):
        raise RuntimeError('Some measurements failed the independent validation; see post_measurement_validation.json')


if __name__=='__main__':main()
