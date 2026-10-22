//! Host-owned resource limits for local file-import admission.
//!
//! A native host may reduce these product ceilings before constructing a
//! service. No policy is deserialized from requests or read from environment
//! variables. Limits are joint and do not promise a wall-time or decoder-RAM cap.

use super::invalid_import;
use motionwright_storage::Result as StorageResult;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ImportLimits {
    source_bytes: u64,
    audio_seconds: u64,
    decoded_samples: u64,
    demux_packets: u64,
    packet_bytes: u64,
    packet_samples: u64,
    audio_read_bytes: u64,
}

impl Default for ImportLimits {
    fn default() -> Self {
        Self {
            source_bytes: 256 * 1024 * 1024,
            audio_seconds: 3_600,
            decoded_samples: 345_600_000,
            demux_packets: 1_000_000,
            packet_bytes: 16 * 1024 * 1024,
            packet_samples: 1_048_576,
            audio_read_bytes: 2 * 1024 * 1024 * 1024,
        }
    }
}

impl ImportLimits {
    /// Maximum bytes in the supplied source, for new capture and known replay.
    pub const fn source_bytes(&self) -> u64 {
        self.source_bytes
    }

    /// Maximum measured audio duration, compared without fractional rounding.
    pub const fn audio_seconds(&self) -> u64 {
        self.audio_seconds
    }

    /// Maximum cumulative decoded samples, counting every channel.
    pub const fn decoded_samples(&self) -> u64 {
        self.decoded_samples
    }

    /// Maximum demuxed packets, including packets from unselected tracks.
    pub const fn demux_packets(&self) -> u64 {
        self.demux_packets
    }

    /// Maximum encoded bytes in one demuxed packet.
    pub const fn packet_bytes(&self) -> u64 {
        self.packet_bytes
    }

    /// Maximum samples returned by one decode, counting every channel.
    pub const fn packet_samples(&self) -> u64 {
        self.packet_samples
    }

    /// Maximum cumulative accepted source reads, including repeated reads.
    /// Detection may read one additional byte; seeking never resets the budget.
    pub const fn audio_read_bytes(&self) -> u64 {
        self.audio_read_bytes
    }

    pub fn with_source_bytes(mut self, value: u64) -> StorageResult<Self> {
        self.source_bytes = reduced_limit("source_bytes", self.source_bytes, value)?;
        Ok(self)
    }

    pub fn with_audio_seconds(mut self, value: u64) -> StorageResult<Self> {
        self.audio_seconds = reduced_limit("audio_seconds", self.audio_seconds, value)?;
        Ok(self)
    }

    pub fn with_decoded_samples(mut self, value: u64) -> StorageResult<Self> {
        self.decoded_samples = reduced_limit("decoded_samples", self.decoded_samples, value)?;
        Ok(self)
    }

    pub fn with_demux_packets(mut self, value: u64) -> StorageResult<Self> {
        self.demux_packets = reduced_limit("demux_packets", self.demux_packets, value)?;
        Ok(self)
    }

    pub fn with_packet_bytes(mut self, value: u64) -> StorageResult<Self> {
        self.packet_bytes = reduced_limit("packet_bytes", self.packet_bytes, value)?;
        Ok(self)
    }

    pub fn with_packet_samples(mut self, value: u64) -> StorageResult<Self> {
        self.packet_samples = reduced_limit("packet_samples", self.packet_samples, value)?;
        Ok(self)
    }

    pub fn with_audio_read_bytes(mut self, value: u64) -> StorageResult<Self> {
        self.audio_read_bytes = reduced_limit("audio_read_bytes", self.audio_read_bytes, value)?;
        Ok(self)
    }
}

fn reduced_limit(name: &'static str, current: u64, value: u64) -> StorageResult<u64> {
    if value == 0 || value > current {
        return Err(invalid_import(format!(
            "import limit {name} must be in 1..={current}; requested {value}"
        )));
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::DomainError;
    use motionwright_storage::StorageError;

    type Getter = fn(&ImportLimits) -> u64;
    type Setter = fn(ImportLimits, u64) -> StorageResult<ImportLimits>;
    type LimitCase = (&'static str, Getter, Setter);

    const LIMITS: [LimitCase; 7] = [
        (
            "source_bytes",
            ImportLimits::source_bytes,
            ImportLimits::with_source_bytes,
        ),
        (
            "audio_seconds",
            ImportLimits::audio_seconds,
            ImportLimits::with_audio_seconds,
        ),
        (
            "decoded_samples",
            ImportLimits::decoded_samples,
            ImportLimits::with_decoded_samples,
        ),
        (
            "demux_packets",
            ImportLimits::demux_packets,
            ImportLimits::with_demux_packets,
        ),
        (
            "packet_bytes",
            ImportLimits::packet_bytes,
            ImportLimits::with_packet_bytes,
        ),
        (
            "packet_samples",
            ImportLimits::packet_samples,
            ImportLimits::with_packet_samples,
        ),
        (
            "audio_read_bytes",
            ImportLimits::audio_read_bytes,
            ImportLimits::with_audio_read_bytes,
        ),
    ];

    fn assert_invalid(name: &str, result: StorageResult<ImportLimits>) {
        assert!(
            matches!(result, Err(StorageError::Domain(DomainError::Invalid(_)))),
            "{name}: expected a domain-invalid limit"
        );
    }

    #[test]
    fn every_limit_rejects_zero_and_an_increase() {
        let original = ImportLimits::default();
        for (name, get, set) in LIMITS {
            assert_invalid(name, set(original, 0));
            assert_invalid(name, set(original, get(&original) + 1));
            assert_invalid(name, set(original, u64::MAX));
        }
    }

    #[test]
    fn reduced_limits_cannot_be_raised_again() {
        let original = ImportLimits::default();
        for (name, get, set) in LIMITS {
            let value = get(&original) / 2;
            let reduced = set(original, value).unwrap();
            assert_eq!(get(&reduced), value, "{name}");
            assert_eq!(set(reduced, value).unwrap(), reduced, "{name}");
            assert_invalid(name, set(reduced, value + 1));
            assert_invalid(name, set(reduced, get(&original)));
            let minimum = set(reduced, 1).unwrap();
            assert_eq!(get(&minimum), 1, "{name}");
            assert_invalid(name, set(minimum, 2));
        }
    }

    #[test]
    fn reducing_a_copy_preserves_the_original_and_other_limits() {
        let original = ImportLimits::default();
        let copied = original;
        let reduced = copied.with_source_bytes(1_024).unwrap();
        assert_eq!(original, ImportLimits::default());
        assert_eq!(copied, original);
        assert_eq!(
            reduced,
            ImportLimits {
                source_bytes: 1_024,
                ..original
            }
        );
    }
}
