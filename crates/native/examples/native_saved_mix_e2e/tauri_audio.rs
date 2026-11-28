//! Preparation and read-only verification for the genuine Tauri audio case.
//! This module never opens a WebView, invokes a command, mixes PCM or grants
//! rights. The external admitted harness binds UI/capture custody separately.
use super::{
    MAX_WITNESS_BYTES, Result, WAV_BYTES, digest, fixture_recipe, seed_inputs, write_json,
};
use motionwright_domain::{Change, DeliveryRecipe, Project, RevisionStamp};
use motionwright_service::StudioServiceReader;
use motionwright_storage::{AssetIntegrityPage, AssetIntegrityStatus, ProjectEvent};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::BTreeSet,
    fs::{self, DirBuilder, Metadata, OpenOptions},
    io::Read,
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    path::{Component, Path, PathBuf},
};
use uuid::Uuid;

const BEFORE: &str = "tauri-audio-before.private.json";
const READBACK: &str = "tauri-audio-readback.private.json";
const BEFORE_SCHEMA: &str = "motionwright.tauri-audio-fixture-before/1";
const SOURCE_CLASS: &str = "SYNTHETIC_TONES_NOT_SPEECH";
const SOURCE_GENERATOR: &str = "tooling/native_saved_mix_av.py::write_stems";
const BUSES: [&str; 3] = ["voice", "music", "sfx"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct NodeIdentity {
    device: u64,
    inode: u64,
    mode: u32,
    uid: u32,
    gid: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct DirectoryWitness {
    path: PathBuf,
    identity: NodeIdentity,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileIdentity {
    node: NodeIdentity,
    links: u64,
    bytes: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    changed_seconds: i64,
    changed_nanoseconds: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileWitness {
    path: PathBuf,
    sha256: String,
    bytes: u64,
    identity: FileIdentity,
    namespace: Vec<DirectoryWitness>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AssetWitness {
    bus: String,
    asset_id: Uuid,
    source: FileWitness,
    cas: FileWitness,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Before {
    schema: String,
    private_root: PathBuf,
    database_path: PathBuf,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    revision_before: u64,
    resource: String,
    deliverable_profile_id: Uuid,
    expected_revision: u64,
    expected_change: Change,
    project: Project,
    history: Vec<ProjectEvent>,
    database_identity: String,
    store_namespace: Vec<DirectoryWitness>,
    cas_root: DirectoryWitness,
    evidence_namespace: Vec<DirectoryWitness>,
    assets: Vec<AssetWitness>,
    asset_integrity: AssetIntegrityPage,
    source_classification: String,
    source_generator: String,
    source_generation_authority: String,
    preparation_tauri_owner_invocation: String,
    expected_save_authority: String,
    writer_handoff_verified: bool,
    automatic_retry: bool,
    human_approval: bool,
    release_authorized: bool,
    #[serde(rename = "formal_G2")]
    formal_g2: bool,
    #[serde(rename = "formal_G3")]
    formal_g3: bool,
}

fn uid() -> u32 {
    // SAFETY: geteuid has no arguments, does not mutate memory and cannot fail.
    unsafe { libc::geteuid() }
}

fn node(meta: &Metadata) -> NodeIdentity {
    NodeIdentity {
        device: meta.dev(),
        inode: meta.ino(),
        mode: meta.mode(),
        uid: meta.uid(),
        gid: meta.gid(),
    }
}

fn file_identity(meta: &Metadata) -> Result<FileIdentity> {
    if !meta.is_file()
        || meta.file_type().is_symlink()
        || meta.nlink() != 1
        || meta.uid() != uid()
        || meta.mode() & 0o077 != 0
    {
        return Err("Tauri audio input must be a private, single-link regular file".into());
    }
    Ok(FileIdentity {
        node: node(meta),
        links: meta.nlink(),
        bytes: meta.len(),
        modified_seconds: meta.mtime(),
        modified_nanoseconds: meta.mtime_nsec(),
        changed_seconds: meta.ctime(),
        changed_nanoseconds: meta.ctime_nsec(),
    })
}

fn directory(path: &Path) -> Result<DirectoryWitness> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir()
        || meta.file_type().is_symlink()
        || meta.uid() != uid()
        || meta.mode() & 0o022 != 0
        || fs::canonicalize(path)? != path
    {
        return Err(
            "Tauri audio directory must be canonical, current-owned and deny other writers".into(),
        );
    }
    // Directory contents may legitimately change; their size/times/link count
    // are not identity. Leaf files retain the complete stability tuple below.
    Ok(DirectoryWitness {
        path: path.to_path_buf(),
        identity: node(&meta),
    })
}

fn namespace(root: &Path, path: &Path) -> Result<Vec<DirectoryWitness>> {
    if !root.is_absolute() || !path.is_absolute() {
        return Err("Tauri audio requires absolute private paths".into());
    }
    let relative = path.strip_prefix(root)?;
    let components: Vec<_> = relative.components().collect();
    if components.len() > 24
        || components
            .iter()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err("Tauri audio path is not a bounded private descendant".into());
    }
    let mut current = root.to_path_buf();
    let root_witness = directory(&current)?;
    if root_witness.identity.mode & 0o077 != 0 {
        return Err("Tauri audio root must deny all group and other access".into());
    }
    // Product CAS subdirectories may retain ordinary read/traverse bits. Their
    // current owner, no-other-writer policy and frozen identity remain checked;
    // the mandatory private root is the access boundary for every descendant.
    let mut observed = vec![root_witness];
    for part in components {
        current.push(part.as_os_str());
        observed.push(directory(&current)?);
    }
    Ok(observed)
}

fn require_namespace(expected: &[DirectoryWitness]) -> Result<()> {
    if expected.is_empty() || expected.len() > 25 {
        return Err("Tauri audio original namespace is absent or oversized".into());
    }
    for original in expected {
        if directory(&original.path)? != *original {
            return Err("Tauri audio original directory identity changed".into());
        }
    }
    Ok(())
}

fn new_private_directory(path: &Path) -> Result<()> {
    DirBuilder::new().mode(0o700).create(path)?;
    directory(path)?;
    Ok(())
}

fn absent(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
        Ok(_) => Err("Tauri audio refuses an existing output or Store; no retry".into()),
    }
}

fn database_parent(root: &Path, database: &Path, create: bool) -> Result<PathBuf> {
    namespace(root, root)?;
    let data = root.join("profile/data");
    let parent = database
        .parent()
        .ok_or("Tauri audio database parent is absent")?;
    if !database.is_absolute()
        || database.file_name().and_then(|name| name.to_str()) != Some("motionwright.sqlite3")
        || !parent.starts_with(&data)
        || parent == data
    {
        return Err("Tauri audio database must be the explicit private XDG app-data Store".into());
    }
    let relative = parent.strip_prefix(root)?;
    if relative.components().count() > 24 {
        return Err("Tauri audio database namespace is oversized".into());
    }
    let mut current = root.to_path_buf();
    for part in relative.components() {
        if !matches!(part, Component::Normal(_)) {
            return Err("Tauri audio database path contains an alias".into());
        }
        current.push(part.as_os_str());
        if create {
            match fs::symlink_metadata(&current) {
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    new_private_directory(&current)?
                }
                Err(error) => return Err(error.into()),
                Ok(_) => {
                    directory(&current)?;
                }
            }
        } else {
            directory(&current)?;
        }
    }
    namespace(root, parent)?;
    Ok(parent.to_path_buf())
}

fn read_file(root: &Path, path: &Path, limit: usize) -> Result<(Vec<u8>, FileWitness)> {
    let parent = path.parent().ok_or("Tauri audio file parent is absent")?;
    let before_namespace = namespace(root, parent)?;
    let original = file_identity(&fs::symlink_metadata(path)?)?;
    if original.bytes > limit as u64 {
        return Err("Tauri audio original file exceeded its fixed bound".into());
    }
    let mut stream = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)?;
    if file_identity(&stream.metadata()?)? != original {
        return Err("Tauri audio opened file differs from its original namespace".into());
    }
    let mut bytes = Vec::with_capacity(original.bytes as usize);
    Read::by_ref(&mut stream)
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != original.bytes
        || bytes.len() > limit
        || file_identity(&stream.metadata()?)? != original
        || file_identity(&fs::symlink_metadata(path)?)? != original
        || namespace(root, parent)? != before_namespace
    {
        return Err("Tauri audio original changed during its bounded read".into());
    }
    let witness = FileWitness {
        path: path.to_path_buf(),
        sha256: digest(&bytes),
        bytes: bytes.len() as u64,
        identity: original,
        namespace: before_namespace,
    };
    Ok((bytes, witness))
}

fn require_wav(raw: &[u8]) -> Result<()> {
    if raw.len() != WAV_BYTES
        || &raw[..4] != b"RIFF"
        || raw[4..8] != (WAV_BYTES as u32 - 8).to_le_bytes()
        || &raw[8..16] != b"WAVEfmt "
        || raw[16..20] != 16_u32.to_le_bytes()
        || raw[20..22] != 1_u16.to_le_bytes()
        || raw[22..24] != 2_u16.to_le_bytes()
        || raw[24..28] != 48_000_u32.to_le_bytes()
        || raw[28..32] != 192_000_u32.to_le_bytes()
        || raw[32..34] != 4_u16.to_le_bytes()
        || raw[34..36] != 16_u16.to_le_bytes()
        || &raw[36..40] != b"data"
        || raw[40..44] != (96_000_u32 * 4).to_le_bytes()
    {
        return Err(
            "Tauri audio requires the original two-second stereo 48-kHz PCM16 stems".into(),
        );
    }
    Ok(())
}

fn sources(root: &Path) -> Result<Vec<(String, FileWitness)>> {
    let source_root = root.join("sources");
    namespace(root, &source_root)?;
    let expected_names: BTreeSet<_> = BUSES
        .iter()
        .map(|bus| format!("saved-mix-{bus}.wav"))
        .collect();
    let entries = fs::read_dir(&source_root)?
        .take(4)
        .map(|entry| {
            Ok(entry?
                .file_name()
                .into_string()
                .map_err(|_| "Tauri audio source name is not UTF-8")?)
        })
        .collect::<Result<BTreeSet<String>>>()?;
    if entries != expected_names {
        return Err(
            "Tauri audio sources must contain exactly the three declared original stems".into(),
        );
    }
    let mut observed = Vec::with_capacity(3);
    for bus in BUSES {
        let (bytes, witness) = read_file(
            root,
            &source_root.join(format!("saved-mix-{bus}.wav")),
            WAV_BYTES,
        )?;
        require_wav(&bytes)?;
        observed.push((bus.to_owned(), witness));
    }
    if observed
        .iter()
        .map(|(_, item)| &item.sha256)
        .collect::<BTreeSet<_>>()
        .len()
        != 3
    {
        return Err("Tauri audio sources are not three distinct original buses".into());
    }
    Ok(observed)
}

fn assets(
    root: &Path,
    database: &Path,
    project: &Project,
    original_sources: &[(String, FileWitness)],
) -> Result<Vec<AssetWitness>> {
    if project.assets.len() != 3 || original_sources.len() != 3 {
        return Err("Tauri audio original asset count changed".into());
    }
    let cas_root = database
        .parent()
        .ok_or("Tauri audio Store parent is absent")?
        .join("blobs");
    let mut observed = Vec::with_capacity(3);
    for (bus, original) in original_sources {
        let name = format!("saved-mix-{bus}.wav");
        let matches: Vec<_> = project
            .assets
            .iter()
            .filter(|asset| asset.name == name)
            .collect();
        if matches.len() != 1
            || matches[0].media_type != "audio/wav"
            || matches[0].content_sha256.as_deref() != Some(original.sha256.as_str())
            || original.sha256.len() != 64
            || !original
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(
                "Tauri audio imported asset is not bound to its exact original source".into(),
            );
        }
        let path = cas_root
            .join("sha256")
            .join(&original.sha256[..2])
            .join(&original.sha256);
        let (bytes, cas) = read_file(root, &path, WAV_BYTES)?;
        require_wav(&bytes)?;
        if cas.sha256 != original.sha256 {
            return Err("Tauri audio original CAS bytes differ from the source stem".into());
        }
        observed.push(AssetWitness {
            bus: bus.clone(),
            asset_id: matches[0].id,
            source: original.clone(),
            cas,
        });
    }
    Ok(observed)
}

