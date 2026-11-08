//! An existing managed Store opened without writer, migration or CAS maintenance authority.
use super::{
    Project, ProjectEvent, Result, STORAGE_SCHEMA_VERSION, StorageError, Store, cas_retention,
    checked_sqlite_storage_path, sqlite_identity, validate_existing_project_store_layout,
};
use rusqlite::{Connection, OpenFlags, ffi};
#[cfg(target_os = "linux")]
use std::fs;
use std::{path::Path, time::Duration};
use uuid::Uuid;

/// A reader deliberately exposes no Store borrow or mutation methods.
/// It retains SQLite's read-only connection and the original managed CAS identity.
pub struct StoreReader {
    store: Store,
    database_identity: String,
    root: cas_retention::ManagedRoot,
    catalog_original_stamp: String,
}

fn invalid(message: &str) -> StorageError {
    StorageError::InvalidBackup(format!("read-only Store: {message}"))
}

fn path_identity(path: &Path) -> Result<String> {
    checked_sqlite_storage_path(path)?;
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::symlink_metadata(path)?;
        if !metadata.is_file() {
            return Err(invalid("database is not a regular file"));
        }
        Ok(format!(
            "linux:{}:{}:{}",
            rustix::fs::major(metadata.dev()),
            rustix::fs::minor(metadata.dev()),
            metadata.ino()
        ))
    }
    #[cfg(not(target_os = "linux"))]
    {
        cas_retention::database_identity(path)
    }
}

// Read the header through SQLite's retained public VFS handle. Opening a
// second descriptor for this database could release this process's POSIX locks.
fn require_read_only_handoff(connection: &Connection) -> Result<()> {
    struct Lock(*mut ffi::sqlite3_mutex);
    impl Drop for Lock {
        fn drop(&mut self) {
            // SAFETY: releases exactly the acquisition below; NULL is supported.
            unsafe { ffi::sqlite3_mutex_leave(self.0) };
        }
    }
    // SAFETY: the private-cache connection remains borrowed and live throughout.
    let mutex = unsafe { ffi::sqlite3_db_mutex(connection.handle()) };
    // SAFETY: SQLite accepts the connection's recursive mutex, including NULL.
    unsafe { ffi::sqlite3_mutex_enter(mutex) };
    let _lock = Lock(mutex);
    let mut file = std::ptr::null_mut::<ffi::sqlite3_file>();
    // SAFETY: FILE_POINTER writes one public sqlite3_file pointer into a correctly
    // aligned output. The connection and mutex remain held until after xRead.
    let status = unsafe {
        ffi::sqlite3_file_control(
            connection.handle(),
            c"main".as_ptr(),
            ffi::SQLITE_FCNTL_FILE_POINTER,
            std::ptr::from_mut(&mut file).cast(),
        )
    };
    if status != ffi::SQLITE_OK || file.is_null() {
        return Err(invalid("retained database header is unavailable"));
    }
    // SAFETY: pMethods is part of the public VFS ABI, not a private OS layout.
    let methods = unsafe { std::ptr::addr_of!((*file).pMethods).read() };
    if methods.is_null() {
        return Err(invalid("retained database VFS is unavailable"));
    }
    // SAFETY: iVersion is the first field in every public VFS methods version.
    if unsafe { std::ptr::addr_of!((*methods).iVersion).read() } < 1 {
        return Err(invalid("retained database VFS version is unavailable"));
    }
    // SAFETY: xRead exists in version 1 and later.
    let read = unsafe { std::ptr::addr_of!((*methods).xRead).read() }
        .ok_or_else(|| invalid("retained database read method is unavailable"))?;
    let mut header = [0u8; 20];
    // SAFETY: the live VFS reads at most 20 bytes into the supplied buffer.
    let status = unsafe { read(file, header.as_mut_ptr().cast(), 20, 0) };
    if status != ffi::SQLITE_OK
        || &header[..16] != b"SQLite format 3\0"
        || header[18] != 1
        || header[19] != 1
    {
        return Err(invalid(
            "writer must complete a read-only handoff before opening the reader",
        ));
    }
    Ok(())
}

