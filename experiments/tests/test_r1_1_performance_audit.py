from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'analysis'))
from audit_r1_1_performance import evidence_payload,histogram_quantiles,parse_bytes,quantile


class R11PerformanceAuditTests(unittest.TestCase):
    def test_histogram_quantiles_use_window_event_count_and_interpolate(self):
        result=histogram_quantiles([dict(le=.01,count=10),dict(le=.02,count=20),dict(le='+Inf',count=20)],20)
        self.assertAlmostEqual(result['p95_estimate_ms'],19.)
        self.assertAlmostEqual(result['p99_estimate_ms'],19.8)
        self.assertEqual(result['sample_count'],20)
        self.assertEqual((result['p99_bucket_lower_ms'],result['p99_bucket_upper_ms']),(10.,20.))

    def test_bad_histograms_are_rejected_instead_of_silently_repaired(self):
        for buckets,count in [
            ([dict(le=.01,count=12),dict(le='+Inf',count=10)],10),
            ([dict(le=.01,count=-1),dict(le='+Inf',count=10)],10),
            ([dict(le=.01,count=5),dict(le='+Inf',count=10)],11),
            ([dict(le=.01,count=5),dict(le=.01,count=10),dict(le='+Inf',count=10)],10),
            ([dict(le=.01,count=5),dict(le=.02,count=10)],10),
            ([],10)]:
            with self.assertRaises(ValueError):histogram_quantiles(buckets,count)

    def test_tail_in_infinite_bucket_is_unbounded(self):
        result=histogram_quantiles([dict(le=.01,count=9),dict(le='+Inf',count=10)],10)
        self.assertIsNone(result['p99_estimate_ms'])
        self.assertTrue(result['p99_unbounded_tail'])
        self.assertEqual(result['p99_bucket_lower_ms'],10.)

    def test_baseline_without_events_is_undefined(self):
        result=histogram_quantiles([],0)
        self.assertIsNone(result['p95_estimate_ms'])
        self.assertIsNone(result['p99_estimate_ms'])

    def test_docker_units_include_scientific_notation(self):
        self.assertEqual(parse_bytes('1e+03kB '),1e6)
        self.assertEqual(parse_bytes('1 MiB'),1048576.)
        self.assertEqual(parse_bytes('1MB'),1000000.)

    def test_ssz_payload_counts_list_offsets_and_binary_fields(self):
        record=dict(tx_hash='0x'+'11'*32,epoch=7,priority_fee_wei=9,irrecoverable_cost_wei=10,
                    aggregate_signature_hex='22'*48,aggregate_signature_len=48,path=[1,2,3])
        payload=evidence_payload([record])
        self.assertEqual(len(payload),112+8*3)
        self.assertEqual(payload[:4],(4).to_bytes(4,'little'))
        self.assertEqual(payload[4:36],bytes.fromhex('11'*32))
        self.assertEqual(evidence_payload([]),b'')

    def test_raw_sample_percentiles_interpolate_without_unit_conversion(self):
        self.assertEqual(quantile([0.,10.],.99),9.9)
        self.assertEqual(quantile([0.,10.],.5),5.)


if __name__=='__main__':unittest.main()
