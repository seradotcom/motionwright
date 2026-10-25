//! Real SQLite/current-proof checks over synthetic receipts, with no native call.
use super::*;

#[test]
fn current_report_reopens_exact_state_without_appending_or_dispatching() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_completed_chain(&service, &project, &coordinator);
    let proof = coordinator
        .reopen_original_mix_audio_measurements(
            project.id,
            &project.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )
        .unwrap();
    let before_project = service.project(project.id).unwrap();
    let before_receipts = service.production_receipts(project.id, 256).unwrap();
    let report = proof.delivery_audio_qc_report(&coordinator).unwrap();
    let value = serde_json::to_value(report).unwrap();
    assert_eq!(value["schema"], "motionwright.delivery-audio-qc-report/2");
    assert_eq!(
        value["decoded_input"]["sample_interval"]["end_sample_frame_exclusive"],
        96_128
    );
    assert_eq!(service.project(project.id).unwrap(), before_project);
    assert_eq!(
        service.production_receipts(project.id, 256).unwrap(),
        before_receipts
    );
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn new_unknown_parent_refuses_report_and_preserves_original_unknown_receipt() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_completed_chain(&service, &project, &coordinator);
    let proof = coordinator
        .reopen_original_mix_audio_measurements(
            project.id,
            &project.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )
        .unwrap();
    let request_id = format!(
        "{}{}",
        binding.namespace().unwrap(),
        binding.lane.mux_suffix()
    );
    let parent = service
        .latest_production_receipt(project.id, &request_id)
        .unwrap()
        .unwrap();
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: parent.project_id,
            generation: parent.generation,
            revision: parent.revision,
            request_id: parent.request_id,
            request_sha256: parent.request_sha256,
            command: parent.command,
            stage: "outcome_unknown".into(),
            payload: json!({"error":"synthetic unresolved parent; must not dispatch"}),
        })
        .unwrap();
    let before = service.production_receipts(project.id, 256).unwrap();
    let error = proof.delivery_audio_qc_report(&coordinator).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(!error.outcome_known);
    assert_eq!(
        service.production_receipts(project.id, 256).unwrap(),
        before
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[derive(Debug, PartialEq)]
struct AudioReadSnapshot {
    project: Project,
    receipts: Vec<ProductionReceipt>,
    artifacts: std::collections::BTreeMap<std::path::PathBuf, (u64, String, std::time::SystemTime)>,
    native_calls: usize,
}

fn read_snapshot(coordinator: &ProductionCoordinator, project_id: Uuid) -> AudioReadSnapshot {
    let artifacts = fs::read_dir(&coordinator.client.connection().output_root)
        .unwrap()
        .map(|entry| {
            let entry = entry.unwrap();
            let metadata = entry.metadata().unwrap();
            assert!(metadata.is_file());
            (
                entry.path(),
                (
                    metadata.len(),
                    hex::encode(Sha256::digest(fs::read(entry.path()).unwrap())),
                    metadata.modified().unwrap(),
                ),
            )
        })
        .collect();
    assert!(
        coordinator
            .service
            .production_receipt_watermark(project_id)
            .unwrap()
            .count
            <= 256
    );
    AudioReadSnapshot {
        project: coordinator.service.project(project_id).unwrap(),
        receipts: coordinator
            .service
            .production_receipts(project_id, 256)
            .unwrap(),
        artifacts,
        native_calls: coordinator.client.execute_calls.load(Ordering::SeqCst),
    }
}

fn stored_hints(coordinator: &ProductionCoordinator, binding: &SourceBinding) -> (Uuid, String) {
    let request_id = format!("{}:measure-decoded", binding.namespace().unwrap());
    let receipt = coordinator
        .service
        .latest_production_receipt(binding.project_id, &request_id)
        .unwrap()
        .unwrap();
    let bytes = fs::read(
        coordinator
            .client
            .connection()
            .output_root
            .join(expected_master_path(binding).unwrap()),
    )
    .unwrap();
    (receipt.id, hex::encode(Sha256::digest(bytes)))
}

fn recover(
    coordinator: &ProductionCoordinator,
    project: &Project,
    receipt_id: Uuid,
    master_sha256: &str,
) -> NativeResult<VerifiedOriginalMixAudioMeasurements> {
    coordinator.recover_original_mix_audio_measurements_from_receipt(
        project.id,
        &project.stamp(),
        receipt_id,
        project.deliverables[0].id,
        master_sha256,
    )
}

#[test]
fn restart_recovers_exact_public_qc_from_receipt_hint_without_retaining_private_selector() {
    let (root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let original = coordinator
        .reopen_original_mix_audio_measurements(
            project.id,
            &project.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )
        .unwrap();
    let report =
        serde_json::to_value(original.delivery_audio_qc_report(&coordinator).unwrap()).unwrap();
    let receipt_id: Uuid =
        serde_json::from_value(report["qc"]["measurement_receipt_id"].clone()).unwrap();
    let master_sha256 = report["master_artifact_sha256"]
        .as_str()
        .unwrap()
        .to_owned();
    let before = read_snapshot(&coordinator, project.id);
    assert_eq!(before.receipts.len(), 6);
    assert_eq!(before.native_calls, 0);
    let connection = coordinator.client.connection().clone();
    drop(original);
    drop(binding);
    drop(coordinator);
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let current = ProductionCoordinator::new(reopened, connection).unwrap();
    // Only existing public hints cross this boundary: no request ID, lane,
    // MeasurementContext, old proof, runtime token or second registry is passed.
    let recovered = recover(&current, &project, receipt_id, &master_sha256).unwrap();
    let after_report =
        serde_json::to_value(recovered.delivery_audio_qc_report(&current).unwrap()).unwrap();
    assert_eq!(after_report, report);
    assert_eq!(
        after_report["decoded_input"]["sample_interval"]["end_sample_frame_exclusive"],
        96_128
    );
    let public = serde_json::to_string(&after_report).unwrap();
    for private in [
        "caller_request_id",
        "audio_reopen",
        "connection_identity",
        "relative_path",
    ] {
        assert!(!public.contains(private), "{private}");
    }
    assert_eq!(read_snapshot(&current, project.id), before);
}

#[test]
fn historical_missing_selector_is_unavailable_without_backfill_and_known_reopen_still_works() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_completed_chain(&service, &project, &coordinator);
    let (receipt_id, master) = stored_hints(&coordinator, &binding);
    let before = read_snapshot(&coordinator, project.id);
    let error = recover(&coordinator, &project, receipt_id, &master).unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    let known = coordinator
        .reopen_original_mix_audio_measurements(
            project.id,
            &project.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )
        .unwrap();
    known.delivery_audio_qc_report(&coordinator).unwrap();
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[test]
fn selected_receipt_profile_master_and_stamp_hints_never_substitute_for_authority() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let (receipt_id, master) = stored_hints(&coordinator, &binding);
    let proof = recover(&coordinator, &project, receipt_id, &master).unwrap();
    let parent_id = proof
        .decoded
        .context
        .native_parent
        .as_ref()
        .unwrap()
        .receipt_id;
    let dispatch_id = service
        .production_receipts(project.id, 256)
        .unwrap()
        .iter()
        .find(|row| {
            row.request_id == proof.decoded.receipt.request_id && row.stage == "dispatching"
        })
        .unwrap()
        .id;
    let mut wrong_generation = project.stamp();
    wrong_generation.generation = Uuid::new_v4();
    let mut wrong_revision = project.stamp();
    wrong_revision.revision += 1;
    let mut wrong_resource = project.stamp();
    wrong_resource.resource = format!("project:{}", Uuid::new_v4());
    let cases = [
        (
            "absent UUID",
            Uuid::new_v4(),
            binding.deliverable_id,
            master.clone(),
            project.stamp(),
        ),
        (
            "source phase",
            proof.source.receipt.local_id,
            binding.deliverable_id,
            master.clone(),
            project.stamp(),
        ),
        (
            "mux command",
            parent_id,
            binding.deliverable_id,
            master.clone(),
            project.stamp(),
        ),
        (
            "dispatch row",
            dispatch_id,
            binding.deliverable_id,
            master.clone(),
            project.stamp(),
        ),
        (
            "profile",
            receipt_id,
            Uuid::new_v4(),
            master.clone(),
            project.stamp(),
        ),
        (
            "master",
            receipt_id,
            binding.deliverable_id,
            "0".repeat(64),
            project.stamp(),
        ),
        (
            "decoded hash is not master",
            receipt_id,
            binding.deliverable_id,
            proof.decoded.context.input().unwrap().0.into(),
            project.stamp(),
        ),
        (
            "invalid hash",
            receipt_id,
            binding.deliverable_id,
            "bad".into(),
            project.stamp(),
        ),
        (
            "generation",
            receipt_id,
            binding.deliverable_id,
            master.clone(),
            wrong_generation,
        ),
        (
            "revision",
            receipt_id,
            binding.deliverable_id,
            master.clone(),
            wrong_revision,
        ),
        (
            "resource",
            receipt_id,
            binding.deliverable_id,
            master,
            wrong_resource,
        ),
    ];
    let before = read_snapshot(&coordinator, project.id);
    for (case, id, profile, master_sha256, stamp) in cases {
        assert!(
            coordinator
                .recover_original_mix_audio_measurements_from_receipt(
                    project.id,
                    &stamp,
                    id,
                    profile,
                    &master_sha256,
                )
                .is_err(),
            "{case}"
        );
        assert_eq!(read_snapshot(&coordinator, project.id), before, "{case}");
    }
}

#[test]
fn consistently_wrong_private_metadata_cannot_mint_a_current_measurement() {
    for case in [
        "null",
        "extra",
        "schema",
        "caller",
        "profile",
        "lane",
        "digest",
        "source_context",
    ] {
        let (_root, service, project, coordinator) = fixture();
        let binding = seed_chain_with_metadata(&service, &project, &coordinator, |context| {
            let mut value = AudioReceiptContext::from_context(context)
                .unwrap()
                .payload();
            let alter = if case == "source_context" {
                context.phase != MeasurementPhase::DecodedMltMaster
            } else {
                context.phase == MeasurementPhase::DecodedMltMaster
            };
            if alter {
                match case {
                    "null" => value = Value::Null,
                    "extra" => value["extra"] = json!("unreviewed"),
                    "schema" => value["schema"] = json!("motionwright.audio-receipt-context/99"),
                    "caller" => value["caller_request_id"] = json!("different-original-request"),
                    "profile" => value["deliverable_id"] = json!(Uuid::new_v4()),
                    "lane" => value["lane"] = json!("multisegment"),
                    _ => value["measurement_context_sha256"] = json!("0".repeat(64)),
                }
            }
            Some(value)
        });
        let (receipt_id, master) = stored_hints(&coordinator, &binding);
        let before = read_snapshot(&coordinator, project.id);
        assert!(
            recover(&coordinator, &project, receipt_id, &master).is_err(),
            "{case}"
        );
        assert_eq!(read_snapshot(&coordinator, project.id), before, "{case}");
    }
}

#[test]
fn an_original_source_without_new_metadata_remains_compatible_with_a_recoverable_decoded_receipt() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_chain_with_metadata(&service, &project, &coordinator, |context| {
        (context.phase == MeasurementPhase::DecodedMltMaster).then(|| {
            AudioReceiptContext::from_context(context)
                .unwrap()
                .payload()
        })
    });
    let (receipt_id, master) = stored_hints(&coordinator, &binding);
    let before = read_snapshot(&coordinator, project.id);
    recover(&coordinator, &project, receipt_id, &master)
        .unwrap()
        .delivery_audio_qc_report(&coordinator)
        .unwrap();
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

fn append_decoded_observation(
    service: &StudioService,
    original: &ProductionReceipt,
    stage: &str,
    payload: Value,
) -> ProductionReceipt {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: original.project_id,
            generation: original.generation,
            revision: original.revision,
            request_id: original.request_id.clone(),
            request_sha256: original.request_sha256.clone(),
            command: original.command.clone(),
            stage: stage.into(),
            payload,
        })
        .unwrap()
}

