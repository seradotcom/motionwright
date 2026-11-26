//! Bounded first-party reference boards and editorial product-state sequences.
//! A registered capture is a declared source, not independently certified
//! product behavior or permission to access an external customer session.
use crate::*;

pub const CAPTURE_BOARD_VERSION: u32 = 1;
pub const CAPTURE_BOARD_DISCLOSURE: &str = "SYNTHETIC FIRST-PARTY / NOT EXECUTION EVIDENCE";
pub const CAPTURE_BOARD_ROLES: [&str; 7] = [
    "surface",
    "title",
    "label-1",
    "image-1",
    "label-2",
    "image-2",
    "disclosure",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureBoardMode {
    ReferenceBoard,
    ProductFlowBridge,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureBoardRole {
    Context,
    Mechanism,
    Evidence,
    Comparison,
    Before,
    After,
}
impl CaptureBoardRole {
    fn label(self) -> &'static str {
        match self {
            Self::Context => "Context",
            Self::Mechanism => "Mechanism",
            Self::Evidence => "Evidence",
            Self::Comparison => "Comparison",
            Self::Before => "Before",
            Self::After => "After",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureBoardEntry {
    pub capture_id: Uuid,
    pub screenshot_sha256: String,
    pub manifest_sha256: String,
    pub fixture_state_sha256: String,
    pub role: CaptureBoardRole,
    pub label: String,
    /// Explicit author declaration, not authenticated legal rights or a grant.
    pub permission_note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptureBoard {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub component_version: u32,
    pub mode: CaptureBoardMode,
    pub entries: [CaptureBoardEntry; 2],
    /// Reference board: both zero. Flow: delayed reveal on exact 30fps clock.
    pub reveal_start_frame: u16,
    pub reveal_duration_frames: u16,
}

fn invalid(message: impl Into<String>) -> DomainError {
    DomainError::Invalid(format!("Capture board: {}", message.into()))
}
pub fn capture_board_node_id(board: Uuid, role: &str) -> Uuid {
    // The existing deterministic source-id function accepts closed role
    // names. A board has a separate role namespace and never reuses a Stage.
    stage_node_id(board, &format!("capture-board/{role}"))
}

impl CaptureBoard {
    pub fn sources<'a>(&self, project: &'a Project) -> Result<[&'a CaptureSourceRecord; 2]> {
        if self.component_version != CAPTURE_BOARD_VERSION || self.id.is_nil() {
            return Err(invalid("unsupported version or identity"));
        }
        let mut sources = Vec::with_capacity(2);
        for entry in &self.entries {
            if entry.label.trim().is_empty()
                || entry.label.chars().count() > 24
                || entry.label.chars().any(char::is_control)
                || entry.permission_note.trim().chars().count() < 12
                || entry.permission_note.len() > 320
                || entry.permission_note.chars().any(char::is_control)
            {
                return Err(invalid(
                    "each reference needs a bounded original label and explicit source-use declaration",
                ));
            }
            let source = project
                .production_design
                .captures
                .iter()
                .find(|capture| capture.id == entry.capture_id)
                .ok_or_else(|| invalid("choose two previously registered first-party captures"))?;
            source.validate(project)?;
            if source.screenshot_sha256 != entry.screenshot_sha256
                || source.manifest_sha256 != entry.manifest_sha256
                || source.fixture_state_sha256 != entry.fixture_state_sha256
            {
                return Err(invalid("registered immutable source identities changed"));
            }
            sources.push(source);
        }
        let [first, second]: [&CaptureSourceRecord; 2] = sources
            .try_into()
            .map_err(|_| invalid("expected two sources"))?;
        if first.id == second.id
            || first.screenshot_sha256 == second.screenshot_sha256
            || first.screenshot_asset_id == second.screenshot_asset_id
            || self.entries[0].role == self.entries[1].role
        {
            return Err(invalid(
                "select distinct original captures and distinct narrative roles",
            ));
        }
        match self.mode {
            CaptureBoardMode::ReferenceBoard => {
                if self.reveal_start_frame != 0
                    || self.reveal_duration_frames != 0
                    || self.entries.iter().any(|entry| {
                        matches!(
                            entry.role,
                            CaptureBoardRole::Before | CaptureBoardRole::After
                        )
                    })
                {
                    return Err(invalid(
                        "reference board is static and uses explicit reference roles",
                    ));
                }
            }
            CaptureBoardMode::ProductFlowBridge => {
                if self.entries[0].role != CaptureBoardRole::Before
                    || self.entries[1].role != CaptureBoardRole::After
                    || self.reveal_start_frame == 0
                    || self.reveal_duration_frames == 0
                    || u32::from(self.reveal_start_frame) + u32::from(self.reveal_duration_frames)
                        > 36000
                    || first.source_generation != second.source_generation
                    || first.source_commit != second.source_commit
                    || first.app_version != second.app_version
                    || first.app_id != second.app_id
                    || first.origin != second.origin
                    || first.route != second.route
                    || first.viewport_width != second.viewport_width
                    || first.viewport_height != second.viewport_height
                    || first.source_revision >= second.source_revision
                    || first.fixture_state_sha256 == second.fixture_state_sha256
                    || first.manifest_sha256 == second.manifest_sha256
                    || first.claim_id == second.claim_id
                {
                    return Err(invalid(
                        "flow needs distinct ordered states of the same original app/build and an exact editorial reveal",
                    ));
                }
            }
        }
        Ok([first, second])
    }

    /// The original board is editable through its typed configuration. Native
    /// aspect-specific projection reuses the same IDs, never recreates sources.
    pub fn realize(&self, project: &Project, width: u32, height: u32) -> Result<Vec<CanvasNode>> {
        let sources = self.sources(project)?;
        if !(320..=7680).contains(&width) || !(320..=7680).contains(&height) {
            return Err(invalid("unsupported output dimensions"));
        }
        let w = f64::from(width);
        let h = f64::from(height);
        let small = w.min(h);
        let wide = w / h >= 1.35;
        let title = match self.mode {
            CaptureBoardMode::ReferenceBoard => "REFERENCE BOARD",
            CaptureBoardMode::ProductFlowBridge => "PRODUCT FLOW / AUTHORED STATES",
        };
        let mut nodes = Vec::with_capacity(7);
        let mut add = |role: &str,
                       rect: [f64; 4],
                       text: Option<String>,
                       font: f64,
                       weight: u16,
                       fill: &str,
                       second: bool|
         -> Result<()> {
            let [x, y, ww, hh] = rect;
            let keyframes = if second && self.mode == CaptureBoardMode::ProductFlowBridge {
                let start = RationalTime::new(i64::from(self.reveal_start_frame), 30)
                    .map_err(|_| invalid("invalid start frame"))?;
                let end = RationalTime::new(
                    i64::from(self.reveal_start_frame) + i64::from(self.reveal_duration_frames),
                    30,
                )
                .map_err(|_| invalid("invalid end frame"))?;
                vec![
                    CanvasKeyframe {
                        at: RationalTime::ZERO,
                        property: MotionProperty::Opacity,
                        value: 0.0,
                        interpolation: MotionInterpolation::Linear,
                    },
                    CanvasKeyframe {
                        at: start,
                        property: MotionProperty::Opacity,
                        value: 0.0,
                        interpolation: MotionInterpolation::Hold,
                    },
                    CanvasKeyframe {
                        at: end,
                        property: MotionProperty::Opacity,
                        value: 1.0,
                        interpolation: MotionInterpolation::Linear,
                    },
                ]
            } else {
                vec![]
            };
            let is_text = text.is_some();
            let node = CanvasNode {
                id: capture_board_node_id(self.id, role),
                name: format!("CaptureBoard / {role}"),
                kind: if is_text { "text" } else { "rectangle" }.into(),
                parent_id: None,
                x: x * w,
                y: y * h,
                width: ww * w,
                height: hh * h,
                rotation_deg: 0.0,
                opacity: 1.0,
                text,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: nodes.len() as i32,
                style: NodeStyle {
                    fill: Some(fill.into()),
                    font_family: is_text.then(|| "Instrument Sans Variable".into()),
                    font_size: is_text.then_some(font),
                    font_weight: is_text.then_some(weight),
                    line_height: is_text.then_some(1.12),
                    ..Default::default()
                },
                relations: vec![],
                property_locks: Default::default(),
                keyframes,
            };
            if let Some(text) = &node.text {
                let lines = text.split('\n').collect::<Vec<_>>();
                if lines
                    .iter()
                    .any(|line| line.chars().count() as f64 * font * 0.64 > node.width * 0.95)
                    || font * (1.4 + 1.12 * (lines.len() - 1) as f64) > node.height
                {
                    return Err(invalid(format!(
                        "{} copy exceeds its bounded text box; shorten the authored label",
                        node.name
                    )));
                }
            }
            node.validate()?;
            nodes.push(node);
            Ok(())
        };
        add(
            "surface",
            [0.06, 0.06, 0.88, 0.88],
            None,
            0.0,
            400,
            "#101820",
            false,
        )?;
        add(
            "title",
            [0.08, 0.075, 0.84, 0.075],
            Some(title.into()),
            small / 32.0,
            600,
            "#F2F4F3",
            false,
        )?;
        for (i, entry) in self.entries.iter().enumerate() {
            let (label_rect, image_rect) = if wide {
                let x = if i == 0 { 0.08 } else { 0.52 };
                ([x, 0.17, 0.40, 0.115], [x, 0.295, 0.40, 0.545])
            } else if i == 0 {
                ([0.08, 0.16, 0.84, 0.085], [0.08, 0.255, 0.84, 0.255])
            } else {
                ([0.08, 0.53, 0.84, 0.085], [0.08, 0.625, 0.84, 0.24])
            };
            let label = format!(
                "{}: {}\nSource r{}",
                entry.role.label(),
                entry.label,
                sources[i].source_revision
            );
            add(
                if i == 0 { "label-1" } else { "label-2" },
                label_rect,
                Some(label),
                small / 40.0,
                400,
                "#F2F4F3",
                i == 1,
            )?;
            // This is the editable source slot, not a claim that the PNG is
            // drawn in the DOM study. Native maps this exact ID to Image.
            add(
                if i == 0 { "image-1" } else { "image-2" },
                image_rect,
                None,
                0.0,
                400,
                "#263B4F",
                i == 1,
            )?;
        }
        add(
            "disclosure",
            [0.08, 0.89, 0.84, 0.05],
            Some(CAPTURE_BOARD_DISCLOSURE.into()),
            small / 48.0,
            400,
            "#F2F4F3",
            false,
        )?;
        Ok(nodes)
    }

    pub fn validate_scene(&self, project: &Project, scene: &Scene) -> Result<()> {
        self.sources(project)?;
        if scene.id != self.scene_id
            || scene.renderer != RendererKind::MotionCanvas
            || scene.camera != CameraState::default()
            || !scene.beats.is_empty()
            || scene.duration < RationalTime::new(2, 1).expect("two seconds")
        {
            return Err(invalid(
                "requires a dedicated neutral Motion Canvas scene, at least two seconds, without beats",
            ));
        }
        let design = &project.production_design;
        if design.brand_profile.is_some()
            || design.heroes.iter().any(|row| row.scene_id == scene.id)
            || design.stages.iter().any(|row| row.scene_id == scene.id)
            || design
                .procedural_fields
                .iter()
                .any(|row| row.scene_id == scene.id)
            || design
                .semantic_graphs
                .iter()
                .any(|row| row.scene_id == scene.id)
            || design
                .camera_reframes
                .iter()
                .any(|row| row.scene_id == scene.id)
            || design.capsules.iter().any(|row| row.scene_id == scene.id)
            || design
                .measured_bindings
                .iter()
                .any(|row| row.scene_id == scene.id)
            || design
                .scene_boundaries
                .iter()
                .any(|row| row.scene_id == scene.id)
        {
            return Err(invalid(
                "cannot replace other authored components, boundaries or an active mandatory brand profile",
            ));
        }
        if self.mode == CaptureBoardMode::ProductFlowBridge {
            let end = RationalTime::new(
                i64::from(self.reveal_start_frame) + i64::from(self.reveal_duration_frames),
                30,
            )
            .map_err(|_| invalid("invalid exact reveal"))?;
            if end >= scene.duration {
                return Err(invalid("reveal must finish strictly inside the scene"));
            }
        }
        let baseline = self.realize(project, 1920, 1080)?;
        if scene.nodes.len() != baseline.len()
            || baseline.iter().any(|expected| {
                !scene.nodes.iter().any(|actual| {
                    let mut node = actual.clone();
                    node.property_locks = expected.property_locks.clone();
                    node == *expected
                })
            })
        {
            return Err(invalid(
                "original board slots and disclosure must retain their exact authored configuration",
            ));
        }
        let mut outputs = 0;
        for profile in project.deliverables.iter().filter(|profile| {
            profile.included_scene_ids.is_empty() || profile.included_scene_ids.contains(&scene.id)
        }) {
            outputs += 1;
            if profile.framing_strategy != FramingStrategy::Replan
                || (self.mode == CaptureBoardMode::ProductFlowBridge
                    && profile.frame_rate != RationalTime::new(30, 1).expect("exact board fps"))
            {
                return Err(invalid(
                    "requires Replan outputs and exact 30fps for product-state reveal",
                ));
            }
            if project
                .scenes
                .iter()
                .filter(|item| {
                    profile.included_scene_ids.is_empty()
                        || profile.included_scene_ids.contains(&item.id)
                })
                .any(|item| item.camera.safe_margin > 0.06)
            {
                return Err(invalid(
                    "selected output safe margin exceeds the original board layout",
                ));
            }
            self.realize(project, profile.width, profile.height)?;
            if baseline
                .iter()
                .filter(|node| node.kind == "text")
                .any(|node| {
                    profile
                        .text_overrides
                        .get(&node.id)
                        .is_some_and(|text| node.text.as_ref() != Some(text))
                })
            {
                return Err(invalid(
                    "source labels and synthetic disclosure must retain exact wording",
                ));
            }
        }
        if outputs == 0 {
            return Err(invalid("include the board in a saved delivery profile"));
        }
        Ok(())
    }
}

impl Project {
    pub(crate) fn validate_capture_boards(&self) -> Result<()> {
        let boards = &self.production_design.capture_boards;
        if boards.len() > 16 {
            return Err(invalid("project board capacity exceeded"));
        }
        let mut ids = BTreeSet::new();
        let mut scenes = BTreeSet::new();
        for board in boards {
            if !ids.insert(board.id) || !scenes.insert(board.scene_id) {
                return Err(invalid("duplicate board or scene identity"));
            }
            let scene = self
                .scenes
                .iter()
                .find(|scene| scene.id == board.scene_id)
                .ok_or_else(|| invalid("source scene was removed"))?;
            board.validate_scene(self, scene)?;
        }
        Ok(())
    }
    fn ensure_capture_board_unlocked(&self, scene_id: Uuid) -> Result<()> {
        let kinds = [
            LockKind::Content,
            LockKind::Position,
            LockKind::Style,
            LockKind::Timing,
            LockKind::Renderer,
        ];
        self.ensure_unlocked(&self.resource_key(), &kinds)?;
        self.ensure_unlocked(&format!("scene:{scene_id}"), &kinds)?;
        if self
            .scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .is_some_and(|scene| {
                scene
                    .nodes
                    .iter()
                    .any(|node| !node.property_locks.is_empty())
            })
        {
            return Err(invalid(
                "unlock the original board objects before replacing or removing their configuration",
            ));
        }
        Ok(())
    }
    pub(crate) fn upsert_capture_board(&mut self, board: &CaptureBoard) -> Result<()> {
        self.ensure_capture_board_unlocked(board.scene_id)?;
        let mut next = self.clone();
        let index = next
            .scenes
            .iter()
            .position(|scene| scene.id == board.scene_id)
            .ok_or_else(|| invalid("choose a saved scene"))?;
        if let Some(existing) = self
            .production_design
            .capture_boards
            .iter()
            .find(|row| row.id == board.id)
        {
            if existing.scene_id != board.scene_id {
                return Err(invalid("cannot move an existing board to another scene"));
            }
            existing.validate_scene(self, &self.scenes[index])?;
        } else if !next.scenes[index].nodes.is_empty()
            || self
                .production_design
                .capture_boards
                .iter()
                .any(|row| row.scene_id == board.scene_id)
        {
            return Err(invalid(
                "choose an empty original scene; no existing Canvas is replaced",
            ));
        }
        let nodes = board.realize(self, 1920, 1080)?;
        next.scenes[index].nodes = nodes;
        next.scenes[index].status = SceneStatus::Draft;
        if let Some(existing) = next
            .production_design
            .capture_boards
            .iter_mut()
            .find(|row| row.id == board.id)
        {
            *existing = board.clone();
        } else {
            next.production_design.capture_boards.push(board.clone());
        }
        next.validate()?;
        *self = next;
        Ok(())
    }
    pub(crate) fn remove_capture_board(&mut self, scene_id: Uuid, board_id: Uuid) -> Result<()> {
        self.ensure_capture_board_unlocked(scene_id)?;
        let board = self
            .production_design
            .capture_boards
            .iter()
            .find(|row| row.scene_id == scene_id && row.id == board_id)
            .ok_or_else(|| invalid("no matching saved board"))?;
        let scene = self
            .scenes
            .iter()
            .find(|scene| scene.id == scene_id)
            .ok_or_else(|| invalid("source scene was removed"))?;
        board.validate_scene(self, scene)?;
        let mut next = self.clone();
        next.production_design
            .capture_boards
            .retain(|row| row.id != board_id);
        let scene = next
            .scenes
            .iter_mut()
            .find(|scene| scene.id == scene_id)
            .expect("source exists");
        scene.nodes.clear();
        scene.status = SceneStatus::Draft;
        // Capture registrations, CAS assets, claim sources and other scenes
        // remain intact. This explicit removal deletes only generated slots.
        next.validate()?;
        *self = next;
        Ok(())
    }
}
