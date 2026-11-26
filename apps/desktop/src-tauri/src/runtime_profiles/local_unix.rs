use super::*;
use crate::{
    effect_grants::{EffectGrantRegistry, EffectKind, EffectScope},
    runtime_profile_bundle::{self as bundle, FileRole, Profile, ValidatedBundle},
    runtime_profile_files::{self as files, Directory, Identity, Snapshot},
};
use motionwright_service::{ProductionReceipt, ProductionReceiptInput, StudioService};
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
const COMMAND: &str = "motionwright.runtime-profile.install";
const INTENT_SCHEMA: &str = "motionwright.runtime-profile-intent/1";
const UNCONFIRMED: &str = "The original runtime operation could not be confirmed. Read its original result; do not repeat installation or rollback.";
const PLAN_TTL: Duration = Duration::from_secs(900);
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    schema: String,
    scope: Scope,
    install_id: Uuid,
    profile: Profile,
    version: String,
    platform: String,
    source_pin: String,
    manifest_sha256: String,
    total_bytes: u64,
    file_count: usize,
    entry: String,
    lock: String,
    lock_sha256: String,
    base: Identity,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    intent: Intent,
    directory: Option<Identity>,
    operation_revision: u64,
}
struct Plan {
    intent: Intent,
    source: Directory,
    manifest: Vec<u8>,
    bundle: ValidatedBundle,
    snapshot: Snapshot,
    expires: Instant,
    required_free: u64,
}
#[derive(Default)]
struct State {
    plans: BTreeMap<Uuid, Plan>,
}
#[derive(Clone)]
pub struct RuntimeProfiles {
    root: Result<Directory, String>,
    state: Arc<Mutex<State>>,
}
fn check_scope(service: &StudioService, scope: Scope) -> Result<(), String> {
    let project = service.project(scope.project_id).map_err(|_| UNCONFIRMED)?;
    if project.generation != scope.generation || project.revision != scope.revision {
        return Err(
            "Project changed during runtime setup. Refresh before a new explicit operation.".into(),
        );
    }
    Ok(())
}
fn digest(intent: &Intent) -> Result<String, String> {
    serde_json::to_vec(intent)
        .map(|bytes| files::sha(&bytes))
        .map_err(|_| UNCONFIRMED.into())
}
fn subject(intent: &Intent, rollback: bool) -> Result<String, String> {
    Ok(format!(
        "runtime-profile-{}|{}|{}",
        if rollback { "rollback" } else { "install" },
        intent.install_id,
        digest(intent)?
    ))
}
fn request_id(id: Uuid) -> String {
    format!("runtime-install-{id}")
}
fn append(service: &StudioService, record: &Record, stage: &str) -> Result<(), String> {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: record.intent.scope.project_id,
            generation: record.intent.scope.generation,
            revision: record.intent.scope.revision,
            request_id: request_id(record.intent.install_id),
            request_sha256: digest(&record.intent)?,
            command: COMMAND.into(),
            stage: stage.into(),
            payload: serde_json::to_value(record).map_err(|_| UNCONFIRMED)?,
        })
        .map(|_| ())
        .map_err(|_| UNCONFIRMED.into())
}
fn prior_effect(service: &StudioService, scope: Scope, id: Uuid, rollback: bool) -> bool {
    match service.latest_production_receipt(scope.project_id, &request_id(id)) {
        Ok(None) => false,
        Ok(Some(receipt)) => {
            !rollback
                || receipt.command != COMMAND
                || matches!(
                    receipt.stage.as_str(),
                    "rollback_dispatching" | "rollback_unknown" | "rolled_back"
                )
        }
        Err(_) => true,
    }
}
impl RuntimeProfiles {
    pub fn supported(&self) -> bool {
        true
    }
    pub fn new(data_dir: &Path) -> Self {
        // Optional runtime storage failure must not stop opening the project Store.
        // Preserve the failure for explicit runtime actions; never reconstruct it.
        let root = (|| -> Result<Directory, String> {
            if data_dir.to_str().is_none() {
                return Err("Runtime profile destination is not a supported local path.".into());
            }
            Directory::open(data_dir)?.create_child("runtime-profiles-v1", true)
        })();
        Self {
            root,
            state: Arc::new(Mutex::new(State::default())),
        }
    }
    fn root(&self) -> Result<&Directory, String> {
        self.root.as_ref().map_err(|message| format!("Offline runtime storage is unavailable. {message} Local project editing remains available."))
    }
    fn decode(&self, receipt: &ProductionReceipt, pin: &str) -> Result<Record, String> {
        let record: Record =
            serde_json::from_value(receipt.payload.clone()).map_err(|_| UNCONFIRMED)?;
        let intent = &record.intent;
        if intent.schema != INTENT_SCHEMA
            || intent.install_id.is_nil()
            || intent.base != self.root()?.identity()
            || intent.scope.project_id != receipt.project_id
            || intent.scope.generation != receipt.generation
            || intent.scope.revision != receipt.revision
            || receipt.command != COMMAND
            || receipt.request_id != request_id(intent.install_id)
            || receipt.request_sha256 != digest(intent)?
            || intent.source_pin != pin
            || intent.platform != crate::runtime_profile_platform::PLATFORM
            || intent.version != intent.profile.version()
            || (intent.profile == Profile::Hyperframes
                && !crate::runtime_profile_platform::SUPPORTS_HYPERFRAMES)
            || !bundle::is_sha256(&intent.manifest_sha256)
            || !bundle::is_sha256(&intent.lock_sha256)
            || intent.total_bytes == 0
            || intent.total_bytes > bundle::MAX_TOTAL_BYTES + bundle::MAX_MANIFEST_BYTES
            || intent.file_count == 0
            || intent.file_count > bundle::MAX_FILES + 1
            || record.operation_revision < intent.scope.revision
            || !matches!(
                receipt.stage.as_str(),
                "dispatching"
                    | "copying"
                    | "completed"
                    | "outcome_unknown"
                    | "rollback_dispatching"
                    | "rollback_unknown"
                    | "rolled_back"
            )
        {
            return Err(UNCONFIRMED.into());
        }
        let (entry, lock) = match intent.profile {
            Profile::MotionCanvas => ("runtime/render.mjs", "runtime/package-lock.json"),
            Profile::Hyperframes => (
                "runtime/semwright-hyperframes-runner.mjs",
                "runtime/runtime-lock.json",
            ),
        };
        if intent.entry != entry
            || intent.lock != lock
            || (receipt.stage != "dispatching" && record.directory.is_none())
        {
            return Err(UNCONFIRMED.into());
        }
        Ok(record)
    }
    fn history(
        &self,
        service: &StudioService,
        scope: Scope,
        pin: &str,
    ) -> Result<BTreeMap<Uuid, (Record, String)>, String> {
        let watermark = service
            .production_receipt_watermark(scope.project_id)
            .map_err(|_| UNCONFIRMED)?;
        if watermark.count > 20_000 {
            return Err("Runtime history exceeds its bounded receipt inspection budget.".into());
        }
        let mut before = None;
        let mut count = 0;
        let mut originals: BTreeMap<Uuid, (Record, String)> = BTreeMap::new();
        while count < watermark.count {
            let page = service
                .production_receipts_snapshot_page(scope.project_id, &watermark, before, 256)
                .map_err(|_| UNCONFIRMED)?;
            if page.is_empty() {
                return Err(UNCONFIRMED.into());
            }
            count += page.len() as u64;
            before = page.last().map(|row| row.id);
            for receipt in page {
                if receipt.command != COMMAND || receipt.generation != scope.generation {
                    continue;
                }
                let record = self.decode(&receipt, pin)?;
                if record.operation_revision > scope.revision {
                    return Err(UNCONFIRMED.into());
                }
                if let Some((latest, _)) = originals.get(&record.intent.install_id) {
                    if latest.intent != record.intent
                        || (record.directory.is_some() && latest.directory != record.directory)
                    {
                        return Err(UNCONFIRMED.into());
                    }
                } else {
                    if originals.len() >= 64 {
                        return Err("Runtime history contains more than 64 original installations for this project.".into());
                    }
                    originals.insert(record.intent.install_id, (record, receipt.stage));
                }
            }
        }
        Ok(originals)
    }
    fn view(
        &self,
        scope: Scope,
        record: &Record,
        state: &'static str,
        verified: bool,
        rollback: bool,
    ) -> Result<InstallView, String> {
        let i = &record.intent;
        let destination = self.root()?.path().join(i.install_id.to_string());
        let disabled = self
            .root()?
            .path()
            .join(format!("disabled-{}", i.install_id));
        let selected = if state == "rolled_back" {
            &disabled
        } else {
            &destination
        };
        Ok(InstallView {
            schema: "motionwright.runtime-profile-installation/1",
            scope,
            install_id: i.install_id,
            profile: i.profile.key().into(),
            label: i.profile.name().into(),
            version: i.version.clone(),
            platform: i.platform.clone(),
            manifest_sha256: i.manifest_sha256.clone(),
            total_bytes: i.total_bytes,
            file_count: i.file_count,
            destination: destination.display().to_string(),
            disabled_destination: disabled.display().to_string(),
            node_path: selected.join("tools/node").display().to_string(),
            entry_path: selected.join(&i.entry).display().to_string(),
            lock_path: selected.join(&i.lock).display().to_string(),
            lock_sha256: i.lock_sha256.clone(),
            state,
            content_verified: verified,
            rollback_available: rollback,
            rollback_subject: subject(i, true)?,
            execution_performed: false,
            automatic_retry: false,
        })
    }
    fn observe(
        &self,
        scope: Scope,
        record: &Record,
        stage: &str,
        pin: &str,
        verify: bool,
    ) -> Result<InstallView, String> {
        self.root()?.validate()?;
        let Some(identity) = record.directory else {
            return self.view(scope, record, "outcome_unknown", false, false);
        };
        let active = self
            .root()?
            .maybe_child(&record.intent.install_id.to_string())?;
        let disabled = self
            .root()?
            .maybe_child(&format!("disabled-{}", record.intent.install_id))?;
        if active.is_some() && disabled.is_some() {
            return Err(UNCONFIRMED.into());
        }
        if let Some(disabled) = disabled {
            if disabled.identity() != identity
                || !matches!(
                    stage,
                    "rollback_dispatching" | "rollback_unknown" | "rolled_back"
                )
            {
                return Err(UNCONFIRMED.into());
            }
            return self.view(scope, record, "rolled_back", false, false);
        }
        let Some(active) = active else {
            return self.view(scope, record, "missing_files", false, false);
        };
        if active.identity() != identity || stage == "rolled_back" {
            return Err(UNCONFIRMED.into());
        }
        if matches!(stage, "rollback_dispatching" | "rollback_unknown") {
            return self.view(scope, record, "outcome_unknown", false, false);
        }
        if !verify {
            return self.view(
                scope,
                record,
                if stage == "completed" {
                    "copy_completed"
                } else {
                    "outcome_unknown"
                },
                false,
                true,
            );
        }
        let verified = (|| {
            let bytes = active.read_small(bundle::MANIFEST, bundle::MAX_MANIFEST_BYTES)?;
            if files::sha(&bytes) != record.intent.manifest_sha256 {
                return Err(UNCONFIRMED.to_string());
            }
            let bundle = bundle::parse(&bytes, pin)?;
            if bundle.total_bytes + bytes.len() as u64 != record.intent.total_bytes
                || bundle.manifest.files.len() + 1 != record.intent.file_count
                || bundle.manifest.profile != record.intent.profile
                || bundle.role(FileRole::RuntimeLock).sha256 != record.intent.lock_sha256
            {
                return Err(UNCONFIRMED.to_string());
            }
            files::inspect(&active, &bundle, &record.intent.manifest_sha256)?;
            Ok::<(), String>(())
        })()
        .is_ok();
        self.view(
            scope,
            record,
            if verified {
                "installed"
            } else if stage == "completed" {
                "files_unverified"
            } else {
                "partial_copy"
            },
            verified,
            true,
        )
    }
    pub fn inventory(
        &self,
        service: &StudioService,
        scope: Scope,
        pin: &str,
    ) -> Result<Inventory, String> {
        check_scope(service, scope)?;
        let _state = self.state.lock().map_err(|_| UNCONFIRMED)?;
        let _exclusive = self.root()?.exclusive()?;
        let mut profiles = Vec::new();
        for (record, stage) in self.history(service, scope, pin)?.values() {
            profiles.push(self.observe(scope, record, stage, pin, false)?);
        }
        check_scope(service, scope)?;
        Ok(Inventory {
            schema: "motionwright.runtime-profile-inventory/1",
            scope,
            supported: true,
            platform: crate::runtime_profile_platform::PLATFORM,
            profiles,
            alternatives: ALTERNATIVES,
        })
    }
    pub fn inspect(
        &self,
        service: &StudioService,
        scope: Scope,
        selected: &Path,
        pin: &str,
    ) -> Result<PlanView, String> {
        check_scope(service, scope)?;
        let mut state = self.state.lock().map_err(|_| UNCONFIRMED)?;
        let _exclusive = self.root()?.exclusive()?;
        state.plans.retain(|_, plan| plan.expires > Instant::now());
        if state.plans.len() >= 4 {
            return Err("Four runtime packages are already awaiting a decision; let unused inspections expire.".into());
        }
        let source = Directory::open(selected)?;
        let manifest = source.read_small(bundle::MANIFEST, bundle::MAX_MANIFEST_BYTES)?;
        let bundle = bundle::parse(&manifest, pin)?;
        for (record, stage) in self.history(service, scope, pin)?.values() {
            if record.intent.profile == bundle.manifest.profile
                && self.observe(scope, record, stage, pin, false)?.state != "rolled_back"
            {
                return Err("This project already has an original operation for that runtime profile. Read its result and explicitly roll it back before another installation.".into());
            }
        }
        let manifest_sha256 = files::sha(&manifest);
        let snapshot = files::inspect(&source, &bundle, &manifest_sha256)?;
        let total_bytes = bundle.total_bytes + manifest.len() as u64;
        let required_free = total_bytes
            + 4096 * (bundle.directories.len() + bundle.manifest.files.len()) as u64
            + 16 * 1024 * 1024;
        let available = self.root()?.free_bytes()?;
        if available < required_free {
            return Err("The fixed runtime destination lacks space for the package and its bounded filesystem overhead.".into());
        }
        let install_id = Uuid::now_v7();
        let intent = Intent {
            schema: INTENT_SCHEMA.into(),
            scope,
            install_id,
            profile: bundle.manifest.profile,
            version: bundle.manifest.version.clone(),
            platform: bundle.manifest.platform.clone(),
            source_pin: pin.into(),
            manifest_sha256: manifest_sha256.clone(),
            total_bytes,
            file_count: bundle.manifest.files.len() + 1,
            entry: bundle.role(FileRole::RuntimeEntry).path.clone(),
            lock: bundle.role(FileRole::RuntimeLock).path.clone(),
            lock_sha256: bundle.role(FileRole::RuntimeLock).sha256.clone(),
            base: self.root()?.identity(),
        };
        let view = self.view(
            scope,
            &Record {
                intent: intent.clone(),
                directory: None,
                operation_revision: scope.revision,
            },
            "outcome_unknown",
            false,
            false,
        )?;
        let result = PlanView {
            schema: "motionwright.runtime-profile-plan/1",
            scope,
            plan_id: install_id,
            profile: view.profile,
            label: view.label,
            version: view.version,
            platform: view.platform,
            manifest_sha256,
            total_bytes,
            required_free_bytes: required_free,
            available_bytes: available,
            file_count: view.file_count,
            destination: view.destination,
            node_path: view.node_path,
            entry_path: view.entry_path,
            lock_path: view.lock_path,
            lock_sha256: view.lock_sha256,
            declared_node_version: bundle.manifest.node_version.clone(),
            effect_subject: subject(&intent, false)?,
            expires_in_seconds: PLAN_TTL.as_secs(),
            execution_performed: false,
        };
        check_scope(service, scope)?;
        state.plans.insert(
            install_id,
            Plan {
                intent,
                source,
                manifest,
                bundle,
                snapshot,
                expires: Instant::now() + PLAN_TTL,
                required_free,
            },
        );
        Ok(result)
    }
    pub fn install(
        &self,
        service: &StudioService,
        grants: &EffectGrantRegistry,
        request: InstallRequest,
        pin: &str,
    ) -> Result<InstallView, RuntimeFailure> {
        let mut failure =
            RuntimeFailure::install(&request, "The original runtime reply was not confirmed.");
        let mut admitted = prior_effect(service, request.scope, request.plan_id, false);
        let result = (|| -> Result<InstallView, String> {
            check_scope(service, request.scope)?;
            let mut state = self.state.lock().map_err(|_| UNCONFIRMED)?;
            let _exclusive = self.root()?.exclusive()?;
            admitted = prior_effect(service, request.scope, request.plan_id, false);
            let plan = state.plans.get(&request.plan_id).ok_or(
            "The inspected runtime package is unavailable. Select it again before a new operation.",
        )?;
            if plan.expires <= Instant::now()
                || plan.intent.scope != request.scope
                || plan.intent.manifest_sha256 != request.manifest_sha256
                || plan.intent.source_pin != pin
            {
                return Err("The inspected runtime package changed or expired.".into());
            }
            for (record, stage) in self.history(service, request.scope, pin)?.values() {
                if record.intent.profile == plan.intent.profile
                    && self
                        .observe(request.scope, record, stage, pin, false)?
                        .state
                        != "rolled_back"
                {
                    return Err(UNCONFIRMED.into());
                }
            }
            plan.source.validate()?;
            if self.root()?.free_bytes()? < plan.required_free {
                return Err("Runtime destination no longer has the reviewed free space.".into());
            }
            check_scope(service, request.scope)?;
            grants.consume(
                request.effect_grant,
                EffectKind::InstallRuntime,
                EffectScope::project(
                    request.scope.project_id,
                    request.scope.generation,
                    request.scope.revision,
                ),
                &subject(&plan.intent, false)?,
            )?;
            let plan = state.plans.remove(&request.plan_id).ok_or(UNCONFIRMED)?;
            let mut record = Record {
                intent: plan.intent,
                directory: None,
                operation_revision: request.scope.revision,
            };
            // The original SQLite intent commits before any package directory or file is written.
            admitted = true;
            append(service, &record, "dispatching")?;
            let directory = self
                .root()?
                .create_child(&record.intent.install_id.to_string(), false)?;
            record.directory = Some(directory.identity());
            // Retain the actual destination inode before the first runtime byte is copied.
            append(service, &record, "copying")?;
            let copied = files::copy(
                &plan.source,
                &directory,
                &plan.bundle,
                &plan.manifest,
                &plan.snapshot,
            );
            if copied.is_err() {
                let _ = append(service, &record, "outcome_unknown");
                return Err("Runtime copy stopped. Retain the original operation and inspect it; a partial copy can be rolled back without deleting its bytes.".into());
            }
            append(service, &record, "completed")?;
            self.view(request.scope, &record, "installed", true, true)
        })();
        result.map_err(|message| {
            failure.message = message;
            failure.certainty = if admitted {
                "outcome_unknown"
            } else {
                "not_started"
            };
            failure
        })
    }
    pub fn read(
        &self,
        service: &StudioService,
        request: ReadRequest,
        pin: &str,
    ) -> Result<InstallView, String> {
        check_scope(service, request.scope)?;
        let _state = self.state.lock().map_err(|_| UNCONFIRMED)?;
        let _exclusive = self.root()?.exclusive()?;
        let history = self.history(service, request.scope, pin)?;
        let (record, stage) = history
            .get(&request.install_id)
            .ok_or("No original runtime operation exists for this project and generation.")?;
        let result = self.observe(request.scope, record, stage, pin, true)?;
        check_scope(service, request.scope)?;
        Ok(result)
    }
    pub fn rollback(
        &self,
        service: &StudioService,
        grants: &EffectGrantRegistry,
        request: RollbackRequest,
        pin: &str,
    ) -> Result<InstallView, RuntimeFailure> {
        let mut failure =
            RuntimeFailure::rollback(&request, "The original runtime reply was not confirmed.");
        let mut admitted = prior_effect(service, request.scope, request.install_id, true);
        let result = (|| -> Result<InstallView, String> {
            check_scope(service, request.scope)?;
            let _state = self.state.lock().map_err(|_| UNCONFIRMED)?;
            let _exclusive = self.root()?.exclusive()?;
            admitted = prior_effect(service, request.scope, request.install_id, true);
            let history = self.history(service, request.scope, pin)?;
            let (original, stage) = history
                .get(&request.install_id)
                .ok_or("No original runtime installation exists for this project.")?;
            let view = self.observe(request.scope, original, stage, pin, false)?;
            if !view.rollback_available
                || original.intent.manifest_sha256 != request.manifest_sha256
            {
                return Err("This original runtime operation cannot be rolled back again.".into());
            }
            check_scope(service, request.scope)?;
            grants.consume(
                request.effect_grant,
                EffectKind::InstallRuntime,
                EffectScope::project(
                    request.scope.project_id,
                    request.scope.generation,
                    request.scope.revision,
                ),
                &subject(&original.intent, true)?,
            )?;
            let record = Record {
                operation_revision: request.scope.revision,
                ..original.clone()
            };
            admitted = true;
            append(service, &record, "rollback_dispatching")?;
            if self
                .root()?
                .disable(
                    &request.install_id.to_string(),
                    record.directory.ok_or(UNCONFIRMED)?,
                )
                .is_err()
            {
                let _ = append(service, &record, "rollback_unknown");
                return Err(UNCONFIRMED.into());
            }
            append(service, &record, "rolled_back")?;
            self.view(request.scope, &record, "rolled_back", false, false)
        })();
        result.map_err(|message| {
            failure.message = message;
            failure.certainty = if admitted {
                "outcome_unknown"
            } else {
                "not_started"
            };
            failure
        })
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
