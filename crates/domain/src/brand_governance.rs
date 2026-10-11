//! Project-local brand policy and non-authoritative creative preferences.
//! Names recorded on exceptions identify a claim by the author; they do not authenticate identity.
use crate::*;
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrandProfile {
    pub id: Uuid,
    pub label: String,
    pub version: u64,
    pub rules: Vec<BrandRule>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum BrandRule {
    ForbiddenPhrase { id: Uuid, phrase: String },
    AllowedAccents { id: Uuid, colors: Vec<String> },
    RequiredWordmark { id: Uuid, text: String },
}
impl BrandRule {
    pub fn id(&self) -> Uuid {
        match self {
            Self::ForbiddenPhrase { id, .. }
            | Self::AllowedAccents { id, .. }
            | Self::RequiredWordmark { id, .. } => *id,
        }
    }
    fn validate(&self) -> Result<()> {
        match self {
            Self::ForbiddenPhrase { phrase, .. } => design_text(phrase, 128, "forbidden phrase")?,
            Self::AllowedAccents { colors, .. } => {
                if colors.is_empty() || colors.len() > 16 {
                    return Err(DomainError::Invalid(
                        "brand palette needs 1..=16 colors".into(),
                    ));
                }
                let mut seen = BTreeSet::new();
                for color in colors {
                    if color.len() != 7
                        || !color.starts_with('#')
                        || !color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
                        || !seen.insert(color.to_ascii_lowercase())
                    {
                        return Err(DomainError::Invalid(
                            "brand palette needs distinct #RRGGBB colors".into(),
                        ));
                    }
                }
            }
            Self::RequiredWordmark { text, .. } => design_text(text, 64, "required wordmark")?,
        }
        Ok(())
    }
}
impl BrandProfile {
    pub fn validate(&self) -> Result<()> {
        design_text(&self.label, 160, "brand label")?;
        if self.version == 0 || self.rules.is_empty() || self.rules.len() > 64 {
            return Err(DomainError::Invalid(
                "brand requires a positive version and 1..=64 rules".into(),
            ));
        }
        let mut seen = BTreeSet::new();
        for rule in &self.rules {
            rule.validate()?;
            if !seen.insert(rule.id()) {
                return Err(DomainError::Invalid("duplicate brand rule id".into()));
            }
        }
        Ok(())
    }
    /// Content binding, not an attestation of brand ownership.
    pub fn content_digest(&self) -> Result<String> {
        let bytes = serde_json::to_vec(self).map_err(|e| DomainError::Invalid(e.to_string()))?;
        Ok(hex::encode(Sha256::digest(bytes)))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TastePreference {
    pub axis: String,
    pub preference: String,
    pub inferred: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TasteProfile {
    pub id: Uuid,
    pub label: String,
    pub preferences: Vec<TastePreference>,
}
impl TasteProfile {
    pub fn validate(&self) -> Result<()> {
        design_text(&self.label, 160, "taste label")?;
        if self.preferences.len() > 64 {
            return Err(DomainError::Invalid(
                "taste preference budget exceeded".into(),
            ));
        }
        let mut seen = BTreeSet::new();
        for pref in &self.preferences {
            design_text(&pref.axis, 80, "taste axis")?;
            design_text(&pref.preference, 1000, "taste preference")?;
            if !seen.insert(pref.axis.to_lowercase()) {
                return Err(DomainError::Invalid("duplicate taste axis".into()));
            }
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrandException {
    pub id: Uuid,
    pub rule_id: Uuid,
    pub scene_id: Uuid,
    pub brand_sha256: String,
    pub campaign: String,
    pub author: String,
    pub rationale: String,
}
impl BrandException {
    fn validate(&self) -> Result<()> {
        design_text(&self.campaign, 160, "campaign scope")?;
        design_text(&self.author, 160, "exception author")?;
        design_text(&self.rationale, 2000, "exception rationale")?;
        if self.brand_sha256.len() != 64
            || !self
                .brand_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(DomainError::Invalid(
                "exception needs lowercase SHA256 of current brand policy".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreativeDecision {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub author: String,
    pub decision: String,
    pub rationale: String,
}
impl CreativeDecision {
    fn validate(&self, scenes: &[Scene]) -> Result<()> {
        design_text(&self.author, 160, "decision author")?;
        design_text(&self.decision, 1000, "creative decision")?;
        design_text(&self.rationale, 2000, "decision rationale")?;
        if !scenes.iter().any(|scene| scene.id == self.scene_id) {
            return Err(DomainError::Invalid(
                "creative decision targets missing scene".into(),
            ));
        }
        Ok(())
    }
}
/// Validate applicable brand constraints against authored hero configs AND effective scene nodes.
/// Scene-scoped exceptions never mutate an organizational rule or another scene.
pub fn validate_brand_governance(
    profile: &Option<BrandProfile>,
    exceptions: &[BrandException],
    taste: &Option<TasteProfile>,
    decisions: &[CreativeDecision],
    scenes: &[Scene],
    heroes: &[ProductHeroInstance],
) -> Result<()> {
    if let Some(taste) = taste {
        taste.validate()?;
    }
    if exceptions.len() > 128 || decisions.len() > 256 {
        return Err(DomainError::Invalid(
            "governance collection budget exceeded".into(),
        ));
    }
    let mut seen = BTreeSet::new();
    for decision in decisions {
        decision.validate(scenes)?;
        if !seen.insert(decision.id) {
            return Err(DomainError::Invalid("duplicate creative decision".into()));
        }
    }
    let Some(brand) = profile else {
        if !exceptions.is_empty() {
            return Err(DomainError::Invalid(
                "brand exceptions require a brand profile".into(),
            ));
        }
        return Ok(());
    };
    brand.validate()?;
    let digest = brand.content_digest()?;
    seen.clear();
    for exception in exceptions {
        exception.validate()?;
        if !seen.insert(exception.id)
            || !brand
                .rules
                .iter()
                .any(|rule| rule.id() == exception.rule_id)
            || !scenes.iter().any(|scene| scene.id == exception.scene_id)
            || exception.brand_sha256 != digest
        {
            return Err(DomainError::Invalid(
                "exception has duplicate id, missing scene/rule or stale policy digest".into(),
            ));
        }
    }
    for scene in scenes {
        let hero = heroes.iter().find(|item| item.scene_id == scene.id);
        for rule in &brand.rules {
            if exceptions
                .iter()
                .any(|e| e.scene_id == scene.id && e.rule_id == rule.id())
            {
                continue;
            }
            let violated = match rule {
                BrandRule::ForbiddenPhrase { phrase, .. } => {
                    let needle = phrase.to_lowercase();
                    scene
                        .nodes
                        .iter()
                        .filter_map(|node| node.text.as_ref())
                        .any(|text| text.to_lowercase().contains(&needle))
                        || hero.is_some_and(|hero| {
                            [
                                &hero.config.eyebrow,
                                &hero.config.headline,
                                &hero.config.body,
                                &hero.config.wordmark,
                            ]
                            .iter()
                            .any(|text| text.to_lowercase().contains(&needle))
                        })
                }
                BrandRule::AllowedAccents { colors, .. } => hero.is_some_and(|hero| {
                    let allowed =
                        |color: &str| colors.iter().any(|v| v.eq_ignore_ascii_case(color));
                    !allowed(&hero.config.accent)
                        || scene
                            .nodes
                            .iter()
                            .filter(|node| {
                                node.id == hero_node_id(hero.id, "wordmark")
                                    || node.id == hero_node_id(hero.id, "rule")
                            })
                            .filter_map(|node| node.style.fill.as_deref())
                            .any(|fill| !allowed(fill))
                }),
                BrandRule::RequiredWordmark { text, .. } => hero.is_some_and(|hero| {
                    hero.config.wordmark != *text
                        || scene
                            .nodes
                            .iter()
                            .find(|node| node.id == hero_node_id(hero.id, "wordmark"))
                            .is_none_or(|node| node.text.as_deref() != Some(text.as_str()))
                }),
            };
            if violated {
                return Err(DomainError::Invalid(format!(
                    "brand rule {} violated in scene {}; scoped campaign exception required",
                    rule.id(),
                    scene.id
                )));
            }
        }
    }
    Ok(())
}
