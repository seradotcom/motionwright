use async_trait::async_trait;
use motionwright_domain::{
    Change, MAX_MUSIC_CONFORM_TARGETS, MusicConformTarget, Project, RevisionStamp,
};
use motionwright_service::{ProductionReceiptWatermark, StudioService};
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyReceiptInvalid, ApplyResultObservation, StorageError,
    apply_request_digest_v1,
};
use semwright_native_sdk::cooperation::{
    Application, CallContext, CancellationSemantics, CommitSemantics, Completion, ObservationPage,
    ObservationProvider, OperationContract, OperationHandler, PageCursor, Query, RecoveryProvider,
    RecoveryRecord, RequestIdentity, ResourceVersion, RetrySemantics, RevisionToken,
    TargetRequirement, UndoSemantics,
};
use semwright_native_sdk::{
    CommandDescriptor, Error, ErrorCode, Idempotency, Result as NativeResult, Risk, Value, json,
};
use std::sync::Arc;
use uuid::Uuid;

pub mod assembly;
pub mod blender_product_scene;
pub mod canonical;
mod component_text;
pub mod creative_doctor;
mod expressive;
pub mod film;
mod graph_native_probe;
pub mod hyperframes;
pub mod manim_camera_projection;
pub mod manim_editable_projection;
pub mod manim_graph_projection;
pub mod manim_static_projection;
pub mod mlt_av_audio;
pub mod mlt_edit_plan;
pub mod multi_renderer;
mod native_scene_boundary;
pub mod owner_reconciliation;
pub mod production;
#[cfg(test)]
#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod semantic_graph_source_fixture;
pub mod sfx_mix;
#[cfg(test)]
#[path = "../../storage/tests/support/sqlite_v6_corruptible.rs"]
mod sqlite_v6_corruptible;

pub use graph_store_provider::run_native_provider;

pub const APP_ID: &str = "motionwright";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

fn resource_version(project: &Project) -> NativeResult<ResourceVersion> {
    let version = ResourceVersion {
        resource: project.resource_key(),
        generation: project.generation.to_string(),
        revision: RevisionToken::new(project.revision.to_string())?,
    };
    version.validate()?;
    Ok(version)
}

fn project_id_from_resource(resource: &str) -> NativeResult<Uuid> {
    let value = resource
        .strip_prefix("project:")
        .ok_or_else(|| Error::new(ErrorCode::NotFound, "Unknown Motionwright resource"))?;
    Uuid::parse_str(value).map_err(|_| Error::invalid("Malformed Motionwright project resource"))
}

fn expected_stamp(context: &CallContext, project: &Project) -> NativeResult<RevisionStamp> {
    let expected = context.expected().ok_or_else(|| {
        Error::new(
            ErrorCode::StaleReference,
            "Observed project base is required",
        )
    })?;
    expected.validate()?;
    if expected.resource != project.resource_key() {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Project reference changed",
        ));
    }
    let generation = Uuid::parse_str(&expected.generation)
        .map_err(|_| Error::new(ErrorCode::StaleReference, "Project generation is invalid"))?;
    let revision = expected
        .revision
        .as_str()
        .parse::<u64>()
        .map_err(|_| Error::new(ErrorCode::StaleReference, "Project revision is invalid"))?;
    Ok(RevisionStamp {
        resource: expected.resource.clone(),
        generation,
        revision,
    })
}

fn storage_error(error: StorageError) -> Error {
    match error {
        StorageError::NotFound => Error::new(ErrorCode::NotFound, "Motionwright project not found"),
        StorageError::Conflict { .. } | StorageError::GenerationConflict => Error::new(
            ErrorCode::StaleReference,
            "Motionwright project changed since observation",
        ),
        StorageError::RequestReuse => Error::new(
            ErrorCode::Conflict,
            "Request id was already used for different input",
        ),
        StorageError::ReceiptStreamChanged => Error::new(
            ErrorCode::StaleReference,
            "Production receipt stream changed during observation; begin a new snapshot",
        ),
        StorageError::Domain(error) => Error::new(ErrorCode::InvalidArgument, error.to_string()),
        StorageError::Serde(_)
        | StorageError::InvalidBackup(_)
        | StorageError::InvalidBlobDigest
        | StorageError::BlobTooLarge { .. }
        | StorageError::UnsafeSourcePath(_) => {
            Error::new(ErrorCode::InvalidArgument, "Malformed project value")
        }
        StorageError::ProjectExists | StorageError::DestinationExists => {
            Error::new(ErrorCode::Conflict, "Motionwright resource already exists")
        }
        StorageError::BlobMissing { .. } => {
            Error::new(ErrorCode::NotFound, "Motionwright asset blob not found")
        }
        StorageError::UnsupportedSchemaVersion { .. } => Error::new(
            ErrorCode::BackendFailed,
            "Motionwright storage schema is newer than this application",
        ),
        StorageError::InvalidApplyReceipt(_)
        | StorageError::InvalidFileImportReceipt(_)
        | StorageError::InvalidProjectCreationReceipt(_) => Error::new(
            ErrorCode::BackendFailed,
            "Motionwright operation receipt validation failed",
        ),
        StorageError::InvalidDerivedCache(_) => Error::new(
            ErrorCode::BackendFailed,
            "Motionwright derived cache validation failed",
        ),
        StorageError::Sql(_) | StorageError::Io(_) => {
            Error::new(ErrorCode::BackendFailed, "Motionwright storage failed")
        }
    }
}

/// Page application-owned arrays without hiding any elements. This cursor is
/// strictly bound to the Native SDK's project generation/revision and scope.
/// Unlike the journal's SQL keyset cursor, these arrays are already loaded as
/// part of the immutable in-memory Project snapshot, so integer offsets are
/// safe *only* while that exact project revision remains current.
fn paginate_project_observation(
    query: &Query,
    version: &ResourceVersion,
    total: usize,
    mut item_at: impl FnMut(usize) -> Value,
) -> NativeResult<ObservationPage> {
    let start = match &query.cursor {
        Some(cursor) => {
            if cursor.version != *version {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Project changed while paging an application-owned scope",
                ));
            }
            let suffix = cursor
                .token
                .strip_prefix("offset:")
                .ok_or_else(|| Error::invalid("Malformed application scope cursor"))?;
            let parsed = suffix
                .parse::<usize>()
                .map_err(|_| Error::invalid("Malformed application scope cursor offset"))?;
            // No zero/leading-zero/terminal/overflow cursors: a continuation
            // must have been issued after a nonempty earlier page.
            if parsed == 0 || suffix != parsed.to_string() || parsed >= total {
                return Err(Error::invalid(
                    "Application scope cursor does not make progress",
                ));
            }
            parsed
        }
        None => 0,
    };
    let end = start.saturating_add(usize::from(query.limit)).min(total);
    let next = (end < total).then(|| PageCursor {
        version: version.clone(),
        scope: query.scope.clone(),
        token: format!("offset:{end}"),
    });
    let page = ObservationPage {
        version: version.clone(),
        scope: query.scope.clone(),
        items: (start..end).map(&mut item_at).collect(),
        complete: next.is_none(),
        next,
    };
    page.validate_for(query)?;
    Ok(page)
}

/// Production receipt cursors have a second, independent watermark in
/// addition to the SDK creative resource version. Receipt writes do not
/// advance the creative revision and must invalidate in-flight enumeration.
fn parse_production_cursor(
    query: &Query,
    version: &ResourceVersion,
    namespace: &str,
) -> NativeResult<Option<(ProductionReceiptWatermark, String)>> {
    let Some(cursor) = &query.cursor else {
        return Ok(None);
    };
    if cursor.version != *version {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Creative project changed during production enumeration",
        ));
    }
    let segments: Vec<&str> = cursor.token.split(':').collect();
    if segments.len() != 5 || segments[0] != namespace || segments[1] != "v1" {
        return Err(Error::invalid("Malformed production receipt cursor"));
    }
    let latest_id = Uuid::parse_str(segments[2])
        .ok()
        .filter(|id| id.to_string() == segments[2])
        .ok_or_else(|| Error::invalid("Malformed production receipt watermark ID"))?;
    let count = segments[3]
        .parse::<u64>()
        .ok()
        .filter(|count| *count > 0 && count.to_string() == segments[3])
        .ok_or_else(|| Error::invalid("Malformed production receipt count"))?;
    Ok(Some((
        ProductionReceiptWatermark {
            latest_id: Some(latest_id),
            count,
        },
        segments[4].to_owned(),
    )))
}

fn production_cursor(
    query: &Query,
    version: &ResourceVersion,
    namespace: &str,
    snapshot: &ProductionReceiptWatermark,
    position: &str,
) -> NativeResult<PageCursor> {
    let latest = snapshot.latest_id.ok_or_else(|| {
        Error::invalid("Cannot create a continuation for an empty receipt stream")
    })?;
    Ok(PageCursor {
        version: version.clone(),
        scope: query.scope.clone(),
        token: format!("{namespace}:v1:{latest}:{}:{position}", snapshot.count),
    })
}

#[derive(Clone)]
pub struct MotionwrightObserver {
    service: StudioService,
    // Only the private owner-mounted provider composition enables this read.
    // This flag creates no authority; each request still requires real Host
    // resource admission, a current observation and exact native font probes.
    mounted_graph_store: bool,
}

impl MotionwrightObserver {
    pub fn new(service: StudioService) -> Self {
        Self {
            service,
            mounted_graph_store: false,
        }
    }
}

#[async_trait]
impl ObservationProvider for MotionwrightObserver {
    async fn observe(&self, query: &Query, context: &CallContext) -> NativeResult<ObservationPage> {
        context.check_cancelled()?;
        query.validate()?;
        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        graph_store_provider::trace_native_observer(query, context)?;
        context.check_cancelled()?;
        let project_id = project_id_from_resource(&query.resource)?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        let version = resource_version(&project)?;

        if query.scope == "graph-authoring" {
            if !self.mounted_graph_store {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Graph native inspection requires the owner-mounted Store",
                ));
            }
            return graph_store_provider::observe_authoring_context(
                &self.service,
                query,
                context,
                &project,
            )
            .await;
        }

        // The Native SDK owns the page/version contract; Motionwright supplies
        // only version-bound application journal cursors, never authorization.
        if query.scope == "history" || query.scope == "history-recent" {
            let recent = query.scope == "history-recent";
            if let Some(cursor) = &query.cursor
                && cursor.version != version
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Project changed while paging its history",
                ));
            }
            let prefix = if recent {
                "through-revision:"
            } else {
                "after-revision:"
            };
            let start = match &query.cursor {
                Some(cursor) => cursor
                    .token
                    .strip_prefix(prefix)
                    .and_then(|value| value.parse::<u64>().ok())
                    .ok_or_else(|| Error::invalid("Malformed history cursor"))?,
                None if recent => project.revision,
                None => 0,
            };
            if start > project.revision {
                return Err(Error::invalid("History cursor exceeds project revision"));
            }
            let limit = usize::from(query.limit);
            let events = if recent {
                self.service.history_recent(project_id, start, limit)
            } else {
                self.service.history(project_id, start, limit)
            }
            .map_err(storage_error)?;

            let continuation = if let Some(last) = events.last() {
                let next_start = if recent {
                    last.revision.checked_sub(1)
                } else {
                    Some(last.revision)
                };
                if let Some(next_start) = next_start {
                    let probe = if recent {
                        self.service.history_recent(project_id, next_start, 1)
                    } else {
                        self.service.history(project_id, next_start, 1)
                    }
                    .map_err(storage_error)?;
                    (!probe.is_empty()).then_some(next_start)
                } else {
                    None
                }
            } else {
                None
            };

            // Do not bind rows to a resource version if a concurrent writer
            // committed between the journal read and its continuation probe.
            let observed_again = self.service.project(project_id).map_err(storage_error)?;
            if observed_again.generation != project.generation
                || observed_again.revision != project.revision
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Project changed during history observation",
                ));
            }

            let next = continuation.map(|position| PageCursor {
                version: version.clone(),
                scope: query.scope.clone(),
                token: format!("{prefix}{position}"),
            });
            let page = ObservationPage {
                version,
                scope: query.scope.clone(),
                items: events
                    .into_iter()
                    .map(|event| serde_json::to_value(event).unwrap_or(Value::Null))
                    .collect(),
                complete: next.is_none(),
                next,
            };
            page.validate_for(query)?;
            return Ok(page);
        }

        // Read-only asset integrity is not a Project Graph projection.
        // Its sole authority is the same application-owned, versioned CAS
        // inspector that the desktop invokes on explicit user request.
        if query.scope == "asset-integrity" {
            let limit = usize::from(query.limit).min(16);
            let offset = match &query.cursor {
                Some(cursor) => {
                    if cursor.version != version {
                        return Err(Error::new(
                            ErrorCode::StaleReference,
                            "Creative project changed during asset integrity paging",
                        ));
                    }
                    let fields: Vec<_> = cursor.token.split(':').collect();
                    if fields.len() != 4
                        || fields[0] != "assets"
                        || fields[1] != "v1"
                        || fields[2] != limit.to_string()
                    {
                        return Err(Error::invalid(
                            "Malformed or changed-page-size asset integrity cursor",
                        ));
                    }
                    let offset = fields[3]
                        .parse::<usize>()
                        .ok()
                        .filter(|value| {
                            *value > 0
                                && value.to_string() == fields[3]
                                && value.is_multiple_of(limit)
                        })
                        .ok_or_else(|| {
                            Error::invalid("Asset integrity cursor does not make progress")
                        })?;
                    Some(offset)
                }
                None => None,
            };
            let service = self.service.clone();
            let stamp = RevisionStamp::from(&project);
            // Actual SHA-256 file hashing is bounded to 256 MiB per page and
            // runs outside the native async executor's core worker threads.
            let report = tokio::task::spawn_blocking(move || {
                service.asset_integrity_page(project_id, &stamp, offset, limit)
            })
            .await
            .map_err(|_| {
                Error::new(
                    ErrorCode::BackendFailed,
                    "Application asset integrity read worker failed",
                )
            })?
            .map_err(storage_error)?;
            // A canceled agent never receives a verified-current result from
            // a hashing task it no longer has permission to await.
            context.check_cancelled()?;
            if report.generation != project.generation || report.revision != project.revision {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Asset integrity result belongs to an older creative revision",
                ));
            }
            let latest = self.service.project(project_id).map_err(storage_error)?;
            if latest.generation != project.generation || latest.revision != project.revision {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Project changed while verifying content-addressed assets",
                ));
            }
            let next = report.next.map(|start| PageCursor {
                version: version.clone(),
                scope: query.scope.clone(),
                token: format!("assets:v1:{limit}:{start}"),
            });
            let page = ObservationPage {
                version,
                scope: query.scope.clone(),
                // Status and size only; source CAS paths/digests and asset
                // bytes stay private to the trusted Motionwright service.
                items: report
                    .items
                    .into_iter()
                    .map(|record| {
                        json!({
                            "asset_id": record.asset_id.to_string(),
                            "status": record.status,
                            "size_bytes": record.size_bytes,
                        })
                    })
                    .collect(),
                complete: next.is_none(),
                next,
            };
            page.validate_for(query)?;
            return Ok(page);
        }

        if query.scope == "production-receipts" || query.scope == "production-jobs" {
            let receipts_scope = query.scope == "production-receipts";
            let namespace = if receipts_scope { "receipts" } else { "jobs" };
            let cursor = parse_production_cursor(query, &version, namespace)?;
            let snapshot = if let Some((head, _)) = &cursor {
                head.clone()
            } else {
                self.service
                    .production_receipt_watermark(project_id)
                    .map_err(storage_error)?
            };
            if (snapshot.count == 0) != snapshot.latest_id.is_none() {
                return Err(Error::invalid(
                    "Production receipt snapshot is inconsistent",
                ));
            }
            let limit = usize::from(query.limit);
            let (items, next) = if receipts_scope {
                let before = match &cursor {
                    Some((head, position)) => {
                        let before = Uuid::parse_str(position)
                            .ok()
                            .filter(|value| value.to_string() == *position)
                            .ok_or_else(|| Error::invalid("Malformed receipt keyset position"))?;
                        if head.latest_id.is_none() {
                            return Err(Error::invalid(
                                "A receipt cursor cannot address an empty snapshot",
                            ));
                        }
                        // UUIDs identify receipts; storage resolves their
                        // project-scoped durable ordinals and snapshot bounds.
                        Some(before)
                    }
                    None => None,
                };
                let receipts = self
                    .service
                    .production_receipts_snapshot_page(project_id, &snapshot, before, limit)
                    .map_err(storage_error)?;
                let continuation = if let Some(last) = receipts.last() {
                    let probe = self
                        .service
                        .production_receipts_snapshot_page(project_id, &snapshot, Some(last.id), 1)
                        .map_err(storage_error)?;
                    (!probe.is_empty()).then(|| last.id.to_string())
                } else {
                    None
                };
                // Raw driver payload may contain private execution details.
                // Expose only the bounded owner-app journal metadata, not
                // provider results, source paths or untrusted payload JSON.
                let items = receipts
                    .into_iter()
                    .map(|receipt| {
                        json!({
                            "id": receipt.id.to_string(),
                            "generation": receipt.generation.to_string(),
                            "revision": receipt.revision.to_string(),
                            "request_id": receipt.request_id,
                            "request_sha256": receipt.request_sha256,
                            "command": receipt.command,
                            "stage": receipt.stage,
                            "created_at": receipt.created_at
                        })
                    })
                    .collect::<Vec<_>>();
                let next = continuation
                    .map(|position| {
                        production_cursor(query, &version, namespace, &snapshot, &position)
                    })
                    .transpose()?;
                (items, next)
            } else {
                let jobs = self
                    .service
                    .production_jobs_snapshot(project_id, &snapshot)
                    .map_err(storage_error)?;
                let offset = if let Some((_, position)) = &cursor {
                    position
                        .parse::<usize>()
                        .ok()
                        .filter(|offset| {
                            *offset > 0 && *offset < jobs.len() && offset.to_string() == *position
                        })
                        .ok_or_else(|| {
                            Error::invalid("Production job cursor does not make progress")
                        })?
                } else {
                    0
                };
                let end = offset.saturating_add(limit).min(jobs.len());
                let items = jobs[offset..end]
                    .iter()
                    .map(|job| serde_json::to_value(job).unwrap_or(Value::Null))
                    .collect::<Vec<_>>();
                let next = if end < jobs.len() {
                    Some(production_cursor(
                        query,
                        &version,
                        namespace,
                        &snapshot,
                        &end.to_string(),
                    )?)
                } else {
                    None
                };
                (items, next)
            };
            // Both creative and independent production watermarks must still
            // match at the end. New status receipts otherwise make historical
            // job rows look like the latest driver state without consent.
            let observed_again = self.service.project(project_id).map_err(storage_error)?;
            if observed_again.generation != project.generation
                || observed_again.revision != project.revision
                || self
                    .service
                    .production_receipt_watermark(project_id)
                    .map_err(storage_error)?
                    != snapshot
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Creative revision or production receipt stream changed during enumeration",
                ));
            }
            let page = ObservationPage {
                version,
                scope: query.scope.clone(),
                items,
                complete: next.is_none(),
                next,
            };
            page.validate_for(query)?;
            return Ok(page);
        }

        // Project-owned scopes enumerate *all* elements over revision-bound
        // pages. The prior take(limit)+complete:true pattern silently hid
        // every scene/object/review past the first page from native agents.
        let page = match query.scope.as_str() {
            "creative-patches" => paginate_project_observation(
                query,
                &version,
                project.production_design.patches.len(),
                |index| {
                    serde_json::to_value(&project.production_design.patches[index])
                        .unwrap_or(Value::Null)
                },
            )?,
            "production-plan" => paginate_project_observation(
                query,
                &version,
                usize::from(project.production_design.plan.is_some()),
                |_| serde_json::to_value(&project.production_design.plan).unwrap_or(Value::Null),
            )?,
            "components" => paginate_project_observation(
                query,
                &version,
                project.production_design.heroes.len(),
                |index| {
                    serde_json::to_value(&project.production_design.heroes[index])
                        .unwrap_or(Value::Null)
                },
            )?,
            "native-capsules" => paginate_project_observation(
                query,
                &version,
                project.production_design.capsules.len(),
                |index| {
                    serde_json::to_value(&project.production_design.capsules[index])
                        .unwrap_or(Value::Null)
                },
            )?,
            "summary" => paginate_project_observation(query, &version, 1, |_| {
                json!({
                    "id": project.id.to_string(),
                    "title": project.title,
                    "project_schema": project.schema_version,
                    "write_schema": motionwright_domain::PROJECT_SCHEMA_VERSION,
                    "state": project.state,
                    "revision": project.revision.to_string(),
                    "active_branch": project.active_branch.to_string(),
                    "scene_count": project.scenes.len(),
                    "asset_count": project.assets.len(),
                    "lock_count": project.locks.len(),
                    "deliverable_count": project.deliverables.len()
                })
            })?,
            "timeline" => {
                paginate_project_observation(query, &version, project.scenes.len(), |index| {
                    let scene = &project.scenes[index];
                    json!({
                        "id": scene.id.to_string(),
                        "name": scene.name,
                        "objective": scene.objective,
                        "start": scene.start,
                        "duration": scene.duration,
                        "renderer": scene.renderer,
                        "status": scene.status,
                        "beats": scene.beats.len(),
                        "nodes": scene.nodes.len()
                    })
                })?
            }
            "brief" => paginate_project_observation(query, &version, 1, |_| {
                serde_json::to_value(&project.brief).unwrap_or(Value::Null)
            })?,
            "narrative" => paginate_project_observation(query, &version, 1, |_| {
                serde_json::to_value(&project.narrative).unwrap_or(Value::Null)
            })?,
            "audio" => paginate_project_observation(query, &version, 1, |_| {
                serde_json::to_value(&project.audio).unwrap_or(Value::Null)
            })?,
            "deliverables" => paginate_project_observation(
                query,
                &version,
                project.deliverables.len(),
                |index| serde_json::to_value(&project.deliverables[index]).unwrap_or(Value::Null),
            )?,
            "visual-language" => paginate_project_observation(query, &version, 1, |_| {
                serde_json::to_value(&project.visual_language).unwrap_or(Value::Null)
            })?,
            "canvas" => {
                paginate_project_observation(query, &version, project.scenes.len(), |index| {
                    let scene = &project.scenes[index];
                    json!({
                        "scene_id": scene.id.to_string(),
                        "scene_name": scene.name,
                        "camera": scene.camera,
                        "nodes": scene.nodes
                    })
                })?
            }
            "alternatives" => paginate_project_observation(
                query,
                &version,
                project.proposal_sets.len(),
                |index| serde_json::to_value(&project.proposal_sets[index]).unwrap_or(Value::Null),
            )?,
            "locks" => {
                paginate_project_observation(query, &version, project.locks.len(), |index| {
                    serde_json::to_value(&project.locks[index]).unwrap_or(Value::Null)
                })?
            }
            "branches" => {
                paginate_project_observation(query, &version, project.branches.len(), |index| {
                    serde_json::to_value(&project.branches[index]).unwrap_or(Value::Null)
                })?
            }
            "reviews" => {
                paginate_project_observation(query, &version, project.reviews.len(), |index| {
                    serde_json::to_value(&project.reviews[index]).unwrap_or(Value::Null)
                })?
            }
            "merges" => {
                paginate_project_observation(query, &version, project.merges.len(), |index| {
                    serde_json::to_value(&project.merges[index]).unwrap_or(Value::Null)
                })?
            }
            _ => {
                return Err(Error::new(
                    ErrorCode::NotFound,
                    "Unknown Motionwright observation scope",
                ));
            }
        };
        // An agent must never observe a slice as "current" if another editor
        // committed while this page was being constructed.
        let latest = self.service.project(project_id).map_err(storage_error)?;
        if latest.generation != project.generation || latest.revision != project.revision {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed during application scope observation",
            ));
        }
        Ok(page)
    }
}

// Private candidate application contract: epoch 1 is one nonexpiring namespace.
// It is not APPLY_REQUEST_ENCODING_V1 and is never advanced to invent expiry.
// The qualified key is a deterministic selector over verified V1 receipt facts,
// not an issuance registry, authentication token, or permission to retry.
const APPLY_RECOVERY_EPOCH: u64 = 1;
const APPLY_RECOVERY_PREFIX: &str = "mw1:";
const MAX_APPLY_RECOVERY_RAW_KEY_BYTES: usize = 87;

fn validate_apply_recovery_raw_key(raw: &str) -> NativeResult<()> {
    if raw.is_empty()
        || raw.len() > MAX_APPLY_RECOVERY_RAW_KEY_BYTES
        || raw.chars().any(char::is_control)
    {
        return Err(Error::invalid(
            "Recovery requires a request key of 1..=87 UTF-8 bytes",
        ));
    }
    Ok(())
}

fn apply_recovery_requested(args: &Value, raw_key: &str) -> NativeResult<bool> {
    let requested = match args.get("include_recovery") {
        None | Some(Value::Bool(false)) => false,
        Some(Value::Bool(true)) => true,
        Some(_) => return Err(Error::invalid("include_recovery must be a boolean")),
    };
    // Legacy calls retain their complete existing request-key behavior.
    if requested {
        validate_apply_recovery_raw_key(raw_key)?;
    }
    Ok(requested)
}

fn apply_recovery_identity(
    id: Uuid,
    generation: Uuid,
    raw_key: &str,
    change: &Change,
) -> NativeResult<RequestIdentity> {
    validate_apply_recovery_raw_key(raw_key)?;
    if id.is_nil() || generation.is_nil() {
        return Err(Error::invalid(
            "Recovery requires a nonnil project and generation",
        ));
    }
    let identity = RequestIdentity {
        resource: format!("project:{id}"),
        epoch: APPLY_RECOVERY_EPOCH,
        key: format!("{APPLY_RECOVERY_PREFIX}{generation}:{raw_key}"),
        request_sha256: apply_request_digest_v1(id, change).map_err(storage_error)?,
    };
    identity.validate()?;
    Ok(identity)
}

fn decode_apply_recovery_identity(request: &RequestIdentity) -> NativeResult<(Uuid, Uuid, &str)> {
    request.validate()?;
    if request.epoch != APPLY_RECOVERY_EPOCH {
        return Err(Error::invalid(
            "Unsupported Motionwright recovery namespace epoch",
        ));
    }
    let project_id = project_id_from_resource(&request.resource)?;
    if project_id.is_nil() || request.resource != format!("project:{project_id}") {
        return Err(Error::invalid(
            "Recovery requires a canonical project resource",
        ));
    }
    let (generation, raw_key) = request
        .key
        .strip_prefix(APPLY_RECOVERY_PREFIX)
        .and_then(|qualified| qualified.split_once(':'))
        .ok_or_else(|| Error::invalid("Malformed Motionwright recovery key"))?;
    let parsed =
        Uuid::parse_str(generation).map_err(|_| Error::invalid("Malformed recovery generation"))?;
    if parsed.is_nil() || parsed.to_string() != generation {
        return Err(Error::invalid(
            "Recovery generation must be a canonical nonnil UUID",
        ));
    }
    validate_apply_recovery_raw_key(raw_key)?;
    Ok((project_id, parsed, raw_key))
}

fn apply_result_value(outcome: &motionwright_storage::ApplyOutcome) -> Value {
    json!({
        "resource": outcome.project.resource_key(),
        "generation": outcome.project.generation.to_string(),
        "revision": outcome.project.revision.to_string(),
        "previous_revision": outcome.previous_revision.to_string(),
        "request_id": outcome.request_id,
        "replayed": outcome.replayed,
        "project": outcome.project
    })
}

fn apply_completion(
    result: std::result::Result<motionwright_storage::ApplyOutcome, StorageError>,
) -> Completion {
    match result {
        Ok(outcome) => Completion::Applied(apply_result_value(&outcome)),
        // The stored result may describe an already committed original call.
        // Failing to authenticate it is not evidence that the call did not apply.
        Err(error @ StorageError::InvalidApplyReceipt(_)) => {
            Completion::Uncertain(storage_error(error))
        }
        Err(error) => Completion::NotApplied(storage_error(error)),
    }
}

// Constructed only by preflight from the actual writer inputs. Its metadata is
// fully validated and serialized before any Store apply or Graph permit effect.
// The completion path introduces no fallible decode, qualifier or new refusal.
struct PreparedApplyRecovery {
    metadata: Value,
}

fn prepare_apply_recovery(
    id: Uuid,
    generation: Uuid,
    raw_key: &str,
    change: &Change,
) -> NativeResult<PreparedApplyRecovery> {
    let identity = apply_recovery_identity(id, generation, raw_key, change)?;
    Ok(PreparedApplyRecovery {
        metadata: serde_json::to_value(identity)?,
    })
}

fn apply_completion_with_recovery(
    result: std::result::Result<motionwright_storage::ApplyOutcome, StorageError>,
    recovery: Option<PreparedApplyRecovery>,
) -> Completion {
    // The original canonical Store/Graph path binds the same project,
    // generation, request key and typed Change. Preserve its actual terminal.
    match apply_completion(result) {
        Completion::Applied(mut value) => {
            if let Some(prepared) = recovery {
                // The emission flag is not persisted or part of the intent hash.
                // This infallible attachment changes none of the seven commit
                // fields, including the original invocation's replayed flag.
                value["recovery"] = prepared.metadata;
            }
            Completion::Applied(value)
        }
        original => original,
    }
}

// Translate only an already obtained canonical Store observation. This helper
// supplies no authority: MotionwrightRecovery performs the genuine current
// resource/generation checks and reads the admitted Store before calling it.
fn apply_recovery_record(
    request: &RequestIdentity,
    observation: ApplyResultObservation,
) -> NativeResult<RecoveryRecord> {
    let record = match observation {
        ApplyResultObservation::KnownVerifiedV1 { outcome, .. } => {
            // Historical output is exactly the original seven-field projection.
            // Optional response metadata never becomes part of stored history.
            RecoveryRecord::Recorded {
                identity: request.clone(),
                result: apply_result_value(&outcome),
            }
        }
        ApplyResultObservation::NotFound => RecoveryRecord::OutcomeUnknown {
            identity: request.clone(),
        },
        ApplyResultObservation::LegacyUnverified => {
            // This is a known refusal of this read, not a statement that the
            // historical mutation did not apply or permission to retry it.
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Historical receipt lacks a verified V1 binding",
            ));
        }
        ApplyResultObservation::AuthorityMismatch => {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Historical project generation is no longer current",
            ));
        }
        ApplyResultObservation::Invalid { reason } => {
            return Err(match reason {
                ApplyReceiptInvalid::RequestMismatch => {
                    Error::new(ErrorCode::Conflict, "Historical request digest differs")
                }
                ApplyReceiptInvalid::ReceiptIntegrity => Error::new(
                    ErrorCode::BackendFailed,
                    "Historical receipt validation failed",
                ),
                ApplyReceiptInvalid::UnsupportedContract => Error::new(
                    ErrorCode::Unsupported,
                    "Historical receipt contract is unsupported",
                ),
            });
        }
    };
    // Validate the complete existing SDK record without truncating the stored
    // ApplyOutcome. A bounded read refusal never authorizes a mutation replay.
    record.validate_for(request)?;
    Ok(record)
}

struct MotionwrightRecovery {
    service: StudioService,
}

