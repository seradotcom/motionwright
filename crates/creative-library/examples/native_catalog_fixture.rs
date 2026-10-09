use motionwright_creative_library::{
    self as library, BrandProfile, ComponentRequest, CopyPack, CreativeRealization, DataSeries,
    Locale, ProceduralOptions, RecipeId, TasteProfile, native,
};
use uuid::Uuid;
fn media(id: u128, digest: &str) -> native::Asset {
    native::Asset {
        id: Uuid::from_u128(id),
        sha256: digest.into(),
        kind: native::AssetKind::Png,
        rights: native::AssetRights {
            owner: "Acceptance fixture author".into(),
            license: "Original synthetic image, fixture only".into(),
            attribution: "FICTIONAL UI - NO PRODUCT PERFORMANCE EVIDENCE".into(),
            use_authorized: true,
            redistribute: false,
        },
    }
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 6 {
        return Err("usage: native_catalog_fixture RECIPE landscape|portrait|square en|es|de RUNTIME_SHA ASSET1_SHA|none ASSET2_SHA|none".into());
    }
    let recipe: RecipeId = serde_json::from_str(&format!("\"{}\"", args[0]))?;
    let (width, height) = match args[1].as_str() {
        "landscape" => (640, 360),
        "portrait" => (360, 640),
        "square" => (640, 640),
        _ => return Err("Unsupported output aspect ratio".into()),
    };
    let locale = match args[2].as_str() {
        "en" => Locale::En,
        "es" => Locale::Es,
        "de" => Locale::De,
        _ => return Err("Unsupported fixture locale".into()),
    };
    let asset = |index: usize, id: u128| -> Option<native::Asset> {
        let digest = args.get(index)?;
        if digest == "none" {
            None
        } else {
            Some(media(id, digest))
        }
    };
    let request = ComponentRequest {
        instance_id: Uuid::from_u128(500),
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
        seed: 512,
        motion: true,
        data: Some(DataSeries::synthetic(Uuid::from_u128(600))),
        primary_asset: asset(4, 700),
        secondary_asset: asset(5, 701),
        procedural: ProceduralOptions::default(),
    };
    let result = library::realize(
        &request,
        &BrandProfile::neutral(Uuid::from_u128(1)),
        &TasteProfile::editorial(Uuid::from_u128(2)),
    )?;
    match &result.output {
        CreativeRealization::NativeHtml(doc) => {
            let plan = native::HyperframesPlan {
                runtime_receipt_sha256: args[3].clone(),
                project_id: Uuid::from_u128(1000),
                generation: Uuid::from_u128(1001),
                revision: 1,
                scene_id: Uuid::from_u128(1002),
                document: doc.clone(),
            };
            native::validate_plan(&plan)?;
            println!(
                "{}",
                serde_json::json!({"schema":"motionwright.catalog-native-fixture/1",
                "recipe":recipe,"output":"native_html","plan_json":serde_json::to_string(&plan)?,
                "source_html":native::compile_html(doc)?,"source_sha256":native::source_digest(doc)?,
                "input_sha256":result.input_sha256,"source_classification":result.source_classification,"approval":"required"})
            );
        }
        _ => println!("{}", serde_json::to_string(&result)?),
    }
    Ok(())
}
