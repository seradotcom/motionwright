//! One cancellation of the ordinary production realization.
//! The outer owner separately proves process closure before broker cleanup.
use motionwright_domain::{RendererKind, RevisionStamp};
use motionwright_manim_profile::ManimRenderProfile;
use motionwright_native::production::{
    ManimRuntimeVersionState, ProductionConnection, ProductionCoordinator,
};
use motionwright_service::StudioService;
use semwright_native_sdk::ErrorCode;
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::OpenOptions,
    io::{Read, Write},
    path::Path,
    time::Duration,
};
use tokio::time::{Instant, sleep};
use uuid::Uuid;

type Failure = Box<dyn std::error::Error>;
const START: &str = "driver.manim-community.editable.render.start";
const CANCEL: &str = "driver.manim-community.render.cancel";
const STATUS: &str = "driver.manim-community.render.status";
const RESULT: &str = "driver.manim-community.render.result";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Control {
    schema: String,
    action: String,
    job_ref: String,
    source_file: String,
    plan_sha256: String,
    witness_sha256: String,
}

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn retain(path: &Path, value: &Value) -> Result<(), Failure> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    if bytes.len() > 2 * 1024 * 1024 {
        return Err("cancel original exceeds its custody bound".into());
    }
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    Ok(())
}

fn result_value<T: serde::Serialize>(result: &Result<T, semwright_native_sdk::Error>) -> Value {
    match result {
        Ok(value) => json!({"result": value, "error": null}),
        Err(error) => json!({"result": null, "error": error}),
    }
}

fn retained_result<T: serde::Serialize>(
    path: &Path,
    original: Result<T, semwright_native_sdk::Error>,
) -> Result<T, Failure> {
    let retained = retain(path, &result_value(&original));
    match original {
        Err(primary) => {
            if let Err(secondary) = retained {
                eprintln!("cancel original custody secondary: {secondary}");
            }
            Err(primary.into())
        }
        Ok(value) => {
            retained?;
            Ok(value)
        }
    }
}

struct OriginalJob {
    reference: String,
    source_file: String,
    generation: u64,
    plan: String,
    profile: Value,
}

impl OriginalJob {
    fn from_start(value: &Value, plan: &str, profile: &Value) -> Result<Self, Failure> {
        let data = value
            .pointer("/result/data")
            .ok_or("original start has no data")?;
        let reference = data["job_ref"]
            .as_str()
            .ok_or("original start has no job")?;
        let token = reference
            .strip_prefix("manim-")
            .ok_or("original job kind differs")?;
        if token.len() != 32
            || !token
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("original job token differs".into());
        }
        let generation = value
            .pointer("/result/execution/provenance/provider_generation")
            .and_then(Value::as_u64)
            .ok_or("original provider generation absent")?;
        let job = Self {
            reference: reference.into(),
            source_file: format!("mw_{token}.py"),
            generation,
            plan: plan.into(),
            profile: profile.clone(),
        };
        if job.observation(value)?["state"] != "rendering" {
            return Err("original start did not acknowledge rendering".into());
        }
        Ok(job)
    }

    fn observation<'a>(&self, value: &'a Value) -> Result<&'a Value, Failure> {
        let data = value
            .pointer("/result/data")
            .ok_or("original observation has no data")?;
        if value["replayed"] != false
            || value["result"]["provenance_checked"] != true
            || value["result"]["execution"]["backend"] != "driver:manim-community"
            || value["result"]["execution"]["dry_run"] != false
            || value["result"]["execution"]["policy_decision"] != "allow"
            || value["result"]["execution"]["fallbacks_attempted"] != json!([])
            || value["result"]["execution"]["provenance"]["provider"] != "driver:manim-community"
            || value["result"]["execution"]["provenance"]["execution_provider"]
                != "driver:manim-community"
            || value["result"]["execution"]["provenance"]["source"] != "driver"
            || data["job_ref"] != self.reference
            || data["source_file"] != self.source_file
            || data["plan_sha256"] != self.plan
            || data["profile"] != self.profile
            || value
                .pointer("/result/execution/provenance/provider_generation")
                .and_then(Value::as_u64)
                != Some(self.generation)
        {
            return Err(
                "observation lost the acknowledged job/source/generation/plan/profile".into(),
            );
        }
        Ok(data)
    }

    fn control(&self, raw: &[u8]) -> Result<Control, Failure> {
        if raw.is_empty() || raw.len() > 8192 {
            return Err("cancel control is absent or exceeds its bound".into());
        }
        let control: Control = serde_json::from_slice(raw)?;
        if control.schema != "motionwright.manim.active-cancel-control/1"
            || control.action != "cancel"
            || control.job_ref != self.reference
            || control.source_file != self.source_file
            || control.plan_sha256 != self.plan
            || control.witness_sha256.len() != 64
            || !control
                .witness_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("cancel control does not identify this original job".into());
        }
        Ok(control)
    }
}

