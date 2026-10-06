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

use motionwright_domain::{Asset, Change, Project, RevisionStamp, VoiceTrack};
use motionwright_storage::{ApplyOutcome, Result as StorageResult, Store};
pub use motionwright_storage::{
    BlobDescriptor, BundleImportPlan, ImportPlan, PortableBlob, ProductionReceipt,
    ProductionReceiptInput, ProjectBackup, ProjectBundleManifest, ProjectCursor, ProjectEvent,
    ProjectPage, ProjectSummary,
};
use parking_lot::Mutex;
use std::{path::Path, sync::Arc};
use uuid::Uuid;

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
