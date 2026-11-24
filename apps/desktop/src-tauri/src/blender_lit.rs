//! One explicit lit-still creation, retained original outcome and CAS adoption.
//! This adapter never exposes the fixed diagnostic Edit/LightControl phases as
//! general editing. Native owns Blender commands and Broker terminal certainty.
use crate::{
    blender_product_run::{RootIdentity, RootLock},
    effect_grants::{EffectGrantRegistry, EffectKind, EffectScope},
};
use motionwright_domain::{NativeCapsuleSourceSpec, Project, RevisionStamp};
use motionwright_native::production::{
    BlenderLitArtifact, BlenderLitSource, BlenderLitStillJournal, BlenderLitStillPhase,
    BlenderLitStillPlan, BlenderLitStillRequest, ProductionConnection, ProductionCoordinator,
    ProductionReceiptCustody, build_blender_lit_still_plan,
};
use motionwright_service::{
    BlobDescriptor, FileImportIntent, ProductionReceiptInput, StudioService,
};
use semwright_native_sdk::Error as NativeError;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use uuid::Uuid;

const COMMAND: &str = "motionwright.blender.desktop-lit-still";
const INTENT: &str = "motionwright.blender.desktop-lit-intent/1";
const PROFILE: &str = "planar-project-cycles-lit-still-v1";
const UNCONFIRMED: &str =
    "Read the original Blender still operation; its effects cannot be repeated automatically.";
