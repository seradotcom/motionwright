use crate::{AlignmentEvidence, DeliverableProfile, DomainError, Project, RationalTime, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use uuid::Uuid;

pub const MAX_DELIVERABLE_PROFILES: usize = 128;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum CaptionFormat {
    #[default]
    WebVtt,
    #[serde(rename = "srt")]
    SubRip,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum VideoCodec {
    #[default]
    H264,
    Hevc,
    #[serde(rename = "prores_422_hq")]
    ProRes422Hq,
    Vp9,
    Av1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum AudioCodec {
    #[default]
    Aac,
    PcmS16Le,
    Opus,
}

pub const fn default_audio_sample_rate_hz() -> u32 {
    48_000
}

impl DeliverableProfile {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.name.trim().is_empty() || self.name.len() > 160 {
            return Err(DomainError::Invalid(
                "deliverable profile name is out of bounds".into(),
            ));
        }
        if self.width == 0 || self.height == 0 || self.width > 16_384 || self.height > 16_384 {
            return Err(DomainError::Invalid(
                "deliverable dimensions are out of bounds".into(),
            ));
        }
        if self.language.trim().is_empty() || self.language.len() > 64 {
            return Err(DomainError::Invalid(
                "deliverable language is out of bounds".into(),
            ));
        }
        if !matches!(self.audio_sample_rate_hz, 44_100 | 48_000 | 96_000) {
            return Err(DomainError::Invalid(
                "deliverable audio sample rate is unsupported".into(),
            ));
        }
        for (label, value) in [
            ("brand profile", self.brand_profile.as_deref()),
            ("cut label", self.cut_label.as_deref()),
        ] {
            if value.is_some_and(|value| value.trim().is_empty() || value.len() > 256) {
                return Err(DomainError::Invalid(format!(
                    "deliverable {label} is out of bounds"
                )));
            }
        }
        Ok(())
    }
}

pub(crate) fn validate_deliverables(profiles: &[DeliverableProfile]) -> Result<()> {
    if profiles.is_empty() || profiles.len() > MAX_DELIVERABLE_PROFILES {
        return Err(DomainError::Invalid(
            "deliverable profile collection is out of bounds".into(),
        ));
    }
    let mut ids = HashSet::new();
    let mut names = HashSet::new();
    for profile in profiles {
        profile.validate()?;
        if !ids.insert(profile.id) {
            return Err(DomainError::Invalid(
                "duplicate deliverable profile id".into(),
            ));
        }
        if !names.insert(profile.name.trim().to_lowercase()) {
            return Err(DomainError::Invalid(
                "duplicate deliverable profile name".into(),
            ));
        }
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptionSidecar {
    pub profile_id: Uuid,
    pub format: CaptionFormat,
    pub cue_count: usize,
    pub body: String,
}

pub fn caption_sidecar(project: &Project, profile_id: Uuid) -> Result<CaptionSidecar> {
    project.validate()?;
    let profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.id == profile_id)
        .ok_or_else(|| DomainError::NotFound(format!("deliverable:{profile_id}")))?;
    if !profile.captions {
        return Err(DomainError::Invalid(
            "captions are disabled for this deliverable profile".into(),
        ));
    }
    if project.audio.transcript.is_empty() {
        return Err(DomainError::Invalid(
            "caption export requires transcript segments".into(),
        ));
    }
    if project
        .audio
        .transcript
        .iter()
        .any(|segment| matches!(segment.alignment, AlignmentEvidence::Unknown))
    {
        return Err(DomainError::Invalid(
            "caption export requires known transcript timing evidence".into(),
        ));
    }

    let mut segments = project.audio.transcript.iter().collect::<Vec<_>>();
    segments.sort_by(|left, right| {
        let left_start = i128::from(left.start.num) * i128::from(right.start.den);
        let right_start = i128::from(right.start.num) * i128::from(left.start.den);
        let left_end = i128::from(left.end.num) * i128::from(right.end.den);
        let right_end = i128::from(right.end.num) * i128::from(left.end.den);
        left_start
            .cmp(&right_start)
            .then(left_end.cmp(&right_end))
            .then(left.id.cmp(&right.id))
    });

    let mut body = String::new();
    if profile.caption_format == CaptionFormat::WebVtt {
        body.push_str("WEBVTT\n\n");
    }
    for (index, segment) in segments.iter().enumerate() {
        let start_ms = rational_milliseconds(segment.start, false)?;
        let mut end_ms = rational_milliseconds(segment.end, true)?;
        if end_ms <= start_ms {
            end_ms = start_ms + 1;
        }
        if profile.caption_format == CaptionFormat::SubRip {
            body.push_str(&(index + 1).to_string());
            body.push('\n');
        }
        body.push_str(&format_timestamp(
            start_ms,
            profile.caption_format == CaptionFormat::SubRip,
        ));
        body.push_str(" --> ");
        body.push_str(&format_timestamp(
            end_ms,
            profile.caption_format == CaptionFormat::SubRip,
        ));
        body.push('\n');
        let text = segment.text.replace(['\0', '\r'], "").trim().to_owned();
        body.push_str(&text);
        body.push_str("\n\n");
    }

    Ok(CaptionSidecar {
        profile_id,
        format: profile.caption_format,
        cue_count: segments.len(),
        body,
    })
}

fn rational_milliseconds(value: RationalTime, ceil: bool) -> Result<i128> {
    if value.num < 0 || value.den <= 0 {
        return Err(DomainError::Invalid(
            "caption timestamp is outside the non-negative time domain".into(),
        ));
    }
    let numerator = i128::from(value.num)
        .checked_mul(1_000)
        .ok_or_else(|| DomainError::Invalid("caption timestamp overflow".into()))?;
    let denominator = i128::from(value.den);
    let millis = if ceil {
        numerator
            .checked_add(denominator - 1)
            .ok_or_else(|| DomainError::Invalid("caption timestamp overflow".into()))?
            / denominator
    } else {
        numerator / denominator
    };
    Ok(millis)
}

fn format_timestamp(milliseconds: i128, comma: bool) -> String {
    let hours = milliseconds / 3_600_000;
    let minutes = (milliseconds / 60_000) % 60;
    let seconds = (milliseconds / 1_000) % 60;
    let millis = milliseconds % 1_000;
    let separator = if comma { ',' } else { '.' };
    format!("{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Asset, TranscriptSegment, VoiceTrack};
    use uuid::Uuid;

    fn fixture(alignment: AlignmentEvidence) -> Project {
        let mut project = Project::new("Delivery fixture").unwrap();
        let asset_id = Uuid::now_v7();
        let sha = "ab".repeat(32);
        project.assets.push(Asset {
            id: asset_id,
            name: "voice.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(sha.clone()),
            source_revision: Some("fixture".into()),
        });
        let track_id = Uuid::now_v7();
        project.audio.voice_tracks.push(VoiceTrack {
            id: track_id,
            asset_id,
            label: "Voice".into(),
            sample_rate_hz: 48_000,
            channels: 2,
            measured_duration: RationalTime { num: 5, den: 1 },
            source_sha256: sha,
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
        project.audio.transcript.push(TranscriptSegment {
            id: Uuid::now_v7(),
            voice_track_id: track_id,
            start: RationalTime { num: 1, den: 2 },
            end: RationalTime { num: 9, den: 4 },
            text: "Semantic edits remain editable.".into(),
            speaker: Some("Narrator".into()),
            alignment,
        });
        project
    }

    #[test]
    fn webvtt_uses_exact_rational_timestamps() {
        let project = fixture(AlignmentEvidence::Manual);
        let profile = project.deliverables[0].id;
        let sidecar = caption_sidecar(&project, profile).unwrap();
        assert_eq!(sidecar.cue_count, 1);
        assert_eq!(sidecar.format, CaptionFormat::WebVtt);
        assert!(sidecar.body.starts_with("WEBVTT\n\n"));
        assert!(sidecar.body.contains("00:00:00.500 --> 00:00:02.250"));
    }

    #[test]
    fn subrip_is_portable_and_numbered() {
        let mut project = fixture(AlignmentEvidence::Measured {
            engine: "fixture-aligner".into(),
            source_sha256: "cd".repeat(32),
            confidence_millis: Some(990),
        });
        project.deliverables[0].caption_format = CaptionFormat::SubRip;
        let sidecar = caption_sidecar(&project, project.deliverables[0].id).unwrap();
        assert!(
            sidecar
                .body
                .starts_with("1\n00:00:00,500 --> 00:00:02,250\n")
        );
    }

    #[test]
    fn unknown_alignment_never_becomes_caption_evidence() {
        let project = fixture(AlignmentEvidence::Unknown);
        let error = caption_sidecar(&project, project.deliverables[0].id).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("known transcript timing evidence")
        );
    }
}
