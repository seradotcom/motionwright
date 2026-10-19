use motionwright_domain::{Asset, Change, Project};
use motionwright_service::StudioService;
use motionwright_storage::{BundleFingerprint, ProjectBackup, ProjectEvent, StorageError};
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::Path};

// Constructed canaries exercise the existing credential patterns. None are
// credentials; negative result messages must not echo the matching bytes.
fn canary() -> String {
    format!("ghp_{}", "A".repeat(24))
}

fn service(root: &Path, name: &str) -> StudioService {
    StudioService::open(root.join(name).join("state.sqlite3")).unwrap()
}

fn renamed(service: &StudioService, project: &Project, key: &str, title: &str) -> Project {
    service
        .apply(
            project.id,
            &project.stamp(),
            key,
            &Change::RenameProject {
                title: title.into(),
            },
        )
        .unwrap()
        .project
}

fn rejected<T>(result: Result<T, StorageError>, marker: &str) {
    let error = match result {
        Err(error) => error,
        Ok(_) => panic!("portable boundary returned credential-bearing fixture as success"),
    };
    assert!(matches!(&error, StorageError::InvalidBackup(_)));
    assert!(!error.to_string().contains(marker));
}

fn rehash(backup: &mut ProjectBackup) {
    backup.payload_sha256 = hex::encode(Sha256::digest(
        serde_json::to_vec(&(
            backup.format_version,
            backup.source_storage_schema,
            &backup.project,
            &backup.events,
        ))
        .unwrap(),
    ));
}

fn assert_empty_destination(service: &StudioService) {
    assert!(service.projects(100).unwrap().is_empty());
    let store = service.store();
    let store = store.lock();
    assert!(fs::read_dir(store.blob_root()).unwrap().next().is_none());
}

fn add_asset(
    service: &StudioService,
    project: &Project,
    path: &Path,
    name: &str,
    media_type: &str,
) -> Project {
    service
        .import_asset_file(
            project.id,
            &project.stamp(),
            &format!("import-{name}"),
            path,
            name.into(),
            media_type.into(),
        )
        .unwrap()
        .project
}

// Fixture-only seed of a project that already held these bytes before the
// current admission policy. Store::apply verifies the digest and creates the
// real coherent journal/receipt; no insecure import API or SQL shortcut is
// needed. The export tests reopen this state before exercising the boundary.
fn add_legacy_asset(
    service: &StudioService,
    project: &Project,
    source: &Path,
    name: &str,
    media_type: &str,
) -> Project {
    let bytes = fs::read(source).unwrap();
    let digest = hex::encode(Sha256::digest(&bytes));
    let shared = service.store();
    let mut store = shared.lock();
    let leaf = store
        .blob_root()
        .join("sha256")
        .join(&digest[..2])
        .join(&digest);
    fs::create_dir_all(leaf.parent().unwrap()).unwrap();
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut output = options.open(&leaf).unwrap();
    output.write_all(&bytes).unwrap();
    output.sync_all().unwrap();
    drop(output);
    assert_eq!(
        store.verify_blob(&digest).unwrap().size_bytes,
        bytes.len() as u64
    );
    store
        .apply(
            project.id,
            &project.stamp(),
            &format!("legacy-import-{name}"),
            &Change::AddAsset {
                asset: Asset {
                    id: uuid::Uuid::now_v7(),
                    name: name.into(),
                    media_type: media_type.into(),
                    content_sha256: Some(digest),
                    source_revision: Some("local-import".into()),
                },
            },
        )
        .unwrap()
        .project
}

fn reopen(service: StudioService) -> StudioService {
    let database = service.store().lock().path().to_path_buf();
    drop(service);
    StudioService::open(database).unwrap()
}

fn blob_bytes(service: &StudioService, digest: &str, length: usize) -> Vec<u8> {
    service
        .store()
        .lock()
        .read_blob(digest, length as u64)
        .unwrap()
}

fn assert_no_export_stage(root: &Path) {
    assert!(fs::read_dir(root).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".motionwright-export-")
    }));
}

