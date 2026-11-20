//! Offline accounting for the path payload carried by a synthetic block.
use clap::Parser;
use std::fs::{self, OpenOptions};
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use trail::blockchain::path::{AggregatedSignedPaths, TransactionPaths};
use trail::blockchain::transaction::Transaction;
use trail::consensus::ValidatorRegistry;
use trail::wallet::Wallet;

const TRANSACTION_COUNTS: [usize; 3] = [100, 1000, 4000];
const HOPS: [usize; 3] = [2, 4, 6];
const TRAIL_SOURCE: &str =
    "TRAIL Sec. III-B.2, pp. 4-5, Fig. 3 and src/blockchain/path.rs and block.rs";
const ERSOY_SOURCE: &str = "Ersoy et al. Sec. VI, PDF pp. 8-10, Algorithm 2 and Storage efficiency, https://arxiv.org/pdf/1712.07564#page=9";
const PLUTUS_SOURCE: &str = "Hemo Sec. 3.2.2 pp. 24-25, Sec. 3.3 pp. 25-28, and Sec. 5.5 p. 38, https://hdl.handle.net/1721.1/119766";
const NKN_SOURCE: &str = "NKN whitepaper Sec. 5.3 pp. 11-12 and Go commit 6ff0fd160d2da341c5c329bea3e971f1c36c1ac5, chain/mining.go:72-84, chain/pool/txpool.go:312-317, lnode/relay.go:263-294, por/sigchain.go:32-145, pb/sigchain.proto";
const TXTAIL_SOURCE: &str = "Dai et al. Sec. V-A/C pp. 3202-3203, V-D p. 3204, VIII-A and Figs. 8-9 pp. 3208-3209, https://doi.org/10.1109/TIFS.2026.3675466";

const NATIVE: &str = "protocol_native";
const RETENTION: &str = "full_evidence_retention";
const SENSITIVITY: &str = "nkn_state_sensitivity";
const NKN_CONDITION: &str = "nkn_disclosure_condition";
const ERSOY_LAYOUT: &str = "ersoy_encoding_sensitivity";
const NOT_SPECIFIED: &str = "not_specified";
const SERIALIZED: &str = "actual_serialized";
const MODELED: &str = "protocol_encoding_model";
const ASSUMED: &str = "assumption_based_estimate";

#[derive(Parser)]
#[command(about = "Compare on-chain path payload sizes using offline Rust encoders")]
struct Args {
    #[arg(long, default_value = "results/processed/onchain_path_storage_v2")]
    output_dir: PathBuf,
}

#[derive(Default)]
struct PathEvidence {
    identities: Vec<u8>,
    signatures: Vec<u8>,
    proofs: Vec<u8>,
    path_identifier: Vec<u8>,
    reward_shares: Vec<u8>,
}

impl PathEvidence {
    fn field_sizes(&self) -> [usize; 4] {
        [
            self.identities.len(),
            self.signatures.len(),
            self.proofs.len() + self.path_identifier.len(),
            self.reward_shares.len(),
        ]
    }

    fn len(&self) -> usize {
        self.field_sizes().iter().sum()
    }
}

fn trail_fields(certificate: &AggregatedSignedPaths) -> PathEvidence {
    let encoded = certificate
        .to_compact_binary()
        .expect("fixture certificate encodes");
    let signature_end = 17 + usize::from(encoded[16]);
    let index_start = signature_end + 8;
    let evidence = PathEvidence {
        signatures: encoded[17..signature_end].to_vec(),
        identities: encoded[index_start..].to_vec(),
        ..PathEvidence::default()
    };
    assert_eq!(evidence.len(), certificate.bytes() as usize);
    evidence
}

fn signed_trail_certificate(hops: usize) -> AggregatedSignedPaths {
    let wallets: Vec<_> = (0..=hops)
        .map(|index| Wallet::new_deterministic(0x5041_5448 + hops as u64, index as u32))
        .collect();
    let proposer = &wallets[hops].address;
    let registry = ValidatorRegistry::from_wallets(&wallets).expect("distinct fixture wallets");
    let tx = Transaction::with_timestamp(
        proposer.clone(),
        1,
        1.0,
        1.0,
        wallets[0].clone(),
        false,
        1_700_000_000,
    );
    let mut path = TransactionPaths::new_with_epoch(tx.clone(), 7);
    for pair in wallets.windows(2) {
        assert!(path.append_completed_hop(
            pair[1].address.clone(),
            pair[0].clone(),
            pair[1].clone()
        ));
    }
    let certificate = path
        .to_aggregated_signed_paths(&registry)
        .expect("fixture path aggregates");
    assert!(certificate.verify_at_epoch_uncached(&tx, proposer, path.epoch, &registry));
    certificate
}

