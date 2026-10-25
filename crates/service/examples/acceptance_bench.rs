#[path = "responsiveness_bench.rs"]
mod reference_profiles;

use motionwright_domain::{
    Change, LockKind, RationalTime, RendererKind, RevisionStamp, SceneStatus,
    variant_dependency_fingerprint,
};
use motionwright_service::{DerivedCacheHit, StudioService};
use motionwright_storage::{Result as StorageResult, StorageError};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{env, fs, io::Write, path::Path, time::Instant};
use uuid::Uuid;

#[derive(Clone, Copy)]
struct BriefSpec {
    slug: &'static str,
    title: &'static str,
    objective: &'static str,
}

#[derive(Serialize)]
struct OperationMetric {
    name: String,
    micros: u128,
}

#[derive(Default, Serialize)]
struct CachePhaseMetric {
    phase: &'static str,
    outcome: &'static str,
    fingerprint_sha256: String,
    lookup_attempts: u64,
    hits: u64,
    misses: u64,
    rejects: u64,
    constructor_calls: u64,
    constructor_scene_records: u64,
    payload_bytes_read: u64,
    validated_bytes: u64,
    fingerprint_micros: u128,
    lookup_micros: u128,
    construction_micros: u128,
    publication_micros: u128,
    read_decode_compare_micros: u128,
    validation_micros: u128,
    total_micros: u128,
    error: Option<String>,
}

struct CacheUse {
    metric: CachePhaseMetric,
    result: StorageResult<(DerivedCacheHit, Vec<u8>)>,
}

#[derive(Serialize)]
struct CacheCorruptionControl {
    scope: &'static str,
    artifact_directory: String,
    original_source: String,
    damaged_blob: String,
    original_sha256: String,
    damaged_sha256: String,
    byte_offset: usize,
    before_byte: u8,
    after_byte: u8,
    bytes_unchanged_after_rejection: bool,
    setup: CachePhaseMetric,
}

#[derive(Serialize)]
struct CacheMetric {
    scope: String,
    kind: &'static str,
    fingerprint_sha256: String,
    stored_bytes: u64,
    validated_bytes: u64,
    store_micros: u128,
    validation_micros: u128,
    avoided_work_units: u64,
    consumer_schema: &'static str,
    consumer_scope: &'static str,
    input_project_before: String,
    input_project_changed: String,
    phases: Vec<CachePhaseMetric>,
    corruption_control: CacheCorruptionControl,
}

#[derive(Serialize)]
struct DatasetMetric {
    artifact_directory: String,
    brief: String,
    size: String,
    scene_count: usize,
    fresh_store_open_micros: u128,
    fresh_project_create_micros: u128,
    first_editable_scene_micros: u128,
    first_scene_preserved_after_revision_ten: bool,
    seed_micros: u128,
    revision_start: u64,
    revision_end: u64,
    revision_delta: u64,
    revision_ten_micros: u128,
    max_commit_micros: u128,
    reopen_micros: u128,
    event_count: usize,
    database_bytes: u64,
    operations: Vec<OperationMetric>,
    cache: CacheMetric,
}

#[derive(Serialize)]
struct CacheEvidence {
    implemented: bool,
    consumer_schema: &'static str,
    consumer_scope: &'static str,
    aggregate_population: &'static str,
    consumer_requests: usize,
    all_phase_lookup_attempts: u64,
    all_phase_hits: u64,
    all_phase_misses: u64,
    all_phase_rejects: u64,
    all_phase_constructor_calls: u64,
    control_setup_requests: usize,
    control_setup_lookup_attempts: u64,
    control_setup_constructor_calls: u64,
    lookup_attempts: usize,
    hits: usize,
    avoided_work_count: u64,
    work_unit: &'static str,
    validated_bytes: u64,
    validation_micros: u128,
    note: &'static str,
}

#[derive(Serialize)]
struct BenchmarkReport {
    dataset_family: &'static str,
    normative_reference: bool,
    source_attribution_verified: bool,
    source_binding_consistent: bool,
    source_context: serde_json::Value,
    artifact_directory: String,
    schema: &'static str,
    source_sha: String,
    generated_by: &'static str,
    latency_budget_ms: u128,
    briefs: usize,
    datasets: usize,
    results: Vec<DatasetMetric>,
    cache_evidence: CacheEvidence,
    human_feedback: &'static str,
    comparative_claims: &'static str,
}

fn commit(
    service: &StudioService,
    project: &mut motionwright_domain::Project,
    label: &str,
    change: Change,
) -> u128 {
    let started = Instant::now();
    let request_id = format!("bench-{label}-{}", Uuid::now_v7());
    *project = service
        .apply(
            project.id,
            &RevisionStamp::from(&*project),
            &request_id,
            &change,
        )
        .unwrap_or_else(|error| panic!("{label} failed: {error}"))
        .project;
    started.elapsed().as_micros()
}

