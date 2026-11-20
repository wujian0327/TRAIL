use crate::blockchain::transaction::Transaction;
use crate::consensus::{ValidatorRegistry, ValidatorRegistryError};
use crate::wallet::Wallet;
use crate::{tools, wallet};
use blst::min_sig::PublicKey;
use dashmap::DashMap;
use hex::{decode, encode};
use lazy_static::lazy_static;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fmt;

#[cfg(feature = "bls-hop-latency")]
#[doc(hidden)]
pub mod latency_bench;

lazy_static! {
    static ref RECEIPT_COMMITMENT_CACHE: DashMap<String, String> = DashMap::new();
    static ref RECEIPT_CONFLICT_CACHE: DashMap<String, usize> = DashMap::new();
    static ref PATH_VERIFY_CACHE: DashMap<String, bool> = DashMap::new();
}

pub const DEFAULT_TRAIL_CHAIN_ID: u64 = 7_032_030;
pub const TRAIL_TX_PATH_DOMAIN: &[u8] = b"TRAIL_TX_PATH_V1";

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct Path {
    pub from: String,
    pub to: String,
    pub prefix: String,
    pub sender_signature: String,
    pub receiver_signature: Option<String>,
}

/// Revised propagation path used while transactions are in flight.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct TransactionPaths {
    pub transaction: Transaction,
    #[serde(default = "default_trail_chain_id")]
    pub chain_id: u64,
    pub epoch: u64,
    pub paths: Vec<Path>,
}

/// Revised block-level path record.
///
/// `paths` stores the non-proposer validator indices. The proposer is obtained
/// from the block header during verification.
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct AggregatedSignedPaths {
    #[serde(default = "default_trail_chain_id")]
    pub chain_id: u64,
    #[serde(default)]
    pub epoch: u64,
    pub signature: String,
    pub paths: Vec<u64>,
}

impl TransactionPaths {
    pub fn new(transaction: Transaction) -> TransactionPaths {
        Self::new_with_epoch(transaction, 0)
    }

    pub fn new_with_epoch(transaction: Transaction, epoch: u64) -> TransactionPaths {
        Self::new_with_epoch_and_chain_id(transaction, epoch, DEFAULT_TRAIL_CHAIN_ID)
    }

    pub fn new_with_epoch_and_chain_id(
        transaction: Transaction,
        epoch: u64,
        chain_id: u64,
    ) -> TransactionPaths {
        TransactionPaths {
            transaction,
            chain_id,
            epoch,
            paths: Vec::new(),
        }
    }

    pub fn new_with_paths(transaction: Transaction, paths: Vec<Path>) -> TransactionPaths {
        TransactionPaths {
            transaction,
            chain_id: DEFAULT_TRAIL_CHAIN_ID,
            epoch: 0,
            paths,
        }
    }

    pub fn add_path(&mut self, to: String, wallet: Wallet) {
        let _ = self.append_outgoing_hop(to, wallet);
    }

    pub fn append_outgoing_hop(&mut self, to: String, sender_wallet: Wallet) -> bool {
        if self.has_pending_hop() {
            return false;
        }
        let from = self.current_tail();
        if from != sender_wallet.address {
            return false;
        }
        if self.node_sequence().iter().any(|node| node == &to) {
            return false;
        }
        let prefix = self.chain_value_for_tail();
        let message = edge_statement(&prefix, &from, &to);
        let sender_signature = sender_wallet.sign_by_bls(message);
        self.paths.push(Path {
            from,
            to,
            prefix: encode(prefix),
            sender_signature,
            receiver_signature: None,
        });
        true
    }

    pub fn complete_pending_hop(&mut self, receiver_wallet: Wallet) -> bool {
        let Some(hop) = self.paths.last() else {
            return self.transaction.from == receiver_wallet.address;
        };
        if hop.to != receiver_wallet.address {
            return false;
        }
        if hop.receiver_signature.is_some() {
            return true;
        }
        if !self.verify_pending_sender_signature() {
            return false;
        }

        let prefix = match decode(&hop.prefix) {
            Ok(prefix) => prefix,
            Err(_) => return false,
        };
        let message = edge_statement(&prefix, &hop.from, &hop.to);
        let receipt_key =
            receipt_cache_key(&self.transaction.hash, self.chain_id, self.epoch, &hop.to);
        let receipt_commitment = encode(tools::Hasher::hash(message.clone()));
        if let Some(existing) = RECEIPT_COMMITMENT_CACHE.get(&receipt_key) {
            if existing.value() != &receipt_commitment {
                RECEIPT_CONFLICT_CACHE
                    .entry(receipt_key)
                    .and_modify(|count| *count += 1)
                    .or_insert(1);
                return false;
            }
        } else {
            RECEIPT_COMMITMENT_CACHE.insert(receipt_key, receipt_commitment);
        }

        let receiver_signature = receiver_wallet.sign_by_bls(message);
        if let Some(last) = self.paths.last_mut() {
            last.receiver_signature = Some(receiver_signature);
            true
        } else {
            false
        }
    }