impl Store {
    /// Explicitly finish an authorized writer's WAL state for a read-only handoff.
    /// SQLite performs its own checkpoint and journal-mode transition on this
    /// same connection/file. No copied database, permission change or lock bypass
    /// is used. A subsequent normal writer open restores the usual WAL policy.
    pub fn prepare_read_only_handoff(&mut self) -> Result<()> {
        let identity = self.database_file_identity()?;
        if path_identity(&self.path)? != identity {
            return Err(invalid("writer no longer owns the original database file"));
        }
        let root = cas_retention::discover(&self.blob_root)?
            .ok_or_else(|| invalid("writer CAS root is not managed"))?;
        // Validate the existing managed catalog *before* checkpointing or
        // switching the original SQLite file out of WAL. A failed CAS
        // preflight must not leave a nominally failed handoff in DELETE mode.
        root.validate_location_read_only()?;
        let busy: i64 = self
            .conn
            .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| row.get(0))?;
        if busy != 0 {
            return Err(invalid("writer checkpoint is busy; no read-only handoff"));
        }
        let mode: String = self
            .conn
            .query_row("PRAGMA journal_mode=DELETE", [], |row| row.get(0))?;
        if mode != "delete"
            || self.database_file_identity()? != identity
            || path_identity(&self.path)? != identity
            || cas_retention::load_store_id(&self.conn)? != self.cas_store_id
        {
            return Err(invalid("writer read-only handoff was not completed"));
        }
        root.validate_location_read_only()?;
        require_read_only_handoff(&self.conn)
    }
}

