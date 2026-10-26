//! Stable local-import identity and explicit read-only receipt recovery.
use super::{
    MAX_SVG_IMPORT_BYTES, StudioService, audio, invalid_import, validate_importable_asset,
};
use motionwright_domain::{Asset, Change, NativeCapsuleSourceSpec, RevisionStamp, VoiceTrack};
use motionwright_storage::{ApplyOutcome, BlobDescriptor, FileImportIntent, Result, StorageError};
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

fn verify_expected_source(
    actual: &BlobDescriptor,
    expected: Option<&BlobDescriptor>,
) -> Result<()> {
    if expected.is_some_and(|expected| expected != actual) {
        return Err(invalid_import(
            "captured import source differs from its expected SHA-256 or byte count",
        ));
    }
    Ok(())
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
            None,
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
        expected_source: Option<&BlobDescriptor>,
    ) -> Result<ApplyOutcome> {
        // Current scope and new-request revision are checked before source IO.
        let recorded = self
            .store
            .lock()
            .inspect_file_import(project_id, expected, request_id, &intent, true)?;
        if let Some(recorded) = recorded {
            // Known replay does not ingest or repair CAS, and never remeasures
            // a voice take. A normal file import still binds the supplied bytes.
            let source_descriptor =
                motionwright_storage::Store::file_import_source_descriptor_with_limit(
                    source,
                    self.import_limits.source_bytes(),
                )?;
            if source_descriptor.sha256 != recorded.source_sha256 {
                return Err(StorageError::RequestReuse);
            }
            verify_expected_source(&source_descriptor, expected_source)?;
            let source_sha256 = source_descriptor.sha256;
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
        // The descriptor is measured by this same retained capture. A mismatch
        // drops only its private stage before publication, decoding or apply.
        verify_expected_source(staged.descriptor(), expected_source)?;
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

#[cfg(test)]
mod verified_source_tests {
    use super::*;
    use motionwright_domain::{DomainError, Fidelity, Project, RationalTime};
    use motionwright_storage::ProjectEvent;
    use std::{fs, path::PathBuf};

    fn fixture() -> (tempfile::TempDir, StudioService, Project) {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let project = service.create_project("Verified source import").unwrap();
        (root, service, project)
    }

    fn descriptor(bytes: &[u8]) -> BlobDescriptor {
        BlobDescriptor {
            sha256: hex::encode(Sha256::digest(bytes)),
            size_bytes: bytes.len() as u64,
        }
    }

    fn asset_intent() -> FileImportIntent {
        FileImportIntent::Asset {
            name: "original-source.bin".into(),
            media_type: "application/octet-stream".into(),
        }
    }

    fn state(service: &StudioService, project_id: Uuid) -> (Project, Vec<ProjectEvent>) {
        (
            service.project(project_id).unwrap(),
            service.history(project_id, 0, 256).unwrap(),
        )
    }

    fn no_published_blob(service: &StudioService, source: &BlobDescriptor) {
        assert!(matches!(
            service.store().lock().read_blob(&source.sha256, source.size_bytes),
            Err(StorageError::BlobMissing { digest }) if digest == source.sha256
        ));
        let staging = service.store().lock().blob_root().join(".staging");
        match fs::read_dir(staging) {
            Ok(mut files) => assert!(files.next().is_none(), "rejected private capture leaked"),
            Err(error) => assert_eq!(error.kind(), std::io::ErrorKind::NotFound),
        }
    }

    fn source(root: &Path, bytes: &[u8]) -> PathBuf {
        let path = root.join("source.bin");
        fs::write(&path, bytes).unwrap();
        path
    }

    #[test]
    fn verified_asset_mismatch_rejects_sha_and_size_before_publication_or_apply() {
        let (root, service, project) = fixture();
        let bytes = b"independently observed immutable source";
        let path = source(root.path(), bytes);
        let actual = descriptor(bytes);
        let before = state(&service, project.id);
        for (request, expected) in [
            (
                "wrong-source-sha",
                BlobDescriptor {
                    sha256: "0".repeat(64),
                    ..actual.clone()
                },
            ),
            (
                "wrong-source-size",
                BlobDescriptor {
                    size_bytes: actual.size_bytes + 1,
                    ..actual.clone()
                },
            ),
        ] {
            let error = service
                .import_file_verified(
                    project.id,
                    &project.stamp(),
                    request,
                    &path,
                    &expected,
                    asset_intent(),
                )
                .unwrap_err();
            assert!(
                matches!(&error, StorageError::Domain(DomainError::Invalid(message))
                if message.contains("expected SHA-256 or byte count"))
            );
            assert_eq!(state(&service, project.id), before);
            assert!(
                service
                    .recover_file_import(project.id, &project.stamp(), request, &asset_intent(),)
                    .unwrap()
                    .is_none()
            );
            no_published_blob(&service, &actual);
            assert_eq!(fs::read(&path).unwrap(), bytes);
        }
    }

    #[test]
    fn verified_asset_commits_one_revision_and_reopens_the_exact_original() {
        let (root, service, project) = fixture();
        let bytes = b"canonical immutable original";
        let path = source(root.path(), bytes);
        let expected = descriptor(bytes);
        let outcome = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "verified-asset",
                &path,
                &expected,
                asset_intent(),
            )
            .unwrap();
        assert!(!outcome.replayed);
        assert_eq!(outcome.previous_revision, project.revision);
        assert_eq!(outcome.project.revision, project.revision + 1);
        assert_eq!(outcome.project.assets.len(), 1);
        assert_eq!(
            outcome.project.assets[0].content_sha256.as_deref(),
            Some(expected.sha256.as_str())
        );
        assert_eq!(
            outcome.project.assets[0].source_revision.as_deref(),
            Some("local-import")
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            bytes
        );
        let committed = state(&service, project.id);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        assert_eq!(state(&reopened, project.id), committed);
        assert_eq!(
            reopened
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            bytes
        );
        let mut historical = outcome;
        historical.replayed = true;
        assert_eq!(
            reopened
                .recover_file_import(
                    project.id,
                    &committed.0.stamp(),
                    "verified-asset",
                    &asset_intent(),
                )
                .unwrap(),
            Some(historical)
        );
    }

    #[test]
    fn verified_known_replay_checks_size_without_repairing_cas_or_advancing_head() {
        let (root, service, project) = fixture();
        let bytes = b"historical source retained outside CAS";
        let path = source(root.path(), bytes);
        let expected = descriptor(bytes);
        let original = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "verified-replay",
                &path,
                &expected,
                asset_intent(),
            )
            .unwrap();
        let advanced = service
            .apply(
                project.id,
                &original.project.stamp(),
                "advance-after-import",
                &Change::RenameProject {
                    title: "Current head after original import".into(),
                },
            )
            .unwrap()
            .project;
        let blob = service
            .store()
            .lock()
            .verified_blob_path(&expected.sha256)
            .unwrap();
        fs::remove_file(&blob).unwrap();
        let before = state(&service, project.id);
        let wrong_size = BlobDescriptor {
            size_bytes: expected.size_bytes + 1,
            ..expected.clone()
        };
        let error = service
            .import_file_verified(
                project.id,
                &advanced.stamp(),
                "verified-replay",
                &path,
                &wrong_size,
                asset_intent(),
            )
            .unwrap_err();
        assert!(
            matches!(&error, StorageError::Domain(DomainError::Invalid(message))
            if message.contains("expected SHA-256 or byte count"))
        );
        assert_eq!(state(&service, project.id), before);
        assert!(!blob.exists());
        let replay = service
            .import_file_verified(
                project.id,
                &advanced.stamp(),
                "verified-replay",
                &path,
                &expected,
                asset_intent(),
            )
            .unwrap();
        let mut historical = original;
        historical.replayed = true;
        assert_eq!(replay, historical);
        assert_eq!(state(&service, project.id), before);
        assert!(
            !blob.exists(),
            "known replay must not repair the historical CAS source"
        );
        assert_eq!(fs::read(path).unwrap(), bytes);
    }

    #[test]
    fn verified_known_replay_rejects_changed_source_with_the_original_request_guard() {
        let (root, service, project) = fixture();
        let path = source(root.path(), b"source-A");
        let expected = descriptor(b"source-A");
        let original = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "verified-reuse",
                &path,
                &expected,
                asset_intent(),
            )
            .unwrap();
        let before = state(&service, project.id);
        let missing = root.path().join("not-opened-on-intent-conflict.bin");
        for intent in [
            FileImportIntent::Asset {
                name: "different-name.bin".into(),
                media_type: "application/octet-stream".into(),
            },
            FileImportIntent::Voice {
                name: "source.wav".into(),
                media_type: "audio/wav".into(),
                label: "Conflicting purpose".into(),
            },
        ] {
            assert!(matches!(
                service.import_file_verified(
                    project.id,
                    &original.project.stamp(),
                    "verified-reuse",
                    &missing,
                    &expected,
                    intent,
                ),
                Err(StorageError::RequestReuse)
            ));
            assert_eq!(state(&service, project.id), before);
        }
        assert!(!missing.exists());
        fs::write(&path, b"source-B").unwrap();
        let error = service
            .import_file_verified(
                project.id,
                &original.project.stamp(),
                "verified-reuse",
                &path,
                &expected,
                asset_intent(),
            )
            .unwrap_err();
        assert!(matches!(error, StorageError::RequestReuse));
        assert_eq!(state(&service, project.id), before);
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            b"source-A"
        );
        no_published_blob(&service, &descriptor(b"source-B"));
    }

    #[test]
    fn verified_new_import_keeps_scope_and_revision_guards_before_source_io() {
        let (root, service, project) = fixture();
        let advanced = service
            .apply(
                project.id,
                &project.stamp(),
                "advance-before-import",
                &Change::RenameProject {
                    title: "Advanced before source access".into(),
                },
            )
            .unwrap()
            .project;
        let missing = root.path().join("never-created-source.bin");
        let expected = descriptor(b"source");
        let before = state(&service, project.id);
        let stale = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "stale-verified-source",
                &missing,
                &expected,
                asset_intent(),
            )
            .unwrap_err();
        assert!(matches!(stale, StorageError::Conflict { expected, actual }
            if expected == project.revision && actual == advanced.revision));
        let mut foreign = advanced.stamp();
        foreign.generation = Uuid::from_u128(7);
        assert!(matches!(
            service.import_file_verified(
                project.id,
                &foreign,
                "foreign-verified-source",
                &missing,
                &expected,
                asset_intent(),
            ),
            Err(StorageError::GenerationConflict)
        ));
        let mut foreign = advanced.stamp();
        foreign.resource = "project:foreign".into();
        assert!(matches!(
            service.import_file_verified(
                project.id,
                &foreign,
                "foreign-resource-source",
                &missing,
                &expected,
                asset_intent(),
            ),
            Err(StorageError::GenerationConflict)
        ));
        assert_eq!(state(&service, project.id), before);
        no_published_blob(&service, &expected);
        assert!(!missing.exists());
    }

    #[test]
    fn verified_native_capsule_preserves_scene_admission_and_unavailable_fidelity() {
        let (root, service, project) = fixture();
        let project = service
            .apply(
                project.id,
                &project.stamp(),
                "source-scene",
                &Change::AddScene {
                    name: "Source capsule".into(),
                    objective: "Preserve an authored source without native acceptance".into(),
                    duration_seconds: 6,
                },
            )
            .unwrap()
            .project;
        let bytes = br#"{"OTIO_SCHEMA":"Timeline.1","name":"original source"}"#;
        let path = source(root.path(), bytes);
        let expected = descriptor(bytes);
        let spec = NativeCapsuleSourceSpec {
            name: "original-source.otio".into(),
            media_type: "application/vnd.opentimelineio+json".into(),
            capsule_id: Uuid::from_u128(42),
            scene_id: project.scenes[0].id,
            label: "Opaque source without native execution claims".into(),
            editable_parameters: Vec::new(),
            native_editor_hint: "OpenTimelineIO".into(),
        };
        let mut invalid = spec.clone();
        invalid.scene_id = Uuid::from_u128(43);
        let before = state(&service, project.id);
        assert!(
            service
                .import_file_verified(
                    project.id,
                    &project.stamp(),
                    "missing-capsule-scene",
                    &path,
                    &expected,
                    FileImportIntent::NativeCapsuleSource { spec: invalid },
                )
                .is_err()
        );
        assert_eq!(state(&service, project.id), before);
        no_published_blob(&service, &expected);
        let outcome = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "verified-capsule",
                &path,
                &expected,
                FileImportIntent::NativeCapsuleSource { spec },
            )
            .unwrap();
        assert_eq!(outcome.project.revision, project.revision + 1);
        assert_eq!(outcome.project.assets.len(), 1);
        let capsule = &outcome.project.production_design.capsules[0];
        assert_eq!(capsule.source_sha256, expected.sha256);
        assert_eq!(capsule.source_asset_id, outcome.project.assets[0].id);
        for observed in [
            &capsule.fidelity.visual,
            &capsule.fidelity.temporal,
            &capsule.fidelity.structural,
            &capsule.fidelity.editable,
        ] {
            assert_eq!(observed, &Fidelity::Unavailable);
        }
        assert!(capsule.fidelity.evidence_sha256.is_none());
        assert!(capsule.property_fidelity.is_empty());
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            bytes
        );
    }

    fn synthetic_stereo_wav() -> Vec<u8> {
        let frames = 48_000u32;
        let data_bytes = frames * 4;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16u32.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&2u16.to_le_bytes());
        bytes.extend_from_slice(&48_000u32.to_le_bytes());
        bytes.extend_from_slice(&192_000u32.to_le_bytes());
        bytes.extend_from_slice(&4u16.to_le_bytes());
        bytes.extend_from_slice(&16u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_bytes.to_le_bytes());
        for _ in 0..frames * 2 {
            bytes.extend_from_slice(&19i16.to_le_bytes());
        }
        bytes
    }

    #[test]
    fn verified_voice_keeps_measurement_before_commit_and_original_pcm_binding() {
        let (root, service, project) = fixture();
        let path = source(root.path(), b"invalid WAV source");
        let voice = FileImportIntent::Voice {
            name: "synthetic-source.wav".into(),
            media_type: "audio/wav".into(),
            label: "Synthetic measured import fixture".into(),
        };
        let malformed = descriptor(b"invalid WAV source");
        let before = state(&service, project.id);
        assert!(
            service
                .import_file_verified(
                    project.id,
                    &project.stamp(),
                    "invalid-measured-source",
                    &path,
                    &malformed,
                    voice.clone(),
                )
                .is_err()
        );
        assert_eq!(state(&service, project.id), before);
        no_published_blob(&service, &malformed);
        let bytes = synthetic_stereo_wav();
        fs::write(&path, &bytes).unwrap();
        let expected = descriptor(&bytes);
        let wrong = BlobDescriptor {
            size_bytes: expected.size_bytes - 1,
            ..expected.clone()
        };
        let error = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "wrong-voice-descriptor",
                &path,
                &wrong,
                voice.clone(),
            )
            .unwrap_err();
        assert!(
            matches!(&error, StorageError::Domain(DomainError::Invalid(message))
            if message.contains("expected SHA-256 or byte count"))
        );
        assert_eq!(state(&service, project.id), before);
        no_published_blob(&service, &expected);
        let outcome = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "verified-measured-source",
                &path,
                &expected,
                voice,
            )
            .unwrap();
        assert_eq!(outcome.project.revision, project.revision + 1);
        assert_eq!(outcome.project.audio.voice_tracks.len(), 1);
        let track = &outcome.project.audio.voice_tracks[0];
        assert_eq!(track.source_sha256, expected.sha256);
        assert_eq!(track.sample_rate_hz, 48_000);
        assert_eq!(track.channels, 2);
        assert_eq!(track.measured_duration, RationalTime::new(1, 1).unwrap());
        assert_eq!(track.asset_id, outcome.project.assets[0].id);
        assert!(track.loudness_lufs.is_none() && track.true_peak_dbfs.is_none());
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            bytes
        );
    }

    #[test]
    fn verified_asset_descriptor_does_not_bypass_existing_svg_admission() {
        let (root, service, project) = fixture();
        let bytes = br#"<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>"#;
        let path = root.path().join("poster.png");
        fs::write(&path, bytes).unwrap();
        let expected = descriptor(bytes);
        let before = state(&service, project.id);
        let error = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "verified-active-svg",
                &path,
                &expected,
                FileImportIntent::Asset {
                    name: "poster.png".into(),
                    media_type: "image/png".into(),
                },
            )
            .unwrap_err();
        assert!(matches!(
            error,
            StorageError::Domain(DomainError::Invalid(_))
        ));
        assert_eq!(state(&service, project.id), before);
        no_published_blob(&service, &expected);
    }
}
