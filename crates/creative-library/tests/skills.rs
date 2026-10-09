use motionwright_creative_library::{
    self as craft, BrandProfile, ComponentRequest, CopyPack, CreativeSkillId, DataOrigin,
    DataSeries, Locale, ProceduralOptions, RecipeId, SkillIssueKind, TasteProfile, native,
    preflight_creative_skills, realize,
};
use uuid::Uuid;
fn req(recipe: RecipeId) -> ComponentRequest {
    ComponentRequest {
        instance_id: Uuid::from_u128(899),
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
        seed: 17,
        motion: true,
        data: Some(DataSeries::synthetic(Uuid::from_u128(990))),
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    }
}
#[test]
fn twelve_original_skills_remain_advisory_and_never_grant_install_publish_or_self_approval() {
    let brand = BrandProfile::neutral(Uuid::nil());
    let taste = TasteProfile::editorial(Uuid::from_u128(1));
    let request = req(RecipeId::HeroReveal);
    let instance = realize(&request, &brand, &taste).unwrap();
    let reviewed = preflight_creative_skills(&request, &brand, &taste, &instance).unwrap();
    assert_eq!(reviewed.skill_definitions.len(), 12);
    assert!(
        reviewed
            .skill_definitions
            .iter()
            .all(|skill| skill.status == "advisory_not_installed"
                && !skill.may_grant_execution
                && !skill.may_approve_own_work)
    );
    assert!(!reviewed.standalone_runtime_authority);
    assert!(!reviewed.independent_human_approval);
    assert!(
        reviewed
            .findings
            .iter()
            .any(|item| item.skill == CreativeSkillId::Delivery
                && item.kind == SkillIssueKind::HumanCreativeDecision)
    );
    assert_eq!(reviewed.source_sha256, instance.source_sha256);
}
#[test]
fn synthetic_data_is_never_reported_as_sourced_product_performance() {
    let brand = BrandProfile::neutral(Uuid::nil());
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    let request = req(RecipeId::SeriesReveal);
    assert_eq!(
        request.data.as_ref().unwrap().origin,
        DataOrigin::SyntheticFixture
    );
    let instance = realize(&request, &brand, &taste).unwrap();
    let review = preflight_creative_skills(&request, &brand, &taste, &instance).unwrap();
    assert!(review.findings.iter().any(|f|
        f.skill==CreativeSkillId::CausalStory && f.observation.contains("synthetic")));
}
#[test]
fn typography_flags_have_a_specific_native_object_and_frame_interval() {
    let brand = BrandProfile::neutral(Uuid::nil());
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    let request = req(RecipeId::CaptionEmphasis);
    let instance = realize(&request, &brand, &taste).unwrap();
    let review = preflight_creative_skills(&request, &brand, &taste, &instance).unwrap();
    let warnings = review
        .findings
        .iter()
        .filter(|finding| {
            finding.skill == CreativeSkillId::Typography
                && finding.kind == SkillIssueKind::TechnicalPreflight
        })
        .collect::<Vec<_>>();
    assert!(!warnings.is_empty());
    assert!(warnings.iter().all(|f| f.object_id.is_some()
        && f.source_frame_start == Some(0)
        && f.source_frame_end == Some(89)));
}
#[test]
fn prior_skill_recommendations_do_not_survive_a_changed_source_digest() {
    let brand = BrandProfile::neutral(Uuid::nil());
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    let request = req(RecipeId::HeroReveal);
    let mut instance = realize(&request, &brand, &taste).unwrap();
    let craft::CreativeRealization::NativeHtml(doc) = &mut instance.output else {
        panic!("Expected HTML")
    };
    doc.nodes[0].name = "A later different human edit".into();
    assert!(preflight_creative_skills(&request, &brand, &taste, &instance).is_err());
}
