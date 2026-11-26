//! Desktop admission for the existing finite MC / static Manim / MC route.
//! Sources, timeline and master retain one original coordinator and root.
//! This layer cannot supply paths, renderer results, grants or resource limits.
use crate::{
    AppState, desktop_owner,
    effect_grants::{EffectKind, EffectScope},
};
use motionwright_domain::{Project, RendererKind, RevisionStamp};
use motionwright_native::{
    film::FilmBuildOptions,
    production::{
        HybridSourcePreflight, ProductionConnection, ProductionCoordinator, SavedMasterReceiptHint,
    },
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct HybridPreflightRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub options: FilmBuildOptions,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct HybridRenderRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub options: FilmBuildOptions,
    pub request_id: String,
    pub effect_grant: Uuid,
}

impl HybridRenderRequest {
    fn source(&self) -> HybridPreflightRequest {
        HybridPreflightRequest {
            project_id: self.project_id,
            generation: self.generation,
            revision: self.revision,
            deliverable_id: self.deliverable_id,
            options: self.options.clone(),
        }
    }
}

#[derive(Debug, Serialize)]
pub(crate) struct HybridFailure {
    schema: &'static str,
    status: &'static str,
    stage: &'static str,
    outcome_known: bool,
    may_have_native_effects: bool,
    automatic_retry: bool,
    message: String,
}

fn failure(
    stage: &'static str,
    outcome_known: bool,
    effects: bool,
    message: impl Into<String>,
) -> HybridFailure {
    HybridFailure {
        schema: "motionwright.hybrid-desktop-failure/1",
        status: if !outcome_known {
            "unknown"
        } else if effects {
            "stopped"
        } else {
            "blocked"
        },
        stage,
        outcome_known,
        may_have_native_effects: effects,
        automatic_retry: false,
        message: message.into().chars().take(480).collect(),
    }
}

fn blocked(message: impl Into<String>) -> HybridFailure {
    failure("preflight", true, false, message)
}

#[derive(Debug, Serialize)]
pub(crate) struct HybridRenderResponse {
    schema: &'static str,
    status: &'static str,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    master_sha256: String,
    frame_count: u64,
    fps_num: u32,
    fps_den: u32,
    saved_master: Option<SavedMasterReceiptHint>,
    review_warning: Option<&'static str>,
    automatic_retry: bool,
}

fn completed(
    source: &HybridSourcePreflight,
    sha256: String,
    hint: Option<SavedMasterReceiptHint>,
) -> HybridRenderResponse {
    // Discovery can fail after an irreversible, verified completion. Never
    // turn a missing hint or a retired owner into a failed render response.
    let hint = hint.filter(|hint| {
        hint.master_sha256 == sha256
            && hint.frame_count == source.frame_count
            && hint.fps_num == source.fps_num
            && hint.fps_den == source.fps_den
    });
    HybridRenderResponse {
        schema: "motionwright.hybrid-desktop-master/1",
        status: "completed",
        project_id: source.project_id,
        generation: source.generation,
        revision: source.revision,
        deliverable_id: source.deliverable_id,
        master_sha256: sha256,
        frame_count: source.frame_count,
        fps_num: source.fps_num,
        fps_den: source.fps_den,
        review_warning: hint.is_none().then_some(
            "The original master completed. Open saved masters to recover it; do not render it again."
        ),
        saved_master: hint,
        automatic_retry: false,
    }
}