#[async_trait]
impl RecoveryProvider for MotionwrightRecovery {
    async fn lookup(
        &self,
        request: &RequestIdentity,
        context: &CallContext,
    ) -> NativeResult<RecoveryRecord> {
        context.check_cancelled()?;
        let (project_id, generation, raw_key) = decode_apply_recovery_identity(request)?;
        let current = context.expected().ok_or_else(|| {
            Error::new(
                ErrorCode::StaleReference,
                "Recovery requires a current observed project",
            )
        })?;
        current.validate()?;
        if current.resource != request.resource || current.generation != generation.to_string() {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Historical request and current project generation differ",
            ));
        }
        context.check_cancelled()?;
        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        graph_store_provider::trace_native_recovery_lookup(request, context)?;
        let observation = {
            let shared = self.service.store();
            let mut store = shared.lock();
            store
                .lookup_apply_result_v1(
                    project_id,
                    generation,
                    raw_key,
                    &request.request_sha256,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .map_err(storage_error)?
        };
        context.check_cancelled()?;
        let record = apply_recovery_record(request, observation)?;
        context.check_cancelled()?;
        Ok(record)
    }
}

#[cfg(test)]
mod apply_receipt_completion_tests;

#[derive(Clone)]
struct ApplyHandler {
    service: StudioService,
    operation: OperationKind,
}

#[derive(Clone, Copy)]
enum OperationKind {
    UndoCreativePatch,
    ApplyBrandCampaign,
    RecordNarrationTake,
    ReleaseNarrationTake,
    UpsertProductHero,
    DetachProductHero,
    UpsertProductStage,
    DetachProductStage,
    UpsertCameraReframe,
    DetachCameraReframe,
    BindStageCapture,
    DetachStageCapture,
    UpsertProceduralField,
    DetachProceduralField,
    ProposeComponentDistillation,
    ReviewComponentDistillation,
    SetProductionPlan,
    UpsertMeasuredTable,
    UpsertMeasuredBinding,
    DetachMeasuredBinding,
    UpsertNativeCapsule,
    RegisterCaptureSource,
    RegisterCreativeEvaluator,
    EvaluateCreativeScene,
    EditPaidGeneration,
    RegisterIdentityOrigin,
    RevokeIdentityOrigin,
    ApplyCreativePatch,
    RenameProject,
    SetBrief,
    AddClaim,
    SetNarrativePremise,
    AddScene,
    ApplyOtioOwnerCutReview,
    MoveScene,
    UpdateSceneObjective,
    SetSceneRenderer,
    SetSceneStatus,
    SetSceneDuration,
    SnapSceneCutToMusic,
    ConformScenesToMusic,
    AddCanvasNode,
    RemoveCanvasNode,
    TransformCanvasNode,
    SetCanvasKeyframe,
    SetCanvasPositionKeyframe,
    SetCanvasLinearPositionMotion,
    SetCanvasOutCubicPositionMotion,
    RemoveCanvasKeyframe,
    UpdateCanvasText,
    UpdateCanvasStyle,
    ReparentCanvasNode,
    SetCanvasRelations,
    SetNodePropertyLock,
    SetCamera,
    AddMarker,
    AddAsset,
    RemoveAsset,
    UpsertDeliverable,
    RemoveDeliverable,
    SetActiveVoiceTrack,
    UpsertTranscriptSegment,
    RemoveTranscriptSegment,
    UpsertAudioCue,
    RemoveAudioCue,
    UpsertSemanticSfx,
    RemoveSemanticSfx,
    SetMixIntent,
    SetVisualLanguage,
    AddProposalSet,
    SelectProposal,
    RecordModelInvocation,
    CreateBranch,
    CheckoutBranch,
    MergeBranch,
    AddReview,
    ResolveReview,
    ReopenReview,
    SetLock,
    RemoveLock,
}

#[async_trait]
impl OperationHandler for ApplyHandler {
    async fn invoke(&self, args: Value, context: &CallContext) -> Completion {
        if let Err(error) = context.check_cancelled() {
            return Completion::NotApplied(error);
        }
        if args.get("ref").and_then(Value::as_str).is_none() {
            return Completion::NotApplied(Error::invalid("Project ref is required"));
        }
        let include_recovery = match apply_recovery_requested(&args, context.request_id()) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(error),
        };
        // The caller's ref is an opaque Native SDK marker. NativeDriver validates and
        // binds it before invoking this handler; application code must never parse it
        // as a Motionwright resource. The Host-bound ResourceVersion is the authority
        // for selecting the application-owned project and checking the commit CAS.
        let Some(bound) = context.expected() else {
            return Completion::NotApplied(Error::new(
                ErrorCode::StaleReference,
                "Observed project base is required",
            ));
        };
        let project_id = match project_id_from_resource(&bound.resource) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(error),
        };
        let project = match self.service.project(project_id) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(storage_error(error)),
        };
        let expected = match expected_stamp(context, &project) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(error),
        };
        let change = match change_from_args(self.operation, &args) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(error),
        };
        if let Err(error) = guard_generic_audio_change(&change) {
            return Completion::NotApplied(error);
        }

        let recovery = if include_recovery {
            match prepare_apply_recovery(
                project_id,
                expected.generation,
                context.request_id(),
                &change,
            ) {
                Ok(identity) => Some(identity),
                Err(error) => return Completion::NotApplied(error),
            }
        } else {
            None
        };
        apply_completion_with_recovery(
            self.service
                .apply(project_id, &expected, context.request_id(), &change),
            recovery,
        )
    }
}

fn change_from_args(kind: OperationKind, args: &Value) -> NativeResult<Change> {
    fn string(args: &Value, name: &str, max: usize) -> NativeResult<String> {
        let value = args
            .get(name)
            .and_then(Value::as_str)
            .ok_or_else(|| Error::invalid(format!("{name} is required")))?;
        if value.trim().is_empty() || value.len() > max {
            return Err(Error::invalid(format!("{name} is out of bounds")));
        }
        Ok(value.to_owned())
    }
    fn uuid(args: &Value, name: &str) -> NativeResult<Uuid> {
        Uuid::parse_str(&string(args, name, 64)?)
            .map_err(|_| Error::invalid(format!("{name} is not a UUID")))
    }
    fn bounded_string(args: &Value, name: &str, max: usize) -> NativeResult<String> {
        let value = args
            .get(name)
            .and_then(Value::as_str)
            .ok_or_else(|| Error::invalid(format!("{name} is required")))?;
        if value.len() > max {
            return Err(Error::invalid(format!("{name} is out of bounds")));
        }
        Ok(value.to_owned())
    }
    fn number(args: &Value, name: &str) -> NativeResult<f64> {
        args.get(name)
            .and_then(Value::as_f64)
            .filter(|value| value.is_finite())
            .ok_or_else(|| Error::invalid(format!("{name} must be a finite number")))
    }
    fn strings(
        args: &Value,
        name: &str,
        max_items: usize,
        max_item: usize,
    ) -> NativeResult<Vec<String>> {
        let values: Vec<String> = serde_json::from_value(
            args.get(name)
                .cloned()
                .ok_or_else(|| Error::invalid(format!("{name} is required")))?,
        )
        .map_err(|_| Error::invalid(format!("{name} must be a string array")))?;
        if values.len() > max_items || values.iter().any(|value| value.len() > max_item) {
            return Err(Error::invalid(format!("{name} is out of bounds")));
        }
        Ok(values)
    }

    match kind {
        OperationKind::UndoCreativePatch => Ok(Change::UndoCreativePatch {
            patch_id: uuid(args, "patch_id")?,
        }),
        OperationKind::RecordNarrationTake => Ok(Change::RecordNarrationTake {
            expected_source_sha256: string(args, "expected_source_sha256", 64)?,
            expected_project_revision: args
                .get("expected_project_revision")
                .and_then(Value::as_u64)
                .ok_or_else(|| Error::invalid("exact original project revision is required"))?,
            reviewer: string(args, "reviewer", 200)?,
            reason: string(args, "reason", 2000)?,
        }),
        OperationKind::ReleaseNarrationTake => Ok(Change::ReleaseNarrationTake {
            expected_source_sha256: string(args, "expected_source_sha256", 64)?,
            reviewer: string(args, "reviewer", 200)?,
            reason: string(args, "reason", 2000)?,
        }),
        OperationKind::ApplyBrandCampaign => {
            Ok(Change::ApplyBrandCampaign {
                rollout: Box::new(
                    serde_json::from_value(args.get("rollout").cloned().ok_or_else(|| {
                        Error::invalid("brand campaign owner review is required")
                    })?)
                    .map_err(|_| Error::invalid("invalid bounded brand campaign rollout"))?,
                ),
            })
        }
        OperationKind::UpsertProductHero => Ok(Change::UpsertProductHero {
            instance_id: uuid(args, "instance_id")?,
            scene_id: uuid(args, "scene_id")?,
            config: serde_json::from_value(
                args.get("config")
                    .cloned()
                    .ok_or_else(|| Error::invalid("config is required"))?,
            )
            .map_err(|_| Error::invalid("invalid ProductHeroReveal config"))?,
        }),
        OperationKind::DetachProductHero => Ok(Change::DetachProductHero {
            instance_id: uuid(args, "instance_id")?,
        }),
        OperationKind::UpsertProductStage => Ok(Change::UpsertProductStage {
            instance_id: uuid(args, "instance_id")?,
            scene_id: uuid(args, "scene_id")?,
            config: serde_json::from_value(
                args.get("config")
                    .cloned()
                    .ok_or_else(|| Error::invalid("config is required"))?,
            )
            .map_err(|_| Error::invalid("invalid owned ProductStage configuration"))?,
        }),
        OperationKind::DetachProductStage => Ok(Change::DetachProductStage {
            instance_id: uuid(args, "instance_id")?,
        }),
        OperationKind::UpsertCameraReframe => Ok(Change::UpsertCameraReframe {
            reframe: serde_json::from_value(
                args.get("reframe")
                    .cloned()
                    .ok_or_else(|| Error::invalid("camera reframe object is required"))?,
            )
            .map_err(|_| Error::invalid("invalid owned native camera reframe contract"))?,
        }),
        OperationKind::DetachCameraReframe => Ok(Change::DetachCameraReframe {
            id: uuid(args, "id")?,
        }),
        OperationKind::BindStageCapture => Ok(Change::BindStageCapture {
            stage_id: uuid(args, "stage_id")?,
            capture_id: uuid(args, "capture_id")?,
        }),
        OperationKind::DetachStageCapture => Ok(Change::DetachStageCapture {
            stage_id: uuid(args, "stage_id")?,
        }),
        OperationKind::UpsertProceduralField => Ok(Change::UpsertProceduralField {
            instance_id: uuid(args, "instance_id")?,
            scene_id: uuid(args, "scene_id")?,
            config: serde_json::from_value(
                args.get("config")
                    .cloned()
                    .ok_or_else(|| Error::invalid("config is required"))?,
            )
            .map_err(|_| Error::invalid("invalid procedural field config"))?,
        }),
        OperationKind::DetachProceduralField => Ok(Change::DetachProceduralField {
            instance_id: uuid(args, "instance_id")?,
        }),
        OperationKind::ProposeComponentDistillation => {
            Ok(Change::ProposeComponentDistillation {
                input: serde_json::from_value(args.get("input").cloned().ok_or_else(|| {
                    Error::invalid("source-bound distillation input is required")
                })?)
                .map_err(|_| Error::invalid("invalid bounded distillation proposal"))?,
            })
        }
        OperationKind::ReviewComponentDistillation => Ok(Change::ReviewComponentDistillation {
            id: uuid(args, "id")?,
            review: serde_json::from_value(
                args.get("review")
                    .cloned()
                    .ok_or_else(|| Error::invalid("distillation review is required"))?,
            )
            .map_err(|_| Error::invalid("invalid source-bound component review"))?,
        }),
        OperationKind::SetProductionPlan => Ok(Change::SetProductionPlan {
            plan: serde_json::from_value(
                args.get("plan")
                    .cloned()
                    .ok_or_else(|| Error::invalid("plan is required"))?,
            )
            .map_err(|_| Error::invalid("invalid ProductionPlan"))?,
        }),
        OperationKind::RegisterCreativeEvaluator => Ok(Change::RegisterCreativeEvaluator {
            bundle: serde_json::from_value(
                args.get("bundle")
                    .cloned()
                    .ok_or_else(|| Error::invalid("creative evaluator bundle is required"))?,
            )
            .map_err(|_| Error::invalid("invalid strictly data-only creative evaluator bundle"))?,
        }),
        OperationKind::EvaluateCreativeScene => Ok(Change::EvaluateCreativeScene {
            evaluator_id: uuid(args, "evaluator_id")?,
            evaluator_version: serde_json::from_value(
                args.get("evaluator_version")
                    .cloned()
                    .ok_or_else(|| Error::invalid("evaluator version is required"))?,
            )
            .map_err(|_| Error::invalid("invalid evaluator version"))?,
            scene_id: uuid(args, "scene_id")?,
        }),
        OperationKind::RegisterCaptureSource => Ok(Change::RegisterCaptureSource {
            record: serde_json::from_value(
                args.get("record")
                    .cloned()
                    .ok_or_else(|| Error::invalid("capture record is required"))?,
            )
            .map_err(|_| Error::invalid("invalid bounded first-party capture source record"))?,
        }),
        OperationKind::EditPaidGeneration => Ok(Change::EditPaidGeneration {
            edit: serde_json::from_value(args.get("edit").cloned().ok_or_else(|| {
                Error::invalid("source-bound generation journal edit is required")
            })?)
            .map_err(|_| Error::invalid("invalid non-executable generation journal edit"))?,
        }),
        OperationKind::RegisterIdentityOrigin => Ok(Change::RegisterIdentityOrigin {
            record: serde_json::from_value(
                args.get("record")
                    .cloned()
                    .ok_or_else(|| Error::invalid("identity declaration is required"))?,
            )
            .map_err(|_| Error::invalid("invalid scoped identity source declaration"))?,
        }),
        OperationKind::RevokeIdentityOrigin => Ok(Change::RevokeIdentityOrigin {
            revocation: serde_json::from_value(
                args.get("revocation")
                    .cloned()
                    .ok_or_else(|| Error::invalid("identity revocation is required"))?,
            )
            .map_err(|_| Error::invalid("invalid immutable identity revocation"))?,
        }),
        OperationKind::UpsertMeasuredTable => Ok(Change::UpsertMeasuredTable {
            table: serde_json::from_value(
                args.get("table")
                    .cloned()
                    .ok_or_else(|| Error::invalid("table is required"))?,
            )
            .map_err(|_| Error::invalid("invalid versioned source-bound measured table"))?,
        }),
        OperationKind::UpsertMeasuredBinding => Ok(Change::UpsertMeasuredBinding {
            binding_id: uuid(args, "binding_id")?,
            scene_id: uuid(args, "scene_id")?,
            node_id: uuid(args, "node_id")?,
            table_id: uuid(args, "table_id")?,
            datum_key: string(args, "datum_key", 48)?,
            view: serde_json::from_value(
                args.get("view")
                    .cloned()
                    .ok_or_else(|| Error::invalid("view is required"))?,
            )
            .map_err(|_| Error::invalid("invalid explicit measurement/axis view"))?,
        }),
        OperationKind::DetachMeasuredBinding => Ok(Change::DetachMeasuredBinding {
            binding_id: uuid(args, "binding_id")?,
        }),
        OperationKind::UpsertNativeCapsule => Ok(Change::UpsertNativeCapsule {
            capsule: serde_json::from_value(
                args.get("capsule")
                    .cloned()
                    .ok_or_else(|| Error::invalid("capsule is required"))?,
            )
            .map_err(|_| Error::invalid("invalid native source capsule"))?,
        }),
        OperationKind::ApplyCreativePatch => Ok(Change::ApplyCreativePatch {
            patch: serde_json::from_value(
                args.get("patch")
                    .cloned()
                    .ok_or_else(|| Error::invalid("patch is required"))?,
            )
            .map_err(|_| Error::invalid("invalid scoped creative patch"))?,
        }),
        OperationKind::RenameProject => Ok(Change::RenameProject {
            title: string(args, "title", 200)?,
        }),
        OperationKind::SetBrief => Ok(Change::SetBrief {
            objective: bounded_string(args, "objective", 8_000)?,
            audience: bounded_string(args, "audience", 2_000)?,
            constraints: strings(args, "constraints", 128, 1_000)?,
            exclusions: strings(args, "exclusions", 128, 1_000)?,
        }),
        OperationKind::AddClaim => Ok(Change::AddClaim {
            text: bounded_string(args, "text", 4_000)?,
            source: serde_json::from_value(args.get("source").cloned().unwrap_or(Value::Null))
                .map_err(|_| Error::invalid("invalid versioned claim source reference"))?,
            context: bounded_string(args, "context", 4_000)?,
            source_revision: serde_json::from_value(
                args.get("source_revision").cloned().unwrap_or(Value::Null),
            )
            .map_err(|_| Error::invalid("claim source revision must be a string or null"))?,
        }),
        OperationKind::SetNarrativePremise => Ok(Change::SetNarrativePremise {
            premise: bounded_string(args, "premise", 12_000)?,
        }),
        OperationKind::AddScene => {
            let duration_seconds = args
                .get("duration_seconds")
                .and_then(Value::as_i64)
                .filter(|value| (1..=86_400).contains(value))
                .ok_or_else(|| Error::invalid("duration_seconds is out of bounds"))?;
            Ok(Change::AddScene {
                name: string(args, "name", 160)?,
                objective: args
                    .get("objective")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .chars()
                    .take(4000)
                    .collect(),
                duration_seconds,
            })
        }
        OperationKind::ApplyOtioOwnerCutReview => {
            Ok(Change::ApplyOtioOwnerCutReview {
                review: Box::new(
                    serde_json::from_value(args.get("review").cloned().ok_or_else(|| {
                        Error::invalid("owner-reviewed OTIO preview is required")
                    })?)
                    .map_err(|_| Error::invalid("invalid bounded OTIO cut owner review"))?,
                ),
            })
        }
        OperationKind::MoveScene => Ok(Change::MoveScene {
            scene_id: uuid(args, "scene_id")?,
            to_index: args
                .get("to_index")
                .and_then(Value::as_u64)
                .and_then(|value| usize::try_from(value).ok())
                .ok_or_else(|| Error::invalid("to_index is required"))?,
        }),
        OperationKind::UpdateSceneObjective => Ok(Change::UpdateSceneObjective {
            scene_id: uuid(args, "scene_id")?,
            objective: string(args, "objective", 4000)?,
        }),
        OperationKind::SetSceneRenderer => {
            let renderer = serde_json::from_value(
                args.get("renderer")
                    .cloned()
                    .ok_or_else(|| Error::invalid("renderer is required"))?,
            )
            .map_err(|_| Error::invalid("renderer is not supported"))?;
            Ok(Change::SetSceneRenderer {
                scene_id: uuid(args, "scene_id")?,
                renderer,
            })
        }
        OperationKind::SetSceneStatus => {
            let status = serde_json::from_value(
                args.get("status")
                    .cloned()
                    .ok_or_else(|| Error::invalid("status is required"))?,
            )
            .map_err(|_| Error::invalid("scene status is invalid"))?;
            Ok(Change::SetSceneStatus {
                scene_id: uuid(args, "scene_id")?,
                status,
            })
        }
        OperationKind::ConformScenesToMusic => {
            let targets: Vec<MusicConformTarget> = serde_json::from_value(
                args.get("targets")
                    .cloned()
                    .ok_or_else(|| Error::invalid("music conform targets are required"))?,
            )
            .map_err(|_| {
                Error::invalid("music conform targets must be ordered scene IDs and beat indices")
            })?;
            if targets.len() < 2 || targets.len() > MAX_MUSIC_CONFORM_TARGETS {
                return Err(Error::invalid(
                    "music conform requires 2–16 explicit targets",
                ));
            }
            Ok(Change::ConformScenesToMusic {
                targets,
                source_sha256: string(args, "source_sha256", 64)?,
                base_revision: args
                    .get("base_revision")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| Error::invalid("base_revision is required"))?,
                acknowledge_absolute_anchors: args
                    .get("acknowledge_absolute_anchors")
                    .and_then(Value::as_bool)
                    .ok_or_else(|| Error::invalid("explicit impact acknowledgement is required"))?,
            })
        }
        OperationKind::SnapSceneCutToMusic => Ok(Change::SnapSceneCutToMusic {
            scene_id: uuid(args, "scene_id")?,
            beat_index: args
                .get("beat_index")
                .and_then(Value::as_u64)
                .filter(|index| *index < 20_000)
                .and_then(|index| usize::try_from(index).ok())
                .ok_or_else(|| Error::invalid("beat_index must be within the music grid"))?,
            source_sha256: string(args, "source_sha256", 64)?,
            base_revision: args
                .get("base_revision")
                .and_then(Value::as_u64)
                .ok_or_else(|| Error::invalid("base_revision is required"))?,
            acknowledge_absolute_anchors: args
                .get("acknowledge_absolute_anchors")
                .and_then(Value::as_bool)
                .ok_or_else(|| Error::invalid("explicit acknowledgement is required"))?,
        }),
        OperationKind::SetSceneDuration => {
            let duration = serde_json::from_value(
                args.get("duration")
                    .cloned()
                    .ok_or_else(|| Error::invalid("duration is required"))?,
            )
            .map_err(|_| Error::invalid("scene duration is invalid"))?;
            Ok(Change::SetSceneDuration {
                scene_id: uuid(args, "scene_id")?,
                duration,
            })
        }
        OperationKind::AddCanvasNode => {
            let node = serde_json::from_value(
                args.get("node")
                    .cloned()
                    .ok_or_else(|| Error::invalid("node is required"))?,
            )
            .map_err(|_| Error::invalid("canvas node is invalid"))?;
            Ok(Change::AddCanvasNode {
                scene_id: uuid(args, "scene_id")?,
                node,
            })
        }
        OperationKind::RemoveCanvasNode => Ok(Change::RemoveCanvasNode {
            scene_id: uuid(args, "scene_id")?,
            node_id: uuid(args, "node_id")?,
        }),
        OperationKind::TransformCanvasNode => {
            let transform = serde_json::from_value(
                args.get("transform")
                    .cloned()
                    .ok_or_else(|| Error::invalid("transform is required"))?,
            )
            .map_err(|_| Error::invalid("canvas transform is invalid"))?;
            Ok(Change::TransformCanvasNode {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                transform,
            })
        }
        OperationKind::SetCanvasKeyframe => {
            let keyframe = serde_json::from_value(
                args.get("keyframe")
                    .cloned()
                    .ok_or_else(|| Error::invalid("keyframe is required"))?,
            )
            .map_err(|_| Error::invalid("canvas keyframe is invalid"))?;
            Ok(Change::SetCanvasKeyframe {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                keyframe,
            })
        }
        OperationKind::SetCanvasPositionKeyframe => {
            let at = serde_json::from_value(
                args.get("at")
                    .cloned()
                    .ok_or_else(|| Error::invalid("position keyframe time is required"))?,
            )
            .map_err(|_| Error::invalid("position keyframe time is invalid"))?;
            let interpolation =
                serde_json::from_value(args.get("interpolation").cloned().ok_or_else(|| {
                    Error::invalid("position keyframe interpolation is required")
                })?)
                .map_err(|_| Error::invalid("position keyframe interpolation is invalid"))?;
            Ok(Change::SetCanvasPositionKeyframe {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                at,
                x: number(args, "x")?,
                y: number(args, "y")?,
                interpolation,
            })
        }
        OperationKind::SetCanvasLinearPositionMotion
        | OperationKind::SetCanvasOutCubicPositionMotion => {
            let end_frame = args.get("end_frame")
                .and_then(Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .filter(|frame| (1..=36_000).contains(frame))
                .ok_or_else(|| Error::invalid(
                    "Native linear position move endpoint must be a bounded positive frame index",
                ))?;
            let scene_id = uuid(args, "scene_id")?;
            let node_id = uuid(args, "node_id")?;
            let deliverable_id = uuid(args, "deliverable_id")?;
            let start_x = number(args, "start_x")?;
            let start_y = number(args, "start_y")?;
            if matches!(kind, OperationKind::SetCanvasOutCubicPositionMotion) {
                Ok(Change::SetCanvasOutCubicPositionMotion {
                    scene_id,
                    node_id,
                    deliverable_id,
                    start_x,
                    start_y,
                    end_frame,
                })
            } else {
                Ok(Change::SetCanvasLinearPositionMotion {
                    scene_id,
                    node_id,
                    deliverable_id,
                    start_x,
                    start_y,
                    end_frame,
                })
            }
        }
        OperationKind::RemoveCanvasKeyframe => {
            let at = serde_json::from_value(
                args.get("at")
                    .cloned()
                    .ok_or_else(|| Error::invalid("keyframe time is required"))?,
            )
            .map_err(|_| Error::invalid("canvas keyframe time is invalid"))?;
            let property = serde_json::from_value(
                args.get("property")
                    .cloned()
                    .ok_or_else(|| Error::invalid("keyframe property is required"))?,
            )
            .map_err(|_| Error::invalid("canvas keyframe property is invalid"))?;
            Ok(Change::RemoveCanvasKeyframe {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                at,
                property,
            })
        }
        OperationKind::UpdateCanvasText => {
            let text: Option<String> = serde_json::from_value(
                args.get("text")
                    .cloned()
                    .ok_or_else(|| Error::invalid("text is required, null clears it"))?,
            )
            .map_err(|_| Error::invalid("text must be a string or null"))?;
            if text.as_ref().is_some_and(|value| value.len() > 100_000) {
                return Err(Error::invalid("text is out of bounds"));
            }
            Ok(Change::UpdateCanvasText {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                text,
            })
        }
        OperationKind::UpdateCanvasStyle => {
            let style = serde_json::from_value(
                args.get("style")
                    .cloned()
                    .ok_or_else(|| Error::invalid("style is required"))?,
            )
            .map_err(|_| Error::invalid("canvas style is invalid"))?;
            Ok(Change::UpdateCanvasStyle {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                style,
            })
        }
        OperationKind::ReparentCanvasNode => {
            let parent_id = match args.get("parent_id") {
                None | Some(Value::Null) => None,
                Some(value) => Some(
                    Uuid::parse_str(
                        value
                            .as_str()
                            .ok_or_else(|| Error::invalid("parent_id must be a UUID or null"))?,
                    )
                    .map_err(|_| Error::invalid("parent_id must be a UUID or null"))?,
                ),
            };
            let z_index = args
                .get("z_index")
                .and_then(Value::as_i64)
                .and_then(|value| i32::try_from(value).ok())
                .ok_or_else(|| Error::invalid("z_index is required"))?;
            Ok(Change::ReparentCanvasNode {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                parent_id,
                z_index,
                preserve: None,
            })
        }
        OperationKind::SetCanvasRelations => {
            let relations = serde_json::from_value(
                args.get("relations")
                    .cloned()
                    .ok_or_else(|| Error::invalid("relations are required"))?,
            )
            .map_err(|_| Error::invalid("canvas relations are invalid"))?;
            Ok(Change::SetCanvasRelations {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                relations,
            })
        }
        OperationKind::SetNodePropertyLock => {
            let property = serde_json::from_value(
                args.get("property")
                    .cloned()
                    .ok_or_else(|| Error::invalid("property is required"))?,
            )
            .map_err(|_| Error::invalid("node property is invalid"))?;
            let locked = args
                .get("locked")
                .and_then(Value::as_bool)
                .ok_or_else(|| Error::invalid("locked is required"))?;
            Ok(Change::SetNodePropertyLock {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                property,
                locked,
            })
        }
        OperationKind::SetCamera => {
            let camera = serde_json::from_value(
                args.get("camera")
                    .cloned()
                    .ok_or_else(|| Error::invalid("camera is required"))?,
            )
            .map_err(|_| Error::invalid("camera is invalid"))?;
            Ok(Change::SetCamera {
                scene_id: uuid(args, "scene_id")?,
                camera,
            })
        }
        OperationKind::AddMarker => {
            let at = serde_json::from_value(
                args.get("at")
                    .cloned()
                    .ok_or_else(|| Error::invalid("at is required"))?,
            )
            .map_err(|_| Error::invalid("at is invalid"))?;
            Ok(Change::AddMarker {
                at,
                label: string(args, "label", 160)?,
            })
        }
        OperationKind::AddAsset => {
            let asset = serde_json::from_value(
                args.get("asset")
                    .cloned()
                    .ok_or_else(|| Error::invalid("asset is required"))?,
            )
            .map_err(|_| Error::invalid("asset is invalid"))?;
            Ok(Change::AddAsset { asset })
        }
        OperationKind::RemoveAsset => Ok(Change::RemoveAsset {
            asset_id: uuid(args, "asset_id")?,
        }),
        OperationKind::UpsertDeliverable => {
            let profile = serde_json::from_value(
                args.get("profile")
                    .cloned()
                    .ok_or_else(|| Error::invalid("profile is required"))?,
            )
            .map_err(|_| Error::invalid("deliverable profile is invalid"))?;
            Ok(Change::UpsertDeliverable { profile })
        }
        OperationKind::RemoveDeliverable => Ok(Change::RemoveDeliverable {
            profile_id: uuid(args, "profile_id")?,
        }),
        OperationKind::SetActiveVoiceTrack => Ok(Change::SetActiveVoiceTrack {
            track_id: uuid(args, "track_id")?,
        }),
        OperationKind::UpsertTranscriptSegment => {
            let segment: motionwright_domain::TranscriptSegment = serde_json::from_value(
                args.get("segment")
                    .cloned()
                    .ok_or_else(|| Error::invalid("segment is required"))?,
            )
            .map_err(|_| Error::invalid("transcript segment is invalid"))?;
            if matches!(
                segment.alignment,
                motionwright_domain::AlignmentEvidence::Measured { .. }
            ) {
                return Err(Error::invalid(
                    "measured transcript alignment is reserved for a qualified alignment boundary",
                ));
            }
            Ok(Change::UpsertTranscriptSegment { segment })
        }
        OperationKind::RemoveTranscriptSegment => Ok(Change::RemoveTranscriptSegment {
            segment_id: uuid(args, "segment_id")?,
        }),
        OperationKind::UpsertAudioCue => {
            let cue: motionwright_domain::AudioCue = serde_json::from_value(
                args.get("cue")
                    .cloned()
                    .ok_or_else(|| Error::invalid("cue is required"))?,
            )
            .map_err(|_| Error::invalid("audio cue is invalid"))?;
            if matches!(
                cue.evidence,
                motionwright_domain::CueEvidence::Measured
                    | motionwright_domain::CueEvidence::TranscriptAligned
            ) {
                return Err(Error::invalid(
                    "measured or transcript-aligned cue evidence is reserved for a qualified evidence boundary",
                ));
            }
            Ok(Change::UpsertAudioCue { cue })
        }
        OperationKind::RemoveAudioCue => Ok(Change::RemoveAudioCue {
            cue_id: uuid(args, "cue_id")?,
        }),
        OperationKind::UpsertSemanticSfx => Ok(Change::UpsertSemanticSfx {
            cue: serde_json::from_value(
                args.get("cue")
                    .cloned()
                    .ok_or_else(|| Error::invalid("semantic SFX cue is required"))?,
            )
            .map_err(|_| Error::invalid("invalid bounded original SFX source cue"))?,
        }),
        OperationKind::RemoveSemanticSfx => Ok(Change::RemoveSemanticSfx {
            cue_id: uuid(args, "cue_id")?,
        }),
        OperationKind::SetMixIntent => {
            let mix = serde_json::from_value(
                args.get("mix")
                    .cloned()
                    .ok_or_else(|| Error::invalid("mix is required"))?,
            )
            .map_err(|_| Error::invalid("mix intent is invalid"))?;
            Ok(Change::SetMixIntent { mix })
        }
        OperationKind::SetVisualLanguage => {
            let visual_language = serde_json::from_value(
                args.get("visual_language")
                    .cloned()
                    .ok_or_else(|| Error::invalid("visual_language is required"))?,
            )
            .map_err(|_| Error::invalid("visual language is invalid"))?;
            Ok(Change::SetVisualLanguage { visual_language })
        }
        OperationKind::AddProposalSet => {
            let proposal_set = serde_json::from_value(
                args.get("proposal_set")
                    .cloned()
                    .ok_or_else(|| Error::invalid("proposal_set is required"))?,
            )
            .map_err(|_| Error::invalid("proposal set is invalid"))?;
            Ok(Change::AddProposalSet { proposal_set })
        }
        OperationKind::SelectProposal => Ok(Change::SelectProposal {
            proposal_set_id: uuid(args, "proposal_set_id")?,
            proposal_id: uuid(args, "proposal_id")?,
        }),
        OperationKind::RecordModelInvocation => {
            let receipt = serde_json::from_value(
                args.get("receipt")
                    .cloned()
                    .ok_or_else(|| Error::invalid("model invocation receipt is required"))?,
            )
            .map_err(|_| Error::invalid("model invocation receipt is invalid"))?;
            Ok(Change::RecordModelInvocation { receipt })
        }
        OperationKind::CreateBranch => Ok(Change::CreateBranch {
            name: string(args, "name", 120)?,
        }),
        OperationKind::CheckoutBranch => Ok(Change::CheckoutBranch {
            branch_id: uuid(args, "branch_id")?,
        }),
        OperationKind::MergeBranch => Ok(Change::MergeBranch {
            source_branch_id: uuid(args, "source_branch_id")?,
        }),
        OperationKind::AddReview => {
            let kind = serde_json::from_value(
                args.get("kind")
                    .cloned()
                    .ok_or_else(|| Error::invalid("review kind is required"))?,
            )
            .map_err(|_| Error::invalid("review kind is invalid"))?;
            let parse_time =
                |name: &str| -> NativeResult<Option<motionwright_domain::RationalTime>> {
                    match args.get(name) {
                        None | Some(Value::Null) => Ok(None),
                        Some(value) => serde_json::from_value(value.clone())
                            .map(Some)
                            .map_err(|_| Error::invalid(format!("{name} is not a rational time"))),
                    }
                };
            let locale = match args.get("locale") {
                None | Some(Value::Null) => None,
                Some(value) => Some(
                    value
                        .as_str()
                        .filter(|value| value.len() <= 64)
                        .ok_or_else(|| Error::invalid("locale is out of bounds"))?
                        .to_owned(),
                ),
            };
            let profile_id = match args.get("profile_id") {
                None | Some(Value::Null) => None,
                Some(value) => Some(
                    Uuid::parse_str(
                        value
                            .as_str()
                            .ok_or_else(|| Error::invalid("profile_id must be a UUID or null"))?,
                    )
                    .map_err(|_| Error::invalid("profile_id must be a UUID or null"))?,
                ),
            };
            Ok(Change::AddReview {
                kind,
                resource: string(args, "resource", 512)?,
                body: string(args, "body", 8_000)?,
                start: parse_time("start")?,
                end: parse_time("end")?,
                locale,
                profile_id,
            })
        }
        OperationKind::ResolveReview => Ok(Change::ResolveReview {
            review_id: uuid(args, "review_id")?,
            resolution: string(args, "resolution", 4_000)?,
        }),
        OperationKind::ReopenReview => Ok(Change::ReopenReview {
            review_id: uuid(args, "review_id")?,
        }),
        OperationKind::SetLock => {
            let kind = serde_json::from_value(
                args.get("kind")
                    .cloned()
                    .ok_or_else(|| Error::invalid("kind is required"))?,
            )
            .map_err(|_| Error::invalid("lock kind is invalid"))?;
            Ok(Change::SetLock {
                resource: string(args, "resource", 512)?,
                kind,
                note: args
                    .get("note")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .chars()
                    .take(500)
                    .collect(),
            })
        }
        OperationKind::RemoveLock => Ok(Change::RemoveLock {
            lock_id: uuid(args, "lock_id")?,
        }),
    }
}

