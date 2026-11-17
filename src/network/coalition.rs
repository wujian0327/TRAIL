//! Opt-in terminal-receiver collection for Fig. 4(b). No RNG is used here.
use crate::blockchain::block::Block;
use crate::blockchain::path::{AggregatedSignedPaths, TransactionPaths};
use crate::consensus::trail::TrailConsensus;
use clap::ValueEnum;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fmt;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::{Arc, Mutex};
use tokio::time::Instant;

#[derive(ValueEnum, Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum CoalitionPathSelection {
    #[default]
    Off,
    Honest,
    Favoritism,
}

impl fmt::Display for CoalitionPathSelection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Off => "off",
            Self::Honest => "honest",
            Self::Favoritism => "favoritism",
        })
    }
}

pub type CandidatePool = Arc<tokio::sync::Mutex<CandidateWindow>>;
pub type PaymentLedger = Arc<Mutex<HashMap<u64, f64>>>;

#[derive(Debug, Default, Clone, Serialize)]
pub struct GenerationBatch {
    pub logical_slot: u64,
    pub sampled: u64,
    pub enqueued: u64,
    pub generated: u64,
    pub rejected: u64,
}

pub struct SlotWorkloadRequest {
    pub logical_slot: u64,
    pub reply: tokio::sync::oneshot::Sender<GenerationBatch>,
}

/// Every processed generation request acknowledges success or failure, including
/// early returns. Dropping an unprocessed message cancels its oneshot instead.
pub struct GenerationCompletion {
    reply: Option<tokio::sync::oneshot::Sender<bool>>,
    pub generated: bool,
}

impl GenerationCompletion {
    pub fn new(reply: Option<tokio::sync::oneshot::Sender<bool>>) -> Self {
        Self {
            reply,
            generated: false,
        }
    }
}

impl Drop for GenerationCompletion {
    fn drop(&mut self) {
        if let Some(reply) = self.reply.take() {
            let _ = reply.send(self.generated);
        }
    }
}

pub type ArrivalLog = Arc<Mutex<File>>;

#[derive(Serialize)]
struct CandidateArrival<'a> {
    epoch: u64,
    slot: u64,
    receiver: &'a str,
    tx_hash: &'a str,
    nodes: Vec<String>,
    elapsed_ms: f64,
    verification_ms: f64,
    disposition: &'a str,
}

/// Brandes dependencies across ALL shortest paths, used only by this opt-in
/// study. Canonical neighbor order avoids HashMap/edge-insertion tie effects.
pub fn betweenness(graph: &petgraph::Graph<String, ()>) -> HashMap<String, f64> {
    let size = graph
        .node_indices()
        .map(|v| v.index() + 1)
        .max()
        .unwrap_or(0);
    let mut scores = vec![0.0; size];
    let mut ordered_nodes: Vec<_> = graph.node_indices().collect();
    ordered_nodes.sort_by(|a, b| graph[*a].cmp(&graph[*b]));
    for source in ordered_nodes {
        let mut predecessors = vec![Vec::new(); size];
        let mut distance = vec![-1; size];
        let mut sigma = vec![0.0; size];
        let mut queue = std::collections::VecDeque::from([source]);
        let mut stack = Vec::new();
        distance[source.index()] = 0;
        sigma[source.index()] = 1.0;
        while let Some(v) = queue.pop_front() {
            stack.push(v);
            let mut neighbors: Vec<_> = graph.neighbors_undirected(v).collect();
            neighbors.sort_by(|a, b| graph[*a].cmp(&graph[*b]));
            neighbors.dedup();
            for w in neighbors {
                if distance[w.index()] < 0 {
                    distance[w.index()] = distance[v.index()] + 1;
                    queue.push_back(w);
                }
                if distance[w.index()] == distance[v.index()] + 1 {
                    sigma[w.index()] += sigma[v.index()];
                    predecessors[w.index()].push(v);
                }
            }
        }
        let mut dependency = vec![0.0; size];
        while let Some(w) = stack.pop() {
            for v in &predecessors[w.index()] {
                dependency[v.index()] +=
                    sigma[v.index()] / sigma[w.index()] * (1.0 + dependency[w.index()]);
            }
            if w != source {
                scores[w.index()] += dependency[w.index()];
            }
        }
    }
    graph
        .node_indices()
        .map(|v| (graph[v].clone(), scores[v.index()]))
        .collect()
}

