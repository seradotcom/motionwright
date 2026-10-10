//! Disposable original PCM mixing fixture. Reads only three actual source
//! PCM payloads supplied by a first-party CI owner; no TTS/ASR or streaming
//! rights, no external process grants, and no automatic audio mastering.
use motionwright_creative_library::{
    OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow, write_mix_wav,
};
use motionwright_domain as domain;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::Path,
};
use uuid::Uuid;
fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}
fn sha(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}
fn time(num: i64, den: i64) -> domain::RationalTime {
    assert!(num >= 0 && den > 0);
    let (mut a, mut b) = (num, den);
    while b != 0 {
        (a, b) = (b, a % b);
    }
    let divisor = a;
    domain::RationalTime {
        num: num / divisor,
        den: den / divisor,
    }
}
fn original_input(file: &str) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let path = Path::new(file);
    if !path.is_absolute()
        || path.symlink_metadata()?.file_type().is_symlink()
        || !path.is_file()
        || path.metadata()?.len() > 1_920_000
    {
        return Err("Mix fixture source must be a bounded owner-generated regular PCM file".into());
    }
    let data = fs::read(path)?;
    if data.len() != 115200 * 4 {
        return Err("Mix fixture requires exactly 115200 stereo frames".into());
    }
    Ok(data)
}
fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    if args.len() != 6 {
        return Err("Usage: native_mix_fixture voice.raw music.raw sfx.raw en|es|de output.wav receipt.json".into());
    }
    let [voice, music, sfx] = [
        original_input(&args[0])?,
        original_input(&args[1])?,
        original_input(&args[2])?,
    ];
    let locale = &args[3];
    if !["en", "es", "de"].contains(&locale.as_str()) {
        return Err(
            "Owner profile only supports individually authored en/es/de source locale".into(),
        );
    }
    let n = 115_200_u64;
    let source_hashes = [sha(&voice), sha(&music), sha(&sfx)];
    let mut project = domain::Project::new("First party original synthetic mix study")?;
    project.id = id(100);
    project.generation = id(101);
    project.revision = 9;
    for (i, name) in [
        "synthetic voice proxy NOT SPEECH",
        "original music tone",
        "original source focus SFX",
    ]
    .iter()
    .enumerate()
    {
        project.assets.push(domain::Asset {
            id: id(200 + i as u128),
            name: (*name).into(),
            media_type: "audio/x-raw".into(),
            content_sha256: Some(source_hashes[i].clone()),
            source_revision: Some("original-fixture-audio".into()),
        });
    }
    project.audio.voice_tracks.push(domain::VoiceTrack {
        id: id(301),
        asset_id: id(200),
        label: "Synthetic original *not spoken* voice proxy".into(),
        sample_rate_hz: 48000,
        channels: 2,
        measured_duration: time(12, 5),
        source_sha256: source_hashes[0].clone(),
        loudness_lufs: None,
        true_peak_dbfs: None,
    });
    project.audio.active_voice_track_id = Some(id(301));
    for (index, (start, end)) in [(28800_u64, 52800_u64), (72000, 91200)]
        .into_iter()
        .enumerate()
    {
        project.audio.transcript.push(domain::TranscriptSegment {
            id: id(330 + index as u128),
            voice_track_id: id(301),
            start: time(start as i64, 48000),
            end: time(end as i64, 48000),
            text: format!(
                "Synthetic proxy timed interval {}, NOT actual speech",
                index + 1
            ),
            speaker: None,
            alignment: domain::AlignmentEvidence::Manual,
        });
    }
    project.deliverables[0].language = locale.clone();
    project.deliverables[0].audio_sample_rate_hz = 48000;
    project.deliverables[0].voice_track_id = Some(id(301));
    let stem = |index: usize| PcmStemReceipt {
        asset_id: id(200 + index as u128),
        decoded_pcm_sha256: source_hashes[index].clone(),
        sample_frames: n,
    };
    let plan = OriginalMixPlan {
        schema: "motionwright.original-audio-mix/1".into(),
        project_id: project.id,
        generation: project.generation,
        project_revision: project.revision,
        deliverable_profile_id: project.deliverables[0].id,
        language: locale.clone(),
        sample_rate: 48000,
        channels: 2,
        sample_frames: n,
        measured_voice: stem(0),
        original_music: stem(1),
        original_sfx: stem(2),
        voice_gain_db: f64::from(project.audio.mix.voice_gain_db),
        music_gain_db: f64::from(project.audio.mix.music_gain_db),
        sfx_gain_db: -8.0,
        duck_attenuation_db: 14.0,
        duck_attack_samples: 2400,
        duck_release_samples: 4800,
        voice_windows: vec![
            VoiceActivityWindow {
                start_sample: 28800,
                end_exclusive: 52800,
            },
            VoiceActivityWindow {
                start_sample: 72000,
                end_exclusive: 91200,
            },
        ],
        sample_peak_guard_dbfs: -1.0,
        source_classification: "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED".into(),
        owner_mixing_approval_authenticated: false,
        is_mastered: false,
    };
    let output = Path::new(&args[4]);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?;
    let evidence = write_mix_wav(&plan, &project, &voice, &music, &sfx, &mut file)?;
    file.flush()?;
    file.sync_all()?;
    let rendered = fs::read(output)?;
    let receipt = serde_json::json!({
        "schema":"motionwright.original-voice-music-sfx-e2e/1",
        "source_plan":plan,
        "source_pcm_sha256":{"voice":source_hashes[0],"music":source_hashes[1],"sfx":source_hashes[2]},
        "original_source_classification":"SYNTHETIC_TONE_VOICE_PROXY_NOT_SPEECH_NOT_APPROVED",
        "output_wav_sha256":sha(&rendered),
        "mix_evidence":evidence,
        "independent_media_loudness":"NOT_MEASURED_HERE",
        "human_listening_approval":false,
        "actual_localized_spoken_voice":false,
        "release_approved":false
    });
    let target = Path::new(&args[5]);
    let mut written = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(target)?;
    written.write_all(&serde_json::to_vec_pretty(&receipt)?)?;
    written.sync_all()?;
    println!(
        "{}",
        serde_json::json!({"language":locale,"source_mix_rendered":"ORIGINAL_UNMASTERED_WAV","frames":n})
    );
    Ok(())
}
fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if let Err(error) = run(&args) {
        eprintln!("{error}");
        std::process::exit(2);
    }
}
