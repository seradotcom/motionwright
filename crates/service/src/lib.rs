mod audio;
mod jobs;
mod models;
pub use audio::AudioMeasurement;
pub use jobs::{
    ProductionJobApplicability, ProductionJobProgress, ProductionJobProjection, ProductionJobState,
    ProductionObservationState,
};
pub use models::{
    ModelContextDisclosure, ModelProviderKind, ModelRequestDraft, ModelRequestPreflight,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VoiceImportMetadata {
    pub name: String,
    pub media_type: String,
    pub label: String,
}

use motionwright_domain::{Asset, Change, DomainError, Project, RevisionStamp, VoiceTrack};
use motionwright_storage::{ApplyOutcome, Result as StorageResult, StorageError, Store};
pub use motionwright_storage::{
    BlobDescriptor, BundleImportPlan, ImportPlan, PortableBlob, ProductionReceipt,
    ProductionReceiptInput, ProjectBackup, ProjectBundleManifest, ProjectCursor, ProjectEvent,
    ProjectPage, ProjectSummary,
};
use parking_lot::Mutex;
use std::{fs, fs::File, io::Read, path::Path, sync::Arc};
use uuid::Uuid;

const MAX_SVG_IMPORT_BYTES: u64 = 8 * 1024 * 1024;
const SVG_SNIFF_BYTES: usize = 16 * 1024;

fn invalid_import(message: impl Into<String>) -> StorageError {
    DomainError::Invalid(message.into()).into()
}

fn source_looks_like_svg(source: &Path) -> StorageResult<bool> {
    let mut input = File::open(source)?;
    let mut prefix = vec![0_u8; SVG_SNIFF_BYTES];
    let read = input.read(&mut prefix)?;
    prefix.truncate(read);
    let prefix = String::from_utf8_lossy(&prefix).to_ascii_lowercase();
    Ok(prefix.contains("<svg"))
}

fn has_event_handler(text: &str) -> bool {
    let bytes = text.as_bytes();
    let mut index = 0;
    while index + 3 < bytes.len() {
        let boundary = bytes[index].is_ascii_whitespace() || bytes[index] == b'<';
        if boundary && bytes[index + 1] == b'o' && bytes[index + 2] == b'n' {
            let mut cursor = index + 3;
            let name_start = cursor;
            while cursor < bytes.len()
                && (bytes[cursor].is_ascii_alphanumeric() || bytes[cursor] == b'-')
            {
                cursor += 1;
            }
            if cursor > name_start {
                while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() {
                    cursor += 1;
                }
                if cursor < bytes.len() && bytes[cursor] == b'=' {
                    return true;
                }
            }
        }
        index += 1;
    }
    false
}

fn has_external_href(text: &str) -> bool {
    let mut cursor = text;
    while let Some(index) = cursor.find("href") {
        let after_name = &cursor[index + 4..];
        let after_name = after_name.trim_start();
        if let Some(after_equals) = after_name.strip_prefix('=') {
            let value = after_equals.trim_start();
            let value = if let Some(value) = value.strip_prefix('"') {
                value
            } else if let Some(value) = value.strip_prefix('\'') {
                value
            } else {
                value
            };
            if !value.is_empty() && !value.starts_with('#') {
                return true;
            }
        }
        cursor = &cursor[index + 4..];
    }
    false
}

fn validate_importable_asset(source: &Path, media_type: &str) -> StorageResult<()> {
    let metadata = fs::symlink_metadata(source)?;
    let file_type = metadata.file_type();
    if file_type.is_symlink() || !file_type.is_file() {
        return Err(StorageError::UnsafeSourcePath(
            "asset import requires a regular non-symlink file".into(),
        ));
    }

    let declared_svg = media_type.eq_ignore_ascii_case("image/svg+xml")
        || source
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("svg"));
    if !declared_svg && !source_looks_like_svg(source)? {
        return Ok(());
    }
    if metadata.len() > MAX_SVG_IMPORT_BYTES {
        return Err(StorageError::BlobTooLarge {
            size: metadata.len(),
            limit: MAX_SVG_IMPORT_BYTES,
        });
    }

    let bytes = fs::read(source)?;
    let text = std::str::from_utf8(&bytes)
        .map_err(|_| invalid_import("SVG assets must be valid UTF-8 XML"))?;
    let lowered = text.to_ascii_lowercase();
    let blocked = [
        "<script",
        "<foreignobject",
        "<iframe",
        "<object",
        "<embed",
        "<?xml-stylesheet",
        "<!doctype",
        "<!entity",
        "javascript:",
        "data:text/html",
        "url(",
        "@import",
    ];
    if blocked.iter().any(|marker| lowered.contains(marker))
        || has_event_handler(&lowered)
        || has_external_href(&lowered)
    {
        return Err(invalid_import(
            "active or externally-referencing SVG assets are not admitted",
        ));
    }
    Ok(())
}

