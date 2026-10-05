use motionwright_domain::{Change, DomainError, Project, RevisionStamp};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum StorageError {
    #[error("sqlite: {0}")]
    Sql(#[from] rusqlite::Error),
    #[error("serialization: {0}")]
    Serde(#[from] serde_json::Error),
    #[error("domain: {0}")]
    Domain(#[from] DomainError),
    #[error("project not found")]
    NotFound,
    #[error("stale project base: expected revision {expected}, current revision {actual}")]
    Conflict { expected: u64, actual: u64 },
    #[error("request id was reused with a different payload")]
    RequestReuse,
    #[error("resource generation differs from expected base")]
    GenerationConflict,
}

pub type Result<T> = std::result::Result<T, StorageError>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ApplyOutcome {
    pub project: Project,
    pub previous_revision: u64,
    pub request_id: String,
    pub replayed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectEvent {
    pub revision: u64,
    pub change: Change,
    pub created_at: String,
}

pub struct Store {
    conn: Connection,
    path: PathBuf,
}

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|_| rusqlite::Error::InvalidPath(parent.into()))?;
        }
        let conn = Connection::open(&path)?;
        conn.busy_timeout(Duration::from_secs(5))?;
        conn.execute_batch(
            r#"
            PRAGMA foreign_keys=ON;
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              title TEXT NOT NULL,
              document_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS receipts(
              project_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(project_id, request_id),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS events(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, revision),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS events_by_project_revision
              ON events(project_id, revision);
            "#,
        )?;
        Ok(Self { conn, path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn create_project(&mut self, project: &Project) -> Result<()> {
        project.validate()?;
        let json = serde_json::to_string(project)?;
        self.conn.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)
             VALUES(?1,?2,?3,?4,?5,?6)",
            params![
                project.id.to_string(),
                project.generation.to_string(),
                project.revision as i64,
                project.title,
                json,
                project.updated_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn create_named_project(&mut self, title: &str) -> Result<Project> {
        let project = Project::new(title)?;
        self.create_project(&project)?;
        Ok(project)
    }

    pub fn load_project(&self, id: Uuid) -> Result<Project> {
        let json: String = self
            .conn
            .query_row(
                "SELECT document_json FROM projects WHERE id=?1",
                [id.to_string()],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(StorageError::NotFound)?;
        let project: Project = serde_json::from_str(&json)?;
        project.validate()?;
        Ok(project)
    }

    pub fn list_projects(&self, limit: usize) -> Result<Vec<Project>> {
        let limit = limit.clamp(1, 100);
        let mut stmt = self
            .conn
            .prepare("SELECT document_json FROM projects ORDER BY updated_at DESC, id LIMIT ?1")?;
        let rows = stmt.query_map([limit as i64], |row| row.get::<_, String>(0))?;
        let mut projects = Vec::new();
        for row in rows {
            let project: Project = serde_json::from_str(&row?)?;
            project.validate()?;
            projects.push(project);
        }
        Ok(projects)
    }

    pub fn apply(
        &mut self,
        id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        change: &Change,
    ) -> Result<ApplyOutcome> {
        if request_id.is_empty()
            || request_id.len() > 256
            || request_id.chars().any(char::is_control)
        {
            return Err(StorageError::Domain(DomainError::Invalid(
                "invalid request id".into(),
            )));
        }
        let request_sha = request_digest(id, change)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;

        if let Some((stored_sha, stored_result)) = tx
            .query_row(
                "SELECT request_sha256,result_json FROM receipts
                 WHERE project_id=?1 AND request_id=?2",
                params![id.to_string(), request_id],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()?
        {
            if stored_sha != request_sha {
                return Err(StorageError::RequestReuse);
            }
            let mut outcome: ApplyOutcome = serde_json::from_str(&stored_result)?;
            outcome.replayed = true;
            tx.commit()?;
            return Ok(outcome);
        }

        let json: String = tx
            .query_row(
                "SELECT document_json FROM projects WHERE id=?1",
                [id.to_string()],
                |row| row.get(0),
            )
            .optional()?
            .ok_or(StorageError::NotFound)?;
        let mut project: Project = serde_json::from_str(&json)?;
        project.validate()?;

        if expected.resource != project.resource_key() || expected.generation != project.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if expected.revision != project.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }

        let previous_revision = project.revision;
        project.apply_change(change)?;
        project.revision = project.revision.saturating_add(1);
        project.validate()?;
        let project_json = serde_json::to_string(&project)?;
        let changed = tx.execute(
            "UPDATE projects
             SET generation=?2,revision=?3,title=?4,document_json=?5,updated_at=?6
             WHERE id=?1 AND revision=?7",
            params![
                project.id.to_string(),
                project.generation.to_string(),
                project.revision as i64,
                project.title,
                project_json,
                project.updated_at.to_rfc3339(),
                previous_revision as i64,
            ],
        )?;
        if changed != 1 {
            return Err(StorageError::Conflict {
                expected: previous_revision,
                actual: previous_revision.saturating_add(1),
            });
        }

        let payload = serde_json::to_string(change)?;
        tx.execute(
            "INSERT INTO events(project_id,revision,kind,payload_json,created_at)
             VALUES(?1,?2,'change',?3,?4)",
            params![
                project.id.to_string(),
                project.revision as i64,
                payload,
                project.updated_at.to_rfc3339(),
            ],
        )?;

        let outcome = ApplyOutcome {
            project,
            previous_revision,
            request_id: request_id.to_owned(),
            replayed: false,
        };
        let result_json = serde_json::to_string(&outcome)?;
        tx.execute(
            "INSERT INTO receipts(project_id,request_id,request_sha256,result_json,created_at)
             VALUES(?1,?2,?3,?4,?5)",
            params![
                id.to_string(),
                request_id,
                request_sha,
                result_json,
                chrono::Utc::now().to_rfc3339(),
            ],
        )?;

        tx.commit()?;
        Ok(outcome)
    }

    pub fn event_records_since(
        &self,
        id: Uuid,
        revision: u64,
        limit: usize,
    ) -> Result<Vec<ProjectEvent>> {
        let limit = limit.clamp(1, 256);
        let mut stmt = self.conn.prepare(
            "SELECT revision,payload_json,created_at FROM events
             WHERE project_id=?1 AND revision>?2
             ORDER BY revision ASC LIMIT ?3",
        )?;
        let rows = stmt.query_map(
            params![id.to_string(), revision as i64, limit as i64],
            |row| {
                Ok((
                    row.get::<_, i64>(0)? as u64,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            },
        )?;
        let mut events = Vec::new();
        for row in rows {
            let (revision, json, created_at) = row?;
            events.push(ProjectEvent {
                revision,
                change: serde_json::from_str(&json)?,
                created_at,
            });
        }
        Ok(events)
    }

    pub fn events_since(
        &self,
        id: Uuid,
        revision: u64,
        limit: usize,
    ) -> Result<Vec<(u64, Change)>> {
        Ok(self
            .event_records_since(id, revision, limit)?
            .into_iter()
            .map(|event| (event.revision, event.change))
            .collect())
    }
}

fn request_digest(id: Uuid, change: &Change) -> Result<String> {
    let canonical = serde_json::to_vec(&(id, change))?;
    Ok(hex::encode(Sha256::digest(canonical)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::Change;

    #[test]
    fn save_reopen_and_replay_are_exact() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("motionwright.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let project = store.create_named_project("Semwright explainer").unwrap();
        let expected = RevisionStamp::from(&project);
        let first = store
            .apply(
                project.id,
                &expected,
                "request-1",
                &Change::RenameProject {
                    title: "Explainer v2".into(),
                },
            )
            .unwrap();
        assert_eq!(first.project.revision, 1);
        assert!(!first.replayed);

        let replay = store
            .apply(
                project.id,
                &expected,
                "request-1",
                &Change::RenameProject {
                    title: "Explainer v2".into(),
                },
            )
            .unwrap();
        assert!(replay.replayed);
        assert_eq!(replay.project.revision, 1);

        drop(store);
        let reopened = Store::open(&path).unwrap();
        assert_eq!(
            reopened.load_project(project.id).unwrap().title,
            "Explainer v2"
        );
    }

    #[test]
    fn stale_base_and_request_key_reuse_fail_closed() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        let project = store.create_named_project("Demo").unwrap();
        let base = RevisionStamp::from(&project);
        let changed = store
            .apply(
                project.id,
                &base,
                "same-key",
                &Change::RenameProject { title: "A".into() },
            )
            .unwrap();

        let error = store
            .apply(
                project.id,
                &base,
                "different-key",
                &Change::RenameProject { title: "B".into() },
            )
            .unwrap_err();
        assert!(matches!(error, StorageError::Conflict { .. }));

        let error = store
            .apply(
                project.id,
                &RevisionStamp::from(&changed.project),
                "same-key",
                &Change::RenameProject { title: "B".into() },
            )
            .unwrap_err();
        assert!(matches!(error, StorageError::RequestReuse));
    }

    #[test]
    fn journal_replays_only_committed_changes() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        let project = store.create_named_project("Demo").unwrap();
        let first = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "one",
                &Change::AddScene {
                    name: "Opening".into(),
                    objective: "Establish the problem".into(),
                    duration_seconds: 6,
                },
            )
            .unwrap();
        store
            .apply(
                project.id,
                &RevisionStamp::from(&first.project),
                "two",
                &Change::AddScene {
                    name: "Mechanism".into(),
                    objective: "Show semantic control".into(),
                    duration_seconds: 9,
                },
            )
            .unwrap();

        let records = store.event_records_since(project.id, 0, 10).unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].revision, 1);
        assert_eq!(records[1].revision, 2);
        assert!(!records[0].created_at.is_empty());

        let events = store.events_since(project.id, 0, 10).unwrap();
        assert_eq!(events[0].0, 1);
        assert_eq!(events[1].0, 2);
    }
}
