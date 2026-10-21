//! Versioned consistency evidence for the existing atomic apply receipt.
//! A binding is neither an execution grant nor authentication of an external effect.
use super::{
    ApplyOutcome, Change, Connection, DomainError, OptionalExtension, Project, Result,
    RevisionStamp, StorageError, Store, TransactionBehavior, Uuid, params, request_digest,
    validate_apply_receipt, validate_digest,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const APPLY_REQUEST_ENCODING_V1: u32 = 1;
pub const MAX_APPLY_BINDING_BYTES: usize = 4 * 1024;
const FORMAT: &str = "motionwright.apply-binding/1";
const HASH_DOMAIN: &[u8] = b"motionwright.apply-binding/1\0";

/// Historical facts captured by the writer, never inferred from the current head.
/// Revisions remain exact decimal strings even above JavaScript's integer range.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerifiedApplyReceiptV1 {
    pub format: String,
    pub project_id: String,
    pub generation: String,
    pub resource: String,
    pub request_id: String,
    pub request_sha256: String,
    pub base_revision: String,
    pub base_branch: String,
    pub result_revision: String,
    pub result_branch: String,
    pub result_sha256: String,
    pub binding_sha256: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApplyReceiptInvalid {
    RequestMismatch,
    ReceiptIntegrity,
    UnsupportedContract,
}

/// Only KnownVerifiedV1 establishes a verified local commit observation.
/// All other states leave an uncertain mutation unresolved and never authorize retry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
pub enum ApplyReceiptObservation {
    KnownVerifiedV1 {
        binding: Box<VerifiedApplyReceiptV1>,
    },
    LegacyUnverified,
    NotFound,
    Invalid {
        reason: ApplyReceiptInvalid,
    },
    AuthorityMismatch,
}

pub(super) struct ApplyBase {
    project_id: Uuid,
    generation: Uuid,
    resource: String,
    revision: u64,
    branch: Uuid,
}

impl ApplyBase {
    pub(super) fn capture(project: &Project) -> Self {
        Self {
            project_id: project.id,
            generation: project.generation,
            resource: project.resource_key(),
            revision: project.revision,
            branch: project.active_branch,
        }
    }
}

pub(super) struct StoredApplyReceipt {
    pub(super) request_sha256: String,
    pub(super) result_json: String,
    pub(super) binding_version: i64,
    binding_bytes: Option<i64>,
    binding_json: Option<String>,
}

impl StoredApplyReceipt {
    pub(super) fn is_legacy_marker(&self) -> bool {
        self.binding_version == 0 && self.binding_bytes.is_none() && self.binding_json.is_none()
    }
}

pub(super) struct CheckedApplyReceipt {
    pub(super) outcome: ApplyOutcome,
    pub(super) change: Change,
    pub(super) binding: Option<VerifiedApplyReceiptV1>,
}

#[derive(Debug)]
pub(super) enum ReceiptCheckError {
    Invalid(ApplyReceiptInvalid),
    Storage(StorageError),
}

impl ReceiptCheckError {
    pub(super) fn into_storage_error(self) -> StorageError {
        match self {
            Self::Storage(error) => error,
            Self::Invalid(reason) => StorageError::InvalidApplyReceipt(
                match reason {
                    ApplyReceiptInvalid::RequestMismatch => "request payload differs",
                    ApplyReceiptInvalid::ReceiptIntegrity => {
                        "stored receipt binding is inconsistent"
                    }
                    ApplyReceiptInvalid::UnsupportedContract => {
                        "unsupported stored receipt contract"
                    }
                }
                .into(),
            ),
        }
    }

    pub(super) fn into_import_error(self) -> StorageError {
        match self.into_storage_error() {
            StorageError::InvalidApplyReceipt(reason) => {
                StorageError::InvalidFileImportReceipt(reason)
            }
            other => other,
        }
    }
}

type Check<T> = std::result::Result<T, ReceiptCheckError>;

fn integrity() -> ReceiptCheckError {
    ReceiptCheckError::Invalid(ApplyReceiptInvalid::ReceiptIntegrity)
}

fn check_digest(value: &str) -> Check<()> {
    validate_digest(value).map_err(|_| integrity())
}

fn canonical_revision(value: &str) -> Check<u64> {
    if value.is_empty()
        || !value.bytes().all(|byte| byte.is_ascii_digit())
        || (value.len() > 1 && value.starts_with('0'))
    {
        return Err(integrity());
    }
    value
        .parse::<u64>()
        .ok()
        .filter(|revision| i64::try_from(*revision).is_ok())
        .ok_or_else(integrity)
}

fn canonical_uuid(value: &str) -> Check<Uuid> {
    let id = Uuid::parse_str(value).map_err(|_| integrity())?;
    if id.is_nil() || id.to_string() != value {
        return Err(integrity());
    }
    Ok(id)
}

fn binding_digest(binding: &VerifiedApplyReceiptV1) -> Result<String> {
    let tuple = [
        binding.project_id.as_str(),
        binding.generation.as_str(),
        binding.resource.as_str(),
        binding.request_id.as_str(),
        binding.request_sha256.as_str(),
        binding.base_revision.as_str(),
        binding.base_branch.as_str(),
        binding.result_revision.as_str(),
        binding.result_branch.as_str(),
        binding.result_sha256.as_str(),
    ];
    Ok(hex::encode(
        Sha256::new()
            .chain_update(HASH_DOMAIN)
            .chain_update(serde_json::to_vec(&tuple)?)
            .finalize(),
    ))
}

/// Serialize the binding for the same INSERT that persists this exact result text.
pub(super) fn create_binding(
    base: &ApplyBase,
    request_id: &str,
    request_sha256: &str,
    outcome: &ApplyOutcome,
    result_json: &str,
) -> Result<String> {
    if base.project_id != outcome.project.id
        || base.generation != outcome.project.generation
        || base.resource != outcome.project.resource_key()
        || base.revision != outcome.previous_revision
        || base.revision.checked_add(1) != Some(outcome.project.revision)
        || outcome.request_id != request_id
        || outcome.replayed
        || i64::try_from(outcome.project.revision).is_err()
    {
        return Err(StorageError::InvalidApplyReceipt(
            "writer result differs from its captured authority".into(),
        ));
    }
    let mut binding = VerifiedApplyReceiptV1 {
        format: FORMAT.into(),
        project_id: base.project_id.to_string(),
        generation: base.generation.to_string(),
        resource: base.resource.clone(),
        request_id: request_id.into(),
        request_sha256: request_sha256.into(),
        base_revision: base.revision.to_string(),
        base_branch: base.branch.to_string(),
        result_revision: outcome.project.revision.to_string(),
        result_branch: outcome.project.active_branch.to_string(),
        result_sha256: hex::encode(Sha256::digest(result_json.as_bytes())),
        binding_sha256: String::new(),
    };
    binding.binding_sha256 = binding_digest(&binding)?;
    let json = serde_json::to_string(&binding)?;
    if json.len() > MAX_APPLY_BINDING_BYTES {
        return Err(StorageError::InvalidApplyReceipt(
            "writer binding exceeds its format budget".into(),
        ));
    }
    Ok(json)
}

/// Do not materialize an oversized binding; keep its presence/length so a V0 row
/// with an oversized non-NULL extension cannot accidentally downgrade to legacy.
pub(super) fn read_receipt(
    conn: &Connection,
    id: Uuid,
    request_id: &str,
) -> Check<Option<StoredApplyReceipt>> {
    conn.query_row(
        "SELECT request_sha256,result_json,apply_binding_version,
                    length(CAST(apply_binding_json AS BLOB)),
                    CASE WHEN length(CAST(apply_binding_json AS BLOB))<=?3
                         THEN apply_binding_json END
             FROM receipts WHERE project_id=?1 AND request_id=?2",
        params![id.to_string(), request_id, MAX_APPLY_BINDING_BYTES as i64],
        |row| {
            Ok(StoredApplyReceipt {
                request_sha256: row.get(0)?,
                result_json: row.get(1)?,
                binding_version: row.get(2)?,
                binding_bytes: row.get(3)?,
                binding_json: row.get(4)?,
            })
        },
    )
    .optional()
    .map_err(check_sql_error)
}

// Column conversion failures describe corrupt stored content. Actual SQLite
// busy, I/O and statement errors remain operational failures, never absence.
fn check_sql_error(error: rusqlite::Error) -> ReceiptCheckError {
    match error {
        rusqlite::Error::InvalidColumnType(..) | rusqlite::Error::FromSqlConversionFailure(..) => {
            integrity()
        }
        other => ReceiptCheckError::Storage(StorageError::Sql(other)),
    }
}

fn decode_binding(receipt: &StoredApplyReceipt) -> Check<Option<VerifiedApplyReceiptV1>> {
    match receipt.binding_version {
        0 if receipt.is_legacy_marker() => Ok(None),
        0 => Err(integrity()),
        1 => {
            if !receipt
                .binding_bytes
                .is_some_and(|bytes| bytes > 0 && bytes <= MAX_APPLY_BINDING_BYTES as i64)
            {
                return Err(integrity());
            }
            let json = receipt.binding_json.as_deref().ok_or_else(integrity)?;
            // Typed serde decoding rejects duplicate and unknown fields.
            let binding: VerifiedApplyReceiptV1 =
                serde_json::from_str(json).map_err(|_| integrity())?;
            if binding.format != FORMAT {
                return Err(ReceiptCheckError::Invalid(
                    ApplyReceiptInvalid::UnsupportedContract,
                ));
            }
            Ok(Some(binding))
        }
        _ => Err(ReceiptCheckError::Invalid(
            ApplyReceiptInvalid::UnsupportedContract,
        )),
    }
}

/// Shared by both generic result readers. The exact event and raw result bytes
/// are verified before a caller marks an outcome replayed or checks import intent.
pub(super) fn check_receipt(
    conn: &Connection,
    current: &Project,
    request_id: &str,
    receipt: &StoredApplyReceipt,
) -> Check<CheckedApplyReceipt> {
    check_digest(&receipt.request_sha256)?;
    let binding = decode_binding(receipt)?;
    if let Some(binding) = &binding {
        check_digest(&binding.result_sha256)?;
        if hex::encode(Sha256::digest(receipt.result_json.as_bytes())) != binding.result_sha256 {
            return Err(integrity());
        }
    }
    let outcome: ApplyOutcome =
        serde_json::from_str(&receipt.result_json).map_err(|_| integrity())?;
    let change =
        validate_apply_receipt(conn, current, request_id, &receipt.request_sha256, &outcome)
            .map_err(|error| match error {
                StorageError::InvalidApplyReceipt(_) => integrity(),
                StorageError::Sql(error) => check_sql_error(error),
                other => ReceiptCheckError::Storage(other),
            })?;

    if let Some(binding) = &binding {
        canonical_uuid(&binding.project_id)?;
        canonical_uuid(&binding.generation)?;
        canonical_uuid(&binding.base_branch)?;
        canonical_uuid(&binding.result_branch)?;
        check_digest(&binding.request_sha256)?;
        check_digest(&binding.binding_sha256)?;
        let base_revision = canonical_revision(&binding.base_revision)?;
        let result_revision = canonical_revision(&binding.result_revision)?;
        if binding.project_id != current.id.to_string()
            || binding.generation != current.generation.to_string()
            || binding.resource != current.resource_key()
            || binding.request_id != request_id
            || binding.request_sha256 != receipt.request_sha256
            || base_revision != outcome.previous_revision
            || result_revision != outcome.project.revision
            || base_revision.checked_add(1) != Some(result_revision)
            || binding.result_branch != outcome.project.active_branch.to_string()
            || binding_digest(binding).map_err(ReceiptCheckError::Storage)?
                != binding.binding_sha256
        {
            return Err(integrity());
        }
    }
    Ok(CheckedApplyReceipt {
        outcome,
        change,
        binding,
    })
}

fn observe_on_snapshot(
    conn: &Connection,
    id: Uuid,
    original: &RevisionStamp,
    request_id: &str,
    request_sha256: &str,
) -> Result<ApplyReceiptObservation> {
    let current = match super::load_project_from_row(conn, id) {
        Ok(project) => project,
        Err(StorageError::NotFound) => return Ok(ApplyReceiptObservation::AuthorityMismatch),
        Err(StorageError::Serde(_) | StorageError::Domain(_)) => {
            return Ok(ApplyReceiptObservation::Invalid {
                reason: ApplyReceiptInvalid::ReceiptIntegrity,
            });
        }
        Err(StorageError::Sql(error)) => match check_sql_error(error) {
            ReceiptCheckError::Invalid(reason) => {
                return Ok(ApplyReceiptObservation::Invalid { reason });
            }
            ReceiptCheckError::Storage(error) => return Err(error),
        },
        Err(error) => return Err(error),
    };
    if current.resource_key() != original.resource || current.generation != original.generation {
        return Ok(ApplyReceiptObservation::AuthorityMismatch);
    }
    let receipt = match read_receipt(conn, id, request_id) {
        Ok(Some(receipt)) => receipt,
        Ok(None) => return Ok(ApplyReceiptObservation::NotFound),
        Err(ReceiptCheckError::Invalid(reason)) => {
            return Ok(ApplyReceiptObservation::Invalid { reason });
        }
        Err(ReceiptCheckError::Storage(error)) => return Err(error),
    };
    if receipt.request_sha256 != request_sha256 {
        return Ok(ApplyReceiptObservation::Invalid {
            reason: ApplyReceiptInvalid::RequestMismatch,
        });
    }
    match check_receipt(conn, &current, request_id, &receipt) {
        Ok(checked) => Ok(match checked.binding {
            Some(binding) => ApplyReceiptObservation::KnownVerifiedV1 {
                binding: Box::new(binding),
            },
            None => ApplyReceiptObservation::LegacyUnverified,
        }),
        Err(ReceiptCheckError::Invalid(reason)) => Ok(ApplyReceiptObservation::Invalid { reason }),
        Err(ReceiptCheckError::Storage(error)) => Err(error),
    }
}

impl Store {
    /// Observe an existing local receipt on one read snapshot of an admitted Store.
    ///
    /// This does not open a Store, migrate, drain caches, consume a grant, call a
    /// provider, read CAS/source bytes or call apply. A missing/legacy/invalid
    /// receipt is never permission to retry an UNKNOWN mutation. Busy/IO/SQLite
    /// failures remain errors, not absence or proof of a failed mutation.
    ///
    /// Response and binding are bounded, but strict Project/result validation
    /// still costs O(document). No wall-time or installed transport claim is made.
    pub fn observe_apply_receipt_v1(
        &mut self,
        id: Uuid,
        original: &RevisionStamp,
        request_id: &str,
        change: &Change,
        request_encoding_version: u32,
    ) -> Result<ApplyReceiptObservation> {
        if request_encoding_version != APPLY_REQUEST_ENCODING_V1 {
            return Ok(ApplyReceiptObservation::Invalid {
                reason: ApplyReceiptInvalid::UnsupportedContract,
            });
        }
        if request_id.is_empty()
            || request_id.len() > 256
            || request_id.chars().any(char::is_control)
        {
            return Err(DomainError::Invalid("invalid request id".into()).into());
        }
        let request_sha = request_digest(id, change)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)?;
        let observation = observe_on_snapshot(&tx, id, original, request_id, &request_sha)?;
        tx.commit()?;
        Ok(observation)
    }
}

