//! Opt-in local profiling of real BLS certificates, without starting a devnet.
use super::*;
use std::{fs::OpenOptions, hint::black_box};

#[derive(Default, Debug)]
struct Timings {
    signature_decode_ns: u128,
    key_lookup_ns: u128,
    key_decode_ns: u128,
    message_build_ns: u128,
    bls_verify_ns: u128,
}

fn measure<const PROFILE: bool, T>(elapsed: &mut u128, action: impl FnOnce() -> T) -> T {
    if PROFILE {
        let started = Instant::now();
        let result = action();
        *elapsed = elapsed.saturating_add(started.elapsed().as_nanos());
        result
    } else {
        action()
    }
}

// Frozen reference for the first per-path implementation. Retained only in tests
// so the optimized verifier can be compared in the same binary on identical inputs.
fn reference_signature_records<E: EthSpec>(
    record: &TrailInlineEvidenceRecord<E>,
    registry: &TrailRelayPublicRegistry,
    chain_id: u64,
) -> Option<Vec<TrailSignatureRecord>> {
    let tx_hash = *record.tx_hash.as_ref();
    let path = record.relay_path.iter().copied().collect::<Vec<_>>();
    let unique: HashSet<_> = path.iter().copied().collect();
    if path.is_empty() || unique.len() != path.len() {
        return None;
    }
    let addresses = path
        .iter()
        .map(|id| registry.address_for_validator(*id, None))
        .collect::<Vec<_>>();
    if addresses.iter().collect::<HashSet<_>>().len() != addresses.len()
        || addresses
            .iter()
            .any(|address| registry.pubkey(address).is_none())
    {
        return None;
    }
    let origin = addresses.first()?;
    let mut records = vec![TrailSignatureRecord {
        signer: origin.clone(),
        statement: origin_statement(chain_id, record.epoch, &tx_hash, origin),
        signature: [0; 48],
    }];
    for (index, edge) in addresses.windows(2).enumerate() {
        let [from, to] = edge else { return None };
        let prefix = chain_value(&tx_hash, chain_id, record.epoch, &addresses, index);
        let statement = edge_statement(&prefix, from, to);
        records.push(TrailSignatureRecord {
            signer: from.clone(),
            statement,
            signature: [0; 48],
        });
        records.push(TrailSignatureRecord {
            signer: to.clone(),
            statement,
            signature: [0; 48],
        });
    }
    Some(records)
}

fn reference_verify<const PROFILE: bool, E: EthSpec>(
    records: &[TrailInlineEvidenceRecord<E>],
    registry: &TrailRelayPublicRegistry,
    spec: &ChainSpec,
    timings: &mut Timings,
) -> bool {
    let work = records.iter().fold(0u64, |work, record| {
        work.saturating_add(record.relay_path.len() as u64)
    });
    if records.len() > spec.trail_config.max_paths_per_block as usize
        || work > spec.trail_config.evidence_work_limit
    {
        return false;
    }
    let mut transactions = HashSet::new();
    for record in records {
        if record.relay_path.is_empty()
            || record.relay_path.len() > spec.trail_config.max_path_evidence_len as usize
            || !transactions.insert(record.tx_hash)
        {
            return false;
        }
        let Some(bytes) = trail_inline_aggregate_signature(record) else {
            return false;
        };
        let Some(statements) = measure::<PROFILE, _>(&mut timings.message_build_ns, || {
            reference_signature_records(record, registry, spec.deposit_chain_id)
        }) else {
            return false;
        };
        let Ok(signature) = measure::<PROFILE, _>(&mut timings.signature_decode_ns, || {
            blst::min_sig::Signature::from_bytes(&bytes)
        }) else {
            return false;
        };
        let mut pubkeys = Vec::with_capacity(statements.len());
        for statement in &statements {
            let Some(bytes) = measure::<PROFILE, _>(&mut timings.key_lookup_ns, || {
                registry.pubkey(&statement.signer)
            }) else {
                return false;
            };
            let Ok(key) = measure::<PROFILE, _>(&mut timings.key_decode_ns, || {
                blst::min_sig::PublicKey::from_bytes(&bytes)
            }) else {
                return false;
            };
            pubkeys.push(key);
        }
        let messages = statements
            .iter()
            .map(|s| s.statement.as_slice())
            .collect::<Vec<_>>();
        let keys = pubkeys.iter().collect::<Vec<_>>();
        let valid = measure::<PROFILE, _>(&mut timings.bls_verify_ns, || {
            signature.aggregate_verify(
                true,
                &messages,
                TRAIL_TX_PATH_DOMAIN.as_bytes(),
                &keys,
                false,
            ) == blst::BLST_ERROR::BLST_SUCCESS
        });
        if !valid {
            return false;
        }
    }
    true
}

