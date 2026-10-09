use motionwright_domain::Project;
use motionwright_native::production::{
    HyperframesRenderEvidence, MotionCanvasRenderEvidence, read_verified_production_artifact,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::VecDeque,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};
use uuid::Uuid;

/// The renderer returns a verified PNG sequence, not an MP4. Never expose
/// owner output paths to the WebView: only ephemeral, source-bound tokens.
const MAX_GRANTS: usize = 64;
const MAX_RENDER_FRAMES: u64 = 72_000;
const MAX_MANIFEST_BYTES: u64 = 16 * 1024 * 1024;
const MAX_FRAME_BYTES: u64 = 24 * 1024 * 1024;
const MAX_SIMULTANEOUS_READS: usize = 2;

#[derive(Debug, Clone, Serialize)]
pub struct NativeFrameGrant {
    pub token: Uuid,
    pub segment_id: String,
    pub scene_ids: Vec<Uuid>,
    pub frame_count: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeFrameRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub token: Uuid,
    pub frame_index: u64,
}

#[derive(Clone)]
struct FrameMeta {
    file: String,
    sha256: String,
    bytes: u64,
}

#[derive(Deserialize)]
struct FrameManifest {
    index: u64,
    file: String,
    sha256: String,
    bytes: u64,
}

#[derive(Deserialize)]
struct ManifestPlan {
    first_frame: u64,
    end_frame_exclusive: u64,
    frame_count: u64,
}

#[derive(Deserialize)]
struct RenderManifest {
    plan: ManifestPlan,
    frames: Vec<FrameManifest>,
}

#[derive(Clone)]
struct GrantEntry {
    token: Uuid,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    directory: String,
    output_root: PathBuf,
    frames: Arc<Vec<FrameMeta>>,
    /// This canonical response was recorded after the authorized Semwright
    /// render and verification, not deserialized from a WebView request.
    source_evidence: Option<MotionCanvasRenderEvidence>,
    native_html: Option<HyperframesRenderEvidence>,
}

#[derive(Default)]
struct RegistryInner {
    grants: Mutex<VecDeque<GrantEntry>>,
    active_reads: AtomicUsize,
}

#[derive(Clone, Default)]
pub struct NativePreviewRegistry {
    inner: Arc<RegistryInner>,
}

struct ReadPermit<'a>(&'a AtomicUsize);
impl Drop for ReadPermit<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Release);
    }
}

fn is_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn safe_render_directory(value: &str) -> bool {
    value.len() == 39
        && value.starts_with("render-")
        && value.as_bytes()[7..]
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
}

fn validate_manifest(bytes: &[u8], frame_count: u64) -> Option<Vec<FrameMeta>> {
    if frame_count == 0 || frame_count > MAX_RENDER_FRAMES {
        return None;
    }
    let manifest: RenderManifest = serde_json::from_slice(bytes).ok()?;
    if manifest.plan.first_frame != 0
        || manifest.plan.frame_count != frame_count
        || manifest.plan.end_frame_exclusive != frame_count
        || manifest.frames.len() != frame_count as usize
    {
        return None;
    }
    let mut frames = Vec::with_capacity(manifest.frames.len());
    for (index, frame) in manifest.frames.into_iter().enumerate() {
        let expected_file = format!("frames/{index:06}.png");
        if frame.index != index as u64
            || frame.file != expected_file
            || !is_digest(&frame.sha256)
            || frame.bytes == 0
            || frame.bytes > MAX_FRAME_BYTES
        {
            return None;
        }
        frames.push(FrameMeta {
            file: frame.file,
            sha256: frame.sha256,
            bytes: frame.bytes,
        });
    }
    Some(frames)
}

