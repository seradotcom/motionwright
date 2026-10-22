//! Original-job video readback. This consumes only the sidecar produced by
//! the existing sealed runner and rehashes every retained RGBA PNG.
use super::*;
use motionwright_manim_profile::video_observation::{MAX_REPORT_BYTES, parse_complete_observation};

#[derive(Debug, Clone, Serialize)]
pub(super) struct NativeVideoArtifact {
    relative_path: String,
    sha256: String,
    bytes: u64,
    summary: Value,
}

pub(super) fn read_for_original_job(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    video_relative: &str,
    video_sha256: &str,
    video_bytes: u64,
    profile: ManimRenderProfile,
    expected_frames: Option<u64>,
) -> Result<NativeVideoArtifact> {
    let relative = format!("{video_relative}.decoded-video.json");
    let (_, bytes) = safe_output_file(output_root, &relative)?;
    if bytes > MAX_REPORT_BYTES as u64 {
        return Err(invalid("Native video observation exceeded its byte budget"));
    }
    let report = bounded_source_file(output_root, &relative, MAX_REPORT_BYTES)?;
    if report.len() as u64 != bytes {
        return Err(invalid("Native video observation changed while reading"));
    }
    let observed = parse_complete_observation(&report).map_err(map_profile_error)?;
    let (source, source_bytes) = safe_output_file(work_root, source_file)?;
    if source_bytes > MAX_SOURCE_BYTES as u64
        || observed.source_sha256() != sha256_file(&source)?
        || observed.artifact_sha256() != video_sha256
        || observed.artifact_bytes() != video_bytes
        || observed.width() != profile.width
        || observed.height() != profile.height
        || observed.frame_rate() != (profile.frame_rate, 1)
        || expected_frames.is_some_and(|frames| observed.frame_count() != frames)
    {
        return Err(invalid(
            "Native video observation differs from its original render source",
        ));
    }
    for (name, digest, size) in observed.retained_frames() {
        let relative_png = format!("{relative}.frames/{name}");
        let (png_path, actual_size) = safe_output_file(output_root, &relative_png)?;
        if actual_size != size || sha256_file(&png_path)? != digest {
            return Err(invalid("Retained native RGBA frame identity differs"));
        }
        let mut signature = [0u8; 33];
        fs::File::open(png_path)?.read_exact(&mut signature)?;
        if &signature[..8] != b"\x89PNG\r\n\x1a\n"
            || &signature[12..16] != b"IHDR"
            || u32::from_be_bytes(signature[16..20].try_into().unwrap()) != observed.width()
            || u32::from_be_bytes(signature[20..24].try_into().unwrap()) != observed.height()
            || signature[24] != 8
            || signature[25] != 6
        {
            return Err(invalid("Retained native RGBA frame header differs"));
        }
    }
    // Recheck the encoded video after reading the complete observation family.
    let (video, actual_bytes) = safe_output_file(output_root, video_relative)?;
    if actual_bytes != video_bytes || sha256_file(&video)? != video_sha256 {
        return Err(invalid(
            "Original native video changed after complete frame readback",
        ));
    }
    Ok(NativeVideoArtifact {
        relative_path: relative,
        sha256: hex::encode(Sha256::digest(&report)),
        bytes,
        summary: observed.summary(),
    })
}