fn integrity(reader: &StudioServiceReader, project: &Project) -> Result<AssetIntegrityPage> {
    let page = reader.asset_integrity_page(project.id, &RevisionStamp::from(project), None, 16)?;
    if !page.complete
        || page.next.is_some()
        || page.total_assets != 3
        || page.items.len() != 3
        || page.checked_bytes != 3 * WAV_BYTES as u64
        || page.items.iter().any(|item| {
            item.status != AssetIntegrityStatus::Verified
                || item.size_bytes != Some(WAV_BYTES as u64)
        })
        || page
            .items
            .iter()
            .map(|item| item.asset_id)
            .collect::<BTreeSet<_>>()
            != project
                .assets
                .iter()
                .map(|asset| asset.id)
                .collect::<BTreeSet<_>>()
    {
        return Err("Tauri audio independent reader did not verify all original CAS bytes".into());
    }
    Ok(page)
}

fn expected_change(project: &Project) -> Result<(Uuid, Change)> {
    let (profile_id, recipe) = fixture_recipe(project)?;
    Ok((
        profile_id,
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(DeliveryRecipe {
                original_mix: Some(recipe),
                ..Default::default()
            }),
        },
    ))
}

fn validate_before(before: &Before, database: &Path, root: &Path) -> Result<()> {
    before.project.validate()?;
    let (profile_id, change) = expected_change(&before.project)?;
    let parent = database
        .parent()
        .ok_or("Tauri audio Store parent is absent")?;
    if before.store_namespace != namespace(root, parent)?
        || before.evidence_namespace != namespace(root, &root.join("evidence"))?
        || before.cas_root.path != parent.join("blobs")
    {
        return Err("Tauri audio before witness selected a foreign namespace".into());
    }
    if before.schema != BEFORE_SCHEMA
        || before.private_root != root
        || before.database_path != database
        || before.project_id != before.project.id
        || before.generation != before.project.generation
        || before.revision != before.project.revision
        || before.revision_before != before.revision
        || before.resource != before.project.resource_key()
        || before.deliverable_profile_id != profile_id
        || before.expected_change != change
        || before.revision >= 64
        || before.expected_revision != before.revision + 1
        || before.history.len() as u64 != before.revision
        || before
            .history
            .iter()
            .enumerate()
            .any(|(index, event)| event.revision != index as u64 + 1)
        || !before.project.production_design.delivery_recipes.is_empty()
        || before.assets.len() != 3
        || before.source_classification != SOURCE_CLASS
        || before.source_generator != SOURCE_GENERATOR
        || before.source_generation_authority != "EXTERNAL_ADMITTED_HARNESS"
        || before.preparation_tauri_owner_invocation != "NOT_EXERCISED"
        || before.expected_save_authority != "REAL_TAURI_UI_NOT_ATTESTED_BY_FIXTURE"
        || !before.writer_handoff_verified
        || before.automatic_retry
        || before.human_approval
        || before.release_authorized
        || before.formal_g2
        || before.formal_g3
    {
        return Err("Tauri audio before witness differs from its closed fixture contract".into());
    }
    Ok(())
}

