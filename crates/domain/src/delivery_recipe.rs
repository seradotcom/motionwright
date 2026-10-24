//! Authored delivery intent. Render plans, source measurements and effect
//! authority are derived from the current project, never stored in a recipe.
use crate::{Asset, DeliverableProfile, DomainError, ProductionDesign, Result, Scene};
use serde::{Deserialize, Deserializer, Serialize, de};
use std::collections::{BTreeMap, HashSet};
use std::fmt;
use uuid::Uuid;

pub const MAX_DELIVERY_SCENE_INTENTS: usize = 4_096;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum DeliveryRecipeSchema {
    #[default]
    #[serde(rename = "motionwright.delivery-recipe/1")]
    V1,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct DeliveryRecipe {
    pub schema: DeliveryRecipeSchema,
    pub scene_intents: Vec<DeliverySceneIntent>,
    pub original_mix: Option<OriginalMixRecipe>,
    /// Optional `motionwright.delivery-audio-targets/1` extension to recipe/1.
    /// When present this requires a reader implementing that exact contract;
    /// earlier deny-unknown-fields readers reject it. Rollback must preserve
    /// the original project/targets/CAS, never strip intent to force a read.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_optional_audio_targets"
    )]
    pub audio_targets: Option<DeliveryAudioTargets>,
}

/// Profile-owned QC intent, independent of whether a music/SFX mix is saved.
/// Values are exact public meter milli units, never silently rounded or used
/// to normalize media.
///
/// Minimum reader contract: `motionwright.delivery-audio-targets/1` including
/// both required nullable keys, exact i32 milli resolution, and at least one
/// requested metric. The contract name is documentation, not an extra JSON
/// key. Keeping recipe schema/1 does not make present targets readable by old
/// strict readers. An absent field alone retains legacy byte compatibility. Native QC consumes the final decoded output waveform.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DeliveryAudioTargets {
    pub target_lufs: Option<f64>,
    pub target_true_peak_dbtp: Option<f64>,
}

impl DeliveryAudioTargets {
    pub fn validated_milli_targets(&self) -> Result<(Option<i32>, Option<i32>)> {
        fn exact(value: f64) -> Result<i32> {
            let milli = (value * 1000.0).round();
            if !value.is_finite()
                || !milli.is_finite()
                || milli < f64::from(i32::MIN)
                || milli > f64::from(i32::MAX)
                || milli / 1000.0 != value
            {
                return Err(DomainError::Invalid(
                    "delivery audio targets require finite exact 0.001 units within the public i32 milli range".into(),
                ));
            }
            Ok(milli as i32)
        }
        if self.target_lufs.is_none() && self.target_true_peak_dbtp.is_none() {
            return Err(DomainError::Invalid(
                "delivery audio targets require at least one non-null target".into(),
            ));
        }
        Ok((
            self.target_lufs.map(exact).transpose()?,
            self.target_true_peak_dbtp.map(exact).transpose()?,
        ))
    }
}

impl<'de> Deserialize<'de> for DeliveryAudioTargets {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        fn required_nullable<'de, D: Deserializer<'de>>(
            deserializer: D,
        ) -> std::result::Result<Option<f64>, D::Error> {
            Option::<f64>::deserialize(deserializer)
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Wire {
            // deserialize_with without default makes both keys mandatory even
            // though their explicit JSON values may be null.
            #[serde(deserialize_with = "required_nullable")]
            target_lufs: Option<f64>,
            #[serde(deserialize_with = "required_nullable")]
            target_true_peak_dbtp: Option<f64>,
        }
        let wire = Wire::deserialize(deserializer)?;
        let result = Self {
            target_lufs: wire.target_lufs,
            target_true_peak_dbtp: wire.target_true_peak_dbtp,
        };
        result
            .validated_milli_targets()
            .map_err(de::Error::custom)?;
        Ok(result)
    }
}

fn deserialize_optional_audio_targets<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<DeliveryAudioTargets>, D::Error> {
    // Absence uses the field's default. An explicitly present null is not the
    // object specified by the closed wire contract.
    DeliveryAudioTargets::deserialize(deserializer).map(Some)
}

