//! Explicit projection from a supported legacy Canvas subset, or direct native source.
//! Unsupported source semantics remain attached to their source; they are never dropped.
use motionwright_domain::{self as d, hyperframes_profile as h};
use semwright_native_sdk::{Error, ErrorCode, Result};
use uuid::Uuid;
fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
fn unsupported(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}
fn rate(profile: &d::DeliverableProfile) -> Result<h::FrameRate> {
    Ok(h::FrameRate {
        num: u32::try_from(profile.frame_rate.num)
            .map_err(|_| invalid("Invalid native frame rate"))?,
        den: u32::try_from(profile.frame_rate.den)
            .map_err(|_| invalid("Invalid native frame rate"))?,
    })
}
fn frame_time(time: d::RationalTime, rate: h::FrameRate) -> Result<(u32, Option<h::Subframe>)> {
    if time.num < 0 || time.den <= 0 {
        return Err(invalid("Native time must be nonnegative"));
    }
    let numerator = i128::from(time.num) * i128::from(rate.num);
    let denominator = i128::from(time.den) * i128::from(rate.den);
    let whole = numerator / denominator;
    let remainder = numerator % denominator;
    let whole = u32::try_from(whole)
        .map_err(|_| unsupported("Native frame range exceeds the bounded profile"))?;
    if remainder == 0 {
        return Ok((whole, None));
    }
    let mut a = remainder;
    let mut b = denominator;
    while b != 0 {
        let next = a % b;
        a = b;
        b = next;
    }
    let num = u32::try_from(remainder / a)
        .map_err(|_| unsupported("Native subframe numerator exceeds the exact profile"))?;
    let den = u32::try_from(denominator / a)
        .map_err(|_| unsupported("Native subframe denominator exceeds the exact profile"))?;
    if den > 1_000_000_000 {
        return Err(unsupported(
            "Native subframe precision cannot be preserved exactly by this profile",
        ));
    }
    Ok((whole, Some(h::Subframe { num, den })))
}
pub fn native_frame_count(time: d::RationalTime, rate: h::FrameRate) -> Result<u32> {
    let (count, sub) = frame_time(time, rate)?;
    if sub.is_some() || count == 0 {
        return Err(unsupported(
            "Scene duration must cover an exact nonzero number of output frames",
        ));
    }
    Ok(count)
}

pub fn prepare_hyperframes_plan(
    project: &d::Project,
    document_id: Uuid,
) -> Result<h::HyperframesPlan> {
    project.validate().map_err(|e| invalid(e.to_string()))?;
    let native = project
        .production_design
        .workspace
        .native_scenes
        .iter()
        .find(|doc| doc.id == document_id)
        .ok_or_else(|| invalid("Native scene document is absent"))?;
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == native.scene_id)
        .ok_or_else(|| invalid("Native scene binding is absent"))?;
    if scene.renderer != d::RendererKind::Hyperframes
        || !d::renderer_extension_enabled(&project.extensions, &d::RendererKind::Hyperframes)
    {
        return Err(unsupported(
            "HyperFrames must be explicitly selected and enabled for this project before production",
        ));
    }
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == native.profile_id)
        .ok_or_else(|| invalid("Native output profile is absent"))?;
    let d::NativeSceneSource::Hyperframes(document) = &native.source;
    let rate = rate(profile)?;
    if document.canvas.width != profile.width
        || document.canvas.height != profile.height
        || document.canvas.rate != rate
        || document.canvas.frames != native_frame_count(scene.duration, rate)?
    {
        return Err(unsupported(
            "Saved native source no longer matches the edited timeline/output profile; create an explicit realization instead of silently retiming or cropping it",
        ));
    }
    let plan = h::HyperframesPlan {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        scene_id: scene.id,
        document: document.clone(),
    };
    h::validate_plan(&plan).map_err(|e| invalid(e.to_string()))?;
    Ok(plan)
}

