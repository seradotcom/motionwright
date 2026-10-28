#[path = "support/sqlite_v6_corruptible.rs"]
mod fixture;
use motionwright_domain::{
    Change, Project, RenderImpactCause, RenderTailInspectRequest, RevisionStamp, SourceFrameWindow,
};
use motionwright_storage::{RenderWindowCacheKey, StorageError, Store};
use std::fs;
use tempfile::TempDir;
use uuid::Uuid;

fn fixture(scoped: bool) -> (TempDir, Store, Project) {
    let dir = tempfile::tempdir().unwrap();
    let mut project = Project::new("Real revision-bound derived cache").unwrap();
    for label in ["Open", "Proof", "Close"] {
        project
            .apply_change(&Change::AddScene {
                name: label.into(),
                objective: format!("Source scene {label}"),
                duration_seconds: 2,
            })
            .unwrap();
    }
    if scoped {
        project.deliverables[0].included_scene_ids = vec![project.scenes[1].id];
    }
    let mut store = Store::open(dir.path().join("project.sqlite3")).unwrap();
    store.create_project(&project).unwrap();
    (dir, store, project)
}

fn key(
    project: &Project,
    profile: Uuid,
    start: u64,
    end: u64,
    source: &str,
) -> RenderWindowCacheKey {
    use sha2::{Digest, Sha256};
    RenderWindowCacheKey {
        project_id: project.id,
        expected: RevisionStamp::from(project),
        deliverable_id: profile,
        source_window: SourceFrameWindow { start, end },
        fingerprint_sha256: hex::encode(Sha256::digest(source.as_bytes())),
    }
}

fn write(dir: &TempDir, name: &str, data: &[u8]) -> std::path::PathBuf {
    let file = dir.path().join(name);
    fs::write(&file, data).unwrap();
    file
}

fn request(
    project: &Project,
    cause: RenderImpactCause,
    selected: Vec<Uuid>,
) -> RenderTailInspectRequest {
    RenderTailInspectRequest {
        expected_generation: project.generation,
        expected_revision: project.revision,
        cause,
        changed_scene_ids: selected,
        tail_frames: 0,
        tail_source_asset_id: None,
    }
}

#[test]
fn selective_physical_purge_preserves_clean_windows_and_immutable_cas() {
    let (dir, mut store, p) = fixture(false);
    let profile = p.deliverables[0].id;
    let keys = [
        key(&p, profile, 0, 60, "opening-native-input-v1"),
        key(&p, profile, 60, 120, "proof-native-input-v1"),
        key(&p, profile, 120, 180, "closing-native-input-v1"),
    ];
    let inputs = [
        write(&dir, "one.bin", b"frame chunk ONE"),
        write(&dir, "two.bin", b"frame chunk TWO"),
        write(&dir, "three.bin", b"frame chunk THREE"),
    ];
    for (k, input) in keys.iter().zip(inputs.iter()) {
        store.put_render_window_proxy(k, input, 60).unwrap();
    }
    let immutable = store
        .lookup_render_window_proxy(&keys[1])
        .unwrap()
        .unwrap()
        .blob_path;
    let project_before = store.load_project(p.id).unwrap();

    let purged = store
        .purge_render_window_proxies(
            p.id,
            &request(&p, RenderImpactCause::SceneVisual, vec![p.scenes[1].id]),
        )
        .unwrap();
    assert_eq!(purged.registered_entries_checked, 3);
    assert_eq!(purged.manifests_removed, 1);
    assert_eq!(purged.registrations_removed, 1);
    assert_eq!(purged.registrations_retained, 2);
    assert!(purged.impact.actually_invalidated_caches);
    assert!(!purged.native_render_equivalence_verified);
    assert_eq!(purged.impact.native_frames_regenerated_or_avoided, None);
    assert_eq!(purged.impact.native_renderer_saved_wallclock_micros, None);
    assert_eq!(purged.immutable_blobs_deleted, 0);
    assert!(
        store
            .lookup_render_window_proxy(&keys[1])
            .unwrap()
            .is_none()
    );
    assert!(
        store
            .lookup_render_window_proxy(&keys[0])
            .unwrap()
            .is_some()
    );
    assert!(
        store
            .lookup_render_window_proxy(&keys[2])
            .unwrap()
            .is_some()
    );
    assert_eq!(fs::read(immutable).unwrap(), b"frame chunk TWO");
    assert_eq!(store.load_project(p.id).unwrap(), project_before);

    // Regeneration of only the dirty derived chunk yields the same bytes as
    // a cold full concatenation; does not claim actual native render parity.
    store
        .put_render_window_proxy(&keys[1], &inputs[1], 60)
        .unwrap();
    let rebuilt = keys
        .iter()
        .map(|k| {
            fs::read(
                store
                    .lookup_render_window_proxy(k)
                    .unwrap()
                    .unwrap()
                    .blob_path,
            )
            .unwrap()
        })
        .collect::<Vec<_>>()
        .concat();
    let full = inputs
        .iter()
        .map(|i| fs::read(i).unwrap())
        .collect::<Vec<_>>()
        .concat();
    assert_eq!(rebuilt, full);
}

