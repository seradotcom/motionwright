//! Persistent originals for the three mandatory Hero output profiles.
//!
//! These entrypoints grant no renderer authority. The outer owner admits each
//! profile separately; these commands preserve and reopen the actual Stores.

use motionwright_domain::Project;
use motionwright_service::StudioService;
use serde_json::{Value, json};
use std::{fs, path::Path};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const MAX_JSON: u64 = 4 * 1024 * 1024;

pub(super) fn require_selection(aspect: &str) -> Result<()> {
    if std::env::var("GITHUB_ACTIONS").as_deref() != Ok("true")
        || std::env::var("MOTIONWRIGHT_NATIVE_SCOPE").as_deref() != Ok("hero-profiles-three")
        || std::env::var("MOTIONWRIGHT_E2E_FIXTURE").as_deref() != Ok(aspect)
        || !matches!(aspect, "landscape" | "portrait" | "square")
    {
        return Err("retained Hero Store requires its exact disposable profile selection".into());
    }
    Ok(())
}

fn private_directory(path: &Path) -> Result<()> {
    if !path.is_absolute() || fs::canonicalize(path)? != path {
        return Err("retained profile directory must be canonical".into());
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err("retained profile directory must be a real directory".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o777 != 0o700 {
            return Err("retained profile directory must be private".into());
        }
    }
    Ok(())
}

pub(super) fn validate_paths(
    database: &Path,
    aspect: &str,
    project_json: &Path,
    imported_database: &Path,
    before_seed: bool,
) -> Result<()> {
    if !matches!(aspect, "landscape" | "portrait" | "square")
        || project_json.file_name().and_then(|name| name.to_str()) != Some("editable-project.json")
    {
        return Err("retained profile accepts only the three original Hero profiles".into());
    }
    let evidence = project_json
        .parent()
        .ok_or("missing profile evidence parent")?;
    let case = evidence.parent().ok_or("missing profile case parent")?;
    let owner = case.join("owner");
    if evidence != case.join("evidence")
        || database != owner.join("motionwright-data/motionwright.sqlite3")
        || imported_database != owner.join("motionwright-import/motionwright.sqlite3")
        || database == imported_database
    {
        return Err("retained profile Stores must use their distinct owned namespace".into());
    }
    for path in [
        case,
        evidence,
        &owner,
        database.parent().ok_or("missing source Store parent")?,
        imported_database
            .parent()
            .ok_or("missing imported Store parent")?,
    ] {
        private_directory(path)?;
    }
    if before_seed {
        for path in [database, imported_database] {
            if fs::read_dir(path.parent().ok_or("missing Store parent")?)?
                .next()
                .is_some()
            {
                return Err("retained profile Store namespace must be fresh".into());
            }
        }
        if project_json.try_exists()?
            || project_json
                .with_file_name("hero.motionwright")
                .try_exists()?
        {
            return Err("retained profile source originals must not preexist".into());
        }
    } else {
        for path in [database, imported_database] {
            let metadata = fs::symlink_metadata(path)?;
            if !metadata.is_file()
                || metadata.file_type().is_symlink()
                || fs::canonicalize(path)? != path
                || metadata.len() == 0
                || metadata.len() > 64 * 1024 * 1024
            {
                return Err("retained profile Store is not the bounded physical original".into());
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if metadata.nlink() != 1 {
                    return Err("retained profile Store must not be hard linked".into());
                }
            }
        }
    }
    Ok(())
}

fn json_original(path: &Path) -> Result<Value> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() == 0
        || metadata.len() > MAX_JSON
        || fs::canonicalize(path)? != path
    {
        return Err("retained profile JSON is not a bounded original".into());
    }
    let raw = fs::read(path)?;
    if raw.len() as u64 != metadata.len() {
        return Err("retained profile JSON changed while reopening".into());
    }
    Ok(serde_json::from_slice(&raw)?)
}

fn actual_project(service: &StudioService, original: &Value) -> Result<Project> {
    let mut projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("retained profile Store no longer has exactly one project".into());
    }
    let project = projects.remove(0);
    if serde_json::to_value(&project)? != *original {
        return Err("retained profile Project differs after physical Store reopen".into());
    }
    Ok(project)
}

fn integrity(service: &StudioService, project: &Project) -> Result<Value> {
    let page = service.asset_integrity_page(project.id, &project.stamp(), None, 16)?;
    let value = serde_json::to_value(&page)?;
    if !page.complete
        || page.next.is_some()
        || page.items.len() != page.total_assets
        || value["items"]
            .as_array()
            .ok_or("missing physical asset results")?
            .iter()
            .any(|item| item["status"] != "verified")
    {
        return Err("retained profile CAS integrity is incomplete or negative".into());
    }
    Ok(value)
}

