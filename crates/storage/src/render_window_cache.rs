//! Explicit, disposable source-window cache receipts for independently generated
//! derived artifacts. No direct renderer admission or incremental-frame savings claim.
use super::{DerivedCacheHit, DerivedCacheRecord, Result, StorageError, Store, validate_digest};
use motionwright_domain::{
    Project, RenderImpactCause, RenderTailImpactReport, RenderTailInspectRequest, RevisionStamp,
    SourceFrameWindow,
};
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use std::path::Path;
use uuid::Uuid;

const KIND: &str = "render-window-proxy";
const MAX_WINDOW_FRAMES: u64 = 4_096;
const MAX_INDEXED_ENTRIES: usize = 4_096;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderWindowCacheKey {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub deliverable_id: Uuid,
    /// Source timeline, not exported montage coordinates.
    pub source_window: SourceFrameWindow,
    /// Caller-authored dependency fingerprint; not renderer-equivalence proof.
    pub fingerprint_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderWindowPurgeReport {
    pub impact: RenderTailImpactReport,
    pub registered_entries_checked: usize,
    pub manifests_removed: usize,
    pub registrations_removed: usize,
    pub registrations_retained: usize,
    /// Physical cache metadata deletion only; immutable blobs remain.
    pub immutable_blobs_deleted: u64,
    pub native_render_equivalence_verified: bool,
}

fn invalid(message: impl Into<String>) -> StorageError {
    StorageError::InvalidDerivedCache(format!("render-window proxy: {}", message.into()))
}

fn scope(key: &RenderWindowCacheKey) -> String {
    format!(
        "project:{}:generation:{}:revision:{}:profile:{}:source:{}-{}",
        key.project_id,
        key.expected.generation,
        key.expected.revision,
        key.deliverable_id,
        key.source_window.start,
        key.source_window.end,
    )
}

fn check_stamp(project: &Project, stamp: &RevisionStamp) -> Result<()> {
    if stamp.resource != project.resource_key() || stamp.generation != project.generation {
        return Err(StorageError::GenerationConflict);
    }
    if stamp.revision != project.revision {
        return Err(StorageError::Conflict {
            expected: stamp.revision,
            actual: project.revision,
        });
    }
    Ok(())
}

fn overlaps(a: SourceFrameWindow, b: SourceFrameWindow) -> bool {
    a.start < b.end && b.start < a.end
}

fn validate_key(store: &Store, key: &RenderWindowCacheKey) -> Result<()> {
    validate_digest(&key.fingerprint_sha256)?;
    let w = key.source_window;
    if w.end <= w.start || w.end - w.start > MAX_WINDOW_FRAMES || w.end > 1_080_000 {
        return Err(invalid(
            "source frame window invalid or exceeds bounded budget",
        ));
    }
    let project = store.load_project(key.project_id)?;
    check_stamp(&project, &key.expected)?;
    // The domain owns the source clock. Cut profiles cannot authorize a cache
    // window outside their selected original scenes.
    let report = project.inspect_render_tail_impact(&RenderTailInspectRequest {
        expected_generation: project.generation,
        expected_revision: project.revision,
        changed_scene_ids: vec![],
        cause: RenderImpactCause::GlobalMix,
        tail_frames: 0,
        tail_source_asset_id: None,
    })?;
    let profile = report
        .profiles
        .iter()
        .find(|p| p.deliverable_id == key.deliverable_id)
        .ok_or_else(|| invalid("unknown deliverable profile"))?;
    if !profile
        .source_timeline_windows
        .iter()
        .any(|valid| w.start >= valid.start && w.end <= valid.end)
    {
        return Err(invalid(
            "window is not wholly within a selected source interval",
        ));
    }
    Ok(())
}

#[derive(Debug)]
struct IndexedWindow {
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    start: u64,
    end: u64,
    fingerprint: String,
}

impl Store {
    /// The cache index is not source authority. A missing registration must be
    /// a miss even when an orphaned manifest exists (e.g. interrupted admission).
    pub fn put_render_window_proxy(
        &self,
        key: &RenderWindowCacheKey,
        source: impl AsRef<Path>,
        work_units: u64,
    ) -> Result<DerivedCacheRecord> {
        validate_key(self, key)?;
        let found: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM render_window_cache_entries WHERE project_id=?1",
            [key.project_id.to_string()],
            |r| r.get(0),
        )?;
        if found >= MAX_INDEXED_ENTRIES as i64 {
            let exists: Option<i64> = self
                .conn
                .query_row(
                    "SELECT 1 FROM render_window_cache_entries
                 WHERE project_id=?1 AND generation=?2 AND revision=?3
                 AND deliverable_id=?4 AND source_start=?5 AND source_end=?6
                 AND fingerprint_sha256=?7",
                    params![
                        key.project_id.to_string(),
                        key.expected.generation.to_string(),
                        key.expected.revision as i64,
                        key.deliverable_id.to_string(),
                        key.source_window.start as i64,
                        key.source_window.end as i64,
                        key.fingerprint_sha256
                    ],
                    |row| row.get(0),
                )
                .optional()?;
            if exists.is_none() {
                return Err(invalid("indexed project cache quota exceeded"));
            }
        }
        let record = self.put_derived_cache_file(
            &scope(key),
            KIND,
            &key.fingerprint_sha256,
            source,
            work_units,
        )?;
        self.conn.execute(
            "INSERT OR IGNORE INTO render_window_cache_entries
             (project_id, generation, revision, deliverable_id,
              source_start, source_end, fingerprint_sha256)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                key.project_id.to_string(),
                key.expected.generation.to_string(),
                key.expected.revision as i64,
                key.deliverable_id.to_string(),
                key.source_window.start as i64,
                key.source_window.end as i64,
                key.fingerprint_sha256
            ],
        )?;
        Ok(record)
    }

    pub fn lookup_render_window_proxy(
        &self,
        key: &RenderWindowCacheKey,
    ) -> Result<Option<DerivedCacheHit>> {
        validate_key(self, key)?;
        let exists: Option<i64> = self
            .conn
            .query_row(
                "SELECT 1 FROM render_window_cache_entries
             WHERE project_id=?1 AND generation=?2 AND revision=?3
             AND deliverable_id=?4 AND source_start=?5 AND source_end=?6
             AND fingerprint_sha256=?7",
                params![
                    key.project_id.to_string(),
                    key.expected.generation.to_string(),
                    key.expected.revision as i64,
                    key.deliverable_id.to_string(),
                    key.source_window.start as i64,
                    key.source_window.end as i64,
                    key.fingerprint_sha256
                ],
                |row| row.get(0),
            )
            .optional()?;
        if exists.is_none() {
            return Ok(None);
        }
        self.lookup_derived_cache(&scope(key), KIND, &key.fingerprint_sha256)
    }

    /// Physically purges overlapping cache manifests while preserving CAS
    /// source and history. Revision mismatch or unverified cut mapping requires
    /// a full-profile purge rather than optimistic subframe reuse.
    pub fn purge_render_window_proxies(
        &mut self,
        project_id: Uuid,
        request: &RenderTailInspectRequest,
    ) -> Result<RenderWindowPurgeReport> {
        let project = self.load_project(project_id)?;
        if request.expected_generation != project.generation {
            return Err(StorageError::GenerationConflict);
        }
        if request.expected_revision != project.revision {
            return Err(StorageError::Conflict {
                expected: request.expected_revision,
                actual: project.revision,
            });
        }
        let mut impact = project.inspect_render_tail_impact(request)?;
        let mut stmt = self.conn.prepare(
            "SELECT generation, revision, deliverable_id, source_start,
                    source_end, fingerprint_sha256
             FROM render_window_cache_entries WHERE project_id=?1
             ORDER BY deliverable_id, source_start, source_end, fingerprint_sha256
             LIMIT ?2",
        )?;
        let rows = stmt.query_map(
            params![project_id.to_string(), (MAX_INDEXED_ENTRIES + 1) as i64],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, i64>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, String>(5)?,
                ))
            },
        )?;
        let mut entries = Vec::new();
        for row in rows {
            let (generation, revision, deliverable, start, end, fingerprint) = row?;
            entries.push(IndexedWindow {
                generation: Uuid::parse_str(&generation)
                    .map_err(|_| invalid("invalid registered generation"))?,
                revision: u64::try_from(revision)
                    .map_err(|_| invalid("invalid registered revision"))?,
                deliverable_id: Uuid::parse_str(&deliverable)
                    .map_err(|_| invalid("invalid registered deliverable"))?,
                start: u64::try_from(start)
                    .map_err(|_| invalid("invalid registered frame start"))?,
                end: u64::try_from(end).map_err(|_| invalid("invalid registered frame end"))?,
                fingerprint,
            });
        }
        drop(stmt);
        if entries.len() > MAX_INDEXED_ENTRIES {
            return Err(invalid(
                "registered cache quota violated; refusing partial purge",
            ));
        }
        let mut manifests_removed = 0;
        let mut registrations_removed = 0;
        for entry in &entries {
            let profile = impact
                .profiles
                .iter()
                .find(|p| p.deliverable_id == entry.deliverable_id);
            let stale =
                entry.generation != project.generation || entry.revision != project.revision;
            let unsafe_projection =
                profile.is_none_or(|p| p.is_scoped_selection || p.exceeds_source_duration);
            let dirty = profile.is_some_and(|p| {
                p.source_timeline_windows.iter().any(|window| {
                    overlaps(
                        *window,
                        SourceFrameWindow {
                            start: entry.start,
                            end: entry.end,
                        },
                    )
                })
            });
            if !(stale || unsafe_projection || dirty) {
                continue;
            }
            let stamp = RevisionStamp {
                resource: project.resource_key(),
                generation: entry.generation,
                revision: entry.revision,
            };
            let key = RenderWindowCacheKey {
                project_id,
                expected: stamp,
                deliverable_id: entry.deliverable_id,
                source_window: SourceFrameWindow {
                    start: entry.start,
                    end: entry.end,
                },
                fingerprint_sha256: entry.fingerprint.clone(),
            };
            // This removes the derived manifest, not the immutable content blob.
            // The SQL row is removed *after* eviction; on interruption, a
            // remaining row can only point to a cache miss, never stale pixels.
            if self.evict_derived_cache_exact(&scope(&key), KIND, &key.fingerprint_sha256)? {
                manifests_removed += 1;
            }
            self.conn.execute(
                "DELETE FROM render_window_cache_entries WHERE project_id=?1 AND generation=?2
                 AND revision=?3 AND deliverable_id=?4 AND source_start=?5
                 AND source_end=?6 AND fingerprint_sha256=?7",
                params![
                    project_id.to_string(),
                    entry.generation.to_string(),
                    entry.revision as i64,
                    entry.deliverable_id.to_string(),
                    entry.start as i64,
                    entry.end as i64,
                    entry.fingerprint
                ],
            )?;
            registrations_removed += 1;
        }
        impact.actually_invalidated_caches = registrations_removed != 0;
        impact.technical_acceptance =
            "DERIVED_RENDER_WINDOW_MANIFESTS_PURGED_NATIVE_RENDER_EQUIVALENCE_NOT_RUN".into();
        Ok(RenderWindowPurgeReport {
            impact,
            registered_entries_checked: entries.len(),
            manifests_removed,
            registrations_removed,
            registrations_retained: entries.len() - registrations_removed,
            immutable_blobs_deleted: 0,
            native_render_equivalence_verified: false,
        })
    }
}