// Validate the saved source on the Rust boundary as well as in Studio. The
// caller cannot relabel an unsaved role, archetype or font as this Project.
fn saved_options(
    project: &Project,
    deliverable_id: Uuid,
    supplied: &FilmBuildOptions,
) -> Result<FilmBuildOptions, HybridFailure> {
    use motionwright_native::film::{
        MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    };
    let recipe = project
        .production_design
        .delivery_recipes
        .get(&deliverable_id)
        .ok_or_else(|| blocked("Save the delivery scene intent before hybrid preflight."))?;
    let mut intents = Vec::<SceneFilmIntent>::new();
    let mut families = std::collections::BTreeSet::new();
    for scene in project
        .scenes
        .iter()
        .filter(|scene| scene.renderer == RendererKind::MotionCanvas)
    {
        let saved = recipe
            .scene_intents
            .iter()
            .find(|intent| intent.scene_id == scene.id)
            .ok_or_else(|| blocked("Every Motion Canvas scene needs saved delivery intent."))?;
        let intent = serde_json::to_value(saved)
            .and_then(serde_json::from_value::<SceneFilmIntent>)
            .map_err(|_| blocked("Every Motion Canvas scene needs a saved role and archetype."))?;
        intents.push(intent);
        for node in scene.nodes.iter().filter(|node| node.kind == "text") {
            let family = node
                .style
                .font_family
                .as_deref()
                .unwrap_or(MOTION_CANVAS_FONT_FAMILY);
            if family.trim().is_empty() {
                return Err(blocked(
                    "The saved Motion Canvas text has an empty font family.",
                ));
            }
            families.insert(family);
        }
    }
    if families.len() > 1 {
        return Err(blocked(
            "The hybrid source uses more than one saved primary text font.",
        ));
    }
    let family = families
        .into_iter()
        .next()
        .unwrap_or(MOTION_CANVAS_FONT_FAMILY);
    if ![MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY].contains(&family) {
        return Err(blocked(
            "This finite hybrid route requires the bundled original text fonts; use the retained-font Motion Canvas route for other families.",
        ));
    }
    let options = FilmBuildOptions {
        frame_rate: supplied.frame_rate,
        font_family: family.to_owned(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.to_owned(),
        scene_intents: intents,
    };
    if options.frame_rate.num != 30
        || options.frame_rate.den != 1
        || supplied.font_family != options.font_family
        || supplied.mono_font_family != options.mono_font_family
        || supplied.scene_intents.len() != options.scene_intents.len()
        || options.scene_intents.iter().any(|intent| {
            supplied
                .scene_intents
                .iter()
                .filter(|provided| *provided == intent)
                .count()
                != 1
        })
    {
        return Err(blocked(
            "Hybrid options differ from the saved delivery intent, font or finite clock.",
        ));
    }
    Ok(options)
}

async fn prepare(
    worker: desktop_owner::DesktopOwner,
    state: &AppState,
    request: HybridPreflightRequest,
) -> Result<
    (
        ProductionCoordinator,
        HybridSourcePreflight,
        FilmBuildOptions,
    ),
    HybridFailure,
> {
    let path = crate::selected_connection_path(state, request.project_id)
        .map_err(blocked)?
        .ok_or_else(|| {
            blocked("Select the current project's canonical Semwright connection first.")
        })?;
    let preparation = state.av_review.admit_preparation().ok_or_else(|| {
        blocked("Source read capacity is busy; wait for the current readers to finish.")
    })?;
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    desktop_owner::spawn_blocking(worker, move || {
        let _preparation = preparation;
        let project = service
            .project(request.project_id)
            .map_err(|_| blocked("The saved hybrid project is unavailable."))?;
        if project.generation != request.generation || project.revision != request.revision {
            return Err(blocked(
                "The hybrid source revision changed; refresh the saved project.",
            ));
        }
        let options = saved_options(&project, request.deliverable_id, &request.options)?;
        let expected = project.stamp();
        let connection = ProductionConnection::load(&path)
            .map_err(|_| blocked("The selected canonical connection could not be loaded."))?;
        let coordinator =
            ProductionCoordinator::with_receipt_custody(service, connection, &custody)
                .map_err(|_| blocked("The selected canonical connection was rejected."))?;
        let source = coordinator
            .preflight_native_hybrid_sources(
                request.project_id,
                &expected,
                request.deliverable_id,
                &options,
            )
            .map_err(|error| failure("preflight", error.outcome_known, false, error.message))?;
        // Observation of the existing owner is read-only. Neither a local
        // observation nor this preflight replaces the normal RenderLocal grant.
        let coordinator = coordinator
            .with_local_hybrid_project_owner(request.project_id, &expected, &path)
            .map_err(|error| failure("owner", error.outcome_known, false, error.message))?;
        Ok((coordinator, source, options))
    })
    .await
    .map_err(|_| blocked("The admitted source preflight worker could not finish."))?
}

fn ensure_current(
    state: &AppState,
    source: &HybridSourcePreflight,
) -> Result<RevisionStamp, HybridFailure> {
    let project = state
        .service
        .project(source.project_id)
        .map_err(|_| blocked("The saved hybrid project is unavailable."))?;
    if project.generation != source.generation || project.revision != source.revision {
        return Err(blocked(
            "The hybrid source changed during preflight; refresh the saved project.",
        ));
    }
    Ok(project.stamp())
}

pub(crate) async fn preflight(
    mut owner: desktop_owner::OwnerInvocation,
    state: &AppState,
    request: HybridPreflightRequest,
) -> Result<HybridSourcePreflight, HybridFailure> {
    let (_, source, _) = prepare(owner.worker_owner(), state, request).await?;
    owner.begin_phase().map_err(blocked)?;
    ensure_current(state, &source)?;
    Ok(source)
}

pub(crate) async fn render(
    mut owner: desktop_owner::OwnerInvocation,
    state: &AppState,
    request: HybridRenderRequest,
) -> Result<HybridRenderResponse, HybridFailure> {
    if Uuid::parse_str(&request.request_id)
        .ok()
        .is_none_or(|id| id.is_nil() || id.to_string() != request.request_id)
    {
        return Err(blocked(
            "The hybrid render requires a fresh canonical request identity.",
        ));
    }
    let (coordinator, source, options) =
        prepare(owner.worker_owner(), state, request.source()).await?;
    owner.begin_phase().map_err(blocked)?;
    let expected = ensure_current(state, &source)?;
    state
        .effect_grants
        .consume(
            request.effect_grant,
            EffectKind::RenderLocal,
            EffectScope::project(request.project_id, request.generation, request.revision),
            &request.request_id,
        )
        .map_err(|message| failure("permission", true, false, message))?;
    let sources = coordinator.realize_native_hybrid_sources(
        request.project_id, &expected, request.deliverable_id, &options,
    ).await.map_err(|error| failure("sources", error.outcome_known, true,
        format!("Hybrid sources did not complete ({:?}); inspect the original Jobs before another render.", error.code)))?;
    owner
        .begin_phase()
        .map_err(|message| failure("timeline", true, true, message))?;
    let timeline = coordinator.assemble_native_hybrid_timeline(&sources)
        .await.map_err(|error| failure("timeline", error.outcome_known, true,
            format!("Hybrid assembly did not complete ({:?}); inspect the original Jobs before another render.", error.code)))?;
    owner
        .begin_phase()
        .map_err(|message| failure("master", true, true, message))?;
    let master = coordinator.assemble_native_hybrid_master(&sources, &timeline, &sources.original_audio())
        .await.map_err(|error| failure("master", error.outcome_known, true,
            format!("Hybrid mastering did not complete ({:?}); inspect the original Jobs before another render.", error.code)))?;
    let sha256 = master.sha256().to_owned();
    if owner.begin_phase().is_err() {
        return Ok(completed(&source, sha256, None));
    }
    let Some(preparation) = state.av_review.admit_preparation() else {
        return Ok(completed(&source, sha256, None));
    };
    let project_id = request.project_id;
    let deliverable_id = request.deliverable_id;
    let inventory = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        coordinator.saved_master_receipt_inventory(project_id, &expected, deliverable_id)
    })
    .await;
    let hint = inventory.ok().and_then(Result::ok).and_then(|inventory| {
        inventory
            .items
            .into_iter()
            .find(|hint| hint.master_sha256 == sha256)
    });
    Ok(completed(&source, sha256, hint))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn saved_source() -> (Project, Uuid, FilmBuildOptions) {
        use motionwright_domain::Change;
        let dir = tempfile::tempdir().unwrap();
        let service =
            motionwright_service::StudioService::open(dir.path().join("source.sqlite3")).unwrap();
        let mut project = service
            .create_project("Saved hybrid admission fixture")
            .unwrap();
        for index in 0..3 {
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    "fixture-scene",
                    &Change::AddScene {
                        name: format!("Scene {index}"),
                        objective: "Original intent".into(),
                        duration_seconds: 2,
                    },
                )
                .unwrap()
                .project;
        }
        project.scenes[1].renderer = RendererKind::ManimCommunity;
        let profile = project.deliverables[0].id;
        let intents: Vec<_> = [0, 2].into_iter().map(|index| serde_json::json!({
            "scene_id": project.scenes[index].id, "role": "evidence", "archetype": "statement",
        })).collect();
        project.production_design.delivery_recipes.insert(profile, serde_json::from_value(serde_json::json!({
            "schema": "motionwright.delivery-recipe/1", "scene_intents": intents, "original_mix": null,
        })).unwrap());
        let options = serde_json::from_value(serde_json::json!({
            "frame_rate": {"num": 30, "den": 1}, "font_family": "Instrument Sans Variable",
            "mono_font_family": "IBM Plex Mono", "scene_intents": intents,
        }))
        .unwrap();
        (project, profile, options)
    }

    #[test]
    fn hybrid_desktop_derives_canonical_saved_intent_and_rejects_unsaved_options() {
        let (project, profile, options) = saved_source();
        assert_eq!(saved_options(&project, profile, &options).unwrap(), options);
        let mut reordered = options.clone();
        reordered.scene_intents.reverse();
        assert_eq!(
            saved_options(&project, profile, &reordered).unwrap(),
            options
        );
        let mut changed = options.clone();
        changed.scene_intents[0].role = serde_json::from_value(serde_json::json!("hook")).unwrap();
        assert!(saved_options(&project, profile, &changed).is_err());
        changed = options.clone();
        changed.scene_intents[0].scene_id = project.scenes[1].id;
        assert!(saved_options(&project, profile, &changed).is_err());
        changed = options.clone();
        changed.scene_intents.push(changed.scene_intents[0].clone());
        assert!(saved_options(&project, profile, &changed).is_err());
        changed = options.clone();
        changed.font_family = "Unsaved font".into();
        assert!(saved_options(&project, profile, &changed).is_err());
        let mut unsaved = project.clone();
        unsaved.production_design.delivery_recipes.clear();
        assert!(saved_options(&unsaved, profile, &options).is_err());
        unsaved = project.clone();
        unsaved
            .production_design
            .delivery_recipes
            .get_mut(&profile)
            .unwrap()
            .scene_intents[0]
            .role = None;
        assert!(saved_options(&unsaved, profile, &options).is_err());
    }

    #[test]
    fn hybrid_request_has_no_caller_path_authority_limits_or_native_results() {
        let (project, profile, options) = saved_source();
        let request = serde_json::json!({
            "project_id": project.id, "generation": project.generation, "revision": project.revision,
            "deliverable_id": profile, "options": options,
        });
        assert!(serde_json::from_value::<HybridPreflightRequest>(request.clone()).is_ok());
        for key in [
            "connection_path",
            "output_root",
            "native_result",
            "root_id",
            "limits",
            "effect_grant",
        ] {
            let mut bad = request.clone();
            bad[key] = serde_json::json!("caller supplied");
            assert!(
                serde_json::from_value::<HybridPreflightRequest>(bad).is_err(),
                "{key}"
            );
        }
        let mut render = request;
        render["request_id"] = serde_json::json!(Uuid::new_v4());
        render["effect_grant"] = serde_json::json!(Uuid::new_v4());
        assert!(serde_json::from_value::<HybridRenderRequest>(render.clone()).is_ok());
        render["original_audio_path"] = serde_json::json!("caller/file.wav");
        assert!(serde_json::from_value::<HybridRenderRequest>(render).is_err());
    }

    #[test]
    fn hybrid_completion_survives_lost_review_and_never_adopts_a_foreign_master() {
        let source = HybridSourcePreflight {
            schema: "motionwright.hybrid-source-preflight/1",
            project_id: Uuid::new_v4(),
            generation: Uuid::new_v4(),
            revision: 5,
            deliverable_id: Uuid::new_v4(),
            project_sha256: "a".repeat(64),
            source_count: 3,
            frame_count: 180,
            width: 1920,
            height: 1080,
            fps_num: 30,
            fps_den: 1,
            source_audio_sha256: "b".repeat(64),
        };
        let sha = "c".repeat(64);
        let original = SavedMasterReceiptHint {
            mux_receipt_id: Uuid::new_v4(),
            master_sha256: sha.clone(),
            frame_count: 180,
            fps_num: 30,
            fps_den: 1,
        };
        let mut wrong_sha = original.clone();
        wrong_sha.master_sha256 = "d".repeat(64);
        let mut wrong_clock = original.clone();
        wrong_clock.frame_count = 181;
        for hint in [None, Some(wrong_sha), Some(wrong_clock)] {
            let result = completed(&source, sha.clone(), hint);
            assert_eq!(result.status, "completed");
            assert_eq!(result.master_sha256, sha);
            assert_eq!(result.project_id, source.project_id);
            assert!(result.saved_master.is_none());
            assert!(result.review_warning.is_some());
            assert!(!result.automatic_retry);
        }
        let result = completed(&source, sha, Some(original.clone()));
        assert_eq!(result.saved_master, Some(original));
        assert!(result.review_warning.is_none());
    }

    #[test]
    fn hybrid_unknown_and_stopped_phases_keep_original_uncertainty_without_retry() {
        let unknown = failure("master", false, true, "Inspect original operation.");
        assert_eq!(unknown.status, "unknown");
        assert!(!unknown.outcome_known);
        assert!(unknown.may_have_native_effects);
        assert!(!unknown.automatic_retry);
        let retired = failure(
            "timeline",
            true,
            true,
            "Owner closed after source completion.",
        );
        assert_eq!(retired.status, "stopped");
        assert!(retired.outcome_known);
        assert!(retired.may_have_native_effects);
        assert!(!retired.automatic_retry);
        let denied = blocked("Unsupported source before permission.");
        assert_eq!(denied.status, "blocked");
        assert!(!denied.may_have_native_effects);
    }
}
