#!/usr/bin/env python3
"""Audit retained devnet artifacts and local BLS samples without running clients."""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import struct

from scipy.stats import binom, t

ROOT = Path(__file__).resolve().parents[1]
FORMAL_SUITES = ('trail_devnet_txslot_main', 'trail_devnet_512_main')
LOADS = (64,128,256,512)
PRIMARY_OPS = ('construct_path','aggregate_only','verify_individual','verify_aggregate_cold')


def read(path): return json.loads(path.read_text())
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def close(a,b,label):
    if not math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-7):
        raise ValueError(f'{label}: recomputed {a}, stored {b}')

def write_csv(path,rows):
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})

def mean_ci(values):
    values=list(values)
    if not values:return None,None
    return statistics.fmean(values),float(t.ppf(.975,len(values)-1))*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>1 else None


def quantile(values,q):
    values=sorted(values);rank=q*(len(values)-1);lo=int(rank);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(rank-lo)


def histogram_quantiles(buckets,expected_count):
    if not buckets:
        if expected_count != 0:raise ValueError('missing histogram with nonzero count')
        return dict(histogram_valid=True,sample_count=0,p95_estimate_ms=None,p99_estimate_ms=None)
    boundaries=[math.inf if str(r['le'])=='+Inf' else float(r['le']) for r in buckets]
    counts=[float(r['count']) for r in buckets]
    if boundaries!=sorted(set(boundaries)) or boundaries[-1]!=math.inf or any(not math.isfinite(b) or b<=0 for b in boundaries[:-1]):
        raise ValueError('invalid histogram boundaries')
    if any(not math.isfinite(c) or c<0 or abs(c-round(c))>1e-6 for c in counts):
        raise ValueError('invalid histogram counts')
    if any(b<a for a,b in zip(counts,counts[1:])):raise ValueError('nonmonotone cumulative histogram')
    close(counts[-1],expected_count,'histogram +Inf vs event counter')
    result=dict(histogram_valid=True,sample_count=int(round(counts[-1])),histogram_bucket_count=len(buckets))
    for label,q in [('p95',.95),('p99',.99)]:
        estimate=None;lower=upper=None;previous_boundary=previous_count=0.
        if counts[-1]:
            for boundary,count in zip(boundaries,counts):
                if count>=q*counts[-1]:
                    lower=previous_boundary*1000;upper=boundary*1000 if math.isfinite(boundary) else None
                    if math.isfinite(boundary):
                        estimate=1000*(previous_boundary+(q*counts[-1]-previous_count)/(count-previous_count)*(boundary-previous_boundary))
                    break
                previous_boundary,previous_count=boundary,count
        result[label+'_estimate_ms']=estimate
        result[label+'_bucket_lower_ms']=lower
        result[label+'_bucket_upper_ms']=upper
        result[label+'_unbounded_tail']=bool(counts[-1] and upper is None)
    return result


def parse_bytes(raw):
    match=re.fullmatch(r'\s*((?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)\s*',raw)
    if not match:raise ValueError('invalid Docker byte value '+raw)
    units={'':1,'B':1,'kB':1000,'KB':1000,'MB':1e6,'GB':1e9,'TB':1e12,'KiB':1024,'MiB':2**20,'GiB':2**30,'TiB':2**40}
    return float(match[1])*units[match[2]]


def resource_audit(path,start=None,end=None):
    nodes=defaultdict(list);timestamps=[];malformed=0
    for line in path.read_text().splitlines():
        if not line.strip():continue
        try:row=json.loads(line)
        except json.JSONDecodeError:malformed+=1;continue
        ts=float(row.get('ts',0))
        if start is not None and ts<start or end is not None and ts>=end:continue
        current=defaultdict(dict)
        for c in row.get('docker',[]):
            name=c.get('TrailService') or c.get('Name','')
            match=re.match(r'^(el|cl)-(\d+)-',name)
            if not match:continue
            kind,index=match[1],int(match[2])
            assert kind not in current[index]
            net=c['NetIO'].split('/')
            current[index][kind]=(parse_bytes(c['MemUsage'].split('/')[0]),parse_bytes(net[0]),parse_bytes(net[1]))
        if current:timestamps.append(ts)
        for index,kinds in current.items():
            if set(kinds)=={'el','cl'}:nodes[index].append((ts,)+tuple(sum(kinds[k][i] for k in ('el','cl')) for i in range(3)))
    assert len(nodes)==8 and all(len(v)>1 for v in nodes.values())
    peaks={};rx={};tx={};spans={};resets=0
    for index,samples in sorted(nodes.items()):
        assert [s[0] for s in samples]==sorted(s[0] for s in samples)
        peaks[index]=max(s[1] for s in samples)/2**20
        for a,b in zip(samples,samples[1:]):
            resets+=int(b[2]<a[2] or b[3]<a[3])
        rx[index]=(samples[-1][2]-samples[0][2])/2**20
        tx[index]=(samples[-1][3]-samples[0][3])/2**20
        assert rx[index]>=0 and tx[index]>=0
        spans[index]=samples[-1][0]-samples[0][0]
    return dict(peak_memory_node_mean_mib=statistics.fmean(peaks.values()),peak_memory_worst_node_mib=max(peaks.values()),
        network_rx_node_mean_mib=statistics.fmean(rx.values()),network_tx_node_mean_mib=statistics.fmean(tx.values()),
        node_peak_memory_mib=peaks,node_network_rx_mib=rx,node_network_tx_mib=tx,node_sample_spans_seconds=spans,
        resource_samples=len(timestamps),resource_first_unix=min(timestamps),resource_last_unix=max(timestamps),
        resource_counter_decreases=resets,resource_malformed_lines=malformed)


