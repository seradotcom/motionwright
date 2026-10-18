//! Preserve an attached GLB as an opaque original through SQLite and portable CAS.
//!
//! Run with no arguments for a synthetic source, or pass
//! --source original.glb --work NEW_DIRECTORY. The default work directory is a
//! new UUID-named child of the current directory. Existing work is never reused.
//!
//! This example records no native format inspection, editor support or render.
//! Source shape, unknown chunks and native fidelity remain the responsibility of
//! the renderer boundary. The synthetic writer below is not an input parser.
//! Tests in this same example run with cargo test --all-targets or
//! cargo test -p motionwright-storage --example attached_native_glb_roundtrip.

use motionwright_domain::{
    Asset, Change, DomainError, Fidelity, FidelityReport, NativeCapsule, Project,
};
use motionwright_storage::{ProjectBundleManifest, ProjectEvent, Result, StorageError, Store};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    env,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

// This bounds the example's evidence/readback allocation, not the storage format.
const MAX_EXAMPLE_SOURCE_BYTES: u64 = 32 * 1024 * 1024;
const UNKNOWN_CHUNK_BYTES: &[u8] = &[
    0xde, 0xad, 0xbe, 0xef, 0, 0xff, 0x80, 0x7f, b'o', b'p', b'a', b'q', b'u', b'e', 0, 1,
];

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn digest_json(value: &impl Serialize) -> Result<String> {
    Ok(digest(&serde_json::to_vec(value)?))
}

fn require(condition: bool, message: &str) -> Result<()> {
    if !condition {
        return Err(StorageError::InvalidBackup(message.into()));
    }
    Ok(())
}

