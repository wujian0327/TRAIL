# BLS same-message fast aggregate benchmark

[Vector PDF](../figures/trail_overhead/bls_same_message_latency.pdf) · [PNG](../figures/trail_overhead/bls_same_message_latency.png) · [Raw samples](../results/processed/bls_same_message_latency/samples.csv) · [Summary CSV](../results/processed/bls_same_message_latency/summary.csv) · [Run environment](../results/processed/bls_same_message_latency/environment.json)

The standalone BLS12-381 benchmark uses BLST 0.3.13 and the minimal-signature-size proof-of-possession ciphersuite. All signers sign the same fixed 32-byte message. The archived CSVs use `hops` for the number of signers. A signing sample times \(h\) sequential calls to `SecretKey::sign`. A verification sample times one call to `fast_aggregate_verify_pre_aggregated` with signature subgroup checking enabled. Key generation, proof-of-possession checks, public-key aggregation, and signature aggregation occur during setup. Each verification executes the cryptographic check. The input to that check is one prepared public key, one message, and one aggregate signature for all signer counts. The full `FastAggregateVerify` algorithm also sums the individual public keys if they are supplied at verification time. [CFRG BLS draft, §3.3.4](https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-bls-signature-07#section-3.3.4) · [Rust benchmark](../src/bin/bls_same_message_latency.rs) · [TRAIL multi-message benchmark](bls_hop_latency.md)

The benchmark ran in Release mode with Rust 1.94.0 on an AMD Ryzen 9 9955HX. The process was pinned to logical CPU 2. Each hop count and operation had 50 warmups and 1,000 measured samples. The starting hop count rotated each round to distribute time-dependent load across the configurations. The plot shows one mean per point, with error bars extending one sample standard deviation above and below it. The summary CSV provides the nearest-rank P95 and P99.

| Signers | Signing mean (ms) | Signing P95 (ms) | Signing P99 (ms) | Verification mean (ms) | Verification P95 (ms) | Verification P99 (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.093 | 0.102 | 0.141 | 0.610 | 0.678 | 0.839 |
| 2 | 0.183 | 0.203 | 0.260 | 0.608 | 0.682 | 0.804 |
| 4 | 0.368 | 0.408 | 0.517 | 0.610 | 0.680 | 0.843 |
| 6 | 0.551 | 0.623 | 0.745 | 0.610 | 0.691 | 0.836 |
| 8 | 0.736 | 0.828 | 1.001 | 0.612 | 0.709 | 0.860 |
| 16 | 1.467 | 1.597 | 1.801 | 0.613 | 0.685 | 0.842 |

Sequential signing time grew roughly linearly, from 0.093 ms for one signer to 1.467 ms for 16 signers. With a prepared aggregate public key, mean verification time ranged from 0.608 to 0.613 ms across all six configurations. Verification P95 ranged from 0.678 to 0.709 ms, and P99 ranged from 0.804 to 0.860 ms.

## Verification audit

Each configuration contributes \(h\) BLS signatures and \(h\) distinct signer–message pairs \((PK_i, m)\) that share the same message. The timed BLST call receives one effective pair \((\sum_i PK_i, m)\) and one aggregate signature. A Release test checked each signature against its own public key and checked the aggregate against the explicit list of all \(h\) pairs. A second test replaced only the indicated signer's signature with a valid signature on a different message, rebuilt the aggregate, and verified against the original message and aggregate public key. BLST returned `BLST_VERIFY_FAIL` for all 15 replacements. [Rust tests](../src/bin/bls_same_message_latency.rs#L186)

| Signers | Signatures per sample | Original signer–message pairs | Pairs in timed verification | Rejected replacement positions, one-based |
| ---: | ---: | ---: | ---: | --- |
| 1 | 1 | 1 | 1 | 1 |
| 2 | 2 | 2 | 1 | 1, 2 |
| 4 | 4 | 4 | 1 | 1, 3, 4 |
| 6 | 6 | 6 | 1 | 1, 4, 6 |
| 8 | 8 | 8 | 1 | 1, 5, 8 |
| 16 | 16 | 16 | 1 | 1, 9, 16 |

BLST creates a fresh pairing context per call, hashes the original message to G1, checks aggregate-signature subgroup membership, and computes the Miller loops and final exponentiation within the timed interval. The benchmark reuses the prepared key and signature points. The code path has no verification-result or pairing-result cache. [BLST 0.3.13 Rust implementation](https://docs.rs/crate/blst/0.3.13/source/src/lib.rs) · [BLST pairing implementation](https://raw.githubusercontent.com/supranational/blst/v0.3.13/src/aggregate.c)

Pairing bilinearity lets a verifier combine public keys for a shared message before checking the aggregate signature. The summed key must correspond to the intended signer set, and each key must pass proof-of-possession validation to prevent rogue-key forgery. The fixture performs these checks before timing. The measured latency applies to the shared-message check with a prepared aggregate key. A certificate with distinct messages per hop requires verification against the corresponding signer–message pairs. [CFRG BLS draft, §3.3.4](https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-bls-signature-07#section-3.3.4)

All means and percentiles in the table above were independently reproduced from the 12,000 existing timing samples. The Release audit passed all three Rust tests. The audit used the original timings and generated no new latency samples.

```bash
out=$(mktemp -d /tmp/bls-hop.XXXXXX)
cargo test --offline --release --bin bls_same_message_latency
cargo build --offline --release --bin bls_same_message_latency
taskset -c 2 target/release/bls_same_message_latency --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_bls_hop_latency.py --same-message \
  --samples "$out/data/samples.csv" --output "$out/bls_same_message_latency.pdf"
```
