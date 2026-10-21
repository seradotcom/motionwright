use super::{ApplyOutcome, Result, StorageError, Store};
use motionwright_domain::{DomainError, Project};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

/// Retain this exact intent before dispatch. It is an operation identity, not
/// an authorization grant or permission to retry an uncertain operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectCreationIntent {
    pub request_id: Uuid,
    pub title: String,
}

/// The original revision-zero document, independent of the current head.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectCreationOutcome {
    pub request_id: Uuid,
    pub project: Project,
    pub replayed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum ReceiptFormat {
    #[serde(rename = "motionwright.project-creation-receipt/1")]
    V1,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CreationReceipt {
    format: ReceiptFormat,
    intent: ProjectCreationIntent,
    // Hash the preserved bytes, not a reserialized future Project. Additive
    // domain defaults must not invalidate an authentic historical receipt.
    project_json: String,
    project_sha256: String,
}

fn invalid_receipt(message: &str) -> StorageError {
    StorageError::InvalidProjectCreationReceipt(message.into())
}

impl ProjectCreationIntent {
    /// Validate the exact input before the caller requests an authorization
    /// grant. This check neither creates a project nor proves consent.
    pub fn validate(&self) -> Result<()> {
        if self.request_id.is_nil() {
            return Err(DomainError::Invalid("creation request id must not be nil".into()).into());
        }
        if self.title.trim().is_empty() || self.title.len() > 200 {
            return Err(DomainError::Invalid("project title is out of bounds".into()).into());
        }
        Ok(())
    }
}

fn project_id(request_id: Uuid) -> Uuid {
    let hash = Sha256::new()
        .chain_update(b"motionwright.project-creation-id/1\0")
        .chain_update(request_id.as_bytes())
        .finalize();
    let mut bytes = [0_u8; 16];
    bytes.copy_from_slice(&hash[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x80;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Uuid::from_bytes(bytes)
}

fn receipt_key(request_id: Uuid) -> String {
    format!("project-create/1:{request_id}")
}

fn request_digest(intent: &ProjectCreationIntent) -> Result<String> {
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(&(
        "motionwright.project-creation-request/1",
        intent.request_id,
        &intent.title,
    ))?)))
}

/// Classify only the typed purpose. An import rejects this envelope; this
/// function does not authenticate or recover its contents.
pub(super) fn is_creation_receipt(json: &str) -> bool {
    serde_json::from_str::<CreationReceipt>(json).is_ok()
}

