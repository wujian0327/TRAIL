//! Offline comparison of lossless formats for an existing block's path evidence.
#[path = "block_validator_compression/archive.rs"]
mod archive;
#[path = "block_validator_compression/codec.rs"]
mod codec;

use clap::Parser;
use codec::{Mode, PathRecord, Scheme, SIGNATURE_BYTES};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    hint::black_box,
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    time::Instant,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MAIN: &str = "results/raw/trail_withholding_main_v2";
const DIAGNOSTIC: &str = "results/raw/withholding-devcheck-20261117-170456";
const REAL: &str = "historical_rust_paths";
const SYNTHETIC: &str = "synthetic_paths";
const CSV_HEADER: &str = concat!(
    "data_source,cohort,topology,validator_count,run_id,block_height,tx_count,path_slot_count,signed_paths,",
    "mean_hops,path_length_histogram,unique_validator_count,total_index_occurrences,repeated_index_occurrences,",
    "repeated_index_pct,index_source,local_index_width,max_global_index,baseline_core_bytes,u32_core_bytes,",
    "global_core_bytes,dictionary_core_bytes,adaptive_core_bytes,baseline_bytes,u32_bytes,global_bytes,dictionary_bytes,",
    "adaptive_bytes,u8_bytes,u32_candidate_bytes,dictionary_entries_bytes,dictionary_overhead_bytes,u32_mode,global_mode,",
    "adaptive_mode,compression_ratio,saving_pct,u32_saving_pct,global_saving_pct,dictionary_saving_pct,",
    "dictionary_vs_u32_saving_pct,dictionary_vs_global_saving_pct,wire_set_sha256",
);

#[derive(Parser)]
#[command(about = "Measure experimental block path encodings with a global 1/4/8-byte cascade")]
struct Args {
    #[arg(
        long,
        default_value = "results/processed/block_validator_compression_v2"
    )]
    output_dir: PathBuf,
    #[arg(long, default_values = [MAIN, DIAGNOSTIC])]
    archive_root: Vec<PathBuf>,
}

fn open_csv(dir: &Path, name: &str, header: &str) -> Result<BufWriter<File>> {
    let mut out = BufWriter::new(
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(dir.join(name))?,
    );
    writeln!(out, "{header}")?;
    Ok(out)
}

fn quote(value: &str) -> String {
    format!("\"{}\"", value.replace('"', "\"\""))
}
fn saving(bytes: usize, baseline: usize) -> f64 {
    100.0 * (1.0 - bytes as f64 / baseline as f64)
}

struct Measurement {
    core: [usize; 5],
    sizes: [usize; 5],
    fixed: Mode,
    global: Mode,
    adaptive: Mode,
    histogram: BTreeMap<usize, usize>,
    n: usize,
    h: usize,
    k: usize,
    max_index: Option<u64>,
    wire_set_sha256: [u8; 32],
}

impl Measurement {
    fn collect(
        records: &[PathRecord],
        block_hash: &[u8; 32],
        valid_index: impl Fn(u64) -> bool,
    ) -> Result<Self> {
        let mut sizes = [0; 5];
        let mut core = [0; 5];
        let mut modes = [Mode::U64; 5];
        let mut wire_set = Sha256::new();
        for (i, scheme) in Scheme::ALL.into_iter().enumerate() {
            let encoded = codec::encode(records, scheme)?;
            wire_set.update((encoded.bytes.len() as u64).to_le_bytes());
            wire_set.update(&encoded.bytes);
            let restored = if scheme == Scheme::Adaptive {
                let digest = codec::commitment(block_hash, &encoded.bytes);
                codec::decode_committed(&encoded.bytes, block_hash, &digest, &valid_index)?
            } else {
                codec::decode(&encoded.bytes, &valid_index)?
            };
            if restored != records {
                return Err("round trip changed path evidence".into());
            }
            sizes[i] = encoded.bytes.len();
            core[i] = encoded.core_bytes;
            modes[i] = encoded.mode;
        }
        let mut histogram = BTreeMap::new();
        for record in records {
            *histogram.entry(record.indices.len()).or_default() += 1;
        }
        let n = records.len() - histogram.get(&0).copied().unwrap_or(0);
        let h = histogram
            .iter()
            .map(|(hop_count, count)| hop_count * count)
            .sum::<usize>();
        let indices = codec::dictionary_entries(records);
        let k = indices.len();
        let max_index = indices.last().copied();
        let signature_bytes = n * SIGNATURE_BYTES;
        let expected_width = match max_index.unwrap_or(0) {
            0..=255 => 1,
            256..=4_294_967_295 => 4,
            _ => 8,
        };
        if core[0] != signature_bytes + 8 * h
            || core[1] != signature_bytes + 4.max(expected_width) * h
            || core[2] != signature_bytes + expected_width * h
            || core[3] != signature_bytes + codec::dictionary_core(h, k)
            || sizes[4] != *sizes[..4].iter().min().unwrap()
        {
            return Err("serialized sizes disagree with the model or adaptive minimum".into());
        }
        Ok(Self {
            core,
            sizes,
            fixed: modes[1],
            global: modes[2],
            adaptive: modes[4],
            histogram,
            n,
            h,
            k,
            max_index,
            wire_set_sha256: wire_set.finalize().into(),
        })
    }

