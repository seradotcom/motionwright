use motionwright_creative_library::{
    self as library, BrandProfile, ComponentRequest, CopyPack, CreativeRealization, Locale,
    ProceduralOptions, RecipeId, TasteProfile, native,
};
use uuid::Uuid;
fn request(width: u32, height: u32, locale: Locale) -> ComponentRequest {
    ComponentRequest {
        instance_id: Uuid::from_u128(9023),
        recipe: RecipeId::HeroReveal,
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
        seed: 55,
        motion: true,
        data: None,
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    }
}
#[test]
fn original_product_hero_is_not_an_invented_app_screenshot() {
    let brand = BrandProfile::neutral(Uuid::from_u128(1));
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    for (w, h) in [(640, 360), (360, 640), (640, 640)] {
        for locale in [Locale::En, Locale::Es, Locale::De] {
            let request = request(w, h, locale);
            let result = library::realize(&request, &brand, &taste).unwrap();
            let CreativeRealization::NativeHtml(doc) = result.output else {
                panic!("Wrong native renderer")
            };
            native::validate_document(&doc).unwrap();
            assert!(
                doc.assets.is_empty(),
                "A hero with no imported product image must not create an asset"
            );
            assert!(doc.nodes.iter().all(|node| !matches!(
                node.content,
                native::Content::Image { .. } | native::Content::Video { .. }
            )));
            assert!(doc.nodes.iter().any(|node| node.name == "hero brand mark"));
            assert!(
                doc.nodes
                    .iter()
                    .any(|node| node.name == "hero evidence classification")
            );
            assert!(doc.nodes.iter().any(|node| node.keyframes.len() >= 2));
            assert!(doc.nodes.iter().any(|node| node.effects.shadow.is_some()));
        }
    }
}
#[test]
fn a_native_product_hero_keeps_owner_source_and_its_identity_stable_during_copy_changes() {
    let brand = BrandProfile::neutral(Uuid::from_u128(1));
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    let mut request = request(640, 360, Locale::En);
    let asset = native::Asset {
        id: Uuid::from_u128(441),
        sha256: "ab".repeat(32),
        kind: native::AssetKind::Png,
        rights: native::AssetRights {
            owner: "Acceptance fixture author".into(),
            license: "Original synthetic fixture".into(),
            attribution: "GRAPHIC STUDY".into(),
            use_authorized: true,
            redistribute: false,
        },
    };
    request.primary_asset = Some(asset.clone());
    let first = library::realize(&request, &brand, &taste).unwrap();
    let CreativeRealization::NativeHtml(doc) = &first.output else {
        panic!("Expected source retained")
    };
    assert!(doc.assets.contains(&asset));
    let img = doc
        .nodes
        .iter()
        .find(|node| node.name == "hero real product surface")
        .unwrap();
    assert!(matches!(img.content,native::Content::Image{asset_id,..}if asset_id==asset.id));
    let stable = img.id;
    let first_digest = first.source_sha256.clone();
    request.copy.headline = "The same source. A different human-edited title.".into();
    let modified = library::realize(&request, &brand, &taste).unwrap();
    let CreativeRealization::NativeHtml(doc) = &modified.output else {
        panic!("Expected source retained")
    };
    assert_eq!(doc.assets[0], asset);
    assert_eq!(
        doc.nodes
            .iter()
            .find(|node| node.name == "hero real product surface")
            .unwrap()
            .id,
        stable
    );
    assert_ne!(first_digest, modified.source_sha256);
    assert!(modified.creative_approval == "human_review_required");
}
#[test]
fn brand_claim_rules_reject_a_prohibited_hero_without_changing_source() {
    let mut brand = BrandProfile::neutral(Uuid::from_u128(1));
    brand.forbidden_claims = vec!["Keep the craft".into()];
    let request = request(640, 360, Locale::En);
    assert!(
        library::realize(
            &request,
            &brand,
            &TasteProfile::editorial(Uuid::from_u128(2))
        )
        .is_err()
    );
}

#[test]
fn product_hero_keeps_authored_copy_and_protects_legibility_at_all_three_ratios() {
    let brand = BrandProfile::neutral(Uuid::from_u128(51));
    let taste = TasteProfile::editorial(Uuid::from_u128(52));
    for (w, h) in [(640, 360), (360, 640), (640, 640)] {
        for locale in [Locale::En, Locale::Es, Locale::De] {
            let authored = request(w, h, locale);
            let doc = library::realize_html(&authored, &brand, &taste).unwrap();
            let body = doc
                .nodes
                .iter()
                .find(|node| node.name == "hero explanation")
                .unwrap();
            let disclosure = doc
                .nodes
                .iter()
                .find(|node| node.name == "hero evidence classification")
                .unwrap();
            let stage = doc
                .nodes
                .iter()
                .find(|node| node.name == "hero product panel")
                .unwrap();
            let native::Content::Text { runs, size, .. } = &body.content else {
                panic!("Hero body must remain editable")
            };
            assert_eq!(
                runs.iter().map(|run| run.text.as_str()).collect::<String>(),
                authored.copy.body
            );
            assert!(
                *size >= brand.minimum_body_size,
                "Body microtype at {w}x{h} {locale:?}"
            );
            let native::Content::Text { runs, size, .. } = &disclosure.content else {
                panic!("Rights caveat must be editable text")
            };
            assert_eq!(
                runs.iter().map(|run| run.text.as_str()).collect::<String>(),
                authored.copy.disclosure
            );
            assert!(
                *size >= 13.0,
                "Evidence caveat microtype at {w}x{h} {locale:?}"
            );
            assert!(disclosure.pose.y + disclosure.pose.height <= f64::from(h));
            if h > w {
                assert!(
                    body.pose.y + body.pose.height < stage.pose.y,
                    "Portrait value statement must not intrude into the native stage"
                );
            } else {
                assert!(
                    body.pose.x + body.pose.width < stage.pose.x,
                    "Landscape value statement must not overlap original stage geometry"
                );
            }
            native::validate_document(&doc).unwrap();
        }
    }
}

#[test]
fn impossible_long_copy_is_rejected_not_shrunk_to_unreadable_type() {
    let brand = BrandProfile::neutral(Uuid::from_u128(51));
    let taste = TasteProfile::editorial(Uuid::from_u128(52));
    let mut req = request(640, 360, Locale::En);
    req.copy.body =
        "An author-written explanation must remain readable to its actual audience. ".repeat(7);
    assert!(req.copy.body.len() < 800);
    let issue = library::realize_html(&req, &brand, &taste)
        .unwrap_err()
        .to_string();
    assert!(issue.contains("cannot fit legibly"), "{issue}");
}
