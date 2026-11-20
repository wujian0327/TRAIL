//! Measure the current TRAIL bidirectional hop certificate for one transaction.
use blst::min_sig::{AggregateSignature, Signature};
use clap::Parser;
use serde_json::json;
use std::fs::{self, File};
use std::hint::black_box;
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;
use trail::blockchain::path::latency_bench::PreparedCertificate;
use trail::blockchain::path::{AggregatedSignedPaths, TransactionPaths, DEFAULT_TRAIL_CHAIN_ID};
use trail::blockchain::transaction::Transaction;
use trail::consensus::ValidatorRegistry;
use trail::wallet::Wallet;

#[path = "bls_latency/support.rs"]
mod support;
use support::{statistics_us, time, SAMPLES, WARMUPS};

const HOPS: [usize; 6] = [1, 2, 4, 6, 8, 16];
const EPOCH: u64 = 7;

#[derive(Parser)]
#[command(about = "Measure signing and uncached verification of one TRAIL path certificate")]
struct Args {
    #[arg(long, default_value = "results/processed/bls_hop_latency")]
    output_dir: PathBuf,
}

struct Fixture {
    wallets: Vec<Wallet>,
    route: TransactionPaths,
    registry: ValidatorRegistry,
    certificate: AggregatedSignedPaths,
}

impl Fixture {
    fn new(hops: usize) -> Self {
        let wallets: Vec<_> = (0..=hops)
            .map(|i| Wallet::new_deterministic(0x424c_5350_4154_4800 + hops as u64, i as u32))
            .collect();
        let registry = ValidatorRegistry::from_wallets(&wallets).expect("distinct validators");
        let transaction = Transaction::with_timestamp(
            wallets[hops].address.clone(),
            hops as i64,
            1.0,
            1.0,
            wallets[0].clone(),
            false,
            1_700_000_000,
        );
        let mut route = TransactionPaths::new_with_epoch(transaction, EPOCH);
        for edge in wallets.windows(2) {
            assert!(route.append_completed_hop(
                edge[1].address.clone(),
                edge[0].clone(),
                edge[1].clone(),
            ));
        }
        let certificate = route
            .to_aggregated_signed_paths(&registry)
            .expect("registered path");
        assert_eq!(certificate.paths.len(), hops);
        Self {
            wallets,
            route,
            registry,
            certificate,
        }
    }

    fn proposer(&self) -> &str {
        &self.wallets.last().expect("path proposer").address
    }

    fn prepare(&self) -> PreparedCertificate<'_> {
        PreparedCertificate::new(
            &self.certificate,
            &self.route.transaction,
            self.proposer(),
            EPOCH,
            &self.registry,
        )
        .expect("production verifier accepts the fixture")
    }

    fn reference_signatures(&self) -> Vec<Signature> {
        self.route
            .paths
            .iter()
            .flat_map(|hop| {
                [
                    &hop.sender_signature,
                    hop.receiver_signature.as_ref().expect("completed hop"),
                ]
                .map(|signature| {
                    Wallet::bls_signature_from_string(signature.clone())
                        .expect("valid hop signature")
                })
            })
            .collect()
    }
}

fn sign_hops(fixture: &Fixture, messages: &[Vec<u8>], output: &mut Vec<Signature>) {
    for (edge, statements) in fixture.wallets.windows(2).zip(messages.chunks_exact(2)) {
        for (wallet, statement) in edge.iter().zip(statements) {
            // Same BLS signing call as Wallet::sign_by_bls, with typed output.
            output.push(wallet.bls_private_key.sign(statement, &[], &[]));
        }
    }
}

fn aggregate(individual: &[Signature]) -> Signature {
    // Wallet::bls_aggregated_sign initializes from the first point and checks each added point.
    let mut aggregate = AggregateSignature::from_signature(&individual[0]);
    for signature in &individual[1..] {
        aggregate
            .add_signature(signature, true)
            .expect("valid hop signature");
    }
    aggregate.to_signature()
}

struct Sample {
    signing_ns: u128,
    aggregation_ns: u128,
    bls_verification_ns: u128,
    certificate_verification_ns: u128,
}