fn database_bytes(path: &Path) -> u64 {
    fs::metadata(path)
        .map(|value| value.len())
        .unwrap_or_default()
}

fn cache_require(condition: bool, message: &str) -> StorageResult<()> {
    if condition {
        Ok(())
    } else {
        Err(StorageError::InvalidDerivedCache(message.into()))
    }
}

fn payload_digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

// Increment at the work itself, never from scene_count or cache metadata.
fn build_preview_index(
    project: &motionwright_domain::Project,
    profile_id: Uuid,
    fingerprint: &str,
    metric: &mut CachePhaseMetric,
) -> StorageResult<Vec<u8>> {
    metric.constructor_calls += 1;
    let mut scenes = Vec::new();
    for scene in &project.scenes {
        metric.constructor_scene_records += 1;
        scenes.push(serde_json::json!({
            "id": scene.id, "name": &scene.name, "objective": &scene.objective,
            "start": &scene.start, "duration": &scene.duration,
            "renderer": &scene.renderer, "status": &scene.status,
        }));
    }
    Ok(serde_json::to_vec(&serde_json::json!({
        "schema": "motionwright.preview-index.v1",
        "project_id": project.id, "generation": project.generation,
        "revision": project.revision, "profile_id": profile_id,
        "master_inputs_sha256": fingerprint, "scenes": scenes,
    }))?)
}

fn observed_cache_lookup(
    service: &StudioService,
    scope: &str,
    fingerprint: &str,
    metric: &mut CachePhaseMetric,
) -> StorageResult<Option<DerivedCacheHit>> {
    metric.lookup_attempts += 1;
    let started = Instant::now();
    let result = service.lookup_derived_cache(scope, "preview-index", fingerprint);
    metric.lookup_micros += started.elapsed().as_micros();
    match &result {
        Ok(Some(hit)) => {
            metric.hits += 1;
            // Payload size admitted by this API; not a count of physical I/O.
            metric.validated_bytes += hit.validated_bytes;
        }
        Ok(None) => metric.misses += 1,
        Err(_) => metric.rejects += 1,
    }
    result
}

// Benchmark consumer only. Its expected bytes are a retained MISS oracle.
// Every request times fingerprint -> lookup/build/publication -> read/decode/
// byte comparison, including all validation. No speedup threshold is asserted.
fn consume_preview_index(
    service: &StudioService,
    project: &motionwright_domain::Project,
    profile_id: Uuid,
    root: &Path,
    phase: &'static str,
    retained_expected: Option<&[u8]>,
) -> CacheUse {
    let total_started = Instant::now();
    let mut metric = CachePhaseMetric {
        phase,
        ..CachePhaseMetric::default()
    };
    let result = (|| -> StorageResult<(DerivedCacheHit, Vec<u8>)> {
        let started = Instant::now();
        let dependency = variant_dependency_fingerprint(project, profile_id);
        metric.fingerprint_micros = started.elapsed().as_micros();
        let fingerprint = dependency?.master_inputs_sha256;
        metric.fingerprint_sha256 = fingerprint.clone();
        let scope = format!("project:{}:generation:{}", project.id, project.generation);
        let mut constructed = None;
        let hit = match observed_cache_lookup(service, &scope, &fingerprint, &mut metric)? {
            Some(hit) => {
                metric.outcome = "HIT";
                hit
            }
            None => {
                metric.outcome = "MISS_BUILT";
                let started = Instant::now();
                let payload = build_preview_index(project, profile_id, &fingerprint, &mut metric);
                metric.construction_micros = started.elapsed().as_micros();
                let payload = payload?;
                let started = Instant::now();
                let published = (|| -> StorageResult<_> {
                    let source = root.join(format!("{phase}-preview-index.json"));
                    let mut output = fs::OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(&source)?;
                    output.write_all(&payload)?;
                    output.sync_all()?;
                    drop(output);
                    service.put_derived_cache_file(
                        &scope,
                        "preview-index",
                        &fingerprint,
                        &source,
                        metric.constructor_scene_records,
                    )
                })();
                metric.publication_micros = started.elapsed().as_micros();
                let stored = published?;
                constructed = Some(payload);
                let hit = observed_cache_lookup(service, &scope, &fingerprint, &mut metric)?
                    .ok_or_else(|| {
                        StorageError::InvalidDerivedCache(
                            "published preview index disappeared".into(),
                        )
                    })?;
                cache_require(hit.record == stored, "published preview record changed")?;
                hit
            }
        };
        let started = Instant::now();
        let consumed = (|| -> StorageResult<Vec<u8>> {
            let bytes = fs::read(&hit.blob_path)?;
            metric.payload_bytes_read = bytes.len() as u64;
            cache_require(
                bytes.len() as u64 == hit.record.size_bytes,
                "consumed preview size differs",
            )?;
            cache_require(
                payload_digest(&bytes) == hit.record.blob_sha256,
                "consumed preview digest differs after lookup",
            )?;
            let value: serde_json::Value = serde_json::from_slice(&bytes)?;
            cache_require(
                value["schema"] == "motionwright.preview-index.v1"
                    && value["project_id"] == project.id.to_string()
                    && value["generation"] == project.generation.to_string()
                    && value["revision"] == project.revision
                    && value["profile_id"] == profile_id.to_string()
                    && value["master_inputs_sha256"] == fingerprint,
                "consumed preview owner, revision or fingerprint differs",
            )?;
            let expected = constructed
                .as_deref()
                .or(retained_expected)
                .ok_or_else(|| {
                    StorageError::InvalidDerivedCache(
                        "warm preview lacks a retained cold-construction oracle".into(),
                    )
                })?;
            cache_require(
                bytes == expected,
                "consumed preview differs from retained construction",
            )?;
            Ok(bytes)
        })();
        metric.read_decode_compare_micros = started.elapsed().as_micros();
        Ok((hit, consumed?))
    })();
    metric.validation_micros = metric.lookup_micros + metric.read_decode_compare_micros;
    if let Err(error) = &result {
        metric.outcome = "ERROR";
        metric.error = Some(error.to_string());
    }
    metric.total_micros = total_started.elapsed().as_micros();
    // Custody serialization is outside the consistently measured consumer interval.
    let observation = serde_json::to_vec_pretty(&metric).expect("encode cache observation");
    let mut receipt = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(root.join(format!("{phase}-observation.json")))
        .expect("retain original cache observation");
    receipt
        .write_all(&observation)
        .expect("write original cache observation");
    receipt.sync_all().expect("sync original cache observation");
    CacheUse { metric, result }
}