fn verify_single_save(before: &Before, project: &Project, history: &[ProjectEvent]) -> Result<()> {
    if project.revision != before.expected_revision
        || history.len() != before.history.len() + 1
        || &history[..before.history.len()] != before.history.as_slice()
    {
        return Err(
            "Tauri audio requires exactly one new revision and one original recipe event".into(),
        );
    }
    let event = history.last().ok_or("Tauri audio recipe event is absent")?;
    if event.revision != before.expected_revision
        || event.change != before.expected_change
        || event.created_at != project.updated_at.to_rfc3339()
        || project.updated_at < before.project.updated_at
    {
        return Err("Tauri audio final event differs from the declared UI recipe Save".into());
    }
    let mut expected = before.project.clone();
    expected.apply_change(&before.expected_change)?;
    expected.commit_revision(before.expected_revision, &before.expected_change)?;
    // The Store supplies this timestamp when committing the exact last event.
    // Every other project field, including branch head and resources, is exact.
    expected.updated_at = project.updated_at;
    if expected != *project {
        return Err("Tauri audio project changed outside the one declared recipe Save".into());
    }
    Ok(())
}

pub(super) fn seed(database: &Path, root: &Path) -> Result<()> {
    let original_sources = sources(root)?;
    let parent = database_parent(root, database, true)?;
    absent(database)?;
    for suffix in ["-wal", "-shm", "-journal"] {
        let mut path = database.as_os_str().to_os_string();
        path.push(suffix);
        absent(Path::new(&path))?;
    }
    absent(&parent.join("blobs"))?;
    let evidence = root.join("evidence");
    new_private_directory(&evidence)?;
    // Allocate the original SQLite leaf exclusively with private creation mode.
    // SQLite initializes this same empty file; no document or database is copied.
    super::write_new(database, b"")?;
    // native-audition/ is reserved exclusively for the real Tauri command.
    let (service, prepared, profile_id, _) = seed_inputs(database, &root.join("sources"))?;
    if !prepared.production_design.delivery_recipes.is_empty() {
        return Err("Tauri audio preparation must stop before SetDeliveryRecipe".into());
    }
    service.prepare_read_only_handoff()?;
    drop(service);

    let reader = StudioServiceReader::open(database)?;
    let projects = reader.projects(2)?;
    if projects.len() != 1 || projects[0] != prepared {
        return Err("Tauri audio preparation did not close the exact original writer".into());
    }
    let project = &projects[0];
    let current_sources = sources(root)?;
    if current_sources != original_sources {
        return Err("Tauri audio original sources changed during import".into());
    }
    let asset_integrity = integrity(&reader, project)?;
    let asset_witnesses = assets(root, database, project, &current_sources)?;
    let (_, change) = expected_change(project)?;
    file_identity(&fs::symlink_metadata(database)?)?;
    let before = Before {
        schema: BEFORE_SCHEMA.into(),
        private_root: root.to_path_buf(),
        database_path: database.to_path_buf(),
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        revision_before: project.revision,
        resource: project.resource_key(),
        deliverable_profile_id: profile_id,
        expected_revision: project
            .revision
            .checked_add(1)
            .ok_or("Tauri audio revision overflow")?,
        expected_change: change,
        project: project.clone(),
        history: reader.project_history(project.id, 0, 64)?,
        database_identity: reader.database_file_identity()?,
        store_namespace: namespace(root, &parent)?,
        cas_root: directory(&parent.join("blobs"))?,
        evidence_namespace: namespace(root, &evidence)?,
        assets: asset_witnesses,
        asset_integrity,
        source_classification: SOURCE_CLASS.into(),
        source_generator: SOURCE_GENERATOR.into(),
        source_generation_authority: "EXTERNAL_ADMITTED_HARNESS".into(),
        preparation_tauri_owner_invocation: "NOT_EXERCISED".into(),
        expected_save_authority: "REAL_TAURI_UI_NOT_ATTESTED_BY_FIXTURE".into(),
        writer_handoff_verified: true,
        automatic_retry: false,
        human_approval: false,
        release_authorized: false,
        formal_g2: false,
        formal_g3: false,
    };
    validate_before(&before, database, root)?;
    drop(reader);
    require_namespace(&before.store_namespace)?;
    require_namespace(&before.evidence_namespace)?;
    let target = evidence.join(BEFORE);
    write_json(&target, &serde_json::to_value(&before)?)?;
    let (_, witness) = read_file(root, &target, MAX_WITNESS_BYTES)?;
    println!(
        "{}",
        json!({
            "schema": "motionwright.tauri-audio-fixture-prepared/1",
            "project_id": before.project_id, "generation": before.generation,
            "revision": before.revision, "revision_before": before.revision_before,
            "deliverable_profile_id": before.deliverable_profile_id,
            "expected_revision": before.expected_revision, "expected_change": before.expected_change,
            "database_path": database, "database_identity": before.database_identity,
            "before_witness": witness,
            "writer_closed": true, "preparation_tauri_owner_invocation": "NOT_EXERCISED",
            "formal_G2": false, "formal_G3": false, "automatic_retry": false,
        })
    );
    Ok(())
}

