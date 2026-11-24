//! Closed Desktop preparation and admission for the existing Blender product cycle.
//! Plans retain owner-selected connections in memory. They are not Native grants.
//! Source lineage, replay protection and native receipts stay in ProductionCoordinator.
use crate::{
    blender_product::{BlenderProductReadRequest, read_source},
    effect_grants::{EffectGrantRegistry, EffectKind, EffectScope},
};
use motionwright_domain::Project;
use motionwright_native::production::{
    BlenderProductCycleJournal, BlenderProductCycleRequest, BlenderProductSourceSnapshot,
    ProductionConnection, ProductionCoordinator, ProductionReceiptCustody,
};
use motionwright_service::StudioService;
use semwright_native_sdk::Error as NativeError;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use uuid::Uuid;

const TTL: Duration = Duration::from_secs(600);
const MAX_PLANS: usize = 16;
const LOCK_NAME: &str = ".motionwright-blender-cycle.lock";
const UNAVAILABLE: &str = "The original Blender preparation is unavailable. Read its retained operation before a separate explicit preparation.";

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareRequest {
    pub source: BlenderProductReadRequest,
    pub source_sha256: String,
    pub request_id: String,
    pub annotation_body: String,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartRequest {
    pub source: BlenderProductReadRequest,
    pub source_sha256: String,
    pub request_id: String,
    pub plan_id: Uuid,
    pub effect_grant: Uuid,
}
#[derive(Debug, Serialize)]
pub struct PlanView {
    pub schema: &'static str,
    pub plan_id: Uuid,
    pub source: BlenderProductReadRequest,
    pub source_sha256: String,
    pub request_id: String,
    pub annotation_before: String,
    pub annotation_after: String,
    pub effect_subject: String,
    pub expires_in_seconds: u64,
    pub phases: [&'static str; 3],
    pub native_dispatch_performed: bool,
    pub execution_grant_inherited: bool,
}
#[derive(Debug, Serialize)]
pub struct CycleResponse {
    pub schema: &'static str,
    pub source: BlenderProductReadRequest,
    pub original_request_id: String,
    /// Preserve Native certainty, including UNKNOWN; do not infer success from files.
    pub outcome: Result<Value, NativeError>,
    pub journal: Value,
    pub automatic_retry: bool,
}
#[derive(Clone)]
struct RootIdentity {
    path: PathBuf,
    handle: Arc<same_file::Handle>,
}
impl RootIdentity {
    fn capture(connection: &ProductionConnection) -> Result<Self, String> {
        // Full connection validation rejects linked/reparse ancestors and hashes
        // the pinned executable. Capture the actual directory identity afterward.
        connection
            .validate()
            .map_err(|_| "Blender connection validation failed.")?;
        let path = fs::canonicalize(&connection.output_root).map_err(|_| UNAVAILABLE)?;
        let handle = same_file::Handle::from_path(&path).map_err(|_| UNAVAILABLE)?;
        Ok(Self {
            path,
            handle: Arc::new(handle),
        })
    }
    fn check(&self) -> Result<(), String> {
        let current = same_file::Handle::from_path(&self.path).map_err(|_| UNAVAILABLE)?;
        if &current != self.handle.as_ref() {
            return Err("A prepared Blender output directory was replaced.".into());
        }
        Ok(())
    }
}
struct RootLock {
    root: RootIdentity,
    path: PathBuf,
    file: File,
}
impl RootLock {
    fn acquire(root: &RootIdentity, request_id: &str) -> Result<Self, String> {
        root.check()?;
        let path = root.path.join(LOCK_NAME);
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&path).map_err(
            |_| "A Blender output root is already reserved; inspect the original operation.",
        )?;
        let mut lease = Self {
            root: root.clone(),
            path,
            file,
        };
        // The exclusive file is an application staging exclusion, never a Host
        // permission. Its retained identity prevents removal of a replacement.
        lease
            .file
            .write_all(request_id.as_bytes())
            .map_err(|_| UNAVAILABLE)?;
        lease.file.sync_all().map_err(|_| UNAVAILABLE)?;
        root.check()?;
        Ok(lease)
    }
}
impl Drop for RootLock {
    fn drop(&mut self) {
        let same = self.root.check().is_ok()
            && self
                .file
                .try_clone()
                .ok()
                .and_then(|file| same_file::Handle::from_file(file).ok())
                .zip(same_file::Handle::from_path(&self.path).ok())
                .is_some_and(|(original, current)| original == current);
        if same {
            let _ = fs::remove_file(&self.path);
        }
    }
}
struct Plan {
    request: PrepareRequest,
    project: Project,
    connections: [ProductionConnection; 3],
    roots: [RootIdentity; 3],
    subject: String,
    expires: Instant,
}
#[derive(Clone, Default)]
pub struct CyclePlans(Arc<Mutex<BTreeMap<Uuid, Plan>>>);

pub struct AdmittedCycle {
    pub request: BlenderProductCycleRequest,
    pub source: BlenderProductReadRequest,
    pub coordinators: [ProductionCoordinator; 3],
    roots: [RootIdentity; 3],
    // Hold all staging exclusions until the existing cycle returns, including
    // source import/attachment and the later original-source reopen phases.
    _locks: Vec<RootLock>,
}
impl AdmittedCycle {
    pub fn check_roots(&self) -> Result<(), String> {
        for root in &self.roots {
            root.check()?;
        }
        Ok(())
    }
    pub fn response(
        &self,
        journal: &BlenderProductCycleJournal,
        outcome: Result<Value, NativeError>,
    ) -> CycleResponse {
        CycleResponse {
            schema: "motionwright.blender.desktop-product-cycle/1",
            source: self.source.clone(),
            original_request_id: self.request.request_id.clone(),
            outcome,
            journal: journal.summary(),
            automatic_retry: false,
        }
    }
}
fn cycle_request(request: &PrepareRequest, project: &Project) -> BlenderProductCycleRequest {
    BlenderProductCycleRequest {
        project_id: project.id,
        expected: project.stamp(),
        scene_id: request.source.scene_id,
        capsule_id: request.source.capsule_id,
        request_id: request.request_id.clone(),
        annotation_body: request.annotation_body.clone(),
    }
}
pub fn checked_source(
    service: &StudioService,
    request: &PrepareRequest,
) -> Result<BlenderProductSourceSnapshot, String> {
    let valid_id = request
        .request_id
        .strip_prefix("blender-ui-")
        .and_then(|value| Uuid::parse_str(value).ok())
        .is_some_and(|id| !id.is_nil() && format!("blender-ui-{id}") == request.request_id);
    if !valid_id
        || request.source_sha256.len() != 64
        || !request
            .source_sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("Blender source or original request identity is invalid.".into());
    }
    let snapshot = read_source(service, &request.source)
        .map_err(|_| "The current saved Blender source could not be verified.")?;
    if snapshot.source_kind != "authored_recipe"
        || snapshot.current_source.sha256 != request.source_sha256
        || snapshot.attempt.is_some()
    {
        return Err("This Blender source already has an attempt or changed. Read the original source lineage; it cannot be replayed.".into());
    }
    let (edited, steps) = motionwright_native::blender_product_scene::project_product_edit(
        &snapshot.effective_recipe,
        &motionwright_native::blender_product_scene::ProductEdit::Annotation(
            request.annotation_body.clone(),
        ),
    )
    .map_err(|_| "The annotation edit is outside the supported Blender source profile.")?;
    if edited.annotation == snapshot.effective_recipe.annotation || steps.len() != 1 {
        return Err("Choose one explicit annotation edit for this original Blender cycle.".into());
    }
    Ok(snapshot)
}
fn coordinators(
    service: &StudioService,
    custody: &ProductionReceiptCustody,
    connections: &[ProductionConnection; 3],
) -> Result<[ProductionCoordinator; 3], String> {
    let mut values = Vec::with_capacity(3);
    for connection in connections {
        values.push(
            ProductionCoordinator::with_receipt_custody(
                service.clone(),
                connection.clone(),
                custody,
            )
            .map_err(|_| "An owner-selected Blender connection was rejected.")?,
        );
    }
    values.try_into().map_err(|_| UNAVAILABLE.into())
}
fn subject(
    plan_id: Uuid,
    request: &PrepareRequest,
    connections: &[ProductionConnection; 3],
) -> Result<String, String> {
    let identities = connections
        .iter()
        .map(ProductionConnection::identity)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| UNAVAILABLE)?;
    let bytes = serde_json::to_vec(&json!({
        "schema": "motionwright.blender.desktop-cycle-intent/1",
        "plan_id": plan_id, "request": request, "connection_identities": identities,
    }))
    .map_err(|_| UNAVAILABLE)?;
    Ok(format!(
        "blender-product|{plan_id}|{:x}",
        Sha256::digest(bytes)
    ))
}
impl CyclePlans {
    pub fn prepare(
        &self,
        service: &StudioService,
        custody: &ProductionReceiptCustody,
        request: PrepareRequest,
        connections: [ProductionConnection; 3],
    ) -> Result<PlanView, String> {
        let snapshot = checked_source(service, &request)?;
        let coordinators = coordinators(service, custody, &connections)?;
        let native = cycle_request(&request, &snapshot.project);
        let digest = coordinators[0].preflight_blender_product_cycle(
            &coordinators[1], &coordinators[2], &native,
        ).map_err(|_| "Blender requires three fresh isolated owner contexts and an eligible saved source.")?;
        if digest != request.source_sha256 {
            return Err(UNAVAILABLE.into());
        }
        let roots = [
            RootIdentity::capture(&connections[0])?,
            RootIdentity::capture(&connections[1])?,
            RootIdentity::capture(&connections[2])?,
        ];
        for root in &roots {
            if root
                .path
                .join(LOCK_NAME)
                .try_exists()
                .map_err(|_| UNAVAILABLE)?
            {
                return Err(
                    "A Blender output root retains an earlier operation reservation.".into(),
                );
            }
        }
        crate::blender_product::ensure_read_current(service, &snapshot.project)
            .map_err(|_| "The project changed during Blender preparation.")?;
        let plan_id = Uuid::now_v7();
        let subject = subject(plan_id, &request, &connections)?;
        let view = PlanView {
            schema: "motionwright.blender.desktop-product-plan/1",
            plan_id,
            source: request.source.clone(),
            source_sha256: request.source_sha256.clone(),
            request_id: request.request_id.clone(),
            annotation_before: snapshot.effective_recipe.annotation,
            annotation_after: request.annotation_body.clone(),
            effect_subject: subject.clone(),
            expires_in_seconds: TTL.as_secs(),
            phases: ["create_original", "edit_original", "verify_edited"],
            native_dispatch_performed: false,
            execution_grant_inherited: false,
        };
        let now = Instant::now();
        let mut plans = self.0.lock().map_err(|_| UNAVAILABLE)?;
        plans.retain(|_, plan| plan.expires > now);
        if plans.len() >= MAX_PLANS {
            return Err("Close or let earlier Blender preparations expire.".into());
        }
        plans.insert(
            plan_id,
            Plan {
                request,
                project: snapshot.project,
                connections,
                roots,
                subject,
                expires: now + TTL,
            },
        );
        Ok(view)
    }
    pub fn admit(
        &self,
        service: &StudioService,
        custody: &ProductionReceiptCustody,
        grants: &EffectGrantRegistry,
        request: StartRequest,
    ) -> Result<AdmittedCycle, String> {
        // Remove once even on a mismatch. A copied plan id is never a replay key.
        let plan = self
            .0
            .lock()
            .map_err(|_| UNAVAILABLE)?
            .remove(&request.plan_id)
            .ok_or(UNAVAILABLE)?;
        if plan.expires <= Instant::now()
            || plan.request.source != request.source
            || plan.request.source_sha256 != request.source_sha256
            || plan.request.request_id != request.request_id
        {
            return Err(
                "The Blender start does not match its original current preparation.".into(),
            );
        }
        for root in &plan.roots {
            root.check()?;
        }
        let snapshot = checked_source(service, &plan.request)?;
        if snapshot.project != plan.project {
            return Err("The prepared Blender project changed.".into());
        }
        let native = cycle_request(&plan.request, &snapshot.project);
        let coordinators = coordinators(service, custody, &plan.connections)?;
        let digest = coordinators[0]
            .preflight_blender_product_cycle(&coordinators[1], &coordinators[2], &native)
            .map_err(|_| "The prepared Blender contexts or output roots are no longer eligible.")?;
        if digest != request.source_sha256 {
            return Err(UNAVAILABLE.into());
        }
        grants.consume(
            request.effect_grant,
            EffectKind::RenderLocal,
            EffectScope::project(
                request.source.project_id,
                request.source.generation,
                request.source.revision,
            ),
            &plan.subject,
        )?;
        let mut locks = Vec::with_capacity(3);
        for root in &plan.roots {
            locks.push(RootLock::acquire(root, &request.request_id)?);
        }
        crate::blender_product::ensure_read_current(service, &snapshot.project)
            .map_err(|_| "The project changed before the original Blender dispatch.")?;
        Ok(AdmittedCycle {
            request: native,
            source: plan.request.source,
            coordinators,
            roots: plan.roots,
            _locks: locks,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, Fidelity, FidelityReport, NativeCapsule, RendererKind};
    use motionwright_native::{
        blender_product_scene::{CameraCurve, ProductScene},
        production::BLENDER_PRODUCT_RECIPE_MEDIA_TYPE,
    };
    use tempfile::{TempDir, tempdir};
    struct Fixture {
        service: StudioService,
        project: Project,
        request: BlenderProductReadRequest,
        recipe: ProductScene,
        root: TempDir,
    }

    fn fixture() -> Fixture {
        let root = tempdir().unwrap();
        let service = StudioService::open(root.path().join("desktop-blender.sqlite3")).unwrap();
        let mut project = service
            .create_project("Desktop Blender reader fixture")
            .unwrap();
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "reader-fixture-scene",
                &Change::AddScene {
                    name: "Saved Blender source".into(),
                    objective: "Read real SQLite and CAS without native execution".into(),
                    duration_seconds: 40,
                },
            )
            .unwrap()
            .project;
        let scene_id = project.scenes[0].id;
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "reader-fixture-renderer",
                &Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::Blender,
                },
            )
            .unwrap()
            .project;
        let recipe = ProductScene::vessel("DesktopReader", CameraCurve::BezierAutoClamped);
        let source = root.path().join("recipe.json");
        fs::write(&source, serde_json::to_vec(&recipe).unwrap()).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "reader-fixture-asset",
                &source,
                "recipe.json".into(),
                BLENDER_PRODUCT_RECIPE_MEDIA_TYPE.into(),
            )
            .unwrap()
            .project;
        let asset = project.assets.last().unwrap().clone();
        let capsule_id = Uuid::now_v7();
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "reader-fixture-capsule",
                &Change::UpsertNativeCapsule {
                    capsule: NativeCapsule {
                        id: capsule_id,
                        scene_id,
                        source_asset_id: asset.id,
                        source_sha256: asset.content_sha256.unwrap(),
                        label: "Source reader fixture".into(),
                        fidelity: FidelityReport {
                            renderer: "Blender".into(),
                            renderer_version: "unobserved".into(),
                            visual: Fidelity::Unavailable,
                            temporal: Fidelity::Unavailable,
                            structural: Fidelity::Unavailable,
                            editable: Fidelity::Unavailable,
                            losses: vec!["Unit fixture; no Blender execution.".into()],
                            evidence_sha256: None,
                        },
                        editable_parameters: vec!["product.annotation.body".into()],
                        native_editor_hint: "Blender".into(),
                        property_fidelity: vec![],
                    },
                },
            )
            .unwrap()
            .project;
        let request = BlenderProductReadRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            capsule_id,
        };
        Fixture {
            root,
            service,
            project,
            request,
            recipe,
        }
    }

    fn intent(f: &Fixture) -> PrepareRequest {
        PrepareRequest {
            source: f.request.clone(),
            source_sha256: f.project.production_design.capsules[0]
                .source_sha256
                .clone(),
            request_id: format!("blender-ui-{}", Uuid::now_v7()),
            annotation_body: "EDITED\nSURFACE".into(),
        }
    }
    fn connections(f: &Fixture) -> [ProductionConnection; 3] {
        // Non-executable fixture bytes: these tests exercise local validation
        // and SQLite only. No Host, provider command or runtime probe is run.
        let owner = f.root.path().join("owner");
        fs::create_dir(&owner).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&owner, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let executable = owner.join("inert-version-fixture");
        let session_file = owner.join("session-fixture.json");
        let socket = owner.join("socket-fixture");
        for path in [&executable, &session_file, &socket] {
            fs::write(path, b"local-validation-fixture").unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
            }
        }
        std::array::from_fn(|index| {
            let output_root = owner.join(format!("phase-{index}"));
            fs::create_dir(&output_root).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&output_root, fs::Permissions::from_mode(0o700)).unwrap();
            }
            ProductionConnection {
                schema: "motionwright-semwright-connection/1".into(),
                executable: executable.clone(),
                executable_sha256: format!("{:x}", Sha256::digest(b"local-validation-fixture")),
                socket: socket.clone(),
                session_file: session_file.clone(),
                output_root,
                resource: f.project.resource_key(),
            }
        })
    }
    fn start(view: &PlanView, token: Uuid) -> StartRequest {
        StartRequest {
            source: view.source.clone(),
            source_sha256: view.source_sha256.clone(),
            request_id: view.request_id.clone(),
            plan_id: view.plan_id,
            effect_grant: token,
        }
    }
    fn grant(grants: &EffectGrantRegistry, view: &PlanView) -> Uuid {
        grants
            .issue(
                EffectKind::RenderLocal,
                EffectScope::project(
                    view.source.project_id,
                    view.source.generation,
                    view.source.revision,
                ),
                &view.effect_subject,
            )
            .unwrap()
            .token
    }
    #[test]
    fn saved_cas_preparation_and_admission_leave_the_original_project_and_native_receipts_untouched()
     {
        let f = fixture();
        let plans = CyclePlans::default();
        let custody = ProductionReceiptCustody::default();
        let grants = EffectGrantRegistry::default();
        let original = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let connections = connections(&f);
        let roots = connections.each_ref().map(|c| c.output_root.clone());
        let view = plans
            .prepare(&f.service, &custody, intent(&f), connections)
            .unwrap();
        assert_eq!(view.annotation_before, f.recipe.annotation);
        assert!(!view.native_dispatch_performed && !view.execution_grant_inherited);
        assert!(roots.iter().all(|root| !root.join(LOCK_NAME).exists()));
        let token = grant(&grants, &view);
        let admitted = plans
            .admit(&f.service, &custody, &grants, start(&view, token))
            .unwrap();
        assert!(roots.iter().all(|root| root.join(LOCK_NAME).exists()));
        assert_eq!(admitted.request.expected, f.project.stamp());
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(
            f.service
                .production_receipt_watermark(f.project.id)
                .unwrap(),
            original
        );
        assert!(
            plans
                .admit(&f.service, &custody, &grants, start(&view, token))
                .is_err()
        );
        assert!(
            grants
                .consume(
                    token,
                    EffectKind::RenderLocal,
                    EffectScope::project(f.project.id, f.project.generation, f.project.revision),
                    &view.effect_subject
                )
                .is_err()
        );
        drop(admitted);
        assert!(roots.iter().all(|root| !root.join(LOCK_NAME).exists()));
    }
    #[test]
    fn source_scope_digest_edit_and_foreign_or_shared_contexts_fail_before_native_work() {
        let f = fixture();
        let original = intent(&f);
        for variant in 0..6 {
            let mut request = original.clone();
            match variant {
                0 => request.source_sha256 = "0".repeat(64),
                1 => request.source.generation = Uuid::now_v7(),
                2 => request.source.revision += 1,
                3 => request.source.capsule_id = Uuid::now_v7(),
                4 => request.annotation_body = f.recipe.annotation.clone(),
                _ => request.request_id = "new-or-replayed".into(),
            }
            assert!(checked_source(&f.service, &request).is_err());
        }
        let mut values = connections(&f);
        let original_resource = values[1].resource.clone();
        values[1].resource = format!("project:{}", Uuid::now_v7());
        let plans = CyclePlans::default();
        let custody = ProductionReceiptCustody::default();
        assert!(
            plans
                .prepare(&f.service, &custody, original.clone(), values.clone())
                .is_err()
        );
        values[1].resource = original_resource;
        values[2].output_root = values[0].output_root.clone();
        assert!(
            plans
                .prepare(&f.service, &custody, original, values)
                .is_err()
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
    }
    #[test]
    fn start_mismatch_discards_the_plan_without_consuming_a_different_grant() {
        let f = fixture();
        let plans = CyclePlans::default();
        let custody = ProductionReceiptCustody::default();
        let grants = EffectGrantRegistry::default();
        let view = plans
            .prepare(&f.service, &custody, intent(&f), connections(&f))
            .unwrap();
        let token = grant(&grants, &view);
        let mut changed = start(&view, token);
        changed.source_sha256 = "0".repeat(64);
        assert!(plans.admit(&f.service, &custody, &grants, changed).is_err());
        assert!(
            plans
                .admit(&f.service, &custody, &grants, start(&view, token))
                .is_err()
        );
        grants
            .consume(
                token,
                EffectKind::RenderLocal,
                EffectScope::project(f.project.id, f.project.generation, f.project.revision),
                &view.effect_subject,
            )
            .unwrap();
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
    }
    #[test]
    fn changed_saved_revision_cannot_start_a_prepared_cycle() {
        let f = fixture();
        let plans = CyclePlans::default();
        let custody = ProductionReceiptCustody::default();
        let grants = EffectGrantRegistry::default();
        let view = plans
            .prepare(&f.service, &custody, intent(&f), connections(&f))
            .unwrap();
        let token = grant(&grants, &view);
        let later = f
            .service
            .apply(
                f.project.id,
                &f.project.stamp(),
                "later-real-edit",
                &Change::RenameProject {
                    title: "Preserve later author input".into(),
                },
            )
            .unwrap()
            .project;
        assert!(
            plans
                .admit(&f.service, &custody, &grants, start(&view, token))
                .is_err()
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), later);
    }
    #[test]
    fn later_root_exclusion_failure_releases_only_our_reservations() {
        let f = fixture();
        let plans = CyclePlans::default();
        let custody = ProductionReceiptCustody::default();
        let grants = EffectGrantRegistry::default();
        let connections = connections(&f);
        let roots = connections.each_ref().map(|c| c.output_root.clone());
        let view = plans
            .prepare(&f.service, &custody, intent(&f), connections)
            .unwrap();
        fs::write(roots[2].join(LOCK_NAME), "foreign-original-operation").unwrap();
        let token = grant(&grants, &view);
        assert!(
            plans
                .admit(&f.service, &custody, &grants, start(&view, token))
                .is_err()
        );
        assert!(!roots[0].join(LOCK_NAME).exists() && !roots[1].join(LOCK_NAME).exists());
        assert_eq!(
            fs::read_to_string(roots[2].join(LOCK_NAME)).unwrap(),
            "foreign-original-operation"
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
    }
    #[test]
    fn request_dtos_reject_paths_authority_journals_and_retry_flags() {
        let f = fixture();
        let request = intent(&f);
        let value = serde_json::to_value(&request).unwrap();
        for field in [
            "connection",
            "executable",
            "source_path",
            "expected",
            "journal",
            "automatic_retry",
            "execution_grant",
        ] {
            let mut injected = value.clone();
            injected[field] = json!("not-authority");
            assert!(serde_json::from_value::<PrepareRequest>(injected).is_err());
        }
        let value = json!({"source": request.source, "source_sha256": request.source_sha256,
            "request_id": request.request_id, "plan_id": Uuid::now_v7(), "effect_grant": Uuid::now_v7()});
        for field in [
            "annotation_body",
            "connection",
            "output_root",
            "journal",
            "automatic_retry",
        ] {
            let mut injected = value.clone();
            injected[field] = json!("not-authority");
            assert!(serde_json::from_value::<StartRequest>(injected).is_err());
        }
    }
}