// Byte-width fixtures for the other protocols. They encode payload fields only.
fn ersoy_fields(hops: usize, identifier_bytes: usize) -> PathEvidence {
    PathEvidence {
        identities: vec![0x21; 33 * (hops - 1)], // Assumed compressed keys of intermediaries.
        signatures: vec![0x22; 64],              // Assumed compact client path signature.
        path_identifier: vec![0x23; identifier_bytes],
        ..PathEvidence::default()
    }
}

fn plutus_fields(hops: usize) -> PathEvidence {
    PathEvidence {
        identities: vec![0x31; 32 * hops], // A recipient key for each edge.
        signatures: vec![0x32; 64 * hops], // A sender signature for each edge.
        ..PathEvidence::default()
    }
}

fn nkn_fields(hops: usize) -> PathEvidence {
    let mut path = PathEvidence::default();
    // Source and destination each carry one ID, key, and Ed25519 signature.
    for _ in 0..2 {
        path.identities.extend_from_slice(&[0x41; 32]);
        path.identities.extend_from_slice(&[0x42; 32]);
        path.signatures.extend_from_slice(&[0x43; 64]);
    }
    // Each interior relay has an ID and a public key carried by its predecessor.
    // The last relay can omit the next key because dest_pubkey carries it.
    for _ in 1..hops {
        path.identities.extend_from_slice(&[0x44; 32]);
        path.identities.extend_from_slice(&[0x45; 32]);
        path.signatures.extend_from_slice(&[0x46; 64]);
        path.proofs.extend_from_slice(&[0x47; 32]); // VRF output.
        path.proofs.extend_from_slice(&[0x48; 96]); // VRF proof.
    }
    path
}

fn txtail_fields(hops: usize) -> PathEvidence {
    PathEvidence {
        identities: vec![0x51; 7 * hops], // Public-key descriptors.
        signatures: vec![0x52; 96],       // Fig. 8 aggregate signature width.
        reward_shares: vec![0x53; hops],
        ..PathEvidence::default()
    }
}

fn txtail_sample(z: usize, block_hash: &[u8; 32]) -> Option<usize> {
    if z == 0 {
        return None;
    }
    Some(block_hash.iter().fold(0_u128, |index, &byte| {
        (index * 256 + u128::from(byte)) % z as u128
    }) as usize)
}

fn nkn_disclosures(has_winner: bool, winner_already_on_chain: bool) -> usize {
    usize::from(has_winner && !winner_already_on_chain)
}

#[derive(Clone, Copy)]
struct Profile {
    scheme: &'static str,
    source: &'static str,
    rule: &'static str,
    assumptions: &'static str,
}

const TRAIL: Profile = Profile {
    scheme: "TRAIL",
    source: TRAIL_SOURCE,
    rule: "One independent path certificate per certified transaction",
    assumptions:
        "All z synthetic transactions have a valid path. One verified Rust fixture per hop count",
};
const ERSOY: Profile = Profile {
    scheme: "Ersoy", source: ERSOY_SOURCE,
    rule: "Every included transaction carries its path under the combined protocol with a predetermined leader",
    assumptions: "Plain-key Combined model. h-1 compressed SEC1 keys at 33 B and one separate compact client path signature at 64 B. Recover the identifier from the leader credential, leader key, and ordered intermediary keys. The original transaction authorization is outside the path payload",
};
const ERSOY_WITH_IDENTIFIER: Profile = Profile {
    assumptions: "Same attached client signature and intermediary keys. Assume the block also carries a separate 32 B path identifier. Generic framing and original transaction authorization are excluded",
    ..ERSOY
};
const PLUTUS: Profile = Profile {
    scheme: "Plutus", source: PLUTUS_SOURCE,
    rule: "Lookback blocks supply relay keys. A new proposal includes lottery winners and a proposer VRF proof",
    assumptions: "Full path disclosure count and native path layout are unspecified",
};
const NKN: Profile = Profile {
    scheme: "NKN", source: NKN_SOURCE,
    rule: "The official block builder inserts one winning SigChainTxn if it is not already on the ledger. Otherwise it inserts none",
    assumptions: "Height >= 1200000 SIGNATURE fixture. Two endpoints and h-1 relays. Last next key recovered from dest_pubkey. Winner state is external to z",
};
const TXTAIL: Profile = Profile {
    scheme: "TxTail", source: TXTAIL_SOURCE,
    rule: "One sampled transaction path per nonempty block",
    assumptions: "Fig. 8 aggregate signature width. h contributing relays per Fig. 9 convention. The initiator key is in the original transaction",
};

