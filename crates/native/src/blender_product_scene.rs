//! Bounded product-scene projection into public Blender contracts.
//!
//! This module emits data. The existing ProductionCoordinator retains revision,
//! journal, CAS, Broker consent, observation and UNKNOWN handling. No output here
//! is a native execution, source-save, media-fidelity or comparison receipt.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::f64::consts::TAU;

pub const PRODUCT_PROFILE: &str = "blender-product-vessel-v1";
pub const SDK_REVISION: &str = "54d3459a051adbce4cae9f60c0dea8f60c824be6";
pub const FRAME_COUNT: u32 = 1_200;
pub const FPS: u32 = 30;
pub const CUT_FRAME: u32 = 600;
pub const SHELL_SEGMENTS: usize = 96;
pub const SHELL_PROFILE: [[f64; 2]; 8] = [
    [0.042, 0.008],
    [0.049, 0.008],
    [0.052, 0.012],
    [0.052, 0.090],
    [0.050, 0.100],
    [0.044, 0.100],
    [0.042, 0.096],
    [0.042, 0.020],
];
pub const REQUIRED_NATIVE_OBSERVATIONS: &[&str] = &[
    "fresh isolated scene, Blender 4.5.14 and pinned SDK",
    "composition plan/apply/verify before semantic augmentation",
    "current target ownership and semantic generation before every mutation",
    "all scalar readbacks plus independent pointer, topology and node-link readback",
    "frame evaluation and camera-cut probe before 1200-frame production",
    "source CAS receipt and new-session save/open/edit/reopen readback",
    "explicit shared native reference revision before fair A/B comparison",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Coating {
    Ocean,
    Clay,
    Chalk,
    Graphite,
}

impl Coating {
    pub fn srgb(self) -> [f64; 3] {
        match self {
            Self::Ocean => [0.06, 0.32, 0.42],
            Self::Clay => [0.48, 0.16, 0.07],
            Self::Chalk => [0.72, 0.70, 0.63],
            Self::Graphite => [0.08, 0.095, 0.11],
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CameraCurve {
    NativeDefault,
    Linear,
    BezierAutoClamped,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductScene {
    pub version: u32,
    pub namespace: String,
    pub coating: Coating,
    pub roughness: f64,
    pub metallic: f64,
    pub samples: u32,
    pub annotation: String,
    pub camera_curve: CameraCurve,
}

impl ProductScene {
    pub fn vessel(namespace: impl Into<String>, camera_curve: CameraCurve) -> Self {
        Self {
            version: 1,
            namespace: namespace.into(),
            coating: Coating::Ocean,
            roughness: 0.28,
            metallic: 0.65,
            samples: 64,
            annotation: "EDITABLE\nSURFACE".into(),
            camera_curve,
        }
    }

    pub fn validate(&self) -> Result<(), ProjectionError> {
        if self.version != 1 {
            return Err(unsupported(
                "version",
                "Only product scene version 1 is admitted",
            ));
        }
        if self.namespace.is_empty()
            || self.namespace.len() > 32
            || !self
                .namespace
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_')
        {
            return Err(invalid(
                "namespace",
                "Expected 1..32 ASCII letters, digits or underscore",
            ));
        }
        for (path, value) in [("roughness", self.roughness), ("metallic", self.metallic)] {
            if !value.is_finite() || !(0.0..=1.0).contains(&value) {
                return Err(invalid(path, "Expected a finite scalar from 0 through 1"));
            }
        }
        if !(16..=256).contains(&self.samples) {
            return Err(invalid("samples", "Expected 16..256 samples"));
        }
        let lines: Vec<_> = self.annotation.split('\n').collect();
        if lines.len() > 2
            || lines.iter().any(|line| line.is_empty() || line.len() > 32)
            || !self
                .annotation
                .bytes()
                .all(|b| b == b'\n' || (0x20..=0x7e).contains(&b))
        {
            return Err(unsupported(
                "annotation",
                "Builtin-font profile admits one or two nonempty printable ASCII lines, at most 32 characters each",
            ));
        }
        if self.camera_curve == CameraCurve::NativeDefault {
            return Err(unsupported(
                "camera_curve",
                "Unspecified native interpolation cannot be replaced by LINEAR or an assumed BEZIER default",
            ));
        }
        Ok(())
    }

    pub fn edited(&self, edit: &ProductEdit) -> Result<Self, ProjectionError> {
        self.validate()?;
        let mut next = self.clone();
        match edit {
            ProductEdit::Coating(value) => next.coating = *value,
            ProductEdit::Roughness(value) => next.roughness = *value,
            ProductEdit::Metallic(value) => next.metallic = *value,
            ProductEdit::Annotation(value) => next.annotation = value.clone(),
        }
        next.validate()?;
        Ok(next)
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProductEdit {
    Coating(Coating),
    Roughness(f64),
    Metallic(f64),
    Annotation(String),
}

impl ProductEdit {
    pub fn parameter_path(&self) -> &'static str {
        match self {
            Self::Coating(_) => "product.coating.variant",
            Self::Roughness(_) => "product.coating.roughness",
            Self::Metallic(_) => "product.coating.metallic",
            Self::Annotation(_) => "product.annotation.body",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProjectionError {
    Invalid {
        path: &'static str,
        reason: &'static str,
    },
    Unsupported {
        path: &'static str,
        reason: &'static str,
    },
    MissingObservation(Binding),
    InvalidObservation(Binding),
}
impl std::fmt::Display for ProjectionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for ProjectionError {}
fn invalid(path: &'static str, reason: &'static str) -> ProjectionError {
    ProjectionError::Invalid { path, reason }
}
fn unsupported(path: &'static str, reason: &'static str) -> ProjectionError {
    ProjectionError::Unsupported { path, reason }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Binding {
    Scene,
    RenderSettings,
    ImageSettings,
    ViewSettings,
    UnitSettings,
    Cycles,
    World,
    WorldBackgroundStrength,
    Entity(&'static str),
    CameraData(&'static str),
    CameraDof(&'static str),
    BodyBevel,
    CylinderCap {
        entity: &'static str,
        index: u8,
    },
    CameraKey {
        entity: &'static str,
        axis: u8,
        frame: u32,
    },
    Marker(u8),
    FontData,
    FontObject,
    FontInk,
    FontInkTree,
    FontInkPrincipled,
    FontInkEmission,
    FontInkEmissionColor,
    FontInkEmissionStrength,
    FontInkEmissionOutput,
    FontInkSurfaceInput,
    FontVisibilityKey(u32),
    CoatingMaterial,
    CoatingBaseColor,
    CoatingRoughness,
    CoatingMetallic,
}

/// Supplied by SDK observations. This data type does not authenticate authority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObservedReference {
    pub reference: String,
    pub native_name: Option<String>,
}
#[derive(Debug, Clone, PartialEq)]
enum Input {
    Literal(Value),
    Reference(Binding),
    NativeName(Binding),
}

/// Constructors are private: profile inputs cannot select arbitrary commands,
/// RNA properties or executable payloads.
#[derive(Debug, Clone, PartialEq)]
pub struct SemanticStep {
    operation: &'static str,
    arguments: Vec<(&'static str, Input)>,
    record_as: Option<Binding>,
}
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct NativeCall {
    pub operation: &'static str,
    pub input: Value,
}

impl SemanticStep {
    pub fn operation(&self) -> &'static str {
        self.operation
    }
    pub fn result_binding(&self) -> Option<&Binding> {
        self.record_as.as_ref()
    }
    pub fn required_bindings(&self) -> Vec<&Binding> {
        self.arguments
            .iter()
            .filter_map(|(_, value)| match value {
                Input::Reference(binding) | Input::NativeName(binding) => Some(binding),
                Input::Literal(_) => None,
            })
            .collect()
    }

    /// Bind immediately before a single dispatch, never prebind a sequence.
    /// Mutation rotates semantic generation; the resolver must observe anew.
    pub fn bind(
        &self,
        mut resolve: impl FnMut(&Binding) -> Option<ObservedReference>,
    ) -> Result<NativeCall, ProjectionError> {
        let mut values = serde_json::Map::new();
        for (key, input) in &self.arguments {
            let value = match input {
                Input::Literal(value) => value.clone(),
                Input::Reference(binding) | Input::NativeName(binding) => {
                    let observed = resolve(binding)
                        .ok_or_else(|| ProjectionError::MissingObservation(binding.clone()))?;
                    if !observed.reference.starts_with("blender-rna/v1/")
                        || !(16..=1536).contains(&observed.reference.chars().count())
                    {
                        return Err(ProjectionError::InvalidObservation(binding.clone()));
                    }
                    match input {
                        Input::Reference(_) => json!(observed.reference),
                        Input::NativeName(_) => {
                            let name = observed
                                .native_name
                                .filter(|name| {
                                    !name.is_empty()
                                        && name.chars().count() <= 128
                                        && !name.contains('\0')
                                })
                                .ok_or_else(|| {
                                    ProjectionError::InvalidObservation(binding.clone())
                                })?;
                            json!(name)
                        }
                        Input::Literal(_) => unreachable!(),
                    }
                }
            };
            values.insert((*key).into(), value);
        }
        Ok(NativeCall {
            operation: self.operation,
            input: Value::Object(values),
        })
    }
}

fn lit(value: impl Into<Value>) -> Input {
    Input::Literal(value.into())
}
fn reference(binding: Binding) -> Input {
    Input::Reference(binding)
}
fn step(operation: &'static str, arguments: Vec<(&'static str, Input)>) -> SemanticStep {
    SemanticStep {
        operation,
        arguments,
        record_as: None,
    }
}
fn create(
    operation: &'static str,
    arguments: Vec<(&'static str, Input)>,
    record_as: Binding,
) -> SemanticStep {
    SemanticStep {
        operation,
        arguments,
        record_as: Some(record_as),
    }
}
fn set(binding: Binding, property: &'static str, value: Value) -> SemanticStep {
    step(
        "driver.blender.semantic.property.set",
        vec![
            ("ref", reference(binding)),
            ("property", lit(property)),
            ("value", lit(value)),
        ],
    )
}
fn relate(binding: Binding, property: &'static str, target: Binding) -> SemanticStep {
    step(
        "driver.blender.semantic.relation.set",
        vec![
            ("ref", reference(binding)),
            ("property", lit(property)),
            ("target_ref", reference(target)),
        ],
    )
}
fn insert_key(binding: Binding, property: &'static str, frame: u32) -> SemanticStep {
    step(
        "driver.blender.animation.keyframe.insert",
        vec![
            ("ref", reference(binding)),
            ("property", lit(property)),
            ("frame", lit(frame)),
            ("index", lit(-1)),
        ],
    )
}

#[derive(Debug, Clone, PartialEq)]
pub struct PropertyExpectation {
    pub target: Binding,
    pub property: &'static str,
    pub expected: Value,
    pub absolute_tolerance: f64,
}
impl PropertyExpectation {
    pub fn getter(&self) -> SemanticStep {
        step(
            "driver.blender.semantic.property.get",
            vec![
                ("ref", reference(self.target.clone())),
                ("property", lit(self.property)),
            ],
        )
    }
    pub fn matches_reply(&self, request: &NativeCall, reply: &Value) -> bool {
        request.operation == "driver.blender.semantic.property.get"
            && request.input.get("property").and_then(Value::as_str) == Some(self.property)
            && reply.get("property").and_then(Value::as_str) == Some(self.property)
            && request.input.get("ref").and_then(Value::as_str).is_some()
            && reply.get("ref") == request.input.get("ref")
            && self.absolute_tolerance.is_finite()
            && self.absolute_tolerance >= 0.0
            && (self.property != "co"
                || match (&self.expected, reply.get("value")) {
                    (Value::Array(expected), Some(Value::Array(actual))) => {
                        expected.first().and_then(Value::as_f64).is_some()
                            && expected.first().and_then(Value::as_f64)
                                == actual.first().and_then(Value::as_f64)
                    }
                    _ => false,
                })
            && reply
                .get("value")
                .is_some_and(|value| equal_value(&self.expected, value, self.absolute_tolerance))
    }
}
fn equal_value(expected: &Value, actual: &Value, tolerance: f64) -> bool {
    match (expected, actual) {
        (Value::Number(a), Value::Number(b)) => match (a.as_f64(), b.as_f64()) {
            (Some(a), Some(b)) => a.is_finite() && b.is_finite() && (a - b).abs() <= tolerance,
            _ => false,
        },
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| equal_value(a, b, tolerance))
        }
        _ => expected == actual,
    }
}
fn expect(target: Binding, property: &'static str, expected: Value) -> PropertyExpectation {
    let absolute_tolerance = if matches!(
        property,
        "frame_start" | "frame_end" | "fps" | "fps_base" | "frame" | "segments"
    ) {
        0.0
    } else {
        1e-6
    };
    PropertyExpectation {
        target,
        property,
        expected,
        absolute_tolerance,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Shot {
    pub camera: &'static str,
    pub start_frame: u32,
    pub end_frame_exclusive: u32,
}
pub const SHOTS: [Shot; 2] = [
    Shot {
        camera: "camera_form",
        start_frame: 0,
        end_frame_exclusive: CUT_FRAME,
    },
    Shot {
        camera: "camera_surface",
        start_frame: CUT_FRAME,
        end_frame_exclusive: FRAME_COUNT,
    },
];
pub fn native_frame(canonical_frame: u32) -> Result<u32, ProjectionError> {
    if canonical_frame >= FRAME_COUNT {
        return Err(invalid(
            "frame",
            "Canonical frame outside half-open 0..1200",
        ));
    }
    Ok(canonical_frame + 1)
}

pub fn vessel_mesh() -> (Vec<[f64; 3]>, Vec<Vec<u32>>) {
    let width = SHELL_PROFILE.len();
    let mut vertices = Vec::with_capacity(SHELL_SEGMENTS * width);
    let mut faces = Vec::with_capacity(SHELL_SEGMENTS * width);
    for i in 0..SHELL_SEGMENTS {
        let angle = i as f64 * TAU / SHELL_SEGMENTS as f64;
        for [radius, height] in SHELL_PROFILE {
            vertices.push([radius * angle.cos(), radius * angle.sin(), height]);
        }
        for j in 0..width {
            faces.push(vec![
                (i * width + j) as u32,
                (((i + 1) % SHELL_SEGMENTS) * width + j) as u32,
                (((i + 1) % SHELL_SEGMENTS) * width + (j + 1) % width) as u32,
                (i * width + (j + 1) % width) as u32,
            ]);
        }
    }
    (vertices, faces)
}
fn srgb_linear(rgb: [f64; 3]) -> [f64; 4] {
    let convert = |c: f64| {
        if c <= 0.04045 {
            c / 12.92
        } else {
            ((c + 0.055) / 1.055).powf(2.4)
        }
    };
    [convert(rgb[0]), convert(rgb[1]), convert(rgb[2]), 1.0]
}
fn material(id: &str, rgb: [f64; 3], roughness: f64, metallic: f64) -> Value {
    json!({"id":id, "base_color":srgb_linear(rgb), "roughness":roughness, "metallic":metallic,
        "emission_color":[0.0,0.0,0.0,1.0], "emission_strength":0.0, "opacity":1.0,
        "base_color_texture":null, "roughness_texture":null, "metallic_texture":null,
        "normal_texture":null, "emission_texture":null, "opacity_texture":null})
}
fn entity(
    id: &str,
    name: &str,
    shape: Value,
    translation: [f64; 3],
    materials: &[&str],
    smooth: bool,
    modifiers: Value,
) -> Value {
    json!({"id":id, "name":name, "shape":shape,
        "transform":{"translation":translation,"rotation":[0.0,0.0,0.0],"scale":[1.0,1.0,1.0]},
        "materials":materials,"modifiers":modifiers,"shade_smooth":smooth,"attributes":[]})
}

#[derive(Debug, Clone, PartialEq)]
pub struct ProductProjection {
    pub composition_spec: Value,
    pub before_create: Vec<SemanticStep>,
    pub after_create: Vec<SemanticStep>,
    pub readback: Vec<PropertyExpectation>,
    pub shots: [Shot; 2],
}
impl ProductProjection {
    pub fn composition_plan_input(&self) -> Value {
        json!({"intent":{"kind":"create","spec":self.composition_spec}})
    }
}

/// A source projection. The existing pinned SDK decoder and validator must
/// admit its data before native plan/apply/verify and semantic augmentation.
pub fn project_product_scene(scene: &ProductScene) -> Result<ProductProjection, ProjectionError> {
    scene.validate()?;
    let (vertices, faces) = vessel_mesh();
    let mut entities = vec![
        entity(
            "vessel_body",
            "Vessel body",
            json!({"kind":"mesh","vertices":vertices,"faces":faces,"uv":null}),
            [0.0; 3],
            &["coating"],
            true,
            json!([{"kind":"bevel","width":0.0006,"segments":3}]),
        ),
        entity(
            "vessel_floor",
            "Vessel interior floor",
            json!({"kind":"cylinder","radius":0.042,"depth":0.012,"segments":96}),
            [0.0, 0.0, 0.014],
            &["coating"],
            true,
            json!([]),
        ),
        entity(
            "vessel_base",
            "Vessel base",
            json!({"kind":"cylinder","radius":0.056,"depth":0.008,"segments":96}),
            [0.0, 0.0, 0.004],
            &["dark"],
            true,
            json!([]),
        ),
        entity(
            "studio_floor",
            "Studio floor",
            json!({"kind":"mesh",
            "vertices":[[-1.0,-1.0,0.0],[1.0,-1.0,0.0],[1.0,1.0,0.0],[-1.0,1.0,0.0]],
            "faces":[[0,1,2,3]],"uv":null}),
            [0.0, 0.0, -0.0005],
            &["dark"],
            false,
            json!([]),
        ),
    ];
    let mut relations = Vec::new();
    for (id, name, target, position, focus, watts, size) in [
        (
            "light_key",
            "Key",
            "target_key",
            [0.22, -0.24, 0.32],
            [0.0, 0.0, 0.05],
            80.0,
            0.35,
        ),
        (
            "light_fill",
            "Fill",
            "target_fill",
            [-0.23, -0.15, 0.20],
            [0.0, 0.0, 0.05],
            35.0,
            0.50,
        ),
        (
            "light_rim",
            "Rim",
            "target_rim",
            [0.05, 0.22, 0.30],
            [0.0, 0.0, 0.06],
            100.0,
            0.22,
        ),
    ] {
        entities.push(entity(
            id,
            name,
            json!({"kind":"area_light","energy_watts":watts,"size":size,"color":[1.0,1.0,1.0]}),
            position,
            &[],
            false,
            json!([]),
        ));
        entities.push(entity(
            target,
            target,
            json!({"kind":"empty"}),
            focus,
            &[],
            false,
            json!([]),
        ));
        relations.push(json!({"kind":"look_at","subject":id,"target":target}));
    }
    for (id, name, target, position, focus, lens) in [
        (
            "camera_form",
            "Shot A - form",
            "focus_form",
            [0.22, -0.30, 0.19],
            [0.0, 0.0, 0.052],
            50.0,
        ),
        (
            "camera_surface",
            "Shot B - surface",
            "focus_surface",
            [0.14, -0.18, 0.145],
            [0.0, -0.012, 0.075],
            70.0,
        ),
    ] {
        entities.push(entity(
            id,
            name,
            json!({"kind":"camera","lens_mm":lens,"clip_start":0.005,"clip_end":20.0}),
            position,
            &[],
            false,
            json!([]),
        ));
        entities.push(entity(
            target,
            target,
            json!({"kind":"empty"}),
            focus,
            &[],
            false,
            json!([]),
        ));
        relations.push(json!({"kind":"look_at","subject":id,"target":target}));
    }
    let spec = json!({"version":1,"collection":scene.namespace,"meters_per_unit":1.0,"textures":[],
        "materials":[material("coating",scene.coating.srgb(),scene.roughness,scene.metallic),
            material("dark",[0.035,0.045,0.055],0.55,0.15)],
        "entities":entities,"relations":relations,"animation":camera_animation_spec()});
    let mut after_create = surface_steps();
    after_create.extend(camera_steps(scene.camera_curve));
    after_create.extend(annotation_steps(scene));
    after_create.push(set(Binding::Scene, "frame_current", json!(1)));
    Ok(ProductProjection {
        composition_spec: spec,
        before_create: scene_settings(scene),
        after_create,
        readback: property_expectations(scene),
        shots: SHOTS,
    })
}

fn scene_settings(scene: &ProductScene) -> Vec<SemanticStep> {
    let mut steps = vec![step(
        "driver.blender.render.settings",
        vec![
            ("width", lit(1920)),
            ("height", lit(1080)),
            ("samples", lit(scene.samples)),
            ("engine", lit("CYCLES")),
            ("device", lit("CPU")),
            ("backend", lit("AUTO")),
        ],
    )];
    for (binding, property, value) in [
        (Binding::Scene, "frame_start", json!(1)),
        (Binding::Scene, "frame_end", json!(FRAME_COUNT)),
        (Binding::RenderSettings, "fps", json!(FPS)),
        (Binding::RenderSettings, "fps_base", json!(1.0)),
        (Binding::RenderSettings, "resolution_percentage", json!(100)),
        (Binding::RenderSettings, "threads_mode", json!("FIXED")),
        (Binding::RenderSettings, "threads", json!(2)),
        (Binding::RenderSettings, "film_transparent", json!(false)),
        (Binding::RenderSettings, "use_file_extension", json!(true)),
        (Binding::ImageSettings, "file_format", json!("PNG")),
        (Binding::ImageSettings, "color_mode", json!("RGBA")),
        (Binding::ImageSettings, "color_depth", json!("8")),
        (Binding::UnitSettings, "system", json!("METRIC")),
        (Binding::UnitSettings, "scale_length", json!(1.0)),
        (Binding::Cycles, "seed", json!(73)),
        (Binding::Cycles, "use_animated_seed", json!(false)),
        (Binding::Cycles, "use_denoising", json!(true)),
        (Binding::ViewSettings, "view_transform", json!("AgX")),
        (Binding::ViewSettings, "exposure", json!(0.0)),
        (Binding::ViewSettings, "gamma", json!(1.0)),
    ] {
        steps.push(set(binding, property, value));
    }
    steps.push(create(
        "driver.blender.semantic.datablock.create",
        vec![
            ("root", lit("worlds")),
            ("name", lit(format!("{}_world", scene.namespace))),
        ],
        Binding::World,
    ));
    steps.push(set(Binding::World, "use_nodes", json!(true)));
    steps.push(relate(Binding::Scene, "world", Binding::World));
    steps.push(set(
        Binding::WorldBackgroundStrength,
        "default_value",
        json!(0.04),
    ));
    steps
}

fn surface_steps() -> Vec<SemanticStep> {
    let mut steps = vec![
        set(Binding::BodyBevel, "limit_method", json!("ANGLE")),
        set(
            Binding::BodyBevel,
            "angle_limit",
            json!(25.0_f64.to_radians()),
        ),
    ];
    for entity in ["vessel_floor", "vessel_base"] {
        for index in 0..=1 {
            steps.push(set(
                Binding::CylinderCap { entity, index },
                "use_smooth",
                json!(false),
            ));
        }
    }
    steps
}

const CAMERA_POSES: [(&str, u32, [f64; 3]); 5] = [
    ("camera_form", 1, [0.22, -0.30, 0.19]),
    ("camera_form", 300, [0.28, -0.24, 0.18]),
    ("camera_form", 600, [0.30, -0.17, 0.17]),
    ("camera_surface", 601, [0.14, -0.18, 0.145]),
    ("camera_surface", 1200, [0.12, -0.15, 0.130]),
];
fn interpolation(curve: CameraCurve) -> &'static str {
    match curve {
        CameraCurve::Linear => "LINEAR",
        CameraCurve::BezierAutoClamped => "BEZIER",
        CameraCurve::NativeDefault => unreachable!("validated before projection"),
    }
}
fn camera_animation_spec() -> Value {
    let channels: Vec<Value> = ["camera_form", "camera_surface"]
        .into_iter()
        .map(|entity| {
            let keys: Vec<Value> = CAMERA_POSES
                .iter()
                .copied()
                .filter(|(camera, _, _)| *camera == entity)
                .map(|(_, frame, location)| json!({"frame": frame, "value": location}))
                .collect();
            json!({
                "entity": entity,
                "bone": null,
                "property": "translation",
                "keys": keys
            })
        })
        .collect();
    json!({
        "id": "camera_motion",
        "rate": {"num": FPS, "den": 1},
        "channels": channels,
        "nla_tracks": []
    })
}

fn camera_steps(curve: CameraCurve) -> Vec<SemanticStep> {
    let mut steps = Vec::new();
    for (camera, focus, fstop) in [
        ("camera_form", "focus_form", 7.1),
        ("camera_surface", "focus_surface", 5.6),
    ] {
        steps.push(set(Binding::CameraDof(camera), "use_dof", json!(true)));
        steps.push(relate(
            Binding::CameraDof(camera),
            "focus_object",
            Binding::Entity(focus),
        ));
        steps.push(set(
            Binding::CameraDof(camera),
            "aperture_fstop",
            json!(fstop),
        ));
    }
    for (entity, frame, _) in CAMERA_POSES {
        for axis in 0..3 {
            let key = Binding::CameraKey {
                entity,
                axis,
                frame,
            };
            steps.push(set(
                key.clone(),
                "interpolation",
                json!(interpolation(curve)),
            ));
            steps.push(set(key.clone(), "handle_left_type", json!("AUTO_CLAMPED")));
            steps.push(set(key, "handle_right_type", json!("AUTO_CLAMPED")));
        }
    }
    for (index, name, frame, camera) in [
        (0, "Form", 1, "camera_form"),
        (1, "Surface", CUT_FRAME + 1, "camera_surface"),
    ] {
        steps.push(create(
            "driver.blender.scene.marker.add",
            vec![
                ("scene_ref", reference(Binding::Scene)),
                ("name", lit(name)),
                ("frame", lit(frame)),
            ],
            Binding::Marker(index),
        ));
        steps.push(relate(
            Binding::Marker(index),
            "camera",
            Binding::Entity(camera),
        ));
    }
    steps.push(relate(
        Binding::Scene,
        "camera",
        Binding::Entity("camera_form"),
    ));
    steps
}

fn annotation_steps(scene: &ProductScene) -> Vec<SemanticStep> {
    let name = format!("{}_annotation", scene.namespace);
    let mut steps = vec![
        create(
            "driver.blender.semantic.datablock.create",
            vec![
                ("root", lit("curves")),
                ("kind", lit("FONT")),
                ("name", lit(name.clone())),
            ],
            Binding::FontData,
        ),
        set(Binding::FontData, "body", json!(scene.annotation)),
        set(Binding::FontData, "size", json!(0.006)),
        set(Binding::FontData, "align_x", json!("LEFT")),
        create(
            "driver.blender.semantic.object.create",
            vec![
                ("name", lit(name)),
                ("data_ref", reference(Binding::FontData)),
            ],
            Binding::FontObject,
        ),
        relate(
            Binding::FontObject,
            "parent",
            Binding::Entity("camera_surface"),
        ),
        set(
            Binding::FontObject,
            "location",
            json!([0.018, -0.018, -0.30]),
        ),
        create(
            "driver.blender.semantic.datablock.create",
            vec![
                ("root", lit("materials")),
                ("name", lit(format!("{}_ink", scene.namespace))),
            ],
            Binding::FontInk,
        ),
        set(Binding::FontInk, "use_nodes", json!(true)),
        step(
            "driver.blender.node.remove",
            vec![("ref", reference(Binding::FontInkPrincipled))],
        ),
        create(
            "driver.blender.node.add",
            vec![
                ("tree_ref", reference(Binding::FontInkTree)),
                ("type", lit("ShaderNodeEmission")),
                ("name", lit("AnnotationEmission")),
            ],
            Binding::FontInkEmission,
        ),
        set(
            Binding::FontInkEmissionColor,
            "default_value",
            json!([0.75, 0.86, 0.90, 1.0]),
        ),
        set(
            Binding::FontInkEmissionStrength,
            "default_value",
            json!(1.0),
        ),
        step(
            "driver.blender.node.link",
            vec![
                ("tree_ref", reference(Binding::FontInkTree)),
                ("from_socket_ref", reference(Binding::FontInkEmissionOutput)),
                ("to_socket_ref", reference(Binding::FontInkSurfaceInput)),
            ],
        ),
        step(
            "driver.blender.material.assign",
            vec![
                ("object", Input::NativeName(Binding::FontObject)),
                ("material", Input::NativeName(Binding::FontInk)),
            ],
        ),
    ];
    for (frame, hidden) in [(1, true), (600, true), (601, false), (1200, false)] {
        steps.push(set(Binding::FontObject, "hide_render", json!(hidden)));
        steps.push(insert_key(Binding::FontObject, "hide_render", frame));
    }
    for frame in [1, 600, 601, 1200] {
        steps.push(set(
            Binding::FontVisibilityKey(frame),
            "interpolation",
            json!("CONSTANT"),
        ));
    }
    steps
}

fn property_expectations(scene: &ProductScene) -> Vec<PropertyExpectation> {
    let mut checks = vec![
        expect(Binding::Scene, "frame_start", json!(1)),
        expect(Binding::Scene, "frame_end", json!(1200)),
        expect(Binding::RenderSettings, "fps", json!(30)),
        expect(Binding::RenderSettings, "fps_base", json!(1.0)),
        expect(Binding::BodyBevel, "width", json!(0.0006)),
        expect(Binding::BodyBevel, "segments", json!(3)),
        expect(Binding::BodyBevel, "limit_method", json!("ANGLE")),
        expect(
            Binding::BodyBevel,
            "angle_limit",
            json!(25.0_f64.to_radians()),
        ),
        expect(Binding::FontData, "body", json!(scene.annotation)),
        expect(Binding::FontData, "size", json!(0.006)),
        expect(Binding::FontData, "align_x", json!("LEFT")),
        expect(
            Binding::FontObject,
            "location",
            json!([0.018, -0.018, -0.30]),
        ),
        expect(
            Binding::FontInkEmissionColor,
            "default_value",
            json!([0.75, 0.86, 0.90, 1.0]),
        ),
        expect(
            Binding::FontInkEmissionStrength,
            "default_value",
            json!(1.0),
        ),
        expect(
            Binding::WorldBackgroundStrength,
            "default_value",
            json!(0.04),
        ),
        expect(
            Binding::CoatingBaseColor,
            "default_value",
            json!(srgb_linear(scene.coating.srgb())),
        ),
        expect(
            Binding::CoatingRoughness,
            "default_value",
            json!(scene.roughness),
        ),
        expect(
            Binding::CoatingMetallic,
            "default_value",
            json!(scene.metallic),
        ),
    ];
    for entity in ["vessel_floor", "vessel_base"] {
        for index in 0..=1 {
            checks.push(expect(
                Binding::CylinderCap { entity, index },
                "use_smooth",
                json!(false),
            ));
        }
    }
    for (camera, lens, fstop) in [("camera_form", 50.0, 7.1), ("camera_surface", 70.0, 5.6)] {
        checks.push(expect(Binding::CameraData(camera), "lens", json!(lens)));
        checks.push(expect(
            Binding::CameraData(camera),
            "clip_start",
            json!(0.005),
        ));
        checks.push(expect(Binding::CameraData(camera), "clip_end", json!(20.0)));
        checks.push(expect(Binding::CameraDof(camera), "use_dof", json!(true)));
        checks.push(expect(
            Binding::CameraDof(camera),
            "aperture_fstop",
            json!(fstop),
        ));
    }
    for (entity, frame, position) in CAMERA_POSES {
        for axis in 0..3 {
            let point = Binding::CameraKey {
                entity,
                axis,
                frame,
            };
            checks.push(expect(
                point.clone(),
                "co",
                json!([frame as f64, position[axis as usize]]),
            ));
            checks.push(expect(
                point.clone(),
                "interpolation",
                json!(interpolation(scene.camera_curve)),
            ));
            checks.push(expect(
                point.clone(),
                "handle_left_type",
                json!("AUTO_CLAMPED"),
            ));
            checks.push(expect(point, "handle_right_type", json!("AUTO_CLAMPED")));
        }
    }
    for (frame, value) in [(1, 1.0), (600, 1.0), (601, 0.0), (1200, 0.0)] {
        checks.push(expect(
            Binding::FontVisibilityKey(frame),
            "co",
            json!([frame as f64, value]),
        ));
        checks.push(expect(
            Binding::FontVisibilityKey(frame),
            "interpolation",
            json!("CONSTANT"),
        ));
    }
    for (index, name, frame) in [(0, "Form", 1), (1, "Surface", 601)] {
        checks.push(expect(Binding::Marker(index), "name", json!(name)));
        checks.push(expect(Binding::Marker(index), "frame", json!(frame)));
    }
    checks
}

pub fn project_product_edit(
    scene: &ProductScene,
    edit: &ProductEdit,
) -> Result<(ProductScene, Vec<SemanticStep>), ProjectionError> {
    let next_source = scene.edited(edit)?;
    let steps = match edit {
        ProductEdit::Coating(value) => vec![
            set(
                Binding::CoatingBaseColor,
                "default_value",
                json!(srgb_linear(value.srgb())),
            ),
            set(
                Binding::CoatingMaterial,
                "diffuse_color",
                json!(srgb_linear(value.srgb())),
            ),
        ],
        ProductEdit::Roughness(value) => vec![
            set(Binding::CoatingRoughness, "default_value", json!(value)),
            set(Binding::CoatingMaterial, "roughness", json!(value)),
        ],
        ProductEdit::Metallic(value) => vec![
            set(Binding::CoatingMetallic, "default_value", json!(value)),
            set(Binding::CoatingMaterial, "metallic", json!(value)),
        ],
        ProductEdit::Annotation(value) => vec![set(Binding::FontData, "body", json!(value))],
    };
    Ok((next_source, steps))
}

#[cfg(test)]
#[path = "blender_product_scene_tests.rs"]
mod tests;
