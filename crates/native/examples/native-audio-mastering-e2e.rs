//! Isolated source-profile lane: an existing canonical OriginalMix Project,
//! saved target revision, native MLT gain, physical WAV/PCM transfer and meter.
//! No fixture bus is recreated and no extra mixer/runtime is introduced.
//! Final MP4 mux/readback uses the existing backend composition path.
use motionwright_domain::{Change, Project};
use motionwright_native::production::{
    OriginalMixSourceRequest, ProductionConnection, ProductionCoordinator,
    audio_mastering::{
        AudioMeasurePins, MasteringClock, MasteringProfile, MasteringSourceCustody,
        NativeAudioMasteringRequest, canonical_mastering_binding, plan_audio_mastering,
    },
    prepare_original_mix_av_audio, resolve_canonical_original_mix_plan,
};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::ExitCode,
    time::SystemTime,
};

const MAX_OWNER_INSTALLATION_BYTES: usize = 16 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OwnerInstallation {
    pins: AudioMeasurePins,
    /// Must already be the readonly MLT media mount admitted by the owner. This
    /// record and filesystem path cannot create that grant or a receipt.
    sealed_media_root: PathBuf,
    source_custody_receipt_sha256: String,
}
fn reject(message: &'static str) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
fn io_error() -> Error {
    Error::new(
        ErrorCode::BackendFailed,
        "Mastering fixture storage operation failed",
    )
}

fn changed_owner_record() -> Error {
    Error::new(
        ErrorCode::Conflict,
        "Owner installation record changed while being read",
    )
}

#[derive(Debug, PartialEq, Eq)]
struct OwnerRecordStamp {
    bytes: u64,
    modified: SystemTime,
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(windows)]
    created: u64,
    #[cfg(windows)]
    attributes: u32,
}

fn owner_record_stamp(metadata: &fs::Metadata) -> Result<OwnerRecordStamp> {
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(reject("Owner installation record must be a regular file"));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(reject(
                "Owner installation record cannot be a reparse point",
            ));
        }
    }
    if metadata.len() == 0 || metadata.len() > MAX_OWNER_INSTALLATION_BYTES as u64 {
        return Err(reject("Owner installation record exceeds profile"));
    }
    #[cfg(unix)]
    use std::os::unix::fs::MetadataExt;
    #[cfg(windows)]
    use std::os::windows::fs::MetadataExt;
    Ok(OwnerRecordStamp {
        bytes: metadata.len(),
        modified: metadata.modified().map_err(|_| io_error())?,
        #[cfg(unix)]
        device: metadata.dev(),
        #[cfg(unix)]
        inode: metadata.ino(),
        #[cfg(windows)]
        created: metadata.creation_time(),
        #[cfg(windows)]
        attributes: metadata.file_attributes(),
    })
}

fn require_same_owner_record(
    expected: &OwnerRecordStamp,
    observed: &OwnerRecordStamp,
) -> Result<()> {
    if expected != observed {
        return Err(changed_owner_record());
    }
    Ok(())
}

/// Consume at most the initially observed size plus one byte. A positive
/// result requires EOF at exactly that size; no parse occurs inside this loop.
/// This bounds returned bytes/allocation, not syscall duration or wall time.
fn read_owner_record_payload(reader: &mut impl Read, initial_size: u64) -> Result<Vec<u8>> {
    if initial_size == 0 || initial_size > MAX_OWNER_INSTALLATION_BYTES as u64 {
        return Err(reject("Owner installation record exceeds profile"));
    }
    let initial_size = usize::try_from(initial_size)
        .map_err(|_| reject("Owner installation record exceeds profile"))?;
    let mut bytes = Vec::with_capacity(initial_size);
    let mut buffer = [0_u8; 1024];
    loop {
        let remaining = initial_size - bytes.len();
        let allowance = buffer.len().min(remaining + 1);
        let read = reader
            .read(&mut buffer[..allowance])
            .map_err(|_| io_error())?;
        if read == 0 {
            return if bytes.len() == initial_size {
                Ok(bytes)
            } else {
                Err(changed_owner_record())
            };
        }
        if read > remaining || read > allowance {
            return Err(changed_owner_record());
        }
        bytes.extend_from_slice(&buffer[..read]);
    }
}

