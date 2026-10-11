//! Real SQLite/StudioService CAS integration for immutable source capture
//! provenance. The PNG and JSON are ingested as actual content-addressed blobs;
//! we do NOT claim this fixture is a third-party capture or human-approved.
use motionwright_domain::{
    CAPTURE_RECORD_SCHEMA, CaptureMask, CaptureSourceRecord, Change, Project, SourceReference,
};
use motionwright_service::StudioService;
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}
fn prepare(service: &StudioService, dir: &Path) -> (Project, CaptureSourceRecord) {
    let mut p = service
        .create_project("First-party source registry")
        .unwrap();
    let icon = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../apps/desktop/src-tauri/icons/icon.png");
    let screenshot = dir.join("captured-first-party.png");
    fs::copy(&icon, &screenshot).unwrap();
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "source-png",
            &screenshot,
            "Owned first-party fixture image".into(),
            "image/png".into(),
        )
        .unwrap()
        .project;
    let screenshot_asset = p.assets.last().unwrap().clone();
    let manifest_file = dir.join("first-party-evidence.json");
    fs::write(&manifest_file, br#"{"schema":"motionwright.first-party-capture-evidence/1","scope":"test-fixture-not-reviewed"}"#).unwrap();
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "source-json",
            &manifest_file,
            "Declared capture provenance".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let manifest_asset = p.assets.last().unwrap().clone();

    // Snapshot the observed owner revision as the immutable claim source.
    let observed = p.revision;
    p = apply(
        service,
        &p,
        "capture-claim",
        Change::AddClaim {
            text: "An authorized Studio UI fixture appears in the source image".into(),
            source: Some(SourceReference::ProjectRevision { revision: observed }),
            context: "synthetic-first-party-capture-validation".into(),
            source_revision: Some("owned-test-fixture".into()),
        },
    );
    let claim = p.brief.claims.last().unwrap().clone();
    let capture = CaptureSourceRecord {
        schema: CAPTURE_RECORD_SCHEMA.into(),
        id: Uuid::new_v4(),
        screenshot_asset_id: screenshot_asset.id,
        screenshot_sha256: screenshot_asset.content_sha256.clone().unwrap(),
        manifest_asset_id: manifest_asset.id,
        manifest_sha256: manifest_asset.content_sha256.clone().unwrap(),
        fixture_state_sha256: "f".repeat(64),
        claim_id: claim.id,
        source_project_id: p.id,
        source_generation: p.generation,
        source_revision: observed,
        app_id: "motionwright-studio".into(),
        app_version: "0.1.0".into(),
        source_commit: "b".repeat(40),
        scenario_id: "production-original-ui-local-v1".into(),
        origin: "http://127.0.0.1:1420".into(),
        route: "/".into(),
        viewport_width: 1440,
        viewport_height: 940,
        device_scale_factor: 1,
        screenshot_repeat_sha256: screenshot_asset.content_sha256.clone().unwrap(),
        redaction: CaptureMask {
            selector: ".revision-chip".into(),
            x: 100,
            y: 50,
            width: 80,
            height: 24,
            mask_color: "#0B0B0B".into(),
        },
        external_requests: 0,
        credential_fields: 0,
        declared_scope: "first-party-synthetic-fixture-only".into(),
    };
    (p, capture)
}
#[test]
fn registered_first_party_capture_survives_durable_reopen_and_prevents_asset_deletion() {
    let folder = tempfile::tempdir().unwrap();
    let db = folder.path().join("capture.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let (p, capture) = prepare(&service, folder.path());
    let change = Change::RegisterCaptureSource {
        record: capture.clone(),
    };
    let observed = p.stamp();
    let result = service
        .apply(p.id, &observed, "capture-admit", &change)
        .unwrap();
    assert!(!result.replayed);
    assert_eq!(
        result.project.production_design.captures,
        vec![capture.clone()]
    );
    assert_eq!(result.project.brief, p.brief);
    let repeated = service
        .apply(p.id, &observed, "capture-admit", &change)
        .unwrap();
    assert!(repeated.replayed);
    assert_eq!(repeated.project, result.project);
    assert!(
        service
            .apply(p.id, &result.project.stamp(), "duplicate-record", &change)
            .is_err()
    );
    for asset in [capture.screenshot_asset_id, capture.manifest_asset_id] {
        assert!(
            service
                .apply(
                    p.id,
                    &result.project.stamp(),
                    &format!("delete-capture-asset-{asset}"),
                    &Change::RemoveAsset { asset_id: asset }
                )
                .is_err()
        );
    }
    drop(service);
    let opened = StudioService::open(&db).unwrap();
    assert_eq!(opened.project(p.id).unwrap(), result.project);
}
#[test]
fn forged_and_stale_source_receipts_do_not_mutate_sqlite_history() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("denied.sqlite3")).unwrap();
    let (p, capture) = prepare(&service, folder.path());
    let foreign = CaptureSourceRecord {
        screenshot_sha256: "a".repeat(64),
        screenshot_repeat_sha256: "a".repeat(64),
        ..capture.clone()
    };
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "forged-source",
                &Change::RegisterCaptureSource { record: foreign }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    let registered = apply(
        &service,
        &p,
        "register-valid",
        Change::RegisterCaptureSource {
            record: capture.clone(),
        },
    );
    assert_eq!(registered.production_design.captures.len(), 1);
    let late = Change::RegisterCaptureSource {
        record: CaptureSourceRecord {
            id: Uuid::new_v4(),
            ..capture
        },
    };
    assert!(
        service
            .apply(p.id, &p.stamp(), "stale-register", &late)
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), registered);
    assert!(
        service
            .apply(p.id, &registered.stamp(), "duplicate-asset-id", &late)
            .is_ok(),
        "a separate explicitly recorded capture may reference the same unchanged source assets"
    );
    assert_eq!(
        service
            .project(p.id)
            .unwrap()
            .production_design
            .captures
            .len(),
        2
    );
}
