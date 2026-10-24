//! Stable local-import identity and explicit read-only receipt recovery.
use super::{
    MAX_SVG_IMPORT_BYTES, StudioService, audio, invalid_import, validate_importable_asset,
};
use motionwright_domain::{Asset, Change, NativeCapsuleSourceSpec, RevisionStamp, VoiceTrack};
use motionwright_storage::{ApplyOutcome, FileImportIntent, Result, StorageError};
use sha2::{Digest, Sha256};
use std::path::Path;
use uuid::Uuid;

fn stable_import_id(
    project: Uuid,
    generation: Uuid,
    request_id: &str,
    kind: &str,
    role: &str,
) -> Result<Uuid> {
    let input = serde_json::to_vec(&(
        "motionwright.file-import-id/1",
        project,
        generation,
        request_id,
        kind,
        role,
    ))?;
    let digest = Sha256::digest(input);
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    // RFC variant and UUID version 8: an application-defined SHA-256 identity.
    // This is deliberately not represented as UUIDv5 or a time-based UUID.
    bytes[6] = (bytes[6] & 0x0f) | 0x80;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(Uuid::from_bytes(bytes))
}

fn hash_source_once(source: &Path, max_bytes: u64) -> Result<String> {
    motionwright_storage::Store::hash_file_import_source_with_limit(source, max_bytes)
}

impl StudioService {
    /// Import immutable source and bind its capsule in one canonical revision.
    ///
    /// This imports authored data only. It does not execute a renderer, carry
    /// source fidelity evidence forward, or grant a provider operation.
    pub fn import_native_capsule_source_file(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        source: impl AsRef<Path>,
        spec: NativeCapsuleSourceSpec,
    ) -> Result<ApplyOutcome> {
        self.import_file_with_identity(
            project_id,
            expected,
            request_id,
            source.as_ref(),
            FileImportIntent::NativeCapsuleSource { spec },
        )
    }

    /// Read the original committed result; no original source, CAS or native IO.
    ///
    /// A missing result is an observation, not permission to repeat an UNKNOWN
    /// mutation. Callers must refresh the canonical Project separately because
    /// an existing receipt contains its historical result revision.
    pub fn recover_file_import(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        intent: &FileImportIntent,
    ) -> Result<Option<ApplyOutcome>> {
        Ok(self
            .store
            .lock()
            .inspect_file_import(project_id, expected, request_id, intent, false)?
            .map(|record| record.outcome))
    }