fn fixture(
    paths: usize,
    hops: usize,
) -> (
    Vec<TrailInlineEvidenceRecord<MinimalEthSpec>>,
    TrailRelayPublicRegistry,
    ChainSpec,
) {
    let mut spec = ChainSpec::minimal();
    spec.deposit_chain_id = 7_032_030;
    spec.trail_config = TrailConfig::devnet_enabled_at(Epoch::new(0));
    let epoch = 9;
    let mut registry = TrailRelayPublicRegistry::default();
    let secrets = (0..=hops)
        .map(|index| {
            blst::min_sig::SecretKey::key_gen(
                &hash(format!("TRAIL local verification fixture key {index}").as_bytes()),
                &[],
            )
            .unwrap()
        })
        .collect::<Vec<_>>();
    let addresses = (0..=hops)
        .map(|index| {
            let address = fallback_trail_relay_address(index as u64);
            registry
                .validator_addresses
                .insert(index as u64, address.clone());
            registry.pubkeys_by_address.insert(
                address.clone(),
                secrets[index].sk_to_pk().to_bytes().to_vec(),
            );
            address
        })
        .collect::<Vec<_>>();
    let records = (0..paths)
        .map(|index| {
            let tx_hash = sha256_array(format!("TRAIL local transaction {index}").as_bytes());
            let origin = origin_statement(spec.deposit_chain_id, epoch, &tx_hash, &addresses[0]);
            let mut signatures =
                vec![secrets[0].sign(&origin, TRAIL_TX_PATH_DOMAIN.as_bytes(), &[])];
            for edge in 0..hops {
                let prefix = chain_value(&tx_hash, spec.deposit_chain_id, epoch, &addresses, edge);
                let message = edge_statement(&prefix, &addresses[edge], &addresses[edge + 1]);
                signatures.push(secrets[edge].sign(&message, TRAIL_TX_PATH_DOMAIN.as_bytes(), &[]));
                signatures.push(secrets[edge + 1].sign(
                    &message,
                    TRAIL_TX_PATH_DOMAIN.as_bytes(),
                    &[],
                ));
            }
            let signature = blst::min_sig::AggregateSignature::aggregate(
                &signatures.iter().collect::<Vec<_>>(),
                true,
            )
            .unwrap()
            .to_signature()
            .to_bytes();
            TrailInlineEvidenceRecord {
                tx_hash: Hash256::from_slice(&tx_hash),
                epoch,
                priority_fee_wei: 42_000,
                irrecoverable_cost_wei: 21_000,
                relay_path: ssz_types::VariableList::new((0..=hops as u64).collect()).unwrap(),
                aggregate_signature: ssz_types::FixedVector::new(signature.to_vec()).unwrap(),
            }
        })
        .collect();
    (records, registry, spec)
}

fn optimized_profile<E: EthSpec>(
    records: &[TrailInlineEvidenceRecord<E>],
    registry: &TrailRelayPublicRegistry,
    spec: &ChainSpec,
    timings: &mut Timings,
) -> bool {
    let work = records
        .iter()
        .fold(0u64, |n, r| n.saturating_add(r.relay_path.len() as u64));
    if records.len() > spec.trail_config.max_paths_per_block as usize
        || work > spec.trail_config.evidence_work_limit
    {
        return false;
    }
    let mut transactions = HashSet::new();
    for record in records {
        if record.relay_path.is_empty()
            || record.relay_path.len() > spec.trail_config.max_path_evidence_len as usize
            || !transactions.insert(record.tx_hash)
        {
            return false;
        }
        let Some(bytes) = trail_inline_aggregate_signature(record) else {
            return false;
        };
        let Ok(signature) = measure::<true, _>(&mut timings.signature_decode_ns, || {
            blst::min_sig::Signature::from_bytes(&bytes)
        }) else {
            return false;
        };
        let Some(keys) = measure::<true, _>(&mut timings.key_lookup_ns, || {
            trail_inline_path_keys(record, registry)
        }) else {
            return false;
        };
        let Some((statements, pubkeys)) = measure::<true, _>(&mut timings.message_build_ns, || {
            trail_inline_path_messages(record, &keys, spec.deposit_chain_id)
        }) else {
            return false;
        };
        let messages = statements.iter().map(|s| s.as_slice()).collect::<Vec<_>>();
        if !measure::<true, _>(&mut timings.bls_verify_ns, || {
            signature.aggregate_verify(
                true,
                &messages,
                TRAIL_TX_PATH_DOMAIN.as_bytes(),
                &pubkeys,
                false,
            ) == blst::BLST_ERROR::BLST_SUCCESS
        }) {
            return false;
        }
    }
    true
}

