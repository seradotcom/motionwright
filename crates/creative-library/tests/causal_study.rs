use motionwright_creative_library::{
    self as craft, BrandProfile, ComponentRequest, CopyPack, Locale, ProceduralOptions, RecipeId,
    TasteProfile, native,
};
use uuid::Uuid;

fn study(locale: Locale, width: u32, height: u32) -> ComponentRequest {
    ComponentRequest {
        instance_id: Uuid::from_u128(5544),
        recipe: RecipeId::CausalDiagram,
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
        seed: 512,
        motion: true,
        data: None,
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    }
}
#[test]
fn original_causal_study_labels_a_direction_and_disclaims_evidence_for_three_locales() {
    let brand = BrandProfile::neutral(Uuid::from_u128(1));
    let taste = TasteProfile::editorial(Uuid::from_u128(2));
    for (w, h) in [(640, 360), (360, 640), (640, 640)] {
        for locale in [Locale::En, Locale::Es, Locale::De] {
            let input = study(locale, w, h);
            let result = craft::realize_html(&input, &brand, &taste).unwrap();
            native::validate_document(&result).unwrap();
            assert!(
                result.assets.is_empty(),
                "Pure semantic original study cannot invent product media"
            );
            let stages = result
                .nodes
                .iter()
                .filter(|node| node.name.starts_with("causal stage label "))
                .collect::<Vec<_>>();
            assert_eq!(
                stages.len(),
                3,
                "Three originally authored semantic stages expected"
            );
            assert_eq!(
                result
                    .nodes
                    .iter()
                    .filter(|node| node.name.starts_with("causal arrowhead "))
                    .count(),
                2
            );
            for node in stages {
                let native::Content::Text { runs, size, .. } = &node.content else {
                    panic!("Semantic stage flattened")
                };
                assert!(*size >= 15.0);
                assert!(runs.iter().any(|r| !r.text.trim().is_empty()));
                assert!(node.pose.y + node.pose.height <= h as f64);
            }
            let label = result
                .nodes
                .iter()
                .find(|node| node.name == "causal source classification")
                .unwrap();
            let native::Content::Text { runs, size, .. } = &label.content else {
                panic!("Source caveat flattened")
            };
            assert_eq!(*size, 13.0);
            let disclaimer = &runs[0].text;
            match locale {
                Locale::En => assert!(disclaimer.contains("NO VERIFIED CAUSAL CLAIM")),
                Locale::Es => assert!(disclaimer.contains("SIN PRUEBA DE CAUSALIDAD")),
                Locale::De => assert!(disclaimer.contains("KEIN KAUSALER NACHWEIS")),
            }
            assert!(label.pose.y + label.pose.height <= h as f64);
        }
    }
}
