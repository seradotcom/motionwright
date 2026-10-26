//! Seed and independently reopen SQLite for the real Core/Host9 Graph fixture.
//! This tool never saves a Graph to SQLite and never constructs native authority.
use motionwright_domain::{Change, Project, RendererKind, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_storage::Store;
use serde_json::{Value, json};
use std::{
    error::Error,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod source_fixture;

type FixtureResult<T> = Result<T, Box<dyn Error>>;

fn fresh(path: &Path) -> FixtureResult<()> {
    if !path.is_absolute()
        || path.exists()
        || path.is_symlink()
        || !path.parent().is_some_and(Path::is_dir)
    {
        return Err("Graph owner fixture requires a fresh absolute file path".into());
    }
    Ok(())
}

fn write_new(path: &Path, value: &Value) -> FixtureResult<()> {
    fresh(path)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let bytes = serde_json::to_vec_pretty(value)?;
    let mut file = options.open(path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    drop(file);
    if fs::read(path)? != bytes {
        return Err("Graph owner fixture output readback differs; do not retry".into());
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "schema":"motionwright.graph-owner-fixture-output/1",
            "path":path,"bytes":bytes.len(),"native_authority":false
        }))?
    );
    Ok(())
}

fn seed(database: &Path, output: &Path) -> FixtureResult<()> {
    fresh(database)?;
    fresh(output)?;
    let mut store = Store::open(database)?;
    let mut project = store.create_named_project("Genuine Host9 Graph source")?;
    project = store
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "graph-fixture-scene",
            &Change::AddScene {
                name: "Explanatory relationships".into(),
                objective: "Commit exact Graph source through the genuine native owner".into(),
                duration_seconds: 6,
            },
        )?
        .project;
    let scene_id = project.scenes[0].id;
    project = store
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "graph-fixture-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::ManimCommunity,
            },
        )?
        .project;
    project.validate()?;
    let graph = source_fixture::graph_for(&project, false);
    // Pure domain validation only. The persisted baseline deliberately has no
    // Graph; its only Graph write will occur through the actual Native provider.
    let mut shape = project.clone();
    shape.apply_change(&Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    })?;
    shape.validate()?;
    drop(store);
    write_new(
        output,
        &json!({
            "schema":"motionwright.graph-owner-fixture-source/1",
            "application":motionwright_native::APP_ID,
            "application_version":motionwright_native::APP_VERSION,
            "resource":project.resource_key(),"project":project,"graph":graph,
            "graph_persisted":false,"native_authority":false
        }),
    )
}

fn readback(database: &Path, project_id: Uuid, output: &Path) -> FixtureResult<()> {
    fresh(output)?;
    if !database.is_absolute() || !database.is_file() || database.is_symlink() {
        return Err("Graph owner readback requires the original database file".into());
    }
    let service = StudioService::open(database)?;
    let project: Project = service.project(project_id)?;
    project.validate()?;
    let history = service.history(project_id, 0, 100)?;
    write_new(
        output,
        &json!({
            "schema":"motionwright.graph-owner-fixture-readback/1",
            "project_revision":project.revision,"project":project,
            "history":history,"native_authority":false
        }),
    )
}

fn run() -> FixtureResult<()> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    match args.first().and_then(|value| value.to_str()) {
        Some("seed") if args.len() == 3 =>
            seed(&PathBuf::from(&args[1]), &PathBuf::from(&args[2])),
        Some("readback") if args.len() == 4 => {
            let project_id = Uuid::parse_str(args[2].to_str().ok_or("Project UUID is not UTF-8")?)?;
            readback(&PathBuf::from(&args[1]), project_id, &PathBuf::from(&args[3]))
        }
        _ => Err("usage: native-graph-owner-fixture seed NEW_DATABASE NEW_SOURCE_JSON | readback DATABASE PROJECT_UUID NEW_JSON".into()),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("Graph owner source fixture failed: {error}; no automatic retry");
        std::process::exit(1);
    }
}
