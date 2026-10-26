//! Disposable CI-only Kdenlive return -> existing owner review -> SQLite reopen.
//! This example adds no public API and no renderer/process executor.
use motionwright_domain::{
    Asset, CanvasNode, Change, CoordinateSpace, Fidelity, NativeCapsuleSourceSpec, NodeStyle,
    OtioEditPreview, OtioNativeCut, OtioOwnerCutReview, Project, RationalTime, otio_interchange,
};
use motionwright_service::StudioService;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::error::Error;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const MAX_JSON: u64 = 2 * 1024 * 1024;
const ASSET_ID: u128 = 0x00fe_ed51;

fn require(condition: bool, reason: &str) -> Result<()> {
    if !condition {
        return Err(reason.into());
    }
    Ok(())
}
fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn lower_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn read_regular(path: &Path, limit: u64) -> Result<Vec<u8>> {
    // CLI callers already admitted this pathname through admit + owned_path.
    // Preserve it as received: the common retained-handle capture rejects leaf
    // and ancestor substitutions before returning bounded original bytes.
    // Capture itself issues no grant and permits a genuinely empty original,
    // matching this helper's prior contract. Callers retain their own SHA checks.
    let parent = path.parent().ok_or("Input has no parent")?;
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or("Input leaf is not a UTF-8 pathname")?;
    let captured = motionwright_native::production::capture_production_file(parent, name, limit)?;
    Ok(captured.into_parts().0)
}
fn new_json(path: &Path, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    require(
        bytes.len() as u64 <= MAX_JSON,
        "Evidence exceeds bounded JSON size",
    )?;
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    File::open(path.parent().ok_or("Evidence has no parent")?)?.sync_all()?;
    Ok(())
}
fn admit() -> Result<PathBuf> {
    for (key, value) in [
        ("GITHUB_ACTIONS", "true"),
        ("CI", "true"),
        ("RUNNER_OS", "Linux"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"),
        ("GITHUB_RUN_ATTEMPT", "1"),
        ("MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE", "1"),
    ] {
        require(
            std::env::var(key).as_deref() == Ok(value),
            "Owner-cut example requires the selected new disposable Linux CI attempt",
        )?;
    }
    require(
        cfg!(target_os = "linux"),
        "Owner-cut example requires Linux",
    )?;
    let root = PathBuf::from(std::env::var("RUNNER_TEMP")?);
    require(
        root.is_absolute() && root.is_dir() && root.canonicalize()? == root,
        "RUNNER_TEMP is not canonical",
    )?;
    Ok(root)
}
fn owned_path(root: &Path, value: &str, existing: bool) -> Result<PathBuf> {
    let path = PathBuf::from(value);
    let parent = path.parent().ok_or("Path has no parent")?;
    require(
        path.is_absolute()
            && path.starts_with(root)
            && path != root
            && parent.canonicalize()? == parent,
        "Path is outside the canonical disposable runner namespace",
    )?;
    if existing {
        let info = fs::symlink_metadata(&path)?;
        require(
            info.is_file() && !info.file_type().is_symlink(),
            "Existing input is not an ordinary file",
        )?;
    } else {
        require(
            fs::symlink_metadata(&path).is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
            "Output or original mutation intent already exists; never replay",
        )?;
    }
    Ok(path)
}
fn verify_asset(service: &StudioService, project: &Project) -> Result<String> {
    require(
        project.assets.len() == 1,
        "Closed fixture requires its single source asset",
    )?;
    let asset = &project.assets[0];
    let digest = asset
        .content_sha256
        .as_deref()
        .ok_or("Source asset has no digest")?;
    require(
        asset.id == Uuid::from_u128(ASSET_ID)
            && asset.media_type == "video/mp4"
            && lower_sha(digest),
        "Source asset binding changed",
    )?;
    let store = service.store();
    let path = store.lock().verified_blob_path(digest)?;
    require(
        sha(&read_regular(&path, 16 * 1024 * 1024)?) == digest,
        "Canonical source blob changed",
    )?;
    Ok(digest.to_owned())
}
fn seed(database: &Path, media: &Path, media_sha: &str) -> Result<Value> {
    require(
        lower_sha(media_sha) && sha(&read_regular(media, 16 * 1024 * 1024)?) == media_sha,
        "Original physical MP4 SHA-256 differs",
    )?;
    require(
        !database.exists(),
        "Original database already exists; never seed again",
    )?;
    let intent = database.with_extension("seed-intent.json");
    new_json(
        &intent,
        &json!({
            "schema":"motionwright.native.otio-owner-source-intent/1",
            "state":"INTENT", "source_media_sha256":media_sha,
            "automatic_retry":false, "automated_disposable_fixture":true
        }),
    )?;
    // Exclusive reservation happens before opening SQLite. An interrupted seed
    // remains evidence; the caller must never retry it against this namespace.
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(database)?
        .sync_all()?;
    let service = StudioService::open(database)?;
    service.ingest_blob_file(media)?;
    let mut project = service.create_project("Original source media editorial exchange")?;
    for (index, (name, objective)) in [
        (
            "Opening source",
            "Editorial entrance from authorized original video",
        ),
        (
            "Detail source",
            "Second source interval from the same video",
        ),
        (
            "Abstract branded CTA",
            "Native graphics with no inferred clip media",
        ),
    ]
    .into_iter()
    .enumerate()
    {
        let result = service.apply(
            project.id,
            &project.stamp(),
            &format!("original:source-scene:{index}"),
            &Change::AddScene {
                name: name.into(),
                objective: objective.into(),
                duration_seconds: 1,
            },
        )?;
        require(!result.replayed, "Fresh scene unexpectedly replayed")?;
        project = result.project;
    }
    let result = service.apply(
        project.id,
        &project.stamp(),
        "original:source-asset",
        &Change::AddAsset {
            asset: Asset {
                id: Uuid::from_u128(ASSET_ID),
                name: "First-party original H.264 test video".into(),
                media_type: "video/mp4".into(),
                content_sha256: Some(media_sha.into()),
                source_revision: Some("disposable-ci-original-asset-1".into()),
            },
        },
    )?;
    require(!result.replayed, "Fresh source asset unexpectedly replayed")?;
    project = result.project;
    // A real editable node makes source-only geometry/text preservation observable.
    let result = service.apply(
        project.id,
        &project.stamp(),
        "original:editable-source-node",
        &Change::AddCanvasNode {
            scene_id: project.scenes[2].id,
            node: CanvasNode {
                id: Uuid::from_u128(0x00fe_ed52),
                name: "Retained editable CTA".into(),
                kind: "text".into(),
                parent_id: None,
                x: 32.0,
                y: 48.0,
                width: 220.0,
                height: 40.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: Some("Original editable source survives the NLE cut".into()),
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 1,
                style: NodeStyle::default(),
                relations: vec![],
                property_locks: Default::default(),
                keyframes: vec![],
            },
        },
    )?;
    require(
        !result.replayed,
        "Fresh editable source unexpectedly replayed",
    )?;
    project = result.project;
    project.validate()?;
    verify_asset(&service, &project)?;
    drop(service);
    let reopened = StudioService::open(database)?;
    require(
        reopened.project(project.id)? == project,
        "Source database did not reopen exactly",
    )?;
    verify_asset(&reopened, &project)?;
    let original: Value = serde_json::from_str(&otio_interchange(&project)?.body)?;
    Ok(json!({
        "schema":"motionwright.real-otio-fixture/1", "project":project,
        "original_otio":original, "owner_media_asset_id":Uuid::from_u128(ASSET_ID),
        "expected_media_sha256":media_sha,
        "editorial_source_provenance":"REAL_FFMPEG_GENERATED_ORIGINAL",
        "creative_approval":"NOT_PERFORMED",
        "persistent_source_created_before_export":true,
        "source_database_reopened_equal":true,
        "human_acceptance":false, "release_authorized":false
    }))
}
fn review_for(
    source: &Project,
    preview: OtioEditPreview,
    raw: &Value,
    source_sha: &str,
) -> Result<OtioOwnerCutReview> {
    require(
        source.scenes.len() == 3
            && source
                .scenes
                .iter()
                .all(|s| s.duration == RationalTime::new(1, 1).expect("positive constant")),
        "Owner fixture requires its original three one-second scenes",
    )?;
    require(
        preview.source_project_sha256 == source_sha,
        "Owner preview does not bind the actual source bytes",
    )?;
    require(
        raw["schema"] == "motionwright.kdenlive-bound-cut-preview/1"
            && raw["profile"] == "kdenlive-bound-video-cut-v1"
            && raw["status"] == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
        "Missing original bounded Kdenlive preview",
    )?;
    let derived = serde_json::to_value(&preview)?;
    for field in [
        "source_project_sha256",
        "project_id",
        "generation",
        "expected_project_revision",
        "original_scene_order",
        "proposed_scene_order",
        "native_change_proposals",
        "media_refs_verified",
        "loss_report",
        "property_fidelity",
        "edit_imported",
        "source_media_relinked",
        "owner_revision_commit_required",
        "human_review_required",
        "semantic_native_roundtrip",
        "nle_receiver_acceptance",
        "publication_approved",
    ] {
        require(
            raw.get(field).is_some() && raw[field] == derived[field],
            "Original Kdenlive and owner-preview bindings differ",
        )?;
    }
    require(
        raw["original_missing_scene_ids"] == json!([])
            && raw["raw_files_rewritten"] == false
            && raw["native_final_frames_verified"] == false
            && raw["native_project_reopened"] == "NOT_VERIFIED"
            && raw["received_clip_count"] == 3
            && raw["edited_clip_count"] == 3
            && preview.media_refs_verified == 3
            && preview.missing_references_retained == 0,
        "Source-media or non-promoting Kdenlive flags changed",
    )?;
    let ids = source.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
    require(
        preview.proposed_scene_order == ids
            && preview.native_change_proposals
                == vec![OtioNativeCut::SetSceneDuration {
                    scene_id: ids[2],
                    duration: RationalTime::new(1, 2)?,
                }],
        "Only the exact returned third-scene tail trim is selected",
    )?;
    preview.bounded_native_changes(source)?;
    Ok(OtioOwnerCutReview {
        preview:Box::new(preview),
        reviewer_declaration:"Automated disposable CI fixture; no human acceptance declared".into(),
        rationale:"Apply the authenticated three-scene test return once through the existing owner contract; preserve every property loss and the original editable source".into(),
        acknowledges_property_losses:true,
        acknowledges_absolute_audio_marker_positions:true,
        confirms_single_revision_change:true,
    })
}
fn check_transition(source: &Project, final_project: &Project, change: &Change) -> Result<()> {
    require(
        final_project.id == source.id
            && final_project.generation == source.generation
            && final_project.revision
                == source.revision.checked_add(1).ok_or("Revision overflow")?,
        "Owner cut did not create exactly one revision of the same generation",
    )?;
    let mut expected = source.clone();
    expected.apply_change(change)?;
    expected.commit_revision(final_project.revision, change)?;
    expected.updated_at = final_project.updated_at;
    require(
        expected == *final_project,
        "Owner cut changed source semantics beyond its reviewed bounded edit",
    )?;
    Ok(())
}
fn commit(
    database: &Path,
    source_path: &Path,
    preview_path: &Path,
    raw_path: &Path,
    receipt_path: &Path,
) -> Result<Value> {
    let source_bytes = read_regular(source_path, MAX_JSON)?;
    let source: Project = serde_json::from_slice(&source_bytes)?;
    let preview_bytes = read_regular(preview_path, MAX_JSON)?;
    let raw_bytes = read_regular(raw_path, MAX_JSON)?;
    let preview: OtioEditPreview = serde_json::from_slice(&preview_bytes)?;
    let raw: Value = serde_json::from_slice(&raw_bytes)?;
    let review = review_for(&source, preview, &raw, &sha(&source_bytes))?;
    let change = Change::ApplyOtioOwnerCutReview {
        review: Box::new(review),
    };
    let service = StudioService::open(database)?;
    require(
        service.project(source.id)? == source,
        "Canonical source changed since export",
    )?;
    let asset_sha = verify_asset(&service, &source)?;
    // Validate the whole owner transaction without mutation before durable intent.
    let mut candidate = source.clone();
    candidate.apply_change(&change)?;
    let request_id = format!("original:kdenlive-cut:{}", sha(&raw_bytes));
    let intent_path = receipt_path.with_extension("intent.json");
    new_json(
        &intent_path,
        &json!({
            "schema":"motionwright.native.otio-owner-cut-intent/1",
            "state":"INTENT", "request_id":request_id, "source_stamp":source.stamp(),
            "source_project_sha256":sha(&source_bytes), "owner_preview_sha256":sha(&preview_bytes),
            "original_Kdenlive_preview_sha256":sha(&raw_bytes),
            "automatic_retry":false, "human_acceptance":false
        }),
    )?;
    // Exactly one call. An error/unknown terminal retains intent and never retries.
    let result = service.apply(source.id, &source.stamp(), &request_id, &change)?;
    require(
        !result.replayed
            && result.previous_revision == source.revision
            && result.request_id == request_id,
        "Fresh owner cut must be the original durable CAS result",
    )?;
    check_transition(&source, &result.project, &change)?;
    let history = service.history(source.id, source.revision, 2)?;
    require(
        history.len() == 1,
        "Owner cut did not leave exactly one history event",
    )?;
    require(
        history[0].revision == result.project.revision
            && history[0].change == change
            && history[0].created_at == result.project.updated_at.to_rfc3339(),
        "Original history event differs from the committed owner review",
    )?;
    require(
        verify_asset(&service, &result.project)? == asset_sha,
        "Original editable source asset was replaced",
    )?;
    let final_project = result.project;
    drop(service);
    let reopened = StudioService::open(database)?;
    require(
        reopened.project(source.id)? == final_project,
        "Committed SQLite source did not reopen",
    )?;
    verify_asset(&reopened, &final_project)?;
    require(
        read_regular(source_path, MAX_JSON)? == source_bytes
            && read_regular(preview_path, MAX_JSON)? == preview_bytes
            && read_regular(raw_path, MAX_JSON)? == raw_bytes,
        "Original source or preview bytes changed during owner commit",
    )?;
    let receipt = json!({
        "schema":"motionwright.native.otio-owner-cut-commit/1",
        "request_id":request_id, "source_project_sha256":sha(&source_bytes),
        "owner_preview_sha256":sha(&preview_bytes),
        "original_Kdenlive_preview_sha256":sha(&raw_bytes),
        "source_asset_sha256":asset_sha, "original_revision":source.revision,
        "committed_project":final_project, "committed_change":change, "history":history,
        "single_revision_committed":true, "source_semantics_preserved":true,
        "verified_source_blob_retained":true, "automatic_retry":false,
        "automated_disposable_fixture":true,
        "human_acceptance":false, "release_authorized":false
    });
    new_json(receipt_path, &receipt)?;
    Ok(receipt)
}
fn readback(database: &Path, source_path: &Path, receipt_path: &Path) -> Result<Value> {
    let source_bytes = read_regular(source_path, MAX_JSON)?;
    let source: Project = serde_json::from_slice(&source_bytes)?;
    let receipt_bytes = read_regular(receipt_path, MAX_JSON)?;
    let receipt: Value = serde_json::from_slice(&receipt_bytes)?;
    require(
        receipt["schema"] == "motionwright.native.otio-owner-cut-commit/1"
            && receipt["source_project_sha256"] == sha(&source_bytes)
            && receipt["single_revision_committed"] == true
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "Original owner commit receipt is not bound",
    )?;
    let expected: Project = serde_json::from_value(receipt["committed_project"].clone())?;
    let change: Change = serde_json::from_value(receipt["committed_change"].clone())?;
    let service = StudioService::open(database)?;
    let actual = service.project(source.id)?;
    require(
        actual == expected,
        "New process did not reopen the exact committed project",
    )?;
    check_transition(&source, &actual, &change)?;
    let history = service.history(source.id, source.revision, 2)?;
    require(
        history.len() == 1 && serde_json::to_value(&history)? == receipt["history"],
        "History changed or one cut produced multiple revisions",
    )?;
    let asset_sha = verify_asset(&service, &actual)?;
    require(
        receipt["source_asset_sha256"] == asset_sha,
        "Source blob was replaced after commit",
    )?;
    Ok(json!({
        "schema":"motionwright.native.otio-owner-cut-readback/1",
        "source_project_sha256":sha(&source_bytes), "commit_receipt_sha256":sha(&receipt_bytes),
        "project_id":actual.id, "generation":actual.generation, "revision":actual.revision,
        "committed_project_sha256":sha(&serde_json::to_vec(&actual)?),
        "project_reopened_equal":true, "exact_one_history_event":true,
        "source_semantics_preserved":true, "verified_source_blob_retained":true,
        "canonical_project_mutation_performed":false, "automatic_retry":false,
        "automated_disposable_fixture":true,
        "human_acceptance":false, "release_authorized":false
    }))
}

const NATIVE_SOURCE_LIMIT: u64 = 5 * 1024 * 1024;
const NATIVE_CAPSULE_ID: u128 = 0x00fe_ed52;
const NATIVE_SOURCE_NAME: &str = "edited.kdenlive";
const NATIVE_SOURCE_MEDIA_TYPE: &str = "application/x-kdenlive";

fn native_source_spec(before: &Project) -> Result<NativeCapsuleSourceSpec> {
    require(
        before.scenes.len() == 3 && before.production_design.capsules.is_empty(),
        "Closed source attachment requires the original three-scene fixture without a capsule",
    )?;
    Ok(NativeCapsuleSourceSpec {
        name: NATIVE_SOURCE_NAME.into(),
        media_type: NATIVE_SOURCE_MEDIA_TYPE.into(),
        capsule_id: Uuid::from_u128(NATIVE_CAPSULE_ID),
        scene_id: before.scenes[0].id,
        label: "Opaque whole-cut Kdenlive source; no per-scene semantic mapping".into(),
        editable_parameters: vec![],
        native_editor_hint: "Kdenlive".into(),
    })
}

fn verify_native_capsule_bytes(service: &StudioService, project: &Project) -> Result<String> {
    require(
        project.assets.len() == 2 && project.production_design.capsules.len() == 1,
        "Source attachment changed the closed asset or capsule inventory",
    )?;
    let capsule = &project.production_design.capsules[0];
    require(
        capsule.id == Uuid::from_u128(NATIVE_CAPSULE_ID)
            && capsule.editable_parameters.is_empty()
            && capsule.fidelity.visual == Fidelity::Unavailable
            && capsule.fidelity.temporal == Fidelity::Unavailable
            && capsule.fidelity.structural == Fidelity::Unavailable
            && capsule.fidelity.editable == Fidelity::Unavailable
            && capsule.property_fidelity.is_empty(),
        "Opaque source attachment gained fidelity or editable parameter claims",
    )?;
    let source_asset = project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .ok_or("Native source asset is missing")?;
    require(
        source_asset.name == NATIVE_SOURCE_NAME
            && source_asset.media_type == NATIVE_SOURCE_MEDIA_TYPE
            && source_asset.source_revision.as_deref() == Some("native-capsule-source-import")
            && source_asset.content_sha256.as_deref() == Some(capsule.source_sha256.as_str()),
        "Native capsule no longer binds its exact imported source asset",
    )?;
    let store = service.store();
    let original = store
        .lock()
        .read_blob(&capsule.source_sha256, NATIVE_SOURCE_LIMIT)?;
    require(
        !original.is_empty() && sha(&original) == capsule.source_sha256,
        "Native source CAS bytes differ from the capsule",
    )?;
    Ok(capsule.source_sha256.clone())
}

fn check_source_attachment(before: &Project, after: &Project, source_sha: &str) -> Result<Change> {
    require(
        lower_sha(source_sha)
            && after.id == before.id
            && after.generation == before.generation
            && after.revision == before.revision.checked_add(1).ok_or("Revision overflow")?
            && after.assets.len() == before.assets.len() + 1
            && after.assets[..before.assets.len()] == before.assets[..],
        "Native source import must add one source asset in one current revision",
    )?;
    let asset = after
        .assets
        .last()
        .ok_or("Imported source asset is missing")?
        .clone();
    require(
        asset.content_sha256.as_deref() == Some(source_sha),
        "Imported source differs from the previously observed native source",
    )?;
    let capsule = native_source_spec(before)?.bind_asset(&asset)?;
    let change = Change::ImportNativeCapsuleSource { asset, capsule };
    let mut expected = before.clone();
    expected.apply_change(&change)?;
    expected.commit_revision(after.revision, &change)?;
    // Store commits observe their own timestamp. Normalize only that observed
    // field; the original history timestamp is checked separately below.
    expected.updated_at = after.updated_at;
    require(
        *after == expected,
        "Source attachment changed other original project data",
    )?;
    Ok(change)
}

fn source_cut_scope(
    source_path: &Path,
    cut_receipt_path: &Path,
) -> Result<(Project, Project, String, String)> {
    let source_bytes = read_regular(source_path, MAX_JSON)?;
    let source: Project = serde_json::from_slice(&source_bytes)?;
    let cut_bytes = read_regular(cut_receipt_path, MAX_JSON)?;
    let cut: Value = serde_json::from_slice(&cut_bytes)?;
    require(
        cut["schema"] == "motionwright.native.otio-owner-cut-commit/1"
            && cut["source_project_sha256"] == sha(&source_bytes)
            && cut["single_revision_committed"] == true
            && cut["human_acceptance"] == false
            && cut["release_authorized"] == false,
        "Original cut receipt is not bound to the original source",
    )?;
    let before: Project = serde_json::from_value(cut["committed_project"].clone())?;
    let change: Change = serde_json::from_value(cut["committed_change"].clone())?;
    check_transition(&source, &before, &change)?;
    Ok((source, before, sha(&source_bytes), sha(&cut_bytes)))
}

fn import_observed_native_capsule_source(
    service: &StudioService,
    before: &Project,
    request_id: &str,
    native_path: &Path,
    observed_sha: &str,
    observed_bytes: &[u8],
    spec: NativeCapsuleSourceSpec,
    returned_path: &Path,
) -> Result<motionwright_storage::ApplyOutcome> {
    require(
        !observed_bytes.is_empty()
            && lower_sha(observed_sha)
            && sha(observed_bytes) == observed_sha,
        "Native source observation does not identify its original bytes",
    )?;
    let expected = motionwright_storage::BlobDescriptor {
        sha256: observed_sha.into(),
        size_bytes: observed_bytes.len() as u64,
    };
    // This is the same Service importer. The descriptor binds the staged
    // capture before publication/apply; a later source hash is not admission.
    let returned = match service.import_file_verified(
        before.id,
        &before.stamp(),
        request_id,
        native_path,
        &expected,
        motionwright_storage::FileImportIntent::NativeCapsuleSource { spec },
    ) {
        Ok(value) => value,
        Err(error) => {
            // A failed diagnostic write must not replace the original Service
            // error or make a failed/unknown attempt eligible for another call.
            let _ = new_json(
                returned_path,
                &json!({
                    "schema":"motionwright.native.kdenlive-source-import-error/1",
                    "request_id":request_id,"original_service_error":error.to_string(),
                    "rendering_is_not_wire_bytes":true,"effects_absent_claimed":false,
                    "automatic_retry":false,
                }),
            );
            return Err(Box::new(error));
        }
    };
    // Preserve the complete original typed ApplyOutcome before qualification.
    new_json(returned_path, &serde_json::to_value(&returned)?)?;
    Ok(returned)
}

fn attach_native_source(
    database: &Path,
    source_path: &Path,
    cut_receipt_path: &Path,
    native_path: &Path,
    native_sha: &str,
    receipt_path: &Path,
) -> Result<Value> {
    let native_bytes = read_regular(native_path, NATIVE_SOURCE_LIMIT)?;
    require(
        native_path.file_name().and_then(|value| value.to_str()) == Some(NATIVE_SOURCE_NAME)
            && !native_bytes.is_empty()
            && lower_sha(native_sha)
            && sha(&native_bytes) == native_sha,
        "The exact retained Kdenlive source is required before import",
    )?;
    // This existing readback must succeed before the independent source import.
    // The original cut and its one-revision receipt remain unchanged.
    readback(database, source_path, cut_receipt_path)?;
    let (source, before, source_sha, cut_sha) = source_cut_scope(source_path, cut_receipt_path)?;
    let service = StudioService::open(database)?;
    require(
        service.project(before.id)? == before,
        "Project changed after the owner-cut readback",
    )?;
    let spec = native_source_spec(&before)?;
    let request_id = format!("original:kdenlive-capsule:{native_sha}");
    let intent_path = receipt_path.with_extension("intent.json");
    new_json(
        &intent_path,
        &json!({
            "schema":"motionwright.native.kdenlive-source-import-intent/1",
            "state":"INTENT", "request_id":request_id, "expected":before.stamp(),
            "source_project_sha256":source_sha, "owner_cut_receipt_sha256":cut_sha,
            "native_source_sha256":native_sha, "native_source_bytes":native_bytes.len(),
            "spec":spec, "automatic_retry":false, "native_editor_dispatched_here":false,
        }),
    )?;
    let imported = import_observed_native_capsule_source(
        &service,
        &before,
        &request_id,
        native_path,
        native_sha,
        &native_bytes,
        spec.clone(),
        &receipt_path.with_extension("return.original.json"),
    )?;
    require(
        !imported.replayed
            && imported.previous_revision == before.revision
            && imported.request_id == request_id,
        "Source import did not produce its original current-revision result",
    )?;
    let change = check_source_attachment(&before, &imported.project, native_sha)?;
    let history = service.history(source.id, source.revision, 3)?;
    require(
        history.len() == 2,
        "Cut and source attachment did not remain two revisions",
    )?;
    let owner_event = history
        .iter()
        .find(|event| event.revision == before.revision)
        .ok_or("Original owner-cut history event is missing")?;
    let source_event = history
        .iter()
        .find(|event| event.revision == imported.project.revision)
        .ok_or("Original source-import history event is missing")?;
    require(
        owner_event.created_at == before.updated_at.to_rfc3339()
            && source_event.change == change
            && source_event.created_at == imported.project.updated_at.to_rfc3339(),
        "Original cut/source history timestamps or source change differ",
    )?;
    require(
        verify_asset(&service, &before)?
            == before.assets[0]
                .content_sha256
                .as_deref()
                .ok_or("Source asset lost SHA")?
            && verify_native_capsule_bytes(&service, &imported.project)? == native_sha
            && read_regular(native_path, NATIVE_SOURCE_LIMIT)? == native_bytes,
        "Original video or native source changed during attachment",
    )?;
    let final_project = imported.project;
    drop(service);
    let reopened = StudioService::open(database)?;
    require(
        reopened.project(final_project.id)? == final_project
            && verify_native_capsule_bytes(&reopened, &final_project)? == native_sha,
        "Attached source did not survive SQLite reopen",
    )?;
    let receipt = json!({
        "schema":"motionwright.native.kdenlive-source-import/1",
        "request_id":request_id, "source_project_sha256":source_sha,
        "owner_cut_receipt_sha256":cut_sha, "native_source_sha256":native_sha,
        "original_owner_cut_revision":before.revision,
        "committed_project":final_project, "committed_change":change, "history":history,
        "committed_project_sha256":sha(&serde_json::to_vec(&final_project)?),
        "single_source_revision_committed":true, "source_fidelity_unavailable":true,
        "source_blob_reopened_equal":true, "native_editor_dispatched_here":false,
        "per_scene_native_semantic_mapping_verified":false,
        "automatic_retry":false, "human_acceptance":false, "release_authorized":false,
    });
    new_json(receipt_path, &receipt)?;
    Ok(receipt)
}

fn checked_source_attachment(
    database: &Path,
    source_path: &Path,
    cut_receipt_path: &Path,
    attachment_receipt_path: &Path,
) -> Result<(StudioService, Project, String, String)> {
    let (source, before, source_sha, cut_sha) = source_cut_scope(source_path, cut_receipt_path)?;
    let receipt_bytes = read_regular(attachment_receipt_path, MAX_JSON)?;
    let receipt: Value = serde_json::from_slice(&receipt_bytes)?;
    let native_sha = receipt["native_source_sha256"]
        .as_str()
        .ok_or("Native source SHA is missing")?
        .to_owned();
    require(
        receipt["schema"] == "motionwright.native.kdenlive-source-import/1"
            && receipt["source_project_sha256"] == source_sha
            && receipt["owner_cut_receipt_sha256"] == cut_sha
            && receipt["request_id"] == format!("original:kdenlive-capsule:{native_sha}")
            && receipt["single_source_revision_committed"] == true
            && receipt["source_fidelity_unavailable"] == true
            && receipt["native_editor_dispatched_here"] == false
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "Original source-import receipt is not bound",
    )?;
    let expected: Project = serde_json::from_value(receipt["committed_project"].clone())?;
    let change = check_source_attachment(&before, &expected, &native_sha)?;
    require(
        receipt["committed_change"] == serde_json::to_value(&change)?
            && receipt["committed_project_sha256"] == sha(&serde_json::to_vec(&expected)?),
        "Source-import receipt changed its original result",
    )?;
    let service = StudioService::open(database)?;
    require(
        service.project(expected.id)? == expected,
        "Attached current project differs",
    )?;
    let history = service.history(source.id, source.revision, 3)?;
    require(
        history.len() == 2 && serde_json::to_value(&history)? == receipt["history"],
        "Cut/source history changed or contains an extra revision",
    )?;
    let owner_event = history
        .iter()
        .find(|event| event.revision == before.revision)
        .ok_or("Reopened owner-cut history event is missing")?;
    let source_event = history
        .iter()
        .find(|event| event.revision == expected.revision)
        .ok_or("Reopened source-import history event is missing")?;
    require(
        owner_event.created_at == before.updated_at.to_rfc3339()
            && source_event.change == change
            && source_event.created_at == expected.updated_at.to_rfc3339(),
        "Reopened original history timestamps or source change differ",
    )?;
    verify_asset(&service, &before)?;
    require(
        verify_native_capsule_bytes(&service, &expected)? == native_sha,
        "Reopened source asset is not the original native file",
    )?;
    Ok((service, expected, native_sha, sha(&receipt_bytes)))
}

fn native_source_readback(
    database: &Path,
    source_path: &Path,
    cut_receipt_path: &Path,
    attachment_receipt_path: &Path,
) -> Result<Value> {
    let (_service, project, native_sha, receipt_sha) = checked_source_attachment(
        database,
        source_path,
        cut_receipt_path,
        attachment_receipt_path,
    )?;
    Ok(json!({
        "schema":"motionwright.native.kdenlive-source-readback/1",
        "attachment_receipt_sha256":receipt_sha, "native_source_sha256":native_sha,
        "project_id":project.id, "generation":project.generation, "revision":project.revision,
        "committed_project_sha256":sha(&serde_json::to_vec(&project)?),
        "project_reopened_equal":true, "source_blob_reopened_equal":true,
        "source_fidelity_unavailable":true, "exact_two_history_events":true,
        "canonical_project_mutation_performed":false, "native_editor_dispatched_here":false,
        "automatic_retry":false, "human_acceptance":false, "release_authorized":false,
    }))
}

fn check_portable_source_project(source: &Project, imported: &Project) -> Result<()> {
    require(
        imported.generation != source.generation && !imported.generation.is_nil(),
        "Portable source import reused its original generation",
    )?;
    let mut expected = source.clone();
    expected.generation = imported.generation;
    // Existing import_captured_project also observes a destination updated_at.
    // No authored field or created_at is normalized.
    expected.updated_at = imported.updated_at;
    require(
        *imported == expected,
        "Portable source import changed authored data beyond its fresh generation and observed timestamp",
    )?;
    Ok(())
}

fn verify_all_source_assets(service: &StudioService, project: &Project) -> Result<String> {
    let native_sha = verify_native_capsule_bytes(service, project)?;
    let video = project
        .assets
        .iter()
        .find(|asset| asset.id == Uuid::from_u128(ASSET_ID))
        .ok_or("Portable source lost its original video asset")?;
    let video_sha = video
        .content_sha256
        .as_deref()
        .ok_or("Original video asset lost its digest")?;
    require(
        video.media_type == "video/mp4" && lower_sha(video_sha),
        "Original video asset was replaced or retyped",
    )?;
    let store = service.store();
    let original_video = store.lock().read_blob(video_sha, 16 * 1024 * 1024)?;
    require(
        sha(&original_video) == video_sha,
        "Original video CAS bytes were not retained",
    )?;
    Ok(native_sha)
}

fn portable_native_source(
    database: &Path,
    source_path: &Path,
    cut_receipt_path: &Path,
    attachment_receipt_path: &Path,
    bundle: &Path,
    destination: &Path,
    receipt_path: &Path,
) -> Result<Value> {
    let (service, source, native_sha, attachment_sha) = checked_source_attachment(
        database,
        source_path,
        cut_receipt_path,
        attachment_receipt_path,
    )?;
    require(
        !bundle.exists()
            && !destination.exists()
            && destination.parent().is_some()
            && destination.parent() != database.parent(),
        "Portable outputs already exist; never resume or replay this attempt",
    )?;
    let expected = source.stamp();
    new_json(
        &receipt_path.with_extension("intent.json"),
        &json!({
            "schema":"motionwright.native.kdenlive-source-portability-intent/1",
            "state":"INTENT", "expected":expected, "attachment_receipt_sha256":attachment_sha,
            "native_source_sha256":native_sha, "bundle":bundle, "destination_database":destination,
            "automatic_retry":false, "execution_authority_issued":false,
        }),
    )?;
    // The existing exporter and the source-bound fingerprint APIs own all
    // serialization, asset admission, CAS publication and generation changes.
    let exported = service.export_project_bundle_at(source.id, &expected, bundle)?;
    let manifest_path = bundle.join("manifest.json");
    let manifest_bytes = read_regular(&manifest_path, MAX_JSON)?;
    let manifest_sha = sha(&manifest_bytes);
    new_json(
        &receipt_path.with_extension("export.original.json"),
        &json!({
            "schema":"motionwright.native.kdenlive-source-bundle-export-original/1",
            "returned_manifest":exported, "original_manifest_sha256":manifest_sha,
            "original_manifest_bytes":manifest_bytes.len(),
        }),
    )?;
    let inspected = service.inspect_project_bundle_with_fingerprint(bundle)?;
    new_json(
        &receipt_path.with_extension("inspection.original.json"),
        &serde_json::to_value(&inspected)?,
    )?;
    require(
        read_regular(&manifest_path, MAX_JSON)? == manifest_bytes,
        "Portable manifest changed after inspection",
    )?;
    new_json(
        &receipt_path.with_extension("import.intent.json"),
        &json!({
            "schema":"motionwright.native.kdenlive-source-bundle-import-intent/1",
            "state":"INTENT", "source_generation":source.generation,
            "inspection_fingerprint":inspected.fingerprint,
            "original_manifest_sha256":manifest_sha, "automatic_retry":false,
        }),
    )?;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?
        .sync_all()?;
    let restored = StudioService::open(destination)?;
    let imported = restored.import_project_bundle_inspected(bundle, &inspected.fingerprint)?;
    // Retain the original typed result before any qualification of that result.
    new_json(
        &receipt_path.with_extension("import.original.json"),
        &serde_json::to_value(&imported)?,
    )?;
    check_portable_source_project(&source, &imported)?;
    require(
        verify_all_source_assets(&restored, &imported)? == native_sha,
        "Portable source import changed the native source blob",
    )?;
    let watermark = restored.production_receipt_watermark(imported.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "Portable source import inherited local production receipts",
    )?;
    require(
        service.project(source.id)? == source
            && read_regular(&manifest_path, MAX_JSON)? == manifest_bytes
            && sha(&read_regular(attachment_receipt_path, MAX_JSON)?) == attachment_sha,
        "Original source or portable manifest changed during source import",
    )?;
    drop(restored);
    let reopened = StudioService::open(destination)?;
    require(
        reopened.project(imported.id)? == imported
            && verify_all_source_assets(&reopened, &imported)? == native_sha,
        "Portable source did not survive destination SQLite reopen",
    )?;
    let receipt = json!({
        "schema":"motionwright.native.kdenlive-source-portability/1",
        "attachment_receipt_sha256":attachment_sha, "native_source_sha256":native_sha,
        "source_project":source, "imported_project":imported,
        "source_updated_at":source.updated_at, "imported_updated_at":imported.updated_at,
        "project_id":imported.id, "source_generation":source.generation,
        "imported_generation":imported.generation, "revision":imported.revision,
        "imported_project_sha256":sha(&serde_json::to_vec(&imported)?),
        "bundle_manifest_sha256":manifest_sha,
        "inspection_fingerprint":inspected.fingerprint,
        "fresh_generation_observed":true, "source_assets_and_capsules_preserved":true,
        "source_blob_reopened_equal":true, "source_fidelity_unavailable":true,
        "production_receipt_count":watermark.count, "local_production_receipts_inherited":false,
        "execution_authority_issued":false, "native_editor_dispatched_here":false,
        "automatic_retry":false, "human_acceptance":false, "release_authorized":false,
    });
    new_json(receipt_path, &receipt)?;
    Ok(receipt)
}

fn portable_native_source_readback(destination: &Path, receipt_path: &Path) -> Result<Value> {
    let receipt_bytes = read_regular(receipt_path, MAX_JSON)?;
    let receipt: Value = serde_json::from_slice(&receipt_bytes)?;
    require(
        receipt["schema"] == "motionwright.native.kdenlive-source-portability/1"
            && receipt["fresh_generation_observed"] == true
            && receipt["source_fidelity_unavailable"] == true
            && receipt["local_production_receipts_inherited"] == false
            && receipt["execution_authority_issued"] == false
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "Original portable source receipt is not admitted",
    )?;
    let source: Project = serde_json::from_value(receipt["source_project"].clone())?;
    let expected: Project = serde_json::from_value(receipt["imported_project"].clone())?;
    check_portable_source_project(&source, &expected)?;
    require(
        receipt["source_generation"] == serde_json::to_value(source.generation)?
            && receipt["imported_generation"] == serde_json::to_value(expected.generation)?
            && receipt["source_updated_at"] == serde_json::to_value(source.updated_at)?
            && receipt["imported_updated_at"] == serde_json::to_value(expected.updated_at)?
            && receipt["imported_project_sha256"] == sha(&serde_json::to_vec(&expected)?),
        "Portable receipt changed its original project identity",
    )?;
    let service = StudioService::open(destination)?;
    let actual = service.project(expected.id)?;
    require(
        actual == expected,
        "New process did not reopen the exact portable source project",
    )?;
    let native_sha = verify_all_source_assets(&service, &actual)?;
    require(
        receipt["native_source_sha256"] == native_sha,
        "Portable native source no longer matches its original digest",
    )?;
    let watermark = service.production_receipt_watermark(actual.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "Destination acquired production receipts after the portable import",
    )?;
    Ok(json!({
        "schema":"motionwright.native.kdenlive-source-portable-readback/1",
        "bundle_receipt_sha256":sha(&receipt_bytes), "native_source_sha256":native_sha,
        "project_id":actual.id, "generation":actual.generation, "revision":actual.revision,
        "imported_project_sha256":sha(&serde_json::to_vec(&actual)?),
        "project_reopened_equal":true, "source_blob_reopened_equal":true,
        "fresh_generation_observed":true, "source_fidelity_unavailable":true,
        "production_receipt_count":watermark.count, "local_production_receipts_inherited":false,
        "canonical_project_mutation_performed":false, "execution_authority_issued":false,
        "native_editor_dispatched_here":false, "automatic_retry":false,
        "human_acceptance":false, "release_authorized":false,
    }))
}

const ALAB_PROFILE: &str = "alab-two-retained-mov-24-v1";
const ALAB_OTIO_NAME: &str = "Original ALab selected-source OTIO";
const ALAB_OTIO_MEDIA_TYPE: &str = "application/vnd.opentimelineio+json";
const ALAB_CAPSULE_ID: u128 = 0x00fe_ed61;
const ALAB_POINTERS: [&str; 2] = [
    "/tracks/children/0/children/0",
    "/tracks/children/0/children/1",
];
const ALAB_TARGETS: [&str; 2] = [
    "ALab_h264_MOVs/mk020_0010.mov",
    "ALab_h264_MOVs/mk020_0020.mov",
];
const ALAB_SOURCE_NAMES: [&str; 2] = ["mk020_0010.mov", "mk020_0020.mov"];
const ALAB_FRAMES: [u64; 2] = [52, 182];
const ALAB_ORIGINS: [u64; 2] = [1002, 1008];

struct AlabExpected {
    originals: [motionwright_storage::BlobDescriptor; 4],
    media: [motionwright_storage::BlobDescriptor; 2],
}

fn alab_blob(sha256: &str, size_bytes: u64) -> motionwright_storage::BlobDescriptor {
    motionwright_storage::BlobDescriptor {
        sha256: sha256.into(),
        size_bytes,
    }
}

fn alab_expected() -> AlabExpected {
    AlabExpected {
        originals: [
            alab_blob(
                "6c90cbc64000e5de1d6450c95b1f5845db2862c053fa2591d02d370505f3bd28",
                218949,
            ),
            alab_blob(
                "2c5739cb39dc7233b955428450939585f68b743cb418d0d2afb1c12f79023c24",
                107354,
            ),
            alab_blob(
                "390c157862ede63b25583502eaac6b3603e4ed9632630267315d2b68bc6d8218",
                1301,
            ),
            alab_blob(
                "52a988b9db2d9d771cbf42e2b24f026b772651a9936459b3859a0f7940baf21a",
                1304,
            ),
        ],
        media: [
            alab_blob(
                "86c40f8271339a4bab948d11ddbdd172588816df5ff52d056bd4e54378069e1a",
                7627921,
            ),
            alab_blob(
                "08aac3e29ea980e80993aa6baaef1b83b4b0598c3ae95685434886a1c326b2f6",
                20227644,
            ),
        ],
    }
}

struct AlabFiles<'a> {
    originals: [&'a Path; 4],
    media: [&'a Path; 2],
    derivation: &'a Path,
}

fn alab_check_descriptor(
    path: &Path,
    expected: &motionwright_storage::BlobDescriptor,
    maximum: u64,
) -> Result<()> {
    require(
        expected.size_bytes > 0 && expected.size_bytes <= maximum && lower_sha(&expected.sha256),
        "ALab input descriptor exceeds its closed profile",
    )?;
    let bytes = read_regular(path, expected.size_bytes)?;
    require(
        bytes.len() as u64 == expected.size_bytes && sha(&bytes) == expected.sha256,
        "ALab original bytes differ from the independently admitted descriptor",
    )
}

fn alab_check_files(files: &AlabFiles<'_>, expected: &AlabExpected) -> Result<()> {
    for (path, descriptor) in files.originals.iter().zip(&expected.originals) {
        alab_check_descriptor(path, descriptor, MAX_JSON)?;
    }
    for (path, descriptor) in files.media.iter().zip(&expected.media) {
        require(
            path.extension().and_then(|value| value.to_str()) == Some("mov"),
            "ALab selected originals must retain the MOV container extension",
        )?;
        alab_check_descriptor(path, descriptor, 256 * 1024 * 1024)?;
    }
    Ok(())
}

fn alab_original_descriptors(expected: &AlabExpected) -> Value {
    Value::Array(["otio", "readback", "container0", "container1"].into_iter()
        .zip(&expected.originals)
        .map(|(role, descriptor)| json!({"role":role,"bytes":descriptor.size_bytes,"sha256":descriptor.sha256}))
        .collect())
}

fn alab_check_derivation(value: &Value, expected: &AlabExpected) -> Result<()> {
    let fields = [
        "schema",
        "profile",
        "status",
        "originals",
        "source_inspection",
        "selected",
        "source_origin_basis",
        "historical_source_origin",
        "historical_readback_rewritten",
        "new_decode",
        "new_render",
        "assets_adopted",
        "canonical_scene_ids_created",
        "nle_opened",
        "formal_acceptance",
        "remaining_source_references",
        "later_edit_proposal",
    ];
    require(
        value.as_object().is_some_and(|object| {
            object.len() == fields.len() && fields.iter().all(|key| object.contains_key(*key))
        }),
        "ALab derived input has missing or undeclared fields",
    )?;
    require(
        value["schema"] == "motionwright.alab-retained-original-cut/1"
            && value["profile"] == ALAB_PROFILE
            && value["status"] == "RETAINED_MEDIA_ORIGIN_DERIVED_NOT_ADOPTED"
            && value["originals"] == alab_original_descriptors(expected)
            && value["historical_source_origin"] == "NOT_OBSERVED"
            && value["remaining_source_references"]
                == "opaque/unavailable for this selected two-occurrence profile"
            && value["source_origin_basis"]
                == "matching original OTIO range, retained complete video PTS and matching format/tmcd 24fps non-drop-frame timecodes"
            && [
                "historical_readback_rewritten",
                "new_decode",
                "new_render",
                "assets_adopted",
                "canonical_scene_ids_created",
                "nle_opened",
                "formal_acceptance",
            ]
            .iter()
            .all(|key| value[*key] == false),
        "ALab derivation changed identity, profile or an unobserved claim",
    )?;
    let inspection = &value["source_inspection"];
    require(
        inspection["schema"] == "motionwright.external-otio-source-inspection/1"
            && inspection["status"] == "SOURCE_INSPECTED_NOT_ADOPTED"
            && inspection["source"]["sha256"] == expected.originals[0].sha256
            && inspection["source"]["bytes"].as_u64() == Some(expected.originals[0].size_bytes)
            && inspection["verified_media"] == false
            && inspection["media_origin"] == "unknown"
            && inspection["observations"]
                .as_array()
                .is_some_and(|rows| rows.len() == 2)
            && inspection["opaque_regions"]
                .as_array()
                .is_some_and(|rows| !rows.is_empty() && rows.len() <= 1024),
        "ALab original source inspection is missing or was promoted",
    )?;
    let selected = value["selected"]
        .as_array()
        .ok_or("ALab selected source roster is missing")?;
    require(
        selected.len() == 2,
        "ALab requires exactly its two selected original occurrences",
    )?;
    for (index, row) in selected.iter().enumerate() {
        require(
            row == &json!({
                "pointer":ALAB_POINTERS[index],"target_url":ALAB_TARGETS[index],
                "source_sha256":expected.media[index].sha256,"source_bytes":expected.media[index].size_bytes,
                "media_type":"video/quicktime","source_fps":24,"width":1920,"height":1080,
                "codec":"h264","source_origin_frame":ALAB_ORIGINS[index],"source_start_frame":0,
                "duration_frames":ALAB_FRAMES[index],"actual_media_frames":ALAB_FRAMES[index],
                "container_timecode":if index == 0 { "00:00:41:18" } else { "00:00:42:00" },
                "rgb_samples_retained":3,"all_decoded_frame_pixel_hashes":false,
            }),
            "ALab selected source interval or retained observation differs",
        )?;
        let observed = &inspection["observations"][index];
        require(
            observed["pointer"] == ALAB_POINTERS[index]
                && observed["schema"] == "Clip.2"
                && observed["track_kind"] == "Video"
                && observed["range_supported_by_existing_cut_parser"] == true
                && observed["active_reference"]["target_url"] == ALAB_TARGETS[index],
            "ALab derived occurrence no longer addresses the original Clip.2",
        )?;
    }
    require(
        value["later_edit_proposal"]
            == json!({"occurrence":ALAB_POINTERS[1],
        "from_frames":182,"to_frames":170,"applied":false}),
        "ALab source adoption cannot claim that its later edit was executed",
    )
}

fn alab_capsule_spec(project: &Project) -> Result<NativeCapsuleSourceSpec> {
    require(
        project.scenes.len() == 2,
        "ALab source capsule needs its two existing canonical scenes",
    )?;
    Ok(NativeCapsuleSourceSpec {
        name: ALAB_OTIO_NAME.into(),
        media_type: ALAB_OTIO_MEDIA_TYPE.into(),
        capsule_id: Uuid::from_u128(ALAB_CAPSULE_ID),
        scene_id: project.scenes[0].id,
        label: "Whole original ALab OTIO; only two selected video occurrences adopted".into(),
        editable_parameters: vec![],
        native_editor_hint: "OpenTimelineIO".into(),
    })
}

fn alab_check_source_project(project: &Project) -> Result<()> {
    project.validate()?;
    require(
        project.scenes.len() == 2
            && project.scenes[0].name == "mk020_0010"
            && project.scenes[1].name == "mk020_0020"
            && project.scenes[0].start == RationalTime::new(0, 1)?
            && project.scenes[0].duration == RationalTime::new(13, 6)?
            && project.scenes[1].start == RationalTime::new(13, 6)?
            && project.scenes[1].duration == RationalTime::new(91, 12)?,
        "ALab canonical source cut differs from the selected 52/182-frame intervals",
    )
}

fn alab_verify_assets(
    service: &StudioService,
    project: &Project,
    expected: &AlabExpected,
) -> Result<Value> {
    project.validate()?;
    require(
        project.scenes.len() == 2
            && project.assets.len() == 3
            && project.production_design.capsules.len() == 1,
        "ALab source lost or added an asset/capsule",
    )?;
    let mut media = Vec::new();
    for (index, descriptor) in expected.media.iter().enumerate() {
        let matches = project
            .assets
            .iter()
            .filter(|asset| asset.content_sha256.as_deref() == Some(descriptor.sha256.as_str()))
            .collect::<Vec<_>>();
        require(
            matches.len() == 1,
            "ALab source asset identity is missing or ambiguous",
        )?;
        let asset = matches[0];
        require(
            !asset.id.is_nil()
                && asset.name == ALAB_SOURCE_NAMES[index]
                && asset.media_type == "video/quicktime",
            "ALab source asset was renamed or retyped",
        )?;
        let bytes = service
            .store()
            .lock()
            .read_blob(&descriptor.sha256, descriptor.size_bytes)?;
        require(
            bytes.len() as u64 == descriptor.size_bytes && sha(&bytes) == descriptor.sha256,
            "ALab source MOV CAS bytes changed",
        )?;
        media.push(
            json!({"asset_id":asset.id,"scene_id":project.scenes[index].id,
            "sha256":descriptor.sha256,"bytes":descriptor.size_bytes,
            "media_type":asset.media_type,"name":asset.name}),
        );
    }
    let capsule = &project.production_design.capsules[0];
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .ok_or("ALab original OTIO source asset is missing")?;
    require(
        asset.content_sha256.as_deref() == Some(expected.originals[0].sha256.as_str())
            && alab_capsule_spec(project)?.bind_asset(asset)? == *capsule,
        "ALab source capsule changed identity, editable claims or Unavailable fidelity",
    )?;
    let bytes = service.store().lock().read_blob(
        &expected.originals[0].sha256,
        expected.originals[0].size_bytes,
    )?;
    require(
        bytes.len() as u64 == expected.originals[0].size_bytes
            && sha(&bytes) == expected.originals[0].sha256,
        "Whole original ALab OTIO bytes did not survive CAS",
    )?;
    Ok(
        json!({"media":media,"source_asset_id":asset.id,"capsule_id":capsule.id,
        "source_sha256":capsule.source_sha256,"source_bytes":bytes.len(),
        "all_source_blobs_reopened_equal":true,"source_fidelity_unavailable":true,
        "original_unselected_references_retained_in_opaque_source":true,
        "per_scene_native_editability_verified":false}),
    )
}

fn alab_stage<F>(
    service: &StudioService,
    before: &Project,
    request_id: &str,
    receipt: &Path,
    index: usize,
    stage_receipts: &mut Vec<Value>,
    apply_once: F,
) -> Result<Project>
where
    F: FnOnce() -> Result<motionwright_storage::ApplyOutcome>,
{
    let intent = receipt.with_extension(format!("stage-{index:02}.intent.json"));
    let original = receipt.with_extension(format!("stage-{index:02}.result.original.json"));
    new_json(
        &intent,
        &json!({"schema":"motionwright.native.alab-source-stage-intent/1",
        "state":"INTENT","request_id":request_id,"expected":before.stamp(),
        "automatic_retry":false,"operation_count":1}),
    )?;
    let outcome = match apply_once() {
        Ok(value) => value,
        Err(error) => {
            // The original Service error remains primary if diagnostic custody fails.
            let _ = new_json(
                &original,
                &json!({"schema":"motionwright.native.alab-source-stage-error/1",
                "request_id":request_id,"original_service_error":error.to_string(),
                "error_rendering_is_not_wire_bytes":true,"effects_absent_claimed":false,
                "automatic_retry":false}),
            );
            return Err(error);
        }
    };
    // Preserve the complete typed returned outcome before any qualification.
    let returned = serde_json::to_value(&outcome)?;
    new_json(&original, &returned)?;
    let mut returned_bytes = serde_json::to_vec_pretty(&returned)?;
    returned_bytes.push(b'\n');
    require(
        read_regular(&original, MAX_JSON)? == returned_bytes,
        "ALab original stage outcome changed while being retained",
    )?;
    stage_receipts.push(json!({"index":index,"request_id":request_id,
        "file":original.file_name().and_then(|name| name.to_str()).ok_or("Stage receipt filename is invalid")?,
        "bytes":returned_bytes.len(),"sha256":sha(&returned_bytes)}));
    require(
        !outcome.replayed
            && outcome.request_id == request_id
            && outcome.previous_revision == before.revision,
        "ALab fresh source stage did not return its original request outcome",
    )?;
    let history = service.history(before.id, before.revision, 2)?;
    require(
        history.len() == 1
            && history[0].revision == outcome.project.revision
            && history[0].created_at == outcome.project.updated_at.to_rfc3339(),
        "ALab source stage has no exact committed history event",
    )?;
    check_transition(before, &outcome.project, &history[0].change)?;
    require(
        service.project(before.id)? == outcome.project,
        "ALab source changed immediately after its original apply result",
    )?;
    Ok(outcome.project)
}

fn alab_adopt_with_expected(
    database: &Path,
    files: &AlabFiles<'_>,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<Value> {
    // Validate every original before creating an intent, DB or authored object.
    alab_check_files(files, expected)?;
    let derived_bytes = read_regular(files.derivation, MAX_JSON)?;
    let derived: Value = serde_json::from_slice(&derived_bytes)?;
    alab_check_derivation(&derived, expected)?;
    require(
        !database.exists() && !receipt_path.exists(),
        "ALab source attempt must use a fresh namespace",
    )?;
    let intent = receipt_path.with_extension("intent.json");
    new_json(
        &intent,
        &json!({"schema":"motionwright.native.alab-source-adoption-intent/1",
        "state":"INTENT","profile":ALAB_PROFILE,"originals":alab_original_descriptors(expected),
        "derived_descriptor_sha256":sha(&derived_bytes),"media":expected.media,
        "automatic_retry":false,"source_origin_is_new_derivation":true,
        "human_acceptance":false,"release_authorized":false}),
    )?;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(database)?
        .sync_all()?;
    let service = StudioService::open(database)?;
    let mut project = service.create_project("Selected original ALab video cut")?;
    new_json(
        &receipt_path.with_extension("project-created.original.json"),
        &serde_json::to_value(&project)?,
    )?;
    let created_project = project.clone();
    let initial_revision = project.revision;
    let mut stage_receipts = Vec::new();
    for index in 0..2 {
        let request = format!("original:alab-source:scene:{index}");
        let change = Change::AddScene {
            name: if index == 0 {
                "mk020_0010"
            } else {
                "mk020_0020"
            }
            .into(),
            objective: "Selected source occurrence; foreign metadata gives no authority".into(),
            duration_seconds: 1,
        };
        project = alab_stage(
            &service,
            &project,
            &request,
            receipt_path,
            index * 2,
            &mut stage_receipts,
            || Ok(service.apply(project.id, &project.stamp(), &request, &change)?),
        )?;
        let request = format!("original:alab-source:duration:{index}");
        let change = Change::SetSceneDuration {
            scene_id: project.scenes[index].id,
            duration: if index == 0 {
                RationalTime::new(13, 6)?
            } else {
                RationalTime::new(91, 12)?
            },
        };
        project = alab_stage(
            &service,
            &project,
            &request,
            receipt_path,
            index * 2 + 1,
            &mut stage_receipts,
            || Ok(service.apply(project.id, &project.stamp(), &request, &change)?),
        )?;
    }
    for index in 0..2 {
        let request = format!("original:alab-source:media:{index}");
        let intent = motionwright_storage::FileImportIntent::Asset {
            name: ALAB_SOURCE_NAMES[index].into(),
            media_type: "video/quicktime".into(),
        };
        project = alab_stage(
            &service,
            &project,
            &request,
            receipt_path,
            4 + index,
            &mut stage_receipts,
            || {
                Ok(service.import_file_verified(
                    project.id,
                    &project.stamp(),
                    &request,
                    files.media[index],
                    &expected.media[index],
                    intent,
                )?)
            },
        )?;
    }
    let request = "original:alab-source:whole-otio";
    let intent = motionwright_storage::FileImportIntent::NativeCapsuleSource {
        spec: alab_capsule_spec(&project)?,
    };
    project = alab_stage(
        &service,
        &project,
        request,
        receipt_path,
        6,
        &mut stage_receipts,
        || {
            Ok(service.import_file_verified(
                project.id,
                &project.stamp(),
                request,
                files.originals[0],
                &expected.originals[0],
                intent,
            )?)
        },
    )?;
    alab_check_source_project(&project)?;
    let source_assets = alab_verify_assets(&service, &project, expected)?;
    let history = service.history(project.id, initial_revision, 8)?;
    require(
        history.len() == 7
            && project.revision
                == initial_revision
                    .checked_add(7)
                    .ok_or("ALab revision overflow")?,
        "ALab source adoption changed its closed seven-stage history",
    )?;
    let watermark = service.production_receipt_watermark(project.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab source adoption acquired native production receipts",
    )?;
    drop(service);
    let reopened = StudioService::open(database)?;
    require(
        reopened.project(project.id)? == project
            && alab_verify_assets(&reopened, &project, expected)? == source_assets,
        "ALab authored source or immutable blobs did not survive SQLite reopen",
    )?;
    alab_check_files(files, expected)?;
    require(
        read_regular(files.derivation, MAX_JSON)? == derived_bytes,
        "ALab derived source input changed during adoption",
    )?;
    let canonical_otio: Value = serde_json::from_str(&otio_interchange(&project)?.body)?;
    let result = json!({"schema":"motionwright.native.alab-selected-source-adoption/1",
        "profile":ALAB_PROFILE,"project":project,"created_project":created_project,
        "stage_receipts":stage_receipts,"canonical_otio":canonical_otio,
        "project_value_sha256":sha(&serde_json::to_vec(&project)?),
        "originals":alab_original_descriptors(expected),"source_assets":source_assets,
        "derived_descriptor_sha256":sha(&derived_bytes),"original_history_events":history.len(),
        "initial_revision":initial_revision,"final_revision":project.revision,
        "canonical_source_adopted":true,"sqlite_source_reopened_equal":true,
        "whole_original_otio_retained":true,"source_fidelity_unavailable":true,
        "remaining_source_references":"opaque/unavailable for this selected two-occurrence profile",
        "historical_readback_rewritten":false,"new_decode":false,"new_render":false,
        "nle_opened":false,"native_editor_dispatched_here":false,"execution_authority_issued":false,
        "production_receipt_count":watermark.count,"automatic_retry":false,
        "human_acceptance":false,"release_authorized":false});
    new_json(receipt_path, &result)?;
    Ok(result)
}

fn alab_check_original_history(
    service: &StudioService,
    source: &Project,
    receipt: &Value,
    receipt_path: &Path,
) -> Result<()> {
    let initial = receipt["initial_revision"]
        .as_u64()
        .ok_or("ALab original initial revision is missing")?;
    let history = service.history(source.id, initial, 8)?;
    alab_check_original_history_rows(source, receipt, receipt_path, &history)
}

fn alab_check_original_history_rows(
    source: &Project,
    receipt: &Value,
    receipt_path: &Path,
    history: &[motionwright_storage::ProjectEvent],
) -> Result<()> {
    let created: Project = serde_json::from_value(receipt["created_project"].clone())?;
    require(
        created.id == source.id
            && created.generation == source.generation
            && created.scenes.is_empty()
            && created.assets.is_empty()
            && created.production_design.capsules.is_empty()
            && receipt["initial_revision"].as_u64() == Some(created.revision)
            && receipt["final_revision"].as_u64() == Some(source.revision)
            && created.revision.checked_add(7) == Some(source.revision),
        "ALab original creation/revision scope differs",
    )?;
    let rows = receipt["stage_receipts"]
        .as_array()
        .ok_or("ALab stage receipt roster is missing")?;
    require(
        rows.len() == 7 && history.len() == 7,
        "ALab original seven-stage history is incomplete",
    )?;
    let requests = [
        "original:alab-source:scene:0",
        "original:alab-source:duration:0",
        "original:alab-source:scene:1",
        "original:alab-source:duration:1",
        "original:alab-source:media:0",
        "original:alab-source:media:1",
        "original:alab-source:whole-otio",
    ];
    let mut before = created;
    for (index, ((descriptor, event), request)) in
        rows.iter().zip(history).zip(requests).enumerate()
    {
        let path = receipt_path.with_extension(format!("stage-{index:02}.result.original.json"));
        require(
            descriptor["index"].as_u64() == Some(index as u64)
                && descriptor["request_id"] == request
                && descriptor["file"].as_str() == path.file_name().and_then(|name| name.to_str()),
            "ALab stage receipt no longer identifies its original request",
        )?;
        let raw = read_regular(&path, MAX_JSON)?;
        require(
            descriptor["bytes"].as_u64() == Some(raw.len() as u64)
                && descriptor["sha256"] == sha(&raw),
            "ALab original stage bytes changed",
        )?;
        let returned: Value = serde_json::from_slice(&raw)?;
        let after: Project = serde_json::from_value(returned["project"].clone())?;
        require(
            returned["request_id"] == request
                && returned["replayed"] == false
                && returned["previous_revision"].as_u64() == Some(before.revision)
                && event.revision == after.revision
                && event.created_at == after.updated_at.to_rfc3339(),
            "ALab SQLite history differs from the original returned stage result",
        )?;
        check_transition(&before, &after, &event.change)?;
        before = after;
    }
    require(
        before == *source,
        "ALab original stage history does not reconstruct the exact final Project",
    )
}

fn alab_checked_source(
    database: &Path,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<(StudioService, Project, String, Value)> {
    let receipt_bytes = read_regular(receipt_path, MAX_JSON)?;
    let receipt: Value = serde_json::from_slice(&receipt_bytes)?;
    require(
        receipt["schema"] == "motionwright.native.alab-selected-source-adoption/1"
            && receipt["profile"] == ALAB_PROFILE
            && receipt["originals"] == alab_original_descriptors(expected)
            && receipt["canonical_source_adopted"] == true
            && receipt["whole_original_otio_retained"] == true
            && receipt["source_fidelity_unavailable"] == true
            && receipt["original_history_events"] == 7
            && receipt["nle_opened"] == false
            && receipt["new_decode"] == false
            && receipt["native_editor_dispatched_here"] == false
            && receipt["execution_authority_issued"] == false
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "ALab adoption receipt changed its original profile or scope",
    )?;
    let source: Project = serde_json::from_value(receipt["project"].clone())?;
    alab_check_source_project(&source)?;
    require(
        receipt["project_value_sha256"] == sha(&serde_json::to_vec(&source)?),
        "ALab adoption receipt changed its exact authored Project",
    )?;
    let service = StudioService::open(database)?;
    require(
        service.project(source.id)? == source,
        "ALab source no longer equals its adoption receipt",
    )?;
    alab_check_original_history(&service, &source, &receipt, receipt_path)?;
    let verified = alab_verify_assets(&service, &source, expected)?;
    require(
        receipt["source_assets"] == verified,
        "ALab adopted asset roster no longer matches the original receipt",
    )?;
    Ok((service, source, sha(&receipt_bytes), verified))
}

fn alab_source_readback(
    database: &Path,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<Value> {
    let (service, project, receipt_sha, assets) =
        alab_checked_source(database, receipt_path, expected)?;
    let watermark = service.production_receipt_watermark(project.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab source gained production authority after adoption",
    )?;
    Ok(
        json!({"schema":"motionwright.native.alab-selected-source-readback/1","profile":ALAB_PROFILE,
        "adoption_receipt_sha256":receipt_sha,"project_id":project.id,"generation":project.generation,
        "revision":project.revision,"project_value_sha256":sha(&serde_json::to_vec(&project)?),
        "source_assets":assets,"project_reopened_equal":true,"source_fidelity_unavailable":true,
        "canonical_project_mutation_performed":false,"execution_authority_issued":false,
        "native_editor_dispatched_here":false,"human_acceptance":false,"release_authorized":false}),
    )
}

fn alab_source_bundle(
    database: &Path,
    adoption_receipt: &Path,
    bundle: &Path,
    destination: &Path,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<Value> {
    let (service, source, adoption_sha, assets) =
        alab_checked_source(database, adoption_receipt, expected)?;
    require(
        !bundle.exists()
            && !destination.exists()
            && destination.parent().is_some()
            && destination.parent() != database.parent(),
        "ALab portable destination must be a fresh distinct owner",
    )?;
    new_json(
        &receipt_path.with_extension("intent.json"),
        &json!({
        "schema":"motionwright.native.alab-source-portability-intent/1","state":"INTENT",
        "expected":source.stamp(),"adoption_receipt_sha256":adoption_sha,
        "bundle":bundle,"destination_database":destination,"automatic_retry":false,
        "execution_authority_issued":false}),
    )?;
    let exported = service.export_project_bundle_at(source.id, &source.stamp(), bundle)?;
    new_json(
        &receipt_path.with_extension("export.original.json"),
        &serde_json::to_value(&exported)?,
    )?;
    let manifest = read_regular(&bundle.join("manifest.json"), MAX_JSON)?;
    let inspected = service.inspect_project_bundle_with_fingerprint(bundle)?;
    new_json(
        &receipt_path.with_extension("inspection.original.json"),
        &serde_json::to_value(&inspected)?,
    )?;
    require(
        read_regular(&bundle.join("manifest.json"), MAX_JSON)? == manifest,
        "ALab portable manifest changed after inspection",
    )?;
    new_json(
        &receipt_path.with_extension("import.intent.json"),
        &json!({
        "schema":"motionwright.native.alab-source-bundle-import-intent/1","state":"INTENT",
        "inspection_fingerprint":inspected.fingerprint,"manifest_sha256":sha(&manifest),
        "source_generation":source.generation,"automatic_retry":false}),
    )?;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?
        .sync_all()?;
    let restored = StudioService::open(destination)?;
    let imported = restored.import_project_bundle_inspected(bundle, &inspected.fingerprint)?;
    new_json(
        &receipt_path.with_extension("import.original.json"),
        &serde_json::to_value(&imported)?,
    )?;
    check_portable_source_project(&source, &imported)?;
    alab_check_source_project(&imported)?;
    require(
        alab_verify_assets(&restored, &imported, expected)? == assets,
        "ALab portable source lost original MOV or OTIO bytes",
    )?;
    let watermark = restored.production_receipt_watermark(imported.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab portable source inherited production receipts",
    )?;
    drop(restored);
    let reopened = StudioService::open(destination)?;
    require(
        reopened.project(imported.id)? == imported
            && alab_verify_assets(&reopened, &imported, expected)? == assets
            && service.project(source.id)? == source
            && read_regular(&bundle.join("manifest.json"), MAX_JSON)? == manifest
            && sha(&read_regular(adoption_receipt, MAX_JSON)?) == adoption_sha,
        "ALab portability changed originals or failed separate-owner SQLite/CAS reopen",
    )?;
    let result = json!({"schema":"motionwright.native.alab-source-portability/1","profile":ALAB_PROFILE,
        "adoption_receipt_sha256":adoption_sha,"source_project":source,"imported_project":imported,
        "source_updated_at":source.updated_at,"imported_updated_at":imported.updated_at,
        "source_generation":source.generation,"imported_generation":imported.generation,
        "source_assets":assets,"originals":alab_original_descriptors(expected),
        "imported_project_value_sha256":sha(&serde_json::to_vec(&imported)?),
        "manifest_sha256":sha(&manifest),"inspection_fingerprint":inspected.fingerprint,
        "fresh_generation_observed":true,"separate_owner_cas":true,
        "project_reopened_equal":true,"all_source_blobs_reopened_equal":true,
        "whole_original_otio_retained":true,"source_fidelity_unavailable":true,
        "production_receipt_count":watermark.count,"local_production_receipts_inherited":false,
        "execution_authority_issued":false,"native_editor_dispatched_here":false,
        "automatic_retry":false,"human_acceptance":false,"release_authorized":false});
    new_json(receipt_path, &result)?;
    Ok(result)
}

fn alab_source_portable_readback(
    database: &Path,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<Value> {
    let original = read_regular(receipt_path, MAX_JSON)?;
    let receipt: Value = serde_json::from_slice(&original)?;
    require(
        receipt["schema"] == "motionwright.native.alab-source-portability/1"
            && receipt["profile"] == ALAB_PROFILE
            && receipt["originals"] == alab_original_descriptors(expected)
            && receipt["fresh_generation_observed"] == true
            && receipt["source_fidelity_unavailable"] == true
            && receipt["whole_original_otio_retained"] == true
            && receipt["separate_owner_cas"] == true
            && receipt["local_production_receipts_inherited"] == false
            && receipt["execution_authority_issued"] == false
            && receipt["native_editor_dispatched_here"] == false
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "ALab portable source receipt was changed or promoted",
    )?;
    let source: Project = serde_json::from_value(receipt["source_project"].clone())?;
    let project: Project = serde_json::from_value(receipt["imported_project"].clone())?;
    check_portable_source_project(&source, &project)?;
    alab_check_source_project(&project)?;
    require(
        receipt["source_generation"] == serde_json::to_value(source.generation)?
            && receipt["imported_generation"] == serde_json::to_value(project.generation)?
            && receipt["source_updated_at"] == serde_json::to_value(source.updated_at)?
            && receipt["imported_updated_at"] == serde_json::to_value(project.updated_at)?
            && receipt["imported_project_value_sha256"] == sha(&serde_json::to_vec(&project)?),
        "ALab portable source identity/timestamp comparison changed",
    )?;
    let service = StudioService::open(database)?;
    require(
        service.project(project.id)? == project,
        "ALab new process did not reopen the exact portable Project",
    )?;
    let assets = alab_verify_assets(&service, &project, expected)?;
    let watermark = service.production_receipt_watermark(project.id)?;
    require(
        receipt["source_assets"] == assets && watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab portable source lost its blob identities or acquired production authority",
    )?;
    Ok(
        json!({"schema":"motionwright.native.alab-source-portable-readback/1","profile":ALAB_PROFILE,
        "portability_receipt_sha256":sha(&original),"project_id":project.id,"generation":project.generation,
        "revision":project.revision,"project_value_sha256":sha(&serde_json::to_vec(&project)?),
        "source_assets":assets,"project_reopened_equal":true,"all_source_blobs_reopened_equal":true,
        "source_fidelity_unavailable":true,"fresh_generation_observed":true,
        "canonical_project_mutation_performed":false,"execution_authority_issued":false,
        "native_editor_dispatched_here":false,"local_production_receipts_inherited":false,
        "human_acceptance":false,"release_authorized":false}),
    )
}

fn alab_edited_project(project: &Project) -> Result<()> {
    project.validate()?;
    require(
        project.scenes.len() == 2
            && project.scenes[0].name == "mk020_0010"
            && project.scenes[1].name == "mk020_0020"
            && project.scenes[0].start == RationalTime::new(0, 1)?
            && project.scenes[0].duration == RationalTime::new(13, 6)?
            && project.scenes[1].start == RationalTime::new(13, 6)?
            && project.scenes[1].duration == RationalTime::new(85, 12)?,
        "ALab returned cut is not exactly the second 182-to-170-frame end trim",
    )
}

fn alab_returned_review(
    source: &Project,
    preview: OtioEditPreview,
    raw: &Value,
    source_sha: &str,
) -> Result<OtioOwnerCutReview> {
    alab_check_source_project(source)?;
    require(
        preview.source_project_sha256 == source_sha
            && raw["schema"] == "motionwright.kdenlive-bound-cut-preview/1"
            && raw["profile"] == "kdenlive-bound-video-cut-v1"
            && raw["status"] == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
        "ALab returned owner cut lacks its exact existing bounded preview",
    )?;
    let derived = serde_json::to_value(&preview)?;
    for field in [
        "source_project_sha256",
        "project_id",
        "generation",
        "expected_project_revision",
        "original_scene_order",
        "proposed_scene_order",
        "native_change_proposals",
        "media_refs_verified",
        "loss_report",
        "property_fidelity",
        "edit_imported",
        "source_media_relinked",
        "owner_revision_commit_required",
        "human_review_required",
        "semantic_native_roundtrip",
        "nle_receiver_acceptance",
        "publication_approved",
    ] {
        require(
            raw.get(field).is_some() && raw[field] == derived[field],
            "ALab raw and owner preview bindings differ",
        )?;
    }
    require(
        raw["original_missing_scene_ids"] == json!([])
            && raw["raw_files_rewritten"] == false
            && raw["native_final_frames_verified"] == false
            && raw["native_project_reopened"] == "NOT_VERIFIED"
            && raw["received_clip_count"] == 2
            && raw["edited_clip_count"] == 2
            && preview.media_refs_verified == 2
            && preview.missing_references_retained == 0
            && preview.property_fidelity.len() == 12
            && !preview.loss_report.is_empty(),
        "ALab returned preview lost media or promoted native acceptance",
    )?;
    let ids = source
        .scenes
        .iter()
        .map(|scene| scene.id)
        .collect::<Vec<_>>();
    require(
        preview.original_scene_order == ids
            && preview.proposed_scene_order == ids
            && preview.native_change_proposals
                == vec![OtioNativeCut::SetSceneDuration {
                    scene_id: ids[1],
                    duration: RationalTime::new(85, 12)?,
                }],
        "ALab returned review must contain only its selected second-scene tail trim",
    )?;
    preview.bounded_native_changes(source)?;
    Ok(OtioOwnerCutReview {
        preview: Box::new(preview),
        reviewer_declaration: "Automated original-source benchmark caller; no human acceptance declared".into(),
        rationale: "Apply the source-bound returned second-scene end trim once; preserve whole original OTIO, both MOVs and every explicit property loss".into(),
        acknowledges_property_losses: true, acknowledges_absolute_audio_marker_positions: true,
        confirms_single_revision_change: true,
    })
}

fn alab_editor_source_spec(project: &Project) -> Result<NativeCapsuleSourceSpec> {
    require(
        project.scenes.len() == 2
            && project.production_design.capsules.len() == 1
            && project.production_design.capsules[0].id == Uuid::from_u128(ALAB_CAPSULE_ID),
        "ALab editor source must follow its retained original OTIO capsule",
    )?;
    Ok(NativeCapsuleSourceSpec {
        name: NATIVE_SOURCE_NAME.into(),
        media_type: NATIVE_SOURCE_MEDIA_TYPE.into(),
        capsule_id: Uuid::from_u128(NATIVE_CAPSULE_ID),
        scene_id: project.scenes[0].id,
        label: "Opaque edited ALab two-occurrence Kdenlive source; no per-scene editable mapping"
            .into(),
        editable_parameters: vec![],
        native_editor_hint: "Kdenlive".into(),
    })
}

fn alab_verify_edited_assets(
    service: &StudioService,
    original: &Project,
    final_project: &Project,
    expected: &AlabExpected,
    native: &motionwright_storage::BlobDescriptor,
) -> Result<Value> {
    alab_edited_project(final_project)?;
    require(
        final_project.id == original.id
            && final_project.assets.len() == 4
            && final_project.assets[..3] == original.assets[..]
            && final_project.production_design.capsules.len() == 2
            && final_project.production_design.capsules[..1]
                == original.production_design.capsules[..],
        "ALab edited source changed or dropped an original asset/capsule",
    )?;
    let retained = alab_verify_assets(service, original, expected)?;
    let added = &final_project.assets[3];
    require(
        added.content_sha256.as_deref() == Some(native.sha256.as_str())
            && alab_editor_source_spec(original)?.bind_asset(added)?
                == final_project.production_design.capsules[1],
        "ALab edited native source does not bind its exact opaque capsule",
    )?;
    require(
        native.size_bytes > 0
            && native.size_bytes <= NATIVE_SOURCE_LIMIT
            && lower_sha(&native.sha256),
        "ALab edited native source descriptor exceeds its bound",
    )?;
    let bytes = service
        .store()
        .lock()
        .read_blob(&native.sha256, native.size_bytes)?;
    require(
        bytes.len() as u64 == native.size_bytes && sha(&bytes) == native.sha256,
        "ALab edited Kdenlive source did not survive CAS readback",
    )?;
    Ok(
        json!({"original_source_assets":retained,"native_source":native,
        "native_source_asset_id":added.id,"native_capsule_id":final_project.production_design.capsules[1].id,
        "all_four_source_blobs_reopened_equal":true,"source_fidelity_unavailable":true,
        "per_scene_native_editability_verified":false}),
    )
}

fn alab_commit_returned_cut(
    database: &Path,
    adoption_path: &Path,
    source_path: &Path,
    preview_path: &Path,
    raw_path: &Path,
    native_path: &Path,
    native_sha: &str,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<Value> {
    let source_bytes = read_regular(source_path, MAX_JSON)?;
    let source: Project = serde_json::from_slice(&source_bytes)?;
    let preview_bytes = read_regular(preview_path, MAX_JSON)?;
    let raw_bytes = read_regular(raw_path, MAX_JSON)?;
    let native_bytes = read_regular(native_path, NATIVE_SOURCE_LIMIT)?;
    require(
        native_path.file_name().and_then(|value| value.to_str()) == Some(NATIVE_SOURCE_NAME)
            && !native_bytes.is_empty()
            && lower_sha(native_sha)
            && sha(&native_bytes) == native_sha,
        "ALab returned source requires the exact observed edited.kdenlive bytes",
    )?;
    let (service, admitted, adoption_sha, original_assets) =
        alab_checked_source(database, adoption_path, expected)?;
    require(
        source == admitted,
        "ALab source export differs from its canonical adopted Project",
    )?;
    let preview: OtioEditPreview = serde_json::from_slice(&preview_bytes)?;
    let raw: Value = serde_json::from_slice(&raw_bytes)?;
    let review = alab_returned_review(&source, preview, &raw, &sha(&source_bytes))?;
    let change = Change::ApplyOtioOwnerCutReview {
        review: Box::new(review),
    };
    let mut candidate = source.clone();
    candidate.apply_change(&change)?;
    alab_edited_project(&candidate)?;
    let cut_request = format!("original:alab-kdenlive-cut:{}", sha(&raw_bytes));
    let source_request = format!("original:alab-kdenlive-capsule:{native_sha}");
    new_json(
        &receipt_path.with_extension("intent.json"),
        &json!({
        "schema":"motionwright.native.alab-returned-cut-intent/1","state":"INTENT",
        "source_stamp":source.stamp(),"adoption_receipt_sha256":adoption_sha,
        "source_project_sha256":sha(&source_bytes),"owner_preview_sha256":sha(&preview_bytes),
        "original_Kdenlive_preview_sha256":sha(&raw_bytes),"native_source_sha256":native_sha,
        "native_source_bytes":native_bytes.len(),"cut_request_id":cut_request,
        "source_import_request_id":source_request,"two_separate_revision_operations":true,
        "automatic_retry":false,"human_acceptance":false}),
    )?;
    let mut stages = Vec::new();
    let cut = alab_stage(
        &service,
        &source,
        &cut_request,
        receipt_path,
        7,
        &mut stages,
        || Ok(service.apply(source.id, &source.stamp(), &cut_request, &change)?),
    )?;
    alab_edited_project(&cut)?;
    require(
        alab_verify_assets(&service, &cut, expected)? == original_assets,
        "ALab owner cut changed original MOV or OTIO source bytes",
    )?;
    let native_descriptor = alab_blob(native_sha, native_bytes.len() as u64);
    let spec = alab_editor_source_spec(&cut)?;
    let final_project = alab_stage(
        &service,
        &cut,
        &source_request,
        receipt_path,
        8,
        &mut stages,
        || {
            Ok(service.import_file_verified(
                cut.id,
                &cut.stamp(),
                &source_request,
                native_path,
                &native_descriptor,
                motionwright_storage::FileImportIntent::NativeCapsuleSource { spec },
            )?)
        },
    )?;
    let assets = alab_verify_edited_assets(
        &service,
        &source,
        &final_project,
        expected,
        &native_descriptor,
    )?;
    let history = service.history(source.id, source.revision, 3)?;
    require(
        history.len() == 2
            && history[0].change == change
            && history[0].revision == cut.revision
            && history[1].revision == final_project.revision,
        "ALab cut and native source attachment must remain exactly two additional events",
    )?;
    let watermark = service.production_receipt_watermark(source.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab cut acquired production authority",
    )?;
    drop(service);
    let reopened = StudioService::open(database)?;
    require(
        reopened.project(source.id)? == final_project
            && alab_verify_edited_assets(
                &reopened,
                &source,
                &final_project,
                expected,
                &native_descriptor,
            )? == assets,
        "ALab edited source failed SQLite/CAS reopen",
    )?;
    require(
        read_regular(source_path, MAX_JSON)? == source_bytes
            && read_regular(preview_path, MAX_JSON)? == preview_bytes
            && read_regular(raw_path, MAX_JSON)? == raw_bytes
            && read_regular(native_path, NATIVE_SOURCE_LIMIT)? == native_bytes
            && sha(&read_regular(adoption_path, MAX_JSON)?) == adoption_sha,
        "ALab original cut/source input changed during canonical adoption",
    )?;
    let result = json!({"schema":"motionwright.native.alab-returned-cut-and-source/1","profile":ALAB_PROFILE,
        "adoption_receipt_sha256":adoption_sha,"source_project_sha256":sha(&source_bytes),
        "owner_preview_sha256":sha(&preview_bytes),"original_Kdenlive_preview_sha256":sha(&raw_bytes),
        "original_project":source,"cut_project":cut,"committed_project":final_project,
        "cut_change":change,"cut_request_id":cut_request,"source_import_request_id":source_request,
        "originals":alab_original_descriptors(expected),"native_source":native_descriptor,
        "source_assets":assets,"stage_receipts":stages,"history":history,
        "committed_project_value_sha256":sha(&serde_json::to_vec(&final_project)?),
        "owner_cut_single_revision_committed":true,"native_source_additional_revision_committed":true,
        "exact_additional_history_events":2,"whole_original_otio_retained":true,
        "all_four_source_blobs_reopened_equal":true,"source_fidelity_unavailable":true,
        "sqlite_source_reopened_equal":true,"edited_second_frames":170,"original_second_frames":182,
        "native_editor_dispatched_here":false,"per_scene_native_editability_verified":false,
        "execution_authority_issued":false,"production_receipt_count":0,"automatic_retry":false,
        "human_acceptance":false,"release_authorized":false});
    new_json(receipt_path, &result)?;
    Ok(result)
}

struct AlabEditedSource {
    service: StudioService,
    original: Project,
    project: Project,
    receipt_sha: String,
    assets: Value,
    native: motionwright_storage::BlobDescriptor,
}

fn alab_checked_edited_source(
    database: &Path,
    adoption_path: &Path,
    receipt_path: &Path,
    expected_receipt_sha: &str,
    expected: &AlabExpected,
) -> Result<AlabEditedSource> {
    let adoption_bytes = read_regular(adoption_path, MAX_JSON)?;
    let adoption: Value = serde_json::from_slice(&adoption_bytes)?;
    let raw = read_regular(receipt_path, MAX_JSON)?;
    require(
        lower_sha(expected_receipt_sha) && sha(&raw) == expected_receipt_sha,
        "ALab returned receipt differs from the caller-retained original SHA",
    )?;
    let receipt: Value = serde_json::from_slice(&raw)?;
    require(
        receipt["schema"] == "motionwright.native.alab-returned-cut-and-source/1"
            && receipt["profile"] == ALAB_PROFILE
            && receipt["adoption_receipt_sha256"] == sha(&adoption_bytes)
            && receipt["originals"] == alab_original_descriptors(expected)
            && receipt["owner_cut_single_revision_committed"] == true
            && receipt["native_source_additional_revision_committed"] == true
            && receipt["exact_additional_history_events"] == 2
            && receipt["source_fidelity_unavailable"] == true
            && receipt["edited_second_frames"] == 170
            && receipt["original_second_frames"] == 182
            && receipt["whole_original_otio_retained"] == true
            && receipt["native_editor_dispatched_here"] == false
            && receipt["per_scene_native_editability_verified"] == false
            && receipt["execution_authority_issued"] == false
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "ALab returned-source receipt changed its original profile or claims",
    )?;
    let original: Project = serde_json::from_value(receipt["original_project"].clone())?;
    let cut: Project = serde_json::from_value(receipt["cut_project"].clone())?;
    let final_project: Project = serde_json::from_value(receipt["committed_project"].clone())?;
    let adoption_project: Project = serde_json::from_value(adoption["project"].clone())?;
    require(
        original == adoption_project
            && adoption["originals"] == alab_original_descriptors(expected)
            && adoption["schema"] == "motionwright.native.alab-selected-source-adoption/1"
            && adoption["profile"] == ALAB_PROFILE
            && adoption["project_value_sha256"] == sha(&serde_json::to_vec(&original)?),
        "ALab returned cut no longer starts from its exact adopted source",
    )?;
    alab_check_source_project(&original)?;
    alab_edited_project(&cut)?;
    alab_edited_project(&final_project)?;
    let change: Change = serde_json::from_value(receipt["cut_change"].clone())?;
    let Change::ApplyOtioOwnerCutReview { review } = &change else {
        return Err("ALab cut changed its existing owner review operation".into());
    };
    require(
        review.preview.native_change_proposals
            == vec![OtioNativeCut::SetSceneDuration {
                scene_id: original.scenes[1].id,
                duration: RationalTime::new(85, 12)?,
            }],
        "ALab retained owner review changed its exact selected trim",
    )?;
    check_transition(&original, &cut, &change)?;
    let native: motionwright_storage::BlobDescriptor =
        serde_json::from_value(receipt["native_source"].clone())?;
    let source_spec = alab_editor_source_spec(&cut)?;
    let native_asset = final_project
        .assets
        .last()
        .ok_or("ALab source attachment asset missing")?
        .clone();
    let attachment = Change::ImportNativeCapsuleSource {
        capsule: source_spec.bind_asset(&native_asset)?,
        asset: native_asset,
    };
    check_transition(&cut, &final_project, &attachment)?;
    require(
        receipt["committed_project_value_sha256"] == sha(&serde_json::to_vec(&final_project)?),
        "ALab final original Project digest changed",
    )?;
    let service = StudioService::open(database)?;
    require(
        service.project(original.id)? == final_project,
        "ALab new process did not reopen its exact edited Project",
    )?;
    let initial = adoption["initial_revision"]
        .as_u64()
        .ok_or("ALab adoption initial revision missing")?;
    let all_history = service.history(original.id, initial, 10)?;
    require(
        all_history.len() == 9,
        "ALab edited source must retain its original seven plus two events",
    )?;
    alab_check_original_history_rows(&original, &adoption, adoption_path, &all_history[..7])?;
    let history = &all_history[7..];
    require(
        serde_json::to_value(history)? == receipt["history"]
            && history[0].change == change
            && history[1].change == attachment,
        "ALab actual cut/source history differs from the retained operations",
    )?;
    let stages = receipt["stage_receipts"]
        .as_array()
        .ok_or("ALab cut/source outcome roster missing")?;
    let raw_preview_sha = receipt["original_Kdenlive_preview_sha256"]
        .as_str()
        .ok_or("ALab returned preview digest missing")?;
    require(
        lower_sha(raw_preview_sha),
        "ALab returned preview digest is not SHA-256",
    )?;
    let cut_request = format!("original:alab-kdenlive-cut:{raw_preview_sha}");
    let source_request = format!("original:alab-kdenlive-capsule:{}", native.sha256);
    require(
        stages.len() == 2
            && receipt["cut_request_id"] == cut_request
            && receipt["source_import_request_id"] == source_request,
        "ALab cut/source original request identity changed",
    )?;
    for (index, (expected_project, previous, request)) in [
        (&cut, &original, cut_request.as_str()),
        (&final_project, &cut, source_request.as_str()),
    ]
    .into_iter()
    .enumerate()
    {
        let number = index + 7;
        let path = receipt_path.with_extension(format!("stage-{number:02}.result.original.json"));
        let bytes = read_regular(&path, MAX_JSON)?;
        require(
            stages[index]["index"] == number
                && stages[index]["request_id"] == request
                && stages[index]["file"].as_str()
                    == path.file_name().and_then(|value| value.to_str())
                && stages[index]["sha256"] == sha(&bytes)
                && stages[index]["bytes"].as_u64() == Some(bytes.len() as u64),
            "ALab cut/source original typed outcome bytes changed",
        )?;
        let outcome: motionwright_storage::ApplyOutcome = serde_json::from_slice(&bytes)?;
        require(
            outcome.project == *expected_project
                && !outcome.replayed
                && outcome.request_id == request
                && outcome.previous_revision == previous.revision
                && history[index].revision == expected_project.revision
                && history[index].created_at == expected_project.updated_at.to_rfc3339(),
            "ALab cut/source committed history does not match the original typed outcome",
        )?;
    }
    let assets = alab_verify_edited_assets(&service, &original, &final_project, expected, &native)?;
    let watermark = service.production_receipt_watermark(original.id)?;
    require(
        receipt["source_assets"] == assets && watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab edited source changed retained bytes or acquired production authority",
    )?;
    require(
        read_regular(receipt_path, MAX_JSON)? == raw
            && read_regular(adoption_path, MAX_JSON)? == adoption_bytes,
        "ALab original receipt changed during the independent readback",
    )?;
    // ProjectEvent binds Change/revision/time. Request identity is retained in
    // the original ApplyOutcome and is anchored by the caller's independent SHA;
    // this read-only check never replays apply to infer that identity.
    Ok(AlabEditedSource {
        service,
        original,
        project: final_project,
        receipt_sha: sha(&raw),
        assets,
        native,
    })
}

fn alab_edited_readback(
    database: &Path,
    adoption_path: &Path,
    receipt_path: &Path,
    expected_receipt_sha: &str,
    expected: &AlabExpected,
) -> Result<Value> {
    let state = alab_checked_edited_source(
        database,
        adoption_path,
        receipt_path,
        expected_receipt_sha,
        expected,
    )?;
    Ok(
        json!({"schema":"motionwright.native.alab-returned-source-readback/1","profile":ALAB_PROFILE,
        "returned_source_receipt_sha256":state.receipt_sha,"project_id":state.project.id,
        "generation":state.project.generation,"revision":state.project.revision,
        "project_value_sha256":sha(&serde_json::to_vec(&state.project)?),"source_assets":state.assets,
        "native_source":state.native,"project_reopened_equal":true,"exact_nine_history_events":true,
        "all_four_source_blobs_reopened_equal":true,"source_fidelity_unavailable":true,
        "canonical_project_mutation_performed":false,"native_editor_dispatched_here":false,
        "execution_authority_issued":false,"human_acceptance":false,"release_authorized":false}),
    )
}

fn alab_edited_bundle(
    database: &Path,
    adoption_path: &Path,
    edit_receipt: &Path,
    expected_edit_sha: &str,
    bundle: &Path,
    destination: &Path,
    receipt_path: &Path,
    expected: &AlabExpected,
) -> Result<Value> {
    let AlabEditedSource {
        service,
        original,
        project: source,
        receipt_sha: edit_sha,
        assets,
        native,
    } = alab_checked_edited_source(
        database,
        adoption_path,
        edit_receipt,
        expected_edit_sha,
        expected,
    )?;
    require(
        !bundle.exists()
            && !destination.exists()
            && destination.parent().is_some()
            && destination.parent() != database.parent(),
        "ALab edited portable destination is not a fresh distinct owner",
    )?;
    new_json(
        &receipt_path.with_extension("intent.json"),
        &json!({
        "schema":"motionwright.native.alab-edited-portability-intent/1","state":"INTENT",
        "expected":source.stamp(),"returned_source_receipt_sha256":edit_sha,
        "bundle":bundle,"destination_database":destination,"automatic_retry":false}),
    )?;
    let exported = service.export_project_bundle_at(source.id, &source.stamp(), bundle)?;
    new_json(
        &receipt_path.with_extension("export.original.json"),
        &serde_json::to_value(&exported)?,
    )?;
    let manifest = read_regular(&bundle.join("manifest.json"), MAX_JSON)?;
    let inspected = service.inspect_project_bundle_with_fingerprint(bundle)?;
    new_json(
        &receipt_path.with_extension("inspection.original.json"),
        &serde_json::to_value(&inspected)?,
    )?;
    require(
        read_regular(&bundle.join("manifest.json"), MAX_JSON)? == manifest,
        "ALab edited bundle changed after original inspection",
    )?;
    new_json(
        &receipt_path.with_extension("import.intent.json"),
        &json!({
        "schema":"motionwright.native.alab-edited-bundle-import-intent/1","state":"INTENT",
        "inspection_fingerprint":inspected.fingerprint,"manifest_sha256":sha(&manifest),
        "source_generation":source.generation,"automatic_retry":false}),
    )?;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)?
        .sync_all()?;
    let restored = StudioService::open(destination)?;
    let imported = restored.import_project_bundle_inspected(bundle, &inspected.fingerprint)?;
    new_json(
        &receipt_path.with_extension("import.original.json"),
        &serde_json::to_value(&imported)?,
    )?;
    check_portable_source_project(&source, &imported)?;
    require(
        alab_verify_edited_assets(&restored, &original, &imported, expected, &native)? == assets,
        "ALab edited bundle lost one of its four exact source blobs",
    )?;
    let watermark = restored.production_receipt_watermark(imported.id)?;
    require(
        watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab edited portable source inherited production receipts",
    )?;
    drop(restored);
    let reopened = StudioService::open(destination)?;
    require(
        reopened.project(imported.id)? == imported
            && alab_verify_edited_assets(&reopened, &original, &imported, expected, &native)?
                == assets
            && service.project(source.id)? == source
            && read_regular(&bundle.join("manifest.json"), MAX_JSON)? == manifest
            && sha(&read_regular(edit_receipt, MAX_JSON)?) == edit_sha,
        "ALab edited portability changed originals or failed SQLite/CAS reopen",
    )?;
    let result = json!({"schema":"motionwright.native.alab-edited-source-portability/1","profile":ALAB_PROFILE,
        "returned_source_receipt_sha256":edit_sha,"original_project":original,
        "source_project":source,"imported_project":imported,"native_source":native,
        "source_updated_at":source.updated_at,"imported_updated_at":imported.updated_at,
        "source_generation":source.generation,"imported_generation":imported.generation,
        "originals":alab_original_descriptors(expected),"source_assets":assets,
        "imported_project_value_sha256":sha(&serde_json::to_vec(&imported)?),
        "manifest_sha256":sha(&manifest),"inspection_fingerprint":inspected.fingerprint,
        "fresh_generation_observed":true,"separate_owner_cas":true,"project_reopened_equal":true,
        "all_four_source_blobs_reopened_equal":true,"source_fidelity_unavailable":true,
        "whole_original_otio_retained":true,"production_receipt_count":0,
        "local_production_receipts_inherited":false,"execution_authority_issued":false,
        "native_editor_dispatched_here":false,"per_scene_native_editability_verified":false,
        "automatic_retry":false,"human_acceptance":false,"release_authorized":false});
    new_json(receipt_path, &result)?;
    Ok(result)
}

fn alab_edited_portable_readback(
    database: &Path,
    receipt_path: &Path,
    expected_receipt_sha: &str,
    expected: &AlabExpected,
) -> Result<Value> {
    let raw = read_regular(receipt_path, MAX_JSON)?;
    require(
        lower_sha(expected_receipt_sha) && sha(&raw) == expected_receipt_sha,
        "ALab edited bundle receipt differs from the caller-retained original SHA",
    )?;
    let receipt: Value = serde_json::from_slice(&raw)?;
    require(
        receipt["schema"] == "motionwright.native.alab-edited-source-portability/1"
            && receipt["profile"] == ALAB_PROFILE
            && receipt["originals"] == alab_original_descriptors(expected)
            && receipt["fresh_generation_observed"] == true
            && receipt["separate_owner_cas"] == true
            && receipt["whole_original_otio_retained"] == true
            && receipt["source_fidelity_unavailable"] == true
            && receipt["local_production_receipts_inherited"] == false
            && receipt["execution_authority_issued"] == false
            && receipt["native_editor_dispatched_here"] == false
            && receipt["per_scene_native_editability_verified"] == false
            && receipt["human_acceptance"] == false
            && receipt["release_authorized"] == false,
        "ALab edited portable receipt was changed or promoted",
    )?;
    let original: Project = serde_json::from_value(receipt["original_project"].clone())?;
    let source: Project = serde_json::from_value(receipt["source_project"].clone())?;
    let project: Project = serde_json::from_value(receipt["imported_project"].clone())?;
    let native: motionwright_storage::BlobDescriptor =
        serde_json::from_value(receipt["native_source"].clone())?;
    check_portable_source_project(&source, &project)?;
    alab_check_source_project(&original)?;
    require(
        receipt["source_generation"] == serde_json::to_value(source.generation)?
            && receipt["imported_generation"] == serde_json::to_value(project.generation)?
            && receipt["source_updated_at"] == serde_json::to_value(source.updated_at)?
            && receipt["imported_updated_at"] == serde_json::to_value(project.updated_at)?
            && receipt["imported_project_value_sha256"] == sha(&serde_json::to_vec(&project)?),
        "ALab edited portable source/timestamp identity changed",
    )?;
    let service = StudioService::open(database)?;
    require(
        service.project(project.id)? == project,
        "ALab edited portable Project did not reopen exactly",
    )?;
    let assets = alab_verify_edited_assets(&service, &original, &project, expected, &native)?;
    let watermark = service.production_receipt_watermark(project.id)?;
    require(
        receipt["source_assets"] == assets && watermark.count == 0 && watermark.latest_id.is_none(),
        "ALab edited portable source lost originals or acquired production authority",
    )?;
    require(
        read_regular(receipt_path, MAX_JSON)? == raw,
        "ALab edited bundle receipt changed during its new-process readback",
    )?;
    Ok(
        json!({"schema":"motionwright.native.alab-edited-portable-readback/1","profile":ALAB_PROFILE,
        "bundle_receipt_sha256":sha(&raw),"project_id":project.id,"generation":project.generation,
        "revision":project.revision,"project_value_sha256":sha(&serde_json::to_vec(&project)?),
        "native_source":native,"source_assets":assets,"project_reopened_equal":true,
        "all_four_source_blobs_reopened_equal":true,"source_fidelity_unavailable":true,
        "fresh_generation_observed":true,"whole_original_otio_retained":true,
        "canonical_project_mutation_performed":false,"local_production_receipts_inherited":false,
        "execution_authority_issued":false,"native_editor_dispatched_here":false,
        "human_acceptance":false,"release_authorized":false}),
    )
}

fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let root = admit()?;
    let result = match args.first().map(String::as_str) {
        Some("alab-returned-cut") if args.len() == 9 => alab_commit_returned_cut(
            &owned_path(&root,&args[1],true)?, &owned_path(&root,&args[2],true)?,
            &owned_path(&root,&args[3],true)?, &owned_path(&root,&args[4],true)?,
            &owned_path(&root,&args[5],true)?, &owned_path(&root,&args[6],true)?, &args[7],
            &owned_path(&root,&args[8],false)?, &alab_expected())?,
        Some("alab-returned-readback") if args.len() == 5 => alab_edited_readback(
            &owned_path(&root,&args[1],true)?, &owned_path(&root,&args[2],true)?,
            &owned_path(&root,&args[3],true)?, &args[4], &alab_expected())?,
        Some("alab-edited-bundle") if args.len() == 8 => alab_edited_bundle(
            &owned_path(&root,&args[1],true)?, &owned_path(&root,&args[2],true)?,
            &owned_path(&root,&args[3],true)?, &args[4], &owned_path(&root,&args[5],false)?,
            &owned_path(&root,&args[6],false)?, &owned_path(&root,&args[7],false)?, &alab_expected())?,
        Some("alab-edited-portable-readback") if args.len() == 4 => alab_edited_portable_readback(
            &owned_path(&root,&args[1],true)?, &owned_path(&root,&args[2],true)?, &args[3], &alab_expected())?,
        Some("alab-adopt") if args.len() == 10 => {
            let database = owned_path(&root, &args[1], false)?;
            let otio = owned_path(&root, &args[2], true)?;
            let media0 = owned_path(&root, &args[3], true)?;
            let media1 = owned_path(&root, &args[4], true)?;
            let readback = owned_path(&root, &args[5], true)?;
            let container0 = owned_path(&root, &args[6], true)?;
            let container1 = owned_path(&root, &args[7], true)?;
            let derivation = owned_path(&root, &args[8], true)?;
            let receipt = owned_path(&root, &args[9], false)?;
            alab_adopt_with_expected(&database, &AlabFiles {
                originals: [&otio, &readback, &container0, &container1],
                media: [&media0, &media1], derivation: &derivation,
            }, &receipt, &alab_expected())?
        },
        Some("alab-source-readback") if args.len() == 3 => alab_source_readback(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?, &alab_expected())?,
        Some("alab-source-bundle") if args.len() == 6 => alab_source_bundle(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?,
            &owned_path(&root, &args[3], false)?,
            &owned_path(&root, &args[4], false)?,
            &owned_path(&root, &args[5], false)?, &alab_expected())?,
        Some("alab-source-portable-readback") if args.len() == 3 => alab_source_portable_readback(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?, &alab_expected())?,
        Some("seed") if args.len() == 4 => seed(
            &owned_path(&root, &args[1], false)?,
            &owned_path(&root, &args[2], true)?, &args[3])?,
        Some("commit") if args.len() == 6 => commit(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?,
            &owned_path(&root, &args[3], true)?,
            &owned_path(&root, &args[4], true)?,
            &owned_path(&root, &args[5], false)?)?,
        Some("readback") if args.len() == 4 => readback(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?,
            &owned_path(&root, &args[3], true)?)?,
        Some("source-attach") if args.len() == 7 => attach_native_source(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?,
            &owned_path(&root, &args[3], true)?,
            &owned_path(&root, &args[4], true)?, &args[5],
            &owned_path(&root, &args[6], false)?)?,
        Some("source-readback") if args.len() == 5 => native_source_readback(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?,
            &owned_path(&root, &args[3], true)?,
            &owned_path(&root, &args[4], true)?)?,
        Some("source-bundle") if args.len() == 8 => portable_native_source(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?,
            &owned_path(&root, &args[3], true)?,
            &owned_path(&root, &args[4], true)?,
            &owned_path(&root, &args[5], false)?,
            &owned_path(&root, &args[6], false)?,
            &owned_path(&root, &args[7], false)?)?,
        Some("source-portable-readback") if args.len() == 3 => portable_native_source_readback(
            &owned_path(&root, &args[1], true)?,
            &owned_path(&root, &args[2], true)?)?,
        _ => return Err("Expected seed DB MP4 SHA | commit DB SOURCE PREVIEW RAW RECEIPT | readback DB SOURCE RECEIPT | source-attach DB SOURCE CUT_RECEIPT NATIVE_FILE NATIVE_SHA RECEIPT | source-readback DB SOURCE CUT_RECEIPT ATTACH_RECEIPT | source-bundle DB SOURCE CUT_RECEIPT ATTACH_RECEIPT BUNDLE_DIR DESTINATION_DB RECEIPT | source-portable-readback DESTINATION_DB BUNDLE_RECEIPT | alab-adopt DB OTIO MOV0 MOV1 READBACK CONTAINER0 CONTAINER1 DERIVED RECEIPT | alab-source-readback DB ADOPTION_RECEIPT | alab-source-bundle DB ADOPTION_RECEIPT BUNDLE_DIR DESTINATION_DB RECEIPT | alab-source-portable-readback DESTINATION_DB BUNDLE_RECEIPT | alab-returned-cut DB ADOPTION_RECEIPT SOURCE PREVIEW RAW EDITED_KDENLIVE SHA RECEIPT | alab-returned-readback DB ADOPTION_RECEIPT EDIT_RECEIPT EXPECTED_SHA | alab-edited-bundle DB ADOPTION_RECEIPT EDIT_RECEIPT EXPECTED_SHA BUNDLE DESTINATION_DB RECEIPT | alab-edited-portable-readback DESTINATION_DB BUNDLE_RECEIPT EXPECTED_SHA".into()),
    };
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use motionwright_domain::{OTIO_PREVIEW_SCHEMA, OtioPropertyFidelity};

    fn prepared() -> (
        tempfile::TempDir,
        PathBuf,
        PathBuf,
        PathBuf,
        PathBuf,
        Project,
    ) {
        let root = tempfile::tempdir().unwrap();
        let database = root.path().join("original.sqlite3");
        let media = root.path().join("unit-source.mp4");
        // These bytes test CAS retention, not a codec. G2 probes a real FFmpeg MP4.
        fs::write(
            &media,
            b"Unit content-addressed asset; no physical video claim",
        )
        .unwrap();
        let body = seed(&database, &media, &sha(&fs::read(&media).unwrap())).unwrap();
        let source: Project = serde_json::from_value(body["project"].clone()).unwrap();
        let source_path = root.path().join("source.json");
        new_json(&source_path, &serde_json::to_value(&source).unwrap()).unwrap();
        let ids = source.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
        let rows = [
            ("scene_order", "editable_otio", "bounded_proposal_only"),
            (
                "scene_duration",
                "editable_otio",
                "bounded_frame_exact_end_trim_only",
            ),
            (
                "source_video_media",
                "verified_external_reference",
                "source_sha_revalidated_no_new_media",
            ),
            (
                "media_source_in_point",
                "represented_in_otio",
                "not_supported_native_scene_offset",
            ),
            (
                "unbound_scene_media",
                "missing_reference",
                "cannot_invent_media",
            ),
            (
                "native_object_geometry_hierarchy_text_masks",
                "source_project_only",
                "not_supported",
            ),
            (
                "camera_keyframes_effects_and_procedural_controls",
                "source_project_only",
                "not_supported",
            ),
            (
                "source_audio_and_voice_cues",
                "source_project_only",
                "not_roundtripped_as_nle_audio",
            ),
            (
                "captions_transcripts_alignment",
                "source_project_only",
                "not_roundtripped",
            ),
            (
                "human_locks_creative_reviews_branches",
                "source_project_only",
                "not_roundtripped",
            ),
            (
                "runtime_render_authority",
                "not_exported",
                "requires_separate_owner_grant",
            ),
            (
                "publication_rights",
                "not_granted",
                "requires_separate_owner_approval",
            ),
        ];
        let preview = OtioEditPreview {
            schema: OTIO_PREVIEW_SCHEMA.into(),
            source_project_sha256: sha(&fs::read(&source_path).unwrap()),
            project_id: source.id,
            generation: source.generation,
            expected_project_revision: source.revision,
            original_scene_order: ids.clone(),
            proposed_scene_order: ids.clone(),
            native_change_proposals: vec![OtioNativeCut::SetSceneDuration {
                scene_id: ids[2],
                duration: RationalTime::new(1, 2).unwrap(),
            }],
            media_refs_verified: 3,
            missing_references_retained: 0,
            loss_report: vec!["Editable geometry and source-only properties remain native".into()],
            property_fidelity: rows
                .into_iter()
                .map(|(property, status, import_state)| OtioPropertyFidelity {
                    property: property.into(),
                    status: status.into(),
                    import_state: import_state.into(),
                    missing_count: (property == "unbound_scene_media").then_some(0),
                })
                .collect(),
            edit_imported: false,
            source_media_relinked: false,
            owner_revision_commit_required: true,
            human_review_required: true,
            rendering_performed: false,
            semantic_native_roundtrip: "NOT_SUPPORTED".into(),
            nle_receiver_acceptance: "NOT_VERIFIED".into(),
            publication_approved: false,
        };
        let preview_path = root.path().join("owner-preview.json");
        let preview_value = serde_json::to_value(preview).unwrap();
        new_json(&preview_path, &preview_value).unwrap();
        let mut raw = preview_value;
        raw["schema"] = json!("motionwright.kdenlive-bound-cut-preview/1");
        raw["profile"] = json!("kdenlive-bound-video-cut-v1");
        raw["status"] = json!("BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED");
        raw["original_missing_scene_ids"] = json!([]);
        raw["raw_files_rewritten"] = json!(false);
        raw["native_final_frames_verified"] = json!(false);
        raw["native_project_reopened"] = json!("NOT_VERIFIED");
        raw["received_clip_count"] = json!(3);
        raw["edited_clip_count"] = json!(3);
        raw.as_object_mut().unwrap().remove("rendering_performed");
        raw.as_object_mut()
            .unwrap()
            .remove("missing_references_retained");
        let raw_path = root.path().join("returned-preview.json");
        new_json(&raw_path, &raw).unwrap();
        (root, database, source_path, preview_path, raw_path, source)
    }

    #[test]
    fn sqlite_owner_cut_commits_once_and_reopens_preserving_editable_source() {
        let (root, database, source_path, preview_path, raw_path, source) = prepared();
        let receipt_path = root.path().join("commit.json");
        let receipt = commit(
            &database,
            &source_path,
            &preview_path,
            &raw_path,
            &receipt_path,
        )
        .unwrap();
        assert_eq!(receipt["single_revision_committed"], true);
        assert_eq!(receipt["human_acceptance"], false);
        let observation = readback(&database, &source_path, &receipt_path).unwrap();
        assert_eq!(observation["project_reopened_equal"], true);
        assert_eq!(observation["canonical_project_mutation_performed"], false);
        let service = StudioService::open(&database).unwrap();
        let actual = service.project(source.id).unwrap();
        assert_eq!(actual.revision, source.revision + 1);
        assert_eq!(actual.scenes[2].duration, RationalTime::new(1, 2).unwrap());
        assert!(!source.scenes[2].nodes.is_empty());
        assert_eq!(actual.scenes[2].nodes, source.scenes[2].nodes);
        assert_eq!(actual.assets, source.assets);
        assert_eq!(
            service
                .history(source.id, source.revision, 2)
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn retained_unknown_intent_prevents_any_second_apply() {
        let (root, database, source_path, preview_path, raw_path, source) = prepared();
        let receipt_path = root.path().join("commit.json");
        let intent_path = receipt_path.with_extension("intent.json");
        new_json(
            &intent_path,
            &json!({"state":"UNKNOWN","original_operation":"never retry"}),
        )
        .unwrap();
        let intent_bytes = fs::read(&intent_path).unwrap();
        assert!(
            commit(
                &database,
                &source_path,
                &preview_path,
                &raw_path,
                &receipt_path
            )
            .is_err()
        );
        let service = StudioService::open(&database).unwrap();
        assert_eq!(service.project(source.id).unwrap(), source);
        assert!(
            service
                .history(source.id, source.revision, 2)
                .unwrap()
                .is_empty()
        );
        assert_eq!(fs::read(&intent_path).unwrap(), intent_bytes);
        assert!(!receipt_path.exists());
    }

    #[test]
    fn concurrent_source_change_rejects_before_owner_intent_or_commit() {
        let (root, database, source_path, preview_path, raw_path, source) = prepared();
        let service = StudioService::open(&database).unwrap();
        let concurrent = service
            .apply(
                source.id,
                &source.stamp(),
                "unit:concurrent-change",
                &Change::RenameProject {
                    title: "Concurrent original owner change".into(),
                },
            )
            .unwrap()
            .project;
        drop(service);
        let receipt_path = root.path().join("commit.json");
        assert!(
            commit(
                &database,
                &source_path,
                &preview_path,
                &raw_path,
                &receipt_path
            )
            .is_err()
        );
        assert!(!receipt_path.exists());
        assert!(!receipt_path.with_extension("intent.json").exists());
        let service = StudioService::open(&database).unwrap();
        assert_eq!(service.project(source.id).unwrap(), concurrent);
        assert!(
            service
                .history(source.id, concurrent.revision, 2)
                .unwrap()
                .is_empty()
        );
    }

    struct NativeSourceFixture {
        root: tempfile::TempDir,
        database: PathBuf,
        source_path: PathBuf,
        cut_receipt: PathBuf,
        native_path: PathBuf,
        source: Project,
        before: Project,
    }

    fn native_source_fixture() -> NativeSourceFixture {
        let (root, database, source_path, preview_path, raw_path, source) = prepared();
        let cut_receipt = root.path().join("commit.json");
        let receipt = commit(
            &database,
            &source_path,
            &preview_path,
            &raw_path,
            &cut_receipt,
        )
        .unwrap();
        let before = serde_json::from_value(receipt["committed_project"].clone()).unwrap();
        let native_path = root.path().join(NATIVE_SOURCE_NAME);
        // Synthetic opaque bytes test the canonical source/CAS boundary.
        // They are not Kdenlive process, parse, render or editable-parameter evidence.
        fs::write(&native_path,
            b"<mlt><!-- original opaque comment --><unknown:effect vendor='opaque'>raw&amp;value</unknown:effect></mlt>"
        ).unwrap();
        NativeSourceFixture {
            root,
            database,
            source_path,
            cut_receipt,
            native_path,
            source,
            before,
        }
    }

    #[test]
    fn native_capsule_source_commits_one_additional_revision_and_reopens_original_bytes() {
        let f = native_source_fixture();
        let original = fs::read(&f.native_path).unwrap();
        let digest = sha(&original);
        let receipt_path = f.root.path().join("source-attach.json");
        let receipt = attach_native_source(
            &f.database,
            &f.source_path,
            &f.cut_receipt,
            &f.native_path,
            &digest,
            &receipt_path,
        )
        .unwrap();
        assert_eq!(receipt["single_source_revision_committed"], true);
        assert_eq!(receipt["source_fidelity_unavailable"], true);
        let observed =
            native_source_readback(&f.database, &f.source_path, &f.cut_receipt, &receipt_path)
                .unwrap();
        assert_eq!(observed["project_reopened_equal"], true);
        assert_eq!(observed["exact_two_history_events"], true);
        assert_eq!(observed["canonical_project_mutation_performed"], false);
        let service = StudioService::open(&f.database).unwrap();
        let current = service.project(f.source.id).unwrap();
        assert_eq!(current.revision, f.source.revision + 2);
        assert_eq!(current.scenes, f.before.scenes);
        assert_eq!(current.assets.len(), 2);
        let capsule = &current.production_design.capsules[0];
        assert!(capsule.editable_parameters.is_empty());
        assert!(capsule.property_fidelity.is_empty());
        assert_eq!(capsule.fidelity.editable, Fidelity::Unavailable);
        let store = service.store();
        let path = store.lock().verified_blob_path(&digest).unwrap();
        assert_eq!(fs::read(path).unwrap(), original);
        assert_eq!(fs::read(&f.native_path).unwrap(), original);
        assert_eq!(current.created_at, f.before.created_at);
        let events = service.history(f.source.id, f.source.revision, 3).unwrap();
        let event = events
            .iter()
            .find(|item| item.revision == current.revision)
            .unwrap();
        assert_eq!(event.created_at, current.updated_at.to_rfc3339());
        // Only the observed updated_at may differ. Authored creation time and
        // the exact committed history timestamp retain their independent checks.
        let mut changed_creation = serde_json::to_value(&current).unwrap();
        changed_creation["created_at"] = json!("2000-01-01T00:00:00Z");
        let changed_creation: Project = serde_json::from_value(changed_creation).unwrap();
        assert!(check_source_attachment(&f.before, &changed_creation, &digest).is_err());
        let mut changed_history = receipt.clone();
        let last = changed_history["history"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|item| item["revision"] == current.revision)
            .unwrap();
        last["created_at"] = json!("2000-01-01T00:00:00+00:00");
        let changed_receipt = f.root.path().join("source-attach-history-changed.json");
        new_json(&changed_receipt, &changed_history).unwrap();
        assert!(
            native_source_readback(
                &f.database,
                &f.source_path,
                &f.cut_receipt,
                &changed_receipt
            )
            .is_err()
        );
    }

    #[test]
    fn native_capsule_wrong_original_digest_refuses_before_intent_or_mutation() {
        let f = native_source_fixture();
        let receipt = f.root.path().join("source-attach.json");
        assert!(
            attach_native_source(
                &f.database,
                &f.source_path,
                &f.cut_receipt,
                &f.native_path,
                &"0".repeat(64),
                &receipt
            )
            .is_err()
        );
        assert!(!receipt.exists());
        assert!(!receipt.with_extension("intent.json").exists());
        let service = StudioService::open(&f.database).unwrap();
        assert_eq!(service.project(f.source.id).unwrap(), f.before);
        assert_eq!(
            service
                .history(f.source.id, f.source.revision, 3)
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn retained_source_import_unknown_intent_prevents_any_replay() {
        let f = native_source_fixture();
        let receipt = f.root.path().join("source-attach.json");
        new_json(
            &receipt.with_extension("intent.json"),
            &json!({"state":"UNKNOWN"}),
        )
        .unwrap();
        let digest = sha(&fs::read(&f.native_path).unwrap());
        assert!(
            attach_native_source(
                &f.database,
                &f.source_path,
                &f.cut_receipt,
                &f.native_path,
                &digest,
                &receipt
            )
            .is_err()
        );
        let service = StudioService::open(&f.database).unwrap();
        assert_eq!(service.project(f.source.id).unwrap(), f.before);
        assert_eq!(
            service
                .history(f.source.id, f.source.revision, 3)
                .unwrap()
                .len(),
            1
        );
        assert!(!receipt.exists());
    }

    #[test]
    fn changed_owner_cut_project_cannot_mint_a_source_import_intent() {
        let f = native_source_fixture();
        let service = StudioService::open(&f.database).unwrap();
        let changed = service
            .apply(
                f.before.id,
                &f.before.stamp(),
                "unit:changed-after-cut",
                &Change::RenameProject {
                    title: "Changed owner after cut".into(),
                },
            )
            .unwrap()
            .project;
        drop(service);
        let receipt = f.root.path().join("source-attach.json");
        let digest = sha(&fs::read(&f.native_path).unwrap());
        assert!(
            attach_native_source(
                &f.database,
                &f.source_path,
                &f.cut_receipt,
                &f.native_path,
                &digest,
                &receipt
            )
            .is_err()
        );
        assert!(!receipt.with_extension("intent.json").exists());
        let service = StudioService::open(&f.database).unwrap();
        assert_eq!(service.project(f.source.id).unwrap(), changed);
        assert!(
            service
                .history(changed.id, changed.revision, 1)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn portable_native_source_uses_inspection_and_survives_new_generation_without_local_receipts() {
        let f = native_source_fixture();
        let original = fs::read(&f.native_path).unwrap();
        let digest = sha(&original);
        let attached = f.root.path().join("source-attach.json");
        attach_native_source(
            &f.database,
            &f.source_path,
            &f.cut_receipt,
            &f.native_path,
            &digest,
            &attached,
        )
        .unwrap();
        // Source-only negative control: bundle/readback must use the retained
        // CAS blob. The G2 producer never deletes its original native evidence.
        fs::remove_file(&f.native_path).unwrap();
        let bundle = f.root.path().join("source-bundle");
        let restored_owner = f.root.path().join("restored-owner");
        fs::create_dir(&restored_owner).unwrap();
        let restored_database = restored_owner.join("motionwright.sqlite3");
        let receipt_path = f.root.path().join("portable.json");
        let receipt = portable_native_source(
            &f.database,
            &f.source_path,
            &f.cut_receipt,
            &attached,
            &bundle,
            &restored_database,
            &receipt_path,
        )
        .unwrap();
        assert_eq!(receipt["fresh_generation_observed"], true);
        assert_eq!(receipt["source_assets_and_capsules_preserved"], true);
        assert_eq!(receipt["local_production_receipts_inherited"], false);
        assert_eq!(receipt["execution_authority_issued"], false);
        let source_project: Project =
            serde_json::from_value(receipt["source_project"].clone()).unwrap();
        let imported_project: Project =
            serde_json::from_value(receipt["imported_project"].clone()).unwrap();
        assert_eq!(source_project.created_at, imported_project.created_at);
        assert_eq!(
            receipt["source_updated_at"],
            serde_json::to_value(source_project.updated_at).unwrap()
        );
        assert_eq!(
            receipt["imported_updated_at"],
            serde_json::to_value(imported_project.updated_at).unwrap()
        );
        let mut renamed = imported_project.clone();
        renamed.title.push_str(" foreign change");
        assert!(check_portable_source_project(&source_project, &renamed).is_err());
        let mut foreign_asset = imported_project.clone();
        foreign_asset.assets[0].id = Uuid::from_u128(0x00fe_ed99);
        assert!(check_portable_source_project(&source_project, &foreign_asset).is_err());
        // Both databases have separate adjacent CAS roots. Removing only the
        // synthetic original fixture's blobs cannot break the restored owner.
        let source_service = StudioService::open(&f.database).unwrap();
        let restored_service = StudioService::open(&restored_database).unwrap();
        for asset in &source_project.assets {
            let digest = asset.content_sha256.as_deref().unwrap();
            let source_store = source_service.store();
            let source_blob = source_store.lock().verified_blob_path(digest).unwrap();
            let restored_store = restored_service.store();
            let restored_blob = restored_store.lock().verified_blob_path(digest).unwrap();
            assert_ne!(source_blob, restored_blob);
            fs::remove_file(source_blob).unwrap();
        }
        drop(source_service);
        drop(restored_service);
        let observation =
            portable_native_source_readback(&restored_database, &receipt_path).unwrap();
        assert_eq!(observation["project_reopened_equal"], true);
        assert_eq!(observation["source_blob_reopened_equal"], true);
        assert_eq!(observation["canonical_project_mutation_performed"], false);
        let restored = StudioService::open(&restored_database).unwrap();
        let project = restored.project(f.source.id).unwrap();
        assert_ne!(project.generation, f.source.generation);
        assert_eq!(
            project.production_design.capsules[0].fidelity.editable,
            Fidelity::Unavailable
        );
        assert_eq!(
            restored
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
        let store = restored.store();
        let path = store.lock().verified_blob_path(&digest).unwrap();
        assert_eq!(fs::read(path).unwrap(), original);
    }

    struct AlabUnitFixture {
        _root: tempfile::TempDir,
        database: PathBuf,
        originals: [PathBuf; 4],
        media: [PathBuf; 2],
        derived: PathBuf,
        receipt: PathBuf,
        expected: AlabExpected,
    }

    impl AlabUnitFixture {
        fn files(&self) -> AlabFiles<'_> {
            AlabFiles {
                originals: std::array::from_fn(|index| self.originals[index].as_path()),
                media: std::array::from_fn(|index| self.media[index].as_path()),
                derivation: &self.derived,
            }
        }

        fn adopt(&self) -> Value {
            alab_adopt_with_expected(&self.database, &self.files(), &self.receipt, &self.expected)
                .unwrap()
        }
    }

    fn alab_unit_derived(expected: &AlabExpected) -> Value {
        // Synthetic source-shaped descriptors exercise the private caller seam.
        // The public CLI fixes real original hashes; these are no codec evidence.
        let selected = (0..2).map(|index| json!({
            "pointer":ALAB_POINTERS[index],"target_url":ALAB_TARGETS[index],
            "source_sha256":expected.media[index].sha256,"source_bytes":expected.media[index].size_bytes,
            "media_type":"video/quicktime","source_fps":24,"width":1920,"height":1080,
            "codec":"h264","source_origin_frame":ALAB_ORIGINS[index],"source_start_frame":0,
            "duration_frames":ALAB_FRAMES[index],"actual_media_frames":ALAB_FRAMES[index],
            "container_timecode":if index == 0 { "00:00:41:18" } else { "00:00:42:00" },
            "rgb_samples_retained":3,"all_decoded_frame_pixel_hashes":false,
        })).collect::<Vec<_>>();
        json!({"schema":"motionwright.alab-retained-original-cut/1","profile":ALAB_PROFILE,
            "status":"RETAINED_MEDIA_ORIGIN_DERIVED_NOT_ADOPTED",
            "originals":alab_original_descriptors(expected),
            "source_inspection":{"schema":"motionwright.external-otio-source-inspection/1",
                "status":"SOURCE_INSPECTED_NOT_ADOPTED","source":{"sha256":expected.originals[0].sha256,
                "bytes":expected.originals[0].size_bytes},"verified_media":false,"media_origin":"unknown",
                "opaque_regions":[{"pointer":"/metadata","reason":"synthetic opaque original"}],
                "observations":(0..2).map(|index| json!({"pointer":ALAB_POINTERS[index],"schema":"Clip.2",
                    "track_kind":"Video","range_supported_by_existing_cut_parser":true,
                    "active_reference":{"target_url":ALAB_TARGETS[index]}})).collect::<Vec<_>>()},
            "selected":selected,
            "source_origin_basis":"matching original OTIO range, retained complete video PTS and matching format/tmcd 24fps non-drop-frame timecodes",
            "historical_source_origin":"NOT_OBSERVED","historical_readback_rewritten":false,
            "new_decode":false,"new_render":false,"assets_adopted":false,"canonical_scene_ids_created":false,
            "nle_opened":false,"formal_acceptance":false,
            "remaining_source_references":"opaque/unavailable for this selected two-occurrence profile",
            "later_edit_proposal":{"occurrence":ALAB_POINTERS[1],"from_frames":182,"to_frames":170,"applied":false}})
    }

    fn alab_unit_fixture() -> AlabUnitFixture {
        let root = tempfile::tempdir().unwrap();
        let originals = [
            "source.otio",
            "readback.json",
            "container0.json",
            "container1.json",
        ]
        .map(|name| root.path().join(name));
        let media = ALAB_SOURCE_NAMES.map(|name| root.path().join(name));
        let source_bytes: [&[u8]; 4] = [
            b"whole opaque original OTIO unit source",
            b"unit original readback",
            b"unit original container zero",
            b"unit original container one",
        ];
        let media_bytes: [&[u8]; 2] = [b"unit source MOV bytes zero", b"unit source MOV bytes one"];
        for (path, bytes) in originals.iter().zip(source_bytes) {
            fs::write(path, bytes).unwrap();
        }
        for (path, bytes) in media.iter().zip(media_bytes) {
            fs::write(path, bytes).unwrap();
        }
        let expected = AlabExpected {
            originals: std::array::from_fn(|index| {
                alab_blob(&sha(source_bytes[index]), source_bytes[index].len() as u64)
            }),
            media: std::array::from_fn(|index| {
                alab_blob(&sha(media_bytes[index]), media_bytes[index].len() as u64)
            }),
        };
        let derived = root.path().join("derived.json");
        new_json(&derived, &alab_unit_derived(&expected)).unwrap();
        AlabUnitFixture {
            database: root.path().join("source.sqlite3"),
            receipt: root.path().join("adoption.json"),
            _root: root,
            originals,
            media,
            derived,
            expected,
        }
    }

    #[test]
    fn alab_source_adoption_uses_real_service_journal_cas_and_independent_sqlite_reopen() {
        let fixture = alab_unit_fixture();
        let originals = fixture
            .originals
            .iter()
            .map(|path| fs::read(path).unwrap())
            .collect::<Vec<_>>();
        let returned = fixture.adopt();
        let project: Project = serde_json::from_value(returned["project"].clone()).unwrap();
        assert_eq!(returned["original_history_events"], 7);
        assert_eq!(returned["canonical_source_adopted"], true);
        assert_eq!(returned["source_fidelity_unavailable"], true);
        assert_eq!(returned["new_decode"], false);
        assert_eq!(returned["nle_opened"], false);
        assert_eq!(project.scenes.len(), 2);
        assert_eq!(project.assets.len(), 3);
        assert_eq!(project.production_design.capsules.len(), 1);
        assert_eq!(
            project.scenes[1].duration,
            RationalTime::new(91, 12).unwrap()
        );
        let reopened =
            alab_source_readback(&fixture.database, &fixture.receipt, &fixture.expected).unwrap();
        assert_eq!(reopened["project_reopened_equal"], true);
        assert_eq!(
            reopened["project_value_sha256"],
            returned["project_value_sha256"]
        );
        assert_eq!(reopened["source_assets"], returned["source_assets"]);
        for (path, original) in fixture.originals.iter().zip(&originals) {
            assert_eq!(fs::read(path).unwrap(), *original);
        }
        // Only this disposable synthetic fixture removes its source input files.
        // Canonical readback must use retained CAS, not reread a media pathname.
        for path in fixture.originals.iter().chain(fixture.media.iter()) {
            fs::remove_file(path).unwrap();
        }
        assert_eq!(
            alab_source_readback(&fixture.database, &fixture.receipt, &fixture.expected).unwrap(),
            reopened
        );
    }

    #[test]
    fn alab_invalid_original_identity_is_rejected_before_database_or_mutation_intent() {
        for changed in 0..6 {
            let fixture = alab_unit_fixture();
            let path = if changed < 4 {
                &fixture.originals[changed]
            } else {
                &fixture.media[changed - 4]
            };
            fs::write(path, b"substituted source bytes").unwrap();
            assert!(
                alab_adopt_with_expected(
                    &fixture.database,
                    &fixture.files(),
                    &fixture.receipt,
                    &fixture.expected
                )
                .is_err()
            );
            assert!(!fixture.database.exists());
            assert!(!fixture.receipt.with_extension("intent.json").exists());
            assert!(!fixture.receipt.exists());
        }
    }

    #[test]
    fn alab_derived_profile_cannot_promote_flags_change_intervals_or_self_admit_a_source() {
        for case in 0..8 {
            let fixture = alab_unit_fixture();
            let mut value = alab_unit_derived(&fixture.expected);
            match case {
                0 => value["formal_acceptance"] = json!(true),
                1 => value["assets_adopted"] = json!(true),
                2 => value["originals"][0]["sha256"] = json!("0".repeat(64)),
                3 => value["selected"][1]["duration_frames"] = json!(170),
                4 => value["selected"][0]["source_start_frame"] = json!(1),
                5 => value["selected"][0]["media_type"] = json!("video/mp4"),
                6 => value["later_edit_proposal"]["applied"] = json!(true),
                _ => value["undeclared"] = json!("cannot expand the profile"),
            }
            fs::write(&fixture.derived, serde_json::to_vec(&value).unwrap()).unwrap();
            assert!(
                alab_adopt_with_expected(
                    &fixture.database,
                    &fixture.files(),
                    &fixture.receipt,
                    &fixture.expected
                )
                .is_err(),
                "case {case}"
            );
            assert!(!fixture.database.exists());
            assert!(!fixture.receipt.with_extension("intent.json").exists());
        }
    }

    #[test]
    fn alab_same_stage_verified_import_rejects_substitution_without_asset_commit_even_if_custody_fails()
     {
        let fixture = alab_unit_fixture();
        let service = StudioService::open(&fixture.database).unwrap();
        let before = service
            .create_project("Disposable verified-source substitution control")
            .unwrap();
        let expected = &fixture.expected.media[0];
        alab_check_descriptor(&fixture.media[0], expected, 256 * 1024 * 1024).unwrap();
        fs::write(&fixture.media[0], b"substituted after admitted source read").unwrap();
        let original_record = fixture
            .receipt
            .with_extension("stage-00.result.original.json");
        fs::write(
            &original_record,
            b"preexisting retained record; never overwrite",
        )
        .unwrap();
        let mut stages = Vec::new();
        let error = alab_stage(
            &service,
            &before,
            "unit:alab-source-substitution",
            &fixture.receipt,
            0,
            &mut stages,
            || {
                Ok(service.import_file_verified(
                    before.id,
                    &before.stamp(),
                    "unit:alab-source-substitution",
                    &fixture.media[0],
                    expected,
                    motionwright_storage::FileImportIntent::Asset {
                        name: ALAB_SOURCE_NAMES[0].into(),
                        media_type: "video/quicktime".into(),
                    },
                )?)
            },
        )
        .unwrap_err();
        assert!(
            error
                .downcast_ref::<motionwright_storage::StorageError>()
                .is_some()
        );
        assert!(error.to_string().contains("expected SHA-256 or byte count"));
        assert_eq!(service.project(before.id).unwrap(), before);
        assert!(
            service
                .history(before.id, before.revision, 2)
                .unwrap()
                .is_empty()
        );
        assert!(
            service
                .store()
                .lock()
                .verified_blob_path(&expected.sha256)
                .is_err()
        );
        assert!(stages.is_empty());
        assert_eq!(
            fs::read(&original_record).unwrap(),
            b"preexisting retained record; never overwrite"
        );
        assert!(
            fixture
                .receipt
                .with_extension("stage-00.intent.json")
                .exists()
        );
    }

    #[test]
    fn alab_reopen_checks_each_original_request_outcome_against_actual_sqlite_history() {
        for changed in [0usize, 4, 6] {
            let fixture = alab_unit_fixture();
            let returned = fixture.adopt();
            let source: Project = serde_json::from_value(returned["project"].clone()).unwrap();
            let path = fixture
                .receipt
                .with_extension(format!("stage-{changed:02}.result.original.json"));
            let mut stage: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
            stage["request_id"] = json!("another-source-request");
            let raw = serde_json::to_vec(&stage).unwrap();
            fs::write(&path, &raw).unwrap();
            // Even a coordinated change to the containing digest cannot make
            // a foreign durable request agree with the fixed original roster.
            let mut receipt = returned.clone();
            receipt["stage_receipts"][changed]["bytes"] = json!(raw.len());
            receipt["stage_receipts"][changed]["sha256"] = json!(sha(&raw));
            fs::write(&fixture.receipt, serde_json::to_vec(&receipt).unwrap()).unwrap();
            assert!(
                alab_source_readback(&fixture.database, &fixture.receipt, &fixture.expected)
                    .is_err()
            );
            let service = StudioService::open(&fixture.database).unwrap();
            assert_eq!(service.project(source.id).unwrap(), source);
            assert_eq!(
                service
                    .history(source.id, returned["initial_revision"].as_u64().unwrap(), 8)
                    .unwrap()
                    .len(),
                7
            );
        }
    }

    #[test]
    fn alab_portable_source_reopens_all_originals_from_a_separate_owner_cas_with_new_generation() {
        let fixture = alab_unit_fixture();
        let returned = fixture.adopt();
        let root = fixture.database.parent().unwrap();
        let bundle = root.join("bundle");
        let restored_owner = root.join("restored-owner");
        fs::create_dir(&restored_owner).unwrap();
        let destination = restored_owner.join("motionwright.sqlite3");
        let receipt_path = root.join("portability.json");
        let result = alab_source_bundle(
            &fixture.database,
            &fixture.receipt,
            &bundle,
            &destination,
            &receipt_path,
            &fixture.expected,
        )
        .unwrap();
        assert_ne!(result["source_generation"], result["imported_generation"]);
        assert_eq!(result["source_assets"], returned["source_assets"]);
        assert_eq!(result["local_production_receipts_inherited"], false);
        assert_eq!(result["execution_authority_issued"], false);
        let source = StudioService::open(&fixture.database).unwrap();
        let restored = StudioService::open(&destination).unwrap();
        for descriptor in fixture
            .expected
            .media
            .iter()
            .chain(std::iter::once(&fixture.expected.originals[0]))
        {
            let old_path = source
                .store()
                .lock()
                .verified_blob_path(&descriptor.sha256)
                .unwrap();
            let new_path = restored
                .store()
                .lock()
                .verified_blob_path(&descriptor.sha256)
                .unwrap();
            assert_ne!(old_path, new_path);
            // Synthetic fixture only: deleting the original CAS proves the
            // imported owner has its own source bytes, not an accidental alias.
            fs::remove_file(old_path).unwrap();
        }
        drop(source);
        drop(restored);
        let final_readback =
            alab_source_portable_readback(&destination, &receipt_path, &fixture.expected).unwrap();
        assert_eq!(final_readback["all_source_blobs_reopened_equal"], true);
        assert_eq!(final_readback["source_assets"], returned["source_assets"]);
        assert_eq!(final_readback["source_fidelity_unavailable"], true);
        assert_eq!(final_readback["native_editor_dispatched_here"], false);
    }

    #[test]
    fn alab_completed_attempt_is_not_reseeded_and_false_native_claims_never_pass_readback() {
        let fixture = alab_unit_fixture();
        let returned = fixture.adopt();
        let original_receipt = fs::read(&fixture.receipt).unwrap();
        assert!(
            alab_adopt_with_expected(
                &fixture.database,
                &fixture.files(),
                &fixture.receipt,
                &fixture.expected
            )
            .is_err()
        );
        assert_eq!(fs::read(&fixture.receipt).unwrap(), original_receipt);
        let source: Project = serde_json::from_value(returned["project"].clone()).unwrap();
        let mut changed = returned;
        changed["nle_opened"] = json!(true);
        fs::write(&fixture.receipt, serde_json::to_vec(&changed).unwrap()).unwrap();
        assert!(
            alab_source_readback(&fixture.database, &fixture.receipt, &fixture.expected).is_err()
        );
        let service = StudioService::open(&fixture.database).unwrap();
        assert_eq!(service.project(source.id).unwrap(), source);
    }

    #[test]
    fn original_three_scene_source_capture_rejects_substitution_before_cas_or_revision_even_if_custody_fails()
     {
        for same_size in [true, false] {
            let f = native_source_fixture();
            let observed = read_regular(&f.native_path, NATIVE_SOURCE_LIMIT).unwrap();
            let observed_sha = sha(&observed);
            let request = format!("original:kdenlive-capsule:{observed_sha}");
            let mut replacement = observed.clone();
            replacement[0] ^= 1;
            if !same_size {
                replacement.push(b'!');
            }
            let replacement_sha = sha(&replacement);
            // The exact original caller passes these observed bytes to the
            // helper below. Substitute the file after that observation and
            // before the SAME Service staged capture, not before preflight.
            fs::write(&f.native_path, &replacement).unwrap();
            let result_path = f.root.path().join("source-import.return.original.json");
            fs::write(&result_path, b"existing diagnostic must remain unchanged").unwrap();
            let service = StudioService::open(&f.database).unwrap();
            let error = import_observed_native_capsule_source(
                &service,
                &f.before,
                &request,
                &f.native_path,
                &observed_sha,
                &observed,
                native_source_spec(&f.before).unwrap(),
                &result_path,
            )
            .unwrap_err();
            assert!(
                matches!(error.downcast_ref::<motionwright_storage::StorageError>(),
                Some(motionwright_storage::StorageError::Domain(motionwright_domain::DomainError::Invalid(message)))
                    if message.contains("expected SHA-256 or byte count"))
            );
            assert_eq!(service.project(f.before.id).unwrap(), f.before);
            assert!(
                service
                    .history(f.before.id, f.before.revision, 2)
                    .unwrap()
                    .is_empty()
            );
            assert_eq!(
                service
                    .history(f.source.id, f.source.revision, 3)
                    .unwrap()
                    .len(),
                1
            );
            assert!(
                service
                    .store()
                    .lock()
                    .verified_blob_path(&observed_sha)
                    .is_err()
            );
            assert!(
                service
                    .store()
                    .lock()
                    .verified_blob_path(&replacement_sha)
                    .is_err()
            );
            assert_eq!(
                fs::read(&result_path).unwrap(),
                b"existing diagnostic must remain unchanged"
            );
            assert_eq!(fs::read(&f.native_path).unwrap(), replacement);
        }
    }

    struct AlabReturnedFixture {
        source_fixture: AlabUnitFixture,
        source: Project,
        source_path: PathBuf,
        preview: PathBuf,
        raw: PathBuf,
        native: PathBuf,
        receipt: PathBuf,
    }

    impl AlabReturnedFixture {
        fn commit(&self) -> Result<Value> {
            alab_commit_returned_cut(
                &self.source_fixture.database,
                &self.source_fixture.receipt,
                &self.source_path,
                &self.preview,
                &self.raw,
                &self.native,
                &sha(&fs::read(&self.native)?),
                &self.receipt,
                &self.source_fixture.expected,
            )
        }
    }

    fn alab_returned_fixture() -> AlabReturnedFixture {
        let fixture = alab_unit_fixture();
        let adopted = fixture.adopt();
        let source: Project = serde_json::from_value(adopted["project"].clone()).unwrap();
        let parent = fixture.database.parent().unwrap();
        let source_path = parent.join("source-project.json");
        new_json(&source_path, &serde_json::to_value(&source).unwrap()).unwrap();
        // Reuse the existing twelve-property owner-review fixture shape. Its
        // identities and selected cut are replaced with this real Service source.
        let (_template_root, _db, _source, template, _raw, _project) = prepared();
        let mut preview: OtioEditPreview =
            serde_json::from_slice(&fs::read(template).unwrap()).unwrap();
        preview.source_project_sha256 = sha(&fs::read(&source_path).unwrap());
        preview.project_id = source.id;
        preview.generation = source.generation;
        preview.expected_project_revision = source.revision;
        preview.original_scene_order = source.scenes.iter().map(|scene| scene.id).collect();
        preview.proposed_scene_order = preview.original_scene_order.clone();
        preview.native_change_proposals = vec![OtioNativeCut::SetSceneDuration {
            scene_id: source.scenes[1].id,
            duration: RationalTime::new(85, 12).unwrap(),
        }];
        preview.media_refs_verified = 2;
        let preview_path = parent.join("alab-owner-cut-preview.json");
        let value = serde_json::to_value(preview).unwrap();
        new_json(&preview_path, &value).unwrap();
        let mut raw = value;
        raw["schema"] = json!("motionwright.kdenlive-bound-cut-preview/1");
        raw["profile"] = json!("kdenlive-bound-video-cut-v1");
        raw["status"] = json!("BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED");
        raw["original_missing_scene_ids"] = json!([]);
        raw["raw_files_rewritten"] = json!(false);
        raw["native_final_frames_verified"] = json!(false);
        raw["native_project_reopened"] = json!("NOT_VERIFIED");
        raw["received_clip_count"] = json!(2);
        raw["edited_clip_count"] = json!(2);
        raw.as_object_mut()
            .unwrap()
            .remove("missing_references_retained");
        raw.as_object_mut().unwrap().remove("rendering_performed");
        let raw_path = parent.join("alab-returned-cut-preview.json");
        new_json(&raw_path, &raw).unwrap();
        let native = parent.join("edited.kdenlive");
        fs::write(
            &native,
            b"<mlt><!-- synthetic opaque edited source, no NLE evidence --></mlt>",
        )
        .unwrap();
        let receipt = parent.join("alab-returned-source.json");
        AlabReturnedFixture {
            source_fixture: fixture,
            source,
            source_path,
            preview: preview_path,
            raw: raw_path,
            native,
            receipt,
        }
    }

    #[test]
    fn alab_returned_cut_and_native_source_use_two_real_revisions_and_four_blobs_survive_portable_reopen()
     {
        let fixture = alab_returned_fixture();
        let result = fixture.commit().unwrap();
        let original_receipt_sha = sha(&fs::read(&fixture.receipt).unwrap());
        let final_project: Project =
            serde_json::from_value(result["committed_project"].clone()).unwrap();
        assert_eq!(final_project.revision, fixture.source.revision + 2);
        assert_eq!(final_project.scenes[0], fixture.source.scenes[0]);
        assert_eq!(
            final_project.scenes[1].duration,
            RationalTime::new(85, 12).unwrap()
        );
        assert_eq!(&final_project.assets[..3], &fixture.source.assets);
        assert_eq!(final_project.assets.len(), 4);
        assert_eq!(final_project.production_design.capsules.len(), 2);
        assert_eq!(result["exact_additional_history_events"], 2);
        assert_eq!(result["native_editor_dispatched_here"], false);
        let readback = alab_edited_readback(
            &fixture.source_fixture.database,
            &fixture.source_fixture.receipt,
            &fixture.receipt,
            &original_receipt_sha,
            &fixture.source_fixture.expected,
        )
        .unwrap();
        assert_eq!(readback["exact_nine_history_events"], true);
        assert_eq!(readback["all_four_source_blobs_reopened_equal"], true);
        let parent = fixture.receipt.parent().unwrap();
        let bundle = parent.join("edited-portable");
        let owner = parent.join("edited-restored-owner");
        fs::create_dir(&owner).unwrap();
        let destination = owner.join("motionwright.sqlite3");
        let portable = parent.join("edited-portability.json");
        let exported = alab_edited_bundle(
            &fixture.source_fixture.database,
            &fixture.source_fixture.receipt,
            &fixture.receipt,
            &original_receipt_sha,
            &bundle,
            &destination,
            &portable,
            &fixture.source_fixture.expected,
        )
        .unwrap();
        assert_eq!(exported["source_assets"], result["source_assets"]);
        assert_ne!(
            exported["source_generation"],
            exported["imported_generation"]
        );
        let source = StudioService::open(&fixture.source_fixture.database).unwrap();
        let restored = StudioService::open(&destination).unwrap();
        for asset in &final_project.assets {
            let digest = asset.content_sha256.as_deref().unwrap();
            let original = source.store().lock().verified_blob_path(digest).unwrap();
            let imported = restored.store().lock().verified_blob_path(digest).unwrap();
            assert_ne!(original, imported);
            // Only this synthetic test removes its old owner's copies.
            fs::remove_file(original).unwrap();
        }
        drop(source);
        drop(restored);
        let portable_sha = sha(&fs::read(&portable).unwrap());
        let final_readback = alab_edited_portable_readback(
            &destination,
            &portable,
            &portable_sha,
            &fixture.source_fixture.expected,
        )
        .unwrap();
        assert_eq!(final_readback["all_four_source_blobs_reopened_equal"], true);
        assert_eq!(final_readback["source_fidelity_unavailable"], true);
        assert_eq!(final_readback["local_production_receipts_inherited"], false);
        assert_eq!(final_readback["execution_authority_issued"], false);
    }

    #[test]
    fn alab_unselected_or_promoted_return_rejects_before_cut_intent_and_original_history_change() {
        for defect in 0..5 {
            let fixture = alab_returned_fixture();
            let mut preview: Value =
                serde_json::from_slice(&fs::read(&fixture.preview).unwrap()).unwrap();
            let mut raw: Value = serde_json::from_slice(&fs::read(&fixture.raw).unwrap()).unwrap();
            match defect {
                0 => {
                    preview["native_change_proposals"][0]["scene_id"] =
                        json!(fixture.source.scenes[0].id);
                    raw["native_change_proposals"] = preview["native_change_proposals"].clone();
                }
                1 => {
                    preview["native_change_proposals"][0]["duration"] =
                        json!({"num":"1","den":"2"});
                    raw["native_change_proposals"] = preview["native_change_proposals"].clone();
                }
                2 => raw["native_final_frames_verified"] = json!(true),
                3 => raw["received_clip_count"] = json!(3),
                _ => {
                    preview["property_fidelity"].as_array_mut().unwrap().pop();
                    raw["property_fidelity"] = preview["property_fidelity"].clone();
                }
            }
            fs::write(&fixture.preview, serde_json::to_vec(&preview).unwrap()).unwrap();
            fs::write(&fixture.raw, serde_json::to_vec(&raw).unwrap()).unwrap();
            assert!(fixture.commit().is_err());
            assert!(!fixture.receipt.with_extension("intent.json").exists());
            let service = StudioService::open(&fixture.source_fixture.database).unwrap();
            assert_eq!(service.project(fixture.source.id).unwrap(), fixture.source);
            assert!(
                service
                    .history(fixture.source.id, fixture.source.revision, 3)
                    .unwrap()
                    .is_empty()
            );
        }
    }

    #[test]
    fn alab_unknown_return_intent_or_completed_source_never_replays_a_cut_or_capsule() {
        let fixture = alab_returned_fixture();
        let intent = fixture.receipt.with_extension("intent.json");
        fs::write(&intent, b"{\"state\":\"UNKNOWN\",\"do_not_retry\":true}").unwrap();
        let original = fs::read(&intent).unwrap();
        assert!(fixture.commit().is_err());
        assert_eq!(fs::read(&intent).unwrap(), original);
        let service = StudioService::open(&fixture.source_fixture.database).unwrap();
        assert_eq!(service.project(fixture.source.id).unwrap(), fixture.source);
        assert!(
            service
                .history(fixture.source.id, fixture.source.revision, 3)
                .unwrap()
                .is_empty()
        );
        // A separate fixture exercises a known completed attempt, never the
        // retained UNKNOWN above. There is no automatic replay branch.
        let complete = alab_returned_fixture();
        let result = complete.commit().unwrap();
        let before = fs::read(&complete.receipt).unwrap();
        assert!(complete.commit().is_err());
        assert_eq!(fs::read(&complete.receipt).unwrap(), before);
        let project: Project = serde_json::from_value(result["committed_project"].clone()).unwrap();
        let service = StudioService::open(&complete.source_fixture.database).unwrap();
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service
                .history(project.id, complete.source.revision, 3)
                .unwrap()
                .len(),
            2
        );
    }

    #[test]
    fn alab_new_process_joins_each_cut_and_source_outcome_to_the_original_nine_event_journal() {
        for stage in [7usize, 8] {
            let fixture = alab_returned_fixture();
            let mut result = fixture.commit().unwrap();
            let original_receipt_sha = sha(&fs::read(&fixture.receipt).unwrap());
            let path = fixture
                .receipt
                .with_extension(format!("stage-{stage:02}.result.original.json"));
            let mut returned: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
            returned["request_id"] = json!("foreign-returned-source-request");
            let raw = serde_json::to_vec(&returned).unwrap();
            fs::write(&path, &raw).unwrap();
            result["stage_receipts"][stage - 7]["sha256"] = json!(sha(&raw));
            result["stage_receipts"][stage - 7]["bytes"] = json!(raw.len());
            fs::write(&fixture.receipt, serde_json::to_vec(&result).unwrap()).unwrap();
            assert!(
                alab_edited_readback(
                    &fixture.source_fixture.database,
                    &fixture.source_fixture.receipt,
                    &fixture.receipt,
                    &original_receipt_sha,
                    &fixture.source_fixture.expected
                )
                .is_err()
            );
            let project: Project =
                serde_json::from_value(result["committed_project"].clone()).unwrap();
            let service = StudioService::open(&fixture.source_fixture.database).unwrap();
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(
                service
                    .history(project.id, fixture.source.revision, 3)
                    .unwrap()
                    .len(),
                2
            );
        }
        // An independently observed original SHA also rejects a coherent change
        // of the claimed raw preview, derived request and its retained outcome.
        // SQLite's ProjectEvent does not itself contain a request_id.
        let fixture = alab_returned_fixture();
        let mut result = fixture.commit().unwrap();
        let original_receipt_sha = sha(&fs::read(&fixture.receipt).unwrap());
        let changed_preview = sha(b"foreign original preview identity");
        let changed_request = format!("original:alab-kdenlive-cut:{changed_preview}");
        result["original_Kdenlive_preview_sha256"] = json!(changed_preview);
        result["cut_request_id"] = json!(changed_request);
        let path = fixture
            .receipt
            .with_extension("stage-07.result.original.json");
        let mut outcome: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        outcome["request_id"] = json!(changed_request);
        let raw = serde_json::to_vec(&outcome).unwrap();
        fs::write(&path, &raw).unwrap();
        result["stage_receipts"][0]["request_id"] = json!(changed_request);
        result["stage_receipts"][0]["sha256"] = json!(sha(&raw));
        result["stage_receipts"][0]["bytes"] = json!(raw.len());
        fs::write(&fixture.receipt, serde_json::to_vec(&result).unwrap()).unwrap();
        let error = alab_edited_readback(
            &fixture.source_fixture.database,
            &fixture.source_fixture.receipt,
            &fixture.receipt,
            &original_receipt_sha,
            &fixture.source_fixture.expected,
        )
        .unwrap_err();
        assert!(error.to_string().contains("caller-retained original SHA"));
        let service = StudioService::open(&fixture.source_fixture.database).unwrap();
        assert_eq!(
            service
                .history(fixture.source.id, fixture.source.revision, 3)
                .unwrap()
                .len(),
            2
        );
    }

    #[test]
    fn alab_concurrent_source_change_after_native_return_is_not_overwritten_or_rebased() {
        let fixture = alab_returned_fixture();
        let service = StudioService::open(&fixture.source_fixture.database).unwrap();
        let concurrent = service
            .apply(
                fixture.source.id,
                &fixture.source.stamp(),
                "unit:concurrent-source",
                &Change::RenameProject {
                    title: "Concurrent owner source change".into(),
                },
            )
            .unwrap()
            .project;
        drop(service);
        assert!(fixture.commit().is_err());
        assert!(!fixture.receipt.with_extension("intent.json").exists());
        let service = StudioService::open(&fixture.source_fixture.database).unwrap();
        assert_eq!(service.project(concurrent.id).unwrap(), concurrent);
        assert!(
            service
                .history(concurrent.id, concurrent.revision, 3)
                .unwrap()
                .is_empty()
        );
    }
}
