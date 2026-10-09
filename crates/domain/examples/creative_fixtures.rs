//! CI interchange fixtures produced by the actual Rust implementation, not a copied JSON oracle.
use motionwright_domain::*;
use serde_json::json;
use uuid::Uuid;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let id = Uuid::parse_str("00000000-0000-4000-8000-000000000005")?;
    let configs = [HeroConfig::default(), HeroConfig { motion: false, ..HeroConfig::default() }, HeroConfig {
        eyebrow: "CREATIVE SYSTEM / FIELD NOTES".into(), headline: "One project.\nEvery revision.".into(),
        body: "Keep each deliberate change. Build the next version without losing the previous decision.".into(),
        wordmark: "FN".into(), accent: "#D9A46E".into(), ..HeroConfig::default()
    }];
    let mut heroes = vec![];
    for config in &configs {
        for (width, height) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
            heroes.push(json!({"id":id,"config":config,"width":width,"height":height,"nodes":realize_product_hero(id,config,width,height)?}));
        }
    }
    let base = realize_product_hero(id, &configs[0], 1920, 1080)?;
    let mut current = base.clone();
    current
        .iter_mut()
        .find(|n| n.id == hero_node_id(id, "body"))
        .unwrap()
        .text = Some("A deliberate human editorial decision.".into());
    current
        .iter_mut()
        .find(|n| n.id == hero_node_id(id, "wordmark"))
        .unwrap()
        .x += 8.0;
    let incoming = realize_product_hero(
        id,
        &HeroConfig {
            accent: "#C4AF84".into(),
            ..configs[0].clone()
        },
        1920,
        1080,
    )?;
    let merged = merge_component_nodes(&base, &current, &incoming)?;
    let plan = ProductionPlan {
        objective: "Explain an editable creative workflow".into(),
        audience: "Creative software teams".into(),
        concept: "Typography carries the argument; observed software proves the behavior.".into(),
        reference_constraints: vec!["Keep the frame hierarchy legible during movement.".into()],
        exclusions: vec!["Do not fabricate product evidence.".into()],
        shots: vec![PlannedShot {
            scene_id: id,
            purpose: "State a clear idea".into(),
            claim_ids: vec![],
            asset_ids: vec![],
            evidence_kind: NarrativeEvidenceKind::GraphicStudy,
        }],
        clock: ProductionClock::Timeline,
        approval: None,
    };
    let mut plans = vec![];
    for clock in [
        ProductionClock::Timeline,
        ProductionClock::Voice { voice_track_id: id },
        ProductionClock::Music {
            asset_id: id,
            beats: vec![
                RationalTime::ZERO,
                RationalTime::new(1001, 30000)?,
                RationalTime::new(1, 2)?,
            ],
        },
    ] {
        let variant = ProductionPlan {
            clock,
            ..plan.clone()
        };
        plans.push(json!({"plan":variant,"content_sha256":variant.content_digest()?}));
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "schema":"motionwright.rust-studio-creative-parity/1","hero_cases":heroes,
            "three_way_merge":{"base":base,"current":current,"incoming":incoming,"expected":merged},
            "plan_digest_cases":plans,"scope":"interchange and deterministic realization; not asset or execution admission"
        }))?
    );
    Ok(())
}
