//! Verify one source-bound PNG against an independently provisioned owner policy.
use super::StudioService;
use base64::{Engine, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signature, VerifyingKey};
use motionwright_domain::RevisionStamp;
use motionwright_storage::{Result, StorageError, capture_portable_source};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs,
    io::Cursor,
    path::{Component, Path, PathBuf},
    time::Duration,
};
use uuid::Uuid;

const APPLICATION_ID: u32 = 0x4D574F50;
const MAX_JSON: u64 = 512 * 1024;
const MAX_PNG: u64 = 32 * 1024 * 1024;
const MAX_DECODED: usize = 4096 * 4096 * 8;
const METHOD: &str = "png_dimensions_v1";

#[derive(Debug, Serialize)]
pub struct ObserverReview {
    pub owner_generation: u64,
    pub verifier_id: String,
    pub producer_sha256: String,
    pub source_frame_sha256: String,
    pub frame_index: u32,
    pub width: u32,
    pub height: u32,
    pub limitations: Vec<String>,
    pub gate_eligible: bool,
}

fn invalid(reason: &str) -> StorageError {
    StorageError::InvalidBackup(format!("observer evidence: {reason}"))
}

fn require(condition: bool, reason: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(reason))
    }
}

fn sha(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

fn is_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn is_id(value: &str) -> bool {
    (3..=64).contains(&value.len())
        && value.as_bytes()[0].is_ascii_lowercase()
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"-_".contains(&b))
}

fn plain(path: &Path, directory: bool) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        require(
            metadata.file_attributes() & 0x400 == 0,
            "a selected source is a reparse point",
        )?;
    }
    require(
        if directory {
            metadata.is_dir()
        } else {
            metadata.is_file()
        },
        "a selected source is not a plain file or directory",
    )
}

fn root(path: &Path) -> Result<PathBuf> {
    require(path.is_absolute(), "select an absolute local directory")?;
    plain(path, true)?;
    Ok(path.canonicalize()?)
}

fn file_under(root: &Path, relative: &str) -> Result<PathBuf> {
    require(
        !relative.is_empty()
            && relative.len() <= 220
            && (!cfg!(windows) || !relative.contains(':')),
        "the frame path is outside the evidence folder",
    )?;
    let mut current = root.to_path_buf();
    let mut components = Path::new(relative).components().peekable();
    require(components.peek().is_some(), "a source file is required")?;
    while let Some(component) = components.next() {
        let Component::Normal(name) = component else {
            return Err(invalid("the frame path is outside the evidence folder"));
        };
        current.push(name);
        plain(&current, components.peek().is_some())?;
    }
    require(
        current.canonicalize()?.starts_with(root),
        "the selected file escaped the evidence folder",
    )?;
    Ok(current)
}

fn source(root: &Path, name: &str, limit: u64) -> Result<Vec<u8>> {
    capture_portable_source(&file_under(root, name)?, limit)
}

// The signed JSON uses Python's compact, UTF-8, sorted-key encoding. Typed
// fields below are deliberately in key order, so duplicate and foreign keys
// are rejected by decoding and map ordering cannot affect signature bytes.
#[derive(Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Signed<T> {
    payload: T,
    signature_b64: String,
}

impl<T: Serialize> Signed<T> {
    fn verify(&self, key: &VerifyingKey) -> Result<()> {
        let signature = STANDARD
            .decode(&self.signature_b64)
            .ok()
            .and_then(|decoded| Signature::from_slice(&decoded).ok())
            .ok_or_else(|| invalid("a signature is malformed"))?;
        key.verify_strict(&serde_json::to_vec(&self.payload)?, &signature)
            .map_err(|_| {
                invalid("a signature does not match its independently provisioned identity")
            })
    }
}

#[derive(Debug, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Policy {
    admitted_ids: Vec<String>,
    owner_generation: u64,
    revoked_ids: Vec<String>,
    schema: String,
}

