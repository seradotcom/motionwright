//! Source carriers are data. Native execution still uses ProductionCoordinator,
//! the current saved Project, and the existing owner-granted Semwright Host.

use super::*;
use crate::multi_renderer::build_manim_plan_v2;
use motionwright_domain::{Asset, CanvasTransform, Change, DomainError, Project};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2, MAX_SOURCE_ENTRY_BYTES_V2, ManimNativeSourceReadbackV1,
    ManimSceneBindingV2, ManimScenePlanV2, ManimSourceClosureV1, ManimSourcePackageV2,
    ManimStartPlan, SOURCE_BUNDLE_MIME_V2, inspect_native_source_readback_v2,
    inspect_source_bundle_v2, rebind_source_v2, source_bundle_v2,
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimSourceSelectionV2 {
    pub scene_id: Uuid,
    pub deliverable_id: Uuid,
    /// An attached immutable carrier to reopen, not a filesystem input or grant.
    pub source_asset_id: Option<Uuid>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ManimSourceInspectionV2 {
    pub carrier_asset_id: Uuid,
    pub carrier_sha256: String,
    pub origin_binding: ManimSceneBindingV2,
    pub current_binding: ManimSceneBindingV2,
    pub prepared_source: ManimSourceClosureV1,
    pub prepared_bundle_sha256: String,
    pub native_execution_performed: bool,
    pub imported_receipts_used_as_authority: bool,
}

/// A reviewable result, never an executable authorization token. Application of
/// an edit re-reads both CAS assets and computes this delta again under the stamp.
#[derive(Debug, Clone, Serialize)]
pub struct ManimSourceImportPreviewV2 {
    pub project_stamp: RevisionStamp,
    pub origin_asset_id: Uuid,
    pub edited_asset_id: Uuid,
    pub origin_carrier_sha256: String,
    pub edited_carrier_sha256: String,
    pub scene_id: Uuid,
    pub deliverable_id: Uuid,
    pub node_id: Uuid,
    pub change: Change,
    pub losses: Vec<String>,
    pub canonical_import_applied: bool,
    pub native_execution_performed: bool,
}

fn read_source_bytes(path: &Path, maximum: usize) -> NativeResult<Vec<u8>> {
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|_| backend("Manim source artifact cannot be opened"))?
        .take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| backend("Manim source artifact cannot be read"))?;
    if bytes.is_empty() || bytes.len() > maximum {
        return Err(backend("Manim source artifact exceeds its byte budget"));
    }
    Ok(bytes)
}

fn profile_error(error: motionwright_manim_profile::ManimProfileError) -> Error {
    invalid(error.to_string())
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = service.project(project_id).map_err(storage_error)?;
    if project.id != project_id || RevisionStamp::from(&project) != *expected {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Manim source belongs to a different current Project stamp",
        ));
    }
    project
        .validate()
        .map_err(|error| invalid(error.to_string()))?;
    Ok(project)
}

fn read_asset_source(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
    require_capsule: bool,
) -> NativeResult<(Asset, ManimSourcePackageV2)> {
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .ok_or_else(|| invalid("Manim source Asset is absent from the current Project"))?
        .clone();
    let digest = asset
        .content_sha256
        .as_deref()
        .ok_or_else(|| invalid("Manim source Asset has no immutable content digest"))?;
    if asset.media_type != SOURCE_BUNDLE_MIME_V2 {
        return Err(invalid("Manim source Asset has an unsupported media type"));
    }
    let store_handle = service.store();
    let store = store_handle.lock();
    let current = store.load_project(project.id).map_err(storage_error)?;
    if current != *project {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Project changed before Manim source CAS observation",
        ));
    }
    let bytes = store
        .read_blob(digest, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)
        .map_err(storage_error)?;
    let package = inspect_source_bundle_v2(&bytes).map_err(profile_error)?;
    if package.plan.binding.project_id != project.id {
        return Err(invalid(
            "Manim source carrier belongs to another Project identity",
        ));
    }
    if require_capsule
        && !project.production_design.capsules.iter().any(|capsule| {
            capsule.scene_id == package.plan.binding.scene_id
                && capsule.source_asset_id == asset_id
                && capsule.source_sha256 == digest
        })
    {
        return Err(invalid(
            "Manim source carrier is not associated with its scene by a current NativeCapsule",
        ));
    }
    Ok((asset, package))
}

