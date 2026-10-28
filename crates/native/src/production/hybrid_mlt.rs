//! The closed original 180-frame mixed-renderer MLT continuation.
//!
//! Only sealed HybridNativeSources from the real MC/Manim producer enter this
//! module. The existing all-MotionCanvas preflight remains unchanged. All
//! effects use the retained coordinator, its original SQLite root, and the
//! pinned Driver Host; neither an arbitrary scoped client nor native refs are
//! exposed to the caller.
use super::hybrid_budget::HybridNativeSources;
use super::mlt_multisegment_av::{MuxExpected, verify_exact_native_mux, verify_mux_artifact_file};
use super::*;
use semwright_video_domain::native_observation::{
    ObservationProfile, PNG_LIMIT, REPORT_LIMIT, parse_complete_observation,
};

const FRAME_COUNT: u64 = 180;
const CLIP_FRAMES: u64 = 60;
const MEDIA_MAX_BYTES: u64 = 256_000_000;
const MASTER_MAX_BYTES: u64 = 32 * 1024 * 1024;
const REPORT_MAX_BYTES: u64 = REPORT_LIMIT as u64;
const PNG_MAX_BYTES: u64 = PNG_LIMIT as u64;
const NATIVE_INPUT_MAX_BYTES: u64 = 8 * 1024 * 1024;
const WAV_BYTES: u64 = 1_152_044;
const SEQUENCE_NAME: &str = "Original Motionwright MC-Manim-MC cut";
const TRACK_NAME: &str = "Original native hybrid contributions";

/// Created only after the original native timeline and its files are checked.
/// JSON can be inspected, but cannot reconstruct this retained continuation.
pub struct HybridNativeTimeline {
    project_sha256: String,
    connection_identity: String,
    source_evidence_sha256: String,
    evidence: Value,
}
impl HybridNativeTimeline {
    pub fn evidence(&self) -> &Value {
        &self.evidence
    }
}

/// This is a retained native return, not a grant or a human acceptance.
pub struct HybridNativeMaster {
    evidence: Value,
}
impl HybridNativeMaster {
    pub fn evidence(&self) -> &Value {
        &self.evidence
    }
}

fn require(condition: bool, message: &'static str) -> NativeResult<()> {
    if condition {
        Ok(())
    } else {
        Err(backend(message))
    }
}
fn sha_literal(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn text_at(value: &Value, pointer: &str) -> NativeResult<String> {
    required_string(
        value,
        pointer,
        "Original hybrid native evidence is missing a required string",
    )
}
fn opaque(value: &Value, key: &str) -> NativeResult<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| {
            !value.is_empty() && value.len() <= 80 && !value.chars().any(char::is_control)
        })
        .map(str::to_owned)
        .ok_or_else(|| backend("Hybrid MLT result has no valid native reference"))
}
#[derive(Clone)]
struct Session {
    project: String,
    revision: String,
}
fn new_session(value: &Value) -> NativeResult<Session> {
    let revision = opaque(value, "revision")?;
    require(
        sha_literal(&revision),
        "Hybrid MLT project has no exact native revision",
    )?;
    Ok(Session {
        project: opaque(value, "project")?,
        revision,
    })
}
fn changed_session(prior: &Session, value: &Value) -> NativeResult<Session> {
    require(
        value.get("applied").and_then(Value::as_bool) == Some(true)
            && value.get("expected_revision").and_then(Value::as_str)
                == Some(prior.revision.as_str())
            && value
                .get("support")
                .and_then(Value::as_str)
                .is_some_and(|level| level != "UNSUPPORTED"),
        "Hybrid MLT edit did not apply to its original expected revision",
    )?;
    let revision = opaque(value, "resulting_revision")?;
    require(
        sha_literal(&revision) && revision != prior.revision,
        "Hybrid MLT edit did not advance its native revision",
    )?;
    Ok(Session {
        project: opaque(value, "project")?,
        revision,
    })
}
fn created(value: &Value, kind: &str) -> NativeResult<String> {
    let entries = value
        .get("created_refs")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Hybrid MLT edit omitted its original created references"))?;
    let mut selected = entries
        .iter()
        .filter(|entry| entry.get("kind").and_then(Value::as_str) == Some(kind));
    let entry = selected
        .next()
        .ok_or_else(|| backend("Hybrid MLT edit did not create its required entity"))?;
    require(
        selected.next().is_none(),
        "Hybrid MLT created references are ambiguous",
    )?;
    opaque(entry, "reference")
}
fn named_reference(value: &Value, revision: &str, name: &str) -> NativeResult<String> {
    require(
        value.get("revision").and_then(Value::as_str) == Some(revision),
        "Hybrid MLT lookup returned a stale revision",
    )?;
    let items = value
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Hybrid MLT lookup omitted its native entities"))?;
    require(
        value.get("total").and_then(Value::as_u64) == Some(items.len() as u64)
            && !items.is_empty()
            && items.len() <= 100
            && value.get("cursor").is_some_and(Value::is_null),
        "Hybrid MLT lookup is incomplete or unbounded",
    )?;
    let mut matches = items
        .iter()
        .filter(|entry| entry.get("name").and_then(Value::as_str) == Some(name));
    let entry = matches
        .next()
        .ok_or_else(|| backend("Hybrid MLT lookup omitted the exact authored entity"))?;
    require(
        matches.next().is_none(),
        "Hybrid MLT authored name is ambiguous",
    )?;
    opaque(entry, "reference")
}

