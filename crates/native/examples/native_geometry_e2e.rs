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

fn check_project(
    project: &motionwright_domain::Project,
    easing: MotionInterpolation,
) -> Result<(), Box<dyn std::error::Error>> {
    if !matches!(
        easing,
        MotionInterpolation::EaseOutCubic | MotionInterpolation::Hold
    ) {
        return Err("Geometry source easing is outside the closed selection".into());
    }
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
    let mut expected = geometry_node();
    for key in expected
        .keyframes
        .iter_mut()
        .filter(|key| key.at > RationalTime::ZERO)
    {
        key.interpolation = easing;
    }
    if scene.nodes[0].x != expected.x
        || scene.nodes[0].y != expected.y
        || scene.nodes[0].width != expected.width
        || scene.nodes[0].height != expected.height
        || scene.nodes[0].rotation_deg != expected.rotation_deg
        || expected.keyframes.iter().any(|key| {
            scene.nodes[0]
                .keyframes
                .iter()
                .filter(|actual| *actual == key)
                .count()
                != 1
        })
    {
        return Err(
            "Geometry source keys or base transform differ from the original selection".into(),
        );
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
        .filter(|instruction| match &instruction.operation {
            semwright_motion_authoring::NativeOp::Tween {
                target: candidate, ..
            } => candidate == &target,
            semwright_motion_authoring::NativeOp::Set {
                target: candidate, ..
            } => easing == MotionInterpolation::Hold && candidate == &target,
            _ => false,
        })
        .collect::<Vec<_>>();
    let valid = if easing == MotionInterpolation::Hold {
        instructions.len() == 8
            && instructions.iter().all(|instruction| {
                matches!(
                    &instruction.operation,
                    semwright_motion_authoring::NativeOp::Set { .. }
                ) && instruction.duration == RationalTime::ZERO
                    && instruction.easing == semwright_motion_authoring::MotionEasing::Linear
                    && (instruction.start == RationalTime::ZERO
                        || instruction.start == RationalTime::new(1, 1).expect("source frame"))
            })
    } else {
        instructions.len() == 4
            && instructions.iter().all(|instruction| {
                matches!(
                    &instruction.operation,
                    semwright_motion_authoring::NativeOp::Tween { .. }
                ) && instruction.start == RationalTime::ZERO
                    && instruction.duration == RationalTime::new(1, 1).expect("source frame")
                    && instruction.easing == semwright_motion_authoring::MotionEasing::OutCubic
            })
    };
    if !valid {
        return Err("Geometry source did not produce its exact Native sets or tweens".into());
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

fn source_identity(project: &motionwright_domain::Project, easing: &str) -> serde_json::Value {
    json!({
        "schema": SCHEMA, "project_id": project.id,
        "generation": project.generation, "revision": project.revision,
        "scene_id": project.scenes[0].id, "node_id": project.scenes[0].nodes[0].id,
        "deliverable_id": project.deliverables[0].id,
        "frame_count": FRAMES, "fps": 30, "canvas_size": [1920, 1080],
        "fill": COLOR, "easing": easing,
        "keyframes": [
            {"frame": 0, "x": 700.0,"y": 360.0,"width": 240.0,"height": 90.0,"rotation_deg": -18.0},
            {"frame": 30,"x": 740.0,"y": 390.0,"width": 400.0,"height": 180.0,"rotation_deg": 0.0}
        ],
        "native_execution": false, "formal_G2": false, "formal_G3": false
    })
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
    check_project(&project, MotionInterpolation::EaseOutCubic)?;
    let identity = source_identity(&project, "ease_out_cubic");
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

/// Capture the exact original Store only after the existing Studio Service case
/// saved all five held destinations and closed its writer. This grants no effect.
pub(super) fn capture_hold(
    database: &Path,
    receipt: &Path,
    studio_receipt: &Path,
    expected_studio_sha256: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    if !receipt.is_absolute()
        || !receipt.parent().is_some_and(Path::is_dir)
        || receipt.exists()
        || receipt.is_symlink()
        || expected_studio_sha256.len() != 64
        || sha_of(studio_receipt)? != expected_studio_sha256
    {
        return Err(
            "Held geometry capture requires a new destination and exact Studio original".into(),
        );
    }
    let studio: serde_json::Value = serde_json::from_slice(&fs::read(studio_receipt)?)?;
    if studio["schema"] != "motionwright.native-geometry-studio-hold/1"
        || studio["status"] != "FIVE_ORIGINAL_UI_SAVES_AND_CLOSED_STORE"
        || studio["ui_save_count"] != 5
        || studio["closed_store_handoff"] != true
        || studio["automatic_retry"] != false
        || studio["native_execution"] != false
        || studio["installed_tauri"] != false
        || studio["human_approval"] != false
    {
        return Err("Held geometry requires the bounded, unaccepted Studio observation".into());
    }
    let initial: motionwright_domain::Project =
        serde_json::from_value(studio["initial_project"].clone())?;
    check_project(&initial, MotionInterpolation::EaseOutCubic)?;
    let reader = StudioServiceReader::open(database)?;
    let projects = reader.projects(2)?;
    if projects.len() != 1 {
        return Err("Held geometry capture requires the single original Project".into());
    }
    let project = &projects[0];
    check_project(project, MotionInterpolation::Hold)?;
    let history = reader.project_history(project.id, 0, 16)?;
    // JSON.stringify writes an integral f64 such as 740.0 as 740. Reparse into
    // the exact existing domain types; every value, event and source byte hash
    // remains binding without treating JSON numeric spelling as a new Project.
    let observed_project: motionwright_domain::Project =
        serde_json::from_value(studio["project"].clone())?;
    let observed_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(studio["history"].clone())?;
    if project.id != initial.id
        || project.generation != initial.generation
        || project.revision != initial.revision.checked_add(5).ok_or("Revision overflow")?
        || project != &observed_project
        || history != observed_history
    {
        return Err("Held geometry Studio receipt differs from original SQLite/CAS".into());
    }
    let mut identity = source_identity(project, "hold");
    identity["studio_authoring_sha256"] = json!(expected_studio_sha256);
    cycle_new_json(
        receipt,
        &json!({
            "schema": SCHEMA, "identity": identity, "project": project, "history": history,
            "database_identity": reader.database_file_identity()?,
        }),
    )?;
    println!(
        "{}",
        json!({
            "geometry_seed": "SOURCE_SQLITE_ONLY", "source_receipt": receipt,
            "source_receipt_sha256": sha_of(receipt)?,
            "project_id": project.id, "generation": project.generation,
            "resource": project.resource_key(), "scene_id": project.scenes[0].id,
            "deliverable_id": project.deliverables[0].id,
            "width": project.deliverables[0].width, "height": project.deliverables[0].height,
            "revision": project.revision, "frame_count": FRAMES, "native_execution": false,
            "geometry_profile": "hold", "studio_authoring_sha256": expected_studio_sha256,
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
    let easing = match previous["identity"]["easing"].as_str() {
        Some("ease_out_cubic") => MotionInterpolation::EaseOutCubic,
        Some("hold") => MotionInterpolation::Hold,
        _ => return Err("Original geometry selection changed".into()),
    };
    check_project(&saved[0], easing)?;
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

    #[test]
    fn held_geometry_capture_requires_exact_ui_source_and_closed_store() {
        // Source contract only: a synthetic UI receipt cannot stand for a
        // browser run or Native effect. The selected CI path supplies originals.
        let dir = tempfile::tempdir().unwrap();
        let database = dir.path().join("held.sqlite3");
        let first_receipt = dir.path().join("before-studio.json");
        seed(&database, &first_receipt).unwrap();
        let service = StudioService::open(&database).unwrap();
        let initial = service.projects(2).unwrap().remove(0);
        let mut project = initial.clone();
        let mut keys = initial.scenes[0].nodes[0]
            .keyframes
            .iter()
            .filter(|key| key.at > RationalTime::ZERO)
            .cloned()
            .collect::<Vec<_>>();
        for (index, key) in keys.iter_mut().enumerate() {
            key.interpolation = MotionInterpolation::Hold;
            project = service
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("synthetic-held-source-{index}"),
                    &Change::SetCanvasKeyframe {
                        scene_id: initial.scenes[0].id,
                        node_id: initial.scenes[0].nodes[0].id,
                        keyframe: key.clone(),
                    },
                )
                .unwrap()
                .project;
        }
        let history = service.project_history(project.id, 0, 16).unwrap();
        let studio = dir.path().join("synthetic-studio-contract.json");
        let original_project = serde_json::to_value(&project).unwrap();
        let mut record = json!({
            "schema": "motionwright.native-geometry-studio-hold/1",
            "status": "FIVE_ORIGINAL_UI_SAVES_AND_CLOSED_STORE", "ui_save_count": 5,
            "closed_store_handoff": true, "automatic_retry": false,
            "native_execution": false, "installed_tauri": false, "human_approval": false,
            "initial_project": initial, "project": project, "history": history,
        });
        fn javascript_numbers(value: &mut serde_json::Value) {
            match value {
                serde_json::Value::Array(values) => values.iter_mut().for_each(javascript_numbers),
                serde_json::Value::Object(values) => {
                    values.values_mut().for_each(javascript_numbers)
                }
                serde_json::Value::Number(number) if number.is_f64() => {
                    let value_f64 = number.as_f64().unwrap();
                    if value_f64.fract() == 0.0 && value_f64.abs() <= 9_007_199_254_740_991.0 {
                        *value = json!(value_f64 as i64);
                    }
                }
                _ => {}
            }
        }
        javascript_numbers(&mut record);
        assert_ne!(
            record["project"], original_project,
            "the control must reproduce the actual JavaScript integer/f64 spelling change"
        );
        cycle_new_json(&studio, &record).unwrap();
        let sha = sha_of(&studio).unwrap();
        let held = dir.path().join("held-source.json");
        assert!(
            capture_hold(&database, &held, &studio, &sha).is_err(),
            "a receipt cannot bypass the original writer's SQLite handoff"
        );
        assert!(!held.exists());
        service.prepare_read_only_handoff().unwrap();
        drop(service);
        assert!(capture_hold(&database, &held, &studio, &"0".repeat(64)).is_err());
        let mut forged = record;
        forged["project"]["title"] = json!("Different source project");
        let substituted = dir.path().join("substituted-studio-contract.json");
        cycle_new_json(&substituted, &forged).unwrap();
        assert!(
            capture_hold(
                &database,
                &held,
                &substituted,
                &sha_of(&substituted).unwrap()
            )
            .is_err()
        );
        assert!(!held.exists());
        capture_hold(&database, &held, &studio, &sha).unwrap();
        let original: serde_json::Value =
            serde_json::from_slice(&fs::read(&held).unwrap()).unwrap();
        assert_eq!(original["identity"]["easing"], "hold");
        assert_eq!(original["identity"]["studio_authoring_sha256"], sha);
        readback(
            &database,
            &held,
            &sha_of(&held).unwrap(),
            &dir.path().join("held-readback.json"),
        )
        .unwrap();
    }

    #[test]
    fn independent_geometry_reader_requires_the_final_native_writer_handoff() {
        // Reproduce the physical post-render boundary without starting a
        // Semwright Host, mutating a native output, or repeating any effect.
        let dir = tempfile::tempdir().unwrap();
        let database = dir.path().join("post-native.sqlite3");
        let receipt = dir.path().join("source.original.json");
        seed(&database, &receipt).unwrap();
        let expected = sha_of(&receipt).unwrap();

        // A later Native coordinator opens a normal writer; that restores WAL
        // even when it does not change the authored Project or its history.
        let native_writer = StudioService::open(&database).unwrap();
        assert_eq!(native_writer.projects(2).unwrap().len(), 1);
        drop(native_writer);

        let refused = dir.path().join("readback-before-handoff.json");
        assert!(
            readback(&database, &receipt, &expected, &refused).is_err(),
            "the independent reader must not silently promote a WAL writer"
        );
        assert!(!refused.exists());

        // The legitimate final writer explicitly completes the original
        // SQLite/CAS handoff. The fresh read-only reader then accepts the same
        // project, journal, database identity and untouched source receipt.
        let writer = StudioService::open(&database).unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let recovered = dir.path().join("readback-after-handoff.json");
        readback(&database, &receipt, &expected, &recovered).unwrap();
        assert!(recovered.is_file());
        let report: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&recovered).unwrap()).unwrap();
        assert_eq!(report["reader_authority"], "StudioServiceReader");
        assert_eq!(report["source_receipt_sha256"], expected);
        assert_eq!(report["same_physical_database"], true);
        assert_eq!(report["source_history_unchanged"], true);
        assert_eq!(report["formal_G2"], false);
    }
}
