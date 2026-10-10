use motionwright_creative_library::{
    ClaimReviewStatus, CreativeDirection, CreativeDirectionShot, CreativeDirectionStudyRequest,
    CreativeEvidenceKind, CreativeReferenceStudy, CreativeRhythm, NarrativeStructure,
    analyze_creative_directions, direction_project_digest,
};
use motionwright_domain::{Asset, Change, Claim, Project, SourceReference};
use uuid::Uuid;
fn id(n: u128) -> Uuid {
    Uuid::from_u128(n)
}
fn project() -> Project {
    let mut p = Project::new("Owner source study").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Owned graphic".into(),
        objective: "Explain real editor operation".into(),
        duration_seconds: 3,
    })
    .unwrap();
    p.brief.objective = "Explain an original action without invented product behavior".into();
    p.brief.audience = "Skeptical technical creative director".into();
    p.assets = [
        Asset {
            id: id(5),
            name: "Original product capture".into(),
            media_type: "image/png".into(),
            content_sha256: Some("ab".repeat(32)),
            source_revision: Some("build-1".into()),
        },
        Asset {
            id: id(6),
            name: "Authorized original brand film".into(),
            media_type: "video/mp4".into(),
            content_sha256: Some("cd".repeat(32)),
            source_revision: Some("build-1".into()),
        },
    ]
    .to_vec();
    p.brief.claims.push(Claim {
        id: id(10),
        text: "An owner-visible software action".into(),
        source: Some(SourceReference::Asset { asset_id: id(5) }),
        context: "This particular source capture, not a generic mock UI".into(),
        source_revision: Some("build-1".into()),
    });
    p.validate().unwrap();
    p
}
fn reference(num: u128, asset_id: Uuid, digest: &str) -> CreativeReferenceStudy {
    CreativeReferenceStudy {
        id: id(num),
        asset_id,
        content_sha256: digest.repeat(32),
        source_revision: "build-1".into(),
        owner_usage_note: "Original owner authorizes design study only".into(),
        owner_attests_licensed_use: true,
        observed_hierarchy: "Headline and one software action".into(),
        observed_framing: "Crop on original product interaction".into(),
        observed_transition: "Held evidence reveal".into(),
        observed_rhythm: "One deliberate timed beat".into(),
        originality_constraint: "Do not imitate this author's layout".into(),
    }
}
fn concept(
    n: u128,
    structure: NarrativeStructure,
    rhythm: CreativeRhythm,
    metaphor: &str,
    scene: Uuid,
    reference: Uuid,
    kind: CreativeEvidenceKind,
    asset: Option<Uuid>,
) -> CreativeDirection {
    CreativeDirection {
        id: id(n),
        title: format!("Original concept {n}"),
        metaphor: metaphor.into(),
        structure,
        rhythm,
        distinct_visual_argument: "Change meaning and scene structure, not palette".into(),
        shot_studies: vec![CreativeDirectionShot {
            scene_id: scene,
            narrative_purpose: "Distinguish action and illustration".into(),
            audience_takeaway: "See what is evidence and what is a visual metaphor".into(),
            claim_ids: vec![id(10)],
            evidence_asset_id: asset,
            evidence_kind: kind,
        }],
        reference_ids: vec![reference],
    }
}
fn request(p: &Project) -> CreativeDirectionStudyRequest {
    CreativeDirectionStudyRequest {
        schema: "motionwright.creative-direction-study/1".into(),
        project_id: p.id,
        generation: p.generation,
        revision: p.revision,
        project_sha256: direction_project_digest(p).unwrap(),
        product_version: "build-1".into(),
        references: vec![reference(100, id(5), "ab"), reference(101, id(6), "cd")],
        alternatives: vec![
            concept(
                200,
                NarrativeStructure::ProblemActionOutcome,
                CreativeRhythm::Accelerating,
                "Journey to source evidence",
                p.scenes[0].id,
                id(100),
                CreativeEvidenceKind::RealProductCapture,
                Some(id(5)),
            ),
            concept(
                201,
                NarrativeStructure::BeforeAfter,
                CreativeRhythm::Deliberate,
                "Before/after visual mirror",
                p.scenes[0].id,
                id(101),
                CreativeEvidenceKind::GraphicIllustration,
                None,
            ),
        ],
    }
}
#[test]
fn distinct_concepts_share_one_source_but_not_a_fabricated_preference() {
    let p = project();
    let req = request(&p);
    let a = analyze_creative_directions(&p, &req).unwrap();
    assert_eq!(a, analyze_creative_directions(&p, &req).unwrap());
    assert_eq!(a.candidate_reviews.len(), 2);
    assert!(a.concept_differences_validated);
    assert!(a.winning_concept_id.is_none());
    assert!(
        !a.renderer_executed
            && !a.project_was_modified
            && !a.reference_media_bytes_opened
            && !a.claim_truth_independently_verified
    );
    assert!(
        a.candidate_reviews
            .iter()
            .all(|r| !r.human_creative_approved
                && !r.actual_native_pixels_reviewed
                && !r.real_product_evidence_approved)
    );
    assert_eq!(
        a.candidate_reviews[0].claim_reviews[0].status,
        ClaimReviewStatus::SourceBoundNeedsHumanVerification
    );
    assert_eq!(
        a.candidate_reviews[1].claim_reviews[0].status,
        ClaimReviewStatus::IllustrationIsNotEvidence
    );
}
#[test]
fn new_product_version_marks_source_dependent_claims_stale() {
    let p = project();
    let mut req = request(&p);
    req.product_version = "build-2".into();
    let report = analyze_creative_directions(&p, &req).unwrap();
    assert_eq!(
        report.candidate_reviews[0].claim_reviews[0].status,
        ClaimReviewStatus::StaleProductRevision
    );
    assert!(!report.claim_truth_independently_verified);
}
#[test]
fn palette_variants_are_not_distinct_metaphor_structure_rhythm() {
    let p = project();
    let mut req = request(&p);
    req.alternatives[1].metaphor = req.alternatives[0].metaphor.clone();
    assert!(analyze_creative_directions(&p, &req).is_err());
    req = request(&p);
    req.alternatives[1].structure = req.alternatives[0].structure;
    assert!(analyze_creative_directions(&p, &req).is_err());
    req = request(&p);
    req.alternatives[1].rhythm = req.alternatives[0].rhythm;
    assert!(analyze_creative_directions(&p, &req).is_err());
}
#[test]
fn changed_project_brief_or_generation_rejects_stale_analysis() {
    let mut p = project();
    let req = request(&p);
    p.brief.objective.push_str(" after revision");
    assert!(analyze_creative_directions(&p, &req).is_err());
    p = project();
    let mut req = request(&p);
    req.generation = id(999);
    assert!(analyze_creative_directions(&p, &req).is_err());
    req = request(&p);
    req.revision += 1;
    assert!(analyze_creative_directions(&p, &req).is_err());
}
#[test]
fn unlicensed_unanalyzed_media_does_not_become_a_product_claim() {
    let p = project();
    let mut req = request(&p);
    req.references[0].content_sha256 = "ee".repeat(32);
    assert!(analyze_creative_directions(&p, &req).is_err());
    req = request(&p);
    req.references[0].owner_attests_licensed_use = false;
    assert!(analyze_creative_directions(&p, &req).is_err());
    req = request(&p);
    req.alternatives[1].shot_studies[0].evidence_kind = CreativeEvidenceKind::RealProductCapture;
    assert!(analyze_creative_directions(&p, &req).is_err());
}
#[test]
fn unsourced_claim_cannot_be_verified_even_with_a_real_capture() {
    let mut p = project();
    p.brief.claims[0].source = None;
    let req = request(&p);
    assert_eq!(
        analyze_creative_directions(&p, &req)
            .unwrap()
            .candidate_reviews[0]
            .claim_reviews[0]
            .status,
        ClaimReviewStatus::UnsourcedProductClaim
    );
    p = project();
    p.brief.claims[0].source = Some(SourceReference::ManualNote {
        label: "Somebody says so".into(),
    });
    let req = request(&p);
    assert_eq!(
        analyze_creative_directions(&p, &req)
            .unwrap()
            .candidate_reviews[0]
            .claim_reviews[0]
            .status,
        ClaimReviewStatus::UnsupportedClaimSource
    );
}
#[test]
fn schema_cannot_grant_owner_preference_or_source_execution() {
    let p = project();
    let original = serde_json::to_value(request(&p)).unwrap();
    let mut forged = original.clone();
    forged["publish_now"] = serde_json::json!(true);
    assert!(serde_json::from_value::<CreativeDirectionStudyRequest>(forged).is_err());
    let mut forged = original;
    forged["alternatives"][0]["preferred_by_user"] = serde_json::json!(true);
    assert!(serde_json::from_value::<CreativeDirectionStudyRequest>(forged).is_err());
}

