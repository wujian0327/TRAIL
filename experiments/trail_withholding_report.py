#!/usr/bin/env python3
"""Audit matched withholding runs and report modeled costs without double counting."""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

from scipy.stats import t
from run_experiments import ROOT, expand_runs, load_yaml


def json_lines(path):
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024*1024), b''):
            h.update(part)
    return h.hexdigest()


def structured_digest(value):
    encoded=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_background(records):
    """Compare signed inputs in logical creation order, independent of log append order.

    Parallel node inboxes flush in node order within a delivery window. Attacks
    can change window boundaries, so physical append order is not input order.
    Retain and compare EVERY record field; only the list order is normalized.
    """
    for record in records:
        for name in ['id','epoch','at_ms']:
            value=record[name]
            if not isinstance(value,int) or isinstance(value,bool) or value<0:
                raise ValueError(f'invalid logical background field: {name}')
        if not isinstance(record['transaction'],dict):
            raise ValueError('missing signed background transaction')
    ordered=sorted(records,key=lambda r:(r['at_ms'],r['id']))
    if [r['id'] for r in ordered]!=list(range(1,len(ordered)+1)):
        raise ValueError('missing, duplicated or nonchronological background transaction IDs')
    return ordered


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def interval(values):
    if len(values) < 2:
        raise ValueError('at least two paired seeds are required for a confidence interval')
    mean = statistics.fmean(values)
    half = float(t.ppf(.975, len(values)-1))*statistics.stdev(values)/math.sqrt(len(values))
    return mean, half


def relative_change(value, baseline):
    """An absent positive denominator is undefined, never a zero effect."""
    return 100*(value/baseline-1) if baseline > 0 else None


SUM_FIELDS = ['generated', 'generation_rejected', 'included', 'block_success', 'block_failure',
    'eligible_node_transactions', 'withheld_node_transactions', 'eligible_unique_transactions',
    'withheld_unique_transactions', 'suppressed_honest_neighbor_forwards',
    'coalition_forwarding_attempts', 'honest_forwarding_attempts', 'origin_broadcast_attempts',
    'coalition_raw_contribution', 'honest_raw_contribution', 'coalition_relay_rewards',
    'coalition_proposer_rewards', 'coalition_actual_proposer_count', 'coalition_transaction_payments',
    'coalition_forwarding_cost', 'ledger_net_income', 'modeled_cost_adjusted_net_income',
    'expected_coalition_proposer_rewards']


def aggregate_period(epochs):
    result = {name: math.fsum(e[name] for e in epochs) for name in SUM_FIELDS}
    result['coalition_proposer_weight_share'] = statistics.fmean(e['coalition_proposer_weight_share'] for e in epochs)
    result['inclusion_ratio'] = result['included']/result['generated'] if result['generated'] else 0.
    result['block_success_ratio'] = result['block_success']/(result['block_success']+result['block_failure'])
    return result


