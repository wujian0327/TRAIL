//! Experimental block path payload. The simulator's production encoding is independent.
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fmt};
use trail::blockchain::path::AggregatedSignedPaths;

pub const SIGNATURE_BYTES: usize = 48;
const BLOCK_FRAME: usize = 1 + 4;
const DICTIONARY_COUNT: usize = 4;
const PATH_COUNT: usize = 2;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PathRecord {
    pub indices: Vec<u64>,
    pub signature: Option<[u8; SIGNATURE_BYTES]>,
}

impl PathRecord {
    pub fn from_certificate(certificate: &AggregatedSignedPaths) -> Result<Self, Error> {
        let signature = if certificate.signature.is_empty() {
            None
        } else {
            Some(
                hex::decode(
                    certificate
                        .signature
                        .strip_prefix("0x")
                        .unwrap_or(&certificate.signature),
                )
                .map_err(|_| Error::InvalidPath)?
                .try_into()
                .map_err(|_| Error::InvalidPath)?,
            )
        };
        let record = Self {
            indices: certificate.paths.clone(),
            signature,
        };
        record.check()?;
        Ok(record)
    }

    pub fn into_certificate(self, chain_id: u64, epoch: u64) -> AggregatedSignedPaths {
        AggregatedSignedPaths {
            chain_id,
            epoch,
            paths: self.indices,
            signature: self
                .signature
                .map(|sig| format!("0x{}", hex::encode(sig)))
                .unwrap_or_default(),
        }
    }

    fn check(&self) -> Result<(), Error> {
        if self.indices.is_empty() == self.signature.is_some() {
            return Err(Error::InvalidPath);
        }
        u16::try_from(self.indices.len()).map_err(|_| Error::Overflow)?;
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scheme {
    Baseline,
    Fixed4,
    Global,
    Dictionary,
    Adaptive,
}

impl Scheme {
    pub const ALL: [Self; 5] = [
        Self::Baseline,
        Self::Fixed4,
        Self::Global,
        Self::Dictionary,
        Self::Adaptive,
    ];

    pub fn name(self) -> &'static str {
        match self {
            Self::Baseline => "baseline",
            Self::Fixed4 => "fixed4",
            Self::Global => "global",
            Self::Dictionary => "dictionary",
            Self::Adaptive => "adaptive",
        }
    }
}

#[repr(u8)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    U64 = 0,
    U32 = 1,
    Dictionary = 2,
    U8 = 3,
}