def evidence_payload(records):
    encoded=[]
    for record in records:
        signature=bytes.fromhex(record['aggregate_signature_hex'].removeprefix('0x'))
        assert len(signature)==record['aggregate_signature_len']==48
        fixed=bytes.fromhex(record['tx_hash'].removeprefix('0x'))
        fixed+=struct.pack('<QQQI',record['epoch'],record['priority_fee_wei'],record['irrecoverable_cost_wei'],108)+signature
        assert len(fixed)==108
        encoded.append(fixed+b''.join(struct.pack('<Q',i) for i in record['path']))
    offset=4*len(encoded);offsets=[]
    for item in encoded:offsets.append(struct.pack('<I',offset));offset+=len(item)
    return b''.join(offsets+encoded)


def validate_acceptance(folder,summary):
    acceptance=read(folder/'acceptance.json');status=read(folder/'runner_status.json')
    failed=[c for c in acceptance.get('checks',[]) if not c.get('passed')]
    if acceptance['passed'] and status['status']=='ok':return 'raw_pass'
    statuses=summary.get('geth_trail_status',[])
    expected=summary['beacon_after']['head_slot']//summary['formal_experiment']['spec']['slots_per_epoch']
    race=(folder.name=='trail_ba_n8_load512_seed1' and len(failed)==1 and failed[0]['name']=='geth-relay-epoch-current'
          and len(statuses)==8 and all(x['relay_epoch']==expected+1 for x in statuses)
          and summary['formal_experiment']['measurement_quality']['passed'])
    if race:return 'adjudicated_one_epoch_post_run_sampling_race_raw_status_failed'
    raise ValueError('Unadjudicated failed run: '+str(folder))


