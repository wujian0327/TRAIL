use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const COMPARISON: &str = "evidence_size_comparison.csv";
const BREAKDOWN: &str = "evidence_size_breakdown.csv";
const HOPS: [usize; 6] = [1, 2, 4, 6, 8, 16];

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        static SEQUENCE: AtomicUsize = AtomicUsize::new(0);
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let sequence = SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "trail-evidence-test-{}-{now}-{sequence}",
            std::process::id()
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn generate(&self) -> Output {
        Command::new(env!("CARGO_BIN_EXE_evidence_size_compare"))
            .arg("--output-dir")
            .arg(&self.0)
            .output()
            .unwrap()
    }

    fn read(&self, file: &str) -> String {
        fs::read_to_string(self.0.join(file)).unwrap()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn generate() -> Scratch {
    let scratch = Scratch::new();
    let result = scratch.generate();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    scratch
}

type CsvRow<'a> = BTreeMap<&'a str, &'a str>;
type RowKey<'a> = (&'a str, &'a str, &'a str, &'a str);

fn parse(csv: &str) -> Vec<CsvRow<'_>> {
    let mut lines = csv.lines();
    let headers: Vec<_> = lines.next().unwrap().split(',').collect();
    // The schema places its free-text citations last. Only the citation
    // field in these fixture rows can contain CSV delimiters.
    lines
        .map(|line| {
            let columns: Vec<_> = line.splitn(headers.len(), ',').collect();
            assert_eq!(columns.len(), headers.len());
            headers.iter().copied().zip(columns).collect()
        })
        .collect()
}

fn key<'a>(row: &CsvRow<'a>) -> RowKey<'a> {
    (
        row["scheme"],
        row["hops"],
        row["evidence_stage"],
        row["profile"],
    )
}

fn row<'a>(rows: &[CsvRow<'a>], scheme: &str, hops: usize, profile: &str) -> CsvRow<'a> {
    let count = hops.to_string();
    rows.iter()
        .find(|row| row["scheme"] == scheme && row["hops"] == count && row["profile"] == profile)
        .unwrap_or_else(|| panic!("missing {scheme} {hops} {profile}"))
        .clone()
}

fn number(row: &CsvRow<'_>, field: &str) -> usize {
    row[field]
        .parse()
        .unwrap_or_else(|_| panic!("expected {field} for {:?}", key(row)))
}

#[test]
fn covers_each_scheme_and_classifies_unavailable_results() {
    let output = generate();
    let csv = output.read(COMPARISON);
    let rows = parse(&csv);
    for hops in HOPS {
        let trail = row(&rows, "TRAIL", hops, "compact_certificate");
        assert_eq!(trail["measurement_type"], "actual_serialized");
        assert_eq!(number(&trail, "recorded_node_identities"), hops);
        assert_eq!(number(&trail, "signature_bytes"), 48);
        assert_eq!(number(&trail, "identity_bytes"), 8 * hops);
        assert_eq!(number(&trail, "core_evidence_bytes"), 48 + 8 * hops);
        assert_eq!(number(&trail, "encoded_evidence_bytes"), 73 + 8 * hops);
        assert_eq!(number(&trail, "encoding_bytes"), 9);

        let ersoy = row(&rows, "Ersoy", hops, "combined_attached");
        assert_eq!(ersoy["measurement_type"], "assumption_based_estimate");
        assert_eq!(number(&ersoy, "recorded_node_identities"), hops - 1);
        assert_eq!(number(&ersoy, "public_key_bytes"), 33 * (hops - 1));
        assert_eq!(
            number(&ersoy, "encoded_evidence_bytes"),
            97 + 33 * (hops - 1)
        );

        let plutus = row(&rows, "Plutus", hops, "thesis_message_evidence");
        assert_eq!(plutus["measurement_type"], "protocol_encoding_model");
        assert_eq!(number(&plutus, "recorded_node_identities"), hops);
        assert_eq!(number(&plutus, "public_key_bytes"), 32 * hops);
        assert_eq!(number(&plutus, "signature_bytes"), 64 * hops);
        assert_eq!(number(&plutus, "encoded_evidence_bytes"), 96 * hops);
        assert_eq!(
            plutus["source_range"],
            if hops > 9 {
                "extrapolated"
            } else {
                "within_paper_range"
            }
        );

        let nkn = row(&rows, "NKN", hops, "completed_signature_chain");
        if hops == 1 {
            assert_eq!(nkn["measurement_type"], "not_specified");
            assert_eq!(nkn["encoded_evidence_bytes"], "");
        } else {
            assert_eq!(nkn["measurement_type"], "protocol_encoding_model");
            assert_eq!(number(&nkn, "recorded_node_identities"), hops + 1);
            assert_eq!(number(&nkn, "proof_bytes"), 128 * (hops - 1));
            assert_eq!(
                number(&nkn, "encoded_evidence_bytes"),
                586 + 271 * (hops - 2)
            );
        }

        let txtail = row(&rows, "TxTail", hops, "fig8_relay_evidence");
        assert_eq!(txtail["measurement_type"], "protocol_encoding_model");
        assert_eq!(number(&txtail, "recorded_node_identities"), hops);
        assert_eq!(number(&txtail, "public_key_bytes"), 0);
        assert_eq!(number(&txtail, "signature_bytes"), 96);
        assert_eq!(number(&txtail, "identity_bytes"), 7 * hops);
        assert_eq!(number(&txtail, "metadata_bytes"), hops);
        assert_eq!(number(&txtail, "proof_bytes"), 0);
        assert_eq!(number(&txtail, "core_evidence_bytes"), 96 + 7 * hops);
        assert_eq!(number(&txtail, "incremental_evidence_bytes"), 96 + 8 * hops);
        assert_eq!(
            txtail["source_range"],
            if hops > 6 {
                "extrapolated"
            } else {
                "within_paper_range"
            }
        );

        if hops >= 2 {
            let legacy = row(&rows, "NKN", hops, "historical_hash_signature_chain");
            assert_eq!(
                number(&legacy, "encoded_evidence_bytes"),
                586 + 271 * (hops - 2) - 30 * (hops - 1)
            );
        }
    }
}