impl Mode {
    pub fn name(self) -> &'static str {
        match self {
            Self::U64 => "u64",
            Self::U32 => "u32",
            Self::U8 => "u8",
            Self::Dictionary => "dictionary",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    Overflow,
    InvalidPath,
    InvalidMode,
    InvalidDictionary,
    UnknownIndex,
    UnknownLocalIndex,
    Truncated,
    TrailingBytes,
    CommitmentMismatch,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}

impl std::error::Error for Error {}

pub fn local_width(k: usize) -> usize {
    match k {
        0..=256 => 1,
        257..=65536 => 2,
        _ => 4,
    }
}

pub fn dictionary_entries(records: &[PathRecord]) -> BTreeSet<u64> {
    records
        .iter()
        .flat_map(|record| record.indices.iter().copied())
        .collect()
}

pub fn dictionary_core(h: usize, k: usize) -> usize {
    local_width(k) * h + 8 * k
}

pub struct Encoded {
    pub mode: Mode,
    pub bytes: Vec<u8>,
    pub core_bytes: usize,
}

pub fn encode(records: &[PathRecord], scheme: Scheme) -> Result<Encoded, Error> {
    let count = u32::try_from(records.len()).map_err(|_| Error::Overflow)?;
    for record in records {
        record.check()?;
    }
    let h = records
        .iter()
        .map(|record| record.indices.len())
        .sum::<usize>();
    let signatures = SIGNATURE_BYTES
        * records
            .iter()
            .filter(|record| record.signature.is_some())
            .count();
    let frame = BLOCK_FRAME + PATH_COUNT * records.len();
    let max_index = if matches!(scheme, Scheme::Fixed4 | Scheme::Global | Scheme::Adaptive) {
        records
            .iter()
            .flat_map(|record| &record.indices)
            .copied()
            .max()
            .unwrap_or(0)
    } else {
        0
    };
    let u8_eligible = u8::try_from(max_index).is_ok();
    let u32_eligible = u32::try_from(max_index).is_ok();
    let dictionary: Vec<_> =
        if scheme == Scheme::Dictionary || (scheme == Scheme::Adaptive && !u8_eligible) {
            dictionary_entries(records).into_iter().collect()
        } else {
            Vec::new()
        };
    let k = if scheme == Scheme::Adaptive && u8_eligible {
        // Count the hypothetical dictionary entries without building a dictionary.
        // Global u8 and local u8 have the same index width in this case.
        let mut seen = [false; 256];
        for &index in records.iter().flat_map(|record| &record.indices) {
            let narrow = u8::try_from(index).map_err(|_| Error::Overflow)?;
            seen[usize::from(narrow)] = true;
        }
        seen.into_iter().filter(|&present| present).count()
    } else {
        dictionary.len()
    };
    let local_index_width = local_width(k);
    let dict_count = u32::try_from(k).map_err(|_| Error::Overflow)?;
    let u8_layout = (Mode::U8, h);
    let u32_layout = (Mode::U32, 4 * h);
    let u64_layout = (Mode::U64, 8 * h);
    let dict_layout = (Mode::Dictionary, dictionary_core(h, k));
    let wire_size = |(mode, index_bytes)| {
        frame + signatures + index_bytes + usize::from(mode == Mode::Dictionary) * DICTIONARY_COUNT
    };
    let fixed = if u32_eligible { u32_layout } else { u64_layout };
    // Candidate order breaks ties in favor of the narrowest global mode.
    let (mode, index_bytes) = match scheme {
        Scheme::Baseline => u64_layout,
        Scheme::Fixed4 => fixed,
        Scheme::Global if u8_eligible => u8_layout,
        Scheme::Global => fixed,
        Scheme::Dictionary => dict_layout,
        Scheme::Adaptive => [
            u8_eligible.then_some(u8_layout),
            u32_eligible.then_some(u32_layout),
            Some(u64_layout),
            Some(dict_layout),
        ]
        .into_iter()
        .flatten()
        .min_by_key(|&candidate| wire_size(candidate))
        .unwrap(),
    };
    let core_bytes = signatures + index_bytes;
    let capacity = wire_size((mode, index_bytes));
    let mut bytes = Vec::with_capacity(capacity);
    bytes.push(mode as u8);
    bytes.extend_from_slice(&count.to_le_bytes());
    if mode == Mode::Dictionary {
        debug_assert_eq!(k, dictionary.len());
        bytes.extend_from_slice(&dict_count.to_le_bytes());
        for index in &dictionary {
            bytes.extend_from_slice(&index.to_le_bytes());
        }
    }
    for record in records {
        bytes.extend_from_slice(&(record.indices.len() as u16).to_le_bytes());
        if let Some(signature) = &record.signature {
            bytes.extend_from_slice(signature);
        }
        for &index in &record.indices {
            match mode {
                Mode::U8 => bytes.push(u8::try_from(index).map_err(|_| Error::Overflow)?),
                Mode::U64 => bytes.extend_from_slice(&index.to_le_bytes()),
                Mode::U32 => bytes.extend_from_slice(
                    &u32::try_from(index)
                        .map_err(|_| Error::Overflow)?
                        .to_le_bytes(),
                ),
                Mode::Dictionary => {
                    let local = u32::try_from(
                        dictionary
                            .binary_search(&index)
                            .map_err(|_| Error::UnknownIndex)?,
                    )
                    .map_err(|_| Error::Overflow)?;
                    bytes.extend_from_slice(&local.to_le_bytes()[..local_index_width]);
                }
            }
        }
    }
    debug_assert_eq!(bytes.len(), capacity);
    Ok(Encoded {
        mode,
        bytes,
        core_bytes,
    })
}

struct Reader<'a>(&'a [u8]);

