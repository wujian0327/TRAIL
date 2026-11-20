use crate::BeaconChain;
use crate::BeaconChainTypes;
use eth2::types::GraffitiPolicy;
use execution_layer::{CommitPrefix, ExecutionLayer, http::ENGINE_GET_CLIENT_VERSION_V1};
use logging::crit;
use serde::{Deserialize, Serialize};
use slot_clock::SlotClock;
use ssz_types::{FixedVector, VariableList};
use std::{fmt::Debug, str::FromStr, time::Duration};
use task_executor::TaskExecutor;
use tracing::{debug, error, warn};
use typenum::U48;
use types::{ChainSpec, EthSpec, GRAFFITI_BYTES_LEN, Graffiti, Hash256, TrailInlineEvidenceRecord};

const ENGINE_VERSION_AGE_LIMIT_EPOCH_MULTIPLE: u32 = 6; // 6 epochs
const ENGINE_VERSION_CACHE_REFRESH_EPOCH_MULTIPLE: u32 = 2; // 2 epochs
const ENGINE_VERSION_CACHE_PRELOAD_STARTUP_DELAY: Duration = Duration::from_secs(60);
/// Represents the source and content of graffiti for block production, excluding
/// inputs from the validator client and execution engine. Graffiti is categorized
/// as either user-specified or calculated to facilitate decisions on graffiti
/// selection.
#[derive(Clone, Copy, Serialize, Deserialize)]
pub enum GraffitiOrigin {
    UserSpecified(Graffiti),
    Calculated(Graffiti),
}

impl GraffitiOrigin {
    pub fn graffiti(&self) -> Graffiti {
        match self {
            GraffitiOrigin::UserSpecified(graffiti) => *graffiti,
            GraffitiOrigin::Calculated(graffiti) => *graffiti,
        }
    }
}

impl Default for GraffitiOrigin {
    fn default() -> Self {
        let version_bytes = lighthouse_version::VERSION.as_bytes();
        let trimmed_len = std::cmp::min(version_bytes.len(), GRAFFITI_BYTES_LEN);
        let mut bytes = [0u8; GRAFFITI_BYTES_LEN];
        bytes[..trimmed_len].copy_from_slice(&version_bytes[..trimmed_len]);
        Self::Calculated(Graffiti::from(bytes))
    }
}

impl Debug for GraffitiOrigin {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        self.graffiti().fmt(f)
    }
}

pub enum GraffitiSettings {
    Unspecified,
    Specified {
        graffiti: Graffiti,
        policy: GraffitiPolicy,
    },
}

impl GraffitiSettings {
    pub fn new(validator_graffiti: Option<Graffiti>, policy: Option<GraffitiPolicy>) -> Self {
        validator_graffiti
            .map(|graffiti| Self::Specified {
                graffiti,
                policy: policy.unwrap_or_default(),
            })
            .unwrap_or(Self::Unspecified)
    }
}

pub struct GraffitiCalculator<T: BeaconChainTypes> {
    pub beacon_graffiti: GraffitiOrigin,
    execution_layer: Option<ExecutionLayer<T::EthSpec>>,
    pub epoch_duration: Duration,
}

impl<T: BeaconChainTypes> GraffitiCalculator<T> {
    pub fn new(
        beacon_graffiti: GraffitiOrigin,
        execution_layer: Option<ExecutionLayer<T::EthSpec>>,
        epoch_duration: Duration,
    ) -> Self {
        Self {
            beacon_graffiti,
            execution_layer,
            epoch_duration,
        }
    }

    /// Returns the appropriate graffiti to use for block production, prioritizing
    /// sources in the following order:
    /// 1. Graffiti specified by the validator client.
    /// 2. Graffiti specified by the user via beacon node CLI options.
    /// 3. The EL & CL client version string, applicable when the EL supports version specification.
    /// 4. The default lighthouse version string, used if the EL lacks version specification support.
    pub async fn get_graffiti(&self, graffiti_settings: GraffitiSettings) -> Graffiti {
        match graffiti_settings {
            GraffitiSettings::Specified { graffiti, policy } => match policy {
                GraffitiPolicy::PreserveUserGraffiti => graffiti,
                GraffitiPolicy::AppendClientVersions => {
                    self.calculate_combined_graffiti(Some(graffiti)).await
                }
            },
            GraffitiSettings::Unspecified => self.calculate_combined_graffiti(None).await,
        }
    }