// The original common layout checks run first. These two names are reserved
// only from schema 4; older extension data must never be adopted as evidence.
const BINDING_SCHEMA_VERSION: u32 = 4;
const VERSION_COLUMN: &str = "apply_binding_version";
const JSON_COLUMN: &str = "apply_binding_json";
const MAX_DDL_BYTES: usize = 256 * 1024;
const MAX_DDL_TOKENS: usize = 32 * 1024;
const MAX_PAREN_DEPTH: usize = 64;
const INVALID_LAYOUT: &str = "invalid schema 4 apply receipt binding layout";

fn layout_error(message: &'static str) -> StorageError {
    StorageError::InvalidBackup(message.into())
}

struct ColumnShape {
    declared_type: String,
    not_null: i64,
    default_value: Option<String>,
    primary_key: i64,
    hidden: i64,
}

impl ColumnShape {
    fn read(row: &rusqlite::Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            declared_type: row.get(2)?,
            not_null: row.get(3)?,
            default_value: row.get(4)?,
            primary_key: row.get(5)?,
            hidden: row.get(6)?,
        })
    }
}

/// Read-only, before WAL and again under the existing schema writer lock.
pub(super) fn validate_schema(conn: &Connection, version: u32) -> Result<()> {
    if version > BINDING_SCHEMA_VERSION {
        return Err(layout_error("unsupported apply binding schema version"));
    }
    let ddl = if version == BINDING_SCHEMA_VERSION {
        Some(read_receipts_ddl(conn)?)
    } else {
        None
    };
    let mut version_shape = None;
    let mut json_shape = None;
    let mut statement = conn.prepare("PRAGMA main.table_xinfo('receipts')")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let name: String = row.get(1)?;
        let slot = if name.eq_ignore_ascii_case(VERSION_COLUMN) {
            &mut version_shape
        } else if name.eq_ignore_ascii_case(JSON_COLUMN) {
            &mut json_shape
        } else {
            continue;
        };
        if version < BINDING_SCHEMA_VERSION {
            return Err(layout_error(
                "reserved apply binding columns occur before schema 4",
            ));
        }
        if slot.is_some() {
            return Err(layout_error(INVALID_LAYOUT));
        }
        *slot = Some(ColumnShape::read(row)?);
    }
    if version < BINDING_SCHEMA_VERSION {
        return Ok(());
    }
    let marker = version_shape.ok_or_else(|| layout_error(INVALID_LAYOUT))?;
    let json = json_shape.ok_or_else(|| layout_error(INVALID_LAYOUT))?;
    let marker_valid = marker.declared_type.trim().eq_ignore_ascii_case("INTEGER")
        && marker.not_null == 1
        && marker.primary_key == 0
        && marker.hidden == 0
        && default_matches(marker.default_value.as_deref(), "0", false)?;
    let json_valid = json.declared_type.trim().eq_ignore_ascii_case("TEXT")
        && json.not_null == 0
        && json.primary_key == 0
        && json.hidden == 0
        && default_matches(json.default_value.as_deref(), "NULL", true)?;
    if !marker_valid || !json_valid {
        return Err(layout_error(INVALID_LAYOUT));
    }
    validate_version_check(&ddl.ok_or_else(|| layout_error(INVALID_LAYOUT))?)
}

