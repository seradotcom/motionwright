use super::super::tests::{fake_transport_connection, transport_reply};
use super::*;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use motionwright_service::{StudioService, VoiceImportMetadata};
use motionwright_storage::{ProductionReceipt, ProductionReceiptInput};
use tempfile::TempDir;

struct BackendFixture {
    coordinator: ProductionCoordinator,
    project: Project,
    temp: TempDir,
    prepared: PreparedAlignment,
    admission: ObservedAlignmentAdmission,
    receipt: ProductionReceipt,
    counter: PathBuf,
}
fn fixture_blob(bytes: &[u8], name: Option<&str>) -> Value {
    let mut v = json!({"encoding":"base64","data":STANDARD.encode(bytes),"sha256":evidence::digest(bytes),"size_bytes":bytes.len()});
    if let Some(name) = name {
        v["name"] = json!(name);
    }
    v
}
fn refresh_file(e: &mut Value, object: &str, file: &str, summary: &str, name: &str) {
    let mut bytes = serde_json::to_vec(&e[object]).unwrap();
    bytes.push(b'\n');
    e[file] = fixture_blob(&bytes, Some(name));
    e["worker_summary"][summary] = json!(evidence::digest(&bytes));
}
fn backend_fixture() -> BackendFixture {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
    let project = service
        .create_project("Synthetic qualified receiver fixture")
        .unwrap();
    let wav = include_bytes!("fixtures/source.wav");
    let wav_path = temp.path().join("synthetic.wav");
    fs::write(&wav_path, wav).unwrap();
    let project = service
        .import_voice_file(
            project.id,
            &RevisionStamp::from(&project),
            "synthetic-voice-import",
            &wav_path,
            VoiceImportMetadata {
                name: "Synthetic PCM fixture".into(),
                media_type: "audio/wav".into(),
                label: "Not recorded speech".into(),
            },
        )
        .unwrap()
        .project;
    let track = project.audio.voice_tracks[0].clone();
    assert_eq!(
        project.audio.active_voice_track_id,
        Some(track.id),
        "first voice import must be selected for this fixture"
    );
    let output =
        serde_json::to_string(&transport_reply("driver.motion-canvas.composition.inspect"))
            .unwrap();
    let (connection, counter) =
        fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
    let coordinator = ProductionCoordinator::new(service, connection).unwrap();
    let source = evidence::inspect_source(wav).unwrap();
    assert_eq!(source.sha256, track.source_sha256);
    let mut request: Value =
        serde_json::from_slice(include_bytes!("fixtures/request.json")).unwrap();
    request["project_id"] = json!(project.id);
    request["project_generation"] = json!(project.generation);
    request["project_revision"] = json!(project.revision);
    request["voice_track_id"] = json!(track.id);
    request["asset_id"] = json!(track.asset_id);
    let request_id = Uuid::parse_str(request["request_id"].as_str().unwrap()).unwrap();
    let intent = VoiceAlignmentIntent {
        request_id,
        voice_track_id: track.id,
        transcript_version: 1,
        transcript_text: "A B".into(),
    };
    let request_sha = evidence::digest(&evidence::canonical_request(&request).unwrap());
    let mut envelope: Value =
        serde_json::from_slice(include_bytes!("fixtures/high.envelope.json")).unwrap();
    envelope["request_sha256"] = json!(request_sha);
    envelope["candidate"]["request_sha256"] = json!(request_sha);
    envelope["execution"]["request_sha256"] = json!(request_sha);
    envelope["validation"]["request_sha256"] = json!(request_sha);
    envelope["worker_summary"]["request_sha256"] = json!(request_sha);
    envelope["worker_summary"]["request_id"] = json!(request_id);
    envelope["candidate"]["binding"] = json!({"request_id":request_id,"project_id":project.id,"project_generation":project.generation,
  "project_revision":project.revision,"voice_track_id":track.id,"asset_id":track.asset_id,"source_commit":request["source_commit"],
  "source_sha256":source.sha256,"transcript_version":1,"transcript_sha256":request["transcript"]["sha256"]});
    refresh_file(
        &mut envelope,
        "candidate",
        "candidate_file",
        "result_sha256",
        "result.json",
    );
    refresh_file(
        &mut envelope,
        "execution",
        "execution_file",
        "execution_sha256",
        "execution.json",
    );
    evidence::validate(&request, &source, &envelope).unwrap();
    let mut stdout = serde_json::to_vec(&envelope).unwrap();
    stdout.push(b'\n');
    let data = json!({"runtime_output":fixture_blob(&stdout,None),"runtime_job":{
  "id":"synthetic-job-no-native-execution","status":"succeeded","tool_name":TOOL,
  "request_id":request_id,"request_sha256":request_sha,
  "candidate_sha256":envelope["candidate_file"]["sha256"],"execution_sha256":envelope["execution_file"]["sha256"]}});
    let result = json!({"data":data,"warnings":[],"provenance_checked":true,"execution":{
  "backend":PROVIDER,"duration_ms":1,"policy_decision":"allow","fallbacks_attempted":[],"dry_run":false,
  "provenance":{"provider":PROVIDER,"source":SourceKind::Driver,"provider_version":"test","capability_version":"test",
   "descriptor_sha256":"ab".repeat(32),"untrusted_metadata":false,"catalog_revision":1,"execution_provider":PROVIDER,"provider_generation":17}}});
    let args = json!({"file_name":format!("{request_id}.wav"),"request":request});
    let receipt=coordinator.service.append_production_receipt(ProductionReceiptInput{
  project_id:project.id,generation:project.generation,revision:project.revision,request_id:request_id.to_string(),
  request_sha256:request_digest(COMMAND,&args).unwrap(),command:COMMAND.into(),stage:"completed".into(),
  payload:json!({"result":result,"broker_request_id":"synthetic-cli-fixture","job_ref":null}),
 }).unwrap();
    let admission = ObservedAlignmentAdmission {
        connection_identity: coordinator.client.connection().identity().unwrap(),
        provider_generation: 17,
        catalog_revision: 1,
        descriptor_sha256: "ab".repeat(32),
        provider_version: "test".into(),
        capability_version: "test".into(),
        source_commit: "ab".repeat(20),
        admission_binding_sha256: "cd".repeat(32),
        native_input_root: temp.path().join("unadmitted-alignment-input"),
    };
    let prepared = PreparedAlignment {
        project_id: project.id,
        expected: RevisionStamp::from(&project),
        intent,
        asset_id: track.asset_id,
        request: args["request"].clone(),
        args,
        source,
    };
    BackendFixture {
        coordinator,
        project,
        temp,
        prepared,
        admission,
        receipt,
        counter,
    }
}
fn synthetic_witness(
    receipt: ProductionReceipt,
    prepared: &PreparedAlignment,
) -> Box<AuthenticatedSdkResult> {
    let result_value = receipt.payload["result"].clone();
    let broker_request_id = receipt.payload["broker_request_id"].clone();
    Box::new(AuthenticatedSdkResult {
        completed_receipt: receipt,
        command: COMMAND.into(),
        mutation: true,
        caller_request_id: prepared.intent.request_id.to_string(),
        project_id: prepared.project_id,
        expected_stamp: json!(prepared.expected),
        result_value,
        broker_request_id,
    })
}
fn qualified_fixture() -> (
    ProductionCoordinator,
    QualifiedVoiceAlignment,
    TempDir,
    Project,
) {
    let f = backend_fixture();
    let observed = synthetic_witness(f.receipt, &f.prepared);
    let qualified = f
        .coordinator
        .qualify_voice_alignment(*observed, f.prepared, &f.admission)
        .unwrap();
    (f.coordinator, qualified, f.temp, f.project)
}

