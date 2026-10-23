//! Claim source editing preserves identity and rejects invalid proposals atomically.
//! Fixtures are authored test data, not factual or creative acceptance.
use motionwright_domain::*;
use uuid::Uuid;

fn commit(project: &mut Project, change: Change) {
    project.apply_change(&change).unwrap();
    project
        .commit_revision(project.revision + 1, &change)
        .unwrap();
}
fn fixture() -> Project {
    let mut project = Project::new("Editable claim sources").unwrap();
    for text in [
        "The observed interaction has a source",
        "A separate untouched claim",
    ] {
        commit(
            &mut project,
            Change::AddClaim {
                text: text.into(),
                source: Some(SourceReference::ManualNote {
                    label: "Original authored source".into(),
                }),
                context: "Explicit test arrangement".into(),
                source_revision: Some("v1".into()),
            },
        );
    }
    let claim_id = project.brief.claims[0].id;
    commit(
        &mut project,
        Change::AddNarrativeBeat {
            label: "Preserved narrative reference".into(),
            objective: "Explain the observation".into(),
            audience_takeaway: "The source can be revised".into(),
            claim_ids: vec![claim_id],
            preferred_duration: None,
        },
    );
    project
}
fn edit(project: &Project) -> Change {
    let claim = &project.brief.claims[0];
    let expected = ClaimEditableFields::from(claim);
    let mut replacement = expected.clone();
    replacement.source = Some(SourceReference::ManualNote {
        label: "Revised authored source".into(),
    });
    Change::UpdateClaim {
        claim_id: claim.id,
        expected,
        replacement,
    }
}
fn reject_unchanged(project: &mut Project, change: &Change) {
    let before = project.clone();
    assert!(
        project.apply_change(change).is_err(),
        "Proposal must be rejected: {change:?}"
    );
    assert_eq!(
        *project, before,
        "Rejected edit must preserve the entire project"
    );
}

#[test]
fn source_only_edit_preserves_claim_id_order_references_and_unrelated_content() {
    let mut project = fixture();
    let before = project.clone();
    let change = edit(&project);
    project.apply_change(&change).unwrap();
    let mut expected = before;
    if let Change::UpdateClaim { replacement, .. } = change {
        expected.brief.claims[0].source = replacement.source;
    }
    expected.updated_at = project.updated_at;
    assert_eq!(project, expected);
    project.validate().unwrap();

    let asset_id = Uuid::new_v4();
    commit(
        &mut project,
        Change::AddAsset {
            asset: Asset {
                id: asset_id,
                name: "Registered source evidence".into(),
                media_type: "application/json".into(),
                content_sha256: None,
                source_revision: Some("source-v1".into()),
            },
        },
    );
    let before_asset_edit = project.clone();
    let expected = ClaimEditableFields::from(&project.brief.claims[0]);
    let mut replacement = expected.clone();
    replacement.source = Some(SourceReference::Asset { asset_id });
    project
        .apply_change(&Change::UpdateClaim {
            claim_id: project.brief.claims[0].id,
            expected,
            replacement,
        })
        .unwrap();
    let mut expected_project = before_asset_edit;
    expected_project.brief.claims[0].source = Some(SourceReference::Asset { asset_id });
    expected_project.updated_at = project.updated_at;
    assert_eq!(project, expected_project);
    project.validate().unwrap();
}

#[test]
fn invalid_preimage_id_noop_source_and_utf8_limits_are_atomic() {
    let baseline = fixture();
    let original = ClaimEditableFields::from(&baseline.brief.claims[0]);
    let mut proposals = Vec::new();
    proposals.push((Uuid::new_v4(), original.clone(), original.clone()));
    let mut stale = original.clone();
    stale.context = "Stale content".into();
    proposals.push((baseline.brief.claims[0].id, stale, original.clone()));
    proposals.push((
        baseline.brief.claims[0].id,
        original.clone(),
        original.clone(),
    ));
    for replacement in [
        ClaimEditableFields {
            text: " ".into(),
            ..original.clone()
        },
        ClaimEditableFields {
            text: "é".repeat(2001),
            ..original.clone()
        },
        ClaimEditableFields {
            context: "é".repeat(2001),
            ..original.clone()
        },
        ClaimEditableFields {
            source_revision: Some("é".repeat(129)),
            ..original.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::Asset {
                asset_id: Uuid::new_v4(),
            }),
            ..original.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::Url { url: " ".into() }),
            ..original.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::ProjectRevision {
                revision: baseline.revision + 1,
            }),
            ..original.clone()
        },
    ] {
        proposals.push((baseline.brief.claims[0].id, original.clone(), replacement));
    }
    for (claim_id, expected, replacement) in proposals {
        reject_unchanged(
            &mut baseline.clone(),
            &Change::UpdateClaim {
                claim_id,
                expected,
                replacement,
            },
        );
    }
}

