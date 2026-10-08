use motionwright_domain::Project;
use motionwright_native::production::{
    MltAvMasterEvidence, read_verified_production_artifact, verified_native_media_path,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, Write},
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex},
};
use uuid::Uuid;

const MAX_MASTER_BYTES: u64 = 1024 * 1024 * 1024;
/// Small-file review only. Long masters are delivered via streaming disk copy,
/// never piped wholesale through WebView IPC.
pub const MAX_BROWSER_REVIEW_BYTES: u64 = 16 * 1024 * 1024;
const MAX_DELIVERY_HANDLES: usize = 32;
const MAX_DESTINATION_BYTES: usize = 2048;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MasterExportRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub effect_grant: Uuid,
    pub export_token: Uuid,
    pub destination: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MasterReviewRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub export_token: Uuid,
}

#[derive(Debug, Serialize)]
pub struct MasterExportReceipt {
    pub destination: String,
    pub size_bytes: u64,
    pub sha256: String,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub source_current: bool,
}

#[derive(Debug, Clone)]
struct DeliveryHandle {
    token: Uuid,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    owner_root: PathBuf,
    relative_path: String,
    sha256: String,
}

#[derive(Clone, Default)]
pub struct NativeMasterDeliveryRegistry {
    entries: Arc<Mutex<VecDeque<DeliveryHandle>>>,
}

fn valid_sha256(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

impl NativeMasterDeliveryRegistry {
    /// Only the trusted post-coordinator path can mint an export token. The
    /// WebView can never supply master artifact paths, hashes or provenance.
    pub fn register(&self, owner_root: &Path, master: &MltAvMasterEvidence) -> Option<Uuid> {
        if master.master.get("profile")?.as_str()? != "h264-aac-mp4"
            || master.frame_count == 0
            || master.frame_count > 36_000
        {
            return None;
        }
        let id = Uuid::parse_str(master.project_resource.strip_prefix("project:")?).ok()?;
        let artifact = master.master.get("artifact")?;
        let relative_path = artifact.get("path")?.as_str()?;
        let sha256 = artifact.get("sha256")?.as_str()?;
        if !valid_sha256(sha256) || relative_path.is_empty() || relative_path.len() > 4096 {
            return None;
        }
        let token = Uuid::new_v4();
        let entry = DeliveryHandle {
            token,
            project_id: id,
            generation: master.generation,
            revision: master.revision,
            deliverable_id: master.deliverable_id,
            owner_root: owner_root.to_path_buf(),
            relative_path: relative_path.to_owned(),
            sha256: sha256.to_owned(),
        };
        let mut entries = self.entries.lock().ok()?;
        if entries.len() >= MAX_DELIVERY_HANDLES {
            entries.pop_front();
        }
        entries.push_back(entry);
        Some(token)
    }

    /// Resolve a version-bound, already authenticated native master handle.
    /// This read-only step never reveals its owner root or output path to UI.
    pub fn resolve(&self, project: &Project, token: Uuid) -> Result<DeliverySource, String> {
        let entry = self
            .entries
            .lock()
            .map_err(|_| "Native AV delivery registry is unavailable.")?
            .iter()
            .find(|entry| entry.token == token)
            .cloned()
            .ok_or("No session-authorized native AV master is available.")?;
        if entry.project_id != project.id
            || entry.generation != project.generation
            || entry.revision != project.revision
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == entry.deliverable_id)
        {
            return Err(
                "Native master export requires the exact current project and output profile."
                    .into(),
            );
        }
        Ok(DeliverySource {
            owner_root: entry.owner_root,
            relative_path: entry.relative_path,
            sha256: entry.sha256,
            revision: entry.revision,
            deliverable_id: entry.deliverable_id,
        })
    }
}

pub struct DeliverySource {
    owner_root: PathBuf,
    relative_path: String,
    sha256: String,
    revision: u64,
    deliverable_id: Uuid,
}

fn validated_destination(value: &str) -> Result<PathBuf, String> {
    if value.is_empty()
        || value.len() > MAX_DESTINATION_BYTES
        || value.chars().any(char::is_control)
    {
        return Err("A bounded absolute MP4 export destination is required.".into());
    }
    let destination = PathBuf::from(value);
    if !destination.is_absolute()
        || destination
            .extension()
            .and_then(|part| part.to_str())
            .is_none_or(|extension| !extension.eq_ignore_ascii_case("mp4"))
    {
        return Err("Export destination must be an absolute path ending in .mp4.".into());
    }
    let parent = destination
        .parent()
        .ok_or("MP4 destination has no parent directory.")?;
    let mut path = PathBuf::new();
    for component in parent.components() {
        match component {
            Component::Prefix(prefix) => path.push(prefix.as_os_str()),
            Component::RootDir => path.push(component.as_os_str()),
            Component::Normal(name) => path.push(name),
            Component::CurDir | Component::ParentDir => {
                return Err("MP4 destination may not contain dot or parent traversal.".into());
            }
        }
        let metadata = fs::symlink_metadata(&path)
            .map_err(|_| "MP4 export parent directory does not exist.")?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err("MP4 export parent may not traverse symlinks or non-directories.".into());
        }
    }
    if destination
        .file_name()
        .and_then(|name| name.to_str())
        .is_none_or(|name| name.len() > 255 || name.trim().is_empty())
    {
        return Err("MP4 export filename is invalid.".into());
    }
    Ok(destination)
}

