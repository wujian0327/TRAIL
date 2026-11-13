import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_experiments import ROOT, command_for_run, expand_runs, load_yaml
from ersoy_participation_report import METRICS, aggregate


class ErsoyParticipationTests(unittest.TestCase):
    def test_fixed_g_pilot_varies_only_fee_across_four_levels(self):
        spec = load_yaml(ROOT / 'experiments/configs/trail_fee_participation_fixed_g_pilot.yaml')
        runs = expand_runs(spec)
        self.assertEqual(len(runs), 32)
        self.assertEqual(len({r['run_id'] for r in runs}), 32)
        self.assertEqual({r['transaction_fee'] for r in runs}, {.0001, .0002, .0005, .001})
        self.assertEqual({r['seed_value'] for r in runs}, {200, 201})
        for run in runs:
            command = command_for_run(spec['binary'], run)
            self.assertEqual(float(command[command.index('--irrecoverable-cost')+1]), .0001)
            self.assertEqual(float(command[command.index('--initial-liquid-balance')+1]), 1)
            self.assertEqual(float(command[command.index('--transaction-fee')+1]), run['transaction_fee'])

    def test_formal_design_and_cli_prevent_accidental_proposer_bonus(self):
        spec = load_yaml(ROOT / 'experiments/configs/trail_ersoy_participation_main.yaml')
        runs = expand_runs(spec)
        self.assertEqual(len(runs), 240)
        self.assertEqual(len({r['run_id'] for r in runs}), 240)
        for run in runs:
            if run['protocol_label'] != 'ersoy_fee':
                continue
            command = command_for_run(spec['binary'], run)
            self.assertEqual(command[command.index('--reward-policy')+1], 'ersoy')
            self.assertEqual(float(command[command.index('--eta')+1]), 0)
            self.assertEqual(float(command[command.index('--ersoy-c')+1]), .25)

    def test_ablation_keeps_bonus_and_changes_only_score_smoothing(self):
        spec = load_yaml(ROOT / 'experiments/configs/trail_ersoy_participation_no_ema.yaml')
        runs = expand_runs(spec)
        self.assertEqual(len(runs), 60)
        self.assertTrue(all(r['beta'] == 1 and r['eta'] == .5 and r['adaptive_benefit_ema_alpha'] == .25 for r in runs))

    def test_paired_contrast_uses_seed_differences(self):
        rows = []
        for name, values in [('Full TRAIL', [10, 30]), ('Fee-only', [9, 29])]:
            for seed, value in enumerate(values):
                rows.append(dict(mechanism=name,cost_multiplier=2,seed=seed,
                                 **{metric:value for metric in METRICS}))
        _, contrasts = aggregate(rows)
        self.assertTrue(all(r['mean_difference'] == 1 and r['ci95'] == 0 for r in contrasts))
        rows[-1]['seed'] = 8
        with self.assertRaises(AssertionError):
            aggregate(rows)


if __name__ == '__main__':
    unittest.main()
