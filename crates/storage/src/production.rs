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

        let prior: Option<(String, String)> = tx
            .query_row(
                "SELECT request_sha256,command FROM production_receipts
                 WHERE project_id=?1 AND request_id=?2
                 ORDER BY receipt_id DESC LIMIT 1",
                params![input.project_id.to_string(), &input.request_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        // Observations may extend an existing request, but cannot grant a new
        // dispatch. Explicit recovery retries use claim_recovery_stage.
        if let Some((prior_sha, prior_command)) = prior
            && (prior_sha != input.request_sha256
                || prior_command != input.command
                || input.stage == "dispatching")
        {
            return Err(StorageError::RequestReuse);
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
}
