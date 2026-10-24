//! Controlled domain/SQLite tests. The silent WAV and locally constructed
//! receipts do not represent authenticated native alignment or speech quality.
use motionwright_domain::{
    AlignmentEvidence, Asset, AudioCue, Change, CueEvidence, Project, RationalTime, RevisionStamp,
    TranscriptSegment, VOICE_ALIGNMENT_PROFILE_V1, VoiceAlignmentApplication,
    VoiceAlignmentReceiptRef, VoiceTrack,
};
use motionwright_storage::{
    ProductionReceiptInput, StorageError, Store, voice_alignment_receipt_sha256_v1,
};
use rusqlite::{Connection, params};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Barrier},
    time::Duration,
};
use tempfile::TempDir;
use uuid::Uuid;

const COMMAND: &str = "controlled.alignment.fixture";

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn time(num: i64, den: i64) -> RationalTime {
    RationalTime { num, den }
}
fn controlled_wav() -> Vec<u8> {
    let rate = 16_000_u32;
    let size = rate * 2;
    let mut bytes = Vec::with_capacity((44 + size) as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + size).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&rate.to_le_bytes());
    bytes.extend_from_slice(&(rate * 2).to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&size.to_le_bytes());
    bytes.resize((44 + size) as usize, 0);
    bytes
}

#[derive(Debug, PartialEq)]
struct Snapshot {
    project: Project,
    events: i64,
    receipts: i64,
    production_receipts: i64,
}
struct Fixture {
    _directory: TempDir,
    database: PathBuf,
    store: Store,
    project: Project,
    application: VoiceAlignmentApplication,
}
impl Fixture {
    fn new(stage: &str) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("project.sqlite3");
        let mut store = Store::open(&database).unwrap();
        let source = directory.path().join("controlled.wav");
        fs::write(&source, controlled_wav()).unwrap();
        let blob = store.ingest_blob_file(&source).unwrap();
        let mut project = Project::new("Controlled alignment receipt fixture").unwrap();
        let asset_id = Uuid::new_v4();
        let voice_track_id = Uuid::new_v4();
        project.assets.push(Asset {
            id: asset_id,
            name: "controlled.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(blob.sha256.clone()),
            source_revision: None,
        });
        project.audio.voice_tracks.push(VoiceTrack {
            id: voice_track_id,
            asset_id,
            label: "Controlled silence; not native qualification".into(),
            sample_rate_hz: 16_000,
            channels: 1,
            measured_duration: time(1, 1),
            source_sha256: blob.sha256.clone(),
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
        project.audio.active_voice_track_id = Some(voice_track_id);
        store.create_project(&project).unwrap();
        let receipt = store
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: Uuid::new_v4().to_string(),
                request_sha256: sha(b"controlled alignment request"),
                command: COMMAND.into(),
                stage: stage.into(),
                payload: json!({"controlled_fixture": true, "native_execution": false}),
            })
            .unwrap();
        let segments: Vec<_> = [
            ("Hello", time(0, 1), time(2, 5)),
            ("world", time(1, 2), time(9, 10)),
        ]
        .into_iter()
        .map(|(text, start, end)| TranscriptSegment {
            id: Uuid::new_v4(),
            voice_track_id,
            start,
            end,
            text: text.into(),
            speaker: None,
            alignment: AlignmentEvidence::Measured {
                engine: "controlled-fixture-not-native".into(),
                source_sha256: blob.sha256.clone(),
                confidence_millis: None,
            },
        })
        .collect();
        let cues = segments
            .iter()
            .map(|segment| AudioCue {
                id: Uuid::new_v4(),
                label: segment.text.clone(),
                at: segment.start,
                source_segment_id: Some(segment.id),
                evidence: CueEvidence::TranscriptAligned,
            })
            .collect();
        let application = VoiceAlignmentApplication {
            profile: VOICE_ALIGNMENT_PROFILE_V1.into(),
            voice_track_id,
            asset_id,
            source_sha256: blob.sha256,
            transcript_version: 1,
            transcript_text: "Hello world".into(),
            transcript_sha256: sha(b"Hello world"),
            receipt: VoiceAlignmentReceiptRef {
                id: receipt.id,
                request_id: receipt.request_id.clone(),
                request_sha256: receipt.request_sha256.clone(),
                command: receipt.command.clone(),
                receipt_sha256: voice_alignment_receipt_sha256_v1(&receipt).unwrap(),
            },
            segments,
            cues,
        };
        Self {
            _directory: directory,
            database,
            store,
            project,
            application,
        }
    }
    fn connection(&self) -> Connection {
        let connection = Connection::open(&self.database).unwrap();
        connection.busy_timeout(Duration::from_secs(2)).unwrap();
        connection
    }
    fn snapshot(&self) -> Snapshot {
        let connection = self.connection();
        let count = |table: &str| {
            connection
                .query_row(
                    &format!("SELECT COUNT(*) FROM {table} WHERE project_id=?1"),
                    [self.project.id.to_string()],
                    |row| row.get(0),
                )
                .unwrap()
        };
        Snapshot {
            project: self.store.load_project(self.project.id).unwrap(),
            events: count("events"),
            receipts: count("receipts"),
            production_receipts: count("production_receipts"),
        }
    }
    fn stamp(&self) -> RevisionStamp {
        RevisionStamp::from(&self.store.load_project(self.project.id).unwrap())
    }
    fn reject(&mut self, stamp: &RevisionStamp, key: &str, application: VoiceAlignmentApplication) {
        let before = self.snapshot();
        assert!(
            self.store
                .apply(
                    self.project.id,
                    stamp,
                    key,
                    &Change::ApplyVoiceAlignment { application },
                )
                .is_err()
        );
        assert_eq!(self.snapshot(), before);
    }
    fn advance(&mut self) {
        let project = self.store.load_project(self.project.id).unwrap();
        let mut mix = project.audio.mix.clone();
        mix.voice_gain_db += 1.0;
        let result = self
            .store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "intervening-local-mix",
                &Change::SetMixIntent { mix },
            )
            .unwrap();
        assert_eq!(result.project.revision, project.revision + 1);
    }
}

