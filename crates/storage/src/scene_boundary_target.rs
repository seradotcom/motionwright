//! Durable SceneBoundary target admission from the existing typed event journal.
//! A removed declaration may no longer exist in any Project workspace. This
//! read belongs to the new-apply transaction, after complete receipt replay.
use crate::Result;
use motionwright_domain::{Change, DomainError, Project, SceneBoundary};
use rusqlite::{Transaction, params};

pub(super) fn verify_upsert_target(
    tx: &Transaction<'_>,
    project: &Project,
    candidate: &SceneBoundary,
) -> Result<()> {
    candidate.validate_shape()?;
    // Store writes and typed backup imports both serialize Change canonically.
    // Filter by the admitted project and typed change/UUID members, never by
    // request IDs, current generation, or a substring of an arbitrary payload.
    // The existing (project_id, revision) index bounds the project scan. Rows
    // stream one at a time; no journal suffix is silently omitted or capped.
    let mut statement = tx.prepare(
        "SELECT revision,kind,payload_json FROM events
         WHERE project_id=?1 AND (
           (json_extract(payload_json,'$.type')='upsert_scene_boundary'
            AND json_extract(payload_json,'$.boundary.id')=?2)
           OR
           (json_extract(payload_json,'$.type')='remove_scene_boundary'
            AND json_extract(payload_json,'$.boundary_id')=?2))
         ORDER BY revision",
    )?;
    let mut rows = statement.query(params![project.id.to_string(), candidate.id.to_string()])?;
    let mut known_target = false;
    let mut last_revision = 0_u64;
    while let Some(row) = rows.next()? {
        let revision = u64::try_from(row.get::<_, i64>(0)?).map_err(|_| {
            DomainError::Invalid("scene boundary: historical revision is invalid".into())
        })?;
        let kind: String = row.get(1)?;
        if revision <= last_revision || revision > project.revision || kind != "change" {
            return Err(DomainError::Invalid(
                "scene boundary: historical target record is inconsistent".into(),
            )
            .into());
        }
        last_revision = revision;
        let payload: String = row.get(2)?;
        match serde_json::from_str::<Change>(&payload)? {
            Change::UpsertSceneBoundary { boundary } if boundary.id == candidate.id => {
                boundary.validate_shape()?;
                // Check every retained binding, so an unsourced or stale later
                // snapshot cannot erase a profile bound by an earlier event.
                candidate.require_same_target_as(&boundary)?;
                known_target = true;
            }
            Change::RemoveSceneBoundary { boundary_id } if boundary_id == candidate.id => {
                if !known_target {
                    // create_project can persist an initial declaration without
                    // an Upsert event. Remove stores only the UUID; a later
                    // Upsert cannot retroactively authenticate that lost target.
                    // Conservatively require a new UUID, without fabricating a
                    // tombstone, modifying history, or changing known replay.
                    return Err(DomainError::Invalid(
                        "scene boundary: removed identity has no retained prior target; use a new UUID"
                            .into(),
                    )
                    .into());
                }
            }
            _ => {
                return Err(DomainError::Invalid(
                    "scene boundary: historical target selector differs from its typed change"
                        .into(),
                )
                .into());
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ApplyOutcome, Store};
    use motionwright_domain::{
        SceneBoundaryArchetype, SceneBoundaryDefinition, SceneBoundaryFilmOptions,
        SceneBoundaryInput, SceneBoundaryNarrativeRole,
    };
    use rusqlite::{Connection, types::Value};
    use serde_json::json;
    use uuid::Uuid;

    fn apply(store: &mut Store, project: &Project, key: &str, change: &Change) -> ApplyOutcome {
        store
            .apply(project.id, &project.stamp(), key, change)
            .unwrap()
    }

    fn seed(store: &mut Store, title: &str) -> Project {
        let mut project = store.create_named_project(title).unwrap();
        for (index, name) in ["First", "Second"].into_iter().enumerate() {
            project = apply(
                store,
                &project,
                &format!("scene-{index}"),
                &Change::AddScene {
                    name: name.into(),
                    objective: "A durable authored target".into(),
                    duration_seconds: 2,
                },
            )
            .project;
        }
        assert!(project.branch_workspaces.is_empty());
        project
    }

    fn options(project: &Project, profile: usize) -> SceneBoundaryFilmOptions {
        let rate = project.deliverables[profile].frame_rate;
        let options = SceneBoundaryFilmOptions {
            frame_rate: serde_json::from_value(json!({
                "num": u32::try_from(rate.num).unwrap(),
                "den": u32::try_from(rate.den).unwrap(),
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

    fn bound(project: &Project, scene: usize, profile: usize, id: Uuid) -> SceneBoundary {
        let scene_id = project.scenes[scene].id;
        SceneBoundary {
            version: 1,
            id,
            scene_id,
            input: SceneBoundaryInput::SelfContained,
            definition: SceneBoundaryDefinition::DraftIncomplete {
                source: Some(
                    project
                        .capture_scene_boundary_source(
                            scene_id,
                            project.deliverables[profile].id,
                            &options(project, profile),
                        )
                        .unwrap(),
                ),
                entry: None,
                exit: None,
            },
        }
    }

    fn unsourced(mut boundary: SceneBoundary) -> SceneBoundary {
        boundary.definition = SceneBoundaryDefinition::DraftIncomplete {
            source: None,
            entry: None,
            exit: None,
        };
        boundary
    }

    fn upsert(boundary: SceneBoundary) -> Change {
        Change::UpsertSceneBoundary {
            boundary: Box::new(boundary),
        }
    }

    fn remove(store: &mut Store, project: &Project, id: Uuid) -> Project {
        let removed = apply(
            store,
            project,
            "remove-boundary",
            &Change::RemoveSceneBoundary { boundary_id: id },
        )
        .project;
        assert!(removed.production_design.scene_boundaries.is_empty());
        assert!(removed.branch_workspaces.is_empty());
        removed
    }

    fn rows(connection: &Connection, sql: &str) -> Vec<Vec<Value>> {
        let mut statement = connection.prepare(sql).unwrap();
        let columns = statement.column_count();
        statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| row.get(column))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    }

    fn inventory(store: &Store) -> Vec<Vec<Vec<Value>>> {
        [
            "SELECT * FROM projects ORDER BY id",
            "SELECT * FROM events ORDER BY project_id,revision",
            "SELECT * FROM receipts ORDER BY project_id,request_id",
        ]
        .map(|sql| rows(&store.conn, sql))
        .to_vec()
    }

    #[test]
    fn removed_id_retains_all_prior_scene_and_profile_bindings_without_workspace_copies() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("historical-target.sqlite3");
        let mut store = Store::open(&database).unwrap();
        let initial = seed(&mut store, "Historical target");
        let id = Uuid::now_v7();
        let draft = unsourced(bound(&initial, 0, 0, id));
        let draft_project = apply(&mut store, &initial, "first-unsourced", &upsert(draft)).project;
        let binding = bound(&draft_project, 0, 0, id);
        let bound_project = apply(
            &mut store,
            &draft_project,
            "first-binding",
            &upsert(binding),
        )
        .project;
        let current = remove(&mut store, &bound_project, id);
        let before = inventory(&store);
        for (key, candidate) in [
            ("retarget-scene", bound(&current, 1, 0, id)),
            ("retarget-profile", bound(&current, 0, 1, id)),
            ("erase-binding", unsourced(bound(&current, 0, 0, id))),
        ] {
            let error = store
                .apply(current.id, &current.stamp(), key, &upsert(candidate))
                .unwrap_err();
            assert!(error.to_string().contains("scene boundary"));
            assert_eq!(store.load_project(current.id).unwrap(), current);
            assert_eq!(inventory(&store), before);
        }
        drop(store);
        let reopened = Store::open(&database).unwrap();
        assert_eq!(reopened.load_project(current.id).unwrap(), current);
        assert_eq!(inventory(&reopened), before);
    }

    #[test]
    fn removed_id_accepts_its_known_target_and_new_uuid_accepts_a_new_target() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("known-target.sqlite3")).unwrap();
        let initial = seed(&mut store, "Known target");
        let id = Uuid::now_v7();
        let original = upsert(bound(&initial, 0, 0, id));
        let first = apply(&mut store, &initial, "bind", &original).project;
        let removed = remove(&mut store, &first, id);
        let retained = bound(&removed, 0, 0, id);
        let restored = apply(
            &mut store,
            &removed,
            "restore-known-target",
            &upsert(retained.clone()),
        );
        assert_eq!(restored.project.revision, removed.revision + 1);
        assert_eq!(
            restored.project.production_design.scene_boundaries,
            vec![retained]
        );
        let new_id = Uuid::now_v7();
        let other = bound(&restored.project, 1, 1, new_id);
        let next = apply(
            &mut store,
            &restored.project,
            "new-target-new-uuid",
            &upsert(other.clone()),
        );
        assert_eq!(next.project.revision, restored.project.revision + 1);
        assert!(
            next.project
                .production_design
                .scene_boundaries
                .contains(&other)
        );
        assert_eq!(
            store
                .event_records_since(initial.id, removed.revision, 16)
                .unwrap()
                .len(),
            2
        );
    }

    #[test]
    fn known_upsert_receipt_replays_before_history_admission_without_restoring_removed_state() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("known-replay.sqlite3")).unwrap();
        let initial = seed(&mut store, "Known receipt replay");
        let id = Uuid::now_v7();
        let change = upsert(bound(&initial, 0, 0, id));
        let first = apply(&mut store, &initial, "original-upsert", &change);
        let current = remove(&mut store, &first.project, id);
        let before = inventory(&store);
        let replay = apply(&mut store, &initial, "original-upsert", &change);
        let mut expected = first;
        expected.replayed = true;
        assert_eq!(replay, expected);
        assert_eq!(store.load_project(current.id).unwrap(), current);
        assert_eq!(inventory(&store), before);
    }

    #[test]
    fn identical_boundary_uuid_in_a_foreign_project_does_not_supply_its_target_history() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("project-scope.sqlite3")).unwrap();
        let first = seed(&mut store, "First project");
        let id = Uuid::now_v7();
        let bound_first =
            apply(&mut store, &first, "bind", &upsert(bound(&first, 0, 0, id))).project;
        let removed_first = remove(&mut store, &bound_first, id);
        let second = seed(&mut store, "Second project");
        let candidate = bound(&second, 1, 1, id);
        assert_ne!(candidate.scene_id, first.scenes[0].id);
        let accepted = apply(&mut store, &second, "bind", &upsert(candidate.clone()));
        assert_eq!(
            accepted.project.production_design.scene_boundaries,
            vec![candidate]
        );
        assert_eq!(store.load_project(first.id).unwrap(), removed_first);
    }

    #[test]
    fn imported_typed_history_retains_target_across_generation_rotation() {
        let directory = tempfile::tempdir().unwrap();
        let mut source = Store::open(directory.path().join("source.sqlite3")).unwrap();
        let initial = seed(&mut source, "Imported history");
        let id = Uuid::now_v7();
        let first = apply(
            &mut source,
            &initial,
            "bind",
            &upsert(bound(&initial, 0, 0, id)),
        )
        .project;
        let removed = remove(&mut source, &first, id);
        let backup = source.export_project(initial.id).unwrap();
        let mut destination = Store::open(directory.path().join("destination.sqlite3")).unwrap();
        let imported = destination.import_project(&backup).unwrap();
        assert_ne!(imported.generation, removed.generation);
        assert_eq!(
            destination.event_records_since(initial.id, 0, 16).unwrap(),
            backup.events
        );
        let before = inventory(&destination);
        let rejected = upsert(bound(&imported, 0, 1, id));
        assert!(
            destination
                .apply(imported.id, &imported.stamp(), "retarget", &rejected)
                .is_err()
        );
        assert_eq!(inventory(&destination), before);
        let same_target = bound(&imported, 0, 0, id);
        let accepted = apply(
            &mut destination,
            &imported,
            "rebind-same-target",
            &upsert(same_target.clone()),
        );
        assert_eq!(
            accepted.project.production_design.scene_boundaries,
            vec![same_target]
        );
    }

    #[test]
    fn removed_initial_declaration_without_an_upsert_record_requires_a_new_uuid() {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("initial-declaration.sqlite3")).unwrap();
        // Actual create_project accepts a validated initial document, with no
        // journal invention. This is the concrete unavailable-history case.
        let mut initial = Project::new("Initial declaration").unwrap();
        initial
            .apply_change(&Change::AddScene {
                name: "Initial".into(),
                objective: "Initial validated source".into(),
                duration_seconds: 2,
            })
            .unwrap();
        let id = Uuid::now_v7();
        let declaration = bound(&initial, 0, 0, id);
        initial.production_design.scene_boundaries.push(declaration);
        initial.validate().unwrap();
        store.create_project(&initial).unwrap();
        assert!(
            store
                .event_records_since(initial.id, 0, 16)
                .unwrap()
                .is_empty()
        );
        let removal = Change::RemoveSceneBoundary { boundary_id: id };
        let removed = apply(&mut store, &initial, "remove-initial", &removal);
        let before = inventory(&store);
        let candidate = upsert(bound(&removed.project, 0, 0, id));
        let error = store
            .apply(
                initial.id,
                &removed.project.stamp(),
                "reuse-unknown-target",
                &candidate,
            )
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("no retained prior target; use a new UUID")
        );
        assert_eq!(inventory(&store), before);
        let replay = apply(&mut store, &initial, "remove-initial", &removal);
        assert!(replay.replayed);
        assert_eq!(replay.project, removed.project);
        assert_eq!(inventory(&store), before);
        let new_id = Uuid::now_v7();
        let new_boundary = bound(&removed.project, 0, 0, new_id);
        let accepted = apply(
            &mut store,
            &removed.project,
            "new-uuid",
            &upsert(new_boundary.clone()),
        );
        assert_eq!(
            accepted.project.production_design.scene_boundaries,
            vec![new_boundary]
        );
    }
}