impl StoreReader {
    /// Open only an existing current-schema database after its writer explicitly
    /// completed the read-only handoff, retaining the original managed CAS.
    /// Missing roots, old schemas and unsupported retained-file identity fail closed.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        let database_identity = path_identity(&path)?;
        let blob_root = path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("blobs");
        let root = cas_retention::discover_read_only(&blob_root)?
            .ok_or_else(|| invalid("CAS root is not managed"))?;
        let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
        let conn = Connection::open_with_flags(&path, flags)?;
        require_read_only_handoff(&conn)?;
        conn.busy_timeout(Duration::from_secs(5))?;
        conn.execute_batch("PRAGMA query_only=ON; PRAGMA foreign_keys=ON;")?;
        if sqlite_identity::connection_identity(&conn)? != database_identity {
            return Err(invalid("opened database differs from the original file"));
        }
        // Retain one read snapshot through all calls. No writer fence, migration,
        // cleanup or mutable Store borrow is available from this reader.
        conn.execute_batch("BEGIN")?;
        let version: u32 = conn.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if version != STORAGE_SCHEMA_VERSION {
            return Err(invalid(
                "reader requires the current schema; migration needs a writer",
            ));
        }
        validate_existing_project_store_layout(&conn, version)?;
        let cas_store_id = cas_retention::load_store_id(&conn)?;
        let catalog_original_stamp = root.catalog_original_stamp_read_only()?;
        let reader = Self {
            store: Store {
                conn,
                path: path.clone(),
                blob_root,
                cas_store_id,
                cas_database_id: cas_retention::database_identity(&path)?,
            },
            database_identity,
            root,
            catalog_original_stamp,
        };
        reader.validate()?;
        Ok(reader)
    }

    fn validate(&self) -> Result<()> {
        require_read_only_handoff(&self.store.conn)?;
        if self.store.database_file_identity()? != self.database_identity
            || path_identity(&self.store.path)? != self.database_identity
            || cas_retention::load_store_id(&self.store.conn)? != self.store.cas_store_id
        {
            return Err(invalid("original database identity changed"));
        }
        if self.root.catalog_original_stamp_read_only()? != self.catalog_original_stamp {
            return Err(invalid("original managed CAS catalog changed"));
        }
        Ok(())
    }

    fn observe<T>(&self, read: impl FnOnce(&Store) -> Result<T>) -> Result<T> {
        self.validate()?;
        let result = read(&self.store)?;
        self.validate()?;
        Ok(result)
    }

    pub fn database_file_identity(&self) -> Result<String> {
        self.observe(|store| store.database_file_identity())
    }

    pub fn project(&self, id: Uuid) -> Result<Project> {
        self.observe(|store| store.load_project(id))
    }

    pub fn projects(&self, limit: usize) -> Result<Vec<Project>> {
        self.observe(|store| store.list_projects(limit))
    }

    pub fn project_history(
        &self,
        id: Uuid,
        after_revision: u64,
        limit: usize,
    ) -> Result<Vec<ProjectEvent>> {
        self.observe(|store| store.event_records_since(id, after_revision, limit))
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use std::{collections::BTreeMap, os::unix::fs::MetadataExt, path::PathBuf};

    fn inventory(root: &Path) -> BTreeMap<PathBuf, (u64, u64, u32, Vec<u8>)> {
        fn visit(root: &Path, path: &Path, out: &mut BTreeMap<PathBuf, (u64, u64, u32, Vec<u8>)>) {
            let meta = fs::symlink_metadata(path).unwrap();
            let bytes = if meta.is_file() {
                fs::read(path).unwrap()
            } else {
                Vec::new()
            };
            out.insert(
                path.strip_prefix(root).unwrap().to_owned(),
                (meta.dev(), meta.ino(), meta.mode(), bytes),
            );
            if meta.is_dir() {
                for entry in fs::read_dir(path).unwrap() {
                    visit(root, &entry.unwrap().path(), out);
                }
            }
        }
        let mut result = BTreeMap::new();
        visit(root, root, &mut result);
        result
    }

    #[test]
    fn reader_requires_explicit_handoff_without_creating_wal_sidecars() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        drop(Store::open(&database).unwrap());
        let before = inventory(directory.path());
        assert!(StoreReader::open(&database).is_err());
        assert_eq!(inventory(directory.path()), before);
    }

    /// An idle second SQLite connection can keep WAL open even when a
    /// checkpoint reports no busy readers. The original writer must fail
    /// closed rather than manufacture a read-only handoff receipt; after the
    /// peer truly closes, the exact original Store can be handed off once.
    #[test]
    fn handoff_requires_competing_sqlite_connection_to_close() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer
            .create_named_project("Original shared Store")
            .unwrap();
        let original_identity = writer.database_file_identity().unwrap();
        writer
            .conn
            .busy_timeout(Duration::from_millis(150))
            .unwrap();
        let peer = Connection::open_with_flags(
            &database,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW,
        )
        .unwrap();
        let peer_journal: String = peer
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(peer_journal, "wal");

        // The peer has no active transaction, but still owns a SQLite handle.
        // A successful checkpoint is not sufficient proof of exclusive
        // authorization to change the original database journal mode.
        assert!(writer.prepare_read_only_handoff().is_err());
        let still_wal: String = writer
            .conn
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(still_wal, "wal");
        assert_eq!(writer.database_file_identity().unwrap(), original_identity);
        assert_eq!(writer.load_project(project.id).unwrap(), project);
        assert!(StoreReader::open(&database).is_err());

        drop(peer);
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let before_reader = inventory(directory.path());
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.database_file_identity().unwrap(), original_identity);
        assert_eq!(reader.project(project.id).unwrap(), project);
        drop(reader);
        assert_eq!(inventory(directory.path()), before_reader);
    }

    /// The Native provider and StudioService both use full Store handles.
    /// A second live Store with the same real database must be released
    /// before the final writer can prepare its strict read-only handoff.
    #[test]
    fn handoff_requires_second_full_store_to_release_original_database() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer.create_named_project("Shared Native source").unwrap();
        let identity = writer.database_file_identity().unwrap();
        writer
            .conn
            .busy_timeout(Duration::from_millis(150))
            .unwrap();

        let native_peer = Store::open(&database).unwrap();
        assert_eq!(native_peer.database_file_identity().unwrap(), identity);
        assert_eq!(native_peer.load_project(project.id).unwrap(), project);

        // Closing request admissions is not equivalent to closing a retained
        // Native Store. Even an idle full Store keeps the WAL journal open.
        assert!(writer.prepare_read_only_handoff().is_err());
        let mode: String = writer
            .conn
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(mode, "wal");
        assert_eq!(writer.database_file_identity().unwrap(), identity);
        assert_eq!(writer.load_project(project.id).unwrap(), project);

        drop(native_peer);
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let before = inventory(directory.path());
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.database_file_identity().unwrap(), identity);
        assert_eq!(reader.project(project.id).unwrap(), project);
        drop(reader);
        assert_eq!(inventory(directory.path()), before);
    }

    #[test]
    fn reader_uses_original_connection_and_sqlite_rejects_writes() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer
            .create_named_project("Original reader project")
            .unwrap();
        let identity = writer.database_file_identity().unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let original_inventory = inventory(directory.path());
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.database_file_identity().unwrap(), identity);
        assert_eq!(reader.project(project.id).unwrap(), project);
        let delete_error = reader
            .store
            .conn
            .execute("DELETE FROM projects", [])
            .unwrap_err();
        eprintln!("Project deletion refused: {delete_error}");
        // A direct header write has no project trigger or writer-fence function;
        // require SQLite itself to refuse it, not merely fail SQL preparation.
        let error = reader
            .store
            .conn
            .execute("PRAGMA user_version=99", [])
            .unwrap_err();
        assert!(
            matches!(&error, rusqlite::Error::SqliteFailure(code, _)
            if code.code == rusqlite::ErrorCode::ReadOnly),
            "{error}"
        );
        assert_eq!(reader.project(project.id).unwrap(), project);
        assert!(
            reader
                .project_history(project.id, 0, 100)
                .unwrap()
                .is_empty()
        );
        drop(reader);
        let reopened = StoreReader::open(&database).unwrap();
        assert_eq!(reopened.database_file_identity().unwrap(), identity);
        assert_eq!(reopened.project(project.id).unwrap(), project);
        // Hash files only after all SQLite handles in this process have closed.
        drop(reopened);
        assert_eq!(inventory(directory.path()), original_inventory);
        // The regular writer retains its normal WAL policy after the handoff.
        let writer = Store::open(&database).unwrap();
        let mode: String = writer
            .conn
            .pragma_query_value(None, "journal_mode", |r| r.get(0))
            .unwrap();
        assert_eq!(mode, "wal");
        assert_eq!(writer.load_project(project.id).unwrap(), project);
    }

    /// The independent Native/Studio reader must refuse physical history
    /// corruption even when a paginated cursor would hide the bad row.
    /// The full writer's history guard must remain active through the strict
    /// read-only API on the very same SQLite/CAS source.
    #[test]
    fn reader_rejects_negative_history_hidden_by_page_and_cursor() {
        use crate::{Change, RevisionStamp};

        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let mut project = writer
            .create_named_project("Original graph source")
            .unwrap();
        let unrelated = writer
            .create_named_project("Independent healthy source")
            .unwrap();

        for n in 1..=2 {
            let outcome = writer
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("graph-event-{n}"),
                    &Change::RenameProject {
                        title: format!("Graph source {n}"),
                    },
                )
                .unwrap();
            project = outcome.project;
        }
        let unrelated_outcome = writer
            .apply(
                unrelated.id,
                &RevisionStamp::from(&unrelated),
                "unrelated-healthy-event",
                &Change::RenameProject {
                    title: "Still healthy".into(),
                },
            )
            .unwrap();
        assert_eq!(unrelated_outcome.project.revision, 1);

        // A CHECK constraint does not forbid a negative revision here;
        // it is below the cursor and below the newest-first page limit.
        assert_eq!(
            writer
                .conn
                .execute(
                    "UPDATE events SET revision=-1 WHERE project_id=?1 AND revision=1",
                    [project.id.to_string()],
                )
                .unwrap(),
            1
        );
        let original_identity = writer.database_file_identity().unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);

        let original_inventory = inventory(directory.path());
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.database_file_identity().unwrap(), original_identity);
        assert_eq!(reader.project(project.id).unwrap(), project);
        for cursor in [0, 1, 2, u64::MAX] {
            for limit in [1, 100] {
                assert!(
                    matches!(
                        reader.project_history(project.id, cursor, limit),
                        Err(StorageError::InvalidBackup(ref message))
                            if message.contains("negative event revision")
                    ),
                    "strict independent reader must refuse hidden original event, cursor={cursor}, limit={limit}"
                );
            }
        }
        assert_eq!(
            reader.project_history(unrelated.id, 0, 1).unwrap().len(),
            1,
            "corrupt Graph source must not revoke an independent healthy project"
        );
        drop(reader);
        assert_eq!(inventory(directory.path()), original_inventory);
    }

    /// The retained Native/Studio consumer must reject an earlier original
    /// SQLite event with a forged kind even when its cursor only sees later,
    /// valid revisions. This is independent of Store's writer-side oracle.
    #[test]
    fn reader_rejects_hidden_non_change_kind_on_original_sqlite() {
        use crate::{Change, RevisionStamp};

        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let mut project = writer.create_named_project("Original kind source").unwrap();
        let unrelated = writer
            .create_named_project("Unrelated kind source")
            .unwrap();

        for revision in 1..=2 {
            let outcome = writer
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("original-kind-{revision}"),
                    &Change::RenameProject {
                        title: format!("Original revision {revision}"),
                    },
                )
                .unwrap();
            project = outcome.project;
        }
        let healthy = writer
            .apply(
                unrelated.id,
                &RevisionStamp::from(&unrelated),
                "healthy-kind-event",
                &Change::RenameProject {
                    title: "Independent original".into(),
                },
            )
            .unwrap();
        assert_eq!(healthy.project.revision, 1);
        // The physical SQLite event type lacks a CHECK(kind='change').
        // Retain a valid revision 2 and corrupt only an older original row.
        assert_eq!(
            writer
                .conn
                .execute(
                    "UPDATE events SET kind='forged-non-change' WHERE project_id=?1 AND revision=1",
                    [project.id.to_string()],
                )
                .unwrap(),
            1
        );
        let identity = writer.database_file_identity().unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let before = inventory(directory.path());

        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.database_file_identity().unwrap(), identity);
        assert_eq!(reader.project(project.id).unwrap(), project);
        // Start with a cursor that hides the forged row. On the unfixed
        // indexed Store implementation this falsely returned revision 2.
        for cursor in [1, 2, u64::MAX, 0] {
            for limit in [1, 100] {
                let result = reader.project_history(project.id, cursor, limit);
                assert!(
                    matches!(
                        result,
                        Err(StorageError::InvalidBackup(ref message))
                            if message.contains("event kind")
                    ),
                    "strict retained reader must reject hidden non-change kind, cursor={cursor}, limit={limit}: {result:?}"
                );
            }
        }
        assert_eq!(
            reader.project_history(unrelated.id, 0, 1).unwrap().len(),
            1,
            "corrupt graph row must not deny an independent healthy project"
        );
        drop(reader);
        assert_eq!(inventory(directory.path()), before);
    }

    #[test]
    fn reader_does_not_create_missing_database_or_cas() {
        let directory = tempfile::tempdir().unwrap();
        let missing_parent = directory.path().join("missing");
        assert!(StoreReader::open(missing_parent.join("state.sqlite3")).is_err());
        assert!(!missing_parent.exists());
        let database = directory.path().join("state.sqlite3");
        drop(Store::open(&database).unwrap());
        fs::rename(
            directory.path().join("blobs"),
            directory.path().join("retained"),
        )
        .unwrap();
        assert!(StoreReader::open(&database).is_err());
        assert!(!directory.path().join("blobs").exists());
    }

    #[test]
    fn reader_rejects_byte_identical_database_substitution() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer.create_named_project("Retained inode").unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let original_bytes = fs::read(&database).unwrap();
        let reader = StoreReader::open(&database).unwrap();
        let original = directory.path().join("original.sqlite3");
        fs::rename(&database, &original).unwrap();
        fs::write(&database, original_bytes).unwrap();
        assert!(reader.project(project.id).is_err());
        assert!(reader.database_file_identity().is_err());
    }

    /// A retained managed CAS root is not trustworthy if its catalog is
    /// corrupted, even when the SQLite Project rows and root anchor survive.
    #[test]
    fn reader_rejects_corrupted_managed_cas_catalog_at_open_and_observe() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer.create_named_project("Catalog integrity").unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let control = cas_retention::test_metadata_root(&directory.path().join("blobs")).unwrap();
        let catalog_path = control.join("catalog.json");
        let original_catalog = fs::read(&catalog_path).unwrap();
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.project(project.id).unwrap(), project);

        // A well-formed Catalog can still have a forged checksum. Parsing
        // its envelope alone must not grant original StoreReader authority.
        let mut changed_checksum: serde_json::Value =
            serde_json::from_slice(&original_catalog).unwrap();
        changed_checksum["sha256"] = serde_json::Value::String("0".repeat(64));
        fs::write(
            &catalog_path,
            serde_json::to_vec(&changed_checksum).unwrap(),
        )
        .unwrap();
        assert!(StoreReader::open(&database).is_err());
        assert!(reader.project(project.id).is_err());

        // Also reject an envelope with a structurally corrupt catalog.
        fs::write(&catalog_path, br#"{"sha256":"forged","catalog":{}}"#).unwrap();
        assert!(StoreReader::open(&database).is_err());
        assert!(reader.project(project.id).is_err());
        fs::write(&catalog_path, original_catalog).unwrap();
        assert_eq!(reader.project(project.id).unwrap(), project);
    }

    /// A valid checksum alone is not the same original CAS catalog. A
    /// strict reader that retained the original SQLite snapshot must not
    /// silently accept a changed, self-consistently rechecksummed control.
    #[test]
    fn reader_rejects_rechecksummed_catalog_drift_after_open() {
        use sha2::Digest;

        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer
            .create_named_project("Original catalog snapshot")
            .unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let control = cas_retention::test_metadata_root(&directory.path().join("blobs")).unwrap();
        let catalog_path = control.join("catalog.json");
        let original_bytes = fs::read(&catalog_path).unwrap();
        let original_envelope: serde_json::Value = serde_json::from_slice(&original_bytes).unwrap();
        let original_sha = original_envelope["sha256"].as_str().unwrap();
        let original_text = String::from_utf8(original_bytes.clone()).unwrap();
        let original_payload = original_text
            .split_once("\"catalog\":")
            .unwrap()
            .1
            .strip_suffix('}')
            .unwrap();
        assert_eq!(
            hex::encode(sha2::Sha256::digest(original_payload.as_bytes())),
            original_sha,
            "fixture must preserve real canonical Catalog checksum"
        );

        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.project(project.id).unwrap(), project);
        let original_pins = "\"legacy_pins\":[]";
        assert!(original_text.contains(original_pins));
        let replacement = format!("\"legacy_pins\":[\"{}\"]", "a".repeat(64));
        let forged = original_text.replacen(original_pins, &replacement, 1);
        let payload = forged
            .split_once("\"catalog\":")
            .unwrap()
            .1
            .strip_suffix('}')
            .unwrap();
        let new_sha = hex::encode(sha2::Sha256::digest(payload.as_bytes()));
        assert_ne!(new_sha, original_sha);
        let authentic_format_forgery = forged.replacen(original_sha, &new_sha, 1);
        let original_file = fs::metadata(&catalog_path).unwrap();
        fs::write(&catalog_path, authentic_format_forgery).unwrap();
        assert_eq!(
            fs::metadata(&catalog_path).unwrap().ino(),
            original_file.ino()
        );
        assert!(
            reader.project(project.id).is_err(),
            "same-inode rechecksummed catalog drift must not be accepted as original Store"
        );
        fs::write(&catalog_path, &original_bytes).unwrap();
        assert_eq!(reader.project(project.id).unwrap(), project);

        // A byte-identical catalog moved to another inode is not the
        // physically original control acquired by the retained reader.
        let new_control = control.join("catalog-replacement.original.json");
        fs::write(&new_control, &original_bytes).unwrap();
        fs::rename(&new_control, &catalog_path).unwrap();
        assert_ne!(
            fs::metadata(&catalog_path).unwrap().ino(),
            original_file.ino()
        );
        assert!(
            reader.project(project.id).is_err(),
            "replacing a catalog with identical bytes must revoke original reader authority"
        );
    }

    /// Invalid CAS authority must be rejected before a live writer changes
    /// WAL journal mode or checkpoints the original SQLite file.
    #[test]
    fn corrupt_managed_catalog_blocks_writer_before_wal_handoff() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer
            .create_named_project("CAS handoff preflight")
            .unwrap();
        let control = cas_retention::test_metadata_root(&directory.path().join("blobs")).unwrap();
        let catalog_path = control.join("catalog.json");
        let original_catalog = fs::read(&catalog_path).unwrap();
        let original_mode: String = writer
            .conn
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(original_mode, "wal");

        // Retain a syntactically valid catalog with a forged checksum: the
        // reader's source of authority is corrupt, not the SQLite Project.
        let mut forged: serde_json::Value = serde_json::from_slice(&original_catalog).unwrap();
        forged["sha256"] = serde_json::Value::String("0".repeat(64));
        fs::write(&catalog_path, serde_json::to_vec(&forged).unwrap()).unwrap();
        assert!(writer.prepare_read_only_handoff().is_err());
        let after_failure: String = writer
            .conn
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(
            after_failure, "wal",
            "rejected CAS handoff changed the live writer journal mode"
        );
        assert_eq!(writer.load_project(project.id).unwrap(), project);

        fs::write(&catalog_path, original_catalog).unwrap();
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(reader.project(project.id).unwrap(), project);
    }

    #[test]
    fn reader_rejects_older_schema_without_migration() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        writer.prepare_read_only_handoff().unwrap();
        writer
            .conn
            .pragma_update(None, "user_version", STORAGE_SCHEMA_VERSION - 1)
            .unwrap();
        drop(writer);
        assert!(StoreReader::open(&database).is_err());
        let conn =
            Connection::open_with_flags(&database, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        let version: u32 = conn
            .pragma_query_value(None, "user_version", |row| row.get(0))
            .unwrap();
        assert_eq!(version, STORAGE_SCHEMA_VERSION - 1);
    }
}

