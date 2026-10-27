//! CI interchange fixtures produced by the actual Rust implementation, not a copied JSON oracle.
use motionwright_domain::*;
use serde_json::json;
use uuid::Uuid;

fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let id = Uuid::parse_str("00000000-0000-4000-8000-000000000005")?;
    let configs = [HeroConfig::default(), HeroConfig { motion: false, ..HeroConfig::default() }, HeroConfig {
        eyebrow: "CREATIVE SYSTEM / FIELD NOTES".into(), headline: "One project.\nEvery revision.".into(),
        body: "Keep each deliberate change. Build the next version without losing the previous decision.".into(),
        wordmark: "FN".into(), accent: "#D9A46E".into(), ..HeroConfig::default()
    }, HeroConfig {layout:HeroLayout::SplitExplanation,
        eyebrow:"TWO PARTS / ONE EXPLANATION".into(),
        headline:"What changes".into(),body:"Keep each decision editable through every subsequent revision.".into(),
        wordmark:"WHY".into(),..HeroConfig::default()
    }, HeroConfig {layout:HeroLayout::MetricEvidence,
        eyebrow:"AUTHOR SUPPLIED / VALIDATION REQUIRED".into(),
        headline:"42% growth".into(),
        body:"An editorial statement is not measured evidence until its source is attached and verified.".into(),
        wordmark:"DATA".into(), accent:"#D9A46E".into(), ..HeroConfig::default()
    }, HeroConfig {layout:HeroLayout::StepSequence,
        eyebrow:"EXPLAINER / AUTHORED STEP".into(),
        headline:"Inspect the intended result".into(),
        body:"Review the native scene state and confirm what changed before approving the next step.".into(),
        wordmark:"02 / 04".into(), accent:"#D9A46E".into(), ..HeroConfig::default()
    }, HeroConfig {layout:HeroLayout::ContrastComparison,
        eyebrow:"EDITORIAL / ORIGINAL STUDY".into(),
        headline:"A deliberate original idea".into(),
        body:"An independently authored supporting idea with clear intent".into(),
        wordmark:"MW".into(), accent:"#86C8D1".into(), ..HeroConfig::default()
    }, HeroConfig {layout:HeroLayout::EditorialQuote,
        eyebrow:"EDITORIAL / ORIGINAL STUDY".into(),
        headline:"A deliberate original idea".into(),
        body:"An independently authored supporting idea with clear intent".into(),
        wordmark:"MW".into(), accent:"#86C8D1".into(), ..HeroConfig::default()
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
    // Both craft layouts use the real domain merge, including a layout change.
    // These are source/serialization oracles, not a SQLite or renderer receipt.
    let mut craft_three_way_merge_cases = Vec::new();
    for config in configs.iter().filter(|config| matches!(
        config.layout, HeroLayout::ContrastComparison | HeroLayout::EditorialQuote
    )) {
        let base = realize_product_hero(id, config, 1920, 1080)?;
        let mut current = base.clone();
        let eyebrow = hero_node_id(id, "eyebrow");
        let body = hero_node_id(id, "body");
        current.iter_mut().find(|node| node.id == eyebrow).unwrap().text =
            Some("Human-authored context".into());
        current.iter_mut().find(|node| node.id == body).unwrap().style.fill =
            Some("#FFE0A0".into());
        let incoming_config = HeroConfig {
            layout: if config.layout == HeroLayout::ContrastComparison {
                HeroLayout::EditorialQuote
            } else {
                HeroLayout::ContrastComparison
            },
            headline: "A revised original idea".into(),
            accent: "#C4AF84".into(),
            ..config.clone()
        };
        let incoming = realize_product_hero(id, &incoming_config, 1920, 1080)?;
        let expected = merge_component_nodes(&base, &current, &incoming)?;
        assert_eq!(expected.iter().find(|node| node.id == eyebrow).unwrap().text.as_deref(),
                   Some("Human-authored context"));
        assert_eq!(expected.iter().find(|node| node.id == body).unwrap().style.fill.as_deref(),
                   Some("#FFE0A0"));
        assert_eq!(expected.iter().map(|node| node.id).collect::<Vec<_>>(),
                   base.iter().map(|node| node.id).collect::<Vec<_>>());
        let serialized_roundtrip: Vec<CanvasNode> =
            serde_json::from_str(&serde_json::to_string(&expected)?)?;
        assert_eq!(serialized_roundtrip, expected);
        let mut conflict = incoming.clone();
        conflict.iter_mut().find(|node| node.id == eyebrow).unwrap().text =
            Some("Conflicting generated context".into());
        let conflict_error = merge_component_nodes(&base, &current, &conflict)
            .expect_err("a conflicting craft copy update must be rejected")
            .to_string();
        assert!(conflict_error.contains("override conflict"));
        craft_three_way_merge_cases.push(json!({
            "id":id,"width":1920,"height":1080,"base_config":config,
            "incoming_config":incoming_config,"base":base,"current":current,
            "incoming":incoming,"expected":expected,"serialized_roundtrip":serialized_roundtrip,
            "conflict":conflict,"conflict_error":conflict_error
        }));
    }
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
    let brand = BrandProfile {
        id: Uuid::parse_str("00000000-0000-4000-8000-000000000041")?,
        label: "Editorial evidence brand".into(),
        version: 3,
        rules: vec![
            BrandRule::ForbiddenPhrase {
                id: Uuid::parse_str("00000000-0000-4000-8000-000000000042")?,
                phrase: "fabricated metrics".into(),
            },
            BrandRule::AllowedAccents {
                id: Uuid::parse_str("00000000-0000-4000-8000-000000000043")?,
                colors: vec!["#D9A46E".into(), "#A5C8DF".into()],
            },
            BrandRule::RequiredWordmark {
                id: Uuid::parse_str("00000000-0000-4000-8000-000000000044")?,
                text: "DATA".into(),
            },
        ],
    };
    let mut brand_cases = Vec::new();
    for profile in [
        brand.clone(),
        BrandProfile {
            version: 4,
            ..brand
        },
    ] {
        profile.validate()?;
        let content_sha256 = profile.content_digest()?;
        brand_cases.push(json!({"profile":profile,"content_sha256":content_sha256}));
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "schema":"motionwright.rust-studio-creative-parity/1","hero_cases":heroes,
            "three_way_merge":{"base":base,"current":current,"incoming":incoming,"expected":merged},
            "craft_three_way_merge_cases":craft_three_way_merge_cases,
            "plan_digest_cases":plans,"brand_profile_cases":brand_cases,
            "scope":"interchange and deterministic realization; not asset or execution admission"
        }))?
    );
    Ok(())
}
