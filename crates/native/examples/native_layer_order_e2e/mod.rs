//! Original layer-order source carried through Studio and Native Film.
//! No effect, provider, grant or installed-Tauri permission is exposed here.
use super::*;
use motionwright_service::StudioServiceReader;
use sha2::{Digest, Sha256};

const SCHEMA: &str = "motionwright.native-layer-order-source/1";
const STUDIO_SCHEMA: &str = "motionwright.native-layer-order-studio/1";
const FRONT: &str = "Native layer order amber foreground";
const BACK: &str = "Native layer order blue background";
// Keep the two editable test layers above the existing group and motion controls.
pub(super) const FRONT_BEFORE_LAYER: i32 = 7;
pub(super) const BACK_LAYER: i32 = 8;
const FRONT_AFTER_LAYER: i32 = 9;

fn sha256_file(path: &Path) -> Result<String, Box<dyn std::error::Error>> {
    if !path.is_absolute()
        || !path.is_file()
        || path.is_symlink()
        || !path.parent().is_some_and(Path::is_dir)
        || fs::metadata(path)?.len() > 4 * 1024 * 1024
    {
        return Err("Layer-order original must be a bounded plain absolute file".into());
    }
    Ok(format!("{:x}", Sha256::digest(fs::read(path)?)))
}

fn new_receipt(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    if !path.is_absolute()
        || path.exists()
        || path.is_symlink()
        || !path.parent().is_some_and(Path::is_dir)
    {
        return Err("Layer-order receipt destination is not an unused absolute file".into());
    }
    Ok(())
}

fn unique<'a>(
    project: &'a motionwright_domain::Project,
    name: &str,
) -> Result<&'a CanvasNode, Box<dyn std::error::Error>> {
    let matched = project.scenes[0]
        .nodes
        .iter()
        .filter(|n| n.name == name)
        .collect::<Vec<_>>();
    if matched.len() != 1 {
        return Err("Original layer-order object identity/roster changed".into());
    }
    Ok(matched[0])
}

pub(super) fn check_project(
    project: &motionwright_domain::Project,
    promoted: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let scene = project
        .scenes
        .first()
        .ok_or("Source layer scene is missing")?;
    let profile = project
        .deliverables
        .first()
        .ok_or("Layer output profile is missing")?;
    if project.scenes.len() != 1
        || scene.duration != RationalTime::new(2, 1)?
        || scene.renderer != motionwright_domain::RendererKind::MotionCanvas
        || (profile.width, profile.height) != (1920, 1080)
        || (profile.frame_rate.num, profile.frame_rate.den) != (30, 1)
        || !scene.beats.is_empty()
    {
        return Err("Original layer-order source scene/output profile changed".into());
    }
    let front = unique(project, FRONT)?;
    let back = unique(project, BACK)?;
    if front.id == back.id
        || front.kind != "rectangle"
        || back.kind != "rectangle"
        || front.parent_id.is_some()
        || back.parent_id.is_some()
        || !front.keyframes.is_empty()
        || !back.keyframes.is_empty()
        || front.opacity != 1.0
        || back.opacity != 1.0
        || front.rotation_deg != 0.0
        || back.rotation_deg != 0.0
        || (front.x, front.y, front.width, front.height) != (405.0, 820.0, 155.0, 95.0)
        || (back.x, back.y, back.width, back.height) != (340.0, 790.0, 240.0, 145.0)
        || front.z_index
            != if promoted {
                FRONT_AFTER_LAYER
            } else {
                FRONT_BEFORE_LAYER
            }
        || back.z_index != BACK_LAYER
        || front.style.fill.as_deref() != Some("#E0A940")
        || back.style.fill.as_deref() != Some("#2569B8")
    {
        return Err("Original layer-order nodes, exact Z or color were substituted".into());
    }
    let segments =
        build_motion_canvas_segments(project, profile.id, &fixture_film_options(scene.id, 60)?)?;
    if segments.len() != 1 || segments[0].frame_count != 60 {
        return Err("Layer Native Film segment no longer matches the original frame roster".into());
    }
    let shot = &segments[0].film.sequences[0].beats[0].shots[0];
    for (node, expected_order) in [(front, front.z_index), (back, back.z_index)] {
        let subject = shot
            .subjects
            .iter()
            .find(|subject| subject.id == format!("node-{}", node.id.simple()))
            .ok_or("Layer Film subject missing")?;
        let expected_layer = if expected_order < 0 {
            format!("layer-n{}", expected_order.unsigned_abs())
        } else {
            format!("layer-p{expected_order}")
        };
        if subject.layer != expected_layer
            || shot
                .layers
                .iter()
                .filter(|layer| layer.id == expected_layer && layer.order == expected_order)
                .count()
                != 1
        {
            return Err("Native Film does not preserve the saved Canvas layer order".into());
        }
    }
    Ok(())
}

