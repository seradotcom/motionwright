//! Bounded project-local measured-data bindings. No fabricated values, ranges,
//! auto axes, remote spreadsheet execution, or inferred data rights.
//! Content SHA-256 is the imported Asset identity; source byte verification is
//! owned by the existing import/asset pipeline, never claimed by this module.
use crate::*;
use std::collections::BTreeSet;

pub const MEASURED_DATA_SCHEMA: &str = "motionwright.measured-table/1";
const MAX_MILLI: i64 = 1_000_000_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MeasuredUnit {
    Count,
    Percent,
    Seconds,
    Milliseconds,
    Bytes,
}
impl MeasuredUnit {
    fn suffix(self) -> &'static str {
        match self {
            Self::Count => "",
            Self::Percent => "%",
            Self::Seconds => " s",
            Self::Milliseconds => " ms",
            Self::Bytes => " B",
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeasuredDatum {
    pub key: String,
    pub label: String,
    /// Signed thousandths of the physical unit. No binary floats in source data.
    pub milli: i64,
    pub unit: MeasuredUnit,
}
impl MeasuredDatum {
    fn validate(&self) -> Result<()> {
        if self.key.len() > 48
            || self.key.is_empty()
            || !self.key.as_bytes()[0].is_ascii_lowercase()
            || !self
                .key
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_')
            || self.label.trim().is_empty()
            || self.label.len() > 120
            || self.label.chars().any(char::is_control)
            || !(-MAX_MILLI..=MAX_MILLI).contains(&self.milli)
        {
            return Err(DomainError::Invalid(
                "measured datum key, label or value out of bounds".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeasuredTable {
    pub schema: String,
    pub id: Uuid,
    pub label: String,
    pub version: u32,
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    /// One numeric datum per row, as in a one-key/one-measure JSON or CSV table.
    /// This is an explicit declared snapshot, NOT a live or audited data feed.
    pub rows: Vec<MeasuredDatum>,
}
impl MeasuredTable {
    pub fn validate(&self, assets: &[Asset]) -> Result<()> {
        if self.schema != MEASURED_DATA_SCHEMA
            || self.version == 0
            || self.label.trim().is_empty()
            || self.label.len() > 160
            || self.label.chars().any(char::is_control)
            || self.rows.is_empty()
            || self.rows.len() > 64
            || self.source_sha256.len() != 64
            || !self
                .source_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(DomainError::Invalid(
                "measured source/schema/budget invalid".into(),
            ));
        }
        let source = assets
            .iter()
            .find(|asset| asset.id == self.source_asset_id)
            .ok_or_else(|| {
                DomainError::Invalid("measured table requires an imported source asset".into())
            })?;
        if !["application/json", "text/csv"].contains(&source.media_type.as_str())
            || source.content_sha256.as_deref() != Some(self.source_sha256.as_str())
        {
            return Err(DomainError::Invalid(
                "measured table SHA-256 must match an imported JSON/CSV asset".into(),
            ));
        }
        let mut names = BTreeSet::new();
        for row in &self.rows {
            row.validate()?;
            if !names.insert(&row.key) {
                return Err(DomainError::Invalid("duplicate measured datum key".into()));
            }
        }
        Ok(())
    }
    fn row(&self, key: &str) -> Result<&MeasuredDatum> {
        self.rows
            .iter()
            .find(|row| row.key == key)
            .ok_or_else(|| DomainError::Invalid(format!("unbound measured datum key: {key}")))
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum MeasuredView {
    Counter {
        decimals: u8,
    },
    Label {
        decimals: u8,
    },
    /// A width-only bar. The axis bounds must be explicitly authored.
    /// It never infers a zero origin, a unit conversion, or an axis label.
    Bar {
        axis_min_milli: i64,
        axis_max_milli: i64,
        max_width_px: u16,
    },
}
impl MeasuredView {
    fn validate(&self) -> Result<()> {
        match self {
            Self::Counter { decimals } | Self::Label { decimals } if *decimals <= 3 => Ok(()),
            Self::Bar {
                axis_min_milli,
                axis_max_milli,
                max_width_px,
            } if axis_min_milli < axis_max_milli
                && *axis_min_milli >= -MAX_MILLI
                && *axis_max_milli <= MAX_MILLI
                && (16..=1920).contains(max_width_px) =>
            {
                Ok(())
            }
            _ => Err(DomainError::Invalid(
                "invalid measured presentation or explicit bar axis".into(),
            )),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum MeasuredOutput {
    Text { value: String },
    Bar { width_px: u16 },
}
fn format_milli(value: i64, decimals: u8) -> String {
    let divisor = 10_i128.pow(u32::from(3 - decimals));
    let positive = i128::from(value).abs();
    let rounded = (positive + divisor / 2) / divisor;
    let base = 10_i128.pow(u32::from(decimals));
    let sign = if value < 0 && rounded != 0 { "-" } else { "" };
    if decimals == 0 {
        return format!("{sign}{rounded}");
    }
    format!(
        "{sign}{}.{:0width$}",
        rounded / base,
        rounded % base,
        width = decimals as usize
    )
}
pub fn measured_output(datum: &MeasuredDatum, view: &MeasuredView) -> Result<MeasuredOutput> {
    datum.validate()?;
    view.validate()?;
    match view {
        MeasuredView::Counter { decimals } | MeasuredView::Label { decimals } => {
            let rendered = format!(
                "{}{}",
                format_milli(datum.milli, *decimals),
                datum.unit.suffix()
            );
            Ok(MeasuredOutput::Text {
                value: if matches!(view, MeasuredView::Label { .. }) {
                    format!("{} · {}", datum.label, rendered)
                } else {
                    rendered
                },
            })
        }
        MeasuredView::Bar {
            axis_min_milli,
            axis_max_milli,
            max_width_px,
        } => {
            if datum.milli <= *axis_min_milli || datum.milli > *axis_max_milli {
                return Err(DomainError::Invalid(
                    "measured bar data outside explicitly declared axis; cannot invent/clamp it"
                        .into(),
                ));
            }
            let numer = i128::from(datum.milli - *axis_min_milli);
            let denom = i128::from(*axis_max_milli - *axis_min_milli);
            let width = (i128::from(*max_width_px) * numer + denom / 2) / denom;
            let width = u16::try_from(width)
                .map_err(|_| DomainError::Invalid("measured bar overflow".into()))?;
            Ok(MeasuredOutput::Bar {
                width_px: width.max(1),
            })
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeasuredBinding {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub node_id: Uuid,
    pub table_id: Uuid,
    pub datum_key: String,
    pub view: MeasuredView,
    /// Expected output under current table. Allows manual changes to other
    /// properties, but rejects silent replacement of a bound text/width.
    pub baseline: MeasuredOutput,
}
impl MeasuredBinding {
    fn expected(&self, tables: &[MeasuredTable]) -> Result<MeasuredOutput> {
        let table = tables
            .iter()
            .find(|table| table.id == self.table_id)
            .ok_or_else(|| {
                DomainError::Invalid("measured binding references missing table".into())
            })?;
        measured_output(table.row(&self.datum_key)?, &self.view)
    }
    fn current_node<'a>(&self, scenes: &'a [Scene]) -> Result<&'a CanvasNode> {
        let scene = scenes
            .iter()
            .find(|scene| scene.id == self.scene_id && scene.renderer == RendererKind::MotionCanvas)
            .ok_or_else(|| {
                DomainError::Invalid("measured binding requires Motion Canvas scene".into())
            })?;
        scene
            .nodes
            .iter()
            .find(|node| node.id == self.node_id)
            .ok_or_else(|| {
                DomainError::Invalid("measured binding targets a missing Canvas node".into())
            })
    }
    fn ensure_output(&self, node: &CanvasNode) -> Result<()> {
        let matches = match &self.baseline {
            MeasuredOutput::Text { value } => {
                node.kind == "text" && node.text.as_deref() == Some(value)
            }
            MeasuredOutput::Bar { width_px } => {
                node.kind == "rectangle" && node.width == f64::from(*width_px)
            }
        };
        if !matches {
            return Err(DomainError::Invalid(
                "bound Canvas metric was edited; detach/rebind before replacing its data".into(),
            ));
        }
        Ok(())
    }
    fn ensure_writable(&self, project: &Project) -> Result<()> {
        let (lock_kind, node_property) = match self.view {
            MeasuredView::Counter { .. } | MeasuredView::Label { .. } => {
                (LockKind::Content, NodeProperty::Text)
            }
            MeasuredView::Bar { .. } => (LockKind::Position, NodeProperty::Size),
        };
        project.ensure_unlocked(&format!("scene:{}", self.scene_id), &[lock_kind])?;
        let node = self.current_node(&project.scenes)?;
        if node.property_locks.contains(&node_property) {
            return Err(DomainError::Locked(format!(
                "node:{} measured output",
                self.node_id
            )));
        }
        Ok(())
    }
}
fn apply_output(
    binding: &MeasuredBinding,
    scenes: &mut [Scene],
    output: &MeasuredOutput,
) -> Result<()> {
    let scene = scenes
        .iter_mut()
        .find(|scene| scene.id == binding.scene_id)
        .ok_or_else(|| DomainError::NotFound(format!("scene:{}", binding.scene_id)))?;
    let node = scene
        .nodes
        .iter_mut()
        .find(|node| node.id == binding.node_id)
        .ok_or_else(|| DomainError::NotFound(format!("node:{}", binding.node_id)))?;
    match output {
        MeasuredOutput::Text { value } => {
            if node.kind != "text" {
                return Err(DomainError::Invalid(
                    "metric counter/label requires text node".into(),
                ));
            }
            node.text = Some(value.clone());
        }
        MeasuredOutput::Bar { width_px } => {
            if node.kind != "rectangle" {
                return Err(DomainError::Invalid(
                    "metric bar requires rectangle node".into(),
                ));
            }
            node.width = f64::from(*width_px);
        }
    }
    scene.status = SceneStatus::Draft;
    Ok(())
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeasuredDataImpact {
    pub table_id: Uuid,
    pub version: u32,
    pub changed_keys: Vec<String>,
    pub scene_ids: Vec<Uuid>,
    pub deliverable_ids: Vec<Uuid>,
}
impl ProductionDesign {
    pub(crate) fn validate_measured_data(&self, scenes: &[Scene], assets: &[Asset]) -> Result<()> {
        if self.measured_tables.len() > 16 || self.measured_bindings.len() > 64 {
            return Err(DomainError::Invalid(
                "measured data project budget exceeded".into(),
            ));
        }
        let mut table_ids = BTreeSet::new();
        for table in &self.measured_tables {
            table.validate(assets)?;
            if !table_ids.insert(table.id) {
                return Err(DomainError::Invalid(
                    "duplicate measured table identity".into(),
                ));
            }
        }
        let mut bindings = BTreeSet::new();
        let mut targets = BTreeSet::new();
        for binding in &self.measured_bindings {
            if !bindings.insert(binding.id) || !targets.insert((binding.scene_id, binding.node_id))
            {
                return Err(DomainError::Invalid(
                    "duplicate measured binding or target".into(),
                ));
            }
            binding.view.validate()?;
            if binding.baseline != binding.expected(&self.measured_tables)? {
                return Err(DomainError::Invalid(
                    "stale measured format/source baseline".into(),
                ));
            }
            binding.ensure_output(binding.current_node(scenes)?)?;
        }
        if let Some(impact) = &self.last_measured_impact
            && (impact.version == 0
                || !table_ids.contains(&impact.table_id)
                || impact.changed_keys.len() > 64
                || impact.scene_ids.len() > 64
                || impact.deliverable_ids.len() > 64
                || impact
                    .scene_ids
                    .iter()
                    .any(|id| !scenes.iter().any(|scene| scene.id == *id)))
        {
            return Err(DomainError::Invalid(
                "invalid measured dependency impact".into(),
            ));
        }
        Ok(())
    }
}
impl Project {
    pub(crate) fn upsert_measured_table(&mut self, table: &MeasuredTable) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let mut next = self.clone();
        table.validate(&next.assets)?;
        let found = next
            .production_design
            .measured_tables
            .iter()
            .position(|candidate| candidate.id == table.id);
        if let Some(index) = found {
            let old = &next.production_design.measured_tables[index];
            if table.version
                != old
                    .version
                    .checked_add(1)
                    .ok_or_else(|| DomainError::Invalid("data version overflow".into()))?
                || table.source_sha256 == old.source_sha256
            {
                return Err(DomainError::Invalid(
                    "measured dataset update requires new source hash and next version".into(),
                ));
            }
        } else {
            if table.version != 1 || next.production_design.measured_tables.len() >= 16 {
                return Err(DomainError::Invalid(
                    "new measured dataset requires version 1 and available capacity".into(),
                ));
            }
        }
        // A bound datum's *meaning* can change even if a particular
        // presentation omits its label or unit (notably a bare counter or
        // proportional bar). Such revisions still require scene/profile
        // re-review, without changing the existing visual geometry.
        let changed_semantic_keys: BTreeSet<String> = found
            .map(|index| {
                next.production_design.measured_tables[index]
                    .rows
                    .iter()
                    .filter(|old| table.rows.iter().find(|new| new.key == old.key) != Some(*old))
                    .map(|old| old.key.clone())
                    .collect()
            })
            .unwrap_or_default();
        let mut keys = BTreeSet::new();
        let mut scenes = BTreeSet::new();
        for old_binding in next
            .production_design
            .measured_bindings
            .iter()
            .filter(|b| b.table_id == table.id)
        {
            // Referential integrity, including deleted keys, is checked before
            // committing. A changed dependent node can veto the entire update.
            old_binding.ensure_writable(&next)?;
            old_binding.ensure_output(old_binding.current_node(&next.scenes)?)?;
            let incoming = measured_output(table.row(&old_binding.datum_key)?, &old_binding.view)?;
            if incoming != old_binding.baseline
                || changed_semantic_keys.contains(&old_binding.datum_key)
            {
                keys.insert(old_binding.datum_key.clone());
                scenes.insert(old_binding.scene_id);
            }
        }
        let dependent: Vec<_> = next
            .production_design
            .measured_bindings
            .iter()
            .filter(|b| b.table_id == table.id)
            .cloned()
            .collect();
        for binding in &dependent {
            let updated = measured_output(table.row(&binding.datum_key)?, &binding.view)?;
            if updated != binding.baseline {
                apply_output(binding, &mut next.scenes, &updated)?;
            } else if changed_semantic_keys.contains(&binding.datum_key) {
                let scene = next
                    .scenes
                    .iter_mut()
                    .find(|scene| scene.id == binding.scene_id)
                    .ok_or_else(|| DomainError::NotFound(format!("scene:{}", binding.scene_id)))?;
                scene.status = SceneStatus::Draft;
            }
        }
        for binding in next
            .production_design
            .measured_bindings
            .iter_mut()
            .filter(|b| b.table_id == table.id)
        {
            binding.baseline = measured_output(table.row(&binding.datum_key)?, &binding.view)?;
        }
        if let Some(index) = found {
            next.production_design.measured_tables[index] = table.clone();
        } else {
            next.production_design.measured_tables.push(table.clone());
        }
        let affected_profiles: Vec<_> = next
            .deliverables
            .iter()
            .filter(|profile| {
                !scenes.is_empty()
                    && (profile.included_scene_ids.is_empty()
                        || profile
                            .included_scene_ids
                            .iter()
                            .any(|id| scenes.contains(id)))
            })
            .map(|profile| profile.id)
            .collect();
        next.production_design.last_measured_impact = Some(MeasuredDataImpact {
            table_id: table.id,
            version: table.version,
            changed_keys: keys.into_iter().collect(),
            scene_ids: scenes.into_iter().collect(),
            deliverable_ids: affected_profiles,
        });
        next.validate()?;
        *self = next;
        Ok(())
    }
    pub(crate) fn upsert_measured_binding(
        &mut self,
        id: Uuid,
        scene_id: Uuid,
        node_id: Uuid,
        table_id: Uuid,
        datum_key: &str,
        view: &MeasuredView,
    ) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let mut next = self.clone();
        view.validate()?;
        let existing = next
            .production_design
            .measured_bindings
            .iter()
            .position(|b| b.id == id);
        if let Some(i) = existing {
            let previous = &next.production_design.measured_bindings[i];
            if previous.scene_id != scene_id
                || previous.node_id != node_id
                || previous.table_id != table_id
            {
                return Err(DomainError::Invalid(
                    "measured binding identity cannot move node/source".into(),
                ));
            }
            previous.ensure_writable(&next)?;
            previous.ensure_output(previous.current_node(&next.scenes)?)?;
        } else if next.production_design.measured_bindings.len() >= 64
            || next
                .production_design
                .measured_bindings
                .iter()
                .any(|b| b.scene_id == scene_id && b.node_id == node_id)
        {
            return Err(DomainError::Invalid(
                "measured binding budget or duplicate target".into(),
            ));
        }
        let mut binding = MeasuredBinding {
            id,
            scene_id,
            node_id,
            table_id,
            datum_key: datum_key.to_owned(),
            view: view.clone(),
            baseline: MeasuredOutput::Text {
                value: String::new(),
            },
        };
        binding.ensure_writable(&next)?;
        let table = next
            .production_design
            .measured_tables
            .iter()
            .find(|t| t.id == table_id)
            .ok_or_else(|| DomainError::Invalid("cannot bind missing measured dataset".into()))?;
        let rendered = measured_output(table.row(datum_key)?, view)?;
        apply_output(&binding, &mut next.scenes, &rendered)?;
        binding.baseline = rendered;
        if let Some(i) = existing {
            next.production_design.measured_bindings[i] = binding;
        } else {
            next.production_design.measured_bindings.push(binding);
        }
        next.validate()?;
        *self = next;
        Ok(())
    }
    pub(crate) fn detach_measured_binding(&mut self, id: Uuid) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let binding = self
            .production_design
            .measured_bindings
            .iter()
            .find(|b| b.id == id)
            .ok_or_else(|| DomainError::NotFound(format!("measured binding:{id}")))?;
        self.ensure_unlocked(&format!("scene:{}", binding.scene_id), &[LockKind::Content])?;
        self.production_design
            .measured_bindings
            .retain(|b| b.id != id);
        Ok(()) // Existing Canvas node is deliberately retained as editable content.
    }
}