    fn write(&self, mut out: impl Write, block: BlockLabel<'_>) -> Result<()> {
        let [baseline, u32_bytes, global_bytes, dictionary, adaptive] = self.sizes;
        let mean = if self.n == 0 {
            0.0
        } else {
            self.h as f64 / self.n as f64
        };
        let repeats = self.h - self.k;
        let repeat_pct = if self.h == 0 {
            0.0
        } else {
            100.0 * repeats as f64 / self.h as f64
        };
        let histogram = self
            .histogram
            .iter()
            .map(|(h, n)| format!("{h}:{n}"))
            .collect::<Vec<_>>()
            .join("|");
        let slots: usize = self.histogram.values().sum();
        let fields = vec![
            block.source.to_owned(),
            quote(block.cohort),
            quote(block.topology),
            block.validators.to_string(),
            quote(block.run_id),
            block
                .height
                .map(|value| value.to_string())
                .unwrap_or_default(),
            block.tx_count.to_string(),
            slots.to_string(),
            self.n.to_string(),
            format!("{mean:.8}"),
            quote(&histogram),
            self.k.to_string(),
            self.h.to_string(),
            repeats.to_string(),
            format!("{repeat_pct:.8}"),
            block.index_source.to_owned(),
            codec::local_width(self.k).to_string(),
            self.max_index
                .map(|index| index.to_string())
                .unwrap_or_default(),
        ];
        write!(out, "{}", fields.join(","))?;
        for size in self.core.into_iter().chain(self.sizes) {
            write!(out, ",{size}")?;
        }
        writeln!(
            out,
            ",{},{},{},{},{},{},{},{:.8},{:.8},{:.8},{:.8},{:.8},{:.8},{:.8},{}",
            (self.global == Mode::U8)
                .then(|| global_bytes.to_string())
                .unwrap_or_default(),
            (self.fixed == Mode::U32)
                .then(|| u32_bytes.to_string())
                .unwrap_or_default(),
            8 * self.k,
            8 * self.k + 4,
            self.fixed.name(),
            self.global.name(),
            self.adaptive.name(),
            adaptive as f64 / baseline as f64,
            saving(adaptive, baseline),
            saving(u32_bytes, baseline),
            saving(global_bytes, baseline),
            saving(dictionary, baseline),
            saving(dictionary, u32_bytes),
            saving(dictionary, global_bytes),
            hex::encode(self.wire_set_sha256),
        )?;
        Ok(())
    }
}

#[derive(Clone, Copy)]
struct BlockLabel<'a> {
    source: &'static str,
    cohort: &'a str,
    topology: &'a str,
    validators: usize,
    run_id: &'a str,
    height: Option<u64>,
    tx_count: usize,
    index_source: &'static str,
}

struct Savings {
    n: usize,
    sum_pct: f64,
    min_pct: f64,
    max_pct: f64,
    compared_bytes: u64,
    base_bytes: u64,
    at_least_25: usize,
    better: usize,
}