/// Called only after the original tables exist, inside Store::open's existing
/// IMMEDIATE transaction. The caller stamps user_version after every DDL succeeds.
pub(super) fn migrate_schema(tx: &rusqlite::Transaction<'_>, version: u32) -> Result<()> {
    validate_schema(tx, version)?;
    if version == BINDING_SCHEMA_VERSION {
        return Ok(());
    }
    tx.execute(
        "ALTER TABLE receipts ADD COLUMN apply_binding_version INTEGER NOT NULL DEFAULT 0
         CHECK (apply_binding_version IN (0, 1))",
        [],
    )?;
    #[cfg(test)]
    migration_checkpoint("after_version_column");
    tx.execute(
        "ALTER TABLE receipts ADD COLUMN apply_binding_json TEXT",
        [],
    )?;
    #[cfg(test)]
    migration_checkpoint("after_json_column");
    validate_schema(tx, BINDING_SCHEMA_VERSION)
}

fn read_receipts_ddl(conn: &Connection) -> Result<String> {
    let ddl: Option<Option<String>> = conn
        .query_row(
            "SELECT CASE WHEN sql IS NOT NULL AND length(CAST(sql AS BLOB))<=?1
                         THEN sql ELSE NULL END
             FROM main.sqlite_schema
             WHERE type='table' AND name='receipts' COLLATE NOCASE",
            [MAX_DDL_BYTES as i64],
            |row| row.get(0),
        )
        .optional()?;
    ddl.flatten().ok_or_else(|| layout_error(INVALID_LAYOUT))
}