#[derive(Clone)]
struct Artifact {
    path: String,
    sha256: String,
    bytes: u64,
}
impl Artifact {
    fn descriptor(&self) -> Value {
        json!({"root":"output","path":self.path,"sha256":self.sha256,"bytes":self.bytes})
    }
}
fn artifact(
    root: &Path,
    value: &Value,
    expected_path: &str,
    maximum: u64,
) -> NativeResult<Artifact> {
    let digest = text_at(value, "/sha256")?;
    let count = value
        .get("bytes")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("Hybrid native artifact has no original byte count"))?;
    require(
        value.get("root").and_then(Value::as_str) == Some("output")
            && value.get("path").and_then(Value::as_str) == Some(expected_path)
            && sha_literal(&digest)
            && (1..=maximum).contains(&count),
        "Hybrid native artifact differs from the exact original output",
    )?;
    let raw = read_verified_production_artifact(root, expected_path, &digest, maximum)?;
    require(
        raw.len() as u64 == count,
        "Hybrid native artifact bytes differ from its original receipt",
    )?;
    Ok(Artifact {
        path: expected_path.into(),
        sha256: digest,
        bytes: count,
    })
}
fn verify_ffv1(root: &Path, value: &Value, expected_path: &str) -> NativeResult<Artifact> {
    require(
        value.get("codec").and_then(Value::as_str) == Some("ffv1")
            && value.get("container").and_then(Value::as_str) == Some("matroska")
            && value.get("frame_count").and_then(Value::as_u64) == Some(CLIP_FRAMES)
            && value.get("width").and_then(Value::as_u64) == Some(1920)
            && value.get("height").and_then(Value::as_u64) == Some(1080)
            && value.get("fps_num").and_then(Value::as_u64) == Some(30)
            && value.get("fps_den").and_then(Value::as_u64) == Some(1)
            && value.pointer("/media/video").and_then(Value::as_bool) == Some(true)
            && value.pointer("/media/audio").and_then(Value::as_bool) == Some(false),
        "Hybrid original MC encode did not return exactly sixty audio-free FFV1 frames",
    )?;
    artifact(
        root,
        value
            .get("artifact")
            .ok_or_else(|| backend("Hybrid FFV1 artifact is absent"))?,
        expected_path,
        MEDIA_MAX_BYTES,
    )
}
fn verify_lossless(
    root: &Path,
    value: &Value,
    expected_path: &str,
    job: &str,
    revision: &str,
) -> NativeResult<Artifact> {
    require(
        value.get("state").and_then(Value::as_str) == Some("succeeded")
            && value.get("job").and_then(Value::as_str) == Some(job)
            && value.get("project_revision").and_then(Value::as_str) == Some(revision)
            && value.get("profile").and_then(Value::as_str) == Some("lossless-video-only")
            && value.get("output").and_then(Value::as_str) == Some(expected_path)
            && value.get("error").is_some_and(Value::is_null)
            && value.get("cancellation_requested").and_then(Value::as_bool) == Some(false),
        "Hybrid lossless result does not identify its exact native render job",
    )?;
    require(
        value.pointer("/media/video").and_then(Value::as_bool) == Some(true)
            && value.pointer("/media/audio").and_then(Value::as_bool) == Some(false)
            && value.pointer("/media/width").and_then(Value::as_u64) == Some(1920)
            && value.pointer("/media/height").and_then(Value::as_u64) == Some(1080)
            && value.pointer("/media/frames").and_then(Value::as_u64) == Some(FRAME_COUNT)
            && value
                .pointer("/media/codecs")
                .and_then(Value::as_array)
                .is_some_and(|codecs| codecs.len() == 1 && codecs[0].as_str() == Some("ffv1")),
        "Hybrid visual result is not an independently counted 180-frame FFV1 video-only source",
    )?;
    let numerator = value
        .pointer("/media/duration_num")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| backend("Hybrid visual duration is absent"))?;
    let denominator = value
        .pointer("/media/duration_den")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| backend("Hybrid visual timebase is absent"))?;
    require(
        (u128::from(numerator) * 30).abs_diff(u128::from(FRAME_COUNT) * u128::from(denominator))
            <= u128::from(denominator),
        "Hybrid visual duration differs from the exact authored clock",
    )?;
    artifact(
        root,
        value
            .get("artifact")
            .ok_or_else(|| backend("Hybrid visual artifact is absent"))?,
        expected_path,
        MEDIA_MAX_BYTES,
    )
}