#[derive(Default)]
pub struct CandidateWindow {
    pub deadline: Option<Instant>,
    pub collecting: bool,
    pub preparing: bool,
    pub opened_at: Option<Instant>,
    pub armed_at: Option<Instant>,
    pub epoch: u64,
    pub slot: u64,
    pub max_hops: usize,
    pub validators: HashSet<String>,
    pub candidates: BTreeMap<String, Vec<Arc<TransactionPaths>>>,
    pub rejected: u64,
    pub capped: u64,
    pub offered: u64,
    pub accepted: u64,
    pub duplicates: u64,
    pub late: u64,
    pub verification_deadline: u64,
    pub already_committed: u64,
    pub arrival_log: Option<ArrivalLog>,
    pub admitted_prefixes: HashSet<(String, Vec<String>)>,
}

impl CandidateWindow {
    pub fn open(
        &mut self,
        epoch: u64,
        deadline: Instant,
        max_hops: usize,
        validators: HashSet<String>,
    ) {
        let arrival_log = self.arrival_log.clone();
        *self = Self {
            epoch,
            max_hops,
            validators,
            arrival_log,
            opened_at: Some(Instant::now()),
            armed_at: Some(Instant::now()),
            collecting: true,
            ..Self::default()
        };
        self.deadline = Some(deadline);
    }

    pub fn prepare(&mut self, epoch: u64, slot: u64, max_hops: usize, validators: HashSet<String>) {
        self.open(epoch, Instant::now(), max_hops, validators);
        self.slot = slot;
        self.deadline = None;
        self.armed_at = None;
        self.preparing = true;
    }

    pub fn arm(&mut self, duration: std::time::Duration) -> Instant {
        assert!(self.collecting && self.preparing);
        let armed_at = Instant::now();
        let deadline = armed_at + duration;
        self.armed_at = Some(armed_at);
        self.deadline = Some(deadline);
        self.preparing = false;
        deadline
    }

    fn record(
        &self,
        path: &TransactionPaths,
        receiver: &str,
        arrival: Instant,
        verification_ms: f64,
        disposition: &str,
    ) {
        if let Some(log) = &self.arrival_log {
            let event = CandidateArrival {
                epoch: self.epoch,
                slot: self.slot,
                receiver,
                tx_hash: &path.transaction.hash,
                nodes: path.node_sequence(),
                elapsed_ms: arrival
                    .duration_since(self.opened_at.unwrap())
                    .as_secs_f64()
                    * 1000.0,
                verification_ms,
                disposition,
            };
            let mut file = log.lock().unwrap();
            serde_json::to_writer(&mut *file, &event).unwrap();
            writeln!(file).unwrap();
        }
    }

    /// True means the active terminal window handled this message. Even late
    /// arrivals must not race the builder into signing a conflicting prefix.
    pub fn offer(&mut self, path: Arc<TransactionPaths>, receiver: &str) -> bool {
        let arrival = Instant::now();
        if !self.collecting {
            // Keep observing the last batch after cutoff. These messages
            // resume ordinary reception, but never enter that closed proposal.
            if self.opened_at.is_some()
                && path.transaction.data
                    == crate::network::world_state::logical_tx_metadata(self.epoch, self.slot)
                && !self
                    .admitted_prefixes
                    .contains(&(path.transaction.hash.clone(), path.node_sequence()))
            {
                self.record(&path, receiver, arrival, 0.0, "late_after_close");
            }
            return false;
        }
        self.offered += 1;
        if self.deadline.is_some_and(|deadline| arrival > deadline) {
            self.late += 1;
            self.record(&path, receiver, arrival, 0.0, "late_before_verification");
            return true;
        }
        if !path.verify_pending_prefix(receiver, self.epoch, self.max_hops)
            || path
                .node_sequence()
                .iter()
                .any(|v| !self.validators.contains(v))
        {
            self.rejected += 1;
            self.record(
                &path,
                receiver,
                arrival,
                arrival.elapsed().as_secs_f64() * 1000.0,
                "invalid",
            );
            return true;
        }
        // Cryptographic verification is part of the collection deadline.
        let verification_ms = arrival.elapsed().as_secs_f64() * 1000.0;
        if self
            .deadline
            .is_some_and(|deadline| Instant::now() > deadline)
        {
            self.verification_deadline += 1;
            self.record(
                &path,
                receiver,
                arrival,
                verification_ms,
                "verification_deadline",
            );
            return true;
        }
        if self.candidates.len() >= 4096 && !self.candidates.contains_key(&path.transaction.hash) {
            self.capped += 1;
            self.record(&path, receiver, arrival, verification_ms, "capped");
            return true;
        }
        let entries = self
            .candidates
            .entry(path.transaction.hash.clone())
            .or_default();
        let sequence = path.node_sequence();
        if entries.iter().any(|p| p.node_sequence() == sequence) {
            self.duplicates += 1;
            self.record(&path, receiver, arrival, verification_ms, "duplicate");
            return true;
        }
        if entries.len() >= 32 {
            self.capped += 1;
            self.record(&path, receiver, arrival, verification_ms, "capped");
        } else {
            entries.push(path.clone());
            self.admitted_prefixes
                .insert((path.transaction.hash.clone(), sequence));
            self.accepted += 1;
            self.record(&path, receiver, arrival, verification_ms, "accepted");
        }
        true
    }
}