#[test]
fn json_backup_export_rejects_credentials_in_current_metadata_and_retained_journal() {
    for journal_only in [false, true] {
        let temp = tempfile::tempdir().unwrap();
        let source = service(temp.path(), "source");
        let initial = source.create_project("Portable metadata").unwrap();
        let marker = canary();
        let sensitive = renamed(&source, &initial, "sensitive-title", &marker);
        let before = if journal_only {
            renamed(
                &source,
                &sensitive,
                "safe-current-title",
                "Current title is safe",
            )
        } else {
            sensitive.clone()
        };
        let history = source.history(before.id, 0, 100).unwrap();

        rejected(source.export_project(before.id), &marker);
        rejected(
            source.export_project_at(before.id, &before.stamp()),
            &marker,
        );

        // Portable rejection must not invalidate the already committed receipt.
        let replay = source
            .apply(
                initial.id,
                &initial.stamp(),
                "sensitive-title",
                &Change::RenameProject {
                    title: marker.clone(),
                },
            )
            .unwrap();
        assert!(replay.replayed);
        assert_eq!(replay.project, sensitive);
        assert_eq!(source.project(before.id).unwrap(), before);
        assert_eq!(source.history(before.id, 0, 100).unwrap(), history);
    }
}

#[test]
fn json_backup_import_rejects_sensitive_metadata_before_any_project_write() {
    for journal_only in [false, true] {
        let temp = tempfile::tempdir().unwrap();
        let source = service(temp.path(), "source");
        let initial = source.create_project("Clean original").unwrap();
        let project = if journal_only {
            renamed(&source, &initial, "clean-rename", "Clean current")
        } else {
            initial
        };
        let mut backup = source.export_project(project.id).unwrap();
        let marker = canary();
        if journal_only {
            match &mut backup.events[0].change {
                Change::RenameProject { title } => *title = marker.clone(),
                _ => unreachable!(),
            }
        } else {
            backup.project.title = marker.clone();
        }
        rehash(&mut backup);
        let encoded = serde_json::to_vec(&backup).unwrap();
        let decoded: ProjectBackup = serde_json::from_slice(&encoded).unwrap();
        let destination = service(temp.path(), "destination");

        rejected(destination.inspect_import(&decoded), &marker);
        rejected(destination.import_project(&decoded), &marker);

        assert_empty_destination(&destination);
        assert_eq!(source.project(project.id).unwrap(), project);
    }
}

#[test]
fn portable_text_credential_rejection_does_not_depend_on_declared_mime() {
    for media_type in ["application/octet-stream", "text/plain"] {
        let temp = tempfile::tempdir().unwrap();
        let source = service(temp.path(), "source");
        let project = source.create_project("Portable note").unwrap();
        let marker = canary();
        let bytes = format!("ordinary note\ncredential={marker}\n").into_bytes();
        let original = temp.path().join("notes.txt");
        fs::write(&original, &bytes).unwrap();
        let project = add_legacy_asset(&source, &project, &original, "notes.txt", media_type);
        let source = reopen(source);
        let digest = project.assets[0].content_sha256.clone().unwrap();
        let history = source.history(project.id, 0, 100).unwrap();
        let destination = temp.path().join("legacy-bundle");
        let stamped = temp.path().join("stamped-bundle");

        rejected(
            source.export_project_bundle(project.id, &destination),
            &marker,
        );
        rejected(
            source.export_project_bundle_at(project.id, &project.stamp(), &stamped),
            &marker,
        );

        assert!(!destination.exists());
        assert!(!stamped.exists());
        assert_no_export_stage(temp.path());
        assert_eq!(source.project(project.id).unwrap(), project);
        assert_eq!(source.history(project.id, 0, 100).unwrap(), history);
        assert_eq!(fs::read(&original).unwrap(), bytes);
        assert_eq!(blob_bytes(&source, &digest, bytes.len()), bytes);
    }
}