    async fn calculate_combined_graffiti(&self, validator_graffiti: Option<Graffiti>) -> Graffiti {
        match self.beacon_graffiti {
            GraffitiOrigin::UserSpecified(graffiti) => graffiti,
            GraffitiOrigin::Calculated(default_graffiti) => {
                let Some(execution_layer) = self.execution_layer.as_ref() else {
                    // Return default graffiti if there is no execution layer. This
                    // shouldn't occur if we're actually producing blocks.
                    crit!(
                        "No execution layer available for graffiti calculation during block production!"
                    );
                    return default_graffiti;
                };

                // The engine version cache refresh service ensures this will almost always retrieve this data from the
                // cache instead of making a request to the execution engine. A cache miss would only occur if lighthouse
                // has recently started or the EL recently went offline.
                let engine_versions = match execution_layer
                    .get_engine_version(Some(
                        self.epoch_duration * ENGINE_VERSION_AGE_LIMIT_EPOCH_MULTIPLE,
                    ))
                    .await
                {
                    Ok(engine_versions) => engine_versions,
                    Err(el_error) => {
                        warn!(error = ?el_error, "Failed to determine execution engine version for graffiti");
                        return default_graffiti;
                    }
                };

                let Some(engine_version) = engine_versions.first() else {
                    // Got an empty array which indicates the EL doesn't support the method
                    debug!(
                        "Using default lighthouse graffiti: EL does not support {} method",
                        ENGINE_GET_CLIENT_VERSION_V1
                    );
                    return default_graffiti;
                };
                if engine_versions.len() != 1 {
                    // More than one version implies lighthouse is connected to
                    // an EL multiplexer. We don't support modifying the graffiti
                    // with these configurations.
                    warn!("Execution Engine multiplexer detected, using default graffiti");
                    return default_graffiti;
                }

                let lighthouse_commit_prefix =
                    CommitPrefix::try_from(lighthouse_version::COMMIT_PREFIX.to_string())
                        .unwrap_or_else(|error_message| {
                            // This really shouldn't happen but we want to definitly log if it does
                            crit!(
                                error = error_message,
                                "Failed to parse lighthouse commit prefix"
                            );
                            CommitPrefix("00000000".to_string())
                        });

                engine_version.calculate_graffiti(lighthouse_commit_prefix, validator_graffiti)
            }
        }
    }
}

pub fn trail_tx_evidence_records_from_json<E: EthSpec>(
    evidence: Option<serde_json::Value>,
    spec: &ChainSpec,
) -> VariableList<TrailInlineEvidenceRecord<E>, E::MaxBlobCommitmentsPerBlock> {
    let Some(evidence) = evidence else {
        return VariableList::empty();
    };
    let parsed = match serde_json::from_value::<TrailBlockEvidence>(evidence) {
        Ok(parsed) => parsed,
        Err(error) => {
            warn!(%error, "TRAIL path certificate response rejected");
            return VariableList::empty();
        }
    };
    let built = parsed.inline_records::<E>(spec);
    for omission in &built.omitted {
        warn!(tx_hash = %omission.tx_hash, reason = %omission.reason, "TRAIL path certificate omitted");
    }
    match built.records.try_into() {
        Ok(records) => records,
        Err(error) => {
            warn!(?error, "TRAIL SSZ evidence list rejected");
            VariableList::empty()
        }
    }
}

pub fn trail_tx_evidence_root_from_json(evidence: Option<serde_json::Value>) -> Option<Hash256> {
    let evidence = evidence?;
    serde_json::from_value::<TrailBlockEvidence>(evidence)
        .ok()
        .and_then(|evidence| evidence.evidence_root)
        .and_then(|root| Hash256::from_str(&root).ok())
}

fn hex_bytes_exact<const N: usize>(input: &str) -> Option<[u8; N]> {
    let hex = input.trim().strip_prefix("0x").unwrap_or(input.trim());
    if hex.len() != N * 2 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let bytes = hex::decode(hex).ok()?;
    bytes.try_into().ok()
}

#[derive(Deserialize)]
struct TrailBlockEvidence {
    evidence_root: Option<String>,
    #[serde(default)]
    transactions: Vec<TrailTxEvidence>,
}

