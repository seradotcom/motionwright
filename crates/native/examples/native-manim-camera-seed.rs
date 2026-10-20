//! Create a fresh editable 120-frame planar-camera source via canonical StudioService.
//!
//! This executable does not dispatch a renderer, authorize a job, or mark
//! video content as verified. A separate native Host and decoded-media test
//! must consume the returned saved-project identities.
use motionwright_domain::{
    BlendMode, CameraReframe, CanvasNode, Change, CoordinateSpace, NodeStyle, OutputColorSpace,
    OutputContainer, Project, RationalTime, RendererKind, RevisionStamp, VideoCodec, VisualToken,
};
use motionwright_native::manim_camera_projection::{
    PreparedManimCameraProgramV1, prepare_saved_manim_camera_program_source_only,
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
            objective: "Realize a moving original camera over one editable source shape".into(),
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
    // The compiled native getter has a 256 KiB inline envelope:
    // 120 frames + one object = 247,808 estimated bytes. Adding a second
    // object makes it 370,688 bytes and must not be silently accepted.
    apply(
        &service,
        &mut project,
        "camera120:single-editable-source-shape",
        Change::AddCanvasNode {
            scene_id,
            node: shape("rectangle", 400.0, 400.0, 220.0, 120.0, "#86C8D1", 0),
        },
    )?;

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

/// Export the same compiler bytes the existing coordinator and driver consume.
/// Reopen the saved SQLite source; an imported plan or JSON label cannot replace it.
fn prepare_program(
    database: &Path,
    project: &Project,
) -> FixtureResult<PreparedManimCameraProgramV1> {
    let service = StudioService::open(database)?;
    let scene = project
        .scenes
        .first()
        .ok_or("Saved camera scene is absent")?;
    let profile = project
        .deliverables
        .iter()
        .find(|row| row.width == 640 && row.height == 360)
        .ok_or("Saved 640x360 camera output is missing")?;
    let compiled = prepare_saved_manim_camera_program_source_only(
        &service,
        project.id,
        &RevisionStamp::from(project),
        scene.id,
        profile.id,
    )?;
    if service.project(project.id)? != *project {
        return Err("Saved camera Project changed before compiler evidence export".into());
    }
    Ok(compiled)
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
    let prepared_program = prepare_program(&database, &project)?;
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
            "prepared_program": prepared_program,
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
        assert_eq!(saved.scenes[0].nodes.len(), 1);
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
    fn exported_compiler_bytes_bind_the_saved_scene_without_starting_a_job() {
        let scratch = tempfile::tempdir().unwrap();
        let database = scratch.path().join("camera-program.sqlite3");
        let saved = seed(&database).unwrap();
        let program = prepare_program(&database, &saved).unwrap();
        let source = &program.prepared.source;
        assert_eq!(source.binding.project_id, saved.id);
        assert_eq!(source.binding.generation, saved.generation);
        assert_eq!(source.binding.revision, saved.revision);
        assert_eq!(source.binding.scene_id, saved.scenes[0].id);
        assert_eq!(source.binding.deliverable_id, saved.deliverables[0].id);
        let mut original_plan = serde_json::to_vec_pretty(source).unwrap();
        original_plan.push(b'\n');
        assert_eq!(program.trusted_compiler.plan_document, original_plan);
        assert_eq!(
            program.trusted_compiler.plan_sha256,
            hex::encode(Sha256::digest(&original_plan))
        );
        assert_eq!(
            program.trusted_compiler.source_sha256,
            hex::encode(Sha256::digest(
                program.trusted_compiler.python_source.as_bytes()
            ))
        );
        assert!(!program.native_execution_performed);
        assert!(!program.native_permissions_verified);
        assert!(!program.trusted_compiler.runtime_sample_pts_verified);
        let service = StudioService::open(&database).unwrap();
        assert_eq!(service.project(saved.id).unwrap(), saved);
        assert!(
            service
                .production_receipts(saved.id, 256)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn compiler_export_rejects_a_foreign_scene_or_stale_revision() {
        let scratch = tempfile::tempdir().unwrap();
        let database = scratch.path().join("camera-identity.sqlite3");
        let saved = seed(&database).unwrap();
        let mut foreign = saved.clone();
        foreign.scenes[0].id = Uuid::now_v7();
        assert!(prepare_program(&database, &foreign).is_err());
        let mut stale = saved.clone();
        stale.revision += 1;
        assert!(prepare_program(&database, &stale).is_err());
        let service = StudioService::open(&database).unwrap();
        assert_eq!(service.project(saved.id).unwrap(), saved);
        assert!(
            service
                .production_receipts(saved.id, 256)
                .unwrap()
                .is_empty()
        );
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