#[test]
fn synthetic_qualified_result_applies_one_atomic_revision_and_verified_replay() {
    let (coordinator, mut qualified, _temp, project) = qualified_fixture();
    assert_eq!(coordinator.service.project(project.id).unwrap(), project);
    let first = coordinator
        .apply_qualified_voice_alignment(&mut qualified, &AtomicBool::new(false))
        .unwrap();
    assert_eq!(first["status"], "APPLIED");
    assert_eq!(first["replayed"], false);
    let after = coordinator.service.project(project.id).unwrap();
    assert_eq!(after.revision, project.revision + 1);
    assert_eq!(after.audio.transcript.len(), 2);
    assert_eq!(after.audio.transcript[0].text, "A");
    assert_eq!(after.audio.transcript[1].text, "B");
    assert_eq!(after.audio.transcript[0].start, rational(1, 50).unwrap());
    assert_eq!(after.audio.transcript[1].end, rational(8, 50).unwrap());
    assert!(after.audio.transcript.iter().all(|s| matches!(
        &s.alignment,
        AlignmentEvidence::Measured {
            confidence_millis: None,
            ..
        }
    )));
    assert_eq!(after.audio.cues, project.audio.cues);
    // A committed apply is observed first, including if cancellation arrived later.
    let replay = coordinator
        .apply_qualified_voice_alignment(&mut qualified, &AtomicBool::new(true))
        .unwrap();
    assert_eq!(replay["status"], "ALREADY_APPLIED");
    assert_eq!(replay["replayed"], true);
    assert_eq!(coordinator.service.project(project.id).unwrap(), after);
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[test]
fn cancellation_before_apply_is_sticky_and_preserves_the_complete_project() {
    let (coordinator, mut qualified, _temp, project) = qualified_fixture();
    assert!(
        coordinator
            .apply_qualified_voice_alignment(&mut qualified, &AtomicBool::new(true))
            .is_err()
    );
    assert!(!qualified.apply_attempted);
    assert!(qualified.cancelled);
    assert_eq!(coordinator.service.project(project.id).unwrap(), project);
    assert!(
        coordinator
            .apply_qualified_voice_alignment(&mut qualified, &AtomicBool::new(false))
            .is_err()
    );
    assert!(!qualified.apply_attempted);
    assert_eq!(coordinator.service.project(project.id).unwrap(), project);
    let observation = coordinator
        .service
        .store()
        .lock()
        .observe_apply_receipt_v1(
            project.id,
            &qualified.expected,
            &qualified.apply_request_id,
            &qualified.change,
            APPLY_REQUEST_ENCODING_V1,
        )
        .unwrap();
    assert_eq!(observation, ApplyReceiptObservation::NotFound);
}

#[test]
fn failed_sqlite_apply_is_not_repeated_even_after_fault_is_removed() {
    let (coordinator, mut qualified, temp, project) = qualified_fixture();
    let connection = rusqlite::Connection::open(temp.path().join("motionwright.sqlite3")).unwrap();
    // ID is generated from a canonical UUID; no user SQL is interpolated.
    assert!(
        qualified
            .apply_request_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b':')
    );
    connection.execute_batch(&format!(
  "CREATE TRIGGER block_synthetic_alignment BEFORE INSERT ON receipts WHEN NEW.request_id = '{}' BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END;",
  qualified.apply_request_id)).unwrap();
    assert!(
        coordinator
            .apply_qualified_voice_alignment(&mut qualified, &AtomicBool::new(false))
            .is_err()
    );
    assert!(qualified.apply_attempted);
    assert_eq!(coordinator.service.project(project.id).unwrap(), project);
    connection
        .execute_batch("DROP TRIGGER block_synthetic_alignment;")
        .unwrap();
    let error = coordinator
        .apply_qualified_voice_alignment(&mut qualified, &AtomicBool::new(false))
        .unwrap_err();
    assert!(!error.outcome_known);
    assert_eq!(coordinator.service.project(project.id).unwrap(), project);
    let observation = coordinator
        .service
        .store()
        .lock()
        .observe_apply_receipt_v1(
            project.id,
            &qualified.expected,
            &qualified.apply_request_id,
            &qualified.change,
            APPLY_REQUEST_ENCODING_V1,
        )
        .unwrap();
    assert_eq!(observation, ApplyReceiptObservation::NotFound);
}

