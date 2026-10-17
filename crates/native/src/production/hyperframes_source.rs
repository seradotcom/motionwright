//! Finite HyperFrames source custody through the existing Project/Store.
//!
//! A NativeCapsule identifies immutable source; its editor/fidelity metadata is
//! never a provider selection, a permission or native execution evidence. This
//! module admits and reopens real CAS bytes. The B250 canonical RendererKind
//! does not yet contain HyperFrames, so the execution preflight is explicitly
//! closed until that shared contract is integrated. No existing renderer aliases
//! HyperFrames and no operation is issued by a source inspection.

use super::*;
use motionwright_domain::{Asset, NativeCapsule, Project, RendererKind};
use motionwright_hyperframes_profile::{
    AdmittedSource, MAX_CARRIER_BYTES, SourceError, SourceReadiness, admit_source, require_sha256,
    sha256,
};

pub const HYPERFRAMES_SOURCE_MIME: &str = "application/vnd.motionwright.hyperframes-source+json";
pub const HYPERFRAMES_RENDER_COMMAND: &str = "driver.hyperframes.source.render";
pub const HYPERFRAMES_CANONICAL_SELECTION_PENDING: &str = "HyperFrames execution requires its canonical RendererKind; an attached source or another renderer cannot select it";

/// Only identities travel from an editor. No paths, source bytes, Producer
/// settings, renderer strings, tool choices or imported evidence are accepted.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct HyperframesSourceSelection {
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub deliverable_id: Uuid,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct HyperframesSourceInspection {
    pub project_stamp: RevisionStamp,
    pub selection: HyperframesSourceSelection,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    /// Conservative dependency on the complete current saved Project. This is
    /// an application binding, not a Semwright composition fingerprint.
    pub project_snapshot_sha256: String,
    pub canonical_dependency_sha256: String,
    pub source: SourceReadiness,
    pub native_execution_performed: bool,
    pub metadata_used_as_authority: bool,
    pub native_render_verified: bool,
    pub canonical_import_applied: bool,
    pub export_authorized: bool,
}

/// No Deserialize, public constructor, permission bit or filesystem path. Only
/// the current service and immutable CAS bytes can create this snapshot.
#[derive(Debug, Clone)]
pub struct PreparedHyperframesSource {
    project: Project,
    selection: HyperframesSourceSelection,
    source: AdmittedSource,
    inspection: HyperframesSourceInspection,
}

impl PreparedHyperframesSource {
    pub fn inspection(&self) -> &HyperframesSourceInspection {
        &self.inspection
    }

    /// Re-read the current Project and the selected CAS object. A snapshot is
    /// never a readiness token after a revision, generation or source change.
    pub fn revalidate(&self, service: &StudioService) -> NativeResult<()> {
        let current = current_project(service, self.project.id, &self.inspection.project_stamp)?;
        self.verify_project(&current)?;
        let observed = read_selected_source(service, &current, &self.selection)?;
        if observed.export_bytes() != self.source.export_bytes() {
            return Err(stale("HyperFrames source bytes changed after preparation"));
        }
        let final_project = current_project(service, current.id, &self.inspection.project_stamp)?;
        self.verify_project(&final_project)
    }

    /// Return the exact admitted carrier for backend storage/export. This reads
    /// CAS again and does not issue a desktop export token or execution grant.
    pub fn retained_source_bytes(&self, service: &StudioService) -> NativeResult<Vec<u8>> {
        self.revalidate(service)?;
        Ok(self.source.export_bytes().to_vec())
    }

