# TRAIL bidirectional hop certificate latency

[Vector PDF](../figures/trail_overhead/bls_hop_latency.pdf) · [PNG](../figures/trail_overhead/bls_hop_latency.png) · [Raw samples](../results/processed/bls_hop_latency/samples.csv) · [Summary CSV](../results/processed/bls_hop_latency/summary.csv) · [Run environment](../results/processed/bls_hop_latency/environment.json)

Each hop count repeats one valid transaction and its complete production path as a deterministic fixture. A path of \(h\) hops has \(h+1\) registered identities, \(h\) distinct Hop Statements, and \(2h\) signer–message pairs. The sender and receiver sign the same Statement for their hop. Its prefix binds the transaction hash, Chain ID, epoch, and ordered path. The origin signs as the first sender. The production certificate stores one 48-byte aggregate signature. The benchmark uses epoch 7 and Chain ID 7,032,030. [Path implementation](../src/blockchain/path.rs) · [Benchmark](../src/bin/bls_hop_latency.rs)

## Timing boundaries

- **Signing + aggregation** times \(2h\) BLS signatures over prepared production Statements, followed by the production sequence of point additions and checks. Every sample's signatures and aggregate match the corresponding production outputs. Wallet and transaction creation, Statement construction, and signature encoding run outside the timer. The CSV also reports signing and aggregation separately.
- **BLS aggregate verification** calls the production `Wallet::bls_aggregated_verify_parsed` with the prepared Statements, registry keys, and parsed aggregate signature. Input cloning is outside the timer. The timed call includes the wallet wrapper and BLST's actual multi-message `aggregate_verify`, with signature and public-key group checks enabled. [Wallet verifier](../src/wallet/mod.rs#L269)
- **Complete certificate verification** times the uncached production evidence check after the transaction has been verified in setup. Each timed call checks Chain ID and epoch, resolves the validator indices and proposer, checks the origin and distinct identities, decodes the signature, rebuilds all prefixes and Statements, looks up the signer keys, and executes aggregate verification. The benchmark adapter requires a certificate and transaction that pass the original uncached verifier before it exposes the timed path. It is available under the `bls-hop-latency` feature. [Benchmark adapter](../src/blockchain/path/latency_bench.rs)

All timers exclude transaction ECDSA verification, key generation, network operations, and file I/O. The verification timers bypass the result cache and execute the pairing check on every sample. Verification order alternates between the two modes to avoid always timing one mode first. The figure shows the first two measurements in one panel with symmetric error bars of one sample standard deviation around each mean. The two CSVs contain the full set of timings.

## Results

Rust 1.94.0 and BLST 0.3.13 ran in Release mode on an AMD Ryzen 9 9955HX, pinned to logical CPU 2. Each hop count and operation used 50 warmups and 1,000 measured samples. P95 and P99 use nearest rank on the raw nanosecond durations. All 30 summary rows were reconciled against 30,000 recorded durations, including the per-sample signing totals derived from the two component durations.

Each cell reports Mean / P95 / P99 in milliseconds.

| Hops | Signer–message pairs | Signing + aggregation | BLS aggregate verification | Complete certificate verification |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 0.214 / 0.236 / 0.261 | 0.813 / 0.859 / 0.936 | 0.827 / 0.882 / 0.921 |
| 2 | 4 | 0.475 / 0.547 / 0.591 | 1.203 / 1.321 / 1.401 | 1.217 / 1.345 / 1.401 |
| 4 | 8 | 0.947 / 1.052 / 1.100 | 1.871 / 2.026 / 2.079 | 1.896 / 2.048 / 2.125 |
| 6 | 12 | 1.384 / 1.436 / 1.465 | 2.546 / 2.596 / 2.646 | 2.577 / 2.632 / 2.662 |
| 8 | 16 | 1.953 / 2.227 / 2.386 | 3.403 / 3.713 / 3.839 | 3.440 / 3.747 / 3.894 |
| 16 | 32 | 3.880 / 4.220 / 4.447 | 6.265 / 6.710 / 6.914 | 6.395 / 6.853 / 7.095 |

The previous same-message microbenchmark measures \(h\) signatures on one fixed message and verifies one effective pair using a prepared aggregate public key. Signature aggregation runs during its setup. Across the six signer counts its verification means were 0.608–0.613 ms, with P95 at 0.678–0.709 ms and P99 at 0.804–0.860 ms. The timings characterize different workloads, so the same-message cost applies only when signers share a message and the aggregate public key is available. [Same-message methods and results](bls_same_message_latency.md) · [Original same-message summary](../results/processed/bls_same_message_latency/summary.csv)

## Correctness and reproduction

Three TRAIL benchmark tests, three same-message benchmark tests, and the 20 existing path tests passed in Release mode. The TRAIL tests confirmed \(2h\) pairs and byte-equivalent signatures at every hop count. The production uncached verifier rejected 30 cases that replaced one sender or receiver signature on the first, middle, or last hop with a valid signature over a mutated Statement. It also rejected five index reorders. The two-hop swap affects the origin, and longer fixtures swap two intermediate identities while preserving both endpoints. The one-hop certificate stores a single index.

```bash
out=$(mktemp -d /tmp/trail-bls-hops.XXXXXX)
cargo test --offline --release --features bls-hop-latency --bin bls_hop_latency --bin bls_same_message_latency
cargo test --offline --release --features bls-hop-latency --lib blockchain::path::tests
cargo build --offline --release --features bls-hop-latency --bin bls_hop_latency
taskset -c 2 target/release/bls_hop_latency --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_bls_hop_latency.py \
  --samples "$out/data/samples.csv" --output "$out/bls_hop_latency.pdf"
```