#[derive(Clone)]
pub struct StudioService {
    store: Arc<Mutex<Store>>,
}

impl StudioService {
    pub fn open(path: impl AsRef<Path>) -> StorageResult<Self> {
        Ok(Self {
            store: Arc::new(Mutex::new(Store::open(path)?)),
        })
    }

    pub fn from_store(store: Store) -> Self {
        Self {
            store: Arc::new(Mutex::new(store)),
        }
    }

    pub fn store(&self) -> Arc<Mutex<Store>> {
        Arc::clone(&self.store)
    }

    pub fn create_project(&self, title: &str) -> StorageResult<Project> {
        self.store.lock().create_named_project(title)
    }

    pub fn project(&self, id: Uuid) -> StorageResult<Project> {
        self.store.lock().load_project(id)
    }

    pub fn projects(&self, limit: usize) -> StorageResult<Vec<Project>> {
        self.store.lock().list_projects(limit)
    }

    pub fn project_summaries(
        &self,
        cursor: Option<&ProjectCursor>,
        limit: usize,
    ) -> StorageResult<ProjectPage> {
        self.store.lock().list_project_summaries(cursor, limit)
    }

    pub fn export_project(&self, id: Uuid) -> StorageResult<ProjectBackup> {
        self.store.lock().export_project(id)
    }

    pub fn inspect_import(&self, backup: &ProjectBackup) -> StorageResult<ImportPlan> {
        self.store.lock().inspect_import(backup)
    }

    pub fn import_project(&self, backup: &ProjectBackup) -> StorageResult<Project> {
        self.store.lock().import_project(backup)
    }

    pub fn ingest_blob_file(&self, source: impl AsRef<Path>) -> StorageResult<BlobDescriptor> {
        self.store.lock().ingest_blob_file(source)
    }

    pub fn import_asset_file(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        source: impl AsRef<Path>,
        name: String,
        media_type: String,
    ) -> StorageResult<ApplyOutcome> {
        let source = source.as_ref();
        validate_importable_asset(source, &media_type)?;
        let mut store = self.store.lock();
        let blob = store.ingest_blob_file(source)?;
        let change = Change::AddAsset {
            asset: Asset {
                id: Uuid::now_v7(),
                name,
                media_type,
                content_sha256: Some(blob.sha256),
                source_revision: Some("local-import".into()),
            },
        };
        store.apply(project_id, expected, request_id, &change)
    }

    pub fn import_voice_file(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        source: impl AsRef<Path>,
        metadata: VoiceImportMetadata,
    ) -> StorageResult<ApplyOutcome> {
        if !metadata.media_type.starts_with("audio/") {
            return Err(motionwright_domain::DomainError::Invalid(
                "voice import requires an audio media type".into(),
            )
            .into());
        }
        let source = source.as_ref();
        let (blob, immutable_path) = {
            let store = self.store.lock();
            let blob = store.ingest_blob_file(source)?;
            let immutable_path = store.verified_blob_path(&blob.sha256)?;
            (blob, immutable_path)
        };
        let measured = audio::measure_audio_file(&immutable_path)?;
        let asset_id = Uuid::now_v7();
        let asset = Asset {
            id: asset_id,
            name: metadata.name,
            media_type: metadata.media_type,
            content_sha256: Some(blob.sha256.clone()),
            source_revision: Some("measured-audio-import".into()),
        };
        let track = VoiceTrack {
            id: Uuid::now_v7(),
            asset_id,
            label: metadata.label,
            sample_rate_hz: measured.sample_rate_hz,
            channels: measured.channels,
            measured_duration: measured.duration,
            source_sha256: blob.sha256,
            loudness_lufs: None,
            true_peak_dbfs: None,
        };
        self.store.lock().apply(
            project_id,
            expected,
            request_id,
            &Change::ImportMeasuredVoice { asset, track },
        )
    }