    pub fn append_completed_hop(
        &mut self,
        to: String,
        sender_wallet: Wallet,
        receiver_wallet: Wallet,
    ) -> bool {
        self.append_outgoing_hop(to, sender_wallet) && self.complete_pending_hop(receiver_wallet)
    }

    pub fn verify(&self, current_address: String) -> bool {
        if !self.transaction.clone().verify() {
            return false;
        }
        if self.has_repeated_identities() {
            return false;
        }
        if self.paths.is_empty() {
            return current_address == self.transaction.from;
        }
        if self.current_tail() != current_address {
            return false;
        }
        self.verify_completed_hops()
    }

    pub fn verify_last(&self, current_address: String) -> bool {
        if !self.transaction.clone().verify() {
            return false;
        }
        let Some(hop) = self.paths.last() else {
            return current_address == self.transaction.from;
        };
        if hop.to != current_address {
            return false;
        }
        self.verify_pending_sender_signature()
    }

    /// Check an authentic incoming prefix without creating a receiving proof.
    /// Used only by the opt-in proposer candidate-collection experiment.
    pub fn verify_pending_prefix(&self, receiver: &str, epoch: u64, max_hops: usize) -> bool {
        if self.epoch != epoch
            || self.chain_id != DEFAULT_TRAIL_CHAIN_ID
            || self.paths.is_empty()
            || self.paths.len() > max_hops
            || self.current_tail() != receiver
            || self.has_repeated_identities()
            || !self.transaction.verify()
        {
            return false;
        }
        let last = self.paths.len() - 1;
        self.paths[last].receiver_signature.is_none()
            && (0..last).all(|idx| self.verify_hop(idx, true))
            && self.verify_hop(last, false)
    }

    pub fn verify_completed_hops(&self) -> bool {
        for (idx, hop) in self.paths.iter().enumerate() {
            if hop.receiver_signature.is_none() {
                return false;
            }
            if !self.verify_hop(idx, true) {
                return false;
            }
        }
        true
    }

    pub fn verify_pending_sender_signature(&self) -> bool {
        let Some(idx) = self.paths.len().checked_sub(1) else {
            return self.transaction.verify();
        };
        self.verify_hop(idx, false)
    }

    fn verify_hop(&self, idx: usize, require_receiver: bool) -> bool {
        let Some(hop) = self.paths.get(idx) else {
            return false;
        };
        let expected_prefix = self.chain_value_for_node_index(idx);
        if encode(&expected_prefix) != hop.prefix {
            return false;
        }
        let nodes = self.node_sequence();
        if nodes.get(idx) != Some(&hop.from) || nodes.get(idx + 1) != Some(&hop.to) {
            return false;
        }
        let message = edge_statement(&expected_prefix, &hop.from, &hop.to);
        let Some(sender_pk) = wallet::get_bls_pub_key(hop.from.clone()) else {
            return false;
        };
        if !Wallet::verify_bls_with_pk(message.clone(), hop.sender_signature.clone(), sender_pk) {
            return false;
        }
        if require_receiver {
            let Some(receiver_signature) = &hop.receiver_signature else {
                return false;
            };
            let Some(receiver_pk) = wallet::get_bls_pub_key(hop.to.clone()) else {
                return false;
            };
            if !Wallet::verify_bls_with_pk(message, receiver_signature.clone(), receiver_pk) {
                return false;
            }
        }
        true
    }

    pub fn to_aggregated_signed_paths(
        &self,
        registry: &ValidatorRegistry,
    ) -> Result<AggregatedSignedPaths, ValidatorRegistryError> {
        AggregatedSignedPaths::from_transaction_paths(self, registry)
    }

    pub fn from_json(json: Vec<u8>) -> Result<TransactionPaths, PathError> {
        let transaction_paths: TransactionPaths = serde_json::from_slice(json.as_slice())?;
        Ok(transaction_paths)
    }

    pub fn to_json(&self) -> Vec<u8> {
        serde_json::to_vec(&self).unwrap()
    }

    pub fn to_paths_string(&self) -> String {
        self.node_sequence()
            .iter()
            .map(|x| x[0..5.min(x.len())].to_string())
            .collect::<Vec<String>>()
            .join("->")
    }

    pub fn node_sequence(&self) -> Vec<String> {
        let mut nodes = vec![self.transaction.from.clone()];
        nodes.extend(self.paths.iter().map(|hop| hop.to.clone()));
        nodes
    }

    pub fn has_pending_hop(&self) -> bool {
        self.paths
            .last()
            .map(|hop| hop.receiver_signature.is_none())
            .unwrap_or(false)
    }

    pub fn has_repeated_identities(&self) -> bool {
        let mut seen = HashSet::new();
        self.node_sequence()
            .iter()
            .any(|node| !seen.insert(node.clone()))
    }

    fn current_tail(&self) -> String {
        self.paths
            .last()
            .map(|hop| hop.to.clone())
            .unwrap_or_else(|| self.transaction.from.clone())
    }

