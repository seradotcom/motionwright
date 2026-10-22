//! Real imports under immutable host resource budgets.
//! The causal baseline has the same policy API but no enforcement.
use motionwright_domain::{Change, DomainError, Project, RationalTime};
use motionwright_service::{FileImportIntent, ImportLimits, StudioService, VoiceImportMetadata};
use motionwright_storage::{ApplyOutcome, StorageError, Store};
use rusqlite::{
    Connection, OpenFlags,
    types::{Type, ValueRef},
};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

const NAME: &str = "Import budget fixture";
const LABEL: &str = "Measured budget fixture";
const WAV_RATE: u32 = 8_000;

fn pcm16_wav(frames: u32, channels: u16) -> Vec<u8> {
    assert!(channels > 0);
    let samples = frames.checked_mul(u32::from(channels)).unwrap();
    let data_bytes = samples.checked_mul(2).unwrap();
    let block_align = channels.checked_mul(2).unwrap();
    let mut bytes = Vec::with_capacity((44 + data_bytes) as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&channels.to_le_bytes());
    bytes.extend_from_slice(&WAV_RATE.to_le_bytes());
    bytes.extend_from_slice(&(WAV_RATE * u32::from(block_align)).to_le_bytes());
    bytes.extend_from_slice(&block_align.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for _ in 0..samples {
        bytes.extend_from_slice(&19_i16.to_le_bytes());
    }
    bytes
}

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

type RawRows = Vec<Vec<Vec<(Type, Vec<u8>)>>>;
type RetainedFiles = BTreeMap<PathBuf, (u64, String)>;

fn raw_rows(database: &Path) -> RawRows {
    let connection =
        Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    connection.pragma_update(None, "query_only", true).unwrap();
    [
        "SELECT * FROM projects ORDER BY id",
        "SELECT * FROM events ORDER BY project_id,revision",
        "SELECT * FROM receipts ORDER BY project_id,request_id",
    ]
    .into_iter()
    .map(|sql| {
        let mut statement = connection.prepare(sql).unwrap();
        let columns = statement.column_count();
        let rows = statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| {
                        let value = row.get_ref(column)?;
                        let kind = value.data_type();
                        let bytes = match value {
                            ValueRef::Null => Vec::new(),
                            ValueRef::Integer(value) => value.to_be_bytes().to_vec(),
                            ValueRef::Real(value) => value.to_bits().to_be_bytes().to_vec(),
                            ValueRef::Text(value) | ValueRef::Blob(value) => value.to_vec(),
                        };
                        Ok((kind, bytes))
                    })
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap();
        rows.map(|row| row.unwrap()).collect()
    })
    .collect()
}

fn retained_files(root: &Path) -> RetainedFiles {
    fn walk(root: &Path, directory: &Path, files: &mut RetainedFiles) {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            if metadata.is_dir() {
                walk(root, &path, files);
            } else {
                assert!(metadata.is_file());
                let bytes = fs::read(&path).unwrap();
                files.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    (bytes.len() as u64, digest(&bytes)),
                );
            }
        }
    }
    let mut files = BTreeMap::new();
    if root.try_exists().unwrap() {
        walk(root, root, &mut files);
    }
    files
}

