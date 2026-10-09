use super::*;
use crate::native_html::prepare_hyperframes_plan;
use motionwright_domain::hyperframes_profile as h;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HyperframesArtifact {
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
    pub media_type: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct HyperframesRenderEvidence {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub document_id: Uuid,
    pub profile_id: Uuid,
    pub job_ref: String,
    pub source_sha256: String,
    pub plan_sha256: String,
    pub rate: h::FrameRate,
    pub width: u32,
    pub height: u32,
    pub frame_count: u32,
    pub alpha: bool,
    pub color: String,
    pub frames: HyperframesArtifact,
    pub mezzanine: HyperframesArtifact,
    pub source: HyperframesArtifact,
    pub document: HyperframesArtifact,
    pub observations: HyperframesArtifact,
    pub runtime_receipt_sha256: String,
    pub creative_approval: String,
}
#[derive(Debug, Clone, Copy)]
pub enum HyperframesJobAction {
    Status,
    Cancel,
    Result,
}
impl HyperframesJobAction {
    fn command(self) -> &'static str {
        match self {
            Self::Status => "driver.hyperframes.render.status",
            Self::Cancel => "driver.hyperframes.render.cancel",
            Self::Result => "driver.hyperframes.render.result",
        }
    }
}
fn start_request(attempt: Uuid) -> String {
    format!("hf:{attempt}:start")
}
fn valid_job(job: &str) -> bool {
    job.len() == 35
        && job.starts_with("hf-")
        && job.as_bytes()[3..]
            .iter()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(b))
}
impl ProductionCoordinator {
    /// The existing receipt stream is the queue/journal. No private scheduler or implicit installation is created.
    pub async fn start_hyperframes_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        document_id: Uuid,
        attempt_id: Uuid,
    ) -> NativeResult<Value> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if project.stamp() != *expected {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native creative source changed before production",
            ));
        }
        let plan = prepare_hyperframes_plan(&project, document_id)?;
        let source_sha256 = h::source_digest(&plan.document).map_err(|e| invalid(e.to_string()))?;
        let args =
            json!({"attempt_id":attempt_id,"plan":plan,"expected_source_sha256":source_sha256});
        // The existing CLI uses one bounded argument. Larger documents require a
        // separate authorized file-handoff route, never an OS-dependent silent truncation.
        if serde_json::to_vec(&args)
            .map_err(|_| invalid("Native arguments are malformed"))?
            .len()
            > 96 * 1024
        {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Native composition exceeds this 96 KiB IPC profile; split scenes or use a separately admitted file-handoff profile",
            ));
        }
        let result = self
            .execute(
                project_id,
                expected,
                &start_request(attempt_id),
                "driver.hyperframes.render.start",
                args,
                true,
            )
            .await?;
        let data = response_data(&result)?;
        if data["job_ref"] != format!("hf-{}", attempt_id.simple())
            || data["source_sha256"] != source_sha256
        {
            return Err(backend(
                "HyperFrames did not acknowledge the exact logical attempt/source",
            ));
        }
        Ok(result)
    }
    /// A source revision can become stale while its job continues. Reconciliation
    /// is allowed only through a recorded attempt from this still-current generation.
    pub async fn query_hyperframes_job(
        &self,
        project_id: Uuid,
        attempt_id: Uuid,
        request_id: &str,
        action: HyperframesJobAction,
    ) -> NativeResult<Value> {
        if request_id.is_empty()
            || request_id.len() > 160
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("Native observation request identity is invalid"));
        }
        let origin = self
            .service
            .latest_production_receipt(project_id, &start_request(attempt_id))
            .map_err(storage_error)?
            .ok_or_else(|| {
                Error::new(
                    ErrorCode::NotFound,
                    "Native attempt has no application-owned start receipt",
                )
            })?;
        if origin.command != "driver.hyperframes.render.start" {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Attempt receipt belongs to another operation",
            ));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if origin.generation != project.generation {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Imported or rotated project generations do not inherit runtime jobs",
            ));
        }
        let stamp = RevisionStamp {
            resource: project.resource_key(),
            generation: origin.generation,
            revision: origin.revision,
        };
        let result=self.execute_at_revision(project_id,&stamp,request_id,action.command(),json!({"job_ref":format!("hf-{}",attempt_id.simple()),"project_id":project_id,"generation":origin.generation}),ExecutionPolicy { mutation: matches!(action, HyperframesJobAction::Cancel), allow_stale_native_observation: true }).await?;
        let data = response_data(&result)?;
        if data.pointer("/identity/project_id").and_then(Value::as_str)
            != Some(project_id.to_string().as_str())
            || data.pointer("/identity/generation").and_then(Value::as_str)
                != Some(origin.generation.to_string().as_str())
            || data.pointer("/identity/revision").and_then(Value::as_u64) != Some(origin.revision)
        {
            return Err(backend(
                "Native job identity differs from its persisted origin",
            ));
        }
        Ok(result)
    }
    pub fn validate_hyperframes_result(
        &self,
        project_id: Uuid,
        document_id: Uuid,
        result: &Value,
    ) -> NativeResult<HyperframesRenderEvidence> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        let plan = prepare_hyperframes_plan(&project, document_id)?;
        let document = project
            .production_design
            .workspace
            .native_scenes
            .iter()
            .find(|doc| doc.id == document_id)
            .expect("validated native document");
        let data = response_data(result)?;
        if data["state"] != "succeeded" {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Native result is not a verified terminal artifact",
            ));
        }
        let raw = &data["result"];
        let plan_sha256 = hex::encode(Sha256::digest(
            serde_json::to_vec(&plan).map_err(|_| invalid("Native source cannot be serialized"))?,
        ));
        let source_sha256 = h::source_digest(&plan.document).map_err(|e| invalid(e.to_string()))?;
        let canvas = &plan.document.canvas;
        if raw["schema"] != "motionwright.hyperframes-runtime-result/1"
            || raw["project_id"] != project.id.to_string()
            || raw["generation"] != project.generation.to_string()
            || raw["revision"] != project.revision
            || raw["scene_id"] != plan.scene_id.to_string()
            || raw["plan_sha256"] != plan_sha256
            || raw["source_sha256"] != source_sha256
            || raw["width"] != canvas.width
            || raw["height"] != canvas.height
            || raw["frame_count"] != canvas.frames
            || raw["rate"] != json!(canvas.rate)
            || raw["alpha"] != canvas.background.is_none()
            || raw["color"] != "srgb"
            || raw["runtime_receipt_sha256"] != plan.runtime_receipt_sha256
        {
            return Err(backend(
                "Native result no longer matches this editable document and current revision",
            ));
        }
        let job_ref = required_string(data, "/job_ref", "Native job reference is missing")?;
        if !valid_job(&job_ref) {
            return Err(backend("Malformed native result reference"));
        }
        let artifact = |name: &str, media: &str, max: u64| -> NativeResult<HyperframesArtifact> {
            let artifact: HyperframesArtifact = serde_json::from_value(raw[name].clone())
                .map_err(|_| backend("Native artifact is malformed"))?;
            if artifact.media_type != media
                || artifact.bytes == 0
                || artifact.bytes > max
                || !artifact.relative_path.starts_with(&format!("{job_ref}/"))
            {
                return Err(backend(
                    "Native artifact violates its exact media/scope contract",
                ));
            }
            let path = verify_output_artifact(
                &self.client.connection().output_root,
                &artifact.relative_path,
                &artifact.sha256,
                max,
            )?;
            if fs::metadata(path)
                .map_err(|_| backend("Native artifact metadata unavailable"))?
                .len()
                != artifact.bytes
            {
                return Err(backend("Native artifact byte count changed"));
            }
            Ok(artifact)
        };
        let frames = artifact("frames", "application/json", 4 * 1024 * 1024)?;
        let source = artifact("source", "text/html", 3 * 1024 * 1024)?;
        let source_document = artifact("document", "application/json", 2 * 1024 * 1024)?;
        if source.sha256 != source_sha256 || source_document.sha256 != plan_sha256 {
            return Err(backend("Native source preservation digest differs"));
        }
        let mezzanine = artifact("mezzanine", "video/x-matroska", 512 * 1024 * 1024)?;
        let observations = artifact("observations", "application/x-ndjson", 64 * 1024 * 1024)?;
        let evidence = HyperframesRenderEvidence {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id: plan.scene_id,
            document_id,
            profile_id: document.profile_id,
            job_ref,
            source_sha256,
            plan_sha256,
            rate: canvas.rate,
            width: canvas.width,
            height: canvas.height,
            frame_count: canvas.frames,
            alpha: canvas.background.is_none(),
            color: "srgb".into(),
            frames,
            mezzanine,
            source,
            document: source_document,
            observations,
            runtime_receipt_sha256: required_string(
                raw,
                "/runtime_receipt_sha256",
                "Native runtime fingerprint is absent",
            )?,
            creative_approval: "required".into(),
        };
        validate_frame_manifest(&self.client.connection().output_root, &evidence)?;
        Ok(evidence)
    }
    pub async fn render_hyperframes_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        document_id: Uuid,
        attempt_id: Uuid,
    ) -> NativeResult<HyperframesRenderEvidence> {
        self.start_hyperframes_scene(project_id, expected, document_id, attempt_id)
            .await?;
        let deadline = Instant::now() + Duration::from_secs(330);
        let query = Uuid::new_v4();
        let mut polls = 0u32;
        loop {
            if Instant::now() >= deadline {
                return Err(Error::new(ErrorCode::Timeout,"Native job exceeded the observation deadline; query its existing attempt instead of starting a duplicate").uncertain());
            }
            let status = self
                .query_hyperframes_job(
                    project_id,
                    attempt_id,
                    &format!("hf:{query}:poll:{polls}"),
                    HyperframesJobAction::Status,
                )
                .await?;
            let data = response_data(&status)?;
            match data["state"].as_str() {
                Some("succeeded") => break,
                Some("failed") => {
                    return Err(backend(format!(
                        "HyperFrames failed: {}",
                        data["diagnostic"].as_str().unwrap_or("inspect its receipt")
                    )));
                }
                Some("cancelled") => {
                    return Err(Error::new(
                        ErrorCode::Cancelled,
                        "Native capture was cancelled",
                    ));
                }
                Some("unknown") => {
                    return Err(Error::new(
                        ErrorCode::Conflict,
                        "Native outcome is unknown; explicit reconciliation is required",
                    )
                    .uncertain());
                }
                Some("starting" | "rendering" | "cancelling") => {}
                _ => {
                    return Err(backend(
                        "Native provider returned an unsupported lifecycle state",
                    ));
                }
            }
            polls += 1;
            sleep(Duration::from_millis(500)).await;
        }
        let result = self
            .query_hyperframes_job(
                project_id,
                attempt_id,
                &format!("hf:{query}:result"),
                HyperframesJobAction::Result,
            )
            .await?;
        self.validate_hyperframes_result(project_id, document_id, &result)
    }
}
fn validate_frame_manifest(
    root: &Path,
    evidence: &HyperframesRenderEvidence,
) -> NativeResult<Value> {
    let bytes = read_verified_production_artifact(
        root,
        &evidence.frames.relative_path,
        &evidence.frames.sha256,
        4 * 1024 * 1024,
    )?;
    let manifest: Value = serde_json::from_slice(&bytes)
        .map_err(|_| backend("Native frame manifest is malformed"))?;
    if manifest["schema"] != "motionwright.hyperframes-native-frames/1"
        || manifest["source_sha256"] != evidence.source_sha256
        || manifest["plan_sha256"] != evidence.plan_sha256
        || manifest["generation"] != evidence.generation.to_string()
        || manifest["project_id"] != evidence.project_id.to_string()
        || manifest["revision"] != evidence.revision
        || manifest["frame_count"] != evidence.frame_count
        || manifest["width"] != evidence.width
        || manifest["height"] != evidence.height
        || manifest["rate"] != json!(evidence.rate)
        || manifest["observation"]["coverage"] != "all_frames"
        || manifest["external_requests"] != 0
    {
        return Err(backend(
            "Native frame coverage/identity differs from the admitted source",
        ));
    }
    let frames = manifest["frames"]
        .as_array()
        .ok_or_else(|| backend("Native manifest has no frames"))?;
    if frames.len() != evidence.frame_count as usize {
        return Err(backend("Native frame count is incomplete"));
    }
    for (index, frame) in frames.iter().enumerate() {
        if frame["frame"] != index
            || frame["relative_path"] != format!("frames/frame-{index:06}.png")
            || !frame["sha256"].as_str().is_some_and(is_sha256)
        {
            return Err(backend("Native frame sequence identity is inconsistent"));
        }
    }
    Ok(manifest)
}
/// Callers must first match a server-held evidence grant to the current project.
/// This helper does not expose arbitrary root/path selection to the WebView.
pub fn read_hyperframes_frame(
    root: &Path,
    evidence: &HyperframesRenderEvidence,
    index: u32,
) -> NativeResult<Vec<u8>> {
    if index >= evidence.frame_count {
        return Err(invalid(
            "Frame index is outside the verified native interval",
        ));
    }
    let manifest = validate_frame_manifest(root, evidence)?;
    let item = &manifest["frames"][index as usize];
    let bytes = read_verified_production_artifact(
        root,
        &format!("{}/frames/frame-{index:06}.png", evidence.job_ref),
        item["sha256"].as_str().expect("validated frame digest"),
        32 * 1024 * 1024,
    )?;
    if bytes.len() < 24
        || bytes[..8] != [137, 80, 78, 71, 13, 10, 26, 10]
        || u32::from_be_bytes(bytes[16..20].try_into().unwrap()) != evidence.width
        || u32::from_be_bytes(bytes[20..24].try_into().unwrap()) != evidence.height
    {
        return Err(backend(
            "Native PNG header does not match the verified output",
        ));
    }
    Ok(bytes)
}
