use motionwright_domain::{Change, DomainError, Project, RevisionStamp};
use rusqlite::{Connection, OpenFlags, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::Duration,
};
use thiserror::Error;
use uuid::Uuid;

#[cfg(test)]
mod apply_crash_tests;
mod apply_receipt_binding;
mod capture_evidence;
mod derived_cache;
mod file_import;
mod identity_evidence;
mod integrity;
mod portability;
mod production;
mod project_creation;
mod project_projection;
mod render_window_cache;
pub use apply_receipt_binding::{
    APPLY_REQUEST_ENCODING_V1, ApplyReceiptInvalid, ApplyReceiptObservation,
    MAX_APPLY_BINDING_BYTES, VerifiedApplyReceiptV1,
};
pub use derived_cache::{DERIVED_CACHE_FORMAT_VERSION, DerivedCacheHit, DerivedCacheRecord};
pub use file_import::{FileImportIntent, RecordedFileImport, StagedFileImport};
pub use integrity::{
    AssetIntegrityPage, AssetIntegrityRecord, AssetIntegrityStatus, MAX_ASSET_INTEGRITY_PAGE,
};
pub use portability::{BundleFingerprint, InspectedProjectBundle};
pub use production::{ProductionReceipt, ProductionReceiptInput, ProductionReceiptWatermark};
pub use project_creation::{ProjectCreationIntent, ProjectCreationOutcome};
pub use project_projection::{
    MAX_NODE_PAGE_ITEMS, MAX_PROJECTION_RESPONSE_BYTES, MAX_SCENE_PAGE_ITEMS, NodePage,
    NodePageCursor, ProjectionError, ProjectionHead, ProjectionIdentity, ProjectionResult,
    ScenePage, ScenePageCursor, SceneRef, SceneSummary,
};
pub use render_window_cache::{
    RenderWindowCacheKey, RenderWindowEvictionDrainReport, RenderWindowPurgeReport,
};

