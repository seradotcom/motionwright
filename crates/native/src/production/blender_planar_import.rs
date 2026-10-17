//! Prepare a canonical planar edit from the original, reopened Collection source.
//!
//! This is a read-only consumer of the existing coordinator and original journal.
//! It never dispatches a native command or applies a Project change. The returned
//! change needs independent source readback and the existing A3 canonical edit
//! transaction/grant before an import may be reported as completed.

use super::blender_editable::{
    BlenderEditableEvidence, BlenderEditablePhase, BlenderEditableTransform,
    prepare_blender_editable_project_source, verify_blender_editable_stored_evidence_custody,
};
use super::*;
use crate::multi_renderer::BlenderPrimitiveContribution;
use motionwright_domain::{CanvasNode, CanvasTransform, Change, DomainError, Project};

const PLANAR_IMPORT_SCHEMA: &str = "motionwright-blender-planar-edit-preview/1";
const COLLECTION_BYTES: u64 = 32 * 1024 * 1024;
const INVERSE_TOLERANCE: f64 = 1.0e-10;

/// Backend-produced review data. There is deliberately no Deserialize or public
/// field constructor that could turn an imported JSON document into authority.
#[derive(Debug, Clone, Serialize)]
pub struct BlenderPlanarEditImportPreview {
    schema: &'static str,
    project_id: Uuid,
    expected_stamp: RevisionStamp,
    project_snapshot_sha256: String,
    scene_id: Uuid,
    node_id: Uuid,
    baseline_source_sha256: String,
    edited_source_sha256: String,
    edited_artifact_relative_path: String,
    edited_artifact_sha256: String,
    edited_artifact_bytes: u64,
    source_fingerprint: String,
    reopened_native_session: String,
    original_request_ids: Vec<String>,
    canonical_change: Change,
    reprojected_transform: BlenderEditableTransform,
    native_transform: BlenderEditableTransform,
    projection_error_max_abs: f64,
    independent_file_readback_verified: bool,
    canonical_edit_imported: bool,
    limitations: Vec<&'static str>,
}

impl BlenderPlanarEditImportPreview {
    pub fn change_for_review(&self) -> &Change {
        &self.canonical_change
    }

    pub fn expected_stamp(&self) -> &RevisionStamp {
        &self.expected_stamp
    }

    pub fn project_snapshot_sha256(&self) -> &str {
        &self.project_snapshot_sha256
    }
}

fn same_stamp(project: &Project, expected: &RevisionStamp) -> bool {
    RevisionStamp::from(project) == *expected
}

fn project_snapshot_sha256(project: &Project) -> NativeResult<String> {
    let data = serde_json::to_vec(project)
        .map_err(|_| invalid("Blender import Project snapshot could not be serialized"))?;
    Ok(format!("{:x}", Sha256::digest(data)))
}

fn planar_projection_error(
    actual: &BlenderEditableTransform,
    expected: &BlenderEditableTransform,
) -> NativeResult<f64> {
    let mut maximum = 0.0_f64;
    for (a, b) in actual
        .translation
        .iter()
        .chain(&actual.rotation)
        .chain(&actual.scale)
        .zip(
            expected
                .translation
                .iter()
                .chain(&expected.rotation)
                .chain(&expected.scale),
        )
    {
        if !a.is_finite() || !b.is_finite() {
            return Err(invalid("Blender planar transform contains non-finite data"));
        }
        let error = (a - b).abs();
        if error > INVERSE_TOLERANCE * a.abs().max(b.abs()).max(1.0) {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Blender native edit cannot round-trip through the planar Canvas transform",
            ));
        }
        maximum = maximum.max(error);
    }
    Ok(maximum)
}