impl<'a> Reader<'a> {
    fn take(&mut self, len: usize) -> Result<&'a [u8], Error> {
        let (field, rest) = self.0.split_at_checked(len).ok_or(Error::Truncated)?;
        self.0 = rest;
        Ok(field)
    }

    fn fixed<const N: usize>(&mut self) -> Result<[u8; N], Error> {
        Ok(self.take(N)?.try_into().unwrap())
    }
}

pub fn decode(bytes: &[u8], valid_index: impl Fn(u64) -> bool) -> Result<Vec<PathRecord>, Error> {
    let mut reader = Reader(bytes);
    let mode = match reader.fixed::<1>()?[0] {
        0 => Mode::U64,
        1 => Mode::U32,
        2 => Mode::Dictionary,
        3 => Mode::U8,
        _ => return Err(Error::InvalidMode),
    };
    let count = u32::from_le_bytes(reader.fixed()?) as usize;
    let mut dictionary = Vec::new();
    if mode == Mode::Dictionary {
        let k = u32::from_le_bytes(reader.fixed()?) as usize;
        let raw = reader.take(k.checked_mul(8).ok_or(Error::Overflow)?)?;
        dictionary.reserve(k);
        for item in raw.chunks_exact(8) {
            let index = u64::from_le_bytes(item.try_into().unwrap());
            if dictionary.last().is_some_and(|&prev| prev >= index) {
                return Err(Error::InvalidDictionary);
            }
            if !valid_index(index) {
                return Err(Error::UnknownIndex);
            }
            dictionary.push(index);
        }
    }
    if count > reader.0.len() / PATH_COUNT {
        return Err(Error::Truncated);
    }
    let width = match mode {
        Mode::U64 => 8,
        Mode::U32 => 4,
        Mode::U8 => 1,
        Mode::Dictionary => local_width(dictionary.len()),
    };
    let mut used = vec![false; dictionary.len()];
    let mut records = Vec::with_capacity(count);
    for _ in 0..count {
        let len = u16::from_le_bytes(reader.fixed()?) as usize;
        let signature = if len > 0 { Some(reader.fixed()?) } else { None };
        let raw = reader.take(len * width)?;
        let mut indices = Vec::with_capacity(len);
        for field in raw.chunks_exact(width) {
            let mut index_bytes = [0; 8];
            index_bytes[..width].copy_from_slice(field);
            let index = u64::from_le_bytes(index_bytes);
            let index = if mode == Mode::Dictionary {
                let local = usize::try_from(index).map_err(|_| Error::UnknownLocalIndex)?;
                let global = *dictionary.get(local).ok_or(Error::UnknownLocalIndex)?;
                used[local] = true;
                global
            } else {
                if !valid_index(index) {
                    return Err(Error::UnknownIndex);
                }
                index
            };
            indices.push(index);
        }
        records.push(PathRecord { indices, signature });
    }
    if !reader.0.is_empty() {
        return Err(Error::TrailingBytes);
    }
    if used.contains(&false) {
        return Err(Error::InvalidDictionary);
    }
    Ok(records)
}

/// The expected digest must come from the authenticated block context.
pub fn commitment(block_hash: &[u8; 32], payload: &[u8]) -> [u8; 32] {
    Sha256::new()
        .chain_update(b"TRAIL/path-compression-experiment/v1")
        .chain_update(block_hash)
        .chain_update(payload)
        .finalize()
        .into()
}

pub fn decode_committed(
    bytes: &[u8],
    block_hash: &[u8; 32],
    expected: &[u8; 32],
    valid_index: impl Fn(u64) -> bool,
) -> Result<Vec<PathRecord>, Error> {
    if commitment(block_hash, bytes) != *expected {
        return Err(Error::CommitmentMismatch);
    }
    decode(bytes, valid_index)
}
