#!/usr/bin/env python3
"""Independently reconstruct pooled-premium EMA decisions from simulator CSV."""

import argparse
import csv
import json
import math
from pathlib import Path

ESTIMATOR_VERSION = 'ema_premiums_v2'


def audit_run(directory):
    directory = Path(directory)
    config = json.loads((directory / 'run_config.json').read_text())['config']
    alpha = config['adaptive_relay']['benefit_ema_alpha']
    with (directory / 'adaptive_relay_metrics.csv').open() as handle:
        rows = list(csv.DictReader(handle))
    assert [int(r['epoch']) for r in rows] == list(range(config['max_epochs']))
    reward_ema = work_ema = None
    updates = invalid = invalid_switches = negative_decisions = 0

    def check_optional(raw, expected):
        if expected is None:
            assert raw == '', (directory, row['epoch'], raw, expected)
        else:
            assert math.isfinite(float(raw)) and math.isclose(float(raw), expected, rel_tol=1e-10, abs_tol=1e-14), (directory, row['epoch'], raw, expected)

    for row in rows:
        assert row['benefit_estimator'] == ESTIMATOR_VERSION
        due = row['update_applied'] == 'true'
        observed = decision = None
        valid = False
        if due:
            updates += 1
            fields = ['active_expected_reward_per_stake', 'lazy_expected_reward_per_stake',
                      'active_forward_attempts_per_stake', 'lazy_forward_attempts_per_stake']
            if all(row[field] for field in fields):
                ar, lr, aw, lw = (float(row[field]) for field in fields)
                reward_gap, work_gap = ar-lr, aw-lw
                valid = math.isfinite(reward_gap) and math.isfinite(work_gap) and work_gap > 0
                if valid:
                    observed = reward_gap/work_gap
                    valid = math.isfinite(observed)
                    if not valid:
                        observed = None
            if reward_ema is not None:
                reward_ema *= 1-alpha
                work_ema *= 1-alpha
            if valid:
                if reward_ema is None:
                    reward_ema, work_ema = reward_gap, work_gap
                else:
                    reward_ema += alpha*reward_gap
                    work_ema += alpha*work_gap
                decision = reward_ema/work_ema if work_ema > 0 else None
                if decision is not None and not math.isfinite(decision):
                    decision = None
            else:
                invalid += 1
                switches = int(row['switched_to_active'])+int(row['switched_to_lazy'])
                assert switches <= int(row['exploratory_decisions']), (directory, row['epoch'], 'non-exploratory invalid-window switch')
                invalid_switches += switches
        history = reward_ema/work_ema if work_ema is not None and work_ema > 0 else None
        if history is not None and not math.isfinite(history):
            history = None
        assert (row['benefit_observation_valid'] == 'true') == valid
        check_optional(row['observed_benefit_per_forward'], observed)
        check_optional(row['smoothed_benefit_per_forward'], history)
        check_optional(row['decision_benefit_per_forward'], decision)
        check_optional(row['reward_premium_ema'], reward_ema)
        check_optional(row['work_premium_ema'], work_ema)
        negative_decisions += decision is not None and decision < 0
    return dict(raw_source=str(directory), epochs=len(rows), update_windows=updates,
                invalid_windows=invalid, exploratory_switches_in_invalid_windows=invalid_switches,
                negative_decision_windows=negative_decisions, estimator=ESTIMATOR_VERSION)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', type=Path, nargs='+')
    args = parser.parse_args()
    for directory in args.directories:
        print(json.dumps(audit_run(directory)))


if __name__ == '__main__':
    main()
