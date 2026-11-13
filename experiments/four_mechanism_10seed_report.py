#!/usr/bin/env python3
"""Join the existing two seeds with eight new seeds and audit/export all four figures."""

from four_figure_reference_report import main


if __name__ == '__main__':
    main(baseline_suite='trail_four_baselines_estimator_v2',
         extra_suite='trail_four_mechanisms_extra8_v2',seed_values=list(range(200,210)),
         destination_name='trail_four_mechanisms_10seeds_v2')