    fn verify_project(&self, current: &Project) -> NativeResult<()> {
        if current != &self.project
            || project_digest(current)? != self.inspection.project_snapshot_sha256
        {
            return Err(stale(
                "The complete HyperFrames source Project dependency changed",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct HyperframesSourceReopenInspection {
    pub project_stamp: RevisionStamp,
    pub selection: HyperframesSourceSelection,
    pub original_asset_id: Uuid,
    pub reopened_asset_id: Uuid,
    pub original_sha256: String,
    pub reopened_sha256: String,
    pub canonical_dependency_sha256: String,
    pub exact_carrier_bytes_reopened: bool,
    pub document_unchanged: bool,
    pub native_html_unchanged: bool,
    pub resource_bytes_unchanged: bool,
    pub finite_source_contract_verified: bool,
    pub evidence_scope: String,
    pub canonical_import_applied: bool,
    pub native_rerender_verified: bool,
    pub imported_receipts_used_as_authority: bool,
    pub export_authorized: bool,
}

fn stale(message: &str) -> Error {
    Error::new(ErrorCode::StaleReference, message)
}

fn profile_error(error: SourceError) -> Error {
    let code = match error {
        SourceError::Unsupported(_) => ErrorCode::Unsupported,
        SourceError::Limit(_) => ErrorCode::ResourceExhausted,
        SourceError::Invalid(_) | SourceError::Binding(_) => ErrorCode::InvalidArgument,
    };
    Error::new(code, error.to_string())
}

fn project_digest(project: &Project) -> NativeResult<String> {
    let bytes = serde_json::to_vec(project)
        .map_err(|_| backend("HyperFrames Project binding cannot be encoded"))?;
    Ok(sha256(&bytes))
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = service.project(project_id).map_err(storage_error)?;
    if project.id != project_id || RevisionStamp::from(&project) != *expected {
        return Err(stale(
            "HyperFrames source belongs to a different current Project stamp",
        ));
    }
    project
        .validate()
        .map_err(|_| invalid("HyperFrames source Project is invalid"))?;
    Ok(project)
}

fn source_bindings<'a>(
    project: &'a Project,
    selection: &HyperframesSourceSelection,
) -> NativeResult<(&'a NativeCapsule, &'a Asset)> {
    if selection.scene_id.is_nil()
        || selection.capsule_id.is_nil()
        || selection.deliverable_id.is_nil()
    {
        return Err(invalid(
            "HyperFrames source selection contains an empty identity",
        ));
    }
    if !project
        .scenes
        .iter()
        .any(|scene| scene.id == selection.scene_id)
        || !project
            .deliverables
            .iter()
            .any(|profile| profile.id == selection.deliverable_id)
    {
        return Err(invalid("HyperFrames source scene or deliverable is absent"));
    }
    let capsule = project
        .production_design
        .capsules
        .iter()
        .find(|capsule| {
            capsule.id == selection.capsule_id && capsule.scene_id == selection.scene_id
        })
        .ok_or_else(|| {
            invalid("HyperFrames source capsule is not attached to the selected scene")
        })?;
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .ok_or_else(|| invalid("HyperFrames source Asset is absent"))?;
    if asset.media_type != HYPERFRAMES_SOURCE_MIME
        || asset.content_sha256.as_deref() != Some(capsule.source_sha256.as_str())
    {
        return Err(invalid(
            "HyperFrames capsule and immutable source Asset do not agree",
        ));
    }
    require_sha256(&capsule.source_sha256).map_err(profile_error)?;
    Ok((capsule, asset))
}

fn read_asset_source(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
) -> NativeResult<AdmittedSource> {
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .ok_or_else(|| invalid("HyperFrames immutable source Asset is absent"))?;
    if asset.media_type != HYPERFRAMES_SOURCE_MIME {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source Asset media type is unsupported",
        ));
    }
    let digest = asset
        .content_sha256
        .as_deref()
        .ok_or_else(|| invalid("HyperFrames source Asset has no immutable digest"))?;
    require_sha256(digest).map_err(profile_error)?;
    let handle = service.store();
    let store = handle.lock();
    if store.load_project(project.id).map_err(storage_error)? != *project {
        return Err(stale(
            "Project changed before HyperFrames source CAS observation",
        ));
    }
    // Reuse the single application CAS API. Its bounded read and filesystem
    // admission remain storage responsibilities, not a second reader here.
    let bytes = store
        .read_blob(digest, MAX_CARRIER_BYTES as u64)
        .map_err(storage_error)?;
    admit_source(&bytes, digest).map_err(profile_error)
}

fn read_selected_source(
    service: &StudioService,
    project: &Project,
    selection: &HyperframesSourceSelection,
) -> NativeResult<AdmittedSource> {
    let (_, asset) = source_bindings(project, selection)?;
    read_asset_source(service, project, asset.id)
}

fn prepare_source_snapshot(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    selection: &HyperframesSourceSelection,
) -> NativeResult<PreparedHyperframesSource> {
    let project = current_project(service, project_id, expected)?;
    let (capsule, asset) = source_bindings(&project, selection)?;
    let source = read_selected_source(service, &project, selection)?;
    let project_snapshot_sha256 = project_digest(&project)?;
    let dependency = json!({
        "schema":"motionwright.hyperframes.canonical-source-dependency/1",
        "project_stamp":expected,"project_snapshot_sha256":project_snapshot_sha256,
        "selection":selection,"asset_id":asset.id,"source_sha256":capsule.source_sha256,
        "source_profile":source.readiness().profile,"producer_revision":source.readiness().producer_revision,
        "html_sha256":source.readiness().html_sha256,"resources":source.readiness().resource_sha256,
    });
    let inspection = HyperframesSourceInspection {
        project_stamp: expected.clone(),
        selection: selection.clone(),
        source_asset_id: asset.id,
        source_sha256: capsule.source_sha256.clone(),
        project_snapshot_sha256,
        canonical_dependency_sha256: sha256(
            &serde_json::to_vec(&dependency)
                .map_err(|_| backend("HyperFrames source dependency cannot be encoded"))?,
        ),
        source: source.readiness().clone(),
        native_execution_performed: false,
        metadata_used_as_authority: false,
        native_render_verified: false,
        canonical_import_applied: false,
        export_authorized: false,
    };
    let prepared = PreparedHyperframesSource {
        project,
        selection: selection.clone(),
        source,
        inspection,
    };
    prepared.revalidate(service)?;
    Ok(prepared)
}

/// Source-only inspection is useful even while a scene uses another renderer.
/// It never treats the attachment as a request to replace that renderer.
pub fn inspect_saved_hyperframes_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    selection: &HyperframesSourceSelection,
) -> NativeResult<HyperframesSourceInspection> {
    Ok(prepare_source_snapshot(service, project_id, expected, selection)?.inspection)
}

fn validate_contribution_context(prepared: &PreparedHyperframesSource) -> NativeResult<()> {
    let scene = prepared
        .project
        .scenes
        .iter()
        .find(|scene| scene.id == prepared.selection.scene_id)
        .expect("source selection checked");
    let profile = prepared
        .project
        .deliverables
        .iter()
        .find(|profile| profile.id == prepared.selection.deliverable_id)
        .expect("source selection checked");
    let document = prepared.source.document();
    if profile.width != document.width
        || profile.height != document.height
        || profile.frame_rate != document.clock.fps
        || scene.duration != document.clock.duration().map_err(profile_error)?
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source clock and geometry must match the exact saved scene and deliverable",
        ));
    }
    if !profile.included_scene_ids.is_empty() && !profile.included_scene_ids.contains(&scene.id) {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source scene is excluded by the saved cut",
        ));
    }
    if profile.video_codec != VideoCodec::H264
        || profile.container != OutputContainer::Mp4
        || profile.color_space != OutputColorSpace::Rec709
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames finite source contribution requires the explicit SDR H.264 MP4 profile",
        ));
    }
    if profile.captions
        || profile.burn_in_captions
        || profile.voice_track_id.is_some()
        || !profile.text_overrides.is_empty()
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames finite source contribution does not realize saved captions, voice selection or text overrides",
        ));
    }
    // The complete Project digest proves that the saved input stayed unchanged;
    // it does not prove that the native source realizes every saved carrier.
    if !scene.beats.is_empty() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize saved scene beats",
        ));
    }
    let scene_end = scene
        .start
        .checked_add(scene.duration)
        .map_err(|_| invalid("HyperFrames saved scene interval overflowed"))?;
    let audio = &prepared.project.audio;
    if audio.active_voice_track_id.is_some()
        || audio.mix != Default::default()
        || audio
            .transcript
            .iter()
            .any(|segment| segment.start < scene_end && segment.end > scene.start)
        || audio
            .cues
            .iter()
            .any(|cue| cue.at >= scene.start && cue.at < scene_end)
        || audio.sfx_cues.iter().any(|cue| cue.scene_id == scene.id)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize selected audio, scene audio timing or mix controls",
        ));
    }
    let design = &prepared.project.production_design;
    if design
        .delivery_recipes
        .get(&profile.id)
        .is_some_and(|recipe| {
            recipe.original_mix.is_some()
                || recipe
                    .scene_intents
                    .iter()
                    .any(|intent| intent.scene_id == scene.id)
        })
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize the saved delivery recipe for this contribution",
        ));
    }
    if design
        .plan
        .as_ref()
        .is_some_and(|plan| plan.shots.iter().any(|shot| shot.scene_id == scene.id))
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize the saved production shot and clock",
        ));
    }
    if design
        .heroes
        .iter()
        .any(|instance| instance.scene_id == scene.id)
        || design
            .procedural_fields
            .iter()
            .any(|instance| instance.scene_id == scene.id)
        || design
            .stages
            .iter()
            .any(|instance| instance.scene_id == scene.id)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize saved scene components or their capture bindings",
        ));
    }
    if design
        .measured_bindings
        .iter()
        .any(|binding| binding.scene_id == scene.id)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize saved measured data bindings",
        ));
    }
    if design
        .camera_reframes
        .iter()
        .any(|reframe| reframe.scene_id == scene.id)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize saved camera reframes",
        ));
    }
    if design
        .capsules
        .iter()
        .any(|capsule| capsule.scene_id == scene.id && capsule.id != prepared.selection.capsule_id)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source requires an unambiguous native capsule for this scene",
        ));
    }
    let language = &prepared.project.visual_language;
    let default_language = motionwright_domain::VisualLanguage::default();
    if language.palette != default_language.palette
        || !language.type_tokens.is_empty()
        || !language.motion_grammar.is_empty()
        || design.brand_profile.is_some()
        || design.taste_profile.is_some()
        || profile.brand_profile.is_some()
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize saved visual language or brand controls",
        ));
    }
    if profile.framing_strategy != Default::default() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames source cannot realize the saved crop strategy",
        ));
    }
    if !scene.nodes.is_empty() || scene.camera != Default::default() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames primary source cannot silently replace saved canvas nodes or camera controls",
        ));
    }
    // Resource syntax and a capsule's declarations do not establish the
    // canonical image/font usage binding or native glyph/image realization.
    // Source inspection/reopen remains available without granting a render.
    for layer in &document.layers {
        match &layer.content {
            motionwright_hyperframes_profile::LayerContent::Rectangle { .. } => {}
            motionwright_hyperframes_profile::LayerContent::Text { .. } => {
                return Err(Error::new(
                    ErrorCode::Unsupported,
                    "HyperFrames text requires canonical font rights and glyph admission before render",
                ));
            }
            motionwright_hyperframes_profile::LayerContent::Image { .. } => {
                return Err(Error::new(
                    ErrorCode::Unsupported,
                    "HyperFrames images require canonical resource rights and image admission before render",
                ));
            }
        }
    }
    // Unreferenced media/data/capture catalog entries and review declarations
    // remain observations. They neither add visible output nor grant rights.
    Ok(())
}