#[derive(Debug, Error)]
pub enum StorageError {
    #[error("sqlite: {0}")]
    Sql(#[from] rusqlite::Error),
    #[error("serialization: {0}")]
    Serde(#[from] serde_json::Error),
    #[error("filesystem: {0}")]
    Io(#[from] std::io::Error),
    #[error("domain: {0}")]
    Domain(#[from] DomainError),
    #[error("project not found")]
    NotFound,
    #[error("stale project base: expected revision {expected}, current revision {actual}")]
    Conflict { expected: u64, actual: u64 },
    #[error("request id was reused with a different payload")]
    RequestReuse,
    #[error("invalid apply receipt: {0}")]
    InvalidApplyReceipt(String),
    #[error("invalid file import receipt: {0}")]
    InvalidFileImportReceipt(String),
    #[error("invalid project creation receipt: {0}")]
    InvalidProjectCreationReceipt(String),
    #[error("production receipt stream changed during enumeration")]
    ReceiptStreamChanged,
    #[error("resource generation differs from expected base")]
    GenerationConflict,
    #[error("storage schema {found} is newer than supported schema {supported}")]
    UnsupportedSchemaVersion { found: u32, supported: u32 },
    #[error("project already exists")]
    ProjectExists,
    #[error("invalid project backup: {0}")]
    InvalidBackup(String),
    #[error("invalid blob digest")]
    InvalidBlobDigest,
    #[error("blob {digest} is missing")]
    BlobMissing { digest: String },
    #[error("blob exceeds read budget: {size} bytes > {limit} bytes")]
    BlobTooLarge { size: u64, limit: u64 },
    #[error("invalid derived cache entry: {0}")]
    InvalidDerivedCache(String),
    #[error("export destination already exists")]
    DestinationExists,
    #[error("unsafe source path: {0}")]
    UnsafeSourcePath(String),
}

pub type Result<T> = std::result::Result<T, StorageError>;

pub const STORAGE_SCHEMA_VERSION: u32 = 4;
pub const PROJECT_BACKUP_FORMAT_VERSION: u32 = 1;
pub const PROJECT_BUNDLE_FORMAT_VERSION: u32 = 1;
const MAX_BUNDLE_MANIFEST_BYTES: u64 = 16 * 1024 * 1024;
const MAX_BUNDLE_BLOBS: usize = 4096;
const MAX_BUNDLE_TOTAL_BYTES: u64 = 256 * 1024 * 1024 * 1024;

// One finite byte-pattern policy is shared by metadata and streamed members.
// This recognizes the existing credential shapes; it is not a proof that
// arbitrary, encoded or encrypted credentials are absent.
const PRIVATE_KEY_MARKER: &[u8] = concat!("-----BEGIN ", "PRIVATE KEY-----").as_bytes();
const CREDENTIAL_PREFIXES: [(&[u8], usize); 10] = [
    (b"ghp_", 20),
    (b"gho_", 20),
    (b"ghu_", 20),
    (b"ghs_", 20),
    (b"xoxb-", 20),
    (b"xoxa-", 20),
    (b"xoxp-", 20),
    (b"xoxr-", 20),
    (b"xoxs-", 20),
    (b"sk-", 24),
];
const ACCESS_KEY_WIDTH: usize = 20;

const fn credential_pattern_width() -> usize {
    let mut width = PRIVATE_KEY_MARKER.len();
    let mut index = 0;
    while index < CREDENTIAL_PREFIXES.len() {
        let (prefix, suffix) = CREDENTIAL_PREFIXES[index];
        let candidate = prefix.len() + suffix;
        if candidate > width {
            width = candidate;
        }
        index += 1;
    }
    if ACCESS_KEY_WIDTH > width {
        width = ACCESS_KEY_WIDTH;
    }
    width
}

const CREDENTIAL_SCAN_OVERLAP: usize = credential_pattern_width() - 1;

fn secret_token_char(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-')
}

fn has_prefixed_secret(bytes: &[u8], prefix: &[u8], min_suffix: usize) -> bool {
    bytes
        .windows(prefix.len())
        .enumerate()
        .any(|(index, window)| {
            if window != prefix {
                return false;
            }
            let suffix = &bytes[index + prefix.len()..];
            suffix.len() >= min_suffix
                && suffix[..min_suffix].iter().copied().all(secret_token_char)
        })
}

fn contains_credential_like_material(bytes: &[u8]) -> bool {
    if bytes
        .windows(PRIVATE_KEY_MARKER.len())
        .any(|window| window == PRIVATE_KEY_MARKER)
    {
        return true;
    }
    for (prefix, min_suffix) in CREDENTIAL_PREFIXES {
        if has_prefixed_secret(bytes, prefix, min_suffix) {
            return true;
        }
    }
    bytes.windows(ACCESS_KEY_WIDTH).any(|window| {
        window.starts_with(b"AKIA")
            && window[4..]
                .iter()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
    })
}

fn ensure_secret_free_bytes(bytes: &[u8], context: &str) -> Result<()> {
    if contains_credential_like_material(bytes) {
        return Err(StorageError::InvalidBackup(format!(
            "{context} contains credential-like material and cannot enter project storage or export"
        )));
    }
    Ok(())
}

/// Detect the same finite byte patterns across arbitrary short reads.
/// Only a bounded suffix survives a call. No media decoder, MIME assertion,
/// text rewriting or complete-member allocation participates in the check.
#[derive(Default)]
struct CredentialScan {
    tail: [u8; CREDENTIAL_SCAN_OVERLAP],
    tail_len: usize,
}

impl CredentialScan {
    fn check(&mut self, bytes: &[u8], context: &str) -> Result<()> {
        let head_len = bytes.len().min(CREDENTIAL_SCAN_OVERLAP);
        let mut crossing = [0_u8; CREDENTIAL_SCAN_OVERLAP * 2];
        crossing[..self.tail_len].copy_from_slice(&self.tail[..self.tail_len]);
        crossing[self.tail_len..self.tail_len + head_len].copy_from_slice(&bytes[..head_len]);
        ensure_secret_free_bytes(&crossing[..self.tail_len + head_len], context)?;
        ensure_secret_free_bytes(bytes, context)?;

        if bytes.len() >= CREDENTIAL_SCAN_OVERLAP {
            self.tail
                .copy_from_slice(&bytes[bytes.len() - CREDENTIAL_SCAN_OVERLAP..]);
            self.tail_len = CREDENTIAL_SCAN_OVERLAP;
        } else {
            let keep = self.tail_len.min(CREDENTIAL_SCAN_OVERLAP - bytes.len());
            self.tail
                .copy_within(self.tail_len - keep..self.tail_len, 0);
            self.tail[keep..keep + bytes.len()].copy_from_slice(bytes);
            self.tail_len = keep + bytes.len();
        }
        Ok(())
    }
}

// Pattern policy is shared by raw backups and bundles. Only the bundle
// format supplies a metadata byte budget; raw JSON backups retain their
// pre-existing size contract.
fn ensure_backup_secret_free(backup: &ProjectBackup, max_bytes: Option<u64>) -> Result<()> {
    let encoded = serde_json::to_vec(backup)?;
    if let Some(limit) = max_bytes
        && encoded.len() as u64 > limit
    {
        return Err(StorageError::BlobTooLarge {
            size: encoded.len() as u64,
            limit,
        });
    }
    ensure_secret_free_bytes(&encoded, "portable project metadata")
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProjectSummary {
    pub id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub title: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProjectCursor {
    pub updated_at: String,
    pub id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ProjectPage {
    pub items: Vec<ProjectSummary>,
    pub next_cursor: Option<ProjectCursor>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectBackup {
    pub format_version: u32,
    pub source_storage_schema: u32,
    pub exported_at: String,
    pub project: Project,
    pub events: Vec<ProjectEvent>,
    pub payload_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ImportPlan {
    pub project_id: Uuid,
    pub source_generation: Uuid,
    pub revision: u64,
    pub event_count: usize,
    pub title: String,
    pub rotates_generation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BlobDescriptor {
    pub sha256: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PortableBlob {
    pub sha256: String,
    pub size_bytes: u64,
    pub relative_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProjectBundleManifest {
    pub format_version: u32,
    pub created_at: String,
    pub backup: ProjectBackup,
    pub blobs: Vec<PortableBlob>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BundleImportPlan {
    pub project: ImportPlan,
    pub blob_count: usize,
    pub total_blob_bytes: u64,
}

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

/// Compare the actual content-addressed source bytes against the declared,
/// bounded measurement snapshot. This is deliberately stricter than ordinary
/// document metadata validation: no Native SDK or Studio client can author
/// values that the referenced JSON/CSV does not contain.
///
/// Only a narrow, unambiguous four-column CSV subset is admitted (no quoted
/// commas/newlines) until a versioned general CSV grammar is introduced.
/// The caller already verified blob path, max byte budget, and SHA-256.
fn parse_measured_source_rows(source: &[u8]) -> Result<Vec<motionwright_domain::MeasuredDatum>> {
    use motionwright_domain::{DomainError, MeasuredDatum, MeasuredUnit};
    let invalid = || {
        StorageError::Domain(DomainError::Invalid(
            "imported measured source must be a bounded exact JSON array or four-column CSV".into(),
        ))
    };
    // The imported Asset MIME is checked against the project in the same
    // transaction; require unambiguous byte grammar here. JSON is an array,
    // while CSV starts with the exact versioned four-column header.
    let rows: Vec<MeasuredDatum> = if source
        .iter()
        .find(|byte| !byte.is_ascii_whitespace())
        .copied()
        == Some(b'[')
    {
        serde_json::from_slice(source).map_err(|_| invalid())?
    } else {
        let text = std::str::from_utf8(source).map_err(|_| invalid())?;
        let mut lines = text.lines();
        if lines.next() != Some("key,label,milli,unit") {
            return Err(invalid());
        }
        let mut rows = Vec::new();
        for line in lines {
            let cells: Vec<_> = line.trim_end_matches('\r').split(',').collect();
            if cells.len() != 4 || rows.len() >= 64 {
                return Err(invalid());
            }
            let unit = match cells[3] {
                "count" => MeasuredUnit::Count,
                "percent" => MeasuredUnit::Percent,
                "seconds" => MeasuredUnit::Seconds,
                "milliseconds" => MeasuredUnit::Milliseconds,
                "bytes" => MeasuredUnit::Bytes,
                _ => return Err(invalid()),
            };
            let milli = cells[2].parse::<i64>().map_err(|_| invalid())?;
            rows.push(MeasuredDatum {
                key: cells[0].to_owned(),
                label: cells[1].to_owned(),
                milli,
                unit,
            });
        }
        rows
    };
    if rows.is_empty() || rows.len() > 64 {
        return Err(invalid());
    }
    Ok(rows)
}

// A valid document is not authority to redirect the row that contains it.
// Every full-document consumer decodes the SQL identity and JSON together.
struct StoredProjectRow {
    id: String,
    generation: String,
    revision: i64,
    title: String,
    document_json: String,
}

impl StoredProjectRow {
    fn read(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            id: row.get(0)?,
            generation: row.get(1)?,
            revision: row.get(2)?,
            title: row.get(3)?,
            document_json: row.get(4)?,
        })
    }

    fn into_project(self) -> Result<Project> {
        let project: Project = serde_json::from_str(&self.document_json)?;
        project.validate()?;
        if project.id.to_string() != self.id
            || project.generation.is_nil()
            || project.generation.to_string() != self.generation
            || i64::try_from(project.revision).ok() != Some(self.revision)
            || project.title != self.title
        {
            return Err(DomainError::Invalid(
                "stored project authority columns differ from the document".into(),
            )
            .into());
        }
        Ok(project)
    }
}

fn load_project_from_row(conn: &Connection, id: Uuid) -> Result<Project> {
    conn.query_row(
        "SELECT id,generation,revision,title,document_json FROM projects WHERE id=?1",
        [id.to_string()],
        StoredProjectRow::read,
    )
    .optional()?
    .ok_or(StorageError::NotFound)?
    .into_project()
}

// Receipt UUIDs and wall-clock timestamps identify observations, not commit order.
// Preserve the original table so extension foreign keys and triggers keep their
// targets. SQLite allocates the ordinal in the same transaction as the receipt.
const PRODUCTION_RECEIPT_ORDER_SCHEMA: [(&str, &str, &str); 3] = [
    (
        "production_receipt_order",
        "table",
        "CREATE TABLE production_receipt_order(
           sequence INTEGER PRIMARY KEY AUTOINCREMENT,
           receipt_id TEXT NOT NULL UNIQUE,
           project_id TEXT NOT NULL,
           FOREIGN KEY(receipt_id) REFERENCES production_receipts(receipt_id)
             ON UPDATE CASCADE ON DELETE CASCADE,
           FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
         )",
    ),
    (
        "production_receipt_order_by_project",
        "index",
        "CREATE INDEX production_receipt_order_by_project
           ON production_receipt_order(project_id, sequence DESC)",
    ),
    (
        "production_receipt_order_append",
        "trigger",
        "CREATE TRIGGER production_receipt_order_append
         AFTER INSERT ON production_receipts
         BEGIN
           INSERT INTO production_receipt_order(receipt_id,project_id)
             VALUES(NEW.receipt_id,NEW.project_id);
         END",
    ),
];

fn normalized_schema_sql(sql: &str) -> String {
    sql.trim_end_matches(';')
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}

fn validate_production_receipt_order_layout(conn: &Connection, version: u32) -> Result<()> {
    for (name, expected_kind, expected_sql) in PRODUCTION_RECEIPT_ORDER_SCHEMA {
        // Triggers have a different SQLite name namespace from tables and
        // indexes. Inspect cardinality across every object kind, not one row.
        let existing = {
            let mut statement = conn.prepare(
                "SELECT type,sql FROM sqlite_master WHERE name=?1 COLLATE NOCASE LIMIT 2",
            )?;
            statement
                .query_map([name], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        match existing.as_slice() {
            [] if version < 3 => {}
            [(kind, Some(sql))]
                if version >= 3
                    && kind.as_str() == expected_kind
                    && normalized_schema_sql(sql) == normalized_schema_sql(expected_sql) => {}
            _ => {
                return Err(StorageError::InvalidBackup(
                    "receipt commit-order schema is missing, incompatible or reserved by an extension"
                        .into(),
                ));
            }
        }
    }
    if version >= 3 {
        validate_production_receipt_order_integrity(conn, None)?;
    }
    Ok(())
}

fn validate_production_receipt_order_integrity(
    conn: &Connection,
    project_id: Option<Uuid>,
) -> Result<()> {
    // An INNER JOIN alone would hide an unmapped pending mutation. Never repair
    // an absent mapping after migration by assigning that old receipt a new head.
    let invalid: bool = conn.query_row(
        "SELECT
           EXISTS(SELECT 1 FROM production_receipts AS r
                  LEFT JOIN production_receipt_order AS o ON o.receipt_id=r.receipt_id
                  WHERE (?1 IS NULL OR r.project_id=?1)
                    AND (o.receipt_id IS NULL OR o.project_id IS NOT r.project_id
                         OR o.sequence<=0))
           OR EXISTS(SELECT 1 FROM production_receipt_order AS o
                     LEFT JOIN production_receipts AS r ON r.receipt_id=o.receipt_id
                     WHERE (?1 IS NULL OR o.project_id=?1)
                       AND (r.receipt_id IS NULL OR o.project_id IS NOT r.project_id
                            OR o.sequence<=0))",
        [project_id.map(|id| id.to_string())],
        |row| row.get(0),
    )?;
    if invalid {
        return Err(StorageError::InvalidBackup(
            "receipt commit-order mapping is incomplete or inconsistent".into(),
        ));
    }
    Ok(())
}

fn migrate_production_receipt_order(conn: &Connection, version: u32) -> Result<()> {
    if version < 3 {
        // Called only under the schema IMMEDIATE transaction. The retained rowid
        // order is the migration boundary, not a reconstructed historical clock.
        conn.execute_batch(PRODUCTION_RECEIPT_ORDER_SCHEMA[0].2)?;
        conn.execute(
            "INSERT INTO production_receipt_order(receipt_id,project_id)
             SELECT receipt_id,project_id FROM production_receipts ORDER BY rowid",
            [],
        )?;
        for (_, _, sql) in &PRODUCTION_RECEIPT_ORDER_SCHEMA[1..] {
            conn.execute_batch(sql)?;
        }
    }
    validate_production_receipt_order_layout(conn, 3)
}

/// A version number alone cannot establish that an existing SQLite file
/// is a Motionwright project database. Reject unknown and incomplete layouts
/// before running the WAL-mode or schema-creation pragmas. This recognizes
/// source layout, not the application's owner or an external authorization.
fn validate_existing_project_store_layout(conn: &Connection, version: u32) -> Result<()> {
    // No user tables is NOT equivalent to an empty database: a foreign
    // SQLite file may contain only a persisted VIEW (or another user object).
    // Do not enable WAL or create product tables in such a database.
    // Exclude only SQLite's reserved internal names such as sqlite_sequence.
    let mut statement = conn.prepare(
        "SELECT name,type FROM sqlite_master
         WHERE name NOT GLOB 'sqlite_*' ORDER BY name",
    )?;
    let mapped = statement.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut tables = BTreeSet::new();
    let mut has_user_schema_object = false;
    for entry in mapped {
        let (name, kind) = entry?;
        has_user_schema_object = true;
        if kind == "table" {
            tables.insert(name);
        }
    }
    drop(statement);

    if !has_user_schema_object {
        return if version == 0 {
            Ok(()) // A genuinely fresh, empty SQLite database.
        } else {
            Err(StorageError::InvalidBackup(
                "an existing versioned SQLite database has no Motionwright tables".into(),
            ))
        };
    }

    // These are the mandatory common columns in the original v1 source and
    // every supported later schema. Additional third-party tables are not
    // touched: requiring the entire exact table set would reject existing
    // projects with harmless extensions.
    const REQUIRED: [(&str, &[&str]); 3] = [
        (
            "projects",
            &[
                "id",
                "generation",
                "revision",
                "title",
                "document_json",
                "updated_at",
            ],
        ),
        (
            "receipts",
            &[
                "project_id",
                "request_id",
                "request_sha256",
                "result_json",
                "created_at",
            ],
        ),
        (
            "events",
            &[
                "event_id",
                "project_id",
                "revision",
                "kind",
                "payload_json",
                "created_at",
            ],
        ),
    ];
    for (name, required_columns) in REQUIRED {
        if !tables.contains(name) {
            return Err(StorageError::InvalidBackup(
                "existing SQLite file is not a recognized complete Motionwright store".into(),
            ));
        }
        // Names are fixed internal identifiers, never input-derived SQL.
        let mut statement = conn.prepare(&format!("PRAGMA table_info({name})"))?;
        let columns = statement
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<rusqlite::Result<BTreeSet<String>>>()?;
        if required_columns
            .iter()
            .any(|column| !columns.contains(*column))
        {
            return Err(StorageError::InvalidBackup(
                "existing SQLite file has an unrecognized project table layout".into(),
            ));
        }
    }
    validate_production_receipt_order_layout(conn, version)?;
    apply_receipt_binding::validate_schema(conn, version)
}

/// A SQLite schema is not proof that the caller owns the path to it.
/// Resolve no links at the SQLite leaf or in any pre-existing parent; doing
/// so could create WAL, schema or project state in another owner's directory.
/// The path checks are intentionally performed before *any* directory creation.
fn checked_sqlite_storage_path(path: &Path) -> Result<()> {
    if path.as_os_str().is_empty()
        || path
            .to_str()
            .is_some_and(|name| name.to_ascii_lowercase().starts_with("file:"))
    {
        return Err(StorageError::UnsafeSourcePath(
            "SQLite storage requires an ordinary filesystem path".into(),
        ));
    }
    for (index, component) in path.ancestors().enumerate() {
        let metadata = match fs::symlink_metadata(component) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.into()),
        };
        #[cfg(windows)]
        let reparse = {
            use std::os::windows::fs::MetadataExt;
            metadata.file_attributes() & 0x0400 != 0
        };
        #[cfg(not(windows))]
        let reparse = false;
        if metadata.file_type().is_symlink() || reparse {
            // macOS system-level /var and /tmp aliases resolve to built-in
            // /private paths on supported hosts. These privileged root
            // aliases are not user project-directory redirections.
            #[cfg(target_os = "macos")]
            if darwin_system_directory_alias(component) {
                continue;
            }
            return Err(StorageError::UnsafeSourcePath(
                "SQLite storage cannot follow a symbolic-link or reparse path".into(),
            ));
        }
        if index == 0 && !metadata.is_file() {
            return Err(StorageError::UnsafeSourcePath(
                "existing SQLite storage leaf must be a regular file".into(),
            ));
        }
        if index != 0 && !metadata.is_dir() {
            return Err(StorageError::UnsafeSourcePath(
                "SQLite storage parent must be a regular directory".into(),
            ));
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn darwin_system_directory_alias(path: &Path) -> bool {
    // A specific immutable OS directory mapping, not a generic symlink
    // exception. A foreign or substituted root alias still fails closed.
    let expected = if path == Path::new("/var") {
        "/private/var"
    } else if path == Path::new("/tmp") {
        "/private/tmp"
    } else {
        return false;
    };
    fs::canonicalize(path).is_ok_and(|resolved| resolved == Path::new(expected))
}

pub struct Store {
    conn: Connection,
    path: PathBuf,
    blob_root: PathBuf,
}

impl Store {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        let parent = path.parent().unwrap_or_else(|| Path::new("."));
        Self::open_with_blob_root(&path, parent.join("blobs"))
    }

    pub fn open_with_blob_root(
        path: impl AsRef<Path>,
        blob_root: impl AsRef<Path>,
    ) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        let blob_root = blob_root.as_ref().to_path_buf();
        checked_sqlite_storage_path(&path)?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        checked_sqlite_storage_path(&path)?;
        // On macOS /var and /tmp are built-in aliases to /private/var and
        // /private/tmp. The checks above admit only these *exact* privileged
        // roots, but SQLite NOFOLLOW still rejects the unresolved alias.
        // Canonicalize the already-checked parent, never the SQLite leaf:
        // this keeps the SQLite-native final-leaf no-follow protection.
        #[cfg(target_os = "macos")]
        let sqlite_path = {
            let parent = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .unwrap_or_else(|| Path::new("."));
            let leaf = path.file_name().ok_or_else(|| {
                StorageError::UnsafeSourcePath("SQLite storage requires a filename".into())
            })?;
            fs::canonicalize(parent)?.join(leaf)
        };
        #[cfg(not(target_os = "macos"))]
        let sqlite_path = path.clone();
        let flags = OpenFlags::default() | OpenFlags::SQLITE_OPEN_NOFOLLOW;
        let mut conn = Connection::open_with_flags(&sqlite_path, flags)?;
        conn.busy_timeout(Duration::from_secs(5))?;
        // Keep the pre-WAL source check on one read snapshot: a concurrent
        // migration must not pair an old version with newly committed objects.
        let admission = conn.unchecked_transaction()?;
        let observed_schema: u32 =
            admission.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if observed_schema > STORAGE_SCHEMA_VERSION {
            return Err(StorageError::UnsupportedSchemaVersion {
                found: observed_schema,
                supported: STORAGE_SCHEMA_VERSION,
            });
        }
        validate_existing_project_store_layout(&admission, observed_schema)?;
        admission.commit()?;
        // Connection pragmas must execute outside a transaction. All
        // schema DDL and its authoritative user_version stamp are committed
        // together under one SQLite IMMEDIATE writer lock.
        conn.execute_batch(
            "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        )?;
        let schema_tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        // An independent opener may have advanced the schema since our
        // fast-fail check. Revalidate AFTER acquiring the writer lock.
        let locked_schema: u32 =
            schema_tx.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if locked_schema > STORAGE_SCHEMA_VERSION {
            return Err(StorageError::UnsupportedSchemaVersion {
                found: locked_schema,
                supported: STORAGE_SCHEMA_VERSION,
            });
        }
        // Another opener may have changed the schema before this writer lock.
        validate_existing_project_store_layout(&schema_tx, locked_schema)?;
        schema_tx.execute_batch(
            r#"
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

            CREATE TABLE IF NOT EXISTS production_receipts(
              receipt_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              request_id TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              command TEXT NOT NULL,
              stage TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            -- Derived proxy metadata is disposable, and never grants render authority.
            CREATE TABLE IF NOT EXISTS render_window_cache_entries(
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              deliverable_id TEXT NOT NULL,
              source_start INTEGER NOT NULL CHECK(source_start >= 0),
              source_end INTEGER NOT NULL CHECK(source_end > source_start),
              fingerprint_sha256 TEXT NOT NULL,
              PRIMARY KEY(project_id, generation, revision, deliverable_id,
                          source_start, source_end, fingerprint_sha256),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS render_window_cache_by_owner
              ON render_window_cache_entries(project_id, generation, deliverable_id);

            -- A commit atomically revokes cache registrations. Recoverable
            -- eviction records retain only derived-artifact keys, never source.
            CREATE TABLE IF NOT EXISTS render_window_cache_evictions(
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              deliverable_id TEXT NOT NULL,
              source_start INTEGER NOT NULL CHECK(source_start >= 0),
              source_end INTEGER NOT NULL CHECK(source_end > source_start),
              fingerprint_sha256 TEXT NOT NULL,
              PRIMARY KEY(project_id, generation, revision, deliverable_id,
                          source_start, source_end, fingerprint_sha256),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TRIGGER IF NOT EXISTS render_window_cache_invalidate_revision
            AFTER UPDATE OF generation, revision ON projects
            WHEN OLD.generation<>NEW.generation OR OLD.revision<>NEW.revision
            BEGIN
              INSERT OR IGNORE INTO render_window_cache_evictions(
                project_id,generation,revision,deliverable_id,
                source_start,source_end,fingerprint_sha256)
                SELECT project_id,generation,revision,deliverable_id,
                       source_start,source_end,fingerprint_sha256
                FROM render_window_cache_entries
                WHERE project_id=NEW.id
                  AND (generation<>NEW.generation OR revision<>NEW.revision);
              DELETE FROM render_window_cache_entries
              WHERE project_id=NEW.id
                AND (generation<>NEW.generation OR revision<>NEW.revision);
            END;

            CREATE INDEX IF NOT EXISTS events_by_project_revision
              ON events(project_id, revision);
            CREATE INDEX IF NOT EXISTS production_receipts_by_project
              ON production_receipts(project_id, receipt_id DESC);
            CREATE INDEX IF NOT EXISTS production_receipts_by_request
              ON production_receipts(project_id, request_id, receipt_id DESC);
            "#,
        )?;
        migrate_production_receipt_order(&schema_tx, locked_schema)?;
        apply_receipt_binding::migrate_schema(&schema_tx, locked_schema)?;
        if locked_schema < STORAGE_SCHEMA_VERSION {
            schema_tx.pragma_update(None, "user_version", i64::from(STORAGE_SCHEMA_VERSION))?;
        }
        schema_tx.commit()?;
        fs::create_dir_all(&blob_root)?;
        ensure_regular_directory(&blob_root, "content-addressed storage root")?;
        let store = Self {
            conn,
            path,
            blob_root,
        };
        // Recover a bounded number of obsolete derived manifests after an
        // interrupted edit. Tombstones and misses remain authoritative even
        // if filesystem cleanup encounters an unsafe or inaccessible file.
        let _ = store.drain_render_window_evictions(64);
        Ok(store)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn blob_root(&self) -> &Path {
        &self.blob_root
    }

    /// Admit new bytes through the same private capture as canonical imports.
    /// Historical blob reads and receipt recovery remain separate operations.
    pub fn ingest_blob_file(&self, source: impl AsRef<Path>) -> Result<BlobDescriptor> {
        let staged = self.stage_file_import(source.as_ref())?;
        self.publish_file_import(&staged)
    }

    pub fn verified_blob_path(&self, digest: &str) -> Result<PathBuf> {
        self.verify_blob(digest)?;
        self.blob_path(digest)
    }

    pub fn verify_blob(&self, digest: &str) -> Result<BlobDescriptor> {
        let path = self.checked_existing_blob_path(digest)?;
        let descriptor = hash_file(&path)?;
        if descriptor.sha256 != digest {
            return Err(StorageError::InvalidBackup(
                "content-addressed blob failed digest verification".into(),
            ));
        }
        Ok(descriptor)
    }

    pub fn read_blob(&self, digest: &str, limit: u64) -> Result<Vec<u8>> {
        read_verified_blob_from_root(&self.blob_root, digest, limit)
    }

    pub fn export_project_bundle(
        &mut self,
        id: Uuid,
        destination: impl AsRef<Path>,
    ) -> Result<ProjectBundleManifest> {
        let destination = destination.as_ref();
        if destination.exists() {
            return Err(StorageError::DestinationExists);
        }
        let backup = self.export_project(id)?;
        self.export_captured_bundle(backup, destination)
    }

    pub fn inspect_project_bundle(&self, source: impl AsRef<Path>) -> Result<BundleImportPlan> {
        let manifest = load_bundle_manifest(source.as_ref())?;
        self.validate_bundle(source.as_ref(), &manifest)
    }

    pub fn import_project_bundle(&mut self, source: impl AsRef<Path>) -> Result<Project> {
        self.import_bundle_capture(source.as_ref(), None)
    }

    fn validate_bundle(
        &self,
        source: &Path,
        manifest: &ProjectBundleManifest,
    ) -> Result<BundleImportPlan> {
        if manifest.format_version != PROJECT_BUNDLE_FORMAT_VERSION {
            return Err(StorageError::InvalidBackup(format!(
                "unsupported bundle format version {}",
                manifest.format_version
            )));
        }
        if manifest.blobs.len() > MAX_BUNDLE_BLOBS {
            return Err(StorageError::InvalidBackup(
                "portable bundle contains too many blobs".into(),
            ));
        }
        ensure_backup_secret_free(&manifest.backup, Some(MAX_BUNDLE_MANIFEST_BYTES))?;
        ensure_secret_free_bytes(&serde_json::to_vec(manifest)?, "portable bundle metadata")?;
        let project_plan = self.inspect_import(&manifest.backup)?;
        let mut expected = BTreeSet::new();
        for asset in &manifest.backup.project.assets {
            let digest = asset.content_sha256.as_deref().ok_or_else(|| {
                StorageError::InvalidBackup(format!("asset {} has no content digest", asset.id))
            })?;
            validate_digest(digest)?;
            expected.insert(digest.to_owned());
        }

        let mut observed = BTreeSet::new();
        let mut total_blob_bytes = 0_u64;
        for blob in &manifest.blobs {
            validate_digest(&blob.sha256)?;
            let expected_relative = blob_relative_path(&blob.sha256)?;
            if blob.relative_path != expected_relative || !observed.insert(blob.sha256.clone()) {
                return Err(StorageError::InvalidBackup(
                    "bundle blob manifest is duplicate or has a non-canonical path".into(),
                ));
            }
            let path = bundle_member_path(source, &blob.relative_path, "portable bundle blob")?;
            let size = fs::symlink_metadata(&path)?.len();
            if size != blob.size_bytes {
                return Err(StorageError::InvalidBackup(
                    "bundle blob size differs from its manifest before hashing".into(),
                ));
            }
            total_blob_bytes = total_blob_bytes.checked_add(size).ok_or_else(|| {
                StorageError::InvalidBackup("portable bundle byte total overflowed".into())
            })?;
            if total_blob_bytes > MAX_BUNDLE_TOTAL_BYTES {
                return Err(StorageError::BlobTooLarge {
                    size: total_blob_bytes,
                    limit: MAX_BUNDLE_TOTAL_BYTES,
                });
            }
            // MIME is caller-supplied metadata. Check the actual member bytes
            // in the same bounded pass that establishes their digest.
            let descriptor =
                portability::inspect_portable_file(&path, "portable asset", blob.size_bytes)?;
            if descriptor.sha256 != blob.sha256 || descriptor.size_bytes != blob.size_bytes {
                return Err(StorageError::InvalidBackup(
                    "bundle blob failed digest or size verification".into(),
                ));
            }
        }
        if observed != expected {
            return Err(StorageError::InvalidBackup(
                "bundle blob set does not match project asset digests".into(),
            ));
        }

        Ok(BundleImportPlan {
            project: project_plan,
            blob_count: manifest.blobs.len(),
            total_blob_bytes,
        })
    }

    fn blob_path(&self, digest: &str) -> Result<PathBuf> {
        validate_digest(digest)?;
        Ok(self
            .blob_root
            .join("sha256")
            .join(&digest[..2])
            .join(digest))
    }

    /// The same checked path is used by public blob reads and transactional
    /// first-party capture admission. Caller ownership is checked separately.
    fn checked_existing_blob_path(&self, digest: &str) -> Result<PathBuf> {
        checked_existing_blob_path_from_root(&self.blob_root, digest)
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
        load_project_from_row(&self.conn, id)
    }

    pub fn list_projects(&self, limit: usize) -> Result<Vec<Project>> {
        let limit = limit.clamp(1, 100);
        let mut stmt = self.conn.prepare(
            "SELECT id,generation,revision,title,document_json
             FROM projects ORDER BY updated_at DESC, id LIMIT ?1",
        )?;
        let rows = stmt.query_map([limit as i64], StoredProjectRow::read)?;
        let mut projects = Vec::new();
        for row in rows {
            projects.push(row?.into_project()?);
        }
        Ok(projects)
    }

    pub fn list_project_summaries(
        &self,
        cursor: Option<&ProjectCursor>,
        limit: usize,
    ) -> Result<ProjectPage> {
        let limit = limit.clamp(1, 100);
        let fetch_limit = limit.saturating_add(1);
        let mut rows = Vec::new();

        if let Some(cursor) = cursor {
            let mut stmt = self.conn.prepare(
                "SELECT id,generation,revision,title,updated_at
                 FROM projects
                 WHERE updated_at < ?1 OR (updated_at = ?1 AND id > ?2)
                 ORDER BY updated_at DESC,id ASC
                 LIMIT ?3",
            )?;
            let mapped = stmt.query_map(
                params![
                    &cursor.updated_at,
                    cursor.id.to_string(),
                    fetch_limit as i64
                ],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, i64>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                    ))
                },
            )?;
            for row in mapped {
                rows.push(row?);
            }
        } else {
            let mut stmt = self.conn.prepare(
                "SELECT id,generation,revision,title,updated_at
                 FROM projects
                 ORDER BY updated_at DESC,id ASC
                 LIMIT ?1",
            )?;
            let mapped = stmt.query_map([fetch_limit as i64], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                ))
            })?;
            for row in mapped {
                rows.push(row?);
            }
        }

        let truncated = rows.len() > limit;
        rows.truncate(limit);
        let mut items = Vec::with_capacity(rows.len());
        for (id, generation, revision, title, updated_at) in rows {
            let id = Uuid::parse_str(&id).map_err(|_| {
                StorageError::Domain(DomainError::Invalid("stored project id is invalid".into()))
            })?;
            let generation = Uuid::parse_str(&generation).map_err(|_| {
                StorageError::Domain(DomainError::Invalid(
                    "stored project generation is invalid".into(),
                ))
            })?;
            items.push(ProjectSummary {
                id,
                generation,
                revision: u64::try_from(revision).map_err(|_| {
                    StorageError::Domain(DomainError::Invalid(
                        "stored project revision is invalid".into(),
                    ))
                })?,
                title,
                updated_at,
            });
        }

        let next_cursor = if truncated {
            items.last().map(|item| ProjectCursor {
                updated_at: item.updated_at.clone(),
                id: item.id,
            })
        } else {
            None
        };
        Ok(ProjectPage {
            items,
            next_cursor,
            truncated,
        })
    }

    pub fn export_project(&mut self, id: Uuid) -> Result<ProjectBackup> {
        self.export_project_snapshot(id, None)
    }

    pub fn inspect_import(&self, backup: &ProjectBackup) -> Result<ImportPlan> {
        validate_backup(backup)?;
        let exists = self
            .conn
            .query_row(
                "SELECT 1 FROM projects WHERE id=?1",
                [backup.project.id.to_string()],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        if exists {
            return Err(StorageError::ProjectExists);
        }
        Ok(ImportPlan {
            project_id: backup.project.id,
            source_generation: backup.project.generation,
            revision: backup.project.revision,
            event_count: backup.events.len(),
            title: backup.project.title.clone(),
            rotates_generation: true,
        })
    }

    pub fn import_project(&mut self, backup: &ProjectBackup) -> Result<Project> {
        self.import_project_with_publication(backup, |_| Ok(()))
    }

    // Hold SQLite's IMMEDIATE writer lock from the destination-ID check
    // through CAS publication and project/journal commit. Another SQLite
    // writer cannot claim the imported ID in the interval after inspection.
    // This is not a filesystem/SQLite crash-atomic transaction.
    fn import_project_with_publication(
        &mut self,
        backup: &ProjectBackup,
        publish_captured_blobs: impl FnOnce(&Path) -> Result<()>,
    ) -> Result<Project> {
        self.inspect_import(backup)?;
        let mut project = backup.project.clone();
        project.generation = Uuid::now_v7();
        project.updated_at = chrono::Utc::now();
        project.validate()?;
        let project_json = serde_json::to_string(&project)?;

        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let exists = tx
            .query_row(
                "SELECT 1 FROM projects WHERE id=?1",
                [project.id.to_string()],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        if exists {
            return Err(StorageError::ProjectExists);
        }
        tx.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)
             VALUES(?1,?2,?3,?4,?5,?6)",
            params![
                project.id.to_string(),
                project.generation.to_string(),
                project.revision as i64,
                project.title,
                project_json,
                project.updated_at.to_rfc3339(),
            ],
        )?;

        for event in &backup.events {
            tx.execute(
                "INSERT INTO events(project_id,revision,kind,payload_json,created_at)
                 VALUES(?1,?2,'change',?3,?4)",
                params![
                    project.id.to_string(),
                    event.revision as i64,
                    serde_json::to_string(&event.change)?,
                    &event.created_at,
                ],
            )?;
        }
        // Prepare both authoritative SQLite tables BEFORE publishing media.
        // A rejected project or journal write cannot orphan CAS. The writer
        // lock remains held through captured publication and SQLite commit.
        publish_captured_blobs(&self.blob_root)?;
        tx.commit()?;
        Ok(project)
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
        let blob_root = self.blob_root.clone();
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;

        // Durable results retain their original revision, but cannot grant
        // access across a different resource or project generation.
        let mut project = load_project_from_row(&tx, id)?;

        if expected.resource != project.resource_key() || expected.generation != project.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if let Some(receipt) = apply_receipt_binding::read_receipt(&tx, id, request_id)
            .map_err(apply_receipt_binding::ReceiptCheckError::into_storage_error)?
        {
            if receipt.request_sha256 != request_sha {
                return Err(StorageError::RequestReuse);
            }
            let mut outcome =
                apply_receipt_binding::check_receipt(&tx, &project, request_id, &receipt)
                    .map_err(apply_receipt_binding::ReceiptCheckError::into_storage_error)?
                    .outcome;
            outcome.replayed = true;
            tx.commit()?;
            return Ok(outcome);
        }

        if expected.revision != project.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }

        // Only a new mutation inspects source bytes. Replaying a committed
        // receipt neither reimports nor proves continued asset availability.
        let required_blob = match change {
            Change::AddAsset { asset } | Change::ImportMeasuredVoice { asset, .. } => {
                match asset.content_sha256.as_deref() {
                    Some(digest) => Some((
                        digest.to_owned(),
                        checked_existing_blob_path_from_root(&blob_root, digest)?,
                    )),
                    None => None,
                }
            }
            _ => None,
        };

        if let Change::RegisterCaptureSource { record } = change {
            // Never touch a digest until the receipt-replay, generation and
            // canonical revision checks above have confirmed authority.
            capture_evidence::verify_capture_source(&blob_root, &project, record)?;
        }
        if let Change::RegisterIdentityOrigin { record } = change {
            // Consent/document bytes are read only after exact owner CAS and
            // canonical durable-request replay have already been checked.
            identity_evidence::verify_identity_source(&blob_root, &project, record)?;
        }
        if let Change::UpsertMeasuredTable { table } = change {
            // These checks run *after* receipt replay and the canonical
            // project generation/revision gate. A successful repeat of the
            // same durable request returns its original receipt, while a
            // stale/foreign request cannot learn blob existence or metadata.
            if !project.assets.iter().any(|asset| {
                asset.id == table.source_asset_id
                    && asset.content_sha256.as_deref() == Some(table.source_sha256.as_str())
                    && ["application/json", "text/csv"].contains(&asset.media_type.as_str())
            }) {
                return Err(StorageError::Domain(DomainError::Invalid(
                    "the project does not own the exact imported measured source asset".into(),
                )));
            }
            let bytes =
                read_verified_blob_from_root(&self.blob_root, &table.source_sha256, 64 * 1024)?;
            if parse_measured_source_rows(&bytes)? != table.rows {
                return Err(StorageError::Domain(DomainError::Invalid(
                    "declared measurements differ from the imported SHA-256 source bytes".into(),
                )));
            }
        }
        if let Some((digest, path)) = required_blob {
            let descriptor = hash_file(&path)?;
            if descriptor.sha256 != digest {
                return Err(StorageError::InvalidBackup(
                    "asset blob failed content-address verification".into(),
                ));
            }
        }

        let previous_revision = project.revision;
        let base = apply_receipt_binding::ApplyBase::capture(&project);
        let result_revision = previous_revision
            .checked_add(1)
            .filter(|revision| i64::try_from(*revision).is_ok())
            .ok_or_else(|| {
                DomainError::Invalid("project revision exceeds SQLite integer range".into())
            })?;
        let previous_sql_revision = i64::try_from(previous_revision).map_err(|_| {
            DomainError::Invalid("project revision exceeds SQLite integer range".into())
        })?;
        let result_sql_revision = i64::try_from(result_revision).map_err(|_| {
            DomainError::Invalid("project revision exceeds SQLite integer range".into())
        })?;
        project.apply_change(change)?;
        project.commit_revision(result_revision, change)?;
        let project_json = serde_json::to_string(&project)?;
        let changed = tx.execute(
            "UPDATE projects
             SET generation=?2,revision=?3,title=?4,document_json=?5,updated_at=?6
             WHERE id=?1 AND revision=?7",
            params![
                project.id.to_string(),
                project.generation.to_string(),
                result_sql_revision,
                project.title,
                project_json,
                project.updated_at.to_rfc3339(),
                previous_sql_revision,
            ],
        )?;
        if changed != 1 {
            return Err(StorageError::Conflict {
                expected: previous_revision,
                actual: result_revision,
            });
        }

        #[cfg(test)]
        apply_crash_tests::checkpoint(request_id, "after_project_update");

        let payload = serde_json::to_string(change)?;
        tx.execute(
            "INSERT INTO events(project_id,revision,kind,payload_json,created_at)
             VALUES(?1,?2,'change',?3,?4)",
            params![
                project.id.to_string(),
                result_sql_revision,
                payload,
                project.updated_at.to_rfc3339(),
            ],
        )?;

        #[cfg(test)]
        apply_crash_tests::checkpoint(request_id, "after_event_insert");

        let outcome = ApplyOutcome {
            project,
            previous_revision,
            request_id: request_id.to_owned(),
            replayed: false,
        };
        let result_json = serde_json::to_string(&outcome)?;
        let binding_json = apply_receipt_binding::create_binding(
            &base,
            request_id,
            &request_sha,
            &outcome,
            &result_json,
        )?;
        tx.execute(
            "INSERT INTO receipts(project_id,request_id,request_sha256,result_json,created_at,
                                  apply_binding_version,apply_binding_json)
             VALUES(?1,?2,?3,?4,?5,1,?6)",
            params![
                id.to_string(),
                request_id,
                request_sha,
                result_json,
                chrono::Utc::now().to_rfc3339(),
                binding_json,
            ],
        )?;

        #[cfg(test)]
        apply_crash_tests::checkpoint(request_id, "before_commit");
        tx.commit()?;
        #[cfg(test)]
        apply_crash_tests::checkpoint(request_id, "after_commit");
        // The SQLite trigger revoked stale cache registrations *inside* the
        // successful edit transaction. Physical derived-manifest cleanup is
        // best-effort: its durable tombstones will be retried, and failure
        // must never turn a successfully committed user edit into an error.
        let _ = self.drain_render_window_evictions(64);
        Ok(outcome)
    }

    /// Newest-first, inclusive revision cursor. This does not replace the
    /// ascending after_revision API consumed by existing Native SDK clients.
    /// Pagination is keyset-based, without OFFSET or loading the full journal.
    pub fn event_records_through(
        &self,
        id: Uuid,
        revision: u64,
        limit: usize,
    ) -> Result<Vec<ProjectEvent>> {
        let limit = limit.clamp(1, 256);
        let through = revision.min(i64::MAX as u64) as i64;
        let mut stmt = self.conn.prepare(
            "SELECT revision,payload_json,created_at FROM events
             WHERE project_id=?1 AND revision<=?2
             ORDER BY revision DESC LIMIT ?3",
        )?;
        let rows = stmt.query_map(params![id.to_string(), through, limit as i64], |row| {
            Ok((
                row.get::<_, i64>(0)? as u64,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?;
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

    pub fn event_records_since(
        &self,
        id: Uuid,
        revision: u64,
        limit: usize,
    ) -> Result<Vec<ProjectEvent>> {
        let limit = limit.clamp(1, 256);
        // A valid SQLite revision is nonnegative signed i64. Casting a larger
        // user-supplied u64 directly would wrap to a negative cursor and
        // replay history already consumed by the caller.
        let after_revision = revision.min(i64::MAX as u64) as i64;
        let mut stmt = self.conn.prepare(
            "SELECT revision,payload_json,created_at FROM events
             WHERE project_id=?1 AND revision>?2
             ORDER BY revision ASC LIMIT ?3",
        )?;
        let rows = stmt.query_map(
            params![id.to_string(), after_revision, limit as i64],
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

// A receipt is a historical result, not authority to return another project.
// Revalidate its original identity and event on the same transaction snapshot;
// later legitimate edits do not change the stored result's revision.
fn validate_apply_receipt(
    conn: &Connection,
    current: &Project,
    request_id: &str,
    request_sha: &str,
    outcome: &ApplyOutcome,
) -> Result<Change> {
    let invalid = |reason: &str| StorageError::InvalidApplyReceipt(reason.to_owned());
    outcome
        .project
        .validate()
        .map_err(|_| invalid("invalid stored result project"))?;
    if outcome.project.id != current.id
        || outcome.project.generation != current.generation
        || outcome.project.resource_key() != current.resource_key()
        || outcome.request_id != request_id
        || outcome.replayed
        || outcome.previous_revision.checked_add(1) != Some(outcome.project.revision)
        || outcome.project.revision > current.revision
    {
        return Err(invalid(
            "inconsistent result identity, generation, revision or replay state",
        ));
    }
    let revision = i64::try_from(outcome.project.revision)
        .map_err(|_| invalid("result revision exceeds SQLite integer range"))?;
    let (count, kind, payload): (i64, Option<String>, Option<String>) = conn.query_row(
        "SELECT COUNT(*),MIN(kind),MIN(payload_json)
         FROM events WHERE project_id=?1 AND revision=?2",
        params![current.id.to_string(), revision],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    )?;
    if count != 1 || kind.as_deref() != Some("change") {
        return Err(invalid("result must reference exactly one change event"));
    }
    let payload = payload.ok_or_else(|| invalid("recorded event has no payload"))?;
    let change: Change =
        serde_json::from_str(&payload).map_err(|_| invalid("invalid recorded event payload"))?;
    if request_digest(current.id, &change)
        .map_err(|_| invalid("recorded event cannot be hashed"))?
        != request_sha
    {
        return Err(invalid("recorded event differs from the requested intent"));
    }
    Ok(change)
}

fn request_digest(id: Uuid, change: &Change) -> Result<String> {
    let canonical = serde_json::to_vec(&(id, change))?;
    Ok(hex::encode(Sha256::digest(canonical)))
}

fn backup_digest(
    format_version: u32,
    source_storage_schema: u32,
    project: &Project,
    events: &[ProjectEvent],
) -> Result<String> {
    let canonical = serde_json::to_vec(&(format_version, source_storage_schema, project, events))?;
    Ok(hex::encode(Sha256::digest(canonical)))
}

fn validate_backup(backup: &ProjectBackup) -> Result<()> {
    if backup.format_version != PROJECT_BACKUP_FORMAT_VERSION {
        return Err(StorageError::InvalidBackup(format!(
            "unsupported format version {}",
            backup.format_version
        )));
    }
    if backup.source_storage_schema > STORAGE_SCHEMA_VERSION {
        return Err(StorageError::UnsupportedSchemaVersion {
            found: backup.source_storage_schema,
            supported: STORAGE_SCHEMA_VERSION,
        });
    }
    backup.project.validate()?;
    let expected = backup_digest(
        backup.format_version,
        backup.source_storage_schema,
        &backup.project,
        &backup.events,
    )?;
    if expected != backup.payload_sha256 {
        return Err(StorageError::InvalidBackup(
            "payload digest does not match project and journal".into(),
        ));
    }
    if backup.events.len() != backup.project.revision as usize {
        return Err(StorageError::InvalidBackup(
            "journal length does not match project revision".into(),
        ));
    }
    for (index, event) in backup.events.iter().enumerate() {
        let expected_revision = index as u64 + 1;
        if event.revision != expected_revision || event.created_at.is_empty() {
            return Err(StorageError::InvalidBackup(
                "journal revisions must be contiguous and timestamped".into(),
            ));
        }
    }
    ensure_backup_secret_free(backup, None)
}

fn validate_digest(digest: &str) -> Result<()> {
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(StorageError::InvalidBlobDigest);
    }
    Ok(())
}

/// All immutable source reads reject symlinks at the root, shard and leaf.
/// This free helper can be called while SQLite holds the connection mutably.
fn checked_existing_blob_path_from_root(blob_root: &Path, digest: &str) -> Result<PathBuf> {
    validate_digest(digest)?;
    let directories = [
        blob_root.to_path_buf(),
        blob_root.join("sha256"),
        blob_root.join("sha256").join(&digest[..2]),
    ];
    for directory in &directories {
        match ensure_regular_directory(directory, "content-addressed source directory") {
            Ok(_) => {}
            Err(StorageError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(StorageError::BlobMissing {
                    digest: digest.to_owned(),
                });
            }
            Err(error) => return Err(error),
        }
    }
    let path = blob_root.join("sha256").join(&digest[..2]).join(digest);
    match fs::symlink_metadata(&path) {
        Ok(meta) if meta.file_type().is_symlink() || !meta.is_file() => {
            Err(StorageError::UnsafeSourcePath(
                "content-addressed blob leaf must be a regular non-symlink file".into(),
            ))
        }
        Ok(_) => Ok(path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            Err(StorageError::BlobMissing {
                digest: digest.to_owned(),
            })
        }
        Err(error) => Err(error.into()),
    }
}
#[cfg(test)]
type VerifiedBlobCaptureHook = Box<dyn FnOnce(&Path)>;

#[cfg(test)]
std::thread_local! {
    static BEFORE_VERIFIED_BLOB_CAPTURE:
        std::cell::RefCell<Option<VerifiedBlobCaptureHook>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
fn run_verified_blob_capture_hook(path: &Path) {
    let hook = BEFORE_VERIFIED_BLOB_CAPTURE.with(|slot| slot.borrow_mut().take());
    if let Some(hook) = hook {
        hook(path);
    }
}

fn read_verified_blob_from_root(blob_root: &Path, digest: &str, limit: u64) -> Result<Vec<u8>> {
    let path = checked_existing_blob_path_from_root(blob_root, digest)?;
    let size = ensure_regular_file(&path, "content-addressed blob")?.len();
    if size > limit {
        return Err(StorageError::BlobTooLarge { size, limit });
    }
    #[cfg(test)]
    run_verified_blob_capture_hook(&path);
    let bytes = fs::read(path)?;
    if hex::encode(Sha256::digest(&bytes)) != digest {
        return Err(StorageError::InvalidBackup(
            "content-addressed blob failed digest verification".into(),
        ));
    }
    Ok(bytes)
}

fn blob_relative_path(digest: &str) -> Result<String> {
    validate_digest(digest)?;
    Ok(format!("blobs/sha256/{}/{}", &digest[..2], digest))
}

fn ensure_regular_file(path: &Path, label: &str) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    let file_type = metadata.file_type();
    if file_type.is_symlink() || !file_type.is_file() {
        return Err(StorageError::UnsafeSourcePath(format!(
            "{label} must be a regular non-symlink file"
        )));
    }
    Ok(metadata)
}

/// CAS subdirectories are created one component at a time and always
/// revalidated; create_dir_all would silently follow an existing symlink.
fn create_checked_blob_directory(path: &Path) -> Result<()> {
    match fs::create_dir(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    ensure_regular_directory(path, "content-addressed storage directory")?;
    Ok(())
}

fn ensure_regular_directory(path: &Path, label: &str) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    let file_type = metadata.file_type();
    if file_type.is_symlink() || !file_type.is_dir() {
        return Err(StorageError::UnsafeSourcePath(format!(
            "{label} must be a real non-symlink directory"
        )));
    }
    Ok(metadata)
}

fn bundle_member_path(root: &Path, relative: &str, label: &str) -> Result<PathBuf> {
    ensure_regular_directory(root, "portable bundle")?;
    let relative = Path::new(relative);
    let components = relative.components().collect::<Vec<_>>();
    if components.is_empty() {
        return Err(StorageError::UnsafeSourcePath(format!(
            "{label} path is empty"
        )));
    }

    let mut current = root.to_path_buf();
    for (index, component) in components.iter().enumerate() {
        let std::path::Component::Normal(part) = component else {
            return Err(StorageError::UnsafeSourcePath(format!(
                "{label} path is not a relative normal path"
            )));
        };
        current.push(part);
        let metadata = fs::symlink_metadata(&current)?;
        let file_type = metadata.file_type();
        let is_last = index + 1 == components.len();
        if file_type.is_symlink()
            || (is_last && !file_type.is_file())
            || (!is_last && !file_type.is_dir())
        {
            return Err(StorageError::UnsafeSourcePath(format!(
                "{label} contains a symlink or special path component"
            )));
        }
    }
    Ok(current)
}

fn hash_file(path: &Path) -> Result<BlobDescriptor> {
    ensure_regular_file(path, "hashed source")?;
    let mut input = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut size_bytes = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        size_bytes = size_bytes.saturating_add(read as u64);
    }
    Ok(BlobDescriptor {
        sha256: hex::encode(hasher.finalize()),
        size_bytes,
    })
}

fn load_bundle_manifest(source: &Path) -> Result<ProjectBundleManifest> {
    let bytes = portability::read_bundle_manifest_bytes(source)?;
    Ok(serde_json::from_slice(&bytes)?)
}

#[cfg(test)]
mod credential_stream_tests {
    use super::*;

    struct ShortReads<'a> {
        remaining: &'a [u8],
        stride: usize,
    }

    impl Read for ShortReads<'_> {
        fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
            let count = output.len().min(self.stride).min(self.remaining.len());
            output[..count].copy_from_slice(&self.remaining[..count]);
            self.remaining = &self.remaining[count..];
            Ok(count)
        }
    }

    fn scan_short_reads(mut input: impl Read) -> Result<()> {
        let mut scanner = CredentialScan::default();
        let mut buffer = [0_u8; 128];
        loop {
            let count = input.read(&mut buffer)?;
            if count == 0 {
                return Ok(());
            }
            scanner.check(&buffer[..count], "file admission test")?;
        }
    }

    #[test]
    fn credential_patterns_and_ordinary_thresholds_survive_every_short_read_width() {
        let mut markers = CREDENTIAL_PREFIXES
            .iter()
            .map(|(prefix, suffix)| {
                let mut marker = prefix.to_vec();
                marker.extend(std::iter::repeat_n(b'A', *suffix));
                marker
            })
            .collect::<Vec<_>>();
        markers.push(PRIVATE_KEY_MARKER.to_vec());
        markers.push([b"AKIA".as_slice(), &[b'A'; ACCESS_KEY_WIDTH - 4]].concat());
        for marker in markers {
            let ordinary = &marker[..marker.len() - 1];
            assert!(contains_credential_like_material(&marker));
            assert!(!contains_credential_like_material(ordinary));
            for stride in 1..=credential_pattern_width() + 1 {
                for (sample, should_reject) in [(&marker[..], true), (ordinary, false)] {
                    let mut bytes = vec![0xff; 17];
                    bytes.extend_from_slice(sample);
                    bytes.extend_from_slice(&[0, 0x80]);
                    let result = scan_short_reads(ShortReads {
                        remaining: &bytes,
                        stride,
                    });
                    if should_reject {
                        let error = result.unwrap_err();
                        assert!(matches!(error, StorageError::InvalidBackup(_)));
                        assert!(
                            !error
                                .to_string()
                                .contains(std::str::from_utf8(&marker).unwrap())
                        );
                    } else {
                        result.unwrap();
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Asset, Change};

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
    fn asset_change_requires_verified_blob_bytes() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        let project = store.create_named_project("Assets").unwrap();
        let missing_digest = "ab".repeat(32);
        let missing = Change::AddAsset {
            asset: Asset {
                id: Uuid::now_v7(),
                name: "missing.bin".into(),
                media_type: "application/octet-stream".into(),
                content_sha256: Some(missing_digest.clone()),
                source_revision: None,
            },
        };
        let error = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "asset-missing",
                &missing,
            )
            .unwrap_err();
        assert!(matches!(
            error,
            StorageError::BlobMissing { digest } if digest == missing_digest
        ));

        let source = temp.path().join("asset.bin");
        fs::write(&source, b"verified-asset").unwrap();
        let descriptor = store.ingest_blob_file(&source).unwrap();
        let change = Change::AddAsset {
            asset: Asset {
                id: Uuid::now_v7(),
                name: "asset.bin".into(),
                media_type: "application/octet-stream".into(),
                content_sha256: Some(descriptor.sha256),
                source_revision: Some("test-import".into()),
            },
        };
        let outcome = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "asset-present",
                &change,
            )
            .unwrap();
        assert_eq!(outcome.project.assets.len(), 1);
        assert_eq!(outcome.project.revision, 1);
    }

    #[test]
    fn blob_store_is_content_addressed_verified_and_bounded() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        let source = temp.path().join("voice.bin");
        fs::write(&source, b"semantic voice bytes").unwrap();

        let first = store.ingest_blob_file(&source).unwrap();
        let second = store.ingest_blob_file(&source).unwrap();
        assert_eq!(first, second);
        assert_eq!(
            store.read_blob(&first.sha256, 1024).unwrap(),
            b"semantic voice bytes"
        );
        let error = store.read_blob(&first.sha256, 4).unwrap_err();
        assert!(matches!(error, StorageError::BlobTooLarge { .. }));
        assert!(matches!(
            store.read_blob("../not-a-digest", 1024).unwrap_err(),
            StorageError::InvalidBlobDigest
        ));
    }

    #[cfg(unix)]
    #[test]
    fn cas_shard_substitution_blocks_read_registration_export_and_ingest() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let root = tempfile::tempdir().unwrap();
        let mut store = Store::open(root.path().join("database.sqlite3")).unwrap();
        let source = root.path().join("original-source.bin");
        fs::write(&source, b"bounded safe CAS bytes").unwrap();
        let blob = store.ingest_blob_file(&source).unwrap();
        let blob_path = store.blob_path(&blob.sha256).unwrap();
        assert_eq!(
            fs::metadata(&blob_path).unwrap().permissions().mode() & 0o077,
            0,
            "newly imported CAS bytes must be owner-private"
        );
        let project = store.create_named_project("Symlink-resistant CAS").unwrap();
        let outcome = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "cas-test-admit-asset",
                &Change::AddAsset {
                    asset: Asset {
                        id: Uuid::now_v7(),
                        name: "safe-asset.bin".into(),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(blob.sha256.clone()),
                        source_revision: None,
                    },
                },
            )
            .unwrap();
        let shard = blob_path.parent().unwrap();
        let preserved_shard = root.path().join("preserved-shard");
        fs::rename(shard, &preserved_shard).unwrap();
        symlink(&preserved_shard, shard).unwrap();

        assert!(matches!(
            store.verify_blob(&blob.sha256),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(matches!(
            store.read_blob(&blob.sha256, 512),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(matches!(
            store.ingest_blob_file(&source),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(matches!(
            store.apply(
                outcome.project.id,
                &RevisionStamp::from(&outcome.project),
                "cas-test-admit-another",
                &Change::AddAsset {
                    asset: Asset {
                        id: Uuid::now_v7(),
                        name: "replay.bin".into(),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(blob.sha256.clone()),
                        source_revision: None,
                    },
                },
            ),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        let delivery = root.path().join("unsafe-bundle-output");
        assert!(matches!(
            store.export_project_bundle(project.id, &delivery),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(
            !delivery.exists(),
            "a failed export cannot create a destination"
        );
        assert_eq!(
            fs::read(preserved_shard.join(&blob.sha256)).unwrap(),
            b"bounded safe CAS bytes"
        );
    }

    #[cfg(unix)]
    #[test]
    fn cas_symlinked_staging_and_root_are_rejected_before_writes() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let external = root.path().join("outside-blobs");
        fs::create_dir(&external).unwrap();
        let alias = root.path().join("aliased-blob-root");
        symlink(&external, &alias).unwrap();
        let failed = Store::open_with_blob_root(root.path().join("aliased.sqlite3"), &alias)
            .err()
            .expect("symlinked blob root must not open");
        assert!(matches!(failed, StorageError::UnsafeSourcePath(_)));

        let store = Store::open(root.path().join("legit.sqlite3")).unwrap();
        symlink(&external, store.blob_root().join(".staging")).unwrap();
        let source = root.path().join("input.bin");
        fs::write(&source, b"safe input stays out of outside dir").unwrap();
        assert!(matches!(
            store.ingest_blob_file(&source),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert_eq!(fs::read_dir(&external).unwrap().count(), 0);
    }

    #[cfg(unix)]
    #[test]
    fn existing_dangling_cas_leaf_cannot_be_replaced_by_ingest() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let store = Store::open(root.path().join("db.sqlite3")).unwrap();
        let content = b"same bytes cannot replace existing symlink";
        let digest = hex::encode(Sha256::digest(content));
        let expected = store.blob_path(&digest).unwrap();
        fs::create_dir_all(expected.parent().unwrap()).unwrap();
        let victim = root.path().join("nonexistent-owner-target");
        symlink(&victim, &expected).unwrap();
        let input = root.path().join("source.bin");
        fs::write(&input, content).unwrap();
        assert!(matches!(
            store.ingest_blob_file(&input),
            Err(StorageError::UnsafeSourcePath(_))
        ));
        assert!(
            fs::symlink_metadata(&expected)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        assert!(!victim.exists());
    }

    #[test]
    fn portable_bundle_round_trip_preserves_history_and_verified_assets() {
        let source_dir = tempfile::tempdir().unwrap();
        let mut source = Store::open(source_dir.path().join("source.sqlite3")).unwrap();
        let blob_source = source_dir.path().join("voice.wav");
        fs::write(&blob_source, b"measured-voice-payload").unwrap();
        let descriptor = source.ingest_blob_file(&blob_source).unwrap();

        let mut project = Project::new("Portable media").unwrap();
        project.assets.push(Asset {
            id: Uuid::now_v7(),
            name: "voice.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: Some("import-r1".into()),
        });
        source.create_project(&project).unwrap();
        let changed = source
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "rename-portable",
                &Change::RenameProject {
                    title: "Portable media v2".into(),
                },
            )
            .unwrap();

        let bundle = source_dir.path().join("portable.motionwright");
        let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        assert_eq!(manifest.blobs.len(), 1);
        assert!(bundle.join("manifest.json").is_file());

        let destination_dir = tempfile::tempdir().unwrap();
        let mut destination =
            Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let plan = destination.inspect_project_bundle(&bundle).unwrap();
        assert_eq!(plan.blob_count, 1);
        assert_eq!(plan.total_blob_bytes, descriptor.size_bytes);

        let imported = destination.import_project_bundle(&bundle).unwrap();
        assert_eq!(imported.id, project.id);
        assert_eq!(imported.revision, changed.project.revision);
        assert_ne!(imported.generation, changed.project.generation);
        assert_eq!(
            destination.read_blob(&descriptor.sha256, 1024).unwrap(),
            b"measured-voice-payload"
        );
        assert_eq!(
            destination.events_since(project.id, 0, 10).unwrap().len(),
            1
        );
    }

    #[test]
    fn portable_bundle_rejects_credential_like_project_metadata_without_creating_destination() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("source.sqlite3")).unwrap();
        let token = format!("ghp_{}", "A".repeat(24));
        let project = store
            .create_named_project(&format!("Sensitive {token}"))
            .unwrap();
        let bundle = temp.path().join("portable");

        let error = store
            .export_project_bundle(project.id, &bundle)
            .unwrap_err();
        assert!(matches!(error, StorageError::InvalidBackup(_)));
        assert!(!error.to_string().contains(&token));
        assert!(!bundle.exists());
    }

    #[test]
    fn portable_bundle_import_rejects_credential_like_metadata_before_project_creation() {
        let source_dir = tempfile::tempdir().unwrap();
        let mut source = Store::open(source_dir.path().join("source.sqlite3")).unwrap();
        let project = source.create_named_project("Safe export").unwrap();
        let bundle = source_dir.path().join("portable");
        source.export_project_bundle(project.id, &bundle).unwrap();

        let manifest_path = bundle.join("manifest.json");
        let mut manifest: ProjectBundleManifest =
            serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
        let token = format!("gho_{}", "C".repeat(24));
        manifest.backup.project.title = format!("Imported {token}");
        manifest.backup.payload_sha256 = backup_digest(
            manifest.backup.format_version,
            manifest.backup.source_storage_schema,
            &manifest.backup.project,
            &manifest.backup.events,
        )
        .unwrap();
        fs::write(
            &manifest_path,
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();

        let destination_dir = tempfile::tempdir().unwrap();
        let mut destination =
            Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let error = destination.import_project_bundle(&bundle).unwrap_err();
        assert!(matches!(error, StorageError::InvalidBackup(_)));
        assert!(!error.to_string().contains(&token));
        assert!(matches!(
            destination.load_project(project.id),
            Err(StorageError::NotFound)
        ));
    }

    #[test]
    fn portable_bundle_rejects_credential_like_text_asset_without_creating_destination() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("source.sqlite3")).unwrap();
        let project = store.create_named_project("Safe metadata").unwrap();
        let token = format!("sk-{}", "B".repeat(28));
        let source = temp.path().join("notes.txt");
        fs::write(
            &source,
            format!(
                "fixture={token}
"
            ),
        )
        .unwrap();
        // Explicit legacy fixture: new admission must refuse these bytes.
        // Seed only this fixture-owned CAS path, keeping the portable-export
        // oracle independent of the current ingestion policy.
        let bytes = fs::read(&source).unwrap();
        let descriptor = BlobDescriptor {
            sha256: hex::encode(Sha256::digest(&bytes)),
            size_bytes: bytes.len() as u64,
        };
        let leaf = store.blob_path(&descriptor.sha256).unwrap();
        fs::create_dir_all(leaf.parent().unwrap()).unwrap();
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut output = options.open(&leaf).unwrap();
        output.write_all(&bytes).unwrap();
        output.sync_all().unwrap();
        drop(output);
        assert_eq!(store.verify_blob(&descriptor.sha256).unwrap(), descriptor);
        let changed = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "add-sensitive-text",
                &Change::AddAsset {
                    asset: Asset {
                        id: Uuid::now_v7(),
                        name: "notes.txt".into(),
                        media_type: "text/plain".into(),
                        content_sha256: Some(descriptor.sha256),
                        source_revision: Some("fixture".into()),
                    },
                },
            )
            .unwrap();
        let database = store.path().to_path_buf();
        drop(store);
        let mut store = Store::open(database).unwrap();
        assert_eq!(
            store.load_project(changed.project.id).unwrap(),
            changed.project
        );
        let bundle = temp.path().join("portable");

        let error = store
            .export_project_bundle(changed.project.id, &bundle)
            .unwrap_err();
        assert!(matches!(error, StorageError::InvalidBackup(_)));
        assert!(!error.to_string().contains(&token));
        assert!(!bundle.exists());
    }

    #[test]
    fn portable_bundle_rejects_tampered_blob_bytes() {
        let source_dir = tempfile::tempdir().unwrap();
        let mut source = Store::open(source_dir.path().join("source.sqlite3")).unwrap();
        let blob_source = source_dir.path().join("asset.bin");
        fs::write(&blob_source, b"original").unwrap();
        let descriptor = source.ingest_blob_file(&blob_source).unwrap();

        let mut project = Project::new("Tamper test").unwrap();
        project.assets.push(Asset {
            id: Uuid::now_v7(),
            name: "asset.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: None,
        });
        source.create_project(&project).unwrap();

        let bundle = source_dir.path().join("bundle");
        let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        fs::write(bundle.join(&manifest.blobs[0].relative_path), b"tampered").unwrap();

        let destination_dir = tempfile::tempdir().unwrap();
        let destination = Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let error = destination.inspect_project_bundle(&bundle).unwrap_err();
        assert!(matches!(error, StorageError::InvalidBackup(_)));
    }

    #[cfg(unix)]
    #[test]
    fn blob_ingest_and_portable_bundle_reject_symlink_sources() {
        use std::os::unix::fs::symlink;

        let temp = tempfile::tempdir().unwrap();
        let real_source = temp.path().join("real.bin");
        let linked_source = temp.path().join("linked.bin");
        fs::write(&real_source, b"outside-bytes").unwrap();
        symlink(&real_source, &linked_source).unwrap();

        let mut source = Store::open(temp.path().join("source.sqlite3")).unwrap();
        let ingest_error = source.ingest_blob_file(&linked_source).unwrap_err();
        assert!(matches!(ingest_error, StorageError::UnsafeSourcePath(_)));

        let descriptor = source.ingest_blob_file(&real_source).unwrap();
        let mut project = Project::new("Symlink bundle").unwrap();
        project.assets.push(Asset {
            id: Uuid::now_v7(),
            name: "real.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: None,
        });
        source.create_project(&project).unwrap();

        let bundle = temp.path().join("portable");
        let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        let bundled_blob = bundle.join(&manifest.blobs[0].relative_path);
        fs::remove_file(&bundled_blob).unwrap();
        symlink(&real_source, &bundled_blob).unwrap();

        let destination_dir = tempfile::tempdir().unwrap();
        let destination = Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let error = destination.inspect_project_bundle(&bundle).unwrap_err();
        assert!(matches!(error, StorageError::UnsafeSourcePath(_)));
    }

    #[cfg(unix)]
    #[test]
    fn portable_bundle_rejects_symlink_path_components() {
        use std::os::unix::fs::symlink;

        let temp = tempfile::tempdir().unwrap();
        let mut source = Store::open(temp.path().join("source.sqlite3")).unwrap();
        let blob_source = temp.path().join("asset.bin");
        fs::write(&blob_source, b"component-bytes").unwrap();
        let descriptor = source.ingest_blob_file(&blob_source).unwrap();

        let mut project = Project::new("Component boundary").unwrap();
        project.assets.push(Asset {
            id: Uuid::now_v7(),
            name: "asset.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: None,
        });
        source.create_project(&project).unwrap();

        let bundle = temp.path().join("bundle");
        let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        let bundled_blob = bundle.join(&manifest.blobs[0].relative_path);
        let prefix_dir = bundled_blob.parent().unwrap().to_path_buf();
        let external_prefix = temp.path().join("external-prefix");
        fs::create_dir(&external_prefix).unwrap();
        fs::copy(
            &bundled_blob,
            external_prefix.join(bundled_blob.file_name().unwrap()),
        )
        .unwrap();
        fs::remove_file(&bundled_blob).unwrap();
        fs::remove_dir(&prefix_dir).unwrap();
        symlink(&external_prefix, &prefix_dir).unwrap();

        let destination_dir = tempfile::tempdir().unwrap();
        let destination = Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let error = destination.inspect_project_bundle(&bundle).unwrap_err();
        assert!(matches!(error, StorageError::UnsafeSourcePath(_)));
    }

    #[cfg(unix)]
    #[test]
    fn portable_bundle_rejects_symlink_manifest_and_root() {
        use std::os::unix::fs::symlink;

        let temp = tempfile::tempdir().unwrap();
        let mut source = Store::open(temp.path().join("source.sqlite3")).unwrap();
        let project = source.create_named_project("Manifest boundary").unwrap();
        let bundle = temp.path().join("bundle");
        source.export_project_bundle(project.id, &bundle).unwrap();

        let manifest = bundle.join("manifest.json");
        let copied_manifest = temp.path().join("manifest-copy.json");
        fs::copy(&manifest, &copied_manifest).unwrap();
        fs::remove_file(&manifest).unwrap();
        symlink(&copied_manifest, &manifest).unwrap();

        let destination_dir = tempfile::tempdir().unwrap();
        let destination = Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let error = destination.inspect_project_bundle(&bundle).unwrap_err();
        assert!(matches!(error, StorageError::UnsafeSourcePath(_)));

        let bundle_link = temp.path().join("bundle-link");
        symlink(&bundle, &bundle_link).unwrap();
        let error = destination
            .inspect_project_bundle(&bundle_link)
            .unwrap_err();
        assert!(matches!(error, StorageError::UnsafeSourcePath(_)));
    }

    #[test]
    fn paginated_project_summaries_do_not_require_document_loading() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        for title in ["A", "B", "C"] {
            store.create_named_project(title).unwrap();
        }

        let first = store.list_project_summaries(None, 2).unwrap();
        assert_eq!(first.items.len(), 2);
        assert!(first.truncated);
        let cursor = first.next_cursor.clone().unwrap();

        let second = store.list_project_summaries(Some(&cursor), 2).unwrap();
        assert_eq!(second.items.len(), 1);
        assert!(!second.truncated);
        assert!(second.next_cursor.is_none());

        let mut ids = first
            .items
            .iter()
            .chain(second.items.iter())
            .map(|item| item.id)
            .collect::<Vec<_>>();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), 3);
    }

    #[test]
    fn backup_import_rotates_generation_and_does_not_restore_receipts() {
        let source_dir = tempfile::tempdir().unwrap();
        let mut source = Store::open(source_dir.path().join("source.sqlite3")).unwrap();
        let project = source.create_named_project("Portable").unwrap();
        let changed = source
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "original-request",
                &Change::RenameProject {
                    title: "Portable v2".into(),
                },
            )
            .unwrap();
        let backup = source.export_project(project.id).unwrap();
        assert_eq!(backup.project, changed.project);
        assert_eq!(backup.events.len(), 1);

        let destination_dir = tempfile::tempdir().unwrap();
        let mut destination =
            Store::open(destination_dir.path().join("destination.sqlite3")).unwrap();
        let plan = destination.inspect_import(&backup).unwrap();
        assert_eq!(plan.project_id, project.id);
        assert!(plan.rotates_generation);

        let imported = destination.import_project(&backup).unwrap();
        assert_eq!(imported.id, project.id);
        assert_ne!(imported.generation, backup.project.generation);
        assert_eq!(imported.revision, backup.project.revision);
        assert_eq!(
            destination.events_since(project.id, 0, 10).unwrap().len(),
            1
        );

        let outcome = destination
            .apply(
                project.id,
                &RevisionStamp::from(&imported),
                "original-request",
                &Change::RenameProject {
                    title: "Request key is reusable only in the new generation".into(),
                },
            )
            .unwrap();
        assert!(!outcome.replayed);
        assert_eq!(outcome.project.revision, imported.revision + 1);
    }

    #[test]
    fn backup_digest_and_future_schema_fail_closed() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        let project = store.create_named_project("Backup").unwrap();
        let mut backup = store.export_project(project.id).unwrap();
        backup.project.title = "Tampered".into();
        let error = store.inspect_import(&backup).unwrap_err();
        assert!(matches!(error, StorageError::InvalidBackup(_)));

        let future_path = temp.path().join("future.sqlite3");
        {
            let conn = Connection::open(&future_path).unwrap();
            conn.pragma_update(None, "user_version", i64::from(STORAGE_SCHEMA_VERSION + 1))
                .unwrap();
        }
        let error = match Store::open(future_path) {
            Err(error) => error,
            Ok(_) => panic!("future storage schema must fail closed"),
        };
        assert!(matches!(
            error,
            StorageError::UnsupportedSchemaVersion { .. }
        ));
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

    #[test]
    fn recent_history_paginates_newest_first_without_overlaps_or_earliest_loss() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("db.sqlite3")).unwrap();
        let mut project = store.create_named_project("History baseline").unwrap();
        let unrelated = store.create_named_project("Another project").unwrap();

        // Deliberately exceed the Studio's former 100-row oldest-first ceiling.
        for revision in 1..=260 {
            let outcome = store
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("history-request-{revision}"),
                    &Change::RenameProject {
                        title: format!("Revision {revision}"),
                    },
                )
                .unwrap();
            project = outcome.project;
        }
        assert_eq!(project.revision, 260);
        let recent = store
            .event_records_through(project.id, project.revision, 101)
            .unwrap();
        assert_eq!(recent.len(), 101);
        assert_eq!(recent.first().unwrap().revision, 260);
        assert_eq!(recent.last().unwrap().revision, 160);

        let middle = store.event_records_through(project.id, 159, 101).unwrap();
        assert_eq!(middle.len(), 101);
        assert_eq!(middle.first().unwrap().revision, 159);
        assert_eq!(middle.last().unwrap().revision, 59);

        let oldest = store.event_records_through(project.id, 58, 101).unwrap();
        assert_eq!(oldest.len(), 58);
        assert_eq!(oldest.first().unwrap().revision, 58);
        assert_eq!(oldest.last().unwrap().revision, 1);

        let all = recent
            .into_iter()
            .chain(middle)
            .chain(oldest)
            .map(|entry| entry.revision)
            .collect::<Vec<_>>();
        assert_eq!(all, (1..=260).rev().collect::<Vec<_>>());
        assert!(
            store
                .event_records_through(project.id, 0, 100)
                .unwrap()
                .is_empty()
        );
        assert!(
            store
                .event_records_through(unrelated.id, u64::MAX, 100)
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            store
                .event_records_through(project.id, u64::MAX, 500)
                .unwrap()
                .len(),
            256
        );

        // Existing forward/oldest API remains unchanged for callers.
        let forward = store.event_records_since(project.id, 0, 2).unwrap();
        assert_eq!(
            forward
                .iter()
                .map(|entry| entry.revision)
                .collect::<Vec<_>>(),
            vec![1, 2]
        );
    }
}

#[cfg(all(test, any(target_os = "linux", target_os = "macos", windows)))]
mod verified_blob_capture_budget_tests {
    use super::*;
    use std::{cell::Cell, rc::Rc};

    struct CaptureHookGuard;
    impl Drop for CaptureHookGuard {
        fn drop(&mut self) {
            let unused = BEFORE_VERIFIED_BLOB_CAPTURE.with(|slot| slot.borrow_mut().take());
            drop(unused);
        }
    }

    fn before_capture(hook: impl FnOnce(&Path) + 'static) -> CaptureHookGuard {
        BEFORE_VERIFIED_BLOB_CAPTURE.with(|slot| {
            let mut slot = slot.borrow_mut();
            assert!(
                slot.is_none(),
                "a verified-blob capture hook is already installed"
            );
            *slot = Some(Box::new(hook));
        });
        CaptureHookGuard
    }

    #[test]
    fn verified_blob_capture_keeps_empty_exact_and_oversize_controls() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().join("store.sqlite3")).unwrap();
        for source_bytes in [b"".as_slice(), b"abcd".as_slice()] {
            let source = temp
                .path()
                .join(format!("source-{}.bin", source_bytes.len()));
            fs::write(&source, source_bytes).unwrap();
            let descriptor = store.ingest_blob_file(&source).unwrap();
            let limit = source_bytes.len() as u64;
            assert_eq!(
                store.read_blob(&descriptor.sha256, limit).unwrap(),
                source_bytes
            );
            if limit != 0 {
                assert!(matches!(
                    store.read_blob(&descriptor.sha256, limit - 1),
                    Err(StorageError::BlobTooLarge { size: 4, limit: 3 })
                ));
            }
            assert_eq!(fs::read(&source).unwrap(), source_bytes);
        }
    }

    #[test]
    fn verified_blob_capture_rejects_growth_after_metadata_at_limit_plus_one() {
        const LIMIT: u64 = 4;
        const GROWN: &[u8] = b"abcdefgh";
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().join("store.sqlite3")).unwrap();
        let source = temp.path().join("source.bin");
        fs::write(&source, GROWN).unwrap();
        let descriptor = store.ingest_blob_file(&source).unwrap();
        let cas_path = store.verified_blob_path(&descriptor.sha256).unwrap();

        assert_eq!(descriptor.sha256, hex::encode(Sha256::digest(GROWN)));
        assert_eq!(
            store
                .read_blob(&descriptor.sha256, GROWN.len() as u64)
                .unwrap(),
            GROWN
        );

        // Preserve the same regular file; only shorten its bytes before admission.
        OpenOptions::new()
            .write(true)
            .open(&cas_path)
            .unwrap()
            .set_len(LIMIT)
            .unwrap();
        assert_eq!(fs::metadata(&cas_path).unwrap().len(), LIMIT);
        assert_eq!(fs::read(&cas_path).unwrap(), &GROWN[..LIMIT as usize]);

        let calls = Rc::new(Cell::new(0_usize));
        let hook_calls = Rc::clone(&calls);
        let expected_path = cas_path.clone();
        let _hook = before_capture(move |path| {
            assert_eq!(path, expected_path.as_path());
            assert_eq!(fs::metadata(path).unwrap().len(), LIMIT);
            hook_calls.set(hook_calls.get() + 1);
            let mut append = OpenOptions::new().append(true).open(path).unwrap();
            append.write_all(&GROWN[LIMIT as usize..]).unwrap();
            append.flush().unwrap();
            assert_eq!(append.metadata().unwrap().len(), GROWN.len() as u64);
        });

        let result = store.read_blob(&descriptor.sha256, LIMIT);
        assert_eq!(
            calls.get(),
            1,
            "growth must occur exactly at the capture boundary"
        );
        BEFORE_VERIFIED_BLOB_CAPTURE.with(|slot| {
            assert!(
                slot.borrow().is_none(),
                "the one-shot hook must be consumed"
            );
        });
        assert_eq!(fs::read(&cas_path).unwrap(), GROWN);
        assert_eq!(fs::read(&source).unwrap(), GROWN);

        match result {
            Err(StorageError::BlobTooLarge {
                size,
                limit: reported_limit,
            }) => {
                assert_eq!(
                    (size, reported_limit),
                    (LIMIT + 1, LIMIT),
                    "report bytes actually observed by the bounded capture"
                );
            }
            Ok(bytes) => {
                assert_eq!(bytes, GROWN, "baseline witness must be the grown object");
                assert_eq!(hex::encode(Sha256::digest(&bytes)), descriptor.sha256);
                panic!(
                    "verified blob capture accepted eight bytes after admitting a four-byte source with limit four"
                );
            }
            Err(error) => {
                panic!("expected streaming BlobTooLarge {{ size: 5, limit: 4 }}, got {error:?}");
            }
        }
    }
}