struct BlockEvidence {
    count: Option<usize>,
    min_count: Option<usize>,
    max_count: Option<usize>,
    probability: Option<f64>,
}

impl BlockEvidence {
    fn exact(count: usize) -> Self {
        Self {
            count: Some(count),
            min_count: Some(count),
            max_count: Some(count),
            probability: None,
        }
    }

    fn unknown() -> Self {
        Self {
            count: None,
            min_count: None,
            max_count: None,
            probability: None,
        }
    }

    fn nkn(probability: Option<f64>) -> Self {
        Self {
            count: None,
            min_count: Some(nkn_disclosures(false, false)),
            max_count: Some(nkn_disclosures(true, false)),
            probability,
        }
    }

    fn expected_count(&self) -> Option<f64> {
        self.probability.map(|p| {
            (1.0 - p) * self.min_count.unwrap() as f64 + p * self.max_count.unwrap() as f64
        })
    }
}

fn percent(total_bytes: f64, z: usize, tx_bytes: usize) -> f64 {
    100.0 * total_bytes / (z * tx_bytes) as f64
}

const CSV_HEADER: &str = concat!(
    "scenario,scheme,transactions_per_block,hops,path_identity_bytes,path_signature_bytes,",
    "path_proof_bytes,path_reward_share_bytes,path_identifier_bytes,evidence_bytes_per_path,onchain_evidence_count,",
    "onchain_evidence_count_min,onchain_evidence_count_max,nkn_new_winner_probability,expected_evidence_count,",
    "total_path_storage_bytes,expected_path_storage_bytes,storage_kib,storage_bytes_per_transaction,",
    "overhead_pct_540B,overhead_pct_300B,measurement_type,path_measurement_type,disclosure_rule,source_reference,assumptions",
);

fn optional<T: ToString>(value: Option<T>) -> String {
    value.map(|value| value.to_string()).unwrap_or_default()
}

fn decimal(value: Option<f64>) -> String {
    value.map(|value| format!("{value:.8}")).unwrap_or_default()
}

struct Row<'a> {
    scenario: &'static str,
    profile: Profile,
    z: usize,
    hops: usize,
    path: Option<&'a PathEvidence>,
    block: BlockEvidence,
    measurement: &'static str,
    path_measurement: &'static str,
}

impl Row<'_> {
    fn write(&self, mut out: impl Write) -> io::Result<()> {
        let per_path = self.path.map(PathEvidence::len);
        let counts = self.path.map(PathEvidence::field_sizes);
        let total = self.block.count.zip(per_path).map(|(n, bytes)| n * bytes);
        let expected_count = self.block.expected_count();
        let expected_total = expected_count
            .zip(per_path)
            .map(|(n, bytes)| n * bytes as f64);
        let storage = total.map(|bytes| bytes as f64).or(expected_total);
        let mut columns = vec![
            self.scenario.to_owned(),
            self.profile.scheme.to_owned(),
            self.z.to_string(),
            self.hops.to_string(),
        ];
        columns.extend((0..4).map(|index| optional(counts.map(|fields| fields[index]))));
        columns.extend([
            optional(self.path.map(|path| path.path_identifier.len())),
            optional(per_path),
            optional(self.block.count),
            optional(self.block.min_count),
            optional(self.block.max_count),
            decimal(self.block.probability),
            decimal(expected_count),
            optional(total),
            decimal(expected_total),
            decimal(storage.map(|bytes| bytes / 1024.0)),
            decimal(storage.map(|bytes| bytes / self.z as f64)),
            decimal(storage.map(|bytes| percent(bytes, self.z, 540))),
            decimal(storage.map(|bytes| percent(bytes, self.z, 300))),
            self.measurement.to_owned(),
            self.path_measurement.to_owned(),
        ]);
        columns.extend(
            [
                self.profile.rule,
                self.profile.source,
                self.profile.assumptions,
            ]
            .map(|s| format!("\"{}\"", s.replace('"', "\"\""))),
        );
        writeln!(out, "{}", columns.join(","))
    }
}

fn open_csv(directory: &Path, name: &str) -> io::Result<BufWriter<fs::File>> {
    let mut file = BufWriter::new(
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(directory.join(name))?,
    );
    writeln!(file, "{CSV_HEADER}")?;
    Ok(file)
}