impl DeliverySource {
    /// Review a *small* completed native master without exporting to a user
    /// path. One immutable session token selects the backend-only source;
    /// bytes are SHA-256 checked before binary IPC can expose them.
    pub fn review_bytes(&self) -> Result<Vec<u8>, String> {
        let bytes = read_verified_production_artifact(
            &self.owner_root,
            &self.relative_path,
            &self.sha256,
            MAX_BROWSER_REVIEW_BYTES,
        )
        .map_err(|_| {
            "MP4 review needs a valid SHA-256-verified owner artifact of at most 16 MiB. For longer masters use Export verified MP4."
                .to_string()
        })?;
        if bytes.get(4..8) != Some(&b"ftyp"[..]) {
            return Err("Native master review rejected invalid MP4 file signature.".into());
        }
        Ok(bytes)
    }

    /// Copy only the SHA-256-verified canonical owner artifact. Existing user
    /// files are NEVER overwritten, and source paths stay backend-private.
    pub fn copy_to(&self, destination: &str) -> Result<MasterExportReceipt, String> {
        let destination = validated_destination(destination)?;
        let original = verified_native_media_path(
            &self.owner_root,
            &self.relative_path,
            &self.sha256,
            MAX_MASTER_BYTES,
        )
        .map_err(|_| "Canonical MP4 is missing, unsafe or failed source SHA-256 verification.")?;
        let expected_bytes = fs::metadata(&original)
            .map_err(|_| "Verified MP4 size is unavailable.")?
            .len();

        let mut input = File::open(&original).map_err(|_| "Canonical MP4 cannot be opened.")?;
        let mut magic = [0u8; 12];
        input
            .read_exact(&mut magic)
            .map_err(|_| "Master MP4 header is incomplete.")?;
        if &magic[4..8] != b"ftyp" {
            return Err("Canonical AV master lacks the expected MP4 file signature.".into());
        }
        input
            .rewind()
            .map_err(|_| "Canonical MP4 could not be rewound.")?;

        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut output = options.open(&destination).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "MP4 destination already exists; Motionwright never overwrites user files."
                    .to_string()
            } else {
                "Unable to create the chosen MP4 export destination.".to_string()
            }
        })?;
        let transfer = (|| -> Result<u64, String> {
            let mut copied = 0_u64;
            let mut digest = Sha256::new();
            let mut buffer = [0u8; 128 * 1024];
            loop {
                let read = input
                    .read(&mut buffer)
                    .map_err(|_| "MP4 source read failed.")?;
                if read == 0 {
                    break;
                }
                copied = copied
                    .checked_add(read as u64)
                    .ok_or("MP4 export byte count overflow.")?;
                if copied > expected_bytes || copied > MAX_MASTER_BYTES {
                    return Err("MP4 source changed or exceeded its recorded byte budget.".into());
                }
                output
                    .write_all(&buffer[..read])
                    .map_err(|_| "MP4 export write failed.")?;
                digest.update(&buffer[..read]);
            }
            output
                .sync_all()
                .map_err(|_| "MP4 export durability sync failed.")?;
            if copied != expected_bytes || hex::encode(digest.finalize()) != self.sha256 {
                return Err("MP4 export SHA-256 differs from the verified native master.".into());
            }
            Ok(copied)
        })();
        drop(output);
        let size_bytes = match transfer {
            Ok(size) => size,
            Err(reason) => {
                // Only the freshly-created failed destination is removed;
                // nothing pre-existing is ever modified or deleted.
                let _ = fs::remove_file(&destination);
                return Err(reason);
            }
        };
        Ok(MasterExportReceipt {
            destination: destination.to_string_lossy().to_string(),
            size_bytes,
            sha256: self.sha256.clone(),
            revision: self.revision,
            deliverable_id: self.deliverable_id,
            source_current: true,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn artifact_for(project: &Project, content: &[u8]) -> MltAvMasterEvidence {
        serde_json::from_value(json!({
            "project_resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "deliverable_id": project.deliverables[0].id,
            "motion_segment_id": "rendered-scope",
            "frame_rate": {"num": 30, "den": 1},
            "frame_count": 60,
            "mezzanine": {"profile": "ffv1"},
            "source_audio": {"relative_path": "voice.wav", "sha256": "a".repeat(64), "sample_rate": 48000, "channels": 2},
            "master": {
                "profile": "h264-aac-mp4",
                "frame_count": 60,
                "artifact": {"path": "signed-master.mp4", "sha256": hex::encode(Sha256::digest(content))}
            },
            "decoded_audio": {"sha256": "b".repeat(64)},
            "sync": null
        })).unwrap()
    }

    fn project() -> Project {
        Project::new("Native AV delivery fixture").unwrap()
    }

    #[test]
    fn exact_native_master_can_export_verified_bytes_without_overwriting() {
        let owner = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let project = project();
        let content = b"\0\0\0\x18ftypisom\0\0\0\0video-bytes";
        fs::write(owner.path().join("signed-master.mp4"), content).unwrap();
        let evidence = artifact_for(&project, content);
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry.register(owner.path(), &evidence).unwrap();
        let source = registry.resolve(&project, token).unwrap();
        assert_eq!(source.review_bytes().unwrap(), content);
        let destination = target.path().join("my-native-master.mp4");
        let first = source.copy_to(destination.to_str().unwrap()).unwrap();
        assert_eq!(first.revision, project.revision);
        assert_eq!(first.sha256, hex::encode(Sha256::digest(content)));
        assert_eq!(fs::read(&destination).unwrap(), content);
        assert!(source.copy_to(destination.to_str().unwrap()).is_err());
        assert_eq!(fs::read(&destination).unwrap(), content);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&destination).unwrap().permissions().mode() & 0o077,
                0
            );
        }
    }

    #[test]
    fn rejects_forged_session_wrong_revision_and_changed_canonical_bytes() {
        let owner = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let project = project();
        let content = b"\0\0\0\x18ftypisom\0\0\0\0video-bytes";
        fs::write(owner.path().join("signed-master.mp4"), content).unwrap();
        let evidence = artifact_for(&project, content);
        let registry = NativeMasterDeliveryRegistry::default();
        let mut forged = evidence.clone();
        forged.master["profile"] = json!("arbitrary-codec");
        assert!(registry.register(owner.path(), &forged).is_none());
        forged = evidence.clone();
        forged.master["artifact"]["sha256"] = json!("not-a-digest");
        assert!(registry.register(owner.path(), &forged).is_none());
        let token = registry.register(owner.path(), &evidence).unwrap();
        assert!(registry.resolve(&project, Uuid::new_v4()).is_err());
        let mut changed = project.clone();
        changed.revision += 1;
        assert!(registry.resolve(&changed, token).is_err());

        fs::write(owner.path().join("signed-master.mp4"), b"bad-source").unwrap();
        let source = registry.resolve(&project, token).unwrap();
        assert!(source.review_bytes().is_err());
        assert!(
            source
                .copy_to(
                    target
                        .path()
                        .join("should-not-export.mp4")
                        .to_str()
                        .unwrap()
                )
                .is_err()
        );
        assert!(!target.path().join("should-not-export.mp4").exists());
        assert!(validated_destination("../relative.mp4").is_err());
    }

    #[test]
    fn browser_review_refuses_oversized_or_invalid_mp4_without_reading_it() {
        let owner = tempfile::tempdir().unwrap();
        let project = project();
        let invalid = b"this is not an MP4 but has a valid SHA-256 hash";
        let source_file = owner.path().join("signed-master.mp4");
        fs::write(&source_file, invalid).unwrap();
        let registry = NativeMasterDeliveryRegistry::default();
        let token = registry
            .register(owner.path(), &artifact_for(&project, invalid))
            .unwrap();
        let media = registry.resolve(&project, token).unwrap();
        assert!(media.review_bytes().is_err());

        let much_larger = MAX_BROWSER_REVIEW_BYTES + 1;
        File::options()
            .write(true)
            .open(&source_file)
            .unwrap()
            .set_len(much_larger)
            .unwrap();
        let error = media.review_bytes().unwrap_err();
        assert!(error.contains("16 MiB"));
        assert!(error.contains("Export verified MP4"));
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_destination_parents_and_target_collisions() {
        use std::os::unix::fs::symlink;
        let target = tempfile::tempdir().unwrap();
        let linked = target.path().join("parent-link");
        let real = target.path().join("real");
        fs::create_dir(&real).unwrap();
        symlink(&real, &linked).unwrap();
        assert!(validated_destination(linked.join("master.mp4").to_str().unwrap()).is_err());
        assert!(validated_destination(real.join("master.mp4").to_str().unwrap()).is_ok());
    }
}
