// Real StudioService/SQLite + actual ProductionCoordinator dispatch boundary.
// A fake local CLI process is used ONLY as a transport witness. These cases do
// not establish a native renderer, installed Host or human product acceptance.

fn original_options(project: &motionwright_domain::Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate::new(30, 1).unwrap(),
        font_family: "Instrument Sans Variable".into(),
        mono_font_family: "IBM Plex Mono".into(),
        scene_intents: vec![crate::film::SceneFilmIntent {
            scene_id: project.scenes[0].id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
        }],
    }
}

fn original_render_request() -> Value {
    json!({
        "expected_fingerprint": "a".repeat(64),
        "profile": {
            "first_frame": 0,
            "end_frame_exclusive": 60,
            "scale": "full",
            "transparent": false,
            "timeout_ms": 300_000
        }
    })
}

#[tokio::test]
async fn productive_root_debits_sqlite_before_cli_and_exhausts_actual_frames() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let scene_id = project.scenes[0].id;
    let deliverable_id = project.deliverables[0].id;
    let connection = fake_render_connection(&temp, project.resource_key());
    let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
    let scoped = coordinator
        .with_motion_scene_boundary_scope(&project, deliverable_id, &options, &[scene_id])
        .unwrap();
    let root_id = scoped.active_motion_budget.as_ref().unwrap().root_id;
    assert_eq!(
        service
            .root_operation_budget(project.id, root_id)
            .unwrap()
            .spent,
        RootBudgetUnits::default()
    );
    assert!(
        service
            .production_receipts(project.id, 10)
            .unwrap()
            .is_empty()
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);

    // N83's dirty/full-after jobs must retain this same genuine Coordinator.
    // A second scoped operation shares the original durable root and cannot
    // reset its accounting. Merely recreating a Coordinator after a Store
    // reopen is deliberately insufficient: Store rejects duplicate creation.
    let original_root = service.root_operation_budget(project.id, root_id).unwrap();
    let second_scope = coordinator
        .with_motion_scene_boundary_scope(&project, deliverable_id, &options, &[scene_id])
        .unwrap();
    assert_eq!(
        second_scope.active_motion_budget.as_ref().unwrap().root_id,
        root_id
    );
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        original_root
    );
    assert_eq!(second_scope.client.execute_calls.load(Ordering::SeqCst), 0);
    drop(second_scope);
    let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let recreated = ProductionCoordinator::new(reopened.clone(), connection.clone()).unwrap();
    let denied_recreation = match recreated.with_motion_scene_boundary_scope(
        &project,
        deliverable_id,
        &options,
        &[scene_id],
    ) {
        Ok(_) => {
            panic!("a new Coordinator must not silently recreate or reattach the accounting root")
        }
        Err(error) => error,
    };
    assert_eq!(denied_recreation.code, ErrorCode::Conflict);
    assert_eq!(
        denied_recreation.message,
        "Production request id was reused with different input"
    );
    assert_eq!(recreated.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        reopened.root_operation_budget(project.id, root_id).unwrap(),
        original_root
    );
    assert!(
        reopened
            .production_receipts(project.id, 10)
            .unwrap()
            .is_empty()
    );
    drop(recreated);
    drop(reopened);

    // Check the N83 accounting shape (one 90-frame MC segment at 640x360)
    // through the actual planner and SQLite root, without a native dispatch.
    // The paired fixture must fit both classified starts in the original cap;
    // changing a request label or reopening a handle cannot add capacity.
    {
        let (n83_service, n83_initial, n83_temp) = fixture_service();
        let n83_project = motion_canvas_fixture(&n83_service, n83_initial);
        let n83_scene = n83_project.scenes[0].id;
        let n83_project = n83_service
            .apply(
                n83_project.id,
                &n83_project.stamp(),
                "n83-budget-duration",
                &Change::SetSceneDuration {
                    scene_id: n83_scene,
                    duration: motionwright_domain::RationalTime::new(3, 1).unwrap(),
                },
            )
            .unwrap()
            .project;
        let mut n83_profile = n83_project.deliverables[0].clone();
        n83_profile.width = 640;
        n83_profile.height = 360;
        let n83_project = n83_service
            .apply(
                n83_project.id,
                &n83_project.stamp(),
                "n83-budget-output",
                &Change::UpsertDeliverable {
                    profile: n83_profile,
                },
            )
            .unwrap()
            .project;
        let n83_options = original_options(&n83_project);
        let n83_connection = fake_connection(&n83_temp, n83_project.resource_key());
        let n83_coordinator =
            ProductionCoordinator::new(n83_service.clone(), n83_connection).unwrap();
        let n83_scoped = n83_coordinator
            .with_motion_scene_boundary_scope(
                &n83_project,
                n83_project.deliverables[0].id,
                &n83_options,
                &[n83_scene],
            )
            .unwrap();
        let n83_active = n83_scoped.active_motion_budget.as_ref().unwrap();
        assert_eq!(n83_active.max_render_frames, 90);
        let n83_root = n83_service
            .root_operation_budget(n83_project.id, n83_active.root_id)
            .unwrap();
        let dirty = n83_active
            .units_for(
                "driver.motion-canvas.render.start",
                &json!({
                    "profile":{"first_frame":0,"end_frame_exclusive":60},
                }),
                true,
            )
            .unwrap();
        let reference = n83_active
            .units_for(
                "driver.motion-canvas.render.start",
                &json!({
                    "profile":{"first_frame":0,"end_frame_exclusive":90},
                }),
                true,
            )
            .unwrap();
        let source_mutation = n83_active
            .units_for("driver.motion-canvas.composition.apply", &json!({}), true)
            .unwrap();
        let font_import = n83_active
            .units_for("driver.motion-canvas.asset.import", &json!({}), true)
            .unwrap();
        assert_eq!(dirty.render_frames + reference.render_frames, 150);
        assert!(dirty.render_frames + reference.render_frames <= n83_root.limits.render_frames);
        // Four source writes and four font imports conservatively cover two
        // separate jobs; these units are projections, not measured work.
        assert!(
            dirty.output_bytes
                + reference.output_bytes
                + 4 * source_mutation.output_bytes
                + 4 * font_import.output_bytes
                <= n83_root.limits.output_bytes
        );
        assert_eq!(n83_root.spent, RootBudgetUnits::default());
        assert_eq!(n83_scoped.client.execute_calls.load(Ordering::SeqCst), 0);
    }

    let args = original_render_request();
    let first = scoped
        .execute(
            project.id,
            &project.stamp(),
            "root-first-render",
            "driver.motion-canvas.render.start",
            args.clone(),
            true,
        )
        .await
        .unwrap();
    assert_eq!(first["replayed"], false);
    assert_eq!(first["result"]["data"]["job_ref"], "job-native-1");
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    let spent = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(spent.spent.attempts, 1);
    assert_eq!(spent.spent.model_calls, 0);
    assert_eq!(spent.spent.render_frames, 60);
    assert!(spent.spent.output_bytes > 0);
    let receipts = service.production_receipts(project.id, 10).unwrap();
    assert_eq!(receipts.len(), 2);
    assert_eq!(
        receipts.iter().filter(|r| r.stage == "dispatching").count(),
        1
    );
    assert_eq!(
        receipts.iter().filter(|r| r.stage == "completed").count(),
        1
    );

    let replay = scoped
        .execute(
            project.id,
            &project.stamp(),
            "root-first-render",
            "driver.motion-canvas.render.start",
            args.clone(),
            true,
        )
        .await
        .unwrap();
    assert_eq!(replay["replayed"], true);
    assert_eq!(replay["result"], first["result"]);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        spent
    );
    assert_eq!(
        service.production_receipts(project.id, 10).unwrap(),
        receipts
    );

    // The root now reserves the finite MC + direct FFV1 + mux operation.
    // Exhaust the actual immutable frame cap; do not mistake the additional
    // continuation capacity for permission to reset the original operation.
    let allowed_renders = spent.limits.render_frames / 60;
    assert_eq!(spent.limits.render_frames % 60, 0);
    for index in 1..allowed_renders {
        scoped
            .execute(
                project.id,
                &project.stamp(),
                &format!("root-budget-child-{index}"),
                "driver.motion-canvas.render.start",
                args.clone(),
                true,
            )
            .await
            .unwrap();
    }
    let spent = service.root_operation_budget(project.id, root_id).unwrap();
    let receipts = service.production_receipts(project.id, 20).unwrap();
    assert_eq!(spent.spent.render_frames, spent.limits.render_frames);

    let denied = scoped
        .execute(
            project.id,
            &project.stamp(),
            "root-second-render-exceeds-budget",
            "driver.motion-canvas.render.start",
            args,
            true,
        )
        .await
        .unwrap_err();
    assert!(denied.outcome_known);
    assert_eq!(
        scoped.client.execute_calls.load(Ordering::SeqCst),
        allowed_renders as usize
    );
    assert!(
        service
            .latest_production_receipt(project.id, "root-second-render-exceeds-budget")
            .unwrap()
            .is_none()
    );
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        spent
    );

    drop(scoped);
    drop(coordinator);
    drop(service);
    let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let retained = reopened.project(project.id).unwrap();
    let fresh = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    let refused = fresh
        .with_motion_scene_boundary_scope(&retained, deliverable_id, &options, &[scene_id])
        .err()
        .expect("a fresh coordinator cannot mint another original budget");
    assert!(refused.outcome_known);
    assert_eq!(fresh.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        reopened.root_operation_budget(project.id, root_id).unwrap(),
        spent
    );
    assert_eq!(
        reopened.production_receipts(project.id, 20).unwrap(),
        receipts
    );
}