def audit_run(run):
    directory = Path(run['output_dir'])
    status = json.loads((directory/'runner_status.json').read_text())
    summary = json.loads((directory/'run_summary.json').read_text())
    if status['status'] != 'ok' or summary['completed_epochs'] != run['max_epochs']:
        raise ValueError(f'incomplete or unsuccessful run: {directory}')
    assert summary['transport']=='parallel-event-v2'
    assert summary['node_workers']==run['withholding_node_workers']
    assert 1 < summary['parallel_node_peak'] <= summary['node_workers'], 'parallel node execution was not observed'
    recorded = json.loads((directory/'run_config.json').read_text())
    config = recorded['config']
    assert config['attack_mode'] == 'SelectiveWithholding'
    assert not config['adaptive_relay']['enabled']
    assert config['coalition_path_selection'] == 'off'
    epochs = json_lines(directory/'withholding_epochs.jsonl')
    slots = json_lines(directory/'withholding_slots.jsonl')
    states = json_lines(directory/'withholding_states.jsonl')
    blocks = json_lines(directory/'blocks.jsonl')
    assert [e['epoch'] for e in epochs] == list(range(run['max_epochs']))
    assert [s['epoch'] for s in states] == list(range(run['max_epochs']))
    assert len(slots) == len(blocks) == run['max_epochs']*run['slot_per_epoch']
    coalition = set(json.loads((directory/'coalition.json').read_text()))
    stakes = json.loads((directory/'initial_stakes.json').read_text())
    share = math.fsum(stakes[a] for a in sorted(coalition))/math.fsum(stakes[a] for a in sorted(stakes))
    edges = json.loads((directory/'graph.json').read_text())
    neighbors = defaultdict(set)
    for a, b in edges: neighbors[a].add(b); neighbors[b].add(a)
    start, end = run['withholding_start_epoch'], run['withholding_end_epoch']
    probability = run['withholding_probability']
    background_records=json_lines(directory/'background_transactions.jsonl')
    background_sequence=canonical_background(background_records)
    generated = {}
    for record in background_sequence:
        assert record['epoch']==record['at_ms']//(run['slot_per_epoch']*run['slot_duration']*1000)
        assert record['transaction']['timestamp']==record['id']
        tx = record['transaction']; assert tx['hash'] not in generated
        generated[tx['hash']] = record
    seen = set(); decision_counts = defaultdict(lambda: [0, 0, 0, set(), set()])
    with (directory/'withholding_decisions.jsonl').open() as handle:
        for line in handle:
            record = json.loads(line); epoch = record['epoch']; prefix = record['validated_prefix']
            tx = prefix['transaction']; hops = prefix['paths']
            sequence = [tx['from']]+[hop['to'] for hop in hops]
            key = record['node'], record['tx_hash']
            assert key not in seen; seen.add(key)
            assert record['node'] in coalition and record['origin'] not in coalition and record['upstream'] not in coalition
            assert tx['hash'] == record['tx_hash'] and tx == generated[tx['hash']]['transaction']
            assert tx['from'] == record['origin'] and sequence[-1] == record['node']
            assert hops[-1]['from'] == record['upstream'] and len(sequence) == len(set(sequence))
            assert all(hop['sender_signature'] and hop['receiver_signature'] for hop in hops)
            payload = int(run['attack_seed']).to_bytes(8, 'little')+b'/selective-withholding/'+record['node'].encode()+b'\0'+tx['hash'].encode()
            value = int.from_bytes(hashlib.sha3_256(payload).digest()[:8], 'little') >> 11
            coin = value/(1 << 53)
            assert coin == record['coin']
            withheld = start <= epoch < end and coin < probability
            assert withheld == record['withheld']
            count = decision_counts[epoch]; count[0] += 1; count[3].add(tx['hash'])
            if withheld:
                count[1] += 1; count[4].add(tx['hash'])
                if len(hops) < run['trail_max_path_hops']:
                    count[2] += len(neighbors[record['node']] - set(sequence) - coalition)
    included = set()
    for block, slot in zip(blocks, slots):
        assert block['header']['hash'] == slot['block_hash']
        epoch = block['header']['epoch']; body = block['body']
        assert len(body['transactions']) == len(body['paths']) <= run['max_tx_per_block']
        for tx, proof in zip(body['transactions'], body['paths']):
            assert tx['hash'] not in included; included.add(tx['hash'])
            assert generated[tx['hash']]['epoch'] == epoch
            assert tx == generated[tx['hash']]['transaction']
            assert proof['epoch'] == epoch
            if proof['paths']:
                assert proof['paths'][0] == tx['from'] and block['header']['miner'] not in proof['paths']
                assert proof['signature']
            else:
                assert tx['from'] == block['header']['miner']
            assert len(proof['paths']) == len(set(proof['paths']))
    violations = []
    for e, state in zip(epochs, states):
        epoch = e['epoch']; count = decision_counts[epoch]
        assert e['eligible_node_transactions'] == count[0] and e['withheld_node_transactions'] == count[1]
        assert e['suppressed_honest_neighbor_forwards'] == count[2]
        assert e['eligible_unique_transactions'] == len(count[3]) and e['withheld_unique_transactions'] == len(count[4])
        assert e['generation_rejected'] == 0 and e['rejected_invalid_paths'] == 0
        assert e['included'] <= e['generated'] and e['block_failure'] == 0
        assert e['block_success'] == run['slot_per_epoch']
        assert all(math.isfinite(v) for v in e.values() if isinstance(v, (float, int)))
        assert math.isclose(e['actual_stake_share'], share, abs_tol=1e-12)
        measured = math.fsum(state['weights'][a] for a in sorted(coalition))
        assert math.isclose(measured, e['coalition_proposer_weight_share'], abs_tol=1e-12)
        bound = share*(1+run['eta'])/(1+share*run['eta'])
        assert math.isclose(e['theoretical_bound'], bound, abs_tol=1e-12)
        assert e['bound_violations'] == int(measured > bound+1e-12)
        assert e['stake_premise_violations'] == int(share >= 1/3)
        assert math.isclose(e['coalition_forwarding_cost'], e['coalition_forwarding_attempts']*run['withholding_forwarding_cost'], abs_tol=1e-12)
        ledger = e['coalition_relay_rewards']+e['coalition_proposer_rewards']-e['coalition_transaction_payments']
        assert math.isclose(e['ledger_net_income'], ledger, abs_tol=1e-12)
        assert math.isclose(e['modeled_cost_adjusted_net_income'], ledger-e['coalition_forwarding_cost'], abs_tol=1e-12)
        own_generated = [g for g in generated.values() if g['epoch'] == epoch and g['transaction']['from'] in coalition]
        paid = math.fsum(g['transaction']['fee']+g['transaction']['irrecoverable_cost'] for g in own_generated)
        assert math.isclose(paid, e['coalition_transaction_payments'], abs_tol=1e-10)
        if e['bound_violations'] or e['stake_premise_violations']:
            violations.append(dict(run_id=run['run_id'], **e))
    assert len(generated) == summary['generated_tx'] == sum(e['generated'] for e in epochs)
    assert len(included) == summary['included_tx'] == sum(e['included'] for e in epochs)
    attack = [e for e in epochs if start <= e['epoch'] < end]
    if probability > 0:
        assert sum(e['withheld_node_transactions'] for e in attack) > 0
        assert sum(e['suppressed_honest_neighbor_forwards'] for e in attack) > 0
    periods = {'warmup': epochs[:start], 'attack': epochs[start:end], 'recovery': epochs[end:],
               'observation': epochs[start:], 'full': epochs}
    comparable = {k: v for k, v in config.items() if k not in ['run_id', 'output_dir', 'metrics_prefix', 'withholding_probability']}
    identity = dict(config=comparable, coalition=sorted(coalition), stakes=stakes, edges=edges,
                    source=[recorded['git_commit_sha'], recorded['git_source_diff_sha256']],
                    background_sequence_sha256=structured_digest(background_sequence))
    return dict(run=run, epochs=epochs, identity=identity, violations=violations,
                background_raw_sha256=digest(directory/'background_transactions.jsonl'),
                background_count=len(background_sequence),
                background_append_order_is_logical=background_records==background_sequence,
                warmup_background_sha256=structured_digest([r for r in background_records if r['epoch']<start]),
                warmup_slots=[s for s in slots if s['epoch'] < start], warmup_states=states[:start+1],
                periods={name: aggregate_period(rows) for name, rows in periods.items()})


