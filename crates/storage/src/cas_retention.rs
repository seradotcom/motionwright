use super::{BlobDescriptor, Project, ProjectBackup, Result, StorageError, Store};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

pub(super) mod derived;

// Root anchors remain v1. Only roots using derived custody adopt catalog v2.
const FORMAT_VERSION: u32 = 1;
const CATALOG_FORMAT_VERSION: u32 = 2;
const MAX_CATALOG_BYTES: u64 = 16 * 1024 * 1024;
const MAX_RECOVERY_OBJECTS: usize = 4096;
const ROOT_ANCHOR: &str = ".motionwright-cas-root-v1.json";

/// Retention never overrides a live writer, a pin or an unknown outcome.
#[derive(Debug, Clone, Copy)]
pub struct CasRetentionPolicy {
    pub retention: Duration,
    pub max_objects: usize,
}

impl Default for CasRetentionPolicy {
    fn default() -> Self {
        Self {
            retention: Duration::from_secs(24 * 60 * 60),
            max_objects: 64,
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct CasRecoveryReport {
    pub root_managed: bool,
    pub inspected: usize,
    pub removed: usize,
    pub retained_by_pins: usize,
    pub retained_by_age: usize,
    pub unresolved: usize,
    pub truncated: bool,
    pub captures_removed: usize,
}

/// Opaque durable protection. Dropping this handle does not release it.
/// A caller must explicitly release it only after its actual use has ended.
#[derive(Debug)]
pub struct CasBlobLease {
    root_id: Uuid,
    lease_id: Uuid,
    store_id: Uuid,
    digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LeaseRecord {
    store_id: Uuid,
    digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CaptureRecord {
    store_id: Uuid,
    name: String,
    created_ms: u64,
    descriptor: Option<BlobDescriptor>,
    intent_id: Option<Uuid>,
    file_id: Option<String>,
    lock_id: String,
    purpose: CapturePurpose,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct IntentRecord {
    store_id: Uuid,
    database_id: String,
    project_id: Uuid,
    request_sha256: String,
    created_ms: u64,
    digests: BTreeSet<String>,
    captures: BTreeSet<Uuid>,
    lock_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PublicationRecord {
    publication_id: Uuid,
    intent_id: Uuid,
    descriptor: BlobDescriptor,
    created_ms: u64,
    deleting: bool,
    file_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RootAnchor {
    format_version: u32,
    root_id: Uuid,
    control: PathBuf,
    canonical_root: PathBuf,
    directory_id: String,
    control_id: String,
    lock_id: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Catalog {
    format_version: u32,
    root_id: Uuid,
    canonical_root: PathBuf,
    directory_id: String,
    control_id: String,
    legacy_pins: BTreeSet<String>,
    leases: BTreeMap<Uuid, LeaseRecord>,
    captures: BTreeMap<Uuid, CaptureRecord>,
    intents: BTreeMap<Uuid, IntentRecord>,
    publications: BTreeMap<String, PublicationRecord>,
    retired_locks: BTreeMap<Uuid, String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    recovery_cursor: Option<RecoveryPosition>,
    // Omission preserves the exact v1 checksum preimage before promotion.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    derived: Option<derived::State>,
}

#[derive(Debug, Clone)]
pub(super) struct ManagedRoot {
    root: PathBuf,
    control: PathBuf,
    root_id: Uuid,
    directory_id: String,
    control_id: String,
    lock_id: String,
}

pub(super) struct RootGate {
    root: ManagedRoot,
    catalog: Catalog,
    _lock: File,
}

/// The lock stays alive through publication and the SQL commit. RootGate
/// is never held while SQLite is queried or while this writer is paused.
pub(super) struct ImportIntent {
    root: ManagedRoot,
    pub(super) operation_id: Uuid,
    pub(super) store_id: Uuid,
    pub(super) project_id: Uuid,
    pub(super) request_sha256: String,
    _live: File,
}

#[derive(Debug)]
pub(super) struct CaptureLease {
    root: ManagedRoot,
    capture_id: Uuid,
    _live: File,
}

fn invalid(message: &str) -> StorageError {
    StorageError::InvalidBackup(format!("CAS retention: {message}"))
}

fn now_ms() -> Result<u64> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| invalid("clock precedes the retention epoch"))?;
    u64::try_from(elapsed.as_millis())
        .map_err(|_| invalid("retention timestamp exceeds supported range"))
}

fn retained(created_ms: u64, now: u64, retention: Duration) -> bool {
    let threshold = u64::try_from(retention.as_millis()).unwrap_or(u64::MAX);
    now < created_ms || now.saturating_sub(created_ms) < threshold
}

fn hash_bytes(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn open_control(path: &Path, directory: bool, write: bool, create: bool) -> Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(write).create_new(create);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut flags = rustix::fs::OFlags::NOFOLLOW | rustix::fs::OFlags::NONBLOCK;
        if directory {
            flags |= rustix::fs::OFlags::DIRECTORY;
        }
        options.custom_flags(flags.bits() as i32);
        if create {
            options.mode(0o600);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
        };
        options.custom_flags(
            FILE_FLAG_OPEN_REPARSE_POINT
                | if directory {
                    FILE_FLAG_BACKUP_SEMANTICS
                } else {
                    0
                },
        );
    }
    let file = options.open(path)?;
    let metadata = file.metadata()?;
    if metadata.file_type().is_symlink()
        || (directory && !metadata.is_dir())
        || (!directory && !metadata.is_file())
    {
        return Err(invalid("control entry has an unexpected filesystem type"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(invalid("control entry is a reparse point"));
        }
    }
    Ok(file)
}

pub(super) fn file_identity(file: &File) -> Result<String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = file.metadata()?;
        Ok(format!("unix:{}:{}", metadata.dev(), metadata.ino()))
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_ID_INFO, FileIdInfo, GetFileInformationByHandleEx,
        };
        let mut information = std::mem::MaybeUninit::<FILE_ID_INFO>::uninit();
        // SAFETY: the borrowed File owns a live handle; the output buffer has
        // the size and alignment required by FileIdInfo and is read only after
        // the OS reports success. The 128-bit ID also covers ReFS identities.
        let status = unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle() as _,
                FileIdInfo,
                information.as_mut_ptr().cast(),
                std::mem::size_of::<FILE_ID_INFO>() as u32,
            )
        };
        if status == 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        // SAFETY: successful FileIdInfo initializes the entire FILE_ID_INFO.
        let information = unsafe { information.assume_init() };
        Ok(format!(
            "windows:{}:{}",
            information.VolumeSerialNumber,
            hex::encode(information.FileId.Identifier)
        ))
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = file;
        Err(invalid("physical identity is unavailable on this platform"))
    }
}

pub(super) fn database_identity(path: &Path) -> Result<String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;

        // This is a pathname observation, never retained-connection authority.
        // Closing an independently opened SQLite file can release POSIX locks
        // held by another connection in this process. Stat without opening it.
        super::checked_sqlite_storage_path(path)?;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.is_file() {
            return Err(invalid("database path is not a regular file"));
        }
        Ok(format!("unix:{}:{}", metadata.dev(), metadata.ino()))
    }
    #[cfg(not(unix))]
    {
        file_identity(&open_control(path, false, false, false)?)
    }
}

