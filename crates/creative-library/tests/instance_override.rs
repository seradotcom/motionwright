use motionwright_creative_library::{
    self as craft, BrandProfile, ColorRole, ComponentRequest, CopyPack, CopyProperty,
    CreativeInstanceEdit, CreativeRealization, Locale, ProceduralOptions, RecipeId, TasteProfile,
    native, propose_instance_override, realize,
};
use uuid::Uuid;
fn fixture() -> (
    ComponentRequest,
    BrandProfile,
    TasteProfile,
    craft::CreativeContribution,
) {
    let req = ComponentRequest {
        instance_id: Uuid::from_u128(782),
        recipe: RecipeId::HeroReveal,
        version: 1,
        output: native::Canvas {
            width: 640,
            height: 360,
            rate: native::FrameRate { num: 30, den: 1 },
            frames: 90,
            background: Some("#111922".into()),
        },
        copy: CopyPack::editorial(Locale::En),
        locale: Locale::En,
        seed: 17,
        motion: true,
        data: None,
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    };
    let brand = BrandProfile::neutral(Uuid::from_u128(4));
    let taste = TasteProfile::editorial(Uuid::from_u128(5));
    let current = realize(&req, &brand, &taste).unwrap();
    (req, brand, taste, current)
}
#[test]
fn one_human_copy_override_keeps_stable_native_ids_and_does_not_edit_shared_recipe() {
    let (req, brand, taste, current) = fixture();
    let expected = current.input_sha256.clone();
    let new = propose_instance_override(
        &current,
        &req,
        &brand,
        &taste,
        &expected,
        &[CreativeInstanceEdit::ReplaceCopy {
            field: CopyProperty::Headline,
            expected_text: req.copy.headline.clone(),
            next_text: "An edited message about retained creative work".into(),
        }],
    )
    .unwrap();
    assert_eq!(new.schema, "motionwright.creative-instance-override/1");
    assert_eq!(new.expected_input_sha256, expected);
    assert_ne!(new.expected_source_sha256, new.proposed_source_sha256);
    assert_eq!(new.changed_properties, vec!["copy/Headline"]);
    assert_eq!(
        new.expected_recipe_definition_sha256,
        new.output_recipe_definition_sha256
    );
    assert!(!new.template_mutated);
    assert!(!new.project_committed);
    assert!(new.requires_project_source_cas);
    assert!(!new.independent_human_approval);
    assert_eq!(req.copy.headline, CopyPack::editorial(Locale::En).headline);
    let CreativeRealization::NativeHtml(before) = &current.output else {
        panic!("Missing native source")
    };
    let CreativeRealization::NativeHtml(after) = &new.proposal.output else {
        panic!("Wrong renderer")
    };
    assert_eq!(before.nodes.len(), after.nodes.len());
    for (one, two) in before.nodes.iter().zip(&after.nodes) {
        assert_eq!(one.id, two.id);
    }
}
#[test]
fn one_atomic_override_can_change_copy_paint_and_motion_without_rewriting_existing_definition() {
    let (req, brand, taste, current) = fixture();
    let expected = current.input_sha256.clone();
    let accent = brand.color(ColorRole::Accent).to_string();
    let proposed = propose_instance_override(
        &current,
        &req,
        &brand,
        &taste,
        &expected,
        &[
            CreativeInstanceEdit::ReplaceCopy {
                field: CopyProperty::Body,
                expected_text: req.copy.body.clone(),
                next_text: "One human-approved revision retains design intent.".into(),
            },
            CreativeInstanceEdit::ReplaceBrandColor {
                role: ColorRole::Accent,
                expected_color: accent,
                next_color: "#FFB37D".into(),
            },
            CreativeInstanceEdit::ReplaceMotionEnergy {
                expected: taste.motion_energy,
                next: 76,
            },
        ],
    )
    .unwrap();
    assert_eq!(proposed.changed_properties.len(), 3);
    assert_eq!(proposed.brand.color(ColorRole::Accent), "#FFB37D");
    assert_eq!(proposed.taste.motion_energy, 76);
    assert_ne!(
        proposed.proposed_input_sha256,
        proposed.expected_input_sha256
    );
    assert_eq!(
        brand.color(ColorRole::Accent),
        BrandProfile::neutral(Uuid::from_u128(4)).color(ColorRole::Accent)
    );
}
#[test]
fn stale_edit_must_not_overwrite_previously_modified_human_text() {
    let (req, brand, taste, current) = fixture();
    let accepted = propose_instance_override(
        &current,
        &req,
        &brand,
        &taste,
        &current.input_sha256,
        &[CreativeInstanceEdit::ReplaceCopy {
            field: CopyProperty::Headline,
            expected_text: req.copy.headline.clone(),
            next_text: "A first approved idea".into(),
        }],
    )
    .unwrap();
    let stale = propose_instance_override(
        &accepted.proposal,
        &accepted.request,
        &accepted.brand,
        &accepted.taste,
        &current.input_sha256,
        &[CreativeInstanceEdit::ReplaceCopy {
            field: CopyProperty::Headline,
            expected_text: req.copy.headline.clone(),
            next_text: "Overwrite owner with older idea".into(),
        }],
    );
    assert!(stale.is_err(), "Stale human text was overwritten");
    let invalid_precondition = propose_instance_override(
        &current,
        &req,
        &brand,
        &taste,
        &current.input_sha256,
        &[CreativeInstanceEdit::ReplaceCopy {
            field: CopyProperty::Headline,
            expected_text: "Some other text".into(),
            next_text: "Alteration".into(),
        }],
    );
    assert!(invalid_precondition.is_err());
}
#[test]
fn duplicate_properties_and_noop_edits_are_rejected_not_reported_as_revisions() {
    let (req, brand, taste, current) = fixture();
    let a = CreativeInstanceEdit::ReplaceSeed {
        expected: req.seed,
        next: 25,
    };
    assert!(
        propose_instance_override(
            &current,
            &req,
            &brand,
            &taste,
            &current.input_sha256,
            &[a.clone(), a]
        )
        .is_err()
    );
    let noop = CreativeInstanceEdit::ReplaceCopy {
        field: CopyProperty::Body,
        expected_text: req.copy.body.clone(),
        next_text: req.copy.body.clone(),
    };
    assert!(
        propose_instance_override(
            &current,
            &req,
            &brand,
            &taste,
            &current.input_sha256,
            &[noop]
        )
        .is_err()
    );
}
#[test]
fn forbidden_copy_and_restricted_assets_cannot_bypass_the_original_brand_profile() {
    let (req, mut brand, taste, mut current) = fixture();
    brand.forbidden_claims.push("forbidden-claim".into());
    current = realize(&req, &brand, &taste).unwrap();
    let invalid = propose_instance_override(
        &current,
        &req,
        &brand,
        &taste,
        &current.input_sha256,
        &[CreativeInstanceEdit::ReplaceCopy {
            field: CopyProperty::Headline,
            expected_text: req.copy.headline.clone(),
            next_text: "forbidden-claim".into(),
        }],
    );
    assert!(invalid.is_err());
    let asset = native::Asset {
        id: Uuid::from_u128(99),
        sha256: "aa".repeat(32),
        kind: native::AssetKind::Png,
        rights: native::AssetRights {
            owner: "Fixture author".into(),
            license: "Not authorized".into(),
            attribution: "".into(),
            use_authorized: false,
            redistribute: false,
        },
    };
    let invalid = propose_instance_override(
        &current,
        &req,
        &brand,
        &taste,
        &current.input_sha256,
        &[CreativeInstanceEdit::ReplaceSourceAsset {
            slot: craft::InputSlot::Primary,
            expected_digest: None,
            next: Some(asset),
        }],
    );
    assert!(invalid.is_err());
}
