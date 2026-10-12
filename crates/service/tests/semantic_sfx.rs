//! Durable owner-local SQLite source gate for semantic SFX imported WAVs.
use motionwright_domain::{
    Asset, Change, Project, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent,
};
use motionwright_service::StudioService;
use std::{fs, path::Path};
use uuid::Uuid;
fn original_wav(frames: usize) -> Vec<u8> {
    let samples = frames * 1600;
    let size = (samples * 4) as u32;
    let mut bytes = Vec::with_capacity(44 + size as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(size + 36).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48000_u32.to_le_bytes());
    bytes.extend_from_slice(&192000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&size.to_le_bytes());
    for i in 0..samples {
        let amplitude: i16 = if (i / 60) % 2 == 0 { 4000 } else { -4000 };
        bytes.extend_from_slice(&amplitude.to_le_bytes());
        bytes.extend_from_slice(&(-amplitude).to_le_bytes());
    }
    bytes
}
fn fixture(service: &StudioService, folder: &Path) -> (Project, SemanticSfxCue, Vec<u8>) {
    let mut project = service
        .create_project("Semantic original sound source")
        .unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "sfx-scene",
            &Change::AddScene {
                name: "Owned sound".into(),
                objective: "Imported original stereo WAV".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let pcm = original_wav(30);
    let file = folder.join("authored-original.wav");
    fs::write(&file, &pcm).unwrap();
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "owned-sfx-asset",
            &file,
            "Generated original stereo technical fixture".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let asset = &project.assets[0];
    let cue = SemanticSfxCue {
        schema: SFX_CUE_SCHEMA.into(),
        id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        event: SfxEvent::Confirmation,
        anchor: SfxAnchor::SceneStart,
        offset_frames: 10,
        source_asset_id: asset.id,
        source_sha256: asset.content_sha256.clone().unwrap(),
        source_frames: 30,
        gain_milli_db: -12000,
        attack_frames: 2,
        release_frames: 8,
        original_source_attestation: "I generated this original test WAV from numeric samples"
            .into(),
    };
    (project, cue, pcm)
}
#[test]
fn source_wav_requires_real_import_exact_owner_revision_and_replays_durable_receipts() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("sfx-cas.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let (original, cue, bytes) = fixture(&service, tmp.path());
    let request = Change::UpsertSemanticSfx { cue: cue.clone() };
    let stamp = original.stamp();
    let recorded = service
        .apply(original.id, &stamp, "bind-sfx", &request)
        .unwrap();
    assert!(!recorded.replayed);
    assert_eq!(recorded.project.audio.sfx_cues, vec![cue.clone()]);
    let replay = service
        .apply(original.id, &stamp, "bind-sfx", &request)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(recorded.project, replay.project);
    assert!(
        service
            .verified_semantic_sfx_wav(original.id, &stamp, cue.source_asset_id, &cue.source_sha256)
            .is_err()
    );
    assert_eq!(
        service
            .verified_semantic_sfx_wav(
                original.id,
                &recorded.project.stamp(),
                cue.source_asset_id,
                &cue.source_sha256
            )
            .unwrap(),
        bytes
    );
    let mut forged = cue.clone();
    forged.source_sha256 = "b".repeat(64);
    assert!(
        service
            .verified_semantic_sfx_wav(
                original.id,
                &recorded.project.stamp(),
                cue.source_asset_id,
                &forged.source_sha256
            )
            .is_err()
    );
    let other = service.create_project("Independent other project").unwrap();
    assert!(
        service
            .verified_semantic_sfx_wav(
                other.id,
                &other.stamp(),
                cue.source_asset_id,
                &cue.source_sha256
            )
            .is_err()
    );
    fs::write(
        tmp.path().join("authored-original.wav"),
        b"tampered original filesystem source",
    )
    .unwrap();
    assert_eq!(
        service
            .verified_semantic_sfx_wav(
                original.id,
                &recorded.project.stamp(),
                cue.source_asset_id,
                &cue.source_sha256
            )
            .unwrap(),
        bytes
    );
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    assert_eq!(reopened.project(original.id).unwrap(), recorded.project);
    assert_eq!(
        reopened
            .verified_semantic_sfx_wav(
                original.id,
                &recorded.project.stamp(),
                cue.source_asset_id,
                &cue.source_sha256
            )
            .unwrap(),
        bytes
    );
}
#[test]
fn stale_or_unowned_sfx_does_not_mutate_project_journal() {
    let tmp = tempfile::tempdir().unwrap();
    let service = StudioService::open(tmp.path().join("bad-sfx.sqlite3")).unwrap();
    let (project, cue, _) = fixture(&service, tmp.path());
    let mut no_source = cue.clone();
    no_source.source_asset_id = Uuid::new_v4();
    assert!(
        service
            .apply(
                project.id,
                &project.stamp(),
                "unowned-audio",
                &Change::UpsertSemanticSfx { cue: no_source }
            )
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    let entered = service
        .apply(
            project.id,
            &project.stamp(),
            "legitimate-audio",
            &Change::UpsertSemanticSfx { cue: cue.clone() },
        )
        .unwrap()
        .project;
    let denied = service.apply(
        project.id,
        &project.stamp(),
        "stale-audio",
        &Change::RemoveSemanticSfx { cue_id: cue.id },
    );
    assert!(denied.is_err());
    assert_eq!(service.project(project.id).unwrap(), entered);
    let deleted = service
        .apply(
            project.id,
            &entered.stamp(),
            "explicit-remove",
            &Change::RemoveSemanticSfx { cue_id: cue.id },
        )
        .unwrap()
        .project;
    assert!(deleted.audio.sfx_cues.is_empty());
    assert!(
        service
            .apply(
                project.id,
                &entered.stamp(),
                "stale-second",
                &Change::UpsertSemanticSfx { cue }
            )
            .is_err()
    );
}
