//! Owner-local read-only source for a saved NativeCapsule.
//!
//! The caller must already have Host/project read authority. Capsule UUIDs are
//! selectors inside that authority, never credentials. Nothing in this module
//! installs a Native operation, exposes bytes to IPC, or upgrades fidelity.
use crate::StudioService;
use motionwright_domain::{DomainError, RevisionStamp};
use motionwright_storage::{Result as StorageResult, StorageError};
use uuid::Uuid;

/// Ceiling for one in-memory source inspection. Trusted format decoders should
/// impose stricter format-specific limits; untrusted args cannot raise this cap.
pub const MAX_NATIVE_CAPSULE_INSPECT_BYTES: u64 = 32 * 1024 * 1024;

/// Verified, revision-bound source captured from a real CAS blob.
///
/// No Serialize/Deserialize or filesystem path: a Native/Host caller must
/// perform its own permission check and invoke only a fixed trusted decoder.
pub struct VerifiedNativeCapsuleSource {
    pub stamp: RevisionStamp,
    pub capsule_id: Uuid,
    pub scene_id: Uuid,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    /// Authored MIME declaration, NOT evidence that the bytes match a format.
    pub media_type: String,
    bytes: Vec<u8>,
}

impl VerifiedNativeCapsuleSource {
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn byte_len(&self) -> usize {
        self.bytes.len()
    }
}

impl StudioService {
    /// Read one original, bounded CAS source belonging to the exact saved
    /// Project/capsule/asset identity. Never returns a receipt or write lease.
    ///
    /// This is an owner-local input seam, not an SDK command. A caller with
    /// access to an unauthenticated project/capsule UUID must *not* expose it
    /// as an IPC endpoint. Host authorizes the project before reaching here.
    ///
    /// Full Project re-read after the digest-verified capture also fences a
    /// concurrent Store connection. The returned stamp is an observation,
    /// not future mutation authority; changes still require a fresh CAS.
    pub fn verified_native_capsule_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        capsule_id: Uuid,
    ) -> StorageResult<VerifiedNativeCapsuleSource> {
        let store = self.store.lock();
        let project = store.load_project(project_id)?;
        let stamp = project.stamp();
        if expected.resource != stamp.resource || expected.generation != stamp.generation {
            return Err(StorageError::GenerationConflict);
        }
        if expected.revision != stamp.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: stamp.revision,
            });
        }
        let capsule = project
            .production_design
            .capsules
            .iter()
            .find(|capsule| capsule.id == capsule_id)
            .ok_or(StorageError::NotFound)?;
        capsule.validate(&project.scenes, &project.assets)?;
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == capsule.source_asset_id)
            .ok_or(StorageError::NotFound)?;
        if asset.content_sha256.as_deref() != Some(capsule.source_sha256.as_str()) {
            return Err(StorageError::Domain(DomainError::Invalid(
                "Saved Native capsule source asset digest differs".into(),
            )));
        }

        let limit = MAX_NATIVE_CAPSULE_INSPECT_BYTES.min(self.import_limits.source_bytes());
        let bytes = store.read_blob(&capsule.source_sha256, limit)?;
        let latest = store.load_project(project_id)?;
        if latest != project {
            let now = latest.stamp();
            if now.resource != stamp.resource || now.generation != stamp.generation {
                return Err(StorageError::GenerationConflict);
            }
            if now.revision == stamp.revision {
                return Err(StorageError::InvalidBackup(
                    "Native capsule changed outside its revisioned Project".into(),
                ));
            }
            return Err(StorageError::Conflict {
                expected: stamp.revision,
                actual: now.revision,
            });
        }
        Ok(VerifiedNativeCapsuleSource {
            stamp,
            capsule_id: capsule.id,
            scene_id: capsule.scene_id,
            source_asset_id: asset.id,
            source_sha256: capsule.source_sha256.clone(),
            media_type: asset.media_type.clone(),
            bytes,
        })
    }
}
