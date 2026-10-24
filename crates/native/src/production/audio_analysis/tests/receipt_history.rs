//! Real SQLite receipt-history tests over explicitly synthetic observations.
//! No native CLI, renderer, meter, model, voice input or retry is executed.
use super::*;
use motionwright_service::StudioService;
use motionwright_storage::ProductionReceiptInput;

fn history_fixture() -> (tempfile::TempDir, StudioService, SourceBinding) {
    let root = tempfile::tempdir().unwrap();
    let service = StudioService::open(root.path().join("history.sqlite3")).unwrap();
    let project = service
        .create_project("Synthetic atomic audio history; no native execution")
        .unwrap();
    let mut source = source_fixture();
    source.project_id = project.id;
    source.project_resource = project.resource_key();
    source.generation = project.generation;
    source.revision = project.revision;
    (root, service, source)
}

fn append_observation(
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

fn all_observations(service: &StudioService, project_id: Uuid) -> Vec<ProductionReceipt> {
    let watermark = service.production_receipt_watermark(project_id).unwrap();
    let mut rows = Vec::new();
    let mut before = None;
    loop {
        let page = service
            .production_receipts_snapshot_page(project_id, &watermark, before, 256)
            .unwrap();
        if page.is_empty() {
            break;
        }
        before = page.last().map(|row| row.id);
        let full = page.len() == 256;
        rows.extend(page);
        if !full {
            break;
        }
    }
    assert_eq!(rows.len() as u64, watermark.count);
    assert_eq!(
        service.production_receipt_watermark(project_id).unwrap(),
        watermark
    );
    rows
}

fn read_atomic(
    service: &StudioService,
    source: &SourceBinding,
    request_id: &str,
) -> NativeResult<ProductionReceipt> {
    super::super::receipt_history::completed_audio_receipt(service, source, request_id)
}

fn assert_unknown_then_completion_is_refused(command: &str) {
    let (root, service, source) = history_fixture();
    let row = if command == MEASURE_COMMAND {
        measurement_receipt(&source.source_context())
    } else {
        receipt_fixture(
            &source,
            &format!(
                "{}{}",
                source.namespace().unwrap(),
                source.lane.mux_suffix()
            ),
            MUX_COMMAND,
            "5".repeat(64),
            json!({"synthetic": "history-only; not a media proof"}),
            "driver:mlt-video",
        )
    };
    append_observation(
        &service,
        &row,
        "outcome_unknown",
        json!({"error":{"code":"Timeout","outcome_known":false},"mutation":command==MUX_COMMAND}),
    );
    let completed = append_observation(&service, &row, "completed", row.payload.clone());
    assert_eq!(
        service
            .latest_production_receipt(source.project_id, &row.request_id)
            .unwrap()
            .unwrap()
            .id,
        completed.id,
    );
    let before = all_observations(&service, source.project_id);
    let before_project = service.project(source.project_id).unwrap();
    let reopened = StudioService::open(root.path().join("history.sqlite3")).unwrap();
    // Causal RED: the old latest-only reader accepts this exact completion.
    // GREEN: the original same-key UNKNOWN remains unresolved.
    let error = read_atomic(&reopened, &source, &row.request_id).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(!error.outcome_known);
    assert_eq!(all_observations(&reopened, source.project_id), before);
    assert_eq!(reopened.project(source.project_id).unwrap(), before_project);
}

#[test]
fn original_meter_unknown_is_not_reconciled_by_a_later_completed_row() {
    assert_unknown_then_completion_is_refused(MEASURE_COMMAND);
}

#[test]
fn original_atomic_mux_unknown_is_not_reconciled_by_a_later_completed_row() {
    assert_unknown_then_completion_is_refused(MUX_COMMAND);
}

#[test]
fn normal_dispatch_then_completion_keeps_original_receipt_and_measurement() {
    let (_root, service, source) = history_fixture();
    let context = source.source_context();
    let row = measurement_receipt(&context);
    append_observation(
        &service,
        &row,
        "dispatching",
        json!({"mutation":false,"job_ref":null,"synthetic":true}),
    );
    let original = append_observation(&service, &row, "completed", row.payload.clone());
    let before = all_observations(&service, source.project_id);
    let before_project = service.project(source.project_id).unwrap();
    let admitted = read_atomic(&service, &source, &row.request_id).unwrap();
    assert_eq!(admitted, original);
    let measured = proof_from_receipt(context, &admitted).unwrap();
    assert_eq!(measured.receipt.local_id, original.id);
    assert_eq!(measured.loudness.integrated_lufs_milli, Some(-23_000));
    assert_eq!(all_observations(&service, source.project_id), before);
    assert_eq!(service.project(source.project_id).unwrap(), before_project);
}

#[test]
fn known_failure_observation_gets_no_new_blanket_retry_or_reader_policy() {
    let (_root, service, source) = history_fixture();
    let context = source.source_context();
    let row = measurement_receipt(&context);
    append_observation(
        &service,
        &row,
        "failed_known",
        json!({"error":{"code":"Unavailable","outcome_known":true},"synthetic":true}),
    );
    let original = append_observation(&service, &row, "completed", row.payload.clone());
    let before = all_observations(&service, source.project_id);
    // This tests the unchanged reader policy for stored observations. It does
    // not dispatch, grant, or claim permission to retry any failed operation.
    let admitted = read_atomic(&service, &source, &row.request_id).unwrap();
    assert_eq!(admitted, original);
    proof_from_receipt(context, &admitted).unwrap();
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn original_render_result_after_status_unknown_does_not_poison_an_audio_key() {
    let (_root, service, source) = history_fixture();
    let job = "mc:synthetic-original-history-job";
    let start = receipt_fixture(
        &source,
        "synthetic-render-start",
        "driver.motion-canvas.render.start",
        "6".repeat(64),
        json!({"job_ref":job,"state":"queued"}),
        "driver:motion-canvas",
    );
    append_observation(&service, &start, "completed", start.payload.clone());
    let status = receipt_fixture(
        &source,
        "synthetic-status-timeout",
        "driver.motion-canvas.render.status",
        "7".repeat(64),
        json!({"job_ref":job}),
        "driver:motion-canvas",
    );
    append_observation(
        &service,
        &status,
        "outcome_unknown",
        json!({"job_ref":job,"error":{"code":"Timeout","outcome_known":false}}),
    );
    let mut result = receipt_fixture(
        &source,
        "synthetic-original-render-result",
        "driver.motion-canvas.render.result",
        "8".repeat(64),
        json!({"job_ref":job,"state":"succeeded","artifact":{"manifest":"synthetic-original-manifest"}}),
        "driver:motion-canvas",
    );
    result.payload["job_ref"] = json!(job);
    append_observation(&service, &result, "completed", result.payload.clone());
    let jobs = service.production_jobs(source.project_id, 10).unwrap();
    assert_eq!(jobs.len(), 1);
    let observed = serde_json::to_value(&jobs[0]).unwrap();
    assert_eq!(observed["state"], "succeeded");
    assert_eq!(observed["root_request_id"], "synthetic-render-start");
    assert_eq!(observed["result_available"], true);

    let context = source.source_context();
    let row = measurement_receipt(&context);
    let original = append_observation(&service, &row, "completed", row.payload.clone());
    let before = all_observations(&service, source.project_id);
    assert_eq!(
        read_atomic(&service, &source, &row.request_id).unwrap(),
        original
    );
    assert_eq!(all_observations(&service, source.project_id), before);
    assert_eq!(
        service.production_jobs(source.project_id, 10).unwrap(),
        jobs
    );
}

#[test]
fn unknown_older_than_the_latest_256_rows_is_still_not_a_completed_audio_proof() {
    let (root, service, source) = history_fixture();
    let row = measurement_receipt(&source.source_context());
    append_observation(
        &service,
        &row,
        "outcome_unknown",
        json!({"error":{"code":"Timeout","outcome_known":false},"mutation":false}),
    );
    for index in 0..260 {
        let noise = receipt_fixture(
            &source,
            &format!("synthetic-unrelated-observation-{index:03}"),
            MEASURE_COMMAND,
            "9".repeat(64),
            json!({"synthetic":"unrelated-no-media-proof"}),
            "driver:audio-analysis",
        );
        append_observation(&service, &noise, "completed", noise.payload.clone());
    }
    append_observation(&service, &row, "completed", row.payload.clone());
    let truncated = service.production_receipts(source.project_id, 256).unwrap();
    assert_eq!(truncated.len(), 256);
    assert!(truncated.iter().all(|item| item.stage != "outcome_unknown"));
    let before = all_observations(&service, source.project_id);
    assert_eq!(before.len(), 262);
    let reopened = StudioService::open(root.path().join("history.sqlite3")).unwrap();
    let error = read_atomic(&reopened, &source, &row.request_id).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(!error.outcome_known);
    assert_eq!(all_observations(&reopened, source.project_id), before);
}

#[test]
fn selected_uuid_reader_visits_beyond_one_page_without_guessing_latest_success() {
    let (_root, service, source) = history_fixture();
    let context = MeasurementContext {
        schema: "motionwright.audio-measurement-context/1",
        source: source.clone(),
        phase: MeasurementPhase::DecodedMltMaster,
        native_parent: Some(parent_fixture(&source, 96_128)),
    };
    let row = measurement_receipt(&context);
    let original = append_observation(&service, &row, "completed", row.payload.clone());
    for index in 0..260 {
        let mut noise = row.clone();
        noise.request_id = format!("unrelated-selection-{index}");
        append_observation(&service, &noise, "completed", noise.payload.clone());
    }
    let before = all_observations(&service, source.project_id);
    let selected = super::super::receipt_history::selected_decoded_measurement(
        &service,
        source.project_id,
        &source.stamp(),
        original.id,
    )
    .unwrap();
    assert_eq!(selected, original);
    assert!(
        super::super::receipt_history::selected_decoded_measurement(
            &service,
            source.project_id,
            &source.stamp(),
            Uuid::new_v4(),
        )
        .is_err()
    );
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn private_selector_disagreement_between_dispatch_and_terminal_is_refused() {
    for mode in ["absent", "different", "malformed"] {
        let (_root, service, source) = history_fixture();
        let context = source.source_context();
        let mut row = measurement_receipt(&context);
        let hint = AudioReceiptContext::from_context(&context)
            .unwrap()
            .payload();
        row.payload["audio_reopen"] = hint.clone();
        let mut dispatch = json!({"mutation":false,"job_ref":null});
        match mode {
            "absent" => {}
            "different" => {
                dispatch["audio_reopen"] = hint;
                dispatch["audio_reopen"]["caller_request_id"] = json!("different-original");
            }
            _ => dispatch["audio_reopen"] = Value::Null,
        }
        append_observation(&service, &row, "dispatching", dispatch);
        append_observation(&service, &row, "completed", row.payload.clone());
        let before = all_observations(&service, source.project_id);
        let error = read_atomic(&service, &source, &row.request_id).unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference, "{mode}");
        assert_eq!(all_observations(&service, source.project_id), before);
    }
}

#[test]
fn unresolved_unknown_keeps_its_certainty_even_when_selector_metadata_is_missing() {
    let (_root, service, source) = history_fixture();
    let context = source.source_context();
    let mut row = measurement_receipt(&context);
    row.payload["audio_reopen"] = AudioReceiptContext::from_context(&context)
        .unwrap()
        .payload();
    append_observation(
        &service,
        &row,
        "outcome_unknown",
        json!({"error":{"code":"Timeout","outcome_known":false},"mutation":false}),
    );
    append_observation(&service, &row, "completed", row.payload.clone());
    let before = all_observations(&service, source.project_id);
    let error = read_atomic(&service, &source, &row.request_id).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(!error.outcome_known);
    assert_eq!(all_observations(&service, source.project_id), before);
}

// Persisted observations are synthetic. No process/client is used.
// These regressions exercise the real bounded SQLite reader after reopening.
fn atomic_audio_observation(source: &SourceBinding, command: &str) -> ProductionReceipt {
    if command == MEASURE_COMMAND {
        measurement_receipt(&source.source_context())
    } else {
        assert_eq!(command, MUX_COMMAND);
        receipt_fixture(
            source,
            &format!(
                "{}{}",
                source.namespace().unwrap(),
                source.lane.mux_suffix()
            ),
            MUX_COMMAND,
            "5".repeat(64),
            json!({"synthetic":"F1 history-only; not a native media proof"}),
            "driver:mlt-video",
        )
    }
}

fn assert_noncompleted_audio_refusal(
    command: &str,
    latest_stage: &str,
    original_atomic_unknown: bool,
    unrelated_render_unknown: bool,
) {
    assert!(matches!(latest_stage, "failed_known" | "dispatching"));
    let (root, service, source) = history_fixture();
    let row = atomic_audio_observation(&source, command);
    let unknown = original_atomic_unknown.then(|| {
        append_observation(
            &service,
            &row,
            "outcome_unknown",
            json!({"error":{"code":"Timeout","outcome_known":false},"mutation":command==MUX_COMMAND,"synthetic":true}),
        )
    });
    if unrelated_render_unknown {
        let render_status = receipt_fixture(
            &source,
            "f1-unrelated-render-status",
            "driver.motion-canvas.render.status",
            "7".repeat(64),
            json!({"job_ref":"mc:f1-history-only-job"}),
            "driver:motion-canvas",
        );
        assert_ne!(render_status.request_id, row.request_id);
        append_observation(
            &service,
            &render_status,
            "outcome_unknown",
            json!({"job_ref":"mc:f1-history-only-job","error":{"code":"Timeout","outcome_known":false},"synthetic":true}),
        );
    }
    let payload = if latest_stage == "failed_known" {
        json!({"error":{"code":"Unavailable","outcome_known":true},"synthetic":true})
    } else {
        json!({"mutation":command==MUX_COMMAND,"job_ref":null,"synthetic":true})
    };
    let latest = append_observation(&service, &row, latest_stage, payload);
    assert_eq!(
        service
            .latest_production_receipt(source.project_id, &row.request_id)
            .unwrap()
            .unwrap(),
        latest,
    );
    let before = all_observations(&service, source.project_id);
    let before_project = service.project(source.project_id).unwrap();
    drop(service);
    let reopened = StudioService::open(root.path().join("history.sqlite3")).unwrap();
    let error = read_atomic(&reopened, &source, &row.request_id).unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict, "{command}/{latest_stage}");
    assert_eq!(
        error.outcome_known, !original_atomic_unknown,
        "{command}/{latest_stage}: a later row is not reconciliation of the original atomic UNKNOWN",
    );
    let after = all_observations(&reopened, source.project_id);
    assert_eq!(after, before);
    assert_eq!(reopened.project(source.project_id).unwrap(), before_project);
    if let Some(original_unknown) = unknown {
        assert!(after.contains(&original_unknown));
        assert_eq!(original_unknown.stage, "outcome_unknown");
    }
}

#[test]
fn original_meter_unknown_is_not_reconciled_by_a_later_failed_known_row() {
    assert_noncompleted_audio_refusal(MEASURE_COMMAND, "failed_known", true, false);
}

#[test]
fn original_meter_unknown_is_not_reconciled_by_a_later_dispatching_row() {
    assert_noncompleted_audio_refusal(MEASURE_COMMAND, "dispatching", true, false);
}

#[test]
fn original_atomic_mux_unknown_is_not_reconciled_by_a_later_failed_known_row() {
    assert_noncompleted_audio_refusal(MUX_COMMAND, "failed_known", true, false);
}

#[test]
fn original_atomic_mux_unknown_is_not_reconciled_by_a_later_dispatching_row() {
    assert_noncompleted_audio_refusal(MUX_COMMAND, "dispatching", true, false);
}

#[test]
fn latest_known_failure_without_unknown_keeps_a_known_refusal() {
    for command in [MEASURE_COMMAND, MUX_COMMAND] {
        assert_noncompleted_audio_refusal(command, "failed_known", false, false);
    }
}

#[test]
fn unrelated_render_status_unknown_does_not_poison_noncompleted_audio_refusal() {
    for command in [MEASURE_COMMAND, MUX_COMMAND] {
        for stage in ["failed_known", "dispatching"] {
            assert_noncompleted_audio_refusal(command, stage, false, true);
        }
    }
}

fn discovery_rows(
    service: &StudioService,
    source: &SourceBinding,
) -> (ProductionReceipt, ProductionReceipt) {
    let namespace = source.namespace().unwrap();
    let input = |request_id: String, command: &str, payload: Value| ProductionReceiptInput {
        project_id: source.project_id,
        generation: source.generation,
        revision: source.revision,
        request_id,
        request_sha256: "4".repeat(64),
        command: command.into(),
        stage: "completed".into(),
        payload,
    };
    let mux = service
        .append_production_receipt(input(
            format!("{}{}", namespace, source.lane.mux_suffix()),
            MUX_COMMAND,
            json!({"result":{"data":{"artifact":{
                "root":"output","path":"synthetic-master.mp4","sha256":"5".repeat(64),"bytes":24
            }}}}),
        ))
        .unwrap();
    let decoded = service
        .append_production_receipt(input(
            format!("{namespace}:measure-decoded"),
            MEASURE_COMMAND,
            json!({"audio_reopen":{
                "schema":"motionwright.audio-receipt-context/1",
                "caller_request_id":source.caller_request_id,
                "deliverable_id":source.deliverable_id,
                "lane":"single_segment","measurement_context_sha256":"6".repeat(64)
            }}),
        ))
        .unwrap();
    (mux, decoded)
}

fn discovery_source() -> (tempfile::TempDir, StudioService, SourceBinding) {
    let (root, service, mut source) = history_fixture();
    let project = service.project(source.project_id).unwrap();
    source.deliverable_id = project.deliverables[0].id;
    source.lane = AudioAnalysisLane::SingleSegment;
    (root, service, source)
}

fn discover(
    service: &StudioService,
    source: &SourceBinding,
) -> NativeResult<super::super::AudioQcReceiptInventory> {
    super::super::receipt_history::discovery_inventory(
        service,
        source.project_id,
        &RevisionStamp {
            resource: source.project_resource.clone(),
            generation: source.generation,
            revision: source.revision,
        },
        source.deliverable_id,
    )
}

#[test]
fn receipt_inventory_empty_is_not_completed_proof() {
    let (_root, service, source) = discovery_source();
    let inventory = discover(&service, &source).unwrap();
    assert!(inventory.items.is_empty());
    assert_eq!(
        (inventory.legacy_unbound_count, inventory.unavailable_count),
        (0, 0)
    );
    assert!(read_atomic(&service, &source, "missing").is_err());
    assert_eq!(
        service
            .production_receipt_watermark(source.project_id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn receipt_inventory_returns_only_public_hints_from_original_mux() {
    let (_root, service, source) = discovery_source();
    let (_, decoded) = discovery_rows(&service, &source);
    let before = all_observations(&service, source.project_id);
    let inventory = discover(&service, &source).unwrap();
    assert_eq!(inventory.items.len(), 1);
    assert_eq!(
        inventory.items[0].decoded_measurement_receipt_id,
        decoded.id
    );
    assert_eq!(inventory.items[0].master_sha256, "5".repeat(64));
    let public = serde_json::to_value(inventory).unwrap();
    assert_eq!(public["items"][0].as_object().unwrap().len(), 2);
    for private in [
        "caller_request_id",
        "audio_reopen",
        "connection_identity",
        "relative_path",
        "synthetic-master.mp4",
        "lane",
    ] {
        assert!(!public.to_string().contains(private), "{private}");
    }
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn receipt_inventory_distinguishes_legacy_absence_from_invalid_metadata() {
    let (_root, service, source) = discovery_source();
    let (_, decoded) = discovery_rows(&service, &source);
    append_observation(&service, &decoded, "completed", json!({"legacy":true}));
    let inventory = discover(&service, &source).unwrap();
    assert!(inventory.items.is_empty());
    assert_eq!(inventory.legacy_unbound_count, 1);
    append_observation(
        &service,
        &decoded,
        "completed",
        json!({"audio_reopen":null}),
    );
    let before = all_observations(&service, source.project_id);
    assert!(discover(&service, &source).is_err());
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn receipt_inventory_reads_candidates_before_the_latest_page() {
    let (_root, service, source) = discovery_source();
    let (_, decoded) = discovery_rows(&service, &source);
    for index in 0..270 {
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: source.project_id,
                generation: source.generation,
                revision: source.revision,
                request_id: format!("unrelated-history-{index}"),
                request_sha256: "4".repeat(64),
                command: "synthetic.unrelated.read".into(),
                stage: "completed".into(),
                payload: json!({}),
            })
            .unwrap();
    }
    let before = all_observations(&service, source.project_id);
    let inventory = discover(&service, &source).unwrap();
    assert_eq!(inventory.items.len(), 1);
    assert_eq!(
        inventory.items[0].decoded_measurement_receipt_id,
        decoded.id
    );
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn receipt_inventory_latest_noncompletion_never_selects_an_older_completion() {
    for parent in [false, true] {
        for stage in ["outcome_unknown", "failed_known", "dispatching"] {
            let (_root, service, source) = discovery_source();
            let (mux, decoded) = discovery_rows(&service, &source);
            let row = if parent { &mux } else { &decoded };
            append_observation(&service, row, stage, row.payload.clone());
            let before = all_observations(&service, source.project_id);
            let inventory = discover(&service, &source).unwrap();
            assert!(inventory.items.is_empty(), "{parent}/{stage}");
            assert_eq!(inventory.unavailable_count, 1);
            assert_eq!(all_observations(&service, source.project_id), before);
        }
    }
}

#[test]
fn receipt_inventory_rejects_malformed_recorded_master_identity() {
    let (_root, service, source) = discovery_source();
    let (mux, _) = discovery_rows(&service, &source);
    let mut payload = mux.payload.clone();
    payload["result"]["data"]["artifact"]["sha256"] = json!("not-a-digest");
    append_observation(&service, &mux, "completed", payload);
    let before = all_observations(&service, source.project_id);
    assert!(discover(&service, &source).is_err());
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn receipt_inventory_rejects_over_budget_results_instead_of_truncating() {
    let (_root, service, source) = discovery_source();
    for index in 0..129 {
        let mut next = source.clone();
        next.caller_request_id = format!("synthetic-discovery-{index}");
        discovery_rows(&service, &next);
    }
    let before = all_observations(&service, source.project_id);
    let error = discover(&service, &source).unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    assert_eq!(all_observations(&service, source.project_id), before);
}

#[test]
fn receipt_inventory_refuses_foreign_profile_and_stale_stamp() {
    let (_root, service, mut source) = discovery_source();
    discovery_rows(&service, &source);
    let before = all_observations(&service, source.project_id);
    source.deliverable_id = Uuid::new_v4();
    assert!(discover(&service, &source).is_err());
    source.deliverable_id = service.project(source.project_id).unwrap().deliverables[0].id;
    source.revision += 1;
    assert!(discover(&service, &source).is_err());
    assert_eq!(all_observations(&service, source.project_id), before);
}
