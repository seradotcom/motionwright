//! Native admission of the existing canonical SceneBoundary contracts.
//! Private scopes retain the actual selected Project and are never wire grants.
use crate::film::FilmBuildOptions;
use motionwright_domain::{Project, RendererKind, SceneBoundaryFilmOptions};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Serialize, de::DeserializeOwned};
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

const MOTION: &str = "driver.motion-canvas.";
const BLENDER: &str = "driver.blender.";
const MANIM: &str = "driver.manim-community.";
const MLT: &str = "driver.mlt-video.";
const HYPERFRAMES: &str = "driver.hyperframes.";

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
fn unsupported(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}
pub(crate) fn stale() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Project changed since native SceneBoundary admission",
    )
}
fn closed_value<T: Serialize, U: DeserializeOwned>(value: &T) -> NativeResult<U> {
    let encoded =
        serde_json::to_value(value).map_err(|_| invalid("Native Film option cannot be encoded"))?;
    serde_json::from_value(encoded)
        .map_err(|_| unsupported("Native Film option has no SceneBoundary mapping"))
}
pub(crate) fn declares_scene(project: &Project, scene_id: Uuid) -> bool {
    project
        .production_design
        .scene_boundaries
        .iter()
        .any(|boundary| boundary.scene_id == scene_id)
}
/// Uses only options actually received by the Film caller, never recorded options.
pub(crate) fn guard_motion_scenes(
    project: &Project,
    deliverable_id: Uuid,
    options: &FilmBuildOptions,
    scene_ids: &[Uuid],
) -> NativeResult<()> {
    let mut intents = BTreeMap::new();
    for intent in &options.scene_intents {
        if intents.insert(intent.scene_id, intent).is_some() {
            return Err(invalid("Duplicate scene Film intent"));
        }
    }
    for scene_id in scene_ids.iter().copied().collect::<BTreeSet<_>>() {
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| invalid("Selected Motion Canvas scene is absent"))?;
        if scene.renderer != RendererKind::MotionCanvas {
            return Err(invalid(
                "SceneBoundary selection is not a Motion Canvas scene",
            ));
        }
        if !declares_scene(project, scene_id) {
            continue;
        }
        let intent = intents
            .get(&scene_id)
            .ok_or_else(|| invalid("Selected scene has no actual native Film intent"))?;
        let actual = SceneBoundaryFilmOptions {
            frame_rate: options.frame_rate,
            font_family: options.font_family.clone(),
            mono_font_family: options.mono_font_family.clone(),
            role: closed_value(&intent.role)?,
            archetype: closed_value(&intent.archetype)?,
        };
        project
            .resolve_scene_boundary_for_execution(scene_id, deliverable_id, &actual)
            .map_err(|error| invalid(format!("Native SceneBoundary admission failed: {error}")))?;
    }
    Ok(())
}

/// These profiles have no actual Film selection in their current public API.
/// Historical absence remains supported; a declaration cannot be silently ignored.
pub(crate) fn guard_scene_without_film_options(
    project: &Project,
    scene_id: Uuid,
) -> NativeResult<()> {
    if !project.scenes.iter().any(|scene| scene.id == scene_id) {
        return Err(invalid("Selected native scene is absent"));
    }
    if declares_scene(project, scene_id) {
        return Err(unsupported(
            "This native scene profile cannot execute a declared SceneBoundary without actual Film options",
        ));
    }
    Ok(())
}

#[derive(Clone)]
pub(crate) struct ExecutionScope {
    project: Project,
    driver: &'static str,
    selected_scene_ids: Vec<Uuid>,
    mlt_selection: Option<(Uuid, FilmBuildOptions)>,
}
impl ExecutionScope {
    pub(crate) fn motion(
        project: &Project,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        scene_ids: &[Uuid],
    ) -> NativeResult<Self> {
        if scene_ids.is_empty() {
            return Err(invalid("Native SceneBoundary selection is empty"));
        }
        guard_motion_scenes(project, deliverable_id, options, scene_ids)?;
        Ok(Self {
            project: project.clone(),
            driver: MOTION,
            mlt_selection: None,
            selected_scene_ids: scene_ids
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
        })
    }

    /// Context only: receipt replay/UNKNOWN precedes admission of a new effect.
    pub(crate) fn mlt_motion(
        project: &Project,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        scene_ids: &[Uuid],
    ) -> Self {
        Self {
            project: project.clone(),
            driver: MLT,
            selected_scene_ids: scene_ids
                .iter()
                .copied()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect(),
            mlt_selection: Some((deliverable_id, options.clone())),
        }
    }

    pub(crate) fn scene_without_film_options(
        project: &Project,
        scene_id: Uuid,
        renderer: RendererKind,
    ) -> NativeResult<Self> {
        let driver = match renderer {
            RendererKind::Blender => BLENDER,
            RendererKind::ManimCommunity => MANIM,
            _ => {
                return Err(unsupported(
                    "Native SceneBoundary scope has no typed caller for this renderer",
                ));
            }
        };
        guard_scene_without_film_options(project, scene_id)?;
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| invalid("Selected native scene is absent"))?;
        if scene.renderer != renderer {
            return Err(invalid(
                "Native SceneBoundary scope renderer differs from its scene",
            ));
        }
        Ok(Self {
            project: project.clone(),
            driver,
            selected_scene_ids: vec![scene_id],
            mlt_selection: None,
        })
    }
}

