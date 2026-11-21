//! Disposable real-Store geometry source for the existing Native production runner.
//! This never renders, grants effects, or claims installed Tauri. The existing
//! source-bound Host produces Native pixels in a separate authorized command.
//! The independent reader runs only after the writer and Host have closed.
use super::*;
use motionwright_service::StudioServiceReader;
use sha2::{Digest, Sha256};

const SCHEMA: &str = "motionwright.native-geometry-source/1";
const NAME: &str = "Native geometry source-bound rectangle";
const COLOR: &str = "#25D4A2";
const FRAMES: u64 = 60;

fn geometry_node() -> CanvasNode {
    let mut node = real_linear_motion_tile();
    node.id = Uuid::now_v7();
    node.name = NAME.into();
    node.x = 740.0;
    node.y = 390.0;
    node.width = 400.0;
    node.height = 180.0;
    node.rotation_deg = 0.0;
    node.z_index = 1;
    node.style.fill = Some(COLOR.into());
    node.keyframes.clear();
    for (property, from, to) in [
        (MotionProperty::X, 700.0, node.x),
        (MotionProperty::Y, 360.0, node.y),
        (MotionProperty::Width, 240.0, node.width),
        (MotionProperty::Height, 90.0, node.height),
        (MotionProperty::RotationDeg, -18.0, node.rotation_deg),
    ] {
        node.keyframes.push(CanvasKeyframe {
            at: RationalTime::ZERO,
            property,
            value: from,
            interpolation: MotionInterpolation::Linear,
        });
        node.keyframes.push(CanvasKeyframe {
            at: RationalTime::new(1, 1).expect("frame-aligned source"),
            property,
            value: to,
            interpolation: MotionInterpolation::EaseOutCubic,
        });
    }
    node
}

fn check_project(project: &motionwright_domain::Project) -> Result<(), Box<dyn std::error::Error>> {
    let scene = project.scenes.first().ok_or("Geometry scene is missing")?;
    let profile = project
        .deliverables
        .first()
        .ok_or("Geometry deliverable is missing")?;
    if project.scenes.len() != 1
        || scene.duration != RationalTime::new(2, 1)?
        || scene.nodes.len() != 1
        || scene.nodes[0].name != NAME
        || scene.nodes[0].kind != "rectangle"
        || scene.nodes[0].style.fill.as_deref() != Some(COLOR)
        || (profile.width, profile.height) != (1920, 1080)
        || profile.frame_rate.num != 30
        || profile.frame_rate.den != 1
        || scene.nodes[0].keyframes.len() != 10
    {
        return Err("Persisted original geometry source differs from bounded fixture".into());
    }
    let options = fixture_film_options(scene.id, FRAMES)?;
    let segments = build_motion_canvas_segments(project, profile.id, &options)?;
    if segments.len() != 1 || segments[0].frame_count != FRAMES {
        return Err("Geometry Film no longer represents the 60 source frames".into());
    }
    let realized = semwright_motion_authoring::realize(&segments[0].film)?;
    let target = format!("node-{}", scene.nodes[0].id.simple());
    let instructions = realized
        .instructions
        .iter()
        .filter(|instruction| {
            matches!(&instruction.operation,
            semwright_motion_authoring::NativeOp::Tween { target: candidate, .. }
                if candidate == &target)
        })
        .collect::<Vec<_>>();
    if instructions.len() != 4
        || instructions.iter().any(|instruction| {
            instruction.start != RationalTime::ZERO
                || instruction.duration != RationalTime::new(1, 1).expect("source frame")
                || instruction.easing != semwright_motion_authoring::MotionEasing::OutCubic
        })
    {
        return Err("Geometry source did not produce exactly four Native tweens".into());
    }
    Ok(())
}

fn sha_of(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    if !path.is_absolute()
        || !path.is_file()
        || path.is_symlink()
        || fs::metadata(path)?.len() > 4 * 1024 * 1024
    {
        return Err("Native geometry receipt must be a bounded, plain absolute file".into());
    }
    Ok(format!("{:x}", Sha256::digest(fs::read(path)?)))
}

