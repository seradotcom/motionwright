//! The closed original mixed-renderer MLT continuation.
//!
//! Only sealed HybridNativeSources from the real MC/Manim producer enter this
//! module. The existing all-MotionCanvas preflight remains unchanged. All
//! effects use the retained coordinator, its original SQLite root, and the
//! pinned Driver Host; neither an arbitrary scoped client nor native refs are
//! exposed to the caller.
use super::hybrid_budget::HybridNativeSources;
use super::mlt_multisegment_av::{
    MuxExpected, require_native_mux_stereo_tail, verify_exact_native_mux,
};
use super::*;
use semwright_audio_domain::wav::WaveReader;
use semwright_video_domain::native_observation::{
    CompleteObservation, ObservationProfile, PNG_LIMIT, REPORT_LIMIT, parse_complete_observation,
};
use std::io::Cursor;
use std::ops::Range;

pub(super) const MEDIA_MAX_BYTES: u64 = 256_000_000;
pub(super) const MASTER_MAX_BYTES: u64 = 32 * 1024 * 1024;
const REPORT_MAX_BYTES: u64 = REPORT_LIMIT as u64;
const PNG_MAX_BYTES: u64 = PNG_LIMIT as u64;
const NATIVE_INPUT_MAX_BYTES: u64 = 8 * 1024 * 1024;
const DECODED_WAVE_MAX_BYTES: u64 = 2 * 1024 * 1024;
const SEQUENCE_NAME: &str = "Original Motionwright MC-Manim-MC cut";
const TRACK_NAME: &str = "Original native hybrid contributions";
const MLT_XML_SOURCE_KIND: &str = "mlt_render_project_bytes";
const DIRECT_AV_SOURCE_KIND: &str = "verified_ffv1_video_source_bytes";

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
    master_sha256: String,
}
impl HybridNativeMaster {
    pub fn evidence(&self) -> &Value {
        &self.evidence
    }

