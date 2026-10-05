use async_trait::async_trait;
use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_storage::StorageError;
use semwright_native_sdk::cooperation::{
    Application, CallContext, CancellationSemantics, CommitSemantics, Completion, ObservationPage,
    ObservationProvider, OperationContract, OperationHandler, Query, ResourceVersion,
    RetrySemantics, RevisionToken, TargetRequirement, UndoSemantics,
};
use semwright_native_sdk::{
    CommandDescriptor, Error, ErrorCode, Idempotency, Result as NativeResult, Risk, Value, json,
};
use std::sync::Arc;
use uuid::Uuid;

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
        StorageError::Domain(error) => Error::new(ErrorCode::InvalidArgument, error.to_string()),
        StorageError::Serde(_)
        | StorageError::InvalidBackup(_)
        | StorageError::InvalidBlobDigest
        | StorageError::BlobTooLarge { .. } => {
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
        StorageError::Sql(_) | StorageError::Io(_) => {
            Error::new(ErrorCode::BackendFailed, "Motionwright storage failed")
        }
    }
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

        let items = match query.scope.as_str() {
            "summary" => vec![json!({
                "id": project.id.to_string(),
                "title": project.title,
                "state": project.state,
                "revision": project.revision.to_string(),
                "active_branch": project.active_branch.to_string(),
                "scene_count": project.scenes.len(),
                "asset_count": project.assets.len(),
                "lock_count": project.locks.len(),
                "deliverable_count": project.deliverables.len()
            })],
            "timeline" => project
                .scenes
                .iter()
                .map(|scene| {
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
                })
                .take(usize::from(query.limit))
                .collect(),
            "brief" => vec![serde_json::to_value(&project.brief).unwrap_or(Value::Null)],
            "narrative" => vec![serde_json::to_value(&project.narrative).unwrap_or(Value::Null)],
            "audio" => vec![serde_json::to_value(&project.audio).unwrap_or(Value::Null)],
            "visual-language" => {
                vec![serde_json::to_value(&project.visual_language).unwrap_or(Value::Null)]
            }
            "canvas" => project
                .scenes
                .iter()
                .take(usize::from(query.limit))
                .map(|scene| {
                    json!({
                        "scene_id": scene.id.to_string(),
                        "scene_name": scene.name,
                        "camera": scene.camera,
                        "nodes": scene.nodes
                    })
                })
                .collect(),
            "alternatives" => project
                .proposal_sets
                .iter()
                .take(usize::from(query.limit))
                .map(|set| serde_json::to_value(set).unwrap_or(Value::Null))
                .collect(),
            "history" => self
                .service
                .history(project_id, 0, usize::from(query.limit))
                .map_err(storage_error)?
                .into_iter()
                .map(|event| serde_json::to_value(event).unwrap_or(Value::Null))
                .collect(),
            "locks" => project
                .locks
                .iter()
                .take(usize::from(query.limit))
                .map(|lock| serde_json::to_value(lock).unwrap_or(Value::Null))
                .collect(),
            _ => {
                return Err(Error::new(
                    ErrorCode::NotFound,
                    "Unknown Motionwright observation scope",
                ));
            }
        };

        let page = ObservationPage {
            version,
            scope: query.scope.clone(),
            items,
            next: None,
            complete: true,
        };
        page.validate_for(query)?;
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
    UpdateCanvasText,
    UpdateCanvasStyle,
    ReparentCanvasNode,
    SetCanvasRelations,
    SetNodePropertyLock,
    SetCamera,
    AddMarker,
    SetVisualLanguage,
    AddProposalSet,
    SelectProposal,
    SetLock,
    RemoveLock,
}

#[async_trait]
impl OperationHandler for ApplyHandler {
    async fn invoke(&self, args: Value, context: &CallContext) -> Completion {
        if let Err(error) = context.check_cancelled() {
            return Completion::NotApplied(error);
        }
        let Some(reference) = args.get("ref").and_then(Value::as_str) else {
            return Completion::NotApplied(Error::invalid("Project ref is required"));
        };
        let project_id = match project_id_from_resource(reference) {
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
                                "property_locks":{"type":"array","maxItems":8,"items":{"type":"string","enum":["position","size","rotation","opacity","text","style","parent","order"]}}
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
        assert!(names.contains(&"driver.motionwright.canvas.node.style.set"));
        assert!(names.contains(&"driver.motionwright.canvas.node.reparent"));
        assert!(names.contains(&"driver.motionwright.canvas.node.relations.set"));
        assert!(names.contains(&"driver.motionwright.scene.duration.set"));
        assert!(names.contains(&"driver.motionwright.alternatives.select"));
        assert_eq!(capabilities.len(), 25);
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
    }

    #[tokio::test]
    async fn creative_observation_scopes_read_application_owned_state() {
        let (service, project) = fixture();
        let observer = MotionwrightObserver::new(service);
        for scope in [
            "brief",
            "narrative",
            "audio",
            "visual-language",
            "canvas",
            "alternatives",
            "history",
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
}