def audit_run(folder,legacy=False):
    summary=read(folder/'summary.json');formal=summary['formal_experiment'];spec=formal['spec'];work=summary['workload']
    assert formal['measurement_quality']['passed']
    status=validate_acceptance(folder,summary)
    variant='trail' if spec['variant'] in ('trail','topostake') else 'baseline'
    window=work.get('measurement_window') if not legacy else None
    txs=work['txs'];assert len({r['tx_hash'] for r in txs})==len(txs)==work['tx_count']
    first=min(float(r['send_unix']) for r in txs);last=max(float(r['send_unix']) for r in txs)
    close(first,work['first_send_unix'],'first_send');close(last,work['last_send_unix'],'last_send')
    stream_rate=len(txs)/float(work['send_elapsed_seconds'])
    close(stream_rate,work['actual_send_tps'],'actual stream send rate')
    base=dict(row_type='run',dataset=folder.parent.name,run_id=folder.name,variant=variant,seed=spec['seed'],n=1,
        load_tx_per_slot=spec['load_tx_per_slot'],target_tps=spec['load_tx_per_slot']/spec['seconds_per_slot'],
        acceptance=status,window_kind='legacy_scan_and_full_run_resources' if legacy else 'formal_half_open_slot_window',
        actual_send_stream_tps=stream_rate,submitted_eventual=len(txs),included_eventual=sum(r.get('status')==1 for r in txs),
        source_summary=str(folder/'summary.json'),source_summary_sha256=digest(folder/'summary.json'))
    start=window['start_unix'] if window else None;end=window['end_unix'] if window else None
    resources=resource_audit(folder/'resources.jsonl',start,end);base.update(resources)
    if not legacy:
        duration=end-start;slots=window['end_slot']-window['start_slot']
        assert slots==40 and duration==120 and start==work['genesis_time']+window['start_slot']*spec['seconds_per_slot']
        sent=[r for r in txs if start<=float(r['send_unix'])<end]
        included=[r for r in txs if r.get('status')==1 and window['start_slot']<=r['included_slot']<window['end_slot']]
        assert len(sent)==window['sent_in_window_count'] and len(included)==window['included_in_window_count']
        close(len(included)/duration,window['throughput_tx_per_second'],'window throughput')
        base.update(window_start_unix=start,window_end_unix=end,window_start_slot=window['start_slot'],window_end_slot=window['end_slot'],
                    window_seconds=duration,slots=slots,sent_in_window=len(sent),included_in_window=len(included),
                    actual_send_window_tps=len(sent)/duration,included_window_tps=len(included)/duration,
                    included_window_tx_per_slot=len(included)/slots,
                    final_inclusions_after_window=sum(r.get('status')==1 and r['included_slot']>=window['end_slot'] for r in txs))
        for key,field in [('per_node_memory_peak_mean_bytes','peak_memory_node_mean_mib'),('per_node_network_rx_delta_mean_bytes','network_rx_node_mean_mib'),('per_node_network_tx_delta_mean_bytes','network_tx_node_mean_mib')]:
            close(base[field]*2**20,formal['resources'][key],key)
        assert formal['prometheus']['start_unix']==start and formal['prometheus']['end_unix']==end
    sizes=formal['block_sizes'];samples=sizes['samples'];assert sizes['encoding']=='ssz' and len(samples)==sizes['count']
    close(statistics.fmean(r['bytes'] for r in samples),sizes['mean_bytes'],'SSZ sample mean')
    base.update(ssz_blocks=len(samples),full_ssz_block_mean_kib=statistics.fmean(r['bytes'] for r in samples)/1024,
                missed_slots=summary['blocks']['missed_slots'],ssz_binary_files_checked=0)
    records=summary['blocks']['records'];by_slot=defaultdict(list)
    for record in records:by_slot[record['slot']].append(record)
    samples_by_slot={r['slot']:r for r in samples}
    if not legacy:
        audit=summary['blocks']['audit_rows'];assert len(audit)==len(samples)
        assert {r['slot'] for r in audit}|set(summary['blocks']['missed_slot_list'])==set(range(window['start_slot'],window['end_slot']))
        total_bytes=total_records=total_txs=missing=legal_empty=max_records=max_work=0;hash_aggregate=hashlib.sha256()
        omissions=[];unique_signatures=[];nonempty_blocks=0
        for block in audit:
            slot=block['slot'];blob=(folder/'blocks_ssz'/f'slot_{slot}.ssz').read_bytes()
            assert len(blob)==block['block_ssz_bytes']==samples_by_slot[slot]['bytes']
            base['ssz_binary_files_checked']+=1;hash_aggregate.update(str(slot).encode()+hashlib.sha256(blob).digest())
            rs=sorted(by_slot[slot],key=lambda r:r['record_index'])
            payload=evidence_payload(rs)
            if rs:
                nonempty_blocks+=1;unique_signatures.append(len({r['aggregate_signature_hex'] for r in rs}))
            assert len(payload)==block['evidence_ssz_bytes']==112*len(rs)+8*sum(len(r['path']) for r in rs)
            assert len(rs)==block['evidence_record_count']
            if payload:assert blob.count(payload)==block['evidence_ssz_occurrences_in_block']==1
            measurement={r['tx_hash'].removeprefix('0x').lower() for r in txs if r.get('status')==1 and r['included_slot']==slot}
            assert measurement=={h.removeprefix('0x').lower() for h in block['measurement_transaction_hashes']}
            tx_hashes=set(block['transaction_hashes']);evidence_hashes={r['tx_hash'] for r in rs}
            assert len(tx_hashes)==block['block_transaction_count']
            assert len(tx_hashes-evidence_hashes)==block['missing_evidence_transaction_count']
            total_bytes+=len(payload);total_records+=len(rs);total_txs+=block['block_transaction_count']
            max_records=max(max_records,len(rs));max_work=max(max_work,sum(len(r['path']) for r in rs))
            missing+=block['missing_evidence_transaction_count'];legal_empty+=block['legal_empty_path_transaction_count']
            if variant=='trail' and block['missing_evidence_transaction_count']:
                omissions.append({k:block[k] for k in ['slot','block_transaction_count','evidence_record_count','missing_evidence_transaction_count','legal_empty_path_transaction_count']})
        assert sum(r['measurement_transaction_count'] for r in audit)==base['included_in_window']
        if variant=='baseline':assert total_records==total_bytes==0
        base.update(evidence_payload_mean_kib=total_bytes/len(audit)/1024,evidence_payload_measurement='reconstructed SSZ bytes matched inside stored complete block',
                    included_all_block_transactions=total_txs,evidence_records=total_records,
                    missing_evidence_records=missing if variant=='trail' else None,
                    missing_records_origin_equals_proposer=legal_empty if variant=='trail' else None,
                    missing_records_other_origins=missing-legal_empty if variant=='trail' else None,
                    max_evidence_records_per_block=max_records,max_evidence_identity_work_per_block=max_work,
                    nonempty_evidence_blocks=nonempty_blocks,
                    max_distinct_aggregate_signatures_per_block=max(unique_signatures,default=0),
                    embedded_signature_bytes_total=48*total_records,
                    signature_bytes_if_one_copy_per_nonempty_block=48*nonempty_blocks,
                    missing_evidence_blocks=omissions,ssz_files_aggregate_sha256=hash_aggregate.hexdigest(),
                    evidence_carriage_pct=100*total_records/total_txs if variant=='trail' else None)
    else:
        inferred=sum(112+8*len(r['path']) for r in records)
        base.update(evidence_payload_mean_kib=inferred/len(samples)/1024 if variant=='trail' else 0,
                    evidence_payload_measurement='inferred from record fields; SSZ payload binaries unavailable',
                    evidence_records=len(records),included_all_block_transactions=None,
                    max_evidence_records_per_block=max((len(rs) for rs in by_slot.values()),default=0))
    pm=formal['prometheus']
    latency=dict(row_type='run',dataset=base['dataset'],run_id=base['run_id'],variant=variant,load_tx_per_slot=base['load_tx_per_slot'],seed=base['seed'],n_runs=1,
        metric='per-block TRAIL evidence verification latency',window_kind=base['window_kind'],source_summary=base['source_summary'],
        method='linear interpolation within cumulative histogram bucket deltas',window_start_unix=start,window_end_unix=end)
    if legacy:
        latency.update(histogram_valid=None,sample_count=None,p95_estimate_ms=None,p99_estimate_ms=None,
            legacy_recorded_lifetime_count=pm.get('evidence_verify_count'),
            legacy_recorded_lifetime_p95_ms=(pm['evidence_verify_p95_seconds']*1000 if pm.get('evidence_verify_p95_seconds') is not None else None),
            limitation='No retained bucket deltas or formal window; legacy lifetime P95 is not a formal-window quantile')
    else:
        histogram=histogram_quantiles(pm.get('evidence_verify_bucket_deltas',[]),pm['evidence_verify_count'])
        latency.update(histogram)
        latency['limitation']='histogram-interpolated estimate, not exact raw-event quantile; counts pooled across node verification calls'
        if variant=='trail':close(latency['p95_estimate_ms'],pm['evidence_verify_p95_seconds']*1000,'stored P95')
        else:latency['limitation']='Not applicable: PoS has no TRAIL evidence verification events'
    peer=summary['peer_graph'];assert peer['matches_target']
    identity=dict(graph=sorted(sorted(e) for e in peer['target_edges']),
        measurement_hashes=[r['tx_hash'] for r in txs],warmup_hashes=[r['tx_hash'] for r in summary['warmup']['txs']],
        images={k:v['id'] for k,v in summary['build_provenance']['images'].items()})
    return base,latency,identity,pm.get('evidence_verify_bucket_deltas',[])


