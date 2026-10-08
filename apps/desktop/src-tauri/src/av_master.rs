use motionwright_native::production::MltAudioArtifact;
use motionwright_service::VerifiedMasterVoice;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};
use uuid::Uuid;

const MAX_AUDIO_SOURCE_BYTES: u64 = 256 * 1024 * 1024;
const MAX_WAVE_CHUNKS: usize = 256;

/// Stable stage identifier for the same authenticated source revision and
/// immutable visual/audio identities. The caller's effect-grant request key
/// stays separate, so manual reentry never authorizes a fresh native mutation.
pub fn canonical_av_request_id(
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    profile_id: Uuid,
    visual_fingerprint: &str,
    audio_digest: &str,
) -> String {
    let mut digest = Sha256::new();
    digest.update(b"motionwright-av-master/v1");
    digest.update(project_id.as_bytes());
    digest.update(generation.as_bytes());
    digest.update(revision.to_be_bytes());
    digest.update(profile_id.as_bytes());
    digest.update((visual_fingerprint.len() as u64).to_be_bytes());
    digest.update(visual_fingerprint.as_bytes());
    digest.update(audio_digest.as_bytes());
    format!("mwav-{}", hex::encode(digest.finalize()))
}

/// Strict source timing: a project VO take cannot be silently stretched or
/// padded to a different video cut. Permit <=1 decoded sample of rational
/// quantization drift at 48kHz (fractional NTSC frame durations included).
pub fn validate_master_voice_timing(
    duration: motionwright_domain::RationalTime,
    frame_count: u64,
    fps_num: u32,
    fps_den: u32,
) -> Result<(), String> {
    if duration.num <= 0
        || duration.den <= 0
        || frame_count == 0
        || frame_count > 36_000
        || fps_num == 0
        || fps_den == 0
    {
        return Err("Native AV mastering has invalid source duration or frame rate.".into());
    }
    let left = i128::from(duration.num)
        .checked_mul(i128::from(fps_num))
        .ok_or("Native AV audio frame arithmetic overflow.")?;
    let right = i128::from(frame_count)
        .checked_mul(i128::from(duration.den))
        .and_then(|value| value.checked_mul(i128::from(fps_den)))
        .ok_or("Native AV video frame arithmetic overflow.")?;
    let tolerance = i128::from(duration.den)
        .checked_mul(i128::from(fps_num))
        .ok_or("Native AV frame tolerance arithmetic overflow.")?;
    // Compare cross-products at one 48kHz sample precision, without floats.
    let sample_delta = left.abs_diff(right).saturating_mul(48_000);
    if sample_delta > tolerance as u128 {
        return Err(
            "Measured voice duration does not match the native video cut. Re-align/re-author before mastering; Motionwright will not stretch or pad it silently."
                .into(),
        );
    }
    Ok(())
}

fn read_u16(bytes: &[u8]) -> u16 {
    u16::from_le_bytes([bytes[0], bytes[1]])
}
fn read_u32(bytes: &[u8]) -> u32 {
    u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])
}

