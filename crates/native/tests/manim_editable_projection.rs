//! Source/SQLite tests only. Native rendering, glyph pixels and reopening in a
//! new Manim process remain separate original-Host G2 observations.
use motionwright_domain::{
    BlendMode, CanvasKeyframe, CanvasNode, Change, CoordinateSpace, CreativePatch, FontRight,
    MotionInterpolation, MotionProperty, NodeProperty, NodeStyle, Project, RationalTime,
    RendererKind, RevisionStamp, RightsStatus, ScopedCanvasEdit, VisualToken,
};
use motionwright_manim_profile::{SOURCE_BUNDLE_MIME_V2, editable_scene as profile};
use motionwright_native::manim_editable_projection::*;
use motionwright_service::StudioService;
use semwright_native_sdk::ErrorCode;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};
use uuid::Uuid;
const FONT: &[u8] = include_bytes!("../../manim-profile/tests/fixtures/Montserrat-variable.ttf");
fn apply(service: &StudioService, p: &mut Project, change: Change) {
    *p = service
        .apply(
            p.id,
            &RevisionStamp::from(&*p),
            &format!("editable-fixture-{}", Uuid::now_v7()),
            &change,
        )
        .unwrap()
        .project;
}
fn attach(
    service: &StudioService,
    dir: &Path,
    p: &Project,
    name: &str,
    bytes: &[u8],
    mime: &str,
) -> (Project, Uuid) {
    let path = dir.join(format!("{name}.data"));
    std::fs::write(&path, bytes).unwrap();
    let attached = service
        .import_asset_file(
            p.id,
            &RevisionStamp::from(p),
            name,
            &path,
            name.into(),
            mime.into(),
        )
        .unwrap()
        .project;
    std::fs::remove_file(path).unwrap();
    let id = attached
        .assets
        .iter()
        .find(|asset| asset.name == name)
        .unwrap()
        .id;
    (attached, id)
}
fn source(service: &StudioService, dir: &Path) -> Project {
    let mut p = service
        .create_project("Editable native text source")
        .unwrap();
    apply(
        service,
        &mut p,
        Change::AddScene {
            name: "Offset".into(),
            objective: "Preserve source timing".into(),
            duration_seconds: 1,
        },
    );
    apply(
        service,
        &mut p,
        Change::AddScene {
            name: "Editable text".into(),
            objective: "Native font source".into(),
            duration_seconds: 4,
        },
    );
    let scene_id = p.scenes[1].id;
    apply(
        service,
        &mut p,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    );
    let (next, font) = attach(service, dir, &p, "licensed-font", FONT, "font/ttf");
    p = next;
    let mut language = p.visual_language.clone();
    language
        .palette
        .retain(|row| !row.name.eq_ignore_ascii_case("background"));
    language.palette.push(VisualToken {
        name: "background".into(),
        value: "#153047".into(),
    });
    language.fonts.push(FontRight {
        family: "Montserrat".into(),
        source: format!("asset:{font}"),
        rights_status: RightsStatus::Cleared,
    });
    apply(
        service,
        &mut p,
        Change::SetVisualLanguage {
            visual_language: language,
        },
    );
    for (ordinal, kind, x, y, w, h) in [
        (0, "text", 650.0, 380.0, 620.0, 300.0),
        (1, "rectangle", 850.0, 780.0, 220.0, 80.0),
    ] {
        apply(
            service,
            &mut p,
            Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id: Uuid::now_v7(),
                    name: format!("Native editable {ordinal}"),
                    kind: kind.into(),
                    parent_id: None,
                    x,
                    y,
                    width: w,
                    height: h,
                    rotation_deg: if ordinal == 0 { 17.0 } else { -11.0 },
                    opacity: 0.65,
                    text: (ordinal == 0).then(|| "Native title\nSecond line".into()),
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: ordinal,
                    style: NodeStyle {
                        fill: Some("#EAC454".into()),
                        font_family: (ordinal == 0).then(|| "Montserrat".into()),
                        font_size: (ordinal == 0).then_some(64.0),
                        font_weight: (ordinal == 0).then_some(700),
                        line_height: (ordinal == 0).then_some(1.2),
                        ..Default::default()
                    },
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                },
            },
        );
    }
    let mut output = p.deliverables[0].clone();
    output.width = 960;
    output.height = 540;
    output.frame_rate = RationalTime::new(30, 1).unwrap();
    output.captions = false;
    output.burn_in_captions = false;
    apply(
        service,
        &mut p,
        Change::UpsertDeliverable { profile: output },
    );
    p.validate().unwrap();
    p
}
fn plan(p: &Project) -> profile::Plan {
    build_manim_editable_plan(
        p,
        &RevisionStamp::from(p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap()
}
fn font_entries(package: &profile::Package) -> BTreeMap<String, Vec<u8>> {
    package
        .entries
        .iter()
        .filter(|(name, _)| name.starts_with("fonts/"))
        .map(|(name, data)| (name.clone(), data.clone()))
        .collect()
}
#[test]
fn editable_projection_preserves_full_source_font_identity_clock_and_transform() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source(&service, temp.path());
    let before = p.clone();
    let projected = plan(&p);
    let original = &p.scenes[1].nodes[0];
    let actual = &projected.nodes[0];
    assert_eq!(actual.node_id, original.id);
    assert_eq!(actual.rotation_deg, 17.0);
    assert_eq!(actual.opacity, 0.65);
    assert_eq!(
        actual.typography.as_ref().unwrap().text,
        original.text.clone().unwrap()
    );
    assert_eq!(projected.fonts[0].family, "Montserrat");
    assert_eq!(
        projected.fonts[0].sha256,
        "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7"
    );
    assert_eq!(
        (
            projected.clock.project_start_frame,
            projected.clock.frame_count
        ),
        (30, 120)
    );
    let package = prepare_saved_manim_editable_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    assert_eq!(package.entries[&projected.fonts[0].archive_path()], FONT);
    for (width, height) in [(960, 540), (540, 960), (720, 720)] {
        let mut q = p.clone();
        q.deliverables[0].width = width;
        q.deliverables[0].height = height;
        let projection = plan(&q);
        assert_eq!(projection.render_profile.width, width);
        assert_eq!(projection.nodes, projected.nodes);
    }
    assert_eq!(service.project(p.id).unwrap(), before);
    assert!(service.production_receipts(p.id, 100).unwrap().is_empty());
}
#[test]
fn editable_projection_refuses_unrepresentable_or_uncleared_data_without_rewriting_project() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source(&service, temp.path());
    for property in [
        "blend_multiply",
        "blend_screen",
        "blend_add",
        "keyframes",
        "parent",
        "stroke",
        "graph",
        "font_missing",
        "rights_unknown",
        "clock_fraction",
    ] {
        let mut q = p.clone();
        let parent = q.scenes[1].nodes[1].id;
        let node = &mut q.scenes[1].nodes[0];
        match property {
            "blend_multiply" => node.style.blend_mode = BlendMode::Multiply,
            "blend_screen" => node.style.blend_mode = BlendMode::Screen,
            "blend_add" => node.style.blend_mode = BlendMode::Add,
            "keyframes" => node.keyframes.push(CanvasKeyframe {
                at: RationalTime::new(1, 1).unwrap(),
                property: MotionProperty::X,
                value: 700.0,
                interpolation: MotionInterpolation::Linear,
            }),
            "parent" => node.parent_id = Some(parent),
            "stroke" => {
                node.style.stroke = Some("#FFFFFF".into());
                node.style.stroke_width = 2.0;
            }
            "graph" => node.kind = "graph".into(),
            "font_missing" => node.style.font_family = Some("Absent".into()),
            "rights_unknown" => {
                q.visual_language
                    .fonts
                    .iter_mut()
                    .find(|font| font.family == "Montserrat")
                    .unwrap()
                    .rights_status = RightsStatus::Unknown
            }
            "clock_fraction" => q.scenes[1].duration = RationalTime::new(121, 31).unwrap(),
            _ => unreachable!(),
        }
        let before = q.clone();
        let error = build_manim_editable_plan(
            &q,
            &RevisionStamp::from(&q),
            q.scenes[1].id,
            q.deliverables[0].id,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported, "{property}");
        assert_eq!(q, before, "{property}");
    }
    assert_eq!(service.project(p.id).unwrap(), p);
}
#[test]
fn editable_text_import_is_one_service_change_and_survives_sqlite_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("source.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let p = source(&service, temp.path());
    let bundle = export_saved_manim_editable_source_bundle(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let original = profile::inspect_source_bundle(&bundle).unwrap();
    let (p, original_id) = attach(
        &service,
        temp.path(),
        &p,
        "source",
        &bundle,
        SOURCE_BUNDLE_MIME_V2,
    );
    let read =
        inspect_saved_manim_editable_source(&service, p.id, &RevisionStamp::from(&p), original_id)
            .unwrap();
    assert!(read.revision_only_rebound);
    assert!(!read.native_execution_performed);
    assert!(!read.imported_source_authority_used);
    let mut changed = original.plan.clone();
    changed.nodes[0].typography.as_mut().unwrap().text = "Edited title\nStill native".into();
    let node_id = changed.nodes[0].node_id;
    let bytes = profile::source_bundle(&changed, &font_entries(&original)).unwrap();
    let (p, edited_id) = attach(
        &service,
        temp.path(),
        &p,
        "edited",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let expected = RevisionStamp::from(&p);
    let preview = preview_saved_manim_editable_edit(
        &service,
        p.id,
        &expected,
        original_id,
        edited_id,
        node_id,
        EditableProperty::Text,
    )
    .unwrap();
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(matches!(&preview.change, Change::UpdateCanvasText { .. }));
    let after = service
        .apply(
            p.id,
            &expected,
            "apply-single-editable-text",
            &preview.change,
        )
        .unwrap()
        .project;
    assert_eq!(after.revision, p.revision + 1);
    assert_eq!(
        after.scenes[1].nodes[0].text.as_deref(),
        Some("Edited title\nStill native")
    );
    assert_eq!(after.scenes[1].nodes[0].style, p.scenes[1].nodes[0].style);
    assert_eq!(after.scenes[1].nodes[1], p.scenes[1].nodes[1]);
    assert_eq!(after.scenes[1].camera, p.scenes[1].camera);
    assert_eq!(after.scenes[1].duration, p.scenes[1].duration);
    assert_eq!(after.assets, p.assets);
    drop(service);
    let reopened = StudioService::open(database).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), after);
    let read = inspect_saved_manim_editable_source(
        &reopened,
        p.id,
        &RevisionStamp::from(&after),
        edited_id,
    )
    .unwrap();
    assert_eq!(read.current_plan.nodes[0].node_id, node_id);
    assert_eq!(
        read.current_plan.nodes[0].typography.as_ref().unwrap().text,
        "Edited title\nStill native"
    );
    assert_eq!(read.current_plan.clock.frame_count, 120);
    assert!(reopened.production_receipts(p.id, 100).unwrap().is_empty());
    assert_eq!(
        inspect_saved_manim_editable_source(&reopened, p.id, &expected, edited_id)
            .unwrap_err()
            .code,
        ErrorCode::StaleReference
    );
}
#[test]
fn editable_import_refuses_extra_style_change_and_locked_text_without_any_commit() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source(&service, temp.path());
    let package = prepare_saved_manim_editable_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let bytes = profile::source_bundle(&package.plan, &font_entries(&package)).unwrap();
    let (p, original_id) = attach(
        &service,
        temp.path(),
        &p,
        "source",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let mut changed = package.plan.clone();
    let node_id = changed.nodes[0].node_id;
    changed.nodes[0].typography.as_mut().unwrap().text = "Edited title".into();
    changed.nodes[0].opacity = 0.5;
    let bytes = profile::source_bundle(&changed, &font_entries(&package)).unwrap();
    let (p, edited_id) = attach(
        &service,
        temp.path(),
        &p,
        "extra",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    assert_eq!(
        preview_saved_manim_editable_edit(
            &service,
            p.id,
            &RevisionStamp::from(&p),
            original_id,
            edited_id,
            node_id,
            EditableProperty::Text
        )
        .unwrap_err()
        .code,
        ErrorCode::Unsupported
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    let mut locked = p.clone();
    locked.scenes[1].nodes[0]
        .property_locks
        .insert(NodeProperty::Text);
    let before = locked.clone();
    assert!(
        locked
            .apply_change(&Change::UpdateCanvasText {
                scene_id: p.scenes[1].id,
                node_id,
                text: Some("No bypass".into())
            })
            .is_err()
    );
    assert_eq!(locked, before);
    assert!(service.production_receipts(p.id, 100).unwrap().is_empty());
}
#[test]
fn editable_style_roundtrip_changes_font_metrics_through_existing_style_change() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source(&service, temp.path());
    let package = prepare_saved_manim_editable_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let fonts = font_entries(&package);
    let bytes = profile::source_bundle(&package.plan, &fonts).unwrap();
    let (p, original_id) = attach(
        &service,
        temp.path(),
        &p,
        "source",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let mut changed = package.plan.clone();
    let node_id = changed.nodes[0].node_id;
    let text = changed.nodes[0].typography.as_mut().unwrap();
    text.font_size_px = 72.0;
    text.weight = 400;
    text.line_height = 1.3;
    let bytes = profile::source_bundle(&changed, &fonts).unwrap();
    let (p, edited_id) = attach(
        &service,
        temp.path(),
        &p,
        "style",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let expected = RevisionStamp::from(&p);
    let preview = preview_saved_manim_editable_edit(
        &service,
        p.id,
        &expected,
        original_id,
        edited_id,
        node_id,
        EditableProperty::Style,
    )
    .unwrap();
    assert!(matches!(&preview.change, Change::UpdateCanvasStyle { .. }));
    let committed = service
        .apply(p.id, &expected, "apply-one-style", &preview.change)
        .unwrap()
        .project;
    assert_eq!(committed.scenes[1].nodes[0].style.font_size, Some(72.0));
    assert_eq!(committed.scenes[1].nodes[0].style.font_weight, Some(400));
    assert_eq!(committed.scenes[1].nodes[0].text, p.scenes[1].nodes[0].text);
    assert_eq!(committed.scenes[1].nodes[0].rotation_deg, 17.0);
    assert!(
        inspect_saved_manim_editable_source(
            &service,
            p.id,
            &RevisionStamp::from(&committed),
            edited_id
        )
        .is_ok()
    );
}

fn input_for(name: &str, bytes: &[u8]) -> OriginalEditableBundleInput {
    use sha2::{Digest, Sha256};
    OriginalEditableBundleInput {
        root: OriginalEditableBundleRoot::Output,
        relative_path: name.into(),
        sha256: hex::encode(Sha256::digest(bytes)),
        bytes: bytes.len() as u64,
    }
}

#[test]
fn original_exported_input_is_the_exact_current_plan_without_font_replacement() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("source.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let p = source(&service, temp.path());
    let expected = RevisionStamp::from(&p);
    let package = prepare_saved_manim_editable_source(
        &service,
        p.id,
        &expected,
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let bytes = export_saved_manim_editable_source_bundle(
        &service,
        p.id,
        &expected,
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let output = temp.path().join("output");
    std::fs::create_dir(&output).unwrap();
    let input = input_for("original.mwedit.zip", &bytes);
    std::fs::write(output.join(&input.relative_path), &bytes).unwrap();
    let observed = prepare_saved_manim_editable_bundle_input(
        &service,
        p.id,
        &expected,
        p.scenes[1].id,
        p.deliverables[0].id,
        &output,
        &input,
    )
    .unwrap();
    assert_eq!(observed.plan, package.plan);
    assert_eq!(observed.entries, package.entries);
    assert_eq!(observed.closure, package.closure);
    assert_eq!(std::fs::read_dir(&output).unwrap().count(), 1);
    assert_eq!(
        std::fs::read(output.join(&input.relative_path)).unwrap(),
        bytes
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
    let mut changed = package.plan.clone();
    changed.nodes[0].typography.as_mut().unwrap().text = "Different valid carrier".into();
    let different = profile::source_bundle(&changed, &font_entries(&package)).unwrap();
    let wrong = input_for("different.mwedit.zip", &different);
    std::fs::write(output.join(&wrong.relative_path), different).unwrap();
    assert_eq!(
        prepare_saved_manim_editable_bundle_input(
            &service,
            p.id,
            &expected,
            p.scenes[1].id,
            p.deliverables[0].id,
            &output,
            &wrong,
        )
        .unwrap_err()
        .code,
        ErrorCode::Unsupported
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
}

#[test]
fn original_input_contract_rejects_missing_changed_or_ungranted_carriers_before_dispatch() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("source.sqlite3")).unwrap();
    let p = source(&service, temp.path());
    let expected = RevisionStamp::from(&p);
    let bytes = export_saved_manim_editable_source_bundle(
        &service,
        p.id,
        &expected,
        p.scenes[1].id,
        p.deliverables[0].id,
    )
    .unwrap();
    let output = temp.path().join("output");
    std::fs::create_dir(&output).unwrap();
    let input = input_for("original.mwedit.zip", &bytes);
    let prepare = |input: &OriginalEditableBundleInput| {
        prepare_saved_manim_editable_bundle_input(
            &service,
            p.id,
            &expected,
            p.scenes[1].id,
            p.deliverables[0].id,
            &output,
            input,
        )
    };
    assert!(prepare(&input).is_err());
    std::fs::write(output.join(&input.relative_path), &bytes).unwrap();
    assert!(prepare(&input).is_ok());
    for (field, value) in [
        ("root", serde_json::json!("work")),
        ("root", serde_json::json!(null)),
        ("bytes", serde_json::json!(-1)),
    ] {
        let mut forged = serde_json::to_value(&input).unwrap();
        forged[field] = value;
        assert!(serde_json::from_value::<OriginalEditableBundleInput>(forged).is_err());
    }
    let mut extra = serde_json::to_value(&input).unwrap();
    extra["path"] = serde_json::json!("../../outside.py");
    assert!(serde_json::from_value::<OriginalEditableBundleInput>(extra).is_err());
    for path in [
        "../original.mwedit.zip",
        "C:\\\\outside.mwedit.zip",
        "source.py",
    ] {
        let mut forged = input.clone();
        forged.relative_path = path.into();
        assert!(prepare(&forged).is_err());
    }
    let mut forged = input.clone();
    forged.sha256 = "0".repeat(64);
    assert!(prepare(&forged).is_err());
    let mut forged = input.clone();
    forged.bytes += 1;
    assert!(prepare(&forged).is_err());
    let mut altered = bytes.clone();
    altered[0] ^= 1;
    std::fs::write(output.join(&input.relative_path), &altered).unwrap();
    let current_digest = input_for(&input.relative_path, &altered);
    assert!(prepare(&current_digest).is_err());
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 256).unwrap().is_empty());
}

#[test]
fn compiled_manim_candidate_is_one_inert_source_edit_and_reopens_without_native_effect() {
    for (index, property) in [
        EditableProperty::Transform,
        EditableProperty::Text,
        EditableProperty::Style,
        EditableProperty::Controls,
    ]
    .into_iter()
    .enumerate()
    {
        let temp = tempfile::tempdir().unwrap();
        let db = temp.path().join("candidate.sqlite3");
        let service = StudioService::open(&db).unwrap();
        let original_project = source(&service, temp.path());
        let scene_id = original_project.scenes[1].id;
        let deliverable_id = original_project.deliverables[0].id;
        let node = original_project.scenes[1].nodes[0].clone();
        let original_bytes = export_saved_manim_editable_source_bundle(
            &service,
            original_project.id,
            &RevisionStamp::from(&original_project),
            scene_id,
            deliverable_id,
        )
        .unwrap();
        let (saved, original_id) = attach(
            &service,
            temp.path(),
            &original_project,
            "candidate-original",
            &original_bytes,
            SOURCE_BUNDLE_MIME_V2,
        );
        let change = match property {
            EditableProperty::Transform => Change::TransformCanvasNode {
                scene_id,
                node_id: node.id,
                transform: motionwright_domain::CanvasTransform {
                    x: node.x + 10.0,
                    y: node.y,
                    width: node.width,
                    height: node.height,
                    rotation_deg: node.rotation_deg - 2.0,
                    opacity: node.opacity,
                },
            },
            EditableProperty::Text => Change::UpdateCanvasText {
                scene_id,
                node_id: node.id,
                text: Some("Reviewed native title\nSecond line".into()),
            },
            EditableProperty::Style => Change::UpdateCanvasStyle {
                scene_id,
                node_id: node.id,
                style: NodeStyle {
                    fill: Some("#86C8D1".into()),
                    ..node.style.clone()
                },
            },
            EditableProperty::Controls => Change::ApplyCreativePatch {
                patch: CreativePatch {
                    scene_id,
                    base_revision: saved.revision,
                    rationale: EDITABLE_CONTROLS_RATIONALE.into(),
                    edits: vec![
                        ScopedCanvasEdit::Transform {
                            node_id: node.id,
                            transform: motionwright_domain::CanvasTransform {
                                x: node.x + 10.0,
                                y: node.y + 5.0,
                                width: node.width - 10.0,
                                height: node.height,
                                rotation_deg: node.rotation_deg - 2.0,
                                opacity: 0.75,
                            },
                        },
                        ScopedCanvasEdit::Text {
                            node_id: node.id,
                            text: "Reviewed full object\nSecond line".into(),
                        },
                        ScopedCanvasEdit::Style {
                            node_id: node.id,
                            style: NodeStyle {
                                fill: Some("#86C8D1".into()),
                                font_size: Some(58.0),
                                ..node.style.clone()
                            },
                        },
                    ],
                },
            },
        };
        let expected = RevisionStamp::from(&saved);
        let compiled = compile_saved_manim_editable_candidate(
            &service,
            saved.id,
            &expected,
            original_id,
            scene_id,
            deliverable_id,
            &change,
        )
        .unwrap();
        assert_ne!(compiled.current_plan_sha256, compiled.proposed_plan_sha256);
        assert_eq!(
            compiled.original_source_sha256,
            saved
                .assets
                .iter()
                .find(|asset| asset.id == original_id)
                .unwrap()
                .content_sha256
                .clone()
                .unwrap()
        );
        assert_eq!(service.project(saved.id).unwrap(), saved);
        let package = profile::inspect_source_bundle(&compiled.bytes).unwrap();
        assert_eq!(package.closure.plan_sha256, compiled.proposed_plan_sha256);
        assert_eq!(package.plan.binding.scene_id, scene_id);
        assert_eq!(package.plan.binding.deliverable_id, deliverable_id);
        assert_eq!(package.plan.nodes[1], plan(&saved).nodes[1]);
        let again = compile_saved_manim_editable_candidate(
            &service,
            saved.id,
            &expected,
            original_id,
            scene_id,
            deliverable_id,
            &change,
        )
        .unwrap();
        assert_eq!(again.bytes, compiled.bytes);
        let (attached, edited_id) = attach(
            &service,
            temp.path(),
            &saved,
            "candidate-edited",
            &compiled.bytes,
            SOURCE_BUNDLE_MIME_V2,
        );
        let preview = preview_saved_manim_editable_edit(
            &service,
            attached.id,
            &RevisionStamp::from(&attached),
            original_id,
            edited_id,
            node.id,
            property,
        )
        .unwrap();
        let mut expected_change = change.clone();
        if let Change::ApplyCreativePatch { patch } = &mut expected_change {
            // Attaching the inert candidate advances source revision, not controls.
            patch.base_revision = attached.revision;
        }
        assert_eq!(
            serde_json::to_value(&preview.change).unwrap(),
            serde_json::to_value(&expected_change).unwrap()
        );
        assert_eq!(service.project(attached.id).unwrap(), attached);
        let committed = service
            .apply(
                attached.id,
                &RevisionStamp::from(&attached),
                &format!("reviewed-candidate-{index}"),
                &preview.change,
            )
            .unwrap()
            .project;
        assert_eq!(committed.revision, attached.revision + 1);
        assert_eq!(committed.assets, attached.assets);
        assert_eq!(committed.scenes[1].nodes[1], attached.scenes[1].nodes[1]);
        drop(service);
        let reopened = StudioService::open(&db).unwrap();
        assert_eq!(reopened.project(committed.id).unwrap(), committed);
        let inspected = inspect_saved_manim_editable_source(
            &reopened,
            committed.id,
            &RevisionStamp::from(&committed),
            edited_id,
        )
        .unwrap();
        assert!(!inspected.native_execution_performed && !inspected.imported_source_authority_used);
        assert!(
            reopened
                .production_receipts(committed.id, 100)
                .unwrap()
                .is_empty()
        );
        if matches!(property, EditableProperty::Controls) {
            let patch = committed.production_design.patches.last().unwrap();
            assert_eq!(patch.before, vec![attached.scenes[1].nodes[0].clone()]);
            assert_eq!(patch.after, vec![committed.scenes[1].nodes[0].clone()]);
            let undone = reopened
                .apply(
                    committed.id,
                    &RevisionStamp::from(&committed),
                    "undo-complete-manim-controls",
                    &Change::UndoCreativePatch { patch_id: patch.id },
                )
                .unwrap()
                .project;
            assert_eq!(undone.scenes[1].nodes, attached.scenes[1].nodes);
            assert_eq!(undone.assets, attached.assets);
            assert_eq!(reopened.project(undone.id).unwrap(), undone);
        }
    }
}

#[test]
fn compiled_manim_candidate_refuses_unrelated_noop_stale_and_locked_changes() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("candidate.sqlite3")).unwrap();
    let original = source(&service, temp.path());
    let scene_id = original.scenes[1].id;
    let deliverable_id = original.deliverables[0].id;
    let node = original.scenes[1].nodes[0].clone();
    let bytes = export_saved_manim_editable_source_bundle(
        &service,
        original.id,
        &RevisionStamp::from(&original),
        scene_id,
        deliverable_id,
    )
    .unwrap();
    let (mut saved, original_id) = attach(
        &service,
        temp.path(),
        &original,
        "candidate-original",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let expected = RevisionStamp::from(&saved);
    for change in [
        Change::UpdateCanvasText {
            scene_id,
            node_id: node.id,
            text: node.text.clone(),
        },
        Change::UpdateCanvasText {
            scene_id: original.scenes[0].id,
            node_id: node.id,
            text: Some("Not the selected scene".into()),
        },
        Change::AddScene {
            name: "Unrelated scene".into(),
            objective: "Must refuse".into(),
            duration_seconds: 1,
        },
    ] {
        assert!(
            compile_saved_manim_editable_candidate(
                &service,
                saved.id,
                &expected,
                original_id,
                scene_id,
                deliverable_id,
                &change
            )
            .is_err()
        );
    }
    let change = Change::UpdateCanvasText {
        scene_id,
        node_id: node.id,
        text: Some("New reviewed text".into()),
    };
    assert!(
        compile_saved_manim_editable_candidate(
            &service,
            saved.id,
            &expected,
            original_id,
            scene_id,
            Uuid::now_v7(),
            &change
        )
        .is_err()
    );
    assert_eq!(service.project(saved.id).unwrap(), saved);
    apply(
        &service,
        &mut saved,
        Change::SetNodePropertyLock {
            scene_id,
            node_id: node.id,
            property: NodeProperty::Text,
            locked: true,
        },
    );
    assert!(
        compile_saved_manim_editable_candidate(
            &service,
            saved.id,
            &expected,
            original_id,
            scene_id,
            deliverable_id,
            &change
        )
        .is_err()
    );
    assert!(
        compile_saved_manim_editable_candidate(
            &service,
            saved.id,
            &RevisionStamp::from(&saved),
            original_id,
            scene_id,
            deliverable_id,
            &change
        )
        .is_err()
    );
    assert_eq!(service.project(saved.id).unwrap(), saved);
}

#[test]
fn complete_manim_controls_refuse_other_objects_and_late_locked_groups_atomically() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("complete-controls.sqlite3")).unwrap();
    let original = source(&service, temp.path());
    let scene_id = original.scenes[1].id;
    let node_id = original.scenes[1].nodes[0].id;
    let output_id = original.deliverables[0].id;
    let package = prepare_saved_manim_editable_source(
        &service,
        original.id,
        &RevisionStamp::from(&original),
        scene_id,
        output_id,
    )
    .unwrap();
    let bytes = profile::source_bundle(&package.plan, &font_entries(&package)).unwrap();
    let (mut saved, original_id) = attach(
        &service,
        temp.path(),
        &original,
        "complete-original",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    for other_object in [true, false] {
        let mut changed = package.plan.clone();
        changed.nodes[0].x_px += 12.0;
        changed.nodes[0].typography.as_mut().unwrap().text = "Returned text\nSecond line".into();
        changed.nodes[0].fill_hex = "#86C8D1".into();
        if other_object {
            changed.nodes[1].x_px += 8.0;
        }
        let edited = profile::source_bundle(&changed, &font_entries(&package)).unwrap();
        let (next, edited_id) = attach(
            &service,
            temp.path(),
            &saved,
            if other_object {
                "other-object"
            } else {
                "complete-controls"
            },
            &edited,
            SOURCE_BUNDLE_MIME_V2,
        );
        saved = next;
        if !other_object {
            // Transform/text are individually writable; the final style lock
            // must reject the complete source without a partial first edit.
            apply(
                &service,
                &mut saved,
                Change::SetNodePropertyLock {
                    scene_id,
                    node_id,
                    property: NodeProperty::Style,
                    locked: true,
                },
            );
        }
        let prior = saved.clone();
        assert!(
            preview_saved_manim_editable_edit(
                &service,
                saved.id,
                &RevisionStamp::from(&saved),
                original_id,
                edited_id,
                node_id,
                EditableProperty::Controls
            )
            .is_err()
        );
        assert_eq!(service.project(saved.id).unwrap(), prior);
        assert!(
            service
                .production_receipts(saved.id, 100)
                .unwrap()
                .is_empty()
        );
    }
}

#[test]
fn complete_manim_candidate_refuses_duplicate_mixed_and_motion_patch_groups() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("closed-controls.sqlite3")).unwrap();
    let original = source(&service, temp.path());
    let scene_id = original.scenes[1].id;
    let output_id = original.deliverables[0].id;
    let node_id = original.scenes[1].nodes[0].id;
    let bytes = export_saved_manim_editable_source_bundle(
        &service,
        original.id,
        &RevisionStamp::from(&original),
        scene_id,
        output_id,
    )
    .unwrap();
    let (saved, original_id) = attach(
        &service,
        temp.path(),
        &original,
        "closed-original",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let text = ScopedCanvasEdit::Text {
        node_id,
        text: "New title".into(),
    };
    for edits in [
        vec![text.clone(), text.clone()],
        vec![
            text.clone(),
            ScopedCanvasEdit::Text {
                node_id: saved.scenes[1].nodes[1].id,
                text: "Other object".into(),
            },
        ],
        vec![
            text.clone(),
            ScopedCanvasEdit::Motion {
                node_id,
                keyframes: vec![],
            },
        ],
    ] {
        let change = Change::ApplyCreativePatch {
            patch: CreativePatch {
                scene_id,
                base_revision: saved.revision,
                rationale: EDITABLE_CONTROLS_RATIONALE.into(),
                edits,
            },
        };
        assert!(
            compile_saved_manim_editable_candidate(
                &service,
                saved.id,
                &RevisionStamp::from(&saved),
                original_id,
                scene_id,
                output_id,
                &change
            )
            .is_err()
        );
    }
    assert_eq!(service.project(saved.id).unwrap(), saved);
}

