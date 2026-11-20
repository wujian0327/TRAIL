use std::collections::BTreeMap;
use trail::blockchain::path::TransactionPaths;
use trail::blockchain::transaction::Transaction;
use trail::consensus::ValidatorRegistry;
use trail::wallet::Wallet;

pub const HOPS: [usize; 6] = [1, 2, 4, 6, 8, 16];
pub const BASE_TRANSACTION_BYTES: usize = 540;
pub const REFERENCE_TRANSACTION_BYTES: usize = 300;
// This is a field in the independently cross-checked NKN wire fixture.
const NKN_FIXTURE_DATA_SIZE: u32 = 540;

const TRAIL_SOURCE: &str =
    "Local Rust TransactionPaths and AggregatedSignedPaths::to_compact_binary";
const ERSOY_SOURCE: &str =
    "Ersoy et al. (2018), Sec. VI, PDF pp. 8-10, Algorithm 2 on p. 9, https://arxiv.org/pdf/1712.07564";
const PLUTUS_SOURCE: &str =
    "Hemo (2018), Sec. 3.2 pp. 22-25 and Sec. 5.5 / Fig. 5-8 p. 38, https://hdl.handle.net/1721.1/119766";
const NKN_SOURCE: &str = "NKN v2 pinned at 6ff0fd160d2da341c5c329bea3e971f1c36c1ac5. pb/sigchain.proto, config/config.go:163-166, por/sigchain.go:32-86 and por/porserver.go:610-653";
const TXTAIL_SOURCE: &str = "Dai et al. (2026), Sec. V-A/C pp. 3202-3203 and Sec. VIII-A Fig. 8 p. 3208, https://doi.org/10.1109/TIFS.2026.3675466";

pub const FINAL: &str = "finalized_path_record";
pub const RELAY: &str = "relay_message";
const CHAIN: &str = "on_chain_publication";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Measure {
    Actual,
    Protocol,
    Assumed,
    Unspecified,
}

impl Measure {
    pub fn name(self) -> &'static str {
        match self {
            Self::Actual => "actual_serialized",
            Self::Protocol => "protocol_encoding_model",
            Self::Assumed => "assumption_based_estimate",
            Self::Unspecified => "not_specified",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Category {
    Signature,
    Identity,
    PublicKey,
    Proof,
    Metadata,
    Encoding,
}

impl Category {
    pub const ALL: [Self; 6] = [
        Self::Signature,
        Self::Identity,
        Self::PublicKey,
        Self::Proof,
        Self::Metadata,
        Self::Encoding,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Signature => "signature",
            Self::Identity => "identity",
            Self::PublicKey => "public_key",
            Self::Proof => "proof",
            Self::Metadata => "metadata",
            Self::Encoding => "encoding",
        }
    }
}

use Category::*;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Field {
    pub category: Category,
    pub occurrences: usize,
    pub payload: usize,
    pub framing: usize,
    pub shared: usize,
}

#[derive(Default, Clone, Debug, PartialEq, Eq)]
pub struct Wire {
    pub bytes: Vec<u8>,
    pub fields: BTreeMap<&'static str, Field>,
}

impl Wire {
    fn account(&mut self, name: &'static str, category: Category, payload: usize, framing: usize) {
        let field = self.fields.entry(name).or_insert(Field {
            category,
            occurrences: 0,
            payload: 0,
            framing: 0,
            shared: 0,
        });
        assert_eq!(field.category, category, "{name}");
        field.occurrences += 1;
        field.payload += payload;
        field.framing += framing;
    }

    fn raw(&mut self, name: &'static str, category: Category, bytes: &[u8]) {
        self.bytes.extend_from_slice(bytes);
        self.account(name, category, bytes.len(), 0);
    }

    fn proto_bytes(&mut self, tag: u8, name: &'static str, category: Category, bytes: &[u8]) {
        if !bytes.is_empty() {
            let framing = self.message_header(tag, bytes.len());
            self.bytes.extend_from_slice(bytes);
            self.account(name, category, bytes.len(), framing);
        }
    }

    fn proto_uint(&mut self, tag: u8, name: &'static str, value: u32) {
        if value != 0 {
            self.bytes.push(tag << 3);
            let start = self.bytes.len();
            push_varint(&mut self.bytes, u64::from(value));
            self.account(name, Metadata, self.bytes.len() - start, 1);
        }
    }