#[test]
fn field_rollups_match_totals_and_percentages_use_incremental_bytes() {
    let output = generate();
    let csv = output.read(COMPARISON);
    let fields = output.read(BREAKDOWN);
    let mut totals = BTreeMap::<RowKey<'_>, (usize, usize, usize)>::new();
    for row in parse(&fields) {
        if row["encoded_bytes"].is_empty() {
            continue;
        }
        let encoded = number(&row, "encoded_bytes");
        assert_eq!(
            encoded,
            number(&row, "payload_bytes") + number(&row, "encoding_bytes")
        );
        let total = totals.entry(key(&row)).or_default();
        total.0 += encoded;
        total.1 += number(&row, "shared_bytes");
        if !row["incremental_bytes"].is_empty() {
            total.2 += number(&row, "incremental_bytes");
        }
    }
    for row in parse(&csv) {
        if row["measurement_type"] == "not_specified" {
            assert!(row["overhead_pct_540B"].is_empty());
            assert!(!totals.contains_key(&key(&row)));
            continue;
        }
        let (encoded, shared, incremental) = totals[&key(&row)];
        assert_eq!(number(&row, "encoded_evidence_bytes"), encoded);
        assert_eq!(number(&row, "shared_evidence_bytes"), shared);
        if row["evidence_scope"] == "partial_subtotal" {
            assert!(row["overhead_pct_540B"].is_empty());
            assert!(row["total_evidence_bytes"].is_empty());
            continue;
        }
        assert_eq!(number(&row, "total_evidence_bytes"), encoded);
        assert_eq!(number(&row, "incremental_evidence_bytes"), incremental);
        assert_eq!(encoded, shared + incremental);
        for (payload, field) in [(540.0, "overhead_pct_540B"), (300.0, "overhead_pct_300B")] {
            let percent: f64 = row[field].parse().unwrap();
            assert!((payload * percent / 100.0 - incremental as f64).abs() < 0.000005);
        }
    }
}

#[test]
fn contexts_credit_only_material_already_available() {
    let output = generate();
    let csv = output.read(COMPARISON);
    let rows = parse(&csv);
    for (scheme, standalone, shared, expected_overlap) in [
        (
            "Ersoy",
            "combined_attached",
            "combined_replaces_native_signature",
            64,
        ),
        (
            "NKN",
            "completed_signature_chain",
            "completed_signature_chain_shared_context",
            68,
        ),
    ] {
        let a = row(&rows, scheme, 4, standalone);
        let b = row(&rows, scheme, 4, shared);
        assert_eq!(
            number(&a, "encoded_evidence_bytes"),
            number(&b, "encoded_evidence_bytes")
        );
        assert_eq!(number(&a, "shared_evidence_bytes"), 0);
        assert_eq!(number(&b, "shared_evidence_bytes"), expected_overlap);
        assert_eq!(
            number(&a, "incremental_evidence_bytes") - number(&b, "incremental_evidence_bytes"),
            expected_overlap
        );
    }
}

#[test]
fn publication_and_origin_key_are_separate_from_the_offchain_model() {
    let output = generate();
    let csv = output.read(COMPARISON);
    let rows = parse(&csv);
    let tx_key = rows
        .iter()
        .find(|r| r["profile"] == "initiator_key_inferred_width")
        .unwrap();
    assert_eq!(tx_key["evidence_stage"], "transaction_extension");
    assert_eq!(tx_key["measurement_type"], "assumption_based_estimate");
    assert_eq!(number(tx_key, "public_key_bytes"), 48);
    assert_eq!(tx_key["incremental_evidence_bytes"], "");
    let four_hops = row(&rows, "TxTail", 4, "fig8_relay_evidence");
    assert_eq!(number(&four_hops, "total_evidence_bytes"), 128);
    assert_eq!(number(&four_hops, "encoding_bytes"), 0);
    for scheme in ["Ersoy", "Plutus", "NKN", "TxTail"] {
        let row = rows
            .iter()
            .find(|r| r["scheme"] == scheme && r["evidence_stage"] == "on_chain_publication")
            .unwrap();
        assert_eq!(row["total_evidence_bytes"], "");
        assert_eq!(row["overhead_pct_540B"], "");
    }
}

#[test]
fn csv_output_is_deterministic_and_existing_results_are_preserved() {
    let a = generate();
    let b = generate();
    for file in [COMPARISON, BREAKDOWN] {
        assert_eq!(a.read(file), b.read(file));
    }
    let second = a.generate();
    assert!(!second.status.success());
    assert!(String::from_utf8_lossy(&second.stderr).contains("output exists"));
    for file in [COMPARISON, BREAKDOWN] {
        assert_eq!(a.read(file), b.read(file));
    }

    let blocked = Scratch::new();
    fs::write(blocked.0.join(BREAKDOWN), "previous result").unwrap();
    assert!(!blocked.generate().status.success());
    assert!(!blocked.0.join(COMPARISON).exists());
    assert_eq!(blocked.read(BREAKDOWN), "previous result");
}
