//! Custody of newly published, never-borrowed derived cache objects.
//!
//! Manifests are filesystem authority, not SQLite import outcomes. Legacy pins
//! are never reclassified. All transitions below hold the managed root gate.
use super::*;
use crate::derived_cache::DerivedCacheRecord;
pub(crate) mod proxy;

const MANIFEST_LIMIT: u64 = 64 * 1024;

#[cfg(test)]
thread_local! {
    // Root-scoped and thread-local: parallel tests cannot grant a clock to
    // another Store. This facility does not exist in a product build.
    static TEST_CLOCKS: std::cell::RefCell<BTreeMap<PathBuf, u64>> =
        const { std::cell::RefCell::new(BTreeMap::new()) };
}

pub(super) fn event_time(root: &Path) -> Result<u64> {
    #[cfg(test)]
    if let Some(now) = TEST_CLOCKS.with(|clocks| clocks.borrow().get(root).copied()) {
        return Ok(now);
    }
    #[cfg(not(test))]
    let _ = root;
    now_ms()
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct State {
    references: BTreeMap<Uuid, Reference>,
    publications: BTreeMap<String, OwnedPublication>,
    // None means a reusable protection has not yet been observed released.
    retention: BTreeMap<String, Option<u64>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    maintenance_cursor: Option<RecoveryPosition>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    proxy: Option<proxy::ProxyState>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct BoundDirectory {
    path: PathBuf,
    file_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    path: PathBuf,
    parents: Vec<BoundDirectory>,
    file_id: Option<String>,
    sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Phase {
    Prepared,
    Active,
    Retiring,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Staging {
    file_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Reference {
    record: DerivedCacheRecord,
    manifest: Manifest,
    // A reserved stage with no physical ID is never swept by name alone.
    staging: Option<Staging>,
    phase: Phase,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    proxy_origin: Option<Uuid>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct OwnedPublication {
    publication_id: Uuid,
    descriptor: BlobDescriptor,
    file_id: String,
    deleting: bool,
}

pub(super) fn state_mut(catalog: &mut Catalog) -> &mut State {
    catalog.format_version = CATALOG_FORMAT_VERSION;
    catalog.derived.get_or_insert_with(State::default)
}

pub(super) fn protects(catalog: &Catalog, digest: &str) -> bool {
    catalog.derived.as_ref().is_some_and(|state| {
        state
            .references
            .values()
            .any(|reference| reference.record.blob_sha256 == digest)
    })
}

pub(super) fn invalidate_retention(catalog: &mut Catalog, digest: &str) {
    if let Some(since) = catalog
        .derived
        .as_mut()
        .and_then(|state| state.retention.get_mut(digest))
    {
        *since = None;
    }
}

pub(super) fn mark_unreferenced(catalog: &mut Catalog, digest: &str, now: u64) {
    let protected = is_pinned(catalog, digest, Uuid::nil());
    if let Some(since) = catalog
        .derived
        .as_mut()
        .and_then(|state| state.retention.get_mut(digest))
    {
        if protected {
            *since = None;
        } else if since.is_none() {
            *since = Some(now);
        }
    }
}

// Called only after the collector has excluded every live reusable protection.
// This also delays import-owned publications adopted by a derived manifest.
pub(super) fn retains(
    gate: &mut RootGate,
    digest: &str,
    now: u64,
    retention: Duration,
) -> Result<bool> {
    let Some(since) = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|state| state.retention.get(digest))
        .copied()
    else {
        return Ok(false);
    };
    let since = match since {
        Some(since) => since,
        None => {
            state_mut(&mut gate.catalog)
                .retention
                .insert(digest.to_owned(), Some(now));
            gate.save()?;
            now
        }
    };
    Ok(retained(since, now, retention))
}

// A new publisher may replace metadata for an old object only after observing
// that its old pathname is absent under rootgate. It never acquires that
// object's identity or deletes an existing replacement.
pub(super) fn forget_absent_publication(catalog: &mut Catalog, digest: &str) {
    if let Some(state) = catalog.derived.as_mut() {
        state.publications.remove(digest);
    }
}

fn namespace(root: &ManagedRoot) -> Result<PathBuf> {
    Ok(root
        .root
        .parent()
        .ok_or_else(|| invalid("derived namespace parent is missing"))?
        .join("derived-cache")
        .join("sha256")
        .join(format!("managed-{}", root.root_id)))
}

pub(crate) fn manifest_root(blob_root: &Path) -> Result<Option<PathBuf>> {
    discover(blob_root)?
        .map(|root| namespace(&root))
        .transpose()
}

fn expected_path(root: &ManagedRoot, record: &DerivedCacheRecord) -> Result<PathBuf> {
    crate::validate_digest(&record.scope_sha256)?;
    crate::validate_digest(&record.fingerprint_sha256)?;
    crate::validate_digest(&record.blob_sha256)?;
    if record.format_version != crate::derived_cache::DERIVED_CACHE_FORMAT_VERSION {
        return Err(invalid("derived manifest format is unsupported"));
    }
    let mut hash = Sha256::new();
    hash.update(record.scope_sha256.as_bytes());
    hash.update([0]);
    hash.update(record.kind.as_bytes());
    hash.update([0]);
    hash.update(record.fingerprint_sha256.as_bytes());
    Ok(namespace(root)?
        .join(&record.scope_sha256[..2])
        .join(&record.scope_sha256)
        .join(format!("{}.json", hex::encode(hash.finalize()))))
}

fn parent_bindings(root: &ManagedRoot, path: &Path) -> Result<Vec<BoundDirectory>> {
    let boundary = root
        .root
        .parent()
        .ok_or_else(|| invalid("derived boundary is missing"))?;
    let mut current = path
        .parent()
        .ok_or_else(|| invalid("manifest parent is missing"))?;
    let mut parents = Vec::new();
    loop {
        parents.push(BoundDirectory {
            path: current.to_path_buf(),
            file_id: directory_identity(current)?,
        });
        if current == boundary {
            break;
        }
        current = current
            .parent()
            .ok_or_else(|| invalid("manifest escapes its root namespace"))?;
    }
    parents.reverse();
    Ok(parents)
}

fn validate_reference(root: &ManagedRoot, reference: &Reference) -> Result<()> {
    if reference.manifest.path != expected_path(root, &reference.record)?
        || reference.manifest.parents != parent_bindings(root, &reference.manifest.path)?
    {
        return Err(invalid(
            "derived manifest namespace or parent identity changed",
        ));
    }
    Ok(())
}

fn read_manifest(path: &Path) -> Result<Option<(String, Vec<u8>, DerivedCacheRecord)>> {
    let mut file = match open_control(path, false, false, false) {
        Ok(file) => file,
        Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(None);
        }
        Err(error) => return Err(error),
    };
    let id = file_identity(&file)?;
    let size = file.metadata()?.len();
    if size > MANIFEST_LIMIT {
        return Err(invalid("derived manifest exceeds its byte budget"));
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MANIFEST_LIMIT + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 != size
        || file.metadata()?.len() != size
        || file_identity(&file)? != id
        || file_identity(&open_control(path, false, false, false)?)? != id
    {
        return Err(invalid("derived manifest changed during observation"));
    }
    let record = serde_json::from_slice(&bytes)?;
    Ok(Some((id, bytes, record)))
}

pub(crate) fn read_record(
    path: &Path,
    scope: &str,
    kind: &str,
    fingerprint: &str,
) -> Result<Option<DerivedCacheRecord>> {
    let Some((_, _, record)) = read_manifest(path)? else {
        return Ok(None);
    };
    if record.format_version != crate::derived_cache::DERIVED_CACHE_FORMAT_VERSION
        || record.scope_sha256 != scope
        || record.kind != kind
        || record.fingerprint_sha256 != fingerprint
    {
        return Err(invalid(
            "derived manifest identity differs from its requested key",
        ));
    }
    crate::validate_digest(&record.blob_sha256)?;
    Ok(Some(record))
}

fn matches_manifest(
    reference: &Reference,
    observed: &(String, Vec<u8>, DerivedCacheRecord),
) -> bool {
    reference.manifest.file_id.as_deref() == Some(observed.0.as_str())
        && reference.manifest.sha256 == hash_bytes(&observed.1)
        && reference.record == observed.2
}

fn staging_path(id: Uuid, reference: &Reference) -> Result<PathBuf> {
    Ok(reference
        .manifest
        .path
        .parent()
        .ok_or_else(|| invalid("manifest staging parent is missing"))?
        .join(format!(".{}.part", id.simple())))
}

fn cleanup_staging(id: Uuid, reference: &mut Reference) -> Result<()> {
    let Some(expected) = &reference.staging else {
        return Ok(());
    };
    let path = staging_path(id, reference)?;
    match open_control(&path, false, false, false) {
        Ok(file) => {
            if expected.file_id.as_deref() != Some(file_identity(&file)?.as_str()) {
                return Err(invalid("derived staging identity is unknown or changed"));
            }
            fs::remove_file(&path)?;
            sync_directory(
                path.parent()
                    .ok_or_else(|| invalid("staging parent is missing"))?,
            )?;
        }
        Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    reference.staging = None;
    Ok(())
}

fn validate_reference_blob(gate: &RootGate, reference: &Reference) -> Result<()> {
    let descriptor = BlobDescriptor {
        sha256: reference.record.blob_sha256.clone(),
        size_bytes: reference.record.size_bytes,
    };
    let digest = &descriptor.sha256;
    let id = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|state| state.publications.get(digest))
        .map(|value| value.file_id.as_str())
        .or_else(|| {
            gate.catalog
                .publications
                .get(digest)
                .map(|value| value.file_id.as_str())
        });
    if let Some(id) = id {
        if checked_owned_blob(&gate.root, digest, &descriptor, id)?.is_none() {
            return Err(invalid("derived manifest publication has no physical blob"));
        }
    } else {
        let path = crate::checked_existing_blob_path_from_root(&gate.root.root, digest)?;
        if crate::portability::hash_portable_file(&path, descriptor.size_bytes)? != descriptor {
            return Err(invalid("derived manifest blob differs from its descriptor"));
        }
    }
    Ok(())
}

// Retiring metadata is already authorized by the journal. Do not parse
// damaged contents as deletion authority; bind the unlink to the exact file.
fn retirement_manifest(reference: &Reference) -> Result<Option<File>> {
    let file = match open_control(&reference.manifest.path, false, false, false) {
        Ok(file) => file,
        Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(None);
        }
        Err(error) => return Err(error),
    };
    let expected = reference
        .manifest
        .file_id
        .as_deref()
        .ok_or_else(|| invalid("retiring manifest has no known physical identity"))?;
    if file_identity(&file)? != expected
        || file_identity(&open_control(
            &reference.manifest.path,
            false,
            false,
            false,
        )?)? != expected
    {
        return Err(invalid("retiring derived manifest was replaced"));
    }
    Ok(Some(file))
}

// Acquiring rootgate excludes every protocol writer throughout these FS
// transitions. No SQLite query or externally pausable work occurs under it.
fn reconcile_reference(gate: &mut RootGate, id: Uuid, now: u64) -> Result<bool> {
    let Some(mut reference) = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|state| state.references.get(&id))
        .cloned()
    else {
        return Ok(false);
    };
    validate_reference(&gate.root, &reference)?;
    proxy::check_reference(&gate.catalog, id, &reference)?;
    let observed = if matches!(reference.phase, Phase::Retiring) {
        None
    } else {
        read_manifest(&reference.manifest.path)?
    };
    match reference.phase {
        Phase::Prepared => {
            if let Some(observed) = observed {
                if !matches_manifest(&reference, &observed) {
                    return Err(invalid("prepared derived manifest has an unknown outcome"));
                }
                validate_reference_blob(gate, &reference)?;
                reference.phase = Phase::Active;
            } else {
                reference.phase = Phase::Retiring;
            }
        }
        Phase::Active => {
            if !observed
                .as_ref()
                .is_some_and(|value| matches_manifest(&reference, value))
            {
                return Err(invalid("active derived manifest disappeared or changed"));
            }
        }
        Phase::Retiring => {
            if let Some(file) = retirement_manifest(&reference)? {
                fs::remove_file(&reference.manifest.path)?;
                sync_directory(
                    reference
                        .manifest
                        .path
                        .parent()
                        .ok_or_else(|| invalid("manifest parent is missing"))?,
                )?;
                checkpoint(&gate.root.root, "derived_after_manifest_unlink");
                drop(file);
            }
        }
    }
    cleanup_staging(id, &mut reference)?;
    if matches!(reference.phase, Phase::Retiring) {
        let digest = reference.record.blob_sha256.clone();
        state_mut(&mut gate.catalog).references.remove(&id);
        proxy::forget_reference(gate, id);
        mark_unreferenced(&mut gate.catalog, &digest, now);
        gate.save()?;
        Ok(false)
    } else {
        state_mut(&mut gate.catalog)
            .references
            .insert(id, reference);
        gate.save()?;
        Ok(true)
    }
}

fn checkpoint(root: &Path, phase: &str) {
    #[cfg(test)]
    crate::cas_retention_process_tests::checkpoint(root, phase);
    #[cfg(not(test))]
    let _ = (root, phase);
}

pub(crate) fn publish(
    store: &Store,
    staged: &crate::StagedFileImport,
    destination: &Path,
    requested: DerivedCacheRecord,
    admission: Option<&proxy::Admission>,
) -> Result<DerivedCacheRecord> {
    if let Some(admission) = admission {
        admission.check_store(store)?;
    }
    let root = discover(&store.blob_root)?
        .ok_or_else(|| invalid("derived publication requires a managed root"))?;
    let mut gate = root.gate()?;
    if destination != expected_path(&root, &requested)? {
        return Err(invalid("derived destination is outside its root namespace"));
    }
    let lease = staged
        .retention
        .as_ref()
        .ok_or_else(|| invalid("derived capture has no custody"))?;
    let captured_root = discover(staged.captured_root())?
        .ok_or_else(|| invalid("derived capture root is unmanaged"))?;
    let capture = gate
        .catalog
        .captures
        .get(&lease.capture_id)
        .ok_or_else(|| invalid("derived capture reservation is missing"))?;
    let source_id = file_identity(&open_control(staged.path(), false, false, false)?)?;
    if lease.root.root_id != root.root_id
        || captured_root.root_id != root.root_id
        || captured_root.directory_id != root.directory_id
        || capture.purpose != CapturePurpose::DerivedCache
        || capture.intent_id.is_some()
        || capture.store_id != store.cas_store_id
        || capture.descriptor.as_ref() != Some(staged.descriptor())
        || capture.file_id.as_deref() != Some(source_id.as_str())
        || file_identity(&lease._live)? != capture.lock_id
        || staged.descriptor().sha256 != requested.blob_sha256
        || staged.descriptor().size_bytes != requested.size_bytes
    {
        return Err(invalid("derived capture binding differs"));
    }
    verify_lock_path(&root, lease.capture_id, &capture.lock_id)?;
    let now = event_time(&root.root)?;
    let previous = gate
        .catalog
        .derived
        .as_ref()
        .map(|state| {
            state
                .references
                .iter()
                .filter(|(_, reference)| reference.manifest.path == destination)
                .map(|(id, _)| *id)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for id in &previous {
        reconcile_reference(&mut gate, *id, now)?;
    }
    let observed = read_manifest(destination)?;
    let (record, manifest_id, encoded) = match observed {
        Some((id, bytes, winner)) => {
            if winner.scope_sha256 != requested.scope_sha256
                || winner.kind != requested.kind
                || winner.fingerprint_sha256 != requested.fingerprint_sha256
                || winner.format_version != requested.format_version
                || winner.blob_sha256 != requested.blob_sha256
                || winner.size_bytes != requested.size_bytes
            {
                return Err(invalid("derived key already belongs to different bytes"));
            }
            (winner, Some(id), bytes)
        }
        None => {
            let bytes = serde_json::to_vec_pretty(&requested)?;
            (requested, None, bytes)
        }
    };
    if encoded.len() as u64 > MANIFEST_LIMIT {
        return Err(invalid("derived manifest exceeds its byte budget"));
    }
    let existing = gate.catalog.derived.as_ref().and_then(|state| {
        state
            .references
            .iter()
            .find(|(_, reference)| reference.manifest.path == destination)
            .map(|(id, reference)| (*id, reference.clone()))
    });
    let (id, mut reference) = if let Some((id, reference)) = existing {
        if reference.manifest.file_id != manifest_id
            || reference.record != record
            || reference.manifest.sha256 != hash_bytes(&encoded)
        {
            return Err(invalid(
                "retained derived admission differs from the manifest",
            ));
        }
        (id, reference)
    } else {
        let id = Uuid::now_v7();
        (
            id,
            Reference {
                record: record.clone(),
                manifest: Manifest {
                    path: destination.to_path_buf(),
                    parents: parent_bindings(&root, destination)?,
                    file_id: manifest_id.clone(),
                    sha256: hash_bytes(&encoded),
                },
                staging: manifest_id.is_none().then_some(Staging { file_id: None }),
                phase: Phase::Prepared,
                proxy_origin: (admission.is_some() && manifest_id.is_none()).then_some(id),
            },
        )
    };
    if let Some(admission) = admission {
        admission.attach(&mut gate, id, &reference)?;
    }
    let digest = record.blob_sha256.clone();
    {
        let state = state_mut(&mut gate.catalog);
        state.references.insert(id, reference.clone());
        state.retention.insert(digest.clone(), None);
    }
    gate.save()?;
    if reference
        .staging
        .as_ref()
        .is_some_and(|stage| stage.file_id.is_none())
    {
        let path = staging_path(id, &reference)?;
        let mut file = open_control(&path, false, true, true)?;
        let file_id = file_identity(&file)?;
        reference.staging = Some(Staging {
            file_id: Some(file_id.clone()),
        });
        reference.manifest.file_id = Some(file_id);
        proxy::bind_manifest(&mut gate, id, &reference)?;
        state_mut(&mut gate.catalog)
            .references
            .insert(id, reference.clone());
        gate.save()?;
        file.write_all(&encoded)?;
        file.sync_all()?;
    }
    validate_reference(&root, &reference)?;
    let blob_path = digest_path(&root.root, &digest)?;
    let missing = match fs::symlink_metadata(&blob_path) {
        Ok(_) => false,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
        Err(error) => return Err(error.into()),
    };
    let publication_id = Uuid::now_v7();
    if missing {
        // The prior physical claim names an absent publication, including a
        // reservation whose writer died before its hardlink. Retire only that
        // claim; its intent, captures and SQL outcome keep their authority.
        gate.catalog.publications.remove(&digest);
        state_mut(&mut gate.catalog).publications.insert(
            digest.clone(),
            OwnedPublication {
                publication_id,
                descriptor: staged.descriptor().clone(),
                file_id: source_id.clone(),
                deleting: false,
            },
        );
        gate.save()?;
    }
    crate::file_import::publish_file_import_unmanaged(staged.captured_root(), staged)?;
    let actual_id = file_identity(&open_control(&blob_path, false, false, false)?)?;
    if missing
        && actual_id != source_id
        && gate.catalog.derived.as_ref().is_some_and(|state| {
            state
                .publications
                .get(&digest)
                .is_some_and(|value| value.publication_id == publication_id)
        })
    {
        state_mut(&mut gate.catalog).publications.remove(&digest);
        gate.save()?;
    }
    checkpoint(&root.root, "derived_after_blob_publication");
    if manifest_id.is_none() {
        let staging = staging_path(id, &reference)?;
        match fs::hard_link(&staging, destination) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                // This is a non-protocol contender: retain UNKNOWN unless it
                // is exactly our admitted physical manifest.
                let observed = read_manifest(destination)?
                    .ok_or_else(|| invalid("derived contender disappeared"))?;
                if !matches_manifest(&reference, &observed) {
                    return Err(invalid(
                        "derived manifest publication has an unknown winner",
                    ));
                }
            }
            Err(error) => return Err(error.into()),
        }
        sync_directory(
            destination
                .parent()
                .ok_or_else(|| invalid("manifest parent is missing"))?,
        )?;
    }
    checkpoint(&root.root, "derived_after_manifest_publication");
    let observed =
        read_manifest(destination)?.ok_or_else(|| invalid("published manifest disappeared"))?;
    if !matches_manifest(&reference, &observed) {
        return Err(invalid(
            "published derived manifest differs from its admission",
        ));
    }
    reference.phase = Phase::Active;
    if admission.is_none() {
        // A successful generic caller is independent filesystem authority.
        // Failed or interrupted generic attempts do not promote proxy custody.
        reference.proxy_origin = None;
    }
    state_mut(&mut gate.catalog)
        .references
        .insert(id, reference.clone());
    gate.save()?;
    cleanup_staging(id, &mut reference)?;
    state_mut(&mut gate.catalog)
        .references
        .insert(id, reference);
    gate.save()?;
    Ok(record)
}

// Unregistered manifests retain only metadata-eviction semantics. They confer
// no physical CAS ownership. None asks the caller to use that legacy behavior.
pub(crate) fn evict(
    blob_root: &Path,
    destination: &Path,
    proxy_guard: Option<&proxy::Guard>,
) -> Result<Option<bool>> {
    let Some(root) = discover(blob_root)? else {
        return Ok(None);
    };
    let mut gate = root.gate()?;
    proxy::check_eviction(proxy_guard, &gate)?;
    let ids = gate
        .catalog
        .derived
        .as_ref()
        .map(|state| {
            state
                .references
                .iter()
                .filter(|(_, reference)| reference.manifest.path == destination)
                .map(|(id, _)| *id)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if ids.is_empty() {
        return Ok(None);
    }
    let mut removed = false;
    let now = event_time(&root.root)?;
    for id in ids {
        let mut reference = gate
            .catalog
            .derived
            .as_ref()
            .and_then(|state| state.references.get(&id))
            .cloned()
            .ok_or_else(|| invalid("derived admission disappeared under gate"))?;
        validate_reference(&root, &reference)?;
        proxy::check_reference(&gate.catalog, id, &reference)?;
        let existed = retirement_manifest(&reference)?.is_some();
        reference.phase = Phase::Retiring;
        state_mut(&mut gate.catalog)
            .references
            .insert(id, reference);
        gate.save()?;
        checkpoint(&root.root, "derived_before_manifest_unlink");
        reconcile_reference(&mut gate, id, now)?;
        removed |= existed;
    }
    Ok(Some(removed))
}

pub(super) fn candidates(catalog: &Catalog) -> BTreeSet<RecoveryPosition> {
    let mut values = BTreeSet::new();
    if let Some(state) = &catalog.derived {
        values.extend(
            state
                .references
                .keys()
                .copied()
                .map(RecoveryPosition::DerivedReference),
        );
        values.extend(
            state
                .publications
                .keys()
                .cloned()
                .map(RecoveryPosition::DerivedPublication),
        );
        values.extend(
            state
                .retention
                .keys()
                .cloned()
                .map(RecoveryPosition::DerivedRetention),
        );
    }
    values
}

pub(super) fn recover_reference(
    root: &ManagedRoot,
    id: Uuid,
    now: u64,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let mut gate = root.gate()?;
    let digest = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|state| state.references.get(&id))
        .map(|reference| reference.record.blob_sha256.clone());
    match reconcile_reference(&mut gate, id, now) {
        Ok(true) => report.retained_by_pins += 1,
        Ok(false) => return Ok(digest.map(RecoveryPosition::DerivedPublication)),
        Err(_) => report.unresolved += 1,
    }
    Ok(None)
}

pub(super) fn recover_publication(
    root: &ManagedRoot,
    digest: &str,
    policy: CasRetentionPolicy,
    now: u64,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let mut gate = root.gate()?;
    let Some(publication) = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|state| state.publications.get(digest))
        .cloned()
    else {
        return Ok(None);
    };
    if gate.catalog.legacy_pins.contains(digest) {
        // Permanent authority is stronger. Retire only our cleanup metadata.
        state_mut(&mut gate.catalog).publications.remove(digest);
        gate.save()?;
        report.retained_by_pins += 1;
        return Ok(Some(RecoveryPosition::DerivedRetention(digest.to_owned())));
    }
    if is_pinned(&gate.catalog, digest, Uuid::nil()) {
        report.retained_by_pins += 1;
        return Ok(None);
    }
    if retains(&mut gate, digest, now, policy.retention)? {
        report.retained_by_age += 1;
        return Ok(None);
    }
    let file = match checked_owned_blob(root, digest, &publication.descriptor, &publication.file_id)
    {
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
        let current = state_mut(&mut gate.catalog)
            .publications
            .get_mut(digest)
            .ok_or_else(|| invalid("derived publication disappeared under gate"))?;
        if current.publication_id != publication.publication_id {
            report.unresolved += 1;
            return Ok(None);
        }
        current.deleting = true;
        gate.save()?;
        fs::remove_file(&path)?;
        sync_directory(
            path.parent()
                .ok_or_else(|| invalid("blob parent is missing"))?,
        )?;
        report.removed += 1;
        checkpoint(&root.root, "derived_after_blob_unlink");
        drop(file);
    }
    if gate.catalog.derived.as_ref().is_some_and(|state| {
        state
            .publications
            .get(digest)
            .is_some_and(|current| current.publication_id == publication.publication_id)
    }) {
        state_mut(&mut gate.catalog).publications.remove(digest);
        gate.save()?;
    }
    Ok(Some(RecoveryPosition::DerivedRetention(digest.to_owned())))
}

pub(super) fn recover_retention(
    root: &ManagedRoot,
    digest: &str,
    policy: CasRetentionPolicy,
    now: u64,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let mut gate = root.gate()?;
    let Some(state) = &gate.catalog.derived else {
        return Ok(None);
    };
    if !state.retention.contains_key(digest) {
        return Ok(None);
    }
    if state.publications.contains_key(digest) || gate.catalog.publications.contains_key(digest) {
        return Ok(None);
    }
    if protects(&gate.catalog, digest) {
        report.retained_by_pins += 1;
        return Ok(None);
    }
    if !gate.catalog.legacy_pins.contains(digest) {
        if is_pinned(&gate.catalog, digest, Uuid::nil()) {
            report.retained_by_pins += 1;
            return Ok(None);
        }
        if retains(&mut gate, digest, now, policy.retention)? {
            report.retained_by_age += 1;
            return Ok(None);
        }
    }
    state_mut(&mut gate.catalog).retention.remove(digest);
    gate.save()?;
    Ok(None)
}

fn maintenance_candidates(catalog: &Catalog, store: Option<&Store>) -> BTreeSet<RecoveryPosition> {
    let mut values = candidates(catalog);
    if let Some(store) = store {
        values.extend(proxy::candidates(catalog, Some(store)));
    }
    values.extend(
        catalog
            .captures
            .iter()
            .filter(|(_, capture)| capture.purpose == CapturePurpose::DerivedCache)
            .map(|(id, _)| RecoveryPosition::Capture(*id)),
    );
    // A retired lock has no SQL authority left to resolve.
    values.extend(
        catalog
            .retired_locks
            .keys()
            .copied()
            .map(RecoveryPosition::Retired),
    );
    values
}

// The generic cache API does not resolve another Store\'s SQL authority.
pub(crate) fn maintain(blob_root: &Path, policy: CasRetentionPolicy) -> Result<CasRecoveryReport> {
    maintain_inner(blob_root, policy, None)
}

// A single bounded queue shares the budget between filesystem maintenance and
// this Store\'s proxy bindings. SQL observation happens outside the root gate.
fn maintain_inner(
    blob_root: &Path,
    policy: CasRetentionPolicy,
    store: Option<&Store>,
) -> Result<CasRecoveryReport> {
    if policy.max_objects == 0 || policy.max_objects > MAX_RECOVERY_OBJECTS {
        return Err(invalid(
            "derived recovery object budget is outside supported bounds",
        ));
    }
    let Some(root) = discover(blob_root)? else {
        return Ok(CasRecoveryReport::default());
    };
    let access = proxy::acquire(blob_root, false)?;
    if matches!(access, proxy::Access::Busy) {
        return Ok(proxy::deferred_report());
    }
    let (mut queue, starting_cursor) = {
        let gate = root.gate()?;
        proxy::check_access(&access, &gate)?;
        if gate.catalog.derived.is_none() {
            return Ok(CasRecoveryReport {
                root_managed: true,
                ..Default::default()
            });
        }
        let mut values = maintenance_candidates(&gate.catalog, store)
            .into_iter()
            .collect::<Vec<_>>();
        let cursor = gate
            .catalog
            .derived
            .as_ref()
            .and_then(|state| state.maintenance_cursor.clone());
        if let Some(after) = &cursor {
            let split = values.partition_point(|value| value <= after);
            values.rotate_left(split);
        }
        (std::collections::VecDeque::from(values), cursor)
    };
    let mut report = CasRecoveryReport {
        root_managed: true,
        ..Default::default()
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
        let now = event_time(&root.root)?;
        let added = match candidate {
            RecoveryPosition::ProxyBinding(id) => {
                let store =
                    store.ok_or_else(|| invalid("SQL binding entered generic maintenance"))?;
                proxy::recover_candidate(store, &access, id, now, &mut report)?
            }
            RecoveryPosition::Capture(id) => {
                recover_capture_candidate(&root, id, policy, now, &mut report)?
            }
            RecoveryPosition::DerivedReference(id) => {
                recover_reference(&root, id, now, &mut report)?
            }
            RecoveryPosition::DerivedPublication(digest) => {
                recover_publication(&root, &digest, policy, now, &mut report)?
            }
            RecoveryPosition::DerivedRetention(digest) => {
                recover_retention(&root, &digest, policy, now, &mut report)?
            }
            RecoveryPosition::Retired(id) => {
                let mut gate = root.gate()?;
                if !gate.retire_lock(id).unwrap_or(false) {
                    report.unresolved += 1;
                }
                None
            }
            RecoveryPosition::Publication(_) | RecoveryPosition::Intent(_) => {
                return Err(invalid(
                    "SQL recovery candidate entered filesystem-only maintenance",
                ));
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
    proxy::check_access(&access, &gate)?;
    let current = maintenance_candidates(&gate.catalog, store);
    report.truncated = current.iter().any(|value| !visited.contains(value));
    let next = if report.truncated { last } else { None };
    let cursor = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|state| state.maintenance_cursor.clone());
    if (current.is_empty() || cursor == starting_cursor) && cursor != next {
        state_mut(&mut gate.catalog).maintenance_cursor = next;
        gate.save()?;
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::{TempDir, tempdir};

    const SCOPE: &str = "project:derived-custody";
    const KIND: &str = "render-window-proxy";
    const PAYLOAD: &[u8] = b"owned derived custody fixture";

    struct Fixture {
        _temp: TempDir,
        base: PathBuf,
        database: PathBuf,
        blob_root: PathBuf,
        source: PathBuf,
        fingerprint: String,
        digest: String,
    }

    impl Fixture {
        fn new() -> Self {
            let temp = tempdir().unwrap();
            let base = fs::canonicalize(temp.path()).unwrap();
            let database = base.join("case.sqlite3");
            let store = Store::open(&database).unwrap();
            let blob_root = fs::canonicalize(store.blob_root()).unwrap();
            drop(store);
            let source = base.join("derived-input.bin");
            fs::write(&source, PAYLOAD).unwrap();
            Self {
                _temp: temp,
                base,
                database,
                blob_root,
                source,
                fingerprint: hash_bytes(b"derived-custody-recipe"),
                digest: hash_bytes(PAYLOAD),
            }
        }

        fn open(&self) -> Store {
            Store::open(&self.database).unwrap()
        }

        fn put(&self, store: &Store) -> DerivedCacheRecord {
            store
                .put_derived_cache_file(SCOPE, KIND, &self.fingerprint, &self.source, 17)
                .unwrap()
        }

        fn evict(&self, store: &Store) {
            assert!(
                store
                    .evict_derived_cache_exact(SCOPE, KIND, &self.fingerprint)
                    .unwrap()
            );
        }

        fn blob(&self) -> PathBuf {
            digest_path(&self.blob_root, &self.digest).unwrap()
        }

        fn assert_blob(&self) {
            assert_eq!(fs::read(self.blob()).unwrap(), PAYLOAD);
            assert_eq!(fs::read(&self.source).unwrap(), PAYLOAD);
        }
    }

    fn zero() -> CasRetentionPolicy {
        CasRetentionPolicy {
            retention: Duration::ZERO,
            max_objects: 64,
        }
    }

    struct Clock {
        root: PathBuf,
    }

    impl Clock {
        fn new(root: &Path, now: u64) -> Self {
            let root = fs::canonicalize(root).unwrap();
            TEST_CLOCKS.with(|clocks| {
                assert!(clocks.borrow_mut().insert(root.clone(), now).is_none());
            });
            Self { root }
        }

        fn set(&self, now: u64) {
            TEST_CLOCKS.with(|clocks| {
                let mut clocks = clocks.borrow_mut();
                let previous = clocks.get_mut(&self.root).unwrap();
                assert!(now >= *previous, "test time must be monotonic");
                *previous = now;
            });
        }
    }

    impl Drop for Clock {
        fn drop(&mut self) {
            TEST_CLOCKS.with(|clocks| {
                clocks.borrow_mut().remove(&self.root);
            });
        }
    }

    #[test]
    fn catalog_v1_checksum_and_legacy_pins_survive_lazy_derived_upgrade() {
        let fixture = Fixture::new();
        let store = fixture.open();
        let legacy_source = fixture.base.join("legacy-source.bin");
        fs::write(&legacy_source, b"legacy source stays protected").unwrap();
        let legacy = store.ingest_blob_file(&legacy_source).unwrap();
        let root = discover(&fixture.blob_root).unwrap().unwrap();
        let catalog_path = root.control.join("catalog.json");
        let before = fs::read(&catalog_path).unwrap();
        let envelope: CatalogEnvelope = serde_json::from_slice(&before).unwrap();
        assert_eq!(envelope.catalog.format_version, FORMAT_VERSION);
        assert!(envelope.catalog.derived.is_none());
        assert_eq!(encode_catalog(&envelope.catalog).unwrap(), before);
        let pins = envelope.catalog.legacy_pins.clone();
        assert!(pins.contains(&legacy.sha256));
        fixture.put(&store);
        let gate = root.gate().unwrap();
        assert_eq!(gate.catalog.format_version, CATALOG_FORMAT_VERSION);
        assert_eq!(gate.catalog.legacy_pins, pins);
        assert_eq!(gate.catalog.derived.as_ref().unwrap().references.len(), 1);
        let anchor: RootAnchor = read_control(&root.root.join(ROOT_ANCHOR)).unwrap();
        assert_eq!(anchor.format_version, FORMAT_VERSION);
        drop(gate);
        drop(store);
        let mut reopened = fixture.open();
        assert_eq!(reopened.recover_cas(zero()).unwrap().removed, 0);
        fixture.assert_blob();
        assert_eq!(
            fs::read(reopened.blob_path(&legacy.sha256).unwrap()).unwrap(),
            b"legacy source stays protected"
        );
    }

    #[test]
    fn last_manifest_and_later_lease_release_each_start_a_full_retention_window() {
        let fixture = Fixture::new();
        let start = now_ms().unwrap();
        let clock = Clock::new(&fixture.blob_root, start);
        let day = 24 * 60 * 60 * 1000;
        let mut store = fixture.open();
        fixture.put(&store);
        clock.set(start + 30 * day);
        assert_eq!(
            store
                .recover_cas(CasRetentionPolicy::default())
                .unwrap()
                .removed,
            0
        );
        fixture.evict(&store);
        assert_eq!(
            store
                .recover_cas(CasRetentionPolicy::default())
                .unwrap()
                .removed,
            0
        );
        clock.set(start + 30 * day + 23 * 60 * 60 * 1000);
        assert_eq!(
            store
                .recover_cas(CasRetentionPolicy::default())
                .unwrap()
                .removed,
            0
        );
        let lease = store.lease_blob(&fixture.digest).unwrap();
        clock.set(start + 32 * day);
        assert_eq!(
            store
                .recover_cas(CasRetentionPolicy::default())
                .unwrap()
                .removed,
            0
        );
        store.release_blob_lease(lease).unwrap();
        drop(store);
        let mut store = fixture.open();
        // Opening runs the real conservative filesystem maintenance path.
        fixture.assert_blob();
        clock.set(start + 33 * day - 1);
        assert_eq!(
            store
                .recover_cas(CasRetentionPolicy::default())
                .unwrap()
                .removed,
            0
        );
        fixture.assert_blob();
        clock.set(start + 33 * day);
        assert_eq!(
            store
                .recover_cas(CasRetentionPolicy::default())
                .unwrap()
                .removed,
            1
        );
        assert_eq!(
            fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
    }

    #[test]
    fn sibling_managed_roots_cannot_share_or_revoke_each_others_manifest() {
        let fixture = Fixture::new();
        let mut first = fixture.open();
        let a = fixture.put(&first);
        let second_source = fixture.base.join("other-derived.bin");
        fs::write(&second_source, b"other root output").unwrap();
        let second_root = fixture.base.join("other-blobs");
        let second =
            Store::open_with_managed_blob_root(fixture.base.join("other.sqlite3"), &second_root)
                .unwrap();
        let b = second
            .put_derived_cache_file(SCOPE, KIND, &fixture.fingerprint, &second_source, 41)
            .unwrap();
        let root_a = discover(&fixture.blob_root).unwrap().unwrap();
        let root_b = discover(&second_root).unwrap().unwrap();
        let path_a = expected_path(&root_a, &a).unwrap();
        let path_b = expected_path(&root_b, &b).unwrap();
        assert_ne!(path_a, path_b);
        let bytes_b = fs::read(&path_b).unwrap();
        fixture.evict(&first);
        assert_eq!(first.recover_cas(zero()).unwrap().removed, 1);
        assert_eq!(fs::read(&path_b).unwrap(), bytes_b);
        assert_eq!(
            fs::read(second.blob_path(&b.blob_sha256).unwrap()).unwrap(),
            b"other root output"
        );
        assert_eq!(fs::read(&second_source).unwrap(), b"other root output");
    }

    fn exit_at(fixture: &Fixture, mode: &str, phase: &str) {
        crate::cas_retention_process_tests::run_derived_exit_checkpoint(
            &fixture.base,
            &fixture.blob_root,
            mode,
            phase,
        );
    }

    #[test]
    fn derived_journal_actor() {
        let Some(base) = std::env::var_os("MOTIONWRIGHT_TEST_DERIVED_BASE") else {
            return;
        };
        let base = PathBuf::from(base);
        let mut store = Store::open(base.join("case.sqlite3")).unwrap();
        let fingerprint = hash_bytes(b"derived-custody-recipe");
        match std::env::var("MOTIONWRIGHT_TEST_DERIVED_MODE")
            .unwrap()
            .as_str()
        {
            "put" => {
                store
                    .put_derived_cache_file(
                        SCOPE,
                        KIND,
                        &fingerprint,
                        base.join("derived-input.bin"),
                        17,
                    )
                    .unwrap();
            }
            "put_proxy" => {
                let key: crate::RenderWindowCacheKey =
                    serde_json::from_slice(&fs::read(base.join("proxy-key.json")).unwrap())
                        .unwrap();
                store
                    .put_render_window_proxy(&key, base.join("derived-input.bin"), 17)
                    .unwrap();
            }
            "proxy_recover" => {
                let key: crate::RenderWindowCacheKey =
                    serde_json::from_slice(&fs::read(base.join("proxy-key.json")).unwrap())
                        .unwrap();
                let report = store.recover_cas(zero()).unwrap();
                let project = store.load_project(key.project_id).unwrap();
                fs::write(
                    base.join("proxy-recovery.json"),
                    serde_json::to_vec(&serde_json::json!({
                        "removed": report.removed, "inspected": report.inspected,
                        "unresolved": report.unresolved, "truncated": report.truncated,
                        "project": project,
                    }))
                    .unwrap(),
                )
                .unwrap();
            }
            "evict" => {
                assert!(
                    store
                        .evict_derived_cache_exact(SCOPE, KIND, &fingerprint)
                        .unwrap()
                );
            }
            "import" => {
                store
                    .import_project_bundle(base.join("import-bundle"))
                    .unwrap();
            }
            "gc" => {
                store.recover_cas(zero()).unwrap();
            }
            _ => panic!("unexpected derived subprocess mode"),
        }
        panic!("derived subprocess did not reach its exact cut");
    }

    #[test]
    fn real_process_publication_cuts_recover_fs_admission_without_sql_authority() {
        for phase in [
            "derived_after_blob_publication",
            "derived_after_manifest_publication",
        ] {
            let fixture = Fixture::new();
            exit_at(&fixture, "put", phase);
            fixture.assert_blob();
            let root = discover(&fixture.blob_root).unwrap().unwrap();
            let gate = root.gate().unwrap();
            let state = gate.catalog.derived.as_ref().unwrap();
            assert_eq!(state.references.len(), 1);
            let reference = state.references.values().next().unwrap();
            assert!(matches!(reference.phase, Phase::Prepared));
            let manifest = reference.manifest.path.clone();
            let linked = phase == "derived_after_manifest_publication";
            assert_eq!(fs::symlink_metadata(&manifest).is_ok(), linked);
            assert!(!gate.catalog.legacy_pins.contains(&fixture.digest));
            drop(gate);
            let mut store = fixture.open();
            let gate = root.gate().unwrap();
            if linked {
                assert!(
                    gate.catalog
                        .derived
                        .as_ref()
                        .unwrap()
                        .references
                        .values()
                        .all(|reference| matches!(reference.phase, Phase::Active))
                );
            } else {
                assert!(gate.catalog.derived.as_ref().unwrap().references.is_empty());
            }
            drop(gate);
            if linked {
                assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
                fixture.assert_blob();
                fixture.evict(&store);
            }
            assert_eq!(store.recover_cas(zero()).unwrap().removed, 1);
            assert_eq!(
                fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
                std::io::ErrorKind::NotFound
            );
            assert_eq!(
                fs::symlink_metadata(&manifest).unwrap_err().kind(),
                std::io::ErrorKind::NotFound
            );
            assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
            assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
        }
    }

    #[test]
    fn real_process_eviction_cut_preserves_retirement_until_recovery() {
        let fixture = Fixture::new();
        let store = fixture.open();
        fixture.put(&store);
        drop(store);
        exit_at(&fixture, "evict", "derived_after_manifest_unlink");
        fixture.assert_blob();
        let root = discover(&fixture.blob_root).unwrap().unwrap();
        let gate = root.gate().unwrap();
        let state = gate.catalog.derived.as_ref().unwrap();
        assert_eq!(state.references.len(), 1);
        let reference = state.references.values().next().unwrap();
        assert!(matches!(reference.phase, Phase::Retiring));
        assert_eq!(
            fs::symlink_metadata(&reference.manifest.path)
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::NotFound
        );
        drop(gate);
        let mut store = fixture.open();
        fixture.assert_blob();
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 1);
        assert_eq!(
            fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
    }

    #[test]
    fn real_process_unlink_cut_cannot_delete_a_new_publication_of_the_same_digest() {
        let fixture = Fixture::new();
        let store = fixture.open();
        fixture.put(&store);
        let root = discover(&fixture.blob_root).unwrap().unwrap();
        let old = root
            .gate()
            .unwrap()
            .catalog
            .derived
            .as_ref()
            .unwrap()
            .publications
            .get(&fixture.digest)
            .unwrap()
            .clone();
        // Retain the old inode under a separate fixture name so inode reuse
        // cannot make the physical-identity comparison pass accidentally.
        let old_link = fixture.base.join("old-publication-witness");
        fs::hard_link(fixture.blob(), &old_link).unwrap();
        fixture.evict(&store);
        drop(store);
        exit_at(&fixture, "gc", "derived_after_blob_unlink");
        assert_eq!(
            fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        let gate = root.gate().unwrap();
        assert!(
            gate.catalog
                .derived
                .as_ref()
                .unwrap()
                .publications
                .get(&fixture.digest)
                .unwrap()
                .deleting
        );
        drop(gate);
        let mut store = fixture.open();
        fixture.put(&store);
        let new = root
            .gate()
            .unwrap()
            .catalog
            .derived
            .as_ref()
            .unwrap()
            .publications
            .get(&fixture.digest)
            .unwrap()
            .clone();
        assert_ne!(new.publication_id, old.publication_id);
        assert_ne!(new.file_id, old.file_id);
        assert_eq!(
            file_identity(&open_control(&fixture.blob(), false, false, false).unwrap()).unwrap(),
            new.file_id
        );
        assert_eq!(
            file_identity(&open_control(&old_link, false, false, false).unwrap()).unwrap(),
            old.file_id
        );
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
        fixture.assert_blob();
        fixture.evict(&store);
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 1);
        assert_eq!(fs::read(&old_link).unwrap(), PAYLOAD);
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
    }

    fn all_sql_rows(store: &Store) -> BTreeMap<String, Vec<Vec<String>>> {
        let names = {
            let mut statement = store.conn.prepare(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            ).unwrap();
            statement
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap()
                .map(|row| row.unwrap())
                .collect::<Vec<_>>()
        };
        let mut snapshot = BTreeMap::new();
        for name in names {
            let query = format!("SELECT * FROM \"{}\"", name.replace('"', "\"\""));
            let mut statement = store.conn.prepare(&query).unwrap();
            let columns = statement.column_count();
            let mut rows = statement
                .query_map([], |row| {
                    (0..columns)
                        .map(|column| {
                            row.get::<_, rusqlite::types::Value>(column)
                                .map(|value| format!("{value:?}"))
                        })
                        .collect::<rusqlite::Result<Vec<_>>>()
                })
                .unwrap()
                .map(|row| row.unwrap())
                .collect::<Vec<_>>();
            rows.sort();
            snapshot.insert(name, rows);
        }
        snapshot
    }

    #[test]
    fn real_import_reservation_cut_does_not_pin_a_later_derived_publication_forever() {
        let fixture = Fixture::new();
        let mut source = Store::open(fixture.base.join("bundle-source/state.sqlite3")).unwrap();
        let descriptor = source.ingest_blob_file(&fixture.source).unwrap();
        let project = source
            .create_named_project("Derived reservation source")
            .unwrap();
        let project = source
            .apply(
                project.id,
                &project.stamp(),
                "reservation-source-asset",
                &motionwright_domain::Change::AddAsset {
                    asset: motionwright_domain::Asset {
                        id: Uuid::now_v7(),
                        name: "reservation source".into(),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(descriptor.sha256.clone()),
                        source_revision: Some("local-import".into()),
                    },
                },
            )
            .unwrap()
            .project;
        let bundle = fixture.base.join("import-bundle");
        let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        assert_eq!(manifest.blobs.len(), 1);
        let manifest_bytes = fs::read(bundle.join("manifest.json")).unwrap();
        let bundle_blob = bundle.join(&manifest.blobs[0].relative_path);
        drop(source);
        let before = all_sql_rows(&fixture.open());

        exit_at(&fixture, "import", "import_after_publication_reservation");
        assert_eq!(
            fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        let root = discover(&fixture.blob_root).unwrap().unwrap();
        let gate = root.gate().unwrap();
        let old = gate
            .catalog
            .publications
            .get(&fixture.digest)
            .unwrap()
            .clone();
        assert!(
            !old.deleting,
            "this cut precedes physical publication, not GC"
        );
        let intent = gate.catalog.intents.get(&old.intent_id).unwrap();
        assert_eq!(intent.captures.len(), 1);
        let capture_id = *intent.captures.iter().next().unwrap();
        let capture = gate.catalog.captures.get(&capture_id).unwrap();
        assert_eq!(capture.file_id.as_deref(), Some(old.file_id.as_str()));
        let witness = fixture.base.join("prior-import-capture-witness");
        fs::hard_link(capture_path(&root, capture_id, capture).unwrap(), &witness).unwrap();
        drop(gate);

        let mut store = fixture.open();
        assert_eq!(all_sql_rows(&store), before);
        fixture.put(&store);
        let gate = root.gate().unwrap();
        assert!(
            !gate.catalog.publications.contains_key(&fixture.digest),
            "an absent prior reservation cannot own the new inode"
        );
        assert!(
            gate.catalog.intents.contains_key(&old.intent_id),
            "publication handoff must not decide the import outcome"
        );
        assert!(gate.catalog.captures.contains_key(&capture_id));
        let new = gate
            .catalog
            .derived
            .as_ref()
            .unwrap()
            .publications
            .get(&fixture.digest)
            .unwrap();
        assert_ne!(new.file_id, old.file_id);
        assert!(!gate.catalog.legacy_pins.contains(&fixture.digest));
        drop(gate);
        fixture.evict(&store);
        let report = store.recover_cas(zero()).unwrap();
        assert_eq!(report.unresolved, 0, "{report:?}");
        assert_eq!(report.removed, 1, "{report:?}");
        assert_eq!(
            fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        assert_eq!(all_sql_rows(&store), before);
        assert_eq!(fs::read(&witness).unwrap(), PAYLOAD);
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
        assert_eq!(fs::read(bundle_blob).unwrap(), PAYLOAD);
        assert_eq!(
            fs::read(bundle.join("manifest.json")).unwrap(),
            manifest_bytes
        );
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
    }
    #[test]
    fn derived_cache_real_proxy_manifest_cut_retires_only_unregistered_admission() {
        // Run the existing-winner control before the expected RED case. Both
        // use the public proxy producer; neither borrows a published PathBuf.
        for already_registered in [true, false] {
            let fixture = Fixture::new();
            let mut store = fixture.open();
            let project = store
                .create_named_project("Interrupted proxy admission")
                .unwrap();
            let project = store
                .apply(
                    project.id,
                    &project.stamp(),
                    "proxy-admission-scene",
                    &motionwright_domain::Change::AddScene {
                        name: "Source".into(),
                        objective: "Proxy admission owner".into(),
                        duration_seconds: 2,
                    },
                )
                .unwrap()
                .project;
            let original_source = fixture.base.join("original-source.bin");
            let original_bytes = b"project source must survive failed proxy admission";
            fs::write(&original_source, original_bytes).unwrap();
            let original_blob = store.ingest_blob_file(&original_source).unwrap();
            assert_ne!(original_blob.sha256, fixture.digest);
            let project = store
                .apply(
                    project.id,
                    &project.stamp(),
                    "proxy-admission-source",
                    &motionwright_domain::Change::AddAsset {
                        asset: motionwright_domain::Asset {
                            id: Uuid::now_v7(),
                            name: "Owned source".into(),
                            media_type: "application/octet-stream".into(),
                            content_sha256: Some(original_blob.sha256.clone()),
                            source_revision: Some("fixture-source-v1".into()),
                        },
                    },
                )
                .unwrap()
                .project;
            let key = crate::RenderWindowCacheKey {
                project_id: project.id,
                expected: project.stamp(),
                deliverable_id: project.deliverables[0].id,
                source_window: motionwright_domain::SourceFrameWindow { start: 0, end: 60 },
                fingerprint_sha256: fixture.fingerprint.clone(),
            };
            let mut winner_key = key.clone();
            if !already_registered {
                winner_key.fingerprint_sha256 = hash_bytes(b"independent committed proxy");
            }
            let winner_input = fixture.base.join("winner-input.bin");
            let winner_bytes: &[u8] = if already_registered {
                PAYLOAD
            } else {
                b"different committed proxy must remain unchanged"
            };
            fs::write(&winner_input, winner_bytes).unwrap();
            let winner = store
                .put_render_window_proxy(&winner_key, &winner_input, 17)
                .unwrap();
            assert_eq!(winner.blob_sha256 == fixture.digest, already_registered);
            let winner_manifest = store
                .derived_cache_manifest_path(
                    &winner.scope_sha256,
                    &winner.kind,
                    &winner.fingerprint_sha256,
                )
                .unwrap();
            let winner_manifest_before = fs::read(&winner_manifest).unwrap();
            let root = discover(&fixture.blob_root).unwrap().unwrap();
            let gate = root.gate().unwrap();
            let (winner_reference_id, winner_reference) = gate
                .catalog
                .derived
                .as_ref()
                .unwrap()
                .references
                .iter()
                .find(|(_, reference)| reference.manifest.path == winner_manifest)
                .unwrap();
            let winner_reference_id = *winner_reference_id;
            let winner_reference_before = serde_json::to_value(winner_reference).unwrap();
            assert!(!gate.catalog.legacy_pins.contains(&fixture.digest));
            assert!(!gate.catalog.legacy_pins.contains(&winner.blob_sha256));
            drop(gate);
            let before_sql = all_sql_rows(&store);
            fs::write(
                fixture.base.join("proxy-key.json"),
                serde_json::to_vec(&key).unwrap(),
            )
            .unwrap();
            drop(store);

            exit_at(&fixture, "put_proxy", "derived_after_manifest_publication");
            // Observe the published exact bytes before Store::open can recover.
            fixture.assert_blob();
            let gate = root.gate().unwrap();
            let interrupted = gate
                .catalog
                .derived
                .as_ref()
                .unwrap()
                .references
                .values()
                .find(|reference| {
                    reference.record.fingerprint_sha256 == key.fingerprint_sha256
                        && reference.record.blob_sha256 == fixture.digest
                })
                .unwrap();
            let interrupted_manifest = interrupted.manifest.path.clone();
            let interrupted_record: DerivedCacheRecord =
                serde_json::from_slice(&fs::read(&interrupted_manifest).unwrap()).unwrap();
            assert_eq!(interrupted_record.blob_sha256, fixture.digest);
            assert_eq!(interrupted_record.size_bytes, PAYLOAD.len() as u64);
            assert!(!gate.catalog.legacy_pins.contains(&fixture.digest));
            drop(gate);
            let mut store = fixture.open();
            assert_eq!(all_sql_rows(&store), before_sql);
            assert_eq!(store.load_project(project.id).unwrap(), project);
            if !already_registered {
                assert!(store.lookup_render_window_proxy(&key).unwrap().is_none());
            }
            assert_eq!(
                store
                    .drain_render_window_evictions(64)
                    .unwrap()
                    .tombstones_selected,
                0
            );
            let report = store.recover_cas(zero()).unwrap();
            assert_eq!(report.unresolved, 0, "{report:?}");
            assert!(!report.truncated, "{report:?}");
            // Check the positive custody controls before the causal RED assertion.
            assert_eq!(all_sql_rows(&store), before_sql);
            assert_eq!(store.load_project(project.id).unwrap(), project);
            assert_eq!(fs::read(&original_source).unwrap(), original_bytes);
            assert_eq!(
                fs::read(digest_path(&fixture.blob_root, &original_blob.sha256).unwrap()).unwrap(),
                original_bytes
            );
            assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
            assert_eq!(fs::read(&winner_input).unwrap(), winner_bytes);
            assert_eq!(
                fs::read(digest_path(&fixture.blob_root, &winner.blob_sha256).unwrap()).unwrap(),
                winner_bytes
            );
            assert_eq!(fs::read(&winner_manifest).unwrap(), winner_manifest_before);
            let gate = root.gate().unwrap();
            let retained_winner = gate
                .catalog
                .derived
                .as_ref()
                .unwrap()
                .references
                .get(&winner_reference_id)
                .unwrap();
            assert_eq!(
                serde_json::to_value(retained_winner).unwrap(),
                winner_reference_before
            );
            assert!(!gate.catalog.legacy_pins.contains(&fixture.digest));
            assert!(!gate.catalog.legacy_pins.contains(&winner.blob_sha256));
            assert!(gate.catalog.captures.is_empty());
            drop(gate);
            assert!(
                fs::read_dir(fixture.blob_root.join(".staging"))
                    .unwrap()
                    .next()
                    .is_none()
            );
            assert_eq!(
                report.removed,
                usize::from(!already_registered),
                "an interrupted fresh proxy must retire its unregistered admission; \
                 the same-key committed winner must remain: {report:?}"
            );
            if !already_registered {
                assert_eq!(
                    fs::symlink_metadata(&interrupted_manifest)
                        .unwrap_err()
                        .kind(),
                    std::io::ErrorKind::NotFound
                );
                assert_eq!(
                    fs::symlink_metadata(fixture.blob()).unwrap_err().kind(),
                    std::io::ErrorKind::NotFound
                );
            }
            drop(store);
            let mut reopened = fixture.open();
            assert_eq!(reopened.recover_cas(zero()).unwrap().removed, 0);
            assert_eq!(all_sql_rows(&reopened), before_sql);
            assert_eq!(reopened.load_project(project.id).unwrap(), project);
            assert_eq!(fs::read(&original_source).unwrap(), original_bytes);
            assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
            assert_eq!(fs::read(&winner_manifest).unwrap(), winner_manifest_before);
            assert_eq!(
                fs::read(digest_path(&fixture.blob_root, &winner.blob_sha256).unwrap()).unwrap(),
                winner_bytes
            );
        }
    }

    fn proxy_case(
        store: &mut Store,
        fixture: &Fixture,
    ) -> (motionwright_domain::Project, crate::RenderWindowCacheKey) {
        let project = store.create_named_project("Proxy custody control").unwrap();
        let project = store
            .apply(
                project.id,
                &project.stamp(),
                "proxy-control-scene",
                &motionwright_domain::Change::AddScene {
                    name: "Source".into(),
                    objective: "Proxy custody".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let key = crate::RenderWindowCacheKey {
            project_id: project.id,
            expected: project.stamp(),
            deliverable_id: project.deliverables[0].id,
            source_window: motionwright_domain::SourceFrameWindow { start: 0, end: 60 },
            fingerprint_sha256: fixture.fingerprint.clone(),
        };
        fs::write(
            fixture.base.join("proxy-key.json"),
            serde_json::to_vec(&key).unwrap(),
        )
        .unwrap();
        (project, key)
    }

    #[test]
    fn derived_cache_proxy_recovery_preserves_successful_generic_adoption() {
        let fixture = Fixture::new();
        let mut store = fixture.open();
        let (project, key) = proxy_case(&mut store, &fixture);
        let rows = all_sql_rows(&store);
        exit_at(&fixture, "put_proxy", "derived_after_manifest_publication");
        let owner = crate::render_window_cache::scope(&key);
        let record = store
            .put_derived_cache_file(&owner, KIND, &key.fingerprint_sha256, &fixture.source, 17)
            .unwrap();
        let manifest = store
            .derived_cache_manifest_path(&record.scope_sha256, KIND, &key.fingerprint_sha256)
            .unwrap();
        let bytes = fs::read(&manifest).unwrap();
        let report = store.recover_cas(zero()).unwrap();
        assert_eq!(report.unresolved, 0, "{report:?}");
        assert_eq!(report.removed, 0, "{report:?}");
        assert_eq!(all_sql_rows(&store), rows);
        assert_eq!(store.load_project(project.id).unwrap(), project);
        assert!(store.lookup_render_window_proxy(&key).unwrap().is_none());
        assert_eq!(fs::read(&manifest).unwrap(), bytes);
        fixture.assert_blob();
        drop(store);
        let mut store = fixture.open();
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 0);
        assert_eq!(fs::read(&manifest).unwrap(), bytes);
        assert!(
            store
                .evict_derived_cache_exact(&owner, KIND, &key.fingerprint_sha256)
                .unwrap()
        );
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 1);
        assert_eq!(all_sql_rows(&store), rows);
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
    }

    #[test]
    fn derived_cache_shared_database_proxy_withdrawal_preserves_peer_until_explicit_eviction() {
        let fixture = Fixture::new();
        let mut first = fixture.open();
        let (project, key) = proxy_case(&mut first, &fixture);
        let mut second = Store::open_with_managed_blob_root(
            fixture.base.join("peer.sqlite3"),
            &fixture.blob_root,
        )
        .unwrap();
        second.create_project(&project).unwrap();
        assert_ne!(first.cas_database_id, second.cas_database_id);
        assert_ne!(first.cas_store_id, second.cas_store_id);
        let record = first
            .put_render_window_proxy(&key, &fixture.source, 17)
            .unwrap();
        second
            .put_render_window_proxy(&key, &fixture.source, 17)
            .unwrap();
        let manifest = first
            .derived_cache_manifest_path(&record.scope_sha256, KIND, &key.fingerprint_sha256)
            .unwrap();
        let before = fs::read(&manifest).unwrap();
        let second_rows = all_sql_rows(&second);
        first
            .apply(
                project.id,
                &project.stamp(),
                "proxy-owner-revision",
                &motionwright_domain::Change::AddScene {
                    name: "Next".into(),
                    objective: "Revoke only primary".into(),
                    duration_seconds: 1,
                },
            )
            .unwrap();
        assert_eq!(
            first
                .drain_render_window_evictions(64)
                .unwrap()
                .tombstones_selected,
            0
        );
        assert_eq!(all_sql_rows(&second), second_rows);
        assert_eq!(second.load_project(project.id).unwrap(), project);
        assert_eq!(fs::read(&manifest).unwrap(), before);
        assert_eq!(second.recover_cas(zero()).unwrap().removed, 0);
        fixture.assert_blob();
        // This public API has always been global exact manifest revocation.
        // Its explicit call may turn the peer's retained SQL key into a miss.
        assert!(
            first
                .evict_derived_cache_exact(
                    &crate::render_window_cache::scope(&key),
                    KIND,
                    &key.fingerprint_sha256
                )
                .unwrap()
        );
        assert!(second.lookup_render_window_proxy(&key).unwrap().is_none());
        assert_eq!(all_sql_rows(&second), second_rows);
        assert_eq!(second.recover_cas(zero()).unwrap().removed, 1);
        assert_eq!(all_sql_rows(&second), second_rows);
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
    }

    #[test]
    fn derived_cache_deleted_project_foreign_keys_leave_proxy_custody_recoverable() {
        let fixture = Fixture::new();
        let mut store = fixture.open();
        let (project, key) = proxy_case(&mut store, &fixture);
        let record = store
            .put_render_window_proxy(&key, &fixture.source, 17)
            .unwrap();
        let manifest = store
            .derived_cache_manifest_path(&record.scope_sha256, KIND, &key.fingerprint_sha256)
            .unwrap();
        // Exercise actual SQLite FK deletion, not a claimed public deletion UI.
        assert_eq!(
            store
                .conn
                .execute("DELETE FROM projects WHERE id=?1", [project.id.to_string()])
                .unwrap(),
            1
        );
        assert_eq!(
            store
                .conn
                .query_row(
                    "SELECT count(*) FROM render_window_cache_entries",
                    [],
                    |r| r.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(
            store
                .conn
                .query_row(
                    "SELECT count(*) FROM render_window_cache_evictions",
                    [],
                    |r| r.get::<_, i64>(0)
                )
                .unwrap(),
            0
        );
        let rows = all_sql_rows(&store);
        drop(store);
        let mut store = fixture.open();
        let report = store.recover_cas(zero()).unwrap();
        assert_eq!(report.removed, 1, "{report:?}");
        assert_eq!(report.unresolved, 0, "{report:?}");
        assert_eq!(all_sql_rows(&store), rows);
        assert!(!manifest.exists());
        assert!(!fixture.blob().exists());
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
    }

    #[test]
    fn derived_cache_live_proxy_writer_defers_store_open_and_gc_without_waiting_for_catalog_gate() {
        let fixture = Fixture::new();
        let mut store = fixture.open();
        let (project, key) = proxy_case(&mut store, &fixture);
        drop(store);
        crate::cas_retention_process_tests::with_paused_derived_checkpoint(
            &fixture.base,
            &fixture.blob_root,
            "put_proxy",
            "derived_after_manifest_publication",
            || {
                crate::cas_retention_process_tests::run_derived_completed(
                    &fixture.base,
                    &fixture.blob_root,
                    "proxy_recover",
                );
                let observation: serde_json::Value = serde_json::from_slice(
                    &fs::read(fixture.base.join("proxy-recovery.json")).unwrap(),
                )
                .unwrap();
                assert_eq!(observation["removed"], 0);
                assert_eq!(observation["inspected"], 0);
                assert_eq!(observation["unresolved"], 1);
                assert_eq!(observation["truncated"], true);
                assert_eq!(
                    observation["project"],
                    serde_json::to_value(&project).unwrap()
                );
                fixture.assert_blob();
            },
        );
        let mut reopened = fixture.open();
        assert_eq!(reopened.load_project(project.id).unwrap(), project);
        let report = reopened.recover_cas(zero()).unwrap();
        assert_eq!(report.removed, 0);
        assert_eq!(report.unresolved, 0);
        let count: i64 = reopened
            .conn
            .query_row(
                "SELECT count(*) FROM render_window_cache_entries WHERE project_id=?1",
                [key.project_id.to_string()],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 1);
        fixture.assert_blob();
    }
    #[test]
    fn derived_cache_old_tombstone_does_not_revoke_a_registered_replacement_reference() {
        let fixture = Fixture::new();
        let mut store = fixture.open();
        let (project, key) = proxy_case(&mut store, &fixture);
        let old = store
            .put_render_window_proxy(&key, &fixture.source, 17)
            .unwrap();
        let manifest = store
            .derived_cache_manifest_path(&old.scope_sha256, KIND, &key.fingerprint_sha256)
            .unwrap();
        let root = discover(&fixture.blob_root).unwrap().unwrap();
        let old_id = {
            let gate = root.gate().unwrap();
            *gate
                .catalog
                .derived
                .as_ref()
                .unwrap()
                .references
                .iter()
                .find(|(_, r)| r.manifest.path == manifest)
                .unwrap()
                .0
        };
        // Retain a real SQL tombstone across explicit revocation and a later
        // valid public admission for the same current key.
        store
            .conn
            .execute_batch(
                "BEGIN IMMEDIATE;
             INSERT INTO render_window_cache_evictions SELECT * FROM render_window_cache_entries;
             DELETE FROM render_window_cache_entries;
             COMMIT;",
            )
            .unwrap();
        assert!(
            store
                .evict_derived_cache_exact(
                    &crate::render_window_cache::scope(&key),
                    KIND,
                    &key.fingerprint_sha256
                )
                .unwrap()
        );
        let replacement_source = fixture.base.join("replacement-proxy.bin");
        let replacement_bytes = b"replacement with a new exact reference identity";
        fs::write(&replacement_source, replacement_bytes).unwrap();
        let new = store
            .put_render_window_proxy(&key, &replacement_source, 23)
            .unwrap();
        assert_ne!(new.blob_sha256, old.blob_sha256);
        let (new_id, reference_before) = {
            let gate = root.gate().unwrap();
            let (id, reference) = gate
                .catalog
                .derived
                .as_ref()
                .unwrap()
                .references
                .iter()
                .find(|(_, r)| r.manifest.path == manifest)
                .unwrap();
            (*id, serde_json::to_value(reference).unwrap())
        };
        assert_ne!(new_id, old_id);
        let manifest_before = fs::read(&manifest).unwrap();
        let report = store.drain_render_window_evictions(64).unwrap();
        assert_eq!(report.tombstones_selected, 1);
        assert_eq!(report.tombstones_cleared, 1);
        assert_eq!(report.manifests_removed, 0);
        assert_eq!(
            store
                .conn
                .query_row(
                    "SELECT count(*) FROM render_window_cache_entries",
                    [],
                    |r| r.get::<_, i64>(0)
                )
                .unwrap(),
            1
        );
        assert_eq!(fs::read(&manifest).unwrap(), manifest_before);
        assert_eq!(store.load_project(project.id).unwrap(), project);
        let recovered = store.recover_cas(zero()).unwrap();
        assert_eq!(
            recovered.removed, 1,
            "only the explicitly revoked old publication: {recovered:?}"
        );
        assert_eq!(recovered.unresolved, 0, "{recovered:?}");
        assert_eq!(
            fs::read(digest_path(&fixture.blob_root, &new.blob_sha256).unwrap()).unwrap(),
            replacement_bytes
        );
        let gate = root.gate().unwrap();
        assert_eq!(
            serde_json::to_value(
                gate.catalog
                    .derived
                    .as_ref()
                    .unwrap()
                    .references
                    .get(&new_id)
                    .unwrap()
            )
            .unwrap(),
            reference_before
        );
        drop(gate);
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
        assert_eq!(fs::read(&replacement_source).unwrap(), replacement_bytes);
    }

    #[test]
    fn derived_cache_changed_proxy_lock_preserves_unresolved_state_and_bytes() {
        let fixture = Fixture::new();
        let mut store = fixture.open();
        let (_, key) = proxy_case(&mut store, &fixture);
        let record = store
            .put_render_window_proxy(&key, &fixture.source, 17)
            .unwrap();
        let manifest = store
            .derived_cache_manifest_path(&record.scope_sha256, KIND, &key.fingerprint_sha256)
            .unwrap();
        store
            .conn
            .execute("DELETE FROM render_window_cache_entries", [])
            .unwrap();
        let root = discover(&fixture.blob_root).unwrap().unwrap();
        let lock_id = {
            let gate = root.gate().unwrap();
            let value = serde_json::to_value(&gate.catalog).unwrap();
            Uuid::parse_str(value["derived"]["proxy"]["lock"]["id"].as_str().unwrap()).unwrap()
        };
        let lock_path = root.lock_path(lock_id);
        let original_id =
            file_identity(&open_control(&lock_path, false, true, false).unwrap()).unwrap();
        fs::rename(&lock_path, fixture.base.join("displaced-proxy.lock")).unwrap();
        let replacement = File::create_new(&lock_path).unwrap();
        replacement.sync_all().unwrap();
        let replacement_id = file_identity(&replacement).unwrap();
        assert_ne!(original_id, replacement_id);
        drop(replacement);
        let catalog_path = root.control.join("catalog.json");
        let before_catalog = fs::read(&catalog_path).unwrap();
        let before_manifest = fs::read(&manifest).unwrap();
        let before_sql = all_sql_rows(&store);
        assert!(store.recover_cas(zero()).is_err());
        assert_eq!(fs::read(&catalog_path).unwrap(), before_catalog);
        assert_eq!(fs::read(&manifest).unwrap(), before_manifest);
        assert_eq!(all_sql_rows(&store), before_sql);
        assert_eq!(
            file_identity(&open_control(&lock_path, false, true, false).unwrap()).unwrap(),
            replacement_id
        );
        assert!(fs::read(&lock_path).unwrap().is_empty());
        fixture.assert_blob();
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
    }

    #[test]
    fn derived_cache_explicit_proxy_eviction_cannot_reveal_a_legacy_fallback() {
        let fixture = Fixture::new();
        let mut store = fixture.open();
        let (_, key) = proxy_case(&mut store, &fixture);
        let record = store
            .put_render_window_proxy(&key, &fixture.source, 17)
            .unwrap();
        let managed = store
            .derived_cache_manifest_path(&record.scope_sha256, KIND, &key.fingerprint_sha256)
            .unwrap();
        // A historical fallback manifest is fixture data, never new custody.
        let legacy = fixture
            .base
            .join("derived-cache")
            .join("sha256")
            .join(&record.scope_sha256[..2])
            .join(&record.scope_sha256)
            .join(managed.file_name().unwrap());
        fs::create_dir_all(legacy.parent().unwrap()).unwrap();
        fs::write(&legacy, serde_json::to_vec_pretty(&record).unwrap()).unwrap();
        let rows = all_sql_rows(&store);
        assert!(
            store
                .evict_derived_cache_exact(
                    &crate::render_window_cache::scope(&key),
                    KIND,
                    &key.fingerprint_sha256
                )
                .unwrap()
        );
        assert!(!managed.exists());
        assert!(!legacy.exists());
        assert!(store.lookup_render_window_proxy(&key).unwrap().is_none());
        assert_eq!(all_sql_rows(&store), rows);
        assert_eq!(store.recover_cas(zero()).unwrap().removed, 1);
        assert_eq!(all_sql_rows(&store), rows);
        assert_eq!(fs::read(&fixture.source).unwrap(), PAYLOAD);
    }
}
