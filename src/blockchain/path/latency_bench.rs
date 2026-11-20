use super::{AggregatedSignedPaths, Transaction, ValidatorRegistry, Wallet};
use blst::min_sig::{PublicKey, Signature};

/// Measurement inputs for one certificate and its already verified transaction.
pub struct PreparedCertificate<'a> {
    certificate: &'a AggregatedSignedPaths,
    transaction: &'a Transaction,
    proposer: &'a str,
    epoch: u64,
    registry: &'a ValidatorRegistry,
    pub messages: Vec<Vec<u8>>,
    pub public_keys: Vec<PublicKey>,
    pub signature: Signature,
}

impl<'a> PreparedCertificate<'a> {
    pub fn new(
        certificate: &'a AggregatedSignedPaths,
        transaction: &'a Transaction,
        proposer: &'a str,
        epoch: u64,
        registry: &'a ValidatorRegistry,
    ) -> Option<Self> {
        if !certificate.verify_at_epoch_uncached(transaction, proposer, epoch, registry) {
            return None;
        }
        let nodes = certificate.full_path(proposer, registry).ok()?;
        let (messages, public_keys) = certificate
            .signer_messages(transaction, &nodes, registry)
            .ok()?;
        let signature = Wallet::bls_signature_from_string(certificate.signature.clone()).ok()?;
        Some(Self {
            certificate,
            transaction,
            proposer,
            epoch,
            registry,
            messages,
            public_keys,
            signature,
        })
    }

    pub fn verify_evidence_uncached(&self) -> bool {
        self.certificate.matches_context(self.epoch)
            && self.certificate.verify_evidence(
                self.transaction,
                self.proposer,
                self.registry,
                false,
            )
    }
}