def micro_audit(directory):
    sample_path=directory/'frozen_v1_evidence_benchmark_samples.csv'
    summary_path=directory/'frozen_v1_evidence_benchmark_summary.csv'
    report=read(directory/'frozen_v1_evidence_benchmark_acceptance.json')
    assert report['passed'] and report['failed_sample_checks']==0
    with sample_path.open() as f:samples=list(csv.DictReader(f))
    with summary_path.open() as f:summary=list(csv.DictReader(f))
    output=[]
    for row in summary:
        if row['operation'] not in PRIMARY_OPS:continue
        group=[r for r in samples if r['operation']==row['operation'] and r['hops']==row['hops']]
        assert len(group)==int(row['samples'])==report['repetitions']
        assert {int(r['iteration']) for r in group}==set(range(len(group)))
        assert all(r['check_passed']=='true' for r in group)
        values=[int(r['nanoseconds'])/1e6 for r in group]
        close(quantile(values,.5)*1000,row['median_us'],'micro P50')
        close(quantile(values,.95)*1000,row['p95_us'],'micro P95')
        n=len(values);ordered=sorted(values)
        lo=max(0,int(binom.ppf(.025,n,.99))-1);hi=min(n-1,int(binom.ppf(.975,n,.99)))
        output.append(dict(operation=row['operation'],hops=int(row['hops']),samples=n,
            p50_ms=quantile(values,.5),p95_ms=quantile(values,.95),p99_ms=quantile(values,.99),
            p99_order_stat_interval95_low_ms=ordered[lo],p99_order_stat_interval95_high_ms=ordered[hi],
            p99_interval_caveat='binomial order-statistic interval assumes independent timing samples; serial single-host samples can be correlated',
            certificate_json_encoded_bytes=int(row['json_bytes']),certificate_zstd_encoded_bytes=int(row['compressed_bytes']),
            reported_evidence_string_bytes=int(row['evidence_bytes']),
            reported_evidence_bytes_definition='path+signature string byte lengths; excludes structural fields and is not SSZ/wire size',
            raw_aggregate_signature_bytes=int(row['signature_bytes']),signer_identities=int(row['signer_identities']),
            encoding='Rust AggregatedSignedPaths JSON; different from Lighthouse SSZ records',
            quantile_method='linear sample percentile Type 7 from raw nanoseconds',all_checks_passed=True,
            source_samples=str(sample_path),source_summary=str(summary_path)))
    assert len(output)==20 and {(r['operation'],r['hops']) for r in output}=={(op,h) for op in PRIMARY_OPS for h in [1,2,4,8,16]}
    provenance=read(directory/'benchmark_provenance.json') if (directory/'benchmark_provenance.json').exists() else {}
    if provenance:assert provenance['source_samples_sha256']==digest(sample_path)
    return output,dict(passed=True,samples_sha256=digest(sample_path),summary_sha256=digest(summary_path),
        provenance=provenance,
        provenance_limitation=None if provenance else 'Original CPU model and binary hash not retained; not inferred from the current host',
        acceptance=report,primary_groups=20,synthetic_repeated_certificate_full_budget_test_excluded_from_real_block_latency=True)


