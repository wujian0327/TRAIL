from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments'))
from run_trail_per_path_smoke import audit_live_certificates


class PerPathSmokeAdapterTests(unittest.TestCase):
    def fixture(self,after):
        hashes=['0x'+'11'*32,'0x'+'22'*32]
        signatures=[bytes([i]*48) for i in [1,2 if after else 1]]
        records=[dict(tx_hash=h,aggregate_signature=list(s)) for h,s in zip(hashes,signatures)]
        message=dict(proposer_index='0',body=dict(execution_payload=dict(block_hash='0xaa',transactions=['0x01','0x02']),trail_evidence_records=records))
        evidence=dict(transactions=[dict(tx_hash=h,**(dict(aggregate_signature='0x'+s.hex()) if after else {})) for h,s in zip(hashes,signatures)])
        if after:evidence['signature_scheme']='per_path_v1'
        else:evidence['aggregate_signature']='0x'+signatures[0].hex()
        summary=dict(endpoints=dict(cl_apis=['http://localhost'],el_rpcs=['http://localhost']),blocks=dict(audit_rows=[dict(slot=1)]))
        return dict(data=dict(message=message)),evidence,summary

    def test_beacon_signature_byte_arrays_match_geth_hex_before_and_after(self):
        for after in [False,True]:
            payload,evidence,summary=self.fixture(after)
            with tempfile.TemporaryDirectory() as tmp,patch('run_trail_per_path_smoke.fetch',return_value=payload),patch('run_trail_per_path_smoke.rpc',return_value=evidence):
                rows=audit_live_certificates(None,summary,Path(tmp),'after' if after else 'before')
                self.assertEqual(rows[0]['distinct_signatures'],2 if after else 1)

    def test_swapped_per_transaction_signature_is_detected(self):
        payload,evidence,summary=self.fixture(True)
        evidence['transactions'][0]['aggregate_signature']=evidence['transactions'][1]['aggregate_signature']
        with tempfile.TemporaryDirectory() as tmp,patch('run_trail_per_path_smoke.fetch',return_value=payload),patch('run_trail_per_path_smoke.rpc',return_value=evidence):
            with self.assertRaises(AssertionError):audit_live_certificates(None,summary,Path(tmp),'after')


if __name__=='__main__':unittest.main()
