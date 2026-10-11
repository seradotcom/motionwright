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
    // Deliberately synthetic viewport-sized PNG with explicit opaque mask.
    // Actual Chromium captures are validated by independent pinned CI.
    let screenshot = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tooling/testdata/synthetic-first-party-viewport.png");
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "source-png",
            &screenshot,
            "Synthetic first-party raster fixture".into(),
            "image/png".into(),
        )
        .unwrap()
        .project;
    let screenshot_asset = p.assets.last().unwrap().clone();
    let observed = p.revision;
    p = apply(
        service,
        &p,
        "capture-claim",
        Change::AddClaim {
            text: "An original synthetic Studio viewport fixture exists".into(),
            source: Some(SourceReference::ProjectRevision { revision: observed }),
            context: "synthetic-first-party-capture-validation".into(),
            source_revision: Some("synthetic-viewport-test".into()),
        },
    );
    let claim = p.brief.claims.last().unwrap().clone();
    // The original data-state JSON is an independently imported immutable
    // asset, not an unverified digest copied from the browser receipt.
    let state_file = dir.join("fixture-state.json");
    let state = serde_json::json!({
        "id": p.id,
        "generation": p.generation,
        "revision": observed,
        "claims": [{
            "id": claim.id,
            "text": claim.text,
            "source": {"kind": "project_revision", "revision": observed},
            "context": claim.context,
            "source_revision": claim.source_revision
        }]
    });
    fs::write(&state_file, serde_json::to_vec_pretty(&state).unwrap()).unwrap();
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "fixture-state-asset",
            &state_file,
            "Exact first-party fixture data state".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let state_asset = p.assets.last().unwrap().clone();
    let screenshot_sha = screenshot_asset.content_sha256.clone().unwrap();
    let manifest_file = dir.join("first-party-evidence.json");
    let manifest = serde_json::json!({
        "schema": "motionwright.first-party-capture-evidence/1",
        "app": {
            "id": "motionwright-studio",
            "version": "0.1.0",
            "source_revision": "b".repeat(40),
        },
        "scenario": {
            "id": "production-original-ui-local-v1",
            "origin": "http://127.0.0.1:1420",
            "route": "/",
            "actions": ["open:/", "click:Production"],
            "viewport": {"width":1440,"height":940,"device_scale_factor":1},
            "browser": "chromium",
            "fixture_kind": "synthetic-first-party-data",
            "auth_mode": "none",
            "external_requests": 0,
            "credential_fields": 0
        },
        "claim_id": claim.id,
        "fixture_state_sha256": state_asset.content_sha256.clone().unwrap(),
        "redactions": [{
            "selector": ".revision-chip",
            "x":100, "y":50, "width":80,"height":24,
            "mask_color":"#0B0B0B"
        }],
        "capture": {
            "path": "capture.png",
            "sha256": screenshot_sha,
            "repeated_sha256": screenshot_sha,
            "pixel_equal": true,
            "redacted": true,
            "raw_unredacted_saved": false,
            "width":1440,
            "height":940
        },
        "review_status": "technical-evidence-only-human-review-required"
    });
    fs::write(
        &manifest_file,
        serde_json::to_vec_pretty(&manifest).unwrap(),
    )
    .unwrap();
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "source-json",
            &manifest_file,
            "Declared source receipt".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let manifest_asset = p.assets.last().unwrap().clone();
    let capture = CaptureSourceRecord {
        schema: CAPTURE_RECORD_SCHEMA.into(),
        id: Uuid::new_v4(),
        screenshot_asset_id: screenshot_asset.id,
        screenshot_sha256: screenshot_asset.content_sha256.clone().unwrap(),
        manifest_asset_id: manifest_asset.id,
        manifest_sha256: manifest_asset.content_sha256.clone().unwrap(),
        fixture_state_asset_id: state_asset.id,
        fixture_state_sha256: state_asset.content_sha256.clone().unwrap(),
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
    for asset in [
        capture.screenshot_asset_id,
        capture.manifest_asset_id,
        capture.fixture_state_asset_id,
    ] {
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

#[test]
fn mismatched_manifest_bytes_reject_a_validly_imported_but_false_receipt() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("forged-manifest.sqlite3")).unwrap();
    let (project, record) = prepare(&service, folder.path());
    let source = folder.path().join("first-party-evidence.json");
    let mut forged: serde_json::Value =
        serde_json::from_slice(&fs::read(&source).unwrap()).unwrap();
    forged["capture"]["sha256"] = serde_json::Value::String("c".repeat(64));
    let false_receipt = folder.path().join("forged-evidence.json");
    fs::write(&false_receipt, serde_json::to_vec(&forged).unwrap()).unwrap();
    let revised = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "forged-manifest-import",
            &false_receipt,
            "Disagreeing JSON source".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let new_asset = revised.assets.last().unwrap();
    let wrong = CaptureSourceRecord {
        manifest_asset_id: new_asset.id,
        manifest_sha256: new_asset.content_sha256.clone().unwrap(),
        ..record
    };
    let denied = service
        .apply(
            revised.id,
            &revised.stamp(),
            "forged-record",
            &Change::RegisterCaptureSource { record: wrong },
        )
        .unwrap_err();
    assert!(
        denied
            .to_string()
            .contains("imported JSON source disagrees")
    );
    assert_eq!(service.project(revised.id).unwrap(), revised);
}
#[test]
fn valid_manifest_does_not_authorize_mismatched_png_viewport_bytes() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("mismatch-png.sqlite3")).unwrap();
    let (project, record) = prepare(&service, folder.path());
    let icon = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../apps/desktop/src-tauri/icons/icon.png");
    let revised = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "wrong-sized-png",
            &icon,
            "Wrong viewport PNG bytes".into(),
            "image/png".into(),
        )
        .unwrap()
        .project;
    let png_asset = revised.assets.last().unwrap();
    let source = folder.path().join("first-party-evidence.json");
    let mut receipt: serde_json::Value =
        serde_json::from_slice(&fs::read(&source).unwrap()).unwrap();
    receipt["capture"]["sha256"] =
        serde_json::Value::String(png_asset.content_sha256.clone().unwrap());
    receipt["capture"]["repeated_sha256"] = receipt["capture"]["sha256"].clone();
    let new_receipt = folder.path().join("wrong-viewport-receipt.json");
    fs::write(&new_receipt, serde_json::to_vec(&receipt).unwrap()).unwrap();
    let revised = service
        .import_asset_file(
            revised.id,
            &revised.stamp(),
            "wrong-viewport-receipt",
            &new_receipt,
            "Matching metadata but invalid viewport image".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let receipt_asset = revised.assets.last().unwrap();
    let bad = CaptureSourceRecord {
        screenshot_asset_id: png_asset.id,
        screenshot_sha256: png_asset.content_sha256.clone().unwrap(),
        screenshot_repeat_sha256: png_asset.content_sha256.clone().unwrap(),
        manifest_asset_id: receipt_asset.id,
        manifest_sha256: receipt_asset.content_sha256.clone().unwrap(),
        ..record
    };
    let denied = service
        .apply(
            revised.id,
            &revised.stamp(),
            "wrong-sized-admission",
            &Change::RegisterCaptureSource { record: bad },
        )
        .unwrap_err();
    assert!(denied.to_string().contains("PNG bytes do not match"));
    assert_eq!(service.project(revised.id).unwrap(), revised);
}