def check_exported_results(out):
    """Cross-check exported metrics using NumPy and alternate raw window predicates."""
    import numpy as np
    def rows(name):
        with (out/name).open(encoding='utf-8-sig') as f:return list(csv.DictReader(f))
    performance=rows('r1_1_performance_audit.csv')
    latency=rows('r1_1_verification_latency.csv')
    micro=rows('r1_1_bls_microbenchmark.csv')
    run_rows=[r for r in performance if r['row_type']=='run']
    raw_sizes={};window_checks=histogram_checks=0
    for row in run_rows:
        source=Path(row['source_summary']);raw=json.loads(source.read_text())
        if row['window_kind']!='formal_half_open_slot_window':continue
        window=raw['workload']['measurement_window']
        blobs=list((source.parent/'blocks_ssz').glob('*.ssz'))
        assert len(blobs)==40
        raw_sizes[row['dataset'],row['variant'],int(row['load_tx_per_slot']),int(row['seed'])]=np.mean([p.stat().st_size for p in blobs])/1024
        # Use execution timestamps rather than the primary included-slot predicate.
        count=sum(tx.get('status')==1 and window['start_unix']<=tx['included_block_timestamp']<window['end_unix'] for tx in raw['workload']['txs'])
        assert count==int(row['included_in_window']);window_checks+=1
        if row['variant']=='trail':
            pm=raw['formal_experiment']['prometheus']
            buckets=pm['evidence_verify_bucket_deltas']
            x=[0.]+[b['count'] for b in buckets[:-1]]
            y=[0.]+[float(b['le'])*1000 for b in buckets[:-1]]
            exported=next(r for r in latency if r['row_type']=='run' and r['dataset']==row['dataset'] and r['run_id']==row['run_id'])
            for label,q in [('p95',.95),('p99',.99)]:
                expected=float(np.interp(q*pm['evidence_verify_count'],x,y))
                close(exported[label+'_estimate_ms'],expected,'independent histogram interpolation')
            histogram_checks+=1
    for load in LOADS:
        source='trail_devnet_512_main' if load==512 else 'trail_devnet_txslot_main'
        differences=[raw_sizes[source,'trail',load,seed]-raw_sizes[source,'baseline',load,seed] for seed in range(5)]
        group=next(r for r in performance if r['row_type']=='across_seed' and r['variant']=='trail' and int(r['load_tx_per_slot'])==load and r['dataset']==source)
        close(group['paired_ssz_difference_kib_mean'],np.mean(differences),'independent paired SSZ mean')
        close(group['paired_ssz_difference_kib_ci95'],np.std(differences,ddof=1)/np.sqrt(5)*t.ppf(.975,4),'independent paired SSZ CI')
    sample_path=Path(micro[0]['source_samples'])
    with sample_path.open() as f:samples=list(csv.DictReader(f))
    for row in micro:
        values=[int(r['nanoseconds'])/1e6 for r in samples if r['operation']==row['operation'] and r['hops']==row['hops']]
        for label,q in [('p50',50),('p95',95),('p99',99)]:
            close(row[label+'_ms'],np.percentile(values,q,method='linear'),'independent micro '+label)
    assert window_checks==40 and histogram_checks==20 and len(micro)==20
    return dict(passed=True,alternate_timestamp_window_checks=window_checks,independent_histogram_checks=histogram_checks,
                raw_ssz_paired_group_checks=4,numpy_raw_microbenchmark_group_checks=20)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--micro-dir',type=Path,default=ROOT/'results/raw/r1_1_bls_microbenchmark_20261118')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'results/processed')
    args=parser.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    runs=[];latencies=[];identities={};histograms={};provenance=[]
    for suite in [*FORMAL_SUITES,'frozen_v1_devnet_main']:
        legacy=suite=='frozen_v1_devnet_main'
        folders=sorted(p for p in (ROOT/'results/raw'/suite).iterdir() if p.is_dir())
        for folder in folders:
            match=re.fullmatch(r'(baseline|trail|topostake)_ba_n8_load(64|128|256|512)_seed([0-4])',folder.name)
            if not match or legacy and match[2]!='64':continue
            row,latency,identity,hist=audit_run(folder,legacy)
            key=(suite,row['variant'],row['load_tx_per_slot'],row['seed']);assert key not in identities
            identities[key]=identity;histograms[key]=hist;runs.append(row);latencies.append(latency)
            provenance.append(dict(run_id=row['run_id'],dataset=suite,summary_sha256=row['source_summary_sha256'],resources_sha256=digest(folder/'resources.jsonl'),
                ssz_files=row['ssz_binary_files_checked'],ssz_files_aggregate_sha256=row.get('ssz_files_aggregate_sha256'),acceptance=row['acceptance']))
            print('Audited',suite,folder.name,flush=True)
    assert len(runs)==50 and sum(r['window_kind']=='formal_half_open_slot_window' for r in runs)==40
    pairs=[];groups=[]
    for row in runs:
        if row['variant']!='trail':continue
        key=(row['dataset'],'baseline',row['load_tx_per_slot'],row['seed'])
        baseline=next(r for r in runs if (r['dataset'],r['variant'],r['load_tx_per_slot'],r['seed'])==key)
        ikey=(row['dataset'],'trail',row['load_tx_per_slot'],row['seed'])
        a,b=identities[ikey],identities[key]
        for field in ['graph','measurement_hashes','warmup_hashes','images']:assert a[field]==b[field],(ikey,field)
        row['paired_baseline_run_id']=baseline['run_id']
        row['same_seed_input_sequence_and_topology']=True
        row['paired_ssz_difference_kib']=row['full_ssz_block_mean_kib']-baseline['full_ssz_block_mean_kib']
        if row['window_kind']=='formal_half_open_slot_window':
            row['paired_window_transaction_count_difference']=row['included_in_window']-baseline['included_in_window']
            row['paired_window_transaction_counts_equal']=row['included_in_window']==baseline['included_in_window']
        pairs.append(dict(dataset=row['dataset'],load_tx_per_slot=row['load_tx_per_slot'],seed=row['seed'],
                          ssz_difference_kib=row['paired_ssz_difference_kib'],window_tx_difference=row.get('paired_window_transaction_count_difference')))
    metrics=['actual_send_stream_tps','actual_send_window_tps','included_window_tps','included_window_tx_per_slot','full_ssz_block_mean_kib',
        'evidence_payload_mean_kib','peak_memory_node_mean_mib','peak_memory_worst_node_mib','network_rx_node_mean_mib','network_tx_node_mean_mib',
        'paired_ssz_difference_kib','included_in_window','resource_samples']
    for suite,variant,load in sorted({(r['dataset'],r['variant'],r['load_tx_per_slot']) for r in runs}):
        selected=[r for r in runs if (r['dataset'],r['variant'],r['load_tx_per_slot'])==(suite,variant,load)]
        assert len(selected)==5 and {r['seed'] for r in selected}==set(range(5))
        group=dict(row_type='across_seed',dataset=suite,variant=variant,load_tx_per_slot=load,n=5,window_kind=selected[0]['window_kind'],
                   missed_slots_total=sum(r['missed_slots'] for r in selected),ssz_blocks_total=sum(r['ssz_blocks'] for r in selected))
        for metric in metrics:
            values=[r[metric] for r in selected if r.get(metric) is not None]
            mean,ci=mean_ci(values);group[metric+'_mean']=mean;group[metric+'_ci95']=ci
        if group['window_kind']=='formal_half_open_slot_window':
            group['measurement_slots_total']=sum(r['slots'] for r in selected)
            group['missing_evidence_records_total']=sum(r['missing_evidence_records'] for r in selected) if variant=='trail' else None
        groups.append(group)
    for load in LOADS:
        selected=[r for r in latencies if r.get('window_kind')=='formal_half_open_slot_window' and r['variant']=='trail' and r['load_tx_per_slot']==load]
        assert len(selected)==5
        seedrow=dict(row_type='mean_of_run_histogram_quantiles',variant='trail',load_tx_per_slot=load,n_runs=5,
            metric='per-block TRAIL evidence verification latency',sample_count=sum(r['sample_count'] for r in selected),
            method='mean and 95% Student-t CI of five run-level histogram-interpolated quantiles; not a pooled-event quantile')
        for label in ['p95','p99']:
            mean,ci=mean_ci(r[label+'_estimate_ms'] for r in selected)
            seedrow[label+'_estimate_ms']=mean;seedrow[label+'_ci95_ms']=ci
            seedrow[label+'_brackets_by_seed_ms']={r['seed']:[r[label+'_bucket_lower_ms'],r[label+'_bucket_upper_ms']] for r in selected}
        latencies.append(seedrow)
        hists=[hist for key,hist in histograms.items() if key[1]=='trail' and key[2]==load and key[0] in FORMAL_SUITES]
        boundaries=[r['le'] for r in hists[0]]
        assert all([r['le'] for r in hist]==boundaries for hist in hists)
        pooled=[dict(le=le,count=sum(hist[i]['count'] for hist in hists)) for i,le in enumerate(boundaries)]
        latencies.append(dict(row_type='pooled_histogram_quantile',variant='trail',load_tx_per_slot=load,n_runs=5,
            metric='per-block TRAIL evidence verification latency',method='linear interpolation of pooled bucket deltas across seeds; event-weighted mixture',
            **histogram_quantiles(pooled,seedrow['sample_count'])))
    micro,micro_validation=micro_audit(args.micro_dir)
    validation=dict(audit_complete=True,devnet_new_runs_started=0,local_microbenchmark_only=True,
        formal_runs=40,legacy_reference_runs=10,paired_seeds_per_load=5,formal_window_seconds=120,
        formal_ssz_files_checked=sum(r['ssz_binary_files_checked'] for r in runs),
        all_formal_histograms_valid=all(r.get('histogram_valid') for r in latencies if r.get('window_kind')=='formal_half_open_slot_window'),
        node_resource_counter_decreases=sum(r['resource_counter_decreases'] for r in runs),
        malformed_resource_lines=sum(r['resource_malformed_lines'] for r in runs),
        acceptance_adjudications=[r['run_id'] for r in runs if r['acceptance']!='raw_pass'],
        pairing_checks='same load/seed; identical measurement and warm-up transaction hashes, configured topology edges and image IDs',
        window_throughput='only successful measurement transactions with included_slot in [start_slot,end_slot); no final drain included',
        latency_scope='per-block inline TRAIL evidence aggregate verification calls; excludes full block end-to-end and empty evidence blocks',
        histogram_limits='coarse cumulative buckets, linear interpolation, pooled node calls; Prometheus boundary snapshots use recent scrapes; counter-reset clamping in original collector cannot be independently undone from deltas',
        legacy_limits='no saved formal slot window or histogram buckets; historical SSZ scans and full monitor lifetime resources must not be mixed with aligned-window results',
        groups=groups,paired_runs=pairs,provenance=provenance,microbenchmark=micro_validation,
        formal_trail_histograms_checked=20,
        verification_event_counts_by_load={load:next(r['sample_count'] for r in latencies if r['row_type']=='mean_of_run_histogram_quantiles' and r['load_tx_per_slot']==load) for load in LOADS},
        findings=[
            dict(code='HISTOGRAM_ESTIMATES',scope='20 formal TRAIL runs',detail='P95/P99 are bucket interpolations with recorded brackets; not exact raw-event quantiles'),
            dict(code='LEGACY_64_DIFFERENT_SCOPE',detail='7.22 cannot be reproduced exactly; old paired SSZ scan difference is 7.210467 KiB/block, no matching formal window/P99'),
            dict(code='TARGET_512_NOT_SUSTAINED',detail='TRAIL measured-window inclusion is 343.5 tx/slot; zero missed slots does not establish 512 tx/slot sustained throughput'),
            dict(code='EVIDENCE_RECORD_CAP',run_id='trail_ba_n8_load512_seed1',detail='one 1284-transaction block has 1024 records, 260 missing, including 27 origin=proposer and 233 other origins'),
            dict(code='CERTIFICATE_ENCODING_SCOPE',detail='Rust JSON and Lighthouse SSZ differ; Rust evidence_bytes is string length, not encoded certificate wire size'),
        ],validation_errors=[])
    assert validation['node_resource_counter_decreases']==validation['malformed_resource_lines']==0
    write_csv(out/'r1_1_performance_audit.csv',runs+groups)
    write_csv(out/'r1_1_verification_latency.csv',latencies)
    write_csv(out/'r1_1_bls_microbenchmark.csv',micro)
    validation['export_cross_checks']=check_exported_results(out)
    validation['all_nonempty_blocks_share_one_signature_value']=all(r.get('max_distinct_aggregate_signatures_per_block',0)<=1 for r in runs)
    assert validation['all_nonempty_blocks_share_one_signature_value']
    (out/'r1_1_validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    make_report(out,groups,runs,latencies,micro,validation)
    print('Wrote five R1-1 audit deliverables to',out,flush=True)


def make_report(out,groups,runs,latencies,micro,validation):
    lines=['# R1 Comment 1 — BLS certificate performance audit','',
        '审计范围：复用 40 次新版正式 Devnet 运行（每个负载 PoS/TRAIL 各 5 个 seed），另以 10 次旧版 64 tx/slot 运行追溯论文数值。未启动新的 Geth/Lighthouse 实验；仅补跑本地 Rust 微基准。',
        '', '## 正式测量窗口的紧凑结果', '',
        '每个运行 40 个槽、120 秒，8 个节点。表中均值在 5 个 seed 上计算，± 为双侧 95% Student-t CI；SSZ 差值为相同 seed 下 TRAIL−PoS 的配对均值。内存为每节点 EL+CL 的窗口内离散采样峰值，再平均 8 个节点。网络为每节点 EL+CL 的 RX/TX 计数差；只覆盖窗口内首末采样之间，未外推边界。延迟为每次运行先用直方图插值得到分位数，再跨 5 个 seed 求均值，非原始事件精确分位数。',
        '', '| 目标负载 tx/slot | 完整 SSZ 配对差 KiB/block | 内存 MiB/node PoS / TRAIL | 网络 RX/TX MiB/node PoS → TRAIL | TRAIL P95 / P99 估计 ms | 漏槽 PoS / TRAIL |',
        '|---:|---:|---:|---|---:|---:|']
    selected={}
    for load in LOADS:
        b=next(g for g in groups if g['load_tx_per_slot']==load and g['variant']=='baseline' and g['window_kind']=='formal_half_open_slot_window')
        a=next(g for g in groups if g['load_tx_per_slot']==load and g['variant']=='trail' and g['window_kind']=='formal_half_open_slot_window')
        lat=next(r for r in latencies if r['row_type']=='mean_of_run_histogram_quantiles' and r['load_tx_per_slot']==load)
        selected[load]=(b,a,lat)
        lines.append(f"| {load} | {a['paired_ssz_difference_kib_mean']:.2f} ± {a['paired_ssz_difference_kib_ci95']:.2f} | {b['peak_memory_node_mean_mib_mean']:.1f} / {a['peak_memory_node_mean_mib_mean']:.1f} | {b['network_rx_node_mean_mib_mean']:.1f}/{b['network_tx_node_mean_mib_mean']:.1f} → {a['network_rx_node_mean_mib_mean']:.1f}/{a['network_tx_node_mean_mib_mean']:.1f} | ≈{lat['p95_estimate_ms']:.0f} / ≈{lat['p99_estimate_ms']:.0f} | {b['missed_slots_total']} / {a['missed_slots_total']} |")
    lines += ['', '每个负载每种方案合计 200 个正式槽。紧凑表的内存、网络及延迟 CI 完整保存在 CSV 中；网络 RX 和 TX 始终分开。',
        '', '## 实际负载、窗口吞吐量与证据载荷', '',
        '| 目标 tx/slot | 实际窗口提交 PoS / TRAIL tx/s | 窗口纳入 PoS / TRAIL tx/slot | 完整 SSZ PoS / TRAIL KiB | TRAIL 证据载荷 KiB/block | 验证事件数（5 runs） |',
        '|---:|---:|---:|---:|---:|---:|']
    for load,(b,a,lat) in selected.items():
        lines.append(f"| {load} | {b['actual_send_window_tps_mean']:.3f} / {a['actual_send_window_tps_mean']:.3f} | {b['included_window_tx_per_slot_mean']:.3f} / {a['included_window_tx_per_slot_mean']:.3f} | {b['full_ssz_block_mean_kib_mean']:.3f} / {a['full_ssz_block_mean_kib_mean']:.3f} | {a['evidence_payload_mean_kib_mean']:.3f} ± {a['evidence_payload_mean_kib_ci95']:.3f} | {lat['sample_count']} |")
    lines += ['', '实际窗口提交速率用窗口内提交数量 / 120 s；完整发送过程的 actual_send_stream_tps 另列于 CSV。窗口结束后排空并最终纳入的交易不计入窗口吞吐量。',
        '', '## 直方图核对和延迟解释', '',
        '新版 20 次 TRAIL 运行均保留桶增量；检查桶边界严格递增、计数有限非负且为整数、累计计数单调、+Inf 等于 count 增量，并重算已有 P95。P99 来自相同窗口和相同桶。PoS 没有该验证事件，延迟记为不适用，不能记为零。',
        '指标是 **per-block TRAIL evidence verification latency**：计时包围 inline aggregate verifier，只在存在非空证据且通过前置条件时记录。它不是完整区块端到端验证，也不是每笔交易/每条证书的延迟。事件在节点级聚合，可能重复验证同一块，不是独立唯一块数。',
        '每次约 350–410 次事件，P99 尾部只有约 3–4 个事件的质量；桶包含 25–50、50–100、100–250、250–500 ms 等较宽区间。CSV 逐 seed 保留分位数所在桶上下界。均匀桶内插值的精度受桶宽限制，不应按小数毫秒解读。另提供合并桶估计，但它与“各 run 分位数的均值”是不同统计量；跨 seed CI 使用 n=5，不把全部节点事件当独立 seed。',
        'Prometheus 数据来自窗口边界 instant query 的最近 scrape；原采集器对负计数差作了 max(0, delta)，现存增量不足以重新排除历史 counter reset。Docker 网络值也经过显示精度舍入；保留其采样范围限制。',
        '', '| 负载 | 各 run P95 均值 ± CI ms | 各 run P99 均值 ± CI ms | 各 seed P99 所在桶 ms |',
        '|---:|---:|---:|---|']
    for load,(_,_,lat) in selected.items():
        lines.append(f"| {load} | {lat['p95_estimate_ms']:.1f} ± {lat['p95_ci95_ms']:.1f} | {lat['p99_estimate_ms']:.1f} ± {lat['p99_ci95_ms']:.1f} | {json.dumps(lat['p99_brackets_by_seed_ms'],sort_keys=True)} |")
    old_b=next(g for g in groups if g['dataset']=='frozen_v1_devnet_main' and g['variant']=='baseline')
    old_t=next(g for g in groups if g['dataset']=='frozen_v1_devnet_main' and g['variant']=='trail')
    lines += ['', '## 7.22 KiB/block 的来源与可比性', '',
        f"旧版 64 tx/slot：PoS 完整 SSZ {old_b['full_ssz_block_mean_kib_mean']:.6f} KiB/block，TRAIL {old_t['full_ssz_block_mean_kib_mean']:.6f}；5 个配对 seed 差值 {old_t['paired_ssz_difference_kib_mean']:.6f} ± {old_t['paired_ssz_difference_kib_ci95']:.6f} KiB/block。各 run 扫描 47 或 48 个块，合计 PoS {old_b['ssz_blocks_total']} / TRAIL {old_t['ssz_blocks_total']}，含发送前与最终等待阶段。7.22 不能精确复现；可追溯的值约为 7.210 KiB/block。",
        f"该差值不是证据载荷。旧版证据字段可推算约 {old_t['evidence_payload_mean_kib_mean']:.3f} KiB/扫描块，但缺少保存的完整 SSZ 二进制来核对真实载荷字节。旧版没有 slot-aligned 正式测量窗口，也未保留直方图桶；其内存/网络是完整监控生命周期，P95 是旧累计统计，P99 无法可靠恢复。",
        '新版四种负载各有 PoS/TRAIL 200 个正式窗口 SSZ 块，共 1,600 个文件逐块核对文件长度。证据按记录字段独立 SSZ 重编码，并核对其在完整块内恰好出现一次。每记录固定字段 108 bytes，另加每身份 8 bytes 与证据列表每记录 4-byte offset。完整 SSZ 配对差与证据载荷必须分列，512 的数值不能替换 64 的值。',
        '当前实现每条记录都带一个 48-byte aggregate signature；逐块核对同一非空证据块内所有记录的签名相同。因此整块聚合签名虽然只有一个逻辑值，SSZ 却重复存储了它，载荷不能简化为单个 48-byte 签名。',
        '所有配对的输入交易哈希、warm-up 交易、配置拓扑和镜像 ID 相同；正式窗口内实际纳入交易数并不逐 seed 相等。因此完整 SSZ 差是实测方案差，不能解释为“交易组成固定时只添加证书”的纯边际大小。逐 seed 纳入数量差保存在 CSV。',
        '', '## 高负载和上限', '',
        '256 的跨 seed P99 估计高于 512，是原始数据的非单调观测，不能据此推断目标负载更高会更快；每个负载只有 5 个独立运行且尾部桶较粗。256 的部分 seed 在窗口内也未达到目标提交数量。',
        '512 目标是 170.667 tx/s；实际窗口提交和纳入速率见上表。全量最终成功回执并不说明持续达到 512 tx/slot；零漏槽只说明这 200 个槽内保持了出块。',
        'TRAIL 512 seed 1 的一个块包含 1,284 笔交易但只带 1,024 条证据，缺失 260 条；其中 27 笔也满足 origin=proposer，另外 233 笔为其他发起者。原始证据未遗漏的新版 64/128/256 运行不代表上限在所有未来负载都不约束。缺失证据与漏槽是不同问题。',
        '该 seed 的原始 runner_status=failed/acceptance=false，唯一失败是运行结束后的 epoch 采样跨界：Beacon head slot 199 对应 epoch 24，8 个 Geth 均已进入 25。其测量质量通过，正式窗口数据完整；本审计保留并明确裁决，未静默排除。',
        '', '## 路径长度和本地 Rust 微基准', '',
        '本地补测每个普通操作/跳数 2,000 次，warm-up 50 次；下表为原始纳秒样本 Type-7 分位数，单位 ms，按 P50 / P95 / P99 排列。这是单机、单 fixture、逐操作的测量；冷聚合验证显式绕过应用缓存。单次本地测量不等于真实网络块验证。',
        '', '| 跳数 | Path construction ms | Signature aggregation ms | Individual verification ms | Cold aggregate verification ms | JSON / zstd 编码 bytes |',
        '|---:|---:|---:|---:|---:|---:|']
    for hops in [1,2,4,8,16]:
        rows={r['operation']:r for r in micro if r['hops']==hops}
        def q(op):
            r=rows[op];return f"{r['p50_ms']:.3f} / {r['p95_ms']:.3f} / {r['p99_ms']:.3f}"
        r=rows['verify_aggregate_cold']
        lines.append(f"| {hops} | {q('construct_path')} | {q('aggregate_only')} | {q('verify_individual')} | {q('verify_aggregate_cold')} | {r['certificate_json_encoded_bytes']} / {r['certificate_zstd_encoded_bytes']} |")
    low=next(r for r in micro if r['operation']=='verify_aggregate_cold' and r['hops']==1)
    high=next(r for r in micro if r['operation']=='verify_aggregate_cold' and r['hops']==16)
    individual_low=next(r for r in micro if r['operation']=='verify_individual' and r['hops']==1)
    individual_high=next(r for r in micro if r['operation']=='verify_individual' and r['hops']==16)
    lines += ['', f"1→16 跳：冷聚合验证 P50 从 {low['p50_ms']:.3f} 增至 {high['p50_ms']:.3f} ms，P99 从 {low['p99_ms']:.3f} 增至 {high['p99_ms']:.3f} ms（约 {high['p99_ms']/low['p99_ms']:.2f} 倍）；逐签名验证 P99 从 {individual_low['p99_ms']:.3f} 增至 {individual_high['p99_ms']:.3f} ms（约 {individual_high['p99_ms']/individual_low['p99_ms']:.2f} 倍）。",
        '路径变长意味着更多跳签名和消息处理，逐签名验证、路径构造与冷聚合验证都应依据实际样本比较，而非假设聚合验证完全与路径长度无关。汇总 CSV 另外提供 P99 的二项分布 order-statistic 区间，仅在独立样本假设下成立；同一主机的连续样本可能相关，尾部精度不作无限外推。',
        'Rust 的 evidence_bytes 实际是路径和签名字符串长度之和，省略结构字段，不能称为真实 SSZ 证书大小。本报告采用实际 JSON/zstd 序列化大小，并保留这个旧字段及其定义；它与 Lighthouse 的 SSZ 编码是不同格式。',
        '满工作预算区块微基准重复使用同一个证书，属于合成压力测试；其结果不进入真实区块验证延迟统计。',
        '', '## 结论与最小补测', '',
        '现有数据足以给出受测 64/128/256/512 设置下的窗口资源开销、SSZ/证据大小、漏槽数和粗粒度 P95/P99 估计，并足以否定“零漏槽证明持续 512 tx/slot”及将证据载荷与完整块差混写的表述。',
        '若 R1-1 只需这些带限制的结果，无需重跑完整 Devnet。若必须主张精确 P99、纯证书边际大小或持续 512 tx/slot，最小补测依次为：① 保存每次证据验证原始耗时或更细尾部桶与 reset/scrape 时间；② 对相同交易集合的块做有/无证据序列化对照；③ 先修正/明确证据上限策略、校准发送器能持续达到目标，再做短窗口 512 目标验证。均未自动启动。',
        '', '复现：`python analysis/audit_r1_1_performance.py`。论文、现有表格与图未修改。']
    (out/'r1_1_summary.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':main()