#[tokio::test]
async fn complete_synthetic_receipt_replay_cannot_reconstruct_native_authority() {
    let f = backend_fixture();
    let outcome = f
        .coordinator
        .execute_with_receipt_origin(
            f.project.id,
            &f.prepared.expected,
            &f.prepared.intent.request_id.to_string(),
            COMMAND,
            f.prepared.args.clone(),
            true,
        )
        .await
        .unwrap();
    assert!(outcome.into_authenticated_result().is_none());
    assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    assert!(!f.counter.exists());
    assert_eq!(
        f.coordinator.service.project(f.project.id).unwrap(),
        f.project
    );
}

#[tokio::test]
async fn reopened_completed_receipt_remains_replay_without_native_authority() {
    let f = backend_fixture();
    let connection = f.coordinator.client.connection().clone();
    drop(f.coordinator);
    let service = StudioService::open(f.temp.path().join("motionwright.sqlite3")).unwrap();
    let recovered = ProductionCoordinator::new(service, connection).unwrap();
    let outcome = recovered
        .execute_with_receipt_origin(
            f.project.id,
            &f.prepared.expected,
            &f.prepared.intent.request_id.to_string(),
            COMMAND,
            f.prepared.args.clone(),
            true,
        )
        .await
        .unwrap();
    assert!(outcome.into_authenticated_result().is_none());
    assert_eq!(recovered.client.execute_calls.load(Ordering::SeqCst), 0);
    assert!(!f.counter.exists());
    assert_eq!(recovered.service.project(f.project.id).unwrap(), f.project);
}