fn exact_source_roster(value: &Value, expected: &[Artifact]) -> NativeResult<()> {
    let actual = value
        .as_array()
        .ok_or_else(|| backend("Original hybrid native source roster is absent"))?;
    require(
        !expected.is_empty() && actual.len() == expected.len(),
        "Original hybrid native source roster has a different cardinality",
    )?;
    let mut unmatched = expected
        .iter()
        .map(Artifact::descriptor)
        .collect::<Vec<_>>();
    for original in actual {
        let index = unmatched
            .iter()
            .position(|expected| expected == original)
            .ok_or_else(|| {
                backend("Original hybrid native staging consumed a different source artifact")
            })?;
        unmatched.swap_remove(index);
    }
    require(
        unmatched.is_empty(),
        "Original hybrid native staging omitted an authored source",
    )
}

/// Read the ORIGINAL native Host report, XML and PNGs. This does not run a
/// decoder, synthesize a report, rename a different observation, or infer PTS.
fn complete_video_observation(
    root: &Path,
    media: &Artifact,
    original_result: &Value,
    expected_profile: ObservationProfile,
    sources: &[Artifact],
) -> NativeResult<Value> {
    let original_receipt = original_result.get("color_observation").ok_or_else(|| {
        backend("Original hybrid native return omitted its color observation descriptor")
    })?;
    let path = format!("{}.color-observation.json", media.path);
    let report_sha = text_at(original_receipt, "/sha256")?;
    let report_bytes = original_receipt
        .get("bytes")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("Original hybrid native report has no receipt byte count"))?;
    require(
        original_receipt.get("root").and_then(Value::as_str) == Some("output")
            && original_receipt.get("path").and_then(Value::as_str) == Some(path.as_str())
            && sha_literal(&report_sha)
            && (1..=REPORT_MAX_BYTES).contains(&report_bytes),
        "Original hybrid native color descriptor does not bind this artifact",
    )?;
    let raw = read_verified_production_artifact(root, &path, &report_sha, REPORT_MAX_BYTES)?;
    require(
        raw.len() as u64 == report_bytes,
        "Original hybrid native report byte count changed",
    )?;
    let parsed = parse_complete_observation(&raw).map_err(|_| {
        backend("Original hybrid native report failed the pinned canonical observation parser")
    })?;
    require(
        parsed.profile() == expected_profile,
        "Original hybrid native report belongs to another observation profile",
    )?;
    let document = parsed.document().clone();
    require(
        document.get("artifact") == Some(&media.descriptor())
            && document.get("frame_count").and_then(Value::as_u64) == Some(FRAME_COUNT)
            && document.get("canonical_fps_num").and_then(Value::as_u64) == Some(30)
            && document.get("canonical_fps_den").and_then(Value::as_u64) == Some(1)
            && document.pointer("/stream/width").and_then(Value::as_u64) == Some(1920)
            && document.pointer("/stream/height").and_then(Value::as_u64) == Some(1080)
            && document.get("frame_path_prefix").and_then(Value::as_str)
                == Some(format!("{}.", media.path).as_str())
            && document
                .get("exact_clock")
                .and_then(Value::as_bool)
                .is_some(),
        "Original hybrid native color observation has a different artifact or frame clock",
    )?;
    // Host render.start stages imported paths. Bind that actual staging to the
    // previously admitted originals; matching output/report hashes alone would
    // permit a changed input at the same path to qualify as the original cut.
    let native_input = original_result
        .get("native_input")
        .ok_or_else(|| backend("Original hybrid native return omitted its editable MLT input"))?;
    let xml = artifact(
        root,
        native_input,
        &format!("{}.native-input.mlt", media.path),
        NATIVE_INPUT_MAX_BYTES,
    )?;
    let binding = json!({"source_kind":"mlt_render_project_bytes","root":"output",
        "path":xml.path,"sha256":xml.sha256,"bytes":xml.bytes,"source_first_frame":0});
    require(
        document.get("source_binding") == Some(&binding),
        "Original hybrid native report refers to a different editable MLT input",
    )?;
    let native_sources = original_result
        .get("native_input_sources")
        .ok_or_else(|| backend("Original hybrid native return omitted its staged source roster"))?;
    require(
        document.get("native_input_sources") == Some(native_sources),
        "Original hybrid native report changed its staged source roster",
    )?;
    // SDK ordering follows asset IDs, not cut order. Compare an exact closed
    // multiset without sorting or replacing either retained original document.
    exact_source_roster(native_sources, sources)?;
    for source in sources {
        artifact(root, &source.descriptor(), &source.path, MEDIA_MAX_BYTES)?;
    }
    let frames = document
        .get("frames")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Original hybrid native color observation has no frame roster"))?;
    require(
        frames.len() == FRAME_COUNT as usize,
        "Original hybrid native frame roster is incomplete",
    )?;
    for (index, frame) in frames.iter().enumerate() {
        let file = format!("decoded-{index:06}.png");
        let png_sha = text_at(frame, "/png_sha256")?;
        let rgba_sha = text_at(frame, "/rgba_sha256")?;
        let bytes = frame
            .get("png_bytes")
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Original hybrid native PNG byte count is absent"))?;
        require(
            frame.get("file").and_then(Value::as_str) == Some(file.as_str())
                && sha_literal(&png_sha)
                && sha_literal(&rgba_sha)
                && (9..=PNG_MAX_BYTES).contains(&bytes),
            "Original hybrid native PNG order or identity changed",
        )?;
        let png = read_verified_production_artifact(
            root,
            &format!("{}.{}", media.path, file),
            &png_sha,
            PNG_MAX_BYTES,
        )?;
        require(
            png.len() as u64 == bytes,
            "Original hybrid native PNG bytes changed",
        )?;
    }
    Ok(json!({"original_receipt":original_receipt,"document":document}))
}

