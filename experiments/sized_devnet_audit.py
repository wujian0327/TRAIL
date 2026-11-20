"""Independent signed-transaction and gas audit, run after timed measurement."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import json

from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from web3 import Web3

from trail_devnet_runner import normalized_hash


def verify_transaction(task):
    tx, target, chain_id = task
    raw = HexBytes(tx["raw_tx_hex"])
    assert len(raw) == target == tx["raw_tx_bytes"], "raw transaction length mismatch"
    assert raw[0] == 2, "transaction is not EIP-1559 Type-2"
    decoded = TypedTransaction.from_bytes(raw).as_dict()
    assert len(decoded["data"]) == tx["calldata_bytes"] and decoded["data"] and all(decoded["data"]), "invalid calldata"
    assert decoded["nonce"] == tx["nonce"] and decoded["chainId"] == chain_id, "nonce/chain mismatch"
    assert decoded["gas"] == tx["gas_limit"] >= tx["gas_used"], "insufficient gas limit"
    assert tx["status"] == 1, "transaction failed"
    assert normalized_hash(Web3.keccak(raw).hex()) == normalized_hash(tx["tx_hash"]), "raw transaction hash mismatch"
    assert Account.recover_transaction(raw).lower() == tx["sender"].lower(), "signature recovery mismatch"
    assert tx["included_block_timestamp"] >= tx["send_unix"] - 3, "invalid inclusion timestamp"
    return dict(phase=tx["phase"], raw_tx_bytes=len(raw), calldata_bytes=len(decoded["data"]), gas_limit=decoded["gas"], gas_used=tx["gas_used"])


def audit_sized_run(folder: Path, summary: dict, target: int):
    phases = [summary.get("warmup"), summary["workload"]]
    tasks = [(tx, target, phase["chain_id"]) for phase in phases if phase for tx in phase["txs"]]
    assert tasks, "no signed transactions to audit"
    with ProcessPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(verify_transaction, tasks, chunksize=32))
    for block in summary["blocks"]["audit_rows"]:
        assert block["execution_transaction_bytes"] == target * block["block_transaction_count"]
        assert all(int(size) == target for size in block["raw_transaction_size_histogram"])
        assert block["block_gas_used"] <= block["block_gas_limit"]
    result = dict(passed=True, target_raw_tx_bytes=target, checked_transactions=len(records),
                  phases={phase:sum(r["phase"] == phase for r in records) for phase in ["warmup", "measurement"]},
                  all_recovered=True, all_successful=True, all_gas_sufficient=True)
    (folder / "transaction_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