#[tokio::test]
async fn productive_unknown_retains_spent_root_after_restart_and_blocks_new_label() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let scene_id = project.scenes[0].id;
    let deliverable_id = project.deliverables[0].id;
    let (connection, counter) =
        fake_transport_connection(&temp, project.resource_key(), "{not-json", "", 0);
    let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
    let scoped = coordinator
        .with_motion_scene_boundary_scope(&project, deliverable_id, &options, &[scene_id])
        .unwrap();
    let root_id = scoped.active_motion_budget.as_ref().unwrap().root_id;
    let args = original_render_request();
    let first_error = scoped
        .execute(
            project.id,
            &project.stamp(),
            "original-unknown-native-start",
            "driver.motion-canvas.render.start",
            args.clone(),
            true,
        )
        .await
        .unwrap_err();
    assert!(!first_error.outcome_known);
    assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
    let unknown = service
        .latest_production_receipt(project.id, "original-unknown-native-start")
        .unwrap()
        .unwrap();
    assert_eq!(unknown.stage, "outcome_unknown");
    assert_eq!(unknown.payload["mutation"], true);
    let spent = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(spent.spent.attempts, 1);
    assert_eq!(spent.spent.render_frames, 60);
    let receipts = service.production_receipts(project.id, 20).unwrap();
    assert_eq!(receipts.len(), 2);

    let retry = scoped
        .execute(
            project.id,
            &project.stamp(),
            "original-unknown-native-start",
            "driver.motion-canvas.render.start",
            args.clone(),
            true,
        )
        .await
        .unwrap_err();
    assert!(!retry.outcome_known);
    assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
    let alias = scoped
        .execute(
            project.id,
            &project.stamp(),
            "unknown-alternate-stage-label",
            "driver.motion-canvas.render.start",
            args,
            true,
        )
        .await
        .unwrap_err();
    assert!(alias.outcome_known);
    assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        spent
    );
    assert_eq!(
        service.production_receipts(project.id, 20).unwrap(),
        receipts
    );

    drop(scoped);
    drop(coordinator);
    drop(service);
    let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let retained = reopened.project(project.id).unwrap();
    let fresh = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    let refused = fresh
        .with_motion_scene_boundary_scope(&retained, deliverable_id, &options, &[scene_id])
        .err()
        .expect("UNKNOWN cannot obtain another budget after restart");
    assert!(refused.outcome_known);
    assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
    assert_eq!(
        reopened.root_operation_budget(project.id, root_id).unwrap(),
        spent
    );
    assert_eq!(
        reopened.production_receipts(project.id, 20).unwrap(),
        receipts
    );
}

