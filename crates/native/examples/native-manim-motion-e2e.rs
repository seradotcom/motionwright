//! Source/portability phases for the Manim motion lane. Each command preserves
//! its original artifacts. Provider start/status/result are deliberately routed
//! by the existing coordinator after registration of the v3 plan variant; this
//! executable cannot silently fall back to v1/v2 or run imported Python.
use motionwright_domain::{
    Asset, CanvasKeyframe, CanvasNode, Change, CoordinateSpace, Fidelity, FidelityReport,
    MotionInterpolation, MotionProperty, NativeCapsule, NodeStyle, Project, RationalTime,
    RendererKind, RevisionStamp,
};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2, SOURCE_BUNDLE_MIME_V2,
    planar_motion::{
        MAX_MOTION_READBACK_BYTES_V3, ManimMotionPropertyV3, ManimSourcePackageV3,
        inspect_motion_readback_v3, inspect_source_bundle_v3, source_bundle_v3,
    },
};
use motionwright_native::manim_projection::{
    apply_saved_manim_motion_edit, inspect_saved_manim_motion_source,
    prepare_saved_manim_motion_source, preview_saved_manim_motion_edit,
};
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const RED: Uuid = Uuid::from_u128(0x20000000000040008000000000000001);
const GREEN: Uuid = Uuid::from_u128(0x20000000000040008000000000000002);
const PHASES: [&str; 7] = [
    "original",
    "reopened",
    "edited",
    "edited-reopened",
    "edge25",
    "camera",
    "camera-reopened",
];

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    if bytes.is_empty() || !path.parent().is_some_and(Path::is_dir) {
        return Err("Manim motion output must be nonempty with an existing explicit parent".into());
    }
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    if read_bounded(path, bytes.len() as u64)? != bytes {
        return Err("Manim motion source output readback differs".into());
    }
    Ok(())
}

