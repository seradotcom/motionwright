use motionwright_domain::*;
use uuid::Uuid;

const SOURCE_SHA: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const CONSENT_SHA: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
fn attach(project: &mut Project, sha: &str) -> Uuid {
    let id = Uuid::new_v4();
    project
        .apply_change(&Change::AddAsset {
            asset: Asset {
                id,
                name: "Immutable declared original JSON source".into(),
                media_type: "application/json".into(),
                content_sha256: Some(sha.into()),
                source_revision: Some("fixture / not legally verified".into()),
            },
        })
        .unwrap();
    id
}
fn fixture() -> (Project, IdentityOriginRecord) {
    let mut project = Project::new("Original synthetic identity provenance").unwrap();
    let asset = attach(&mut project, SOURCE_SHA);
    let source = IdentityOriginRecord {
        schema: IDENTITY_SOURCE_SCHEMA.into(),
        id: Uuid::new_v4(),
        source_project_id: project.id,
        source_generation: project.generation,
        origin: IdentityOrigin::OriginalFictional,
        display_label: "Original abstract narrator".into(),
        origin_description: "Entirely authored fictional character; no real person modeled".into(),
        created_by_declaration: "I created the original fictional concept and hold its rights"
            .into(),
        created_at_utc: "2026-10-11T00:00:00Z".into(),
        expires_at_utc: "2026-11-11T00:00:00Z".into(),
        allowed_modalities: vec![IdentityModality::Voice, IdentityModality::Presenter],
        allowed_purposes: vec![
            IdentityPurpose::ProductDemonstration,
            IdentityPurpose::Education,
        ],
        provenance_asset_id: asset,
        provenance_sha256: SOURCE_SHA.into(),
        consent_asset_id: None,
        consent_sha256: None,
        consent_subject_declaration: None,
        consent_scope_declaration: None,
        synthetic_disclosure: IDENTITY_DISCLOSURE_FICTIONAL.into(),
    };
    (project, source)
}
#[test]
fn fictional_source_is_content_bound_labeled_and_never_grants_provider_execution() {
    let (mut p, source) = fixture();
    p.apply_change(&Change::RegisterIdentityOrigin {
        record: source.clone(),
    })
    .unwrap();
    assert_eq!(p.production_design.identity_origins, vec![source.clone()]);
    assert_eq!(
        p.review_identity_use(
            source.id,
            "2026-10-20T12:00:00Z",
            IdentityModality::Voice,
            IdentityPurpose::ProductDemonstration
        )
        .unwrap(),
        IdentityReviewOutcome::FictionalSourceNeedsOwnerApproval
    );
    assert_eq!(
        p.review_identity_use(
            source.id,
            "2026-10-20T12:00:00Z",
            IdentityModality::Face,
            IdentityPurpose::Education
        )
        .unwrap(),
        IdentityReviewOutcome::OutsideDeclaredScope
    );
    assert_eq!(
        p.review_identity_use(
            source.id,
            "2026-12-01T00:00:00Z",
            IdentityModality::Voice,
            IdentityPurpose::Education
        )
        .unwrap(),
        IdentityReviewOutcome::OutsideValidPeriod
    );
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(restored, p);
    p.validate().unwrap();
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: source.provenance_asset_id
        })
        .unwrap_err()
        .to_string()
        .contains("identity consent/provenance")
    );
}
#[test]
fn human_likeness_requires_distinct_content_addressed_consent_but_still_abstains() {
    let (mut p, original) = fixture();
    let consent_id = attach(&mut p, CONSENT_SHA);
    let human = IdentityOriginRecord {
        origin: IdentityOrigin::HumanLikeness,
        consent_asset_id: Some(consent_id),
        consent_sha256: Some(CONSENT_SHA.into()),
        consent_subject_declaration: Some(
            "The depicted person named in the imported consent".into(),
        ),
        consent_scope_declaration: Some("Only one educational synthetic presenter study".into()),
        allowed_modalities: vec![IdentityModality::Presenter],
        allowed_purposes: vec![IdentityPurpose::Education],
        synthetic_disclosure: IDENTITY_DISCLOSURE_HUMAN.into(),
        ..original
    };
    let mut rejected = p.clone();
    assert!(
        rejected
            .apply_change(&Change::RegisterIdentityOrigin {
                record: IdentityOriginRecord {
                    consent_asset_id: None,
                    ..human.clone()
                }
            })
            .is_err()
    );
    let mut rejected = p.clone();
    assert!(
        rejected
            .apply_change(&Change::RegisterIdentityOrigin {
                record: IdentityOriginRecord {
                    consent_asset_id: Some(human.provenance_asset_id),
                    ..human.clone()
                }
            })
            .is_err()
    );
    p.apply_change(&Change::RegisterIdentityOrigin {
        record: human.clone(),
    })
    .unwrap();
    assert_eq!(
        p.review_identity_use(
            human.id,
            "2026-10-20T12:00:00Z",
            IdentityModality::Presenter,
            IdentityPurpose::Education
        )
        .unwrap(),
        IdentityReviewOutcome::HumanConsentNeedsIndependentReview
    );
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: consent_id
        })
        .is_err()
    );
}
#[test]
fn revocation_is_append_only_blocks_all_further_uses_and_retains_history() {
    let (mut p, source) = fixture();
    p.apply_change(&Change::RegisterIdentityOrigin {
        record: source.clone(),
    })
    .unwrap();
    let revocation = IdentityRevocation {
        id: Uuid::new_v4(),
        identity_id: source.id,
        revoked_at_utc: "2026-10-19T00:00:00Z".into(),
        reason: "The originating author withdrew permission for this concept".into(),
    };
    p.apply_change(&Change::RevokeIdentityOrigin {
        revocation: revocation.clone(),
    })
    .unwrap();
    assert_eq!(
        p.review_identity_use(
            source.id,
            "2026-10-19T01:00:00Z",
            IdentityModality::Voice,
            IdentityPurpose::Education
        )
        .unwrap(),
        IdentityReviewOutcome::Revoked
    );
    assert_eq!(
        p.production_design.identity_revocations,
        vec![revocation.clone()]
    );
    assert_eq!(p.production_design.identity_origins, vec![source]);
    assert!(
        p.apply_change(&Change::RevokeIdentityOrigin { revocation })
            .is_err()
    );
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(restored, p);
}
#[test]
fn malformed_future_or_cross_project_grants_are_refused_without_source_mutation() {
    let (p, source) = fixture();
    let adversarial = vec![
        IdentityOriginRecord {
            source_project_id: Uuid::new_v4(),
            ..source.clone()
        },
        IdentityOriginRecord {
            source_generation: Uuid::nil(),
            ..source.clone()
        },
        IdentityOriginRecord {
            source_generation: Uuid::new_v4(),
            ..source.clone()
        },
        IdentityOriginRecord {
            provenance_sha256: "c".repeat(64),
            ..source.clone()
        },
        IdentityOriginRecord {
            synthetic_disclosure: "REAL HUMAN IDENTITY VERIFIED".into(),
            ..source.clone()
        },
        IdentityOriginRecord {
            allowed_purposes: vec![IdentityPurpose::Marketing, IdentityPurpose::Marketing],
            ..source.clone()
        },
        IdentityOriginRecord {
            allowed_modalities: vec![],
            ..source.clone()
        },
        IdentityOriginRecord {
            created_at_utc: "2026-12-01T00:00:00Z".into(),
            ..source.clone()
        },
        IdentityOriginRecord {
            expires_at_utc: "2029-11-11T00:00:00Z".into(),
            ..source.clone()
        },
        IdentityOriginRecord {
            created_at_utc: "2026-10-11T01:00:00+01:00".into(),
            ..source.clone()
        },
    ];
    for candidate in adversarial {
        let mut attempt = p.clone();
        assert!(
            attempt
                .apply_change(&Change::RegisterIdentityOrigin { record: candidate })
                .is_err()
        );
        assert_eq!(attempt.production_design.identity_origins.len(), 0);
    }
    let mut value = serde_json::to_value(&source).unwrap();
    value["bypass_human_consent"] = serde_json::json!(true);
    assert!(serde_json::from_value::<IdentityOriginRecord>(value).is_err());
}
#[test]
fn canonical_timestamp_and_origination_generation_survive_portable_import() {
    let (mut p, source) = fixture();
    p.apply_change(&Change::RegisterIdentityOrigin {
        record: source.clone(),
    })
    .unwrap();
    let history = p.clone();
    p.generation = Uuid::new_v4();
    p.validate().unwrap();
    assert_eq!(p.production_design.identity_origins[0], source);
    assert_ne!(p.generation, source.source_generation);
    assert_ne!(p.generation, history.generation);
    let second = IdentityOriginRecord {
        id: Uuid::new_v4(),
        ..source
    };
    assert!(
        p.apply_change(&Change::RegisterIdentityOrigin { record: second })
            .is_err(),
        "A newly registered receipt must use the current CAS generation"
    );
}
#[test]
fn old_format_two_projects_without_new_identity_keys_remain_readable() {
    let (p, _) = fixture();
    let mut data = serde_json::to_value(&p).unwrap();
    let obj = data["production_design"].as_object_mut().unwrap();
    obj.remove("identity_origins");
    obj.remove("identity_revocations");
    let imported: Project = serde_json::from_value(data).unwrap();
    imported.validate().unwrap();
    assert!(imported.production_design.identity_origins.is_empty());
}

