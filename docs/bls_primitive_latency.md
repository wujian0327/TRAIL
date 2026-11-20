# Standalone BLS signing and verification latency

[Single-panel PDF](../figures/trail_overhead/bls_primitive_latency.pdf) · [Raw samples](../results/processed/bls_primitive_latency/samples.csv) · [Summary](../results/processed/bls_primitive_latency/summary.csv) · [Run environment](../results/processed/bls_primitive_latency/environment.json)

The benchmark uses the BLS12-381 Basic minimal-signature-size ciphersuite in BLST 0.3.13. A fixed 32-byte message and one pre-generated key pair supply the inputs. Each signing sample calls `SecretKey::sign` once. Each verification sample calls `Signature::verify` once on the prepared valid signature and public key, with signature subgroup checking and public-key validation enabled. Setup and result assertions are outside the timed intervals. [CFRG BLS draft, §4.2.1](https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-bls-signature-07#section-4.2.1) · [Rust benchmark](../src/bin/bls_primitive_latency.rs)

The run used Rust 1.94.0 in Release mode on an AMD Ryzen 9 9955HX with 16 physical cores and 32 logical CPUs. The process was pinned to logical CPU 2. Each operation had 50 warmups and 1,000 recorded samples. P95 and P99 use nearest rank on the actual nanosecond samples. The figure shows the mean as a bar and the two percentiles as caps.

| Operation | Mean | P95 | P99 |
| --- | ---: | ---: | ---: |
| BLS signing | 0.094 ms | 0.118 ms | 0.161 ms |
| BLS verification | 0.632 ms | 0.673 ms | 0.712 ms |

In the standalone BLS12-381 microbenchmark, signing one 32-byte message took 0.094 ms on average. Verification with a prepared public key and signature took 0.632 ms on average, with a P95 of 0.673 ms and a P99 of 0.712 ms. These measurements characterize one cryptographic operation per sample on the stated single-CPU configuration.

```bash
out=$(mktemp -d /tmp/bls-primitive.XXXXXX)
cargo test --offline --bin bls_primitive_latency
cargo build --offline --release --bin bls_primitive_latency
taskset -c 2 target/release/bls_primitive_latency --output-dir "$out/data"
MPLCONFIGDIR="$out/cache" python3 experiments/plot_bls_primitive_latency.py \
  --summary "$out/data/summary.csv" --output "$out/bls_primitive_latency.pdf"
```