fn descriptor(name: &str, description: &str, mut input_schema: Value) -> OperationContract {
    input_schema["properties"]["include_recovery"] = json!({"type":"boolean"});
    OperationContract {
        descriptor: CommandDescriptor {
            name: format!("driver.{APP_ID}.{name}"),
            version: "1.0.0".into(),
            description: description.into(),
            input_schema,
            output_schema: json!({
                "type": "object",
                "required": ["resource", "generation", "revision", "request_id", "project"],
                "properties": {
                    "resource": {"type": "string"},
                    "generation": {"type": "string"},
                    "revision": {"type": "string"},
                    "previous_revision": {"type": "string"},
                    "request_id": {"type": "string"},
                    "replayed": {"type": "boolean"},
                    "project": {"type": "object"},
                    "recovery": {
                        "type":"object",
                        "required":["resource","epoch","key","request_sha256"],
                        "properties": {
                            "resource":{"type":"string","minLength":1,"maxLength":512},
                            "epoch":{"const":1},
                            "key":{"type":"string","minLength":1,"maxLength":128},
                            "request_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"}
                        },
                        "additionalProperties":false
                    }
                },
                "additionalProperties": false
            }),
            requires: vec![format!("driver:{APP_ID}")],
            risk: Risk::Mutating,
            idempotency: Idempotency::Idempotent,
            timeout_ms: 15_000,
            dry_run: false,
            interactive_consent: false,
            backends: vec![format!("driver:{APP_ID}")],
        },
        target: TargetRequirement::ObservedResource,
        commit: CommitSemantics::ApplicationTransaction,
        retry: RetrySemantics::DurableRequestKey,
        undo: UndoSemantics::None,
        cancellation: CancellationSemantics::BeforeEffects,
        atomic_revision_cas: true,
    }
}

fn schema(properties: Value, required: &[&str]) -> Value {
    json!({
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": false
    })
}

pub fn build_application(service: StudioService) -> NativeResult<Application> {
    build_application_with_graph_store(service, false)
}

