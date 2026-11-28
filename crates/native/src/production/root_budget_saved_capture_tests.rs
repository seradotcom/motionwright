// Synthetic CLI and file bytes with real SQLite. These test cold recovery,
// integrity and accounting only; they are not physical Native render evidence.

fn saved_capture_connection(temp: &tempfile::TempDir, resource: String) -> ProductionConnection {
    let mut connection = fake_render_connection(temp, resource);
    let directory = connection.output_root.join("render-native-1");
    fs::create_dir_all(directory.join("frames")).unwrap();
    let mut frames = Vec::new();
    for index in 0..60_u64 {
        let bytes = format!("synthetic original frame {index:06}").into_bytes();
        let file = format!("frames/{index:06}.png");
        fs::write(directory.join(&file), &bytes).unwrap();
        frames.push(json!({"index":index,"file":file,"bytes":bytes.len(),
            "sha256":hex::encode(Sha256::digest(&bytes))}));
    }
    let manifest =
        json!({"plan":{"first_frame":0,"end_frame_exclusive":60,"frame_count":60},"frames":frames});
    let manifest_path = directory.join("artifact-manifest.json");
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    let digest = sha256_file(&manifest_path).unwrap();
    let script = fs::read_to_string(&connection.executable).unwrap()
        .replace(&format!("\"manifest_sha256\":\"{}\"","b".repeat(64)),
            &format!("\"manifest\":\"render-native-1/artifact-manifest.json\",\"manifest_sha256\":\"{digest}\""))
        .replace("case \"$command\" in\n",
            "case \"$command\" in\n  driver.mlt-video.frames.encode)\n    printf '{not-json'; exit 0\n    ;;\n");
    fs::write(&connection.executable, script).unwrap();
    connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
    connection
}