    fn proto_message(&mut self, tag: u8, name: &'static str, message: Wire) {
        let framing = self.message_header(tag, message.bytes.len());
        self.account(name, Encoding, 0, framing);
        self.bytes.extend(message.bytes);
        for (name, field) in message.fields {
            let total = self.fields.entry(name).or_insert(Field {
                category: field.category,
                occurrences: 0,
                payload: 0,
                framing: 0,
                shared: 0,
            });
            total.occurrences += field.occurrences;
            total.payload += field.payload;
            total.framing += field.framing;
        }
    }

    fn message_header(&mut self, tag: u8, length: usize) -> usize {
        let start = self.bytes.len();
        self.bytes.push((tag << 3) | 2);
        push_varint(&mut self.bytes, length as u64);
        self.bytes.len() - start
    }

    fn mark_shared(&mut self, name: &str) {
        let field = self.fields.get_mut(name).expect("shared field must exist");
        field.shared = field.payload + field.framing;
    }

    pub fn category_bytes(&self, category: Category) -> usize {
        let fields = self.fields.values();
        if category == Encoding {
            fields.map(|field| field.framing).sum::<usize>()
                + self
                    .fields
                    .values()
                    .filter(|field| field.category == Encoding)
                    .map(|field| field.payload)
                    .sum::<usize>()
        } else {
            fields
                .filter(|field| field.category == category)
                .map(|field| field.payload)
                .sum()
        }
    }

    pub fn core(&self) -> usize {
        [Signature, Identity, PublicKey, Proof]
            .into_iter()
            .map(|category| self.category_bytes(category))
            .sum()
    }

    pub fn shared(&self) -> usize {
        self.fields.values().map(|field| field.shared).sum()
    }

    pub fn incremental(&self) -> usize {
        self.bytes.len() - self.shared()
    }
}

fn push_varint(bytes: &mut Vec<u8>, mut value: u64) {
    while value >= 128 {
        bytes.push((value as u8 & 0x7f) | 0x80);
        value >>= 7;
    }
    bytes.push(value as u8);
}

#[derive(Clone, Debug)]
pub struct Row {
    pub scheme: &'static str,
    pub hops: Option<usize>,
    pub stage: &'static str,
    pub profile: &'static str,
    pub measurement: Measure,
    pub identities: Option<usize>,
    pub wire: Option<Wire>,
    pub evidence_scope: &'static str,
    pub source_range: &'static str,
    pub baseline: &'static str,
    pub note: String,
}

impl Row {
    fn with_paper_range(mut self, last_hop: usize) -> Self {
        self.source_range = if self.hops.expect("paper range is per hop") > last_hop {
            "extrapolated"
        } else {
            "within_paper_range"
        };
        self
    }

    pub fn percent(&self, payload_bytes: usize) -> Option<f64> {
        if self.evidence_scope == "complete_record" && payload_bytes != 0 {
            return self
                .wire
                .as_ref()
                .map(|wire| 100.0 * wire.incremental() as f64 / payload_bytes as f64);
        }
        None
    }
}

#[derive(Clone, Copy)]
struct Profile {
    scheme: &'static str,
    stage: &'static str,
    name: &'static str,
    measurement: Measure,
    baseline: &'static str,
    source: &'static str,
    assumptions: &'static str,
}

impl Profile {
    fn row(self, hops: Option<usize>, identities: usize, wire: Wire) -> Row {
        Row {
            scheme: self.scheme,
            hops,
            stage: self.stage,
            profile: self.name,
            measurement: self.measurement,
            identities: Some(identities),
            wire: Some(wire),
            evidence_scope: "complete_record",
            source_range: "not_applicable",
            baseline: self.baseline,
            note: format!("{} | {}", self.source, self.assumptions),
        }
    }