#[test]
fn locks_protect_project_or_target_without_blocking_an_unrelated_claim() {
    let baseline = fixture();
    for resource in [
        baseline.resource_key(),
        format!("claim:{}", baseline.brief.claims[0].id),
    ] {
        let mut project = baseline.clone();
        commit(
            &mut project,
            Change::SetLock {
                resource,
                kind: LockKind::Content,
                note: "Preserve authored work".into(),
            },
        );
        let change = edit(&project);
        reject_unchanged(&mut project, &change);
    }
    let mut project = baseline.clone();
    commit(
        &mut project,
        Change::SetLock {
            resource: format!("claim:{}", baseline.brief.claims[1].id),
            kind: LockKind::Content,
            note: "Only the other claim".into(),
        },
    );
    let change = edit(&project);
    project.apply_change(&change).unwrap();
}

#[test]
fn wire_requires_complete_preimage_and_replacement_but_accepts_explicit_null() {
    let change = edit(&fixture());
    let wire = serde_json::to_value(&change).unwrap();
    let roundtrip: Change = serde_json::from_value(wire.clone()).unwrap();
    assert_eq!(serde_json::to_value(roundtrip).unwrap(), wire);
    for parent in ["expected", "replacement"] {
        for field in ["text", "source", "context", "source_revision"] {
            let mut missing = wire.clone();
            missing[parent].as_object_mut().unwrap().remove(field);
            assert!(
                serde_json::from_value::<Change>(missing).is_err(),
                "{parent}.{field} is required"
            );
        }
        let mut extra = wire.clone();
        extra[parent]["permission"] = serde_json::json!("not authority");
        assert!(serde_json::from_value::<Change>(extra).is_err());
    }
    let mut nulls = wire;
    nulls["replacement"]["source"] = serde_json::Value::Null;
    nulls["replacement"]["source_revision"] = serde_json::Value::Null;
    let decoded: Change = serde_json::from_value(nulls).unwrap();
    let mut project = fixture();
    // Only test complete nullable fields here; the fixture has independent IDs.
    if let Change::UpdateClaim {
        expected,
        replacement,
        ..
    } = decoded
    {
        assert!(replacement.source.is_none() && replacement.source_revision.is_none());
        let claim_id = project.brief.claims[0].id;
        assert_eq!(
            expected,
            ClaimEditableFields::from(&project.brief.claims[0])
        );
        project
            .apply_change(&Change::UpdateClaim {
                claim_id,
                expected,
                replacement,
            })
            .unwrap();
    } else {
        panic!("The exact new wire operation was not retained");
    }
}