/// Application wire enums deliberately keep domain independent of the native
/// authoring crate. Native adapters must convert every variant exhaustively.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryNarrativeRole {
    Hook,
    Problem,
    Mechanism,
    Evidence,
    Comparison,
    Reveal,
    Payoff,
    Cta,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryArchetype {
    Statement,
    SplitExplanation,
    ArchitectureReveal,
    Comparison,
    Metric,
    Timeline,
    CodeFocus,
    DiagramBuild,
    ObjectSpotlight,
    EvidenceFrame,
    ProductProof,
    Endcard,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliverySceneIntent {
    pub scene_id: Uuid,
    pub role: Option<DeliveryNarrativeRole>,
    pub archetype: Option<DeliveryArchetype>,
}

/// Per-bus gain envelope on the existing 48 kHz output sample clock.
/// Muting never removes the selected source or bypasses source verification.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixBusEnvelope {
    pub muted: bool,
    pub fade_in_samples: u32,
    pub fade_out_samples: u32,
}

impl OriginalMixBusEnvelope {
    pub fn is_neutral(&self) -> bool {
        !self.muted && !self.has_fades()
    }

    pub fn has_fades(&self) -> bool {
        self.fade_in_samples != 0 || self.fade_out_samples != 0
    }

    /// The spans may touch, but may never overlap or exceed the output.
    /// The native mixer repeats this check against the verified PCM length.
    pub fn validate_for_sample_frames(&self, sample_frames: u64) -> Result<()> {
        let incoming = u64::from(self.fade_in_samples);
        let outgoing = u64::from(self.fade_out_samples);
        if incoming > sample_frames
            || outgoing > sample_frames
            || incoming + outgoing > sample_frames
        {
            return Err(DomainError::Invalid(
                "original mix fades overlap or exceed the exact output sample range".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixRecipe {
    pub music_asset_id: Option<Uuid>,
    pub sfx_asset_id: Option<Uuid>,
    pub sfx_gain_db: f64,
    pub duck_attenuation_db: f64,
    pub duck_attack_samples: u32,
    pub duck_release_samples: u32,
    #[serde(default, skip_serializing_if = "OriginalMixBusEnvelope::is_neutral")]
    pub voice_envelope: OriginalMixBusEnvelope,
    #[serde(default, skip_serializing_if = "OriginalMixBusEnvelope::is_neutral")]
    pub music_envelope: OriginalMixBusEnvelope,
    #[serde(default, skip_serializing_if = "OriginalMixBusEnvelope::is_neutral")]
    pub sfx_envelope: OriginalMixBusEnvelope,
}

impl DeliveryRecipe {
    pub fn validate(&self, scenes: &[Scene], assets: &[Asset]) -> Result<()> {
        if self.scene_intents.len() > MAX_DELIVERY_SCENE_INTENTS {
            return Err(DomainError::Invalid(
                "delivery recipe scene intent collection is too large".into(),
            ));
        }
        let scene_ids: HashSet<_> = scenes.iter().map(|scene| scene.id).collect();
        let mut seen = HashSet::new();
        for intent in &self.scene_intents {
            if !scene_ids.contains(&intent.scene_id) || !seen.insert(intent.scene_id) {
                return Err(DomainError::Invalid(
                    "delivery recipe scene intents require existing, unique scene ids".into(),
                ));
            }
        }
        if let Some(mix) = &self.original_mix {
            mix.validate(assets)?;
        }
        if let Some(targets) = &self.audio_targets {
            targets.validated_milli_targets()?;
        }
        Ok(())
    }

    /// Sorting is a derived projection only. Never rewrite the stored list on
    /// read; excluded scenes retain their authored decisions for another cut.
    pub fn consumed_scene_intents(&self, scene_ids: &HashSet<Uuid>) -> Vec<&DeliverySceneIntent> {
        let mut intents = self
            .scene_intents
            .iter()
            .filter(|intent| {
                scene_ids.contains(&intent.scene_id)
                    && (intent.role.is_some() || intent.archetype.is_some())
            })
            .collect::<Vec<_>>();
        intents.sort_by_key(|intent| intent.scene_id);
        intents
    }
}

impl OriginalMixRecipe {
    pub fn has_fades(&self) -> bool {
        self.voice_envelope.has_fades()
            || self.music_envelope.has_fades()
            || self.sfx_envelope.has_fades()
    }

    pub fn validate_envelopes_for_sample_frames(&self, sample_frames: u64) -> Result<()> {
        for envelope in [self.voice_envelope, self.music_envelope, self.sfx_envelope] {
            envelope.validate_for_sample_frames(sample_frames)?;
        }
        Ok(())
    }

    pub fn validate(&self, assets: &[Asset]) -> Result<()> {
        if !self.sfx_gain_db.is_finite() || !(-80.0..=12.0).contains(&self.sfx_gain_db) {
            return Err(DomainError::Invalid(
                "delivery recipe SFX gain must be finite and within -80..12 dB".into(),
            ));
        }
        if !self.duck_attenuation_db.is_finite()
            || !(0.0..=30.0).contains(&self.duck_attenuation_db)
        {
            return Err(DomainError::Invalid(
                "delivery recipe duck attenuation must be finite and within 0..30 dB".into(),
            ));
        }
        if !(1..=48_000).contains(&self.duck_attack_samples)
            || !(1..=48_000).contains(&self.duck_release_samples)
        {
            return Err(DomainError::Invalid(
                "delivery recipe duck attack/release must be within 1..48000 samples".into(),
            ));
        }
        if self.music_asset_id.is_some() && self.music_asset_id == self.sfx_asset_id {
            return Err(DomainError::Invalid(
                "delivery recipe music and SFX require distinct source assets".into(),
            ));
        }
        for id in [self.music_asset_id, self.sfx_asset_id]
            .into_iter()
            .flatten()
        {
            let asset = assets.iter().find(|asset| asset.id == id).ok_or_else(|| {
                DomainError::Invalid("delivery recipe references an unknown source asset".into())
            })?;
            asset.validate()?;
            if !asset.media_type.starts_with("audio/") || asset.content_sha256.is_none() {
                return Err(DomainError::Invalid(
                    "delivery recipe sources require registered content-addressed audio assets"
                        .into(),
                ));
            }
        }
        Ok(())
    }
}

impl ProductionDesign {
    /// Each branch supplies its own scenes/profiles; assets remain global.
    pub fn validate_delivery_recipes(
        &self,
        scenes: &[Scene],
        assets: &[Asset],
        profiles: &[DeliverableProfile],
    ) -> Result<()> {
        if self.delivery_recipes.len() > crate::MAX_DELIVERABLE_PROFILES {
            return Err(DomainError::Invalid("too many delivery recipes".into()));
        }
        for (profile_id, recipe) in &self.delivery_recipes {
            if !profiles.iter().any(|profile| profile.id == *profile_id) {
                return Err(DomainError::Invalid(
                    "delivery recipe references an unknown deliverable profile".into(),
                ));
            }
            recipe.validate(scenes, assets)?;
            if let Some(mix) = &recipe.original_mix
                && mix.has_fades()
            {
                // This is the authored cut bound. Native admission additionally
                // checks the actual verified PCM output, with no pad or trim.
                let profile = profiles
                    .iter()
                    .find(|profile| profile.id == *profile_id)
                    .expect("profile existence checked above");
                let selected = crate::delivery::scenes_for_profile(scenes, profile)?;
                let mut duration = crate::RationalTime::ZERO;
                for scene in selected {
                    duration = duration
                        .checked_add(scene.duration)
                        .map_err(|_| DomainError::Invalid("mix output duration overflow".into()))?;
                }
                if duration.den <= 0 || duration.num < 0 {
                    return Err(DomainError::Invalid("invalid mix output duration".into()));
                }
                let samples = i128::from(duration.num) * 48_000;
                if samples % i128::from(duration.den) != 0 {
                    return Err(DomainError::Invalid(
                        "mix fades require an exact 48 kHz output clock; no implicit snap".into(),
                    ));
                }
                let samples = u64::try_from(samples / i128::from(duration.den))
                    .map_err(|_| DomainError::Invalid("mix output sample range overflow".into()))?;
                mix.validate_envelopes_for_sample_frames(samples)?;
            }
        }
        Ok(())
    }

    pub(crate) fn delivery_recipe_references_asset(&self, asset_id: Uuid) -> bool {
        self.delivery_recipes.values().any(|recipe| {
            recipe.original_mix.as_ref().is_some_and(|mix| {
                mix.music_asset_id == Some(asset_id) || mix.sfx_asset_id == Some(asset_id)
            })
        })
    }
}

/// Run at typed raw-JSON boundaries. Passing through serde_json::Value first
/// has already discarded duplicate spellings and cannot provide this check.
pub(crate) fn deserialize_delivery_recipes<'de, D>(
    deserializer: D,
) -> std::result::Result<BTreeMap<Uuid, DeliveryRecipe>, D::Error>
where
    D: Deserializer<'de>,
{
    struct RecipesVisitor;
    impl<'de> de::Visitor<'de> for RecipesVisitor {
        type Value = BTreeMap<Uuid, DeliveryRecipe>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("a map with unique delivery profile UUID keys")
        }

        fn visit_map<A>(self, mut entries: A) -> std::result::Result<Self::Value, A::Error>
        where
            A: de::MapAccess<'de>,
        {
            let mut recipes = BTreeMap::new();
            while let Some(profile_id) = entries.next_key::<Uuid>()? {
                if recipes.contains_key(&profile_id) {
                    return Err(de::Error::custom(format!(
                        "duplicate delivery recipe profile UUID: {profile_id}"
                    )));
                }
                if recipes.len() >= crate::MAX_DELIVERABLE_PROFILES {
                    return Err(de::Error::custom("too many delivery recipes"));
                }
                recipes.insert(profile_id, entries.next_value::<DeliveryRecipe>()?);
            }
            Ok(recipes)
        }
    }
    deserializer.deserialize_map(RecipesVisitor)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Change, Project, variant_dependency_fingerprint};

    fn project() -> Project {
        let mut project = Project::new("Synthetic delivery recipe unit fixture").unwrap();
        for name in ["Opening", "Evidence"] {
            project
                .apply_change(&Change::AddScene {
                    name: name.into(),
                    objective: "Editable recipe source".into(),
                    duration_seconds: 2,
                })
                .unwrap();
        }
        project
    }

    fn recipe(project: &Project) -> DeliveryRecipe {
        DeliveryRecipe {
            schema: DeliveryRecipeSchema::V1,
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| DeliverySceneIntent {
                    scene_id: scene.id,
                    role: Some(DeliveryNarrativeRole::Evidence),
                    archetype: Some(DeliveryArchetype::EvidenceFrame),
                })
                .collect(),
            audio_targets: None,
            original_mix: None,
        }
    }

    fn mix() -> OriginalMixRecipe {
        OriginalMixRecipe {
            music_asset_id: None,
            sfx_asset_id: None,
            sfx_gain_db: -12.0,
            duck_attenuation_db: 8.0,
            duck_attack_samples: 960,
            duck_release_samples: 4_800,
            voice_envelope: Default::default(),
            music_envelope: Default::default(),
            sfx_envelope: Default::default(),
        }
    }

    fn set(project: &mut Project, profile_id: Uuid, recipe: Option<DeliveryRecipe>) {
        project
            .apply_change(&Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe,
            })
            .unwrap();
    }