#[test]
fn bundle_inspection_and_import_scan_actual_members_before_cas_publication() {
    for media_type in ["application/octet-stream", "text/plain"] {
        let temp = tempfile::tempdir().unwrap();
        let source = service(temp.path(), "source");
        let project = source.create_project("Clean bundle producer").unwrap();
        let benign = temp.path().join("benign.bin");
        let benign_bytes = b"first member remains ordinary source bytes\n";
        fs::write(&benign, benign_bytes).unwrap();
        let project = add_asset(
            &source,
            &project,
            &benign,
            "benign.bin",
            "application/octet-stream",
        );
        let benign_digest = project.assets[0].content_sha256.clone().unwrap();
        let original = temp.path().join("notes.txt");
        fs::write(&original, b"ordinary fixture note").unwrap();
        let project = add_asset(&source, &project, &original, "notes.txt", media_type);
        let original_digest = project.assets[1].content_sha256.as_deref().unwrap();
        let bundle = temp.path().join("bundle");
        let mut manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        let target = manifest
            .blobs
            .iter()
            .position(|blob| blob.sha256 == original_digest)
            .unwrap();
        let marker = canary();
        let mut bytes = b"ordinary note\n".repeat(12 * 1024);
        assert!(bytes.len() > 128 * 1024);
        bytes.extend_from_slice(format!("fixture={marker}\n").as_bytes());
        let digest = hex::encode(Sha256::digest(&bytes));
        let old_relative = manifest.blobs[target].relative_path.clone();
        let relative = format!("blobs/sha256/{}/{}", &digest[..2], digest);
        let member = bundle.join(&relative);
        fs::create_dir_all(member.parent().unwrap()).unwrap();
        fs::write(&member, &bytes).unwrap();
        fs::remove_file(bundle.join(old_relative)).unwrap();

        manifest.backup.project.assets[1].content_sha256 = Some(digest.clone());
        match &mut manifest.backup.events[1].change {
            Change::AddAsset { asset } => asset.content_sha256 = Some(digest.clone()),
            _ => unreachable!(),
        }
        manifest.blobs[target].sha256 = digest.clone();
        manifest.blobs[target].relative_path = relative;
        manifest.blobs[target].size_bytes = bytes.len() as u64;
        // The manifest permits any unique canonical member order. Put the
        // benign member first so a late refusal cannot publish that prefix.
        manifest.blobs.sort_by_key(|blob| blob.sha256 == digest);
        assert_eq!(manifest.blobs[0].sha256, benign_digest);
        assert_eq!(manifest.blobs[1].sha256, digest);
        rehash(&mut manifest.backup);
        let encoded = serde_json::to_vec_pretty(&manifest).unwrap();
        fs::write(bundle.join("manifest.json"), &encoded).unwrap();
        let fingerprint = BundleFingerprint::from_sha256(hex::encode(
            Sha256::new()
                .chain_update(b"motionwright.bundle-inspection.v1\0")
                .chain_update(&encoded)
                .finalize(),
        ))
        .unwrap();
        let destination = service(temp.path(), "destination");

        rejected(destination.inspect_project_bundle(&bundle), &marker);
        rejected(
            destination.inspect_project_bundle_with_fingerprint(&bundle),
            &marker,
        );
        rejected(destination.import_project_bundle(&bundle), &marker);
        rejected(
            destination.import_project_bundle_inspected(&bundle, &fingerprint),
            &marker,
        );

        assert_empty_destination(&destination);
        assert_eq!(fs::read(bundle.join("manifest.json")).unwrap(), encoded);
        assert_eq!(fs::read(&member).unwrap(), bytes);
        assert_eq!(
            blob_bytes(&source, &benign_digest, benign_bytes.len()),
            benign_bytes.as_slice(),
        );
        assert_eq!(source.project(project.id).unwrap(), project);
    }
}