/// A contained layout projection. This preserves stored coordinates and all admitted
/// keyframe channels with rational subframes. It does not invent semantic reflow.
pub fn project_canvas_to_hyperframes(
    project: &d::Project,
    scene_id: Uuid,
    profile_id: Uuid,
) -> Result<h::HyperframesDocument> {
    project.validate().map_err(|e| invalid(e.to_string()))?;
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .ok_or_else(|| invalid("Source scene is absent"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == profile_id)
        .ok_or_else(|| invalid("Source profile is absent"))?;
    if !scene.beats.is_empty() {
        return Err(unsupported(
            "Editorial beat scopes require explicit continuity mapping before native conversion",
        ));
    }
    if scene.camera.rotation_deg != 0.0 {
        return Err(unsupported(
            "Legacy camera rotation convention must be reviewed explicitly before conversion",
        ));
    }
    let rate = rate(profile)?;
    let frames = native_frame_count(scene.duration, rate)?;
    let scale = (f64::from(profile.width) / 1920.0).min(f64::from(profile.height) / 1080.0);
    let offset_x = (f64::from(profile.width) - 1920.0 * scale) / 2.0;
    let offset_y = (f64::from(profile.height) - 1080.0 * scale) / 2.0;
    let mut nodes = Vec::new();
    for node in &scene.nodes {
        if node.coordinate_space != d::CoordinateSpace::ProjectPixels
            || node.parent_id.is_some()
            || !node.relations.is_empty()
        {
            return Err(unsupported(format!(
                "Node {} has hierarchy, coordinate or relation semantics that cannot be inferred; native source was not replaced",
                node.id
            )));
        }
        let fill = || {
            node.style.fill.clone().ok_or_else(||unsupported("Native conversion requires an explicit paint, not renderer-specific default styling"))
        };
        let content = match node.kind.as_str() {
            "text" => {
                let font = match node.style.font_family.as_deref() {
                    Some("Instrument Sans Variable") => h::Font::Sans,
                    Some("IBM Plex Mono") => h::Font::Mono,
                    _ => {
                        return Err(unsupported(
                            "Resolve the exact licensed font before native conversion; no ambient font fallback",
                        ));
                    }
                };
                h::Content::Text {
                    runs: vec![h::TextRun {
                        text: profile
                            .text_overrides
                            .get(&node.id)
                            .or(node.text.as_ref())
                            .cloned()
                            .ok_or_else(|| invalid("Text node has no logical content"))?,
                        color: fill()?,
                        weight: node.style.font_weight.unwrap_or(400),
                        italic: false,
                    }],
                    font,
                    size: node
                        .style
                        .font_size
                        .ok_or_else(|| invalid("Text node has no explicit size"))?
                        * scale,
                    line_height: node.style.line_height.unwrap_or(1.12),
                    align: h::TextAlign::Left,
                }
            }
            "shape" | "rectangle" => h::Content::Rectangle {
                fill: fill()?,
                stroke: node.style.stroke.clone(),
                stroke_width: node.style.stroke_width * scale,
                radius: 0.0,
            },
            "circle" => h::Content::Ellipse {
                fill: fill()?,
                stroke: node.style.stroke.clone(),
                stroke_width: node.style.stroke_width * scale,
            },
            "group" => h::Content::Group,
            _ => {
                return Err(unsupported(format!(
                    "Node {} requires its native source renderer",
                    node.id
                )));
            }
        };
        let blend = match node.style.blend_mode {
            d::BlendMode::Normal => h::Blend::Normal,
            d::BlendMode::Multiply => h::Blend::Multiply,
            d::BlendMode::Screen => h::Blend::Screen,
            d::BlendMode::Add => {
                return Err(unsupported(
                    "Legacy additive blending is not silently substituted with another browser blend operator",
                ));
            }
        };
        let mut keys = Vec::new();
        for key in &node.keyframes {
            let (frame, subframe) = frame_time(key.at, rate)?;
            let (property, value) = match key.property {
                d::MotionProperty::X => (h::Property::X, key.value * scale + offset_x),
                d::MotionProperty::Y => (h::Property::Y, key.value * scale + offset_y),
                d::MotionProperty::Width => (h::Property::Width, key.value * scale),
                d::MotionProperty::Height => (h::Property::Height, key.value * scale),
                d::MotionProperty::RotationDeg => (h::Property::Rotation, key.value),
                d::MotionProperty::Opacity => (h::Property::Opacity, key.value),
            };
            let curve = match key.interpolation {
                d::MotionInterpolation::Hold => h::Curve::Hold,
                d::MotionInterpolation::Linear => h::Curve::Linear,
                d::MotionInterpolation::EaseInOut => h::Curve::EaseInOut,
                d::MotionInterpolation::EaseOutCubic => h::Curve::EaseOutCubic,
            };
            keys.push(h::Keyframe {
                frame,
                subframe,
                property,
                value,
                curve,
            });
        }
        let mut protected = Vec::new();
        let mut fields = Vec::new();
        for lock in &node.property_locks {
            match lock {
                d::NodeProperty::Position => protected.extend([h::Property::X, h::Property::Y]),
                d::NodeProperty::Size => {
                    protected.extend([h::Property::Width, h::Property::Height])
                }
                d::NodeProperty::Rotation => protected.push(h::Property::Rotation),
                d::NodeProperty::Opacity => protected.push(h::Property::Opacity),
                d::NodeProperty::Text => fields.push(h::NodeField::Content),
                d::NodeProperty::Style => {
                    fields.extend([h::NodeField::Content, h::NodeField::Appearance])
                }
                d::NodeProperty::Parent => fields.push(h::NodeField::Parent),
                d::NodeProperty::Order => {
                    return Err(unsupported(
                        "Native layer-order protection requires an explicit corresponding lock",
                    ));
                }
            };
        }
        fields.sort();
        fields.dedup();
        protected.sort();
        protected.dedup();
        nodes.push(h::Node {
            id: node.id,
            name: node.name.clone(),
            parent_id: None,
            pose: h::Pose {
                x: node.x * scale + offset_x,
                y: node.y * scale + offset_y,
                width: node.width * scale,
                height: node.height * scale,
                scale_x: 1.0,
                scale_y: 1.0,
                rotation: node.rotation_deg,
                opacity: node.opacity,
                z_index: node.z_index,
            },
            content,
            blend,
            clip: h::Clip::None,
            effects: h::Effects::default(),
            keyframes: keys,
            locked_properties: protected,
            locked_fields: fields,
        });
    }
    let background = project
        .visual_language
        .palette
        .iter()
        .find(|token| {
            matches!(
                token.name.to_ascii_lowercase().as_str(),
                "surface" | "background"
            )
        })
        .map(|token| token.value.clone())
        .ok_or_else(|| invalid("Native source requires an explicit background token"))?;
    let document = h::HyperframesDocument {
        version: h::PROFILE_VERSION,
        canvas: h::Canvas {
            width: profile.width,
            height: profile.height,
            rate,
            frames,
            background: Some(background),
        },
        camera: h::Camera {
            x: (960.0 - scene.camera.center_x) * scale * scene.camera.zoom,
            y: (540.0 - scene.camera.center_y) * scale * scene.camera.zoom,
            zoom: scene.camera.zoom,
            rotation: 0.0,
            keyframes: vec![],
        },
        nodes,
        assets: vec![],
    };
    h::validate_document(&document).map_err(|e| invalid(e.to_string()))?;
    Ok(document)
}