/// Classifies visual consumption without treating shared audio preparation as
/// a scene grant. A typed MLT continuation checks its actual selection.
fn effect_target(
    command: &str,
    scope: Option<&ExecutionScope>,
    mlt_render_profile: Option<&str>,
) -> Option<(&'static str, Option<RendererKind>)> {
    match command {
        "driver.motion-canvas.project.create"
        | "driver.motion-canvas.asset.import"
        | "driver.motion-canvas.composition.plan"
        | "driver.motion-canvas.composition.apply"
        | "driver.motion-canvas.render.start" => Some((MOTION, Some(RendererKind::MotionCanvas))),
        "driver.blender.composition.measure"
        | "driver.blender.animation.keyframe.insert"
        | "driver.blender.scene.marker.add"
        | "driver.blender.node.add"
        | "driver.blender.node.remove"
        | "driver.blender.node.link"
        | "driver.blender.collection.create"
        | "driver.blender.object.create"
        | "driver.blender.object.transform"
        | "driver.blender.collection.link"
        | "driver.blender.material.create"
        | "driver.blender.material.assign"
        | "driver.blender.semantic.rename"
        | "driver.blender.semantic.custom.set"
        | "driver.blender.semantic.object.create"
        | "driver.blender.semantic.datablock.create"
        | "driver.blender.semantic.relation.set"
        | "driver.blender.semantic.relation.unlink"
        | "driver.blender.semantic.property.set"
        | "driver.blender.file.open"
        | "driver.blender.composition.plan"
        | "driver.blender.composition.apply"
        | "driver.blender.render.settings"
        | "driver.blender.render" => Some((BLENDER, Some(RendererKind::Blender))),
        "driver.manim-community.render.start"
        | "driver.manim-community.static.render.start"
        | "driver.manim-community.editable.render.start"
        | "driver.manim-community.camera.render.start"
        | "driver.manim-community.graph.render.start" => {
            Some((MANIM, Some(RendererKind::ManimCommunity)))
        }
        "driver.hyperframes.project.plan"
        | "driver.hyperframes.project.apply"
        | "driver.hyperframes.render.start" => Some((HYPERFRAMES, None)),
        "driver.mlt-video.project.create"
        | "driver.mlt-video.sequence.create"
        | "driver.mlt-video.track.create"
        | "driver.mlt-video.asset.import"
        | "driver.mlt-video.clip.insert"
            if scope.is_some_and(|scope| scope.driver == MLT) =>
        {
            Some((MLT, None))
        }
        // This is the existing SDK output profile, not a new authorization flag.
        "driver.mlt-video.render.plan" | "driver.mlt-video.render.start"
            if mlt_render_profile != Some("audio-wav")
                || scope.is_some_and(|scope| scope.driver == MLT) =>
        {
            Some((MLT, None))
        }
        "driver.mlt-video.frames.encode" | "driver.mlt-video.av.mux" => Some((MLT, None)),
        _ => None,
    }
}
pub(crate) fn is_new_scene_effect(
    command: &str,
    scope: Option<&ExecutionScope>,
    mlt_render_profile: Option<&str>,
) -> bool {
    effect_target(command, scope, mlt_render_profile).is_some()
}

/// Call after the complete existing receipt branch. Queries, verification,
/// cancellation, close, historical source save/export and audio-only commands
/// retain their current behavior.
pub(crate) fn guard_new_effect(
    project: &Project,
    command: &str,
    scope: Option<&ExecutionScope>,
    mlt_render_profile: Option<&str>,
) -> NativeResult<()> {
    let Some((driver, renderer)) = effect_target(command, scope, mlt_render_profile) else {
        return Ok(());
    };
    if let Some(scope) = scope.filter(|scope| scope.driver == driver) {
        if scope.project != *project || scope.selected_scene_ids.is_empty() {
            return Err(stale());
        }
        if driver == MLT {
            let (deliverable_id, options) = scope
                .mlt_selection
                .as_ref()
                .ok_or_else(|| unsupported("MLT visual scope has no actual Film selection"))?;
            return guard_motion_scenes(
                project,
                *deliverable_id,
                options,
                &scope.selected_scene_ids,
            );
        }
        return Ok(());
    }
    let relevant = project
        .production_design
        .scene_boundaries
        .iter()
        .any(|boundary| {
            renderer.as_ref().is_none_or(|renderer| {
                project
                    .scenes
                    .iter()
                    .any(|scene| scene.id == boundary.scene_id && &scene.renderer == renderer)
            })
        });
    if relevant {
        return Err(unsupported(
            "This native scene effect requires a typed SceneBoundary selection",
        ));
    }
    Ok(())
}