#[test]
fn trail_cached_preparation_matches_reference_messages_and_keys() {
    for hops in 0..16 {
        let (records, registry, spec) = fixture(2, hops);
        assert!(verify_trail_inline_path_certificates(
            &records, &registry, &spec
        ));
        assert!(reference_verify::<false, _>(
            &records,
            &registry,
            &spec,
            &mut Timings::default()
        ));
        for record in &records {
            let reference =
                reference_signature_records(record, &registry, spec.deposit_chain_id).unwrap();
            let keys = trail_inline_path_keys(record, &registry).unwrap();
            let (statements, pubkeys) =
                trail_inline_path_messages(record, &keys, spec.deposit_chain_id).unwrap();
            assert_eq!(
                statements,
                reference.iter().map(|r| r.statement).collect::<Vec<_>>()
            );
            assert_eq!(
                pubkeys.iter().map(|p| p.to_bytes()).collect::<Vec<_>>(),
                reference
                    .iter()
                    .map(|r| blst::min_sig::PublicKey::from_bytes(
                        &registry.pubkey(&r.signer).unwrap()
                    )
                    .unwrap()
                    .to_bytes())
                    .collect::<Vec<_>>()
            );
        }
    }
}

#[test]
fn trail_cache_rejects_malformed_infinite_alias_and_wrong_registry_keys() {
    for bad_key in [vec![0; 96], {
        let mut key = vec![0; 96];
        key[0] = 0xc0;
        key
    }] {
        let (records, mut registry, spec) = fixture(1, 1);
        registry
            .pubkeys_by_address
            .insert(fallback_trail_relay_address(1), bad_key);
        assert!(!verify_trail_inline_path_certificates(
            &records, &registry, &spec
        ));
    }
    let (records, mut alias, spec) = fixture(1, 1);
    alias
        .validator_addresses
        .insert(1, fallback_trail_relay_address(0));
    assert!(!verify_trail_inline_path_certificates(
        &records, &alias, &spec
    ));
    let (_, mut other, _) = fixture(1, 1);
    let replacement = blst::min_sig::SecretKey::key_gen(&[88; 32], &[]).unwrap();
    other.pubkeys_by_address.insert(
        fallback_trail_relay_address(1),
        replacement.sk_to_pk().to_bytes().to_vec(),
    );
    assert!(!verify_trail_inline_path_certificates(
        &records, &other, &spec
    ));
    // A curve point may decode successfully while lying outside the prime-order
    // subgroup. Registry caching must validate it before reusing it.
    let bad_subgroup = (1u16..256)
        .find_map(|index| {
            let mut bytes = vec![0; 96];
            bytes[0] = 0x80;
            bytes[94..].copy_from_slice(&index.to_be_bytes());
            let key = blst::min_sig::PublicKey::from_bytes(&bytes).ok()?;
            key.validate().err().map(|_| bytes)
        })
        .expect("test should find a decodable non-subgroup G2 point");
    let (records, mut registry, spec) = fixture(1, 1);
    registry
        .pubkeys_by_address
        .insert(fallback_trail_relay_address(1), bad_subgroup);
    assert!(!verify_trail_inline_path_certificates(
        &records, &registry, &spec
    ));
}

#[test]
fn trail_per_path_checks_reject_cancelling_invalid_signatures() {
    let (mut records, registry, spec) = fixture(2, 3);
    let mut positive_bytes = [0; 32];
    positive_bytes[31] = 1;
    let positive = blst::min_sig::SecretKey::from_bytes(&positive_bytes).unwrap();
    let negative = blst::min_sig::SecretKey::from_bytes(
        &hex_bytes_exact::<32>("73eda753299d7d483339d80809a1d80553bda402fffe5bfeffffffff00000000")
            .unwrap(),
    )
    .unwrap();
    let delta = positive.sign(
        b"cancellation test delta",
        TRAIL_TX_PATH_DOMAIN.as_bytes(),
        &[],
    );
    let minus_delta = negative.sign(
        b"cancellation test delta",
        TRAIL_TX_PATH_DOMAIN.as_bytes(),
        &[],
    );
    for (record, offset) in records.iter_mut().zip([delta, minus_delta]) {
        let signature =
            blst::min_sig::Signature::from_bytes(record.aggregate_signature.as_ref()).unwrap();
        let mut altered = blst::min_sig::AggregateSignature::from_signature(&signature);
        altered.add_signature(&offset, true).unwrap();
        record.aggregate_signature =
            ssz_types::FixedVector::new(altered.to_signature().to_bytes().to_vec()).unwrap();
        assert!(!verify_trail_inline_path_certificates(
            &[record.clone()],
            &registry,
            &spec
        ));
    }
    assert!(!verify_trail_inline_path_certificates(
        &records, &registry, &spec
    ));
    let signatures = records
        .iter()
        .map(|r| blst::min_sig::Signature::from_bytes(r.aggregate_signature.as_ref()).unwrap())
        .collect::<Vec<_>>();
    let aggregate =
        blst::min_sig::AggregateSignature::aggregate(&signatures.iter().collect::<Vec<_>>(), true)
            .unwrap()
            .to_signature();
    let statements = records
        .iter()
        .flat_map(|record| {
            reference_signature_records(record, &registry, spec.deposit_chain_id).unwrap()
        })
        .collect::<Vec<_>>();
    // The same two invalid certificates pass a naive cross-path sum. This is
    // deliberately a negative control, never a production acceptance method.
    assert!(verify_trail_aggregate_signature_bytes(
        &registry,
        &aggregate.to_bytes(),
        &statements
    ));
}

