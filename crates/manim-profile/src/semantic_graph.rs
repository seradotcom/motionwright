//! Geometry and exact frame projection for the canonical semantic Graph.
//!
//! This module is source preparation only. It does not register a Driver command,
//! grant native font support, return GraphAvailability::Ready, or authorize a
//! Project mutation. The canonical SemanticGraph remains the editable source.
use crate::{
    MAX_SOURCE_ENTRY_BYTES_V2, ManimFrameClockV2, ManimMatteKindV2, ManimMatteV2,
    ManimProfileError, ManimRenderProfile, Result,
};
use motionwright_domain::{
    GraphAppearance, GraphBox, GraphContent, GraphFontFamily, GraphMotionPreference, GraphPoint,
    GraphPort, GraphSourceSnapshot, GraphVertex, GraphVertexShape, RationalTime,
    SEMANTIC_GRAPH_FONT_WEIGHT, SEMANTIC_GRAPH_PROFILE, SemanticGraph,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManimSemanticGraphPlan {
    pub schema_version: u16,
    pub profile_id: String,
    pub execution_snapshot: GraphSourceSnapshot,
    pub scene_clock: ManimFrameClockV2,
    pub render_profile: ManimRenderProfile,
    pub scene_matte: ManimMatteV2,
    pub source: SemanticGraph,
}

fn invalid(reason: &'static str) -> ManimProfileError {
    ManimProfileError::Invalid(reason.into())
}

pub(crate) fn exact_frame(time: RationalTime, fps: u32) -> Result<u32> {
    time.validate().map_err(|_| invalid("invalid_graph_time"))?;
    if time.num < 0 {
        return Err(invalid("negative_graph_time"));
    }
    let numerator = i128::from(time.num) * i128::from(fps);
    let denominator = i128::from(time.den);
    if numerator % denominator != 0 {
        return Err(invalid("graph_time_is_not_an_exact_frame"));
    }
    u32::try_from(numerator / denominator).map_err(|_| invalid("graph_frame_overflow"))
}

fn encoded(plan: &ManimSemanticGraphPlan) -> Result<Vec<u8>> {
    let mut bytes =
        serde_json::to_vec_pretty(plan).map_err(|_| invalid("graph_source_encoding_failed"))?;
    bytes.push(b'\n');
    if bytes.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
        return Err(invalid("graph_source_document_budget"));
    }
    Ok(bytes)
}

impl ManimSemanticGraphPlan {
    pub fn new(
        execution_snapshot: GraphSourceSnapshot,
        scene_clock: ManimFrameClockV2,
        render_profile: ManimRenderProfile,
        scene_matte: ManimMatteV2,
        source: SemanticGraph,
    ) -> Result<Self> {
        let plan = Self {
            schema_version: 1,
            profile_id: SEMANTIC_GRAPH_PROFILE.into(),
            execution_snapshot,
            scene_clock,
            render_profile,
            scene_matte,
            source,
        };
        plan.validate()?;
        Ok(plan)
    }

    pub fn validate(&self) -> Result<()> {
        self.render_profile.validate()?;
        self.scene_clock.validate()?;
        self.source
            .validate_shape(Some(self.scene_clock.frame_rate))
            .map_err(|_| invalid("canonical_graph_shape_rejected"))?;
        if self.schema_version != 1
            || self.profile_id != SEMANTIC_GRAPH_PROFILE
            || self.scene_clock.frame_rate.den != 1
            || i64::from(self.render_profile.frame_rate) != self.scene_clock.frame_rate.num
            || self.source.reference_frame.width != u64::from(self.render_profile.width)
            || self.source.reference_frame.height != u64::from(self.render_profile.height)
            || !self.render_profile.width.is_multiple_of(2)
            || !self.render_profile.height.is_multiple_of(2)
            || self.scene_matte.kind != ManimMatteKindV2::Solid
            || self.scene_matte.opacity != 1
            || !(4.0..=512.0).contains(&self.source.style.font_size)
            || self.render_profile.width > 4096
            || self.render_profile.height > 4096
            || u64::from(self.render_profile.width) * u64::from(self.render_profile.height)
                > 8_847_360
        {
            return Err(invalid("unsupported_graph_source_profile"));
        }
        crate::color_hex(&self.scene_matte.color)?;
        let first = exact_frame(self.source.time_range.start, self.render_profile.frame_rate)?;
        let count = exact_frame(
            self.source.time_range.duration,
            self.render_profile.frame_rate,
        )?;
        let end = first
            .checked_add(count)
            .ok_or_else(|| invalid("graph_frame_overflow"))?;
        if end > self.scene_clock.frame_count {
            return Err(invalid("graph_interval_exceeds_scene"));
        }
        // Historical authorship is intentionally neither overwritten nor used as
        // the current execution stamp. The owner validates execution_snapshot
        // against the selected Project and checks current dependency digests.
        encoded(self)?;
        Ok(())
    }

