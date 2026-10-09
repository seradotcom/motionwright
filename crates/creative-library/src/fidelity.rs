//! Property-level realization negotiation. This report is **not** a renderer result.
//! It retains source IDs/digests and makes unsupported transfers explicit.
use crate::*;
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityState {
    Native,
    Translated,
    Baked,
    Approximated,
    Unavailable,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RealizationTarget {
    Hyperframes,
    Blender,
    MotionCanvas,
    MltVideo,
    ManimCommunity,
    FframesExperimental,
    OriginalPcmWav,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum CreativeFeature {
    ObjectIdentity,
    EditableText,
    OriginalTypography,
    VectorGeometry,
    ImageOrVideoAsset,
    MaskAndClip,
    CompositingBlend,
    VisualEffects,
    TransformAndAnimation,
    Camera2d,
    Physical3dGeometry,
    Physical3dCamera,
    StageLightingAndMaterials,
    OriginalSoundVoices,
    SampleAccurateSoundEnvelope,
    NumericDataSource,
    ProceduralSourceControls,
    CreativeBriefAndCopy,
    HumanPropertyProtections,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PropertyFidelity {
    pub feature: CreativeFeature,
    pub state: CapabilityState,
    pub original_source_retained: bool,
    pub target_editable: bool,
    pub source_explanation: String,
    pub loss_or_limitation: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RealizationFidelity {
    pub schema: String,
    pub component_id: Uuid,
    pub recipe: RecipeId,
    pub source_sha256: String,
    pub target: RealizationTarget,
    pub native_source_retained: bool,
    pub project_level_source_admission_verified: bool,
    pub renderer_readback_verified: bool,
    pub creative_quality_approved: bool,
    pub score: Option<u8>,
    pub properties: Vec<PropertyFidelity>,
    pub executable_without_separate_owner_grant: bool,
    pub summary: String,
}
fn add(set: &mut BTreeSet<CreativeFeature>, feature: CreativeFeature) {
    set.insert(feature);
}
fn authored_features(contribution: &CreativeContribution) -> Result<BTreeSet<CreativeFeature>> {
    let mut set = BTreeSet::new();
    match &contribution.output {
        CreativeRealization::NativeHtml(doc) => {
            native::validate_document(doc).map_err(|e| CraftError(e.to_string()))?;
            add(&mut set, CreativeFeature::ObjectIdentity);
            add(&mut set, CreativeFeature::CreativeBriefAndCopy);
            for node in &doc.nodes {
                match &node.content {
                    native::Content::Text { .. } => {
                        add(&mut set, CreativeFeature::EditableText);
                        add(&mut set, CreativeFeature::OriginalTypography);
                    }
                    native::Content::Group
                    | native::Content::Rectangle { .. }
                    | native::Content::Ellipse { .. }
                    | native::Content::Path { .. } => {
                        add(&mut set, CreativeFeature::VectorGeometry)
                    }
                    native::Content::Image { .. } | native::Content::Video { .. } => {
                        add(&mut set, CreativeFeature::ImageOrVideoAsset)
                    }
                }
                if node.clip != native::Clip::None {
                    add(&mut set, CreativeFeature::MaskAndClip);
                }
                if node.blend != native::Blend::Normal {
                    add(&mut set, CreativeFeature::CompositingBlend);
                }
                if node.effects != native::Effects::default() {
                    add(&mut set, CreativeFeature::VisualEffects);
                }
                if !node.keyframes.is_empty()
                    || node.pose.rotation != 0.0
                    || node.pose.scale_x != 1.0
                    || node.pose.scale_y != 1.0
                {
                    add(&mut set, CreativeFeature::TransformAndAnimation);
                }
                if !node.locked_properties.is_empty() || !node.locked_fields.is_empty() {
                    add(&mut set, CreativeFeature::HumanPropertyProtections);
                }
            }
            if !doc.camera.keyframes.is_empty()
                || doc.camera.x != 0.0
                || doc.camera.y != 0.0
                || doc.camera.zoom != 1.0
                || doc.camera.rotation != 0.0
            {
                add(&mut set, CreativeFeature::Camera2d);
            }
            if matches!(
                contribution.recipe,
                RecipeId::MeasuredNumber | RecipeId::SeriesReveal | RecipeId::StateComparison
            ) {
                add(&mut set, CreativeFeature::NumericDataSource);
            }
            if matches!(
                contribution.recipe,
                RecipeId::Repeater
                    | RecipeId::InfluenceField
                    | RecipeId::PathDistribution
                    | RecipeId::GridResponse
                    | RecipeId::SeededTexture
            ) {
                add(&mut set, CreativeFeature::ProceduralSourceControls);
            }
        }
        CreativeRealization::BlenderStage(plan) => {
            plan.validate()?;
            for feature in [
                CreativeFeature::ObjectIdentity,
                CreativeFeature::Physical3dGeometry,
                CreativeFeature::Physical3dCamera,
                CreativeFeature::StageLightingAndMaterials,
                CreativeFeature::CreativeBriefAndCopy,
            ] {
                add(&mut set, feature);
            }
            if plan.devices.iter().any(|d| d.screen_asset_id.is_some()) {
                add(&mut set, CreativeFeature::ImageOrVideoAsset);
            }
        }
        CreativeRealization::AudioScore(plan) => {
            plan.validate()?;
            add(&mut set, CreativeFeature::SampleAccurateSoundEnvelope);
            add(&mut set, CreativeFeature::CreativeBriefAndCopy);
            if !plan.voices.is_empty() {
                add(&mut set, CreativeFeature::OriginalSoundVoices);
            }
        }
    }
    Ok(set)
}
fn outcome(
    feature: CreativeFeature,
    source: &CreativeRealization,
    target: RealizationTarget,
) -> PropertyFidelity {
    let native_match = matches!(
        (source, target),
        (
            CreativeRealization::NativeHtml(_),
            RealizationTarget::Hyperframes
        ) | (
            CreativeRealization::BlenderStage(_),
            RealizationTarget::Blender
        ) | (
            CreativeRealization::AudioScore(_),
            RealizationTarget::OriginalPcmWav
        )
    );
    let semantic_loss = matches!(
        feature,
        CreativeFeature::NumericDataSource
            | CreativeFeature::ProceduralSourceControls
            | CreativeFeature::CreativeBriefAndCopy
    );
    let native_bake = matches!(
        (source, target),
        (
            CreativeRealization::AudioScore(_),
            RealizationTarget::OriginalPcmWav
        )
    );
    let (state, target_editable, description) = if !native_match {
        (
            CapabilityState::Unavailable,
            false,
            "No implemented, independently checked semantic mapping for this target. Retain source and select another admitted route.",
        )
    } else if semantic_loss {
        (
            CapabilityState::Baked,
            false,
            "The rendered values or composition remain visible, but the original recipe/data/brief controls are not represented as a first-class editable object in this target.",
        )
    } else if native_bake {
        (
            CapabilityState::Baked,
            false,
            "PCM retains samples and exact rate; the original oscillators, event envelopes and editables must be retained in the associated typed plan.",
        )
    } else {
        (
            CapabilityState::Native,
            true,
            "A first-party typed native source representation exists. Runtime fidelity and authorization still require independent evidence.",
        )
    };
    PropertyFidelity{feature,state,original_source_retained:true,target_editable,
        source_explanation:"Source identity, recipe version and SHA-256 are retained in the contribution and report.".into(),
        loss_or_limitation:description.into()}
}
pub fn negotiate_realization(
    contribution: &CreativeContribution,
    target: RealizationTarget,
) -> Result<RealizationFidelity> {
    check(
        valid_sha(&contribution.input_sha256) && valid_sha(&contribution.source_sha256),
        "Negotiation requires valid source-bound input and source digests",
    )?;
    check(
        canonical_digest(&contribution.output)? == contribution.source_sha256,
        "Source was modified after the contribution was admitted",
    )?;
    let properties = authored_features(contribution)?
        .into_iter()
        .map(|feature| outcome(feature, &contribution.output, target))
        .collect::<Vec<_>>();
    let losses = properties
        .iter()
        .filter(|p| p.state != CapabilityState::Native)
        .count();
    let fully_unavailable = properties
        .iter()
        .all(|p| p.state == CapabilityState::Unavailable);
    Ok(RealizationFidelity {
        schema: "motionwright.realization-fidelity/1".into(),
        component_id: contribution.component_id,
        recipe: contribution.recipe,
        source_sha256: contribution.source_sha256.clone(),
        target,
        native_source_retained: true,
        project_level_source_admission_verified: false,
        renderer_readback_verified: false,
        creative_quality_approved: false,
        score: None,
        properties,
        executable_without_separate_owner_grant: false,
        summary: if fully_unavailable {
            "No approved realization exists for this source/target pair; source retained for later explicit negotiation.".into()
        } else if losses > 0 {
            format!(
                "{losses} property groups require source retention or have noneditable semantics. Technical renderer evidence and human creative approval remain pending."
            )
        } else {
            "Typed native source is present, but execution authority, pixel readback and creative approval are NOT established by this report.".into()
        },
    })
}
