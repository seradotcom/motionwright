use crate::{Result, StorageError, Store};
use motionwright_domain::DomainError;
use rusqlite::{OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
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

/// Snapshot identity for an append-only local receipt stream. The sequence
/// count detects new writes that sort before the UUIDv7 latest-id watermark.
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

impl Store {
    /// Atomically claim one application-owned recovery stage. This is deliberately
    /// NOT a Semwright command: the Driver Host retains runtime authority.
    /// An unfinished claim survives process death and blocks blind replay.
    pub fn claim_recovery_stage(
        &mut self,
        mut input: ProductionReceiptInput,
    ) -> Result<ProductionReceipt> {
        if !matches!(
            input.command.as_str(),
            "motionwright.recovery.blender" | "motionwright.recovery.motion-canvas"
        ) || input.stage != "dispatching"
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
        // BEGIN IMMEDIATE serializes claims even across independent processes.
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
        validate_original_production_binding(&tx, &input)?;
        // A changed input fingerprint (or newly named workflow) must NOT
        // bypass an earlier uncertain mutation of the same native provider.
        // Read the latest receipt for every stage key, not historic dispatches
        // that were later resolved. The query runs under the write claim lock.
        let unresolved: Option<String> = tx
            .query_row(
                "SELECT current.request_id
                 FROM production_receipts AS current
                 JOIN (
                   SELECT request_id,MAX(receipt_id) AS latest
                   FROM production_receipts
                   WHERE project_id=?1 AND generation=?2 AND command=?3
                   GROUP BY request_id
                 ) AS recent ON current.receipt_id=recent.latest
                 WHERE (current.stage IN ('dispatching','outcome_unknown')
                   OR (current.stage='failed_known' AND current.command='motionwright.recovery.blender'))
                   AND current.request_id<>?4
                 LIMIT 1",
                params![
                    input.project_id.to_string(),
                    input.generation.to_string(),
                    input.command,
                    input.request_id,
                ],
                |row| row.get(0),
            )
            .optional()?;
        if unresolved.is_some() {
            return Err(StorageError::RequestReuse);
        }
        let prior: Option<(String, String, String, String)> = tx
            .query_row(
                "SELECT request_sha256,command,stage,payload_json
                 FROM production_receipts WHERE project_id=?1 AND request_id=?2
                 ORDER BY receipt_id DESC LIMIT 1",
                params![input.project_id.to_string(), input.request_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .optional()?;
        let attempt = match prior {
            None => 1_u64,
            Some((digest, command, stage, payload)) => {
                if digest != input.request_sha256 || command != input.command {
                    return Err(StorageError::RequestReuse);
                }
                let payload: Value = serde_json::from_str(&payload)?;
                if stage != "failed_known"
                    || input.command != "motionwright.recovery.motion-canvas"
                    || payload.get("retryable").and_then(Value::as_bool) != Some(true)
                {
                    return Err(StorageError::RequestReuse);
                }
                payload
                    .get("attempt")
                    .and_then(Value::as_u64)
                    .filter(|n| (1..16).contains(n))
                    .ok_or(StorageError::RequestReuse)?
                    + 1
            }
        };
        let data = input.payload.as_object_mut().ok_or_else(|| {
            StorageError::Domain(DomainError::Invalid(
                "Recovery payload is not an object".into(),
            ))
        })?;
        data.insert("attempt".into(), serde_json::json!(attempt));
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
        tx.execute(
            "INSERT INTO production_receipts(
                receipt_id,project_id,generation,revision,request_id,request_sha256,
                command,stage,payload_json,created_at
             ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
            params![
                receipt.id.to_string(),
                receipt.project_id.to_string(),
                receipt.generation.to_string(),
                receipt.revision as i64,
                &receipt.request_id,
                &receipt.request_sha256,
                &receipt.command,
                &receipt.stage,
                serde_json::to_string(&receipt.payload)?,
                &receipt.created_at,
            ],
        )?;
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

        // Serialize the project check, request binding and append across
        // connections. Reading before the write lock lets competing writers
        // both observe an unused request key.
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

        let has_prior = validate_original_production_binding(&tx, &input)?;
        // Observations may extend an existing request, but cannot grant a new
        // dispatch. Explicit recovery retries use claim_recovery_stage.
        if has_prior && input.stage == "dispatching" {
            return Err(StorageError::RequestReuse);
        }

        // A caller's preflight may predate a peer commit. A new dispatch must
        // still match the head while this transaction holds the write lock.
        // Observations of an admitted request retain their original revision.
        if input.stage == "dispatching" && input.revision != current.revision {
            return Err(StorageError::Conflict {
                expected: input.revision,
                actual: current.revision,
            });
        }

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
        tx.execute(
            "INSERT INTO production_receipts(
                receipt_id,project_id,generation,revision,request_id,request_sha256,
                command,stage,payload_json,created_at
             ) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
            params![
                receipt.id.to_string(),
                receipt.project_id.to_string(),
                receipt.generation.to_string(),
                receipt.revision as i64,
                &receipt.request_id,
                &receipt.request_sha256,
                &receipt.command,
                &receipt.stage,
                serde_json::to_string(&receipt.payload)?,
                &receipt.created_at,
            ],
        )?;
        tx.commit()?;
        Ok(receipt)
    }

    pub fn latest_production_receipt(
        &self,
        project_id: Uuid,
        request_id: &str,
    ) -> Result<Option<ProductionReceipt>> {
        bounded_token(request_id, 256, "production request id")?;
        self.conn
            .query_row(
                "SELECT receipt_id,generation,revision,request_id,request_sha256,
                        command,stage,payload_json,created_at
                 FROM production_receipts
                 WHERE project_id=?1 AND request_id=?2
                 ORDER BY receipt_id DESC LIMIT 1",
                params![project_id.to_string(), request_id],
                |row| {
                    let id: String = row.get(0)?;
                    let generation: String = row.get(1)?;
                    let revision: i64 = row.get(2)?;
                    let payload: String = row.get(7)?;
                    Ok((
                        id,
                        generation,
                        revision,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, String>(6)?,
                        payload,
                        row.get::<_, String>(8)?,
                    ))
                },
            )
            .optional()?
            .map(
                |(
                    id,
                    generation,
                    revision,
                    request_id,
                    request_sha256,
                    command,
                    stage,
                    payload,
                    created_at,
                )| {
                    Ok(ProductionReceipt {
                        id: Uuid::parse_str(&id).map_err(|_| {
                            StorageError::Domain(DomainError::Invalid(
                                "stored production receipt id is invalid".into(),
                            ))
                        })?,
                        project_id,
                        generation: Uuid::parse_str(&generation).map_err(|_| {
                            StorageError::Domain(DomainError::Invalid(
                                "stored production generation is invalid".into(),
                            ))
                        })?,
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
                },
            )
            .transpose()
    }

    /// High watermark for this project's local append-only production
    /// receipts, independent of the creative project revision.
    pub fn production_receipt_watermark(
        &self,
        project_id: Uuid,
    ) -> Result<ProductionReceiptWatermark> {
        let (latest, count): (Option<String>, i64) = self.conn.query_row(
            "SELECT MAX(receipt_id),COUNT(*) FROM production_receipts WHERE project_id=?1",
            params![project_id.to_string()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        Ok(ProductionReceiptWatermark {
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
        })
    }

    /// Bounded descending keyset read. A caller must compare the stream
    /// watermark before and after reading; a new receipt can arrive without
    /// changing the project's creative revision.
    pub fn production_receipts_before(
        &self,
        project_id: Uuid,
        through_id: Uuid,
        before_id: Option<Uuid>,
        limit: usize,
    ) -> Result<Vec<ProductionReceipt>> {
        let limit = limit.clamp(1, 256);
        let before = before_id.map(|id| id.to_string());
        let mut stmt = self.conn.prepare(
            "SELECT receipt_id,generation,revision,request_id,request_sha256,
                    command,stage,payload_json,created_at
             FROM production_receipts
             WHERE project_id=?1
               AND receipt_id <= ?2
               AND (?3 IS NULL OR receipt_id < ?3)
             ORDER BY receipt_id DESC LIMIT ?4",
        )?;
        let rows = stmt.query_map(
            params![
                project_id.to_string(),
                through_id.to_string(),
                before,
                limit as i64
            ],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                ))
            },
        )?;
        let mut receipts = Vec::new();
        for row in rows {
            let (
                id,
                generation,
                revision,
                request_id,
                request_sha256,
                command,
                stage,
                payload,
                created_at,
            ) = row?;
            receipts.push(ProductionReceipt {
                id: Uuid::parse_str(&id).map_err(|_| {
                    StorageError::Domain(DomainError::Invalid(
                        "Stored production receipt ID is invalid".into(),
                    ))
                })?,
                project_id,
                generation: Uuid::parse_str(&generation).map_err(|_| {
                    StorageError::Domain(DomainError::Invalid(
                        "Stored production generation is invalid".into(),
                    ))
                })?,
                revision: u64::try_from(revision).map_err(|_| {
                    StorageError::Domain(DomainError::Invalid(
                        "Stored production revision is invalid".into(),
                    ))
                })?,
                request_id,
                request_sha256,
                command,
                stage,
                payload: serde_json::from_str(&payload)?,
                created_at,
            });
        }
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
        for index in 0..311 {
            store
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
        assert!(items.windows(2).all(|pair| pair[0] > pair[1]));
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
        assert!(
            store
                .production_receipts_before(other.id, head.latest_id.unwrap(), None, 16)
                .unwrap()
                .is_empty()
        );
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