fn observed_warm_skip(cold: &CachePhaseMetric, warm: &CachePhaseMetric) -> u64 {
    assert_eq!(cold.outcome, "MISS_BUILT");
    assert_eq!(cold.constructor_calls, 1);
    assert!(cold.constructor_scene_records > 0);
    assert_eq!(
        (cold.lookup_attempts, cold.hits, cold.misses, cold.rejects),
        (2, 1, 1, 0)
    );
    assert_eq!(warm.outcome, "HIT");
    assert_eq!(
        (warm.lookup_attempts, warm.hits, warm.misses, warm.rejects),
        (1, 1, 0, 0)
    );
    assert_eq!(warm.constructor_calls, 0);
    assert_eq!(warm.constructor_scene_records, 0);
    assert_eq!(cold.fingerprint_sha256, warm.fingerprint_sha256);
    cold.constructor_scene_records - warm.constructor_scene_records
}

fn assert_digest_rejection(observation: &CacheUse) {
    assert!(
        matches!(&observation.result, Err(StorageError::InvalidBackup(message))
            if message == "content-addressed blob failed digest verification"),
        "corrupt fixture must fail at physical digest verification, not a generic error"
    );
    assert_eq!(observation.metric.outcome, "ERROR");
    assert_eq!(observation.metric.lookup_attempts, 1);
    assert_eq!(observation.metric.rejects, 1);
    assert_eq!(observation.metric.hits, 0);
    assert_eq!(observation.metric.misses, 0);
    assert_eq!(observation.metric.constructor_calls, 0);
    assert_eq!(observation.metric.constructor_scene_records, 0);
    assert_eq!(observation.metric.payload_bytes_read, 0);
}