// Fixed synthetic output follows the GLB 2.0 header/chunk layout:
// https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#glb-file-format-specification
// JSON is first, BIN second, then one opaque extension chunk. We never parse or
// reinterpret a caller's source file, including that extension payload.
fn synthetic_glb() -> Vec<u8> {
    fn chunk(output: &mut Vec<u8>, kind: &[u8; 4], bytes: &[u8], padding: u8) {
        let mut aligned = bytes.to_vec();
        while aligned.len() % 4 != 0 {
            aligned.push(padding);
        }
        output.extend_from_slice(&(aligned.len() as u32).to_le_bytes());
        output.extend_from_slice(kind);
        output.extend_from_slice(&aligned);
    }
    let json = br#"{"asset":{"version":"2.0","generator":"Motionwright storage fixture"},"scene":0,"scenes":[{"nodes":[0]}],"nodes":[{"name":"Original source"}],"buffers":[{"byteLength":4}]}"#;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"glTF");
    bytes.extend_from_slice(&2_u32.to_le_bytes());
    bytes.extend_from_slice(&0_u32.to_le_bytes());
    chunk(&mut bytes, b"JSON", json, b' ');
    chunk(&mut bytes, b"BIN\0", &[0, 1, 2, 3], 0);
    chunk(&mut bytes, b"MWUX", UNKNOWN_CHUNK_BYTES, 0);
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn original_bytes(path: &Path) -> Result<Vec<u8>> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(StorageError::UnsafeSourcePath(
            "the example source must be a regular non-symlink file".into(),
        ));
    }
    if metadata.len() > MAX_EXAMPLE_SOURCE_BYTES {
        return Err(StorageError::BlobTooLarge {
            size: metadata.len(),
            limit: MAX_EXAMPLE_SOURCE_BYTES,
        });
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(MAX_EXAMPLE_SOURCE_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_EXAMPLE_SOURCE_BYTES {
        return Err(StorageError::BlobTooLarge {
            size: bytes.len() as u64,
            limit: MAX_EXAMPLE_SOURCE_BYTES,
        });
    }
    Ok(bytes)
}

fn empty_destination(store: &Store) -> Result<()> {
    require(
        store.list_projects(100)?.is_empty(),
        "destination contains a Project",
    )?;
    require(
        fs::read_dir(store.blob_root())?.next().is_none(),
        "destination CAS is not empty before import",
    )
}

fn apply(store: &mut Store, project: &Project, key: &str, change: Change) -> Result<Project> {
    let outcome = store.apply(project.id, &project.stamp(), key, &change)?;
    require(
        !outcome.replayed,
        "new example operation unexpectedly replayed",
    )?;
    Ok(outcome.project)
}

struct AttachedBundle {
    store: Store,
    project: Project,
    original_path: PathBuf,
    bytes: Vec<u8>,
    bundle: PathBuf,
    manifest: ProjectBundleManifest,
    history: Vec<ProjectEvent>,
}

fn prepare_bundle(work: &Path, provided_source: Option<&Path>) -> Result<AttachedBundle> {
    // Refuse an existing file, directory or symlink before opening any Store.
    // The parent must already exist; this example owns only this new directory.
    fs::create_dir(work)?;
    let original_path = match provided_source {
        Some(path) => path.to_path_buf(),
        None => {
            let path = work.join("original.glb");
            let mut output = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)?;
            output.write_all(&synthetic_glb())?;
            output.sync_all()?;
            path
        }
    };
    let mut store =
        Store::open_with_blob_root(work.join("source/state.sqlite3"), work.join("source/cas"))?;
    // Reuse the existing bounded capture, then the public CAS ingest. No
    // unbounded external read or private alternative storage path is introduced.
    let captured = store.stage_file_import_with_limit(&original_path, MAX_EXAMPLE_SOURCE_BYTES)?;
    let descriptor = store.ingest_blob_file(captured.path())?;
    require(
        descriptor == *captured.descriptor(),
        "captured original changed before CAS ingest",
    )?;
    drop(captured);
    let bytes = store.read_blob(&descriptor.sha256, MAX_EXAMPLE_SOURCE_BYTES)?;
    require(
        original_bytes(&original_path)? == bytes,
        "original source changed during capture",
    )?;

    let project = store.create_named_project("Attached GLB storage roundtrip")?;
    let source_asset = Asset {
        id: Uuid::now_v7(),
        name: "attached-source.glb".into(),
        media_type: "model/gltf-binary".into(),
        content_sha256: Some(descriptor.sha256.clone()),
        source_revision: None,
    };
    let project = apply(
        &mut store,
        &project,
        "attach-original-source",
        Change::AddAsset {
            asset: source_asset.clone(),
        },
    )?;
    let project = apply(
        &mut store,
        &project,
        "create-source-scene",
        Change::AddScene {
            name: "Attached original".into(),
            objective: "Preserve the original source without native execution".into(),
            duration_seconds: 1,
        },
    )?;
    let scene_id = project
        .scenes
        .last()
        .ok_or_else(|| StorageError::InvalidBackup("the source scene was not persisted".into()))?
        .id;
    let capsule = NativeCapsule {
        id: Uuid::now_v7(),
        scene_id,
        source_asset_id: source_asset.id,
        source_sha256: descriptor.sha256.clone(),
        label: "Original GLB attachment".into(),
        fidelity: FidelityReport {
            renderer: "unobserved".into(),
            renderer_version: "unobserved".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Unavailable,
            editable: Fidelity::Unavailable,
            losses: vec![
                "Native format, editability and rendering have not been inspected.".into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: Vec::new(),
        native_editor_hint: "External GLB editor (not launched)".into(),
        property_fidelity: Vec::new(),
    };
    let project = apply(
        &mut store,
        &project,
        "bind-source-capsule",
        Change::UpsertNativeCapsule { capsule },
    )?;
    let history = store.event_records_since(project.id, 0, 256)?;
    require(
        history.len() == 3 && project.revision == 3,
        "attachment journal differs",
    )?;
    let bundle = work.join("bundle-original");
    let manifest = store.export_project_bundle_at(project.id, &project.stamp(), &bundle)?;
    require(
        manifest.backup.project == project,
        "export changed the Project",
    )?;
    require(
        manifest.backup.events == history,
        "export changed the journal",
    )?;
    require(
        manifest.blobs.len() == 1
            && manifest.blobs[0].sha256 == descriptor.sha256
            && manifest.blobs[0].size_bytes == descriptor.size_bytes,
        "export did not include the exact original source",
    )?;
    require(
        original_bytes(&bundle.join(&manifest.blobs[0].relative_path))? == bytes,
        "export changed original source bytes",
    )?;
    Ok(AttachedBundle {
        store,
        project,
        original_path,
        bytes,
        bundle,
        manifest,
        history,
    })
}

fn roundtrip(work: &Path, provided_source: Option<&Path>) -> Result<Value> {
    let AttachedBundle {
        store: source_store,
        project,
        original_path,
        bytes,
        bundle,
        manifest,
        history,
    } = prepare_bundle(work, provided_source)?;
    let source_db = source_store.path().to_path_buf();
    let source_cas = source_store.blob_root().to_path_buf();
    let capsule = project
        .production_design
        .capsules
        .first()
        .ok_or_else(|| {
            StorageError::InvalidBackup("the attachment capsule was not persisted".into())
        })?
        .clone();
    let source_manifest_digest = digest(&fs::read(bundle.join("manifest.json"))?);
    let source_history_digest = digest_json(&history)?;
    // Import must use the exported bundle with the source connection closed.
    drop(source_store);

    let restored_db = work.join("restored/state.sqlite3");
    let restored_cas = work.join("restored/cas");
    let mut destination = Store::open_with_blob_root(&restored_db, &restored_cas)?;
    empty_destination(&destination)?;
    require(
        fs::canonicalize(&source_cas)? != fs::canonicalize(&restored_cas)?,
        "source and destination must not share CAS",
    )?;
    let inspected = destination.inspect_project_bundle_with_fingerprint(&bundle)?;
    require(
        inspected.plan.project.project_id == project.id
            && inspected.plan.project.source_generation == project.generation
            && inspected.plan.project.revision == project.revision
            && inspected.plan.project.event_count == history.len()
            && inspected.plan.blob_count == 1
            && inspected.plan.total_blob_bytes == bytes.len() as u64,
        "inspection did not describe the captured Project and original source",
    )?;
    let imported = destination.import_project_bundle_inspected(&bundle, &inspected.fingerprint)?;
    require(
        imported.generation != project.generation,
        "restore retained source generation",
    )?;
    let mut expected = project.clone();
    expected.generation = imported.generation;
    expected.updated_at = imported.updated_at;
    require(
        imported == expected,
        "restore changed logical Project state",
    )?;
    require(
        destination.event_records_since(imported.id, 0, 256)? == history,
        "restore changed retained journal entries",
    )?;
    require(
        destination.read_blob(&capsule.source_sha256, MAX_EXAMPLE_SOURCE_BYTES)? == bytes,
        "restored CAS differs from the original bytes",
    )?;
    drop(destination);

    let mut reopened = Store::open_with_blob_root(&restored_db, &restored_cas)?;
    require(
        reopened.load_project(imported.id)? == imported,
        "reopen changed the imported Project",
    )?;
    require(
        reopened.event_records_since(imported.id, 0, 256)? == history,
        "reopen changed retained journal entries",
    )?;
    require(
        reopened.read_blob(&capsule.source_sha256, MAX_EXAMPLE_SOURCE_BYTES)? == bytes,
        "reopen lost the independently restored source",
    )?;
    // Rotation must also prevent a write from reusing the original authority.
    match reopened.apply(
        imported.id,
        &project.stamp(),
        "rejected-original-generation",
        &Change::RenameProject {
            title: "This stale write must fail".into(),
        },
    ) {
        Err(StorageError::GenerationConflict) => {}
        Err(error) => return Err(error),
        Ok(_) => {
            return Err(StorageError::InvalidBackup(
                "restored Project admitted the source generation".into(),
            ));
        }
    }
    require(
        reopened.load_project(imported.id)? == imported,
        "rejected write changed the Project",
    )?;
    let change = Change::UpdateSceneObjective {
        scene_id: capsule.scene_id,
        objective: "Revised scene intent while preserving the attached original".into(),
    };
    let edited = apply(
        &mut reopened,
        &imported,
        "edit-restored-scene",
        change.clone(),
    )?;
    require(
        edited.revision == imported.revision + 1
            && edited.assets == project.assets
            && edited.production_design.capsules == project.production_design.capsules,
        "legal edit changed the original asset or capsule binding",
    )?;
    let edited_history = reopened.event_records_since(edited.id, 0, 256)?;
    require(
        edited_history.len() == history.len() + 1
            && edited_history.starts_with(&history)
            && edited_history
                .last()
                .is_some_and(|event| event.revision == edited.revision && event.change == change),
        "legal edit did not append exactly one journal event",
    )?;
    let edited_bundle = work.join("bundle-edited");
    let edited_manifest =
        reopened.export_project_bundle_at(edited.id, &edited.stamp(), &edited_bundle)?;
    require(
        edited_manifest.backup.project == edited
            && edited_manifest.backup.events == edited_history
            && edited_manifest.blobs == manifest.blobs,
        "edited export changed the preserved source descriptor or journal",
    )?;
    require(
        original_bytes(&edited_bundle.join(&edited_manifest.blobs[0].relative_path))? == bytes,
        "edited export rewrote the original source bytes",
    )?;
    drop(reopened);

    let final_destination = Store::open_with_blob_root(&restored_db, &restored_cas)?;
    require(
        final_destination.load_project(edited.id)? == edited,
        "the edit did not survive reopen",
    )?;
    require(
        final_destination.event_records_since(edited.id, 0, 256)? == edited_history,
        "the edited journal did not survive reopen",
    )?;
    require(
        final_destination.read_blob(&capsule.source_sha256, MAX_EXAMPLE_SOURCE_BYTES)? == bytes,
        "editing changed the independently restored CAS source",
    )?;
    let final_descriptor = final_destination.verify_blob(&capsule.source_sha256)?;
    require(
        final_descriptor.sha256 == digest(&bytes)
            && final_descriptor.size_bytes == bytes.len() as u64,
        "final CAS descriptor does not describe the retained bytes",
    )?;
    drop(final_destination);

    let original_store = Store::open_with_blob_root(&source_db, &source_cas)?;
    require(
        original_store.load_project(project.id)? == project,
        "restore or edit changed the source Project",
    )?;
    require(
        original_store.event_records_since(project.id, 0, 256)? == history,
        "restore or edit changed the source journal",
    )?;
    require(
        original_store.read_blob(&capsule.source_sha256, MAX_EXAMPLE_SOURCE_BYTES)? == bytes
            && original_bytes(&original_path)? == bytes
            && original_bytes(&bundle.join(&manifest.blobs[0].relative_path))? == bytes,
        "the original file, original CAS or original bundle was rewritten",
    )?;
    require(
        digest(&fs::read(bundle.join("manifest.json"))?) == source_manifest_digest,
        "restoring or editing replaced the original manifest",
    )?;
    drop(original_store);

    let known_unknown_chunk_preserved = if provided_source.is_none() {
        require(
            bytes.ends_with(UNKNOWN_CHUNK_BYTES),
            "synthetic unknown payload is absent",
        )?;
        Some(true)
    } else {
        // Opaque byte equality above does not identify a caller's chunk types.
        None
    };
    let evidence = json!({
        "schema": "motionwright.attached-glb-storage-roundtrip/1",
        "suite_source_sha256": digest(include_bytes!("attached_native_glb_roundtrip.rs")),
        "compiled_app_sha_if_supplied": option_env!("MOTIONWRIGHT_APP_SHA").filter(|sha| {
            sha.len() == 40
                && sha.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        }),
        "storage_package_version": env!("CARGO_PKG_VERSION"),
        "workspace": work.to_string_lossy(),
        "source_kind": if provided_source.is_some() { "provided_opaque_file" } else { "synthetic_glb" },
        "project_id": project.id,
        "source_generation": project.generation,
        "restored_generation": imported.generation,
        "imported_revision": imported.revision,
        "edited_revision": edited.revision,
        "scene_id": capsule.scene_id,
        "source_asset_id": capsule.source_asset_id,
        "capsule_id": capsule.id,
        "source_sha256": final_descriptor.sha256,
        "source_size_bytes": final_descriptor.size_bytes,
        "original_manifest_sha256": source_manifest_digest,
        "original_inspection_fingerprint": inspected.fingerprint.as_str(),
        "original_history_sha256": source_history_digest,
        "edited_history_sha256": digest_json(&edited_history)?,
        "logical_project_and_ids_preserved": true,
        "journal_preserved_before_edit": true,
        "old_generation_write_rejected": true,
        "source_and_cas_byte_equal_after_reopen_and_edit": true,
        "known_synthetic_unknown_chunk_preserved": known_unknown_chunk_preserved,
        "native_format_inspection": "not_run",
        "native_editor_readback": "not_run",
        "native_render": "not_run",
        "artifacts": {
            "source_database": "source/state.sqlite3",
            "source_cas": "source/cas",
            "restored_database": "restored/state.sqlite3",
            "restored_cas": "restored/cas",
            "original_bundle": "bundle-original",
            "edited_bundle": "bundle-edited"
        }
    });
    // This is an evidence artifact, not an operation or authorization receipt.
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(work.join("roundtrip-evidence.json"))?;
    output.write_all(&serde_json::to_vec_pretty(&evidence)?)?;
    output.write_all(b"\n")?;
    output.sync_all()?;
    Ok(evidence)
}

#[derive(Default)]
struct Options {
    source: Option<PathBuf>,
    work: Option<PathBuf>,
}

fn arguments(args: impl IntoIterator<Item = OsString>) -> Result<Option<Options>> {
    let invalid = || {
        StorageError::Domain(DomainError::Invalid(
            "use [--source FILE] [--work NEW_DIRECTORY]; both options require one value".into(),
        ))
    };
    let mut options = Options::default();
    let mut args = args.into_iter();
    while let Some(argument) = args.next() {
        let target = match argument.to_str() {
            Some("--help" | "-h") => return Ok(None),
            Some("--source") => &mut options.source,
            Some("--work") => &mut options.work,
            _ => return Err(invalid()),
        };
        if target.is_some() {
            return Err(invalid());
        }
        let value = args.next().ok_or_else(invalid)?;
        if value.is_empty() || value.to_str().is_some_and(|text| text.starts_with("--")) {
            return Err(invalid());
        }
        *target = Some(PathBuf::from(value));
    }
    Ok(Some(options))
}

fn main() -> Result<()> {
    let Some(options) = arguments(env::args_os().skip(1))? else {
        println!(
            "attached_native_glb_roundtrip [--source FILE] [--work NEW_DIRECTORY]\n\
             Without --source, generate a small GLB with an unknown chunk.\n\
             Sources are retained as opaque bytes, with no native format or render claim.\n\
             Source readback is limited to 32 MiB for this example.\n\
             --work must not exist and its parent must exist; the default is a new\n\
             UUID-named child of the current directory. Failed work is retained."
        );
        return Ok(());
    };
    let work = match options.work {
        Some(path) => path,
        None => env::current_dir()?.join(format!("attached-glb-roundtrip-{}", Uuid::now_v7())),
    };
    let evidence = roundtrip(&work, options.source.as_deref())?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(root: &Path) -> AttachedBundle {
        prepare_bundle(&root.join("producer"), None).unwrap()
    }

    fn destination(root: &Path) -> Store {
        Store::open_with_blob_root(
            root.join("consumer/state.sqlite3"),
            root.join("consumer/cas"),
        )
        .unwrap()
    }

    fn missing<T>(result: Result<T>) {
        assert!(matches!(
            result,
            Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound
        ));
    }

    fn invalid_backup<T>(result: Result<T>) {
        assert!(matches!(result, Err(StorageError::InvalidBackup(_))));
    }

    fn untouched_destination(store: &Store, project_id: Uuid) {
        empty_destination(store).unwrap();
        assert!(matches!(
            store.load_project(project_id),
            Err(StorageError::NotFound)
        ));
        assert!(
            store
                .event_records_since(project_id, 0, 256)
                .unwrap()
                .is_empty()
        );
    }

    fn original_source_unchanged(fixture: &AttachedBundle) {
        assert_eq!(
            original_bytes(&fixture.original_path).unwrap(),
            fixture.bytes
        );
        assert_eq!(
            fixture
                .store
                .read_blob(&fixture.manifest.blobs[0].sha256, MAX_EXAMPLE_SOURCE_BYTES)
                .unwrap(),
            fixture.bytes,
        );
        assert_eq!(
            fixture.store.load_project(fixture.project.id).unwrap(),
            fixture.project
        );
        assert_eq!(
            fixture
                .store
                .event_records_since(fixture.project.id, 0, 256)
                .unwrap(),
            fixture.history,
        );
    }

    #[test]
    fn original_unknown_chunk_survives_import_reopen_and_legal_edit() {
        let temp = tempfile::tempdir().unwrap();
        let work = temp.path().join("roundtrip");
        let original = synthetic_glb();
        assert_eq!(original.len(), 228);
        assert_eq!(
            digest(&original),
            "77e3c6aabc18a12cb39b3809d7eb26f0f31c982b0911ddb33f5f81521d8bf594",
        );
        assert!(original.ends_with(UNKNOWN_CHUNK_BYTES));

        let evidence = roundtrip(&work, None).unwrap();
        assert_eq!(evidence["source_sha256"], digest(&original));
        assert_eq!(evidence["source_size_bytes"], 228);
        assert_eq!(evidence["known_synthetic_unknown_chunk_preserved"], true);
        assert_ne!(
            evidence["source_generation"],
            evidence["restored_generation"]
        );
        assert_eq!(evidence["imported_revision"], 3);
        assert_eq!(evidence["edited_revision"], 4);
        assert_eq!(evidence["native_format_inspection"], "not_run");
        assert_eq!(evidence["native_editor_readback"], "not_run");
        assert_eq!(evidence["native_render"], "not_run");
        let saved: Value =
            serde_json::from_slice(&fs::read(work.join("roundtrip-evidence.json")).unwrap())
                .unwrap();
        assert_eq!(saved, evidence);
        assert_eq!(fs::read(work.join("original.glb")).unwrap(), original);

        let restored = Store::open_with_blob_root(
            work.join("restored/state.sqlite3"),
            work.join("restored/cas"),
        )
        .unwrap();
        let project_id: Uuid = serde_json::from_value(evidence["project_id"].clone()).unwrap();
        let project = restored.load_project(project_id).unwrap();
        let capsule = &project.production_design.capsules[0];
        assert_eq!(capsule.source_sha256, digest(&original));
        assert_eq!(project.assets[0].id, capsule.source_asset_id);
        assert_eq!(project.scenes[0].id, capsule.scene_id);
        assert!(capsule.editable_parameters.is_empty());
        assert!(capsule.property_fidelity.is_empty());
        assert!(capsule.fidelity.evidence_sha256.is_none());
        assert!(!capsule.fidelity.usable_without_loss_consent());
        assert_eq!(
            restored
                .read_blob(&capsule.source_sha256, MAX_EXAMPLE_SOURCE_BYTES)
                .unwrap(),
            original,
        );
    }

    #[test]
    fn provided_original_is_preserved_without_substituting_the_synthetic_fixture() {
        let temp = tempfile::tempdir().unwrap();
        let original_path = temp.path().join("caller original.glb");
        let work = temp.path().join("provided source run");
        let mut original = synthetic_glb();
        *original.last_mut().unwrap() ^= 0x80;
        fs::write(&original_path, &original).unwrap();
        let options = arguments([
            OsString::from("--source"),
            original_path.as_os_str().to_os_string(),
            OsString::from("--work"),
            work.as_os_str().to_os_string(),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(options.source.as_deref(), Some(original_path.as_path()));
        assert_eq!(options.work.as_deref(), Some(work.as_path()));

        let evidence =
            roundtrip(options.work.as_deref().unwrap(), options.source.as_deref()).unwrap();
        assert_eq!(evidence["source_kind"], "provided_opaque_file");
        assert_eq!(evidence["source_sha256"], digest(&original));
        assert!(evidence["known_synthetic_unknown_chunk_preserved"].is_null());
        assert_eq!(fs::read(&original_path).unwrap(), original);
        assert!(!work.join("original.glb").exists());
        let restored = Store::open_with_blob_root(
            work.join("restored/state.sqlite3"),
            work.join("restored/cas"),
        )
        .unwrap();
        assert_eq!(
            restored
                .read_blob(&digest(&original), MAX_EXAMPLE_SOURCE_BYTES)
                .unwrap(),
            original,
        );
    }

    #[test]
    fn missing_member_refuses_inspection_and_import_before_publication() {
        let temp = tempfile::tempdir().unwrap();
        let fixture = fixture(temp.path());
        let mut destination = destination(temp.path());
        let inspected = destination
            .inspect_project_bundle_with_fingerprint(&fixture.bundle)
            .unwrap();
        let manifest_before = fs::read(fixture.bundle.join("manifest.json")).unwrap();
        fs::remove_file(
            fixture
                .bundle
                .join(&fixture.manifest.blobs[0].relative_path),
        )
        .unwrap();

        missing(destination.inspect_project_bundle(&fixture.bundle));
        missing(destination.inspect_project_bundle_with_fingerprint(&fixture.bundle));
        missing(destination.import_project_bundle(&fixture.bundle));
        untouched_destination(&destination, fixture.project.id);
        missing(
            destination.import_project_bundle_inspected(&fixture.bundle, &inspected.fingerprint),
        );
        untouched_destination(&destination, fixture.project.id);
        assert_eq!(
            fs::read(fixture.bundle.join("manifest.json")).unwrap(),
            manifest_before
        );
        original_source_unchanged(&fixture);
    }

    #[test]
    fn same_size_member_tamper_refuses_inspection_and_import_before_publication() {
        let temp = tempfile::tempdir().unwrap();
        let fixture = fixture(temp.path());
        let mut destination = destination(temp.path());
        let inspected = destination
            .inspect_project_bundle_with_fingerprint(&fixture.bundle)
            .unwrap();
        let manifest_before = fs::read(fixture.bundle.join("manifest.json")).unwrap();
        let member = fixture
            .bundle
            .join(&fixture.manifest.blobs[0].relative_path);
        let mut changed = fs::read(&member).unwrap();
        *changed.last_mut().unwrap() ^= 0x80;
        assert_eq!(changed.len(), fixture.bytes.len());
        assert_ne!(digest(&changed), fixture.manifest.blobs[0].sha256);
        fs::write(&member, &changed).unwrap();

        invalid_backup(destination.inspect_project_bundle(&fixture.bundle));
        invalid_backup(destination.inspect_project_bundle_with_fingerprint(&fixture.bundle));
        invalid_backup(destination.import_project_bundle(&fixture.bundle));
        untouched_destination(&destination, fixture.project.id);
        invalid_backup(
            destination.import_project_bundle_inspected(&fixture.bundle, &inspected.fingerprint),
        );
        untouched_destination(&destination, fixture.project.id);
        assert_eq!(fs::read(&member).unwrap(), changed);
        assert_eq!(
            fs::read(fixture.bundle.join("manifest.json")).unwrap(),
            manifest_before
        );
        original_source_unchanged(&fixture);
    }

    #[test]
    fn replaced_valid_bundle_rejects_the_original_inspection_fingerprint() {
        let temp = tempfile::tempdir().unwrap();
        let mut fixture = fixture(temp.path());
        let mut destination = destination(temp.path());
        let inspected = destination
            .inspect_project_bundle_with_fingerprint(&fixture.bundle)
            .unwrap();
        let original_manifest = fs::read(fixture.bundle.join("manifest.json")).unwrap();
        let original_project = fixture.project.clone();
        let replacement_project = apply(
            &mut fixture.store,
            &original_project,
            "create-distinct-valid-candidate",
            Change::RenameProject {
                title: "A different valid export candidate".into(),
            },
        )
        .unwrap();
        let replacement = temp.path().join("replacement-bundle");
        fixture
            .store
            .export_project_bundle_at(
                replacement_project.id,
                &replacement_project.stamp(),
                &replacement,
            )
            .unwrap();
        let replacement_manifest = fs::read(replacement.join("manifest.json")).unwrap();
        assert_ne!(original_manifest, replacement_manifest);
        fs::write(fixture.bundle.join("manifest.json"), &replacement_manifest).unwrap();

        // This positive inspection proves the replacement is independently
        // admissible; it does not authorize using the original fingerprint.
        let current = destination
            .inspect_project_bundle_with_fingerprint(&fixture.bundle)
            .unwrap();
        assert_ne!(current.fingerprint, inspected.fingerprint);
        assert_eq!(current.plan.project.revision, replacement_project.revision);
        invalid_backup(
            destination.import_project_bundle_inspected(&fixture.bundle, &inspected.fingerprint),
        );
        untouched_destination(&destination, fixture.project.id);
        assert_eq!(
            fixture.store.load_project(original_project.id).unwrap(),
            replacement_project,
        );
        assert_eq!(
            original_bytes(&fixture.original_path).unwrap(),
            fixture.bytes
        );
        assert_eq!(
            fs::read(fixture.bundle.join("manifest.json")).unwrap(),
            replacement_manifest,
        );
    }

    #[test]
    fn explicit_missing_source_is_an_error_and_never_falls_back_to_a_fixture() {
        let temp = tempfile::tempdir().unwrap();
        let work = temp.path().join("missing-source");
        let absent = temp.path().join("absent.glb");
        missing(roundtrip(&work, Some(&absent)));
        assert!(!absent.exists());
        assert!(!work.join("original.glb").exists());
        assert!(!work.join("roundtrip-evidence.json").exists());
        let store =
            Store::open_with_blob_root(work.join("source/state.sqlite3"), work.join("source/cas"))
                .unwrap();
        empty_destination(&store).unwrap();
        assert!(arguments([OsString::from("--source")]).is_err());
    }
}