    pub(crate) fn import_file_with_identity(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        source: &Path,
        intent: FileImportIntent,
    ) -> Result<ApplyOutcome> {
        // Current scope and new-request revision are checked before source IO.
        let recorded = self
            .store
            .lock()
            .inspect_file_import(project_id, expected, request_id, &intent, true)?;
        if let Some(recorded) = recorded {
            // Known replay does not ingest or repair CAS, and never remeasures
            // a voice take. A normal file import still binds the supplied bytes.
            let source_sha256 = hash_source_once(source, self.import_limits.source_bytes())?;
            if source_sha256 != recorded.source_sha256 {
                return Err(StorageError::RequestReuse);
            }
            let confirmed = self
                .store
                .lock()
                .inspect_file_import(project_id, expected, request_id, &intent, false)?
                .ok_or_else(|| {
                    StorageError::InvalidFileImportReceipt(
                        "previously observed import receipt disappeared".into(),
                    )
                })?;
            if confirmed.source_sha256 != source_sha256 || confirmed.outcome != recorded.outcome {
                return Err(StorageError::InvalidFileImportReceipt(
                    "observed import receipt changed during source verification".into(),
                ));
            }
            return Ok(confirmed.outcome);
        }

        if let FileImportIntent::Voice { media_type, .. } = &intent
            && !media_type.starts_with("audio/")
        {
            return Err(invalid_import("voice import requires an audio media type"));
        }
        let svg_extension = source
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("svg"));
        let max_bytes = match &intent {
            FileImportIntent::Asset { media_type, .. }
            | FileImportIntent::NativeCapsuleSource {
                spec: NativeCapsuleSourceSpec { media_type, .. },
            } if media_type.eq_ignore_ascii_case("image/svg+xml") || svg_extension => {
                MAX_SVG_IMPORT_BYTES.min(self.import_limits.source_bytes())
            }
            _ => self.import_limits.source_bytes(),
        };
        let staged = self
            .store
            .lock()
            .stage_file_import_with_limit(source, max_bytes)?;
        let source_sha256 = staged.descriptor().sha256.clone();
        let immutable_path = staged.path();
        let change = match &intent {
            FileImportIntent::NativeCapsuleSource { spec } => {
                validate_importable_asset(immutable_path, &spec.media_type, svg_extension)?;
                let asset = Asset {
                    id: stable_import_id(
                        project_id,
                        expected.generation,
                        request_id,
                        "native-capsule-source",
                        "asset",
                    )?,
                    name: spec.name.clone(),
                    media_type: spec.media_type.clone(),
                    content_sha256: Some(source_sha256.clone()),
                    source_revision: Some("native-capsule-source-import".into()),
                };
                let capsule = spec.bind_asset(&asset)?;
                Change::ImportNativeCapsuleSource { asset, capsule }
            }
            FileImportIntent::Asset { name, media_type } => {
                validate_importable_asset(immutable_path, media_type, svg_extension)?;
                Change::AddAsset {
                    asset: Asset {
                        id: stable_import_id(
                            project_id,
                            expected.generation,
                            request_id,
                            "asset",
                            "asset",
                        )?,
                        name: name.clone(),
                        media_type: media_type.clone(),
                        content_sha256: Some(source_sha256.clone()),
                        source_revision: Some("local-import".into()),
                    },
                }
            }
            FileImportIntent::Voice {
                name,
                media_type,
                label,
            } => {
                let measured = audio::measure_audio_file(immutable_path, &self.import_limits)?;
                let asset_id = stable_import_id(
                    project_id,
                    expected.generation,
                    request_id,
                    "voice",
                    "asset",
                )?;
                let asset = Asset {
                    id: asset_id,
                    name: name.clone(),
                    media_type: media_type.clone(),
                    content_sha256: Some(source_sha256.clone()),
                    source_revision: Some("measured-audio-import".into()),
                };
                let track = VoiceTrack {
                    id: stable_import_id(
                        project_id,
                        expected.generation,
                        request_id,
                        "voice",
                        "track",
                    )?,
                    asset_id,
                    label: label.clone(),
                    sample_rate_hz: measured.sample_rate_hz,
                    channels: measured.channels,
                    measured_duration: measured.duration,
                    source_sha256: source_sha256.clone(),
                    loudness_lufs: None,
                    true_peak_dbfs: None,
                };
                Change::ImportMeasuredVoice { asset, track }
            }
        };

        // Release the mutex before inspecting a known request-reuse rejection.
        // No writer transaction is held during file copying or audio decoding.
        let result = {
            let mut store = self.store.lock();
            if let Some(recorded) =
                store.inspect_file_import(project_id, expected, request_id, &intent, true)?
            {
                if recorded.source_sha256 != source_sha256 {
                    return Err(StorageError::RequestReuse);
                }
                return Ok(recorded.outcome);
            }
            if matches!(&change, Change::ImportNativeCapsuleSource { .. }) {
                // Reject invalid capsule metadata and locked/missing scenes
                // before immutable publication. Store.apply still performs the
                // authoritative generation/revision check in its transaction.
                let mut candidate = store.load_project(project_id)?;
                candidate.apply_change(&change)?;
                candidate.validate()?;
            }
            store.publish_file_import(&staged)?;
            drop(staged);
            store.apply(project_id, expected, request_id, &change)
        };
        match result {
            Err(StorageError::RequestReuse) => {
                // An older process can have committed its legacy random IDs
                // after preflight. Observe that receipt; never retry a mutation.
                let recorded = self
                    .store
                    .lock()
                    .inspect_file_import(project_id, expected, request_id, &intent, false)?;
                match recorded {
                    Some(recorded) if recorded.source_sha256 == source_sha256 => {
                        Ok(recorded.outcome)
                    }
                    _ => Err(StorageError::RequestReuse),
                }
            }
            result => result,
        }
    }
}