#[tokio::test]
async fn saved_capture_rejects_foreign_generations_during_render_and_recovery() {
    for command in [
        "driver.motion-canvas.render.status",
        "driver.motion-canvas.render.result",
        "driver.motion-canvas.composition.verify",
    ] {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let mut connection = saved_capture_connection(&temp, project.resource_key());
        let script = fs::read_to_string(&connection.executable).unwrap();
        let marker = r#""provider_generation":2"#;
        assert_eq!(script.matches(marker).count(), 1);
        let foreign = script.replace(marker, r#""provider_generation":3"#);
        fs::write(
            &connection.executable,
            format!(
                "#!/bin/sh\nif [ \"$7\" = '{command}' ]; then\n{foreign}\nexit $?\nfi\n{script}"
            ),
        )
        .unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();

        let owner = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let profile = project.deliverables[0].id;
        let result = owner
            .render_motion_canvas_segments(
                project.id,
                &project.stamp(),
                "foreign-generation",
                profile,
                &original_options(&project),
            )
            .await;
        let error = result.expect_err(command);
        assert!(!error.outcome_known, "{command}");
        assert_eq!(error.code, ErrorCode::StaleReference, "{command}");
        let receipts = service.production_receipts(project.id, 20).unwrap();
        assert_eq!(
            receipts
                .iter()
                .filter(|row| row.command == "driver.motion-canvas.render.start"
                    && row.stage == "dispatching")
                .count(),
            1
        );

        let observation = receipts.iter().find(|row| row.command == command).unwrap();
        assert_eq!(observation.stage, "outcome_unknown");
        assert_eq!(
            observation
                .payload
                .pointer("/result/execution/provenance/provider_generation")
                .and_then(Value::as_u64),
            Some(3)
        );

        drop(owner);
        drop(service);
        let store = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let reader = ProductionCoordinator::new(store, connection).unwrap();
        assert!(
            reader
                .saved_motion_capture_inventory(project.id, &project.stamp(), profile)
                .unwrap()
                .capture
                .is_none()
        );
        assert_eq!(reader.client.execute_calls.load(Ordering::SeqCst), 0);
    }
}

async fn completed_saved_capture_fixture() -> (
    StudioService,
    motionwright_domain::Project,
    tempfile::TempDir,
    ProductionConnection,
    MotionCanvasRenderEvidence,
    RootOperationBudget,
) {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let options = original_options(&project);
    let connection = saved_capture_connection(&temp, project.resource_key());
    let owner = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
    let evidence = owner
        .render_motion_canvas_segments(
            project.id,
            &project.stamp(),
            "saved-full",
            project.deliverables[0].id,
            &options,
        )
        .await
        .unwrap();
    let root_id = owner
        .retained_motion_continuation(
            &project,
            project.deliverables[0].id,
            Some(&options),
            &evidence,
        )
        .unwrap()
        .root_id;
    let root = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(root.spent.render_frames, 60);
    (service, project, temp, connection, evidence, root)
}

#[tokio::test]
async fn saved_capture_reopens_closed_store_without_redispatch_or_new_budget() {
    let (service, project, temp, connection, evidence, original_root) =
        completed_saved_capture_fixture().await;
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let profile = project.deliverables[0].id;
    drop(service); // All original Coordinators and Store Arcs are now dropped.
    let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let custody = ProductionReceiptCustody::default();
    let reader =
        ProductionCoordinator::with_receipt_custody(reopened.clone(), connection.clone(), &custody)
            .unwrap();
    assert!(
        reader
            .retained_motion_continuation(&project, profile, None, &evidence)
            .is_err()
    );
    let hint = reader
        .saved_motion_capture_inventory(project.id, &project.stamp(), profile)
        .unwrap()
        .capture
        .unwrap();
    assert_eq!(hint.segment_count, 1);
    assert_eq!(hint.frame_count, 60);
    let from_job = reader
        .saved_motion_capture_for_job(
            project.id,
            &project.stamp(),
            "saved-full:motion:0:render-start",
            &evidence.segments[0].job_ref,
        )
        .unwrap();
    assert_eq!(from_job.deliverable_id, profile);
    assert_eq!(from_job.capture.unwrap().receipt_id, hint.receipt_id);
    assert!(
        reader
            .saved_motion_capture_for_job(
                project.id,
                &project.stamp(),
                "saved-full:motion:0:render-start",
                "different-job",
            )
            .is_err()
    );
    let original = reader
        .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
        .unwrap();
    assert_eq!(json!(original.evidence()), json!(evidence));
    assert_eq!(original.options(), &original_options(&project));
    assert_eq!(original.capture_sha256(), hint.capture_sha256);
    assert_eq!(reader.client.execute_calls.load(Ordering::SeqCst), 0);
    drop(original);
    drop(reader);
    let delivery =
        ProductionCoordinator::with_receipt_custody(reopened.clone(), connection.clone(), &custody)
            .unwrap();
    let continuation = delivery
        .retained_motion_continuation(&project, profile, None, &evidence)
        .unwrap();
    assert_eq!(continuation.root_id, original_root.root_id);
    assert_eq!(continuation.mlt_route, Some(MltBudgetRoute::Direct));
    // Reopening again neither writes nor spends, and a different unverified
    // coordinator still cannot adopt caller-supplied evidence on its own.
    delivery
        .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
        .unwrap();
    let fresh = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    assert!(
        fresh
            .retained_motion_continuation(&project, profile, None, &evidence)
            .is_err()
    );
    assert_eq!(fresh.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(delivery.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        reopened
            .root_operation_budget(project.id, original_root.root_id)
            .unwrap(),
        original_root
    );
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(reopened.project(project.id).unwrap(), project);
}

#[tokio::test]
async fn saved_capture_recovery_rejects_changed_files_owner_and_source() {
    let (service, project, temp, connection, _, root) = completed_saved_capture_fixture().await;
    let profile = project.deliverables[0].id;
    let reader = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
    let hint = reader
        .saved_motion_capture_inventory(project.id, &project.stamp(), profile)
        .unwrap()
        .capture
        .unwrap();
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    // The LAST frame must be read, not just the manifest or first preview.
    let frame = connection
        .output_root
        .join("render-native-1/frames/000059.png");
    let bytes = fs::read(&frame).unwrap();
    let mut changed = bytes.clone();
    changed[0] ^= 1;
    fs::write(&frame, &changed).unwrap();
    assert!(
        reader
            .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
            .is_err()
    );
    assert!(reader.motion_budget_roots.lock().unwrap().is_empty());
    fs::write(&frame, &bytes).unwrap();
    let manifest = connection
        .output_root
        .join("render-native-1/artifact-manifest.json");
    let original_manifest = fs::read(&manifest).unwrap();
    fs::write(&manifest, b"{}").unwrap();
    assert!(
        reader
            .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
            .is_err()
    );
    fs::write(&manifest, &original_manifest).unwrap();
    assert!(
        reader
            .recover_saved_motion_capture(project.id, &project.stamp(), profile, Uuid::new_v4())
            .is_err()
    );
    let mut foreign = connection;
    foreign.output_root = temp.path().join("another-owner-output");
    fs::create_dir(&foreign.output_root).unwrap();
    let other = ProductionCoordinator::new(service.clone(), foreign).unwrap();
    assert!(
        other
            .saved_motion_capture_inventory(project.id, &project.stamp(), profile)
            .is_err()
    );
    assert!(
        other
            .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
            .is_err()
    );
    let changed = service
        .apply(
            project.id,
            &project.stamp(),
            "edit-after-saved-capture",
            &Change::SetSceneDuration {
                scene_id: project.scenes[0].id,
                duration: motionwright_domain::RationalTime::new(3, 1).unwrap(),
            },
        )
        .unwrap()
        .project;
    assert!(
        reader
            .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
            .is_err()
    );
    assert!(
        reader
            .saved_motion_capture_inventory(project.id, &changed.stamp(), profile)
            .unwrap()
            .capture
            .is_none()
    );
    assert!(reader.motion_budget_roots.lock().unwrap().is_empty());
    assert_eq!(reader.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(other.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        service
            .root_operation_budget(project.id, root.root_id)
            .unwrap(),
        root
    );
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
}

#[tokio::test]
async fn saved_capture_recovery_preserves_unknown_delivery_and_spent_capacity() {
    let (service, project, temp, connection, evidence, _) = completed_saved_capture_fixture().await;
    let profile = project.deliverables[0].id;
    let owner = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
    let hint = owner
        .saved_motion_capture_inventory(project.id, &project.stamp(), profile)
        .unwrap()
        .capture
        .unwrap();
    owner
        .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
        .unwrap();
    let audio_path = connection.output_root.join("synthetic-original.wav");
    fs::write(&audio_path, b"transport-only original audio").unwrap();
    let audio = MltAudioArtifact {
        relative_path: "synthetic-original.wav".into(),
        sha256: sha256_file(&audio_path).unwrap(),
        sample_rate: 48_000,
        channels: 2,
    };
    let error = owner
        .assemble_mlt_av_master(
            project.id,
            &project.stamp(),
            MltAvMasterRequest {
                request_id: "saved-capture-original-master",
                deliverable_id: profile,
                motion: &evidence,
                audio: &audio,
                sync: None,
            },
        )
        .await
        .unwrap_err();
    assert!(!error.outcome_known);
    assert_eq!(owner.client.execute_calls.load(Ordering::SeqCst), 1);
    let root_id = owner
        .retained_motion_continuation(&project, profile, None, &evidence)
        .unwrap()
        .root_id;
    let spent = service.root_operation_budget(project.id, root_id).unwrap();
    assert_eq!(spent.spent.render_frames, 120);
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    drop(owner);
    drop(service);
    let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let reader = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    let original = reader
        .recover_saved_motion_capture(project.id, &project.stamp(), profile, hint.receipt_id)
        .unwrap();
    assert_eq!(json!(original.evidence()), json!(evidence));
    assert!(
        reader
            .assemble_mlt_av_master(
                project.id,
                &project.stamp(),
                MltAvMasterRequest {
                    request_id: "renamed-after-restart",
                    deliverable_id: profile,
                    motion: original.evidence(),
                    audio: &audio,
                    sync: None,
                }
            )
            .await
            .is_err()
    );
    assert_eq!(reader.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        reopened.root_operation_budget(project.id, root_id).unwrap(),
        spent
    );
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(
        reopened
            .latest_production_receipt(
                project.id,
                "saved-capture-original-master:mlt:frames-encode"
            )
            .unwrap()
            .unwrap()
            .stage,
        "outcome_unknown"
    );
}

#[tokio::test]
async fn saved_capture_legacy_full_receipts_are_not_backfilled() {
    let (service, initial, temp) = fixture_service();
    let project = motion_canvas_fixture(&service, initial);
    let profile = project.deliverables[0].id;
    let options = original_options(&project);
    let connection = saved_capture_connection(&temp, project.resource_key());
    let owner = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
    let scoped = owner
        .with_motion_scene_boundary_scope(&project, profile, &options, &[project.scenes[0].id])
        .unwrap();
    let segments = plan_motion_canvas_segments(&project, profile, &options).unwrap();
    // Use the original producer without the new full-capture context. This
    // represents existing history; recovery must never add metadata to it.
    scoped
        .capture_motion_canvas_segment(
            project.id,
            &project.stamp(),
            "legacy-full:motion:0",
            0,
            &segments[0],
            motion_canvas_window::MotionCanvasCaptureSpec {
                end: segments[0].frame_count,
                scale: MotionCanvasWindowScale::Full,
                blend_admission: None,
            },
        )
        .await
        .unwrap();
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let selected = service
        .latest_production_receipt(project.id, "legacy-full:motion:0:verify")
        .unwrap()
        .unwrap();
    assert!(selected.payload.get("original_motion_reopen").is_none());
    drop(scoped);
    drop(owner);
    drop(service);
    let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let reader = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
    assert!(
        reader
            .saved_motion_capture_inventory(project.id, &project.stamp(), profile)
            .unwrap()
            .capture
            .is_none()
    );
    assert!(
        reader
            .recover_saved_motion_capture(project.id, &project.stamp(), profile, selected.id)
            .is_err()
    );
    assert_eq!(reader.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(
        reopened
            .latest_production_receipt(project.id, "legacy-full:motion:0:verify")
            .unwrap()
            .unwrap(),
        selected
    );
}