fn run_dataset(brief: BriefSpec, size: &str, scene_count: usize) -> DatasetMetric {
    let temp = reference_profiles::support::retained_directory(&format!(
        "historical-{}-{size}",
        brief.slug
    ))
    .expect("retained historical benchmark directory");
    let db_path = temp.join("motionwright.sqlite3");
    let open_started = Instant::now();
    let service = StudioService::open(&db_path).expect("benchmark store");
    let fresh_store_open_micros = open_started.elapsed().as_micros();
    let create_started = Instant::now();
    let mut project = service
        .create_project(&format!("{} · {size}", brief.title))
        .expect("benchmark project");
    let fresh_project_create_micros = create_started.elapsed().as_micros();

    // This starts AFTER project creation, not at user installation or human
    // decision time. It is strictly a first EDITABLE SCENE proxy.
    let seed_started = Instant::now();
    let mut first_editable_scene_micros = None;
    commit(
        &service,
        &mut project,
        "brief",
        Change::SetBrief {
            objective: brief.objective.into(),
            audience: "General public".into(),
            constraints: vec!["Preserve source truth".into()],
            exclusions: vec!["No fabricated measurements".into()],
        },
    );
    for index in 0..scene_count {
        commit(
            &service,
            &mut project,
            &format!("seed-scene-{index}"),
            Change::AddScene {
                name: format!("{} scene {:03}", brief.slug, index + 1),
                objective: format!(
                    "Explain beat {:03} without backend-specific assumptions.",
                    index + 1
                ),
                duration_seconds: 4 + (index % 5) as i64,
            },
        );
        if index == 0 {
            assert_eq!(project.scenes.len(), 1);
            first_editable_scene_micros = Some(seed_started.elapsed().as_micros());
        }
    }
    let first_editable_scene_micros =
        first_editable_scene_micros.expect("the first original scene was saved");
    let seed_micros = seed_started.elapsed().as_micros();
    assert!(first_editable_scene_micros <= seed_micros);
    let first_scene_id = project.scenes[0].id;
    let revision_start = project.revision;

    let scene_a = project.scenes[0].id;
    let scene_b = project.scenes[project.scenes.len().saturating_sub(1).min(1)].id;
    let project_resource = project.resource_key();
    let mut operations = Vec::with_capacity(10);
    let revision_ten_started = Instant::now();

    {
        let mut record = |name: &str, change: Change| {
            let micros = commit(&service, &mut project, name, change);
            operations.push(OperationMetric {
                name: name.into(),
                micros,
            });
        };

        record(
            "01-rename",
            Change::RenameProject {
                title: format!("{} · {size} · revision ten", brief.title),
            },
        );
        record(
            "02-narrative-premise",
            Change::SetNarrativePremise {
                premise: "Maintain semantic intent while the project accumulates revisions.".into(),
            },
        );
        record(
            "03-scene-objective",
            Change::UpdateSceneObjective {
                scene_id: scene_a,
                objective: "Revision-ten objective remains editable and renderer independent."
                    .into(),
            },
        );
        record(
            "04-scene-status",
            Change::SetSceneStatus {
                scene_id: scene_a,
                status: SceneStatus::Review,
            },
        );
        record(
            "05-ripple-duration",
            Change::SetSceneDuration {
                scene_id: scene_a,
                duration: RationalTime { num: 7, den: 1 },
            },
        );
        record(
            "06-second-renderer",
            Change::SetSceneRenderer {
                scene_id: scene_b,
                renderer: RendererKind::Blender,
            },
        );
        record(
            "07-marker",
            Change::AddMarker {
                at: RationalTime { num: 3, den: 1 },
                label: "Revision ten review".into(),
            },
        );
        record(
            "08-project-lock",
            Change::SetLock {
                resource: project_resource,
                kind: LockKind::Style,
                note: "Benchmark lock round trip".into(),
            },
        );
    }
    let lock_id = project
        .locks
        .iter()
        .find(|lock| lock.note == "Benchmark lock round trip")
        .expect("benchmark lock")
        .id;

    let unlock_micros = commit(
        &service,
        &mut project,
        "09-project-unlock",
        Change::RemoveLock { lock_id },
    );
    operations.push(OperationMetric {
        name: "09-project-unlock".into(),
        micros: unlock_micros,
    });
    let final_micros = commit(
        &service,
        &mut project,
        "10-final-objective",
        Change::UpdateSceneObjective {
            scene_id: scene_a,
            objective: "Tenth predefined change persisted after reopen.".into(),
        },
    );
    operations.push(OperationMetric {
        name: "10-final-objective".into(),
        micros: final_micros,
    });

    let revision_ten_micros = revision_ten_started.elapsed().as_micros();
    let revision_end = project.revision;
    assert_eq!(
        revision_end - revision_start,
        10,
        "revision-ten sequence must commit exactly ten changes"
    );

    let max_commit_micros = operations
        .iter()
        .map(|entry| entry.micros)
        .max()
        .unwrap_or(0);
    let event_count = service
        .history(project.id, 0, 100_000)
        .expect("benchmark history")
        .len();
    drop(service);

    let reopen_started = Instant::now();
    let reopened_service = StudioService::open(&db_path).expect("reopen benchmark store");
    let reopened = reopened_service
        .project(project.id)
        .expect("reopen benchmark project");
    let reopen_micros = reopen_started.elapsed().as_micros();
    assert_eq!(reopened.revision, revision_end);
    assert_eq!(
        reopened.scenes[0].objective,
        "Tenth predefined change persisted after reopen."
    );
    assert_eq!(reopened.scenes.len(), scene_count);
    assert_eq!(
        reopened.scenes[0].id, first_scene_id,
        "the exact first editable scene survives revision ten and SQLite reopen"
    );

    let profile_id = reopened
        .deliverables
        .first()
        .expect("benchmark project has a default deliverable")
        .id;
    let scope = format!("project:{}:generation:{}", reopened.id, reopened.generation);
    // Retain the complete input outside the measured consumer interval. The
    // independent reader must compare this original with the closed main DB.
    let input_project_before = temp.join("cache-input-project.json");
    fs::write(
        &input_project_before,
        serde_json::to_vec_pretty(&reopened).expect("encode original cache input"),
    )
    .expect("retain original cache input");
    let cold = consume_preview_index(
        &reopened_service,
        &reopened,
        profile_id,
        &temp,
        "cold",
        None,
    );
    let (cold_hit, cold_payload) = cold.result.expect("cold cache consumer");

    let warm = consume_preview_index(
        &reopened_service,
        &reopened,
        profile_id,
        &temp,
        "warm",
        Some(&cold_payload),
    );
    let (warm_hit, _) = warm.result.expect("warm cache consumer");
    assert_eq!(warm_hit.record, cold_hit.record);
    let avoided_work_units = observed_warm_skip(&cold.metric, &warm.metric);

    let mut changed = reopened.clone();
    changed.deliverables[0].width = changed.deliverables[0].width.saturating_sub(2);
    // This input is a private in-memory variant, not a durable Project edit.
    let input_project_changed = temp.join("cache-changed-input-project.json");
    fs::write(
        &input_project_changed,
        serde_json::to_vec_pretty(&changed).expect("encode changed cache input"),
    )
    .expect("retain changed cache input");
    let changed_use = consume_preview_index(
        &reopened_service,
        &changed,
        profile_id,
        &temp,
        "changed",
        None,
    );
    let (_, changed_payload) = changed_use.result.expect("changed input cache consumer");
    assert_ne!(
        changed_use.metric.fingerprint_sha256,
        cold.metric.fingerprint_sha256
    );
    assert_eq!(changed_use.metric.outcome, "MISS_BUILT");
    assert_eq!(changed_use.metric.constructor_calls, 1);
    assert_ne!(changed_payload, cold_payload);

    // Keep the positive cache intact. This separate private store tests only
    // derived-cache integrity; it does not assert Project SQL ownership.
    let corruption_root = temp.join("cache-corrupt-control");
    fs::create_dir(&corruption_root).expect("create isolated corrupt-cache fixture");
    let corruption_service = StudioService::open(corruption_root.join("control.sqlite3"))
        .expect("open isolated corrupt-cache fixture");
    let prime = consume_preview_index(
        &corruption_service,
        &reopened,
        profile_id,
        &corruption_root,
        "corrupt-prime",
        None,
    );
    let (prime_hit, prime_payload) = prime.result.expect("prime isolated corrupt-cache fixture");
    assert_eq!(prime_payload, cold_payload);
    assert_eq!(prime_hit.record.blob_sha256, cold_hit.record.blob_sha256);
    let mut damaged = prime_payload;
    let before_byte = damaged[0];
    damaged[0] ^= 1;
    let after_byte = damaged[0];
    let damaged_sha256 = payload_digest(&damaged);
    assert_ne!(damaged_sha256, prime_hit.record.blob_sha256);
    fs::write(&prime_hit.blob_path, &damaged).expect("inject isolated physical corruption");
    let rejected = consume_preview_index(
        &corruption_service,
        &reopened,
        profile_id,
        &corruption_root,
        "corrupt",
        Some(&cold_payload),
    );
    assert_digest_rejection(&rejected);
    assert_eq!(
        fs::read(&prime_hit.blob_path).expect("read corrupt original after rejection"),
        damaged
    );
    let corruption_control = CacheCorruptionControl {
        scope: "isolated derived-cache integrity fixture; no Project SQL ownership claim",
        artifact_directory: corruption_root.display().to_string(),
        original_source: corruption_root
            .join("corrupt-prime-preview-index.json")
            .display()
            .to_string(),
        damaged_blob: prime_hit.blob_path.display().to_string(),
        original_sha256: prime_hit.record.blob_sha256,
        damaged_sha256,
        byte_offset: 0,
        before_byte,
        after_byte,
        bytes_unchanged_after_rejection: true,
        setup: prime.metric,
    };
    let cache = CacheMetric {
        scope,
        kind: "preview-index",
        fingerprint_sha256: cold.metric.fingerprint_sha256.clone(),
        stored_bytes: cold_hit.record.size_bytes,
        validated_bytes: warm_hit.validated_bytes,
        store_micros: cold.metric.publication_micros,
        validation_micros: warm.metric.validation_micros,
        avoided_work_units,
        consumer_schema: "motionwright.preview-cache-consumer.v2",
        consumer_scope: "historical benchmark preview-index consumer; not Studio or native renderer cache",
        input_project_before: input_project_before.display().to_string(),
        input_project_changed: input_project_changed.display().to_string(),
        phases: vec![
            cold.metric,
            warm.metric,
            changed_use.metric,
            rejected.metric,
        ],
        corruption_control,
    };

    DatasetMetric {
        artifact_directory: temp.display().to_string(),
        brief: brief.slug.into(),
        size: size.into(),
        scene_count,
        fresh_store_open_micros,
        fresh_project_create_micros,
        first_editable_scene_micros,
        first_scene_preserved_after_revision_ten: true,
        seed_micros,
        revision_start,
        revision_end,
        revision_delta: revision_end - revision_start,
        revision_ten_micros,
        max_commit_micros,
        reopen_micros,
        event_count,
        database_bytes: database_bytes(&db_path),
        operations,
        cache,
    }
}

