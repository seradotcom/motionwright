//! Data-only first-party recipe distillation: draft -> source contract experiment
//! -> review proposal. This module NEVER installs executable plugins, grants
//! trust, promotes a package, calls renderers or approves its own artwork.
use crate::*;
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DistillationPolarity {
    Positive,
    Negative,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DistillationReference {
    pub original_source_sha256: String,
    pub polarity: DistillationPolarity,
    pub observed_strength_or_failure: String,
    pub source_rights_note: String,
    pub source_owner_attested_rights: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DistillationDraft {
    pub schema: String,
    pub id: Uuid,
    pub recipe: RecipeId,
    pub authoring_intent: String,
    pub proposed_version: u32,
    pub source_examples: Vec<DistillationReference>,
    pub color_source: BrandProfile,
    pub taste_source: TasteProfile,
    pub original_template_sha256: String,
    pub request_blueprint: ComponentRequest,
    pub owner_approved: bool,
    pub executable_install_authorized: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DistilledVariation {
    pub aspect: String,
    pub locale: Locale,
    pub request_sha256: String,
    pub native_output_sha256: String,
    pub output_kind: String,
    pub native_renderer_observed: bool,
    pub human_design_approved: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DistillationTrial {
    pub schema: String,
    pub draft_sha256: String,
    pub recipe: RecipeId,
    pub examples_sha256: String,
    pub variations: Vec<DistilledVariation>,
    pub source_validation: String,
    pub native_pixel_validation: String,
    pub human_design_status: String,
    pub owner_install_approval: String,
    pub rendered: bool,
    pub owner_install_authorized: bool,
    pub source_contract_only: bool,
    pub executable_code_admitted: bool,
}

impl DistillationDraft {
    pub fn validate(&self) -> Result<()> {
        check(
            self.schema == "motionwright.recipe-distillation-draft/1",
            "Unknown creative distillation draft schema",
        )?;
        check(
            !self.owner_approved && !self.executable_install_authorized,
            "A model-authored creative draft cannot approve its own installation",
        )?;
        text(
            &self.authoring_intent,
            2000,
            "Distillation requires a stated original design purpose",
        )?;
        check(
            self.proposed_version > 0
                && self.request_blueprint.recipe == self.recipe
                && self.request_blueprint.version == self.proposed_version,
            "Distillation candidate and existing native recipe identity/version disagree",
        )?;
        self.request_blueprint.validate()?;
        self.color_source.validate()?;
        self.taste_source.validate()?;
        check(
            valid_sha(&self.original_template_sha256)
                && self.original_template_sha256
                    == canonical_digest(&(
                        &self.request_blueprint,
                        &self.color_source,
                        &self.taste_source,
                    ))?,
            "Distillation draft does not preserve the exact source request/brand/taste",
        )?;
        check(
            (4..=32).contains(&self.source_examples.len()),
            "A creative pattern cannot be generalized from one sample",
        )?;
        let mut seen = BTreeSet::new();
        let (mut positives, mut negatives) = (0, 0);
        for example in &self.source_examples {
            check(
                valid_sha(&example.original_source_sha256)
                    && seen.insert(&example.original_source_sha256),
                "Positive/negative creative examples cannot duplicate source bytes",
            )?;
            text(
                &example.observed_strength_or_failure,
                1200,
                "Every example needs an explicit non-generic visual observation",
            )?;
            text(
                &example.source_rights_note,
                900,
                "Source examples require a traceable owner rights note",
            )?;
            check(
                example.source_owner_attested_rights,
                "An unlicensed example cannot be used for reusable recipe distillation",
            )?;
            match example.polarity {
                DistillationPolarity::Positive => positives += 1,
                DistillationPolarity::Negative => negatives += 1,
            }
        }
        check(
            positives >= 2 && negatives >= 2,
            "Both favorable and unfavorable independently sourced examples are required",
        )?;
        Ok(())
    }
}
fn profile_name(request: &ComponentRequest) -> Result<&'static str> {
    let (width, height) = (request.output.width, request.output.height);
    if u64::from(width) * 9 == u64::from(height) * 16 {
        return Ok("landscape");
    }
    if u64::from(width) * 16 == u64::from(height) * 9 {
        return Ok("portrait");
    }
    if width == height {
        return Ok("square");
    }
    Err(CraftError(
        "Distillation experiments require exact 16:9, 9:16 or 1:1 output ratio".into(),
    ))
}
pub fn experiment_source_variants(
    draft: &DistillationDraft,
    requests: &[ComponentRequest],
) -> Result<DistillationTrial> {
    draft.validate()?;
    check(
        requests.len() == 9,
        "Source-level distillation experiments must cover three aspect ratios and three locales",
    )?;
    let mut seen = BTreeSet::new();
    let mut ids = BTreeSet::new();
    let mut observations = Vec::new();
    for req in requests {
        let aspect = profile_name(req)?;
        check(
            req.recipe == draft.recipe && req.version == draft.proposed_version,
            "Distillation variation switched to another recipe/version",
        )?;
        check(
            ids.insert(req.instance_id),
            "Variations must preserve independent, stable creative instance identity",
        )?;
        let locale = match req.locale {
            Locale::En => "en",
            Locale::Es => "es",
            Locale::De => "de",
        };
        check(
            seen.insert((aspect, locale)),
            "Distillation source experiment contains duplicate locale/aspect variants",
        )?;
        let rendered = realize(req, &draft.color_source, &draft.taste_source)?;
        let kind = match &rendered.output {
            CreativeRealization::NativeHtml(_) => "native_html",
            CreativeRealization::BlenderStage(_) => "blender_stage",
            CreativeRealization::AudioScore(_) => "audio_score",
        };
        observations.push(DistilledVariation {
            aspect: aspect.into(),
            locale: req.locale,
            request_sha256: rendered.input_sha256,
            native_output_sha256: rendered.source_sha256,
            output_kind: kind.into(),
            native_renderer_observed: false,
            human_design_approved: false,
        });
    }
    Ok(DistillationTrial {
        schema: "motionwright.recipe-distillation-source-trial/1".into(),
        draft_sha256: canonical_digest(draft)?,
        recipe: draft.recipe,
        examples_sha256: canonical_digest(&draft.source_examples)?,
        variations: observations,
        source_validation: "PASS_SOURCE_SCHEMA_ONLY".into(),
        native_pixel_validation: "NOT_PERFORMED".into(),
        human_design_status: "NOT_REVIEWED".into(),
        owner_install_approval: "NOT_REQUESTED".into(),
        rendered: false,
        owner_install_authorized: false,
        source_contract_only: true,
        executable_code_admitted: false,
    })
}
