//! Declarative deterministic Canvas node generation; no executable plugins or hidden renderer.
//! Project mutations, locks and native rendering remain owned by the existing service/SDK.
use crate::*;
use sha2::{Digest, Sha256};

pub const PROCEDURAL_VERSION: u32 = 1;
pub const PROCEDURAL_MAX_ITEMS: u16 = 64;
pub const PROCEDURAL_MAX_FIELDS: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FieldDistribution {
    Grid,
    Staggered,
    Scatter,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProceduralConfig {
    pub seed: u32,
    pub count: u16,
    pub columns: u8,
    pub distribution: FieldDistribution,
    pub origin_x: u16,
    pub origin_y: u16,
    pub area_width: u16,
    pub area_height: u16,
    pub size: u16,
    pub opacity_percent: u8,
    pub fill: String,
    /// 0 disables sequencing; otherwise each item enters step_frames after the previous.
    #[serde(default)]
    pub reveal_step_frames: u8,
    #[serde(default = "default_reveal_duration")]
    pub reveal_duration_frames: u8,
}
const fn default_reveal_duration() -> u8 {
    12
}
impl Default for ProceduralConfig {
    fn default() -> Self {
        Self {
            seed: 41,
            count: 12,
            columns: 4,
            distribution: FieldDistribution::Scatter,
            origin_x: 180,
            origin_y: 170,
            area_width: 900,
            area_height: 560,
            size: 32,
            opacity_percent: 100,
            fill: "#A5C8DF".into(),
            reveal_step_frames: 0,
            reveal_duration_frames: default_reveal_duration(),
        }
    }
}
impl ProceduralConfig {
    pub fn validate(&self) -> Result<()> {
        if self.count == 0
            || self.count > PROCEDURAL_MAX_ITEMS
            || self.columns == 0
            || self.columns > 16
            || self.size < 4
            || self.size > 128
            || self.opacity_percent == 0
            || self.opacity_percent > 100
            || (self.reveal_step_frames > 0 && self.opacity_percent != 100)
            || self.reveal_step_frames > 10
            || self.reveal_duration_frames == 0
            || self.reveal_duration_frames > 60
            || self.fill.len() != 7
            || !self.fill.starts_with('#')
            || !self.fill.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
        {
            return Err(DomainError::Invalid(
                "procedural item, style or CPU budget exceeded".into(),
            ));
        }
        if u32::from(self.origin_x) + u32::from(self.area_width) > 1920
            || u32::from(self.origin_y) + u32::from(self.area_height) > 1080
        {
            return Err(DomainError::Invalid(
                "procedural field exceeds 1920x1080 authored stage".into(),
            ));
        }
        let columns = u32::from(self.columns).min(u32::from(self.count));
        let rows = u32::from(self.count).div_ceil(columns);
        if u32::from(self.area_width) / columns < u32::from(self.size)
            || u32::from(self.area_height) / rows < u32::from(self.size)
        {
            return Err(DomainError::Invalid(
                "procedural item does not fit its bounded cell".into(),
            ));
        }
        Ok(())
    }
}

/// Uses only unsigned 32-bit wrapping integer arithmetic, mirrored with JS Math.imul.
/// No runtime randomness, clock, locale, floating trigonometry or global RNG.
pub fn procedural_hash(seed: u32, index: u32, salt: u32) -> u32 {
    let mut value = seed ^ index.wrapping_mul(0x9e37_79b9) ^ salt;
    value ^= value >> 16;
    value = value.wrapping_mul(0x7feb_352d);
    value ^= value >> 15;
    value = value.wrapping_mul(0x846c_a68b);
    value ^ (value >> 16)
}
pub fn procedural_node_id(id: Uuid, index: u32) -> Uuid {
    let mut hash = Sha256::new();
    hash.update(b"motionwright.procedural.v1\0");
    hash.update(id.as_bytes());
    hash.update(index.to_le_bytes());
    let bytes: [u8; 32] = hash.finalize().into();
    let mut result = [0u8; 16];
    result.copy_from_slice(&bytes[..16]);
    result[6] = (result[6] & 0x0f) | 0x80;
    result[8] = (result[8] & 0x3f) | 0x80;
    Uuid::from_bytes(result)
}
/// Generates real, individually editable native Canvas nodes.
pub fn realize_procedural_field(id: Uuid, config: &ProceduralConfig) -> Result<Vec<CanvasNode>> {
    config.validate()?; // all limits checked before creating any nodes
    let columns = u32::from(config.columns).min(u32::from(config.count));
    let rows = u32::from(config.count).div_ceil(columns);
    let cell_w = u32::from(config.area_width) / columns;
    let cell_h = u32::from(config.area_height) / rows;
    let size = u32::from(config.size);
    let mut nodes = Vec::with_capacity(usize::from(config.count));
    for index in 0..u32::from(config.count) {
        let col = index % columns;
        let row = index / columns;
        let free_x = cell_w - size;
        let free_y = cell_h - size;
        let (dx, dy) = match config.distribution {
            FieldDistribution::Grid => (free_x / 2, free_y / 2),
            FieldDistribution::Staggered => {
                let quarter = free_x / 4;
                let mid = free_x / 2;
                (
                    if row % 2 == 0 {
                        mid + quarter
                    } else {
                        mid - quarter
                    },
                    free_y / 2,
                )
            }
            FieldDistribution::Scatter => (
                procedural_hash(config.seed, index, 0x71a5_029b) % (free_x + 1),
                procedural_hash(config.seed, index, 0xda39_c617) % (free_y + 1),
            ),
        };
        let x = u32::from(config.origin_x) + col * cell_w + dx;
        let y = u32::from(config.origin_y) + row * cell_h + dy;
        let node = CanvasNode {
            id: procedural_node_id(id, index),
            name: format!("ProceduralField / item {index:03}"),
            kind: "rectangle".into(),
            parent_id: None,
            x: f64::from(x),
            y: f64::from(y),
            width: f64::from(size),
            height: f64::from(size),
            rotation_deg: 0.0,
            opacity: f64::from(config.opacity_percent) / 100.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            // Native Film admits up to 32 layers: repetition shares one underlying layer.
            z_index: -32,
            style: NodeStyle {
                fill: Some(config.fill.clone()),
                ..Default::default()
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: if config.reveal_step_frames == 0 {
                vec![]
            } else {
                let start = index * u32::from(config.reveal_step_frames);
                let end = start + u32::from(config.reveal_duration_frames);
                // Mirror the admitted Hero entrance grammar: synchronized hold
                // then OutCubic Y+opacity, rather than a silently unsupported
                // opacity-only linear motion. Fixed position is retained.
                let mut keys = vec![];
                for property in [MotionProperty::Y, MotionProperty::Opacity] {
                    let value = if property == MotionProperty::Y {
                        f64::from(y) + 12.0
                    } else {
                        0.0
                    };
                    keys.push(CanvasKeyframe {
                        at: RationalTime::ZERO,
                        property,
                        value,
                        interpolation: MotionInterpolation::Hold,
                    });
                    if start > 0 {
                        keys.push(CanvasKeyframe {
                            at: RationalTime::new(i64::from(start), 30).expect("bounded timeline"),
                            property,
                            value,
                            interpolation: MotionInterpolation::Hold,
                        });
                    }
                    keys.push(CanvasKeyframe {
                        at: RationalTime::new(i64::from(end), 30).expect("bounded timeline"),
                        property,
                        value: if property == MotionProperty::Y {
                            f64::from(y)
                        } else {
                            1.0
                        },
                        interpolation: MotionInterpolation::EaseOutCubic,
                    });
                }
                keys
            },
        };
        node.validate()?;
        nodes.push(node);
    }
    Ok(nodes)
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProceduralFieldInstance {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub generator_version: u32,
    pub config: ProceduralConfig,
    /// Three-way merge base, not a copy of human-edited nodes.
    pub baseline: Vec<CanvasNode>,
}
impl ProceduralFieldInstance {
    pub fn validate(&self) -> Result<()> {
        if self.generator_version != PROCEDURAL_VERSION
            || self.baseline != realize_procedural_field(self.id, &self.config)?
        {
            return Err(DomainError::Invalid(
                "procedural baseline/version mismatch".into(),
            ));
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn upsert_procedural_field(
        &mut self,
        id: Uuid,
        scene_id: Uuid,
        config: &ProceduralConfig,
    ) -> Result<()> {
        self.ensure_unlocked(
            &self.resource_key(),
            &[LockKind::Content, LockKind::Position, LockKind::Style],
        )?;
        self.ensure_unlocked(
            &format!("scene:{scene_id}"),
            &[
                LockKind::Content,
                LockKind::Position,
                LockKind::Style,
                LockKind::Renderer,
            ],
        )?;
        let index = self
            .scenes
            .iter()
            .position(|scene| scene.id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{scene_id}")))?;
        if self.scenes[index].renderer != RendererKind::MotionCanvas {
            return Err(DomainError::Invalid(
                "procedural fields require a Motion Canvas scene".into(),
            ));
        }
        // Evaluate temporal budget before constructing any nodes or hashing identities.
        config.validate()?;
        if config.reveal_step_frames > 0 {
            let final_frame = (u32::from(config.count) - 1) * u32::from(config.reveal_step_frames)
                + u32::from(config.reveal_duration_frames);
            let end =
                RationalTime::new(i64::from(final_frame), 30).expect("bounded procedural timeline");
            if end >= self.scenes[index].duration {
                return Err(DomainError::Invalid(
                    "procedural sequence exceeds scene duration; shorten step/duration or extend scene".into()
                ));
            }
        }
        let baseline = realize_procedural_field(id, config)?;
        let existing = self
            .production_design
            .procedural_fields
            .iter()
            .find(|field| field.id == id)
            .cloned();
        let mut nodes = self.scenes[index].nodes.clone();
        if let Some(old) = existing {
            old.validate()?;
            if old.scene_id != scene_id {
                return Err(DomainError::Invalid(
                    "a procedural field cannot move scenes via update".into(),
                ));
            }
            let common = old.baseline.len().min(baseline.len());
            nodes = merge_component_nodes(&old.baseline[..common], &nodes, &baseline[..common])?;
            if old.baseline.len() > common {
                for removed in &old.baseline[common..] {
                    let live = nodes.iter().find(|node| node.id == removed.id);
                    if live != Some(removed) {
                        return Err(DomainError::Invalid(
                            "cannot shrink procedural field over human edits or deleted nodes; detach first".into(),
                        ));
                    }
                    if nodes.iter().any(|node| {
                        node.parent_id == Some(removed.id)
                            || node.relations.iter().any(|rel| rel.target_id == removed.id)
                    }) {
                        return Err(DomainError::Invalid(
                            "procedural field shrink would orphan references".into(),
                        ));
                    }
                }
                let discarded: BTreeSet<_> =
                    old.baseline[common..].iter().map(|node| node.id).collect();
                nodes.retain(|node| !discarded.contains(&node.id));
            }
        } else if self.production_design.procedural_fields.len() >= PROCEDURAL_MAX_FIELDS
            || self
                .production_design
                .procedural_fields
                .iter()
                .any(|field| field.scene_id == scene_id)
        {
            return Err(DomainError::Invalid(
                "one procedural field per scene; at most 16 per project".into(),
            ));
        }
        let current: BTreeSet<_> = nodes.iter().map(|node| node.id).collect();
        let common = existing_count(&self.production_design.procedural_fields, id)
            .unwrap_or(0)
            .min(baseline.len());
        for node in &baseline[common..] {
            if current.contains(&node.id) {
                return Err(DomainError::Invalid(
                    "procedural node identity collides with authored scene".into(),
                ));
            }
            nodes.push(node.clone());
        }
        let instance = ProceduralFieldInstance {
            id,
            scene_id,
            generator_version: PROCEDURAL_VERSION,
            config: config.clone(),
            baseline,
        };
        instance.validate()?;
        self.scenes[index].nodes = nodes;
        self.scenes[index].status = SceneStatus::Draft;
        if let Some(field) = self
            .production_design
            .procedural_fields
            .iter_mut()
            .find(|field| field.id == id)
        {
            *field = instance;
        } else {
            self.production_design.procedural_fields.push(instance);
        }
        Ok(())
    }

    pub(crate) fn detach_procedural_field(&mut self, id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let existing = self
            .production_design
            .procedural_fields
            .iter()
            .find(|field| field.id == id)
            .ok_or_else(|| DomainError::NotFound(format!("procedural field:{id}")))?;
        self.ensure_unlocked(
            &format!("scene:{}", existing.scene_id),
            &[LockKind::Content],
        )?;
        // Matches hero detach: keep every authored/edited node, remove only the generator link.
        self.production_design
            .procedural_fields
            .retain(|field| field.id != id);
        Ok(())
    }
}
fn existing_count(fields: &[ProceduralFieldInstance], id: Uuid) -> Option<usize> {
    fields
        .iter()
        .find(|field| field.id == id)
        .map(|field| field.baseline.len())
}
