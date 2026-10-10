mod audio;
mod jobs;
mod models;
pub use audio::{AudioMeasurement, MAX_WAVEFORM_PAGE_SIZE, WAVEFORM_FRAMES_PER_PEAK, WaveformPage};
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

/// Backend-only verified measured voice input for canonical MLT mastering.
/// Never serialized to the WebView; the path is not an API credential.
pub struct VerifiedMasterVoice {
    pub path: PathBuf,
    pub sha256: String,
    pub size_bytes: u64,
}

use motionwright_domain::{Asset, Change, DomainError, Project, RevisionStamp, VoiceTrack};
use motionwright_storage::{ApplyOutcome, Result as StorageResult, StorageError, Store};
pub use motionwright_storage::{
    AssetIntegrityPage, AssetIntegrityRecord, AssetIntegrityStatus, BlobDescriptor,
    BundleImportPlan, DerivedCacheHit, DerivedCacheRecord, ImportPlan, PortableBlob,
    ProductionReceipt, ProductionReceiptInput, ProductionReceiptWatermark, ProjectBackup,
    ProjectBundleManifest, ProjectCursor, ProjectEvent, ProjectPage, ProjectSummary,
};
use parking_lot::Mutex;
use std::{
    fs,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    sync::Arc,
};
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

    /// Explicit read-only, bounded SHA-256 inspection of assets already
    /// referenced by the exact project revision. No file paths cross IPC.
    pub fn asset_integrity_page(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        offset: Option<usize>,
        limit: usize,
    ) -> StorageResult<AssetIntegrityPage> {
        self.store
            .lock()
            .inspect_asset_integrity_page(project_id, expected, offset, limit)
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

    pub fn put_derived_cache_file(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
        source: impl AsRef<Path>,
        work_units: u64,
    ) -> StorageResult<DerivedCacheRecord> {
        self.store.lock().put_derived_cache_file(
            scope,
            kind,
            fingerprint_sha256,
            source,
            work_units,
        )
    }

    pub fn lookup_derived_cache(
        &self,
        scope: &str,
        kind: &str,
        fingerprint_sha256: &str,
    ) -> StorageResult<Option<DerivedCacheHit>> {
        self.store
            .lock()
            .lookup_derived_cache(scope, kind, fingerprint_sha256)
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

    pub fn waveform_page(
        &self,
        project_id: Uuid,
        track_id: Uuid,
        page_index: u64,
        page_size: usize,
    ) -> StorageResult<WaveformPage> {
        if page_size == 0 || page_size > audio::MAX_WAVEFORM_PAGE_SIZE {
            return Err(invalid_import("waveform page size is out of bounds"));
        }

        let (track, immutable_path) = {
            let store = self.store.lock();
            let project = store.load_project(project_id)?;
            let track = project
                .audio
                .voice_tracks
                .iter()
                .find(|track| track.id == track_id)
                .cloned()
                .ok_or(StorageError::NotFound)?;
            let asset = project
                .assets
                .iter()
                .find(|asset| asset.id == track.asset_id)
                .ok_or(StorageError::NotFound)?;
            if asset.content_sha256.as_deref() != Some(track.source_sha256.as_str()) {
                return Err(StorageError::InvalidDerivedCache(
                    "voice track and immutable asset digests do not match".into(),
                ));
            }
            let immutable_path = store.verified_blob_path(&track.source_sha256)?;
            (track, immutable_path)
        };

        let fingerprint =
            audio::waveform_fingerprint(&track.source_sha256, audio::WAVEFORM_FRAMES_PER_PEAK)?;
        let scope = format!(
            "project:{project_id}:voice:{track_id}:source:{}",
            track.source_sha256
        );
        let kind = "waveform-proxy";

        let hit = match self.lookup_derived_cache(&scope, kind, &fingerprint)? {
            Some(hit) => hit,
            None => {
                let temporary = std::env::temp_dir()
                    .join(format!("motionwright-waveform-{}.mwpeak", Uuid::now_v7()));
                let generated = audio::generate_waveform_proxy(
                    &immutable_path,
                    &track.source_sha256,
                    &temporary,
                    audio::WAVEFORM_FRAMES_PER_PEAK,
                );
                let summary = match generated {
                    Ok(summary) => summary,
                    Err(error) => {
                        let _ = fs::remove_file(&temporary);
                        return Err(error);
                    }
                };

                let duration_matches = i128::from(summary.total_frames)
                    * i128::from(track.measured_duration.den)
                    == i128::from(track.measured_duration.num) * i128::from(track.sample_rate_hz);
                if summary.sample_rate_hz != track.sample_rate_hz
                    || summary.channels != track.channels
                    || !duration_matches
                {
                    let _ = fs::remove_file(&temporary);
                    return Err(StorageError::InvalidDerivedCache(
                        "waveform decode does not match measured voice-track evidence".into(),
                    ));
                }

                let admitted = self.put_derived_cache_file(
                    &scope,
                    kind,
                    &fingerprint,
                    &temporary,
                    summary.peak_count,
                );
                let _ = fs::remove_file(&temporary);
                admitted?;
                self.lookup_derived_cache(&scope, kind, &fingerprint)?
                    .ok_or_else(|| {
                        StorageError::InvalidDerivedCache(
                            "waveform proxy disappeared after cache admission".into(),
                        )
                    })?
            }
        };

        audio::read_waveform_page(
            hit.blob_path,
            &track.source_sha256,
            track.sample_rate_hz,
            track.channels,
            page_index,
            page_size,
        )
    }

    /// Return an existing immutable, SHA-256-verified WAV source bound to a
    /// measured 48 kHz stereo voice take and exact application revision.
    /// This is only called by the owner's trusted desktop mastering command.
    pub fn verified_master_voice(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        track_id: Uuid,
    ) -> StorageResult<VerifiedMasterVoice> {
        const MAX_SOURCE_BYTES: u64 = 256 * 1024 * 1024;
        let store = self.store.lock();
        let project = store.load_project(project_id)?;
        if expected.resource != project.resource_key() || expected.generation != project.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if expected.revision != project.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == track_id)
            .ok_or_else(|| invalid_import("Selected measured voice take is unavailable"))?;
        if track.sample_rate_hz != 48_000 || track.channels != 2 {
            return Err(invalid_import(
                "Native AV mastering requires measured 48 kHz stereo audio",
            ));
        }
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == track.asset_id)
            .ok_or_else(|| invalid_import("Measured voice asset is missing from the project"))?;
        if asset.content_sha256.as_deref() != Some(track.source_sha256.as_str())
            || !matches!(
                asset.media_type.as_str(),
                "audio/wav" | "audio/wave" | "audio/x-wav"
            )
        {
            return Err(invalid_import(
                "Native AV mastering requires the exact SHA-256-bound imported WAV asset",
            ));
        }
        let path = store.verified_blob_path(&track.source_sha256)?;
        let size_bytes = fs::metadata(&path)?.len();
        if !(44..=MAX_SOURCE_BYTES).contains(&size_bytes) {
            return Err(invalid_import(
                "Measured WAV exceeds the bounded AV master input size",
            ));
        }
        Ok(VerifiedMasterVoice {
            path,
            sha256: track.source_sha256.clone(),
            size_bytes,
        })
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

    /// Atomic cross-process application recovery claim, not an execution grant.
    pub fn claim_recovery_stage(
        &self,
        input: ProductionReceiptInput,
    ) -> StorageResult<ProductionReceipt> {
        self.store.lock().claim_recovery_stage(input)
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

    /// Snapshot the locally persisted production receipt stream independently
    /// from the creative project's generation/revision.
    pub fn production_receipt_watermark(
        &self,
        project_id: Uuid,
    ) -> StorageResult<ProductionReceiptWatermark> {
        let store = self.store.lock();
        let _project = store.load_project(project_id)?;
        store.production_receipt_watermark(project_id)
    }

    /// Read a bounded receipt keyset page and reject concurrent changes to
    /// its stream identity even if the creative project revision is unchanged.
    pub fn production_receipts_snapshot_page(
        &self,
        project_id: Uuid,
        expected: &ProductionReceiptWatermark,
        before: Option<Uuid>,
        limit: usize,
    ) -> StorageResult<Vec<ProductionReceipt>> {
        let store = self.store.lock();
        let _project = store.load_project(project_id)?;
        if store.production_receipt_watermark(project_id)? != *expected {
            return Err(StorageError::ReceiptStreamChanged);
        }
        let receipts = match expected.latest_id {
            Some(head) => store.production_receipts_before(project_id, head, before, limit)?,
            None => Vec::new(),
        };
        if store.production_receipt_watermark(project_id)? != *expected {
            return Err(StorageError::ReceiptStreamChanged);
        }
        Ok(receipts)
    }

    /// Reconstruct the full canonical local job projection over a fixed
    /// receipt snapshot, not the old latest-256-receipts UI window. A bounded
    /// ceiling refuses oversized scans instead of claiming fake completeness.
    pub fn production_jobs_snapshot(
        &self,
        project_id: Uuid,
        expected: &ProductionReceiptWatermark,
    ) -> StorageResult<Vec<ProductionJobProjection>> {
        const MAX_JOB_ENUMERATION_RECEIPTS: u64 = 20_000;
        let store = self.store.lock();
        let project = store.load_project(project_id)?;
        if store.production_receipt_watermark(project_id)? != *expected {
            return Err(StorageError::ReceiptStreamChanged);
        }
        if expected.count > MAX_JOB_ENUMERATION_RECEIPTS {
            return Err(invalid_import(
                "Production job enumeration exceeds the bounded 20,000-receipt safety budget; no complete claim was made",
            ));
        }
        let mut receipts = Vec::with_capacity(expected.count as usize);
        let mut before = None;
        if let Some(head) = expected.latest_id {
            loop {
                let batch = store.production_receipts_before(project_id, head, before, 256)?;
                if batch.is_empty() {
                    break;
                }
                before = batch.last().map(|item| item.id);
                let full_page = batch.len() == 256;
                receipts.extend(batch);
                if !full_page {
                    break;
                }
            }
        }
        if receipts.len() as u64 != expected.count
            || store.production_receipt_watermark(project_id)? != *expected
        {
            return Err(StorageError::ReceiptStreamChanged);
        }
        Ok(jobs::derive_production_jobs_all(
            &receipts,
            project.generation,
            project.revision,
        ))
    }

    pub fn history_recent(
        &self,
        id: Uuid,
        through_revision: u64,
        limit: usize,
    ) -> StorageResult<Vec<ProjectEvent>> {
        self.store
            .lock()
            .event_records_through(id, through_revision, limit)
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
mod native_linear_motion_service_tests {
    use super::*;
    use motionwright_domain::{
        BlendMode, CanvasNode, CoordinateSpace, MotionInterpolation, MotionProperty, NodeStyle,
    };
    use std::collections::BTreeSet;

    #[test]
    fn one_native_linear_motion_change_is_one_durable_cas_revision() {
        let folder = tempfile::tempdir().unwrap();
        let service = StudioService::open(folder.path().join("motionwright.sqlite3")).unwrap();
        let project = service.create_project("Durable native motion").unwrap();
        let project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-motion-create-scene",
                &Change::AddScene {
                    name: "Keyed native scene".into(),
                    objective: "real four-point Motion Canvas authoring".into(),
                    duration_seconds: 3,
                },
            )
            .unwrap()
            .project;
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: "Animated tile".into(),
            kind: "rectangle".into(),
            parent_id: None,
            x: 780.0,
            y: 320.0,
            width: 180.0,
            height: 100.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 1,
            style: NodeStyle {
                fill: Some("#F5F5F2".into()),
                stroke: None,
                stroke_width: 0.0,
                font_family: None,
                font_size: None,
                font_weight: None,
                line_height: None,
                blend_mode: BlendMode::Normal,
            },
            relations: Vec::new(),
            property_locks: BTreeSet::new(),
            keyframes: Vec::new(),
        };
        let node_id = node.id;
        let scene_id = project.scenes[0].id;
        let project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-motion-create-tile",
                &Change::AddCanvasNode { scene_id, node },
            )
            .unwrap()
            .project;
        let profile_id = project.deliverables[0].id;
        let base_revision = project.revision;
        let move_change = Change::SetCanvasLinearPositionMotion {
            scene_id,
            node_id,
            deliverable_id: profile_id,
            start_x: 600.0,
            start_y: 320.0,
            end_frame: 30,
        };
        let result = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-motion-one-transaction",
                &move_change,
            )
            .unwrap()
            .project;
        assert_eq!(result.revision, base_revision + 1);
        assert_eq!(result.scenes[0].nodes[0].keyframes.len(), 4);
        assert_eq!(result.scenes[0].nodes[0].x, 780.0);
        assert_eq!(result.scenes[0].nodes[0].keyframes[0].value, 600.0);
        assert_eq!(
            result.scenes[0].nodes[0].keyframes[3].property,
            MotionProperty::Y
        );
        assert_eq!(
            result.scenes[0].nodes[0].keyframes[3].interpolation,
            MotionInterpolation::Linear,
        );
        assert_eq!(
            service
                .history(project.id, base_revision, 10)
                .unwrap()
                .len(),
            1
        );
        assert!(
            service
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    "native-motion-stale-base",
                    &move_change
                )
                .is_err()
        );
        assert!(
            service
                .apply(
                    project.id,
                    &RevisionStamp::from(&result),
                    "native-motion-must-not-overwrite",
                    &move_change
                )
                .is_err()
        );
        let reopened = StudioService::open(folder.path().join("motionwright.sqlite3")).unwrap();
        let readback = reopened.project(project.id).unwrap();
        assert_eq!(readback.revision, result.revision);
        assert_eq!(
            readback.scenes[0].nodes[0].keyframes,
            result.scenes[0].nodes[0].keyframes
        );
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
    fn native_master_voice_requires_exact_measured_stereo_wav_blob_and_revision() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("Native AV voice").unwrap();

        let mono_path = temp.path().join("mono.wav");
        write_pcm16_wav(&mono_path, 48_000, 1, 1024, 0);
        let mono = service
            .import_voice_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "mono-measure",
                &mono_path,
                VoiceImportMetadata {
                    name: "mono.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Mono".into(),
                },
            )
            .unwrap()
            .project;
        let mono_track = mono.audio.voice_tracks[0].id;
        assert!(
            service
                .verified_master_voice(mono.id, &RevisionStamp::from(&mono), mono_track,)
                .is_err()
        );

        let stereo_path = temp.path().join("stereo.wav");
        write_pcm16_wav(&stereo_path, 48_000, 2, 1024, 19);
        let stereo = service
            .import_voice_file(
                mono.id,
                &RevisionStamp::from(&mono),
                "stereo-measure",
                &stereo_path,
                VoiceImportMetadata {
                    name: "stereo.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Stereo".into(),
                },
            )
            .unwrap()
            .project;
        let track = stereo.audio.voice_tracks.last().unwrap();
        let source = service
            .verified_master_voice(stereo.id, &RevisionStamp::from(&stereo), track.id)
            .unwrap();
        assert_eq!(source.sha256, track.source_sha256);
        assert_eq!(
            fs::read(&source.path).unwrap(),
            fs::read(&stereo_path).unwrap()
        );
        assert!(source.size_bytes > 44);
        assert!(
            service
                .verified_master_voice(stereo.id, &RevisionStamp::from(&mono), track.id,)
                .is_err()
        );
        assert!(
            service
                .verified_master_voice(stereo.id, &RevisionStamp::from(&stereo), Uuid::now_v7(),)
                .is_err()
        );

        fs::write(source.path, b"changed bytes").unwrap();
        assert!(
            service
                .verified_master_voice(stereo.id, &RevisionStamp::from(&stereo), track.id,)
                .is_err()
        );
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

    #[test]
    fn av_master_voice_source_requires_exact_measured_stereo_wav_and_revision() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("Mastering voice").unwrap();
        let mono_path = temp.path().join("mono.wav");
        write_pcm16_wav(&mono_path, 48_000, 1, 48_000, 0);
        let mono = service
            .import_voice_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "mono-mastering-input",
                &mono_path,
                VoiceImportMetadata {
                    name: "mono.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Mono take".into(),
                },
            )
            .unwrap()
            .project;
        assert!(
            service
                .verified_master_voice(
                    mono.id,
                    &RevisionStamp::from(&mono),
                    mono.audio.voice_tracks[0].id,
                )
                .is_err()
        );

        let stereo_path = temp.path().join("stereo.wav");
        write_pcm16_wav(&stereo_path, 48_000, 2, 48_000, 1);
        let stereo = service
            .import_voice_file(
                mono.id,
                &RevisionStamp::from(&mono),
                "stereo-mastering-input",
                &stereo_path,
                VoiceImportMetadata {
                    name: "stereo.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Stereo take".into(),
                },
            )
            .unwrap()
            .project;
        let measured = stereo.audio.voice_tracks.last().unwrap();
        let source = service
            .verified_master_voice(stereo.id, &RevisionStamp::from(&stereo), measured.id)
            .unwrap();
        assert_eq!(source.sha256, measured.source_sha256);
        assert!(source.size_bytes > 44);
        assert_eq!(
            fs::read(&source.path).unwrap(),
            fs::read(&stereo_path).unwrap()
        );
        assert!(
            service
                .verified_master_voice(stereo.id, &RevisionStamp::from(&mono), measured.id)
                .is_err()
        );
        assert!(
            service
                .verified_master_voice(
                    stereo.id,
                    &RevisionStamp::from(&stereo),
                    uuid::Uuid::new_v4()
                )
                .is_err()
        );
    }

    #[test]
    fn waveform_pages_are_generated_from_the_immutable_import_and_then_reused() {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let initial = service.create_project("Paged waveform").unwrap();
        let source = temp.path().join("long-enough.wav");
        write_pcm16_wav(&source, 8_000, 1, 6_000, i16::MAX / 2);

        let imported = service
            .import_voice_file(
                initial.id,
                &RevisionStamp::from(&initial),
                "waveform-import",
                &source,
                VoiceImportMetadata {
                    name: "long-enough.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Measured take".into(),
                },
            )
            .unwrap()
            .project;
        let track = imported.audio.voice_tracks.first().unwrap();

        let first = service.waveform_page(imported.id, track.id, 0, 2).unwrap();
        assert_eq!(first.algorithm, "sample-peak-max-abs-v1");
        assert_eq!(first.peaks.len(), 2);
        assert_eq!(first.peak_count, 3);
        assert!(first.has_next);
        assert!(first.peaks.iter().all(|peak| (0.49..0.51).contains(peak)));

        let second = service.waveform_page(imported.id, track.id, 1, 2).unwrap();
        assert_eq!(second.peaks.len(), 1);
        assert!(second.has_previous);
        assert!(!second.has_next);
        assert_eq!(second.source_sha256, track.source_sha256);
    }
}