#[derive(Debug)]
struct TrailCertificateOmission {
    tx_hash: String,
    reason: String,
}

struct TrailInlineBuild<E: EthSpec> {
    records: Vec<TrailInlineEvidenceRecord<E>>,
    omitted: Vec<TrailCertificateOmission>,
}

impl TrailBlockEvidence {
    fn inline_records<E: EthSpec>(self, spec: &ChainSpec) -> TrailInlineBuild<E> {
        let max_paths = spec.trail_config.max_paths_per_block as usize;
        let max_len = spec.trail_config.max_path_evidence_len as usize;
        let mut result = TrailInlineBuild {
            records: Vec::new(),
            omitted: Vec::new(),
        };
        let mut work = 0u64;
        for tx in self.transactions {
            let tx_hash = tx
                .tx_hash
                .clone()
                .unwrap_or_else(|| tx.metadata.tx_hash.clone());
            if result.records.len() >= max_paths {
                result.omitted.push(TrailCertificateOmission {
                    tx_hash,
                    reason: "max_paths_per_block".into(),
                });
                continue;
            }
            match tx.inline_record::<E>(max_len) {
                Ok(record) => {
                    let next_work = work.saturating_add(record.relay_path.len() as u64);
                    if next_work > spec.trail_config.evidence_work_limit {
                        result.omitted.push(TrailCertificateOmission {
                            tx_hash,
                            reason: "evidence_work_limit".into(),
                        });
                    } else {
                        work = next_work;
                        result.records.push(record);
                    }
                }
                Err(reason) => result
                    .omitted
                    .push(TrailCertificateOmission { tx_hash, reason }),
            }
        }
        result
    }
}

#[derive(Deserialize)]
struct TrailTxEvidence {
    #[serde(default)]
    aggregate_signature: Option<String>,
    #[serde(default)]
    tx_hash: Option<String>,
    #[serde(default)]
    priority_fee_wei: Option<String>,
    #[serde(default)]
    irrecoverable_cost_wei: Option<String>,
    metadata: TrailTxMetadata,
}

impl TrailTxEvidence {
    fn inline_record<E: EthSpec>(
        self,
        max_len: usize,
    ) -> Result<TrailInlineEvidenceRecord<E>, String> {
        let signature = self
            .aggregate_signature
            .as_deref()
            .and_then(hex_bytes_exact::<48>)
            .ok_or_else(|| "missing_or_malformed_per_path_signature".to_string())?;
        let aggregate_signature = FixedVector::<u8, U48>::new(signature.to_vec())
            .map_err(|error| format!("invalid aggregate signature: {error}"))?;
        let tx_hash = self.tx_hash.as_deref().unwrap_or(&self.metadata.tx_hash);
        let tx_hash =
            hex_bytes_exact::<32>(tx_hash).ok_or_else(|| "invalid TRAIL tx hash".to_string())?;
        if hex_bytes_exact::<32>(&self.metadata.tx_hash) != Some(tx_hash) {
            return Err("transaction_metadata_mismatch".into());
        }
        let mut path = Vec::with_capacity(self.metadata.paths.len().saturating_add(1));
        path.push(self.metadata.origin_relay_validator_index);
        for edge in self.metadata.paths {
            path.push(edge.to);
        }
        if path.is_empty() || path.len() > max_len {
            return Err("invalid_path_length".into());
        }
        if path.iter().collect::<std::collections::HashSet<_>>().len() != path.len() {
            return Err("repeated_path_identity".into());
        }
        let priority_fee_wei = self
            .priority_fee_wei
            .as_deref()
            .and_then(parse_u64_decimal)
            .unwrap_or(0);
        let irrecoverable_cost_wei = self
            .irrecoverable_cost_wei
            .as_deref()
            .and_then(parse_u64_decimal)
            .unwrap_or(0);
        let relay_path =
            VariableList::new(path).map_err(|e| format!("invalid TRAIL inline relay path: {e}"))?;
        Ok(TrailInlineEvidenceRecord {
            tx_hash: Hash256::from_slice(&tx_hash),
            epoch: self.metadata.epoch,
            priority_fee_wei,
            irrecoverable_cost_wei,
            relay_path,
            aggregate_signature,
        })
    }
}

