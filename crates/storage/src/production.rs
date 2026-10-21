use crate::{Result, StorageError, Store};
use motionwright_domain::{DomainError, Project};
use rusqlite::{OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProductionReceiptInput {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub request_id: String,
    pub request_sha256: String,
    pub command: String,
    pub stage: String,
    pub payload: Value,
}

/// Snapshot identity for an append-only local receipt stream. The head UUID
/// identifies the last committed ordinal; UUID magnitude and dates are not order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProductionReceiptWatermark {
    pub latest_id: Option<Uuid>,
    pub count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProductionReceipt {
    pub id: Uuid,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub request_id: String,
    pub request_sha256: String,
    pub command: String,
    pub stage: String,
    pub payload: Value,
    pub created_at: String,
}

fn bounded_token(value: &str, max: usize, label: &str) -> Result<()> {
    if value.is_empty() || value.len() > max || value.chars().any(char::is_control) {
        return Err(StorageError::Domain(DomainError::Invalid(format!(
            "{label} is out of bounds"
        ))));
    }
    Ok(())
}

// A request key has one immutable identity even when observations arrive out of
// order. Check every prior binding so older inconsistent histories fail closed
// instead of choosing authority from a receipt UUID or the latest observation.
fn validate_original_production_binding(
    conn: &rusqlite::Connection,
    input: &ProductionReceiptInput,
) -> Result<bool> {
    let revision = i64::try_from(input.revision).map_err(|_| StorageError::RequestReuse)?;
    let (has_prior, binding_changed): (bool, bool) = conn.query_row(
        "SELECT
           EXISTS(SELECT 1 FROM production_receipts
                  WHERE project_id=?1 AND request_id=?2),
           EXISTS(SELECT 1 FROM production_receipts
                  WHERE project_id=?1 AND request_id=?2
                    AND (request_sha256 IS NOT ?3 OR command IS NOT ?4
                         OR generation IS NOT ?5 OR revision IS NOT ?6))",
        params![
            input.project_id.to_string(),
            &input.request_id,
            &input.request_sha256,
            &input.command,
            input.generation.to_string(),
            revision,
        ],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if binding_changed {
        return Err(StorageError::RequestReuse);
    }
    Ok(has_prior)
}

type StoredReceiptFields = (
    String,
    String,
    String,
    i64,
    String,
    String,
    String,
    String,
    String,
    String,
);

fn stored_receipt_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<StoredReceiptFields> {
    Ok((
        row.get(0)?,
        row.get(1)?,
        row.get(2)?,
        row.get(3)?,
        row.get(4)?,
        row.get(5)?,
        row.get(6)?,
        row.get(7)?,
        row.get(8)?,
        row.get(9)?,
    ))
}

fn decode_receipt(fields: StoredReceiptFields) -> Result<ProductionReceipt> {
    let (
        id,
        project_id,
        generation,
        revision,
        request_id,
        request_sha256,
        command,
        stage,
        payload,
        created_at,
    ) = fields;
    let parse_id = |value: &str| {
        Uuid::parse_str(value).map_err(|_| {
            StorageError::Domain(DomainError::Invalid(
                "stored production receipt identity is invalid".into(),
            ))
        })
    };
    Ok(ProductionReceipt {
        id: parse_id(&id)?,
        project_id: parse_id(&project_id)?,
        generation: parse_id(&generation)?,
        revision: u64::try_from(revision).map_err(|_| {
            StorageError::Domain(DomainError::Invalid(
                "stored production revision is invalid".into(),
            ))
        })?,
        request_id,
        request_sha256,
        command,
        stage,
        payload: serde_json::from_str(&payload)?,
        created_at,
    })
}

fn query_receipts(
    conn: &rusqlite::Connection,
    sql: &str,
    values: impl rusqlite::Params,
) -> Result<Vec<ProductionReceipt>> {
    let mut statement = conn.prepare(sql)?;
    let rows = statement.query_map(values, stored_receipt_row)?;
    let mut result = Vec::new();
    for row in rows {
        result.push(decode_receipt(row?)?);
    }
    Ok(result)
}

fn request_history(
    conn: &rusqlite::Connection,
    project_id: Uuid,
    request_id: &str,
) -> Result<Vec<ProductionReceipt>> {
    query_receipts(
        conn,
        "SELECT r.receipt_id,r.project_id,r.generation,r.revision,r.request_id,
                r.request_sha256,r.command,r.stage,r.payload_json,r.created_at
         FROM production_receipts AS r
         JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
         WHERE r.project_id=?1 AND r.request_id=?2 ORDER BY o.sequence",
        params![project_id.to_string(), request_id],
    )
}

fn is_recovery_command(command: &str) -> bool {
    matches!(
        command,
        "motionwright.recovery.blender" | "motionwright.recovery.motion-canvas"
    )
}

fn recovery_attempt(receipt: &ProductionReceipt) -> Result<u64> {
    receipt
        .payload
        .get("attempt")
        .and_then(Value::as_u64)
        .filter(|attempt| (1..=16).contains(attempt))
        .ok_or(StorageError::RequestReuse)
}

fn retryable_recovery(receipt: &ProductionReceipt) -> bool {
    receipt.command == "motionwright.recovery.motion-canvas"
        && receipt.stage == "failed_known"
        && receipt.payload.get("retryable").and_then(Value::as_bool) == Some(true)
}

// Ordinal order alone cannot grant authority to a late result from an older
// attempt. Validate every retained transition, including legacy histories.
// Ambiguous histories remain available through audit pages, but grant no replay.
fn recovery_head(history: &[ProductionReceipt]) -> Result<Option<ProductionReceipt>> {
    let mut previous: Option<&ProductionReceipt> = None;
    for receipt in history {
        if !is_recovery_command(&receipt.command) {
            return Err(StorageError::RequestReuse);
        }
        let attempt = recovery_attempt(receipt)?;
        if let Some(prior) = previous {
            if receipt.project_id != prior.project_id
                || receipt.generation != prior.generation
                || receipt.revision != prior.revision
                || receipt.request_id != prior.request_id
                || receipt.request_sha256 != prior.request_sha256
                || receipt.command != prior.command
            {
                return Err(StorageError::RequestReuse);
            }
            let prior_attempt = recovery_attempt(prior)?;
            if receipt.stage == "dispatching" {
                if !retryable_recovery(prior) || attempt != prior_attempt + 1 {
                    return Err(StorageError::RequestReuse);
                }
            } else if prior.stage != "dispatching"
                || attempt != prior_attempt
                || !matches!(
                    receipt.stage.as_str(),
                    "completed" | "failed_known" | "outcome_unknown"
                )
            {
                return Err(StorageError::RequestReuse);
            }
        } else if receipt.stage != "dispatching" || attempt != 1 {
            return Err(StorageError::RequestReuse);
        }
        previous = Some(receipt);
    }
    Ok(previous.cloned())
}

#[derive(PartialEq, Eq)]
struct ReceiptHistoryFingerprint {
    count: u64,
    last_sequence: Option<i64>,
    sha256: [u8; 32],
}

// Protect every retained receipt in this project, across keys, providers and
// generations. Stream raw SQLite fields so JSON normalization cannot hide an
// extension's rewrite and memory does not grow with the complete history.
fn receipt_history_fingerprint(
    conn: &rusqlite::Connection,
    project_id: Uuid,
    exclude: Option<Uuid>,
) -> Result<ReceiptHistoryFingerprint> {
    super::validate_production_receipt_order_integrity(conn, Some(project_id))?;
    let mut statement = conn.prepare(
        "SELECT r.receipt_id,r.project_id,r.generation,r.revision,r.request_id,
                r.request_sha256,r.command,r.stage,r.payload_json,r.created_at,o.sequence
         FROM production_receipts AS r
         JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
         WHERE r.project_id=?1 AND (?2 IS NULL OR r.receipt_id<>?2)
         ORDER BY o.sequence",
    )?;
    let mut rows = statement.query(params![
        project_id.to_string(),
        exclude.map(|id| id.to_string())
    ])?;
    let mut hash = Sha256::new();
    hash.update(b"motionwright-production-receipt-history/1");
    let mut count = 0_u64;
    let mut last_sequence = None;
    while let Some(row) = rows.next()? {
        let sequence: i64 = row.get(10)?;
        // payload_json stays a String within this tuple, not a parsed Value.
        let bytes = serde_json::to_vec(&stored_receipt_row(row)?)?;
        hash.update(sequence.to_le_bytes());
        hash.update((bytes.len() as u64).to_le_bytes());
        hash.update(&bytes);
        count += 1;
        last_sequence = Some(sequence);
    }
    Ok(ReceiptHistoryFingerprint {
        count,
        last_sequence,
        sha256: hash.finalize().into(),
    })
}

fn append_record(
    conn: &rusqlite::Transaction<'_>,
    input: ProductionReceiptInput,
    current_project: &Project,
) -> Result<ProductionReceipt> {
    let before = receipt_history_fingerprint(conn, input.project_id, None)?;
    let receipt = ProductionReceipt {
        id: Uuid::now_v7(),
        project_id: input.project_id,
        generation: input.generation,
        revision: input.revision,
        request_id: input.request_id,
        request_sha256: input.request_sha256,
        command: input.command,
        stage: input.stage,
        payload: input.payload,
        created_at: chrono::Utc::now().to_rfc3339(),
    };
    let revision = i64::try_from(receipt.revision).map_err(|_| StorageError::RequestReuse)?;
    conn.execute(
        "INSERT INTO production_receipts(
            receipt_id,project_id,generation,revision,request_id,request_sha256,
            command,stage,payload_json,created_at
         ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
        params![
            receipt.id.to_string(),
            receipt.project_id.to_string(),
            receipt.generation.to_string(),
            revision,
            &receipt.request_id,
            &receipt.request_sha256,
            &receipt.command,
            &receipt.stage,
            serde_json::to_string(&receipt.payload)?,
            &receipt.created_at,
        ],
    )?;
    // This comparison runs after every INSERT trigger. Only the requested
    // receipt may be new; nested admissions or even valid-looking historical
    // rewrites must roll back along with any extension audit side effects.
    let after = receipt_history_fingerprint(conn, receipt.project_id, Some(receipt.id))?;
    if after != before {
        return Err(StorageError::InvalidBackup(
            "recording a receipt changed the retained project receipt history".into(),
        ));
    }
    // An extension can also RAISE(IGNORE) or alter the requested row itself.
    let persisted = query_receipts(
        conn,
        "SELECT r.receipt_id,r.project_id,r.generation,r.revision,r.request_id,
                r.request_sha256,r.command,r.stage,r.payload_json,r.created_at
         FROM production_receipts AS r
         JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
         WHERE r.project_id=?1 AND r.receipt_id=?2",
        params![receipt.project_id.to_string(), receipt.id.to_string()],
    )?;
    if persisted.as_slice() != std::slice::from_ref(&receipt) {
        return Err(StorageError::InvalidBackup(
            "receipt and its commit-order mapping were not atomically recorded".into(),
        ));
    }
    let sequence = receipt_anchor(conn, receipt.project_id, receipt.id)?;
    if before.last_sequence.is_some_and(|prior| sequence <= prior) {
        return Err(StorageError::InvalidBackup(
            "new receipt ordinal does not follow the retained history".into(),
        ));
    }
    if super::load_project_from_row(conn, receipt.project_id)? != *current_project {
        return Err(StorageError::InvalidBackup(
            "project authority changed while recording a production receipt".into(),
        ));
    }
    if is_recovery_command(&receipt.command)
        && recovery_head(&request_history(
            conn,
            receipt.project_id,
            &receipt.request_id,
        )?)?
        .as_ref()
            != Some(&receipt)
    {
        return Err(StorageError::RequestReuse);
    }
    Ok(receipt)
}

fn receipt_anchor(conn: &rusqlite::Connection, project_id: Uuid, receipt_id: Uuid) -> Result<i64> {
    conn.query_row(
        "SELECT sequence FROM production_receipt_order WHERE project_id=?1 AND receipt_id=?2",
        params![project_id.to_string(), receipt_id.to_string()],
        |row| row.get(0),
    )
    .optional()?
    .ok_or_else(|| {
        StorageError::Domain(DomainError::Invalid(
            "receipt cursor does not identify an observation in this project".into(),
        ))
    })
}

impl Store {
    /// Atomically claim one application-owned recovery stage. This is deliberately
    /// not a Semwright command: the Driver Host retains runtime authority.
    /// An unfinished claim survives process death and blocks blind replay.
    pub fn claim_recovery_stage(
        &mut self,
        mut input: ProductionReceiptInput,
    ) -> Result<ProductionReceipt> {
        if !is_recovery_command(&input.command)
            || input.stage != "dispatching"
            || input.request_id.len() > 256
            || input.request_id.is_empty()
            || input.request_id.chars().any(char::is_control)
            || input.request_sha256.len() != 64
            || !input
                .request_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(StorageError::Domain(DomainError::Invalid(
                "Recovery stage claim is malformed".into(),
            )));
        }
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current = super::load_project_from_row(&tx, input.project_id)?;
        if current.generation != input.generation {
            return Err(StorageError::GenerationConflict);
        }
        if current.revision != input.revision {
            return Err(StorageError::Conflict {
                expected: input.revision,
                actual: current.revision,
            });
        }
        super::validate_production_receipt_order_integrity(&tx, Some(input.project_id))?;
        validate_original_production_binding(&tx, &input)?;
        // A new name or fingerprint cannot bypass an unresolved provider effect.
        // Inspect each complete attempt chain, not MAX(UUID) or a bare last row.
        let keys = {
            let mut statement = tx.prepare(
                "SELECT DISTINCT request_id FROM production_receipts
                 WHERE project_id=?1 AND generation=?2 AND command=?3",
            )?;
            statement
                .query_map(
                    params![
                        input.project_id.to_string(),
                        input.generation.to_string(),
                        &input.command
                    ],
                    |row| row.get::<_, String>(0),
                )?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        for key in keys {
            let history = request_history(&tx, input.project_id, &key)?;
            let head = recovery_head(&history)?.ok_or(StorageError::RequestReuse)?;
            if key != input.request_id
                && (matches!(head.stage.as_str(), "dispatching" | "outcome_unknown")
                    || (head.stage == "failed_known"
                        && head.command == "motionwright.recovery.blender"))
            {
                return Err(StorageError::RequestReuse);
            }
        }
        let history = request_history(&tx, input.project_id, &input.request_id)?;
        let attempt = match recovery_head(&history)? {
            None => 1,
            Some(prior) if retryable_recovery(&prior) && recovery_attempt(&prior)? < 16 => {
                recovery_attempt(&prior)? + 1
            }
            Some(_) => return Err(StorageError::RequestReuse),
        };
        let data = input.payload.as_object_mut().ok_or_else(|| {
            StorageError::Domain(DomainError::Invalid(
                "Recovery payload is not an object".into(),
            ))
        })?;
        data.insert("attempt".into(), serde_json::json!(attempt));
        let receipt = append_record(&tx, input, &current)?;
        tx.commit()?;
        Ok(receipt)
    }

    pub fn append_production_receipt(
        &mut self,
        input: ProductionReceiptInput,
    ) -> Result<ProductionReceipt> {
        bounded_token(&input.request_id, 256, "production request id")?;
        bounded_token(&input.command, 256, "production command")?;
        bounded_token(&input.stage, 64, "production stage")?;
        if input.request_sha256.len() != 64
            || !input
                .request_sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(StorageError::Domain(DomainError::Invalid(
                "production request digest is invalid".into(),
            )));
        }
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current = super::load_project_from_row(&tx, input.project_id)?;
        if current.generation != input.generation {
            return Err(StorageError::GenerationConflict);
        }
        if input.revision > current.revision {
            return Err(StorageError::Conflict {
                expected: input.revision,
                actual: current.revision,
            });
        }
        super::validate_production_receipt_order_integrity(&tx, Some(input.project_id))?;
        let has_prior = validate_original_production_binding(&tx, &input)?;
        if has_prior && input.stage == "dispatching" {
            return Err(StorageError::RequestReuse);
        }
        if input.stage == "dispatching" && input.revision != current.revision {
            return Err(StorageError::Conflict {
                expected: input.revision,
                actual: current.revision,
            });
        }
        if is_recovery_command(&input.command) {
            let history = request_history(&tx, input.project_id, &input.request_id)?;
            let head = recovery_head(&history)?.ok_or(StorageError::RequestReuse)?;
            if head.stage != "dispatching"
                || input.payload.get("attempt").and_then(Value::as_u64)
                    != Some(recovery_attempt(&head)?)
                || !matches!(
                    input.stage.as_str(),
                    "completed" | "failed_known" | "outcome_unknown"
                )
            {
                return Err(StorageError::RequestReuse);
            }
        }
        let receipt = append_record(&tx, input, &current)?;
        tx.commit()?;
        Ok(receipt)
    }

    pub fn latest_production_receipt(
        &self,
        project_id: Uuid,
        request_id: &str,
    ) -> Result<Option<ProductionReceipt>> {
        bounded_token(request_id, 256, "production request id")?;
        let tx = self.conn.unchecked_transaction()?;
        super::validate_production_receipt_order_integrity(&tx, Some(project_id))?;
        let history = request_history(&tx, project_id, request_id)?;
        let latest = if history
            .iter()
            .any(|receipt| is_recovery_command(&receipt.command))
        {
            recovery_head(&history)?
        } else {
            history.last().cloned()
        };
        tx.commit()?;
        Ok(latest)
    }

    /// Head of the committed local receipt stream, independent of wall time and
    /// the creative project revision. Both fields come from one read snapshot.
    pub fn production_receipt_watermark(
        &self,
        project_id: Uuid,
    ) -> Result<ProductionReceiptWatermark> {
        let tx = self.conn.unchecked_transaction()?;
        super::validate_production_receipt_order_integrity(&tx, Some(project_id))?;
        let (latest, count): (Option<String>, i64) = tx.query_row(
            "SELECT
               (SELECT receipt_id FROM production_receipt_order
                WHERE project_id=?1 ORDER BY sequence DESC LIMIT 1),
               (SELECT COUNT(*) FROM production_receipts WHERE project_id=?1)",
            params![project_id.to_string()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        let head = ProductionReceiptWatermark {
            latest_id: latest
                .map(|text| {
                    Uuid::parse_str(&text).map_err(|_| {
                        StorageError::Domain(DomainError::Invalid(
                            "Stored receipt watermark ID is invalid".into(),
                        ))
                    })
                })
                .transpose()?,
            count: u64::try_from(count).map_err(|_| {
                StorageError::Domain(DomainError::Invalid(
                    "Stored receipt count is invalid".into(),
                ))
            })?,
        };
        tx.commit()?;
        Ok(head)
    }

    /// Bounded descending commit-order read. Public UUID anchors are opaque and
    /// resolve within the same project and read snapshot. Callers compare the
    /// watermark before and after a multi-page enumeration.
    pub fn production_receipts_before(
        &self,
        project_id: Uuid,
        through_id: Uuid,
        before_id: Option<Uuid>,
        limit: usize,
    ) -> Result<Vec<ProductionReceipt>> {
        let tx = self.conn.unchecked_transaction()?;
        super::validate_production_receipt_order_integrity(&tx, Some(project_id))?;
        let through = receipt_anchor(&tx, project_id, through_id)?;
        let before = before_id
            .map(|id| receipt_anchor(&tx, project_id, id))
            .transpose()?;
        if before.is_some_and(|sequence| sequence > through) {
            return Err(StorageError::Domain(DomainError::Invalid(
                "receipt cursor exceeds the snapshot commit order".into(),
            )));
        }
        let receipts = query_receipts(
            &tx,
            "SELECT r.receipt_id,r.project_id,r.generation,r.revision,r.request_id,
                    r.request_sha256,r.command,r.stage,r.payload_json,r.created_at
             FROM production_receipts AS r
             JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
             WHERE r.project_id=?1 AND o.sequence<=?2 AND (?3 IS NULL OR o.sequence<?3)
             ORDER BY o.sequence DESC LIMIT ?4",
            params![
                project_id.to_string(),
                through,
                before,
                limit.clamp(1, 256) as i64
            ],
        )?;
        tx.commit()?;
        Ok(receipts)
    }

    /// Existing UI latest-window API remains compatible.
    pub fn production_receipts(
        &self,
        project_id: Uuid,
        limit: usize,
    ) -> Result<Vec<ProductionReceipt>> {
        let head = self.production_receipt_watermark(project_id)?;
        match head.latest_id {
            Some(latest) => self.production_receipts_before(project_id, latest, None, limit),
            None => Ok(Vec::new()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    use std::{
        cell::RefCell,
        sync::mpsc,
        thread,
        time::{Duration, Instant},
    };

    struct ReceiptBusyGate {
        announced: bool,
        reached: mpsc::Sender<()>,
        release: mpsc::Receiver<()>,
        deadline: Instant,
    }

    thread_local! {
        static RECEIPT_BUSY_GATE: RefCell<Option<ReceiptBusyGate>> =
            const { RefCell::new(None) };
    }

    fn wait_for_receipt_writer_lock(_: i32) -> bool {
        RECEIPT_BUSY_GATE.with(|slot| {
            let Ok(mut slot) = slot.try_borrow_mut() else {
                return false;
            };
            let Some(gate) = slot.as_mut() else {
                return false;
            };
            if !gate.announced {
                gate.announced = true;
                if gate.reached.send(()).is_err() {
                    return false;
                }
                let Some(remaining) = gate.deadline.checked_duration_since(Instant::now()) else {
                    return false;
                };
                if gate.release.recv_timeout(remaining).is_err() {
                    return false;
                }
            }
            thread::yield_now();
            Instant::now() < gate.deadline
        })
    }

    fn assert_one_contended_receipt(stage: &str, second_digest: &str, second_command: &str) {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("contended-receipts.sqlite3");
        let mut blocker = Store::open(&path).unwrap();
        let project = blocker
            .create_named_project("Concurrent production requests")
            .unwrap();
        // Opening Store performs schema setup. Both writers must already be
        // open before the third connection holds the SQLite write lock.
        let writers = [Store::open(&path).unwrap(), Store::open(&path).unwrap()];
        let first = ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "shared-production-key".into(),
            request_sha256: "a".repeat(64),
            command: "driver.motion-canvas.render.start".into(),
            stage: stage.into(),
            payload: json!({"fixture": "first"}),
        };
        let second = ProductionReceiptInput {
            request_sha256: second_digest.into(),
            command: second_command.into(),
            ..first.clone()
        };
        let lock = blocker
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .unwrap();
        let (reached_tx, reached_rx) = mpsc::channel();
        let mut releases = Vec::new();
        let mut handles = Vec::new();
        for (mut writer, input) in writers.into_iter().zip([first, second]) {
            let (release_tx, release_rx) = mpsc::channel();
            releases.push(release_tx);
            let reached = reached_tx.clone();
            handles.push(thread::spawn(move || {
                RECEIPT_BUSY_GATE.with(|slot| {
                    *slot.borrow_mut() = Some(ReceiptBusyGate {
                        announced: false,
                        reached,
                        release: release_rx,
                        deadline: Instant::now() + Duration::from_secs(30),
                    });
                });
                writer
                    .conn
                    .busy_handler(Some(wait_for_receipt_writer_lock))
                    .unwrap();
                let result = writer.append_production_receipt(input);
                RECEIPT_BUSY_GATE.with(|slot| *slot.borrow_mut() = None);
                result
            }));
        }
        drop(reached_tx);
        // Each signal comes from SQLite's actual busy callback. Without the
        // transaction both writers reach INSERT after reading no prior key;
        // with it both wait at BEGIN IMMEDIATE before reading that key.
        let both_waiting = reached_rx.recv_timeout(Duration::from_secs(10)).is_ok()
            && reached_rx.recv_timeout(Duration::from_secs(10)).is_ok();
        lock.commit().unwrap();
        for release in releases {
            let _ = release.send(());
        }
        let results: Vec<_> = handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect();
        assert!(both_waiting, "both writers must reach the real SQLite lock");
        assert_eq!(
            results.iter().filter(|result| result.is_ok()).count(),
            1,
            "one request key must have exactly one winning append: {results:?}"
        );
        assert_eq!(
            results
                .iter()
                .filter(|result| matches!(result, Err(StorageError::RequestReuse)))
                .count(),
            1,
            "the losing writer must receive a request conflict, not a busy error"
        );
        let winner = results.into_iter().find_map(Result::ok).unwrap();
        drop(blocker);
        let reopened = Store::open(&path).unwrap();
        let persisted = reopened.production_receipts(project.id, 10).unwrap();
        assert_eq!(persisted, vec![winner]);
    }

    #[test]
    fn concurrent_receipts_cannot_bind_one_key_to_different_digests() {
        assert_one_contended_receipt(
            "completed",
            &"b".repeat(64),
            "driver.motion-canvas.render.start",
        );
    }

    #[test]
    fn concurrent_receipts_cannot_bind_one_key_to_different_commands() {
        assert_one_contended_receipt(
            "completed",
            &"a".repeat(64),
            "driver.mlt-video.render.start",
        );
    }

    #[test]
    fn concurrent_identical_dispatches_have_one_persisted_winner() {
        assert_one_contended_receipt(
            "dispatching",
            &"a".repeat(64),
            "driver.motion-canvas.render.start",
        );
    }

    #[test]
    fn foreign_generation_rejects_receipts_without_leaking_a_writer_transaction() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("receipt-generation.sqlite3")).unwrap();
        let project = store
            .create_named_project("Production generation validation")
            .unwrap();
        let input = ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "generation-bound-request".into(),
            request_sha256: "a".repeat(64),
            command: "driver.motion-canvas.render.start".into(),
            stage: "dispatching".into(),
            payload: json!({"mutation": true}),
        };
        let foreign_generation = Uuid::now_v7();
        assert_ne!(foreign_generation, project.generation);
        let before = store.production_receipt_watermark(project.id).unwrap();

        assert!(matches!(
            store.append_production_receipt(ProductionReceiptInput {
                generation: foreign_generation,
                ..input.clone()
            }),
            Err(StorageError::GenerationConflict)
        ));
        assert_eq!(
            store.production_receipt_watermark(project.id).unwrap(),
            before
        );
        assert!(
            store
                .latest_production_receipt(project.id, &input.request_id)
                .unwrap()
                .is_none()
        );

        // Rejection must release the transaction and leave the request key
        // available for an append bound to the actual project generation.
        let accepted = store.append_production_receipt(input).unwrap();
        assert_eq!(
            store.production_receipts(project.id, 10).unwrap(),
            vec![accepted]
        );
        assert_eq!(store.load_project(project.id).unwrap(), project);
    }

    #[test]
    fn corrupt_project_document_cannot_accept_a_production_receipt() {
        for document in ["{".to_owned(), String::new()] {
            let temp = tempfile::tempdir().unwrap();
            let mut store =
                Store::open(temp.path().join("invalid-receipt-project.sqlite3")).unwrap();
            let project = store.create_named_project("Document validation").unwrap();
            let document = if document.is_empty() {
                let mut value = serde_json::to_value(&project).unwrap();
                value["title"] = json!("");
                serde_json::to_string(&value).unwrap()
            } else {
                document
            };
            store
                .conn
                .execute(
                    "UPDATE projects SET document_json=?1 WHERE id=?2",
                    params![document, project.id.to_string()],
                )
                .unwrap();
            let result = store.append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: "must-not-be-recorded".into(),
                request_sha256: "a".repeat(64),
                command: "driver.motion-canvas.render.start".into(),
                stage: "dispatching".into(),
                payload: json!({}),
            });
            assert!(matches!(
                result,
                Err(StorageError::Serde(_) | StorageError::Domain(_))
            ));
            assert_eq!(
                store
                    .production_receipt_watermark(project.id)
                    .unwrap()
                    .count,
                0
            );
        }
    }

    #[test]
    fn receipt_keyset_spans_more_than_old_window_with_exact_snapshot_watermark() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("receipt-keyset.sqlite3")).unwrap();
        let first = store
            .create_named_project("Receipt paging fixture")
            .unwrap();
        let other = store
            .create_named_project("Foreign receipt fixture")
            .unwrap();
        let empty = store.production_receipt_watermark(first.id).unwrap();
        assert_eq!(empty.count, 0);
        assert!(empty.latest_id.is_none());
        let digest = "a".repeat(64);
        let mut committed_ids = Vec::new();
        for index in 0..311 {
            let receipt = store
                .append_production_receipt(ProductionReceiptInput {
                    project_id: first.id,
                    generation: first.generation,
                    revision: first.revision,
                    request_id: format!("verified-req-{index}"),
                    request_sha256: digest.clone(),
                    command: "driver.motion-canvas.render.status".into(),
                    stage: "completed".into(),
                    payload: json!({"index": index}),
                })
                .unwrap();
            committed_ids.push(receipt.id);
        }
        let head = store.production_receipt_watermark(first.id).unwrap();
        assert_eq!(head.count, 311);
        assert!(head.latest_id.is_some());
        assert_eq!(
            store.production_receipt_watermark(other.id).unwrap().count,
            0
        );
        let mut before = None;
        let mut items = Vec::new();
        for _ in 0..20 {
            let batch = store
                .production_receipts_before(first.id, head.latest_id.unwrap(), before, 23)
                .unwrap();
            if batch.is_empty() {
                break;
            }
            assert!(batch.len() <= 23);
            before = batch.last().map(|record| record.id);
            items.extend(batch.iter().map(|record| record.id));
        }
        assert_eq!(items.len(), 311);
        committed_ids.reverse();
        assert_eq!(
            items, committed_ids,
            "pages must retain durable insertion order"
        );
        assert_eq!(store.production_receipts(first.id, 256).unwrap().len(), 256);

        // New independent receipts update the receipt stream watermark
        // without a creative project mutation or cross-project data leak.
        store
            .append_production_receipt(ProductionReceiptInput {
                project_id: first.id,
                generation: first.generation,
                revision: first.revision,
                request_id: "late-readback".into(),
                request_sha256: digest,
                command: "driver.motion-canvas.render.status".into(),
                stage: "completed".into(),
                payload: json!({"late": true}),
            })
            .unwrap();
        assert_ne!(store.production_receipt_watermark(first.id).unwrap(), head);
        // A foreign UUID is an invalid scoped anchor, not proof of emptiness.
        assert!(matches!(
            store.production_receipts_before(other.id, head.latest_id.unwrap(), None, 16),
            Err(StorageError::Domain(DomainError::Invalid(_)))
        ));
    }

    #[test]
    fn production_receipts_are_revision_bound_and_request_digest_stable() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let project = store.create_named_project("Production fixture").unwrap();
        let sha = "ab".repeat(32);

        let input = |stage: &str, digest: &str, revision: u64| ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision,
            request_id: "request-one".into(),
            request_sha256: digest.into(),
            command: "driver.motion-canvas.composition.inspect".into(),
            stage: stage.into(),
            payload: if stage == "completed" {
                json!({"result":{"ok":true}})
            } else {
                json!({"mutation":false})
            },
        };
        store
            .append_production_receipt(input("dispatching", &sha, project.revision))
            .unwrap();
        store
            .append_production_receipt(input("completed", &sha, project.revision))
            .unwrap();

        let latest = store
            .latest_production_receipt(project.id, "request-one")
            .unwrap()
            .unwrap();
        assert_eq!(latest.stage, "completed");
        assert_eq!(store.production_receipts(project.id, 10).unwrap().len(), 2);

        assert!(matches!(
            store.append_production_receipt(input(
                "dispatching",
                &"cd".repeat(32),
                project.revision
            )),
            Err(StorageError::RequestReuse)
        ));
        assert!(matches!(
            store.append_production_receipt(ProductionReceiptInput {
                request_id: "future".into(),
                ..input("dispatching", &sha, project.revision + 1)
            }),
            Err(StorageError::Conflict { .. })
        ));
    }

    fn revision_binding_input(
        project: &motionwright_domain::Project,
        request_id: &str,
        command: &str,
    ) -> ProductionReceiptInput {
        ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: request_id.into(),
            request_sha256: "a".repeat(64),
            command: command.into(),
            stage: "dispatching".into(),
            payload: json!({"mutation": true}),
        }
    }

    fn edit_after_dispatch(
        store: &mut Store,
        project: &motionwright_domain::Project,
    ) -> motionwright_domain::Project {
        store
            .apply(
                project.id,
                &project.stamp(),
                "edit-after-dispatch",
                &motionwright_domain::Change::AddScene {
                    name: "Later revision".into(),
                    objective: "Keep earlier production observations historical".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project
    }

    #[test]
    fn new_dispatch_rechecks_the_current_project_revision_after_peer_commit() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("stale-dispatch.sqlite3");
        let mut editor = Store::open(&path).unwrap();
        let prepared = editor.create_named_project("Prepared production").unwrap();
        let mut dispatcher = Store::open(&path).unwrap();
        let input = revision_binding_input(
            &prepared,
            "prepared-but-never-dispatched",
            "driver.motion-canvas.render.start",
        );
        let before = dispatcher
            .production_receipt_watermark(prepared.id)
            .unwrap();

        // The coordinator already read and checked this revision. A different
        // connection commits a normal project edit before dispatch admission.
        let edited = edit_after_dispatch(&mut editor, &prepared);
        assert!(edited.revision > prepared.revision);
        let result = dispatcher.append_production_receipt(input.clone());
        assert!(
            matches!(
                result,
                Err(StorageError::Conflict { expected, actual })
                    if expected == prepared.revision && actual == edited.revision
            ),
            "a new dispatch must recheck the head inside its write transaction: {result:?}"
        );
        assert_eq!(
            dispatcher
                .production_receipt_watermark(prepared.id)
                .unwrap(),
            before
        );
        assert!(
            dispatcher
                .latest_production_receipt(prepared.id, &input.request_id)
                .unwrap()
                .is_none()
        );
        drop(dispatcher);
        drop(editor);

        let mut reopened = Store::open(&path).unwrap();
        assert_eq!(reopened.load_project(prepared.id).unwrap(), edited);
        assert!(
            reopened
                .production_receipts(prepared.id, 10)
                .unwrap()
                .is_empty()
        );
        let current = reopened
            .append_production_receipt(revision_binding_input(
                &edited,
                "new-intent-on-current-revision",
                "driver.motion-canvas.render.start",
            ))
            .unwrap();
        assert_eq!(current.revision, edited.revision);
        drop(reopened);
        let final_read = Store::open(&path).unwrap();
        assert_eq!(final_read.load_project(prepared.id).unwrap(), edited);
        assert_eq!(
            final_read.production_receipts(prepared.id, 10).unwrap(),
            vec![current]
        );
    }

    #[test]
    fn stale_dispatch_guard_preserves_consumed_keys_and_historical_observations() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("admitted-historical-dispatch.sqlite3");
        let mut editor = Store::open(&path).unwrap();
        let original = editor.create_named_project("Admitted production").unwrap();
        let mut observer = Store::open(&path).unwrap();
        let input = revision_binding_input(
            &original,
            "already-admitted-original-request",
            "driver.motion-canvas.render.start",
        );
        let dispatch = observer.append_production_receipt(input.clone()).unwrap();
        let edited = edit_after_dispatch(&mut editor, &original);
        let before = observer.production_receipt_watermark(original.id).unwrap();
        let duplicate = observer.append_production_receipt(input.clone());
        assert!(
            matches!(duplicate, Err(StorageError::RequestReuse)),
            "a consumed key must retain RequestReuse precedence: {duplicate:?}"
        );
        assert_eq!(
            observer.production_receipt_watermark(original.id).unwrap(),
            before
        );
        let late = observer
            .append_production_receipt(ProductionReceiptInput {
                stage: "completed".into(),
                payload: json!({"result": {"state": "succeeded", "job_ref": "original-job"}}),
                ..input
            })
            .unwrap();
        assert_eq!(late.revision, original.revision);
        let current = observer
            .append_production_receipt(revision_binding_input(
                &edited,
                "independent-current-request",
                "driver.motion-canvas.render.start",
            ))
            .unwrap();
        assert_eq!(current.revision, edited.revision);
        drop(observer);
        drop(editor);
        let reopened = Store::open(&path).unwrap();
        assert_eq!(reopened.load_project(original.id).unwrap(), edited);
        let rows = reopened.production_receipts(original.id, 10).unwrap();
        assert_eq!(rows.len(), 3);
        assert!(rows.contains(&dispatch) && rows.contains(&late) && rows.contains(&current));
    }

    #[test]
    fn observations_cannot_move_the_original_request_to_a_later_revision() {
        for stage in ["outcome_unknown", "completed", "failed_known"] {
            let temp = tempfile::tempdir().unwrap();
            let path = temp.path().join("observation-revision.sqlite3");
            let mut writer = Store::open(&path).unwrap();
            let original = writer
                .create_named_project("Original request binding")
                .unwrap();
            let mut observer = Store::open(&path).unwrap();
            let input = revision_binding_input(
                &original,
                "one-original-request",
                "driver.motion-canvas.render.start",
            );
            let dispatch = writer.append_production_receipt(input.clone()).unwrap();
            let edited = edit_after_dispatch(&mut writer, &original);
            assert!(edited.revision > original.revision);
            let before = observer.production_receipt_watermark(original.id).unwrap();

            let result = observer.append_production_receipt(ProductionReceiptInput {
                revision: edited.revision,
                stage: stage.into(),
                payload: json!({"observation": "must retain original revision"}),
                ..input.clone()
            });
            assert!(
                matches!(result, Err(StorageError::RequestReuse)),
                "a {stage} observation cannot reattribute the original request: {result:?}"
            );
            assert_eq!(
                observer.production_receipt_watermark(original.id).unwrap(),
                before
            );
            assert_eq!(
                observer.production_receipts(original.id, 10).unwrap(),
                vec![dispatch.clone()]
            );

            let late = observer
                .append_production_receipt(ProductionReceiptInput {
                    stage: stage.into(),
                    payload: json!({"observation": "original revision"}),
                    ..input
                })
                .unwrap();
            assert_eq!(late.revision, original.revision);
            drop(observer);
            drop(writer);
            let reopened = Store::open(&path).unwrap();
            assert_eq!(reopened.load_project(original.id).unwrap(), edited);
            let rows = reopened.production_receipts(original.id, 10).unwrap();
            assert_eq!(rows.len(), 2);
            assert!(rows.contains(&dispatch) && rows.contains(&late));
            assert!(rows.iter().all(|row| row.revision == original.revision));
        }
    }

    #[test]
    fn recovery_retry_cannot_move_the_original_request_to_a_later_revision() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("recovery-revision.sqlite3");
        let mut writer = Store::open(&path).unwrap();
        let original = writer
            .create_named_project("Recovery original binding")
            .unwrap();
        let input = revision_binding_input(
            &original,
            "one-recovery-stage",
            "motionwright.recovery.motion-canvas",
        );
        let dispatch = writer.claim_recovery_stage(input.clone()).unwrap();
        let failed = writer
            .append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({"retryable": true, "attempt": 1}),
                ..input.clone()
            })
            .unwrap();
        let edited = edit_after_dispatch(&mut writer, &original);
        drop(writer);

        let mut observer = Store::open(&path).unwrap();
        let before = observer.production_receipt_watermark(original.id).unwrap();
        let result = observer.claim_recovery_stage(ProductionReceiptInput {
            revision: edited.revision,
            ..input
        });
        assert!(
            matches!(result, Err(StorageError::RequestReuse)),
            "an explicit retry cannot bind an old request to a new revision: {result:?}"
        );
        assert_eq!(
            observer.production_receipt_watermark(original.id).unwrap(),
            before
        );
        drop(observer);
        let reopened = Store::open(&path).unwrap();
        assert_eq!(reopened.load_project(original.id).unwrap(), edited);
        let rows = reopened.production_receipts(original.id, 10).unwrap();
        assert_eq!(rows.len(), 2);
        assert!(rows.contains(&dispatch) && rows.contains(&failed));
        assert_eq!(
            reopened
                .latest_production_receipt(original.id, "one-recovery-stage")
                .unwrap(),
            Some(failed)
        );
    }

    #[test]
    fn same_revision_explicit_recovery_retry_keeps_its_original_binding() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("same-revision-retry.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let project = store
            .create_named_project("Supported recovery retry")
            .unwrap();
        let input = revision_binding_input(
            &project,
            "retryable-original-stage",
            "motionwright.recovery.motion-canvas",
        );
        let original = store.claim_recovery_stage(input.clone()).unwrap();
        assert_eq!(original.payload["attempt"], json!(1));
        store
            .append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({"retryable": true, "attempt": 1}),
                ..input.clone()
            })
            .unwrap();
        drop(store);
        let mut reopened = Store::open(&path).unwrap();
        let retry = reopened.claim_recovery_stage(input).unwrap();
        assert_eq!(retry.payload["attempt"], json!(2));
        assert_eq!(retry.revision, original.revision);
        assert_eq!(retry.generation, original.generation);
        assert_eq!(
            reopened
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            3
        );
    }

    fn extension_order_project() -> (
        tempfile::TempDir,
        std::path::PathBuf,
        motionwright_domain::Project,
    ) {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("receipt-extension.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let project = store.create_named_project("Extension authority").unwrap();
        store
            .conn
            .execute_batch("CREATE TABLE extension_receipt_audit(receipt_id TEXT NOT NULL);")
            .unwrap();
        drop(store);
        (temp, path, project)
    }

    fn extension_recovery_input(
        project: &motionwright_domain::Project,
        stage: &str,
        payload: Value,
    ) -> ProductionReceiptInput {
        ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "requested-recovery".into(),
            request_sha256: "a".repeat(64),
            command: "motionwright.recovery.motion-canvas".into(),
            stage: stage.into(),
            payload,
        }
    }

    fn install_order_extension(path: &std::path::Path, sql: &str, retained_v2: bool) {
        let db = rusqlite::Connection::open(path).unwrap();
        if retained_v2 {
            db.execute_batch(
                "DROP TRIGGER production_receipt_order_append;
                 DROP TABLE production_receipt_order;
                 ALTER TABLE receipts DROP COLUMN apply_binding_json;
                 ALTER TABLE receipts DROP COLUMN apply_binding_version;
                 PRAGMA user_version=2;",
            )
            .unwrap();
        }
        db.execute_batch(sql).unwrap();
    }

    fn order_extension_snapshot(conn: &rusqlite::Connection) -> Value {
        let receipts =
            {
                let mut stmt = conn.prepare(
                "SELECT receipt_id,project_id,generation,revision,request_id,request_sha256,
                        command,stage,payload_json,created_at
                 FROM production_receipts ORDER BY rowid"
            ).unwrap();
                stmt.query_map([], stored_receipt_row)
                    .unwrap()
                    .collect::<rusqlite::Result<Vec<_>>>()
                    .unwrap()
            };
        let mappings = {
            let mut stmt = conn.prepare(
                "SELECT sequence,receipt_id,project_id FROM production_receipt_order ORDER BY sequence"
            ).unwrap();
            stmt.query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap()
        };
        let schema = {
            let mut stmt = conn
                .prepare("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")
                .unwrap();
            stmt.query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, Option<String>>(3)?,
                ))
            })
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap()
        };
        let sequences = {
            let mut stmt = conn
                .prepare("SELECT name,seq FROM sqlite_sequence ORDER BY name")
                .unwrap();
            stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                .unwrap()
                .collect::<rusqlite::Result<Vec<_>>>()
                .unwrap()
        };
        let audit = {
            let mut stmt = conn
                .prepare("SELECT receipt_id FROM extension_receipt_audit ORDER BY rowid")
                .unwrap();
            stmt.query_map([], |r| r.get::<_, String>(0))
                .unwrap()
                .collect::<rusqlite::Result<Vec<_>>>()
                .unwrap()
        };
        json!({
            "receipts":receipts, "mappings":mappings, "schema":schema,
            "sequences":sequences, "audit":audit,
            "version":conn.pragma_query_value(None,"user_version",|r|r.get::<_,u32>(0)).unwrap()
        })
    }

    fn reject_nested_recovery_terminal(retained_v2: bool) {
        let (_temp, path, project) = extension_order_project();
        let nested = Uuid::now_v7();
        install_order_extension(
            &path,
            &format!(
                "CREATE TRIGGER extension_nested_terminal AFTER INSERT ON production_receipts
             WHEN NEW.command='motionwright.recovery.motion-canvas' AND NEW.stage='dispatching'
             BEGIN
               INSERT INTO extension_receipt_audit VALUES(NEW.receipt_id);
               INSERT INTO production_receipts
               SELECT '{nested}',NEW.project_id,NEW.generation,NEW.revision,NEW.request_id,
                      NEW.request_sha256,NEW.command,'failed_known',
                      json_set(NEW.payload_json,'$.retryable',json('true')),NEW.created_at;
             END;"
            ),
            retained_v2,
        );
        let mut store = Store::open(&path).unwrap();
        let before = order_extension_snapshot(&store.conn);
        let result = store.claim_recovery_stage(extension_recovery_input(
            &project,
            "dispatching",
            json!({}),
        ));
        let post_head = store.latest_production_receipt(project.id, "requested-recovery");
        assert!(
            result.is_err(),
            "a nested terminal cannot change the claimed authority before return: {result:?}, head={post_head:?}"
        );
        assert_eq!(order_extension_snapshot(&store.conn), before);
        assert_eq!(store.load_project(project.id).unwrap(), project);
        drop(store);
        let mut reopened = Store::open(&path).unwrap();
        assert_eq!(order_extension_snapshot(&reopened.conn), before);
        reopened
            .conn
            .execute_batch("DROP TRIGGER extension_nested_terminal")
            .unwrap();
        let admitted = reopened
            .claim_recovery_stage(extension_recovery_input(&project, "dispatching", json!({})))
            .unwrap();
        assert_eq!(admitted.payload["attempt"], 1);
        assert!(matches!(
            reopened.claim_recovery_stage(extension_recovery_input(
                &project,
                "dispatching",
                json!({})
            )),
            Err(StorageError::RequestReuse)
        ));
    }

    #[test]
    fn recovery_legacy_extension_nested_terminal_cannot_publish_a_claim() {
        reject_nested_recovery_terminal(true);
    }

    #[test]
    fn recovery_new_extension_nested_terminal_cannot_publish_a_claim() {
        reject_nested_recovery_terminal(false);
    }

    fn reject_nested_recovery_key(other_generation: bool) {
        let (_temp, path, project) = extension_order_project();
        let nested = Uuid::now_v7();
        let generation = if other_generation {
            format!("'{}'", Uuid::now_v7())
        } else {
            "NEW.generation".into()
        };
        install_order_extension(
            &path,
            &format!(
                "CREATE TRIGGER extension_nested_key AFTER INSERT ON production_receipts
             WHEN NEW.request_id='requested-recovery' AND NEW.stage='dispatching'
             BEGIN
               INSERT INTO extension_receipt_audit VALUES(NEW.receipt_id);
               INSERT INTO production_receipts
               SELECT '{nested}',NEW.project_id,{generation},NEW.revision,'nested-unadmitted-key',
                      NEW.request_sha256,NEW.command,'dispatching',NEW.payload_json,NEW.created_at;
             END;"
            ),
            true,
        );
        let mut store = Store::open(&path).unwrap();
        let before = order_extension_snapshot(&store.conn);
        let result = store.claim_recovery_stage(extension_recovery_input(
            &project,
            "dispatching",
            json!({}),
        ));
        assert!(
            result.is_err(),
            "an extension cannot add another recovery admission: {result:?}"
        );
        assert_eq!(order_extension_snapshot(&store.conn), before);
        drop(store);
        assert_eq!(
            order_extension_snapshot(&Store::open(&path).unwrap().conn),
            before
        );
    }

    #[test]
    fn recovery_extension_nested_new_key_cannot_publish_a_claim() {
        reject_nested_recovery_key(false);
    }

    #[test]
    fn recovery_extension_nested_other_generation_cannot_publish_a_claim() {
        reject_nested_recovery_key(true);
    }

    #[test]
    fn recovery_extension_cannot_rewrite_retained_proof_during_retry_claim() {
        let (_temp, path, project) = extension_order_project();
        let mut store = Store::open(&path).unwrap();
        store
            .claim_recovery_stage(extension_recovery_input(&project, "dispatching", json!({})))
            .unwrap();
        store
            .append_production_receipt(extension_recovery_input(
                &project,
                "failed_known",
                json!({"attempt":1,"retryable":true,"proof":"original"}),
            ))
            .unwrap();
        drop(store);
        install_order_extension(&path,
            "CREATE TRIGGER extension_rewrite_history AFTER INSERT ON production_receipts
             WHEN NEW.stage='dispatching' AND json_extract(NEW.payload_json,'$.attempt')=2
             BEGIN
               INSERT INTO extension_receipt_audit VALUES(NEW.receipt_id);
               UPDATE production_receipts SET payload_json=json_set(payload_json,'$.proof','changed')
               WHERE project_id=NEW.project_id AND request_id=NEW.request_id AND stage='failed_known';
             END;", false);
        let mut store = Store::open(&path).unwrap();
        let before = order_extension_snapshot(&store.conn);
        let result = store.claim_recovery_stage(extension_recovery_input(
            &project,
            "dispatching",
            json!({}),
        ));
        assert!(
            result.is_err(),
            "a still-valid chain cannot authorize rewriting retained proof: {result:?}"
        );
        assert_eq!(order_extension_snapshot(&store.conn), before);
    }

    #[test]
    fn recovery_extension_cannot_admit_retry_during_terminal_append() {
        let (_temp, path, project) = extension_order_project();
        let mut store = Store::open(&path).unwrap();
        store
            .claim_recovery_stage(extension_recovery_input(&project, "dispatching", json!({})))
            .unwrap();
        drop(store);
        let nested = Uuid::now_v7();
        install_order_extension(
            &path,
            &format!(
                "CREATE TRIGGER extension_nested_retry AFTER INSERT ON production_receipts
             WHEN NEW.request_id='requested-recovery' AND NEW.stage='failed_known'
             BEGIN
               INSERT INTO extension_receipt_audit VALUES(NEW.receipt_id);
               INSERT INTO production_receipts
               SELECT '{nested}',NEW.project_id,NEW.generation,NEW.revision,NEW.request_id,
                      NEW.request_sha256,NEW.command,'dispatching',
                      json_set(NEW.payload_json,'$.attempt',2),NEW.created_at;
             END;"
            ),
            true,
        );
        let mut store = Store::open(&path).unwrap();
        let before = order_extension_snapshot(&store.conn);
        let result = store.append_production_receipt(extension_recovery_input(
            &project,
            "failed_known",
            json!({"attempt":1,"retryable":true}),
        ));
        assert!(
            result.is_err(),
            "terminal append cannot include an unrequested next claim: {result:?}"
        );
        assert_eq!(order_extension_snapshot(&store.conn), before);
        assert_eq!(
            store
                .latest_production_receipt(project.id, "requested-recovery")
                .unwrap()
                .unwrap()
                .stage,
            "dispatching"
        );
    }

    #[test]
    fn recovery_extension_cannot_change_project_authority_during_claim() {
        let (_temp, path, project) = extension_order_project();
        install_order_extension(
            &path,
            "CREATE TRIGGER extension_change_project AFTER INSERT ON production_receipts
             WHEN NEW.request_id='requested-recovery' AND NEW.stage='dispatching'
             BEGIN
               UPDATE projects SET revision=revision+1 WHERE id=NEW.project_id;
             END;",
            false,
        );
        let mut store = Store::open(&path).unwrap();
        let before = order_extension_snapshot(&store.conn);
        let result = store.claim_recovery_stage(extension_recovery_input(
            &project,
            "dispatching",
            json!({}),
        ));
        assert!(
            result.is_err(),
            "claim cannot outlive its project revision inside the transaction: {result:?}"
        );
        assert_eq!(order_extension_snapshot(&store.conn), before);
        assert_eq!(store.load_project(project.id).unwrap(), project);
    }

    fn reject_schema3_reserved_shadow(collision: &str) {
        let (_temp, path, project) = extension_order_project();
        let mut store = Store::open(&path).unwrap();
        let original = store
            .claim_recovery_stage(extension_recovery_input(&project, "dispatching", json!({})))
            .unwrap();
        drop(store);
        install_order_extension(&path, collision, false);
        let db = rusqlite::Connection::open(&path).unwrap();
        let before = order_extension_snapshot(&db);
        drop(db);
        let result = Store::open(&path);
        assert!(
            result.is_err(),
            "schema3 must reject an extra object sharing a reserved name"
        );
        drop(result);
        let after = rusqlite::Connection::open(&path).unwrap();
        assert_eq!(order_extension_snapshot(&after), before);
        assert_eq!(
            after
                .query_row("SELECT receipt_id FROM production_receipts", [], |r| r
                    .get::<_, String>(
                    0
                ))
                .unwrap(),
            original.id.to_string()
        );
    }

    #[test]
    fn schema3_receipt_order_table_name_rejects_shadow_trigger() {
        reject_schema3_reserved_shadow(
            "CREATE TRIGGER PrOdUcTiOn_ReCeIpT_OrDeR AFTER INSERT ON production_receipts BEGIN SELECT 1; END;",
        );
    }

    #[test]
    fn schema3_receipt_order_index_name_rejects_shadow_trigger() {
        reject_schema3_reserved_shadow(
            "CREATE TRIGGER production_receipt_order_by_project AFTER INSERT ON production_receipts BEGIN SELECT 1; END;",
        );
    }

    #[test]
    fn schema3_receipt_order_trigger_name_rejects_shadow_table() {
        reject_schema3_reserved_shadow(
            "CREATE TABLE production_receipt_order_append(retained TEXT NOT NULL);
             INSERT INTO production_receipt_order_append VALUES('foreign extension bytes');",
        );
    }

    #[test]
    fn recovery_ordinary_extension_audit_remains_compatible_in_both_creation_orders() {
        for retained_v2 in [true, false] {
            let (_temp, path, project) = extension_order_project();
            install_order_extension(
                &path,
                "CREATE TRIGGER extension_ordinary_audit AFTER INSERT ON production_receipts
                 BEGIN INSERT INTO extension_receipt_audit VALUES(NEW.receipt_id); END;",
                retained_v2,
            );
            let mut store = Store::open(&path).unwrap();
            let first = store
                .claim_recovery_stage(extension_recovery_input(&project, "dispatching", json!({})))
                .unwrap();
            let failed = store
                .append_production_receipt(extension_recovery_input(
                    &project,
                    "failed_known",
                    json!({"attempt":1,"retryable":true}),
                ))
                .unwrap();
            let retry = store
                .claim_recovery_stage(extension_recovery_input(&project, "dispatching", json!({})))
                .unwrap();
            assert_eq!(retry.payload["attempt"], 2);
            drop(store);
            let reopened = Store::open(&path).unwrap();
            assert_eq!(
                reopened.production_receipts(project.id, 10).unwrap(),
                vec![retry, failed, first]
            );
            assert_eq!(
                reopened
                    .conn
                    .query_row("SELECT COUNT(*) FROM extension_receipt_audit", [], |r| r
                        .get::<_, i64>(0))
                    .unwrap(),
                3
            );
        }
    }

    struct ReceiptClockFixture {
        project: motionwright_domain::Project,
        input: ProductionReceiptInput,
        initial: ProductionReceipt,
        earlier_failure: ProductionReceipt,
        pending: ProductionReceipt,
    }

    fn retained_receipts_after_clock_reversal(path: &std::path::Path) -> ReceiptClockFixture {
        let mut store = Store::open(path).unwrap();
        let project = store.create_named_project("Durable receipt order").unwrap();
        let input = revision_binding_input(
            &project,
            "original-recovery-stage",
            "motionwright.recovery.motion-canvas",
        );
        let initial = store.claim_recovery_stage(input.clone()).unwrap();
        assert_eq!(initial.payload["attempt"], json!(1));
        let mut earlier_failure = store
            .append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({"retryable": true, "attempt": 1}),
                ..input.clone()
            })
            .unwrap();

        // Model retained bytes from an earlier process with an advanced clock.
        // No system clock is changed and no native operation is dispatched.
        let original_id = earlier_failure.id;
        earlier_failure.id = Uuid::from_u128(
            original_id
                .as_u128()
                .checked_add(86_400_000_u128 << 80)
                .unwrap(),
        );
        earlier_failure.created_at =
            (chrono::DateTime::parse_from_rfc3339(&earlier_failure.created_at).unwrap()
                + chrono::Duration::days(1))
            .to_rfc3339();
        assert_eq!(
            store
                .conn
                .execute(
                    "UPDATE production_receipts SET receipt_id=?1,created_at=?2 WHERE receipt_id=?3",
                    params![
                        earlier_failure.id.to_string(),
                        &earlier_failure.created_at,
                        original_id.to_string(),
                    ],
                )
                .unwrap(),
            1
        );
        drop(store);

        let mut reopened = Store::open(path).unwrap();
        let pending = reopened.claim_recovery_stage(input.clone()).unwrap();
        assert_eq!(pending.payload["attempt"], json!(2));
        assert!(
            pending.id < earlier_failure.id,
            "fixture must retain a later commit with a lower UUID"
        );
        assert_eq!(
            reopened
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            3
        );
        drop(reopened);
        ReceiptClockFixture {
            project,
            input,
            initial,
            earlier_failure,
            pending,
        }
    }

    #[test]
    fn commit_order_latest_receipt_survives_vacuum_and_reopen() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("latest-commit.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let store = Store::open(&path).unwrap();
        store.conn.execute_batch("VACUUM").unwrap();
        drop(store);
        let reopened = Store::open(&path).unwrap();
        assert_eq!(
            reopened
                .latest_production_receipt(fixture.project.id, &fixture.input.request_id)
                .unwrap(),
            Some(fixture.pending),
            "the durable latest receipt must not be chosen by wall-clock UUID"
        );
    }

    #[test]
    fn commit_order_watermark_tracks_lower_uuid_append() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("watermark-commit.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let reopened = Store::open(&path).unwrap();
        assert_eq!(
            reopened
                .production_receipt_watermark(fixture.project.id)
                .unwrap(),
            ProductionReceiptWatermark {
                latest_id: Some(fixture.pending.id),
                count: 3,
            },
            "watermark head must identify the last commit, including a lower UUID"
        );
    }

    #[test]
    fn commit_order_receipt_pages_preserve_history_across_decreasing_uuids() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("paged-commit.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let reopened = Store::open(&path).unwrap();
        let mut before = None;
        let mut observed = Vec::new();
        for _ in 0..4 {
            let page = reopened
                .production_receipts_before(fixture.project.id, fixture.pending.id, before, 1)
                .unwrap();
            if page.is_empty() {
                break;
            }
            assert_eq!(page.len(), 1);
            before = Some(page[0].id);
            observed.extend(page);
        }
        assert_eq!(
            observed,
            vec![fixture.pending, fixture.earlier_failure, fixture.initial],
            "UUID cursor must resolve to durable order without omitting a prior future-clock receipt"
        );
    }

    #[test]
    fn commit_order_receipt_pages_reject_missing_foreign_and_forward_anchors() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("scoped-anchors.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let mut reopened = Store::open(&path).unwrap();
        let other = reopened
            .create_named_project("Foreign cursor owner")
            .unwrap();
        let foreign = reopened
            .append_production_receipt(revision_binding_input(
                &other,
                "foreign-observation",
                "driver.motion-canvas.render.start",
            ))
            .unwrap();
        let missing = Uuid::now_v7();
        for (label, through, before) in [
            ("missing head", missing, None),
            ("foreign head", foreign.id, None),
            ("missing cursor", fixture.pending.id, Some(missing)),
            ("foreign cursor", fixture.pending.id, Some(foreign.id)),
            (
                "cursor newer than snapshot",
                fixture.initial.id,
                Some(fixture.pending.id),
            ),
        ] {
            let result =
                reopened.production_receipts_before(fixture.project.id, through, before, 2);
            assert!(
                result.is_err(),
                "{label} must not widen a page or fabricate an empty complete history: {result:?}"
            );
        }
    }

    #[test]
    fn commit_order_pending_retry_vetoes_same_key_after_reopen() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("same-key-pending.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let mut reopened = Store::open(&path).unwrap();
        let before = reopened
            .production_receipt_watermark(fixture.project.id)
            .unwrap();
        let result = reopened.claim_recovery_stage(fixture.input.clone());
        assert!(
            matches!(result, Err(StorageError::RequestReuse)),
            "pending attempt2 must not be dispatched again from earlier failed_known1: {result:?}"
        );
        assert_eq!(
            reopened
                .production_receipt_watermark(fixture.project.id)
                .unwrap(),
            before
        );
        assert_eq!(
            reopened
                .latest_production_receipt(fixture.project.id, &fixture.input.request_id)
                .unwrap(),
            Some(fixture.pending)
        );
    }

    #[test]
    fn commit_order_pending_retry_vetoes_renamed_key_after_reopen() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("renamed-key-pending.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let mut reopened = Store::open(&path).unwrap();
        let before = reopened
            .production_receipt_watermark(fixture.project.id)
            .unwrap();
        let result = reopened.claim_recovery_stage(ProductionReceiptInput {
            request_id: "renamed-recovery-stage".into(),
            request_sha256: "b".repeat(64),
            ..fixture.input.clone()
        });
        assert!(
            matches!(result, Err(StorageError::RequestReuse)),
            "renaming a request must not bypass pending original attempt2: {result:?}"
        );
        assert_eq!(
            reopened
                .production_receipt_watermark(fixture.project.id)
                .unwrap(),
            before
        );
    }

    #[test]
    fn commit_order_known_failed_retry_advances_to_next_attempt() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("known-next-attempt.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let mut store = Store::open(&path).unwrap();
        store
            .append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({"retryable": true, "attempt": 2}),
                ..fixture.input.clone()
            })
            .unwrap();
        drop(store);
        let mut reopened = Store::open(&path).unwrap();
        let third = reopened.claim_recovery_stage(fixture.input).unwrap();
        assert_eq!(
            third.payload["attempt"],
            json!(3),
            "known failed retry consumes its attempt even across lower UUIDs"
        );
        assert_eq!(third.generation, fixture.project.generation);
        assert_eq!(third.revision, fixture.project.revision);
        assert_eq!(
            reopened
                .production_receipt_watermark(fixture.project.id)
                .unwrap()
                .count,
            5
        );
    }

    #[test]
    fn commit_order_completed_retry_cannot_reopen_earlier_failure() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("completed-retry.sqlite3");
        let fixture = retained_receipts_after_clock_reversal(&path);
        let mut store = Store::open(&path).unwrap();
        let completed = store
            .append_production_receipt(ProductionReceiptInput {
                stage: "completed".into(),
                payload: json!({"attempt": 2}),
                ..fixture.input.clone()
            })
            .unwrap();
        drop(store);
        let mut reopened = Store::open(&path).unwrap();
        let before = reopened
            .production_receipt_watermark(fixture.project.id)
            .unwrap();
        let result = reopened.claim_recovery_stage(fixture.input.clone());
        assert!(
            matches!(result, Err(StorageError::RequestReuse)),
            "completed attempt2 must not reopen from an older failed_known1: {result:?}"
        );
        assert_eq!(
            reopened
                .production_receipt_watermark(fixture.project.id)
                .unwrap(),
            before
        );
        assert_eq!(
            reopened
                .latest_production_receipt(fixture.project.id, &fixture.input.request_id)
                .unwrap(),
            Some(completed)
        );
    }

    fn admitted_recovery_retry(
        path: &std::path::Path,
    ) -> (
        Store,
        motionwright_domain::Project,
        ProductionReceiptInput,
        ProductionReceipt,
    ) {
        let mut store = Store::open(path).unwrap();
        let project = store
            .create_named_project("Recovery attempt scope")
            .unwrap();
        let input = revision_binding_input(
            &project,
            "attempt-bound-stage",
            "motionwright.recovery.motion-canvas",
        );
        store.claim_recovery_stage(input.clone()).unwrap();
        store
            .append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({"attempt": 1, "retryable": true}),
                ..input.clone()
            })
            .unwrap();
        let pending = store.claim_recovery_stage(input.clone()).unwrap();
        assert_eq!(pending.payload["attempt"], json!(2));
        (store, project, input, pending)
    }

    #[test]
    fn recovery_old_attempt_result_cannot_close_active_retry() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("late-attempt.sqlite3");
        let (mut store, project, input, pending) = admitted_recovery_retry(&path);
        let before = store.production_receipt_watermark(project.id).unwrap();
        for (stage, payload) in [
            ("completed", json!({"attempt": 1})),
            ("failed_known", json!({"attempt": 1, "retryable": true})),
            ("outcome_unknown", json!({"attempt": 1})),
            ("completed", json!({"attempt": 3})),
            ("completed", json!({})),
        ] {
            let result = store.append_production_receipt(ProductionReceiptInput {
                stage: stage.into(),
                payload,
                ..input.clone()
            });
            assert!(
                matches!(result, Err(StorageError::RequestReuse)),
                "only the active admitted attempt can be observed: {result:?}"
            );
            assert_eq!(
                store.production_receipt_watermark(project.id).unwrap(),
                before
            );
        }
        drop(store);
        let mut reopened = Store::open(&path).unwrap();
        assert_eq!(
            reopened
                .latest_production_receipt(project.id, &input.request_id)
                .unwrap(),
            Some(pending)
        );
        assert!(matches!(
            reopened.claim_recovery_stage(input.clone()),
            Err(StorageError::RequestReuse)
        ));
        assert!(matches!(
            reopened.claim_recovery_stage(ProductionReceiptInput {
                request_id: "renamed-after-late-observer".into(),
                request_sha256: "b".repeat(64),
                ..input
            }),
            Err(StorageError::RequestReuse)
        ));
        assert_eq!(
            reopened.production_receipt_watermark(project.id).unwrap(),
            before
        );
    }

    #[test]
    fn recovery_terminal_observation_cannot_be_reclassified_as_retryable() {
        for terminal in ["completed", "outcome_unknown", "failed_known"] {
            let temp = tempfile::tempdir().unwrap();
            let path = temp.path().join("terminal-stability.sqlite3");
            let mut store = Store::open(&path).unwrap();
            let project = store.create_named_project("Terminal observation").unwrap();
            let input = revision_binding_input(
                &project,
                "terminal-stage",
                "motionwright.recovery.motion-canvas",
            );
            store.claim_recovery_stage(input.clone()).unwrap();
            let original = store
                .append_production_receipt(ProductionReceiptInput {
                    stage: terminal.into(),
                    payload: json!({"attempt": 1, "retryable": false}),
                    ..input.clone()
                })
                .unwrap();
            drop(store);
            let mut reopened = Store::open(&path).unwrap();
            let before = reopened.production_receipt_watermark(project.id).unwrap();
            let result = reopened.append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({"attempt": 1, "retryable": true}),
                ..input.clone()
            });
            assert!(
                matches!(result, Err(StorageError::RequestReuse)),
                "a generic append cannot downgrade {terminal}: {result:?}"
            );
            assert_eq!(
                reopened
                    .latest_production_receipt(project.id, &input.request_id)
                    .unwrap(),
                Some(original)
            );
            assert!(matches!(
                reopened.claim_recovery_stage(input),
                Err(StorageError::RequestReuse)
            ));
            assert_eq!(
                reopened.production_receipt_watermark(project.id).unwrap(),
                before
            );
        }
    }

    #[test]
    fn recovery_receipt_append_cannot_bypass_stage_admission() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("no-admission.sqlite3")).unwrap();
        let project = store
            .create_named_project("Only claim can admit recovery")
            .unwrap();
        let input = revision_binding_input(
            &project,
            "unadmitted-stage",
            "motionwright.recovery.motion-canvas",
        );
        for stage in [
            "dispatching",
            "failed_known",
            "completed",
            "outcome_unknown",
        ] {
            let result = store.append_production_receipt(ProductionReceiptInput {
                stage: stage.into(),
                payload: json!({"attempt": 1, "retryable": true}),
                ..input.clone()
            });
            assert!(
                matches!(result, Err(StorageError::RequestReuse)),
                "generic append cannot create an unadmitted recovery history: {result:?}"
            );
            assert_eq!(
                store
                    .production_receipt_watermark(project.id)
                    .unwrap()
                    .count,
                0
            );
        }
        let admitted = store.claim_recovery_stage(input).unwrap();
        assert_eq!(admitted.payload["attempt"], json!(1));
        assert_eq!(
            store
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            1
        );
    }

    #[test]
    fn recovery_ambiguous_retained_attempt_history_cannot_authorize_replay() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("ambiguous-retained-history.sqlite3");
        let (store, project, input, _) = admitted_recovery_retry(&path);
        // Retained bytes from an older writer: a terminal for attempt1 arrived
        // after attempt2's admission. Keep it available for audit, never as proof.
        store
            .conn
            .execute(
                "INSERT INTO production_receipts(
               receipt_id,project_id,generation,revision,request_id,request_sha256,
               command,stage,payload_json,created_at
             ) VALUES(?1,?2,?3,?4,?5,?6,?7,'failed_known',?8,?9)",
                params![
                    Uuid::now_v7().to_string(),
                    project.id.to_string(),
                    project.generation.to_string(),
                    project.revision as i64,
                    &input.request_id,
                    &input.request_sha256,
                    &input.command,
                    r#"{"attempt":1,"retryable":true}"#,
                    chrono::Utc::now().to_rfc3339()
                ],
            )
            .unwrap();
        drop(store);
        let mut reopened = Store::open(&path).unwrap();
        let before = reopened.production_receipt_watermark(project.id).unwrap();
        assert_eq!(before.count, 4);
        assert!(
            reopened
                .latest_production_receipt(project.id, &input.request_id)
                .is_err(),
            "ambiguous history must not return a retryable authority"
        );
        assert!(matches!(
            reopened.claim_recovery_stage(input.clone()),
            Err(StorageError::RequestReuse)
        ));
        assert!(matches!(
            reopened.claim_recovery_stage(ProductionReceiptInput {
                request_id: "renamed-ambiguous-stage".into(),
                request_sha256: "b".repeat(64),
                ..input
            }),
            Err(StorageError::RequestReuse)
        ));
        assert_eq!(
            reopened.production_receipt_watermark(project.id).unwrap(),
            before
        );
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap().len(),
            4,
            "ambiguous history remains available to read-only audit"
        );
    }
}
