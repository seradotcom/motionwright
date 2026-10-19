//! Create a fresh editable 120-frame planar-camera source via canonical StudioService.
//!
//! This executable does not dispatch a renderer, authorize a job, or mark
//! video content as verified. A separate native Host and decoded-media test
//! must consume the returned saved-project identities.
use motionwright_domain::{
    BlendMode, CameraReframe, CanvasNode, Change, CoordinateSpace, NodeStyle, OutputColorSpace,
    OutputContainer, Project, RationalTime, RendererKind, RevisionStamp, VideoCodec, VisualToken,
};
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    env,
    error::Error,
    path::{Component, Path, PathBuf},
};
use uuid::Uuid;

type FixtureResult<T> = std::result::Result<T, Box<dyn Error>>;

fn apply(
    service: &StudioService,
    project: &mut Project,
    key: &str,
    change: Change,
) -> FixtureResult<()> {
    let result = service.apply(project.id, &RevisionStamp::from(&*project), key, &change)?;
    *project = result.project;
    Ok(())
}

fn shape(
    kind: &str,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    fill: &str,
    index: i32,
) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: format!("Editable camera source {kind}"),
        kind: kind.into(),
        parent_id: None,
        x,
        y,
        width,
        height,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: index,
        style: NodeStyle {
            fill: Some(fill.into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: None,
            font_size: None,
            font_weight: None,
            line_height: None,
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

fn seed(database: &Path) -> FixtureResult<Project> {
    // Reject reuse rather than re-opening an uncertain seed command.
    if !database.is_absolute()
        || database
            .components()
            .any(|part| matches!(part, Component::ParentDir))
        || database.extension().and_then(|value| value.to_str()) != Some("sqlite3")
        || !database.parent().is_some_and(Path::is_dir)
        || database.try_exists()?
    {
        return Err(
            "Camera seed requires a fresh absolute .sqlite3 path in an existing owner directory"
                .into(),
        );
    }
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Camera seed expected an empty newly allocated store".into());
    }
    let mut project = service.create_project("Editable Manim 120-frame planar camera source")?;
    apply(
        &service,
        &mut project,
        "camera120:add-scene",
        Change::AddScene {
            name: "Four-second bounded camera movement".into(),
            objective: "Realize a moving original camera over independently editable shapes".into(),
            duration_seconds: 4,
        },
    )?;
    let scene_id = project.scenes[0].id;
    apply(
        &service,
        &mut project,
        "camera120:set-renderer",
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    )?;
    for (index, source) in [
        shape("rectangle", 400.0, 400.0, 220.0, 120.0, "#86C8D1", 0),
        shape("circle", 1050.0, 430.0, 160.0, 160.0, "#F5B84A", 1),
    ]
    .into_iter()
    .enumerate()
    {
        apply(
            &service,
            &mut project,
            &format!("camera120:shape:{index}"),
            Change::AddCanvasNode {
                scene_id,
                node: source,
            },
        )?;
    }

    // Make matte a real saved authoring choice, not a Python-render fallback.
    let mut language = project.visual_language.clone();
    language.palette.push(VisualToken {
        name: "background".into(),
        value: "#000000".into(),
    });
    apply(
        &service,
        &mut project,
        "camera120:black-matte",
        Change::SetVisualLanguage {
            visual_language: language,
        },
    )?;

    let mut delivery = project.deliverables[0].clone();
    delivery.width = 640;
    delivery.height = 360;
    delivery.frame_rate = RationalTime::new(30, 1)?;
    delivery.container = OutputContainer::Mp4;
    delivery.video_codec = VideoCodec::H264;
    delivery.color_space = OutputColorSpace::Rec709;
    delivery.captions = false;
    delivery.burn_in_captions = false;
    let deliverable_id = delivery.id;
    apply(
        &service,
        &mut project,
        "camera120:deliverable",
        Change::UpsertDeliverable { profile: delivery },
    )?;

    apply(
        &service,
        &mut project,
        "camera120:authored-move",
        Change::UpsertCameraReframe {
            reframe: CameraReframe {
                id: Uuid::now_v7(),
                scene_id,
                component_version: 2,
                start_center_x: 960.0,
                start_center_y: 540.0,
                end_center_x: 1000.0,
                end_center_y: 544.0,
                duration_frames: 120,
            },
        },
    )?;
    project.validate()?;

    let identity = project.id;
    drop(service);
    let reopened = StudioService::open(database)?;
    let saved = reopened.project(identity)?;
    if saved != project || RevisionStamp::from(&saved) != RevisionStamp::from(&project) {
        return Err(
            "Fresh-process SQLite project readback diverged from saved camera source".into(),
        );
    }
    Ok(saved)
}

fn usage() -> ! {
    eprintln!("usage: native-manim-camera-seed DATABASE_ABSOLUTE_NEW.sqlite3");
    std::process::exit(2);
}

fn main() -> FixtureResult<()> {
    let mut args = env::args_os().skip(1);
    let database = PathBuf::from(args.next().unwrap_or_else(|| usage()));
    if args.next().is_some() {
        usage();
    }
    let project = seed(&database)?;
    let scene = &project.scenes[0];
    let reframe = &project.production_design.camera_reframes[0];
    let profile = project
        .deliverables
        .iter()
        .find(|row| row.width == 640 && row.height == 360)
        .ok_or("Saved 640x360 camera output is missing")?;
    let source = serde_json::to_vec(&project)?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "schema": "motionwright.native.manim-camera-seed/1",
            "project_id": project.id,
            "generation": project.generation,
            "revision": project.revision,
            "scene_id": scene.id,
            "deliverable_id": profile.id,
            "reframe_id": reframe.id,
            "source_project_sha256": hex::encode(Sha256::digest(source)),
            "frame_count": 120,
            "frame_rate_num": 30,
            "frame_rate_den": 1,
            "source_objects": scene.nodes.iter().map(|node| node.id).collect::<Vec<_>>(),
            "fresh_sqlite_readback": true,
            "native_dispatch_performed": false,
            "decoded_media_verified": false,
            "rights": "first-party synthetic shapes; no external media",
        }))?
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_camera_is_120_frames_and_reopens_without_source_replacement() {
        let scratch = tempfile::tempdir().unwrap();
        let database = scratch.path().join("camera-fresh.sqlite3");
        let saved = seed(&database).unwrap();
        let reframe = &saved.production_design.camera_reframes[0];
        assert_eq!(reframe.component_version, 2);
        assert_eq!(reframe.duration_frames, 120);
        assert_eq!(reframe.start_center_x, 960.0);
        assert_eq!(reframe.end_center_x, 1000.0);
        assert_eq!(saved.scenes[0].renderer, RendererKind::ManimCommunity);
        assert_eq!(saved.scenes[0].nodes.len(), 2);
        assert_eq!(saved.scenes[0].duration, RationalTime::new(4, 1).unwrap());
        assert_eq!(saved.scenes[0].camera.center_x, reframe.end_center_x);
        assert_eq!(saved.scenes[0].camera.center_y, reframe.end_center_y);
        assert_eq!(saved.deliverables[0].width, 640);
        assert_eq!(saved.deliverables[0].height, 360);
        assert_eq!(
            saved.deliverables[0].frame_rate,
            RationalTime::new(30, 1).unwrap()
        );
        assert_eq!(
            saved
                .visual_language
                .palette
                .iter()
                .find(|row| row.name == "background")
                .unwrap()
                .value,
            "#000000"
        );
        assert!(
            seed(&database).is_err(),
            "repeat seed must not replay original side effect"
        );
        let reopened = StudioService::open(&database)
            .unwrap()
            .project(saved.id)
            .unwrap();
        assert_eq!(reopened, saved);
    }

    #[test]
    fn existing_database_bytes_are_never_overwritten_even_if_unreadable() {
        let scratch = tempfile::tempdir().unwrap();
        let database = scratch.path().join("owned.sqlite3");
        std::fs::write(&database, b"existing unknown outcome").unwrap();
        assert!(seed(&database).is_err());
        assert_eq!(
            std::fs::read(database).unwrap(),
            b"existing unknown outcome"
        );
    }
}
