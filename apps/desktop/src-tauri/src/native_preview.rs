use motionwright_domain::{Project, RevisionStamp};
use motionwright_native::film::FilmBuildOptions;
use motionwright_native::production::{
    MotionCanvasRenderEvidence, MotionCanvasWindowEvidence, MotionCanvasWindowRequest,
    read_cached_motion_canvas_window_frame, read_verified_production_artifact,
    reuse_motion_canvas_window,
};
use motionwright_service::StudioService;
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
enum GrantOrigin {
    Full {
        evidence: MotionCanvasRenderEvidence,
        options: Option<FilmBuildOptions>,
    },
    Window {
        evidence: Arc<MotionCanvasWindowEvidence>,
        service: StudioService,
    },
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
    /// Backend-produced origin, never deserialized from WebView input.
    origin: GrantOrigin,
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
    /// Legacy manifest-only registration is exclusively for adversarial unit
    /// tests. Production must require source-bound FilmBuildOptions via
    /// register_with_options; otherwise readiness may be misattributed.
    #[cfg(test)]
    pub fn register(
        &self,
        output_root: &Path,
        owner_resource: &str,
        evidence: &MotionCanvasRenderEvidence,
    ) -> Vec<NativeFrameGrant> {
        self.register_internal(output_root, owner_resource, evidence, None)
    }

    /// Only the actual production boundary can attach authoring provenance.
    /// Old synthetic/standalone test registrations cannot claim readiness for
    /// multi-segment assembly without that bound source intent.
    pub fn register_with_options(
        &self,
        output_root: &Path,
        owner_resource: &str,
        evidence: &MotionCanvasRenderEvidence,
        options: &FilmBuildOptions,
    ) -> Vec<NativeFrameGrant> {
        self.register_internal(output_root, owner_resource, evidence, Some(options.clone()))
    }

    fn register_internal(
        &self,
        output_root: &Path,
        owner_resource: &str,
        evidence: &MotionCanvasRenderEvidence,
        options: Option<FilmBuildOptions>,
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
                origin: GrantOrigin::Full {
                    evidence: evidence.clone(),
                    options: options.clone(),
                },
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

    /// Only a backend-produced native window enters this registry. It remains
    /// a preview origin, including when its retained prefix covers the Film.
    pub fn register_window(
        &self,
        service: &StudioService,
        owner_resource: &str,
        evidence: &MotionCanvasWindowEvidence,
    ) -> Result<NativeFrameGrant, String> {
        let project_id = owner_resource
            .strip_prefix("project:")
            .and_then(|text| Uuid::parse_str(text).ok())
            .ok_or("Native window owner resource is invalid.")?;
        let project = service
            .project(project_id)
            .map_err(|_| "Native window source project is unavailable.")?;
        if evidence.project_resource != owner_resource
            || evidence.generation != project.generation
            || evidence.revision != project.revision
            || evidence.cached_frames.is_empty()
            || evidence.cached_frames.len() as u64
                != evidence
                    .request
                    .source_window
                    .end
                    .checked_sub(evidence.request.source_window.start)
                    .ok_or("Native window bounds are invalid.")?
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == evidence.request.deliverable_id)
        {
            return Err("Native window belongs to another saved source scope.".into());
        }
        let token = Uuid::new_v4();
        let frames = evidence
            .cached_frames
            .iter()
            .map(|frame| FrameMeta {
                file: frame.original_relative_path.clone(),
                sha256: frame.sha256.clone(),
                bytes: frame.bytes,
            })
            .collect::<Vec<_>>();
        let grant = NativeFrameGrant {
            token,
            segment_id: evidence.segment_id.clone(),
            scene_ids: vec![evidence.request.source_scene_id],
            frame_count: frames.len() as u64,
        };
        let entry = GrantEntry {
            token,
            project_id,
            generation: project.generation,
            revision: project.revision,
            deliverable_id: evidence.request.deliverable_id,
            // Window frame reads use the same Store, never these Full-only paths.
            directory: String::new(),
            output_root: PathBuf::new(),
            frames: Arc::new(frames),
            origin: GrantOrigin::Window {
                evidence: Arc::new(evidence.clone()),
                service: service.clone(),
            },
        };
        let mut entries = self
            .inner
            .grants
            .lock()
            .map_err(|_| "Native preview registry is unavailable.")?;
        if entries.len() >= MAX_GRANTS {
            entries.pop_front();
        }
        entries.push_back(entry);
        Ok(grant)
    }