fn assert_staging_empty(root: &Path) {
    match fs::read_dir(root.join(".staging")) {
        Ok(entries) => {
            let remaining: Vec<_> = entries.map(|entry| entry.unwrap().file_name()).collect();
            assert!(remaining.is_empty(), "staging leftovers: {remaining:?}");
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot inspect staging: {error}"),
    }
}

#[derive(Debug, PartialEq)]
struct Effects {
    rows: RawRows,
    files: RetainedFiles,
}

struct Fixture {
    // Drop the service before removing its directory.
    service: StudioService,
    project: Project,
    database: PathBuf,
    source: PathBuf,
    media_type: String,
    bytes: Vec<u8>,
    _directory: tempfile::TempDir,
}

impl Fixture {
    fn new(bytes: &[u8], limits: ImportLimits, via_store: bool) -> Self {
        Self::for_media(bytes, "source.wav", "audio/wav", limits, via_store)
    }

    fn for_media(
        bytes: &[u8],
        file_name: &str,
        media_type: &str,
        limits: ImportLimits,
        via_store: bool,
    ) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("imports.sqlite3");
        let source = directory.path().join(file_name);
        fs::write(&source, bytes).unwrap();
        let service = if via_store {
            StudioService::from_store_with_import_limits(Store::open(&database).unwrap(), limits)
        } else {
            StudioService::open_with_import_limits(&database, limits).unwrap()
        };
        let project = service.create_project("Import budget regression").unwrap();
        Self {
            service,
            project,
            database,
            source,
            media_type: media_type.into(),
            bytes: bytes.to_vec(),
            _directory: directory,
        }
    }

    fn reopen(self, limits: ImportLimits) -> Self {
        let Self {
            service,
            project,
            database,
            source,
            media_type,
            bytes,
            _directory,
        } = self;
        drop(service);
        let service = StudioService::open_with_import_limits(&database, limits).unwrap();
        Self {
            service,
            project,
            database,
            source,
            media_type,
            bytes,
            _directory,
        }
    }

    fn intent(&self, voice: bool) -> FileImportIntent {
        if voice {
            FileImportIntent::Voice {
                name: NAME.into(),
                media_type: self.media_type.clone(),
                label: LABEL.into(),
            }
        } else {
            FileImportIntent::Asset {
                name: NAME.into(),
                media_type: self.media_type.clone(),
            }
        }
    }

    fn import(&self, voice: bool, request_id: &str) -> Result<ApplyOutcome, StorageError> {
        if voice {
            self.service.import_voice_file(
                self.project.id,
                &self.project.stamp(),
                request_id,
                &self.source,
                VoiceImportMetadata {
                    name: NAME.into(),
                    media_type: self.media_type.clone(),
                    label: LABEL.into(),
                },
            )
        } else {
            self.service.import_asset_file(
                self.project.id,
                &self.project.stamp(),
                request_id,
                &self.source,
                NAME.into(),
                self.media_type.clone(),
            )
        }
    }

    fn blob_root(&self) -> PathBuf {
        let shared = self.service.store();
        shared.lock().blob_root().to_path_buf()
    }

    fn cas_path(&self) -> PathBuf {
        let shared = self.service.store();
        shared
            .lock()
            .verified_blob_path(&digest(&self.bytes))
            .unwrap()
    }

    fn effects(&self) -> Effects {
        Effects {
            rows: raw_rows(&self.database),
            files: retained_files(&self.blob_root()),
        }
    }
}

#[track_caller]
fn assert_new_accepted(
    fixture: &Fixture,
    result: Result<ApplyOutcome, StorageError>,
) -> ApplyOutcome {
    let outcome = result.expect("valid input within its budgets must commit");
    assert!(!outcome.replayed);
    assert_eq!(outcome.previous_revision, fixture.project.revision);
    assert_eq!(outcome.project.revision, fixture.project.revision + 1);
    let shared = fixture.service.store();
    let store = shared.lock();
    assert_eq!(
        store
            .read_blob(&digest(&fixture.bytes), fixture.bytes.len() as u64)
            .unwrap(),
        fixture.bytes
    );
    drop(store);
    assert_staging_empty(&fixture.blob_root());
    outcome
}

#[track_caller]
fn assert_source_limit(result: &Result<ApplyOutcome, StorageError>, size: u64, limit: u64) {
    assert!(
        matches!(result, Err(StorageError::BlobTooLarge {size: observed, limit: actual})
        if *observed == size && *actual == limit),
        "expected source byte budget ({size}/{limit}), got {result:?}"
    );
}

#[track_caller]
fn assert_audio_limit(result: &Result<ApplyOutcome, StorageError>, dimension: &str) {
    let expected = format!("audio measurement limit exceeded: {dimension}");
    assert!(
        matches!(result, Err(StorageError::Domain(DomainError::Invalid(message)))
        if message == &expected),
        "expected {expected}, got {result:?}"
    );
}