#[tokio::test]
async fn fresh_transport_observation_for_another_command_cannot_qualify_alignment() {
    let f = backend_fixture();
    let outcome = f
        .coordinator
        .execute_with_receipt_origin(
            f.project.id,
            &f.prepared.expected,
            "synthetic-other-command",
            "driver.motion-canvas.composition.inspect",
            json!({"transport_fixture_only":true}),
            false,
        )
        .await
        .unwrap();
    let fresh = outcome.into_authenticated_result().unwrap();
    assert!(
        f.coordinator
            .qualify_voice_alignment(*fresh, f.prepared, &f.admission)
            .is_err()
    );
    assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
    assert_eq!(fs::read(&f.counter).unwrap(), b"invoked\n");
    assert_eq!(
        f.coordinator.service.project(f.project.id).unwrap(),
        f.project
    );
}

#[tokio::test]
async fn missing_runtime_admission_rejects_before_staging_or_dispatch() {
    let f = backend_fixture();
    let intent = VoiceAlignmentIntent {
        request_id: Uuid::new_v4(),
        voice_track_id: f.prepared.intent.voice_track_id,
        transcript_version: 1,
        transcript_text: "A B".into(),
    };
    let result = f
        .coordinator
        .prepare_voice_alignment(
            f.project.id,
            &f.prepared.expected,
            intent,
            &AtomicBool::new(false),
        )
        .await;
    assert!(result.is_err());
    assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    assert!(!f.admission.native_input_root.exists());
    assert!(!f.counter.exists());
    assert_eq!(
        f.coordinator.service.project(f.project.id).unwrap(),
        f.project
    );
}

#[tokio::test]
async fn unknown_production_attempt_never_invokes_the_cli_again() {
    let f = backend_fixture();
    let request_id = Uuid::new_v4().to_string();
    let mut args = f.prepared.args.clone();
    args["request"]["request_id"] = json!(request_id);
    f.coordinator.service.append_production_receipt(ProductionReceiptInput{
  project_id:f.project.id,generation:f.project.generation,revision:f.project.revision,
  request_id:request_id.clone(),request_sha256:request_digest(COMMAND,&args).unwrap(),command:COMMAND.into(),
  stage:"outcome_unknown".into(),payload:json!({"error":"synthetic unknown transport fixture","mutation":true,"job_ref":null}),
 }).unwrap();
    let error = match f
        .coordinator
        .execute_with_receipt_origin(
            f.project.id,
            &f.prepared.expected,
            &request_id,
            COMMAND,
            args,
            true,
        )
        .await
    {
        Err(error) => error,
        Ok(_) => panic!("Unknown alignment unexpectedly dispatched"),
    };
    assert!(!error.outcome_known);
    assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    assert!(!f.counter.exists());
    assert_eq!(
        f.coordinator.service.project(f.project.id).unwrap(),
        f.project
    );
}

#[test]
fn altered_registration_mutation_policy_and_terminal_job_never_qualify() {
    for case in 0..9 {
        let f = backend_fixture();
        let mut observed = synthetic_witness(f.receipt, &f.prepared);
        match case {
            0 => observed.mutation = false,
            1 => {
                observed.completed_receipt.payload["result"]["execution"]["provenance"]["provider_generation"] =
                    json!(18)
            }
            2 => {
                observed.completed_receipt.payload["result"]["execution"]["provenance"]["descriptor_sha256"] =
                    json!("ee".repeat(32))
            }
            3 => {
                observed.completed_receipt.payload["result"]["execution"]["provenance"]["untrusted_metadata"] =
                    json!(true)
            }
            4 => observed.completed_receipt.payload["result"]["execution"]["dry_run"] = json!(true),
            5 => {
                observed.completed_receipt.payload["result"]["execution"]["policy_decision"] =
                    json!("deny")
            }
            6 => {
                observed.completed_receipt.payload["result"]["data"]["runtime_job"]["request_sha256"] =
                    json!("00".repeat(32))
            }
            7 => {
                observed.completed_receipt.payload["result"]["data"]["runtime_job"]["status"] =
                    json!("running")
            }
            _ => {
                observed.completed_receipt.payload["result"]["data"]["runtime_job"]["candidate_sha256"] =
                    json!("00".repeat(32))
            }
        }
        assert!(
            f.coordinator
                .qualify_voice_alignment(*observed, f.prepared, &f.admission)
                .is_err(),
            "case {case}"
        );
        assert_eq!(
            f.coordinator.service.project(f.project.id).unwrap(),
            f.project
        );
        assert_eq!(f.coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    }
}