#[test]
fn complete_manim_controls_preserve_unchanged_output_text_overrides() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("variant-controls.sqlite3")).unwrap();
    let mut original = source(&service, temp.path());
    let scene_id = original.scenes[1].id;
    let node_id = original.scenes[1].nodes[0].id;
    let base_text = original.scenes[1].nodes[0].text.clone();
    let mut output = original.deliverables[0].clone();
    output
        .text_overrides
        .insert(node_id, "Localized output title".into());
    apply(
        &service,
        &mut original,
        Change::UpsertDeliverable { profile: output },
    );
    let package = prepare_saved_manim_editable_source(
        &service,
        original.id,
        &RevisionStamp::from(&original),
        scene_id,
        original.deliverables[0].id,
    )
    .unwrap();
    let bytes = profile::source_bundle(&package.plan, &font_entries(&package)).unwrap();
    let (saved, original_id) = attach(
        &service,
        temp.path(),
        &original,
        "variant-original",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let mut returned = package.plan.clone();
    returned.nodes[0].x_px += 12.0;
    returned.nodes[0].fill_hex = "#86C8D1".into();
    let bytes = profile::source_bundle(&returned, &font_entries(&package)).unwrap();
    let (saved, edited_id) = attach(
        &service,
        temp.path(),
        &saved,
        "variant-returned",
        &bytes,
        SOURCE_BUNDLE_MIME_V2,
    );
    let preview = preview_saved_manim_editable_edit(
        &service,
        saved.id,
        &RevisionStamp::from(&saved),
        original_id,
        edited_id,
        node_id,
        EditableProperty::Controls,
    )
    .unwrap();
    let Change::ApplyCreativePatch { patch } = &preview.change else {
        panic!("Expected one complete controls patch");
    };
    assert_eq!(patch.edits.len(), 2);
    assert!(
        patch
            .edits
            .iter()
            .all(|edit| !matches!(edit, ScopedCanvasEdit::Text { .. }))
    );
    let committed = service
        .apply(
            saved.id,
            &RevisionStamp::from(&saved),
            "reviewed-variant-controls",
            &preview.change,
        )
        .unwrap()
        .project;
    assert_eq!(committed.scenes[1].nodes[0].text, base_text);
    assert_eq!(committed.deliverables, saved.deliverables);
    assert_eq!(committed.assets, saved.assets);
    assert_eq!(
        plan(&committed).nodes[0].typography.as_ref().unwrap().text,
        "Localized output title"
    );
    assert_eq!(service.project(committed.id).unwrap(), committed);
}
