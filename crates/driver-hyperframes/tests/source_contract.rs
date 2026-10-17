use std::path::PathBuf;

use motionwright_hyperframes_driver::*;
use motionwright_hyperframes_profile::{
    HYPERFRAMES_REVISION, HYPERFRAMES_VERSION, LayerContent, LayerTransform, Rational, SourceClock,
    SourceDocument, SourceLayer, admit_source, author_source, carrier_bytes, sha256,
};
use semwright_driver_sdk::{Driver, RuntimeToolArg, ToolExecutionOutput};
use semwright_types::{Error, ErrorCode};
use serde_json::json;
use uuid::Uuid;

fn request() -> SourceRenderRequest {
    SourceRenderRequest {
        schema: REQUEST_SCHEMA.into(),
        project_id: Uuid::from_u128(1),
        generation: Uuid::from_u128(2),
        revision: 3,
        scene_id: Uuid::from_u128(4),
        capsule_id: Uuid::from_u128(5),
        operation_id: Uuid::from_u128(6),
        source_sha256: "a".repeat(64),
        canonical_dependency_sha256: "b".repeat(64),
        owner_profile_sha256: "c".repeat(64),
    }
}

fn owner() -> OwnerRuntimeProfile {
    OwnerRuntimeProfile {
        schema: OWNER_SCHEMA.into(),
        producer_revision: HYPERFRAMES_REVISION.into(),
        producer_version: HYPERFRAMES_VERSION.into(),
        runner_sha256: sha256(RUNNER_SOURCE.as_bytes()),
        chromium_major: 132,
        tool_pins: ToolPins {
            node_sha256: "1".repeat(64),
            chrome_sha256: "2".repeat(64),
            ffmpeg_sha256: "3".repeat(64),
            ffprobe_sha256: "4".repeat(64),
        },
        files: [
            "node_modules/@hyperframes/producer/dist/index.js",
            "node_modules/@hyperframes/producer/package.json",
            "node_modules/@hyperframes/engine/dist/index.js",
            "node_modules/@hyperframes/engine/package.json",
            "node_modules/@hyperframes/core/package.json",
            "bun.lock",
        ]
        .map(|path| RuntimeFile {
            path: path.into(),
            bytes: 1,
            sha256: "d".repeat(64),
        })
        .to_vec(),
    }
}

fn source() -> motionwright_hyperframes_profile::AdmittedSource {
    let carrier = author_source(
        SourceDocument {
            clock: SourceClock {
                fps: Rational::new(30, 1).unwrap(),
                frame_count: 30,
            },
            width: 640,
            height: 360,
            background: "#000000".into(),
            layers: vec![SourceLayer {
                id: Uuid::from_u128(9),
                start_frame: 0,
                end_frame: 30,
                width: 100,
                height: 100,
                transform: LayerTransform::default(),
                content: LayerContent::Rectangle {
                    color: "#E05522".into(),
                    radius: 0,
                },
            }],
        },
        vec![],
    )
    .unwrap();
    let bytes = carrier_bytes(&carrier).unwrap();
    admit_source(&bytes, &sha256(&bytes)).unwrap()
}

#[test]
fn owner_manifest_uses_one_host_tool_with_explicit_mounts_and_no_ambient_network() {
    let installation = registration::OwnerInstallation::new(
        PathBuf::from(if cfg!(windows) {
            "C:\\fixture\\driver.exe"
        } else {
            "/fixture/driver"
        }),
        "0".repeat(64),
        owner(),
    )
    .unwrap();
    let manifest = installation.manifest().unwrap();
    assert_eq!(manifest.protocol, 7);
    assert!(!manifest.network);
    assert!(manifest.loopback_port.is_none());
    assert!(manifest.secrets.is_empty());
    assert!(!manifest.nvidia_gpu);
    let node = manifest
        .tools
        .iter()
        .find(|tool| tool.name == NODE_TOOL)
        .unwrap();
    assert_eq!(node.mounts, [SOURCE_MOUNT, RUNTIME_MOUNT, OPERATION_MOUNT]);
    assert_eq!(node.dependencies, [CHROME_TOOL, FFMPEG_TOOL, FFPROBE_TOOL]);
    assert!(
        manifest
            .mounts
            .iter()
            .find(|mount| mount.root == SOURCE_MOUNT)
            .unwrap()
            .read_only
    );
    assert!(
        !manifest
            .mounts
            .iter()
            .find(|mount| mount.root == OPERATION_MOUNT)
            .unwrap()
            .read_only
    );
    assert_eq!(
        manifest.resources.address_space_bytes,
        4 * 1024 * 1024 * 1024
    );
    assert_eq!(manifest.resources.operation_cpu_seconds, 240);
    // A valid manifest is not evidence of loopback or a runtime execution.
    assert_eq!(
        manifest.application.supported_versions,
        [HYPERFRAMES_VERSION]
    );
}