/// Only an existing, regular, uncompressed 48 kHz stereo PCM/float WAV may
/// be handed to the pinned MLT H.264/AAC 48 kHz AV master.
fn validate_bounded_wav(file: &mut File, actual_bytes: u64) -> Result<(), String> {
    if !(44..=MAX_AUDIO_SOURCE_BYTES).contains(&actual_bytes) {
        return Err("Measured source WAV is outside the bounded size policy.".into());
    }
    let mut riff = [0_u8; 12];
    file.read_exact(&mut riff)
        .map_err(|_| "WAV header is incomplete.")?;
    if &riff[..4] != b"RIFF"
        || &riff[8..12] != b"WAVE"
        || u64::from(read_u32(&riff[4..8])) + 8 != actual_bytes
    {
        return Err("Native AV mastering requires a complete RIFF/WAVE source.".into());
    }
    let mut cursor = 12_u64;
    let mut fmt: Option<(u16, u16)> = None;
    let mut data_frames: Option<u64> = None;
    for _ in 0..MAX_WAVE_CHUNKS {
        if cursor + 8 > actual_bytes {
            break;
        }
        file.seek(SeekFrom::Start(cursor))
            .map_err(|_| "WAV chunk offset is inaccessible.")?;
        let mut chunk = [0_u8; 8];
        file.read_exact(&mut chunk)
            .map_err(|_| "WAV chunk header is incomplete.")?;
        let size = u64::from(read_u32(&chunk[4..8]));
        let end = cursor
            .checked_add(8)
            .and_then(|value| value.checked_add(size))
            .ok_or("WAV chunk byte size overflow.")?;
        if end > actual_bytes {
            return Err("WAV chunk exceeds the imported source bounds.".into());
        }
        if &chunk[..4] == b"fmt " {
            if fmt.is_some() {
                return Err("WAV contains ambiguous duplicate format chunks.".into());
            }
            if size < 16 {
                return Err("WAV format chunk is incomplete.".into());
            }
            let mut metadata = [0_u8; 16];
            file.read_exact(&mut metadata)
                .map_err(|_| "WAV format metadata is unreadable.")?;
            let format = read_u16(&metadata[..2]);
            let channels = read_u16(&metadata[2..4]);
            let sample_rate = read_u32(&metadata[4..8]);
            let byte_rate = read_u32(&metadata[8..12]);
            let block_align = read_u16(&metadata[12..14]);
            let bits_per_sample = read_u16(&metadata[14..16]);
            if !matches!((format, bits_per_sample), (1, 16 | 24 | 32) | (3, 32))
                || channels != 2
                || sample_rate != 48_000
                || block_align != (channels * bits_per_sample / 8)
                || byte_rate != sample_rate * u32::from(block_align)
            {
                return Err(
                    "Native AV master only supports measured 48 kHz stereo PCM/float WAV.".into(),
                );
            }
            fmt = Some((block_align, channels));
        }
        if &chunk[..4] == b"data" {
            if data_frames.is_some() {
                return Err("WAV contains ambiguous duplicate audio data chunks.".into());
            }
            data_frames = Some(size);
        }
        cursor = end
            .checked_add(size % 2)
            .ok_or("WAV padded chunk exceeds bounds.")?;
    }
    if cursor != actual_bytes {
        return Err("WAV chunk layout has trailing or incomplete bytes.".into());
    }
    let (align, _) = fmt.ok_or("WAV is missing the supported format chunk.")?;
    let audio_bytes = data_frames.ok_or("WAV has no PCM sample-data chunk.")?;
    if audio_bytes == 0 || audio_bytes % u64::from(align) != 0 {
        return Err("WAV has no complete measured stereo audio frames.".into());
    }
    Ok(())
}