pub fn inspect_saved_manim_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    asset_id: Uuid,
) -> NativeResult<ManimSourceInspectionV2> {
    let project = current_project(service, project_id, expected)?;
    let (asset, origin) = read_asset_source(service, &project, asset_id, true)?;
    let current = build_manim_plan_v2(
        &project,
        origin.plan.binding.scene_id,
        origin.plan.binding.deliverable_id,
    )?;
    let prepared = rebind_source_v2(&origin.plan, &current).map_err(profile_error)?;
    let bundle = source_bundle_v2(&current).map_err(profile_error)?;
    current_project(service, project_id, expected)?;
    Ok(ManimSourceInspectionV2 {
        carrier_asset_id: asset.id,
        carrier_sha256: asset.content_sha256.expect("verified source digest"),
        origin_binding: origin.plan.binding,
        current_binding: current.binding,
        prepared_source: prepared.closure,
        prepared_bundle_sha256: hex::encode(Sha256::digest(bundle)),
        native_execution_performed: false,
        imported_receipts_used_as_authority: false,
    })
}

pub fn preview_saved_manim_source_transform(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    origin_asset_id: Uuid,
    edited_asset_id: Uuid,
) -> NativeResult<ManimSourceImportPreviewV2> {
    if origin_asset_id == edited_asset_id {
        return Err(invalid(
            "Manim source edit needs a distinct immutable carrier",
        ));
    }
    let project = current_project(service, project_id, expected)?;
    let (origin_asset, origin) = read_asset_source(service, &project, origin_asset_id, true)?;
    let (edited_asset, edited) = read_asset_source(service, &project, edited_asset_id, false)?;
    let current = build_manim_plan_v2(
        &project,
        origin.plan.binding.scene_id,
        origin.plan.binding.deliverable_id,
    )?;
    rebind_source_v2(&origin.plan, &current).map_err(profile_error)?;
    if edited.plan.binding != origin.plan.binding && edited.plan.binding != current.binding {
        return Err(invalid(
            "Edited Manim source must preserve either its exact original or current binding",
        ));
    }
    let mut proposed = edited.plan.clone();
    proposed.binding = current.binding.clone();
    if proposed.nodes.len() != current.nodes.len() {
        return Err(invalid(
            "Manim transform import cannot add or remove source nodes",
        ));
    }
    let mut changed = Vec::new();
    for (index, (before, after)) in current.nodes.iter().zip(&proposed.nodes).enumerate() {
        let mut expected_after = before.clone();
        expected_after.x_px = after.x_px;
        expected_after.y_px = after.y_px;
        expected_after.width_px = after.width_px;
        expected_after.height_px = after.height_px;
        if expected_after != *after {
            return Err(invalid(
                "Manim transform import contains an unsupported identity, kind, color, order, or metadata change",
            ));
        }
        if before != after {
            changed.push(index);
        }
    }
    if changed.len() != 1 {
        return Err(invalid(
            "Manim transform import requires exactly one explicit node edit",
        ));
    }
    let index = changed[0];
    let mut expected_after = current.clone();
    expected_after.nodes[index] = proposed.nodes[index].clone();
    if proposed != expected_after {
        return Err(invalid(
            "Manim transform import changed the source clock, matte, or profile",
        ));
    }
    let node = &proposed.nodes[index];
    let change = Change::TransformCanvasNode {
        scene_id: current.binding.scene_id,
        node_id: node.node_id,
        transform: CanvasTransform {
            x: node.x_px,
            y: node.y_px,
            width: node.width_px,
            height: node.height_px,
            rotation_deg: 0.0,
            opacity: 1.0,
        },
    };
    let mut candidate = project.clone();
    candidate
        .apply_change(&change)
        .map_err(|error| match error {
            DomainError::Locked(_) => Error::new(
                ErrorCode::PermissionDenied,
                "The canonical Project or node locks deny this Manim source edit",
            ),
            other => invalid(other.to_string()),
        })?;
    current_project(service, project_id, expected)?;
    Ok(ManimSourceImportPreviewV2 {
        project_stamp: expected.clone(),
        origin_asset_id,
        edited_asset_id,
        origin_carrier_sha256: origin_asset.content_sha256.expect("verified digest"),
        edited_carrier_sha256: edited_asset.content_sha256.expect("verified digest"),
        scene_id: current.binding.scene_id,
        deliverable_id: current.binding.deliverable_id,
        node_id: node.node_id,
        change,
        losses: vec![
            "Only one planar position/size edit is imported by this profile".into(),
            "Generated source code edits are not imported".into(),
            "The NativeCapsule association remains an explicit separate canonical change".into(),
        ],
        canonical_import_applied: false,
        native_execution_performed: false,
    })
}