pub(super) fn readback(database: &Path, root: &Path) -> Result<()> {
    let parent = database_parent(root, database, false)?;
    let evidence = root.join("evidence");
    namespace(root, &evidence)?;
    let output = evidence.join(READBACK);
    absent(&output)?;
    let (before_raw, original_witness) =
        read_file(root, &evidence.join(BEFORE), MAX_WITNESS_BYTES)?;
    let before: Before = serde_json::from_slice(&before_raw)?;
    validate_before(&before, database, root)?;
    require_namespace(&before.store_namespace)?;
    require_namespace(&before.evidence_namespace)?;
    if directory(&parent.join("blobs"))? != before.cas_root {
        return Err("Tauri audio original physical CAS root changed".into());
    }
    file_identity(&fs::symlink_metadata(database)?)?;
    let reader = StudioServiceReader::open(database)?;
    if reader.database_file_identity()? != before.database_identity {
        return Err("Tauri audio original physical SQLite Store changed".into());
    }
    let projects = reader.projects(2)?;
    if projects.len() != 1 {
        return Err("Tauri audio reader requires the one original project".into());
    }
    let project = &projects[0];
    let history = reader.project_history(project.id, 0, 64)?;
    verify_single_save(&before, project, &history)?;
    let observed_integrity = integrity(&reader, project)?;
    let current_sources = sources(root)?;
    let observed_assets = assets(root, database, project, &current_sources)?;
    if observed_assets != before.assets {
        return Err("Tauri audio original source or CAS file identity changed".into());
    }
    let mut expected_integrity = before.asset_integrity.clone();
    expected_integrity.revision = project.revision;
    if expected_integrity != observed_integrity {
        return Err("Tauri audio original asset-integrity binding changed".into());
    }
    if reader.database_file_identity()? != before.database_identity
        || directory(&parent.join("blobs"))? != before.cas_root
    {
        return Err("Tauri audio Store identity changed during readback".into());
    }
    let response = json!({
        "schema": "motionwright.tauri-audio-independent-readback/1",
        "project_id": project.id, "generation": project.generation,
        "previous_revision": before.revision, "revision": project.revision,
        "deliverable_profile_id": before.deliverable_profile_id,
        "expected_change": before.expected_change, "observed_project": project,
        "observed_history": history, "database_path": database,
        "database_identity": before.database_identity, "cas_root": before.cas_root,
        "assets": observed_assets, "asset_integrity": observed_integrity,
        "source_witness_sha256": original_witness.sha256,
        "reader_authority": "StudioServiceReader", "writer_handoff_verified": true,
        "same_physical_database": true, "same_physical_cas_root": true,
        "exactly_one_recipe_save": true, "additional_journal_events": 0,
        "verified_original_assets": 3, "verified_original_bytes": 3 * WAV_BYTES,
        "source_classification": SOURCE_CLASS,
        "tauri_owner_invocation": "REQUIRES_EXTERNAL_UI_AND_CAPTURE_JOIN",
        "mixed_pcm_read_or_generated_by_reader": false,
        "human_approval": false, "release_authorized": false,
        "formal_G2": false, "formal_G3": false, "automatic_retry": false,
    });
    drop(reader);
    require_namespace(&before.store_namespace)?;
    require_namespace(&before.evidence_namespace)?;
    let (after_raw, after_witness) = read_file(root, &evidence.join(BEFORE), MAX_WITNESS_BYTES)?;
    if after_raw != before_raw || after_witness != original_witness {
        return Err("Tauri audio before witness changed during readback".into());
    }
    write_json(&output, &response)?;
    let (_, witness) = read_file(root, &output, MAX_WITNESS_BYTES)?;
    println!(
        "{}",
        json!({
            "schema": "motionwright.tauri-audio-readback-receipt/1",
            "readback_witness": witness, "project_id": before.project_id,
            "previous_revision": before.revision, "revision": before.expected_revision,
            "exactly_one_recipe_save": true, "additional_journal_events": 0,
            "formal_G2": false, "formal_G3": false, "automatic_retry": false,
        })
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_service::StudioService;
    use std::os::unix::fs::{PermissionsExt, symlink};

    fn private_test_root() -> tempfile::TempDir {
        tempfile::Builder::new()
            .permissions(fs::Permissions::from_mode(0o700))
            .tempdir()
            .unwrap()
    }

    fn inputs() -> (tempfile::TempDir, PathBuf) {
        let root = private_test_root();
        new_private_directory(&root.path().join("sources")).unwrap();
        for (bus, left, right) in [
            ("voice", 1_000, 2_000),
            ("music", 3_000, 4_000),
            ("sfx", 500, 700),
        ] {
            // Existing bounded WAV helper only; these source controls are not
            // a Native/UI run or the independent synthetic-tone PCM oracle.
            let wav = super::super::tests::source_wav(left, right);
            super::super::write_new(
                &root
                    .path()
                    .join("sources")
                    .join(format!("saved-mix-{bus}.wav")),
                &wav,
            )
            .unwrap();
        }
        let database = root
            .path()
            .join("profile/data/com.seradotcom.motionwright/motionwright.sqlite3");
        (root, database)
    }

    fn prepared() -> (tempfile::TempDir, PathBuf, Before) {
        let (root, database) = inputs();
        seed(&database, root.path()).unwrap();
        let (raw, _) = read_file(
            root.path(),
            &root.path().join("evidence").join(BEFORE),
            MAX_WITNESS_BYTES,
        )
        .unwrap();
        let before: Before = serde_json::from_slice(&raw).unwrap();
        (root, database, before)
    }

    fn apply_for_source_control(database: &Path, change: &Change, count: usize) {
        // This intentionally uses the real service only to control the reader's
        // inputs. It makes no claim that a Tauri OwnerInvocation was exercised.
        let service = StudioService::open(database).unwrap();
        let mut project = service.projects(2).unwrap().remove(0);
        for index in 0..count {
            project = service
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("tauri-audio-reader-source-control-{index}"),
                    change,
                )
                .unwrap()
                .project;
        }
        service.prepare_read_only_handoff().unwrap();
        drop(service);
    }

    #[test]
    fn preparation_stops_before_recipe_and_reader_accepts_exactly_one_save() {
        let (root, database, before) = prepared();
        assert!(before.project.production_design.delivery_recipes.is_empty());
        assert_eq!(before.preparation_tauri_owner_invocation, "NOT_EXERCISED");
        assert!(!root.path().join("evidence/native-audition").exists());
        assert_eq!(before.assets.len(), 3);
        apply_for_source_control(&database, &before.expected_change, 1);

        let reader = StudioServiceReader::open(&database).unwrap();
        let observed = reader.projects(2).unwrap().remove(0);
        let history = reader.project_history(observed.id, 0, 64).unwrap();
        let mut foreign = observed.clone();
        foreign.title = "An unjournaled replacement".into();
        assert!(verify_single_save(&before, &foreign, &history).is_err());
        let mut rewritten = history.clone();
        rewritten[0].created_at = "rewritten prior history".into();
        assert!(verify_single_save(&before, &observed, &rewritten).is_err());
        drop(reader);

        readback(&database, root.path()).unwrap();
        let (raw, _) = read_file(
            root.path(),
            &root.path().join("evidence").join(READBACK),
            MAX_WITNESS_BYTES,
        )
        .unwrap();
        let receipt: serde_json::Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(receipt["exactly_one_recipe_save"], true);
        assert_eq!(receipt["additional_journal_events"], 0);
        assert_eq!(receipt["same_physical_database"], true);
        assert_eq!(receipt["same_physical_cas_root"], true);
        assert_eq!(receipt["verified_original_bytes"], 3 * WAV_BYTES);
        assert_eq!(receipt["reader_authority"], "StudioServiceReader");
        assert_eq!(
            receipt["tauri_owner_invocation"],
            "REQUIRES_EXTERNAL_UI_AND_CAPTURE_JOIN"
        );
        assert_eq!(receipt["mixed_pcm_read_or_generated_by_reader"], false);
        assert_eq!(receipt["formal_G2"], false);
        assert_eq!(receipt["formal_G3"], false);
        assert!(readback(&database, root.path()).is_err());
    }

    #[test]
    fn reader_rejects_missing_extra_or_different_recipe_save() {
        for selection in ["missing", "extra", "different"] {
            let (root, database, before) = prepared();
            let mut change = before.expected_change.clone();
            if selection == "different" {
                let Change::SetDeliveryRecipe {
                    recipe: Some(recipe),
                    ..
                } = &mut change
                else {
                    panic!("expected closed fixture recipe");
                };
                recipe.original_mix.as_mut().unwrap().sfx_gain_db = -7.0;
            }
            match selection {
                "extra" => apply_for_source_control(&database, &change, 2),
                "different" => apply_for_source_control(&database, &change, 1),
                _ => {}
            }
            assert!(readback(&database, root.path()).is_err(), "{selection}");
            assert!(!root.path().join("evidence").join(READBACK).exists());
        }
    }

    #[test]
    fn reader_rejects_copied_database_or_identical_replaced_cas_leaf() {
        for replace_database in [true, false] {
            let (root, database, before) = prepared();
            apply_for_source_control(&database, &before.expected_change, 1);
            let target = if replace_database {
                database.clone()
            } else {
                before.assets[0].cas.path.clone()
            };
            let retained = root.path().join("displaced-original");
            fs::rename(&target, &retained).unwrap();
            fs::copy(&retained, &target).unwrap();
            assert!(readback(&database, root.path()).is_err());
            assert!(!root.path().join("evidence").join(READBACK).exists());
        }
    }

    #[test]
    fn reader_rejects_changed_original_source_after_the_ui_save() {
        let (root, database, before) = prepared();
        apply_for_source_control(&database, &before.expected_change, 1);
        let source = &before.assets[0].source.path;
        let mut bytes = fs::read(source).unwrap();
        bytes[44] ^= 1;
        fs::write(source, bytes).unwrap();
        assert!(readback(&database, root.path()).is_err());
        assert!(!root.path().join("evidence").join(READBACK).exists());
    }

    #[test]
    fn preparation_rejects_linked_oversized_or_foreign_inputs_before_store_creation() {
        for selection in ["symlink", "hardlink", "oversized", "foreign-database"] {
            let (root, mut database) = inputs();
            let source = root.path().join("sources/saved-mix-voice.wav");
            if selection == "symlink" {
                let original = root.path().join("retained-source.wav");
                fs::rename(&source, &original).unwrap();
                symlink(&original, &source).unwrap();
            } else if selection == "hardlink" {
                fs::hard_link(&source, root.path().join("retained-source.wav")).unwrap();
            } else if selection == "oversized" {
                let file = OpenOptions::new().write(true).open(&source).unwrap();
                file.set_len(WAV_BYTES as u64 + 1).unwrap();
            } else {
                database = root.path().join("foreign/motionwright.sqlite3");
            }
            assert!(seed(&database, root.path()).is_err(), "{selection}");
            assert!(!database.exists());
            assert!(!root.path().join("evidence").exists());
        }
    }

    #[test]
    fn namespace_allows_ancestor_content_change_and_rejects_replacement() {
        let root = private_test_root();
        let parent = root.path().join("parent");
        new_private_directory(&parent).unwrap();
        let file = parent.join("original.json");
        super::super::write_new(&file, b"{}").unwrap();
        let (_, before) = read_file(root.path(), &file, 2).unwrap();
        super::super::write_new(&parent.join("another.json"), b"[]").unwrap();
        let (_, unchanged) = read_file(root.path(), &file, 2).unwrap();
        assert_eq!(before, unchanged);
        fs::rename(&parent, root.path().join("old-parent")).unwrap();
        new_private_directory(&parent).unwrap();
        super::super::write_new(&file, b"{}").unwrap();
        let (_, replaced) = read_file(root.path(), &file, 2).unwrap();
        assert_ne!(before, replaced);
        assert!(require_namespace(&before.namespace).is_err());
    }
}
