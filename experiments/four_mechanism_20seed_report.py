#!/usr/bin/env python3
"""Combine immutable seeds200-209 with the matching seeds210-219 extension."""

import argparse
from pathlib import Path
import subprocess
import sys

from four_figure_reference_report import main
from run_experiments import ROOT


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-proof', type=Path,
                        default=Path('results/batches/participation-20seeds-v2-plan/source_equivalence_proof.json'))
    args = parser.parse_args()
    main(baseline_suite='trail_four_baselines_estimator_v2',
         extra_suites={'trail_four_mechanisms_extra8_v2': list(range(202, 210)),
                       'trail_four_mechanisms_extra10_v2': list(range(210, 220))},
         seed_values=list(range(200, 220)),
         destination_name='trail_four_mechanisms_20seeds_v2',
         source_equivalence_proof=args.source_proof)
    destination = ROOT/'results/processed/trail_four_mechanisms_20seeds_v2'
    subprocess.run([sys.executable, str(ROOT/'analysis/plot_trail_relay_participation_v2.py'),
                    '--data', str(destination/'plot_data.csv'),
                    '--output-dir', str(destination/'paper_figures')], cwd=ROOT, check=True)