fn default_matches(value: Option<&str>, expected: &str, missing_allowed: bool) -> Result<bool> {
    let Some(value) = value else {
        return Ok(missing_allowed);
    };
    let tokens = lex_schema(value)?;
    let Some(tokens) = strip_outer_parens(&tokens) else {
        return Ok(false);
    };
    Ok(tokens.len() == 1 && tokens[0].bare_eq(expected))
}

#[derive(Clone, Copy)]
enum SchemaToken<'a> {
    Bare(&'a str),
    Identifier(&'a str),
    StringLiteral,
    Symbol(u8),
}

impl SchemaToken<'_> {
    fn bare_eq(self, expected: &str) -> bool {
        matches!(self, Self::Bare(value) if value.eq_ignore_ascii_case(expected))
    }
    fn identifier_eq(self, expected: &str) -> bool {
        match self {
            Self::Bare(value) | Self::Identifier(value) => value.eq_ignore_ascii_case(expected),
            _ => false,
        }
    }
    fn symbol(self, value: u8) -> bool {
        matches!(self, Self::Symbol(observed) if observed == value)
    }
}

fn is_schema_word(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'$' || byte >= 0x80
}

/// Bounded lexical recognition of the required column CHECK, not a SQL evaluator.
/// Strings/comments cannot supply tokens that falsely assert the constraint.
fn lex_schema(sql: &str) -> Result<Vec<SchemaToken<'_>>> {
    if sql.len() > MAX_DDL_BYTES {
        return Err(layout_error(INVALID_LAYOUT));
    }
    let bytes = sql.as_bytes();
    let mut index = 0;
    let mut tokens = Vec::new();
    while index < bytes.len() {
        let byte = bytes[index];
        if byte.is_ascii_whitespace() {
            index += 1;
            continue;
        }
        if byte == b'-' && bytes.get(index + 1) == Some(&b'-') {
            index += 2;
            while index < bytes.len() && bytes[index] != b'\n' {
                index += 1;
            }
            continue;
        }
        if byte == b'/' && bytes.get(index + 1) == Some(&b'*') {
            index += 2;
            while index + 1 < bytes.len() && !(bytes[index] == b'*' && bytes[index + 1] == b'/') {
                index += 1;
            }
            index = if index + 1 < bytes.len() {
                index + 2
            } else {
                bytes.len()
            };
            continue;
        }
        let token = if matches!(byte, b'\'' | b'"' | b'\x60' | b'[') {
            let closing = if byte == b'[' { b']' } else { byte };
            let start = index + 1;
            index += 1;
            let end = loop {
                if index >= bytes.len() {
                    return Err(layout_error(INVALID_LAYOUT));
                }
                if bytes[index] == closing {
                    if byte != b'[' && bytes.get(index + 1) == Some(&closing) {
                        index += 2;
                        continue;
                    }
                    let end = index;
                    index += 1;
                    break end;
                }
                index += 1;
            };
            if byte == b'\'' {
                SchemaToken::StringLiteral
            } else {
                SchemaToken::Identifier(&sql[start..end])
            }
        } else if is_schema_word(byte) {
            let start = index;
            index += 1;
            while index < bytes.len() && is_schema_word(bytes[index]) {
                index += 1;
            }
            SchemaToken::Bare(&sql[start..index])
        } else {
            index += 1;
            SchemaToken::Symbol(byte)
        };
        if tokens.len() == MAX_DDL_TOKENS {
            return Err(layout_error(INVALID_LAYOUT));
        }
        tokens.push(token);
    }
    Ok(tokens)
}