fn backup_state_equal(original: &Value, observed: &Value) -> Result<bool> {
    // Store::export_project_snapshot regenerates exported_at for each real
    // export. Its payload digest covers these five stable fields instead.
    // Both complete originals, including their timestamps, remain retained.
    for value in [original, observed] {
        if value.as_object().map(|object| object.len()) != Some(6)
            || value["exported_at"].as_str().is_none_or(str::is_empty)
        {
            return Err("retained profile backup shape or export timestamp changed".into());
        }
    }
    Ok([
        "format_version",
        "source_storage_schema",
        "project",
        "events",
        "payload_sha256",
    ]
    .iter()
    .all(|field| original.get(*field).is_some() && original.get(*field) == observed.get(*field)))
}

pub(super) fn reopen(
    database: &Path,
    aspect: &str,
    project_json: &Path,
    imported_database: &Path,
    receipt: &Path,
) -> Result<()> {
    validate_paths(database, aspect, project_json, imported_database, false)?;
    if receipt.parent() != project_json.parent()
        || !matches!(
            receipt.file_name().and_then(|name| name.to_str()),
            Some("profile-before-native-reopen.json" | "profile-after-native-reopen.json")
        )
        || receipt.try_exists()?
    {
        return Err("retained profile reopening requires a fresh closed receipt path".into());
    }
    let phase = if receipt.file_name().and_then(|name| name.to_str())
        == Some("profile-before-native-reopen.json")
    {
        "before-native"
    } else {
        "after-native"
    };
    let source_observation =
        project_json.with_file_name(format!("source-{phase}-backup.original.json"));
    let imported_observation =
        project_json.with_file_name(format!("imported-{phase}-backup.original.json"));
    if source_observation.try_exists()? || imported_observation.try_exists()? {
        return Err("retained profile backup observation must not be repeated".into());
    }
    let source_original = json_original(project_json)?;
    let imported_original =
        json_original(&project_json.with_file_name("imported-project.original.json"))?;
    let source_backup = json_original(&project_json.with_file_name("source-backup.original.json"))?;
    let imported_backup =
        json_original(&project_json.with_file_name("imported-backup.original.json"))?;
    let roundtrip = json_original(&project_json.with_file_name("bundle-import-check.json"))?;
    let source = StudioService::open(database)?;
    let imported = StudioService::open(imported_database)?;
    let project = actual_project(&source, &source_original)?;
    let recovered = actual_project(&imported, &imported_original)?;
    let source_reread = serde_json::to_value(source.export_project(project.id)?)?;
    let imported_reread = serde_json::to_value(imported.export_project(recovered.id)?)?;
    super::cycle_new_json(&source_observation, &source_reread)?;
    super::cycle_new_json(&imported_observation, &imported_reread)?;
    let size = match aspect {
        "landscape" => (1920, 1080),
        "portrait" => (1080, 1920),
        "square" => (1080, 1080),
        _ => unreachable!("validated profile"),
    };
    if project.deliverables.len() != 1
        || (
            project.deliverables[0].width,
            project.deliverables[0].height,
        ) != size
        || project.id != recovered.id
        || project.generation == recovered.generation
        || project.revision != recovered.revision
        || project.scenes != recovered.scenes
        || project.production_design != recovered.production_design
        || project.deliverables != recovered.deliverables
        || roundtrip["source_generation"] != json!(project.generation)
        || roundtrip["import_generation"] != json!(recovered.generation)
        || roundtrip["old_generation_write_rejected"] != true
        || !backup_state_equal(&source_backup, &source_reread)?
        || !backup_state_equal(&imported_backup, &imported_reread)?
    {
        return Err("retained profile state, generations or event history changed".into());
    }
    let bundle =
        imported.inspect_project_bundle(project_json.with_file_name("hero.motionwright"))?;
    if roundtrip["event_count"] != json!(bundle.project.event_count)
        || roundtrip["blob_count"] != json!(bundle.blob_count)
    {
        return Err("retained profile bundle differs from the original round trip".into());
    }
    let source_integrity = integrity(&source, &project)?;
    let imported_integrity = integrity(&imported, &recovered)?;
    // Both independent Store handles are closed before this readback is sealed.
    drop(source);
    drop(imported);
    let observed = json!({
        "schema": "motionwright.native.hero-profile-reopen/1",
        "profile": aspect, "width": size.0, "height": size.1, "frames": 180,
        "project_id": project.id, "source_generation": project.generation,
        "import_generation": recovered.generation, "revision": project.revision,
        "source_project_equal": true, "imported_project_equal": true,
        "source_events_equal": true, "imported_events_equal": true,
        "source_backup_payload_sha256": source_reread["payload_sha256"],
        "imported_backup_payload_sha256": imported_reread["payload_sha256"],
        "complete_export_observations_retained": true,
        "bundle_event_count": bundle.project.event_count, "bundle_blob_count": bundle.blob_count,
        "source_asset_integrity": source_integrity, "imported_asset_integrity": imported_integrity,
        "generation_rotated": true, "old_generation_write_rejected_original": true,
        "stores_closed_before_receipt": true, "new_apply": false,
        "native_calls": 0, "automatic_retry": false
    });
    super::cycle_new_json(receipt, &observed)?;
    println!(
        "{}",
        serde_json::to_string(&json!({"profile_reopen": "PASS", "profile": aspect,
        "native_calls": 0, "new_apply": false}))?
    );
    Ok(())
}

