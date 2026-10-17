//! Native acceptance/demo: Blender checkpoint -> process exit -> Motion Canvas
//! resume. Uses real Semwright Driver Host when supplied an owner connection.
//! The "stop" phase interrupts BETWEEN applications; the "fail" phase invokes
//! the real native renderer with a deliberately unavailable CI-only browser,
//! requires a typed terminal native failure and a durable retryable checkpoint.
//! "resume" runs with the restored browser and MUST NOT rerun native Blender.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::{
    film::{FilmBuildOptions, SceneFilmIntent},
    production::{ProductionConnection, ProductionCoordinator, StageDisposition},
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_native_sdk::ErrorCode;
use serde_json::json;
use std::{
    collections::BTreeSet,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn usage() -> ! {
    eprintln!(
        "usage: native-cross-app-recovery-e2e <seed DATABASE | stop DATABASE CONNECTION EVIDENCE_JSON | fail DATABASE CONNECTION EVIDENCE_JSON | resume DATABASE CONNECTION EVIDENCE_JSON | repeat DATABASE CONNECTION EVIDENCE_JSON>"
    );
    std::process::exit(2);
}
fn arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

fn shape(name: &str, kind: &str, x: f64, color: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x,
        y: 250.0,
        width: 280.0,
        height: 280.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(color.into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: None,
            font_size: None,
            font_weight: None,
            line_height: None,
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

fn text_node() -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Motion Canvas continuation".into(),
        kind: "text".into(),
        parent_id: None,
        x: 180.0,
        y: 410.0,
        width: 1510.0,
        height: 170.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Blender stays done. Motion Canvas resumes.".into()),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some("#F5F5F2".into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: Some("Instrument Sans Variable".into()),
            font_size: Some(64.0),
            font_weight: Some(700),
            line_height: Some(1.05),
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

fn apply(
    service: &StudioService,
    project: motionwright_domain::Project,
    id: &str,
    change: Change,
) -> Result<motionwright_domain::Project, Box<dyn std::error::Error>> {
    Ok(service
        .apply(project.id, &RevisionStamp::from(&project), id, &change)?
        .project)
}

fn seed(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("cross-app recovery seed database must be empty".into());
    }
    let initial = service.create_project("Cross-app selective recovery demo")?;
    let project = apply(
        &service,
        initial,
        "demo-blender-scene",
        Change::AddScene {
            name: "Reusable native Blender contribution".into(),
            objective: "Native 3D geometry survives downstream failures".into(),
            duration_seconds: 2,
        },
    )?;
    let blender_scene = project.scenes[0].id;
    let project = apply(
        &service,
        project,
        "demo-blender-kind",
        Change::SetSceneRenderer {
            scene_id: blender_scene,
            renderer: RendererKind::Blender,
        },
    )?;
    let project = apply(
        &service,
        project,
        "demo-blender-panel",
        Change::AddCanvasNode {
            scene_id: blender_scene,
            node: shape("Semantic Blender rectangle", "rectangle", 300.0, "#3E75A9"),
        },
    )?;
    let project = apply(
        &service,
        project,
        "demo-blender-circle",
        Change::AddCanvasNode {
            scene_id: blender_scene,
            node: shape("Semantic Blender circle", "circle", 950.0, "#EFB84E"),
        },
    )?;
    let project = apply(
        &service,
        project,
        "demo-motion-scene",
        Change::AddScene {
            name: "Motion Canvas after native Blender".into(),
            objective: "Resume semantic Motion Canvas without reexecuting Blender".into(),
            duration_seconds: 2,
        },
    )?;
    let motion_scene = project.scenes[1].id;
    let project = apply(
        &service,
        project,
        "demo-motion-title",
        Change::AddCanvasNode {
            scene_id: motion_scene,
            node: text_node(),
        },
    )?;
    let deliverable_id = project
        .deliverables
        .first()
        .ok_or("missing deliverable")?
        .id;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "demo":"cross-app-selective-recovery/1", "project_id":project.id,
            "resource":project.resource_key(), "generation":project.generation,
            "revision":project.revision, "blender_scene":blender_scene,
            "motion_scene":motion_scene, "deliverable_id":deliverable_id,
        }))?
    );
    Ok(())
}