/// One data handle for this existing owner record. Recheck that handle and the
/// named file before parsing. This is not a media reader, a grant or a complete
/// defence against every concurrent ancestor/pathname replacement.
fn read_owner_installation_record(path: &Path) -> Result<Vec<u8>> {
    let named_before = owner_record_stamp(&fs::symlink_metadata(path).map_err(|_| io_error())?)?;
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        // FILE_SHARE_READ: no concurrent write or delete sharing for the data
        // handle. Use stable std APIs; no nightly file-index extension or FFI.
        options.share_mode(0x0000_0001);
    }
    let mut file = options.open(path).map_err(|_| io_error())?;
    let before = owner_record_stamp(&file.metadata().map_err(|_| io_error())?)?;
    require_same_owner_record(&named_before, &before)?;
    let bytes = read_owner_record_payload(&mut file, before.bytes)?;
    let after = owner_record_stamp(&file.metadata().map_err(|_| io_error())?)?;
    require_same_owner_record(&before, &after)?;
    let named_after = owner_record_stamp(&fs::symlink_metadata(path).map_err(|_| io_error())?)?;
    require_same_owner_record(&after, &named_after)?;
    Ok(bytes)
}

fn current(service: &StudioService) -> Result<Project> {
    let mut projects = service.projects(2).map_err(|_| io_error())?;
    if projects.len() != 1 {
        return Err(reject(
            "Expected exactly one existing canonical source project",
        ));
    }
    Ok(projects.remove(0))
}
fn selected(project: &Project) -> Result<uuid::Uuid> {
    let profiles: Vec<_> = project
        .production_design
        .delivery_recipes
        .iter()
        .filter(|(_, recipe)| recipe.original_mix.is_some())
        .map(|(id, _)| *id)
        .collect();
    if profiles.len() != 1 {
        return Err(reject(
            "Expected exactly one saved OriginalMix delivery recipe",
        ));
    }
    Ok(profiles[0])
}
fn clock(frames: u64, num: u32, den: u32) -> Result<MasteringClock> {
    let numerator = u128::from(frames) * u128::from(num);
    let denominator = 48_000 * u128::from(den);
    if denominator == 0 || numerator % denominator != 0 {
        return Err(reject("Existing mix has a fractional video clock"));
    }
    let clock = MasteringClock {
        video_frames: u64::try_from(numerator / denominator)
            .map_err(|_| reject("Clock overflow"))?,
        fps_num: num,
        fps_den: den,
        sample_frames: frames,
    };
    clock.validate()?;
    Ok(clock)
}