#[cfg(test)]
mod profile_store_tests {
    use super::*;
    use motionwright_domain::Change;

    fn layout(root: &Path) -> (std::path::PathBuf, std::path::PathBuf, std::path::PathBuf) {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(root, fs::Permissions::from_mode(0o700)).unwrap();
        }
        for relative in [
            "owner",
            "owner/motionwright-data",
            "owner/motionwright-import",
            "evidence",
        ] {
            let path = root.join(relative);
            fs::create_dir(&path).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            }
        }
        (
            root.join("owner/motionwright-data/motionwright.sqlite3"),
            root.join("owner/motionwright-import/motionwright.sqlite3"),
            root.join("evidence/editable-project.json"),
        )
    }

    #[test]
    fn three_real_stores_preserve_export_import_generations_events_and_reopen() {
        for aspect in ["landscape", "portrait", "square"] {
            let root = tempfile::tempdir().unwrap();
            let (database, imported, project) = layout(root.path());
            validate_paths(&database, aspect, &project, &imported, true).unwrap();
            super::super::seed_hero_with_import(&database, aspect, &project, Some(&imported))
                .unwrap();
            for name in [
                "profile-before-native-reopen.json",
                "profile-after-native-reopen.json",
            ] {
                let receipt = project.with_file_name(name);
                reopen(&database, aspect, &project, &imported, &receipt).unwrap();
                let value = json_original(&receipt).unwrap();
                assert_eq!(value["native_calls"], 0);
                assert_eq!(value["generation_rotated"], true);
                assert_eq!(value["stores_closed_before_receipt"], true);
            }
            assert!(database.is_file() && imported.is_file());
            assert!(validate_paths(&database, aspect, &project, &imported, true).is_err());
        }
    }

    #[test]
    fn changed_imported_store_is_a_negative_and_does_not_create_reopen_receipt() {
        let root = tempfile::tempdir().unwrap();
        let (database, imported, project) = layout(root.path());
        validate_paths(&database, "portrait", &project, &imported, true).unwrap();
        super::super::seed_hero_with_import(&database, "portrait", &project, Some(&imported))
            .unwrap();
        validate_paths(&database, "portrait", &project, &imported, false).unwrap();
        let service = StudioService::open(&imported).unwrap();
        let imported_original =
            json_original(&project.with_file_name("imported-project.original.json")).unwrap();
        let original = actual_project(&service, &imported_original).unwrap();
        service
            .apply(
                original.id,
                &original.stamp(),
                "profile-control-external-change",
                &Change::RenameProject {
                    title: "External change retained as a negative".into(),
                },
            )
            .unwrap();
        drop(service);
        let receipt = project.with_file_name("profile-before-native-reopen.json");
        let error = reopen(&database, "portrait", &project, &imported, &receipt).unwrap_err();
        assert_eq!(
            error.to_string(),
            "retained profile Project differs after physical Store reopen"
        );
        assert!(!receipt.exists());
        assert_eq!(
            StudioService::open(&imported)
                .unwrap()
                .project(original.id)
                .unwrap()
                .revision,
            original.revision + 1
        );
    }

    #[test]
    fn legacy_family_and_foreign_import_destinations_are_rejected_before_seed() {
        let root = tempfile::tempdir().unwrap();
        let (database, imported, project) = layout(root.path());
        assert!(validate_paths(&database, "split-portrait", &project, &imported, true).is_err());
        assert!(validate_paths(&database, "square", &project, &database, true).is_err());
        assert!(
            validate_paths(
                &database,
                "square",
                &project,
                &root.path().join("foreign.sqlite3"),
                true
            )
            .is_err()
        );
        assert!(!database.exists() && !imported.exists());
    }
}
