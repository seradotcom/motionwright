//! Exact original voice conform for approved delivery cuts that change the source clock.
//! A saved digest is editorial intent, not a native effect grant. Re-derive
//! the source and every sample span before using it; never trim implicitly.
use crate::{DeliverableProfile, DomainError, Project, RationalTime, Result};
use sha2::{Digest, Sha256};
use uuid::Uuid;

const MAX_SAMPLES: u64 = 48_000 * 180;
const MAX_SCENES: usize = 512;
fn invalid(message: &str) -> DomainError {
    DomainError::Invalid(message.into())
}
fn exact_samples(time: RationalTime) -> Result<u64> {
    let n = i128::from(time.num) * 48_000;
    let d = i128::from(time.den);
    if n < 0 || d <= 0 || n % d != 0 {
        return Err(invalid("Voice cut boundaries must be exact 48-kHz samples"));
    }
    u64::try_from(n / d)
        .ok()
        .filter(|v| *v <= MAX_SAMPLES)
        .ok_or_else(|| invalid("Voice cut exceeds the bounded original source clock"))
}
pub fn is_original_voice_montage(project: &Project, profile: &DeliverableProfile) -> bool {
    !crate::delivery::editorial_matches_full_cut(&project.scenes)
        || (!profile.included_scene_ids.is_empty()
            && !profile
                .included_scene_ids
                .iter()
                .copied()
                .eq(project.scenes.iter().map(|scene| scene.id)))
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OriginalVoiceCutSpan {
    pub scene_id: Uuid,
    pub source_start: u64,
    pub source_end: u64,
    pub output_start: u64,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OriginalVoiceCut {
    pub source_frames: u64,
    pub output_frames: u64,
    pub spans: Vec<OriginalVoiceCutSpan>,
    pub sha256: String,
}
/// This function derives the reviewable binding from canonical saved inputs;
/// it does not grant any operation or accept a caller-supplied span.
pub fn derive_original_voice_cut(
    project: &Project,
    profile: &DeliverableProfile,
) -> Result<OriginalVoiceCut> {
    if !is_original_voice_montage(project, profile)
        || profile.audio_sample_rate_hz != 48_000
        || project.scenes.is_empty()
        || project.scenes.len() > MAX_SCENES
    {
        return Err(invalid(
            "Voice cut review requires a saved cut that changes the original clock at 48 kHz",
        ));
    }
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|t| Some(t.id) == profile.voice_track_id)
        .ok_or_else(|| invalid("Voice cut requires an explicitly bound original voice take"))?;
    if track.sample_rate_hz != 48_000
        || track.channels != 2
        || track.source_sha256.len() != 64
        || !track
            .source_sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !project.assets.iter().any(|a| {
            a.id == track.asset_id
                && a.content_sha256.as_deref() == Some(track.source_sha256.as_str())
                && matches!(
                    a.media_type.as_str(),
                    "audio/wav" | "audio/x-wav" | "audio/wave"
                )
        })
    {
        return Err(invalid(
            "Voice cut requires an exact imported stereo 48-kHz WAV",
        ));
    }
    let source_frames = exact_samples(track.measured_duration)?;
    let mut full_end = 0;
    for scene in &project.scenes {
        let start = exact_samples(scene.start)?;
        let length = exact_samples(scene.duration)?;
        if start < full_end || length == 0 {
            return Err(invalid(
                "Original voice scenes must have positive, non-overlapping source intervals in project order",
            ));
        }
        full_end = start
            .checked_add(length)
            .ok_or_else(|| invalid("Voice source clock overflow"))?;
    }
    if source_frames != full_end || source_frames < 48_000 {
        return Err(invalid(
            "Original voice must exactly cover the complete original project clock",
        ));
    }
    let mut output_frames = 0u64;
    let mut previous_source_end = 0u64;
    let mut spans = Vec::new();
    for scene in crate::delivery::scenes_for_profile(&project.scenes, profile)? {
        let source_start = exact_samples(scene.start)?;
        let length = exact_samples(scene.duration)?;
        let source_end = source_start + length;
        if source_start < previous_source_end {
            return Err(invalid(
                "Voice cut must preserve approved project scene order",
            ));
        }
        previous_source_end = source_end;
        spans.push(OriginalVoiceCutSpan {
            scene_id: scene.id,
            source_start,
            source_end,
            output_start: output_frames,
        });
        output_frames += length;
    }
    if !(48_000..=MAX_SAMPLES).contains(&output_frames) {
        return Err(invalid("Selected voice cut must last 1 to 180 seconds"));
    }
    // Closed array wire is shared with Studio. UUIDs, lower-case digest and
    // safe integer sample counts have identical JSON bytes in both runtimes.
    let wire = (
        "motionwright.original-voice-cut/1",
        profile.id,
        track.id,
        track.asset_id,
        &track.source_sha256,
        source_frames,
        spans
            .iter()
            .map(|s| (s.scene_id, s.source_start, s.source_end, s.output_start))
            .collect::<Vec<_>>(),
    );
    let bytes = serde_json::to_vec(&wire)
        .map_err(|_| invalid("Voice cut identity could not be encoded"))?;
    Ok(OriginalVoiceCut {
        source_frames,
        output_frames,
        spans,
        sha256: hex::encode(Sha256::digest(bytes)),
    })
}
pub fn resolve_original_voice_cut(
    project: &Project,
    profile: &DeliverableProfile,
    saved: Option<&str>,
) -> Result<Option<OriginalVoiceCut>> {
    if !is_original_voice_montage(project, profile) {
        if saved.is_some() {
            return Err(invalid(
                "Saved voice cut binding is stale; clear or review it",
            ));
        }
        return Ok(None);
    }
    let saved = saved
        .ok_or_else(|| invalid("Selected cut requires an explicit saved voice cut binding"))?;
    let cut = derive_original_voice_cut(project, profile)?;
    if saved != cut.sha256 {
        return Err(invalid(
            "Saved voice cut binding is stale; review exact source ranges again",
        ));
    }
    Ok(Some(cut))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Asset, Change, VoiceTrack};
    fn id(s: &str) -> Uuid {
        Uuid::parse_str(s).unwrap()
    }
    #[test]
    fn voice_cut_binding_matches_studio_wire_and_rejects_stale_order() {
        let mut p = Project::new("Synthetic voice-cut clock").unwrap();
        for n in 0..3 {
            p.apply_change(&Change::AddScene {
                name: format!("Scene {n}"),
                objective: "Synthetic clock".into(),
                duration_seconds: 2,
            })
            .unwrap();
        }
        for (n, scene) in p.scenes.iter_mut().enumerate() {
            scene.id = id(&format!("00000000-0000-4000-8000-00000000010{n}"));
        }
        let track_id = id("00000000-0000-4000-8000-000000000300");
        let asset_id = id("00000000-0000-4000-8000-000000000400");
        p.assets.push(Asset {
            id: asset_id,
            name: "original.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some("a".repeat(64)),
            source_revision: None,
        });
        p.audio.voice_tracks.push(VoiceTrack {
            id: track_id,
            asset_id,
            label: "Original".into(),
            source_sha256: "a".repeat(64),
            sample_rate_hz: 48_000,
            channels: 2,
            measured_duration: RationalTime::new(6, 1).unwrap(),
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
        p.deliverables[0].id = id("00000000-0000-4000-8000-000000000200");
        p.deliverables[0].voice_track_id = Some(track_id);
        p.deliverables[0].audio_sample_rate_hz = 48_000;
        p.deliverables[0].included_scene_ids = vec![p.scenes[0].id, p.scenes[2].id];
        let cut = derive_original_voice_cut(&p, &p.deliverables[0]).unwrap();
        assert_eq!(cut.source_frames, 288_000);
        assert_eq!(cut.output_frames, 192_000);
        assert_eq!(
            cut.sha256,
            "e398be6acc88b7618f6f3bc6130f12fb1020901d48ed7157c675cc77feeee7f8"
        );
        assert!(resolve_original_voice_cut(&p, &p.deliverables[0], None).is_err());
        assert_eq!(
            resolve_original_voice_cut(&p, &p.deliverables[0], Some(&cut.sha256)).unwrap(),
            Some(cut.clone())
        );
        p.deliverables[0].included_scene_ids.reverse();
        assert!(
            derive_original_voice_cut(&p, &p.deliverables[0])
                .unwrap_err()
                .to_string()
                .contains("approved project scene order")
        );
        p.deliverables[0].included_scene_ids = vec![p.scenes[0].id, p.scenes[1].id];
        assert!(resolve_original_voice_cut(&p, &p.deliverables[0], Some(&cut.sha256)).is_err());
    }
}
