#[path = "../src/bin/block_validator_compression/codec.rs"]
mod codec;

use codec::{
    commitment, decode, decode_committed, dictionary_entries, encode, local_width, Error, Mode,
    PathRecord, Scheme,
};
use std::sync::Arc;
use trail::blockchain::{
    block::{Block, Body},
    path::TransactionPaths,
    transaction::Transaction,
};
use trail::consensus::{
    trail::{TrailConfig, TrailConsensus},
    Consensus, Validator, ValidatorRegistry,
};
use trail::wallet::Wallet;

fn record(indices: Vec<u64>, signature_byte: u8) -> PathRecord {
    PathRecord {
        signature: (!indices.is_empty()).then_some([signature_byte; 48]),
        indices,
    }
}

#[test]
fn adaptive_picks_the_smallest_frame_and_u32_falls_back_without_truncation() {
    let wide = u64::from(u32::MAX) + 1;
    for (records, winner) in [
        (vec![record(vec![u64::from(u32::MAX)], 1)], Mode::U32),
        (vec![record(vec![wide], 1)], Mode::U64),
        (
            (0..30).map(|i| record(vec![wide, 3, 1], i)).collect(),
            Mode::Dictionary,
        ),
        (vec![record(vec![], 0)], Mode::U8),
        (vec![], Mode::U8),
        // H = 4 and K = 1 would make dictionary/fixed tie, but this evidence has four
        // separate valid one-hop records. A tie selects the fixed mode.
        ((0..4).map(|i| record(vec![256], i)).collect(), Mode::U32),
    ] {
        let sizes: Vec<_> = Scheme::ALL
            .iter()
            .map(|&scheme| {
                let encoded = encode(&records, scheme).unwrap();
                assert_eq!(decode(&encoded.bytes, |_| true).unwrap(), records);
                assert_eq!(
                    encoded.bytes,
                    encode(&records, scheme).unwrap().bytes,
                    "{} is deterministic",
                    scheme.name()
                );
                if scheme == Scheme::Adaptive {
                    assert_eq!(
                        encoded.mode,
                        winner,
                        "adaptive chose {}",
                        encoded.mode.name()
                    );
                }
                if scheme == Scheme::Fixed4 {
                    let expected = if records.iter().flat_map(|p| &p.indices).any(|&i| i >= wide) {
                        Mode::U64
                    } else {
                        Mode::U32
                    };
                    assert_eq!(encoded.mode, expected);
                }
                if scheme == Scheme::Global {
                    let max = records
                        .iter()
                        .flat_map(|p| &p.indices)
                        .copied()
                        .max()
                        .unwrap_or(0);
                    let expected = match max {
                        0..=255 => Mode::U8,
                        value if value < wide => Mode::U32,
                        _ => Mode::U64,
                    };
                    assert_eq!(encoded.mode, expected);
                }
                encoded.bytes.len()
            })
            .collect();
        let (adaptive, candidates) = sizes.split_last().unwrap();
        assert_eq!(Some(adaptive), candidates.iter().min());
    }
    let four_hops = [record(vec![10, 20, 30, 40], 11)];
    let baseline = encode(&four_hops, Scheme::Baseline).unwrap();
    let fixed = encode(&four_hops, Scheme::Fixed4).unwrap();
    let global = encode(&four_hops, Scheme::Global).unwrap();
    let dictionary = encode(&four_hops, Scheme::Dictionary).unwrap();
    assert_eq!((baseline.core_bytes, fixed.core_bytes), (80, 64));
    assert_eq!(
        (global.mode, global.core_bytes, global.bytes.len()),
        (Mode::U8, 52, 59)
    );
    assert_eq!(
        (
            baseline.bytes.len(),
            fixed.bytes.len(),
            dictionary.bytes.len()
        ),
        (87, 71, 95)
    );
}

#[test]
fn global_255_fits_one_byte_and_256_requires_four() {
    let at_limit = [record(vec![255, 0], 0x42)];
    let above_limit = [record(vec![255, 256], 0x42)];
    for scheme in [Scheme::Global, Scheme::Adaptive] {
        let encoded = encode(&at_limit, scheme).unwrap();
        assert_eq!((encoded.mode, encoded.bytes[0]), (Mode::U8, 3));
        assert_eq!(&encoded.bytes[55..], &[255, 0]);
        assert_eq!(decode(&encoded.bytes, |id| id <= 255).unwrap(), at_limit);

        let encoded = encode(&above_limit, scheme).unwrap();
        assert_eq!((encoded.mode, encoded.bytes[0]), (Mode::U32, 1));
        assert_eq!(&encoded.bytes[55..], &[255, 0, 0, 0, 0, 1, 0, 0]);
        assert_eq!(decode(&encoded.bytes, |id| id <= 256).unwrap(), above_limit);
        assert_eq!(
            decode(&encoded.bytes, |id| id <= 255),
            Err(Error::UnknownIndex)
        );
    }
    let repeated = vec![above_limit[0].clone(); 16];
    let encoded = encode(&repeated, Scheme::Adaptive).unwrap();
    assert_eq!(encoded.mode, Mode::Dictionary);
    assert_eq!(encoded.core_bytes, 16 * 48 + 32 + 2 * 8);
    assert_eq!(decode(&encoded.bytes, |id| id <= 256).unwrap(), repeated);
}