fn matching_paren(tokens: &[SchemaToken<'_>], opening: usize) -> Option<usize> {
    if !tokens.get(opening)?.symbol(b'(') {
        return None;
    }
    let mut depth = 0_usize;
    for (index, token) in tokens.iter().enumerate().skip(opening) {
        if token.symbol(b'(') {
            depth += 1;
            if depth > MAX_PAREN_DEPTH {
                return None;
            }
        } else if token.symbol(b')') {
            depth = depth.checked_sub(1)?;
            if depth == 0 {
                return Some(index);
            }
        }
    }
    None
}

fn strip_outer_parens<'t, 's>(mut tokens: &'t [SchemaToken<'s>]) -> Option<&'t [SchemaToken<'s>]> {
    while tokens.first().is_some_and(|token| token.symbol(b'(')) {
        let closing = matching_paren(tokens, 0)?;
        if closing != tokens.len() - 1 {
            break;
        }
        tokens = &tokens[1..closing];
    }
    Some(tokens)
}

fn take_schema_word(tokens: &[SchemaToken<'_>], position: &mut usize, word: &str) -> bool {
    if tokens
        .get(*position)
        .is_some_and(|token| token.bare_eq(word))
    {
        *position += 1;
        true
    } else {
        false
    }
}

fn receipts_body(tokens: &[SchemaToken<'_>]) -> Result<(usize, usize)> {
    let mut position = 0;
    if !take_schema_word(tokens, &mut position, "CREATE") {
        return Err(layout_error(INVALID_LAYOUT));
    }
    if !take_schema_word(tokens, &mut position, "TEMP") {
        take_schema_word(tokens, &mut position, "TEMPORARY");
    }
    if !take_schema_word(tokens, &mut position, "TABLE") {
        return Err(layout_error(INVALID_LAYOUT));
    }
    if take_schema_word(tokens, &mut position, "IF")
        && !(take_schema_word(tokens, &mut position, "NOT")
            && take_schema_word(tokens, &mut position, "EXISTS"))
    {
        return Err(layout_error(INVALID_LAYOUT));
    }
    if tokens
        .get(position + 1)
        .is_some_and(|token| token.symbol(b'.'))
    {
        if !tokens
            .get(position)
            .is_some_and(|token| token.identifier_eq("main"))
        {
            return Err(layout_error(INVALID_LAYOUT));
        }
        position += 2;
    }
    if !tokens
        .get(position)
        .is_some_and(|token| token.identifier_eq("receipts"))
    {
        return Err(layout_error(INVALID_LAYOUT));
    }
    position += 1;
    let closing = matching_paren(tokens, position).ok_or_else(|| layout_error(INVALID_LAYOUT))?;
    Ok((position + 1, closing))
}

fn validate_version_check(sql: &str) -> Result<()> {
    let tokens = lex_schema(sql)?;
    let (start, end) = receipts_body(&tokens)?;
    let mut definition_start = start;
    let mut depth = 0_usize;
    let mut version_definition = None;
    for index in start..=end {
        if index == end || (depth == 0 && tokens[index].symbol(b',')) {
            let definition = &tokens[definition_start..index];
            if definition.is_empty() {
                return Err(layout_error(INVALID_LAYOUT));
            }
            if definition[0].identifier_eq(VERSION_COLUMN) {
                if version_definition.is_some() {
                    return Err(layout_error(INVALID_LAYOUT));
                }
                version_definition = Some(definition);
            }
            definition_start = index + 1;
            continue;
        }
        if tokens[index].symbol(b'(') {
            depth += 1;
            if depth > MAX_PAREN_DEPTH {
                return Err(layout_error(INVALID_LAYOUT));
            }
        } else if tokens[index].symbol(b')') {
            depth = depth
                .checked_sub(1)
                .ok_or_else(|| layout_error(INVALID_LAYOUT))?;
        }
    }
    let definition = version_definition.ok_or_else(|| layout_error(INVALID_LAYOUT))?;
    if has_required_version_check(definition) {
        Ok(())
    } else {
        Err(layout_error(INVALID_LAYOUT))
    }
}

fn has_required_version_check(definition: &[SchemaToken<'_>]) -> bool {
    let mut index = 1;
    while index < definition.len() {
        if definition[index].bare_eq("CHECK")
            && definition
                .get(index + 1)
                .is_some_and(|token| token.symbol(b'('))
        {
            let Some(end) = matching_paren(definition, index + 1) else {
                return false;
            };
            if is_required_version_expression(&definition[index + 2..end]) {
                return true;
            }
            index = end + 1;
        } else if definition[index].symbol(b'(') {
            let Some(end) = matching_paren(definition, index) else {
                return false;
            };
            index = end + 1;
        } else {
            index += 1;
        }
    }
    false
}

fn is_required_version_expression(tokens: &[SchemaToken<'_>]) -> bool {
    let Some(tokens) = strip_outer_parens(tokens) else {
        return false;
    };
    tokens.len() == 7
        && tokens[0].identifier_eq(VERSION_COLUMN)
        && tokens[1].bare_eq("IN")
        && tokens[2].symbol(b'(')
        && tokens[4].symbol(b',')
        && tokens[6].symbol(b')')
        && ((tokens[3].bare_eq("0") && tokens[5].bare_eq("1"))
            || (tokens[3].bare_eq("1") && tokens[5].bare_eq("0")))
}

#[cfg(test)]
fn migration_checkpoint(phase: &str) {
    if std::env::var("MOTIONWRIGHT_TEST_BINDING_MIGRATION_CHILD").as_deref() == Ok("1")
        && std::env::var("MOTIONWRIGHT_TEST_BINDING_MIGRATION_PHASE").as_deref() == Ok(phase)
    {
        use std::io::Write;
        println!("BINDING_MIGRATION_CHECKPOINT:{phase}");
        std::io::stdout().flush().unwrap();
        std::process::exit(86);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observation_succeeds_on_query_only_connection_without_sql_changes() {
        let dir = tempfile::tempdir().unwrap();
        let mut store = Store::open(dir.path().join("state.sqlite3")).unwrap();
        let original = store.create_named_project("Read-only receipt").unwrap();
        let change = Change::RenameProject {
            title: "Known commit".into(),
        };
        let committed = store
            .apply(original.id, &original.stamp(), "known", &change)
            .unwrap();
        store.conn.pragma_update(None, "query_only", true).unwrap();
        let before = store.conn.total_changes();
        assert!(matches!(
            store
                .observe_apply_receipt_v1(original.id, &original.stamp(), "known", &change, 1)
                .unwrap(),
            ApplyReceiptObservation::KnownVerifiedV1 { .. }
        ));
        assert_eq!(store.conn.total_changes(), before);
        let blocked = store.apply(original.id, &committed.project.stamp(), "new", &change);
        assert!(matches!(
            blocked,
            Err(StorageError::Sql(rusqlite::Error::SqliteFailure(error, _)))
                if error.code == rusqlite::ErrorCode::ReadOnly
        ));
        assert_eq!(store.conn.total_changes(), before);
    }

    #[test]
    fn observation_keeps_one_snapshot_during_real_peer_commit() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let original = store.create_named_project("Snapshot receipt").unwrap();
        let mut peer = Store::open(&path).unwrap();
        let change = Change::RenameProject {
            title: "Peer commit".into(),
        };
        let sha = request_digest(original.id, &change).unwrap();
        let tx = store
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)
            .unwrap();
        super::super::load_project_from_row(&tx, original.id).unwrap();
        peer.apply(original.id, &original.stamp(), "peer", &change)
            .unwrap();
        assert_eq!(
            observe_on_snapshot(&tx, original.id, &original.stamp(), "peer", &sha).unwrap(),
            ApplyReceiptObservation::NotFound
        );
        tx.commit().unwrap();
        assert!(matches!(
            store
                .observe_apply_receipt_v1(original.id, &original.stamp(), "peer", &change, 1)
                .unwrap(),
            ApplyReceiptObservation::KnownVerifiedV1 { .. }
        ));
    }

    #[test]
    fn resealed_noncanonical_decimal_is_still_invalid() {
        for revision in ["", "00", "+0", "-0", "9223372036854775808"] {
            let dir = tempfile::tempdir().unwrap();
            let mut store = Store::open(dir.path().join("decimal.sqlite3")).unwrap();
            let project = store.create_named_project("Canonical decimal").unwrap();
            let change = Change::RenameProject {
                title: "Committed decimal".into(),
            };
            store
                .apply(project.id, &project.stamp(), "decimal", &change)
                .unwrap();
            let receipt = read_receipt(&store.conn, project.id, "decimal")
                .unwrap()
                .unwrap();
            let mut binding = decode_binding(&receipt).unwrap().unwrap();
            binding.base_revision = revision.into();
            // Deliberately recompute the consistency checksum. This isolates
            // canonical decimal admission from a mere checksum mismatch.
            binding.binding_sha256 = binding_digest(&binding).unwrap();
            store.conn.execute(
                "UPDATE receipts SET apply_binding_json=?1 WHERE project_id=?2 AND request_id='decimal'",
                params![serde_json::to_string(&binding).unwrap(), project.id.to_string()],
            ).unwrap();
            store.conn.pragma_update(None, "query_only", true).unwrap();
            let before = store.conn.total_changes();
            assert_eq!(
                store
                    .observe_apply_receipt_v1(
                        project.id,
                        &project.stamp(),
                        "decimal",
                        &change,
                        APPLY_REQUEST_ENCODING_V1,
                    )
                    .unwrap(),
                ApplyReceiptObservation::Invalid {
                    reason: ApplyReceiptInvalid::ReceiptIntegrity
                },
            );
            assert_eq!(store.conn.total_changes(), before);
        }
    }

    #[test]
    fn sqlite_statement_failure_is_not_content_failure_or_absence() {
        let dir = tempfile::tempdir().unwrap();
        let mut store = Store::open(dir.path().join("statement.sqlite3")).unwrap();
        let project = store.create_named_project("Operational failure").unwrap();
        let change = Change::RenameProject {
            title: "Known operation".into(),
        };
        store
            .apply(project.id, &project.stamp(), "known", &change)
            .unwrap();
        // Valid DDL in an owned fixture deliberately breaks an admitted handle.
        // This is an actual engine statement failure, not a simulated I/O test.
        store.conn.execute_batch("DROP TABLE receipts").unwrap();
        store.conn.pragma_update(None, "query_only", true).unwrap();
        let before = store.conn.total_changes();
        let result = store.observe_apply_receipt_v1(
            project.id,
            &project.stamp(),
            "known",
            &change,
            APPLY_REQUEST_ENCODING_V1,
        );
        assert!(matches!(
            result,
            Err(StorageError::Sql(rusqlite::Error::SqliteFailure(..)))
        ));
        assert_eq!(store.conn.total_changes(), before);
    }

    fn check_layout_without_writes(sql: &str, accepted: bool) {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(sql).unwrap();
        let before: (u32, u32, String, u64) = (
            conn.pragma_query_value(None, "user_version", |row| row.get(0))
                .unwrap(),
            conn.pragma_query_value(None, "schema_version", |row| row.get(0))
                .unwrap(),
            conn.query_row(
                "SELECT sql FROM sqlite_schema WHERE name='receipts'",
                [],
                |row| row.get(0),
            )
            .unwrap(),
            conn.total_changes(),
        );
        conn.pragma_update(None, "query_only", true).unwrap();
        assert_eq!(validate_schema(&conn, 4).is_ok(), accepted);
        let after = (
            conn.pragma_query_value(None, "user_version", |row| row.get::<_, u32>(0))
                .unwrap(),
            conn.pragma_query_value(None, "schema_version", |row| row.get::<_, u32>(0))
                .unwrap(),
            conn.query_row(
                "SELECT sql FROM sqlite_schema WHERE name='receipts'",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap(),
            conn.total_changes(),
        );
        assert_eq!(after, before);
    }

    #[test]
    fn schema_constraint_tokens_cannot_be_supplied_by_literals_or_comments() {
        for (marker, extra) in [
            (
                "apply_binding_version INTEGER NOT NULL DEFAULT 0 /* CHECK (apply_binding_version IN (0,1)) */",
                "",
            ),
            (
                "apply_binding_version INTEGER NOT NULL DEFAULT 0",
                ", note TEXT DEFAULT 'CHECK (apply_binding_version IN (0,1))'",
            ),
            (
                "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK ('apply_binding_version IN (0,1)')",
                "",
            ),
            (
                "apply_binding_version INTEGER NOT NULL DEFAULT 0",
                ", note INTEGER CHECK (apply_binding_version IN (0,1))",
            ),
            (
                "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1) OR 1)",
                "",
            ),
            (
                "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1,NULL))",
                "",
            ),
        ] {
            check_layout_without_writes(
                &format!("CREATE TABLE receipts({marker},apply_binding_json TEXT{extra})"),
                false,
            );
        }
        check_layout_without_writes(
            "CREATE TABLE receipts(
                \"APPLY_BINDING_VERSION\" INTEGER NOT NULL DEFAULT (0)
                    CHECK ((\"APPLY_BINDING_VERSION\" /* real */ IN (0,1))),
                \"APPLY_BINDING_JSON\" TEXT DEFAULT NULL,
                note TEXT DEFAULT 'comma, parentheses() -- /* and ''quote''')",
            true,
        );
    }

    #[test]
    fn schema_inspection_bounds_ddl_tokens_and_nesting() {
        let marker = "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1))";
        let oversized = format!(
            "CREATE TABLE receipts({marker},apply_binding_json TEXT,note TEXT DEFAULT '{}')",
            "x".repeat(MAX_DDL_BYTES),
        );
        check_layout_without_writes(&oversized, false);
        let too_many_tokens = format!(
            "CREATE TABLE receipts({marker},apply_binding_json TEXT,note INTEGER CHECK(note IN ({})))",
            std::iter::repeat_n("0", MAX_DDL_TOKENS / 2 + 1)
                .collect::<Vec<_>>()
                .join(","),
        );
        assert!(too_many_tokens.len() < MAX_DDL_BYTES);
        check_layout_without_writes(&too_many_tokens, false);
        let nested = format!(
            "{}0{}",
            "(".repeat(MAX_PAREN_DEPTH + 1),
            ")".repeat(MAX_PAREN_DEPTH + 1)
        );
        let tokens = lex_schema(&nested).unwrap();
        assert!(matching_paren(&tokens, 0).is_none());
    }

    #[test]
    fn every_legacy_version_rejects_reserved_hidden_and_case_variant_columns() {
        for version in 0..4 {
            for definition in [
                "APPLY_BINDING_VERSION INTEGER",
                "APPLY_BINDING_JSON TEXT",
                "apply_binding_version INTEGER GENERATED ALWAYS AS (0) VIRTUAL",
                "apply_binding_json TEXT GENERATED ALWAYS AS ('{}') VIRTUAL",
            ] {
                let conn = Connection::open_in_memory().unwrap();
                conn.execute_batch(&format!(
                    "CREATE TABLE receipts(request_id TEXT,{definition})"
                ))
                .unwrap();
                conn.pragma_update(None, "query_only", true).unwrap();
                assert!(validate_schema(&conn, version).is_err());
                assert_eq!(conn.total_changes(), 0);
            }
        }
    }
}
