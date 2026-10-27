//! Read-only SceneBoundary authoring context over the existing Store.
//!
//! Capturing source does not require a destination declaration to be executable.
//! A predecessor digest identifies the exact persisted Explicit declaration; it
//! is not a readiness result, an effect grant or permission to render that source.
use crate::{MAX_PROJECTION_RESPONSE_BYTES, StorageResult, StudioService};
use motionwright_domain::{
    DomainError, Project, RevisionStamp, SceneBoundaryFilmOptions, SceneBoundarySource,
};
use serde::{
    Deserialize, Deserializer, Serialize,
    de::{MapAccess, Visitor, value::MapAccessDeserializer},
};
use std::{fmt, marker::PhantomData};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SceneBoundaryContextRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub branch_id: Uuid,
    pub scene_id: Uuid,
    pub deliverable_profile_id: Uuid,
    pub authored_film_options: SceneBoundaryFilmOptions,
    /// The wire decoder requires this key even when its value is null.
    pub predecessor_boundary_id: Option<Uuid>,
}

impl<'de> Deserialize<'de> for SceneBoundaryContextRequest {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct RequestObject {
            project_id: Uuid,
            #[serde(deserialize_with = "complete_revision_stamp")]
            expected: RevisionStamp,
            branch_id: Uuid,
            scene_id: Uuid,
            deliverable_profile_id: Uuid,
            authored_film_options: SceneBoundaryFilmOptions,
            // No default: nullable is not the same as an omitted key.
            #[serde(deserialize_with = "required_nullable_uuid")]
            predecessor_boundary_id: Option<Uuid>,
        }
        let request: RequestObject = deserialize_object(deserializer)?;
        Ok(Self {
            project_id: request.project_id,
            expected: request.expected,
            branch_id: request.branch_id,
            scene_id: request.scene_id,
            deliverable_profile_id: request.deliverable_profile_id,
            authored_film_options: request.authored_film_options,
            predecessor_boundary_id: request.predecessor_boundary_id,
        })
    }
}

// Derived structs also admit positional sequences in Serde. These two new
// request objects require keys; decode their maps directly into typed fields.
fn deserialize_object<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct Object<T>(PhantomData<T>);
    impl<'de, T: Deserialize<'de>> Visitor<'de> for Object<T> {
        type Value = T;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("an object with named fields")
        }

        fn visit_map<A>(self, map: A) -> Result<Self::Value, A::Error>
        where
            A: MapAccess<'de>,
        {
            T::deserialize(MapAccessDeserializer::new(map))
        }
    }
    deserializer.deserialize_map(Object(PhantomData))
}

fn complete_revision_stamp<'de, D>(deserializer: D) -> Result<RevisionStamp, D::Error>
where
    D: Deserializer<'de>,
{
    // Keep this reader's wire boundary closed without changing the existing
    // global RevisionStamp representation or historical mutation decoders.
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct CompleteRevisionStamp {
        resource: String,
        generation: Uuid,
        revision: u64,
    }

    let stamp: CompleteRevisionStamp = deserialize_object(deserializer)?;
    Ok(RevisionStamp {
        resource: stamp.resource,
        generation: stamp.generation,
        revision: stamp.revision,
    })
}

