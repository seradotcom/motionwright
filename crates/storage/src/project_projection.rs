//! Read-only, revision-bound project projections. A page validates its own fields;
//! it is not admission of the entire project or authority to mutate it.
use crate::{StorageError, Store};
use motionwright_domain::{
    CanvasNode, DomainError, LEGACY_PROJECT_SCHEMA_VERSION, PROJECT_SCHEMA_VERSION, RationalTime,
    RendererKind, RevisionStamp, SceneStatus,
};
use rusqlite::{OptionalExtension, Row, Transaction, TransactionBehavior, params, types::ValueRef};
use serde::{Deserialize, Serialize};
use std::io::{self, Write};
use thiserror::Error;
use uuid::Uuid;

pub const MAX_SCENE_PAGE_ITEMS: u16 = 64;
pub const MAX_NODE_PAGE_ITEMS: u16 = 128;
pub const MAX_PROJECTION_RESPONSE_BYTES: usize = 1_048_576;
// SQLite BLOB byte counts use database encoding. UTF-16 needs at most twice
// the UTF-8 budget; borrowed UTF-8 bytes are checked before deserialization.
const MAX_SQL_FRAGMENT_BYTES: usize = MAX_PROJECTION_RESPONSE_BYTES * 2;

#[derive(Debug, Error)]
pub enum ProjectionError {
    #[error(transparent)]
    Storage(#[from] StorageError),
    #[error("invalid project projection request or content: {0}")]
    Invalid(String),
    #[error("project projection exceeds the {limit}-byte payload budget")]
    TooLarge { limit: usize },
}
pub type ProjectionResult<T> = std::result::Result<T, ProjectionError>;

impl From<rusqlite::Error> for ProjectionError {
    fn from(value: rusqlite::Error) -> Self {
        StorageError::from(value).into()
    }
}
impl From<serde_json::Error> for ProjectionError {
    fn from(value: serde_json::Error) -> Self {
        invalid(format!(
            "projected JSON does not match its schema (line {}, column {})",
            value.line(),
            value.column()
        ))
    }
}
impl From<DomainError> for ProjectionError {
    fn from(_value: DomainError) -> Self {
        invalid("selected canvas node violates its domain constraints")
    }
}
fn invalid(message: impl Into<String>) -> ProjectionError {
    ProjectionError::Invalid(message.into())
}
fn too_large() -> ProjectionError {
    ProjectionError::TooLarge {
        limit: MAX_PROJECTION_RESPONSE_BYTES,
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionIdentity {
    pub project_id: Uuid,
    pub generation: Uuid,
    /// Canonical decimal, safe to round-trip through JavaScript without rounding.
    pub revision: String,
}
impl ProjectionIdentity {
    pub fn checked_stamp(&self) -> ProjectionResult<RevisionStamp> {
        let revision = self
            .revision
            .parse::<u64>()
            .map_err(|_| invalid("revision must be canonical unsigned decimal"))?;
        if self.generation.is_nil() || revision.to_string() != self.revision {
            return Err(invalid("invalid projection identity"));
        }
        Ok(RevisionStamp {
            resource: format!("project:{}", self.project_id),
            generation: self.generation,
            revision,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneRef {
    pub ordinal: u32,
    pub id: Uuid,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScenePageCursor {
    pub identity: ProjectionIdentity,
    pub next_ordinal: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodePageCursor {
    pub identity: ProjectionIdentity,
    pub scene: SceneRef,
    pub next_ordinal: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProjectionHead {
    pub identity: ProjectionIdentity,
    pub schema_version: u32,
    pub title: String,
    pub scene_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneSummary {
    pub ordinal: u32,
    pub id: Uuid,
    pub name: String,
    pub start: RationalTime,
    pub duration: RationalTime,
    pub renderer: RendererKind,
    pub status: SceneStatus,
    pub beat_count: u32,
    pub node_count: u32,
}
impl SceneSummary {
    pub fn scene_ref(&self) -> SceneRef {
        SceneRef {
            ordinal: self.ordinal,
            id: self.id,
        }
    }
    fn validate(&self) -> ProjectionResult<()> {
        if self.name.trim().is_empty()
            || self.name.len() > 160
            || self.start.validate().is_err()
            || self.start < RationalTime::ZERO
            || self.duration.validate().is_err()
            || self.duration.num <= 0
        {
            return Err(invalid("invalid scene summary"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ScenePage {
    pub identity: ProjectionIdentity,
    pub total_scenes: u32,
    pub start_ordinal: u32,
    pub items: Vec<SceneSummary>,
    pub next_cursor: Option<ScenePageCursor>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NodePage {
    pub identity: ProjectionIdentity,
    pub scene: SceneRef,
    pub total_nodes: u32,
    pub start_ordinal: u32,
    pub items: Vec<CanvasNode>,
    pub next_cursor: Option<NodePageCursor>,
}

impl Store {
    pub fn project_projection_head(
        &mut self,
        project_id: Uuid,
    ) -> ProjectionResult<ProjectionHead> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)?;
        let head = read_head(&tx, project_id)?;
        enforce_response_budget(&head)?;
        tx.commit()?;
        Ok(head)
    }

    pub fn project_scene_page(
        &mut self,
        identity: &ProjectionIdentity,
        cursor: Option<&ScenePageCursor>,
        limit: u16,
    ) -> ProjectionResult<ScenePage> {
        identity.checked_stamp()?;
        let start = match cursor {
            Some(cursor) if &cursor.identity == identity => cursor.next_ordinal,
            Some(_) => return Err(invalid("scene cursor belongs to another snapshot")),
            None => 0,
        };
        validate_limit(limit, MAX_SCENE_PAGE_ITEMS)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)?;
        let head = read_head(&tx, identity.project_id)?;
        require_identity(identity, &head.identity)?;
        let end = checked_end(start, limit, head.scene_count)?;
        let items = read_scene_summaries(&tx, identity.project_id, start, end)?;
        require_contiguous_count(items.len(), start, end)?;
        let next_cursor = (end < head.scene_count).then(|| ScenePageCursor {
            identity: head.identity.clone(),
            next_ordinal: end,
        });
        let page = ScenePage {
            identity: head.identity,
            total_scenes: head.scene_count,
            start_ordinal: start,
            items,
            next_cursor,
        };
        enforce_response_budget(&page)?;
        tx.commit()?;
        Ok(page)
    }

    pub fn project_scene_nodes(
        &mut self,
        identity: &ProjectionIdentity,
        scene: &SceneRef,
        cursor: Option<&NodePageCursor>,
        limit: u16,
    ) -> ProjectionResult<NodePage> {
        identity.checked_stamp()?;
        let start = match cursor {
            Some(cursor) if &cursor.identity == identity && &cursor.scene == scene => {
                cursor.next_ordinal
            }
            Some(_) => return Err(invalid("node cursor belongs to another snapshot or scene")),
            None => 0,
        };
        validate_limit(limit, MAX_NODE_PAGE_ITEMS)?;
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Deferred)?;
        let head = read_head(&tx, identity.project_id)?;
        require_identity(identity, &head.identity)?;
        if scene.ordinal >= head.scene_count {
            return Err(invalid("scene ordinal is out of range"));
        }
        let scene_end = scene
            .ordinal
            .checked_add(1)
            .ok_or_else(|| invalid("scene ordinal overflow"))?;
        let mut summaries =
            read_scene_summaries(&tx, identity.project_id, scene.ordinal, scene_end)?;
        let summary = summaries.pop().ok_or_else(|| invalid("scene is missing"))?;
        if summary.id != scene.id {
            return Err(invalid("scene identity differs at the requested ordinal"));
        }
        let end = checked_end(start, limit, summary.node_count)?;
        let items = read_nodes(
            &tx,
            identity.project_id,
            scene.ordinal,
            start,
            end,
            summary.duration,
        )?;
        require_contiguous_count(items.len(), start, end)?;
        let next_cursor = (end < summary.node_count).then(|| NodePageCursor {
            identity: head.identity.clone(),
            scene: scene.clone(),
            next_ordinal: end,
        });
        let page = NodePage {
            identity: head.identity,
            scene: scene.clone(),
            total_nodes: summary.node_count,
            start_ordinal: start,
            items,
            next_cursor,
        };
        enforce_response_budget(&page)?;
        tx.commit()?;
        Ok(page)
    }
}

fn validate_limit(limit: u16, max: u16) -> ProjectionResult<()> {
    if limit == 0 || limit > max {
        return Err(invalid("page item limit is out of range"));
    }
    Ok(())
}
fn checked_end(start: u32, limit: u16, total: u32) -> ProjectionResult<u32> {
    if start > total {
        return Err(invalid("page ordinal is out of range"));
    }
    Ok(start
        .checked_add(u32::from(limit))
        .ok_or_else(|| invalid("page ordinal overflow"))?
        .min(total))
}
fn require_contiguous_count(count: usize, start: u32, end: u32) -> ProjectionResult<()> {
    if u32::try_from(count).ok() != end.checked_sub(start) {
        return Err(invalid("projection page omitted an element"));
    }
    Ok(())
}
fn require_identity(
    expected: &ProjectionIdentity,
    actual: &ProjectionIdentity,
) -> ProjectionResult<()> {
    let expected_stamp = expected.checked_stamp()?;
    let actual_stamp = actual.checked_stamp()?;
    if expected.project_id != actual.project_id {
        return Err(invalid("projection project differs"));
    }
    if expected_stamp.generation != actual_stamp.generation {
        return Err(StorageError::GenerationConflict.into());
    }
    if expected_stamp.revision != actual_stamp.revision {
        return Err(StorageError::Conflict {
            expected: expected_stamp.revision,
            actual: actual_stamp.revision,
        }
        .into());
    }
    Ok(())
}

fn text<'a>(row: &'a Row<'_>, index: usize, max: usize) -> ProjectionResult<&'a str> {
    match row.get_ref(index)? {
        ValueRef::Text(bytes) if bytes.len() <= max => {
            std::str::from_utf8(bytes).map_err(|_| invalid("projection contains invalid UTF-8"))
        }
        ValueRef::Text(_) if max == MAX_PROJECTION_RESPONSE_BYTES => Err(too_large()),
        ValueRef::Text(_) => Err(invalid("projection field exceeds its domain byte limit")),
        _ => Err(invalid("projection field has the wrong storage type")),
    }
}
fn add_input_bytes(total: &mut usize, bytes: usize) -> ProjectionResult<()> {
    *total = total.checked_add(bytes).ok_or_else(too_large)?;
    if *total > MAX_PROJECTION_RESPONSE_BYTES {
        return Err(too_large());
    }
    Ok(())
}
struct ResponseCounter {
    bytes: usize,
    exceeded: bool,
}
impl Write for ResponseCounter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        match self.bytes.checked_add(bytes.len()) {
            Some(size) if size <= MAX_PROJECTION_RESPONSE_BYTES => {
                self.bytes = size;
                Ok(bytes.len())
            }
            _ => {
                self.exceeded = true;
                Err(io::Error::other("projection response budget exceeded"))
            }
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn enforce_response_budget(value: &impl Serialize) -> ProjectionResult<()> {
    let mut counter = ResponseCounter {
        bytes: 0,
        exceeded: false,
    };
    let result = serde_json::to_writer(&mut counter, value);
    if counter.exceeded {
        return Err(too_large());
    }
    result?;
    Ok(())
}

// SQLite PATH labels can compare only the prefix before NUL. Some versions
// also truncate KEY; FULLKEY retains the escaped label for consulted fields.
// This guard does not ban NUL inside a legitimate text value.
const ROOT_STATUS_SQL: &str = r#"
SELECT CASE
    WHEN typeof(document_json) <> 'text' THEN 'document is not text'
    WHEN json_valid(document_json) <> 1 THEN 'malformed project JSON'
    WHEN json_type(document_json) <> 'object' THEN 'project is not an object'
    WHEN EXISTS (
        SELECT 1 FROM json_each(document_json)
        WHERE instr(key, char(0)) > 0
           OR (key IN ('id','generation','revision','schema_version','title','scenes')
               AND instr(fullkey, '\u0000') > 0)
    ) THEN 'ambiguous project key'
    WHEN EXISTS (
        SELECT 1 FROM json_each(document_json)
        WHERE key IN ('id','generation','revision','schema_version','title','scenes')
        GROUP BY key COLLATE BINARY HAVING count(*) > 1
    ) THEN 'duplicate projection authority key'
    ELSE 'ok'
END FROM projects WHERE id = ?1
"#;

const HEAD_SQL: &str = r#"
SELECT id, generation, revision, title,
    json_type(document_json,'$.schema_version'),
    typeof(json_extract(document_json,'$.schema_version')),
    json_extract(document_json,'$.schema_version'),
    json_extract(document_json,'$.id'),
    json_extract(document_json,'$.generation'),
    json_type(document_json,'$.revision'),
    typeof(json_extract(document_json,'$.revision')),
    json_extract(document_json,'$.revision'),
    json_extract(document_json,'$.title'),
    json_type(document_json,'$.scenes'),
    json_array_length(document_json,'$.scenes'),
    json_type(document_json,'$.id'),
    json_type(document_json,'$.generation'),
    json_type(document_json,'$.title')
FROM projects WHERE id = ?1
"#;

fn read_head(tx: &Transaction<'_>, project_id: Uuid) -> ProjectionResult<ProjectionHead> {
    let key = project_id.to_string();
    let status: Option<String> = tx
        .query_row(ROOT_STATUS_SQL, [&key], |row| row.get(0))
        .optional()?;
    let status = status.ok_or(StorageError::NotFound)?;
    if status != "ok" {
        return Err(invalid(status));
    }
    let mut statement = tx.prepare(HEAD_SQL)?;
    let mut rows = statement.query([&key])?;
    let row = rows.next()?.ok_or(StorageError::NotFound)?;
    for (index, expected) in [
        (4, "integer"),
        (5, "integer"),
        (9, "integer"),
        (10, "integer"),
        (13, "array"),
        (15, "text"),
        (16, "text"),
        (17, "text"),
    ] {
        if text(row, index, 16)? != expected {
            return Err(invalid("project header type differs"));
        }
    }
    let parse_id =
        |value: &str| Uuid::parse_str(value).map_err(|_| invalid("invalid UUID in project header"));
    let row_id = parse_id(text(row, 0, 45)?)?;
    let generation = parse_id(text(row, 1, 45)?)?;
    let revision =
        u64::try_from(row.get::<_, i64>(2)?).map_err(|_| invalid("negative row revision"))?;
    let title = text(row, 3, 200)?;
    let document_id = parse_id(text(row, 7, 45)?)?;
    let document_generation = parse_id(text(row, 8, 45)?)?;
    let document_revision =
        u64::try_from(row.get::<_, i64>(11)?).map_err(|_| invalid("negative document revision"))?;
    let document_title = text(row, 12, 200)?;
    if row_id != project_id
        || document_id != row_id
        || generation.is_nil()
        || document_generation != generation
        || document_revision != revision
        || document_title != title
    {
        return Err(invalid("project row and document authority differ"));
    }
    if title.trim().is_empty() {
        return Err(invalid("empty project title"));
    }
    let schema_version = u32::try_from(row.get::<_, i64>(6)?)
        .map_err(|_| invalid("invalid project schema version"))?;
    if ![LEGACY_PROJECT_SCHEMA_VERSION, PROJECT_SCHEMA_VERSION].contains(&schema_version) {
        return Err(invalid("unsupported project document schema"));
    }
    let scene_count = u32::try_from(row.get::<_, i64>(14)?)
        .map_err(|_| invalid("scene count exceeds the projection contract"))?;
    Ok(ProjectionHead {
        identity: ProjectionIdentity {
            project_id: row_id,
            generation,
            revision: revision.to_string(),
        },
        schema_version,
        title: title.to_owned(),
        scene_count,
    })
}

const SCENE_PAGE_SQL: &str = r#"
WITH selected AS (
    SELECT item.key AS ordinal, item.type AS item_type,
        CASE WHEN item.type = 'object' THEN item.value ELSE '{}' END AS scene
    FROM projects AS project, json_each(project.document_json,'$.scenes') AS item
    WHERE project.id = ?1 AND item.key >= ?2 AND item.key < ?3
),
checked AS (
    SELECT *, CASE
        WHEN item_type <> 'object' THEN 'scene is not an object'
        WHEN EXISTS (
            SELECT 1 FROM json_each(scene)
            WHERE instr(key,char(0)) > 0
               OR (key IN ('id','name','start','duration','renderer','status','beats','nodes')
                   AND instr(fullkey, '\u0000') > 0)
        )
            THEN 'ambiguous scene key'
        WHEN EXISTS (
            SELECT 1 FROM json_each(scene)
            WHERE key IN ('id','name','start','duration','renderer','status','beats','nodes')
            GROUP BY key COLLATE BINARY HAVING count(*) > 1
        ) THEN 'duplicate scene projection key'
        WHEN json_type(scene,'$.id') IS NOT 'text'
          OR json_type(scene,'$.name') IS NOT 'text'
          OR json_type(scene,'$.start') IS NOT 'object'
          OR json_type(scene,'$.duration') IS NOT 'object'
          OR json_type(scene,'$.renderer') IS NOT 'text'
          OR json_type(scene,'$.status') IS NOT 'text'
          OR json_type(scene,'$.beats') IS NOT 'array'
          OR json_type(scene,'$.nodes') IS NOT 'array'
            THEN 'scene projection field has the wrong type'
        ELSE 'ok' END AS status
    FROM selected
),
encoded AS (
    SELECT *, CASE WHEN status = 'ok' THEN json_object(
        'ordinal', ordinal,
        'id', json_extract(scene,'$.id'),
        'name', json_extract(scene,'$.name'),
        'start', json_extract(scene,'$.start'),
        'duration', json_extract(scene,'$.duration'),
        'renderer', json_extract(scene,'$.renderer'),
        'status', json_extract(scene,'$.status'),
        'beat_count', json_array_length(scene,'$.beats'),
        'node_count', json_array_length(scene,'$.nodes')
    ) END AS summary FROM checked
)
SELECT ordinal, status, length(CAST(summary AS BLOB)),
    CASE WHEN length(CAST(summary AS BLOB)) <= ?4 THEN summary END
FROM encoded ORDER BY ordinal
"#;

fn read_scene_summaries(
    tx: &Transaction<'_>,
    project_id: Uuid,
    start: u32,
    end: u32,
) -> ProjectionResult<Vec<SceneSummary>> {
    let mut statement = tx.prepare(SCENE_PAGE_SQL)?;
    let mut rows = statement.query(params![
        project_id.to_string(),
        i64::from(start),
        i64::from(end),
        i64::try_from(MAX_SQL_FRAGMENT_BYTES).map_err(|_| too_large())?,
    ])?;
    let mut items = Vec::new();
    let mut input_bytes = 0;
    let mut ordinal = start;
    while let Some(row) = rows.next()? {
        if row.get::<_, i64>(0)? != i64::from(ordinal) {
            return Err(invalid("scene page order is not contiguous"));
        }
        let status = text(row, 1, 96)?;
        if status != "ok" {
            return Err(invalid(status));
        }
        let source_bytes = usize::try_from(row.get::<_, i64>(2)?).map_err(|_| too_large())?;
        if source_bytes > MAX_SQL_FRAGMENT_BYTES {
            return Err(too_large());
        }
        let json = text(row, 3, MAX_PROJECTION_RESPONSE_BYTES)?;
        add_input_bytes(&mut input_bytes, json.len())?;
        let summary: SceneSummary = serde_json::from_str(json)?;
        if summary.ordinal != ordinal {
            return Err(invalid("scene summary ordinal differs"));
        }
        summary.validate()?;
        items.push(summary);
        ordinal = ordinal
            .checked_add(1)
            .ok_or_else(|| invalid("scene ordinal overflow"))?;
    }
    Ok(items)
}

const NODE_PAGE_SQL: &str = r#"
SELECT item.key, item.type, length(CAST(item.value AS BLOB)),
    CASE WHEN item.type = 'object' AND length(CAST(item.value AS BLOB)) <= ?5
         THEN item.value END
FROM projects AS project, json_each(project.document_json, ?2) AS item
WHERE project.id = ?1 AND item.key >= ?3 AND item.key < ?4
ORDER BY item.key
"#;

fn read_nodes(
    tx: &Transaction<'_>,
    project_id: Uuid,
    scene_ordinal: u32,
    start: u32,
    end: u32,
    duration: RationalTime,
) -> ProjectionResult<Vec<CanvasNode>> {
    let path = format!("$.scenes[{scene_ordinal}].nodes");
    let mut statement = tx.prepare(NODE_PAGE_SQL)?;
    let mut rows = statement.query(params![
        project_id.to_string(),
        path,
        i64::from(start),
        i64::from(end),
        i64::try_from(MAX_SQL_FRAGMENT_BYTES).map_err(|_| too_large())?,
    ])?;
    let mut items = Vec::new();
    let mut input_bytes = 0;
    let mut ordinal = start;
    while let Some(row) = rows.next()? {
        if row.get::<_, i64>(0)? != i64::from(ordinal) {
            return Err(invalid("node page order is not contiguous"));
        }
        if text(row, 1, 16)? != "object" {
            return Err(invalid("node is not an object"));
        }
        let source_bytes = usize::try_from(row.get::<_, i64>(2)?).map_err(|_| too_large())?;
        if source_bytes > MAX_SQL_FRAGMENT_BYTES {
            return Err(too_large());
        }
        let json = text(row, 3, MAX_PROJECTION_RESPONSE_BYTES)?;
        add_input_bytes(&mut input_bytes, json.len())?;
        let node: CanvasNode = serde_json::from_str(json)?;
        node.validate()?;
        if node
            .keyframes
            .iter()
            .any(|keyframe| keyframe.at >= duration)
        {
            return Err(invalid("canvas keyframe exceeds its scene interval"));
        }
        items.push(node);
        ordinal = ordinal
            .checked_add(1)
            .ok_or_else(|| invalid("node ordinal overflow"))?;
    }
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{CameraState, Change, CoordinateSpace, NodeStyle, Project, Scene};
    use serde_json::{Value, json};
    use std::collections::BTreeSet;
    use tempfile::{TempDir, tempdir};

    fn persisted() -> (TempDir, Store, Project) {
        let directory = tempdir().unwrap();
        let mut project = Project::new("Projection authority").unwrap();
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: "One".into(),
            kind: "text".into(),
            parent_id: None,
            x: 0.0,
            y: 0.0,
            width: 100.0,
            height: 100.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: Some("Visible".into()),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle::default(),
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        let mut second = node.clone();
        second.id = Uuid::now_v7();
        second.name = "Two".into();
        project.scenes.push(Scene {
            id: Uuid::now_v7(),
            name: "Scene".into(),
            objective: "Read fragments".into(),
            start: RationalTime::ZERO,
            duration: RationalTime::new(10, 1).unwrap(),
            renderer: RendererKind::MotionCanvas,
            status: SceneStatus::Draft,
            beats: vec![],
            nodes: vec![node, second],
            camera: CameraState::default(),
        });
        let mut store = Store::open(directory.path().join("project.sqlite")).unwrap();
        store.create_project(&project).unwrap();
        (directory, store, project)
    }

    fn replace_document(store: &Store, project_id: Uuid, raw: &str) {
        store
            .conn
            .execute(
                "UPDATE projects SET document_json=?2 WHERE id=?1",
                params![project_id.to_string(), raw],
            )
            .unwrap();
    }

    #[test]
    fn projection_authority_rejects_json_coercions_and_row_mismatches() {
        let (_directory, mut store, project) = persisted();
        let original = serde_json::to_value(&project).unwrap();
        let cases = [
            ("revision", json!(false)),
            ("revision", json!(0.0)),
            ("revision", json!("0")),
            ("revision", json!(-1)),
            ("revision", json!(u64::MAX)),
            ("schema_version", json!(2.0)),
            ("schema_version", json!("2")),
            ("schema_version", json!(999)),
            ("id", json!(Uuid::now_v7())),
            ("generation", json!(Uuid::nil())),
            ("title", json!("Different")),
            ("scenes", json!({})),
            ("scenes", Value::Null),
        ];
        for (field, value) in cases {
            let mut document = original.clone();
            document[field] = value;
            replace_document(
                &store,
                project.id,
                &serde_json::to_string(&document).unwrap(),
            );
            assert!(
                store.project_projection_head(project.id).is_err(),
                "{field}"
            );
        }
        for raw in ["{", "[]", "null", "false"] {
            replace_document(&store, project.id, raw);
            assert!(store.project_projection_head(project.id).is_err(), "{raw}");
        }
        replace_document(
            &store,
            project.id,
            &serde_json::to_string(&original).unwrap(),
        );
        assert_eq!(
            store
                .project_projection_head(project.id)
                .unwrap()
                .identity
                .revision,
            "0"
        );
        assert_eq!(store.load_project(project.id).unwrap(), project);
    }

    #[test]
    fn projection_keys_reject_duplicates_and_nul_aliases_without_truncating_values() {
        let (_directory, mut store, project) = persisted();
        let raw = serde_json::to_string(&project).unwrap();
        for suffix in [
            format!(r#""id":"{}""#, project.id),
            format!(r#""\u0069d":"{}""#, project.id),
            format!(r#""id\u0000shadow":"{}""#, project.id),
            r#""scenes\u0000shadow":[]"#.into(),
        ] {
            let malformed = format!("{},{}{}", &raw[..raw.len() - 1], suffix, "}");
            replace_document(&store, project.id, &malformed);
            assert!(
                store.project_projection_head(project.id).is_err(),
                "{suffix}"
            );
        }
        // A NUL alias must fail even when the exact key is absent.
        let aliased = raw.replacen("\"id\":", "\"id\\u0000shadow\":", 1);
        replace_document(&store, project.id, &aliased);
        assert!(store.project_projection_head(project.id).is_err());
        let mut document = serde_json::to_value(&project).unwrap();
        document["id"] = json!(format!("{}\0shadow", project.id));
        replace_document(
            &store,
            project.id,
            &serde_json::to_string(&document).unwrap(),
        );
        assert!(store.project_projection_head(project.id).is_err());
        let mut valid = project.clone();
        valid.title = "Title\0suffix".into();
        valid.scenes[0].nodes[0].text = Some("Text\0suffix é".into());
        valid.validate().unwrap();
        store
            .conn
            .execute(
                "UPDATE projects SET title=?2,document_json=?3 WHERE id=?1",
                params![
                    project.id.to_string(),
                    valid.title,
                    serde_json::to_string(&valid).unwrap()
                ],
            )
            .unwrap();
        let head = store.project_projection_head(project.id).unwrap();
        assert_eq!(head.title, valid.title);
        let page = store
            .project_scene_nodes(
                &head.identity,
                &SceneRef {
                    ordinal: 0,
                    id: valid.scenes[0].id,
                },
                None,
                1,
            )
            .unwrap();
        assert_eq!(page.items[0], valid.scenes[0].nodes[0]);
        assert_eq!(store.load_project(project.id).unwrap(), valid);
    }

    #[test]
    fn projection_preserves_accepted_uuid_forms_and_escaped_valid_field_names() {
        let (_directory, mut store, project) = persisted();
        let mut document = serde_json::to_value(&project).unwrap();
        document["id"] = json!(project.id.to_string().to_uppercase());
        document["generation"] = json!(project.generation.simple().to_string());
        document["scenes"][0]["id"] = json!(project.scenes[0].id.braced().to_string());
        let raw = serde_json::to_string(&document)
            .unwrap()
            .replace(
                &format!("\"id\":\"{}\"", project.id.to_string().to_uppercase()),
                &format!("\"\\u0069d\":\"{}\"", project.id.to_string().to_uppercase()),
            )
            .replace("\"scenes\":", "\"scen\\u0065s\":")
            .replace("\"nodes\":", "\"nod\\u0065s\":");
        replace_document(&store, project.id, &raw);
        assert_eq!(store.load_project(project.id).unwrap(), project);
        let head = store.project_projection_head(project.id).unwrap();
        let page = store.project_scene_page(&head.identity, None, 1).unwrap();
        assert_eq!(page.items[0].id, project.scenes[0].id);
        let nodes = store
            .project_scene_nodes(&head.identity, &page.items[0].scene_ref(), None, 2)
            .unwrap();
        assert_eq!(nodes.items, project.scenes[0].nodes);
    }

    #[test]
    fn projection_validates_selected_content_without_inventing_a_complete_scene() {
        let (_directory, mut store, project) = persisted();
        let mut document = serde_json::to_value(&project).unwrap();
        document["scenes"][0]["nodes"][1]["name"] = json!("");
        replace_document(
            &store,
            project.id,
            &serde_json::to_string(&document).unwrap(),
        );
        let head = store.project_projection_head(project.id).unwrap();
        let scene = SceneRef {
            ordinal: 0,
            id: project.scenes[0].id,
        };
        let first = store
            .project_scene_nodes(&head.identity, &scene, None, 1)
            .unwrap();
        assert_eq!(first.items[0], project.scenes[0].nodes[0]);
        assert!(
            store
                .project_scene_nodes(&head.identity, &scene, first.next_cursor.as_ref(), 1)
                .is_err()
        );
        assert!(store.load_project(project.id).is_err());

        for replacement in [json!(null), json!({}), json!("nodes")] {
            let mut document = serde_json::to_value(&project).unwrap();
            document["scenes"][0]["nodes"] = replacement;
            replace_document(
                &store,
                project.id,
                &serde_json::to_string(&document).unwrap(),
            );
            assert!(store.project_scene_page(&head.identity, None, 1).is_err());
            assert!(
                store
                    .project_scene_nodes(&head.identity, &scene, None, 1)
                    .is_err()
            );
        }
        let raw = serde_json::to_string(&project)
            .unwrap()
            .replace("\"nodes\":", "\"nodes\\u0000shadow\":");
        replace_document(&store, project.id, &raw);
        assert!(store.project_scene_page(&head.identity, None, 1).is_err());
        assert!(
            store
                .project_scene_nodes(&head.identity, &scene, None, 1)
                .is_err()
        );
    }

    #[test]
    fn projection_read_transaction_keeps_one_snapshot_during_real_peer_apply() {
        let (directory, mut store, project) = persisted();
        let mut peer = Store::open(directory.path().join("project.sqlite")).unwrap();
        let old_identity;
        {
            let tx = store
                .conn
                .transaction_with_behavior(TransactionBehavior::Deferred)
                .unwrap();
            let head = read_head(&tx, project.id).unwrap();
            old_identity = head.identity;
            assert_eq!(head.scene_count, 1);
            peer.apply(
                project.id,
                &RevisionStamp::from(&project),
                "projection-snapshot-peer",
                &Change::AddScene {
                    name: "Peer addition".into(),
                    objective: "New revision".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap();
            let selected = read_scene_summaries(&tx, project.id, 0, 2).unwrap();
            assert_eq!(selected.len(), 1);
            assert_eq!(selected[0].id, project.scenes[0].id);
            assert_eq!(read_head(&tx, project.id).unwrap().identity, old_identity);
            tx.commit().unwrap();
        }
        assert!(matches!(
            store.project_scene_page(&old_identity, None, 1),
            Err(ProjectionError::Storage(StorageError::Conflict {
                expected: 0,
                actual: 1
            }))
        ));
        let current = store.project_projection_head(project.id).unwrap();
        assert_eq!(current.scene_count, 2);
        assert_eq!(current.identity.revision, "1");
        assert_eq!(store.load_project(project.id).unwrap().scenes.len(), 2);
    }

    #[test]
    fn projection_revision_wire_is_exact_and_rejects_noncanonical_decimals() {
        let (_directory, mut store, project) = persisted();
        for revision in [0, 9_007_199_254_740_993_u64, u64::MAX] {
            let identity = ProjectionIdentity {
                project_id: project.id,
                generation: project.generation,
                revision: revision.to_string(),
            };
            let bytes = serde_json::to_vec(&identity).unwrap();
            let decoded: ProjectionIdentity = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(decoded.checked_stamp().unwrap().revision, revision);
        }
        for revision in ["", "01", "+1", "-1", " 1", "1.0", "18446744073709551616"] {
            let identity = ProjectionIdentity {
                project_id: project.id,
                generation: project.generation,
                revision: revision.into(),
            };
            assert!(identity.checked_stamp().is_err(), "{revision}");
        }
        let mut persisted = project.clone();
        persisted.revision = 9_007_199_254_740_993;
        persisted.validate().unwrap();
        store
            .conn
            .execute(
                "UPDATE projects SET revision=?2,document_json=?3 WHERE id=?1",
                params![
                    project.id.to_string(),
                    i64::try_from(persisted.revision).unwrap(),
                    serde_json::to_string(&persisted).unwrap()
                ],
            )
            .unwrap();
        assert_eq!(
            store
                .project_projection_head(project.id)
                .unwrap()
                .identity
                .revision,
            "9007199254740993"
        );
        assert_eq!(
            store.load_project(project.id).unwrap().revision,
            persisted.revision
        );
    }

    #[test]
    fn projection_oversized_fragment_and_malformed_schema_errors_are_bounded() {
        let (_directory, mut store, project) = persisted();
        let mut document = serde_json::to_value(&project).unwrap();
        document["scenes"][0]["nodes"][0]["text"] =
            json!("x".repeat(MAX_PROJECTION_RESPONSE_BYTES + 1));
        replace_document(
            &store,
            project.id,
            &serde_json::to_string(&document).unwrap(),
        );
        let head = store.project_projection_head(project.id).unwrap();
        let scene = SceneRef {
            ordinal: 0,
            id: project.scenes[0].id,
        };
        assert!(matches!(
            store.project_scene_nodes(&head.identity, &scene, None, 1),
            Err(ProjectionError::TooLarge { .. })
        ));

        let mut document = serde_json::to_value(&project).unwrap();
        let marker = "UNTRUSTED_FIELD_".repeat(4_000);
        document["scenes"][0]["nodes"][0]
            .as_object_mut()
            .unwrap()
            .insert(marker.clone(), json!(1));
        replace_document(
            &store,
            project.id,
            &serde_json::to_string(&document).unwrap(),
        );
        let error = store
            .project_scene_nodes(&head.identity, &scene, None, 1)
            .unwrap_err()
            .to_string();
        assert!(error.len() < 256);
        assert!(!error.contains(&marker));

        let mut document = serde_json::to_value(&project).unwrap();
        document.as_object_mut().unwrap().insert(
            r"id\u0000literal".into(),
            json!("ordinary unknown extension"),
        );
        replace_document(
            &store,
            project.id,
            &serde_json::to_string(&document).unwrap(),
        );
        assert_eq!(
            store.project_projection_head(project.id).unwrap().identity,
            head.identity
        );
        assert_eq!(store.load_project(project.id).unwrap(), project);
    }

    #[test]
    fn projection_keyframes_keep_rational_half_open_scene_bounds() {
        use motionwright_domain::{CanvasKeyframe, MotionInterpolation, MotionProperty};
        let (_directory, mut store, project) = persisted();
        let mut valid = project.clone();
        valid.scenes[0].duration = RationalTime::new(1, 2).unwrap();
        valid.scenes[0].nodes[0].keyframes.push(CanvasKeyframe {
            at: RationalTime::new(2, 5).unwrap(),
            property: MotionProperty::Opacity,
            value: 0.5,
            interpolation: MotionInterpolation::Linear,
        });
        valid.validate().unwrap();
        replace_document(&store, project.id, &serde_json::to_string(&valid).unwrap());
        let head = store.project_projection_head(project.id).unwrap();
        let scene = SceneRef {
            ordinal: 0,
            id: valid.scenes[0].id,
        };
        assert_eq!(
            store
                .project_scene_nodes(&head.identity, &scene, None, 1)
                .unwrap()
                .items[0],
            valid.scenes[0].nodes[0]
        );
        assert_eq!(store.load_project(project.id).unwrap(), valid);
        for at in [
            RationalTime::new(1, 2).unwrap(),
            RationalTime::new(3, 5).unwrap(),
        ] {
            let mut invalid = valid.clone();
            invalid.scenes[0].nodes[0].keyframes[0].at = at;
            assert!(invalid.validate().is_err());
            replace_document(
                &store,
                project.id,
                &serde_json::to_string(&invalid).unwrap(),
            );
            assert!(
                store
                    .project_scene_nodes(&head.identity, &scene, None, 1)
                    .is_err()
            );
        }
    }
}
