#!/usr/bin/env python3
"""Merge the matched PoS/Fee-only batch with the existing Full/Ersoy pilot."""

from four_figure_reference_report import main


if __name__ == '__main__':
    main(baseline_suite='trail_four_baselines_estimator_v2',
         destination_name='trail_four_mechanisms_v2')