#[test]
fn later_completed_uuid_is_not_a_substitute_for_the_original_selected_receipt() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let (receipt_id, master) = stored_hints(&coordinator, &binding);
    let row = service
        .latest_production_receipt(
            project.id,
            &format!("{}:measure-decoded", binding.namespace().unwrap()),
        )
        .unwrap()
        .unwrap();
    let later = append_decoded_observation(&service, &row, "completed", row.payload.clone());
    assert_ne!(later.id, receipt_id);
    let before = read_snapshot(&coordinator, project.id);
    assert!(recover(&coordinator, &project, receipt_id, &master).is_err());
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[test]
fn recovery_preserves_historical_unknown_even_after_a_later_completed_row() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let (_, master) = stored_hints(&coordinator, &binding);
    let row = service
        .latest_production_receipt(
            project.id,
            &format!("{}:measure-decoded", binding.namespace().unwrap()),
        )
        .unwrap()
        .unwrap();
    let unknown = append_decoded_observation(
        &service,
        &row,
        "outcome_unknown",
        json!({"error":{"code":"Timeout","outcome_known":false}, "mutation":false,
            "audio_reopen":row.payload["audio_reopen"]}),
    );
    let later = append_decoded_observation(&service, &row, "completed", row.payload.clone());
    let before = read_snapshot(&coordinator, project.id);
    let error = recover(&coordinator, &project, later.id, &master).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(!error.outcome_known);
    assert!(before.receipts.iter().any(|receipt| receipt == &unknown));
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[test]
fn changed_master_decoded_and_staged_bytes_cannot_be_recovered_from_an_old_hint() {
    for case in ["master", "decoded", "staged"] {
        let (_root, service, project, coordinator) = fixture();
        let binding = seed_recoverable_chain(&service, &project, &coordinator);
        let (receipt_id, master) = stored_hints(&coordinator, &binding);
        let proof = recover(&coordinator, &project, receipt_id, &master).unwrap();
        let parent = proof.decoded.context.native_parent.as_ref().unwrap();
        let relative = match case {
            "master" => parent.master.relative_path.clone(),
            "decoded" => parent.decoded.relative_path.clone(),
            _ => proof.decoded.context.file_name().unwrap(),
        };
        fs::write(
            coordinator.client.connection().output_root.join(relative),
            b"changed fixture",
        )
        .unwrap();
        let before = read_snapshot(&coordinator, project.id);
        assert!(
            recover(&coordinator, &project, receipt_id, &master).is_err(),
            "{case}"
        );
        assert_eq!(read_snapshot(&coordinator, project.id), before, "{case}");
    }
}