fn build_application_with_graph_store(
    service: StudioService,
    mounted_graph_store: bool,
) -> NativeResult<Application> {
    let observer = Arc::new(MotionwrightObserver {
        service: service.clone(),
        mounted_graph_store,
    });
    let mut app = Application::new(APP_ID, APP_VERSION)?
        .require_resource_read_scope()
        .with_observer(observer)
        .with_recovery(Arc::new(MotionwrightRecovery {
            service: service.clone(),
        }));

    let operations = [
        (
            OperationKind::RenameProject,
            descriptor(
                "project.rename",
                "Rename a Motionwright project using transactional revision CAS",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "title": {"type":"string","minLength":1,"maxLength":200}
                    }),
                    &["ref", "title"],
                ),
            ),
        ),
        (
            OperationKind::SetBrief,
            descriptor(
                "brief.set",
                "Replace the bounded project brief while preserving revision history",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "objective": {"type":"string","maxLength":8000},
                        "audience": {"type":"string","maxLength":2000},
                        "constraints": {"type":"array","maxItems":128,"items":{"type":"string","maxLength":1000}},
                        "exclusions": {"type":"array","maxItems":128,"items":{"type":"string","maxLength":1000}}
                    }),
                    &["ref", "objective", "audience", "constraints", "exclusions"],
                ),
            ),
        ),
        (
            OperationKind::AddClaim,
            descriptor(
                "claim.add",
                "Append an authored first-party project evidence claim without granting external capture or verification",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "text":{"type":"string","minLength":1,"maxLength":4000},
                        "source":{"anyOf":[{"type":"null"},{"type":"object"}]},
                        "context":{"type":"string","maxLength":4000},
                        "source_revision":{"anyOf":[{"type":"null"},{"type":"string","maxLength":256}]}
                    }),
                    &["ref", "text", "source", "context", "source_revision"],
                ),
            ),
        ),
        (
            OperationKind::SetNarrativePremise,
            descriptor(
                "narrative.premise.set",
                "Update the project narrative premise without replacing scene realizations",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "premise": {"type":"string","maxLength":12000}
                    }),
                    &["ref", "premise"],
                ),
            ),
        ),
        (
            OperationKind::AddScene,
            descriptor(
                "scene.add",
                "Append a semantic scene to the Motionwright timeline",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "name": {"type":"string","minLength":1,"maxLength":160},
                        "objective": {"type":"string","maxLength":4000},
                        "duration_seconds": {"type":"integer","minimum":1,"maximum":86400}
                    }),
                    &["ref", "name", "duration_seconds"],
                ),
            ),
        ),
        (
            OperationKind::ApplyOtioOwnerCutReview,
            descriptor(
                "otio-cut.owner-commit",
                "Apply only a reviewed reorder/end trim in one canonical project CAS, never an external NLE effect",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "review":{"type":"object","required":[
                            "preview","reviewer_declaration","rationale",
                            "acknowledges_property_losses",
                            "acknowledges_absolute_audio_marker_positions",
                            "confirms_single_revision_change"
                        ],"properties":{
                            "preview":{"type":"object","required":["schema","project_id","generation",
                                "expected_project_revision","original_scene_order","proposed_scene_order",
                                "native_change_proposals"],"additionalProperties":true},
                            "reviewer_declaration":{"type":"string","minLength":1,"maxLength":160},
                            "rationale":{"type":"string","minLength":1,"maxLength":1600},
                            "acknowledges_property_losses":{"type":"boolean","const":true},
                            "acknowledges_absolute_audio_marker_positions":{"type":"boolean"},
                            "confirms_single_revision_change":{"type":"boolean","const":true}
                        },"additionalProperties":false}
                    }),
                    &["ref", "review"],
                ),
            ),
        ),
        (
            OperationKind::MoveScene,
            descriptor(
                "scene.move",
                "Reorder a semantic scene while preserving scene identity",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "to_index": {"type":"integer","minimum":0}
                    }),
                    &["ref", "scene_id", "to_index"],
                ),
            ),
        ),
        (
            OperationKind::UpdateSceneObjective,
            descriptor(
                "scene.objective.set",
                "Update one scene objective without replacing scene identity",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "objective": {"type":"string","minLength":1,"maxLength":4000}
                    }),
                    &["ref", "scene_id", "objective"],
                ),
            ),
        ),
        (
            OperationKind::SetSceneRenderer,
            descriptor(
                "scene.renderer.set",
                "Select the semantic renderer realization for one scene",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "renderer": {"type":"string","enum":["motion-canvas","mlt","blender","manim-community","remotion","manim-gl"]}
                    }),
                    &["ref", "scene_id", "renderer"],
                ),
            ),
        ),
        (
            OperationKind::SetSceneStatus,
            descriptor(
                "scene.status.set",
                "Set the explicit review status of one persistent scene",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "status": {"type":"string","enum":["draft","review","approved","needs_work"]}
                    }),
                    &["ref", "scene_id", "status"],
                ),
            ),
        ),
        (
            OperationKind::SetSceneDuration,
            descriptor(
                "scene.duration.set",
                "Set one scene duration and ripple subsequent scene starts on the shared rational clock",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "duration": {
                            "type":"object",
                            "properties":{
                                "num":{"type":"string","pattern":"^[1-9][0-9]*$"},
                                "den":{"type":"string","pattern":"^[1-9][0-9]*$"}
                            },
                            "required":["num","den"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "scene_id", "duration"],
                ),
            ),
        ),
        (
            OperationKind::ConformScenesToMusic,
            descriptor(
                "scene.music.cuts.conform",
                "Atomically conform 2–16 ordered editable scene boundaries to saved exact music beats; validate all downstream locks and preserve fixed global audio markers",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "targets": {
                            "type":"array","minItems":2,"maxItems":16,
                            "items": {
                                "type":"object",
                                "properties": {
                                    "scene_id":{"type":"string","format":"uuid","maxLength":64},
                                    "beat_index":{"type":"integer","minimum":0,"maximum":511}
                                },
                                "required":["scene_id","beat_index"],
                                "additionalProperties":false
                            }
                        },
                        "source_sha256": {"type":"string","pattern":"^[a-f0-9]{64}$"},
                        "base_revision": {"type":"integer","minimum":0},
                        "acknowledge_absolute_anchors": {"type":"boolean"}
                    }),
                    &[
                        "ref",
                        "targets",
                        "source_sha256",
                        "base_revision",
                        "acknowledge_absolute_anchors",
                    ],
                ),
            ),
        ),
        (
            OperationKind::SnapSceneCutToMusic,
            descriptor(
                "scene.music.cut.snap",
                "Snap one editorial scene end to a saved author-marked musical beat; preserve absolute cues and require scoped impact acknowledgement",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","format":"uuid","maxLength":64},
                        "beat_index": {"type":"integer","minimum":0,"maximum":19999},
                        "source_sha256": {"type":"string","pattern":"^[a-f0-9]{64}$"},
                        "base_revision": {"type":"integer","minimum":0},
                        "acknowledge_absolute_anchors": {"type":"boolean"}
                    }),
                    &[
                        "ref",
                        "scene_id",
                        "beat_index",
                        "source_sha256",
                        "base_revision",
                        "acknowledge_absolute_anchors",
                    ],
                ),
            ),
        ),
        (
            OperationKind::AddCanvasNode,
            descriptor(
                "canvas.node.add",
                "Add one bounded semantic canvas object with a stable identity",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node": {
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","maxLength":64},
                                "name":{"type":"string","minLength":1,"maxLength":160},
                                "kind":{"type":"string","minLength":1,"maxLength":128},
                                "parent_id":{"anyOf":[{"type":"string","maxLength":64},{"type":"null"}]},
                                "x":{"type":"number"},
                                "y":{"type":"number"},
                                "width":{"type":"number","minimum":0},
                                "height":{"type":"number","minimum":0},
                                "rotation_deg":{"type":"number"},
                                "opacity":{"type":"number","minimum":0,"maximum":1},
                                "text":{"anyOf":[{"type":"string","maxLength":100000},{"type":"null"}]},
                                "coordinate_space":{"type":"string","enum":["project_pixels","normalized","scene_local"]},
                                "z_index":{"type":"integer"},
                                "style":{"type":"object"},
                                "relations":{"type":"array","maxItems":128},
                                "property_locks":{"type":"array","maxItems":8,"items":{"type":"string","enum":["position","size","rotation","opacity","text","style","parent","order"]}},
                                "keyframes":{
                                    "type":"array",
                                    "maxItems":128,
                                    "items":{
                                        "type":"object",
                                        "properties":{
                                            "at":{"type":"object","properties":{"num":{"type":"string","pattern":"^(0|[1-9][0-9]*)$"},"den":{"type":"string","pattern":"^[1-9][0-9]*$"}},"required":["num","den"],"additionalProperties":false},
                                            "property":{"type":"string","enum":["x","y","width","height","rotation_deg","opacity"]},
                                            "value":{"type":"number"},
                                            "interpolation":{"type":"string","enum":["hold","linear","ease_in_out"]}
                                        },
                                        "required":["at","property","value","interpolation"],
                                        "additionalProperties":false
                                    }
                                }
                            },
                            "required":["id","name","kind","parent_id","x","y","width","height","rotation_deg","opacity","text","coordinate_space","z_index","style","relations","property_locks"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "scene_id", "node"],
                ),
            ),
        ),
        (
            OperationKind::RemoveCanvasNode,
            descriptor(
                "canvas.node.remove",
                "Remove an unreferenced semantic canvas object without leaving dangling hierarchy or relation references",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "scene_id", "node_id"],
                ),
            ),
        ),
        (
            OperationKind::TransformCanvasNode,
            descriptor(
                "canvas.node.transform",
                "Apply a bounded transform to one semantic canvas object",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64},
                        "transform": {
                            "type":"object",
                            "properties":{
                                "x":{"type":"number"},
                                "y":{"type":"number"},
                                "width":{"type":"number","minimum":0},
                                "height":{"type":"number","minimum":0},
                                "rotation_deg":{"type":"number"},
                                "opacity":{"type":"number","minimum":0,"maximum":1}
                            },
                            "required":["x","y","width","height","rotation_deg","opacity"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "scene_id", "node_id", "transform"],
                ),
            ),
        ),
        (
            OperationKind::SetCanvasKeyframe,
            descriptor(
                "canvas.keyframe.set",
                "Create or replace one typed scene-local keyframe without bypassing object property locks",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "scene_id":{"type":"string","maxLength":64},
                        "node_id":{"type":"string","maxLength":64},
                        "keyframe":{
                            "type":"object",
                            "properties":{
                                "at":{"type":"object","properties":{"num":{"type":"string","pattern":"^(0|[1-9][0-9]*)$"},"den":{"type":"string","pattern":"^[1-9][0-9]*$"}},"required":["num","den"],"additionalProperties":false},
                                "property":{"type":"string","enum":["x","y","width","height","rotation_deg","opacity"]},
                                "value":{"type":"number"},
                                "interpolation":{"type":"string","enum":["hold","linear","ease_in_out"]}
                            },
                            "required":["at","property","value","interpolation"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "scene_id", "node_id", "keyframe"],
                ),
            ),
        ),
        (
            OperationKind::SetCanvasPositionKeyframe,
            descriptor(
                "canvas.position-keyframe.set",
                "Atomically record both scene-local X and Y animation axes in one revision with exact locks and provenance",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "scene_id":{"type":"string","maxLength":64},
                        "node_id":{"type":"string","maxLength":64},
                        "at":{"type":"object","properties":{"num":{"type":"string","pattern":"^(0|[1-9][0-9]*)$"},"den":{"type":"string","pattern":"^[1-9][0-9]*$"}},"required":["num","den"],"additionalProperties":false},
                        "x":{"type":"number"},
                        "y":{"type":"number"},
                        "interpolation":{"type":"string","enum":["hold","linear","ease_in_out"]}
                    }),
                    &[
                        "ref",
                        "scene_id",
                        "node_id",
                        "at",
                        "x",
                        "y",
                        "interpolation",
                    ],
                ),
            ),
        ),
        (
            OperationKind::SetCanvasLinearPositionMotion,
            descriptor(
                "canvas.motion.linear-position.set",
                "Create four exact paired linear X/Y keys from source pose to unchanged base in one revision, frame-bound to the saved deliverable",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "scene_id":{"type":"string","maxLength":64},
                        "node_id":{"type":"string","maxLength":64},
                        "deliverable_id":{"type":"string","maxLength":64},
                        "start_x":{"type":"number"},
                        "start_y":{"type":"number"},
                        "end_frame":{"type":"integer","minimum":1,"maximum":36000}
                    }),
                    &[
                        "ref",
                        "scene_id",
                        "node_id",
                        "deliverable_id",
                        "start_x",
                        "start_y",
                        "end_frame",
                    ],
                ),
            ),
        ),
        (
            OperationKind::SetCanvasOutCubicPositionMotion,
            descriptor(
                "canvas.motion.out-cubic-position.set",
                "Commit four exact paired X/Y keys with source-authorized cubic-out easing through the pinned Semwright Native SDK",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "scene_id":{"type":"string","maxLength":64},
                        "node_id":{"type":"string","maxLength":64},
                        "deliverable_id":{"type":"string","maxLength":64},
                        "start_x":{"type":"number"},
                        "start_y":{"type":"number"},
                        "end_frame":{"type":"integer","minimum":1,"maximum":36000}
                    }),
                    &[
                        "ref",
                        "scene_id",
                        "node_id",
                        "deliverable_id",
                        "start_x",
                        "start_y",
                        "end_frame",
                    ],
                ),
            ),
        ),
        (
            OperationKind::RemoveCanvasKeyframe,
            descriptor(
                "canvas.keyframe.remove",
                "Remove one exact typed keyframe while preserving the remaining motion curve",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "scene_id":{"type":"string","maxLength":64},
                        "node_id":{"type":"string","maxLength":64},
                        "at":{"type":"object","properties":{"num":{"type":"string","pattern":"^(0|[1-9][0-9]*)$"},"den":{"type":"string","pattern":"^[1-9][0-9]*$"}},"required":["num","den"],"additionalProperties":false},
                        "property":{"type":"string","enum":["x","y","width","height","rotation_deg","opacity"]}
                    }),
                    &["ref", "scene_id", "node_id", "at", "property"],
                ),
            ),
        ),
        (
            OperationKind::UpdateCanvasText,
            descriptor(
                "canvas.node.text.set",
                "Set or clear the text realization of one semantic canvas object",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64},
                        "text": {"anyOf":[{"type":"string","maxLength":100000},{"type":"null"}]}
                    }),
                    &["ref", "scene_id", "node_id", "text"],
                ),
            ),
        ),
        (
            OperationKind::UpdateCanvasStyle,
            descriptor(
                "canvas.node.style.set",
                "Replace the validated semantic style of one canvas object",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64},
                        "style": {
                            "type":"object",
                            "properties":{
                                "fill":{"anyOf":[{"type":"string","maxLength":512},{"type":"null"}]},
                                "stroke":{"anyOf":[{"type":"string","maxLength":512},{"type":"null"}]},
                                "stroke_width":{"type":"number","minimum":0,"maximum":1000},
                                "font_family":{"anyOf":[{"type":"string","maxLength":512},{"type":"null"}]},
                                "font_size":{"anyOf":[{"type":"number","minimum":1,"maximum":2048},{"type":"null"}]},
                                "font_weight":{"anyOf":[{"type":"integer","minimum":1,"maximum":1000},{"type":"null"}]},
                                "line_height":{"anyOf":[{"type":"number","minimum":0.1,"maximum":20},{"type":"null"}]},
                                "blend_mode":{"type":"string","enum":["normal","multiply","screen","add"]}
                            },
                            "required":["fill","stroke","stroke_width","font_family","font_size","font_weight","line_height","blend_mode"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "scene_id", "node_id", "style"],
                ),
            ),
        ),
        (
            OperationKind::ReparentCanvasNode,
            descriptor(
                "canvas.node.reparent",
                "Change one canvas object's parent and z-order under hierarchy locks",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64},
                        "parent_id": {"anyOf":[{"type":"string","maxLength":64},{"type":"null"}]},
                        "z_index": {"type":"integer","minimum":-2147483648,"maximum":2147483647}
                    }),
                    &["ref", "scene_id", "node_id", "parent_id", "z_index"],
                ),
            ),
        ),
        (
            OperationKind::SetCanvasRelations,
            descriptor(
                "canvas.node.relations.set",
                "Replace bounded semantic alignment/follow/attach relations for one canvas object",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64},
                        "relations": {
                            "type":"array",
                            "maxItems":128,
                            "items":{
                                "type":"object",
                                "properties":{
                                    "id":{"type":"string","maxLength":64},
                                    "kind":{"type":"string","enum":["align_left","align_center_x","align_right","align_top","align_center_y","align_bottom","follow","attach"]},
                                    "target_id":{"type":"string","maxLength":64}
                                },
                                "required":["id","kind","target_id"],
                                "additionalProperties":false
                            }
                        }
                    }),
                    &["ref", "scene_id", "node_id", "relations"],
                ),
            ),
        ),
        (
            OperationKind::SetNodePropertyLock,
            descriptor(
                "canvas.node.property-lock.set",
                "Set or clear an explicit property lock on one semantic canvas object",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "node_id": {"type":"string","maxLength":64},
                        "property": {"type":"string","enum":["position","size","rotation","opacity","text","style","parent","order"]},
                        "locked": {"type":"boolean"}
                    }),
                    &["ref", "scene_id", "node_id", "property", "locked"],
                ),
            ),
        ),
        (
            OperationKind::SetCamera,
            descriptor(
                "canvas.camera.set",
                "Update semantic camera state for one scene",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "scene_id": {"type":"string","maxLength":64},
                        "camera": {
                            "type":"object",
                            "properties":{
                                "center_x":{"type":"number"},
                                "center_y":{"type":"number"},
                                "zoom":{"type":"number","exclusiveMinimum":0,"maximum":100},
                                "rotation_deg":{"type":"number"},
                                "safe_margin":{"type":"number","minimum":0,"maximum":0.49}
                            },
                            "required":["center_x","center_y","zoom","rotation_deg","safe_margin"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "scene_id", "camera"],
                ),
            ),
        ),
        (
            OperationKind::AddMarker,
            descriptor(
                "marker.add",
                "Add a project marker on the shared rational timeline",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "at": {
                            "type":"object",
                            "properties":{"num":{"type":"string","pattern":"^-?(0|[1-9][0-9]*)$"},"den":{"type":"string","pattern":"^[1-9][0-9]*$"}},
                            "required":["num","den"],
                            "additionalProperties":false
                        },
                        "label": {"type":"string","minLength":1,"maxLength":160}
                    }),
                    &["ref", "at", "label"],
                ),
            ),
        ),
        (
            OperationKind::AddAsset,
            descriptor(
                "asset.register",
                "Register one already-ingested content-addressed asset in project history",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "asset": {
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","maxLength":64},
                                "name":{"type":"string","minLength":1,"maxLength":512},
                                "media_type":{"type":"string","minLength":1,"maxLength":255},
                                "content_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "source_revision":{"anyOf":[{"type":"string","maxLength":512},{"type":"null"}]}
                            },
                            "required":["id","name","media_type","content_sha256","source_revision"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "asset"],
                ),
            ),
        ),
        (
            OperationKind::RemoveAsset,
            descriptor(
                "asset.remove",
                "Remove an unreferenced project asset without deleting immutable blob bytes",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "asset_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "asset_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertDeliverable,
            descriptor(
                "deliverable.upsert",
                "Create or replace one versioned delivery profile without claiming a rendered artifact exists",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "profile": {
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","maxLength":64},
                                "name":{"type":"string","minLength":1,"maxLength":160},
                                "width":{"type":"integer","minimum":1,"maximum":16384},
                                "height":{"type":"integer","minimum":1,"maximum":16384},
                                "language":{"type":"string","minLength":1,"maxLength":64},
                                "captions":{"type":"boolean"},
                                "caption_format":{"type":"string","enum":["web_vtt","srt"]},
                                "video_codec":{"type":"string","enum":["h264","hevc","prores_422_hq","vp9","av1"]},
                                "audio_codec":{"type":"string","enum":["aac","pcm_s16_le","opus"]},
                                "audio_sample_rate_hz":{"type":"integer","enum":[44100,48000,96000]},
                                "brand_profile":{"anyOf":[{"type":"null"},{"type":"string","minLength":1,"maxLength":256}]},
                                "cut_label":{"anyOf":[{"type":"null"},{"type":"string","minLength":1,"maxLength":256}]},
                                "parent_profile_id":{"anyOf":[{"type":"null"},{"type":"string","maxLength":64}]},
                                "source_revision":{"anyOf":[{"type":"null"},{"type":"integer","minimum":0}]},
                                "framing_strategy":{"type":"string","enum":["replan","crop"]},
                                "crop_approved":{"type":"boolean"},
                                "timing_locked":{"type":"boolean"},
                                "voice_track_id":{"anyOf":[{"type":"null"},{"type":"string","maxLength":64}]},
                                "text_overrides":{"type":"object","maxProperties":4096,"additionalProperties":{"type":"string","minLength":1,"maxLength":16384}},
                                "included_scene_ids":{"type":"array","maxItems":4096,"items":{"type":"string","maxLength":64}},
                                "protected_scene_ids":{"type":"array","maxItems":4096,"items":{"type":"string","maxLength":64}},
                                "burn_in_captions":{"type":"boolean"},
                                "frame_rate":{"type":"object","properties":{"num":{"type":"string","pattern":"^-?(0|[1-9][0-9]{0,18})$"},"den":{"type":"string","pattern":"^[1-9][0-9]{0,18}$"}},"required":["num","den"],"additionalProperties":false},
                                "color_space":{"type":"string","enum":["rec709","display_p3","rec2020"]},
                                "container":{"type":"string","enum":["mp4","mov","webm","mkv"]},
                                "adaptation_notes":{"type":"array","maxItems":64,"items":{"type":"string","minLength":1,"maxLength":1000}}
                            },
                            "required":["id","name","width","height","language","captions","caption_format","video_codec","audio_codec","audio_sample_rate_hz","brand_profile","cut_label"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "profile"],
                ),
            ),
        ),
        (
            OperationKind::RemoveDeliverable,
            descriptor(
                "deliverable.remove",
                "Remove an unreferenced delivery profile while preserving anchored review history",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "profile_id":{"type":"string","maxLength":64}
                    }),
                    &["ref", "profile_id"],
                ),
            ),
        ),
        (
            OperationKind::SetActiveVoiceTrack,
            descriptor(
                "audio.voice.active.set",
                "Select one already-measured voice take without deleting or replacing prior takes",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "track_id":{"type":"string","maxLength":64}
                    }),
                    &["ref", "track_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertTranscriptSegment,
            descriptor(
                "audio.transcript.upsert",
                "Create or update one transcript interval with explicit timing evidence",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "segment":{
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","maxLength":64},
                                "voice_track_id":{"type":"string","maxLength":64},
                                "start":{"type":"object"},
                                "end":{"type":"object"},
                                "text":{"type":"string","minLength":1,"maxLength":8000},
                                "speaker":{"anyOf":[{"type":"null"},{"type":"string","maxLength":256}]},
                                "alignment":{"type":"object"}
                            },
                            "required":["id","voice_track_id","start","end","text","speaker","alignment"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "segment"],
                ),
            ),
        ),
        (
            OperationKind::RemoveTranscriptSegment,
            descriptor(
                "audio.transcript.remove",
                "Remove an unreferenced transcript segment while preserving cue referential integrity",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "segment_id":{"type":"string","maxLength":64}
                    }),
                    &["ref", "segment_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertAudioCue,
            descriptor(
                "audio.cue.upsert",
                "Create or update one stable audio cue at an explicit rational timestamp",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "cue":{
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","maxLength":64},
                                "label":{"type":"string","minLength":1,"maxLength":512},
                                "at":{"type":"object"},
                                "source_segment_id":{"anyOf":[{"type":"null"},{"type":"string","maxLength":64}]},
                                "evidence":{"type":"string","enum":["manual","transcript_aligned","measured","unknown"]}
                            },
                            "required":["id","label","at","source_segment_id","evidence"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "cue"],
                ),
            ),
        ),
        (
            OperationKind::RemoveAudioCue,
            descriptor(
                "audio.cue.remove",
                "Remove one audio cue by stable identifier",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "cue_id":{"type":"string","maxLength":64}
                    }),
                    &["ref", "cue_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertSemanticSfx,
            descriptor(
                "audio.sfx.upsert",
                "Author a source-hashed original stereo effect cue anchored to a scene or beat",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "cue":{
                            "type":"object",
                            "properties":{
                                "schema":{"type":"string","const":"motionwright.semantic-sfx/1"},
                                "id":{"type":"string","format":"uuid"},
                                "scene_id":{"type":"string","format":"uuid"},
                                "event":{"type":"string","enum":["scene_reveal","beat_emphasis","ui_focus","confirmation","impact","transition"]},
                                "anchor":{"type":"object"},
                                "offset_frames":{"type":"integer","minimum":-15,"maximum":30},
                                "source_asset_id":{"type":"string","format":"uuid"},
                                "source_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "source_frames":{"type":"integer","minimum":1,"maximum":180},
                                "gain_milli_db":{"type":"integer","minimum":-36000,"maximum":0},
                                "attack_frames":{"type":"integer","minimum":0,"maximum":30},
                                "release_frames":{"type":"integer","minimum":0,"maximum":90},
                                "original_source_attestation":{"type":"string","minLength":1,"maxLength":160}
                            },
                            "required":["schema","id","scene_id","event","anchor",
                                "offset_frames","source_asset_id","source_sha256","source_frames",
                                "gain_milli_db","attack_frames","release_frames",
                                "original_source_attestation"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "cue"],
                ),
            ),
        ),
        (
            OperationKind::RemoveSemanticSfx,
            descriptor(
                "audio.sfx.remove",
                "Remove an original source-bound SFX placement by stable identity",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "cue_id":{"type":"string","format":"uuid"}
                    }),
                    &["ref", "cue_id"],
                ),
            ),
        ),
        (
            OperationKind::SetMixIntent,
            descriptor(
                "audio.mix.set",
                "Set bounded voice/music gain and optional mastering targets without claiming measured loudness",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "mix":{
                            "type":"object",
                            "properties":{
                                "voice_gain_db":{"type":"number","minimum":-120,"maximum":24},
                                "music_gain_db":{"type":"number","minimum":-120,"maximum":24},
                                "target_lufs":{"anyOf":[{"type":"null"},{"type":"number"}]},
                                "target_true_peak_dbfs":{"anyOf":[{"type":"null"},{"type":"number"}]}
                            },
                            "required":["voice_gain_db","music_gain_db","target_lufs","target_true_peak_dbfs"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "mix"],
                ),
            ),
        ),
        (
            OperationKind::RecordNarrationTake,
            descriptor(
                "narration.take.record",
                "Record an exact active measured narration source through canonical project CAS; no voice generation or authenticated signature",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "expected_source_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                        "expected_project_revision":{"type":"integer","minimum":0},
                        "reviewer":{"type":"string","minLength":1,"maxLength":200},
                        "reason":{"type":"string","minLength":1,"maxLength":2000}
                    }),
                    &[
                        "ref",
                        "expected_source_sha256",
                        "expected_project_revision",
                        "reviewer",
                        "reason",
                    ],
                ),
            ),
        ),
        (
            OperationKind::ReleaseNarrationTake,
            descriptor(
                "narration.take.release",
                "Explicitly release the exact source-locked narration take; source approval is owner-declared, not certified",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "expected_source_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                        "reviewer":{"type":"string","minLength":1,"maxLength":200},
                        "reason":{"type":"string","minLength":1,"maxLength":2000}
                    }),
                    &["ref", "expected_source_sha256", "reviewer", "reason"],
                ),
            ),
        ),
        (
            OperationKind::ApplyBrandCampaign,
            descriptor(
                "brand-campaign.owner-rollout",
                "Apply existing brand accent/wordmark to selected attached editable Heroes with one owner-review CAS",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "rollout":{"type":"object","required":[
                            "schema","project_id","generation","expected_revision","brand_sha256",
                            "campaign","scene_ids","accent","wordmark","reviewer_declaration",
                            "rationale","accepts_semantic_component_rebuild"
                        ],"properties":{
                            "schema":{"type":"string","const":"motionwright.brand-campaign-rollout/1"},
                            "project_id":{"type":"string","format":"uuid"},
                            "generation":{"type":"string","format":"uuid"},
                            "expected_revision":{"type":"integer","minimum":0},
                            "brand_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                            "campaign":{"type":"string","minLength":1,"maxLength":160},
                            "scene_ids":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"string","format":"uuid"},"uniqueItems":true},
                            "accent":{"type":["string","null"],"maxLength":7},
                            "wordmark":{"type":["string","null"],"maxLength":8},
                            "reviewer_declaration":{"type":"string","minLength":1,"maxLength":160},
                            "rationale":{"type":"string","minLength":1,"maxLength":1200},
                            "accepts_semantic_component_rebuild":{"type":"boolean","const":true}
                        },"additionalProperties":false}
                    }),
                    &["ref", "rollout"],
                ),
            ),
        ),
        (
            OperationKind::UpsertProductHero,
            descriptor(
                "product-hero.upsert",
                "Create or three-way update an editable ProductHeroReveal",
                schema(
                    json!({"ref": {"type": "string", "maxLength": 512}, "instance_id": {"type": "string", "format": "uuid", "maxLength": 64}, "scene_id": {"type": "string", "format": "uuid", "maxLength": 64}, "config": {"type": "object", "properties": {"eyebrow": {"type": "string", "minLength": 1, "maxLength": 48}, "headline": {"type": "string", "minLength": 1, "maxLength": 64}, "body": {"type": "string", "minLength": 1, "maxLength": 150}, "wordmark": {"type": "string", "minLength": 1, "maxLength": 8}, "foreground": {"type": "string", "minLength": 1, "maxLength": 7}, "accent": {"type": "string", "minLength": 1, "maxLength": 7}, "motion": {"type": "boolean"}}, "required": ["eyebrow", "headline", "body", "wordmark", "foreground", "accent", "motion"], "additionalProperties": false}}),
                    &["ref", "instance_id", "scene_id", "config"],
                ),
            ),
        ),
        (
            OperationKind::DetachProductHero,
            descriptor(
                "product-hero.detach",
                "Detach component ownership without deleting authored nodes",
                schema(
                    json!({"ref": {"type": "string", "maxLength": 512}, "instance_id": {"type": "string", "format": "uuid", "maxLength": 64}}),
                    &["ref", "instance_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertProductStage,
            descriptor(
                "product-stage.upsert",
                "Author or replace a rights-attested original UI study on generic browser/panel/handset anchors",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "instance_id":{"type":"string","format":"uuid","maxLength":64},
                        "scene_id":{"type":"string","format":"uuid","maxLength":64},
                        "config":{
                            "type":"object",
                            "properties":{
                                "frame":{"type":"string","enum":["browser","flat_panel","generic_handset"]},
                                "screen_heading":{"type":"string","minLength":1,"maxLength":40},
                                "screen_detail":{"type":"string","minLength":1,"maxLength":80},
                                "action_label":{"type":"string","minLength":1,"maxLength":20},
                                "author_attestation":{"type":"string","minLength":1,"maxLength":160},
                                "screen_surface":{"type":"string","pattern":"^#[0-9a-fA-F]{6}$"},
                                "foreground":{"type":"string","pattern":"^#[0-9a-fA-F]{6}$"},
                                "accent":{"type":"string","pattern":"^#[0-9a-fA-F]{6}$"},
                                "focus_x_permille":{"type":"integer","minimum":150,"maximum":850},
                                "focus_y_permille":{"type":"integer","minimum":200,"maximum":800},
                                "animate_focus":{"type":"boolean"}
                            },
                            "required":["frame","screen_heading","screen_detail","action_label","author_attestation",
                                "screen_surface","foreground","accent","focus_x_permille",
                                "focus_y_permille","animate_focus"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "instance_id", "scene_id", "config"],
                ),
            ),
        ),
        (
            OperationKind::DetachProductStage,
            descriptor(
                "product-stage.detach",
                "Release generic stage ownership without deleting or flattening its editable Canvas objects",
                schema(
                    json!({"ref":{"type":"string","maxLength":512},
                        "instance_id":{"type":"string","format":"uuid","maxLength":64}}),
                    &["ref", "instance_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertCameraReframe,
            descriptor(
                "camera-reframe.upsert",
                "Author an exact bounded planar camera move while preserving existing editable Canvas subjects",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "reframe":{
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","format":"uuid"},
                                "scene_id":{"type":"string","format":"uuid"},
                                "component_version":{"type":"integer","const":1},
                                "start_center_x":{"type":"number","minimum":720,"maximum":1200},
                                "start_center_y":{"type":"number","minimum":320,"maximum":760},
                                "end_center_x":{"type":"number","minimum":720,"maximum":1200},
                                "end_center_y":{"type":"number","minimum":320,"maximum":760},
                                "duration_frames":{"type":"integer","minimum":12,"maximum":180}
                            },
                            "required":["id","scene_id","component_version",
                                "start_center_x","start_center_y","end_center_x","end_center_y",
                                "duration_frames"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "reframe"],
                ),
            ),
        ),
        (
            OperationKind::BindStageCapture,
            descriptor(
                "product-stage.capture.bind",
                "Bind one content-addressed, registered first-party synthetic screenshot as a contained editable-stage source tile",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "stage_id":{"type":"string","format":"uuid","maxLength":64},
                        "capture_id":{"type":"string","format":"uuid","maxLength":64}
                    }),
                    &["ref", "stage_id", "capture_id"],
                ),
            ),
        ),
        (
            OperationKind::DetachCameraReframe,
            descriptor(
                "camera-reframe.detach",
                "Release an original camera pan without deleting its semantic subjects or terminal camera position",
                schema(
                    json!({"ref":{"type":"string","maxLength":512},
                        "id":{"type":"string","format":"uuid"}}),
                    &["ref", "id"],
                ),
            ),
        ),
        (
            OperationKind::DetachStageCapture,
            descriptor(
                "product-stage.capture.detach",
                "Remove the source image tile while retaining original editable ProductStage nodes and immutable capture provenance",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "stage_id":{"type":"string","format":"uuid","maxLength":64}
                    }),
                    &["ref", "stage_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertProceduralField,
            descriptor(
                "procedural-field.upsert",
                "Create or revise a bounded, deterministically seeded editable Canvas field via project CAS",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "instance_id":{"type":"string","format":"uuid","maxLength":64},
                        "scene_id":{"type":"string","format":"uuid","maxLength":64},
                        "config":{
                            "type":"object",
                            "properties":{
                                "seed":{"type":"integer","minimum":0,"maximum":4294967295u64},
                                "count":{"type":"integer","minimum":1,"maximum":64},
                                "columns":{"type":"integer","minimum":1,"maximum":16},
                                "distribution":{"type":"string","enum":["grid","staggered","scatter","radial","spiral"]},
                                "shape":{"type":"string","enum":["square","circle"]},
                                "origin_x":{"type":"integer","minimum":0,"maximum":1920},
                                "origin_y":{"type":"integer","minimum":0,"maximum":1080},
                                "area_width":{"type":"integer","minimum":0,"maximum":1920},
                                "area_height":{"type":"integer","minimum":0,"maximum":1080},
                                "size":{"type":"integer","minimum":4,"maximum":128},
                                "opacity_percent":{"type":"integer","minimum":1,"maximum":100},
                                "fill":{"type":"string","pattern":"^#[0-9a-fA-F]{6}$"},
                                "reveal_step_frames":{"type":"integer","minimum":0,"maximum":10},
                                "reveal_duration_frames":{"type":"integer","minimum":1,"maximum":60}
                            },
                            "required":["seed","count","columns","distribution","origin_x","origin_y",
                                "area_width","area_height","size","opacity_percent","fill"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "instance_id", "scene_id", "config"],
                ),
            ),
        ),
        (
            OperationKind::DetachProceduralField,
            descriptor(
                "procedural-field.detach",
                "Detach an editable generator without deleting its authored Canvas objects",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "instance_id":{"type":"string","format":"uuid","maxLength":64}
                    }),
                    &["ref", "instance_id"],
                ),
            ),
        ),
        (
            OperationKind::ProposeComponentDistillation,
            descriptor(
                "component-distillation.propose",
                "Freeze original Canvas variants and verified counterexamples as an uninstalled component proposal",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "input":{"type":"object","properties":{
                            "id":{"type":"string","format":"uuid"},
                            "label":{"type":"string","minLength":1,"maxLength":120},
                            "author":{"type":"string","minLength":1,"maxLength":120},
                            "original_rights_attestation":{"type":"string","minLength":1,"maxLength":480},
                            "source_project_id":{"type":"string","format":"uuid"},
                            "source_generation":{"type":"string","format":"uuid"},
                            "source_revision":{"type":"integer","minimum":0},
                            "roles":{"type":"array","minItems":2,"maxItems":12,"items":{
                                "type":"object","properties":{
                                    "key":{"type":"string","pattern":"^[a-z][a-z0-9_]{1,31}$"},
                                    "kind":{"type":"string","enum":["text","rectangle","circle"]}
                                },"required":["key","kind"],"additionalProperties":false
                            }},
                            "positives":{"type":"array","minItems":2,"maxItems":4,"items":{
                                "type":"object","properties":{
                                    "scene_id":{"type":"string","format":"uuid"},
                                    "node_ids":{"type":"array","minItems":2,"maxItems":12,"items":{"type":"string","format":"uuid"}}
                                },"required":["scene_id","node_ids"],"additionalProperties":false
                            }},
                            "negatives":{"type":"array","minItems":1,"maxItems":4,"items":{
                                "type":"object","properties":{
                                    "scene_id":{"type":"string","format":"uuid"},
                                    "node_id":{"anyOf":[{"type":"null"},{"type":"string","format":"uuid"}]},
                                    "rejection":{"type":"string","enum":["non_motion_canvas","locked_object","unsupported_kind"]}
                                },"required":["scene_id","node_id","rejection"],"additionalProperties":false
                            }},
                            "requirements":{"type":"array","minItems":1,"maxItems":12,"items":{"type":"string","maxLength":240}}
                        },"required":["id","label","author","original_rights_attestation",
                            "source_project_id","source_generation","source_revision",
                            "roles","positives","negatives","requirements"],"additionalProperties":false}
                    }),
                    &["ref", "input"],
                ),
            ),
        ),
        (
            OperationKind::ReviewComponentDistillation,
            descriptor(
                "component-distillation.review",
                "Record independent source-bound compatibility review; never install or execute candidate code",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "id":{"type":"string","format":"uuid"},
                        "review":{"type":"object","properties":{
                            "reviewer":{"type":"string","minLength":1,"maxLength":120},
                            "rationale":{"type":"string","minLength":1,"maxLength":1600},
                            "verdict":{"type":"string","enum":["compatible_proposal","rejected"]},
                            "source_revision":{"type":"integer","minimum":0},
                            "compatibility":{"type":"string","const":"motion_canvas_original_nodes_v1"}
                        },"required":["reviewer","rationale","verdict","source_revision","compatibility"],
                        "additionalProperties":false}
                    }),
                    &["ref", "id", "review"],
                ),
            ),
        ),
        (
            OperationKind::SetProductionPlan,
            descriptor(
                "production-plan.set",
                "Store or clear the revisioned narrative and evidence plan",
                schema(
                    json!({"ref": {"type": "string", "maxLength": 512}, "plan": {"anyOf": [{"type": "null"}, {"type": "object"}]}}),
                    &["ref", "plan"],
                ),
            ),
        ),
        (
            OperationKind::RegisterCreativeEvaluator,
            descriptor(
                "creative-evaluator.register",
                "Append a versioned non-executable creative playbook with bounded observable and human criteria",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "bundle":{
                            "type":"object",
                            "properties":{
                                "schema":{"type":"string","const":"motionwright.creative-evaluator/1"},
                                "id":{"type":"string","format":"uuid"},
                                "version":{"type":"integer","minimum":1},
                                "label":{"type":"string","minLength":1,"maxLength":100},
                                "purpose":{"type":"string","minLength":1,"maxLength":280},
                                "author_attestation":{"type":"string","minLength":1,"maxLength":240},
                                "playbook":{"type":"array","minItems":1,"maxItems":12,"items":{
                                    "type":"string","minLength":1,"maxLength":500
                                }},
                                "criteria":{"type":"array","minItems":1,"maxItems":16,"items":{
                                    "oneOf":[
                                        {"type":"object","properties":{
                                            "kind":{"const":"minimum_nodes"},
                                            "id":{"type":"string","format":"uuid"},
                                            "minimum":{"type":"integer","minimum":1,"maximum":256}
                                        },"required":["kind","id","minimum"],"additionalProperties":false},
                                        {"type":"object","properties":{
                                            "kind":{"const":"text_contains"},
                                            "id":{"type":"string","format":"uuid"},
                                            "phrase":{"type":"string","minLength":1,"maxLength":120}
                                        },"required":["kind","id","phrase"],"additionalProperties":false},
                                        {"type":"object","properties":{
                                            "kind":{"const":"project_pixel_safe_area"},
                                            "id":{"type":"string","format":"uuid"},
                                            "margin_permille":{"type":"integer","minimum":0,"maximum":150}
                                        },"required":["kind","id","margin_permille"],"additionalProperties":false},
                                        {"type":"object","properties":{
                                            "kind":{"const":"human_question"},
                                            "id":{"type":"string","format":"uuid"},
                                            "question":{"type":"string","minLength":1,"maxLength":250}
                                        },"required":["kind","id","question"],"additionalProperties":false}
                                    ]
                                }}
                            },
                            "required":["schema","id","version","label","purpose",
                                "author_attestation","playbook","criteria"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "bundle"],
                ),
            ),
        ),
        (
            OperationKind::EvaluateCreativeScene,
            descriptor(
                "creative-evaluator.evaluate",
                "Record deterministic technical observations against an exact scene and preserve human-review abstention",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "evaluator_id":{"type":"string","format":"uuid"},
                        "evaluator_version":{"type":"integer","minimum":1},
                        "scene_id":{"type":"string","format":"uuid"}
                    }),
                    &["ref", "evaluator_id", "evaluator_version", "scene_id"],
                ),
            ),
        ),
        (
            OperationKind::RegisterCaptureSource,
            descriptor(
                "capture-source.register",
                "Append an immutable declared first-party PNG/JSON capture source linked to an authored project-revision claim",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "record":{
                            "type":"object",
                            "properties":{
                                "schema":{"type":"string","const":"motionwright.capture-source/1"},
                                "id":{"type":"string","format":"uuid"},
                                "screenshot_asset_id":{"type":"string","format":"uuid"},
                                "screenshot_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "manifest_asset_id":{"type":"string","format":"uuid"},
                                "fixture_state_asset_id":{"type":"string","format":"uuid"},
                                "manifest_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "fixture_state_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "claim_id":{"type":"string","format":"uuid"},
                                "source_project_id":{"type":"string","format":"uuid"},
                                "source_generation":{"type":"string","format":"uuid"},
                                "source_revision":{"type":"integer","minimum":1},
                                "app_id":{"type":"string","const":"motionwright-studio"},
                                "app_version":{"type":"string","maxLength":17},
                                "source_commit":{"type":"string","pattern":"^[0-9a-f]{40}$"},
                                "scenario_id":{"type":"string","const":"production-original-ui-local-v1"},
                                "origin":{"type":"string","const":"http://127.0.0.1:1420"},
                                "route":{"type":"string","const":"/"},
                                "viewport_width":{"type":"integer","const":1440},
                                "viewport_height":{"type":"integer","const":940},
                                "device_scale_factor":{"type":"integer","const":1},
                                "screenshot_repeat_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "redaction":{"type":"object","properties":{
                                    "selector":{"type":"string","const":".revision-chip"},
                                    "x":{"type":"integer","minimum":0,"maximum":1440},
                                    "y":{"type":"integer","minimum":0,"maximum":940},
                                    "width":{"type":"integer","minimum":12,"maximum":1440},
                                    "height":{"type":"integer","minimum":12,"maximum":940},
                                    "mask_color":{"type":"string","const":"#0B0B0B"}
                                },"required":["selector","x","y","width","height","mask_color"],
                                  "additionalProperties":false},
                                "external_requests":{"type":"integer","const":0},
                                "credential_fields":{"type":"integer","const":0},
                                "declared_scope":{"type":"string","const":"first-party-synthetic-fixture-only"}
                            },
                            "required":["schema","id","screenshot_asset_id","screenshot_sha256",
                                "manifest_asset_id","manifest_sha256","fixture_state_asset_id","fixture_state_sha256","claim_id","source_project_id",
                                "source_generation","source_revision","app_id","app_version","source_commit",
                                "scenario_id","origin","route","viewport_width","viewport_height",
                                "device_scale_factor","screenshot_repeat_sha256","redaction",
                                "external_requests","credential_fields","declared_scope"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "record"],
                ),
            ),
        ),
        (
            OperationKind::EditPaidGeneration,
            descriptor(
                "paid-generation.edit",
                "Append a project accounting declaration; never contact, pay, approve or retry an external provider",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "edit":{"type":"object","required":["kind"],"properties":{
                            "kind":{"type":"string","enum":["create","append"]},
                            "spec":{"type":"object"},
                            "job_id":{"type":"string","format":"uuid"},
                            "expected_history_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                            "event":{"type":"object"}
                        },"additionalProperties":false}
                    }),
                    &["ref", "edit"],
                ),
            ),
        ),
        (
            OperationKind::RegisterIdentityOrigin,
            descriptor(
                "identity-origin.register",
                "Append a scoped synthetic-identity origin and declared consent references without authorizing media synthesis",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "record":{
                            "type":"object",
                            "properties":{
                                "schema":{"type":"string","const":"motionwright.identity-origin/1"},
                                "id":{"type":"string","format":"uuid"},
                                "source_project_id":{"type":"string","format":"uuid"},
                                "source_generation":{"type":"string","format":"uuid"},
                                "origin":{"type":"string","enum":["original_fictional","human_likeness"]},
                                "display_label":{"type":"string","minLength":1,"maxLength":96},
                                "origin_description":{"type":"string","minLength":1,"maxLength":300},
                                "created_by_declaration":{"type":"string","minLength":1,"maxLength":160},
                                "created_at_utc":{"type":"string","format":"date-time","maxLength":20},
                                "expires_at_utc":{"type":"string","format":"date-time","maxLength":20},
                                "allowed_modalities":{"type":"array","minItems":1,"maxItems":3,"uniqueItems":true,
                                    "items":{"type":"string","enum":["voice","face","presenter"]}},
                                "allowed_purposes":{"type":"array","minItems":1,"maxItems":4,"uniqueItems":true,
                                    "items":{"type":"string","enum":["product_demonstration","marketing","education","internal_review"]}},
                                "provenance_asset_id":{"type":"string","format":"uuid"},
                                "provenance_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "consent_asset_id":{"anyOf":[{"type":"null"},{"type":"string","format":"uuid"}]},
                                "consent_sha256":{"anyOf":[{"type":"null"},{"type":"string","pattern":"^[0-9a-f]{64}$"}]},
                                "consent_subject_declaration":{"anyOf":[{"type":"null"},{"type":"string","maxLength":160}]},
                                "consent_scope_declaration":{"anyOf":[{"type":"null"},{"type":"string","maxLength":300}]},
                                "synthetic_disclosure":{"type":"string","enum":[
                                    "SYNTHETIC ORIGINAL CHARACTER / NOT A REAL PERSON",
                                    "SYNTHETIC HUMAN LIKENESS / CONSENT REQUIRES INDEPENDENT REVIEW"]}
                            },
                            "required":["schema","id","source_project_id","source_generation","origin",
                                "display_label","origin_description","created_by_declaration",
                                "created_at_utc","expires_at_utc","allowed_modalities","allowed_purposes",
                                "provenance_asset_id","provenance_sha256","consent_asset_id",
                                "consent_sha256","consent_subject_declaration","consent_scope_declaration",
                                "synthetic_disclosure"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "record"],
                ),
            ),
        ),
        (
            OperationKind::RevokeIdentityOrigin,
            descriptor(
                "identity-origin.revoke",
                "Append an irreversible identity revocation without deleting historical source evidence",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "revocation":{
                            "type":"object",
                            "properties":{
                                "id":{"type":"string","format":"uuid"},
                                "identity_id":{"type":"string","format":"uuid"},
                                "revoked_at_utc":{"type":"string","format":"date-time","maxLength":20},
                                "reason":{"type":"string","minLength":1,"maxLength":240}
                            },
                            "required":["id","identity_id","revoked_at_utc","reason"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "revocation"],
                ),
            ),
        ),
        (
            OperationKind::UpsertMeasuredTable,
            descriptor(
                "measured-table.upsert",
                "Commit a versioned numeric table bound to an imported JSON/CSV SHA-256 asset; no metric inference",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "table":{
                            "type":"object",
                            "properties":{
                                "schema":{"type":"string","const":"motionwright.measured-table/1"},
                                "id":{"type":"string","format":"uuid"},
                                "label":{"type":"string","minLength":1,"maxLength":160},
                                "version":{"type":"integer","minimum":1},
                                "source_asset_id":{"type":"string","format":"uuid"},
                                "source_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
                                "rows":{"type":"array","minItems":1,"maxItems":64,"items":{
                                    "type":"object",
                                    "properties":{
                                        "key":{"type":"string","pattern":"^[a-z][a-z0-9_]{0,47}$"},
                                        "label":{"type":"string","minLength":1,"maxLength":120},
                                        "milli":{"type":"integer","minimum":-1000000000,"maximum":1000000000},
                                        "unit":{"type":"string","enum":["count","percent","seconds","milliseconds","bytes"]}
                                    },
                                    "required":["key","label","milli","unit"],
                                    "additionalProperties":false
                                }}
                            },
                            "required":["schema","id","label","version","source_asset_id","source_sha256","rows"],
                            "additionalProperties":false
                        }
                    }),
                    &["ref", "table"],
                ),
            ),
        ),
        (
            OperationKind::UpsertMeasuredBinding,
            descriptor(
                "measured-binding.upsert",
                "Bind a measured row to existing Canvas text or explicitly bounded bar width",
                schema(
                    json!({
                        "ref":{"type":"string","maxLength":512},
                        "binding_id":{"type":"string","format":"uuid"},
                        "scene_id":{"type":"string","format":"uuid"},
                        "node_id":{"type":"string","format":"uuid"},
                        "table_id":{"type":"string","format":"uuid"},
                        "datum_key":{"type":"string","maxLength":48},
                        "view":{"type":"object"}
                    }),
                    &[
                        "ref",
                        "binding_id",
                        "scene_id",
                        "node_id",
                        "table_id",
                        "datum_key",
                        "view",
                    ],
                ),
            ),
        ),
        (
            OperationKind::DetachMeasuredBinding,
            descriptor(
                "measured-binding.detach",
                "Remove source binding only, retaining the existing editable Canvas node",
                schema(
                    json!({"ref":{"type":"string","maxLength":512},"binding_id":{"type":"string","format":"uuid"}}),
                    &["ref", "binding_id"],
                ),
            ),
        ),
        (
            OperationKind::UpsertNativeCapsule,
            descriptor(
                "native-capsule.upsert",
                "Preserve an immutable native source attachment",
                schema(
                    json!({"ref": {"type": "string", "maxLength": 512}, "capsule": {"type": "object"}}),
                    &["ref", "capsule"],
                ),
            ),
        ),
        (
            OperationKind::ApplyCreativePatch,
            descriptor(
                "creative.patch.apply",
                "Atomically apply a bounded scene-scoped patch against the observed revision",
                schema(
                    json!({"ref": {"type": "string", "maxLength": 512}, "patch": {"type": "object"}}),
                    &["ref", "patch"],
                ),
            ),
        ),
        (
            OperationKind::UndoCreativePatch,
            descriptor(
                "creative.patch.undo",
                "Revert a stored scoped patch as a new revision, preserving compatible later edits",
                schema(
                    json!({"ref":{"type":"string","maxLength":512},"patch_id":{"type":"string","format":"uuid","maxLength":64}}),
                    &["ref", "patch_id"],
                ),
            ),
        ),
        (
            OperationKind::SetVisualLanguage,
            descriptor(
                "visual-language.set",
                "Replace the bounded versioned visual language for this project",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "visual_language": {"type":"object"}
                    }),
                    &["ref", "visual_language"],
                ),
            ),
        ),
        (
            OperationKind::AddProposalSet,
            descriptor(
                "alternatives.add",
                "Add a bounded proposal set against the exact current project revision",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "proposal_set": {"type":"object"}
                    }),
                    &["ref", "proposal_set"],
                ),
            ),
        ),
        (
            OperationKind::SelectProposal,
            descriptor(
                "alternatives.select",
                "Select one stored proposal without executing its edits",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "proposal_set_id": {"type":"string","maxLength":64},
                        "proposal_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "proposal_set_id", "proposal_id"],
                ),
            ),
        ),
        (
            OperationKind::RecordModelInvocation,
            descriptor(
                "model-invocation.record",
                "Record bounded model provenance against the exact observed project revision",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "receipt": {"type":"object"}
                    }),
                    &["ref", "receipt"],
                ),
            ),
        ),
        (
            OperationKind::CreateBranch,
            descriptor(
                "branch.create",
                "Create an isolated creative branch from the exact active project revision",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "name": {"type":"string","minLength":1,"maxLength":120}
                    }),
                    &["ref", "name"],
                ),
            ),
        ),
        (
            OperationKind::CheckoutBranch,
            descriptor(
                "branch.checkout",
                "Switch the active creative branch while preserving the current branch workspace",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "branch_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "branch_id"],
                ),
            ),
        ),
        (
            OperationKind::MergeBranch,
            descriptor(
                "branch.merge",
                "Three-way merge a direct child branch into the active branch and fail closed on semantic conflicts",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "source_branch_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "source_branch_id"],
                ),
            ),
        ),
        (
            OperationKind::AddReview,
            descriptor(
                "review.add",
                "Anchor a technical, creative or editorial review to the exact active branch and revision",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "kind": {"type":"string","enum":["technical","creative","editorial"]},
                        "resource": {"type":"string","minLength":1,"maxLength":512},
                        "body": {"type":"string","minLength":1,"maxLength":8000},
                        "start": {"anyOf":[
                            {"type":"null"},
                            {"type":"object","properties":{
                                "num":{"type":"string","pattern":"^-?(0|[1-9][0-9]*)$"},
                                "den":{"type":"string","pattern":"^[1-9][0-9]*$"}
                            },"required":["num","den"],"additionalProperties":false}
                        ]},
                        "end": {"anyOf":[
                            {"type":"null"},
                            {"type":"object","properties":{
                                "num":{"type":"string","pattern":"^-?(0|[1-9][0-9]*)$"},
                                "den":{"type":"string","pattern":"^[1-9][0-9]*$"}
                            },"required":["num","den"],"additionalProperties":false}
                        ]},
                        "locale": {"anyOf":[{"type":"null"},{"type":"string","maxLength":64}]},
                        "profile_id": {"anyOf":[{"type":"null"},{"type":"string","maxLength":64}]}
                    }),
                    &["ref", "kind", "resource", "body"],
                ),
            ),
        ),
        (
            OperationKind::ResolveReview,
            descriptor(
                "review.resolve",
                "Resolve one anchored review without transferring its approval to later revisions",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "review_id": {"type":"string","maxLength":64},
                        "resolution": {"type":"string","minLength":1,"maxLength":4000}
                    }),
                    &["ref", "review_id", "resolution"],
                ),
            ),
        ),
        (
            OperationKind::ReopenReview,
            descriptor(
                "review.reopen",
                "Mark an anchored review as needing recheck",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "review_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "review_id"],
                ),
            ),
        ),
        (
            OperationKind::SetLock,
            descriptor(
                "lock.set",
                "Add an explicit Motionwright resource lock",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "resource": {"type":"string","minLength":1,"maxLength":512},
                        "kind": {"type":"string","enum":["content","timing","position","style","renderer"]},
                        "note": {"type":"string","maxLength":500}
                    }),
                    &["ref", "resource", "kind"],
                ),
            ),
        ),
        (
            OperationKind::RemoveLock,
            descriptor(
                "lock.remove",
                "Remove an explicit Motionwright resource lock",
                schema(
                    json!({
                        "ref": {"type":"string","maxLength":512},
                        "lock_id": {"type":"string","maxLength":64}
                    }),
                    &["ref", "lock_id"],
                ),
            ),
        ),
    ];

    for (operation, contract) in operations {
        app = app.register(
            contract,
            Arc::new(ApplyHandler {
                service: service.clone(),
                operation,
            }),
        )?;
    }
    Ok(app)
}

