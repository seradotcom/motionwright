use super::*;

impl StudioService {
    pub fn export_project_at(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
    ) -> StorageResult<ProjectBackup> {
        self.store.lock().export_project_at(id, expected)
    }

    pub fn export_project_bundle_at(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        destination: impl AsRef<Path>,
    ) -> StorageResult<ProjectBundleManifest> {
        self.store
            .lock()
            .export_project_bundle_at(id, expected, destination)
    }

    pub fn inspect_project_bundle_with_fingerprint(
        &self,
        source: impl AsRef<Path>,
    ) -> StorageResult<InspectedProjectBundle> {
        self.store
            .lock()
            .inspect_project_bundle_with_fingerprint(source)
    }

    pub fn import_project_bundle_inspected(
        &self,
        source: impl AsRef<Path>,
        expected: &BundleFingerprint,
    ) -> StorageResult<Project> {
        self.store
            .lock()
            .import_project_bundle_inspected(source, expected)
    }
}
