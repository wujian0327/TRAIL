//! Parallel node inboxes with deterministic logical-time delivery boundaries.
//! Uses authentic one-prefix receipts and the existing frozen-v1 consensus.
use super::{coalition, generate_stake_by_gini, graph, select_adversarial_nodes, SimulationConfig};
use crate::blockchain::{
    block::{Block, Body},
    path::TransactionPaths,
    transaction::Transaction,
    Blockchain,
};
use crate::consensus::{trail::TrailConsensus, Consensus, Validator, ValidatorRegistry};
use crate::{tools, wallet::Wallet};
use rand::{rngs::StdRng, Rng, SeedableRng};
use rand_distr::{Distribution, Poisson};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    fs::{File, OpenOptions},
    io::Write,
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};

pub fn default_forwarding_cost() -> f64 {
    1.4055695425812074e-7
}

pub fn default_node_workers() -> usize {
    8
}

#[derive(Default)]
struct ParallelStats {
    active: AtomicUsize,
    peak: AtomicUsize,
    batches: AtomicUsize,
}

struct ActiveNode(Arc<ParallelStats>);
impl ActiveNode {
    fn start(stats: Arc<ParallelStats>) -> Self {
        let active = stats.active.fetch_add(1, Ordering::SeqCst) + 1;
        stats.peak.fetch_max(active, Ordering::SeqCst);
        Self(stats)
    }
}
impl Drop for ActiveNode {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::SeqCst);
    }
}

/// Stateless node/transaction coin. No draw depends on arrivals or iteration order.
pub fn coin(seed: u64, node: &str, transaction: &str) -> f64 {
    let mut bytes = seed.to_le_bytes().to_vec();
    bytes.extend_from_slice(b"/selective-withholding/");
    bytes.extend_from_slice(node.as_bytes());
    bytes.push(0);
    bytes.extend_from_slice(transaction.as_bytes());
    let digest = tools::Hasher::hash(bytes);
    let value = u64::from_le_bytes(digest[..8].try_into().unwrap());
    (value >> 11) as f64 / ((1u64 << 53) as f64)
}

pub fn should_withhold(
    c: &SimulationConfig,
    epoch: u64,
    node: &str,
    tx: &str,
    coalition_receiver: bool,
    coalition_origin: bool,
    coalition_upstream: bool,
) -> bool {
    decision(
        c.withholding_probability,
        c.withholding_start_epoch,
        c.withholding_end_epoch,
        c.attack_seed,
        epoch,
        node,
        tx,
        coalition_receiver,
        coalition_origin,
        coalition_upstream,
    )
}

fn decision(
    probability: f64,
    start: u64,
    end: u64,
    seed: u64,
    epoch: u64,
    node: &str,
    tx: &str,
    coalition_receiver: bool,
    coalition_origin: bool,
    coalition_upstream: bool,
) -> bool {
    coalition_receiver
        && !coalition_origin
        && !coalition_upstream
        && (start..end).contains(&epoch)
        && coin(seed, node, tx) < probability
}

struct Peer {
    wallet: Wallet,
    stake: f64,
    balance: f64,
    neighbors: Vec<usize>,
    cache: BTreeMap<String, TransactionPaths>,
}

enum Event {
    Create {
        from: usize,
        to: usize,
        id: u64,
        epoch: u64,
    },
    Receive {
        from: usize,
        to: usize,
        path: TransactionPaths,
    },
}

#[derive(Default, Serialize, Deserialize)]
pub struct EpochAudit {
    pub epoch: u64,
    pub phase: String,
    pub generated: u64,
    pub generation_rejected: u64,
    pub included: u64,
    pub eligible_node_transactions: u64,
    pub withheld_node_transactions: u64,
    pub eligible_unique_transactions: u64,
    pub withheld_unique_transactions: u64,
    pub suppressed_honest_neighbor_forwards: u64,
    pub coalition_forwarding_attempts: u64,
    pub honest_forwarding_attempts: u64,
    pub origin_broadcast_attempts: u64,
    pub rejected_invalid_paths: u64,
    pub expired_paths: u64,
    pub block_success: u64,
    pub block_failure: u64,
    pub coalition_raw_contribution: f64,
    pub honest_raw_contribution: f64,
    pub coalition_relay_rewards: f64,
    pub coalition_proposer_rewards: f64,
    pub coalition_actual_proposer_count: u64,
    pub coalition_transaction_payments: f64,
    pub coalition_forwarding_cost: f64,
    pub ledger_net_income: f64,
    pub modeled_cost_adjusted_net_income: f64,
    pub coalition_proposer_weight_share: f64,
    pub expected_coalition_proposer_rewards: f64,
    pub actual_stake_share: f64,
    pub theoretical_bound: f64,
    pub bound_utilization: f64,
    pub bound_violations: u64,
    pub stake_premise_violations: u64,
}

