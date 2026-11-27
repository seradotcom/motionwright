use super::{cycle_new_json, fixture_film_options};
use motionwright_domain::{BlendMode, Change, Project, RationalTime, RendererKind};
use motionwright_native::film::build_motion_canvas_segments;
use motionwright_service::StudioServiceReader;
use motionwright_storage::ProjectEvent;
use semwright_motion_authoring::{AUTHORING_BLEND_VERSION, SubjectBlendMode};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{error::Error, fs, io::Read, path::Path};

const MAX_STUDIO_BYTES: u64 = 1024 * 1024;
const BLENDS: [(&str, BlendMode, SubjectBlendMode); 3] = [
    ("multiply", BlendMode::Multiply, SubjectBlendMode::Multiply),
    ("screen", BlendMode::Screen, SubjectBlendMode::Screen),
    ("add", BlendMode::Add, SubjectBlendMode::Add),
];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StudioBlend {
    schema: String,
    before: Project,
    before_history: Vec<ProjectEvent>,
    project: Project,
    history: Vec<ProjectEvent>,
    database_identity_sha256: String,
    closed_store_handoff: bool,
}

pub(super) fn capture(
    database: &Path,
    studio: &Path,
    expected_sha256: &str,
    output: &Path,
) -> Result<(), Box<dyn Error>> {
    let metadata = fs::symlink_metadata(studio)?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > MAX_STUDIO_BYTES {
        return Err("Blend Studio receipt is not a bounded original file".into());
    }
    let mut bytes = Vec::new();
    fs::File::open(studio)?
        .take(MAX_STUDIO_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != metadata.len()
        || hex::encode(Sha256::digest(&bytes)) != expected_sha256
    {
        return Err("Blend Studio receipt SHA-256 differs".into());
    }
    let StudioBlend {
        schema,
        mut before,
        before_history,
        project,
        history,
        database_identity_sha256,
        closed_store_handoff,
    } = serde_json::from_slice(&bytes)?;
    if schema != "motionwright.native-blend-studio/1"
        || !closed_store_handoff
        || before.revision.checked_add(3) != Some(project.revision)
        || before_history.len() as u64 != before.revision
        || history.len() != before_history.len() + BLENDS.len()
        || !history.starts_with(&before_history)
    {
        return Err("Blend Studio receipt does not retain three ordered original commits".into());
    }
    let [scene] = before.scenes.as_mut_slice() else {
        return Err("Blend source requires one saved scene".into());
    };
    // Match the selected profile in the existing one-project Native runner.
    let profile = before
        .deliverables
        .first()
        .ok_or("Blend source has no saved output profile")?;
    if scene.renderer != RendererKind::MotionCanvas
        || scene.start != RationalTime::new(0, 1)?
        || scene.duration != RationalTime::new(2, 1)?
        || scene.nodes.len() != 2 * BLENDS.len()
        || (profile.width, profile.height) != (1920, 1080)
    {
        return Err("Blend original scene or output clock differs".into());
    }
    let scene_id = scene.id;
    let deliverable_id = profile.id;
    let mut subjects = Vec::with_capacity(BLENDS.len());
    for ((name, mode, film_mode), event) in BLENDS.into_iter().zip(&history[before_history.len()..])
    {
        let node = scene
            .nodes
            .iter_mut()
            .find(|node| node.name == format!("{name} overlay"))
            .ok_or("An original blend overlay is absent")?;
        if node.style.blend_mode != BlendMode::Normal {
            return Err("The original overlay was already blended before Canvas Save".into());
        }
        node.style.blend_mode = mode;
        if event.change
            != (Change::UpdateCanvasStyle {
                scene_id,
                node_id: node.id,
                style: node.style.clone(),
            })
        {
            return Err("Canvas changed a different original field or layer".into());
        }
        subjects.push((format!("node-{}", node.id.simple()), film_mode));
    }
    before.revision = project.revision;
    before.updated_at = project.updated_at;
    before
        .branches
        .iter_mut()
        .find(|branch| branch.id == before.active_branch)
        .ok_or("Blend original active branch is absent")?
        .head_revision = project.revision;
    if project != before {
        return Err("Blend Save modified unrelated original Project state".into());
    }

    let reader = StudioServiceReader::open(database)?;
    let identity = reader.database_file_identity()?;
    if hex::encode(Sha256::digest(identity.as_bytes())) != database_identity_sha256
        || reader.projects(2)? != [project.clone()]
        || reader.project_history(project.id, 0, 64)? != history
    {
        return Err("Blend Studio receipt differs from the reopened original Store".into());
    }

    let options = fixture_film_options(scene_id, 60)?;
    let segments = build_motion_canvas_segments(&project, deliverable_id, &options)?;
    let [segment] = segments.as_slice() else {
        return Err("Saved blend did not compile as one Motion Canvas segment".into());
    };
    let film = &segment.film;
    if segment.scene_ids != [scene_id]
        || segment.frame_count != 60
        || film.version != AUTHORING_BLEND_VERSION
        || film.shots().next().is_none()
        || !film.shots().all(|shot| {
            let overlays = shot
                .subjects
                .iter()
                .filter(|subject| subject.blend_mode != SubjectBlendMode::Normal);
            overlays.count() == subjects.len()
                && subjects.iter().all(|(id, mode)| {
                    shot.subjects
                        .iter()
                        .any(|subject| subject.id == *id && subject.blend_mode == *mode)
                })
        })
    {
        return Err("Saved Canvas blends did not survive the exact Film v2 lowering".into());
    }
    let film_sha256 = semwright_semantic_composition::canonical_digest(film)?;
    cycle_new_json(
        output,
        &json!({
            "schema": "motionwright.native-blend-source/1", "project_id": project.id,
            "generation": project.generation, "revision": project.revision, "scene_id": scene_id,
            "deliverable_id": deliverable_id, "frame_count": segment.frame_count,
            "database_identity_sha256": database_identity_sha256, "studio_original_sha256": expected_sha256,
            "film_sha256": film_sha256, "film": film, "native_execution": false,
        }),
    )?;
    Ok(())
}
