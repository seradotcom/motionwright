//! Explicit alternative native-source projection. Scene.renderer is not rewritten.
//! Native capability exposure requires the reviewed provider and consumer contract.
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
    let backgrounds = project
        .visual_language
        .palette
        .iter()
        .filter(|p| p.name == "background")
        .collect::<Vec<_>>();
    if backgrounds.len() != 1 {
        return Err(unsupported(
            "HyperFrames source needs one explicit background token; no inferred matte",
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
        background: backgrounds[0].value.clone(),
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
            if node.kind != "rect"
                || node.parent_id.is_some()
                || node.coordinate_space != CoordinateSpace::ProjectPixels
                || node.rotation_deg != 0.0
                || node.text.is_some()
                || !node.keyframes.is_empty()
                || !node.relations.is_empty()
                || !node.property_locks.is_empty()
                || node.style.blend_mode != BlendMode::Normal
                || node.style.stroke.is_some()
                || node.style.stroke_width != 0.0
                || node.style.font_family.is_some()
                || node.style.font_size.is_some()
                || node.style.font_weight.is_some()
                || node.style.line_height.is_some()
            {
                return Err(unsupported(
                    "HyperFrames source would lose a node property outside the closed rectangle profile",
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
}