pub fn coalition_gamma(path: &TransactionPaths, coalition: &HashSet<String>, depth: usize) -> f64 {
    let nodes = path.node_sequence();
    let hops = nodes.len().saturating_sub(1);
    (1..hops)
        .filter(|&i| coalition.contains(&nodes[i]))
        .map(|i| TrailConsensus::gamma_for_depth(depth, i, hops))
        .sum()
}

pub fn choose_path(
    candidates: &[Arc<TransactionPaths>],
    favoritism: bool,
    coalition: &HashSet<String>,
    depth: usize,
) -> usize {
    assert!(!candidates.is_empty());
    (0..candidates.len())
        .min_by(|&a, &b| {
            let bias = if favoritism {
                coalition_gamma(&candidates[b], coalition, depth).total_cmp(&coalition_gamma(
                    &candidates[a],
                    coalition,
                    depth,
                ))
            } else {
                std::cmp::Ordering::Equal
            };
            bias.then_with(|| candidates[a].paths.len().cmp(&candidates[b].paths.len()))
                .then_with(|| {
                    candidates[a]
                        .node_sequence()
                        .cmp(&candidates[b].node_sequence())
                })
        })
        .unwrap()
}

#[derive(Serialize, Deserialize, Debug)]
pub struct SelectionAudit {
    pub epoch: u64,
    pub slot: u64,
    pub proposer: String,
    pub coalition_proposer: bool,
    pub phase: String,
    pub applied_rule: String,
    pub tx_hash: String,
    pub candidate_count: usize,
    pub eligible: bool,
    pub would_change: bool,
    pub effective_change: bool,
    pub honest_path: Vec<String>,
    pub favoritism_path: Vec<String>,
    pub selected_path: Vec<String>,
    pub honest_coalition_relay_reward: f64,
    pub favoritism_coalition_relay_reward: f64,
    pub honest_coalition_raw_contribution: f64,
    pub favoritism_coalition_raw_contribution: f64,
    pub local_honest_relay_reward_loss: f64,
    pub candidate_prefixes: Vec<Arc<TransactionPaths>>,
    pub selected_certificate: AggregatedSignedPaths,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct EpochEconomics {
    pub epoch: u64,
    pub phase: String,
    pub selection: String,
    pub relay_reward: f64,
    pub proposer_reward: f64,
    pub transaction_cost: f64,
    pub modeled_net_income: f64,
    pub raw_contribution: f64,
    pub honest_relay_reward: f64,
    pub proposer_weight_share: f64,
    pub next_epoch_expected_base_proposer_reward: f64,
    pub actual_stake_share: f64,
    pub theoretical_weight_bound: f64,
    pub rejected_candidates: u64,
    pub capped_candidates: u64,
}

pub struct CoalitionExperiment {
    pub selection: CoalitionPathSelection,
    pub window: std::time::Duration,
    pub max_hops: usize,
    pub attack_start: u64,
    pub attack_end: u64,
    pub pools: HashMap<String, CandidatePool>,
    pub payments: PaymentLedger,
    pub pending_audits: Vec<SelectionAudit>,
    pub rejected: u64,
    pub capped: u64,
    pub workload_sender: Option<tokio::sync::mpsc::Sender<SlotWorkloadRequest>>,
    pub generation_batch: GenerationBatch,
    pub preparation_ms: f64,
    pub arrival_log: ArrivalLog,
    selection_file: File,
    economics_file: File,
    window_file: File,
}

impl CoalitionExperiment {
    pub fn new(
        directory: &Path,
        selection: CoalitionPathSelection,
        window: std::time::Duration,
        attack_start: u64,
        attack_end: u64,
    ) -> Self {
        let create = |name| {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(directory.join(name))
                .expect("coalition audit output must be new and writable")
        };
        Self {
            selection,
            window,
            max_hops: 16,
            attack_start,
            attack_end,
            pools: HashMap::new(),
            payments: Arc::new(Mutex::new(HashMap::new())),
            pending_audits: Vec::new(),
            rejected: 0,
            capped: 0,
            workload_sender: None,
            generation_batch: GenerationBatch::default(),
            preparation_ms: 0.0,
            arrival_log: Arc::new(Mutex::new(create("coalition_candidate_arrivals.jsonl"))),
            selection_file: create("coalition_path_selections.jsonl"),
            economics_file: create("coalition_epoch_economics.jsonl"),
            window_file: create("coalition_windows.jsonl"),
        }
    }

