//! Evidence-first creative direction over the canonical Project and Brief.
//! A digest-bound advisory trial, not a second scheduler, pixel analysis,
//! source-license verifier, model preference or renderer execution authority.
use crate::*;
use motionwright_domain::{Project, SourceReference};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum NarrativeStructure {
    ProblemActionOutcome,
    BeforeAfter,
    CausalWalkthrough,
    EditorialReveal,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum CreativeRhythm {
    Deliberate,
    Accelerating,
    Staccato,
    Continuous,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CreativeEvidenceKind {
    GraphicIllustration,
    RealProductCapture,
    LicensedFootage,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeReferenceStudy {
    pub id: Uuid,
    pub asset_id: Uuid,
    pub content_sha256: String,
    pub source_revision: String,
    pub owner_usage_note: String,
    pub owner_attests_licensed_use: bool,
    pub observed_hierarchy: String,
    pub observed_framing: String,
    pub observed_transition: String,
    pub observed_rhythm: String,
    pub originality_constraint: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeDirectionShot {
    pub scene_id: Uuid,
    pub narrative_purpose: String,
    pub audience_takeaway: String,
    pub claim_ids: Vec<Uuid>,
    pub evidence_asset_id: Option<Uuid>,
    pub evidence_kind: CreativeEvidenceKind,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeDirection {
    pub id: Uuid,
    pub title: String,
    pub metaphor: String,
    pub structure: NarrativeStructure,
    pub rhythm: CreativeRhythm,
    pub distinct_visual_argument: String,
    pub shot_studies: Vec<CreativeDirectionShot>,
    pub reference_ids: Vec<Uuid>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeDirectionStudyRequest {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub project_sha256: String,
    pub product_version: String,
    pub references: Vec<CreativeReferenceStudy>,
    pub alternatives: Vec<CreativeDirection>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ClaimReviewStatus {
    IllustrationIsNotEvidence,
    UnsourcedProductClaim,
    UnsupportedClaimSource,
    SourceBoundNeedsHumanVerification,
    StaleProductRevision,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DirectionClaimReview {
    pub scene_id: Uuid,
    pub claim_id: Uuid,
    pub source_asset_id: Option<Uuid>,
    pub source_sha256: Option<String>,
    pub source_version: Option<String>,
    pub current_product_version: String,
    pub status: ClaimReviewStatus,
    pub verified_product_behavior: bool,
    pub independently_verified_rights: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeDirectionCandidateReview {
    pub concept_id: Uuid,
    pub concept_sha256: String,
    pub reference_analysis_sha256: String,
    pub claim_reviews: Vec<DirectionClaimReview>,
    pub owner_selected: bool,
    pub human_creative_approved: bool,
    pub actual_native_pixels_reviewed: bool,
    pub real_product_evidence_approved: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeDirectionStudyReport {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub project_sha256: String,
    pub study_sha256: String,
    pub product_version: String,
    pub candidate_reviews: Vec<CreativeDirectionCandidateReview>,
    pub concept_differences_validated: bool,
    pub reference_media_bytes_opened: bool,
    pub rights_independently_verified: bool,
    pub claim_truth_independently_verified: bool,
    pub winning_concept_id: Option<Uuid>,
    pub project_was_modified: bool,
    pub renderer_executed: bool,
    pub production_approval: String,
}
pub fn direction_project_digest(project: &Project) -> Result<String> {
    // Includes brief, narrative, source assets, scenes, revisions and locks.
    canonical_digest(project)
}
fn media(project: &Project, id: Uuid) -> Option<&motionwright_domain::Asset> {
    project.assets.iter().find(|asset| asset.id == id)
}
fn review_claim(
    project: &Project,
    shot: &CreativeDirectionShot,
    id: Uuid,
    product_version: &str,
) -> DirectionClaimReview {
    let claim = project.brief.claims.iter().find(|claim| claim.id == id);
    let src = shot
        .evidence_asset_id
        .and_then(|asset_id| media(project, asset_id));
    let sha = src.and_then(|asset| asset.content_sha256.clone());
    let source_version = src.and_then(|asset| asset.source_revision.clone());
    let status = match (shot.evidence_kind, claim) {
        (CreativeEvidenceKind::GraphicIllustration, _) => {
            ClaimReviewStatus::IllustrationIsNotEvidence
        }
        // Licensed generic footage is visual material, not proof of the
        // behavior or version of somebody else's software.
        (CreativeEvidenceKind::LicensedFootage, _) => ClaimReviewStatus::UnsupportedClaimSource,
        (_, None) => ClaimReviewStatus::UnsourcedProductClaim,
        (_, Some(claim)) => match &claim.source {
            None => ClaimReviewStatus::UnsourcedProductClaim,
            Some(SourceReference::Asset { asset_id })
                if Some(*asset_id) == shot.evidence_asset_id =>
            {
                if claim.source_revision.as_deref() == Some(product_version)
                    && source_version.as_deref() == Some(product_version)
                    && sha.is_some()
                {
                    ClaimReviewStatus::SourceBoundNeedsHumanVerification
                } else {
                    ClaimReviewStatus::StaleProductRevision
                }
            }
            _ => ClaimReviewStatus::UnsupportedClaimSource,
        },
    };
    DirectionClaimReview {
        scene_id: shot.scene_id,
        claim_id: id,
        source_asset_id: shot.evidence_asset_id,
        source_sha256: sha,
        source_version,
        current_product_version: product_version.into(),
        status,
        verified_product_behavior: false,
        independently_verified_rights: false,
    }
}
pub fn analyze_creative_directions(
    project: &Project,
    request: &CreativeDirectionStudyRequest,
) -> Result<CreativeDirectionStudyReport> {
    check(
        request.schema == "motionwright.creative-direction-study/1",
        "Unknown creative direction study schema",
    )?;
    check(
        project.id == request.project_id
            && project.generation == request.generation
            && project.revision == request.revision,
        "Direction source belongs to another project/generation/revision",
    )?;
    let digest = direction_project_digest(project)?;
    check(
        valid_sha(&request.project_sha256) && digest == request.project_sha256,
        "Creative direction source fingerprint is stale",
    )?;
    text(
        &request.product_version,
        160,
        "Owner-declared product version is missing",
    )?;
    text(&project.brief.objective, 8000, "Brief objective is missing")?;
    text(&project.brief.audience, 2000, "Brief audience is missing")?;
    check(
        (1..=16).contains(&request.references.len()),
        "Reference analysis needs one to 16 source references",
    )?;
    check(
        (2..=4).contains(&request.alternatives.len()),
        "Concept comparison needs two to four alternatives",
    )?;
    let mut refs = BTreeMap::new();
    for reference in &request.references {
        check(
            refs.insert(reference.id, reference).is_none(),
            "Creative reference identity repeated",
        )?;
        let src = media(project, reference.asset_id)
            .ok_or_else(|| CraftError("Reference asset absent from exact project".into()))?;
        check(
            valid_sha(&reference.content_sha256)
                && src.content_sha256.as_deref() == Some(reference.content_sha256.as_str())
                && src.source_revision.as_deref() == Some(reference.source_revision.as_str()),
            "Reference asset bytes or version have changed",
        )?;
        check(
            src.media_type.starts_with("image/") || src.media_type.starts_with("video/"),
            "Reference must be an imported digest-bound image/video",
        )?;
        check(
            reference.owner_attests_licensed_use,
            "Reference requires explicit owner-attested usage terms",
        )?;
        for (value, maximum, role) in [
            (&reference.source_revision, 160, "Reference version"),
            (&reference.owner_usage_note, 1000, "Reference rights"),
            (&reference.observed_hierarchy, 1000, "Observed hierarchy"),
            (&reference.observed_framing, 1000, "Observed framing"),
            (&reference.observed_transition, 1000, "Observed transition"),
            (&reference.observed_rhythm, 1000, "Observed rhythm"),
            (
                &reference.originality_constraint,
                1200,
                "Originality constraint",
            ),
        ] {
            text(value, maximum, role)?;
        }
    }
    let (mut concept_ids, mut structures, mut rhythms, mut metaphors) = (
        BTreeSet::new(),
        BTreeSet::new(),
        BTreeSet::new(),
        BTreeSet::new(),
    );
    let mut reviews = Vec::new();
    for concept in &request.alternatives {
        check(concept_ids.insert(concept.id), "Concept ID was duplicated")?;
        check(
            structures.insert(concept.structure)
                && rhythms.insert(concept.rhythm)
                && metaphors.insert(concept.metaphor.trim().to_lowercase()),
            "Concepts must differ in metaphor, structure and rhythm; colorway changes are insufficient",
        )?;
        for (value, maximum, role) in [
            (&concept.title, 200, "Concept title"),
            (&concept.metaphor, 800, "Concept metaphor"),
            (
                &concept.distinct_visual_argument,
                2000,
                "Original direction rationale",
            ),
        ] {
            text(value, maximum, role)?;
        }
        check(
            (1..=32).contains(&concept.shot_studies.len()),
            "Concept needs one to 32 real-scene shot plans",
        )?;
        check(
            (1..=16).contains(&concept.reference_ids.len()),
            "Each concept must cite real, analyzed references",
        )?;
        let mut used = BTreeSet::new();
        for id in &concept.reference_ids {
            check(
                refs.contains_key(id) && used.insert(*id),
                "Concept cites a missing or repeated analyzed reference",
            )?;
        }
        let mut scenes = BTreeSet::new();
        let mut claim_reviews = Vec::new();
        for shot in &concept.shot_studies {
            check(
                project.scenes.iter().any(|scene| scene.id == shot.scene_id)
                    && scenes.insert(shot.scene_id),
                "Concept shot must preserve a unique existing scene",
            )?;
            text(&shot.narrative_purpose, 2000, "Narrative shot purpose")?;
            text(&shot.audience_takeaway, 2000, "Audience takeaway")?;
            check(shot.claim_ids.len() <= 32, "Too many claims per shot")?;
            let mut claims = BTreeSet::new();
            for claim_id in &shot.claim_ids {
                check(
                    claims.insert(*claim_id)
                        && project
                            .brief
                            .claims
                            .iter()
                            .any(|claim| claim.id == *claim_id),
                    "Concept includes a missing or duplicate brief claim",
                )?;
            }
            match shot.evidence_kind {
                CreativeEvidenceKind::GraphicIllustration => {
                    check(
                        shot.evidence_asset_id.is_none(),
                        "An illustration cannot masquerade as actual product evidence",
                    )?;
                }
                CreativeEvidenceKind::RealProductCapture
                | CreativeEvidenceKind::LicensedFootage => {
                    if matches!(shot.evidence_kind, CreativeEvidenceKind::RealProductCapture) {
                        check(
                            !shot.claim_ids.is_empty(),
                            "Real product capture requires at least one explicit brief claim",
                        )?;
                    }
                    let id = shot.evidence_asset_id.ok_or_else(|| {
                        CraftError("Real capture/footage claim lacks original source asset".into())
                    })?;
                    let src = media(project, id).ok_or_else(|| {
                        CraftError("Claim source asset is missing from exact project".into())
                    })?;
                    check(
                        src.content_sha256.as_deref().is_some_and(valid_sha)
                            && (src.media_type.starts_with("image/")
                                || src.media_type.starts_with("video/"))
                            && concept.reference_ids.iter().any(|ref_id| {
                                refs.get(ref_id)
                                    .is_some_and(|reference| reference.asset_id == id)
                            }),
                        "Real media evidence needs matching digest-bound, owner-annotated source",
                    )?;
                }
            }
            for claim_id in &shot.claim_ids {
                claim_reviews.push(review_claim(
                    project,
                    shot,
                    *claim_id,
                    &request.product_version,
                ));
            }
        }
        let related = concept
            .reference_ids
            .iter()
            .filter_map(|id| refs.get(id).copied())
            .collect::<Vec<_>>();
        reviews.push(CreativeDirectionCandidateReview {
            concept_id: concept.id,
            concept_sha256: canonical_digest(concept)?,
            reference_analysis_sha256: canonical_digest(&related)?,
            claim_reviews,
            owner_selected: false,
            human_creative_approved: false,
            actual_native_pixels_reviewed: false,
            real_product_evidence_approved: false,
        });
    }
    Ok(CreativeDirectionStudyReport {
        schema: "motionwright.creative-direction-study-report/1".into(),
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        project_sha256: digest,
        study_sha256: canonical_digest(request)?,
        product_version: request.product_version.clone(),
        candidate_reviews: reviews,
        concept_differences_validated: true,
        reference_media_bytes_opened: false,
        rights_independently_verified: false,
        claim_truth_independently_verified: false,
        winning_concept_id: None,
        project_was_modified: false,
        renderer_executed: false,
        production_approval: "PENDING_INDEPENDENT_OWNER_REVIEW".into(),
    })
}

/// Turn a specifically owner-selected, source-bound direction into an
/// **unapproved** canonical ProductionPlan. The existing StudioService CAS
/// remains the only persistence path, and this never authenticates content,
/// media rights, model claims or a native renderer result.
pub fn propose_selected_direction_plan(
    project: &Project,
    request: &CreativeDirectionStudyRequest,
    selected_id: Uuid,
) -> Result<motionwright_domain::ProductionPlan> {
    use motionwright_domain::{
        NarrativeEvidenceKind, PlannedShot, ProductionClock, ProductionPlan,
    };
    let reviewed = analyze_creative_directions(project, request)?;
    let direction = request
        .alternatives
        .iter()
        .find(|item| item.id == selected_id)
        .ok_or_else(|| {
            CraftError(
                "The owner-selected concept does not belong to the current source study".into(),
            )
        })?;
    let candidate = reviewed
        .candidate_reviews
        .iter()
        .find(|item| item.concept_id == selected_id)
        .ok_or_else(|| CraftError("No source review matches the selected concept".into()))?;
    check(
        !candidate.owner_selected
            && !candidate.human_creative_approved
            && !candidate.real_product_evidence_approved
            && !candidate.actual_native_pixels_reviewed
            && canonical_digest(direction)? == candidate.concept_sha256,
        "A reference analysis cannot manufacture owner approval or another concept",
    )?;
    let mut shots = Vec::new();
    for shot in &direction.shot_studies {
        let kind = match shot.evidence_kind {
            CreativeEvidenceKind::GraphicIllustration => {
                check(
                    shot.claim_ids.is_empty(),
                    "Illustrations must not carry product claims into a real-evidence production plan",
                )?;
                NarrativeEvidenceKind::GraphicStudy
            }
            CreativeEvidenceKind::RealProductCapture => {
                check(
                    !shot.claim_ids.is_empty(),
                    "A real capture needs at least one original source-bound claim",
                )?;
                NarrativeEvidenceKind::RealCapture
            }
            CreativeEvidenceKind::LicensedFootage => {
                check(
                    shot.claim_ids.is_empty(),
                    "Licensed generic footage cannot prove source product behavior",
                )?;
                NarrativeEvidenceKind::LicensedFootage
            }
        };
        for claim_id in &shot.claim_ids {
            let review = candidate
                .claim_reviews
                .iter()
                .find(|entry| entry.claim_id == *claim_id && entry.scene_id == shot.scene_id)
                .ok_or_else(|| {
                    CraftError("Selected concept claim has no exact source review".into())
                })?;
            check(
                review.status == ClaimReviewStatus::SourceBoundNeedsHumanVerification
                    && !review.verified_product_behavior
                    && !review.independently_verified_rights
                    && review.source_asset_id == shot.evidence_asset_id
                    && review.current_product_version == request.product_version,
                "Cannot promote unverified, stale, illustrative or unsupported claims as source evidence",
            )?;
            let existing = project
                .brief
                .claims
                .iter()
                .find(|claim| claim.id == *claim_id)
                .ok_or_else(|| CraftError("Claim no longer belongs to project Brief".into()))?;
            let source_asset = shot
                .evidence_asset_id
                .and_then(|id| media(project, id))
                .ok_or_else(|| {
                    CraftError("Claim source asset was removed from current Project".into())
                })?;
            check(
                matches!(&existing.source,Some(SourceReference::Asset{asset_id})
                   if Some(*asset_id)==shot.evidence_asset_id)
                    && existing.source_revision.as_deref()
                        == Some(request.product_version.as_str())
                    && source_asset.source_revision.as_deref()
                        == Some(request.product_version.as_str())
                    && source_asset.content_sha256.as_deref() == review.source_sha256.as_deref(),
                "Claim source version or media SHA changed after direction comparison",
            )?;
        }
        let asset_ids = shot.evidence_asset_id.into_iter().collect::<Vec<_>>();
        let purpose = format!(
            "{}. Audience takeaway: {}",
            shot.narrative_purpose, shot.audience_takeaway
        );
        text(
            &purpose,
            4000,
            "Selected narrative shot exceeds canonical plan text budget",
        )?;
        shots.push(PlannedShot {
            scene_id: shot.scene_id,
            purpose,
            claim_ids: shot.claim_ids.clone(),
            asset_ids,
            evidence_kind: kind,
        });
    }
    let mut ref_notes = Vec::new();
    for source_id in &direction.reference_ids {
        let source = request
            .references
            .iter()
            .find(|item| item.id == *source_id)
            .ok_or_else(|| CraftError("Selected direction lost the analyzed reference".into()))?;
        let note = format!(
            "Originality: {}. Owner-observed hierarchy: {}. No independent rights, copying or claim certification.",
            source.originality_constraint, source.observed_hierarchy
        );
        text(
            &note,
            2000,
            "Reference-derived constraint exceeds canonical plan bounds",
        )?;
        ref_notes.push(note);
    }
    let mut exclusions = project.brief.exclusions.clone();
    exclusions
        .push("No illustrative mockup or generic footage may imply real product operation.".into());
    exclusions.push(
        "Claim truth, media rights, render quality and owner approval remain separate.".into(),
    );
    let concept = format!(
        "{}: {}. Structure: {:?}; rhythm: {:?}. {}. Human-selected draft, not an approved product claim.",
        direction.title,
        direction.metaphor,
        direction.structure,
        direction.rhythm,
        direction.distinct_visual_argument
    );
    let plan = ProductionPlan {
        objective: project.brief.objective.clone(),
        audience: project.brief.audience.clone(),
        concept,
        reference_constraints: ref_notes,
        exclusions,
        shots,
        clock: ProductionClock::Timeline,
        approval: None,
    };
    plan.validate(
        &project.scenes,
        &project.assets,
        &project.brief,
        &project.audio,
    )
    .map_err(|e| CraftError(e.to_string()))?;
    Ok(plan)
}
