#[path = "responsiveness_bench.rs"]
mod reference_profiles;

use motionwright_domain::{
    Change, LockKind, RationalTime, RendererKind, RevisionStamp, SceneStatus,
    variant_dependency_fingerprint,
};
use motionwright_service::StudioService;
use serde::Serialize;
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
    let dependency = variant_dependency_fingerprint(&reopened, profile_id)
        .expect("benchmark dependency fingerprint");
    let scope = format!("project:{}:generation:{}", reopened.id, reopened.generation);
    let cache_kind = "preview-index";
    let derived_payload = serde_json::to_vec(&serde_json::json!({
        "schema": "motionwright.preview-index.v1",
        "project_id": reopened.id,
        "generation": reopened.generation,
        "revision": reopened.revision,
        "profile_id": profile_id,
        "master_inputs_sha256": &dependency.master_inputs_sha256,
        "scenes": reopened.scenes.iter().map(|scene| serde_json::json!({
            "id": scene.id,
            "name": &scene.name,
            "objective": &scene.objective,
            "start": &scene.start,
            "duration": &scene.duration,
            "renderer": &scene.renderer,
            "status": &scene.status,
        })).collect::<Vec<_>>(),
    }))
    .expect("serialize derived preview index");
    let cache_source = temp.join("derived-preview-index.json");
    fs::write(&cache_source, &derived_payload).expect("write derived preview index");

    let cache_store_started = Instant::now();
    let stored = reopened_service
        .put_derived_cache_file(
            &scope,
            cache_kind,
            &dependency.master_inputs_sha256,
            &cache_source,
            scene_count as u64,
        )
        .expect("store derived preview cache");
    let store_micros = cache_store_started.elapsed().as_micros();

    let cache_lookup_started = Instant::now();
    let hit = reopened_service
        .lookup_derived_cache(&scope, cache_kind, &dependency.master_inputs_sha256)
        .expect("validate derived preview cache")
        .expect("fresh derived preview cache hit");
    let validation_micros = cache_lookup_started.elapsed().as_micros();
    assert_eq!(hit.record, stored);
    assert_eq!(
        fs::read(&hit.blob_path).expect("read verified cache blob"),
        derived_payload
    );

    let mut changed = reopened.clone();
    changed.deliverables[0].width = changed.deliverables[0].width.saturating_sub(2);
    let changed_dependency = variant_dependency_fingerprint(&changed, profile_id)
        .expect("changed dependency fingerprint");
    assert_ne!(
        changed_dependency.master_inputs_sha256,
        dependency.master_inputs_sha256
    );
    assert!(
        reopened_service
            .lookup_derived_cache(&scope, cache_kind, &changed_dependency.master_inputs_sha256,)
            .expect("changed fingerprint lookup")
            .is_none(),
        "changed render inputs must not reuse a stale cache entry"
    );

    let cache = CacheMetric {
        scope,
        kind: cache_kind,
        fingerprint_sha256: dependency.master_inputs_sha256,
        stored_bytes: stored.size_bytes,
        validated_bytes: hit.validated_bytes,
        store_micros,
        validation_micros,
        avoided_work_units: scene_count as u64,
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

    let cache_lookup_attempts = results.len();
    let cache_hits = results
        .iter()
        .filter(|result| result.cache.validated_bytes == result.cache.stored_bytes)
        .count();
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
            lookup_attempts: cache_lookup_attempts,
            hits: cache_hits,
            avoided_work_count: cache_avoided_work,
            work_unit: "scene preview-index regeneration units",
            validated_bytes: cache_validated_bytes,
            validation_micros: cache_validation_micros,
            note: "The index is built before cache storage. Hits validate its bytes; avoided_work_count sums declared scene units, not observed regeneration skips. Changed-fingerprint misses are separate from lookup_attempts. No renderer frames or wall-clock savings are measured.",
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