fn private_evidence(
    path: &Path,
    value: &serde_json::Value,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut opts = OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut file = opts.open(path)?;
    file.write_all(&serde_json::to_vec_pretty(value)?)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    Ok(())
}

#[derive(Clone, Copy)]
enum NativeFailureObservation {
    NativeCall,
    ReceiptCollectionFailed,
    BlenderReexecutionDetected,
    RetryableCheckpointMissing,
    EvidenceWriteFailed,
}

impl NativeFailureObservation {
    fn as_str(self) -> &'static str {
        match self {
            Self::NativeCall => "native_call_failed",
            Self::ReceiptCollectionFailed => "receipt_collection_failed",
            Self::BlenderReexecutionDetected => "blender_reexecution_detected",
            Self::RetryableCheckpointMissing => "retryable_checkpoint_missing",
            Self::EvidenceWriteFailed => "evidence_write_failed",
        }
    }
}

fn is_expected_font_evidence_failure(error: &semwright_native_sdk::Error) -> bool {
    error.outcome_known
        && error.code == ErrorCode::BackendFailed
        && error.message == "Motion Canvas segment 1 render failed (category: font_evidence)"
}

/// The original SDK enum and outcome bit remain authoritative even when a
/// subsequent receipt read, recovery check, or evidence write also fails.
fn native_failure_envelope(
    error: &semwright_native_sdk::Error,
    receipts: &[motionwright_storage::ProductionReceipt],
    phase: &str,
    observation: NativeFailureObservation,
) -> serde_json::Value {
    let closed_error =
        |code: semwright_native_sdk::ErrorCode, known: Option<bool>, message: &str| {
            let mut value = json!({ "code": code });
            if let Some(known) = known {
                value["outcome_known"] = json!(known);
            }
            if message.contains("Provider capability generation is inactive") {
                // This existing consumer category is fixed; provider text, paths,
                // candidates, capabilities and job references are never forwarded.
                value["message"] = json!("Provider capability generation is inactive");
            }
            value
        };
    let safe_receipts = receipts
        .iter()
        .take(256)
        .filter_map(|receipt| {
            let command = match receipt.command.as_str() {
                "driver.blender.collection.create" => "driver.blender.collection.create",
                "driver.blender.object.create" => "driver.blender.object.create",
                "driver.blender.object.transform" => "driver.blender.object.transform",
                "driver.blender.collection.link" => "driver.blender.collection.link",
                "driver.blender.material.create" => "driver.blender.material.create",
                "driver.blender.material.assign" => "driver.blender.material.assign",
                "driver.blender.export.glb" => "driver.blender.export.glb",
                "driver.motion-canvas.composition.inspect" => {
                    "driver.motion-canvas.composition.inspect"
                }
                "driver.motion-canvas.composition.plan" => "driver.motion-canvas.composition.plan",
                "driver.motion-canvas.composition.apply" => {
                    "driver.motion-canvas.composition.apply"
                }
                "driver.motion-canvas.composition.verify" => {
                    "driver.motion-canvas.composition.verify"
                }
                "driver.motion-canvas.render.start" => "driver.motion-canvas.render.start",
                "driver.motion-canvas.render.status" => "driver.motion-canvas.render.status",
                "driver.motion-canvas.render.result" => "driver.motion-canvas.render.result",
                "motionwright.recovery.motion-canvas" => "motionwright.recovery.motion-canvas",
                _ => return None,
            };
            let stage = match receipt.stage.as_str() {
                "dispatching" => "dispatching",
                "completed" => "completed",
                "failed_known" => "failed_known",
                "failed" => "failed",
                "outcome_unknown" => "outcome_unknown",
                _ => "unrecognized",
            };
            let mut payload = json!({});
            // Use the SDK's typed enum, never a second error-code roster.
            if let Some(observed) = receipt.payload.get("error")
                && let Some(code) = observed.get("code").and_then(|value| {
                    serde_json::from_value::<semwright_native_sdk::ErrorCode>(value.clone()).ok()
                })
            {
                payload["error"] = closed_error(
                    code,
                    observed
                        .get("outcome_known")
                        .and_then(serde_json::Value::as_bool),
                    observed
                        .get("message")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or(""),
                );
            }
            Some(json!({ "command": command, "stage": stage, "payload": payload }))
        })
        .collect::<Vec<_>>();
    let phase = match phase {
        "stop" => "stop",
        "fail" => "fail",
        "resume" => "resume",
        "repeat" => "repeat",
        _ => "unrecognized",
    };
    json!({
        "native_cross_app_recovery_e2e": "FAIL",
        "phase": phase,
        "error": closed_error(error.code, Some(error.outcome_known), &error.message),
        "receipts": safe_receipts,
        "observation": observation.as_str(),
        "native_media_verified": false,
        "automatic_mutating_retry": false,
    })
}