/// Native provider composition is intentionally separate from the public
/// application-local builder. Only the private mounted-Store constructor below
/// can install the Graph writer, and even that writer requires a live Host permit.
mod graph_store_provider {
    use super::*;
    use motionwright_domain::{DomainError, GraphSourceSnapshot, SemanticGraph};
    use motionwright_service::{
        SemanticGraphAdmission, SemanticGraphContextRequest, SemanticGraphWriteLease,
        SemanticGraphWriteScope,
    };
    use motionwright_storage::Result as StorageResult;
    use semwright_native_sdk::{
        NativeDriver,
        driver_sdk::{OperationPermit, OperationPermitGuard, workspace_mount},
        serve,
    };
    use std::{
        cell::RefCell,
        ffi::OsString,
        path::{Path, PathBuf},
    };

    const DATA_MOUNT: &str = "motionwright-data";
    const DATABASE_NAME: &str = "motionwright.sqlite3";
    #[cfg(test)]
    const GRAPH_COMMAND: &str = "driver.motionwright.graph.upsert";

    fn database_override(
        environment: Option<OsString>,
        argument: Option<OsString>,
    ) -> Option<PathBuf> {
        environment.or(argument).map(PathBuf::from)
    }

    fn is_derived_database(requested: Option<&Path>, derived: &Path) -> bool {
        // Path equality normalizes some component spellings. Admission requires
        // the exact owner-derived spelling: no canonicalize, alias or case fold.
        requested.is_none_or(|path| path.as_os_str() == derived.as_os_str())
    }

    /// Not serializable or publicly constructible. A database with equal
    /// project UUID/generation/revision is not evidence of this Store binding.
    #[derive(Clone)]
    struct GraphStoreBinding {
        service: StudioService,
    }

    impl GraphStoreBinding {
        fn open_if_derived(requested: Option<&Path>) -> NativeResult<Option<Self>> {
            let derived = match workspace_mount(DATA_MOUNT) {
                Ok(root) => root.join(DATABASE_NAME),
                // Existing arbitrary overrides retain their ordinary application
                // behavior even when no owner mount is available in this process.
                Err(_) if requested.is_some() => return Ok(None),
                Err(error) => return Err(error),
            };
            if !is_derived_database(requested, &derived) {
                return Ok(None);
            }
            // Open the path derived above, never the caller's equal-looking path.
            // This is the only production construction site for this binding.
            let service = StudioService::open(derived).map_err(storage_error)?;
            Ok(Some(Self { service }))
        }
    }

    enum ProviderDatabase {
        Mounted(GraphStoreBinding),
        Ordinary(StudioService),
    }

    impl ProviderDatabase {
        fn open(requested: Option<PathBuf>) -> NativeResult<Self> {
            if let Some(binding) = GraphStoreBinding::open_if_derived(requested.as_deref())? {
                return Ok(Self::Mounted(binding));
            }
            let path = requested.ok_or_else(|| {
                Error::new(
                    ErrorCode::Internal,
                    "Default provider database lost its derived Store binding",
                )
            })?;
            Ok(Self::Ordinary(
                StudioService::open(path).map_err(storage_error)?,
            ))
        }

        fn into_application(self) -> NativeResult<Application> {
            match self {
                Self::Mounted(binding) => {
                    // The existing observer and the dedicated Graph handler share
                    // clones of this one StudioService, hence the same Store mutex.
                    let mut app =
                        build_application_with_graph_store(binding.service.clone(), true)?;
                    // Only the owner-mounted runtime enables sealed tool negotiation.
                    // Availability still requires actual observations on each request.
                    if crate::graph_native_probe::owner_probe_mounts_present() {
                        app = app.require_host_tools();
                    }
                    let app = app.register(
                        graph_contract(),
                        Arc::new(GraphUpsertHandler {
                            binding: binding.clone(),
                        }),
                    )?;
                    app.register(
                        graph_remove_contract(),
                        Arc::new(GraphRemoveHandler { binding }),
                    )
                }
                Self::Ordinary(service) => {
                    // Ordinary reads/operations survive an override; Graph writes
                    // are a precise denial rather than an inferred Store binding.
                    build_application(service)?
                        .register(graph_contract(), Arc::new(GraphStoreDeniedHandler))?
                        .register(graph_remove_contract(), Arc::new(GraphStoreDeniedHandler))
                }
            }
        }
    }

    /// Run the canonical native provider using env, argv, then the owner mount.
    ///
    /// No caller-supplied StudioService, path, ready flag, or serialized authority
    /// can install a Graph writer through this public entrypoint.
    pub async fn run_native_provider() -> NativeResult<()> {
        let requested = database_override(
            std::env::var_os("MOTIONWRIGHT_DB"),
            std::env::args_os().nth(1),
        );
        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        let requested = owner_fixture::database_request(requested)?;
        let app = ProviderDatabase::open(requested)?.into_application()?;
        serve(NativeDriver::new(app)?).await
    }