    /// Identity of the already verified original master, never a read grant.
    pub fn sha256(&self) -> &str {
        &self.master_sha256
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
    artifact_and_bytes(root, value, expected_path, maximum).map(|(artifact, _)| artifact)
}

fn artifact_and_bytes(
    root: &Path,
    value: &Value,
    expected_path: &str,
    maximum: u64,
) -> NativeResult<(Artifact, Vec<u8>)> {
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
    Ok((
        Artifact {
            path: expected_path.into(),
            sha256: digest,
            bytes: count,
        },
        raw,
    ))
}
fn verify_ffv1(
    root: &Path,
    value: &Value,
    expected_path: &str,
    expected_frames: u64,
) -> NativeResult<Artifact> {
    require(
        value.get("codec").and_then(Value::as_str) == Some("ffv1")
            && value.get("container").and_then(Value::as_str) == Some("matroska")
            && value.get("frame_count").and_then(Value::as_u64) == Some(expected_frames)
            && value.get("width").and_then(Value::as_u64) == Some(1920)
            && value.get("height").and_then(Value::as_u64) == Some(1080)
            && value.get("fps_num").and_then(Value::as_u64) == Some(30)
            && value.get("fps_den").and_then(Value::as_u64) == Some(1)
            && value.pointer("/media/video").and_then(Value::as_bool) == Some(true)
            && value.pointer("/media/audio").and_then(Value::as_bool) == Some(false),
        "Hybrid original MC encode differs from the planned audio-free FFV1 contribution",
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
    frame_count: u64,
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
            && value.pointer("/media/frames").and_then(Value::as_u64) == Some(frame_count)
            && value
                .pointer("/media/codecs")
                .and_then(Value::as_array)
                .is_some_and(|codecs| codecs.len() == 1 && codecs[0].as_str() == Some("ffv1")),
        "Hybrid visual result differs from the authored FFV1 video-only clock",
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
        (u128::from(numerator) * 30).abs_diff(u128::from(frame_count) * u128::from(denominator))
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
fn mlt_property<'a>(node: roxmltree::Node<'a, '_>, name: &str) -> Option<&'a str> {
    let mut properties = node
        .children()
        .filter(|child| child.has_tag_name("property") && child.attribute("name") == Some(name));
    let property = properties.next()?;
    if properties.next().is_some() || property.children().any(|child| child.is_element()) {
        return None;
    }
    property.text()
}

fn only_mlt_track<'a>(
    tractor: roxmltree::Node<'a, '_>,
    expected_hide: Option<&str>,
) -> Option<&'a str> {
    let mut children = tractor
        .children()
        .filter(|node| node.is_element() && !node.has_tag_name("property"));
    let track = children.next()?;
    if children.next().is_some()
        || !track.has_tag_name("track")
        || track.attribute("hide") != expected_hide
        || track.attributes().len() != 1 + usize::from(expected_hide.is_some())
        || track.children().any(|child| child.is_element())
    {
        return None;
    }
    track.attribute("producer")
}

/// Bind the pinned host's staged normal form to the authored order and lengths.
/// Its closed source roster can be identical for two different cuts.
fn verify_authored_cut_input(
    raw: &[u8],
    sources: &[Artifact],
    ranges: &[Range<u64>],
    native_sources: &Value,
) -> NativeResult<()> {
    const MISMATCH: &str = "Original hybrid MLT input differs from the authored cuts";
    let bad_input = || backend(MISMATCH);
    let xml = std::str::from_utf8(raw).map_err(|_| bad_input())?;
    let options = roxmltree::ParsingOptions {
        nodes_limit: 1024,
        ..Default::default()
    };
    let document =
        roxmltree::Document::parse_with_options(xml, options).map_err(|_| bad_input())?;
    let root = document.root_element();
    require(
        sources.len() == ranges.len()
            && root.has_tag_name("mlt")
            && root.attribute("semwright_normal_form") == Some("1")
            && root.attribute("LC_NUMERIC") == Some("C")
            && root.attributes().len() == 2,
        MISMATCH,
    )?;
    let mut children = root.children().filter(|node| node.is_element());
    let profile = children.next().ok_or_else(bad_input)?;
    require(
        profile.has_tag_name("profile")
            && profile.attribute("width") == Some("1920")
            && profile.attribute("height") == Some("1080")
            && profile.attribute("frame_rate_num") == Some("30")
            && profile.attribute("frame_rate_den") == Some("1"),
        MISMATCH,
    )?;
    let mut nodes = std::collections::BTreeMap::new();
    let mut sequence = None;
    for node in children {
        let id = node.attribute("id").ok_or_else(bad_input)?;
        require(!id.is_empty() && nodes.insert(id, node).is_none(), MISMATCH)?;
        if node.has_tag_name("tractor") && mlt_property(node, "semwright:sequence") == Some("1") {
            require(sequence.replace(node).is_none(), MISMATCH)?;
        }
    }
    let sequence = sequence.ok_or_else(bad_input)?;
    let frame_end = ranges.last().and_then(|range| range.end.checked_sub(1));
    require(
        frame_end.is_some()
            && nodes.len() == sources.len() * 2 + 3
            && sequence.attributes().len() == 2
            && sequence
                .attribute("out")
                .and_then(|value| value.parse::<u64>().ok())
                == frame_end,
        MISMATCH,
    )?;
    let linked = |id| nodes.get(id).copied().ok_or_else(bad_input);
    let track = linked(only_mlt_track(sequence, None).ok_or_else(bad_input)?)?;
    require(
        track.has_tag_name("tractor") && track.attributes().len() == 1,
        MISMATCH,
    )?;
    let playlist = linked(only_mlt_track(track, Some("audio")).ok_or_else(bad_input)?)?;
    require(
        playlist.has_tag_name("playlist") && playlist.attributes().len() == 1,
        MISMATCH,
    )?;

    let mut entries = playlist.children().filter(|node| node.is_element());
    let mut cursor = 0;
    let mut staged = std::collections::BTreeMap::new();
    for (source, range) in sources.iter().zip(ranges) {
        let entry = entries.next().ok_or_else(bad_input)?;
        let inclusive_end = range
            .end
            .checked_sub(range.start)
            .and_then(|duration| duration.checked_sub(1))
            .ok_or_else(bad_input)?;
        require(
            range.start == cursor
                && entry.has_tag_name("entry")
                && entry.attributes().len() == 3
                && entry.attribute("in") == Some("0")
                && entry
                    .attribute("out")
                    .and_then(|value| value.parse::<u64>().ok())
                    == Some(inclusive_end)
                && !entry.children().any(|node| node.is_element()),
            MISMATCH,
        )?;
        cursor = range.end;
        let clip_id = entry.attribute("producer").ok_or_else(bad_input)?;
        let clip = linked(clip_id)?;
        let asset_id = mlt_property(clip, "semwright:clip_asset").ok_or_else(bad_input)?;
        let asset = linked(asset_id)?;
        require(
            clip_id.starts_with("source_")
                && clip != asset
                && staged.insert(asset_id, source.descriptor()).is_none(),
            MISMATCH,
        )?;
        let staged_sha = hex::encode(Sha256::digest(asset_id.as_bytes()));
        let staged_name = format!("asset-{}.bin", &staged_sha[..24]);
        for producer in [clip, asset] {
            require(
                (producer.has_tag_name("chain") || producer.has_tag_name("producer"))
                    && producer
                        .children()
                        .filter(|child| child.is_element())
                        .all(|child| child.has_tag_name("property"))
                    && mlt_property(producer, "resource") == Some(staged_name.as_str())
                    && mlt_property(producer, "semwright:resource_root") == Some("output")
                    && mlt_property(producer, "semwright:resource_path")
                        == Some(source.path.as_str()),
                MISMATCH,
            )?;
        }
    }
    // The host stages assets in ascending ID order. This binds each staged
    // filename to the original byte descriptor, even when metadata is swapped.
    let staged: Vec<_> = staged.into_values().collect();
    require(
        entries.next().is_none() && native_sources.as_array() == Some(&staged),
        MISMATCH,
    )
}

fn read_bound_video_observation<'a>(
    root: &Path,
    media: &Value,
    original_result: &'a Value,
    expected_profile: ObservationProfile,
    frame_count: u64,
) -> NativeResult<(&'a Value, CompleteObservation)> {
    let media_path = text_at(media, "/path")?;
    let original_receipt = original_result.get("color_observation").ok_or_else(|| {
        backend("Original hybrid native return omitted its color observation descriptor")
    })?;
    let path = format!("{}.color-observation.json", media_path);
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
    let document = parsed.document();
    require(
        document.get("artifact") == Some(media)
            && document.get("frame_count").and_then(Value::as_u64) == Some(frame_count)
            && document.get("canonical_fps_num").and_then(Value::as_u64) == Some(30)
            && document.get("canonical_fps_den").and_then(Value::as_u64) == Some(1)
            && document.pointer("/stream/width").and_then(Value::as_u64) == Some(1920)
            && document.pointer("/stream/height").and_then(Value::as_u64) == Some(1080)
            && document.get("frame_path_prefix").and_then(Value::as_str)
                == Some(format!("{}.", media_path).as_str())
            && document
                .get("exact_clock")
                .and_then(Value::as_bool)
                .is_some(),
        "Original hybrid native color observation has a different artifact or frame clock",
    )?;
    Ok((original_receipt, parsed))
}

