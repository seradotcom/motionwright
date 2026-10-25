//! Source/SQLite tests only. Native rendering, glyph pixels and reopening in a
//! new Manim process remain separate original-Host G2 observations.
use motionwright_domain::{
    BlendMode, CanvasKeyframe, CanvasNode, Change, CoordinateSpace, FontRight, MotionInterpolation,
    MotionProperty, NodeProperty, NodeStyle, Project, RationalTime, RendererKind, RevisionStamp,
    RightsStatus, VisualToken,
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
