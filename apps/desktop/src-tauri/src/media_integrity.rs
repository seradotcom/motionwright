//! Read-only, bounded, portable SHA-256 delivery verification.
//!
//! A receipt is self-declared data: success verifies the exact media bytes,
//! never the producer identity, codec decodability, or editorial approval.
//! Unlike export, this command has no mutation grant or session source token;
//! it only inspects a user-supplied pair of local files.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Read,
    path::{Component, Path, PathBuf},
};
use uuid::Uuid;

const MAX_PATH_BYTES: usize = 2048;
const MAX_DESCRIPTOR_BYTES: u64 = 12 * 1024;
const MAX_MP4_BYTES: u64 = 1024 * 1024 * 1024;
const PROOF_SUFFIX: &str = ".motionwright-integrity.json";
const SCHEMA: &str = "motionwright-media-integrity-v1";
const SCOPE: &str = "sha256-content-consistency-not-signed-attestation";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerifyPortableMediaRequest {
    manifest_path: String,
    trusted_sha256: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct PortableMediaVerification {
    status: &'static str,
    filename: String,
    size_bytes: u64,
    sha256: String,
    source_revision: String,
    signed_authenticity: bool,
    human_acceptance: bool,
    trusted_anchor_matched: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Descriptor {
    schema: String,
    scope: String,
    media: MediaDescriptor,
    origin: OriginDescriptor,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MediaDescriptor {
    filename: String,
    size_bytes: u64,
    sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OriginDescriptor {
    project_id: String,
    generation: String,
    revision: u64,
    deliverable_id: String,
    native_profile: String,
    frame_count: u64,
    frame_rate: FrameRate,
    semwright_native_sdk_revision: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FrameRate {
    num: u64,
    den: u64,
}

fn lower_hex(value: &str, len: usize) -> bool {
    value.len() == len
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn canonical_uuid(value: &str) -> bool {
    Uuid::parse_str(value).is_ok_and(|parsed| parsed.to_string() == value)
}

fn plain_basename(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 255
        && !matches!(name, "." | "..")
        && !name.contains('/')
        && !name.contains('\\')
        && !name.chars().any(char::is_control)
        && name.to_ascii_lowercase().ends_with(".mp4")
}

/// Reject symlinks in every observed parent and the file itself. Keep reads
/// on the same opened handle, so replacement after opening cannot change
/// the bytes being hashed. This is not an OS sandbox or a general filesystem
/// authority; only explicitly requested read-only local verification is allowed.
fn open_plain_local_file(path: &Path) -> Result<File, String> {
    if !path.is_absolute() {
        return Err("Verification needs an absolute local manifest path.".into());
    }
    let mut walk = PathBuf::new();
    let mut components = path.components().peekable();
    while let Some(component) = components.next() {
        match component {
            Component::Prefix(prefix) => {
                walk.push(prefix.as_os_str());
                continue; // Windows drive/UNC prefix requires its root component.
            }
            Component::RootDir => walk.push(component.as_os_str()),
            Component::Normal(name) => walk.push(name),
            Component::CurDir | Component::ParentDir => {
                return Err("Verification path may not contain dot or parent traversal.".into());
            }
        }
        let info = fs::symlink_metadata(&walk)
            .map_err(|_| "Verification file or parent does not exist.")?;
        if info.file_type().is_symlink() {
            return Err("Verification path must not traverse symlinks.".into());
        }
        if components.peek().is_some() && !info.is_dir() {
            return Err("Verification path parent must be a directory.".into());
        }
        if components.peek().is_none() && !info.is_file() {
            return Err("Verification input must be a regular local file.".into());
        }
    }
    let file = File::open(path).map_err(|_| "Verification file could not be opened.")?;
    if !file
        .metadata()
        .map_err(|_| "Verification file metadata is unavailable.")?
        .is_file()
    {
        return Err("Verification input must be a regular local file.".into());
    }
    Ok(file)
}

fn valid_descriptor(value: &Descriptor) -> Result<(), String> {
    if value.schema != SCHEMA || value.scope != SCOPE {
        return Err("Unsupported or unsigned integrity descriptor contract.".into());
    }
    if !plain_basename(&value.media.filename) {
        return Err("Receipt media filename must be a plain MP4 basename.".into());
    }
    if !lower_hex(&value.media.sha256, 64) {
        return Err("Receipt SHA-256 must be lowercase 64-character hex.".into());
    }
    if !(12..=MAX_MP4_BYTES).contains(&value.media.size_bytes) {
        return Err("Receipt MP4 byte count is outside the verification budget.".into());
    }
    let source = &value.origin;
    if !canonical_uuid(&source.project_id)
        || !canonical_uuid(&source.generation)
        || !canonical_uuid(&source.deliverable_id)
    {
        return Err("Receipt origin must use canonical lowercase UUIDs.".into());
    }
    if source.native_profile != "h264-aac-mp4"
        || !(1..=36_000).contains(&source.frame_count)
        || !(1..=120_000).contains(&source.frame_rate.num)
        || !(1..=120_000).contains(&source.frame_rate.den)
        || !lower_hex(&source.semwright_native_sdk_revision, 40)
    {
        return Err("Receipt origin has invalid native-profile or source fields.".into());
    }
    Ok(())
}

pub fn verify_portable_media(
    request: VerifyPortableMediaRequest,
) -> Result<PortableMediaVerification, String> {
    let manifest_string = request.manifest_path.trim();
    if manifest_string.is_empty()
        || manifest_string.len() > MAX_PATH_BYTES
        || manifest_string.chars().any(char::is_control)
        || manifest_string
            .split(['/', '\\'])
            .any(|part| part == "." || part == "..")
    {
        return Err("Choose a bounded absolute manifest path without traversal.".into());
    }
    let manifest_path = Path::new(manifest_string);
    let proof_name = manifest_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or("Verification manifest has an invalid filename.")?;
    if !proof_name.ends_with(PROOF_SUFFIX) {
        return Err("Choose an .mp4.motionwright-integrity.json receipt.".into());
    }

    // A previously trusted hash must come from the user or an independent
    // channel, never from the JSON being verified.
    let trusted = request.trusted_sha256.as_deref();
    if trusted.is_some_and(|digest| !lower_hex(digest, 64)) {
        return Err("Trusted SHA-256 anchor must be 64 lowercase hex characters.".into());
    }

    let mut descriptor = open_plain_local_file(manifest_path)?;
    let descriptor_len = descriptor
        .metadata()
        .map_err(|_| "Unable to inspect verification receipt.")?
        .len();
    if !(1..=MAX_DESCRIPTOR_BYTES).contains(&descriptor_len) {
        return Err("Integrity receipt is empty or exceeds 12 KiB.".into());
    }
    let mut document_bytes = Vec::with_capacity(descriptor_len as usize);
    (&mut descriptor)
        .take(MAX_DESCRIPTOR_BYTES + 1)
        .read_to_end(&mut document_bytes)
        .map_err(|_| "Unable to read verification receipt.")?;
    if document_bytes.len() as u64 != descriptor_len {
        return Err("Integrity receipt changed while reading.".into());
    }
    let document: Descriptor = serde_json::from_slice(&document_bytes)
        .map_err(|_| "Verification receipt has an invalid or incomplete JSON schema.")?;
    valid_descriptor(&document)?;

    let expected_proof = format!("{}{}", document.media.filename, PROOF_SUFFIX);
    if proof_name != expected_proof {
        return Err("Receipt filename does not match its paired MP4.".into());
    }
    if trusted.is_some_and(|digest| digest != document.media.sha256.as_str()) {
        return Err("Trusted SHA-256 anchor does not match the receipt.".into());
    }
    let media_path = manifest_path
        .parent()
        .ok_or("Verification receipt has no parent directory.")?
        .join(&document.media.filename);
    let mut media = open_plain_local_file(&media_path)?;
    let size = media
        .metadata()
        .map_err(|_| "Unable to inspect local MP4.")?
        .len();
    if size != document.media.size_bytes || size > MAX_MP4_BYTES {
        return Err("Local MP4 size differs from its integrity receipt.".into());
    }

    let mut signature = [0_u8; 12];
    media
        .read_exact(&mut signature)
        .map_err(|_| "Local MP4 header is incomplete.")?;
    if &signature[4..8] != b"ftyp" {
        return Err("Local media has no MP4 ftyp signature.".into());
    }
    let mut digest = Sha256::new();
    digest.update(signature);
    let mut count = signature.len() as u64;
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let bytes = media
            .read(&mut buffer)
            .map_err(|_| "Local MP4 read failed.")?;
        if bytes == 0 {
            break;
        }
        count = count
            .checked_add(bytes as u64)
            .ok_or("MP4 verification byte count overflow.")?;
        if count > size || count > MAX_MP4_BYTES {
            return Err("MP4 grew while verifying its bytes.".into());
        }
        digest.update(&buffer[..bytes]);
    }
    if count != size || hex::encode(digest.finalize()) != document.media.sha256 {
        return Err("Local MP4 SHA-256 does not match its integrity receipt.".into());
    }

    Ok(PortableMediaVerification {
        status: "sha256-content-verified",
        filename: document.media.filename,
        size_bytes: count,
        sha256: document.media.sha256,
        source_revision: document.origin.revision.to_string(),
        signed_authenticity: false,
        human_acceptance: false,
        trusted_anchor_matched: trusted.is_some(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};
    use tempfile::TempDir;

    const PROJECT: &str = "11111111-1111-4111-8111-111111111111";
    const GENERATION: &str = "22222222-2222-4222-8222-222222222222";
    const PROFILE: &str = "33333333-3333-4333-8333-333333333333";

    fn fixture() -> (TempDir, PathBuf, Value) {
        let root = tempfile::tempdir().unwrap();
        let media_name = "deliverable.mp4";
        let media = b"\0\0\0\x18ftypisom\0\0\0\0test media";
        fs::write(root.path().join(media_name), media).unwrap();
        let descriptor = json!({
            "schema": SCHEMA,
            "scope": SCOPE,
            "media": {
                "filename": media_name,
                "size_bytes": media.len(),
                "sha256": hex::encode(Sha256::digest(media)),
            },
            "origin": {
                "project_id": PROJECT,
                "generation": GENERATION,
                "revision": 7,
                "deliverable_id": PROFILE,
                "native_profile": "h264-aac-mp4",
                "frame_count": 180,
                "frame_rate": { "num": 30, "den": 1 },
                "semwright_native_sdk_revision": "8fa191250ae68274182570c65f067f7a60f85625"
            }
        });
        let path = root.path().join(format!("{media_name}{PROOF_SUFFIX}"));
        fs::write(&path, serde_json::to_vec(&descriptor).unwrap()).unwrap();
        (root, path, descriptor)
    }

    fn verify(
        path: &Path,
        trusted_sha256: Option<String>,
    ) -> Result<PortableMediaVerification, String> {
        verify_portable_media(VerifyPortableMediaRequest {
            manifest_path: path.to_string_lossy().into_owned(),
            trusted_sha256,
        })
    }

    #[test]
    fn verifies_paired_content_with_separately_trusted_anchor() {
        let (_root, path, document) = fixture();
        let hash = document["media"]["sha256"].as_str().unwrap().to_string();
        let result = verify(&path, Some(hash.clone())).unwrap();
        assert_eq!(result.status, "sha256-content-verified");
        assert_eq!(result.sha256, hash);
        assert_eq!(result.source_revision, "7");
        assert_eq!(result.filename, "deliverable.mp4");
        assert!(result.trusted_anchor_matched);
        assert!(!result.signed_authenticity);
        assert!(!result.human_acceptance);
        assert!(!path.with_file_name("deliverable.verified").exists());
    }

    #[test]
    fn rejects_changed_bytes_and_invalid_anchor_without_moving_files() {
        let (root, path, document) = fixture();
        let actual = document["media"]["sha256"].as_str().unwrap();
        assert!(
            verify(&path, Some("f".repeat(64)))
                .unwrap_err()
                .contains("anchor")
        );
        assert!(
            verify(&path, Some(actual.to_uppercase()))
                .unwrap_err()
                .contains("anchor")
        );
        fs::write(
            root.path().join("deliverable.mp4"),
            b"\0\0\0\x18ftypisom\0\0\0\0replacement",
        )
        .unwrap();
        assert!(verify(&path, None).is_err());
        assert!(
            path.exists(),
            "read-only verification must preserve the receipt"
        );
    }

    #[test]
    fn rejects_untrusted_schemas_traversal_and_false_provenance() {
        let (_root, path, mut document) = fixture();
        document["extra"] = json!("unrecognized");
        fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
        assert!(verify(&path, None).unwrap_err().contains("schema"));
        document.as_object_mut().unwrap().remove("extra");
        document["media"]["filename"] = json!("../deliverable.mp4");
        fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
        assert!(verify(&path, None).unwrap_err().contains("basename"));
        document["media"]["filename"] = json!("deliverable.mp4");
        document["origin"]["semwright_native_sdk_revision"] = json!("not-a-commit");
        fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
        assert!(verify(&path, None).unwrap_err().contains("origin"));
    }

    #[test]
    fn rejects_size_header_and_unpaired_manifest_filename() {
        let (root, path, mut document) = fixture();
        document["media"]["size_bytes"] = json!(999);
        fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
        assert!(verify(&path, None).unwrap_err().contains("size"));
        let wrong_name = root.path().join(format!("other.mp4{PROOF_SUFFIX}"));
        fs::write(&wrong_name, serde_json::to_vec(&document).unwrap()).unwrap();
        assert!(verify(&wrong_name, None).unwrap_err().contains("filename"));
        let bytes = fs::read(root.path().join("deliverable.mp4")).unwrap();
        let mut invalid = bytes.clone();
        invalid[4..8].copy_from_slice(b"zzzz");
        fs::write(root.path().join("deliverable.mp4"), &invalid).unwrap();
        document["media"]["size_bytes"] = json!(invalid.len());
        document["media"]["sha256"] = json!(hex::encode(Sha256::digest(&invalid)));
        fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
        assert!(verify(&path, None).unwrap_err().contains("ftyp"));
    }

    #[test]
    fn rejects_nonabsolute_overlarge_and_traversal_inputs() {
        let (_root, path, _document) = fixture();
        assert!(
            verify_portable_media(VerifyPortableMediaRequest {
                manifest_path: "relative.mp4.motionwright-integrity.json".into(),
                trusted_sha256: None,
            })
            .is_err()
        );
        let traversal = format!(
            "{}/../deliverable.mp4{PROOF_SUFFIX}",
            path.parent().unwrap().display()
        );
        assert!(
            verify_portable_media(VerifyPortableMediaRequest {
                manifest_path: traversal,
                trusted_sha256: None,
            })
            .is_err()
        );
        let massive = path
            .parent()
            .unwrap()
            .join(format!("{}{}", "x".repeat(2049), PROOF_SUFFIX));
        assert!(verify(&massive, None).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlink_manifest_media_and_parent() {
        use std::os::unix::fs::symlink;
        let (root, path, descriptor) = fixture();
        let symlink_manifest = root.path().join(format!("second.mp4{PROOF_SUFFIX}"));
        symlink(&path, &symlink_manifest).unwrap();
        assert!(verify(&symlink_manifest, None).is_err());
        let outer = tempfile::tempdir().unwrap();
        let parent_link = outer.path().join("linked");
        symlink(root.path(), &parent_link).unwrap();
        let through_parent = parent_link.join(path.file_name().unwrap());
        assert!(
            verify(&through_parent, None)
                .unwrap_err()
                .contains("symlink")
        );
        let media = root.path().join("deliverable.mp4");
        let moved = root.path().join("original.mp4");
        fs::rename(&media, &moved).unwrap();
        symlink(&moved, &media).unwrap();
        assert!(verify(&path, None).unwrap_err().contains("symlink"));
        // Keep an origin fixture to guard against an accidental no-op test.
        assert_eq!(descriptor["schema"], SCHEMA);
    }
}