#[tokio::test]
async fn rooted_project_disallows_unbudgeted_native_side_door_but_admits_scoped_read() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let connection = fake_connection(&temp, project.resource_key());
    let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let scoped = coordinator
        .with_motion_scene_boundary_scope(
            &project,
            project.deliverables[0].id,
            &options,
            &[project.scenes[0].id],
        )
        .unwrap();
    let root_id = scoped.active_motion_budget.as_ref().unwrap().root_id;
    let rejected = coordinator
        .execute(
            project.id,
            &project.stamp(),
            "unbudgeted-observation",
            "driver.motion-canvas.composition.inspect",
            json!({}),
            false,
        )
        .await
        .unwrap_err();
    assert!(rejected.outcome_known);
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    assert!(
        service
            .production_receipts(project.id, 10)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        service
            .root_operation_budget(project.id, root_id)
            .unwrap()
            .spent,
        RootBudgetUnits::default()
    );

    let admitted = scoped
        .execute(
            project.id,
            &project.stamp(),
            "scoped-original-observation",
            "driver.motion-canvas.composition.inspect",
            json!({}),
            false,
        )
        .await
        .unwrap();
    assert_eq!(admitted["replayed"], false);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        service
            .root_operation_budget(project.id, root_id)
            .unwrap()
            .spent
            .attempts,
        1
    );
    assert_eq!(
        service.production_receipts(project.id, 10).unwrap().len(),
        2
    );
}