fn generate(directory: &Path) -> io::Result<()> {
    fs::create_dir_all(directory)?;
    let mut native = open_csv(directory, "protocol_native.csv")?;
    let mut retention = open_csv(directory, "full_evidence_retention.csv")?;
    let mut sensitivity = open_csv(directory, "nkn_state_sensitivity.csv")?;
    let mut nkn_conditions = open_csv(directory, "nkn_disclosure_conditions.csv")?;
    let mut ersoy_layouts = open_csv(directory, "ersoy_encoding_sensitivity.csv")?;
    for hops in HOPS {
        let verified = trail_fields(&signed_trail_certificate(hops));
        let ersoy = ersoy_fields(hops, 0);
        let ersoy_with_identifier = ersoy_fields(hops, 32);
        let plutus = plutus_fields(hops);
        let nkn = nkn_fields(hops);
        let txtail = txtail_fields(hops);
        for z in TRANSACTION_COUNTS {
            let sampled_paths = txtail_sample(z, &[0x47; 32]).iter().count();
            for (profile, path, block, measurement, path_measurement) in [
                (
                    TRAIL,
                    Some(&verified),
                    BlockEvidence::exact(z),
                    SERIALIZED,
                    SERIALIZED,
                ),
                (
                    ERSOY,
                    Some(&ersoy),
                    BlockEvidence::exact(z),
                    ASSUMED,
                    ASSUMED,
                ),
                (
                    PLUTUS,
                    None,
                    BlockEvidence::unknown(),
                    NOT_SPECIFIED,
                    NOT_SPECIFIED,
                ),
                (
                    NKN,
                    Some(&nkn),
                    BlockEvidence::nkn(None),
                    NOT_SPECIFIED,
                    MODELED,
                ),
                (
                    TXTAIL,
                    Some(&txtail),
                    BlockEvidence::exact(sampled_paths),
                    MODELED,
                    MODELED,
                ),
            ] {
                Row {
                    scenario: NATIVE,
                    profile,
                    z,
                    hops,
                    path,
                    block,
                    measurement,
                    path_measurement,
                }
                .write(&mut native)?;
            }
            let plutus_modeled = Profile {
                assumptions: "Hypothetical retention of the complete relay-message attachment in Fig. 5-8. 32 B recipient key and 64 B sender signature per edge. Lottery fields excluded",
                ..PLUTUS
            };
            for (profile, path, path_measurement) in [
                (TRAIL, &verified, SERIALIZED),
                (ERSOY, &ersoy, ASSUMED),
                (plutus_modeled, &plutus, MODELED),
                (NKN, &nkn, MODELED),
                (TXTAIL, &txtail, MODELED),
            ] {
                let profile = Profile {
                    rule:
                        "Counterfactual retention of one complete evidence object per transaction",
                    ..profile
                };
                Row {
                    scenario: RETENTION,
                    profile,
                    z,
                    hops,
                    path: Some(path),
                    block: BlockEvidence::exact(z),
                    measurement: ASSUMED,
                    path_measurement,
                }
                .write(&mut retention)?;
            }
            for (profile, path) in [
                (ERSOY, &ersoy),
                (ERSOY_WITH_IDENTIFIER, &ersoy_with_identifier),
            ] {
                Row {
                    scenario: ERSOY_LAYOUT,
                    profile,
                    z,
                    hops,
                    path: Some(path),
                    block: BlockEvidence::exact(z),
                    measurement: ASSUMED,
                    path_measurement: ASSUMED,
                }
                .write(&mut ersoy_layouts)?;
            }
            for (count, rule) in [
                (nkn_disclosures(false, false), "Zero new SigChains. No winner is selected or the winner is already on the ledger"),
                (nkn_disclosures(true, false), "One new SigChain. TXN_SIGNER winner selected and not yet on the ledger"),
            ] {
                Row {
                    scenario: NKN_CONDITION,
                    profile: Profile { rule, ..NKN },
                    z,
                    hops,
                    path: Some(&nkn),
                    block: BlockEvidence::exact(count),
                    measurement: MODELED,
                    path_measurement: MODELED,
                }
                .write(&mut nkn_conditions)?;
            }
            for probability in [0.0, 0.25, 0.5, 0.75, 1.0] {
                Row {
                    scenario: SENSITIVITY,
                    profile: NKN,
                    z,
                    hops,
                    path: Some(&nkn),
                    block: BlockEvidence::nkn(Some(probability)),
                    measurement: ASSUMED,
                    path_measurement: MODELED,
                }
                .write(&mut sensitivity)?;
            }
        }
    }
    native.flush()?;
    retention.flush()?;
    nkn_conditions.flush()?;
    ersoy_layouts.flush()?;
    sensitivity.flush()
}