fn parse_u64_decimal(input: &str) -> Option<u64> {
    input
        .trim()
        .parse::<u128>()
        .ok()
        .map(|value| value.min(u128::from(u64::MAX)) as u64)
}

#[derive(Deserialize)]
struct TrailTxMetadata {
    epoch: u64,
    tx_hash: String,
    origin_relay_validator_index: u64,
    #[serde(default)]
    paths: Vec<TrailTxPathEdge>,
}

#[derive(Deserialize)]
struct TrailTxPathEdge {
    to: u64,
}

pub fn start_engine_version_cache_refresh_service<T: BeaconChainTypes>(
    chain: &BeaconChain<T>,
    executor: TaskExecutor,
) {
    let Some(el_ref) = chain.execution_layer.as_ref() else {
        debug!("No execution layer configured, not starting engine version cache refresh service");
        return;
    };
    if matches!(
        chain.graffiti_calculator.beacon_graffiti,
        GraffitiOrigin::UserSpecified(_)
    ) {
        debug!("Graffiti is user-specified, not starting engine version cache refresh service");
        return;
    }

    let execution_layer = el_ref.clone();
    let slot_clock = chain.slot_clock.clone();
    let epoch_duration = chain.graffiti_calculator.epoch_duration;
    executor.spawn(
        async move {
            engine_version_cache_refresh_service::<T>(execution_layer, slot_clock, epoch_duration)
                .await
        },
        "engine_version_cache_refresh_service",
    );
}

async fn engine_version_cache_refresh_service<T: BeaconChainTypes>(
    execution_layer: ExecutionLayer<T::EthSpec>,
    slot_clock: T::SlotClock,
    epoch_duration: Duration,
) {
    // Preload the engine version cache after a brief delay to allow for EL initialization.
    // This initial priming ensures cache readiness before the service's regular update cycle begins.
    tokio::time::sleep(ENGINE_VERSION_CACHE_PRELOAD_STARTUP_DELAY).await;
    if let Err(e) = execution_layer.get_engine_version(None).await {
        debug!(
            error = ?e,
            "Failed to preload engine version cache"
        );
    }

    // this service should run 3/8 of the way through the epoch
    let epoch_delay = (epoch_duration * 3) / 8;
    // the duration of 1 epoch less than the total duration between firing of this service
    let partial_firing_delay =
        epoch_duration * ENGINE_VERSION_CACHE_REFRESH_EPOCH_MULTIPLE.saturating_sub(1);
    loop {
        match slot_clock.duration_to_next_epoch(T::EthSpec::slots_per_epoch()) {
            Some(duration_to_next_epoch) => {
                let firing_delay = partial_firing_delay + duration_to_next_epoch + epoch_delay;
                tokio::time::sleep(firing_delay).await;

                debug!("Engine version cache refresh service firing");

                match execution_layer.get_engine_version(None).await {
                    Err(e) => warn!( error = ?e, "Failed to populate engine version cache"),
                    Ok(versions) => {
                        if versions.is_empty() {
                            // Empty array indicates the EL doesn't support the method
                            debug!(
                                "EL does not support {} method. Sleeping twice as long before retry",
                                ENGINE_GET_CLIENT_VERSION_V1
                            );
                            tokio::time::sleep(
                                epoch_duration * ENGINE_VERSION_CACHE_REFRESH_EPOCH_MULTIPLE,
                            )
                            .await;
                        }
                    }
                }
            }
            None => {
                error!("Failed to read slot clock");
                // If we can't read the slot clock, just wait another slot.
                tokio::time::sleep(slot_clock.slot_duration()).await;
            }
        };
    }
}

#[cfg(test)]
mod tests {
    use crate::ChainConfig;
    use crate::graffiti_calculator::{GraffitiSettings, trail_tx_evidence_root_from_json};
    use crate::test_utils::{BeaconChainHarness, EphemeralHarnessType, test_spec};
    use bls::Keypair;
    use eth2::types::GraffitiPolicy;
    use execution_layer::EngineCapabilities;
    use execution_layer::test_utils::{DEFAULT_CLIENT_VERSION, DEFAULT_ENGINE_CAPABILITIES};
    use std::sync::Arc;
    use std::sync::LazyLock;
    use std::time::Duration;
    use tracing::info;
    use types::{ChainSpec, GRAFFITI_BYTES_LEN, Graffiti, MinimalEthSpec};

