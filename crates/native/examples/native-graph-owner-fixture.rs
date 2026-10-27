//! Seed and independently reopen SQLite for the real Core/Host9 Graph fixture.
//! This tool never saves a Graph to SQLite and never constructs native authority.
use motionwright_domain::{Change, Project, RendererKind, ReviewKind, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyResultObservation, Store, apply_request_digest_v1,
};
use rusqlite::{Connection, OpenFlags, TransactionBehavior, types::ValueRef};
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
    seed_with_security(database, output, false)
}

fn seed_with_security(database: &Path, output: &Path, security: bool) -> FixtureResult<()> {
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
    let security_source = if security {
        let marker = format!("SEC07 foreign project {}", Uuid::now_v7());
        let mut foreign = store.create_named_project(&marker)?;
        foreign = store
            .apply(
                foreign.id,
                &RevisionStamp::from(&foreign),
                "sec07-foreign-branch",
                &Change::CreateBranch {
                    name: format!("private branch {}", foreign.id),
                },
            )?
            .project;
        foreign = store
            .apply(
                foreign.id,
                &RevisionStamp::from(&foreign),
                "sec07-foreign-review",
                &Change::AddReview {
                    kind: ReviewKind::Technical,
                    resource: foreign.resource_key(),
                    body: format!("Private review for {marker}"),
                    start: None,
                    end: None,
                    locale: None,
                    profile_id: None,
                },
            )?
            .project;
        foreign.validate()?;
        let absent = Uuid::now_v7();
        if absent == project.id || absent == foreign.id {
            return Err("Unexpected fixture UUID collision".into());
        }
        Some(json!({
            "schema":"motionwright.sec07-fixture-source/1",
            "foreign_resource":foreign.resource_key(),"foreign_project":foreign,
            "foreign_marker":marker,"absent_resource":format!("project:{absent}"),
            "native_authority":false
        }))
    } else {
        None
    };
    drop(store);
    let mut source = json!({
        "schema":"motionwright.graph-owner-fixture-source/1",
        "application":motionwright_native::APP_ID,
        "application_version":motionwright_native::APP_VERSION,
        "resource":project.resource_key(),"project":project,"graph":graph,
        "graph_persisted":false,"native_authority":false
    });
    if let Some(security) = security_source {
        source["security"] = security;
    }
    write_new(output, &source)
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

// Independent owner fixture evidence only. Opening a Store uses its normal
// bootstrap/maintenance path; causal query inventories use the separate raw
// read-only SQLite verb below and do not attribute fixture bootstrap to a read.
fn read_apply_receipt(
    database: &Path,
    project_id: Uuid,
    generation: Uuid,
    request_id: &str,
    result_revision: u64,
    output: &Path,
) -> FixtureResult<()> {
    fresh(output)?;
    if !database.is_absolute() || !database.is_file() || database.is_symlink() {
        return Err("Receipt readback requires the original database file".into());
    }
    let preceding = result_revision
        .checked_sub(1)
        .ok_or("An apply receipt must name a positive result revision")?;
    let mut store = Store::open(database)?;
    let project = store.load_project(project_id)?;
    let events = store.event_records_since(project_id, preceding, 1)?;
    let event = events
        .first()
        .filter(|event| event.revision == result_revision)
        .ok_or("The exact actual result event is absent")?;
    // Hash the actual typed event, never a serde_json::Value or operation wire args.
    let request_sha256 = apply_request_digest_v1(project_id, &event.change)?;
    let stamp = RevisionStamp {
        resource: project.resource_key(),
        generation,
        revision: preceding,
    };
    let binding_only = store.observe_apply_receipt_v1(
        project_id,
        &stamp,
        request_id,
        &event.change,
        APPLY_REQUEST_ENCODING_V1,
    )?;
    let observation = store.lookup_apply_result_v1(
        project_id,
        generation,
        request_id,
        &request_sha256,
        APPLY_REQUEST_ENCODING_V1,
    )?;
    let mut projection = serde_json::to_value(&observation)?;
    projection
        .as_object_mut()
        .ok_or("Receipt observation must be an object")?
        .remove("outcome");
    if projection != serde_json::to_value(&binding_only)? {
        return Err("The existing binding reader and result reader disagree".into());
    }
    let mut value = json!({
        "schema":"motionwright.sec07-apply-receipt-readback/1",
        "project_id":project_id.to_string(),"generation":generation.to_string(),
        "request_id":request_id,"request_sha256":request_sha256,
        "result_revision":result_revision.to_string(),
        "observation":observation,"native_authority":false
    });
    if let ApplyResultObservation::KnownVerifiedV1 { binding, outcome } = &observation {
        if binding.result_revision != result_revision.to_string()
            || outcome.project.revision != result_revision
        {
            return Err("The actual receipt names another result revision".into());
        }
        // Construct the public seven-field projection independently of the app
        // helper, from the actual checked stored outcome. No NativeRef is minted.
        value["result"] = json!({
            "resource":outcome.project.resource_key(),
            "generation":outcome.project.generation.to_string(),
            "revision":outcome.project.revision.to_string(),
            "previous_revision":outcome.previous_revision.to_string(),
            "request_id":outcome.request_id,"replayed":outcome.replayed,
            "project":outcome.project
        });
    }
    if serde_json::to_vec_pretty(&value)?.len() > INVENTORY_OUTPUT_LIMIT {
        return Err("Receipt readback exceeds its fixture output byte budget".into());
    }
    write_new(output, &value)
}

const INVENTORY_ROW_LIMIT: usize = 512;
const INVENTORY_CELL_LIMIT: usize = 1024 * 1024;
const INVENTORY_OUTPUT_LIMIT: usize = 8 * 1024 * 1024;

fn inventory_cell(value: ValueRef<'_>) -> FixtureResult<Value> {
    Ok(match value {
        ValueRef::Null => json!({"type":"null"}),
        ValueRef::Integer(value) => json!({"type":"integer","value":value.to_string()}),
        ValueRef::Real(value) => json!({"type":"real","value":format!("{:016x}",value.to_bits())}),
        ValueRef::Text(bytes) => {
            if bytes.len() > INVENTORY_CELL_LIMIT {
                return Err("Inventory text exceeds its fixture byte budget".into());
            }
            json!({"type":"text","value":std::str::from_utf8(bytes)?})
        }
        ValueRef::Blob(bytes) => {
            if bytes.len() > INVENTORY_CELL_LIMIT {
                return Err("Inventory blob exceeds its fixture byte budget".into());
            }
            json!({"type":"blob","value":hex::encode(bytes)})
        }
    })
}

fn read_storage_inventory(database: &Path, output: &Path) -> FixtureResult<()> {
    fresh(output)?;
    if !database.is_absolute() || !database.is_file() || database.is_symlink() {
        return Err("Storage inventory requires the original database file".into());
    }
    // Use the actual existing WAL-aware database in read-only mode. Do not set
    // immutable=1, reopen a Store, migrate, checkpoint or maintain any cache.
    let mut connection = Connection::open_with_flags(
        database,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW,
    )?;
    connection.pragma_update(None, "query_only", true)?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Deferred)?;
    let mut tables = serde_json::Map::new();
    let mut retained_bytes = 0_usize;
    for (table, sql) in [
        ("projects", "SELECT * FROM projects ORDER BY id LIMIT 513"),
        (
            "events",
            "SELECT * FROM events ORDER BY project_id,revision LIMIT 513",
        ),
        (
            "receipts",
            "SELECT * FROM receipts ORDER BY project_id,request_id LIMIT 513",
        ),
    ] {
        let mut statement = transaction.prepare(sql)?;
        let columns: Vec<String> = statement
            .column_names()
            .iter()
            .map(|name| (*name).to_owned())
            .collect();
        let mut cursor = statement.query([])?;
        let mut rows = Vec::new();
        while let Some(row) = cursor.next()? {
            if rows.len() >= INVENTORY_ROW_LIMIT {
                return Err(
                    "Inventory row budget exceeded; no partial inventory is emitted".into(),
                );
            }
            let mut cells = Vec::with_capacity(columns.len());
            for column in 0..columns.len() {
                let cell = inventory_cell(row.get_ref(column)?)?;
                retained_bytes = retained_bytes
                    .checked_add(serde_json::to_vec(&cell)?.len())
                    .filter(|bytes| *bytes <= INVENTORY_OUTPUT_LIMIT)
                    .ok_or("Inventory retained bytes exceed their fixture budget")?;
                cells.push(cell);
            }
            rows.push(cells);
        }
        tables.insert(table.to_owned(), json!({"columns":columns,"rows":rows}));
    }
    transaction.commit()?;
    let value = json!({
        "schema":"motionwright.sec07-storage-inventory/1",
        "tables":tables,"native_authority":false
    });
    if serde_json::to_vec_pretty(&value)?.len() > INVENTORY_OUTPUT_LIMIT {
        return Err("Inventory output exceeds its fixture byte budget".into());
    }
    write_new(output, &value)
}

