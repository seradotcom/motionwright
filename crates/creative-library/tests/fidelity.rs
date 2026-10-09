use motionwright_creative_library::{
    self as library, BrandProfile, CapabilityState, ComponentRequest, CopyPack, CreativeFeature,
    CreativeRealization, DataSeries, Locale, ProceduralOptions, RealizationTarget, RecipeId,
    TasteProfile, native, negotiate_realization, realize,
};
use uuid::Uuid;

fn design(recipe: RecipeId) -> library::CreativeContribution {
    let input = ComponentRequest {
        instance_id: Uuid::from_u128(4567),
        recipe,
        version: 1,
        output: native::Canvas {
            width: 640,
            height: 360,
            rate: native::FrameRate { num: 30, den: 1 },
            frames: 90,
            background: None,
        },
        copy: CopyPack::editorial(Locale::En),
        locale: Locale::En,
        seed: 6,
        motion: true,
        data: Some(DataSeries::synthetic(Uuid::from_u128(400))),
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    };
    realize(
        &input,
        &BrandProfile::neutral(Uuid::from_u128(4)),
        &TasteProfile::editorial(Uuid::from_u128(5)),
    )
    .unwrap()
}
fn property<'a>(
    audit: &'a library::RealizationFidelity,
    name: CreativeFeature,
) -> &'a library::PropertyFidelity {
    audit.properties.iter().find(|p| p.feature == name).unwrap()
}
#[test]
fn native_html_preserves_objects_but_cannot_claim_all_editable_source_semantics() {
    let source = design(RecipeId::SeriesReveal);
    let audit = negotiate_realization(&source, RealizationTarget::Hyperframes).unwrap();
    assert_eq!(
        property(&audit, CreativeFeature::ObjectIdentity).state,
        CapabilityState::Native
    );
    assert_eq!(
        property(&audit, CreativeFeature::NumericDataSource).state,
        CapabilityState::Baked
    );
    assert!(!property(&audit, CreativeFeature::NumericDataSource).target_editable);
    assert!(!audit.renderer_readback_verified);
    assert!(!audit.project_level_source_admission_verified);
    assert!(!audit.executable_without_separate_owner_grant);
    assert!(audit.summary.contains("source retention"));
    assert_eq!(audit.score, None);
    assert!(!audit.creative_quality_approved);
}
#[test]
fn external_renderers_are_not_called_full_fidelity_when_no_mapping_exists() {
    let source = design(RecipeId::HeroReveal);
    for target in [
        RealizationTarget::MotionCanvas,
        RealizationTarget::Blender,
        RealizationTarget::ManimCommunity,
        RealizationTarget::MltVideo,
        RealizationTarget::FframesExperimental,
        RealizationTarget::OriginalPcmWav,
    ] {
        let audit = negotiate_realization(&source, target).unwrap();
        assert!(!audit.properties.is_empty());
        assert!(
            audit
                .properties
                .iter()
                .all(|p| p.state == CapabilityState::Unavailable)
        );
        assert!(audit.properties.iter().all(|p| p.original_source_retained));
        assert!(audit.summary.contains("No approved realization"));
        assert!(!audit.executable_without_separate_owner_grant);
    }
}
#[test]
fn procedural_native_geometry_keeps_elements_but_not_separate_parameters() {
    let source = design(RecipeId::SeededTexture);
    let audit = negotiate_realization(&source, RealizationTarget::Hyperframes).unwrap();
    assert_eq!(
        property(&audit, CreativeFeature::ProceduralSourceControls).state,
        CapabilityState::Baked
    );
    assert_eq!(
        property(&audit, CreativeFeature::VectorGeometry).state,
        CapabilityState::Native
    );
}
#[test]
fn blender_stage_plans_have_real_3d_controls_but_require_a_separate_blender_runtime() {
    let source = design(RecipeId::DollyFocus);
    assert!(matches!(
        &source.output,
        CreativeRealization::BlenderStage(_)
    ));
    let audit = negotiate_realization(&source, RealizationTarget::Blender).unwrap();
    assert_eq!(
        property(&audit, CreativeFeature::Physical3dCamera).state,
        CapabilityState::Native
    );
    assert_eq!(
        property(&audit, CreativeFeature::Physical3dGeometry).state,
        CapabilityState::Native
    );
    assert!(!audit.renderer_readback_verified);
    assert!(!audit.executable_without_separate_owner_grant);
    let wrong = negotiate_realization(&source, RealizationTarget::Hyperframes).unwrap();
    assert_eq!(
        property(&wrong, CreativeFeature::Physical3dCamera).state,
        CapabilityState::Unavailable
    );
}
#[test]
fn real_wav_is_baked_not_reported_as_editable_original_oscillators() {
    let source = design(RecipeId::FocusHit);
    let audit = negotiate_realization(&source, RealizationTarget::OriginalPcmWav).unwrap();
    assert_eq!(
        property(&audit, CreativeFeature::OriginalSoundVoices).state,
        CapabilityState::Baked
    );
    assert_eq!(
        property(&audit, CreativeFeature::SampleAccurateSoundEnvelope).state,
        CapabilityState::Baked
    );
    assert!(audit.native_source_retained);
    assert!(!audit.executable_without_separate_owner_grant);
}
#[test]
fn an_mutated_native_source_is_rejected_before_realization_negotiation() {
    let mut source = design(RecipeId::HeroReveal);
    let CreativeRealization::NativeHtml(doc) = &mut source.output else {
        panic!("Expected HTML native")
    };
    doc.nodes[0].pose.x += 12.0;
    assert!(negotiate_realization(&source, RealizationTarget::Hyperframes).is_err());
}
