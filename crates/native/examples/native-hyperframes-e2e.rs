//! Source stage of the HyperFrames native lane. No native job is started here.
//!
//! `source-fixture <new-directory>` emits original finite native HTML, a sealed
//! carrier, a real Project/Store/CAS bundle, unchanged and edited reopen inputs,
//! and pixel probes for the independent native lane. The final report explicitly
//! leaves provider execution, whole-medium readback and native edited reopen
//! unverified. A2's canonical renderer selection and the owner Host installation
//! are separate integration gates, never inferred from this fixture.

use motionwright_domain::{
    Asset, Change, Fidelity, FidelityReport, NativeCapsule, Project, RevisionStamp,
};
use motionwright_hyperframes_profile::{
    LayerContent, LayerTransform, Rational, SourceClock, SourceDocument, SourceLayer,
    author_source, carrier_bytes, sha256,
};
use motionwright_native::production::hyperframes_source::{
    HYPERFRAMES_SOURCE_MIME, HyperframesSourceSelection, inspect_saved_hyperframes_source,
    prepare_native_hyperframes_request, prepare_saved_hyperframes_source,
    preview_saved_hyperframes_source_reopen,
};
use motionwright_service::StudioService;
use serde_json::{Value, json};
use std::{error::Error, fs, io::Write, path::Path};
use uuid::Uuid;

type ExampleResult<T> = Result<T, Box<dyn Error>>;

fn write_new(path: &Path, bytes: &[u8]) -> ExampleResult<()> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn write_json(path: &Path, value: &Value) -> ExampleResult<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    write_new(path, &bytes)
}

fn apply(service: &StudioService, project: &Project, change: Change) -> ExampleResult<Project> {
    Ok(service
        .apply(
            project.id,
            &RevisionStamp::from(project),
            &format!("hf-source-fixture-{}", project.revision),
            &change,
        )?
        .project)
}

fn document() -> SourceDocument {
    SourceDocument {
        clock: SourceClock {
            fps: Rational::new(30, 1).expect("fixed rate"),
            frame_count: 60,
        },
        width: 640,
        height: 360,
        background: "#101020".into(),
        layers: vec![
            SourceLayer {
                id: Uuid::from_u128(0x1001),
                start_frame: 0,
                end_frame: 30,
                width: 180,
                height: 120,
                transform: LayerTransform {
                    x: 40,
                    y: 50,
                    ..LayerTransform::default()
                },
                content: LayerContent::Rectangle {
                    color: "#E02040".into(),
                    radius: 0,
                },
            },
            SourceLayer {
                id: Uuid::from_u128(0x1002),
                start_frame: 30,
                end_frame: 60,
                width: 180,
                height: 120,
                transform: LayerTransform {
                    x: 300,
                    y: 130,
                    ..LayerTransform::default()
                },
                content: LayerContent::Rectangle {
                    color: "#20CC40".into(),
                    radius: 0,
                },
            },
        ],
    }
}

fn add_asset(
    service: &StudioService,
    project: &Project,
    path: &Path,
    name: &str,
) -> ExampleResult<(Project, Uuid)> {
    let blob = service.ingest_blob_file(path)?;
    let asset = Asset {
        id: Uuid::now_v7(),
        name: name.into(),
        media_type: HYPERFRAMES_SOURCE_MIME.into(),
        content_sha256: Some(blob.sha256),
        source_revision: Some(project.revision.to_string()),
    };
    let id = asset.id;
    Ok((apply(service, project, Change::AddAsset { asset })?, id))
}