#[test]
fn dictionary_width_boundaries_round_trip_the_largest_local_index() {
    for (k, width) in [(256, 1), (257, 2), (65_536, 2), (65_537, 4)] {
        let records = (0..k)
            .step_by(8)
            .map(|start| {
                // The global values all exceed u32. Local indices still use the smallest width.
                record(
                    (start..(start + 8).min(k))
                        .rev()
                        .map(|i| (1_u64 << 40) + i as u64)
                        .collect(),
                    0x45,
                )
            })
            .collect::<Vec<_>>();
        let encoded = encode(&records, Scheme::Dictionary).unwrap();
        assert_eq!(local_width(k), width);
        assert_eq!(encoded.core_bytes, 48 * records.len() + (8 + width) * k);
        assert_eq!(
            encoded.bytes.len(),
            encoded.core_bytes + 9 + 2 * records.len()
        );
        assert_eq!(
            decode(&encoded.bytes, |id| ((1_u64 << 40)
                ..(1_u64 << 40) + k as u64)
                .contains(&id))
            .unwrap(),
            records
        );
    }
}

#[test]
fn malformed_wire_and_unregistered_indices_are_rejected() {
    let records = [
        record(vec![400, 101], 1),
        record(vec![], 0),
        record(vec![101, 201], 2),
    ];
    let valid_id = |id| [101, 201, 400].contains(&id);
    let encoded = encode(&records, Scheme::Dictionary).unwrap();
    let bytes = &encoded.bytes;
    assert_eq!(&bytes[9..17], &101_u64.to_le_bytes());
    assert_eq!(&bytes[17..25], &201_u64.to_le_bytes());
    assert_eq!(&bytes[25..33], &400_u64.to_le_bytes());
    assert_eq!(decode(bytes, valid_id).unwrap(), records);
    for end in 0..bytes.len() {
        assert!(decode(&bytes[..end], valid_id).is_err());
    }

    let mut corrupt = bytes.clone();
    corrupt[0] = 9;
    assert_eq!(decode(&corrupt, valid_id), Err(Error::InvalidMode));
    corrupt = bytes.clone();
    corrupt[9..17].copy_from_slice(&201_u64.to_le_bytes());
    assert_eq!(decode(&corrupt, valid_id), Err(Error::InvalidDictionary));
    corrupt = bytes.clone();
    corrupt[33 + 2 + 48] = 3; // Local index K is out of range.
    assert_eq!(decode(&corrupt, valid_id), Err(Error::UnknownLocalIndex));
    corrupt[33 + 2 + 48] = 0; // Leaves the last dictionary entry unused.
    assert_eq!(decode(&corrupt, valid_id), Err(Error::InvalidDictionary));
    corrupt = bytes.clone();
    corrupt.push(0);
    assert_eq!(decode(&corrupt, valid_id), Err(Error::TrailingBytes));
    for scheme in Scheme::ALL {
        let bytes = encode(&records, scheme).unwrap().bytes;
        assert_eq!(decode(&bytes, |id| id < 400), Err(Error::UnknownIndex));
    }
    assert!(matches!(
        encode(
            &[PathRecord {
                indices: vec![4],
                signature: None
            }],
            Scheme::Baseline
        ),
        Err(Error::InvalidPath)
    ));
}

fn fixture() -> (Block, Arc<ValidatorRegistry>, Vec<Validator>) {
    let wallets: Vec<_> = (0..8)
        .map(|i| Wallet::new_deterministic(0x434f4d5052455353, i))
        .collect();
    let proposer = &wallets[0];
    let registry = Arc::new(ValidatorRegistry::from_wallets(&wallets).unwrap());
    let mut transactions = Vec::new();
    let mut certificates = Vec::new();
    for (tx_number, route) in [
        vec![1, 5, 2, 0],
        vec![0],
        vec![3, 5, 0],
        vec![1, 4, 5, 2, 0],
    ]
    .into_iter()
    .enumerate()
    {
        let tx = Transaction::with_timestamp(
            proposer.address.clone(),
            27 + tx_number as i64,
            0.8,
            0.9,
            wallets[route[0]].clone(),
            false,
            1_700_000_001,
        );
        let mut route_proof = TransactionPaths::new_with_epoch(tx.clone(), 0);
        for edge in route.windows(2) {
            assert!(route_proof.append_completed_hop(
                wallets[edge[1]].address.clone(),
                wallets[edge[0]].clone(),
                wallets[edge[1]].clone()
            ));
        }
        certificates.push(route_proof.to_aggregated_signed_paths(&registry).unwrap());
        transactions.push(tx);
    }
    let block = Block::new(
        1,
        0,
        1,
        "fixture_parent".into(),
        Body::new(transactions, certificates),
        proposer.clone(),
    )
    .unwrap();
    let validators = wallets
        .iter()
        .enumerate()
        .map(|(i, w)| Validator::new(w.address.clone(), (i + 3) as f64, 1.0))
        .collect();
    (block, registry, validators)
}

