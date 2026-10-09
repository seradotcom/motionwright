use motionwright_creative_library::native;
use motionwright_creative_library::*;
use uuid::Uuid;
fn asset(id: u128) -> native::Asset {
    native::Asset {
        id: Uuid::from_u128(id),
        sha256: format!("{:064x}", id),
        kind: native::AssetKind::Png,
        rights: native::AssetRights {
            owner: "Acceptance fixture author".into(),
            license: "Original test fixture".into(),
            attribution: "Fixtures are synthetic, not screenshots of product behavior".into(),
            use_authorized: true,
            redistribute: false,
        },
    }
}
fn fixture(recipe: RecipeId, aspect: u8, locale: Locale) -> ComponentRequest {
    let (width, height) = match aspect {
        0 => (640, 360),
        1 => (360, 640),
        _ => (640, 640),
    };
    ComponentRequest {
        instance_id: Uuid::from_u128(221),
        recipe,
        version: 1,
        output: native::Canvas {
            width,
            height,
            rate: native::FrameRate { num: 30, den: 1 },
            frames: 90,
            background: Some("#111922".into()),
        },
        copy: CopyPack::editorial(locale),
        locale,
        seed: 124,
        motion: true,
        data: Some(DataSeries::synthetic(Uuid::from_u128(66))),
        primary_asset: Some(asset(100)),
        secondary_asset: Some(asset(101)),
        procedural: ProceduralOptions::default(),
    }
}
#[test]
fn all_36_recipe_ids_have_unique_semantics_and_six_kits() {
    let defs = RecipeId::ALL.map(|id| id.definition());
    assert_eq!(defs.len(), 36);
    let ids = defs
        .iter()
        .map(|d| d.canonical_id.as_str())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), 36);
    for d in defs {
        assert_eq!(d.version, 1);
        assert!(!d.purpose.is_empty());
        assert!(d.status.contains("requires_source_bound_native_evidence"));
    }
    assert_eq!(creative_kits().len(), 6);
}
#[test]
fn all_recipes_create_distinct_editable_native_or_source_plans_in_three_ratios_and_locales() {
    let brand = BrandProfile::neutral(Uuid::from_u128(1));
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    for recipe in RecipeId::ALL {
        for aspect in 0..3 {
            for locale in [Locale::En, Locale::Es, Locale::De] {
                let request = fixture(recipe, aspect, locale);
                let generated = realize(&request, &brand, &taste)
                    .unwrap_or_else(|error| panic!("{recipe:?} {aspect} {locale:?}: {error}"));
                assert_eq!(generated.recipe, recipe);
                assert_eq!(generated.creative_approval, "human_review_required");
                assert_eq!(generated.input_sha256.len(), 64);
                assert_eq!(generated.source_sha256.len(), 64);
                match generated.output {
                    CreativeRealization::NativeHtml(doc) => {
                        native::validate_document(&doc).unwrap();
                        assert!(doc.nodes.len() >= 2, "{recipe:?}");
                        let identities = doc
                            .nodes
                            .iter()
                            .map(|n| n.id)
                            .collect::<std::collections::BTreeSet<_>>();
                        assert_eq!(identities.len(), doc.nodes.len());
                        assert!(
                            doc.nodes.iter().any(|node| !node.keyframes.is_empty())
                                || !request.motion
                                || matches!(
                                    recipe,
                                    RecipeId::ContextLabel
                                        | RecipeId::ReferenceBoard
                                        | RecipeId::EvidencePair
                                        | RecipeId::Comparison
                                        | RecipeId::CameraMatch
                                        | RecipeId::CausalDiagram
                                        | RecipeId::SeriesReveal
                                        | RecipeId::StateComparison
                                )
                        );
                        let html = native::compile_html(&doc).unwrap();
                        assert!(html.contains("script id=\"mw-source\""));
                    }
                    CreativeRealization::BlenderStage(plan) => {
                        plan.validate().unwrap();
                        assert!(plan.devices.len() >= 1);
                        assert_eq!(plan.cameras[0].frame, 0);
                    }
                    CreativeRealization::AudioScore(plan) => {
                        plan.validate().unwrap();
                        if plan.applies_to_existing_bus {
                            assert!(plan.voices.is_empty())
                        } else {
                            let samples = render_sound_block(&plan, 0, 1024).unwrap();
                            assert_eq!(samples.len(), 1024);
                        }
                    }
                }
            }
        }
    }
}
#[test]
fn the_same_seed_and_authoring_inputs_produce_byte_identical_source() {
    let brand = BrandProfile::neutral(Uuid::nil());
    let taste = TasteProfile::editorial(Uuid::from_u128(3));
    for recipe in RecipeId::ALL {
        let request = fixture(recipe, 1, Locale::En);
        let a = realize(&request, &brand, &taste).unwrap();
        let b = realize(&request, &brand, &taste).unwrap();
        assert_eq!(a, b, "Non-deterministic authoring: {recipe:?}");
    }
}
#[test]
fn missing_media_is_not_replaced_with_fabricated_product_screens() {
    let brand = BrandProfile::neutral(Uuid::nil());
    let taste = TasteProfile::editorial(Uuid::from_u128(3));
    for recipe in [
        RecipeId::ScreenFocus,
        RecipeId::FlowBridge,
        RecipeId::BrowserStage,
        RecipeId::EvidencePair,
    ] {
        let mut request = fixture(recipe, 0, Locale::En);
        request.primary_asset = None;
        assert!(realize(&request, &brand, &taste).is_err(), "{recipe:?}");
    }
    for recipe in [RecipeId::EvidencePair, RecipeId::FlowBridge] {
        let mut request = fixture(recipe, 0, Locale::En);
        request.secondary_asset = None;
        assert!(realize(&request, &brand, &taste).is_err(), "{recipe:?}");
    }
}
#[test]
fn data_cannot_be_relabelled_without_recomputing_its_digest() {
    let mut value = DataSeries::synthetic(Uuid::from_u128(8));
    value.validate().unwrap();
    value.rows[0].value += 1.0;
    assert!(value.validate().is_err());
    value.content_sha256 = value.payload_digest().unwrap();
    value.validate().unwrap();
    value.rows[0].lower = Some(1000.0);
    assert!(value.validate().is_err());
}
#[test]
fn original_sound_is_seekable_and_a_silence_plan_does_not_fake_an_audio_bus() {
    let request = fixture(RecipeId::EnergyBed, 0, Locale::En);
    let plan = sound_component(&request).unwrap();
    let a = render_sound_block(&plan, 5000, 2048).unwrap();
    let b = render_sound_block(&plan, 0, 7048).unwrap();
    assert_eq!(a, &b[5000..7048]);
    assert!(a.iter().all(|sample| {
        sample
            .iter()
            .all(|channel| channel.is_finite() && channel.abs() < 1.0)
    }));
    let mut request = request;
    request.recipe = RecipeId::SilenceRelease;
    let release = sound_component(&request).unwrap();
    assert!(release.applies_to_existing_bus);
    assert_eq!(
        gain_at(&release.gain_envelope, release.sample_frames - 1),
        0.0
    );
    assert!(render_sound_block(&release, 0, 10).is_err());
}
#[test]
fn human_brand_restrictions_are_not_silently_waived() {
    let mut brand = BrandProfile::neutral(Uuid::nil());
    brand.forbidden_claims.push("Make the work".into());
    let taste = TasteProfile::editorial(Uuid::from_u128(3));
    let req = fixture(RecipeId::HeroFocus, 0, Locale::En);
    assert!(realize(&req, &brand, &taste).is_err());
}
#[test]
fn procedural_parameter_changes_modify_geometry_without_random_source_drift() {
    let options = ProceduralOptions::default();
    for recipe in [
        RecipeId::Repeater,
        RecipeId::InfluenceField,
        RecipeId::PathDistribution,
        RecipeId::GridResponse,
        RecipeId::SeededTexture,
    ] {
        let base = placements(recipe, 444, &options, 480.0, 270.0).unwrap();
        assert_eq!(
            base,
            placements(recipe, 444, &options, 480.0, 270.0).unwrap()
        );
        let mut changed = options.clone();
        changed.amplitude = 0.71;
        let revised = placements(recipe, 444, &changed, 480.0, 270.0).unwrap();
        if matches!(recipe, RecipeId::InfluenceField | RecipeId::GridResponse) {
            assert_ne!(base, revised);
        }
    }
}

#[test]
fn a_transparent_native_canvas_is_not_replaced_with_an_opaque_brand_color() {
    let mut request = fixture(RecipeId::HeroFocus, 0, Locale::En);
    request.output.background = None;
    let result = realize_html(
        &request,
        &BrandProfile::neutral(Uuid::nil()),
        &TasteProfile::editorial(Uuid::nil()),
    )
    .unwrap();
    assert_eq!(result.canvas.background, None);
    assert!(
        native::compile_html(&result)
            .unwrap()
            .contains("background:transparent")
    );
}