fn segment_artifact(
    output_root: &Path,
    artifact: &Value,
    frame_count: u64,
) -> Option<(String, Vec<FrameMeta>)> {
    let directory = artifact.get("directory")?.as_str()?;
    if !safe_render_directory(directory) || artifact.get("frame_count")?.as_u64()? != frame_count {
        return None;
    }
    let digest = artifact.get("manifest_sha256")?.as_str()?;
    if !is_digest(digest)
        || artifact.get("manifest")?.as_str()? != format!("{directory}/artifact-manifest.json")
        || artifact.get("first_png")?.as_str()? != format!("{directory}/frames/000000.png")
        || artifact.get("last_png")?.as_str()?
            != format!("{directory}/frames/{:06}.png", frame_count.checked_sub(1)?)
    {
        return None;
    }
    let bytes = read_verified_production_artifact(
        output_root,
        &format!("{directory}/artifact-manifest.json"),
        digest,
        MAX_MANIFEST_BYTES,
    )
    .ok()?;
    if artifact.get("manifest_bytes")?.as_u64()? != bytes.len() as u64 {
        return None;
    }
    Some((
        directory.to_owned(),
        validate_manifest(&bytes, frame_count)?,
    ))
}

impl NativePreviewRegistry {
    /// Registration is possible only after the real, verified Semwright
    /// render operation has returned its authoritative segment evidence.
    pub fn register(
        &self,
        output_root: &Path,
        owner_resource: &str,
        evidence: &MotionCanvasRenderEvidence,
    ) -> Vec<NativeFrameGrant> {
        if evidence.project_resource != owner_resource {
            return Vec::new();
        }
        let Some(project_id) = owner_resource
            .strip_prefix("project:")
            .and_then(|text| Uuid::parse_str(text).ok())
        else {
            return Vec::new();
        };
        let mut granted = Vec::new();
        for segment in evidence.segments.iter().take(MAX_GRANTS) {
            if segment.segment_id.is_empty()
                || segment.job_ref.is_empty()
                || segment.scene_ids.is_empty()
            {
                continue;
            }
            let Some((directory, frames)) =
                segment_artifact(output_root, &segment.artifact, segment.frame_count)
            else {
                continue;
            };
            let token = Uuid::new_v4();
            let entry = GrantEntry {
                token,
                project_id,
                generation: evidence.generation,
                revision: evidence.revision,
                deliverable_id: evidence.deliverable_id,
                directory,
                output_root: output_root.to_path_buf(),
                frames: Arc::new(frames),
                source_evidence: Some(evidence.clone()),
                native_html: None,
            };
            if let Ok(mut entries) = self.inner.grants.lock() {
                if entries.len() >= MAX_GRANTS {
                    entries.pop_front();
                }
                entries.push_back(entry);
                granted.push(NativeFrameGrant {
                    token,
                    segment_id: segment.segment_id.clone(),
                    scene_ids: segment.scene_ids.clone(),
                    frame_count: segment.frame_count,
                });
            }
        }
        granted
    }

    /// Resolve the canonical rendering evidence behind an unguessable session
    /// handle. The WebView can never substitute a forged renderer response.
    pub fn master_source(
        &self,
        project: &Project,
        token: Uuid,
        deliverable_id: Uuid,
    ) -> Result<MotionCanvasRenderEvidence, String> {
        let entry = self
            .inner
            .grants
            .lock()
            .map_err(|_| "Native preview registry is unavailable.")?
            .iter()
            .find(|record| record.token == token)
            .cloned()
            .ok_or("Native render session is no longer available.")?;
        let source_evidence = entry.source_evidence.as_ref().ok_or("This source is a native HTML contribution; use a compatible hybrid mastering route, not the Motion Canvas-only master shortcut.")?;
        if entry.project_id != project.id
            || entry.generation != project.generation
            || entry.revision != project.revision
            || entry.deliverable_id != deliverable_id
            || source_evidence.project_resource != project.resource_key()
            || source_evidence.generation != project.generation
            || source_evidence.revision != project.revision
            || source_evidence.deliverable_id != deliverable_id
            || source_evidence.segments.len() != 1
            || source_evidence.segments[0].frame_count != entry.frames.len() as u64
        {
            return Err("Native master source belongs to another project revision or requires an unsupported multi-segment assembly.".into());
        }
        Ok(source_evidence.clone())
    }

