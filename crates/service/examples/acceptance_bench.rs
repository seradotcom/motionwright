use motionwright_domain::{
    Change, LockKind, RationalTime, RendererKind, RevisionStamp, SceneStatus,
};
use motionwright_service::StudioService;
use serde::Serialize;
use std::{env, fs, path::Path, time::Instant};
use tempfile::tempdir;
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
struct DatasetMetric {
    brief: String,
    size: String,
    scene_count: usize,
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
}

#[derive(Serialize)]
struct CacheEvidence {
    implemented: bool,
    avoided_work_count: u64,
    validation_micros: u128,
    note: &'static str,
}

#[derive(Serialize)]
struct BenchmarkReport {
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
    let temp = tempdir().expect("temporary benchmark directory");
    let db_path = temp.path().join("motionwright.sqlite3");
    let service = StudioService::open(&db_path).expect("benchmark store");
    let mut project = service
        .create_project(format!("{} · {size}", brief.title))
        .expect("benchmark project");

    let seed_started = Instant::now();
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
    }
    let seed_micros = seed_started.elapsed().as_micros();
    let revision_start = project.revision;

    let scene_a = project.scenes[0].id;
    let scene_b = project.scenes[project.scenes.len().saturating_sub(1).min(1)].id;
    let project_resource = project.resource_key();
    let mut operations = Vec::with_capacity(10);
    let revision_ten_started = Instant::now();

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
            objective: "Revision-ten objective remains editable and renderer independent.".into(),
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
            duration: RationalTime::new(7, 1).expect("valid rational"),
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
            at: RationalTime::new(3, 1).expect("valid rational"),
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
    let lock_id = project
        .locks
        .iter()
        .find(|lock| lock.note == "Benchmark lock round trip")
        .expect("benchmark lock")
        .id;
    record("09-project-unlock", Change::RemoveLock { lock_id });
    record(
        "10-final-objective",
        Change::UpdateSceneObjective {
            scene_id: scene_a,
            objective: "Tenth predefined change persisted after reopen.".into(),
        },
    );

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

    DatasetMetric {
        brief: brief.slug.into(),
        size: size.into(),
        scene_count,
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
    }
}

fn main() {
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

    let budget_micros = budget_ms * 1_000;
    if let Some(failure) = results
        .iter()
        .find(|result| result.max_commit_micros > budget_micros)
    {
        panic!(
            "measured commit budget exceeded: {} {} took {}us (budget {}us)",
            failure.brief, failure.size, failure.max_commit_micros, budget_micros
        );
    }

    let report = BenchmarkReport {
        schema: "motionwright.performance.v1",
        source_sha: env::var("GITHUB_SHA").unwrap_or_else(|_| "local-unattributed".into()),
        generated_by: "motionwright-service/examples/acceptance_bench",
        latency_budget_ms: budget_ms,
        briefs: briefs.len(),
        datasets: results.len(),
        results,
        cache_evidence: CacheEvidence {
            implemented: false,
            avoided_work_count: 0,
            validation_micros: 0,
            note: "Derived-media cache acceptance remains open; Motionwright does not call a boolean cache flag measured evidence.",
        },
        human_feedback: "NOT_RUN: this executable does not fabricate human participants or ratings.",
        comparative_claims: "NONE: the benchmark reports Motionwright measurements only and makes no competitor-superiority claim.",
    };

    serde_json::to_writer_pretty(std::io::stdout(), &report).expect("write benchmark report");
    println!();
}