    pub fn source_document(&self) -> Result<Vec<u8>> {
        self.validate()?;
        encoded(self)
    }

    pub fn inspect_source_document(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > MAX_SOURCE_ENTRY_BYTES_V2 {
            return Err(invalid("graph_source_document_budget"));
        }
        let plan: Self =
            serde_json::from_slice(bytes).map_err(|_| invalid("graph_source_document_invalid"))?;
        plan.validate()?;
        // Typed reserialization is not a native observation or an authority.
        // The source package owner retains the original bytes and their digest.
        Ok(plan)
    }

    /// Exact authored pixels; no world-coordinate scaling or layout
    /// guesses are introduced here. The native compiler consumes these values.
    pub fn sample(&self, scene_frame: u32) -> Result<GraphFrameSample> {
        self.validate()?;
        if scene_frame >= self.scene_clock.frame_count {
            return Err(invalid("graph_sample_outside_scene"));
        }
        let fps = self.render_profile.frame_rate;
        let graph_first = exact_frame(self.source.time_range.start, fps)?;
        let graph_count = exact_frame(self.source.time_range.duration, fps)?;
        let graph_local = scene_frame
            .checked_sub(graph_first)
            .filter(|frame| *frame < graph_count);
        let mut vertices = Vec::with_capacity(self.source.content.vertices().len());
        for vertex in self.source.content.vertices() {
            vertices.push(GraphVertexSample {
                id: vertex.id,
                shape: vertex.shape,
                authored_box: vertex.r#box.clone(),
                opacity: opacity_at(&vertex.appearance, self.source.motion, graph_local, fps)?,
            });
        }
        let mut edges = Vec::new();
        let mut active_events = Vec::new();
        match &self.source.content {
            GraphContent::CausalDiagram {
                edges: source_edges,
                ..
            } => {
                for edge in source_edges {
                    edges.push(sample_edge(
                        &self.source,
                        graph_local,
                        fps,
                        EdgeGeometry {
                            id: edge.id,
                            appearance: &edge.appearance,
                            from: (edge.from_vertex_id, edge.from_port),
                            to: (edge.to_vertex_id, edge.to_port),
                            waypoints: &edge.waypoints,
                        },
                    )?);
                }
            }
            GraphContent::SystemFlow {
                edges: source_edges,
                events,
                ..
            } => {
                for edge in source_edges {
                    edges.push(sample_edge(
                        &self.source,
                        graph_local,
                        fps,
                        EdgeGeometry {
                            id: edge.id,
                            appearance: &edge.appearance,
                            from: (edge.from_vertex_id, edge.from_port),
                            to: (edge.to_vertex_id, edge.to_port),
                            waypoints: &edge.waypoints,
                        },
                    )?);
                }
                if let Some(frame) = graph_local {
                    for event in events {
                        let first = exact_frame(event.at, fps)?;
                        let count = exact_frame(event.duration, fps)?;
                        let end = first
                            .checked_add(count)
                            .ok_or_else(|| invalid("graph_frame_overflow"))?;
                        if frame >= first && frame < end {
                            active_events.push(event.id);
                        }
                    }
                }
            }
        }
        let project_frame = self
            .scene_clock
            .project_start_frame
            .checked_add(u64::from(scene_frame))
            .ok_or_else(|| invalid("graph_frame_overflow"))?;
        Ok(GraphFrameSample {
            scene_frame,
            project_frame,
            graph_local_frame: graph_local,
            background_hex: if graph_local.is_some() {
                self.source.style.background.clone()
            } else {
                self.scene_matte.color.clone()
            },
            vertices,
            edges,
            active_events,
        })
    }