    fn chain_value_for_tail(&self) -> Vec<u8> {
        let tail_idx = self.paths.len();
        self.chain_value_for_node_index(tail_idx)
    }

    fn chain_value_for_node_index(&self, node_idx: usize) -> Vec<u8> {
        let nodes = self.node_sequence();
        chain_value_for_nodes(
            &self.transaction.hash,
            self.chain_id,
            self.epoch,
            &nodes,
            node_idx,
        )
    }
}

pub fn clear_receipt_cache_for_tests() {
    RECEIPT_COMMITMENT_CACHE.clear();
    RECEIPT_CONFLICT_CACHE.clear();
    PATH_VERIFY_CACHE.clear();
}

pub fn conflicting_receipt_count(tx_hash: &str, epoch: u64, receiver: &str) -> usize {
    RECEIPT_CONFLICT_CACHE
        .get(&receipt_cache_key(
            tx_hash,
            DEFAULT_TRAIL_CHAIN_ID,
            epoch,
            receiver,
        ))
        .map(|entry| *entry.value())
        .unwrap_or(0)
}

impl AggregatedSignedPaths {
    pub fn from_transaction_paths(
        paths: &TransactionPaths,
        registry: &ValidatorRegistry,
    ) -> Result<Self, ValidatorRegistryError> {
        let nodes = paths.node_sequence();
        let (proposer, non_proposers) = nodes
            .split_last()
            .expect("path includes transaction origin");
        registry.index(proposer)?;

        // Invalid hop evidence has an empty signature, preserving transaction
        // inclusion and the existing handling of ineligible relay evidence.
        let mut signature = String::new();
        if paths.verify_completed_hops() {
            let signatures = paths
                .paths
                .iter()
                .flat_map(|hop| {
                    std::iter::once(&hop.sender_signature).chain(hop.receiver_signature.iter())
                })
                .map(|signature| Wallet::bls_signature_from_string(signature.clone()))
                .collect::<Result<Vec<_>, _>>();
            if let Ok(signatures) = signatures {
                signature = Wallet::bls_aggregated_sign(signatures);
            }
        }

        Ok(Self {
            chain_id: paths.chain_id,
            epoch: paths.epoch,
            signature,
            paths: non_proposers
                .iter()
                .map(|address| registry.index(address))
                .collect::<Result<_, _>>()?,
        })
    }

    pub(crate) fn empty(chain_id: u64, epoch: u64) -> Self {
        Self {
            chain_id,
            epoch,
            signature: String::new(),
            paths: Vec::new(),
        }
    }

    pub fn verify_at_epoch(
        &self,
        transaction: &Transaction,
        miner: &str,
        epoch: u64,
        registry: &ValidatorRegistry,
    ) -> bool {
        self.verify_at_epoch_with_cache(transaction, miner, epoch, registry, true)
    }

    /// Verify a path record without consulting or populating the process-local cache.
    pub fn verify_at_epoch_uncached(
        &self,
        transaction: &Transaction,
        miner: &str,
        epoch: u64,
        registry: &ValidatorRegistry,
    ) -> bool {
        self.verify_at_epoch_with_cache(transaction, miner, epoch, registry, false)
    }

    fn verify_at_epoch_with_cache(
        &self,
        transaction: &Transaction,
        miner: &str,
        epoch: u64,
        registry: &ValidatorRegistry,
        use_cache: bool,
    ) -> bool {
        if !self.matches_context(epoch) || !transaction.verify() {
            return false;
        }
        self.verify_evidence(transaction, miner, registry, use_cache)
    }

    fn matches_context(&self, epoch: u64) -> bool {
        self.epoch == epoch && self.chain_id == DEFAULT_TRAIL_CHAIN_ID
    }

    fn verify_evidence(
        &self,
        transaction: &Transaction,
        miner: &str,
        registry: &ValidatorRegistry,
        use_cache: bool,
    ) -> bool {
        if self.paths.is_empty() {
            return transaction.from == miner
                && self.signature.is_empty()
                && registry.index(miner).is_ok();
        }
        // Each stored index and the header proposer must name a distinct
        // validator. Consensus also applies its configured hop limit.
        if self.signature.is_empty() || self.paths.len() >= registry.len() {
            return false;
        }

        let Ok(full_nodes) = self.full_path(miner, registry) else {
            return false;
        };
        if full_nodes.first() != Some(&transaction.from) || has_repeated_nodes(&full_nodes) {
            return false;
        }
        let cache_key = use_cache.then(|| {
            format!(
                "{}|{}|{}|{}|{}|{}|{:?}",
                encode(registry.fingerprint()),
                transaction.hash,
                self.chain_id,
                self.epoch,
                miner,
                self.signature,
                self.paths
            )
        });
        if let Some(key) = &cache_key {
            if let Some(cached) = PATH_VERIFY_CACHE.get(key) {
                return *cached.value();
            }
        }
        let Ok(signature) = Wallet::bls_signature_from_string(self.signature.clone()) else {
            return false;
        };
        let Ok((messages, pks)) = self.signer_messages(transaction, &full_nodes, registry) else {
            return false;
        };
        let valid = Wallet::bls_aggregated_verify_parsed(messages, pks, signature);
        if let Some(key) = cache_key {
            PATH_VERIFY_CACHE.insert(key, valid);
        }
        valid
    }