def build_report(spec, destination):
    inputs = expand_runs(spec)
    results = []
    for i, run in enumerate(inputs, 1):
        results.append(audit_run(run))
        print(f'Validated {i}/{len(inputs)} runs', flush=True)
    groups = defaultdict(dict)
    for result in results:
        run = result['run']; groups[run['adversary_placement'],run['seed_value']][run['withholding_probability']] = result
    paired = []; coverage = []; backgrounds=[]
    for (placement, seed), arms in sorted(groups.items()):
        assert set(arms) == {0., .5, 1.}
        baseline = arms[0.]
        for probability, result in sorted(arms.items()):
            if result['identity'] != baseline['identity']:
                mismatches=[k for k in result['identity'] if result['identity'][k]!=baseline['identity'][k]]
                raise ValueError(f'paired external input mismatch in {mismatches}: {placement}, seed={seed}, p={probability}')
            if result['warmup_background_sha256']!=baseline['warmup_background_sha256']:
                raise ValueError(f'warmup background log mismatch: {placement}, seed={seed}, p={probability}')
            if result['epochs'][:inputs[0]['withholding_start_epoch']] != baseline['epochs'][:inputs[0]['withholding_start_epoch']] or result['warmup_slots'] != baseline['warmup_slots'] or result['warmup_states'] != baseline['warmup_states']:
                raise ValueError(f'warmup is not EXACTLY identical: {placement}, seed={seed}, p={probability}')
            coverage.append(dict(placement=placement,seed=seed,probability=probability,warmup_exact=True,external_inputs_exact=True))
            backgrounds.append(dict(placement=placement,seed=seed,probability=probability,
                transaction_count=result['background_count'],
                logical_sequence_sha256=result['identity']['background_sequence_sha256'],
                raw_log_sha256=result['background_raw_sha256'],baseline_raw_log_sha256=baseline['background_raw_sha256'],
                raw_log_bytes_equal=result['background_raw_sha256']==baseline['background_raw_sha256'],
                append_order_is_logical=result['background_append_order_is_logical'],
                logical_transactions_exact=True,warmup_background_exact=True))
            for phase, a in result['periods'].items():
                h = baseline['periods'][phase]
                row = dict(placement=placement, seed=seed, probability=probability, period=phase)
                row.update(a)
                row.update({'delta_'+name: a[name]-h[name] for name in a})
                row['saved_forwarding_cost'] = h['coalition_forwarding_cost']-a['coalition_forwarding_cost']
                row['relay_change_plus_saved_cost'] = row['delta_coalition_relay_rewards']+row['saved_forwarding_cost']
                row['honest_contribution_change_pct'] = relative_change(a['honest_raw_contribution'], h['honest_raw_contribution'])
                row['inclusion_pct'] = 100*a['inclusion_ratio']
                assert math.isclose(row['delta_modeled_cost_adjusted_net_income'], row['delta_coalition_relay_rewards']+
                    row['delta_coalition_proposer_rewards']-row['delta_coalition_transaction_payments']+row['saved_forwarding_cost'],abs_tol=1e-10)
                paired.append(row)
    summary = []
    for placement in ['random', 'high-betweenness']:
        for probability in [0., .5, 1.]:
            for phase in ['attack','recovery','observation','full']:
                rows = [r for r in paired if r['placement']==placement and r['probability']==probability and r['period']==phase]
                assert {r['seed'] for r in rows} == set(spec['seeds'])
                row = dict(placement=placement, probability=probability, period=phase, n=len(rows))
                for field in ['delta_modeled_cost_adjusted_net_income', 'honest_contribution_change_pct', 'inclusion_pct',
                              'delta_coalition_proposer_weight_share', 'delta_expected_coalition_proposer_rewards',
                              'delta_coalition_relay_rewards', 'delta_coalition_proposer_rewards', 'saved_forwarding_cost',
                              'relay_change_plus_saved_cost',
                              'delta_coalition_transaction_payments', 'delta_inclusion_ratio', 'delta_coalition_raw_contribution',
                              'coalition_forwarding_attempts', 'honest_forwarding_attempts','withheld_node_transactions',
                              'suppressed_honest_neighbor_forwards','coalition_actual_proposer_count']:
                    values=[r[field] for r in rows]
                    mean, half = interval(values) if all(v is not None for v in values) else (None, None)
                    row[field+'_mean']=mean;row[field+'_ci95']=half
                    row[field+'_defined_seeds']=sum(v is not None for v in values)
                summary.append(row)
    destination.mkdir(parents=True,exist_ok=True)
    write_csv(destination/'paired.csv', paired);write_csv(destination/'summary.csv',summary);write_csv(destination/'consistency.csv',coverage)
    write_csv(destination/'background_comparison.csv',backgrounds)
    all_violations = [v for r in results for v in r['violations']]
    validation = dict(runs=len(results),expected_runs=len(inputs),paired_seeds=len(groups),warmup_exact=True,
        background_and_inputs_exact=True,validation_errors=[],max_bound_utilization=max(e['bound_utilization'] for r in results for e in r['epochs']),
        background_comparison='All fields of every signed creation record in (at_ms, id) order; physical append order is diagnostic only.',
        warmup_background_append_order_exact=True,warmup_states_include_attack_start=True,
        bound_violations=sum(e['bound_violations'] for r in results for e in r['epochs']),
        stake_premise_violating_epochs=sum(e['stake_premise_violations'] for r in results for e in r['epochs']),
        violations=all_violations,forwarding_cost_reference=inputs[0]['withholding_forwarding_cost'],
        cost_model='signed relay forwarding attempts; origin broadcast excluded, matching participation counter; other deployment costs unmodeled')
    (destination/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    epochs = [dict(run_id=r['run']['run_id'],placement=r['run']['adversary_placement'],seed=r['run']['seed_value'],
                   probability=r['run']['withholding_probability'],**e) for r in results for e in r['epochs']]
    write_csv(destination/'epochs.csv',epochs)
    lines=['# Selective Withholding Attack Evaluation','',f"Completed {len(results)} successful runs, {len(spec['seeds'])} seeds per scenario.",
        'Warm-up state, proposer sequence, contributions, rewards, background transactions, topology and initial stakes match exactly across probabilities.',
        'Background input comparison retains every signed transaction field and its epoch, logical generation time and ID, sorted by (at_ms, id). Parallel log append order is retained in raw files and diagnosed in background_comparison.csv, but is not treated as input order. Warm-up logs remain exactly matched in their original append order, and state checks include the attack-start boundary.',
        '', 'Net income includes accrued relay and proposer rewards minus actual F+g deductions and modeled relay forwarding costs. Pending settlement is not added again.',
        f"Forwarding cost: {inputs[0]['withholding_forwarding_cost']:.17g} token per signed relay forwarding attempt. Other deployment costs are not modeled.",
        'Expected proposer rewards include both base reward and the fee share of each possible proposer\u2019s actual eligible transaction set, weighted by frozen election probabilities; they are diagnostic and never added to actual income.',
        '', 'Transport: parallel node inbox processing with deterministic positive-delay delivery boundaries (50 ms times configured delay multiplier). Independent nodes verify signatures and forward concurrently; the coordinator only dispatches inboxes and merges results in a fixed order. Observed concurrent node-worker peaks are checked for every run. All three arms share this transport. Transactions are sampled with unchanged background RNG streams; origin identities use logical timestamps. Blocks synchronize canonically at slot end. Signed path domains expire at epoch boundaries; there is no artificial rebroadcast or cross-epoch resigning.',
        'Each validator starts with 100 token liquid balance, independently of immutable economic stake, to prevent insufficient funds from changing paired workload.',
        '', '## Observed effects','']
    table=[r'\begin{table}[t]',r'\centering',r'\caption{Selective Withholding Attack Evaluation}',r'\label{tab:selective-withholding}',
        r'\scriptsize',r'\setlength{\tabcolsep}{2.5pt}',r'\begin{tabular}{@{}lcrrr@{}}',r'\hline',
        r'Placement & $p$ & $\Delta I_C$ & $\Delta C_H$ (\%) & Inclusion (\%) \\',r'\hline']
    for row in [r for r in summary if r['period']=='observation']:
        label='Random' if row['placement']=='random' else 'High-betw.'
        net=row['delta_modeled_cost_adjusted_net_income_mean']; ci=row['delta_modeled_cost_adjusted_net_income_ci95']
        finding='significant positive' if net-ci>0 else 'significant negative' if net+ci<0 else 'no statistically significant'
        h=row['honest_contribution_change_pct_mean'];hc=row['honest_contribution_change_pct_ci95']
        inc=row['inclusion_pct_mean'];ic=row['inclusion_pct_ci95']
        honest_text=f'{h:+.4g} ± {hc:.4g}%' if h is not None else 'undefined (at least one zero honest baseline)'
        honest_latex=f'${h:+.2f}\\pm{hc:.2f}$' if h is not None else '--'
        lines.append(f"- {label}, p={row['probability']:.0%}: modeled cost-adjusted net-income change {net:+.6g} ± {ci:.6g} token ({finding} difference); honest contribution {honest_text}; inclusion {inc:.4g} ± {ic:.4g}%. All intervals are 95% Student-t across matched seeds.")
        if row['probability'] > 0:
            def number(field, scale=1):
                return f"{scale*row[field+'_mean']:+.6g} ± {scale*row[field+'_ci95']:.6g}"
            lines.append(f"  Withholding occurred: mean {row['withheld_node_transactions_mean']:.6g} node/transaction decisions and {row['suppressed_honest_neighbor_forwards_mean']:.6g} suppressed honest-neighbor sends. Coalition election-weight change: {number('delta_coalition_proposer_weight_share',100)} percentage points; conditional expected proposer-reward change: {number('delta_expected_coalition_proposer_rewards')} token; actual proposer-reward change: {number('delta_coalition_proposer_rewards')} token.")
            lines.append(f"  Relay-reward change: {number('delta_coalition_relay_rewards')} token; saved forwarding cost: {number('saved_forwarding_cost')} token. Their sum is {number('relay_change_plus_saved_cost')} token before proposer effects and transaction-payment differences. This decomposition separates modeled forwarding savings from lost rewards.")
            recovery=next(r for r in summary if r['placement']==row['placement'] and r['probability']==row['probability'] and r['period']=='recovery')
            lines.append(f"  Recovery-only modeled net-income change: {recovery['delta_modeled_cost_adjusted_net_income_mean']:+.6g} ± {recovery['delta_modeled_cost_adjusted_net_income_ci95']:.6g} token; no withholding decisions are permitted during recovery.")
        table.append(f"{label} & {row['probability']:.0%} & ${net:+.3f}\\pm{ci:.3f}$ & {honest_latex} & ${inc:.2f}\\pm{ic:.2f}$ \\\\".replace('%',r'\%'))
    table += [r'\hline',r'\end{tabular}',r'\par\vspace{2pt}',r'\begin{minipage}{\columnwidth}\scriptsize',
        f"Mean $\\pm$ 95\\% Student-$t$ CI, {len(spec['seeds'])} paired seeds. $\\Delta I_C$: modeled cost-adjusted net-income change (token); $\\Delta C_H$: honest raw-contribution change relative to $p=0$. Epochs {inputs[0]['withholding_start_epoch']}--{inputs[0]['max_epochs']-1}. Forwarding cost $C_{{\\rm ref}}=1.40557\\times10^{{-7}}$ token/attempt. A dash indicates an undefined zero-baseline percentage; no seeds are silently excluded.",
        r'\end{minipage}',r'\end{table}']
    lines += ['', '## Proposer influence and safety', '',
        f"Maximum Theorem 6 bound utilization: {validation['max_bound_utilization']:.9g}. Bound violations: {validation['bound_violations']}. Epochs violating a < 1/3: {validation['stake_premise_violating_epochs']}.",
        'Full attack/recovery/observation decompositions, expected-versus-actual proposer effects, and inclusion differences are in summary.csv and paired.csv.',
        'Intervals containing zero do not establish attack unprofitability. Results apply to this controlled transport, forwarding-cost model and tested configurations; they do not establish all-cost deployment security.']
    (destination/'report.md').write_text('\n'.join(lines)+'\n')
    (destination/'appendix_table.tex').write_text('\n'.join(table)+'\n')
    return validation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args();spec=load_yaml(args.config)
    out=args.output_dir or ROOT/'results/processed'/spec['suite']
    print(json.dumps(build_report(spec,out),indent=2))


if __name__=='__main__':main()
