//! Disposable-worker acceptance. Production uses the same service/coordinator as Studio.
use motionwright_domain::{self as d, hyperframes_profile as h};
use motionwright_native::{
    native_html::prepare_hyperframes_plan,
    production::{
        HyperframesJobAction, ProductionConnection, ProductionCoordinator, read_hyperframes_frame,
    },
};
use motionwright_service::StudioService;
use serde_json::json;
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::Path,
};
use uuid::Uuid;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn write(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&serde_json::to_vec_pretty(value)?)?;
    file.sync_all()?;
    Ok(())
}
fn read_source(path: &Path) -> Result<serde_json::Value> {
    let stat = fs::symlink_metadata(path)?;
    if !stat.is_file() || stat.file_type().is_symlink() || stat.len() > 2 * 1024 * 1024 {
        return Err("Acceptance source must be a bounded regular JSON fixture".into());
    }
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
fn seed(database: &Path, fixture: &Path, expected_source: &Path) -> Result<()> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Acceptance seed database must be empty".into());
    }
    let source = read_source(fixture)?;
    let fixture: h::HyperframesPlan = serde_json::from_value(source["plan"].clone())?;
    h::validate_plan(&fixture)?;
    let mut p = service.create_project("Native HyperFrames canonical acceptance")?;
    let apply = |project: &d::Project, key: &str, change: d::Change| -> Result<d::Project> {
        Ok(service
            .apply(project.id, &project.stamp(), key, &change)?
            .project)
    };
    p = apply(
        &p,
        "hf-seed-scene",
        d::Change::AddScene {
            name: "Native composition".into(),
            objective: "Preserve the native renderer's expressive source and exact output".into(),
            duration_seconds: 3,
        },
    )?;
    let scene_id = p.scenes[0].id;
    let duration = d::RationalTime::new(
        i64::from(fixture.document.canvas.frames) * i64::from(fixture.document.canvas.rate.den),
        i64::from(fixture.document.canvas.rate.num),
    )?;
    p = apply(
        &p,
        "hf-seed-duration",
        d::Change::SetSceneDuration { scene_id, duration },
    )?;
    let mut profile = p.deliverables[0].clone();
    profile.width = fixture.document.canvas.width;
    profile.height = fixture.document.canvas.height;
    profile.frame_rate = d::RationalTime::new(
        i64::from(fixture.document.canvas.rate.num),
        i64::from(fixture.document.canvas.rate.den),
    )?;
    p = apply(
        &p,
        "hf-seed-output",
        d::Change::UpsertDeliverable {
            profile: profile.clone(),
        },
    )?;
    p = apply(
        &p,
        "hf-explicit-owner-profile",
        d::Change::UpsertExtension {
            extension: d::ExtensionProfile {
                id: Uuid::new_v4(),
                name: "HyperFrames owner-installed native profile".into(),
                kind: d::ExtensionKind::HyperframesRenderer,
                package_version: h::HYPERFRAMES_VERSION.into(),
                digest_sha256: fixture.runtime_receipt_sha256,
                license: "Installed dependencies reviewed for this disposable acceptance".into(),
                source: "Explicit owner provisioned runtime receipt".into(),
                rights_status: d::RightsStatus::Cleared,
                enabled: true,
                permissions: vec![
                    "read_project".into(),
                    "read_assets".into(),
                    "write_artifacts".into(),
                ],
            },
        },
    )?;
    let doc = d::NativeSceneDocument {
        id: Uuid::new_v4(),
        scene_id,
        profile_id: profile.id,
        label: "Native mask/curve/alpha fixture".into(),
        source: d::NativeSceneSource::Hyperframes(fixture.document),
        source_capsule_id: None,
    };
    p = apply(
        &p,
        "hf-attach-editable-source",
        d::Change::EditCreativeWorkspace {
            edit: d::CreativeWorkspaceEdit::UpsertNativeScene {
                scene: doc.clone(),
                expected_source_sha256: None,
            },
        },
    )?;
    p = apply(
        &p,
        "hf-select-native-renderer",
        d::Change::SetSceneRenderer {
            scene_id,
            renderer: d::RendererKind::Hyperframes,
        },
    )?;
    let plan = prepare_hyperframes_plan(&p, doc.id)?;
    write(
        expected_source,
        &json!({"plan":plan,"source_sha256":h::source_digest(&plan.document)?,"source_html":h::compile_html(&plan.document)?,"plan_json":serde_json::to_string(&plan)?}),
    )?;
    println!(
        "{}",
        json!({"project_id":p.id,"resource":p.resource_key(),"generation":p.generation,"revision":p.revision,"document_id":doc.id,"profile_id":profile.id,"scene_id":scene_id,"source_sha256":doc.source.source_digest()?,"frame_count":plan.document.canvas.frames})
    );
    Ok(())
}
async fn realize(database: &Path, connection: &Path, evidence: &Path) -> Result<()> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("Expected one acceptance project".into());
    }
    let p = projects.into_iter().next().unwrap();
    let doc_id = p.production_design.workspace.native_scenes[0].id;
    let connection = ProductionConnection::load(connection)?;
    let root = connection.output_root.clone();
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let attempt = Uuid::new_v4();
    let result = coordinator
        .render_hyperframes_scene(p.id, &p.stamp(), doc_id, attempt)
        .await?;
    let again = coordinator
        .start_hyperframes_scene(p.id, &p.stamp(), doc_id, attempt)
        .await?;
    let readback = coordinator
        .query_hyperframes_job(
            p.id,
            attempt,
            "hf-existing-result",
            HyperframesJobAction::Result,
        )
        .await?;
    let checked = coordinator.validate_hyperframes_result(p.id, doc_id, &readback)?;
    if result != checked {
        return Err("Existing attempt result changed across process-restart reconciliation".into());
    }
    for index in [0, 15, 30, 89] {
        let _frame = read_hyperframes_frame(&root, &result, index)?;
    }
    let before = service.project(p.id)?;
    let newer = service
        .apply(
            p.id,
            &before.stamp(),
            "hf-new-human-revision",
            &d::Change::RenameProject {
                title: "A later human decision".into(),
            },
        )?
        .project;
    let status = coordinator
        .query_hyperframes_job(
            p.id,
            attempt,
            "hf-stale-source-observe",
            HyperframesJobAction::Status,
        )
        .await?;
    if coordinator
        .validate_hyperframes_result(p.id, doc_id, &status)
        .is_ok()
    {
        return Err("Old native frames were promoted to a new project revision".into());
    }
    if newer.production_design.workspace != p.production_design.workspace {
        return Err("Readback altered native source".into());
    }
    write(
        evidence,
        &json!({"schema":"motionwright.hyperframes-broker-acceptance/1","native_result":result,"same_attempt_replayed":again.pointer("/result/data/job_ref")==Some(&json!(format!("hf-{}",attempt.simple()))),"cross_process_reconciliation":"PASS","stale_result_not_current":true,"semantic_source_preserved":true,"attempt":attempt,"creative_approval":"required"}),
    )?;
    println!(
        "{}",
        json!({"hyperframes_canonical":"PASS","attempt":attempt,"job_ref":result.job_ref,"source_sha256":result.source_sha256,"frames":result.frame_count})
    );
    Ok(())
}
#[tokio::main]
async fn main() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice(){[op,db,fixture,target]if op=="seed"=>seed(Path::new(db),Path::new(fixture),Path::new(target)),[op,db,connection,evidence]if op=="realize"=>realize(Path::new(db),Path::new(connection),Path::new(evidence)).await,_=>Err("usage: native-hyperframes-e2e seed DB FIXTURE_JSON SOURCE_JSON | realize DB CONNECTION_JSON EVIDENCE_JSON".into())}
}
