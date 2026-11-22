//! Preserve one original Studio-authored rectangular Clip group through the
//! same physical SQLite/CAS source used by the existing native render runner.
//! Source capture is read-only and never grants, renders or replays an effect.
use super::*;
use motionwright_service::StudioServiceReader;
use sha2::{Digest, Sha256};

const SCHEMA: &str = "motionwright.native-clip-group-source/1";
const STUDIO_SCHEMA: &str = "motionwright.native-clip-group-studio/1";

fn sha256_file(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    if !path.is_absolute()
        || !path.is_file()
        || path.is_symlink()
        || !path.parent().is_some_and(Path::is_dir)
        || fs::metadata(path)?.len() > 4 * 1024 * 1024
    {
        return Err("Original Clip source must be a bounded plain absolute file".into());
    }
    Ok(format!("{:x}", Sha256::digest(fs::read(path)?)))
}
fn new_receipt(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    if !path.is_absolute()
        || path.exists()
        || path.is_symlink()
        || !path.parent().is_some_and(Path::is_dir)
    {
        return Err("Original Clip destination is not a new absolute path".into());
    }
    Ok(())
}
fn clip_nodes(
    project: &motionwright_domain::Project,
) -> Result<(&CanvasNode, &CanvasNode), Box<dyn std::error::Error>> {
    let scene = project
        .scenes
        .first()
        .ok_or("Original Clip scene is missing")?;
    let groups = scene
        .nodes
        .iter()
        .filter(|node| node.kind == "clip_group")
        .collect::<Vec<_>>();
    if groups.len() != 1 || groups[0].name != "Clip group 1" {
        return Err("Original Clip group identity/roster differs".into());
    }
    let group = groups[0];
    let children = scene
        .nodes
        .iter()
        .filter(|node| node.parent_id == Some(group.id))
        .collect::<Vec<_>>();
    if children.len() != 1 || children[0].name != "Shape 1" {
        return Err("Original editable Clip child identity/roster differs".into());
    }
    Ok((group, children[0]))
}
fn check_project(
    project: &motionwright_domain::Project,
    baseline: &motionwright_domain::Project,
) -> Result<(), Box<dyn std::error::Error>> {
    let scene = project
        .scenes
        .first()
        .ok_or("Original Clip scene missing")?;
    let original_scene = baseline
        .scenes
        .first()
        .ok_or("Original baseline scene missing")?;
    let profile = project
        .deliverables
        .first()
        .ok_or("Original Clip profile missing")?;
    if project.scenes.len() != 1
        || baseline.scenes.len() != 1
        || scene.renderer != motionwright_domain::RendererKind::MotionCanvas
        || scene.duration != RationalTime::new(2, 1)?
        || scene.nodes.len() != original_scene.nodes.len() + 2
        || scene.nodes[..original_scene.nodes.len()] != original_scene.nodes[..]
        || scene.camera != original_scene.camera
        || scene.beats != original_scene.beats
        || project.id != baseline.id
        || project.generation != baseline.generation
        || project.active_branch != baseline.active_branch
        || project.assets != baseline.assets
        || project.audio != baseline.audio
        || project.deliverables != baseline.deliverables
        || project.production_design != baseline.production_design
        || (
            profile.width,
            profile.height,
            profile.frame_rate.num,
            profile.frame_rate.den,
        ) != (1920, 1080, 30, 1)
    {
        return Err("Clip Save modified unrelated original source or output profile".into());
    }
    let (group, child) = clip_nodes(project)?;
    if group.parent_id.is_some()
        || child.parent_id != Some(group.id)
        || group.kind != "clip_group"
        || child.kind != "shape"
        || (group.x, group.y, group.width, group.height) != (640.0, 360.0, 640.0, 360.0)
        || (child.x, child.y, child.width, child.height) != (1100.0, 430.0, 360.0, 220.0)
        || group.rotation_deg != 0.0
        || child.rotation_deg != 0.0
        || group.opacity != 1.0
        || child.opacity != 1.0
        || !group.keyframes.is_empty()
        || !child.keyframes.is_empty()
        || group.style.fill.is_some()
        || child.style.fill.as_deref() != Some("#E0A940")
        || !group.relations.is_empty()
        || !child.relations.is_empty()
    {
        return Err("Saved Clip bounds, parent, source fill or transform differ".into());
    }
    let options = fixture_film_options(scene.id, 60)?;
    let segments = build_motion_canvas_segments(project, profile.id, &options)?;
    if segments.len() != 1 || segments[0].frame_count != 60 {
        return Err("Original Clip changed Native Film duration or segment roster".into());
    }
    let shot = &segments[0].film.sequences[0].beats[0].shots[0];
    let native_group = shot
        .subjects
        .iter()
        .find(|subject| subject.id == format!("node-{}", group.id.simple()))
        .ok_or("Original Clip parent missing from Film")?;
    let native_child = shot
        .subjects
        .iter()
        .find(|subject| subject.id == format!("node-{}", child.id.simple()))
        .ok_or("Original Clip child missing from Film")?;
    if !matches!(
        &native_group.content,
        semwright_motion_authoring::SubjectContent::Group
    ) || !native_group.clip_intentional
        || native_child.clip_intentional
        || native_child.parent.as_deref() != Some(native_group.id.as_str())
    {
        return Err("Native Group.clip_intentional or editable child identity was lost".into());
    }
    match &native_child.layout {
        semwright_motion_authoring::SpatialIntent::Fixed { position, size }
            if (position.x - 320.0).abs() <= 1e-6
                && position.y.abs() <= 1e-6
                && (size.width - 360.0).abs() <= 1e-6
                && (size.height - 220.0).abs() <= 1e-6 => {}
        _ => {
            return Err(
                "Original world coordinates did not map to exact local Clip geometry".into(),
            );
        }
    }
    semwright_motion_authoring::realize(&segments[0].film)?;
    Ok(())
}
pub(super) fn before(database: &Path, receipt: &Path) -> Result<(), Box<dyn std::error::Error>> {
    new_receipt(receipt)?;
    let reader = StudioServiceReader::open(database)?;
    let projects = reader.projects(2)?;
    if projects.len() != 1
        || projects[0].scenes.len() != 1
        || projects[0].scenes[0]
            .nodes
            .iter()
            .any(|n| n.kind == "clip_group")
        || projects[0].scenes[0].duration != RationalTime::new(2, 1)?
    {
        return Err("Clip before-UI source is not the original native standard fixture".into());
    }
    let project = &projects[0];
    let history = reader.project_history(project.id, 0, 96)?;
    cycle_new_json(
        receipt,
        &json!({
            "schema":SCHEMA,"phase":"before_ui","project":project,"history":history,
            "database_identity":reader.database_file_identity()?,
            "native_execution":false,"automatic_retry":false,"formal_G2":false,"formal_G3":false
        }),
    )?;
    println!(
        "{}",
        json!({"source_receipt_sha256":sha256_file(receipt)?,
        "project_id":project.id,"generation":project.generation,"revision":project.revision,
        "native_execution":false})
    );
    Ok(())
}
pub(super) fn capture(
    database: &Path,
    before_path: &Path,
    studio_path: &Path,
    studio_sha: &str,
    receipt: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    new_receipt(receipt)?;
    if studio_sha.len() != 64 || sha256_file(studio_path)? != studio_sha {
        return Err("Original Clip Studio receipt hash mismatch".into());
    }
    let before: serde_json::Value = serde_json::from_slice(&fs::read(before_path)?)?;
    let studio: serde_json::Value = serde_json::from_slice(&fs::read(studio_path)?)?;
    if before["schema"] != SCHEMA
        || before["phase"] != "before_ui"
        || studio["schema"] != STUDIO_SCHEMA
        || studio["status"] != "FOUR_ORIGINAL_UI_SAVES_AND_CLOSED_STORE"
        || studio["ui_save_count"] != 4
        || studio["closed_store_handoff"] != true
        || studio["before_original_sha256"] != sha256_file(before_path)?
        || studio["automatic_retry"] != false
        || studio["native_execution"] != false
        || studio["installed_tauri"] != false
        || studio["human_approval"] != false
        || studio["lifecycle_taints"] != json!([])
        || studio["process_exits"].as_array().is_none_or(|exits| {
            exits.len() != 1 || exits[0]["code"] != 0 || !exits[0]["signal"].is_null()
        })
    {
        return Err("Original Studio Clip Save or closed writer not established".into());
    }
    let original: motionwright_domain::Project = serde_json::from_value(before["project"].clone())?;
    let observed_initial: motionwright_domain::Project =
        serde_json::from_value(studio["initial_project"].clone())?;
    let observed: motionwright_domain::Project = serde_json::from_value(studio["project"].clone())?;
    let ui_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(studio["history"].clone())?;
    let old_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(before["history"].clone())?;
    if original != observed_initial {
        return Err("Studio Clip original differs from independent pre-Save Store".into());
    }
    let reader = StudioServiceReader::open(database)?;
    // The saved Project and journal may be byte-for-byte identical in a copy.
    // Admission requires the original physical SQLite/CAS Store identity
    // captured before the first Studio UI Save, not a transplanted copy.
    let current_database_identity = serde_json::to_value(reader.database_file_identity()?)?;
    if before["database_identity"] != current_database_identity {
        return Err("Original Clip Store identity changed across the Studio Save".into());
    }
    let projects = reader.projects(2)?;
    if projects.len() != 1 {
        return Err("Original Clip Store project roster changed".into());
    }
    let actual = &projects[0];
    let history = reader.project_history(actual.id, 0, 96)?;
    if observed != *actual
        || history != ui_history
        || original.id != actual.id
        || original.generation != actual.generation
        || actual.revision
            != original
                .revision
                .checked_add(4)
                .ok_or("Clip revision overflow")?
        || history.len() != old_history.len() + 4
        || history[..old_history.len()] != old_history
    {
        return Err("Clip exact UI Saves differ from original physical SQLite/CAS".into());
    }
    check_project(actual, &original)?;
    let (group, child) = clip_nodes(actual)?;
    let events = &history[old_history.len()..];
    let scene_id = actual.scenes[0].id;
    let mut staged = child.clone();
    staged.x = 780.0;
    staged.style.fill = Some("#1D242C".into());
    let mut styled = staged.style.clone();
    styled.fill = Some("#E0A940".into());
    let final_transform = motionwright_domain::CanvasTransform {
        x: child.x,
        y: child.y,
        width: child.width,
        height: child.height,
        rotation_deg: child.rotation_deg,
        opacity: child.opacity,
    };
    let expected = [
        Change::AddCanvasNode {
            scene_id,
            node: group.clone(),
        },
        Change::AddCanvasNode {
            scene_id,
            node: staged,
        },
        Change::UpdateCanvasStyle {
            scene_id,
            node_id: child.id,
            style: styled,
        },
        Change::TransformCanvasNode {
            scene_id,
            node_id: child.id,
            transform: final_transform,
        },
    ];
    if events.iter().map(|event| &event.change).ne(expected.iter()) {
        return Err(
            "Clip Studio event journal contains unexpected or missing source changes".into(),
        );
    }
    cycle_new_json(
        receipt,
        &json!({
            "schema":SCHEMA,"phase":"after_ui","project":actual,"history":history,
            "before_original_sha256":sha256_file(before_path)?,
            "studio_original_sha256":studio_sha,
            "database_identity":current_database_identity,
            "native_execution":false,"automatic_retry":false,"formal_G2":false,"formal_G3":false
        }),
    )?;
    println!(
        "{}",
        json!({
            "project_id":actual.id,"generation":actual.generation,"revision":actual.revision,
            "source_receipt_sha256":sha256_file(receipt)?,
            "studio_authoring_sha256":studio_sha,"clip_group_saved":true,"native_execution":false
        })
    );
    Ok(())
}
pub(super) fn readback(
    database: &Path,
    receipt: &Path,
    expected_sha: &str,
    report: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    new_receipt(report)?;
    if expected_sha.len() != 64 || sha256_file(receipt)? != expected_sha {
        return Err("Clip independent reader source hash mismatch".into());
    }
    let previous: serde_json::Value = serde_json::from_slice(&fs::read(receipt)?)?;
    if previous["schema"] != SCHEMA || previous["phase"] != "after_ui" {
        return Err("Clip independent reader has no admitted original Save".into());
    }
    let expected: motionwright_domain::Project =
        serde_json::from_value(previous["project"].clone())?;
    let expected_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(previous["history"].clone())?;
    let reader = StudioServiceReader::open(database)?;
    let actual = reader.projects(2)?;
    if actual.len() != 1
        || actual[0] != expected
        || reader.project_history(expected.id, 0, 96)? != expected_history
        || serde_json::to_value(reader.database_file_identity()?)? != previous["database_identity"]
    {
        return Err("Clip independent same physical Store, journal or Project changed".into());
    }
    let report_value = json!({"schema":"motionwright.native-clip-group-readback/1",
        "source_receipt_sha256":expected_sha,"reader_authority":"StudioServiceReader",
        "same_physical_database":true,"source_project_unchanged":true,
        "source_history_unchanged":true,"native_effect_observed_by_reader":false,
        "formal_G2":false,"formal_G3":false,"human_approval":false});
    cycle_new_json(report, &report_value)?;
    println!("{report_value}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_clip_source_requires_closed_writer_and_reopens_original_store() {
        // These source-only synthetic edits do not substitute for the
        // separately selected Chromium UI and actual physical Native PNGs.
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("original.sqlite3");
        let (_writer, _project) = seed_project(&database).unwrap();
        drop(_writer);
        let before_file = temp.path().join("before.json");
        before(&database, &before_file).unwrap();
        let before_sha = sha256_file(&before_file).unwrap();
        let source: serde_json::Value =
            serde_json::from_slice(&fs::read(&before_file).unwrap()).unwrap();
        let baseline: motionwright_domain::Project =
            serde_json::from_value(source["project"].clone()).unwrap();
        let scene_id = baseline.scenes[0].id;
        let mut group = real_linear_motion_tile();
        group.id = Uuid::now_v7();
        group.kind = "clip_group".into();
        group.name = "Clip group 1".into();
        group.parent_id = None;
        group.x = 640.0;
        group.y = 360.0;
        group.width = 640.0;
        group.height = 360.0;
        group.z_index = 7;
        group.style = NodeStyle::default();
        group.keyframes.clear();
        let mut child = real_linear_motion_tile();
        child.id = Uuid::now_v7();
        child.kind = "shape".into();
        child.name = "Shape 1".into();
        child.parent_id = Some(group.id);
        child.x = 780.0;
        child.y = 430.0;
        child.width = 360.0;
        child.height = 220.0;
        child.z_index = 8;
        child.style = NodeStyle {
            fill: Some("#1D242C".into()),
            stroke: Some("#5A6570".into()),
            stroke_width: 1.0,
            font_family: None,
            font_size: None,
            font_weight: None,
            line_height: None,
            blend_mode: BlendMode::Normal,
        };
        child.keyframes.clear();
        let service = StudioService::open(&database).unwrap();
        let mut saved = service.projects(2).unwrap().remove(0);
        for (index, next) in [group.clone(), child.clone()].into_iter().enumerate() {
            saved = service
                .apply(
                    saved.id,
                    &RevisionStamp::from(&saved),
                    &format!("clip-test-add-{index}"),
                    &Change::AddCanvasNode {
                        scene_id,
                        node: next,
                    },
                )
                .unwrap()
                .project;
        }
        child.style.fill = Some("#E0A940".into());
        saved = service
            .apply(
                saved.id,
                &RevisionStamp::from(&saved),
                "clip-test-style",
                &Change::UpdateCanvasStyle {
                    scene_id,
                    node_id: child.id,
                    style: child.style.clone(),
                },
            )
            .unwrap()
            .project;
        child.x = 1100.0;
        saved = service
            .apply(
                saved.id,
                &RevisionStamp::from(&saved),
                "clip-test-position",
                &Change::TransformCanvasNode {
                    scene_id,
                    node_id: child.id,
                    transform: motionwright_domain::CanvasTransform {
                        x: child.x,
                        y: child.y,
                        width: child.width,
                        height: child.height,
                        rotation_deg: child.rotation_deg,
                        opacity: child.opacity,
                    },
                },
            )
            .unwrap()
            .project;
        check_project(&saved, &baseline).unwrap();
        let history = service.history(saved.id, 0, 96).unwrap();
        let studio = temp.path().join("synthetic-no-browser.json");
        cycle_new_json(
            &studio,
            &json!({
                "schema":STUDIO_SCHEMA,
                "status":"FOUR_ORIGINAL_UI_SAVES_AND_CLOSED_STORE",
                "ui_save_count":4,"closed_store_handoff":true,
                "before_original_sha256":before_sha,
                "initial_project":baseline,"project":saved,"history":history,
                "lifecycle_taints":[],"process_exits":[{"code":0,"signal":null}],
                "automatic_retry":false,"native_execution":false,
                "installed_tauri":false,"human_approval":false
            }),
        )
        .unwrap();
        let studio_sha = sha256_file(&studio).unwrap();
        let captured = temp.path().join("captured.json");
        assert!(
            capture(&database, &before_file, &studio, &studio_sha, &captured).is_err(),
            "Open original writer must refuse any independent source capture"
        );
        assert!(!captured.exists());
        service.prepare_read_only_handoff().unwrap();
        drop(service);
        capture(&database, &before_file, &studio, &studio_sha, &captured).unwrap();
        let source_sha = sha256_file(&captured).unwrap();
        let report = temp.path().join("reopened.json");
        readback(&database, &captured, &source_sha, &report).unwrap();
        let output: serde_json::Value = serde_json::from_slice(&fs::read(report).unwrap()).unwrap();
        assert_eq!(output["source_project_unchanged"], true);
        assert_eq!(output["same_physical_database"], true);
        assert_eq!(output["native_effect_observed_by_reader"], false);
        let wrong = temp.path().join("wrong-hash.json");
        assert!(readback(&database, &captured, &"0".repeat(64), &wrong).is_err());
        assert!(!wrong.exists());
        let mut forged: serde_json::Value =
            serde_json::from_slice(&fs::read(&studio).unwrap()).unwrap();
        forged["project"]["scenes"][0]["nodes"][0]["name"] = json!("tampered baseline");
        let fake = temp.path().join("forged.json");
        cycle_new_json(&fake, &forged).unwrap();
        assert!(
            capture(
                &database,
                &before_file,
                &fake,
                &sha256_file(&fake).unwrap(),
                &temp.path().join("forged-capture.json")
            )
            .is_err()
        );

        // A new SQLite file can contain the same Project and event history.
        // Re-signing both source receipts after replacing the database-file
        // identity must still fail before any Native Host can be admitted.
        let mut substituted_before: serde_json::Value =
            serde_json::from_slice(&fs::read(&before_file).unwrap()).unwrap();
        let original_identity = substituted_before["database_identity"].clone();
        assert!(original_identity.is_string());
        substituted_before["database_identity"] = json!("forged-volume-and-inode");
        let false_before = temp.path().join("substituted-physical-before.json");
        cycle_new_json(&false_before, &substituted_before).unwrap();
        let mut matching_studio: serde_json::Value =
            serde_json::from_slice(&fs::read(&studio).unwrap()).unwrap();
        matching_studio["before_original_sha256"] = json!(sha256_file(&false_before).unwrap());
        let false_studio = temp.path().join("substituted-physical-studio.json");
        cycle_new_json(&false_studio, &matching_studio).unwrap();
        let refused = temp.path().join("substituted-physical-capture.json");
        assert!(
            capture(
                &database,
                &false_before,
                &false_studio,
                &sha256_file(&false_studio).unwrap(),
                &refused,
            )
            .is_err(),
            "Original physical Store custody must survive re-signed matching source/history bytes",
        );
        assert!(!refused.exists());
    }
}