#[track_caller]
fn assert_rejected_unchanged(
    fixture: &Fixture,
    before: &Effects,
    result: Result<ApplyOutcome, StorageError>,
) {
    assert!(
        result.is_err(),
        "expected budget rejection, received {result:?}"
    );
    assert_eq!(
        &fixture.effects(),
        before,
        "rejection changed SQL rows or retained files"
    );
    assert_eq!(fs::read(&fixture.source).unwrap(), fixture.bytes);
    assert_staging_empty(&fixture.blob_root());
}

#[track_caller]
fn assert_new_rejected(
    fixture: &Fixture,
    before: &Effects,
    request_id: &str,
    voice: bool,
    result: Result<ApplyOutcome, StorageError>,
) {
    assert_rejected_unchanged(fixture, before, result);
    assert!(
        fixture
            .service
            .recover_file_import(
                fixture.project.id,
                &fixture.project.stamp(),
                request_id,
                &fixture.intent(voice)
            )
            .unwrap()
            .is_none(),
        "a rejected new import must not have a receipt"
    );
    assert_eq!(&fixture.effects(), before);
}

fn assert_replayed(original: &ApplyOutcome, replay: &ApplyOutcome) {
    assert!(replay.replayed);
    assert_eq!(replay.request_id, original.request_id);
    assert_eq!(replay.previous_revision, original.previous_revision);
    assert_eq!(replay.project, original.project);
}

fn assert_voice_limit_rejects(bytes: &[u8], limits: ImportLimits, dimension: &str, key: &str) {
    let control = Fixture::new(bytes, ImportLimits::default(), false);
    assert_new_accepted(&control, control.import(true, "valid-media-control"));
    let fixture = Fixture::new(bytes, limits, false);
    let before = fixture.effects();
    let result = fixture.import(true, key);
    assert_audio_limit(&result, dimension);
    assert_new_rejected(&fixture, &before, key, true, result);
}

#[test]
fn source_byte_boundary_applies_to_asset_voice_and_both_host_constructors() {
    let bytes = pcm16_wav(64, 1);
    let length = bytes.len() as u64;
    for via_store in [false, true] {
        for voice in [false, true] {
            for limit in [length, length - 1] {
                let limits = ImportLimits::default().with_source_bytes(limit).unwrap();
                let fixture = Fixture::new(&bytes, limits, via_store);
                let before = fixture.effects();
                let result = fixture.import(voice, "source-byte-boundary");
                if limit == length {
                    assert_new_accepted(&fixture, result);
                } else {
                    assert_source_limit(&result, length, limit);
                    assert_new_rejected(&fixture, &before, "source-byte-boundary", voice, result);
                }
            }
        }
    }
}

#[test]
fn known_source_hash_respects_reduced_bytes_but_source_free_recovery_survives() {
    for voice in [false, true] {
        let bytes = pcm16_wav(256, 1);
        let mut fixture = Fixture::new(&bytes, ImportLimits::default(), false);
        let key = "known-source-budget";
        let committed = assert_new_accepted(&fixture, fixture.import(voice, key));
        let later = fixture
            .service
            .apply(
                fixture.project.id,
                &committed.project.stamp(),
                "later-title",
                &Change::RenameProject {
                    title: "Current head after the import".into(),
                },
            )
            .unwrap();
        let cas_path = fixture.cas_path();
        fs::remove_file(&cas_path).unwrap();
        let lower = fixture
            .service
            .import_limits()
            .with_source_bytes(bytes.len() as u64 - 1)
            .unwrap();
        fixture = fixture.reopen(lower);
        let before = fixture.effects();
        let result = fixture.import(voice, key);
        assert_source_limit(&result, bytes.len() as u64, bytes.len() as u64 - 1);
        assert_rejected_unchanged(&fixture, &before, result);
        assert!(!cas_path.try_exists().unwrap());
        fs::remove_file(&fixture.source).unwrap();
        let limits = fixture.service.import_limits();
        fixture = fixture.reopen(limits);
        let before_recovery = fixture.effects();
        let recovered = fixture
            .service
            .recover_file_import(
                fixture.project.id,
                &fixture.project.stamp(),
                key,
                &fixture.intent(voice),
            )
            .unwrap()
            .unwrap();
        assert_replayed(&committed, &recovered);
        assert_eq!(fixture.effects(), before_recovery);
        assert_eq!(
            fixture.service.project(fixture.project.id).unwrap(),
            later.project
        );
        assert!(!fixture.source.try_exists().unwrap());
        assert!(!cas_path.try_exists().unwrap());
        assert_staging_empty(&fixture.blob_root());
    }
}

