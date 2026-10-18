//! Process-termination persistence fixture for the public service and storage APIs.
//!
//! Build this example separately, then run its executable directly through the
//! companion Python wrapper. The hold mode keeps both SQLite clients alive until
//! the wrapper kills this process. The verify mode performs observation only.
//! This fixture does not establish power-loss durability or external Host effects.

use motionwright_domain::{
    Asset, BranchState, Change, Marker, Project, RationalTime, RevisionStamp,
};
use motionwright_service::{FileImportIntent, ProjectBackup, StudioService};
use motionwright_storage::{ApplyOutcome, StorageError};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    error::Error,
    ffi::OsString,
    fs::{self, Metadata, OpenOptions},
    io::{self, Read, Write},
    path::{Component, Path, PathBuf},
    sync::{Arc, Barrier},
    thread,
};
use uuid::Uuid;

type FixtureResult<T> = Result<T, Box<dyn Error>>;

const DATABASE: &str = "project/state.sqlite3";
const ORIGINAL_INTENT: &str = "original-intent.json";
const RACE_INTENTS: &str = "race-intents.json";
const CHECKPOINT: &str = "checkpoint.json";
const SOURCE_PATHS: [&str; 3] = [
    "inputs/original.wav",
    "inputs/cas-left.wav",
    "inputs/cas-right.wav",
];
const SOURCE_SAMPLES: [i16; 3] = [100, 200, 300];
const PREPARATION_KEYS: [&str; 7] = [
    "project-process/scene/1",
    "project-process/main-marker/1",
    "project-process/original-import/1",
    "project-process/create-branch/1",
    "project-process/checkout-alternate/1",
    "project-process/alternate-marker/1",
    "project-process/checkout-main/1",
];
const RACE_KEYS: [&str; 2] = ["project-process/cas-left/1", "project-process/cas-right/1"];
const RACE_NAMES: [&str; 2] = ["CAS left source", "CAS right source"];
const MAX_JSON_BYTES: u64 = 2 * 1024 * 1024;
const MAX_BLOB_BYTES: u64 = 16 * 1024;

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
enum Schema {
    #[serde(rename = "motionwright.project-process-recovery/1")]
    V1,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct OriginalIntent {
    schema: Schema,
    project_id: Uuid,
    expected: RevisionStamp,
    request_id: String,
    intent: FileImportIntent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RaceAttempt {
    request_id: String,
    change: Change,
    intent: FileImportIntent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RaceIntents {
    schema: Schema,
    project_id: Uuid,
    expected: RevisionStamp,
    attempts: [RaceAttempt; 2],
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CasExpectation {
    sha256: String,
    size_bytes: u64,
    relative_path: String,
    sample_value: i16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Checkpoint {
    schema: Schema,
    pid: u32,
    database_relative_path: String,
    main_branch_id: Uuid,
    alternate_branch_id: Uuid,
    shared_scene_id: Uuid,
    main_marker_id: Uuid,
    alternate_marker_id: Uuid,
    original_outcome: ApplyOutcome,
    winner_request_id: String,
    winner_outcome: ApplyOutcome,
    loser_request_id: String,
    loser_expected_revision: u64,
    loser_actual_revision: u64,
    committed_request_ids: Vec<String>,
    expected_export: ProjectBackup,
    blobs: [CasExpectation; 3],
    source_relative_paths: [String; 3],
}

fn problem(message: impl Into<String>) -> Box<dyn Error> {
    Box::new(io::Error::other(message.into()))
}

fn require(condition: bool, message: &str) -> FixtureResult<()> {
    if condition {
        Ok(())
    } else {
        Err(problem(message))
    }
}

fn is_link_or_reparse(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_type().is_symlink() || metadata.file_attributes() & 0x0400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

fn checked_root(path: &Path) -> FixtureResult<PathBuf> {
    require(path.is_absolute(), "case root must be absolute")?;
    let metadata = fs::symlink_metadata(path)?;
    require(
        metadata.is_dir() && !is_link_or_reparse(&metadata),
        "case root must be an existing ordinary directory",
    )?;
    Ok(fs::canonicalize(path)?)
}

fn relative_parts(relative: &str) -> FixtureResult<Vec<&std::ffi::OsStr>> {
    require(
        !relative.is_empty() && !relative.contains('\\') && !relative.contains(':'),
        "artifact paths must be portable relative paths",
    )?;
    Path::new(relative)
        .components()
        .map(|part| match part {
            Component::Normal(name) => Ok(name),
            _ => Err(problem("artifact path escapes the case root")),
        })
        .collect()
}

fn existing_file(root: &Path, relative: &str) -> FixtureResult<PathBuf> {
    let parts = relative_parts(relative)?;
    let mut path = root.to_owned();
    for (index, part) in parts.iter().enumerate() {
        path.push(part);
        let metadata = fs::symlink_metadata(&path)?;
        require(!is_link_or_reparse(&metadata), "linked fixture path")?;
        require(
            if index + 1 == parts.len() {
                metadata.is_file()
            } else {
                metadata.is_dir()
            },
            "fixture path has an unexpected file type",
        )?;
    }
    let canonical = fs::canonicalize(&path)?;
    require(
        canonical.starts_with(root),
        "fixture path leaves the case root",
    )?;
    Ok(canonical)
}

fn relative_regular_file(root: &Path, path: &Path) -> FixtureResult<String> {
    let relative = path
        .strip_prefix(root)
        .map_err(|_| problem("product returned a blob outside the case root"))?;
    let mut parts = Vec::new();
    for part in relative.components() {
        match part {
            Component::Normal(name) => {
                parts.push(
                    name.to_str()
                        .ok_or_else(|| problem("fixture path is not UTF-8"))?,
                );
            }
            _ => return Err(problem("product returned a nonlocal blob path")),
        }
    }
    let relative = parts.join("/");
    let checked = existing_file(root, &relative)?;
    require(
        checked == fs::canonicalize(path)?,
        "blob path does not resolve to its recorded location",
    )?;
    Ok(relative)
}

fn sync_parent(path: &Path) -> FixtureResult<()> {
    #[cfg(unix)]
    {
        fs::File::open(
            path.parent()
                .ok_or_else(|| problem("artifact has no parent directory"))?,
        )?
        .sync_all()?;
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
    Ok(())
}

fn write_new(path: &Path, bytes: &[u8]) -> FixtureResult<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    sync_parent(path)
}

fn write_json<T: Serialize>(root: &Path, relative: &str, value: &T) -> FixtureResult<()> {
    relative_parts(relative)?;
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    write_new(&root.join(relative), &bytes)
}

fn read_json<T: DeserializeOwned>(root: &Path, relative: &str) -> FixtureResult<T> {
    let path = existing_file(root, relative)?;
    let metadata = fs::metadata(&path)?;
    require(
        metadata.len() > 0 && metadata.len() <= MAX_JSON_BYTES,
        "fixture JSON exceeds its bounded size",
    )?;
    let mut bytes = Vec::new();
    fs::File::open(path)?
        .take(MAX_JSON_BYTES + 1)
        .read_to_end(&mut bytes)?;
    require(
        !bytes.is_empty() && bytes.len() as u64 <= MAX_JSON_BYTES,
        "fixture JSON grew beyond its bounded size",
    )?;
    Ok(serde_json::from_slice(&bytes)?)
}

fn pcm_wav(sample: i16) -> Vec<u8> {
    let frame_count = 4_800_u32;
    let data_bytes = frame_count * 2;
    let mut wav = Vec::with_capacity((44 + data_bytes) as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&48_000_u32.to_le_bytes());
    wav.extend_from_slice(&96_000_u32.to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_bytes.to_le_bytes());
    for _ in 0..frame_count {
        wav.extend_from_slice(&sample.to_le_bytes());
    }
    wav
}

fn asset_intent(asset: &Asset) -> FileImportIntent {
    FileImportIntent::Asset {
        name: asset.name.clone(),
        media_type: asset.media_type.clone(),
    }
}

fn original_intent() -> FileImportIntent {
    FileImportIntent::Asset {
        name: "Original source".into(),
        media_type: "audio/wav".into(),
    }
}

fn asset_change(change: &Change) -> FixtureResult<&Asset> {
    match change {
        Change::AddAsset { asset } => Ok(asset),
        _ => Err(problem("race request is not an AddAsset change")),
    }
}

fn check_commit(outcome: &ApplyOutcome, project: &Project, request_id: &str) -> FixtureResult<()> {
    require(
        !outcome.replayed
            && outcome.request_id == request_id
            && outcome.previous_revision == project.revision
            && outcome.project.revision == project.revision + 1
            && outcome.project.id == project.id
            && outcome.project.generation == project.generation,
        "new mutation did not produce one sequential original outcome",
    )?;
    outcome.project.validate()?;
    Ok(())
}

fn commit_change(
    service: &StudioService,
    project: &mut Project,
    request_id: &str,
    change: &Change,
) -> FixtureResult<ApplyOutcome> {
    let outcome = service.apply(project.id, &project.stamp(), request_id, change)?;
    check_commit(&outcome, project, request_id)?;
    *project = outcome.project.clone();
    Ok(outcome)
}

fn record_blob(
    service: &StudioService,
    root: &Path,
    digest: &str,
    sample_value: i16,
) -> FixtureResult<CasExpectation> {
    let expected = pcm_wav(sample_value);
    require(
        hex::encode(Sha256::digest(&expected)) == digest,
        "ingested source digest differs from the synthetic input",
    )?;
    let shared = service.store();
    let store = shared.lock();
    let path = store.verified_blob_path(digest)?;
    require(
        store.read_blob(digest, MAX_BLOB_BYTES)? == expected,
        "ingested source bytes differ",
    )?;
    Ok(CasExpectation {
        sha256: digest.into(),
        size_bytes: expected.len() as u64,
        relative_path: relative_regular_file(root, &path)?,
        sample_value,
    })
}

fn verify_blobs(
    service: &StudioService,
    root: &Path,
    blobs: &[CasExpectation; 3],
) -> FixtureResult<()> {
    let digests: BTreeSet<_> = blobs.iter().map(|blob| &blob.sha256).collect();
    require(
        digests.len() == 3,
        "fixture sources must have distinct digests",
    )?;
    for (index, blob) in blobs.iter().enumerate() {
        require(
            blob.sample_value == SOURCE_SAMPLES[index],
            "recorded input generator differs from this fixture",
        )?;
        let observed = record_blob(service, root, &blob.sha256, blob.sample_value)?;
        require(
            observed.size_bytes == blob.size_bytes && observed.relative_path == blob.relative_path,
            "CAS descriptor or path changed",
        )?;
    }
    Ok(())
}

fn verify_source_absence(root: &Path, checkpoint: &Checkpoint) -> FixtureResult<()> {
    let inputs = fs::symlink_metadata(root.join("inputs"))?;
    require(
        inputs.is_dir() && !is_link_or_reparse(&inputs),
        "input directory is not an ordinary directory",
    )?;
    for (index, relative) in checkpoint.source_relative_paths.iter().enumerate() {
        require(
            relative == SOURCE_PATHS[index],
            "checkpoint source path differs from the fixture layout",
        )?;
        match fs::symlink_metadata(root.join(relative)) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Ok(_) => return Err(problem("an original input survived or was recreated")),
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

fn compare_exports(actual: &ProjectBackup, expected: &ProjectBackup) -> FixtureResult<()> {
    require(
        actual.format_version == expected.format_version
            && actual.source_storage_schema == expected.source_storage_schema
            && actual.project == expected.project
            && actual.events == expected.events
            && actual.payload_sha256 == expected.payload_sha256,
        "project export changed across process termination or observation",
    )
}

fn check_branch_state(
    state: &BranchState,
    main_state: &BranchState,
    alternate: bool,
    alternate_marker: &Marker,
) -> FixtureResult<()> {
    let mut expected = main_state.clone();
    if alternate {
        expected.markers.push(alternate_marker.clone());
    }
    require(
        state == &expected,
        "persisted branch content differs from its committed state",
    )
}

fn check_checkpoint(
    checkpoint: &Checkpoint,
    original: &OriginalIntent,
    race: &RaceIntents,
) -> FixtureResult<()> {
    let project = &checkpoint.expected_export.project;
    project.validate()?;
    checkpoint.original_outcome.project.validate()?;
    checkpoint.winner_outcome.project.validate()?;
    require(
        checkpoint.pid > 0 && checkpoint.database_relative_path == DATABASE,
        "checkpoint process or database identity is invalid",
    )?;
    require(
        original.project_id == project.id
            && original.expected.resource == project.resource_key()
            && original.expected.generation == project.generation
            && original.expected.revision == 2
            && original.request_id == PREPARATION_KEYS[2]
            && original.intent == original_intent(),
        "original import intent changed",
    )?;
    require(
        checkpoint.original_outcome.previous_revision == 2
            && checkpoint.original_outcome.project.revision == 3
            && checkpoint.original_outcome.project.id == project.id
            && checkpoint.original_outcome.project.generation == project.generation
            && checkpoint.original_outcome.request_id == original.request_id
            && !checkpoint.original_outcome.replayed,
        "historical original receipt does not match its dispatched intent",
    )?;
    require(
        race.project_id == project.id
            && race.expected.resource == project.resource_key()
            && race.expected.generation == project.generation
            && race.expected.revision == 7,
        "race clients did not share the original revision scope",
    )?;
    let mut candidate_ids = BTreeSet::new();
    for (index, attempt) in race.attempts.iter().enumerate() {
        let asset = asset_change(&attempt.change)?;
        require(
            attempt.request_id == RACE_KEYS[index]
                && asset.name == RACE_NAMES[index]
                && asset.media_type == "audio/wav"
                && asset.source_revision.as_deref() == Some("local-import")
                && asset.content_sha256.as_deref()
                    == Some(checkpoint.blobs[index + 1].sha256.as_str())
                && attempt.intent == asset_intent(asset),
            "candidate input, metadata or identity changed",
        )?;
        require(
            candidate_ids.insert(asset.id),
            "candidate asset IDs are not distinct",
        )?;
    }
    let winner = race
        .attempts
        .iter()
        .find(|attempt| attempt.request_id == checkpoint.winner_request_id)
        .ok_or_else(|| problem("winner is not an original race request"))?;
    let loser = race
        .attempts
        .iter()
        .find(|attempt| attempt.request_id == checkpoint.loser_request_id)
        .ok_or_else(|| problem("loser is not an original race request"))?;
    require(
        winner.request_id != loser.request_id
            && winner.change != loser.change
            && checkpoint.loser_expected_revision == 7
            && checkpoint.loser_actual_revision == 8,
        "race is not distinct-change CAS with one explicit revision conflict",
    )?;
    require(
        checkpoint.winner_outcome.previous_revision == 7
            && checkpoint.winner_outcome.request_id == winner.request_id
            && !checkpoint.winner_outcome.replayed
            && checkpoint.winner_outcome.project == *project
            && project.revision == 8,
        "winning mutation does not match the committed final document",
    )?;
    let expected_keys: Vec<_> = PREPARATION_KEYS
        .iter()
        .map(|key| (*key).to_owned())
        .chain(std::iter::once(winner.request_id.clone()))
        .collect();
    require(
        checkpoint.committed_request_ids == expected_keys,
        "expected receipt keys do not describe the eight original commits",
    )?;
    require(
        checkpoint.expected_export.events.len() == 8
            && checkpoint
                .expected_export
                .events
                .iter()
                .enumerate()
                .all(|(index, event)| event.revision == index as u64 + 1)
            && checkpoint.expected_export.events[7].change == winner.change,
        "event history does not contain exactly one committed race change",
    )?;
    require(
        checkpoint.original_outcome.project.assets.len() == 1,
        "original import did not commit exactly one asset",
    )?;
    let original_asset = &checkpoint.original_outcome.project.assets[0];
    require(
        original_asset.name == "Original source"
            && original_asset.media_type == "audio/wav"
            && original_asset.source_revision.as_deref() == Some("local-import")
            && original_asset.content_sha256.as_deref()
                == Some(checkpoint.blobs[0].sha256.as_str())
            && checkpoint.expected_export.events[2].change
                == Change::AddAsset {
                    asset: original_asset.clone(),
                },
        "original receipt, event and CAS source are not bound",
    )?;
    let winning_asset = asset_change(&winner.change)?;
    let losing_asset = asset_change(&loser.change)?;
    require(
        project.assets.len() == 2
            && project.assets.contains(original_asset)
            && project.assets.contains(winning_asset)
            && !project
                .assets
                .iter()
                .any(|asset| asset.id == losing_asset.id),
        "a losing candidate was associated or a committed asset disappeared",
    )?;
    require(
        project.active_branch == checkpoint.main_branch_id
            && project.branches.len() == 2
            && project.branch_workspaces.len() == 2,
        "both expected branches and workspaces must survive",
    )?;
    let main = project
        .branches
        .iter()
        .find(|branch| branch.id == checkpoint.main_branch_id)
        .ok_or_else(|| problem("main branch missing"))?;
    let alternate = project
        .branches
        .iter()
        .find(|branch| branch.id == checkpoint.alternate_branch_id)
        .ok_or_else(|| problem("alternate branch missing"))?;
    require(
        main.name == "main"
            && main.parent_branch.is_none()
            && main.base_revision == 0
            && main.head_revision == 8
            && alternate.name == "Persistence alternate"
            && alternate.parent_branch == Some(main.id)
            && alternate.base_revision == 3
            && alternate.head_revision == 6,
        "branch parent, base or head revisions changed",
    )?;
    require(
        project.scenes.len() == 1,
        "committed scene missing or duplicated",
    )?;
    let scene = &project.scenes[0];
    require(
        scene.id == checkpoint.shared_scene_id
            && scene.name == "Persistent scene"
            && scene.objective == "Committed scene survives process termination"
            && scene.start == RationalTime { num: 0, den: 1 }
            && scene.duration == RationalTime { num: 4, den: 1 },
        "committed scene identity or content changed",
    )?;
    let main_marker = Marker {
        id: checkpoint.main_marker_id,
        at: RationalTime { num: 1, den: 1 },
        label: "Main baseline".into(),
    };
    let alternate_marker = Marker {
        id: checkpoint.alternate_marker_id,
        at: RationalTime { num: 2, den: 1 },
        label: "Alternate committed marker".into(),
    };
    require(
        project.markers == vec![main_marker],
        "active main content was replaced by alternate branch content",
    )?;
    let main_workspace = project
        .branch_workspaces
        .iter()
        .find(|workspace| workspace.branch_id == main.id)
        .ok_or_else(|| problem("main workspace missing"))?;
    let alternate_workspace = project
        .branch_workspaces
        .iter()
        .find(|workspace| workspace.branch_id == alternate.id)
        .ok_or_else(|| problem("alternate workspace missing"))?;
    require(
        main_workspace.current_state.scenes == project.scenes
            && main_workspace.current_state.markers == project.markers
            && alternate_workspace.base_revision == 3,
        "workspace snapshot does not match the committed fork",
    )?;
    check_branch_state(
        &alternate_workspace.base_state,
        &main_workspace.current_state,
        false,
        &alternate_marker,
    )?;
    check_branch_state(
        &alternate_workspace.current_state,
        &main_workspace.current_state,
        true,
        &alternate_marker,
    )
}

fn emit(value: serde_json::Value) -> FixtureResult<()> {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    serde_json::to_writer(&mut output, &value)?;
    output.write_all(b"\n")?;
    output.flush()?;
    Ok(())
}

fn hold(root: &Path) -> FixtureResult<()> {
    // The wrapper owns the new case root. The fixture never reuses its own
    // application subdirectories or replaces an existing intent/checkpoint.
    fs::create_dir(root.join("project"))?;
    fs::create_dir(root.join("inputs"))?;
    for (relative, sample) in SOURCE_PATHS.iter().zip(SOURCE_SAMPLES) {
        write_new(&root.join(relative), &pcm_wav(sample))?;
    }

    let left = StudioService::open(root.join(DATABASE))?;
    let mut project = left.create_project("Project process persistence")?;
    require(
        project.revision == 0 && project.branches.len() == 1 && project.assets.is_empty(),
        "fixture requires a new project",
    )?;
    let main_branch_id = project.active_branch;
    commit_change(
        &left,
        &mut project,
        PREPARATION_KEYS[0],
        &Change::AddScene {
            name: "Persistent scene".into(),
            objective: "Committed scene survives process termination".into(),
            duration_seconds: 4,
        },
    )?;
    let shared_scene_id = project.scenes[0].id;
    commit_change(
        &left,
        &mut project,
        PREPARATION_KEYS[1],
        &Change::AddMarker {
            at: RationalTime { num: 1, den: 1 },
            label: "Main baseline".into(),
        },
    )?;
    let main_marker_id = project.markers[0].id;

    let original = OriginalIntent {
        schema: Schema::V1,
        project_id: project.id,
        expected: project.stamp(),
        request_id: PREPARATION_KEYS[2].into(),
        intent: original_intent(),
    };
    write_json(root, ORIGINAL_INTENT, &original)?;
    let original_outcome = left.import_asset_file(
        original.project_id,
        &original.expected,
        &original.request_id,
        root.join(SOURCE_PATHS[0]),
        "Original source".into(),
        "audio/wav".into(),
    )?;
    check_commit(&original_outcome, &project, &original.request_id)?;
    project = original_outcome.project.clone();
    require(
        project.assets.len() == 1,
        "original import did not create one asset",
    )?;
    let original_digest = project.assets[0]
        .content_sha256
        .as_deref()
        .ok_or_else(|| problem("original import lacks a content digest"))?
        .to_owned();
    let original_blob = record_blob(&left, root, &original_digest, SOURCE_SAMPLES[0])?;

    commit_change(
        &left,
        &mut project,
        PREPARATION_KEYS[3],
        &Change::CreateBranch {
            name: "Persistence alternate".into(),
        },
    )?;
    require(
        project.active_branch == main_branch_id && project.branches.len() == 2,
        "CreateBranch changed the active branch or failed to fork",
    )?;
    let alternate_branch_id = project
        .branches
        .iter()
        .find(|branch| branch.id != main_branch_id)
        .ok_or_else(|| problem("new branch missing"))?
        .id;
    commit_change(
        &left,
        &mut project,
        PREPARATION_KEYS[4],
        &Change::CheckoutBranch {
            branch_id: alternate_branch_id,
        },
    )?;
    commit_change(
        &left,
        &mut project,
        PREPARATION_KEYS[5],
        &Change::AddMarker {
            at: RationalTime { num: 2, den: 1 },
            label: "Alternate committed marker".into(),
        },
    )?;
    let alternate_marker_id = project
        .markers
        .iter()
        .find(|marker| marker.label == "Alternate committed marker")
        .ok_or_else(|| problem("alternate marker missing"))?
        .id;
    commit_change(
        &left,
        &mut project,
        PREPARATION_KEYS[6],
        &Change::CheckoutBranch {
            branch_id: main_branch_id,
        },
    )?;

    let mut candidates = Vec::new();
    let mut candidate_blobs = Vec::new();
    for index in 0..2 {
        let descriptor = left
            .store()
            .lock()
            .ingest_blob_file(root.join(SOURCE_PATHS[index + 1]))?;
        let blob = record_blob(&left, root, &descriptor.sha256, SOURCE_SAMPLES[index + 1])?;
        require(
            descriptor.size_bytes == blob.size_bytes,
            "ingested candidate size differs",
        )?;
        let asset = Asset {
            id: Uuid::now_v7(),
            name: RACE_NAMES[index].into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(descriptor.sha256),
            source_revision: Some("local-import".into()),
        };
        candidates.push(RaceAttempt {
            request_id: RACE_KEYS[index].into(),
            intent: asset_intent(&asset),
            change: Change::AddAsset { asset },
        });
        candidate_blobs.push(blob);
    }
    let attempts: [RaceAttempt; 2] = candidates
        .try_into()
        .map_err(|_| problem("fixture candidate count is not two"))?;
    let [left_blob, right_blob]: [CasExpectation; 2] = candidate_blobs
        .try_into()
        .map_err(|_| problem("fixture candidate blob count is not two"))?;
    let blobs = [original_blob, left_blob, right_blob];

    // Open a second independent SQLite connection, not a clone of the first
    // service's Arc/Mutex. Both clients observe the same revision before dispatch.
    let right = StudioService::open(root.join(DATABASE))?;
    let left_observed = left.project(project.id)?;
    let right_observed = right.project(project.id)?;
    require(
        project.revision == 7 && left_observed == project && right_observed == project,
        "independent clients did not observe the same prepared document",
    )?;
    let race = RaceIntents {
        schema: Schema::V1,
        project_id: project.id,
        expected: project.stamp(),
        attempts,
    };
    write_json(root, RACE_INTENTS, &race)?;

    let barrier = Arc::new(Barrier::new(3));
    let left_barrier = Arc::clone(&barrier);
    let left_attempt = race.attempts[0].clone();
    let left_stamp = race.expected.clone();
    let project_id = project.id;
    let left_thread = thread::spawn(move || {
        left_barrier.wait();
        let outcome = left.apply(
            project_id,
            &left_stamp,
            &left_attempt.request_id,
            &left_attempt.change,
        );
        (left, outcome)
    });
    let right_barrier = Arc::clone(&barrier);
    let right_attempt = race.attempts[1].clone();
    let right_stamp = race.expected.clone();
    let right_thread = thread::spawn(move || {
        right_barrier.wait();
        let outcome = right.apply(
            project_id,
            &right_stamp,
            &right_attempt.request_id,
            &right_attempt.change,
        );
        (right, outcome)
    });
    barrier.wait();
    let (left, left_result) = left_thread
        .join()
        .map_err(|_| problem("left CAS client panicked"))?;
    let (right, right_result) = right_thread
        .join()
        .map_err(|_| problem("right CAS client panicked"))?;
    let (winner_outcome, winner_index, loser_expected_revision, loser_actual_revision) =
        match (left_result, right_result) {
            (Ok(winner), Err(StorageError::Conflict { expected, actual })) => {
                (winner, 0, expected, actual)
            }
            (Err(StorageError::Conflict { expected, actual }), Ok(winner)) => {
                (winner, 1, expected, actual)
            }
            other => {
                return Err(problem(format!(
                    "CAS requires one original success and one Conflict; observed {other:?}"
                )));
            }
        };
    check_commit(
        &winner_outcome,
        &project,
        &race.attempts[winner_index].request_id,
    )?;
    let winner_request_id = race.attempts[winner_index].request_id.clone();
    let loser_request_id = race.attempts[1 - winner_index].request_id.clone();
    let expected_export = left.export_project(project_id)?;
    require(
        right.project(project_id)? == expected_export.project,
        "independent clients disagree after the committed race",
    )?;
    let committed_request_ids = PREPARATION_KEYS
        .iter()
        .map(|key| (*key).to_owned())
        .chain(std::iter::once(winner_request_id.clone()))
        .collect();
    let checkpoint = Checkpoint {
        schema: Schema::V1,
        pid: std::process::id(),
        database_relative_path: DATABASE.into(),
        main_branch_id,
        alternate_branch_id,
        shared_scene_id,
        main_marker_id,
        alternate_marker_id,
        original_outcome,
        winner_request_id,
        winner_outcome,
        loser_request_id,
        loser_expected_revision,
        loser_actual_revision,
        committed_request_ids,
        expected_export,
        blobs,
        source_relative_paths: SOURCE_PATHS.map(str::to_owned),
    };
    check_checkpoint(&checkpoint, &original, &race)?;
    verify_blobs(&left, root, &checkpoint.blobs)?;
    for relative in SOURCE_PATHS {
        fs::remove_file(existing_file(root, relative)?)?;
        sync_parent(&root.join(relative))?;
    }
    verify_source_absence(root, &checkpoint)?;
    write_json(root, CHECKPOINT, &checkpoint)?;
    emit(serde_json::json!({
        "schema": Schema::V1,
        "phase": "ready",
        "pid": std::process::id(),
        "checkpoint": CHECKPOINT,
    }))?;

    // Retain both live services until forced termination. Returning them from
    // the worker threads avoids closing either connection at thread exit.
    loop {
        std::hint::black_box((&left, &right));
        thread::park();
    }
}

fn verify(root: &Path) -> FixtureResult<()> {
    let original: OriginalIntent = read_json(root, ORIGINAL_INTENT)?;
    let race: RaceIntents = read_json(root, RACE_INTENTS)?;
    let checkpoint: Checkpoint = read_json(root, CHECKPOINT)?;
    check_checkpoint(&checkpoint, &original, &race)?;
    verify_source_absence(root, &checkpoint)?;
    let database = existing_file(root, DATABASE)?;
    let service = StudioService::open(database)?;
    let project_id = checkpoint.expected_export.project.id;
    let current = service.project(project_id)?;
    current.validate()?;
    require(
        current == checkpoint.expected_export.project && service.projects(2)?.len() == 1,
        "reopened project differs or an extra project exists",
    )?;
    compare_exports(
        &service.export_project(project_id)?,
        &checkpoint.expected_export,
    )?;
    verify_blobs(&service, root, &checkpoint.blobs)?;

    let winner = race
        .attempts
        .iter()
        .find(|attempt| attempt.request_id == checkpoint.winner_request_id)
        .ok_or_else(|| problem("winner intent missing"))?;
    let loser = race
        .attempts
        .iter()
        .find(|attempt| attempt.request_id == checkpoint.loser_request_id)
        .ok_or_else(|| problem("loser intent missing"))?;
    let mut expected_original = checkpoint.original_outcome.clone();
    expected_original.replayed = true;
    let mut expected_winner = checkpoint.winner_outcome.clone();
    expected_winner.replayed = true;
    for _ in 0..2 {
        require(
            service.recover_file_import(
                original.project_id,
                &original.expected,
                &original.request_id,
                &original.intent,
            )? == Some(expected_original.clone()),
            "read-only recovery did not return the historical original outcome",
        )?;
        require(
            service.recover_file_import(
                race.project_id,
                &race.expected,
                &winner.request_id,
                &winner.intent,
            )? == Some(expected_winner.clone()),
            "read-only recovery did not return the committed winner",
        )?;
        require(
            service
                .recover_file_import(
                    race.project_id,
                    &race.expected,
                    &loser.request_id,
                    &loser.intent,
                )?
                .is_none(),
            "a receipt exists for the rejected distinct-change request",
        )?;
        require(
            service.project(project_id)? == current,
            "receipt observation rewound or modified the current project",
        )?;
    }
    compare_exports(
        &service.export_project(project_id)?,
        &checkpoint.expected_export,
    )?;
    verify_blobs(&service, root, &checkpoint.blobs)?;
    verify_source_absence(root, &checkpoint)?;
    emit(serde_json::json!({
        "schema": Schema::V1,
        "phase": "verified",
        "pid": std::process::id(),
        "project_id": project_id,
        "revision": current.revision,
        "original_revision": checkpoint.original_outcome.project.revision,
        "winner_revision": checkpoint.winner_outcome.project.revision,
        "missing_request_id": checkpoint.loser_request_id,
        "read_only_recovery": true,
    }))
}

fn run() -> FixtureResult<()> {
    let mut arguments = std::env::args_os().skip(1);
    let mode: OsString = arguments
        .next()
        .ok_or_else(|| problem("usage: project-process-recovery <hold|verify> <case-root>"))?;
    let root = arguments
        .next()
        .ok_or_else(|| problem("missing absolute case root"))?;
    require(arguments.next().is_none(), "unexpected fixture argument")?;
    let root = checked_root(Path::new(&root))?;
    match mode.to_str() {
        Some("hold") => hold(&root),
        Some("verify") => verify(&root),
        _ => Err(problem("mode must be hold or verify")),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("project-process-recovery: {error}");
        std::process::exit(1);
    }
}
