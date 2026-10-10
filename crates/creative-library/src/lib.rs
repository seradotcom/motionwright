//! Original first-party creative recipes, not a renderer or another execution backend.
//! All outputs are editable, bounded native documents with stable component identities.
mod catalog;
mod clock;
mod data;
mod distillation;
mod fidelity;
mod instance;
mod procedural;
mod profiles;
mod realization;
mod repair;
mod skills;
mod sound;
mod stage;
mod wav;
pub use catalog::*;
pub use clock::*;
pub use data::*;
pub use distillation::*;
pub use fidelity::*;
pub use instance::*;
pub use motionwright_hyperframes_profile as native;
pub use procedural::*;
pub use profiles::*;
pub use realization::*;
pub use repair::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
pub use skills::*;
pub use sound::*;
pub use stage::*;
use thiserror::Error;
use uuid::Uuid;
pub use wav::*;

#[derive(Debug, Clone, Error, PartialEq)]
#[error("{0}")]
pub struct CraftError(pub String);
pub type Result<T> = std::result::Result<T, CraftError>;
pub(crate) fn check(condition: bool, reason: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(CraftError(reason.into()))
    }
}
pub(crate) fn text(value: &str, max: usize, label: &str) -> Result<()> {
    check(
        !value.trim().is_empty()
            && value.len() <= max
            && !value.chars().any(|c| c.is_control() && c != '\n'),
        label,
    )
}
pub fn canonical_digest(value: &impl Serialize) -> Result<String> {
    Ok(hex::encode(Sha256::digest(
        serde_json::to_vec(value).map_err(|e| CraftError(e.to_string()))?,
    )))
}
pub fn stable_id(instance: Uuid, role: &str) -> Uuid {
    let mut hash = Sha256::new();
    hash.update(b"motionwright.creative.component.v1\0");
    hash.update(instance.as_bytes());
    hash.update(role.as_bytes());
    let bytes: [u8; 32] = hash.finalize().into();
    let mut id = [0; 16];
    id.copy_from_slice(&bytes[..16]);
    id[6] = (id[6] & 15) | 128;
    id[8] = (id[8] & 63) | 128;
    Uuid::from_bytes(id)
}
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Locale {
    En,
    Es,
    De,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CopyPack {
    pub eyebrow: String,
    pub headline: String,
    pub body: String,
    pub label_a: String,
    pub label_b: String,
    pub disclosure: String,
}
impl CopyPack {
    pub fn editorial(locale: Locale) -> Self {
        match locale {
            Locale::En => Self {
                eyebrow: "CREATIVE PRODUCTION".into(),
                headline: "Make the work.\nKeep the craft.".into(),
                body: "Every revision preserves the decisions that matter.".into(),
                label_a: "Before".into(),
                label_b: "After".into(),
                disclosure: "GRAPHIC STUDY / NOT PRODUCT EVIDENCE".into(),
            },
            Locale::Es => Self {
                eyebrow: "PRODUCCION CREATIVA".into(),
                headline: "Crea la obra.\nConserva tu criterio.".into(),
                body: "Cada revision conserva las decisiones importantes.".into(),
                label_a: "Antes".into(),
                label_b: "Despues".into(),
                disclosure: "ESTUDIO GRAFICO / NO ES EVIDENCIA DEL PRODUCTO".into(),
            },
            Locale::De => Self {
                eyebrow: "KREATIVE PRODUKTION".into(),
                headline: "Gestalte das Werk.\nBewahre die Idee.".into(),
                body: "Jede Version bewahrt die wichtigen Entscheidungen.".into(),
                label_a: "Vorher".into(),
                label_b: "Nachher".into(),
                disclosure: "GRAFIKSTUDIE / KEIN PRODUKTNACHWEIS".into(),
            },
        }
    }
    pub fn validate(&self) -> Result<()> {
        for (label, value, max) in [
            ("eyebrow", &self.eyebrow, 100),
            ("headline", &self.headline, 240),
            ("body", &self.body, 800),
            ("first label", &self.label_a, 80),
            ("second label", &self.label_b, 80),
            ("disclosure", &self.disclosure, 200),
        ] {
            text(value, max, &format!("Invalid {label} copy budget"))?;
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ComponentRequest {
    pub instance_id: Uuid,
    pub recipe: RecipeId,
    pub version: u32,
    pub output: native::Canvas,
    pub copy: CopyPack,
    pub locale: Locale,
    pub seed: u64,
    pub motion: bool,
    pub data: Option<DataSeries>,
    pub primary_asset: Option<native::Asset>,
    pub secondary_asset: Option<native::Asset>,
    pub procedural: ProceduralOptions,
}
impl ComponentRequest {
    pub fn validate(&self) -> Result<()> {
        check(
            self.version == 1,
            "Component definition version is not admitted",
        )?;
        self.copy.validate()?;
        self.procedural.validate()?;
        let output = &self.output;
        check(
            output.width >= 320
                && output.height >= 320
                && output.width <= 4096
                && output.height <= 4096,
            "Component output is outside bounded design dimensions",
        )?;
        check(
            output.frames >= 30
                && output.frames <= 3600
                && output.rate.num > 0
                && output.rate.den > 0,
            "Component needs a bounded, positive output clock",
        )?;
        if let Some(data) = &self.data {
            data.validate()?;
        }
        for asset in [&self.primary_asset, &self.secondary_asset]
            .into_iter()
            .flatten()
        {
            check(
                asset.rights.use_authorized,
                "Component assets need explicit usage rights before realization",
            )?;
        }
        Ok(())
    }
}
