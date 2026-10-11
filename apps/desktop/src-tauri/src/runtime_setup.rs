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

impl RuntimeConnectionRegistry {
    pub(crate) fn new(app_data_dir: &Path) -> Result<Self, String> {
        let root = app_data_dir.join("owner-native-connections");
        fs::create_dir_all(&root).map_err(|_| "Runtime settings directory could not be created")?;
        let metadata = fs::symlink_metadata(&root)
            .map_err(|_| "Runtime settings directory could not be inspected")?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err("Runtime settings directory is not a regular directory".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&root, fs::Permissions::from_mode(0o700))
                .map_err(|_| "Runtime settings directory permissions are unsafe")?;
        }
        Ok(Self { root })
    }

    fn destination(&self, project_id: Uuid) -> PathBuf {
        self.root.join(format!("{}.json", project_id.simple()))
    }

    pub(crate) fn resolve(
        &self,
        project_id: Uuid,
        generation: Uuid,
    ) -> Result<Option<PathBuf>, String> {
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
        #[cfg(windows)]
        {
            // Windows cannot reliably replace an existing file via rename.
            // A completed staged file exists before removing the old pointer.
            if destination.exists() {
                fs::remove_file(&destination)
                    .map_err(|_| "Existing Semwright connection reference cannot be replaced")?;
            }
        }
        if fs::rename(&temporary, &destination).is_err() {
            let _ = fs::remove_file(&temporary);
            return Err("Semwright connection reference could not be activated".into());
        }
        Ok(())
    }

    /// Remove only the application-owned reference, never the operator's
    /// Semwright connection file, Broker session or native job evidence.
    pub(crate) fn clear_saved(&self, project_id: Uuid) -> Result<bool, String> {
        let destination = self.destination(project_id);
        match fs::symlink_metadata(&destination) {
            Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
                Err("Saved Semwright reference is not a regular file".into())
            }
            Ok(_) => {
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
}