    #[test]
    fn absent_map_stays_absent_and_recipe_wire_is_strict() {
        let design: ProductionDesign = serde_json::from_str("{}").unwrap();
        assert!(design.delivery_recipes.is_empty());
        assert!(
            serde_json::to_value(design)
                .unwrap()
                .get("delivery_recipes")
                .is_none()
        );
        let valid = serde_json::to_string(&DeliveryRecipe::default()).unwrap();
        assert!(serde_json::from_str::<DeliveryRecipe>(&valid).is_ok());
        assert!(
            serde_json::from_str::<DeliveryRecipe>(&valid.replace(
                "motionwright.delivery-recipe/1",
                "motionwright.delivery-recipe/2"
            ))
            .is_err()
        );
        let mut unknown = serde_json::to_value(DeliveryRecipe::default()).unwrap();
        unknown["preview_approval"] = serde_json::json!(true);
        assert!(serde_json::from_value::<DeliveryRecipe>(unknown).is_err());
        let mut extra_mix = serde_json::to_value(mix()).unwrap();
        extra_mix["sample_peak_guard_dbfs"] = serde_json::json!(-1.0);
        assert!(serde_json::from_value::<OriginalMixRecipe>(extra_mix).is_err());
        let mut intent = serde_json::to_value(&recipe(&project()).scene_intents[0]).unwrap();
        intent["role"] = serde_json::json!("unregistered_role");
        assert!(serde_json::from_value::<DeliverySceneIntent>(intent).is_err());
    }

