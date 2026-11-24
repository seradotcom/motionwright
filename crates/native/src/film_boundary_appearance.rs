//! Source-only continuity at the mathematical cut between compatible shots.
//! The persisted appearance map is the identity link. Native subject IDs remain
//! shot-local; this is neither cross-shot morphing nor pixel/velocity evidence.
use super::*;
use motionwright_domain::{
    SceneBoundary, SceneBoundaryDefinition, SceneBoundaryEndpoint, SceneBoundaryInput,
};

fn loss(message: &str) -> Error {
    unsupported(format!(
        "SceneBoundary appearance continuity loss: {message}"
    ))
}

struct CutPose {
    center: Point,
    width: f64,
    height: f64,
    rotation: f64,
    opacity: f64,
}

fn focal_node<'a>(
    scene: &'a Scene,
    endpoint: &SceneBoundaryEndpoint,
    id: Uuid,
) -> NativeResult<&'a CanvasNode> {
    let node = scene
        .nodes
        .iter()
        .find(|node| node.id == id)
        .ok_or_else(|| loss("mapped object is absent from the saved scene"))?;
    // The endpoint stores a snapshot of the authored source. Do not use an
    // unrelated edited snapshot to pretend the actual native scene moved.
    if endpoint.nodes.iter().find(|value| value.id == id) != Some(node)
        || endpoint.camera.as_ref() != Some(&scene.camera)
    {
        return Err(loss(
            "mapped snapshot differs from the saved object or camera; copy the current source",
        ));
    }
    authored_beats_tile_scene(scene)?;
    if scene.renderer != RendererKind::MotionCanvas
        || !matches!(node.kind.as_str(), "shape" | "rectangle" | "circle")
        || node.parent_id.is_some()
        || !node.relations.is_empty()
        || scene
            .nodes
            .iter()
            .any(|candidate| candidate.parent_id == Some(id))
    {
        return Err(loss(
            "only independent Motion Canvas shapes with exact scene clocks have a bounded continuity projection",
        ));
    }
    Ok(node)
}

fn cut_pose(
    project: &Project,
    scene: &Scene,
    node: &CanvasNode,
    profile: &DeliverableProfile,
    rate: Rate,
    entering: bool,
) -> NativeResult<CutPose> {
    // Responsive Hero substitutions must not change the mapped source
    // object behind this proof. Other mapped object types remain unsupported.
    let responsive = responsive_scene(project, scene, profile)?;
    if responsive
        .nodes
        .iter()
        .find(|candidate| candidate.id == node.id)
        != Some(node)
    {
        return Err(loss("responsive delivery changed the mapped object"));
    }
    validate_node_projection(node, MOTION_CANVAS_FONT_FAMILY)?;
    let (output, projection) = output_profile(profile, rate, &[scene])?;
    let mut geometry = node.clone();
    geometry
        .keyframes
        .retain(|key| key.property != MotionProperty::Opacity);
    let mut alpha = node.clone();
    alpha
        .keyframes
        .retain(|key| key.property == MotionProperty::Opacity);
    let mut initial = node.clone();
    if let Some(points) = native_pose_waypoints(&geometry, scene, rate)? {
        initial = points
            .first()
            .ok_or_else(|| loss("empty pose track"))?
            .pose
            .clone();
    } else if let Some(motion) = native_linear_geometry_motion(&geometry, scene, rate)? {
        initial = motion.start;
    } else if let Some(motion) = native_linear_position_motion(&geometry, scene, rate)? {
        initial.x = motion.start_x;
        initial.y = motion.start_y;
    }
    let opacity = if alpha.keyframes.is_empty() {
        node.opacity
    } else {
        // Validate every authored span, including a constant envelope with no
        // native fades. The cut uses the actual first/last saved alpha, not
        // the direction of an arbitrarily selected interval.
        native_exact_opacity_fade(&alpha, scene, rate)?
            .ok_or_else(|| loss("opacity has no exact boundary projection"))?;
        alpha.keyframes.sort_by(|a, b| {
            (i128::from(a.at.num) * i128::from(b.at.den))
                .cmp(&(i128::from(b.at.num) * i128::from(a.at.den)))
        });
        let endpoint = if entering {
            alpha.keyframes.first()
        } else {
            alpha.keyframes.last()
        };
        endpoint
            .ok_or_else(|| loss("opacity envelope is empty"))?
            .value
    };
    let pose = if entering { &initial } else { node };
    let terminal_projection = scene_layout_projection(scene, projection);
    let center = projected_geometry_centers(node, pose, terminal_projection, &output).0;
    let mut center = center;
    if let Some(reframe) = project
        .production_design
        .camera_reframes
        .iter()
        .find(|value| value.scene_id == scene.id)
    {
        reframe
            .validate(scene)
            .map_err(|error| contract("SceneBoundary camera", error))?;
        if reframe.component_version != motionwright_domain::CAMERA_REFRAME_VERSION
            || rate.num != 30
            || rate.den != 1
        {
            return Err(loss("camera has no exact Motion Canvas cut projection"));
        }
        if entering {
            center.x +=
                (reframe.end_center_x - reframe.start_center_x) * projection.position_scale_x;
            center.y +=
                (reframe.end_center_y - reframe.start_center_y) * projection.position_scale_y;
        }
    }
    Ok(CutPose {
        center,
        width: pose.width * projection.object_scale,
        height: pose.height * projection.object_scale,
        rotation: pose.rotation_deg,
        opacity,
    })
}