fn read_bounded(path: &Path, maximum: u64) -> Result<Vec<u8>> {
    let entry = fs::symlink_metadata(path)?;
    if !entry.file_type().is_file() || entry.len() == 0 || entry.len() > maximum {
        return Err("Manim motion input is not a bounded nonempty regular file".into());
    }
    let file = fs::File::open(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() != entry.len() {
        return Err("Manim motion input changed before reading".into());
    }
    let mut bytes = Vec::with_capacity(entry.len() as usize);
    file.take(maximum + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 != entry.len() || fs::symlink_metadata(path)?.len() != entry.len() {
        return Err("Manim motion input changed while reading".into());
    }
    Ok(bytes)
}

fn one_project(service: &StudioService) -> Result<Project> {
    let mut rows = service.projects(2)?;
    if rows.len() != 1 {
        return Err("Manim motion fixture requires exactly one Project".into());
    }
    Ok(rows.remove(0))
}

fn apply(service: &StudioService, project: &Project, operation: Change) -> Result<Project> {
    Ok(service
        .apply(
            project.id,
            &RevisionStamp::from(project),
            &format!("manim-motion-source-change-{}", project.revision),
            &operation,
        )?
        .project)
}

fn new_service(database: &Path) -> Result<StudioService> {
    if database.try_exists()? {
        return Err("Manim motion seed/import requires a new database path".into());
    }
    if !database.parent().is_some_and(Path::is_dir) {
        return Err("Manim motion database parent must already exist".into());
    }
    Ok(StudioService::open(database)?)
}

fn current_source(
    service: &StudioService,
    project: &Project,
) -> Result<(NativeCapsule, Vec<u8>, ManimSourcePackageV3)> {
    let sources = project
        .production_design
        .capsules
        .iter()
        .filter(|c| {
            project
                .assets
                .iter()
                .any(|a| a.id == c.source_asset_id && a.media_type == SOURCE_BUNDLE_MIME_V2)
        })
        .collect::<Vec<_>>();
    if sources.len() != 1 {
        return Err("Manim motion fixture needs one current source capsule".into());
    }
    let capsule = sources[0].clone();
    inspect_saved_manim_motion_source(
        service,
        project.id,
        &RevisionStamp::from(project),
        capsule.source_asset_id,
    )?;
    let bytes = service
        .store()
        .lock()
        .read_blob(&capsule.source_sha256, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)?;
    if digest(&bytes) != capsule.source_sha256 {
        return Err("Manim motion source CAS digest differs".into());
    }
    let package = inspect_source_bundle_v3(&bytes)?;
    Ok((capsule, bytes, package))
}

fn add_carrier(
    service: &StudioService,
    project: &Project,
    path: &Path,
    bytes: &[u8],
) -> Result<(Project, Uuid)> {
    inspect_source_bundle_v3(bytes)?;
    write_new(path, bytes)?;
    let blob = service.ingest_blob_file(path)?;
    if blob.sha256 != digest(bytes) {
        return Err("Manim motion source ingestion differs".into());
    }
    let id = Uuid::now_v7();
    let asset = Asset {
        id,
        name: path
            .file_name()
            .ok_or("source carrier name missing")?
            .to_string_lossy()
            .into_owned(),
        media_type: SOURCE_BUNDLE_MIME_V2.into(),
        content_sha256: Some(blob.sha256),
        source_revision: Some(project.revision.to_string()),
    };
    Ok((apply(service, project, Change::AddAsset { asset })?, id))
}

fn seed(database: &Path, edge25: bool) -> Result<()> {
    let service = new_service(database)?;
    let mut p = service.create_project("Original editable Manim planar motion")?;
    p = apply(
        &service,
        &p,
        Change::AddScene {
            name: "Two authored moving shapes".into(),
            objective: "Observe six controls at every rational frame and reopen editable source"
                .into(),
            duration_seconds: 2,
        },
    )?;
    let scene_id = p.scenes[0].id;
    p = apply(
        &service,
        &p,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    )?;
    let mut profile = p.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    profile.frame_rate = RationalTime::new(if edge25 { 25 } else { 12 }, 1)?;
    p = apply(&service, &p, Change::UpsertDeliverable { profile })?;
    if edge25 {
        p = apply(
            &service,
            &p,
            Change::SetSceneDuration {
                scene_id,
                duration: RationalTime::new(29, 25)?,
            },
        )?;
    }
    let mut language = p.visual_language.clone();
    language
        .palette
        .iter_mut()
        .find(|t| t.name == "surface")
        .ok_or("surface token absent")?
        .value = "#000000".into();
    p = apply(
        &service,
        &p,
        Change::SetVisualLanguage {
            visual_language: language,
        },
    )?;
    for (id, kind, x, y, w, h, fill, z) in [
        (GREEN, "circle", 750.0, 390.0, 240.0, 240.0, "#20CC40", 20),
        (RED, "rectangle", 240.0, 180.0, 600.0, 360.0, "#E02040", 10),
    ] {
        let node = CanvasNode {
            id,
            name: format!("Original {kind}"),
            kind: kind.into(),
            parent_id: None,
            x,
            y,
            width: w,
            height: h,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: z,
            style: NodeStyle {
                fill: Some(fill.into()),
                ..NodeStyle::default()
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        p = apply(&service, &p, Change::AddCanvasNode { scene_id, node })?;
    }
    for (property, value, interpolation) in [
        (MotionProperty::X, 600.0, MotionInterpolation::Linear),
        (MotionProperty::Y, 300.0, MotionInterpolation::Hold),
        (
            MotionProperty::Width,
            840.0,
            MotionInterpolation::EaseOutCubic,
        ),
        (
            MotionProperty::Height,
            480.0,
            MotionInterpolation::EaseInOut,
        ),
        (
            MotionProperty::RotationDeg,
            90.0,
            MotionInterpolation::EaseInOut,
        ),
        (MotionProperty::Opacity, 0.25, MotionInterpolation::Linear),
    ] {
        p = apply(
            &service,
            &p,
            Change::SetCanvasKeyframe {
                scene_id,
                node_id: RED,
                keyframe: CanvasKeyframe {
                    at: RationalTime::new(1, 1)?,
                    property,
                    value,
                    interpolation,
                },
            },
        )?;
    }
    let source = prepare_saved_manim_motion_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        scene_id,
        p.deliverables[0].id,
    )?;
    let bytes = source_bundle_v3(&source.plan)?;
    let path = database
        .parent()
        .ok_or("database parent absent")?
        .join("manim-motion-original.mwmsrc.zip");
    let (next, source_asset_id) = add_carrier(&service, &p, &path, &bytes)?;
    p = next;
    let capsule = NativeCapsule {
        id: Uuid::now_v7(),
        scene_id,
        source_asset_id,
        source_sha256: digest(&bytes),
        label: "Editable planar motion source awaiting native evidence".into(),
        fidelity: FidelityReport {
            renderer: "manim-community".into(),
            renderer_version: "0.21.0".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Translated,
            editable: Fidelity::Translated,
            losses: vec![
                "Source preparation does not verify the midpoint sampler or native media".into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: vec![
            "node.transform".into(),
            "node.keyframe".into(),
            "scene.camera.static".into(),
        ],
        native_editor_hint: "Edit typed plan data and regenerate with the trusted compiler".into(),
        property_fidelity: vec![],
    };
    p = apply(&service, &p, Change::UpsertNativeCapsule { capsule })?;
    let inspected = inspect_saved_manim_motion_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        source_asset_id,
    )?;
    println!(
        "{}",
        json!({"phase":if edge25 { "edge25" } else { "original" }, "project_id":p.id,
        "generation":p.generation,"revision":p.revision,"source":inspected,"native_execution_performed":false})
    );
    Ok(())
}

fn export(database: &Path, destination: &Path) -> Result<()> {
    let service = StudioService::open(database)?;
    let p = one_project(&service)?;
    let (capsule, _, _) = current_source(&service, &p)?;
    let before = service.production_receipt_watermark(p.id)?;
    let manifest = service.export_project_bundle(p.id, destination)?;
    if service.project(p.id)? != p || service.production_receipt_watermark(p.id)? != before {
        return Err("Manim motion source export changed Project or native journal".into());
    }
    println!(
        "{}",
        json!({"project_id":p.id,"source_asset_id":capsule.source_asset_id,"bundle":manifest,
        "native_execution_performed":false,"imported_receipts_used_as_authority":false})
    );
    Ok(())
}

fn import(database: &Path, bundle: &Path) -> Result<()> {
    let service = new_service(database)?;
    let p = service.import_project_bundle(bundle)?;
    let (capsule, _, package) = current_source(&service, &p)?;
    if p.generation == package.plan.binding.generation
        || !service.production_receipts(p.id, 1)?.is_empty()
    {
        return Err(
            "Manim motion import did not rotate generation or contains native receipts".into(),
        );
    }
    let inspection = inspect_saved_manim_motion_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        capsule.source_asset_id,
    )?;
    println!(
        "{}",
        json!({"project_id":p.id,"generation":p.generation,"revision":p.revision,"source":inspection,
        "native_execution_performed":false,"native_reopen_verified":false})
    );
    Ok(())
}

fn edit(database: &Path, camera: bool) -> Result<()> {
    let service = StudioService::open(database)?;
    let p = one_project(&service)?;
    let (mut capsule, _, mut source) = current_source(&service, &p)?;
    if camera {
        if source.plan.camera.zoom != 1.0
            || source.plan.camera.center_x != 960.0
            || source.plan.camera.center_y != 540.0
        {
            return Err("Manim motion original camera has already changed".into());
        }
        source.plan.camera.center_x = 1000.0;
        source.plan.camera.center_y = 510.0;
        source.plan.camera.zoom = 0.8;
        source.plan.camera.safe_margin = 0.1;
    } else {
        let key = source
            .plan
            .nodes
            .iter_mut()
            .find(|n| n.node_id == RED)
            .ok_or("original RED identity absent")?
            .keyframes
            .iter_mut()
            .find(|k| {
                k.property == ManimMotionPropertyV3::X
                    && k.at == RationalTime::new(1, 1).expect("constant rational")
            })
            .ok_or("original X key absent")?;
        if key.value != 600.0 {
            return Err("Manim motion original X key has already changed".into());
        }
        key.value = 720.0;
    }
    let bytes = source_bundle_v3(&source.plan)?;
    let name = if camera {
        "manim-motion-camera.mwmsrc.zip"
    } else {
        "manim-motion-edited.mwmsrc.zip"
    };
    let path = database
        .parent()
        .ok_or("database parent absent")?
        .join(name);
    let (p, edited_id) = add_carrier(&service, &p, &path, &bytes)?;
    let before = service.production_receipt_watermark(p.id)?;
    let preview = preview_saved_manim_motion_edit(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        capsule.source_asset_id,
        edited_id,
    )?;
    let imported = apply_saved_manim_motion_edit(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        if camera {
            "manim-motion-import-original-camera"
        } else {
            "manim-motion-import-original-key"
        },
        capsule.source_asset_id,
        edited_id,
    )?;
    // One separately recorded association follows the one canonical edit. The
    // import API itself never updates a capsule or creates a native request.
    capsule.source_asset_id = edited_id;
    capsule.source_sha256 = digest(&bytes);
    let current = apply(&service, &imported, Change::UpsertNativeCapsule { capsule })?;
    let inspected = inspect_saved_manim_motion_source(
        &service,
        current.id,
        &RevisionStamp::from(&current),
        edited_id,
    )?;
    if service.production_receipt_watermark(p.id)? != before {
        return Err("Manim motion source edit changed native journal".into());
    }
    println!(
        "{}",
        json!({"project_id":current.id,"generation":current.generation,"revision":current.revision,
        "source_import_preview":preview,"source":inspected,"canonical_edit_import_applied":true,
        "separate_capsule_association_applied":true,"native_execution_performed":false})
    );
    Ok(())
}

fn prepare(database: &Path, destination: &Path, phase: &str) -> Result<()> {
    if !PHASES.contains(&phase)
        || !destination.is_dir()
        || fs::read_dir(destination)?.next().is_some()
    {
        return Err(
            "Manim motion preparation requires a closed phase and an empty explicit directory"
                .into(),
        );
    }
    let service = StudioService::open(database)?;
    let p = one_project(&service)?;
    let before = service.production_receipt_watermark(p.id)?;
    let (capsule, origin_bytes, _) = current_source(&service, &p)?;
    let source = inspect_saved_manim_motion_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        capsule.source_asset_id,
    )?;
    let package = prepare_saved_manim_motion_source(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        source.current_binding.scene_id,
        source.current_binding.deliverable_id,
    )?;
    let bytes = source_bundle_v3(&package.plan)?;
    if source.prepared_source != package.closure || source.prepared_bundle_sha256 != digest(&bytes)
    {
        return Err("Manim motion preparation changed its source closure".into());
    }
    // No empty marker is an artifact. All files are exclusive, nonempty and
    // read back after fsync. Source preparation cannot create a provider grant.
    for (name, bytes) in &package.entries {
        write_new(&destination.join(name), bytes)?;
    }
    write_new(&destination.join("source.mwmsrc.zip"), &bytes)?;
    write_new(&destination.join("origin.mwmsrc.zip"), &origin_bytes)?;
    if service.project(p.id)? != p || service.production_receipt_watermark(p.id)? != before {
        return Err("Manim motion preparation lost its original Project/journal snapshot".into());
    }
    let report = json!({"schema_version":1,"kind":"motionwright.manim.planar_motion.preparation","phase":phase,
        "project":p,"source":source,"native_execution_performed":false,"native_readback_verified":false,
        "provider_job_custody_verified":false});
    let mut report_bytes = serde_json::to_vec_pretty(&report)?;
    report_bytes.push(b'\n');
    write_new(&destination.join("prepared.json"), &report_bytes)?;
    println!(
        "{}",
        json!({"phase":phase,"prepared_record_sha256":digest(&report_bytes),
        "source_bundle_sha256":digest(&bytes),"source_files":8,"native_execution_performed":false})
    );
    Ok(())
}

fn inspect_native(source: &Path, trace: &Path) -> Result<()> {
    let source_bytes = read_bounded(source, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)?;
    let package = inspect_source_bundle_v3(&source_bytes)?;
    let native = read_bounded(trace, MAX_MOTION_READBACK_BYTES_V3 as u64)?;
    let observed = inspect_motion_readback_v3(&package.plan, &native)?;
    println!(
        "{}",
        json!({"source_bundle_sha256":digest(&source_bytes),"source":package.closure,
        "native_getter_sha256":digest(&native),"sample_count":observed.sample_count,
        "canonical_source_archive_verified":true,"native_getter_samples_verified":true,
        "decoded_video_verified":false,"provider_job_custody_verified":false,"native_execution_performed":false})
    );
    Ok(())
}

fn main() -> Result<()> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    let command = args.first().and_then(|s| s.to_str()).unwrap_or("");
    let path = |index: usize| PathBuf::from(&args[index]);
    match (command, args.len()) {
        ("seed", 2) => seed(&path(1), false),
        ("seed-edge25", 2) => seed(&path(1), true),
        ("export", 3) => export(&path(1), &path(2)),
        ("import", 3) => import(&path(1), &path(2)),
        ("edit-key", 2) => edit(&path(1), false),
        ("edit-camera", 2) => edit(&path(1), true),
        ("prepare", 4) => prepare(&path(1), &path(2), args[3].to_str().ok_or("phase must be UTF-8")?),
        ("inspect-native", 3) => inspect_native(&path(1), &path(2)),
        _ => Err("usage: native-manim-motion-e2e <seed DB | seed-edge25 DB | export DB BUNDLE | import NEW_DB BUNDLE | edit-key DB | edit-camera DB | prepare DB EMPTY_DIR PHASE | inspect-native SOURCE_ZIP GETTER_JSON>; provider realization uses the existing v3-enabled coordinator".into()),
    }
}