    fn per_path_response(count: usize) -> serde_json::Value {
        serde_json::json!({"signature_scheme":"per_path_v1", "transactions": (0..count).map(|index| {
            let hash = format!("0x{:064x}", index.saturating_add(1));
            serde_json::json!({"tx_hash":hash,"aggregate_signature":format!("0x{}", hex::encode([index as u8;48])),
                "metadata":{"tx_hash":hash,"epoch":7,"origin_relay_validator_index":0,"paths":[{"to":1}]}})
        }).collect::<Vec<_>>()})
    }

    #[test]
    fn trail_per_path_packing_preserves_signatures_and_reports_1024_cap() {
        let spec = ChainSpec::minimal();
        let parsed: super::TrailBlockEvidence =
            serde_json::from_value(per_path_response(1025)).unwrap();
        let built = parsed.inline_records::<MinimalEthSpec>(&spec);
        assert_eq!(built.records.len(), 1024);
        assert_eq!(built.omitted.len(), 1);
        assert_eq!(built.omitted[0].tx_hash, format!("0x{:064x}", 1025));
        assert_eq!(built.omitted[0].reason, "max_paths_per_block");
        for (index, record) in built.records.iter().enumerate() {
            assert_eq!(record.aggregate_signature.as_ref(), &[index as u8; 48]);
            assert_eq!(record.relay_path.as_ref(), &[0, 1]);
        }
        assert_ne!(
            built.records[0].aggregate_signature,
            built.records[1].aggregate_signature
        );
    }

    #[test]
    fn trail_per_path_packing_does_not_fall_back_to_block_signature() {
        let spec = ChainSpec::minimal();
        let mut response = per_path_response(2);
        response["aggregate_signature"] =
            response["transactions"][0]["aggregate_signature"].clone();
        response["transactions"][0]
            .as_object_mut()
            .unwrap()
            .remove("aggregate_signature");
        let parsed: super::TrailBlockEvidence = serde_json::from_value(response).unwrap();
        let built = parsed.inline_records::<MinimalEthSpec>(&spec);
        assert_eq!(built.records.len(), 1);
        let mut expected_hash = [0u8; 32];
        expected_hash[31] = 2;
        assert_eq!(
            built.records[0].tx_hash,
            types::Hash256::from_slice(&expected_hash)
        );
        assert_eq!(
            built.omitted[0].reason,
            "missing_or_malformed_per_path_signature"
        );
    }

    #[test]
    fn trail_per_path_packing_reports_budget_and_malformed_paths() {
        let mut spec = ChainSpec::minimal();
        spec.trail_config.evidence_work_limit = 2;
        let parsed: super::TrailBlockEvidence =
            serde_json::from_value(per_path_response(3)).unwrap();
        let built = parsed.inline_records::<MinimalEthSpec>(&spec);
        assert_eq!(built.records.len(), 1);
        assert_eq!(built.omitted.len(), 2);
        assert!(
            built
                .omitted
                .iter()
                .all(|r| r.reason == "evidence_work_limit")
        );
        let mut response = per_path_response(1);
        response["transactions"][0]["metadata"]["paths"][0]["to"] = serde_json::json!(0);
        let parsed: super::TrailBlockEvidence = serde_json::from_value(response).unwrap();
        let built = parsed.inline_records::<MinimalEthSpec>(&spec);
        assert!(built.records.is_empty());
        assert_eq!(built.omitted[0].reason, "repeated_path_identity");
    }

    const VALIDATOR_COUNT: usize = 48;
    /// A cached set of keys.
    static KEYPAIRS: LazyLock<Vec<Keypair>> =
        LazyLock::new(|| types::test_utils::generate_deterministic_keypairs(VALIDATOR_COUNT));

    fn get_harness(
        validator_count: usize,
        spec: Arc<ChainSpec>,
        chain_config: Option<ChainConfig>,
    ) -> BeaconChainHarness<EphemeralHarnessType<MinimalEthSpec>> {
        let harness = BeaconChainHarness::builder(MinimalEthSpec)
            .spec(spec)
            .chain_config(chain_config.unwrap_or_default())
            .keypairs(KEYPAIRS[0..validator_count].to_vec())
            .fresh_ephemeral_store()
            .mock_execution_layer()
            .build();

        harness.advance_slot();

        harness
    }

