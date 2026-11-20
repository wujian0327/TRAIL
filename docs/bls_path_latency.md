# TRAIL single-path BLS latency

[Combined signing and verification PDF](../figures/trail_overhead/bls_signing_verification_latency.pdf) · [Raw timing samples](../results/processed/bls_path_latency/samples.csv) · [Statistics](../results/processed/bls_path_latency/summary.csv) · [Run environment](../results/processed/bls_path_latency/environment.json)

## Measurement

Each hop count uses one valid transaction and one complete path with `h+1` distinct validators from the current Rust `ValidatorRegistry`. The production path builder creates the signed hop prefixes at epoch 7 using the default Chain ID 7,032,030. The origin signs as the sender on the first hop, so the certificate aggregates exactly `2h` signatures. The fixture reproduces each production hop signature and the 48-byte final aggregate byte for byte. The full uncached path verifier checks the production certificate during setup. [Benchmark](../src/bin/bls_path_latency.rs) · [Path implementation](../src/blockchain/path.rs)

- Signing measures `2h` BLST signatures and the production aggregation sequence, including its checks on the added signatures. Every generated aggregate is compared with the validated fixture after timing. The signing curve shows the mean of the per-sample signing-plus-aggregation total.
- Verification times a single `blst::min_sig::Signature::aggregate_verify` call with the prepared production signer-message pairs, the parsed certificate and the same group-check flags as the wallet verifier. Each call performs the BLS verification and must succeed. The same axis shows the verification mean with error bars ending at the measured P95 and P99. [Wallet verifier](../src/wallet/mod.rs#L269)

Wallet and transaction construction, path hashing, registry lookups, signature decoding, ECDSA verification, propagation, serialization and file output are outside the timers. The timings use wall-clock durations and include scheduling within the BLST verification call. Each operation has 50 discarded warmups and 1,000 measured samples per hop count. P95 and P99 use nearest rank, `ceil(p n)`, applied to sorted nanosecond durations. The raw CSV pairs the separate signing and verification series by sample index. All 24 summaries were independently reconciled with the raw samples.

The run used Rust 1.94.0 in Cargo Release mode on Linux x86-64 with BLST 0.3.13. The AMD Ryzen 9 9955HX host has 16 physical cores and 32 logical CPUs. The benchmark was pinned to logical CPU 2 and reported one available logical CPU. The benchmark uses one caller and BLST's default worker configuration under that affinity. [Run environment](../results/processed/bls_path_latency/environment.json) · [Dependency lockfile](../Cargo.lock)

## Results

All values are in milliseconds. Signing total is the sum of BLS signing and aggregation for each sample.

| Relay hops | Hop signatures | Signing mean | Signing P95 | Signing P99 | Verification mean | Verification P95 | Verification P99 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 0.228 | 0.360 | 0.446 | 0.842 | 1.045 | 1.157 |
| 2 | 4 | 0.489 | 0.667 | 0.839 | 1.153 | 1.352 | 1.564 |
| 4 | 8 | 0.911 | 0.975 | 1.057 | 1.828 | 1.972 | 2.035 |
| 6 | 12 | 1.354 | 1.378 | 1.400 | 2.517 | 2.562 | 2.744 |
| 8 | 16 | 1.814 | 1.848 | 1.907 | 3.251 | 3.475 | 3.603 |
| 16 | 32 | 3.760 | 4.098 | 4.316 | 6.138 | 6.572 | 7.105 |

### English results paragraph

The mean cost of producing a TRAIL path certificate increased from 0.228 ms at one hop to 3.760 ms at 16 hops as the number of hop signatures increased from 2 to 32. At four hops, BLS signing and aggregation took 0.727 ms and 0.185 ms on average, respectively. For one uncached aggregate verification per certificate, P95 and P99 were 1.972 ms and 2.035 ms at four hops and reached 6.572 ms and 7.105 ms at 16 hops. These figures characterize single-path BLS computation with prepared inputs on one logical CPU. [Summary CSV](../results/processed/bls_path_latency/summary.csv)

## Reproduction

Three benchmark tests and 20 existing Rust path tests passed. They cover signature counts, a changed signed statement, raw-sample percentile ranks and the production path security rules. [Benchmark tests](../src/bin/bls_path_latency.rs) · [Plotting script](../experiments/plot_bls_path_latency.py)

```bash
out=$(mktemp -d /tmp/trail-bls-path.XXXXXX)
cargo test --offline --bin bls_path_latency
cargo test --offline --lib blockchain::path::tests
cargo build --offline --release --bin bls_path_latency
taskset -c 2 target/release/bls_path_latency --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_bls_path_latency.py \
  --summary "$out/data/summary.csv" --output "$out/bls_signing_verification_latency.pdf"
```
