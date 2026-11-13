#!/usr/bin/env python3
"""Render the four pilot figures in the original paper's compact figure style."""

import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/trail-four-figure-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# Preserve the original PoS/Fee-only/Full encodings; distinguish Ersoy in green.
STYLES = {'pos':('#6B6B6B','^',':','PoS'),
          'trail_eta0':('#0072B2','s','--','Fee-only'),
          'ersoy_fee':('#009E73','D','-.','Ersoy'),
          'trail':('#D55E00','o','-','Full TRAIL')}
PANELS = {
    'a':('fig_a_epoch_active','(a) Participation over time'),
    'b':('fig_b_fee_active','(b) Participation vs. transaction fee'),
    'c':('fig_c_cost_active','(c) Active relay stake'),
    'd':('fig_d_cost_inclusion','(d) Transaction inclusion time'),
}
UNCERTAINTY = ('Curves are equal-weight means of seeds 200 and 201. Shading in (a) '
               'and error bars in (b-d) show the observed minimum-to-maximum seed range, '
               'not a confidence interval. Across-seed t intervals remain in the CSV.')
STYLE_METADATA = dict(
    reference='User-supplied original Fig. 5; analysis/plot_trail_relay_participation.py',
    font='Times New Roman, with Nimbus Roman / DejaVu Serif fallbacks',
    series={protocol:dict(color=color,marker=marker,linestyle=line,label=label)
            for protocol,(color,marker,line,label) in STYLES.items()},
    line_width_pt=1.2,marker_face='white',grid='light gray, horizontal only',
    legend='compact, inside each axis',panel_captions='below axes in the composite',
    uncertainty=UNCERTAINTY,
    data_change='Plotting preserves input sample values and aggregation results.')
CAPTION = (
    'Relay participation and transaction inclusion under a shared PoS propagation model. '
    'Full TRAIL uses eta=0.5 and theta=0.25; Ersoy uses C=0.25 and stake-only election. '
    '(a) Active relay stake over epochs at F=0.001 and forwarding cost 5x. '
    '(b) Fee sensitivity at cost 5x (logarithmic fee axis). '
    '(c) Active relay stake versus cost at F=0.001. '
    '(d) Restricted mean inclusion time versus cost at F=0.001, with a fixed 50-second follow-up. '
    'Cost axes are logarithmic; 1x is a median per-attempt forwarding cost of '
    '1.4055695425812074e-7 token. Missing or late inclusions receive the 50-second cap. '
    + UNCERTAINTY + ' Participation in (b,c) is averaged over epochs 100-199; '
    'inclusion cohorts are generated in epochs 100-189. All times are logical simulation times. '
    'These are exploratory two-seed pilot results. Single-panel exports omit the caption '
    'for insertion into the paper; this text supplies their shared measurement context.\n')


def uncertainty_for(seeds):
    if list(seeds)==[200,201]:
        return UNCERTAINTY
    if len(seeds)<5:
        return (f'Curves are equal-weight means across {len(seeds)} seeds. '
                'Shading and error bars show the observed seed range, not confidence intervals.')
    return (f'Curves are equal-weight means across {len(seeds)} seeds. '
            'Shading in (a) and error bars in (b-d) are pointwise 95% Student-t '
            f'confidence intervals across seeds (df={len(seeds)-1}); epochs are not independent replicates.')


def caption_for(seeds):
    if list(seeds)==[200,201]:
        return CAPTION
    return CAPTION.replace(UNCERTAINTY,uncertainty_for(seeds)).replace(
        'These are exploratory two-seed pilot results.',
        f'These are {len(seeds)}-seed results over a finite observation window.')


