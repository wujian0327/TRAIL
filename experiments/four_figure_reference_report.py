#!/usr/bin/env python3
"""Audit one consistent pilot reference and export all four figures without simulation."""

import copy
import csv
import hashlib
import json
import math
import shutil
import statistics
import zipfile

from run_experiments import ROOT
from ersoy_participation_report import interval, write_csv
from adaptive_estimator_audit import audit_run, ESTIMATOR_VERSION
from four_figure_plot import render, STYLE_METADATA, uncertainty_for, caption_for


def read_csv(path):
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def main(baseline_suite=None, destination_name='trail_four_figures_v2', extra_suite=None, seed_values=None):
    destination = ROOT/'results/processed'/destination_name
    fee_root = ROOT/'results/processed/trail_full_theta025_cost5_estimator_v2'
    cost_root = ROOT/'results/processed/trail_cost_participation_estimator_v2'
    fee_audit = json.loads((fee_root/'audit.json').read_text())
    cost_audit = json.loads((cost_root/'audit.json').read_text())
    assert fee_audit['valid_runs']==30 and cost_audit['valid_runs']==12
    assert fee_audit['binary_sha256']==cost_audit['binary_sha256']
    fees, costs, pilot_seeds = [.0001,.0005,.001,.005,.01], [1,2,5], [200,201]
    seeds = sorted(seed_values if seed_values is not None else pilot_seeds)
    assert len(seeds)==len(set(seeds)) and set(pilot_seeds)<=set(seeds)
    new_seeds = sorted(set(seeds)-set(pilot_seeds))
    verified_binaries = {}
    def verify_binary(row):
        status = json.loads((ROOT/row['raw_source']/'runner_status.json').read_text())
        path = ROOT/status['cmd'][0]
        if path not in verified_binaries:
            verified_binaries[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert verified_binaries[path]==fee_audit['binary_sha256']
    fee_rows = [r for r in read_csv(fee_root/'runs.csv') if r['series'] in ['full_theta025','ersoy_fee']]
    cost_rows = read_csv(cost_root/'runs.csv')
    assert len(fee_rows)==20 and len(cost_rows)==12
    source_csvs = [fee_root/'runs.csv',cost_root/'runs.csv']
    protocols = ['trail','ersoy_fee']
    if baseline_suite:
        protocols = ['pos','trail_eta0','ersoy_fee','trail']
        for cost in costs:
            baseline_root = ROOT/'results/processed'/f'{baseline_suite}_cost{cost:g}'
            baseline_rows = read_csv(baseline_root/'runs.csv')
            assert len(baseline_rows)==(20 if cost==5 else 4)
            assert {r['protocol'] for r in baseline_rows}=={'pos','trail_eta0'}
            for row in baseline_rows:
                assert float(row['cost_multiplier'])==cost
                assert float(row['transaction_fee']) in (fees if cost==5 else [.001])
                verify_binary(row)
            cost_rows.extend(r for r in baseline_rows if float(r['transaction_fee'])==.001)
            if cost==5:
                fee_rows.extend(baseline_rows)
            source_csvs.append(baseline_root/'runs.csv')
    if extra_suite:
        assert baseline_suite and new_seeds
        for cost in costs:
            extra_root = ROOT/'results/processed'/f'{extra_suite}_cost{cost:g}'
            extra_rows = read_csv(extra_root/'runs.csv')
            assert len(extra_rows)==len(protocols)*len(new_seeds)*(len(fees) if cost==5 else 1)
            assert {r['protocol'] for r in extra_rows}==set(protocols)
            assert {int(r['seed']) for r in extra_rows}==set(new_seeds)
            for row in extra_rows:
                assert float(row['cost_multiplier'])==cost
                assert float(row['transaction_fee']) in (fees if cost==5 else [.001])
                verify_binary(row)
            cost_rows.extend(r for r in extra_rows if float(r['transaction_fee'])==.001)
            if cost==5:
                fee_rows.extend(extra_rows)
            source_csvs.append(extra_root/'runs.csv')
    else:
        assert not new_seeds
    mechanism_labels = dict(trail='Full TRAIL',ersoy_fee='Ersoy (C=0.25)',pos='PoS',trail_eta0='Fee-only')
    cases, fee_keys, cost_keys = {}, [], []
    for rows,keys in [(fee_rows,fee_keys),(cost_rows,cost_keys)]:
        for source in rows:
            row = dict(source)
            for key in ['transaction_fee','cost_multiplier','active_stake_pct','restricted_mean_s']:
                row[key] = float(row[key])
            row['seed'] = int(row['seed'])
            row['mechanism'] = mechanism_labels[row['protocol']]
            key = (row['protocol'],row['transaction_fee'],row['cost_multiplier'],row['seed'])
            if key in cases:
                previous = cases[key]
                assert row['raw_source']==previous['raw_source']
                assert row['active_stake_pct']==previous['active_stake_pct']
                assert row['restricted_mean_s']==previous['restricted_mean_s']
            else:
                cases[key] = row
            keys.append(key)
    assert len(cases)==7*len(seeds)*len(protocols)
    expected_cases = {(p,f,5,s) for p in protocols for f in fees for s in seeds}
    expected_cases |= {(p,.001,c,s) for p in protocols for c in costs for s in seeds}
    assert set(cases)==expected_cases
    assert len(fee_keys)==len(set(fee_keys))==len(fees)*len(protocols)*len(seeds)
    assert len(cost_keys)==len(set(cost_keys))==len(costs)*len(protocols)*len(seeds)
    assert len({(r['git_commit_sha'],r['git_source_diff_sha256']) for r in cases.values()})==1
    assert {k[1] for k in fee_keys}==set(fees) and {k[2] for k in fee_keys}=={5}
    assert {k[1] for k in cost_keys}=={.001} and {k[2] for k in cost_keys}==set(costs)
    reference_keys = [k for k in cost_keys if k[2]==5]
    assert len(reference_keys)==len(seeds)*len(protocols)
    configs, trajectories, initial, estimator_checks = {}, {}, {}, []
    maximum_stake_error, stake_checks = 0.0, 0
    raw_hashes = {}
    for key,run in cases.items():
        raw = ROOT/run['raw_source']
        config_path = raw/'run_config.json'
        config = json.loads(config_path.read_text())['config']
        configs[key] = config
        trail = config['trail_config']
        assert config['max_epochs']==200 and config['warmup_epochs']==100
        assert config['node_num']==100 and config['trans_num_per_second']==20
        assert config['irrecoverable_cost']==.0001 and config['initial_liquid_balance']==10
        assert config['base_reward']==.1 and config['reward_reinvestment_rate']==0
        assert config['transaction_fee']==key[1]
        assert config['adaptive_relay']['cost_median_multiplier']==key[2]
        assert str(config['consensus']).lower()==('pos' if key[0]=='pos' else 'trail')
        assert trail['eta']==(.5 if key[0]=='trail' else 0)
        assert trail['reward_policy']==('ersoy' if key[0]=='ersoy_fee' else 'trail')
        assert trail['ersoy_c']==.25
        if key[0] in ['trail','trail_eta0']:
            assert trail['proposer_fee_ratio']==.25
        estimator_checks.append(audit_run(raw))
        adaptive = read_csv(raw/'adaptive_relay_metrics.csv')
        trajectories[key] = [100*float(r['active_stake_share']) for r in adaptive]
        assert len(trajectories[key])==200
        assert abs(statistics.mean(trajectories[key][100:])-run['active_stake_pct'])<1e-9
        totals,active,counts,nodes0 = {},{},{},{}
        with (raw/'node_epoch_metrics.csv').open(newline='') as handle:
            for node in csv.DictReader(handle):
                epoch = int(node['epoch'])
                stake = float(node['economic_stake'])
                totals[epoch] = totals.get(epoch,0)+stake
                counts[epoch] = counts.get(epoch,0)+1
                if node['relay_profile']=='active':
                    active[epoch] = active.get(epoch,0)+stake
                if epoch==0:
                    nodes0[node['validator_id']] = {k:node[k] for k in
                        ['economic_stake','relay_profile','degree','relay_cost_per_forward']}
        assert counts=={e:100 for e in range(200)}
        initial[key] = nodes0
        for epoch in range(199):
            share = active.get(epoch+1,0)/totals[epoch+1]
            error = abs(share-trajectories[key][epoch]/100)
            assert error < 2*100*.5e-6/totals[epoch+1]+.5e-9
            maximum_stake_error = max(maximum_stake_error,error)
            stake_checks += 1
        raw_hashes[run['raw_source']] = dict(
            config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
            adaptive_csv_sha256=hashlib.sha256((raw/'adaptive_relay_metrics.csv').read_bytes()).hexdigest())
    normalized_cost_checks = 0
    ignore = {'transaction_fee','metrics_prefix','run_id','output_dir','consensus'}
    for seed in seeds:
        baseline_nodes = initial[('trail',.001,5,seed)]
        comparable_configs = []
        for key,config in configs.items():
            if key[3]!=seed:
                continue
            c = {k:copy.deepcopy(v) for k,v in config.items() if k not in ignore}
            c['adaptive_relay'].pop('cost_median_multiplier')
            for field in ['eta','reward_policy','proposer_fee_ratio']:
                c['trail_config'].pop(field)
            comparable_configs.append(c)
            assert initial[key].keys()==baseline_nodes.keys()
            for address,node in initial[key].items():
                base = baseline_nodes[address]
                assert all(node[field]==base[field] for field in ['economic_stake','relay_profile','degree'])
                assert math.isclose(float(node['relay_cost_per_forward'])/key[2],
                                    float(base['relay_cost_per_forward'])/5,rel_tol=1e-12,abs_tol=1e-20)
                normalized_cost_checks += 1
        assert all(c==comparable_configs[0] for c in comparable_configs)
        for field in ['graph_sha256','background_workload_hash','generated_tx']:
            assert len({r[field] for k,r in cases.items() if k[3]==seed})==1

    latency_checks = []
    for key in cost_keys:
        run,config = cases[key],configs[key]
        raw = ROOT/run['raw_source']
        meta = json.loads((raw/'experiment_meta.json').read_text())
        slots = config['slot_per_epoch']
        duration = config['slot_duration']
        followup = meta['adaptive_followup_epochs']*slots
        start,end = config['warmup_epochs']*slots,config['max_epochs']*slots-1-followup
        horizon = followup*duration
        assert (start,end,horizon)==(500,949,50)
        first_generation,birth_slots,included,delays,recorded_birth = {},{},{},{},{}
        for row in read_csv(raw/'generation_samples.csv'):
            created = int(row['created_epoch'])*slots+int(row['created_slot'])
            tx = row['tx_hash']
            first_generation[tx] = min(created,first_generation.get(tx,created))
            birth_slots.setdefault(tx,set()).add(created)
        generated = {tx:created for tx,created in first_generation.items() if start<=created<=end}
        for row in read_csv(raw/'inclusion_samples.csv'):
            tx,slot = row['tx_hash'],int(row['included_slot'])
            if tx in generated and (tx not in included or slot<included[tx]):
                included[tx],delays[tx],recorded_birth[tx] = slot,float(row['latency_s']),int(row['created_slot'])
        bounded,timeouts,duplicate_births = [],0,[]
        for tx,created in generated.items():
            if tx in included:
                assert recorded_birth[tx] in birth_slots[tx]
                assert abs((included[tx]-recorded_birth[tx])*duration-delays[tx])<=.5e-6
                assert included[tx]>=created
                if recorded_birth[tx]!=created:
                    duplicate_births.append(dict(tx_hash=tx,first_created_slot=created,
                        included_copy_created_slot=recorded_birth[tx],included_slot=included[tx]))
            if tx in included and 0<=included[tx]-created<=followup:
                delay = (included[tx]-created)*duration
                bounded.append(min(delay,horizon))
            else:
                bounded.append(horizon)
                timeouts += 1
        assert len(generated)==int(run['generated_cohort'])
        assert abs(statistics.mean(bounded)-run['restricted_mean_s'])<1e-12
        latency_checks.append(dict(raw_source=run['raw_source'],cohort=len(generated),
                                   not_included_within_50_s=timeouts,restricted_mean_s=statistics.mean(bounded),
                                   duplicate_birth_metadata=duplicate_births,
                                   clock='First global generation and first inclusion of each transaction hash; logged copy delay checked separately.'))

    plot_rows = []
    def add_panel(panel,keys,x_function,y_function):
        for protocol in protocols:
            sample_keys = [k for k in keys if k[0]==protocol]
            samples = {}
            for key in sample_keys:
                run = cases[key]
                for x,y in zip(x_function(key),y_function(key)):
                    plot_rows.append(dict(panel=panel,kind='seed',protocol=protocol,seed=key[3],
                        x=x,y=y,n_seeds=1,ci95='',raw_source=run['raw_source']))
                    samples.setdefault(x,[]).append(y)
            for x,values in sorted(samples.items()):
                assert len(values)==len(seeds)
                mean,ci = interval(values)
                plot_rows.append(dict(panel=panel,kind='mean',protocol=protocol,seed='',
                    x=x,y=mean,n_seeds=len(seeds),ci95=ci,raw_source=''))
    add_panel('a',reference_keys,lambda k:range(200),lambda k:trajectories[k])
    add_panel('b',fee_keys,lambda k:[k[1]],lambda k:[cases[k]['active_stake_pct']])
    add_panel('c',cost_keys,lambda k:[k[2]],lambda k:[cases[k]['active_stake_pct']])
    add_panel('d',cost_keys,lambda k:[k[2]],lambda k:[cases[k]['restricted_mean_s']])
    assert len(plot_rows)==211*(len(seeds)+1)*len(protocols)
    assert len({(r['panel'],r['kind'],r['protocol'],r['seed'],r['x']) for r in plot_rows})==len(plot_rows)
    clipped_intervals = ([dict(panel=r['panel'],protocol=r['protocol'],x=r['x'],
                               lower=r['y']-r['ci95'],upper=r['y']+r['ci95'])
                          for r in plot_rows if r['kind']=='mean' and r['panel']!='d'
                          and (r['y']-r['ci95']<0 or r['y']+r['ci95']>100)]
                         if len(seeds)>=5 else [])
    destination.mkdir(parents=True,exist_ok=True)
    write_csv(destination/'plot_data.csv',plot_rows)
    source_rows = [{k:r[k] for k in ['protocol','seed','transaction_fee','cost_multiplier',
                    'active_stake_pct','restricted_mean_s','raw_source','run_id']} for r in cases.values()]
    write_csv(destination/'source_runs.csv',source_rows)
    for panel in 'abcd':
        write_csv(destination/f'panel_{panel}_means.csv',[r for r in plot_rows if r['panel']==panel and r['kind']=='mean'])
    reference_config = configs[('trail',.001,5,200)]
    settings = dict(
        status=f'Unified reference for the matched {len(seeds)}-seed experiment.',
        mechanisms=dict(full=dict(eta=.5,theta=.25),ersoy=dict(C=.25,eta=0),
                        **(dict(pos=dict(election='stake-only',relay_fee=0),fee_only=dict(eta=0,theta=.25)) if baseline_suite else {})),
        reference_point=dict(transaction_fee=.001,forwarding_cost_multiplier=5),
        panels=dict(a=dict(x='epoch',epochs=[0,199],transaction_fee=.001,cost_multiplier=5),
                    b=dict(x='fee',values=fees,cost_multiplier=5),
                    c=dict(x='forwarding cost multiplier',values=costs,transaction_fee=.001),
                    d=dict(x='forwarding cost multiplier',values=costs,transaction_fee=.001,
                           y='Restricted mean inclusion time (logical seconds)',horizon_s=50,
                           cohort_created_epochs=[100,189])),
        seeds=seeds,epochs=200,warmup_epochs=100,participation_analysis_epochs=[100,199],
        g=.0001,block_subsidy=.1,initial_liquid_balance=10,reinvestment_rate=0,
        nodes=100,topology='BA',stake_gini=.6,transactions_per_second=20,
        slot_duration_s=1,slots_per_epoch=5,block_capacity=80,
        cost_reference=reference_config['adaptive_relay']['cost_reference'],
        cost_log_sigma=reference_config['adaptive_relay']['cost_log_sigma'],
        behavior_estimator=ESTIMATOR_VERSION,behavior_parameters=reference_config['adaptive_relay'],
        full_protocol_parameters=reference_config['trail_config'],
        inactive_parameter_note='The serialized TRAIL theta field is unused by Ersoy; its fee allocation uses C.',
        uncertainty=uncertainty_for(seeds),figure_style=dict(STYLE_METADATA,uncertainty=uncertainty_for(seeds)))
    (destination/'reference_settings.json').write_text(json.dumps(settings,indent=2)+'\n')
    audit = dict(unique_runs=len(cases),epoch_reference_runs=len(reference_keys),fee_runs=len(fee_rows),cost_and_inclusion_runs=len(cost_rows),
        binary_sha256=fee_audit['binary_sha256'],source_csv_sha256={
            str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_csvs},
        node_stake_identity_checks=stake_checks,max_stake_share_abs_error=maximum_stake_error,
        normalized_node_cost_checks=normalized_cost_checks,estimator_checks=estimator_checks,
        transaction_latency_checks=latency_checks,raw_source_hashes=raw_hashes,
        active_stake_ci_clipped_to_axis=clipped_intervals,
        inclusion_clock='first_global_generation_by_hash',
        analysis_scripts_sha256={name:hashlib.sha256((ROOT/'experiments'/name).read_bytes()).hexdigest()
            for name in ['adaptive_participation_report.py','fee_participation_report.py',
                         'ersoy_participation_report.py','adaptive_estimator_audit.py',
                         'four_figure_reference_report.py','four_figure_plot.py']},
        scope='Same estimator, binary, network and economic settings across four panels; all original pilot points retained. Finite-window results do not establish equilibrium or individual causal incentives.')
    (destination/'data_audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    caption = caption_for(seeds)
    if clipped_intervals:
        caption += 'Active-stake confidence intervals outside 0-100% are clipped in figures; exact intervals remain in plot_data.csv.\n'
    if baseline_suite:
        caption += ('PoS gives the transaction fee to the proposer and uses stake-only election. '
                    'Fee-only uses the same TRAIL fee allocation (theta=0.25) as Full, '
                    'with eta=0 and stake-only election. All four mechanisms share the '
                    'same corrected behavioral estimator, network, costs, workloads and subsidy.\n')
    (destination/'captions.txt').write_text(caption)
    shutil.copy2(ROOT/'experiments/four_figure_plot.py',destination/'replot.py')
    render(destination)
    bundle = destination/'four_figures_bundle.zip'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(destination.iterdir()):
            if path.is_file() and path!=bundle:
                archive.write(path,path.name)
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert archive.read('plot_data.csv')==(destination/'plot_data.csv').read_bytes()
        assert archive.read('replot.py')==(destination/'replot.py').read_bytes()
        assert len([name for name in archive.namelist() if name.endswith('.pdf')])==5
    for name in ['four_panels','fig_a_epoch_active','fig_b_fee_active','fig_c_cost_active','fig_d_cost_inclusion']:
        for extension in ['png','pdf','svg']:
            assert (destination/f'{name}.{extension}').stat().st_size>1000
    print(destination)
    print(json.dumps(dict(unique_runs=len(cases),estimator_windows=sum(r['update_windows'] for r in estimator_checks),
                          stake_checks=stake_checks,cost_checks=normalized_cost_checks,latency_runs=len(latency_checks))))


if __name__=='__main__':
    main()