    fn graph_context_selection(
        query: &Query,
        project: &Project,
    ) -> NativeResult<Option<SemanticGraphContextRequest>> {
        query.validate()?;
        if query.scope != "graph-authoring"
            || query.limit != 1
            || query.resource != project.resource_key()
        {
            return Err(Error::invalid(
                "Graph authoring observation requires its closed single-row scope",
            ));
        }
        let Some(cursor) = &query.cursor else {
            // NativeDriver remembers the scope but deliberately not this
            // selection token. Its fresh() probe needs only the real version.
            return Ok(None);
        };
        let request: SemanticGraphContextRequest =
            serde_json::from_str(&cursor.token).map_err(|_| {
                Error::invalid("Graph cursor requires the exact typed original selection")
            })?;
        if serde_json::to_string(&request)
            .map_err(|_| Error::invalid("Graph selection encoding failed"))?
            != cursor.token
            || request.scene_id.is_nil()
            || request.variant_profile_ref.is_nil()
        {
            return Err(Error::invalid(
                "Graph cursor must preserve the compact original typed selection",
            ));
        }
        request
            .source_snapshot
            .require_current(project)
            .map_err(|_| {
                Error::new(
                    ErrorCode::StaleReference,
                    "Graph cursor source differs from the actual persisted project",
                )
            })?;
        if cursor.version != resource_version(project)? {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Graph cursor differs from the genuine observed resource version",
            ));
        }
        Ok(Some(request))
    }

    pub(super) async fn observe_authoring_context(
        service: &StudioService,
        query: &Query,
        context: &CallContext,
        project: &Project,
    ) -> NativeResult<ObservationPage> {
        context.driver_context()?;
        context.check_cancelled()?;
        let selected = graph_context_selection(query, project)?;
        let version = resource_version(project)?;
        let Some(request) = selected else {
            // This is a partial version observation, not a Ready DTO, a font
            // result or a manufactured selection. It can refresh genuine refs.
            let page = ObservationPage {
                version,
                scope: query.scope.clone(),
                items: vec![],
                complete: false,
                next: None,
            };
            page.validate_for(query)?;
            return Ok(page);
        };
        if genuine_project_ref(context)? != &version {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Graph authoring source changed since the genuine observation",
            ));
        }
        let can_inspect = crate::graph_native_probe::owner_probe_mounts_present()
            && crate::manim_graph_projection::validate_manim_semantic_graph_scene_output(
                project,
                &request.source_snapshot,
                request.scene_id,
                request.variant_profile_ref,
            )
            .is_ok();
        let value = if can_inspect {
            // All awaits occur outside the Store mutex. Service receives the
            // original decoded request, not a request rebound to a newer state.
            let fonts =
                crate::graph_native_probe::observe_authoring_fonts(context, project, &request)
                    .await?;
            let inspection = NativeGraphAuthoringInspection {
                context,
                original_request: &request,
                fonts,
                failure: RefCell::new(None),
            };
            let result =
                service.semantic_graph_authoring_context_with_admission(&request, &inspection);
            if let Some(error) = inspection.failure.take() {
                return Err(error);
            }
            result.map_err(storage_error)?
        } else {
            service
                .semantic_graph_authoring_context(&request)
                .map_err(storage_error)?
        };
        let page = ObservationPage {
            version,
            scope: query.scope.clone(),
            items: vec![
                serde_json::to_value(value)
                    .map_err(|_| Error::invalid("Graph context could not be encoded"))?,
            ],
            complete: true,
            next: None,
        };
        page.validate_for(query)?;
        Ok(page)
    }

    struct NativeGraphAuthoringInspection<'a> {
        context: &'a CallContext,
        original_request: &'a SemanticGraphContextRequest,
        fonts: crate::graph_native_probe::AuthoringFonts,
        failure: RefCell<Option<Error>>,
    }

    impl SemanticGraphAdmission for NativeGraphAuthoringInspection<'_> {
        fn inspect_authoring(
            &self,
            project: &Project,
            request: &SemanticGraphContextRequest,
        ) -> StorageResult<()> {
            let check = || -> NativeResult<()> {
                if !std::ptr::eq(request, self.original_request)
                    || genuine_project_ref(self.context)? != &resource_version(project)?
                {
                    return Err(Error::new(
                        ErrorCode::StaleReference,
                        "Graph inspection no longer selects its original observed Store source",
                    ));
                }
                crate::manim_graph_projection::validate_manim_semantic_graph_scene_output(
                    project,
                    &request.source_snapshot,
                    request.scene_id,
                    request.variant_profile_ref,
                )?;
                self.fonts.check(self.context, project, request)
            };
            check().map_err(|error| retain_admission_error(&self.failure, error))
        }

        fn acquire<'a>(
            &'a self,
            _scope: &'a SemanticGraphWriteScope<'a>,
        ) -> StorageResult<Box<dyn SemanticGraphWriteLease + 'a>> {
            Err(DomainError::Invalid(
                "A native authoring observation cannot authorize a Graph write".into(),
            )
            .into())
        }
    }

    fn graph_contract() -> OperationContract {
        let mut contract = descriptor(
            "graph.upsert",
            "Save exact editable Semantic Graph source through the mounted Store and current Host operation",
            schema(
                json!({
                    "ref": {"type":"string","minLength":1,"maxLength":512},
                    "graph": {"type":"object"},
                    "request_id": {
                        "type":"string","minLength":1,"maxLength":256,
                        "description":"Optional application request key: 1..=256 UTF-8 bytes without control characters; recovery accepts at most 87 bytes."
                    }
                }),
                &["ref", "graph"],
            ),
        );
        contract.descriptor.timeout_ms = 30_000;
        contract
    }

    fn graph_remove_contract() -> OperationContract {
        let snapshot = schema(
            json!({
                "project_id":{"type":"string"}, "generation":{"type":"string"},
                "branch_id":{"type":"string"}, "revision":{"type":"integer","minimum":0}
            }),
            &["project_id", "generation", "branch_id", "revision"],
        );
        let mut contract = descriptor(
            "graph.remove",
            "Remove one exact persisted Semantic Graph source",
            schema(
                json!({
                    "ref":{"type":"string","minLength":1,"maxLength":512},
                    "request_id":{"type":"string","minLength":1,"maxLength":256},
                    "graph_id":{"type":"string"}, "scene_id":{"type":"string"},
                    "source_snapshot":snapshot, "include_recovery":{"type":"boolean"}
                }),
                &["ref", "graph_id", "scene_id", "source_snapshot"],
            ),
        );
        contract.descriptor.timeout_ms = 30_000;
        contract
    }

    #[derive(serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct GraphRemoveArgs {
        #[serde(rename = "ref")]
        reference: String,
        #[serde(default, deserialize_with = "deserialize_graph_application_request_id")]
        request_id: Option<String>,
        graph_id: Uuid,
        scene_id: Uuid,
        source_snapshot: GraphSourceSnapshot,
        #[serde(default)]
        include_recovery: bool,
    }

    struct GraphRemoveHandler {
        binding: GraphStoreBinding,
    }

    #[async_trait]
    impl OperationHandler for GraphRemoveHandler {
        async fn invoke(&self, args: Value, context: &CallContext) -> Completion {
            let input: GraphRemoveArgs = match serde_json::from_value(args) {
                Ok(value) => value,
                Err(_) => {
                    return Completion::NotApplied(Error::invalid(
                        "Graph removal requires its exact source selection",
                    ));
                }
            };
            let change = Change::RemoveSemanticGraph {
                graph_id: input.graph_id,
                scene_id: input.scene_id,
                source_snapshot: input.source_snapshot,
            };
            invoke_graph_source_write(
                &self.binding,
                context,
                &input.reference,
                input.request_id.as_deref(),
                input.include_recovery,
                change,
            )
            .await
        }
    }

    #[derive(serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct GraphUpsertArgs {
        #[serde(rename = "ref")]
        reference: String,
        graph: SemanticGraph,
        #[serde(default, deserialize_with = "deserialize_graph_application_request_id")]
        request_id: Option<String>,
        #[serde(default)]
        include_recovery: bool,
    }

    fn deserialize_graph_application_request_id<'de, D>(
        deserializer: D,
    ) -> std::result::Result<Option<String>, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        // Absence retains the existing wire key. Explicit null is not absence.
        <String as serde::Deserialize<'de>>::deserialize(deserializer).map(Some)
    }

    fn graph_application_request_id<'a>(
        supplied: Option<&'a str>,
        wire_request_id: &'a str,
        include_recovery: bool,
    ) -> NativeResult<&'a str> {
        let raw = supplied.unwrap_or(wire_request_id);
        // Match Store.apply's existing byte/control contract before acquiring
        // a permit. This application selector never replaces the Host call ID.
        if supplied.is_some()
            && (raw.is_empty() || raw.len() > 256 || raw.chars().any(char::is_control))
        {
            return Err(Error::invalid(
                "Graph request_id requires 1..=256 UTF-8 bytes without control characters",
            ));
        }
        if include_recovery {
            validate_apply_recovery_raw_key(raw)?;
        }
        Ok(raw)
    }

    fn check_graph_application_scope_key(
        scope: &SemanticGraphWriteScope<'_>,
        original_request_id: &str,
    ) -> NativeResult<()> {
        if scope.request_id() != original_request_id {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Graph Store scope differs from the original application request key",
            ));
        }
        Ok(())
    }

    struct GraphStoreDeniedHandler;

    #[async_trait]
    impl OperationHandler for GraphStoreDeniedHandler {
        async fn invoke(&self, _args: Value, _context: &CallContext) -> Completion {
            Completion::NotApplied(Error::new(
                ErrorCode::PermissionDenied,
                "Graph source saving requires the provider-bound motionwright-data Store",
            ))
        }
    }

    struct GraphUpsertHandler {
        binding: GraphStoreBinding,
    }

    fn native_failure(error: Error) -> Completion {
        if error.outcome_known {
            Completion::NotApplied(error)
        } else {
            Completion::Uncertain(error)
        }
    }

    fn genuine_project_ref(context: &CallContext) -> NativeResult<&ResourceVersion> {
        let host = context.driver_context()?;
        context.check_cancelled()?;
        if host.native_target().is_none() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Graph source saving requires a genuine Motionwright Native Ref",
            ));
        }
        let bound = context.expected().ok_or_else(|| {
            Error::new(
                ErrorCode::StaleReference,
                "Graph source saving requires a current observed project",
            )
        })?;
        bound.validate()?;
        Ok(bound)
    }

    #[async_trait]
    impl OperationHandler for GraphUpsertHandler {
        async fn invoke(&self, args: Value, context: &CallContext) -> Completion {
            let input: GraphUpsertArgs = match serde_json::from_value(args) {
                Ok(value) => value,
                Err(_) => {
                    return Completion::NotApplied(Error::invalid(
                        "Graph source saving requires ref and typed graph source only",
                    ));
                }
            };
            let change = Change::UpsertSemanticGraph {
                graph: Box::new(input.graph),
            };
            invoke_graph_source_write(
                &self.binding,
                context,
                &input.reference,
                input.request_id.as_deref(),
                input.include_recovery,
                change,
            )
            .await
        }
    }

    fn check_fresh_graph_reference(
        bound: &ResourceVersion,
        current: &Project,
        source: &GraphSourceSnapshot,
    ) -> NativeResult<bool> {
        let fresh = source == &GraphSourceSnapshot::from_project(current);
        if fresh && bound != &resource_version(current)? {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Fresh Graph source differs from its original native reference revision",
            ));
        }
        Ok(fresh)
    }

    async fn invoke_graph_source_write(
        binding: &GraphStoreBinding,
        context: &CallContext,
        reference: &str,
        application_key: Option<&str>,
        include_recovery: bool,
        change: Change,
    ) -> Completion {
        let bound = match genuine_project_ref(context) {
            Ok(value) => value,
            Err(error) => return native_failure(error),
        };
        if reference.is_empty() {
            return Completion::NotApplied(Error::invalid("Project ref is required"));
        }
        let request_id = match graph_application_request_id(
            application_key,
            context.request_id(),
            include_recovery,
        ) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(error),
        };
        let project_id = match project_id_from_resource(&bound.resource) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(error),
        };
        let source = match &change {
            Change::UpsertSemanticGraph { graph } => &graph.authorship.source_snapshot,
            Change::RemoveSemanticGraph {
                source_snapshot, ..
            } => source_snapshot,
            _ => {
                return Completion::NotApplied(Error::invalid(
                    "Only exact Graph source changes are admitted",
                ));
            }
        };
        if source.project_id != project_id {
            return Completion::NotApplied(Error::new(
                ErrorCode::StaleReference,
                "Graph source and observed project differ",
            ));
        }
        let recovery = if include_recovery {
            let generation = match Uuid::parse_str(&bound.generation) {
                Ok(value) if !value.is_nil() && value.to_string() == bound.generation => value,
                _ => {
                    return Completion::NotApplied(Error::new(
                        ErrorCode::StaleReference,
                        "Observed recovery generation is invalid",
                    ));
                }
            };
            match prepare_apply_recovery(project_id, generation, request_id, &change) {
                Ok(identity) => Some(identity),
                Err(error) => return Completion::NotApplied(error),
            }
        } else {
            None
        };

        // No project load, Store mutex, or local mutation precedes this await.
        // The owner already selected the resource/mount for this invocation.
        let permit = match context.acquire_operation_permit().await {
            Ok(value) => value,
            Err(error) => return native_failure(error),
        };
        if permit.logical_mount() != DATA_MOUNT {
            return Completion::NotApplied(Error::new(
                ErrorCode::PermissionDenied,
                "Graph source saving requires the motionwright-data operation",
            ));
        }

        // The current project is read only after reserving the actual
        // operation. Release its Store lock before the bounded native probe.
        let current = match binding.service.project(project_id) {
            Ok(value) => value,
            Err(error) => return Completion::NotApplied(storage_error(error)),
        };
        let is_fresh = match check_fresh_graph_reference(bound, &current, source) {
            Ok(value) => value,
            Err(error) => return native_failure(error),
        };
        let proposed_fonts = if is_fresh && matches!(&change, Change::UpsertSemanticGraph { .. }) {
            match crate::graph_native_probe::observe_proposed_fonts(context, &current, &change)
                .await
            {
                Ok(value) => Some(value),
                Err(error) => return native_failure(error),
            }
        } else {
            // Historical exact replay resolves at Store; it never reruns a
            // probe or uses a prior observation as current write authority.
            None
        };

        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        let fixture = match owner_fixture::Checkpoint::load(context)
            .and_then(|fixture| fixture.before_enter().map(|()| fixture))
        {
            Ok(value) => value,
            Err(error) => return native_failure(error),
        };

        // A fresh adapter/error slot belongs to exactly this invocation.
        // It never survives a retry or becomes part of the handler state.
        let admission = NativeGraphAdmission {
            context,
            permit: &permit,
            original_request_id: request_id,
            original_change: &change,
            proposed_fonts,
            failure: RefCell::new(None),
            #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
            fixture,
        };
        let result = binding
            .service
            .apply_semantic_graph_with_admission(request_id, &change, &admission);
        // The Service seam returns StorageError. Preserve the original SDK
        // error, including uncertainty, instead of recoding denial/cancellation.
        if let Some(error) = admission.failure.take() {
            return native_failure(error);
        }
        apply_completion_with_recovery(result, recovery)
    }

    struct NativeGraphAdmission<'a> {
        context: &'a CallContext,
        permit: &'a OperationPermit,
        original_request_id: &'a str,
        original_change: &'a Change,
        proposed_fonts: Option<crate::graph_native_probe::ProposedFonts>,
        failure: RefCell<Option<Error>>,
        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        fixture: owner_fixture::Checkpoint,
    }

    fn retain_admission_error(slot: &RefCell<Option<Error>>, error: Error) -> StorageError {
        let mut saved = slot.borrow_mut();
        if saved.is_none() {
            *saved = Some(error);
        }
        DomainError::Invalid(
            "Native Graph admission failed before the Store mutation boundary".into(),
        )
        .into()
    }

    impl NativeGraphAdmission<'_> {
        fn check_scope(&self, scope: &SemanticGraphWriteScope<'_>) -> NativeResult<()> {
            let (original_source, graph_id, scene_id, profile, original_graph) =
                match self.original_change {
                    Change::UpsertSemanticGraph { graph } => (
                        &graph.authorship.source_snapshot,
                        graph.id,
                        graph.scene_id,
                        Some(graph.variant_profile_ref),
                        Some(graph.as_ref()),
                    ),
                    Change::RemoveSemanticGraph {
                        graph_id,
                        scene_id,
                        source_snapshot,
                    } => (source_snapshot, *graph_id, *scene_id, None, None),
                    _ => {
                        return Err(Error::invalid(
                            "Graph admission accepts only canonical Graph source changes",
                        ));
                    }
                };
            check_graph_application_scope_key(scope, self.original_request_id)?;
            let same_graph = match (scope.graph(), original_graph) {
                (Some(scoped), Some(original)) => std::ptr::eq(scoped, original),
                (None, None) => true,
                _ => false,
            };
            if !std::ptr::eq(scope.change(), self.original_change)
                || !same_graph
                || scope.source_snapshot() != original_source
                || scope.graph_id() != graph_id
                || scope.scene_id() != scene_id
                || scope.variant_profile_ref() != profile
            {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Graph Store scope differs from the original native invocation",
                ));
            }
            let bound = genuine_project_ref(self.context)?;
            let current = scope.current_project();
            if bound.resource != current.resource_key()
                || bound.generation != current.generation.to_string()
                || current.id != original_source.project_id
            {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Current Native Ref does not select this Store project generation",
                ));
            }
            // Do not compare source revision/branch or reference revision with
            // current here. Store.apply authenticates exact historical receipts
            // before source freshness; a replay uses a fresh current Native Ref.
            if self.permit.logical_mount() != DATA_MOUNT {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Graph Store admission lost its owner-selected logical mount",
                ));
            }
            // Validate only a new, current proposal here. An older proposal may
            // be an exact durable replay; Store.apply authenticates that receipt
            // before freshness and rejects stale inputs for a new mutation.
            // This pure candidate check does not acquire another Store lock,
            // execute a runtime, attest fonts or replace the live Host permit.
            // Remove has no new text or profile. Its exact source fields,
            // scene/graph IDs and canonical domain mutation remain Store-owned.
            if check_fresh_graph_reference(bound, current, original_source)?
                && matches!(scope.change(), Change::UpsertSemanticGraph { .. })
            {
                crate::manim_graph_projection::validate_proposed_manim_semantic_graph_source(
                    current,
                    scope.change(),
                )?;
                self.proposed_fonts
                    .as_ref()
                    .ok_or_else(|| {
                        Error::new(
                            ErrorCode::PermissionDenied,
                            "A fresh Graph proposal requires its original native font observation",
                        )
                    })?
                    .check(self.context, current, scope.change())?;
            }
            Ok(())
        }
    }

    impl SemanticGraphAdmission for NativeGraphAdmission<'_> {
        fn acquire<'a>(
            &'a self,
            scope: &'a SemanticGraphWriteScope<'a>,
        ) -> StorageResult<Box<dyn SemanticGraphWriteLease + 'a>> {
            // Service calls this synchronously under its actual Store mutex.
            // No await, second Store lock, or service callback is permitted here.
            self.check_scope(scope)
                .map_err(|error| retain_admission_error(&self.failure, error))?;
            let guard = self
                .context
                .enter_operation_permit(self.permit)
                .map_err(|error| retain_admission_error(&self.failure, error))?;
            #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
            self.fixture
                .entered(&guard)
                .map_err(|error| retain_admission_error(&self.failure, error))?;
            Ok(Box::new(NativeGraphWriteLease {
                guard,
                failure: &self.failure,
                #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
                fixture: &self.fixture,
                #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
                current_passed: std::cell::Cell::new(false),
            }))
        }
    }

    struct NativeGraphWriteLease<'a> {
        guard: OperationPermitGuard<'a>,
        failure: &'a RefCell<Option<Error>>,
        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        fixture: &'a owner_fixture::Checkpoint,
        #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
        current_passed: std::cell::Cell<bool>,
    }

    impl SemanticGraphWriteLease for NativeGraphWriteLease<'_> {
        fn check_current(&self) -> StorageResult<()> {
            self.guard
                .check_current()
                .map_err(|error| retain_admission_error(self.failure, error))?;
            #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
            {
                self.fixture
                    .after_current_check()
                    .map_err(|error| retain_admission_error(self.failure, error))?;
                self.current_passed.set(true);
            }
            Ok(())
        }
    }

    #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
    impl Drop for NativeGraphWriteLease<'_> {
        fn drop(&mut self) {
            if self.current_passed.get() {
                if let Err(error) = self.fixture.after_store_returned() {
                    // Store may already have committed. Retain uncertainty rather
                    // than turn a custody failure into a known application refusal.
                    let _ = retain_admission_error(self.failure, error.uncertain());
                }
            }
            // The actual SDK guard field drops only after this method returns.
        }
    }

    #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
    pub(super) fn trace_native_observer(query: &Query, context: &CallContext) -> NativeResult<()> {
        owner_fixture::trace_native_observer(query, context)
    }

    #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
    pub(super) fn trace_native_recovery_lookup(
        request: &RequestIdentity,
        context: &CallContext,
    ) -> NativeResult<()> {
        owner_fixture::trace_native_recovery_lookup(request, context)
    }

    // This module is absent from normal builds. It supplies byte-limited
    // observations and checked deadlines, not authority or a storage wall-time bound.
    #[cfg(all(feature = "native-graph-owner-fixture", target_os = "linux"))]
    mod owner_fixture {
        use super::*;
        use std::{
            fs::{self, File, OpenOptions},
            io::{Read, Write},
            os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
            time::{Duration, Instant},
        };
        const CONTROL: &str = "permit-fixture-control";

        fn open_control_original(path: &Path, before: &fs::Metadata) -> NativeResult<File> {
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
                .open(path)?;
            let actual = file.metadata()?;
            if !actual.file_type().is_file()
                || actual.permissions().mode() & 0o077 != 0
                || before.dev() != actual.dev()
                || before.ino() != actual.ino()
                || before.len() != actual.len()
                || before.mtime() != actual.mtime()
                || before.mtime_nsec() != actual.mtime_nsec()
            {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Fixture control identity, kind or content metadata changed",
                ));
            }
            Ok(file)
        }

        fn read_optional(path: &Path, limit: usize) -> NativeResult<Option<Vec<u8>>> {
            let before = match fs::symlink_metadata(path) {
                Ok(value) => value,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
                Err(error) => return Err(error.into()),
            };
            if !before.file_type().is_file()
                || before.permissions().mode() & 0o077 != 0
                || before.len() > limit as u64
            {
                return Err(Error::invalid(
                    "Fixture control is not a bounded private regular file",
                ));
            }
            let mut file = open_control_original(path, &before)?;
            let mut bytes = Vec::new();
            Read::by_ref(&mut file)
                .take(limit as u64 + 1)
                .read_to_end(&mut bytes)?;
            let after = file.metadata()?;
            let current = fs::symlink_metadata(path)?;
            if bytes.len() > limit || bytes.len() as u64 != before.len() {
                return Err(Error::invalid(
                    "Fixture control length changed during its read",
                ));
            }
            for observed in [&after, &current] {
                if !observed.file_type().is_file()
                    || observed.permissions().mode() & 0o077 != 0
                    || observed.dev() != before.dev()
                    || observed.ino() != before.ino()
                    || observed.len() != before.len()
                    || observed.mtime() != before.mtime()
                    || observed.mtime_nsec() != before.mtime_nsec()
                {
                    return Err(Error::new(
                        ErrorCode::Conflict,
                        "Fixture control changed during its retained read",
                    ));
                }
            }
            Ok(Some(bytes))
        }

        fn publish(root: &Path, name: &str, value: &Value) -> NativeResult<()> {
            let final_path = root.join(name);
            let staging = root.join(format!("{name}.pending"));
            let bytes = serde_json::to_vec(value)?;
            if bytes.len() > 4096 {
                return Err(Error::invalid("Fixture marker exceeded its byte budget"));
            }
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
                .mode(0o600)
                .open(&staging)?;
            let opened = file.metadata()?;
            if !opened.file_type().is_file()
                || opened.permissions().mode() & 0o077 != 0
                || opened.len() != 0
            {
                return Err(Error::invalid(
                    "Fixture marker descriptor is not a new private file",
                ));
            }
            file.write_all(&bytes)
                .map_err(|error| Error::from(error).uncertain())?;
            // Synchronous storage calls have no internal wall-time guarantee.
            // The external owner observes setup/closure; a late completed
            // publication cannot reset the original checkpoint deadline.
            file.sync_all()
                .map_err(|error| Error::from(error).uncertain())?;
            let after = file.metadata()?;
            let current = fs::symlink_metadata(&staging)?;
            if !after.file_type().is_file()
                || !current.file_type().is_file()
                || after.dev() != opened.dev()
                || after.ino() != opened.ino()
                || current.dev() != opened.dev()
                || current.ino() != opened.ino()
                || after.len() != bytes.len() as u64
                || current.len() != bytes.len() as u64
            {
                return Err(
                    Error::new(ErrorCode::Conflict, "Fixture marker descriptor changed")
                        .uncertain(),
                );
            }
            fs::hard_link(&staging, &final_path).map_err(|error| Error::from(error).uncertain())?;
            fs::remove_file(&staging).map_err(|error| Error::from(error).uncertain())?;
            Ok(())
        }

        pub(super) fn trace_native_observer(
            query: &Query,
            context: &CallContext,
        ) -> NativeResult<()> {
            // A diagnostic only, never an actor/context or authority oracle.
            if context.driver_context().is_err() {
                return Ok(());
            }
            let root = workspace_mount(CONTROL)?;
            match read_optional(&root.join("native-read-mode"), 32)?.as_deref() {
                None => return Ok(()),
                Some(b"trace\n") => (),
                _ => return Err(Error::invalid("Unknown native read fixture mode")),
            }
            context.check_cancelled()?;
            static SERIAL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
            let serial = SERIAL.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if serial == 0 || serial > 256 {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "Native read fixture entry budget exceeded",
                ));
            }
            publish(
                &root,
                &format!("read-entry-{serial:04}.json"),
                &json!({
                    "schema":"motionwright.sec07-observer-entry/1",
                    "request_id":context.request_id(),
                    "provider_process_id":std::process::id(),
                    "resource":query.resource,"scope":query.scope,
                    "native_authority":false
                }),
            )?;
            context.check_cancelled()
        }

        pub(super) fn trace_native_recovery_lookup(
            request: &RequestIdentity,
            context: &CallContext,
        ) -> NativeResult<()> {
            // Bounded pre-lookup diagnostic; never an actor or authority oracle.
            if context.driver_context().is_err() {
                return Ok(());
            }
            let root = workspace_mount(CONTROL)?;
            match read_optional(&root.join("native-read-mode"), 32)?.as_deref() {
                None => return Ok(()),
                Some(b"trace\n") => (),
                _ => return Err(Error::invalid("Unknown native read fixture mode")),
            }
            context.check_cancelled()?;
            static SERIAL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
            let serial = SERIAL.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if serial == 0 || serial > 256 {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "Native recovery lookup fixture entry budget exceeded",
                ));
            }
            publish(
                &root,
                &format!("lookup-entry-{serial:04}.json"),
                &json!({
                    "schema":"motionwright.sec07-recovery-lookup-entry/1",
                    "request_id":context.request_id(),
                    "provider_process_id":std::process::id(),
                    "resource":request.resource,"key":request.key,"epoch":request.epoch,
                    "request_sha256":request.request_sha256,"native_authority":false
                }),
            )?;
            context.check_cancelled()
        }

        fn checkpoint_time_remaining(deadline: Instant) -> NativeResult<()> {
            if Instant::now() >= deadline {
                return Err(Error::new(
                    ErrorCode::Timeout,
                    "Original fixture checkpoint expired; no retry",
                )
                .uncertain());
            }
            Ok(())
        }

        fn observed_release(bytes: Option<&[u8]>, deadline: Instant) -> NativeResult<bool> {
            // This check follows the actual read, before accepting even exact
            // release bytes. A filesystem delay cannot turn a late go into admission.
            checkpoint_time_remaining(deadline)?;
            match bytes {
                Some(b"go\n") => Ok(true),
                Some(_) => Err(Error::invalid("Fixture release bytes differ")),
                None => Ok(false),
            }
        }

        fn wait(root: &Path, name: &str, deadline: Instant) -> NativeResult<()> {
            loop {
                checkpoint_time_remaining(deadline)?;
                let bytes = read_optional(&root.join(name), 16)?;
                if observed_release(bytes.as_deref(), deadline)? {
                    return Ok(());
                }
                let remaining = deadline.saturating_duration_since(Instant::now());
                std::thread::sleep(remaining.min(Duration::from_millis(2)));
            }
        }

        pub(super) fn database_request(
            requested: Option<PathBuf>,
        ) -> NativeResult<Option<PathBuf>> {
            // Ordinary env/argv selection always wins. DriverHost intentionally
            // does not expose manifest-controlled launch args/environment, so the
            // fixture uses this closed selector only in its instrumented binary.
            if requested.is_some() {
                return Ok(requested);
            }
            let control = workspace_mount(CONTROL)?;
            match read_optional(&control.join("provider-mode"), 32)?.as_deref() {
                None | Some(b"default\n") => Ok(None),
                Some(b"equivalent\n") => Ok(Some(workspace_mount(DATA_MOUNT)?.join(DATABASE_NAME))),
                Some(b"copied\n") => Ok(Some(
                    workspace_mount(DATA_MOUNT)?.join("override-copy.sqlite3"),
                )),
                _ => Err(Error::invalid(
                    "Unknown closed Graph provider fixture selection",
                )),
            }
        }

        #[derive(Clone, Copy, PartialEq, Eq)]
        enum Mode {
            Disabled,
            BeforeEnter,
            Commit,
        }

        pub(super) struct Checkpoint {
            root: PathBuf,
            mode: Mode,
            request: String,
        }
        impl Checkpoint {
            pub(super) fn load(context: &CallContext) -> NativeResult<Self> {
                let root = workspace_mount(CONTROL)?;
                let mode = match read_optional(&root.join("checkpoint-mode"), 32)?.as_deref() {
                    None => Mode::Disabled,
                    Some(b"before-enter\n") => Mode::BeforeEnter,
                    Some(b"commit\n") => Mode::Commit,
                    _ => return Err(Error::invalid("Unknown Graph fixture checkpoint")),
                };
                Ok(Self {
                    root,
                    mode,
                    request: context.request_id().to_owned(),
                })
            }
            fn marker(&self, name: &str, phase: &str) -> NativeResult<()> {
                publish(
                    &self.root,
                    name,
                    &json!({
                        "schema":"motionwright.graph-owner-checkpoint/1",
                        "phase":phase,"request_id":self.request,
                        "provider_process_id":std::process::id(),
                        "native_authority":false
                    }),
                )
            }
            pub(super) fn before_enter(&self) -> NativeResult<()> {
                if self.mode == Mode::BeforeEnter {
                    let deadline = Instant::now() + Duration::from_secs(8);
                    self.marker("before-enter.json", "before-enter")?;
                    wait(&self.root, "continue-enter", deadline)?;
                }
                Ok(())
            }
            pub(super) fn entered(&self, _actual: &OperationPermitGuard<'_>) -> NativeResult<()> {
                if self.mode != Mode::Disabled {
                    self.marker("entered.json", "entered")?;
                }
                Ok(())
            }
            pub(super) fn after_current_check(&self) -> NativeResult<()> {
                if self.mode == Mode::Commit {
                    // Real guard.check_current already succeeded. A later revoke
                    // request may wait for this admitted operation to finish.
                    let deadline = Instant::now() + Duration::from_secs(8);
                    self.marker("current-checked.json", "current-checked")?;
                    wait(&self.root, "continue-store", deadline)?;
                }
                Ok(())
            }
            pub(super) fn after_store_returned(&self) -> NativeResult<()> {
                if self.mode == Mode::Commit {
                    // This only reports the Drop boundary while the real guard is
                    // still a live field. Independent SQLite readback proves commit.
                    let deadline = Instant::now() + Duration::from_secs(8);
                    self.marker("store-returned.json", "store-returned-guard-retained")?;
                    wait(&self.root, "release-guard", deadline)?;
                }
                Ok(())
            }
        }

        #[cfg(test)]
        mod checkpoint_io_tests {
            use super::*;
            use std::os::unix::ffi::OsStrExt;
            use std::sync::mpsc;

            #[test]
            fn expired_checkpoint_does_not_accept_an_existing_release() {
                let root = tempfile::tempdir().unwrap();
                fs::write(root.path().join("continue-enter"), b"go\n").unwrap();
                fs::set_permissions(
                    root.path().join("continue-enter"),
                    fs::Permissions::from_mode(0o600),
                )
                .unwrap();
                let error = wait(root.path(), "continue-enter", Instant::now()).unwrap_err();
                assert_eq!(error.code, ErrorCode::Timeout);
                assert!(!error.outcome_known);
            }

            #[test]
            fn release_bytes_observed_after_the_original_deadline_remain_unknown() {
                let error = observed_release(Some(b"go\n"), Instant::now()).unwrap_err();
                assert_eq!(error.code, ErrorCode::Timeout);
                assert!(!error.outcome_known);
                assert!(
                    observed_release(Some(b"go\n"), Instant::now() + Duration::from_secs(1))
                        .unwrap()
                );
            }

            #[test]
            fn rebound_control_fifo_is_rejected_without_a_writer() {
                let root = tempfile::tempdir().unwrap();
                let path = root.path().join("release-guard");
                fs::write(&path, b"go\n").unwrap();
                fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
                let before = fs::symlink_metadata(&path).unwrap();
                fs::rename(&path, root.path().join("original-release")).unwrap();
                let c_path = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
                // SAFETY: this CString is owned, NUL-terminated and alive for
                // the call. The FIFO exists only in the private test directory.
                let created = unsafe { libc::mkfifo(c_path.as_ptr(), 0o600) };
                assert_eq!(created, 0, "{}", std::io::Error::last_os_error());
                let (tx, rx) = mpsc::sync_channel(1);
                let reader_path = path.clone();
                let reader = std::thread::spawn(move || {
                    let result = open_control_original(&reader_path, &before).map(|_| ());
                    let _ = tx.send(result);
                });
                let first = rx.recv_timeout(Duration::from_secs(5));
                if matches!(&first, Err(mpsc::RecvTimeoutError::Timeout)) {
                    let unblock = OpenOptions::new()
                        .read(true)
                        .write(true)
                        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
                        .open(&path);
                    let recovered = match &unblock {
                        Ok(_) => rx.recv_timeout(Duration::from_secs(5)),
                        Err(_) => Err(mpsc::RecvTimeoutError::Timeout),
                    };
                    drop(unblock);
                    assert!(
                        recovered.is_ok(),
                        "Control FIFO regression cleanup remained unconfirmed"
                    );
                }
                reader.join().unwrap();
                assert!(
                    first
                        .expect("Control descriptor open blocked on a FIFO")
                        .is_err()
                );
                fs::remove_file(path).unwrap();
            }
        }
    }

    // Dropping the Service lease drops the actual SDK guard only after
    // Store.apply returns. Its SDK Drop performs the matching real release.
    // There is no caller-owned release frame or synthetic permit implementation.

    #[cfg(test)]
    mod tests {
        use super::*;
        use motionwright_storage::Store;
        use semwright_native_sdk::Driver;

        fn seed_database(path: &Path) -> Project {
            let mut store = Store::open(path).unwrap();
            let mut project = store
                .create_named_project("Graph Store binding fixture")
                .unwrap();
            let expected = RevisionStamp {
                resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
            };
            project = store
                .apply(
                    project.id,
                    &expected,
                    "seed-graph-scene",
                    &Change::AddScene {
                        name: "Explanatory relationships".into(),
                        objective: "Native Graph Store admission negative fixture".into(),
                        duration_seconds: 6,
                    },
                )
                .unwrap()
                .project;
            let expected = RevisionStamp {
                resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
            };
            let scene_id = project.scenes[0].id;
            project = store
                .apply(
                    project.id,
                    &expected,
                    "seed-graph-renderer",
                    &Change::SetSceneRenderer {
                        scene_id,
                        renderer: motionwright_domain::RendererKind::ManimCommunity,
                    },
                )
                .unwrap()
                .project;
            // The connection closes before copying SQLite; no live WAL is copied.
            drop(store);
            project
        }

        fn assert_denied(completion: Completion) {
            let error = completion.into_result().unwrap_err();
            assert_eq!(error.code, ErrorCode::PermissionDenied);
            assert!(error.outcome_known);
        }

        #[test]
        fn default_and_exact_override_select_only_the_derived_spelling() {
            let derived = Path::new("/workspace/motionwright-data/motionwright.sqlite3");
            assert!(is_derived_database(None, derived));
            assert!(is_derived_database(Some(derived), derived));
            assert!(!is_derived_database(
                Some(Path::new("/workspace/another/motionwright.sqlite3")),
                derived
            ));
        }

        #[test]
        fn path_aliases_and_case_changes_never_select_the_graph_binding() {
            for (derived, alias) in [
                (
                    "/workspace/motionwright-data/motionwright.sqlite3",
                    "/workspace/motionwright-data/./motionwright.sqlite3",
                ),
                (
                    "/workspace/motionwright-data/motionwright.sqlite3",
                    "/workspace//motionwright-data/motionwright.sqlite3",
                ),
                (
                    "/workspace/motionwright-data/motionwright.sqlite3",
                    "/workspace/motionwright-data/child/../motionwright.sqlite3",
                ),
                (
                    r"E:\mounted\motionwright-data\motionwright.sqlite3",
                    r"e:\mounted\motionwright-data\motionwright.sqlite3",
                ),
                (
                    r"E:\mounted\motionwright-data\motionwright.sqlite3",
                    r"E:\mounted\motionwright-data\.\motionwright.sqlite3",
                ),
            ] {
                assert!(
                    !is_derived_database(Some(Path::new(alias)), Path::new(derived)),
                    "alias cannot certify the opened Store: {alias}"
                );
            }
        }

        #[test]
        fn environment_then_argument_then_default_preserves_empty_override() {
            assert_eq!(
                database_override(
                    Some("environment.sqlite3".into()),
                    Some("argument.sqlite3".into())
                ),
                Some(PathBuf::from("environment.sqlite3"))
            );
            assert_eq!(
                database_override(None, Some("argument.sqlite3".into())),
                Some(PathBuf::from("argument.sqlite3"))
            );
            assert_eq!(database_override(None, None), None);
            assert_eq!(
                database_override(Some(OsString::new()), Some("argument.sqlite3".into())),
                Some(PathBuf::new())
            );
        }

        #[tokio::test]
        async fn generic_application_never_installs_a_graph_writer() {
            let root = tempfile::tempdir().unwrap();
            let path = root.path().join("ordinary.sqlite3");
            seed_database(&path);
            let app = build_application(StudioService::open(&path).unwrap()).unwrap();
            assert!(app.handler(GRAPH_COMMAND).is_none());
            assert!(
                app.contracts()
                    .iter()
                    .all(|contract| contract.descriptor.name != GRAPH_COMMAND)
            );
            let mut driver = NativeDriver::new(app).unwrap();
            assert!(
                driver
                    .capabilities()
                    .await
                    .unwrap()
                    .iter()
                    .all(|capability| capability.descriptor.name != GRAPH_COMMAND)
            );
        }

        #[tokio::test]
        async fn copied_database_identity_preserves_reads_but_cannot_install_graph_writer() {
            let root = tempfile::tempdir().unwrap();
            let original_path = root.path().join("motionwright.sqlite3");
            let copied_path = root.path().join("override-copy.sqlite3");
            let original = seed_database(&original_path);
            std::fs::copy(&original_path, &copied_path).unwrap();
            let database = ProviderDatabase::open(Some(copied_path.clone())).unwrap();
            let service = match &database {
                ProviderDatabase::Ordinary(service) => service.clone(),
                ProviderDatabase::Mounted(_) => panic!("Copied identity cannot bind a Store"),
            };
            let before = service.project(original.id).unwrap();
            assert_eq!(
                serde_json::to_value(&before).unwrap(),
                serde_json::to_value(&original).unwrap()
            );
            let before_history =
                serde_json::to_value(service.history(original.id, 0, 100).unwrap()).unwrap();
            let observer = MotionwrightObserver::new(service.clone());
            let query = Query {
                resource: original.resource_key(),
                scope: "summary".into(),
                limit: 1,
                cursor: None,
            };
            let page = observer
                .observe(
                    &query,
                    &CallContext::application_local("copied-db-read").unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(page.version, resource_version(&original).unwrap());
            let graph = crate::semantic_graph_source_fixture::graph_for(&before, false);
            let app = database.into_application().unwrap();
            let handler = app.handler(GRAPH_COMMAND).unwrap();
            assert_denied(
                handler
                    .invoke(
                        json!({"ref":"native:untrusted-test-marker","graph":graph}),
                        &CallContext::application_local("copied-db-graph-denied").unwrap(),
                    )
                    .await,
            );
            assert_eq!(
                serde_json::to_value(service.project(original.id).unwrap()).unwrap(),
                serde_json::to_value(&before).unwrap()
            );
            assert_eq!(
                serde_json::to_value(service.history(original.id, 0, 100).unwrap()).unwrap(),
                before_history
            );
            drop(app);
            drop(observer);
            drop(service);
            let reopened = StudioService::open(copied_path).unwrap();
            assert_eq!(
                serde_json::to_value(reopened.project(original.id).unwrap()).unwrap(),
                serde_json::to_value(&before).unwrap()
            );
        }

        #[tokio::test]
        async fn private_handler_still_rejects_application_local_context_before_store_mutation() {
            let root = tempfile::tempdir().unwrap();
            let path = root.path().join("negative-only.sqlite3");
            let project = seed_database(&path);
            let service = StudioService::open(path).unwrap();
            let graph = crate::semantic_graph_source_fixture::graph_for(&project, false);
            // A private unit-test arrangement supplies no authority. It verifies
            // rejection, never the genuine mounted constructor or a successful write.
            let handler = GraphUpsertHandler {
                binding: GraphStoreBinding {
                    service: service.clone(),
                },
            };
            assert_denied(
                handler
                    .invoke(
                        json!({"ref":"native:untrusted-test-marker","graph":graph}),
                        &CallContext::application_local("local-graph-denied").unwrap(),
                    )
                    .await,
            );
            assert_eq!(
                serde_json::to_value(service.project(project.id).unwrap()).unwrap(),
                serde_json::to_value(project).unwrap()
            );
        }

        #[test]
        fn fresh_graph_source_cannot_use_an_older_genuine_reference_revision() {
            // Resource facts only: this test constructs no Host or write grant.
            let project = crate::semantic_graph_source_fixture::seed_domain();
            let observed = resource_version(&project).unwrap();
            let mut changed = project.clone();
            changed.revision += 1;
            let new_source = GraphSourceSnapshot::from_project(&changed);
            let error = check_fresh_graph_reference(&observed, &changed, &new_source).unwrap_err();
            assert_eq!(error.code, ErrorCode::StaleReference);
            assert!(error.outcome_known);
            assert!(
                check_fresh_graph_reference(
                    &resource_version(&changed).unwrap(),
                    &changed,
                    &new_source
                )
                .unwrap()
            );
            // An old source is not treated as a new mutation or reprobed here;
            // Store still owns exact historical receipt authentication.
            assert!(
                !check_fresh_graph_reference(
                    &resource_version(&changed).unwrap(),
                    &changed,
                    &GraphSourceSnapshot::from_project(&project)
                )
                .unwrap()
            );
        }

        fn graph_context_query(project: &Project) -> (Query, SemanticGraphContextRequest) {
            let request = SemanticGraphContextRequest {
                source_snapshot: GraphSourceSnapshot::from_project(project),
                scene_id: project.scenes[0].id,
                variant_profile_ref: project.deliverables[0].id,
            };
            let query = Query {
                resource: project.resource_key(),
                scope: "graph-authoring".into(),
                limit: 1,
                cursor: Some(PageCursor {
                    version: resource_version(project).unwrap(),
                    scope: "graph-authoring".into(),
                    token: serde_json::to_string(&request).unwrap(),
                }),
            };
            (query, request)
        }

        #[test]
        fn graph_authoring_cursor_preserves_original_source_and_selection() {
            let project = crate::semantic_graph_source_fixture::seed_domain();
            let (mut query, original) = graph_context_query(&project);
            assert_eq!(
                graph_context_selection(&query, &project).unwrap(),
                Some(original)
            );
            query.cursor = None;
            assert!(graph_context_selection(&query, &project).unwrap().is_none());
        }

        #[test]
        fn graph_authoring_cursor_rejects_changed_source_before_native_probe() {
            let project = crate::semantic_graph_source_fixture::seed_domain();
            let (query, original) = graph_context_query(&project);
            for case in 0..6 {
                let mut candidate = query.clone();
                let mut selected = original.clone();
                match case {
                    0 => selected.source_snapshot.branch_id = Uuid::new_v4(),
                    1 => selected.source_snapshot.generation = Uuid::new_v4(),
                    2 => selected.source_snapshot.project_id = Uuid::new_v4(),
                    3 => selected.source_snapshot.revision += 1,
                    4 => {
                        candidate.cursor.as_mut().unwrap().version.generation =
                            Uuid::new_v4().to_string()
                    }
                    _ => candidate.cursor.as_mut().unwrap().scope = "summary".into(),
                }
                candidate.cursor.as_mut().unwrap().token =
                    serde_json::to_string(&selected).unwrap();
                assert_eq!(
                    graph_context_selection(&candidate, &project)
                        .unwrap_err()
                        .code,
                    ErrorCode::StaleReference
                );
            }
        }

        #[test]
        fn graph_authoring_cursor_rejects_untyped_or_noncanonical_selector() {
            let project = crate::semantic_graph_source_fixture::seed_domain();
            let (query, request) = graph_context_query(&project);
            let mut extra = serde_json::to_value(&request).unwrap();
            extra["ready"] = json!(true);
            for token in [
                "null".into(),
                "{}".into(),
                "x".repeat(1025),
                serde_json::to_string(&extra).unwrap(),
                serde_json::to_string_pretty(&request).unwrap(),
            ] {
                let mut candidate = query.clone();
                candidate.cursor.as_mut().unwrap().token = token;
                assert!(graph_context_selection(&candidate, &project).is_err());
            }
            let mut too_many = query;
            too_many.limit = 2;
            assert!(graph_context_selection(&too_many, &project).is_err());
        }

        #[test]
        fn graph_remove_wire_keeps_original_source_without_graph_or_profile_substitutes() {
            let project = crate::semantic_graph_source_fixture::seed_domain();
            let graph = crate::semantic_graph_source_fixture::graph_for(&project, false);
            let args = json!({"ref":"native:untrusted-test-marker", "request_id":"remove-original",
                "graph_id":graph.id, "scene_id":graph.scene_id,
                "source_snapshot":graph.authorship.source_snapshot, "include_recovery":true});
            let parsed: GraphRemoveArgs = serde_json::from_value(args.clone()).unwrap();
            assert_eq!(parsed.graph_id, graph.id);
            assert_eq!(parsed.scene_id, graph.scene_id);
            assert_eq!(parsed.source_snapshot, graph.authorship.source_snapshot);
            assert_eq!(parsed.request_id.as_deref(), Some("remove-original"));
            for field in [
                "graph",
                "variant_profile_ref",
                "ready",
                "operation_permit",
                "store_binding",
            ] {
                let mut invalid = args.clone();
                invalid[field] = json!(true);
                assert!(serde_json::from_value::<GraphRemoveArgs>(invalid).is_err());
            }
            let mut null_key = args;
            null_key["request_id"] = Value::Null;
            assert!(serde_json::from_value::<GraphRemoveArgs>(null_key).is_err());
            let contract = graph_remove_contract();
            assert_eq!(contract.descriptor.name, "driver.motionwright.graph.remove");
            assert_eq!(contract.target, TargetRequirement::ObservedResource);
            assert_eq!(contract.retry, RetrySemantics::DurableRequestKey);
        }

        #[tokio::test]
        async fn graph_remove_application_local_context_never_reaches_store() {
            let root = tempfile::tempdir().unwrap();
            let path = root.path().join("negative-remove.sqlite3");
            let project = seed_database(&path);
            let service = StudioService::open(path).unwrap();
            let graph = crate::semantic_graph_source_fixture::graph_for(&project, false);
            let handler = GraphRemoveHandler {
                binding: GraphStoreBinding {
                    service: service.clone(),
                },
            };
            assert_denied(
                handler
                    .invoke(
                        json!({"ref":"native:untrusted-test-marker",
                "graph_id":graph.id, "scene_id":graph.scene_id,
                "source_snapshot":graph.authorship.source_snapshot}),
                        &CallContext::application_local("remove-no-host").unwrap(),
                    )
                    .await,
            );
            assert_eq!(service.project(project.id).unwrap(), project);
        }

        #[test]
        fn graph_arguments_reject_serialized_readiness_or_authority_fields() {
            let graph = crate::semantic_graph_source_fixture::raw_graph(false);
            let source = json!({"ref":"native:opaque-test-marker","graph":graph});
            assert!(serde_json::from_value::<GraphUpsertArgs>(source.clone()).is_ok());
            for key in [
                "grant",
                "ready",
                "context",
                "store_binding",
                "operation_permit",
            ] {
                let mut changed = source.clone();
                changed
                    .as_object_mut()
                    .unwrap()
                    .insert(key.into(), json!(true));
                assert!(
                    serde_json::from_value::<GraphUpsertArgs>(changed).is_err(),
                    "serialized field cannot become native authority: {key}"
                );
            }
        }

        #[test]
        fn graph_application_key_is_optional_in_the_existing_contract() {
            let contract = graph_contract();
            let input = &contract.descriptor.input_schema;
            assert_eq!(input["required"], json!(["ref", "graph"]));
            assert_eq!(input["properties"]["request_id"]["type"], "string");
            assert_eq!(input["properties"]["request_id"]["minLength"], 1);
            assert_eq!(input["properties"]["request_id"]["maxLength"], 256);
            assert!(matches!(
                contract.target,
                TargetRequirement::ObservedResource
            ));
            assert_eq!(contract.retry, RetrySemantics::DurableRequestKey);
        }

        #[test]
        fn absent_graph_application_key_preserves_the_legacy_context_key() {
            let graph = crate::semantic_graph_source_fixture::raw_graph(false);
            let input: GraphUpsertArgs = serde_json::from_value(json!({
                "ref":"native:opaque-test-marker","graph":graph
            }))
            .unwrap();
            let context = CallContext::application_local("original-wire-call").unwrap();
            assert!(input.request_id.is_none());
            assert_eq!(
                graph_application_request_id(
                    input.request_id.as_deref(),
                    context.request_id(),
                    input.include_recovery,
                )
                .unwrap(),
                context.request_id()
            );
            let legacy = "k".repeat(256);
            assert_eq!(
                graph_application_request_id(None, &legacy, false).unwrap(),
                legacy
            );
            assert!(graph_application_request_id(None, &legacy, true).is_err());
        }

        #[test]
        fn graph_application_key_preserves_original_utf8_and_spaces() {
            let context = CallContext::application_local("wire-call-stays-original").unwrap();
            for raw in [
                "  graph:é:original  ".to_owned(),
                " ".to_owned(),
                "é".repeat(128),
            ] {
                let graph = crate::semantic_graph_source_fixture::raw_graph(false);
                let input: GraphUpsertArgs = serde_json::from_value(json!({
                    "ref":"native:opaque-test-marker","graph":graph,"request_id":raw
                }))
                .unwrap();
                let selected = graph_application_request_id(
                    input.request_id.as_deref(),
                    context.request_id(),
                    false,
                )
                .unwrap();
                assert_eq!(selected, raw);
                assert_eq!(selected.as_bytes(), raw.as_bytes());
                assert_eq!(context.request_id(), "wire-call-stays-original");
            }
        }

        #[test]
        fn graph_application_key_rejects_empty_oversize_and_control_bytes() {
            for raw in [
                String::new(),
                "k".repeat(257),
                "é".repeat(129),
                "line\nbreak".into(),
                "nul\0key".into(),
                "delete\u{7f}key".into(),
                "control\u{85}key".into(),
            ] {
                let error =
                    graph_application_request_id(Some(&raw), "wire-call", false).unwrap_err();
                assert_eq!(error.code, ErrorCode::InvalidArgument);
                assert!(error.outcome_known);
            }
        }

        #[test]
        fn present_graph_application_key_must_be_a_string_not_null_or_other_json() {
            let graph = crate::semantic_graph_source_fixture::raw_graph(false);
            for value in [Value::Null, json!(true), json!(7), json!([]), json!({})] {
                assert!(
                    serde_json::from_value::<GraphUpsertArgs>(json!({
                        "ref":"native:opaque-test-marker","graph":graph,"request_id":value
                    }))
                    .is_err()
                );
            }
        }

        #[test]
        fn graph_recovery_uses_the_existing_utf8_byte_limit_only_when_requested() {
            for raw in ["k".repeat(87), format!("{}x", "é".repeat(43))] {
                assert_eq!(
                    graph_application_request_id(Some(&raw), "wire-call", true).unwrap(),
                    raw
                );
            }
            for raw in ["k".repeat(88), "é".repeat(44)] {
                assert_eq!(
                    graph_application_request_id(Some(&raw), "wire-call", false).unwrap(),
                    raw
                );
                assert!(graph_application_request_id(Some(&raw), "wire-call", true).is_err());
            }
        }

        #[test]
        fn graph_application_key_routes_to_real_service_scope_and_recovery_without_authority() {
            struct ObserveThenDeny<'a> {
                expected_key: &'a str,
                wrong_key: &'a str,
                observed: std::cell::Cell<bool>,
            }
            impl SemanticGraphAdmission for ObserveThenDeny<'_> {
                fn acquire<'a>(
                    &'a self,
                    scope: &'a SemanticGraphWriteScope<'a>,
                ) -> StorageResult<Box<dyn SemanticGraphWriteLease + 'a>> {
                    check_graph_application_scope_key(scope, self.expected_key).unwrap();
                    let error =
                        check_graph_application_scope_key(scope, self.wrong_key).unwrap_err();
                    assert_eq!(error.code, ErrorCode::PermissionDenied);
                    self.observed.set(true);
                    // This observes the real Service scope and always refuses;
                    // it constructs no Host permit, lease or successful write.
                    Err(
                        DomainError::Invalid("Source-only scope observation ends here".into())
                            .into(),
                    )
                }
            }

            for supplied in [None, Some("graph:é:original-key")] {
                let root = tempfile::tempdir().unwrap();
                let path = root.path().join("application-key.sqlite3");
                let project = seed_database(&path);
                let service = StudioService::open(path).unwrap();
                let graph = crate::semantic_graph_source_fixture::graph_for(&project, false);
                let change = Change::UpsertSemanticGraph {
                    graph: Box::new(graph),
                };
                let context = CallContext::application_local("original-wire-call").unwrap();
                let request_id =
                    graph_application_request_id(supplied, context.request_id(), true).unwrap();
                let prepared =
                    prepare_apply_recovery(project.id, project.generation, request_id, &change)
                        .unwrap();
                let identity: RequestIdentity = serde_json::from_value(prepared.metadata).unwrap();
                assert_eq!(
                    decode_apply_recovery_identity(&identity).unwrap(),
                    (project.id, project.generation, request_id)
                );
                assert_eq!(
                    identity.request_sha256,
                    apply_request_digest_v1(project.id, &change).unwrap()
                );
                let before_history =
                    serde_json::to_value(service.history(project.id, 0, 100).unwrap()).unwrap();
                let admission = ObserveThenDeny {
                    expected_key: request_id,
                    wrong_key: if supplied.is_some() {
                        context.request_id()
                    } else {
                        "different-application-key"
                    },
                    observed: std::cell::Cell::new(false),
                };
                assert!(
                    service
                        .apply_semantic_graph_with_admission(request_id, &change, &admission)
                        .is_err()
                );
                assert!(admission.observed.get());
                assert_eq!(service.project(project.id).unwrap(), project);
                assert_eq!(
                    serde_json::to_value(service.history(project.id, 0, 100).unwrap()).unwrap(),
                    before_history
                );
                assert_eq!(context.request_id(), "original-wire-call");
            }
        }

        #[test]
        fn uncertain_native_error_retains_its_original_outcome_and_code() {
            let error = Error::new(ErrorCode::Cancelled, "original Host uncertainty").uncertain();
            let observed = native_failure(error).into_result().unwrap_err();
            assert_eq!(observed.code, ErrorCode::Cancelled);
            assert_eq!(observed.message, "original Host uncertainty");
            assert!(!observed.outcome_known);
        }

        #[test]
        fn each_admission_slot_preserves_only_its_first_original_error() {
            let first = RefCell::new(None);
            let _ = retain_admission_error(
                &first,
                Error::new(ErrorCode::Cancelled, "first request original"),
            );
            let _ = retain_admission_error(
                &first,
                Error::new(ErrorCode::StaleReference, "later conversion"),
            );
            assert_eq!(first.take().unwrap().code, ErrorCode::Cancelled);
            assert!(first.take().is_none());
            let second = RefCell::new(None);
            let _ = retain_admission_error(
                &second,
                Error::new(ErrorCode::PermissionDenied, "independent request"),
            );
            assert_eq!(second.take().unwrap().code, ErrorCode::PermissionDenied);
            assert!(first.take().is_none());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_storage::Store;
    use semwright_native_sdk::cooperation::{CallContext, Query};
    use semwright_native_sdk::{Driver, NativeDriver};

    fn fixture() -> (StudioService, Project) {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.keep().join("db.sqlite3");
        let mut store = Store::open(path).unwrap();
        let project = store.create_named_project("Native fixture").unwrap();
        (StudioService::from_store(store), project)
    }

    #[tokio::test]
    async fn public_native_sdk_exposes_bounded_motionwright_capabilities() {
        let (service, _) = fixture();
        let app = build_application(service).unwrap();
        let mut driver = NativeDriver::new(app).unwrap();
        let capabilities = driver.capabilities().await.unwrap();
        let names: Vec<_> = capabilities
            .iter()
            .map(|capability| capability.descriptor.name.as_str())
            .collect();
        assert!(names.contains(&"driver.motionwright.observe"));
        assert!(names.contains(&"driver.motionwright.project.rename"));
        assert!(names.contains(&"driver.motionwright.scene.add"));
        assert!(names.contains(&"driver.motionwright.scene.renderer.set"));
        assert!(names.contains(&"driver.motionwright.brief.set"));
        assert!(names.contains(&"driver.motionwright.canvas.node.add"));
        assert!(names.contains(&"driver.motionwright.canvas.node.remove"));
        assert!(names.contains(&"driver.motionwright.canvas.node.transform"));
        assert!(names.contains(&"driver.motionwright.canvas.keyframe.set"));
        assert!(names.contains(&"driver.motionwright.canvas.keyframe.remove"));
        assert!(names.contains(&"driver.motionwright.canvas.node.style.set"));
        assert!(names.contains(&"driver.motionwright.canvas.node.reparent"));
        assert!(names.contains(&"driver.motionwright.canvas.node.relations.set"));
        assert!(names.contains(&"driver.motionwright.scene.duration.set"));
        assert!(names.contains(&"driver.motionwright.scene.music.cut.snap"));
        assert!(names.contains(&"driver.motionwright.canvas.motion.out-cubic-position.set"));
        assert!(names.contains(&"driver.motionwright.asset.register"));
        assert!(names.contains(&"driver.motionwright.asset.remove"));
        assert!(names.contains(&"driver.motionwright.alternatives.select"));
        assert!(names.contains(&"driver.motionwright.model-invocation.record"));
        assert!(names.contains(&"driver.motionwright.branch.create"));
        assert!(names.contains(&"driver.motionwright.branch.checkout"));
        assert!(names.contains(&"driver.motionwright.branch.merge"));
        assert!(names.contains(&"driver.motionwright.review.add"));
        assert!(names.contains(&"driver.motionwright.review.resolve"));
        assert!(names.contains(&"driver.motionwright.review.reopen"));
        assert!(names.contains(&"driver.motionwright.audio.voice.active.set"));
        assert!(names.contains(&"driver.motionwright.audio.transcript.upsert"));
        assert!(names.contains(&"driver.motionwright.audio.cue.upsert"));
        assert!(names.contains(&"driver.motionwright.audio.mix.set"));
        assert!(names.contains(&"driver.motionwright.product-hero.upsert"));
        assert!(names.contains(&"driver.motionwright.product-stage.upsert"));
        assert!(names.contains(&"driver.motionwright.product-stage.detach"));
        assert!(names.contains(&"driver.motionwright.creative.patch.apply"));
        assert!(names.contains(&"driver.motionwright.creative.patch.undo"));
        assert!(names.contains(&"driver.motionwright.capture-source.register"));
        assert!(names.contains(&"driver.motionwright.claim.add"));
        assert!(names.contains(&"driver.motionwright.measured-table.upsert"));
        assert!(names.contains(&"driver.motionwright.measured-binding.upsert"));
        assert!(names.contains(&"driver.motionwright.measured-binding.detach"));
        assert!(names.contains(&"driver.motionwright.procedural-field.upsert"));
        assert!(names.contains(&"driver.motionwright.procedural-field.detach"));
        // Music conform, exact cubic-out motion, and bounded planar camera edits
        // remain independent source-bound Native SDK operations.
        assert!(names.contains(&"driver.motionwright.scene.music.cuts.conform"));
        assert!(names.contains(&"driver.motionwright.camera-reframe.upsert"));
        assert!(names.contains(&"driver.motionwright.camera-reframe.detach"));
        // Semantic SFX contributes two additional source-owned and revision-bound operations.
        assert!(names.contains(&"driver.motionwright.audio.sfx.upsert"));
        assert!(names.contains(&"driver.motionwright.audio.sfx.remove"));
        assert!(names.contains(&"driver.motionwright.creative-evaluator.register"));
        assert!(names.contains(&"driver.motionwright.creative-evaluator.evaluate"));
        assert!(names.contains(&"driver.motionwright.identity-origin.register"));
        assert!(names.contains(&"driver.motionwright.identity-origin.revoke"));

        assert!(names.contains(&"driver.motionwright.component-distillation.propose"));
        assert!(names.contains(&"driver.motionwright.component-distillation.review"));
        assert!(names.contains(&"driver.motionwright.product-stage.capture.bind"));
        assert!(names.contains(&"driver.motionwright.product-stage.capture.detach"));
        // Preserve both native SDK operation families admitted independently.
        assert!(names.contains(&"driver.motionwright.paid-generation.edit"));
        assert!(names.contains(&"driver.motionwright.otio-cut.owner-commit"));
        assert!(names.contains(&"driver.motionwright.brand-campaign.owner-rollout"));
        assert!(names.contains(&"driver.motionwright.narration.take.record"));
        assert!(names.contains(&"driver.motionwright.narration.take.release"));
        assert!(names.contains(&"driver.motionwright.operation.get"));
        assert_eq!(capabilities.len(), 82);
    }

    #[test]
    fn exact_out_cubic_native_sdk_operation_is_typed_and_fails_closed() {
        let scene_id = Uuid::now_v7();
        let node_id = Uuid::now_v7();
        let deliverable_id = Uuid::now_v7();
        let original = json!({
            "scene_id": scene_id.to_string(),
            "node_id": node_id.to_string(),
            "deliverable_id": deliverable_id.to_string(),
            "start_x": 125.0,
            "start_y": 330.0,
            "end_frame": 30
        });
        match change_from_args(OperationKind::SetCanvasOutCubicPositionMotion, &original).unwrap() {
            Change::SetCanvasOutCubicPositionMotion {
                scene_id: id,
                node_id: n,
                deliverable_id: p,
                start_x,
                start_y,
                end_frame,
            } => {
                assert_eq!((id, n, p), (scene_id, node_id, deliverable_id));
                assert_eq!((start_x, start_y, end_frame), (125.0, 330.0, 30));
            }
            _ => panic!("Native SDK lost exact authoring operation identity"),
        }
        for field in [
            "scene_id",
            "node_id",
            "deliverable_id",
            "start_x",
            "start_y",
            "end_frame",
        ] {
            let mut tampered = original.clone();
            tampered.as_object_mut().unwrap().remove(field);
            assert!(
                change_from_args(OperationKind::SetCanvasOutCubicPositionMotion, &tampered)
                    .is_err(),
                "required source field cannot be omitted: {field}"
            );
        }
        for value in [json!(-1), json!(0), json!(36001), json!(1.5), json!("30")] {
            let mut tampered = original.clone();
            tampered["end_frame"] = value;
            assert!(
                change_from_args(OperationKind::SetCanvasOutCubicPositionMotion, &tampered)
                    .is_err()
            );
        }
        let mut tampered = original;
        tampered["start_x"] = json!("NaN");
        assert!(
            change_from_args(OperationKind::SetCanvasOutCubicPositionMotion, &tampered).is_err()
        );
    }

    #[tokio::test]
    async fn application_local_observation_reads_the_same_project_state() {
        let (service, project) = fixture();
        let observer = MotionwrightObserver::new(service);
        let query = Query {
            resource: project.resource_key(),
            scope: "summary".into(),
            limit: 8,
            cursor: None,
        };
        let page = observer
            .observe(&query, &CallContext::application_local("ui-read").unwrap())
            .await
            .unwrap();
        assert_eq!(page.version.generation, project.generation.to_string());
        assert_eq!(page.version.revision.as_str(), "0");
        assert_eq!(page.items[0]["title"], "Native fixture");
    }

    #[test]
    fn every_operation_contract_keeps_semwright_provider_scope() {
        let (service, _) = fixture();
        let app = build_application(service).unwrap();
        for contract in app.contracts() {
            contract.validate(APP_ID).unwrap();
            assert_eq!(contract.descriptor.backends, vec!["driver:motionwright"]);
            assert!(contract.atomic_revision_cas);
            assert_eq!(contract.retry, RetrySemantics::DurableRequestKey);
        }
    }
    #[test]
    fn creative_operation_arguments_remain_typed_and_bounded() {
        let brief = change_from_args(
            OperationKind::SetBrief,
            &json!({
                "ref": "project:ignored-by-parser",
                "objective": "Explain native semantic control",
                "audience": "technical creators",
                "constraints": ["preserve evidence"],
                "exclusions": ["no fabricated PASS"]
            }),
        )
        .unwrap();
        assert!(matches!(brief, Change::SetBrief { .. }));

        let asset_id = Uuid::now_v7();
        let asset = change_from_args(
            OperationKind::AddAsset,
            &json!({
                "asset": {
                    "id": asset_id.to_string(),
                    "name": "voice.wav",
                    "media_type": "audio/wav",
                    "content_sha256": "ab".repeat(32),
                    "source_revision": "import-r1"
                }
            }),
        )
        .unwrap();
        assert!(matches!(
            asset,
            Change::AddAsset {
                asset: motionwright_domain::Asset { id, .. }
            } if id == asset_id
        ));

        let profile_id = Uuid::now_v7();
        let profile = change_from_args(
            OperationKind::UpsertDeliverable,
            &json!({
                "profile": {
                    "id": profile_id.to_string(),
                    "name": "Vertical review",
                    "width": 1080,
                    "height": 1920,
                    "language": "es-MX",
                    "captions": true,
                    "caption_format": "srt",
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "audio_sample_rate_hz": 48000,
                    "brand_profile": "launch",
                    "cut_label": "social",
                    "parent_profile_id": null,
                    "source_revision": null,
                    "framing_strategy": "replan",
                    "crop_approved": false,
                    "timing_locked": true,
                    "voice_track_id": null,
                    "text_overrides": {},
                    "included_scene_ids": [],
                    "protected_scene_ids": [],
                    "burn_in_captions": false,
                    "frame_rate": {"num": "30", "den": "1"},
                    "color_space": "rec709",
                    "container": "mp4",
                    "adaptation_notes": []
                }
            }),
        )
        .unwrap();
        assert!(matches!(
            profile,
            Change::UpsertDeliverable {
                profile: motionwright_domain::DeliverableProfile {
                    id,
                    timing_locked: true,
                    ..
                }
            } if id == profile_id
        ));

        let track_id = Uuid::now_v7();
        let segment_id = Uuid::now_v7();
        let segment = change_from_args(
            OperationKind::UpsertTranscriptSegment,
            &json!({
                "segment": {
                    "id": segment_id.to_string(),
                    "voice_track_id": track_id.to_string(),
                    "start": {"num": "1", "den": "2"},
                    "end": {"num": "3", "den": "2"},
                    "text": "Native audio evidence remains editable.",
                    "speaker": "Narrator",
                    "alignment": {"kind": "manual"}
                }
            }),
        )
        .unwrap();
        assert!(matches!(
            segment,
            Change::UpsertTranscriptSegment {
                segment: motionwright_domain::TranscriptSegment { id, .. }
            } if id == segment_id
        ));

        let mix = change_from_args(
            OperationKind::SetMixIntent,
            &json!({
                "mix": {
                    "voice_gain_db": 0.0,
                    "music_gain_db": -14.0,
                    "target_lufs": null,
                    "target_true_peak_dbfs": null
                }
            }),
        )
        .unwrap();
        assert!(matches!(mix, Change::SetMixIntent { .. }));

        let transform = change_from_args(
            OperationKind::TransformCanvasNode,
            &json!({
                "scene_id": Uuid::now_v7().to_string(),
                "node_id": Uuid::now_v7().to_string(),
                "transform": {
                    "x": 10.0,
                    "y": 20.0,
                    "width": 300.0,
                    "height": 120.0,
                    "rotation_deg": 4.0,
                    "opacity": 0.8
                }
            }),
        )
        .unwrap();
        assert!(matches!(transform, Change::TransformCanvasNode { .. }));

        let keyframe = change_from_args(
            OperationKind::SetCanvasKeyframe,
            &json!({
                "scene_id": Uuid::now_v7().to_string(),
                "node_id": Uuid::now_v7().to_string(),
                "keyframe": {
                    "at": {"num": "3", "den": "2"},
                    "property": "opacity",
                    "value": 0.4,
                    "interpolation": "ease_in_out"
                }
            }),
        )
        .unwrap();
        assert!(matches!(
            keyframe,
            Change::SetCanvasKeyframe {
                keyframe: motionwright_domain::CanvasKeyframe {
                    property: motionwright_domain::MotionProperty::Opacity,
                    interpolation: motionwright_domain::MotionInterpolation::EaseInOut,
                    ..
                },
                ..
            }
        ));

        let pair = change_from_args(
            OperationKind::SetCanvasPositionKeyframe,
            &json!({
                "scene_id": Uuid::now_v7().to_string(),
                "node_id": Uuid::now_v7().to_string(),
                "at": {"num": "3", "den": "2"},
                "x": 15.0,
                "y": 250.0,
                "interpolation": "ease_in_out"
            }),
        )
        .unwrap();
        match pair {
            Change::SetCanvasPositionKeyframe {
                x,
                y,
                interpolation,
                ..
            } => {
                assert_eq!(x, 15.0);
                assert_eq!(y, 250.0);
                assert_eq!(
                    interpolation,
                    motionwright_domain::MotionInterpolation::EaseInOut
                );
            }
            _ => panic!("Atomic X/Y operation returned a different creative change"),
        }
        let frame_move = change_from_args(
            OperationKind::SetCanvasLinearPositionMotion,
            &json!({
                "scene_id": Uuid::now_v7().to_string(),
                "node_id": Uuid::now_v7().to_string(),
                "deliverable_id": Uuid::now_v7().to_string(),
                "start_x": 120.0,
                "start_y": 250.0,
                "end_frame": 30
            }),
        )
        .unwrap();
        assert!(matches!(
            frame_move,
            Change::SetCanvasLinearPositionMotion {
                start_x: 120.0,
                start_y: 250.0,
                end_frame: 30,
                ..
            }
        ));
        assert!(
            change_from_args(
                OperationKind::SetCanvasLinearPositionMotion,
                &json!({
                    "scene_id": Uuid::now_v7().to_string(),
                    "node_id": Uuid::now_v7().to_string(),
                    "deliverable_id": Uuid::now_v7().to_string(),
                    "start_x": 120.0, "start_y": 250.0, "end_frame": -1
                })
            )
            .is_err()
        );
        assert!(
            change_from_args(
                OperationKind::SetCanvasLinearPositionMotion,
                &json!({
                    "scene_id": Uuid::now_v7().to_string(),
                    "node_id": Uuid::now_v7().to_string(),
                    "deliverable_id": Uuid::now_v7().to_string(),
                    "start_x": "unsafe", "start_y": 250.0, "end_frame": 30
                })
            )
            .is_err()
        );

        assert!(
            change_from_args(
                OperationKind::SetCanvasPositionKeyframe,
                &json!({
                    "scene_id": Uuid::now_v7().to_string(),
                    "node_id": Uuid::now_v7().to_string(),
                    "at": {"num": "3", "den": "2"},
                    "x": "invalid",
                    "y": 250.0,
                    "interpolation": "linear"
                })
            )
            .is_err()
        );
    }

    #[tokio::test]
    async fn creative_observation_scopes_read_application_owned_state() {
        let (service, project) = fixture();
        let observer = MotionwrightObserver::new(service);
        for scope in [
            "brief",
            "narrative",
            "audio",
            "deliverables",
            "visual-language",
            "canvas",
            "alternatives",
            "production-jobs",
            "history",
            "history-recent",
        ] {
            let query = Query {
                resource: project.resource_key(),
                scope: scope.into(),
                limit: 8,
                cursor: None,
            };
            let page = observer
                .observe(
                    &query,
                    &CallContext::application_local("creative-read").unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(page.scope, scope);
            assert_eq!(page.version.revision.as_str(), "0");
        }
    }

    #[test]
    fn native_project_array_cursors_cover_all_items_without_silent_truncation() {
        let (_, project) = fixture();
        let version = resource_version(&project).unwrap();
        // A project array can exceed the SDK's max page limit; each page
        // must make progress and complete only after all values are returned.
        let total = 271_usize;
        for limit in [1_u16, 3, 64, 256] {
            let mut cursor = None;
            let mut observed = Vec::new();
            let mut pages = 0;
            loop {
                pages += 1;
                assert!(pages <= 275, "project array continuation must terminate");
                let query = Query {
                    resource: project.resource_key(),
                    scope: "timeline".into(),
                    limit,
                    cursor,
                };
                let page = paginate_project_observation(
                    &query,
                    &version,
                    total,
                    |index| json!({"position": index}),
                )
                .unwrap();
                page.validate_for(&query).unwrap();
                observed.extend(
                    page.items
                        .iter()
                        .map(|value| value["position"].as_u64().unwrap()),
                );
                if page.complete {
                    assert!(page.next.is_none());
                    break;
                }
                let next = page.next.expect("incomplete array must provide cursor");
                assert_eq!(next.scope, query.scope);
                assert_eq!(next.version, version);
                cursor = Some(next);
            }
            assert_eq!(observed, (0..total as u64).collect::<Vec<_>>());
        }

        for total in [0, 1] {
            let query = Query {
                resource: project.resource_key(),
                scope: "brief".into(),
                limit: 1,
                cursor: None,
            };
            let page = paginate_project_observation(&query, &version, total, |_| json!(1)).unwrap();
            assert!(page.complete);
            assert!(page.next.is_none());
            assert_eq!(page.items.len(), total);
        }
    }

    #[test]
    fn native_project_array_cursors_reject_malformed_cross_scope_and_stale_versions() {
        let (_, project) = fixture();
        let version = resource_version(&project).unwrap();
        let query = Query {
            resource: project.resource_key(),
            scope: "timeline".into(),
            limit: 2,
            cursor: None,
        };
        let first =
            paginate_project_observation(&query, &version, 8, |index| json!(index)).unwrap();
        let good = first.next.unwrap();
        for token in [
            "",
            "offset:0",
            "offset:00",
            "offset:02",
            "offset:8",
            "offset:999",
            "offset:-2",
            "after-revision:2",
            "offset:not-a-number",
        ] {
            let mut forged = good.clone();
            forged.token = token.into();
            let next = Query {
                cursor: Some(forged),
                ..query.clone()
            };
            assert!(
                paginate_project_observation(&next, &version, 8, |index| json!(index)).is_err()
            );
        }
        let altered_scope = Query {
            scope: "canvas".into(),
            cursor: Some(good.clone()),
            ..query.clone()
        };
        assert!(
            paginate_project_observation(&altered_scope, &version, 8, |index| json!(index))
                .is_err()
        );
        let mut newer = project.clone();
        newer.revision += 1;
        let next = Query {
            cursor: Some(good),
            ..query
        };
        let later_version = resource_version(&newer).unwrap();
        assert!(
            paginate_project_observation(&next, &later_version, 8, |index| json!(index)).is_err()
        );
    }

    #[tokio::test]
    async fn native_asset_integrity_scopes_every_app_owned_digest_without_paths() {
        use motionwright_domain::Asset;
        let (service, mut project) = fixture();
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("native-integrity-fixture.bin");
        std::fs::write(&source, b"CAS source verified for Native SDK").unwrap();
        let blob = service.ingest_blob_file(&source).unwrap();
        let mut ids = Vec::new();
        for index in 0..19 {
            let id = Uuid::now_v7();
            ids.push(id.to_string());
            project = service
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("native-integrity-add-{index}"),
                    &Change::AddAsset {
                        asset: Asset {
                            id,
                            name: format!("Imported asset {index}"),
                            media_type: "application/octet-stream".into(),
                            content_sha256: Some(blob.sha256.clone()),
                            source_revision: Some("test-import".into()),
                        },
                    },
                )
                .unwrap()
                .project;
        }
        let observer = MotionwrightObserver::new(service.clone());
        let context = CallContext::application_local("source-bound-asset-integrity").unwrap();
        let mut cursor = None;
        let mut observed = Vec::new();
        let mut first_cursor = None;
        for iteration in 0..5 {
            let query = Query {
                resource: project.resource_key(),
                scope: "asset-integrity".into(),
                limit: 7,
                cursor,
            };
            let page = observer.observe(&query, &context).await.unwrap();
            page.validate_for(&query).unwrap();
            assert!(page.items.len() <= 7);
            for item in &page.items {
                assert_eq!(item["status"], "verified");
                assert_eq!(item["size_bytes"], blob.size_bytes);
                assert!(item.get("path").is_none());
                assert!(item.get("sha256").is_none());
                observed.push(item["asset_id"].as_str().unwrap().to_owned());
            }
            if page.complete {
                assert_eq!(iteration, 2);
                assert!(page.next.is_none());
                break;
            }
            if first_cursor.is_none() {
                first_cursor = page.next.clone();
            }
            cursor = page.next;
            assert!(cursor.is_some());
        }
        assert_eq!(observed, ids);
        let first = first_cursor.unwrap();
        for token in [
            "offset:7",
            "assets:v1:8:7",
            "assets:v1:7:0",
            "assets:v1:7:07",
            "assets:v1:7:1000",
        ] {
            let mut forged = first.clone();
            forged.token = token.into();
            assert!(
                observer
                    .observe(
                        &Query {
                            resource: project.resource_key(),
                            scope: "asset-integrity".into(),
                            limit: 7,
                            cursor: Some(forged),
                        },
                        &context
                    )
                    .await
                    .is_err()
            );
        }
        let current = service.project(project.id).unwrap();
        let changed = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-integrity-revision-change",
                &Change::RenameProject {
                    title: "Current after integrity".into(),
                },
            )
            .unwrap()
            .project;
        assert_eq!(changed.revision, current.revision + 1);
        let stale = observer
            .observe(
                &Query {
                    resource: project.resource_key(),
                    scope: "asset-integrity".into(),
                    limit: 7,
                    cursor: Some(first),
                },
                &context,
            )
            .await
            .unwrap_err();
        assert_eq!(stale.code, ErrorCode::StaleReference);
    }

    #[tokio::test]
    async fn native_asset_integrity_does_not_fabricate_empty_or_undigested_bytes() {
        let (service, project) = fixture();
        let observer = MotionwrightObserver::new(service);
        let context = CallContext::application_local("native-integrity-empty").unwrap();
        let query = Query {
            resource: project.resource_key(),
            scope: "asset-integrity".into(),
            limit: 16,
            cursor: None,
        };
        let page = observer.observe(&query, &context).await.unwrap();
        assert!(page.complete);
        assert!(page.items.is_empty());
        assert!(page.next.is_none());
    }

    #[tokio::test]
    async fn native_scene_and_canvas_observation_enumerate_every_authored_scene() {
        let (service, mut project) = fixture();
        for index in 0..11 {
            project = service
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("native-project-page-scene-{index}"),
                    &Change::AddScene {
                        name: format!("Real scene {index}"),
                        objective: format!("Objective {index}"),
                        duration_seconds: 2,
                    },
                )
                .unwrap()
                .project;
        }
        let observer = MotionwrightObserver::new(service.clone());
        let context = CallContext::application_local("native-project-paging").unwrap();
        for (scope, key) in [("timeline", "id"), ("canvas", "scene_id")] {
            let mut cursor = None;
            let mut ids = Vec::new();
            let mut page_count = 0;
            loop {
                page_count += 1;
                assert!(page_count <= 5, "scope pagination must terminate");
                let query = Query {
                    resource: project.resource_key(),
                    scope: scope.into(),
                    limit: 3,
                    cursor,
                };
                let page = observer.observe(&query, &context).await.unwrap();
                ids.extend(
                    page.items
                        .iter()
                        .map(|entry| entry[key].as_str().unwrap().to_owned()),
                );
                if page.complete {
                    assert!(page.next.is_none());
                    break;
                }
                cursor = Some(page.next.unwrap());
            }
            assert_eq!(page_count, 4);
            assert_eq!(
                ids,
                project
                    .scenes
                    .iter()
                    .map(|scene| scene.id.to_string())
                    .collect::<Vec<_>>()
            );
        }

        let initial = observer
            .observe(
                &Query {
                    resource: project.resource_key(),
                    scope: "timeline".into(),
                    limit: 3,
                    cursor: None,
                },
                &context,
            )
            .await
            .unwrap();
        let previous = initial.next.unwrap();
        let latest = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-project-page-stale",
                &Change::RenameProject {
                    title: "Changed after first page".into(),
                },
            )
            .unwrap()
            .project;
        assert!(latest.revision > project.revision);
        let error = observer
            .observe(
                &Query {
                    resource: project.resource_key(),
                    scope: "timeline".into(),
                    limit: 3,
                    cursor: Some(previous),
                },
                &context,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
    }

    #[tokio::test]
    async fn native_empty_production_windows_are_provably_complete() {
        let (service, project) = fixture();
        let observer = MotionwrightObserver::new(service);
        let context = CallContext::application_local("native-empty-production").unwrap();
        for scope in ["production-jobs", "production-receipts"] {
            let query = Query {
                resource: project.resource_key(),
                scope: scope.into(),
                limit: 1,
                cursor: None,
            };
            let page = observer.observe(&query, &context).await.unwrap();
            assert!(page.items.is_empty());
            assert!(page.complete);
            assert!(page.next.is_none());
            let attempted = observer
                .observe(
                    &Query {
                        cursor: Some(PageCursor {
                            version: page.version,
                            scope: scope.into(),
                            token: "offset:1".into(),
                        }),
                        ..query
                    },
                    &context,
                )
                .await;
            assert!(attempted.is_err());
        }
    }

    #[tokio::test]
    async fn native_production_receipts_and_jobs_cover_more_than_old_256_receipt_window() {
        use motionwright_service::ProductionReceiptInput;
        let (service, project) = fixture();
        let digest = "a".repeat(64);
        // 85 separate jobs x 4 observations = 340 append-only receipts.
        // The preexisting UI reads only the latest 256 and caps jobs at 64.
        for index in 0..340 {
            let ordinal = index / 4;
            let command = if index % 4 == 0 {
                "driver.motion-canvas.render.start"
            } else {
                "driver.motion-canvas.render.status"
            };
            let payload = json!({
                "result": {
                    "data": {
                        "job_ref": format!("actual-job-{ordinal:03}"),
                        "state": "running",
                        // Provider-owned output may contain private text.
                        "private_path": "/private/do-not-expose/recording.wav"
                    }
                }
            });
            service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: format!("job-{ordinal:03}-observation-{index}"),
                    request_sha256: digest.clone(),
                    command: command.into(),
                    stage: "completed".into(),
                    payload,
                })
                .unwrap();
        }
        assert_eq!(
            service.production_receipts(project.id, 256).unwrap().len(),
            256
        );
        assert_eq!(service.production_jobs(project.id, 256).unwrap().len(), 64);
        let observer = MotionwrightObserver::new(service.clone());
        let context = CallContext::application_local("native-full-production").unwrap();
        for (scope, limit, expected) in [
            ("production-receipts", 17_u16, 340_usize),
            ("production-jobs", 11_u16, 85_usize),
        ] {
            let mut cursor = None;
            let mut values = Vec::new();
            for _ in 0..32 {
                let query = Query {
                    resource: project.resource_key(),
                    scope: scope.into(),
                    limit,
                    cursor,
                };
                let page = observer.observe(&query, &context).await.unwrap();
                assert!(page.items.len() <= usize::from(limit));
                for item in &page.items {
                    if scope == "production-receipts" {
                        assert!(item.get("payload").is_none());
                        assert!(!item.to_string().contains("/private/do-not-expose/"));
                        values.push(item["id"].as_str().unwrap().to_owned());
                    } else {
                        assert_eq!(item["local_observations"].as_u64(), Some(4));
                        values.push(item["job_ref"].as_str().unwrap().to_owned());
                    }
                }
                if page.complete {
                    assert!(page.next.is_none());
                    break;
                }
                cursor = page.next;
                assert!(cursor.is_some());
            }
            assert_eq!(values.len(), expected);
            let unique: std::collections::HashSet<_> = values.iter().collect();
            assert_eq!(unique.len(), expected, "{scope} must not duplicate rows");
        }

        // Receipt observations change independently of creative revisions.
        let first = observer
            .observe(
                &Query {
                    resource: project.resource_key(),
                    scope: "production-receipts".into(),
                    limit: 3,
                    cursor: None,
                },
                &context,
            )
            .await
            .unwrap();
        let prior_receipt_cursor = first.next.unwrap();
        let prior_jobs_cursor = observer
            .observe(
                &Query {
                    resource: project.resource_key(),
                    scope: "production-jobs".into(),
                    limit: 3,
                    cursor: None,
                },
                &context,
            )
            .await
            .unwrap()
            .next
            .unwrap();
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: "late-new-status-without-creative-commit".into(),
                request_sha256: digest,
                command: "driver.motion-canvas.render.status".into(),
                stage: "completed".into(),
                payload: json!({"result":{"data":{
                    "job_ref":"actual-job-000","state":"succeeded"
                }}}),
            })
            .unwrap();
        assert_eq!(
            service.project(project.id).unwrap().revision,
            project.revision
        );
        for (scope, cursor) in [
            ("production-receipts", prior_receipt_cursor),
            ("production-jobs", prior_jobs_cursor),
        ] {
            let error = observer
                .observe(
                    &Query {
                        resource: project.resource_key(),
                        scope: scope.into(),
                        limit: 3,
                        cursor: Some(cursor),
                    },
                    &context,
                )
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::StaleReference);
        }
    }

    fn append_receipt_order_fixture(
        service: &StudioService,
        project: &Project,
        key: &str,
        command: &str,
        stage: &str,
        payload: Value,
    ) -> motionwright_storage::ProductionReceipt {
        service
            .append_production_receipt(motionwright_storage::ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: key.into(),
                request_sha256: "a".repeat(64),
                command: command.into(),
                stage: stage.into(),
                payload,
            })
            .unwrap()
    }

    fn advance_receipt_order_fixture_clock(path: &std::path::Path, id: Uuid) -> Uuid {
        // Retained bytes simulate an earlier process with an advanced clock.
        // No host clock, provider, Broker or real-effect operation is touched.
        let db = crate::sqlite_v6_corruptible::open_v6_writer(path);
        db.execute_batch("PRAGMA foreign_keys=ON").unwrap();
        let ordinal: i64 = db
            .query_row(
                "SELECT sequence FROM production_receipt_order WHERE receipt_id=?1",
                [id.to_string()],
                |row| row.get(0),
            )
            .unwrap();
        let advanced = Uuid::from_u128(id.as_u128() + (86_400_000_u128 << 80));
        assert_eq!(
            db.execute(
                "UPDATE production_receipts SET receipt_id=?1,created_at='2100-01-01T00:00:00Z' WHERE receipt_id=?2",
                rusqlite::params![advanced.to_string(), id.to_string()],
            )
            .unwrap(),
            1
        );
        assert_eq!(
            db.query_row(
                "SELECT sequence FROM production_receipt_order WHERE receipt_id=?1",
                [advanced.to_string()],
                |row| row.get::<_, i64>(0),
            )
            .unwrap(),
            ordinal
        );
        advanced
    }

    #[tokio::test]
    async fn native_receipt_pages_follow_persisted_order_across_clock_correction() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("native-receipt-clock.sqlite3");
        let service = StudioService::open(&path).unwrap();
        let other = service.create_project("Foreign receipt owner").unwrap();
        let foreign = append_receipt_order_fixture(
            &service,
            &other,
            "foreign-first",
            "driver.motion-canvas.composition.inspect",
            "completed",
            json!({}),
        );
        let project = service.create_project("Native commit order").unwrap();
        let mut ids = Vec::new();
        for index in 0..270 {
            ids.push(
                append_receipt_order_fixture(
                    &service,
                    &project,
                    &format!("observation-{index}"),
                    "driver.motion-canvas.composition.inspect",
                    "completed",
                    json!({"private_fixture_payload":"must-not-be-exposed"}),
                )
                .id,
            );
        }
        drop(service);
        // Page one has indices 269..253; its continuation UUID exceeds the
        // newest UUID even though that receipt was committed earlier.
        ids[253] = advance_receipt_order_fixture_clock(&path, ids[253]);
        assert!(ids[253] > ids[269]);
        let service = StudioService::open(&path).unwrap();
        let head = service.production_receipt_watermark(project.id).unwrap();
        assert_eq!(head.latest_id, Some(ids[269]));
        assert_eq!(head.count, 270);
        let observer = MotionwrightObserver::new(service.clone());
        let context = CallContext::application_local("native-clock-order").unwrap();
        let mut query = Query {
            resource: project.resource_key(),
            scope: "production-receipts".into(),
            limit: 17,
            cursor: None,
        };
        let first = observer.observe(&query, &context).await.unwrap();
        let version = first.version.clone();
        let first_cursor = first.next.clone().unwrap();
        assert_eq!(first.items.last().unwrap()["id"], ids[253].to_string());
        let mut observed = Vec::new();
        let mut page = first;
        let mut completed = false;
        for _ in 0..17 {
            for item in &page.items {
                assert!(item.get("payload").is_none());
                assert!(!item.to_string().contains("must-not-be-exposed"));
                observed.push(Uuid::parse_str(item["id"].as_str().unwrap()).unwrap());
            }
            if page.complete {
                assert!(page.next.is_none());
                completed = true;
                break;
            }
            query.cursor = page.next;
            assert!(query.cursor.is_some());
            page = observer.observe(&query, &context).await.unwrap();
        }
        assert!(completed, "the complete retained snapshot must terminate");
        ids.reverse();
        assert_eq!(observed, ids, "no omissions, duplicates or UUID sorting");
        for invalid in [foreign.id, Uuid::nil()] {
            query.cursor = Some(
                production_cursor(&query, &version, "receipts", &head, &invalid.to_string())
                    .unwrap(),
            );
            assert_eq!(
                observer.observe(&query, &context).await.unwrap_err().code,
                ErrorCode::InvalidArgument
            );
        }
        append_receipt_order_fixture(
            &service,
            &project,
            "new-snapshot",
            "driver.motion-canvas.composition.inspect",
            "completed",
            json!({}),
        );
        query.cursor = Some(first_cursor);
        assert_eq!(
            observer.observe(&query, &context).await.unwrap_err().code,
            ErrorCode::StaleReference
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn native_job_pages_rank_commit_order_without_rewriting_display_dates() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("native-job-clock.sqlite3");
        let service = StudioService::open(&path).unwrap();
        let project = service.create_project("Native committed jobs").unwrap();
        let job_payload = |reference: &str| {
            json!({
                "job_ref":reference,
                "result":{
                    "data":{"job_ref":reference,"state":"queued"},
                    "execution":{"provenance":{
                        "provider":"driver:motion-canvas","provider_generation":17
                    }}
                }
            })
        };
        let older = append_receipt_order_fixture(
            &service,
            &project,
            "older-start",
            "driver.motion-canvas.render.start",
            "completed",
            job_payload("a-older"),
        );
        drop(service);
        let advanced = advance_receipt_order_fixture_clock(&path, older.id);
        let service = StudioService::open(&path).unwrap();
        let newer = append_receipt_order_fixture(
            &service,
            &project,
            "newer-start",
            "driver.motion-canvas.render.start",
            "completed",
            job_payload("z-newer"),
        );
        assert!(newer.id < advanced);
        let observer = MotionwrightObserver::new(service.clone());
        let context = CallContext::application_local("native-job-commit-order").unwrap();
        let mut query = Query {
            resource: project.resource_key(),
            scope: "production-jobs".into(),
            limit: 1,
            cursor: None,
        };
        let first = observer.observe(&query, &context).await.unwrap();
        assert_eq!(first.items.len(), 1);
        assert_eq!(first.items[0]["job_ref"], "z-newer");
        assert_eq!(first.items[0]["root_request_id"], "newer-start");
        assert_eq!(first.items[0]["provider_generation"], 17);
        assert!(!first.complete);
        query.cursor = first.next;
        let last = observer.observe(&query, &context).await.unwrap();
        assert!(last.complete);
        assert!(last.next.is_none());
        assert_eq!(last.items.len(), 1);
        assert_eq!(last.items[0]["job_ref"], "a-older");
        assert_eq!(last.items[0]["root_request_id"], "older-start");
        assert_eq!(last.items[0]["created_at"], "2100-01-01T00:00:00Z");
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn native_history_cursor_pages_remain_revision_bound_and_complete() {
        let (service, mut project) = fixture();
        for revision in 1..=9 {
            let outcome = service
                .apply(
                    project.id,
                    &RevisionStamp::from(&project),
                    &format!("native-history-request-{revision}"),
                    &Change::RenameProject {
                        title: format!("Revision {revision}"),
                    },
                )
                .unwrap();
            project = outcome.project;
        }
        let observer = MotionwrightObserver::new(service.clone());
        let context = CallContext::application_local("native-history-reader").unwrap();

        for (scope, expected) in [
            ("history", (1..=9).collect::<Vec<_>>()),
            ("history-recent", (1..=9).rev().collect::<Vec<_>>()),
        ] {
            let mut cursor = None;
            let mut revisions = Vec::new();
            let mut pages = 0;
            loop {
                pages += 1;
                assert!(pages <= 4, "history continuation must finish");
                let query = Query {
                    resource: project.resource_key(),
                    scope: scope.into(),
                    limit: 3,
                    cursor,
                };
                let page = observer.observe(&query, &context).await.unwrap();
                assert_eq!(page.items.len(), 3);
                for value in &page.items {
                    revisions.push(value["revision"].as_u64().unwrap());
                }
                if page.complete {
                    assert!(page.next.is_none());
                    break;
                }
                cursor = Some(page.next.expect("incomplete page requires a cursor"));
            }
            assert_eq!(pages, 3);
            assert_eq!(revisions, expected);
        }

        let first = observer
            .observe(
                &Query {
                    resource: project.resource_key(),
                    scope: "history-recent".into(),
                    limit: 3,
                    cursor: None,
                },
                &context,
            )
            .await
            .unwrap();
        let valid_cursor = first.next.expect("recent history must continue");
        let mut forged = valid_cursor.clone();
        forged.token = "after-revision:5".into();
        assert!(
            observer
                .observe(
                    &Query {
                        resource: project.resource_key(),
                        scope: "history-recent".into(),
                        limit: 3,
                        cursor: Some(forged),
                    },
                    &context,
                )
                .await
                .is_err()
        );

        let _committed = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "native-history-new-revision",
                &Change::RenameProject {
                    title: "Changed after cursor".into(),
                },
            )
            .unwrap();
        assert!(
            observer
                .observe(
                    &Query {
                        resource: project.resource_key(),
                        scope: "history-recent".into(),
                        limit: 3,
                        cursor: Some(valid_cursor),
                    },
                    &context,
                )
                .await
                .is_err()
        );
    }
}

