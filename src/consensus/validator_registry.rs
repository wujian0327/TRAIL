use crate::tools::Hasher;
use crate::wallet::Wallet;
use blst::min_sig::PublicKey;
use std::collections::HashMap;
use std::fmt;

pub type ValidatorIndex = u64;

/// Immutable snapshot of the simulator's trusted validator identities and keys.
/// Sorting the complete roster makes the index independent of node IDs and
/// the order in which validators announce their stakes.
#[derive(Debug)]
pub struct ValidatorRegistry {
    by_address: HashMap<String, ValidatorIndex>,
    by_index: Vec<(String, PublicKey)>,
    fingerprint: [u8; 32],
}

impl Default for ValidatorRegistry {
    fn default() -> Self {
        Self::from_wallets([]).expect("empty validator registry")
    }
}

impl ValidatorRegistry {
    pub fn from_wallets<'a>(
        wallets: impl IntoIterator<Item = &'a Wallet>,
    ) -> Result<Self, ValidatorRegistryError> {
        Self::new(
            wallets
                .into_iter()
                .map(|wallet| (wallet.address.clone(), wallet.bls_public_key)),
        )
    }

    pub fn new(
        identities: impl IntoIterator<Item = (String, PublicKey)>,
    ) -> Result<Self, ValidatorRegistryError> {
        let mut by_index: Vec<_> = identities.into_iter().collect();
        by_index.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        let mut by_address = HashMap::with_capacity(by_index.len());
        let mut context = Vec::new();
        for (index, (address, key)) in by_index.iter().enumerate() {
            if by_address.insert(address.clone(), index as u64).is_some() {
                return Err(ValidatorRegistryError::DuplicateAddress(address.clone()));
            }
            context.extend_from_slice(&(address.len() as u64).to_le_bytes());
            context.extend_from_slice(address.as_bytes());
            context.extend_from_slice(&key.to_bytes());
        }
        Ok(Self {
            by_address,
            by_index,
            fingerprint: Hasher::hash(context),
        })
    }

    pub fn index(&self, address: &str) -> Result<ValidatorIndex, ValidatorRegistryError> {
        self.by_address
            .get(address)
            .copied()
            .ok_or_else(|| ValidatorRegistryError::UnknownAddress(address.to_owned()))
    }

    pub fn address(&self, index: ValidatorIndex) -> Result<&str, ValidatorRegistryError> {
        usize::try_from(index)
            .ok()
            .and_then(|index| self.by_index.get(index))
            .map(|(address, _)| address.as_str())
            .ok_or(ValidatorRegistryError::UnknownIndex(index))
    }

    pub fn public_key(&self, address: &str) -> Result<PublicKey, ValidatorRegistryError> {
        let index = self.index(address)?;
        Ok(self.by_index[index as usize].1)
    }

    pub(crate) fn len(&self) -> usize {
        self.by_index.len()
    }

    pub fn entries(&self) -> impl Iterator<Item = (ValidatorIndex, &str, PublicKey)> {
        self.by_index
            .iter()
            .enumerate()
            .map(|(index, (address, key))| (index as u64, address.as_str(), *key))
    }

    pub(crate) fn fingerprint(&self) -> [u8; 32] {
        self.fingerprint
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ValidatorRegistryError {
    DuplicateAddress(String),
    UnknownAddress(String),
    UnknownIndex(ValidatorIndex),
}

impl fmt::Display for ValidatorRegistryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DuplicateAddress(address) => write!(f, "duplicate validator address {address}"),
            Self::UnknownAddress(address) => write!(f, "unknown validator address {address}"),
            Self::UnknownIndex(index) => write!(f, "unknown validator index {index}"),
        }
    }
}

impl std::error::Error for ValidatorRegistryError {}