#[test]
fn trail_optimized_verifier_matches_reference_on_tampered_certificates() {
    let (records, registry, spec) = fixture(3, 3);
    for index in 0..records.len() {
        for field in 0..5 {
            let mut mutated = records.clone();
            match field {
                0 => mutated[index].epoch = mutated[index].epoch.saturating_add(1),
                1 => mutated[index].tx_hash = Hash256::from_slice(&[77; 32]),
                2 => {
                    mutated[index].relay_path = ssz_types::VariableList::new(vec![0, 1, 0]).unwrap()
                }
                3 => {
                    mutated[index].aggregate_signature =
                        ssz_types::FixedVector::new(vec![0; 48]).unwrap()
                }
                _ => {
                    mutated[index].relay_path =
                        ssz_types::VariableList::new(vec![0, 1, 2, 99]).unwrap()
                }
            }
            assert!(!verify_trail_inline_path_certificates(
                &mutated, &registry, &spec
            ));
            assert!(!reference_verify::<false, _>(
                &mutated,
                &registry,
                &spec,
                &mut Timings::default()
            ));
        }
    }
}

#[test]
#[ignore = "local release-mode profiling only; requires TRAIL_VERIFY_PROFILE_CSV"]
fn trail_profile_path_verification() {
    let output = std::env::var("TRAIL_VERIFY_PROFILE_CSV").expect("set a fresh CSV output path");
    let repeats: usize = std::env::var("TRAIL_VERIFY_PROFILE_REPEATS")
        .unwrap_or_else(|_| "30".into())
        .parse()
        .unwrap();
    let mut csv = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(output)
        .unwrap();
    writeln!(csv,"algorithm,paths,hops,iteration,wall_ns,profiled_total_ns,signature_decode_ns,key_lookup_ns,key_decode_ns,message_build_ns,bls_verify_ns").unwrap();
    let cases = if std::env::var_os("TRAIL_VERIFY_PROFILE_QUICK").is_some() {
        vec![(16, 3)]
    } else {
        vec![
            (16, 0),
            (16, 1),
            (16, 3),
            (64, 1),
            (64, 3),
            (256, 1),
            (256, 3),
            (64, 15),
        ]
    };
    for (paths, hops) in cases {
        let (records, registry, spec) = fixture(paths, hops);
        let cold_started = Instant::now();
        assert!(verify_trail_inline_path_certificates(
            &records, &registry, &spec
        ));
        let cold_ns = cold_started.elapsed().as_nanos();
        writeln!(csv, "optimized_cold,{paths},{hops},0,{cold_ns},0,0,0,0,0,0").unwrap();
        for _ in 0..4 {
            assert!(verify_trail_inline_path_certificates(
                &records, &registry, &spec
            ));
        }
        for iteration in 0..repeats {
            // Alternate ordering to reduce systematic drift and shared-pool effects.
            for optimized in if iteration % 2 == 0 {
                [false, true]
            } else {
                [true, false]
            } {
                let started = Instant::now();
                let valid = if optimized {
                    verify_trail_inline_path_certificates(black_box(&records), &registry, &spec)
                } else {
                    reference_verify::<false, _>(
                        black_box(&records),
                        &registry,
                        &spec,
                        &mut Timings::default(),
                    )
                };
                assert!(black_box(valid));
                let wall = started.elapsed().as_nanos();
                let mut times = Timings::default();
                let started = Instant::now();
                assert!(if optimized {
                    optimized_profile(&records, &registry, &spec, &mut times)
                } else {
                    reference_verify::<true, _>(&records, &registry, &spec, &mut times)
                });
                let profiled = started.elapsed().as_nanos();
                let algorithm = if optimized { "optimized" } else { "reference" };
                writeln!(
                    csv,
                    "{algorithm},{paths},{hops},{iteration},{wall},{profiled},{},{},{},{},{}",
                    times.signature_decode_ns,
                    times.key_lookup_ns,
                    times.key_decode_ns,
                    times.message_build_ns,
                    times.bls_verify_ns
                )
                .unwrap();
            }
        }
    }
}