#[tokio::test]
async fn productive_unknown_alias_is_refused_with_headroom_and_status_remains_budgeted() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let mut connection = fake_render_connection(&temp, project.resource_key());
    let script = fs::read_to_string(&connection.executable).unwrap();
    let anchor = "  driver.motion-canvas.render.start)\n";
    assert_eq!(script.matches(anchor).count(), 1);
    fs::write(
        &connection.executable,
        script.replace(
            anchor,
            &format!("{anchor}    printf '{{not-json'; exit 0\n"),
        ),
    )
    .unwrap();
    connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
    let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let scoped = coordinator
        .with_motion_scene_boundary_scope(
            &project,
            project.deliverables[0].id,
            &options,
            &[project.scenes[0].id],
        )
        .unwrap();
    let root_id = scoped.active_motion_budget.as_ref().unwrap().root_id;
    let mut args = original_render_request();
    args["profile"]["end_frame_exclusive"] = json!(30);
    let units = scoped
        .active_motion_budget
        .as_ref()
        .unwrap()
        .units_for("driver.motion-canvas.render.start", &args, true)
        .unwrap();
    let unknown = scoped
        .execute(
            project.id,
            &project.stamp(),
            "unknown-with-headroom",
            "driver.motion-canvas.render.start",
            args.clone(),
            true,
        )
        .await
        .unwrap_err();
    assert!(!unknown.outcome_known);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    let retained = service.root_operation_budget(project.id, root_id).unwrap();
    let receipts = service.production_receipts(project.id, 20).unwrap();
    // A second identical debit fits every dimension. The denial below must
    // therefore come from unresolved effect identity, not an exhausted cap.
    assert!(retained.spent.attempts + units.attempts <= retained.limits.attempts);
    assert!(retained.spent.model_calls + units.model_calls <= retained.limits.model_calls);
    assert!(retained.spent.render_frames + units.render_frames <= retained.limits.render_frames);
    assert!(retained.spent.output_bytes + units.output_bytes <= retained.limits.output_bytes);
    let denied = scoped
        .execute(
            project.id,
            &project.stamp(),
            "renamed-unknown-with-headroom",
            "driver.motion-canvas.render.start",
            args,
            true,
        )
        .await
        .unwrap_err();
    assert!(denied.outcome_known);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        retained
    );
    assert_eq!(
        service.production_receipts(project.id, 20).unwrap(),
        receipts
    );
    assert_eq!(
        service
            .latest_production_receipt(project.id, "unknown-with-headroom")
            .unwrap()
            .unwrap()
            .stage,
        "outcome_unknown"
    );

    let observed = scoped
        .execute(
            project.id,
            &project.stamp(),
            "retained-job-status-after-unknown",
            "driver.motion-canvas.render.status",
            json!({"job_ref":"job-native-1"}),
            false,
        )
        .await
        .unwrap();
    assert_eq!(observed["replayed"], false);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 2);
    let after_read = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(after_read.spent.attempts, retained.spent.attempts + 1);
    assert_eq!(after_read.spent.render_frames, retained.spent.render_frames);
    assert_eq!(after_read.spent.output_bytes, retained.spent.output_bytes);
    assert_eq!(
        service
            .latest_production_receipt(project.id, "unknown-with-headroom")
            .unwrap()
            .unwrap()
            .stage,
        "outcome_unknown"
    );
}

