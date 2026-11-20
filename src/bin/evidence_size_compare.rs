//! Offline size comparison. The only real cryptographic fixture is TRAIL.
#[path = "evidence_size_compare/model.rs"]
mod model;

use clap::Parser;
use model::{Category, Row};
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};

const COMPARISON: &str = "evidence_size_comparison.csv";
const BREAKDOWN: &str = "evidence_size_breakdown.csv";

#[derive(Parser)]
#[command(about = "Measure and model propagation evidence sizes without running a simulator")]
struct Args {
    #[arg(long, default_value = "results/processed/evidence_size_paper_verified")]
    output_dir: PathBuf,
}

fn csv_row(mut out: impl Write, fields: &[String]) -> io::Result<()> {
    for (index, value) in fields.iter().enumerate() {
        if index != 0 {
            write!(out, ",")?;
        }
        if value.contains([',', '"', '\n', '\r']) {
            write!(out, "\"{}\"", value.replace('"', "\"\""))?;
        } else {
            write!(out, "{value}")?;
        }
    }
    writeln!(out)
}

fn optional(value: Option<usize>) -> String {
    value.map(|value| value.to_string()).unwrap_or_default()
}

fn key(row: &Row) -> Vec<String> {
    vec![
        row.scheme.to_owned(),
        optional(row.hops),
        row.stage.to_owned(),
        row.profile.to_owned(),
        row.measurement.name().to_owned(),
        row.evidence_scope.to_owned(),
        row.source_range.to_owned(),
    ]
}

fn comparison(out: &mut impl Write, rows: &[Row]) -> io::Result<()> {
    writeln!(out, "scheme,hops,evidence_stage,profile,measurement_type,evidence_scope,source_range,recorded_node_identities,signature_bytes,identity_bytes,public_key_bytes,proof_bytes,metadata_bytes,encoding_bytes,core_evidence_bytes,encoded_evidence_bytes,total_evidence_bytes,shared_evidence_bytes,incremental_evidence_bytes,overhead_pct_540B,overhead_pct_300B,baseline,source_or_assumptions")?;
    for row in rows {
        let mut columns = key(row);
        let wire = row.wire.as_ref();
        columns.push(optional(row.identities));
        columns.extend(
            Category::ALL.map(|category| optional(wire.map(|wire| wire.category_bytes(category)))),
        );
        columns.push(optional(wire.map(|wire| wire.core())));
        let encoded = optional(wire.map(|wire| wire.bytes.len()));
        columns.push(encoded.clone());
        columns.push(if row.evidence_scope == "complete_record" {
            encoded
        } else {
            String::new()
        });
        columns.push(optional(wire.map(|wire| wire.shared())));
        columns.push(optional(
            wire.filter(|_| row.evidence_scope == "complete_record")
                .map(|wire| wire.incremental()),
        ));
        for payload in [
            model::BASE_TRANSACTION_BYTES,
            model::REFERENCE_TRANSACTION_BYTES,
        ] {
            columns.push(
                row.percent(payload)
                    .map(|percent| format!("{percent:.6}"))
                    .unwrap_or_default(),
            );
        }
        columns.push(row.baseline.to_owned());
        columns.push(row.note.clone());
        csv_row(&mut *out, &columns)?;
    }
    Ok(())
}

fn breakdown(out: &mut impl Write, rows: &[Row]) -> io::Result<()> {
    writeln!(out, "scheme,hops,evidence_stage,profile,measurement_type,evidence_scope,source_range,field,category,occurrences,payload_bytes,encoding_bytes,encoded_bytes,shared_bytes,incremental_bytes,source_or_assumptions")?;
    for row in rows {
        let Some(wire) = &row.wire else {
            let mut columns = key(row);
            columns.extend(vec![String::new(); 8]);
            columns.push(row.note.clone());
            csv_row(&mut *out, &columns)?;
            continue;
        };
        for (name, field) in &wire.fields {
            let encoded = field.payload + field.framing;
            let incremental =
                (row.evidence_scope == "complete_record").then_some(encoded - field.shared);
            let mut columns = key(row);
            columns.extend([
                name.to_string(),
                field.category.name().to_owned(),
                field.occurrences.to_string(),
                field.payload.to_string(),
                field.framing.to_string(),
                encoded.to_string(),
                field.shared.to_string(),
                optional(incremental),
                row.note.clone(),
            ]);
            csv_row(&mut *out, &columns)?;
        }
    }
    Ok(())
}

fn create(path: &Path) -> io::Result<BufWriter<File>> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map(BufWriter::new)
}

fn main() -> io::Result<()> {
    let directory = Args::parse().output_dir;
    fs::create_dir_all(&directory)?;
    let main_path = directory.join(COMPARISON);
    let detail_path = directory.join(BREAKDOWN);
    for path in [&main_path, &detail_path] {
        if path.try_exists()? {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                format!("output exists: {}", path.display()),
            ));
        }
    }
    let rows = model::rows();
    let mut main = Vec::new();
    let mut detail = Vec::new();
    comparison(&mut main, &rows)?;
    breakdown(&mut detail, &rows)?;
    let mut encoded = create(&main_path)?;
    let mut fields = match create(&detail_path) {
        Ok(file) => file,
        Err(error) => {
            drop(encoded);
            let _ = fs::remove_file(&main_path);
            return Err(error);
        }
    };
    encoded.write_all(&main)?;
    fields.write_all(&detail)?;
    encoded.flush()?;
    fields.flush()?;
    println!("{}\n{}", main_path.display(), detail_path.display());
    Ok(())
}