fn write_native_failure_response(
    stream: &mut impl Write,
    response: &serde_json::Value,
) -> Result<(), Box<dyn std::error::Error>> {
    serde_json::to_writer(&mut *stream, response)?;
    stream.write_all(b"\n")?;
    stream.flush()?;
    Ok(())
}

/// Emit and flush the complete public result before optional owner-local
/// retention. None means retention was already attempted: never retry it here.
fn emit_native_failure(
    stream: &mut impl Write,
    diagnostic_path: Option<&Path>,
    phase: &str,
    error: &semwright_native_sdk::Error,
    receipts: &[motionwright_storage::ProductionReceipt],
    observation: NativeFailureObservation,
) -> Box<dyn std::error::Error> {
    let response = native_failure_envelope(error, receipts, phase, observation);
    let written = write_native_failure_response(stream, &response).is_ok();
    if let Some(path) = diagnostic_path {
        let retained = private_evidence(
            path,
            &json!({
                "public_response": response,
                "original_error": error,
                "receipts": receipts,
            }),
        );
        if retained.is_err() {
            eprintln!("native-owner-diagnostic=NOT_WRITTEN");
        }
    }
    if written {
        "native-cross-app-recovery-e2e failed; typed failure emitted on stdout".into()
    } else {
        "native-cross-app-recovery-e2e failed; stdout write failed".into()
    }
}

fn report_native_call_failure(
    coordinator: &ProductionCoordinator,
    project_id: Uuid,
    evidence_path: &Path,
    phase: &str,
    error: &semwright_native_sdk::Error,
) -> Box<dyn std::error::Error> {
    let (receipts, observation) = match coordinator.receipts(project_id, 256) {
        Ok(receipts) => (receipts, NativeFailureObservation::NativeCall),
        Err(_) => (
            Vec::new(),
            NativeFailureObservation::ReceiptCollectionFailed,
        ),
    };
    emit_native_failure(
        &mut std::io::stdout().lock(),
        Some(evidence_path),
        phase,
        error,
        &receipts,
        observation,
    )
}