    /// This declaration is NOT evidence that the runtime opened these fonts.
    /// Pango must check every final composed label's glyphs and selected files
    /// against the fixed font mount before native frame rendering begins.
    pub fn typography_requirement(&self) -> GraphTypographyRequirement {
        let (editorial_family, native_family) = match self.source.style.font_family {
            GraphFontFamily::InstrumentSansVariable => {
                ("Instrument Sans Variable", "Instrument Sans")
            }
            GraphFontFamily::IbmPlexMono => ("IBM Plex Mono", "IBM Plex Mono"),
        };
        GraphTypographyRequirement {
            editorial_family,
            native_family,
            weight: SEMANTIC_GRAPH_FONT_WEIGHT,
            font_size_px: self.source.style.font_size,
            fallback_allowed: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphOpacity {
    pub numerator: u32,
    pub denominator: u32,
}

impl GraphOpacity {
    fn hidden() -> Self {
        Self {
            numerator: 0,
            denominator: 1,
        }
    }
    fn visible() -> Self {
        Self {
            numerator: 1,
            denominator: 1,
        }
    }
}

fn opacity_at(
    appearance: &GraphAppearance,
    motion: GraphMotionPreference,
    frame: Option<u32>,
    fps: u32,
) -> Result<GraphOpacity> {
    let Some(frame) = frame else {
        return Ok(GraphOpacity::hidden());
    };
    let first = exact_frame(appearance.at, fps)?;
    let count = exact_frame(appearance.duration, fps)?;
    let end = first
        .checked_add(count)
        .ok_or_else(|| invalid("graph_frame_overflow"))?;
    if frame < first {
        Ok(GraphOpacity::hidden())
    } else if motion == GraphMotionPreference::Off || frame >= end {
        Ok(GraphOpacity::visible())
    } else {
        Ok(GraphOpacity {
            numerator: frame - first,
            denominator: count,
        })
    }
}

fn port_point(vertex: &GraphVertex, port: GraphPort) -> GraphPoint {
    let rect = &vertex.r#box;
    match port {
        GraphPort::Top => GraphPoint {
            x: rect.x + rect.width / 2.0,
            y: rect.y,
        },
        GraphPort::Right => GraphPoint {
            x: rect.x + rect.width,
            y: rect.y + rect.height / 2.0,
        },
        GraphPort::Bottom => GraphPoint {
            x: rect.x + rect.width / 2.0,
            y: rect.y + rect.height,
        },
        GraphPort::Left => GraphPoint {
            x: rect.x,
            y: rect.y + rect.height / 2.0,
        },
    }
}

struct EdgeGeometry<'a> {
    id: Uuid,
    appearance: &'a GraphAppearance,
    from: (Uuid, GraphPort),
    to: (Uuid, GraphPort),
    waypoints: &'a [GraphPoint],
}

fn sample_edge(
    graph: &SemanticGraph,
    graph_local: Option<u32>,
    fps: u32,
    edge: EdgeGeometry<'_>,
) -> Result<GraphEdgeSample> {
    let vertex = |id| {
        graph
            .content
            .vertices()
            .iter()
            .find(|item| item.id == id)
            .ok_or_else(|| invalid("graph_edge_endpoint_missing"))
    };
    let mut points = Vec::with_capacity(edge.waypoints.len() + 2);
    points.push(port_point(vertex(edge.from.0)?, edge.from.1));
    points.extend_from_slice(edge.waypoints);
    points.push(port_point(vertex(edge.to.0)?, edge.to.1));
    Ok(GraphEdgeSample {
        id: edge.id,
        points,
        opacity: opacity_at(edge.appearance, graph.motion, graph_local, fps)?,
    })
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphVertexSample {
    pub id: Uuid,
    pub shape: GraphVertexShape,
    pub authored_box: GraphBox,
    pub opacity: GraphOpacity,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphEdgeSample {
    pub id: Uuid,
    pub points: Vec<GraphPoint>,
    pub opacity: GraphOpacity,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphFrameSample {
    pub scene_frame: u32,
    pub project_frame: u64,
    pub graph_local_frame: Option<u32>,
    pub background_hex: String,
    pub vertices: Vec<GraphVertexSample>,
    pub edges: Vec<GraphEdgeSample>,
    pub active_events: Vec<Uuid>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GraphTypographyRequirement {
    pub editorial_family: &'static str,
    pub native_family: &'static str,
    pub weight: u16,
    pub font_size_px: f64,
    pub fallback_allowed: bool,
}

#[cfg(test)]
#[path = "semantic_graph_tests.rs"]
mod tests;