#[test]
fn whole_alignment_commits_once_and_reopens_with_exact_words_cues_and_receipt() {
    let mut fixture = Fixture::new("completed");
    let before = fixture.snapshot();
    let stamp = fixture.stamp();
    let segments = fixture.application.segments.clone();
    let cues = fixture.application.cues.clone();
    let outcome = fixture
        .store
        .apply(
            fixture.project.id,
            &stamp,
            "alignment-commit",
            &Change::ApplyVoiceAlignment {
                application: fixture.application.clone(),
            },
        )
        .unwrap();
    assert!(!outcome.replayed);
    assert_eq!(outcome.previous_revision, stamp.revision);
    assert_eq!(outcome.project.revision, stamp.revision + 1);
    let after = fixture.snapshot();
    assert_eq!(after.events, before.events + 1);
    assert_eq!(after.receipts, before.receipts + 1);
    assert_eq!(after.production_receipts, before.production_receipts);
    let expected = outcome.project;
    let Fixture {
        _directory: directory,
        database,
        store,
        project,
        ..
    } = fixture;
    drop(store);
    let reopened = Store::open(&database).unwrap();
    let loaded = reopened.load_project(project.id).unwrap();
    assert_eq!(loaded, expected);
    assert_eq!(loaded.audio.transcript, segments);
    assert_eq!(loaded.audio.cues, cues);
    loaded.validate().unwrap();
    drop(reopened);
    drop(directory);
}

#[test]
fn invalid_last_cue_cannot_publish_earlier_words_or_cues() {
    let mut fixture = Fixture::new("completed");
    let mut application = fixture.application.clone();
    application.cues.last_mut().unwrap().source_segment_id = Some(Uuid::new_v4());
    let stamp = fixture.stamp();
    fixture.reject(&stamp, "invalid-last-cue", application);
}

