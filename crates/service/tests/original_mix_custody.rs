//! Original user WAVs must pass actual canonical content-addressed Storage.
//! This is not an arbitrary filesystem source read and creates no mix master.
use motionwright_domain::{Asset, Change, Project};
use motionwright_service::StudioService;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use uuid::Uuid;

fn pcm16_wav(path: &Path, rate: u32, channels: u16, frames: usize) -> (String, String) {
    let mut pcm = Vec::with_capacity(frames * usize::from(channels) * 2);
    for frame in 0..frames {
        let value = if frame % 2 == 0 { 4100_i16 } else { -4100_i16 };
        for _ in 0..channels {
            pcm.extend_from_slice(&value.to_le_bytes());
        }
    }
    let data_bytes = pcm.len() as u32;
    let mut wav = Vec::with_capacity(pcm.len() + 44);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&channels.to_le_bytes());
    wav.extend_from_slice(&rate.to_le_bytes());
    wav.extend_from_slice(&(rate * u32::from(channels) * 2).to_le_bytes());
    wav.extend_from_slice(&(channels * 2).to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_bytes.to_le_bytes());
    wav.extend_from_slice(&pcm);
    fs::write(path, &wav).unwrap();
    (
        hex::encode(Sha256::digest(wav)),
        hex::encode(Sha256::digest(pcm)),
    )
}
fn add_asset(
    service: &StudioService,
    project: &Project,
    path: &Path,
    label: &str,
    source_sha: &str,
) -> (Project, Uuid) {
    let stored = service.ingest_blob_file(path).unwrap();
    assert_eq!(stored.sha256, source_sha);
    let asset = Asset {
        id: Uuid::new_v4(),
        name: label.into(),
        media_type: "audio/wav".into(),
        content_sha256: Some(stored.sha256),
        source_revision: Some("owner-import".into()),
    };
    let id = asset.id;
    let after = service
        .apply(
            project.id,
            &project.stamp(),
            &format!("original-mix-custody:{}", id.simple()),
            &Change::AddAsset { asset },
        )
        .unwrap()
        .project;
    (after, id)
}
#[test]
fn content_addressed_extensionless_storage_admits_exact_original_wav_pcm() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("canonical.sqlite3")).unwrap();
    let mut project = service
        .create_project("Original three-bus source ownership")
        .unwrap();
    for label in ["voice", "music", "sfx"] {
        let filename = dir.path().join(format!("{label}.wav"));
        let (container_sha, pcm_sha) = pcm16_wav(&filename, 48000, 2, 48000);
        let (updated, id) = add_asset(&service, &project, &filename, label, &container_sha);
        project = updated;
        fs::remove_file(&filename).unwrap();
        let sample = service
            .decoded_stem_for_original_mix(project.id, project.revision, id)
            .unwrap();
        assert_eq!(sample.original_asset_sha256, container_sha);
        assert_eq!(sample.decoded_pcm_sha256, pcm_sha);
        assert_eq!(sample.sample_frames, 48000);
        assert_eq!(sample.interleaved_s16le.len(), 48000 * 4);
        assert_ne!(sample.decoded_pcm_sha256, sample.original_asset_sha256);
    }
}
#[test]
fn stale_revision_foreign_asset_and_unsupported_source_format_are_refused() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("canonical.sqlite3")).unwrap();
    let project = service
        .create_project("Real project mixing revision contract")
        .unwrap();
    let file = dir.path().join("owned.wav");
    let (sha, _) = pcm16_wav(&file, 48000, 2, 48000);
    let (next, id) = add_asset(&service, &project, &file, "Owner original", &sha);
    assert!(
        service
            .decoded_stem_for_original_mix(next.id, project.revision, id)
            .is_err()
    );
    assert!(
        service
            .decoded_stem_for_original_mix(next.id, next.revision, Uuid::new_v4())
            .is_err()
    );
    let other = service
        .create_project("A separate owner's project")
        .unwrap();
    assert!(
        service
            .decoded_stem_for_original_mix(other.id, other.revision, id)
            .is_err()
    );
    // Asset attachment alone grants no right to decode a different source format.
    let new_asset = Asset {
        id: Uuid::new_v4(),
        name: "Unverified other codec".into(),
        media_type: "application/octet-stream".into(),
        content_sha256: Some(sha),
        source_revision: Some("owner-import".into()),
    };
    let another_id = new_asset.id;
    let updated = service
        .apply(
            next.id,
            &next.stamp(),
            "original-mix-custody:invalid-media-type",
            &Change::AddAsset { asset: new_asset },
        )
        .unwrap()
        .project;
    assert!(
        service
            .decoded_stem_for_original_mix(updated.id, updated.revision, another_id)
            .is_err()
    );
}