#[tokio::test]
async fn productive_two_children_observe_committed_debits_from_independent_transport_process() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let scene_id = project.scenes[0].id;
    let deliverable_id = project.deliverables[0].id;
    let database = temp.path().join("motionwright.sqlite3");
    let witness = temp.path().join("transport-sqlite-observations.jsonl");
    let probe = temp.path().join("observe_root.py");
    fs::write(
        &probe,
        r#"import json, pathlib, sqlite3, sys
database, project, command, destination = sys.argv[1:]
connection = sqlite3.connect(pathlib.Path(database).as_uri() + '?mode=ro', uri=True)
rows = connection.execute('''SELECT d.request_id,d.command,d.attempts,p.stage,d.root_id
  FROM production_operation_debits AS d
  JOIN production_receipts AS p ON p.receipt_id=d.receipt_id
  WHERE d.project_id=? ORDER BY d.request_id''', (project,)).fetchall()
connection.close()
assert rows and any(row[1] == command and row[3] == 'dispatching' for row in rows)
with open(destination, 'a', encoding='utf-8') as output:
    output.write(json.dumps({'command':command,'rows':rows}) + '\n')
"#,
    )
    .unwrap();
    let mut connection = fake_render_connection(&temp, project.resource_key());
    let quoted = |path: &Path| format!("'{}'", path.display().to_string().replace('\'', "'\\''"));
    let script = fs::read_to_string(&connection.executable).unwrap();
    let anchor = "command=\"$7\"\n";
    assert_eq!(script.matches(anchor).count(), 1);
    let observed = script.replace(
        anchor,
        &format!(
            "{anchor}python3 {} {} '{}' \"$command\" {} || exit 31\n",
            quoted(&probe),
            quoted(&database),
            project.id,
            quoted(&witness),
        ),
    );
    fs::write(&connection.executable, observed).unwrap();
    connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
    let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let scoped = coordinator
        .with_motion_scene_boundary_scope(&project, deliverable_id, &options, &[scene_id])
        .unwrap();
    let root_id = scoped.active_motion_budget.as_ref().unwrap().root_id;
    let segment = plan_motion_canvas_segments(&project, deliverable_id, &options)
        .unwrap()
        .remove(0);
    let planned = scoped
        .execute(
            project.id,
            &project.stamp(),
            "original-plan-child",
            "driver.motion-canvas.composition.plan",
            json!({"film":segment.film,"budget":{"max_iterations":8,
            "max_operations":MOTION_PLAN_MAX_OPERATIONS,"max_findings":1024,
            "max_observations":128,"max_elapsed_ms":120000}}),
            false,
        )
        .await
        .unwrap();
    let plan_ref = planned["result"]["data"]["plan_ref"].as_str().unwrap();
    let applied = scoped
        .execute(
            project.id,
            &project.stamp(),
            "original-apply-child",
            "driver.motion-canvas.composition.apply",
            json!({"plan_ref":plan_ref,"dry_run":false}),
            true,
        )
        .await
        .unwrap();
    assert_eq!(applied["result"]["data"]["applied"], true);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 2);
    let root = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(root.spent.attempts, 2);
    assert_eq!(root.spent.render_frames, 0);
    assert_eq!(root.spent.output_bytes, MOTION_ROOT_FRAME_RESERVE_MIN_BYTES);
    let observations = fs::read_to_string(&witness)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(observations.len(), 2);
    assert_eq!(observations[0]["rows"].as_array().unwrap().len(), 1);
    assert_eq!(observations[0]["rows"][0][0], "original-plan-child");
    assert_eq!(observations[1]["rows"].as_array().unwrap().len(), 2);
    for observation in observations {
        for row in observation["rows"].as_array().unwrap() {
            assert_eq!(row[2], 1);
            assert_eq!(row[3], "dispatching");
            assert_eq!(row[4], root_id.to_string());
        }
    }
}

