//! Owner-local SQLite admission: three trust levels stay distinct:
//! actual imported bytes, declared origin/consent, and ungranted synthesis.
use motionwright_domain::*;
use motionwright_service::StudioService;
use std::{fs, path::Path};
use uuid::Uuid;

const ISSUED: &str = "2026-10-11T00:00:00Z";
const EXPIRES: &str = "2026-11-11T00:00:00Z";
const DESCRIPTION: &str = "An entirely fictional original created for this project";
const DECLARATION: &str = "I authored the original source and declare its usage scope";

fn prepare(service: &StudioService, dir: &Path, human: bool) -> (Project, IdentityOriginRecord) {
    let mut p = service
        .create_project("Authenticated project identity declarations")
        .unwrap();
    let id = Uuid::new_v4();
    let (label, origin, disclosure) = if human {
        (
            "Recorded human consent (unverified signature)",
            "human_likeness",
            IDENTITY_DISCLOSURE_HUMAN,
        )
    } else {
        (
            "Original narrator",
            "original_fictional",
            IDENTITY_DISCLOSURE_FICTIONAL,
        )
    };
    let modalities = if human {
        vec!["presenter"]
    } else {
        vec!["voice"]
    };
    let purposes = if human {
        vec!["education"]
    } else {
        vec!["product_demonstration"]
    };
    let (consent_id, consent_digest) = if human {
        let consent = dir.join("human-consent-declaration.json");
        let data = serde_json::json!({
            "schema":"motionwright.identity-consent-declaration/1",
            "identity_id":id,
            "subject_declaration":"The subject as named in an independently reviewed authorization",
            "scope_declaration":"One explicit educational presentation only",
            "created_at_utc":ISSUED,
            "expires_at_utc":EXPIRES,
            "allowed_modalities":modalities,
            "allowed_purposes":purposes,
        });
        fs::write(&consent, serde_json::to_vec_pretty(&data).unwrap()).unwrap();
        p = service
            .import_asset_file(
                p.id,
                &p.stamp(),
                "consent-json",
                &consent,
                "Separate source consent declaration".into(),
                "application/json".into(),
            )
            .unwrap()
            .project;
        let asset = p.assets.last().unwrap();
        (Some(asset.id), asset.content_sha256.clone())
    } else {
        (None, None)
    };
    let source = dir.join("identity-provenance.json");
    let manifest = serde_json::json!({
        "schema":"motionwright.identity-origin-evidence/1",
        "id":id,
        "source_project_id":p.id,
        "source_generation":p.generation,
        "origin":origin,
        "display_label":label,
        "origin_description":DESCRIPTION,
        "created_by_declaration":DECLARATION,
        "created_at_utc":ISSUED,
        "expires_at_utc":EXPIRES,
        "allowed_modalities":modalities,
        "allowed_purposes":purposes,
        "consent_sha256":consent_digest,
        "synthetic_disclosure":disclosure,
    });
    fs::write(&source, serde_json::to_vec_pretty(&manifest).unwrap()).unwrap();
    p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "identity-provenance",
            &source,
            "Owner-authored identity source JSON".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let asset = p.assets.last().unwrap();
    let record = IdentityOriginRecord {
        schema: IDENTITY_SOURCE_SCHEMA.into(),
        id,
        source_project_id: p.id,
        source_generation: p.generation,
        origin: if human {
            IdentityOrigin::HumanLikeness
        } else {
            IdentityOrigin::OriginalFictional
        },
        display_label: label.into(),
        origin_description: DESCRIPTION.into(),
        created_by_declaration: DECLARATION.into(),
        created_at_utc: ISSUED.into(),
        expires_at_utc: EXPIRES.into(),
        allowed_modalities: if human {
            vec![IdentityModality::Presenter]
        } else {
            vec![IdentityModality::Voice]
        },
        allowed_purposes: if human {
            vec![IdentityPurpose::Education]
        } else {
            vec![IdentityPurpose::ProductDemonstration]
        },
        provenance_asset_id: asset.id,
        provenance_sha256: asset.content_sha256.clone().unwrap(),
        consent_asset_id: consent_id,
        consent_sha256: consent_digest,
        consent_subject_declaration: human
            .then(|| "The subject as named in an independently reviewed authorization".into()),
        consent_scope_declaration: human
            .then(|| "One explicit educational presentation only".into()),
        synthetic_disclosure: disclosure.into(),
    };
    (p, record)
}
#[test]
fn exact_imported_fictional_provenance_survives_cas_replay_and_revocation() {
    let folder = tempfile::tempdir().unwrap();
    let database = folder.path().join("identity.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let (p, record) = prepare(&service, folder.path(), false);
    let stamp = p.stamp();
    let request = Change::RegisterIdentityOrigin {
        record: record.clone(),
    };
    let first = service
        .apply(p.id, &stamp, "register-original", &request)
        .unwrap();
    assert!(!first.replayed);
    assert_eq!(
        first.project.production_design.identity_origins,
        vec![record.clone()]
    );
    let repeated = service
        .apply(p.id, &stamp, "register-original", &request)
        .unwrap();
    assert!(repeated.replayed);
    assert_eq!(repeated.project, first.project);
    assert!(
        service
            .apply(
                p.id,
                &first.project.stamp(),
                "repeated-source",
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
                &first.project.stamp(),
                "delete-origin",
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
        reason: "Author withdrew the original declaration".into(),
    };
    let change = Change::RevokeIdentityOrigin {
        revocation: revocation.clone(),
    };
    let revoked = service
        .apply(p.id, &first.project.stamp(), "revoke", &change)
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
                "2026-10-20T00:00:00Z",
                IdentityModality::Voice,
                IdentityPurpose::ProductDemonstration
            )
            .unwrap(),
        IdentityReviewOutcome::Revoked
    );
    drop(service);
    let opened = StudioService::open(&database).unwrap();
    assert_eq!(opened.project(p.id).unwrap(), revoked.project);
}
#[test]
fn content_digest_matching_a_forged_declared_source_is_not_sufficient() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("false-source.sqlite3")).unwrap();
    let (p, record) = prepare(&service, folder.path(), false);
    let old = p.clone();
    let source = folder.path().join("identity-provenance.json");
    let mut wrong: serde_json::Value = serde_json::from_slice(&fs::read(&source).unwrap()).unwrap();
    wrong["allowed_purposes"] = serde_json::json!(["marketing"]);
    let forged = folder.path().join("rewritten-source.json");
    fs::write(&forged, serde_json::to_vec(&wrong).unwrap()).unwrap();
    let revised = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "wrong-json",
            &forged,
            "A genuine blob with false permissions".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let imported = revised.assets.last().unwrap();
    let proposed = IdentityOriginRecord {
        provenance_asset_id: imported.id,
        provenance_sha256: imported.content_sha256.clone().unwrap(),
        ..record
    };
    let error = service
        .apply(
            p.id,
            &revised.stamp(),
            "untrue-source",
            &Change::RegisterIdentityOrigin { record: proposed },
        )
        .unwrap_err();
    assert!(error.to_string().contains("imported identity bytes differ"));
    assert_eq!(service.project(p.id).unwrap(), revised);
    assert_eq!(old.production_design.identity_origins.len(), 0);
}
#[test]
fn human_consent_bytes_must_match_subject_and_scope_and_never_grant_synthesis() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("human-consent.sqlite3")).unwrap();
    let (p, record) = prepare(&service, folder.path(), true);
    let entered = service
        .apply(
            p.id,
            &p.stamp(),
            "register-human",
            &Change::RegisterIdentityOrigin {
                record: record.clone(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(
        entered
            .review_identity_use(
                record.id,
                "2026-10-20T00:00:00Z",
                IdentityModality::Presenter,
                IdentityPurpose::Education
            )
            .unwrap(),
        IdentityReviewOutcome::HumanConsentNeedsIndependentReview
    );
    let mut tampered = record.clone();
    tampered.id = Uuid::new_v4();
    assert!(
        service
            .apply(
                p.id,
                &entered.stamp(),
                "consent-reused",
                &Change::RegisterIdentityOrigin { record: tampered }
            )
            .is_err()
    );
    assert!(
        service
            .apply(
                p.id,
                &entered.stamp(),
                "delete-consent",
                &Change::RemoveAsset {
                    asset_id: record.consent_asset_id.unwrap()
                }
            )
            .is_err()
    );
}
#[test]
fn unrelated_foreign_or_stale_requests_do_not_reveal_source_blob_existence() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("gated.sqlite3")).unwrap();
    let (p, record) = prepare(&service, folder.path(), false);
    let forged = IdentityOriginRecord {
        provenance_sha256: "c".repeat(64),
        ..record.clone()
    };
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "unknown-blob",
                &Change::RegisterIdentityOrigin { record: forged }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    let accepted = service
        .apply(
            p.id,
            &p.stamp(),
            "accepted",
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
                "old-revision",
                &Change::RegisterIdentityOrigin {
                    record: IdentityOriginRecord {
                        id: Uuid::new_v4(),
                        ..record
                    }
                }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), accepted);
}
#[test]
fn imported_human_consent_with_forged_scope_cannot_override_recorded_limits() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("forged-consent.sqlite3")).unwrap();
    let (p, record) = prepare(&service, folder.path(), true);
    let original = folder.path().join("human-consent-declaration.json");
    let mut altered: serde_json::Value =
        serde_json::from_slice(&fs::read(&original).unwrap()).unwrap();
    altered["scope_declaration"] =
        serde_json::json!("A different broad permission not present in the receipt");
    let forged_consent = folder.path().join("forged-consent-source.json");
    fs::write(&forged_consent, serde_json::to_vec(&altered).unwrap()).unwrap();
    let p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "forged-consent",
            &forged_consent,
            "Imported contradictory scope".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let consent = p.assets.last().unwrap().clone();
    let source_file = folder.path().join("identity-provenance.json");
    let mut origin: serde_json::Value =
        serde_json::from_slice(&fs::read(&source_file).unwrap()).unwrap();
    origin["consent_sha256"] = serde_json::json!(consent.content_sha256.clone().unwrap());
    let forged_source = folder.path().join("forged-provenance.json");
    fs::write(&forged_source, serde_json::to_vec(&origin).unwrap()).unwrap();
    let p = service
        .import_asset_file(
            p.id,
            &p.stamp(),
            "forged-origin",
            &forged_source,
            "Imported matching SHA with conflicting consent meaning".into(),
            "application/json".into(),
        )
        .unwrap()
        .project;
    let provenance = p.assets.last().unwrap();
    let candidate = IdentityOriginRecord {
        provenance_asset_id: provenance.id,
        provenance_sha256: provenance.content_sha256.clone().unwrap(),
        consent_asset_id: Some(consent.id),
        consent_sha256: consent.content_sha256,
        ..record
    };
    let denied = service
        .apply(
            p.id,
            &p.stamp(),
            "forged-human-scope",
            &Change::RegisterIdentityOrigin { record: candidate },
        )
        .unwrap_err();
    assert!(
        denied
            .to_string()
            .contains("human consent bytes contradict")
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(p.production_design.identity_origins.is_empty());
}
