//! Canonical semantic Graph data and validation for the existing Project.
//!
//! These types do not grant execution, claim native support, create a separate
//! store, or accept a caller-authored authoring context as authority. Native
//! admission is enforced by the matching service and renderer.
use crate::*;
use sha2::{Digest, Sha256};

pub const SEMANTIC_GRAPH_COMPONENT_VERSION: u8 = 1;
pub const SEMANTIC_GRAPH_PROFILE: &str = "mw-manim-semantic-graph-v1";
pub const SEMANTIC_GRAPH_FONT_WEIGHT: u16 = 400;
pub const SEMANTIC_GRAPH_MAX_GRAPHS: usize = 16;
pub const SEMANTIC_GRAPH_MAX_VERTICES: usize = 32;
pub const SEMANTIC_GRAPH_MAX_EDGES: usize = 64;
pub const SEMANTIC_GRAPH_MAX_EVENTS: usize = 64;
pub const SEMANTIC_GRAPH_MAX_WAYPOINTS: usize = 8;

// Unlike an ordinary Option field, the JSON member must be present. `null` is
// meaningful author intent; an absent target/brand member is not the same wire.
fn required_nullable<'de, D, T>(deserializer: D) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphSourceSnapshot {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub branch_id: Uuid,
    pub revision: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphAuthorship {
    pub author_label: String,
    pub rationale: String,
    pub source_snapshot: GraphSourceSnapshot,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphTimeRange {
    pub start: RationalTime,
    pub duration: RationalTime,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphPoint {
    pub x: f64,
    pub y: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphBox {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Opt-in derived placement. The offset is a signed vector, never a frame point.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum GraphLabelPlacement {
    PolylineMidpoint { offset: GraphPoint },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphLabelPlacementFields {
    kind: String,
    #[serde(deserialize_with = "deserialize_graph_label_object")]
    offset: GraphPoint,
}

// New placement and offset members require JSON objects. Keep the decoding
// typed while refusing positional struct/enum sequences; unrelated GraphPoint
// members retain their existing deserialization behavior.
fn deserialize_graph_label_object<'de, D, T>(deserializer: D) -> std::result::Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct ObjectVisitor<T>(std::marker::PhantomData<T>);

    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for ObjectVisitor<T> {
        type Value = T;

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a typed Graph label object")
        }

        fn visit_map<M>(self, map: M) -> std::result::Result<T, M::Error>
        where
            M: serde::de::MapAccess<'de>,
        {
            T::deserialize(serde::de::value::MapAccessDeserializer::new(map))
        }
    }

    deserializer.deserialize_map(ObjectVisitor(std::marker::PhantomData))
}

impl<'de> Deserialize<'de> for GraphLabelPlacement {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let fields: GraphLabelPlacementFields = deserialize_graph_label_object(deserializer)?;
        match fields.kind.as_str() {
            "polyline_midpoint" => Ok(Self::PolylineMidpoint {
                offset: fields.offset,
            }),
            other => Err(serde::de::Error::unknown_variant(
                other,
                &["polyline_midpoint"],
            )),
        }
    }
}

fn deserialize_graph_label_placement<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<GraphLabelPlacement>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    // Only absence is manual. Explicit null must not erase authored intent.
    GraphLabelPlacement::deserialize(deserializer).map(Some)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphReferenceFrame {
    pub width: u64,
    pub height: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum GraphSubjectRef {
    Scene {
        scene_id: Uuid,
    },
    Claim {
        claim_id: Uuid,
        content_sha256: String,
    },
    Asset {
        asset_id: Uuid,
        content_sha256: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum GraphTargetBinding {
    CanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
        content_sha256: String,
    },
    Claim {
        claim_id: Uuid,
        content_sha256: String,
    },
    MeasuredDatum {
        table_id: Uuid,
        table_version: u32,
        datum_key: String,
        source_sha256: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphBrandRef {
    pub id: Uuid,
    pub version: u64,
    pub content_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum GraphUnit {
    Qualitative,
    Symbol { symbol: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphAppearance {
    pub at: RationalTime,
    pub duration: RationalTime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphMotionPreference {
    Full,
    Off,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphVertexShape {
    Rectangle,
    Circle,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphPort {
    Top,
    Right,
    Bottom,
    Left,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphVertex {
    pub id: Uuid,
    pub label: String,
    pub meaning: String,
    pub unit: GraphUnit,
    pub r#box: GraphBox,
    pub shape: GraphVertexShape,
    #[serde(deserialize_with = "required_nullable")]
    pub target: Option<GraphTargetBinding>,
    pub appearance: GraphAppearance,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum GraphCausalBasis {
    AuthorHypothesis {
        disclosure: String,
    },
    ClaimReference {
        claim_id: Uuid,
        content_sha256: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphCausalInfluence {
    Positive,
    Negative,
    Unspecified,
}

// The wire is flat. Avoid serde flatten together with deny_unknown_fields:
// both concrete edge kinds retain all exact original JSON member names.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphCausalEdge {
    pub id: Uuid,
    pub label: String,
    pub from_vertex_id: Uuid,
    pub to_vertex_id: Uuid,
    pub from_port: GraphPort,
    pub to_port: GraphPort,
    pub waypoints: Vec<GraphPoint>,
    pub label_box: GraphBox,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_graph_label_placement"
    )]
    pub label_placement: Option<GraphLabelPlacement>,
    pub appearance: GraphAppearance,
    pub influence: GraphCausalInfluence,
    pub basis: GraphCausalBasis,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphFlowChannel {
    Data,
    Control,
    Event,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphFlowEdge {
    pub id: Uuid,
    pub label: String,
    pub from_vertex_id: Uuid,
    pub to_vertex_id: Uuid,
    pub from_port: GraphPort,
    pub to_port: GraphPort,
    pub waypoints: Vec<GraphPoint>,
    pub label_box: GraphBox,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_graph_label_placement"
    )]
    pub label_placement: Option<GraphLabelPlacement>,
    pub appearance: GraphAppearance,
    pub channel: GraphFlowChannel,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum GraphSyntheticFlowSource {
    Synthetic { disclosure: String },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphFlowEvent {
    pub id: Uuid,
    pub edge_id: Uuid,
    pub label: String,
    pub at: RationalTime,
    pub duration: RationalTime,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum GraphContent {
    CausalDiagram {
        vertices: Vec<GraphVertex>,
        edges: Vec<GraphCausalEdge>,
    },
    SystemFlow {
        source: GraphSyntheticFlowSource,
        vertices: Vec<GraphVertex>,
        edges: Vec<GraphFlowEdge>,
        events: Vec<GraphFlowEvent>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum GraphFontFamily {
    #[serde(rename = "Instrument Sans Variable")]
    InstrumentSansVariable,
    #[serde(rename = "IBM Plex Mono")]
    IbmPlexMono,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GraphStyle {
    pub background: String,
    pub vertex_fill: String,
    pub text_color: String,
    pub edge_color: String,
    pub accent_color: String,
    pub font_family: GraphFontFamily,
    pub font_size: f64,
    pub edge_width: f64,
    pub disclosure_box: GraphBox,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticGraph {
    pub id: Uuid,
    pub scene_id: Uuid,
    pub component_version: u8,
    pub subject_ref: GraphSubjectRef,
    pub time_range: GraphTimeRange,
    #[serde(deserialize_with = "required_nullable")]
    pub brand_profile_ref: Option<GraphBrandRef>,
    pub variant_profile_ref: Uuid,
    pub reference_frame: GraphReferenceFrame,
    pub motion: GraphMotionPreference,
    pub style: GraphStyle,
    pub content: GraphContent,
    pub authorship: GraphAuthorship,
}

// Backend-produced read DTO. No Deserialize implementation: the WebView must
// never submit an arbitrary ready context as authorization for a Project edit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphBlockReason {
    GraphWriteUnavailable,
    NativeGraphProfileUnavailable,
    UnsupportedRendererGuardMissing,
    FontSourceUnavailable,
    DependencyDescriptorUnavailable,
    SourceStale,
    ProfileUnsupported,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GraphAvailability {
    Ready,
    Blocked,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphFontSelection {
    pub primary_family: String,
    pub mono_family: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum GraphDependencyDescriptor {
    Scene {
        scene_id: Uuid,
    },
    Claim {
        claim_id: Uuid,
        content_sha256: String,
    },
    Asset {
        asset_id: Uuid,
        content_sha256: String,
    },
    CanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
        content_sha256: String,
    },
    MeasuredDatum {
        table_id: Uuid,
        table_version: u32,
        datum_key: String,
        source_sha256: String,
    },
    Brand {
        id: Uuid,
        version: u64,
        content_sha256: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphAuthoringContext {
    pub schema: &'static str,
    pub source_snapshot: GraphSourceSnapshot,
    pub scene_id: Uuid,
    pub variant_profile_ref: Uuid,
    pub component_version: u8,
    pub native_profile: &'static str,
    pub font_weight: u16,
    pub font_selection: GraphFontSelection,
    pub availability: GraphAvailability,
    pub blocking_reasons: Vec<GraphBlockReason>,
    pub dependencies: Vec<GraphDependencyDescriptor>,
}

const GRAPH_SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;

fn graph_invalid(message: impl Into<String>) -> DomainError {
    DomainError::Invalid(message.into())
}

fn graph_text(value: &str, max: usize, label: &str) -> Result<()> {
    if value.trim().is_empty()
        || value.len() > max
        || value
            .chars()
            .any(|c| (c <= '\u{1f}' && c != '\n') || c == '\u{7f}')
    {
        return Err(graph_invalid(format!("invalid Graph {label}")));
    }
    Ok(())
}

fn graph_digest(value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err(graph_invalid(
            "Graph dependency digest must be lowercase SHA-256",
        ));
    }
    Ok(())
}

fn graph_gcd(mut a: i128, mut b: i128) -> i128 {
    a = a.abs();
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

fn graph_time(value: RationalTime) -> Result<()> {
    if value.den <= 0 || graph_gcd(i128::from(value.num), i128::from(value.den)) != 1 {
        return Err(graph_invalid(
            "Graph time must be a reduced i64 rational with positive denominator",
        ));
    }
    Ok(())
}

fn graph_compare_time(a: RationalTime, b: RationalTime) -> std::cmp::Ordering {
    (i128::from(a.num) * i128::from(b.den)).cmp(&(i128::from(b.num) * i128::from(a.den)))
}

fn graph_add_time(a: RationalTime, b: RationalTime) -> Result<RationalTime> {
    graph_time(a)?;
    graph_time(b)?;
    let num = i128::from(a.num) * i128::from(b.den) + i128::from(b.num) * i128::from(a.den);
    let den = i128::from(a.den) * i128::from(b.den);
    let divisor = graph_gcd(num, den);
    let result = RationalTime {
        num: i64::try_from(num / divisor)
            .map_err(|_| graph_invalid("Graph time addition exceeds i64"))?,
        den: i64::try_from(den / divisor)
            .map_err(|_| graph_invalid("Graph time addition exceeds i64"))?,
    };
    graph_time(result)?;
    Ok(result)
}

fn graph_exact_frame(at: RationalTime, rate: RationalTime) -> Result<()> {
    graph_time(at)?;
    graph_time(rate)?;
    if rate.num <= 0
        || (i128::from(at.num) * i128::from(rate.num)) % (i128::from(at.den) * i128::from(rate.den))
            != 0
    {
        return Err(graph_invalid(
            "Graph time must lie on an exact frame; no rounding is permitted",
        ));
    }
    Ok(())
}

fn graph_interval(
    at: RationalTime,
    duration: RationalTime,
    limit: RationalTime,
    rate: Option<RationalTime>,
) -> Result<()> {
    graph_time(at)?;
    graph_time(duration)?;
    graph_time(limit)?;
    if at.num < 0
        || duration.num <= 0
        || graph_compare_time(graph_add_time(at, duration)?, limit).is_gt()
    {
        return Err(graph_invalid(
            "Graph interval must fit its containing interval",
        ));
    }
    if let Some(rate) = rate {
        graph_exact_frame(at, rate)?;
        graph_exact_frame(duration, rate)?;
    }
    Ok(())
}

fn graph_positive(value: f64) -> bool {
    value.is_finite() && value > 0.0
}

fn graph_point(point: &GraphPoint, frame: &GraphReferenceFrame) -> Result<()> {
    if !point.x.is_finite()
        || !point.y.is_finite()
        || point.x < 0.0
        || point.y < 0.0
        || point.x > frame.width as f64
        || point.y > frame.height as f64
    {
        return Err(graph_invalid("Graph point is outside its authored frame"));
    }
    Ok(())
}

fn graph_box(value: &GraphBox, frame: &GraphReferenceFrame) -> Result<()> {
    graph_point(
        &GraphPoint {
            x: value.x,
            y: value.y,
        },
        frame,
    )?;
    if !graph_positive(value.width)
        || !graph_positive(value.height)
        || value.x + value.width > frame.width as f64
        || value.y + value.height > frame.height as f64
    {
        return Err(graph_invalid(
            "Graph box extends outside its authored frame",
        ));
    }
    Ok(())
}

impl GraphSourceSnapshot {
    fn validate(&self) -> Result<()> {
        if self.revision > GRAPH_SAFE_INTEGER_MAX {
            return Err(graph_invalid(
                "Graph source revision cannot be represented exactly by Studio",
            ));
        }
        Ok(())
    }

    pub fn from_project(project: &Project) -> Self {
        Self {
            project_id: project.id,
            generation: project.generation,
            branch_id: project.active_branch,
            revision: project.revision,
        }
    }

    pub fn require_current(&self, project: &Project) -> Result<()> {
        self.validate()?;
        if self != &Self::from_project(project) {
            return Err(graph_invalid(
                "Graph source changed; read the selected Project again",
            ));
        }
        Ok(())
    }
}

impl GraphSubjectRef {
    fn validate(&self) -> Result<()> {
        match self {
            Self::Scene { .. } => Ok(()),
            Self::Claim { content_sha256, .. } | Self::Asset { content_sha256, .. } => {
                graph_digest(content_sha256)
            }
        }
    }
}

impl GraphTargetBinding {
    fn validate(&self) -> Result<()> {
        match self {
            Self::CanvasNode { content_sha256, .. } | Self::Claim { content_sha256, .. } => {
                graph_digest(content_sha256)
            }
            Self::MeasuredDatum {
                table_version,
                datum_key,
                source_sha256,
                ..
            } => {
                if *table_version == 0 {
                    return Err(graph_invalid("Graph table version must be positive"));
                }
                graph_text(datum_key, 64, "datum key")?;
                graph_digest(source_sha256)
            }
        }
    }
}

impl GraphContent {
    pub fn vertices(&self) -> &[GraphVertex] {
        match self {
            Self::CausalDiagram { vertices, .. } | Self::SystemFlow { vertices, .. } => vertices,
        }
    }
    pub fn edge_ids(&self) -> Vec<Uuid> {
        match self {
            Self::CausalDiagram { edges, .. } => edges.iter().map(|edge| edge.id).collect(),
            Self::SystemFlow { edges, .. } => edges.iter().map(|edge| edge.id).collect(),
        }
    }
}

fn graph_port_point(vertex: &GraphVertex, port: GraphPort) -> GraphPoint {
    let b = &vertex.r#box;
    match port {
        GraphPort::Top => GraphPoint {
            x: b.x + b.width / 2.0,
            y: b.y,
        },
        GraphPort::Right => GraphPoint {
            x: b.x + b.width,
            y: b.y + b.height / 2.0,
        },
        GraphPort::Bottom => GraphPoint {
            x: b.x + b.width / 2.0,
            y: b.y + b.height,
        },
        GraphPort::Left => GraphPoint {
            x: b.x,
            y: b.y + b.height / 2.0,
        },
    }
}

/// Euclidean arc-length midpoint of a nonempty finite polyline. Repeated
/// points contribute zero length; an all-zero polyline returns its first point.
/// This arithmetic helper does not admit a Graph or relax its segment guards.
pub fn graph_polyline_midpoint(points: &[GraphPoint]) -> Result<GraphPoint> {
    let first = points
        .first()
        .ok_or_else(|| graph_invalid("Graph midpoint needs a nonempty polyline"))?;
    if points
        .iter()
        .any(|point| !point.x.is_finite() || !point.y.is_finite())
    {
        return Err(graph_invalid("Graph midpoint contains a non-finite point"));
    }
    let mut total = 0.0;
    for pair in points.windows(2) {
        let length = (pair[1].x - pair[0].x).hypot(pair[1].y - pair[0].y);
        total += length;
        if !total.is_finite() {
            return Err(graph_invalid(
                "Graph polyline length exceeds finite geometry",
            ));
        }
    }
    if total == 0.0 {
        return Ok(first.clone());
    }
    // Match Studio's remaining-distance subtraction order, not a separately
    // accumulated prefix, so avoid an unnecessary cross-language rounding join.
    let mut remaining = total / 2.0;
    for pair in points.windows(2) {
        let dx = pair[1].x - pair[0].x;
        let dy = pair[1].y - pair[0].y;
        let length = dx.hypot(dy);
        if length == 0.0 {
            continue;
        }
        if remaining <= length {
            let fraction = remaining / length;
            let midpoint = GraphPoint {
                x: pair[0].x + dx * fraction,
                y: pair[0].y + dy * fraction,
            };
            if !midpoint.x.is_finite() || !midpoint.y.is_finite() {
                return Err(graph_invalid("Graph midpoint exceeds finite geometry"));
            }
            return Ok(midpoint);
        }
        remaining -= length;
    }
    Err(graph_invalid("Graph midpoint could not be resolved"))
}

struct GraphEdgeView<'a> {
    id: Uuid,
    label: &'a str,
    from_vertex_id: Uuid,
    to_vertex_id: Uuid,
    from_port: GraphPort,
    to_port: GraphPort,
    waypoints: &'a [GraphPoint],
    label_box: &'a GraphBox,
    label_placement: Option<&'a GraphLabelPlacement>,
    appearance: &'a GraphAppearance,
}

impl GraphCausalEdge {
    /// Resolve from the current endpoint ports and the unchanged authored path.
    /// This is pure geometry; Project/source/native admission remains separate.
    pub fn resolved_label_box(&self, graph: &SemanticGraph) -> Result<GraphBox> {
        graph_resolved_label_box(&self.geometry(), graph)
    }

    fn geometry(&self) -> GraphEdgeView<'_> {
        GraphEdgeView {
            id: self.id,
            label: &self.label,
            from_vertex_id: self.from_vertex_id,
            to_vertex_id: self.to_vertex_id,
            from_port: self.from_port,
            to_port: self.to_port,
            waypoints: &self.waypoints,
            label_box: &self.label_box,
            label_placement: self.label_placement.as_ref(),
            appearance: &self.appearance,
        }
    }
}
impl GraphFlowEdge {
    /// Every event-label interval uses this same current derived box.
    pub fn resolved_label_box(&self, graph: &SemanticGraph) -> Result<GraphBox> {
        graph_resolved_label_box(&self.geometry(), graph)
    }

    fn geometry(&self) -> GraphEdgeView<'_> {
        GraphEdgeView {
            id: self.id,
            label: &self.label,
            from_vertex_id: self.from_vertex_id,
            to_vertex_id: self.to_vertex_id,
            from_port: self.from_port,
            to_port: self.to_port,
            waypoints: &self.waypoints,
            label_box: &self.label_box,
            label_placement: self.label_placement.as_ref(),
            appearance: &self.appearance,
        }
    }
}

fn graph_resolved_label_box(edge: &GraphEdgeView<'_>, graph: &SemanticGraph) -> Result<GraphBox> {
    let frame = &graph.reference_frame;
    if frame.width == 0
        || frame.height == 0
        || frame.width > GRAPH_SAFE_INTEGER_MAX
        || frame.height > GRAPH_SAFE_INTEGER_MAX
    {
        return Err(graph_invalid("unsupported Graph authored frame"));
    }
    // The authored manual box remains valid and unchanged, including in auto
    // mode. Width and height always come from that exact authored box.
    graph_box(edge.label_box, frame)?;
    let Some(GraphLabelPlacement::PolylineMidpoint { offset }) = edge.label_placement else {
        return Ok(edge.label_box.clone());
    };
    if !offset.x.is_finite() || !offset.y.is_finite() {
        return Err(graph_invalid(
            "Graph label offset must be a finite signed vector",
        ));
    }
    if edge.waypoints.len() > SEMANTIC_GRAPH_MAX_WAYPOINTS {
        return Err(graph_invalid(
            "Graph label path exceeds the waypoint budget",
        ));
    }
    let from = graph
        .content
        .vertices()
        .iter()
        .find(|vertex| vertex.id == edge.from_vertex_id)
        .ok_or_else(|| graph_invalid("Graph source vertex is missing"))?;
    let to = graph
        .content
        .vertices()
        .iter()
        .find(|vertex| vertex.id == edge.to_vertex_id)
        .ok_or_else(|| graph_invalid("Graph destination vertex is missing"))?;
    graph_box(&from.r#box, frame)?;
    graph_box(&to.r#box, frame)?;
    let mut points = Vec::with_capacity(edge.waypoints.len() + 2);
    points.push(graph_port_point(from, edge.from_port));
    points.extend_from_slice(edge.waypoints);
    points.push(graph_port_point(to, edge.to_port));
    for point in &points {
        graph_point(point, frame)?;
    }
    let midpoint = graph_polyline_midpoint(&points)?;
    let resolved = GraphBox {
        x: midpoint.x + offset.x - edge.label_box.width / 2.0,
        y: midpoint.y + offset.y - edge.label_box.height / 2.0,
        width: edge.label_box.width,
        height: edge.label_box.height,
    };
    graph_box(&resolved, frame)?;
    Ok(resolved)
}

fn graph_edge_shape(
    edge: GraphEdgeView<'_>,
    graph: &SemanticGraph,
    ids: &mut HashSet<Uuid>,
    rate: Option<RationalTime>,
) -> Result<()> {
    if !ids.insert(edge.id) {
        return Err(graph_invalid("duplicate semantic Graph identity"));
    }
    graph_text(edge.label, 256, "edge label")?;
    let from = graph
        .content
        .vertices()
        .iter()
        .find(|v| v.id == edge.from_vertex_id)
        .ok_or_else(|| graph_invalid("Graph source vertex is missing"))?;
    let to = graph
        .content
        .vertices()
        .iter()
        .find(|v| v.id == edge.to_vertex_id)
        .ok_or_else(|| graph_invalid("Graph destination vertex is missing"))?;
    if from.id == to.id || edge.waypoints.len() > SEMANTIC_GRAPH_MAX_WAYPOINTS {
        return Err(graph_invalid(
            "Graph edge needs distinct endpoints and bounded waypoints",
        ));
    }
    graph_resolved_label_box(&edge, graph)?;
    let mut previous = graph_port_point(from, edge.from_port);
    let destination = graph_port_point(to, edge.to_port);
    for point in edge.waypoints.iter().chain(std::iter::once(&destination)) {
        graph_point(point, &graph.reference_frame)?;
        if *point == previous {
            return Err(graph_invalid("Graph edge has a degenerate segment"));
        }
        previous = point.clone();
    }
    graph_interval(
        edge.appearance.at,
        edge.appearance.duration,
        graph.time_range.duration,
        rate,
    )?;
    if graph_compare_time(
        edge.appearance.at,
        graph_add_time(from.appearance.at, from.appearance.duration)?,
    )
    .is_lt()
        || graph_compare_time(
            edge.appearance.at,
            graph_add_time(to.appearance.at, to.appearance.duration)?,
        )
        .is_lt()
    {
        return Err(graph_invalid(
            "Graph edges begin only after both vertices are fully visible",
        ));
    }
    Ok(())
}

impl SemanticGraph {
    /// Historical shape validation only. This never grants a write or reports
    /// native readiness; current references, source CAS and fonts are separate.
    pub fn validate_shape(&self, rate: Option<RationalTime>) -> Result<()> {
        if self.component_version != SEMANTIC_GRAPH_COMPONENT_VERSION
            || self.reference_frame.width == 0
            || self.reference_frame.height == 0
            || self.reference_frame.width > GRAPH_SAFE_INTEGER_MAX
            || self.reference_frame.height > GRAPH_SAFE_INTEGER_MAX
        {
            return Err(graph_invalid(
                "unsupported Graph component or authored frame",
            ));
        }
        self.subject_ref.validate()?;
        graph_time(self.time_range.start)?;
        graph_time(self.time_range.duration)?;
        if self.time_range.start.num < 0 || self.time_range.duration.num <= 0 {
            return Err(graph_invalid(
                "Graph interval requires nonnegative start and positive duration",
            ));
        }
        graph_add_time(self.time_range.start, self.time_range.duration)?;
        if let Some(rate) = rate {
            graph_exact_frame(self.time_range.start, rate)?;
            graph_exact_frame(self.time_range.duration, rate)?;
        }
        self.authorship.source_snapshot.validate()?;
        graph_text(&self.authorship.author_label, 160, "author label")?;
        graph_text(&self.authorship.rationale, 2000, "rationale")?;
        if let Some(brand) = &self.brand_profile_ref {
            if brand.version == 0 || brand.version > GRAPH_SAFE_INTEGER_MAX {
                return Err(graph_invalid(
                    "Graph brand version is outside the exact supported range",
                ));
            }
            graph_digest(&brand.content_sha256)?;
        }
        let style = &self.style;
        for color in [
            &style.background,
            &style.vertex_fill,
            &style.text_color,
            &style.edge_color,
            &style.accent_color,
        ] {
            if color.len() != 7
                || !color.starts_with('#')
                || !color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
            {
                return Err(graph_invalid("Graph color must be #RRGGBB"));
            }
        }
        if !graph_positive(style.font_size) || !graph_positive(style.edge_width) {
            return Err(graph_invalid(
                "Graph font size and edge width must be finite and positive",
            ));
        }
        graph_box(&style.disclosure_box, &self.reference_frame)?;
        let vertices = self.content.vertices();
        if vertices.is_empty()
            || vertices.len() > SEMANTIC_GRAPH_MAX_VERTICES
            || self.content.edge_ids().len() > SEMANTIC_GRAPH_MAX_EDGES
        {
            return Err(graph_invalid("Graph vertex or edge budget exceeded"));
        }
        let mut ids = HashSet::new();
        for vertex in vertices {
            if !ids.insert(vertex.id) {
                return Err(graph_invalid("duplicate semantic Graph identity"));
            }
            graph_text(&vertex.label, 256, "vertex label")?;
            graph_text(&vertex.meaning, 2000, "vertex meaning")?;
            graph_box(&vertex.r#box, &self.reference_frame)?;
            if vertex.shape == GraphVertexShape::Circle && vertex.r#box.width != vertex.r#box.height
            {
                return Err(graph_invalid(
                    "Graph circle vertices require equal width and height",
                ));
            }
            if let GraphUnit::Symbol { symbol } = &vertex.unit {
                graph_text(symbol, 64, "unit symbol")?;
            }
            if let Some(target) = &vertex.target {
                target.validate()?;
            }
            graph_interval(
                vertex.appearance.at,
                vertex.appearance.duration,
                self.time_range.duration,
                rate,
            )?;
        }
        match &self.content {
            GraphContent::CausalDiagram { edges, .. } => {
                for edge in edges {
                    graph_edge_shape(edge.geometry(), self, &mut ids, rate)?;
                    match &edge.basis {
                        GraphCausalBasis::AuthorHypothesis { disclosure } => {
                            graph_text(disclosure, 2000, "causal disclosure")?
                        }
                        GraphCausalBasis::ClaimReference { content_sha256, .. } => {
                            graph_digest(content_sha256)?
                        }
                    }
                }
            }
            GraphContent::SystemFlow {
                source,
                edges,
                events,
                ..
            } => {
                let GraphSyntheticFlowSource::Synthetic { disclosure } = source;
                graph_text(disclosure, 2000, "synthetic flow disclosure")?;
                for edge in edges {
                    graph_edge_shape(edge.geometry(), self, &mut ids, rate)?;
                }
                if events.len() > SEMANTIC_GRAPH_MAX_EVENTS {
                    return Err(graph_invalid("Graph event budget exceeded"));
                }
                for event in events {
                    if !ids.insert(event.id) {
                        return Err(graph_invalid("duplicate semantic Graph identity"));
                    }
                    graph_text(&event.label, 256, "event label")?;
                    let edge = edges
                        .iter()
                        .find(|edge| edge.id == event.edge_id)
                        .ok_or_else(|| {
                            graph_invalid("Graph flow event references a missing edge")
                        })?;
                    graph_interval(event.at, event.duration, self.time_range.duration, rate)?;
                    if graph_compare_time(
                        event.at,
                        graph_add_time(edge.appearance.at, edge.appearance.duration)?,
                    )
                    .is_lt()
                    {
                        return Err(graph_invalid(
                            "Graph flow event starts before its edge is fully visible",
                        ));
                    }
                    for other in events
                        .iter()
                        .filter(|other| other.id != event.id && other.edge_id == event.edge_id)
                    {
                        if graph_compare_time(event.at, graph_add_time(other.at, other.duration)?)
                            .is_lt()
                            && graph_compare_time(
                                other.at,
                                graph_add_time(event.at, event.duration)?,
                            )
                            .is_lt()
                        {
                            return Err(graph_invalid(
                                "overlapping Graph events on one edge are not admitted",
                            ));
                        }
                    }
                }
            }
        }
        Ok(())
    }
}

fn graph_resource_uuid(value: &str) -> Option<Uuid> {
    if value.len() != 36
        || value.bytes().enumerate().any(|(index, byte)| {
            if [8, 13, 18, 23].contains(&index) {
                byte != b'-'
            } else {
                !byte.is_ascii_hexdigit()
            }
        })
    {
        return None;
    }
    let id = Uuid::parse_str(value).ok()?;
    // Locks compare resource strings exactly. Accept only the spelling produced
    // by Uuid's canonical display, so lookup/review cannot recognize an alias
    // that the lock check would fail to match.
    (id.hyphenated().to_string() == value).then_some(id)
}

/// Graph resource identities have one spelling. Do not require the target to
/// exist here: a canonical lock may protect a child before that child is added.
/// All non-Graph resource semantics remain with their existing owners.
pub(crate) fn validate_semantic_graph_resource_spelling(resource: &str) -> Result<()> {
    let parts: Vec<_> = resource.split(':').collect();
    let Some(kind) = parts.first() else {
        return Ok(());
    };
    if !matches!(*kind, "graph" | "graph-vertex" | "graph-edge") {
        return Ok(());
    }
    let canonical = match parts.as_slice() {
        ["graph", graph_id] => graph_resource_uuid(graph_id).is_some(),
        ["graph-vertex" | "graph-edge", graph_id, child_id] => {
            graph_resource_uuid(graph_id).is_some() && graph_resource_uuid(child_id).is_some()
        }
        _ => false,
    };
    if !canonical {
        return Err(graph_invalid(
            "Graph resources require exact canonical lowercase UUIDs and resource syntax",
        ));
    }
    Ok(())
}

pub(crate) fn validate_semantic_graph_lock_resources(locks: &[ProjectLock]) -> Result<()> {
    for lock in locks {
        validate_semantic_graph_resource_spelling(&lock.resource)?;
    }
    Ok(())
}

/// Reopening a saved lock/review must not defer identity checks until a later
/// mutation. Other resource namespaces retain their existing String codec.
pub(crate) fn deserialize_semantic_graph_resource<'de, D>(
    deserializer: D,
) -> std::result::Result<String, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let resource = String::deserialize(deserializer)?;
    validate_semantic_graph_resource_spelling(&resource).map_err(serde::de::Error::custom)?;
    Ok(resource)
}

/// Exact existing-review resource lookup against the caller's selected saved
/// graph collection. Passing another branch's collection is not permitted by
/// the eventual Project/BranchState review adapter; this helper grants nothing.
pub fn semantic_graph_resource_exists(graphs: &[SemanticGraph], resource: &str) -> bool {
    let parts: Vec<_> = resource.split(':').collect();
    match parts.as_slice() {
        ["graph", graph_id] => graph_resource_uuid(graph_id)
            .is_some_and(|id| graphs.iter().any(|graph| graph.id == id)),
        [kind @ ("graph-vertex" | "graph-edge"), graph_id, child_id] => {
            let (Some(graph_id), Some(child_id)) =
                (graph_resource_uuid(graph_id), graph_resource_uuid(child_id))
            else {
                return false;
            };
            graphs
                .iter()
                .find(|graph| graph.id == graph_id)
                .is_some_and(|graph| {
                    if *kind == "graph-vertex" {
                        graph
                            .content
                            .vertices()
                            .iter()
                            .any(|vertex| vertex.id == child_id)
                    } else {
                        graph.content.edge_ids().contains(&child_id)
                    }
                })
        }
        _ => false,
    }
}

/// Proposed domain codec: hash the typed canonical Claim serialization, including
/// its source and source_revision. UI consumes this descriptor; it must not hash
/// a separate JavaScript projection. This is content binding, not an attestation.
pub fn semantic_graph_claim_digest(claim: &Claim) -> Result<String> {
    let bytes = serde_json::to_vec(claim).map_err(|error| graph_invalid(error.to_string()))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

/// The complete typed CanvasNode is bound, including geometry, style, locks,
/// hierarchy, relations and keyframes. No UI-only subset is authoritative.
pub fn semantic_graph_node_digest(node: &CanvasNode) -> Result<String> {
    let bytes = serde_json::to_vec(node).map_err(|error| graph_invalid(error.to_string()))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

fn graph_profile<'a>(
    graph: &SemanticGraph,
    scenes: &'a [Scene],
    profiles: &'a [DeliverableProfile],
) -> Result<(&'a Scene, &'a DeliverableProfile)> {
    let scene = scenes
        .iter()
        .find(|scene| scene.id == graph.scene_id)
        .ok_or_else(|| DomainError::NotFound(format!("scene:{}", graph.scene_id)))?;
    let profile = profiles
        .iter()
        .find(|profile| profile.id == graph.variant_profile_ref)
        .ok_or_else(|| {
            DomainError::NotFound(format!("deliverable:{}", graph.variant_profile_ref))
        })?;
    graph.validate_shape(Some(profile.frame_rate))?;
    if graph.reference_frame.width != u64::from(profile.width)
        || graph.reference_frame.height != u64::from(profile.height)
    {
        return Err(graph_invalid(
            "Graph reference frame changed; explicit author review is required",
        ));
    }
    if !profile.included_scene_ids.is_empty() && !profile.included_scene_ids.contains(&scene.id) {
        return Err(graph_invalid(
            "Graph scene is excluded from its authored Deliverable",
        ));
    }
    if graph_compare_time(
        graph_add_time(graph.time_range.start, graph.time_range.duration)?,
        scene.duration,
    )
    .is_gt()
    {
        return Err(graph_invalid(
            "Graph interval exceeds its scene; no automatic trim is permitted",
        ));
    }
    Ok((scene, profile))
}

impl ProductionDesign {
    /// Validate all saved snapshots without turning historical authorship into
    /// a current CAS token. Import/reopen must not rewrite generation or digests.
    /// Current dependency fingerprints are checked by each write and each use.
    pub fn validate_semantic_graphs(
        &self,
        scenes: &[Scene],
        profiles: &[DeliverableProfile],
    ) -> Result<()> {
        if self.semantic_graphs.len() > SEMANTIC_GRAPH_MAX_GRAPHS {
            return Err(graph_invalid("Project semantic Graph budget exceeded"));
        }
        let mut identities = HashSet::new();
        let mut scene_profiles = HashSet::new();
        for graph in &self.semantic_graphs {
            if !identities.insert(graph.id)
                || !scene_profiles.insert((graph.scene_id, graph.variant_profile_ref))
            {
                return Err(graph_invalid("duplicate Graph identity or scene/profile"));
            }
            graph_profile(graph, scenes, profiles)?;
            self.validate_semantic_graph_brand(graph)?;
        }
        Ok(())
    }

    fn validate_semantic_graph_brand(&self, graph: &SemanticGraph) -> Result<()> {
        let Some(brand) = &self.brand_profile else {
            return Ok(());
        };
        let digest = brand.content_digest()?;
        let mut texts = Vec::new();
        for vertex in graph.content.vertices() {
            texts.extend([vertex.label.as_str(), vertex.meaning.as_str()]);
            if let GraphUnit::Symbol { symbol } = &vertex.unit {
                texts.push(symbol.as_str());
            }
        }
        match &graph.content {
            GraphContent::CausalDiagram { edges, .. } => {
                for edge in edges {
                    texts.push(&edge.label);
                    if let GraphCausalBasis::AuthorHypothesis { disclosure } = &edge.basis {
                        texts.push(disclosure);
                    }
                }
            }
            GraphContent::SystemFlow {
                source,
                edges,
                events,
                ..
            } => {
                let GraphSyntheticFlowSource::Synthetic { disclosure } = source;
                texts.push(disclosure);
                texts.extend(edges.iter().map(|edge| edge.label.as_str()));
                texts.extend(events.iter().map(|event| event.label.as_str()));
            }
        }
        for rule in &brand.rules {
            if self.brand_exceptions.iter().any(|exception| {
                exception.scene_id == graph.scene_id
                    && exception.rule_id == rule.id()
                    && exception.brand_sha256 == digest
            }) {
                continue;
            }
            let violation = match rule {
                BrandRule::ForbiddenPhrase { phrase, .. } => texts
                    .iter()
                    .any(|text| text.to_lowercase().contains(&phrase.to_lowercase())),
                BrandRule::AllowedAccents { colors, .. } => [
                    &graph.style.accent_color,
                    &graph.style.edge_color,
                    &graph.style.vertex_fill,
                ]
                .iter()
                .any(|color| {
                    !colors
                        .iter()
                        .any(|allowed| allowed.eq_ignore_ascii_case(color))
                }),
                BrandRule::RequiredWordmark { text, .. } => !graph
                    .content
                    .vertices()
                    .iter()
                    .any(|vertex| vertex.label.contains(text)),
            };
            if violation {
                return Err(graph_invalid(
                    "Graph violates the active BrandProfile rule without a current scene exception",
                ));
            }
        }
        Ok(())
    }
}

impl Project {
    /// Backend descriptors from the selected Project. This never reads caller
    /// paths, trusts caller fingerprints or grants a native execution capability.
    pub fn semantic_graph_dependencies(&self) -> Result<Vec<GraphDependencyDescriptor>> {
        let mut result = Vec::new();
        for scene in &self.scenes {
            result.push(GraphDependencyDescriptor::Scene { scene_id: scene.id });
            for node in &scene.nodes {
                result.push(GraphDependencyDescriptor::CanvasNode {
                    scene_id: scene.id,
                    node_id: node.id,
                    content_sha256: semantic_graph_node_digest(node)?,
                });
            }
        }
        for claim in &self.brief.claims {
            result.push(GraphDependencyDescriptor::Claim {
                claim_id: claim.id,
                content_sha256: semantic_graph_claim_digest(claim)?,
            });
        }
        for asset in &self.assets {
            if let Some(content_sha256) = &asset.content_sha256 {
                graph_digest(content_sha256)?;
                result.push(GraphDependencyDescriptor::Asset {
                    asset_id: asset.id,
                    content_sha256: content_sha256.clone(),
                });
            }
        }
        for table in &self.production_design.measured_tables {
            for row in &table.rows {
                result.push(GraphDependencyDescriptor::MeasuredDatum {
                    table_id: table.id,
                    table_version: table.version,
                    datum_key: row.key.clone(),
                    source_sha256: table.source_sha256.clone(),
                });
            }
        }
        if let Some(brand) = &self.production_design.brand_profile {
            if brand.version > GRAPH_SAFE_INTEGER_MAX {
                return Err(graph_invalid(
                    "Graph brand descriptor cannot be represented exactly by Studio",
                ));
            }
            result.push(GraphDependencyDescriptor::Brand {
                id: brand.id,
                version: brand.version,
                content_sha256: brand.content_digest()?,
            });
        }
        Ok(result)
    }

    /// Current content checks are deliberately separate from historical shape.
    /// A subsequent Claim/Canvas edit preserves the old source; attempting to
    /// reuse that stale source requires explicit rebinding before a write/use.
    pub fn validate_semantic_graph_dependencies(&self, graph: &SemanticGraph) -> Result<()> {
        let descriptors = self.semantic_graph_dependencies()?;
        let require = |descriptor: GraphDependencyDescriptor| -> Result<()> {
            if !descriptors.contains(&descriptor) {
                return Err(graph_invalid(
                    "Graph dependency is stale or has no current domain fingerprint",
                ));
            }
            Ok(())
        };
        match &graph.subject_ref {
            GraphSubjectRef::Scene { scene_id } => require(GraphDependencyDescriptor::Scene {
                scene_id: *scene_id,
            })?,
            GraphSubjectRef::Claim {
                claim_id,
                content_sha256,
            } => require(GraphDependencyDescriptor::Claim {
                claim_id: *claim_id,
                content_sha256: content_sha256.clone(),
            })?,
            GraphSubjectRef::Asset {
                asset_id,
                content_sha256,
            } => require(GraphDependencyDescriptor::Asset {
                asset_id: *asset_id,
                content_sha256: content_sha256.clone(),
            })?,
        }
        for vertex in graph.content.vertices() {
            match &vertex.target {
                None => {}
                Some(GraphTargetBinding::CanvasNode {
                    scene_id,
                    node_id,
                    content_sha256,
                }) => require(GraphDependencyDescriptor::CanvasNode {
                    scene_id: *scene_id,
                    node_id: *node_id,
                    content_sha256: content_sha256.clone(),
                })?,
                Some(GraphTargetBinding::Claim {
                    claim_id,
                    content_sha256,
                }) => require(GraphDependencyDescriptor::Claim {
                    claim_id: *claim_id,
                    content_sha256: content_sha256.clone(),
                })?,
                Some(GraphTargetBinding::MeasuredDatum {
                    table_id,
                    table_version,
                    datum_key,
                    source_sha256,
                }) => {
                    require(GraphDependencyDescriptor::MeasuredDatum {
                        table_id: *table_id,
                        table_version: *table_version,
                        datum_key: datum_key.clone(),
                        source_sha256: source_sha256.clone(),
                    })?;
                    let row = self
                        .production_design
                        .measured_tables
                        .iter()
                        .find(|table| table.id == *table_id)
                        .and_then(|table| table.rows.iter().find(|row| row.key == *datum_key))
                        .ok_or_else(|| graph_invalid("Graph measured datum is missing"))?;
                    // Preserve the original TS wire comparison against the enum
                    // string, not the presentation suffix (%, ms, B, ...).
                    let unit = match row.unit {
                        MeasuredUnit::Count => "count",
                        MeasuredUnit::Percent => "percent",
                        MeasuredUnit::Seconds => "seconds",
                        MeasuredUnit::Milliseconds => "milliseconds",
                        MeasuredUnit::Bytes => "bytes",
                    };
                    if !matches!(&vertex.unit, GraphUnit::Symbol { symbol } if symbol == unit) {
                        return Err(graph_invalid(
                            "Graph measured binding requires the exact datum unit",
                        ));
                    }
                }
            }
        }
        if let GraphContent::CausalDiagram { edges, .. } = &graph.content {
            for edge in edges {
                if let GraphCausalBasis::ClaimReference {
                    claim_id,
                    content_sha256,
                } = &edge.basis
                {
                    require(GraphDependencyDescriptor::Claim {
                        claim_id: *claim_id,
                        content_sha256: content_sha256.clone(),
                    })?;
                }
            }
        }
        if let Some(brand) = &graph.brand_profile_ref {
            require(GraphDependencyDescriptor::Brand {
                id: brand.id,
                version: brand.version,
                content_sha256: brand.content_sha256.clone(),
            })?;
        }
        self.production_design.validate_semantic_graph_brand(graph)
    }

    fn ensure_semantic_graph_unlocked(
        &self,
        graph: &SemanticGraph,
        previous: Option<&SemanticGraph>,
    ) -> Result<()> {
        let kinds = [
            LockKind::Content,
            LockKind::Timing,
            LockKind::Position,
            LockKind::Style,
            LockKind::Renderer,
        ];
        self.ensure_unlocked(&self.resource_key(), &kinds)?;
        self.ensure_unlocked(&format!("scene:{}", graph.scene_id), &kinds)?;
        self.ensure_unlocked(&format!("graph:{}", graph.id), &kinds)?;
        for source in std::iter::once(graph).chain(previous) {
            for vertex in source.content.vertices() {
                self.ensure_unlocked(&format!("graph-vertex:{}:{}", graph.id, vertex.id), &kinds)?;
            }
            for edge_id in source.content.edge_ids() {
                self.ensure_unlocked(&format!("graph-edge:{}:{edge_id}", graph.id), &kinds)?;
            }
        }
        Ok(())
    }

    pub fn validate_semantic_graph_for_write(&self, graph: &SemanticGraph) -> Result<()> {
        graph.authorship.source_snapshot.require_current(self)?;
        let (scene, _) = graph_profile(graph, &self.scenes, &self.deliverables)?;
        if scene.renderer != RendererKind::ManimCommunity {
            return Err(graph_invalid(
                "Semantic Graph source requires the declared Manim Community scene",
            ));
        }
        let all = &self.production_design.semantic_graphs;
        let previous = all.iter().find(|previous| previous.id == graph.id);
        if let Some(previous) = previous {
            if previous.scene_id != graph.scene_id
                || std::mem::discriminant(&previous.content)
                    != std::mem::discriminant(&graph.content)
            {
                return Err(graph_invalid(
                    "Graph identity cannot change scene or semantic kind",
                ));
            }
        } else if all.len() >= SEMANTIC_GRAPH_MAX_GRAPHS {
            return Err(graph_invalid("Project semantic Graph budget exceeded"));
        }
        if all.iter().any(|previous| {
            previous.id != graph.id
                && previous.scene_id == graph.scene_id
                && previous.variant_profile_ref == graph.variant_profile_ref
        }) {
            return Err(graph_invalid(
                "Only one Graph per scene/profile is admitted",
            ));
        }
        self.ensure_semantic_graph_unlocked(graph, previous)?;
        self.validate_semantic_graph_dependencies(graph)
    }

    pub(crate) fn upsert_semantic_graph(&mut self, graph: &SemanticGraph) -> Result<()> {
        self.validate_semantic_graph_for_write(graph)?;
        if let Some(index) = self
            .production_design
            .semantic_graphs
            .iter()
            .position(|saved| saved.id == graph.id)
        {
            self.production_design.semantic_graphs[index] = graph.clone();
        } else {
            self.production_design.semantic_graphs.push(graph.clone());
        }
        Ok(())
    }

    pub(crate) fn remove_semantic_graph(
        &mut self,
        graph_id: Uuid,
        scene_id: Uuid,
        source: &GraphSourceSnapshot,
    ) -> Result<()> {
        source.require_current(self)?;
        let graph = self
            .production_design
            .semantic_graphs
            .iter()
            .find(|graph| graph.id == graph_id && graph.scene_id == scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("graph:{graph_id}")))?;
        self.ensure_semantic_graph_unlocked(graph, None)?;
        self.production_design
            .semantic_graphs
            .retain(|graph| graph.id != graph_id);
        Ok(())
    }

    /// A new review targets a saved object on the selected branch and preserves
    /// its exact profile/locale. Its authored point or inclusive range must fit
    /// the saved absolute Graph interval, as in Studio's reviewTimes validation.
    /// Historical reviews retain their anchor after edits, deletion or checkout.
    pub(crate) fn validate_semantic_graph_review_anchor(
        &self,
        resource: &str,
        start: Option<RationalTime>,
        end: Option<RationalTime>,
        locale: Option<&str>,
        profile_id: Option<Uuid>,
    ) -> Result<()> {
        if !["graph:", "graph-vertex:", "graph-edge:"]
            .iter()
            .any(|prefix| resource.starts_with(prefix))
        {
            return Ok(());
        }
        if !semantic_graph_resource_exists(&self.production_design.semantic_graphs, resource) {
            return Err(DomainError::NotFound(resource.to_owned()));
        }
        let graph_id = resource
            .split(':')
            .nth(1)
            .and_then(graph_resource_uuid)
            .ok_or_else(|| DomainError::NotFound(resource.to_owned()))?;
        let graph = self
            .production_design
            .semantic_graphs
            .iter()
            .find(|graph| graph.id == graph_id)
            .ok_or_else(|| DomainError::NotFound(resource.to_owned()))?;
        let (scene, profile) = graph_profile(graph, &self.scenes, &self.deliverables)?;
        let expected_start = graph_add_time(scene.start, graph.time_range.start)?;
        let expected_end = graph_add_time(expected_start, graph.time_range.duration)?;
        if locale != Some(profile.language.as_str()) || profile_id != Some(profile.id) {
            return Err(graph_invalid(
                "Graph review must preserve its saved profile and locale",
            ));
        }
        // Resource-only Graph review materializes the whole interval in Studio.
        // A point always has a start; null/null is not a Graph time anchor.
        let start =
            start.ok_or_else(|| graph_invalid("Graph review requires an absolute time anchor"))?;
        graph_time(start)?;
        if graph_compare_time(start, expected_start).is_lt()
            || graph_compare_time(start, expected_end).is_gt()
        {
            return Err(graph_invalid(
                "Graph review start is outside its saved absolute interval",
            ));
        }
        if let Some(end) = end {
            graph_time(end)?;
            if graph_compare_time(end, start).is_lt()
                || graph_compare_time(end, expected_end).is_gt()
            {
                return Err(graph_invalid(
                    "Graph review range is outside its saved absolute interval",
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod label_placement_tests {
    use super::*;
    use serde_json::{Value, json};

    // Authored domain geometry only. This fixture is not a native admission,
    // ready context, persisted document or source of execution authority.
    fn raw_graph(flow: bool) -> Value {
        let id = |value: u128| Uuid::from_u128(value).to_string();
        let vertex = |value, x, y| {
            json!({
                "id": id(value), "label": "Concept", "meaning": "Authored concept",
                "unit": {"kind": "qualitative"},
                "box": {"x": x, "y": y, "width": 100.0, "height": 80.0},
                "shape": "rectangle", "target": null,
                "appearance": {"at": {"num": "0", "den": "1"}, "duration": {"num": "1", "den": "30"}}
            })
        };
        let mut edge = json!({
            "id": id(12), "label": "Relationship", "from_vertex_id": id(10), "to_vertex_id": id(11),
            "from_port": "right", "to_port": "left",
            "waypoints": [{"x": 230.0, "y": 490.0}, {"x": 500.0, "y": 490.0}],
            "label_box": {"x": 300.0, "y": 100.0, "width": 100.0, "height": 40.0},
            "appearance": {"at": {"num": "1", "den": "30"}, "duration": {"num": "1", "den": "30"}}
        });
        let vertices = json!([vertex(10, 130.0, 250.0), vertex(11, 500.0, 830.0)]);
        let content = if flow {
            edge["channel"] = json!("data");
            json!({"kind": "system_flow", "vertices": vertices, "edges": [edge],
                "source": {"kind": "synthetic", "disclosure": "Illustrative flow; no recorded execution."},
                "events": [{"id": id(13), "edge_id": id(12), "label": "Illustrated transfer",
                    "at": {"num": "1", "den": "15"}, "duration": {"num": "1", "den": "30"}}]})
        } else {
            edge["influence"] = json!("unspecified");
            edge["basis"] =
                json!({"kind": "author_hypothesis", "disclosure": "Authored hypothesis."});
            json!({"kind": "causal_diagram", "vertices": vertices, "edges": [edge]})
        };
        json!({"id": id(1), "scene_id": id(2), "component_version": 1,
            "subject_ref": {"kind": "scene", "scene_id": id(2)},
            "time_range": {"start": {"num": "0", "den": "1"}, "duration": {"num": "5", "den": "1"}},
            "brand_profile_ref": null, "variant_profile_ref": id(3),
            "reference_frame": {"width": 1000, "height": 1080}, "motion": "full",
            "style": {"background": "#101820", "vertex_fill": "#263B4F", "text_color": "#FFFFFF",
                "edge_color": "#77C9D4", "accent_color": "#E9B872", "font_family": "Instrument Sans Variable",
                "font_size": 16.0, "edge_width": 2.0,
                "disclosure_box": {"x": 20.0, "y": 1000.0, "width": 400.0, "height": 40.0}},
            "content": content,
            "authorship": {"author_label": "Domain fixture", "rationale": "Pure label geometry",
                "source_snapshot": {"project_id": id(4), "generation": id(5), "branch_id": id(6), "revision": 7}}})
    }

    fn graph(flow: bool, automatic: bool) -> SemanticGraph {
        let mut value = raw_graph(flow);
        if automatic {
            value["content"]["edges"][0]["label_placement"] =
                json!({"kind": "polyline_midpoint", "offset": {"x": -5.0, "y": 10.0}});
        }
        let graph: SemanticGraph = serde_json::from_value(value).unwrap();
        graph
            .validate_shape(Some(RationalTime { num: 30, den: 1 }))
            .unwrap();
        graph
    }

    fn resolve(graph: &SemanticGraph) -> Result<GraphBox> {
        match &graph.content {
            GraphContent::CausalDiagram { edges, .. } => edges[0].resolved_label_box(graph),
            GraphContent::SystemFlow { edges, .. } => edges[0].resolved_label_box(graph),
        }
    }

    fn vertices_mut(graph: &mut SemanticGraph) -> &mut Vec<GraphVertex> {
        match &mut graph.content {
            GraphContent::CausalDiagram { vertices, .. }
            | GraphContent::SystemFlow { vertices, .. } => vertices,
        }
    }

    fn set_offset(graph: &mut SemanticGraph, x: f64, y: f64) {
        let placement = Some(GraphLabelPlacement::PolylineMidpoint {
            offset: GraphPoint { x, y },
        });
        match &mut graph.content {
            GraphContent::CausalDiagram { edges, .. } => edges[0].label_placement = placement,
            GraphContent::SystemFlow { edges, .. } => edges[0].label_placement = placement,
        }
    }

    #[test]
    fn absent_label_placement_preserves_original_wire_and_both_graph_kinds_roundtrip() {
        for flow in [false, true] {
            let manual = graph(flow, false);
            let encoded = serde_json::to_value(&manual).unwrap();
            assert_eq!(encoded, raw_graph(flow));
            assert!(
                encoded["content"]["edges"][0]
                    .get("label_placement")
                    .is_none()
            );
            assert_eq!(encoded["brand_profile_ref"], Value::Null);
            assert_eq!(encoded["content"]["vertices"][0]["target"], Value::Null);
            for source in [manual, graph(flow, true)] {
                let encoded = serde_json::to_vec(&source).unwrap();
                assert_eq!(
                    serde_json::from_slice::<SemanticGraph>(&encoded).unwrap(),
                    source
                );
            }
        }
    }

    #[test]
    fn present_null_incomplete_and_unknown_label_intent_are_closed_wire_errors() {
        for flow in [false, true] {
            for invalid in [
                Value::Null,
                json!("polyline_midpoint"),
                json!(["polyline_midpoint", {"x": 0.0, "y": 0.0}]),
                json!(["polyline_midpoint", {"offset": {"x": 0.0, "y": 0.0}}]),
                json!({"kind": "polyline_midpoint"}),
                json!({"kind": "polyline_midpoint", "offset": null}),
                json!({"kind": "polyline_midpoint", "offset": [0.0, 0.0]}),
                json!({"kind": {"polyline_midpoint": null}, "offset": {"x": 0.0, "y": 0.0}}),
                json!({"kind": "polyline_midpoint", "offset": {"x": 0.0}}),
                json!({"kind": "polyline_midpoint", "offset": {"x": 0.0, "y": 0.0, "z": 0.0}}),
                json!({"kind": "polyline_midpoint", "offset": {"x": 0.0, "y": 0.0}, "ready": true}),
                json!({"kind": "automatic", "offset": {"x": 0.0, "y": 0.0}}),
            ] {
                let mut wire = raw_graph(flow);
                wire["content"]["edges"][0]["label_placement"] = invalid;
                assert!(serde_json::from_value::<SemanticGraph>(wire).is_err());
            }
        }
    }

    #[test]
    fn manual_boxes_do_not_follow_endpoint_edits_or_rewrite_waypoints() {
        for flow in [false, true] {
            let mut graph = graph(flow, false);
            let authored = serde_json::to_value(&graph).unwrap()["content"]["edges"].clone();
            let expected = resolve(&graph).unwrap();
            vertices_mut(&mut graph)[1].r#box.y = 990.0;
            graph
                .validate_shape(Some(RationalTime { num: 30, den: 1 }))
                .unwrap();
            assert_eq!(resolve(&graph).unwrap(), expected);
            assert_eq!(
                serde_json::to_value(&graph).unwrap()["content"]["edges"],
                authored
            );
        }
    }

    #[test]
    fn auto_causal_and_flow_boxes_use_current_ports_fixed_waypoints_and_signed_offset() {
        for flow in [false, true] {
            let mut graph = graph(flow, true);
            let original = graph.clone();
            // Length 200 +270 +380 =850; its midpoint is (455,490).
            assert_eq!(
                resolve(&graph).unwrap(),
                GraphBox {
                    x: 400.0,
                    y: 480.0,
                    width: 100.0,
                    height: 40.0
                }
            );
            assert_eq!(graph, original);
            vertices_mut(&mut graph)[1].r#box.y = 990.0;
            graph
                .validate_shape(Some(RationalTime { num: 30, den: 1 }))
                .unwrap();
            // Only the endpoint moves: length 1010 gives midpoint (500,525).
            assert_eq!(
                resolve(&graph).unwrap(),
                GraphBox {
                    x: 445.0,
                    y: 515.0,
                    width: 100.0,
                    height: 40.0
                }
            );
            assert_eq!(
                serde_json::to_value(&graph).unwrap()["content"]["edges"],
                serde_json::to_value(&original).unwrap()["content"]["edges"]
            );
        }
    }

    #[test]
    fn midpoint_skips_repeated_segments_and_is_total_for_all_zero_paths() {
        let points = vec![
            GraphPoint { x: 230.0, y: 290.0 },
            GraphPoint { x: 230.0, y: 290.0 },
            GraphPoint { x: 230.0, y: 490.0 },
            GraphPoint { x: 230.0, y: 490.0 },
            GraphPoint { x: 500.0, y: 490.0 },
            GraphPoint { x: 500.0, y: 870.0 },
        ];
        assert_eq!(
            graph_polyline_midpoint(&points).unwrap(),
            GraphPoint { x: 455.0, y: 490.0 }
        );
        let first = GraphPoint { x: -4.0, y: 5.0 };
        let repeated = [first.clone(), first.clone(), first.clone(), first.clone()];
        assert_eq!(graph_polyline_midpoint(&repeated).unwrap(), first);
        assert_eq!(
            graph_polyline_midpoint(std::slice::from_ref(&first)).unwrap(),
            first
        );
        assert!(graph_polyline_midpoint(&[]).is_err());
        assert!(
            graph_polyline_midpoint(&[GraphPoint {
                x: f64::NAN,
                y: 0.0
            }])
            .is_err()
        );
        assert!(
            graph_polyline_midpoint(&[
                GraphPoint {
                    x: -f64::MAX,
                    y: 0.0
                },
                GraphPoint {
                    x: f64::MAX,
                    y: 0.0
                },
            ])
            .is_err()
        );
    }

    #[test]
    fn midpoint_arithmetic_does_not_relax_existing_whole_graph_segment_admission() {
        for flow in [false, true] {
            let mut graph = graph(flow, true);
            let start = GraphPoint { x: 230.0, y: 290.0 };
            match &mut graph.content {
                GraphContent::CausalDiagram { edges, .. } => edges[0].waypoints.insert(0, start),
                GraphContent::SystemFlow { edges, .. } => edges[0].waypoints.insert(0, start),
            }
            assert!(resolve(&graph).is_ok());
            let before = graph.clone();
            let error = graph
                .validate_shape(Some(RationalTime { num: 30, den: 1 }))
                .unwrap_err();
            assert!(error.to_string().contains("degenerate segment"));
            assert_eq!(graph, before);
        }
    }

    #[test]
    fn resolved_bounds_reject_without_clamping_fallback_or_source_mutation() {
        for flow in [false, true] {
            for (x, y) in [(-1000.0, 0.0), (1000.0, 0.0), (0.0, -1000.0), (0.0, 1000.0)] {
                let mut graph = graph(flow, true);
                set_offset(&mut graph, x, y);
                let before = graph.clone();
                assert!(resolve(&graph).is_err());
                assert!(graph.validate_shape(None).is_err());
                assert_eq!(graph, before);
            }
            for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
                let mut graph = graph(flow, true);
                set_offset(&mut graph, value, 0.0);
                assert!(resolve(&graph).is_err());
                assert!(graph.validate_shape(None).is_err());
            }
            let mut graph = graph(flow, true);
            graph.reference_frame.width = GRAPH_SAFE_INTEGER_MAX + 1;
            assert!(resolve(&graph).is_err());
        }
    }

    #[test]
    fn auto_labels_require_current_endpoints_and_the_original_bounded_path() {
        for flow in [false, true] {
            let mut missing = graph(flow, true);
            vertices_mut(&mut missing).pop();
            let before = missing.clone();
            assert!(resolve(&missing).is_err());
            assert_eq!(missing, before);
            let mut over_budget = graph(flow, true);
            let points = vec![GraphPoint { x: 400.0, y: 400.0 }; SEMANTIC_GRAPH_MAX_WAYPOINTS + 1];
            match &mut over_budget.content {
                GraphContent::CausalDiagram { edges, .. } => edges[0].waypoints = points,
                GraphContent::SystemFlow { edges, .. } => edges[0].waypoints = points,
            }
            assert!(resolve(&over_budget).is_err());
        }
    }
}