    #[tokio::test]
    async fn check_graffiti_without_el_version_support() {
        let spec = Arc::new(test_spec::<MinimalEthSpec>());
        let harness = get_harness(VALIDATOR_COUNT, spec, None);
        // modify execution engine so it doesn't support engine_getClientVersionV1 method
        let mock_execution_layer = harness.mock_execution_layer.as_ref().unwrap();
        mock_execution_layer
            .server
            .set_engine_capabilities(EngineCapabilities {
                get_client_version_v1: false,
                ..DEFAULT_ENGINE_CAPABILITIES
            });
        // refresh capabilities cache
        harness
            .chain
            .execution_layer
            .as_ref()
            .unwrap()
            .get_engine_capabilities(Some(Duration::ZERO))
            .await
            .unwrap();

        let version_bytes = std::cmp::min(lighthouse_version::VERSION.len(), GRAFFITI_BYTES_LEN);
        // grab the slice of the graffiti that corresponds to the lighthouse version
        let graffiti_slice = &harness
            .chain
            .graffiti_calculator
            .get_graffiti(GraffitiSettings::Unspecified)
            .await
            .0[..version_bytes];

        // convert graffiti bytes slice to ascii for easy debugging if this test should fail
        let graffiti_str =
            std::str::from_utf8(graffiti_slice).expect("bytes should convert nicely to ascii");

        info!(
            lighthouse_version = lighthouse_version::VERSION,
            graffiti_str, "results"
        );
        println!("lighthouse_version: '{}'", lighthouse_version::VERSION);
        println!("graffiti_str:       '{}'", graffiti_str);

        assert!(lighthouse_version::VERSION.starts_with(graffiti_str));
    }

    #[tokio::test]
    async fn check_graffiti_with_el_version_support() {
        let spec = Arc::new(test_spec::<MinimalEthSpec>());
        let harness = get_harness(VALIDATOR_COUNT, spec, None);

        let found_graffiti_bytes = harness
            .chain
            .graffiti_calculator
            .get_graffiti(GraffitiSettings::Unspecified)
            .await
            .0;

        let mock_commit = DEFAULT_CLIENT_VERSION.commit.clone();
        let expected_graffiti_string = format!(
            "{}{}{}{}",
            DEFAULT_CLIENT_VERSION.code,
            mock_commit
                .strip_prefix("0x")
                .unwrap_or(&mock_commit)
                .get(0..4)
                .expect("should get first 2 bytes in hex"),
            "LH",
            lighthouse_version::COMMIT_PREFIX
                .get(0..4)
                .expect("should get first 2 bytes in hex")
        );

        let expected_graffiti_prefix_bytes = expected_graffiti_string.as_bytes();
        let expected_graffiti_prefix_len =
            std::cmp::min(expected_graffiti_prefix_bytes.len(), GRAFFITI_BYTES_LEN);

        let found_graffiti_string =
            std::str::from_utf8(&found_graffiti_bytes[..expected_graffiti_prefix_len])
                .expect("bytes should convert nicely to ascii");

        info!(expected_graffiti_string, found_graffiti_string, "results");
        println!("expected_graffiti_string: '{}'", expected_graffiti_string);
        println!("found_graffiti_string:    '{}'", found_graffiti_string);

        assert_eq!(expected_graffiti_string, found_graffiti_string);

        let mut expected_graffiti_bytes = [0u8; GRAFFITI_BYTES_LEN];
        expected_graffiti_bytes[..expected_graffiti_prefix_len]
            .copy_from_slice(expected_graffiti_string.as_bytes());
        assert_eq!(found_graffiti_bytes, expected_graffiti_bytes);
    }

    #[tokio::test]
    async fn check_graffiti_with_validator_specified_value() {
        let spec = Arc::new(test_spec::<MinimalEthSpec>());
        let harness = get_harness(VALIDATOR_COUNT, spec, None);

        let graffiti_str = "nice graffiti bro";
        let mut graffiti_bytes = [0u8; GRAFFITI_BYTES_LEN];
        graffiti_bytes[..graffiti_str.len()].copy_from_slice(graffiti_str.as_bytes());

        let found_graffiti = harness
            .chain
            .graffiti_calculator
            .get_graffiti(GraffitiSettings::new(
                Some(Graffiti::from(graffiti_bytes)),
                Some(GraffitiPolicy::PreserveUserGraffiti),
            ))
            .await;

        assert_eq!(
            found_graffiti.to_string(),
            "0x6e6963652067726166666974692062726f000000000000000000000000000000"
        );
    }