#[test]
fn source_change_invalidates_resolved_review_even_when_text_does_not_change() {
    let mut project = fixture();
    let resource = format!("claim:{}", project.brief.claims[0].id);
    commit(
        &mut project,
        Change::AddReview {
            kind: ReviewKind::Editorial,
            resource,
            body: "Review the source".into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
    let review_id = project.reviews.last().unwrap().id;
    commit(
        &mut project,
        Change::ResolveReview {
            review_id,
            resolution: "Reviewed original source".into(),
        },
    );
    let before = project.clone();
    let change = edit(&project);
    commit(&mut project, change);
    assert_eq!(project.brief.claims[0].text, before.brief.claims[0].text);
    let review = project
        .reviews
        .iter()
        .find(|review| review.id == review_id)
        .unwrap();
    assert_eq!(review.status, ReviewStatus::NeedsRecheck);
    assert_eq!(review.anchor, before.reviews.last().unwrap().anchor);
    assert!(review.resolution.is_none() && review.resolved_at.is_none());
}

#[test]
fn new_review_requires_an_existing_claim_target() {
    let mut project = fixture();
    reject_unchanged(
        &mut project,
        &Change::AddReview {
            kind: ReviewKind::Editorial,
            resource: format!("claim:{}", Uuid::new_v4()),
            body: "Cannot attach new review to an absent claim".into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
}

// Reuses the existing capture fixture contract without changing its source.
const SCREEN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const RECEIPT: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const COMMIT: &str = "cccccccccccccccccccccccccccccccccccccccc";

fn capture_fixture() -> (Project, CaptureSourceRecord) {
    let mut p = Project::new("Isolated first-party capture").unwrap();
    p.revision = 7;
    let origin = p.revision;
    p.apply_change(&Change::AddClaim {
        text: "The authored Studio Production screen is visible in the pinned first-party fixture"
            .into(),
        context: "synthetic-first-party-capture-validation".into(),
        source: Some(SourceReference::ProjectRevision { revision: origin }),
        source_revision: Some("fixture-7".into()),
    })
    .unwrap();
    let screenshot = Uuid::new_v4();
    let manifest = Uuid::new_v4();
    let state = Uuid::new_v4();
    for (id, name, mime, hash) in [
        (screenshot, "source.png", "image/png", SCREEN),
        (
            manifest,
            "capture-evidence.json",
            "application/json",
            RECEIPT,
        ),
        (
            state,
            "fixture-state.json",
            "application/json",
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        ),
    ] {
        p.apply_change(&Change::AddAsset {
            asset: Asset {
                id,
                name: name.into(),
                media_type: mime.into(),
                content_sha256: Some(hash.into()),
                source_revision: Some("local-import".into()),
            },
        })
        .unwrap();
    }
    let record = CaptureSourceRecord {
        schema: CAPTURE_RECORD_SCHEMA.into(),
        id: Uuid::new_v4(),
        screenshot_asset_id: screenshot,
        screenshot_sha256: SCREEN.into(),
        manifest_asset_id: manifest,
        manifest_sha256: RECEIPT.into(),
        fixture_state_asset_id: state,
        fixture_state_sha256: "f".repeat(64),
        claim_id: p.brief.claims[0].id,
        source_project_id: p.id,
        source_generation: p.generation,
        source_revision: origin,
        app_id: "motionwright-studio".into(),
        app_version: "0.1.0".into(),
        source_commit: COMMIT.into(),
        scenario_id: "production-original-ui-local-v1".into(),
        origin: "http://127.0.0.1:1420".into(),
        route: "/".into(),
        viewport_width: 1440,
        viewport_height: 940,
        device_scale_factor: 1,
        screenshot_repeat_sha256: SCREEN.into(),
        redaction: CaptureMask {
            selector: ".revision-chip".into(),
            x: 100,
            y: 64,
            width: 72,
            height: 28,
            mask_color: "#0B0B0B".into(),
        },
        external_requests: 0,
        credential_fields: 0,
        declared_scope: "first-party-synthetic-fixture-only".into(),
    };
    (p, record)
}

#[test]
fn immutable_capture_source_and_context_reject_edits_without_rebinding_original_evidence() {
    let (mut project, capture) = capture_fixture();
    project
        .apply_change(&Change::RegisterCaptureSource {
            record: capture.clone(),
        })
        .unwrap();
    let claim_id = capture.claim_id;
    let expected = ClaimEditableFields::from(
        project
            .brief
            .claims
            .iter()
            .find(|claim| claim.id == claim_id)
            .unwrap(),
    );
    for replacement in [
        ClaimEditableFields {
            source: None,
            ..expected.clone()
        },
        ClaimEditableFields {
            source: Some(SourceReference::ProjectRevision {
                revision: capture.source_revision - 1,
            }),
            ..expected.clone()
        },
        ClaimEditableFields {
            context: "A different source context".into(),
            ..expected.clone()
        },
    ] {
        reject_unchanged(
            &mut project,
            &Change::UpdateClaim {
                claim_id,
                expected: expected.clone(),
                replacement,
            },
        );
        assert_eq!(project.production_design.captures, vec![capture.clone()]);
    }
}