struct Transport<'a> {
    config: &'a SimulationConfig,
    peers: Vec<Peer>,
    coalition: BTreeSet<usize>,
    queue: BTreeMap<(u64, u64, usize, usize, u8), Event>,
    parallel_stats: Arc<ParallelStats>,
    included: HashSet<String>,
    eligible: BTreeSet<String>,
    withheld: BTreeSet<String>,
    audit: EpochAudit,
    decisions: File,
    generations: File,
}

fn fresh(directory: &Path, name: &str) -> File {
    OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(directory.join(name))
        .expect("withholding evidence requires fresh output files")
}
fn json_line(file: &mut File, value: &impl Serialize) {
    serde_json::to_writer(&mut *file, value).unwrap();
    writeln!(file).unwrap();
}
fn identity(value: &impl Serialize) -> String {
    hex::encode(tools::Hasher::hash(serde_json::to_vec(value).unwrap()))
}

struct NodeContext {
    config: SimulationConfig,
    addresses: Vec<String>,
    coalition: BTreeSet<usize>,
    included: HashSet<String>,
    epoch: u64,
}

struct NodeWork {
    node: usize,
    peer: Peer,
    context: Arc<NodeContext>,
    audit: EpochAudit,
    eligible: BTreeSet<String>,
    withheld: BTreeSet<String>,
    outgoing: Vec<(u64, Event)>,
    decisions: Vec<serde_json::Value>,
    generations: Vec<serde_json::Value>,
}

impl NodeWork {
    fn new(node: usize, peer: Peer, context: Arc<NodeContext>) -> Self {
        Self {
            node,
            peer,
            context,
            audit: EpochAudit::default(),
            eligible: BTreeSet::new(),
            withheld: BTreeSet::new(),
            outgoing: vec![],
            decisions: vec![],
            generations: vec![],
        }
    }

    fn forward(
        &mut self,
        at: u64,
        upstream: Option<usize>,
        path: &TransactionPaths,
        withheld: bool,
    ) {
        let nodes = path.node_sequence();
        for to in self.peer.neighbors.clone() {
            if Some(to) == upstream
                || nodes.contains(&self.context.addresses[to])
                || path.paths.len() >= self.context.config.trail_config.max_path_hops
            {
                continue;
            }
            if withheld && !self.context.coalition.contains(&to) {
                self.audit.suppressed_honest_neighbor_forwards += 1;
                continue;
            }
            let mut outgoing = path.clone();
            assert!(outgoing
                .append_outgoing_hop(self.context.addresses[to].clone(), self.peer.wallet.clone()));
            if upstream.is_some() {
                if self.context.coalition.contains(&self.node) {
                    self.audit.coalition_forwarding_attempts += 1;
                } else {
                    self.audit.honest_forwarding_attempts += 1;
                }
            } else {
                self.audit.origin_broadcast_attempts += 1;
            }
            let delay = (50.0 * self.context.config.network_delay_multiplier).round() as u64;
            self.outgoing.push((
                at + delay,
                Event::Receive {
                    from: self.node,
                    to,
                    path: outgoing,
                },
            ));
        }
    }

