//! Durable source lineage for the fixed product cycle.
//!
//! Receipts describe one original attempt. They are never execution grants,
//! serialized native witnesses, or instructions to repeat a native operation.

use super::*;
use motionwright_domain::{Asset, NativeCapsule, Project};
use motionwright_storage::ProductionReceiptWatermark;

const ROOT_COMMAND: &str = "motionwright.blender.product-cycle";
const INTENT_SCHEMA: &str = "motionwright.blender.product-cycle-intent/1";
const ATOMIC_INTENT_SCHEMA: &str = "motionwright.blender.product-cycle-intent/2";
const TERMINAL_SCHEMA: &str = "motionwright.blender.product-cycle-terminal/1";
const MAX_HISTORY_RECEIPTS: u64 = 20_000;
const MAX_HISTORY_BYTES: usize = 64 * 1024 * 1024;
const HISTORY_PAGE: usize = 32;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductSourceScope {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductAssetBinding {
    pub asset_id: Uuid,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductNativeBinding {
    pub asset_id: Uuid,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductAnnotationEdit {
    pub before: String,
    pub after: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CycleIntent {
    schema: String,
    scope: BlenderProductSourceScope,
    original_request_id: String,
    recipe: BlenderProductAssetBinding,
    annotation: BlenderProductAnnotationEdit,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct CompletedLineage {
    original: BlenderProductNativeBinding,
    edited: BlenderProductNativeBinding,
    original_attached: RevisionStamp,
    edited_attached: RevisionStamp,
    original_surface_png_sha256: String,
    edited_surface_png_sha256: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
enum CycleOutcome {
    Completed { lineage: CompletedLineage },
    FailedKnown { error: Error },
    OutcomeUnknown { error: Error },
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CycleTerminal {
    schema: String,
    claim_receipt_id: Uuid,
    intent: CycleIntent,
    outcome: CycleOutcome,
}

/// Source metadata only. Deserializing a binding does not admit execution.
#[derive(Debug, Clone, Serialize)]
pub struct BlenderProductLineageSnapshot {
    pub original_request_id: String,
    pub claim_receipt_id: Uuid,
    pub terminal_receipt_id: Uuid,
    pub expected_before: RevisionStamp,
    pub original_attached: RevisionStamp,
    pub expected_after: RevisionStamp,
    pub recipe: BlenderProductAssetBinding,
    pub original: BlenderProductNativeBinding,
    pub edited: BlenderProductNativeBinding,
    pub annotation: BlenderProductAnnotationEdit,
    pub original_surface_png_sha256: String,
    pub edited_surface_png_sha256: String,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BlenderProductAttemptState {
    Completed,
    FailedKnown,
    OutcomeUnknown,
}

#[derive(Debug, Clone, Serialize)]
pub struct BlenderProductAttemptSummary {
    pub original_request_id: String,
    pub expected_before: RevisionStamp,
    pub claim_receipt_id: Uuid,
    pub terminal_receipt_id: Option<Uuid>,
    pub state: BlenderProductAttemptState,
    pub native_outcome_known: bool,
    pub error_code: Option<String>,
}

/// No connection, path, custody journal, or execution capability is returned.
#[derive(Debug, Serialize)]
pub struct BlenderProductSourceSnapshot {
    pub schema: &'static str,
    pub project: Project,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub current_source: BlenderProductAssetBinding,
    pub original_recipe: BlenderProductAssetBinding,
    pub effective_recipe: ProductScene,
    pub source_kind: &'static str,
    pub lineage: Option<BlenderProductLineageSnapshot>,
    /// Only this Store is observed. None is not evidence of never-dispatched.
    pub attempt: Option<BlenderProductAttemptSummary>,
    pub source_cas_verified: bool,
    pub source_acceptance_inherited: bool,
    pub execution_grant_inherited: bool,
    pub native_dispatch_performed: bool,
    pub full_1200_frame_render: bool,
    pub decoded_pixel_difference_verified: bool,
    pub visual_acceptance: bool,
    pub human_acceptance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BlenderProductAttemptRequest {
    pub scope: BlenderProductSourceScope,
    pub original_request_id: String,
    pub receipt_id: Option<Uuid>,
}

#[derive(Debug, Serialize)]
pub struct BlenderProductAttemptProjection {
    pub schema: &'static str,
    pub project: Project,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub attempt: BlenderProductAttemptSummary,
    pub lineage: Option<BlenderProductLineageSnapshot>,
    pub current_source_matches: bool,
    pub source_cas_verified: bool,
    pub effective_recipe: Option<ProductScene>,
    pub source_unavailable_reason: Option<&'static str>,
    pub execution_grant_inherited: bool,
    pub native_dispatch_performed: bool,
    pub automatic_retry: bool,
    pub full_1200_frame_render: bool,
    pub decoded_pixel_difference_verified: bool,
    pub visual_acceptance: bool,
    pub human_acceptance: bool,
}

pub(super) struct ProductCycleCompletion {
    pub(super) value: Value,
    lineage: CompletedLineage,
}

pub(super) struct ProductCycleClaim {
    intent: CycleIntent,
    key: String,
    digest: String,
    claim_id: Uuid,
    custody: crate::production::receipt_custody::ReceiptReservation,
}

fn unavailable(message: &'static str) -> Error {
    Error::new(ErrorCode::Unavailable, message)
}

fn stale_snapshot() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Product source or receipt snapshot changed during read",
    )
}

fn intent_key(intent: &CycleIntent) -> NativeResult<String> {
    // The same retained source binding keeps one key across UI requests,
    // revisions, generations, annotations, and duplicate Asset ids. This barrier
    // depends on the original receipt history remaining in the canonical Store.
    let binding = json!({
        "schema": "motionwright.blender.product-source-attempt-key/1",
        "project_id": intent.scope.project_id,
        "scene_id": intent.scope.scene_id,
        "capsule_id": intent.scope.capsule_id,
        "recipe_sha256": intent.recipe.sha256,
    });
    Ok(format!(
        "blender-product-cycle:{}",
        request_digest(ROOT_COMMAND, &binding)?
    ))
}

fn intent_digest(intent: &CycleIntent) -> NativeResult<String> {
    let value =
        serde_json::to_value(intent).map_err(|_| invalid("Product intent could not be encoded"))?;
    request_digest(ROOT_COMMAND, &value)
}

fn validate_intent(intent: &CycleIntent) -> NativeResult<()> {
    let request = BlenderProductCreateRequest {
        project_id: intent.scope.project_id,
        expected: intent.scope.expected.clone(),
        scene_id: intent.scope.scene_id,
        capsule_id: intent.scope.capsule_id,
        request_id: intent.original_request_id.clone(),
    };
    product_request_valid(&request)?;
    if !matches!(intent.schema.as_str(), INTENT_SCHEMA | ATOMIC_INTENT_SCHEMA)
        || !is_sha256(&intent.recipe.sha256)
        || intent.annotation.before.is_empty()
        || intent.annotation.after.is_empty()
        || intent.annotation.before.len() > PRODUCT_RECIPE_MAX_BYTES as usize
        || intent.annotation.after.len() > PRODUCT_RECIPE_MAX_BYTES as usize
        || intent.annotation.before == intent.annotation.after
    {
        return Err(unavailable("Product attempt intent is invalid"));
    }
    Ok(())
}

fn make_intent(
    request: &BlenderProductCycleRequest,
    source: &SavedProductSource,
) -> NativeResult<CycleIntent> {
    if source.project.id != request.project_id
        || source.project.stamp() != request.expected
        || source.scene_id != request.scene_id
        || source.capsule_id != request.capsule_id
    {
        return Err(stale_snapshot());
    }
    let intent = CycleIntent {
        schema: INTENT_SCHEMA.into(),
        scope: BlenderProductSourceScope {
            project_id: request.project_id,
            expected: request.expected.clone(),
            scene_id: request.scene_id,
            capsule_id: request.capsule_id,
        },
        original_request_id: request.request_id.clone(),
        recipe: BlenderProductAssetBinding {
            asset_id: source.source_asset_id,
            sha256: source.source_sha256.clone(),
        },
        annotation: BlenderProductAnnotationEdit {
            before: source.source.annotation.clone(),
            after: request.annotation_body.clone(),
        },
    };
    validate_intent(&intent)?;
    let (_, steps) = crate::blender_product_scene::project_product_edit(
        &source.source,
        &crate::blender_product_scene::ProductEdit::Annotation(intent.annotation.after.clone()),
    )
    .map_err(|_| invalid("Product annotation is outside the closed source profile"))?;
    if steps.len() != 1 {
        return Err(invalid(
            "Product intent must contain exactly one annotation edit",
        ));
    }
    Ok(intent)
}

fn make_atomic_intent(
    request: &BlenderProductCycleRequest,
    source: &SavedProductSource,
) -> NativeResult<CycleIntent> {
    // The source-attempt key intentionally stays identical across protocols.
    // A new protocol never grants permission to repeat unresolved old work.
    let mut intent = make_intent(request, source)?;
    intent.schema = ATOMIC_INTENT_SCHEMA.into();
    validate_intent(&intent)?;
    Ok(intent)
}

fn require_atomic_completion_protocol(intent: &CycleIntent, value: &Value) -> NativeResult<()> {
    if intent.schema != ATOMIC_INTENT_SCHEMA
        || value.get("schema").and_then(Value::as_str) != Some(cycle::PRODUCT_CALLER_SCHEMA)
    {
        return Err(unavailable(
            "Product completion cannot mix historical and atomic protocols",
        ));
    }
    Ok(())
}

pub(super) fn reserve_product_cycle(
    coordinator: &ProductionCoordinator,
    request: &BlenderProductCycleRequest,
    source: &SavedProductSource,
) -> NativeResult<ProductCycleClaim> {
    let intent = make_atomic_intent(request, source)?;
    reserve_intent(&coordinator.service, &coordinator.receipt_custody, intent)
}

fn reserve_intent(
    service: &StudioService,
    receipt_custody: &crate::production::receipt_custody::ReceiptCustody,
    intent: CycleIntent,
) -> NativeResult<ProductCycleClaim> {
    validate_intent(&intent)?;
    let key = intent_key(&intent)?;
    let digest = intent_digest(&intent)?;
    let custody = receipt_custody.reserve(true, &key, ROOT_COMMAND, None)?;
    // The existing BEGIN IMMEDIATE append admits dispatching once, at the
    // exact current generation/revision. No native call precedes this claim.
    let receipt = service
        .append_production_receipt(ProductionReceiptInput {
            project_id: intent.scope.project_id,
            generation: intent.scope.expected.generation,
            revision: intent.scope.expected.revision,
            request_id: key.clone(),
            request_sha256: digest.clone(),
            command: ROOT_COMMAND.into(),
            stage: "dispatching".into(),
            payload: json!(intent),
        })
        .map_err(|error| match error {
            motionwright_storage::StorageError::RequestReuse => Error::new(
                ErrorCode::Conflict,
                "Product source was already attempted; inspect its original durable outcome",
            )
            .uncertain(),
            error => storage_error(error),
        })?;
    Ok(ProductCycleClaim {
        intent,
        key,
        digest,
        claim_id: receipt.id,
        custody,
    })
}

impl ProductCycleClaim {
    pub(super) fn journal_intent(&self) -> Value {
        json!({
            "claim_receipt_id": self.claim_id,
            "request_key": self.key,
            "intent": self.intent,
        })
    }
}

fn bound_native_asset(
    project: &Project,
    id: Uuid,
    file: &BlenderOriginalFile,
) -> NativeResult<BlenderProductNativeBinding> {
    let binding = BlenderProductAssetBinding {
        asset_id: id,
        sha256: file.sha256.clone(),
    };
    let _ = asset_with_binding(project, &binding, cycle::BLENDER_PRODUCT_NATIVE_MEDIA_TYPE)?;
    if file.bytes == 0 || file.bytes > ORIGINAL_MAX_BYTES {
        return Err(unavailable("Product native source byte size is invalid"));
    }
    Ok(BlenderProductNativeBinding {
        asset_id: id,
        sha256: file.sha256.clone(),
        bytes: file.bytes,
    })
}

pub(super) fn complete_product_cycle(
    claim: &ProductCycleClaim,
    source: &SavedProductSource,
    original: (&ProductNativeSourceWitness, &Project, Uuid),
    edited: (&ProductNativeSourceWitness, &Project, Uuid),
    verified: &Value,
    value: Value,
) -> NativeResult<ProductCycleCompletion> {
    require_atomic_completion_protocol(&claim.intent, &value).map_err(Error::uncertain)?;
    let (original, original_project, original_asset_id) = original;
    let (edited, edited_project, edited_asset_id) = edited;
    let expected_source = effective_recipe(&source.source, &claim.intent.annotation)?;
    if original.stage != ProductSavedStage::Original
        || edited.stage != ProductSavedStage::EditedAnnotation
        || original.source != source.source
        || edited.source != expected_source
        || original.origin_project != source.project
        || edited.origin_project != source.project
        || [original, edited].iter().any(|witness| {
            witness.scene_id != claim.intent.scope.scene_id
                || witness.capsule_id != claim.intent.scope.capsule_id
                || witness.recipe_asset_id != claim.intent.recipe.asset_id
                || witness.recipe_sha256 != claim.intent.recipe.sha256
                || witness.original_annotation != claim.intent.annotation.before
        })
        || original.file.relative_path != ORIGINAL_FILE
        || edited.file.relative_path != EDITED_FILE
        || original.original_cut_601_sha256 != edited.original_cut_601_sha256
        || verified.get("schema").and_then(Value::as_str) != Some(PRODUCT_CYCLE_SCHEMA)
        || verified.get("phase").and_then(Value::as_str) != Some("product-reopen-verify")
        || verified.get("project_id") != Some(&json!(edited_project.id))
        || verified.get("expected") != Some(&json!(edited_project.stamp()))
        || verified.get("source_asset_id") != Some(&json!(edited_asset_id))
        || verified.get("reopened_source") != Some(&json!(edited.file))
        || verified
            .pointer("/surface_probe/frame")
            .and_then(Value::as_u64)
            != Some(601)
        || verified.get("edited_source_fresh_reopen_performed") != Some(&json!(true))
        || verified.get("reopened_source_cas_verified") != Some(&json!(true))
        || verified
            .get("observed_native_sessions")
            .and_then(Value::as_array)
            .map(Vec::len)
            != Some(3)
    {
        return Err(
            backend("Product completion does not match its original source intent").uncertain(),
        );
    }
    let edited_png = verified
        .pointer("/surface_probe/artifact/sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            backend("Product completion omitted its observed surface PNG").uncertain()
        })?;
    let lineage = CompletedLineage {
        original: bound_native_asset(original_project, original_asset_id, &original.file)?,
        edited: bound_native_asset(edited_project, edited_asset_id, &edited.file)?,
        original_attached: original_project.stamp(),
        edited_attached: edited_project.stamp(),
        original_surface_png_sha256: original.original_cut_601_sha256.clone(),
        edited_surface_png_sha256: edited_png.into(),
    };
    validate_completed(&claim.intent, &lineage)?;
    if current_binding(original_project, &claim.intent.scope)?.asset_id != original_asset_id
        || current_binding(edited_project, &claim.intent.scope)?.asset_id != edited_asset_id
    {
        return Err(
            backend("Product completion lost its same-capsule native attachments").uncertain(),
        );
    }
    Ok(ProductCycleCompletion { value, lineage })
}

pub(super) fn finish_product_cycle(
    service: &StudioService,
    claim: ProductCycleClaim,
    result: NativeResult<ProductCycleCompletion>,
    retained_terminal: &mut Option<Value>,
) -> NativeResult<Value> {
    finish_product_cycle_with_append(claim, result, retained_terminal, |input| {
        service.append_production_receipt(input)
    })
}

fn finish_product_cycle_with_append(
    mut claim: ProductCycleClaim,
    result: NativeResult<ProductCycleCompletion>,
    retained_terminal: &mut Option<Value>,
    append: impl FnOnce(ProductionReceiptInput) -> motionwright_storage::Result<ProductionReceipt>,
) -> NativeResult<Value> {
    // Preserve the original caller terminal before serialization/persistence
    // diagnostics. The existing private journal is retained by the owner.
    *retained_terminal = Some(match &result {
        Ok(completed) => json!({"status": "ok", "value": completed.value}),
        Err(error) => json!({"status": "error", "error": error}),
    });
    let (stage, outcome) = match &result {
        Ok(completed) => (
            "completed",
            json!({"status": "completed", "lineage": completed.lineage}),
        ),
        Err(error) if error.outcome_known => (
            "failed_known",
            json!({"status": "failed_known", "error": error}),
        ),
        Err(error) => (
            "outcome_unknown",
            json!({"status": "outcome_unknown", "error": error}),
        ),
    };
    claim.custody.observe(ProductionReceiptInput {
        project_id: claim.intent.scope.project_id,
        generation: claim.intent.scope.expected.generation,
        revision: claim.intent.scope.expected.revision,
        request_id: claim.key,
        request_sha256: claim.digest,
        command: ROOT_COMMAND.into(),
        stage: stage.into(),
        payload: json!({
            "schema": TERMINAL_SCHEMA,
            "claim_receipt_id": claim.claim_id,
            "intent": claim.intent,
            "outcome": outcome,
        }),
    });
    let appended = append(claim.custody.attempted_receipt());
    match result {
        Ok(completed) => match appended {
            Ok(receipt) => {
                claim.custody.confirmed();
                let mut value = completed.value;
                value["durable_source_lineage"] = json!({
                    "schema": TERMINAL_SCHEMA,
                    "claim_receipt_id": claim.claim_id,
                    "terminal_receipt_id": receipt.id,
                    "execution_grant_inherited": false,
                });
                Ok(value)
            }
            Err(error) => {
                claim.custody.failed(error);
                Err(backend(
                    "Product cycle completed but its source lineage receipt was not persisted",
                )
                .uncertain())
            }
        },
        Err(original) => {
            match appended {
                Ok(_) => claim.custody.confirmed(),
                Err(error) => claim.custody.failed(error),
            }
            // The original native error, including its certainty, wins over
            // a later receipt append failure.
            Err(original)
        }
    }
}

/// Explicit execution entry point using three existing owner connections and
/// the application's existing custody channel. Provisioning remains with the owner.
pub async fn realize_blender_product_cycle_with_connections(
    service: &StudioService,
    connections: [ProductionConnection; 3],
    custody: &ProductionReceiptCustody,
    request: &BlenderProductCycleRequest,
    journal: &mut BlenderProductCycleJournal,
) -> NativeResult<Value> {
    let [create, edit, verify] = connections;
    let create = ProductionCoordinator::with_receipt_custody(service.clone(), create, custody)?;
    let edit = ProductionCoordinator::with_receipt_custody(service.clone(), edit, custody)?;
    let verify = ProductionCoordinator::with_receipt_custody(service.clone(), verify, custody)?;
    create
        .realize_blender_product_cycle(&edit, &verify, request, journal)
        .await
}

fn asset_with_binding<'a>(
    project: &'a Project,
    binding: &BlenderProductAssetBinding,
    media_type: &str,
) -> NativeResult<&'a Asset> {
    if !is_sha256(&binding.sha256) {
        return Err(unavailable("Product source digest is invalid"));
    }
    let mut matches = project
        .assets
        .iter()
        .filter(|asset| asset.id == binding.asset_id);
    let asset = matches
        .next()
        .ok_or_else(|| unavailable("Product source asset is absent"))?;
    if matches.next().is_some()
        || asset.media_type != media_type
        || asset.content_sha256.as_deref() != Some(binding.sha256.as_str())
    {
        return Err(unavailable(
            "Product source asset does not match its immutable binding",
        ));
    }
    Ok(asset)
}

fn source_capsule<'a>(
    project: &'a Project,
    scope: &BlenderProductSourceScope,
) -> NativeResult<&'a NativeCapsule> {
    if project.id != scope.project_id || project.generation != scope.expected.generation {
        return Err(stale_snapshot());
    }
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == scope.scene_id)
        .ok_or_else(|| unavailable("Product source Scene is absent"))?;
    validate_product_source_scene(project, scene)?;
    let capsules: Vec<_> = project
        .production_design
        .capsules
        .iter()
        .filter(|capsule| capsule.scene_id == scope.scene_id)
        .collect();
    if capsules.len() != 1 || capsules[0].id != scope.capsule_id {
        return Err(unavailable("Product source capsule is absent or ambiguous"));
    }
    let capsule = capsules[0];
    capsule
        .validate(&project.scenes, &project.assets)
        .map_err(|_| unavailable("Product source capsule binding is invalid"))?;
    Ok(capsule)
}

fn current_binding(
    project: &Project,
    scope: &BlenderProductSourceScope,
) -> NativeResult<BlenderProductAssetBinding> {
    let capsule = source_capsule(project, scope)?;
    Ok(BlenderProductAssetBinding {
        asset_id: capsule.source_asset_id,
        sha256: capsule.source_sha256.clone(),
    })
}

fn captured_project(
    service: &StudioService,
    scope: &BlenderProductSourceScope,
) -> NativeResult<Project> {
    let project = service.project(scope.project_id).map_err(storage_error)?;
    if project.stamp() != scope.expected {
        return Err(stale_snapshot());
    }
    let _ = source_capsule(&project, scope)?;
    Ok(project)
}

fn bound_bytes(
    service: &StudioService,
    project: &Project,
    binding: &BlenderProductAssetBinding,
    media_type: &str,
    maximum: u64,
    exact_bytes: Option<u64>,
) -> NativeResult<Vec<u8>> {
    let _ = asset_with_binding(project, binding, media_type)?;
    let bytes = {
        let handle = service.store();
        let store = handle.lock();
        if store.load_project(project.id).map_err(storage_error)? != *project {
            return Err(stale_snapshot());
        }
        let bytes = store
            .read_blob(&binding.sha256, maximum)
            .map_err(storage_error)?;
        if store.load_project(project.id).map_err(storage_error)? != *project {
            return Err(stale_snapshot());
        }
        bytes
    };
    if bytes.is_empty()
        || exact_bytes.is_some_and(|expected| bytes.len() as u64 != expected)
        || hex::encode(Sha256::digest(&bytes)) != binding.sha256
    {
        return Err(unavailable(
            "Product source CAS bytes do not match their immutable binding",
        ));
    }
    Ok(bytes)
}

fn recipe_from_cas(
    service: &StudioService,
    project: &Project,
    binding: &BlenderProductAssetBinding,
) -> NativeResult<ProductScene> {
    let bytes = bound_bytes(
        service,
        project,
        binding,
        BLENDER_PRODUCT_RECIPE_MEDIA_TYPE,
        PRODUCT_RECIPE_MAX_BYTES,
        None,
    )?;
    decode_blender_product_recipe(&bytes)
}

fn native_from_cas(
    service: &StudioService,
    project: &Project,
    binding: &BlenderProductNativeBinding,
) -> NativeResult<()> {
    // Owned immutable bytes are dropped before the next capture. No path lease
    // is minted, and no Store guard is retained across native work.
    let _ = bound_bytes(
        service,
        project,
        &BlenderProductAssetBinding {
            asset_id: binding.asset_id,
            sha256: binding.sha256.clone(),
        },
        cycle::BLENDER_PRODUCT_NATIVE_MEDIA_TYPE,
        ORIGINAL_MAX_BYTES,
        Some(binding.bytes),
    )?;
    Ok(())
}

fn effective_recipe(
    recipe: &ProductScene,
    annotation: &BlenderProductAnnotationEdit,
) -> NativeResult<ProductScene> {
    if recipe.annotation != annotation.before || annotation.before == annotation.after {
        return Err(unavailable(
            "Product annotation lineage does not match its original recipe",
        ));
    }
    let (edited, steps) = crate::blender_product_scene::project_product_edit(
        recipe,
        &crate::blender_product_scene::ProductEdit::Annotation(annotation.after.clone()),
    )
    .map_err(|_| unavailable("Product annotation lineage is outside the closed source profile"))?;
    if steps.len() != 1 {
        return Err(unavailable(
            "Product lineage is not one exact annotation edit",
        ));
    }
    let mut expected = recipe.clone();
    expected.annotation = annotation.after.clone();
    if edited != expected {
        return Err(unavailable(
            "Product lineage changed an unrecorded source field",
        ));
    }
    Ok(edited)
}

fn validate_completed(intent: &CycleIntent, lineage: &CompletedLineage) -> NativeResult<()> {
    let (original_step, edited_step) = match intent.schema.as_str() {
        INTENT_SCHEMA => (2, 4),
        ATOMIC_INTENT_SCHEMA => (1, 2),
        _ => {
            return Err(unavailable(
                "Product lineage has an unknown source-save protocol",
            ));
        }
    };
    let before = &intent.scope.expected;
    if lineage.original_attached.resource != before.resource
        || lineage.edited_attached.resource != before.resource
        || lineage.original_attached.generation != before.generation
        || lineage.edited_attached.generation != before.generation
        || before.revision.checked_add(original_step) != Some(lineage.original_attached.revision)
        || before.revision.checked_add(edited_step) != Some(lineage.edited_attached.revision)
        || lineage.original.asset_id == intent.recipe.asset_id
        || lineage.edited.asset_id == intent.recipe.asset_id
        || lineage.original.asset_id == lineage.edited.asset_id
        || lineage.original.sha256 == lineage.edited.sha256
        || !is_sha256(&lineage.original_surface_png_sha256)
        || !is_sha256(&lineage.edited_surface_png_sha256)
        || [&lineage.original, &lineage.edited].iter().any(|binding| {
            !is_sha256(&binding.sha256) || binding.bytes == 0 || binding.bytes > ORIGINAL_MAX_BYTES
        })
    {
        return Err(unavailable("Product completed lineage is invalid"));
    }
    Ok(())
}

struct HistorySize {
    bytes: usize,
}

impl std::io::Write for HistorySize {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.bytes = self
            .bytes
            .checked_add(bytes.len())
            .filter(|value| *value <= MAX_HISTORY_BYTES)
            .ok_or_else(|| {
                std::io::Error::other("Product receipt snapshot byte budget exceeded")
            })?;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

struct ProductReceiptChain {
    claim: ProductionReceipt,
    intent: CycleIntent,
    terminal: Option<(ProductionReceipt, CycleTerminal)>,
}

fn snapshot_rows(
    service: &StudioService,
    project: &Project,
    watermark: &ProductionReceiptWatermark,
) -> NativeResult<Vec<ProductionReceipt>> {
    if watermark.count > MAX_HISTORY_RECEIPTS
        || watermark.latest_id.is_none() != (watermark.count == 0)
    {
        return Err(unavailable(
            "Product receipt history exceeds the complete snapshot budget",
        ));
    }
    let mut roots = Vec::new();
    let mut seen = BTreeSet::new();
    let mut before = None;
    let mut size = HistorySize { bytes: 0 };
    while (seen.len() as u64) < watermark.count {
        let page = service
            .production_receipts_snapshot_page(project.id, watermark, before, HISTORY_PAGE)
            .map_err(|_| stale_snapshot())?;
        if page.is_empty()
            || page.len() > HISTORY_PAGE
            || (before.is_none() && page.first().map(|row| row.id) != watermark.latest_id)
        {
            return Err(stale_snapshot());
        }
        for row in page {
            if row.project_id != project.id
                || !seen.insert(row.id)
                || seen.len() as u64 > watermark.count
            {
                return Err(stale_snapshot());
            }
            serde_json::to_writer(&mut size, &row).map_err(|_| {
                unavailable("Product receipt snapshot exceeds its finite byte budget")
            })?;
            // UUIDs are opaque cursor identities. Only the service supplies order.
            before = Some(row.id);
            if row.command == ROOT_COMMAND {
                roots.push(row);
            }
        }
    }
    if service
        .production_receipt_watermark(project.id)
        .map_err(storage_error)?
        != *watermark
    {
        return Err(stale_snapshot());
    }
    roots.reverse();
    Ok(roots)
}

fn decode_chains(
    project: &Project,
    roots: Vec<ProductionReceipt>,
) -> NativeResult<Vec<ProductReceiptChain>> {
    let mut grouped: BTreeMap<String, Vec<ProductionReceipt>> = BTreeMap::new();
    for row in roots {
        grouped.entry(row.request_id.clone()).or_default().push(row);
    }
    let mut chains = Vec::with_capacity(grouped.len());
    for (_, rows) in grouped {
        if rows.is_empty() || rows.len() > 2 {
            return Err(unavailable(
                "Product attempt has an ambiguous receipt chain",
            ));
        }
        let mut rows = rows.into_iter();
        let claim = rows.next().expect("checked nonempty receipt chain");
        let intent: CycleIntent = serde_json::from_value(claim.payload.clone())
            .map_err(|_| unavailable("Product dispatch receipt has an invalid closed intent"))?;
        validate_intent(&intent)?;
        if claim.stage != "dispatching"
            || claim.command != ROOT_COMMAND
            || claim.project_id != project.id
            || intent.scope.project_id != project.id
            || intent.scope.expected.generation != claim.generation
            || intent.scope.expected.resource != project.resource_key()
            || claim.revision != intent.scope.expected.revision
            || (claim.generation == project.generation && claim.revision > project.revision)
            || claim.request_id != intent_key(&intent)?
            || claim.request_sha256 != intent_digest(&intent)?
        {
            return Err(unavailable(
                "Product dispatch receipt does not match its immutable intent",
            ));
        }
        let terminal = if let Some(row) = rows.next() {
            let parsed: CycleTerminal =
                serde_json::from_value(row.payload.clone()).map_err(|_| {
                    unavailable("Product terminal receipt has an invalid closed lineage")
                })?;
            if row.command != claim.command
                || row.project_id != claim.project_id
                || row.generation != claim.generation
                || row.revision != claim.revision
                || row.request_id != claim.request_id
                || row.request_sha256 != claim.request_sha256
                || parsed.schema != TERMINAL_SCHEMA
                || parsed.claim_receipt_id != claim.id
                || parsed.intent != intent
            {
                return Err(unavailable(
                    "Product terminal receipt does not join its original dispatch",
                ));
            }
            match &parsed.outcome {
                CycleOutcome::Completed { lineage } => {
                    validate_completed(&intent, lineage)?;
                    if row.stage != "completed"
                        || (claim.generation == project.generation
                            && lineage.edited_attached.revision > project.revision)
                    {
                        return Err(unavailable(
                            "Product completion has an invalid result revision",
                        ));
                    }
                }
                CycleOutcome::FailedKnown { error } => {
                    if row.stage != "failed_known" || !error.outcome_known {
                        return Err(unavailable(
                            "Product failure receipt changed native certainty",
                        ));
                    }
                }
                CycleOutcome::OutcomeUnknown { error } => {
                    if row.stage != "outcome_unknown" || error.outcome_known {
                        return Err(unavailable(
                            "Product unknown receipt changed native certainty",
                        ));
                    }
                }
            }
            Some((row, parsed))
        } else {
            None
        };
        chains.push(ProductReceiptChain {
            claim,
            intent,
            terminal,
        });
    }
    Ok(chains)
}

fn chain_scope_matches(chain: &ProductReceiptChain, scope: &BlenderProductSourceScope) -> bool {
    chain.intent.scope.project_id == scope.project_id
        && chain.intent.scope.scene_id == scope.scene_id
        && chain.intent.scope.capsule_id == scope.capsule_id
}

fn chain_summary(chain: &ProductReceiptChain) -> BlenderProductAttemptSummary {
    let (state, known, code) = match chain
        .terminal
        .as_ref()
        .map(|(_, terminal)| &terminal.outcome)
    {
        Some(CycleOutcome::Completed { .. }) => (BlenderProductAttemptState::Completed, true, None),
        Some(CycleOutcome::FailedKnown { error }) => (
            BlenderProductAttemptState::FailedKnown,
            true,
            Some(format!("{:?}", error.code)),
        ),
        Some(CycleOutcome::OutcomeUnknown { error }) => (
            BlenderProductAttemptState::OutcomeUnknown,
            false,
            Some(format!("{:?}", error.code)),
        ),
        None => (BlenderProductAttemptState::OutcomeUnknown, false, None),
    };
    BlenderProductAttemptSummary {
        original_request_id: chain.intent.original_request_id.clone(),
        expected_before: chain.intent.scope.expected.clone(),
        claim_receipt_id: chain.claim.id,
        terminal_receipt_id: chain.terminal.as_ref().map(|(row, _)| row.id),
        state,
        native_outcome_known: known,
        error_code: code,
    }
}

fn completed_lineage(
    chain: &ProductReceiptChain,
) -> Option<(&ProductionReceipt, &CompletedLineage)> {
    match chain.terminal.as_ref() {
        Some((
            row,
            CycleTerminal {
                outcome: CycleOutcome::Completed { lineage },
                ..
            },
        )) => Some((row, lineage)),
        _ => None,
    }
}

fn public_lineage(chain: &ProductReceiptChain) -> Option<BlenderProductLineageSnapshot> {
    let (row, link) = completed_lineage(chain)?;
    Some(BlenderProductLineageSnapshot {
        original_request_id: chain.intent.original_request_id.clone(),
        claim_receipt_id: chain.claim.id,
        terminal_receipt_id: row.id,
        expected_before: chain.intent.scope.expected.clone(),
        original_attached: link.original_attached.clone(),
        expected_after: link.edited_attached.clone(),
        recipe: chain.intent.recipe.clone(),
        original: link.original.clone(),
        edited: link.edited.clone(),
        annotation: chain.intent.annotation.clone(),
        original_surface_png_sha256: link.original_surface_png_sha256.clone(),
        edited_surface_png_sha256: link.edited_surface_png_sha256.clone(),
    })
}

fn binding_is_edited(binding: &BlenderProductAssetBinding, link: &CompletedLineage) -> bool {
    binding.asset_id == link.edited.asset_id && binding.sha256 == link.edited.sha256
}

fn restored_recipe(
    service: &StudioService,
    project: &Project,
    chain: &ProductReceiptChain,
    link: &CompletedLineage,
) -> NativeResult<ProductScene> {
    let original = recipe_from_cas(service, project, &chain.intent.recipe)?;
    let effective = effective_recipe(&original, &chain.intent.annotation)?;
    native_from_cas(service, project, &link.original)?;
    native_from_cas(service, project, &link.edited)?;
    Ok(effective)
}

fn finish_read_snapshot(
    service: &StudioService,
    project: &Project,
    watermark: &ProductionReceiptWatermark,
) -> NativeResult<()> {
    if service.project(project.id).map_err(storage_error)? != *project
        || service
            .production_receipt_watermark(project.id)
            .map_err(storage_error)?
            != *watermark
        || service.project(project.id).map_err(storage_error)? != *project
    {
        return Err(stale_snapshot());
    }
    Ok(())
}

/// Resolve source bytes and original durable lineage without constructing any
/// coordinator, connection, native session, lease, or execution capability.
pub fn read_blender_product_source(
    service: &StudioService,
    scope: &BlenderProductSourceScope,
) -> NativeResult<BlenderProductSourceSnapshot> {
    let project = captured_project(service, scope)?;
    let watermark = service
        .production_receipt_watermark(project.id)
        .map_err(storage_error)?;
    let chains = decode_chains(&project, snapshot_rows(service, &project, &watermark)?)?;
    let current = current_binding(&project, scope)?;
    let current_asset = project
        .assets
        .iter()
        .find(|asset| asset.id == current.asset_id)
        .ok_or_else(|| unavailable("Current product source asset is absent"))?;
    let (recipe, effective, kind, lineage, attempt) = if current_asset.media_type
        == BLENDER_PRODUCT_RECIPE_MEDIA_TYPE
    {
        let original = recipe_from_cas(service, &project, &current)?;
        let matching: Vec<_> = chains
            .iter()
            .filter(|chain| {
                chain_scope_matches(chain, scope) && chain.intent.recipe.sha256 == current.sha256
            })
            .collect();
        if matching.len() > 1 {
            return Err(unavailable(
                "Current product recipe has ambiguous original attempts",
            ));
        }
        let attempt = matching.first().map(|chain| chain_summary(chain));
        // A still-bound authored recipe remains readable even when a native
        // attempt is unknown. Its attempt state is explicit and grants nothing.
        let again = recipe_from_cas(service, &project, &current)?;
        if again != original {
            return Err(stale_snapshot());
        }
        (current.clone(), original, "authored_recipe", None, attempt)
    } else if current_asset.media_type == cycle::BLENDER_PRODUCT_NATIVE_MEDIA_TYPE {
        let matching: Vec<_> = chains
            .iter()
            .filter(|chain| {
                chain_scope_matches(chain, scope)
                    && chain.intent.scope.expected.generation == project.generation
                    && completed_lineage(chain)
                        .is_some_and(|(_, link)| binding_is_edited(&current, link))
            })
            .collect();
        if matching.len() != 1 {
            return Err(unavailable(
                "Current native product source has no unique completed lineage",
            ));
        }
        let chain = matching[0];
        let (_, link) = completed_lineage(chain)
            .ok_or_else(|| unavailable("Product source lineage is incomplete"))?;
        let effective = restored_recipe(service, &project, chain, link)?;
        native_from_cas(service, &project, &link.edited)?;
        (
            chain.intent.recipe.clone(),
            effective,
            "edited_native_source",
            public_lineage(chain),
            Some(chain_summary(chain)),
        )
    } else {
        return Err(unavailable(
            "Current product source has no admitted fixed recipe/native codec",
        ));
    };
    finish_read_snapshot(service, &project, &watermark)?;
    Ok(BlenderProductSourceSnapshot {
        schema: "motionwright.blender.product-source-snapshot/1",
        project,
        scene_id: scope.scene_id,
        capsule_id: scope.capsule_id,
        current_source: current,
        original_recipe: recipe,
        effective_recipe: effective,
        source_kind: kind,
        lineage,
        attempt,
        source_cas_verified: true,
        source_acceptance_inherited: false,
        execution_grant_inherited: false,
        native_dispatch_performed: false,
        full_1200_frame_render: false,
        decoded_pixel_difference_verified: false,
        visual_acceptance: false,
        human_acceptance: false,
    })
}

/// Read an original attempt and its bounded source projection. Missing terminals
/// remain unknown; reading never appends, repairs, resumes, or repeats an effect.
pub fn read_blender_product_attempt(
    service: &StudioService,
    request: &BlenderProductAttemptRequest,
) -> NativeResult<BlenderProductAttemptProjection> {
    let validation = BlenderProductCreateRequest {
        project_id: request.scope.project_id,
        expected: request.scope.expected.clone(),
        scene_id: request.scope.scene_id,
        capsule_id: request.scope.capsule_id,
        request_id: request.original_request_id.clone(),
    };
    product_request_valid(&validation)?;
    let project = captured_project(service, &request.scope)?;
    let watermark = service
        .production_receipt_watermark(project.id)
        .map_err(storage_error)?;
    let chains = decode_chains(&project, snapshot_rows(service, &project, &watermark)?)?;
    let matches: Vec<_> = chains
        .iter()
        .filter(|chain| {
            chain_scope_matches(chain, &request.scope)
                && chain.intent.original_request_id == request.original_request_id
                && request.receipt_id.is_none_or(|id| {
                    chain.claim.id == id
                        || chain.terminal.as_ref().is_some_and(|(row, _)| row.id == id)
                })
        })
        .collect();
    if matches.len() != 1 {
        return Err(unavailable(
            "Original product attempt is absent or ambiguous",
        ));
    }
    let chain = matches[0];
    let current = current_binding(&project, &request.scope)?;
    let mut current_source_matches = false;
    let mut effective = None;
    let mut reason = Some("original_attempt_has_no_completed_lineage");
    if let Some((_, link)) = completed_lineage(chain) {
        current_source_matches = chain.intent.scope.expected.generation == project.generation
            && binding_is_edited(&current, link);
        if current_source_matches {
            match restored_recipe(service, &project, chain, link).and_then(|recipe| {
                native_from_cas(service, &project, &link.edited).map(|()| recipe)
            }) {
                Ok(recipe) => {
                    effective = Some(recipe);
                    reason = None;
                }
                Err(_) => {
                    reason = Some("current_source_cas_or_lineage_unavailable");
                }
            }
        } else if chain.intent.scope.expected.generation != project.generation {
            reason = Some("original_attempt_generation_changed");
        } else {
            reason = Some("current_source_binding_changed");
        }
    }
    finish_read_snapshot(service, &project, &watermark)?;
    Ok(BlenderProductAttemptProjection {
        schema: "motionwright.blender.product-attempt-projection/1",
        project,
        scene_id: request.scope.scene_id,
        capsule_id: request.scope.capsule_id,
        attempt: chain_summary(chain),
        lineage: public_lineage(chain),
        current_source_matches,
        source_cas_verified: effective.is_some(),
        effective_recipe: effective,
        source_unavailable_reason: reason,
        execution_grant_inherited: false,
        native_dispatch_performed: false,
        automatic_retry: false,
        full_1200_frame_render: false,
        decoded_pixel_difference_verified: false,
        visual_acceptance: false,
        human_acceptance: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::blender_product_scene::{CameraCurve, Coating};
    use crate::production::receipt_custody::ReceiptCustody;
    use motionwright_domain::{Change, Fidelity, FidelityReport, RendererKind};

    struct Fixture {
        root: tempfile::TempDir,
        service: StudioService,
        source: SavedProductSource,
        request: BlenderProductCycleRequest,
    }

    fn fixture() -> Fixture {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("lineage.sqlite3")).unwrap();
        let mut project = service
            .create_project("Synthetic product lineage fixture")
            .unwrap();
        project = service
            .apply(
                project.id,
                &project.stamp(),
                "fixture-scene",
                &Change::AddScene {
                    name: "Product source".into(),
                    objective: "Synthetic closed source fixture".into(),
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
                "fixture-renderer",
                &Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::Blender,
                },
            )
            .unwrap()
            .project;
        let mut recipe = ProductScene::vessel("LineageFixture", CameraCurve::BezierAutoClamped);
        recipe.coating = Coating::Graphite;
        recipe.annotation = "ORIGINAL\nSURFACE".into();
        let bytes = serde_json::to_vec_pretty(&recipe).unwrap();
        let path = root.path().join("source.json");
        fs::write(&path, &bytes).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "fixture-recipe",
                &path,
                "source.json".into(),
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
                "fixture-capsule",
                &Change::UpsertNativeCapsule {
                    capsule: NativeCapsule {
                        id: capsule_id,
                        scene_id,
                        source_asset_id: asset.id,
                        source_sha256: asset.content_sha256.clone().unwrap(),
                        label: "Synthetic source lineage".into(),
                        fidelity: FidelityReport {
                            renderer: "Blender".into(),
                            renderer_version: "unobserved".into(),
                            visual: Fidelity::Unavailable,
                            temporal: Fidelity::Unavailable,
                            structural: Fidelity::Unavailable,
                            editable: Fidelity::Unavailable,
                            losses: vec![
                                "Synthetic unit fixture; no native Blender execution.".into(),
                            ],
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
        let request = BlenderProductCycleRequest {
            project_id: project.id,
            expected: project.stamp(),
            scene_id,
            capsule_id,
            request_id: "synthetic-product-cycle".into(),
            annotation_body: "EDITED\nSURFACE".into(),
        };
        let source = read_saved_product_source(
            &service,
            &BlenderProductCreateRequest {
                project_id: request.project_id,
                expected: request.expected.clone(),
                scene_id,
                capsule_id,
                request_id: request.request_id.clone(),
            },
            BLENDER_PRODUCT_RECIPE_MEDIA_TYPE,
            decode_blender_product_recipe,
        )
        .unwrap();
        Fixture {
            root,
            service,
            source,
            request,
        }
    }

    fn scope(f: &Fixture, project: &Project) -> BlenderProductSourceScope {
        BlenderProductSourceScope {
            project_id: project.id,
            expected: project.stamp(),
            scene_id: f.request.scene_id,
            capsule_id: f.request.capsule_id,
        }
    }

    fn claim(f: &Fixture, custody: &ReceiptCustody) -> ProductCycleClaim {
        reserve_intent(
            &f.service,
            custody,
            make_intent(&f.request, &f.source).unwrap(),
        )
        .unwrap()
    }

    // These byte fixtures are not Blender files or native witnesses. They test
    // the existing immutable CAS/receipt reader, never physical native acceptance.
    fn attach_synthetic_native(
        f: &Fixture,
        before: &Project,
        name: &str,
        bytes: &[u8],
    ) -> (Project, BlenderProductNativeBinding) {
        let path = f.root.path().join(name);
        fs::write(&path, bytes).unwrap();
        let imported = f
            .service
            .import_asset_file(
                before.id,
                &before.stamp(),
                &format!("fixture-import-{name}"),
                &path,
                name.into(),
                cycle::BLENDER_PRODUCT_NATIVE_MEDIA_TYPE.into(),
            )
            .unwrap()
            .project;
        let added: Vec<_> = imported
            .assets
            .iter()
            .filter(|asset| !before.assets.iter().any(|old| old.id == asset.id))
            .collect();
        assert_eq!(added.len(), 1);
        let asset = added[0];
        let binding = BlenderProductNativeBinding {
            asset_id: asset.id,
            sha256: asset.content_sha256.clone().unwrap(),
            bytes: bytes.len() as u64,
        };
        let previous = imported
            .production_design
            .capsules
            .iter()
            .find(|capsule| capsule.id == f.request.capsule_id)
            .unwrap();
        let capsule = product_repoint_capsule(previous, binding.asset_id, &binding.sha256);
        let attached = f
            .service
            .apply(
                before.id,
                &imported.stamp(),
                &format!("fixture-attach-{name}"),
                &Change::UpsertNativeCapsule { capsule },
            )
            .unwrap()
            .project;
        (attached, binding)
    }

    fn synthetic_completion(f: &Fixture) -> (Project, ProductCycleCompletion) {
        let (first, original) = attach_synthetic_native(
            f,
            &f.source.project,
            "synthetic-original.blend",
            b"synthetic original CAS bytes",
        );
        let (last, edited) = attach_synthetic_native(
            f,
            &first,
            "synthetic-edited.blend",
            b"synthetic edited CAS bytes",
        );
        let lineage = CompletedLineage {
            original,
            edited,
            original_attached: first.stamp(),
            edited_attached: last.stamp(),
            original_surface_png_sha256: "a".repeat(64),
            edited_surface_png_sha256: "b".repeat(64),
        };
        validate_completed(&make_intent(&f.request, &f.source).unwrap(), &lineage).unwrap();
        (
            last,
            ProductCycleCompletion {
                value: json!({"synthetic_contract_fixture": true, "human_acceptance": false}),
                lineage,
            },
        )
    }

    fn attempt(f: &Fixture, project: &Project) -> BlenderProductAttemptRequest {
        BlenderProductAttemptRequest {
            scope: scope(f, project),
            original_request_id: f.request.request_id.clone(),
            receipt_id: None,
        }
    }

    fn unrelated_receipt(f: &Fixture, project: &Project, index: usize) {
        f.service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: format!("synthetic-unrelated-{index}"),
                request_sha256: "c".repeat(64),
                command: "synthetic.readonly.fixture".into(),
                stage: "completed".into(),
                payload: json!({"fixture_index": index}),
            })
            .unwrap();
    }

    #[test]
    fn product_lineage_authored_recipe_survives_restart_without_any_write_or_native_grant() {
        let f = fixture();
        let before = f.source.project.clone();
        let watermark = f.service.production_receipt_watermark(before.id).unwrap();
        let reopened = StudioService::open(f.root.path().join("lineage.sqlite3")).unwrap();
        let read = read_blender_product_source(&reopened, &scope(&f, &before)).unwrap();
        assert_eq!(read.project, before);
        assert_eq!(read.effective_recipe, f.source.source);
        assert_eq!(read.source_kind, "authored_recipe");
        assert!(read.lineage.is_none() && read.attempt.is_none());
        assert!(!read.execution_grant_inherited && !read.native_dispatch_performed);
        assert!(!read.full_1200_frame_render && !read.human_acceptance);
        assert_eq!(reopened.project(before.id).unwrap(), before);
        assert_eq!(
            reopened.production_receipt_watermark(before.id).unwrap(),
            watermark
        );
    }

    #[test]
    fn product_lineage_restarted_reader_restores_only_recorded_annotation_beyond_256_receipts() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let reserved = claim(&f, &custody);
        let claim_id = reserved.claim_id;
        let (last, completed) = synthetic_completion(&f);
        let mut retained = None;
        let result =
            finish_product_cycle(&f.service, reserved, Ok(completed), &mut retained).unwrap();
        let terminal_id: Uuid =
            serde_json::from_value(result["durable_source_lineage"]["terminal_receipt_id"].clone())
                .unwrap();
        for index in 0..270 {
            unrelated_receipt(&f, &last, index);
        }
        let reopened = StudioService::open(f.root.path().join("lineage.sqlite3")).unwrap();
        let watermark = reopened.production_receipt_watermark(last.id).unwrap();
        assert_eq!(watermark.count, 272);
        let read = read_blender_product_source(&reopened, &scope(&f, &last)).unwrap();
        let mut expected = f.source.source.clone();
        expected.annotation = f.request.annotation_body.clone();
        assert_eq!(read.effective_recipe, expected);
        assert_eq!(read.original_recipe.asset_id, f.source.source_asset_id);
        assert_eq!(read.lineage.as_ref().unwrap().claim_receipt_id, claim_id);
        assert_eq!(
            read.lineage.as_ref().unwrap().terminal_receipt_id,
            terminal_id
        );
        assert_eq!(read.source_kind, "edited_native_source");
        assert!(!read.execution_grant_inherited && !read.decoded_pixel_difference_verified);
        assert!(!read.visual_acceptance && !read.human_acceptance);
        let projection = read_blender_product_attempt(&reopened, &attempt(&f, &last)).unwrap();
        assert_eq!(
            projection.attempt.state,
            BlenderProductAttemptState::Completed
        );
        assert!(projection.current_source_matches && projection.source_cas_verified);
        assert_eq!(projection.effective_recipe, Some(expected));
        assert_eq!(reopened.project(last.id).unwrap(), last);
        assert_eq!(
            reopened.production_receipt_watermark(last.id).unwrap(),
            watermark
        );
        assert_eq!(custody.snapshot()["reserved_slots"], 0);
    }

    #[test]
    fn product_lineage_unknown_root_cannot_be_evaded_by_ui_id_annotation_revision_or_asset_id() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let reserved = claim(&f, &custody);
        let original_key = reserved.key.clone();
        let original_intent = reserved.intent.clone();
        drop(reserved); // Simulated lost continuation; the durable dispatch remains.
        let renamed = f
            .service
            .apply(
                f.source.project.id,
                &f.source.project.stamp(),
                "fixture-unrelated-rename",
                &Change::RenameProject {
                    title: "Unrelated edit".into(),
                },
            )
            .unwrap()
            .project;
        let mut variant = original_intent;
        variant.scope.expected = renamed.stamp();
        variant.original_request_id = "renamed-ui-request".into();
        variant.annotation.after = "ANOTHER\nEDIT".into();
        variant.recipe.asset_id = Uuid::now_v7();
        assert_eq!(intent_key(&variant).unwrap(), original_key);
        let mut other_generation = variant.clone();
        other_generation.scope.expected.generation = Uuid::now_v7();
        // Defensive key invariant only: there is no public in-place generation
        // rotation exercised here. Import into another Store loses receipts.
        assert_eq!(intent_key(&other_generation).unwrap(), original_key);
        let error = reserve_intent(&f.service, &custody, variant).err().unwrap();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(!error.outcome_known);
        let projection = read_blender_product_attempt(&f.service, &attempt(&f, &renamed)).unwrap();
        assert_eq!(
            projection.attempt.state,
            BlenderProductAttemptState::OutcomeUnknown
        );
        assert!(!projection.attempt.native_outcome_known && !projection.automatic_retry);
        assert!(projection.attempt.terminal_receipt_id.is_none());
        let source = read_blender_product_source(&f.service, &scope(&f, &renamed)).unwrap();
        assert_eq!(source.effective_recipe, f.source.source);
        assert_eq!(
            source.attempt.unwrap().state,
            BlenderProductAttemptState::OutcomeUnknown
        );
        assert_eq!(
            f.service
                .production_receipt_watermark(renamed.id)
                .unwrap()
                .count,
            1
        );
    }

    #[test]
    fn product_lineage_atomic_dispatch_claim_is_unique_and_stale_claim_does_not_append() {
        let f = fixture();
        let intent = make_intent(&f.request, &f.source).unwrap();
        let first_custody = ReceiptCustody::default();
        let first = reserve_intent(&f.service, &first_custody, intent.clone()).unwrap();
        let peer = StudioService::open(f.root.path().join("lineage.sqlite3")).unwrap();
        let second = reserve_intent(&peer, &ReceiptCustody::default(), intent.clone())
            .err()
            .unwrap();
        assert_eq!(second.code, ErrorCode::Conflict);
        drop(first);
        let current = peer
            .apply(
                f.source.project.id,
                &f.source.project.stamp(),
                "fixture-new-revision",
                &Change::RenameProject {
                    title: "New revision".into(),
                },
            )
            .unwrap()
            .project;
        let mut stale = intent;
        stale.recipe.sha256 = "d".repeat(64); // Different key, but an old snapshot.
        assert!(reserve_intent(&peer, &ReceiptCustody::default(), stale).is_err());
        assert_eq!(
            peer.production_receipt_watermark(current.id).unwrap().count,
            1
        );
    }

    #[test]
    fn product_lineage_current_native_source_without_terminal_is_unavailable_and_attempt_stays_unknown()
     {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let reserved = claim(&f, &custody);
        let (last, _) = synthetic_completion(&f);
        drop(reserved);
        assert!(read_blender_product_source(&f.service, &scope(&f, &last)).is_err());
        let projection = read_blender_product_attempt(&f.service, &attempt(&f, &last)).unwrap();
        assert_eq!(
            projection.attempt.state,
            BlenderProductAttemptState::OutcomeUnknown
        );
        assert!(projection.effective_recipe.is_none() && !projection.source_cas_verified);
        assert!(!projection.native_dispatch_performed && !projection.execution_grant_inherited);
    }

    #[test]
    fn product_lineage_duplicate_terminal_and_closed_payload_tampering_are_rejected() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let reserved = claim(&f, &custody);
        let key = reserved.key.clone();
        let (last, completed) = synthetic_completion(&f);
        finish_product_cycle(&f.service, reserved, Ok(completed), &mut None).unwrap();
        let watermark = f.service.production_receipt_watermark(last.id).unwrap();
        let rows = snapshot_rows(&f.service, &last, &watermark).unwrap();
        for kind in 0..5 {
            let mut changed = rows.clone();
            match kind {
                0 => changed[0].payload["execution_grant"] = json!({"allow": true}),
                1 => changed[1].payload["claim_receipt_id"] = json!(Uuid::now_v7()),
                2 => {
                    changed[1].payload["outcome"]["lineage"]["edited_attached"]["revision"] =
                        json!(last.revision + 1)
                }
                3 => changed[1].payload["intent"]["annotation"]["after"] = json!("UNRECORDED"),
                _ => changed[1].request_sha256 = "0".repeat(64),
            }
            assert!(decode_chains(&last, changed).is_err());
        }
        let terminal = f
            .service
            .latest_production_receipt(last.id, &key)
            .unwrap()
            .unwrap();
        f.service
            .append_production_receipt(ProductionReceiptInput {
                project_id: terminal.project_id,
                generation: terminal.generation,
                revision: terminal.revision,
                request_id: terminal.request_id,
                request_sha256: terminal.request_sha256,
                command: terminal.command,
                stage: terminal.stage,
                payload: terminal.payload,
            })
            .unwrap();
        assert!(read_blender_product_source(&f.service, &scope(&f, &last)).is_err());
        assert!(read_blender_product_attempt(&f.service, &attempt(&f, &last)).is_err());
    }

    #[test]
    fn product_lineage_cas_length_mismatch_never_restores_the_effective_recipe() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let reserved = claim(&f, &custody);
        let (last, mut completed) = synthetic_completion(&f);
        completed.lineage.edited.bytes += 1; // Synthetic corrupted durable lineage.
        finish_product_cycle(&f.service, reserved, Ok(completed), &mut None).unwrap();
        assert!(read_blender_product_source(&f.service, &scope(&f, &last)).is_err());
        let read = read_blender_product_attempt(&f.service, &attempt(&f, &last)).unwrap();
        assert_eq!(read.attempt.state, BlenderProductAttemptState::Completed);
        assert!(read.current_source_matches);
        assert!(!read.source_cas_verified && read.effective_recipe.is_none());
        assert_eq!(
            read.source_unavailable_reason,
            Some("current_source_cas_or_lineage_unavailable")
        );
    }

    #[test]
    fn product_lineage_repointed_recipe_does_not_inherit_the_previous_native_edit_or_acceptance() {
        let f = fixture();
        let reserved = claim(&f, &ReceiptCustody::default());
        let (last, completed) = synthetic_completion(&f);
        finish_product_cycle(&f.service, reserved, Ok(completed), &mut None).unwrap();
        let previous = last
            .production_design
            .capsules
            .iter()
            .find(|capsule| capsule.id == f.request.capsule_id)
            .unwrap();
        let capsule =
            product_repoint_capsule(previous, f.source.source_asset_id, &f.source.source_sha256);
        let repointed = f
            .service
            .apply(
                last.id,
                &last.stamp(),
                "fixture-repoint-recipe",
                &Change::UpsertNativeCapsule { capsule },
            )
            .unwrap()
            .project;
        let read = read_blender_product_source(&f.service, &scope(&f, &repointed)).unwrap();
        assert_eq!(read.effective_recipe, f.source.source);
        assert!(read.lineage.is_none());
        assert!(!read.source_acceptance_inherited && !read.human_acceptance);
        let attempt = read_blender_product_attempt(&f.service, &attempt(&f, &repointed)).unwrap();
        assert_eq!(attempt.attempt.state, BlenderProductAttemptState::Completed);
        assert!(!attempt.current_source_matches && !attempt.source_cas_verified);
        assert!(attempt.effective_recipe.is_none());
    }

    #[test]
    fn product_lineage_original_error_survives_append_failure_with_exact_private_custody() {
        for known in [true, false] {
            let f = fixture();
            let custody = ReceiptCustody::default();
            let reserved = claim(&f, &custody);
            let original = Error::new(
                ErrorCode::BackendFailed,
                "synthetic private native terminal",
            );
            let original = if known {
                original
            } else {
                original.uncertain()
            };
            let expected = json!(&original);
            let mut retained = None;
            let returned =
                finish_product_cycle_with_append(reserved, Err(original), &mut retained, |_| {
                    Err(motionwright_storage::StorageError::NotFound)
                })
                .unwrap_err();
            assert_eq!(json!(returned), expected);
            assert_eq!(retained.unwrap()["error"], expected);
            let private = custody.snapshot();
            assert_eq!(
                private["entries"][0]["attempted_receipt"]["payload"]["outcome"]["error"],
                expected
            );
            assert_eq!(private["entries"][0]["persistence"], "unconfirmed");
            let read =
                read_blender_product_attempt(&f.service, &attempt(&f, &f.source.project)).unwrap();
            assert_eq!(
                read.attempt.state,
                BlenderProductAttemptState::OutcomeUnknown
            );
            assert!(
                !serde_json::to_string(&read)
                    .unwrap()
                    .contains("synthetic private native terminal")
            );
        }
    }

    #[test]
    fn product_lineage_original_ok_survives_append_failure_and_no_durable_success_is_invented() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let reserved = claim(&f, &custody);
        let (last, completed) = synthetic_completion(&f);
        let expected = completed.value.clone();
        let mut retained = None;
        let returned =
            finish_product_cycle_with_append(reserved, Ok(completed), &mut retained, |_| {
                Err(motionwright_storage::StorageError::NotFound)
            })
            .unwrap_err();
        assert!(!returned.outcome_known);
        assert_eq!(retained.unwrap()["value"], expected);
        assert_eq!(
            custody.snapshot()["entries"][0]["attempted_receipt"]["stage"],
            "completed"
        );
        let read = read_blender_product_attempt(&f.service, &attempt(&f, &last)).unwrap();
        assert_eq!(
            read.attempt.state,
            BlenderProductAttemptState::OutcomeUnknown
        );
        assert!(read.lineage.is_none() && read.effective_recipe.is_none());
    }

    #[test]
    fn product_lineage_receipt_watermark_project_scope_and_history_budget_fail_closed() {
        let f = fixture();
        let before = f.source.project.clone();
        let watermark = f.service.production_receipt_watermark(before.id).unwrap();
        unrelated_receipt(&f, &before, 1);
        assert!(snapshot_rows(&f.service, &before, &watermark).is_err());
        assert!(finish_read_snapshot(&f.service, &before, &watermark).is_err());
        let too_many = ProductionReceiptWatermark {
            latest_id: Some(Uuid::now_v7()),
            count: MAX_HISTORY_RECEIPTS + 1,
        };
        assert!(snapshot_rows(&f.service, &before, &too_many).is_err());
        let current = f
            .service
            .apply(
                before.id,
                &before.stamp(),
                "fixture-stale-reader",
                &Change::RenameProject {
                    title: "Changed current scope".into(),
                },
            )
            .unwrap()
            .project;
        assert!(read_blender_product_source(&f.service, &scope(&f, &before)).is_err());
        let mut wrong = scope(&f, &current);
        wrong.expected.resource = "another-resource".into();
        assert!(read_blender_product_source(&f.service, &wrong).is_err());
    }

    #[test]
    fn product_lineage_recipe_and_annotation_ranges_are_revalidated_without_native_effects() {
        let f = fixture();
        for after in ["", "A\nB\nC", "\u{00e1}", "ORIGINAL\nSURFACE"] {
            assert!(
                effective_recipe(
                    &f.source.source,
                    &BlenderProductAnnotationEdit {
                        before: f.source.source.annotation.clone(),
                        after: after.into(),
                    }
                )
                .is_err()
            );
        }
        assert!(
            effective_recipe(
                &f.source.source,
                &BlenderProductAnnotationEdit {
                    before: "WRONG BEFORE".into(),
                    after: "EDITED".into(),
                }
            )
            .is_err()
        );
        let scope = scope(&f, &f.source.project);
        let snapshot = read_blender_product_source(&f.service, &scope).unwrap();
        let text = serde_json::to_value(snapshot).unwrap();
        for field in [
            "native_dispatch_performed",
            "execution_grant_inherited",
            "source_acceptance_inherited",
            "full_1200_frame_render",
            "decoded_pixel_difference_verified",
            "visual_acceptance",
            "human_acceptance",
        ] {
            assert_eq!(text[field], false);
        }
        assert!(text.get("native_witness").is_none());
        assert!(text.get("connection").is_none());
        assert!(text.get("private_journal").is_none());
    }
    #[test]
    fn product_lineage_explicitly_saved_different_recipe_bytes_get_a_distinct_root_claim() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let first = claim(&f, &custody);
        let original_key = first.key.clone();
        drop(first);
        let mut changed = f.source.source.clone();
        changed.roughness = 0.45;
        assert_ne!(changed, f.source.source);
        let bytes = serde_json::to_vec(&changed).unwrap();
        let digest = hex::encode(Sha256::digest(&bytes));
        let path = f.root.path().join("revised-recipe.json");
        fs::write(&path, &bytes).unwrap();
        let imported = f
            .service
            .import_asset_file(
                f.source.project.id,
                &f.source.project.stamp(),
                "fixture-explicit-new-recipe",
                &path,
                "revised-recipe.json".into(),
                BLENDER_PRODUCT_RECIPE_MEDIA_TYPE.into(),
            )
            .unwrap()
            .project;
        let asset = imported
            .assets
            .iter()
            .find(|asset| asset.content_sha256.as_deref() == Some(digest.as_str()))
            .unwrap();
        let prior = imported
            .production_design
            .capsules
            .iter()
            .find(|capsule| capsule.id == f.request.capsule_id)
            .unwrap();
        let capsule = product_repoint_capsule(prior, asset.id, &digest);
        let saved = f
            .service
            .apply(
                imported.id,
                &imported.stamp(),
                "fixture-explicit-repoint",
                &Change::UpsertNativeCapsule { capsule },
            )
            .unwrap()
            .project;
        let mut request = f.request.clone();
        request.request_id = "explicit-new-source-attempt".into();
        request.expected = saved.stamp();
        let source = read_saved_product_source(
            &f.service,
            &BlenderProductCreateRequest {
                project_id: saved.id,
                expected: saved.stamp(),
                scene_id: request.scene_id,
                capsule_id: request.capsule_id,
                request_id: request.request_id.clone(),
            },
            BLENDER_PRODUCT_RECIPE_MEDIA_TYPE,
            decode_blender_product_recipe,
        )
        .unwrap();
        let second = reserve_intent(
            &f.service,
            &custody,
            make_intent(&request, &source).unwrap(),
        )
        .unwrap();
        assert_ne!(second.key, original_key);
        assert_eq!(
            f.service
                .production_receipt_watermark(saved.id)
                .unwrap()
                .count,
            2
        );
        let snapshot = read_blender_product_source(&f.service, &scope(&f, &saved)).unwrap();
        assert_eq!(snapshot.effective_recipe, changed);
        assert_eq!(
            snapshot.attempt.unwrap().original_request_id,
            request.request_id
        );
        assert!(!snapshot.native_dispatch_performed && !snapshot.execution_grant_inherited);
    }

    #[cfg(unix)]
    #[test]
    fn product_lineage_missing_or_replaced_cas_bytes_never_restore_a_completed_native_source() {
        let f = fixture();
        let reserved = claim(&f, &ReceiptCustody::default());
        let (last, completed) = synthetic_completion(&f);
        let digest = completed.lineage.edited.sha256.clone();
        finish_product_cycle(&f.service, reserved, Ok(completed), &mut None).unwrap();
        let path = f
            .service
            .store()
            .lock()
            .verified_blob_path(&digest)
            .unwrap();
        // Adversarial mutation of this test's private synthetic CAS, never a
        // production cleanup, permission change, lease release, or native file.
        fs::remove_file(&path).unwrap();
        assert!(read_blender_product_source(&f.service, &scope(&f, &last)).is_err());
        fs::write(&path, b"replaced synthetic bytes with the wrong digest").unwrap();
        assert!(read_blender_product_source(&f.service, &scope(&f, &last)).is_err());
        let read = read_blender_product_attempt(&f.service, &attempt(&f, &last)).unwrap();
        assert_eq!(read.attempt.state, BlenderProductAttemptState::Completed);
        assert!(!read.source_cas_verified && read.effective_recipe.is_none());
        assert!(!read.execution_grant_inherited && !read.native_dispatch_performed);
    }

    #[test]
    fn product_lineage_real_bundle_import_does_not_restore_receipts_or_execution_authority() {
        for native_source in [false, true] {
            let f = fixture();
            let custody = ReceiptCustody::default();
            let reserved = claim(&f, &custody);
            let current = if native_source {
                let (current, completed) = synthetic_completion(&f);
                finish_product_cycle(&f.service, reserved, Ok(completed), &mut None).unwrap();
                current
            } else {
                // An actual durable dispatch with no terminal. No native renderer
                // is invoked by this synthetic source/receipt unit fixture.
                drop(reserved);
                f.source.project.clone()
            };
            let original_watermark = f.service.production_receipt_watermark(current.id).unwrap();
            let original_rows = snapshot_rows(&f.service, &current, &original_watermark).unwrap();
            let bundle = f.root.path().join("portable-bundle");
            f.service
                .export_project_bundle(current.id, &bundle)
                .unwrap();
            let destination =
                StudioService::open(f.root.path().join("destination.sqlite3")).unwrap();
            let imported = destination.import_project_bundle(&bundle).unwrap();
            assert_eq!(imported.id, current.id);
            assert_ne!(imported.generation, current.generation);
            assert_eq!(imported.revision, current.revision);
            assert_eq!(imported.scenes, current.scenes);
            assert_eq!(
                imported.production_design.capsules,
                current.production_design.capsules
            );
            let watermark = destination
                .production_receipt_watermark(imported.id)
                .unwrap();
            assert_eq!(watermark.count, 0);
            assert!(watermark.latest_id.is_none());

            // Parser coverage only, using real original receipts and the actual
            // imported identity: records remain historical. These rows are never
            // inserted into the destination Store or promoted into a grant.
            let historical = decode_chains(&imported, original_rows).unwrap();
            assert_eq!(historical.len(), 1);
            assert_eq!(
                chain_summary(&historical[0]).expected_before.generation,
                current.generation
            );
            assert_ne!(
                historical[0].intent.scope.expected.generation,
                imported.generation
            );
            if native_source {
                assert!(read_blender_product_source(&destination, &scope(&f, &imported)).is_err());
            } else {
                let read =
                    read_blender_product_source(&destination, &scope(&f, &imported)).unwrap();
                assert_eq!(read.effective_recipe, f.source.source);
                assert_eq!(read.source_kind, "authored_recipe");
                assert!(read.attempt.is_none() && read.lineage.is_none());
                assert!(!read.execution_grant_inherited && !read.native_dispatch_performed);
                assert!(!read.source_acceptance_inherited && !read.human_acceptance);
                // None means no local attempt record. It is not a never-dispatched
                // certificate and cannot authorize a native retry.
            }
            assert!(read_blender_product_attempt(&destination, &attempt(&f, &imported)).is_err());
            assert_eq!(destination.project(imported.id).unwrap(), imported);
            assert_eq!(
                destination
                    .production_receipt_watermark(imported.id)
                    .unwrap(),
                watermark
            );
            assert_eq!(
                f.service.production_receipt_watermark(current.id).unwrap(),
                original_watermark
            );
        }
    }

    // Synthetic bytes exercise the existing service/CAS boundary only.
    // They are not native .blend files, renderer results or fidelity evidence.
    fn attach_atomic_synthetic_native(
        f: &Fixture,
        before: &Project,
        name: &str,
        bytes: &[u8],
    ) -> (Project, BlenderProductNativeBinding) {
        let path = f.root.path().join(name);
        fs::write(&path, bytes).unwrap();
        let prior = before
            .production_design
            .capsules
            .iter()
            .find(|capsule| capsule.id == f.request.capsule_id)
            .unwrap();
        let spec = motionwright_domain::NativeCapsuleSourceSpec {
            name: name.into(),
            media_type: cycle::BLENDER_PRODUCT_NATIVE_MEDIA_TYPE.into(),
            capsule_id: prior.id,
            scene_id: prior.scene_id,
            label: prior.label.clone(),
            editable_parameters: vec!["product.annotation.body".into()],
            native_editor_hint: "Blender".into(),
        };
        let after = f
            .service
            .import_native_capsule_source_file(
                before.id,
                &before.stamp(),
                &format!("fixture-atomic-{name}"),
                &path,
                spec.clone(),
            )
            .unwrap()
            .project;
        assert_eq!(after.revision, before.revision + 1);
        let added: Vec<_> = after
            .assets
            .iter()
            .filter(|asset| !before.assets.iter().any(|old| old.id == asset.id))
            .collect();
        assert_eq!(added.len(), 1);
        let asset = added[0];
        assert_eq!(
            asset.source_revision.as_deref(),
            Some("native-capsule-source-import")
        );
        let capsule = after
            .production_design
            .capsules
            .iter()
            .find(|capsule| capsule.id == prior.id)
            .unwrap();
        assert_eq!(*capsule, spec.bind_asset(asset).unwrap());
        assert_eq!(capsule.fidelity.renderer, "source-import");
        assert_eq!(capsule.fidelity.renderer_version, "unverified");
        assert_eq!(capsule.fidelity.evidence_sha256, None);
        assert!(capsule.property_fidelity.is_empty());
        let binding = BlenderProductNativeBinding {
            asset_id: asset.id,
            sha256: asset.content_sha256.clone().unwrap(),
            bytes: bytes.len() as u64,
        };
        let handle = f.service.store();
        let store = handle.lock();
        assert_eq!(
            store.read_blob(&binding.sha256, 1024 * 1024).unwrap(),
            bytes
        );
        for old in &before.assets {
            assert_eq!(after.assets.iter().find(|row| row.id == old.id), Some(old));
            if let Some(digest) = &old.content_sha256 {
                assert!(!store.read_blob(digest, 1024 * 1024).unwrap().is_empty());
            }
        }
        drop(store);
        (after, binding)
    }

    #[test]
    fn product_atomic_lineage_uses_one_revision_per_source_and_reads_reset_fidelity() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let intent = make_atomic_intent(&f.request, &f.source).unwrap();
        let reserved = reserve_intent(&f.service, &custody, intent.clone()).unwrap();
        let (first, original) = attach_atomic_synthetic_native(
            &f,
            &f.source.project,
            "atomic-original.blend",
            b"synthetic atomic original",
        );
        let (last, edited) = attach_atomic_synthetic_native(
            &f,
            &first,
            "atomic-edited.blend",
            b"synthetic atomic edited",
        );
        let lineage = CompletedLineage {
            original,
            edited,
            original_attached: first.stamp(),
            edited_attached: last.stamp(),
            original_surface_png_sha256: "a".repeat(64),
            edited_surface_png_sha256: "b".repeat(64),
        };
        validate_completed(&intent, &lineage).unwrap();
        assert!(
            validate_completed(&make_intent(&f.request, &f.source).unwrap(), &lineage).is_err()
        );
        let completed = ProductCycleCompletion {
            value: json!({"synthetic_contract_fixture": true, "human_acceptance": false}),
            lineage,
        };
        let mut terminal = None;
        finish_product_cycle(&f.service, reserved, Ok(completed), &mut terminal).unwrap();
        let reopened = StudioService::open(f.root.path().join("lineage.sqlite3")).unwrap();
        let read = read_blender_product_source(&reopened, &scope(&f, &last)).unwrap();
        assert_eq!(read.project, last);
        assert_eq!(read.effective_recipe.annotation, f.request.annotation_body);
        assert_eq!(
            read.lineage.as_ref().unwrap().original_attached.revision,
            f.request.expected.revision + 1
        );
        assert_eq!(
            read.lineage.as_ref().unwrap().expected_after.revision,
            f.request.expected.revision + 2
        );
        assert!(read.source_cas_verified);
        assert!(!read.source_acceptance_inherited && !read.execution_grant_inherited);
        assert!(!read.native_dispatch_performed && !read.human_acceptance);
    }

    #[test]
    fn product_atomic_protocol_preserves_legacy_two_step_lineage_as_historical_only() {
        let f = fixture();
        let (_, completed) = synthetic_completion(&f);
        let legacy = make_intent(&f.request, &f.source).unwrap();
        let atomic = make_atomic_intent(&f.request, &f.source).unwrap();
        assert_eq!(legacy.schema, INTENT_SCHEMA);
        assert_eq!(atomic.schema, ATOMIC_INTENT_SCHEMA);
        validate_completed(&legacy, &completed.lineage).unwrap();
        assert!(validate_completed(&atomic, &completed.lineage).is_err());
        assert_eq!(intent_key(&legacy).unwrap(), intent_key(&atomic).unwrap());
        assert_ne!(
            intent_digest(&legacy).unwrap(),
            intent_digest(&atomic).unwrap()
        );
        let current_caller = json!({"schema": cycle::PRODUCT_CALLER_SCHEMA});
        require_atomic_completion_protocol(&atomic, &current_caller).unwrap();
        assert!(require_atomic_completion_protocol(&legacy, &current_caller).is_err());
        assert!(
            require_atomic_completion_protocol(
                &atomic,
                &json!({"schema": "motionwright.blender.product-cycle-caller/1"})
            )
            .is_err()
        );
        let mut unknown = atomic.clone();
        unknown.schema = "motionwright.blender.product-cycle-intent/3".into();
        assert!(validate_intent(&unknown).is_err());
        assert!(validate_completed(&unknown, &completed.lineage).is_err());
    }

    #[test]
    fn product_atomic_protocol_cannot_bypass_an_unresolved_legacy_source_attempt() {
        let f = fixture();
        let custody = ReceiptCustody::default();
        let _legacy = claim(&f, &custody);
        let project = f.service.project(f.source.project.id).unwrap();
        let watermark = f.service.production_receipt_watermark(project.id).unwrap();
        let receipts =
            serde_json::to_value(f.service.production_receipts(project.id, 64).unwrap()).unwrap();
        let mut request = f.request.clone();
        request.request_id = "new-id-cannot-retry-old-source".into();
        let atomic = make_atomic_intent(&request, &f.source).unwrap();
        assert!(reserve_intent(&f.service, &custody, atomic).is_err());
        assert_eq!(f.service.project(project.id).unwrap(), project);
        assert_eq!(
            f.service.production_receipt_watermark(project.id).unwrap(),
            watermark
        );
        assert_eq!(
            serde_json::to_value(f.service.production_receipts(project.id, 64).unwrap()).unwrap(),
            receipts
        );
    }
}