fn directory_identity(path: &Path) -> Result<String> {
    file_identity(&open_control(path, true, false, false)?)
}

fn read_control<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T> {
    let input = open_control(path, false, false, false)?;
    if input.metadata()?.len() > MAX_CATALOG_BYTES {
        return Err(invalid("control record exceeds its byte budget"));
    }
    let mut bytes = Vec::new();
    input.take(MAX_CATALOG_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return Err(invalid("control record grew beyond its byte budget"));
    }
    Ok(serde_json::from_slice(&bytes)?)
}

fn write_new_control(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = open_control(path, false, true, true)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn sync_directory(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        open_control(path, true, false, false)?.sync_all()?;
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
    Ok(())
}

fn replace_control(path: &Path, bytes: &[u8]) -> Result<()> {
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return Err(invalid("control record exceeds its byte budget"));
    }
    let parent = path
        .parent()
        .ok_or_else(|| invalid("control parent is missing"))?;
    super::ensure_regular_directory(parent, "CAS control parent")?;
    super::ensure_regular_file(path, "CAS control record")?;
    let temporary = parent.join(format!(".{}.tmp", Uuid::now_v7()));
    let result = (|| {
        write_new_control(&temporary, bytes)?;
        fs::rename(&temporary, path)?;
        sync_directory(parent)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CatalogEnvelope {
    sha256: String,
    catalog: Catalog,
}

fn encode_catalog(catalog: &Catalog) -> Result<Vec<u8>> {
    #[derive(Serialize)]
    struct Envelope<'a> {
        sha256: String,
        catalog: &'a Catalog,
    }
    let payload = serde_json::to_vec(catalog)?;
    let bytes = serde_json::to_vec(&Envelope {
        sha256: hash_bytes(&payload),
        catalog,
    })?;
    if bytes.len() as u64 > MAX_CATALOG_BYTES {
        return Err(invalid("control catalog exceeds its byte budget"));
    }
    Ok(bytes)
}

fn control_path(parent: &Path, root_id: Uuid) -> PathBuf {
    parent.join(format!(".motionwright-cas-{root_id}"))
}

pub(super) fn discover(root: &Path) -> Result<Option<ManagedRoot>> {
    super::ensure_regular_directory(root, "CAS root")?;
    let canonical_root = fs::canonicalize(root)?;
    let anchor_path = canonical_root.join(ROOT_ANCHOR);
    match fs::symlink_metadata(&anchor_path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
        Ok(_) => {}
    }
    let anchor: RootAnchor = read_control(&anchor_path)?;
    let parent = canonical_root
        .parent()
        .ok_or_else(|| invalid("root parent is missing"))?;
    if anchor.format_version != FORMAT_VERSION
        || anchor.canonical_root != canonical_root
        || anchor.control != control_path(parent, anchor.root_id)
        || directory_identity(&canonical_root)? != anchor.directory_id
        || directory_identity(&anchor.control)? != anchor.control_id
    {
        return Err(invalid("managed root identity changed"));
    }
    let lock = open_control(&anchor.control.join("root.lock"), false, true, false)?;
    if file_identity(&lock)? != anchor.lock_id {
        return Err(invalid("root gate identity changed"));
    }
    Ok(Some(ManagedRoot {
        root: canonical_root,
        control: anchor.control,
        root_id: anchor.root_id,
        directory_id: anchor.directory_id,
        control_id: anchor.control_id,
        lock_id: anchor.lock_id,
    }))
}

fn remove_initializer(root: &Path, root_id: &str, control: &Path, control_id: &str) {
    // These unique directories were created by this initializer. Never recurse
    // into a pathname whose physical identity no longer matches our creation.
    if directory_identity(root).is_ok_and(|actual| actual == root_id) {
        let _ = fs::remove_dir_all(root);
    }
    if directory_identity(control).is_ok_and(|actual| actual == control_id) {
        let _ = fs::remove_dir_all(control);
    }
}

pub(super) fn prepare_blob_root(root: &Path, require_managed: bool) -> Result<()> {
    match fs::symlink_metadata(root) {
        Ok(_) => {
            let managed = discover(root)?;
            if require_managed && managed.is_none() {
                return Err(invalid("existing unadmitted root cannot be promoted"));
            }
            return Ok(());
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    let parent = root
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)?;
    super::ensure_regular_directory(parent, "CAS root parent")?;
    let parent = fs::canonicalize(parent)?;
    let name = root
        .file_name()
        .ok_or_else(|| invalid("root name is missing"))?;
    let destination = parent.join(name);
    let root_id = Uuid::now_v7();
    let staging = parent.join(format!(".motionwright-cas-root-{root_id}.creating"));
    let control = control_path(&parent, root_id);
    fs::create_dir(&staging)?;
    let directory_id = directory_identity(&staging)?;
    if let Err(error) = fs::create_dir(&control) {
        let _ = fs::remove_dir(&staging);
        return Err(error.into());
    }
    let control_id = directory_identity(&control)?;
    let initialized = (|| -> Result<()> {
        fs::create_dir(control.join("locks"))?;
        write_new_control(&control.join("root.lock"), b"")?;
        let lock_id = file_identity(&open_control(
            &control.join("root.lock"),
            false,
            true,
            false,
        )?)?;
        let catalog = Catalog {
            format_version: FORMAT_VERSION,
            root_id,
            canonical_root: destination.clone(),
            directory_id: directory_id.clone(),
            control_id: control_id.clone(),
            legacy_pins: BTreeSet::new(),
            leases: BTreeMap::new(),
            captures: BTreeMap::new(),
            intents: BTreeMap::new(),
            publications: BTreeMap::new(),
            retired_locks: BTreeMap::new(),
            recovery_cursor: None,
            derived: None,
        };
        write_new_control(&control.join("catalog.json"), &encode_catalog(&catalog)?)?;
        let anchor = RootAnchor {
            format_version: FORMAT_VERSION,
            root_id,
            control: control.clone(),
            canonical_root: destination.clone(),
            directory_id: directory_id.clone(),
            control_id: control_id.clone(),
            lock_id,
        };
        write_new_control(&staging.join(ROOT_ANCHOR), &serde_json::to_vec(&anchor)?)?;
        sync_directory(&control)?;
        sync_directory(&staging)?;
        super::portability::publish_bundle_directory(&staging, &destination)?;
        sync_directory(&parent)?;
        Ok(())
    })();
    if let Err(error) = initialized {
        // A vanished initializer may already have been published, even if
        // the subsequent directory sync or readback failed. Preserve its
        // control state unless our original staging directory is still here.
        if !directory_identity(&staging).is_ok_and(|actual| actual == directory_id) {
            return Err(error);
        }
        let existing = fs::symlink_metadata(&destination).is_ok();
        remove_initializer(&staging, &directory_id, &control, &control_id);
        if existing {
            let winner = discover(&destination)?;
            if require_managed && winner.is_none() {
                return Err(invalid("concurrent root is not admitted"));
            }
            return Ok(());
        }
        return Err(error);
    }
    discover(&destination)?.ok_or_else(|| invalid("new root was not admitted"))?;
    Ok(())
}

impl ManagedRoot {
    fn validate_location(&self) -> Result<()> {
        let current = discover(&self.root)?.ok_or_else(|| invalid("managed anchor disappeared"))?;
        if current.root_id != self.root_id
            || current.control != self.control
            || current.directory_id != self.directory_id
            || current.control_id != self.control_id
            || current.lock_id != self.lock_id
        {
            return Err(invalid("managed root changed after acquisition"));
        }
        Ok(())
    }

    fn lock_path(&self, id: Uuid) -> PathBuf {
        self.control.join("locks").join(format!("{id}.lock"))
    }

    fn gate(&self) -> Result<RootGate> {
        self.validate_location()?;
        let lock = open_control(&self.control.join("root.lock"), false, true, false)?;
        if file_identity(&lock)? != self.lock_id {
            return Err(invalid("root gate was replaced"));
        }
        lock.lock()?;
        self.validate_location()?;
        let envelope: CatalogEnvelope = read_control(&self.control.join("catalog.json"))?;
        let catalog = envelope.catalog;
        if envelope.sha256 != hash_bytes(&serde_json::to_vec(&catalog)?)
            || !((catalog.format_version == FORMAT_VERSION && catalog.derived.is_none())
                || (catalog.format_version == CATALOG_FORMAT_VERSION && catalog.derived.is_some()))
            || catalog.root_id != self.root_id
            || catalog.canonical_root != self.root
            || catalog.directory_id != self.directory_id
            || catalog.control_id != self.control_id
        {
            return Err(invalid("catalog identity or checksum is invalid"));
        }
        for digest in catalog
            .legacy_pins
            .iter()
            .chain(catalog.publications.keys())
        {
            super::validate_digest(digest)?;
        }
        for lease in catalog.leases.values() {
            super::validate_digest(&lease.digest)?;
        }
        for intent in catalog.intents.values() {
            for digest in &intent.digests {
                super::validate_digest(digest)?;
            }
        }
        Ok(RootGate {
            root: self.clone(),
            catalog,
            _lock: lock,
        })
    }
}

impl RootGate {
    fn save(&mut self) -> Result<()> {
        self.root.validate_location()?;
        replace_control(
            &self.root.control.join("catalog.json"),
            &encode_catalog(&self.catalog)?,
        )
    }
}

#[cfg(test)]
pub(super) fn test_metadata_root(root: &Path) -> Result<PathBuf> {
    Ok(discover(root)?
        .ok_or_else(|| invalid("test root is not managed"))?
        .control)
}

pub(super) fn begin_capture(
    root: &Path,
    store_id: Uuid,
    purpose: CapturePurpose,
) -> Result<(PathBuf, File, Option<CaptureLease>)> {
    let managed = discover(root)?;
    let staging = root.join(".staging");
    super::create_checked_blob_directory(&staging)?;
    let id = Uuid::now_v7();
    let name = format!("{}.part", id.simple());
    let path = staging.join(&name);
    let Some(managed) = managed else {
        let output = open_control(&path, false, true, true)?;
        return Ok((path, output, None));
    };
    let created_ms = now_ms()?;
    let mut gate = managed.gate()?;
    if purpose == CapturePurpose::DerivedCache {
        derived::state_mut(&mut gate.catalog);
    }
    let live = open_control(&managed.lock_path(id), false, true, true)?;
    live.lock()?;
    let lock_id = file_identity(&live)?;
    gate.catalog.captures.insert(
        id,
        CaptureRecord {
            store_id,
            name,
            created_ms,
            descriptor: None,
            intent_id: None,
            file_id: None,
            lock_id,
            purpose,
        },
    );
    gate.save()?;
    // An error after create_new may have created a file: keep the reservation.
    let output = open_control(&path, false, true, true)?;
    let identity = file_identity(&output)?;
    gate.catalog
        .captures
        .get_mut(&id)
        .ok_or_else(|| invalid("capture reservation disappeared"))?
        .file_id = Some(identity);
    gate.save()?;
    drop(gate);
    Ok((
        path,
        output,
        Some(CaptureLease {
            root: managed,
            capture_id: id,
            _live: live,
        }),
    ))
}

impl CaptureLease {
    pub(super) fn completed(&mut self, descriptor: &BlobDescriptor) -> Result<()> {
        super::validate_digest(&descriptor.sha256)?;
        let mut gate = self.root.gate()?;
        let record = gate
            .catalog
            .captures
            .get(&self.capture_id)
            .ok_or_else(|| invalid("capture reservation disappeared"))?;
        let path = capture_path(&self.root, self.capture_id, record)?;
        let file = open_control(&path, false, false, false)?;
        if record.file_id.as_deref() != Some(file_identity(&file)?.as_str())
            || file.metadata()?.len() != descriptor.size_bytes
        {
            return Err(invalid("capture physical identity changed"));
        }
        gate.catalog
            .captures
            .get_mut(&self.capture_id)
            .ok_or_else(|| invalid("capture reservation disappeared"))?
            .descriptor = Some(descriptor.clone());
        gate.save()
    }
}

pub(super) fn pin_digests<'a>(
    root: &Path,
    digests: impl IntoIterator<Item = &'a str>,
) -> Result<()> {
    let digests = digests
        .into_iter()
        .map(str::to_owned)
        .collect::<BTreeSet<_>>();
    for digest in &digests {
        super::validate_digest(digest)?;
    }
    let Some(managed) = discover(root)? else {
        return Ok(());
    };
    let mut gate = managed.gate()?;
    let before = gate.catalog.legacy_pins.len();
    gate.catalog.legacy_pins.extend(digests);
    if gate.catalog.legacy_pins.len() != before {
        gate.save()?;
    }
    Ok(())
}

pub(super) fn pin_project(root: &Path, project: &Project) -> Result<()> {
    pin_digests(
        root,
        project
            .assets
            .iter()
            .filter_map(|asset| asset.content_sha256.as_deref()),
    )
}

pub(super) fn publish_legacy(
    root: &Path,
    staged: &super::StagedFileImport,
) -> Result<BlobDescriptor> {
    let Some(managed) = discover(root)? else {
        return super::file_import::publish_file_import_unmanaged(root, staged);
    };
    let mut gate = managed.gate()?;
    let digest = &staged.descriptor().sha256;
    super::validate_digest(digest)?;
    if gate.catalog.legacy_pins.insert(digest.clone()) {
        gate.save()?;
    }
    super::file_import::publish_file_import_unmanaged(root, staged)
}

impl Store {
    pub fn lease_blob(&self, digest: &str) -> Result<CasBlobLease> {
        super::validate_digest(digest)?;
        let managed =
            discover(&self.blob_root)?.ok_or_else(|| invalid("leases require an admitted root"))?;
        let mut gate = managed.gate()?;
        self.verify_blob(digest)?;
        let lease_id = Uuid::now_v7();
        derived::invalidate_retention(&mut gate.catalog, digest);
        gate.catalog.leases.insert(
            lease_id,
            LeaseRecord {
                store_id: self.cas_store_id,
                digest: digest.to_owned(),
            },
        );
        gate.save()?;
        Ok(CasBlobLease {
            root_id: managed.root_id,
            lease_id,
            store_id: self.cas_store_id,
            digest: digest.to_owned(),
        })
    }

    pub fn release_blob_lease(&self, lease: CasBlobLease) -> Result<()> {
        let managed =
            discover(&self.blob_root)?.ok_or_else(|| invalid("lease root is not admitted"))?;
        if managed.root_id != lease.root_id || lease.store_id != self.cas_store_id {
            return Err(invalid("lease belongs to another root or store"));
        }
        let mut gate = managed.gate()?;
        let record = gate
            .catalog
            .leases
            .get(&lease.lease_id)
            .ok_or_else(|| invalid("lease is unknown or already released"))?;
        if record.store_id != lease.store_id || record.digest != lease.digest {
            return Err(invalid("lease binding differs"));
        }
        gate.catalog.leases.remove(&lease.lease_id);
        derived::mark_unreferenced(
            &mut gate.catalog,
            &lease.digest,
            derived::event_time(&managed.root)?,
        );
        gate.save()
    }
}

impl Store {
    pub(super) fn begin_bundle_import(
        &self,
        backup: &ProjectBackup,
        staged: &[super::StagedFileImport],
        request_sha256: &str,
    ) -> Result<Option<ImportIntent>> {
        super::validate_digest(request_sha256)?;
        let Some(root) = discover(&self.blob_root)? else {
            return Ok(None);
        };
        if database_identity(&self.path)? != self.cas_database_id {
            return Err(invalid("database physical identity changed"));
        }
        let digests = backup
            .project
            .assets
            .iter()
            .filter_map(|a| a.content_sha256.clone())
            .collect::<BTreeSet<_>>();
        let captured = staged
            .iter()
            .map(|s| s.descriptor().sha256.clone())
            .collect::<BTreeSet<_>>();
        if digests != captured {
            return Err(invalid("import captures do not cover exact asset digests"));
        }
        for digest in &digests {
            super::validate_digest(digest)?;
        }
        let created_ms = now_ms()?;
        let mut gate = root.gate()?;
        let mut captures = BTreeSet::new();
        for staged in staged {
            let lease = staged
                .retention
                .as_ref()
                .ok_or_else(|| invalid("managed import has an unowned capture"))?;
            if lease.root.root_id != root.root_id {
                return Err(invalid("capture belongs to another root"));
            }
            let record = gate
                .catalog
                .captures
                .get(&lease.capture_id)
                .ok_or_else(|| invalid("capture reservation is missing"))?;
            if record.store_id != self.cas_store_id
                || record.intent_id.is_some()
                || record.purpose != CapturePurpose::BundleImport
                || record.descriptor.as_ref() != Some(staged.descriptor())
                || file_identity(&lease._live)? != record.lock_id
            {
                return Err(invalid("capture cannot be attached to this import"));
            }
            verify_lock_path(&root, lease.capture_id, &record.lock_id)?;
            captures.insert(lease.capture_id);
        }
        let operation_id = Uuid::now_v7();
        let live = open_control(&root.lock_path(operation_id), false, true, true)?;
        live.lock()?;
        let lock_id = file_identity(&live)?;
        for id in &captures {
            gate.catalog
                .captures
                .get_mut(id)
                .ok_or_else(|| invalid("validated capture disappeared"))?
                .intent_id = Some(operation_id);
        }
        for digest in &digests {
            derived::invalidate_retention(&mut gate.catalog, digest);
        }
        gate.catalog.intents.insert(
            operation_id,
            IntentRecord {
                store_id: self.cas_store_id,
                database_id: self.cas_database_id.clone(),
                project_id: backup.project.id,
                request_sha256: request_sha256.to_owned(),
                created_ms,
                digests,
                captures,
                lock_id,
            },
        );
        gate.save()?;
        drop(gate);
        Ok(Some(ImportIntent {
            root,
            operation_id,
            store_id: self.cas_store_id,
            project_id: backup.project.id,
            request_sha256: request_sha256.to_owned(),
            _live: live,
        }))
    }
}

impl ImportIntent {
    pub(super) fn publish(&self, staged: &super::StagedFileImport) -> Result<BlobDescriptor> {
        let lease = staged
            .retention
            .as_ref()
            .ok_or_else(|| invalid("publication has no capture lease"))?;
        let mut gate = self.root.gate()?;
        let captured_root = discover(staged.captured_root())?
            .ok_or_else(|| invalid("capture root is not admitted"))?;
        if captured_root.root_id != self.root.root_id
            || captured_root.directory_id != self.root.directory_id
        {
            return Err(invalid("capture root identity differs"));
        }
        let intent = gate
            .catalog
            .intents
            .get(&self.operation_id)
            .ok_or_else(|| invalid("import intent is missing"))?;
        let descriptor = staged.descriptor();
        if !intent.digests.contains(&descriptor.sha256)
            || !intent.captures.contains(&lease.capture_id)
        {
            return Err(invalid("publication is outside the captured import"));
        }
        let capture = gate
            .catalog
            .captures
            .get(&lease.capture_id)
            .ok_or_else(|| invalid("publication capture is missing"))?;
        if capture.intent_id != Some(self.operation_id)
            || capture.descriptor.as_ref() != Some(descriptor)
        {
            return Err(invalid("publication capture binding differs"));
        }
        let source_file = open_control(staged.path(), false, false, false)?;
        let source_id = file_identity(&source_file)?;
        if capture.file_id.as_deref() != Some(source_id.as_str()) {
            return Err(invalid("publication capture was replaced"));
        }
        let destination = digest_path(&self.root.root, &descriptor.sha256)?;
        let missing = match fs::symlink_metadata(&destination) {
            Ok(_) => false,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
            Err(error) => return Err(error.into()),
        };
        let publication_id = Uuid::now_v7();
        if missing {
            derived::forget_absent_publication(&mut gate.catalog, &descriptor.sha256);
            gate.catalog.publications.insert(
                descriptor.sha256.clone(),
                PublicationRecord {
                    publication_id,
                    intent_id: self.operation_id,
                    descriptor: descriptor.clone(),
                    created_ms: now_ms()?,
                    deleting: false,
                    file_id: source_id.clone(),
                },
            );
            gate.save()?;
            #[cfg(test)]
            crate::cas_retention_process_tests::checkpoint(
                &self.root.root,
                "import_after_publication_reservation",
            );
        }
        let result =
            super::file_import::publish_file_import_unmanaged(staged.captured_root(), staged)?;
        if missing {
            let actual_id = file_identity(&open_control(&destination, false, false, false)?)?;
            if actual_id != source_id
                && gate
                    .catalog
                    .publications
                    .get(&descriptor.sha256)
                    .is_some_and(|p| p.publication_id == publication_id)
            {
                // A non-protocol publication won; its bytes are never ours.
                gate.catalog.publications.remove(&descriptor.sha256);
                gate.save()?;
            }
        }
        Ok(result)
    }

    pub(super) fn record_commit(&self, tx: &Connection, project: &Project) -> Result<()> {
        if project.id != self.project_id {
            return Err(invalid("import result belongs to another project"));
        }
        let expected = (
            self.operation_id.to_string(),
            self.root.root_id.to_string(),
            self.store_id.to_string(),
            self.request_sha256.clone(),
            self.project_id.to_string(),
            project.generation.to_string(),
        );
        tx.execute(
            "INSERT INTO cas_import_commits(operation_id,root_id,store_id,request_sha256,project_id,generation) VALUES(?1,?2,?3,?4,?5,?6)",
            params![&expected.0, &expected.1, &expected.2, &expected.3, &expected.4, &expected.5],
        )?;
        let recorded: Option<(String, String, String, String, String, String)> = tx.query_row(
            "SELECT operation_id,root_id,store_id,request_sha256,project_id,generation FROM cas_import_commits WHERE operation_id=?1",
            [&expected.0], |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?,row.get(4)?,row.get(5)?)),
        ).optional()?;
        if recorded.as_ref() != Some(&expected) {
            return Err(invalid(
                "import marker did not retain the exact committed binding",
            ));
        }
        Ok(())
    }

    pub(super) fn finish_committed(self) -> Result<()> {
        let Self {
            root,
            operation_id,
            _live,
            ..
        } = self;
        let mut gate = root.gate()?;
        let intent = gate
            .catalog
            .intents
            .get(&operation_id)
            .ok_or_else(|| invalid("committed import intent is missing"))?;
        let digests = intent.digests.clone();
        let lock_id = intent.lock_id.clone();
        if file_identity(&_live)? != lock_id {
            return Err(invalid("import lock custody differs"));
        }
        verify_lock_path(&root, operation_id, &lock_id)?;
        gate.catalog.legacy_pins.extend(digests);
        gate.catalog
            .publications
            .retain(|_, p| p.intent_id != operation_id);
        gate.catalog.intents.remove(&operation_id);
        gate.catalog.retired_locks.insert(operation_id, lock_id);
        gate.save()?;
        drop(_live);
        let _ = gate.retire_lock(operation_id)?;
        Ok(())
    }
}