#[test]
fn owner_runtime_rejects_missing_entries_duplicate_paths_wrong_pin_and_overbudget() {
    let mut profile = owner();
    profile.files.pop();
    assert!(profile.validate().is_err());
    let mut profile = owner();
    profile.files.push(profile.files[0].clone());
    assert!(profile.validate().is_err());
    let mut profile = owner();
    profile.producer_revision = "0".repeat(40);
    assert!(profile.validate().is_err());
    let mut profile = owner();
    profile.runner_sha256 = "0".repeat(64);
    assert!(profile.validate().is_err());
    let mut profile = owner();
    profile.files[0].bytes = 128 * 1024 * 1024 + 1;
    assert!(profile.validate().is_err());
    let mut profile = owner();
    profile.files[0].path = "../outside.js".into();
    assert!(profile.validate().is_err());
}

#[test]
fn command_dto_rejects_webview_paths_options_tools_and_verdicts() {
    for field in [
        "path",
        "project_dir",
        "output_path",
        "html",
        "argv",
        "executable",
        "engine_config",
        "verified",
        "grant",
    ] {
        let mut value = serde_json::to_value(request()).unwrap();
        value[field] = json!("caller-value");
        assert!(parse_request(value).is_err(), "unexpected field {field}");
    }
    let parsed = parse_request(serde_json::to_value(request()).unwrap()).unwrap();
    assert_eq!(parsed, request());
    assert!(!render_capability().descriptor.dry_run);
    assert!(render_capability().descriptor.interactive_consent);
    assert!(render_capability().descriptor.risk.mutates());
}

#[test]
fn runtime_arguments_only_use_declared_root_and_sealed_tool_references() {
    let args = runtime_args();
    assert_eq!(args.len(), 8);
    assert_eq!(
        args[0],
        RuntimeToolArg::Literal {
            value: "--input-type=module".into()
        }
    );
    assert_eq!(args[1], RuntimeToolArg::Literal { value: "-".into() });
    for arg in &args[2..5] {
        assert!(matches!(arg,RuntimeToolArg::MountPath{relative,..} if relative.is_empty()));
    }
    for arg in &args[5..] {
        assert!(matches!(arg, RuntimeToolArg::ToolPath { .. }));
    }
}

#[test]
fn request_identity_and_full_source_binding_change_together() {
    let base = request();
    let original = base.binding_sha256().unwrap();
    for change in 0..4 {
        let mut next = base.clone();
        match change {
            0 => next.revision += 1,
            1 => next.generation = Uuid::from_u128(99),
            2 => next.source_sha256 = "f".repeat(64),
            _ => next.canonical_dependency_sha256 = "e".repeat(64),
        }
        assert_ne!(next.binding_sha256().unwrap(), original);
    }
    let mut invalid = base;
    invalid.operation_id = Uuid::nil();
    assert!(invalid.validate().is_err());
}

#[test]
fn the_pinned_runner_and_source_packet_fit_existing_host_stdin_budget() {
    let source = source();
    let mut req = request();
    req.source_sha256 = source.readiness().carrier_sha256.clone();
    let program = runtime_stdin(&req, &source, &owner()).unwrap();
    assert!(program.len() <= 64 * 1024);
    let text = String::from_utf8(program).unwrap();
    assert!(text.starts_with(RUNNER_SOURCE));
    assert!(text.contains("await main(JSON.parse("));
    req.source_sha256 = "0".repeat(64);
    assert!(runtime_stdin(&req, &source, &owner()).is_err());
}

#[test]
fn bounded_file_reader_checks_actual_bytes_and_hard_limit() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("source.json");
    std::fs::write(&path, b"closed source").unwrap();
    assert_eq!(read_bounded_file(&path, 13).unwrap(), b"closed source");
    assert!(read_bounded_file(&path, 12).is_err());
    assert!(read_bounded_file(root.path(), 1024).is_err());
}

