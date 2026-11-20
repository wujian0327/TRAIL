//! Time a single BLS12-381 signature and a single public-key verification.
use blst::{
    min_sig::{PublicKey, SecretKey, Signature},
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

const DST: &str = "BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_NUL_";
const MESSAGE: [u8; 32] = [0x42; 32];

#[derive(Parser)]
#[command(about = "Measure a standalone BLST sign and verify with one prepared key and message")]
struct Args {
    #[arg(long, default_value = "results/processed/bls_primitive_latency")]
    output_dir: PathBuf,
}

fn verify(signature: &Signature, public_key: &PublicKey, message: &[u8]) -> BLST_ERROR {
    signature.verify(true, message, DST.as_bytes(), &[], public_key, true)
}

fn sample<T>(mut run: impl FnMut() -> T, check: impl Fn(T)) -> Vec<u128> {
    let mut samples = Vec::with_capacity(SAMPLES);
    for iteration in 0..WARMUPS + SAMPLES {
        let (result, elapsed) = time(&mut run);
        check(result);
        if iteration >= WARMUPS {
            samples.push(elapsed);
        }
    }
    samples
}

fn write_csvs(dir: &Path, operations: [(&str, Vec<u128>); 2]) -> io::Result<()> {
    let mut raw = BufWriter::new(File::create_new(dir.join("samples.csv"))?);
    let mut summary = BufWriter::new(File::create_new(dir.join("summary.csv"))?);
    writeln!(raw, "operation,sample,elapsed_ns")?;
    writeln!(summary, "operation,warmups,samples,mean_us,p95_us,p99_us")?;
    for (operation, mut times) in operations {
        for (i, ns) in times.iter().enumerate() {
            writeln!(raw, "{operation},{},{ns}", i + 1)?;
        }
        let (mean, p95, p99) = statistics_us(&mut times);
        writeln!(
            summary,
            "{operation},{WARMUPS},{SAMPLES},{mean:.6},{p95:.6},{p99:.6}"
        )?;
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
    environment["ciphersuite"] = json!(DST);
    environment["message_bytes"] = json!(MESSAGE.len());
    environment["signature_group_check"] = json!(true);
    environment["public_key_validation"] = json!(true);

    let secret_key = SecretKey::key_gen(&[0x6d; 32], &[])
        .map_err(|error| format!("fixture key generation failed: {error:?}"))?;
    let public_key = secret_key.sk_to_pk();
    let signature = secret_key.sign(&MESSAGE, DST.as_bytes(), &[]);
    assert_eq!(
        verify(&signature, &public_key, &MESSAGE),
        BLST_ERROR::BLST_SUCCESS
    );
    let signature_bytes = signature.to_bytes();
    let signing = sample(
        || black_box(&secret_key).sign(black_box(&MESSAGE), DST.as_bytes(), &[]),
        |result| assert_eq!(result.to_bytes(), signature_bytes),
    );
    let verification = sample(
        || {
            verify(
                black_box(&signature),
                black_box(&public_key),
                black_box(&MESSAGE),
            )
        },
        |result| assert_eq!(result, BLST_ERROR::BLST_SUCCESS),
    );

    fs::create_dir_all(&args.output_dir)?;
    write_csvs(
        &args.output_dir,
        [("signing", signing), ("verification", verification)],
    )?;
    let mut metadata = BufWriter::new(File::create_new(args.output_dir.join("environment.json"))?);
    serde_json::to_writer_pretty(&mut metadata, &environment)?;
    writeln!(metadata)?;
    metadata.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use support::percentile;

    #[test]
    fn signature_verifies_with_the_matching_message_and_key() {
        let secret = SecretKey::key_gen(&[0x6d; 32], &[]).unwrap();
        let key = secret.sk_to_pk();
        let other_key = SecretKey::key_gen(&[0x2d; 32], &[]).unwrap().sk_to_pk();
        let signature = secret.sign(&MESSAGE, DST.as_bytes(), &[]);
        assert_eq!(verify(&signature, &key, &MESSAGE), BLST_ERROR::BLST_SUCCESS);
        let mut changed = MESSAGE;
        changed[0] ^= 1;
        assert_ne!(verify(&signature, &key, &changed), BLST_ERROR::BLST_SUCCESS);
        assert_ne!(
            verify(&signature, &other_key, &MESSAGE),
            BLST_ERROR::BLST_SUCCESS
        );
    }

    #[test]
    fn percentiles_use_measured_order_statistics() {
        let values: Vec<_> = (1..=1000).map(|i| i * i).collect();
        assert_eq!(percentile(&values, 95), 950 * 950);
        assert_eq!(percentile(&values, 99), 990 * 990);
    }
}