#[test]
fn committed_revision_evicts_all_even_when_new_scene_windows_do_not_overlap() {
    let (dir, mut store, p) = fixture(false);
    let profile = p.deliverables[0].id;
    let old = key(&p, profile, 120, 180, "closing-before-unrelated-change");
    store
        .put_render_window_proxy(&old, write(&dir, "old.bin", b"before"), 60)
        .unwrap();
    let edited = store
        .apply(
            p.id,
            &RevisionStamp::from(&p),
            "test-new-title",
            &Change::RenameProject {
                title: "New project title".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(edited.revision, p.revision + 1);
    let report = store
        .purge_render_window_proxies(
            p.id,
            &request(
                &edited,
                RenderImpactCause::SceneVisual,
                vec![edited.scenes[0].id],
            ),
        )
        .unwrap();
    // The successful edit already physically evicted this old derived entry;
    // manual inspection cannot claim it performed the same deletion twice.
    assert_eq!(report.manifests_removed, 0);
    assert_eq!(report.registered_entries_checked, 0);
    assert!(matches!(
        store.lookup_render_window_proxy(&old),
        Err(StorageError::Conflict { .. }),
    ));
}

#[test]
fn stale_authority_cannot_purge_or_admit_and_invalid_windows_fail_closed() {
    let (dir, mut store, p) = fixture(false);
    let key = key(&p, p.deliverables[0].id, 0, 60, "pinned");
    store
        .put_render_window_proxy(&key, write(&dir, "source.bin", b"pinned"), 60)
        .unwrap();
    let mut stale = request(&p, RenderImpactCause::GlobalMix, vec![]);
    stale.expected_revision += 1;
    assert!(matches!(
        store.purge_render_window_proxies(p.id, &stale),
        Err(StorageError::Conflict { .. })
    ));
    let mut foreign = key.clone();
    foreign.expected.generation = Uuid::new_v4();
    assert!(matches!(
        store.lookup_render_window_proxy(&foreign),
        Err(StorageError::GenerationConflict)
    ));
    foreign = key.clone();
    foreign.source_window = SourceFrameWindow { start: 59, end: 59 };
    assert!(
        store
            .put_render_window_proxy(&foreign, write(&dir, "rejected.bin", b"reject"), 1)
            .is_err()
    );
    assert!(store.lookup_render_window_proxy(&key).unwrap().is_some());
    assert_eq!(
        store
            .purge_render_window_proxies(
                p.id,
                &request(&p, RenderImpactCause::SceneVisual, vec![p.scenes[2].id])
            )
            .unwrap()
            .manifests_removed,
        0
    );
    assert!(store.lookup_render_window_proxy(&key).unwrap().is_some());
}

#[test]
fn scoped_montage_purges_all_proxy_entries_without_fabricating_frame_mapping() {
    let (dir, mut store, p) = fixture(true);
    let k = key(&p, p.deliverables[0].id, 60, 120, "selected-only");
    store
        .put_render_window_proxy(&k, write(&dir, "selected.bin", b"selected"), 60)
        .unwrap();
    let outside = key(&p, p.deliverables[0].id, 0, 60, "not-selected");
    assert!(
        store
            .put_render_window_proxy(&outside, write(&dir, "outside.bin", b"unselected"), 60)
            .is_err()
    );
    let report = store
        .purge_render_window_proxies(
            p.id,
            &request(&p, RenderImpactCause::SceneVisual, vec![p.scenes[0].id]),
        )
        .unwrap();
    assert_eq!(report.manifests_removed, 1);
    assert!(report.impact.profiles[0].is_scoped_selection);
    assert!(!report.impact.profiles[0].output_frame_projection_verified);
}

#[test]
fn global_mix_invalidates_all_selected_profile_windows_without_deleting_sources() {
    let (dir, mut store, p) = fixture(false);
    let profile = p.deliverables[0].id;
    let a = key(&p, profile, 0, 60, "global-a");
    let b = key(&p, profile, 60, 120, "global-b");
    for (k, name) in [(&a, "a"), (&b, "b")] {
        store
            .put_render_window_proxy(k, write(&dir, &format!("{name}.bin"), name.as_bytes()), 60)
            .unwrap();
    }
    let evidence = store
        .purge_render_window_proxies(p.id, &request(&p, RenderImpactCause::GlobalMix, vec![]))
        .unwrap();
    assert_eq!(evidence.manifests_removed, 2);
    assert!(store.lookup_render_window_proxy(&a).unwrap().is_none());
    assert!(store.lookup_render_window_proxy(&b).unwrap().is_none());
    let repeated = store
        .purge_render_window_proxies(p.id, &request(&p, RenderImpactCause::GlobalMix, vec![]))
        .unwrap();
    assert_eq!(repeated.manifests_removed, 0);
    assert!(!repeated.impact.actually_invalidated_caches);
}

fn manifest_path(dir: &TempDir, key: &RenderWindowCacheKey) -> std::path::PathBuf {
    use sha2::{Digest, Sha256};
    let scope = format!(
        "project:{}:generation:{}:revision:{}:profile:{}:source:{}-{}",
        key.project_id,
        key.expected.generation,
        key.expected.revision,
        key.deliverable_id,
        key.source_window.start,
        key.source_window.end,
    );
    let hash = hex::encode(Sha256::digest(scope.as_bytes()));
    let root = dir.path().join("derived-cache").join("sha256");
    let namespaces = fs::read_dir(&root)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| {
            path.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with("managed-")
        })
        .collect::<Vec<_>>();
    assert_eq!(
        namespaces.len(),
        1,
        "fixture must have exactly one managed root namespace"
    );
    assert!(fs::symlink_metadata(&namespaces[0]).unwrap().is_dir());
    namespaces[0].join(&hash[..2]).join(hash)
}

#[test]
fn project_commit_atomically_invalidates_old_window_keys_and_deletes_derived_manifest() {
    let (dir, mut store, p) = fixture(false);
    let key = key(&p, p.deliverables[0].id, 0, 60, "render source v1");
    let input = write(&dir, "artifact.bin", b"rendered-derived-only");
    store.put_render_window_proxy(&key, &input, 60).unwrap();
    let before = store.lookup_render_window_proxy(&key).unwrap().unwrap();
    let blob = before.blob_path;
    let manifest = manifest_path(&dir, &key);
    assert_eq!(fs::read_dir(&manifest).unwrap().count(), 1);
    let edited = store
        .apply(
            p.id,
            &RevisionStamp::from(&p),
            "successful-project-edit",
            &Change::RenameProject {
                title: "Revision two".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(edited.revision, p.revision + 1);
    assert_eq!(store.load_project(p.id).unwrap(), edited);
    assert!(matches!(
        store.lookup_render_window_proxy(&key),
        Err(StorageError::Conflict { .. })
    ));
    let db = rusqlite::Connection::open(dir.path().join("project.sqlite3")).unwrap();
    let active: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM render_window_cache_entries WHERE project_id=?1",
            [p.id.to_string()],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(active, 0);
    // Best-effort postcommit physical deletion succeeded; original immutable
    // CAS media survives despite loss of its disposable derived manifest.
    assert_eq!(fs::read_dir(manifest).unwrap().count(), 0);
    assert_eq!(fs::read(blob).unwrap(), b"rendered-derived-only");
    let remaining = store.drain_render_window_evictions(16).unwrap();
    assert_eq!(remaining.tombstones_selected, 0);
}

#[test]
fn rejected_edit_never_clears_live_cache_registration() {
    let (dir, mut store, p) = fixture(false);
    let k = key(&p, p.deliverables[0].id, 0, 60, "source-bound");
    store
        .put_render_window_proxy(&k, write(&dir, "unchanged.bin", b"unchanged"), 60)
        .unwrap();
    let mut stale = RevisionStamp::from(&p);
    stale.revision += 1;
    assert!(matches!(
        store.apply(
            p.id,
            &stale,
            "invalid-edit",
            &Change::RenameProject {
                title: "Should not apply".into()
            }
        ),
        Err(StorageError::Conflict { .. })
    ));
    assert!(store.lookup_render_window_proxy(&k).unwrap().is_some());
    assert_eq!(fs::read_dir(manifest_path(&dir, &k)).unwrap().count(), 1);
}

#[test]
fn interruption_after_sql_revocation_can_recover_physical_eviction() {
    let (dir, store, p) = fixture(false);
    let k = key(&p, p.deliverables[0].id, 0, 60, "interrupted cleanup");
    store
        .put_render_window_proxy(
            &k,
            write(&dir, "pending.bin", b"persistently protected"),
            60,
        )
        .unwrap();
    let path = dir.path().join("project.sqlite3");
    let manifest = manifest_path(&dir, &k);
    let conn = fixture::open_v6_writer(path);
    conn.execute_batch(
        "BEGIN IMMEDIATE;
         INSERT INTO render_window_cache_evictions
             SELECT * FROM render_window_cache_entries;
         DELETE FROM render_window_cache_entries;
         COMMIT;",
    )
    .unwrap();
    drop(conn);
    drop(store);
    let reloaded = Store::open(dir.path().join("project.sqlite3")).unwrap();
    assert!(reloaded.lookup_render_window_proxy(&k).unwrap().is_none());
    // Opening the existing store now automatically drains bounded tombstones.
    assert_eq!(fs::read_dir(manifest).unwrap().count(), 0);
    let report = reloaded.drain_render_window_evictions(16).unwrap();
    assert_eq!(report.tombstones_selected, 0);
    assert_eq!(report.manifests_removed, 0);
    assert_eq!(report.immutable_blobs_deleted, 0);
}

#[test]
fn corrupt_derived_manifest_can_be_evicted_without_trusting_corrupt_bytes() {
    let (dir, mut store, p) = fixture(false);
    let k = key(&p, p.deliverables[0].id, 0, 60, "corruption");
    store
        .put_render_window_proxy(&k, write(&dir, "corrupt.bin", b"source-is-untouched"), 60)
        .unwrap();
    let folder = manifest_path(&dir, &k);
    let file = fs::read_dir(&folder)
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    fs::write(&file, b"{corrupt-malformed-cache-manifest").unwrap();
    assert!(store.lookup_render_window_proxy(&k).is_err());
    let edited = store
        .apply(
            p.id,
            &RevisionStamp::from(&p),
            "evict-corrupt-manifest",
            &Change::RenameProject {
                title: "Revision without orphan manifest".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(edited.revision, p.revision + 1);
    assert_eq!(fs::read_dir(folder).unwrap().count(), 0);
}

#[cfg(unix)]
#[test]
fn symlink_manifests_never_escape_eviction_directory_and_tombstone_can_retry() {
    use std::os::unix::fs::symlink;
    let (dir, mut store, p) = fixture(false);
    let k = key(&p, p.deliverables[0].id, 0, 60, "symlink attack");
    store
        .put_render_window_proxy(&k, write(&dir, "symlink.bin", b"protected bytes"), 60)
        .unwrap();
    let folder = manifest_path(&dir, &k);
    let manifest = fs::read_dir(&folder)
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    fs::remove_file(&manifest).unwrap();
    let outside = write(&dir, "outside-file", b"outside cannot be touched");
    symlink(&outside, &manifest).unwrap();
    let edited = store
        .apply(
            p.id,
            &RevisionStamp::from(&p),
            "commit-with-insecure-cache-entry",
            &Change::RenameProject {
                title: "Committed edit should survive".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(edited.revision, p.revision + 1);
    assert_eq!(fs::read(&outside).unwrap(), b"outside cannot be touched");
    assert!(matches!(
        store.lookup_render_window_proxy(&k),
        Err(StorageError::Conflict { .. })
    ));
    assert!(store.drain_render_window_evictions(16).is_err());
    fs::remove_file(manifest).unwrap();
    assert_eq!(
        store
            .drain_render_window_evictions(16)
            .unwrap()
            .tombstones_cleared,
        1
    );
    assert_eq!(fs::read(&outside).unwrap(), b"outside cannot be touched");
}