/// Pure inversion of the already-existing project-pixel projection. It accepts
/// only its representable subspace; depth, reflection and optical 3D edits are
/// not silently flattened. It grants no authority over an artifact or Project.
fn planar_canvas_transform(
    node: &CanvasNode,
    original: &BlenderPrimitiveContribution,
    edited: &BlenderEditableTransform,
) -> NativeResult<CanvasTransform> {
    if node.id != original.node_id {
        return Err(invalid(
            "Blender planar edit identity differs from the canonical node",
        ));
    }
    if edited
        .translation
        .iter()
        .chain(&edited.rotation)
        .chain(&edited.scale)
        .any(|value| !value.is_finite() || value.abs() > 1_000_000.0)
    {
        return Err(invalid(
            "Blender planar edit exceeds the finite Collection bounds",
        ));
    }
    if edited.rotation[0] != 0.0
        || edited.rotation[1] != 0.0
        || edited.translation[2] != original.location[2]
        || edited.scale[2] != original.scale[2]
        || edited.scale[0] < 0.0001
        || edited.scale[1] < 0.0001
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Blender planar import cannot preserve 3D rotation, depth, Z order, reflection or degenerate scale",
        ));
    }
    // Keep untouched canonical values byte-for-byte rather than round-trip
    // them unnecessarily through floating point radians or center coordinates.
    let width = if edited.scale[0] == original.scale[0] {
        node.width
    } else {
        edited.scale[0] / 0.005
    };
    let height = if edited.scale[1] == original.scale[1] {
        node.height
    } else {
        edited.scale[1] / 0.005
    };
    let x = if edited.translation[0] == original.location[0] && width == node.width {
        node.x
    } else {
        edited.translation[0] / 0.01 + 960.0 - width / 2.0
    };
    let y = if edited.translation[1] == original.location[1] && height == node.height {
        node.y
    } else {
        540.0 - edited.translation[1] / 0.01 - height / 2.0
    };
    let rotation_deg = if edited.rotation[2] == original.rotation[2] {
        node.rotation_deg
    } else {
        -edited.rotation[2].to_degrees()
    };
    let transform = CanvasTransform {
        x,
        y,
        width,
        height,
        rotation_deg,
        opacity: node.opacity,
    };
    transform
        .validate()
        .map_err(|_| invalid("Blender inverse Canvas transform is invalid"))?;
    Ok(transform)
}

fn preview_canonical_change(
    project: &Project,
    scene_id: Uuid,
    node_id: Uuid,
    edited: &BlenderEditableTransform,
) -> NativeResult<(Change, BlenderEditableTransform, f64)> {
    let original = prepare_blender_editable_project_source(project, scene_id)?;
    let node = project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .and_then(|scene| scene.nodes.iter().find(|node| node.id == node_id))
        .ok_or_else(|| invalid("Blender import target is absent from the canonical scene"))?;
    let source_object = original
        .canonical_contribution
        .objects
        .iter()
        .find(|object| object.node_id == node_id)
        .ok_or_else(|| invalid("Blender import target is absent from the admitted projection"))?;
    let transform = planar_canvas_transform(node, source_object, edited)?;
    if transform
        == (CanvasTransform {
            x: node.x,
            y: node.y,
            width: node.width,
            height: node.height,
            rotation_deg: node.rotation_deg,
            opacity: node.opacity,
        })
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Blender import contains no canonical transform change",
        ));
    }
    let change = Change::TransformCanvasNode {
        scene_id,
        node_id,
        transform,
    };
    // Domain validation on a clone preserves canonical scene/property locks.
    // It does not commit a transaction, create an event, or consume a grant.
    let mut candidate = project.clone();
    candidate
        .apply_change(&change)
        .map_err(|error| match error {
            DomainError::Locked(_) => Error::new(
                ErrorCode::PermissionDenied,
                "Blender native edit conflicts with a canonical Project lock",
            ),
            _ => invalid("Blender native edit is rejected by the canonical domain"),
        })?;
    let reprojected = prepare_blender_editable_project_source(&candidate, scene_id)?;
    for before in &original.canonical_contribution.objects {
        let after = reprojected
            .canonical_contribution
            .objects
            .iter()
            .find(|object| object.node_id == before.node_id)
            .ok_or_else(|| backend("Blender import removed an unrelated projected identity"))?;
        if before.node_id != node_id && after != before {
            return Err(backend(
                "Blender planar edit changed another projected source object",
            ));
        }
    }
    let target = reprojected
        .canonical_contribution
        .objects
        .iter()
        .find(|object| object.node_id == node_id)
        .ok_or_else(|| backend("Blender inverse projection lost its target identity"))?;
    let observed = BlenderEditableTransform {
        translation: target.location,
        rotation: target.rotation,
        scale: target.scale,
    };
    let error = planar_projection_error(&observed, edited)?;
    Ok((change, observed, error))
}

