use motionwright_domain::{
    Beat, CanvasNode, Change, CoordinateSpace, NodeStyle, RationalTime, RevisionStamp,
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use serde::Serialize;
use std::{
    collections::BTreeSet,
    env,
    fs::{self, File},
    io::Write,
    path::Path,
    time::Instant,
};
use tempfile::tempdir;
use uuid::Uuid;

const SCENE_COUNT: usize = 12;
const BEAT_COUNT: usize = 80;
const NODE_COUNT: usize = 300;
const VOICE_SECONDS: u32 = 300;
const VOICE_SAMPLE_RATE_HZ: u32 = 8_000;
const WARMUP_COMMITS: usize = 5;
const MEASURED_COMMITS: usize = 40;

#[derive(Debug, Serialize)]
struct DatasetShape {
    scenes: usize,
    beats: usize,
    canvas_nodes: usize,
    voice_seconds: u32,
    voice_sample_rate_hz: u32,
    width: u32,
    height: u32,
    frame_rate: &'static str,
}

#[derive(Debug, Serialize)]
struct LatencyEvidence {
    samples: usize,
    budget_ms: u128,
    p95_micros: u128,
    max_micros: u128,
    samples_micros: Vec<u128>,
}

#[derive(Debug, Serialize)]
struct ResponsivenessReport {
    schema: &'static str,
    source_sha: String,
    generated_by: &'static str,
    dataset: DatasetShape,
    setup_revision: u64,
    database_bytes: u64,
    small_commit: LatencyEvidence,
    reopen_budget_ms: u128,
    reopen_micros: u128,
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
    let request_id = format!("responsiveness-{label}-{}", Uuid::now_v7());
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

fn canvas_node(scene_index: usize, node_index: usize) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: format!("S{:02} object {:03}", scene_index + 1, node_index + 1),
        kind: if node_index.is_multiple_of(5) {
            "text".into()
        } else {
            "shape".into()
        },
        parent_id: None,
        x: f64::from((node_index % 5) as u32) * 120.0,
        y: f64::from((node_index / 5) as u32) * 72.0,
        width: 96.0,
        height: 54.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: node_index
            .is_multiple_of(5)
            .then(|| format!("Object {}", node_index + 1)),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: i32::try_from(node_index).expect("bounded S-profile z-index"),
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

fn write_silent_pcm16_wav(path: &Path) {
    let frames = VOICE_SAMPLE_RATE_HZ * VOICE_SECONDS;
    let data_bytes = frames * 2;
    let mut file = File::create(path).expect("create S-profile voice fixture");
    file.write_all(b"RIFF").unwrap();
    file.write_all(&(36 + data_bytes).to_le_bytes()).unwrap();
    file.write_all(b"WAVEfmt ").unwrap();
    file.write_all(&16_u32.to_le_bytes()).unwrap();
    file.write_all(&1_u16.to_le_bytes()).unwrap();
    file.write_all(&1_u16.to_le_bytes()).unwrap();
    file.write_all(&VOICE_SAMPLE_RATE_HZ.to_le_bytes()).unwrap();
    file.write_all(&(VOICE_SAMPLE_RATE_HZ * 2).to_le_bytes())
        .unwrap();
    file.write_all(&2_u16.to_le_bytes()).unwrap();
    file.write_all(&16_u16.to_le_bytes()).unwrap();
    file.write_all(b"data").unwrap();
    file.write_all(&data_bytes.to_le_bytes()).unwrap();

    let zeros = vec![0_u8; 64 * 1024];
    let mut remaining = usize::try_from(data_bytes).expect("voice fixture byte count");
    while remaining > 0 {
        let count = remaining.min(zeros.len());
        file.write_all(&zeros[..count]).unwrap();
        remaining -= count;
    }
    file.sync_all().unwrap();
}

fn p95(samples: &[u128]) -> u128 {
    assert!(!samples.is_empty());
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    let rank = (sorted.len() * 95).div_ceil(100).max(1);
    sorted[rank - 1]
}

fn main() {
    let commit_budget_ms = env::var("MOTIONWRIGHT_SMALL_COMMIT_P95_BUDGET_MS")
        .ok()
        .and_then(|value| value.parse::<u128>().ok())
        .unwrap_or(250);
    let reopen_budget_ms = env::var("MOTIONWRIGHT_S_REOPEN_BUDGET_MS")
        .ok()
        .and_then(|value| value.parse::<u128>().ok())
        .unwrap_or(2_000);

    let temp = tempdir().expect("temporary responsiveness directory");
    let db_path = temp.path().join("motionwright.sqlite3");
    let service = StudioService::open(&db_path).expect("responsiveness store");
    let mut project = service
        .create_project("Responsiveness S profile")
        .expect("responsiveness project");

    for scene_index in 0..SCENE_COUNT {
        commit(
            &service,
            &mut project,
            &format!("seed-scene-{scene_index}"),
            Change::AddScene {
                name: format!("S scene {:02}", scene_index + 1),
                objective: "Interactive editing must stay bounded under the S profile.".into(),
                duration_seconds: 30,
            },
        );
    }

    let scene_ids: Vec<_> = project.scenes.iter().map(|scene| scene.id).collect();
    let mut authored_beats = 0_usize;
    for (scene_index, scene_id) in scene_ids.iter().copied().enumerate() {
        let per_scene = if scene_index < 8 { 7 } else { 6 };
        for beat_index in 0..per_scene {
            commit(
                &service,
                &mut project,
                &format!("seed-beat-{scene_index}-{beat_index}"),
                Change::UpsertSceneBeat {
                    scene_id,
                    beat: Beat {
                        id: Uuid::now_v7(),
                        label: format!("Beat {:03}", authored_beats + 1),
                        objective:
                            "Preserve authored timing while the inspector remains responsive."
                                .into(),
                        start: RationalTime::new(i64::from(beat_index), 1).unwrap(),
                        duration: RationalTime::new(1, 1).unwrap(),
                    },
                },
            );
            authored_beats += 1;
        }
    }
    assert_eq!(authored_beats, BEAT_COUNT);

    let mut authored_nodes = 0_usize;
    for (scene_index, scene_id) in scene_ids.iter().copied().enumerate() {
        for node_index in 0..25 {
            commit(
                &service,
                &mut project,
                &format!("seed-node-{scene_index}-{node_index}"),
                Change::AddCanvasNode {
                    scene_id,
                    node: canvas_node(scene_index, node_index),
                },
            );
            authored_nodes += 1;
        }
    }
    assert_eq!(authored_nodes, NODE_COUNT);

    let voice_path = temp.path().join("five-minute-reference.wav");
    write_silent_pcm16_wav(&voice_path);
    project = service
        .import_voice_file(
            project.id,
            &RevisionStamp::from(&project),
            "responsiveness-five-minute-voice",
            &voice_path,
            VoiceImportMetadata {
                name: "five-minute-reference.wav".into(),
                media_type: "audio/wav".into(),
                label: "Measured five-minute reference".into(),
            },
        )
        .expect("measure S-profile voice")
        .project;

    assert_eq!(project.scenes.len(), SCENE_COUNT);
    assert_eq!(
        project
            .scenes
            .iter()
            .map(|scene| scene.beats.len())
            .sum::<usize>(),
        BEAT_COUNT
    );
    assert_eq!(
        project
            .scenes
            .iter()
            .map(|scene| scene.nodes.len())
            .sum::<usize>(),
        NODE_COUNT
    );
    let voice = project
        .audio
        .voice_tracks
        .first()
        .expect("measured S-profile voice");
    assert_eq!(voice.sample_rate_hz, VOICE_SAMPLE_RATE_HZ);
    assert_eq!(
        voice.measured_duration,
        RationalTime::new(VOICE_SECONDS.into(), 1).unwrap()
    );
    let (master_width, master_height) = {
        let master = project
            .deliverables
            .first()
            .expect("default 16:9 deliverable");
        assert_eq!((master.width, master.height), (1920, 1080));
        assert_eq!(master.frame_rate, RationalTime::new(30, 1).unwrap());
        (master.width, master.height)
    };

    for index in 0..WARMUP_COMMITS {
        commit(
            &service,
            &mut project,
            &format!("warmup-{index}"),
            Change::RenameProject {
                title: format!("Responsiveness S profile warmup {index}"),
            },
        );
    }

    let mut commit_samples = Vec::with_capacity(MEASURED_COMMITS);
    for index in 0..MEASURED_COMMITS {
        commit_samples.push(commit(
            &service,
            &mut project,
            &format!("small-commit-{index}"),
            Change::RenameProject {
                title: format!("Responsiveness S profile sample {index:02}"),
            },
        ));
    }
    let commit_p95 = p95(&commit_samples);
    let commit_max = commit_samples.iter().copied().max().unwrap_or_default();
    let budget_micros = commit_budget_ms * 1_000;
    assert!(
        commit_p95 < budget_micros,
        "S-profile small commit p95 {commit_p95}us exceeds {budget_micros}us budget"
    );

    let setup_revision = project.revision;
    drop(service);
    let reopen_started = Instant::now();
    let reopened_service = StudioService::open(&db_path).expect("reopen responsiveness store");
    let reopened = reopened_service
        .project(project.id)
        .expect("reopen responsiveness project");
    let reopen_micros = reopen_started.elapsed().as_micros();
    assert_eq!(reopened.revision, setup_revision);
    assert!(
        reopen_micros < reopen_budget_ms * 1_000,
        "S-profile reopen {reopen_micros}us exceeds {}us budget",
        reopen_budget_ms * 1_000
    );

    let report = ResponsivenessReport {
        schema: "motionwright.responsiveness.v1",
        source_sha: env::var("GITHUB_SHA").unwrap_or_else(|_| "local-unattributed".into()),
        generated_by: "motionwright-service/examples/responsiveness_bench",
        dataset: DatasetShape {
            scenes: SCENE_COUNT,
            beats: BEAT_COUNT,
            canvas_nodes: NODE_COUNT,
            voice_seconds: VOICE_SECONDS,
            voice_sample_rate_hz: VOICE_SAMPLE_RATE_HZ,
            width: master_width,
            height: master_height,
            frame_rate: "30/1",
        },
        setup_revision,
        database_bytes: fs::metadata(&db_path)
            .map(|metadata| metadata.len())
            .unwrap_or_default(),
        small_commit: LatencyEvidence {
            samples: commit_samples.len(),
            budget_ms: commit_budget_ms,
            p95_micros: commit_p95,
            max_micros: commit_max,
            samples_micros: commit_samples,
        },
        reopen_budget_ms,
        reopen_micros,
        human_feedback: "NOT_RUN: responsiveness evidence contains no fabricated human review.",
        comparative_claims: "NONE: measurements are Motionwright-only and runner-specific.",
    };
    serde_json::to_writer_pretty(std::io::stdout(), &report).expect("write responsiveness report");
    println!();
}