fn digest_path(root: &Path, digest: &str) -> Result<PathBuf> {
    super::validate_digest(digest)?;
    Ok(root.join("sha256").join(&digest[..2]).join(digest))
}

const STORE_TABLE: &str = "cas_store_identity";
const COMMITS_TABLE: &str = "cas_import_commits";

fn control_table_exists(conn: &Connection, name: &str) -> Result<bool> {
    let kind: Option<String> = conn
        .query_row(
            "SELECT type FROM sqlite_schema WHERE name=?1",
            [name],
            |row| row.get(0),
        )
        .optional()?;
    match kind.as_deref() {
        None => Ok(false),
        Some("table") => Ok(true),
        Some(_) => Err(invalid("reserved SQLite control name is not a table")),
    }
}

fn table_columns(conn: &Connection, name: &str) -> Result<Vec<(String, String, bool, u32)>> {
    let sql = match name {
        STORE_TABLE => "PRAGMA table_info(cas_store_identity)",
        COMMITS_TABLE => "PRAGMA table_info(cas_import_commits)",
        _ => return Err(invalid("unknown control table")),
    };
    let mut statement = conn.prepare(sql)?;
    let rows = statement.query_map([], |row| {
        Ok((row.get(1)?, row.get(2)?, row.get(3)?, row.get(5)?))
    })?;
    Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
}