#[test]
fn productive_root_refuses_changed_options_foreign_generation_and_resource_before_effects() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let scene_id = project.scenes[0].id;
    let deliverable_id = project.deliverables[0].id;
    let connection = fake_connection(&temp, project.resource_key());
    let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let scoped = coordinator
        .with_motion_scene_boundary_scope(&project, deliverable_id, &options, &[scene_id])
        .unwrap();
    let root_id = scoped.active_motion_budget.as_ref().unwrap().root_id;
    let original = service.root_operation_budget(project.id, root_id).unwrap();
    let mut changed_options = options.clone();
    changed_options.scene_intents[0].role = NarrativeRole::Evidence;
    assert!(plan_motion_canvas_segments(&project, deliverable_id, &changed_options).is_ok());
    let changed = coordinator
        .with_motion_scene_boundary_scope(&project, deliverable_id, &changed_options, &[scene_id])
        .err()
        .expect("a changed source option cannot mint fresh root credit");
    assert_eq!(changed.code, ErrorCode::Conflict);
    let mut foreign = project.clone();
    foreign.generation = Uuid::now_v7();
    assert!(
        coordinator
            .with_motion_scene_boundary_scope(&foreign, deliverable_id, &options, &[scene_id])
            .is_err()
    );
    let mut foreign_resource = coordinator.clone();
    foreign_resource.client.connection.resource = "project:foreign".into();
    assert!(
        foreign_resource
            .with_motion_scene_boundary_scope(&project, deliverable_id, &options, &[scene_id])
            .is_err()
    );
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        original
    );
    assert!(
        service
            .production_receipts(project.id, 10)
            .unwrap()
            .is_empty()
    );
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn productive_mlt_continuation_keeps_root_and_fences_unknown_changed_output() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let deliverable_id = project.deliverables[0].id;
    let mut connection = fake_render_connection(&temp, project.resource_key());
    let manifest_path = connection.output_root.join("original-motion-manifest.json");
    fs::write(&manifest_path, b"transport-only original manifest").unwrap();
    let manifest_sha = sha256_file(&manifest_path).unwrap();
    let audio_path = connection.output_root.join("original-audio.wav");
    fs::write(&audio_path, b"transport-only original audio").unwrap();
    let audio = MltAudioArtifact {
        relative_path: "original-audio.wav".into(),
        sha256: sha256_file(&audio_path).unwrap(),
        sample_rate: 48_000,
        channels: 2,
    };
    let database = temp.path().join("motionwright.sqlite3");
    let witness = temp.path().join("mixed-provider-admissions.jsonl");
    let probe = temp.path().join("observe_mixed_root.py");
    fs::write(
        &probe,
        r#"import json, pathlib, sqlite3, sys
database, project, command, destination = sys.argv[1:]
connection = sqlite3.connect(pathlib.Path(database).as_uri() + '?mode=ro', uri=True)
rows = connection.execute('''SELECT d.command,d.root_id,d.render_frames,p.stage
 FROM production_operation_debits AS d JOIN production_receipts AS p
 ON p.receipt_id=d.receipt_id WHERE d.project_id=?''', (project,)).fetchall()
connection.close()
assert any(row[0] == command and row[3] == 'dispatching' for row in rows)
with open(destination, 'a', encoding='utf-8') as output:
 output.write(json.dumps({'command':command,'rows':rows}) + '\n')
"#,
    )
    .unwrap();
    let quote = |path: &Path| format!("'{}'", path.display().to_string().replace('\'', "'\\''"));
    let script = fs::read_to_string(&connection.executable).unwrap();
    let old_manifest = format!("\"manifest_sha256\":\"{}\"", "b".repeat(64));
    assert_eq!(script.matches(&old_manifest).count(), 1);
    let script = script.replace(
        &old_manifest,
        &format!(
            "\"manifest\":\"original-motion-manifest.json\",\"manifest_sha256\":\"{manifest_sha}\""
        ),
    );
    let anchor = "command=\"$7\"\n";
    assert_eq!(script.matches(anchor).count(), 1);
    let script = script.replace(
        anchor,
        &format!(
            "{anchor}python3 {} {} '{}' \"$command\" {} || exit 31\n",
            quote(&probe),
            quote(&database),
            project.id,
            quote(&witness),
        ),
    );
    let anchor = "case \"$command\" in\n";
    assert_eq!(script.matches(anchor).count(), 1);
    fs::write(&connection.executable, script.replace(anchor, &format!(
        "{anchor}  driver.mlt-video.frames.encode)\n    printf '{{not-json'; exit 0\n    ;;\n"
    ))).unwrap();
    connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
    let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let rendered = coordinator
        .render_motion_canvas_segments(
            project.id,
            &project.stamp(),
            "original-mixed-motion",
            deliverable_id,
            &options,
        )
        .await
        .unwrap();
    let original = coordinator
        .retained_motion_continuation(&project, deliverable_id, Some(&options), &rendered)
        .unwrap();
    let root_id = original.root_id;
    let before = service.root_operation_budget(project.id, root_id).unwrap();
    let before_calls = coordinator.client.execute_calls.load(Ordering::SeqCst);
    assert_eq!(before.spent.render_frames, 60);
    let first = coordinator
        .assemble_mlt_av_master(
            project.id,
            &project.stamp(),
            MltAvMasterRequest {
                request_id: "original-mixed-master",
                deliverable_id,
                motion: &rendered,
                audio: &audio,
                sync: None,
            },
        )
        .await
        .unwrap_err();
    assert!(!first.outcome_known);
    assert_eq!(
        coordinator.client.execute_calls.load(Ordering::SeqCst),
        before_calls + 1
    );
    let retained = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(retained.spent.attempts, before.spent.attempts + 1);
    assert_eq!(
        retained.spent.render_frames,
        before.spent.render_frames + 60
    );
    let master_token = hex::encode(Sha256::digest(b"original-mixed-master"));
    let encode_path = format!(
        "mw-{}-r{}-{}-motion.mkv",
        project.id.simple(),
        project.revision,
        &master_token[..16]
    );
    let original_encode_bytes = hybrid_budget::mlt_encode_output_bytes(
        project.deliverables[0].width,
        project.deliverables[0].height,
        60,
        &encode_path,
        MLT_MEZZANINE_MAX_BYTES,
    )
    .unwrap();
    assert_eq!(
        retained.spent.output_bytes,
        before.spent.output_bytes + original_encode_bytes
    );
    assert!(retained.spent.attempts < retained.limits.attempts);
    assert!(retained.spent.render_frames + 60 <= retained.limits.render_frames);
    assert!(retained.spent.output_bytes + original_encode_bytes <= retained.limits.output_bytes);
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    // The producer derives another output filename from this label, so this
    // is a different digest. The unresolved original root must still fence it.
    let renamed = coordinator
        .assemble_mlt_av_master(
            project.id,
            &project.stamp(),
            MltAvMasterRequest {
                request_id: "changed-mixed-master-output",
                deliverable_id,
                motion: &rendered,
                audio: &audio,
                sync: None,
            },
        )
        .await
        .unwrap_err();
    assert!(renamed.outcome_known);
    assert_eq!(
        coordinator.client.execute_calls.load(Ordering::SeqCst),
        before_calls + 1
    );
    assert_eq!(
        service.root_operation_budget(project.id, root_id).unwrap(),
        retained
    );
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    let unknown_key = "original-mixed-master:mlt:frames-encode";
    assert_eq!(
        service
            .latest_production_receipt(project.id, unknown_key)
            .unwrap()
            .unwrap()
            .stage,
        "outcome_unknown"
    );
    let observations = fs::read_to_string(&witness)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(observations.len(), before_calls + 1);
    let last = observations.last().unwrap();
    assert_eq!(last["command"], "driver.mlt-video.frames.encode");
    for row in last["rows"].as_array().unwrap() {
        assert_eq!(row[1], root_id.to_string());
        assert_eq!(row[3], "dispatching");
    }
    let scoped = coordinator
        .with_motion_scene_boundary_scope(
            &project,
            deliverable_id,
            &options,
            &[project.scenes[0].id],
        )
        .unwrap();
    scoped
        .execute(
            project.id,
            &project.stamp(),
            "mixed-read-after-unknown",
            "driver.motion-canvas.render.status",
            json!({"job_ref":"job-native-1"}),
            false,
        )
        .await
        .unwrap();
    assert_eq!(
        service
            .root_operation_budget(project.id, root_id)
            .unwrap()
            .spent
            .attempts,
        retained.spent.attempts + 1
    );
    assert_eq!(
        service
            .latest_production_receipt(project.id, unknown_key)
            .unwrap()
            .unwrap()
            .stage,
        "outcome_unknown"
    );
}