    pub fn export_project_bundle(
        &self,
        id: Uuid,
        destination: impl AsRef<Path>,
    ) -> StorageResult<ProjectBundleManifest> {
        self.store.lock().export_project_bundle(id, destination)
    }

    pub fn inspect_project_bundle(
        &self,
        source: impl AsRef<Path>,
    ) -> StorageResult<BundleImportPlan> {
        self.store.lock().inspect_project_bundle(source)
    }

    pub fn import_project_bundle(&self, source: impl AsRef<Path>) -> StorageResult<Project> {
        self.store.lock().import_project_bundle(source)
    }

    pub fn append_production_receipt(
        &self,
        input: ProductionReceiptInput,
    ) -> StorageResult<ProductionReceipt> {
        self.store.lock().append_production_receipt(input)
    }

    pub fn latest_production_receipt(
        &self,
        project_id: Uuid,
        request_id: &str,
    ) -> StorageResult<Option<ProductionReceipt>> {
        self.store
            .lock()
            .latest_production_receipt(project_id, request_id)
    }

    pub fn production_receipts(
        &self,
        project_id: Uuid,
        limit: usize,
    ) -> StorageResult<Vec<ProductionReceipt>> {
        self.store.lock().production_receipts(project_id, limit)
    }

    pub fn model_request_preflight(
        &self,
        project_id: Uuid,
        draft: &ModelRequestDraft,
    ) -> StorageResult<ModelRequestPreflight> {
        let store = self.store.lock();
        let project = store.load_project(project_id)?;
        models::build_model_request_preflight(&store, &project, draft)
    }

    pub fn production_jobs(
        &self,
        project_id: Uuid,
        limit: usize,
    ) -> StorageResult<Vec<ProductionJobProjection>> {
        let store = self.store.lock();
        let project = store.load_project(project_id)?;
        let receipts = store.production_receipts(project_id, 256)?;
        Ok(jobs::derive_production_jobs(
            &receipts,
            project.generation,
            project.revision,
            limit,
        ))
    }

    pub fn history(
        &self,
        id: Uuid,
        after_revision: u64,
        limit: usize,
    ) -> StorageResult<Vec<ProjectEvent>> {
        self.store
            .lock()
            .event_records_since(id, after_revision, limit)
    }

    pub fn apply(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        change: &Change,
    ) -> StorageResult<ApplyOutcome> {
        self.store.lock().apply(id, expected, request_id, change)
    }
}

#[cfg(test)]
mod asset_import_security_tests {
    use super::*;

    #[test]
    fn static_svg_is_admitted_as_immutable_asset() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("Static SVG").unwrap();
        let source = temp.path().join("mark.svg");
        fs::write(
            &source,
            br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M1 1h18v18H1z"/></svg>"#,
        )
        .unwrap();

        let outcome = service
            .import_asset_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "static-svg",
                &source,
                "mark.svg".into(),
                "image/svg+xml".into(),
            )
            .unwrap();

        assert_eq!(outcome.project.assets.len(), 1);
        let asset = &outcome.project.assets[0];
        assert_eq!(asset.media_type, "image/svg+xml");
        assert!(asset.content_sha256.is_some());
    }

    #[test]
    fn active_svg_is_rejected_even_when_mislabeled() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("Active SVG").unwrap();
        let source = temp.path().join("poster.png");
        fs::write(
            &source,
            br#"<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>"#,
        )
        .unwrap();

        let error = service
            .import_asset_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "active-svg",
                &source,
                "poster.png".into(),
                "image/png".into(),
            )
            .unwrap_err();

        assert!(matches!(
            error,
            StorageError::Domain(DomainError::Invalid(_))
        ));
        assert!(service.project(initial.id).unwrap().assets.is_empty());
    }

    #[test]
    fn svg_external_references_are_rejected() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("External SVG").unwrap();
        let source = temp.path().join("external.svg");
        fs::write(
            &source,
            br#"<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/pixel.png"/></svg>"#,
        )
        .unwrap();

        let error = service
            .import_asset_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "external-svg",
                &source,
                "external.svg".into(),
                "image/svg+xml".into(),
            )
            .unwrap_err();

        assert!(matches!(
            error,
            StorageError::Domain(DomainError::Invalid(_))
        ));
        assert!(service.project(initial.id).unwrap().assets.is_empty());
    }
}

