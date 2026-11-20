//! Read exact certificate sequences from saved Rust block logs, with roster provenance.
use super::{codec::PathRecord, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
};
use trail::{
    blockchain::{
        block::{Block, Header},
        path::AggregatedSignedPaths,
        transaction::Transaction,
    },
    consensus::ValidatorRegistry,
    tools::Hasher,
    wallet::Wallet,
};

#[derive(Deserialize)]
struct RunConfig {
    config: Run,
}

#[derive(Deserialize)]
pub struct Run {
    pub run_id: String,
    pub topology: String,
    pub node_num: u32,
    wallet_seed: u64,
}

#[derive(Deserialize)]
struct RunSummary {
    block_production_success: usize,
}

#[derive(Deserialize, Serialize)]
#[serde(untagged)]
enum Identity {
    Indexed(u64),
    Address(String),
}

#[derive(Deserialize, Serialize)]
struct Certificate {
    chain_id: u64,
    epoch: u64,
    signature: String,
    paths: Vec<Identity>,
}

#[derive(Deserialize)]
struct StoredBody {
    transactions: Vec<Transaction>,
    paths: Vec<Certificate>,
}

#[derive(Deserialize)]
struct StoredBlock {
    header: Header,
    body: StoredBody,
}

pub struct ArchivedBlock {
    pub height: u64,
    pub hash: [u8; 32],
    pub transaction_count: usize,
    pub records: Vec<PathRecord>,
    pub index_source: &'static str,
}

pub struct Audit {
    pub blocks: usize,
    pub legacy_certificates: usize,
    pub indexed_certificates: usize,
    pub bls_samples: usize,
}

pub fn discover(root: &Path) -> Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    for item in fs::read_dir(root)? {
        let item = item?;
        let ty = item.file_type()?;
        if ty.is_file() && item.file_name() == "blocks.jsonl" {
            files.push(item.path());
        } else if ty.is_dir() {
            files.extend(discover(&item.path())?);
        }
    }
    files.sort();
    Ok(files)
}

fn read_json<T: for<'a> Deserialize<'a>>(path: &Path) -> Result<T> {
    serde_json::from_reader(BufReader::new(File::open(path)?))
        .map_err(|err| format!("{}: {err}", path.display()).into())
}

pub fn read_run(
    path: &Path,
    mut visit: impl FnMut(&Run, ArchivedBlock) -> Result<()>,
) -> Result<(Run, Audit)> {
    let directory = path
        .parent()
        .ok_or("block archive requires a run directory")?;
    let RunConfig { config } = read_json(&directory.join("run_config.json"))?;
    let roster: BTreeMap<String, f64> = read_json(&directory.join("initial_stakes.json"))?;
    let summary: RunSummary = read_json(&directory.join("run_summary.json"))?;
    let wallets: Vec<_> = (0..config.node_num)
        .map(|i| Wallet::new_deterministic(config.wallet_seed, i))
        .collect();
    let registry = ValidatorRegistry::from_wallets(&wallets)?;
    if !registry
        .entries()
        .map(|(_, address, _)| address)
        .eq(roster.keys().map(String::as_str))
    {
        return Err(format!(
            "{}: wallet seed does not reproduce the complete archived roster",
            path.display()
        )
        .into());
    }
    let mut audit = Audit {
        blocks: 0,
        legacy_certificates: 0,
        indexed_certificates: 0,
        bls_samples: 0,
    };
    for line in BufReader::new(File::open(path)?).lines() {
        let line = line?;
        let block: StoredBlock = serde_json::from_str(&line)
            .map_err(|err| format!("{} line {}: {err}", path.display(), audit.blocks + 1))?;
        let height = block.header.index;
        let archival = convert_block(block, &registry, &mut audit)
            .map_err(|err| format!("{} block {height}: {err}", path.display()))?;
        visit(&config, archival)?;
        audit.blocks += 1;
    }
    if audit.blocks != summary.block_production_success {
        return Err(format!(
            "{}: saved block count differs from run summary",
            path.display()
        )
        .into());
    }
    Ok((config, audit))
}

fn convert_block(
    block: StoredBlock,
    registry: &ValidatorRegistry,
    audit: &mut Audit,
) -> Result<ArchivedBlock> {
    let StoredBlock { header, body } = block;
    let StoredBody {
        transactions,
        paths,
    } = body;
    if paths.len() > transactions.len() {
        return Err("more path slots than transactions".into());
    }
    let leaves = paths
        .iter()
        .map(|path| serde_json::to_vec(path).map(|json| hex::encode(Hasher::hash(json))))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    if Block::cal_merkle_root(leaves) != header.path_root || header.get_hash() != header.hash {
        return Err("archived path root or header hash differs".into());
    }
    let proposer = registry.index(&header.miner)?;
    let mut records = Vec::with_capacity(paths.len());
    let mut legacy = false;
    let mut indexed = false;
    for (certificate, tx) in paths.into_iter().zip(&transactions) {
        let address_path = matches!(certificate.paths.first(), Some(Identity::Address(_)));
        let mut indices = Vec::with_capacity(certificate.paths.len());
        let mut seen = BTreeSet::from([proposer]);
        for item in certificate.paths {
            let index = match item {
                Identity::Indexed(index) if !address_path => {
                    registry.address(index)?;
                    index
                }
                Identity::Address(address) if address_path => registry.index(&address)?,
                _ => return Err("mixed identity types in certificate".into()),
            };
            if !seen.insert(index) {
                return Err("duplicate validator in path".into());
            }
            indices.push(index);
        }
        let certificate = AggregatedSignedPaths {
            chain_id: certificate.chain_id,
            epoch: certificate.epoch,
            signature: certificate.signature,
            paths: indices,
        };
        let record = PathRecord::from_certificate(&certificate)?;
        if record.signature.is_some() {
            if registry.address(record.indices[0])? != tx.from || certificate.epoch != header.epoch
            {
                return Err("path does not match transaction sender or block epoch".into());
            }
            // Verify a signed path from every run against the real transaction and reconstructed keys.
            if audit.bls_samples == 0 {
                let restored = record
                    .clone()
                    .into_certificate(certificate.chain_id, certificate.epoch);
                if !restored.verify_at_epoch_uncached(tx, &header.miner, header.epoch, registry) {
                    return Err("native BLS verification failed after reindexing".into());
                }
                audit.bls_samples += 1;
            }
            if address_path {
                audit.legacy_certificates += 1;
                legacy = true;
            } else {
                audit.indexed_certificates += 1;
                indexed = true;
            }
        }
        records.push(record);
    }
    let index_source = match (legacy, indexed) {
        (true, false) => "reconstructed_from_full_roster",
        (false, true) => "archived_indices",
        (true, true) => "mixed_certificates",
        (false, false) => "no_signed_paths",
    };
    let hash = hex::decode(&header.hash)?
        .try_into()
        .map_err(|_| "invalid block hash width")?;
    Ok(ArchivedBlock {
        height: header.index,
        hash,
        transaction_count: transactions.len(),
        records,
        index_source,
    })
}
