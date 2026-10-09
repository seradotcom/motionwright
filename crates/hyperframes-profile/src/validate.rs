use crate::*;
use std::collections::{BTreeMap, BTreeSet};
fn check(ok: bool, reason: &str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(ProfileError(reason.into()))
    }
}
fn number(value: f64, min: f64, max: f64, name: &str) -> Result<()> {
    check(value.is_finite() && value >= min && value <= max, name)
}
fn text(value: &str, max: usize, name: &str) -> Result<()> {
    check(
        !value.trim().is_empty()
            && value.len() <= max
            && !value
                .chars()
                .any(|c| c.is_control() && c != '\n' && c != '\t'),
        name,
    )
}
pub(crate) fn color(value: &str) -> Result<()> {
    check(
        matches!(value.len(), 7 | 9)
            && value.starts_with('#')
            && value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit),
        "Paint must be #RRGGBB or #RRGGBBAA; CSS, URLs and source code are not paint",
    )
}
fn rate(value: FrameRate) -> Result<()> {
    check(
        value.num > 0
            && value.den > 0
            && value.num <= 120_000
            && value.den <= 1001
            && (1.0..=120.0).contains(&(f64::from(value.num) / f64::from(value.den))),
        "Frame rate is outside the rational capture profile",
    )
}
fn property_value(property: Property, value: f64) -> Result<()> {
    let (min, max) = match property {
        Property::X | Property::Y => (-32768.0, 32768.0),
        Property::Width | Property::Height => (0.1, 16384.0),
        Property::ScaleX | Property::ScaleY => (0.01, 16.0),
        Property::Rotation => (-3600.0, 3600.0),
        Property::Opacity => (0.0, 1.0),
        Property::Blur => (0.0, 64.0),
        Property::ClipTop | Property::ClipRight | Property::ClipBottom | Property::ClipLeft => {
            (0.0, 100.0)
        }
    };
    number(
        value,
        min,
        max,
        "Keyframe value is outside its property bounds",
    )
}
fn keys(values: &[Keyframe], frames: u32) -> Result<()> {
    check(values.len() <= 256, "Too many keyframes on one target")?;
    let mut unique = BTreeSet::new();
    for key in values {
        let (num, den) = key.subframe.map_or((0, 1), |s| (s.num, s.den));
        check(
            den > 0 && den <= 1_000_000_000 && num < den,
            "Subframe must be a proper nonnegative rational fraction",
        )?;
        let mut a = num;
        let mut b = den;
        while b != 0 {
            let remainder = a % b;
            a = b;
            b = remainder;
        }
        check(
            a == 1 && (num != 0 || key.subframe.is_none()),
            "Subframe fractions must be canonical and zero must be omitted",
        )?;
        check(
            key.frame < frames && unique.insert((key.property, key.frame, num, den)),
            "Keyframe is duplicate or outside the exact frame interval",
        )?;
        property_value(key.property, key.value)?;
        if let Curve::CubicBezier { x1, y1, x2, y2 } = key.curve {
            for x in [x1, x2] {
                number(
                    x,
                    0.0,
                    1.0,
                    "Bezier time handles must remain monotonic in [0,1]",
                )?;
            }
            // Bounded monotonic progress keeps opacity/clip/geometry inside validated endpoints.
            for y in [y1, y2] {
                number(
                    y,
                    0.0,
                    1.0,
                    "Bezier value handles must stay in [0,1] for this profile",
                )?;
            }
        }
    }
    Ok(())
}
fn asset_kind(assets: &[Asset], id: Uuid, allowed: &[AssetKind]) -> Result<()> {
    check(
        assets
            .iter()
            .any(|a| a.id == id && allowed.contains(&a.kind)),
        "Content references an absent or incompatible digest-bound asset",
    )
}
pub fn validate_plan(plan: &HyperframesPlan) -> Result<()> {
    check(
        plan.revision <= i64::MAX as u64,
        "Project revision is outside persistent bounds",
    )?;
    validate_document(&plan.document)
}
pub fn validate_document(doc: &HyperframesDocument) -> Result<()> {
    check(
        doc.version == PROFILE_VERSION,
        "Unsupported HyperFrames profile version",
    )?;
    let c = &doc.canvas;
    rate(c.rate)?;
    check(
        (320..=4096).contains(&c.width)
            && (240..=4096).contains(&c.height)
            && u64::from(c.width) * u64::from(c.height) <= 8_294_400,
        "Canvas exceeds the advertised pixel budget",
    )?;
    check(
        (1..=MAX_FRAMES).contains(&c.frames) && c.rate.seconds(c.frames) <= 120.0,
        "Timeline exceeds the bounded frame/duration budget",
    )?;
    check(
        u64::from(c.width) * u64::from(c.height) * u64::from(c.frames) <= 7_500_000_000,
        "Pixel-frame work budget exceeded",
    )?;
    if let Some(bg) = &c.background {
        color(bg)?;
    }
    check(
        !doc.nodes.is_empty() && doc.nodes.len() <= MAX_NODES,
        "Composition needs 1..=256 nodes",
    )?;
    check(
        doc.assets.len() <= MAX_ASSETS,
        "Asset count exceeds the native profile",
    )?;
    let mut asset_ids = BTreeSet::new();
    for asset in &doc.assets {
        check(asset_ids.insert(asset.id), "Duplicate asset identity")?;
        check(
            asset.sha256.len() == 64
                && asset
                    .sha256
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Asset digest must be lowercase SHA-256",
        )?;
        text(&asset.rights.owner, 256, "Asset owner is required")?;
        text(&asset.rights.license, 512, "Asset usage terms are required")?;
        check(
            asset.rights.attribution.len() <= 2048 && asset.rights.use_authorized,
            "Explicit asset-use authorization is required",
        )?;
    }
    number(doc.camera.x, -32768.0, 32768.0, "Camera X is out of bounds")?;
    number(doc.camera.y, -32768.0, 32768.0, "Camera Y is out of bounds")?;
    number(doc.camera.zoom, 0.01, 16.0, "Camera zoom is out of bounds")?;
    number(
        doc.camera.rotation,
        -3600.0,
        3600.0,
        "Camera rotation is out of bounds",
    )?;
    keys(&doc.camera.keyframes, c.frames)?;
    check(
        doc.camera.keyframes.iter().all(|key| {
            matches!(
                key.property,
                Property::X | Property::Y | Property::ScaleX | Property::Rotation
            )
        }),
        "2D camera admits X, Y, rotation and uniform zoom (scale_x), not arbitrary target channels",
    )?;
    let nodes = doc
        .nodes
        .iter()
        .map(|n| (n.id, n))
        .collect::<BTreeMap<_, _>>();
    check(nodes.len() == doc.nodes.len(), "Duplicate node identity")?;
    let mut key_count = doc.camera.keyframes.len();
    let mut text_bytes = 0;
    for node in &doc.nodes {
        text(&node.name, 256, "Node name is empty or exceeds its budget")?;
        let p = node.pose;
        for (kind, value) in [
            (Property::X, p.x),
            (Property::Y, p.y),
            (Property::Width, p.width),
            (Property::Height, p.height),
            (Property::ScaleX, p.scale_x),
            (Property::ScaleY, p.scale_y),
            (Property::Rotation, p.rotation),
            (Property::Opacity, p.opacity),
        ] {
            property_value(kind, value)?;
        }
        check(
            (-10000..=10000).contains(&p.z_index),
            "Layer order is out of bounds",
        )?;
        let mut parent = node.parent_id;
        let mut seen = BTreeSet::from([node.id]);
        while let Some(id) = parent {
            check(
                seen.len() <= 16 && seen.insert(id),
                "Node hierarchy exceeds depth bound or contains a cycle",
            )?;
            let ancestor = nodes
                .get(&id)
                .ok_or_else(|| ProfileError("Node parent is missing".into()))?;
            check(
                matches!(ancestor.content, Content::Group),
                "Only a typed group may parent other nodes",
            )?;
            parent = ancestor.parent_id;
        }
        match &node.content {
            Content::Group => {}
            Content::Text {
                runs,
                font,
                size,
                line_height,
                ..
            } => {
                check(
                    !runs.is_empty() && runs.len() <= 32,
                    "Text requires 1..=32 styled runs",
                )?;
                number(
                    *size,
                    4.0,
                    1024.0,
                    "Font size is outside the native text profile",
                )?;
                number(
                    *line_height,
                    0.75,
                    3.0,
                    "Line height is outside bounded typography",
                )?;
                for run in runs {
                    text(&run.text, 16384, "Text run is empty or too large")?;
                    color(&run.color)?;
                    check(
                        (100..=900).contains(&run.weight),
                        "Font weight is outside [100,900]",
                    )?;
                    text_bytes += run.text.len();
                }
                if let Font::Asset { asset_id, family } = font {
                    asset_kind(&doc.assets, *asset_id, &[AssetKind::Woff2])?;
                    check(
                        !family.is_empty()
                            && family.len() <= 80
                            && family.bytes().all(|b| {
                                b.is_ascii_alphanumeric() || matches!(b, b' ' | b'-' | b'_')
                            }),
                        "Custom font family must be a safe local label",
                    )?;
                }
            }
            Content::Rectangle {
                fill,
                stroke,
                stroke_width,
                radius,
            } => {
                color(fill)?;
                if let Some(stroke) = stroke {
                    color(stroke)?;
                }
                number(*stroke_width, 0.0, 128.0, "Stroke width is out of bounds")?;
                number(*radius, 0.0, 4096.0, "Corner radius is out of bounds")?;
            }
            Content::Ellipse {
                fill,
                stroke,
                stroke_width,
            } => {
                color(fill)?;
                if let Some(stroke) = stroke {
                    color(stroke)?;
                }
                number(*stroke_width, 0.0, 128.0, "Stroke width is out of bounds")?;
            }
            Content::Path {
                points,
                fill,
                stroke,
                stroke_width,
                ..
            } => {
                check(
                    (2..=256).contains(&points.len()),
                    "Path requires 2..=256 numeric points",
                )?;
                for p in points {
                    number(p.x, -32768.0, 32768.0, "Path coordinate out of bounds")?;
                    number(p.y, -32768.0, 32768.0, "Path coordinate out of bounds")?;
                }
                if let Some(fill) = fill {
                    color(fill)?;
                }
                color(stroke)?;
                number(*stroke_width, 0.1, 128.0, "Path stroke width out of bounds")?;
            }
            Content::Image { asset_id, .. } => {
                asset_kind(&doc.assets, *asset_id, &[AssetKind::Png, AssetKind::Jpeg])?
            }
            Content::Video {
                asset_id,
                source_start_frame,
                source_rate,
                ..
            } => {
                asset_kind(&doc.assets, *asset_id, &[AssetKind::Mp4])?;
                rate(*source_rate)?;
                check(
                    *source_start_frame <= 432_000,
                    "Video source offset out of bounds",
                )?;
            }
        }
        match &node.clip {
            Clip::None => {}
            Clip::Inset {
                top,
                right,
                bottom,
                left,
                radius,
            } => {
                for v in [top, right, bottom, left] {
                    number(*v, 0.0, 100.0, "Clip inset is not a percentage in [0,100]")?;
                }
                number(*radius, 0.0, 4096.0, "Clip radius out of bounds")?;
            }
            Clip::Circle {
                radius,
                center_x,
                center_y,
            } => {
                number(*radius, 0.0, 200.0, "Circle mask radius out of bounds")?;
                for v in [center_x, center_y] {
                    number(*v, -100.0, 200.0, "Circle mask center out of bounds")?;
                }
            }
            Clip::Polygon { points } => {
                check(
                    (3..=64).contains(&points.len()),
                    "Polygon mask requires 3..=64 points",
                )?;
                for p in points {
                    number(
                        p.x,
                        0.0,
                        100.0,
                        "Mask polygon point outside normalized bounds",
                    )?;
                    number(
                        p.y,
                        0.0,
                        100.0,
                        "Mask polygon point outside normalized bounds",
                    )?;
                }
            }
        }
        property_value(Property::Blur, node.effects.blur)?;
        if let Some(s) = &node.effects.shadow {
            color(&s.color)?;
            number(s.x, -256.0, 256.0, "Shadow offset out of bounds")?;
            number(s.y, -256.0, 256.0, "Shadow offset out of bounds")?;
            number(s.blur, 0.0, 128.0, "Shadow blur out of bounds")?;
        }
        keys(&node.keyframes, c.frames)?;
        key_count += node.keyframes.len();
        if node.keyframes.iter().any(|k| {
            matches!(
                k.property,
                Property::ClipTop | Property::ClipRight | Property::ClipBottom | Property::ClipLeft
            )
        }) {
            check(
                matches!(node.clip, Clip::Inset { .. }),
                "Animated inset requires an inset mask; masks are not approximated",
            )?;
        }
        let locks = node.locked_properties.iter().collect::<BTreeSet<_>>();
        check(
            locks.len() == node.locked_properties.len(),
            "Duplicate property lock",
        )?;
    }
    check(
        key_count <= MAX_KEYFRAMES && text_bytes <= 128 * 1024,
        "Total keyframe or text budget exceeded",
    )?;
    let bytes = serde_json::to_vec(doc).map_err(|e| ProfileError(e.to_string()))?;
    check(
        bytes.len() <= MAX_DOCUMENT_BYTES,
        "Serialized native document exceeds its byte budget",
    )
}
