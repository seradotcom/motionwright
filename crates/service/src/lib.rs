use motionwright_domain::{Asset, Change, Project, RevisionStamp};
use motionwright_storage::{ApplyOutcome, Result as StorageResult, Store};
pub use motionwright_storage::{
    BlobDescriptor, BundleImportPlan, ImportPlan, PortableBlob, ProductionReceipt,
    ProductionReceiptInput, ProjectBackup, ProjectBundleManifest, ProjectCursor, ProjectEvent,
    ProjectPage, ProjectSummary,
};
use parking_lot::Mutex;
use std::{path::Path, sync::Arc};
use uuid::Uuid;

#[derive(Clone)]
pub struct StudioService {
    store: Arc<Mutex<Store>>,
}

impl StudioService {
    pub fn open(path: impl AsRef<Path>) -> StorageResult<Self> {
        Ok(Self {
            store: Arc::new(Mutex::new(Store::open(path)?)),
        })
    }

    pub fn from_store(store: Store) -> Self {
        Self {
            store: Arc::new(Mutex::new(store)),
        }
    }

    pub fn store(&self) -> Arc<Mutex<Store>> {
        Arc::clone(&self.store)
    }

    pub fn create_project(&self, title: &str) -> StorageResult<Project> {
        self.store.lock().create_named_project(title)
    }

    pub fn project(&self, id: Uuid) -> StorageResult<Project> {
        self.store.lock().load_project(id)
    }

    pub fn projects(&self, limit: usize) -> StorageResult<Vec<Project>> {
        self.store.lock().list_projects(limit)
    }

    pub fn project_summaries(
        &self,
        cursor: Option<&ProjectCursor>,
        limit: usize,
    ) -> StorageResult<ProjectPage> {
        self.store.lock().list_project_summaries(cursor, limit)
    }

    pub fn export_project(&self, id: Uuid) -> StorageResult<ProjectBackup> {
        self.store.lock().export_project(id)
    }

    pub fn inspect_import(&self, backup: &ProjectBackup) -> StorageResult<ImportPlan> {
        self.store.lock().inspect_import(backup)
    }

    pub fn import_project(&self, backup: &ProjectBackup) -> StorageResult<Project> {
        self.store.lock().import_project(backup)
    }

    pub fn ingest_blob_file(&self, source: impl AsRef<Path>) -> StorageResult<BlobDescriptor> {
        self.store.lock().ingest_blob_file(source)
    }

    pub fn import_asset_file(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        source: impl AsRef<Path>,
        name: String,
        media_type: String,
    ) -> StorageResult<ApplyOutcome> {
        let mut store = self.store.lock();
        let blob = store.ingest_blob_file(source)?;
        let change = Change::AddAsset {
            asset: Asset {
                id: Uuid::now_v7(),
                name,
                media_type,
                content_sha256: Some(blob.sha256),
                source_revision: Some("local-import".into()),
            },
        };
        store.apply(project_id, expected, request_id, &change)
    }

    pub fn export_project_bundle(
        &self,
        id: Uuid,
        destination: impl AsRef<Path>,
    ) -> StorageResult<ProjectBundleManifest> {
        self.store.lock().export_project_bundle(id, destination)
    }

    pub fn inspect_project_bundle(
        &self,
        source: impl AsRef<Path>,
    ) -> StorageResult<BundleImportPlan> {
        self.store.lock().inspect_project_bundle(source)
    }

    pub fn import_project_bundle(&self, source: impl AsRef<Path>) -> StorageResult<Project> {
        self.store.lock().import_project_bundle(source)
    }

    pub fn append_production_receipt(
        &self,
        input: ProductionReceiptInput,
    ) -> StorageResult<ProductionReceipt> {
        self.store.lock().append_production_receipt(input)
    }

    pub fn latest_production_receipt(
        &self,
        project_id: Uuid,
        request_id: &str,
    ) -> StorageResult<Option<ProductionReceipt>> {
        self.store
            .lock()
            .latest_production_receipt(project_id, request_id)
    }

    pub fn production_receipts(
        &self,
        project_id: Uuid,
        limit: usize,
    ) -> StorageResult<Vec<ProductionReceipt>> {
        self.store.lock().production_receipts(project_id, limit)
    }

    pub fn history(
        &self,
        id: Uuid,
        after_revision: u64,
        limit: usize,
    ) -> StorageResult<Vec<ProjectEvent>> {
        self.store
            .lock()
            .event_records_since(id, after_revision, limit)
    }

    pub fn apply(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        change: &Change,
    ) -> StorageResult<ApplyOutcome> {
        self.store.lock().apply(id, expected, request_id, change)
    }
}