/// Prepare retained native source against exact saved timing/geometry. This is
/// a source observation, not provider admission or a final movie delivery.
pub fn prepare_saved_hyperframes_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    selection: &HyperframesSourceSelection,
) -> NativeResult<PreparedHyperframesSource> {
    let prepared = prepare_source_snapshot(service, project_id, expected, selection)?;
    validate_contribution_context(&prepared)?;
    prepared.revalidate(service)?;
    Ok(prepared)
}

/// A2 owns the canonical enum/migration. This exhaustive match deliberately
/// forces that integration to revisit this gate; Remotion is not an alias.
pub fn require_canonical_hyperframes_renderer(renderer: &RendererKind) -> NativeResult<()> {
    match renderer {
        RendererKind::MotionCanvas
        | RendererKind::Mlt
        | RendererKind::Blender
        | RendererKind::ManimCommunity
        | RendererKind::Remotion
        | RendererKind::ManimGl => Err(Error::new(
            ErrorCode::Unsupported,
            HYPERFRAMES_CANONICAL_SELECTION_PENDING,
        )),
    }
}

impl ProductionCoordinator {
    /// Read-only preflight before asking for an effect grant or staging source.
    /// The current canonical contract closes this entry before every effect.
    pub fn preflight_native_hyperframes_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        selection: &HyperframesSourceSelection,
    ) -> NativeResult<PreparedHyperframesSource> {
        let prepared =
            prepare_saved_hyperframes_source(&self.service, project_id, expected, selection)?;
        let renderer = &prepared
            .project
            .scenes
            .iter()
            .find(|scene| scene.id == selection.scene_id)
            .expect("source selection checked")
            .renderer;
        require_canonical_hyperframes_renderer(renderer)?;
        prepared.revalidate(&self.service)?;
        Ok(prepared)
    }
}