    fn process(&mut self, at: u64, event: Event) {
        match event {
            Event::Create {
                from,
                to,
                id,
                epoch,
            } => {
                assert_eq!(from, self.node);
                let config = &self.context.config;
                let price = config.transaction_fee + config.effective_irrecoverable_cost();
                if self.peer.balance < price {
                    self.audit.generation_rejected += 1;
                    return;
                }
                self.peer.balance -= price;
                if self.context.coalition.contains(&self.node) {
                    self.audit.coalition_transaction_payments += price;
                }
                let mut tx = Transaction::with_timestamp(
                    self.context.addresses[to].clone(),
                    0,
                    config.transaction_fee,
                    config.effective_irrecoverable_cost(),
                    self.peer.wallet.clone(),
                    false,
                    id,
                );
                tx.data = super::world_state::logical_tx_metadata(
                    epoch,
                    (at / 1000) % config.slot_per_epoch,
                );
                assert!(tx.verify());
                self.audit.generated += 1;
                self.generations
                    .push(serde_json::json!({"epoch":epoch,"at_ms":at,"id":id,"transaction":tx}));
                let path = TransactionPaths::new_with_epoch(tx.clone(), epoch);
                self.peer.cache.insert(tx.hash.clone(), path.clone());
                self.forward(at, None, &path, false);
            }
            Event::Receive { from, to, mut path } => {
                assert_eq!(to, self.node);
                let hash = path.transaction.hash.clone();
                if self.context.included.contains(&hash) || self.peer.cache.contains_key(&hash) {
                    return;
                }
                if path.epoch != self.context.epoch {
                    self.audit.expired_paths += 1;
                    return;
                }
                if !path.verify_pending_prefix(
                    &self.peer.wallet.address,
                    path.epoch,
                    self.context.config.trail_config.max_path_hops,
                ) || path.paths.last().unwrap().from != self.context.addresses[from]
                    || !path.complete_pending_hop(self.peer.wallet.clone())
                {
                    self.audit.rejected_invalid_paths += 1;
                    return;
                }
                let coalition_origin = self
                    .context
                    .coalition
                    .iter()
                    .any(|i| self.context.addresses[*i] == path.transaction.from);
                let eligible = self.context.coalition.contains(&self.node)
                    && !self.context.coalition.contains(&from)
                    && !coalition_origin;
                let withheld = should_withhold(
                    &self.context.config,
                    self.context.epoch,
                    &self.peer.wallet.address,
                    &hash,
                    self.context.coalition.contains(&self.node),
                    coalition_origin,
                    self.context.coalition.contains(&from),
                );
                if eligible {
                    self.audit.eligible_node_transactions += 1;
                    self.eligible.insert(hash.clone());
                    if withheld {
                        self.audit.withheld_node_transactions += 1;
                        self.withheld.insert(hash.clone());
                    }
                    self.decisions.push(serde_json::json!({"epoch":self.context.epoch,"at_ms":at,
                        "node":self.peer.wallet.address,"upstream":self.context.addresses[from],
                        "origin":path.transaction.from,"tx_hash":hash,
                        "coin":coin(self.context.config.attack_seed,&self.peer.wallet.address,&hash),
                        "withheld":withheld,"validated_prefix":path}));
                }
                self.peer.cache.insert(hash, path.clone());
                self.forward(at, Some(from), &path, withheld);
            }
        }
    }
}