struct Selected<'a> {
    coordinator: &'a ProductionCoordinator,
    project: Uuid,
    expected: &'a RevisionStamp,
    scene: Uuid,
    profile: ManimRenderProfile,
    plan_sha256: &'a str,
    request_id: &'a str,
    evidence: &'a Path,
}

async fn attempt(selected: Selected<'_>, record: &mut Value) -> Result<(), Failure> {
    let Selected {
        coordinator,
        project,
        expected,
        scene,
        profile,
        plan_sha256,
        request_id,
        evidence,
    } = selected;
    let expected_profile = json!(profile);
    // Preserve all ordinary saved-source, scene, font and Native admission.
    let rendering = coordinator.realize_manim_scene(project, expected, request_id, scene, profile);
    tokio::pin!(rendering);
    let start_id = format!("{request_id}:start");
    let acknowledge_by = Instant::now() + Duration::from_secs(30);
    let start_receipt = loop {
        tokio::select! {
            terminal = &mut rendering => {
                record["render_terminal"] = result_value(&terminal);
                return Err("original realization ended before active cancellation could be observed".into());
            }
            () = sleep(Duration::from_millis(20)) => {
                if Instant::now() >= acknowledge_by {
                    return Err("original start acknowledgement exceeded its bound".into());
                }
                let receipts = coordinator.receipts(project, 256)?;
                if let Some(receipt) = receipts.into_iter()
                    .find(|receipt| receipt.request_id == start_id && receipt.stage == "completed")
                {
                    if receipt.project_id != project || receipt.generation != expected.generation
                        || receipt.revision != expected.revision || receipt.command != START
                    {
                        return Err("original start receipt has a foreign source identity".into());
                    }
                    break receipt;
                }
            }
        }
    };
    record["start_receipt"] = json!(start_receipt);
    let job = OriginalJob::from_start(&start_receipt.payload, plan_sha256, &expected_profile)?;
    retain(
        &evidence.with_file_name("cancel-start.original.json"),
        &json!({
            "schema": "motionwright.manim.active-cancel-start/1",
            "project_id": project, "generation": expected.generation, "revision": expected.revision,
            "scene_id": scene, "request_id": request_id, "job_ref": job.reference,
            "source_file": job.source_file, "plan_sha256": job.plan, "profile": job.profile,
            "provider_generation": job.generation, "start_receipt": start_receipt,
        }),
    )?;

    // The owner writes one bounded control and closes this pipe. A standard
    // reader thread cannot hold Tokio shutdown alive after an early terminal.
    let (sender, mut receiver) = tokio::sync::oneshot::channel();
    let _control_reader = std::thread::spawn(move || {
        let mut raw = Vec::new();
        let read = std::io::stdin().lock().take(8193).read_to_end(&mut raw);
        let _ = sender.send(read.map(|_| raw));
    });
    let received = tokio::select! {
        terminal = &mut rendering => {
            record["render_terminal"] = result_value(&terminal);
            return Err("original realization completed before observed cancellation control".into());
        }
        control = &mut receiver => control.map_err(|_| "cancel control reader closed")?,
        () = sleep(Duration::from_secs(30)) => {
            return Err("physical cancellation witness was not supplied before its bound".into());
        }
    }?;
    record["control_original_sha256"] = json!(sha(&received));
    let control = job.control(&received)?;
    record["physical_witness_sha256"] = json!(control.witness_sha256);

    // One cancellation only, including on uncertain or failed response.
    let cancelled = coordinator
        .execute(
            project,
            expected,
            &format!("{request_id}:cancel"),
            CANCEL,
            json!({"job_ref": job.reference}),
            true,
        )
        .await;
    record["cancel_response"] = result_value(&cancelled);
    let cancel_response = retained_result(
        &evidence.with_file_name("cancel-response.original.json"),
        cancelled,
    )?;
    let data = job.observation(&cancel_response)?;
    if !matches!(data["state"].as_str(), Some("cancelled" | "cancelling"))
        || !data["artifact"].is_null()
    {
        if let Ok(terminal) = tokio::time::timeout(Duration::from_secs(60), &mut rendering).await {
            record["render_terminal"] = result_value(&terminal);
        }
        return Err(
            "cancel retained a different original outcome; active cancellation is unproven".into(),
        );
    }

    let terminal = tokio::time::timeout(Duration::from_secs(60), &mut rendering)
        .await
        .map_err(|_| "original realization terminal was not observed after cancellation")?;
    record["render_terminal"] = result_value(&terminal);
    let terminal_custody = retain(
        &evidence.with_file_name("cancel-render-terminal.original.json"),
        &record["render_terminal"],
    );
    match terminal {
        Err(error) if error.code == ErrorCode::Cancelled && error.outcome_known => {
            terminal_custody?;
        }
        Err(primary) => {
            if let Err(secondary) = terminal_custody {
                eprintln!("cancel original custody secondary: {secondary}");
            }
            return Err(primary.into());
        }
        Ok(_) => {
            terminal_custody?;
            return Err("ordinary realization did not retain a known cancelled terminal".into());
        }
    }

    let status = coordinator
        .execute(
            project,
            expected,
            &format!("{request_id}:cancel-status"),
            STATUS,
            json!({"job_ref": job.reference}),
            false,
        )
        .await;
    record["post_cancel_status"] = result_value(&status);
    let status_response = status?;
    let data = job.observation(&status_response)?;
    if data["state"] != "cancelled" || !data["artifact"].is_null() {
        return Err("post-cancel status exposed a different state or artifact".into());
    }
    let result = coordinator
        .execute(
            project,
            expected,
            &format!("{request_id}:cancel-result"),
            RESULT,
            json!({"job_ref": job.reference}),
            false,
        )
        .await;
    record["post_cancel_result"] = result_value(&result);
    let result_response = result?;
    let data = job.observation(&result_response)?;
    if data["state"] != "cancelled"
        || data.get("artifact") != Some(&Value::Null)
        || data.get("original_error") != Some(&Value::Null)
    {
        return Err("cancelled original result was publishable or inconsistent".into());
    }
    record["cancellation_exercised"] = json!(true);
    // No process terminal or descendant closure is inferred from these states.
    Ok(())
}