    fn signer_messages(
        &self,
        transaction: &Transaction,
        full_nodes: &[String],
        registry: &ValidatorRegistry,
    ) -> Result<(Vec<Vec<u8>>, Vec<PublicKey>), ValidatorRegistryError> {
        let hop_count = self.paths.len();
        let mut messages: Vec<Vec<u8>> = Vec::with_capacity(hop_count * 2);
        let mut pks: Vec<PublicKey> = Vec::with_capacity(hop_count * 2);
        for (idx, nodes) in full_nodes.windows(2).enumerate() {
            let prefix = chain_value_for_nodes(
                &transaction.hash,
                self.chain_id,
                self.epoch,
                full_nodes,
                idx,
            );
            let message = edge_statement(&prefix, &nodes[0], &nodes[1]);
            let sender_pk = registry.public_key(&nodes[0])?;
            let receiver_pk = registry.public_key(&nodes[1])?;
            messages.push(message.clone());
            pks.push(sender_pk);
            messages.push(message);
            pks.push(receiver_pk);
        }

        Ok((messages, pks))
    }

    pub fn full_path(
        &self,
        miner: &str,
        registry: &ValidatorRegistry,
    ) -> Result<Vec<String>, ValidatorRegistryError> {
        registry.index(miner)?;
        let mut nodes = self
            .paths
            .iter()
            .map(|&index| registry.address(index).map(str::to_owned))
            .collect::<Result<Vec<_>, _>>()?;
        nodes.push(miner.to_owned());
        Ok(nodes)
    }

    /// Core size for a well-formed certificate. Zero-hop and ineligible
    /// records with an empty signature carry no BLS bytes.
    pub fn bytes(&self) -> u64 {
        let signature_bytes = decode(self.signature.trim_start_matches("0x"))
            .map_or(0, |signature| signature.len() as u64);
        signature_bytes + 8 * self.paths.len() as u64
    }

    /// Chain ID (u64), epoch (u64), signature byte length (u8), raw signature,
    /// index count (u64), and fixed-width indices (u64), all little-endian.
    pub fn to_compact_binary(&self) -> Result<Vec<u8>, PathError> {
        let signature = decode(self.signature.strip_prefix("0x").unwrap_or(&self.signature))
            .map_err(|_| PathError::InvalidBinaryEncoding)?;
        if !matches!(signature.len(), 0 | 48) {
            return Err(PathError::InvalidBinaryEncoding);
        }
        let mut bytes = Vec::with_capacity(25 + signature.len() + self.paths.len() * 8);
        bytes.extend_from_slice(&self.chain_id.to_le_bytes());
        bytes.extend_from_slice(&self.epoch.to_le_bytes());
        bytes.push(signature.len() as u8);
        bytes.extend_from_slice(&signature);
        bytes.extend_from_slice(&(self.paths.len() as u64).to_le_bytes());
        for index in &self.paths {
            bytes.extend_from_slice(&index.to_le_bytes());
        }
        Ok(bytes)
    }

    pub fn from_compact_binary(bytes: &[u8]) -> Result<Self, PathError> {
        if bytes.len() < 25 || !matches!(bytes[16], 0 | 48) {
            return Err(PathError::InvalidBinaryEncoding);
        }
        let signature_end = 17 + bytes[16] as usize;
        let count_end = signature_end + 8;
        if bytes.len() < count_end {
            return Err(PathError::InvalidBinaryEncoding);
        }
        let read_u64 = |field: &[u8]| u64::from_le_bytes(field.try_into().unwrap());
        let indices = &bytes[count_end..];
        let count = read_u64(&bytes[signature_end..count_end]);
        if indices.len() % 8 != 0 || count != (indices.len() / 8) as u64 {
            return Err(PathError::InvalidBinaryEncoding);
        }
        let signature = if signature_end == 17 {
            String::new()
        } else {
            format!("0x{}", encode(&bytes[17..signature_end]))
        };
        Ok(Self {
            chain_id: read_u64(&bytes[..8]),
            epoch: read_u64(&bytes[8..16]),
            signature,
            paths: indices.chunks_exact(8).map(read_u64).collect(),
        })
    }

    pub fn to_json(&self) -> Vec<u8> {
        serde_json::to_vec(&self).unwrap()
    }

    pub fn from_json(json: Vec<u8>) -> AggregatedSignedPaths {
        serde_json::from_slice(json.as_slice()).unwrap()
    }

    pub fn json_bytes(&self) -> u64 {
        self.to_json().len() as u64
    }