#[test]
fn known_voice_replay_does_not_remeasure_or_rehydrate_under_tiny_audio_budgets() {
    let bytes = pcm16_wav(8_001, 1);
    let mut fixture = Fixture::new(&bytes, ImportLimits::default(), false);
    let key = "known-voice-audio-budgets";
    let committed = assert_new_accepted(&fixture, fixture.import(true, key));
    let cas_path = fixture.cas_path();
    fs::remove_file(&cas_path).unwrap();
    let lower = fixture
        .service
        .import_limits()
        .with_source_bytes(bytes.len() as u64)
        .unwrap()
        .with_audio_seconds(1)
        .unwrap()
        .with_decoded_samples(1)
        .unwrap()
        .with_demux_packets(1)
        .unwrap()
        .with_packet_bytes(1)
        .unwrap()
        .with_packet_samples(1)
        .unwrap()
        .with_audio_read_bytes(8)
        .unwrap();
    fixture = fixture.reopen(lower);
    let before = fixture.effects();
    let replay = fixture.import(true, key).unwrap();
    assert_replayed(&committed, &replay);
    assert_eq!(fixture.effects(), before);
    assert_eq!(fs::read(&fixture.source).unwrap(), bytes);
    assert!(!cas_path.try_exists().unwrap());
    assert_staging_empty(&fixture.blob_root());
}

#[test]
fn source_free_recovery_ignores_all_import_limits_after_reopen() {
    let bytes = pcm16_wav(8_001, 1);
    let mut fixture = Fixture::new(&bytes, ImportLimits::default(), false);
    let key = "source-free-budget-control";
    let committed = assert_new_accepted(&fixture, fixture.import(true, key));
    let later = fixture
        .service
        .apply(
            fixture.project.id,
            &committed.project.stamp(),
            "later-current-title",
            &Change::RenameProject {
                title: "Later current head".into(),
            },
        )
        .unwrap();
    let cas = fixture.cas_path();
    fs::remove_file(&cas).unwrap();
    fs::remove_file(&fixture.source).unwrap();
    let lower = ImportLimits::default()
        .with_source_bytes(1)
        .unwrap()
        .with_audio_seconds(1)
        .unwrap()
        .with_decoded_samples(1)
        .unwrap()
        .with_demux_packets(1)
        .unwrap()
        .with_packet_bytes(1)
        .unwrap()
        .with_packet_samples(1)
        .unwrap()
        .with_audio_read_bytes(1)
        .unwrap();
    fixture = fixture.reopen(lower);
    let before = fixture.effects();
    let recovered = fixture
        .service
        .recover_file_import(
            fixture.project.id,
            &fixture.project.stamp(),
            key,
            &fixture.intent(true),
        )
        .unwrap()
        .unwrap();
    assert_replayed(&committed, &recovered);
    assert_eq!(fixture.effects(), before);
    assert_eq!(
        fixture.service.project(fixture.project.id).unwrap(),
        later.project
    );
    assert!(!fixture.source.try_exists().unwrap());
    assert!(!cas.try_exists().unwrap());
}

#[test]
fn audio_duration_accepts_one_second_and_rejects_one_additional_frame() {
    for frames in [8_000, 8_001] {
        let bytes = pcm16_wav(frames, 1);
        let limits = ImportLimits::default().with_audio_seconds(1).unwrap();
        let fixture = Fixture::new(&bytes, limits, false);
        let before = fixture.effects();
        let result = fixture.import(true, "duration-boundary");
        if frames == 8_000 {
            let outcome = assert_new_accepted(&fixture, result);
            let track = &outcome.project.audio.voice_tracks[0];
            assert_eq!(track.sample_rate_hz, WAV_RATE);
            assert_eq!(
                track.measured_duration,
                RationalTime::new(8_000, 8_000).unwrap()
            );
        } else {
            assert_audio_limit(&result, "audio_seconds");
            assert_new_rejected(&fixture, &before, "duration-boundary", true, result);
        }
    }
}

