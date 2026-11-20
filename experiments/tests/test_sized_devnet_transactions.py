from pathlib import Path
import sys
import unittest

from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trail_devnet_runner import (DEFAULT_PREFUNDED_PRIVATE_KEYS, DEFAULT_RECIPIENT,
                                calibrate_signed_transaction, deterministic_calldata)
from sized_devnet_audit import verify_transaction
from web3 import Web3


class SizedTransactionTests(unittest.TestCase):
    def transaction(self, nonce):
        return dict(chainId=3151908, nonce=nonce, to=DEFAULT_RECIPIENT, value=1,
                    gas=46860, maxFeePerGas=50_000_000_000, maxPriorityFeePerGas=2_000_000_000)

    def test_exact_signed_length_across_nonce_rlp_boundaries(self):
        for sender in [0, 7]:
            for nonce in [0, 1, 127, 128, 255, 256, 65535, 65536]:
                with self.subTest(sender=sender, nonce=nonce):
                    fields = self.transaction(nonce)
                    raw, metadata = calibrate_signed_transaction(DEFAULT_PREFUNDED_PRIVATE_KEYS[sender], fields, 540, 0, "measurement", nonce)
                    decoded = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
                    self.assertEqual(len(raw), 540)
                    self.assertEqual(raw[0], 2)
                    self.assertTrue(decoded["data"])
                    self.assertNotIn(0, decoded["data"])
                    self.assertEqual(len(decoded["data"]), metadata["calldata_bytes"])
                    for name in ["chainId", "nonce", "gas", "value", "maxFeePerGas", "maxPriorityFeePerGas"]:
                        self.assertEqual(decoded[name], fields[name])
                    self.assertEqual(Account.recover_transaction(raw), Account.from_key(DEFAULT_PREFUNDED_PRIVATE_KEYS[sender]).address)

    def test_pairing_is_independent_of_repeat_and_binds_seed_phase_index(self):
        key = DEFAULT_PREFUNDED_PRIVATE_KEYS[0]
        fields = self.transaction(8)
        first = calibrate_signed_transaction(key, fields, 540, 0, "measurement", 42)[0]
        self.assertEqual(first, calibrate_signed_transaction(key, fields, 540, 0, "measurement", 42)[0])
        for seed, phase, index in [(1,"measurement",42),(0,"warmup",42),(0,"measurement",43)]:
            self.assertNotEqual(first, calibrate_signed_transaction(key, fields, 540, seed, phase, index)[0])

    def test_impossible_size_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "exactly 20 bytes"):
            calibrate_signed_transaction(DEFAULT_PREFUNDED_PRIVATE_KEYS[0], self.transaction(0), 20, 0, "warmup", 0)

    def test_calldata_prefix_is_deterministic_and_nonzero(self):
        data = deterministic_calldata(7,"measurement",9,420)
        self.assertEqual(data[:100], deterministic_calldata(7,"measurement",9,100))
        self.assertTrue(all(1 <= byte <= 255 for byte in data))

    def audit_fixture(self):
        raw, metadata = calibrate_signed_transaction(DEFAULT_PREFUNDED_PRIVATE_KEYS[0], self.transaction(4), 540, 0, "measurement", 4)
        return dict(metadata, sender=metadata["recovered_sender"], nonce=4, phase="measurement",
                    gas_used=37880, status=1, tx_hash=Web3.keccak(raw).hex(), included_block_timestamp=20, send_unix=18)

    def test_independent_audit_checks_receipt_gas_and_raw_hash(self):
        row=self.audit_fixture()
        self.assertEqual(verify_transaction((row,540,3151908))["raw_tx_bytes"],540)
        with self.assertRaisesRegex(AssertionError,"gas limit"):
            verify_transaction((dict(row,gas_used=46861),540,3151908))
        with self.assertRaisesRegex(AssertionError,"hash mismatch"):
            verify_transaction((dict(row,tx_hash="0x"+"00"*32),540,3151908))

    def test_independent_audit_does_not_trust_sender_or_success_flags(self):
        row=self.audit_fixture()
        with self.assertRaisesRegex(AssertionError,"signature recovery"):
            verify_transaction((dict(row,sender=DEFAULT_RECIPIENT),540,3151908))
        with self.assertRaisesRegex(AssertionError,"transaction failed"):
            verify_transaction((dict(row,status=0),540,3151908))


if __name__ == "__main__":
    unittest.main()
