use crate::blockchain::path::{AggregatedSignedPaths, DEFAULT_TRAIL_CHAIN_ID};
use crate::blockchain::transaction::Transaction;
use crate::consensus::{ValidatorRegistry, ValidatorRegistryError};
use crate::tools;
use crate::wallet::Wallet;
use hex::{decode, encode};
use log::{error, info};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fmt;
#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Block {
    pub header: Header,
    pub body: Body,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Header {
    pub index: u64,
    pub epoch: u64,
    pub slot: u64,
    pub hash: String,
    pub parent_hash: String,
    pub timestamp: u64,
    pub merkle_root: String,
    pub path_root: String,
    pub miner: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct Body {
    pub transactions: Vec<Transaction>,
    pub paths: Vec<AggregatedSignedPaths>,
}

impl Header {
    pub fn new(
        index: u64,
        epoch: u64,
        slot: u64,
        merkle_root: String,
        path_root: String,
        miner: String,
        parent_hash: String,
    ) -> Header {
        let mut header = Header {
            index,
            epoch,
            slot,
            hash: "".to_string(),
            parent_hash,
            timestamp: tools::get_timestamp(),
            merkle_root,
            path_root,
            miner,
        };
        header.hash = header.get_hash();
        header
    }

    pub fn get_hash(&self) -> String {
        let mut header = self.clone();
        header.hash = "".to_string();
        let t_json = serde_json::to_string(&header).unwrap();
        let hash = tools::Hasher::hash(t_json.as_bytes().to_vec());
        encode(hash)
    }

    pub fn bytes(&self) -> u64 {
        let index = 8;
        let epoch = 8;
        let slot = 8;
        let timestamp = 8;
        let hash = self.hash.as_bytes().len() as u64;
        let parent_hash = self.parent_hash.as_bytes().len() as u64;
        let merkle_root = self.merkle_root.as_bytes().len() as u64;
        let path_root = self.path_root.as_bytes().len() as u64;
        let miner = self.miner.as_bytes().len() as u64;
        index + epoch + slot + timestamp + hash + parent_hash + merkle_root + path_root + miner
    }
}

impl Block {
    pub fn new(
        index: u64,
        epoch: u64,
        slot: u64,
        parent_hash: String,
        body: Body,
        wallet: Wallet,
    ) -> Result<Block, BlockError> {
        if body.paths.len() > body.transactions.len() {
            return Err(BlockError::InvalidBlock);
        }
        for transaction in body.transactions.iter() {
            if !transaction.verify() {
                return Err(BlockError::InvalidBlockTransactions);
            }
        }
        let hash_vec = body.transactions.iter().map(|t| t.hash.clone()).collect();
        let merkle_root = Block::cal_merkle_root(hash_vec);
        let path_root = Block::cal_path_root(&body.paths);
        let header = Header::new(
            index,
            epoch,
            slot,
            merkle_root,
            path_root,
            wallet.address,
            parent_hash,
        );
        Ok(Block { header, body })
    }

    pub fn verify(&self) -> bool {
        self.verify_base()
    }

    pub fn verify_base(&self) -> bool {
        if self.body.paths.len() > self.body.transactions.len() {
            error!("{}", BlockError::InvalidBlock);
            return false;
        }
        for transaction in self.body.transactions.iter() {
            if !transaction.verify() {
                error!("{}", BlockError::InvalidBlockTransactions);
                return false;
            }
        }
        let tx_root = Block::cal_merkle_root(
            self.body
                .transactions
                .iter()
                .map(|tx| tx.hash.clone())
                .collect(),
        );
        if tx_root != self.header.merkle_root {
            error!("{}", BlockError::InvalidBlockTransactions);
            return false;
        }
        let path_root = Block::cal_path_root(&self.body.paths);
        if path_root != self.header.path_root {
            error!("{}", BlockError::InvalidBlockPath);
            return false;
        }
        if self.header.hash != self.header.get_hash() {
            error!("{}", BlockError::InvalidBlock);
            return false;
        }
        true
    }

    pub fn verify_path_evidence(
        &self,
        transaction_index: usize,
        registry: &ValidatorRegistry,
    ) -> bool {
        let Some(transaction) = self.body.transactions.get(transaction_index) else {
            return false;
        };
        let Some(path) = self.body.paths.get(transaction_index) else {
            return false;
        };
        // The synthetic genesis proposer is outside the validator roster.
        if self.header.index == 0 && path.paths.is_empty() {
            return path.signature.is_empty()
                && path.epoch == self.header.epoch
                && path.chain_id == DEFAULT_TRAIL_CHAIN_ID
                && transaction.from == self.header.miner
                && transaction.verify();
        }
        path.verify_at_epoch(transaction, &self.header.miner, self.header.epoch, registry)
    }

    pub fn cal_merkle_root(mut leaves: Vec<String>) -> String {
        // 使用迭代替代递归，避免深度递归导致栈溢出
        while leaves.len() > 1 {
            if leaves.len() % 2 != 0 {
                leaves.push(leaves.last().unwrap().clone());
            }

            let mut next_level = Vec::new();
            for pair in leaves.chunks(2) {
                let mut combined = decode(pair[0].clone()).unwrap();
                combined.append(&mut decode(pair[1].clone()).unwrap());
                let hash = encode(tools::Hasher::hash(combined));
                next_level.push(hash);
            }
            leaves = next_level;
        }

        leaves.into_iter().next().unwrap_or_else(String::new)
    }

    pub fn cal_path_root(paths: &[AggregatedSignedPaths]) -> String {
        let leaves = paths
            .iter()
            .map(|path| {
                let bytes = serde_json::to_vec(path).unwrap_or_default();
                encode(tools::Hasher::hash(bytes))
            })
            .collect();
        Block::cal_merkle_root(leaves)
    }

    pub fn gen_genesis_block() -> Block {
        let miner = Wallet::new();
        let transaction = Transaction::new("000".to_string(), 50, miner.clone());
        let paths = AggregatedSignedPaths::empty(DEFAULT_TRAIL_CHAIN_ID, 0);
        let body = Body::new(vec![transaction], vec![paths]);
        Block::new(0, 0, 0, "".to_string(), body, miner).unwrap()
    }

    pub fn decode_path(
        &self,
        path: &AggregatedSignedPaths,
        registry: &ValidatorRegistry,
    ) -> Result<Vec<String>, ValidatorRegistryError> {
        if self.header.index == 0 && path.paths.is_empty() {
            return Ok(vec![self.header.miner.clone()]);
        }
        path.full_path(&self.header.miner, registry)
    }

    pub fn count_node_paths_map(&self, registry: &ValidatorRegistry) -> HashMap<String, usize> {
        let mut counts = HashMap::new();
        for address in self.get_all_paths(registry).into_iter().flatten() {
            *counts.entry(address).or_insert(0) += 1;
        }
        counts
    }

    pub fn count_node_paths(&self, address: String, registry: &ValidatorRegistry) -> usize {
        self.count_node_paths_map(registry)
            .get(&address)
            .copied()
            .unwrap_or(0)
    }

    pub fn count_all_paths(&self, registry: &ValidatorRegistry) -> usize {
        self.get_all_paths(registry).iter().map(Vec::len).sum()
    }

    pub fn get_all_paths(&self, registry: &ValidatorRegistry) -> Vec<Vec<String>> {
        self.body
            .paths
            .iter()
            .filter_map(|path| self.decode_path(path, registry).ok())
            .collect()
    }

    pub fn from_json(json: Vec<u8>) -> Result<Block, BlockError> {
        let block: Block = serde_json::from_slice(json.as_slice())?;
        Ok(block)
    }

    pub fn to_json(&self) -> Vec<u8> {
        serde_json::to_vec(&self).unwrap()
    }

    pub fn simple_print_with_transaction(&self, registry: &ValidatorRegistry) {
        info!("Block[{}]:", self.header.index);
        info!("\t epoch:{}:", self.header.epoch);
        info!("\t slot:{}:", self.header.slot);
        info!("\t miner:{}:", self.header.miner);
        info!("\t timestamp:{}:", self.header.timestamp);
        info!("\t path_root:{}:", self.header.path_root);
        for (i, x) in self.body.transactions.iter().enumerate() {
            info!("\t transactions[{i}]:");
            info!("\t\t from:{}:", x.from);
            info!("\t\t to:{}:", x.to);
            info!("\t\t paths:");
            let path = self
                .body
                .paths
                .get(i)
                .map(|path| self.decode_path(path, registry));
            match path {
                Some(Ok(nodes)) => info!("\t\t\t{}", nodes.join("->")),
                Some(Err(error)) => info!("\t\t\t{error}"),
                None => info!("\t\t\tmissing path evidence"),
            }
        }
    }

    pub fn simple_print(&self) {
        info!("Block[{}]:", self.header.index);
        info!("\t epoch:{}:", self.header.epoch);
        info!("\t slot:{}:", self.header.slot);
        info!("\t miner:{}:", self.header.miner);
        info!("\t timestamp:{}:", self.header.timestamp);
        info!("\t transactions:{}:", self.body.transactions.len());
    }

    pub fn simple_print_no_transaction_string(&self, registry: &ValidatorRegistry) -> String {
        let mut s = format!("Block[{}]: \n", self.header.index);
        s.push_str(format!("\t epoch:{} \n", self.header.epoch).as_str());
        s.push_str(format!("\t slot:{} \n", self.header.slot).as_str());
        s.push_str(format!("\t miner:{} \n", self.header.miner).as_str());
        s.push_str(format!("\t timestamp:{} \n", self.header.timestamp).as_str());
        let trans_hash: Vec<String> = self
            .body
            .transactions
            .iter()
            .map(|x| x.hash.to_string())
            .collect();
        s.push_str(format!("\t transactions[{}]\n", trans_hash.join(",")).as_str());
        let paths: Vec<String> = self
            .body
            .paths
            .iter()
            .map(|path| match self.decode_path(path, registry) {
                Ok(nodes) => nodes.join("->"),
                Err(error) => error.to_string(),
            })
            .collect();
        s.push_str(format!("\t paths[{}]\n", paths.join(",")).as_str());
        s
    }

    pub fn simple_print_no_transaction_detail(&self, registry: &ValidatorRegistry) {
        info!("{}", self.simple_print_no_transaction_string(registry));
    }

    pub fn bytes(&self) -> u64 {
        self.header.bytes() + self.body.bytes()
    }
}

impl Body {
    pub fn new(transactions: Vec<Transaction>, paths: Vec<AggregatedSignedPaths>) -> Body {
        Body {
            transactions,
            paths,
        }
    }
    pub fn bytes(&self) -> u64 {
        let txs: u64 = self.transactions.iter().map(|x| x.bytes()).sum();
        let paths: u64 = self.paths.iter().map(|x| x.bytes()).sum();
        txs + paths
    }
}

#[derive(Debug)]
pub enum BlockError {
    InvalidBlock,
    InvalidBlockPath,
    ValidatorRegistry(ValidatorRegistryError),
    InvalidBlockTransactions,
    JSONError,
}

impl fmt::Display for BlockError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match *self {
            BlockError::ValidatorRegistry(ref error) => write!(f, "{error}"),
            BlockError::InvalidBlock => {
                write!(f, "Invalid Block Error")
            }

            BlockError::InvalidBlockPath => {
                write!(f, "Invalid Block Path Error")
            }
            BlockError::InvalidBlockTransactions => {
                write!(f, "Invalid Block Transactions Error")
            }
            BlockError::JSONError => {
                write!(f, "Invalid Block Json Error")
            }
        }
    }
}

