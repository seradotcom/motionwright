//! Real StudioService + SQLite cases for the new scoped Claim edit.
//! These tests need the contextual Change composition; fixtures make no factual claims.
use motionwright_domain::*;
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
fn fixture(service: &StudioService) -> Project {
    let mut project = service.create_project("Claim source continuity").unwrap();
    for (index, text) in [
        "Same factual text with editable provenance",
        "Other claim retained",
    ]
    .iter()
    .enumerate()
    {
        project = apply(
            service,
            &project,
            &format!("arrange-claim-{index}"),
            Change::AddClaim {
                text: (*text).into(),
                source: Some(SourceReference::ManualNote {
                    label: "Original source".into(),
                }),
                context: "Synthetic source-edit scenario".into(),
                source_revision: Some("source-v1".into()),
            },
        );
    }
    project
}
fn edit(project: &Project, label: &str) -> Change {
    let claim = &project.brief.claims[0];
    let expected = ClaimEditableFields::from(claim);
    let mut replacement = expected.clone();
    replacement.source = Some(SourceReference::ManualNote {
        label: label.into(),
    });
    Change::UpdateClaim {
        claim_id: claim.id,
        expected,
        replacement,
    }
}
fn assert_rejected(service: &StudioService, project: &Project, request: &str, change: &Change) {
    let journal = serde_json::to_value(service.history(project.id, 0, 2048).unwrap()).unwrap();
    assert!(
        service
            .apply(project.id, &project.stamp(), request, change)
            .is_err(),
        "Request {request} must be rejected: {change:?}"
    );
    assert_eq!(service.project(project.id).unwrap(), *project);
    assert_eq!(
        serde_json::to_value(service.history(project.id, 0, 2048).unwrap()).unwrap(),
        journal
    );
}
fn check_closed_database(path: &Path) {
    let db =
        rusqlite::Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    let integrity: String = db
        .query_row("PRAGMA integrity_check", [], |row| row.get(0))
        .unwrap();
    assert_eq!(integrity, "ok");
    let mut stmt = db.prepare("PRAGMA foreign_key_check").unwrap();
    assert!(stmt.query([]).unwrap().next().unwrap().is_none());
}

#[test]
fn exact_source_edit_reopens_with_same_identity_history_and_original_request_receipt() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("studio.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = fixture(&service);
    let source_file = directory.path().join("claim-source.json");
    fs::write(&source_file, br#"{"source":"explicit authored fixture"}"#).unwrap();
    let before = service
        .import_asset_file(
            before.id,
            &before.stamp(),
            "claim-source-import",
            &source_file,
            "Imported source for claim edit".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let source_asset_id = before.assets.last().unwrap().id;
    let expected = ClaimEditableFields::from(&before.brief.claims[0]);
    let mut replacement = expected.clone();
    replacement.source = Some(SourceReference::Asset {
        asset_id: source_asset_id,
    });
    let change = Change::UpdateClaim {
        claim_id: before.brief.claims[0].id,
        expected,
        replacement,
    };
    let result = service
        .apply(before.id, &before.stamp(), "claim-source-original", &change)
        .unwrap();
    assert!(!result.replayed);
    let saved = result.project;
    assert_eq!(saved.revision, before.revision + 1);
    assert_eq!(saved.brief.claims.len(), before.brief.claims.len());
    assert_eq!(saved.brief.claims[0].id, before.brief.claims[0].id);
    assert_eq!(saved.brief.claims[0].text, before.brief.claims[0].text);
    assert_eq!(
        saved.brief.claims[0].source,
        Some(SourceReference::Asset {
            asset_id: source_asset_id,
        })
    );
    assert_eq!(
        saved.brief.claims[0].context,
        before.brief.claims[0].context
    );
    assert_eq!(saved.brief.claims[1], before.brief.claims[1]);
    let journal = service.history(before.id, before.revision, 16).unwrap();
    assert_eq!(journal.len(), 1);
    assert_eq!(journal[0].revision, saved.revision);
    assert_eq!(
        serde_json::to_value(&journal[0].change).unwrap(),
        serde_json::to_value(&change).unwrap()
    );
    drop(service);
    check_closed_database(&path);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(saved.id).unwrap(), saved);
    let receipt = reopened
        .apply(before.id, &before.stamp(), "claim-source-original", &change)
        .unwrap();
    assert!(receipt.replayed);
    assert_eq!(receipt.project, saved);
    assert_eq!(
        reopened
            .history(before.id, before.revision, 16)
            .unwrap()
            .len(),
        1
    );
    // This explicitly addresses the same original request; the UI never retries UNKNOWN automatically.
    let different = edit(&before, "A different operation must not reuse that receipt");
    assert_rejected(&reopened, &saved, "claim-source-original", &different);
}

#[test]
fn another_client_revision_conflicts_even_when_the_claim_preimage_is_still_exact() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("studio.sqlite3");
    let first = StudioService::open(&path).unwrap();
    let before = fixture(&first);
    let second = StudioService::open(&path).unwrap();
    let proposed = edit(&before, "First author's unsaved source");
    let external = apply(
        &second,
        &before,
        "other-client-content",
        Change::SetNarrativePremise {
            premise: "A separate author preserved this narrative premise".into(),
        },
    );
    assert_eq!(external.brief.claims, before.brief.claims);
    assert!(
        first
            .apply(before.id, &before.stamp(), "stale-first-author", &proposed)
            .is_err()
    );
    assert_eq!(first.project(before.id).unwrap(), external);
    let journal = first.history(before.id, before.revision, 16).unwrap();
    assert_eq!(journal.len(), 1);
    assert!(matches!(
        journal[0].change,
        Change::SetNarrativePremise { .. }
    ));
}

