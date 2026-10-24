//! Strict data validation for a complete native-video observation.
//! Parsing grants no execution authority. A separate original Host job and
//! application-owned receipt must bind these data to their actual source.
use crate::{ManimProfileError, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const SCHEMA: &str = "motionwright.native-video-observation/1";
pub const MAX_REPORT_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Color {
    matrix: Option<u8>,
    range: Option<u8>,
    primaries: Option<u8>,
    transfer: Option<u8>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Decoder {
    name: String,
    version: String,
    libraries: BTreeMap<String, Vec<u32>>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Presentation {
    scope: String,
    sample_aspect_num: Option<u32>,
    sample_aspect_den: Option<u32>,
    interlaced_frame: Option<bool>,
}
impl Presentation {
    fn valid(&self) -> bool {
        self.scope == "pyav-codec-context-per-frame-and-frame-interlace-v1"
            && match (self.sample_aspect_num, self.sample_aspect_den) {
                (None, None) => true,
                (Some(n), Some(d)) => {
                    n > 0 && d > 0 && n <= 100_000 && d <= 100_000 && gcd(n, d) == 1
                }
                _ => false,
            }
    }
    fn square_progressive(&self) -> bool {
        self.sample_aspect_num == Some(1)
            && self.sample_aspect_den == Some(1)
            && self.interlaced_frame == Some(false)
    }
}
/// Native C AVFrame fields read by the existing PyAV process through showinfo.
/// API color nulls stay in Wire::frame_color; this is a separate observation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeMetadata {
    scope: String,
    index: u64,
    pts: u64,
    time_base_num: u32,
    time_base_den: u32,
    duration: u64,
    color: Color,
    yuv_sha256: String,
}
impl NativeMetadata {
    fn valid_for(&self, frame: &Frame, api: &Color) -> bool {
        self.scope == "ffmpeg-showinfo-avframe-color-clock-yuv-passthrough-v1"
            && self.index == frame.index
            && self.pts <= i64::MAX as u64
            && self.duration <= i64::MAX as u64
            && (1..=1_000_000_000).contains(&self.time_base_num)
            && (1..=1_000_000_000).contains(&self.time_base_den)
            && gcd(self.time_base_num, self.time_base_den) == 1
            && sha(&self.yuv_sha256)
            && u128::from(self.pts) * u128::from(self.time_base_num) * u128::from(frame.pts_den)
                == u128::from(frame.pts_num) * u128::from(self.time_base_den)
            && [
                (api.matrix, self.color.matrix),
                (api.range, self.color.range),
                (api.primaries, self.color.primaries),
                (api.transfer, self.color.transfer),
            ]
            .iter()
            .all(|(api, native)| match (api, native) {
                (Some(a), Some(b)) => a == b,
                _ => true,
            })
    }
    fn complete_rec709(&self, range: Option<u8>, fps_num: u32, fps_den: u32) -> bool {
        self.color.matrix == Some(1)
            && self.color.range == range
            && matches!(range, Some(1 | 2))
            && self.color.primaries == Some(1)
            && self.color.transfer == Some(1)
            // This closed CFR MP4 admission compares the observed duration.
            // Zero/mismatched durations remain inspectable; none is filled from fps.
            && u128::from(self.duration) * u128::from(self.time_base_num)
                * u128::from(fps_num)
                == u128::from(fps_den) * u128::from(self.time_base_den)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Frame {
    index: u64,
    pts_num: u64,
    pts_den: u64,
    rgba_sha256: String,
    file: String,
    png_sha256: String,
    png_bytes: u64,
    /// Older data remain inspectable, but missing presentation is not admitted.
    #[serde(default)]
    presentation: Option<Presentation>,
    #[serde(default)]
    native_metadata: Option<NativeMetadata>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Wire {
    schema: String,
    artifact_sha256: String,
    artifact_bytes: u64,
    source_sha256: String,
    decoder: Decoder,
    video_streams: u32,
    audio_streams: u32,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
    codec: String,
    pixel_format: String,
    stream_color: Color,
    frame_color: Color,
    frame_count: u64,
    first_frame: u64,
    end_frame_exclusive: u64,
    rgba_bytes_per_frame: u64,
    retained_png_bytes: u64,
    rgba_sequence_sha256: String,
    alpha_min: u8,
    alpha_max: u8,
    all_pixels_opaque: bool,
    rgba_conversion: String,
    frames: Vec<Frame>,
}
/// Validated data with immutable fields; it is not a source-admission receipt.
#[derive(Debug, Clone)]
pub struct DecodedVideoObservation {
    value: Wire,
}
fn refuse(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}
fn sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}
fn name(value: &str, limit: usize) -> bool {
    !value.is_empty()
        && value.len() <= limit
        && value
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || b"._-".contains(&v))
}
fn gcd(mut a: u32, mut b: u32) -> u32 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}
/// Validate every indexed frame and exact rational PTS; container header counts
/// and requested durations cannot substitute for the complete decoded range.
pub fn parse_complete_observation(bytes: &[u8]) -> Result<DecodedVideoObservation> {
    if bytes.is_empty() || bytes.len() > MAX_REPORT_BYTES {
        return Err(refuse("native_video_observation_byte_budget"));
    }
    let v: Wire =
        serde_json::from_slice(bytes).map_err(|_| refuse("native_video_observation_schema"))?;
    if v.schema != SCHEMA
        || !sha(&v.artifact_sha256)
        || !(1..=512 * 1024 * 1024).contains(&v.artifact_bytes)
        || !sha(&v.source_sha256)
        || v.decoder.name != "PyAV"
        || !name(&v.decoder.version, 64)
        || v.decoder.libraries.is_empty()
        || v.decoder.libraries.len() > 16
        || v.decoder.libraries.iter().any(|(key, value)| {
            !name(key, 32) || value.len() != 3 || value.iter().any(|v| *v > 1_000_000)
        })
        || v.video_streams != 1
        || v.audio_streams > 15
        || !(16..=7680).contains(&v.width)
        || !(16..=4320).contains(&v.height)
        || u64::from(v.width) * u64::from(v.height) > 33_177_600
        || v.fps_num == 0
        || v.fps_den == 0
        || v.fps_num > 240_000
        || v.fps_den > 100_000
        || u64::from(v.fps_num) > u64::from(v.fps_den) * 240
        || gcd(v.fps_num, v.fps_den) != 1
        || !name(&v.codec, 32)
        || !name(&v.pixel_format, 32)
        || !(1..=36_000).contains(&v.frame_count)
        || v.first_frame != 0
        || v.end_frame_exclusive != v.frame_count
        || v.frames.len() as u64 != v.frame_count
        || v.rgba_bytes_per_frame != u64::from(v.width) * u64::from(v.height) * 4
        || !sha(&v.rgba_sequence_sha256)
        || v.alpha_min > v.alpha_max
        || v.all_pixels_opaque != (v.alpha_min == 255 && v.alpha_max == 255)
        || v.rgba_conversion != "pyav-observed-matrix-range-or-unverified-default-to-rgba8"
    {
        return Err(refuse("native_video_observation_values"));
    }
    let mut sequence = Sha256::new();
    sequence.update(b"motionwright.native-rgba-sequence/1\0");
    let mut png_bytes = 0u64;
    for (index, frame) in v.frames.iter().enumerate() {
        if frame.index != index as u64
            || frame.pts_den == 0
            || frame.pts_den > 1_000_000_000
            || !sha(&frame.rgba_sha256)
            || frame.file != format!("{index:06}.png")
            || !sha(&frame.png_sha256)
            || !(33..=32 * 1024 * 1024).contains(&frame.png_bytes)
            || u128::from(frame.pts_num) * u128::from(v.fps_num)
                != index as u128 * u128::from(v.fps_den) * u128::from(frame.pts_den)
        {
            return Err(refuse("native_video_observation_frame_clock"));
        }
        if frame
            .presentation
            .as_ref()
            .is_some_and(|value| !value.valid())
        {
            return Err(refuse("native_video_observation_presentation"));
        }
        if frame
            .native_metadata
            .as_ref()
            .is_some_and(|native| !native.valid_for(frame, &v.frame_color))
        {
            return Err(refuse("native_video_observation_native_metadata"));
        }
        png_bytes = png_bytes
            .checked_add(frame.png_bytes)
            .ok_or_else(|| refuse("native_video_observation_png_budget"))?;
        if png_bytes > 1024 * 1024 * 1024 {
            return Err(refuse("native_video_observation_png_budget"));
        }
        sequence.update((index as u64).to_be_bytes());
        sequence.update(
            hex::decode(&frame.rgba_sha256)
                .map_err(|_| refuse("native_video_observation_frame_hash"))?,
        );
    }
    if hex::encode(sequence.finalize()) != v.rgba_sequence_sha256
        || png_bytes != v.retained_png_bytes
    {
        return Err(refuse("native_video_observation_sequence_hash"));
    }
    Ok(DecodedVideoObservation { value: v })
}
impl DecodedVideoObservation {
    pub fn artifact_sha256(&self) -> &str {
        &self.value.artifact_sha256
    }
    pub fn artifact_bytes(&self) -> u64 {
        self.value.artifact_bytes
    }
    pub fn source_sha256(&self) -> &str {
        &self.value.source_sha256
    }
    pub fn width(&self) -> u32 {
        self.value.width
    }
    pub fn height(&self) -> u32 {
        self.value.height
    }
    pub fn frame_count(&self) -> u64 {
        self.value.frame_count
    }
    pub fn frame_rate(&self) -> (u32, u32) {
        (self.value.fps_num, self.value.fps_den)
    }
    pub fn audio_streams(&self) -> u32 {
        self.value.audio_streams
    }
    pub fn rgba_sequence_sha256(&self) -> &str {
        &self.value.rgba_sequence_sha256
    }
    /// The original data validator retains all frame hashes. Admission callers
    /// must independently decode each retained PNG and compare this value.
    pub fn rgba_sha256_at(&self, index: usize) -> Option<&str> {
        self.value
            .frames
            .get(index)
            .map(|frame| frame.rgba_sha256.as_str())
    }
    pub fn alpha_range(&self) -> (u8, u8) {
        (self.value.alpha_min, self.value.alpha_max)
    }
    pub fn retained_frames(&self) -> impl Iterator<Item = (&str, &str, u64)> {
        self.value
            .frames
            .iter()
            .map(|v| (v.file.as_str(), v.png_sha256.as_str(), v.png_bytes))
    }
    /// Missing presentation never acquires a requested square/progressive value.
    pub fn has_observed_square_progressive_frames(&self) -> bool {
        self.value.frames.iter().all(|frame| {
            frame
                .presentation
                .as_ref()
                .is_some_and(Presentation::square_progressive)
        })
    }
    /// Every frame needs complete observed color. Stream tags cannot fill API
    /// nulls. Native metadata, when supplied, must also carry a coherent actual
    /// duration; a complete API summary cannot bypass contradictory C metadata.
    pub fn is_opaque_rec709_h264_without_audio(&self) -> bool {
        let v = &self.value;
        let api_complete = v.frame_color.primaries == Some(1) && v.frame_color.transfer == Some(1);
        let any_native = v.frames.iter().any(|frame| frame.native_metadata.is_some());
        let native_complete = v.frames.iter().all(|frame| {
            frame.native_metadata.as_ref().is_some_and(|native| {
                native.complete_rec709(v.stream_color.range, v.fps_num, v.fps_den)
            })
        });
        v.codec == "h264"
            && v.pixel_format == "yuv420p"
            && v.audio_streams == 0
            && v.all_pixels_opaque
            && self.has_observed_square_progressive_frames()
            && v.stream_color.matrix == Some(1)
            && v.stream_color.primaries == Some(1)
            && v.stream_color.transfer == Some(1)
            && matches!(v.stream_color.range, Some(1 | 2))
            && v.frame_color.matrix == Some(1)
            && v.frame_color.range == v.stream_color.range
            && if any_native {
                native_complete
            } else {
                api_complete
            }
    }
    pub fn summary(&self) -> Value {
        let v = &self.value;
        json!({
            "schema": SCHEMA, "artifact_sha256": v.artifact_sha256,
            "artifact_bytes": v.artifact_bytes, "source_sha256": v.source_sha256,
            "decoder": v.decoder, "width": v.width, "height": v.height,
            "fps_num": v.fps_num, "fps_den": v.fps_den, "frame_count": v.frame_count,
            "first_frame": 0, "end_frame_exclusive": v.frame_count,
            "video_streams": v.video_streams, "audio_streams": v.audio_streams,
            "codec": v.codec, "pixel_format": v.pixel_format,
            "stream_color": v.stream_color, "frame_color": v.frame_color,
            "alpha_min": v.alpha_min, "alpha_max": v.alpha_max,
            "all_pixels_opaque": v.all_pixels_opaque,
            "rgba_sequence_sha256": v.rgba_sequence_sha256,
            "retained_png_bytes": v.retained_png_bytes,
            "square_progressive_all_frames": self.has_observed_square_progressive_frames(),
            "native_metadata_frame_count": v.frames.iter().filter(|frame| frame.native_metadata.is_some()).count(),
            "native_metadata_scope": "separate C AVFrame color/PTS/timebase/duration; original API nulls retained; filter-link SAR is not decoded-frame SAR",
            "last_native_frame_clock": v.frames.last().and_then(|frame| frame.native_metadata.as_ref()).map(|native| json!({
                "pts": native.pts, "duration": native.duration,
                "time_base_num": native.time_base_num, "time_base_den": native.time_base_den,
                "endpoint_policy": "actual (pts + duration) * time_base; no endpoint filled from fps"
            })),
            "presentation_observation_scope": "codec context SAR read per decoded frame; VideoFrame interlaced flag; no inferred missing metadata",
            "opaque_rec709_h264_without_audio": self.is_opaque_rec709_h264_without_audio(),
            "authority": "data_only_requires_original_host_job_and_current_source_binding"
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Value {
        let mut sequence = Sha256::new();
        sequence.update(b"motionwright.native-rgba-sequence/1\0");
        let frames = (0u64..3)
            .map(|index| {
                let hash = Sha256::digest(index.to_be_bytes());
                sequence.update(index.to_be_bytes());
                sequence.update(hash);
                json!({"index":index,"pts_num":index,"pts_den":30,
                "rgba_sha256":hex::encode(hash),"file":format!("{index:06}.png"),
                "png_sha256":"e".repeat(64),"png_bytes":128,
                "presentation":{"scope":"pyav-codec-context-per-frame-and-frame-interlace-v1",
                    "sample_aspect_num":1,"sample_aspect_den":1,"interlaced_frame":false},
                // Synthetic native C observation; not a claimed execution.
                "native_metadata":{"scope":"ffmpeg-showinfo-avframe-color-clock-yuv-passthrough-v1",
                    "index":index,"pts":index*512,"time_base_num":1,"time_base_den":15360,
                    "duration":512,"color":{"matrix":1,"range":1,"primaries":1,"transfer":1},
                    "yuv_sha256":"f".repeat(64)}})
            })
            .collect::<Vec<_>>();
        json!({
            "schema":SCHEMA,"artifact_sha256":"a".repeat(64),"artifact_bytes":8192,
            "source_sha256":"b".repeat(64),
            "decoder":{"name":"PyAV","version":"15.0.0","libraries":{"libavcodec":[61,19,100]}},
            "video_streams":1,"audio_streams":0,"width":1280,"height":720,
            "fps_num":30,"fps_den":1,"codec":"h264","pixel_format":"yuv420p",
            "stream_color":{"matrix":1,"range":1,"primaries":1,"transfer":1},
            "frame_color":{"matrix":1,"range":1,"primaries":null,"transfer":null},
            "frame_count":3,"first_frame":0,"end_frame_exclusive":3,
            "rgba_bytes_per_frame":1280*720*4,"retained_png_bytes":384,
            "rgba_sequence_sha256":hex::encode(sequence.finalize()),
            "alpha_min":255,"alpha_max":255,"all_pixels_opaque":true,
            "rgba_conversion":"pyav-observed-matrix-range-or-unverified-default-to-rgba8","frames":frames
        })
    }
    fn parse(v: &Value) -> Result<DecodedVideoObservation> {
        parse_complete_observation(&serde_json::to_vec(v).unwrap())
    }
    #[test]
    fn complete_observation_checks_all_frames_and_preserves_data_only_scope() {
        let value = parse(&fixture()).unwrap();
        assert_eq!(value.frame_count(), 3);
        assert_eq!(value.retained_frames().count(), 3);
        assert_eq!(
            value.rgba_sha256_at(0),
            fixture()["frames"][0]["rgba_sha256"].as_str()
        );
        assert_eq!(
            value.rgba_sha256_at(2),
            fixture()["frames"][2]["rgba_sha256"].as_str()
        );
        assert_eq!(value.rgba_sha256_at(3), None);
        assert_eq!(value.alpha_range(), (255, 255));
        assert!(value.is_opaque_rec709_h264_without_audio());
        assert_eq!(
            value.summary()["authority"],
            "data_only_requires_original_host_job_and_current_source_binding"
        );
    }
    #[test]
    fn absent_or_unspecified_color_never_acquires_rec709() {
        for path in [
            "/stream_color/matrix",
            "/stream_color/primaries",
            "/stream_color/transfer",
            "/stream_color/range",
            "/frame_color/matrix",
            "/frame_color/range",
        ] {
            for bad in [Value::Null, json!(0), json!(2)] {
                let mut v = fixture();
                *v.pointer_mut(path).unwrap() = bad;
                assert!(
                    parse(&v).map_or(true, |value| !value.is_opaque_rec709_h264_without_audio()),
                    "{path}"
                );
            }
        }
    }
    #[test]
    fn audio_and_nonopaque_pixels_remain_observed_but_not_admitted() {
        let mut v = fixture();
        v["audio_streams"] = json!(1);
        assert!(!parse(&v).unwrap().is_opaque_rec709_h264_without_audio());
        let mut v = fixture();
        v["alpha_min"] = json!(254);
        v["all_pixels_opaque"] = json!(false);
        assert!(!parse(&v).unwrap().is_opaque_rec709_h264_without_audio());
        v["all_pixels_opaque"] = json!(true);
        assert!(parse(&v).is_err());
    }
    #[test]
    fn bad_geometry_rate_count_pts_files_and_hashes_are_rejected() {
        for (path, bad) in [
            ("/width", json!(0)),
            ("/fps_num", json!(0)),
            ("/fps_den", json!(0)),
            ("/first_frame", json!(1)),
            ("/frame_count", json!(2)),
            ("/frames/1/pts_num", json!(9)),
            ("/frames/1/index", json!(0)),
            ("/frames/2/rgba_sha256", json!("c".repeat(64))),
            ("/rgba_sequence_sha256", json!("d".repeat(64))),
            ("/frames/2/file", json!("../outside.png")),
            ("/retained_png_bytes", json!(0)),
            ("/artifact_sha256", json!("not-a-digest")),
        ] {
            let mut v = fixture();
            *v.pointer_mut(path).unwrap() = bad;
            assert!(parse(&v).is_err(), "{path}");
        }
    }
    #[test]
    fn unknown_fields_cannot_smuggle_an_authority_claim() {
        let mut v = fixture();
        v["accepted"] = json!(true);
        assert!(parse(&v).is_err());
        let mut v = fixture();
        v["frames"][0]["native_grant"] = json!(true);
        assert!(parse(&v).is_err());
    }

    #[test]
    fn unknown_or_interlaced_presentation_is_data_but_never_mlt_admission() {
        for replacement in [
            Value::Null,
            json!({"scope":"pyav-codec-context-per-frame-and-frame-interlace-v1",
                "sample_aspect_num":null,"sample_aspect_den":null,"interlaced_frame":null}),
            json!({"scope":"pyav-codec-context-per-frame-and-frame-interlace-v1",
                "sample_aspect_num":4,"sample_aspect_den":3,"interlaced_frame":false}),
            json!({"scope":"pyav-codec-context-per-frame-and-frame-interlace-v1",
                "sample_aspect_num":1,"sample_aspect_den":1,"interlaced_frame":true}),
        ] {
            let mut v = fixture();
            v["frames"][1]["presentation"] = replacement;
            let observation = parse(&v).unwrap();
            assert!(!observation.has_observed_square_progressive_frames());
            assert!(!observation.is_opaque_rec709_h264_without_audio());
        }
        let mut legacy = fixture();
        for frame in legacy["frames"].as_array_mut().unwrap() {
            frame.as_object_mut().unwrap().remove("presentation");
        }
        assert!(
            !parse(&legacy)
                .unwrap()
                .is_opaque_rec709_h264_without_audio()
        );
    }
    #[test]
    fn malformed_presentation_is_rejected_instead_of_normalized() {
        for (key, value) in [
            ("scope", json!("requested-output-profile")),
            ("sample_aspect_num", json!(0)),
            ("sample_aspect_den", json!(null)),
            ("interlaced_frame", json!(0)),
        ] {
            let mut v = fixture();
            v["frames"][1]["presentation"][key] = value;
            assert!(parse(&v).is_err(), "{key}");
        }
    }
    #[test]
    fn native_metadata_causal_null_api_without_c_is_not_admitted() {
        let mut v = fixture();
        for frame in v["frames"].as_array_mut().unwrap() {
            frame.as_object_mut().unwrap().remove("native_metadata");
        }
        let observed = parse(&v).unwrap();
        assert_eq!(observed.summary()["frame_color"]["primaries"], Value::Null);
        assert_eq!(observed.summary()["frame_color"]["transfer"], Value::Null);
        assert!(!observed.is_opaque_rec709_h264_without_audio());
    }
    #[test]
    fn native_metadata_all_frames_closes_api_nulls_without_overwriting_them() {
        let observed = parse(&fixture()).unwrap();
        assert!(observed.is_opaque_rec709_h264_without_audio());
        assert_eq!(observed.summary()["frame_color"]["primaries"], Value::Null);
        assert_eq!(observed.summary()["frame_color"]["transfer"], Value::Null);
        assert_eq!(observed.summary()["native_metadata_frame_count"], 3);
        assert_eq!(observed.summary()["last_native_frame_clock"]["pts"], 1024);
        assert_eq!(
            observed.summary()["last_native_frame_clock"]["duration"],
            512
        );
        assert_eq!(
            observed.summary()["last_native_frame_clock"]["time_base_den"],
            15360
        );
    }
    #[test]
    fn native_metadata_missing_or_unknown_middle_frame_never_borrows_neighbors() {
        for bad in [
            Value::Null,
            json!({
                "scope":"ffmpeg-showinfo-avframe-color-clock-yuv-passthrough-v1",
                "index":1,"pts":512,"time_base_num":1,"time_base_den":15360,
                "duration":512,"color":{"matrix":1,"range":1,"primaries":null,"transfer":1},
                "yuv_sha256":"f".repeat(64)
            }),
        ] {
            let mut v = fixture();
            v["frames"][1]["native_metadata"] = bad;
            assert!(!parse(&v).unwrap().is_opaque_rec709_h264_without_audio());
        }
        let mut mixed = fixture();
        mixed["frame_color"]["primaries"] = json!(1);
        mixed["frame_color"]["transfer"] = json!(1);
        mixed["frames"][1]["native_metadata"] = Value::Null;
        assert!(!parse(&mixed).unwrap().is_opaque_rec709_h264_without_audio());
        let mut v = fixture();
        v["frames"][1]["native_metadata"]["color"]["transfer"] = json!(13);
        assert!(!parse(&v).unwrap().is_opaque_rec709_h264_without_audio());
    }
    #[test]
    fn native_metadata_ordinal_pts_timebase_and_api_contradictions_are_rejected() {
        for (path, bad) in [
            ("/frames/1/native_metadata/index", json!(0)),
            ("/frames/1/native_metadata/pts", json!(513)),
            ("/frames/1/native_metadata/time_base_num", json!(2)),
            ("/frames/1/native_metadata/time_base_den", json!(15361)),
            ("/frames/1/native_metadata/time_base_den", json!(0)),
            (
                "/frames/1/native_metadata/time_base_den",
                json!(1_000_000_001u64),
            ),
            ("/frames/1/native_metadata/color/matrix", json!(6)),
            ("/frames/1/native_metadata/yuv_sha256", json!("invented")),
            (
                "/frames/1/native_metadata/scope",
                json!("profile_intention"),
            ),
        ] {
            let mut v = fixture();
            *v.pointer_mut(path).unwrap() = bad;
            assert!(parse(&v).is_err(), "{path}");
        }
    }
    #[test]
    fn native_metadata_observed_duration_is_never_filled_or_hidden_by_api_color() {
        for duration in [0, 511, 513] {
            for complete_api in [false, true] {
                let mut v = fixture();
                v["frames"][1]["native_metadata"]["duration"] = json!(duration);
                if complete_api {
                    v["frame_color"]["primaries"] = json!(1);
                    v["frame_color"]["transfer"] = json!(1);
                }
                let observed = parse(&v).unwrap();
                assert!(!observed.is_opaque_rec709_h264_without_audio());
                assert_eq!(
                    observed.value.frames[1]
                        .native_metadata
                        .as_ref()
                        .unwrap()
                        .duration,
                    duration
                );
            }
        }
        let mut v = fixture();
        v["frames"][1]["native_metadata"]["duration"] = json!(-1);
        assert!(parse(&v).is_err());
    }
    #[test]
    fn native_metadata_fields_are_closed_and_duplicate_fields_are_rejected() {
        let mut v = fixture();
        v["frames"][1]["native_metadata"]["accepted"] = json!(true);
        assert!(parse(&v).is_err());
        let bytes = serde_json::to_string(&fixture()).unwrap();
        let duplicate = bytes.replacen("\"duration\":512", "\"duration\":512,\"duration\":512", 1);
        assert_ne!(duplicate, bytes);
        assert!(parse_complete_observation(duplicate.as_bytes()).is_err());
    }
}
