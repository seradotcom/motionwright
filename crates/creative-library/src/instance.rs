//! Typed human overrides on a versioned creative instance. No implicit library
//! template mutation, network requests, installs or privileged source execution.
use crate::*;
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum CopyProperty {
    Eyebrow,
    Headline,
    Body,
    LabelA,
    LabelB,
    Disclosure,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum InputSlot {
    Primary,
    Secondary,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CreativeInstanceEdit {
    ReplaceCopy {
        field: CopyProperty,
        expected_text: String,
        next_text: String,
    },
    ReplaceBrandColor {
        role: ColorRole,
        expected_color: String,
        next_color: String,
    },
    ReplaceMotionEnergy {
        expected: u8,
        next: u8,
    },
    ReplaceSeed {
        expected: u64,
        next: u64,
    },
    SetAnimated {
        expected: bool,
        next: bool,
    },
    ReplaceSourceAsset {
        slot: InputSlot,
        expected_digest: Option<String>,
        next: Option<native::Asset>,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CreativeOverrideProposal {
    pub schema: String,
    pub component_id: Uuid,
    pub recipe: RecipeId,
    pub recipe_version: u32,
    pub expected_input_sha256: String,
    pub proposed_input_sha256: String,
    pub expected_source_sha256: String,
    pub proposed_source_sha256: String,
    pub changed_properties: Vec<String>,
    pub expected_recipe_definition_sha256: String,
    pub output_recipe_definition_sha256: String,
    pub proposal: CreativeContribution,
    pub request: ComponentRequest,
    pub brand: BrandProfile,
    pub taste: TasteProfile,
    pub template_mutated: bool,
    pub project_committed: bool,
    pub requires_project_source_cas: bool,
    pub independent_human_approval: bool,
}
fn copy_field(copy: &mut CopyPack, field: CopyProperty) -> &mut String {
    match field {
        CopyProperty::Eyebrow => &mut copy.eyebrow,
        CopyProperty::Headline => &mut copy.headline,
        CopyProperty::Body => &mut copy.body,
        CopyProperty::LabelA => &mut copy.label_a,
        CopyProperty::LabelB => &mut copy.label_b,
        CopyProperty::Disclosure => &mut copy.disclosure,
    }
}
fn slot(req: &mut ComponentRequest, slot: InputSlot) -> &mut Option<native::Asset> {
    match slot {
        InputSlot::Primary => &mut req.primary_asset,
        InputSlot::Secondary => &mut req.secondary_asset,
    }
}
fn assert_transition(before: &Option<native::Asset>, next: &Option<native::Asset>) -> Result<()> {
    if let Some(asset) = next {
        check(
            asset.rights.use_authorized,
            "An asset override cannot introduce unapproved media",
        )?;
        check(
            valid_sha(&asset.sha256),
            "An asset override must retain a valid exact source SHA",
        )?;
        check(
            matches!(
                asset.kind,
                native::AssetKind::Png
                    | native::AssetKind::Jpeg
                    | native::AssetKind::Mp4
                    | native::AssetKind::Woff2
            ),
            "An override must use the recognized native asset profile",
        )?;
        check(
            before
                .as_ref()
                .is_none_or(|old| old.id != asset.id || old.sha256 == asset.sha256),
            "An asset ID cannot be rebound to different bytes without an explicit import identity",
        )?;
    }
    Ok(())
}
pub fn propose_instance_override(
    current: &CreativeContribution,
    request: &ComponentRequest,
    brand: &BrandProfile,
    taste: &TasteProfile,
    expected_input_sha256: &str,
    edits: &[CreativeInstanceEdit],
) -> Result<CreativeOverrideProposal> {
    check(
        !edits.is_empty() && edits.len() <= 32,
        "A creative override must contain 1..32 explicit field edits",
    )?;
    check(
        current.component_id == request.instance_id
            && current.recipe == request.recipe
            && current.recipe_version == request.version,
        "Override cannot change a component's canonical recipe or stable instance ID",
    )?;
    check(
        current.input_sha256 == canonical_digest(&(request, brand, taste))?
            && current.input_sha256 == expected_input_sha256
            && current.source_sha256 == canonical_digest(&current.output)?,
        "Override was prepared against another native source or project input revision",
    )?;
    let original_recipe_digest = canonical_digest(&request.recipe.definition())?;
    let mut req = request.clone();
    let mut brand = brand.clone();
    let mut taste = taste.clone();
    let mut changed = Vec::new();
    let mut seen = BTreeSet::new();
    for edit in edits {
        let (key, did_change) = match edit {
            CreativeInstanceEdit::ReplaceCopy {
                field,
                expected_text,
                next_text,
            } => {
                let value = copy_field(&mut req.copy, *field);
                check(
                    value == expected_text,
                    "Human/agent copy edit is stale; existing edited text must not be overwritten",
                )?;
                text(
                    next_text,
                    2000,
                    "Creative copy override is empty or out of bounds",
                )?;
                let updated = *value != *next_text;
                *value = next_text.clone();
                (format!("copy/{field:?}"), updated)
            }
            CreativeInstanceEdit::ReplaceBrandColor {
                role,
                expected_color,
                next_color,
            } => {
                rgb(next_color)?;
                let entry = brand
                    .colors
                    .iter_mut()
                    .find(|color| color.role == *role)
                    .ok_or_else(|| CraftError("Brand color role does not exist".into()))?;
                check(
                    entry.value == *expected_color,
                    "A protected brand color was changed since this override was prepared",
                )?;
                let updated = entry.value != *next_color;
                entry.value = next_color.clone();
                (format!("brand/color/{role:?}"), updated)
            }
            CreativeInstanceEdit::ReplaceMotionEnergy { expected, next } => {
                check(
                    taste.motion_energy == *expected && *next <= 100,
                    "Motion energy precondition is stale or outside [0,100]",
                )?;
                let updated = *expected != *next;
                taste.motion_energy = *next;
                (String::from("taste/motion_energy"), updated)
            }
            CreativeInstanceEdit::ReplaceSeed { expected, next } => {
                check(
                    req.seed == *expected,
                    "Procedural seed precondition is stale",
                )?;
                let updated = *expected != *next;
                req.seed = *next;
                (String::from("component/seed"), updated)
            }
            CreativeInstanceEdit::SetAnimated { expected, next } => {
                check(
                    req.motion == *expected,
                    "Motion opt-in has changed since this proposal was created",
                )?;
                let updated = *expected != *next;
                req.motion = *next;
                (String::from("component/motion"), updated)
            }
            CreativeInstanceEdit::ReplaceSourceAsset {
                slot: which,
                expected_digest,
                next,
            } => {
                let value = slot(&mut req, *which);
                check(
                    value.as_ref().map(|asset| &asset.sha256) == expected_digest.as_ref(),
                    "Media replacement is stale and cannot discard another user's source",
                )?;
                assert_transition(value, next)?;
                let updated = *value != *next;
                *value = next.clone();
                (format!("asset/{which:?}"), updated)
            }
        };
        check(
            seen.insert(key.clone()),
            "An atomic creative override cannot specify one field twice",
        )?;
        if did_change {
            changed.push(key);
        }
    }
    check(
        !changed.is_empty(),
        "An override with no change must not consume a project revision",
    )?;
    req.validate()?;
    brand.validate()?;
    taste.validate()?;
    brand.check_copy(&req.copy, req.locale)?;
    check(
        original_recipe_digest == canonical_digest(&req.recipe.definition())?,
        "An instance override is not allowed to modify the shared recipe definition",
    )?;
    let proposal = realize(&req, &brand, &taste)?;
    check(
        proposal.source_sha256 != current.source_sha256,
        "Override had no resulting native source difference; do not silently claim a change",
    )?;
    Ok(CreativeOverrideProposal {
        schema: "motionwright.creative-instance-override/1".into(),
        component_id: req.instance_id,
        recipe: req.recipe,
        recipe_version: req.version,
        expected_input_sha256: current.input_sha256.clone(),
        proposed_input_sha256: proposal.input_sha256.clone(),
        expected_source_sha256: current.source_sha256.clone(),
        proposed_source_sha256: proposal.source_sha256.clone(),
        changed_properties: changed,
        expected_recipe_definition_sha256: original_recipe_digest.clone(),
        output_recipe_definition_sha256: original_recipe_digest,
        proposal,
        request: req,
        brand,
        taste,
        template_mutated: false,
        project_committed: false,
        requires_project_source_cas: true,
        independent_human_approval: false,
    })
}

#[cfg(test)]
mod transition_tests {
    use super::*;

    fn original(id: u128, sha: &str) -> native::Asset {
        native::Asset {
            id: Uuid::from_u128(id),
            sha256: sha.to_owned(),
            kind: native::AssetKind::Png,
            rights: native::AssetRights {
                owner: "Owner".into(),
                license: "Owned original".into(),
                attribution: "Fixture".into(),
                use_authorized: true,
                redistribute: false,
            },
        }
    }

    #[test]
    fn the_same_asset_identity_may_not_point_to_different_bytes() {
        let before = Some(original(1, &"a".repeat(64)));
        let forged = Some(original(1, &"b".repeat(64)));
        assert!(assert_transition(&before, &forged).is_err());
        let new_identity = Some(original(2, &"b".repeat(64)));
        assert!(assert_transition(&before, &new_identity).is_ok());
        assert!(assert_transition(&before, &before).is_ok());
    }
}