fn required_nullable_uuid<'de, D>(deserializer: D) -> Result<Option<Uuid>, D::Error>
where
    D: Deserializer<'de>,
{
    Option::<Uuid>::deserialize(deserializer)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SceneBoundaryPredecessorContext {
    pub scene_id: Uuid,
    pub boundary_id: Uuid,
    pub exit_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SceneBoundaryAuthoringContext {
    pub schema: &'static str,
    pub scene_id: Uuid,
    pub source: SceneBoundarySource,
    pub predecessor: Option<SceneBoundaryPredecessorContext>,
}

impl StudioService {
    /// Inspect authoring source for an explicitly chosen scene/profile/options.
    /// Writes still use the ordinary Change, expected stamp and Store receipt
    /// path. Native freshness, assets, endpoints and execution remain separate.
    pub fn scene_boundary_authoring_context(
        &self,
        request: &SceneBoundaryContextRequest,
    ) -> StorageResult<SceneBoundaryAuthoringContext> {
        let store = self.store.lock();
        let project = store.load_project(request.project_id)?;
        if project.stamp() != request.expected {
            return Err(DomainError::Invalid(
                "Scene boundary authoring context requires the complete current revision stamp"
                    .into(),
            )
            .into());
        }
        if project.active_branch != request.branch_id
            || !project
                .branches
                .iter()
                .any(|branch| branch.id == request.branch_id)
        {
            return Err(DomainError::Invalid(
                "Scene boundary authoring context requires the current active branch".into(),
            )
            .into());
        }
        let source = project.capture_scene_boundary_source(
            request.scene_id,
            request.deliverable_profile_id,
            &request.authored_film_options,
        )?;
        let predecessor = request
            .predecessor_boundary_id
            .map(|id| {
                let boundary = project
                    .production_design
                    .scene_boundaries
                    .iter()
                    .find(|boundary| boundary.id == id)
                    .ok_or_else(|| DomainError::NotFound(format!("scene-boundary:{id}")))?;
                Ok::<_, DomainError>(SceneBoundaryPredecessorContext {
                    scene_id: boundary.scene_id,
                    boundary_id: boundary.id,
                    // This shared Domain helper rejects Draft and hashes the
                    // full Explicit declaration. It makes no freshness claim.
                    exit_sha256: boundary.bound_exit_sha256()?,
                })
            })
            .transpose()?;
        let context = SceneBoundaryAuthoringContext {
            schema: "motionwright.scene-boundary-authoring-context/1",
            scene_id: request.scene_id,
            source,
            predecessor,
        };
        let encoded = serde_json::to_vec(&context)?;
        if encoded.len() > MAX_PROJECTION_RESPONSE_BYTES {
            return Err(DomainError::Invalid(
                "Scene boundary authoring context exceeds the existing projection response budget"
                    .into(),
            )
            .into());
        }

        #[cfg(test)]
        BEFORE_CONTEXT_RECHECK.with(|slot| {
            let hook = slot.borrow_mut().take();
            if let Some(hook) = hook {
                hook();
            }
        });

        // The Store mutex covers this Service instance, not another connection
        // or process. Compare the entire Project, not only its stamp or the
        // dependency digest, before returning the inspected source.
        require_unchanged_project(&project, &store.load_project(project.id)?)?;
        Ok(context)
    }
}

fn require_unchanged_project(original: &Project, current: &Project) -> StorageResult<()> {
    if current != original {
        return Err(DomainError::Invalid(
            "Scene boundary source changed during authoring inspection".into(),
        )
        .into());
    }
    Ok(())
}

#[cfg(test)]
std::thread_local! {
    static BEFORE_CONTEXT_RECHECK: std::cell::RefCell<Option<Box<dyn FnOnce()>>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{
        Change, Project, SceneBoundary, SceneBoundaryArchetype, SceneBoundaryDefinition,
        SceneBoundaryEndpoint, SceneBoundaryInput, SceneBoundaryNarrativeRole,
    };
    use motionwright_storage::StorageError;
    use rusqlite::{Connection, OpenFlags, types::Value as SqlValue};
    use serde_json::json;
    use std::{
        cell::{Cell, RefCell},
        path::Path,
        rc::Rc,
    };

    fn fixture() -> (tempfile::TempDir, StudioService, Project) {
        let directory = tempfile::tempdir().unwrap();
        let service = StudioService::open(directory.path().join("project.sqlite3")).unwrap();
        let mut project = service.create_project("Scene boundary authoring").unwrap();
        for index in 0..3 {
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("scene-{index}"),
                    &Change::AddScene {
                        name: format!("Scene {index}"),
                        objective: format!("Actual authoring source {index}"),
                        duration_seconds: 4,
                    },
                )
                .unwrap()
                .project;
        }
        (directory, service, project)
    }

    fn options(project: &Project) -> SceneBoundaryFilmOptions {
        let options = SceneBoundaryFilmOptions {
            frame_rate: serde_json::from_value(json!({
                "num": u32::try_from(project.deliverables[0].frame_rate.num).unwrap(),
                "den": u32::try_from(project.deliverables[0].frame_rate.den).unwrap(),
            }))
            .unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            role: SceneBoundaryNarrativeRole::Hook,
            archetype: SceneBoundaryArchetype::Statement,
        };
        options.validate().unwrap();
        options
    }

    fn request(project: &Project) -> SceneBoundaryContextRequest {
        SceneBoundaryContextRequest {
            project_id: project.id,
            expected: project.stamp(),
            branch_id: project.active_branch,
            scene_id: project.scenes[2].id,
            deliverable_profile_id: project.deliverables[0].id,
            authored_film_options: options(project),
            predecessor_boundary_id: None,
        }
    }

    fn explicit(project: &Project, scene_index: usize) -> SceneBoundary {
        let scene = &project.scenes[scene_index];
        assert!(scene.nodes.is_empty());
        let endpoint = SceneBoundaryEndpoint {
            nodes: vec![],
            camera: Some(scene.camera.clone()),
            assets: vec![],
        };
        SceneBoundary {
            version: 1,
            id: Uuid::now_v7(),
            scene_id: scene.id,
            input: SceneBoundaryInput::SelfContained,
            definition: SceneBoundaryDefinition::Explicit {
                source: project
                    .capture_scene_boundary_source(
                        scene.id,
                        project.deliverables[0].id,
                        &options(project),
                    )
                    .unwrap(),
                entry: endpoint.clone(),
                exit: endpoint,
            },
        }
    }

    fn save_boundary(
        service: &StudioService,
        project: &Project,
        boundary: SceneBoundary,
    ) -> Project {
        service
            .apply(
                project.id,
                &project.stamp(),
                &format!("boundary-{}", boundary.id),
                &Change::UpsertSceneBoundary {
                    boundary: Box::new(boundary),
                },
            )
            .unwrap()
            .project
    }

    #[derive(Debug, PartialEq)]
    struct StoreRows {
        projects: Vec<Vec<SqlValue>>,
        events: Vec<Vec<SqlValue>>,
        receipts: Vec<Vec<SqlValue>>,
    }

    fn rows(connection: &Connection, sql: &str) -> Vec<Vec<SqlValue>> {
        let mut statement = connection.prepare(sql).unwrap();
        let columns = statement.column_count();
        statement
            .query_map([], |row| {
                (0..columns)
                    .map(|index| row.get::<_, SqlValue>(index))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap()
    }

    fn store_rows(database: &Path) -> StoreRows {
        let connection =
            Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        StoreRows {
            projects: rows(&connection, "SELECT * FROM projects ORDER BY id"),
            events: rows(&connection, "SELECT * FROM events ORDER BY event_id"),
            receipts: rows(
                &connection,
                "SELECT * FROM receipts ORDER BY project_id,request_id",
            ),
        }
    }

    #[test]
    fn reader_wire_requires_all_keys_a_complete_closed_stamp_and_explicit_nullable_selector() {
        let (_directory, _service, project) = fixture();
        let request = request(&project);
        let value = serde_json::to_value(&request).unwrap();
        assert!(value["predecessor_boundary_id"].is_null());
        assert_eq!(
            serde_json::from_value::<SceneBoundaryContextRequest>(value.clone()).unwrap(),
            request,
        );
        let positional = json!([
            request.project_id,
            request.expected,
            request.branch_id,
            request.scene_id,
            request.deliverable_profile_id,
            request.authored_film_options,
            null,
        ]);
        assert!(serde_json::from_value::<SceneBoundaryContextRequest>(positional).is_err());
        let mut positional_stamp = value.clone();
        positional_stamp["expected"] = json!([
            request.expected.resource,
            request.expected.generation,
            request.expected.revision,
        ]);
        assert!(serde_json::from_value::<SceneBoundaryContextRequest>(positional_stamp).is_err());
        for invalid in [json!(null), json!(true), json!(7), json!("object")] {
            assert!(
                serde_json::from_value::<SceneBoundaryContextRequest>(invalid.clone()).is_err()
            );
            let mut wrong_stamp = value.clone();
            wrong_stamp["expected"] = invalid;
            assert!(serde_json::from_value::<SceneBoundaryContextRequest>(wrong_stamp).is_err());
        }
        for key in [
            "project_id",
            "expected",
            "branch_id",
            "scene_id",
            "deliverable_profile_id",
            "authored_film_options",
            "predecessor_boundary_id",
        ] {
            let mut missing = value.clone();
            missing.as_object_mut().unwrap().remove(key);
            assert!(
                serde_json::from_value::<SceneBoundaryContextRequest>(missing).is_err(),
                "required key {key} was silently defaulted",
            );
        }
        for key in ["resource", "generation", "revision"] {
            let mut missing = value.clone();
            missing["expected"].as_object_mut().unwrap().remove(key);
            assert!(serde_json::from_value::<SceneBoundaryContextRequest>(missing).is_err());
        }
        for field in [None, Some("expected"), Some("authored_film_options")] {
            let mut unknown = value.clone();
            let object = match field {
                Some(field) => &mut unknown[field],
                None => &mut unknown,
            };
            object["unexpected_ready"] = json!(true);
            assert!(serde_json::from_value::<SceneBoundaryContextRequest>(unknown).is_err());
        }
        for invalid in [json!(true), json!(7), json!([]), json!("not-a-uuid")] {
            let mut malformed = value.clone();
            malformed["predecessor_boundary_id"] = invalid;
            assert!(serde_json::from_value::<SceneBoundaryContextRequest>(malformed).is_err());
        }
        let predecessor = Uuid::now_v7();
        let mut selected = value;
        selected["predecessor_boundary_id"] = json!(predecessor);
        assert_eq!(
            serde_json::from_value::<SceneBoundaryContextRequest>(selected)
                .unwrap()
                .predecessor_boundary_id,
            Some(predecessor),
        );
    }

    #[test]
    fn reader_captures_absent_and_unsourced_draft_destinations_without_store_writes() {
        let (directory, service, project) = fixture();
        let database = directory.path().join("project.sqlite3");
        let before = store_rows(&database);
        let original_request = request(&project);
        let absent = service
            .scene_boundary_authoring_context(&original_request)
            .unwrap();
        assert_eq!(
            absent.source,
            project
                .capture_scene_boundary_source(
                    original_request.scene_id,
                    original_request.deliverable_profile_id,
                    &original_request.authored_film_options,
                )
                .unwrap(),
        );
        assert!(absent.predecessor.is_none());
        assert_eq!(store_rows(&database), before);

        let draft = SceneBoundary {
            version: 1,
            id: Uuid::now_v7(),
            scene_id: original_request.scene_id,
            input: SceneBoundaryInput::SelfContained,
            definition: SceneBoundaryDefinition::DraftIncomplete {
                source: None,
                entry: None,
                exit: None,
            },
        };
        let project = save_boundary(&service, &project, draft);
        let request = request(&project);
        let before = store_rows(&database);
        let context = service.scene_boundary_authoring_context(&request).unwrap();
        assert_eq!(context.source.authored_revision, project.revision);
        assert_eq!(context.source.film_options, request.authored_film_options);
        assert_eq!(
            context.source.dependencies_sha256,
            absent.source.dependencies_sha256
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(store_rows(&database), before);
        let encoded = serde_json::to_value(&context).unwrap();
        assert_eq!(
            encoded
                .as_object()
                .unwrap()
                .keys()
                .map(String::as_str)
                .collect::<std::collections::BTreeSet<_>>(),
            ["schema", "scene_id", "source", "predecessor"]
                .into_iter()
                .collect::<std::collections::BTreeSet<_>>(),
        );
        assert_eq!(
            encoded["schema"],
            "motionwright.scene-boundary-authoring-context/1"
        );
        assert!(serde_json::to_vec(&context).unwrap().len() <= MAX_PROJECTION_RESPONSE_BYTES);

        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        assert_eq!(
            reopened.scene_boundary_authoring_context(&request).unwrap(),
            context
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
        assert_eq!(store_rows(&database), before);
    }

    #[test]
    fn reader_rejects_foreign_stamp_components_and_inactive_or_missing_branch() {
        let (directory, service, project) = fixture();
        let project = service
            .apply(
                project.id,
                &project.stamp(),
                "create-inactive-branch",
                &Change::CreateBranch {
                    name: "Other authoring branch".into(),
                },
            )
            .unwrap()
            .project;
        let request = request(&project);
        let before = store_rows(&directory.path().join("project.sqlite3"));
        assert!(service.scene_boundary_authoring_context(&request).is_ok());
        let mut wrong_resource = request.clone();
        wrong_resource.expected.resource = format!("project:{}", Uuid::now_v7());
        let mut wrong_generation = request.clone();
        wrong_generation.expected.generation = Uuid::now_v7();
        let mut wrong_revision = request.clone();
        wrong_revision.expected.revision += 1;
        let mut inactive = request.clone();
        inactive.branch_id = project
            .branches
            .iter()
            .find(|branch| branch.id != project.active_branch)
            .unwrap()
            .id;
        let mut missing = request.clone();
        missing.branch_id = Uuid::now_v7();
        for invalid in [
            wrong_resource,
            wrong_generation,
            wrong_revision,
            inactive,
            missing,
        ] {
            assert!(matches!(
                service.scene_boundary_authoring_context(&invalid),
                Err(StorageError::Domain(DomainError::Invalid(_))),
            ));
        }
        assert!(service.scene_boundary_authoring_context(&request).is_ok());
        assert_eq!(
            store_rows(&directory.path().join("project.sqlite3")),
            before
        );
    }

    #[test]
    fn reader_requires_actual_scene_profile_and_explicit_authored_options() {
        let (directory, service, project) = fixture();
        let request = request(&project);
        let before = store_rows(&directory.path().join("project.sqlite3"));
        assert!(service.scene_boundary_authoring_context(&request).is_ok());
        let mut missing_scene = request.clone();
        missing_scene.scene_id = Uuid::now_v7();
        let mut missing_profile = request.clone();
        missing_profile.deliverable_profile_id = Uuid::now_v7();
        let mut invalid_options = request.clone();
        invalid_options.authored_film_options.font_family.clear();
        for invalid in [missing_scene, missing_profile, invalid_options] {
            assert!(service.scene_boundary_authoring_context(&invalid).is_err());
        }
        assert!(service.scene_boundary_authoring_context(&request).is_ok());
        assert_eq!(
            store_rows(&directory.path().join("project.sqlite3")),
            before
        );
    }

    #[test]
    fn reader_enforces_the_existing_projection_byte_cap_without_truncating_source() {
        let (directory, service, project) = fixture();
        let mut request = request(&project);
        let baseline = service.scene_boundary_authoring_context(&request).unwrap();
        let baseline_bytes = serde_json::to_vec(&baseline).unwrap().len();
        // These authored family names are nonempty unescaped ASCII. The
        // canonical source digest stays a fixed-width SHA256 string, so this
        // derives the exact response boundary from the actual typed result.
        let overhead = baseline_bytes - request.authored_film_options.font_family.len();
        request.authored_film_options.font_family =
            "A".repeat(MAX_PROJECTION_RESPONSE_BYTES - overhead);
        let before = store_rows(&directory.path().join("project.sqlite3"));
        let at_limit = service.scene_boundary_authoring_context(&request).unwrap();
        assert_eq!(
            serde_json::to_vec(&at_limit).unwrap().len(),
            MAX_PROJECTION_RESPONSE_BYTES
        );
        assert_eq!(at_limit.source.film_options, request.authored_film_options);
        request.authored_film_options.font_family.push('A');
        let error = service
            .scene_boundary_authoring_context(&request)
            .unwrap_err();
        assert!(
            matches!(error, StorageError::Domain(DomainError::Invalid(ref message)) if message == "Scene boundary authoring context exceeds the existing projection response budget")
        );
        assert_eq!(
            store_rows(&directory.path().join("project.sqlite3")),
            before
        );
    }

    #[test]
    fn reader_selects_exact_explicit_predecessor_uuid_and_never_infers_readiness() {
        let (directory, service, project) = fixture();
        let first = explicit(&project, 0);
        let first_id = first.id;
        let project = save_boundary(&service, &project, first);
        let second = explicit(&project, 1);
        let second_id = second.id;
        let expected_exit = second.bound_exit_sha256().unwrap();
        let project = save_boundary(&service, &project, second);
        let first = project
            .production_design
            .scene_boundaries
            .iter()
            .find(|item| item.id == first_id)
            .unwrap();
        assert_ne!(first.bound_exit_sha256().unwrap(), expected_exit);
        let mut selected = request(&project);
        selected.predecessor_boundary_id = Some(second_id);
        let before = store_rows(&directory.path().join("project.sqlite3"));
        let context = service.scene_boundary_authoring_context(&selected).unwrap();
        assert_eq!(
            context.predecessor,
            Some(SceneBoundaryPredecessorContext {
                scene_id: project.scenes[1].id,
                boundary_id: second_id,
                exit_sha256: expected_exit.clone(),
            })
        );
        assert_eq!(
            store_rows(&directory.path().join("project.sqlite3")),
            before
        );

        // Ordinary creative editing makes the persisted Explicit source stale.
        // Its exact declaration remains inspectable; this reader never returns
        // a readiness boolean or invokes the execution resolver as admission.
        let changed = service
            .apply(
                project.id,
                &project.stamp(),
                "change-predecessor-source",
                &Change::UpdateSceneObjective {
                    scene_id: project.scenes[0].id,
                    objective: "Changed source; retained declarations remain editable".into(),
                },
            )
            .unwrap()
            .project;
        let saved = changed
            .production_design
            .scene_boundaries
            .iter()
            .find(|item| item.id == second_id)
            .unwrap();
        assert_ne!(
            changed
                .capture_scene_boundary_source(
                    changed.scenes[1].id,
                    changed.deliverables[0].id,
                    &options(&changed)
                )
                .unwrap()
                .dependencies_sha256,
            saved.source().unwrap().dependencies_sha256,
        );
        assert!(
            changed
                .resolve_scene_boundary_for_execution(
                    changed.scenes[1].id,
                    changed.deliverables[0].id,
                    &options(&changed)
                )
                .is_err()
        );
        let mut selected = request(&changed);
        selected.predecessor_boundary_id = Some(second_id);
        let before = store_rows(&directory.path().join("project.sqlite3"));
        let inspected = service.scene_boundary_authoring_context(&selected).unwrap();
        assert_eq!(inspected.predecessor.unwrap().exit_sha256, expected_exit);
        assert_eq!(
            store_rows(&directory.path().join("project.sqlite3")),
            before
        );
    }

    #[test]
    fn reader_does_not_replace_missing_or_draft_predecessor_with_another_declaration() {
        let (directory, service, project) = fixture();
        let ready_shape = explicit(&project, 0);
        let project = save_boundary(&service, &project, ready_shape);
        let draft = SceneBoundary {
            version: 1,
            id: Uuid::now_v7(),
            scene_id: project.scenes[1].id,
            input: SceneBoundaryInput::SelfContained,
            definition: SceneBoundaryDefinition::DraftIncomplete {
                source: None,
                entry: None,
                exit: None,
            },
        };
        let draft_id = draft.id;
        let project = save_boundary(&service, &project, draft);
        let mut request = request(&project);
        assert!(service.scene_boundary_authoring_context(&request).is_ok());
        let before = store_rows(&directory.path().join("project.sqlite3"));
        request.predecessor_boundary_id = Some(Uuid::now_v7());
        assert!(matches!(
            service.scene_boundary_authoring_context(&request),
            Err(StorageError::Domain(DomainError::NotFound(_)))
        ));
        request.predecessor_boundary_id = Some(draft_id);
        assert!(matches!(
            service.scene_boundary_authoring_context(&request),
            Err(StorageError::Domain(DomainError::Invalid(_)))
        ));
        request.predecessor_boundary_id = None;
        assert!(service.scene_boundary_authoring_context(&request).is_ok());
        assert_eq!(
            store_rows(&directory.path().join("project.sqlite3")),
            before
        );
    }

    #[test]
    fn reader_compares_complete_content_and_rereads_after_an_independent_store_commit() {
        struct ClearHook;
        impl Drop for ClearHook {
            fn drop(&mut self) {
                BEFORE_CONTEXT_RECHECK.with(|slot| {
                    let _ = slot.borrow_mut().take();
                });
            }
        }
        let (directory, service, project) = fixture();
        let database = directory.path().join("project.sqlite3");
        let request = request(&project);
        let original = service.scene_boundary_authoring_context(&request).unwrap();
        let mut replacement = project.clone();
        replacement.title = "Independent same-stamp replacement".into();
        replacement.validate().unwrap();
        assert_eq!(replacement.stamp(), project.stamp());
        // Title is intentionally outside the canonical boundary dependency
        // digest: checking only that digest would miss this replacement.
        assert_eq!(
            replacement
                .capture_scene_boundary_source(
                    request.scene_id,
                    request.deliverable_profile_id,
                    &request.authored_film_options
                )
                .unwrap(),
            original.source
        );
        // This is a pure comparison control, not a claimed same-stamp write
        // through the Store. Its schema6 writer fence rejects raw connections.
        assert!(require_unchanged_project(&project, &project).is_ok());
        let error = require_unchanged_project(&project, &replacement).unwrap_err();
        assert!(
            matches!(error, StorageError::Domain(DomainError::Invalid(ref message)) if message == "Scene boundary source changed during authoring inspection")
        );

        // The actual reread control uses a second ordinary StudioService and
        // its existing Store apply/receipt path, which legitimately advances
        // the revision. No private writer marker or authority is fabricated.
        let independent = StudioService::open(&database).unwrap();
        let replacement_title = replacement.title;
        let id = project.id;
        let path = database.clone();
        let calls = Rc::new(Cell::new(0_usize));
        let hook_calls = Rc::clone(&calls);
        let committed = Rc::new(RefCell::new(None));
        let hook_committed = Rc::clone(&committed);
        let before = store_rows(&database);
        let _clear = ClearHook;
        BEFORE_CONTEXT_RECHECK.with(|slot| {
            assert!(slot.borrow().is_none());
            *slot.borrow_mut() = Some(Box::new(move || {
                let current = independent.project(id).unwrap();
                let outcome = independent
                    .apply(
                        id,
                        &current.stamp(),
                        "independent-context-reread-change",
                        &Change::RenameProject {
                            title: replacement_title,
                        },
                    )
                    .unwrap();
                assert!(!outcome.replayed);
                *hook_committed.borrow_mut() = Some((outcome.project, store_rows(&path)));
                hook_calls.set(hook_calls.get() + 1);
            }));
        });
        let error = service
            .scene_boundary_authoring_context(&request)
            .unwrap_err();
        assert_eq!(calls.get(), 1);
        assert!(
            matches!(error, StorageError::Domain(DomainError::Invalid(ref message)) if message == "Scene boundary source changed during authoring inspection")
        );
        let (committed, expected_rows) = committed.borrow_mut().take().unwrap();
        assert_eq!(committed.revision, project.revision + 1);
        assert_eq!(service.project(project.id).unwrap(), committed);
        let after = store_rows(&database);
        assert_ne!(after.projects, before.projects);
        assert_eq!(after.events.len(), before.events.len() + 1);
        assert_eq!(after.receipts.len(), before.receipts.len() + 1);
        assert_eq!(
            after, expected_rows,
            "the rejected reader must not add a write or repair the independent commit"
        );
        let mut fresh = request;
        fresh.expected = committed.stamp();
        let context = service.scene_boundary_authoring_context(&fresh).unwrap();
        assert_eq!(
            context.source.dependencies_sha256,
            original.source.dependencies_sha256
        );
        assert_eq!(context.source.authored_revision, committed.revision);
        assert_eq!(service.project(project.id).unwrap(), committed);
        assert_eq!(store_rows(&database), expected_rows);
    }
}
