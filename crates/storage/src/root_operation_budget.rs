//! Durable per-operation accounting. A root ID is an accounting key, never
//! an authorization grant or permission to run a native provider.
//! The producer must retain the admitted root across all child requests.
use crate::production::{
    ProductionReceipt, ProductionReceiptInput, append_record, request_history,
    validate_original_production_binding,
};
use crate::{Result, StorageError, Store};
use motionwright_domain::DomainError;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const SCHEMA: [(&str, &str, &str); 8] = [
    (
        "production_operation_roots",
        "table",
        r#"
CREATE TABLE production_operation_roots(
 project_id TEXT NOT NULL,
 root_id TEXT NOT NULL,
 operation_key TEXT NOT NULL,
 generation TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision >= 0),
 limit_attempts INTEGER NOT NULL CHECK(limit_attempts > 0),
 limit_model_calls INTEGER NOT NULL CHECK(limit_model_calls >= 0),
 limit_render_frames INTEGER NOT NULL CHECK(limit_render_frames >= 0),
 limit_output_bytes INTEGER NOT NULL CHECK(limit_output_bytes >= 0),
 PRIMARY KEY(project_id,root_id),
 UNIQUE(project_id,operation_key),
 FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE RESTRICT
)"#,
    ),
    (
        "production_operation_debits",
        "table",
        r#"
CREATE TABLE production_operation_debits(
 project_id TEXT NOT NULL,
 request_id TEXT NOT NULL,
 root_id TEXT NOT NULL,
 receipt_id TEXT NOT NULL UNIQUE,
 generation TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision >= 0),
 request_sha256 TEXT NOT NULL,
 command TEXT NOT NULL,
 attempts INTEGER NOT NULL CHECK(attempts = 1),
 model_calls INTEGER NOT NULL CHECK(model_calls >= 0),
 render_frames INTEGER NOT NULL CHECK(render_frames >= 0),
 output_bytes INTEGER NOT NULL CHECK(output_bytes >= 0),
 PRIMARY KEY(project_id,request_id),
 FOREIGN KEY(project_id,root_id) REFERENCES production_operation_roots(project_id,root_id)
   ON DELETE RESTRICT
)"#,
    ),
    (
        "production_operation_dispatch_gate",
        "trigger",
        r#"
CREATE TRIGGER production_operation_dispatch_gate
BEFORE INSERT ON production_receipts
WHEN EXISTS(SELECT 1 FROM production_operation_roots WHERE project_id=NEW.project_id)
 AND (NEW.stage='dispatching'
      OR NOT EXISTS(
        SELECT 1 FROM production_receipts AS prior
        WHERE prior.project_id=NEW.project_id AND prior.request_id=NEW.request_id
      ))
BEGIN
 SELECT RAISE(ABORT,'budgeted dispatch requires matching retained debit')
 WHERE NOT EXISTS(
  SELECT 1 FROM production_operation_debits AS d
  WHERE d.project_id=NEW.project_id AND d.request_id=NEW.request_id
   AND d.receipt_id=NEW.receipt_id AND d.generation=NEW.generation
   AND d.revision=NEW.revision AND d.command=NEW.command
   AND d.request_sha256=NEW.request_sha256
 );
END"#,
    ),
    (
        "production_operation_root_immutable",
        "trigger",
        r#"
CREATE TRIGGER production_operation_root_immutable
BEFORE UPDATE ON production_operation_roots
BEGIN SELECT RAISE(ABORT,'operation roots are immutable'); END"#,
    ),
    (
        "production_operation_debit_immutable",
        "trigger",
        r#"
CREATE TRIGGER production_operation_debit_immutable
BEFORE UPDATE ON production_operation_debits
BEGIN SELECT RAISE(ABORT,'operation debits are immutable'); END"#,
    ),
    (
        "production_operation_root_nodelete",
        "trigger",
        r#"
CREATE TRIGGER production_operation_root_nodelete
BEFORE DELETE ON production_operation_roots
BEGIN SELECT RAISE(ABORT,'operation roots cannot be discarded'); END"#,
    ),
    (
        "production_operation_debit_nodelete",
        "trigger",
        r#"
CREATE TRIGGER production_operation_debit_nodelete
BEFORE DELETE ON production_operation_debits
BEGIN SELECT RAISE(ABORT,'operation debits cannot be discarded'); END"#,
    ),
    (
        "production_operation_debit_budget_gate",
        "trigger",
        r#"
CREATE TRIGGER production_operation_debit_budget_gate
BEFORE INSERT ON production_operation_debits
BEGIN
 SELECT RAISE(ABORT,'unknown or stale root')
 WHERE NOT EXISTS(
  SELECT 1 FROM production_operation_roots AS r
  WHERE r.project_id=NEW.project_id AND r.root_id=NEW.root_id
   AND r.generation=NEW.generation AND r.revision=NEW.revision
 );
 SELECT RAISE(ABORT,'root operation budget exhausted')
 WHERE EXISTS(
   SELECT 1 FROM production_operation_roots AS r
   WHERE r.project_id=NEW.project_id AND r.root_id=NEW.root_id
     AND (
       (SELECT COALESCE(SUM(d.attempts),0) FROM production_operation_debits AS d
        WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)
            > r.limit_attempts-NEW.attempts
       OR (SELECT COALESCE(SUM(d.model_calls),0) FROM production_operation_debits AS d
           WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)
            > r.limit_model_calls-NEW.model_calls
       OR (SELECT COALESCE(SUM(d.render_frames),0) FROM production_operation_debits AS d
           WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)
            > r.limit_render_frames-NEW.render_frames
       OR (SELECT COALESCE(SUM(d.output_bytes),0) FROM production_operation_debits AS d
           WHERE d.project_id=NEW.project_id AND d.root_id=NEW.root_id)
            > r.limit_output_bytes-NEW.output_bytes
     )
 );
END"#,
    ),
];

