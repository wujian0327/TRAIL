# Devnet per-path certificate migration

This change applies only to Ethereum Geth/Lighthouse and their devnet tests.
The root Rust simulator, paper, SSZ types and prior results are unchanged.

## Certificate contract

`trail_getBlockEvidence` returns `signature_scheme: per_path_v1`. Each item in
`transactions` has its own 48-byte `aggregate_signature` and a statement count.
The aggregate contains the existing transaction origin proof and both sender
and receiver signatures for each completed edge. Domain, chain, transaction hash,
epoch, identity registration and prefix chaining remain unchanged. The origin
proof is retained so a zero-hop transaction still has an authenticated origin.
There is no top-level aggregate signature.

Geth reconstructs and verifies that transaction's aggregate before exporting it.
Missing metadata or a malformed/invalid path is listed under
`uncertified_transactions` with transaction index, hash and reason. Evidence
roots remain a separate block metadata commitment, not an aggregate signature.

Lighthouse uses the per-transaction signature without changing the 48-byte SSZ
field or any record/body structure. Each record's aggregate is verified against
its own reconstructed messages and registered keys. Reusing a different
transaction's signature or a legacy block aggregate fails. Empty record paths,
duplicate transaction records, repeated path identities, unknown keys, excessive
path length and excessive work are rejected. Empty evidence lists are permitted.
Past-epoch certificates are authenticated against their tagged epoch but do not
receive propagation score/relay credit; future-epoch records remain invalid.
An invalid certificate prevents the block's TRAIL evidence batch from receiving
credit, as in the prior fail-closed policy.

## Explicit omission policy

Records are visited in Geth's deterministic transaction order. Validly encoded
records are appended only while both the record cap and identity-work budget
permit. Every omission has a transaction hash and reason and is emitted as a
structured Lighthouse warning. Geth's own rejected certificates are also
explicit in its RPC output. Cap/budget decisions do not modify a retained
signature or combine it with any omitted transaction's signature.

The 1,025-record packing test checks that exactly 1,024 records remain, each
keeps its own signature bytes, and the omitted hash/reason is exposed. A separate
verifier check rejects an oversized received list. Coverage is always measured
against all included transactions, including omissions.

## Validation and small devnet comparison

Targeted Go tests cover independent real BLS certificates for two different
multi-hop transactions, per-path verification, transaction/epoch/path tampering,
invalid signatures and explicit rejection records. Lighthouse tests cover
independent real path signatures, legacy block-aggregate rejection, mutation,
unknown keys, repeated identities, empty paths/lists, length/work bounds and
count-cap packing. Two stale pre-existing Lighthouse test fixtures were updated
for the existing inline SSZ fields; short Go test key seeds were padded to the
BLS library's minimum 32 bytes. No simulator or client protocol parameters were
changed to make tests pass.

The dedicated smoke script uses four validators, a ring, seed 0, 16 tx/slot,
five warm-up epochs and two measurement epochs (16 slots / 48 seconds). Both
phases share the same relay key registry and workload settings. Raw outputs
and source snapshots are under `results/raw/devnet_per_path_r1c1_20261118`.
Existing `:dev` images are retained; new clients use distinct before/after tags.
The old and new runs are sequential, and cleanup removes only their own
Kurtosis enclaves.

```bash
GOCACHE="$PWD/target/devnet-r1c1/go-cache" go test ./eth/trail
# Run the command above from the Geth checkout, using an absolute GOCACHE.
cd ethereum-trail/lighthouse
cargo check --offline -p state_processing -p beacon_chain
cargo test --offline -p state_processing -p beacon_chain --lib trail_
cargo build --offline --release -p lighthouse --features spec-minimal
```

`experiments/run_trail_per_path_smoke.py` records per-window SSZ bytes, evidence
payload, coverage, observed missed slots and histogram verification latency. It
also retrieves the actual proposer's Geth certificate for every on-chain record
and checks exact signature correspondence. The after-phase requires a distinct
signature for every distinct certified transaction. All omissions remain in the
audit artifact.

This is a one-seed smoke comparison, not a performance matrix or a statistical
performance claim. Histogram P95/P99 remain coarse interpolated estimates of
per-block TRAIL evidence verification, not whole-block end-to-end latency. For
a fixed path/record list, encoded SSZ length is unchanged because signature
contents changed while field lengths did not. Real observed block means can
differ with packing/propagation and must be reported alongside coverage and
transaction counts.

## Local verifier profiling and caching

The verifier caches parsed, subgroup-validated keys and static identity hashes
within each immutable registry. It rebuilds every transaction/epoch-bound message
and verifies every path signature independently. Prefix hashing is incremental;
the reconstructed messages and key ordering are tested against the original
implementation for 0 through 15 hops. No cross-path batch verifier is enabled.

An opt-in release test compares a frozen reference and the optimized verifier
on identical synthetic real-BLS certificates, with per-stage timing and a
separate first-call measurement that includes cache initialization. It also
tests two invalid signatures with cancelling offsets: naive cross-path addition
accepts them, while the production per-path verifier rejects them.

```bash
cd ethereum-trail/lighthouse
TRAIL_VERIFY_PROFILE_CSV=/tmp/trail-profile-fresh.csv \
  cargo test --offline --release -p state_processing --lib \
  trail_profile_path_verification -- --ignored --nocapture
```

The CSV path must not exist. Results are local microbenchmarks, not Devnet
throughput or paper performance measurements. The earlier Smoke report remains
unchanged and describes the first per-path implementation before caching.