    pub fn phase(&self, epoch: u64) -> &'static str {
        if epoch < self.attack_start {
            "warmup"
        } else if epoch < self.attack_end {
            "attack"
        } else {
            "post"
        }
    }

    pub fn uses_favoritism(&self, epoch: u64, coalition_proposer: bool) -> bool {
        self.selection == CoalitionPathSelection::Favoritism
            && self.phase(epoch) == "attack"
            && coalition_proposer
    }

    pub fn accept_block(&mut self, block: &Block) {
        for audit in self.pending_audits.drain(..) {
            assert!(block
                .body
                .transactions
                .iter()
                .any(|tx| tx.hash == audit.tx_hash));
            serde_json::to_writer(&mut self.selection_file, &audit).unwrap();
            writeln!(self.selection_file).unwrap();
        }
        self.selection_file.flush().unwrap();
    }

    pub fn write_economics(&mut self, row: &EpochEconomics) {
        serde_json::to_writer(&mut self.economics_file, row).unwrap();
        writeln!(self.economics_file).unwrap();
        self.economics_file.flush().unwrap();
    }

    pub fn write_window(&mut self, window: &CandidateWindow, proposer: &str) {
        let event = serde_json::json!({
            "epoch": window.epoch, "slot": window.slot, "proposer": proposer,
            "generation": self.generation_batch, "preparation_ms": self.preparation_ms,
            "collection_ms": self.window.as_secs_f64() * 1000.0,
            "collection_started_ms": window.armed_at.map(|t| t.duration_since(window.opened_at.unwrap()).as_secs_f64() * 1000.0),
            "deadline_ms": window.deadline.map(|t| t.duration_since(window.opened_at.unwrap()).as_secs_f64() * 1000.0),
            "total_elapsed_ms": window.opened_at.unwrap().elapsed().as_secs_f64() * 1000.0,
            "offered": window.offered, "accepted": window.accepted,
            "duplicates": window.duplicates, "invalid": window.rejected, "capped": window.capped,
            "late_before_verification": window.late,
            "verification_deadline": window.verification_deadline,
            "already_committed": window.already_committed,
        });
        serde_json::to_writer(&mut self.window_file, &event).unwrap();
        writeln!(self.window_file).unwrap();
        self.window_file.flush().unwrap();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blockchain::transaction::Transaction;
    use crate::wallet::Wallet;
    use std::time::Duration;

    fn prefixes() -> (Wallet, Vec<Arc<TransactionPaths>>, HashSet<String>) {
        let origin = Wallet::new();
        let a = Wallet::new();
        let b = Wallet::new();
        let proposer = Wallet::new();
        let tx =
            Transaction::with_costs(proposer.address.clone(), 0, 0.001, 0.00001, origin.clone());
        let mut paths = Vec::new();
        for relay in [&a, &b] {
            let mut p = TransactionPaths::new_with_epoch(tx.clone(), 10);
            assert!(p.append_completed_hop(relay.address.clone(), origin.clone(), relay.clone()));
            assert!(p.append_outgoing_hop(proposer.address.clone(), relay.clone()));
            paths.push(Arc::new(p));
        }
        (
            proposer.clone(),
            paths,
            [origin.address, a.address, b.address, proposer.address].into(),
        )
    }

    #[test]
    fn coalition_candidates_validate_upstream_and_enforce_cutoff() {
        let (proposer, paths, validators) = prefixes();
        let mut window = CandidateWindow::default();
        assert!(!window.offer(paths[0].clone(), &proposer.address));
        window.open(10, Instant::now() + Duration::from_secs(5), 16, validators);
        let mut forged = paths[0].as_ref().clone();
        forged.paths[0].receiver_signature = Some("invalid".to_string());
        assert!(window.offer(Arc::new(forged), &proposer.address));
        let mut wrong_epoch = paths[0].as_ref().clone();
        wrong_epoch.epoch += 1;
        window.offer(Arc::new(wrong_epoch), &proposer.address);
        assert_eq!(window.rejected, 2);
        assert!(window.candidates.is_empty());
        for path in &paths {
            window.offer(path.clone(), &proposer.address);
        }
        window.offer(paths[0].clone(), &proposer.address);
        assert_eq!(window.candidates[&paths[0].transaction.hash].len(), 2);
        window.candidates.clear();
        window.deadline = Some(Instant::now() - Duration::from_secs(1));
        assert!(window.offer(paths[0].clone(), &proposer.address));
        assert!(window.candidates.is_empty());
    }

    #[test]
    fn coalition_selection_preserves_one_receipt_and_stable_ties() {
        let (proposer, paths, _) = prefixes();
        let coalition: HashSet<String> =
            [paths[1].paths[0].to.clone(), proposer.address.clone()].into();
        assert_eq!(choose_path(&paths, true, &coalition, 4), 1);
        let mut reversed = paths.clone();
        reversed.reverse();
        let honest = choose_path(&paths, false, &coalition, 4);
        let reverse_honest = choose_path(&reversed, false, &coalition, 4);
        assert_eq!(
            paths[honest].node_sequence(),
            reversed[reverse_honest].node_sequence()
        );
        assert_eq!(choose_path(&paths[..1], true, &coalition, 4), 0);
        let mut chosen = paths[1].as_ref().clone();
        assert!(chosen.complete_pending_hop(proposer.clone()));
        let mut other = paths[0].as_ref().clone();
        assert!(!other.complete_pending_hop(proposer));
    }

    #[tokio::test]
    async fn coalition_preparation_does_not_consume_the_fixed_collection_window() {
        let (proposer, paths, validators) = prefixes();
        let mut window = CandidateWindow::default();
        window.prepare(10, 3, 16, validators);
        assert!(window.offer(paths[0].clone(), &proposer.address));
        tokio::time::sleep(Duration::from_millis(40)).await;
        let deadline = window.arm(Duration::from_millis(50));
        assert!(deadline.duration_since(Instant::now()) > Duration::from_millis(45));
        assert!(window.offer(paths[1].clone(), &proposer.address));
        assert_eq!(window.candidates[&paths[0].transaction.hash].len(), 2);
        window.deadline = Some(Instant::now() - Duration::from_millis(1));
        window.offer(paths[0].clone(), &proposer.address);
        assert_eq!(window.late, 1);
        window.collecting = false;
        assert!(!window.offer(paths[0].clone(), &proposer.address));
    }

    #[tokio::test]
    async fn coalition_generation_completion_acknowledges_success_and_early_failure() {
        let (reply, completion) = tokio::sync::oneshot::channel();
        drop(GenerationCompletion::new(Some(reply)));
        assert!(!completion.await.unwrap());
        let (reply, completion) = tokio::sync::oneshot::channel();
        let mut guard = GenerationCompletion::new(Some(reply));
        guard.generated = true;
        drop(guard);
        assert!(completion.await.unwrap());
    }

    #[test]
    fn coalition_betweenness_handles_equal_shortest_paths_and_insertion_order() {
        let build = |reverse: bool| {
            let mut graph = petgraph::Graph::<String, ()>::new();
            let labels = if reverse {
                ["d", "c", "b", "a"]
            } else {
                ["a", "b", "c", "d"]
            };
            let nodes: HashMap<_, _> = labels
                .into_iter()
                .map(|n| (n, graph.add_node(n.to_string())))
                .collect();
            let mut edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")];
            if reverse {
                edges.reverse();
            }
            for (a, b) in edges {
                graph.add_edge(nodes[a], nodes[b], ());
            }
            graph
        };
        let scores = betweenness(&build(false));
        assert_eq!(scores, betweenness(&build(true)));
        assert!(scores.values().all(|v| (*v - 1.0).abs() < 1e-12));
    }
}