async fn phase(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    phase: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("expected one seeded project".into());
    }
    let project = &projects[0];
    let blender = project
        .scenes
        .iter()
        .find(|s| s.renderer == RendererKind::Blender)
        .ok_or("missing Blender scene")?;
    let motion = project
        .scenes
        .iter()
        .find(|s| s.renderer == RendererKind::MotionCanvas)
        .ok_or("missing Motion Canvas scene")?;
    let deliverable = project.deliverables.first().ok_or("missing deliverable")?;
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("wrong Semwright resource".into());
    }
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let expected = RevisionStamp::from(project);
    let before = coordinator.receipts(project.id, 256)?;
    let blender_calls_before = before
        .iter()
        .filter(|r| r.command.starts_with("driver.blender.") && r.stage == "completed")
        .count();
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1)?,
        font_family: "Instrument Sans Variable".into(),
        mono_font_family: "IBM Plex Mono".into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: motion.id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
        }],
    };
    let mut expected_native_failure: Option<(
        semwright_native_sdk::Error,
        Vec<motionwright_storage::ProductionReceipt>,
    )> = None;
    let report = if phase == "stop" {
        let (disposition, proof) = coordinator
            .checkpoint_blender_for_cross_app(project.id, &expected, "reddit-demo", blender.id)
            .await
            .map_err(|error| {
                report_native_call_failure(&coordinator, project.id, evidence_path, phase, &error)
            })?;
        let after = coordinator.receipts(project.id, 256)?;
        if after
            .iter()
            .any(|r| r.command.starts_with("driver.motion-canvas."))
        {
            return Err("stop phase must not dispatch Motion Canvas".into());
        }
        json!({
            "phase":"stop", "fault":"controlled process interruption before Motion Canvas dispatch",
            "blender":disposition, "blender_proof":proof,
            "project":project.resource_key(), "revision":project.revision,
        })
    } else if phase == "fail" {
        let failed = coordinator
            .recover_blender_motion(
                project.id,
                &expected,
                "reddit-demo",
                blender.id,
                deliverable.id,
                &options,
            )
            .await;
        match failed {
            Err(error) if is_expected_font_evidence_failure(&error) => {
                let after = match coordinator.receipts(project.id, 256) {
                    Ok(receipts) => receipts,
                    Err(_) => {
                        return Err(emit_native_failure(
                            &mut std::io::stdout().lock(),
                            Some(evidence_path),
                            phase,
                            &error,
                            &[],
                            NativeFailureObservation::ReceiptCollectionFailed,
                        ));
                    }
                };
                let blender_calls_after = after
                    .iter()
                    .filter(|r| r.command.starts_with("driver.blender.") && r.stage == "completed")
                    .count();
                if blender_calls_after != blender_calls_before {
                    return Err(emit_native_failure(
                        &mut std::io::stdout().lock(),
                        Some(evidence_path),
                        phase,
                        &error,
                        &after,
                        NativeFailureObservation::BlenderReexecutionDetected,
                    ));
                }
                let failed_stage = after.iter().find(|r| {
                    r.command == "motionwright.recovery.motion-canvas"
                        && r.stage == "failed_known"
                        && r.payload.get("retryable").and_then(|v| v.as_bool()) == Some(true)
                        && r.payload.get("attempt").and_then(|v| v.as_u64()) == Some(1)
                });
                if failed_stage.is_none() {
                    return Err(emit_native_failure(
                        &mut std::io::stdout().lock(),
                        Some(evidence_path),
                        phase,
                        &error,
                        &after,
                        NativeFailureObservation::RetryableCheckpointMissing,
                    ));
                }
                expected_native_failure = Some((error, after));
                json!({
                    "phase":"fail",
                    "expected_native_failure": true,
                    "native_failure_class":"font_evidence",
                    "native_status":"failed",
                    "outcome_known":true,
                    "retryable":true,
                    "motion_canvas_attempt":1,
                    "blender_reexecution_count":blender_calls_after - blender_calls_before,
                    "project":project.resource_key()
                })
            }
            Ok(_) => {
                return Err(
                    "native renderer unexpectedly succeeded during failure injection".into(),
                );
            }
            Err(error) => {
                return Err(report_native_call_failure(
                    &coordinator,
                    project.id,
                    evidence_path,
                    phase,
                    &error,
                ));
            }
        }
    } else {
        let result = coordinator
            .recover_blender_motion(
                project.id,
                &expected,
                "reddit-demo",
                blender.id,
                deliverable.id,
                &options,
            )
            .await
            .map_err(|error| {
                report_native_call_failure(&coordinator, project.id, evidence_path, phase, &error)
            })?;
        let afterwards = coordinator.receipts(project.id, 256)?;
        let blender_calls_after = afterwards
            .iter()
            .filter(|r| r.command.starts_with("driver.blender.") && r.stage == "completed")
            .count();
        if blender_calls_after != blender_calls_before {
            return Err("cross-app recovery reexecuted a Blender driver command".into());
        }
        if !matches!(result.blender, StageDisposition::Reused)
            || !matches!(
                result.motion_canvas,
                StageDisposition::Executed | StageDisposition::Reused
            )
        {
            return Err("recovery did not reuse native Blender".into());
        }
        if phase == "repeat" && !matches!(result.motion_canvas, StageDisposition::Reused) {
            return Err("repeat did not reuse the verified Motion Canvas frames".into());
        }
        json!({
            "phase":phase, "recovery":result,
            "blender_command_calls_before":blender_calls_before,
            "blender_command_calls_after":blender_calls_after,
            "blender_reexecution_count":blender_calls_after - blender_calls_before,
            "project":project.resource_key(),
        })
    };
    if let Err(write_error) = private_evidence(evidence_path, &report) {
        if let Some((error, receipts)) = &expected_native_failure {
            return Err(emit_native_failure(
                &mut std::io::stdout().lock(),
                None,
                phase,
                error,
                receipts,
                NativeFailureObservation::EvidenceWriteFailed,
            ));
        }
        return Err(write_error);
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "native_cross_app_recovery_e2e":
                if phase == "fail" { "EXPECTED_NATIVE_FAILURE" } else { "PASS" },
            "phase":phase,
            "blender_reexecution_count":report.get("blender_reexecution_count"),
            "evidence":evidence_path,
        }))?
    );
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let command = args
        .next()
        .and_then(|os| os.into_string().ok())
        .unwrap_or_else(|| usage());
    let database = arg(&mut args);
    if command == "seed" {
        if args.next().is_some() {
            usage();
        }
        return seed(&database);
    }
    if !matches!(command.as_str(), "stop" | "fail" | "resume" | "repeat") {
        usage();
    }
    let connection = arg(&mut args);
    let evidence = arg(&mut args);
    if args.next().is_some() {
        usage();
    }
    phase(&database, &connection, &evidence, &command).await
}