pub fn apply_saved_manim_source_transform(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request_id: &str,
    origin_asset_id: Uuid,
    edited_asset_id: Uuid,
) -> NativeResult<Project> {
    let preview = preview_saved_manim_source_transform(
        service,
        project_id,
        expected,
        origin_asset_id,
        edited_asset_id,
    )?;
    // One existing atomic canonical Change. No native process or capsule/source
    // rewrite is hidden in this operation; all previous source assets survive.
    let outcome = service
        .apply(project_id, expected, request_id, &preview.change)
        .map_err(storage_error)?;
    Ok(outcome.project)
}

impl ProductionCoordinator {
    pub async fn realize_manim_scene_v2(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        selection: &ManimSourceSelectionV2,
    ) -> NativeResult<Value> {
        let project = current_project(&self.service, project_id, expected)?;
        let plan = build_manim_plan_v2(&project, selection.scene_id, selection.deliverable_id)?;
        let origin = if let Some(asset_id) = selection.source_asset_id {
            let inspection =
                inspect_saved_manim_source(&self.service, project_id, expected, asset_id)?;
            if inspection.current_binding != plan.binding {
                return Err(invalid(
                    "Selected Manim carrier is not bound to this scene and deliverable",
                ));
            }
            Some(inspection)
        } else {
            None
        };
        let profile = plan.render_profile;
        let mut realized = self
            .realize_manim_plan(
                project_id,
                expected,
                request_id,
                selection.scene_id,
                ManimStartPlan::V2(Box::new(plan)),
                profile,
            )
            .await?;
        realized["source_carrier_origin"] = serde_json::to_value(origin)
            .map_err(|_| backend("Manim source origin cannot be encoded"))?;
        Ok(realized)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct SourceArtifact {
    relative_path: String,
    sha256: String,
    bytes: u64,
    media_type: String,
}

pub(super) struct ManimSourceJobBinding {
    expected: ManimSourcePackageV2,
    closure: Value,
    artifact: SourceArtifact,
    source_file: String,
}

#[derive(Debug, Serialize)]
pub(super) struct ManimNativeSourceObservationV2 {
    source_plan: ManimScenePlanV2,
    source_closure: ManimSourceClosureV1,
    source_artifact: SourceArtifact,
    native_readback_artifact: SourceArtifact,
    native_readback: ManimNativeSourceReadbackV1,
    native_getter_properties_verified: bool,
    decoded_media_fidelity_verified: bool,
    native_edit_import_applied: bool,
    audio_and_captions_included: bool,
}

impl ManimSourceJobBinding {
    pub(super) fn from_start(
        expected: &ManimSourcePackageV2,
        started: &Value,
    ) -> NativeResult<Self> {
        let data = response_data(started).map_err(unconfirmed_started_manim_render)?;
        let job_ref = required_string(data, "/job_ref", "Manim source start has no original job")
            .map_err(unconfirmed_started_manim_render)?;
        let token = job_ref
            .strip_prefix("manim-")
            .filter(|part| {
                part.len() == 32
                    && part
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
            .ok_or_else(|| {
                unconfirmed_started_manim_render(backend("Manim source start job is malformed"))
            })?;
        let bytes = source_bundle_v2(&expected.plan).map_err(profile_error)?;
        let artifact = SourceArtifact {
            relative_path: format!("mw_{token}.mwmsrc.zip"),
            sha256: hex::encode(Sha256::digest(&bytes)),
            bytes: bytes.len() as u64,
            media_type: SOURCE_BUNDLE_MIME_V2.into(),
        };
        let binding = Self {
            expected: expected.clone(),
            closure: serde_json::to_value(&expected.closure)
                .map_err(|_| backend("Manim source closure cannot be encoded"))?,
            artifact,
            source_file: format!("mw_{token}.py"),
        };
        binding.observe(data)?;
        if data
            .get("native_source_readback")
            .is_some_and(|value| !value.is_null())
        {
            return Err(unconfirmed_started_manim_render(backend(
                "Manim source start cannot already claim native getter readback",
            )));
        }
        Ok(binding)
    }

    pub(super) fn observe(&self, data: &Value) -> NativeResult<()> {
        if data.get("source_file").and_then(Value::as_str) != Some(self.source_file.as_str())
            || data.get("source_sha256").and_then(Value::as_str)
                != Some(self.expected.closure.source_sha256.as_str())
            || data.get("source_closure") != Some(&self.closure)
            || data.get("source_artifact") != Some(&json!(self.artifact))
        {
            return Err(unconfirmed_started_manim_render(backend(
                "Manim observation lost the source closure fixed at its original start",
            )));
        }
        Ok(())
    }

    pub(super) fn finish(
        &self,
        output_root: &Path,
        data: &Value,
    ) -> NativeResult<ManimNativeSourceObservationV2> {
        self.observe(data)?;
        let carrier_path = verify_output_artifact(
            output_root,
            &self.artifact.relative_path,
            &self.artifact.sha256,
            MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
        )?;
        let carrier = read_source_bytes(&carrier_path, MAX_SOURCE_BUNDLE_BYTES_V2)?;
        if carrier.len() as u64 != self.artifact.bytes {
            return Err(backend(
                "Manim source carrier size changed after native result",
            ));
        }
        let source = inspect_source_bundle_v2(&carrier).map_err(profile_error)?;
        if source != self.expected {
            return Err(backend(
                "Manim output source differs from the exact original invocation",
            ));
        }
        let readback: SourceArtifact = serde_json::from_value(
            data.get("native_source_readback")
                .cloned()
                .ok_or_else(|| backend("Manim result has no native getter readback"))?,
        )
        .map_err(|_| backend("Manim native getter readback metadata is malformed"))?;
        let stem = self
            .source_file
            .strip_suffix(".py")
            .expect("source identity checked");
        if readback.relative_path != format!("{stem}.objects.json")
            || readback.media_type != "application/json"
            || readback.bytes == 0
            || readback.bytes > MAX_SOURCE_ENTRY_BYTES_V2 as u64
        {
            return Err(backend(
                "Manim native getter readback belongs to a different job",
            ));
        }
        let path = verify_output_artifact(
            output_root,
            &readback.relative_path,
            &readback.sha256,
            MAX_SOURCE_ENTRY_BYTES_V2 as u64,
        )?;
        let bytes = read_source_bytes(&path, MAX_SOURCE_ENTRY_BYTES_V2)?;
        if bytes.len() as u64 != readback.bytes {
            return Err(backend("Manim native getter readback size changed"));
        }
        let observed = inspect_native_source_readback_v2(&self.expected.plan, &bytes)
            .map_err(profile_error)?;
        Ok(ManimNativeSourceObservationV2 {
            source_plan: self.expected.plan.clone(),
            source_closure: self.expected.closure.clone(),
            source_artifact: self.artifact.clone(),
            native_readback_artifact: readback,
            native_readback: observed,
            native_getter_properties_verified: true,
            decoded_media_fidelity_verified: false,
            native_edit_import_applied: false,
            audio_and_captions_included: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_manim_profile::{
        ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2, ManimProfileIdV2,
        ManimSourceShapeKindV2, ManimStaticShapeV2,
    };
    use semwright_media_time::Rational;

    fn source() -> ManimSourcePackageV2 {
        let plan = ManimScenePlanV2 {
            schema_version: 2,
            profile_id: ManimProfileIdV2::StaticShapes,
            output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
            binding: ManimSceneBindingV2 {
                project_id: Uuid::from_u128(1),
                generation: Uuid::from_u128(2),
                revision: 7,
                scene_id: Uuid::from_u128(3),
                deliverable_id: Uuid::from_u128(4),
            },
            clock: ManimFrameClockV2::from_canonical(
                Rational::ZERO,
                Rational::new(2, 1).unwrap(),
                Rational::new(12, 1).unwrap(),
            )
            .unwrap(),
            render_profile: ManimRenderProfile {
                width: 640,
                height: 360,
                frame_rate: 12,
            },
            canvas: ManimCanvasV2::default(),
            background: ManimMatteV2::default(),
            nodes: vec![ManimStaticShapeV2 {
                node_id: Uuid::from_u128(5),
                display_name: "Source identity fixture".into(),
                source_kind: ManimSourceShapeKindV2::Rectangle,
                canonical_ordinal: 0,
                z_index: 10,
                x_px: 240.0,
                y_px: 180.0,
                width_px: 600.0,
                height_px: 360.0,
                fill_hex: "#E02040".into(),
            }],
        };
        motionwright_manim_profile::source_package_v2(&plan).unwrap()
    }

    fn start(package: &ManimSourcePackageV2) -> Value {
        let bundle = source_bundle_v2(&package.plan).unwrap();
        json!({"result":{"data":{
            "job_ref":"manim-0123456789abcdef0123456789abcdef","state":"rendering",
            "source_file":"mw_0123456789abcdef0123456789abcdef.py",
            "source_sha256":package.closure.source_sha256,"source_closure":package.closure,
            "source_artifact":{"relative_path":"mw_0123456789abcdef0123456789abcdef.mwmsrc.zip",
                "sha256":hex::encode(Sha256::digest(&bundle)),"bytes":bundle.len(),"media_type":SOURCE_BUNDLE_MIME_V2},
            "native_source_readback":null
        }}})
    }

    #[test]
    fn source_closure_is_fixed_at_start_and_missing_or_foreign_status_stays_uncertain() {
        let package = source();
        let original = start(&package);
        let binding = ManimSourceJobBinding::from_start(&package, &original).unwrap();
        binding.observe(response_data(&original).unwrap()).unwrap();
        for pointer in [
            "/source_file",
            "/source_sha256",
            "/source_closure/plan_sha256",
            "/source_closure/manifest_sha256",
            "/source_closure/source_sha256",
            "/source_closure/entries/0/sha256",
            "/source_artifact/sha256",
            "/source_artifact/media_type",
            "/source_artifact/relative_path",
        ] {
            let mut data = response_data(&original).unwrap().clone();
            *data.pointer_mut(pointer).unwrap() = json!("changed");
            let error = binding.observe(&data).unwrap_err();
            assert!(!error.outcome_known);
            assert_eq!(error.code, ErrorCode::BackendFailed);
        }
        let mut missing = response_data(&original).unwrap().clone();
        missing.as_object_mut().unwrap().remove("source_closure");
        assert!(!binding.observe(&missing).unwrap_err().outcome_known);
        let mut bad = original.clone();
        bad["result"]["data"]["source_artifact"]["bytes"] = json!(1);
        assert!(ManimSourceJobBinding::from_start(&package, &bad).is_err());
        assert_eq!(original, start(&package));
    }

    #[test]
    fn source_start_cannot_claim_native_getters_before_original_execution() {
        let package = source();
        let mut view = start(&package);
        view["result"]["data"]["native_source_readback"] = json!({"verified":true});
        let result = ManimSourceJobBinding::from_start(&package, &view);
        assert!(result.is_err());
        assert!(!result.err().unwrap().outcome_known);
    }
}
