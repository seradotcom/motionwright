//! Explicit alternative native-source projection. Scene.renderer is not rewritten.
//! Native capability exposure requires the reviewed provider and consumer contract.
#[cfg(test)]
use crate::semantic_graph_source_fixture as graph_source_guard_fixture;

use motionwright_domain::{
    AudioState, BlendMode, CameraState, CoordinateSpace, ProductionDesign, Project, RationalTime,
};
use motionwright_hyperframes_profile::{self as model, Clip, Composition, SourceBinding};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use uuid::Uuid;
fn unsupported(message: &str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}
fn frames(time: RationalTime) -> NativeResult<u32> {
    if time.num < 0 || time.den <= 0 {
        return Err(unsupported(
            "HyperFrames clock must be nonnegative and exact",
        ));
    }
    let scaled = time
        .num
        .checked_mul(30)
        .ok_or_else(|| unsupported("HyperFrames clock overflow"))?;
    if scaled % time.den != 0 {
        return Err(unsupported(
            "HyperFrames clock cannot be quantized silently",
        ));
    }
    let frames = u32::try_from(scaled / time.den)
        .map_err(|_| unsupported("HyperFrames clock exceeds bounds"))?;
    if frames % 30 != 0 {
        return Err(unsupported(
            "Initial HyperFrames clip profile requires whole seconds",
        ));
    }
    Ok(frames)
}
fn pixel(value: f64, scale: f64) -> NativeResult<u32> {
    let scaled = value * scale;
    if !scaled.is_finite() || scaled < 0.0 || scaled > u32::MAX as f64 || scaled.fract() != 0.0 {
        return Err(unsupported(
            "HyperFrames projection would round an authored pixel coordinate",
        ));
    }
    Ok(scaled as u32)
}
pub fn build_hyperframes_source(
    project: &Project,
    deliverable_id: Uuid,
) -> NativeResult<Composition> {
    project
        .validate()
        .map_err(|_| unsupported("HyperFrames requires a valid saved Project"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|d| d.id == deliverable_id)
        .ok_or_else(|| Error::new(ErrorCode::NotFound, "HyperFrames source profile not found"))?;
    if profile.width as u64 * 1080 != profile.height as u64 * 1920
        || profile.captions
        || profile.burn_in_captions
        || profile.voice_track_id.is_some()
        || !profile.text_overrides.is_empty()
        || !profile.included_scene_ids.is_empty()
        || profile.frame_rate.num != 30
        || profile.frame_rate.den != 1
        || profile.framing_strategy != Default::default()
        || profile.color_space != Default::default()
    {
        return Err(unsupported(
            "HyperFrames source profile supports original 16:9 framing, 30 fps and no captions, voice or editorial overrides",
        ));
    }
    if project.scenes.is_empty()
        || project.scenes.len() > 8
        || !project.assets.is_empty()
        || !project.locks.is_empty()
        || project.audio != AudioState::default()
        || project.production_design != ProductionDesign::default()
    {
        return Err(unsupported(
            "Initial HyperFrames profile is a first-party rectangular clip source without assets, audio, locks or production extensions",
        ));
    }
    // Preserve the explicit background contract of historical rect sources.
    // A new Studio project authors its matte as "surface"; admit that exact
    // token if no explicit background exists. Do not invent a default color.
    let palette = &project.visual_language.palette;
    let backgrounds = palette
        .iter()
        .filter(|p| p.name.eq_ignore_ascii_case("background"))
        .collect::<Vec<_>>();
    let surfaces = palette
        .iter()
        .filter(|p| p.name.eq_ignore_ascii_case("surface"))
        .collect::<Vec<_>>();
    let matte = match (backgrounds.as_slice(), surfaces.as_slice()) {
        ([background], _) => *background,
        ([], [surface]) => *surface,
        _ => {
            return Err(unsupported(
                "HyperFrames source needs one unambiguous authored background or surface token; no inferred matte",
            ));
        }
    };
    // Studio's editorial stage uses the first saved background/surface token.
    // New Studio drawable sources must not silently export another matte.
    // Historical rect-only sources keep their existing source fingerprint.
    if project
        .scenes
        .iter()
        .flat_map(|scene| &scene.nodes)
        .any(|node| matches!(node.kind.as_str(), "shape" | "rectangle"))
        && palette
            .iter()
            .find(|token| {
                token.name.eq_ignore_ascii_case("background")
                    || token.name.eq_ignore_ascii_case("surface")
            })
            .is_none_or(|token| !token.value.eq_ignore_ascii_case(&matte.value))
    {
        return Err(unsupported(
            "HyperFrames matte differs from the Studio composition; reconcile the authored background and surface tokens before export",
        ));
    }
    let mut source = Composition {
        schema: model::SCHEMA.into(),
        binding: SourceBinding {
            project_resource: project.resource_key(),
            generation: project.generation.to_string(),
            revision: project.revision,
            deliverable_id: deliverable_id.to_string(),
        },
        width: profile.width,
        height: profile.height,
        fps: 30,
        frame_count: 0,
        background: matte.value.clone(),
        clips: vec![],
    };
    let scale = profile.width as f64 / 1920.0;
    let mut cursor = 0u32;
    for scene in &project.scenes {
        let start = frames(scene.start)?;
        let duration = frames(scene.duration)?;
        if start != cursor
            || duration == 0
            || scene.camera != CameraState::default()
            || !scene.beats.is_empty()
        {
            return Err(unsupported(
                "HyperFrames cannot drop scene gaps, camera state or beat semantics",
            ));
        }
        let end = start
            .checked_add(duration)
            .ok_or_else(|| unsupported("HyperFrames duration overflow"))?;
        if scene.nodes.is_empty() {
            return Err(unsupported("HyperFrames clip scene has no authored shape"));
        }
        for node in &scene.nodes {
            // Studio authors shape and rectangle as the same planar rectangle
            // primitive. Keep the legacy rect source vocabulary and every ID.
            // All nonrepresentable properties remain explicit pre-effect errors.
            let property = if !matches!(node.kind.as_str(), "rect" | "shape" | "rectangle") {
                Some("drawable kind (use a rectangle)")
            } else if node.parent_id.is_some() {
                Some("group or clipping parent")
            } else if node.coordinate_space != CoordinateSpace::ProjectPixels {
                Some("coordinate space (requires project pixels)")
            } else if node.rotation_deg != 0.0 {
                Some("rotation")
            } else if node.text.is_some() {
                Some("text")
            } else if !node.keyframes.is_empty() {
                Some("motion keyframes")
            } else if !node.relations.is_empty() {
                Some("semantic relations")
            } else if !node.property_locks.is_empty() {
                Some("property locks")
            } else if node.style.blend_mode != BlendMode::Normal {
                Some("blend mode")
            } else if node.style.stroke.is_some() || node.style.stroke_width != 0.0 {
                Some("stroke (use no stroke and width zero)")
            } else if node.style.font_family.is_some()
                || node.style.font_size.is_some()
                || node.style.font_weight.is_some()
                || node.style.line_height.is_some()
            {
                Some("typography")
            } else {
                None
            };
            if let Some(property) = property {
                return Err(Error::new(
                    ErrorCode::Unsupported,
                    format!(
                        "HyperFrames node {} would lose {property} outside the closed rectangle profile",
                        node.id
                    ),
                ));
            }
            let opacity = node.opacity * 1000.0;
            if !opacity.is_finite() || !(0.0..=1000.0).contains(&opacity) || opacity.fract() != 0.0
            {
                return Err(unsupported(
                    "HyperFrames opacity cannot be rounded silently",
                ));
            }
            source.clips.push(Clip {
                id: format!("node-{}", node.id.simple()),
                start_frame: start,
                end_frame_exclusive: end,
                x: pixel(node.x, scale)?,
                y: pixel(node.y, scale)?,
                width: pixel(node.width, scale)?,
                height: pixel(node.height, scale)?,
                fill: node
                    .style
                    .fill
                    .clone()
                    .ok_or_else(|| unsupported("HyperFrames rectangle needs explicit fill"))?,
                opacity_milli: opacity as u16,
                z_index: node.z_index,
            });
        }
        cursor = end;
    }
    source.frame_count = cursor;
    source.validate()?;
    Ok(source)
}
/// Compile inert native source from one exact saved Project and delivery profile.
/// This never installs dependencies, changes the Project, or dispatches a provider.
pub fn compile_hyperframes_source(
    project: &Project,
    deliverable_id: Uuid,
) -> NativeResult<model::CompiledSource> {
    model::compile(&build_hyperframes_source(project, deliverable_id)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{CanvasNode, Change, NodeStyle, VisualToken};
    use motionwright_service::StudioService;
    use std::collections::BTreeSet;
    fn fixture() -> (tempfile::TempDir, StudioService, Project, Uuid) {
        let dir = tempfile::tempdir().unwrap();
        let service = StudioService::open(dir.path().join("hf.sqlite3")).unwrap();
        let mut p = service
            .create_project("First-party HyperFrames source")
            .unwrap();
        let mut language = p.visual_language.clone();
        language.palette.retain(|t| t.name != "background");
        language.palette.push(VisualToken {
            name: "background".into(),
            value: "#101820".into(),
        });
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "hf:language",
                &Change::SetVisualLanguage {
                    visual_language: language,
                },
            )
            .unwrap()
            .project;
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "hf:scene",
                &Change::AddScene {
                    name: "Original card".into(),
                    objective: "First-party rectangle".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap()
            .project;
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: "Editable red rectangle".into(),
            kind: "rect".into(),
            parent_id: None,
            x: 120.0,
            y: 120.0,
            width: 720.0,
            height: 360.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle {
                fill: Some("#E53935".into()),
                ..Default::default()
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "hf:node",
                &Change::AddCanvasNode {
                    scene_id: p.scenes[0].id,
                    node,
                },
            )
            .unwrap()
            .project;
        let mut profile = p.deliverables[0].clone();
        profile.width = 640;
        profile.height = 360;
        profile.captions = false;
        profile.burn_in_captions = false;
        profile.frame_rate = RationalTime::new(30, 1).unwrap();
        let id = profile.id;
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "hf:profile",
                &Change::UpsertDeliverable { profile },
            )
            .unwrap()
            .project;
        (dir, service, p, id)
    }
    #[test]
    fn typed_hyperframes_source_keeps_canvas_identity_and_does_not_dispatch() {
        let (_dir, service, p, id) = fixture();
        let before = p.clone();
        let source = build_hyperframes_source(&p, id).unwrap();
        assert_eq!(source.width, 640);
        assert_eq!(source.clips[0].x, 40);
        assert_eq!(source.frame_count, 30);
        assert_eq!(source.binding.project_resource, p.resource_key());
        assert_eq!(p, before);
        assert!(service.production_receipts(p.id, 20).unwrap().is_empty());
    }
    #[test]
    fn unsupported_blend_camera_and_subpixel_source_refuse_before_native_dispatch() {
        let (_dir, service, p, id) = fixture();
        let mut wrong = p.clone();
        wrong.scenes[0].nodes[0].style.blend_mode = BlendMode::Multiply;
        assert!(build_hyperframes_source(&wrong, id).is_err());
        let mut wrong = p.clone();
        wrong.scenes[0].camera.zoom = 2.0;
        assert!(build_hyperframes_source(&wrong, id).is_err());
        let mut wrong = p.clone();
        wrong.scenes[0].nodes[0].x = 120.25;
        assert!(build_hyperframes_source(&wrong, id).is_err());
        assert!(service.production_receipts(p.id, 20).unwrap().is_empty());
    }

    #[test]
    fn studio_rectangles_use_saved_matte_and_reopen_exact_compiled_source() {
        use motionwright_domain::CanvasTransform;
        let (dir, service, legacy, _) = fixture();
        let mut p = service
            .create_project("Editable Studio rectangle production")
            .unwrap();
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "studio-hf:scene",
                &Change::AddScene {
                    name: "Opening".into(),
                    objective: "Author two editable rectangles".into(),
                    duration_seconds: 6,
                },
            )
            .unwrap()
            .project;
        let scene_id = p.scenes[0].id;
        let mut rectangle = legacy.scenes[0].nodes[0].clone();
        rectangle.id = Uuid::now_v7();
        rectangle.kind = "rectangle".into();
        rectangle.name = "Rectangle 1".into();
        rectangle.x = 276.0;
        rectangle.y = 252.0;
        rectangle.width = 360.0;
        rectangle.height = 216.0;
        rectangle.style.fill = Some("#86C8D1".into());
        rectangle.z_index = 1;
        let mut shape = rectangle.clone();
        shape.id = Uuid::now_v7();
        shape.kind = "shape".into();
        shape.name = "Shape 1".into();
        shape.x += 36.0;
        shape.y += 36.0;
        shape.z_index = 2;
        for (index, node) in [rectangle.clone(), shape.clone()].into_iter().enumerate() {
            p = service
                .apply(
                    p.id,
                    &p.stamp(),
                    &format!("studio-hf:node:{index}"),
                    &Change::AddCanvasNode { scene_id, node },
                )
                .unwrap()
                .project;
        }
        let mut profile = p.deliverables[0].clone();
        profile.captions = false;
        let profile_id = profile.id;
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "studio-hf:profile",
                &Change::UpsertDeliverable { profile },
            )
            .unwrap()
            .project;
        let first = build_hyperframes_source(&p, profile_id).unwrap();
        assert_eq!(first.background, "#0F1216");
        assert_eq!(first.frame_count, 180);
        assert_eq!(first.clips.len(), 2);
        assert_eq!(first.clips[0].id, format!("node-{}", rectangle.id.simple()));
        assert_eq!(first.clips[1].id, format!("node-{}", shape.id.simple()));
        assert_eq!(
            (
                first.clips[0].x,
                first.clips[0].y,
                first.clips[0].width,
                first.clips[0].height
            ),
            (276, 252, 360, 216)
        );
        let transform = CanvasTransform {
            x: 480.0,
            y: 360.0,
            width: 420.0,
            height: 240.0,
            rotation_deg: 0.0,
            opacity: 0.625,
        };
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "studio-hf:transform",
                &Change::TransformCanvasNode {
                    scene_id,
                    node_id: rectangle.id,
                    transform,
                },
            )
            .unwrap()
            .project;
        let mut style = rectangle.style.clone();
        style.fill = Some("#E0A940".into());
        p = service
            .apply(
                p.id,
                &p.stamp(),
                "studio-hf:style",
                &Change::UpdateCanvasStyle {
                    scene_id,
                    node_id: rectangle.id,
                    style,
                },
            )
            .unwrap()
            .project;
        let source = build_hyperframes_source(&p, profile_id).unwrap();
        let clip = &source.clips[0];
        assert_eq!(clip.id, first.clips[0].id);
        assert_eq!(
            (clip.x, clip.y, clip.width, clip.height, clip.opacity_milli),
            (480, 360, 420, 240, 625)
        );
        assert_eq!(clip.fill, "#E0A940");
        assert_eq!(source.clips[1], first.clips[1]);
        let compiled = model::compile(&source).unwrap();
        assert_ne!(compiled.source, model::compile(&first).unwrap().source);
        assert!(service.production_receipts(p.id, 20).unwrap().is_empty());
        drop(service);
        let reopened = StudioService::open(dir.path().join("hf.sqlite3")).unwrap();
        let readback = reopened.project(p.id).unwrap();
        assert_eq!(p, readback);
        let reconstructed =
            model::compile(&build_hyperframes_source(&readback, profile_id).unwrap()).unwrap();
        assert_eq!(reconstructed.source, compiled.source);
        assert_eq!(reconstructed.html, compiled.html);
        assert_eq!(reconstructed.fingerprint, compiled.fingerprint);
        assert!(reopened.production_receipts(p.id, 20).unwrap().is_empty());
    }

    #[test]
    fn modern_rectangles_fail_closed_on_matte_conflict_or_unsupported_property() {
        let (_dir, service, legacy, id) = fixture();
        // Preserve the preexisting explicitly authored legacy background.
        assert_eq!(
            build_hyperframes_source(&legacy, id).unwrap().background,
            "#101820"
        );
        let mut modern = legacy.clone();
        modern.scenes[0].nodes[0].kind = "rectangle".into();
        let error = build_hyperframes_source(&modern, id).unwrap_err();
        assert!(error.message.contains("matte differs"));
        modern
            .visual_language
            .palette
            .retain(|token| token.name != "background");
        assert_eq!(
            build_hyperframes_source(&modern, id).unwrap().background,
            "#0F1216"
        );
        for property in [
            "stroke",
            "rotation",
            "drawable kind",
            "blend mode",
            "motion keyframes",
        ] {
            let mut invalid = modern.clone();
            let node = &mut invalid.scenes[0].nodes[0];
            match property {
                "stroke" => {
                    node.style.stroke = Some("#000000".into());
                    node.style.stroke_width = 1.0;
                }
                "rotation" => node.rotation_deg = 10.0,
                "drawable kind" => node.kind = "circle".into(),
                "blend mode" => node.style.blend_mode = BlendMode::Multiply,
                "motion keyframes" => node.keyframes.push(motionwright_domain::CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property: motionwright_domain::MotionProperty::X,
                    value: node.x,
                    interpolation: motionwright_domain::MotionInterpolation::Linear,
                }),
                _ => unreachable!(),
            }
            let expected = node.id.to_string();
            let error = build_hyperframes_source(&invalid, id).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
            assert!(error.message.contains(property), "{:?}", error);
            assert!(error.message.contains(&expected));
        }
        assert_eq!(service.project(legacy.id).unwrap(), legacy);
        assert!(
            service
                .production_receipts(legacy.id, 20)
                .unwrap()
                .is_empty()
        );
    }

    fn attach_source_guard_graph(project: &mut Project, scene_index: usize, profile_index: usize) {
        let mut selected = project.clone();
        selected.scenes.swap(0, scene_index);
        selected.deliverables.swap(0, profile_index);
        let mut graph = graph_source_guard_fixture::graph_for(&selected, false);
        graph.time_range.start = motionwright_domain::RationalTime::new(0, 1).unwrap();
        graph.time_range.duration = motionwright_domain::RationalTime::new(1, 1).unwrap();
        let change = motionwright_domain::Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        };
        if selected.scenes[0].renderer == motionwright_domain::RendererKind::ManimCommunity {
            project.apply_change(&change).unwrap();
        } else {
            // These consumer guards must also inspect historical snapshots.
            // New Graph writes remain restricted to Manim Community.
            let before = project.clone();
            let error = project.apply_change(&change).unwrap_err();
            assert!(matches!(
                error,
                motionwright_domain::DomainError::Invalid(message)
                    if message == "Semantic Graph source requires the declared Manim Community scene"
            ));
            assert_eq!(*project, before);
            project.production_design.semantic_graphs.push(graph);
        }
        project.validate().unwrap();
    }

    #[test]
    fn hyperframes_reject_semantic_graph_production_design() {
        let (_directory, service, mut project, profile_id) = fixture();
        build_hyperframes_source(&project, profile_id).unwrap();
        let canonical_before = service.project(project.id).unwrap();
        attach_source_guard_graph(&mut project, 0, 0);
        let before = project.clone();
        assert_eq!(
            build_hyperframes_source(&project, profile_id)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
        assert_eq!(project, before);
        assert_eq!(service.project(project.id).unwrap(), canonical_before);
        assert!(
            service
                .production_receipts(project.id, 20)
                .unwrap()
                .is_empty()
        );
    }
}
