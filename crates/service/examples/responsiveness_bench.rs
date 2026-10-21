//! Retained, exact-shape service benchmarks. UI selection/scrub and scene/object
//! projection paging are separate capabilities and are never inferred here.
pub mod support {
    use motionwright_domain::{
        Beat, CameraState, CanvasNode, Change, Claim, CoordinateSpace, DomainError, LockKind,
        NodeProperty, NodeStyle, Project, ProjectLock, RationalTime, RendererKind, RevisionStamp,
        Scene, SceneStatus, SourceReference,
    };
    use motionwright_service::{StudioService, VoiceImportMetadata};
    use motionwright_storage::{BlobDescriptor, StorageError, Store};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::{
        collections::BTreeSet,
        env,
        fs::{self, File, OpenOptions},
        io::{Read, Write},
        path::{Path, PathBuf},
        process::ExitCode,
        time::Instant,
    };
    use uuid::Uuid;

    type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
    const SAMPLE_RATE: u32 = 8_000;
    const WARMUPS: usize = 5;
    const SAMPLES: usize = 40;

    // The same declared cases feed fixture generation and the measured suite.
    #[derive(Clone, Copy)]
    struct BriefSpec {
        slug: &'static str,
        title: &'static str,
    }

    const REFERENCE_BRIEFS: [BriefSpec; 3] = [
        BriefSpec {
            slug: "harbor-safety",
            title: "Harbor Safety Explainer",
        },
        BriefSpec {
            slug: "museum-night",
            title: "Museum Night Campaign",
        },
        BriefSpec {
            slug: "bike-route",
            title: "Bike Route Service Update",
        },
    ];
    const RESPONSIVENESS_BRIEF: BriefSpec = BriefSpec {
        slug: "responsiveness",
        title: "Responsiveness reference project",
    };

    #[derive(Clone, Copy)]
    struct Profile {
        label: &'static str,
        scenes: usize,
        nodes: usize,
        seconds: i64,
        required_beats: Option<usize>,
        required_variants: Option<usize>,
        open_budget_ms: Option<u128>,
    }

    impl Profile {
        fn named(label: &str) -> Result<Self> {
            match label {
                "S" => Ok(Self {
                    label: "S",
                    scenes: 12,
                    nodes: 300,
                    seconds: 300,
                    required_beats: Some(80),
                    required_variants: None,
                    open_budget_ms: Some(2_000),
                }),
                "M" => Ok(Self {
                    label: "M",
                    scenes: 120,
                    nodes: 3_000,
                    seconds: 1_800,
                    required_beats: None,
                    required_variants: Some(5),
                    open_budget_ms: Some(5_000),
                }),
                "L" => Ok(Self {
                    label: "L",
                    scenes: 500,
                    nodes: 15_000,
                    seconds: 7_200,
                    required_beats: None,
                    required_variants: None,
                    open_budget_ms: None,
                }),
                _ => Err("profile must be S, M or L".into()),
            }
        }
    }

    fn require(condition: bool, message: &str) -> Result<()> {
        if condition {
            Ok(())
        } else {
            Err(message.into())
        }
    }

    fn write_json(path: &Path, value: &impl serde::Serialize) -> Result<()> {
        let mut file = File::create(path)?;
        serde_json::to_writer_pretty(&mut file, value)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        Ok(())
    }