fn restore(block: &Block, records: Vec<PathRecord>) -> Block {
    assert_eq!(records.len(), block.body.paths.len());
    let mut result = block.clone();
    for (record, path) in records.into_iter().zip(&mut result.body.paths) {
        *path = record.into_certificate(path.chain_id, path.epoch);
    }
    result
}

#[test]
fn certificate_signatures_rewards_scores_and_proposer_weights_are_exact() {
    let (block, registry, validators) = fixture();
    let original = block
        .body
        .paths
        .iter()
        .map(PathRecord::from_certificate)
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    let ledger_state = |block: &Block| {
        let mut consensus =
            TrailConsensus::new(0.1, TrailConfig::default(), registry.clone()).unwrap();
        consensus.enable_deterministic_reductions();
        let plan = consensus.compute_block_rewards(block, &validators);
        assert!(plan.relay_reward > 0.0);
        consensus.on_epoch_end(std::slice::from_ref(block), &validators);
        consensus.on_epoch_end(&[], &validators);
        let scores = consensus.metrics_snapshot();
        assert!(scores.score_history.values().any(|&score| score > 0.0));
        assert!(scores.bonuses.values().any(|&bonus| bonus > 0.0));
        (
            plan.rewards,
            [
                plan.proposer_reward,
                plan.relay_reward,
                plan.burned_relay_fee,
                plan.total_fee,
            ],
            scores.latest_score_history,
            scores.score_history,
            scores.unnormalized_proposer_weights,
            scores.normalized_proposer_weights,
        )
    };
    let expected = ledger_state(&block);
    let hash = hex::decode(&block.header.hash).unwrap().try_into().unwrap();
    for scheme in Scheme::ALL {
        let wire = encode(&original, scheme).unwrap();
        let digest = commitment(&hash, &wire.bytes);
        let restored = decode_committed(&wire.bytes, &hash, &digest, |id| {
            registry.address(id).is_ok()
        })
        .unwrap();
        assert_eq!(restored, original);
        let block_after = restore(&block, restored);
        assert_eq!(block_after.to_json(), block.to_json());
        assert!(block_after.verify_base());
        for (certificate, tx) in block_after
            .body
            .paths
            .iter()
            .zip(&block_after.body.transactions)
        {
            assert!(certificate.verify_at_epoch_uncached(
                tx,
                &block.header.miner,
                block.header.epoch,
                &registry
            ));
        }
        assert_eq!(ledger_state(&block_after), expected);
    }
}

#[test]
fn commitment_authenticates_dictionary_path_bytes_and_block_context() {
    let (block, registry, _) = fixture();
    let original = block
        .body
        .paths
        .iter()
        .map(PathRecord::from_certificate)
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    let wire = encode(&original, Scheme::Dictionary).unwrap().bytes;
    let hash = hex::decode(&block.header.hash).unwrap().try_into().unwrap();
    let trusted = commitment(&hash, &wire);
    let valid_id = |id| registry.address(id).is_ok();
    let keys: Vec<_> = dictionary_entries(&original).into_iter().collect();
    let proposer = registry.index(&block.header.miner).unwrap();
    let replacement = registry
        .entries()
        .map(|(index, _, _)| index)
        .find(|id| *id != proposer && !keys.contains(id))
        .unwrap();
    let position = keys
        .partition_point(|&id| id < replacement)
        .min(keys.len() - 1);
    let mut altered = wire.clone();
    altered[9 + 8 * position..17 + 8 * position].copy_from_slice(&replacement.to_le_bytes());
    assert_eq!(
        decode_committed(&altered, &hash, &trusted, valid_id),
        Err(Error::CommitmentMismatch)
    );
    let syntactically_valid = decode(&altered, valid_id).unwrap();
    let altered_block = restore(&block, syntactically_valid);
    assert!(!altered_block.verify_base());
    assert!(altered_block
        .body
        .paths
        .iter()
        .zip(&block.body.transactions)
        .any(|(certificate, tx)| {
            certificate.signature.len() > 0
                && !certificate.verify_at_epoch_uncached(tx, &block.header.miner, 0, &registry)
        }));

    let signature_offset = 9 + 8 * keys.len() + 2;
    let local_offset = signature_offset + 48;
    for offset in [signature_offset, local_offset] {
        let mut changed = wire.clone();
        changed[offset] ^= 1;
        assert_eq!(
            decode_committed(&changed, &hash, &trusted, valid_id),
            Err(Error::CommitmentMismatch)
        );
    }
    assert_eq!(
        decode_committed(&wire, &[9; 32], &trusted, valid_id),
        Err(Error::CommitmentMismatch)
    );
}