#[test]
fn retained_mlt_costs_bind_exact_source_frames_and_both_mux_artifacts() {
    let encode = MltEncodeBudget {
        manifest_sha256: "a".repeat(64),
        frames: 60,
        output_path: "original.mkv".into(),
        max_bytes: MLT_MEZZANINE_MAX_BYTES,
    };
    let scope = MltDispatchBudget {
        route: MltBudgetRoute::Direct,
        width: 1920,
        height: 1080,
        frames: 60,
        encodes: std::collections::BTreeMap::from([("original.json".into(), encode)]),
        timeline_paths: vec![],
        mux_video_path: "original.mkv".into(),
        mux_output_path: Some("original.mp4".into()),
    };
    let encode_args = json!({"root":"output", "manifest_path":"original.json",
        "expected_manifest_sha256":"a".repeat(64), "output_path":"original.mkv",
        "max_bytes":MLT_MEZZANINE_MAX_BYTES});
    let units = scope
        .units_for("driver.mlt-video.frames.encode", &encode_args, true)
        .unwrap();
    assert_eq!(units.render_frames, 60);
    assert_eq!(
        units.output_bytes,
        hybrid_budget::mlt_encode_output_bytes(
            1920,
            1080,
            60,
            "original.mkv",
            MLT_MEZZANINE_MAX_BYTES,
        )
        .unwrap()
    );
    let mux = json!({"video_root":"output", "video_path":"original.mkv",
        "output_path":"original.mp4", "frame_count":60,
        "max_bytes":MLT_MASTER_MAX_BYTES, "profile":"h264-aac-mp4"});
    let units = scope
        .units_for("driver.mlt-video.av.mux", &mux, true)
        .unwrap();
    assert_eq!(units.render_frames, 60);
    assert_eq!(
        units.output_bytes,
        hybrid_budget::mlt_mux_output_bytes(1920, 1080, 60, "original.mp4", MLT_MASTER_MAX_BYTES,)
            .unwrap()
    );
    for (field, bad) in [
        ("manifest_path", json!("foreign.json")),
        ("expected_manifest_sha256", json!("b".repeat(64))),
        ("output_path", json!("changed.mkv")),
        ("max_bytes", json!(MLT_MASTER_MAX_BYTES)),
    ] {
        let mut foreign = encode_args.clone();
        foreign[field] = bad;
        assert!(
            scope
                .units_for("driver.mlt-video.frames.encode", &foreign, true)
                .is_err()
        );
    }
    assert!(
        scope
            .units_for("driver.mlt-video.project.close", &json!({}), true)
            .is_err()
    );
}