def render(directory):
    directory = Path(directory)
    with (directory/'plot_data.csv').open(newline='') as handle:
        data = list(csv.DictReader(handle))
    for row in data:
        row['x'],row['y'] = float(row['x']),float(row['y'])
    seeds = sorted({int(r['seed']) for r in data if r['kind']=='seed'})
    use_ci = len(seeds)>=5
    clipped_ci = use_ci and any(r['y']-float(r['ci95'])<0 or r['y']+float(r['ci95'])>100
                               for r in data if r['kind']=='mean' and r['panel']!='d')
    protocols = [p for p in STYLES if any(r['protocol']==p for r in data)]
    inclusion_max = (max(r['y']+float(r['ci95']) for r in data if r['panel']=='d' and r['kind']=='mean')
                     if use_ci else max(r['y'] for r in data if r['panel']=='d'))
    inclusion_top = 1.8 if inclusion_max<=1.8 else math.ceil(inclusion_max*1.08)
    plt.rcParams.update({
        'font.family':'serif',
        'font.serif':['Times New Roman','Nimbus Roman','DejaVu Serif'],
        'mathtext.fontset':'stix',
        'font.size':8.5,'axes.labelsize':9.5,'legend.fontsize':7,
        'xtick.labelsize':7.5,'ytick.labelsize':7.5,
        'axes.spines.top':False,'axes.spines.right':False,'axes.grid':False,
        'pdf.fonttype':42,'ps.fonttype':42,'svg.fonttype':'none','savefig.bbox':None,
    })

    def draw(ax,panel):
        for protocol in protocols:
            color,marker,line,label = STYLES[protocol]
            rows = [r for r in data if r['panel']==panel and r['protocol']==protocol]
            points = sorted((r for r in rows if r['kind']=='mean'),key=lambda r:r['x'])
            x,y = [r['x'] for r in points],[r['y'] for r in points]
            samples = {value:[] for value in x}
            for row in rows:
                if row['kind']=='seed':
                    samples[row['x']].append(row['y'])
            assert all(len(values)==len(seeds) for values in samples.values())
            assert all(int(r['n_seeds'])==len(seeds) for r in points)
            low = ([r['y']-float(r['ci95']) for r in points] if use_ci else
                   [min(samples[value]) for value in x])
            high = ([r['y']+float(r['ci95']) for r in points] if use_ci else
                    [max(samples[value]) for value in x])
            if panel=='a':
                ax.fill_between(x,low,high,color=color,alpha=.12,linewidth=0,zorder=1)
                ax.plot(x,y,color=color,linestyle=line,marker=marker,
                        markevery=list(range(0,len(x),20))+[len(x)-1],
                        markerfacecolor='white',markeredgewidth=.9,
                        linewidth=1.2,markersize=3.8,label=label,zorder=2)
            else:
                # Pilot: observed seed range. Larger batches: across-seed t intervals.
                ax.errorbar(x,y,yerr=[[a-b for a,b in zip(y,low)],
                                     [a-b for a,b in zip(high,y)]],
                            color=color,linestyle=line,marker=marker,
                            markerfacecolor='white',markeredgewidth=1,
                            linewidth=1.2,elinewidth=.8,markersize=4.5,capsize=2.5,
                            label=label,zorder=2)
        ax.set_ylabel('Inclusion time (s)' if panel=='d' else 'Active relay stake (%)')
        if panel=='a':
            ax.set(xlabel='Epoch',xlim=(0,200),ylim=(0,100))
            ax.set_xticks([0,50,100,150,200])
        elif panel=='b':
            values = [.0001,.0005,.001,.005,.01]
            ax.set_xscale('log')
            ax.set_xticks(values,[f'{value:g}' for value in values])
            ax.set(xlabel=r'Transaction fee $F$',ylim=(0,100))
            ax.set_xlim(.000075,.0133)
            ax.minorticks_off()
        else:
            ax.set_xscale('log')
            ax.set_xticks([1,2,5],[r'$1\times$',r'$2\times$',r'$5\times$'])
            ax.set_xlabel(r'Median relay cost / $C_{\mathrm{ref}}$')
            ax.set_xlim(.88,5.7)
            ax.minorticks_off()
            ax.set_ylim(0,inclusion_top if panel=='d' else 100)
        if panel=='d' and inclusion_top>1.8:
            ax.yaxis.set_major_locator(MaxNLocator(nbins=6,integer=True))
        else:
            ax.set_yticks([0,.3,.6,.9,1.2,1.5,1.8] if panel=='d' else [0,20,40,60,80,100])
        ax.grid(axis='y',color='#E6E6E6',linewidth=.7,zorder=0)
        ax.spines['left'].set_linewidth(.9)
        ax.spines['bottom'].set_linewidth(.9)
        ax.tick_params(width=.8,length=3)
        positions = ({'a':'center right','b':'center right','c':'center left','d':'center left'}
                     if len(protocols)==4 else
                     {'a':'lower right','b':'upper left','c':'lower left','d':'upper left'})
        extra = dict(loc='lower right',bbox_to_anchor=(.995,.12)) if len(protocols)==4 and panel=='a' else dict(loc=positions[panel])
        ax.legend(**extra,
                  frameon=True,framealpha=.9,facecolor='white',edgecolor='#D0D0D0',
                  borderpad=.35,handletextpad=.5)

    def save(fig,name):
        for extension in ['png','pdf','svg']:
            fig.savefig(directory/f'{name}.{extension}',dpi=300)

    fig,axes = plt.subplots(2,2,figsize=(7.45,6.35))
    fig.subplots_adjust(left=.09,right=.985,top=.98,
                        bottom=.26 if clipped_ci else (.23 if len(protocols)==4 else .21),
                        wspace=.31,hspace=.5)
    for ax,panel in zip(axes.flat,PANELS):
        draw(ax,panel)
        ax.text(.5,-.32,PANELS[panel][1],transform=ax.transAxes,
                ha='center',va='top',fontsize=10)
    seed_label = (f'{seeds[0]}-{seeds[-1]}' if seeds==list(range(seeds[0],seeds[-1]+1)) else
                  ', '.join(map(str,seeds)))
    note = (f'{len(seeds)} seeds ({seed_label}); shading / error bars: 95% pointwise confidence intervals across seeds.\n'
            if use_ci else
            f'{len(seeds)}-seed pilot ({seed_label}); shading / error bars: seed range, not 95% confidence intervals.\n')
    note += r'Full: $\eta=0.5$, $\theta=0.25$; '
    if 'trail_eta0' in protocols:
        note += r'Fee-only: $\eta=0$, $\theta=0.25$; '
    note += r'Ersoy: $C=0.25$; $g=0.0001$.'
    note += ('\n' if len(protocols)==4 else ' ')
    note += r'Reference: $F=0.001$, cost=$5\times$; follow-up: 50 s.'
    if clipped_ci:
        note += '\nActive-stake intervals beyond 0-100% are clipped; exact intervals remain in CSV.'
    fig.text(.5,.022,note,
             ha='center',va='bottom',fontsize=7.3,linespacing=1.4)
    save(fig,'four_panels')
    fig.savefig(directory/'four_panels_preview.png',dpi=150)
    plt.close(fig)
    for panel,(name,_) in PANELS.items():
        fig,ax = plt.subplots(figsize=(3.45,2.55))
        draw(ax,panel)
        fig.subplots_adjust(bottom=.18,left=.16,right=.97,top=.97)
        save(fig,name)
        plt.close(fig)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path(__file__).resolve().parent)
    render(parser.parse_args().data_dir)