impl Transport<'_> {
    fn enqueue(&mut self, at: u64, event: Event) {
        // Stable envelope identity; completion order of parallel workers is irrelevant.
        let key = match &event {
            Event::Create { from, id, .. } => (at, *id, *from, *from, 0),
            Event::Receive { from, to, path } => (at, path.transaction.timestamp, *to, *from, 1),
        };
        assert!(
            self.queue.insert(key, event).is_none(),
            "duplicate delivery envelope"
        );
    }

    async fn drain(&mut self, deadline: u64) {
        let lookahead = (50.0 * self.config.network_delay_multiplier).round() as u64;
        assert!(
            lookahead > 0,
            "parallel transport requires a positive hop delay"
        );
        let context = Arc::new(NodeContext {
            config: self.config.clone(),
            addresses: self
                .peers
                .iter()
                .map(|p| p.wallet.address.clone())
                .collect(),
            coalition: self.coalition.clone(),
            included: self.included.clone(),
            epoch: self.audit.epoch,
        });
        let semaphore = Arc::new(tokio::sync::Semaphore::new(
            self.config.withholding_node_workers,
        ));
        while self
            .queue
            .first_key_value()
            .map(|(key, _)| key.0 <= deadline)
            .unwrap_or(false)
        {
            let first = self.queue.first_key_value().unwrap().0 .0;
            let end = (first + lookahead).min(deadline + 1);
            let mut inboxes: Vec<Vec<(u64, Event)>> =
                (0..self.peers.len()).map(|_| vec![]).collect();
            // Only dispatch is ordered. Independent nodes verify and forward concurrently.
            // Every new message needs >=lookahead to arrive, so none can reach another
            // node inside this batch. This preserves causal order without global serial work.
            while self
                .queue
                .first_key_value()
                .map(|(key, _)| key.0 < end)
                .unwrap_or(false)
            {
                let (key, event) = self.queue.pop_first().unwrap();
                inboxes[key.2].push((key.0, event));
            }
            self.parallel_stats.batches.fetch_add(1, Ordering::SeqCst);
            let peers = std::mem::take(&mut self.peers);
            let mut tasks = vec![];
            for (node, peer) in peers.into_iter().enumerate() {
                let events = std::mem::take(&mut inboxes[node]);
                let context = context.clone();
                let semaphore = semaphore.clone();
                let stats = self.parallel_stats.clone();
                tasks.push(async move {
                    let mut work = NodeWork::new(node, peer, context);
                    if events.is_empty() {
                        return work;
                    }
                    let permit = semaphore.acquire_owned().await.unwrap();
                    tokio::task::spawn_blocking(move || {
                        let _permit = permit;
                        let _active = ActiveNode::start(stats);
                        for (at, event) in events {
                            work.process(at, event);
                        }
                        work
                    })
                    .await
                    .expect("parallel node processing failed")
                });
            }
            // join_all retains node order, not completion order: deterministic reductions.
            for work in futures::future::join_all(tasks).await {
                assert_eq!(work.node, self.peers.len());
                self.peers.push(work.peer);
                self.audit.generated += work.audit.generated;
                self.audit.generation_rejected += work.audit.generation_rejected;
                self.audit.eligible_node_transactions += work.audit.eligible_node_transactions;
                self.audit.withheld_node_transactions += work.audit.withheld_node_transactions;
                self.audit.suppressed_honest_neighbor_forwards +=
                    work.audit.suppressed_honest_neighbor_forwards;
                self.audit.coalition_forwarding_attempts +=
                    work.audit.coalition_forwarding_attempts;
                self.audit.honest_forwarding_attempts += work.audit.honest_forwarding_attempts;
                self.audit.origin_broadcast_attempts += work.audit.origin_broadcast_attempts;
                self.audit.rejected_invalid_paths += work.audit.rejected_invalid_paths;
                self.audit.expired_paths += work.audit.expired_paths;
                self.audit.coalition_transaction_payments +=
                    work.audit.coalition_transaction_payments;
                self.eligible.extend(work.eligible);
                self.withheld.extend(work.withheld);
                for record in work.decisions {
                    json_line(&mut self.decisions, &record);
                }
                for record in work.generations {
                    json_line(&mut self.generations, &record);
                }
                for (at, event) in work.outgoing {
                    self.enqueue(at, event);
                }
            }
        }
    }

    fn candidates(&self, node: usize) -> Vec<TransactionPaths> {
        let mut candidates: Vec<_> = self.peers[node]
            .cache
            .values()
            .filter(|p| p.epoch == self.audit.epoch && !self.included.contains(&p.transaction.hash))
            .cloned()
            .collect();
        candidates.sort_by(|a, b| {
            b.transaction
                .fee
                .total_cmp(&a.transaction.fee)
                .then_with(|| a.transaction.timestamp.cmp(&b.transaction.timestamp))
                .then_with(|| a.transaction.hash.cmp(&b.transaction.hash))
        });
        candidates.truncate(self.config.max_tx_per_block);
        candidates
    }
}