    /// Explicit reuse of this session's original Window handle. No connection
    /// or producer is available here; every failure stays a read failure.
    pub fn reuse_window(
        &self,
        project: &Project,
        token: Uuid,
        request: &MotionCanvasWindowRequest,
    ) -> Result<(MotionCanvasWindowEvidence, NativeFrameGrant), String> {
        let entry = self
            .inner
            .grants
            .lock()
            .map_err(|_| "Native preview registry is unavailable.")?
            .iter()
            .find(|entry| entry.token == token)
            .cloned()
            .ok_or("Original native window is no longer registered in this session.")?;
        if entry.project_id != project.id
            || entry.generation != project.generation
            || entry.revision != project.revision
            || entry.deliverable_id != request.deliverable_id
        {
            return Err("Original native window belongs to another saved revision.".into());
        }
        let GrantOrigin::Window { evidence, service } = &entry.origin else {
            return Err("This handle does not identify an original native window.".into());
        };
        let reused = reuse_motion_canvas_window(service,project.id,&RevisionStamp::from(project),request,evidence)
            .map_err(|_| "Original native window cannot be reused at this revision; no render was dispatched.")?;
        Ok((
            reused,
            NativeFrameGrant {
                token,
                segment_id: evidence.segment_id.clone(),
                scene_ids: vec![evidence.request.source_scene_id],
                frame_count: entry.frames.len() as u64,
            },
        ))
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
        let GrantOrigin::Full {
            evidence: source_evidence,
            options: _,
        } = entry.origin
        else {
            return Err("A native window is preview-only and cannot supply a master.".into());
        };
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
        Ok(source_evidence)
    }

