//! Read-only source-timeline dependency preflight for editable scenes.
//!
//! This is an exact-rational *invalidation proposal*, not a rendered cache
//! hit, video regeneration, time saving, sound analysis or human approval.
//! Real native frame equivalence must be proved separately before caching.
use crate::{DomainError, Project, RationalTime, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use uuid::Uuid;

pub const RENDER_TAIL_IMPACT_SCHEMA: &str = "motionwright.render-tail-impact/1";
const MAX_SOURCE_FRAMES: u64 = 1_080_000;
const MAX_SCENES: usize = 256;
const MAX_OUTPUTS: usize = 64;
const MAX_AUDIO_TAIL_FRAMES: u32 = 300;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RenderImpactCause {
    SceneVisual,
    SceneAudioTail,
    TimeRipple,
    GlobalMix,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderTailInspectRequest {
    pub expected_generation: Uuid,
    pub expected_revision: u64,
    pub changed_scene_ids: Vec<Uuid>,
    pub cause: RenderImpactCause,
    /// A source owner may declare at most 300 trailing frames. This is NOT
    /// measured audio-tail provenance or a provider-observed tail duration.
    pub tail_frames: u32,
    pub tail_source_asset_id: Option<Uuid>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceFrameWindow {
    pub start: u64,
    pub end: u64,
}
impl SourceFrameWindow {
    fn intersects(self, other: Self) -> bool {
        self.start < other.end && other.start < self.end
    }
    fn overlap(self, other: Self) -> Option<Self> {
        if self.intersects(other) {
            Some(Self {
                start: self.start.max(other.start),
                end: self.end.min(other.end),
            })
        } else {
            None
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProfileRenderTailImpact {
    pub deliverable_id: Uuid,
    pub frame_rate: RationalTime,
    pub is_scoped_selection: bool,
    /// All frame intervals are expressed on the original source timeline.
    /// Profiles selecting an edited subset need an independently checked
    /// cut mapping before these can be called output-frame coordinates.
    pub source_timeline_windows: Vec<SourceFrameWindow>,
    pub affected_scene_ids: Vec<Uuid>,
    pub exceeds_source_duration: bool,
    pub output_frame_projection_verified: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderTailImpactReport {
    pub schema: String,
    pub project_id: Uuid,
    pub project_generation: Uuid,
    pub project_revision: u64,
    pub cause: RenderImpactCause,
    pub requested_scene_ids: Vec<Uuid>,
    pub declared_audio_tail_frames: u32,
    pub profiles: Vec<ProfileRenderTailImpact>,
    pub actually_invalidated_caches: bool,
    pub native_frames_regenerated_or_avoided: Option<u64>,
    pub native_renderer_saved_wallclock_micros: Option<u64>,
    pub technical_acceptance: String,
}

fn denied(message: &str) -> DomainError {
    DomainError::Invalid(format!("render impact preflight: {message}"))
}
fn positive_fps(value: RationalTime) -> Result<(i128, i128)> {
    value.validate().map_err(|_| denied("invalid frame rate"))?;
    if value.num <= 0 || value.den <= 0 || value.num > 240_000 || value.den > 10_000 {
        return Err(denied(
            "frame rate outside the bounded rational output range",
        ));
    }
    let n = i128::from(value.num);
    let d = i128::from(value.den);
    // Cap to an actual 1..120fps range, not just the numerator.
    if n < d || n > d * 120 {
        return Err(denied("frame rate must be between 1 and 120 fps"));
    }
    Ok((n, d))
}
fn checked_mul(a: i128, b: i128) -> Result<i128> {
    a.checked_mul(b)
        .ok_or_else(|| denied("source time arithmetic overflow"))
}
fn checked_add(a: i128, b: i128) -> Result<i128> {
    a.checked_add(b)
        .ok_or_else(|| denied("source time arithmetic overflow"))
}
fn frames_at(time: RationalTime, fps: (i128, i128), ceil: bool) -> Result<u64> {
    time.validate().map_err(|_| denied("source time invalid"))?;
    if time.num < 0 || time.den <= 0 {
        return Err(denied("source timestamps must not be negative"));
    }
    let top = checked_mul(i128::from(time.num), fps.0)?;
    let bottom = checked_mul(i128::from(time.den), fps.1)?;
    let n = if ceil {
        checked_add(top, bottom - 1)?
    } else {
        top
    } / bottom;
    let number = u64::try_from(n).map_err(|_| denied("invalid derived source frame index"))?;
    if number > MAX_SOURCE_FRAMES {
        return Err(denied("source timeline exceeds frame budget"));
    }
    Ok(number)
}
fn frame_end(start: RationalTime, duration: RationalTime, fps: (i128, i128)) -> Result<u64> {
    duration
        .validate()
        .map_err(|_| denied("invalid scene duration"))?;
    if duration.num <= 0 || duration.den <= 0 {
        return Err(denied("scene duration must be positive"));
    }
    // ceil((start + duration) * fps) with checked 128-bit rational math.
    let left = checked_mul(i128::from(start.num), i128::from(duration.den))?;
    let right = checked_mul(i128::from(duration.num), i128::from(start.den))?;
    let time_num = checked_add(left, right)?;
    let time_den = checked_mul(i128::from(start.den), i128::from(duration.den))?;
    let top = checked_mul(time_num, fps.0)?;
    let bottom = checked_mul(time_den, fps.1)?;
    let n = checked_add(top, bottom - 1)? / bottom;
    let number = u64::try_from(n).map_err(|_| denied("invalid end-frame index"))?;
    if number > MAX_SOURCE_FRAMES {
        return Err(denied("source timeline exceeds frame budget"));
    }
    Ok(number)
}
fn union(mut windows: Vec<SourceFrameWindow>) -> Vec<SourceFrameWindow> {
    windows.sort_by_key(|w| (w.start, w.end));
    let mut merged: Vec<SourceFrameWindow> = Vec::new();
    for window in windows {
        if let Some(last) = merged.last_mut()
            && window.start <= last.end
        {
            last.end = last.end.max(window.end);
            continue;
        }
        merged.push(window);
    }
    merged
}
impl Project {
    /// Nonmutating, source-frame-granular dependency preflight. Absence of a
    /// tail-source does not authorize claiming zero audio tails: scene_visual
    /// is explicitly separate from scene_audio_tail. Existing mix changes
    /// always invalidate the complete source timeline.
    pub fn inspect_render_tail_impact(
        &self,
        request: &RenderTailInspectRequest,
    ) -> Result<RenderTailImpactReport> {
        if request.expected_generation != self.generation
            || request.expected_revision != self.revision
        {
            return Err(denied("expected project generation/revision is stale"));
        }
        if self.scenes.is_empty()
            || self.scenes.len() > MAX_SCENES
            || self.deliverables.is_empty()
            || self.deliverables.len() > MAX_OUTPUTS
        {
            return Err(denied("source project scene/output budget invalid"));
        }
        let mut unique = BTreeSet::new();
        for id in &request.changed_scene_ids {
            if !unique.insert(*id) || !self.scenes.iter().any(|scene| scene.id == *id) {
                return Err(denied("unknown or repeated changed-scene identity"));
            }
        }
        let global = request.cause == RenderImpactCause::GlobalMix;
        if global != request.changed_scene_ids.is_empty() || request.changed_scene_ids.len() > 64 {
            return Err(denied("global mix and scene edit scopes must be distinct"));
        }
        if request.cause == RenderImpactCause::SceneAudioTail {
            if request.tail_frames > MAX_AUDIO_TAIL_FRAMES || request.tail_frames == 0 {
                return Err(denied("audio tail must be explicitly bounded and nonzero"));
            }
            let asset = request
                .tail_source_asset_id
                .ok_or_else(|| denied("a declared audio-tail source asset is required"))?;
            if !self.assets.iter().any(|entry| {
                entry.id == asset
                    && entry.media_type.starts_with("audio/")
                    && entry.content_sha256.as_ref().is_some_and(|sha| {
                        sha.len() == 64
                            && sha
                                .bytes()
                                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                    })
            }) {
                return Err(denied("tail source must be an identified audio asset"));
            }
        } else if request.tail_frames != 0 || request.tail_source_asset_id.is_some() {
            return Err(denied(
                "non-audio dependency scope cannot invent tail frames",
            ));
        }
        let mut profiles = Vec::with_capacity(self.deliverables.len());
        for profile in &self.deliverables {
            let fps = positive_fps(profile.frame_rate)?;
            let mut scenes = Vec::with_capacity(self.scenes.len());
            for scene in &self.scenes {
                let start = frames_at(scene.start, fps, false)?;
                let end = frame_end(scene.start, scene.duration, fps)?;
                if end <= start {
                    return Err(denied("scene interval collapses at selected frame rate"));
                }
                scenes.push((scene.id, SourceFrameWindow { start, end }));
            }
            let film_end = scenes
                .iter()
                .map(|(_, w)| w.end)
                .max()
                .ok_or_else(|| denied("project has no scene frame windows"))?;
            let source_ranges = match request.cause {
                RenderImpactCause::GlobalMix => vec![SourceFrameWindow {
                    start: 0,
                    end: film_end,
                }],
                RenderImpactCause::TimeRipple => {
                    let start = scenes
                        .iter()
                        .filter(|(id, _)| unique.contains(id))
                        .map(|(_, window)| window.start)
                        .min()
                        .ok_or_else(|| denied("time ripple requires an actual source scene"))?;
                    vec![SourceFrameWindow {
                        start,
                        end: film_end,
                    }]
                }
                RenderImpactCause::SceneVisual | RenderImpactCause::SceneAudioTail => scenes
                    .iter()
                    .filter(|(id, _)| unique.contains(id))
                    .map(|(_, window)| SourceFrameWindow {
                        start: window.start,
                        end: window.end.saturating_add(u64::from(request.tail_frames)),
                    })
                    .collect::<Vec<_>>(),
            };
            let exceeds = source_ranges.iter().any(|window| window.end > film_end);
            let scoped = !profile.included_scene_ids.is_empty();
            let selected = |id: &Uuid| !scoped || profile.included_scene_ids.contains(id);
            let mut selected_dirty = Vec::new();
            let mut affected_ids = Vec::new();
            for (id, interval) in &scenes {
                if !selected(id) {
                    continue;
                }
                let mut intersects = false;
                for range in &source_ranges {
                    if let Some(overlap) = interval.overlap(*range) {
                        selected_dirty.push(overlap);
                        intersects = true;
                    }
                }
                if intersects {
                    affected_ids.push(*id);
                }
            }
            if global && !scoped {
                selected_dirty = source_ranges;
            }
            profiles.push(ProfileRenderTailImpact {
                deliverable_id: profile.id,
                frame_rate: profile.frame_rate,
                is_scoped_selection: scoped,
                source_timeline_windows: union(selected_dirty),
                affected_scene_ids: affected_ids,
                exceeds_source_duration: exceeds,
                // Even an all-scene profile needs proof of actual native
                // cut-time projection before claiming output-frame equivalence.
                output_frame_projection_verified: false,
            });
        }
        Ok(RenderTailImpactReport {
            schema: RENDER_TAIL_IMPACT_SCHEMA.into(),
            project_id: self.id,
            project_generation: self.generation,
            project_revision: self.revision,
            cause: request.cause,
            requested_scene_ids: request.changed_scene_ids.clone(),
            declared_audio_tail_frames: request.tail_frames,
            profiles,
            actually_invalidated_caches: false,
            native_frames_regenerated_or_avoided: None,
            native_renderer_saved_wallclock_micros: None,
            technical_acceptance: "DEPENDENCY_PREFLIGHT_ONLY_NATIVE_COMPARISON_NOT_RUN".into(),
        })
    }
}
