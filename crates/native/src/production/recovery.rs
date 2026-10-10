//! Revision-aware, receipt-backed selective recovery for the bounded
//! Blender -> Motion Canvas production demonstration.
//!
//! These receipts are *application evidence*, never a parallel scheduler or a
//! permission grant. All actual commands still flow through Semwright Broker.

use super::*;
use crate::film::FilmBuildOptions;
use motionwright_domain::Project;
use serde::de::DeserializeOwned;

const RECOVERY_SCHEMA: &str = "motionwright-cross-app-recovery/1";
const RECOVERY_MANIFEST_LIMIT: u64 = 16 * 1024 * 1024;
const RECOVERY_FRAME_LIMIT: u64 = 24 * 1024 * 1024;
const RECOVERY_MAX_SEGMENTS: usize = 32;
const RECOVERY_MAX_FRAMES: u64 = 18_000;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StageDisposition {
    Executed,
    Reused,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryBlenderProof {
    pub scene_id: Uuid,
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrossAppRecoveryReport {
    pub schema: String,
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub blender: StageDisposition,
    pub blender_proof: RecoveryBlenderProof,
    pub motion_canvas: StageDisposition,
    pub motion: MotionCanvasRenderEvidence,
    pub blender_stage_executions: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MotionProof {
    evidence: MotionCanvasRenderEvidence,
    manifests_sha256: Vec<String>,
}

#[derive(Debug, Clone)]
struct StageKey {
    id: String,
    digest: String,
    command: &'static str,
}

impl StageKey {
    fn for_input(
        flow: &str,
        stage: &str,
        command: &'static str,
        input: &impl Serialize,
    ) -> NativeResult<Self> {
        if flow.is_empty()
            || flow.len() > 24
            || !flow
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(invalid("Recovery flow id must be a bounded ASCII token"));
        }
        let digest = digest_serialized(&(RECOVERY_SCHEMA, stage, input))?;
        Ok(Self {
            // Flow labels are deliberately NOT part of the durable identity:
            // callers cannot bypass an uncertain mutation by renaming a flow.
            id: format!("mw-recovery:{stage}:{digest}"),
            digest,
            command,
        })
    }
}

fn digest_serialized(value: &impl Serialize) -> NativeResult<String> {
    let bytes = serde_json::to_vec(value)
        .map_err(|_| invalid("Recovery source fingerprint could not be encoded"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

fn read_previous<T: DeserializeOwned>(
    coordinator: &ProductionCoordinator,
    project: &Project,
    key: &StageKey,
) -> NativeResult<Option<(T, ProductionReceipt)>> {
    let receipt = coordinator
        .service
        .latest_production_receipt(project.id, &key.id)
        .map_err(storage_error)?;
    let Some(receipt) = receipt else {
        return Ok(None);
    };
    if receipt.generation != project.generation
        || receipt.request_sha256 != key.digest
        || receipt.command != key.command
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Recovery receipt does not match the current source identity",
        ));
    }
    if receipt.stage == "completed" {
        let proof: T = serde_json::from_value(
            receipt
                .payload
                .get("proof")
                .cloned()
                .ok_or_else(|| backend("Completed recovery receipt has no bounded proof"))?,
        )
        .map_err(|_| backend("Completed recovery receipt proof is malformed"))?;
        return Ok(Some((proof, receipt)));
    }
    if receipt.stage == "failed_known"
        && key.command == "motionwright.recovery.motion-canvas"
        && receipt.payload.get("retryable").and_then(Value::as_bool) == Some(true)
    {
        return Ok(None);
    }
    Err(
        if receipt.stage == "outcome_unknown" || receipt.stage == "dispatching" {
            Error::new(
                ErrorCode::Conflict,
                "Recovery outcome is uncertain; reconcile with the native provider before retrying",
            )
            .uncertain()
        } else {
            Error::new(
                ErrorCode::Conflict,
                "Recovery stage requires explicit reconciliation before a new attempt",
            )
        },
    )
}

fn ensure_same_revision(
    coordinator: &ProductionCoordinator,
    id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = coordinator.service.project(id).map_err(storage_error)?;
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Recovery project revision changed",
        ));
    }
    if coordinator.client.connection().resource != project.resource_key() {
        return Err(Error::new(
            ErrorCode::PermissionDenied,
            "Recovery connection belongs to a different project",
        ));
    }
    Ok(project)
}