#[test]
fn stereo_sample_budget_counts_both_channels_without_duration_preemption() {
    let bytes = pcm16_wav(800, 2);
    for allowed_samples in [1_600, 1_599] {
        let limits = ImportLimits::default()
            .with_audio_seconds(1)
            .unwrap()
            .with_decoded_samples(allowed_samples)
            .unwrap();
        let fixture = Fixture::new(&bytes, limits, false);
        let before = fixture.effects();
        let result = fixture.import(true, "stereo-sample-boundary");
        if allowed_samples == 1_600 {
            let outcome = assert_new_accepted(&fixture, result);
            let track = &outcome.project.audio.voice_tracks[0];
            assert_eq!(track.channels, 2);
            assert_eq!(
                track.measured_duration,
                RationalTime::new(800, 8_000).unwrap()
            );
        } else {
            assert_audio_limit(&result, "decoded_samples");
            assert_new_rejected(&fixture, &before, "stereo-sample-boundary", true, result);
        }
    }
}

#[test]
fn packet_byte_budget_rejects_valid_pcm_before_publication() {
    let limits = ImportLimits::default().with_packet_bytes(1).unwrap();
    assert_voice_limit_rejects(
        &pcm16_wav(3_456, 1),
        limits,
        "packet_bytes",
        "packet-byte-budget",
    );
}

#[test]
fn declared_packet_sample_budget_rejects_valid_pcm_before_publication() {
    let limits = ImportLimits::default().with_packet_samples(1).unwrap();
    assert_voice_limit_rejects(
        &pcm16_wav(3_456, 1),
        limits,
        "packet_samples",
        "packet-sample-budget",
    );
}

#[test]
fn demux_packet_budget_accepts_one_packet_and_rejects_a_three_packet_wav() {
    // The pinned PCM WAV reader uses packets of up to 1,152 frames.
    let limits = ImportLimits::default().with_demux_packets(1).unwrap();
    let control = Fixture::new(&pcm16_wav(1_152, 1), limits, false);
    assert_new_accepted(&control, control.import(true, "one-packet-control"));
    assert_voice_limit_rejects(
        &pcm16_wav(3_456, 1),
        limits,
        "demux_packets",
        "demux-packet-budget",
    );
}

#[test]
fn audio_read_budget_rejects_valid_wav_during_probe_without_publication() {
    let limits = ImportLimits::default().with_audio_read_bytes(8).unwrap();
    assert_voice_limit_rejects(
        &pcm16_wav(3_456, 1),
        limits,
        "audio_read_bytes",
        "audio-probe-read-budget",
    );
}

// Minimal finite Matroska PCM fixtures, decoded by the pinned real media stack.
// Duration and frame-count declarations are deliberately absent.
fn ebml(id: u32, payload: &[u8]) -> Vec<u8> {
    assert_ne!(id, 0);
    let id_bytes = id.to_be_bytes();
    let mut output = id_bytes[(id.leading_zeros() / 8) as usize..].to_vec();
    let length = u64::try_from(payload.len()).unwrap();
    for width in 1..=8 {
        let reserved_unknown = (1_u64 << (7 * width)) - 1;
        if length < reserved_unknown {
            let encoded = (length | (1_u64 << (7 * width))).to_be_bytes();
            output.extend_from_slice(&encoded[8 - width..]);
            output.extend_from_slice(payload);
            return output;
        }
    }
    panic!("fixture element exceeds finite EBML lengths")
}

fn ebml_uint(id: u32, value: u64) -> Vec<u8> {
    let bytes = value.to_be_bytes();
    let first = bytes.iter().position(|byte| *byte != 0).unwrap_or(7);
    ebml(id, &bytes[first..])
}

fn ebml_master(id: u32, elements: &[Vec<u8>]) -> Vec<u8> {
    ebml(id, &elements.concat())
}

