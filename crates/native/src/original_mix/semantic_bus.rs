//! A derived SFX bus is source lineage, never a new imported Asset or VoiceTrack.
//! Imported-only serialization remains the original PcmStemReceipt map. A derived
//! receipt has private fields and cannot be reconstructed from caller JSON.
use super::*;
use crate::sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx_with_budget};
use motionwright_domain::{ScheduledSfx, SemanticSfxCue};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(untagged)]
pub enum OriginalSfxSource {
    Imported(PcmStemReceipt),
    SemanticBus(Box<SemanticSfxBusReceipt>),
}

impl From<PcmStemReceipt> for OriginalSfxSource {
    fn from(receipt: PcmStemReceipt) -> Self {
        Self::Imported(receipt)
    }
}

impl<'de> Deserialize<'de> for OriginalSfxSource {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        // PcmStemReceipt denies unknown fields. In particular a serialized
        // SemanticBus cannot use this compatibility path to mint authority.
        PcmStemReceipt::deserialize(deserializer).map(Self::Imported)
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SemanticSfxBusReceipt {
    kind: &'static str,
    project_id: Uuid,
    generation: Uuid,
    project_revision: u64,
    deliverable_profile_id: Uuid,
    selected_scene_ids: Vec<Uuid>,
    source_cut_sha256: String,
    audio_inputs_sha256: String,
    imported_base: Option<PcmStemReceipt>,
    cues: Vec<SemanticSfxCue>,
    placements: Vec<ScheduledSfx>,
    sources: Vec<PcmStemReceipt>,
    sample_frames: u64,
    semantic_wav_sha256: String,
    decoded_pcm_sha256: String,
}

impl OriginalSfxSource {
    pub fn imported_asset_id(&self) -> Option<Uuid> {
        match self {
            Self::Imported(source) => Some(source.asset_id),
            Self::SemanticBus(bus) => bus.imported_base.as_ref().map(|source| source.asset_id),
        }
    }

    pub fn semantic_bus(&self) -> Option<&SemanticSfxBusReceipt> {
        match self {
            Self::Imported(_) => None,
            Self::SemanticBus(bus) => Some(bus.as_ref()),
        }
    }

    pub fn sample_frames(&self) -> u64 {
        match self {
            Self::Imported(source) => source.sample_frames,
            Self::SemanticBus(bus) => bus.sample_frames,
        }
    }

    pub fn decoded_pcm_sha256(&self) -> &str {
        match self {
            Self::Imported(source) => &source.decoded_pcm_sha256,
            Self::SemanticBus(bus) => &bus.decoded_pcm_sha256,
        }
    }

    pub(super) fn plan_schema(&self) -> &'static str {
        match self {
            Self::Imported(_) => "motionwright.original-audio-mix/1",
            Self::SemanticBus(_) => "motionwright.original-audio-mix/2",
        }
    }

    pub(super) fn validate_source(
        &self,
        project: &Project,
        profile_id: Uuid,
        samples: u64,
    ) -> Result<()> {
        let placements = selected_placements(project, profile_id)?;
        match self {
            Self::Imported(source) => {
                check(
                    placements.is_empty(),
                    "An imported SFX WAV cannot replace semantic cues consumed by the selected cut",
                )?;
                check_stem(source, project, samples)
            }
            Self::SemanticBus(bus) => {
                let (selected_scene_ids, source_cut_sha256) =
                    selected_cut_identity(project, profile_id)?;
                let fingerprint = variant_dependency_fingerprint(project, profile_id)
                    .map_err(|error| mix_unsupported(error.to_string()))?;
                check_current(
                    bus.kind == "motionwright.semantic-sfx-bus/1"
                        && bus.project_id == project.id
                        && bus.generation == project.generation
                        && bus.project_revision == project.revision
                        && bus.deliverable_profile_id == profile_id
                        && bus.sample_frames == samples
                        && !placements.is_empty()
                        && bus.placements == placements
                        && bus.cues == selected_cues(project, &placements)
                        && bus.selected_scene_ids == selected_scene_ids
                        && bus.source_cut_sha256 == source_cut_sha256
                        && bus.audio_inputs_sha256 == fingerprint.audio_inputs_sha256
                        && valid_sha(&bus.semantic_wav_sha256)
                        && valid_sha(&bus.decoded_pcm_sha256),
                    "Derived SFX source, schedule, cut, recipe or revision changed",
                )?;
                if let Some(base) = &bus.imported_base {
                    check_stem(base, project, samples)?;
                }
                let expected: BTreeSet<_> =
                    bus.cues.iter().map(|cue| cue.source_asset_id).collect();
                let actual: BTreeSet<_> =
                    bus.sources.iter().map(|source| source.asset_id).collect();
                check_current(
                    expected == actual && actual.len() == bus.sources.len(),
                    "Derived SFX source inventory changed",
                )?;
                for source in &bus.sources {
                    check_stem(source, project, source.sample_frames)?;
                    for cue in bus
                        .cues
                        .iter()
                        .filter(|cue| cue.source_asset_id == source.asset_id)
                    {
                        check_current(
                            source.source_asset_sha256 == cue.source_sha256
                                && source.sample_frames == u64::from(cue.source_frames) * 1_600,
                            "Derived SFX cue no longer binds its exact imported WAV and PCM clock",
                        )?;
                    }
                }
                Ok(())
            }
        }
    }