fn measure(fixture: &Fixture) -> Vec<Sample> {
    let prepared = fixture.prepare();
    let reference_signatures = fixture.reference_signatures();
    let mut signatures = Vec::with_capacity(reference_signatures.len());
    let mut samples = Vec::with_capacity(SAMPLES);

    for i in 0..WARMUPS + SAMPLES {
        signatures.clear();
        let signing_start = Instant::now();
        sign_hops(
            black_box(fixture),
            black_box(&prepared.messages),
            &mut signatures,
        );
        let aggregation_start = Instant::now();
        let combined = black_box(aggregate(black_box(&signatures)));
        let finish = Instant::now();
        assert_eq!(signatures, reference_signatures);
        assert_eq!(combined, prepared.signature);

        // The production wallet wrapper borrows from owned vectors internally.
        // Duplicate the already prepared inputs before starting the timer.
        let messages = prepared.messages.clone();
        let keys = prepared.public_keys.clone();
        let verify_bls = || {
            time(|| {
                Wallet::bls_aggregated_verify_parsed(
                    black_box(messages),
                    black_box(keys),
                    black_box(prepared.signature),
                )
            })
        };
        let verify_certificate = || time(|| black_box(&prepared).verify_evidence_uncached());
        let ((bls_valid, bls_verification_ns), (certificate_valid, certificate_verification_ns)) =
            if i % 2 == 0 {
                let bls = verify_bls();
                (bls, verify_certificate())
            } else {
                let certificate = verify_certificate();
                (verify_bls(), certificate)
            };
        assert!(
            bls_valid && certificate_valid,
            "production verifier rejected a valid certificate"
        );
        if i >= WARMUPS {
            samples.push(Sample {
                signing_ns: (aggregation_start - signing_start).as_nanos(),
                aggregation_ns: (finish - aggregation_start).as_nanos(),
                bls_verification_ns,
                certificate_verification_ns,
            });
        }
    }
    samples
}