fn mkv_pcm(blocks: &[(u8, u32)]) -> Vec<u8> {
    let header = ebml_master(
        0x1a45dfa3,
        &[
            ebml_uint(0x4286, 1),
            ebml_uint(0x42f7, 1),
            ebml_uint(0x42f2, 4),
            ebml_uint(0x42f3, 8),
            ebml(0x4282, b"matroska"),
            ebml_uint(0x4287, 4),
            ebml_uint(0x4285, 2),
        ],
    );
    let info = ebml_master(
        0x1549a966,
        &[
            ebml_uint(0x2ad7b1, 1_000_000),
            ebml(0x4d80, b"Motionwright fixture"),
            ebml(0x5741, b"Motionwright fixture"),
        ],
    );
    let track = |number: u64| {
        ebml_master(
            0xae,
            &[
                ebml_uint(0xd7, number),
                ebml_uint(0x73c5, number),
                ebml_uint(0x83, 2),
                ebml_uint(0x88, if number == 1 { 1 } else { 0 }),
                ebml(0x86, b"A_PCM/INT/LIT"),
                ebml_master(
                    0xe1,
                    &[
                        ebml(0xb5, &8_000_f64.to_be_bytes()),
                        ebml_uint(0x9f, 1),
                        ebml_uint(0x6264, 16),
                    ],
                ),
            ],
        )
    };
    let tracks = ebml_master(0x1654ae6b, &[track(1), track(2)]);
    let mut cluster = vec![ebml_uint(0xe7, 0)];
    let mut frames_before = [0_u32; 2];
    for &(number, frames) in blocks {
        assert!((1..=2).contains(&number));
        let index = usize::from(number - 1);
        let timestamp = i16::try_from(frames_before[index] / 8).unwrap();
        frames_before[index] = frames_before[index].checked_add(frames).unwrap();
        let mut block = vec![0x80 | number];
        block.extend_from_slice(&timestamp.to_be_bytes());
        block.push(0x80); // Keyframe, no lacing.
        for _ in 0..frames {
            block.extend_from_slice(&19_i16.to_le_bytes());
        }
        cluster.push(ebml(0xa3, &block));
    }
    let segment = ebml_master(
        0x18538067,
        &[info, tracks, ebml_master(0x1f43b675, &cluster)],
    );
    [header, segment].concat()
}

fn mkv_fixture(blocks: &[(u8, u32)], limits: ImportLimits) -> Fixture {
    Fixture::for_media(
        &mkv_pcm(blocks),
        "measured.mka",
        "audio/x-matroska",
        limits,
        false,
    )
}