    /// All source/intent/paths are taken from the owner-issued registry entry.
    /// WebView only provides an opaque token and expected project/profile.
    pub fn multi_segment_source(
        &self,
        project: &Project,
        token: Uuid,
        deliverable_id: Uuid,
    ) -> Result<(MotionCanvasRenderEvidence, FilmBuildOptions, PathBuf), String> {
        let entry = self
            .inner
            .grants
            .lock()
            .map_err(|_| "Native preview registry is unavailable.")?
            .iter()
            .find(|item| item.token == token)
            .cloned()
            .ok_or("Native render session was not registered by this desktop.")?;
        let GrantOrigin::Full {
            evidence: source_evidence,
            options: source_options,
        } = entry.origin
        else {
            return Err("A native window is preview-only and cannot supply a master.".into());
        };
        if entry.project_id != project.id
            || entry.generation != project.generation
            || entry.revision != project.revision
            || entry.deliverable_id != deliverable_id
            || source_evidence.project_resource != project.resource_key()
            || source_evidence.generation != project.generation
            || source_evidence.revision != project.revision
            || source_evidence.deliverable_id != deliverable_id
            || source_evidence.segments.len() < 2
            || source_evidence
                .segments
                .iter()
                .filter(|segment| {
                    segment.artifact.get("directory").and_then(Value::as_str)
                        == Some(entry.directory.as_str())
                        && segment.frame_count == entry.frames.len() as u64
                })
                .count()
                != 1
        {
            return Err("Native multi-segment source belongs to another creative version, lacks a registered segment or is not multi-segment.".into());
        }
        let options = source_options.ok_or(
            "Original Film authoring options are unavailable for this native render session.",
        )?;
        Ok((source_evidence, options, entry.output_root))
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
        let bytes = match &entry.origin {
            GrantOrigin::Full { .. } => read_verified_production_artifact(
                &entry.output_root,
                &format!("{}/{}", entry.directory, frame.file),
                &frame.sha256,
                MAX_FRAME_BYTES,
            ),
            GrantOrigin::Window { evidence, service } => {
                read_cached_motion_canvas_window_frame(service, evidence, request.frame_index)
            }
        }
        .map_err(|_| "Rendered frame is unavailable or failed SHA-256 verification.")?;
        if bytes.len() as u64 != frame.bytes || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
            return Err("Rendered frame metadata no longer matches the verified PNG.".into());
        }
        Ok(bytes)
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
    fn multi_segment_readiness_requires_owner_provenance_and_exact_session_scope() {
        let root = tempfile::tempdir().unwrap();
        let project = project();
        let (_, mut one) = sample(root.path(), &project);
        let (_, two) = sample(root.path(), &project);
        let mut second = two.segments[0].clone();
        second.segment_id = "second-canonical-segment".into();
        second.job_ref = "second-native-job".into();
        one.segments.push(second);

        let options: FilmBuildOptions = serde_json::from_value(json!({
            "frame_rate": {"num": 30, "den": 1},
            "font_family": "Instrument Sans Variable",
            "mono_font_family": "IBM Plex Mono",
            "scene_intents": [{
                "scene_id": project.scenes[0].id,
                "role": "mechanism",
                "archetype": "statement"
            }]
        }))
        .unwrap();
        // Legacy synthetic/test registration lacks the original explicit
        // Film options and can never qualify for multi-source readiness.
        let unsigned = NativePreviewRegistry::default();
        let unbound = unsigned.register(root.path(), &project.resource_key(), &one);
        assert_eq!(unbound.len(), 2);
        assert!(
            unsigned
                .multi_segment_source(&project, unbound[0].token, project.deliverables[0].id)
                .is_err()
        );

        let trusted = NativePreviewRegistry::default();
        let issued =
            trusted.register_with_options(root.path(), &project.resource_key(), &one, &options);
        assert_eq!(issued.len(), 2);
        let (source, bound_options, source_root) = trusted
            .multi_segment_source(&project, issued[0].token, project.deliverables[0].id)
            .unwrap();
        assert_eq!(source.segments.len(), 2);
        assert_eq!(bound_options, options);
        assert_eq!(source_root, root.path());
        // The older master path remains strictly single-segment.
        assert!(
            trusted
                .master_source(&project, issued[0].token, project.deliverables[0].id)
                .is_err()
        );
        assert!(
            trusted
                .multi_segment_source(&project, Uuid::now_v7(), project.deliverables[0].id)
                .is_err()
        );
        let mut stale = project.clone();
        stale.revision += 1;
        assert!(
            trusted
                .multi_segment_source(&stale, issued[0].token, stale.deliverables[0].id)
                .is_err()
        );
        assert!(
            trusted
                .multi_segment_source(&project, issued[0].token, Uuid::now_v7())
                .is_err()
        );
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

    fn window_fixture(project: &Project) -> MotionCanvasWindowEvidence {
        use motionwright_domain::{RevisionStamp, SourceFrameWindow};
        use motionwright_native::production::{
            MotionCanvasCachedWindowFrame, MotionCanvasCaptureTimings, MotionCanvasStartOrigin,
            MotionCanvasWindowCapture, MotionCanvasWindowCost, MotionCanvasWindowUse,
            MotionCanvasWindowValidation,
        };
        use motionwright_service::{DerivedCacheRecord, RenderWindowCacheKey};
        let request: MotionCanvasWindowRequest = serde_json::from_value(json!({
            "deliverable_id":project.deliverables[0].id,"source_scene_id":project.scenes[0].id,
            "source_window":{"start":0,"end":1},
            "options":{"frame_rate":{"num":30,"den":1},"font_family":"Instrument Sans Variable",
                "mono_font_family":"IBM Plex Mono","scene_intents":[{
                    "scene_id":project.scenes[0].id,"role":"mechanism","archetype":"statement"}]}
        }))
        .unwrap();
        let record: DerivedCacheRecord = serde_json::from_value(json!({
            "format_version":1,"scope_sha256":"a1".repeat(32),"kind":"render-window-proxy",
            "fingerprint_sha256":"b2".repeat(32),"blob_sha256":"c3".repeat(32),
            "size_bytes":16,"work_units":1,"created_at":"unit-only"
        }))
        .unwrap();
        let key = RenderWindowCacheKey {
            project_id: project.id,
            expected: RevisionStamp::from(project),
            deliverable_id: project.deliverables[0].id,
            source_window: SourceFrameWindow { start: 0, end: 1 },
            fingerprint_sha256: record.fingerprint_sha256.clone(),
        };
        // This backend-only fixture tests the origin barrier, not native pixels.
        MotionCanvasWindowEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            request,
            segment_id: "window-unit-fixture".into(),
            segment_index: 0,
            segment_window: SourceFrameWindow { start: 0, end: 1 },
            film_digest: "d4".repeat(32),
            original_capture: MotionCanvasWindowCapture {
                segment_frame_count: 1,
                prefix: SourceFrameWindow { start: 0, end: 1 },
                plan_ref: "private-plan".into(),
                fingerprint: "e5".repeat(32),
                job_ref: "private-job".into(),
                start_origin: MotionCanvasStartOrigin::FreshAuthenticatedCall,
                artifact: json!({"private_path":"/private/owner/source.png"}),
                verification: json!({}),
                timings: MotionCanvasCaptureTimings {
                    fonts_micros: 0,
                    plan_micros: 0,
                    apply_micros: 0,
                    render_and_wait_micros: 0,
                    verification_micros: 0,
                },
            },
            validation: MotionCanvasWindowValidation {
                verdict: serde_json::from_value(json!("PASS")).unwrap(),
                native_frame_coverage: serde_json::from_value(json!("PASS")).unwrap(),
                pass_checks: 1,
                unknown_checks: 0,
                unknown_findings: 0,
            },
            cached_frames: vec![MotionCanvasCachedWindowFrame {
                source_frame: 0,
                segment_frame: 0,
                original_relative_path: "private-source.png".into(),
                sha256: record.blob_sha256.clone(),
                bytes: 16,
                key,
                record,
            }],
            cost: MotionCanvasWindowCost {
                operation: MotionCanvasWindowUse::NativePrefix,
                original_prefix_pngs: 1,
                selected_pngs: 1,
                reused_original_pngs: 0,
                start_calls_observed: 1,
                cached_png_bytes_returned: 0,
                cache_payload_bytes_admitted: 16,
                cache_micros: 0,
                internal_simulation_steps: None,
                internal_simulation_observation: "not_observed",
            },
        }
    }