// Installed SQLite triggers invoke this connection-scoped function. An older
// writer that already opened the database cannot supply it; SQLite rejects its
// statement after schema invalidation. Proper current Store connections keep
// normal project CAS and UI edits available during remote rendering.
// This is a version marker, NOT native or payment authorization.
pub(crate) const WRITER_FENCE_FUNCTION: &str = "motionwright_writer_epoch_guard_v6";
const FENCED_TABLES: [&str; 9] = [
    "projects",
    "receipts",
    "events",
    "production_receipts",
    "production_receipt_order",
    "render_window_cache_entries",
    "render_window_cache_evictions",
    "cas_store_identity",
    "cas_import_commits",
];
const FENCED_EVENTS: [&str; 3] = ["INSERT", "UPDATE", "DELETE"];

pub(crate) fn register_writer_fence(conn: &Connection) -> Result<()> {
    use rusqlite::functions::FunctionFlags;
    conn.create_scalar_function(
        WRITER_FENCE_FUNCTION,
        0,
        FunctionFlags::SQLITE_UTF8
            | FunctionFlags::SQLITE_DETERMINISTIC
            | FunctionFlags::SQLITE_INNOCUOUS,
        |_| Ok(6_i64),
    )?;
    Ok(())
}

// Identifiers come solely from the above fixed private code constants.
// Do not interpolate user-controlled identifiers or SQL in schema DDL.
fn fence_trigger_name(table: &str, event: &str) -> String {
    format!("mw_writer_v6_{table}_{}", event.to_ascii_lowercase())
}
fn writer_fence_ddl(table: &str, event: &str) -> String {
    format!(
        "CREATE TRIGGER {} BEFORE {event} ON {table} BEGIN
           SELECT RAISE(ABORT,'schema6 registered writer required')
           WHERE {WRITER_FENCE_FUNCTION}() IS NOT 6;
         END",
        fence_trigger_name(table, event),
    )
}
/// Compare a fixed SQLite writer-fence object's name, table and complete DDL.
/// CAS custody uses this same source contract when validating its six triggers.
/// This is source-layout validation, not an authorization or runtime permit.
pub(crate) fn is_canonical_writer_fence(name: &str, table: &str, sql: &str) -> bool {
    FENCED_TABLES.contains(&table)
        && FENCED_EVENTS.iter().any(|event| {
            name == fence_trigger_name(table, event)
                && crate::normalized_schema_sql(sql)
                    == crate::normalized_schema_sql(&writer_fence_ddl(table, event))
        })
}