type Timing = (&'static str, fn(&Sample) -> u128);
const TIMINGS: [Timing; 5] = [
    ("signing_total", |s| s.signing_ns + s.aggregation_ns),
    ("bls_signing", |s| s.signing_ns),
    ("bls_aggregation", |s| s.aggregation_ns),
    ("bls_verification", |s| s.bls_verification_ns),
    ("certificate_verification", |s| {
        s.certificate_verification_ns
    }),
];

fn write_results(dir: &Path, results: Vec<Vec<Sample>>) -> io::Result<()> {
    let mut raw = BufWriter::new(File::create_new(dir.join("samples.csv"))?);
    let mut summary = BufWriter::new(File::create_new(dir.join("summary.csv"))?);
    let workload = "hops,operation,hop_signatures,distinct_hop_messages,signer_message_pairs";
    writeln!(raw, "{workload},sample,elapsed_ns")?;
    writeln!(summary, "{workload},warmups,samples,mean_us,p95_us,p99_us")?;
    for (hops, samples) in HOPS.into_iter().zip(results) {
        let signatures = hops * 2;
        for (operation, field) in TIMINGS {
            let mut times: Vec<_> = samples.iter().map(field).collect();
            for (i, ns) in times.iter().enumerate() {
                writeln!(
                    raw,
                    "{hops},{operation},{signatures},{hops},{signatures},{},{ns}",
                    i + 1
                )?;
            }
            let (mean, p95, p99) = statistics_us(&mut times);
            writeln!(summary,
                "{hops},{operation},{signatures},{hops},{signatures},{WARMUPS},{SAMPLES},{mean:.6},{p95:.6},{p99:.6}")?;
        }
    }
    raw.flush()?;
    summary.flush()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    if cfg!(debug_assertions) {
        return Err("run this benchmark in release mode".into());
    }
    for name in ["samples.csv", "summary.csv", "environment.json"] {
        if args.output_dir.join(name).try_exists()? {
            return Err(format!(
                "output already exists: {}",
                args.output_dir.join(name).display()
            )
            .into());
        }
    }
    let mut environment = support::environment()?;
    environment["workload"] = json!("TRAIL bidirectional multi-message hop certificate");
    environment["hops"] = json!(HOPS);
    environment["chain_id"] = json!(DEFAULT_TRAIL_CHAIN_ID);
    environment["epoch"] = json!(EPOCH);
    environment["verification_api"] =
        json!("Wallet::bls_aggregated_verify_parsed / BLST aggregate_verify");
    environment["verification_cache"] = json!(false);
    environment["ecdsa_in_timers"] = json!(false);

    let mut results = Vec::with_capacity(HOPS.len());
    for hops in HOPS {
        results.push(measure(&Fixture::new(hops)));
        eprintln!("Completed {hops} hops ({SAMPLES} samples per operation)");
    }
    fs::create_dir_all(&args.output_dir)?;
    write_results(&args.output_dir, results)?;
    let mut metadata = BufWriter::new(File::create_new(args.output_dir.join("environment.json"))?);
    serde_json::to_writer_pretty(&mut metadata, &environment)?;
    writeln!(metadata)?;
    metadata.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    use trail::blockchain::path::TRAIL_TX_PATH_DOMAIN;

    #[test]
    fn prepared_pairs_and_signatures_match_production() {
        for hops in HOPS {
            let fixture = Fixture::new(hops);
            let prepared = fixture.prepare();
            let mut signatures = Vec::new();
            sign_hops(&fixture, &prepared.messages, &mut signatures);
            assert_eq!(signatures.len(), 2 * hops);
            assert_eq!(signatures, fixture.reference_signatures());
            assert_eq!(aggregate(&signatures), prepared.signature);
            assert_eq!(prepared.signature.to_bytes().len(), 48);
            assert_eq!(prepared.public_keys.len(), 2 * hops);
            assert_eq!(prepared.messages.len(), 2 * hops);
            let distinct_messages: HashSet<_> = prepared.messages.iter().collect();
            assert_eq!(distinct_messages.len(), hops);
            for (idx, messages) in prepared.messages.chunks_exact(2).enumerate() {
                assert_eq!(messages[0], messages[1]);
                assert_eq!(
                    prepared.public_keys[2 * idx],
                    fixture.wallets[idx].bls_public_key
                );
                assert_eq!(
                    prepared.public_keys[2 * idx + 1],
                    fixture.wallets[idx + 1].bls_public_key
                );
            }
            assert!(prepared.verify_evidence_uncached());
        }
    }

    #[test]
    fn production_rejects_wrong_messages_at_first_middle_and_last_hops() {
        for hops in HOPS {
            let fixture = Fixture::new(hops);
            let prepared = fixture.prepare();
            let mut positions = [0, hops / 2, hops - 1].to_vec();
            positions.dedup();
            for hop in positions {
                for side in 0..2 {
                    let mut signatures = fixture.reference_signatures();
                    let mut changed = prepared.messages[2 * hop + side].clone();
                    changed[TRAIL_TX_PATH_DOMAIN.len()] ^= 1;
                    signatures[2 * hop + side] =
                        fixture.wallets[hop + side]
                            .bls_private_key
                            .sign(&changed, &[], &[]);
                    let mut corrupted = fixture.certificate.clone();
                    corrupted.signature = Wallet::bls_aggregated_sign(signatures);
                    assert!(
                        !corrupted.verify_at_epoch_uncached(
                            &fixture.route.transaction,
                            fixture.proposer(),
                            EPOCH,
                            &fixture.registry,
                        ),
                        "{hops} hops, modified hop {} side {side}",
                        hop + 1
                    );
                }
            }
        }
    }

    #[test]
    fn production_rejects_reordered_certificate_indices() {
        for hops in HOPS.into_iter().filter(|&hops| hops > 1) {
            let fixture = Fixture::new(hops);
            let mut reordered = fixture.certificate.clone();
            // With two stored indices the only swap also changes the origin.
            // Longer paths preserve the origin and the header proposer.
            if hops == 2 {
                reordered.paths.swap(0, 1);
            } else {
                reordered.paths.swap(1, 2);
            }
            assert!(
                !reordered.verify_at_epoch_uncached(
                    &fixture.route.transaction,
                    fixture.proposer(),
                    EPOCH,
                    &fixture.registry,
                ),
                "reordered {hops}-hop path"
            );
        }
    }
}