fn main() -> io::Result<()> {
    generate(&Args::parse().output_dir)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ersoy_identifier_can_be_rebuilt_but_transaction_only_signature_cannot_bind_it() {
        use secp256k1::{Message, PublicKey, Secp256k1, SecretKey};
        use sha2::{Digest, Sha256};

        let secp = Secp256k1::new();
        let secret = |byte| SecretKey::from_byte_array(&[byte; 32]).unwrap();
        let key = |byte| PublicKey::from_secret_key(&secp, &secret(byte)).serialize();
        let hash = |prefix: &[u8], suffix: &[u8]| -> [u8; 32] {
            Sha256::new()
                .chain_update(prefix)
                .chain_update(suffix)
                .finalize()
                .into()
        };
        let leader_identifier = hash(b"published leader credential", &key(1));
        let relay_keys = [key(2), key(3), key(4)]; // Client-to-leader order.
                                                   // Recognition travels in the opposite direction to transaction relay.
        let received_identifier = hash(&hash(&hash(&leader_identifier, &key(4)), &key(3)), &key(2));
        let rebuilt = relay_keys
            .iter()
            .rev()
            .fold(leader_identifier, |id, public_key| hash(&id, public_key));
        assert_eq!(rebuilt, received_identifier);

        let tx = b"original transaction payload";
        let path_statement = Message::from_digest(hash(tx, &rebuilt));
        let ordinary_statement = Message::from_digest(Sha256::digest(tx).into());
        let client = secret(5);
        let client_key = PublicKey::from_secret_key(&secp, &client);
        let transaction_signature = secp.sign_ecdsa(&ordinary_statement, &client);
        let path_signature = secp.sign_ecdsa(&path_statement, &client);
        assert!(secp
            .verify_ecdsa(&path_statement, &transaction_signature, &client_key)
            .is_err());
        assert!(secp
            .verify_ecdsa(&path_statement, &path_signature, &client_key)
            .is_ok());
        assert_eq!(path_signature.serialize_compact().len(), 64);
        assert_eq!(ersoy_fields(4, 0).len(), 163);
        assert_eq!(ersoy_fields(4, 32).len(), 195);
    }

    #[test]
    fn trail_counts_only_raw_signature_and_indices() {
        for (hops, expected_bytes) in [(2, 64), (4, 80), (6, 96)] {
            let mut certificate = signed_trail_certificate(hops);
            let fields = trail_fields(&certificate);
            assert_eq!(fields.field_sizes(), [8 * hops, 48, 0, 0]);
            assert_eq!(fields.len(), expected_bytes);
            let encoded = certificate.to_compact_binary().unwrap();
            assert_eq!(encoded.len(), expected_bytes + 16 + 9);
            assert_eq!(
                fields.signatures,
                hex::decode(&certificate.signature[2..]).unwrap()
            );
            for (bytes, index) in fields.identities.chunks_exact(8).zip(&certificate.paths) {
                assert_eq!(bytes, index.to_le_bytes());
            }
            certificate.chain_id = 0xfeed;
            certificate.epoch = u64::MAX;
            assert_ne!(certificate.to_compact_binary().unwrap(), encoded);
            let changed = trail_fields(&certificate);
            assert_eq!(changed.signatures, fields.signatures);
            assert_eq!(changed.identities, fields.identities);
        }
    }

    #[test]
    fn txtail_selects_one_index_in_the_block() {
        let mut hash = [0; 32];
        hash[30..].copy_from_slice(&4167_u16.to_be_bytes());
        assert_eq!(txtail_sample(0, &hash), None);
        for z in TRANSACTION_COUNTS {
            assert_eq!(txtail_sample(z, &hash), Some(4167 % z));
            assert_eq!(txtail_sample(z, &hash).iter().count(), 1);
        }
    }

    #[test]
    fn nkn_adds_only_a_new_winner() {
        assert_eq!(nkn_disclosures(false, false), 0);
        assert_eq!(nkn_disclosures(true, true), 0);
        assert_eq!(nkn_disclosures(true, false), 1);
        assert_eq!(BlockEvidence::nkn(None).expected_count(), None);
        assert_eq!(BlockEvidence::nkn(Some(0.25)).expected_count(), Some(0.25));
    }
}