async fn execute(args: &[String]) -> Result<Value> {
    if args.len() < 2 {
        return Err(reject(
            "Use configure-profile DATABASE speech16|programme23 OUTPUT_ROOT or run-source-profile DATABASE CONNECTION OWNER_INSTALLATION",
        ));
    }
    let service = StudioService::open(&args[1]).map_err(|_| io_error())?;
    let mut project = current(&service)?;
    let deliverable_id = selected(&project)?;
    match args[0].as_str() {
        "configure-profile" if args.len() == 4 => {
            let chosen = match args[2].as_str() {
                "speech16" => MasteringProfile::Speech16,
                "programme23" => MasteringProfile::Programme23,
                _ => return Err(reject("Unknown fixed engineering profile")),
            };
            let original_assets = serde_json::to_vec(&project.assets)?;
            let original_recipes = serde_json::to_vec(&project.production_design.delivery_recipes)?;
            let (loudness, peak) = chosen.targets();
            let mut mix = project.audio.mix.clone();
            mix.target_lufs = Some(loudness);
            mix.target_true_peak_dbfs = Some(peak);
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("native-audio-mastering-{}", chosen.id()),
                    &Change::SetMixIntent { mix },
                )
                .map_err(|_| io_error())?
                .project;
            if serde_json::to_vec(&project.assets)? != original_assets
                || serde_json::to_vec(&project.production_design.delivery_recipes)?
                    != original_recipes
            {
                return Err(reject(
                    "Saving targets changed source identities or saved recipe",
                ));
            }
            let plan = resolve_canonical_original_mix_plan(
                &service,
                project.id,
                &project.stamp(),
                deliverable_id,
            )?;
            let selected = project
                .deliverables
                .iter()
                .find(|item| item.id == deliverable_id)
                .ok_or_else(|| reject("Selected delivery disappeared"))?;
            let num = u32::try_from(selected.frame_rate.num)
                .map_err(|_| reject("FPS numerator is unsupported"))?;
            let den = u32::try_from(selected.frame_rate.den)
                .map_err(|_| reject("FPS denominator is unsupported"))?;
            let clock = clock(plan.sample_frames, num, den)?;
            let prepared = prepare_original_mix_av_audio(
                &service,
                project.id,
                &project.stamp(),
                OriginalMixSourceRequest {
                    plan: &plan,
                    frame_count: clock.video_frames,
                    fps_num: num,
                    fps_den: den,
                },
                Path::new(&args[3]),
            )?;
            let (binding, profile) = canonical_mastering_binding(
                &service,
                project.id,
                &project.stamp(),
                deliverable_id,
                &prepared.audio,
                Path::new(&args[3]),
                clock,
            )?;
            Ok(
                json!({"schema":"motionwright.native-audio-mastering-source-e2e/1","status":"PREPARED_SOURCE",
                "binding":binding,"profile":profile,"canonical_audio":prepared.audio,"canonical_source":prepared.proof,
                "native_gain_executed":false,"independent_measurement":false,"final_master_verified":false,
                "scope":"existing_three_bus_source_and_saved_target_revision; no_speech_listening_or_external_rights_acceptance"}),
            )
        }
        "run-source-profile" if args.len() == 4 => {
            let bytes = read_owner_installation_record(Path::new(&args[3]))?;
            let owner: OwnerInstallation = serde_json::from_slice(&bytes)?;
            owner.pins.validate()?;
            if !owner.sealed_media_root.is_absolute() {
                return Err(reject("Owner media mount must be absolute"));
            }
            let connection = ProductionConnection::load(&args[2])?;
            let before = serde_json::to_vec(&project)?;
            let plan = resolve_canonical_original_mix_plan(
                &service,
                project.id,
                &project.stamp(),
                deliverable_id,
            )?;
            let selected = project
                .deliverables
                .iter()
                .find(|item| item.id == deliverable_id)
                .ok_or_else(|| reject("Selected delivery disappeared"))?;
            let num = u32::try_from(selected.frame_rate.num)
                .map_err(|_| reject("FPS numerator is unsupported"))?;
            let den = u32::try_from(selected.frame_rate.den)
                .map_err(|_| reject("FPS denominator is unsupported"))?;
            let clock = clock(plan.sample_frames, num, den)?;
            let prepared = prepare_original_mix_av_audio(
                &service,
                project.id,
                &project.stamp(),
                OriginalMixSourceRequest {
                    plan: &plan,
                    frame_count: clock.video_frames,
                    fps_num: num,
                    fps_den: den,
                },
                &connection.output_root,
            )?;
            let (binding, profile) = canonical_mastering_binding(
                &service,
                project.id,
                &project.stamp(),
                deliverable_id,
                &prepared.audio,
                &connection.output_root,
                clock,
            )?;
            let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
            let id = format!("am-{}", &binding.sha256()?[..24]);
            let source_observation = coordinator
                .measure_native_mastering_premix(
                    &project.stamp(),
                    &format!("{id}:measure-premix"),
                    &binding,
                    &prepared.audio,
                    &owner.pins,
                )
                .await?;
            let mastering = plan_audio_mastering(
                binding.clone(),
                profile,
                source_observation.report.clone(),
                source_observation.original_receipt_sha256.clone(),
                MasteringSourceCustody::OwnerSealedReadOnly {
                    owner_receipt_sha256: owner.source_custody_receipt_sha256,
                    source_sha256: binding.premix_wav_sha256.clone(),
                },
            )?;
            let native = coordinator
                .realize_native_audio_mastering(
                    &project.stamp(),
                    NativeAudioMasteringRequest {
                        request_id: &id,
                        plan: &mastering,
                        canonical_audio: &prepared.audio,
                        owner_media_root: &owner.sealed_media_root,
                        premix_measurement_request_id: &source_observation.original_request_id,
                    },
                )
                .await?;
            let ready = coordinator
                .verify_native_mastered_audio(
                    &project.stamp(),
                    &format!("{id}:measure-mastered"),
                    &mastering,
                    native,
                    &prepared.audio,
                )
                .await?;
            let after = service.project(project.id).map_err(|_| io_error())?;
            if serde_json::to_vec(&after)? != before {
                return Err(reject(
                    "Measurement or native audio gain changed the canonical Project",
                ));
            }
            Ok(
                json!({"schema":"motionwright.native-audio-mastering-source-e2e/1","status":"SOURCE_PROFILE_VERIFIED",
                "plan":mastering,"premix":source_observation,"mastered_wav":ready,
                "unchanged_project_sha256":hex::encode(Sha256::digest(before)),
                "final_master_verified":false,"portable_source_bundle_verified":false,
                "scope":"physical_native_constant_gain_wav_transfer_and_measured_targets; AAC_and_export_join_pending"}),
            )
        }
        _ => Err(reject("Unknown source-profile action or argument count")),
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = execute(&args).await;
    // Exactly one complete typed stdout response. No Debug suffix or stderr
    // fragment is authoritative, and no failed operation is retried here.
    let (response, status) = match result {
        Ok(data) => (json!({"ok":true,"data":data}), ExitCode::SUCCESS),
        Err(error) => (
            json!({"ok":false,"error":{"code":error.code,"outcome_known":error.outcome_known}}),
            ExitCode::FAILURE,
        ),
    };
    match serde_json::to_vec(&response) {
        Ok(mut bytes) => {
            bytes.push(b'\n');
            let mut stdout = std::io::stdout().lock();
            if stdout
                .write_all(&bytes)
                .and_then(|_| stdout.flush())
                .is_err()
            {
                eprintln!("native_audio_mastering_stdout_unavailable");
                return ExitCode::FAILURE;
            }
        }
        Err(_) => {
            eprintln!("native_audio_mastering_serialization_failed");
            return ExitCode::FAILURE;
        }
    }
    status
}

