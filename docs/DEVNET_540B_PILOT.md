# Exact-size Ethereum transactions

`experiments/trail_devnet_runner.py run --raw-tx-target-bytes 540` calibrates
the length of the **signed** EIP-1559 raw transaction. The option defaults to
disabled, preserving the original empty-data transfer construction.

Calldata is a deterministic nonzero byte stream bound to seed, phase and
transaction index. Bounded deterministic recalibration also handles RLP nonce
boundaries and variable signature integer lengths. Failure to reach the target
is an error. Signatures are recovered before submission and independently
audited after measurement.

Preparation runs before the send schedule and before selecting the measurement
window. It preserves sender assignment, nonces, fees, 64-way submission and
receipt collection. For the padded EOA workload, the active node estimates gas
for a conservative all-nonzero calldata upper bound; the generator adds 10%
margin and verifies that it fits the block gas limit. Actual receipt gas and
block gas fields are retained. No client/protocol or BLS changes are needed.

The isolated configuration is
`experiments/configs/r1_1_540b_performance_pilot.yaml`. It first runs a four-node
smoke, then paired PoS/TRAIL runs at 64, 128 and 256 tx/slot on eight BA nodes,
seed 0 only. Existing images and old results are reused read-only. A fresh suite
name is required to repeat a completed attempt.

Core evidence is explicitly defined as 48 bytes per signature plus 8 bytes per
path identity. Actual SSZ evidence additionally has 64 bytes per record for
binding, fee and offset fields. Both ratios use actual window transaction bytes.
The difference of complete paired block means is reported separately. Archived
short transactions are reconstructed and matched to their hashes to establish
their real length, rather than assuming a 120-byte denominator.