fn validate_blender_proof(root: &Path, proof: &RecoveryBlenderProof) -> NativeResult<()> {
    if proof.bytes <= 20 || proof.bytes > BLENDER_GLTF_MAX_BYTES {
        return Err(invalid("Recovery Blender artifact has an invalid length"));
    }
    let path = verify_output_artifact(
        root,
        &proof.relative_path,
        &proof.sha256,
        BLENDER_GLTF_MAX_BYTES,
    )?;
    if fs::metadata(&path)
        .map_err(|_| backend("Recovery artifact is missing"))?
        .len()
        != proof.bytes
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Recovery Blender artifact size changed",
        ));
    }
    let mut file =
        fs::File::open(path).map_err(|_| backend("Recovery artifact could not be opened"))?;
    let mut header = [0_u8; 12];
    file.read_exact(&mut header)
        .map_err(|_| backend("Recovery GLB header is incomplete"))?;
    if &header[0..4] != b"glTF"
        || u32::from_le_bytes(header[4..8].try_into().unwrap()) != 2
        || u64::from(u32::from_le_bytes(header[8..12].try_into().unwrap())) != proof.bytes
    {
        return Err(backend("Recovery artifact is not an intact glTF 2 GLB"));
    }
    Ok(())
}

/// Validate the complete native renderer manifest and EVERY frame against its
/// recorded digest. A manifest hash alone does not detect missing PNG frames.
fn validate_motion_proof(
    root: &Path,
    evidence: &MotionCanvasRenderEvidence,
    previous_manifests: Option<&[String]>,
) -> NativeResult<Vec<String>> {
    if evidence.segments.is_empty() || evidence.segments.len() > RECOVERY_MAX_SEGMENTS {
        return Err(invalid("Recovery Motion Canvas segment count is invalid"));
    }
    if previous_manifests.is_some_and(|items| items.len() != evidence.segments.len()) {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Recovery manifest count changed",
        ));
    }
    let mut digests = Vec::with_capacity(evidence.segments.len());
    let mut total_frames = 0_u64;
    for (segment_index, segment) in evidence.segments.iter().enumerate() {
        ensure_native_motion_verification(&segment.verification)?;
        if segment.frame_count == 0 || segment.frame_count > RECOVERY_MAX_FRAMES {
            return Err(invalid("Recovery segment frame count is out of bounds"));
        }
        total_frames = total_frames
            .checked_add(segment.frame_count)
            .ok_or_else(|| invalid("Recovery frame count overflow"))?;
        if total_frames > RECOVERY_MAX_FRAMES {
            return Err(invalid("Recovery frame budget exceeded"));
        }
        if segment.artifact.get("frame_count").and_then(Value::as_u64) != Some(segment.frame_count)
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Recovery native frame count changed",
            ));
        }
        let directory = required_string(
            &segment.artifact,
            "/directory",
            "Recovery Motion Canvas artifact directory is missing",
        )?;
        let manifest_relative = format!("{directory}/artifact-manifest.json");
        let path = output_artifact_path(root, &manifest_relative, RECOVERY_MANIFEST_LIMIT)?;
        let sha = sha256_file(&path)?;
        if let Some(known) = previous_manifests
            && known[segment_index] != sha
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Recovery renderer manifest digest changed",
            ));
        }
        if let Some(provider_digest) = segment
            .artifact
            .get("manifest_sha256")
            .and_then(Value::as_str)
            && provider_digest != sha
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Recovery renderer manifest disagrees with provider",
            ));
        }
        let manifest: Value = serde_json::from_slice(
            &fs::read(path).map_err(|_| backend("Recovery manifest could not be read"))?,
        )
        .map_err(|_| backend("Recovery renderer manifest is malformed"))?;
        if manifest
            .pointer("/plan/frame_count")
            .and_then(Value::as_u64)
            != Some(segment.frame_count)
            || manifest
                .pointer("/plan/first_frame")
                .and_then(Value::as_u64)
                != Some(0)
            || manifest
                .pointer("/plan/end_frame_exclusive")
                .and_then(Value::as_u64)
                != Some(segment.frame_count)
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Recovery renderer plan frame range changed",
            ));
        }
        let frames = manifest
            .get("frames")
            .and_then(Value::as_array)
            .ok_or_else(|| backend("Recovery renderer frame index is missing"))?;
        if frames.len() as u64 != segment.frame_count {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Recovery renderer frame index is incomplete",
            ));
        }
        for (index, frame) in frames.iter().enumerate() {
            let expected = format!("frames/{index:06}.png");
            if frame.get("file").and_then(Value::as_str) != Some(expected.as_str())
                || frame.get("index").and_then(Value::as_u64) != Some(index as u64)
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Recovery renderer frame index changed",
                ));
            }
            let bytes = frame
                .get("bytes")
                .and_then(Value::as_u64)
                .filter(|n| *n > 0 && *n <= RECOVERY_FRAME_LIMIT)
                .ok_or_else(|| backend("Recovery renderer frame size is invalid"))?;
            let sha = frame
                .get("sha256")
                .and_then(Value::as_str)
                .ok_or_else(|| backend("Recovery renderer frame digest is missing"))?;
            let frame_path = verify_output_artifact(
                root,
                &format!("{directory}/{expected}"),
                sha,
                RECOVERY_FRAME_LIMIT,
            )?;
            if fs::metadata(&frame_path)
                .map_err(|_| backend("Recovery frame disappeared"))?
                .len()
                != bytes
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Recovery renderer frame size changed",
                ));
            }
        }
        digests.push(sha);
    }
    Ok(digests)
}