#[test]
fn credential_patterns_cross_portable_read_boundaries_without_mime_trust() {
    let patterns = [
        format!("ghp_{}", "A".repeat(20)),
        format!("gho_{}", "B".repeat(20)),
        format!("ghu_{}", "C".repeat(20)),
        format!("ghs_{}", "D".repeat(20)),
        format!("xoxb-{}", "E".repeat(20)),
        format!("xoxa-{}", "F".repeat(20)),
        format!("xoxp-{}", "G".repeat(20)),
        format!("xoxr-{}", "H".repeat(20)),
        format!("xoxs-{}", "I".repeat(20)),
        format!("sk-{}", "J".repeat(24)),
        format!("AKIA{}", "K".repeat(16)),
        concat!("-----BEGIN ", "PRIVATE KEY-----").to_owned(),
    ];
    for marker in patterns {
        for split in [1, marker.len() - 1] {
            let temp = tempfile::tempdir().unwrap();
            let source = service(temp.path(), "source");
            let project = source.create_project("Split marker").unwrap();
            // A full transfer buffer ends inside the marker. Opaque bytes on
            // both sides ensure this is not relying on UTF-8 or text MIME.
            let mut bytes = vec![0x80; 128 * 1024 - split];
            bytes.extend_from_slice(marker.as_bytes());
            bytes.extend_from_slice(&[0, 0xff, 0x80]);
            let original = temp.path().join("source.bin");
            fs::write(&original, &bytes).unwrap();
            let project = add_legacy_asset(
                &source,
                &project,
                &original,
                "source.bin",
                "application/octet-stream",
            );
            let source = reopen(source);
            let destination = temp.path().join("bundle");

            rejected(
                source.export_project_bundle(project.id, &destination),
                &marker,
            );

            assert!(!destination.exists());
            assert_no_export_stage(temp.path());
            assert_eq!(fs::read(&original).unwrap(), bytes);
        }
    }
}

