use crate::{Result, StorageError, Store};
use motionwright_domain::DomainError;
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

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
    pub fn append_production_receipt(
        &mut self,
        project_id: Uuid,
        generation: Uuid,
        revision: u64,
        request_id: &str,
        request_sha256: &str,
        command: &str,
        stage: &str,
        payload: Value,
    ) -> Result<ProductionReceipt> {
        bounded_token(request_id, 256, "production request id")?;
        bounded_token(command, 256, "production command")?;
        bounded_token(stage, 64, "production stage")?;
        if request_sha256.len() != 64
            || !request_sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(StorageError::Domain(DomainError::Invalid(
                "production request digest is invalid".into(),
            )));
        }

        let current = self.load_project(project_id)?;
        if current.generation != generation {
            return Err(StorageError::GenerationConflict);
        }
        if revision > current.revision {
            return Err(StorageError::Conflict {
                expected: revision,
                actual: current.revision,
            });
        }

        let prior: Option<(String, String)> = self
            .conn
            .query_row(
                "SELECT request_sha256,command FROM production_receipts
                 WHERE project_id=?1 AND request_id=?2
                 ORDER BY receipt_id DESC LIMIT 1",
                params![project_id.to_string(), request_id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        if let Some((prior_sha, prior_command)) = prior
            && (prior_sha != request_sha256 || prior_command != command)
        {
            return Err(StorageError::RequestReuse);
        }

        let receipt = ProductionReceipt {
            id: Uuid::now_v7(),
            project_id,
            generation,
            revision,
            request_id: request_id.to_owned(),
            request_sha256: request_sha256.to_owned(),
            command: command.to_owned(),
            stage: stage.to_owned(),
            payload,
            created_at: chrono::Utc::now().to_rfc3339(),
        };
        self.conn.execute(
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

    pub fn production_receipts(
        &self,
        project_id: Uuid,
        limit: usize,
    ) -> Result<Vec<ProductionReceipt>> {
        let limit = limit.clamp(1, 256);
        let mut stmt = self.conn.prepare(
            "SELECT receipt_id,generation,revision,request_id,request_sha256,
                    command,stage,payload_json,created_at
             FROM production_receipts
             WHERE project_id=?1
             ORDER BY receipt_id DESC LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![project_id.to_string(), limit as i64], |row| {
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
        })?;
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
            });
        }
        Ok(receipts)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn production_receipts_are_revision_bound_and_request_digest_stable() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let project = store.create_named_project("Production fixture").unwrap();
        let sha = "ab".repeat(32);

        store
            .append_production_receipt(
                project.id,
                project.generation,
                project.revision,
                "request-one",
                &sha,
                "driver.motion-canvas.composition.inspect",
                "dispatching",
                json!({"mutation":false}),
            )
            .unwrap();
        store
            .append_production_receipt(
                project.id,
                project.generation,
                project.revision,
                "request-one",
                &sha,
                "driver.motion-canvas.composition.inspect",
                "completed",
                json!({"result":{"ok":true}}),
            )
            .unwrap();

        let latest = store
            .latest_production_receipt(project.id, "request-one")
            .unwrap()
            .unwrap();
        assert_eq!(latest.stage, "completed");
        assert_eq!(store.production_receipts(project.id, 10).unwrap().len(), 2);

        assert!(matches!(
            store.append_production_receipt(
                project.id,
                project.generation,
                project.revision,
                "request-one",
                &"cd".repeat(32),
                "driver.motion-canvas.composition.inspect",
                "dispatching",
                json!({})
            ),
            Err(StorageError::RequestReuse)
        ));
        assert!(matches!(
            store.append_production_receipt(
                project.id,
                project.generation,
                project.revision + 1,
                "future",
                &sha,
                "driver.motion-canvas.composition.inspect",
                "dispatching",
                json!({})
            ),
            Err(StorageError::Conflict { .. })
        ));
    }
}
