import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments"))
from run_experiments import command_for_run, expand_runs, load_yaml, seed_bundle
from trail_coalition_favoritism_report import aggregate_run, graph_identity, pair_runs, validate_observed_receipts


class CoalitionFavoritismTests(unittest.TestCase):
    def test_matrices_and_seed_parameters(self):
        for name, count in [("pilot", 12), ("main", 240)]:
            spec = load_yaml(ROOT / f"experiments/configs/trail_coalition_favoritism_{name}.yaml")
            runs = expand_runs(spec)
            self.assertEqual(len(runs), count)
            self.assertEqual(len({r["run_id"] for r in runs}), count)
            for run in runs:
                for key, value in seed_bundle(run["seed_value"]).items():
                    self.assertEqual(run[key], value)
                command = command_for_run("target/release/trail", run)
                self.assertEqual(command[command.index("--coalition-path-selection") + 1],
                                 run["coalition_path_selection"])
                self.assertEqual(run["irrecoverable_cost"], 0.00001)

    def test_old_configuration_does_not_enable_collection(self):
        spec = load_yaml(ROOT / "experiments/configs/frozen_v1_security_main.yaml")
        for run in expand_runs(spec):
            self.assertNotIn("--coalition-path-selection", command_for_run("target/release/trail", run))

    def test_graph_pairing_ignores_undirected_export_orientation(self):
        self.assertEqual(graph_identity([["a", "b"], ["c", "a"]]),
                         graph_identity([["a", "c"], ["b", "a"]]))

    def test_different_signed_prefixes_are_rejected_but_unsigned_candidates_are_allowed(self):
        def audit(prefix, signature):
            return dict(candidate_prefixes=[dict(transaction=dict(hash="same-transaction"),
                chain_id=7032030, epoch=10, paths=[{
                    "from": "relay", "to": "receiver", "prefix": prefix,
                    "receiver_signature": signature}])])
        self.assertEqual(validate_observed_receipts([audit("a", None), audit("b", None)]), 0)
        self.assertEqual(validate_observed_receipts([audit("a", "sig"), audit("a", "sig")]), 1)
        with self.assertRaisesRegex(ValueError, "conflicting signed incoming prefixes"):
            validate_observed_receipts([audit("a", "sig-a"), audit("b", "sig-b")])

    def test_income_uses_actual_payments_once_and_pairs_against_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for mode, extra in [("honest", 0), ("favoritism", 0.1)]:
                directory = Path(tmp) / mode
                directory.mkdir()
                run = dict(output_dir=str(directory), max_epochs=3, run_id=mode, seed_value=0,
                           adversary_stake_fraction=0.1, adversary_placement="random",
                           coalition_path_selection=mode)
                (directory / "run_summary.json").write_text(json.dumps(dict(completed_epochs=3,
                    coalition_identity_hash="same-coalition", background_workload_hash="same-workload")))
                (directory / "run_config.json").write_text(json.dumps(dict(git_commit_sha="same",
                    git_source_diff_sha256="same", config=dict(run_id=mode, output_dir=str(directory),
                    coalition_path_selection=mode, wallet_seed=42))))
                (directory / "graph.json").write_text('[["a","b"]]')
                (directory / "initial_stakes.json").write_text('{"alice":1.0,"bob":9.0}')
                (directory / "proposer_duties.csv").write_text(
                    "epoch,slot,proposer_address\n0,1,same-initial-proposer\n")
                (directory / "coalition_actual_payments.json").write_text('{"0":0,"1":0.25,"2":0.5}')
                (directory / "coalition_path_selections.jsonl").write_text("")
                # Requests denied BEFORE signing are descriptive diagnostics,
                # and must not be confused with two accepted receiving proofs.
                (directory / "epoch_metrics.csv").write_text("epoch,conflicting_receipt_count\n0,1\n1,0\n2,2\n")
                economics = []
                for epoch, phase in enumerate(["warmup", "attack", "post"]):
                    economics.append(dict(epoch=epoch, phase=phase, relay_reward=1.0 + extra,
                        proposer_reward=2.0, transaction_cost=999, modeled_net_income=-996,
                        raw_contribution=0.5, honest_relay_reward=0.4, proposer_weight_share=0.12,
                        next_epoch_expected_base_proposer_reward=0.06, actual_stake_share=0.1,
                        theoretical_weight_bound=0.142857142857, rejected_candidates=0, capped_candidates=0))
                (directory / "coalition_epoch_economics.jsonl").write_text(
                    "\n".join(json.dumps(row) for row in economics))
                row = aggregate_run(run)
                self.assertEqual(row["rejected_receipt_attempts"], 3)
                self.assertAlmostEqual(row["total_transaction_cost"], 0.75)
                self.assertAlmostEqual(row["total_modeled_net_income"], 5.25 + 2 * extra)
                rows.append(row)
            # No eligible candidate must fail; zero income/opportunities cannot
            # silently be exported as evidence of attack unprofitability.
            with self.assertRaisesRegex(ValueError, "no genuine multi-path"):
                pair_runs(rows)
            rows[1]["attack_eligible"] = 2
            rows[1]["attack_effective"] = 1
            paired = pair_runs(rows)
            self.assertAlmostEqual(paired[0]["total_delta_modeled_net_income"], 0.2)
            self.assertAlmostEqual(paired[0]["total_delta_transaction_cost"], 0)
            rows[1]["actual_stake_share"] += 1e-16
            self.assertEqual(len(pair_runs(rows)), 1)
            rows[1]["actual_stake_share"] += 1e-5
            with self.assertRaisesRegex(ValueError, "actual_stake_share"):
                pair_runs(rows)
            rows[1]["actual_stake_share"] = rows[0]["actual_stake_share"]
            rows[1]["initial_proposer_address"] = "different-initial-proposer"
            rows[1]["background_workload_hash"] = "different"
            rows[1]["config"]["wallet_seed"] = 99
            self.assertEqual(len(pair_runs(rows)), 1)
            rows[1]["initial_stake_identity"] = (("alice", 2.0), ("bob", 8.0))
            with self.assertRaisesRegex(ValueError, "initial_stake_identity"):
                pair_runs(rows)


if __name__ == "__main__":
    unittest.main()