pub(super) fn validate_schema(conn: &Connection, version: u32) -> Result<()> {
    let identity = control_table_exists(conn, STORE_TABLE)?;
    let commits = control_table_exists(conn, COMMITS_TABLE)?;
    if !identity && !commits && version < 5 {
        return Ok(());
    }
    if !identity || !commits {
        return Err(invalid("incomplete CAS control schema"));
    }
    let identity_columns = vec![
        ("singleton".into(), "INTEGER".into(), false, 1),
        ("store_id".into(), "TEXT".into(), true, 0),
    ];
    let commit_columns = vec![
        ("operation_id".into(), "TEXT".into(), true, 1),
        ("root_id".into(), "TEXT".into(), true, 0),
        ("store_id".into(), "TEXT".into(), true, 0),
        ("request_sha256".into(), "TEXT".into(), true, 0),
        ("project_id".into(), "TEXT".into(), true, 0),
        ("generation".into(), "TEXT".into(), true, 0),
    ];
    if table_columns(conn, STORE_TABLE)? != identity_columns
        || table_columns(conn, COMMITS_TABLE)? != commit_columns
    {
        return Err(invalid("incompatible CAS control columns"));
    }
    let foreign_keys: u64 = conn.query_row(
        "SELECT count(*) FROM pragma_foreign_key_list('cas_import_commits')",
        [],
        |row| row.get(0),
    )?;
    if foreign_keys != 0 {
        return Err(invalid("import outcome markers must not cascade"));
    }
    // Before schema6 these owner-control tables must have no triggers.
    // Schema6 adds exactly three registered-writer fences to each table.
    // Cardinality alone is insufficient: a foreign or rewritten trigger
    // could alter the CAS store identity or import history.
    let observed_triggers: Vec<(String, String, Option<String>)> = {
        let mut statement = conn.prepare(
            "SELECT tbl_name,name,sql FROM sqlite_schema
             WHERE type='trigger' AND tbl_name IN ('cas_store_identity','cas_import_commits')
             ORDER BY tbl_name,name",
        )?;
        statement
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))?
            .collect::<rusqlite::Result<_>>()?
    };
    if version < 6 {
        if !observed_triggers.is_empty() {
            return Err(invalid(
                "CAS control tables must not have mutating triggers",
            ));
        }
    } else if observed_triggers.len() != 6
        || observed_triggers.iter().any(|(table, name, sql)| {
            !sql.as_deref().is_some_and(|body| {
                super::root_operation_budget::is_canonical_writer_fence(name, table, body)
            })
        })
    {
        return Err(invalid(
            "CAS writer fences are missing, foreign or modified",
        ));
    }
    load_store_id(conn)?;
    Ok(())
}