    pub fn compress(&self) -> Vec<u8> {
        zstd::stream::encode_all(self.to_json().as_slice(), 22).unwrap()
    }

    pub fn decompress(data: Vec<u8>) -> AggregatedSignedPaths {
        let data = zstd::stream::decode_all(data.as_slice()).unwrap();
        AggregatedSignedPaths::from_json(data)
    }
}

fn chain_value_for_nodes(
    tx_hash: &str,
    chain_id: u64,
    epoch: u64,
    nodes: &[String],
    node_idx: usize,
) -> Vec<u8> {
    let tx_hash_bytes = decode(tx_hash)
        .unwrap_or_else(|_| tools::Hasher::hash(tx_hash.as_bytes().to_vec()).to_vec());
    if nodes.is_empty() {
        let mut data = TRAIL_TX_PATH_DOMAIN.to_vec();
        data.extend_from_slice(&chain_id.to_be_bytes());
        data.extend_from_slice(&epoch.to_be_bytes());
        data.extend_from_slice(&tx_hash_bytes);
        return tools::Hasher::hash(data).to_vec();
    }
    let capped_idx = node_idx.min(nodes.len() - 1);
    let mut initial = TRAIL_TX_PATH_DOMAIN.to_vec();
    initial.extend_from_slice(&chain_id.to_be_bytes());
    initial.extend_from_slice(&tx_hash_bytes);
    initial.extend_from_slice(&epoch.to_be_bytes());
    initial.extend_from_slice(&hash_identity(&nodes[0]));
    let mut chain = tools::Hasher::hash(initial).to_vec();
    for node in nodes.iter().take(capped_idx + 1).skip(1) {
        let mut data = chain;
        data.extend_from_slice(&hash_identity(node));
        chain = tools::Hasher::hash(data).to_vec();
    }
    chain
}

fn edge_statement(prefix: &[u8], from: &str, to: &str) -> Vec<u8> {
    let mut message = TRAIL_TX_PATH_DOMAIN.to_vec();
    message.extend_from_slice(prefix);
    message.extend_from_slice(&hash_identity(from));
    message.extend_from_slice(&hash_identity(to));
    message
}

fn hash_identity(identity: &str) -> Vec<u8> {
    tools::Hasher::hash(identity.as_bytes().to_vec()).to_vec()
}

fn receipt_cache_key(tx_hash: &str, chain_id: u64, epoch: u64, receiver: &str) -> String {
    format!("{}:{}:{}:{}", tx_hash, chain_id, epoch, receiver)
}

fn has_repeated_nodes(nodes: &[String]) -> bool {
    let mut seen = HashSet::new();
    nodes.iter().any(|node| !seen.insert(node))
}

fn default_trail_chain_id() -> u64 {
    DEFAULT_TRAIL_CHAIN_ID
}

#[derive(Debug)]
pub enum PathError {
    JSONError,
    InvalidBinaryEncoding,
}

impl fmt::Display for PathError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match *self {
            PathError::JSONError => {
                write!(f, "Invalid Json Error")
            }
            PathError::InvalidBinaryEncoding => {
                write!(f, "invalid path certificate binary encoding")
            }
        }
    }
}