    #[test]
    fn raw_duplicate_keys_reject_all_equivalent_uuid_spellings() {
        let id = Uuid::parse_str("abcdefab-1234-4567-89ab-abcdef123456").unwrap();
        let recipe = serde_json::to_string(&DeliveryRecipe::default()).unwrap();
        for second in [
            id.to_string(),
            id.to_string().to_uppercase(),
            id.simple().to_string(),
            id.urn().to_string(),
        ] {
            let raw =
                format!("{{\"delivery_recipes\":{{\"{id}\":{recipe},\"{second}\":{recipe}}}}}");
            let error = serde_json::from_str::<ProductionDesign>(&raw).unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("duplicate delivery recipe profile UUID")
            );
        }
    }

    #[test]
    fn invalid_recipe_changes_leave_the_domain_document_exactly_unchanged() {
        let mut project = project();
        let profile_id = project.deliverables[0].id;
        let valid = recipe(&project);
        set(&mut project, profile_id, Some(valid.clone()));
        let mut invalid = Vec::new();
        let mut duplicate = valid.clone();
        duplicate
            .scene_intents
            .push(duplicate.scene_intents[0].clone());
        invalid.push(duplicate);
        let mut missing = valid.clone();
        missing.scene_intents[0].scene_id = Uuid::new_v4();
        invalid.push(missing);
        for value in [f64::NAN, f64::INFINITY, -80.01, 12.01] {
            let mut candidate = valid.clone();
            candidate.original_mix = Some(OriginalMixRecipe {
                sfx_gain_db: value,
                ..mix()
            });
            invalid.push(candidate);
        }
        for value in [f64::NAN, f64::NEG_INFINITY, -0.01, 30.01] {
            let mut candidate = valid.clone();
            candidate.original_mix = Some(OriginalMixRecipe {
                duck_attenuation_db: value,
                ..mix()
            });
            invalid.push(candidate);
        }
        for value in [0, 48_001] {
            for attack in [true, false] {
                let mut original_mix = mix();
                if attack {
                    original_mix.duck_attack_samples = value;
                } else {
                    original_mix.duck_release_samples = value;
                }
                let mut candidate = valid.clone();
                candidate.original_mix = Some(original_mix);
                invalid.push(candidate);
            }
        }
        for recipe in invalid {
            let before = project.clone();
            assert!(
                project
                    .apply_change(&Change::SetDeliveryRecipe {
                        deliverable_profile_id: profile_id,
                        recipe: Some(recipe),
                    })
                    .is_err()
            );
            assert_eq!(project, before);
        }
        let before = project.clone();
        assert!(
            project
                .apply_change(&Change::SetDeliveryRecipe {
                    deliverable_profile_id: Uuid::new_v4(),
                    recipe: None,
                })
                .is_err()
        );
        assert_eq!(project, before);
    }

    #[test]
    fn both_branch_snapshots_use_their_own_profiles_and_scenes() {
        let mut project = project();
        let profile_id = project.deliverables[0].id;
        let recipe = recipe(&project);
        set(&mut project, profile_id, Some(recipe.clone()));
        project
            .apply_change(&Change::CreateBranch {
                name: "Recipe branch".into(),
            })
            .unwrap();
        for base in [true, false] {
            let mut candidate = project.clone();
            let workspace = &mut candidate.branch_workspaces[0];
            let state = if base {
                &mut workspace.base_state
            } else {
                &mut workspace.current_state
            };
            let mut local_profile = state.deliverables[0].clone();
            local_profile.id = Uuid::new_v4();
            local_profile.name = "Only in this saved branch state".into();
            state
                .production_design
                .delivery_recipes
                .insert(local_profile.id, recipe.clone());
            state.deliverables.push(local_profile);
            candidate.validate().unwrap();
            let workspace = &mut candidate.branch_workspaces[0];
            let state = if base {
                &mut workspace.base_state
            } else {
                &mut workspace.current_state
            };
            state
                .deliverables
                .retain(|profile| profile.id != profile_id);
            assert!(
                candidate
                    .validate()
                    .unwrap_err()
                    .to_string()
                    .contains("unknown deliverable profile")
            );

            let mut candidate = project.clone();
            let workspace = &mut candidate.branch_workspaces[0];
            let state = if base {
                &mut workspace.base_state
            } else {
                &mut workspace.current_state
            };
            state.scenes.remove(0);
            assert!(
                candidate
                    .validate()
                    .unwrap_err()
                    .to_string()
                    .contains("unique scene ids")
            );
        }
        let mut active = project.clone();
        active.scenes.remove(0);
        assert!(active.validate().is_err());
    }

    #[test]
    fn fingerprints_normalize_intent_order_and_ignore_other_profiles_and_excluded_scenes() {
        let mut project = project();
        let a = project.deliverables[0].id;
        let b = project.deliverables[1].id;
        let recipe = recipe(&project);
        set(&mut project, a, Some(recipe.clone()));
        let before_a = variant_dependency_fingerprint(&project, a).unwrap();
        let before_b = variant_dependency_fingerprint(&project, b).unwrap();
        let mut reordered = recipe.clone();
        reordered.scene_intents.reverse();
        set(&mut project, a, Some(reordered.clone()));
        assert_eq!(
            variant_dependency_fingerprint(&project, a).unwrap(),
            before_a
        );
        assert_eq!(project.production_design.delivery_recipes[&a], reordered);

        let mut changed = recipe.clone();
        changed.scene_intents[0].role = Some(DeliveryNarrativeRole::Hook);
        set(&mut project, a, Some(changed));
        let after_a = variant_dependency_fingerprint(&project, a).unwrap();
        assert_ne!(after_a.frame_inputs_sha256, before_a.frame_inputs_sha256);
        assert_eq!(after_a.audio_inputs_sha256, before_a.audio_inputs_sha256);
        assert_eq!(
            variant_dependency_fingerprint(&project, b).unwrap(),
            before_b
        );

        project.deliverables[0].included_scene_ids = vec![project.scenes[0].id];
        let before = variant_dependency_fingerprint(&project, a).unwrap();
        project
            .production_design
            .delivery_recipes
            .get_mut(&a)
            .unwrap()
            .scene_intents[1]
            .archetype = Some(DeliveryArchetype::Metric);
        assert_eq!(variant_dependency_fingerprint(&project, a).unwrap(), before);
    }

    #[test]
    fn neutral_bus_envelopes_preserve_legacy_json_and_explicit_values_are_closed() {
        let expected = r#"{"music_asset_id":null,"sfx_asset_id":null,"sfx_gain_db":-12.0,"duck_attenuation_db":8.0,"duck_attack_samples":960,"duck_release_samples":4800}"#;
        assert_eq!(serde_json::to_string(&mix()).unwrap(), expected);
        let legacy = serde_json::to_value(mix()).unwrap();
        for key in ["voice_envelope", "music_envelope", "sfx_envelope"] {
            assert!(legacy.get(key).is_none());
            let mut explicit = legacy.clone();
            explicit[key] = serde_json::json!({
                "muted": false, "fade_in_samples": 0, "fade_out_samples": 0
            });
            let decoded: OriginalMixRecipe = serde_json::from_value(explicit).unwrap();
            assert_eq!(serde_json::to_value(decoded).unwrap(), legacy);
        }
        for invalid in [
            serde_json::json!({"muted": 0, "fade_in_samples": 0, "fade_out_samples": 0}),
            serde_json::json!({"muted": false, "fade_in_samples": -1, "fade_out_samples": 0}),
            serde_json::json!({"muted": false, "fade_in_samples": 0.5, "fade_out_samples": 0}),
            serde_json::json!({"muted": false, "fade_in_samples": 4294967296_u64, "fade_out_samples": 0}),
            serde_json::json!({"muted": true}),
            serde_json::json!({"muted": false, "fade_in_samples": 0, "fade_out_samples": 0, "approved": true}),
        ] {
            assert!(serde_json::from_value::<OriginalMixBusEnvelope>(invalid).is_err());
        }
        let repeated = r#"{"muted":false,"muted":true,"fade_in_samples":0,"fade_out_samples":0}"#;
        assert!(serde_json::from_str::<OriginalMixBusEnvelope>(repeated).is_err());
    }

    #[test]
    fn touching_fades_fit_exact_samples_and_muted_invalid_ranges_still_fail() {
        let touching = OriginalMixBusEnvelope {
            muted: false,
            fade_in_samples: 24_000,
            fade_out_samples: 24_000,
        };
        touching.validate_for_sample_frames(48_000).unwrap();
        assert!(touching.validate_for_sample_frames(47_999).is_err());
        for envelope in [
            OriginalMixBusEnvelope {
                muted: false,
                fade_in_samples: 48_001,
                fade_out_samples: 0,
            },
            OriginalMixBusEnvelope {
                muted: true,
                fade_in_samples: 0,
                fade_out_samples: 48_001,
            },
            OriginalMixBusEnvelope {
                muted: true,
                fade_in_samples: u32::MAX,
                fade_out_samples: u32::MAX,
            },
        ] {
            assert!(envelope.validate_for_sample_frames(48_000).is_err());
        }
    }

    #[test]
    fn persisted_fades_invalidate_audio_only_and_invalid_cut_or_retime_is_atomic() {
        let mut project = project();
        let profile_id = project.deliverables[0].id;
        let mut saved = recipe(&project);
        saved.original_mix = Some(mix());
        set(&mut project, profile_id, Some(saved.clone()));
        let neutral = variant_dependency_fingerprint(&project, profile_id).unwrap();
        saved.original_mix.as_mut().unwrap().music_envelope = OriginalMixBusEnvelope {
            muted: true,
            fade_in_samples: 96_000,
            fade_out_samples: 96_000,
        };
        set(&mut project, profile_id, Some(saved));
        let changed = variant_dependency_fingerprint(&project, profile_id).unwrap();
        assert_eq!(changed.frame_inputs_sha256, neutral.frame_inputs_sha256);
        assert_eq!(changed.caption_inputs_sha256, neutral.caption_inputs_sha256);
        assert_ne!(changed.audio_inputs_sha256, neutral.audio_inputs_sha256);
        let before = project.clone();
        let mut cut = project.deliverables[0].clone();
        cut.included_scene_ids = vec![project.scenes[0].id];
        for change in [
            Change::SetSceneDuration {
                scene_id: project.scenes[1].id,
                duration: crate::RationalTime::new(1, 1).unwrap(),
            },
            Change::UpsertDeliverable { profile: cut },
        ] {
            assert!(project.apply_change(&change).is_err());
            assert_eq!(project, before);
        }
    }

    #[test]
    fn audio_targets_require_both_nullable_keys_and_preserve_absent_legacy_bytes() {
        let legacy =
            r#"{"schema":"motionwright.delivery-recipe/1","scene_intents":[],"original_mix":null}"#;
        let decoded: DeliveryRecipe = serde_json::from_str(legacy).unwrap();
        assert!(decoded.audio_targets.is_none());
        assert_eq!(serde_json::to_string(&decoded).unwrap(), legacy);
        for raw in [
            r#"{}"#,
            r#"{"target_lufs":-23.0}"#,
            r#"{"target_true_peak_dbtp":-1.0}"#,
            r#"{"target_lufs":null,"target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":-23.0,"target_true_peak_dbtp":null,"approved":true}"#,
            r#"{"target_lufs":-23.0,"target_lufs":-14.0,"target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":"-23.0","target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":-23.0001,"target_true_peak_dbtp":null}"#,
        ] {
            assert!(
                serde_json::from_str::<DeliveryAudioTargets>(raw).is_err(),
                "{raw}"
            );
        }
        let explicit_null = legacy.replace(
            "\"original_mix\":null",
            "\"original_mix\":null,\"audio_targets\":null",
        );
        assert!(serde_json::from_str::<DeliveryRecipe>(&explicit_null).is_err());
        for raw in [
            r#"{"target_lufs":-23.001,"target_true_peak_dbtp":null}"#,
            r#"{"target_lufs":null,"target_true_peak_dbtp":-1.0}"#,
            r#"{"target_lufs":-23.0,"target_true_peak_dbtp":-1.0}"#,
        ] {
            let targets: DeliveryAudioTargets = serde_json::from_str(raw).unwrap();
            let wire = serde_json::to_value(&targets).unwrap();
            assert!(wire.get("target_lufs").is_some());
            assert!(wire.get("target_true_peak_dbtp").is_some());
        }
    }

    #[test]
    fn audio_target_precision_is_exact_i32_milli_without_arbitrary_ranges_or_rounding() {
        for milli in [i32::MIN, -23_001, -1, 0, 1, 23_001, i32::MAX] {
            let targets = DeliveryAudioTargets {
                target_lufs: Some(f64::from(milli) / 1000.0),
                target_true_peak_dbtp: None,
            };
            assert_eq!(
                targets.validated_milli_targets().unwrap(),
                (Some(milli), None)
            );
            let reparsed: DeliveryAudioTargets =
                serde_json::from_str(&serde_json::to_string(&targets).unwrap()).unwrap();
            assert_eq!(reparsed, targets);
        }
        for bad in [
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::from(i32::MIN) / 1000.0 - 0.001,
            f64::from(i32::MAX) / 1000.0 + 0.001,
            -23.0001,
            -0.0001,
            0.0005,
            f64::MAX,
        ] {
            for lufs in [true, false] {
                let targets = DeliveryAudioTargets {
                    target_lufs: if lufs { Some(bad) } else { None },
                    target_true_peak_dbtp: if lufs { None } else { Some(bad) },
                };
                assert!(targets.validated_milli_targets().is_err(), "{bad}");
            }
        }
    }

    #[test]
    fn target_only_recipes_bind_only_consuming_audio_and_invalid_changes_are_atomic() {
        let mut project = project();
        let a = project.deliverables[0].id;
        let b = project.deliverables[1].id;
        let before_a = variant_dependency_fingerprint(&project, a).unwrap();
        let before_b = variant_dependency_fingerprint(&project, b).unwrap();
        let targets = DeliveryAudioTargets {
            target_lufs: Some(-23.0),
            target_true_peak_dbtp: Some(-1.0),
        };
        let authored = DeliveryRecipe {
            audio_targets: Some(targets.clone()),
            ..Default::default()
        };
        set(&mut project, a, Some(authored.clone()));
        assert_eq!(project.production_design.delivery_recipes[&a], authored);
        assert!(
            project.production_design.delivery_recipes[&a]
                .original_mix
                .is_none()
        );
        let after_a = variant_dependency_fingerprint(&project, a).unwrap();
        assert_eq!(after_a.frame_inputs_sha256, before_a.frame_inputs_sha256);
        assert_eq!(
            after_a.caption_inputs_sha256,
            before_a.caption_inputs_sha256
        );
        assert_ne!(after_a.audio_inputs_sha256, before_a.audio_inputs_sha256);
        assert_ne!(after_a.master_inputs_sha256, before_a.master_inputs_sha256);
        assert_eq!(
            variant_dependency_fingerprint(&project, b).unwrap(),
            before_b
        );
        let before = project.clone();
        for target in [
            DeliveryAudioTargets {
                target_lufs: None,
                target_true_peak_dbtp: None,
            },
            DeliveryAudioTargets {
                target_lufs: Some(-23.0001),
                target_true_peak_dbtp: None,
            },
        ] {
            assert!(
                project
                    .apply_change(&Change::SetDeliveryRecipe {
                        deliverable_profile_id: a,
                        recipe: Some(DeliveryRecipe {
                            audio_targets: Some(target),
                            ..Default::default()
                        }),
                    })
                    .is_err()
            );
            assert_eq!(project, before);
        }
        set(&mut project, a, None);
        assert_eq!(
            variant_dependency_fingerprint(&project, a).unwrap(),
            before_a
        );
    }
}