fn run() -> FixtureResult<()> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    match args.first().and_then(|value| value.to_str()) {
        Some("seed") if args.len() == 3 =>
            seed(&PathBuf::from(&args[1]), &PathBuf::from(&args[2])),
        Some("seed-security") if args.len() == 3 =>
            seed_with_security(&PathBuf::from(&args[1]), &PathBuf::from(&args[2]), true),
        Some("readback") if args.len() == 4 => {
            let project_id = Uuid::parse_str(args[2].to_str().ok_or("Project UUID is not UTF-8")?)?;
            readback(&PathBuf::from(&args[1]), project_id, &PathBuf::from(&args[3]))
        }
        Some("read-apply-receipt") if args.len() == 7 => {
            let project_id = Uuid::parse_str(args[2].to_str().ok_or("Project UUID is not UTF-8")?)?;
            let generation = Uuid::parse_str(args[3].to_str().ok_or("Generation UUID is not UTF-8")?)?;
            let request_id = args[4].to_str().ok_or("Receipt key is not UTF-8")?;
            let revision = args[5].to_str().ok_or("Result revision is not UTF-8")?.parse::<u64>()?;
            read_apply_receipt(&PathBuf::from(&args[1]), project_id, generation, request_id,
                revision, &PathBuf::from(&args[6]))
        }
        Some("read-storage-inventory") if args.len() == 3 =>
            read_storage_inventory(&PathBuf::from(&args[1]), &PathBuf::from(&args[2])),
        _ => Err("usage: native-graph-owner-fixture seed|seed-security NEW_DATABASE NEW_SOURCE_JSON | readback DATABASE PROJECT_UUID NEW_JSON | read-apply-receipt DATABASE PROJECT_UUID GENERATION_UUID RAW_REQUEST_KEY RESULT_REVISION NEW_JSON | read-storage-inventory DATABASE NEW_JSON".into()),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("Graph owner source fixture failed: {error}; no automatic retry");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod recovery_fixture_tests {
    use super::*;

    fn read_json(path: &Path) -> Value {
        serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
    }

    #[test]
    fn actual_receipt_readback_matches_stored_result_and_readonly_inventory() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("actual.sqlite3");
        let mut store = Store::open(&database).unwrap();
        let original = store.create_named_project("Actual fixture source").unwrap();
        let change = Change::RenameProject {
            title: "Actual stored fixture result".into(),
        };
        let committed = store
            .apply(original.id, &original.stamp(), "actual-key", &change)
            .unwrap();
        let before = directory.path().join("before.json");
        read_storage_inventory(&database, &before).unwrap();
        let receipt = directory.path().join("receipt.json");
        read_apply_receipt(
            &database,
            original.id,
            original.generation,
            "actual-key",
            committed.project.revision,
            &receipt,
        )
        .unwrap();
        let observed = read_json(&receipt);
        assert_eq!(
            observed["schema"],
            "motionwright.sec07-apply-receipt-readback/1"
        );
        assert_eq!(observed["native_authority"], false);
        assert_eq!(
            observed["request_sha256"],
            apply_request_digest_v1(original.id, &change).unwrap()
        );
        assert_eq!(observed["observation"]["state"], "known_verified_v1");
        assert_eq!(
            observed["observation"]["outcome"],
            serde_json::to_value(&committed).unwrap()
        );
        assert_eq!(
            observed["result"]["project"],
            serde_json::to_value(&committed.project).unwrap()
        );
        assert_eq!(observed["result"]["request_id"], "actual-key");
        assert_eq!(observed["result"]["replayed"], false);
        assert_eq!(observed["result"].as_object().unwrap().len(), 7);
        let after = directory.path().join("after.json");
        read_storage_inventory(&database, &after).unwrap();
        assert_eq!(read_json(&before), read_json(&after));
        let inventory = read_json(&after);
        assert_eq!(inventory["tables"].as_object().unwrap().len(), 3);
        for table in ["projects", "events", "receipts"] {
            assert!(
                !inventory["tables"][table]["columns"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
            assert!(
                !inventory["tables"][table]["rows"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
        }
        // Positive sensitivity control: a real second commit must change the
        // actual stored-row inventory, including its real receipt and event.
        store
            .apply(
                original.id,
                &committed.project.stamp(),
                "second-key",
                &Change::RenameProject {
                    title: "Second actual fixture commit".into(),
                },
            )
            .unwrap();
        let changed = directory.path().join("changed.json");
        read_storage_inventory(&database, &changed).unwrap();
        let changed = read_json(&changed);
        assert_ne!(changed, inventory);
        assert_eq!(
            changed["tables"]["receipts"]["rows"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            changed["tables"]["events"]["rows"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
    }
}