#[cfg(test)]
mod owner_record_tests {
    use super::*;
    use std::{cell::Cell, io::Cursor};

    fn installation_bytes(root: &Path) -> Vec<u8> {
        serde_json::to_vec(&json!({
            "pins": {
                "worker_sha256": "1".repeat(64),
                "python_sha256": "2".repeat(64),
                "ffmpeg_sha256": "3".repeat(64),
                "ffprobe_sha256": "4".repeat(64),
                "owner_runtime_profile_sha256": "5".repeat(64),
                "meter_validation_sha256": "6".repeat(64)
            },
            "sealed_media_root": root,
            "source_custody_receipt_sha256": "7".repeat(64)
        }))
        .unwrap()
    }

    #[test]
    fn a_stable_small_or_exact_limit_owner_record_is_read_before_deserialization() {
        let directory = tempfile::tempdir().unwrap();
        let original = installation_bytes(directory.path());
        for fill_to_limit in [false, true] {
            let mut bytes = original.clone();
            if fill_to_limit {
                bytes.resize(MAX_OWNER_INSTALLATION_BYTES, b' ');
            }
            let path = directory.path().join(if fill_to_limit {
                "limit.json"
            } else {
                "small.json"
            });
            fs::write(&path, &bytes).unwrap();
            let observed = read_owner_installation_record(&path).unwrap();
            assert_eq!(observed, bytes);
            let owner: OwnerInstallation = serde_json::from_slice(&observed).unwrap();
            assert_eq!(owner.sealed_media_root.as_path(), directory.path());
            assert_eq!(owner.pins.worker_sha256, "1".repeat(64));
        }
    }