    /// A grant never accepts caller-provided paths, hashes or media MIME types.
    /// The selected file is picked by index from a preverified manifest.
    pub fn read_frame(
        &self,
        project: &Project,
        request: &NativeFrameRequest,
    ) -> Result<Vec<u8>, String> {
        if project.id != request.project_id
            || project.generation != request.generation
            || project.revision != request.revision
        {
            return Err("Native preview reference is stale; reopen the rendered revision.".into());
        }
        let entry = self
            .inner
            .grants
            .lock()
            .map_err(|_| "Native preview registry is unavailable.")?
            .iter()
            .find(|grant| grant.token == request.token)
            .cloned()
            .ok_or("Native preview is not registered for this application session.")?;
        if entry.project_id != request.project_id
            || entry.generation != request.generation
            || entry.revision != request.revision
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == entry.deliverable_id)
        {
            return Err("Native preview grant belongs to a different project revision.".into());
        }
        let frame = entry
            .frames
            .get(usize::try_from(request.frame_index).map_err(|_| "Frame index overflow.")?)
            .ok_or("Requested native frame is outside the verified render.")?;

        let current =
            self.inner
                .active_reads
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| {
                    (n < MAX_SIMULTANEOUS_READS).then_some(n + 1)
                });
        if current.is_err() {
            return Err("Native preview is busy; retry this frame.".into());
        }
        let _permit = ReadPermit(&self.inner.active_reads);
        let bytes = read_verified_production_artifact(
            &entry.output_root,
            &format!("{}/{}", entry.directory, frame.file),
            &frame.sha256,
            MAX_FRAME_BYTES,
        )
        .map_err(|_| "Rendered frame is unavailable or failed SHA-256 verification.")?;
        if bytes.len() as u64 != frame.bytes || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            return Err("Rendered frame metadata no longer matches the verified PNG.".into());
        }
        Ok(bytes)
    }
}