    fn unavailable(self, hops: Option<usize>, reason: &str) -> Row {
        Row {
            scheme: self.scheme,
            hops,
            stage: self.stage,
            profile: self.name,
            measurement: Measure::Unspecified,
            identities: None,
            wire: None,
            evidence_scope: "unavailable",
            source_range: "not_applicable",
            baseline: self.baseline,
            note: format!("{} | {reason}", self.source),
        }
    }
}

const TRAIL: Profile = Profile {
    scheme: "TRAIL", stage: FINAL, name: "compact_certificate", measurement: Measure::Actual,
    baseline: "native signed transaction plus existing block header", source: TRAIL_SOURCE,
    assumptions: "One independent BLS signature. Proposer recovered from header. Transaction and block envelope excluded",
};
const ERSOY: Profile = Profile {
    scheme: "Ersoy", stage: RELAY, name: "combined_attached", measurement: Measure::Assumed,
    baseline: "native signed transaction retained", source: ERSOY_SOURCE,
    assumptions: "Predetermined leader. h-1 intermediary keys. Assumed compressed SEC1 secp256k1 keys, compact ECDSA, SHA-256 and u8 list count",
};
const PLUTUS: Profile = Profile {
    scheme: "Plutus", stage: RELAY, name: "thesis_message_evidence", measurement: Measure::Protocol,
    baseline: "initial Algorand message before per-hop attachments", source: PLUTUS_SOURCE,
    assumptions: "Per edge the sender appends a 32-byte recipient public key and a 64-byte signature. Fig. 5-8 starts at 200 bytes of native message and covers h=0..9. Model covers the added evidence fields",
};
const NKN: Profile = Profile {
    scheme: "NKN", stage: FINAL, name: "completed_signature_chain", measurement: Measure::Protocol,
    baseline: "application payload plus detached completed SigChain", source: NKN_SOURCE,
    assumptions: "Ed25519 SIGNATURE mode at heights >= 1200000. Mining relays. Fixed nonce 0x12345678 and data_size 540 in the wire fixture. Excludes SigChainTxn and transport wrappers",
};
const TXTAIL: Profile = Profile {
    scheme: "TxTail", stage: RELAY, name: "fig8_relay_evidence", measurement: Measure::Protocol,
    baseline: "authorized TxTail transaction already containing the initiator BLS public key", source: TXTAIL_SOURCE,
    assumptions: "Fig. 8 vector trace intercept 96 bytes attributed to the initial signature in V-A and consistent with Phore g1pubs. Eight bytes per appended contribution (7-byte descriptor and 1-byte reward share). Counts Fig. 8 fields without additional outer framing. Fig. 8 covers h=0..6",
};

fn trail(hops: usize) -> Wire {
    let wallets: Vec<_> = (0..=hops)
        .map(|index| Wallet::new_deterministic(0x5450_5300 + hops as u64, index as u32))
        .collect();
    let proposer = &wallets[hops].address;
    let registry = ValidatorRegistry::from_wallets(&wallets).expect("distinct fixture wallets");
    let tx = Transaction::with_timestamp(
        proposer.clone(),
        hops as i64,
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
    let cert = path
        .to_aggregated_signed_paths(&registry)
        .expect("fixture identities registered");
    assert!(cert.verify_at_epoch_uncached(&tx, proposer, path.epoch, &registry));
    assert_eq!(
        cert.full_path(proposer, &registry).unwrap(),
        path.node_sequence()
    );
    let binary = cert.to_compact_binary().expect("valid compact certificate");
    let mut wire = Wire::default();
    let sig_len = usize::from(binary[16]);
    let sig_end = 17 + sig_len;
    wire.raw("chain_id", Metadata, &binary[..8]);
    wire.raw("evidence_epoch", Metadata, &binary[8..16]);
    wire.raw("signature_length", Encoding, &binary[16..17]);
    wire.raw("bls_aggregate_signature", Signature, &binary[17..sig_end]);
    wire.raw("index_count", Encoding, &binary[sig_end..sig_end + 8]);
    for index in binary[sig_end + 8..].chunks_exact(8) {
        wire.raw("validator_index", Identity, index);
    }
    assert_eq!(wire.bytes, binary);
    assert_eq!(wire.core(), cert.bytes() as usize);
    wire
}

fn ersoy(hops: usize) -> Wire {
    let relay_count = hops - 1;
    let mut wire = Wire::default();
    wire.raw(
        "intermediary_key_count",
        Encoding,
        &[relay_count.try_into().unwrap()],
    );
    wire.raw("incoming_path_identifier", Proof, &[0x31; 32]);
    wire.raw("client_path_signature", Signature, &[0x32; 64]);
    for relay in 0..relay_count {
        let mut key = [relay as u8; 33];
        key[0] = 2; // A compressed SEC1-shaped placeholder.
        wire.raw("intermediary_public_key", PublicKey, &key);
    }
    wire
}

#[derive(Clone, Copy)]
struct NknParams {
    nonce: u32,
    data_size: u32,
    mining: bool,
    explicit_destination_key: bool,
    hash_mode: bool,
}

const NKN_DEFAULT: NknParams = NknParams {
    nonce: 0x1234_5678,
    data_size: NKN_FIXTURE_DATA_SIZE,
    mining: true,
    explicit_destination_key: false,
    hash_mode: false,
};

fn nkn(hops: usize, params: NknParams) -> Option<Wire> {
    if hops < 2 {
        return None;
    }
    let NknParams {
        nonce,
        data_size,
        mining,
        explicit_destination_key,
        hash_mode,
    } = params;
    let mut chain = Wire::default();
    chain.proto_uint(1, "nonce", nonce);
    chain.proto_uint(2, "data_size", data_size);
    chain.proto_bytes(3, "block_hash", Metadata, &[0x11; 32]);
    chain.proto_bytes(4, "source_id", Identity, &[0x12; 32]);
    chain.proto_bytes(5, "source_public_key", PublicKey, &[0x13; 32]);
    chain.proto_bytes(6, "destination_id", Identity, &[0x14; 32]);
    chain.proto_bytes(7, "destination_public_key", PublicKey, &[0x15; 32]);

    let mut source = Wire::default();
    source.proto_bytes(2, "source_next_public_key", PublicKey, &[0x20; 32]);
    source.proto_bytes(4, "source_signature", Signature, &[0x21; 64]);
    chain.proto_message(8, "source_element_frame", source);

    for relay in 1..hops {
        let mut elem = Wire::default();
        elem.proto_bytes(1, "relay_id", Identity, &[relay as u8; 32]);
        if relay < hops - 1 {
            elem.proto_bytes(
                2,
                "relay_next_public_key",
                PublicKey,
                &[0x20 + relay as u8; 32],
            );
        } else if explicit_destination_key {
            elem.proto_bytes(2, "last_relay_next_public_key", PublicKey, &[0x15; 32]);
        }
        elem.proto_uint(3, "relay_mining", u32::from(mining));
        if hash_mode {
            elem.proto_bytes(4, "relay_hash", Proof, &[0x22; 32]);
            elem.proto_uint(5, "relay_sig_algo", 1);
        } else {
            elem.proto_bytes(4, "relay_signature", Signature, &[0x22; 64]);
        }
        elem.proto_bytes(6, "relay_vrf_output", Proof, &[0x23; 32]);
        elem.proto_bytes(7, "relay_vrf_proof", Proof, &[0x24; 96]);
        chain.proto_message(8, "relay_element_frame", elem);
    }

    let mut destination = Wire::default();
    destination.proto_bytes(4, "destination_receipt_signature", Signature, &[0x25; 64]);
    chain.proto_message(8, "destination_element_frame", destination);
    Some(chain)
}

fn plutus(hops: usize) -> Wire {
    let mut wire = Wire::default();
    for recipient in 0..hops {
        wire.raw("recipient_public_key", PublicKey, &[recipient as u8; 32]);
        wire.raw("sender_path_signature", Signature, &[0x31; 64]);
    }
    wire
}

fn txtail(hops: usize) -> Wire {
    let mut wire = Wire::default();
    wire.raw("bls_aggregate_signature", Signature, &[0x42; 96]);
    for relay in 0..hops {
        wire.raw("relay_public_key_descriptor", Identity, &[relay as u8; 7]);
        wire.raw("relay_reward_share", Metadata, &[1]);
    }
    wire
}

pub fn rows() -> Vec<Row> {
    let mut rows = Vec::new();
    for hops in HOPS {
        rows.push(TRAIL.row(Some(hops), hops, trail(hops)));

        let combined = ersoy(hops);
        rows.push(ERSOY.row(Some(hops), hops - 1, combined.clone()));
        let mut replaced = combined;
        replaced.mark_shared("client_path_signature");
        rows.push(Profile {
            name: "combined_replaces_native_signature",
            baseline: "64-byte original authorization replaced by same-size path-bound authorization",
            assumptions: "Conditional replacement accounting. Credits the original authorization only when the path-bound authorization replaces it",
            ..ERSOY
        }.row(Some(hops), hops - 1, replaced));

        rows.push(
            PLUTUS
                .row(Some(hops), hops, plutus(hops))
                .with_paper_range(9),
        );

        if let Some(chain) = nkn(hops, NKN_DEFAULT) {
            rows.push(NKN.row(Some(hops), hops + 1, chain.clone()));
            let mut shared = chain;
            shared.mark_shared("source_public_key");
            shared.mark_shared("destination_id");
            rows.push(Profile {
                name: "completed_signature_chain_shared_context",
                measurement: Measure::Assumed,
                baseline: "hypothetical shared routing context supplying source public key and destination ID",
                assumptions: "The conditional marginal estimate credits two encoded fields only under an enclosing deduplicating carrier",
                ..NKN
            }.row(Some(hops), hops + 1, shared));
            let legacy = nkn(
                hops,
                NknParams {
                    hash_mode: true,
                    ..NKN_DEFAULT
                },
            )
            .expect("valid completed path length");
            rows.push(Profile {
                name: "historical_hash_signature_chain",
                assumptions: "Historical HASH relay mode permitted below height 1200000. Relay signature field holds the 32-byte hash and enum=1. Endpoint client signatures and relay VRF remain",
                ..NKN
            }.row(Some(hops), hops + 1, legacy));
        } else {
            rows.push(NKN.unavailable(Some(hops), "A completed SigChain requires at least three elements (source, relay and destination)"));
        }

        // The aggregate signs for the origin and appended relays. The origin
        // key itself is carried by the authorized transaction.
        rows.push(
            TXTAIL
                .row(Some(hops), hops, txtail(hops))
                .with_paper_range(6),
        );
    }

    rows.push(Profile {
        stage: "leader_recognition", name: "recognition_message", ..ERSOY
    }.unavailable(None, "Sec. VI, Algorithm 2. Leader credential and outgoing path hash per recognition message. Credential encoding and round-level amortization unspecified"));
    rows.push(Profile {
        stage: CHAIN, name: "block_path_publication", ..ERSOY
    }.unavailable(None, "Sec. VI PDF p. 9. Leader publishes transactions with their paths. Native block path framing unspecified"));
    rows.push(Profile {
        stage: CHAIN, name: "lottery_result", source: "Hemo (2018), Sec. 3.3 pp. 25-28 and Ch. 4 p. 29, https://hdl.handle.net/1721.1/119766",
        ..PLUTUS
    }.unavailable(None, "Lookback path keys and proposer VRF seed the stake-weighted draw. Public result includes winning keys and a proposer proof. Lottery framing and proof encoding width unspecified"));
    rows.push(Profile {
        stage: CHAIN, name: "selected_sigchain_transaction", ..NKN
    }.unavailable(None, "lnode/relay.go:263-295, 388-415. Selected completed proof is wrapped in a SigChainTxn. Per-block publication and transaction envelope are outside this comparison"));
    rows.push(Profile {
        stage: CHAIN, name: "fig9_sampled_publication", source: "Dai et al. (2026), Sec. V-D p. 3204 and Sec. VIII-A / Fig. 9 pp. 3208-3209",
        ..TXTAIL
    }.unavailable(None, "One sampled transaction proof per block, a Merkle inclusion proof, relaybase, and the initiator key carried by each authorized transaction. Block figure uses a different normalization"));

    let mut key = Wire::default();
    key.raw("initiator_bls_public_key", PublicKey, &[0x41; 48]);
    let mut key_row = Profile {
        stage: "transaction_extension", name: "initiator_key_inferred_width", measurement: Measure::Assumed,
        baseline: "raw key extension to native Bitcoin transaction",
        assumptions: "Sec. V-A puts the key inside the transaction. Inferred 48-byte G1 key paired with the 96-byte Fig. 8 signature using Phore g1pubs. Full Bitcoin transaction framing excluded",
        ..TXTAIL
    }.row(None, 1, key);
    key_row.evidence_scope = "partial_subtotal";
    rows.push(key_row);
    rows
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paper_models_match_initial_and_four_hop_field_counts() {
        assert_eq!(plutus(0).bytes.len(), 0); // The native 200 B in the thesis figure is excluded.
        assert_eq!(plutus(4).bytes.len(), 384);
        assert_eq!(txtail(0).bytes.len(), 96); // Fig. 8 includes the initiator signature.
        let four = txtail(4);
        assert_eq!(four.bytes.len(), 128);
        assert_eq!(four.category_bytes(Metadata), 4);
        assert_eq!(four.category_bytes(Proof), 0);
    }

    #[test]
    fn nkn_default_omissions_and_varint_boundaries() {
        assert!(nkn(1, NKN_DEFAULT).is_none());
        let base = nkn(4, NKN_DEFAULT).unwrap();
        assert!(!base.fields.contains_key("last_relay_next_public_key"));
        assert!(!base.fields.contains_key("relay_sig_algo"));
        assert_eq!(base.fields["relay_vrf_output"].payload, 3 * 32);
        assert_eq!(base.fields["relay_vrf_proof"].payload, 3 * 96);
        let explicit = nkn(
            4,
            NknParams {
                explicit_destination_key: true,
                ..NKN_DEFAULT
            },
        )
        .unwrap();
        assert_eq!(explicit.bytes.len(), base.bytes.len() + 34);
        let no_mining = nkn(
            4,
            NknParams {
                mining: false,
                ..NKN_DEFAULT
            },
        )
        .unwrap();
        assert_eq!(no_mining.bytes.len(), base.bytes.len() - 3 * 2);
        let hash_mode = nkn(
            4,
            NknParams {
                hash_mode: true,
                ..NKN_DEFAULT
            },
        )
        .unwrap();
        assert_eq!(hash_mode.bytes.len(), base.bytes.len() - 3 * 30);
        let before = nkn(
            2,
            NknParams {
                data_size: 127,
                ..NKN_DEFAULT
            },
        )
        .unwrap();
        let after = nkn(
            2,
            NknParams {
                data_size: 128,
                ..NKN_DEFAULT
            },
        )
        .unwrap();
        assert_eq!(after.bytes.len(), before.bytes.len() + 1);
        let zero_nonce = nkn(
            2,
            NknParams {
                nonce: 0,
                ..NKN_DEFAULT
            },
        )
        .unwrap();
        assert_eq!(
            nkn(2, NKN_DEFAULT).unwrap().bytes.len(),
            zero_nonce.bytes.len() + 6
        );
    }

    #[test]
    fn fields_cover_serialized_bytes_and_share_only_the_baseline() {
        for row in rows() {
            if let Some(wire) = row.wire {
                let bytes: usize = Category::ALL
                    .into_iter()
                    .map(|c| wire.category_bytes(c))
                    .sum();
                assert_eq!(bytes, wire.bytes.len());
                assert_eq!(wire.incremental() + wire.shared(), bytes);
            }
        }
    }

    #[test]
    fn nkn_wire_matches_reference_proto3_fixture() {
        // SHA-256 of the official field-number fixture encoded independently with
        // NKN's generated Go protobuf type (deterministic serialization).
        use sha2::{Digest, Sha256};
        let fixtures = [
            (
                2,
                "c76d6c3e6e304969951748209554a18b2149552730394589d9bfa46a10bbfcac",
            ),
            (
                4,
                "593bd3864634c207d142851d1b2c178797d94a0b63f6da3ba0378b6908199f37",
            ),
            (
                6,
                "bfc46f772cbc5a694bbf0590743e7ff7a42290959ad983059f8a61794c9b648d",
            ),
            (
                8,
                "e5ea3480af44b33236a9e8852d531ff6ca701c39abfc70dffea4d01708fb1b40",
            ),
            (
                16,
                "48fda460fba6865c5d0047e02c4e1ba4cac6249bbcbe9147ca3d81d10c4b4eb7",
            ),
        ];
        for (hops, expected) in fixtures {
            let wire = nkn(hops, NKN_DEFAULT).unwrap();
            assert_eq!(
                hex::encode(Sha256::digest(wire.bytes)),
                expected,
                "{hops} hops"
            );
        }
    }

    #[test]
    fn trail_path_fixture_is_deterministic_and_four_hops_has_80_core_bytes() {
        let first = trail(4);
        assert_eq!(first, trail(4));
        assert_eq!(first.core(), 80);
        assert_eq!(first.bytes.len(), 105);
    }
}
