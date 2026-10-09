use motionwright_creative_library::{
    self as library, ComponentRequest, CopyPack, Locale, ProceduralOptions, RecipeId, SoundPlan,
    native,
};
use std::{fs::OpenOptions, path::Path};
use uuid::Uuid;
fn fixture(recipe: RecipeId) -> ComponentRequest {
    ComponentRequest {
        instance_id: Uuid::from_u128(451),
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
        seed: 1453,
        motion: true,
        data: None,
        primary_asset: None,
        secondary_asset: None,
        procedural: ProceduralOptions::default(),
    }
}
fn run(recipe: RecipeId, wav: &Path, receipt: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let plan: SoundPlan = library::sound_component(&fixture(recipe))?;
    if plan.applies_to_existing_bus {
        return Err(
            "Silence release is a bus automation envelope, not an original generated audio source"
                .into(),
        );
    }
    let mut out = OpenOptions::new().write(true).create_new(true).open(wav)?;
    let proof = library::write_original_wav(&plan, &mut out)?;
    out.sync_all()?;
    std::fs::write(
        receipt,
        serde_json::to_vec_pretty(&serde_json::json!({
            "schema":"motionwright.original-sound-e2e/1",
            "recipe":recipe,"source":plan,"receipt":proof,
            "creative_approval":"required","mastering":"not_performed"
        }))?,
    )?;
    println!(
        "{}",
        serde_json::json!({"original_audio":"WRITTEN","recipe":recipe,"sha256":proof.wav_sha256,"frames":proof.frames})
    );
    Ok(())
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 3 {
        return Err("usage: original_sound_fixture focus-hit|transition-tail|energy-bed|silence-release output.wav receipt.json".into());
    }
    let recipe: RecipeId = serde_json::from_str(&format!("\"{}\"", args[0]))?;
    if recipe.definition().backend != library::RecipeBackend::AudioScore {
        return Err("Audio fixture accepts original sound only".into());
    }
    run(recipe, Path::new(&args[1]), Path::new(&args[2]))
}
