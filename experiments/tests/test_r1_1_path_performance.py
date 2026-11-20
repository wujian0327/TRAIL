from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from report_r1_1_path_performance import extract_metrics


class PathPerformanceAccountingTests(unittest.TestCase):
    def fixture(self):
        return {
            "workload": {"success_count": 1000, "measurement_window": {
                "duration_seconds": 120, "sent_in_window_count": 64,
                "included_in_window_count": 40, "throughput_tx_per_slot": 1.0}},
            "blocks": {"missed_slots": 0, "audit_rows": [{
                "block_transaction_count": 50, "missing_evidence_transaction_count": 5,
                "valid_evidence_unique_transaction_count": 30,
                "block_ssz_bytes": 2048, "evidence_ssz_bytes": 1024}]},
            "formal_experiment": {
                "spec": {"variant": "trail", "seed": 0, "load_tx_per_slot": 64, "seconds_per_slot": 3},
                "resources": {"per_node_memory_peak_mean_bytes": 2**20,
                    "per_node_network_rx_delta_mean_bytes": 2**20,
                    "per_node_network_tx_delta_mean_bytes": 2**21,
                    "samples": 40, "sample_span_seconds": 116, "node_count": 8},
                "prometheus": {"evidence_verify_count": 10, "evidence_verify_mean_seconds": 0.01,
                    "evidence_verify_bucket_deltas": [{"le": 0.025, "count": 10}, {"le": "+Inf", "count": 10}]},
                "acceptance": {"passed": True}, "measurement_quality": {"passed": True}}}

    def test_window_throughput_excludes_later_inclusion(self):
        row = extract_metrics(self.fixture())
        self.assertEqual(row["included_in_window"], 40)
        self.assertAlmostEqual(row["included_tx_per_second"], 40 / 120)
        self.assertEqual(row["mean_transactions_per_canonical_block"], 50)

    def test_stale_authenticated_certificates_are_covered_but_not_scored(self):
        row = extract_metrics(self.fixture())
        self.assertEqual(row["evidence_coverage_pct"], 90)
        self.assertEqual(row["evidence_certificates"], 45)
        self.assertEqual(row["scoring_eligible_certificates"], 30)
        self.assertEqual(row["missing_certificates"], 5)

    def test_pos_latency_is_unavailable_and_traffic_includes_both_directions(self):
        fixture = self.fixture()
        fixture["formal_experiment"]["spec"]["variant"] = "baseline"
        fixture["formal_experiment"]["prometheus"] = {}
        row = extract_metrics(fixture)
        self.assertIsNone(row["p95_ms"])
        self.assertIsNone(row["p99_ms"])
        self.assertEqual(row["verification_events"], 0)
        self.assertEqual(row["network_mib_per_node"], 3)
        self.assertEqual(row["peak_memory_mib_per_node"], 1)


if __name__ == "__main__":
    unittest.main()