#[cfg(unix)]
#[test]
fn linked_runtime_or_source_files_are_not_admitted() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("source");
    let link = root.path().join("link");
    std::fs::write(&source, b"source").unwrap();
    std::os::unix::fs::symlink(&source, &link).unwrap();
    assert!(read_bounded_file(&link, 1024).is_err());
    std::fs::remove_file(&link).unwrap();
    std::fs::hard_link(&source, &link).unwrap();
    assert!(read_bounded_file(&link, 1024).is_err());
}

#[tokio::test]
async fn no_context_entry_cannot_dispatch_a_host_job() {
    let mut driver = HyperframesDriver;
    let error = driver
        .execute(
            RENDER_COMMAND,
            "untrusted",
            serde_json::to_value(request()).unwrap(),
        )
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::PermissionDenied);
    assert!(error.outcome_known);
}

#[test]
fn exit_zero_is_insufficient_without_bound_producer_result() {
    let source = source();
    let mut req = request();
    req.source_sha256 = source.readiness().carrier_sha256.clone();
    for stdout in [
        b"{}".to_vec(),
        br#"{"status":"complete","verified":true}"#.to_vec(),
    ] {
        let error = validate_output(
            &req,
            &source,
            &owner(),
            ToolExecutionOutput {
                exit_code: 0,
                stdout,
                stderr: vec![],
            },
        )
        .unwrap_err();
        assert!(!error.outcome_known);
    }
}

fn result_fixture(
    req: &SourceRenderRequest,
    source: &motionwright_hyperframes_profile::AdmittedSource,
) -> ProducerResult {
    let proof = |name: &str, bytes: &[u8]| FileProof {
        name: name.into(),
        bytes: bytes.len() as u64,
        sha256: sha256(bytes),
    };
    ProducerResult {
        schema: RESULT_SCHEMA.into(),
        request_sha256: req.binding_sha256().unwrap(),
        carrier_sha256: req.source_sha256.clone(),
        html_sha256: source.readiness().html_sha256.clone(),
        runtime_profile_sha256: req.owner_profile_sha256.clone(),
        runtime_closure_verified: true,
        runner_sha256: owner().runner_sha256,
        producer_revision: HYPERFRAMES_REVISION.into(),
        producer_version: HYPERFRAMES_VERSION.into(),
        producer_job_id: Uuid::from_u128(11).to_string(),
        producer_status: "complete".into(),
        operation_id: req.operation_id,
        clock: source.document().clock,
        width: source.document().width,
        height: source.document().height,
        // These original bytes intentionally are NOT encoded media. This fixture
        // only tests physical custody and cannot establish native video fidelity.
        video: proof("output.mp4", b"physical readback fixture, not a native MP4"),
        retained_source: proof("source.json", source.export_bytes()),
        diagnostic: proof("diagnostic.jsonl", b"{\"fixture\":\"source check only\"}\n"),
        effective_config: proof("effective-config.json", b"{\"fixture\":true}\n"),
        complete_report_retained: true,
        pixel_fidelity_verified: false,
        editable_roundtrip_verified: false,
        export_authorized: false,
    }
}

#[test]
fn a_coherent_typed_fixture_passes_bindings_but_forged_claims_or_identity_stay_uncertain() {
    let source = source();
    let mut req = request();
    req.source_sha256 = source.readiness().carrier_sha256.clone();
    let result = result_fixture(&req, &source);
    let output = |value: &ProducerResult| ToolExecutionOutput {
        exit_code: 0,
        stdout: serde_json::to_vec(value).unwrap(),
        stderr: vec![],
    };
    assert_eq!(
        validate_output(&req, &source, &owner(), output(&result)).unwrap(),
        result
    );
    for field in [
        "pixel_fidelity_verified",
        "editable_roundtrip_verified",
        "export_authorized",
    ] {
        let mut value = serde_json::to_value(&result).unwrap();
        value[field] = json!(true);
        let changed = serde_json::from_value(value).unwrap();
        assert!(
            !validate_output(&req, &source, &owner(), output(&changed))
                .unwrap_err()
                .outcome_known
        );
    }
    let mut changed = result.clone();
    changed.operation_id = Uuid::from_u128(999);
    assert!(
        !validate_output(&req, &source, &owner(), output(&changed))
            .unwrap_err()
            .outcome_known
    );
    let mut changed = result;
    changed.clock.frame_count += 1;
    assert!(
        !validate_output(&req, &source, &owner(), output(&changed))
            .unwrap_err()
            .outcome_known
    );
}