impl From<serde_json::error::Error> for PathError {
    fn from(_: serde_json::error::Error) -> Self {
        PathError::JSONError
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wallet;

    fn build_valid_path(
        epoch: u64,
    ) -> (
        Transaction,
        TransactionPaths,
        Wallet,
        Wallet,
        Wallet,
        Wallet,
        ValidatorRegistry,
    ) {
        let origin = Wallet::new();
        let relay1 = Wallet::new();
        let relay2 = Wallet::new();
        let miner = Wallet::new();
        let transaction = Transaction::new("123".to_string(), 32, origin.clone());
        let mut transaction_paths = TransactionPaths::new_with_epoch(transaction.clone(), epoch);
        assert!(transaction_paths.append_outgoing_hop(relay1.address.clone(), origin.clone()));
        assert!(transaction_paths.complete_pending_hop(relay1.clone()));
        assert!(transaction_paths.append_outgoing_hop(relay2.address.clone(), relay1.clone()));
        assert!(transaction_paths.complete_pending_hop(relay2.clone()));
        assert!(transaction_paths.append_outgoing_hop(miner.address.clone(), relay2.clone()));
        assert!(transaction_paths.complete_pending_hop(miner.clone()));
        let registry =
            ValidatorRegistry::from_wallets([&origin, &relay1, &relay2, &miner]).unwrap();
        (
            transaction,
            transaction_paths,
            origin,
            relay1,
            relay2,
            miner,
            registry,
        )
    }

    #[test]
    fn valid_two_sided_path() {
        let (transaction, transaction_paths, _origin, _relay1, _relay2, miner, registry) =
            build_valid_path(7);
        assert!(transaction_paths.verify(miner.address.clone()));
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert!(aggregated.verify_at_epoch(&transaction, &miner.address, 7, &registry));
    }

    #[test]
    fn indexed_paths_round_trip_and_verify_at_supported_lengths() {
        for hops in [2, 4, 8, 16] {
            let wallets: Vec<_> = (0..=hops)
                .map(|index| Wallet::new_deterministic(2400 + hops as u64, index))
                .collect();
            let registry = ValidatorRegistry::from_wallets(wallets.iter().rev()).unwrap();
            let miner = &wallets.last().unwrap().address;
            let tx = Transaction::new(miner.clone(), 32, wallets[0].clone());
            let mut path = TransactionPaths::new_with_epoch(tx.clone(), 7);
            for pair in wallets.windows(2) {
                assert!(path.append_completed_hop(
                    pair[1].address.clone(),
                    pair[0].clone(),
                    pair[1].clone(),
                ));
            }

            let certificate = path.to_aggregated_signed_paths(&registry).unwrap();
            assert_eq!(certificate.paths.len(), hops as usize);
            assert_eq!(
                certificate.full_path(miner, &registry).unwrap(),
                path.node_sequence()
            );
            assert_eq!(certificate.bytes(), 48 + 8 * hops as u64);
            let binary = certificate.to_compact_binary().unwrap();
            assert_eq!(binary.len() as u64, certificate.bytes() + 25);
            let restored = AggregatedSignedPaths::from_compact_binary(&binary).unwrap();
            assert_eq!(restored.to_json(), certificate.to_json());
            assert!(restored.verify_at_epoch(&tx, miner, 7, &registry));
            assert!(restored.verify_at_epoch_uncached(&tx, miner, 7, &registry));
            assert!(
                AggregatedSignedPaths::from_compact_binary(&binary[..binary.len() - 1]).is_err()
            );
            let mut incorrect_count = binary;
            incorrect_count[65..73].copy_from_slice(&u64::MAX.to_le_bytes());
            assert!(AggregatedSignedPaths::from_compact_binary(&incorrect_count).is_err());
        }
    }

    #[test]
    fn rejects_unknown_indices_and_proposers_and_changed_indices() {
        let (tx, path, origin, relay1, relay2, miner, registry) = build_valid_path(7);
        let mut certificate = path.to_aggregated_signed_paths(&registry).unwrap();
        assert!(!certificate.verify_at_epoch(&tx, &relay2.address, 7, &registry));

        let spare = Wallet::new();
        let extended =
            ValidatorRegistry::from_wallets([&origin, &relay1, &relay2, &miner, &spare]).unwrap();
        // With the proposer already in the sequence, appending the same
        // identity from the header must also fail.
        let mut repeated_proposer = path.to_aggregated_signed_paths(&extended).unwrap();
        repeated_proposer
            .paths
            .push(extended.index(&miner.address).unwrap());
        assert!(!repeated_proposer.verify_at_epoch(&tx, &miner.address, 7, &extended));

        let mut changed = path.to_aggregated_signed_paths(&extended).unwrap();
        changed.paths[1] = extended.index(&spare.address).unwrap();
        assert!(!changed.verify_at_epoch(&tx, &miner.address, 7, &extended));

        certificate.paths[1] = u64::MAX;
        assert_eq!(
            certificate.full_path(&miner.address, &registry),
            Err(ValidatorRegistryError::UnknownIndex(u64::MAX))
        );
        assert!(!certificate.verify_at_epoch(&tx, &miner.address, 7, &registry));

        let incomplete = ValidatorRegistry::from_wallets([&origin, &relay2, &miner]).unwrap();
        assert!(
            matches!(path.to_aggregated_signed_paths(&incomplete), Err(ValidatorRegistryError::UnknownAddress(address)) if address == relay1.address)
        );
    }

    #[test]
    fn cached_verification_is_bound_to_the_registry_keys() {
        let (tx, path, origin, relay1, relay2, miner, registry) = build_valid_path(6);
        let certificate = path.to_aggregated_signed_paths(&registry).unwrap();
        let wrong_keys = ValidatorRegistry::new([
            (origin.address.clone(), origin.bls_public_key),
            (relay1.address.clone(), relay2.bls_public_key),
            (relay2.address.clone(), relay2.bls_public_key),
            (miner.address.clone(), miner.bls_public_key),
        ])
        .unwrap();
        assert_eq!(
            certificate.full_path(&miner.address, &wrong_keys).unwrap(),
            path.node_sequence()
        );
        assert!(certificate.verify_at_epoch(&tx, &miner.address, 6, &registry));
        assert!(!certificate.verify_at_epoch(&tx, &miner.address, 6, &wrong_keys));
        assert!(!certificate.verify_at_epoch_uncached(&tx, &miner.address, 6, &wrong_keys));
        assert!(certificate.verify_at_epoch(&tx, &miner.address, 6, &registry));
    }

    #[test]
    fn individual_hop_checks_reject_canceling_signature_errors() {
        let (tx, mut path, _, _, _, miner, registry) = build_valid_path(7);
        let valid = path.to_aggregated_signed_paths(&registry).unwrap();

        // Swapping the two signers' proofs leaves their BLS sum unchanged.
        // Each proof must still match its own registered key.
        let hop = &mut path.paths[0];
        std::mem::swap(
            &mut hop.sender_signature,
            hop.receiver_signature.as_mut().unwrap(),
        );
        assert!(!path.verify_completed_hops());
        let signatures = path
            .paths
            .iter()
            .flat_map(|hop| {
                [
                    Wallet::bls_signature_from_string(hop.sender_signature.clone()).unwrap(),
                    Wallet::bls_signature_from_string(hop.receiver_signature.clone().unwrap())
                        .unwrap(),
                ]
            })
            .collect();
        assert_eq!(Wallet::bls_aggregated_sign(signatures), valid.signature);
        let rejected = path.to_aggregated_signed_paths(&registry).unwrap();
        assert!(rejected.signature.is_empty());
        assert!(!rejected.verify_at_epoch(&tx, &miner.address, 7, &registry));
    }

    #[test]
    fn missing_receiver_signature_fails() {
        let origin = Wallet::new();
        let relay = Wallet::new();
        let registry = ValidatorRegistry::from_wallets([&origin, &relay]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, origin.clone());
        let mut transaction_paths = TransactionPaths::new_with_epoch(transaction.clone(), 1);
        assert!(transaction_paths.append_outgoing_hop(relay.address.clone(), origin));
        assert!(!transaction_paths.verify(relay.address.clone()));
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert!(!aggregated.verify_at_epoch(&transaction, &relay.address, 1, &registry));
    }

    #[test]
    fn modified_transaction_fails() {
        let (mut transaction, transaction_paths, origin, _relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        transaction.amount += 1;
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 1, &registry));
        let different_signed_transaction = Transaction::new("123".to_string(), 33, origin);
        assert!(different_signed_transaction.verify());
        assert!(!aggregated.verify_at_epoch(
            &different_signed_transaction,
            &miner.address,
            1,
            &registry
        ));
    }