fn source_fixture(destination: &Path) -> ExampleResult<Value> {
    // An existing destination is never reused, cleared or considered retryable.
    fs::create_dir(destination)?;
    let original = author_source(document(), vec![])?;
    let original_bytes = carrier_bytes(&original)?;
    let original_sha256 = sha256(&original_bytes);
    write_new(&destination.join("original.mwhf.json"), &original_bytes)?;
    write_new(&destination.join("index.html"), original.html.as_bytes())?;
    write_new(&destination.join("reopened.mwhf.json"), &original_bytes)?;
    let mut edited_document = document();
    edited_document.layers[1].transform.x += 40;
    let edited = author_source(edited_document, vec![])?;
    let edited_bytes = carrier_bytes(&edited)?;
    write_new(&destination.join("edited.mwhf.json"), &edited_bytes)?;
    write_new(&destination.join("edited.html"), edited.html.as_bytes())?;
    let source_mount = destination.join("source-mount");
    fs::create_dir(&source_mount)?;
    write_new(
        &source_mount.join(format!("mw-hf-{original_sha256}.json")),
        &original_bytes,
    )?;

    let service = StudioService::open(destination.join("motionwright.sqlite3"))?;
    let mut project = service.create_project("Original finite HyperFrames source study")?;
    project = apply(
        &service,
        &project,
        Change::AddScene {
            name: "Two original timed cards".into(),
            objective: "Check timed native pixels and edited source independently".into(),
            duration_seconds: 2,
        },
    )?;
    let mut profile = project.deliverables[0].clone();
    profile.width = original.document.width;
    profile.height = original.document.height;
    profile.frame_rate = original.document.clock.fps;
    profile.captions = false;
    project = apply(&service, &project, Change::UpsertDeliverable { profile })?;
    let (next, original_asset_id) = add_asset(
        &service,
        &project,
        &destination.join("original.mwhf.json"),
        "Original finite HyperFrames source",
    )?;
    project = next;
    let capsule = NativeCapsule {
        id: Uuid::now_v7(),
        scene_id: project.scenes[0].id,
        source_asset_id: original_asset_id,
        source_sha256: original_sha256.clone(),
        label: "Finite HTML source awaiting native evidence".into(),
        fidelity: FidelityReport {
            renderer: "hyperframes".into(),
            renderer_version: "0.8.167".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Native,
            losses: vec![
                "Native pixels, owner execution and edited native reopen remain unverified".into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: vec!["source.layer.transform".into()],
        native_editor_hint: "Original HTML in a finite, resource-closed carrier".into(),
        property_fidelity: vec![],
    };
    let selection = HyperframesSourceSelection {
        scene_id: capsule.scene_id,
        capsule_id: capsule.id,
        deliverable_id: project.deliverables[0].id,
    };
    project = apply(&service, &project, Change::UpsertNativeCapsule { capsule })?;
    let (next, reopened_asset_id) = add_asset(
        &service,
        &project,
        &destination.join("reopened.mwhf.json"),
        "Unchanged source reopen",
    )?;
    project = next;
    let (project, edited_asset_id) = add_asset(
        &service,
        &project,
        &destination.join("edited.mwhf.json"),
        "Explicit source translation edit",
    )?;
    let stamp = RevisionStamp::from(&project);
    let watermark = service.production_receipt_watermark(project.id)?;
    if watermark.count != 0 {
        return Err("Source fixture unexpectedly has production receipts".into());
    }
    let prepared = prepare_saved_hyperframes_source(&service, project.id, &stamp, &selection)?;
    let retained = prepared.retained_source_bytes(&service)?;
    if retained != original_bytes {
        return Err("CAS source bytes differ from the retained original".into());
    }
    write_new(&destination.join("exported.mwhf.json"), &retained)?;
    let reopen = preview_saved_hyperframes_source_reopen(
        &service,
        project.id,
        &stamp,
        &selection,
        reopened_asset_id,
    )?;
    let changed = preview_saved_hyperframes_source_reopen(
        &service,
        project.id,
        &stamp,
        &selection,
        edited_asset_id,
    )?;
    if !reopen.exact_carrier_bytes_reopened
        || changed.exact_carrier_bytes_reopened
        || changed.native_html_unchanged
        || reopen.canonical_import_applied
        || changed.canonical_import_applied
    {
        return Err("Source reopen fixture did not preserve its exact intended distinction".into());
    }
    // B250 lacks HyperFrames in the canonical enum. A capsule's renderer label
    // cannot select an executor, even though its finite source was admitted.
    let admission_error = prepare_native_hyperframes_request(
        &service,
        project.id,
        &stamp,
        &selection,
        Uuid::from_u128(0x9001),
        &"c".repeat(64),
    )
    .expect_err("canonical selection is not integrated in this source profile");
    if admission_error.code != semwright_native_sdk::ErrorCode::Unsupported
        || !admission_error.outcome_known
    {
        return Err("Canonical source-only gate produced an unexpected classification".into());
    }
    service.export_project_bundle(project.id, destination.join("project-bundle"))?;
    let reopened_service = StudioService::open(destination.join("reopened-project.sqlite3"))?;
    let reopened_project =
        reopened_service.import_project_bundle(destination.join("project-bundle"))?;
    let reopened_prepared = prepare_saved_hyperframes_source(
        &reopened_service,
        reopened_project.id,
        &RevisionStamp::from(&reopened_project),
        &selection,
    )?;
    if reopened_project.generation == project.generation
        || reopened_prepared.retained_source_bytes(&reopened_service)? != original_bytes
    {
        return Err("Independent Project/Store source reopen did not preserve the carrier".into());
    }
    if service.project(project.id)? != project
        || service.production_receipt_watermark(project.id)? != watermark
        || reopened_service
            .production_receipt_watermark(reopened_project.id)?
            .count
            != 0
    {
        return Err("Read-only source observations changed Project or native receipts".into());
    }

    let probes = json!([
        {"frame":0,"x":100,"y":100,"rgba":[224,32,64,255],"maximum_channel_error":12},
        {"frame":29,"x":100,"y":100,"rgba":[224,32,64,255],"maximum_channel_error":12},
        {"frame":30,"x":100,"y":100,"rgba":[16,16,32,255],"maximum_channel_error":12},
        {"frame":0,"x":320,"y":160,"rgba":[16,16,32,255],"maximum_channel_error":12},
        {"frame":30,"x":320,"y":160,"rgba":[32,204,64,255],"maximum_channel_error":12},
        {"frame":59,"x":320,"y":160,"rgba":[32,204,64,255],"maximum_channel_error":12}
    ]);
    let edited_probes = json!([
        {"frame":29,"x":100,"y":100,"rgba":[224,32,64,255],"maximum_channel_error":12},
        {"frame":30,"x":320,"y":160,"rgba":[16,16,32,255],"maximum_channel_error":12},
        {"frame":59,"x":380,"y":160,"rgba":[32,204,64,255],"maximum_channel_error":12}
    ]);
    write_json(&destination.join("pixel-probes.json"), &probes)?;
    write_json(
        &destination.join("edited-pixel-probes.json"),
        &edited_probes,
    )?;
    let report = json!({
        "schema":"motionwright.hyperframes.source-fixture/1",
        "project_stamp":stamp,"selection":selection,"source":prepared.inspection(),
        "source_sha256":original_sha256,"edited_source_sha256":sha256(&edited_bytes),
        "unchanged_reopen":reopen,"edited_reopen":changed,
        "independent_project_reopen":reopened_prepared.inspection(),
        "native_admission":{"code":admission_error.code,"outcome_known":admission_error.outcome_known,"message":admission_error.message},
        "production_receipts_before":watermark.count,"production_receipts_after":watermark.count,
        "evidence_scope":"EXECUTED_SOURCE_CAS_AND_PROJECT_BUNDLE_ONLY",
        "native_execution_performed":false,"native_pixel_fidelity_verified":false,
        "native_edited_reopen_verified":false,"export_authorized":false,
        "pending":["canonical HyperFrames renderer selection","owner-signed Host installation and access scopes",
            "native Producer run and original receipt custody","whole-medium decode and selected pixel checks",
            "native reopen render and explicit source edit render","font and image profile cases"]
    });
    write_json(&destination.join("source-fixture.json"), &report)?;
    Ok(report)
}

fn main() -> ExampleResult<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let report = match args.as_slice() {
        [mode, path] if mode == "source-fixture" => source_fixture(Path::new(path))?,
        [mode, database, project_id, scene_id, capsule_id, deliverable_id] if mode == "inspect-source" => {
            if !Path::new(database).is_file() { return Err("Inspect needs an existing application database".into()); }
            let service = StudioService::open(database)?;
            let project = service.project(Uuid::parse_str(project_id)?)?;
            let selection = HyperframesSourceSelection { scene_id: Uuid::parse_str(scene_id)?,
                capsule_id: Uuid::parse_str(capsule_id)?, deliverable_id: Uuid::parse_str(deliverable_id)? };
            serde_json::to_value(inspect_saved_hyperframes_source(&service, project.id, &RevisionStamp::from(&project), &selection)?)?
        }
        _ => return Err("Usage: native-hyperframes-e2e source-fixture <new-directory> | inspect-source <database> <project-id> <scene-id> <capsule-id> <deliverable-id>".into()),
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}
