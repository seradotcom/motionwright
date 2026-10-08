use crate::{AlignmentEvidence, DeliverableProfile, DomainError, Project, RationalTime, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
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

pub const fn default_frame_rate() -> RationalTime {
    RationalTime { num: 30, den: 1 }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum FramingStrategy {
    #[default]
    Replan,
    Crop,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OutputColorSpace {
    #[default]
    Rec709,
    DisplayP3,
    Rec2020,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OutputContainer {
    #[default]
    Mp4,
    Mov,
    Webm,
    Mkv,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VariantDependencyFingerprint {
    pub profile_id: Uuid,
    pub project_generation: Uuid,
    pub project_revision: u64,
    pub frame_inputs_sha256: String,
    pub caption_inputs_sha256: String,
    pub audio_inputs_sha256: String,
    pub master_inputs_sha256: String,
    pub profile_sha256: String,
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
        if self.frame_rate.validate().is_err()
            || self.frame_rate.num <= 0
            || i128::from(self.frame_rate.num) > i128::from(self.frame_rate.den) * 240
        {
            return Err(DomainError::Invalid(
                "deliverable frame rate is outside supported bounds".into(),
            ));
        }
        if self.parent_profile_id == Some(self.id) {
            return Err(DomainError::Invalid(
                "deliverable profile cannot derive from itself".into(),
            ));
        }
        if self.parent_profile_id.is_some() != self.source_revision.is_some() {
            return Err(DomainError::Invalid(
                "derived deliverable profiles require both parent and source revision".into(),
            ));
        }
        if self.parent_profile_id.is_some() && self.adaptation_notes.is_empty() {
            return Err(DomainError::Invalid(
                "derived deliverable profile requires at least one adaptation note".into(),
            ));
        }
        if self.framing_strategy == FramingStrategy::Crop && !self.crop_approved {
            return Err(DomainError::Invalid(
                "crop framing requires explicit approval".into(),
            ));
        }
        if self.text_overrides.len() > 4_096
            || self.included_scene_ids.len() > 4_096
            || self.protected_scene_ids.len() > 4_096
            || self.adaptation_notes.len() > 64
        {
            return Err(DomainError::Invalid(
                "deliverable variant metadata collection is too large".into(),
            ));
        }
        if self
            .text_overrides
            .values()
            .any(|value| value.trim().is_empty() || value.len() > 16_384)
        {
            return Err(DomainError::Invalid(
                "localized text override is out of bounds".into(),
            ));
        }
        if self
            .adaptation_notes
            .iter()
            .any(|value| value.trim().is_empty() || value.len() > 1_000)
        {
            return Err(DomainError::Invalid(
                "deliverable adaptation note is out of bounds".into(),
            ));
        }
        let included: HashSet<_> = self.included_scene_ids.iter().copied().collect();
        if included.len() != self.included_scene_ids.len() {
            return Err(DomainError::Invalid(
                "deliverable cut contains duplicate scene ids".into(),
            ));
        }
        let protected: HashSet<_> = self.protected_scene_ids.iter().copied().collect();
        if protected.len() != self.protected_scene_ids.len() {
            return Err(DomainError::Invalid(
                "deliverable protected-scene list contains duplicates".into(),
            ));
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

pub(crate) fn validate_deliverable_context(project: &Project) -> Result<()> {
    let profile_ids: HashSet<_> = project
        .deliverables
        .iter()
        .map(|profile| profile.id)
        .collect();
    let scene_ids: HashSet<_> = project.scenes.iter().map(|scene| scene.id).collect();
    let voice_ids: HashSet<_> = project
        .audio
        .voice_tracks
        .iter()
        .map(|track| track.id)
        .collect();
    let all_text_node_ids: HashSet<_> = project
        .scenes
        .iter()
        .flat_map(|scene| scene.nodes.iter())
        .filter(|node| node.kind == "text")
        .map(|node| node.id)
        .collect();

    for profile in &project.deliverables {
        if profile
            .source_revision
            .is_some_and(|revision| revision > project.revision)
        {
            return Err(DomainError::Invalid(
                "deliverable source revision is newer than project state".into(),
            ));
        }
        if let Some(parent_id) = profile.parent_profile_id {
            let parent = project
                .deliverables
                .iter()
                .find(|candidate| candidate.id == parent_id)
                .ok_or_else(|| {
                    DomainError::Invalid("deliverable parent profile does not exist".into())
                })?;
            if profile.language != parent.language {
                if parent.voice_track_id.is_some()
                    && profile.voice_track_id == parent.voice_track_id
                {
                    return Err(DomainError::Invalid(
                        "localized deliverable cannot reuse the parent language voice track".into(),
                    ));
                }
                let localized_scene_ids: HashSet<_> = if profile.included_scene_ids.is_empty() {
                    scene_ids.clone()
                } else {
                    profile.included_scene_ids.iter().copied().collect()
                };
                let localized_text_node_ids = project
                    .scenes
                    .iter()
                    .filter(|scene| localized_scene_ids.contains(&scene.id))
                    .flat_map(|scene| scene.nodes.iter())
                    .filter(|node| node.kind == "text")
                    .map(|node| node.id)
                    .collect::<Vec<_>>();
                if localized_text_node_ids
                    .iter()
                    .any(|node_id| !profile.text_overrides.contains_key(node_id))
                {
                    return Err(DomainError::Invalid(
                        "localized deliverable requires explicit text for every canvas text node in its cut"
                            .into(),
                    ));
                }
                if profile.timing_locked {
                    match (parent.voice_track_id, profile.voice_track_id) {
                        (Some(parent_track_id), Some(localized_track_id)) => {
                            let parent_track = project
                                .audio
                                .voice_tracks
                                .iter()
                                .find(|track| track.id == parent_track_id)
                                .ok_or_else(|| {
                                    DomainError::Invalid(
                                        "deliverable parent voice track does not exist".into(),
                                    )
                                })?;
                            let localized_track = project
                                .audio
                                .voice_tracks
                                .iter()
                                .find(|track| track.id == localized_track_id)
                                .ok_or_else(|| {
                                    DomainError::Invalid(
                                        "deliverable voice track does not exist".into(),
                                    )
                                })?;
                            if parent_track.measured_duration != localized_track.measured_duration {
                                return Err(DomainError::Invalid(
                                    "localized deliverable cannot keep source timing when measured voice duration changes; re-time the variant or disable timing lock".into(),
                                ));
                            }
                        }
                        (Some(_), None) => {
                            return Err(DomainError::Invalid(
                                "localized deliverable with locked timing requires an explicit locale voice track".into(),
                            ));
                        }
                        _ => {}
                    }
                }
            }
        }
        if profile
            .voice_track_id
            .is_some_and(|track_id| !voice_ids.contains(&track_id))
        {
            return Err(DomainError::Invalid(
                "deliverable voice track does not exist".into(),
            ));
        }
        if profile
            .text_overrides
            .keys()
            .any(|node_id| !all_text_node_ids.contains(node_id))
        {
            return Err(DomainError::Invalid(
                "deliverable text override targets an unknown text node".into(),
            ));
        }

        let mut last_position = None;
        for scene_id in &profile.included_scene_ids {
            let position = project
                .scenes
                .iter()
                .position(|scene| scene.id == *scene_id)
                .ok_or_else(|| {
                    DomainError::Invalid("deliverable cut references an unknown scene".into())
                })?;
            if last_position.is_some_and(|last| position <= last) {
                return Err(DomainError::Invalid(
                    "deliverable cut must preserve approved project scene order".into(),
                ));
            }
            last_position = Some(position);
        }
        for scene_id in &profile.protected_scene_ids {
            if !scene_ids.contains(scene_id) {
                return Err(DomainError::Invalid(
                    "deliverable protected scene does not exist".into(),
                ));
            }
            if !profile.included_scene_ids.is_empty()
                && !profile.included_scene_ids.contains(scene_id)
            {
                return Err(DomainError::Invalid(
                    "deliverable cut cannot omit a protected narrative scene".into(),
                ));
            }
        }

        let mut seen = HashSet::new();
        let mut cursor = profile.parent_profile_id;
        while let Some(parent_id) = cursor {
            if !profile_ids.contains(&parent_id) || !seen.insert(parent_id) {
                return Err(DomainError::Invalid(
                    "deliverable profile lineage contains a cycle".into(),
                ));
            }
            cursor = project
                .deliverables
                .iter()
                .find(|candidate| candidate.id == parent_id)
                .and_then(|candidate| candidate.parent_profile_id);
        }
    }
    Ok(())
}

fn rational_cmp(left: RationalTime, right: RationalTime) -> std::cmp::Ordering {
    let left_scaled = i128::from(left.num) * i128::from(right.den);
    let right_scaled = i128::from(right.num) * i128::from(left.den);
    left_scaled.cmp(&right_scaled)
}

pub fn transcript_for_profile(
    project: &Project,
    profile: &DeliverableProfile,
) -> Result<Vec<crate::TranscriptSegment>> {
    if project.audio.transcript.is_empty() {
        return Err(DomainError::Invalid(
            "caption export requires transcript segments".into(),
        ));
    }
    let mut segments = if let Some(track_id) = profile.voice_track_id {
        let selected = project
            .audio
            .transcript
            .iter()
            .filter(|segment| segment.voice_track_id == track_id)
            .cloned()
            .collect::<Vec<_>>();
        if selected.is_empty() {
            return Err(DomainError::Invalid(
                "deliverable voice track has no transcript timing evidence".into(),
            ));
        }
        selected
    } else {
        let tracks = project
            .audio
            .transcript
            .iter()
            .map(|segment| segment.voice_track_id)
            .collect::<HashSet<_>>();
        if tracks.len() > 1 {
            return Err(DomainError::Invalid(
                "caption export is ambiguous; select the deliverable voice track".into(),
            ));
        }
        project.audio.transcript.clone()
    };
    if segments
        .iter()
        .any(|segment| matches!(segment.alignment, AlignmentEvidence::Unknown))
    {
        return Err(DomainError::Invalid(
            "caption export requires known transcript timing evidence".into(),
        ));
    }
    segments.sort_by(|left, right| {
        rational_cmp(left.start, right.start)
            .then(rational_cmp(left.end, right.end))
            .then(left.id.cmp(&right.id))
    });

    if profile.included_scene_ids.is_empty() {
        return Ok(segments);
    }

    let mut output = Vec::new();
    let mut cursor = RationalTime::ZERO;
    for scene_id in &profile.included_scene_ids {
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == *scene_id)
            .ok_or_else(|| {
                DomainError::Invalid("deliverable cut references an unknown scene".into())
            })?;
        let scene_end = scene.start.checked_add(scene.duration).map_err(|_| {
            DomainError::Invalid("scene end overflowed during caption reflow".into())
        })?;

        for segment in &segments {
            let overlaps = rational_cmp(segment.start, scene_end).is_lt()
                && rational_cmp(segment.end, scene.start).is_gt();
            let contained = !rational_cmp(segment.start, scene.start).is_lt()
                && !rational_cmp(segment.end, scene_end).is_gt();
            if overlaps && !contained {
                return Err(DomainError::Invalid(
                    "caption segment crosses a selected scene boundary; re-align transcript timing before exporting this cut".into(),
                ));
            }
            if contained {
                let local_start = segment.start.checked_sub(scene.start).map_err(|_| {
                    DomainError::Invalid("caption start underflowed during cut reflow".into())
                })?;
                let local_end = segment.end.checked_sub(scene.start).map_err(|_| {
                    DomainError::Invalid("caption end underflowed during cut reflow".into())
                })?;
                let mut rebased = segment.clone();
                rebased.start = cursor.checked_add(local_start).map_err(|_| {
                    DomainError::Invalid("caption start overflowed during cut reflow".into())
                })?;
                rebased.end = cursor.checked_add(local_end).map_err(|_| {
                    DomainError::Invalid("caption end overflowed during cut reflow".into())
                })?;
                output.push(rebased);
            }
        }
        cursor = cursor
            .checked_add(scene.duration)
            .map_err(|_| DomainError::Invalid("deliverable cut duration overflowed".into()))?;
    }
    Ok(output)
}

fn digest_json(value: serde_json::Value) -> Result<String> {
    let bytes = serde_json::to_vec(&value)
        .map_err(|_| DomainError::Invalid("variant dependency serialization failed".into()))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

pub fn variant_dependency_fingerprint(
    project: &Project,
    profile_id: Uuid,
) -> Result<VariantDependencyFingerprint> {
    project.validate()?;
    let profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.id == profile_id)
        .ok_or_else(|| DomainError::NotFound(format!("deliverable:{profile_id}")))?;

    let scenes = if profile.included_scene_ids.is_empty() {
        project.scenes.iter().collect::<Vec<_>>()
    } else {
        profile
            .included_scene_ids
            .iter()
            .map(|scene_id| {
                project
                    .scenes
                    .iter()
                    .find(|scene| scene.id == *scene_id)
                    .expect("deliverable context validation ensures scene existence")
            })
            .collect::<Vec<_>>()
    };
    let caption_segments = if project.audio.transcript.is_empty() {
        vec![]
    } else {
        transcript_for_profile(project, profile)?
    };
    let frame_node_ids = scenes
        .iter()
        .flat_map(|scene| scene.nodes.iter())
        .map(|node| node.id)
        .collect::<HashSet<_>>();
    let frame_text_overrides = profile
        .text_overrides
        .iter()
        .filter(|(node_id, _)| frame_node_ids.contains(node_id))
        .map(|(node_id, text)| (*node_id, text.clone()))
        .collect::<std::collections::BTreeMap<_, _>>();
    let selected_voice = profile.voice_track_id.and_then(|track_id| {
        project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == track_id)
    });

    let frame_inputs_sha256 = digest_json(serde_json::json!({
        "width": profile.width,
        "height": profile.height,
        "frame_rate": profile.frame_rate,
        "color_space": profile.color_space,
        "framing_strategy": profile.framing_strategy,
        "brand_profile": &profile.brand_profile,
        "scenes": scenes,
        "visual_language": &project.visual_language,
        "text_overrides": frame_text_overrides,
        "burn_in_captions": profile.burn_in_captions,
        "burn_in_transcript": if profile.burn_in_captions {
            serde_json::to_value(&caption_segments).unwrap_or(serde_json::Value::Null)
        } else {
            serde_json::Value::Null
        }
    }))?;
    let caption_inputs_sha256 = digest_json(serde_json::json!({
        "language": &profile.language,
        "caption_format": profile.caption_format,
        "voice_track_id": profile.voice_track_id,
        "segments": caption_segments,
    }))?;
    let audio_inputs_sha256 = digest_json(serde_json::json!({
        "audio_codec": profile.audio_codec,
        "sample_rate_hz": profile.audio_sample_rate_hz,
        "voice_track": selected_voice,
        "mix": &project.audio.mix,
    }))?;
    let master_inputs_sha256 = digest_json(serde_json::json!({
        "frame_inputs_sha256": &frame_inputs_sha256,
        "audio_inputs_sha256": &audio_inputs_sha256,
        "video_codec": profile.video_codec,
        "audio_codec": profile.audio_codec,
        "sample_rate_hz": profile.audio_sample_rate_hz,
        "frame_rate": profile.frame_rate,
        "color_space": profile.color_space,
        "container": profile.container,
    }))?;
    let profile_sha256 =
        digest_json(serde_json::to_value(profile).map_err(|_| {
            DomainError::Invalid("deliverable profile serialization failed".into())
        })?)?;

    Ok(VariantDependencyFingerprint {
        profile_id,
        project_generation: project.generation,
        project_revision: project.revision,
        frame_inputs_sha256,
        caption_inputs_sha256,
        audio_inputs_sha256,
        master_inputs_sha256,
        profile_sha256,
    })
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
    let segments = transcript_for_profile(project, profile)?;

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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OtioInterchange {
    pub scene_count: usize,
    pub loss_report: Vec<String>,
    pub body: String,
}

pub fn otio_interchange(project: &Project) -> Result<OtioInterchange> {
    project.validate()?;
    const RATE: f64 = 24.0;

    let mut loss_report = vec![
        "Scene clips use MissingReference because Motionwright scenes do not imply a flattened media file.".to_string(),
        "Renderer-specific behavior is namespaced Motionwright metadata, not portable OTIO execution semantics.".to_string(),
    ];
    if project.scenes.iter().any(|scene| !scene.nodes.is_empty()) {
        loss_report.push(
            "Canvas node geometry, hierarchy, semantic relations and node property locks are not represented as standard OTIO edits."
                .into(),
        );
    }
    if !project.markers.is_empty() {
        loss_report.push(
            "Project markers are not exported in this conservative interchange profile.".into(),
        );
    }
    if !project.audio.transcript.is_empty() || !project.audio.cues.is_empty() {
        loss_report.push(
            "Transcript, alignment evidence, audio cues and mix intent remain in Motionwright; they are not flattened into OTIO audio tracks."
                .into(),
        );
    }
    if !project.reviews.is_empty() || !project.locks.is_empty() {
        loss_report.push(
            "Review state and project locks remain Motionwright-only workflow metadata.".into(),
        );
    }
    if project.branches.len() > 1 || !project.merges.is_empty() {
        loss_report
            .push("Branch and merge history are not encoded into the linear OTIO cut.".into());
    }
    if !project.assets.is_empty() {
        loss_report.push(
            "Project assets are not guessed onto scenes; explicit scene-to-media bindings are required before an ExternalReference can be truthful."
                .into(),
        );
    }

    let clips = project
        .scenes
        .iter()
        .map(|scene| {
            let duration_frames =
                (scene.duration.num as f64 / scene.duration.den as f64) * RATE;
            let renderer = serde_json::to_value(&scene.renderer)
                .unwrap_or_else(|_| serde_json::Value::String("unknown".into()));
            serde_json::json!({
                "OTIO_SCHEMA": "Clip.1",
                "effects": [],
                "markers": [],
                "enabled": true,
                "media_reference": {
                    "OTIO_SCHEMA": "MissingReference.1",
                    "available_range": serde_json::Value::Null,
                    "metadata": {},
                    "name": serde_json::Value::Null
                },
                "metadata": {
                    "motionwright": {
                        "scene_id": scene.id.to_string(),
                        "renderer": renderer,
                        "status": serde_json::to_value(&scene.status).unwrap_or(serde_json::Value::Null),
                        "objective": scene.objective,
                    }
                },
                "name": scene.name,
                "source_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "duration": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "rate": RATE,
                        "value": duration_frames
                    },
                    "start_time": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "rate": RATE,
                        "value": 0.0
                    }
                }
            })
        })
        .collect::<Vec<_>>();

    let timeline = serde_json::json!({
        "OTIO_SCHEMA": "Timeline.1",
        "metadata": {
            "motionwright": {
                "project_id": project.id.to_string(),
                "generation": project.generation.to_string(),
                "revision": project.revision,
                "export_profile": "conservative-cut-v1",
                "loss_report": loss_report.clone(),
            }
        },
        "name": project.title,
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "children": [{
                "OTIO_SCHEMA": "Track.1",
                "children": clips,
                "effects": [],
                "kind": "Video",
                "markers": [],
                "enabled": true,
                "metadata": {},
                "name": "Motionwright scenes",
                "source_range": serde_json::Value::Null
            }],
            "effects": [],
            "markers": [],
            "enabled": true,
            "metadata": {},
            "name": "tracks",
            "source_range": serde_json::Value::Null
        }
    });
    let body = serde_json::to_string_pretty(&timeline)
        .map_err(|_| DomainError::Invalid("OTIO serialization failed".into()))?;

    Ok(OtioInterchange {
        scene_count: project.scenes.len(),
        loss_report,
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

    #[test]
    fn otio_export_is_linear_and_carries_an_explicit_loss_report() {
        let mut project = Project::new("OTIO fixture").unwrap();
        project
            .apply_change(&crate::Change::AddScene {
                name: "Editable scene".into(),
                objective: "Preserve timing without claiming renderer portability.".into(),
                duration_seconds: 7,
            })
            .unwrap();
        let export = otio_interchange(&project).unwrap();
        assert_eq!(export.scene_count, 1);
        assert!(export.body.contains("\"OTIO_SCHEMA\": \"Timeline.1\""));
        assert!(export.body.contains("\"OTIO_SCHEMA\": \"Clip.1\""));
        assert!(
            export
                .body
                .contains("\"OTIO_SCHEMA\": \"MissingReference.1\"")
        );
        assert!(export.body.contains("\"motionwright\""));
        assert!(!export.loss_report.is_empty());
    }

    #[test]
    fn crop_variants_require_explicit_approval() {
        let mut project = Project::new("Crop approval").unwrap();
        let mut profile = project.deliverables[0].clone();
        profile.framing_strategy = FramingStrategy::Crop;
        profile.crop_approved = false;
        let error = project
            .apply_change(&crate::Change::UpsertDeliverable { profile })
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("crop framing requires explicit approval")
        );
    }

    #[test]
    fn localized_variant_cannot_reuse_parent_voice_timing() {
        let mut project = fixture(AlignmentEvidence::Manual);
        let voice_id = project.audio.voice_tracks[0].id;
        let parent_id = project.deliverables[0].id;
        let mut parent = project.deliverables[0].clone();
        parent.voice_track_id = Some(voice_id);
        project
            .apply_change(&crate::Change::UpsertDeliverable { profile: parent })
            .unwrap();

        let mut localized = project.deliverables[0].clone();
        localized.id = Uuid::now_v7();
        localized.name = "Spanish".into();
        localized.language = "es-MX".into();
        localized.parent_profile_id = Some(parent_id);
        localized.source_revision = Some(project.revision);
        localized.adaptation_notes = vec!["Spanish voice and text adaptation".into()];
        let error = project
            .apply_change(&crate::Change::UpsertDeliverable { profile: localized })
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("cannot reuse the parent language voice track")
        );
    }

    #[test]
    fn caption_export_is_bound_to_the_variant_voice_track() {
        let mut project = fixture(AlignmentEvidence::Manual);
        let first_track = project.audio.voice_tracks[0].id;
        let second_asset = Uuid::now_v7();
        let second_track = Uuid::now_v7();
        let second_sha = "ef".repeat(32);
        project.assets.push(Asset {
            id: second_asset,
            name: "voice-es.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(second_sha.clone()),
            source_revision: Some("fixture-es".into()),
        });
        project.audio.voice_tracks.push(VoiceTrack {
            id: second_track,
            asset_id: second_asset,
            label: "Spanish".into(),
            sample_rate_hz: 48_000,
            channels: 2,
            measured_duration: RationalTime { num: 5, den: 1 },
            source_sha256: second_sha,
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
        project.audio.transcript.push(TranscriptSegment {
            id: Uuid::now_v7(),
            voice_track_id: second_track,
            start: RationalTime { num: 1, den: 1 },
            end: RationalTime { num: 3, den: 1 },
            text: "La edición semántica sigue siendo editable.".into(),
            speaker: Some("Narrator".into()),
            alignment: AlignmentEvidence::Manual,
        });
        let profile_id = project.deliverables[0].id;
        project.deliverables[0].voice_track_id = Some(second_track);
        project.validate().unwrap();

        let sidecar = caption_sidecar(&project, profile_id).unwrap();
        assert_eq!(sidecar.cue_count, 1);
        assert!(sidecar.body.contains("La edición semántica"));
        assert!(!sidecar.body.contains("Semantic edits"));
        assert_ne!(first_track, second_track);
    }

    #[test]
    fn protected_narrative_scene_cannot_be_dropped_from_a_cut() {
        let mut project = Project::new("Protected cut").unwrap();
        for name in ["Premise", "Payoff"] {
            project
                .apply_change(&crate::Change::AddScene {
                    name: name.into(),
                    objective: name.into(),
                    duration_seconds: 2,
                })
                .unwrap();
        }
        let premise = project.scenes[0].id;
        let payoff = project.scenes[1].id;
        let mut cut = project.deliverables[0].clone();
        cut.id = Uuid::now_v7();
        cut.name = "Short cut".into();
        cut.parent_profile_id = Some(project.deliverables[0].id);
        cut.source_revision = Some(project.revision);
        cut.cut_label = Some("30s".into());
        cut.included_scene_ids = vec![payoff];
        cut.protected_scene_ids = vec![premise];
        cut.adaptation_notes = vec!["Shorten while preserving the premise.".into()];
        let error = project
            .apply_change(&crate::Change::UpsertDeliverable { profile: cut })
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("cannot omit a protected narrative scene")
        );
    }

    #[test]
    fn sidecar_only_caption_edits_do_not_invalidate_frames() {
        let mut project = fixture(AlignmentEvidence::Manual);
        let profile_id = project.deliverables[0].id;
        let before = variant_dependency_fingerprint(&project, profile_id).unwrap();

        let mut segment = project.audio.transcript[0].clone();
        segment.text = "Caption wording changed without burn-in.".into();
        project
            .apply_change(&crate::Change::UpsertTranscriptSegment { segment })
            .unwrap();
        let sidecar_only = variant_dependency_fingerprint(&project, profile_id).unwrap();
        assert_eq!(before.frame_inputs_sha256, sidecar_only.frame_inputs_sha256);
        assert_ne!(
            before.caption_inputs_sha256,
            sidecar_only.caption_inputs_sha256
        );

        let mut profile = project.deliverables[0].clone();
        profile.burn_in_captions = true;
        project
            .apply_change(&crate::Change::UpsertDeliverable { profile })
            .unwrap();
        let burned_before = variant_dependency_fingerprint(&project, profile_id).unwrap();

        let mut segment = project.audio.transcript[0].clone();
        segment.text = "Burned caption wording changed.".into();
        project
            .apply_change(&crate::Change::UpsertTranscriptSegment { segment })
            .unwrap();
        let burned_after = variant_dependency_fingerprint(&project, profile_id).unwrap();
        assert_ne!(
            burned_before.frame_inputs_sha256,
            burned_after.frame_inputs_sha256
        );
    }

    #[test]
    fn locked_localized_timing_rejects_changed_measured_voice_duration() {
        let mut project = fixture(AlignmentEvidence::Manual);
        let parent_id = project.deliverables[0].id;
        let parent_voice_id = project.audio.voice_tracks[0].id;
        let mut parent = project.deliverables[0].clone();
        parent.voice_track_id = Some(parent_voice_id);
        project
            .apply_change(&crate::Change::UpsertDeliverable { profile: parent })
            .unwrap();

        let localized_asset_id = Uuid::now_v7();
        project.assets.push(Asset {
            id: localized_asset_id,
            name: "voice-es.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some("ef".repeat(32)),
            source_revision: Some("fixture-es".into()),
        });
        let localized_voice_id = Uuid::now_v7();
        project.audio.voice_tracks.push(VoiceTrack {
            id: localized_voice_id,
            asset_id: localized_asset_id,
            label: "Spanish voice".into(),
            sample_rate_hz: 48_000,
            channels: 2,
            measured_duration: RationalTime { num: 6, den: 1 },
            source_sha256: "ef".repeat(32),
            loudness_lufs: None,
            true_peak_dbfs: None,
        });

        let mut localized = project.deliverables[0].clone();
        localized.id = Uuid::now_v7();
        localized.name = "Spanish locked".into();
        localized.language = "es-MX".into();
        localized.parent_profile_id = Some(parent_id);
        localized.source_revision = Some(project.revision);
        localized.voice_track_id = Some(localized_voice_id);
        localized.timing_locked = true;
        localized.adaptation_notes = vec!["Preserve timing while localizing voice.".into()];

        let error = project
            .apply_change(&crate::Change::UpsertDeliverable { profile: localized })
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("measured voice duration changes")
        );
    }

    #[test]
    fn cut_caption_sidecar_rebases_selected_scene_timing() {
        let mut project = fixture(AlignmentEvidence::Manual);
        project.audio.transcript.clear();
        for name in ["Opening", "Proof"] {
            project
                .apply_change(&crate::Change::AddScene {
                    name: name.into(),
                    objective: name.into(),
                    duration_seconds: 3,
                })
                .unwrap();
        }
        let voice_id = project.audio.voice_tracks[0].id;
        project
            .apply_change(&crate::Change::UpsertTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::now_v7(),
                    voice_track_id: voice_id,
                    start: RationalTime { num: 1, den: 2 },
                    end: RationalTime { num: 2, den: 1 },
                    text: "Opening line".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            })
            .unwrap();
        project
            .apply_change(&crate::Change::UpsertTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::now_v7(),
                    voice_track_id: voice_id,
                    start: RationalTime { num: 7, den: 2 },
                    end: RationalTime { num: 5, den: 1 },
                    text: "Proof line".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            })
            .unwrap();

        let profile_id = project.deliverables[0].id;
        let mut profile = project.deliverables[0].clone();
        profile.voice_track_id = Some(voice_id);
        profile.included_scene_ids = vec![project.scenes[1].id];
        project
            .apply_change(&crate::Change::UpsertDeliverable { profile })
            .unwrap();

        let sidecar = caption_sidecar(&project, profile_id).unwrap();
        assert_eq!(sidecar.cue_count, 1);
        assert!(!sidecar.body.contains("Opening line"));
        assert!(sidecar.body.contains("Proof line"));
        assert!(sidecar.body.contains("00:00:00.500 --> 00:00:02.000"));
    }

    #[test]
    fn mastering_settings_invalidate_master_without_invalidating_source_frames() {
        let mut project = fixture(AlignmentEvidence::Manual);
        let profile_id = project.deliverables[0].id;
        let before = variant_dependency_fingerprint(&project, profile_id).unwrap();

        let mut profile = project.deliverables[0].clone();
        profile.container = OutputContainer::Mov;
        project
            .apply_change(&crate::Change::UpsertDeliverable { profile })
            .unwrap();

        let after = variant_dependency_fingerprint(&project, profile_id).unwrap();
        assert_eq!(before.frame_inputs_sha256, after.frame_inputs_sha256);
        assert_eq!(before.caption_inputs_sha256, after.caption_inputs_sha256);
        assert_eq!(before.audio_inputs_sha256, after.audio_inputs_sha256);
        assert_ne!(before.master_inputs_sha256, after.master_inputs_sha256);
        assert_ne!(before.profile_sha256, after.profile_sha256);
    }

    #[test]
    fn derived_profile_records_parent_revision_and_adaptation() {
        let mut project = Project::new("Variant history").unwrap();
        let parent_id = project.deliverables[0].id;
        let mut derived = project.deliverables[0].clone();
        derived.id = Uuid::now_v7();
        derived.name = "Vertical derivative".into();
        derived.width = 1080;
        derived.height = 1920;
        derived.parent_profile_id = Some(parent_id);
        derived.source_revision = Some(project.revision);
        derived.adaptation_notes = vec!["Replan composition for portrait framing.".into()];
        project
            .apply_change(&crate::Change::UpsertDeliverable {
                profile: derived.clone(),
            })
            .unwrap();

        let stored = project
            .deliverables
            .iter()
            .find(|candidate| candidate.id == derived.id)
            .unwrap();
        assert_eq!(stored.parent_profile_id, Some(parent_id));
        assert_eq!(stored.source_revision, Some(0));
        assert_eq!(
            stored.adaptation_notes,
            vec!["Replan composition for portrait framing."]
        );
        assert!(
            project
                .deliverables
                .iter()
                .any(|profile| profile.id == parent_id)
        );
    }
}