impl ProductionCoordinator {
    /// Read-only preparation. A valid result remains a review preview, not an
    /// import grant, independent .blend topology proof or a completed edit.
    pub fn prepare_native_blender_planar_edit_import(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        evidence: &BlenderEditableEvidence,
    ) -> NativeResult<BlenderPlanarEditImportPreview> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if !same_stamp(&project, expected)
            || self.client.connection().resource != project.resource_key()
            || evidence.source.project_resource != project.resource_key()
            || evidence.source.canonical_contribution.project_id != project_id
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender edit import no longer matches the full canonical Project stamp",
            ));
        }
        let scene_id = evidence.source.canonical_contribution.scene_id;
        let current = prepare_blender_editable_project_source(&project, scene_id)?;
        if evidence.source.canonical_contribution != current.canonical_contribution
            || evidence.phase != BlenderEditablePhase::EditedSourceReopened
            || evidence.source.native_edits.len() != 1
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender import requires the original edited source reopened in a fresh process",
            ));
        }
        // This reads all selected original requests from the bounded, stable
        // Store snapshot. The UNKNOWN classification fix is a prerequisite.
        verify_blender_editable_stored_evidence_custody(&self.service, evidence)?;
        let artifact = &evidence.artifact;
        let path = verify_output_artifact(
            &self.client.connection().output_root,
            &artifact.relative_path,
            &artifact.sha256,
            COLLECTION_BYTES,
        )?;
        if safe_metadata(&path)?.len() != artifact.bytes {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender edited artifact length differs from its original persist receipt",
            ));
        }
        let delta = &evidence.source.native_edits[0];
        let (change, reprojected, projection_error_max_abs) =
            preview_canonical_change(&project, scene_id, delta.node_id, &delta.transform)?;
        verify_blender_editable_stored_evidence_custody(&self.service, evidence)?;
        // Re-read rather than trusting only the caller stamp. The prepare path
        // is read-only; the actual canonical transaction must recheck again.
        if self.service.project(project_id).map_err(storage_error)? != project {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Blender import Project changed while its source was inspected",
            ));
        }
        verify_output_artifact(
            &self.client.connection().output_root,
            &artifact.relative_path,
            &artifact.sha256,
            COLLECTION_BYTES,
        )?;
        Ok(BlenderPlanarEditImportPreview {
            schema: PLANAR_IMPORT_SCHEMA,
            project_id,
            expected_stamp: expected.clone(),
            project_snapshot_sha256: project_snapshot_sha256(&project)?,
            scene_id,
            node_id: delta.node_id,
            baseline_source_sha256: current.source_sha256,
            edited_source_sha256: evidence.source.source_sha256.clone(),
            edited_artifact_relative_path: artifact.relative_path.clone(),
            edited_artifact_sha256: artifact.sha256.clone(),
            edited_artifact_bytes: artifact.bytes,
            source_fingerprint: evidence.snapshot.fingerprint.clone(),
            reopened_native_session: evidence.snapshot.native_session.clone(),
            original_request_ids: evidence
                .stages
                .iter()
                .map(|proof| proof.request_id.clone())
                .collect(),
            canonical_change: change,
            reprojected_transform: reprojected,
            native_transform: delta.transform.clone(),
            projection_error_max_abs,
            independent_file_readback_verified: false,
            canonical_edit_imported: false,
            limitations: vec![
                "independent_blend_topology_readback_required",
                "canonical_edit_transaction_and_grant_required",
                "planar_trs_only",
                "source_material_alpha_is_not_raster_fidelity",
                "scene_camera_audio_fonts_and_masks_outside_profile",
                "source_assets_and_external_rights_not_changed_by_this_preview",
            ],
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{CoordinateSpace, LockKind, NodeStyle, ProjectLock, RendererKind};

    fn fixture() -> (Project, Uuid, Uuid) {
        let mut project = Project::new("Planar import source fixture").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Editable scene".into(),
                objective: "Inverse source mapping".into(),
                duration_seconds: 2,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        project
            .apply_change(&Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            })
            .unwrap();
        let node_id = Uuid::parse_str("0198aabb-ccdd-7000-8000-000000000001").unwrap();
        for (id, x) in [
            (node_id, 360.0),
            (
                Uuid::parse_str("0198aabb-ccdd-7000-8000-000000000002").unwrap(),
                1040.0,
            ),
        ] {
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id,
                        name: "Exact UUID identity".into(),
                        kind: "rectangle".into(),
                        parent_id: None,
                        x,
                        y: 280.0,
                        width: 240.0,
                        height: 180.0,
                        rotation_deg: 12.0,
                        opacity: 1.0,
                        text: None,
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#4F7CAC80".into()),
                            ..NodeStyle::default()
                        },
                        relations: vec![],
                        property_locks: Default::default(),
                        keyframes: vec![],
                    },
                })
                .unwrap();
        }
        (project, scene_id, node_id)
    }

    fn native_transform(
        project: &Project,
        scene_id: Uuid,
        node_id: Uuid,
    ) -> BlenderEditableTransform {
        let source = prepare_blender_editable_project_source(project, scene_id).unwrap();
        let object = source
            .canonical_contribution
            .objects
            .iter()
            .find(|object| object.node_id == node_id)
            .unwrap();
        BlenderEditableTransform {
            translation: object.location,
            rotation: object.rotation,
            scale: object.scale,
        }
    }

    #[test]
    fn explicit_native_probe_maps_to_one_canonical_change_without_writing_project() {
        let (project, scene_id, node_id) = fixture();
        let original = project.clone();
        let mut edit = native_transform(&project, scene_id, node_id);
        edit.translation[0] += 0.625;
        edit.rotation[2] += 0.125;
        let (change, reprojection, error) =
            preview_canonical_change(&project, scene_id, node_id, &edit).unwrap();
        let Change::TransformCanvasNode {
            scene_id: actual_scene,
            node_id: actual_node,
            transform,
        } = &change
        else {
            panic!("typed transform expected")
        };
        assert_eq!((*actual_scene, *actual_node), (scene_id, node_id));
        assert_eq!(
            (transform.x, transform.y, transform.width, transform.height),
            (422.5, 280.0, 240.0, 180.0)
        );
        assert!((transform.rotation_deg - (12.0 - 0.125_f64.to_degrees())).abs() < 1e-12);
        assert!(error < 1e-12);
        assert!(planar_projection_error(&reprojection, &edit).is_ok());
        assert_eq!(project, original);
        let mut imported = project.clone();
        imported.apply_change(&change).unwrap();
        assert_eq!(imported.scenes[0].nodes[1], project.scenes[0].nodes[1]);
        assert_eq!(imported.assets, project.assets);
        assert_eq!(
            imported.scenes[0].nodes[0].style,
            project.scenes[0].nodes[0].style
        );
        assert_eq!(imported.scenes[0].nodes[0].id, node_id);
    }

    #[test]
    fn scale_edit_preserves_the_native_center_and_unchanged_rotation() {
        let (project, scene_id, node_id) = fixture();
        let mut edit = native_transform(&project, scene_id, node_id);
        edit.scale[0] *= 1.5;
        edit.scale[1] *= 0.5;
        let (change, _, error) =
            preview_canonical_change(&project, scene_id, node_id, &edit).unwrap();
        let Change::TransformCanvasNode { transform, .. } = change else {
            panic!("typed transform expected")
        };
        assert_eq!(
            (
                transform.x,
                transform.y,
                transform.width,
                transform.height,
                transform.rotation_deg
            ),
            (300.0, 325.0, 360.0, 90.0, 12.0)
        );
        assert!(error < 1e-12);
    }

    #[test]
    fn refuses_unrepresentable_3d_controls_and_nonfinite_or_reflected_edits() {
        let (project, scene_id, node_id) = fixture();
        let valid = native_transform(&project, scene_id, node_id);
        for index in 0..8 {
            let mut edited = valid.clone();
            match index {
                0 => edited.rotation[0] = 0.001,
                1 => edited.rotation[1] = std::f64::consts::TAU,
                2 => edited.translation[2] += 0.01,
                3 => edited.scale[2] *= 2.0,
                4 => edited.scale[0] *= -1.0,
                5 => edited.scale[1] = 0.0,
                6 => edited.translation[0] = f64::NAN,
                _ => edited.rotation[2] = f64::INFINITY,
            }
            assert!(
                preview_canonical_change(&project, scene_id, node_id, &edited).is_err(),
                "case {index}"
            );
        }
        assert!(preview_canonical_change(&project, scene_id, Uuid::nil(), &valid).is_err());
        assert!(preview_canonical_change(&project, scene_id, node_id, &valid).is_err());
    }

    #[test]
    fn canonical_locks_are_checked_on_the_cloned_domain_transaction() {
        let (mut project, scene_id, node_id) = fixture();
        let mut edited = native_transform(&project, scene_id, node_id);
        edited.translation[0] += 0.625;
        project.locks.push(ProjectLock {
            id: Uuid::from_u128(71),
            resource: format!("scene:{scene_id}"),
            kind: LockKind::Position,
            note: "Keep canonical pose".into(),
        });
        let before = project.clone();
        assert_eq!(
            preview_canonical_change(&project, scene_id, node_id, &edited)
                .unwrap_err()
                .code,
            ErrorCode::PermissionDenied
        );
        assert_eq!(project, before);
    }
}
