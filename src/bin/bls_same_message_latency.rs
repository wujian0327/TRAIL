//! Time h BLS signatures and one same-message aggregate verification.
use blst::{
    min_sig::{AggregatePublicKey, AggregateSignature, PublicKey, SecretKey, Signature},
    BLST_ERROR,
};
use clap::Parser;
use serde_json::json;
use std::fs::{self, File};
use std::hint::black_box;
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};

#[path = "bls_latency/support.rs"]
mod support;
use support::{statistics_us, time, SAMPLES, WARMUPS};

const HOPS: [usize; 6] = [1, 2, 4, 6, 8, 16];
const SIGNATURE_DST: &[u8] = b"BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_POP_";
const PROOF_DST: &[u8] = b"BLS_POP_BLS12381G1_XMD:SHA-256_SSWU_RO_POP_";
const MESSAGE: [u8; 32] = [0x42; 32];

#[derive(Parser)]
#[command(about = "Measure same-message BLS fast aggregate verification and sequential signing")]
struct Args {
    #[arg(long, default_value = "results/processed/bls_same_message_latency")]
    output_dir: PathBuf,
}

fn keys(count: usize) -> (Vec<SecretKey>, Vec<PublicKey>) {
    (0..count)
        .map(|i| {
            let mut ikm = [0x6d; 32];
            ikm[..8].copy_from_slice(&(i as u64).to_le_bytes());
            let secret = SecretKey::key_gen(&ikm, &[]).expect("fixture key generation");
            let public = secret.sk_to_pk();
            let message = public.to_bytes();
            let proof = secret.sign(&message, PROOF_DST, &[]);
            assert_eq!(
                proof.verify(true, &message, PROOF_DST, &[], &public, true),
                BLST_ERROR::BLST_SUCCESS
            );
            (secret, public)
        })
        .unzip()
}

struct Case {
    expected_signatures: Vec<Signature>,
    signing_output: Vec<Signature>,
    aggregate_signature: Signature,
    aggregate_key: PublicKey,
    signing_ns: Vec<u128>,
    verification_ns: Vec<u128>,
}

impl Case {
    fn new(secrets: &[SecretKey], public_keys: &[PublicKey]) -> Self {
        assert_eq!(secrets.len(), public_keys.len());
        let expected_signatures: Vec<_> = secrets
            .iter()
            .map(|key| key.sign(&MESSAGE, SIGNATURE_DST, &[]))
            .collect();
        let signatures: Vec<_> = expected_signatures.iter().collect();
        let public_keys: Vec<_> = public_keys.iter().collect();
        let aggregate_signature = AggregateSignature::aggregate(&signatures, true)
            .expect("valid fixture signatures")
            .to_signature();
        let aggregate_key = AggregatePublicKey::aggregate(&public_keys, false)
            .expect("proofs of possession passed")
            .to_public_key();
        aggregate_key.validate().expect("valid aggregate key");
        assert_eq!(
            aggregate_signature.fast_aggregate_verify_pre_aggregated(
                true,
                &MESSAGE,
                SIGNATURE_DST,
                &aggregate_key,
            ),
            BLST_ERROR::BLST_SUCCESS
        );
        Self {
            signing_output: Vec::with_capacity(secrets.len()),
            expected_signatures,
            aggregate_signature,
            aggregate_key,
            signing_ns: Vec::with_capacity(SAMPLES),
            verification_ns: Vec::with_capacity(SAMPLES),
        }
    }

    fn sample(&mut self, secrets: &[SecretKey], record: bool) {
        let secrets = &secrets[..self.expected_signatures.len()];
        self.signing_output.clear();
        let ((), signing_ns) = time(|| {
            for secret in secrets {
                self.signing_output
                    .push(secret.sign(black_box(&MESSAGE), SIGNATURE_DST, &[]));
            }
        });
        assert_eq!(self.signing_output, self.expected_signatures);

        let (result, verification_ns) = time(|| {
            black_box(&self.aggregate_signature).fast_aggregate_verify_pre_aggregated(
                true,
                black_box(&MESSAGE),
                SIGNATURE_DST,
                black_box(&self.aggregate_key),
            )
        });
        assert_eq!(result, BLST_ERROR::BLST_SUCCESS);
        if record {
            self.signing_ns.push(signing_ns);
            self.verification_ns.push(verification_ns);
        }
    }
}