#[test]
fn licensed_generic_footage_cannot_be_promoted_to_a_real_software_action() {
    let p = project();
    let mut request = request(&p);
    let shot = &mut request.alternatives[1].shot_studies[0];
    shot.evidence_kind = CreativeEvidenceKind::LicensedFootage;
    shot.evidence_asset_id = Some(id(5));
    request.alternatives[1].reference_ids = vec![id(100)];
    let result = analyze_creative_directions(&p, &request).unwrap();
    assert_eq!(
        result.candidate_reviews[1].claim_reviews[0].status,
        ClaimReviewStatus::UnsupportedClaimSource
    );
    assert!(!result.candidate_reviews[1].claim_reviews[0].verified_product_behavior);
}
#[test]
fn real_capture_kind_without_an_explicit_brief_claim_is_not_an_evidence_plan() {
    let p = project();
    let mut request = request(&p);
    request.alternatives[0].shot_studies[0].claim_ids.clear();
    assert!(analyze_creative_directions(&p, &request).is_err());
}

#[test]
fn selected_direction_is_still_unapproved_after_canonical_domain_change_validation() {
    use motionwright_creative_library::propose_selected_direction_plan;
    let p = project();
    let req = request(&p);
    let plan = propose_selected_direction_plan(&p, &req, id(200)).unwrap();
    assert!(plan.approval.is_none());
    assert_eq!(plan.shots[0].claim_ids, vec![id(10)]);
    assert_eq!(plan.shots[0].asset_ids, vec![id(5)]);
    assert_eq!(plan.clock, motionwright_domain::ProductionClock::Timeline);
    plan.validate(&p.scenes, &p.assets, &p.brief, &p.audio)
        .unwrap();
    let mut saved = p.clone();
    saved
        .apply_change(&Change::SetProductionPlan {
            plan: Some(plan.clone()),
        })
        .unwrap();
    assert_eq!(saved.production_design.plan, Some(plan));
    assert!(
        p.production_design.plan.is_none(),
        "Pure source preflight changed its input"
    );
}
#[test]
fn canonical_plan_must_not_launder_claims_through_generic_licensed_footage() {
    use motionwright_creative_library::propose_selected_direction_plan;
    let p = project();
    let mut req = request(&p);
    req.alternatives[1].shot_studies[0].evidence_kind = CreativeEvidenceKind::LicensedFootage;
    req.alternatives[1].shot_studies[0].evidence_asset_id = Some(id(5));
    req.alternatives[1].reference_ids = vec![id(100)];
    assert!(propose_selected_direction_plan(&p, &req, id(201)).is_err());
    req.alternatives[1].shot_studies[0].claim_ids.clear();
    let plan = propose_selected_direction_plan(&p, &req, id(201)).unwrap();
    assert!(plan.approval.is_none());
    assert_eq!(plan.shots[0].asset_ids, vec![id(5)]);
    assert!(plan.shots[0].claim_ids.is_empty());
    assert_eq!(
        plan.shots[0].evidence_kind,
        motionwright_domain::NarrativeEvidenceKind::LicensedFootage
    );
}
#[test]
fn stale_claim_version_and_illustrative_claim_cannot_be_committed_by_model() {
    use motionwright_creative_library::propose_selected_direction_plan;
    let p = project();
    let mut req = request(&p);
    req.product_version = "build-2".into();
    assert!(propose_selected_direction_plan(&p, &req, id(200)).is_err());
    req = request(&p);
    assert!(
        propose_selected_direction_plan(&p, &req, id(201)).is_err(),
        "Illustration with an actual product claim cannot enter an evidence plan"
    );
    req = request(&p);
    req.alternatives[0].shot_studies[0].claim_ids.clear();
    assert!(
        propose_selected_direction_plan(&p, &req, id(200)).is_err(),
        "Real capture needs explicit owner brief claim"
    );
}
#[test]
fn an_unknown_owner_selection_id_never_creates_an_approved_plan() {
    use motionwright_creative_library::propose_selected_direction_plan;
    let p = project();
    let req = request(&p);
    assert!(propose_selected_direction_plan(&p, &req, id(999)).is_err());
}