#[test]
fn successful_json_without_the_original_artifacts_does_not_establish_physical_custody() {
    let root = tempfile::tempdir().unwrap();
    let source = source();
    let mut req = request();
    req.source_sha256 = source.readiness().carrier_sha256.clone();
    let result = result_fixture(&req, &source);
    let error = verify_result_artifact_bytes(root.path(), &req, &source, &result).unwrap_err();
    assert!(!error.outcome_known);
    assert!(!root.path().join(req.operation_name().unwrap()).exists());
}

#[test]
fn physical_artifact_recheck_detects_tamper_and_preserves_every_original_file() {
    let root = tempfile::tempdir().unwrap();
    let source = source();
    let mut req = request();
    req.source_sha256 = source.readiness().carrier_sha256.clone();
    let result = result_fixture(&req, &source);
    let operation = root.path().join(req.operation_name().unwrap());
    std::fs::create_dir(&operation).unwrap();
    for (name, bytes) in [
        (
            "output.mp4",
            b"physical readback fixture, not a native MP4".as_slice(),
        ),
        ("source.json", source.export_bytes()),
        (
            "diagnostic.jsonl",
            b"{\"fixture\":\"source check only\"}\n".as_slice(),
        ),
        ("effective-config.json", b"{\"fixture\":true}\n".as_slice()),
    ] {
        std::fs::write(operation.join(name), bytes).unwrap();
    }
    let retained_result = serde_json::to_vec(&result).unwrap();
    std::fs::write(operation.join("result.json"), &retained_result).unwrap();
    verify_result_artifact_bytes(root.path(), &req, &source, &result).unwrap();
    std::fs::write(operation.join("output.mp4"), b"changed physical bytes").unwrap();
    assert!(
        !verify_result_artifact_bytes(root.path(), &req, &source, &result)
            .unwrap_err()
            .outcome_known
    );
    assert_eq!(
        std::fs::read(operation.join("result.json")).unwrap(),
        retained_result
    );
    assert_eq!(std::fs::read_dir(&operation).unwrap().count(), 5);
    assert_eq!(
        std::fs::read(operation.join("output.mp4")).unwrap(),
        b"changed physical bytes"
    );
}

#[test]
fn lost_start_ack_from_sdk_errors_is_uncertain_without_a_fabricated_job() {
    let request = request();
    for code in [
        ErrorCode::Timeout,
        ErrorCode::Cancelled,
        ErrorCode::Unavailable,
        ErrorCode::ProtocolMismatch,
    ] {
        // The pinned SDK constructs these with Error::new after sending a
        // start control. Their message is deliberately unrelated to its text.
        let original = Error::new(code, "arbitrary observer wording");
        assert!(original.outcome_known);
        let classified =
            classify_original_start_error(&request, "original-parent-request", original);
        assert_eq!(classified.code, code);
        assert_eq!(classified.message, "arbitrary observer wording");
        assert!(!classified.outcome_known);
        assert!(
            classified
                .candidates
                .contains(&format!("hyperframes-operation:{}", request.operation_id))
        );
        assert!(
            classified
                .candidates
                .contains(&"hyperframes-host-request:original-parent-request".into())
        );
        assert!(
            !classified
                .candidates
                .iter()
                .any(|candidate| candidate.starts_with("hyperframes-host-job:"))
        );
    }
}

#[test]
fn an_already_unknown_start_keeps_original_code_and_observation_candidates() {
    let mut original = Error::new(ErrorCode::BackendFailed, "original transport error").uncertain();
    original
        .candidates
        .push("original-observation:retained".into());
    let classified = classify_original_start_error(&request(), "same-parent", original);
    assert_eq!(classified.code, ErrorCode::BackendFailed);
    assert_eq!(classified.message, "original transport error");
    assert!(!classified.outcome_known);
    assert_eq!(classified.candidates[0], "original-observation:retained");
    assert_eq!(classified.candidates.len(), 3);
}

#[test]
fn local_request_preflight_denials_remain_known_before_the_host_start_boundary() {
    let mut invalid = request();
    invalid.operation_id = Uuid::nil();
    let error = parse_request(serde_json::to_value(invalid).unwrap()).unwrap_err();
    assert_eq!(error.code, ErrorCode::InvalidArgument);
    assert!(error.outcome_known);
    assert!(error.candidates.is_empty());
}