    #[test]
    fn modified_path_epoch_fails() {
        let (transaction, transaction_paths, _origin, _relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let mut aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        aggregated.epoch = 2;
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 1, &registry));
    }

    #[test]
    fn replayed_path_in_different_epoch_fails() {
        let (transaction, transaction_paths, _origin, _relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 2, &registry));
    }

    #[test]
    fn modified_chain_id_fails() {
        let (transaction, transaction_paths, _origin, _relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let mut aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        aggregated.chain_id += 1;
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 1, &registry));
    }

    #[test]
    fn reordered_node_fails() {
        let (transaction, transaction_paths, _origin, _relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let mut aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        aggregated.paths.swap(1, 2);
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 1, &registry));
    }

    #[test]
    fn removed_node_fails() {
        let (transaction, transaction_paths, _origin, _relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let mut aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        aggregated.paths.remove(1);
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 1, &registry));
    }

    #[test]
    fn repeated_identity_fails() {
        let (transaction, transaction_paths, _origin, relay1, _relay2, miner, registry) =
            build_valid_path(1);
        let mut aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        aggregated.paths[2] = registry.index(&relay1.address).unwrap();
        assert!(!aggregated.verify_at_epoch(&transaction, &miner.address, 1, &registry));
    }

    #[test]
    fn conflicting_receipts_are_detected() {
        let origin = Wallet::new();
        let other_origin = Wallet::new();
        let receiver = Wallet::new();
        let transaction = Transaction::new("123".to_string(), 32, origin.clone());

        let mut first = TransactionPaths::new_with_epoch(transaction.clone(), 9);
        assert!(first.append_outgoing_hop(receiver.address.clone(), origin));
        assert!(first.complete_pending_hop(receiver.clone()));

        let mut second = TransactionPaths::new_with_epoch(transaction.clone(), 9);
        second.transaction.from = other_origin.address.clone();
        assert!(second.append_outgoing_hop(receiver.address.clone(), other_origin));
        assert!(!second.complete_pending_hop(receiver.clone()));
        assert_eq!(
            conflicting_receipt_count(&transaction.hash, 9, &receiver.address),
            1
        );
    }

    #[test]
    fn aggregate_verification_accepts_repeated_messages_with_different_keys() {
        let origin = Wallet::new();
        let relay = Wallet::new();
        let registry = ValidatorRegistry::from_wallets([&origin, &relay]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, origin.clone());
        let mut transaction_paths = TransactionPaths::new_with_epoch(transaction.clone(), 5);
        assert!(transaction_paths.append_outgoing_hop(relay.address.clone(), origin));
        assert!(transaction_paths.complete_pending_hop(relay.clone()));
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert!(aggregated.verify_at_epoch(&transaction, &relay.address, 5, &registry));
    }

    #[test]
    fn malformed_aggregate_signature_is_rejected_without_panicking() {
        let origin = Wallet::new_deterministic(9001, 0);
        let relay = Wallet::new_deterministic(9001, 1);
        let registry = ValidatorRegistry::from_wallets([&origin, &relay]).unwrap();
        let transaction =
            Transaction::with_costs(relay.address.clone(), 1, 1.0, 1.0, origin.clone());
        let mut paths = TransactionPaths::new_with_epoch(transaction.clone(), 3);
        assert!(paths.append_completed_hop(relay.address.clone(), origin, relay.clone(),));
        let mut aggregate = paths.to_aggregated_signed_paths(&registry).unwrap();
        aggregate.signature = "0x00".to_string();
        assert!(!aggregate.verify_at_epoch_uncached(&transaction, &relay.address, 3, &registry));
    }

    #[test]
    fn cached_and_uncached_aggregate_verification_agree() {
        let origin = Wallet::new_deterministic(9002, 0);
        let relay = Wallet::new_deterministic(9002, 1);
        let registry = ValidatorRegistry::from_wallets([&origin, &relay]).unwrap();
        let transaction =
            Transaction::with_costs(relay.address.clone(), 1, 1.0, 1.0, origin.clone());
        let mut paths = TransactionPaths::new_with_epoch(transaction.clone(), 4);
        assert!(paths.append_completed_hop(relay.address.clone(), origin, relay.clone(),));
        let aggregate = paths.to_aggregated_signed_paths(&registry).unwrap();
        assert!(aggregate.verify_at_epoch(&transaction, &relay.address, 4, &registry));
        assert!(aggregate.verify_at_epoch_uncached(&transaction, &relay.address, 4, &registry));
    }

    #[test]
    fn direct_originator_to_proposer_path() {
        let origin = Wallet::new();
        let miner = Wallet::new();
        let registry = ValidatorRegistry::from_wallets([&origin, &miner]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, origin.clone());
        let mut transaction_paths = TransactionPaths::new_with_epoch(transaction.clone(), 4);
        assert!(transaction_paths.append_outgoing_hop(miner.address.clone(), origin));
        assert!(transaction_paths.complete_pending_hop(miner.clone()));
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert_eq!(
            aggregated.paths,
            vec![registry.index(&transaction.from).unwrap()]
        );
        assert!(aggregated.verify_at_epoch(&transaction, &miner.address, 4, &registry));
    }

    #[test]
    fn proposer_generated_transaction_has_empty_path() {
        let miner = Wallet::new();
        let registry = ValidatorRegistry::from_wallets([&miner]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, miner.clone());
        let transaction_paths = TransactionPaths::new_with_epoch(transaction.clone(), 4);
        let aggregated = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert!(aggregated.paths.is_empty());
        assert!(aggregated.signature.is_empty());
        assert!(aggregated.verify_at_epoch(&transaction, &miner.address, 4, &registry));
        assert!(!aggregated.verify_at_epoch(
            &transaction,
            &miner.address,
            4,
            &ValidatorRegistry::default()
        ));
        assert_eq!(aggregated.bytes(), 0);
        assert_eq!(aggregated.to_compact_binary().unwrap().len(), 25);
        let mut malformed = aggregated;
        malformed.chain_id += 1;
        assert!(!malformed.verify_at_epoch(&transaction, &miner.address, 4, &registry));
    }

    #[test]
    fn test_transaction_paths_bls() {
        let wallet = Wallet::new();
        let wallet2 = Wallet::new();
        let wallet3 = Wallet::new();
        let miner = Wallet::new();
        let registry =
            ValidatorRegistry::from_wallets([&wallet, &wallet2, &wallet3, &miner]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, wallet.clone());
        let mut transaction_paths = TransactionPaths::new(transaction.clone());
        assert!(transaction_paths.append_outgoing_hop(wallet2.address.clone(), wallet.clone()));
        assert!(transaction_paths.complete_pending_hop(wallet2.clone()));
        assert!(transaction_paths.append_outgoing_hop(wallet3.address.clone(), wallet2.clone()));
        assert!(transaction_paths.complete_pending_hop(wallet3.clone()));
        assert!(transaction_paths.append_outgoing_hop(miner.address.clone(), wallet3.clone()));
        assert!(transaction_paths.complete_pending_hop(miner.clone()));
        wallet::insert_bls_pub_key(wallet.address.clone(), wallet.bls_public_key);
        wallet::insert_bls_pub_key(wallet2.address.clone(), wallet2.bls_public_key);
        wallet::insert_bls_pub_key(wallet3.address.clone(), wallet3.bls_public_key);
        wallet::insert_bls_pub_key(miner.address.clone(), miner.bls_public_key);
        assert!(transaction_paths.verify(miner.address.clone()));

        let aggregated_signed_paths = transaction_paths
            .to_aggregated_signed_paths(&registry)
            .unwrap();
        assert!(aggregated_signed_paths.verify_at_epoch(
            &transaction,
            &miner.address,
            0,
            &registry
        ));
        assert_eq!(
            aggregated_signed_paths.paths.last(),
            Some(&registry.index(&wallet3.address).unwrap())
        );
    }
}
