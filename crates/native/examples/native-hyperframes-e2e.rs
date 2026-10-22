//! Disposable Actions producer: one saved source, exact native export, original
//! Broker route, and fresh-process readback. No fixture claims human acceptance.
use motionwright_domain::{
    CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RationalTime, RevisionStamp,
    VisualToken,
};
use motionwright_hyperframes_profile::{self as model, CompiledSource};
use motionwright_native::{
    hyperframes::build_hyperframes_source,
    production::{ProductionConnection, ProductionCoordinator},
};
use motionwright_service::StudioService;
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;
type TestResult<T> = Result<T, Box<dyn std::error::Error>>;
const PROFILE: &str = "hyperframes-first-party-640x360";
fn value_sha(v: &impl Serialize) -> TestResult<String> {
    Ok(model::sha(&serde_json::to_vec(&serde_json::to_value(v)?)?))
}
fn fresh_bytes(path: &Path, bytes: &[u8]) -> TestResult<()> {
    let parent = path.parent().ok_or("private output parent missing")?;
    if !path.is_absolute() || parent.canonicalize()? != parent || bytes.len() > 8 * 1024 * 1024 {
        return Err("private source output invalid".into());
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    if fs::read(path)? != bytes {
        return Err("private original readback differs".into());
    }
    Ok(())
}
fn fresh_json(path: &Path, value: &impl Serialize) -> TestResult<()> {
    let mut b = serde_json::to_vec_pretty(value)?;
    b.push(b'\n');
    fresh_bytes(path, &b)
}
fn apply(service: &StudioService, p: &mut Project, name: &str, change: Change) -> TestResult<()> {
    *p = service.apply(p.id, &p.stamp(), name, &change)?.project;
    Ok(())
}
fn rectangle(name: &str, x: f64, y: f64, fill: &str, z: i32, opacity: f64) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: "rect".into(),
        parent_id: None,
        x,
        y,
        width: 720.0,
        height: 360.0,
        rotation_deg: 0.0,
        opacity,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: z,
        style: NodeStyle {
            fill: Some(fill.into()),
            ..Default::default()
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn profile(p: &Project) -> TestResult<Uuid> {
    let rows = p
        .deliverables
        .iter()
        .filter(|d| d.name == PROFILE)
        .collect::<Vec<_>>();
    if rows.len() != 1 || (rows[0].width, rows[0].height) != (640, 360) {
        return Err("saved native profile differs".into());
    }
    Ok(rows[0].id)
}
fn project(service: &StudioService) -> TestResult<Project> {
    let mut rows = service.projects(2)?;
    if rows.len() != 1 {
        return Err("native scenario requires one saved Project".into());
    }
    let p = rows.remove(0);
    p.validate()?;
    let _ = profile(&p)?;
    if p.scenes.len() != 2 || p.scenes.iter().any(|s| s.nodes.len() != 2) {
        return Err("native scene source differs".into());
    }
    Ok(p)
}
fn compiled(p: &Project) -> TestResult<CompiledSource> {
    Ok(model::compile(&build_hyperframes_source(p, profile(p)?)?)?)
}
fn record(p: &Project, phase: &str) -> TestResult<Value> {
    let native = compiled(p)?;
    Ok(
        json!({"schema":"motionwright.native.hyperframes-source/1","phase":phase,"project":p,
        "canonical_project_sha256":value_sha(p)?,"source":native.inventory(),"native_dispatch_performed":false,
        "fixture_origin":"generated-first-party-fixture","human_acceptance":false}),
    )
}
fn frozen(p: &Project, path: &Path) -> TestResult<Value> {
    let data = fs::read(path)?;
    if data.len() > 8 * 1024 * 1024 {
        return Err("native source document exceeds bound".into());
    }
    let value: Value = serde_json::from_slice(&data)?;
    let phase = value["phase"]
        .as_str()
        .filter(|s| matches!(*s, "original" | "edited"))
        .ok_or("native phase invalid")?;
    if value != record(p, phase)? {
        return Err("saved Project or native source differs from the original document".into());
    }
    Ok(value)
}
fn fixture(service: &StudioService) -> TestResult<Project> {
    if !service.projects(2)?.is_empty() {
        return Err("native fixture seed must start empty".into());
    }
    let mut p = service.create_project("First-party editable HyperFrames rectangle cuts")?;
    let mut language = p.visual_language.clone();
    language.palette.retain(|t| t.name != "background");
    language.palette.push(VisualToken {
        name: "background".into(),
        value: "#101820".into(),
    });
    apply(
        service,
        &mut p,
        "hf:language",
        Change::SetVisualLanguage {
            visual_language: language,
        },
    )?;
    for (i, (name, x, y, color)) in [
        ("Original red card", 120.0, 120.0, "#E53935"),
        ("Original blue card", 960.0, 540.0, "#1E88E5"),
    ]
    .into_iter()
    .enumerate()
    {
        apply(service,&mut p,&format!("hf:scene-{i}"),Change::AddScene{name:name.into(),objective:"Preserve exact clip timing, rectangle geometry, compositing order and native source".into(),duration_seconds:1})?;
        let scene_id = p.scenes[i].id;
        apply(
            service,
            &mut p,
            &format!("hf:card-{i}"),
            Change::AddCanvasNode {
                scene_id,
                node: rectangle(name, x, y, color, 0, 1.0),
            },
        )?;
        let mut overlay = rectangle(
            "Editable half-opacity white overlay",
            x + 180.0,
            y + 90.0,
            "#FFFFFF",
            1,
            0.5,
        );
        overlay.width = 360.0;
        overlay.height = 180.0;
        apply(
            service,
            &mut p,
            &format!("hf:overlay-{i}"),
            Change::AddCanvasNode {
                scene_id,
                node: overlay,
            },
        )?;
    }
    let mut target = p.deliverables[0].clone();
    target.name = PROFILE.into();
    target.width = 640;
    target.height = 360;
    target.captions = false;
    target.burn_in_captions = false;
    target.frame_rate = RationalTime::new(30, 1)?;
    apply(
        service,
        &mut p,
        "hf:profile",
        Change::UpsertDeliverable { profile: target },
    )?;
    let native = compiled(&p)?;
    if native.composition.frame_count != 60 {
        return Err("native fixture clock differs".into());
    }
    Ok(p)
}
fn seed(db: &Path, output: &Path) -> TestResult<()> {
    if !db.is_absolute() || db.exists() || !db.parent().is_some_and(Path::is_dir) {
        return Err("native database must be a fresh absolute private path".into());
    }
    let service = StudioService::open(db)?;
    let p = fixture(&service)?;
    fresh_json(output, &record(&p, "original")?)
}
fn export(db: &Path, source: &Path, root: &Path, output: &Path) -> TestResult<()> {
    let service = StudioService::open(db)?;
    let p = project(&service)?;
    let _ = frozen(&p, source)?;
    let c = compiled(&p)?;
    if root.canonicalize()? != root {
        return Err("native export root is not canonical".into());
    }
    let bundle = root.join(format!("source-{}", c.fingerprint));
    fs::create_dir(&bundle)?;
    fresh_bytes(&bundle.join("semwright-hyperframes.json"), &c.source)?;
    fresh_bytes(&bundle.join("index.html"), &c.html)?;
    fresh_json(&bundle.join("source-inventory.json"), &c.inventory())?;
    fresh_json(
        output,
        &json!({"schema":"motionwright.native.hyperframes-export/1","source":c.inventory(),
        "source_document_sha256":model::sha(&fs::read(source)?),"new_native_dispatch":false,"editable_source_retained":true}),
    )
}
fn edit(db: &Path, output: &Path) -> TestResult<()> {
    let service = StudioService::open(db)?;
    let mut p = project(&service)?;
    let before = p.clone();
    if p.scenes[0].nodes[0].style.fill.as_deref() != Some("#E53935") {
        return Err("original source edit was already applied or is uncertain".into());
    }
    let scene_id = p.scenes[0].id;
    let node_id = p.scenes[0].nodes[0].id;
    let mut style = p.scenes[0].nodes[0].style.clone();
    style.fill = Some("#00AA66".into());
    apply(
        &service,
        &mut p,
        "hf:edit-original-fill-once",
        Change::UpdateCanvasStyle {
            scene_id,
            node_id,
            style,
        },
    )?;
    if p.revision != before.revision + 1
        || p.scenes[1] != before.scenes[1]
        || p.scenes[0].nodes[1] != before.scenes[0].nodes[1]
        || p.scenes[0].nodes[0].id != node_id
        || p.deliverables != before.deliverables
        || p.assets != before.assets
    {
        return Err("native editable source identities changed unexpectedly".into());
    }
    fresh_json(output, &record(&p, "edited")?)
}
async fn render(
    db: &Path,
    connection: &Path,
    source: &Path,
    pin: &str,
    output: &Path,
) -> TestResult<()> {
    let service = StudioService::open(db)?;
    let p = project(&service)?;
    let document = frozen(&p, source)?;
    let phase = document["phase"].as_str().ok_or("native phase absent")?;
    let conn = ProductionConnection::load(connection)?;
    if conn.resource != p.resource_key() {
        return Err("native connection resource differs".into());
    }
    let coordinator = ProductionCoordinator::new(service, conn)?;
    let prefix = format!("hyperframes:{phase}");
    fresh_json(
        &output.with_extension("intent.json"),
        &json!({"schema":"motionwright.native.hyperframes-intent/1",
        "project_resource":p.resource_key(),"generation":p.generation,"revision":p.revision,"request_id":prefix,
        "source":document["source"],"runtime_lock_sha256":pin,"native_attempt":1,"automatic_retry":false}),
    )?;
    let result = match coordinator
        .render_hyperframes_source(p.id, &p.stamp(), &prefix, profile(&p)?, pin)
        .await
    {
        Ok(value) => value,
        Err(error) => {
            fresh_json(
                &output.with_extension("failure.json"),
                &json!({"schema":"motionwright.native.hyperframes-failure/1",
                "error":{"code":format!("{:?}",error.code),"message":error.message,"outcome_known":error.outcome_known},
                "receipts":coordinator.receipts(p.id,512).unwrap_or_default(),"pending_custody":coordinator.private_receipt_custody_snapshot(),
                "automatic_retry":false,"originals_retained":true}),
            )?;
            return Err(
                "native HyperFrames outcome is not verified; reconcile the original attempt".into(),
            );
        }
    };
    if result.source != compiled(&p)?.inventory()
        || result.revision != p.revision
        || result.generation != p.generation
    {
        return Err("native source response differs".into());
    }
    fresh_json(
        output,
        &json!({"schema":"motionwright.native.hyperframes-render/1","phase":phase,
        "canonical_project_sha256":value_sha(&p)?,"source_document_sha256":model::sha(&fs::read(source)?),
        "hyperframes":result,"receipts":coordinator.receipts(p.id,512)?,"native_attempt":1,"automatic_retry":false}),
    )
}
fn readback(db: &Path, source: &Path, render: &Path, output: &Path) -> TestResult<()> {
    // A new process creates a new service, with no native connection or coordinator.
    let service = StudioService::open(db)?;
    let p = project(&service)?;
    let doc = frozen(&p, source)?;
    let produced: Value = serde_json::from_slice(&fs::read(render)?)?;
    let receipts = service.production_receipts(p.id, 512)?;
    let receipt_sha = value_sha(&receipts)?;
    if produced["canonical_project_sha256"] != doc["canonical_project_sha256"]
        || produced.get("receipts") != Some(&serde_json::to_value(&receipts)?)
        || produced.pointer("/hyperframes/source") != doc.get("source")
    {
        return Err("fresh saved source or receipt readback differs".into());
    }
    if value_sha(&service.production_receipts(p.id, 512)?)? != receipt_sha {
        return Err("read-only reopen changed production receipts".into());
    }
    fresh_json(
        output,
        &json!({"schema":"motionwright.native.hyperframes-reopen/1","phase":doc["phase"],
        "canonical_project_sha256":value_sha(&p)?,"source_document_sha256":model::sha(&fs::read(source)?),
        "render_document_sha256":model::sha(&fs::read(render)?),"receipts_sha256":receipt_sha,
        "source":compiled(&p)?.inventory(),"fresh_service":true,"production_receipts_unchanged":true,
        "new_native_dispatch":false,"editable_source_retained":true}),
    )
}
async fn run() -> TestResult<()> {
    if std::env::var("GITHUB_ACTIONS").as_deref() != Ok("true") {
        return Err("native producer requires disposable Actions".into());
    }
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    let verb = args
        .first()
        .and_then(|a| a.to_str())
        .ok_or("native stage absent")?;
    let p = |i| PathBuf::from(&args[i]);
    match (verb, args.len()) {
        ("seed", 3) => seed(&p(1), &p(2))?,
        ("export", 5) => export(&p(1), &p(2), &p(3), &p(4))?,
        ("edit", 3) => edit(&p(1), &p(2))?,
        ("render", 6) => {
            render(
                &p(1),
                &p(2),
                &p(3),
                args[4].to_str().ok_or("native pin invalid")?,
                &p(5),
            )
            .await?
        }
        ("readback", 5) => readback(&p(1), &p(2), &p(3), &p(4))?,
        _ => return Err("invalid closed native producer arguments".into()),
    }
    println!("{{\"status\":\"STAGE_COMPLETED\",\"automatic_retry\":false}}");
    Ok(())
}
#[tokio::main]
async fn main() {
    if run().await.is_err() {
        eprintln!("native-hyperframes=NOT_VERIFIED; automatic_retry=false");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn original_project_contains_exact_native_timing_and_editable_nodes() {
        let d = tempfile::tempdir().unwrap();
        let s = StudioService::open(d.path().join("source.sqlite3")).unwrap();
        let p = fixture(&s).unwrap();
        let c = compiled(&p).unwrap();
        assert_eq!(c.composition.frame_count, 60);
        assert_eq!(c.composition.clips.len(), 4);
        assert_eq!(c.composition.clips[1].opacity_milli, 500);
        assert_eq!(c.composition.clips[2].start_frame, 30);
        assert!(s.production_receipts(p.id, 512).unwrap().is_empty());
    }
    #[test]
    fn source_edit_keeps_original_id_and_does_not_replay() {
        let d = tempfile::tempdir().unwrap();
        let db = d.path().join("source.sqlite3");
        let s = StudioService::open(&db).unwrap();
        let p = fixture(&s).unwrap();
        let before = compiled(&p).unwrap();
        drop(s);
        edit(&db, &d.path().join("edited.json")).unwrap();
        let s = StudioService::open(&db).unwrap();
        let after = project(&s).unwrap();
        assert_ne!(before.fingerprint, compiled(&after).unwrap().fingerprint);
        assert_eq!(after.scenes[0].nodes[0].id, p.scenes[0].nodes[0].id);
        assert!(edit(&db, &d.path().join("second-edit.json")).is_err());
        assert!(!d.path().join("second-edit.json").exists());
    }
}
