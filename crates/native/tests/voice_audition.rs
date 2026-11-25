//! Original measured take review over real StudioService and CAS.
//! Synthetic PCM fixtures are not actual speech or an approval.
use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_native::mlt_av_audio::read_measured_voice_audition;
use motionwright_service::{StudioService, VoiceImportMetadata};
use std::{fs, path::Path};
use uuid::Uuid;

fn wav(rate: u32, channels: u16, bits: u16, level: i16) -> Vec<u8> {
    let align = channels * bits / 8;
    let data_size = rate * u32::from(align);
    let mut bytes = b"RIFF".to_vec();
    bytes.extend_from_slice(&(36 + data_size).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&channels.to_le_bytes());
    bytes.extend_from_slice(&rate.to_le_bytes());
    bytes.extend_from_slice(&(rate * u32::from(align)).to_le_bytes());
    bytes.extend_from_slice(&align.to_le_bytes());
    bytes.extend_from_slice(&bits.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_size.to_le_bytes());
    for frame in 0..rate {
        for channel in 0..channels {
            let sample = level.wrapping_add((frame % 19) as i16 + channel as i16);
            if bits == 16 {
                bytes.extend_from_slice(&sample.to_le_bytes());
            } else {
                bytes.extend_from_slice(&((i32::from(sample)) << 8).to_le_bytes()[..3]);
            }
        }
    }
    bytes
}
fn import(service: &StudioService, p: &Project, dir: &Path, label: &str, bytes: &[u8]) -> Project {
    let path = dir.join(format!("{label}.wav"));
    fs::write(&path, bytes).unwrap();
    service
        .import_voice_file(
            p.id,
            &p.stamp(),
            label,
            &path,
            VoiceImportMetadata {
                name: format!("{label}.wav"),
                media_type: "audio/wav".into(),
                label: label.into(),
            },
        )
        .unwrap()
        .project
}
#[test]
fn original_take_audition_reads_two_exact_wavs_without_selecting_or_mutating_takes() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("takes.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let p = service
        .create_project("Synthetic original take review")
        .unwrap();
    let mono = wav(8_000, 1, 16, 1000);
    let stereo = wav(48_000, 2, 16, -2000);
    let p = import(&service, &p, dir.path(), "mono", &mono);
    let p = import(&service, &p, dir.path(), "stereo", &stereo);
    let a = p
        .audio
        .voice_tracks
        .iter()
        .find(|t| t.label == "mono")
        .unwrap();
    let b = p
        .audio
        .voice_tracks
        .iter()
        .find(|t| t.label == "stereo")
        .unwrap();
    let first_id = a.id;
    let second_id = b.id;
    let first_sha = a.source_sha256.clone();
    let second_sha = b.source_sha256.clone();
    let p = service
        .apply(
            p.id,
            &p.stamp(),
            "choose-first",
            &Change::SetActiveVoiceTrack { track_id: first_id },
        )
        .unwrap()
        .project;
    let stamp = p.stamp();
    assert_eq!(
        read_measured_voice_audition(&service, p.id, &stamp, first_id, &first_sha).unwrap(),
        mono
    );
    assert_eq!(
        read_measured_voice_audition(&service, p.id, &stamp, second_id, &second_sha).unwrap(),
        stereo
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert_eq!(p.audio.active_voice_track_id, Some(first_id));
    assert!(read_measured_voice_audition(&service, p.id, &stamp, second_id, &first_sha).is_err());
    assert!(
        read_measured_voice_audition(&service, p.id, &stamp, Uuid::new_v4(), &second_sha).is_err()
    );
    let mut stale: RevisionStamp = stamp.clone();
    stale.revision += 1;
    assert!(read_measured_voice_audition(&service, p.id, &stale, second_id, &second_sha).is_err());
    fs::remove_file(dir.path().join("mono.wav")).unwrap();
    fs::remove_file(dir.path().join("stereo.wav")).unwrap();
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    assert_eq!(
        read_measured_voice_audition(&reopened, p.id, &stamp, second_id, &second_sha).unwrap(),
        stereo
    );
    assert_eq!(
        reopened.project(p.id).unwrap().audio.active_voice_track_id,
        Some(first_id)
    );
}
#[test]
fn source_audition_does_not_silently_convert_a_measured_non_pcm16_wav() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("format.sqlite3")).unwrap();
    let p = service
        .create_project("Synthetic unsupported take representation")
        .unwrap();
    let source = wav(48_000, 2, 24, 1000);
    let p = import(&service, &p, dir.path(), "pcm24", &source);
    let track = &p.audio.voice_tracks[0];
    assert!(
        read_measured_voice_audition(&service, p.id, &p.stamp(), track.id, &track.source_sha256)
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), p);
}