    #[test]
    fn trail_evidence_root_uses_the_ssz_field_input() {
        let root = format!("0x{}", "ab".repeat(32));
        let parsed =
            trail_tx_evidence_root_from_json(Some(serde_json::json!({"evidence_root":root})));
        assert_eq!(parsed, Some(root.parse().unwrap()));
    }

    #[tokio::test]
    async fn check_append_el_version_graffiti_various_length() {
        let spec = Arc::new(test_spec::<MinimalEthSpec>());
        let harness = get_harness(VALIDATOR_COUNT, spec, None);

        let graffiti_vec = vec![
            // less than 20 characters, example below is 19 characters
            "This is my graffiti",
            // 20-23 characters, example below is 22 characters
            "This is my graffiti yo",
            // 24-27 characters, example below is 26 characters
            "This is my graffiti string",
            // 28-29 characters, example below is 29 characters
            "This is my graffiti string yo",
            // 30-32 characters, example below is 32 characters
            "This is my graffiti string yo yo",
        ];

        for graffiti in graffiti_vec {
            let mut graffiti_bytes = [0; GRAFFITI_BYTES_LEN];
            graffiti_bytes[..graffiti.len()].copy_from_slice(graffiti.as_bytes());

            // To test appending client version info with user specified graffiti
            let policy = GraffitiPolicy::AppendClientVersions;
            let found_graffiti_bytes = harness
                .chain
                .graffiti_calculator
                .get_graffiti(GraffitiSettings::Specified {
                    graffiti: Graffiti::from(graffiti_bytes),
                    policy,
                })
                .await
                .0;

            let mock_commit = DEFAULT_CLIENT_VERSION.commit.clone();

            let graffiti_length = graffiti.len();
            let append_graffiti_string = match graffiti_length {
                0..=19 => format!(
                    "{}{}{}{}",
                    DEFAULT_CLIENT_VERSION.code,
                    mock_commit
                        .strip_prefix("0x")
                        .unwrap_or(&mock_commit)
                        .get(0..4)
                        .expect("should get first 2 bytes in hex"),
                    "LH",
                    lighthouse_version::COMMIT_PREFIX
                        .get(0..4)
                        .expect("should get first 2 bytes in hex")
                ),
                20..=23 => format!(
                    "{}{}{}{}",
                    DEFAULT_CLIENT_VERSION.code,
                    mock_commit
                        .strip_prefix("0x")
                        .unwrap_or(&mock_commit)
                        .get(0..2)
                        .expect("should get first 2 bytes in hex"),
                    "LH",
                    lighthouse_version::COMMIT_PREFIX
                        .get(0..2)
                        .expect("should get first 2 bytes in hex")
                ),
                24..=27 => format!("{}{}", DEFAULT_CLIENT_VERSION.code, "LH",),
                28..=29 => DEFAULT_CLIENT_VERSION.code.to_string(),
                // when user graffiti length is 30-32 characters, append nothing
                30..=32 => String::new(),
                _ => panic!(
                    "graffiti length should be less than or equal to GRAFFITI_BYTES_LEN (32 characters)"
                ),
            };

            let expected_graffiti_string = if append_graffiti_string.is_empty() {
                // for the case of empty append_graffiti_string, i.e., user-specified graffiti is 30-32 characters
                graffiti.to_string()
            } else {
                // There is a space between the user graffiti and client version info
                // as defined in calculate_graffiti function in engine_api.rs
                format!("{} {}", graffiti, append_graffiti_string)
            };

            let expected_graffiti_prefix_bytes = expected_graffiti_string.as_bytes();
            let expected_graffiti_prefix_len =
                std::cmp::min(expected_graffiti_prefix_bytes.len(), GRAFFITI_BYTES_LEN);

            let found_graffiti_string =
                std::str::from_utf8(&found_graffiti_bytes[..expected_graffiti_prefix_len])
                    .expect("bytes should convert nicely to ascii");

            assert_eq!(expected_graffiti_string, found_graffiti_string);
        }
    }
}
