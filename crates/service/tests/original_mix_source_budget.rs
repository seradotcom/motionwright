//! Closed OriginalMix policies through real import, SQLite, CAS and Service.
//! Large 180-second / 64-MiB fixtures belong in the selected CI cohort.
//! Ordinary WAVs do not exhaust the packet-count or cumulative-read ceilings.
use motionwright_domain::{DomainError, Project, RevisionStamp};
use motionwright_service::{OriginalMixDecodeBudget, OriginalMixDecodedPcm, StudioService};
use motionwright_storage::StorageError;
use rusqlite::{Connection, OpenFlags, types::ValueRef};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{BufWriter, Read, Write},
    path::{Path, PathBuf},
};
use tempfile::TempDir;
use uuid::Uuid;

#[derive(Debug)]
struct ExpectedAudio {
    frames: u32,
    container_sha256: String,
    pcm_sha256: String,
    first: [u8; 4],
    last: [u8; 4],
}

fn pcm_frame(index: u32, frames: u32) -> [u8; 4] {
    let (left, right): (i16, i16) = if index + 1 == frames {
        (22_222, -12_345)
    } else if index.is_multiple_of(2) {
        (8_192, -16_384)
    } else {
        (-8_192, 16_384)
    };
    let left = left.to_le_bytes();
    let right = right.to_le_bytes();
    [left[0], left[1], right[0], right[1]]
}

fn file_identity(path: &Path) -> (u64, String) {
    let mut file = File::open(path).unwrap();
    let mut hash = Sha256::new();
    let mut size = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).unwrap();
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
        size += count as u64;
    }
    (size, hex::encode(hash.finalize()))
}

fn write_wav(path: &Path, frames: u32, total_bytes: Option<u64>) -> ExpectedAudio {
    assert!(frames > 0);
    let data_bytes = frames.checked_mul(4).unwrap();
    let natural = 44_u64 + u64::from(data_bytes);
    let total = total_bytes.unwrap_or(natural);
    let junk_bytes = if total_bytes.is_some() {
        assert!(total >= natural + 8 && total.is_multiple_of(2));
        Some(u32::try_from(total - natural - 8).unwrap())
    } else {
        None
    };
    let mut file = BufWriter::new(File::create(path).unwrap());
    file.write_all(b"RIFF").unwrap();
    file.write_all(&u32::try_from(total - 8).unwrap().to_le_bytes())
        .unwrap();
    file.write_all(b"WAVEfmt ").unwrap();
    file.write_all(&16_u32.to_le_bytes()).unwrap();
    file.write_all(&1_u16.to_le_bytes()).unwrap();
    file.write_all(&2_u16.to_le_bytes()).unwrap();
    file.write_all(&48_000_u32.to_le_bytes()).unwrap();
    file.write_all(&192_000_u32.to_le_bytes()).unwrap();
    file.write_all(&4_u16.to_le_bytes()).unwrap();
    file.write_all(&16_u16.to_le_bytes()).unwrap();
    if let Some(junk_bytes) = junk_bytes {
        file.write_all(b"JUNK").unwrap();
        file.write_all(&junk_bytes.to_le_bytes()).unwrap();
        let zeroes = [0_u8; 64 * 1024];
        let mut remaining = junk_bytes as usize;
        while remaining != 0 {
            let count = remaining.min(zeroes.len());
            file.write_all(&zeroes[..count]).unwrap();
            remaining -= count;
        }
    }
    file.write_all(b"data").unwrap();
    file.write_all(&data_bytes.to_le_bytes()).unwrap();
    let mut pcm_hash = Sha256::new();
    let mut block = Vec::with_capacity(4096 * 4);
    for frame in 0..frames {
        block.extend_from_slice(&pcm_frame(frame, frames));
        if block.len() == 4096 * 4 || frame + 1 == frames {
            pcm_hash.update(&block);
            file.write_all(&block).unwrap();
            block.clear();
        }
    }
    file.flush().unwrap();
    file.into_inner().unwrap().sync_all().unwrap();
    let (actual_size, container_sha256) = file_identity(path);
    assert_eq!(actual_size, total);
    ExpectedAudio {
        frames,
        container_sha256,
        pcm_sha256: hex::encode(pcm_hash.finalize()),
        first: pcm_frame(0, frames),
        last: pcm_frame(frames - 1, frames),
    }
}

type SqlInventory = Vec<(String, Vec<String>, Vec<Vec<String>>)>;