fn main() -> std::process::ExitCode {
    match env::var("MOTIONWRIGHT_REFERENCE_PROFILES") {
        Ok(mode) if mode == "normative" => return reference_profiles::support::acceptance_main(),
        Ok(mode) if mode == "historical" => {}
        Err(env::VarError::NotPresent) => {}
        _ => {
            println!(
                "{}",
                serde_json::json!({
                    "schema": "motionwright.performance.configuration-error.v1",
                    "status": "FAIL",
                    "error": "MOTIONWRIGHT_REFERENCE_PROFILES must be normative or historical; unset it for the historical default"
                })
            );
            return std::process::ExitCode::FAILURE;
        }
    }
    let evidence = reference_profiles::support::retained_directory("historical-source")
        .expect("historical source evidence directory");
    let context_before = reference_profiles::support::context();
    let (sources_before, identity_before) =
        reference_profiles::support::retain_sources(&evidence.join("source-before"))
            .expect("retain historical benchmark sources before execution");
    let budget_ms = env::var("MOTIONWRIGHT_LATENCY_BUDGET_MS")
        .ok()
        .and_then(|value| value.parse::<u128>().ok())
        .unwrap_or(2_000);
    let briefs = [
        BriefSpec {
            slug: "harbor-safety",
            title: "Harbor Safety Explainer",
            objective: "Explain a harbor evacuation route with clear timing and signage.",
        },
        BriefSpec {
            slug: "museum-night",
            title: "Museum Night Campaign",
            objective: "Build a short cultural-event film with typography, image and review beats.",
        },
        BriefSpec {
            slug: "bike-route",
            title: "Bike Route Service Update",
            objective: "Communicate a city bike-route service change with maps, labels and variants.",
        },
    ];
    let sizes = [("S", 8_usize), ("M", 32_usize), ("L", 96_usize)];

    let mut results = Vec::with_capacity(briefs.len() * sizes.len());
    for brief in briefs {
        for (label, scene_count) in sizes {
            results.push(run_dataset(brief, label, scene_count));
        }
    }

    // Preserve the historical aggregate as the explicitly named warm cohort.
    // Cold/change/corruption requests and setup remain separately enumerated.
    let warm_phases: Vec<_> = results
        .iter()
        .map(|result| {
            result
                .cache
                .phases
                .iter()
                .find(|phase| phase.phase == "warm")
                .expect("one measured warm consumer request per dataset")
        })
        .collect();
    let cache_lookup_attempts = warm_phases
        .iter()
        .map(|phase| usize::try_from(phase.lookup_attempts).expect("lookup count"))
        .sum();
    let cache_hits = warm_phases
        .iter()
        .map(|phase| usize::try_from(phase.hits).expect("hit count"))
        .sum();
    let phases: Vec<_> = results
        .iter()
        .flat_map(|result| &result.cache.phases)
        .collect();
    let consumer_requests = phases.len();
    let all_phase_lookup_attempts = phases.iter().map(|phase| phase.lookup_attempts).sum();
    let all_phase_hits = phases.iter().map(|phase| phase.hits).sum();
    let all_phase_misses = phases.iter().map(|phase| phase.misses).sum();
    let all_phase_rejects = phases.iter().map(|phase| phase.rejects).sum();
    let all_phase_constructor_calls = phases.iter().map(|phase| phase.constructor_calls).sum();
    let control_setup_requests = results.len();
    let control_setup_lookup_attempts = results
        .iter()
        .map(|result| result.cache.corruption_control.setup.lookup_attempts)
        .sum();
    let control_setup_constructor_calls = results
        .iter()
        .map(|result| result.cache.corruption_control.setup.constructor_calls)
        .sum();
    let cache_avoided_work = results
        .iter()
        .map(|result| result.cache.avoided_work_units)
        .sum();
    let cache_validated_bytes = results
        .iter()
        .map(|result| result.cache.validated_bytes)
        .sum();
    let cache_validation_micros = results
        .iter()
        .map(|result| result.cache.validation_micros)
        .sum();

    let context_after = reference_profiles::support::context();
    let (sources_after, identity_after) =
        reference_profiles::support::retain_sources(&evidence.join("source-after"))
            .expect("retain historical benchmark sources after execution");
    let source_attribution_verified =
        reference_profiles::support::source_attributed(&context_before)
            && reference_profiles::support::source_attributed(&context_after)
            && context_before["source_sha_compiled"] == context_after["source_sha_compiled"]
            && context_before["executable"] == context_after["executable"]
            && context_before["compiler_version"] == context_after["compiler_version"]
            && identity_before == identity_after;
    let report = BenchmarkReport {
        source_binding_consistent: source_attribution_verified,
        dataset_family: "historical_scene_count_microbench",
        normative_reference: false,
        source_attribution_verified: false,
        source_context: serde_json::json!({
            "before": context_before, "after": context_after,
            "retained_sources_before": sources_before, "retained_sources_after": sources_after,
        }),
        artifact_directory: evidence.display().to_string(),
        schema: "motionwright.performance.v1",
        source_sha: context_before["source_sha_compiled"]
            .as_str()
            .unwrap_or("unattributed")
            .to_owned(),
        generated_by: "motionwright-service/examples/acceptance_bench",
        latency_budget_ms: budget_ms,
        briefs: briefs.len(),
        datasets: results.len(),
        results,
        cache_evidence: CacheEvidence {
            implemented: true,
            consumer_schema: "motionwright.preview-cache-consumer.v2",
            consumer_scope: "historical benchmark preview-index consumer; not Studio or native renderer cache",
            aggregate_population: "one warm request per dataset; all other requests and control setup are separately retained",
            consumer_requests,
            all_phase_lookup_attempts,
            all_phase_hits,
            all_phase_misses,
            all_phase_rejects,
            all_phase_constructor_calls,
            control_setup_requests,
            control_setup_lookup_attempts,
            control_setup_constructor_calls,
            lookup_attempts: cache_lookup_attempts,
            hits: cache_hits,
            avoided_work_count: cache_avoided_work,
            work_unit: "scene preview-index regeneration units",
            validated_bytes: cache_validated_bytes,
            validation_micros: cache_validation_micros,
            note: "Warm-cohort lookup_attempts/hits come from actual calls. Avoided units compare the matched cold constructor scene visits with the warm constructor's observed zero visits. Validation includes lookup, blob read, hash, JSON decode and exact comparison; each phase retains a homogeneous full consumer interval. All cold/change/corruption calls and corruption-fixture setup are separate explicit observations. These are benchmark constructor skips, not total computation avoided, Studio/native caching, renderer frames or wall-clock savings.",
        },
        human_feedback: "NOT_RUN: this executable does not fabricate human participants or ratings.",
        comparative_claims: "NONE: the benchmark reports Motionwright measurements only and makes no competitor-superiority claim.",
    };

    fs::write(
        evidence.join("report.json"),
        serde_json::to_vec_pretty(&report).expect("encode retained historical report"),
    )
    .expect("retain historical report before enforcing budgets");

    // Keep all nine measured datasets available even when a budget rejects the run.
    let stdout = std::io::stdout();
    let mut output = stdout.lock();
    serde_json::to_writer_pretty(&mut output, &report).expect("write benchmark report");
    writeln!(output).expect("finish benchmark report");
    output.flush().expect("flush benchmark report");

    let budget_micros = budget_ms * 1_000;
    if let Some(failure) = report
        .results
        .iter()
        .find(|result| result.max_commit_micros > budget_micros)
    {
        panic!(
            "measured commit budget exceeded: {} {} took {}us (budget {}us)",
            failure.brief, failure.size, failure.max_commit_micros, budget_micros
        );
    }
    if !report.source_binding_consistent {
        eprintln!("benchmark source attribution changed or is unavailable");
        return std::process::ExitCode::FAILURE;
    }
    std::process::ExitCode::SUCCESS
}