pub(crate) fn validate_pair(
    project: &Project,
    profile: &DeliverableProfile,
    rate: Rate,
    predecessor: &SceneBoundary,
    successor: &SceneBoundary,
) -> NativeResult<()> {
    let SceneBoundaryInput::Predecessor { appearances, .. } = &successor.input else {
        return Ok(());
    };
    if appearances.is_empty() {
        return Ok(());
    }
    let SceneBoundaryDefinition::Explicit { exit, .. } = &predecessor.definition else {
        return Err(loss("predecessor is incomplete"));
    };
    let SceneBoundaryDefinition::Explicit { entry, .. } = &successor.definition else {
        return Err(loss("successor is incomplete"));
    };
    let selection = if profile.included_scene_ids.is_empty() {
        project
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>()
    } else {
        profile.included_scene_ids.clone()
    };
    if !selection
        .windows(2)
        .any(|pair| pair == [predecessor.scene_id, successor.scene_id])
    {
        return Err(loss(
            "mapped shots are no longer adjacent in the selected deliverable",
        ));
    }
    let previous = project
        .scenes
        .iter()
        .find(|scene| scene.id == predecessor.scene_id)
        .ok_or_else(|| loss("predecessor scene is missing"))?;
    let next = project
        .scenes
        .iter()
        .find(|scene| scene.id == successor.scene_id)
        .ok_or_else(|| loss("successor scene is missing"))?;
    for appearance in appearances {
        let from = focal_node(previous, exit, appearance.predecessor_node_id)?;
        let to = focal_node(next, entry, appearance.successor_node_id)?;
        let rectangle = |kind: &str| matches!(kind, "shape" | "rectangle");
        if !(from.kind == to.kind || (rectangle(&from.kind) && rectangle(&to.kind)))
            || from.style != to.style
            || from.text != to.text
        {
            return Err(loss("mapped shape appearance changed"));
        }
        let a = cut_pose(project, previous, from, profile, rate, false)?;
        let b = cut_pose(project, next, to, profile, rate, true)?;
        // Only numerical representation tolerance, not a pixel acceptance
        // threshold or inferred motion. This verifies C0 at the authored cut.
        let equal = |a: f64, b: f64| a.is_finite() && b.is_finite() && (a - b).abs() <= 1e-6;
        if a.opacity != 1.0
            || b.opacity != 1.0
            || !equal(a.center.x, b.center.x)
            || !equal(a.center.y, b.center.y)
            || !equal(a.width, b.width)
            || !equal(a.height, b.height)
            || !equal(a.rotation, b.rotation)
        {
            return Err(loss(
                "mapped shape jumps, changes size/rotation, or is invisible at the cut; edit the original Canvas motion",
            ));
        }
    }
    Ok(())
}
