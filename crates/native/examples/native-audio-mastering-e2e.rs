//! Isolated source-profile lane: an existing canonical OriginalMix Project,
//! saved target revision, native MLT gain, physical WAV/PCM transfer and meter.
//! No fixture bus is recreated and no extra mixer/runtime is introduced.
//! Final MP4 mux/readback uses A1's existing composition join, not this lane.
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
    io::Write,
    path::{Path, PathBuf},
    process::ExitCode,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OwnerInstallation {
    pins: AudioMeasurePins,
    /// Must already be the A2-admitted readonly MLT media mount. This config
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
            let connection = ProductionConnection::load(&args[2])?;
            let bytes = fs::read(&args[3]).map_err(|_| io_error())?;
            if bytes.len() > 16 * 1024 {
                return Err(reject("Owner installation record exceeds profile"));
            }
            let owner: OwnerInstallation = serde_json::from_slice(&bytes)?;
            owner.pins.validate()?;
            if !owner.sealed_media_root.is_absolute() {
                return Err(reject("Owner media mount must be absolute"));
            }
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