#[cfg(test)]
mod audio_import_tests {
    use super::*;
    use motionwright_domain::RationalTime;
    use std::{fs::File, io::Write};

    fn write_pcm16_wav(path: &Path, sample_rate: u32, channels: u16, frames: u32, sample: i16) {
        let data_bytes = frames * u32::from(channels) * 2;
        let mut file = File::create(path).unwrap();
        file.write_all(b"RIFF").unwrap();
        file.write_all(&(36 + data_bytes).to_le_bytes()).unwrap();
        file.write_all(b"WAVEfmt ").unwrap();
        file.write_all(&16_u32.to_le_bytes()).unwrap();
        file.write_all(&1_u16.to_le_bytes()).unwrap();
        file.write_all(&channels.to_le_bytes()).unwrap();
        file.write_all(&sample_rate.to_le_bytes()).unwrap();
        file.write_all(&(sample_rate * u32::from(channels) * 2).to_le_bytes())
            .unwrap();
        file.write_all(&(channels * 2).to_le_bytes()).unwrap();
        file.write_all(&16_u16.to_le_bytes()).unwrap();
        file.write_all(b"data").unwrap();
        file.write_all(&data_bytes.to_le_bytes()).unwrap();
        for _ in 0..frames * u32::from(channels) {
            file.write_all(&sample.to_le_bytes()).unwrap();
        }
        file.sync_all().unwrap();
    }

    #[test]
    fn measured_voice_import_preserves_prior_take_and_exact_blob_binding() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("Audio evidence").unwrap();

        let first_path = temp.path().join("take-01.wav");
        write_pcm16_wav(&first_path, 48_000, 1, 48_000, 0);
        let first = service
            .import_voice_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "voice-take-1",
                &first_path,
                VoiceImportMetadata {
                    name: "take-01.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Narrator take 01".into(),
                },
            )
            .unwrap()
            .project;

        assert_eq!(first.audio.voice_tracks.len(), 1);
        assert_eq!(first.assets.len(), 1);
        let first_track = first.audio.voice_tracks[0].clone();
        assert_eq!(first.audio.active_voice_track_id, Some(first_track.id));
        assert_eq!(
            first_track.measured_duration,
            RationalTime::new(1, 1).unwrap()
        );
        assert_eq!(
            first.assets[0].content_sha256.as_deref(),
            Some(first_track.source_sha256.as_str())
        );
        assert!(first_track.loudness_lufs.is_none());
        assert!(first_track.true_peak_dbfs.is_none());

        let second_path = temp.path().join("take-02.wav");
        write_pcm16_wav(&second_path, 44_100, 2, 22_050, 7);
        let second = service
            .import_voice_file(
                first.id,
                &RevisionStamp::from(&first),
                "voice-take-2",
                &second_path,
                VoiceImportMetadata {
                    name: "take-02.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Narrator take 02".into(),
                },
            )
            .unwrap()
            .project;

        assert_eq!(second.audio.voice_tracks.len(), 2);
        assert_eq!(second.assets.len(), 2);
        assert!(
            second
                .audio
                .voice_tracks
                .iter()
                .any(|track| track.id == first_track.id)
        );
        let active = second
            .audio
            .voice_tracks
            .iter()
            .find(|track| Some(track.id) == second.audio.active_voice_track_id)
            .unwrap();
        assert_eq!(active.label, "Narrator take 02");
        assert_eq!(active.sample_rate_hz, 44_100);
        assert_eq!(active.channels, 2);
        assert_eq!(active.measured_duration, RationalTime::new(1, 2).unwrap());
        assert_ne!(active.source_sha256, first_track.source_sha256);
    }
}