    #[test]
    fn window_origin_cannot_be_promoted_to_either_master_source_even_at_full_range() {
        let directory = tempfile::tempdir().unwrap();
        let service = StudioService::open(directory.path().join("registry.sqlite3")).unwrap();
        let first = service.create_project("Window origin barrier").unwrap();
        let project = service
            .apply(
                first.id,
                &motionwright_domain::RevisionStamp::from(&first),
                "window-registry-scene",
                &motionwright_domain::Change::AddScene {
                    name: "Original".into(),
                    objective: "Origin barrier".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap()
            .project;
        let evidence = window_fixture(&project);
        let registry = NativePreviewRegistry::default();
        let grant = registry
            .register_window(&service, &project.resource_key(), &evidence)
            .unwrap();
        assert!(
            registry
                .master_source(&project, grant.token, evidence.request.deliverable_id)
                .is_err()
        );
        assert!(
            registry
                .multi_segment_source(&project, grant.token, evidence.request.deliverable_id)
                .is_err()
        );
        let public = serde_json::to_string(&evidence.summary()).unwrap();
        for private in [
            "private-plan",
            "private-job",
            "/private/owner/source.png",
            "private-source.png",
        ] {
            assert!(!public.contains(private));
        }
        assert!(evidence.summary().preview_only);
        assert!(!evidence.summary().master_eligible);
        let mut stale = project.clone();
        stale.revision += 1;
        assert!(
            registry
                .reuse_window(&stale, grant.token, &evidence.request)
                .is_err()
        );
        assert!(
            registry
                .read_frame(
                    &stale,
                    &NativeFrameRequest {
                        project_id: project.id,
                        generation: project.generation,
                        revision: project.revision,
                        token: grant.token,
                        frame_index: 0
                    }
                )
                .is_err()
        );
        assert!(
            registry
                .read_frame(
                    &project,
                    &NativeFrameRequest {
                        project_id: project.id,
                        generation: project.generation,
                        revision: project.revision,
                        token: grant.token,
                        frame_index: 1
                    }
                )
                .is_err()
        );
        assert!(
            service
                .production_receipts(project.id, 512)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn full_or_missing_handle_cannot_trigger_implicit_window_capture() {
        let root = tempfile::tempdir().unwrap();
        let project = project();
        let (registry, full) = sample(root.path(), &project);
        let grant = registry
            .register(root.path(), &project.resource_key(), &full)
            .remove(0);
        let window = window_fixture(&project);
        assert!(
            registry
                .reuse_window(&project, grant.token, &window.request)
                .is_err()
        );
        assert!(
            registry
                .reuse_window(&project, Uuid::new_v4(), &window.request)
                .is_err()
        );
        assert!(
            registry
                .master_source(&project, grant.token, full.deliverable_id)
                .is_ok()
        );
    }
}