fn validate_writer_fences(conn: &Connection, version: u32) -> Result<()> {
    for table in FENCED_TABLES {
        for event in FENCED_EVENTS {
            let name = fence_trigger_name(table, event);
            let actual: Vec<(String, Option<String>)> = {
                let mut stmt = conn.prepare(
                    "SELECT type,sql FROM sqlite_master
                     WHERE name=?1 COLLATE NOCASE LIMIT 2",
                )?;
                stmt.query_map([&name], |row| Ok((row.get(0)?, row.get(1)?)))?
                    .collect::<rusqlite::Result<_>>()?
            };
            match actual.as_slice() {
                [] if version < 6 => (),
                [(kind, Some(sql))]
                    if version >= 6
                        && kind == "trigger"
                        && crate::normalized_schema_sql(sql)
                            == crate::normalized_schema_sql(&writer_fence_ddl(table, event)) => {}
                _ => {
                    return Err(StorageError::InvalidBackup(
                        "schema6 connection writer fence absent, changed or reserved".into(),
                    ));
                }
            }
        }
    }
    Ok(())
}

fn invalid(message: &'static str) -> StorageError {
    StorageError::Domain(DomainError::Invalid(message.into()))
}
fn to_sql(value: u64) -> Result<i64> {
    i64::try_from(value).map_err(|_| invalid("root budget unit exceeds SQLite integer range"))
}
fn from_sql(value: i64) -> Result<u64> {
    u64::try_from(value).map_err(|_| invalid("stored root budget unit is negative"))
}
fn bounded_key(value: &str) -> Result<()> {
    if value.is_empty() || value.len() > 256 || value.chars().any(char::is_control) {
        return Err(invalid("root operation key is invalid"));
    }
    Ok(())
}
fn digest_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(deny_unknown_fields)]
pub struct RootBudgetUnits {
    /// Every distinct actual producer dispatch charges exactly one attempt.
    pub attempts: u64,
    /// Actual model-provider calls (not a currency or token estimate).
    pub model_calls: u64,
    /// Frames requested for render; producer separately enforces actual output.
    pub render_frames: u64,
    /// Reserved maximal output bytes; never presented as observed disk usage.
    pub output_bytes: u64,
}
impl RootBudgetUnits {
    fn sql(self) -> Result<[i64; 4]> {
        Ok([
            to_sql(self.attempts)?,
            to_sql(self.model_calls)?,
            to_sql(self.render_frames)?,
            to_sql(self.output_bytes)?,
        ])
    }
    fn from_row(values: [i64; 4]) -> Result<Self> {
        Ok(Self {
            attempts: from_sql(values[0])?,
            model_calls: from_sql(values[1])?,
            render_frames: from_sql(values[2])?,
            output_bytes: from_sql(values[3])?,
        })
    }
    fn fits(self, added: Self, ceiling: Self) -> bool {
        self.attempts
            .checked_add(added.attempts)
            .is_some_and(|v| v <= ceiling.attempts)
            && self
                .model_calls
                .checked_add(added.model_calls)
                .is_some_and(|v| v <= ceiling.model_calls)
            && self
                .render_frames
                .checked_add(added.render_frames)
                .is_some_and(|v| v <= ceiling.render_frames)
            && self
                .output_bytes
                .checked_add(added.output_bytes)
                .is_some_and(|v| v <= ceiling.output_bytes)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RootOperationBudgetInput {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    /// Retained work identity, distinct from request/stage ID.
    pub operation_key: String,
    pub limits: RootBudgetUnits,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RootOperationBudget {
    pub project_id: Uuid,
    pub root_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub operation_key: String,
    pub limits: RootBudgetUnits,
    pub spent: RootBudgetUnits,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RootDispatchBudgetInput {
    pub root_id: Uuid,
    pub receipt: ProductionReceiptInput,
    pub units: RootBudgetUnits,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "state", content = "receipt", rename_all = "snake_case")]
pub enum RootDispatchBudgetOutcome {
    /// One durable debit and original dispatching intent committed together.
    Admitted(ProductionReceipt),
    /// A recorded completed request. It was not executed or charged again.
    AlreadyCompleted(ProductionReceipt),
}

pub(crate) fn validate_schema(conn: &Connection, version: u32) -> Result<()> {
    for (name, kind, canonical) in SCHEMA {
        let actual: Vec<(String, Option<String>)> = {
            let mut stmt = conn.prepare(
                "SELECT type,sql FROM sqlite_master WHERE name=?1 COLLATE NOCASE LIMIT 2",
            )?;
            stmt.query_map([name], |row| Ok((row.get(0)?, row.get(1)?)))?
                .collect::<rusqlite::Result<_>>()?
        };
        match actual.as_slice() {
            [] if version < 6 => (),
            [(observed_kind, Some(sql))]
                if version >= 6
                    && observed_kind == kind
                    && crate::normalized_schema_sql(sql)
                        == crate::normalized_schema_sql(canonical) => {}
            _ => {
                return Err(StorageError::InvalidBackup(
                    "operation budget schema is absent, incompatible or reserved".into(),
                ));
            }
        }
    }
    if version >= 6 {
        // Full foreign-key and receipt linkage checks. A copied budget row
        // alone does not establish a coherent original production history.
        let corruption: bool = conn.query_row(
            "SELECT EXISTS(
                SELECT 1 FROM production_operation_debits AS d
                LEFT JOIN production_operation_roots AS r
                  ON r.project_id=d.project_id AND r.root_id=d.root_id
                LEFT JOIN production_receipts AS p ON p.receipt_id=d.receipt_id
                WHERE r.root_id IS NULL OR p.receipt_id IS NULL
                 OR d.project_id<>p.project_id OR d.request_id<>p.request_id
                 OR d.request_sha256<>p.request_sha256 OR d.command<>p.command
                 OR d.generation<>p.generation OR d.revision<>p.revision
                 OR p.stage<>'dispatching'
            )",
            [],
            |row| row.get(0),
        )?;
        if corruption {
            return Err(StorageError::InvalidBackup(
                "operation debit history does not identify original dispatches".into(),
            ));
        }
    }
    validate_writer_fences(conn, version)?;
    Ok(())
}

pub(crate) fn migrate_schema(conn: &Connection, version: u32) -> Result<()> {
    if version < 6 {
        // A foreign or partially prepared schema cannot be silently adopted.
        validate_schema(conn, version)?;
        for (_, _, sql) in SCHEMA {
            conn.execute_batch(sql)?;
        }
        // These are SQLite schema objects: any pre-v6 connection missing the
        // scalar function fails its first protected write. New writers do not
        // hold a transaction while the native/model/render service runs.
        for table in FENCED_TABLES {
            for event in FENCED_EVENTS {
                conn.execute_batch(&writer_fence_ddl(table, event))?;
            }
        }
    }
    validate_schema(conn, 6)
}

fn root(conn: &Connection, project_id: Uuid, root_id: Uuid) -> Result<RootOperationBudget> {
    type RootRow = (String, i64, String, [i64; 4], [i64; 4]);
    let row: RootRow = conn
        .query_row(
            "SELECT generation,revision,operation_key,
             limit_attempts,limit_model_calls,limit_render_frames,limit_output_bytes,
             (SELECT COALESCE(SUM(attempts),0) FROM production_operation_debits AS d
              WHERE d.project_id=r.project_id AND d.root_id=r.root_id),
             (SELECT COALESCE(SUM(model_calls),0) FROM production_operation_debits AS d
              WHERE d.project_id=r.project_id AND d.root_id=r.root_id),
             (SELECT COALESCE(SUM(render_frames),0) FROM production_operation_debits AS d
              WHERE d.project_id=r.project_id AND d.root_id=r.root_id),
             (SELECT COALESCE(SUM(output_bytes),0) FROM production_operation_debits AS d
              WHERE d.project_id=r.project_id AND d.root_id=r.root_id)
         FROM production_operation_roots AS r WHERE project_id=?1 AND root_id=?2",
            params![project_id.to_string(), root_id.to_string()],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    [row.get(3)?, row.get(4)?, row.get(5)?, row.get(6)?],
                    [row.get(7)?, row.get(8)?, row.get(9)?, row.get(10)?],
                ))
            },
        )
        .optional()?
        .ok_or(StorageError::NotFound)?;
    let generation =
        Uuid::parse_str(&row.0).map_err(|_| invalid("stored operation generation invalid"))?;
    let revision =
        u64::try_from(row.1).map_err(|_| invalid("stored operation revision invalid"))?;
    let limits = RootBudgetUnits::from_row(row.3)?;
    let spent = RootBudgetUnits::from_row(row.4)?;
    if !spent.fits(RootBudgetUnits::default(), limits) {
        return Err(StorageError::InvalidBackup(
            "operation budget overspent".into(),
        ));
    }
    Ok(RootOperationBudget {
        project_id,
        root_id,
        generation,
        revision,
        operation_key: row.2,
        limits,
        spent,
    })
}
impl Store {
    /// New operation only. Repeating an operation key never creates new credit.
    /// This accounting identity is not a Broker grant.
    pub fn create_root_operation_budget(
        &mut self,
        input: RootOperationBudgetInput,
    ) -> Result<RootOperationBudget> {
        bounded_key(&input.operation_key)?;
        crate::ensure_secret_free_bytes(&serde_json::to_vec(&input)?, "root operation budget")?;
        if input.limits.attempts == 0 {
            return Err(invalid("root attempt limit must be positive"));
        }
        let [a, m, f, b] = input.limits.sql()?;
        let revision = to_sql(input.revision)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        validate_schema(&tx, 6)?;
        let current = crate::load_project_from_row(&tx, input.project_id)?;
        if current.generation != input.generation {
            return Err(StorageError::GenerationConflict);
        }
        if current.revision != input.revision {
            return Err(StorageError::Conflict {
                expected: input.revision,
                actual: current.revision,
            });
        }
        // Migration must not reset uncertain earlier effects under a new key.
        // Inspect committed order, not UUID magnitude or unreliable timestamps.
        let unresolved: bool = tx.query_row(
            "SELECT EXISTS(
               SELECT 1 FROM production_receipts AS r
               JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
               WHERE r.project_id=?1 AND r.stage IN ('dispatching','outcome_unknown')
                 AND NOT EXISTS(
                   SELECT 1 FROM production_receipts AS terminal
                   JOIN production_receipt_order AS later
                     ON later.receipt_id=terminal.receipt_id
                   WHERE terminal.project_id=r.project_id
                     AND terminal.request_id=r.request_id
                     AND later.sequence>o.sequence
                     AND terminal.stage IN ('completed','failed_known')
                 )
             )",
            [input.project_id.to_string()],
            |row| row.get(0),
        )?;
        if unresolved {
            return Err(invalid("unresolved historical effect blocks new root"));
        }
        if tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM production_operation_roots
                WHERE project_id=?1 AND operation_key=?2)",
            params![input.project_id.to_string(), input.operation_key],
            |row| row.get::<_, bool>(0),
        )? {
            return Err(StorageError::RequestReuse);
        }
        let root_id = Uuid::now_v7();
        tx.execute(
            "INSERT INTO production_operation_roots(
                project_id,root_id,operation_key,generation,revision,
                limit_attempts,limit_model_calls,limit_render_frames,limit_output_bytes
             ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",
            params![
                input.project_id.to_string(),
                root_id.to_string(),
                input.operation_key,
                input.generation.to_string(),
                revision,
                a,
                m,
                f,
                b
            ],
        )?;
        let result = root(&tx, input.project_id, root_id)?;
        tx.commit()?;
        Ok(result)
    }

    /// Read-only identity check. Callers must not silently create a new root
    /// when this fails, even after an uncertain effect or a process restart.
    pub fn root_operation_budget(
        &self,
        project_id: Uuid,
        root_id: Uuid,
    ) -> Result<RootOperationBudget> {
        let tx = self.conn.unchecked_transaction()?;
        validate_schema(&tx, 6)?;
        let result = root(&tx, project_id, root_id)?;
        tx.commit()?;
        Ok(result)
    }

    /// A debit and its dispatching receipt share one IMMEDIATE transaction.
    /// The native effect is allowed only *after* this method returns Admitted.
    /// A timeout must never cause the caller to run the effect again.
    pub fn admit_root_operation_dispatch(
        &mut self,
        input: RootDispatchBudgetInput,
    ) -> Result<RootDispatchBudgetOutcome> {
        let intent = &input.receipt;
        if intent.stage != "dispatching"
            || input.units.attempts != 1
            || intent.request_id.is_empty()
            || intent.request_id.len() > 256
            || intent.command.is_empty()
            || intent.command.len() > 256
            || intent.request_id.chars().any(char::is_control)
            || intent.command.chars().any(char::is_control)
            || !digest_valid(&intent.request_sha256)
            || intent.command.starts_with("motionwright.recovery.")
        {
            return Err(invalid("budgeted dispatch intent is malformed"));
        }
        crate::ensure_secret_free_bytes(
            &serde_json::to_vec(&input)?,
            "budgeted operation dispatch",
        )?;
        let [a, m, f, b] = input.units.sql()?;
        let revision = to_sql(intent.revision)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        validate_schema(&tx, 6)?;
        let state = root(&tx, intent.project_id, input.root_id)?;
        if state.generation != intent.generation || state.revision != intent.revision {
            return Err(StorageError::RequestReuse);
        }
        validate_original_production_binding(&tx, intent)?;
        let history = request_history(&tx, intent.project_id, &intent.request_id)?;
        if !history.is_empty() {
            let saved: Option<(String, String, String, i64, String, [i64; 4])> = tx
                .query_row(
                    "SELECT root_id,request_sha256,command,revision,generation,
                        attempts,model_calls,render_frames,output_bytes
                     FROM production_operation_debits
                     WHERE project_id=?1 AND request_id=?2",
                    params![intent.project_id.to_string(), &intent.request_id],
                    |row| {
                        Ok((
                            row.get(0)?,
                            row.get(1)?,
                            row.get(2)?,
                            row.get(3)?,
                            row.get(4)?,
                            [row.get(5)?, row.get(6)?, row.get(7)?, row.get(8)?],
                        ))
                    },
                )
                .optional()?;
            if let (Some(previous), Some(done)) = (saved, history.last())
                && previous.0 == input.root_id.to_string()
                && previous.1 == intent.request_sha256
                && previous.2 == intent.command
                && previous.3 == revision
                && previous.4 == intent.generation.to_string()
                && previous.5 == [a, m, f, b]
                && done.stage == "completed"
                && done.generation == intent.generation
                && done.revision == intent.revision
            {
                tx.commit()?;
                return Ok(RootDispatchBudgetOutcome::AlreadyCompleted(done.clone()));
            }
            // Retain UNKNOWN/dispatching/failed, never restart a mutating effect.
            return Err(StorageError::RequestReuse);
        }
        let current = crate::load_project_from_row(&tx, intent.project_id)?;
        if current.generation != intent.generation {
            return Err(StorageError::GenerationConflict);
        }
        if current.revision != intent.revision {
            return Err(StorageError::Conflict {
                expected: intent.revision,
                actual: current.revision,
            });
        }
        if !state.spent.fits(input.units, state.limits) {
            return Err(invalid("root operation budget exhausted"));
        }
        let id = Uuid::now_v7();
        tx.execute(
            "INSERT INTO production_operation_debits(
                project_id,request_id,root_id,receipt_id,generation,revision,
                request_sha256,command,attempts,model_calls,render_frames,output_bytes
             ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)",
            params![
                intent.project_id.to_string(),
                &intent.request_id,
                input.root_id.to_string(),
                id.to_string(),
                intent.generation.to_string(),
                revision,
                &intent.request_sha256,
                &intent.command,
                a,
                m,
                f,
                b,
            ],
        )?;
        let receipt = append_record(&tx, input.receipt, &current, Some(id))?;
        // Both the original receipt and debit must be visible before commit.
        let recorded = root(&tx, receipt.project_id, input.root_id)?;
        if !recorded
            .spent
            .fits(RootBudgetUnits::default(), recorded.limits)
        {
            return Err(invalid("root operation debit exceeds ceiling"));
        }
        tx.commit()?;
        Ok(RootDispatchBudgetOutcome::Admitted(receipt))
    }
}