const TTL: Duration = Duration::from_secs(600);
const MAX_PNG: u64 = 32 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceScope {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareRequest {
    pub source: SourceScope,
    pub source_sha256: String,
    pub fingerprint: String,
    pub request_id: String,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartRequest {
    pub intent: PrepareRequest,
    pub plan_id: Uuid,
    pub effect_grant: Uuid,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadRequest {
    pub source: SourceScope,
    pub attempt_key: String,
}
#[derive(Debug, Clone, Serialize)]
pub struct SourceView {
    pub schema: &'static str,
    pub source: SourceScope,
    pub source_sha256: String,
    pub fingerprint: String,
    pub attempt_key: String,
    pub attempted: bool,
    pub width: u32,
    pub height: u32,
    pub object_count: usize,
    pub profile: String,
    pub native_dispatch_performed: bool,
}
#[derive(Debug, Clone, Serialize)]
pub struct PlanView {
    pub schema: &'static str,
    pub plan_id: Uuid,
    pub intent: PrepareRequest,
    pub effect_subject: String,
    pub attempt_key: String,
    pub capsule_id: Uuid,
    pub expires_in_seconds: u64,
    pub native_dispatch_performed: bool,
    pub execution_grant_inherited: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    schema: String,
    request: PrepareRequest,
    plan_id: Uuid,
    capsule_id: Uuid,
    connection_identity: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssetBinding {
    pub asset_id: Uuid,
    pub sha256: String,
    pub bytes: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SavedAssets {
    pub expected_after: RevisionStamp,
    pub capsule_id: Uuid,
    pub image: AssetBinding,
    pub editable_source: AssetBinding,
    pub width: u32,
    pub height: u32,
}
#[derive(Debug, Serialize)]
pub struct AttemptView {
    pub schema: &'static str,
    pub attempt_key: String,
    pub original_request_id: String,
    pub source: SourceScope,
    pub source_sha256: String,
    pub fingerprint: String,
    pub state: String,
    pub source_current: bool,
    pub native_outcome: Option<Value>,
    pub saved_assets: Option<SavedAssets>,
    pub journal: Option<Value>,
    pub receipt_id: Option<Uuid>,
    pub automatic_retry: bool,
    pub execution_grant_inherited: bool,
    pub visual_acceptance: bool,
}
struct Plan {
    intent: Intent,
    project: Project,
    connection: ProductionConnection,
    root: RootIdentity,
    subject: String,
    expires: Instant,
}
#[derive(Clone, Default)]
pub struct LitPlans(Arc<Mutex<BTreeMap<Uuid, Plan>>>);
pub struct AdmittedStill {
    intent: Intent,
    project: Project,
    pub request: BlenderLitStillRequest,
    pub coordinator: ProductionCoordinator,
    root: RootIdentity,
    _lock: RootLock,
}
#[derive(Clone)]
pub struct MeasuredStill {
    image: BlenderLitArtifact,
    source: BlenderLitSource,
    width: u32,
    height: u32,
}
#[derive(Clone)]
pub struct ImageAdoption {
    project: Project,
    image: AssetBinding,
}
fn sha(value: &impl Serialize) -> Result<String, String> {
    serde_json::to_vec(value)
        .map(|bytes| format!("{:x}", Sha256::digest(bytes)))
        .map_err(|_| UNCONFIRMED.into())
}
fn is_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn key(fingerprint: &str) -> String {
    format!("blender-lit-{fingerprint}")
}
fn request_identity(value: &str) -> bool {
    value
        .strip_prefix("blit-ui-")
        .and_then(|v| Uuid::parse_str(v).ok())
        .is_some_and(|id| !id.is_nil() && value == format!("blit-ui-{id}"))
}
pub fn project(service: &StudioService, source: &SourceScope) -> Result<Project, String> {
    let value = service
        .project(source.project_id)
        .map_err(|_| UNCONFIRMED)?;
    if source.scene_id.is_nil()
        || value.generation != source.generation
        || value.revision != source.revision
        || !value.scenes.iter().any(|scene| scene.id == source.scene_id)
    {
        return Err("The saved Blender scene or project revision changed.".into());
    }
    Ok(value)
}
fn fingerprint(plan: &BlenderLitStillPlan) -> Result<String, String> {
    // Exclude project bookkeeping and unrelated assets. A new request id,
    // import, revision or restore generation cannot replay the same geometry.
    sha(&json!({
        "profile":plan.profile, "project_id":plan.source.contribution.project_id,
        "scene_id":plan.source.contribution.scene_id, "camera":plan.source.camera,
        "composition":plan.composition_spec, "light_translation":plan.light_translation,
        "light_rotation":plan.light_rotation,
    }))
}
pub fn inspect(service: &StudioService, source: &SourceScope) -> Result<SourceView, String> {
    let saved = project(service, source)?;
    let plan =
        build_blender_lit_still_plan(&saved, source.scene_id).map_err(|error| error.message)?;
    let fingerprint = fingerprint(&plan)?;
    let attempt_key = key(&fingerprint);
    let attempted = service
        .latest_production_receipt(saved.id, &attempt_key)
        .map_err(|_| UNCONFIRMED)?
        .is_some();
    if service.project(saved.id).map_err(|_| UNCONFIRMED)? != saved {
        return Err(UNCONFIRMED.into());
    }
    Ok(SourceView {
        schema: "motionwright.blender.desktop-lit-source/1",
        source: source.clone(),
        source_sha256: plan.source.project_json_sha256,
        fingerprint,
        attempt_key,
        attempted,
        width: plan.source.camera.resolution[0],
        height: plan.source.camera.resolution[1],
        object_count: plan.source.contribution.objects.len(),
        profile: plan.profile,
        native_dispatch_performed: false,
    })
}
pub fn checked(service: &StudioService, request: &PrepareRequest) -> Result<Project, String> {
    if !request_identity(&request.request_id)
        || !is_sha(&request.source_sha256)
        || !is_sha(&request.fingerprint)
    {
        return Err("The original Blender still identity is invalid.".into());
    }
    let view = inspect(service, &request.source)?;
    if view.source_sha256 != request.source_sha256
        || view.fingerprint != request.fingerprint
        || view.attempted
    {
        return Err(UNCONFIRMED.into());
    }
    project(service, &request.source)
}
fn roots_current(root: &RootIdentity) -> Result<(), String> {
    root.check()?;
    for name in ["lit-original.png", "lit-original.blend"] {
        if std::fs::symlink_metadata(root.path.join(name)).is_ok() {
            return Err(
                "The selected Blender output root already contains an original still.".into(),
            );
        }
        match std::fs::symlink_metadata(root.path.join(name)) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            _ => return Err("The selected Blender output root cannot be safely inspected.".into()),
        }
    }
    Ok(())
}
fn native_request(request: &PrepareRequest, project: &Project) -> BlenderLitStillRequest {
    BlenderLitStillRequest {
        project_id: project.id,
        expected: project.stamp(),
        scene_id: request.source.scene_id,
        request_id: request.request_id.clone(),
        phase: BlenderLitStillPhase::Create,
        source: None,
        edit_node_id: None,
    }
}
impl LitPlans {
    pub fn prepare(
        &self,
        service: &StudioService,
        custody: &ProductionReceiptCustody,
        request: PrepareRequest,
        connection: ProductionConnection,
    ) -> Result<PlanView, String> {
        let project = checked(service, &request)?;
        if connection.resource != project.resource_key() {
            return Err("Blender connection belongs to another project.".into());
        }
        let root = RootIdentity::capture(&connection)?;
        roots_current(&root)?;
        let coordinator = ProductionCoordinator::with_receipt_custody(
            service.clone(),
            connection.clone(),
            custody,
        )
        .map_err(|_| "Blender owner connection is unavailable.")?;
        coordinator
            .preflight_blender_lit_still(&native_request(&request, &project))
            .map_err(|error| error.message)?;
        if service.project(project.id).map_err(|_| UNCONFIRMED)? != project {
            return Err(UNCONFIRMED.into());
        }
        let plan_id = Uuid::now_v7();
        let intent = Intent {
            schema: INTENT.into(),
            request: request.clone(),
            plan_id,
            capsule_id: Uuid::now_v7(),
            connection_identity: connection.identity().map_err(|_| UNCONFIRMED)?,
        };
        let subject = format!("blender-lit|{plan_id}|{}", sha(&intent)?);
        let view = PlanView {
            schema: "motionwright.blender.desktop-lit-plan/1",
            plan_id,
            intent: request.clone(),
            effect_subject: subject.clone(),
            attempt_key: key(&request.fingerprint),
            capsule_id: intent.capsule_id,
            expires_in_seconds: TTL.as_secs(),
            native_dispatch_performed: false,
            execution_grant_inherited: false,
        };
        let now = Instant::now();
        let mut plans = self.0.lock().map_err(|_| UNCONFIRMED)?;
        plans.retain(|_, plan| plan.expires > now);
        if plans.len() >= 16 {
            return Err("Let earlier Blender preparations expire.".into());
        }
        plans.insert(
            plan_id,
            Plan {
                intent,
                project,
                connection,
                root,
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
        start: StartRequest,
    ) -> Result<AdmittedStill, String> {
        let plan = self
            .0
            .lock()
            .map_err(|_| UNCONFIRMED)?
            .remove(&start.plan_id)
            .ok_or(UNCONFIRMED)?;
        if plan.expires <= Instant::now()
            || serde_json::to_value(&plan.intent.request).map_err(|_| UNCONFIRMED)?
                != serde_json::to_value(&start.intent).map_err(|_| UNCONFIRMED)?
        {
            return Err("The Blender start differs from its original preparation.".into());
        }
        let current = checked(service, &start.intent)?;
        if current != plan.project {
            return Err("The prepared Blender project changed.".into());
        }
        roots_current(&plan.root)?;
        let coordinator = ProductionCoordinator::with_receipt_custody(
            service.clone(),
            plan.connection.clone(),
            custody,
        )
        .map_err(|_| "Blender owner connection is unavailable.")?;
        let request = native_request(&start.intent, &current);
        coordinator
            .preflight_blender_lit_still(&request)
            .map_err(|error| error.message)?;
        grants.consume(
            start.effect_grant,
            EffectKind::RenderLocal,
            EffectScope::project(current.id, current.generation, current.revision),
            &plan.subject,
        )?;
        let lock = RootLock::acquire(&plan.root, &start.intent.request_id)?;
        Ok(AdmittedStill {
            intent: plan.intent,
            project: current,
            request,
            coordinator,
            root: plan.root,
            _lock: lock,
        })
    }
}
impl AdmittedStill {
    pub fn check_roots(&self) -> Result<(), String> {
        self.root.check()
    }
    pub fn reserve(&self, service: &StudioService) -> Result<(), String> {
        if service.project(self.project.id).map_err(|_| UNCONFIRMED)? != self.project {
            return Err(UNCONFIRMED.into());
        }
        roots_current(&self.root)?;
        // Existing BEGIN IMMEDIATE admits this source fingerprint once, even
        // across another process, request UUID or plan. No native call precedes it.
        service
            .append_production_receipt(self.input("dispatching", json!({"intent":self.intent}))?)
            .map(|_| ())
            .map_err(|_| UNCONFIRMED.into())
    }
    fn input(&self, stage: &str, payload: Value) -> Result<ProductionReceiptInput, String> {
        Ok(ProductionReceiptInput {
            project_id: self.project.id,
            generation: self.project.generation,
            revision: self.project.revision,
            request_id: key(&self.intent.request.fingerprint),
            request_sha256: sha(&self.intent)?,
            command: COMMAND.into(),
            stage: stage.into(),
            payload,
        })
    }
    pub fn measure(&self, outcome: &Value) -> Result<MeasuredStill, String> {
        self.root.check()?;
        let plan = build_blender_lit_still_plan(&self.project, self.request.scene_id)
            .map_err(|_| UNCONFIRMED)?;
        if outcome.get("schema").and_then(Value::as_str) != Some("motionwright.blender.lit-still/1")
            || outcome.get("profile").and_then(Value::as_str) != Some(PROFILE)
            || outcome.get("phase").and_then(Value::as_str) != Some("create")
            || outcome.get("project_json_sha256").and_then(Value::as_str)
                != Some(self.intent.request.source_sha256.as_str())
            || outcome.get("native_render_invoked") != Some(&json!(true))
            || outcome.get("native_render_file_measured") != Some(&json!(true))
            || outcome.get("visual_acceptance") != Some(&json!(false))
            || outcome.get("general_product_3d_authoring") != Some(&json!(false))
            || outcome.get("source") != Some(&Value::Null)
            || outcome.get("edit_node_id") != Some(&Value::Null)
        {
            return Err("The original native still reply is not bound to its saved source.".into());
        }
        let image: BlenderLitArtifact =
            serde_json::from_value(outcome.get("image").cloned().ok_or(UNCONFIRMED)?)
                .map_err(|_| UNCONFIRMED)?;
        let source: BlenderLitSource =
            serde_json::from_value(outcome.get("saved_source").cloned().ok_or(UNCONFIRMED)?)
                .map_err(|_| UNCONFIRMED)?;
        if image.relative_path != "lit-original.png"
            || source.file.relative_path != "lit-original.blend"
            || source.edited_node_id.is_some()
            || !is_sha(&image.sha256)
            || !is_sha(&source.file.sha256)
            || image.bytes == 0
            || image.bytes > MAX_PNG
            || source.file.bytes == 0
            || source.file.bytes > 512 * 1024 * 1024
        {
            return Err(UNCONFIRMED.into());
        }
        Ok(MeasuredStill {
            image,
            source,
            width: plan.source.camera.resolution[0],
            height: plan.source.camera.resolution[1],
        })
    }
    pub fn import_image(
        &self,
        service: &StudioService,
        measured: &MeasuredStill,
    ) -> Result<ImageAdoption, String> {
        self.root.check()?;
        if service.project(self.project.id).map_err(|_| UNCONFIRMED)? != self.project {
            return Err(UNCONFIRMED.into());
        }
        let descriptor = BlobDescriptor {
            sha256: measured.image.sha256.clone(),
            size_bytes: measured.image.bytes,
        };
        let saved = service.import_file_verified(self.project.id, &self.project.stamp(),
            &format!("{}-image", self.request.request_id), self.root.path.join(&measured.image.relative_path), &descriptor,
            FileImportIntent::Asset { name:"Blender lit original.png".into(), media_type:"image/png".into() })
            .map_err(|_| "The original Blender image import is unconfirmed; inspect its existing receipt.")?.project;
        let asset = saved
            .assets
            .iter()
            .find(|asset| {
                !self.project.assets.iter().any(|old| old.id == asset.id)
                    && asset.content_sha256.as_deref() == Some(measured.image.sha256.as_str())
                    && asset.media_type == "image/png"
            })
            .ok_or(UNCONFIRMED)?;
        let image = AssetBinding {
            asset_id: asset.id,
            sha256: measured.image.sha256.clone(),
            bytes: measured.image.bytes,
        };
        Ok(ImageAdoption {
            project: saved,
            image,
        })
    }
    pub fn import_source(
        &self,
        service: &StudioService,
        measured: &MeasuredStill,
        image: &ImageAdoption,
    ) -> Result<SavedAssets, String> {
        self.root.check()?;
        if service.project(self.project.id).map_err(|_| UNCONFIRMED)? != image.project {
            return Err(UNCONFIRMED.into());
        }
        let file = &measured.source.file;
        let descriptor = BlobDescriptor {
            sha256: file.sha256.clone(),
            size_bytes: file.bytes,
        };
        let saved = service.import_file_verified(self.project.id, &image.project.stamp(),
            &format!("{}-source", self.request.request_id), self.root.path.join(&file.relative_path), &descriptor,
            FileImportIntent::NativeCapsuleSource { spec:NativeCapsuleSourceSpec {
                name:"Blender lit original.blend".into(), media_type:"application/x-blender".into(),
                capsule_id:self.intent.capsule_id, scene_id:self.request.scene_id, label:"Blender lit original".into(),
                editable_parameters:vec![], native_editor_hint:"Blender".into(),
            }}).map_err(|_| "The original Blender source import is unconfirmed; inspect its existing receipt.")?.project;
        let capsule = saved
            .production_design
            .capsules
            .iter()
            .find(|c| {
                c.id == self.intent.capsule_id
                    && c.source_sha256 == file.sha256
                    && c.scene_id == self.request.scene_id
            })
            .ok_or(UNCONFIRMED)?;
        Ok(SavedAssets {
            expected_after: saved.stamp(),
            capsule_id: capsule.id,
            image: image.image.clone(),
            editable_source: AssetBinding {
                asset_id: capsule.source_asset_id,
                sha256: file.sha256.clone(),
                bytes: file.bytes,
            },
            width: measured.width,
            height: measured.height,
        })
    }
    pub fn finish(
        &self,
        service: &StudioService,
        native_outcome: &Result<Value, NativeError>,
        journal: &BlenderLitStillJournal,
        adoption: Result<SavedAssets, String>,
    ) -> AttemptView {
        let (stage, state) = match (native_outcome, &adoption) {
            (Ok(_), Ok(_)) => ("completed", "completed"),
            (Err(error), _) if error.outcome_known => ("failed_known", "failed_known"),
            (Err(_), _) => ("outcome_unknown", "outcome_unknown"),
            _ => ("outcome_unknown", "native_completed_adoption_unconfirmed"),
        };
        let native_outcome = serde_json::to_value(native_outcome).unwrap_or(Value::Null);
        let summary = json!({"schema":"motionwright.blender.lit-journal-summary/1",
            "phase":journal.phase,"native_call_count":journal.calls.len(),"automatic_retry":false});
        let payload = json!({"intent":self.intent,"state":state,"native_outcome":native_outcome,
            "saved_assets":adoption.as_ref().ok(),"adoption_error":adoption.as_ref().err(),"journal":summary});
        let receipt = self.input(stage, payload).and_then(|input| {
            service
                .append_production_receipt(input)
                .map_err(|_| UNCONFIRMED.into())
        });
        AttemptView {
            schema: "motionwright.blender.desktop-lit-attempt/1",
            attempt_key: key(&self.intent.request.fingerprint),
            original_request_id: self.request.request_id.clone(),
            source: self.intent.request.source.clone(),
            source_sha256: self.intent.request.source_sha256.clone(),
            fingerprint: self.intent.request.fingerprint.clone(),
            state: if receipt.is_ok() {
                state.into()
            } else {
                "terminal_unconfirmed".into()
            },
            source_current: false,
            native_outcome: Some(native_outcome),
            saved_assets: adoption.ok(),
            journal: Some(summary),
            receipt_id: receipt.ok().map(|r| r.id),
            automatic_retry: false,
            execution_grant_inherited: false,
            visual_acceptance: false,
        }
    }
}
fn read_intent(receipt: &motionwright_service::ProductionReceipt) -> Result<Intent, String> {
    let intent: Intent =
        serde_json::from_value(receipt.payload.get("intent").cloned().ok_or(UNCONFIRMED)?)
            .map_err(|_| UNCONFIRMED)?;
    if intent.schema != INTENT
        || !request_identity(&intent.request.request_id)
        || !is_sha(&intent.request.fingerprint)
        || !is_sha(&intent.request.source_sha256)
        || receipt.command != COMMAND
        || intent.plan_id.is_nil()
        || intent.capsule_id.is_nil()
        || receipt.project_id != intent.request.source.project_id
        || receipt.generation != intent.request.source.generation
        || receipt.revision != intent.request.source.revision
        || receipt.request_id != key(&intent.request.fingerprint)
        || receipt.request_sha256 != sha(&intent)?
    {
        return Err(UNCONFIRMED.into());
    }
    Ok(intent)
}
pub fn read_attempt(
    service: &StudioService,
    request: &ReadRequest,
) -> Result<Option<AttemptView>, String> {
    let current = project(service, &request.source)?;
    if !request
        .attempt_key
        .strip_prefix("blender-lit-")
        .is_some_and(is_sha)
    {
        return Err(UNCONFIRMED.into());
    }
    let Some(receipt) = service
        .latest_production_receipt(current.id, &request.attempt_key)
        .map_err(|_| UNCONFIRMED)?
    else {
        return Ok(None);
    };
    let intent = read_intent(&receipt)?;
    if intent.request.source.scene_id != request.source.scene_id {
        return Err(UNCONFIRMED.into());
    }
    let terminal = receipt.stage != "dispatching";
    let state = if terminal {
        receipt
            .payload
            .get("state")
            .and_then(Value::as_str)
            .ok_or(UNCONFIRMED)?
    } else {
        "dispatching"
    };
    if !matches!(
        (receipt.stage.as_str(), state),
        ("dispatching", "dispatching")
            | ("completed", "completed")
            | ("failed_known", "failed_known")
            | (
                "outcome_unknown",
                "outcome_unknown" | "native_completed_adoption_unconfirmed"
            )
    ) {
        return Err(UNCONFIRMED.into());
    }
    let saved_assets: Option<SavedAssets> = serde_json::from_value(
        receipt
            .payload
            .get("saved_assets")
            .cloned()
            .unwrap_or(Value::Null),
    )
    .map_err(|_| UNCONFIRMED)?;
    if (state == "completed") != saved_assets.is_some() {
        return Err(UNCONFIRMED.into());
    }
    if let Some(saved) = &saved_assets {
        if saved.capsule_id != intent.capsule_id
            || saved.expected_after.resource != current.resource_key()
            || saved.expected_after.generation != intent.request.source.generation
            || saved.expected_after.revision <= intent.request.source.revision
            || !is_sha(&saved.image.sha256)
            || !is_sha(&saved.editable_source.sha256)
            || saved.image.asset_id.is_nil()
            || saved.editable_source.asset_id.is_nil()
            || saved.width == 0
            || saved.height == 0
            || saved.image.bytes == 0
            || saved.image.bytes > MAX_PNG
        {
            return Err(UNCONFIRMED.into());
        }
    }
    if terminal {
        let outcome = receipt
            .payload
            .get("native_outcome")
            .and_then(Value::as_object)
            .ok_or(UNCONFIRMED)?;
        if outcome.len() != 1 {
            return Err(UNCONFIRMED.into());
        }
        if let Some(saved) = &saved_assets {
            let native = outcome.get("Ok").ok_or(UNCONFIRMED)?;
            let file = native.pointer("/saved_source/file").ok_or(UNCONFIRMED)?;
            let image = native.get("image").ok_or(UNCONFIRMED)?;
            if native.get("schema").and_then(Value::as_str)
                != Some("motionwright.blender.lit-still/1")
                || native.get("profile").and_then(Value::as_str) != Some(PROFILE)
                || native.get("phase").and_then(Value::as_str) != Some("create")
                || native.get("project_json_sha256").and_then(Value::as_str)
                    != Some(intent.request.source_sha256.as_str())
                || native.get("native_render_invoked") != Some(&json!(true))
                || native.get("native_render_file_measured") != Some(&json!(true))
                || native.get("visual_acceptance") != Some(&json!(false))
                || image.get("relative_path").and_then(Value::as_str) != Some("lit-original.png")
                || image.get("sha256").and_then(Value::as_str) != Some(saved.image.sha256.as_str())
                || image.get("bytes").and_then(Value::as_u64) != Some(saved.image.bytes)
                || file.get("relative_path").and_then(Value::as_str) != Some("lit-original.blend")
                || file.get("sha256").and_then(Value::as_str)
                    != Some(saved.editable_source.sha256.as_str())
                || file.get("bytes").and_then(Value::as_u64) != Some(saved.editable_source.bytes)
            {
                return Err(UNCONFIRMED.into());
            }
        } else if state == "native_completed_adoption_unconfirmed" {
            if !outcome.get("Ok").is_some_and(Value::is_object) {
                return Err(UNCONFIRMED.into());
            }
        } else if !outcome.get("Err").is_some_and(Value::is_object) {
            return Err(UNCONFIRMED.into());
        }
    }
    let source_current = current.generation == intent.request.source.generation
        && build_blender_lit_still_plan(&current, request.source.scene_id)
            .ok()
            .and_then(|p| fingerprint(&p).ok())
            .as_deref()
            == Some(intent.request.fingerprint.as_str());
    if service.project(current.id).map_err(|_| UNCONFIRMED)? != current {
        return Err(UNCONFIRMED.into());
    }
    Ok(Some(AttemptView {
        schema: "motionwright.blender.desktop-lit-attempt/1",
        attempt_key: request.attempt_key.clone(),
        original_request_id: intent.request.request_id,
        source: intent.request.source,
        source_sha256: intent.request.source_sha256,
        fingerprint: intent.request.fingerprint,
        state: state.into(),
        source_current,
        native_outcome: receipt.payload.get("native_outcome").cloned(),
        saved_assets,
        journal: receipt.payload.get("journal").cloned(),
        receipt_id: Some(receipt.id),
        automatic_retry: false,
        execution_grant_inherited: false,
        visual_acceptance: false,
    }))
}
pub fn read_png(service: &StudioService, request: &ReadRequest) -> Result<Vec<u8>, String> {
    let view = read_attempt(service, request)?.ok_or(UNCONFIRMED)?;
    let saved = view
        .saved_assets
        .filter(|_| view.state == "completed")
        .ok_or(UNCONFIRMED)?;
    let current = project(service, &request.source)?;
    if saved.expected_after.generation == current.generation
        && saved.expected_after.revision > current.revision
    {
        return Err(UNCONFIRMED.into());
    }
    let bytes = service
        .verified_render_png_asset(
            current.id,
            &current.stamp(),
            saved.image.asset_id,
            &saved.image.sha256,
        )
        .map_err(
            |_| "The retained Blender PNG is missing, changed or belongs to another project.",
        )?;
    if bytes.len() as u64 != saved.image.bytes
        || bytes.len() < 24
        || !bytes.starts_with(b"\x89PNG\r\n\x1a\n")
        || &bytes[12..16] != b"IHDR"
        || bytes[16..20] != saved.width.to_be_bytes()
        || bytes[20..24] != saved.height.to_be_bytes()
    {
        return Err("The retained Blender PNG no longer matches its native observation.".into());
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind,
    };
    use std::{collections::BTreeSet, fs};
    use tempfile::{TempDir, tempdir};

    struct Fixture {
        _temp: TempDir,
        service: StudioService,
        project: Project,
        connection: ProductionConnection,
    }
    fn fixture() -> Fixture {
        let temp = tempdir().unwrap();
        let service = StudioService::open(temp.path().join("lit.sqlite3")).unwrap();
        let mut project = service
            .create_project("Lit source custody fixture")
            .unwrap();
        let apply = |request: &str, change: Change, project: &mut Project| {
            *project = service
                .apply(project.id, &project.stamp(), request, &change)
                .unwrap()
                .project;
        };
        apply(
            "scene",
            Change::AddScene {
                name: "Lit scene".into(),
                objective: "Bound the original source".into(),
                duration_seconds: 2,
            },
            &mut project,
        );
        let scene_id = project.scenes[0].id;
        apply(
            "renderer",
            Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            },
            &mut project,
        );
        apply(
            "node",
            Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id: Uuid::now_v7(),
                    name: "Gold card".into(),
                    kind: "rectangle".into(),
                    parent_id: None,
                    x: 240.0,
                    y: 180.0,
                    width: 360.0,
                    height: 160.0,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                    text: None,
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: 1,
                    style: NodeStyle {
                        fill: Some("#F5B84A".into()),
                        stroke: None,
                        stroke_width: 0.0,
                        font_family: None,
                        font_size: None,
                        font_weight: None,
                        line_height: None,
                        blend_mode: BlendMode::Normal,
                    },
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                },
            },
            &mut project,
        );
        let owner = temp.path().join("owner");
        fs::create_dir(&owner).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&owner, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let (executable, session_file, socket) = (
            owner.join("inert-validation-fixture"),
            owner.join("session.json"),
            owner.join("socket"),
        );
        for path in [&executable, &session_file, &socket] {
            fs::write(path, b"inert local validation fixture").unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
            }
        }
        let output_root = owner.join("output");
        fs::create_dir(&output_root).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&output_root, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let connection = ProductionConnection {
            schema: "motionwright-semwright-connection/1".into(),
            executable,
            executable_sha256: format!("{:x}", Sha256::digest(b"inert local validation fixture")),
            socket,
            session_file,
            output_root,
            resource: project.resource_key(),
        };
        Fixture {
            _temp: temp,
            service,
            project,
            connection,
        }
    }
    fn source(project: &Project) -> SourceScope {
        SourceScope {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id: project.scenes[0].id,
        }
    }
    fn intent(f: &Fixture) -> PrepareRequest {
        let view = inspect(&f.service, &source(&f.project)).unwrap();
        PrepareRequest {
            source: view.source,
            source_sha256: view.source_sha256,
            fingerprint: view.fingerprint,
            request_id: format!("blit-ui-{}", Uuid::now_v7()),
        }
    }
    fn admitted(f: &Fixture) -> AdmittedStill {
        let plans = LitPlans::default();
        let custody = ProductionReceiptCustody::default();
        let grants = EffectGrantRegistry::default();
        let view = plans
            .prepare(&f.service, &custody, intent(f), f.connection.clone())
            .unwrap();
        let grant = grants
            .issue(
                EffectKind::RenderLocal,
                EffectScope::project(f.project.id, f.project.generation, f.project.revision),
                &view.effect_subject,
            )
            .unwrap();
        plans
            .admit(
                &f.service,
                &custody,
                &grants,
                StartRequest {
                    intent: view.intent,
                    plan_id: view.plan_id,
                    effect_grant: grant.token,
                },
            )
            .unwrap()
    }
    #[test]
    fn blender_lit_preparation_has_no_dispatch_and_durable_claim_blocks_new_request_and_unrelated_revision()
     {
        let f = fixture();
        let before = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let original = admitted(&f);
        assert_eq!(
            before,
            f.service
                .production_receipt_watermark(f.project.id)
                .unwrap()
        );
        assert_eq!(f.project, f.service.project(f.project.id).unwrap());
        original.reserve(&f.service).unwrap();
        assert!(original.reserve(&f.service).is_err());
        let request = ReadRequest {
            source: source(&f.project),
            attempt_key: key(&original.intent.request.fingerprint),
        };
        assert_eq!(
            read_attempt(&f.service, &request).unwrap().unwrap().state,
            "dispatching"
        );
        assert!(checked(&f.service, &intent(&f)).is_err());
        let changed = f
            .service
            .apply(
                f.project.id,
                &f.project.stamp(),
                "unrelated-scene",
                &Change::AddScene {
                    name: "Unrelated".into(),
                    objective: "Unrelated metadata cannot repeat original native work".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let view = inspect(&f.service, &source(&changed)).unwrap();
        assert_eq!(view.fingerprint, original.intent.request.fingerprint);
        assert_ne!(view.source_sha256, original.intent.request.source_sha256);
        assert!(view.attempted);
        let reopened = StudioService::open(f._temp.path().join("lit.sqlite3")).unwrap();
        let request = ReadRequest {
            source: source(&changed),
            ..request
        };
        assert_eq!(
            read_attempt(&reopened, &request).unwrap().unwrap().state,
            "dispatching"
        );
        assert_eq!(changed, reopened.project(changed.id).unwrap());
    }
    #[test]
    fn blender_lit_source_fingerprint_ignores_bookkeeping_but_preserves_real_source_changes() {
        let f = fixture();
        let plan = build_blender_lit_still_plan(&f.project, f.project.scenes[0].id).unwrap();
        let mut changed = f.project.clone();
        changed.revision += 7;
        changed.generation = Uuid::now_v7();
        let later = build_blender_lit_still_plan(&changed, changed.scenes[0].id).unwrap();
        assert_eq!(fingerprint(&plan).unwrap(), fingerprint(&later).unwrap());
        assert_ne!(
            plan.source.project_json_sha256,
            later.source.project_json_sha256
        );
        changed.scenes[0].nodes[0].x += 1.0;
        assert_ne!(
            fingerprint(&plan).unwrap(),
            fingerprint(&build_blender_lit_still_plan(&changed, changed.scenes[0].id).unwrap())
                .unwrap()
        );
    }
    fn synthetic_reply(f: &Fixture) -> Value {
        // Only a source/custody fixture: no actual Host, pixel decoder, Blender
        // scene or Native command runs. These bytes are not physical evidence.
        let plan = build_blender_lit_still_plan(&f.project, f.project.scenes[0].id).unwrap();
        let mut png = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR".to_vec();
        png.extend_from_slice(&plan.source.camera.resolution[0].to_be_bytes());
        png.extend_from_slice(&plan.source.camera.resolution[1].to_be_bytes());
        let blend = b"BLENDER-v300 source custody fixture";
        fs::write(f.connection.output_root.join("lit-original.png"), &png).unwrap();
        fs::write(f.connection.output_root.join("lit-original.blend"), blend).unwrap();
        json!({"schema":"motionwright.blender.lit-still/1","profile":PROFILE,"phase":"create",
            "project_json_sha256":plan.source.project_json_sha256,"native_render_invoked":true,"native_render_file_measured":true,
            "visual_acceptance":false,"general_product_3d_authoring":false,"source":null,"edit_node_id":null,
            "image":{"relative_path":"lit-original.png","sha256":format!("{:x}",Sha256::digest(&png)),"bytes":png.len()},
            "saved_source":{"file":{"relative_path":"lit-original.blend","sha256":format!("{:x}",Sha256::digest(blend)),"bytes":blend.len()},
                "island":"synthetic-fixture","composition_fingerprint":"a".repeat(64),"origin_native_session":"synthetic-fixture","edited_node_id":null}
        })
    }
    #[test]
    fn blender_lit_synthetic_native_reply_adopts_both_originals_and_reads_verified_cas_after_reopen()
     {
        let f = fixture();
        let original = admitted(&f);
        original.reserve(&f.service).unwrap();
        let reply = synthetic_reply(&f);
        let measured = original.measure(&reply).unwrap();
        let image = original.import_image(&f.service, &measured).unwrap();
        let saved = original
            .import_source(&f.service, &measured, &image)
            .unwrap();
        assert_eq!(saved.expected_after.revision, f.project.revision + 2);
        let view = original.finish(
            &f.service,
            &Ok(reply),
            &BlenderLitStillJournal::default(),
            Ok(saved.clone()),
        );
        assert_eq!(view.state, "completed");
        assert!(!view.visual_acceptance);
        drop(original);
        fs::remove_file(f.connection.output_root.join("lit-original.png")).unwrap();
        fs::remove_file(f.connection.output_root.join("lit-original.blend")).unwrap();
        let reopened = StudioService::open(f._temp.path().join("lit.sqlite3")).unwrap();
        let current = reopened.project(f.project.id).unwrap();
        let request = ReadRequest {
            source: source(&current),
            attempt_key: view.attempt_key,
        };
        let recovered = read_attempt(&reopened, &request).unwrap().unwrap();
        assert!(recovered.source_current);
        let before = reopened.production_receipt_watermark(current.id).unwrap();
        assert_eq!(
            read_png(&reopened, &request).unwrap().len() as u64,
            saved.image.bytes
        );
        assert_eq!(
            before,
            reopened.production_receipt_watermark(current.id).unwrap()
        );
        let foreign = reopened.create_project("Foreign PNG owner").unwrap();
        assert!(
            reopened
                .verified_render_png_asset(
                    foreign.id,
                    &foreign.stamp(),
                    saved.image.asset_id,
                    &saved.image.sha256
                )
                .is_err()
        );
        let stale = ReadRequest {
            source: source(&f.project),
            ..request
        };
        assert!(read_png(&reopened, &stale).is_err());
    }
    #[test]
    fn blender_lit_changed_source_bytes_fail_before_any_adoption_and_unknown_cannot_restart() {
        let f = fixture();
        let original = admitted(&f);
        original.reserve(&f.service).unwrap();
        let reply = synthetic_reply(&f);
        let measured = original.measure(&reply).unwrap();
        fs::write(
            f.connection.output_root.join("lit-original.png"),
            b"changed original bytes",
        )
        .unwrap();
        assert!(original.import_image(&f.service, &measured).is_err());
        assert_eq!(f.project, f.service.project(f.project.id).unwrap());
        let error = NativeError::new(
            semwright_native_sdk::ErrorCode::BackendFailed,
            "Synthetic original uncertainty",
        )
        .uncertain();
        let view = original.finish(
            &f.service,
            &Err(error),
            &BlenderLitStillJournal::default(),
            Err("No adoption".into()),
        );
        assert_eq!(view.state, "outcome_unknown");
        let mut next = original.intent.request.clone();
        next.request_id = format!("blit-ui-{}", Uuid::now_v7());
        assert!(checked(&f.service, &next).is_err());
    }
    #[test]
    fn blender_lit_closed_ipc_refuses_paths_phase_replay_and_inherited_authority() {
        let f = fixture();
        let request = intent(&f);
        for field in [
            "output_root",
            "executable",
            "phase",
            "retry",
            "native_outcome",
            "execution_grant_inherited",
        ] {
            let mut value = serde_json::to_value(&request).unwrap();
            value[field] = json!("not accepted");
            assert!(
                serde_json::from_value::<PrepareRequest>(value).is_err(),
                "{field}"
            );
        }
        let mut stale = request;
        stale.source.revision += 1;
        assert!(checked(&f.service, &stale).is_err());
    }
}