#[test]
fn missing_cross_project_and_modified_original_receipts_do_not_authorize_apply() {
    let mut missing = Fixture::new("completed");
    let mut application = missing.application.clone();
    application.receipt.id = Uuid::new_v4();
    missing.reject(&missing.stamp(), "missing-receipt", application);

    let mut crossed = Fixture::new("completed");
    let foreign = Project::new("Foreign controlled fixture").unwrap();
    crossed.store.create_project(&foreign).unwrap();
    let receipt = crossed
        .store
        .append_production_receipt(ProductionReceiptInput {
            project_id: foreign.id,
            generation: foreign.generation,
            revision: foreign.revision,
            request_id: Uuid::new_v4().to_string(),
            request_sha256: sha(b"foreign controlled request"),
            command: COMMAND.into(),
            stage: "completed".into(),
            payload: json!({"controlled_fixture": true, "native_execution": false}),
        })
        .unwrap();
    let mut application = crossed.application.clone();
    application.receipt = VoiceAlignmentReceiptRef {
        id: receipt.id,
        request_id: receipt.request_id.clone(),
        request_sha256: receipt.request_sha256.clone(),
        command: receipt.command.clone(),
        receipt_sha256: voice_alignment_receipt_sha256_v1(&receipt).unwrap(),
    };
    crossed.reject(&crossed.stamp(), "cross-project", application);

    let mut modified = Fixture::new("completed");
    assert_eq!(
        modified
            .connection()
            .execute(
                "UPDATE production_receipts SET payload_json=?1 WHERE receipt_id=?2",
                params![
                    json!({"controlled_fixture": true, "modified_after_observation": true})
                        .to_string(),
                    modified.application.receipt.id.to_string(),
                ],
            )
            .unwrap(),
        1
    );
    let application = modified.application.clone();
    modified.reject(&modified.stamp(), "modified-receipt", application);
}

#[test]
fn incomplete_receipt_and_intervening_unknown_observation_preserve_the_project() {
    let mut failed = Fixture::new("failed_known");
    let application = failed.application.clone();
    failed.reject(&failed.stamp(), "failed-original", application);
    let mut uncertain = Fixture::new("completed");
    let reference = uncertain.application.receipt.clone();
    let later = uncertain
        .store
        .append_production_receipt(ProductionReceiptInput {
            project_id: uncertain.project.id,
            generation: uncertain.project.generation,
            revision: uncertain.project.revision,
            request_id: reference.request_id,
            request_sha256: reference.request_sha256,
            command: reference.command,
            stage: "outcome_unknown".into(),
            payload: json!({"controlled_fixture": true, "native_execution": false}),
        })
        .unwrap();
    assert_ne!(later.id, reference.id);
    let application = uncertain.application.clone();
    uncertain.reject(&uncertain.stamp(), "intervening-unknown", application);
}

#[test]
fn old_observation_is_rejected_even_when_apply_supplies_the_new_current_revision() {
    let mut fixture = Fixture::new("completed");
    fixture.advance();
    let stamp = fixture.stamp();
    assert_eq!(stamp.revision, fixture.project.revision + 1);
    let application = fixture.application.clone();
    fixture.reject(&stamp, "stale-source-current-cas", application);
}

#[test]
fn exact_replay_precedes_add_only_source_checks_but_changed_payload_fails() {
    let mut fixture = Fixture::new("completed");
    let stamp = fixture.stamp();
    let change = Change::ApplyVoiceAlignment {
        application: fixture.application.clone(),
    };
    let original = fixture
        .store
        .apply(fixture.project.id, &stamp, "stable-alignment-key", &change)
        .unwrap();
    fixture.advance();
    let before = fixture.snapshot();
    let replay = fixture
        .store
        .apply(fixture.project.id, &stamp, "stable-alignment-key", &change)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, original.project);
    assert_eq!(replay.previous_revision, original.previous_revision);
    assert_eq!(replay.request_id, original.request_id);
    assert_eq!(fixture.snapshot(), before);
    let mut different = fixture.application.clone();
    different.cues[0].label = "Different retained payload".into();
    let before_mismatch = fixture.snapshot();
    assert!(matches!(
        fixture.store.apply(
            fixture.project.id,
            &stamp,
            "stable-alignment-key",
            &Change::ApplyVoiceAlignment {
                application: different
            },
        ),
        Err(StorageError::RequestReuse)
    ));
    assert_eq!(fixture.snapshot(), before_mismatch);
}

