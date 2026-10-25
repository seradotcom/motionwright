//! Disposable CI-only Kdenlive return -> existing owner review -> SQLite reopen.
//! This example adds no public API and no renderer/process executor.
use motionwright_domain::{
    Asset, CanvasNode, Change, CoordinateSpace, NodeStyle, OtioEditPreview, OtioNativeCut,
    OtioOwnerCutReview, Project, RationalTime, otio_interchange,
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
    let info = fs::symlink_metadata(path)?;
    require(
        info.is_file() && !info.file_type().is_symlink() && info.len() <= limit,
        "Input is not a bounded ordinary file",
    )?;
    let bytes = fs::read(path)?;
    require(
        bytes.len() as u64 == info.len(),
        "Input size changed while reading",
    )?;
    Ok(bytes)
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
fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let root = admit()?;
    let result = match args.first().map(String::as_str) {
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
        _ => return Err("Expected seed DB MP4 SHA | commit DB SOURCE PREVIEW RAW RECEIPT | readback DB SOURCE RECEIPT".into()),
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
}
