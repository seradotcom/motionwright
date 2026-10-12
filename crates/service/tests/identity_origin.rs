//! SQLite durable CAS: identity declarations require genuine imported byte
//! identities; revocations are immutable and cannot be replayed as a grant.
//! This does not authenticate a consent signature or run a generation model.
use motionwright_domain::*;
use motionwright_service::StudioService;
use std::fs;
use uuid::Uuid;

const ISSUED: &str = "2026-10-11T00:00:00Z";
const EXPIRES: &str = "2026-11-11T00:00:00Z";
fn prepare(service: &StudioService, dir: &std::path::Path) -> (Project, IdentityOriginRecord) {
    let mut p = service
        .create_project("Source-bound identity and consent")
        .unwrap();
    let original_file = dir.join("original-identity-source.json");
    fs::write(&original_file,
        br#"{"schema":"motionwright.identity-origin/1","kind":"original_fictional","source":"authored internal concept, no modeled person"}"#,
    ).unwrap();
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "authored-original",
            &original_file,
            "Original declared identity provenance".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let manifest = p.assets.last().unwrap().clone();
    let record = IdentityOriginRecord {
        schema: IDENTITY_SOURCE_SCHEMA.into(),
        id: Uuid::new_v4(),
        source_project_id: p.id,
        source_generation: p.generation,
        origin: IdentityOrigin::OriginalFictional,
        display_label: "Original abstract narrator".into(),
        origin_description: "An entirely fictional character designed in this project".into(),
        created_by_declaration: "I authored this original fictional concept".into(),
        created_at_utc: ISSUED.into(),
        expires_at_utc: EXPIRES.into(),
        allowed_modalities: vec![IdentityModality::Voice],
        allowed_purposes: vec![IdentityPurpose::ProductDemonstration],
        provenance_asset_id: manifest.id,
        provenance_sha256: manifest.content_sha256.unwrap(),
        consent_asset_id: None,
        consent_sha256: None,
        consent_subject_declaration: None,
        consent_scope_declaration: None,
        synthetic_disclosure: IDENTITY_DISCLOSURE_FICTIONAL.into(),
    };
    (p, record)
}
#[test]
fn sqlite_cas_persists_source_and_revocation_and_replays_same_authorized_request() {
    let folder = tempfile::tempdir().unwrap();
    let database = folder.path().join("identity-source.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let (p, record) = prepare(&service, folder.path());
    let stamp = p.stamp();
    let change = Change::RegisterIdentityOrigin {
        record: record.clone(),
    };
    let registered = service
        .apply(p.id, &stamp, "register-original", &change)
        .unwrap();
    assert!(!registered.replayed);
    assert_eq!(
        registered.project.production_design.identity_origins,
        vec![record.clone()]
    );
    let replay = service
        .apply(p.id, &stamp, "register-original", &change)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, registered.project);
    assert!(
        service
            .apply(
                p.id,
                &registered.project.stamp(),
                "second-registration",
                &change
            )
            .is_err()
    );
    assert!(
        service
            .apply(
                p.id,
                &registered.project.stamp(),
                "delete-source",
                &Change::RemoveAsset {
                    asset_id: record.provenance_asset_id
                }
            )
            .is_err()
    );
    let revocation = IdentityRevocation {
        id: Uuid::new_v4(),
        identity_id: record.id,
        revoked_at_utc: "2026-10-19T00:00:00Z".into(),
        reason: "The original rights holder withdrew the declaration".into(),
    };
    let revocation_change = Change::RevokeIdentityOrigin {
        revocation: revocation.clone(),
    };
    let revoked = service
        .apply(
            p.id,
            &registered.project.stamp(),
            "revoke-source",
            &revocation_change,
        )
        .unwrap();
    assert_eq!(
        revoked.project.production_design.identity_revocations,
        vec![revocation]
    );
    assert_eq!(
        revoked
            .project
            .review_identity_use(
                record.id,
                "2026-10-20T12:00:00Z",
                IdentityModality::Voice,
                IdentityPurpose::ProductDemonstration
            )
            .unwrap(),
        IdentityReviewOutcome::Revoked
    );
    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), revoked.project);
    assert!(
        reopened
            .apply(
                p.id,
                &revoked.project.stamp(),
                "no-double-revoke",
                &revocation_change
            )
            .is_err()
    );
}
#[test]
fn stale_generation_and_foreign_source_blobs_never_register_any_consent() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("rejected.sqlite3")).unwrap();
    let (p, record) = prepare(&service, folder.path());
    let unknown = IdentityOriginRecord {
        provenance_sha256: "c".repeat(64),
        ..record.clone()
    };
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "foreign-hash",
                &Change::RegisterIdentityOrigin { record: unknown }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    let denied = IdentityOriginRecord {
        source_generation: Uuid::new_v4(),
        ..record.clone()
    };
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "foreign-generation",
                &Change::RegisterIdentityOrigin { record: denied }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    let first = service
        .apply(
            p.id,
            &p.stamp(),
            "first-registration",
            &Change::RegisterIdentityOrigin {
                record: record.clone(),
            },
        )
        .unwrap()
        .project;
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "stale-registration",
                &Change::RegisterIdentityOrigin {
                    record: IdentityOriginRecord {
                        id: Uuid::new_v4(),
                        ..record.clone()
                    }
                }
            )
            .is_err()
    );
    assert!(
        service
            .apply(
                p.id,
                &first.stamp(),
                "reused-source",
                &Change::RegisterIdentityOrigin {
                    record: IdentityOriginRecord {
                        id: Uuid::new_v4(),
                        ..record
                    }
                }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), first);
}