#[cfg(test)]
mod cache_consumer_tests {
    use super::*;
    use tempfile::{TempDir, tempdir};

    fn fixture() -> (TempDir, StudioService, motionwright_domain::Project, Uuid) {
        let directory = tempdir().unwrap();
        let service = StudioService::open(directory.path().join("fixture.sqlite3")).unwrap();
        let mut project = service
            .create_project("Observed preview consumer fixture")
            .unwrap();
        for index in 0..3 {
            commit(
                &service,
                &mut project,
                &format!("fixture-{index}"),
                Change::AddScene {
                    name: format!("Scene {index}"),
                    objective: format!("Fixture objective {index}"),
                    duration_seconds: 4,
                },
            );
        }
        let profile = project.deliverables[0].id;
        (directory, service, project, profile)
    }

    fn full_timing(metric: &CachePhaseMetric) {
        assert_eq!(
            metric.validation_micros,
            metric.lookup_micros + metric.read_decode_compare_micros
        );
        assert!(
            metric.total_micros
                >= metric.fingerprint_micros
                    + metric.lookup_micros
                    + metric.construction_micros
                    + metric.publication_micros
                    + metric.read_decode_compare_micros
        );
    }

    #[test]
    fn cold_miss_counts_real_construction_and_retains_original() {
        let (directory, service, project, profile) = fixture();
        let cold =
            consume_preview_index(&service, &project, profile, directory.path(), "cold", None);
        let (hit, bytes) = cold.result.unwrap();
        let value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(cold.metric.outcome, "MISS_BUILT");
        assert_eq!(cold.metric.constructor_calls, 1);
        assert_eq!(
            cold.metric.constructor_scene_records,
            value["scenes"].as_array().unwrap().len() as u64
        );
        assert_eq!(cold.metric.constructor_scene_records, 3);
        assert_eq!(cold.metric.lookup_attempts, 2);
        assert_eq!(
            (cold.metric.hits, cold.metric.misses, cold.metric.rejects),
            (1, 1, 0)
        );
        assert_eq!(hit.record.work_units, cold.metric.constructor_scene_records);
        assert_eq!(
            fs::read(directory.path().join("cold-preview-index.json")).unwrap(),
            bytes
        );
        let original: serde_json::Value = serde_json::from_slice(
            &fs::read(directory.path().join("cold-observation.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(original, serde_json::to_value(&cold.metric).unwrap());
        full_timing(&cold.metric);
    }

    #[test]
    fn warm_hit_skips_constructor_and_times_read_decode_comparison() {
        let (directory, service, project, profile) = fixture();
        let cold =
            consume_preview_index(&service, &project, profile, directory.path(), "cold", None);
        let (first, bytes) = cold.result.unwrap();
        let warm = consume_preview_index(
            &service,
            &project,
            profile,
            directory.path(),
            "warm",
            Some(&bytes),
        );
        let (second, consumed) = warm.result.unwrap();
        assert_eq!(first.record, second.record);
        assert_eq!(consumed, bytes);
        assert_eq!(warm.metric.lookup_attempts, 1);
        assert_eq!(
            (warm.metric.hits, warm.metric.misses, warm.metric.rejects),
            (1, 0, 0)
        );
        assert_eq!(warm.metric.payload_bytes_read, bytes.len() as u64);
        assert_eq!(
            observed_warm_skip(&cold.metric, &warm.metric),
            cold.metric.constructor_scene_records
        );
        assert!(!directory.path().join("warm-preview-index.json").exists());
        full_timing(&warm.metric);
    }

    #[test]
    fn changed_fingerprint_misses_and_constructs_distinct_bytes() {
        let (directory, service, project, profile) = fixture();
        let cold =
            consume_preview_index(&service, &project, profile, directory.path(), "cold", None);
        let (_, original) = cold.result.unwrap();
        let mut changed = project.clone();
        changed.deliverables[0].width -= 2;
        let observed = consume_preview_index(
            &service,
            &changed,
            profile,
            directory.path(),
            "changed",
            None,
        );
        let (_, rebuilt) = observed.result.unwrap();
        assert_ne!(
            observed.metric.fingerprint_sha256,
            cold.metric.fingerprint_sha256
        );
        assert_ne!(rebuilt, original);
        assert_eq!(observed.metric.outcome, "MISS_BUILT");
        assert_eq!(observed.metric.constructor_calls, 1);
        assert_eq!(
            observed.metric.constructor_scene_records,
            cold.metric.constructor_scene_records
        );
        assert_eq!(
            (
                observed.metric.hits,
                observed.metric.misses,
                observed.metric.rejects
            ),
            (1, 1, 0)
        );
        full_timing(&observed.metric);
    }

    #[test]
    fn physical_corruption_rejects_exact_cause_without_build_or_repair() {
        let (directory, service, project, profile) = fixture();
        let cold =
            consume_preview_index(&service, &project, profile, directory.path(), "cold", None);
        let (hit, original) = cold.result.unwrap();
        let mut damaged = original.clone();
        damaged[0] ^= 1;
        assert_ne!(payload_digest(&damaged), hit.record.blob_sha256);
        fs::write(&hit.blob_path, &damaged).unwrap();
        let rejected = consume_preview_index(
            &service,
            &project,
            profile,
            directory.path(),
            "corrupt",
            Some(&original),
        );
        assert_digest_rejection(&rejected);
        assert_eq!(fs::read(&hit.blob_path).unwrap(), damaged);
        assert_eq!(
            fs::read(directory.path().join("cold-preview-index.json")).unwrap(),
            original
        );
        assert!(!directory.path().join("corrupt-preview-index.json").exists());
        full_timing(&rejected.metric);
    }

    #[test]
    fn wrong_warm_oracle_is_rejected_by_timed_consumer_without_rebuild() {
        let (directory, service, project, profile) = fixture();
        let cold =
            consume_preview_index(&service, &project, profile, directory.path(), "cold", None);
        let (hit, original) = cold.result.unwrap();
        let mut wrong = original.clone();
        wrong[0] ^= 1;
        let rejected = consume_preview_index(
            &service,
            &project,
            profile,
            directory.path(),
            "wrong-oracle",
            Some(&wrong),
        );
        assert!(
            matches!(&rejected.result, Err(StorageError::InvalidDerivedCache(message))
                if message == "consumed preview differs from retained construction")
        );
        assert_eq!(rejected.metric.outcome, "ERROR");
        assert_eq!(rejected.metric.constructor_calls, 0);
        assert_eq!(rejected.metric.constructor_scene_records, 0);
        assert_eq!(rejected.metric.payload_bytes_read, original.len() as u64);
        assert_eq!(fs::read(hit.blob_path).unwrap(), original);
        full_timing(&rejected.metric);
    }
}