impl Policy {
    fn validate(&self) -> Result<()> {
        let valid_ids = |ids: &[String]| {
            ids.len() <= 256
                && ids.iter().all(|id| is_id(id))
                && ids.iter().collect::<BTreeSet<_>>().len() == ids.len()
        };
        require(
            self.schema == "motionwright.observation-owner-policy/1"
                && (1..=1_000_000).contains(&self.owner_generation)
                && valid_ids(&self.admitted_ids)
                && valid_ids(&self.revoked_ids),
            "the owner policy is outside the supported format",
        )
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Admission {
    admission_id: String,
    coverage: String,
    limitations: Vec<String>,
    method: String,
    owner_generation: u64,
    permission: String,
    schema: String,
    units: String,
    verifier_id: String,
    verifier_public_key_b64: String,
    verifier_source_sha256: String,
    verifier_version: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Observation {
    admission_id: String,
    coverage: String,
    frame_index: u32,
    height: u32,
    limitations: Vec<String>,
    method: String,
    producer_id: String,
    producer_sha256: String,
    schema: String,
    source_frame_path: String,
    source_frame_sha256: String,
    units: String,
    verifier_id: String,
    verifier_version: String,
    width: u32,
}

fn key(data: &[u8]) -> Result<VerifyingKey> {
    let bytes = data
        .try_into()
        .map_err(|_| invalid("an Ed25519 public key must contain 32 bytes"))?;
    VerifyingKey::from_bytes(bytes).map_err(|_| invalid("the Ed25519 public key is malformed"))
}

fn current_policy(config: &Path, owner: &VerifyingKey) -> Result<Signed<Policy>> {
    let database = file_under(config, "policies.sqlite3")?;
    let mut conn = Connection::open_with_flags(
        database,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE,
    )?;
    conn.busy_timeout(Duration::from_secs(3))?;
    conn.execute_batch("PRAGMA trusted_schema=OFF; PRAGMA query_only=ON;")?;
    let snapshot = conn.transaction()?;
    let application: u32 = snapshot.pragma_query_value(None, "application_id", |r| r.get(0))?;
    let version: u32 = snapshot.pragma_query_value(None, "user_version", |r| r.get(0))?;
    require(
        application == APPLICATION_ID && version == 1,
        "the registry format is not supported",
    )?;
    let owner_sha: String = snapshot.query_row(
        "SELECT CASE WHEN length(key_sha256)=64 THEN key_sha256 END FROM policy_owner WHERE singleton=1",
        [], |r| r.get(0))?;
    require(
        owner_sha == sha(owner.as_bytes()),
        "the registry belongs to a different owner",
    )?;
    let (generation, raw): (u32, Vec<u8>) = snapshot.query_row(
        "SELECT generation, CASE WHEN length(envelope) BETWEEN 1 AND ?1 THEN envelope END
         FROM policies ORDER BY generation DESC LIMIT 1",
        [MAX_JSON as u32],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    snapshot.commit()?;
    let signed: Signed<Policy> = serde_json::from_slice(&raw)?;
    signed.payload.validate()?;
    signed.verify(owner)?;
    require(
        u64::from(generation) == signed.payload.owner_generation,
        "the current policy differs from its retained generation",
    )?;
    Ok(signed)
}

fn decode_png(raw: &[u8], width: u32, height: u32) -> Result<()> {
    require(
        (1..=4096).contains(&width) && (1..=4096).contains(&height),
        "reported PNG dimensions exceed the limit",
    )?;
    let decoder =
        png::Decoder::new_with_limits(Cursor::new(raw), png::Limits { bytes: MAX_DECODED });
    let mut reader = decoder
        .read_info()
        .map_err(|_| invalid("the PNG header failed independent decoding"))?;
    let info = reader.info();
    require(
        info.width == width
            && info.height == height
            && info.animation_control.is_none()
            && matches!(info.color_type, png::ColorType::Rgb | png::ColorType::Rgba),
        "the original is not the reported single RGB(A) frame",
    )?;
    let length = reader
        .output_buffer_size()
        .filter(|&length| length <= MAX_DECODED)
        .ok_or_else(|| invalid("the decoded frame exceeds the byte limit"))?;
    let mut decoded = vec![0u8; length];
    reader
        .next_frame(&mut decoded)
        .and_then(|_| reader.finish())
        .map_err(|_| invalid("the PNG failed independent decoding"))
}

fn review(config: &Path, evidence: &Path) -> Result<ObserverReview> {
    let config = root(config)?;
    let evidence = root(evidence)?;
    require(
        !config.starts_with(&evidence) && !evidence.starts_with(&config),
        "trusted policy and untrusted evidence folders must be separate",
    )?;
    let owner = key(&source(&config, "owner.pub", 32)?)?;
    let current = current_policy(&config, &owner)?;
    let producer_bytes = source(&config, "producer.sha256", 66)?;
    let producer_sha = std::str::from_utf8(&producer_bytes)
        .map_err(|_| invalid("the configured producer SHA-256 is invalid"))?
        .trim_end_matches(['\n', '\r']);
    require(
        is_sha(producer_sha),
        "the configured producer SHA-256 is invalid",
    )?;
    let signed_policy: Signed<Policy> =
        serde_json::from_slice(&source(&evidence, "owner-policy.json", MAX_JSON)?)?;
    require(
        signed_policy == current,
        "the signed policy is stale or does not match the owner registry",
    )?;
    let admission: Signed<Admission> =
        serde_json::from_slice(&source(&evidence, "owner-admission.json", MAX_JSON)?)?;
    admission.verify(&owner)?;
    let admission = admission.payload;
    require(
        admission.schema == "motionwright.observation-verifier-admission/1"
            && is_id(&admission.verifier_id)
            && is_id(&admission.admission_id)
            && admission.method == METHOD
            && admission.units == "pixels"
            && admission.coverage == "single_full_frame"
            && admission.verifier_version == "1.0.0"
            && admission.permission == "observe_png_dimensions_only"
            && (1..=10).contains(&admission.limitations.len())
            && admission
                .limitations
                .iter()
                .all(|s| (1..=350).contains(&s.chars().count()))
            && (1..=current.payload.owner_generation).contains(&admission.owner_generation)
            && current
                .payload
                .admitted_ids
                .contains(&admission.admission_id),
        "the owner has not admitted this verifier and method",
    )?;
    let source_sha = sha(&source(&config, "verifier.py", 2 * 1024 * 1024)?);
    require(
        source_sha == admission.verifier_source_sha256,
        "the reviewed verifier source has changed",
    )?;
    let verifier = key(&STANDARD
        .decode(&admission.verifier_public_key_b64)
        .map_err(|_| invalid("the verifier public key is malformed"))?)?;
    require(
        verifier != owner,
        "the owner and verifier must use independent signing identities",
    )?;
    let observed: Signed<Observation> =
        serde_json::from_slice(&source(&evidence, "observed-frame.json", MAX_JSON)?)?;
    observed.verify(&verifier)?;
    let frame = observed.payload;
    require(
        frame.schema == "motionwright.observation-evidence/1"
            && frame.admission_id == admission.admission_id
            && frame.verifier_id == admission.verifier_id
            && frame.verifier_version == admission.verifier_version
            && frame.method == admission.method
            && frame.coverage == admission.coverage
            && frame.units == admission.units
            && frame.limitations == admission.limitations
            && is_id(&frame.producer_id)
            && frame.producer_id != frame.verifier_id
            && frame.producer_sha256 == producer_sha
            && frame.frame_index <= 3600,
        "the signed measurement is outside the independently admitted scope",
    )?;
    require(
        Path::new(&frame.source_frame_path)
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("png")),
        "the original frame must be a PNG",
    )?;
    let original = source(&evidence, &frame.source_frame_path, MAX_PNG)?;
    require(
        sha(&original) == frame.source_frame_sha256,
        "the original PNG differs from the signed measurement",
    )?;
    decode_png(&original, frame.width, frame.height)?;
    require(
        key(&source(&config, "owner.pub", 32)?)? == owner
            && current_policy(&config, &owner)? == current
            && source(&config, "producer.sha256", 66)? == producer_bytes
            && sha(&source(&config, "verifier.py", 2 * 1024 * 1024)?) == source_sha,
        "the trusted owner policy or original source changed during observation",
    )?;
    let revoked = current
        .payload
        .revoked_ids
        .contains(&admission.admission_id);
    Ok(ObserverReview {
        owner_generation: current.payload.owner_generation,
        verifier_id: frame.verifier_id,
        producer_sha256: frame.producer_sha256,
        source_frame_sha256: frame.source_frame_sha256,
        frame_index: frame.frame_index,
        width: frame.width,
        height: frame.height,
        limitations: frame.limitations,
        gate_eligible: !revoked,
    })
}

impl StudioService {
    /// Inspect original technical evidence within the same saved project scope.
    /// The native caller chooses the independently provisioned trust directory.
    pub fn review_observer_evidence(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        config: &Path,
        evidence: &Path,
    ) -> Result<ObserverReview> {
        let original = self.project(project_id)?;
        require(
            original.stamp() == *expected,
            "the saved project revision changed",
        )?;
        let review = review(config, evidence)?;
        require(
            self.project(project_id)? == original,
            "the saved project changed during observation",
        )?;
        Ok(review)
    }
}