fn read_creation(
    conn: &Connection,
    intent: &ProjectCreationIntent,
) -> Result<Option<ProjectCreationOutcome>> {
    let id = project_id(intent.request_id);
    let key = receipt_key(intent.request_id);
    let stored =
        super::apply_receipt_binding::read_receipt(conn, id, &key).map_err(|error| match error
            .into_storage_error()
        {
            StorageError::InvalidApplyReceipt(reason) => invalid_receipt(&reason),
            other => other,
        })?;
    let Some(stored) = stored else {
        return Ok(None);
    };
    let json = &stored.result_json;
    let receipt: CreationReceipt = match serde_json::from_str(json) {
        Ok(receipt) => receipt,
        Err(_) if serde_json::from_str::<ApplyOutcome>(json).is_ok() => {
            return Err(StorageError::RequestReuse);
        }
        Err(_) => {
            return Err(invalid_receipt(
                "unrecognized or malformed creation receipt",
            ));
        }
    };
    // Creation has its own revision-zero envelope and no Change event.
    // A generic apply marker must never upgrade or reinterpret this family.
    if !stored.is_legacy_marker() {
        return Err(invalid_receipt(
            "creation receipt carries a generic apply binding",
        ));
    }
    receipt
        .intent
        .validate()
        .map_err(|_| invalid_receipt("invalid recorded creation intent"))?;
    if receipt.intent.request_id != intent.request_id
        || request_digest(&receipt.intent)? != stored.request_sha256
        || hex::encode(Sha256::digest(receipt.project_json.as_bytes())) != receipt.project_sha256
    {
        return Err(invalid_receipt(
            "inconsistent recorded intent or result digest",
        ));
    }
    let original: Project = serde_json::from_str(&receipt.project_json)
        .map_err(|_| invalid_receipt("invalid original project JSON"))?;
    original
        .validate()
        .map_err(|_| invalid_receipt("invalid original project document"))?;
    if original.id != id
        || original.generation.is_nil()
        || original.revision != 0
        || original.title != receipt.intent.title
    {
        return Err(invalid_receipt(
            "inconsistent original creation identity or revision",
        ));
    }

    let current: Option<(String, i64, String, String)> = conn
        .query_row(
            "SELECT generation,revision,title,document_json FROM projects WHERE id=?1",
            [id.to_string()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()?;
    let Some((generation, revision, title, current_json)) = current else {
        return Err(invalid_receipt("creation receipt has no current project"));
    };
    let current: Project = serde_json::from_str(&current_json)
        .map_err(|_| invalid_receipt("invalid current project JSON"))?;
    current
        .validate()
        .map_err(|_| invalid_receipt("invalid current project document"))?;
    if current.id != id
        || current.generation.to_string() != generation
        || i64::try_from(current.revision).ok() != Some(revision)
        || current.title != title
    {
        return Err(invalid_receipt(
            "current project authority columns disagree",
        ));
    }
    if current.generation != original.generation {
        return Err(StorageError::GenerationConflict);
    }
    if current.revision == 0 && current != original {
        return Err(invalid_receipt(
            "revision-zero head differs from original creation",
        ));
    }
    if receipt.intent != *intent {
        return Err(StorageError::RequestReuse);
    }
    Ok(Some(ProjectCreationOutcome {
        request_id: intent.request_id,
        project: original,
        replayed: true,
    }))
}

impl Store {
    /// Commit one project and its durable creation receipt atomically.
    ///
    /// The caller must authorize the initial dispatch. Recovery returning None
    /// never authorizes retry: deleting a project also cascades its receipts.
    pub fn create_project_with_intent(
        &mut self,
        intent: &ProjectCreationIntent,
    ) -> Result<ProjectCreationOutcome> {
        intent.validate()?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(outcome) = read_creation(&tx, intent)? {
            tx.commit()?;
            return Ok(outcome);
        }
        let id = project_id(intent.request_id);
        let occupied: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM projects WHERE id=?1)",
            [id.to_string()],
            |row| row.get(0),
        )?;
        if occupied {
            return Err(StorageError::ProjectExists);
        }
        let mut project = Project::new(intent.title.clone())?;
        // Project::new initializes independent branch/profile identifiers;
        // none are derived from or reference its initial random project ID.
        project.id = id;
        project.validate()?;
        let project_json = serde_json::to_string(&project)?;
        let receipt = CreationReceipt {
            format: ReceiptFormat::V1,
            intent: intent.clone(),
            project_sha256: hex::encode(Sha256::digest(project_json.as_bytes())),
            project_json: project_json.clone(),
        };
        tx.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)
             VALUES(?1,?2,0,?3,?4,?5)",
            params![
                project.id.to_string(),
                project.generation.to_string(),
                project.title,
                project_json,
                project.updated_at.to_rfc3339(),
            ],
        )?;
        tx.execute(
            "INSERT INTO receipts(project_id,request_id,request_sha256,result_json,created_at,
                                  apply_binding_version,apply_binding_json)
             VALUES(?1,?2,?3,?4,?5,0,NULL)",
            params![
                id.to_string(),
                receipt_key(intent.request_id),
                request_digest(intent)?,
                serde_json::to_string(&receipt)?,
                project.updated_at.to_rfc3339(),
            ],
        )?;
        // Initial revision zero has no Change event. Do not fabricate one.
        tx.commit()?;
        Ok(ProjectCreationOutcome {
            request_id: intent.request_id,
            project,
            replayed: false,
        })
    }

    /// Observe a receipt in one read snapshot, without creating or repairing
    /// anything. Some returns the original document, not the latest head.
    pub fn recover_project_creation(
        &mut self,
        intent: &ProjectCreationIntent,
    ) -> Result<Option<ProjectCreationOutcome>> {
        intent.validate()?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)?;
        let outcome = read_creation(&tx, intent)?;
        tx.commit()?;
        Ok(outcome)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::FileImportIntent;
    use motionwright_domain::Change;
    use std::sync::{Arc, Barrier};

    fn intent(title: &str) -> ProjectCreationIntent {
        ProjectCreationIntent {
            request_id: Uuid::now_v7(),
            title: title.into(),
        }
    }

    fn counts(store: &Store) -> (i64, i64, i64) {
        store
            .conn
            .query_row(
                "SELECT (SELECT COUNT(*) FROM projects),
                    (SELECT COUNT(*) FROM receipts),
                    (SELECT COUNT(*) FROM events)",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap()
    }

    fn snapshot(store: &Store) -> Vec<Vec<Vec<String>>> {
        [
            "SELECT * FROM projects ORDER BY id",
            "SELECT * FROM receipts ORDER BY project_id,request_id",
            "SELECT * FROM events ORDER BY event_id",
        ]
        .into_iter()
        .map(|sql| {
            let mut statement = store.conn.prepare(sql).unwrap();
            let columns = statement.column_count();
            statement
                .query_map([], |row| {
                    (0..columns)
                        .map(|index| row.get_ref(index).map(|value| format!("{value:?}")))
                        .collect()
                })
                .unwrap()
                .collect::<rusqlite::Result<Vec<Vec<String>>>>()
                .unwrap()
        })
        .collect()
    }

    fn read_only(store: &Store) {
        store.conn.execute_batch("PRAGMA query_only=ON").unwrap();
    }

    #[test]
    fn receipt_failure_rolls_back_project_and_initial_journal() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
        let request = intent("Atomic creation");
        store
            .conn
            .execute_batch(
                "CREATE TRIGGER reject_creation_receipt BEFORE INSERT ON receipts
             BEGIN SELECT RAISE(ABORT, 'synthetic receipt insertion failure'); END;",
            )
            .unwrap();
        assert!(matches!(
            store.create_project_with_intent(&request),
            Err(StorageError::Sql(_))
        ));
        assert_eq!(counts(&store), (0, 0, 0));
        store
            .conn
            .execute_batch("DROP TRIGGER reject_creation_receipt")
            .unwrap();
        let created = store.create_project_with_intent(&request).unwrap();
        assert_eq!(created.project.id, project_id(request.request_id));
        assert_eq!(created.project.revision, 0);
        assert_eq!(counts(&store), (1, 1, 0));
    }

    #[test]
    fn lost_response_recovers_original_after_reopen_without_changing_current_head() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let request = intent("  Original title with spaces  ");
        // The caller retains bytes before dispatch; the simulated transport
        // then withholds the committed response from that caller.
        let retained = directory.path().join("creation-intent.json");
        std::fs::write(&retained, serde_json::to_vec(&request).unwrap()).unwrap();
        let mut backend = Store::open(&database).unwrap();
        let committed = backend.create_project_with_intent(&request).unwrap();
        let head = backend
            .apply(
                committed.project.id,
                &committed.project.stamp(),
                "later-edit",
                &Change::RenameProject {
                    title: "Current head after creation".into(),
                },
            )
            .unwrap()
            .project;
        drop(backend);

        let mut reopened = Store::open(&database).unwrap();
        let original_intent: ProjectCreationIntent =
            serde_json::from_slice(&std::fs::read(retained).unwrap()).unwrap();
        let before = snapshot(&reopened);
        read_only(&reopened);
        let changed_before: i64 = reopened
            .conn
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        let recovered = reopened
            .recover_project_creation(&original_intent)
            .unwrap()
            .unwrap();
        assert_eq!(recovered.project, committed.project);
        assert_eq!(recovered.request_id, request.request_id);
        assert!(recovered.replayed);
        assert_eq!(recovered.project.revision, 0);
        assert_eq!(reopened.load_project(head.id).unwrap(), head);
        assert_eq!(snapshot(&reopened), before);
        let changed_after: i64 = reopened
            .conn
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(changed_after, changed_before);
        assert!(
            reopened
                .recover_project_creation(&intent("Unknown intention"))
                .unwrap()
                .is_none()
        );
        assert_eq!(snapshot(&reopened), before);
    }

    #[test]
    fn malformed_or_inconsistent_creation_receipts_are_never_absent_or_repaired() {
        for case in [
            "request_digest",
            "result_digest",
            "request_id",
            "title",
            "project_id",
            "revision",
            "format",
            "duplicate_field",
        ] {
            let directory = tempfile::tempdir().unwrap();
            let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
            let request = intent("Original");
            let original = store.create_project_with_intent(&request).unwrap().project;
            let key = receipt_key(request.request_id);
            let json: String = store
                .conn
                .query_row(
                    "SELECT result_json FROM receipts WHERE project_id=?1 AND request_id=?2",
                    params![original.id.to_string(), key],
                    |row| row.get(0),
                )
                .unwrap();
            let mut receipt: CreationReceipt = serde_json::from_str(&json).unwrap();
            let mut stored_digest = request_digest(&request).unwrap();
            match case {
                "request_digest" => stored_digest = "0".repeat(64),
                "result_digest" => receipt.project_sha256 = "0".repeat(64),
                "request_id" => {
                    receipt.intent.request_id = Uuid::now_v7();
                    stored_digest = request_digest(&receipt.intent).unwrap();
                }
                "title" | "project_id" | "revision" => {
                    let mut result = original.clone();
                    match case {
                        "title" => result.title = "Altered original".into(),
                        "project_id" => result.id = Uuid::now_v7(),
                        "revision" => result.revision = 1,
                        _ => unreachable!(),
                    }
                    receipt.project_json = serde_json::to_string(&result).unwrap();
                    receipt.project_sha256 =
                        hex::encode(Sha256::digest(receipt.project_json.as_bytes()));
                }
                "format" | "duplicate_field" => {}
                _ => unreachable!(),
            }
            let mut changed = serde_json::to_string(&receipt).unwrap();
            if case == "format" {
                changed =
                    changed.replace("project-creation-receipt/1", "project-creation-receipt/99");
            } else if case == "duplicate_field" {
                changed.insert_str(1, "\"format\":\"motionwright.project-creation-receipt/1\",");
            }
            store
                .conn
                .execute(
                    "UPDATE receipts SET result_json=?3,request_sha256=?4
                 WHERE project_id=?1 AND request_id=?2",
                    params![original.id.to_string(), key, changed, stored_digest],
                )
                .unwrap();
            let before = snapshot(&store);
            read_only(&store);
            assert!(
                matches!(
                    store.recover_project_creation(&request),
                    Err(StorageError::InvalidProjectCreationReceipt(_))
                ),
                "case={case}"
            );
            assert_eq!(snapshot(&store), before, "case={case}");
        }
    }

    #[test]
    fn imported_project_is_not_adopted_as_original_creation() {
        let directory = tempfile::tempdir().unwrap();
        let mut source = Store::open(directory.path().join("source/state.sqlite3")).unwrap();
        let request = intent("Portable creation");
        let original = source.create_project_with_intent(&request).unwrap().project;
        let backup = source.export_project(original.id).unwrap();
        let mut destination =
            Store::open(directory.path().join("destination/state.sqlite3")).unwrap();
        let imported = destination.import_project(&backup).unwrap();
        assert_eq!(imported.id, original.id);
        assert_ne!(imported.generation, original.generation);
        assert_eq!(counts(&destination), (1, 0, 0));
        let before = snapshot(&destination);
        assert!(
            destination
                .recover_project_creation(&request)
                .unwrap()
                .is_none()
        );
        assert!(matches!(
            destination.create_project_with_intent(&request),
            Err(StorageError::ProjectExists)
        ));
        assert_eq!(snapshot(&destination), before);
        assert_eq!(destination.load_project(imported.id).unwrap(), imported);

        source
            .conn
            .execute(
                "DELETE FROM projects WHERE id=?1",
                [original.id.to_string()],
            )
            .unwrap();
        assert_eq!(counts(&source), (0, 0, 0));
        read_only(&source);
        assert!(source.recover_project_creation(&request).unwrap().is_none());
        // No retry follows None: cascade removed the deduplication evidence.
        assert_eq!(counts(&source), (0, 0, 0));
    }

    #[test]
    fn current_generation_and_sql_authority_must_still_match_the_receipt() {
        for rotate_generation in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
            let request = intent("Current authority");
            let mut current = store.create_project_with_intent(&request).unwrap().project;
            if rotate_generation {
                current.generation = Uuid::now_v7();
                store
                    .conn
                    .execute(
                        "UPDATE projects SET generation=?2,document_json=?3 WHERE id=?1",
                        params![
                            current.id.to_string(),
                            current.generation.to_string(),
                            serde_json::to_string(&current).unwrap()
                        ],
                    )
                    .unwrap();
            } else {
                store
                    .conn
                    .execute(
                        "UPDATE projects SET revision=1 WHERE id=?1",
                        [current.id.to_string()],
                    )
                    .unwrap();
            }
            let before = snapshot(&store);
            read_only(&store);
            let result = store.recover_project_creation(&request);
            if rotate_generation {
                assert!(matches!(result, Err(StorageError::GenerationConflict)));
            } else {
                assert!(matches!(
                    result,
                    Err(StorageError::InvalidProjectCreationReceipt(_))
                ));
            }
            assert_eq!(snapshot(&store), before);
        }
    }

    #[test]
    fn orphan_creation_receipt_is_corruption_not_a_new_creation_opportunity() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
        let request = intent("Orphan receipt");
        let original = store.create_project_with_intent(&request).unwrap().project;
        // Model an inconsistent external database, not a supported delete.
        store.conn.execute_batch("PRAGMA foreign_keys=OFF").unwrap();
        store
            .conn
            .execute(
                "DELETE FROM projects WHERE id=?1",
                [original.id.to_string()],
            )
            .unwrap();
        assert_eq!(counts(&store), (0, 1, 0));
        let before = snapshot(&store);
        read_only(&store);
        assert!(matches!(
            store.recover_project_creation(&request),
            Err(StorageError::InvalidProjectCreationReceipt(_))
        ));
        assert_eq!(snapshot(&store), before);
    }

    #[test]
    fn creation_and_legacy_change_receipts_keep_distinct_purposes() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
        let request = intent("Typed creation receipt");
        let original = store.create_project_with_intent(&request).unwrap().project;
        let key = receipt_key(request.request_id);
        let rename = Change::RenameProject {
            title: "Unrelated mutation".into(),
        };
        assert!(matches!(
            store.apply(original.id, &original.stamp(), &key, &rename),
            Err(StorageError::RequestReuse)
        ));
        assert!(matches!(
            store.inspect_file_import(
                original.id,
                &original.stamp(),
                &key,
                &FileImportIntent::Asset {
                    name: "file".into(),
                    media_type: "image/png".into()
                },
                false,
            ),
            Err(StorageError::RequestReuse)
        ));
        let legacy_request = intent("Legacy operation using the same key prefix");
        let mut legacy = Project::new("Existing ordinary project").unwrap();
        legacy.id = project_id(legacy_request.request_id);
        store.create_project(&legacy).unwrap();
        let legacy_key = receipt_key(legacy_request.request_id);
        let changed = store
            .apply(legacy.id, &legacy.stamp(), &legacy_key, &rename)
            .unwrap();
        let replay = store
            .apply(legacy.id, &legacy.stamp(), &legacy_key, &rename)
            .unwrap();
        assert_eq!(replay.project, changed.project);
        assert!(replay.replayed);
        assert!(matches!(
            store.recover_project_creation(&legacy_request),
            Err(StorageError::RequestReuse)
        ));
        assert!(matches!(
            store.create_project_with_intent(&legacy_request),
            Err(StorageError::RequestReuse)
        ));
        assert_eq!(counts(&store), (2, 2, 1));
    }

    #[test]
    fn concurrent_different_payloads_for_one_operation_have_one_winner() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut left_store = Store::open(&database).unwrap();
        let mut right_store = Store::open(&database).unwrap();
        let left_request = intent("Left input");
        let right_request = ProjectCreationIntent {
            request_id: left_request.request_id,
            title: "Right input".into(),
        };
        let barrier = Arc::new(Barrier::new(3));
        let left_barrier = barrier.clone();
        let right_barrier = barrier.clone();
        let left = std::thread::spawn(move || {
            left_barrier.wait();
            left_store.create_project_with_intent(&left_request)
        });
        let right = std::thread::spawn(move || {
            right_barrier.wait();
            right_store.create_project_with_intent(&right_request)
        });
        barrier.wait();
        let left = left.join().unwrap();
        let right = right.join().unwrap();
        assert_eq!(usize::from(left.is_ok()) + usize::from(right.is_ok()), 1);
        let winner = match (left, right) {
            (Ok(winner), Err(StorageError::RequestReuse))
            | (Err(StorageError::RequestReuse), Ok(winner)) => winner,
            observed => {
                panic!("one committed result and one payload rejection required: {observed:?}")
            }
        };
        let reopened = Store::open(&database).unwrap();
        assert_eq!(counts(&reopened), (1, 1, 0));
        assert_eq!(
            reopened.load_project(winner.project.id).unwrap(),
            winner.project
        );
    }
}