#[cfg(test)]
mod native_cross_app_failure_stdout_tests {
    use super::*;
    use semwright_native_sdk::{Error, ErrorCode};

    const PRIVATE: &str = "job_ref=private-job ticket=secret /owner/private/source.blend";
    const FONT_FAILURE: &str = "Motion Canvas segment 1 render failed (category: font_evidence)";

    fn receipt(
        command: &str,
        stage: &str,
        payload: serde_json::Value,
    ) -> motionwright_storage::ProductionReceipt {
        // In-memory negative fixtures, never appended to a native journal.
        motionwright_storage::ProductionReceipt {
            id: Uuid::nil(),
            project_id: Uuid::nil(),
            generation: Uuid::nil(),
            revision: 1,
            request_id: PRIVATE.into(),
            request_sha256: "a".repeat(64),
            command: command.into(),
            stage: stage.into(),
            payload,
            created_at: PRIVATE.into(),
        }
    }

    fn complete_response(stdout: &[u8]) -> serde_json::Value {
        assert_eq!(stdout.last(), Some(&b'\n'));
        assert_eq!(stdout.iter().filter(|byte| **byte == b'\n').count(), 1);
        assert!(!std::str::from_utf8(stdout).unwrap().contains(PRIVATE));
        serde_json::from_slice(stdout).unwrap()
    }

    #[test]
    fn complete_stdout_preserves_sdk_code_and_unknown_in_every_native_phase() {
        for phase in ["stop", "fail", "resume", "repeat"] {
            for code in [
                ErrorCode::BackendFailed,
                ErrorCode::InvalidArgument,
                ErrorCode::Unsupported,
                ErrorCode::Timeout,
            ] {
                let mut error = Error::new(code, PRIVATE).uncertain();
                error.candidates.push(PRIVATE.into());
                let mut stdout = Vec::new();
                let terminal = emit_native_failure(
                    &mut stdout,
                    None,
                    phase,
                    &error,
                    &[],
                    NativeFailureObservation::NativeCall,
                );
                let parsed = complete_response(&stdout);
                assert_eq!(parsed["phase"], phase);
                assert_eq!(parsed["error"]["code"], serde_json::to_value(code).unwrap());
                assert_eq!(parsed["error"]["outcome_known"], false);
                assert_eq!(parsed["native_cross_app_recovery_e2e"], "FAIL");
                assert_eq!(parsed["native_media_verified"], false);
                assert_eq!(parsed["automatic_mutating_retry"], false);
                assert_eq!(
                    terminal.to_string(),
                    "native-cross-app-recovery-e2e failed; typed failure emitted on stdout"
                );
            }
        }
    }