impl Default for Savings {
    fn default() -> Self {
        Self {
            n: 0,
            sum_pct: 0.0,
            min_pct: f64::INFINITY,
            max_pct: f64::NEG_INFINITY,
            compared_bytes: 0,
            base_bytes: 0,
            at_least_25: 0,
            better: 0,
        }
    }
}

impl Savings {
    fn observe(&mut self, bytes: usize, baseline: usize) {
        let pct = saving(bytes, baseline);
        self.n += 1;
        self.sum_pct += pct;
        self.min_pct = self.min_pct.min(pct);
        self.max_pct = self.max_pct.max(pct);
        self.compared_bytes += bytes as u64;
        self.base_bytes += baseline as u64;
        self.at_least_25 += usize::from(bytes as u128 * 4 <= baseline as u128 * 3);
        self.better += usize::from(bytes < baseline);
    }
}

#[derive(Default)]
struct ModeStats {
    selected: [usize; 4],
    adaptive: Savings,
}

type ModeGroups = BTreeMap<(&'static str, String, usize), ModeStats>;

fn observe_mode(groups: &mut ModeGroups, block: BlockLabel<'_>, measured: &Measurement) {
    let stats = groups
        .entry((block.source, block.cohort.to_owned(), block.validators))
        .or_default();
    stats.selected[measured.adaptive as usize] += 1;
    stats.adaptive.observe(measured.sizes[4], measured.sizes[0]);
}

struct TimingCase {
    name: String,
    records: Vec<PathRecord>,
    min_index: u64,
    end_index: u64,
}

fn synthetic(n: usize, h: usize, k: usize, start: u64) -> Vec<PathRecord> {
    assert!(h <= k && k <= n * h);
    (0..n)
        .map(|tx| {
            let indices = (0..h).map(|i| start + ((tx * h + i) % k) as u64).collect();
            let mut signature = [0; SIGNATURE_BYTES];
            signature[..8].copy_from_slice(&(tx as u64).to_le_bytes());
            PathRecord {
                indices,
                signature: Some(signature),
            }
        })
        .collect()
}

fn write_synthetic(
    out: &mut impl Write,
    dir: &Path,
    mode_groups: &mut ModeGroups,
) -> Result<Vec<TimingCase>> {
    let mut unavailable = open_csv(
        dir,
        "synthetic_unavailable.csv",
        "tx_count,hops,target_unique_validators,reason",
    )?;
    let mut timing = Vec::new();
    for n in [100, 1000, 4000] {
        for h in [2, 4, 6] {
            for k in [16, 64, 128, 256, 512, 1024, n * h] {
                if k > n * h {
                    writeln!(
                        unavailable,
                        "{n},{h},{k},unique indices exceed index occurrences"
                    )?;
                    continue;
                }
                let cohort = if k == n * h { "all_distinct" } else { "grid" };
                let records = synthetic(n, h, k, 0);
                let measured = Measurement::collect(&records, &[0; 32], |id| id < k as u64)?;
                assert_eq!((measured.h, measured.k), (n * h, k));
                measured.write(
                    &mut *out,
                    BlockLabel {
                        source: SYNTHETIC,
                        cohort,
                        topology: "synthetic",
                        validators: k + 1,
                        run_id: "",
                        height: None,
                        tx_count: n,
                        index_source: "generated",
                    },
                )?;
                if (h == 4 && k == 64 && n <= 1000)
                    || (n == 4000 && h == 4 && (k == 512 || k == n * h))
                {
                    timing.push(TimingCase {
                        name: format!("synthetic_{n}_{h}_{k}"),
                        records,
                        min_index: 0,
                        end_index: k as u64,
                    });
                }
            }
        }
    }
    let start = u64::from(u32::MAX) + 1;
    let records = synthetic(100, 4, 64, start);
    let measured =
        Measurement::collect(&records, &[0; 32], |id| (start..start + 64).contains(&id))?;
    assert_eq!(measured.fixed, Mode::U64);
    measured.write(
        &mut *out,
        BlockLabel {
            source: SYNTHETIC,
            cohort: "u64_required",
            topology: "synthetic",
            validators: 65,
            run_id: "",
            height: None,
            tx_count: 100,
            index_source: "generated",
        },
    )?;
    for validators in [100, 256, 1000] {
        for n in [100, 1000, 4000] {
            for h in [2, 4, 6] {
                let spread = (validators - 1).min(n * h);
                for (cohort, k, first) in [
                    ("roster_low", 64, 0),
                    ("roster_high", 64, validators - 64),
                    ("roster_spread", spread, validators - spread),
                ] {
                    let records = synthetic(n, h, k, first as u64);
                    let measured =
                        Measurement::collect(&records, &[0; 32], |id| id < validators as u64)?;
                    let label = BlockLabel {
                        source: SYNTHETIC,
                        cohort,
                        topology: "synthetic",
                        validators,
                        run_id: "",
                        height: None,
                        tx_count: n,
                        index_source: "generated",
                    };
                    measured.write(&mut *out, label)?;
                    observe_mode(mode_groups, label, &measured);
                    if validators == 1000 && n == 1000 && h == 4 && cohort == "roster_high" {
                        timing.push(TimingCase {
                            name: "synthetic_1000_4_64_high_indices".into(),
                            records,
                            min_index: first as u64,
                            end_index: validators as u64,
                        });
                    }
                }
            }
        }
    }
    unavailable.flush()?;
    Ok(timing)
}

fn time_cases(dir: &Path, cases: &[TimingCase]) -> Result<()> {
    const BATCHES: usize = 9;
    const REPEATS: usize = 100;
    let mut out = open_csv(dir, "codec_timing.csv", "sample,path_slots,signed_paths,total_index_occurrences,unique_validator_count,scheme,selected_mode,phase,batches,iterations_per_batch,min_us,median_us,max_us")?;
    for case in cases {
        let records = &case.records;
        let n = records.iter().filter(|p| p.signature.is_some()).count();
        let h: usize = records.iter().map(|p| p.indices.len()).sum();
        let k = codec::dictionary_entries(records).len();
        let allowed = |id| (case.min_index..case.end_index).contains(&id);
        for scheme in Scheme::ALL {
            let encoded = codec::encode(records, scheme)?;
            black_box(codec::decode(&encoded.bytes, allowed)?);
            let mut encode_us = [0.0; BATCHES];
            let mut decode_us = [0.0; BATCHES];
            for (encoding, decoding) in encode_us.iter_mut().zip(&mut decode_us) {
                let start = Instant::now();
                for _ in 0..REPEATS {
                    black_box(codec::encode(black_box(records), scheme)?);
                }
                *encoding = start.elapsed().as_secs_f64() * 1e6 / REPEATS as f64;
                let start = Instant::now();
                for _ in 0..REPEATS {
                    black_box(codec::decode(black_box(&encoded.bytes), allowed)?);
                }
                *decoding = start.elapsed().as_secs_f64() * 1e6 / REPEATS as f64;
            }
            for (phase, mut samples) in [("encode", encode_us), ("decode", decode_us)] {
                samples.sort_by(f64::total_cmp);
                writeln!(
                    out,
                    "{},{},{n},{h},{k},{},{},{phase},{BATCHES},{REPEATS},{:.6},{:.6},{:.6}",
                    quote(&case.name),
                    records.len(),
                    scheme.name(),
                    encoded.mode.name(),
                    samples[0],
                    samples[BATCHES / 2],
                    samples[BATCHES - 1]
                )?;
            }
        }
    }
    out.flush()?;
    Ok(())
}

fn main() -> Result<()> {
    let args = Args::parse();
    let dir = &args.output_dir;
    fs::create_dir_all(dir)?;
    let mut out = open_csv(dir, "block_compression.csv", CSV_HEADER)?;
    let mut provenance = open_csv(dir, "archive_provenance.csv", "cohort,run_id,topology,validator_count,archive_path,blocks,legacy_signed_paths,indexed_signed_paths,uncached_bls_samples")?;
    let mut summary: BTreeMap<(String, String, u32), [Savings; 6]> = BTreeMap::new();
    let mut mode_groups = ModeGroups::new();
    let mut archive_timing = BTreeMap::new();
    for root in &args.archive_root {
        let cohort = root
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or("invalid archive root")?;
        let files = archive::discover(root)?;
        if files.is_empty() {
            return Err(format!("no blocks.jsonl in {}", root.display()).into());
        }
        for file in files {
            let (run, audit) = archive::read_run(&file, |run, block| {
                let count = u64::from(run.node_num);
                let measured = Measurement::collect(&block.records, &block.hash, |id| id < count)?;
                let label = BlockLabel {
                    source: REAL,
                    cohort,
                    topology: &run.topology,
                    validators: count as usize,
                    run_id: &run.run_id,
                    height: Some(block.height),
                    tx_count: block.transaction_count,
                    index_source: block.index_source,
                };
                measured.write(&mut out, label)?;
                observe_mode(&mut mode_groups, label, &measured);
                let group = (cohort.to_owned(), run.topology.clone(), run.node_num);
                let stats = summary.entry(group.clone()).or_default();
                let [base, fixed, global, dict, adaptive] = measured.sizes;
                for (stat, (bytes, reference)) in stats.iter_mut().zip([
                    (fixed, base),
                    (dict, base),
                    (adaptive, base),
                    (dict, fixed),
                    (global, base),
                    (dict, global),
                ]) {
                    stat.observe(bytes, reference);
                }
                if measured.n > 0 && !archive_timing.contains_key(&group) {
                    let name = format!(
                        "{}_{}_{}_block_{}",
                        cohort, run.node_num, run.run_id, block.height
                    );
                    archive_timing.insert(
                        group,
                        TimingCase {
                            name,
                            records: block.records,
                            min_index: 0,
                            end_index: count,
                        },
                    );
                }
                Ok(())
            })?;
            writeln!(
                provenance,
                "{},{},{},{},{},{},{},{},{}",
                quote(cohort),
                quote(&run.run_id),
                quote(&run.topology),
                run.node_num,
                quote(&file.to_string_lossy()),
                audit.blocks,
                audit.legacy_certificates,
                audit.indexed_certificates,
                audit.bls_samples
            )?;
        }
        eprintln!("Read {cohort}");
    }
    let mut timings = write_synthetic(&mut out, dir, &mut mode_groups)?;
    timings.extend(archive_timing.into_values());
    out.flush()?;
    provenance.flush()?;

    let mut stats_out = open_csv(dir, "archive_savings_summary.csv", "cohort,topology,validator_count,scheme,reference,blocks,mean_saving_pct,min_saving_pct,max_saving_pct,pooled_saving_pct,blocks_at_least_25_pct,blocks_smaller_than_reference")?;
    for ((cohort, topology, count), stats) in summary {
        for ((scheme, reference), stat) in [
            ("fixed4", "baseline"),
            ("dictionary", "baseline"),
            ("adaptive", "baseline"),
            ("dictionary", "fixed4"),
            ("global", "baseline"),
            ("dictionary", "global"),
        ]
        .into_iter()
        .zip(stats)
        {
            writeln!(
                stats_out,
                "{},{},{count},{scheme},{reference},{},{:.8},{:.8},{:.8},{:.8},{},{}",
                quote(&cohort),
                quote(&topology),
                stat.n,
                stat.sum_pct / stat.n as f64,
                stat.min_pct,
                stat.max_pct,
                saving(stat.compared_bytes as usize, stat.base_bytes as usize),
                stat.at_least_25,
                stat.better
            )?;
        }
    }
    stats_out.flush()?;
    let mut mode_out = open_csv(dir, "adaptive_mode_selection.csv", "data_source,cohort,validator_count,blocks,u8_blocks,u32_blocks,u64_blocks,dictionary_blocks,mean_saving_pct,min_saving_pct,max_saving_pct,blocks_at_least_25_pct")?;
    for ((source, cohort, validators), stats) in mode_groups {
        let [u64_blocks, u32_blocks, dictionary_blocks, u8_blocks] = stats.selected;
        let savings = stats.adaptive;
        writeln!(mode_out, "{source},{},{validators},{},{u8_blocks},{u32_blocks},{u64_blocks},{dictionary_blocks},{:.8},{:.8},{:.8},{}",
            quote(&cohort), savings.n, savings.sum_pct / savings.n as f64, savings.min_pct, savings.max_pct, savings.at_least_25)?;
    }
    mode_out.flush()?;
    time_cases(dir, &timings)
}
