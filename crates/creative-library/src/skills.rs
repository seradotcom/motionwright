//! The twelve v0.5 first-party knowledge skills are advisory, not installed executors.
//! Mechanical checks are deterministic, scoped and cannot create runtime authority.
use crate::*;
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "kebab-case")]
pub enum CreativeSkillId {
    Direction,
    CausalStory,
    Typography,
    Motion,
    ProductStage,
    Capture,
    Sound,
    Responsive,
    Critic,
    Repair,
    Distillation,
    Delivery,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CreativeSkillDefinition {
    pub id: CreativeSkillId,
    pub canonical_id: String,
    pub version: String,
    pub status: String,
    pub work_product: String,
    pub review_condition: String,
    pub may_grant_execution: bool,
    pub may_approve_own_work: bool,
}
impl CreativeSkillId {
    pub const ALL: [Self; 12] = [
        Self::Direction,
        Self::CausalStory,
        Self::Typography,
        Self::Motion,
        Self::ProductStage,
        Self::Capture,
        Self::Sound,
        Self::Responsive,
        Self::Critic,
        Self::Repair,
        Self::Distillation,
        Self::Delivery,
    ];
    pub fn definition(self) -> CreativeSkillDefinition {
        let (slug, output, review) = match self {
            Self::Direction => (
                "direction",
                "Alternative concepts with different metaphors or structures",
                "Human selects a concept linked to the audience and verified claim plan",
            ),
            Self::CausalStory => (
                "causal-story",
                "Causal beats with antecedent, change and consequence",
                "Audience can identify the stated relationship without the source prompt",
            ),
            Self::Typography => (
                "typography",
                "Copy/layout hierarchy with glyph, bounds and locale review",
                "No claim-bearing text disappears or clips",
            ),
            Self::Motion => (
                "motion",
                "Intentional editable animation and continuity anchors",
                "Every transfer has an explicit persistent object or a justified cut",
            ),
            Self::ProductStage => (
                "product-stage",
                "Authorized product UI on an editable browser, panel or device stage",
                "A synthetic screen must not be asserted as product functionality",
            ),
            Self::Capture => (
                "capture",
                "Reproducible authorized product capture scenario and receipt",
                "A local operation grant cannot authorize private screenshots for external upload",
            ),
            Self::Sound => (
                "sound",
                "Source-bound sound cues and requested mix checks",
                "Measured loudness alone is not artistic or intelligibility approval",
            ),
            Self::Responsive => (
                "responsive",
                "Variant proposal with readable layout and explicit differences",
                "A new aspect ratio never alters the owner-approved original silently",
            ),
            Self::Critic => (
                "critic",
                "Localized visual/audio findings and up to three bounded candidate repairs",
                "A self-generated review never constitutes independent acceptance",
            ),
            Self::Repair => (
                "repair",
                "Scoped revision-aware repair proposals preserving human locks",
                "Stale changes and protected controls cannot be overwritten",
            ),
            Self::Distillation => (
                "distillation",
                "New draft component from diverse positive and negative examples",
                "No trace or attractive screenshot can unilaterally admit executable code",
            ),
            Self::Delivery => (
                "delivery",
                "Source-bound immutable handoff with rights, losses and publication gate",
                "An export does not imply permission to publish",
            ),
        };
        CreativeSkillDefinition {
            id: self,
            canonical_id: format!("motionwright-{slug}"),
            version: "0.5-knowledge".into(),
            status: "advisory_not_installed".into(),
            work_product: output.into(),
            review_condition: review.into(),
            may_grant_execution: false,
            may_approve_own_work: false,
        }
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SkillIssueKind {
    TechnicalPreflight,
    UnverifiedEvidence,
    HumanCreativeDecision,
    ScopeConflict,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SkillFinding {
    pub skill: CreativeSkillId,
    pub kind: SkillIssueKind,
    pub object_id: Option<Uuid>,
    pub source_frame_start: Option<u32>,
    pub source_frame_end: Option<u32>,
    pub observation: String,
    pub risk: String,
    pub scoped_next_action: String,
    pub evidence_level: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SkillAudit {
    pub schema: String,
    pub component_id: Uuid,
    pub recipe: RecipeId,
    pub input_sha256: String,
    pub source_sha256: String,
    pub skill_definitions: Vec<CreativeSkillDefinition>,
    pub findings: Vec<SkillFinding>,
    pub artifact_classification: String,
    pub standalone_runtime_authority: bool,
    pub independent_human_approval: bool,
    pub source_changed: bool,
}
fn finding(
    skill: CreativeSkillId,
    kind: SkillIssueKind,
    observation: &str,
    risk: &str,
    next: &str,
) -> SkillFinding {
    SkillFinding {
        skill,
        kind,
        object_id: None,
        source_frame_start: None,
        source_frame_end: None,
        observation: observation.into(),
        risk: risk.into(),
        scoped_next_action: next.into(),
        evidence_level: "bounded_authoring_preflight_no_pixel_or_independent_approval".into(),
    }
}
pub fn preflight_creative_skills(
    request: &ComponentRequest,
    brand: &BrandProfile,
    taste: &TasteProfile,
    contribution: &CreativeContribution,
) -> Result<SkillAudit> {
    request.validate()?;
    brand.validate()?;
    taste.validate()?;
    check(
        contribution.component_id == request.instance_id && contribution.recipe == request.recipe,
        "Skill review cannot cross component identities",
    )?;
    check(
        canonical_digest(&(request, brand, taste))? == contribution.input_sha256,
        "Skill preflight is stale relative to its authorized creative inputs",
    )?;
    check(
        canonical_digest(&contribution.output)? == contribution.source_sha256,
        "Skill preflight cannot assess a source modified after realization",
    )?;
    let mut findings = Vec::new();
    findings.push(finding(
        CreativeSkillId::Direction,
        SkillIssueKind::HumanCreativeDecision,
        "A typed composition exists, but no audience-tested concept choice is evidenced.",
        "A valid renderer may still tell the wrong story.",
        "Review two materially distinct concepts against the actual brief.",
    ));
    findings.push(finding(CreativeSkillId::CausalStory,SkillIssueKind::UnverifiedEvidence,
        "The recipe records copy and visual relationships; audience comprehension has not been independently measured.",
        "Causality or product performance could be implied without sufficient evidence.",
        "Connect each factual beat and claim to a real source or mark it as illustration."));
    match &contribution.output {
        CreativeRealization::NativeHtml(doc) => {
            for node in &doc.nodes {
                if let native::Content::Text { size, .. } = &node.content
                    && *size < brand.minimum_body_size
                {
                    let mut item = finding(
                        CreativeSkillId::Typography,
                        SkillIssueKind::TechnicalPreflight,
                        "A native text node falls below the brand-designated minimum body size.",
                        "Readability at delivery size may be insufficient.",
                        "Recompose the text block or shorten copy; do not reduce type indefinitely.",
                    );
                    item.object_id = Some(node.id);
                    item.source_frame_start = Some(0);
                    item.source_frame_end = Some(doc.canvas.frames - 1);
                    findings.push(item);
                }
            }
            if doc.nodes.iter().any(|node| !node.keyframes.is_empty())
                || !doc.camera.keyframes.is_empty()
            {
                findings.push(finding(CreativeSkillId::Motion,SkillIssueKind::UnverifiedEvidence,
                    "Keyframes are authored but the full temporal curve has not been reviewed here.",
                    "A static frame cannot demonstrate continuity or temporal readability.",
                    "Inspect native sequential and reverse seeks before accepting the animation."));
            }
            if matches!(
                contribution.recipe,
                RecipeId::ScreenFocus
                    | RecipeId::FlowBridge
                    | RecipeId::BrowserStage
                    | RecipeId::EvidencePair
            ) && doc.assets.is_empty()
            {
                findings.push(finding(
                    CreativeSkillId::ProductStage,
                    SkillIssueKind::ScopeConflict,
                    "A recipe requiring real media has no attached authorized asset.",
                    "A synthetic interface might be presented as real product evidence.",
                    "Import and digest-bind an authorized source before previewing or publishing.",
                ));
            }
        }
        CreativeRealization::BlenderStage(plan) => {
            if plan.devices.iter().all(|d| d.screen_asset_id.is_none()) {
                findings.push(finding(CreativeSkillId::ProductStage,SkillIssueKind::UnverifiedEvidence,
                    "Generic original 3D device geometry contains no owner-supplied product screen.",
                    "Generic materials cannot verify a software claim.",
                    "Label this material a graphic study or attach a real authorized product capture."));
            }
            findings.push(finding(CreativeSkillId::Motion,SkillIssueKind::UnverifiedEvidence,
                "3D camera knots and source optics exist but visual arc and focus behavior are not reviewed by this skill.",
                "Geometry continuity can fail during real renders.",
                "Inspect Blender-native camera frames and editability in an owner-granted runtime."));
        }
        CreativeRealization::AudioScore(_) => {
            findings.push(finding(CreativeSkillId::Sound,SkillIssueKind::UnverifiedEvidence,
                "The original audio score has deterministic oscillator/envelope controls, not a mastered VO/music/SFX mix.",
                "Click-free synthesis does not establish loudness, synchronization or intelligibility.",
                "Decode PCM, measure waveform/peaks and listen with the approved narration and cut."));
        }
    }
    if request
        .data
        .as_ref()
        .is_some_and(|series| series.origin == DataOrigin::SyntheticFixture)
    {
        findings.push(finding(CreativeSkillId::CausalStory,SkillIssueKind::UnverifiedEvidence,
            "The input numbers are explicitly synthetic acceptance values, not measured product outcomes.",
            "Synthetic metrics could be mistaken for verified claims.",
            "Keep synthetic labels visible and substitute source-bound observations before publication."));
    }
    if request.primary_asset.is_some() || request.secondary_asset.is_some() {
        findings.push(finding(CreativeSkillId::Capture,SkillIssueKind::UnverifiedEvidence,
            "A digest/rights declaration is present but no build, viewport, user scenario or redaction review is attached.",
            "Ownership of local source bytes does not prove a product behavior or permission to disclose private data.",
            "Attach an authorized reproducible capture receipt with the exact product revision."));
    }
    findings.push(finding(
        CreativeSkillId::Responsive,
        SkillIssueKind::UnverifiedEvidence,
        "This proposal covers one aspect ratio and one locale, not all connected variants.",
        "Other delivery profiles may have clipping, reflow or rhythm changes.",
        "Render independent landscape/portrait/square variants and their actual copy translations.",
    ));
    findings.push(finding(CreativeSkillId::Critic,SkillIssueKind::UnverifiedEvidence,
        "Source inspection cannot substitute for full temporal native frame/audio observation.",
        "A visually appealing contact sheet does not prove continuity or an independently reviewed result.",
        "Record source-bound object/time findings after reviewing the native preview."));
    findings.push(finding(
        CreativeSkillId::Repair,
        SkillIssueKind::HumanCreativeDecision,
        "No particular user-approved localized repair has been selected.",
        "Unscoped transformations could overwrite deliberate human edits.",
        "Propose an atomic source-digest-bound edit, preserve locks and allow explicit rejection.",
    ));
    findings.push(finding(CreativeSkillId::Distillation,SkillIssueKind::HumanCreativeDecision,
        "A single generated instance is not sufficient evidence to standardize a reusable component.",
        "Premature promotion would turn one aesthetically arbitrary solution into a global default.",
        "Review positive/negative cases, three ratios, locale variations and documented overrides."));
    findings.push(finding(CreativeSkillId::Delivery,SkillIssueKind::HumanCreativeDecision,
        "Technical authoring has not produced an independently approved, immutable final deliverable.",
        "Native source, rights and publication authority are separate from a compiled composition.",
        "Verify exact SHA, media rights, loss report, editable source, owner approval and final master."));
    check(findings.len() <= 64, "Bounded skill review count exceeded")?;
    let mut seen = BTreeSet::new();
    let definitions = CreativeSkillId::ALL
        .into_iter()
        .map(|skill| {
            let entry = skill.definition();
            seen.insert(entry.id);
            entry
        })
        .collect::<Vec<_>>();
    check(
        seen.len() == 12,
        "Creative skill advisory catalog is incomplete",
    )?;
    Ok(SkillAudit {
        schema: "motionwright.creative-skill-advisory/1".into(),
        component_id: contribution.component_id,
        recipe: contribution.recipe,
        input_sha256: contribution.input_sha256.clone(),
        source_sha256: contribution.source_sha256.clone(),
        skill_definitions: definitions,
        findings,
        artifact_classification: "authoring_advisory_not_a_renderer_or_owner_approval".into(),
        standalone_runtime_authority: false,
        independent_human_approval: false,
        source_changed: false,
    })
}