/// Backend-only request construction. Owner profile digest and operation UUID
/// identify an already selected installation/attempt; they do not grant it. No
/// caller plans or paths are accepted. B250 returns Unsupported before issuing
/// this DTO because its canonical renderer selection is not integrated yet.
pub fn prepare_native_hyperframes_request(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    selection: &HyperframesSourceSelection,
    operation_id: Uuid,
    owner_profile_sha256: &str,
) -> NativeResult<Value> {
    if operation_id.is_nil() {
        return Err(invalid("HyperFrames operation identity is empty"));
    }
    require_sha256(owner_profile_sha256).map_err(profile_error)?;
    let prepared = prepare_saved_hyperframes_source(service, project_id, expected, selection)?;
    let renderer = &prepared
        .project
        .scenes
        .iter()
        .find(|scene| scene.id == selection.scene_id)
        .expect("source selection checked")
        .renderer;
    require_canonical_hyperframes_renderer(renderer)?;
    if prepared.project.revision > (1u64 << 53) - 1 {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "HyperFrames revision exceeds exact Producer JSON integers",
        ));
    }
    prepared.revalidate(service)?;
    Ok(json!({
        "schema":"motionwright.hyperframes.source-render/1",
        "project_id":project_id,"generation":expected.generation,"revision":expected.revision,
        "scene_id":selection.scene_id,"capsule_id":selection.capsule_id,"operation_id":operation_id,
        "source_sha256":prepared.inspection.source_sha256,
        "canonical_dependency_sha256":prepared.inspection.canonical_dependency_sha256,
        "owner_profile_sha256":owner_profile_sha256,
    }))
}