pub async fn run(
    database: &Path,
    connection_path: &Path,
    evidence: &Path,
    request_id: &str,
) -> Result<(), Failure> {
    if request_id.is_empty()
        || request_id.len() > 80
        || request_id.chars().any(char::is_control)
        || evidence.exists()
        || !evidence.parent().is_some_and(Path::is_dir)
    {
        return Err("cancel original requires a fresh bounded identity and evidence path".into());
    }
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("cancel original requires one saved project".into());
    }
    let project = projects.into_iter().next().ok_or("saved project absent")?;
    if project.scenes.len() != 1 || project.scenes[0].renderer != RendererKind::ManimCommunity {
        return Err("cancel original requires the selected saved Manim scene".into());
    }
    let scene = project.scenes[0].id;
    let expected = RevisionStamp::from(&project);
    if service
        .latest_production_receipt(project.id, &format!("{request_id}:start"))?
        .is_some()
    {
        return Err("cancel original start identity already exists; no replay".into());
    }
    let profile = ManimRenderProfile {
        width: 640,
        height: 360,
        frame_rate: 12,
    };
    let deliverable =
        motionwright_native::manim_static_projection::select_saved_manim_deliverable_for_profile(
            &project, &expected, scene, &profile,
        )?;
    let prepared =
        motionwright_native::manim_editable_projection::prepare_saved_manim_editable_source(
            &service,
            project.id,
            &expected,
            scene,
            deliverable,
        )?;
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("cancel connection is not bound to the saved project".into());
    }
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let runtime = coordinator
        .observe_manim_runtime_version(project.id, &expected)
        .await;
    let observed = retained_result(
        &evidence.with_file_name("cancel-runtime-observation.original.json"),
        runtime,
    )?;
    if observed.state() != ManimRuntimeVersionState::ObservedCompatible {
        return Err("cancel original runtime was not observed compatible".into());
    }
    let mut record = json!({
        "schema": "motionwright.manim.active-cancel-original/2",
        "project_before": project, "scene_id": scene, "deliverable_id": deliverable,
        "request_id": request_id, "plan_sha256": prepared.closure.plan_sha256,
        "runtime_observation": observed, "cancellation_exercised": false,
        "physical_process_closure_claimed": false, "human_acceptance": false,
        "formal_acceptance": false, "automatic_retry": false,
    });
    let attempted = attempt(
        Selected {
            coordinator: &coordinator,
            project: project.id,
            expected: &expected,
            scene,
            profile,
            plan_sha256: &prepared.closure.plan_sha256,
            request_id,
            evidence,
        },
        &mut record,
    )
    .await;
    record["protocol_error"] = json!(attempted.as_ref().err().map(|error| error.to_string()));
    let receipts = coordinator.receipts(project.id, 256);
    record["receipts"] = json!(receipts.as_ref().ok());
    record["receipt_read_error"] = json!(receipts.as_ref().err());
    let after = service.project(project.id);
    record["project_after"] = json!(after.as_ref().ok());
    record["project_read_error"] = json!(after.as_ref().err().map(|error| error.to_string()));
    let unchanged = after.as_ref().is_ok_and(|after| after == &project);
    record["project_unchanged"] = json!(unchanged);
    let retained = retain(evidence, &record);
    if let Err(error) = attempted {
        if let Err(secondary) = retained {
            eprintln!("cancel original custody secondary: {secondary}");
        }
        return Err(error);
    }
    retained?;
    receipts?;
    if !unchanged {
        return Err("cancelled realization changed the saved project".into());
    }
    println!(
        "{}",
        json!({
            "native_manim_cancel_protocol": "CANCELLED_WITH_UNPUBLISHABLE_RESULT",
            "project_id": project.id, "generation": project.generation, "revision": project.revision,
            "scene_id": scene, "deliverable_id": deliverable, "request_id": request_id,
            "physical_process_closure_claimed": false, "formal_acceptance": false,
        })
    );
    Ok(())
}
