use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

struct Scratch(std::path::PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn read_csv(directory: &Path, file: &str) -> Vec<BTreeMap<String, String>> {
    let contents = fs::read_to_string(directory.join(file)).unwrap();
    let mut lines = contents.lines();
    let header: Vec<_> = lines.next().unwrap().split(',').collect();
    // Numeric columns precede the three quoted descriptions, which can contain commas.
    let numeric_and_label_columns = header.len() - 3;
    lines
        .map(|line| {
            header
                .iter()
                .zip(line.splitn(numeric_and_label_columns + 1, ','))
                .take(numeric_and_label_columns)
                .map(|(&key, value)| (key.to_owned(), value.to_owned()))
                .collect()
        })
        .collect()
}

#[test]
fn csvs_reconcile_all_synthetic_blocks_and_keep_unknowns_empty() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let scratch =
        Scratch(std::env::temp_dir().join(format!("trail-onchain-{}-{stamp}", std::process::id())));
    let binary = env!("CARGO_BIN_EXE_onchain_path_storage");
    let args = ["--output-dir", scratch.0.to_str().unwrap()];
    assert!(Command::new(binary).args(args).status().unwrap().success());

    let native = read_csv(&scratch.0, "protocol_native.csv");
    let full = read_csv(&scratch.0, "full_evidence_retention.csv");
    let sensitivity = read_csv(&scratch.0, "nkn_state_sensitivity.csv");
    let conditions = read_csv(&scratch.0, "nkn_disclosure_conditions.csv");
    let layouts = read_csv(&scratch.0, "ersoy_encoding_sensitivity.csv");
    for rows in [&native, &full, &sensitivity] {
        assert_eq!(rows.len(), 45);
    }
    assert_eq!(conditions.len(), 18);
    assert_eq!(layouts.len(), 18);

    for (hops, trail_bytes, txtail_bytes) in [(2, 64, 112), (4, 80, 128), (6, 96, 144)] {
        for z in [100, 1000, 4000] {
            let rows_for = |rows: &[BTreeMap<String, String>]| {
                rows.iter()
                    .filter(|row| {
                        row["hops"] == hops.to_string()
                            && row["transactions_per_block"] == z.to_string()
                    })
                    .cloned()
                    .collect::<Vec<_>>()
            };
            let native_rows = rows_for(&native);
            let full_rows = rows_for(&full);
            let state_rows = rows_for(&sensitivity);
            for rows in [&native_rows, &full_rows] {
                assert_eq!(rows.len(), 5);
                for scheme in ["TRAIL", "Ersoy", "Plutus", "NKN", "TxTail"] {
                    assert_eq!(rows.iter().filter(|row| row["scheme"] == scheme).count(), 1);
                }
            }
            for row in native_rows.iter().chain(&full_rows) {
                let number = |key: &str| row[key].parse::<usize>().unwrap();
                let sum = || {
                    [
                        "path_identity_bytes",
                        "path_signature_bytes",
                        "path_proof_bytes",
                        "path_reward_share_bytes",
                    ]
                    .map(number)
                    .iter()
                    .sum::<usize>()
                };
                if row["scenario"] == "full_evidence_retention" {
                    let bytes_per_path = match row["scheme"].as_str() {
                        "TRAIL" => trail_bytes,
                        "Ersoy" => 33 * (hops - 1) + 64,
                        "Plutus" => 96 * hops,
                        "NKN" => 256 * hops,
                        "TxTail" => txtail_bytes,
                        _ => unreachable!(),
                    };
                    assert_eq!(number("evidence_bytes_per_path"), bytes_per_path);
                    assert_eq!(number("onchain_evidence_count"), z);
                    assert_eq!(row["measurement_type"], "assumption_based_estimate");
                } else {
                    match row["scheme"].as_str() {
                        "TRAIL" => {
                            assert_eq!(number("evidence_bytes_per_path"), trail_bytes);
                            assert_eq!(number("onchain_evidence_count"), z);
                            assert_eq!(number("path_identity_bytes"), 8 * hops);
                            assert_eq!(number("path_signature_bytes"), 48);
                            if z == 1000 && hops == 4 {
                                assert_eq!(number("total_path_storage_bytes"), 80_000);
                            }
                        }
                        "TxTail" => {
                            assert_eq!(number("evidence_bytes_per_path"), txtail_bytes);
                            assert_eq!(number("onchain_evidence_count"), 1);
                            assert_eq!(number("path_identity_bytes"), 7 * hops);
                            assert_eq!(number("path_signature_bytes"), 96);
                            assert_eq!(number("path_reward_share_bytes"), hops);
                            assert_eq!(number("path_proof_bytes"), 0);
                        }
                        "Ersoy" => {
                            assert_eq!(number("onchain_evidence_count"), z);
                            assert_eq!(row["measurement_type"], "assumption_based_estimate");
                            assert_eq!(number("path_identity_bytes"), 33 * (hops - 1));
                            assert_eq!(number("path_signature_bytes"), 64);
                            assert_eq!(number("path_identifier_bytes"), 0);
                            assert_eq!(
                                number("total_path_storage_bytes"),
                                z * (33 * (hops - 1) + 64)
                            );
                        }
                        "Plutus" => {
                            assert!(row["onchain_evidence_count"].is_empty());
                            assert!(row["path_proof_bytes"].is_empty());
                        }
                        "NKN" => {
                            assert!(row["onchain_evidence_count"].is_empty());
                            assert_eq!(number("onchain_evidence_count_min"), 0);
                            assert_eq!(number("onchain_evidence_count_max"), 1);
                            assert_eq!(number("path_proof_bytes"), 128 * (hops - 1));
                            assert_eq!(sum(), 256 * hops);
                        }
                        _ => unreachable!(),
                    }
                }
                if row["measurement_type"] == "not_specified" {
                    assert!(row["total_path_storage_bytes"].is_empty());
                    assert!(row["overhead_pct_540B"].is_empty());
                    assert!(row["overhead_pct_300B"].is_empty());
                } else {
                    let total = number("total_path_storage_bytes");
                    assert_eq!(total, sum() * number("onchain_evidence_count"));
                    assert_percentages(row, total as f64, z);
                }
            }
            let condition_rows = rows_for(&conditions);
            assert_eq!(condition_rows.len(), 2);
            for (row, count) in condition_rows.iter().zip([0, 1]) {
                let number = |key: &str| row[key].parse::<usize>().unwrap();
                assert_eq!(row["scenario"], "nkn_disclosure_condition");
                assert_eq!(row["scheme"], "NKN");
                assert_eq!(row["measurement_type"], "protocol_encoding_model");
                assert_eq!(number("onchain_evidence_count"), count);
                assert_eq!(number("path_identity_bytes"), 64 * (hops + 1));
                assert_eq!(number("path_signature_bytes"), 64 * (hops + 1));
                assert_eq!(number("path_proof_bytes"), 128 * (hops - 1));
                assert!(row["expected_path_storage_bytes"].is_empty());
                let total = 256 * hops * count;
                assert_eq!(number("total_path_storage_bytes"), total);
                assert_percentages(row, total as f64, z);
            }
            let layout_rows = rows_for(&layouts);
            assert_eq!(layout_rows.len(), 2);
            for (row, identifier_bytes) in layout_rows.iter().zip([0, 32]) {
                let number = |key: &str| row[key].parse::<usize>().unwrap();
                assert_eq!(row["scenario"], "ersoy_encoding_sensitivity");
                assert_eq!(row["scheme"], "Ersoy");
                assert_eq!(row["measurement_type"], "assumption_based_estimate");
                assert_eq!(number("onchain_evidence_count"), z);
                assert_eq!(number("path_identifier_bytes"), identifier_bytes);
                assert_eq!(number("path_proof_bytes"), identifier_bytes);
                let per_path = 33 * (hops - 1) + 64 + identifier_bytes;
                assert_eq!(number("evidence_bytes_per_path"), per_path);
                assert_eq!(number("total_path_storage_bytes"), z * per_path);
                assert_percentages(row, (z * per_path) as f64, z);
            }
            assert_eq!(state_rows.len(), 5);
            for (row, p) in state_rows.iter().zip([0.0, 0.25, 0.5, 0.75, 1.0]) {
                assert_eq!(row["scheme"], "NKN");
                assert!(row["total_path_storage_bytes"].is_empty());
                assert_eq!(row["nkn_new_winner_probability"].parse::<f64>().unwrap(), p);
                assert_eq!(row["expected_evidence_count"].parse::<f64>().unwrap(), p);
                let expected = 256.0 * hops as f64 * p;
                assert_eq!(
                    row["expected_path_storage_bytes"].parse::<f64>().unwrap(),
                    expected
                );
                assert_percentages(row, expected, z);
            }
        }
    }

    let before = fs::read(scratch.0.join("protocol_native.csv")).unwrap();
    assert!(!Command::new(binary)
        .args(args)
        .output()
        .unwrap()
        .status
        .success());
    assert_eq!(
        fs::read(scratch.0.join("protocol_native.csv")).unwrap(),
        before
    );
}

fn assert_percentages(row: &BTreeMap<String, String>, total: f64, z: usize) {
    for (column, expected) in [
        ("overhead_pct_540B", 100.0 * total / (540.0 * z as f64)),
        ("overhead_pct_300B", 100.0 * total / (300.0 * z as f64)),
        ("storage_kib", total / 1024.0),
        ("storage_bytes_per_transaction", total / z as f64),
    ] {
        assert!(
            (row[column].parse::<f64>().unwrap() - expected).abs() <= 5.1e-9,
            "{column}"
        );
    }
}