#[test]
fn revoked_source_digest_cannot_be_re_registered_with_a_fresh_asset_and_identity_id() {
    let (mut project, source) = fixture();
    project
        .apply_change(&Change::RegisterIdentityOrigin {
            record: source.clone(),
        })
        .unwrap();
    project
        .apply_change(&Change::RevokeIdentityOrigin {
            revocation: IdentityRevocation {
                id: Uuid::new_v4(),
                identity_id: source.id,
                revoked_at_utc: "2026-10-19T00:00:00Z".into(),
                reason: "Original owner revoked this provenance".into(),
            },
        })
        .unwrap();

    // Asset IDs are not content identities: AddAsset permits independent
    // references to the *same* bytes, so the digest must remain revoked.
    let alias_id = attach(&mut project, SOURCE_SHA);
    let attempted_reissue = IdentityOriginRecord {
        id: Uuid::new_v4(),
        provenance_asset_id: alias_id,
        ..source.clone()
    };
    let before = project.clone();
    let refusal = project
        .apply_change(&Change::RegisterIdentityOrigin {
            record: attempted_reissue.clone(),
        })
        .unwrap_err()
        .to_string();
    assert!(refusal.contains("duplicate immutable"), "{refusal}");
    assert_eq!(project, before, "failed registration must be atomic");

    // A forged imported project cannot bypass the same invariant by
    // editing production_design in JSON without executing Change.
    let mut forged = before;
    forged
        .production_design
        .identity_origins
        .push(attempted_reissue);
    assert!(forged.validate().is_err());
    assert_eq!(
        project
            .review_identity_use(
                source.id,
                "2026-10-20T00:00:00Z",
                IdentityModality::Voice,
                IdentityPurpose::Education,
            )
            .unwrap(),
        IdentityReviewOutcome::Revoked
    );
}

#[test]
fn separate_consent_asset_id_cannot_reuse_exact_provenance_bytes() {
    let (mut project, source) = fixture();
    let alias_id = attach(&mut project, SOURCE_SHA);
    let human = IdentityOriginRecord {
        origin: IdentityOrigin::HumanLikeness,
        consent_asset_id: Some(alias_id),
        consent_sha256: Some(SOURCE_SHA.into()),
        consent_subject_declaration: Some("Named depicted subject".into()),
        consent_scope_declaration: Some("One source-reviewed educational prototype".into()),
        allowed_modalities: vec![IdentityModality::Presenter],
        allowed_purposes: vec![IdentityPurpose::Education],
        synthetic_disclosure: IDENTITY_DISCLOSURE_HUMAN.into(),
        ..source
    };
    let unchanged = project.clone();
    let refusal = project
        .apply_change(&Change::RegisterIdentityOrigin { record: human })
        .unwrap_err()
        .to_string();
    assert!(refusal.contains("distinct consent bytes"), "{refusal}");
    assert_eq!(project, unchanged);
}