impl From<ValidatorRegistryError> for BlockError {
    fn from(error: ValidatorRegistryError) -> Self {
        Self::ValidatorRegistry(error)
    }
}

impl From<serde_json::error::Error> for BlockError {
    fn from(_: serde_json::error::Error) -> Self {
        BlockError::JSONError
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blockchain::path::TransactionPaths;

    #[test]
    fn test_block() {
        let wallet = Wallet::new();
        let wallet2 = Wallet::new();
        let wallet3 = Wallet::new();
        let miner = Wallet::new();

        let registry =
            ValidatorRegistry::from_wallets([&wallet, &wallet2, &wallet3, &miner]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, wallet.clone());
        let mut transaction_paths = TransactionPaths::new(transaction.clone());
        transaction_paths.add_path(wallet2.address.clone(), wallet);
        assert!(transaction_paths.complete_pending_hop(wallet2.clone()));
        transaction_paths.add_path(wallet3.address.clone(), wallet2);
        assert!(transaction_paths.complete_pending_hop(wallet3.clone()));
        transaction_paths.add_path(miner.address.clone(), wallet3);
        assert!(transaction_paths.complete_pending_hop(miner.clone()));
        let body = Body::new(
            vec![transaction],
            vec![transaction_paths
                .to_aggregated_signed_paths(&registry)
                .unwrap()],
        );
        let block = match Block::new(0, 0, 0, String::from(""), body, miner) {
            Ok(block) => block,
            Err(e) => {
                error!("{}", e);
                return;
            }
        };
        println!("{:#?}", block);
        block.simple_print();
    }

    #[test]
    fn test_gen_genesis_block() {
        let genesis = Block::gen_genesis_block();
        let registry = ValidatorRegistry::default();
        assert!(genesis.verify_base());
        assert!(genesis.verify_path_evidence(0, &registry));
        assert_eq!(genesis.body.paths[0].bytes(), 0);
        assert_eq!(
            genesis.get_all_paths(&registry),
            vec![vec![genesis.header.miner.clone()]]
        );
        assert!(!genesis.verify_path_evidence(1, &registry));
    }

    #[test]
    fn path_metadata_is_committed() {
        let origin = Wallet::new();
        let relay = Wallet::new();
        let miner = Wallet::new();
        let registry = ValidatorRegistry::from_wallets([&origin, &relay, &miner]).unwrap();
        let transaction = Transaction::new("123".to_string(), 32, origin.clone());
        let mut transaction_paths = TransactionPaths::new(transaction.clone());
        transaction_paths.add_path(relay.address.clone(), origin);
        assert!(transaction_paths.complete_pending_hop(relay.clone()));
        transaction_paths.add_path(miner.address.clone(), relay);
        assert!(transaction_paths.complete_pending_hop(miner.clone()));
        let body = Body::new(
            vec![transaction],
            vec![transaction_paths
                .to_aggregated_signed_paths(&registry)
                .unwrap()],
        );
        let mut block = Block::new(1, 0, 0, String::from("parent"), body, miner).unwrap();
        assert!(block.verify_base());
        assert!(block.verify_path_evidence(0, &registry));
        let decoded = Block::from_json(block.to_json()).unwrap();
        assert!(decoded.verify_base());
        assert!(decoded.verify_path_evidence(0, &registry));

        block.body.paths[0].paths[0] = u64::MAX;
        assert!(!block.verify_base());
        block.header.path_root = Block::cal_path_root(&block.body.paths);
        block.header.hash = block.header.get_hash();
        assert!(block.verify_base());
        assert!(!block.verify_path_evidence(0, &registry));
    }

    #[test]
    fn path_signatures_cannot_cancel_between_certificates() {
        let origin = Wallet::new();
        let miner = Wallet::new();
        let registry = ValidatorRegistry::from_wallets([&origin, &miner]).unwrap();
        let transactions: Vec<_> = [1, 2]
            .into_iter()
            .map(|amount| Transaction::new(miner.address.clone(), amount, origin.clone()))
            .collect();
        let mut certs: Vec<_> = transactions
            .iter()
            .map(|transaction| {
                let mut path = TransactionPaths::new(transaction.clone());
                assert!(path.append_completed_hop(
                    miner.address.clone(),
                    origin.clone(),
                    miner.clone()
                ));
                path.to_aggregated_signed_paths(&registry).unwrap()
            })
            .collect();
        let body = Body::new(transactions.clone(), certs.clone());
        let valid = Block::new(1, 0, 0, String::new(), body, miner.clone()).unwrap();
        assert!((0..2).all(|index| valid.verify_path_evidence(index, &registry)));
        // The sum of both certificates would stay the same after this swap.
        let (first, second) = certs.split_at_mut(1);
        std::mem::swap(&mut first[0].signature, &mut second[0].signature);
        let swapped = Block::new(
            1,
            0,
            0,
            String::new(),
            Body::new(transactions, certs),
            miner,
        )
        .unwrap();
        assert!(swapped.verify_base());
        assert!((0..2).all(|index| !swapped.verify_path_evidence(index, &registry)));
    }
}
