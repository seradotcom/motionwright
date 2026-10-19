//! Persist only an owner-selected Semwright connection *reference*, never its
//! session ticket, broker material, driver receipts or executable contents.
//! Every subsequent native command still reloads and validates the canonical
//! connection, resource, executable digest and Semwright policy gates.
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

const REGISTRY_SCHEMA: u32 = 1;
const MAX_CONFIG_BYTES: u64 = 8 * 1024;

#[derive(Debug, Clone)]
pub(crate) struct RuntimeConnectionRegistry {
    root: PathBuf,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SavedConnectionReference {
    schema_version: u32,
    project_id: Uuid,
    generation: Uuid,
    connection_file: PathBuf,
}

fn is_link_or_reparse(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
        metadata.file_type().is_symlink()
            || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

impl RuntimeConnectionRegistry {
    pub(crate) fn new(app_data_dir: &Path) -> Result<Self, String> {
        let root = app_data_dir.join("owner-native-connections");
        fs::create_dir_all(&root).map_err(|_| "Runtime settings directory could not be created")?;
        let metadata = fs::symlink_metadata(&root)
            .map_err(|_| "Runtime settings directory could not be inspected")?;
        if !metadata.is_dir() || is_link_or_reparse(&metadata) {
            return Err("Runtime settings directory is not a regular directory".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
                .map_err(|_| "Runtime settings directory permissions are unsafe")?;
        }
        // Tauri may supply a trusted data-directory alias. Resolve it once so
        // an existing registry does not follow a later retarget of that alias.
        let root = fs::canonicalize(&root)
            .map_err(|_| "Runtime settings directory could not be resolved")?;
        let registry = Self { root };
        registry.validate_root()?;
        Ok(registry)
    }

    fn validate_root(&self) -> Result<(), String> {
        let metadata = fs::symlink_metadata(&self.root)
            .map_err(|_| "Runtime settings directory is unavailable")?;
        if !metadata.is_dir() || is_link_or_reparse(&metadata) {
            return Err("Runtime settings directory is not a regular directory".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o077 != 0 {
                return Err("Runtime settings directory permissions are unsafe".into());
            }
        }
        let current = fs::canonicalize(&self.root)
            .map_err(|_| "Runtime settings directory could not be resolved")?;
        if current != self.root {
            return Err("Runtime settings directory location has changed".into());
        }
        // This rejects redirection already present before the operation.
        // Path checks do not provide atomic directory-handle confinement.
        Ok(())
    }

    fn destination(&self, project_id: Uuid) -> PathBuf {
        self.root.join(format!("{}.json", project_id.simple()))
    }

    pub(crate) fn resolve(
        &self,
        project_id: Uuid,
        generation: Uuid,
    ) -> Result<Option<PathBuf>, String> {
        self.validate_root()?;
        let destination = self.destination(project_id);
        let bytes = match fs::symlink_metadata(&destination) {
            Ok(metadata) => {
                if !metadata.is_file()
                    || metadata.file_type().is_symlink()
                    || metadata.len() == 0
                    || metadata.len() > MAX_CONFIG_BYTES
                {
                    return Err("Saved Semwright connection reference is invalid".into());
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    if metadata.permissions().mode() & 0o077 != 0 {
                        return Err(
                            "Saved Semwright connection reference has unsafe permissions".into(),
                        );
                    }
                }
                fs::read(&destination)
                    .map_err(|_| "Saved Semwright connection reference is unreadable")?
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                // An explicitly configured legacy environment connection is
                // still respected. A saved selection takes precedence.
                return Ok(std::env::var_os("MOTIONWRIGHT_SEMWRIGHT_CONNECTION").map(PathBuf::from));
            }
            Err(_) => return Err("Saved Semwright connection reference cannot be inspected".into()),
        };
        let selected: SavedConnectionReference = serde_json::from_slice(&bytes)
            .map_err(|_| "Saved Semwright connection reference is malformed")?;
        if selected.schema_version != REGISTRY_SCHEMA || selected.project_id != project_id {
            return Err("Saved Semwright connection reference has incorrect scope".into());
        }
        if selected.generation != generation {
            return Err(
                "Saved Semwright connection belongs to an earlier project generation".into(),
            );
        }
        if !selected.connection_file.is_absolute() {
            return Err("Saved Semwright connection path must be absolute".into());
        }
        Ok(Some(selected.connection_file))
    }

    /// The selected path must already have been independently validated by
    /// ProductionConnection::load + resource match + read-only version probe.
    pub(crate) fn remember_verified(
        &self,
        project_id: Uuid,
        generation: Uuid,
        connection_file: &Path,
    ) -> Result<(), String> {
        self.validate_root()?;
        if !connection_file.is_absolute() {
            return Err("The selected Semwright file must have an absolute path".into());
        }
        let value = SavedConnectionReference {
            schema_version: REGISTRY_SCHEMA,
            project_id,
            generation,
            connection_file: connection_file.to_path_buf(),
        };
        let encoded = serde_json::to_vec(&value)
            .map_err(|_| "Semwright connection reference could not be serialized")?;
        if encoded.is_empty() || encoded.len() > MAX_CONFIG_BYTES as usize {
            return Err("Semwright connection reference exceeds the local storage budget".into());
        }
        let destination = self.destination(project_id);
        match fs::symlink_metadata(&destination) {
            Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
                return Err("Previous Semwright connection reference is not a regular file".into());
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => {
                return Err("Previous Semwright connection reference cannot be inspected".into());
            }
        }

        let temporary = self.root.join(format!(
            ".{}-{}.tmp",
            project_id.simple(),
            Uuid::now_v7().simple()
        ));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut handle = options
            .open(&temporary)
            .map_err(|_| "Semwright connection reference cannot be staged")?;
        let write_result = handle.write_all(&encoded).and_then(|()| handle.sync_all());
        drop(handle);
        if write_result.is_err() {
            let _ = fs::remove_file(&temporary);
            return Err("Semwright connection reference could not be saved".into());
        }
        // Rust's fs::rename replaces an existing regular file on supported
        // platforms, including Windows (MoveFileExW). Never delete the prior
        // owner-selected reference before promoting the staged replacement:
        // an AV/ACL/share-mode failure must leave the last valid pointer intact.
        if fs::rename(&temporary, &destination).is_err() {
            let _ = fs::remove_file(&temporary);
            return Err("Semwright connection reference could not be activated".into());
        }
        Ok(())
    }

    /// Remove only the application-owned reference, never the operator's
    /// Semwright connection file, Broker session or native job evidence.
    pub(crate) fn clear_saved(&self, project_id: Uuid) -> Result<bool, String> {
        self.validate_root()?;
        let destination = self.destination(project_id);
        match fs::symlink_metadata(&destination) {
            Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
                Err("Saved Semwright reference is not a regular file".into())
            }
            Ok(metadata) => {
                // A regular pathname alone is not proof of ownership.
                // Never delete forged, malformed, stale-format or otherwise
                // unbounded records: they may contain unrelated user bytes.
                if metadata.len() == 0 || metadata.len() > MAX_CONFIG_BYTES {
                    return Err("Saved Semwright reference is invalid".into());
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    if metadata.permissions().mode() & 0o077 != 0 {
                        return Err(
                            "Saved Semwright connection reference has unsafe permissions".into(),
                        );
                    }
                }
                let bytes = fs::read(&destination)
                    .map_err(|_| "Saved Semwright reference cannot be inspected")?;
                if bytes.is_empty() || bytes.len() as u64 > MAX_CONFIG_BYTES {
                    return Err("Saved Semwright reference is invalid".into());
                }
                let selected: SavedConnectionReference = serde_json::from_slice(&bytes)
                    .map_err(|_| "Saved Semwright reference is malformed")?;
                if selected.schema_version != REGISTRY_SCHEMA
                    || selected.project_id != project_id
                    || !selected.connection_file.is_absolute()
                {
                    return Err("Saved Semwright reference has incorrect scope".into());
                }
                fs::remove_file(&destination)
                    .map_err(|_| "Saved Semwright reference could not be cleared")?;
                Ok(true)
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(_) => Err("Saved Semwright reference could not be inspected".into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn saved_reference_is_project_and_generation_scoped_and_survives_reopen() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project_id = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let path = tmp.path().join("owner-connection.json");
        registry
            .remember_verified(project_id, generation, &path)
            .unwrap();
        let reopened = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        assert_eq!(
            reopened.resolve(project_id, generation).unwrap(),
            Some(path.clone())
        );
        assert!(reopened.resolve(project_id, Uuid::now_v7()).is_err());
        assert!(
            reopened
                .resolve(Uuid::now_v7(), generation)
                .unwrap()
                .is_none()
        );
        assert!(reopened.clear_saved(project_id).unwrap());
        assert!(!reopened.clear_saved(project_id).unwrap());
        assert!(reopened.resolve(project_id, generation).unwrap().is_none());
    }

    #[test]
    fn verified_replacement_is_persistent_and_no_runtime_secrets_are_copied() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project_id = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let first = tmp.path().join("first.json");
        let second = tmp.path().join("second.json");
        fs::write(&first, b"private-token-must-not-be-copied").unwrap();
        registry
            .remember_verified(project_id, generation, &first)
            .unwrap();
        registry
            .remember_verified(project_id, generation, &second)
            .unwrap();
        let reference = fs::read_to_string(registry.destination(project_id)).unwrap();
        assert!(!reference.contains("private-token-must-not-be-copied"));
        assert!(!reference.contains(first.to_str().unwrap()));
        assert_eq!(
            registry.resolve(project_id, generation).unwrap(),
            Some(second)
        );
    }

    #[cfg(unix)]
    #[test]
    fn saved_reference_symlink_and_world_readable_files_fail_closed() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project_id = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let path = registry.destination(project_id);
        symlink(tmp.path().join("outside.json"), &path).unwrap();
        assert!(registry.resolve(project_id, generation).is_err());
        assert!(
            registry
                .remember_verified(project_id, generation, &tmp.path().join("owner.json"))
                .is_err()
        );
        assert!(registry.clear_saved(project_id).is_err());
        fs::remove_file(&path).unwrap();
        registry
            .remember_verified(project_id, generation, &tmp.path().join("owner.json"))
            .unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(registry.resolve(project_id, generation).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn failed_windows_replace_preserves_last_verified_reference() {
        use std::os::windows::fs::OpenOptionsExt;

        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project_id = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let first = tmp.path().join("existing-owner.json");
        let replacement = tmp.path().join("replacement-owner.json");
        registry
            .remember_verified(project_id, generation, &first)
            .unwrap();
        let destination = registry.destination(project_id);
        let original_bytes = fs::read(&destination).unwrap();

        // Deny FILE_SHARE_DELETE (and all other sharing) so Windows cannot
        // replace the destination while the handle is held.
        let locked = OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&destination)
            .unwrap();
        assert!(
            registry
                .remember_verified(project_id, generation, &replacement)
                .is_err()
        );
        drop(locked);

        // The previous registration still works even though activation failed.
        assert_eq!(fs::read(&destination).unwrap(), original_bytes);
        assert_eq!(
            registry.resolve(project_id, generation).unwrap(),
            Some(first)
        );
        registry
            .remember_verified(project_id, generation, &replacement)
            .unwrap();
        assert_eq!(
            registry.resolve(project_id, generation).unwrap(),
            Some(replacement)
        );
    }

    #[test]
    fn forged_scope_and_corrupted_records_are_rejected() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project_id = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let path = registry.destination(project_id);
        fs::write(&path, b"{\"schema_version\":999}").unwrap();
        assert!(registry.resolve(project_id, generation).is_err());
        registry
            .remember_verified(project_id, generation, &tmp.path().join("owner.json"))
            .unwrap();
        let mut forged: serde_json::Value =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        forged["project_id"] = serde_json::json!(Uuid::now_v7());
        fs::write(&path, serde_json::to_vec(&forged).unwrap()).unwrap();
        assert!(registry.resolve(project_id, generation).is_err());
    }

    #[test]
    fn selecting_new_connection_never_overwrites_foreign_project_record() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project = Uuid::now_v7();
        let other_project = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let first = tmp.path().join("owner-selected.json");
        let replacement = tmp.path().join("replacement-selected.json");
        registry
            .remember_verified(project, generation, &first)
            .unwrap();
        let path = registry.destination(project);
        let legitimate = fs::read(&path).unwrap();

        // Two different foreign records. The second is intentionally not a
        // valid SavedConnectionReference, but still declares a distinct owner.
        // Neither may be replaced just because the filename names our project.
        for case in ["complete_foreign", "partial_foreign"] {
            let foreign = if case == "complete_foreign" {
                let mut payload: serde_json::Value = serde_json::from_slice(&legitimate).unwrap();
                payload["project_id"] = serde_json::json!(other_project);
                serde_json::to_vec(&payload).unwrap()
            } else {
                format!("{{\"project_id\":\"{other_project}\",\"foreign\":true}}").into_bytes()
            };
            fs::write(&path, &foreign).unwrap();
            let outcome = registry.remember_verified(project, generation, &replacement);
            assert!(outcome.is_err(), "foreign record was overwritten: {case}");
            assert_eq!(fs::read(&path).unwrap(), foreign, "case={case}");
            let mut entries: Vec<_> = fs::read_dir(&registry.root)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect();
            entries.sort();
            assert_eq!(
                entries,
                vec![path.file_name().unwrap().to_os_string()],
                "foreign replacement left private temporary files: {case}"
            );
        }

        // A legitimate record is still replaceable, including after an
        // independently authorized project-generation rotation.
        fs::write(&path, legitimate).unwrap();
        let next_generation = Uuid::now_v7();
        registry
            .remember_verified(project, next_generation, &replacement)
            .unwrap();
        assert_eq!(
            registry.resolve(project, next_generation).unwrap(),
            Some(replacement)
        );
        assert!(registry.resolve(project, generation).is_err());
    }

    #[test]
    fn clear_saved_rejects_corrupt_reference_without_deleting_bytes() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project = Uuid::now_v7();
        let destination = registry.destination(project);
        let malformed = b"{\"schema_version\":999,\"foreign\":true}";
        fs::write(&destination, malformed).unwrap();

        assert!(registry.clear_saved(project).is_err());
        assert_eq!(fs::read(&destination).unwrap(), malformed);
    }

    #[test]
    fn clear_saved_rejects_foreign_project_scope_without_deleting_bytes() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let destination = registry.destination(project);
        registry
            .remember_verified(project, generation, &tmp.path().join("owner.json"))
            .unwrap();
        let mut foreign: serde_json::Value =
            serde_json::from_slice(&fs::read(&destination).unwrap()).unwrap();
        foreign["project_id"] = serde_json::json!(Uuid::now_v7());
        let foreign_bytes = serde_json::to_vec(&foreign).unwrap();
        fs::write(&destination, &foreign_bytes).unwrap();

        assert!(registry.clear_saved(project).is_err());
        assert_eq!(fs::read(&destination).unwrap(), foreign_bytes);
    }

    #[test]
    fn clear_saved_rejects_relative_reference_without_deleting_bytes() {
        let tmp = TempDir::new().unwrap();
        let registry = RuntimeConnectionRegistry::new(tmp.path()).unwrap();
        let project = Uuid::now_v7();
        let generation = Uuid::now_v7();
        let destination = registry.destination(project);
        registry
            .remember_verified(project, generation, &tmp.path().join("owner.json"))
            .unwrap();
        let mut forged: serde_json::Value =
            serde_json::from_slice(&fs::read(&destination).unwrap()).unwrap();
        forged["connection_file"] = serde_json::json!("relative-foreign.json");
        let forged_bytes = serde_json::to_vec(&forged).unwrap();
        fs::write(&destination, &forged_bytes).unwrap();

        assert!(registry.clear_saved(project).is_err());
        assert_eq!(fs::read(&destination).unwrap(), forged_bytes);
    }

    #[test]
    fn missing_registry_root_rejects_all_operations() {
        let temporary = TempDir::new().unwrap();
        let base = fs::canonicalize(temporary.path()).unwrap();
        let registry = RuntimeConnectionRegistry::new(&base).unwrap();
        let project = Uuid::from_u128(1);
        let generation = Uuid::from_u128(2);
        let root = base.join("owner-native-connections");
        fs::remove_dir(&root).unwrap();

        let read = registry.resolve(project, generation);
        let write = registry.remember_verified(project, generation, &base.join("connection.json"));
        let clear = registry.clear_saved(project);

        assert!(!root.exists(), "an operation recreated the absent registry");
        assert!(
            read.is_err() && write.is_err() && clear.is_err(),
            "missing root accepted an operation: read={read:?}, write={write:?}, clear={clear:?}"
        );
    }

    #[cfg(unix)]
    mod root_integrity {
        use super::*;
        use std::ffi::OsString;
        use std::os::unix::fs::{PermissionsExt, symlink};

        const DIRECTORY: &str = "owner-native-connections";

        #[derive(Clone, Copy, Debug)]
        enum Operation {
            Read,
            Write,
            Clear,
        }

        #[derive(Debug, PartialEq, Eq)]
        struct Snapshot {
            entries: Vec<OsString>,
            record: Option<Vec<u8>>,
            mode: u32,
        }

        fn snapshot(root: &Path, project: Uuid) -> Snapshot {
            let mut entries: Vec<_> = fs::read_dir(root)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect();
            entries.sort_unstable();
            let record = match fs::read(root.join(format!("{}.json", project.simple()))) {
                Ok(bytes) => Some(bytes),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
                Err(error) => panic!("cannot snapshot record: {error}"),
            };
            Snapshot {
                entries,
                record,
                mode: fs::symlink_metadata(root).unwrap().permissions().mode() & 0o7777,
            }
        }

        struct Fixture {
            _temporary: TempDir,
            base: PathBuf,
            registry: RuntimeConnectionRegistry,
            project: Uuid,
            generation: Uuid,
        }

        impl Fixture {
            fn new() -> Self {
                let temporary = TempDir::new().unwrap();
                // Normalize only the fixture base, including macOS temp aliases.
                let base = fs::canonicalize(temporary.path()).unwrap();
                let app_data = base.join("original").join("app-data");
                let decoy_data = base.join("decoy").join("app-data");
                let registry = RuntimeConnectionRegistry::new(&app_data).unwrap();
                let decoy = RuntimeConnectionRegistry::new(&decoy_data).unwrap();
                let project = Uuid::from_u128(1);
                let generation = Uuid::from_u128(2);
                // Synthetic absolute references test this registry only. No
                // ProductionConnection, Broker or owner consent is claimed.
                for (item, name) in [(&registry, "original.json"), (&decoy, "decoy.json")] {
                    let selected = base.join(name);
                    item.remember_verified(project, generation, &selected)
                        .unwrap();
                    assert_eq!(item.resolve(project, generation).unwrap(), Some(selected));
                }
                Self {
                    _temporary: temporary,
                    base,
                    registry,
                    project,
                    generation,
                }
            }

            fn root(&self, tree: &str) -> PathBuf {
                self.base.join(tree).join("app-data").join(DIRECTORY)
            }

            fn apply(&self, operation: Operation) -> Result<(), String> {
                match operation {
                    Operation::Read => self
                        .registry
                        .resolve(self.project, self.generation)
                        .map(|_| ()),
                    Operation::Write => self.registry.remember_verified(
                        self.project,
                        self.generation,
                        &self.base.join("replacement.json"),
                    ),
                    Operation::Clear => self.registry.clear_saved(self.project).map(|_| ()),
                }
            }

            fn assert_rejected_unchanged(&self, operation: Operation, preserved: &Path) {
                let decoy = self.root("decoy");
                let original_before = snapshot(preserved, self.project);
                let decoy_before = snapshot(&decoy, self.project);
                let result = self.apply(operation);
                assert_eq!(
                    snapshot(preserved, self.project),
                    original_before,
                    "{operation:?} changed the preserved original registry"
                );
                assert_eq!(
                    snapshot(&decoy, self.project),
                    decoy_before,
                    "{operation:?} changed foreign bytes or left temporary entries"
                );
                assert!(
                    result.is_err(),
                    "{operation:?} accepted an invalid registry root"
                );
            }

            fn through_alias(&self) -> (RuntimeConnectionRegistry, PathBuf) {
                let alias = self.base.join("initial-alias");
                symlink(self.base.join("original").join("app-data"), &alias).unwrap();
                (RuntimeConnectionRegistry::new(&alias).unwrap(), alias)
            }
        }

        fn rejects_substitution(operation: Operation, ancestor: bool) {
            let fixture = Fixture::new();
            let root = fixture.root("original");
            let preserved;
            if ancestor {
                let original_parent = fixture.base.join("original");
                let preserved_parent = fixture.base.join("preserved");
                fs::rename(&original_parent, &preserved_parent).unwrap();
                symlink(fixture.base.join("decoy"), &original_parent).unwrap();
                preserved = preserved_parent.join("app-data").join(DIRECTORY);

                // Final root and record deliberately satisfy the old checks.
                let metadata = fs::symlink_metadata(&root).unwrap();
                assert!(metadata.is_dir() && !metadata.file_type().is_symlink());
                assert_eq!(metadata.permissions().mode() & 0o7777, 0o700);
                let record =
                    fs::symlink_metadata(fixture.registry.destination(fixture.project)).unwrap();
                assert!(record.is_file() && !record.file_type().is_symlink());
                assert_eq!(record.permissions().mode() & 0o7777, 0o600);
                assert_ne!(fs::canonicalize(&root).unwrap(), root);
            } else {
                preserved = fixture.base.join("preserved-root");
                fs::rename(&root, &preserved).unwrap();
                symlink(fixture.root("decoy"), &root).unwrap();
                assert!(
                    fs::symlink_metadata(&root)
                        .unwrap()
                        .file_type()
                        .is_symlink()
                );
            }
            fixture.assert_rejected_unchanged(operation, &preserved);
        }

        fn rejects_non_private_root(operation: Operation) {
            let fixture = Fixture::new();
            let root = fixture.root("original");
            fs::set_permissions(&root, fs::Permissions::from_mode(0o755)).unwrap();
            assert_eq!(snapshot(&root, fixture.project).mode, 0o755);
            // Rejection must preserve even the changed permissions.
            fixture.assert_rejected_unchanged(operation, &root);
        }

        #[test]
        fn read_rejects_root_substitution() {
            rejects_substitution(Operation::Read, false);
        }
        #[test]
        fn write_rejects_root_substitution() {
            rejects_substitution(Operation::Write, false);
        }
        #[test]
        fn clear_rejects_root_substitution() {
            rejects_substitution(Operation::Clear, false);
        }
        #[test]
        fn read_rejects_ancestor_substitution() {
            rejects_substitution(Operation::Read, true);
        }
        #[test]
        fn write_rejects_ancestor_substitution() {
            rejects_substitution(Operation::Write, true);
        }
        #[test]
        fn clear_rejects_ancestor_substitution() {
            rejects_substitution(Operation::Clear, true);
        }
        #[test]
        fn read_rejects_non_private_root() {
            rejects_non_private_root(Operation::Read);
        }
        #[test]
        fn write_rejects_non_private_root() {
            rejects_non_private_root(Operation::Write);
        }
        #[test]
        fn clear_rejects_non_private_root() {
            rejects_non_private_root(Operation::Clear);
        }

        #[test]
        fn initial_app_data_alias_supports_normal_round_trip() {
            let fixture = Fixture::new();
            let (registry, _) = fixture.through_alias();
            let decoy_before = snapshot(&fixture.root("decoy"), fixture.project);
            let replacement = fixture.base.join("replacement.json");
            registry
                .remember_verified(fixture.project, fixture.generation, &replacement)
                .unwrap();
            assert_eq!(
                registry
                    .resolve(fixture.project, fixture.generation)
                    .unwrap(),
                Some(replacement.clone())
            );
            assert_eq!(
                fixture
                    .registry
                    .resolve(fixture.project, fixture.generation)
                    .unwrap(),
                Some(replacement)
            );
            assert!(registry.clear_saved(fixture.project).unwrap());
            assert!(
                snapshot(&fixture.root("original"), fixture.project)
                    .entries
                    .is_empty()
            );
            assert_eq!(
                snapshot(&fixture.root("decoy"), fixture.project),
                decoy_before
            );
        }

        #[test]
        fn retargeted_initial_alias_keeps_original_physical_registry() {
            let fixture = Fixture::new();
            let (registry, alias) = fixture.through_alias();
            let decoy_before = snapshot(&fixture.root("decoy"), fixture.project);
            fs::remove_file(&alias).unwrap();
            symlink(fixture.base.join("decoy").join("app-data"), &alias).unwrap();

            let original = registry.resolve(fixture.project, fixture.generation);
            let replacement = fixture.base.join("replacement.json");
            let write =
                registry.remember_verified(fixture.project, fixture.generation, &replacement);
            let updated = registry.resolve(fixture.project, fixture.generation);
            let clear = registry.clear_saved(fixture.project);

            assert_eq!(
                snapshot(&fixture.root("decoy"), fixture.project),
                decoy_before
            );
            assert_eq!(original, Ok(Some(fixture.base.join("original.json"))));
            assert_eq!(write, Ok(()));
            assert_eq!(updated, Ok(Some(replacement)));
            assert_eq!(clear, Ok(true));
            assert!(
                snapshot(&fixture.root("original"), fixture.project)
                    .entries
                    .is_empty()
            );
        }
    }

    #[cfg(windows)]
    mod junction_integrity {
        use super::*;
        use std::ffi::OsString;
        use std::os::windows::fs::MetadataExt;

        const PROJECT_ID: Uuid = Uuid::from_u128(1);
        const GENERATION: Uuid = Uuid::from_u128(2);
        const REPARSE_POINT: u32 = 0x400;
        const DIRECTORY: u32 = 0x10;
        const CASE_IDS: [&str; 8] = [
            "root-read",
            "root-write",
            "root-clear",
            "ancestor-read",
            "ancestor-write",
            "ancestor-clear",
            "alias-initial",
            "alias-retarget",
        ];

        fn plain_metadata(path: &Path) -> fs::Metadata {
            let metadata = fs::symlink_metadata(path).expect("inspect ordinary fixture entry");
            assert_eq!(
                metadata.file_attributes() & REPARSE_POINT,
                0,
                "fixture entry is unexpectedly a reparse point: {}",
                path.display()
            );
            metadata
        }

        fn ensure_plain_directory(path: &Path) {
            match fs::create_dir(path) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(error) => panic!("cannot create {}: {error}", path.display()),
            }
            assert!(plain_metadata(path).is_dir());
        }

        fn require_vacant(path: &Path) {
            assert!(
                matches!(fs::symlink_metadata(path),
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound),
                "fixture destination must be absent: {}",
                path.display()
            );
        }

        fn require_junction_to(link: &Path, target: &Path) {
            let attributes = fs::symlink_metadata(link)
                .expect("runner must provision the junction")
                .file_attributes();
            assert_eq!(
                attributes & (REPARSE_POINT | DIRECTORY),
                REPARSE_POINT | DIRECTORY,
                "fixture entry must be a directory reparse point: {}",
                link.display()
            );
            assert_eq!(
                fs::canonicalize(link).expect("resolve provisioned junction"),
                fs::canonicalize(target).expect("resolve expected physical target"),
                "junction has the wrong target: {}",
                link.display()
            );
        }

        fn fixture_case(case_id: &str) -> PathBuf {
            assert!(CASE_IDS.contains(&case_id), "unknown fixture case");
            let configured = PathBuf::from(
                std::env::var_os("MW_REGISTRY_JUNCTION_FIXTURE")
                    .expect("MW_REGISTRY_JUNCTION_FIXTURE is required"),
            );
            assert!(configured.is_absolute(), "fixture root must be absolute");
            let root = fs::canonicalize(configured).expect("fixture root must exist");
            let runner_temp = fs::canonicalize(PathBuf::from(
                std::env::var_os("RUNNER_TEMP").expect("RUNNER_TEMP is required"),
            ))
            .expect("RUNNER_TEMP must exist");
            assert!(
                root.starts_with(&runner_temp),
                "fixture must remain inside RUNNER_TEMP"
            );
            for id in CASE_IDS {
                assert!(
                    plain_metadata(&root.join(id)).is_dir(),
                    "missing fixture case: {id}"
                );
            }
            root.join(case_id)
        }

        fn record_path(root: &Path) -> PathBuf {
            root.join(format!("{}.json", PROJECT_ID.simple()))
        }

        #[derive(Debug, PartialEq, Eq)]
        struct Snapshot {
            entries: Vec<OsString>,
            record: Option<Vec<u8>>,
        }

        fn snapshot(root: &Path) -> Snapshot {
            let mut entries: Vec<_> = fs::read_dir(root)
                .expect("read physical registry directory")
                .map(|entry| entry.expect("read fixture entry").file_name())
                .collect();
            entries.sort_unstable();
            let record = match fs::read(record_path(root)) {
                Ok(bytes) => Some(bytes),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
                Err(error) => panic!("cannot snapshot registry record: {error}"),
            };
            Snapshot { entries, record }
        }

        struct Fixture {
            case: PathBuf,
            registry: RuntimeConnectionRegistry,
        }

        impl Fixture {
            fn new(case_id: &str) -> Self {
                let case = fixture_case(case_id);
                let create_registry = |name: &str| {
                    let parent = case.join(name);
                    ensure_plain_directory(&parent);
                    let app_data = parent.join("app-data");
                    ensure_plain_directory(&app_data);
                    let registry = RuntimeConnectionRegistry::new(&app_data)
                        .expect("create ordinary registry");
                    let reference = case.join(format!("synthetic-{name}.json"));
                    // Synthetic references exercise persistence only; no Broker
                    // or ProductionConnection validation is claimed.
                    registry
                        .remember_verified(PROJECT_ID, GENERATION, &reference)
                        .expect("seed ordinary registry");
                    assert_eq!(
                        registry.resolve(PROJECT_ID, GENERATION),
                        Ok(Some(reference))
                    );
                    let root = app_data.join("owner-native-connections");
                    assert!(plain_metadata(&root).is_dir());
                    assert!(plain_metadata(&record_path(&root)).is_file());
                    registry
                };
                let registry = create_registry("original");
                let _decoy = create_registry("decoy");
                Self { case, registry }
            }

            fn app_data(&self, parent: &str) -> PathBuf {
                self.case.join(parent).join("app-data")
            }

            fn root(&self, parent: &str) -> PathBuf {
                self.app_data(parent).join("owner-native-connections")
            }

            fn reference(&self, name: &str) -> PathBuf {
                self.case.join(format!("synthetic-{name}.json"))
            }
        }

        #[derive(Clone, Copy, Debug)]
        enum Operation {
            Read,
            Write,
            Clear,
        }

        fn operation_error(operation: Operation, fixture: &Fixture) -> Option<String> {
            match operation {
                Operation::Read => fixture.registry.resolve(PROJECT_ID, GENERATION).err(),
                Operation::Write => fixture
                    .registry
                    .remember_verified(PROJECT_ID, GENERATION, &fixture.reference("replacement"))
                    .err(),
                Operation::Clear => fixture.registry.clear_saved(PROJECT_ID).err(),
            }
        }

        fn substitution(case_id: &str, operation: Operation, ancestor: bool) {
            let fixture = Fixture::new(case_id);
            let original_root = fixture.root("original");
            let decoy_root = fixture.root("decoy");
            let before = (snapshot(&original_root), snapshot(&decoy_root));
            let prepared = fixture.case.join("prepared-junction");
            let preserved = fixture.case.join("preserved");
            require_vacant(&preserved);
            let (replaced, target, preserved_root) = if ancestor {
                (
                    fixture.case.join("original"),
                    fixture.case.join("decoy"),
                    preserved.join("app-data").join("owner-native-connections"),
                )
            } else {
                (original_root.clone(), decoy_root.clone(), preserved.clone())
            };
            require_junction_to(&prepared, &target);
            fs::rename(&replaced, &preserved).expect("preserve original physical directory");
            fs::rename(&prepared, &replaced).expect("move provisioned junction into place");
            require_junction_to(&replaced, &target);
            assert_eq!(
                fs::canonicalize(&original_root).expect("resolve redirected registry"),
                fs::canonicalize(&decoy_root).expect("resolve physical decoy")
            );
            if ancestor {
                // Both final entries satisfy the old checks; the substituted
                // ancestor must be detected.
                assert!(plain_metadata(&original_root).is_dir());
                assert!(plain_metadata(&record_path(&original_root)).is_file());
            }
            let error = operation_error(operation, &fixture);
            let after = (snapshot(&preserved_root), snapshot(&decoy_root));
            assert_eq!(
                after, before,
                "{operation:?} changed a physical registry or left a temporary entry"
            );
            assert!(
                error.is_some(),
                "{operation:?} accepted a substituted registry path"
            );
        }

        fn alias_round_trip(case_id: &str, retarget: bool) {
            let fixture = Fixture::new(case_id);
            let original_root = fixture.root("original");
            let decoy_root = fixture.root("decoy");
            let decoy_before = snapshot(&decoy_root);
            let prepared = fixture.case.join("prepared-junction");
            let alias = fixture.case.join("active-alias");
            require_vacant(&alias);
            require_junction_to(&prepared, &fixture.app_data("original"));
            fs::rename(&prepared, &alias).expect("activate provisioned initial alias");
            require_junction_to(&alias, &fixture.app_data("original"));
            let registry =
                RuntimeConnectionRegistry::new(&alias).expect("accept legitimate initial alias");
            if retarget {
                let replacement = fixture.case.join("replacement-junction");
                let retired = fixture.case.join("retired-alias");
                require_vacant(&retired);
                require_junction_to(&replacement, &fixture.app_data("decoy"));
                fs::rename(&alias, &retired).expect("retire only the initial junction");
                fs::rename(&replacement, &alias).expect("activate replacement junction");
                require_junction_to(&retired, &fixture.app_data("original"));
                require_junction_to(&alias, &fixture.app_data("decoy"));
            }
            // Capture all operations and both physical snapshots before asserting.
            let initial_selection = registry.resolve(PROJECT_ID, GENERATION);
            let write_result = registry.remember_verified(
                PROJECT_ID,
                GENERATION,
                &fixture.reference("replacement"),
            );
            let written_selection = registry.resolve(PROJECT_ID, GENERATION);
            let physical_selection = fixture.registry.resolve(PROJECT_ID, GENERATION);
            let clear_result = registry.clear_saved(PROJECT_ID);
            let original_after = snapshot(&original_root);
            let decoy_after = snapshot(&decoy_root);
            assert_eq!(
                decoy_after, decoy_before,
                "alias followed the decoy registry"
            );
            assert_eq!(initial_selection, Ok(Some(fixture.reference("original"))));
            assert_eq!(write_result, Ok(()));
            assert_eq!(
                written_selection,
                Ok(Some(fixture.reference("replacement")))
            );
            assert_eq!(
                physical_selection,
                Ok(Some(fixture.reference("replacement")))
            );
            assert_eq!(clear_result, Ok(true));
            assert!(
                original_after.entries.is_empty(),
                "round trip left original registry entries: {original_after:?}"
            );
        }

        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn read_rejects_root_substitution() {
            substitution("root-read", Operation::Read, false);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn write_rejects_root_substitution() {
            substitution("root-write", Operation::Write, false);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn clear_rejects_root_substitution() {
            substitution("root-clear", Operation::Clear, false);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn read_rejects_ancestor_substitution() {
            substitution("ancestor-read", Operation::Read, true);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn write_rejects_ancestor_substitution() {
            substitution("ancestor-write", Operation::Write, true);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn clear_rejects_ancestor_substitution() {
            substitution("ancestor-clear", Operation::Clear, true);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn initial_app_data_alias_supports_normal_round_trip() {
            alias_round_trip("alias-initial", false);
        }
        #[test]
        #[ignore = "requires runner-provisioned NTFS junctions"]
        fn retargeted_initial_alias_keeps_original_physical_registry() {
            alias_round_trip("alias-retarget", true);
        }
    }
}