pub(super) fn before(database: &Path, receipt: &Path) -> Result<(), Box<dyn std::error::Error>> {
    new_receipt(receipt)?;
    let reader = StudioServiceReader::open(database)?;
    let projects = reader.projects(2)?;
    if projects.len() != 1 {
        return Err("Original z-order source must contain one physical Project".into());
    }
    let project = &projects[0];
    check_project(project, false)?;
    let history = reader.project_history(project.id, 0, 64)?;
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
        json!({"original_source_sha256":sha256_file(receipt)?,
        "project_id":project.id,"generation":project.generation,"revision":project.revision,
        "native_execution":false})
    );
    Ok(())
}

pub(super) fn capture(
    database: &Path,
    before_path: &Path,
    studio_path: &Path,
    expected_studio_sha: &str,
    receipt: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    new_receipt(receipt)?;
    if expected_studio_sha.len() != 64 || sha256_file(studio_path)? != expected_studio_sha {
        return Err("Layer Studio receipt source SHA differs".into());
    }
    let before: serde_json::Value = serde_json::from_slice(&fs::read(before_path)?)?;
    let studio: serde_json::Value = serde_json::from_slice(&fs::read(studio_path)?)?;
    if before["schema"] != SCHEMA
        || before["phase"] != "before_ui"
        || studio["schema"] != STUDIO_SCHEMA
        || studio["status"] != "ONE_SAVED_UI_LAYER_PROMOTION_AND_CLOSED_STORE"
        || studio["ui_save_count"] != 1
        || studio["closed_store_handoff"] != true
        || studio["automatic_retry"] != false
        || studio["native_execution"] != false
        || studio["installed_tauri"] != false
        || studio["human_approval"] != false
        || studio["before_original_sha256"] != sha256_file(before_path)?
        || studio["lifecycle_taints"] != json!([])
        || studio["process_exits"].as_array().is_none_or(|exits| {
            exits.len() != 1 || exits[0]["code"] != 0 || !exits[0]["signal"].is_null()
        })
    {
        return Err("Layer UI authoring original was not admitted".into());
    }
    let original: motionwright_domain::Project = serde_json::from_value(before["project"].clone())?;
    let observed_initial: motionwright_domain::Project =
        serde_json::from_value(studio["initial_project"].clone())?;
    let observed: motionwright_domain::Project = serde_json::from_value(studio["project"].clone())?;
    if observed_initial != original {
        return Err("Original UI layer source differs from independently captured Store".into());
    }
    let ui_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(studio["history"].clone())?;
    let original_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(before["history"].clone())?;
    check_project(&original, false)?;
    check_project(&observed, true)?;
    // The only visual source mutation in this unit is front.z_index. Nothing
    // else in the edited scene, nodes, style or other authored content moves.
    let mut expected_scene = original.scenes[0].clone();
    let front_id = unique(&original, FRONT)?.id;
    let expected_front = expected_scene
        .nodes
        .iter_mut()
        .find(|n| n.id == front_id)
        .ok_or("Original layer foreground disappeared")?;
    expected_front.z_index = FRONT_AFTER_LAYER;
    if observed.scenes != vec![expected_scene]
        || observed.assets != original.assets
        || observed.audio != original.audio
        || observed.deliverables != original.deliverables
        || observed.production_design != original.production_design
        || observed.visual_language != original.visual_language
    {
        return Err("Layer Save modified unrelated original creative content".into());
    }
    let reader = StudioServiceReader::open(database)?;
    // A copied SQLite database can contain an identical Project and journal.
    // Source custody binds the physical file identity captured BEFORE the
    // Studio Save to the independent reader AFTER the saved revision.
    let original_file_identity = serde_json::to_value(reader.database_file_identity()?)?;
    if before["database_identity"] != original_file_identity {
        return Err("Original layer Store identity changed across Studio Save".into());
    }
    let actual = reader.projects(2)?;
    if actual.len() != 1 {
        return Err("Layer UI Store contains a substituted project roster".into());
    }
    let final_project = &actual[0];
    let history = reader.project_history(final_project.id, 0, 64)?;
    if original.id != final_project.id
        || original.generation != final_project.generation
        || final_project.revision
            != original
                .revision
                .checked_add(1)
                .ok_or("Layer revision overflow")?
        || final_project != &observed
        || history != ui_history
        || history.len() != original_history.len() + 1
        || history[..original_history.len()] != original_history
    {
        return Err("Layer UI Save differs from exact original SQLite/CAS source".into());
    }
    let original_front_id = unique(final_project, FRONT)?.id;
    match &history
        .last()
        .ok_or("Layer project has no saved event")?
        .change
    {
        Change::ReparentCanvasNode {
            scene_id,
            node_id,
            parent_id,
            z_index,
            preserve,
        } if *scene_id == final_project.scenes[0].id
            && *node_id == original_front_id
            && parent_id.is_none()
            && *z_index == FRONT_AFTER_LAYER
            && preserve.is_none() => {}
        _ => return Err("The original layer UI saved a different change or authority".into()),
    };
    // The exact complete final Project and event history are required, not just
    // matching IDs. No native grant or second operation may be inferred.
    cycle_new_json(
        receipt,
        &json!({
            "schema":SCHEMA,"phase":"after_ui","project":final_project,
            "history":history,"database_identity":original_file_identity,
            "before_original_sha256":sha256_file(before_path)?,
            "studio_original_sha256":expected_studio_sha,"native_execution":false,
            "formal_G2":false,"formal_G3":false,"automatic_retry":false
        }),
    )?;
    println!(
        "{}",
        json!({
            "project_id":final_project.id,"generation":final_project.generation,
            "revision":final_project.revision,"layer_order_ui_saved":true,
            "source_receipt_sha256":sha256_file(receipt)?,
            "studio_authoring_sha256":expected_studio_sha,"native_execution":false
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
        return Err("Layer readback does not match the original source receipt SHA".into());
    }
    let previous: serde_json::Value = serde_json::from_slice(&fs::read(receipt)?)?;
    if previous["schema"] != SCHEMA || previous["phase"] != "after_ui" {
        return Err("Layer readback has no admitted saved Source".into());
    }
    let expected_project: motionwright_domain::Project =
        serde_json::from_value(previous["project"].clone())?;
    let expected_history: Vec<motionwright_storage::ProjectEvent> =
        serde_json::from_value(previous["history"].clone())?;
    let reader = StudioServiceReader::open(database)?;
    let actual = reader.projects(2)?;
    if actual.len() != 1
        || actual[0] != expected_project
        || reader.project_history(expected_project.id, 0, 64)? != expected_history
        || serde_json::to_value(reader.database_file_identity()?)? != previous["database_identity"]
    {
        return Err(
            "Independent layer reader found a different Project, event history or Store".into(),
        );
    }
    check_project(&actual[0], true)?;
    let result = json!({"schema":"motionwright.native-layer-order-independent-readback/1",
        "source_receipt_sha256":expected_sha,"reader_authority":"StudioServiceReader",
        "source_project_unchanged":true,"source_history_unchanged":true,
        "same_physical_database":true,"native_effect_observed_by_reader":false,
        "human_approval":false,"formal_G2":false,"formal_G3":false});
    cycle_new_json(report, &result)?;
    println!("{result}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_layer_save_requires_exact_source_and_independent_store_reopen() {
        // Pure source/Store fixture, not browser evidence or a Native effect.
        let dir = tempfile::tempdir().unwrap();
        let database = dir.path().join("layer.sqlite3");
        let original = dir.path().join("before-layer.json");
        seed_layer_order(&database).unwrap();
        before(&database, &original).unwrap();
        let original_sha = sha256_file(&original).unwrap();
        let service = StudioService::open(&database).unwrap();
        let initial = service.projects(2).unwrap().remove(0);
        check_project(&initial, false).unwrap();
        let id = unique(&initial, FRONT).unwrap().id;
        let saved = service
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "source-only-unique-layer-commit",
                &Change::ReparentCanvasNode {
                    scene_id: initial.scenes[0].id,
                    node_id: id,
                    parent_id: None,
                    z_index: FRONT_AFTER_LAYER,
                    preserve: None,
                },
            )
            .unwrap()
            .project;
        check_project(&saved, true).unwrap();
        let history = service.history(saved.id, 0, 64).unwrap();
        let studio = dir.path().join("synthetic-not-a-browser.json");
        let initial_receipt = json!({
            "schema": STUDIO_SCHEMA, "status":"ONE_SAVED_UI_LAYER_PROMOTION_AND_CLOSED_STORE",
            "ui_save_count":1,"closed_store_handoff":true,"automatic_retry":false,
            "native_execution":false,"installed_tauri":false,"human_approval":false,
            "before_original_sha256":original_sha,"initial_project":initial,
            "project":saved,"history":history,"lifecycle_taints":[],
            "process_exits":[{"code":0,"signal":null}],
        });
        cycle_new_json(&studio, &initial_receipt).unwrap();
        let studio_sha = sha256_file(&studio).unwrap();
        let saved_source = dir.path().join("source-after-studio.json");
        assert!(
            capture(
                &database,
                &original,
                &studio,
                &"0".repeat(64),
                &saved_source
            )
            .is_err()
        );
        assert!(!saved_source.exists());
        service.prepare_read_only_handoff().unwrap();
        drop(service);
        capture(&database, &original, &studio, &studio_sha, &saved_source).unwrap();
        let source_sha = sha256_file(&saved_source).unwrap();
        let report = dir.path().join("reopened-same-store.json");
        readback(&database, &saved_source, &source_sha, &report).unwrap();
        let output: serde_json::Value =
            serde_json::from_slice(&fs::read(&report).unwrap()).unwrap();
        assert_eq!(output["source_project_unchanged"], true);
        assert_eq!(output["source_history_unchanged"], true);
        assert_eq!(output["same_physical_database"], true);
        assert_eq!(output["native_effect_observed_by_reader"], false);
        assert!(
            readback(
                &database,
                &saved_source,
                &"0".repeat(64),
                &dir.path().join("wrong-hash.json")
            )
            .is_err()
        );
        let mut forged = initial_receipt;
        forged["project"]["scenes"][0]["nodes"][0]["name"] = json!("Substituted node");
        let other = dir.path().join("substituted-ui.json");
        cycle_new_json(&other, &forged).unwrap();
        assert!(
            capture(
                &database,
                &original,
                &other,
                &sha256_file(&other).unwrap(),
                &dir.path().join("substituted.json")
            )
            .is_err()
        );

        // A copied SQLite file can contain an identical Project/history.
        // Even after both receipts are re-hashed, the original OS file
        // identity must remain bound across the actual UI Save.
        let mut forged_before: serde_json::Value =
            serde_json::from_slice(&fs::read(&original).unwrap()).unwrap();
        assert!(forged_before["database_identity"].is_string());
        forged_before["database_identity"] = json!("substituted-same-content-physical-file");
        let different_file = dir.path().join("substituted-file-before.json");
        cycle_new_json(&different_file, &forged_before).unwrap();
        let mut matching_ui: serde_json::Value =
            serde_json::from_slice(&fs::read(&studio).unwrap()).unwrap();
        matching_ui["before_original_sha256"] = json!(sha256_file(&different_file).unwrap());
        let matching_ui_file = dir.path().join("substituted-file-studio.json");
        cycle_new_json(&matching_ui_file, &matching_ui).unwrap();
        let refused = dir.path().join("substituted-file-receipt.json");
        assert!(
            capture(
                &database,
                &different_file,
                &matching_ui_file,
                &sha256_file(&matching_ui_file).unwrap(),
                &refused,
            )
            .is_err(),
            "A substituted database identity with otherwise matching content must refuse pre-effect",
        );
        assert!(!refused.exists());
    }
}
