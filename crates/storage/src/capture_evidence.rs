//! Source-byte gate for declared first-party screenshot receipts.
//!
//! This must run only after SQLite durable replay + project CAS authorization.
//! It verifies immutable PNG and JSON bytes and exact cross-linked source
//! metadata. Full screenshot redaction pixels are checked separately by the
//! independent Chromium/PNG evidence suite; this gate does not claim that
//! a customer application was captured or that visual privacy was approved.
use super::{Result, StorageError, read_verified_blob_from_root};
use motionwright_domain::{CaptureMask, CaptureSourceRecord, DomainError, Project};
use serde::Deserialize;
use std::path::Path;

const MAX_CAPTURE_MANIFEST_BYTES: u64 = 64 * 1024;
const MAX_CAPTURE_PNG_BYTES: u64 = 12 * 1024 * 1024;
const MANIFEST_SCHEMA: &str = "motionwright.first-party-capture-evidence/1";
const PNG_SIGNATURE: &[u8; 8] = b"\x89PNG\r\n\x1a\n";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceApp {
    id: String,
    version: String,
    source_revision: String,
}
#[derive(Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct SourceViewport {
    width: u32,
    height: u32,
    device_scale_factor: u8,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceScenario {
    id: String,
    origin: String,
    route: String,
    actions: Vec<String>,
    viewport: SourceViewport,
    browser: String,
    fixture_kind: String,
    auth_mode: String,
    external_requests: u16,
    credential_fields: u16,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourcePng {
    path: String,
    sha256: String,
    repeated_sha256: String,
    pixel_equal: bool,
    redacted: bool,
    raw_unredacted_saved: bool,
    width: u32,
    height: u32,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceManifest {
    schema: String,
    app: SourceApp,
    scenario: SourceScenario,
    claim_id: uuid::Uuid,
    fixture_state_sha256: String,
    redactions: Vec<CaptureMask>,
    capture: SourcePng,
    review_status: String,
}
fn denied(message: &str) -> StorageError {
    StorageError::Domain(DomainError::Invalid(format!(
        "Capture admission: {message}"
    )))
}
fn crc32(data: &[u8]) -> u32 {
    let mut crc = !0_u32;
    for byte in data {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xedb8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}
fn verify_png_header(png: &[u8], width: u32, height: u32) -> Result<()> {
    // This is deliberately only a bounded PNG header check. It rejects an
    // image mislabeled as PNG or with a contradictory viewport. Independent
    // pixel decoding/mask checks live in tooling/capture_evidence.py.
    if png.len() < 57
        || &png[..8] != PNG_SIGNATURE
        || png[8..12] != 13_u32.to_be_bytes()
        || &png[12..16] != b"IHDR"
        || u32::from_be_bytes(png[16..20].try_into().expect("IHDR bytes")) != width
        || u32::from_be_bytes(png[20..24].try_into().expect("IHDR bytes")) != height
        || png[24] != 8
        || !matches!(png[25], 2 | 6)
        || png[26..29] != [0, 0, 0]
        || u32::from_be_bytes(png[29..33].try_into().expect("IHDR crc bytes"))
            != crc32(&png[12..29])
        || png[png.len() - 12..png.len() - 8] != 0_u32.to_be_bytes()
        || &png[png.len() - 8..png.len() - 4] != b"IEND"
    {
        return Err(denied(
            "PNG bytes do not match the admitted viewport and source image format",
        ));
    }
    Ok(())
}
pub(super) fn verify_capture_source(
    blob_root: &Path,
    project: &Project,
    record: &CaptureSourceRecord,
) -> Result<()> {
    // No source can be read before the canonical owner Asset identity and
    // declared metadata are accepted. The caller already checked revision
    // and generation against the SQLite project.
    record.validate(project)?;
    if record.source_generation != project.generation {
        return Err(denied(
            "new capture requires the current project generation",
        ));
    }
    let manifest_bytes = read_verified_blob_from_root(
        blob_root,
        &record.manifest_sha256,
        MAX_CAPTURE_MANIFEST_BYTES,
    )?;
    let source: SourceManifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|_| denied("imported source manifest is not a bounded exact schema"))?;
    if source.schema != MANIFEST_SCHEMA
        || source.app.id != record.app_id
        || source.app.version != record.app_version
        || source.app.source_revision != record.source_commit
        || source.scenario.id != record.scenario_id
        || source.scenario.origin != record.origin
        || source.scenario.route != record.route
        || source.scenario.actions != ["open:/", "click:Production"]
        || source.scenario.viewport
            != (SourceViewport {
                width: record.viewport_width,
                height: record.viewport_height,
                device_scale_factor: record.device_scale_factor,
            })
        || source.scenario.browser != "chromium"
        || source.scenario.fixture_kind != "synthetic-first-party-data"
        || source.scenario.auth_mode != "none"
        || source.scenario.external_requests != record.external_requests
        || source.scenario.credential_fields != record.credential_fields
        || source.claim_id != record.claim_id
        || source.fixture_state_sha256 != record.fixture_state_sha256
        || source.redactions.as_slice() != [record.redaction.clone()]
        || source.capture.path != "capture.png"
        || source.capture.sha256 != record.screenshot_sha256
        || source.capture.repeated_sha256 != record.screenshot_repeat_sha256
        || !source.capture.pixel_equal
        || !source.capture.redacted
        || source.capture.raw_unredacted_saved
        || source.capture.width != record.viewport_width
        || source.capture.height != record.viewport_height
        || source.review_status != "technical-evidence-only-human-review-required"
    {
        return Err(denied(
            "imported JSON source disagrees with the authored capture, claim, mask or fixture",
        ));
    }
    let png =
        read_verified_blob_from_root(blob_root, &record.screenshot_sha256, MAX_CAPTURE_PNG_BYTES)?;
    verify_png_header(&png, record.viewport_width, record.viewport_height)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn crc32_matches_png_specification() {
        assert_eq!(crc32(b"IHDR"), 0xa8a1_ae0a);
    }
}