#[cfg(test)]
mod music_clock_contract_tests {
    use super::*;

    #[test]
    fn native_sdk_parses_exact_source_and_revision_bound_cut_intent() {
        let scene_id = Uuid::new_v4();
        let digest = "a".repeat(64);
        let arguments = json!({
            "ref": "opaque-application-ref",
            "scene_id": scene_id.to_string(),
            "beat_index": 3,
            "source_sha256": digest,
            "base_revision": 8,
            "acknowledge_absolute_anchors": false,
        });
        match change_from_args(OperationKind::SnapSceneCutToMusic, &arguments).unwrap() {
            Change::SnapSceneCutToMusic {
                scene_id: actual,
                beat_index,
                source_sha256,
                base_revision,
                acknowledge_absolute_anchors,
            } => {
                assert_eq!(actual, scene_id);
                assert_eq!(beat_index, 3);
                assert_eq!(source_sha256, "a".repeat(64));
                assert_eq!(base_revision, 8);
                assert!(!acknowledge_absolute_anchors);
            }
            _ => panic!("the Native SDK mapped the wrong semantic mutation"),
        }
    }

    #[test]
    fn native_sdk_requires_explicit_impact_acknowledgement_and_bounded_indices() {
        let valid = json!({
            "ref": "opaque-application-ref",
            "scene_id": Uuid::new_v4().to_string(),
            "beat_index": 0,
            "source_sha256": "a".repeat(64),
            "base_revision": 0,
            "acknowledge_absolute_anchors": false,
        });
        for key in [
            "scene_id",
            "beat_index",
            "source_sha256",
            "base_revision",
            "acknowledge_absolute_anchors",
        ] {
            let mut changed = valid.clone();
            changed.as_object_mut().unwrap().remove(key);
            assert!(
                change_from_args(OperationKind::SnapSceneCutToMusic, &changed).is_err(),
                "{key} cannot be omitted"
            );
        }
        let mut changed = valid.clone();
        changed["beat_index"] = json!(20_000);
        assert!(change_from_args(OperationKind::SnapSceneCutToMusic, &changed).is_err());
        changed["beat_index"] = json!(-1);
        assert!(change_from_args(OperationKind::SnapSceneCutToMusic, &changed).is_err());
        changed["beat_index"] = json!(3.5);
        assert!(change_from_args(OperationKind::SnapSceneCutToMusic, &changed).is_err());
        changed["beat_index"] = json!(0);
        changed["acknowledge_absolute_anchors"] = json!("yes");
        assert!(change_from_args(OperationKind::SnapSceneCutToMusic, &changed).is_err());
    }