    #[test]
    fn secondary_receipts_and_exclusive_retention_failure_cannot_strengthen_unknown() {
        let error = Error::new(ErrorCode::BackendFailed, PRIVATE).uncertain();
        let receipts = [
            receipt(
                "driver.blender.export.glb",
                "completed",
                json!({ "job_ref": PRIVATE }),
            ),
            receipt(
                "driver.motion-canvas.render.status",
                "failed_known",
                json!({ "error": {
                    "code": "Unavailable", "outcome_known": true,
                    "message": format!("Provider capability generation is inactive {PRIVATE}"),
                }}),
            ),
            receipt(PRIVATE, PRIVATE, json!({ "error": { "code": PRIVATE } })),
        ];
        let temporary = tempfile::tempdir().unwrap();
        let diagnostic = temporary.path().join("existing-evidence.json");
        std::fs::write(&diagnostic, b"existing evidence must remain unchanged").unwrap();
        let mut stdout = Vec::new();
        let terminal = emit_native_failure(
            &mut stdout,
            Some(&diagnostic),
            "resume",
            &error,
            &receipts,
            NativeFailureObservation::NativeCall,
        );
        let parsed = complete_response(&stdout);
        assert_eq!(parsed["error"]["code"], "BackendFailed");
        assert_eq!(parsed["error"]["outcome_known"], false);
        assert_eq!(parsed["receipts"].as_array().unwrap().len(), 2);
        assert_eq!(
            parsed["receipts"][1]["payload"]["error"]["code"],
            "Unavailable"
        );
        assert_eq!(
            std::fs::read(&diagnostic).unwrap(),
            b"existing evidence must remain unchanged"
        );
        assert!(!terminal.to_string().contains(PRIVATE));
        // Receipt read failure is an empty observation, never a new SDK result.
        let unobserved = native_failure_envelope(
            &error,
            &[],
            "resume",
            NativeFailureObservation::ReceiptCollectionFailed,
        );
        assert_eq!(unobserved["error"], parsed["error"]);
        assert_eq!(unobserved["observation"], "receipt_collection_failed");
    }

    #[test]
    fn expected_font_failure_gate_never_accepts_unknown_or_other_native_error() {
        assert!(is_expected_font_evidence_failure(&Error::new(
            ErrorCode::BackendFailed,
            FONT_FAILURE,
        )));
        for error in [
            Error::new(ErrorCode::BackendFailed, FONT_FAILURE).uncertain(),
            Error::new(ErrorCode::Unavailable, FONT_FAILURE),
            Error::new(
                ErrorCode::BackendFailed,
                format!("{FONT_FAILURE} {PRIVATE}"),
            ),
            Error::new(ErrorCode::BackendFailed, "different native failure"),
        ] {
            assert!(!is_expected_font_evidence_failure(&error));
        }
    }

    #[test]
    fn failed_expected_failure_retention_still_reports_the_original_native_result() {
        let error = Error::new(ErrorCode::BackendFailed, FONT_FAILURE);
        let temporary = tempfile::tempdir().unwrap();
        let diagnostic = temporary.path().join("absent-parent/evidence.json");
        assert!(
            private_evidence(&diagnostic, &json!({ "expected_native_failure": true })).is_err()
        );
        let mut stdout = Vec::new();
        let _terminal = emit_native_failure(
            &mut stdout,
            None,
            "fail",
            &error,
            &[],
            NativeFailureObservation::EvidenceWriteFailed,
        );
        let parsed = complete_response(&stdout);
        assert_eq!(parsed["native_cross_app_recovery_e2e"], "FAIL");
        assert_eq!(parsed["error"]["code"], "BackendFailed");
        assert_eq!(parsed["error"]["outcome_known"], true);
        assert_eq!(parsed["observation"], "evidence_write_failed");
        assert!(!diagnostic.exists());
    }
}