pub async fn run(config: &SimulationConfig) {
    let directory = Path::new(&config.output_dir);
    let n = config.node_num as usize;
    let stakes = generate_stake_by_gini(config.node_num, config.gini, config.wallet_seed);
    let mut peers: Vec<_> = (0..n)
        .map(|i| Peer {
            wallet: Wallet::new_deterministic(config.wallet_seed, i as u32),
            stake: stakes[i],
            balance: config.initial_liquid_balance.unwrap_or(stakes[i]),
            neighbors: vec![],
            cache: BTreeMap::new(),
        })
        .collect();
    peers.sort_by(|a, b| a.wallet.address.cmp(&b.wallet.address));
    let addresses: Vec<_> = peers.iter().map(|p| p.wallet.address.clone()).collect();
    let graph = match config.topology {
        graph::TopologyType::BA => graph::random_ba_graph(addresses.clone(), config.graph_seed),
        graph::TopologyType::ER => {
            graph::random_er_graph(addresses.clone(), 0.1, config.graph_seed)
        }
        graph::TopologyType::WS => {
            graph::random_ws_graph(addresses.clone(), 4, 0.1, config.graph_seed)
        }
        graph::TopologyType::EthEmpirical => {
            graph::random_eth_empirical_graph(addresses.clone(), config.graph_seed)
        }
    };
    let index: HashMap<_, _> = addresses
        .iter()
        .enumerate()
        .map(|(i, a)| (a.clone(), i))
        .collect();
    let mut edges: Vec<_> = graph::graph_edges(&graph)
        .into_iter()
        .map(|(a, b)| if a < b { (a, b) } else { (b, a) })
        .collect();
    edges.sort();
    edges.dedup();
    for (a, b) in &edges {
        let a = index[a];
        let b = index[b];
        peers[a].neighbors.push(b);
        peers[b].neighbors.push(a);
    }
    for p in &mut peers {
        p.neighbors.sort();
    }
    let stake_map: HashMap<_, _> = peers
        .iter()
        .map(|p| (p.wallet.address.clone(), p.stake))
        .collect();
    let degrees = peers
        .iter()
        .map(|p| (p.wallet.address.clone(), p.neighbors.len()))
        .collect();
    let chosen = select_adversarial_nodes(
        &stake_map,
        &degrees,
        &coalition::betweenness(&graph),
        config.adversary_stake_fraction,
        config.adversary_placement,
        config.attack_seed,
    );
    let coalition: BTreeSet<_> = chosen.iter().map(|a| index[a]).collect();
    let initial_stakes: BTreeMap<_, _> = stake_map.into_iter().collect();
    let coalition_addresses: BTreeSet<_> = chosen.into_iter().collect();
    for (name, value) in [
        ("graph.json", serde_json::to_value(&edges).unwrap()),
        (
            "initial_stakes.json",
            serde_json::to_value(&initial_stakes).unwrap(),
        ),
        (
            "coalition.json",
            serde_json::to_value(&coalition_addresses).unwrap(),
        ),
    ] {
        serde_json::to_writer_pretty(fresh(directory, name), &value).unwrap();
    }
    let total_stake: f64 = peers.iter().map(|p| p.stake).sum();
    let actual_share: f64 = coalition.iter().map(|i| peers[*i].stake).sum::<f64>() / total_stake;
    let validators: Vec<_> = peers
        .iter()
        .map(|p| Validator::new(p.wallet.address.clone(), p.stake, p.stake))
        .collect();
    let registry = Arc::new(
        ValidatorRegistry::from_wallets(peers.iter().map(|peer| &peer.wallet))
            .expect("invalid withholding validator roster"),
    );
    super::write_validator_indices(directory, &registry);
    let mut consensus = TrailConsensus::new(
        config.base_reward,
        config.trail_config.clone(),
        registry.clone(),
    )
    .unwrap();
    consensus.enable_deterministic_reductions();
    consensus.freeze_proposer_weights(&validators);
    let mut genesis = Block::new(
        0,
        0,
        0,
        String::new(),
        Body::new(vec![], vec![]),
        peers[0].wallet.clone(),
    )
    .unwrap();
    genesis.header.timestamp = 0;
    genesis.header.hash = genesis.header.get_hash();
    let mut chain = Blockchain::new(genesis).with_validator_registry(registry.clone());
    let mut transport = Transport {
        config,
        peers,
        coalition,
        queue: BTreeMap::new(),
        parallel_stats: Arc::new(ParallelStats::default()),
        included: HashSet::new(),
        eligible: BTreeSet::new(),
        withheld: BTreeSet::new(),
        audit: EpochAudit::default(),
        decisions: fresh(directory, "withholding_decisions.jsonl"),
        generations: fresh(directory, "background_transactions.jsonl"),
    };
    let mut counts = StdRng::seed_from_u64(config.workload_seed ^ 0x4247_5f43_4f55_4e54);
    let mut routes = StdRng::seed_from_u64(config.workload_seed ^ 0x4247_5f52_4f55_5445);
    let poisson =
        Poisson::new(config.trans_num_per_second as f64 * config.slot_duration as f64).unwrap();
    let mut economics = fresh(directory, "withholding_epochs.jsonl");
    let mut slots = fresh(directory, "withholding_slots.jsonl");
    let mut blocks_file = fresh(directory, "blocks.jsonl");
    let mut states = fresh(directory, "withholding_states.jsonl");
    let mut csv = fresh(directory, "withholding_epochs.csv");
    let mut generated_total = 0u64;
    let mut included_total = 0u64;
    let mut next_tx = 0;
    for epoch in 0..config.max_epochs {
        transport.audit = EpochAudit {
            epoch,
            phase: if epoch < config.withholding_start_epoch {
                "warmup"
            } else if epoch < config.withholding_end_epoch {
                "attack"
            } else {
                "recovery"
            }
            .into(),
            actual_stake_share: actual_share,
            theoretical_bound: actual_share * (1.0 + config.trail_config.eta)
                / (1.0 + actual_share * config.trail_config.eta),
            ..EpochAudit::default()
        };
        transport.eligible.clear();
        transport.withheld.clear();
        let weights = consensus.metrics_snapshot().normalized_proposer_weights;
        let ordered_weights: BTreeMap<_, _> = weights.clone().into_iter().collect();
        let scores: BTreeMap<_, _> = consensus.score_history().clone().into_iter().collect();
        let balances: BTreeMap<_, _> = transport
            .peers
            .iter()
            .map(|p| (p.wallet.address.clone(), p.balance))
            .collect();
        json_line(
            &mut states,
            &serde_json::json!({"epoch":epoch,"weights":ordered_weights,"latest_scores":scores,"balances":balances}),
        );
        transport.audit.coalition_proposer_weight_share = transport
            .coalition
            .iter()
            .map(|i| weights[&addresses[*i]])
            .sum();
        transport.audit.bound_utilization =
            transport.audit.coalition_proposer_weight_share / transport.audit.theoretical_bound;
        transport.audit.bound_violations = u64::from(
            transport.audit.coalition_proposer_weight_share
                > transport.audit.theoretical_bound + 1e-12,
        );
        transport.audit.stake_premise_violations = u64::from(actual_share >= 1.0 / 3.0);
        let mut epoch_blocks = vec![];
        for slot in 0..config.slot_per_epoch {
            let logical_slot = epoch * config.slot_per_epoch + slot;
            let start = logical_slot * config.slot_duration * 1000;
            let deadline = start + config.slot_duration * 1000 - 1;
            let count = poisson.sample(&mut counts) as u64;
            for ordinal in 0..count {
                let from = routes.gen_range(0..n);
                let mut to = routes.gen_range(0..n);
                while from == to {
                    to = routes.gen_range(0..n);
                }
                next_tx += 1;
                let at = start + (ordinal + 1) * config.slot_duration * 1000 / (count + 1);
                transport.enqueue(
                    at,
                    Event::Create {
                        from,
                        to,
                        id: next_tx,
                        epoch,
                    },
                );
            }
            transport.drain(deadline).await;
            let index_block = logical_slot + 1;
            let mut seed = config.election_seed.to_le_bytes().to_vec();
            seed.extend_from_slice(&epoch.to_le_bytes());
            seed.extend_from_slice(&slot.to_le_bytes());
            seed.extend_from_slice(&index_block.to_le_bytes());
            let proposer = consensus
                .select_proposer(&validators, tools::Hasher::hash(seed), &chain)
                .unwrap();
            let proposer_index = index[&proposer.address];
            let expected: f64 = transport
                .coalition
                .iter()
                .map(|i| {
                    let fees: f64 = transport
                        .candidates(*i)
                        .iter()
                        .map(|p| p.transaction.fee)
                        .sum();
                    weights[&addresses[*i]]
                        * (config.base_reward + config.trail_config.proposer_fee_ratio * fees)
                })
                .sum();
            transport.audit.expected_coalition_proposer_rewards += expected;
            let paths = transport.candidates(proposer_index);
            let body = Body::new(
                paths.iter().map(|p| p.transaction.clone()).collect(),
                paths
                    .iter()
                    .map(|path| path.to_aggregated_signed_paths(&registry))
                    .collect::<Result<_, _>>()
                    .expect("registered withholding path identities"),
            );
            let mut block = Block::new(
                index_block,
                epoch,
                slot,
                chain.get_last_hash(),
                body,
                transport.peers[proposer_index].wallet.clone(),
            )
            .unwrap();
            block.header.timestamp = deadline;
            block.header.hash = block.header.get_hash();
            assert!(block.verify());
            for (i, tx) in block.body.transactions.iter().enumerate() {
                assert!(block.body.paths[i].verify_at_epoch_uncached(
                    tx,
                    &proposer.address,
                    epoch,
                    &registry
                ));
                assert!(transport.included.insert(tx.hash.clone()));
                let path = block.body.paths[i]
                    .full_path(&proposer.address, &registry)
                    .unwrap();
                let hops = path.len().saturating_sub(1);
                for position in 1..hops {
                    let q = TrailConsensus::transaction_credit_weight(
                        tx.irrecoverable_cost,
                        config.trail_config.score_cost_reference,
                    );
                    let contribution = q * TrailConsensus::gamma_for_depth(
                        config.trail_config.target_depth,
                        position,
                        hops,
                    );
                    if transport.coalition.contains(&index[&path[position]]) {
                        transport.audit.coalition_raw_contribution += contribution;
                    } else {
                        transport.audit.honest_raw_contribution += contribution;
                    }
                }
            }
            let plan = consensus.compute_block_rewards(&block, &validators);
            let coalition_proposer = transport.coalition.contains(&proposer_index);
            let coalition_reward: f64 = plan
                .rewards
                .iter()
                .filter(|r| coalition_addresses.contains(&r.address))
                .map(|r| r.amount)
                .sum();
            let proposer_reward = if coalition_proposer {
                plan.proposer_reward
            } else {
                0.0
            };
            transport.audit.coalition_proposer_rewards += proposer_reward;
            transport.audit.coalition_relay_rewards += coalition_reward - proposer_reward;
            transport.audit.coalition_actual_proposer_count += u64::from(coalition_proposer);
            transport.audit.included += block.body.transactions.len() as u64;
            transport.audit.block_success += 1;
            chain.add_block(block.clone()).unwrap();
            // Settle the existing delayed reward plan, without counting it twice as income.
            for delta in consensus.distribute_rewards(&block, &validators, HashMap::new()) {
                transport.peers[index[&delta.address]].balance += delta.amount;
            }
            for peer in &mut transport.peers {
                peer.cache
                    .retain(|hash, _| !transport.included.contains(hash));
            }
            json_line(
                &mut slots,
                &serde_json::json!({"epoch":epoch,"slot":slot,"proposer":proposer.address,
                "coalition_proposer":coalition_proposer,"expected_coalition_proposer_reward":expected,
                "included":block.body.transactions.len(),"block_hash":block.header.hash}),
            );
            json_line(&mut blocks_file, &block);
            epoch_blocks.push(block);
        }
        transport.audit.eligible_unique_transactions = transport.eligible.len() as u64;
        transport.audit.withheld_unique_transactions = transport.withheld.len() as u64;
        transport.audit.coalition_forwarding_cost = transport.audit.coalition_forwarding_attempts
            as f64
            * config.withholding_forwarding_cost;
        transport.audit.ledger_net_income = transport.audit.coalition_relay_rewards
            + transport.audit.coalition_proposer_rewards
            - transport.audit.coalition_transaction_payments;
        transport.audit.modeled_cost_adjusted_net_income =
            transport.audit.ledger_net_income - transport.audit.coalition_forwarding_cost;
        let value = serde_json::to_value(&transport.audit).unwrap();
        if epoch == 0 {
            writeln!(
                csv,
                "{}",
                value
                    .as_object()
                    .unwrap()
                    .keys()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(",")
            )
            .unwrap();
        }
        writeln!(
            csv,
            "{}",
            value
                .as_object()
                .unwrap()
                .values()
                .map(|v| if let Some(s) = v.as_str() {
                    s.to_string()
                } else {
                    v.to_string()
                })
                .collect::<Vec<_>>()
                .join(",")
        )
        .unwrap();
        csv.flush().unwrap();
        json_line(&mut economics, &transport.audit);
        economics.flush().unwrap();
        generated_total += transport.audit.generated;
        included_total += transport.audit.included;
        // Signed path domains expire at their epoch. No artificial retry or cross-epoch resigning.
        for peer in &mut transport.peers {
            peer.cache.clear();
        }
        consensus.on_epoch_end(&epoch_blocks, &validators);
        let summary = serde_json::json!({"run_id":config.run_id,"completed_epochs":epoch+1,"generated_tx":generated_total,
            "included_tx":included_total,"block_production_success":(epoch+1)*config.slot_per_epoch,"block_production_failed":0,
            "actual_stake_share":actual_share,"coalition_identity_hash":identity(&coalition_addresses),
            "graph_identity_hash":identity(&edges),"initial_stakes_identity_hash":identity(&initial_stakes),
            "transport":"parallel-event-v2",
            "node_workers":config.withholding_node_workers,
            "parallel_node_peak":transport.parallel_stats.peak.load(Ordering::SeqCst),"forwarding_cost_reference":config.withholding_forwarding_cost});
        let temp = directory.join("run_summary.tmp");
        std::fs::write(&temp, serde_json::to_vec_pretty(&summary).unwrap()).unwrap();
        std::fs::rename(temp, directory.join("run_summary.json")).unwrap();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn node_transaction_coin_is_stable_and_not_shared_between_nodes() {
        assert_eq!(coin(1, "a", "tx"), coin(1, "a", "tx"));
        assert_ne!(coin(1, "a", "tx"), coin(1, "b", "tx"));
        let hits = (0..10000)
            .filter(|i| coin(1, "a", &i.to_string()) < 0.5)
            .count();
        assert!((4800..5200).contains(&hits));
    }

    #[test]
    fn only_coalition_receivers_of_honest_to_honest_origin_messages_are_targets() {
        assert!(decision(1., 10, 50, 1, 10, "a", "t", true, false, false));
        assert!(!decision(1., 10, 50, 1, 10, "a", "t", false, false, false));
        assert!(!decision(1., 10, 50, 1, 10, "a", "t", true, true, false));
        assert!(!decision(1., 10, 50, 1, 10, "a", "t", true, false, true));
    }

    #[test]
    fn zero_probability_warmup_and_recovery_never_withhold() {
        for epoch in 0..80 {
            assert!(!decision(
                0., 10, 50, 1, epoch, "a", "t", true, false, false
            ));
            assert_eq!(
                decision(1., 10, 50, 1, epoch, "a", "t", true, false, false),
                (10..50).contains(&epoch)
            );
        }
    }

    #[test]
    fn logical_transaction_identity_is_reproducible_and_signed() {
        let wallet = Wallet::new_deterministic(88381, 1);
        let a = Transaction::with_timestamp(
            "receiver".into(),
            0,
            0.001,
            0.001,
            wallet.clone(),
            false,
            1,
        );
        let b = Transaction::with_timestamp("receiver".into(), 0, 0.001, 0.001, wallet, false, 1);
        assert_eq!(a.hash, b.hash);
        assert!(a.verify());
        let mut tampered = a;
        tampered.timestamp += 1;
        assert!(!tampered.verify());
    }
}