fn verify_original_observation_source(
    document: &Value,
    profile: ObservationProfile,
    editable_xml: &Artifact,
    authored_sources: &[Artifact],
) -> NativeResult<()> {
    // Render.start executes the staged MLT document. The closed AV mux
    // retains that XML beside its actual FFV1/WAV inputs for inspection.
    let (kind, source) = match (profile, authored_sources) {
        (ObservationProfile::MltH264AacMaster, [video, _audio]) => (DIRECT_AV_SOURCE_KIND, video),
        (ObservationProfile::MltVisualFfv1, _) => (MLT_XML_SOURCE_KIND, editable_xml),
        _ => {
            return Err(backend(
                "Original hybrid observation source profile is invalid",
            ));
        }
    };
    let binding = json!({"source_kind":kind,"root":"output",
        "path":source.path,"sha256":source.sha256,"bytes":source.bytes,"source_first_frame":0});
    require(
        document.get("source_binding") == Some(&binding),
        "Original hybrid native report refers to different consumed media or editable input",
    )
}

fn complete_video_observation(
    root: &Path,
    media: &Artifact,
    original_result: &Value,
    expected_profile: ObservationProfile,
    sources: &[Artifact],
    frame_count: u64,
    authored_cuts: Option<&[Range<u64>]>,
) -> NativeResult<Value> {
    let (original_receipt, parsed) = read_bound_video_observation(
        root,
        &media.descriptor(),
        original_result,
        expected_profile,
        frame_count,
    )?;
    let document = parsed.document();
    // Host render.start stages imported paths. Bind that actual staging to the
    // previously admitted originals; matching output/report hashes alone would
    // permit a changed input at the same path to qualify as the original cut.
    let native_input = original_result
        .get("native_input")
        .ok_or_else(|| backend("Original hybrid native return omitted its editable MLT input"))?;
    let (xml, xml_bytes) = artifact_and_bytes(
        root,
        native_input,
        &format!("{}.native-input.mlt", media.path),
        NATIVE_INPUT_MAX_BYTES,
    )?;
    verify_original_observation_source(document, expected_profile, &xml, sources)?;
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
    if let Some(ranges) = authored_cuts {
        verify_authored_cut_input(&xml_bytes, sources, ranges, native_sources)?;
    }
    for source in sources {
        artifact(root, &source.descriptor(), &source.path, MEDIA_MAX_BYTES)?;
    }
    let frames = document
        .get("frames")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Original hybrid native color observation has no frame roster"))?;
    require(
        frames.len() == frame_count as usize,
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

/// These clocks are measured by the original Host from the final MP4. The
/// emitted WAV can contain enough decoded samples when the MP4 edit is short.
#[derive(serde::Serialize)]
struct HybridMasterStreamClock {
    exact_video_clock: bool,
    audio_start_sample: Option<i64>,
    presented_audio_samples: Option<u64>,
    authored_audio_samples: u64,
}

impl HybridMasterStreamClock {
    fn exact_audio_presentation(&self) -> bool {
        self.audio_start_sample == Some(0)
            && self.presented_audio_samples == Some(self.authored_audio_samples)
    }
}

fn observed_hybrid_master_stream_clock(
    document: &Value,
    authored_audio_samples: u64,
) -> HybridMasterStreamClock {
    // The pinned complete-observation parser binds AAC to a 1/48000 time
    // base. Use its original stream interval, rather than decoded PCM size.
    HybridMasterStreamClock {
        exact_video_clock: document.get("exact_clock").and_then(Value::as_bool) == Some(true),
        audio_start_sample: document
            .pointer("/audio_stream/start_pts")
            .and_then(Value::as_i64),
        presented_audio_samples: document
            .pointer("/audio_stream/duration_ts")
            .and_then(Value::as_u64),
        authored_audio_samples,
    }
}

/// Delivery of a persisted hybrid master reopens its original bounded report
/// even after the renderer and original Studio process have closed.
pub(super) fn verify_saved_hybrid_master_stream_clock(
    root: &Path,
    original_result: &Value,
    original_video_path: &str,
    authored_video_frames: u64,
    authored_audio_samples: u64,
) -> NativeResult<()> {
    let media = original_result
        .get("artifact")
        .ok_or_else(|| backend("Saved hybrid master has no MP4 identity"))?;
    let (_, parsed) = read_bound_video_observation(
        root,
        media,
        original_result,
        ObservationProfile::MltH264AacMaster,
        authored_video_frames,
    )?;
    let document = parsed.document();
    let binding = &document["source_binding"];
    let consumed = match binding["source_kind"].as_str() {
        Some(DIRECT_AV_SOURCE_KIND) => {
            binding["path"].as_str() == Some(original_video_path)
                && binding.get("sha256") == original_result.get("video_sha256")
        }
        // Older completed provider renders actually executed this XML.
        Some(MLT_XML_SOURCE_KIND) => original_result.get("native_input").is_some_and(|xml| {
            ["root", "path", "bytes", "sha256"]
                .iter()
                .all(|key| binding.get(key) == xml.get(key))
        }),
        _ => false,
    };
    require(
        consumed
            && document.get("native_input_sources") == original_result.get("native_input_sources"),
        "Saved hybrid clock report does not identify its original input and media roster",
    )?;
    let clock = observed_hybrid_master_stream_clock(document, authored_audio_samples);
    require(
        authored_video_frames.checked_mul(1_600) == Some(authored_audio_samples)
            && clock.exact_video_clock
            && clock.exact_audio_presentation(),
        "Original hybrid MP4 stream presentation differs from the authored video or audio clock",
    )
}

/// Read the pinned Host's decoded WAV and run the same terminal stereo check
/// used when qualifying the saved master for delivery.
fn verify_hybrid_mux_audio(
    output: &Path,
    master: &Value,
    decoded_path: &str,
    source_wave: &[u8],
    expected_frames: u64,
) -> NativeResult<()> {
    let decoded_receipt = master
        .get("decoded_audio")
        .ok_or_else(|| backend("Hybrid native decoded audio is absent"))?;
    let (_, decoded_bytes) = artifact_and_bytes(
        output,
        decoded_receipt,
        decoded_path,
        DECODED_WAVE_MAX_BYTES,
    )?;
    let source = WaveReader::open(Cursor::new(source_wave), source_wave.len() as u64)
        .map_err(|_| backend("Hybrid authored WAV could not be inspected"))?;
    let decoded = WaveReader::open(Cursor::new(decoded_bytes), DECODED_WAVE_MAX_BYTES)
        .map_err(|_| backend("Hybrid original decoded WAV could not be inspected"))?;
    require(
        master
            .get("decoded_audio_sample_frames")
            .and_then(Value::as_u64)
            == Some(decoded.info().frames),
        "Hybrid original decoded WAV sample count differs from the native return",
    )?;
    require_native_mux_stereo_tail(source, decoded, expected_frames)
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
    /// Assemble exactly the retained Motion Canvas and static Manim originals. Neither a JSON
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
        let ranges = sources.segment_ranges();
        let frame_count = sources.frame_count();
        let manim_ordinal = sources.manim_ordinal();
        let manim_range = &ranges[manim_ordinal];
        let manim_frames = manim_range.end - manim_range.start;
        // Preserve the original sequence name in the three-source route so the
        // same saved input never presents different arguments under an old ID.
        let sequence_name = if ranges.len() == 3 {
            SEQUENCE_NAME
        } else {
            "Original Motionwright two-source cut"
        };
        require(
            sources.motion_canvas.segments.len() + 1 == ranges.len(),
            "Hybrid source lost a native MC contribution",
        )?;
        let mut clips = Vec::with_capacity(ranges.len());
        let mut originals = Vec::new();
        // The Manim contribution is its original source-bound native MP4.
        let manim = &sources.manim;
        require(
            manim.get("renderer").and_then(Value::as_str) == Some("manim-community")
                && manim.get("project_revision").and_then(Value::as_u64) == Some(expected.revision)
                && manim.get("scene_id") == Some(&json!(sources.manim_scene_id()))
                && sources.retains_original_manim_source(),
            "Hybrid Manim contribution lacks its retained original source",
        )?;
        let original = manim
            .pointer("/result/result/data/artifact")
            .ok_or_else(|| {
                backend("Hybrid Manim contribution lacks its original native artifact")
            })?;
        let manim_path = text_at(original, "/relative_path")?;
        let manim_descriptor = json!({"root":"output","path":manim_path,
            "sha256":original.get("sha256"),"bytes":original.get("bytes")});
        let manim_media = artifact(output, &manim_descriptor, &manim_path, MEDIA_MAX_BYTES)?;
        let report = original.get("decoded_video").ok_or_else(|| {
            backend("Hybrid Manim contribution lacks its original decoded-video report")
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
            observed.get("frame_count").and_then(Value::as_u64) == Some(manim_frames)
                && observed
                    .get("frames")
                    .and_then(Value::as_array)
                    .is_some_and(|frames| frames.len() == manim_frames as usize),
            "Hybrid Manim contribution differs from its planned native frame count",
        )?;
        let motion_ordinals = (0..ranges.len()).filter(|&index| index != manim_ordinal);
        for (ordinal, segment) in motion_ordinals.zip(&sources.motion_canvas.segments) {
            let range = &ranges[ordinal];
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
            let actual = verify_ffv1(
                output,
                response_data(&original)?,
                &path,
                range.end - range.start,
            )?;
            originals.push(original);
            clips.push(actual);
        }
        clips.insert(manim_ordinal, manim_media);

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
            json!({"name":sequence_name}),
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
        for (index, (clip, range)) in clips.iter().zip(ranges).enumerate() {
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
                sequence_name,
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
                    "start":range.start,"source_in":0,"source_out":range.end-range.start,
                    "name":format!("Original hybrid cut {index}"),"ripple":false}),
            )
            .await?;
            let _ = created(&inserted, "clip")?;
            require(
                inserted.get("after_frames").and_then(Value::as_u64) == Some(range.end),
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
            sequence_name,
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
            duration_data.get("frames").and_then(Value::as_u64) == Some(frame_count)
                && duration_data.get("fps_num").and_then(Value::as_u64) == Some(30)
                && duration_data.get("fps_den").and_then(Value::as_u64) == Some(1),
            "Hybrid MLT semantic timeline differs from the original frame clock",
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
                && plan.get("frames").and_then(Value::as_u64) == Some(frame_count)
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
            frame_count,
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
            frame_count,
            Some(ranges),
        )?;
        require(
            scoped.service.project(id).map_err(storage_error)? == sources.project,
            "Hybrid editable source changed during native assembly",
        )?;
        let source_segment_frames: Vec<_> =
            ranges.iter().map(|range| range.end - range.start).collect();
        let cut_frames: Vec<_> = ranges[..ranges.len() - 1]
            .iter()
            .map(|range| range.end)
            .collect();
        let evidence = json!({"schema":"motionwright.native.hybrid-timeline/1",
            "project_sha256":sources.project_sha256,"project_resource":sources.project.resource_key(),
            "generation":expected.generation,"revision":expected.revision,
            "frame_count":frame_count,"source_segment_frames":source_segment_frames,"cut_frames":cut_frames,
            "native_render_profile":"lossless-video-only","transport_pcm_audio":false,
            "native_frame_count_observed":frame_count,"native_cut_binding_verified":true,
            "artifact_path":visual.path,
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
        let output = &scoped.client.connection().output_root;
        let frame_count = sources.frame_count();
        let wav_bytes = sources.original_audio_bytes();
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
                && verified.size_bytes == wav_bytes,
            "Hybrid mux selected a different source audio artifact",
        )?;
        let wave = read_verified_production_artifact(
            output,
            &audio.relative_path,
            &audio.sha256,
            wav_bytes,
        )?;
        require(
            wave.len() as u64 == wav_bytes,
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
            wav_bytes,
        )?;
        require(
            original_wave == wave,
            "Hybrid staged WAV differs from the original saved source bytes",
        )?;
        let master_stem = format!("{}-master", sources.namespace);
        let master_path = format!("{master_stem}.mp4");
        let original = scoped
            .execute_hybrid_master_mux(
                sources,
                json!({"video_root":"output","video_path":visual.path,"video_sha256":visual.sha256,
                "audio_root":"output","audio_path":audio.relative_path,"audio_sha256":audio.sha256,
                "width":1920,"height":1080,"fps_num":30,"fps_den":1,"frame_count":frame_count,
                "sample_rate":48_000,"channels":2,"profile":"h264-aac-mp4",
                "output_path":master_path,"max_bytes":MASTER_MAX_BYTES}),
            )
            .await?;
        let master = response_data(&original)?;
        verify_exact_native_mux(
            master,
            &MuxExpected {
                frames: frame_count,
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
        let measured_source_frames = (wav_bytes - 44) / 4;
        verify_hybrid_mux_audio(
            output,
            master,
            &format!("{master_stem}.decoded.wav"),
            &wave,
            measured_source_frames,
        )?;
        let original_inputs = [
            visual.clone(),
            Artifact {
                path: audio.relative_path.clone(),
                sha256: audio.sha256.clone(),
                bytes: wav_bytes,
            },
        ];
        let color = complete_video_observation(
            output,
            &native_master,
            master,
            ObservationProfile::MltH264AacMaster,
            &original_inputs,
            frame_count,
            None,
        )?;
        artifact(output, &visual.descriptor(), &visual.path, MEDIA_MAX_BYTES)?;
        require(
            read_verified_production_artifact(
                output,
                &audio.relative_path,
                &audio.sha256,
                wav_bytes,
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
        let observed_stream_clock =
            observed_hybrid_master_stream_clock(&color["document"], measured_source_frames);
        let exact_audio_presentation = observed_stream_clock.exact_audio_presentation();
        let master_sha256 = text_at(master, "/artifact/sha256")?;
        Ok(HybridNativeMaster {
            master_sha256,
            evidence: json!({"schema":"motionwright.native.hybrid-master/1",
            "project_sha256":sources.project_sha256,"frame_count":frame_count,
            "original_native_return":original,"master":master,"complete_video_observation":color,
            "original_audio_sha256":audio.sha256,"measured_original_audio_frames":measured_source_frames,
            "decoded_wav_stereo_tail_verified":true,"observed_stream_clock":observed_stream_clock,
            "native_audio_presentation_matches_authored":exact_audio_presentation,
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
    fn hybrid_native_master_binds_consumed_ffv1_and_keeps_visual_xml_binding() {
        let xml = Artifact {
            path: "master.mp4.native-input.mlt".into(),
            sha256: "1".repeat(64),
            bytes: 10,
        };
        let sources = [
            Artifact {
                path: "visual.mkv".into(),
                sha256: "2".repeat(64),
                bytes: 100,
            },
            Artifact {
                path: "mix.wav".into(),
                sha256: "3".repeat(64),
                bytes: 1_000,
            },
        ];
        let document = |kind, source: &Artifact| {
            json!({"source_binding":{
                "source_kind":kind,"root":"output","path":source.path,"sha256":source.sha256,
                "bytes":source.bytes,"source_first_frame":0,
            }})
        };
        let observed_master = document(DIRECT_AV_SOURCE_KIND, &sources[0]);
        let observed_xml = document("mlt_render_project_bytes", &xml);
        let master = ObservationProfile::MltH264AacMaster;
        let visual = ObservationProfile::MltVisualFfv1;
        assert!(
            verify_original_observation_source(&observed_master, master, &xml, &sources).is_ok()
        );
        assert!(verify_original_observation_source(&observed_xml, visual, &xml, &sources).is_ok());
        assert!(verify_original_observation_source(&observed_xml, master, &xml, &sources).is_err());
        assert!(
            verify_original_observation_source(&observed_master, visual, &xml, &sources).is_err()
        );
        assert!(
            verify_original_observation_source(
                &document(DIRECT_AV_SOURCE_KIND, &sources[1]),
                master,
                &xml,
                &sources,
            )
            .is_err()
        );
    }

    #[test]
    fn hybrid_native_master_clock_checks_presentation_instead_of_decoded_sample_count() {
        let report = json!({"exact_clock":true,"audio_stream":{
            "start_pts":0,"duration_ts":49_600,"decoded_sample_count":50_176
        }});
        let exact = observed_hybrid_master_stream_clock(&report, 49_600);
        assert!(exact.exact_video_clock && exact.exact_audio_presentation());

        let mut shortened = report.clone();
        shortened["audio_stream"]["duration_ts"] = json!(49_584);
        let clock = observed_hybrid_master_stream_clock(&shortened, 49_600);
        assert!(!clock.exact_audio_presentation());
        assert_eq!(clock.presented_audio_samples, Some(49_584));

        let mut shifted = report.clone();
        shifted["audio_stream"]["start_pts"] = json!(16);
        assert!(!observed_hybrid_master_stream_clock(&shifted, 49_600).exact_audio_presentation());

        let mut unknown = report.clone();
        unknown["audio_stream"]["duration_ts"] = Value::Null;
        assert!(!observed_hybrid_master_stream_clock(&unknown, 49_600).exact_audio_presentation());

        let mut video = report;
        video["exact_clock"] = json!(false);
        let clock = observed_hybrid_master_stream_clock(&video, 49_600);
        assert!(!clock.exact_video_clock && clock.exact_audio_presentation());
    }

    #[test]
    fn hybrid_native_master_audio_binds_the_exact_decode_and_both_source_channels() {
        let directory = tempfile::tempdir().unwrap();
        let path = "authored-master.decoded.wav";
        let frames = 48_000_u64;
        let wave = |count: u64, lose_terminal_right: bool| {
            let mut bytes = b"RIFF".to_vec();
            bytes.extend_from_slice(&(36 + count as u32 * 4).to_le_bytes());
            bytes.extend_from_slice(b"WAVEfmt ");
            bytes.extend_from_slice(&16_u32.to_le_bytes());
            bytes.extend_from_slice(&1_u16.to_le_bytes());
            bytes.extend_from_slice(&2_u16.to_le_bytes());
            bytes.extend_from_slice(&48_000_u32.to_le_bytes());
            bytes.extend_from_slice(&192_000_u32.to_le_bytes());
            bytes.extend_from_slice(&4_u16.to_le_bytes());
            bytes.extend_from_slice(&16_u16.to_le_bytes());
            bytes.extend_from_slice(b"data");
            bytes.extend_from_slice(&(count as u32 * 4).to_le_bytes());
            for index in 0..count {
                bytes.extend_from_slice(&2500_i16.to_le_bytes());
                let right: i16 = if lose_terminal_right && index >= frames - 256 {
                    0
                } else {
                    -5000
                };
                bytes.extend_from_slice(&right.to_le_bytes());
            }
            bytes
        };
        let authored = wave(frames, false);

        let put_decode = |name: &str, bytes: Vec<u8>, count: u64| {
            fs::write(directory.path().join(name), &bytes).unwrap();
            json!({
                "decoded_audio": {
                    "root": "output", "path": name, "bytes": bytes.len(),
                    "sha256": hex::encode(Sha256::digest(&bytes))
                },
                "decoded_audio_sample_frames": count
            })
        };
        let verify = |result: &Value| {
            verify_hybrid_mux_audio(directory.path(), result, path, &authored, frames)
        };
        let exact = put_decode(path, wave(frames, false), frames);
        assert!(verify(&exact).is_ok());
        let padded = put_decode(path, wave(frames + 512, false), frames + 512);
        assert!(verify(&padded).is_ok());
        let wrong_receipt = put_decode(path, wave(frames + 512, false), frames);
        assert!(verify(&wrong_receipt).is_err());
        let shortened = put_decode(path, wave(frames - 1, false), frames - 1);
        assert!(verify(&shortened).is_err());
        let muted = put_decode(path, wave(frames, true), frames);
        assert!(
            verify(&muted)
                .unwrap_err()
                .to_string()
                .contains("lost authored stereo channel")
        );
        let unrelated = put_decode("another.decoded.wav", authored.clone(), frames);
        assert!(verify(&unrelated).is_err());
    }

    #[test]
    fn hybrid_native_input_binds_authored_order_and_inclusive_endpoints() {
        let sources = ["original-mc.mkv", "original-manim.mp4"].map(|path| Artifact {
            path: path.into(),
            sha256: "a".repeat(64),
            bytes: 100,
        });
        let chains: String = sources
            .iter()
            .enumerate()
            .map(|(index, source)| {
                let id = format!("asset{}", 1 - index);
                let staged = hex::encode(Sha256::digest(id.as_bytes()));
                let properties = format!(
                    r#"<property name="resource">asset-{}.bin</property><property name="semwright:resource_root">output</property><property name="semwright:resource_path">{}</property>"#,
                    &staged[..24], source.path
                );
                format!(
                    r#"<chain id="{id}">{properties}</chain><chain id="source_{index}">{properties}<property name="semwright:clip_asset">{id}</property></chain>"#
                )
            })
            .collect();
        let xml = format!(
            r#"<mlt LC_NUMERIC="C" semwright_normal_form="1"><profile width="1920" height="1080" frame_rate_num="30" frame_rate_den="1"/>{chains}
<playlist id="lane"><entry producer="source_0" in="0" out="17"/><entry producer="source_1" in="0" out="11"/></playlist>
<tractor id="track"><track producer="lane" hide="audio"/></tractor>
<tractor id="sequence" out="29"><property name="semwright:sequence">1</property><track producer="track"/></tractor></mlt>"#
        );
        let cuts = [0..18, 18..30];
        let native_order = json!([sources[1].descriptor(), sources[0].descriptor()]);
        assert!(verify_authored_cut_input(xml.as_bytes(), &sources, &cuts, &native_order).is_ok());
        let wrong_order = json!([sources[0].descriptor(), sources[1].descriptor()]);
        assert!(verify_authored_cut_input(xml.as_bytes(), &sources, &cuts, &wrong_order).is_err());
        for altered in [
            xml.replace(r#"entry producer="source_0""#, r#"entry producer="swap""#)
                .replace(
                    r#"entry producer="source_1""#,
                    r#"entry producer="source_0""#,
                )
                .replace(r#"entry producer="swap""#, r#"entry producer="source_1""#),
            xml.replace("out=\"17", "out=\"16")
                .replace("out=\"11", "out=\"12"),
            xml.replace("in=\"0\" out=\"17", "in=\"1\" out=\"18"),
            xml.replace(
                r#"<entry producer="source_1""#,
                r#"<blank length="1"/><entry producer="source_1""#,
            ),
            xml.replace("hide=\"audio\"", "hide=\"both\""),
            xml.replacen("asset-", "other-", 1),
        ] {
            assert!(
                verify_authored_cut_input(altered.as_bytes(), &sources, &cuts, &native_order)
                    .is_err()
            );
        }
    }

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
