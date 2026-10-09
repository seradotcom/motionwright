//! Bounded, project-version-bound read-only integrity inspection of
//! application-owned content-addressed asset blobs. This is NOT Project Graph
//! admission, dependency provenance or third-party ownership certification.
use super::*;
use std::collections::HashMap;

pub const MAX_ASSET_INTEGRITY_PAGE: usize = 16;
pub const MAX_INTEGRITY_FILE_BYTES: u64 = 256 * 1024 * 1024;
pub const MAX_INTEGRITY_PAGE_BYTES: u64 = 256 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AssetIntegrityStatus {
    Verified,
    Missing,
    Corrupt,
    Unsafe,
    Unreadable,
    NotContentAddressed,
    DeferredByBudget,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AssetIntegrityRecord {
    pub asset_id: Uuid,
    pub status: AssetIntegrityStatus,
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AssetIntegrityPage {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub total_assets: usize,
    pub items: Vec<AssetIntegrityRecord>,
    pub next: Option<usize>,
    pub complete: bool,
    /// How many actual asset bytes this particular page hashed.
    pub checked_bytes: u64,
}

/// All file paths are derived internally from the project's versioned digest.
/// Do not expose filesystem paths or digest-bearing source inputs to the UI.
impl Store {
    pub fn inspect_asset_integrity_page(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        offset: Option<usize>,
        limit: usize,
    ) -> Result<AssetIntegrityPage> {
        if !(1..=MAX_ASSET_INTEGRITY_PAGE).contains(&limit) {
            return Err(StorageError::Domain(DomainError::Invalid(
                "Asset integrity page size must be 1–16".into(),
            )));
        }
        let project = self.load_project(project_id)?;
        if project.resource_key() != expected.resource || project.generation != expected.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if project.revision != expected.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }
        let start = offset.unwrap_or(0);
        if (offset.is_some() && (start == 0 || start >= project.assets.len() || start % limit != 0))
            || start > project.assets.len()
        {
            return Err(StorageError::Domain(DomainError::Invalid(
                "Asset integrity cursor does not advance within the exact project".into(),
            )));
        }
        let end = start.saturating_add(limit).min(project.assets.len());
        let mut remaining = MAX_INTEGRITY_PAGE_BYTES;
        let mut checked_bytes = 0;
        // One content-addressed source may be referenced by multiple assets.
        // Reuse the same verified result in a page, never skip the first hash.
        let mut checked_sources: HashMap<&str, (AssetIntegrityStatus, Option<u64>)> =
            HashMap::new();
        let mut items = Vec::with_capacity(end - start);
        for asset in &project.assets[start..end] {
            let (status, size_bytes) = match asset.content_sha256.as_deref() {
                None => (AssetIntegrityStatus::NotContentAddressed, None),
                Some(digest) => {
                    if let Some(cached) = checked_sources.get(digest) {
                        *cached
                    } else {
                        let before = remaining;
                        let result = self.inspect_integrity_digest(digest, &mut remaining);
                        checked_bytes += before - remaining;
                        checked_sources.insert(digest, result);
                        result
                    }
                }
            };
            items.push(AssetIntegrityRecord {
                asset_id: asset.id,
                status,
                size_bytes,
            });
        }
        Ok(AssetIntegrityPage {
            project_id,
            generation: project.generation,
            revision: project.revision,
            total_assets: project.assets.len(),
            items,
            next: (end < project.assets.len()).then_some(end),
            complete: end == project.assets.len(),
            checked_bytes,
        })
    }

    fn inspect_integrity_digest(
        &self,
        digest: &str,
        remaining: &mut u64,
    ) -> (AssetIntegrityStatus, Option<u64>) {
        let path = match self.blob_path(digest) {
            Ok(path) => path,
            Err(_) => return (AssetIntegrityStatus::Unsafe, None),
        };
        // A content-addressed leaf alone cannot provide path isolation when
        // an ancestor shard was swapped for a symlink.
        for directory in [
            self.blob_root.clone(),
            self.blob_root.join("sha256"),
            self.blob_root.join("sha256").join(&digest[..2]),
        ] {
            match fs::symlink_metadata(&directory) {
                Ok(meta) if meta.is_dir() && !meta.file_type().is_symlink() => {}
                Ok(_) => return (AssetIntegrityStatus::Unsafe, None),
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                    return (AssetIntegrityStatus::Missing, None);
                }
                Err(_) => return (AssetIntegrityStatus::Unreadable, None),
            }
        }
        let meta = match fs::symlink_metadata(&path) {
            Ok(meta) if meta.file_type().is_symlink() || !meta.is_file() => {
                return (AssetIntegrityStatus::Unsafe, None);
            }
            Ok(meta) => meta,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                return (AssetIntegrityStatus::Missing, None);
            }
            Err(_) => return (AssetIntegrityStatus::Unreadable, None),
        };
        let size = meta.len();
        if size > MAX_INTEGRITY_FILE_BYTES || size > *remaining {
            return (AssetIntegrityStatus::DeferredByBudget, Some(size));
        }

        let mut file = match File::open(&path) {
            Ok(file) => file,
            Err(_) => return (AssetIntegrityStatus::Unreadable, Some(size)),
        };
        let opened = match file.metadata() {
            Ok(opened) if opened.is_file() && opened.len() == size => opened,
            _ => return (AssetIntegrityStatus::Unreadable, Some(size)),
        };
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if meta.ino() != opened.ino() || meta.dev() != opened.dev() {
                return (AssetIntegrityStatus::Unsafe, Some(size));
            }
        }
        let mut sha = Sha256::new();
        let mut seen = 0_u64;
        let mut buffer = [0u8; 128 * 1024];
        loop {
            let read = match file.read(&mut buffer) {
                Ok(read) => read,
                Err(_) => return (AssetIntegrityStatus::Unreadable, Some(size)),
            };
            if read == 0 {
                break;
            }
            seen = seen.saturating_add(read as u64);
            if seen > size || seen > MAX_INTEGRITY_FILE_BYTES {
                return (AssetIntegrityStatus::Corrupt, Some(size));
            }
            sha.update(&buffer[..read]);
        }
        *remaining -= size;
        let unchanged = fs::symlink_metadata(&path).ok().is_some_and(|later| {
            if !later.is_file() || later.file_type().is_symlink() || later.len() != size {
                return false;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                later.ino() == opened.ino() && later.dev() == opened.dev()
            }
            #[cfg(not(unix))]
            {
                true
            }
        });
        if !unchanged {
            return (AssetIntegrityStatus::Unsafe, Some(size));
        }
        if seen != size || hex::encode(sha.finalize()) != digest {
            (AssetIntegrityStatus::Corrupt, Some(size))
        } else {
            (AssetIntegrityStatus::Verified, Some(size))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Asset, Change};

    fn add_asset(
        store: &mut Store,
        project: &mut Project,
        name: &str,
        digest: Option<String>,
    ) -> Uuid {
        let id = Uuid::now_v7();
        let next = store
            .apply(
                project.id,
                &RevisionStamp::from(&*project),
                &format!("integrity-test-add-{id}"),
                &Change::AddAsset {
                    asset: Asset {
                        id,
                        name: name.into(),
                        media_type: "image/png".into(),
                        content_sha256: digest,
                        source_revision: Some("local-import".into()),
                    },
                },
            )
            .unwrap();
        *project = next.project;
        id
    }

    #[test]
    fn exact_versioned_integrity_detects_missing_tampered_unbound_and_verified_assets() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("projects.sqlite3")).unwrap();
        let mut project = store.create_named_project("Asset integrity").unwrap();
        let source = temp.path().join("imported.png");
        fs::write(&source, b"real imported media source bytes").unwrap();
        let original = store.ingest_blob_file(&source).unwrap();
        let valid = add_asset(
            &mut store,
            &mut project,
            "verified.png",
            Some(original.sha256.clone()),
        );
        let second = add_asset(
            &mut store,
            &mut project,
            "duplicate-ref.png",
            Some(original.sha256.clone()),
        );
        let unbound = add_asset(&mut store, &mut project, "planned.png", None);
        let lost_source = temp.path().join("lost.png");
        fs::write(&lost_source, b"original bytes before moving").unwrap();
        let lost = store.ingest_blob_file(&lost_source).unwrap();
        let missing = add_asset(
            &mut store,
            &mut project,
            "unavailable.png",
            Some(lost.sha256.clone()),
        );
        fs::rename(
            store.blob_path(&lost.sha256).unwrap(),
            temp.path().join("moved-asset-for-test"),
        )
        .unwrap();
        let other_source = temp.path().join("damaged.png");
        fs::write(&other_source, b"original digest-bound bytes").unwrap();
        let other = store.ingest_blob_file(&other_source).unwrap();
        let corrupt = add_asset(
            &mut store,
            &mut project,
            "corrupted.png",
            Some(other.sha256.clone()),
        );
        fs::write(
            store.blob_path(&other.sha256).unwrap(),
            b"tampered media bytes",
        )
        .unwrap();

        let first = store
            .inspect_asset_integrity_page(project.id, &RevisionStamp::from(&project), None, 2)
            .unwrap();
        assert_eq!(first.total_assets, 5);
        assert_eq!(first.next, Some(2));
        assert!(!first.complete);
        assert_eq!(first.items[0].asset_id, valid);
        assert_eq!(first.items[1].asset_id, second);
        assert_eq!(first.items[0].status, AssetIntegrityStatus::Verified);
        assert_eq!(first.items[1].status, AssetIntegrityStatus::Verified);
        assert_eq!(first.checked_bytes, original.size_bytes); // deduplicated source
        let second_page = store
            .inspect_asset_integrity_page(project.id, &RevisionStamp::from(&project), first.next, 2)
            .unwrap();
        assert_eq!(second_page.items[0].asset_id, unbound);
        assert_eq!(
            second_page.items[0].status,
            AssetIntegrityStatus::NotContentAddressed
        );
        assert_eq!(second_page.items[1].asset_id, missing);
        assert_eq!(second_page.items[1].status, AssetIntegrityStatus::Missing);
        let third = store
            .inspect_asset_integrity_page(
                project.id,
                &RevisionStamp::from(&project),
                second_page.next,
                2,
            )
            .unwrap();
        assert_eq!(third.items[0].asset_id, corrupt);
        assert_eq!(third.items[0].status, AssetIntegrityStatus::Corrupt);
        assert!(third.complete);
        assert!(third.next.is_none());
        assert_eq!(
            store.load_project(project.id).unwrap().revision,
            project.revision
        );
        assert!(
            store
                .inspect_asset_integrity_page(
                    project.id,
                    &RevisionStamp::from(&project),
                    Some(1),
                    2
                )
                .is_err()
        );
        let advanced = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "integrity-stale-rename",
                &Change::RenameProject {
                    title: "changed source".into(),
                },
            )
            .unwrap()
            .project;
        assert!(advanced.revision > project.revision);
        assert!(
            store
                .inspect_asset_integrity_page(
                    project.id,
                    &RevisionStamp::from(&project),
                    Some(2),
                    2
                )
                .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn content_addressed_symlinks_are_unsafe_and_not_followed() {
        use std::os::unix::fs::symlink;
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("projects.sqlite3")).unwrap();
        let mut project = store.create_named_project("Unsafe shard").unwrap();
        let source = temp.path().join("good.png");
        fs::write(&source, b"stored, digest-verified content").unwrap();
        let blob = store.ingest_blob_file(&source).unwrap();
        let id = add_asset(
            &mut store,
            &mut project,
            "source.png",
            Some(blob.sha256.clone()),
        );
        let leaf = store.blob_path(&blob.sha256).unwrap();
        fs::remove_file(&leaf).unwrap();
        symlink(&source, &leaf).unwrap();
        let report = store
            .inspect_asset_integrity_page(project.id, &RevisionStamp::from(&project), None, 1)
            .unwrap();
        assert_eq!(report.items[0].asset_id, id);
        assert_eq!(report.items[0].status, AssetIntegrityStatus::Unsafe);
    }

    #[test]
    fn empty_projects_are_completely_checked_without_fake_evidence() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("projects.sqlite3")).unwrap();
        let project = store.create_named_project("Empty").unwrap();
        let page = store
            .inspect_asset_integrity_page(project.id, &RevisionStamp::from(&project), None, 8)
            .unwrap();
        assert!(page.complete);
        assert_eq!(page.total_assets, 0);
        assert!(page.items.is_empty());
        assert_eq!(page.checked_bytes, 0);
    }
}
