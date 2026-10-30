//! Durable render-proxy admission beside the existing generic cache journal.
//! SQLite is inspected only while no catalog gate is held.
use super::*;
use crate::RenderWindowCacheKey;
use rusqlite::{OptionalExtension, params};
use std::cell::Cell;

const MAX_BINDINGS: usize = 16_384;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct BoundLock {
    id: Uuid,
    file_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Binding {
    store_id: Uuid,
    database_id: String,
    key: RenderWindowCacheKey,
    reference_id: Uuid,
    manifest_id: Option<String>,
    registered: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ProxyState {
    lock: BoundLock,
    bindings: BTreeMap<Uuid, Binding>,
}

pub(crate) struct Guard {
    root: ManagedRoot,
    lock: BoundLock,
    file: File,
}

pub(crate) enum Access {
    Unmanaged,
    Empty,
    Busy,
    Locked(Box<Guard>),
}

impl Guard {
    fn check(&self, gate: &RootGate) -> Result<()> {
        self.root.validate_location()?;
        let current = gate
            .catalog
            .derived
            .as_ref()
            .and_then(|state| state.proxy.as_ref())
            .ok_or_else(|| invalid("proxy admission lock binding disappeared"))?;
        if gate.root.root_id != self.root.root_id
            || current.lock != self.lock
            || file_identity(&self.file)? != self.lock.file_id
            || file_identity(&open_control(
                &self.root.lock_path(self.lock.id),
                false,
                true,
                false,
            )?)? != self.lock.file_id
        {
            return Err(invalid("proxy admission lock identity changed"));
        }
        Ok(())
    }
}

fn state(gate: &RootGate) -> Result<&ProxyState> {
    gate.catalog
        .derived
        .as_ref()
        .and_then(|state| state.proxy.as_ref())
        .ok_or_else(|| invalid("proxy admission journal is missing"))
}

fn state_mut_proxy(gate: &mut RootGate) -> Result<&mut ProxyState> {
    state_mut(&mut gate.catalog)
        .proxy
        .as_mut()
        .ok_or_else(|| invalid("proxy admission journal is missing"))
}

fn snapshot_lock(root: &ManagedRoot) -> Result<Option<BoundLock>> {
    root.validate_location()?;
    // read_control retains one FD and enforces the catalog byte limit. An
    // atomic catalog replacement may yield an older sealed snapshot; the
    // immutable lock descriptor is revalidated after acquisition.
    let envelope: CatalogEnvelope = read_control(&root.control.join("catalog.json"))?;
    let catalog = envelope.catalog;
    if envelope.sha256 != hash_bytes(&serde_json::to_vec(&catalog)?)
        || !((catalog.format_version == FORMAT_VERSION && catalog.derived.is_none())
            || (catalog.format_version == CATALOG_FORMAT_VERSION && catalog.derived.is_some()))
        || catalog.root_id != root.root_id
        || catalog.canonical_root.as_os_str() != root.recorded_root.as_os_str()
        || catalog.directory_id != root.directory_id
        || catalog.control_id != root.control_id
    {
        return Err(invalid("proxy lock catalog snapshot is invalid"));
    }
    Ok(catalog
        .derived
        .and_then(|state| state.proxy)
        .map(|proxy| proxy.lock))
}

pub(crate) fn acquire(blob_root: &Path, create: bool) -> Result<Access> {
    let Some(root) = discover(blob_root)? else {
        return Ok(Access::Unmanaged);
    };
    let lock = if let Some(lock) = snapshot_lock(&root)? {
        lock
    } else if create {
        // Mutators may initialize, but double-check under the root gate. No
        // proxy lock is acquired while that gate is held.
        let mut gate = root.gate()?;
        if let Some(proxy) = gate
            .catalog
            .derived
            .as_ref()
            .and_then(|state| state.proxy.as_ref())
        {
            proxy.lock.clone()
        } else {
            let id = Uuid::now_v7();
            let file = open_control(&root.lock_path(id), false, true, true)?;
            file.sync_all()?;
            let lock = BoundLock {
                id,
                file_id: file_identity(&file)?,
            };
            state_mut(&mut gate.catalog).proxy = Some(ProxyState {
                lock: lock.clone(),
                bindings: BTreeMap::new(),
            });
            sync_directory(&root.control.join("locks"))?;
            gate.save()?;
            lock
        }
    } else {
        // This snapshot cannot authorize resolving bindings created later.
        return Ok(Access::Empty);
    };
    let Some(file) = try_live_lock(&root.lock_path(lock.id), &lock.file_id)? else {
        return Ok(Access::Busy);
    };
    let guard = Guard { root, lock, file };
    let gate = guard.root.gate()?;
    guard.check(&gate)?;
    drop(gate);
    Ok(Access::Locked(Box::new(guard)))
}

pub(crate) fn explicit_eviction_guard(blob_root: &Path) -> Result<Option<Guard>> {
    match acquire(blob_root, false)? {
        Access::Unmanaged | Access::Empty => Ok(None),
        Access::Busy => Err(invalid(
            "proxy admission is active; eviction was not started",
        )),
        Access::Locked(guard) => Ok(Some(*guard)),
    }
}

pub(crate) fn validate_store(store: &Store) -> Result<()> {
    if database_identity(&store.path)? != store.cas_database_id
        || load_store_id(&store.conn)? != store.cas_store_id
    {
        return Err(invalid("proxy binding belongs to a changed database"));
    }
    Ok(())
}

pub(crate) struct Admission {
    guard: Guard,
    key: RenderWindowCacheKey,
    store_id: Uuid,
    database_id: String,
    binding: Cell<Option<Uuid>>,
}

impl Admission {
    pub(crate) fn begin(store: &Store, key: &RenderWindowCacheKey) -> Result<Option<Self>> {
        validate_store(store)?;
        match acquire(&store.blob_root, true)? {
            Access::Unmanaged => Ok(None),
            Access::Empty => Err(invalid("proxy admission journal was not initialized")),
            Access::Busy => Err(invalid("another proxy admission is active")),
            Access::Locked(guard) => Ok(Some(Self {
                guard: *guard,
                key: key.clone(),
                store_id: store.cas_store_id,
                database_id: store.cas_database_id.clone(),
                binding: Cell::new(None),
            })),
        }
    }

    pub(super) fn check_store(&self, store: &Store) -> Result<()> {
        validate_store(store)?;
        if self.store_id != store.cas_store_id || self.database_id != store.cas_database_id {
            return Err(invalid("proxy admission was passed to another Store"));
        }
        Ok(())
    }

    pub(super) fn attach(
        &self,
        gate: &mut RootGate,
        reference_id: Uuid,
        reference: &Reference,
    ) -> Result<()> {
        self.guard.check(gate)?;
        let owner = crate::render_window_cache::scope(&self.key);
        if reference.record.scope_sha256 != hash_bytes(owner.as_bytes())
            || reference.record.kind != "render-window-proxy"
            || reference.record.fingerprint_sha256 != self.key.fingerprint_sha256
            || reference.proxy_origin.is_some_and(|id| id != reference_id)
        {
            return Err(invalid(
                "proxy publication differs from its exact cache key",
            ));
        }
        let existing = state(gate)?
            .bindings
            .iter()
            .find(|(_, binding)| {
                binding.store_id == self.store_id
                    && binding.database_id == self.database_id
                    && binding.key == self.key
                    && binding.reference_id == reference_id
            })
            .map(|(id, binding)| (*id, binding.clone()));
        let id = if let Some((id, binding)) = existing {
            if binding.manifest_id != reference.manifest.file_id {
                return Err(invalid("proxy winner has a different physical manifest"));
            }
            id
        } else {
            if state(gate)?.bindings.len() >= MAX_BINDINGS {
                return Err(invalid("proxy binding journal exceeds its object budget"));
            }
            let id = Uuid::now_v7();
            state_mut_proxy(gate)?.bindings.insert(
                id,
                Binding {
                    store_id: self.store_id,
                    database_id: self.database_id.clone(),
                    key: self.key.clone(),
                    reference_id,
                    manifest_id: reference.manifest.file_id.clone(),
                    registered: false,
                },
            );
            id
        };
        self.binding.set(Some(id));
        Ok(())
    }

    pub(crate) fn finish(&self, store: &Store) -> Result<bool> {
        self.check_store(store)?;
        let id = self
            .binding
            .get()
            .ok_or_else(|| invalid("proxy publication did not retain its binding"))?;
        match resolve(store, &self.guard, id, event_time(&self.guard.root.root)?)? {
            Resolution::Registered => Ok(true),
            Resolution::Removed { .. } => Ok(false),
            Resolution::Missing | Resolution::Foreign => Err(invalid(
                "proxy binding changed before registration was settled",
            )),
        }
    }
}

pub(super) fn bind_manifest(
    gate: &mut RootGate,
    reference_id: Uuid,
    reference: &Reference,
) -> Result<()> {
    let Some(proxy) = gate
        .catalog
        .derived
        .as_mut()
        .and_then(|state| state.proxy.as_mut())
    else {
        return Ok(());
    };
    for binding in proxy
        .bindings
        .values_mut()
        .filter(|binding| binding.reference_id == reference_id)
    {
        if binding.manifest_id.is_some() && binding.manifest_id != reference.manifest.file_id {
            return Err(invalid(
                "proxy manifest identity changed before publication",
            ));
        }
        binding.manifest_id = reference.manifest.file_id.clone();
    }
    Ok(())
}

pub(super) fn check_reference(
    catalog: &Catalog,
    reference_id: Uuid,
    reference: &Reference,
) -> Result<()> {
    if reference.proxy_origin.is_some_and(|id| id != reference_id) {
        return Err(invalid("proxy origin does not name this derived reference"));
    }
    let bindings = catalog
        .derived
        .as_ref()
        .and_then(|state| state.proxy.as_ref())
        .map(|proxy| &proxy.bindings);
    let mut count = 0;
    if let Some(bindings) = bindings {
        for binding in bindings
            .values()
            .filter(|binding| binding.reference_id == reference_id)
        {
            let owner = crate::render_window_cache::scope(&binding.key);
            if binding.manifest_id != reference.manifest.file_id
                || reference.record.scope_sha256 != hash_bytes(owner.as_bytes())
                || reference.record.kind != "render-window-proxy"
                || reference.record.fingerprint_sha256 != binding.key.fingerprint_sha256
            {
                return Err(invalid("proxy binding differs from its physical reference"));
            }
            count += 1;
        }
    }
    if reference.proxy_origin.is_some() && count == 0 && !matches!(reference.phase, Phase::Retiring)
    {
        return Err(invalid("proxy-only reference lost its durable binding"));
    }
    Ok(())
}

pub(super) fn forget_reference(gate: &mut RootGate, reference_id: Uuid) {
    if let Some(proxy) = gate
        .catalog
        .derived
        .as_mut()
        .and_then(|state| state.proxy.as_mut())
    {
        proxy
            .bindings
            .retain(|_, binding| binding.reference_id != reference_id);
    }
}

pub(in crate::cas_retention) fn check_access(access: &Access, gate: &RootGate) -> Result<()> {
    match access {
        Access::Locked(guard) => guard.check(gate),
        Access::Empty
            if gate
                .catalog
                .derived
                .as_ref()
                .is_none_or(|s| s.proxy.is_none()) =>
        {
            Ok(())
        }
        Access::Unmanaged => Err(invalid(
            "managed catalog appeared after an unmanaged observation",
        )),
        Access::Empty => Err(invalid(
            "proxy admission journal appeared after the empty snapshot",
        )),
        Access::Busy => Err(invalid("proxy admission is active")),
    }
}

pub(super) fn check_eviction(guard: Option<&Guard>, gate: &RootGate) -> Result<()> {
    if let Some(guard) = guard {
        return guard.check(gate);
    }
    if gate
        .catalog
        .derived
        .as_ref()
        .is_some_and(|s| s.proxy.is_some())
    {
        return Err(invalid("proxy journal appeared before unguarded eviction"));
    }
    Ok(())
}

enum Resolution {
    Registered,
    Removed {
        digest: Option<String>,
        manifest_removed: bool,
    },
    Missing,
    Foreign,
}

fn remove_binding(
    gate: &mut RootGate,
    id: Uuid,
    snapshot: &Binding,
    now: u64,
) -> Result<Resolution> {
    if state(gate)?.bindings.get(&id) != Some(snapshot) {
        return Err(invalid("proxy binding changed during reconciliation"));
    }
    let mut reference = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|s| s.references.get(&snapshot.reference_id))
        .cloned()
        .ok_or_else(|| invalid("proxy binding lost its reference"))?;
    check_reference(&gate.catalog, snapshot.reference_id, &reference)?;
    validate_reference(&gate.root, &reference)?;
    let others = state(gate)?.bindings.iter().any(|(other_id, binding)| {
        *other_id != id && binding.reference_id == snapshot.reference_id
    });
    if reference.proxy_origin.is_none() || others {
        state_mut_proxy(gate)?.bindings.remove(&id);
        gate.save()?;
        return Ok(Resolution::Removed {
            digest: None,
            manifest_removed: false,
        });
    }
    let existed = retirement_manifest(&reference)?.is_some();
    let digest = reference.record.blob_sha256.clone();
    reference.phase = Phase::Retiring;
    // A crash cannot leave a proxy-only Active reference with no binding.
    // The journal owns retirement before any physical manifest deletion.
    state_mut_proxy(gate)?.bindings.remove(&id);
    state_mut(&mut gate.catalog)
        .references
        .insert(snapshot.reference_id, reference);
    gate.save()?;
    checkpoint(&gate.root.root, "proxy_after_binding_retirement");
    reconcile_reference(gate, snapshot.reference_id, now)?;
    Ok(Resolution::Removed {
        digest: Some(digest),
        manifest_removed: existed,
    })
}

fn resolve(store: &Store, guard: &Guard, id: Uuid, now: u64) -> Result<Resolution> {
    let snapshot = {
        let gate = guard.root.gate()?;
        guard.check(&gate)?;
        let Some(binding) = state(&gate)?.bindings.get(&id).cloned() else {
            return Ok(Resolution::Missing);
        };
        if binding.store_id != store.cas_store_id || binding.database_id != store.cas_database_id {
            return Ok(Resolution::Foreign);
        }
        let reference = gate
            .catalog
            .derived
            .as_ref()
            .and_then(|s| s.references.get(&binding.reference_id))
            .ok_or_else(|| invalid("proxy binding lost its reference"))?;
        check_reference(&gate.catalog, binding.reference_id, reference)?;
        binding
    };
    // The root gate above has been dropped. The live proxy lock excludes
    // publication/registration by another protocol writer, not project edits.
    // A concurrently revoked SQL row is conservatively retained until a
    // later observation or its existing eviction tombstone is drained.
    validate_store(store)?;
    let key = &snapshot.key;
    let present: Option<i64> = store
        .conn
        .query_row(
            "SELECT 1 FROM render_window_cache_entries
         WHERE project_id=?1 AND generation=?2 AND revision=?3
           AND deliverable_id=?4 AND source_start=?5 AND source_end=?6
           AND fingerprint_sha256=?7",
            params![
                key.project_id.to_string(),
                key.expected.generation.to_string(),
                key.expected.revision as i64,
                key.deliverable_id.to_string(),
                key.source_window.start as i64,
                key.source_window.end as i64,
                key.fingerprint_sha256,
            ],
            |row| row.get(0),
        )
        .optional()?;
    validate_store(store)?;
    let mut gate = guard.root.gate()?;
    guard.check(&gate)?;
    if state(&gate)?.bindings.get(&id) != Some(&snapshot) {
        return Err(invalid("proxy binding changed after SQL observation"));
    }
    let reference = gate
        .catalog
        .derived
        .as_ref()
        .and_then(|s| s.references.get(&snapshot.reference_id))
        .ok_or_else(|| invalid("proxy reference disappeared after SQL observation"))?;
    check_reference(&gate.catalog, snapshot.reference_id, reference)?;
    if present.is_some() {
        if !snapshot.registered {
            state_mut_proxy(&mut gate)?
                .bindings
                .get_mut(&id)
                .ok_or_else(|| invalid("proxy binding disappeared under gate"))?
                .registered = true;
            gate.save()?;
        }
        Ok(Resolution::Registered)
    } else {
        // Absence is only cache-registration state. It is not NotApplied for
        // an import or operation, and no mutation is retried here.
        remove_binding(&mut gate, id, &snapshot, now)
    }
}

pub(in crate::cas_retention) fn candidates(
    catalog: &Catalog,
    store: Option<&Store>,
) -> BTreeSet<RecoveryPosition> {
    catalog
        .derived
        .as_ref()
        .and_then(|s| s.proxy.as_ref())
        .map(|proxy| {
            proxy
                .bindings
                .iter()
                .filter(|(_, binding)| {
                    store.is_none_or(|store| {
                        binding.store_id == store.cas_store_id
                            && binding.database_id == store.cas_database_id
                    })
                })
                .map(|(id, _)| RecoveryPosition::ProxyBinding(*id))
                .collect()
        })
        .unwrap_or_default()
}

pub(in crate::cas_retention) fn recover_candidate(
    store: &Store,
    access: &Access,
    id: Uuid,
    now: u64,
    report: &mut CasRecoveryReport,
) -> Result<Option<RecoveryPosition>> {
    let Access::Locked(guard) = access else {
        report.unresolved += 1;
        return Ok(None);
    };
    match resolve(store, guard, id, now) {
        Ok(Resolution::Registered) => report.retained_by_pins += 1,
        Ok(Resolution::Removed { digest, .. }) => {
            return Ok(digest.map(RecoveryPosition::DerivedPublication));
        }
        Ok(Resolution::Missing) => {}
        Ok(Resolution::Foreign) | Err(_) => report.unresolved += 1,
    }
    Ok(None)
}

pub(crate) fn deferred_report() -> CasRecoveryReport {
    CasRecoveryReport {
        root_managed: true,
        unresolved: 1,
        truncated: true,
        ..Default::default()
    }
}

pub(crate) fn maintain_store(
    store: &Store,
    policy: CasRetentionPolicy,
) -> Result<CasRecoveryReport> {
    validate_store(store)?;
    super::maintain_inner(&store.blob_root, policy, Some(store))
}

// Called with the same live lock held through the caller's SQL deletion.
// Only this Store's durable bindings are revoked. Generic manifests and other
// databases' bindings remain authoritative; global eviction is a separate API.
pub(crate) fn withdraw(
    store: &Store,
    access: &Access,
    owner: &str,
    fingerprint: &str,
) -> Result<Option<bool>> {
    validate_store(store)?;
    let guard = match access {
        Access::Unmanaged => return Ok(None),
        Access::Busy => {
            return Err(invalid(
                "proxy admission is active; withdrawal was not started",
            ));
        }
        Access::Empty => {
            let Some(root) = discover(&store.blob_root)? else {
                return Ok(None);
            };
            let gate = root.gate()?;
            check_access(access, &gate)?;
            // Legacy entries have no durable Store ownership. Do not infer it
            // from an absent row or revoke a potentially shared generic key.
            return Ok(Some(false));
        }
        Access::Locked(guard) => guard,
    };
    let mut gate = guard.root.gate()?;
    guard.check(&gate)?;
    let bindings = state(&gate)?
        .bindings
        .iter()
        .filter(|(_, b)| {
            b.store_id == store.cas_store_id
                && b.database_id == store.cas_database_id
                && crate::render_window_cache::scope(&b.key) == owner
                && b.key.fingerprint_sha256 == fingerprint
        })
        .map(|(id, binding)| (*id, binding.clone()))
        .collect::<Vec<_>>();
    let mut removed = false;
    for (id, binding) in bindings {
        if let Resolution::Removed {
            manifest_removed, ..
        } = remove_binding(&mut gate, id, &binding, event_time(&guard.root.root)?)?
        {
            removed |= manifest_removed;
        }
    }
    Ok(Some(removed))
}
