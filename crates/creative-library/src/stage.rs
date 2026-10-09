use crate::*;
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Vec3 {
    pub x: f64,
    pub y: f64,
    pub z: f64,
}
impl Vec3 {
    pub fn new(x: f64, y: f64, z: f64) -> Self {
        Self { x, y, z }
    }
    pub fn finite(self) -> bool {
        [self.x, self.y, self.z]
            .iter()
            .all(|value| value.is_finite() && value.abs() <= 1000.0)
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum GenericDevice {
    Phone,
    Tablet,
    Laptop,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StageDevice {
    pub id: Uuid,
    pub name: String,
    pub model: GenericDevice,
    pub position: Vec3,
    pub rotation_deg: Vec3,
    pub scale: f64,
    pub body_color: String,
    pub screen_color: String,
    pub roughness: f64,
    pub metallic: f64,
    pub screen_asset_id: Option<Uuid>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CameraPose {
    pub frame: u32,
    pub position: Vec3,
    pub target: Vec3,
    pub focal_length_mm: f64,
    pub focus_distance: f64,
    pub f_stop: f64,
    pub curve: native::Curve,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StageLight {
    pub id: Uuid,
    pub name: String,
    pub position: Vec3,
    pub target: Vec3,
    pub power_watts: f64,
    pub size_m: f64,
    pub color: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StagePlan {
    pub version: u32,
    pub instance_id: Uuid,
    pub recipe: RecipeId,
    pub output: native::Canvas,
    pub devices: Vec<StageDevice>,
    pub cameras: Vec<CameraPose>,
    pub lights: Vec<StageLight>,
    pub floor_color: String,
    pub world_color: String,
    pub assets: Vec<native::Asset>,
    pub seed: u32,
    pub samples: u16,
    pub color_management: String,
    pub units: String,
    pub source_classification: String,
}
impl StagePlan {
    pub fn validate(&self) -> Result<()> {
        check(
            self.version == 1
                && (1..=12).contains(&self.devices.len())
                && (2..=64).contains(&self.cameras.len())
                && (1..=8).contains(&self.lights.len()),
            "Native product stage exceeds its geometry/camera/light budget",
        )?;
        check(
            self.output.frames >= 2
                && self.output.frames <= 3600
                && self.output.width <= 4096
                && self.output.height <= 4096
                && self.output.width >= 320
                && self.output.height >= 240,
            "Native stage output is out of bounds",
        )?;
        check(
            self.units == "metres_degrees_millimetres"
                && self.color_management == "standard_scene_linear_to_srgb_rgba",
            "Stage units and color interpretation must be explicit",
        )?;
        check(
            (8..=128).contains(&self.samples) && self.assets.len() <= 16,
            "Stage sampling or source asset budget exceeded",
        )?;
        rgb(&self.floor_color)?;
        rgb(&self.world_color)?;
        let mut ids = std::collections::BTreeSet::new();
        for device in &self.devices {
            check(
                ids.insert(device.id) && device.position.finite() && device.rotation_deg.finite(),
                "Duplicate or non-finite stage device transform",
            )?;
            text(&device.name, 120, "Stage device name is required")?;
            check(
                device.scale.is_finite()
                    && (0.1..=10.0).contains(&device.scale)
                    && device.roughness.is_finite()
                    && (0.0..=1.0).contains(&device.roughness)
                    && device.metallic.is_finite()
                    && (0.0..=1.0).contains(&device.metallic),
                "Stage material and physical scale values are outside their bounds",
            )?;
            rgb(&device.body_color)?;
            rgb(&device.screen_color)?;
            if let Some(id) = device.screen_asset_id {
                check(
                    self.assets.iter().any(|asset| {
                        asset.id == id
                            && matches!(
                                asset.kind,
                                native::AssetKind::Png | native::AssetKind::Jpeg
                            )
                            && asset.rights.use_authorized
                    }),
                    "Stage screen needs a digest-bound, explicitly authorized raster source",
                )?;
            }
        }
        for camera in &self.cameras {
            check(
                camera.position.finite()
                    && camera.target.finite()
                    && camera.position != camera.target
                    && camera.frame < self.output.frames,
                "Stage camera is invalid or outside the output interval",
            )?;
            check(
                camera.focal_length_mm.is_finite()
                    && (18.0..=200.0).contains(&camera.focal_length_mm)
                    && camera.focus_distance.is_finite()
                    && (0.1..=100.0).contains(&camera.focus_distance)
                    && camera.f_stop.is_finite()
                    && (1.2..=32.0).contains(&camera.f_stop),
                "Camera optics exceed the bounded production profile",
            )?;
        }
        check(
            self.cameras[0].frame == 0
                && self
                    .cameras
                    .last()
                    .is_some_and(|camera| camera.frame == self.output.frames - 1)
                && self
                    .cameras
                    .windows(2)
                    .all(|pair| pair[0].frame < pair[1].frame),
            "Camera path must cover the complete scene with strictly ordered keys",
        )?;
        for light in &self.lights {
            check(
                ids.insert(light.id)
                    && light.position.finite()
                    && light.target.finite()
                    && light.position != light.target,
                "Invalid stage light identity or direction",
            )?;
            text(&light.name, 120, "Stage light name is required")?;
            check(
                light.power_watts.is_finite()
                    && (1.0..=5000.0).contains(&light.power_watts)
                    && light.size_m.is_finite()
                    && (0.01..=20.0).contains(&light.size_m),
                "Light energy or size is outside its physical range",
            )?;
            rgb(&light.color)?;
        }
        Ok(())
    }
}
pub fn product_stage(request: &ComponentRequest, brand: &BrandProfile) -> Result<StagePlan> {
    request.validate()?;
    brand.validate()?;
    check(
        request.recipe.definition().backend == RecipeBackend::BlenderStage,
        "Component is not a product cinematography recipe",
    )?;
    let portrait = request.output.height > request.output.width;
    let end = request.output.frames - 1;
    let target = Vec3::new(0.0, 0.0, 1.05);
    let radius = if portrait { 6.5 } else { 5.5 };
    let pose = |frame, position, target| CameraPose {
        frame,
        position,
        target,
        focal_length_mm: 48.0,
        focus_distance: ((position.x - target.x).powi(2)
            + (position.y - target.y).powi(2)
            + (position.z - target.z).powi(2))
        .sqrt(),
        f_stop: 4.5,
        curve: native::Curve::EaseInOut,
    };
    let mut devices = vec![StageDevice {
        id: stable_id(request.instance_id, "device-0"),
        name: "Original generic device".into(),
        model: if request.recipe == RecipeId::DeviceStage {
            GenericDevice::Laptop
        } else {
            GenericDevice::Phone
        },
        position: Vec3::new(0.0, 0.0, 0.0),
        rotation_deg: Vec3::new(0.0, 0.0, -8.0),
        scale: 1.0,
        body_color: brand.color(ColorRole::Surface).into(),
        screen_color: brand.color(ColorRole::Accent).into(),
        roughness: 0.26,
        metallic: 0.45,
        screen_asset_id: request.primary_asset.as_ref().map(|asset| asset.id),
    }];
    let cameras = match request.recipe {
        RecipeId::DeviceStage => vec![
            pose(0, Vec3::new(-radius * 0.38, -radius, 2.3), target),
            pose(end, Vec3::new(radius * 0.22, -radius * 0.88, 2.2), target),
        ],
        RecipeId::ArcReveal => vec![
            pose(0, Vec3::new(-radius * 0.62, -radius * 0.80, 2.2), target),
            pose(end, Vec3::new(radius * 0.62, -radius * 0.80, 2.2), target),
        ],
        RecipeId::DollyFocus => vec![
            pose(0, Vec3::new(-0.4, -radius * 1.25, 2.5), target),
            pose(end, Vec3::new(0.25, -radius * 0.65, 1.8), target),
        ],
        RecipeId::DetailReturn => vec![
            pose(0, Vec3::new(1.5, -radius, 2.2), target),
            pose(
                end / 2,
                Vec3::new(0.85, -radius * 0.54, 2.0),
                Vec3::new(0.12, 0.0, 1.55),
            ),
            pose(end, Vec3::new(-1.2, -radius, 2.15), target),
        ],
        RecipeId::GroupReframe => {
            for (index, (x, y)) in [(-1.4, 0.45), (1.4, 0.6)].into_iter().enumerate() {
                let mut device = devices[0].clone();
                device.id = stable_id(request.instance_id, &format!("device-{}", index + 1));
                device.name = format!("Generic group subject {}", index + 2);
                device.position = Vec3::new(x, y, 0.0);
                device.rotation_deg.z = if index == 0 { -18.0 } else { 18.0 };
                device.scale = 0.82;
                device.screen_color = brand
                    .color(if index == 0 {
                        ColorRole::Secondary
                    } else {
                        ColorRole::Text
                    })
                    .into();
                devices.push(device);
            }
            vec![
                pose(
                    0,
                    Vec3::new(-2.1, -radius * 1.3, 2.9),
                    Vec3::new(-0.45, 0.0, 1.0),
                ),
                pose(
                    end,
                    Vec3::new(2.1, -radius * 1.3, 2.9),
                    Vec3::new(0.45, 0.0, 1.0),
                ),
            ]
        }
        _ => return Err(CraftError("Unsupported product camera recipe".into())),
    };
    let mut cameras = cameras;
    if !request.motion {
        let fixed = cameras[0].clone();
        cameras = vec![
            fixed.clone(),
            CameraPose {
                frame: end,
                ..fixed
            },
        ];
    }
    let lights = vec![
        StageLight {
            id: stable_id(request.instance_id, "key-light"),
            name: "Soft key".into(),
            position: Vec3::new(-3.0, -4.0, 6.0),
            target,
            power_watts: 750.0,
            size_m: 4.0,
            color: "#FFFFFF".into(),
        },
        StageLight {
            id: stable_id(request.instance_id, "fill-light"),
            name: "Controlled fill".into(),
            position: Vec3::new(4.0, -1.0, 3.3),
            target,
            power_watts: 400.0,
            size_m: 3.0,
            color: brand.color(ColorRole::Accent).into(),
        },
        StageLight {
            id: stable_id(request.instance_id, "rim-light"),
            name: "Separation rim".into(),
            position: Vec3::new(0.0, 3.5, 4.8),
            target,
            power_watts: 900.0,
            size_m: 2.5,
            color: "#FFFFFF".into(),
        },
    ];
    let plan = StagePlan {
        version: 1,
        instance_id: request.instance_id,
        recipe: request.recipe,
        output: request.output.clone(),
        devices,
        cameras,
        lights,
        floor_color: brand.color(ColorRole::Background).into(),
        world_color: brand.color(ColorRole::Background).into(),
        assets: request.primary_asset.iter().cloned().collect(),
        seed: request.seed as u32,
        samples: 24,
        color_management: "standard_scene_linear_to_srgb_rgba".into(),
        units: "metres_degrees_millimetres".into(),
        source_classification: if request.primary_asset.is_some() {
            "original_generic_geometry_with_provided_source_screen"
        } else {
            "original_generic_geometry_graphic_study_not_product_capture"
        }
        .into(),
    };
    plan.validate()?;
    Ok(plan)
}
