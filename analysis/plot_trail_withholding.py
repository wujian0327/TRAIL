#!/usr/bin/env python3
"""Plot the complete 20-seed withholding study with matched Student-t intervals."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

os.environ.setdefault('MPLCONFIGDIR','/tmp/trail-matplotlib')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter, FormatStrFormatter, MaxNLocator
from scipy.stats import t

ROOT=Path(__file__).resolve().parents[1]
SERIES=[('random','Random','#0072B2','o'),
        ('high-betweenness','High-betweenness','#D55E00','s')]
METRICS=[
    ('income','delta_modeled_cost_adjusted_net_income',1.,'Modeled Cost-adjusted\nNet Income Change (token)',True),
    ('honest_contribution','honest_contribution_change_pct',1.,'Honest Contribution Change (%)',True),
    ('inclusion','inclusion_pct',1.,'Transaction Inclusion Ratio (%)',False),
    ('proposer_weight','delta_coalition_proposer_weight_share',100.,'Proposer Weight Change (pp)',True),
]


def read(path):
    with path.open(encoding='utf-8-sig',newline='') as handle:return list(csv.DictReader(handle))


def write(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def interval(values):
    if len(values)!=20:raise ValueError('all 20 paired seeds are required')
    mean=statistics.fmean(values)
    half=float(t.ppf(.975,19))*statistics.stdev(values)/math.sqrt(20)
    return mean,half


def draw(ax,points,metric,legend):
    name,field,scale,ylabel,zero=metric
    for placement,label,color,marker in SERIES:
        group=sorted((r for r in points if r['placement']==placement),key=lambda r:r['probability'])
        ax.errorbar([100*r['probability'] for r in group],
                    [scale*r[field+'_mean'] for r in group],
                    yerr=[scale*r[field+'_ci95'] for r in group],color=color,marker=marker,
                    markerfacecolor='white',markersize=4,linewidth=1,capsize=2.5,label=label)
    if zero:ax.axhline(0,color='#666666',linewidth=.7,linestyle='--')
    ax.set_xlabel('Withholding Probability (%)')
    ax.set_ylabel(ylabel)
    ax.set_xticks([0,50,100]);ax.set_xlim(-5,105)
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',color='#e5e5e5',linewidth=.6);ax.set_axisbelow(True)
    if name in ['honest_contribution','income_percent']:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6,integer=True))
        ax.yaxis.set_major_formatter(PercentFormatter(100,decimals=0))
    elif name=='inclusion':ax.yaxis.set_major_formatter(PercentFormatter(100,decimals=1))
    elif name=='proposer_weight':ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    if legend:ax.legend(frameon=True,edgecolor='#bbbbbb')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,default=ROOT/'results/processed/trail_withholding_main_v2')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'figures/trail_securityV2')
    args=parser.parse_args()
    validation=json.loads((args.input_dir/'validation.json').read_text())
    assert validation['runs']==validation['expected_runs']==120
    assert validation['warmup_exact'] and validation['background_and_inputs_exact']
    assert not validation['validation_errors']
    source=[r for r in read(args.input_dir/'summary.csv') if r['period']=='observation']
    pairs=[r for r in read(args.input_dir/'paired.csv') if r['period']=='observation']
    assert len(source)==6 and len(pairs)==120
    expected={(p,q) for p,_,_,_ in SERIES for q in [0.,.5,1.]}
    assert {(r['placement'],float(r['probability'])) for r in source}==expected
    baseline={(r['placement'],int(r['seed'])):float(r['modeled_cost_adjusted_net_income'])
              for r in pairs if float(r['probability'])==0}
    assert len(baseline)==40 and all(math.isfinite(v) and v>0 for v in baseline.values())
    points=[];percent_pairs=[]
    for row in source:
        placement=row['placement'];probability=float(row['probability'])
        group=[r for r in pairs if r['placement']==placement and float(r['probability'])==probability]
        assert len(group)==20 and {int(r['seed']) for r in group}==set(range(20))
        assert int(row['n'])==20
        point=dict(placement=placement,probability=probability,n=20)
        for _,field,_,_,_ in METRICS:
            values=[float(r[field]) for r in group]
            assert all(math.isfinite(v) for v in values)
            mean,half=interval(values)
            assert math.isclose(mean,float(row[field+'_mean']),abs_tol=1e-11)
            assert math.isclose(half,float(row[field+'_ci95']),abs_tol=1e-11)
            point[field+'_mean']=mean;point[field+'_ci95']=half
        percentages=[]
        for pair in group:
            seed=int(pair['seed']);honest=baseline[placement,seed]
            delta=float(pair['delta_modeled_cost_adjusted_net_income'])
            percent=100*delta/honest;percentages.append(percent)
            percent_pairs.append(dict(placement=placement,probability=probability,seed=seed,
                honest_cost_adjusted_net_token=honest,delta_cost_adjusted_net_token=delta,
                relative_net_income_change_pct=percent))
        mean,half=interval(percentages)
        point['relative_net_income_change_pct_mean']=mean
        point['relative_net_income_change_pct_ci95']=half
        points.append(point)
    write(args.input_dir/'figure_points.csv',points)
    write(args.input_dir/'income_percent_pairs.csv',percent_pairs)
    chinese=[]
    for row in points:
        chinese.append(dict(联盟位置='Random' if row['placement']=='random' else 'High-betweenness',
            扣留概率=f"{row['probability']:.0%}",配对seed数=20,
            成本调整后净收益差_token=row['delta_modeled_cost_adjusted_net_income_mean'],
            净收益差95CI半宽_token=row['delta_modeled_cost_adjusted_net_income_ci95'],
            诚实贡献变化_pct=row['honest_contribution_change_pct_mean'],
            诚实贡献变化95CI半宽_pct=row['honest_contribution_change_pct_ci95'],
            交易纳入率_pct=row['inclusion_pct_mean'],交易纳入率95CI半宽_pct=row['inclusion_pct_ci95'],
            Proposer权重变化_百分点=100*row['delta_coalition_proposer_weight_share_mean'],
            Proposer权重变化95CI半宽_百分点=100*row['delta_coalition_proposer_weight_share_ci95'],
            相对净收益变化_pct=row['relative_net_income_change_pct_mean'],
            相对净收益变化95CI半宽_pct=row['relative_net_income_change_pct_ci95']))
    write(args.input_dir/'最终结果汇总.csv',chinese)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9.5,
        'xtick.labelsize':7.5,'ytick.labelsize':7.5,'legend.fontsize':7,'pdf.fonttype':42,'ps.fonttype':42})
    args.output_dir.mkdir(parents=True,exist_ok=True)
    outputs=[]
    percent_metric=('income_percent','relative_net_income_change_pct',1.,'Relative Net Income Change (%)',True)
    for metric in METRICS+[percent_metric]:
        fig,ax=plt.subplots(figsize=(3.6,2.7));draw(ax,points,metric,True);fig.tight_layout()
        for extension in ['png','pdf']:
            name=f'trail_selective_withholding_{metric[0]}.{extension}'
            fig.savefig(args.output_dir/name,dpi=300,bbox_inches='tight');outputs.append(name)
        plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(7.2,5.2))
    for ax,metric in zip(axes.flat,METRICS):draw(ax,points,metric,False)
    handles,labels=axes.flat[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.55,1.015),ncol=2,
               frameon=True,edgecolor='#bbbbbb',fontsize=8)
    fig.tight_layout(rect=(0,0,1,.96),w_pad=2.,h_pad=1.5)
    for extension in ['png','pdf']:
        name=f'trail_selective_withholding_overview.{extension}'
        fig.savefig(args.output_dir/name,dpi=300,bbox_inches='tight');outputs.append(name)
    plt.close(fig)
    metadata=dict(study='Selective Withholding Attack',runs=120,seeds_per_scenario=20,
        observation_epochs=[10,79],income_unit='token',net_income='accrued relay + proposer rewards - actual F+g - modeled signed relay forwarding costs',
        forwarding_cost_reference=validation['forwarding_cost_reference'],
        confidence_intervals='two-sided 95% Student-t across 20 matched-seed contrasts; inclusion uses per-run ratios',
        percentage_income_definition='mean of per-seed 100*(attack cost-adjusted net - baseline cost-adjusted net)/baseline cost-adjusted net',
        percentage_income_note='Percentage and token means have different seed weighting; their confidence intervals need not have identical significance.',
        units=dict(proposer_weight='percentage points, original share difference times 100',
                   honest_contribution='percentage change from each matched honest baseline',inclusion='absolute percentage'),
        sources={name:dict(path=str((args.input_dir/name).resolve()),sha256=hashlib.sha256((args.input_dir/name).read_bytes()).hexdigest())
                 for name in ['summary.csv','paired.csv','validation.json']},points=points,outputs=outputs)
    (args.input_dir/'trail_selective_withholding_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(dict(directory=str(args.output_dir),outputs=outputs,points=points),indent=2))


if __name__=='__main__':main()