    #[test]
    fn an_initially_oversized_file_is_rejected_before_parse_or_connection_load() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("oversized.json");
        fs::write(&path, vec![b' '; MAX_OWNER_INSTALLATION_BYTES + 1]).unwrap();
        let parsed = Cell::new(false);
        let result = read_owner_installation_record(&path).inspect(|_| parsed.set(true));
        let error = result.unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert!(error.outcome_known);
        assert!(!parsed.get());
        assert_eq!(
            fs::metadata(&path).unwrap().len(),
            MAX_OWNER_INSTALLATION_BYTES as u64 + 1
        );
    }

    struct GrowingReader {
        delivered: usize,
        calls: usize,
    }
    impl Read for GrowingReader {
        fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
            self.calls += 1;
            // Independent finite guard: an unbounded read-to-EOF implementation
            // cannot hang this regression, even though this source never ends.
            assert!(self.calls <= 32, "unbounded growing-reader iteration");
            assert!(!buffer.is_empty());
            let count = buffer.len().min(3);
            buffer[..count].fill(b'x');
            self.delivered += count;
            Ok(count)
        }
    }

    #[test]
    fn a_growing_reader_is_rejected_after_at_most_initial_size_plus_one_byte() {
        let initial = 5;
        let mut reader = GrowingReader {
            delivered: 0,
            calls: 0,
        };
        let parsed = Cell::new(false);
        let error = read_owner_record_payload(&mut reader, initial)
            .inspect(|_| parsed.set(true))
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.outcome_known);
        assert!(!parsed.get());
        assert_eq!(reader.delivered, initial as usize + 1);
        assert!(reader.calls < 32);
    }

    #[test]
    fn oversized_initial_observation_performs_zero_reader_calls() {
        let mut reader = GrowingReader {
            delivered: 0,
            calls: 0,
        };
        let error = read_owner_record_payload(&mut reader, MAX_OWNER_INSTALLATION_BYTES as u64 + 1)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert!(error.outcome_known);
        assert_eq!(reader.calls, 0);
        assert_eq!(reader.delivered, 0);
    }

    #[test]
    fn early_eof_is_rejected_instead_of_parsing_a_truncated_record() {
        let bytes = b"{\"pins\":";
        let mut reader = Cursor::new(bytes);
        let parsed = Cell::new(false);
        let error = read_owner_record_payload(&mut reader, bytes.len() as u64 + 8)
            .inspect(|_| parsed.set(true))
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.outcome_known);
        assert!(!parsed.get());
    }

    #[test]
    fn success_requires_an_actual_eof_observation_at_the_initial_size() {
        struct CountingReader {
            inner: Cursor<Vec<u8>>,
            eof_reads: usize,
        }
        impl Read for CountingReader {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                let count = self.inner.read(buffer)?;
                if count == 0 {
                    self.eof_reads += 1;
                }
                Ok(count)
            }
        }
        let mut reader = CountingReader {
            inner: Cursor::new(b"{}".to_vec()),
            eof_reads: 0,
        };
        assert_eq!(read_owner_record_payload(&mut reader, 2).unwrap(), b"{}");
        assert_eq!(reader.eof_reads, 1);
    }

    #[test]
    fn read_failure_does_not_expose_the_private_path_or_reach_parse() {
        struct FailedReader;
        impl Read for FailedReader {
            fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    "private-owner-location",
                ))
            }
        }
        let parsed = Cell::new(false);
        let error = read_owner_record_payload(&mut FailedReader, 2)
            .inspect(|_| parsed.set(true))
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert!(error.outcome_known);
        assert_eq!(error.message, "Mastering fixture storage operation failed");
        assert!(!parsed.get());
    }

    #[test]
    fn changed_metadata_or_a_nonregular_record_is_rejected_before_parse() {
        let directory = tempfile::tempdir().unwrap();
        assert_eq!(
            read_owner_installation_record(directory.path())
                .unwrap_err()
                .code,
            ErrorCode::InvalidArgument
        );
        let path = directory.path().join("changed.json");
        fs::write(&path, b"{}").unwrap();
        let before = owner_record_stamp(&fs::metadata(&path).unwrap()).unwrap();
        fs::write(&path, b"{\"changed\":true}").unwrap();
        let after = owner_record_stamp(&fs::metadata(&path).unwrap()).unwrap();
        let error = require_same_owner_record(&before, &after).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.outcome_known);
    }
}