    #[test]
    fn native_sdk_maps_one_atomic_music_conform_with_two_explicit_cut_targets() {
        let opening = Uuid::new_v4();
        let proof = Uuid::new_v4();
        let args = json!({
            "ref": "native-sdk-opaque-project-ref",
            "targets":[
                {"scene_id":opening.to_string(),"beat_index":2},
                {"scene_id":proof.to_string(),"beat_index":4}
            ],
            "source_sha256":"a".repeat(64),
            "base_revision":44,
            "acknowledge_absolute_anchors":true,
        });
        match change_from_args(OperationKind::ConformScenesToMusic, &args).unwrap() {
            Change::ConformScenesToMusic {
                targets,
                source_sha256,
                base_revision,
                acknowledge_absolute_anchors,
            } => {
                assert_eq!(targets.len(), 2);
                assert_eq!(targets[0].scene_id, opening);
                assert_eq!(targets[0].beat_index, 2);
                assert_eq!(targets[1].scene_id, proof);
                assert_eq!(targets[1].beat_index, 4);
                assert_eq!(source_sha256, "a".repeat(64));
                assert_eq!(base_revision, 44);
                assert!(acknowledge_absolute_anchors);
            }
            _ => panic!("incorrect Semwright Native SDK semantic conform intent"),
        }
    }

    #[test]
    fn native_sdk_fails_closed_on_missing_or_forged_multi_cut_fields() {
        let valid = json!({
            "ref":"native-sdk-opaque-project-ref",
            "targets":[
                {"scene_id":Uuid::new_v4().to_string(),"beat_index":2},
                {"scene_id":Uuid::new_v4().to_string(),"beat_index":4}
            ],
            "source_sha256":"a".repeat(64),
            "base_revision":5,
            "acknowledge_absolute_anchors":false,
        });
        for key in [
            "targets",
            "source_sha256",
            "base_revision",
            "acknowledge_absolute_anchors",
        ] {
            let mut args = valid.clone();
            args.as_object_mut().unwrap().remove(key);
            assert!(
                change_from_args(OperationKind::ConformScenesToMusic, &args).is_err(),
                "required field {key} cannot be dropped"
            );
        }
        let mut empty = valid.clone();
        empty["targets"] = json!([]);
        assert!(change_from_args(OperationKind::ConformScenesToMusic, &empty).is_err());
        let mut giant = valid.clone();
        giant["targets"] = json!(vec![
            json!({"scene_id":Uuid::new_v4().to_string(),"beat_index":2});
            MAX_MUSIC_CONFORM_TARGETS + 1
        ]);
        assert!(change_from_args(OperationKind::ConformScenesToMusic, &giant).is_err());
        let mut malformed = valid.clone();
        malformed["targets"][1]["beat_index"] = json!(-1);
        assert!(change_from_args(OperationKind::ConformScenesToMusic, &malformed).is_err());
        malformed["targets"][1]["beat_index"] = json!(1);
        malformed["targets"][1]["unknown"] = json!("injected");
        assert!(change_from_args(OperationKind::ConformScenesToMusic, &malformed).is_err());
        malformed = valid.clone();
        malformed["acknowledge_absolute_anchors"] = json!("yes");
        assert!(change_from_args(OperationKind::ConformScenesToMusic, &malformed).is_err());
    }
}

#[cfg(test)]
mod generation_journal_tests;

#[cfg(test)]
mod otio_cut_tests;

#[cfg(test)]
mod brand_campaign_tests;

#[cfg(test)]
mod narration_tests;

/// Original-source, bounded PCM preview only; native SDK remains host authority.
pub mod original_mix;

/// Whether a change needs an owner-qualified audio ingress instead of a generic edit.
/// Both the Native SDK and desktop shell use this same boundary.
pub fn requires_qualified_audio_boundary(change: &Change) -> bool {
    match change {
        Change::ImportMeasuredVoice { .. }
        | Change::AddVoiceTrack { .. }
        | Change::ApplyVoiceAlignment { .. } => true,
        Change::AddTranscriptSegment { segment } | Change::UpsertTranscriptSegment { segment } => {
            matches!(
                segment.alignment,
                motionwright_domain::AlignmentEvidence::Measured { .. }
            )
        }
        Change::AddAudioCue { cue } | Change::UpsertAudioCue { cue } => {
            matches!(
                cue.evidence,
                motionwright_domain::CueEvidence::Measured
                    | motionwright_domain::CueEvidence::TranscriptAligned
            )
        }
        _ => false,
    }
}

fn guard_generic_audio_change(change: &Change) -> NativeResult<()> {
    if requires_qualified_audio_boundary(change) {
        return Err(Error::invalid(
            "audio evidence is reserved for a qualified audio boundary",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod qualified_audio_boundary_tests {
    use super::*;
    use serde_json::{Value, json};

    const VOICE_ID: &str = "11111111-1111-4111-8111-111111111111";
    const ASSET_ID: &str = "22222222-2222-4222-8222-222222222222";
    const SEGMENT_ID: &str = "33333333-3333-4333-8333-333333333333";
    const CUE_ID: &str = "44444444-4444-4444-8444-444444444444";
    const RECEIPT_ID: &str = "55555555-5555-4555-8555-555555555555";

    fn segment_wire(kind: &str) -> Value {
        let alignment = match kind {
            "manual" => json!({"kind": "manual"}),
            "unknown" => json!({"kind": "unknown"}),
            "measured" => json!({
                "kind": "measured",
                "engine": "controlled-fixture-not-native",
                "source_sha256": "ab".repeat(32),
                "confidence_millis": null
            }),
            _ => panic!("unexpected segment fixture evidence"),
        };
        json!({
            "id": SEGMENT_ID, "voice_track_id": VOICE_ID,
            "start": {"num": "1", "den": "2"}, "end": {"num": "3", "den": "2"},
            "text": "hello", "speaker": null, "alignment": alignment
        })
    }

    fn cue_wire(evidence: &str) -> Value {
        assert!(matches!(
            evidence,
            "manual" | "unknown" | "measured" | "transcript_aligned"
        ));
        json!({
            "id": CUE_ID, "label": "controlled fixture",
            "at": {"num": "1", "den": "2"}, "source_segment_id": SEGMENT_ID,
            "evidence": evidence
        })
    }

    fn transcript_change(operation: &str, kind: &str) -> Change {
        serde_json::from_value(json!({"type": operation, "segment": segment_wire(kind)}))
            .expect("real transcript Change fixture must deserialize")
    }

    fn cue_change(operation: &str, evidence: &str) -> Change {
        serde_json::from_value(json!({"type": operation, "cue": cue_wire(evidence)}))
            .expect("real cue Change fixture must deserialize")
    }

    fn voice_alignment_change(empty_roster: bool) -> Change {
        let segments = if empty_roster {
            vec![]
        } else {
            vec![segment_wire("measured")]
        };
        let cues = if empty_roster {
            vec![]
        } else {
            vec![cue_wire("transcript_aligned")]
        };
        serde_json::from_value(json!({
            "type": "apply_voice_alignment",
            "application": {
                "profile": "acc007-en-ctc-1", "voice_track_id": VOICE_ID, "asset_id": ASSET_ID,
                "source_sha256": "ab".repeat(32), "transcript_version": 1,
                "transcript_text": "hello",
                "transcript_sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                "receipt": {
                    "id": RECEIPT_ID, "request_id": "controlled-fixture-not-native-1",
                    "request_sha256": "cd".repeat(32), "command": "controlled-fixture-not-native",
                    "receipt_sha256": "ef".repeat(32)
                },
                "segments": segments, "cues": cues
            }
        })).expect("real ApplyVoiceAlignment fixture must deserialize")
    }

    #[test]
    fn generic_guard_rejects_populated_voice_alignment() {
        let change = voice_alignment_change(false);
        let Change::ApplyVoiceAlignment { application } = &change else {
            panic!("fixture must be ApplyVoiceAlignment");
        };
        assert_eq!(application.segments.len(), 1);
        assert_eq!(application.cues.len(), 1);
        assert!(guard_generic_audio_change(&change).is_err());
    }
    #[test]
    fn generic_guard_rejects_voice_alignment_with_empty_roster() {
        let change = voice_alignment_change(true);
        let Change::ApplyVoiceAlignment { application } = &change else {
            panic!("fixture must be ApplyVoiceAlignment");
        };
        assert!(application.segments.is_empty());
        assert!(application.cues.is_empty());
        assert!(guard_generic_audio_change(&change).is_err());
    }
    #[test]
    fn transcript_parser_preserves_manual_and_unknown_evidence() {
        for kind in ["manual", "unknown"] {
            let args = json!({"segment": segment_wire(kind)});
            let change = change_from_args(OperationKind::UpsertTranscriptSegment, &args)
                .expect("manual and unknown transcript edits must remain available");
            assert!(matches!(&change, Change::UpsertTranscriptSegment { .. }));
            assert!(guard_generic_audio_change(&change).is_ok());
            let wire = serde_json::to_value(&change).expect("Change must serialize");
            assert_eq!(wire["segment"]["alignment"], json!({"kind": kind}));
        }
    }
    #[test]
    fn transcript_parser_still_rejects_measured_evidence() {
        let decoded = transcript_change("upsert_transcript_segment", "measured");
        assert!(matches!(decoded, Change::UpsertTranscriptSegment { .. }));
        let args = json!({"segment": segment_wire("measured")});
        let error = change_from_args(OperationKind::UpsertTranscriptSegment, &args)
            .expect_err("measured alignment must not enter through the generic parser");
        assert!(
            error.to_string().contains(
                "measured transcript alignment is reserved for a qualified alignment boundary"
            ),
            "rejection must come from the existing qualified-alignment boundary"
        );
    }
    #[test]
    fn cue_parser_preserves_manual_and_unknown_evidence() {
        for evidence in ["manual", "unknown"] {
            let args = json!({"cue": cue_wire(evidence)});
            let change = change_from_args(OperationKind::UpsertAudioCue, &args)
                .expect("manual and unknown cue edits must remain available");
            assert!(matches!(&change, Change::UpsertAudioCue { .. }));
            assert!(guard_generic_audio_change(&change).is_ok());
            let wire = serde_json::to_value(&change).expect("Change must serialize");
            assert_eq!(wire["cue"]["evidence"], json!(evidence));
        }
    }
    #[test]
    fn cue_parser_still_rejects_measured_and_transcript_aligned_evidence() {
        for evidence in ["measured", "transcript_aligned"] {
            let decoded = cue_change("upsert_audio_cue", evidence);
            assert!(matches!(decoded, Change::UpsertAudioCue { .. }));
            let args = json!({"cue": cue_wire(evidence)});
            let error = change_from_args(OperationKind::UpsertAudioCue, &args)
                .expect_err("qualified cue evidence must not enter through the generic parser");
            assert!(
                error.to_string().contains("qualified"),
                "rejection must come from the existing qualified cue boundary"
            );
        }
    }
    #[test]
    fn generic_guard_allows_manual_and_unknown_transcript_edits() {
        for operation in ["add_transcript_segment", "upsert_transcript_segment"] {
            for kind in ["manual", "unknown"] {
                let change = transcript_change(operation, kind);
                assert!(!requires_qualified_audio_boundary(&change));
            }
        }
    }
    #[test]
    fn generic_guard_still_rejects_measured_transcript_edits() {
        for operation in ["add_transcript_segment", "upsert_transcript_segment"] {
            let change = transcript_change(operation, "measured");
            assert!(requires_qualified_audio_boundary(&change));
        }
    }
    #[test]
    fn generic_guard_allows_manual_and_unknown_cue_edits() {
        for operation in ["add_audio_cue", "upsert_audio_cue"] {
            for evidence in ["manual", "unknown"] {
                let change = cue_change(operation, evidence);
                assert!(!requires_qualified_audio_boundary(&change));
            }
        }
    }
    #[test]
    fn generic_guard_still_rejects_measured_and_transcript_aligned_cues() {
        for operation in ["add_audio_cue", "upsert_audio_cue"] {
            for evidence in ["measured", "transcript_aligned"] {
                let change = cue_change(operation, evidence);
                assert!(requires_qualified_audio_boundary(&change));
            }
        }
    }
}

#[cfg(test)]
mod apply_recovery_candidate_tests {
    use super::*;
    use motionwright_storage::{ApplyReceiptObservation, Store};

    fn applied(completion: Completion) -> Value {
        match completion {
            Completion::Applied(value) => value,
            other => panic!("expected actual local Store completion, got {other:?}"),
        }
    }

    #[test]
    fn recovery_opt_in_preserves_legacy_key_behavior_and_exact_old_result() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("legacy-key.sqlite3")).unwrap();
        let original = store.create_named_project("Legacy request key").unwrap();
        let key = "k".repeat(256);
        assert!(!apply_recovery_requested(&json!({}), &key).unwrap());
        assert!(!apply_recovery_requested(&json!({"include_recovery":false}), &key).unwrap());
        assert!(apply_recovery_requested(&json!({"include_recovery":true}), &key).is_err());
        assert!(apply_recovery_requested(&json!({"include_recovery":null}), "valid").is_err());
        let outcome = store
            .apply(
                original.id,
                &original.stamp(),
                &key,
                &Change::RenameProject {
                    title: "Actual legacy commit".into(),
                },
            )
            .unwrap();
        let expected = applied(apply_completion(Ok(outcome.clone())));
        let actual = applied(apply_completion_with_recovery(Ok(outcome), None));
        assert_eq!(actual, expected);
        assert_eq!(actual["request_id"], key);
        assert!(actual.get("recovery").is_none());
        assert_eq!(actual.as_object().unwrap().len(), 7);
    }

    #[test]
    fn recovery_namespace_uses_full_generation_and_utf8_byte_budget() {
        let id = Uuid::parse_str("11111111-1111-4111-8111-111111111111").unwrap();
        let generation = Uuid::parse_str("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap();
        let change = Change::RenameProject {
            title: "Namespace control".into(),
        };
        for raw in [
            "a".repeat(87),
            format!("{}x", "é".repeat(43)),
            "part:with:colons".into(),
        ] {
            let identity = apply_recovery_identity(id, generation, &raw, &change).unwrap();
            let (observed_id, observed_generation, observed_raw) =
                decode_apply_recovery_identity(&identity).unwrap();
            assert_eq!(
                (observed_id, observed_generation, observed_raw),
                (id, generation, raw.as_str())
            );
            assert_eq!(identity.key.len(), 41 + raw.len());
            assert!(identity.key.len() <= 128);
            assert_eq!(identity.epoch, 1);
        }
        for raw in [
            String::new(),
            "a".repeat(88),
            "é".repeat(44),
            "control\nkey".into(),
        ] {
            assert!(apply_recovery_identity(id, generation, &raw, &change).is_err());
        }
        let identity = apply_recovery_identity(id, generation, "valid", &change).unwrap();
        for key in [
            format!("mw1:{}:valid", generation.to_string().to_uppercase()),
            format!("mw1:{}:valid", Uuid::nil()),
            format!("mw2:{generation}:valid"),
            format!("mw1:{}:valid", generation.simple()),
        ] {
            let mut changed = identity.clone();
            changed.key = key;
            assert!(decode_apply_recovery_identity(&changed).is_err());
        }
        for epoch in [0, 2] {
            let mut changed = identity.clone();
            changed.epoch = epoch;
            assert!(decode_apply_recovery_identity(&changed).is_err());
        }
    }

    #[test]
    fn opted_in_commit_identity_matches_actual_writer_binding_and_seven_fields() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("identity.sqlite3")).unwrap();
        let original = store
            .create_named_project("Original native result")
            .unwrap();
        let change = Change::RenameProject {
            title: "Actual bound digest".into(),
        };
        let identity =
            apply_recovery_identity(original.id, original.generation, "actual-key", &change)
                .unwrap();
        let prepared =
            prepare_apply_recovery(original.id, original.generation, "actual-key", &change)
                .unwrap();
        let outcome = store
            .apply(original.id, &original.stamp(), "actual-key", &change)
            .unwrap();
        let mut emitted = applied(apply_completion_with_recovery(
            Ok(outcome.clone()),
            Some(prepared),
        ));
        let metadata = emitted.as_object_mut().unwrap().remove("recovery").unwrap();
        assert_eq!(metadata, serde_json::to_value(&identity).unwrap());
        assert_eq!(emitted, applied(apply_completion(Ok(outcome.clone()))));
        assert_eq!(emitted.as_object().unwrap().len(), 7);
        let ApplyReceiptObservation::KnownVerifiedV1 { binding } = store
            .observe_apply_receipt_v1(
                original.id,
                &original.stamp(),
                "actual-key",
                &change,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
        else {
            panic!("actual writer must leave verified V1 evidence")
        };
        assert_eq!(identity.request_sha256, binding.request_sha256);
        assert_eq!(binding.generation, original.generation.to_string());
        let ApplyResultObservation::KnownVerifiedV1 {
            outcome: historical,
            ..
        } = store
            .lookup_apply_result_v1(
                original.id,
                original.generation,
                "actual-key",
                &identity.request_sha256,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
        else {
            panic!("actual historical result must be present")
        };
        assert_eq!(*historical, outcome);
        assert_eq!(apply_result_value(&historical), emitted);
    }

    #[test]
    fn opted_in_replay_keeps_invocation_flag_and_historical_original_separate() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("replay.sqlite3")).unwrap();
        let original = store
            .create_named_project("Original replay control")
            .unwrap();
        let change = Change::RenameProject {
            title: "Single actual commit".into(),
        };
        let identity =
            apply_recovery_identity(original.id, original.generation, "replay", &change).unwrap();
        let first = store
            .apply(original.id, &original.stamp(), "replay", &change)
            .unwrap();
        let prepared =
            prepare_apply_recovery(original.id, original.generation, "replay", &change).unwrap();
        let second = store
            .apply(original.id, &original.stamp(), "replay", &change)
            .unwrap();
        assert!(!first.replayed);
        assert!(second.replayed);
        let returned = applied(apply_completion_with_recovery(Ok(second), Some(prepared)));
        assert_eq!(returned["replayed"], true);
        assert_eq!(
            returned["recovery"],
            serde_json::to_value(&identity).unwrap()
        );
        let ApplyResultObservation::KnownVerifiedV1 { outcome, .. } = store
            .lookup_apply_result_v1(
                original.id,
                original.generation,
                "replay",
                &identity.request_sha256,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
        else {
            panic!("actual original receipt must remain verified")
        };
        assert_eq!(*outcome, first);
        assert!(!outcome.replayed);
        assert_eq!(
            store.load_project(original.id).unwrap().revision,
            first.project.revision
        );
    }

    #[test]
    fn prepared_recovery_preserves_actual_store_success_and_refusal_terminals() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("terminal-custody.sqlite3")).unwrap();
        let original = store
            .create_named_project("Original committed control")
            .unwrap();
        let change = Change::RenameProject {
            title: "Commit remains real".into(),
        };
        let prepared =
            prepare_apply_recovery(original.id, original.generation, "original-key", &change)
                .unwrap();
        let committed = store
            .apply(original.id, &original.stamp(), "original-key", &change)
            .unwrap();
        let mut returned = applied(apply_completion_with_recovery(
            Ok(committed.clone()),
            Some(prepared),
        ));
        assert!(
            returned
                .as_object_mut()
                .unwrap()
                .remove("recovery")
                .is_some()
        );
        assert_eq!(returned, apply_result_value(&committed));

        // A genuine canonical request-reuse refusal must retain its original
        // known rejection. Prepared metadata never replaces an error terminal.
        let changed = Change::RenameProject {
            title: "Rejected different intent".into(),
        };
        let prepared =
            prepare_apply_recovery(original.id, original.generation, "original-key", &changed)
                .unwrap();
        let actual = store.apply(
            original.id,
            &committed.project.stamp(),
            "original-key",
            &changed,
        );
        assert!(matches!(&actual, Err(StorageError::RequestReuse)));
        let error = apply_completion_with_recovery(actual, Some(prepared))
            .into_result()
            .unwrap_err();
        assert!(error.outcome_known);
        assert_eq!(error.code, ErrorCode::Conflict);
        assert_eq!(store.load_project(original.id).unwrap(), committed.project);

        // Break the actual owned receipt after its real commit. The shared
        // checker, not metadata formatting, supplies this uncertain terminal.
        let peer = crate::sqlite_v6_corruptible::open_v6_writer(store.path());
        peer.execute(
            "UPDATE receipts SET result_json='{}' WHERE request_id='original-key'",
            [],
        )
        .unwrap();
        let prepared =
            prepare_apply_recovery(original.id, original.generation, "original-key", &change)
                .unwrap();
        let actual = store.apply(
            original.id,
            &committed.project.stamp(),
            "original-key",
            &change,
        );
        assert!(matches!(&actual, Err(StorageError::InvalidApplyReceipt(_))));
        let error = apply_completion_with_recovery(actual, Some(prepared))
            .into_result()
            .unwrap_err();
        assert!(!error.outcome_known);
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert_eq!(store.load_project(original.id).unwrap(), committed.project);
    }
}