#[tokio::test]
async fn mlt_import_retained_originals_require_disk_credit_before_cli_even_without_media_output() {
    let cost = hybrid_budget::mlt_import_output_bytes().unwrap();
    for capacity in [cost - 1, cost] {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        // The witness has no MLT implementation and emits no media or JSON for
        // this command. If reached, that original attempt remains UNKNOWN.
        let connection = fake_render_connection(&temp, project.resource_key());
        let mut scoped = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let root = service
            .create_root_operation_budget(RootOperationBudgetInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                operation_key: "test-original-import".into(),
                limits: RootBudgetUnits {
                    attempts: 1,
                    model_calls: 0,
                    render_frames: 0,
                    output_bytes: capacity,
                },
            })
            .unwrap();
        scoped.active_motion_budget = Some(MotionRootBudgetScope {
            root_id: root.root_id,
            max_render_frames: 60,
            max_frame_bytes: MOTION_ROOT_FRAME_RESERVE_MIN_BYTES,
            mlt_route: Some(MltBudgetRoute::Timeline),
            mlt: Some(MltDispatchBudget {
                route: MltBudgetRoute::Timeline,
                width: 1920,
                height: 1080,
                frames: 60,
                encodes: std::collections::BTreeMap::from([(
                    "source.json".into(),
                    MltEncodeBudget {
                        manifest_sha256: "a".repeat(64),
                        frames: 60,
                        output_path: "original.mkv".into(),
                        max_bytes: MLT_MEZZANINE_MAX_BYTES,
                    },
                )]),
                timeline_paths: vec!["timeline.mkv".into()],
                mux_video_path: "timeline.mkv".into(),
                mux_output_path: None,
            }),
            audio_analysis: false,
        });
        let result = scoped
            .execute(
                project.id,
                &project.stamp(),
                "original-import",
                "driver.mlt-video.asset.import",
                json!({
                    "project":"test-native-project", "expected_revision":"test-native-revision",
                    "kind":"file", "root":"output", "path":"original.mkv", "name":"Original",
                }),
                true,
            )
            .await;
        assert!(result.is_err());
        let after = service
            .root_operation_budget(project.id, root.root_id)
            .unwrap();
        if capacity < cost {
            assert!(result.unwrap_err().outcome_known);
            assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);
            assert_eq!(after, root);
            assert!(
                service
                    .production_receipts(project.id, 10)
                    .unwrap()
                    .is_empty()
            );
        } else {
            assert!(!result.unwrap_err().outcome_known);
            assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
            assert_eq!(after.spent.attempts, 1);
            assert_eq!(after.spent.render_frames, 0);
            assert_eq!(after.spent.output_bytes, cost);
            assert_eq!(
                service
                    .latest_production_receipt(project.id, "original-import")
                    .unwrap()
                    .unwrap()
                    .stage,
                "outcome_unknown"
            );
        }
    }
}

#[tokio::test]
async fn mc_capture_keeps_its_root_when_optional_mlt_is_unsupported_but_explicit_mlt_is_rejected() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let scene_id = project.scenes[0].id;
    let project = service
        .apply(
            project.id,
            &project.stamp(),
            "long-mc-only-source",
            &Change::SetSceneDuration {
                scene_id,
                duration: motionwright_domain::RationalTime::new(30, 1).unwrap(),
            },
        )
        .unwrap()
        .project;
    let options = original_options(&project);
    let deliverable = &project.deliverables[0];
    assert!(
        semwright_video_domain::native_observation::observation_budget(
            deliverable.width,
            deliverable.height,
            900,
        )
        .is_err()
    );
    let connection = fake_render_connection(&temp, project.resource_key());
    let owner = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let scoped = owner
        .with_motion_scene_boundary_scope(&project, deliverable.id, &options, &[scene_id])
        .unwrap();
    let scope = scoped.active_motion_budget.as_ref().unwrap();
    assert_eq!(scope.mlt_route, None);
    let root = service
        .root_operation_budget(project.id, scope.root_id)
        .unwrap();
    assert_eq!(root.limits.render_frames, 900);
    assert!(
        scope
            .units_for(
                "driver.motion-canvas.render.start",
                &original_render_request(),
                true
            )
            .is_ok()
    );
    let error = scoped
        .execute(
            project.id,
            &project.stamp(),
            "explicit-unsupported-mlt",
            "driver.mlt-video.frames.encode",
            json!({
                "root":"output", "manifest_path":"original.json",
                "expected_manifest_sha256":"a".repeat(64),
                "output_path":"explicit.mkv", "max_bytes":MLT_MEZZANINE_MAX_BYTES,
            }),
            true,
        )
        .await
        .unwrap_err();
    assert!(error.outcome_known);
    assert_eq!(error.code, ErrorCode::PermissionDenied);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        service
            .root_operation_budget(project.id, scope.root_id)
            .unwrap(),
        root
    );
    assert!(
        service
            .production_receipts(project.id, 10)
            .unwrap()
            .is_empty()
    );
}