/// Inspect a distinct Asset containing an exported/reopened source. Changed
/// bytes remain an edit proposal; this never writes the capsule or applies a
/// Project change. A native rerender and the original journal remain separate.
pub fn preview_saved_hyperframes_source_reopen(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    selection: &HyperframesSourceSelection,
    reopened_asset_id: Uuid,
) -> NativeResult<HyperframesSourceReopenInspection> {
    let original = prepare_source_snapshot(service, project_id, expected, selection)?;
    if reopened_asset_id == original.inspection.source_asset_id {
        return Err(invalid(
            "HyperFrames reopen needs a distinct imported source Asset",
        ));
    }
    let reopened = read_asset_source(service, &original.project, reopened_asset_id)?;
    original.revalidate(service)?;
    let repeated = read_asset_source(service, &original.project, reopened_asset_id)?;
    if reopened.export_bytes() != repeated.export_bytes() {
        return Err(stale(
            "Reopened HyperFrames source changed during inspection",
        ));
    }
    original.verify_project(&current_project(service, project_id, expected)?)?;
    Ok(HyperframesSourceReopenInspection {
        project_stamp: expected.clone(),
        selection: selection.clone(),
        original_asset_id: original.inspection.source_asset_id,
        reopened_asset_id,
        original_sha256: original.inspection.source_sha256,
        reopened_sha256: reopened.readiness().carrier_sha256.clone(),
        canonical_dependency_sha256: original.inspection.canonical_dependency_sha256,
        exact_carrier_bytes_reopened: original.source.export_bytes() == reopened.export_bytes(),
        document_unchanged: original.source.document() == reopened.document(),
        native_html_unchanged: original.source.html() == reopened.html(),
        resource_bytes_unchanged: original.source.resources() == reopened.resources(),
        finite_source_contract_verified: true,
        evidence_scope: "CAS_SOURCE_REOPEN_AND_FINITE_GRAMMAR_ONLY".into(),
        canonical_import_applied: false,
        native_rerender_verified: false,
        imported_receipts_used_as_authority: false,
        export_authorized: false,
    })
}

#[cfg(test)]
mod tests_hyperframes_source_dependency {
    use super::*;

    #[test]
    fn full_project_dependency_changes_for_intent_even_without_a_stamp_change() {
        let original = Project::new("Original source intent").unwrap();
        let mut changed = original.clone();
        changed.title = "Different source intent".into();
        changed.validate().unwrap();
        assert_eq!(
            RevisionStamp::from(&original),
            RevisionStamp::from(&changed)
        );
        assert_ne!(
            project_digest(&original).unwrap(),
            project_digest(&changed).unwrap()
        );
    }
}
