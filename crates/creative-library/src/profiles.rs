use crate::*;
use std::collections::BTreeSet;
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(rename_all = "snake_case")]
pub enum ColorRole {
    Background,
    Surface,
    Text,
    MutedText,
    Accent,
    Secondary,
    Warning,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BrandColor {
    pub name: String,
    pub value: String,
    pub role: ColorRole,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BrandProfile {
    pub id: Uuid,
    pub revision: u32,
    pub name: String,
    pub colors: Vec<BrandColor>,
    pub font: native::Font,
    pub font_asset: Option<native::Asset>,
    pub logo_asset: Option<native::Asset>,
    pub body_weight: u16,
    pub display_weight: u16,
    pub minimum_body_size: f64,
    pub minimum_margin_ratio: f64,
    pub require_product_capture_provenance: bool,
    pub forbidden_claims: Vec<String>,
    pub allowed_locales: Vec<Locale>,
    pub source_note: String,
}
impl BrandProfile {
    pub fn neutral(id: Uuid) -> Self {
        Self{id,revision:1,name:"Motionwright original editorial study".into(),colors:vec![
        BrandColor{name:"Canvas".into(),value:"#111922".into(),role:ColorRole::Background},
        BrandColor{name:"Surface".into(),value:"#21313F".into(),role:ColorRole::Surface},
        BrandColor{name:"Ink".into(),value:"#F2F4F3".into(),role:ColorRole::Text},
        BrandColor{name:"Context".into(),value:"#AFBCC7".into(),role:ColorRole::MutedText},
        BrandColor{name:"Accent".into(),value:"#90C9DF".into(),role:ColorRole::Accent},
        BrandColor{name:"Secondary".into(),value:"#D9B77D".into(),role:ColorRole::Secondary},
        BrandColor{name:"Warning".into(),value:"#EDBEA3".into(),role:ColorRole::Warning},
    ],font:native::Font::Sans,font_asset:None,logo_asset:None,body_weight:400,display_weight:600,minimum_body_size:18.0,minimum_margin_ratio:0.065,require_product_capture_provenance:true,forbidden_claims:vec![],allowed_locales:vec![Locale::En,Locale::Es,Locale::De],source_note:"Original first-party recipe palette. Not an assertion of another company's brand rights.".into()}
    }
    pub fn validate(&self) -> Result<()> {
        text(&self.name, 160, "Brand name is required")?;
        text(&self.source_note, 2000, "Brand provenance note is required")?;
        check(
            self.revision > 0 && self.colors.len() <= 16,
            "Brand revision or color budget is invalid",
        )?;
        let mut roles = BTreeSet::new();
        let mut names = BTreeSet::new();
        for color in &self.colors {
            text(&color.name, 80, "Brand color name is invalid")?;
            check(
                roles.insert(color.role) && names.insert(&color.name),
                "Brand color roles and names must be unique",
            )?;
            rgb(&color.value)?;
        }
        for role in [
            ColorRole::Background,
            ColorRole::Surface,
            ColorRole::Text,
            ColorRole::MutedText,
            ColorRole::Accent,
            ColorRole::Secondary,
        ] {
            check(
                roles.contains(&role),
                "Brand is missing a required semantic color role",
            )?;
        }
        check(
            (100..=900).contains(&self.body_weight) && (100..=900).contains(&self.display_weight),
            "Brand weights are outside supported typography",
        )?;
        check(
            self.minimum_body_size.is_finite() && (12.0..=72.0).contains(&self.minimum_body_size),
            "Brand minimum body size is outside its admissible design budget",
        )?;
        check(
            self.minimum_margin_ratio.is_finite()
                && (0.0..=0.20).contains(&self.minimum_margin_ratio),
            "Brand margin ratio must remain within [0,0.2]",
        )?;
        check(
            !self.allowed_locales.is_empty() && self.allowed_locales.len() <= 3,
            "Brand requires a bounded locale policy",
        )?;
        check(
            self.forbidden_claims.len() <= 64,
            "Too many forbidden claims",
        )?;
        for claim in &self.forbidden_claims {
            text(claim, 300, "Invalid forbidden claim")?;
        }
        match &self.font {
            native::Font::Asset { asset_id, .. } => check(
                self.font_asset.as_ref().is_some_and(|asset| {
                    asset.id == *asset_id
                        && asset.kind == native::AssetKind::Woff2
                        && asset.rights.use_authorized
                }),
                "Brand font must reference an explicitly licensed local font asset",
            )?,
            _ => check(
                self.font_asset.is_none(),
                "An unused brand font asset must not be silently attached",
            )?,
        }
        if let Some(logo) = &self.logo_asset {
            check(
                matches!(logo.kind, native::AssetKind::Png | native::AssetKind::Jpeg)
                    && logo.rights.use_authorized,
                "Logo needs a supported digest-bound asset and usage authorization",
            )?;
        }
        Ok(())
    }
    pub fn color(&self, role: ColorRole) -> &str {
        self.colors
            .iter()
            .find(|c| c.role == role)
            .map(|c| c.value.as_str())
            .unwrap_or("#FFFFFF")
    }
    pub fn check_copy(&self, copy: &CopyPack, locale: Locale) -> Result<()> {
        check(
            self.allowed_locales.contains(&locale),
            "This language is not enabled by the selected brand profile",
        )?;
        let corpus = format!(
            "{}\n{}\n{}\n{}\n{}",
            copy.eyebrow, copy.headline, copy.body, copy.label_a, copy.label_b
        )
        .to_lowercase();
        for denied in &self.forbidden_claims {
            check(
                !corpus.contains(&denied.to_lowercase()),
                "Copy violates an explicit brand claim restriction",
            )?;
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Avoidance {
    DecorativeDashboard,
    ArbitraryCameraSpin,
    UnreadableType,
    UnexplainedParticles,
    EveryWordAnimated,
    UnlabeledSyntheticCapture,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct TasteProfile {
    pub id: Uuid,
    pub name: String,
    pub revision: u32,
    pub motion_energy: u8,
    pub density: u8,
    pub contrast: u8,
    pub minimum_hold_ms: u32,
    pub preferred_kit: KitId,
    pub avoid: Vec<Avoidance>,
    pub reference_constraints: Vec<String>,
}
impl TasteProfile {
    pub fn editorial(id: Uuid) -> Self {
        Self {
            id,
            name: "Editorial restraint".into(),
            revision: 1,
            motion_energy: 35,
            density: 35,
            contrast: 70,
            minimum_hold_ms: 1100,
            preferred_kit: KitId::EditorialPrecision,
            avoid: vec![
                Avoidance::DecorativeDashboard,
                Avoidance::UnreadableType,
                Avoidance::EveryWordAnimated,
            ],
            reference_constraints: vec![
                "Hierarchy must remain readable while elements move".into(),
                "A transition should explain a change in the argument".into(),
            ],
        }
    }
    pub fn validate(&self) -> Result<()> {
        text(&self.name, 160, "Taste profile name is required")?;
        check(
            self.revision > 0
                && self.motion_energy <= 100
                && self.density <= 100
                && self.contrast <= 100
                && self.minimum_hold_ms <= 10000,
            "Taste values exceed their explicit preference ranges",
        )?;
        check(
            self.avoid.len() <= 6 && self.reference_constraints.len() <= 64,
            "Taste profile collection budget exceeded",
        )?;
        for rule in &self.reference_constraints {
            text(rule, 2000, "Reference interpretation is invalid")?;
        }
        Ok(())
    }
    pub fn entrance_fraction(&self) -> f64 {
        0.18 + f64::from(self.motion_energy) / 1000.0
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BrandException {
    pub id: Uuid,
    pub brand_id: Uuid,
    pub component_id: Uuid,
    pub proposed_source_sha256: String,
    pub constraint: String,
    pub justification: String,
    pub reviewer: String,
}
impl BrandException {
    pub fn validate(&self) -> Result<()> {
        for value in [&self.constraint, &self.justification, &self.reviewer] {
            text(
                value,
                2000,
                "A brand exception needs an explicit constraint, justification and reviewer",
            )?;
        }
        check(
            valid_sha(&self.proposed_source_sha256),
            "Brand exception must bind the exact proposed source",
        )
    }
}
pub fn valid_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn rgb(value: &str) -> Result<[u8; 3]> {
    check(
        value.len() == 7
            && value.starts_with('#')
            && value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit),
        "Brand colors require exact opaque #RRGGBB values",
    )?;
    Ok([
        u8::from_str_radix(&value[1..3], 16).map_err(|_| CraftError("Invalid color".into()))?,
        u8::from_str_radix(&value[3..5], 16).map_err(|_| CraftError("Invalid color".into()))?,
        u8::from_str_radix(&value[5..7], 16).map_err(|_| CraftError("Invalid color".into()))?,
    ])
}
pub fn contrast_ratio(a: &str, b: &str) -> Result<f64> {
    fn luminance(rgb: [u8; 3]) -> f64 {
        let linear = rgb.map(|channel| {
            let n = f64::from(channel) / 255.0;
            if n <= 0.04045 {
                n / 12.92
            } else {
                ((n + 0.055) / 1.055).powf(2.4)
            }
        });
        linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
    }
    let a = luminance(rgb(a)?);
    let b = luminance(rgb(b)?);
    Ok((a.max(b) + 0.05) / (a.min(b) + 0.05))
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ReferenceStudy {
    pub id: Uuid,
    pub title: String,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub author: String,
    pub usage_terms: String,
    pub allowed_use: ReferenceUse,
    pub observed_constraints: Vec<String>,
    pub excluded_elements: Vec<String>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceUse {
    AnalyzeOnly,
    LicensedReuse,
    OwnedOriginal,
}
impl ReferenceStudy {
    pub fn validate(&self) -> Result<()> {
        for value in [&self.title, &self.author, &self.usage_terms] {
            text(
                value,
                2000,
                "Reference source needs attribution and usage terms",
            )?;
        }
        check(
            valid_sha(&self.source_sha256),
            "Reference source digest is invalid",
        )?;
        check(
            !self.observed_constraints.is_empty()
                && self.observed_constraints.len() <= 32
                && self.excluded_elements.len() <= 32,
            "Reference grammar must contain bounded observations and exclusions",
        )?;
        for value in self
            .observed_constraints
            .iter()
            .chain(&self.excluded_elements)
        {
            text(value, 2000, "Reference analysis is out of bounds")?;
        }
        Ok(())
    }
}