pub(super) fn migrate_schema(conn: &Connection, version: u32) -> Result<()> {
    validate_schema(conn, version)?;
    if control_table_exists(conn, STORE_TABLE)? {
        return Ok(());
    }
    conn.execute_batch(
        "CREATE TABLE cas_store_identity (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            store_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE cas_import_commits (
            operation_id TEXT PRIMARY KEY NOT NULL,
            root_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            project_id TEXT NOT NULL,
            generation TEXT NOT NULL
        );",
    )?;
    conn.execute(
        "INSERT INTO cas_store_identity(singleton,store_id) VALUES(1,?1)",
        [Uuid::now_v7().to_string()],
    )?;
    validate_schema(conn, 5)
}

pub(super) fn load_store_id(conn: &Connection) -> Result<Uuid> {
    let count: u64 = conn.query_row("SELECT count(*) FROM cas_store_identity", [], |row| {
        row.get(0)
    })?;
    if count != 1 {
        return Err(invalid("store identity is not singular"));
    }
    let value: String = conn.query_row(
        "SELECT store_id FROM cas_store_identity WHERE singleton=1",
        [],
        |row| row.get(0),
    )?;
    Uuid::parse_str(&value).map_err(|_| invalid("store identity is malformed"))
}

fn committed(
    conn: &Connection,
    root: &ManagedRoot,
    id: Uuid,
    intent: &IntentRecord,
) -> Result<bool> {
    let marker:Option<(String,String,String,String,String)>=conn.query_row(
        "SELECT root_id,store_id,request_sha256,project_id,generation FROM cas_import_commits WHERE operation_id=?1",
        [id.to_string()],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?,row.get(4)?)),
    ).optional()?;
    let Some((root_id, store_id, request_sha256, project_id, generation)) = marker else {
        return Ok(false);
    };
    if root_id != root.root_id.to_string()
        || store_id != intent.store_id.to_string()
        || request_sha256 != intent.request_sha256
        || project_id != intent.project_id.to_string()
        || Uuid::parse_str(&generation).is_err()
    {
        return Err(invalid("durable import result binding differs"));
    }
    Ok(true)
}