    pub(super) fn verify_pcm(&self, pcm: &[u8], max_pcm_bytes: usize) -> Result<()> {
        check_current(
            pcm.len() <= max_pcm_bytes
                && pcm.len() as u64 == self.sample_frames() * 4
                && hex::encode(Sha256::digest(pcm)) == self.decoded_pcm_sha256(),
            "SFX bus PCM bytes differ from their exact prepared source lineage",
        )
    }
}

fn selected_placements(project: &Project, profile_id: Uuid) -> Result<Vec<ScheduledSfx>> {
    project
        .validated_sfx_schedule_for_profile(profile_id)
        .map_err(|error| mix_unsupported(error.to_string()))
}

fn selected_cues(project: &Project, placements: &[ScheduledSfx]) -> Vec<SemanticSfxCue> {
    // The source project order remains canonical; placements themselves retain
    // the existing domain scheduler's exact cut order and offsets.
    project
        .audio
        .sfx_cues
        .iter()
        .filter(|cue| {
            placements
                .iter()
                .any(|placement| placement.cue_id == cue.id)
        })
        .cloned()
        .collect()
}

fn selected_cut_identity(project: &Project, profile_id: Uuid) -> Result<(Vec<Uuid>, String)> {
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == profile_id)
        .ok_or_else(|| mix_unsupported("Missing SFX delivery profile"))?;
    // This is an identity projection only. Source placement and duration are
    // always admitted by the existing domain scheduler, not computed here.
    let ids = if profile.included_scene_ids.is_empty() {
        project
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>()
    } else {
        profile.included_scene_ids.clone()
    };
    let cut = ids
        .iter()
        .map(|id| {
            project
                .scenes
                .iter()
                .find(|scene| scene.id == *id)
                .map(|scene| (scene.id, scene.start, scene.duration))
                .ok_or_else(|| mix_unsupported("Selected SFX scene is absent"))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok((ids, canonical_digest(&cut)?))
}

/// The optional imported base is already decoded by the existing service under
/// its explicit budget and full RevisionStamp. Semantic files use the canonical
/// CAS read API; the same existing mixer schedules and renders them.
pub(super) fn prepare_sfx_source(
    service: &StudioService,
    project: &Project,
    expected: &RevisionStamp,
    profile_id: Uuid,
    base: Option<(PcmStemReceipt, Vec<u8>)>,
    sample_frames: u64,
    budget: OriginalMixDecodeBudget,
) -> Result<(OriginalSfxSource, Vec<u8>)> {
    let placements = selected_placements(project, profile_id)?;
    if placements.is_empty() {
        let (receipt, pcm) = base.ok_or_else(|| mix_unsupported(
            "Saved mix needs an imported SFX base or explicitly authored cues in this cut; no invented silent source",
        ))?;
        check_stem(&receipt, project, sample_frames)?;
        return Ok((receipt.into(), pcm));
    }
    let total_frames = project
        .sfx_cut_frames(profile_id)
        .map_err(|error| mix_unsupported(error.to_string()))?;
    check(
        total_frames.checked_mul(1_600) == Some(sample_frames),
        "Semantic SFX and source voice must have the exact same selected-cut sample clock",
    )?;
    let cues = selected_cues(project, &placements);
    let mut seen = BTreeSet::new();
    let mut verified = Vec::new();
    for cue in &cues {
        if seen.insert(cue.source_asset_id) {
            let bytes = service
                .verified_semantic_sfx_wav(
                    project.id,
                    expected,
                    cue.source_asset_id,
                    &cue.source_sha256,
                )
                .map_err(|_| {
                    mix_stale("Selected semantic SFX WAV failed current project/CAS verification")
                })?;
            verified.push(VerifiedSfxWav {
                asset_id: cue.source_asset_id,
                source_sha256: cue.source_sha256.clone(),
                bytes,
            });
        }
    }
    let mixed = mix_source_bound_semantic_sfx_with_budget(
        project,
        profile_id,
        &verified,
        total_frames,
        budget,
    )
    .map_err(|error| mix_unsupported(error.to_string()))?;
    // The existing mixer has now checked exact RIFF layout/clock and every
    // source SHA. Its data bytes are authoritative only in this in-process call.
    let sources = verified
        .iter()
        .map(|source| PcmStemReceipt {
            asset_id: source.asset_id,
            source_asset_sha256: source.source_sha256.clone(),
            decoded_pcm_sha256: hex::encode(Sha256::digest(&source.bytes[44..])),
            sample_frames: (source.bytes.len() as u64 - 44) / 4,
        })
        .collect();
    let mut pcm = mixed.wav_bytes[44..].to_vec();
    let imported_base = if let Some((receipt, original)) = base {
        check_stem(&receipt, project, sample_frames)?;
        check_current(
            original.len() == pcm.len()
                && hex::encode(Sha256::digest(&original)) == receipt.decoded_pcm_sha256,
            "Imported SFX base changed before additive composition",
        )?;
        for (out, input) in pcm.chunks_exact_mut(2).zip(original.chunks_exact(2)) {
            let combined = i32::from(i16::from_le_bytes([out[0], out[1]]))
                + i32::from(i16::from_le_bytes([input[0], input[1]]));
            check(
                (-32_767..=32_767).contains(&combined),
                "Imported SFX base plus semantic cues would clip; no hidden limiter or dropped source",
            )?;
            out.copy_from_slice(&(combined as i16).to_le_bytes());
        }
        Some(receipt)
    } else {
        None
    };
    let (selected_scene_ids, source_cut_sha256) = selected_cut_identity(project, profile_id)?;
    let fingerprint = variant_dependency_fingerprint(project, profile_id)
        .map_err(|error| mix_unsupported(error.to_string()))?;
    let receipt = SemanticSfxBusReceipt {
        kind: "motionwright.semantic-sfx-bus/1",
        project_id: project.id,
        generation: project.generation,
        project_revision: project.revision,
        deliverable_profile_id: profile_id,
        selected_scene_ids,
        source_cut_sha256,
        audio_inputs_sha256: fingerprint.audio_inputs_sha256,
        imported_base,
        cues,
        placements,
        sources,
        sample_frames,
        semantic_wav_sha256: mixed.output_sha256,
        decoded_pcm_sha256: hex::encode(Sha256::digest(&pcm)),
    };
    Ok((OriginalSfxSource::SemanticBus(Box::new(receipt)), pcm))
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, SFX_CUE_SCHEMA, SfxAnchor, SfxEvent};
    use std::fs;

    fn source_wav(level: i16) -> Vec<u8> {
        let data_bytes = 48_000_u32 * 4;
        let mut out = Vec::with_capacity(44 + data_bytes as usize);
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + data_bytes).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16_u32.to_le_bytes());
        out.extend_from_slice(&1_u16.to_le_bytes());
        out.extend_from_slice(&2_u16.to_le_bytes());
        out.extend_from_slice(&48_000_u32.to_le_bytes());
        out.extend_from_slice(&192_000_u32.to_le_bytes());
        out.extend_from_slice(&4_u16.to_le_bytes());
        out.extend_from_slice(&16_u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&data_bytes.to_le_bytes());
        for _ in 0..96_000 {
            out.extend_from_slice(&level.to_le_bytes());
        }
        out
    }

    fn apply(service: &StudioService, project: &mut Project, key: &str, change: Change) {
        *project = service
            .apply(project.id, &project.stamp(), key, &change)
            .unwrap()
            .project;
    }

    #[test]
    fn selected_cut_bus_reads_only_consumed_cas_sources_without_native_voice_cut_authority() {
        // This is the SFX bus source closure, independent of the native
        // original voice-cut guard. It grants no shortened/reordered voice mix.
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("source-cut.sqlite3")).unwrap();
        let mut project = service
            .create_project("Selected semantic SFX CAS source fixture")
            .unwrap();
        for key in ["excluded-scene", "included-scene"] {
            apply(
                &service,
                &mut project,
                key,
                Change::AddScene {
                    name: key.into(),
                    objective: "Explicit authored source selection".into(),
                    duration_seconds: 1,
                },
            );
        }
        let excluded_scene = project.scenes[0].id;
        let included_scene = project.scenes[1].id;
        for (key, scene_id, level) in [
            ("excluded-source", excluded_scene, 500_i16),
            ("included-source", included_scene, 1_000_i16),
        ] {
            let bytes = source_wav(level);
            let path = root.path().join(format!("{key}.wav"));
            fs::write(&path, &bytes).unwrap();
            project = service
                .import_asset_file(
                    project.id,
                    &project.stamp(),
                    key,
                    &path,
                    format!("{key}.wav"),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            let asset_id = project
                .assets
                .iter()
                .find(|asset| asset.name == format!("{key}.wav"))
                .unwrap()
                .id;
            apply(
                &service,
                &mut project,
                &format!("{key}-cue"),
                Change::UpsertSemanticSfx {
                    cue: SemanticSfxCue {
                        schema: SFX_CUE_SCHEMA.into(),
                        id: Uuid::now_v7(),
                        scene_id,
                        event: SfxEvent::SceneReveal,
                        anchor: SfxAnchor::SceneStart,
                        offset_frames: 0,
                        source_asset_id: asset_id,
                        source_sha256: hex::encode(Sha256::digest(&bytes)),
                        source_frames: 30,
                        gain_milli_db: 0,
                        attack_frames: 0,
                        release_frames: 0,
                        tail_policy: Default::default(),
                        original_source_attestation: "Owned synthetic PCM fixture".into(),
                    },
                },
            );
        }
        let mut profile = project.deliverables[0].clone();
        profile.included_scene_ids = vec![included_scene];
        let profile_id = profile.id;
        apply(
            &service,
            &mut project,
            "select-second-scene",
            Change::UpsertDeliverable { profile },
        );
        let excluded = project
            .audio
            .sfx_cues
            .iter()
            .find(|cue| cue.scene_id == excluded_scene)
            .unwrap();
        let included = project
            .audio
            .sfx_cues
            .iter()
            .find(|cue| cue.scene_id == included_scene)
            .unwrap();
        let excluded_path = service
            .store()
            .lock()
            .verified_blob_path(&excluded.source_sha256)
            .unwrap();
        fs::write(
            excluded_path,
            b"excluded source is never read for the selected SFX bus",
        )
        .unwrap();
        let (source, pcm) = prepare_sfx_source(
            &service,
            &project,
            &project.stamp(),
            profile_id,
            None,
            48_000,
            OriginalMixDecodeBudget::Audition15Seconds,
        )
        .unwrap();
        let receipt = source.semantic_bus().unwrap();
        assert_eq!(receipt.selected_scene_ids, vec![included_scene]);
        assert_eq!(receipt.cues, vec![included.clone()]);
        assert_eq!(receipt.sources.len(), 1);
        assert_eq!(receipt.sources[0].asset_id, included.source_asset_id);
        assert_eq!(receipt.placements[0].first_sample, 0);
        assert_eq!(source.imported_asset_id(), None);
        assert_eq!(pcm.len(), 48_000 * 4);
        assert!(
            pcm.chunks_exact(2)
                .all(|bytes| i16::from_le_bytes([bytes[0], bytes[1]]) == 1_000)
        );
        source
            .validate_source(&project, profile_id, 48_000)
            .unwrap();
        assert_eq!(service.project(project.id).unwrap(), project);
        let included_path = service
            .store()
            .lock()
            .verified_blob_path(&included.source_sha256)
            .unwrap();
        fs::write(included_path, b"changed included source must fail").unwrap();
        assert!(matches!(
            prepare_sfx_source(
                &service,
                &project,
                &project.stamp(),
                profile_id,
                None,
                48_000,
                OriginalMixDecodeBudget::Audition15Seconds
            ),
            Err(MixError::StaleReference(_))
        ));
    }
}