#[cfg(all(test, windows))]
mod windows_original_handoff_tests {
    use super::*;
    use crate::{Change, RevisionStamp};
    use std::fs;

    /// Windows has a different VFS and file-identity API from Linux. Verify
    /// that Native/Studio can hand off the *same* physical SQLite and CAS
    /// authority to the strictly read-only service on this platform too.
    #[test]
    fn windows_original_read_only_handoff_preserves_sqlite_and_cas() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite3");
        let mut writer = Store::open(&database).unwrap();
        let project = writer
            .create_named_project("Windows original Graph")
            .unwrap();
        let outcome = writer
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "windows-original-change",
                &Change::RenameProject {
                    title: "Windows original updated".into(),
                },
            )
            .unwrap();
        let expected = outcome.project;
        let original_identity = writer.database_file_identity().unwrap();

        // A writer still in ordinary WAL mode may not be silently treated
        // as a closed read-only service, even with an idle connection.
        assert!(
            StoreReader::open(&database).is_err(),
            "reader must refuse the original writer before explicit handoff"
        );
        writer.prepare_read_only_handoff().unwrap();
        drop(writer);

        let before_database = fs::read(&database).unwrap();
        let catalog_path = cas_retention::test_metadata_root(&directory.path().join("blobs"))
            .unwrap()
            .join("catalog.json");
        let before_catalog = fs::read(&catalog_path).unwrap();
        let reader = StoreReader::open(&database).unwrap();
        assert_eq!(
            reader.database_file_identity().unwrap(),
            original_identity,
            "the second service must hold the original SQLite file"
        );
        assert_eq!(reader.project(expected.id).unwrap(), expected);
        let history = reader.project_history(expected.id, 0, 100).unwrap();
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].revision, expected.revision);

        let rejected = reader
            .store
            .conn
            .execute("PRAGMA user_version=91", [])
            .unwrap_err();
        assert!(
            matches!(&rejected, rusqlite::Error::SqliteFailure(code, _)
                if code.code == rusqlite::ErrorCode::ReadOnly),
            "the actual SQLite VFS must reject writes: {rejected}"
        );
        drop(reader);
        assert_eq!(fs::read(&database).unwrap(), before_database);
        assert_eq!(fs::read(&catalog_path).unwrap(), before_catalog);

        // Returning to normal production restores the original writer's
        // WAL policy and preserves the same project, rather than a copy.
        let resumed = Store::open(&database).unwrap();
        assert_eq!(resumed.database_file_identity().unwrap(), original_identity);
        assert_eq!(resumed.load_project(expected.id).unwrap(), expected);
        let mode: String = resumed
            .conn
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(mode, "wal");
    }
}