fn pcm16_wav() -> Vec<u8> {
    let frames = 96_000_u32;
    let data_bytes = frames * 2;
    let mut bytes = Vec::with_capacity((44 + data_bytes) as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&96_000_u32.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    for index in 0..frames {
        let sample: i16 = if index % 48 < 24 { 12_000 } else { -12_000 };
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    bytes
}

#[test]
fn ordinary_text_media_and_large_opaque_bytes_roundtrip_without_rewriting() {
    let temp = tempfile::tempdir().unwrap();
    let source = service(temp.path(), "source");
    let mut project = source.create_project("Preserved source bytes").unwrap();
    let binary = (0..(8 * 1024 * 1024 + 128 * 1024))
        .map(|index| (index % 256) as u8)
        .collect::<Vec<_>>();
    let assets = [
        ("voice.wav", "audio/wav", pcm16_wav()),
        ("opaque.bin", "application/octet-stream", binary),
        (
            "notes.txt",
            "text/plain",
            b"Creative note: ghp_, sk-, and short examples remain ordinary text.\n".to_vec(),
        ),
    ];
    let mut originals = Vec::new();
    for (name, media_type, bytes) in &assets {
        let path = temp.path().join(name);
        fs::write(&path, bytes).unwrap();
        project = add_asset(&source, &project, &path, name, media_type);
        originals.push(path);
    }
    let bundle = temp.path().join("bundle");
    let manifest = source
        .export_project_bundle_at(project.id, &project.stamp(), &bundle)
        .unwrap();
    let destination = service(temp.path(), "destination");
    let inspected = destination
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    assert_eq!(inspected.plan.blob_count, assets.len());
    let imported = destination
        .import_project_bundle_inspected(&bundle, &inspected.fingerprint)
        .unwrap();
    assert_eq!(imported.id, project.id);
    assert_ne!(imported.generation, project.generation);
    let mut expected = project.clone();
    expected.generation = imported.generation;
    expected.updated_at = imported.updated_at;
    assert_eq!(imported, expected);
    drop(destination);

    let reopened = service(temp.path(), "destination");
    assert_eq!(reopened.project(imported.id).unwrap(), imported);
    for ((asset, (_, _, bytes)), path) in project.assets.iter().zip(&assets).zip(&originals) {
        let digest = asset.content_sha256.as_deref().unwrap();
        assert_eq!(blob_bytes(&reopened, digest, bytes.len()), *bytes);
        assert_eq!(fs::read(path).unwrap(), *bytes);
        let member = manifest
            .blobs
            .iter()
            .find(|blob| blob.sha256 == digest)
            .unwrap();
        assert_eq!(
            fs::read(bundle.join(&member.relative_path)).unwrap(),
            *bytes
        );
    }
    let changed = renamed(&reopened, &imported, "edit-after-reopen", "Still editable");
    assert_eq!(changed.revision, imported.revision + 1);
    assert_eq!(changed.assets, imported.assets);
}

#[test]
fn safe_json_backup_roundtrip_preserves_digest_history_and_editability() {
    let temp = tempfile::tempdir().unwrap();
    let source = service(temp.path(), "source");
    let original = source.create_project("Ordinary project").unwrap();
    let project = renamed(&source, &original, "rename-one", "Ordinary revision");
    let backup = source.export_project(project.id).unwrap();
    let stamped = source
        .export_project_at(project.id, &project.stamp())
        .unwrap();
    assert_eq!(backup.payload_sha256, stamped.payload_sha256);
    assert_eq!(backup.project, stamped.project);
    assert_eq!(backup.events, stamped.events);
    let encoded = serde_json::to_vec(&backup).unwrap();
    let decoded: ProjectBackup = serde_json::from_slice(&encoded).unwrap();
    let destination = service(temp.path(), "destination");
    assert!(destination.inspect_import(&decoded).is_ok());
    let imported = destination.import_project(&decoded).unwrap();
    assert_eq!(imported.id, project.id);
    assert_ne!(imported.generation, project.generation);
    assert_eq!(
        destination.history(imported.id, 0, 100).unwrap(),
        backup.events
    );
    drop(destination);

    let reopened = service(temp.path(), "destination");
    assert_eq!(reopened.project(imported.id).unwrap(), imported);
    let changed = renamed(&reopened, &imported, "next-edit", "Next ordinary revision");
    assert_eq!(changed.revision, imported.revision + 1);
    assert_eq!(changed.id, imported.id);
}

const BUNDLE_MANIFEST_BUDGET: u64 = 16 * 1024 * 1024;
const LARGE_HISTORY_REVISIONS: u64 = 64;

// An ordinary historical backup can exceed the bundle manifest budget using
// valid retained edits. Build sixty-four legal domain revisions, then exercise
// the actual SQLite import/export APIs; no oversized field or invalid timestamp
// is used to manufacture the size boundary.
fn large_json_history_fixture(seed: &StudioService) -> ProjectBackup {
    let original = seed.create_project("Large ordinary journal").unwrap();
    let mut backup = seed.export_project(original.id).unwrap();
    for revision in 1..=LARGE_HISTORY_REVISIONS {
        let change = Change::SetBrief {
            objective: format!("{revision:02}:{}", "o".repeat(7_997)),
            audience: "a".repeat(2_000),
            constraints: vec!["c".repeat(1_000); 128],
            exclusions: vec!["e".repeat(1_000); 128],
        };
        backup.project.apply_change(&change).unwrap();
        backup.project.commit_revision(revision, &change).unwrap();
        backup.events.push(ProjectEvent {
            revision,
            change,
            created_at: backup.project.updated_at.to_rfc3339(),
        });
    }
    backup.exported_at = backup.project.updated_at.to_rfc3339();
    backup.project.validate().unwrap();
    rehash(&mut backup);
    backup
}

#[test]
fn safe_json_backups_larger_than_bundle_budget_restore_and_reexport() {
    let temp = tempfile::tempdir().unwrap();
    let seed = service(temp.path(), "seed");
    let backup = large_json_history_fixture(&seed);
    let encoded = serde_json::to_vec(&backup).unwrap();
    assert!(encoded.len() as u64 > BUNDLE_MANIFEST_BUDGET);
    let decoded: ProjectBackup = serde_json::from_slice(&encoded).unwrap();
    drop(encoded);
    drop(backup);
    let history_digest = hex::encode(Sha256::digest(serde_json::to_vec(&decoded.events).unwrap()));
    let destination = service(temp.path(), "destination");
    let plan = destination.inspect_import(&decoded).unwrap();
    assert_eq!(plan.event_count, LARGE_HISTORY_REVISIONS as usize);
    let imported = destination.import_project(&decoded).unwrap();
    assert_eq!(imported.id, decoded.project.id);
    assert_ne!(imported.generation, decoded.project.generation);
    let mut expected = decoded.project.clone();
    expected.generation = imported.generation;
    expected.updated_at = imported.updated_at;
    assert_eq!(imported, expected);
    drop(decoded);

    let exported = destination.export_project(imported.id).unwrap();
    assert_eq!(exported.events.len(), LARGE_HISTORY_REVISIONS as usize);
    assert_eq!(
        hex::encode(Sha256::digest(
            serde_json::to_vec(&exported.events).unwrap(),
        )),
        history_digest,
    );
    let stamped = destination
        .export_project_at(imported.id, &imported.stamp())
        .unwrap();
    assert_eq!(stamped.payload_sha256, exported.payload_sha256);
    assert_eq!(stamped.events.len(), LARGE_HISTORY_REVISIONS as usize);
    drop(stamped);
    drop(exported);
    drop(destination);

    let reopened = service(temp.path(), "destination");
    assert_eq!(reopened.project(imported.id).unwrap(), imported);
    let changed = renamed(&reopened, &imported, "large-history-next", "Still editable");
    assert_eq!(changed.revision, LARGE_HISTORY_REVISIONS + 1);
    assert_eq!(changed.brief, imported.brief);
}

fn rejected_bundle_budget<T>(result: Result<T, StorageError>) {
    assert!(matches!(
        result,
        Err(StorageError::BlobTooLarge { size, limit })
            if size > BUNDLE_MANIFEST_BUDGET && limit == BUNDLE_MANIFEST_BUDGET
    ));
}

#[test]
fn bundle_metadata_keeps_its_existing_budget_on_export_inspect_and_import() {
    let temp = tempfile::tempdir().unwrap();
    let seed = service(temp.path(), "seed");
    let backup = large_json_history_fixture(&seed);
    let bundle = temp.path().join("oversized-bundle");
    // First create a valid small bundle using the actual exporter. Replacing
    // its backup with the valid large journal isolates the existing format cap.
    let mut manifest = seed
        .export_project_bundle(backup.project.id, &bundle)
        .unwrap();
    let producer = service(temp.path(), "producer");
    let imported = producer.import_project(&backup).unwrap();
    manifest.backup = backup;
    let encoded = serde_json::to_vec_pretty(&manifest).unwrap();
    assert!(encoded.len() as u64 > BUNDLE_MANIFEST_BUDGET);
    fs::write(bundle.join("manifest.json"), &encoded).unwrap();
    let fingerprint = BundleFingerprint::from_sha256(hex::encode(
        Sha256::new()
            .chain_update(b"motionwright.bundle-inspection.v1\0")
            .chain_update(&encoded)
            .finalize(),
    ))
    .unwrap();
    let manifest_digest = hex::encode(Sha256::digest(&encoded));
    drop(encoded);
    drop(manifest);

    let exported = temp.path().join("oversized-export");
    let stamped = temp.path().join("oversized-stamped-export");
    rejected_bundle_budget(producer.export_project_bundle(imported.id, &exported));
    rejected_bundle_budget(producer.export_project_bundle_at(
        imported.id,
        &imported.stamp(),
        &stamped,
    ));
    assert!(!exported.exists());
    assert!(!stamped.exists());
    assert_no_export_stage(temp.path());
    assert_eq!(producer.project(imported.id).unwrap(), imported);

    let destination = service(temp.path(), "destination");
    rejected_bundle_budget(destination.inspect_project_bundle(&bundle));
    rejected_bundle_budget(destination.inspect_project_bundle_with_fingerprint(&bundle));
    rejected_bundle_budget(destination.import_project_bundle(&bundle));
    rejected_bundle_budget(destination.import_project_bundle_inspected(&bundle, &fingerprint));
    assert_empty_destination(&destination);
    assert_eq!(
        hex::encode(Sha256::digest(
            fs::read(bundle.join("manifest.json")).unwrap(),
        )),
        manifest_digest,
    );
}