#[test]
fn missing_or_corrupt_cas_bytes_cannot_supply_the_qualified_source() {
    for corrupt in [false, true] {
        let mut fixture = Fixture::new("completed");
        let stamp = fixture.stamp();
        let source = fixture
            .store
            .verified_blob_path(&fixture.application.source_sha256)
            .unwrap();
        if corrupt {
            fs::write(&source, b"controlled corruption").unwrap();
        } else {
            fs::remove_file(&source).unwrap();
        }
        let application = fixture.application.clone();
        fixture.reject(&stamp, "source-integrity", application);
    }
}

#[test]
fn known_sql_receipt_abort_rolls_back_project_and_journal_before_a_known_failed_retry() {
    let mut fixture = Fixture::new("completed");
    fixture
        .connection()
        .execute_batch(
            "CREATE TRIGGER fixture_fail_alignment_receipt BEFORE INSERT ON receipts
         BEGIN SELECT RAISE(ABORT, 'controlled receipt rollback'); END;",
        )
        .unwrap();
    let stamp = fixture.stamp();
    let application = fixture.application.clone();
    fixture.reject(&stamp, "known-sql-abort", application);
    fixture
        .connection()
        .execute_batch("DROP TRIGGER fixture_fail_alignment_receipt;")
        .unwrap();
    let before = fixture.snapshot();
    let outcome = fixture
        .store
        .apply(
            fixture.project.id,
            &stamp,
            "known-sql-abort",
            &Change::ApplyVoiceAlignment {
                application: fixture.application.clone(),
            },
        )
        .unwrap();
    assert!(!outcome.replayed);
    assert_eq!(outcome.project.revision, stamp.revision + 1);
    let after = fixture.snapshot();
    assert_eq!(after.events, before.events + 1);
    assert_eq!(after.receipts, before.receipts + 1);
    assert_eq!(after.production_receipts, before.production_receipts);
}

#[test]
fn two_independent_sqlite_writers_have_one_whole_alignment_and_one_cas_conflict() {
    let mut fixture = Fixture::new("completed");
    let mut second = fixture.application.clone();
    for segment in &mut second.segments {
        segment.id = Uuid::new_v4();
    }
    for (cue, segment) in second.cues.iter_mut().zip(&second.segments) {
        cue.id = Uuid::new_v4();
        cue.source_segment_id = Some(segment.id);
    }
    let observation = fixture
        .store
        .append_production_receipt(ProductionReceiptInput {
            project_id: fixture.project.id,
            generation: fixture.project.generation,
            revision: fixture.project.revision,
            request_id: Uuid::new_v4().to_string(),
            request_sha256: sha(b"second independent controlled request"),
            command: COMMAND.into(),
            stage: "completed".into(),
            payload: json!({"controlled_fixture": true, "native_execution": false}),
        })
        .unwrap();
    second.receipt = VoiceAlignmentReceiptRef {
        id: observation.id,
        request_id: observation.request_id.clone(),
        request_sha256: observation.request_sha256.clone(),
        command: observation.command.clone(),
        receipt_sha256: voice_alignment_receipt_sha256_v1(&observation).unwrap(),
    };
    let before = fixture.snapshot();
    let stamp = fixture.stamp();
    let barrier = Arc::new(Barrier::new(2));
    let mut workers = Vec::new();
    let stores = [
        Store::open(&fixture.database).unwrap(),
        Store::open(&fixture.database).unwrap(),
    ];
    for ((key, application), mut store) in [
        ("writer-one", fixture.application.clone()),
        ("writer-two", second),
    ]
    .into_iter()
    .zip(stores)
    {
        let project_id = fixture.project.id;
        let expected = stamp.clone();
        let ready = Arc::clone(&barrier);
        workers.push(std::thread::spawn(move || {
            ready.wait();
            store.apply(
                project_id,
                &expected,
                key,
                &Change::ApplyVoiceAlignment { application },
            )
        }));
    }
    let results: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        results
            .iter()
            .filter(|result| matches!(result, Err(StorageError::Conflict { .. })))
            .count(),
        1
    );
    let winner = results.into_iter().find_map(Result::ok).unwrap();
    let after = fixture.snapshot();
    assert_eq!(after.project, winner.project);
    assert_eq!(after.events, before.events + 1);
    assert_eq!(after.receipts, before.receipts + 1);
    assert_eq!(after.production_receipts, before.production_receipts);
    assert_eq!(after.project.audio.transcript.len(), 2);
    assert_eq!(after.project.audio.cues.len(), 2);
}
