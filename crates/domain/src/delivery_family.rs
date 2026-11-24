//! Explicit, atomic updates to an existing family of delivery profiles.
//! Derivation history is not rewritten; equal values do not prove inheritance.
use crate::{
    AudioCodec, CaptionFormat, DeliverableProfile, DomainError, LockKind, OutputColorSpace,
    OutputContainer, Project, RationalTime, Result, VideoCodec,
};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(
    tag = "field",
    content = "value",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum SharedDeliverySetting {
    BrandProfile(Option<String>),
    FrameRate(RationalTime),
    CaptionFormat(CaptionFormat),
    VideoCodec(VideoCodec),
    AudioCodec(AudioCodec),
    AudioSampleRateHz(u32),
    ColorSpace(OutputColorSpace),
    Container(OutputContainer),
}

impl SharedDeliverySetting {
    fn read(&self, profile: &DeliverableProfile) -> Self {
        match self {
            Self::BrandProfile(_) => Self::BrandProfile(profile.brand_profile.clone()),
            Self::FrameRate(_) => Self::FrameRate(profile.frame_rate),
            Self::CaptionFormat(_) => Self::CaptionFormat(profile.caption_format),
            Self::VideoCodec(_) => Self::VideoCodec(profile.video_codec),
            Self::AudioCodec(_) => Self::AudioCodec(profile.audio_codec),
            Self::AudioSampleRateHz(_) => Self::AudioSampleRateHz(profile.audio_sample_rate_hz),
            Self::ColorSpace(_) => Self::ColorSpace(profile.color_space),
            Self::Container(_) => Self::Container(profile.container),
        }
    }

    fn write(&self, profile: &mut DeliverableProfile) {
        match self {
            Self::BrandProfile(value) => profile.brand_profile.clone_from(value),
            Self::FrameRate(value) => profile.frame_rate = *value,
            Self::CaptionFormat(value) => profile.caption_format = *value,
            Self::VideoCodec(value) => profile.video_codec = *value,
            Self::AudioCodec(value) => profile.audio_codec = *value,
            Self::AudioSampleRateHz(value) => profile.audio_sample_rate_hz = *value,
            Self::ColorSpace(value) => profile.color_space = *value,
            Self::Container(value) => profile.container = *value,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliverySettingTarget {
    pub profile_id: Uuid,
    pub expected: SharedDeliverySetting,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliverySettingPropagation {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub branch_id: Uuid,
    pub expected_revision: u64,
    pub source_profile_id: Uuid,
    pub source: SharedDeliverySetting,
    pub targets: Vec<DeliverySettingTarget>,
}

fn is_descendant(
    profiles: &[DeliverableProfile],
    target: &DeliverableProfile,
    source: Uuid,
) -> bool {
    let mut next = target.parent_profile_id;
    let mut visited = HashSet::from([target.id]);
    while let Some(id) = next {
        if !visited.insert(id) {
            return false;
        }
        if id == source {
            return true;
        }
        next = profiles
            .iter()
            .find(|profile| profile.id == id)
            .and_then(|profile| profile.parent_profile_id);
    }
    false
}

impl Project {
    pub(crate) fn propagate_delivery_setting(
        &mut self,
        proposal: &DeliverySettingPropagation,
    ) -> Result<()> {
        if proposal.project_id != self.id
            || proposal.generation != self.generation
            || proposal.branch_id != self.active_branch
            || proposal.expected_revision != self.revision
            || proposal.targets.is_empty()
            || proposal.targets.len() >= crate::MAX_DELIVERABLE_PROFILES
        {
            return Err(DomainError::Invalid(
                "Delivery family source scope changed or selection is out of bounds".into(),
            ));
        }
        let kinds = [
            LockKind::Content,
            LockKind::Style,
            LockKind::Timing,
            LockKind::Renderer,
        ];
        self.ensure_unlocked(&self.resource_key(), &kinds)?;
        let source = self
            .deliverables
            .iter()
            .find(|profile| profile.id == proposal.source_profile_id)
            .ok_or_else(|| {
                DomainError::NotFound(format!("deliverable:{}", proposal.source_profile_id))
            })?;
        if proposal.source.read(source) != proposal.source {
            return Err(DomainError::Invalid(
                "Saved delivery setting differs from reviewed source".into(),
            ));
        }

        // Resolve every row against the same original project. A late failure
        // cannot publish any earlier target, even to a caller outside Store.
        let mut seen = HashSet::new();
        let mut replacements = Vec::with_capacity(proposal.targets.len());
        for row in &proposal.targets {
            if !seen.insert(row.profile_id) || row.profile_id == source.id {
                return Err(DomainError::Invalid(
                    "Delivery family targets must be distinct descendants".into(),
                ));
            }
            let resource = format!("deliverable:{}", row.profile_id);
            self.ensure_unlocked(&resource, &kinds)?;
            let profile = self
                .deliverables
                .iter()
                .find(|profile| profile.id == row.profile_id)
                .ok_or_else(|| DomainError::NotFound(resource.clone()))?;
            if !is_descendant(&self.deliverables, profile, source.id) {
                return Err(DomainError::Invalid(
                    "Delivery family target is not a descendant of the saved source".into(),
                ));
            }
            // Do not mutate any version that already has a profile-specific
            // review. A separate derived profile is the explicit alternative.
            if self.reviews.iter().any(|review| {
                review.anchor.profile_id == Some(profile.id) || review.anchor.resource == resource
            }) {
                return Err(DomainError::Locked(
                    "Reviewed delivery version: derive a new version instead".into(),
                ));
            }
            if proposal.source.read(profile) != row.expected || row.expected == proposal.source {
                return Err(DomainError::Invalid(
                    "Delivery family target changed or already has the source value".into(),
                ));
            }
            let mut replacement = profile.clone();
            proposal.source.write(&mut replacement);
            replacement.validate()?;
            replacements.push(replacement);
        }
        for replacement in replacements {
            let profile = self
                .deliverables
                .iter_mut()
                .find(|profile| profile.id == replacement.id)
                .ok_or_else(|| DomainError::NotFound(format!("deliverable:{}", replacement.id)))?;
            *profile = replacement;
        }
        for row in &proposal.targets {
            let profile = self
                .deliverables
                .iter()
                .find(|profile| profile.id == row.profile_id)
                .ok_or_else(|| DomainError::NotFound(format!("deliverable:{}", row.profile_id)))?;
            crate::delivery::validate_localized_voice_authority(self, profile)?;
        }
        // The ordinary Project validator and outer change transaction also
        // check recipes, graph dependencies, editorial protections and locks.
        Ok(())
    }
}