pub(super) fn seed(database: &Path, receipt: &Path) -> Result<(), Box<dyn std::error::Error>> {
    if !receipt.is_absolute()
        || !receipt.parent().is_some_and(Path::is_dir)
        || receipt.exists()
        || receipt.is_symlink()
    {
        return Err("Native geometry source receipt must be a new absolute path".into());
    }
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Native geometry fixture requires an empty Store".into());
    }
    let initial = service.create_project("Native geometry source")?;
    let scene = service
        .apply(
            initial.id,
            &RevisionStamp::from(&initial),
            "geometry-first-scene",
            &Change::AddScene {
                name: "Geometry frame samples".into(),
                objective: "Verify real Native shape keyframe geometry".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let project = service
        .apply(
            scene.id,
            &RevisionStamp::from(&scene),
            "geometry-original-shape",
            &Change::AddCanvasNode {
                scene_id: scene.scenes[0].id,
                node: geometry_node(),
            },
        )?
        .project;
    check_project(&project)?;
    let identity = json!({
        "schema": SCHEMA, "project_id": project.id,
        "generation": project.generation, "revision": project.revision,
        "scene_id": project.scenes[0].id, "node_id": project.scenes[0].nodes[0].id,
        "deliverable_id": project.deliverables[0].id,
        "frame_count": FRAMES, "fps": 30, "canvas_size": [1920, 1080],
        "fill": COLOR, "easing": "ease_out_cubic",
        "keyframes": [
            {"frame": 0, "x": 700.0,"y": 360.0,"width": 240.0,"height": 90.0,"rotation_deg": -18.0},
            {"frame": 30,"x": 740.0,"y": 390.0,"width": 400.0,"height": 180.0,"rotation_deg": 0.0}
        ],
        "native_execution": false, "formal_G2": false, "formal_G3": false
    });
    service.prepare_read_only_handoff()?;
    drop(service);
    let reader = StudioServiceReader::open(database)?;
    let observed = reader.project(project.id)?;
    if observed != project {
        return Err("Independent reader did not recover original geometry project".into());
    }
    let history = reader.project_history(project.id, 0, 16)?;
    if history.len() < 2 {
        return Err("Native geometry source journal is incomplete".into());
    }
    cycle_new_json(
        receipt,
        &json!({
            "schema": SCHEMA,
            "identity": identity,
            "project": observed,
            "history": history,
            "database_identity": reader.database_file_identity()?,
        }),
    )?;
    println!(
        "{}",
        json!({
            "geometry_seed": "SOURCE_SQLITE_ONLY",
            "source_receipt": receipt,
            "source_receipt_sha256": sha_of(receipt)?,
            "project_id": project.id, "generation": project.generation,
            "resource": project.resource_key(),
            "scene_id": project.scenes[0].id,
            "deliverable_id": project.deliverables[0].id,
            "width": project.deliverables[0].width,
            "height": project.deliverables[0].height,
            "revision": project.revision, "frame_count": FRAMES,
            "native_execution": false
        })
    );
    Ok(())
}

pub(super) fn readback(
    database: &Path,
    receipt: &Path,
    expected_sha256: &str,
    output: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    if expected_sha256.len() != 64
        || !expected_sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || sha_of(receipt)? != expected_sha256
        || !output.is_absolute()
        || !output.parent().is_some_and(Path::is_dir)
        || output.exists()
        || output.is_symlink()
    {
        return Err("Independent geometry readback input or destination is unsafe".into());
    }
    let previous: serde_json::Value = serde_json::from_slice(&fs::read(receipt)?)?;
    if previous["schema"] != SCHEMA {
        return Err("Source geometry receipt schema changed".into());
    }
    let id: Uuid = serde_json::from_value(previous["identity"]["project_id"].clone())?;
    let reader = StudioServiceReader::open(database)?;
    let saved = reader.projects(2)?;
    if saved.len() != 1
        || saved[0].id != id
        || serde_json::to_value(&saved[0])? != previous["project"]
        || serde_json::to_value(reader.project_history(id, 0, 16)?)? != previous["history"]
        || serde_json::to_value(reader.database_file_identity()?)? != previous["database_identity"]
    {
        return Err("Independent reader found different original project, journal or Store".into());
    }
    check_project(&saved[0])?;
    let report = json!({
        "schema": "motionwright.native-geometry-independent-store-readback/1",
        "source_receipt_sha256": expected_sha256,
        "reader_authority": "StudioServiceReader",
        "source_project_unchanged": true, "source_history_unchanged": true,
        "same_physical_database": true,
        "native_effect_observed_by_reader": false,
        "frame_pixels_measured_by_reader": false,
        "formal_G2": false, "formal_G3": false, "automatic_retry": false,
    });
    cycle_new_json(output, &report)?;
    println!("{report}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn original_geometry_uses_sqlite_and_independent_reader() {
        let dir = tempfile::tempdir().unwrap();
        let database = dir.path().join("project.sqlite3");
        let receipt = dir.path().join("geometry-source.json");
        seed(&database, &receipt).unwrap();
        let expected = sha_of(&receipt).unwrap();
        let read = dir.path().join("geometry-readback.json");
        readback(&database, &receipt, &expected, &read).unwrap();
        assert!(read.is_file());
        assert!(
            readback(
                &database,
                &receipt,
                &"0".repeat(64),
                &dir.path().join("refused.json")
            )
            .is_err()
        );
    }
}