    /// Every run retains its fixtures, SQLite files, CAS and partial reports.
    /// The caller/runner supplies an isolated TEMP or explicit artifact root.
    pub fn retained_directory(label: &str) -> Result<PathBuf> {
        let base = env::var_os("MOTIONWRIGHT_BENCH_ARTIFACT_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                source_root()
                    .join("verification")
                    .join("performance-reference-evidence")
            });
        require(
            base.is_absolute(),
            "benchmark artifact root must be absolute",
        )?;
        fs::create_dir_all(&base)?;
        let path = base.join(format!("{label}-{}", Uuid::now_v7()));
        fs::create_dir(&path)?;
        Ok(path)
    }

    fn digest_file(path: &Path) -> Result<(String, u64)> {
        let mut file = File::open(path)?;
        let mut hash = Sha256::new();
        let mut count = 0_u64;
        let mut buffer = [0_u8; 128 * 1024];
        loop {
            let read = file.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            count = count
                .checked_add(read as u64)
                .ok_or("file length overflow")?;
            hash.update(&buffer[..read]);
        }
        Ok((hex::encode(hash.finalize()), count))
    }

    /// The Actions checkout SHA is independently attested by the runner; these
    /// compiled and runtime values detect binary/source mixing, not signatures.
    pub fn context() -> Value {
        let compile_sha = option_env!("GITHUB_SHA");
        let runtime_sha = env::var("GITHUB_SHA").ok();
        let executable = env::current_exe().ok().and_then(|path| {
            digest_file(&path).ok().map(|(sha256, size_bytes)|
                json!({"path": path, "sha256": sha256, "size_bytes": size_bytes}))
        });
        json!({
            "source_sha_compiled": compile_sha,
            "source_sha_runtime": runtime_sha,
            "source_binding_scope": "compile-time/runtime equality, independent runner checkout required",
            "independent_checkout_verification": "NOT_PERFORMED_BY_BENCHMARK",
            "compiler_version": option_env!("RUSTC_VERSION").unwrap_or("NOT_CAPTURED"),
            "cargo_lock_retained": true,
            "executable": executable,
            "observed_os": env::consts::OS,
            "observed_arch": env::consts::ARCH,
            "available_parallelism": std::thread::available_parallelism().ok().map(usize::from),
            "runner_os_declared": env::var("RUNNER_OS").ok(),
            "runner_arch_declared": env::var("RUNNER_ARCH").ok(),
            "hardware_description_declared": env::var("MOTIONWRIGHT_BENCH_HARDWARE_DESCRIPTION").ok(),
            "hardware_description_verified": false,
            "cache_policy": "SQLite close/reopen on same runner; OS page cache is not evicted. No cold-disk claim.",
            "sampling": "All raw samples retained, nearest-rank p95, no outlier removal. Fixture construction and readback excluded from commit timing.",
        })
    }

    fn genesis(profile: Profile, brief: BriefSpec) -> Result<Project> {
        let mut project = Project::new(brief.title)?;
        project.brief.objective =
            "Retain authored identities and constraints across measured service edits.".into();
        project.brief.claims.push(Claim {
            id: Uuid::now_v7(),
            text: "This project is a synthetic performance fixture with explicitly measured dimensions.".into(),
            source: Some(SourceReference::ManualNote { label: "Benchmark fixture declaration; not a real-world factual claim".into() }),
            context: format!("Reference profile {}", profile.label),
            source_revision: Some("fixture-v1".into()),
        });
        let beats = profile.required_beats.unwrap_or(profile.scenes * 6);
        for scene_index in 0..profile.scenes {
            let mut scene = Scene {
                id: Uuid::now_v7(),
                name: format!("{} scene {:03}", profile.label, scene_index + 1),
                objective: "Preserve authored scene and object identities after reopening.".into(),
                start: RationalTime::new(
                    profile.seconds * scene_index as i64,
                    profile.scenes as i64,
                )?,
                duration: RationalTime::new(profile.seconds, profile.scenes as i64)?,
                renderer: RendererKind::MotionCanvas,
                status: SceneStatus::Draft,
                beats: vec![],
                nodes: vec![],
                camera: CameraState::default(),
            };
            let scene_beats =
                beats / profile.scenes + usize::from(scene_index < beats % profile.scenes);
            for index in 0..scene_beats {
                scene.beats.push(Beat {
                    id: Uuid::now_v7(),
                    label: format!("Beat {}", index + 1),
                    objective: "Authored fixture beat".into(),
                    start: RationalTime::new(index as i64, 1)?,
                    duration: RationalTime::new(1, 1)?,
                });
            }
            let scene_nodes = profile.nodes / profile.scenes
                + usize::from(scene_index < profile.nodes % profile.scenes);
            for index in 0..scene_nodes {
                scene.nodes.push(CanvasNode {
                    id: Uuid::now_v7(),
                    name: format!("Object {:03}", index + 1),
                    kind: if index.is_multiple_of(5) {
                        "text".into()
                    } else {
                        "shape".into()
                    },
                    parent_id: None,
                    x: (index % 5) as f64 * 120.0,
                    y: (index / 5) as f64 * 72.0,
                    width: 96.0,
                    height: 54.0,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                    text: index
                        .is_multiple_of(5)
                        .then(|| format!("Object {}", index + 1)),
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: i32::try_from(index)?,
                    style: NodeStyle::default(),
                    relations: vec![],
                    property_locks: if index == 0 {
                        BTreeSet::from([NodeProperty::Opacity])
                    } else {
                        BTreeSet::new()
                    },
                    keyframes: vec![],
                });
            }
            project.scenes.push(scene);
        }
        project.locks.push(ProjectLock {
            id: Uuid::now_v7(),
            resource: format!(
                "scene:{}",
                project.scenes.last().ok_or("missing final scene")?.id
            ),
            kind: LockKind::Timing,
            note: "Retained authored timing lock".into(),
        });
        if profile.required_variants == Some(5) {
            let master = project.deliverables[0].clone();
            for (name, width, height, language) in [
                ("Portrait 4:5", 1080, 1350, "en"),
                ("Master Spanish", 1920, 1080, "es"),
            ] {
                let mut variant = master.clone();
                variant.id = Uuid::now_v7();
                variant.name = name.into();
                variant.width = width;
                variant.height = height;
                variant.language = language.into();
                variant.parent_profile_id = Some(master.id);
                variant.source_revision = Some(0);
                project.deliverables.push(variant);
            }
        }
        // Controlled gate-negative input: a real, domain-valid smaller project
        // must be rejected by the reference-shape check after SQL/import readback.
        if env::var("MOTIONWRIGHT_BENCH_NEGATIVE").as_deref() == Ok("truncated-profile") {
            project.scenes[0].nodes.pop();
        }
        project.validate()?;
        Ok(project)
    }

    fn write_wav(path: &Path, seconds: i64) -> Result<()> {
        let frames = SAMPLE_RATE
            .checked_mul(u32::try_from(seconds)?)
            .ok_or("WAV frame overflow")?;
        let bytes = frames.checked_mul(2).ok_or("WAV byte overflow")?;
        let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
        file.write_all(b"RIFF")?;
        file.write_all(&(36 + bytes).to_le_bytes())?;
        file.write_all(b"WAVEfmt ")?;
        file.write_all(&16_u32.to_le_bytes())?;
        file.write_all(&1_u16.to_le_bytes())?;
        file.write_all(&1_u16.to_le_bytes())?;
        file.write_all(&SAMPLE_RATE.to_le_bytes())?;
        file.write_all(&(SAMPLE_RATE * 2).to_le_bytes())?;
        file.write_all(&2_u16.to_le_bytes())?;
        file.write_all(&16_u16.to_le_bytes())?;
        file.write_all(b"data")?;
        file.write_all(&bytes.to_le_bytes())?;
        let zeros = [0_u8; 64 * 1024];
        let mut remaining = bytes as usize;
        while remaining != 0 {
            let count = remaining.min(zeros.len());
            file.write_all(&zeros[..count])?;
            remaining -= count;
        }
        file.sync_all()?;
        Ok(())
    }
    fn shape(project: &Project) -> Result<Value> {
        let mut total = RationalTime::ZERO;
        let mut contiguous = true;
        for scene in &project.scenes {
            contiguous &= scene.start == total;
            total = total.checked_add(scene.duration)?;
        }
        let master = project
            .deliverables
            .first()
            .ok_or("missing master profile")?;
        let voice = project
            .audio
            .voice_tracks
            .first()
            .ok_or("missing measured voice")?;
        Ok(json!({
            "scenes": project.scenes.len(),
            "beats": project.scenes.iter().map(|s| s.beats.len()).sum::<usize>(),
            "canvas_nodes": project.scenes.iter().map(|s| s.nodes.len()).sum::<usize>(),
            "scene_duration": total,
            "contiguous_scene_timeline": contiguous,
            "voice_seconds": voice.measured_duration.num / voice.measured_duration.den,
            "voice_duration": voice.measured_duration,
            "voice_sample_rate_hz": voice.sample_rate_hz,
            "width": master.width, "height": master.height,
            "frame_rate": format!("{}/{}", master.frame_rate.num, master.frame_rate.den),
            "deliverable_variants": project.deliverables.len(),
            "claims": project.brief.claims.len(), "locks": project.locks.len(),
            "node_property_locks": project.scenes.iter().flat_map(|s| &s.nodes)
                .map(|node| node.property_locks.len()).sum::<usize>(),
        }))
    }

    fn verify_persisted_timeline(
        project: &motionwright_domain::Project,
        expected_seconds: i64,
    ) -> std::result::Result<(), String> {
        let mut observed = motionwright_domain::RationalTime::ZERO;
        for scene in &project.scenes {
            if scene.start != observed {
                return Err(format!(
                    "persisted reference scene {} starts at {}/{}; expected {}/{}",
                    scene.id, scene.start.num, scene.start.den, observed.num, observed.den
                ));
            }
            observed = observed
                .checked_add(scene.duration)
                .map_err(|error| format!("persisted reference timeline overflow: {error}"))?;
        }
        let expected = motionwright_domain::RationalTime::new(expected_seconds, 1)
            .map_err(|error| format!("invalid reference duration: {error}"))?;
        if observed != expected {
            return Err(format!(
                "persisted reference timeline expected {}/{} seconds; observed {}/{}",
                expected.num, expected.den, observed.num, observed.den
            ));
        }
        Ok(())
    }

    fn validate_shape(profile: Profile, project: &Project) -> Result<()> {
        project.validate()?;
        let observed = shape(project)?;
        require(
            observed["scenes"].as_u64() == Some(profile.scenes as u64),
            "reference scene count differs after persisted readback",
        )?;
        require(
            observed["canvas_nodes"].as_u64() == Some(profile.nodes as u64),
            "reference object count differs after persisted readback",
        )?;
        if let Some(beats) = profile.required_beats {
            require(
                observed["beats"].as_u64() == Some(beats as u64),
                "reference beat count differs after persisted readback",
            )?;
        }
        if let Some(variants) = profile.required_variants {
            require(
                project.deliverables.len() == variants,
                "reference variant count differs after persisted readback",
            )?;
        }
        let seconds = RationalTime::new(profile.seconds, 1)?;
        verify_persisted_timeline(project, profile.seconds)?;
        require(
            project.audio.voice_tracks[0].measured_duration == seconds,
            "measured WAV duration differs from reference timeline",
        )?;
        require(
            project.audio.voice_tracks[0].sample_rate_hz == SAMPLE_RATE,
            "measured WAV sample rate differs",
        )?;
        let master = &project.deliverables[0];
        require(
            (master.width, master.height) == (1920, 1080),
            "reference master dimensions differ",
        )?;
        require(
            master.frame_rate == RationalTime::new(30, 1)?,
            "reference frame rate differs",
        )?;
        require(
            !project.brief.claims.is_empty() && !project.locks.is_empty(),
            "fixture must retain nonempty claims and locks",
        )?;
        Ok(())
    }

    fn cas_snapshot(service: &StudioService, project: &Project) -> Result<Vec<BlobDescriptor>> {
        let mut digests = BTreeSet::new();
        for asset in &project.assets {
            digests.insert(
                asset
                    .content_sha256
                    .as_ref()
                    .ok_or("fixture asset lacks content identity")?
                    .clone(),
            );
        }
        let store = service.store();
        let store = store.lock();
        let mut descriptors = Vec::new();
        for digest in digests {
            descriptors.push(store.verify_blob(&digest)?);
        }
        Ok(descriptors)
    }

    fn paged_history(service: &StudioService, project: &Project) -> Result<Value> {
        let mut after = 0_u64;
        let mut pages = Vec::new();
        let mut events = Vec::new();
        loop {
            let page = service.history(project.id, after, 7)?;
            pages.push(json!({"after_revision": after, "returned": page.len()}));
            if page.is_empty() {
                break;
            }
            require(page.len() <= 7, "history page exceeded requested limit")?;
            for event in &page {
                require(
                    event.revision == after + 1,
                    "history contains a gap, duplicate or reordered revision",
                )?;
                after = event.revision;
            }
            events.extend(page);
            require(
                after <= project.revision,
                "history escaped the known project revision",
            )?;
        }
        require(
            after == project.revision,
            "history enumeration stopped before the committed revision",
        )?;
        Ok(
            json!({"kind": "event_history_only", "pages": pages, "events": events,
            "scene_object_projection_claim": false}),
        )
    }

    fn apply_sample(
        service: &StudioService,
        project: &mut Project,
        label: &str,
        change: Change,
    ) -> Result<Value> {
        let expected = RevisionStamp::from(&*project);
        let request_id = format!("reference-{label}-{}", Uuid::now_v7());
        let started = Instant::now();
        let outcome = service.apply(project.id, &expected, &request_id, &change)?;
        let micros = started.elapsed().as_micros();
        require(
            !outcome.replayed
                && outcome.previous_revision == expected.revision
                && outcome.project.revision == expected.revision + 1,
            "measured edit must be one new committed revision",
        )?;
        *project = outcome.project;
        Ok(
            json!({"name": label, "micros": micros, "request_id": request_id,
            "previous_revision": expected.revision, "revision": project.revision, "change": change}),
        )
    }

    fn ten_changes(project: &Project, title: &str) -> Vec<(&'static str, Change)> {
        let first = project.scenes[0].id;
        let second = project.scenes[1].id;
        vec![
            (
                "01-scene-objective",
                Change::UpdateSceneObjective {
                    scene_id: first,
                    objective: "First measured edit of the complete imported reference project."
                        .into(),
                },
            ),
            (
                "02-project-title",
                Change::RenameProject {
                    title: format!("{title} · measured"),
                },
            ),
            (
                "03-narrative-premise",
                Change::SetNarrativePremise {
                    premise: "Retain the complete authored project through ten durable changes."
                        .into(),
                },
            ),
            (
                "04-scene-status",
                Change::SetSceneStatus {
                    scene_id: first,
                    status: SceneStatus::Review,
                },
            ),
            (
                "05-marker",
                Change::AddMarker {
                    at: RationalTime { num: 3, den: 1 },
                    label: "Measured review marker".into(),
                },
            ),
            (
                "06-renderer-choice",
                Change::SetSceneRenderer {
                    scene_id: second,
                    renderer: RendererKind::Mlt,
                },
            ),
            (
                "07-retained-style-lock",
                Change::SetLock {
                    resource: project.resource_key(),
                    kind: LockKind::Style,
                    note: "Persist this exact lock after revision ten".into(),
                },
            ),
            (
                "08-second-status",
                Change::SetSceneStatus {
                    scene_id: second,
                    status: SceneStatus::NeedsWork,
                },
            ),
            (
                "09-second-objective",
                Change::UpdateSceneObjective {
                    scene_id: second,
                    objective: "The second scene remains independently editable.".into(),
                },
            ),
            (
                "10-final-objective",
                Change::UpdateSceneObjective {
                    scene_id: first,
                    objective: "The exact tenth change survives SQLite close/reopen.".into(),
                },
            ),
        ]
    }

    fn percentile(samples: &[u128]) -> u128 {
        let mut ordered = samples.to_vec();
        ordered.sort_unstable();
        ordered[(ordered.len() * 95).div_ceil(100).max(1) - 1]
    }

    fn metric(samples: &[u128], budget_ms: Option<u128>, required_ms: Option<u128>) -> Value {
        json!({"samples": samples.len(), "budget_ms": budget_ms,
            "required_budget_ms": required_ms, "p95_micros": percentile(samples),
            "max_micros": samples.iter().copied().max().unwrap_or_default(),
            "samples_micros": samples, "percentile_method": "nearest-rank",
            "outliers_removed": 0})
    }

    fn optional_budget(name: &str, fallback: Option<u128>) -> Result<Option<u128>> {
        match env::var(name) {
            Ok(value) => Ok(Some(u128::from(value.parse::<u64>()?))),
            Err(env::VarError::NotPresent) => Ok(fallback),
            Err(error) => Err(error.into()),
        }
    }

    fn configured_budget(name: &str, fallback: u128) -> Result<u128> {
        match env::var(name) {
            Ok(value) => Ok(u128::from(value.parse::<u64>()?)),
            Err(env::VarError::NotPresent) => Ok(fallback),
            Err(error) => Err(error.into()),
        }
    }

    fn database_files(path: &Path) -> Result<Value> {
        let mut files = Vec::new();
        for suffix in ["", "-wal", "-shm"] {
            let mut name = path.as_os_str().to_os_string();
            name.push(suffix);
            let candidate = PathBuf::from(name);
            match fs::metadata(&candidate) {
                Ok(metadata) => files.push(json!({"path": candidate, "bytes": metadata.len()})),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error.into()),
            }
        }
        Ok(json!(files))
    }

    fn prepare(
        profile: Profile,
        brief: BriefSpec,
        folder: &Path,
        report: &mut Value,
    ) -> Result<(StudioService, Project, PathBuf)> {
        let setup = Instant::now();
        report["stage"] = json!("generate_validated_genesis");
        let seed = genesis(profile, brief)?;
        write_json(&folder.join("genesis-project.json"), &seed)?;
        let genesis_dir = folder.join("genesis-store");
        let target_dir = folder.join("target-store");
        fs::create_dir(&genesis_dir)?;
        fs::create_dir(&target_dir)?;
        let mut seed_store = Store::open(genesis_dir.join("genesis.sqlite3"))?;
        seed_store.create_project(&seed)?;
        require(
            seed_store.load_project(seed.id)? == seed,
            "genesis SQL readback differs from authored fixture",
        )?;
        let backup = seed_store.export_project(seed.id)?;
        require(
            backup.project.revision == 0 && backup.events.is_empty(),
            "genesis backup must be a genuine revision-zero export",
        )?;
        write_json(&folder.join("genesis-backup.json"), &backup)?;
        drop(seed_store);
        let db = target_dir.join("motionwright.sqlite3");
        let opened = Instant::now();
        let service = StudioService::open(&db)?;
        report["fresh_store_open_micros"] = json!(opened.elapsed().as_micros());
        let import_started = Instant::now();
        let plan = service.inspect_import(&backup)?;
        write_json(&folder.join("import-plan.json"), &plan)?;
        let mut project = service.import_project(&backup)?;
        report["reference_import_micros"] = json!(import_started.elapsed().as_micros());
        require(
            project.generation != seed.generation && project.id == seed.id,
            "reference import must preserve project identity and rotate generation",
        )?;
        let mut expected_import = seed;
        expected_import.generation = project.generation;
        expected_import.updated_at = project.updated_at;
        require(
            expected_import == project,
            "import changed authored fields beyond generation/timestamp",
        )?;
        write_json(&folder.join("imported-project.json"), &project)?;
        report["stage"] = json!("measure_and_import_wav");
        let wav = folder.join("reference-voice.wav");
        write_wav(&wav, profile.seconds)?;
        project = service
            .import_voice_file(
                project.id,
                &RevisionStamp::from(&project),
                &format!("reference-voice-{}", Uuid::now_v7()),
                &wav,
                VoiceImportMetadata {
                    name: "reference-voice.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Synthetic PCM duration fixture; no human narration claim".into(),
                },
            )?
            .project;
        project = service.project(project.id)?;
        report["dataset"] = shape(&project)?;
        report["scene_count"] = json!(project.scenes.len());
        report["setup_revision"] = json!(project.revision);
        report["stage"] = json!("validate_persisted_reference_shape");
        write_json(&folder.join("prepared-project.json"), &project)?;
        write_json(&folder.join("case-report.json"), report)?;
        validate_shape(profile, &project)?;
        report["normative_reference"] = json!(true);
        report["reference_shape_verified"] = json!(true);
        report["setup_micros"] = json!(setup.elapsed().as_micros());
        Ok((service, project, db))
    }
    fn identities(project: &Project) -> Value {
        json!({
            "project_id": project.id, "generation": project.generation,
            "active_branch": project.active_branch,
            "branch_ids": project.branches.iter().map(|branch| branch.id).collect::<Vec<_>>(),
            "scenes": project.scenes.iter().map(|scene| json!({
                "id": scene.id,
                "beat_ids": scene.beats.iter().map(|beat| beat.id).collect::<Vec<_>>(),
                "nodes": scene.nodes.iter().map(|node| json!({
                    "id": node.id, "property_locks": node.property_locks
                })).collect::<Vec<_>>()
            })).collect::<Vec<_>>(),
            "assets": project.assets, "claims": project.brief.claims,
            "deliverables": project.deliverables,
        })
    }

    fn run_case(
        profile: Profile,
        brief: BriefSpec,
        folder: &Path,
        responsiveness: bool,
        report: &mut Value,
    ) -> Result<()> {
        let title = brief.title;
        let (service, mut project, db) = prepare(profile, brief, folder, report)?;
        let stable_identities = identities(&project);
        let original_locks = project.locks.clone();
        let baseline_cas = cas_snapshot(&service, &project)?;
        require(
            !baseline_cas.is_empty(),
            "reference case must own real CAS bytes",
        )?;
        write_json(
            &folder.join("identities-before-edits.json"),
            &stable_identities,
        )?;
        write_json(&folder.join("cas-before-edits.json"), &baseline_cas)?;
        report["stage"] = json!("open_complete_reference_project");
        let expected_open = project.clone();
        drop(service);
        let first_open_started = Instant::now();
        let service = StudioService::open(&db)?;
        project = service.project(expected_open.id)?;
        let first_open_micros = first_open_started.elapsed().as_micros();
        require(
            project == expected_open,
            "complete project differs at first reference reopen",
        )?;
        report["reference_open_micros"] = json!(first_open_micros);
        let revision_start = project.revision;
        let mut operations = Vec::with_capacity(10);
        let sequence_started = Instant::now();
        for (label, change) in ten_changes(&project, title) {
            report["stage"] = json!(label);
            let sample = apply_sample(&service, &mut project, label, change)?;
            operations.push(sample);
            report["operations"] = json!(operations);
            write_json(&folder.join("case-report.json"), report)?;
        }
        let revision_ten_micros = sequence_started.elapsed().as_micros();
        require(
            project.revision == revision_start + 10,
            "sequence did not commit exactly ten revisions",
        )?;
        validate_shape(profile, &project)?;
        require(
            identities(&project) == stable_identities,
            "ten changes lost an authored identity, claim, property lock, asset or variant",
        )?;
        require(
            original_locks
                .iter()
                .all(|lock| project.locks.contains(lock)),
            "ten changes lost an original lock",
        )?;
        report["revision_start"] = json!(revision_start);
        report["revision_end"] = json!(project.revision);
        report["revision_delta"] = json!(project.revision - revision_start);
        report["revision_ten_micros"] = json!(revision_ten_micros);
        report["post_import_first_scene_edit_micros"] = operations[0]["micros"].clone();
        report["first_editable_scene_preserved_after_revision_ten"] = json!(true);
        write_json(&folder.join("project-after-ten.json"), &project)?;
        write_json(
            &folder.join("history-after-ten.json"),
            &paged_history(&service, &project)?,
        )?;

        if env::var("MOTIONWRIGHT_BENCH_NEGATIVE").as_deref() == Ok("uneditable-reopen") {
            // A real supported mutation creates a Content lock before reopening.
            // The later edit must surface the real DomainError::Locked as failure.
            let change = Change::SetLock {
                resource: project.resource_key(),
                kind: LockKind::Content,
                note: "Controlled negative: reject the positive edit after reopen".into(),
            };
            report["negative_injection"] =
                apply_sample(&service, &mut project, "negative-content-lock", change)?;
        }
        let expected_reopen = project.clone();
        drop(service);
        report["stage"] = json!("reopen_after_ten_and_compare_complete_project");
        let reopen_started = Instant::now();
        let service = StudioService::open(&db)?;
        let mut reopened = service.project(expected_reopen.id)?;
        let reopen_micros = reopen_started.elapsed().as_micros();
        report["reopen_micros"] = json!(reopen_micros);
        require(
            reopened == expected_reopen,
            "whole Project changed across SQLite close/reopen",
        )?;
        require(
            cas_snapshot(&service, &reopened)? == baseline_cas,
            "CAS bytes changed across SQLite close/reopen",
        )?;
        report["full_project_equal_after_reopen"] = json!(true);
        report["claims_ids_and_all_locks_equal_after_reopen"] = json!(true);
        report["cas_bytes_equal_after_reopen"] = json!(true);
        write_json(&folder.join("project-after-reopen.json"), &reopened)?;
        report["stage"] = json!("positive_cas_edit_after_reopen");
        let expected = RevisionStamp::from(&reopened);
        let request_id = format!("reference-edit-after-reopen-{}", Uuid::now_v7());
        let edit = Change::RenameProject {
            title: format!("{title} · edited after reopen"),
        };
        let edit_started = Instant::now();
        match service.apply(reopened.id, &expected, &request_id, &edit) {
            Ok(outcome) => {
                require(
                    !outcome.replayed
                        && outcome.previous_revision == expected.revision
                        && outcome.project.revision == expected.revision + 1,
                    "edit after reopen was not one new durable revision",
                )?;
                reopened = outcome.project;
                report["post_reopen_edit"] = json!({"status": "PASS", "request_id": request_id,
                    "previous_revision": expected.revision, "revision": reopened.revision,
                    "micros": edit_started.elapsed().as_micros()});
            }
            Err(error) => {
                let class = if matches!(&error, StorageError::Domain(DomainError::Locked(_))) {
                    "domain_locked"
                } else {
                    "storage_error"
                };
                report["post_reopen_edit"] = json!({"status": "FAIL", "class": class,
                    "request_id": request_id, "error": error.to_string()});
                return Err(error.into());
            }
        }
        require(
            service.project(reopened.id)? == reopened,
            "positive edit after reopen did not persist",
        )?;
        require(
            identities(&reopened) == stable_identities,
            "edit after reopen changed retained identities or claims",
        )?;
        require(
            cas_snapshot(&service, &reopened)? == baseline_cas,
            "positive edit changed immutable CAS bytes",
        )?;
        write_json(&folder.join("project-edited-after-reopen.json"), &reopened)?;

        let mut warmup_samples = Vec::new();
        let mut measured_samples = Vec::new();
        if responsiveness {
            for index in 0..WARMUPS {
                report["stage"] = json!(format!("warmup-{index}"));
                warmup_samples.push(apply_sample(
                    &service,
                    &mut reopened,
                    &format!("warmup-{index}"),
                    Change::RenameProject {
                        title: format!("{title} · warmup {index}"),
                    },
                )?);
                report["warmup_operations"] = json!(warmup_samples);
                write_json(&folder.join("case-report.json"), report)?;
            }
            for index in 0..SAMPLES {
                report["stage"] = json!(format!("small-commit-{index}"));
                measured_samples.push(apply_sample(
                    &service,
                    &mut reopened,
                    &format!("sample-{index}"),
                    Change::RenameProject {
                        title: format!("{title} · sample {index}"),
                    },
                )?);
                report["measured_operations"] = json!(measured_samples);
                write_json(&folder.join("case-report.json"), report)?;
            }
        }
        let selected = if responsiveness {
            &measured_samples
        } else {
            &operations
        };
        let micros: Vec<u128> = selected
            .iter()
            .map(|sample| {
                sample["micros"]
                    .as_u64()
                    .map(u128::from)
                    .ok_or("sample duration is missing or out of range")
            })
            .collect::<std::result::Result<_, _>>()?;
        require(!micros.is_empty(), "no measured commit samples")?;
        let required_commit_ms = (profile.label == "S").then_some(250_u128);
        let commit_budget_ms = optional_budget(
            "MOTIONWRIGHT_SMALL_COMMIT_P95_BUDGET_MS",
            required_commit_ms,
        )?;
        report["small_commit"] = metric(&micros, commit_budget_ms, required_commit_ms);
        report["max_commit_micros"] = json!(
            operations
                .iter()
                .filter_map(|entry| entry["micros"].as_u64())
                .max()
        );
        report["all_warmup_samples_retained"] = json!(true);
        let expected_final = reopened;
        drop(service);
        let final_open_started = Instant::now();
        let final_service = StudioService::open(&db)?;
        let final_project = final_service.project(expected_final.id)?;
        let final_reopen_micros = final_open_started.elapsed().as_micros();
        require(
            final_project == expected_final,
            "full Project changed after measured samples and final reopen",
        )?;
        validate_shape(profile, &final_project)?;
        require(
            identities(&final_project) == stable_identities,
            "measured samples lost retained identities",
        )?;
        require(
            cas_snapshot(&final_service, &final_project)? == baseline_cas,
            "measured samples changed source CAS bytes",
        )?;
        let history = paged_history(&final_service, &final_project)?;
        report["event_count"] = json!(history["events"].as_array().ok_or("missing history")?.len());
        report["final_revision"] = json!(final_project.revision);
        report["final_reopen_micros"] = json!(final_reopen_micros);
        write_json(&folder.join("final-history.json"), &history)?;
        write_json(&folder.join("final-project.json"), &final_project)?;
        write_json(
            &folder.join("final-cas.json"),
            &cas_snapshot(&final_service, &final_project)?,
        )?;
        drop(final_service);
        report["database_files"] = database_files(&db)?;
        report["database_bytes"] = json!(fs::metadata(&db)?.len());
        let mut failures = Vec::new();
        let commit_p95 = percentile(&micros);
        if let Some(configured) = commit_budget_ms
            && commit_p95 >= configured.saturating_mul(1_000)
        {
            failures.push(
                json!({"criterion": "configured_small_commit_p95", "observed_micros": commit_p95,
                "limit_ms": configured}),
            );
        }
        if let Some(required) = required_commit_ms
            && commit_p95 >= required.saturating_mul(1_000)
        {
            failures.push(
                json!({"criterion": "reference_small_commit_p95", "observed_micros": commit_p95,
                "limit_ms": required}),
            );
        }
        if let Some(required) = profile.open_budget_ms {
            let variable = if profile.label == "S" {
                "MOTIONWRIGHT_S_REOPEN_BUDGET_MS"
            } else {
                "MOTIONWRIGHT_M_REOPEN_BUDGET_MS"
            };
            let configured = configured_budget(variable, required)?;
            report["reopen_budget_ms"] = json!(configured);
            report["required_reopen_budget_ms"] = json!(required);
            for (phase, value) in [
                ("reference_open", first_open_micros),
                ("after_ten", reopen_micros),
                ("after_samples", final_reopen_micros),
            ] {
                if value >= configured.saturating_mul(1_000)
                    || value >= required.saturating_mul(1_000)
                {
                    failures.push(json!({"criterion": "reference_open", "phase": phase,
                        "observed_micros": value, "configured_limit_ms": configured, "required_limit_ms": required}));
                }
            }
        } else {
            report["reopen_budget_ms"] = Value::Null;
            report["required_reopen_budget_ms"] = Value::Null;
        }
        report["budget_failures"] = json!(failures);
        report["status"] = json!(if failures.is_empty() { "PASS" } else { "FAIL" });
        report["stage"] = json!("complete");
        Ok(())
    }
    fn source_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
    }

    pub fn retain_sources(destination: &Path) -> Result<(Value, Value)> {
        fs::create_dir(destination)?;
        let root = source_root().canonicalize()?;
        let mut retained = serde_json::Map::new();
        let mut identity = serde_json::Map::new();
        for relative in [
            "Cargo.lock",
            "crates/service/examples/acceptance_bench.rs",
            "crates/service/examples/responsiveness_bench.rs",
        ] {
            let source = root.join(relative);
            let bytes = fs::read(&source)?;
            let name = Path::new(relative)
                .file_name()
                .ok_or("source has no filename")?;
            let target = destination.join(name);
            let mut output = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&target)?;
            output.write_all(&bytes)?;
            output.sync_all()?;
            let sha256 = hex::encode(Sha256::digest(&bytes));
            retained.insert(
                relative.into(),
                json!({"source": source, "retained": target,
                "sha256": sha256, "bytes": bytes.len()}),
            );
            identity.insert(
                relative.into(),
                json!({"sha256": sha256, "bytes": bytes.len()}),
            );
        }
        Ok((Value::Object(retained), Value::Object(identity)))
    }

    fn pending_capabilities() -> Value {
        json!({
            "studio_selection_s_p95": {"status": "NOT_RUN", "required_limit_ms": 100,
                "reason": "This process has no Studio event loop or selection-to-paint observation."},
            "studio_scrub_or_proxy_ui_blocking": {"status": "NOT_RUN", "required_limit_ms": 200,
                "reason": "Service timings do not measure UI blocking or input-to-paint."},
            "large_scene_object_projection_paging": {"status": "NOT_RUN",
                "reason": "The available service APIs load a full Project. Event-history keyset paging is measured separately and does not satisfy this capability."},
            "installed_first_use": {"status": "NOT_RUN",
                "reason": "Prepared project API editing does not measure installation or first human use."},
            "native_render_cache_avoided_work": {"status": "NOT_RUN",
                "reason": "No renderer execution or avoided render work is counted by this fixture."}
        })
    }

    pub fn source_attributed(observation: &Value) -> bool {
        let compiled = observation["source_sha_compiled"]
            .as_str()
            .unwrap_or_default();
        let runtime = observation["source_sha_runtime"]
            .as_str()
            .unwrap_or_default();
        compiled.len() == 40
            && compiled.bytes().all(|b| b.is_ascii_hexdigit())
            && compiled == runtime
            && observation["executable"].is_object()
    }

    fn emit_budget_diagnostics(report: &Value, responsiveness: bool) {
        if !responsiveness || report["size"] != "S" {
            return;
        }
        if let (Some(p95), Some(budget_ms)) = (
            report["small_commit"]["p95_micros"].as_u64(),
            report["small_commit"]["budget_ms"].as_u64(),
        ) {
            let budget_us = u128::from(budget_ms) * 1_000;
            if u128::from(p95) >= budget_us {
                eprintln!("S-profile small commit p95 {p95}us exceeds {budget_us}us budget");
            }
        }
        if let Some(budget_ms) = report["reopen_budget_ms"].as_u64() {
            let budget_us = u128::from(budget_ms) * 1_000;
            let observed = [
                "reference_open_micros",
                "reopen_micros",
                "final_reopen_micros",
            ]
            .iter()
            .filter_map(|key| report[*key].as_u64())
            .max();
            if let Some(observed) = observed
                && u128::from(observed) >= budget_us
            {
                eprintln!("S-profile reopen {observed}us exceeds {budget_us}us budget");
            }
        }
    }

    fn suite(responsiveness: bool) -> Result<ExitCode> {
        let negative = match env::var("MOTIONWRIGHT_BENCH_NEGATIVE") {
            Ok(value) if matches!(value.as_str(), "truncated-profile" | "uneditable-reopen") => Some(value),
            Ok(_) => return Err("MOTIONWRIGHT_BENCH_NEGATIVE accepts only truncated-profile or uneditable-reopen; unset it for normal runs".into()),
            Err(env::VarError::NotPresent) => None,
            Err(error) => return Err(error.into()),
        };
        let root = retained_directory(if responsiveness {
            "responsiveness"
        } else {
            "reference-suite"
        })?;
        let before = context();
        let (sources_before, identity_before) = retain_sources(&root.join("source-before"))?;
        write_json(&root.join("context-before.json"), &before)?;
        write_json(&root.join("source-before.json"), &sources_before)?;
        let selected = if responsiveness {
            let selected_name = match env::var("MOTIONWRIGHT_RESPONSIVENESS_PROFILE") {
                Ok(value) => value,
                Err(env::VarError::NotPresent) => "S".into(),
                Err(error) => return Err(error.into()),
            };
            vec![(RESPONSIVENESS_BRIEF, Profile::named(&selected_name)?)]
        } else {
            let mut cases = Vec::new();
            for brief in REFERENCE_BRIEFS {
                for profile in ["S", "M", "L"] {
                    cases.push((brief, Profile::named(profile)?));
                }
            }
            cases
        };
        let mut results = Vec::new();
        for (brief, profile) in selected {
            let folder = root.join(format!("{}-{}", brief.slug, profile.label));
            fs::create_dir(&folder)?;
            let mut report = json!({
                "brief": brief.slug, "size": profile.label, "requested_scene_count": profile.scenes,
                "dataset_family": "normative_reference_profile", "normative_reference": false,
                "required_shape": {"scenes": profile.scenes, "canvas_nodes": profile.nodes,
                    "duration_seconds": profile.seconds, "beats": profile.required_beats,
                    "deliverable_variants": profile.required_variants,
                    "width": 1920, "height": 1080, "frame_rate": "30/1"},
                "fixture_policy": "M/L beat counts are explicit fixture composition, not invented normative requirements. All profiles use measured synthetic PCM at 8000 Hz.",
                "artifact_directory": folder, "status": "RUNNING", "stage": "starting",
                "negative_fixture": negative,
                "context_file": root.join("context-before.json"),
            });
            write_json(&folder.join("case-report.json"), &report)?;
            if let Err(error) = run_case(profile, brief, &folder, responsiveness, &mut report) {
                report["status"] = json!("FAIL");
                report["error"] = json!(error.to_string());
            }
            write_json(&folder.join("case-report.json"), &report)?;
            results.push(report);
            write_json(&root.join("partial-results.json"), &results)?;
        }
        let after = context();
        let (sources_after, identity_after) = retain_sources(&root.join("source-after"))?;
        write_json(&root.join("context-after.json"), &after)?;
        write_json(&root.join("source-after.json"), &sources_after)?;
        let attribution_ok = source_attributed(&before)
            && source_attributed(&after)
            && before["source_sha_compiled"] == after["source_sha_compiled"]
            && before["executable"] == after["executable"]
            && before["compiler_version"] == after["compiler_version"]
            && identity_before == identity_after;
        let passed = attribution_ok && results.iter().all(|case| case["status"] == "PASS");
        let source_sha = before["source_sha_compiled"]
            .as_str()
            .unwrap_or("unattributed");
        let mut report = if responsiveness {
            results.first().cloned().ok_or("no responsiveness case")?
        } else {
            json!({"briefs": 3, "datasets": results.len(), "results": results})
        };
        report["schema"] = json!(if responsiveness {
            "motionwright.responsiveness.v1"
        } else {
            "motionwright.performance.reference.v1"
        });
        report["source_sha"] = json!(source_sha);
        report["generated_by"] = json!(if responsiveness {
            "motionwright-service/examples/responsiveness_bench"
        } else {
            "motionwright-service/examples/acceptance_bench"
        });
        report["dataset_family"] = json!("normative_reference_profile");
        report["source_binding_consistent"] = json!(attribution_ok);
        report["source_attribution_verified"] = json!(false);
        report["source_attribution_scope"] = json!(
            "Actions checkout attestation remains external; this binary checks compiled/runtime SHA agreement and exact retained files."
        );
        report["source_context_before"] = before;
        report["source_context_after"] = after;
        report["retained_sources_before"] = sources_before;
        report["retained_sources_after"] = sources_after;
        report["artifact_directory"] = json!(root);
        report["pending_capabilities"] = pending_capabilities();
        report["human_feedback"] =
            json!("NOT_RUN: no human participants, ratings or consent are fabricated.");
        report["comparative_claims"] =
            json!("NONE: runner-specific Motionwright measurements only.");
        report["status"] = json!(if passed { "PASS" } else { "FAIL" });
        write_json(&root.join("report.json"), &report)?;
        let stdout = std::io::stdout();
        let mut output = stdout.lock();
        serde_json::to_writer_pretty(&mut output, &report)?;
        writeln!(output)?;
        output.flush()?;
        emit_budget_diagnostics(&report, responsiveness);
        Ok(if passed {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        })
    }

    fn entry(responsiveness: bool) -> ExitCode {
        match suite(responsiveness) {
            Ok(code) => code,
            Err(error) => {
                let failure = json!({"schema": "motionwright.performance.configuration-error.v1",
                    "status": "FAIL", "error": error.to_string(),
                    "generated_by": if responsiveness { "responsiveness_bench" } else { "acceptance_bench" }});
                let stdout = std::io::stdout();
                let mut output = stdout.lock();
                let _ = serde_json::to_writer_pretty(&mut output, &failure);
                let _ = writeln!(output);
                let _ = output.flush();
                ExitCode::FAILURE
            }
        }
    }

    #[cfg(test)]
    mod fixture_tests {
        use super::*;

        fn reopened_fixture(brief: BriefSpec) -> Project {
            assert!(env::var_os("MOTIONWRIGHT_BENCH_NEGATIVE").is_none());
            let root = tempfile::tempdir().unwrap();
            let database = root.path().join("fixture.sqlite3");
            let project = genesis(Profile::named("S").unwrap(), brief).unwrap();
            assert_eq!(project.scenes.len(), 12);
            assert_eq!(
                project.scenes.iter().map(|s| s.beats.len()).sum::<usize>(),
                80
            );
            assert_eq!(
                project.scenes.iter().map(|s| s.nodes.len()).sum::<usize>(),
                300
            );
            verify_persisted_timeline(&project, 300).unwrap();
            {
                let mut store = Store::open(&database).unwrap();
                store.create_project(&project).unwrap();
                assert_eq!(store.load_project(project.id).unwrap(), project);
            }
            let store = Store::open(&database).unwrap();
            let reopened = store.load_project(project.id).unwrap();
            assert_eq!(reopened, project);
            reopened
        }

        // Labels, IDs, timestamps, order and repetition cannot forge diversity.
        fn content_families(project: &Project) -> [BTreeSet<String>; 3] {
            let mut brief = BTreeSet::from([project.brief.objective.clone()]);
            brief.extend(project.brief.claims.iter().map(|claim| claim.text.clone()));
            let mut scenes = BTreeSet::new();
            let mut objects = BTreeSet::new();
            for scene in &project.scenes {
                scenes.insert(scene.objective.clone());
                scenes.extend(scene.beats.iter().map(|beat| beat.objective.clone()));
                objects.extend(scene.nodes.iter().filter_map(|node| node.text.clone()));
            }
            [brief, scenes, objects]
        }

        fn distinct_family_count(family: usize) -> usize {
            REFERENCE_BRIEFS
                .into_iter()
                .map(reopened_fixture)
                .map(|project| content_families(&project)[family].clone())
                .collect::<BTreeSet<_>>()
                .len()
        }

        #[test]
        fn reference_briefs_have_distinct_brief_content_after_sqlite_reopen() {
            assert_eq!(
                distinct_family_count(0),
                3,
                "three titles must carry unrelated brief content"
            );
        }

        #[test]
        fn reference_briefs_have_distinct_scene_and_beat_content_after_sqlite_reopen() {
            assert_eq!(
                distinct_family_count(1),
                3,
                "three titles must carry unrelated scene and beat content"
            );
        }

        #[test]
        fn reference_briefs_have_distinct_node_text_after_sqlite_reopen() {
            assert_eq!(
                distinct_family_count(2),
                3,
                "three titles must carry unrelated object text"
            );
        }

        #[test]
        fn changing_labels_only_preserves_authored_content_after_sqlite_reopen() {
            let original = reopened_fixture(REFERENCE_BRIEFS[0]);
            let relabeled = reopened_fixture(BriefSpec {
                slug: "unlisted-fixture-input",
                title: "An unrelated label cannot supply missing authored content",
                ..REFERENCE_BRIEFS[0]
            });
            assert_ne!(original.id, relabeled.id);
            assert_ne!(original.title, relabeled.title);
            assert_eq!(content_families(&original), content_families(&relabeled));
        }
    }

    pub fn responsiveness_main() -> ExitCode {
        entry(true)
    }
    pub fn acceptance_main() -> ExitCode {
        entry(false)
    }
}

// acceptance_bench imports this file solely for the shared fixture support.
// This one standalone entry point is intentionally unused in that import mode.
#[allow(
    dead_code,
    reason = "standalone entry point; shared support is also imported by acceptance_bench"
)]
fn main() -> std::process::ExitCode {
    support::responsiveness_main()
}
