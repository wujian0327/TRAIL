#!/usr/bin/env python3
"""One small before/after devnet smoke; preserves all existing images and results."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments'))
import run_frozen_devnet_experiments as devnet
from trail_devnet_runner import decoded_bytes
from run_frozen_devnet_smoke import collect_build_provenance,command_env


class SmokeSpec(devnet.RunSpec):
    @property
    def enclave(self):return self.suite


def rpc(endpoint,method,params):
    request=urllib.request.Request(endpoint,data=json.dumps(dict(jsonrpc='2.0',id=1,method=method,params=params)).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=30) as response:payload=json.load(response)
    if 'error' in payload:raise RuntimeError(payload['error'])
    return payload['result']


def fetch(url):
    with urllib.request.urlopen(url,timeout=30) as response:return json.load(response)


def audit_live_certificates(spec,summary,directory,phase):
    cl=summary['endpoints']['cl_apis'][0]
    endpoints=summary['endpoints']['el_rpcs']
    audits=[]
    for block in summary['blocks']['audit_rows']:
        slot=block['slot'];message=fetch(f'{cl}/eth/v2/beacon/blocks/{slot}')['data']['message']
        body=message['body'];execution=body.get('execution_payload') or body.get('executionPayload')
        onchain=body.get('trail_evidence_records') or body.get('trailEvidenceRecords') or []
        proposer=int(message['proposer_index'])
        evidence=rpc(endpoints[proposer],'trail_getBlockEvidence',[execution['block_hash']])
        transactions={tx['tx_hash'].lower():tx for tx in evidence.get('transactions',[])}
        hashes={record['tx_hash'].lower() for record in onchain}
        assert len(hashes)==len(onchain),'duplicate on-chain transaction evidence'
        for record in onchain:
            tx=transactions[record['tx_hash'].lower()]
            signature=tx.get('aggregate_signature') if phase=='after' else evidence.get('aggregate_signature')
            assert signature and decoded_bytes(record['aggregate_signature'],48)==decoded_bytes(signature,48)
        if phase=='after':
            assert evidence['signature_scheme']=='per_path_v1'
            assert 'aggregate_signature' not in evidence
            assert len({decoded_bytes(r['aggregate_signature'],48) for r in onchain})==len(onchain)
        audits.append(dict(slot=slot,proposer_index=proposer,execution_block_hash=execution['block_hash'],
            transaction_count=len(execution['transactions']),onchain_certificates=len(onchain),
            distinct_signatures=len({decoded_bytes(r['aggregate_signature'],48) for r in onchain}),
            el_certificates_not_carried=[dict(tx_hash=h,reason='CL count/work cap or explicit construction rejection') for h in sorted(set(transactions)-hashes)],
            geth_uncertified_transactions=evidence.get('uncertified_transactions',[]),geth_evidence=evidence))
    (directory/'certificate_audit.json').write_text(json.dumps(audits,indent=2)+'\n')
    return audits


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['before','after'],required=True)
    parser.add_argument('--root',type=Path,default=ROOT/'results/raw/devnet_per_path_r1c1_20261118')
    parser.add_argument('--package',type=Path,default=Path('/home/wujian/ethereum-package'))
    parser.add_argument('--geth-image',required=True);parser.add_argument('--lighthouse-image',required=True)
    args=parser.parse_args();args.root=args.root.resolve()
    # Local client RPC must not inherit a desktop HTTP proxy configuration.
    bypass=[]
    for key in ['NO_PROXY','no_proxy']:
        bypass.extend(item.strip() for item in os.environ.get(key,'').split(',') if item.strip())
    bypass.extend(['localhost','127.0.0.1','::1'])
    for key in ['NO_PROXY','no_proxy']:os.environ[key]=','.join(dict.fromkeys(bypass))
    public=args.root/'registry_public.json';private=args.root/'registry_private.json'
    if not public.exists():
        devnet.run([str(ROOT/'target/release/trail_relay_registry'),'--count','4','--public-out',str(public),'--private-out',str(private)])
    spec=SmokeSpec(suite='trail-per-path-'+args.phase+'-20261118',variant='trail',nodes=4,load_tx_per_slot=16,
        topology='ring',seed=0,validators_per_node=1,seconds_per_slot=3,slots_per_epoch=8,
        warmup_epochs=5,measurement_epochs=2,resource_interval=1.,receipt_timeout=180,finality_timeout=600,
        send_concurrency=16,receipt_concurrency=16,prometheus_scrape_interval=1.)
    output=args.root/args.phase;folder=output/spec.run_id
    if folder.exists():raise RuntimeError('Smoke output already exists; choose a fresh root instead of overwriting')
    original_generate=devnet.generate_args
    def generate(spec,public,private,path):
        original_generate(spec,public,private,path)
        text=path.read_text()
        assert text.count('el_image: trail/geth:dev')==text.count('cl_image: trail/lighthouse:dev')==1
        path.write_text(text.replace('el_image: trail/geth:dev','el_image: '+args.geth_image)
                            .replace('cl_image: trail/lighthouse:dev','cl_image: '+args.lighthouse_image))
    devnet.generate_args=generate
    provenance=collect_build_provenance(args.package)
    provenance['images']={}
    for tag in [args.geth_image,args.lighthouse_image]:
        image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
        provenance['images'][tag]=dict(id=image['Id'],created=image['Created'],labels=image['Config'].get('Labels'))
    namespace=SimpleNamespace(output_root=output,package=args.package,resume=False,keep_enclaves=True)
    started=time.time()
    try:
        status,error=devnet.execute_run(spec,namespace,provenance,public,private)
        if status!='ok':raise RuntimeError(error)
        summary=json.loads((folder/'summary.json').read_text())
        audits=audit_live_certificates(spec,summary,folder,args.phase)
        windows=summary['workload']['measurement_window'];blocks=summary['blocks']['audit_rows']
        pm=summary['formal_experiment']['prometheus'];resources=summary['formal_experiment']['resources']
        sys.path.insert(0,str(ROOT/'analysis'))
        from audit_r1_1_performance import histogram_quantiles
        lat=histogram_quantiles(pm['evidence_verify_bucket_deltas'],pm['evidence_verify_count'])
        total=sum(b['block_transaction_count'] for b in blocks);carried=sum(b['evidence_record_count'] for b in blocks)
        result=dict(passed=True,phase=args.phase,scope='4-node 16-tx/slot smoke; one paired seed, not a formal performance matrix',
            elapsed_seconds=time.time()-started,formal_window=windows,ssz_mean_bytes=sum(b['block_ssz_bytes'] for b in blocks)/len(blocks),
            evidence_payload_mean_bytes=sum(b['evidence_ssz_bytes'] for b in blocks)/len(blocks),
            block_transactions=total,certificates=carried,evidence_coverage=carried/total if total else None,
            missed_slots=summary['blocks']['missed_slots'],verification=lat,
            verify_mean_ms=pm['evidence_verify_mean_seconds']*1000,
            multi_transaction_blocks=sum(a['onchain_certificates']>1 for a in audits),
            max_distinct_signatures=max(a['distinct_signatures'] for a in audits),
            geth_uncertified_count=sum(len(a['geth_uncertified_transactions']) for a in audits),
            cl_omissions=sum(len(a['el_certificates_not_carried']) for a in audits),resources=resources,
            summary_path=str(folder/'summary.json'),images=provenance['images'])
        assert result['multi_transaction_blocks']>0 and result['certificates']>0
        (args.root/(args.phase+'_validation.json')).write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2),flush=True)
    finally:
        devnet.remove_enclave(spec.enclave)


if __name__=='__main__':main()