fn write_csvs(dir: &Path, cases: Vec<Case>) -> io::Result<()> {
    let mut raw = BufWriter::new(File::create_new(dir.join("samples.csv"))?);
    let mut summary = BufWriter::new(File::create_new(dir.join("summary.csv"))?);
    writeln!(raw, "hops,operation,sample,elapsed_ns")?;
    writeln!(
        summary,
        "hops,operation,warmups,samples,mean_us,p95_us,p99_us"
    )?;
    for (hops, case) in HOPS.into_iter().zip(cases) {
        for (operation, mut times) in [
            ("signing", case.signing_ns),
            ("aggregate_verification", case.verification_ns),
        ] {
            for (i, ns) in times.iter().enumerate() {
                writeln!(raw, "{hops},{operation},{},{ns}", i + 1)?;
            }
            let (mean, p95, p99) = statistics_us(&mut times);
            writeln!(
                summary,
                "{hops},{operation},{WARMUPS},{SAMPLES},{mean:.6},{p95:.6},{p99:.6}"
            )?;
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
    for filename in ["samples.csv", "summary.csv", "environment.json"] {
        let path = args.output_dir.join(filename);
        if path.try_exists()? {
            return Err(format!("output already exists: {}", path.display()).into());
        }
    }

    let mut environment = support::environment()?;
    environment["hops"] = json!(HOPS);
    environment["signature_ciphersuite"] = json!(std::str::from_utf8(SIGNATURE_DST)?);
    environment["proof_domain"] = json!(std::str::from_utf8(PROOF_DST)?);
    environment["message_bytes"] = json!(MESSAGE.len());
    environment["verification_api"] = json!("fast_aggregate_verify_pre_aggregated");
    environment["signature_group_check"] = json!(true);
    environment["hops_sampling_order"] = json!("rotated each round");

    let (secrets, public_keys) = keys(HOPS[HOPS.len() - 1]);
    let mut cases: Vec<_> = HOPS
        .into_iter()
        .map(|hops| Case::new(&secrets[..hops], &public_keys[..hops]))
        .collect();
    for round in 0..WARMUPS + SAMPLES {
        for offset in 0..HOPS.len() {
            cases[(round + offset) % HOPS.len()].sample(&secrets, round >= WARMUPS);
        }
    }

    fs::create_dir_all(&args.output_dir)?;
    write_csvs(&args.output_dir, cases)?;
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

    #[test]
    fn signs_once_per_hop_and_verifies_one_aggregate_for_all_configurations() {
        let (secrets, public_keys) = keys(16);
        let distinct_keys: HashSet<_> = public_keys.iter().map(PublicKey::to_bytes).collect();
        assert_eq!(distinct_keys.len(), public_keys.len());
        for hops in HOPS {
            let mut case = Case::new(&secrets[..hops], &public_keys[..hops]);
            case.sample(&secrets, true);
            assert_eq!(case.signing_output.len(), hops);
            for (i, signature) in case.signing_output.iter().enumerate() {
                assert_eq!(
                    signature.verify(true, &MESSAGE, SIGNATURE_DST, &[], &public_keys[i], true),
                    BLST_ERROR::BLST_SUCCESS
                );
            }
            let messages = vec![MESSAGE.as_slice(); hops];
            let individual_keys: Vec<_> = public_keys[..hops].iter().collect();
            assert_eq!(
                case.aggregate_signature.aggregate_verify(
                    true,
                    &messages,
                    SIGNATURE_DST,
                    &individual_keys,
                    true,
                ),
                BLST_ERROR::BLST_SUCCESS,
                "explicit signer-message verification for {hops} hops"
            );
            assert_eq!(case.signing_ns.len(), 1);
            assert_eq!(case.verification_ns.len(), 1);
        }
    }

    #[test]
    fn aggregate_rejects_a_different_message_at_each_tested_hop() {
        let (secrets, public_keys) = keys(16);
        let mut changed = MESSAGE;
        changed[0] ^= 1;
        for hops in HOPS {
            let case = Case::new(&secrets[..hops], &public_keys[..hops]);
            // For one or two hops, the middle position coincides with an endpoint.
            let mut positions = vec![0, hops / 2, hops - 1];
            positions.dedup();
            for position in positions {
                let mut signatures = case.expected_signatures.clone();
                signatures[position] = secrets[position].sign(&changed, SIGNATURE_DST, &[]);
                let refs: Vec<_> = signatures.iter().collect();
                let changed_aggregate = AggregateSignature::aggregate(&refs, true)
                    .expect("all signatures have valid group elements")
                    .to_signature();
                assert_eq!(
                    changed_aggregate.fast_aggregate_verify_pre_aggregated(
                        true,
                        &MESSAGE,
                        SIGNATURE_DST,
                        &case.aggregate_key,
                    ),
                    BLST_ERROR::BLST_VERIFY_FAIL,
                    "{hops} hops with only hop {} signing a different message",
                    position + 1
                );
            }
        }
    }

    #[test]
    fn aggregate_rejects_a_changed_message_and_a_missing_signer() {
        let (secrets, public_keys) = keys(4);
        let case = Case::new(&secrets, &public_keys);
        let incomplete = Case::new(&secrets[..3], &public_keys[..3]);
        let mut changed = MESSAGE;
        changed[0] ^= 1;
        for (message, key) in [
            (&changed, &case.aggregate_key),
            (&MESSAGE, &incomplete.aggregate_key),
        ] {
            assert_ne!(
                case.aggregate_signature
                    .fast_aggregate_verify_pre_aggregated(true, message, SIGNATURE_DST, key),
                BLST_ERROR::BLST_SUCCESS
            );
        }
    }
}