fn verify_lock_path(root: &ManagedRoot, id: Uuid, expected: &str) -> Result<()> {
    let actual = open_control(&root.lock_path(id), false, true, false)?;
    if file_identity(&actual)? != expected {
        return Err(invalid("liveness lock was replaced"));
    }
    Ok(())
}

fn try_live_lock(path: &Path, expected_id: &str) -> Result<Option<File>> {
    let file = open_control(path, false, true, false)?;
    if file_identity(&file)? != expected_id {
        return Err(invalid("liveness lock identity differs"));
    }
    match file.try_lock() {
        Ok(()) => {
            let current = open_control(path, false, true, false)?;
            if file_identity(&current)? != expected_id {
                return Err(invalid("liveness lock changed during acquisition"));
            }
            Ok(Some(file))
        }
        Err(std::fs::TryLockError::WouldBlock) => Ok(None),
        Err(std::fs::TryLockError::Error(error)) => Err(error.into()),
    }
}

fn remove_lock(root: &ManagedRoot, id: Uuid, expected_id: &str) -> Result<()> {
    match open_control(&root.lock_path(id), false, true, false) {
        Ok(file) => {
            if file_identity(&file)? != expected_id {
                return Err(invalid("lock changed before retirement"));
            }
            drop(file);
            fs::remove_file(root.lock_path(id))?;
            Ok(())
        }
        Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn capture_path(root: &ManagedRoot, id: Uuid, record: &CaptureRecord) -> Result<PathBuf> {
    if record.name != format!("{}.part", id.simple()) {
        return Err(invalid("capture name is outside its reservation"));
    }
    let staging = root.root.join(".staging");
    match fs::symlink_metadata(&staging) {
        Ok(_) => {
            super::ensure_regular_directory(&staging, "CAS capture directory")?;
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    Ok(staging.join(&record.name))
}

fn checked_publication_file(
    root: &ManagedRoot,
    digest: &str,
    record: &PublicationRecord,
) -> Result<Option<File>> {
    checked_owned_blob(root, digest, &record.descriptor, &record.file_id)
}

fn checked_owned_blob(
    root: &ManagedRoot,
    digest: &str,
    descriptor: &BlobDescriptor,
    file_id: &str,
) -> Result<Option<File>> {
    let path = match super::checked_existing_blob_path_from_root(&root.root, digest) {
        Ok(path) => path,
        Err(StorageError::BlobMissing { .. }) => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut file = open_control(&path, false, false, false)?;
    if file_identity(&file)? != file_id || file.metadata()?.len() != descriptor.size_bytes {
        return Err(invalid("publication physical identity or size changed"));
    }
    let mut hash = Sha256::new();
    let mut read = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        read = read
            .checked_add(count as u64)
            .ok_or_else(|| invalid("publication size overflow"))?;
        if read > descriptor.size_bytes {
            return Err(invalid("publication grew during cleanup"));
        }
        hash.update(&buffer[..count]);
    }
    if read != descriptor.size_bytes
        || hex::encode(hash.finalize()) != digest
        || descriptor.sha256 != digest
    {
        return Err(invalid("publication content differs from its reservation"));
    }
    Ok(Some(file))
}

fn same_intent(left: &IntentRecord, right: &IntentRecord) -> bool {
    left.store_id == right.store_id
        && left.database_id == right.database_id
        && left.project_id == right.project_id
        && left.request_sha256 == right.request_sha256
        && left.created_ms == right.created_ms
        && left.digests == right.digests
        && left.captures == right.captures
        && left.lock_id == right.lock_id
}

impl RootGate {
    // Caller holds CaptureRecord.lock_id exclusively, or owns CaptureLease.
    fn erase_capture(&mut self, id: Uuid) -> Result<bool> {
        let Some(record) = self.catalog.captures.get(&id).cloned() else {
            return Ok(false);
        };
        let path = capture_path(&self.root, id, &record)?;
        let removed = match open_control(&path, false, false, false) {
            Ok(file) => {
                let expected = record
                    .file_id
                    .as_deref()
                    .ok_or_else(|| invalid("capture identity was never committed"))?;
                if file_identity(&file)? != expected {
                    return Err(invalid("capture was replaced before cleanup"));
                }
                fs::remove_file(&path)?;
                true
            }
            Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => false,
            Err(error) => return Err(error),
        };
        self.catalog.captures.remove(&id);
        self.catalog.retired_locks.insert(id, record.lock_id);
        self.save()?;
        Ok(removed)
    }
}

impl CaptureLease {
    pub(super) fn discard(self) -> Result<()> {
        let Self {
            root,
            capture_id,
            _live,
        } = self;
        let mut gate = root.gate()?;
        let record = gate
            .catalog
            .captures
            .get(&capture_id)
            .ok_or_else(|| invalid("capture custody is missing"))?;
        let lock_id = record.lock_id.clone();
        if file_identity(&_live)? != lock_id {
            return Err(invalid("capture lock custody differs"));
        }
        verify_lock_path(&root, capture_id, &lock_id)?;
        gate.erase_capture(capture_id)?;
        drop(_live);
        let _ = gate.retire_lock(capture_id)?;
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum CapturePurpose {
    Legacy,
    BundleImport,
    DerivedCache,
}

impl RootGate {
    fn retire_lock(&mut self, id: Uuid) -> Result<bool> {
        let Some(expected) = self.catalog.retired_locks.get(&id).cloned() else {
            return Ok(true);
        };
        if self.catalog.captures.contains_key(&id) || self.catalog.intents.contains_key(&id) {
            return Err(invalid("live control record cannot retire its lock"));
        }
        match try_live_lock(&self.root.lock_path(id), &expected) {
            Ok(Some(file)) => {
                drop(file);
                remove_lock(&self.root, id, &expected)?;
            }
            Ok(None) => return Ok(false),
            Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        self.catalog.retired_locks.remove(&id);
        self.save()?;
        Ok(true)
    }
}

#[cfg(test)]
pub(super) fn test_file_identity(path: &Path) -> Result<String> {
    file_identity(&open_control(path, false, false, false)?)
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum RecoveryPosition {
    Capture(Uuid),
    Publication(String),
    Intent(Uuid),
    Retired(Uuid),
    ProxyBinding(Uuid),
    DerivedReference(Uuid),
    DerivedPublication(String),
    DerivedRetention(String),
}

fn recovery_candidates(catalog: &Catalog) -> BTreeSet<RecoveryPosition> {
    let mut candidates = BTreeSet::new();
    for (id, capture) in &catalog.captures {
        if capture.purpose != CapturePurpose::Legacy {
            candidates.insert(RecoveryPosition::Capture(*id));
        }
    }
    candidates.extend(
        catalog
            .publications
            .keys()
            .cloned()
            .map(RecoveryPosition::Publication),
    );
    candidates.extend(
        catalog
            .intents
            .keys()
            .copied()
            .map(RecoveryPosition::Intent),
    );
    candidates.extend(
        catalog
            .retired_locks
            .keys()
            .copied()
            .map(RecoveryPosition::Retired),
    );
    candidates.extend(derived::proxy::candidates(catalog, None));
    candidates.extend(derived::candidates(catalog));
    candidates
}

fn recovery_queue(catalog: &Catalog) -> std::collections::VecDeque<RecoveryPosition> {
    let mut candidates = recovery_candidates(catalog).into_iter().collect::<Vec<_>>();
    if let Some(after) = &catalog.recovery_cursor {
        let split = candidates.partition_point(|candidate| candidate <= after);
        candidates.rotate_left(split);
    }
    candidates.into()
}

fn is_pinned(catalog: &Catalog, digest: &str, excluding: Uuid) -> bool {
    catalog.legacy_pins.contains(digest)
        || derived::protects(catalog, digest)
        || catalog.leases.values().any(|lease| lease.digest == digest)
        || catalog
            .intents
            .iter()
            .any(|(id, intent)| *id != excluding && intent.digests.contains(digest))
}

struct ResolvedIntent {
    gate: RootGate,
    _live: File,
    snapshot: IntentRecord,
    applied: bool,
}

fn resolve_recovery_intent(
    store: &mut Store,
    root: &ManagedRoot,
    id: Uuid,
    report: &mut CasRecoveryReport,
) -> Result<Option<ResolvedIntent>> {
    let gate = root.gate()?;
    let Some(snapshot) = gate.catalog.intents.get(&id).cloned() else {
        return Ok(None);
    };
    if snapshot.store_id != store.cas_store_id || snapshot.database_id != store.cas_database_id {
        report.unresolved += 1;
        return Ok(None);
    }
    let live = match try_live_lock(&root.lock_path(id), &snapshot.lock_id) {
        Ok(Some(file)) => file,
        Ok(None) | Err(_) => {
            report.unresolved += 1;
            return Ok(None);
        }
    };
    drop(gate);
    if database_identity(&store.path)? != store.cas_database_id {
        return Err(invalid("database changed before outcome resolution"));
    }
    let tx = store
        .conn
        .transaction_with_behavior(super::TransactionBehavior::Deferred)?;
    if load_store_id(&tx)? != snapshot.store_id {
        return Err(invalid("outcome belongs to another database identity"));
    }
    let applied = committed(&tx, root, id, &snapshot)?;
    tx.commit()?;
    if database_identity(&store.path)? != store.cas_database_id {
        return Err(invalid("database changed during outcome resolution"));
    }
    let gate = root.gate()?;
    if !gate
        .catalog
        .intents
        .get(&id)
        .is_some_and(|current| same_intent(current, &snapshot))
        || verify_lock_path(root, id, &snapshot.lock_id).is_err()
    {
        report.unresolved += 1;
        return Ok(None);
    }
    Ok(Some(ResolvedIntent {
        gate,
        _live: live,
        snapshot,
        applied,
    }))
}

fn retire_applied_intent(
    resolved: &mut ResolvedIntent,
    id: Uuid,
    report: &mut CasRecoveryReport,
) -> Result<RecoveryPosition> {
    let snapshot = &resolved.snapshot;
    let gate = &mut resolved.gate;
    gate.catalog
        .legacy_pins
        .extend(snapshot.digests.iter().cloned());
    gate.catalog
        .publications
        .retain(|_, publication| publication.intent_id != id);
    gate.catalog.intents.remove(&id);
    gate.catalog
        .retired_locks
        .insert(id, snapshot.lock_id.clone());
    gate.save()?;
    report.retained_by_pins += snapshot.digests.len();
    Ok(RecoveryPosition::Retired(id))
}

fn recover_capture_candidate(
    root: &ManagedRoot,
    id: Uuid,
    policy: CasRetentionPolicy,
    now: u64,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let mut gate = root.gate()?;
    let Some(snapshot) = gate.catalog.captures.get(&id).cloned() else {
        return Ok(None);
    };
    if snapshot.purpose == CapturePurpose::Legacy {
        report.retained_by_pins += 1;
        return Ok(None);
    }
    if retained(snapshot.created_ms, now, policy.retention) {
        report.retained_by_age += 1;
        return Ok(None);
    }
    let live = match try_live_lock(&root.lock_path(id), &snapshot.lock_id) {
        Ok(Some(file)) => file,
        Ok(None) => {
            report.retained_by_pins += 1;
            return Ok(None);
        }
        Err(_) => {
            report.unresolved += 1;
            return Ok(None);
        }
    };
    match gate.erase_capture(id) {
        Ok(removed) => {
            report.captures_removed += usize::from(removed);
            let retired = gate.catalog.retired_locks.contains_key(&id);
            if !retired && gate.catalog.captures.contains_key(&id) {
                report.unresolved += 1;
            }
            drop(live);
            Ok(retired.then_some(RecoveryPosition::Retired(id)))
        }
        Err(_) => {
            report.unresolved += 1;
            Ok(None)
        }
    }
}

fn recover_publication_candidate(
    store: &mut Store,
    root: &ManagedRoot,
    digest: &str,
    policy: CasRetentionPolicy,
    now: u64,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let Some(snapshot) = root.gate()?.catalog.publications.get(digest).cloned() else {
        return Ok(None);
    };
    let before_unresolved = report.unresolved;
    let Some(mut resolved) = resolve_recovery_intent(store, root, snapshot.intent_id, report)?
    else {
        // A vanished candidate is harmless. An ownerless publication still
        // present in the catalog has no outcome authority for removal.
        if report.unresolved == before_unresolved
            && root
                .gate()?
                .catalog
                .publications
                .get(digest)
                .is_some_and(|current| current.publication_id == snapshot.publication_id)
        {
            report.unresolved += 1;
        }
        return Ok(None);
    };
    if resolved.applied {
        return retire_applied_intent(&mut resolved, snapshot.intent_id, report).map(Some);
    }
    let gate = &mut resolved.gate;
    let Some(publication) = gate.catalog.publications.get(digest).cloned() else {
        return Ok(None);
    };
    if publication.publication_id != snapshot.publication_id
        || publication.intent_id != snapshot.intent_id
        || publication.file_id != snapshot.file_id
        || publication.descriptor != snapshot.descriptor
        || publication.created_ms != snapshot.created_ms
    {
        report.unresolved += 1;
        return Ok(None);
    }
    if gate.catalog.legacy_pins.contains(digest) {
        report.retained_by_pins += 1;
        gate.catalog.publications.remove(digest);
        gate.save()?;
        return Ok(None);
    }
    if is_pinned(&gate.catalog, digest, publication.intent_id) {
        report.retained_by_pins += 1;
        return Ok(None);
    }
    if derived::retains(gate, digest, now, policy.retention)?
        || retained(publication.created_ms, now, policy.retention)
    {
        report.retained_by_age += 1;
        return Ok(None);
    }
    let file = match checked_publication_file(root, digest, &publication) {
        Ok(file) => file,
        Err(_) => {
            report.unresolved += 1;
            return Ok(None);
        }
    };
    if let Some(file) = file {
        let path = digest_path(&root.root, digest)?;
        if file_identity(&open_control(&path, false, false, false)?)? != publication.file_id {
            report.unresolved += 1;
            return Ok(None);
        }
        let current = gate
            .catalog
            .publications
            .get_mut(digest)
            .ok_or_else(|| invalid("publication disappeared under root gate"))?;
        if current.publication_id != publication.publication_id {
            report.unresolved += 1;
            return Ok(None);
        }
        current.deleting = true;
        gate.save()?;
        fs::remove_file(&path)?;
        report.removed += 1;
        #[cfg(test)]
        crate::cas_retention_process_tests::checkpoint(&root.root, "gc_after_unlink");
        drop(file);
    }
    if gate
        .catalog
        .publications
        .get(digest)
        .is_some_and(|current| current.publication_id == publication.publication_id)
    {
        gate.catalog.publications.remove(digest);
        gate.save()?;
    }
    Ok(None)
}

fn recover_intent_candidate(
    store: &mut Store,
    root: &ManagedRoot,
    id: Uuid,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let Some(mut resolved) = resolve_recovery_intent(store, root, id, report)? else {
        return Ok(None);
    };
    if resolved.applied {
        return retire_applied_intent(&mut resolved, id, report).map(Some);
    }
    let gate = &mut resolved.gate;
    let owns_publication = gate
        .catalog
        .publications
        .values()
        .any(|p| p.intent_id == id);
    let owns_capture = gate
        .catalog
        .captures
        .values()
        .any(|c| c.intent_id == Some(id));
    if owns_publication || owns_capture {
        // The SQL result is known. Retention or another candidate still
        // owns the associated filesystem work; do not call it UNKNOWN.
        return Ok(None);
    }
    gate.catalog.intents.remove(&id);
    for digest in &resolved.snapshot.digests {
        derived::mark_unreferenced(&mut gate.catalog, digest, derived::event_time(&root.root)?);
    }
    gate.catalog
        .retired_locks
        .insert(id, resolved.snapshot.lock_id.clone());
    gate.save()?;
    Ok(Some(RecoveryPosition::Retired(id)))
}

impl Store {
    pub fn recover_cas(&mut self, policy: CasRetentionPolicy) -> Result<CasRecoveryReport> {
        if policy.max_objects == 0 || policy.max_objects > MAX_RECOVERY_OBJECTS {
            return Err(invalid(
                "recovery object budget is outside supported bounds",
            ));
        }
        let Some(root) = discover(&self.blob_root)? else {
            return Ok(CasRecoveryReport::default());
        };
        if database_identity(&self.path)? != self.cas_database_id
            || load_store_id(&self.conn)? != self.cas_store_id
        {
            return Err(invalid("database identity changed before recovery"));
        }
        let mut report = CasRecoveryReport {
            root_managed: true,
            ..Default::default()
        };
        let access = derived::proxy::acquire(&self.blob_root, false)?;
        if matches!(access, derived::proxy::Access::Busy) {
            return Ok(derived::proxy::deferred_report());
        }
        let (mut queue, starting_cursor) = {
            let gate = root.gate()?;
            derived::proxy::check_access(&access, &gate)?;
            (
                recovery_queue(&gate.catalog),
                gate.catalog.recovery_cursor.clone(),
            )
        };
        let mut queued = queue.iter().cloned().collect::<BTreeSet<_>>();
        let mut visited = BTreeSet::new();
        let mut last = starting_cursor.clone();
        while report.inspected < policy.max_objects {
            let Some(candidate) = queue.pop_front() else {
                break;
            };
            queued.remove(&candidate);
            if !visited.insert(candidate.clone()) {
                continue;
            }
            report.inspected += 1;
            last = Some(candidate.clone());
            // A prior candidate may have just released the last protection.
            // Compare retention against this candidate's current observation,
            // never a timestamp from before that durable release.
            let now = derived::event_time(&root.root)?;
            let added = match candidate {
                RecoveryPosition::Capture(id) => {
                    recover_capture_candidate(&root, id, policy, now, &mut report)?
                }
                RecoveryPosition::Publication(digest) => {
                    recover_publication_candidate(self, &root, &digest, policy, now, &mut report)?
                }
                RecoveryPosition::Intent(id) => {
                    recover_intent_candidate(self, &root, id, &mut report)?
                }
                RecoveryPosition::ProxyBinding(id) => {
                    derived::proxy::recover_candidate(self, &access, id, now, &mut report)?
                }
                RecoveryPosition::DerivedReference(id) => {
                    derived::recover_reference(&root, id, now, &mut report)?
                }
                RecoveryPosition::DerivedPublication(digest) => {
                    derived::recover_publication(&root, &digest, policy, now, &mut report)?
                }
                RecoveryPosition::DerivedRetention(digest) => {
                    derived::recover_retention(&root, &digest, policy, now, &mut report)?
                }
                RecoveryPosition::Retired(id) => {
                    let mut gate = root.gate()?;
                    match gate.retire_lock(id) {
                        Ok(true) => {}
                        Ok(false) | Err(_) => report.unresolved += 1,
                    }
                    None
                }
            };
            if let Some(added) = added
                && !visited.contains(&added)
                && queued.insert(added.clone())
            {
                queue.push_back(added);
            }
        }
        let mut gate = root.gate()?;
        derived::proxy::check_access(&access, &gate)?;
        let current_candidates = recovery_candidates(&gate.catalog);
        report.truncated = current_candidates
            .iter()
            .any(|candidate| !visited.contains(candidate));
        let next_cursor = if report.truncated { last } else { None };
        // Scheduling metadata is never evidence that a blob can be removed.
        // Do not overwrite another collector's advance. An empty queue needs
        // no cursor, regardless of which collector finished it.
        if (current_candidates.is_empty() || gate.catalog.recovery_cursor == starting_cursor)
            && gate.catalog.recovery_cursor != next_cursor
        {
            gate.catalog.recovery_cursor = next_cursor;
            gate.save()?;
        }
        Ok(report)
    }
}