fn sql_inventory(database: &Path) -> SqlInventory {
    let connection = Connection::open_with_flags(
        database,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .unwrap();
    connection
        .execute_batch("PRAGMA query_only=ON; BEGIN;")
        .unwrap();
    let tables = connection
        .prepare("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    let mut inventory = Vec::new();
    for table in std::iter::once("sqlite_schema".to_owned()).chain(tables) {
        let quoted = table.replace('"', "\"\"");
        let mut statement = connection
            .prepare(&format!("SELECT * FROM \"{quoted}\""))
            .unwrap();
        let columns = statement
            .column_names()
            .iter()
            .map(|value| (*value).to_owned())
            .collect();
        let count = statement.column_count();
        let mut rows = statement
            .query_map([], |row| {
                (0..count)
                    .map(|index| {
                        Ok(match row.get_ref(index)? {
                            ValueRef::Null => "null".to_owned(),
                            ValueRef::Integer(value) => format!("integer:{value}"),
                            ValueRef::Real(value) => format!("real-bits:{:016x}", value.to_bits()),
                            ValueRef::Text(value) => format!("text:{}", hex::encode(value)),
                            ValueRef::Blob(value) => format!("blob:{}", hex::encode(value)),
                        })
                    })
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        rows.sort();
        inventory.push((table, columns, rows));
    }
    assert_eq!(
        connection.total_changes(),
        0,
        "the SQL observer must remain read-only"
    );
    connection.execute_batch("ROLLBACK").unwrap();
    inventory
}

fn cas_inventory(root: &Path) -> Vec<(PathBuf, String)> {
    fn walk(root: &Path, current: &Path, inventory: &mut Vec<(PathBuf, String)>) {
        for entry in fs::read_dir(current).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            let relative = path.strip_prefix(root).unwrap().to_path_buf();
            if metadata.file_type().is_symlink() {
                inventory.push((
                    relative,
                    format!("symlink:{:?}", fs::read_link(&path).unwrap()),
                ));
            } else if metadata.is_dir() {
                inventory.push((relative, "directory".into()));
                walk(root, &path, inventory);
            } else {
                assert!(metadata.is_file(), "unexpected CAS fixture object");
                let (size, digest) = file_identity(&path);
                inventory.push((relative, format!("file:{size}:{digest}")));
            }
        }
    }
    let mut inventory = Vec::new();
    walk(root, root, &mut inventory);
    inventory.sort();
    inventory
}

#[derive(Debug, PartialEq, Eq)]
struct Custody {
    sql: SqlInventory,
    cas: Vec<(PathBuf, String)>,
    original: (u64, String),
}

struct Fixture {
    _temp: TempDir,
    database: PathBuf,
    source: PathBuf,
    blob_root: PathBuf,
    service: StudioService,
    before_import: RevisionStamp,
    project: Project,
    asset_id: Uuid,
    expected: ExpectedAudio,
}

impl Fixture {
    fn new(frames: u32, total_bytes: Option<u64>) -> Self {
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("studio.sqlite3");
        let source = temp.path().join("original.wav");
        let expected = write_wav(&source, frames, total_bytes);
        let service = StudioService::open(&database).unwrap();
        let initial = service
            .create_project("OriginalMix bounded source")
            .unwrap();
        let before_import = RevisionStamp::from(&initial);
        let imported = service
            .import_asset_file(
                initial.id,
                &before_import,
                "fixture-original-mix-source",
                &source,
                "original.wav".into(),
                "audio/wav".into(),
            )
            .unwrap()
            .project;
        let project = service.project(imported.id).unwrap();
        assert_eq!(project.assets.len(), 1);
        let asset_id = project.assets[0].id;
        assert_eq!(
            project.assets[0].content_sha256.as_deref(),
            Some(expected.container_sha256.as_str())
        );
        let blob_root = service.store().lock().blob_root().to_path_buf();
        Self {
            _temp: temp,
            database,
            source,
            blob_root,
            service,
            before_import,
            project,
            asset_id,
            expected,
        }
    }

    fn custody(&self) -> Custody {
        Custody {
            sql: sql_inventory(&self.database),
            cas: cas_inventory(&self.blob_root),
            original: file_identity(&self.source),
        }
    }

    fn assert_custody_from_independent_store(&self, before: &Custody) {
        assert_eq!(&self.custody(), before);
        let reopened = StudioService::open(&self.database).unwrap();
        let project = reopened.project(self.project.id).unwrap();
        assert_eq!(
            serde_json::to_value(&project).unwrap(),
            serde_json::to_value(&self.project).unwrap()
        );
        drop(reopened);
        assert_eq!(&self.custody(), before);
    }

    fn decode(
        &self,
        budget: OriginalMixDecodeBudget,
    ) -> Result<OriginalMixDecodedPcm, StorageError> {
        self.service.decoded_stem_for_original_mix_with_budget(
            self.project.id,
            &RevisionStamp::from(&self.project),
            self.asset_id,
            budget,
        )
    }

    fn decode_old(&self) -> Result<OriginalMixDecodedPcm, StorageError> {
        self.service.decoded_stem_for_original_mix(
            self.project.id,
            self.project.revision,
            self.asset_id,
        )
    }

    fn assert_exact_audio(&self, decoded: &OriginalMixDecodedPcm) {
        assert_eq!(decoded.sample_frames, u64::from(self.expected.frames));
        assert_eq!(
            decoded.interleaved_s16le.len(),
            self.expected.frames as usize * 4
        );
        assert_eq!(&decoded.interleaved_s16le[..4], &self.expected.first);
        assert_eq!(
            &decoded.interleaved_s16le[decoded.interleaved_s16le.len() - 4..],
            &self.expected.last
        );
        assert_eq!(
            decoded.original_asset_sha256,
            self.expected.container_sha256
        );
        assert_eq!(decoded.decoded_pcm_sha256, self.expected.pcm_sha256);
        assert_eq!(
            hex::encode(Sha256::digest(&decoded.interleaved_s16le)),
            self.expected.pcm_sha256
        );
        assert_ne!(decoded.original_asset_sha256, decoded.decoded_pcm_sha256);
    }
}

fn assert_frame_limit(result: Result<OriginalMixDecodedPcm, StorageError>) {
    let error = result.unwrap_err();
    assert!(
        matches!(&error, StorageError::Domain(DomainError::Invalid(message))
        if message == "Original mix decode limit exceeded: sample_frames"),
        "{error:?}"
    );
}

#[test]
fn audition_compatibility_keeps_fifteen_seconds_and_rejects_one_more_frame() {
    let exact = Fixture::new(48_000 * 15, None);
    let before = exact.custody();
    let old = exact.decode_old().unwrap();
    let explicit = exact
        .decode(OriginalMixDecodeBudget::Audition15Seconds)
        .unwrap();
    exact.assert_exact_audio(&old);
    assert_eq!(explicit, old);
    exact.assert_custody_from_independent_store(&before);

    let excess = Fixture::new(48_000 * 15 + 1, None);
    let before = excess.custody();
    assert_frame_limit(excess.decode_old());
    assert_frame_limit(excess.decode(OriginalMixDecodeBudget::Audition15Seconds));
    excess.assert_custody_from_independent_store(&before);
}

#[test]
fn explicit_full_cut_decodes_sixteen_seconds_with_the_nonzero_tail_intact() {
    let fixture = Fixture::new(48_000 * 16, None);
    let before = fixture.custody();
    assert_frame_limit(fixture.decode_old());
    fixture.assert_exact_audio(
        &fixture
            .decode(OriginalMixDecodeBudget::FullCut180Seconds)
            .unwrap(),
    );
    fixture.assert_custody_from_independent_store(&before);
}

#[test]
fn full_cut_accepts_exactly_one_hundred_eighty_seconds_and_rejects_one_more_frame() {
    let exact = Fixture::new(48_000 * 180, None);
    let before = exact.custody();
    exact.assert_exact_audio(
        &exact
            .decode(OriginalMixDecodeBudget::FullCut180Seconds)
            .unwrap(),
    );
    exact.assert_custody_from_independent_store(&before);

    let excess = Fixture::new(48_000 * 180 + 1, None);
    let before = excess.custody();
    assert_frame_limit(excess.decode(OriginalMixDecodeBudget::FullCut180Seconds));
    excess.assert_custody_from_independent_store(&before);
}

fn check_source_bytes(budget: OriginalMixDecodeBudget, cap: u64) {
    let exact = Fixture::new(48_000, Some(cap));
    let before = exact.custody();
    exact.assert_exact_audio(&exact.decode(budget).unwrap());
    if budget == OriginalMixDecodeBudget::Audition15Seconds {
        exact.assert_exact_audio(&exact.decode_old().unwrap());
    }
    exact.assert_custody_from_independent_store(&before);

    // Valid RIFF chunks are even-aligned. The reader's causal unit covers +1;
    // +2 retains a completely valid WAV while isolating the container-byte cap.
    let excess = Fixture::new(48_000, Some(cap + 2));
    let before = excess.custody();
    assert!(matches!(excess.decode(budget),
        Err(StorageError::BlobTooLarge { size, limit }) if size == cap + 2 && limit == cap));
    if budget == OriginalMixDecodeBudget::Audition15Seconds {
        assert!(matches!(excess.decode_old(),
            Err(StorageError::BlobTooLarge { size, limit }) if size == cap + 2 && limit == cap));
    }
    excess.assert_custody_from_independent_store(&before);
}

#[test]
fn audition_enforces_its_eight_mebibyte_source_cap_independently_of_duration() {
    check_source_bytes(OriginalMixDecodeBudget::Audition15Seconds, 8 * 1024 * 1024);
}

#[test]
fn full_cut_enforces_its_sixty_four_mebibyte_source_cap_independently_of_duration() {
    check_source_bytes(OriginalMixDecodeBudget::FullCut180Seconds, 64 * 1024 * 1024);
}

#[test]
fn complete_stamp_rejects_foreign_resource_generation_and_stale_revision_without_writes() {
    let fixture = Fixture::new(48_000, None);
    let other = fixture
        .service
        .create_project("Foreign revision identity")
        .unwrap();
    let expected = RevisionStamp::from(&fixture.project);
    let other_stamp = RevisionStamp::from(&other);
    assert_ne!(expected.generation, other_stamp.generation);
    assert_ne!(expected.resource, other_stamp.resource);
    assert!(fixture.before_import.revision < expected.revision);
    let mut wrong_resource = expected.clone();
    wrong_resource.resource = other_stamp.resource;
    let mut wrong_generation = expected.clone();
    wrong_generation.generation = other_stamp.generation;
    let mut wrong_revision = expected.clone();
    wrong_revision.revision = fixture.before_import.revision;
    let before = fixture.custody();
    for stamp in [wrong_resource, wrong_generation, wrong_revision] {
        let error = fixture
            .service
            .decoded_stem_for_original_mix_with_budget(
                fixture.project.id,
                &stamp,
                fixture.asset_id,
                OriginalMixDecodeBudget::FullCut180Seconds,
            )
            .unwrap_err();
        assert!(
            matches!(&error, StorageError::Domain(DomainError::Invalid(message))
            if message.contains("stale project")),
            "{error:?}"
        );
        assert_eq!(fixture.custody(), before);
    }
    fixture.assert_exact_audio(
        &fixture
            .decode(OriginalMixDecodeBudget::FullCut180Seconds)
            .unwrap(),
    );
    fixture.assert_custody_from_independent_store(&before);
}

#[test]
fn changed_cas_bytes_do_not_keep_the_original_digest_authority() {
    let fixture = Fixture::new(48_000, None);
    let original = fixture.custody();
    let path = fixture
        .service
        .store()
        .lock()
        .verified_blob_path(&fixture.expected.container_sha256)
        .unwrap();
    let mut changed = fs::read(&fixture.source).unwrap();
    let last = changed.len() - 1;
    changed[last] ^= 1;
    fs::write(&path, &changed).unwrap();
    let before = fixture.custody();
    assert!(
        matches!(fixture.decode(OriginalMixDecodeBudget::FullCut180Seconds),
        Err(StorageError::InvalidBackup(message)) if message.contains("digest verification"))
    );
    assert_eq!(fixture.custody(), before);
    fs::copy(&fixture.source, &path).unwrap();
    fixture.assert_exact_audio(
        &fixture
            .decode(OriginalMixDecodeBudget::FullCut180Seconds)
            .unwrap(),
    );
    fixture.assert_custody_from_independent_store(&original);
}

#[cfg(unix)]
#[test]
fn redirected_cas_leaf_is_rejected_without_changing_its_target() {
    let fixture = Fixture::new(48_000, None);
    let original = fixture.custody();
    let path = fixture
        .service
        .store()
        .lock()
        .verified_blob_path(&fixture.expected.container_sha256)
        .unwrap();
    let outside = fixture.source.with_file_name("outside-cas-target.wav");
    fs::copy(&fixture.source, &outside).unwrap();
    let sentinel = file_identity(&outside);
    fs::remove_file(&path).unwrap();
    std::os::unix::fs::symlink(&outside, &path).unwrap();
    let before = fixture.custody();
    assert!(matches!(
        fixture.decode(OriginalMixDecodeBudget::FullCut180Seconds),
        Err(StorageError::UnsafeSourcePath(_))
    ));
    assert_eq!(fixture.custody(), before);
    assert_eq!(file_identity(&outside), sentinel);
    fs::remove_file(&path).unwrap();
    fs::copy(&fixture.source, &path).unwrap();
    fixture.assert_exact_audio(
        &fixture
            .decode(OriginalMixDecodeBudget::FullCut180Seconds)
            .unwrap(),
    );
    fixture.assert_custody_from_independent_store(&original);
}