fn source_evidence_sha256(sources: &HybridNativeSources) -> NativeResult<String> {
    let bytes = serde_json::to_vec(sources.evidence())
        .map_err(|_| invalid("Original hybrid source evidence cannot be bound"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

async fn edit(
    coordinator: &ProductionCoordinator,
    project_id: Uuid,
    expected: &RevisionStamp,
    request_id: &str,
    command: &str,
    prior: Session,
    extra: Value,
) -> NativeResult<(Session, Value)> {
    let mut args = extra
        .as_object()
        .cloned()
        .ok_or_else(|| invalid("Hybrid edit requires closed arguments"))?;
    args.insert("project".into(), prior.project.clone().into());
    args.insert("expected_revision".into(), prior.revision.clone().into());
    let original = coordinator
        .execute(
            project_id,
            expected,
            request_id,
            command,
            Value::Object(args),
            true,
        )
        .await?;
    let data = response_data(&original)?;
    Ok((changed_session(&prior, data)?, data.clone()))
}
async fn reference(
    coordinator: &ProductionCoordinator,
    project_id: Uuid,
    expected: &RevisionStamp,
    request_id: &str,
    session: &Session,
    sequence: Option<&str>,
    name: &str,
) -> NativeResult<String> {
    let (command, mut args) = if let Some(sequence) = sequence {
        ("driver.mlt-video.track.list", json!({"sequence":sequence}))
    } else {
        ("driver.mlt-video.sequence.list", json!({}))
    };
    args["project"] = session.project.clone().into();
    args["limit"] = json!(100);
    let response = coordinator
        .execute(project_id, expected, request_id, command, args, false)
        .await?;
    named_reference(response_data(&response)?, &session.revision, name)
}

impl ProductionCoordinator {
    /// Assemble exactly the retained MC/Manim/MC originals. Neither a JSON
    /// caller nor another project can construct the input source capability.
    pub async fn assemble_native_hybrid_timeline(
        &self,
        sources: &HybridNativeSources,
    ) -> NativeResult<HybridNativeTimeline> {
        let scoped = self.hybrid_mlt_scope(sources)?;
        let id = sources.project.id;
        let request = &sources.request_prefix;
        let expected = &sources.expected;
        let output = &scoped.client.connection().output_root;
        let namespace = &sources.namespace;
        require(
            sources.motion_canvas.segments.len() == 2,
            "Hybrid source lost a native MC contribution",
        )?;
        let mut clips = Vec::with_capacity(3);
        let mut originals = Vec::new();
        // The middle contribution is the real static Manim native MP4, not an
        // invented MC manifest or a renderer label on a different source.
        let manim = &sources.manim;
        require(
            manim.get("renderer").and_then(Value::as_str) == Some("manim-community")
                && manim.get("project_revision").and_then(Value::as_u64) == Some(expected.revision)
                && manim.get("scene_id") == Some(&json!(sources.project.scenes[1].id))
                && manim
                    .pointer("/artifact/static_source")
                    .is_some_and(|source| source.is_object())
                && manim
                    .pointer("/artifact/static_source_custody_error")
                    .is_none(),
            "Hybrid middle contribution lacks its retained original static Manim source",
        )?;
        let original = manim
            .pointer("/result/result/data/artifact")
            .ok_or_else(|| {
                backend("Hybrid middle contribution lacks its original native Manim artifact")
            })?;
        let manim_path = text_at(original, "/relative_path")?;
        let manim_descriptor = json!({"root":"output","path":manim_path,
            "sha256":original.get("sha256"),"bytes":original.get("bytes")});
        let manim_media = artifact(output, &manim_descriptor, &manim_path, MEDIA_MAX_BYTES)?;
        let report = original.get("decoded_video").ok_or_else(|| {
            backend("Hybrid middle contribution lacks its original decoded-video report")
        })?;
        let report_path = text_at(report, "/relative_path")?;
        require(
            report_path == format!("{manim_path}.decoded-video.json"),
            "Hybrid Manim report belongs to another artifact",
        )?;
        let raw = read_verified_production_artifact(
            output,
            &report_path,
            &text_at(report, "/sha256")?,
            REPORT_MAX_BYTES,
        )?;
        require(
            report.get("bytes").and_then(Value::as_u64) == Some(raw.len() as u64),
            "Hybrid Manim report byte count changed",
        )?;
        let observed: Value =
            serde_json::from_slice(&raw).map_err(|_| backend("Hybrid Manim report is not JSON"))?;
        require(
            observed.get("frame_count").and_then(Value::as_u64) == Some(CLIP_FRAMES)
                && observed
                    .get("frames")
                    .and_then(Value::as_array)
                    .is_some_and(|frames| frames.len() == CLIP_FRAMES as usize),
            "Hybrid middle contribution was not observed as sixty native frames",
        )?;
        for (index, segment) in sources.motion_canvas.segments.iter().enumerate() {
            let ordinal = index * 2;
            let manifest = text_at(&segment.artifact, "/manifest")?;
            let manifest_sha = text_at(&segment.artifact, "/manifest_sha256")?;
            // Read the pinned manifest immediately before the real encoder.
            let _ = read_verified_production_artifact(
                output,
                &manifest,
                &manifest_sha,
                8 * 1024 * 1024,
            )?;
            let path = format!("{namespace}-mc-{ordinal}.mkv");
            let original = scoped.execute(id, expected, &format!("{request}:ffv1:{ordinal}"),
                "driver.mlt-video.frames.encode", json!({"root":"output","manifest_path":manifest,
                    "expected_manifest_sha256":manifest_sha,"output_path":path,"max_bytes":MEDIA_MAX_BYTES}), true).await?;
            let actual = verify_ffv1(output, response_data(&original)?, &path)?;
            originals.push(original);
            clips.push(actual);
        }
        clips.insert(1, manim_media);

        let provider_profile = crate::mlt_edit_plan::MltOwnedVideoProfile {
            width: 1920,
            height: 1080,
            fps_num: 30,
            fps_den: 1,
            progressive: true,
            sample_aspect_num: 1,
            sample_aspect_den: 1,
            display_aspect_num: 16,
            display_aspect_den: 9,
            colorspace: 709,
            audio_channels: 2,
        };
        let create = scoped
            .execute(
                id,
                expected,
                &format!("{request}:mlt:project-create"),
                "driver.mlt-video.project.create",
                json!({"profile":provider_profile}),
                true,
            )
            .await?;
        let mut session = new_session(response_data(&create)?)?;
        originals.push(create);
        let (next, created_sequence) = edit(
            &scoped,
            id,
            expected,
            &format!("{request}:mlt:sequence-create"),
            "driver.mlt-video.sequence.create",
            session,
            json!({"name":SEQUENCE_NAME}),
        )
        .await?;
        let sequence = created(&created_sequence, "sequence")?;
        session = next;
        let (next, created_track) = edit(
            &scoped,
            id,
            expected,
            &format!("{request}:mlt:track-create"),
            "driver.mlt-video.track.create",
            session,
            json!({"sequence":sequence,"name":TRACK_NAME,"kind":"video"}),
        )
        .await?;
        let _ = created(&created_track, "track")?;
        session = next;
        let mut edit_receipts = vec![created_sequence, created_track];
        for (index, clip) in clips.iter().enumerate() {
            // Rehash every actual contribution before import; a provider ref
            // from an earlier native revision is never carried across edits.
            artifact(output, &clip.descriptor(), &clip.path, MEDIA_MAX_BYTES)?;
            let (next, imported) = edit(&scoped, id, expected, &format!("{request}:mlt:asset:{index}"),
                "driver.mlt-video.asset.import", session, json!({"kind":"file",
                    "name":format!("Original hybrid source {index}"),"root":"output","path":clip.path})).await?;
            let asset = created(&imported, "asset")?;
            session = next;
            let sequence = reference(
                &scoped,
                id,
                expected,
                &format!("{request}:mlt:sequence-ref:{index}"),
                &session,
                None,
                SEQUENCE_NAME,
            )
            .await?;
            let track = reference(
                &scoped,
                id,
                expected,
                &format!("{request}:mlt:track-ref:{index}"),
                &session,
                Some(&sequence),
                TRACK_NAME,
            )
            .await?;
            let (next, inserted) = edit(
                &scoped,
                id,
                expected,
                &format!("{request}:mlt:clip:{index}"),
                "driver.mlt-video.clip.insert",
                session,
                json!({"sequence":sequence,"track":track,"asset":asset,
                    "start":index as u64 * CLIP_FRAMES,"source_in":0,"source_out":CLIP_FRAMES,
                    "name":format!("Original hybrid cut {index}"),"ripple":false}),
            )
            .await?;
            let _ = created(&inserted, "clip")?;
            require(
                inserted.get("after_frames").and_then(Value::as_u64)
                    == Some((index as u64 + 1) * CLIP_FRAMES),
                "Hybrid MLT edit changed an original cut boundary",
            )?;
            edit_receipts.push(imported);
            edit_receipts.push(inserted);
            session = next;
        }
        let sequence = reference(
            &scoped,
            id,
            expected,
            &format!("{request}:mlt:final-sequence-ref"),
            &session,
            None,
            SEQUENCE_NAME,
        )
        .await?;
        let duration = scoped
            .execute(
                id,
                expected,
                &format!("{request}:mlt:duration"),
                "driver.mlt-video.sequence.duration",
                json!({"project":session.project,"sequence":sequence}),
                false,
            )
            .await?;
        let duration_data = response_data(&duration)?;
        require(
            duration_data.get("frames").and_then(Value::as_u64) == Some(FRAME_COUNT)
                && duration_data.get("fps_num").and_then(Value::as_u64) == Some(30)
                && duration_data.get("fps_den").and_then(Value::as_u64) == Some(1),
            "Hybrid MLT semantic timeline differs from the original 180-frame clock",
        )?;
        originals.push(duration);
        let visual_path = format!("{namespace}-visual.mkv");
        let args = json!({"project":session.project,"sequence":sequence,"profile":"lossless-video-only","output":visual_path});
        let planned = scoped
            .execute(
                id,
                expected,
                &format!("{request}:mlt:render-plan"),
                "driver.mlt-video.render.plan",
                args.clone(),
                false,
            )
            .await?;
        let plan = response_data(&planned)?;
        require(
            plan.get("runnable").and_then(Value::as_bool) == Some(true)
                && plan.get("frames").and_then(Value::as_u64) == Some(FRAME_COUNT)
                && plan.get("project_revision").and_then(Value::as_str)
                    == Some(session.revision.as_str())
                && plan.get("output_root").and_then(Value::as_str) == Some("output")
                && plan.get("output_path").and_then(Value::as_str) == Some(visual_path.as_str())
                && plan
                    .get("missing_services")
                    .and_then(Value::as_array)
                    .is_some_and(|missing| missing.is_empty()),
            "Hybrid MLT plan did not confirm its exact native source and output",
        )?;
        originals.push(planned);
        let mut start_args = args;
        start_args["expected_revision"] = session.revision.clone().into();
        let started = scoped
            .execute(
                id,
                expected,
                &format!("{request}:mlt:render-start"),
                "driver.mlt-video.render.start",
                start_args,
                true,
            )
            .await?;
        let started_data = response_data(&started).map_err(unconfirmed_render)?;
        let job = opaque(started_data, "job").map_err(unconfirmed_render)?;
        let mut terminal = match started_data.get("state").and_then(Value::as_str) {
            Some("succeeded") => true,
            Some("queued" | "starting" | "running") => false,
            Some("failed") => {
                return Err(backend(
                    "Original hybrid native MLT start reported a terminal failure",
                ));
            }
            Some("cancelled") => {
                return Err(Error::new(
                    ErrorCode::Cancelled,
                    "Original hybrid native MLT start reported cancellation",
                ));
            }
            _ => {
                return Err(unconfirmed_render(backend(
                    "Original hybrid native MLT start has an unconfirmed state",
                )));
            }
        };
        originals.push(started);
        let deadline = Instant::now() + Duration::from_secs(330);
        let mut polls = 0;
        while !terminal {
            if Instant::now() >= deadline || polls >= 330 {
                return Err(unconfirmed_render(Error::new(
                    ErrorCode::Timeout,
                    "Original hybrid MLT render has no confirmed terminal within its bounded observation window",
                )));
            }
            sleep(Duration::from_secs(1)).await;
            polls += 1;
            let status = scoped
                .execute(
                    id,
                    expected,
                    &format!("{request}:mlt:status:{polls}"),
                    "driver.mlt-video.render.status",
                    json!({"job":job}),
                    false,
                )
                .await
                .map_err(unconfirmed_render)?;
            let data = response_data(&status).map_err(unconfirmed_render)?;
            require(
                data.get("job").and_then(Value::as_str) == Some(job.as_str()),
                "Hybrid native status identifies another render job",
            )
            .map_err(unconfirmed_render)?;
            match data.get("state").and_then(Value::as_str) {
                Some("succeeded") => terminal = true,
                Some("queued" | "starting" | "running") => {}
                Some("failed") => {
                    return Err(backend(
                        "Original hybrid native MLT render reported failure",
                    ));
                }
                Some("cancelled") => {
                    return Err(Error::new(
                        ErrorCode::Cancelled,
                        "Original hybrid native MLT render was cancelled",
                    ));
                }
                _ => {
                    return Err(unconfirmed_render(backend(
                        "Original hybrid native MLT render has an unknown or malformed state",
                    )));
                }
            }
        }
        let original_result = scoped
            .execute(
                id,
                expected,
                &format!("{request}:mlt:render-result"),
                "driver.mlt-video.render.result",
                json!({"job":job}),
                false,
            )
            .await
            .map_err(unconfirmed_render)?;
        let visual = verify_lossless(
            output,
            response_data(&original_result).map_err(unconfirmed_render)?,
            &visual_path,
            &job,
            &session.revision,
        )
        .map_err(unconfirmed_render)?;
        // The producer only reads native observations. Missing originals fail;
        // no fallback decoder, rerender or report conversion is performed.
        let color = complete_video_observation(
            output,
            &visual,
            response_data(&original_result)?,
            ObservationProfile::MltVisualFfv1,
            &clips,
        )?;
        require(
            scoped.service.project(id).map_err(storage_error)? == sources.project,
            "Hybrid editable source changed during native assembly",
        )?;
        let evidence = json!({"schema":"motionwright.native.hybrid-timeline/1",
            "project_sha256":sources.project_sha256,"project_resource":sources.project.resource_key(),
            "generation":expected.generation,"revision":expected.revision,
            "frame_count":FRAME_COUNT,"source_segment_frames":[60,60,60],"cut_frames":[60,120],
            "native_render_profile":"lossless-video-only","transport_pcm_audio":false,
            "native_frame_count_observed":FRAME_COUNT,"artifact_path":visual.path,
            "artifact_sha256":visual.sha256,"artifact_bytes":visual.bytes,
            "native_job_ref":job,"provider_revision":session.revision,
            "native_operation_returns":originals,"native_edit_returns":edit_receipts,
            "original_native_render_result":original_result,
            "contributions":clips.iter().map(Artifact::descriptor).collect::<Vec<_>>(),
            "complete_video_observation":color,
            "provider_project_cleanup":"not_requested_requires_foreground_broker_consent",
            "automatic_retry":false,"physical_acceptance":false,"human_approval":false});
        Ok(HybridNativeTimeline {
            project_sha256: sources.project_sha256.clone(),
            connection_identity: scoped.client.connection().identity()?,
            source_evidence_sha256: source_evidence_sha256(sources)?,
            evidence,
        })
    }

    /// Mux only the sealed timeline and its unchanged saved original WAV.
    pub async fn assemble_native_hybrid_master(
        &self,
        sources: &HybridNativeSources,
        timeline: &HybridNativeTimeline,
        audio: &MltAudioArtifact,
    ) -> NativeResult<HybridNativeMaster> {
        let scoped = self.hybrid_mlt_scope(sources)?;
        let request = &sources.request_prefix;
        let output = &scoped.client.connection().output_root;
        require(
            timeline.project_sha256 == sources.project_sha256
                && timeline.connection_identity == scoped.client.connection().identity()?
                && timeline.source_evidence_sha256 == source_evidence_sha256(sources)?,
            "Hybrid timeline belongs to another original source or owner connection",
        )?;
        let visual_path = format!("{}-visual.mkv", sources.namespace);
        let visual_row = json!({"root":"output","path":timeline.evidence.get("artifact_path"),
            "sha256":timeline.evidence.get("artifact_sha256"),"bytes":timeline.evidence.get("artifact_bytes")});
        let visual = artifact(output, &visual_row, &visual_path, MEDIA_MAX_BYTES)?;
        let profile = sources
            .project
            .deliverables
            .iter()
            .find(|profile| profile.id == sources.deliverable_id)
            .ok_or_else(|| invalid("Hybrid original master profile is absent"))?;
        let voice_id = profile
            .voice_track_id
            .ok_or_else(|| invalid("Hybrid original source voice is absent"))?;
        let verified = scoped
            .service
            .verified_master_voice(sources.project.id, &sources.expected, voice_id)
            .map_err(storage_error)?;
        require(
            audio.relative_path == format!("{}-source.wav", sources.namespace)
                && audio.sha256 == verified.sha256
                && audio.sample_rate == 48_000
                && audio.channels == 2
                && verified.size_bytes == WAV_BYTES,
            "Hybrid mux selected a different source audio artifact",
        )?;
        let wave = read_verified_production_artifact(
            output,
            &audio.relative_path,
            &audio.sha256,
            WAV_BYTES,
        )?;
        require(
            wave.len() as u64 == WAV_BYTES,
            "Hybrid staged original source WAV has a changed byte count",
        )?;
        let source_parent = verified
            .path
            .parent()
            .ok_or_else(|| backend("Original hybrid CAS source parent is absent"))?;
        let source_name = verified
            .path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| backend("Original hybrid CAS source member is invalid"))?;
        let original_wave = read_verified_production_artifact(
            source_parent,
            source_name,
            &verified.sha256,
            WAV_BYTES,
        )?;
        require(
            original_wave == wave,
            "Hybrid staged WAV differs from the original saved source bytes",
        )?;
        let master_path = format!("{}-master.mp4", sources.namespace);
        let original = scoped
            .execute(
                sources.project.id,
                &sources.expected,
                &format!("{request}:mlt:mux"),
                "driver.mlt-video.av.mux",
                json!({"video_root":"output","video_path":visual.path,"video_sha256":visual.sha256,
                "audio_root":"output","audio_path":audio.relative_path,"audio_sha256":audio.sha256,
                "width":1920,"height":1080,"fps_num":30,"fps_den":1,"frame_count":FRAME_COUNT,
                "sample_rate":48_000,"channels":2,"profile":"h264-aac-mp4",
                "output_path":master_path,"max_bytes":MASTER_MAX_BYTES}),
                true,
            )
            .await?;
        let master = response_data(&original)?;
        verify_exact_native_mux(
            master,
            &MuxExpected {
                frames: FRAME_COUNT,
                width: 1920,
                height: 1080,
                fps_num: 30,
                fps_den: 1,
                source_video: &visual.sha256,
                source_audio: &audio.sha256,
                output_path: &master_path,
            },
        )?;
        let native_master = artifact(
            output,
            master
                .get("artifact")
                .ok_or_else(|| backend("Hybrid native master is absent"))?,
            &master_path,
            MASTER_MAX_BYTES,
        )?;
        // Existing validator rehashes both real mux products. A tighter local
        // decoded-WAV bound precedes it, so malformed observations stay finite.
        let decoded = master
            .get("decoded_audio")
            .ok_or_else(|| backend("Hybrid native decoded audio is absent"))?;
        let decoded_path = text_at(decoded, "/path")?;
        artifact(output, decoded, &decoded_path, 2 * 1024 * 1024)?;
        verify_mux_artifact_file(output, master.get("artifact").unwrap())?;
        verify_mux_artifact_file(output, decoded)?;
        let original_inputs = [
            visual.clone(),
            Artifact {
                path: audio.relative_path.clone(),
                sha256: audio.sha256.clone(),
                bytes: WAV_BYTES,
            },
        ];
        let color = complete_video_observation(
            output,
            &native_master,
            master,
            ObservationProfile::MltH264AacMaster,
            &original_inputs,
        )?;
        artifact(output, &visual.descriptor(), &visual.path, MEDIA_MAX_BYTES)?;
        require(
            read_verified_production_artifact(
                output,
                &audio.relative_path,
                &audio.sha256,
                WAV_BYTES,
            )? == wave,
            "Original hybrid source WAV changed during the native mux",
        )?;
        require(
            scoped
                .service
                .project(sources.project.id)
                .map_err(storage_error)?
                == sources.project,
            "Hybrid editable source changed during native mastering",
        )?;
        Ok(HybridNativeMaster {
            evidence: json!({"schema":"motionwright.native.hybrid-master/1",
            "project_sha256":sources.project_sha256,"frame_count":FRAME_COUNT,
            "original_native_return":original,"master":master,"complete_video_observation":color,
            "original_audio_sha256":audio.sha256,"measured_original_audio_frames":288000,
            "automatic_retry":false,"packet_PTS_and_AAC_priming_verified":false,
            "physical_acceptance":false,"human_approval":false}),
        })
    }
}

fn unconfirmed_render(error: Error) -> Error {
    error
        .uncertain()
        .recipe_progress(1, "driver.mlt-video.render.start")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hybrid_native_staging_roster_rejects_changed_bytes_duplicates_and_substituted_sources() {
        let sources = [
            Artifact {
                path: "original-red.mkv".into(),
                sha256: "1".repeat(64),
                bytes: 101,
            },
            Artifact {
                path: "original-green.mp4".into(),
                sha256: "2".repeat(64),
                bytes: 102,
            },
            Artifact {
                path: "original-blue.mkv".into(),
                sha256: "3".repeat(64),
                bytes: 103,
            },
        ];
        // Native asset-ID ordering need not equal authored timeline ordering.
        let original = json!([
            sources[2].descriptor(),
            sources[0].descriptor(),
            sources[1].descriptor()
        ]);
        assert!(exact_source_roster(&original, &sources).is_ok());
        let mut changed = original.clone();
        changed[1]["sha256"] = json!("4".repeat(64));
        assert!(exact_source_roster(&changed, &sources).is_err());
        changed = original.clone();
        changed[1]["bytes"] = json!(999);
        assert!(exact_source_roster(&changed, &sources).is_err());
        changed = original.clone();
        changed[1] = sources[2].descriptor();
        assert!(exact_source_roster(&changed, &sources).is_err());
        changed = original.clone();
        changed[1]["path"] = json!("substitute-same-sha.mkv");
        assert!(exact_source_roster(&changed, &sources).is_err());
        assert!(
            exact_source_roster(
                &json!([sources[0].descriptor(), sources[1].descriptor()]),
                &sources
            )
            .is_err()
        );
        assert_eq!(original[0], sources[2].descriptor());
    }
}