#[test]
fn current_profile_edit_and_foreign_project_cannot_reauthorize_old_receipt_hints() {
    let (_root, service, mut project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let (receipt_id, master) = stored_hints(&coordinator, &binding);
    let mut profile = project.deliverables[0].clone();
    profile.width += 2;
    apply(
        &service,
        &mut project,
        "changed-delivery-profile",
        Change::UpsertDeliverable { profile },
    );
    let foreign = service
        .create_project("Foreign read hint; no native call")
        .unwrap();
    let before = read_snapshot(&coordinator, project.id);
    let foreign_before = read_snapshot(&coordinator, foreign.id);
    assert!(recover(&coordinator, &project, receipt_id, &master).is_err());
    assert!(
        coordinator
            .recover_original_mix_audio_measurements_from_receipt(
                foreign.id,
                &foreign.stamp(),
                receipt_id,
                binding.deliverable_id,
                &master,
            )
            .is_err()
    );
    assert_eq!(read_snapshot(&coordinator, project.id), before);
    assert_eq!(read_snapshot(&coordinator, foreign.id), foreign_before);
}

#[tokio::test]
async fn legacy_completed_replay_does_not_backfill_selector_metadata_or_dispatch() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_completed_chain(&service, &project, &coordinator);
    let context = binding.source_context();
    let before = read_snapshot(&coordinator, project.id);
    let result = coordinator
        .execute_with_receipt_context(
            project.id,
            &project.stamp(),
            &context.request_id().unwrap(),
            (MEASURE_COMMAND, false),
            context.args().unwrap(),
            Some(AudioReceiptContext::from_context(&context).unwrap()),
        )
        .await
        .unwrap();
    assert_eq!(result["replayed"], true);
    assert!(result["receipt"]["payload"].get("audio_reopen").is_none());
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[tokio::test]
async fn shared_execute_refuses_changed_private_context_before_any_receipt_or_native_effect() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let context = binding.source_context();
    let mut hint = AudioReceiptContext::from_context(&context).unwrap();
    hint.caller_request_id = "contradictory-original".into();
    let before = read_snapshot(&coordinator, project.id);
    let error = coordinator
        .execute_with_receipt_context(
            project.id,
            &project.stamp(),
            &context.request_id().unwrap(),
            (MEASURE_COMMAND, false),
            context.args().unwrap(),
            Some(hint),
        )
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[tokio::test]
async fn private_audio_metadata_cannot_attach_to_a_mutation_or_another_command() {
    let (_root, _service, project, coordinator) = fixture();
    let context = source_fixture().source_context();
    let before = read_snapshot(&coordinator, project.id);
    for operation in [(MEASURE_COMMAND, true), (MUX_COMMAND, false)] {
        assert!(
            coordinator
                .execute_with_receipt_context(
                    project.id,
                    &project.stamp(),
                    "closed-context-only",
                    operation,
                    json!({}),
                    Some(AudioReceiptContext::from_context(&context).unwrap()),
                )
                .await
                .is_err()
        );
        assert_eq!(read_snapshot(&coordinator, project.id), before);
    }
}

#[test]
fn missing_or_malformed_metadata_cannot_hide_an_original_unknown() {
    for case in ["legacy", "malformed", "latest_unknown"] {
        let (_root, service, project, coordinator) = fixture();
        let binding = if case == "legacy" {
            seed_completed_chain(&service, &project, &coordinator)
        } else {
            seed_recoverable_chain(&service, &project, &coordinator)
        };
        let (original_id, master) = stored_hints(&coordinator, &binding);
        let row = service
            .latest_production_receipt(
                project.id,
                &format!("{}:measure-decoded", binding.namespace().unwrap()),
            )
            .unwrap()
            .unwrap();
        let unknown = append_decoded_observation(
            &service,
            &row,
            "outcome_unknown",
            json!({"error":{"code":"Timeout","outcome_known":false},"mutation":false}),
        );
        let selected_id = if case == "latest_unknown" {
            original_id
        } else {
            let mut payload = row.payload.clone();
            if case == "malformed" {
                payload["audio_reopen"] = Value::Null;
            }
            append_decoded_observation(&service, &row, "completed", payload).id
        };
        let before = read_snapshot(&coordinator, project.id);
        let error = recover(&coordinator, &project, selected_id, &master).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{case}");
        assert!(!error.outcome_known, "{case}");
        assert!(before.receipts.iter().any(|receipt| receipt == &unknown));
        assert_eq!(read_snapshot(&coordinator, project.id), before, "{case}");
    }
}

#[test]
fn latest_unknown_in_source_or_mux_keeps_original_uncertainty_during_recovery() {
    for phase in ["source", "mux"] {
        let (_root, service, project, coordinator) = fixture();
        let binding = seed_recoverable_chain(&service, &project, &coordinator);
        let (receipt_id, master) = stored_hints(&coordinator, &binding);
        let proof = recover(&coordinator, &project, receipt_id, &master).unwrap();
        let request_id = if phase == "source" {
            &proof.source.receipt.request_id
        } else {
            &proof
                .decoded
                .context
                .native_parent
                .as_ref()
                .unwrap()
                .request_id
        };
        let original = service
            .latest_production_receipt(project.id, request_id)
            .unwrap()
            .unwrap();
        let unknown = append_decoded_observation(
            &service,
            &original,
            "outcome_unknown",
            json!({"error":{"code":"Timeout","outcome_known":false},"mutation":phase=="mux"}),
        );
        let before = read_snapshot(&coordinator, project.id);
        let error = recover(&coordinator, &project, receipt_id, &master).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{phase}");
        assert!(!error.outcome_known, "{phase}");
        assert!(before.receipts.iter().any(|receipt| receipt == &unknown));
        assert_eq!(read_snapshot(&coordinator, project.id), before, "{phase}");
    }
}

#[test]
fn multisegment_receipt_hint_recovers_the_same_current_qc_without_dispatch() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_chain_for_lane(
        &service,
        &project,
        &coordinator,
        AudioAnalysisLane::Multisegment,
        |context| {
            Some(
                AudioReceiptContext::from_context(context)
                    .unwrap()
                    .payload(),
            )
        },
    );
    let (receipt_id, master) = stored_hints(&coordinator, &binding);
    let before = read_snapshot(&coordinator, project.id);
    let proof = recover(&coordinator, &project, receipt_id, &master).unwrap();
    assert_eq!(
        proof.decoded.context.source.lane,
        AudioAnalysisLane::Multisegment
    );
    let report =
        serde_json::to_value(proof.delivery_audio_qc_report(&coordinator).unwrap()).unwrap();
    assert_eq!(report["master_artifact_sha256"], master);
    assert_eq!(
        report["qc"]["measurement_receipt_id"],
        receipt_id.to_string()
    );
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[tokio::test]
async fn fresh_meter_fixture_preserves_private_context_in_the_same_dispatch_and_terminal_receipts()
{
    use std::os::unix::fs::PermissionsExt;
    for stage in ["completed", "failed_known", "outcome_unknown"] {
        let (root, service, project, initial) = fixture();
        let mut connection = initial.client.connection().clone();
        drop(initial);
        let response_file = root.path().join("synthetic-meter-envelope.json");
        let args_file = root.path().join("synthetic-meter-observed-args.json");
        // G0 transport stub only. It prints a fixture; no meter, Broker,
        // renderer or codec exists in this process. Each case uses a new DB.
        let response_path = response_file.to_string_lossy().replace('\'', "'\\''");
        let args_path = args_file.to_string_lossy().replace('\'', "'\\''");
        let script = format!(
            "#!/bin/sh\n[ \"$7\" = 'driver.audio-analysis.artifact.measure' ] || exit 2\nprintf '%s' \"$9\" > '{}'\ncat '{}'\n",
            args_path, response_path,
        );
        fs::write(&connection.executable, script).unwrap();
        for (path, mode) in [
            (&connection.executable, 0o700),
            (&connection.session_file, 0o600),
            (&connection.socket, 0o600),
            (&connection.output_root, 0o700),
        ] {
            fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
        }
        connection.executable_sha256 =
            hex::encode(Sha256::digest(fs::read(&connection.executable).unwrap()));
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let (_, mix, source) = coordinator
            .current_audio_source(
                project.id,
                &project.stamp(),
                "fresh-synthetic-meter-receipts",
                project.deliverables[0].id,
                AudioAnalysisLane::SingleSegment,
            )
            .unwrap();
        let context = source.source_context();
        let fixture = measurement_receipt(&context);
        let error = match stage {
            "completed" => None,
            "failed_known" => Some(Error::new(
                ErrorCode::Unavailable,
                "Synthetic known meter failure",
            )),
            _ => {
                Some(Error::new(ErrorCode::Timeout, "Synthetic unknown meter outcome").uncertain())
            }
        };
        let envelope = json!({
            "ok":error.is_none(),
            "request_id":"synthetic-meter-transport-receipt",
            "command":MEASURE_COMMAND,
            "data":if error.is_none() { fixture.payload["result"]["data"].clone() } else { Value::Null },
            "error":error,
            "execution":fixture.payload["result"]["execution"],
            "warnings":[],
        });
        fs::write(&response_file, serde_json::to_vec(&envelope).unwrap()).unwrap();
        let request_id = context.request_id().unwrap();
        let args = context.args().unwrap();
        let expected_digest = request_digest(MEASURE_COMMAND, &args).unwrap();
        let metadata = AudioReceiptContext::from_context(&context)
            .unwrap()
            .payload();
        let outcome = coordinator.measure_context(context, mix.wav_bytes()).await;
        match stage {
            "completed" => {
                outcome.unwrap();
            }
            "failed_known" => {
                let error = outcome.unwrap_err();
                assert_eq!(error.code, ErrorCode::Unavailable);
                assert!(error.outcome_known);
            }
            _ => {
                let error = outcome.unwrap_err();
                assert_eq!(error.code, ErrorCode::Timeout);
                assert!(!error.outcome_known);
            }
        }
        let observed_args: Value = serde_json::from_slice(&fs::read(&args_file).unwrap()).unwrap();
        assert_eq!(observed_args, args);
        assert!(observed_args.get("audio_reopen").is_none());
        let receipts = service.production_receipts(project.id, 256).unwrap();
        assert_eq!(receipts.len(), 2, "{stage}");
        assert_eq!(
            receipts
                .iter()
                .filter(|row| row.stage == "dispatching")
                .count(),
            1
        );
        assert_eq!(receipts.iter().filter(|row| row.stage == stage).count(), 1);
        assert!(
            receipts.iter().all(|row| {
                row.request_id == request_id
                    && row.command == MEASURE_COMMAND
                    && row.request_sha256 == expected_digest
                    && row.payload.get("audio_reopen") == Some(&metadata)
            }),
            "{stage}"
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        let independent = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        assert_eq!(
            independent.production_receipts(project.id, 256).unwrap(),
            receipts
        );
    }
}

#[test]
fn durable_inventory_after_restart_recovers_without_a_session_selector() {
    let (root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let (receipt_id, master_sha256) = stored_hints(&coordinator, &binding);
    let before = read_snapshot(&coordinator, project.id);
    let connection = coordinator.client.connection().clone();
    drop(binding);
    drop(coordinator);
    drop(service);
    let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let current = ProductionCoordinator::new(service, connection).unwrap();
    // No previous proof, request, lane, registry token or browser state survives.
    let inventory = current
        .original_mix_audio_qc_receipt_inventory(
            project.id,
            &project.stamp(),
            project.deliverables[0].id,
        )
        .unwrap();
    assert_eq!(inventory.items.len(), 1);
    assert_eq!(
        inventory.items[0].decoded_measurement_receipt_id,
        receipt_id
    );
    assert_eq!(inventory.items[0].master_sha256, master_sha256);
    let proof = recover(
        &current,
        &project,
        inventory.items[0].decoded_measurement_receipt_id,
        &inventory.items[0].master_sha256,
    )
    .unwrap();
    let report = serde_json::to_value(proof.delivery_audio_qc_report(&current).unwrap()).unwrap();
    assert_eq!(report["master_artifact_sha256"], master_sha256);
    assert_eq!(
        report["qc"]["measurement_receipt_id"],
        receipt_id.to_string()
    );
    assert_eq!(
        report["decoded_input"]["sample_interval"]["end_sample_frame_exclusive"],
        96_128
    );
    assert_eq!(read_snapshot(&current, project.id), before);
}

#[test]
fn inventory_hint_never_reconciles_a_historical_unknown() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let key = format!("{}:measure-decoded", binding.namespace().unwrap());
    let original = service
        .latest_production_receipt(project.id, &key)
        .unwrap()
        .unwrap();
    for stage in ["outcome_unknown", "completed"] {
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: original.project_id,
                generation: original.generation,
                revision: original.revision,
                request_id: original.request_id.clone(),
                request_sha256: original.request_sha256.clone(),
                command: original.command.clone(),
                stage: stage.into(),
                payload: original.payload.clone(),
            })
            .unwrap();
    }
    let before = read_snapshot(&coordinator, project.id);
    let inventory = coordinator
        .original_mix_audio_qc_receipt_inventory(
            project.id,
            &project.stamp(),
            project.deliverables[0].id,
        )
        .unwrap();
    assert_eq!(
        inventory.items.len(),
        1,
        "discovery makes no admission claim"
    );
    let hint = &inventory.items[0];
    let error = recover(
        &coordinator,
        &project,
        hint.decoded_measurement_receipt_id,
        &hint.master_sha256,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(!error.outcome_known);
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}

#[test]
fn incomplete_decoded_measurement_after_mux_remains_unavailable_without_retry() {
    let (_root, service, project, coordinator) = fixture();
    let binding = seed_recoverable_chain(&service, &project, &coordinator);
    let key = format!("{}:measure-decoded", binding.namespace().unwrap());
    let original = service
        .latest_production_receipt(project.id, &key)
        .unwrap()
        .unwrap();
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: original.project_id,
            generation: original.generation,
            revision: original.revision,
            request_id: original.request_id,
            request_sha256: original.request_sha256,
            command: original.command,
            stage: "outcome_unknown".into(),
            payload: original.payload,
        })
        .unwrap();
    let before = read_snapshot(&coordinator, project.id);
    let inventory = coordinator
        .original_mix_audio_qc_receipt_inventory(
            project.id,
            &project.stamp(),
            project.deliverables[0].id,
        )
        .unwrap();
    assert!(inventory.items.is_empty());
    assert_eq!(inventory.unavailable_count, 1);
    assert_eq!(read_snapshot(&coordinator, project.id), before);
}