impl NativePreviewRegistry {
    /// Extend the existing read-grant registry; never grant a WebView HTML execution.
    pub fn register_hyperframes(
        &self,
        output_root: &Path,
        project: &Project,
        evidence: &HyperframesRenderEvidence,
    ) -> Option<NativeFrameGrant> {
        if evidence.project_id != project.id
            || evidence.generation != project.generation
            || evidence.revision != project.revision
            || evidence.frame_count == 0
            || evidence.frame_count > 3600
            || evidence.job_ref.len() != 35
            || !evidence.job_ref.starts_with("hf-")
            || !evidence.job_ref.as_bytes()[3..]
                .iter()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(b))
        {
            return None;
        }
        let source = project
            .production_design
            .workspace
            .native_scenes
            .iter()
            .find(|doc| {
                doc.id == evidence.document_id
                    && doc.scene_id == evidence.scene_id
                    && doc.profile_id == evidence.profile_id
            })?;
        if source.source.source_digest().ok()? != evidence.source_sha256 {
            return None;
        }
        let bytes = read_verified_production_artifact(
            output_root,
            &evidence.frames.relative_path,
            &evidence.frames.sha256,
            4 * 1024 * 1024,
        )
        .ok()?;
        let manifest: Value = serde_json::from_slice(&bytes).ok()?;
        if manifest["source_sha256"] != evidence.source_sha256
            || manifest["plan_sha256"] != evidence.plan_sha256
            || manifest["project_id"] != project.id.to_string()
            || manifest["generation"] != project.generation.to_string()
            || manifest["revision"] != project.revision
            || manifest["frame_count"] != evidence.frame_count
        {
            return None;
        }
        let source_frames = manifest["frames"].as_array()?;
        if source_frames.len() != evidence.frame_count as usize {
            return None;
        }
        let mut frames = Vec::with_capacity(source_frames.len());
        for (index, frame) in source_frames.iter().enumerate() {
            let file = format!("frames/frame-{index:06}.png");
            let digest = frame["sha256"].as_str()?;
            let size = frame["bytes"].as_u64()?;
            if frame["relative_path"] != file
                || frame["frame"] != index
                || !is_digest(digest)
                || size == 0
                || size > MAX_FRAME_BYTES
            {
                return None;
            }
            frames.push(FrameMeta {
                file,
                sha256: digest.into(),
                bytes: size,
            });
        }
        let token = Uuid::new_v4();
        let entry = GrantEntry {
            token,
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            deliverable_id: evidence.profile_id,
            directory: evidence.job_ref.clone(),
            output_root: output_root.to_path_buf(),
            frames: Arc::new(frames),
            source_evidence: None,
            native_html: Some(evidence.clone()),
        };
        let mut entries = self.inner.grants.lock().ok()?;
        if entries.len() >= MAX_GRANTS {
            entries.pop_front();
        }
        entries.push_back(entry);
        Some(NativeFrameGrant {
            token,
            segment_id: evidence.document_id.to_string(),
            scene_ids: vec![evidence.scene_id],
            frame_count: u64::from(evidence.frame_count),
        })
    }

    /// Resolve retained native source only from a server-owned, current session token.
    pub fn hyperframes_source(
        &self,
        project: &Project,
        token: Uuid,
    ) -> Result<HyperframesRenderEvidence, String> {
        let entries = self
            .inner
            .grants
            .lock()
            .map_err(|_| "Native preview registry is unavailable.")?;
        let entry = entries
            .iter()
            .find(|entry| entry.token == token)
            .ok_or("Native source grant is unavailable.")?;
        if entry.project_id != project.id
            || entry.generation != project.generation
            || entry.revision != project.revision
        {
            return Err("Native source grant belongs to another project revision.".into());
        }
        entry
            .native_html
            .clone()
            .ok_or_else(|| "This native grant is not a HyperFrames contribution.".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::Project;
    use serde_json::json;
    use sha2::{Digest, Sha256};

    fn digest(bytes: &[u8]) -> String {
        hex::encode(Sha256::digest(bytes))
    }

    fn sample(
        root: &Path,
        project: &Project,
    ) -> (NativePreviewRegistry, MotionCanvasRenderEvidence) {
        let dir = format!("render-{}", Uuid::new_v4().simple());
        let frames_dir = root.join(&dir).join("frames");
        std::fs::create_dir_all(&frames_dir).unwrap();
        let png = include_bytes!("../icons/icon.png");
        std::fs::write(frames_dir.join("000000.png"), png).unwrap();
        let manifest = json!({
            "plan": {"first_frame": 0, "end_frame_exclusive": 1, "frame_count": 1},
            "frames": [{"index": 0, "file": "frames/000000.png", "bytes": png.len(), "sha256": digest(png)}]
        });
        let manifest_bytes = serde_json::to_vec(&manifest).unwrap();
        std::fs::write(
            root.join(&dir).join("artifact-manifest.json"),
            &manifest_bytes,
        )
        .unwrap();
        let evidence: MotionCanvasRenderEvidence = serde_json::from_value(json!({
            "project_resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "deliverable_id": project.deliverables[0].id,
            "frame_rate": {"num": 30, "den": 1},
            "segments": [{
                "segment_id": "native-segment",
                "scene_ids": [project.scenes[0].id],
                "frame_count": 1,
                "plan_ref": "canonical-plan",
                "fingerprint": "canonical-fingerprint",
                "job_ref": "canonical-job",
                "artifact": {
                    "directory": dir,
                    "manifest": format!("{dir}/artifact-manifest.json"),
                    "first_png": format!("{dir}/frames/000000.png"),
                    "last_png": format!("{dir}/frames/000000.png"),
                    "frame_count": 1,
                    "manifest_bytes": manifest_bytes.len(),
                    "manifest_sha256": digest(&manifest_bytes),
                },
                "verification": {"report": {"support_level": "native"}}
            }]
        }))
        .unwrap();
        (NativePreviewRegistry::default(), evidence)
    }

    fn project() -> Project {
        let mut project = Project::new("Native preview test").unwrap();
        project
            .apply_change(&motionwright_domain::Change::AddScene {
                name: "Opening".into(),
                objective: "Bounded test".into(),
                duration_seconds: 1,
            })
            .unwrap();
        project
    }

    #[test]
    fn grants_only_canonical_owned_frames_and_reads_verified_png() {
        let temp = tempfile::tempdir().unwrap();
        let project = project();
        let (registry, evidence) = sample(temp.path(), &project);
        let grants = registry.register(temp.path(), &project.resource_key(), &evidence);
        assert_eq!(grants.len(), 1);
        let read = NativeFrameRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            token: grants[0].token,
            frame_index: 0,
        };
        assert_eq!(
            registry.read_frame(&project, &read).unwrap(),
            include_bytes!("../icons/icon.png")
        );
        assert!(
            registry
                .read_frame(
                    &project,
                    &NativeFrameRequest {
                        frame_index: 1,
                        ..read
                    }
                )
                .is_err()
        );
    }

    #[test]
    fn av_master_source_uses_authenticated_session_evidence_not_client_media() {
        let temp = tempfile::tempdir().unwrap();
        let project = project();
        let (registry, evidence) = sample(temp.path(), &project);
        let profile = evidence.deliverable_id;
        let grants = registry.register(temp.path(), &project.resource_key(), &evidence);
        assert_eq!(grants.len(), 1);
        let original = registry
            .master_source(&project, grants[0].token, profile)
            .unwrap();
        assert_eq!(original.project_resource, project.resource_key());
        assert_eq!(original.segments[0].job_ref, "canonical-job");
        assert_eq!(original.segments[0].frame_count, 1);
        assert!(
            registry
                .master_source(&project, Uuid::new_v4(), profile)
                .is_err()
        );
        assert!(
            registry
                .master_source(&project, grants[0].token, Uuid::new_v4())
                .is_err()
        );
        let mut stale = project.clone();
        stale.revision += 1;
        assert!(
            registry
                .master_source(&stale, grants[0].token, profile)
                .is_err()
        );

        let mut unsupported = evidence.clone();
        unsupported.segments.push(unsupported.segments[0].clone());
        let multi = registry.register(temp.path(), &project.resource_key(), &unsupported);
        assert!(!multi.is_empty());
        assert!(
            registry
                .master_source(&project, multi[0].token, profile)
                .is_err()
        );
    }

    #[test]
    fn rejects_unverified_manifests_wrong_revisions_and_tampered_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let project = project();
        let (registry, mut evidence) = sample(temp.path(), &project);
        let original = evidence.clone();
        evidence.segments[0].artifact["manifest_sha256"] = json!("f".repeat(64));
        assert!(
            registry
                .register(temp.path(), &project.resource_key(), &evidence)
                .is_empty()
        );
        assert!(
            registry
                .register(temp.path(), "project:other", &original)
                .is_empty()
        );
        let grants = registry.register(temp.path(), &project.resource_key(), &original);
        let request = NativeFrameRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            token: grants[0].token,
            frame_index: 0,
        };
        let mut newer = project.clone();
        newer.revision += 1;
        assert!(registry.read_frame(&newer, &request).is_err());
        let directory = original.segments[0].artifact["directory"].as_str().unwrap();
        std::fs::write(
            temp.path().join(directory).join("frames/000000.png"),
            b"forged bytes",
        )
        .unwrap();
        assert!(registry.read_frame(&project, &request).is_err());
    }

    #[test]
    fn rejects_parent_path_and_foreign_manifest_entries() {
        assert!(!safe_render_directory(
            "../render-0123456789abcdef0123456789abcdef"
        ));
        assert!(!safe_render_directory(
            "render-../0123456789abcdef0123456789abcd"
        ));
        assert!(validate_manifest(b"{\"frames\":[]}", 1).is_none());
    }
}