fn stage_input(
    project: &Project,
    connection: &ProductionConnection,
    projection: &impl Serialize,
) -> NativeResult<Value> {
    Ok(json!({
        "schema": RECOVERY_SCHEMA,
        "resource": project.resource_key(),
        "generation": project.generation,
        "connection": connection.identity()?,
        "projection": projection
    }))
}

impl ProductionCoordinator {
    fn claim(&self, project: &Project, key: &StageKey) -> NativeResult<u64> {
        let receipt = self
            .service
            .claim_recovery_stage(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: key.id.clone(),
                request_sha256: key.digest.clone(),
                command: key.command.into(),
                stage: "dispatching".into(),
                payload: json!({"schema": RECOVERY_SCHEMA, "retryable": false}),
            })
            .map_err(storage_error)?;
        receipt
            .payload
            .get("attempt")
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Recovery stage claim lost its attempt number"))
    }

    fn checkpoint(
        &self,
        project: &Project,
        key: &StageKey,
        attempt: u64,
        stage: &str,
        payload: Value,
    ) -> NativeResult<()> {
        let retryable = payload
            .get("retryable")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let proof = payload.get("proof").cloned();
        self.service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: key.id.clone(),
                request_sha256: key.digest.clone(),
                command: key.command.into(),
                stage: stage.into(),
                payload: json!({
                    "schema": RECOVERY_SCHEMA,
                    "attempt": attempt,
                    "data": payload,
                    "retryable": retryable,
                    "proof": proof
                }),
            })
            .map_err(|_| {
                backend("Recovery stage result could not be durably checkpointed").uncertain()
            })?;
        Ok(())
    }

    async fn recover_blender(
        &self,
        project: &Project,
        expected: &RevisionStamp,
        flow: &str,
        scene_id: Uuid,
    ) -> NativeResult<(StageDisposition, RecoveryBlenderProof)> {
        let mut projection = build_blender_contribution(project, scene_id)?;
        // Creative edits to unrelated scenes should not invalidate this
        // contribution. Its own semantic projection and generation are stable.
        projection.revision = 0;
        let key = StageKey::for_input(
            flow,
            "blender",
            "motionwright.recovery.blender",
            &stage_input(project, self.client.connection(), &projection)?,
        )?;
        if let Some((proof, _)) = read_previous::<RecoveryBlenderProof>(self, project, &key)? {
            if proof.scene_id != scene_id {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Recovery Blender scene identity changed",
                ));
            }
            validate_blender_proof(&self.client.connection().output_root, &proof)?;
            return Ok((StageDisposition::Reused, proof));
        }
        let attempt = self.claim(project, &key)?;
        // A Blender mutation can be partially applied. No automatic second
        // attempt is allowed after an interrupted/failed first attempt.
        let driver_request = format!("rc-{}-b{attempt}", &key.digest[..22]);
        let result = self
            .realize_blender_scene(project.id, expected, &driver_request, scene_id)
            .await;
        let result = match result {
            Ok(value) => value,
            Err(error) => {
                self.checkpoint(
                    project,
                    &key,
                    attempt,
                    if error.outcome_known {
                        "failed_known"
                    } else {
                        "outcome_unknown"
                    },
                    json!({"retryable": false}),
                )?;
                return Err(error);
            }
        };
        let proof = (|| -> NativeResult<RecoveryBlenderProof> {
            let artifact = result
                .get("artifact")
                .ok_or_else(|| backend("Recovery Blender render returned no artifact"))?;
            let proof = RecoveryBlenderProof {
                scene_id,
                relative_path: required_string(artifact, "/path", "Recovery Blender path missing")?,
                sha256: required_string(artifact, "/sha256", "Recovery Blender digest missing")?,
                bytes: artifact
                    .get("bytes")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| backend("Recovery Blender size missing"))?,
            };
            validate_blender_proof(&self.client.connection().output_root, &proof)?;
            Ok(proof)
        })();
        match proof {
            Ok(proof) => {
                self.checkpoint(project, &key, attempt, "completed", json!({"proof": proof}))?;
                Ok((StageDisposition::Executed, proof))
            }
            Err(error) => {
                self.checkpoint(
                    project,
                    &key,
                    attempt,
                    "failed_known",
                    json!({"retryable": false}),
                )?;
                Err(error)
            }
        }
    }

    async fn recover_motion(
        &self,
        project: &Project,
        expected: &RevisionStamp,
        flow: &str,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<(StageDisposition, MotionCanvasRenderEvidence)> {
        let segments = build_motion_canvas_segments(project, deliverable_id, options)?;
        let key = StageKey::for_input(
            flow,
            "motion-canvas",
            "motionwright.recovery.motion-canvas",
            &stage_input(
                project,
                self.client.connection(),
                &(project.revision, &segments),
            )?,
        )?;
        if let Some((proof, _)) = read_previous::<MotionProof>(self, project, &key)? {
            if proof.evidence.generation != project.generation
                || proof.evidence.project_resource != project.resource_key()
                || proof.evidence.revision != project.revision
                || proof.evidence.deliverable_id != deliverable_id
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Recovery Motion Canvas source identity changed",
                ));
            }
            validate_motion_proof(
                &self.client.connection().output_root,
                &proof.evidence,
                Some(&proof.manifests_sha256),
            )?;
            return Ok((StageDisposition::Reused, proof.evidence));
        }
        let attempt = self.claim(project, &key)?;
        let driver_request = format!("rc-{}-m{attempt}", &key.digest[..22]);
        let result = self
            .render_motion_canvas_segments(
                project.id,
                expected,
                &driver_request,
                deliverable_id,
                options,
            )
            .await;
        let evidence = match result {
            Ok(value) => value,
            Err(error) => {
                // Only a typed, terminal native render failure can be safely
                // rerendered. A lost status/readback is *not* a failed job.
                let retryable = error.outcome_known
                    && error.code == ErrorCode::BackendFailed
                    && error.message.starts_with("Motion Canvas segment ")
                    && error.message.contains(" render failed (category: ");
                self.checkpoint(
                    project,
                    &key,
                    attempt,
                    if error.outcome_known {
                        "failed_known"
                    } else {
                        "outcome_unknown"
                    },
                    json!({"retryable": retryable}),
                )?;
                return Err(error);
            }
        };
        let validated =
            validate_motion_proof(&self.client.connection().output_root, &evidence, None);
        match validated {
            Ok(manifests_sha256) => {
                let proof = MotionProof {
                    evidence,
                    manifests_sha256,
                };
                self.checkpoint(project, &key, attempt, "completed", json!({"proof": proof}))?;
                Ok((StageDisposition::Executed, proof.evidence))
            }
            Err(error) => {
                self.checkpoint(
                    project,
                    &key,
                    attempt,
                    "failed_known",
                    json!({"retryable": false}),
                )?;
                Err(error)
            }
        }
    }

    /// Owner-only, bounded interruption point for the cross-app demo. Completes
    /// only the Blender native stage; a different process may later run the
    /// complete pipeline. No Motion Canvas request is dispatched here.
    pub async fn checkpoint_blender_for_cross_app(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        flow: &str,
        blender_scene: Uuid,
    ) -> NativeResult<(StageDisposition, RecoveryBlenderProof)> {
        let project = ensure_same_revision(self, project_id, expected)?;
        self.recover_blender(&project, expected, flow, blender_scene)
            .await
    }

    /// Verify/reuse the native Blender artifact across failed Motion Canvas
    /// attempts and process restarts; call real Semwright native providers for
    /// all execution. The GLB is a separate creative contribution, not falsely
    /// represented as a Motion Canvas input or as an assembled video.
    pub async fn recover_blender_motion(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        flow: &str,
        blender_scene: Uuid,
        deliverable: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<CrossAppRecoveryReport> {
        let project = ensure_same_revision(self, project_id, expected)?;
        // Preflight both stages before any expensive mutation.
        build_blender_contribution(&project, blender_scene)?;
        let segments = build_motion_canvas_segments(&project, deliverable, options)?;
        let total_frames: u64 = segments
            .iter()
            .try_fold(0_u64, |total, seg| total.checked_add(seg.frame_count))
            .ok_or_else(|| invalid("Recovery preflight frame count overflow"))?;
        if segments.len() > RECOVERY_MAX_SEGMENTS || total_frames > RECOVERY_MAX_FRAMES {
            return Err(invalid(
                "Recovery preflight exceeds native frame verification budget",
            ));
        }
        let (blender, blender_proof) = self
            .recover_blender(&project, expected, flow, blender_scene)
            .await?;
        let (motion_canvas, motion) = self
            .recover_motion(&project, expected, flow, deliverable, options)
            .await?;
        Ok(CrossAppRecoveryReport {
            schema: RECOVERY_SCHEMA.into(),
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            blender_stage_executions: if matches!(blender, StageDisposition::Reused) {
                0
            } else {
                1
            },
            blender,
            blender_proof,
            motion_canvas,
            motion,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RevisionStamp,
    };
    use motionwright_storage::Store;
    use semwright_media_time::Rate;
    use std::collections::BTreeSet;
    use std::io::Write;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    #[cfg(unix)]
    fn fixture(dir: &tempfile::TempDir) -> (ProductionCoordinator, Project, ProductionConnection) {
        let db = dir.path().join("project.sqlite3");
        let service = StudioService::from_store(Store::open(&db).unwrap());
        let project = service.create_project("Selective recovery").unwrap();
        let executable = dir.path().join("semwright-fake");
        let session_file = dir.path().join("session.json");
        let socket = dir.path().join("broker.sock");
        let output_root = dir.path().join("output");
        fs::write(&executable, b"#!/bin/sh\nexit 1\n").unwrap();
        fs::write(&session_file, b"{}").unwrap();
        fs::write(&socket, b"").unwrap();
        fs::create_dir(&output_root).unwrap();
        let mut perms = fs::metadata(&executable).unwrap().permissions();
        perms.set_mode(0o700);
        fs::set_permissions(&executable, perms).unwrap();
        for path in [&session_file, &socket] {
            let mut perms = fs::metadata(path).unwrap().permissions();
            perms.set_mode(0o600);
            fs::set_permissions(path, perms).unwrap();
        }
        let mut perms = fs::metadata(&output_root).unwrap().permissions();
        perms.set_mode(0o700);
        fs::set_permissions(&output_root, perms).unwrap();
        let connection = ProductionConnection {
            schema: CONNECTION_SCHEMA.into(),
            executable_sha256: sha256_file(&executable).unwrap(),
            executable,
            socket,
            session_file,
            output_root,
            resource: project.resource_key(),
        };
        let coordinator = ProductionCoordinator::new(service, connection.clone()).unwrap();
        (coordinator, project, connection)
    }

    fn fake_glb(path: &Path) -> RecoveryBlenderProof {
        let mut file = fs::File::create(path).unwrap();
        file.write_all(b"glTF").unwrap();
        file.write_all(&2_u32.to_le_bytes()).unwrap();
        file.write_all(&24_u32.to_le_bytes()).unwrap();
        file.write_all(&[0_u8; 12]).unwrap();
        file.flush().unwrap();
        RecoveryBlenderProof {
            scene_id: Uuid::now_v7(),
            relative_path: path.file_name().unwrap().to_str().unwrap().into(),
            sha256: sha256_file(path).unwrap(),
            bytes: 24,
        }
    }

    #[test]
    fn stage_key_does_not_allow_flow_renaming_to_evade_uncertain_mutations() {
        let same = json!({"project": "same", "scene": 1});
        let first = StageKey::for_input(
            "flow-one",
            "blender",
            "motionwright.recovery.blender",
            &same,
        )
        .unwrap();
        let renamed = StageKey::for_input(
            "flow-two",
            "blender",
            "motionwright.recovery.blender",
            &same,
        )
        .unwrap();
        assert_eq!(first.id, renamed.id);
        assert_eq!(first.digest, renamed.digest);
        let edited = StageKey::for_input(
            "flow-one",
            "blender",
            "motionwright.recovery.blender",
            &json!({"scene":2}),
        )
        .unwrap();
        assert_ne!(first.id, edited.id);
        assert!(
            StageKey::for_input("../bad", "blender", "motionwright.recovery.blender", &same,)
                .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn unrelated_scene_edit_preserves_blender_cache_key_but_blender_edit_invalidates_it() {
        let dir = tempfile::tempdir().unwrap();
        let (coordinator, initial, connection) = fixture(&dir);
        let service = &coordinator.service;
        let p = service
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "seed-blender",
                &Change::AddScene {
                    name: "Blender".into(),
                    objective: "Reusable geometry".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let blender = p.scenes[0].id;
        let p = service
            .apply(
                p.id,
                &RevisionStamp::from(&p),
                "seed-blender-kind",
                &Change::SetSceneRenderer {
                    scene_id: blender,
                    renderer: motionwright_domain::RendererKind::Blender,
                },
            )
            .unwrap()
            .project;
        let node = |name: &str| CanvasNode {
            id: Uuid::now_v7(),
            name: name.into(),
            kind: "rectangle".into(),
            parent_id: None,
            x: 160.0,
            y: 220.0,
            width: 200.0,
            height: 110.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 1,
            style: NodeStyle {
                fill: Some("#123456".into()),
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
        };
        let p = service
            .apply(
                p.id,
                &RevisionStamp::from(&p),
                "seed-blender-geometry",
                &Change::AddCanvasNode {
                    scene_id: blender,
                    node: node("base"),
                },
            )
            .unwrap()
            .project;
        let source_key = |project: &Project| {
            let mut projection = build_blender_contribution(project, blender).unwrap();
            projection.revision = 0;
            StageKey::for_input(
                "demo",
                "blender",
                "motionwright.recovery.blender",
                &stage_input(project, &connection, &projection).unwrap(),
            )
            .unwrap()
        };
        let original = source_key(&p);
        let p = service
            .apply(
                p.id,
                &RevisionStamp::from(&p),
                "seed-motion",
                &Change::AddScene {
                    name: "Motion Canvas".into(),
                    objective: "Downstream".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let motion = p.scenes[1].id;
        let unrelated = service
            .apply(
                p.id,
                &RevisionStamp::from(&p),
                "edit-motion-objective",
                &Change::UpdateSceneObjective {
                    scene_id: motion,
                    objective: "Changed downstream state".into(),
                },
            )
            .unwrap()
            .project;
        assert_eq!(
            original.digest,
            source_key(&unrelated).digest,
            "downstream changes must not force another native Blender dispatch"
        );
        let changed_blender = service
            .apply(
                unrelated.id,
                &RevisionStamp::from(&unrelated),
                "edit-blender-geometry",
                &Change::AddCanvasNode {
                    scene_id: blender,
                    node: node("added"),
                },
            )
            .unwrap()
            .project;
        assert_ne!(
            original.digest,
            source_key(&changed_blender).digest,
            "a changed Blender input MUST invalidate the old proof"
        );
    }

    #[cfg(unix)]
    #[test]
    fn scripted_fail_resume_preserves_blender_artifact_after_reopening_sqlite() {
        let dir = tempfile::tempdir().unwrap();
        let (coordinator, project, connection) = fixture(&dir);
        let blender_key = StageKey::for_input(
            "demo",
            "blender",
            "motionwright.recovery.blender",
            &json!({"source":"glb-v1"}),
        )
        .unwrap();
        let motion_key = StageKey::for_input(
            "demo",
            "motion-canvas",
            "motionwright.recovery.motion-canvas",
            &json!({"source":"film-v1"}),
        )
        .unwrap();
        // Stage A completes and persists a digest-bound native-format artifact.
        let b_attempt = coordinator.claim(&project, &blender_key).unwrap();
        assert_eq!(b_attempt, 1);
        let proof = fake_glb(&connection.output_root.join("scene.glb"));
        validate_blender_proof(&connection.output_root, &proof).unwrap();
        coordinator
            .checkpoint(
                &project,
                &blender_key,
                b_attempt,
                "completed",
                json!({"proof":proof}),
            )
            .unwrap();
        // Stage B gets a *confirmed* terminal failure.
        let m_attempt = coordinator.claim(&project, &motion_key).unwrap();
        coordinator
            .checkpoint(
                &project,
                &motion_key,
                m_attempt,
                "failed_known",
                json!({"retryable":true}),
            )
            .unwrap();

        drop(coordinator); // Simulate process death and a new service/SQLite connection.
        let reopened = StudioService::open(dir.path().join("project.sqlite3")).unwrap();
        let coordinator = ProductionCoordinator::new(reopened, connection.clone()).unwrap();
        let (resumed_glb, _) =
            read_previous::<RecoveryBlenderProof>(&coordinator, &project, &blender_key)
                .unwrap()
                .unwrap();
        assert_eq!(proof.sha256, resumed_glb.sha256);
        validate_blender_proof(&connection.output_root, &resumed_glb).unwrap();
        // Blender cannot claim a second dispatch: no duplicate execution.
        assert!(coordinator.claim(&project, &blender_key).is_err());
        assert!(
            read_previous::<MotionProof>(&coordinator, &project, &motion_key)
                .unwrap()
                .is_none()
        );
        assert_eq!(coordinator.claim(&project, &motion_key).unwrap(), 2);
        let stage_receipts = coordinator.receipts(project.id, 20).unwrap();
        assert_eq!(
            stage_receipts
                .iter()
                .filter(|row| row.command == "motionwright.recovery.blender")
                .count(),
            2
        );
        assert_eq!(
            stage_receipts
                .iter()
                .filter(|row| row.command == "motionwright.recovery.motion-canvas")
                .count(),
            3
        );
        println!(
            "cross-app scripted recovery: Blender reused, zero repeat dispatches, Motion Canvas attempt 2"
        );
    }

    #[cfg(unix)]
    #[test]
    fn interrupted_or_uncertain_stage_blocks_competing_process_and_changed_flow() {
        let dir = tempfile::tempdir().unwrap();
        let (first, project, connection) = fixture(&dir);
        let key = StageKey::for_input(
            "one",
            "blender",
            "motionwright.recovery.blender",
            &json!({"artifact":"same"}),
        )
        .unwrap();
        assert_eq!(first.claim(&project, &key).unwrap(), 1);
        let other = ProductionCoordinator::new(
            StudioService::open(dir.path().join("project.sqlite3")).unwrap(),
            connection,
        )
        .unwrap();
        let renamed = StageKey::for_input(
            "renamed",
            "blender",
            "motionwright.recovery.blender",
            &json!({"artifact":"same"}),
        )
        .unwrap();
        assert_eq!(key.id, renamed.id);
        assert!(other.claim(&project, &renamed).is_err());
        let changed = StageKey::for_input(
            "different",
            "blender",
            "motionwright.recovery.blender",
            &json!({"artifact":"new"}),
        )
        .unwrap();
        // A changed fingerprint cannot cause a second native Blender
        // dispatch while an earlier mutation is unsettled.
        assert!(other.claim(&project, &changed).is_err());
        let blocked =
            read_previous::<RecoveryBlenderProof>(&other, &project, &renamed).unwrap_err();
        assert!(!blocked.outcome_known);
        first
            .checkpoint(
                &project,
                &key,
                1,
                "outcome_unknown",
                json!({"retryable":false}),
            )
            .unwrap();
        assert!(other.claim(&project, &renamed).is_err());
        assert!(read_previous::<RecoveryBlenderProof>(&other, &project, &renamed).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn corrupted_blender_artifact_is_not_reused_even_with_completed_receipt() {
        let dir = tempfile::tempdir().unwrap();
        let (coordinator, project, connection) = fixture(&dir);
        let key = StageKey::for_input(
            "demo",
            "blender",
            "motionwright.recovery.blender",
            &json!({"source":"a"}),
        )
        .unwrap();
        let proof = fake_glb(&connection.output_root.join("original.glb"));
        let attempt = coordinator.claim(&project, &key).unwrap();
        coordinator
            .checkpoint(&project, &key, attempt, "completed", json!({"proof":proof}))
            .unwrap();
        let (remembered, _) = read_previous::<RecoveryBlenderProof>(&coordinator, &project, &key)
            .unwrap()
            .unwrap();
        fs::write(connection.output_root.join("original.glb"), b"tampered").unwrap();
        assert!(validate_blender_proof(&connection.output_root, &remembered).is_err());
        assert!(coordinator.claim(&project, &key).is_err());
        // A path escape is rejected even with a matching digest.
        let mut path_escape = remembered;
        path_escape.relative_path = "../secret.glb".into();
        assert!(validate_blender_proof(&connection.output_root, &path_escape).is_err());
    }

    #[test]
    fn native_motion_manifest_full_frame_readback_detects_modified_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let artifact_root = root.join("render-one");
        fs::create_dir_all(artifact_root.join("frames")).unwrap();
        let png = artifact_root.join("frames/000000.png");
        fs::write(&png, b"frame 0 controlled fixture bytes").unwrap();
        let frame_sha = sha256_file(&png).unwrap();
        let manifest = json!({
            "plan": {"frame_count":1,"first_frame":0,"end_frame_exclusive":1},
            "frames":[{"index":0,"file":"frames/000000.png","sha256":frame_sha,
                      "bytes":fs::metadata(&png).unwrap().len()}]
        });
        let manifest_path = artifact_root.join("artifact-manifest.json");
        fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        let manifest_sha = sha256_file(&manifest_path).unwrap();
        let generation = Uuid::now_v7();
        let proof = MotionCanvasRenderEvidence {
            project_resource: "project:fixture".into(),
            generation,
            revision: 1,
            deliverable_id: Uuid::now_v7(),
            frame_rate: Rate::new(30, 1).unwrap(),
            segments: vec![MotionCanvasSegmentEvidence {
                segment_id: "segment-1".into(),
                scene_ids: vec![Uuid::now_v7()],
                frame_count: 1,
                plan_ref: "native-plan".into(),
                fingerprint: "a".repeat(64),
                job_ref: "real-job".into(),
                artifact: json!({"directory":"render-one","frame_count":1,
                                 "manifest_sha256":manifest_sha}),
                verification: json!({
                    "report":{"execution_status":"completed","support_level":"native"},
                    "measurement":{"findings":[],"validation":{"checks":[
                        {"id":"native","verdict":"PASS"}
                    ]}}
                }),
            }],
        };
        let hashes = validate_motion_proof(root, &proof, None).unwrap();
        assert_eq!(hashes, vec![manifest_sha]);
        validate_motion_proof(root, &proof, Some(&hashes)).unwrap();
        fs::write(&png, b"tampered frame").unwrap();
        assert!(validate_motion_proof(root, &proof, Some(&hashes)).is_err());
    }
}