#[test]
fn current_revision_does_not_authorize_a_stale_claim_preimage() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("studio.sqlite3")).unwrap();
    let before = fixture(&service);
    let stale = edit(&before, "Obsolete source proposed by the first author");
    let current = apply(
        &service,
        &before,
        "other-source-author",
        edit(&before, "Saved by another author"),
    );
    assert_rejected(&service, &current, "fresh-stamp-stale-preimage", &stale);
}

#[test]
fn a_source_only_change_invalidates_resolved_active_branch_reviews_and_retains_their_history() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("studio.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = fixture(&service);
    let claim_id = project.brief.claims[0].id;
    let main = project.active_branch;
    project = apply(
        &service,
        &project,
        "source-review",
        Change::AddReview {
            kind: ReviewKind::Editorial,
            resource: format!("claim:{claim_id}"),
            body: "Review this source version".into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
    let main_review = project.reviews.last().unwrap().id;
    project = apply(
        &service,
        &project,
        "source-resolution",
        Change::ResolveReview {
            review_id: main_review,
            resolution: "Original source reviewed in its original context".into(),
        },
    );
    project = apply(
        &service,
        &project,
        "source-alternative",
        Change::CreateBranch {
            name: "Separate review branch".into(),
        },
    );
    let alternate = project
        .branches
        .iter()
        .find(|branch| branch.id != main)
        .unwrap()
        .id;
    project = apply(
        &service,
        &project,
        "checkout-alternative",
        Change::CheckoutBranch {
            branch_id: alternate,
        },
    );
    project = apply(
        &service,
        &project,
        "alternate-source-review",
        Change::AddReview {
            kind: ReviewKind::Editorial,
            resource: format!("claim:{claim_id}"),
            body: "Review on another saved branch".into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
    let alternate_review = project.reviews.last().unwrap().id;
    project = apply(
        &service,
        &project,
        "alternate-source-resolution",
        Change::ResolveReview {
            review_id: alternate_review,
            resolution: "Separate branch decision".into(),
        },
    );
    project = apply(
        &service,
        &project,
        "return-main",
        Change::CheckoutBranch { branch_id: main },
    );
    let before = project.clone();
    let saved = apply(
        &service,
        &project,
        "same-text-new-source",
        edit(&project, "New source with identical claim text"),
    );
    assert_eq!(saved.brief.claims[0].text, before.brief.claims[0].text);
    let review = saved
        .reviews
        .iter()
        .find(|value| value.id == main_review)
        .unwrap();
    assert_eq!(review.status, ReviewStatus::NeedsRecheck);
    assert!(review.resolution.is_none());
    assert_eq!(
        saved
            .reviews
            .iter()
            .find(|value| value.id == alternate_review),
        before
            .reviews
            .iter()
            .find(|value| value.id == alternate_review)
    );
    assert!(service.history(saved.id, 0, 2048).unwrap().iter().any(|event| matches!(&event.change,
        Change::ResolveReview { review_id, resolution }
        if *review_id == main_review && resolution == "Original source reviewed in its original context")));
    drop(service);
    assert_eq!(
        StudioService::open(&path)
            .unwrap()
            .project(saved.id)
            .unwrap(),
        saved
    );
    check_closed_database(&path);
}

#[test]
fn content_locks_reject_project_or_target_edits_without_changing_the_saved_project() {
    for project_lock in [true, false] {
        let directory = tempfile::tempdir().unwrap();
        let service = StudioService::open(directory.path().join("studio.sqlite3")).unwrap();
        let before = fixture(&service);
        let resource = if project_lock {
            before.resource_key()
        } else {
            format!("claim:{}", before.brief.claims[0].id)
        };
        let locked = apply(
            &service,
            &before,
            "set-content-lock",
            Change::SetLock {
                resource,
                kind: LockKind::Content,
                note: "Authored content lock".into(),
            },
        );
        assert_rejected(
            &service,
            &locked,
            "locked-source-change",
            &edit(&locked, "Must remain a draft"),
        );
    }
}

#[test]
fn explicit_source_withdrawal_is_durable_and_a_rejected_noop_adds_no_content_event() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("studio.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let before = fixture(&service);
    let expected = ClaimEditableFields::from(&before.brief.claims[0]);
    let mut replacement = expected.clone();
    replacement.source = None;
    replacement.source_revision = None;
    let change = Change::UpdateClaim {
        claim_id: before.brief.claims[0].id,
        expected,
        replacement,
    };
    let saved = apply(&service, &before, "explicit-source-withdrawal", change);
    let fields = ClaimEditableFields::from(&saved.brief.claims[0]);
    assert!(fields.source.is_none() && fields.source_revision.is_none());
    assert_rejected(
        &service,
        &saved,
        "source-noop",
        &Change::UpdateClaim {
            claim_id: saved.brief.claims[0].id,
            expected: fields.clone(),
            replacement: fields,
        },
    );
    drop(service);
    assert_eq!(
        StudioService::open(&path)
            .unwrap()
            .project(saved.id)
            .unwrap(),
        saved
    );
}

// Reuses the existing capture fixture contract without changing its source.
fn capture_fixture(service: &StudioService, dir: &Path) -> (Project, CaptureSourceRecord) {
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
fn immutable_capture_bindings_survive_direct_service_rejections_and_durable_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("studio.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (before, capture) = capture_fixture(&service, directory.path());
    let saved = apply(
        &service,
        &before,
        "register-original-capture",
        Change::RegisterCaptureSource {
            record: capture.clone(),
        },
    );
    let expected = ClaimEditableFields::from(
        saved
            .brief
            .claims
            .iter()
            .find(|claim| claim.id == capture.claim_id)
            .unwrap(),
    );
    for (index, replacement) in [
        ClaimEditableFields {
            source: None,
            ..expected.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::ProjectRevision { revision: 0 }),
            ..expected.clone()
        },
        ClaimEditableFields {
            context: "Cannot turn old capture into current evidence".into(),
            ..expected.clone()
        },
    ]
    .into_iter()
    .enumerate()
    {
        assert_rejected(
            &service,
            &saved,
            &format!("reject-capture-rebind-{index}"),
            &Change::UpdateClaim {
                claim_id: capture.claim_id,
                expected: expected.clone(),
                replacement,
            },
        );
    }
    drop(service);
    check_closed_database(&path);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(saved.id).unwrap(), saved);
    assert_eq!(saved.production_design.captures, vec![capture]);
}

#[test]
fn invalid_fields_assets_future_sources_and_missing_review_targets_have_no_partial_commit() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("studio.sqlite3")).unwrap();
    let project = fixture(&service);
    let expected = ClaimEditableFields::from(&project.brief.claims[0]);
    for (index, replacement) in [
        ClaimEditableFields {
            text: " ".into(),
            ..expected.clone()
        },
        ClaimEditableFields {
            context: "é".repeat(2001),
            ..expected.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::Asset {
                asset_id: Uuid::new_v4(),
            }),
            ..expected.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::ProjectRevision {
                revision: project.revision + 1,
            }),
            ..expected.clone()
        },
    ]
    .into_iter()
    .enumerate()
    {
        assert_rejected(
            &service,
            &project,
            &format!("invalid-source-{index}"),
            &Change::UpdateClaim {
                claim_id: project.brief.claims[0].id,
                expected: expected.clone(),
                replacement,
            },
        );
    }
    assert_rejected(
        &service,
        &project,
        "missing-claim-review",
        &Change::AddReview {
            kind: ReviewKind::Editorial,
            resource: format!("claim:{}", Uuid::new_v4()),
            body: "Cannot review an absent current claim".into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
}