#[test]
fn fixture_state_claim_tampering_is_rejected_even_with_matching_imported_digests() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("forged-data-state.sqlite3")).unwrap();
    let (mut project, record) = prepare(&service, folder.path());
    let state_path = folder.path().join("fixture-state.json");
    let manifest_path = folder.path().join("first-party-evidence.json");
    for (case, forge) in ["wrong-claim", "wrong-generation"].iter().enumerate() {
        let mut state: serde_json::Value =
            serde_json::from_slice(&fs::read(&state_path).unwrap()).unwrap();
        if *forge == "wrong-claim" {
            state["claims"][0]["text"] = serde_json::json!("Forged observed claim");
        } else {
            state["generation"] = serde_json::json!(Uuid::new_v4());
        }
        let new_state = folder.path().join(format!("{case}-fixture-state.json"));
        fs::write(&new_state, serde_json::to_vec(&state).unwrap()).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                &format!("{case}-fixture-import"),
                &new_state,
                format!("{forge} authored state"),
                "application/json".into(),
            )
            .unwrap()
            .project;
        let new_state_asset = project.assets.last().unwrap().clone();

        let mut manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
        manifest["fixture_state_sha256"] =
            serde_json::json!(new_state_asset.content_sha256.clone().unwrap());
        let new_manifest = folder.path().join(format!("{case}-manifest.json"));
        fs::write(&new_manifest, serde_json::to_vec(&manifest).unwrap()).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                &format!("{case}-receipt-import"),
                &new_manifest,
                format!("{forge} evidence receipt"),
                "application/json".into(),
            )
            .unwrap()
            .project;
        let new_manifest_asset = project.assets.last().unwrap();
        let bad = CaptureSourceRecord {
            id: Uuid::new_v4(),
            fixture_state_asset_id: new_state_asset.id,
            fixture_state_sha256: new_state_asset.content_sha256.clone().unwrap(),
            manifest_asset_id: new_manifest_asset.id,
            manifest_sha256: new_manifest_asset.content_sha256.clone().unwrap(),
            ..record.clone()
        };
        let rejection = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("{case}-denied"),
                &Change::RegisterCaptureSource { record: bad },
            )
            .unwrap_err();
        assert!(
            rejection.to_string().contains("fixture"),
            "{case}: {rejection}"
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert!(project.production_design.captures.is_empty());
    }
}
