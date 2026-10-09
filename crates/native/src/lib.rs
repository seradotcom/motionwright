use async_trait::async_trait;
use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_service::{ProductionReceiptWatermark, StudioService};
use motionwright_storage::StorageError;
use semwright_native_sdk::cooperation::{
    Application, CallContext, CancellationSemantics, CommitSemantics, Completion, ObservationPage,
    ObservationProvider, OperationContract, OperationHandler, PageCursor, Query, ResourceVersion,
    RetrySemantics, RevisionToken, TargetRequirement, UndoSemantics,
};
use semwright_native_sdk::{
    CommandDescriptor, Error, ErrorCode, Idempotency, Result as NativeResult, Risk, Value, json,
};
use std::sync::Arc;
use uuid::Uuid;

pub mod canonical;
mod component_text;
mod expressive;
pub mod film;
pub mod multi_renderer;
pub mod production;

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
}

impl MotionwrightObserver {
    pub fn new(service: StudioService) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ObservationProvider for MotionwrightObserver {
    async fn observe(&self, query: &Query, context: &CallContext) -> NativeResult<ObservationPage> {
        context.check_cancelled()?;
        query.validate()?;
        let project_id = project_id_from_resource(&query.resource)?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        let version = resource_version(&project)?;

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
                        if head.latest_id.is_none_or(|latest| before > latest) {
                            return Err(Error::invalid(
                                "Production receipt cursor exceeds the snapshot watermark",
                            ));
                        }
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

#[derive(Clone)]
struct ApplyHandler {
    service: StudioService,
    operation: OperationKind,
}

#[derive(Clone, Copy)]
enum OperationKind {
    UpsertProductHero,
    DetachProductHero,
    SetProductionPlan,
    UpsertNativeCapsule,
    ApplyCreativePatch,
    RenameProject,
    SetBrief,
    SetNarrativePremise,
    AddScene,
    MoveScene,
    UpdateSceneObjective,
    SetSceneRenderer,
    SetSceneStatus,
    SetSceneDuration,
    AddCanvasNode,
    RemoveCanvasNode,
    TransformCanvasNode,
    SetCanvasKeyframe,
    SetCanvasPositionKeyframe,
    SetCanvasLinearPositionMotion,
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

        match self
            .service
            .apply(project_id, &expected, context.request_id(), &change)
        {
            Ok(outcome) => Completion::Applied(json!({
                "resource": outcome.project.resource_key(),
                "generation": outcome.project.generation.to_string(),
                "revision": outcome.project.revision.to_string(),
                "previous_revision": outcome.previous_revision.to_string(),
                "request_id": outcome.request_id,
                "replayed": outcome.replayed,
                "project": outcome.project
            })),
            Err(error) => Completion::NotApplied(storage_error(error)),
        }
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
        OperationKind::SetProductionPlan => Ok(Change::SetProductionPlan {
            plan: serde_json::from_value(
                args.get("plan")
                    .cloned()
                    .ok_or_else(|| Error::invalid("plan is required"))?,
            )
            .map_err(|_| Error::invalid("invalid ProductionPlan"))?,
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
        OperationKind::SetCanvasLinearPositionMotion => {
            let end_frame = args.get("end_frame")
                .and_then(Value::as_u64)
                .and_then(|value| u32::try_from(value).ok())
                .filter(|frame| (1..=36_000).contains(frame))
                .ok_or_else(|| Error::invalid(
                    "Native linear position move endpoint must be a bounded positive frame index",
                ))?;
            Ok(Change::SetCanvasLinearPositionMotion {
                scene_id: uuid(args, "scene_id")?,
                node_id: uuid(args, "node_id")?,
                deliverable_id: uuid(args, "deliverable_id")?,
                start_x: number(args, "start_x")?,
                start_y: number(args, "start_y")?,
                end_frame,
            })
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

fn descriptor(name: &str, description: &str, input_schema: Value) -> OperationContract {
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
                    "project": {"type": "object"}
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
    let observer = Arc::new(MotionwrightObserver::new(service.clone()));
    let mut app = Application::new(APP_ID, APP_VERSION)?.with_observer(observer);

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
        assert!(names.contains(&"driver.motionwright.creative.patch.apply"));
        assert_eq!(capabilities.len(), 51);
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
