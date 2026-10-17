//! Capture an authentic pre-recipe backup through the existing Store exporter.
//! This generator is retained by its immutable commit for fixture reproduction.
use motionwright_domain::{Asset, Change, MixIntent};
use motionwright_storage::Store;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

const EXPORTER_BASE: &str = "2178e31c55f1d041e39fea23227536f4886c4873";
const ORIGINAL: &[u8] = b"Synthetic original source for pre-recipe storage compatibility.\n";

fn file_sha256(path: &Path) -> String {
    let mut file = File::open(path).unwrap();
    let mut digest = Sha256::new();
    let mut chunk = [0_u8; 128 * 1024];
    loop {
        let count = file.read(&mut chunk).unwrap();
        if count == 0 {
            break;
        }
        digest.update(&chunk[..count]);
    }
    hex::encode(digest.finalize())
}

fn collect_source_files(directory: &Path, files: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(directory).unwrap() {
        let entry = entry.unwrap();
        let kind = entry.file_type().unwrap();
        assert!(!kind.is_symlink(), "source manifest cannot follow links");
        if kind.is_dir() {
            collect_source_files(&entry.path(), files);
        } else if kind.is_file() {
            files.push(entry.path());
        }
    }
}

#[test]
fn export_pre_recipe_backup_with_real_journal_and_cas() {
    let temporary = tempfile::tempdir().unwrap();
    let database = temporary.path().join("source.sqlite3");
    let mut source = Store::open(&database).unwrap();
    let mut project = source
        .create_named_project("Pre-recipe persistence fixture")
        .unwrap();
    let original_path = temporary.path().join("original.txt");
    fs::write(&original_path, ORIGINAL).unwrap();
    let blob = source.ingest_blob_file(&original_path).unwrap();
    let asset_id = Uuid::from_u128(0x0102030405060708090a0b0c0d0e0f10);
    let changes = [
        Change::AddScene {
            name: "Original fixture scene".into(),
            objective: "Preserve editable state across format extensions".into(),
            duration_seconds: 3,
        },
        Change::AddAsset {
            asset: Asset {
                id: asset_id,
                name: "Original compatibility source".into(),
                media_type: "text/plain".into(),
                content_sha256: Some(blob.sha256.clone()),
                source_revision: Some("synthetic-compatibility-source/1".into()),
            },
        },
        Change::SetMixIntent {
            mix: MixIntent {
                voice_gain_db: -3.25,
                music_gain_db: -14.0,
                target_lufs: Some(-16.0),
                target_true_peak_dbfs: Some(-1.5),
            },
        },
        Change::RenameProject {
            title: "Pre-recipe project and journal".into(),
        },
    ];
    for (index, change) in changes.iter().enumerate() {
        project = source
            .apply(
                project.id,
                &project.stamp(),
                &format!("legacy-fixture-{index}"),
                change,
            )
            .unwrap()
            .project;
    }
    let backup = source.export_project(project.id).unwrap();
    assert_eq!(backup.project, project);
    assert_eq!(backup.events.len(), changes.len());
    for (event, change) in backup.events.iter().zip(&changes) {
        assert_eq!(&event.change, change);
    }
    assert_eq!(backup.project.schema_version, 2);
    assert_eq!(backup.project.revision, 4);
    assert_eq!(backup.project.audio.mix.voice_gain_db, -3.25);
    assert_eq!(source.read_blob(&blob.sha256, 4096).unwrap(), ORIGINAL);
    drop(source);

    let mut reopened = Store::open(&database).unwrap();
    assert_eq!(reopened.load_project(project.id).unwrap(), project);
    let reexported = reopened.export_project(project.id).unwrap();
    assert_eq!(reexported.payload_sha256, backup.payload_sha256);
    assert_eq!(reexported.project, backup.project);
    assert_eq!(reexported.events, backup.events);

    let bundle = temporary.path().join("portable.motionwright");
    let manifest = reopened.export_project_bundle(project.id, &bundle).unwrap();
    assert_eq!(manifest.backup.payload_sha256, backup.payload_sha256);
    let mut destination = Store::open(temporary.path().join("destination.sqlite3")).unwrap();
    let imported = destination.import_project_bundle(&bundle).unwrap();
    assert_eq!(imported.id, project.id);
    assert_eq!(imported.revision, project.revision);
    assert_ne!(imported.generation, project.generation);
    assert_eq!(imported.assets, project.assets);
    assert_eq!(imported.scenes, project.scenes);
    assert_eq!(imported.audio, project.audio);
    assert_eq!(destination.read_blob(&blob.sha256, 4096).unwrap(), ORIGINAL);

    let fixture = format!("{}\n", serde_json::to_string(&backup).unwrap());
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut files = Vec::new();
    collect_source_files(&root.join("crates/domain/src"), &mut files);
    collect_source_files(&root.join("crates/storage/src"), &mut files);
    for relative in [
        "Cargo.toml",
        "Cargo.lock",
        "SOURCE_LOCK.json",
        "rust-toolchain.toml",
        "crates/domain/Cargo.toml",
        "crates/storage/Cargo.toml",
    ] {
        files.push(root.join(relative));
    }
    files.sort();
    let source_files: Vec<_> = files
        .iter()
        .map(|path| {
            json!({
                "path": path.strip_prefix(&root).unwrap().to_str().unwrap().replace('\\', "/"),
                "sha256": file_sha256(path),
            })
        })
        .collect();
    let executable = std::env::current_exe().unwrap();
    let provenance = json!({
        "schema": "motionwright.pre-recipe-backup-provenance/1",
        "exporter_base_sha": EXPORTER_BASE,
        "generator_commit": std::env::var("GITHUB_SHA").unwrap_or_else(|_| "LOCAL_UNATTESTED".into()),
        "workflow_run_id": std::env::var("GITHUB_RUN_ID").ok(),
        "workflow_run_attempt": std::env::var("GITHUB_RUN_ATTEMPT").ok(),
        "workflow": std::env::var("GITHUB_WORKFLOW").ok(),
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "command": "cargo test -p motionwright-storage -p motionwright-service --all-targets",
        "generator_test": "export_pre_recipe_backup_with_real_journal_and_cas",
        "producer": "Actual pre-recipe Store::export_project in a compiled integration-test executable",
        "not_claimed": "Installed desktop export, human acceptance, platform signing",
        "executable_sha256": file_sha256(&executable),
        "fixture_file_sha256": hex::encode(Sha256::digest(fixture.as_bytes())),
        "fixture_file_size_bytes": fixture.len(),
        "fixture_encoding": "UTF-8 compact JSON with one final LF, no BOM",
        "payload_sha256": backup.payload_sha256,
        "project_revision": backup.project.revision,
        "event_count": backup.events.len(),
        "original_source_utf8": std::str::from_utf8(ORIGINAL).unwrap(),
        "original_source_sha256": blob.sha256,
        "source_files": source_files,
    });
    // A machine-readable export stream also works under the standard test
    // harness capture. It contains synthetic fixture bytes and public hashes.
    let mut output = std::io::stdout().lock();
    output.write_all(b"MW_PRE_RECIPE_BACKUP_JSON\t").unwrap();
    output.write_all(fixture.as_bytes()).unwrap();
    writeln!(output, "MW_PRE_RECIPE_BACKUP_PROVENANCE\t{provenance}").unwrap();
}
