//! Candidate consistency only; authentication belongs to the opaque native caller.
use base64::{Engine as _, engine::general_purpose::STANDARD};
use semwright_audio_domain::wav::WaveReader;
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Number, Value, json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fmt, io::Cursor};
use uuid::Uuid;
pub(super) const PROFILE: &str = "audio-analysis-alignment-cpu-py312-v1";
pub(super) const CONTRACT: &str = "acc007-en-ctc-1";
pub(super) const WORKER_SHA: &str =
    "60cfbdfe02796b0ee6333808a0c847c1642a0ff80a400e3ee88933570bf5ab42";
pub(super) const CONTRACT_SHA: &str =
    "cf63e26be28d143e1ee541e93ce5d55c3f438ac9edb791ae6153f4764041ded6";
pub(super) const BRIDGE_SHA: &str =
    "ffca843d76030f264bd017285154f2cbb7fca8818e7eeed8d04d7e7715d8e15b";
pub(super) const CHECKPOINT_SHA: &str =
    "488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d";
pub(super) const MAX_SOURCE_BYTES: usize = 1_048_576;
pub(super) const MAX_ENVELOPE_BYTES: usize = 245_760;
const LABELS: [&str; 29] = [
    "-", "|", "E", "T", "A", "O", "N", "I", "H", "S", "R", "D", "L", "U", "M", "W", "C", "F", "G",
    "Y", "P", "B", "V", "K", "'", "X", "J", "Q", "Z",
];
const ARTIFACTS: [&str; 5] = [
    "source.pcm16le",
    "model-input.f32le",
    "emissions.f32le",
    "ctc-path.i32le",
    "ctc-logprobs.f32le",
];
type Check<T> = Result<T, &'static str>;
pub(super) struct SourceEvidence {
    pub sha256: String,
    pub size_bytes: usize,
    pub sample_frames: usize,
    pcm: Vec<u8>,
    model_input: Vec<u8>,
}
pub(super) struct ResolvedWord {
    pub text: String,
    pub frames: Option<(usize, usize)>,
}
pub(super) struct CheckedEvidence {
    pub request_sha256: String,
    pub candidate_sha256: String,
    pub execution_sha256: String,
    pub unresolved_word_count: usize,
    pub words: Vec<ResolvedWord>,
}
fn require(ok: bool, message: &'static str) -> Check<()> {
    if ok { Ok(()) } else { Err(message) }
}
pub(super) fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn object<'a>(v: &'a Value, keys: &[&str]) -> Check<&'a Map<String, Value>> {
    let m = v.as_object().ok_or("object required")?;
    require(
        m.len() == keys.len() && keys.iter().all(|k| m.contains_key(*k)),
        "unexpected or missing object field",
    )?;
    Ok(m)
}
fn field<'a>(v: &'a Value, k: &str) -> Check<&'a Value> {
    v.get(k).ok_or("required field is absent")
}
fn text(v: &Value) -> Check<&str> {
    v.as_str().ok_or("string required")
}
fn integer(v: &Value, min: u64, max: u64) -> Check<u64> {
    let n = v.as_u64().ok_or("unsigned integer required")?;
    require(n >= min && n <= max, "integer outside profile")?;
    Ok(n)
}
fn array(v: &Value) -> Check<&[Value]> {
    v.as_array().map(Vec::as_slice).ok_or("array required")
}
fn same(actual: &Value, expected: Value) -> Check<()> {
    require(
        actual == &expected,
        "evidence field differs from reconstructed value",
    )
}
fn hash_text(v: &Value) -> Check<&str> {
    let h = text(v)?;
    require(is_hex(h, 64), "lowercase SHA-256 required")?;
    Ok(h)
}
pub(super) fn is_hex(v: &str, n: usize) -> bool {
    v.len() == n
        && v.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub(super) fn transcript_is_supported(v: &str) -> bool {
    !v.is_empty()
        && v.len() <= 256
        && v.split(' ').count() <= 32
        && v.split(' ').all(|w| {
            !w.is_empty()
                && w.split('\'')
                    .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_alphabetic()))
        })
}
// Only ASCII/integer request JSON is canonicalized, never float artifact hashes.
pub(super) fn canonical_request(v: &Value) -> Check<Vec<u8>> {
    fn sorted(v: &Value) -> Check<Value> {
        match v {
            Value::Object(m) => {
                let ordered: BTreeMap<_, _> = m.iter().collect();
                let mut out = Map::new();
                for (k, v) in ordered {
                    out.insert(k.clone(), sorted(v)?);
                }
                Ok(Value::Object(out))
            }
            Value::Array(a) => a
                .iter()
                .map(sorted)
                .collect::<Check<Vec<_>>>()
                .map(Value::Array),
            Value::String(s) => {
                require(s.is_ascii(), "request strings must be ASCII")?;
                Ok(Value::String(s.clone()))
            }
            Value::Number(n) => {
                require(n.is_i64() || n.is_u64(), "request floats are forbidden")?;
                Ok(Value::Number(n.clone()))
            }
            other => Ok(other.clone()),
        }
    }
    let bytes = serde_json::to_vec(&sorted(v)?).map_err(|_| "request serialization failed")?;
    require(bytes.len() <= 65_536, "request exceeds byte budget")?;
    Ok(bytes)
}
struct StrictValue(Value);
impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct StrictVisitor;
        impl<'de> Visitor<'de> for StrictVisitor {
            type Value = StrictValue;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("finite JSON without duplicate keys")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Bool(v)))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(v.into())))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Number(v.into())))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Self::Value, E> {
                Number::from_f64(v)
                    .map(Value::Number)
                    .map(StrictValue)
                    .ok_or_else(|| E::custom("non-finite JSON"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(v.to_owned())))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::String(v)))
            }
            fn visit_none<E: de::Error>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
                let mut out = Vec::new();
                while let Some(v) = seq.next_element::<StrictValue>()? {
                    out.push(v.0);
                }
                Ok(StrictValue(Value::Array(out)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut out = Map::new();
                while let Some((k, v)) = map.next_entry::<String, StrictValue>()? {
                    if out.insert(k, v.0).is_some() {
                        return Err(de::Error::custom("duplicate JSON key"));
                    }
                }
                Ok(StrictValue(Value::Object(out)))
            }
        }
        d.deserialize_any(StrictVisitor)
    }
}
fn strict_json(bytes: &[u8]) -> Check<Value> {
    let mut p = serde_json::Deserializer::from_slice(bytes);
    let v = StrictValue::deserialize(&mut p)
        .map_err(|_| "invalid raw JSON")?
        .0;
    p.end().map_err(|_| "trailing raw JSON data")?;
    Ok(v)
}
pub(super) fn inspect_source(bytes: &[u8]) -> Check<SourceEvidence> {
    require(
        (44..=MAX_SOURCE_BYTES).contains(&bytes.len()),
        "WAV exceeds source budget",
    )?;
    let mut reader = WaveReader::open(Cursor::new(bytes), MAX_SOURCE_BYTES as u64)
        .map_err(|_| "invalid bounded WAV")?;
    let info = reader.info();
    require(
        info.sample_rate.0 == 16000
            && info.channels == 1
            && info.container_bits == 16
            && info.valid_bits == 16
            && !info.ieee_float
            && (400..=128000).contains(&info.frames),
        "alignment profile requires at most eight seconds of 16 kHz mono PCM16",
    )?;
    let sample_frames = usize::try_from(info.frames).map_err(|_| "frame count overflow")?;
    let mut pcm = Vec::with_capacity(sample_frames * 2);
    let mut model_input = Vec::with_capacity(sample_frames * 4);
    let mut nonzero = false;
    loop {
        let values = reader
            .read_frames(16384)
            .map_err(|_| "WAV sample read failed")?;
        if values.is_empty() {
            break;
        }
        require(
            pcm.len() / 2 + values.len() <= sample_frames,
            "extra WAV samples",
        )?;
        for value in values {
            let scaled = value * 32768.0;
            require(
                scaled.is_finite()
                    && scaled.fract() == 0.0
                    && (-32768.0..=32767.0).contains(&scaled),
                "WAV samples are not exact PCM16",
            )?;
            let sample = scaled as i16;
            nonzero |= sample != 0;
            pcm.extend_from_slice(&sample.to_le_bytes());
            model_input.extend_from_slice(&(f32::from(sample) / 32768.0).to_le_bytes());
        }
    }
    require(
        pcm.len() == sample_frames * 2 && nonzero,
        "truncated or zero-only voice source",
    )?;
    Ok(SourceEvidence {
        sha256: digest(bytes),
        size_bytes: bytes.len(),
        sample_frames,
        pcm,
        model_input,
    })
}
pub(super) fn runtime_request() -> Value {
    json!({"contract_version":CONTRACT,"torch_version":"2.8.0+cpu","torchaudio_version":"2.8.0+cpu","numpy_version":"2.2.6","model_id":"WAV2VEC2_ASR_BASE_960H","checkpoint_sha256":CHECKPOINT_SHA,"checkpoint_size_bytes":377664473,"worker_sha256":WORKER_SHA,"contract_sha256":CONTRACT_SHA})
}
fn validate_request(r: &Value, s: &SourceEvidence) -> Check<()> {
    object(
        r,
        &[
            "schema",
            "request_id",
            "project_id",
            "project_generation",
            "project_revision",
            "voice_track_id",
            "asset_id",
            "source_commit",
            "language",
            "transcript",
            "source",
            "runtime",
        ],
    )?;
    same(
        field(r, "schema")?,
        json!("motionwright.alignment-request/1"),
    )?;
    same(field(r, "language")?, json!("en"))?;
    for name in [
        "request_id",
        "project_id",
        "project_generation",
        "voice_track_id",
        "asset_id",
    ] {
        let v = text(field(r, name)?)?;
        let id = Uuid::parse_str(v).map_err(|_| "invalid request UUID")?;
        require(
            !id.is_nil() && id.to_string() == v,
            "non-canonical or nil request UUID",
        )?;
    }
    integer(field(r, "project_revision")?, 0, 9007199254740991)?;
    require(
        is_hex(text(field(r, "source_commit")?)?, 40),
        "invalid source commit",
    )?;
    let t = field(r, "transcript")?;
    object(t, &["version", "text", "sha256"])?;
    integer(field(t, "version")?, 1, 9007199254740991)?;
    let words = text(field(t, "text")?)?;
    require(
        transcript_is_supported(words),
        "transcript outside bounded ASCII profile",
    )?;
    same(field(t, "sha256")?, json!(digest(words.as_bytes())))?;
    same(
        field(r, "source")?,
        json!({"sha256":s.sha256,"size_bytes":s.size_bytes,"sample_rate_hz":16000,"channels":1,"sample_frames":s.sample_frames}),
    )?;
    same(field(r, "runtime")?, runtime_request())?;
    let target = words.to_ascii_uppercase().replace(' ', "|");
    let repeats = target
        .as_bytes()
        .windows(2)
        .filter(|p| p[0] == p[1])
        .count();
    require(
        (s.sample_frames - 400) / 320 + 1 >= target.len() + repeats,
        "not enough model frames for CTC target",
    )?;
    canonical_request(r)?;
    Ok(())
}
fn decode_blob(v: &Value, name: Option<&str>, max: usize) -> Check<Vec<u8>> {
    if name.is_some() {
        object(v, &["name", "encoding", "sha256", "size_bytes", "data"])?;
    } else {
        object(v, &["encoding", "sha256", "size_bytes", "data"])?;
    }
    if let Some(n) = name {
        same(field(v, "name")?, json!(n))?;
    }
    same(field(v, "encoding")?, json!("base64"))?;
    let size = integer(field(v, "size_bytes")?, 1, max as u64)? as usize;
    let encoded = text(field(v, "data")?)?;
    require(
        encoded.len() == size.div_ceil(3) * 4,
        "base64 size differs from declaration",
    )?;
    let bytes = STANDARD
        .decode(encoded)
        .map_err(|_| "invalid base64 evidence")?;
    require(
        bytes.len() == size && STANDARD.encode(&bytes) == encoded,
        "non-canonical base64 evidence",
    )?;
    require(
        digest(&bytes) == hash_text(field(v, "sha256")?)?,
        "raw artifact SHA-256 mismatch",
    )?;
    Ok(bytes)
}
fn artifact_descriptor(v: &Value, b: &[u8]) -> Check<()> {
    same(v, json!({"sha256":digest(b),"size_bytes":b.len()}))
}
fn floats(b: &[u8], count: usize) -> Check<Vec<f64>> {
    require(
        b.len() == count * 4,
        "float array length differs from frame clock",
    )?;
    b.as_chunks::<4>()
        .0
        .iter()
        .map(|c| {
            let v = f64::from(f32::from_le_bytes([c[0], c[1], c[2], c[3]]));
            require(v.is_finite(), "non-finite float evidence")?;
            Ok(v)
        })
        .collect()
}
fn ratio(n: usize, d: usize) -> Value {
    let (mut a, mut b) = (n, d);
    while b != 0 {
        (a, b) = (b, a % b);
    }
    json!({"numerator":n/a,"denominator":d/a})
}
fn rounded_probability(v: &Value, p: f64) -> Check<()> {
    let c = v.as_f64().ok_or("probability number required")?;
    require(
        c.is_finite() && (0.0..=1.0).contains(&c) && (c - p).abs() <= 0.000_000_005_000_1,
        "reported model probability differs from raw evidence",
    )
}
fn check_execution(v: &Value, sha: &str) -> Check<()> {
    object(
        v,
        &[
            "schema",
            "authority",
            "backend_authority",
            "request_sha256",
            "started_at_utc",
            "finished_at_utc",
            "python",
            "system",
            "machine",
            "observed_numpy_version",
            "observed_torch_version",
            "observed_torchaudio_version",
            "device",
            "inference_threads",
            "interop_threads",
            "deterministic_algorithms",
            "network_allowed",
            "checkpoint_layout",
            "weights_only_load",
            "checkpoint_sha256",
            "self_report_is_native_attestation",
            "qualified_receipt_required",
        ],
    )?;
    require(
        text(field(v, "python")?)?
            .strip_prefix("3.12.")
            .is_some_and(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit())),
        "worker Python version differs",
    )?;
    for name in ["started_at_utc", "finished_at_utc"] {
        let t = text(field(v, name)?)?;
        require(
            !t.is_empty() && t.len() <= 64 && t.is_ascii(),
            "invalid execution timestamp text",
        )?;
    }
    require(
        matches!(
            text(field(v, "checkpoint_layout")?)?,
            "official_32_class_remove_axes_1_2_3" | "already_normalized_29_class"
        ),
        "unsupported checkpoint layout",
    )?;
    let expected = json!({"schema":"motionwright.alignment-worker-execution/1","authority":"CANDIDATE_ONLY","backend_authority":false,"request_sha256":sha,"system":"Linux","machine":"x86_64","observed_numpy_version":"2.2.6","observed_torch_version":"2.8.0+cpu","observed_torchaudio_version":"2.8.0+cpu","device":"cpu","inference_threads":2,"interop_threads":1,"deterministic_algorithms":true,"network_allowed":false,"weights_only_load":true,"checkpoint_sha256":CHECKPOINT_SHA,"self_report_is_native_attestation":false,"qualified_receipt_required":true});
    for (k, e) in expected.as_object().ok_or("internal execution shape")? {
        same(field(v, k)?, e.clone())?;
    }
    Ok(())
}
pub(super) fn validate(r: &Value, s: &SourceEvidence, e: &Value) -> Check<CheckedEvidence> {
    validate_request(r, s)?;
    object(
        e,
        &[
            "schema",
            "profile_id",
            "status",
            "backend_authority",
            "native_execution_attested",
            "human_acceptance",
            "qualified_ingress",
            "request_sha256",
            "producer",
            "candidate",
            "candidate_file",
            "execution",
            "execution_file",
            "worker_summary",
            "retained_artifacts",
            "inline_artifacts",
            "reconstruction",
            "validation",
        ],
    )?;
    require(
        serde_json::to_vec(e)
            .map_err(|_| "envelope serialization failed")?
            .len()
            <= MAX_ENVELOPE_BYTES,
        "runtime envelope exceeds transport budget",
    )?;
    let request_sha = digest(&canonical_request(r)?);
    for (k, v) in [
        ("schema", json!("motionwright.alignment-runtime-envelope/2")),
        ("profile_id", json!(PROFILE)),
        ("status", json!("CANDIDATE_ONLY")),
        ("backend_authority", json!(false)),
        ("native_execution_attested", json!(false)),
        ("human_acceptance", json!(false)),
        ("qualified_ingress", json!("NOT_EXECUTED")),
        ("request_sha256", json!(request_sha)),
    ] {
        same(field(e, k)?, v)?;
    }
    same(
        field(e, "producer")?,
        json!({"bridge_sha256":BRIDGE_SHA,"worker_sha256":WORKER_SHA,"contract_sha256":CONTRACT_SHA,"checkpoint_sha256":CHECKPOINT_SHA}),
    )?;
    let cb = decode_blob(field(e, "candidate_file")?, Some("result.json"), 32768)?;
    let xb = decode_blob(field(e, "execution_file")?, Some("execution.json"), 8192)?;
    let c = strict_json(&cb)?;
    let x = strict_json(&xb)?;
    same(field(e, "candidate")?, c.clone())?;
    same(field(e, "execution")?, x.clone())?;
    check_execution(&x, &request_sha)?;
    object(
        &c,
        &[
            "schema",
            "contract_version",
            "authority",
            "backend_authority",
            "request_sha256",
            "binding",
            "runtime",
            "source",
            "preprocessing",
            "frame_clock",
            "labels",
            "greedy",
            "token_spans",
            "words",
            "unresolved_word_count",
            "word_policy",
            "artifacts",
        ],
    )?;
    for (k, v) in [
        ("schema", json!("motionwright.alignment-candidate/1")),
        ("contract_version", json!(CONTRACT)),
        ("authority", json!("CANDIDATE_ONLY")),
        ("backend_authority", json!(false)),
        ("request_sha256", json!(request_sha)),
        ("runtime", runtime_request()),
        ("source", field(r, "source")?.clone()),
        ("labels", json!(LABELS)),
    ] {
        same(field(&c, k)?, v)?;
    }
    let t = field(r, "transcript")?;
    let corrected = text(field(t, "text")?)?;
    same(
        field(&c, "binding")?,
        json!({"request_id":r["request_id"],"project_id":r["project_id"],"project_generation":r["project_generation"],"project_revision":r["project_revision"],"voice_track_id":r["voice_track_id"],"asset_id":r["asset_id"],"source_commit":r["source_commit"],"source_sha256":s.sha256,"transcript_version":t["version"],"transcript_sha256":t["sha256"]}),
    )?;
    let frames = (s.sample_frames - 400) / 320 + 1;
    same(
        field(&c, "preprocessing")?,
        json!({"pcm_format":"signed-pcm16le","normalization":"sample/32768.0","downmix":"none","resampling":null,"model_input_sample_rate_hz":16000,"model_input_sample_frames":s.sample_frames,"stdlib_recomputed_exact_source_to_model_input":true}),
    )?;
    same(
        field(&c, "frame_clock")?,
        json!({"frame_count":frames,"class_count":29,"blank_id":0,"stride_samples":320,"receptive_samples":400,"sample_rate_hz":16000,"temporal_resolution":{"numerator":1,"denominator":50},"receptive_window":{"numerator":1,"denominator":40},"interval_convention":"half_open_frame_grid","phonetic_boundary_error":"uncalibrated","precision_claim":"none"}),
    )?;
    same(
        field(&c, "word_policy")?,
        json!({"requires_whole_transcript_greedy_match":true,"minimum_mean_model_emission_probability":0.80,"probability_is_calibrated_confidence":false,"unknown_word_interval":null,"promotion_to_domain_measured":"forbidden_without_qualified_receiver"}),
    )?;
    let inline = field(e, "inline_artifacts")?;
    object(inline, &ARTIFACTS[2..])?;
    let eb = decode_blob(field(inline, ARTIFACTS[2])?, None, 399 * 29 * 4)?;
    let pb = decode_blob(field(inline, ARTIFACTS[3])?, None, 399 * 4)?;
    let sb = decode_blob(field(inline, ARTIFACTS[4])?, None, 399 * 4)?;
    let all: [&[u8]; 5] = [&s.pcm, &s.model_input, &eb, &pb, &sb];
    let retained = field(e, "retained_artifacts")?;
    let artifacts = field(&c, "artifacts")?;
    object(retained, &ARTIFACTS)?;
    object(artifacts, &ARTIFACTS)?;
    for (name, bytes) in ARTIFACTS.iter().zip(all) {
        artifact_descriptor(field(retained, name)?, bytes)?;
        artifact_descriptor(field(artifacts, name)?, bytes)?;
    }
    same(
        field(e, "reconstruction")?,
        json!({"method":"pcm16le-mono-16000-divide-32768-to-f32le/1","source_sha256":s.sha256,"sample_frames":s.sample_frames,"exact":true}),
    )?;
    let emissions = floats(&eb, frames * 29)?;
    let scores = floats(&sb, frames)?;
    require(pb.len() == frames * 4, "CTC path length differs from clock")?;
    let mut path = Vec::with_capacity(frames);
    let mut greedy_ids = Vec::new();
    let mut previous = None;
    for (frame, chunk) in pb.as_chunks::<4>().0.iter().enumerate() {
        let token = i32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
        require((0..29).contains(&token), "CTC token outside label roster")?;
        let token = token as usize;
        path.push(token);
        let row = &emissions[frame * 29..(frame + 1) * 29];
        require(
            row.iter().all(|v| *v <= 0.00001),
            "emission is not a log probability",
        )?;
        require(
            (row.iter().map(|v| v.exp()).sum::<f64>() - 1.0).abs() <= 0.0002,
            "emission row is not normalized",
        )?;
        require(
            (scores[frame] - row[token]).abs() <= 0.000002,
            "selected CTC score differs from emission",
        )?;
        let mut greedy = 0;
        for (class, value) in row.iter().enumerate().skip(1) {
            if *value > row[greedy] {
                greedy = class;
            }
        }
        if previous != Some(greedy) && greedy != 0 {
            greedy_ids.push(greedy);
        }
        previous = Some(greedy);
    }
    let mut spans = Vec::new();
    let mut left = 0;
    while left < frames {
        let token = path[left];
        let mut right = left + 1;
        while right < frames && path[right] == token {
            right += 1;
        }
        if token != 0 {
            spans.push((token, left, right));
        }
        left = right;
    }
    let target = corrected.to_ascii_uppercase().replace(' ', "|");
    let target_ids = target
        .chars()
        .map(|ch| {
            LABELS
                .iter()
                .position(|v| v.starts_with(ch))
                .ok_or("corrected transcript token outside labels")
        })
        .collect::<Check<Vec<_>>>()?;
    require(
        spans.iter().map(|v| v.0).eq(target_ids.iter().copied()),
        "CTC path does not collapse to exact corrected transcript",
    )?;
    let greedy_raw: String = greedy_ids.iter().map(|id| LABELS[*id]).collect();
    let greedy_text = greedy_raw
        .split('|')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let greedy_exact = greedy_text == corrected.to_ascii_uppercase();
    same(
        field(&c, "greedy")?,
        json!({"token_ids":greedy_ids,"raw_text":greedy_raw,"text":greedy_text,"exact_corrected_transcript_match":greedy_exact}),
    )?;
    let tokens = array(field(&c, "token_spans")?)?;
    require(
        tokens.len() == spans.len(),
        "token roster has missing or duplicate spans",
    )?;
    for (index, (row, &(token, start, end))) in tokens.iter().zip(&spans).enumerate() {
        object(
            row,
            &[
                "index",
                "token_id",
                "token",
                "start_frame",
                "end_frame_exclusive",
                "mean_emission_probability",
            ],
        )?;
        for (k, v) in [
            ("index", json!(index)),
            ("token_id", json!(token)),
            ("token", json!(LABELS[token])),
            ("start_frame", json!(start)),
            ("end_frame_exclusive", json!(end)),
        ] {
            same(field(row, k)?, v)?;
        }
        let p = scores[start..end].iter().map(|v| v.exp()).sum::<f64>() / (end - start) as f64;
        rounded_probability(field(row, "mean_emission_probability")?, p)?;
    }
    let words: Vec<_> = corrected.split(' ').collect();
    let rows = array(field(&c, "words")?)?;
    require(
        rows.len() == words.len(),
        "word roster has missing or duplicate entries",
    )?;
    let mut checked = Vec::with_capacity(words.len());
    let mut offset = 0;
    let mut unresolved = 0;
    for (index, (word, row)) in words.iter().zip(rows).enumerate() {
        object(
            row,
            &[
                "index",
                "text",
                "token_start_index",
                "token_end_index_exclusive",
                "status",
                "reasons",
                "interval",
                "mean_emission_probability",
            ],
        )?;
        let selected = spans
            .get(offset..offset + word.len())
            .ok_or("word token range overflow")?;
        let start = selected.first().ok_or("empty word span")?.1;
        let end = selected.last().ok_or("empty word span")?.2;
        let count: usize = selected.iter().map(|v| v.2 - v.1).sum();
        let probability = selected
            .iter()
            .flat_map(|v| &scores[v.1..v.2])
            .map(|v| v.exp())
            .sum::<f64>()
            / count as f64;
        let mut reasons = Vec::new();
        if !greedy_exact {
            reasons.push("whole_transcript_greedy_disagreement");
        }
        if probability < 0.80 {
            reasons.push("low_model_emission_probability");
        }
        let resolved = reasons.is_empty();
        let interval = if resolved {
            require(
                (end - 1) * 320 + 400 <= s.sample_frames,
                "receptive support exceeds original source",
            )?;
            json!({"start":ratio(start*320,16000),"end":ratio(end*320,16000),"source_start_sample":start*320,"source_end_sample_exclusive":end*320,"receptive_support_start_sample":start*320,"receptive_support_end_sample_exclusive":(end-1)*320+400})
        } else {
            unresolved += 1;
            Value::Null
        };
        for (k, v) in [
            ("index", json!(index)),
            ("text", json!(word)),
            ("token_start_index", json!(offset)),
            ("token_end_index_exclusive", json!(offset + word.len())),
            (
                "status",
                json!(if resolved {
                    "aligned_candidate"
                } else {
                    "unknown"
                }),
            ),
            ("reasons", json!(reasons)),
            ("interval", interval),
        ] {
            same(field(row, k)?, v)?;
        }
        rounded_probability(field(row, "mean_emission_probability")?, probability)?;
        checked.push(ResolvedWord {
            text: (*word).to_owned(),
            frames: resolved.then_some((start, end)),
        });
        offset += word.len() + 1;
    }
    same(field(&c, "unresolved_word_count")?, json!(unresolved))?;
    same(
        field(e, "validation")?,
        json!({"status":"CANDIDATE_ONLY","backend_authority":false,"request_sha256":request_sha,"unresolved_word_count":unresolved,"native_execution_attested":false,"human_acceptance":false,"stdlib_recomputed_exact_source_to_model_input":true}),
    )?;
    let candidate_sha = digest(&cb);
    let execution_sha = digest(&xb);
    same(
        field(e, "worker_summary")?,
        json!({"status":"CANDIDATE_ONLY","backend_authority":false,"request_id":r["request_id"],"request_sha256":request_sha,"result_sha256":candidate_sha,"execution_sha256":execution_sha,"word_count":words.len(),"unresolved_word_count":unresolved,"stdlib_recomputed_exact_source_to_model_input":true,"qualified_ingress":"NOT_EXECUTED","human_acceptance":"NOT_DECLARED"}),
    )?;
    Ok(CheckedEvidence {
        request_sha256: request_sha,
        candidate_sha256: candidate_sha,
        execution_sha256: execution_sha,
        unresolved_word_count: unresolved,
        words: checked,
    })
}
pub(super) struct CheckedRuntimeOutput {
    pub envelope: Value,
    pub sha256: String,
    pub size_bytes: usize,
}
// Verify original stdout bytes, not a reserialized floating-point Value.
pub(super) fn decode_runtime_output(v: &Value) -> Check<CheckedRuntimeOutput> {
    let bytes = decode_blob(v, None, MAX_ENVELOPE_BYTES)?;
    let envelope = strict_json(&bytes)?;
    Ok(CheckedRuntimeOutput {
        envelope,
        sha256: digest(&bytes),
        size_bytes: bytes.len(),
    })
}
