use motionwright_domain::*;
use serde_json::json;
use std::collections::BTreeSet;
use uuid::Uuid;

fn id() -> Uuid {
    Uuid::parse_str("00000000-0000-4000-8000-000000000005").unwrap()
}
fn config() -> ProductStageConfig {
    ProductStageConfig {
        author_attestation: "I authored this original generic graphic and authorize publishing it."
            .into(),
        ..Default::default()
    }
}
fn project() -> Project {
    let mut project = Project::new("ProductStage content replacement").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Anonymous screen study".into(),
            objective: "Original semantic UI study, never real app capture".into(),
            duration_seconds: 6,
        })
        .unwrap();
    project
}
fn upsert(p: &mut Project, c: ProductStageConfig) -> Result<()> {
    p.apply_change(&Change::UpsertProductStage {
        instance_id: id(),
        scene_id: p.scenes[0].id,
        config: c,
    })
}
#[test]
fn three_generic_origins_are_bounded_and_keep_every_screen_anchor_across_aspects() {
    for frame in [
        StageFrame::Browser,
        StageFrame::FlatPanel,
        StageFrame::GenericHandset,
    ] {
        let cfg = ProductStageConfig { frame, ..config() };
        for (w, h) in [(1920, 1080), (1080, 1920), (1080, 1080)] {
            let nodes = realize_product_stage(id(), &cfg, w, h).unwrap();
            assert_eq!(nodes.len(), STAGE_ROLES.len());
            assert_eq!(
                nodes.iter().map(|n| n.id).collect::<BTreeSet<_>>().len(),
                STAGE_ROLES.len()
            );
            assert!(nodes.iter().all(|n| n.x >= f64::from(w) * 0.04
                && n.y >= f64::from(h) * 0.04
                && n.x + n.width <= f64::from(w) * 0.96
                && n.y + n.height <= f64::from(h) * 0.95
                && n.width > 0.0
                && n.height > 0.0));
            assert!(
                nodes
                    .iter()
                    .any(|n| n.text.as_deref() == Some(PRODUCT_STAGE_DISCLOSURE))
            );
            let screen = nodes
                .iter()
                .find(|n| n.id == stage_node_id(id(), "screen"))
                .unwrap();
            let focus = nodes
                .iter()
                .find(|n| n.id == stage_node_id(id(), "focus_marker"))
                .unwrap();
            let cx = focus.x + focus.width / 2.0;
            let cy = focus.y + focus.height / 2.0;
            assert!((cx-screen.x-screen.width*.510).abs()<0.000001);
            assert!((cy-screen.y-screen.height*.620).abs()<0.000001);
            assert_eq!(
                nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
                realize_product_stage(
                    id(),
                    &ProductStageConfig {
                        frame: StageFrame::Browser,
                        ..config()
                    },
                    w,
                    h
                )
                .unwrap()
                .iter()
                .map(|n| n.id)
                .collect::<Vec<_>>()
            );
        }
    }
}
#[test]
fn swapping_screen_text_keeps_shell_focus_camera_existing_effects_and_human_style() {
    let mut p = project();
    upsert(&mut p, config()).unwrap();
    let scene_id = p.scenes[0].id;
    let before = p.clone();
    let camera = before.scenes[0].camera.clone();
    let base = &before.production_design.stages[0].baseline;
    let frame = base
        .iter()
        .find(|n| n.id == stage_node_id(id(), "frame"))
        .unwrap()
        .clone();
    let screen = base
        .iter()
        .find(|n| n.id == stage_node_id(id(), "screen"))
        .unwrap()
        .clone();
    let focus = base
        .iter()
        .find(|n| n.id == stage_node_id(id(), "focus_marker"))
        .unwrap()
        .clone();
    let detail = stage_node_id(id(), "screen_detail");
    let mut styled = p.scenes[0]
        .nodes
        .iter()
        .find(|n| n.id == detail)
        .unwrap()
        .style
        .clone();
    styled.fill = Some("#FFE2A0".into());
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id,
        node_id: detail,
        style: styled.clone(),
    })
    .unwrap();
    let mut cfg = config();
    cfg.screen_heading = "A different original benefit".into();
    cfg.screen_detail = "Replacement copy on same screen".into();
    cfg.action_label = "Continue".into();
    upsert(&mut p, cfg).unwrap();
    assert_eq!(p.scenes[0].camera, camera);
    assert_eq!(
        p.scenes[0].nodes.iter().find(|n| n.id == frame.id).unwrap(),
        &frame
    );
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == screen.id)
            .unwrap(),
        &screen
    );
    assert_eq!(
        p.scenes[0].nodes.iter().find(|n| n.id == focus.id).unwrap(),
        &focus
    );
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == detail)
            .unwrap()
            .style,
        styled
    );
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == detail)
            .unwrap()
            .text
            .as_deref(),
        Some("Replacement copy on same screen")
    );
    assert_eq!(p.scenes[0].nodes.len(), 14);
    assert_eq!(p.production_design.stages[0].component_version, 1);
    p.validate().unwrap();
}
#[test]
fn authored_entrance_motion_preserves_identical_screen_anchors_when_content_changes() {
    let cfg = ProductStageConfig {
        animate_focus: true,
        ..config()
    };
    let first = realize_product_stage(id(), &cfg, 1920, 1080).unwrap();
    let focus = first
        .iter()
        .find(|n| n.id == stage_node_id(id(), "focus_marker"))
        .unwrap();
    assert_eq!(focus.keyframes.len(), 4);
    assert_eq!(focus.keyframes[1].at, RationalTime::new(3, 10).unwrap());
    let later = realize_product_stage(
        id(),
        &ProductStageConfig {
            screen_heading: "Another exact idea".into(),
            ..cfg
        },
        1920,
        1080,
    )
    .unwrap();
    let changed_focus = later.iter().find(|n| n.id == focus.id).unwrap();
    assert_eq!(focus, changed_focus);
    let mut p = project();
    upsert(
        &mut p,
        ProductStageConfig {
            animate_focus: true,
            ..config()
        },
    )
    .unwrap();
    let original = p.scenes[0]
        .nodes
        .iter()
        .find(|n| n.id == focus.id)
        .unwrap()
        .keyframes
        .clone();
    upsert(
        &mut p,
        ProductStageConfig {
            screen_heading: "Another exact idea".into(),
            animate_focus: true,
            ..config()
        },
    )
    .unwrap();
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == focus.id)
            .unwrap()
            .keyframes,
        original
    );
}
#[test]
fn third_party_assets_unverified_rights_and_removed_disclosure_fail_closed() {
    let mut p = project();
    assert!(
        upsert(&mut p, ProductStageConfig::default())
            .unwrap_err()
            .to_string()
            .contains("attestation")
    );
    let unlicensed = json!({
        "frame":"commercial_handset",
        "screen_heading":"Fake branded UI",
        "screen_detail":"Unknown ownership",
        "action_label":"Tap",
        "author_attestation":"No real permission",
        "screen_surface":"#132431","foreground":"#F2F4F3","accent":"#86C8D1",
        "focus_x_permille":500,"focus_y_permille":600,"animate_focus":false,
    });
    assert!(serde_json::from_value::<ProductStageConfig>(unlicensed).is_err());
    let external = json!({
        "frame":"browser",
        "screen_heading":"Fake screenshot",
        "screen_detail":"Not a capture",
        "action_label":"View",
        "author_attestation":"Original author",
        "screen_surface":"#132431","foreground":"#F2F4F3","accent":"#86C8D1",
        "focus_x_permille":500,"focus_y_permille":600,"animate_focus":false,
        "unreviewed_screenshot_asset":"file:///secret/photo.png"
    });
    assert!(serde_json::from_value::<ProductStageConfig>(external).is_err());
    upsert(&mut p, config()).unwrap();
    let before = p.clone();
    let disclosure_id = stage_node_id(id(), "disclosure");
    let error = p
        .apply_change(&Change::UpdateCanvasText {
            scene_id: p.scenes[0].id,
            node_id: disclosure_id,
            text: Some("VERIFIED REAL CAPTURE".into()),
        })
        .unwrap_err();
    assert!(error.to_string().contains("disclosure"));
    // Project.apply_change mutates staging state before Project::validate,
    // so authoritative StudioService CAS must be used for atomic admission.
    assert_ne!(p, before);
    assert!(before.validate().is_ok());
}
#[test]
fn human_override_conflicts_but_detach_preserves_every_canvas_object() {
    let mut p = project();
    upsert(&mut p, config()).unwrap();
    let scene_id = p.scenes[0].id;
    let title = stage_node_id(id(), "screen_heading");
    p.apply_change(&Change::UpdateCanvasText {
        scene_id,
        node_id: title,
        text: Some("Human-written title".into()),
    })
    .unwrap();
    let saved = p.clone();
    let err = upsert(
        &mut p,
        ProductStageConfig {
            screen_heading: "Regenerated conflicting title".into(),
            ..config()
        },
    )
    .unwrap_err();
    assert!(err.to_string().contains("conflict"));
    assert_eq!(p, saved);
    let original = p.scenes[0].nodes.clone();
    p.apply_change(&Change::DetachProductStage { instance_id: id() })
        .unwrap();
    assert_eq!(p.scenes[0].nodes, original);
    assert!(p.production_design.stages.is_empty());
    p.apply_change(&Change::UpdateCanvasText {
        scene_id,
        node_id: stage_node_id(id(), "disclosure"),
        text: Some("Detached editorial text".into()),
    })
    .unwrap();
    assert_eq!(p.scenes[0].nodes.len(), 14);
}
#[test]
fn bound_generic_stage_refuses_locked_fields_and_component_overlap() {
    let mut p = project();
    upsert(&mut p, config()).unwrap();
    let scene_id = p.scenes[0].id;
    let existing = p.clone();
    assert!(
        p.apply_change(&Change::UpsertProductHero {
            instance_id: Uuid::new_v4(),
            scene_id,
            config: HeroConfig::default()
        })
        .is_err()
    );
    assert_eq!(p, existing);
    let mut locked = p.clone();
    let title = stage_node_id(id(), "screen_heading");
    locked.scenes[0]
        .nodes
        .iter_mut()
        .find(|n| n.id == title)
        .unwrap()
        .property_locks
        .insert(NodeProperty::Text);
    let err = upsert(
        &mut locked,
        ProductStageConfig {
            screen_heading: "Changed while locked".into(),
            ..config()
        },
    )
    .unwrap_err();
    assert!(err.to_string().contains("locked"));
}