/// Copy one *already verified* content-addressed measured voice into the
/// owner's output root using create-new semantics. Neither path comes from
/// WebView input; both are resolved within reviewed application boundaries.
pub fn stage_measured_wav(
    owner_output_root: &Path,
    voice: &VerifiedMasterVoice,
) -> Result<MltAudioArtifact, String> {
    let root = fs::symlink_metadata(owner_output_root)
        .map_err(|_| "Canonical output root is inaccessible.")?;
    if root.file_type().is_symlink() || !root.is_dir() {
        return Err("Canonical output root is not a real directory.".into());
    }
    let input_meta =
        fs::symlink_metadata(&voice.path).map_err(|_| "Measured source audio is inaccessible.")?;
    if input_meta.file_type().is_symlink()
        || !input_meta.is_file()
        || input_meta.len() != voice.size_bytes
        || !(44..=MAX_AUDIO_SOURCE_BYTES).contains(&voice.size_bytes)
    {
        return Err("Measured source does not match its verified bounded file.".into());
    }
    let mut input = File::open(&voice.path).map_err(|_| "Measured voice cannot be opened.")?;
    validate_bounded_wav(&mut input, voice.size_bytes)?;
    input
        .rewind()
        .map_err(|_| "Measured voice could not be rewound.")?;

    let relative = format!("mw-source-audio-{}.wav", Uuid::new_v4().simple());
    let destination = owner_output_root.join(&relative);
    let mut open = OpenOptions::new();
    open.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        open.mode(0o600);
    }
    let mut output = open
        .open(&destination)
        .map_err(|_| "Native output audio could not be created safely.")?;
    let transfer = (|| -> Result<(), String> {
        let mut hashed = Sha256::new();
        let mut count = 0_u64;
        let mut buf = [0u8; 128 * 1024];
        loop {
            let read = input
                .read(&mut buf)
                .map_err(|_| "Measured audio copy could not be read.")?;
            if read == 0 {
                break;
            }
            count += read as u64;
            if count > voice.size_bytes {
                return Err("Measured audio changed during copy.".into());
            }
            output
                .write_all(&buf[..read])
                .map_err(|_| "Native audio staging write failed.")?;
            hashed.update(&buf[..read]);
        }
        output
            .sync_all()
            .map_err(|_| "Native audio staging sync failed.")?;
        if count != voice.size_bytes || hex::encode(hashed.finalize()) != voice.sha256 {
            return Err("Measured audio SHA-256 changed during mastering handoff.".into());
        }
        Ok(())
    })();
    drop(output);
    if let Err(reason) = transfer {
        // Only this function's newly-created, uncommitted temporary output
        // is discarded; existing owner files are never touched or replaced.
        let _ = fs::remove_file(destination);
        return Err(reason);
    }
    Ok(MltAudioArtifact {
        relative_path: relative,
        sha256: voice.sha256.clone(),
        sample_rate: 48_000,
        channels: 2,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_service::VerifiedMasterVoice;

    fn wav_data() -> Vec<u8> {
        let samples = [0u8; 40]; // 10 exact stereo, 16-bit PCM frames
        let mut wav = Vec::new();
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36 + samples.len() as u32).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&48_000u32.to_le_bytes());
        wav.extend_from_slice(&192_000u32.to_le_bytes());
        wav.extend_from_slice(&4u16.to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&(samples.len() as u32).to_le_bytes());
        wav.extend_from_slice(&samples);
        wav
    }

    #[test]
    fn canonical_master_request_identity_is_stable_and_content_bound() {
        let project = Uuid::new_v4();
        let generation = Uuid::new_v4();
        let profile = Uuid::new_v4();
        let first = canonical_av_request_id(
            project,
            generation,
            11,
            profile,
            "visual-fingerprint",
            &"a".repeat(64),
        );
        assert_eq!(first.len(), 69);
        assert_eq!(
            first,
            canonical_av_request_id(
                project,
                generation,
                11,
                profile,
                "visual-fingerprint",
                &"a".repeat(64),
            )
        );
        assert_ne!(
            first,
            canonical_av_request_id(
                project,
                generation,
                12,
                profile,
                "visual-fingerprint",
                &"a".repeat(64),
            )
        );
        assert_ne!(
            first,
            canonical_av_request_id(
                project,
                generation,
                11,
                profile,
                "changed-visual-fingerprint",
                &"a".repeat(64),
            )
        );
        assert_ne!(
            first,
            canonical_av_request_id(
                project,
                generation,
                11,
                profile,
                "visual-fingerprint",
                &"b".repeat(64),
            )
        );
    }

    #[test]
    fn strict_rational_voice_alignment_rejects_padding_and_drift() {
        use motionwright_domain::RationalTime;

        let exact = RationalTime::new(2, 1).unwrap();
        assert!(validate_master_voice_timing(exact, 60, 30, 1).is_ok());
        let ntsc = RationalTime::new(1001, 500).unwrap();
        assert!(validate_master_voice_timing(ntsc, 60, 30_000, 1001).is_ok());
        let mismatch = RationalTime::new(1999, 1000).unwrap();
        assert!(validate_master_voice_timing(mismatch, 60, 30, 1).is_err());
        assert!(validate_master_voice_timing(exact, 0, 30, 1).is_err());
        assert!(validate_master_voice_timing(exact, 60, 0, 1).is_err());
        assert!(validate_master_voice_timing(exact, 60, 30, 0).is_err());
    }

    #[test]
    fn stages_exact_verified_wav_once_and_rejects_wrong_sha() {
        let root = tempfile::tempdir().unwrap();
        let src_root = tempfile::tempdir().unwrap();
        let bytes = wav_data();
        let source = src_root.path().join("voice-source.wav");
        fs::write(&source, &bytes).unwrap();
        let voice = VerifiedMasterVoice {
            path: source,
            size_bytes: bytes.len() as u64,
            sha256: hex::encode(Sha256::digest(&bytes)),
        };
        let artifact = stage_measured_wav(root.path(), &voice).unwrap();
        assert_eq!(artifact.sample_rate, 48_000);
        assert_eq!(artifact.channels, 2);
        assert_eq!(artifact.sha256, voice.sha256);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(root.path().join(&artifact.relative_path))
                .unwrap()
                .permissions()
                .mode();
            assert_eq!(
                mode & 0o077,
                0,
                "staged source WAV must remain owner-private"
            );
        }
        assert_eq!(
            fs::read(root.path().join(artifact.relative_path)).unwrap(),
            bytes
        );
        let tampered = VerifiedMasterVoice {
            sha256: "0".repeat(64),
            ..voice
        };
        assert!(stage_measured_wav(root.path(), &tampered).is_err());
    }

    #[test]
    fn rejects_unsupported_pcm_rates_and_ambiguous_wav_chunks() {
        let temp = tempfile::tempdir().unwrap();
        let source = temp.path().join("reject.wav");
        let mut bytes = wav_data();

        // The source parser must not treat 44.1 kHz PCM as 48 kHz audio.
        bytes[24..28].copy_from_slice(&44_100u32.to_le_bytes());
        fs::write(&source, &bytes).unwrap();
        let mut file = File::open(&source).unwrap();
        assert!(validate_bounded_wav(&mut file, bytes.len() as u64).is_err());

        // Restore correct rate, then forge the claimed data chunk length.
        let mut bytes = wav_data();
        bytes[40..44].copy_from_slice(&u32::MAX.to_le_bytes());
        fs::write(&source, &bytes).unwrap();
        let mut file = File::open(&source).unwrap();
        assert!(validate_bounded_wav(&mut file, bytes.len() as u64).is_err());

        // No ambiguous second audio data chunk may pass to the MLT boundary.
        let mut bytes = wav_data();
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&4u32.to_le_bytes());
        bytes.extend_from_slice(&[0u8; 4]);
        let declared_size = (bytes.len() as u32 - 8).to_le_bytes();
        bytes[4..8].copy_from_slice(&declared_size);
        fs::write(&source, &bytes).unwrap();
        let mut file = File::open(&source).unwrap();
        assert!(validate_bounded_wav(&mut file, bytes.len() as u64).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symlinked_measured_voice_source() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let original = root.path().join("source.wav");
        let link = root.path().join("linked.wav");
        let bytes = wav_data();
        fs::write(&original, &bytes).unwrap();
        symlink(&original, &link).unwrap();
        let source = VerifiedMasterVoice {
            path: link,
            size_bytes: bytes.len() as u64,
            sha256: hex::encode(Sha256::digest(&bytes)),
        };
        assert!(stage_measured_wav(root.path(), &source).is_err());
    }

    #[test]
    fn refuses_invalid_header_or_unbounded_chunks() {
        let mut bytes = wav_data();
        bytes[0] = b'X';
        let input = std::io::Cursor::new(bytes);
        // The file path validator handles tests for special source files;
        // syntactic RIFF rejection is deterministic and independent of disk.
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("bad.wav");
        fs::write(&source, input.get_ref()).unwrap();
        let mut file = File::open(source).unwrap();
        assert!(validate_bounded_wav(&mut file, input.get_ref().len() as u64).is_err());
    }
}