fn inspect_mkv_packets(fixture: &Fixture) -> Vec<u32> {
    use symphonia::core::{
        formats::{FormatOptions, TrackType, probe::Hint},
        io::MediaSourceStream,
        meta::MetadataOptions,
    };
    let input = fs::File::open(&fixture.source).unwrap();
    let stream = MediaSourceStream::new(Box::new(input), Default::default());
    let mut format = symphonia::default::get_probe()
        .probe(
            &Hint::new(),
            stream,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .expect("the real demuxer must accept this finite Matroska fixture");
    let selected = format.default_track(TrackType::Audio).unwrap();
    assert_eq!(selected.id, 1);
    assert!(
        selected.num_frames.is_none(),
        "fixture must not predeclare frame count"
    );
    assert!(
        selected.duration.is_none(),
        "fixture must not predeclare duration"
    );
    let codec = selected.codec_params.as_ref().unwrap().audio().unwrap();
    assert!(
        codec.max_frames_per_packet.is_none(),
        "fixture must not predeclare packet frames"
    );
    let mut roster = Vec::new();
    while let Some(packet) = format
        .next_packet()
        .expect("fixture must have ordinary finite EOF")
    {
        roster.push(packet.track_id);
    }
    roster
}

#[test]
fn real_multitrack_control_has_unknown_duration_and_exact_selected_pcm() {
    let fixture = mkv_fixture(&[(2, 8), (2, 8), (1, 8)], ImportLimits::default());
    assert_eq!(inspect_mkv_packets(&fixture), [2, 2, 1]);
    let outcome = assert_new_accepted(&fixture, fixture.import(true, "real-multitrack-control"));
    let track = &outcome.project.audio.voice_tracks[0];
    assert_eq!(track.sample_rate_hz, 8_000);
    assert_eq!(track.channels, 1);
    assert_eq!(
        track.measured_duration,
        RationalTime::new(8, 8_000).unwrap()
    );
}

#[test]
fn demux_budget_counts_real_packets_from_unselected_tracks() {
    let blocks = [(2, 8), (2, 8), (1, 8)];
    let control = mkv_fixture(&blocks, ImportLimits::default());
    assert_eq!(inspect_mkv_packets(&control), [2, 2, 1]);
    assert_new_accepted(&control, control.import(true, "all-tracks-control"));
    let limits = ImportLimits::default().with_demux_packets(1).unwrap();
    let fixture = mkv_fixture(&blocks, limits);
    let before = fixture.effects();
    let result = fixture.import(true, "all-tracks-budget");
    assert_audio_limit(&result, "demux_packets");
    assert_new_rejected(&fixture, &before, "all-tracks-budget", true, result);
}

#[test]
fn actual_decode_sample_budget_applies_without_declared_frame_count() {
    for samples in [8, 7] {
        let limits = ImportLimits::default()
            .with_decoded_samples(samples)
            .unwrap();
        let fixture = mkv_fixture(&[(1, 8)], limits);
        assert_eq!(inspect_mkv_packets(&fixture), [1]);
        let before = fixture.effects();
        let result = fixture.import(true, "actual-decode-samples");
        if samples == 8 {
            assert_new_accepted(&fixture, result);
        } else {
            assert_audio_limit(&result, "decoded_samples");
            assert_new_rejected(&fixture, &before, "actual-decode-samples", true, result);
        }
    }
}

#[test]
fn actual_decode_packet_budget_applies_without_declared_packet_frames() {
    for samples in [8, 7] {
        let limits = ImportLimits::default()
            .with_packet_samples(samples)
            .unwrap();
        let fixture = mkv_fixture(&[(1, 8)], limits);
        assert_eq!(inspect_mkv_packets(&fixture), [1]);
        let before = fixture.effects();
        let result = fixture.import(true, "actual-decode-block");
        if samples == 8 {
            assert_new_accepted(&fixture, result);
        } else {
            assert_audio_limit(&result, "packet_samples");
            assert_new_rejected(&fixture, &before, "actual-decode-block", true, result);
        }
    }
}

#[test]
fn actual_duration_budget_rejects_one_extra_frame_without_declared_duration() {
    for extra in [false, true] {
        let mut blocks = vec![(1, 1_000); 8];
        if extra {
            blocks.push((1, 1));
        }
        let limits = ImportLimits::default().with_audio_seconds(1).unwrap();
        let fixture = mkv_fixture(&blocks, limits);
        assert_eq!(inspect_mkv_packets(&fixture), vec![1; blocks.len()]);
        let before = fixture.effects();
        let result = fixture.import(true, "actual-duration-boundary");
        if extra {
            assert_audio_limit(&result, "audio_seconds");
            assert_new_rejected(&fixture, &before, "actual-duration-boundary", true, result);
        } else {
            let accepted = assert_new_accepted(&fixture, result);
            assert_eq!(
                accepted.project.audio.voice_tracks[0].measured_duration,
                RationalTime::new(1, 1).unwrap()
            );
        }
    }
}

#[test]
fn declared_svg_budget_also_respects_a_lower_host_byte_limit() {
    let bytes = br#"<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>"#;
    let length = bytes.len() as u64;
    for (name, media_type) in [
        ("vector.svg", "application/octet-stream"),
        ("vector.bin", "image/svg+xml"),
    ] {
        for limit in [length, length - 1] {
            let fixture = Fixture::for_media(
                bytes,
                name,
                media_type,
                ImportLimits::default().with_source_bytes(limit).unwrap(),
                false,
            );
            let before = fixture.effects();
            let result = fixture.import(false, "joint-svg-source-budget");
            if limit == length {
                assert_new_accepted(&fixture, result);
            } else {
                assert_source_limit(&result, length, limit);
                assert_new_rejected(&fixture, &before, "joint-svg-source-budget", false, result);
            }
        }
    }
}
