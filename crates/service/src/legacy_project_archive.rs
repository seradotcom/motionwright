//! Versioned, data-only migration of an application-owned legacy project export.
use super::*;
use base64::{Engine, prelude::BASE64_STANDARD};
use motionwright_domain::{
    Asset, CanvasKeyframe, CanvasNode, MotionInterpolation, MotionProperty, NodeStyle, Project,
    RationalTime, RendererKind, Scene, SceneStatus,
};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

const ARCHIVE_V1: &str = "sequencewright/archive/1";
const DOCUMENT_V1: &str = "sequencewright/document/1";
const MEDIA_LIMIT: usize = 16 * 1024 * 1024;

pub(super) struct Migration {
    pub project: Project,
    pub fingerprint: BundleFingerprint,
    pub originals: BTreeMap<String, Vec<u8>>,
    pub notices: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Archive {
    schema: String,
    version: SourceVersion,
    document: Document,
    blobs: Vec<SourceBlob>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceVersion {
    resource: String,
    generation: Uuid,
    revision: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    schema: String,
    id: String,
    title: String,
    description: String,
    profile: Profile,
    brief: SourceBrief,
    scenes: Vec<SourceScene>,
    assets: Vec<SourceAsset>,
    captions: Vec<serde_json::Value>,
    locks: Vec<serde_json::Value>,
    proposals: Vec<serde_json::Value>,
    reviews: Vec<serde_json::Value>,
    deliveries: Vec<serde_json::Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Profile {
    width: u32,
    height: u32,
    fps: Rate,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Rate {
    num: i64,
    den: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceBrief {
    audience: String,
    objective: String,
    promise: String,
    tone: String,
    constraints: String,
    references: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct SourceScene {
    id: String,
    name: String,
    role: String,
    duration: i64,
    renderer: SourceRenderer,
    background: String,
    narration: String,
    claim_status: String,
    objects: Vec<SourceObject>,
    archived: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum SourceRenderer {
    MotionCanvas,
    Blender,
    Manim,
    Mlt,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct SourceObject {
    id: String,
    kind: SourceKind,
    name: String,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    rotation: i32,
    opacity: u16,
    fill: String,
    text: String,
    font_size: u16,
    weight: u16,
    asset_id: Option<String>,
    keyframes: Vec<SourceKey>,
    archived: bool,
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum SourceKind {
    Text,
    Rectangle,
    Circle,
    Line,
    Image,
    Video,
    Audio,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceKey {
    frame: i64,
    property: SourceProperty,
    value: i32,
    easing: SourceEasing,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum SourceProperty {
    X,
    Y,
    Width,
    Height,
    Rotation,
    Opacity,
}

#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
enum SourceEasing {
    Linear,
    Hold,
    EaseInOut,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceAsset {
    id: String,
    name: String,
    sha256: String,
    mime: String,
    bytes: usize,
    license: String,
    provenance: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceBlob {
    mime: String,
    base64: String,
}

fn invalid() -> motionwright_storage::StorageError {
    motionwright_storage::StorageError::InvalidBackup(
        "invalid or unsupported legacy archive v1".into(),
    )
}

fn source_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-'))
}

fn stable_id(kind: &str, identity: &[&str]) -> Uuid {
    let mut hash = Sha256::new();
    hash.update(b"motionwright.legacy-import/1\0");
    hash.update(kind.as_bytes());
    for part in identity {
        hash.update([0]);
        hash.update(part.as_bytes());
    }
    let mut bytes = [0; 16];
    bytes.copy_from_slice(&hash.finalize()[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x80;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Uuid::from_bytes(bytes)
}

fn frame_time(frame: i64, fps: &Rate) -> StorageResult<RationalTime> {
    let num = frame.checked_mul(fps.den).ok_or_else(invalid)?;
    RationalTime::new(num, fps.num).map_err(|_| invalid())
}

fn color(value: &str) -> bool {
    value.len() == 7 && value.starts_with('#') && value[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

fn keyframe(key: SourceKey, duration: i64, fps: &Rate) -> StorageResult<CanvasKeyframe> {
    if !(0..duration).contains(&key.frame) || !(-16_384..=16_384).contains(&key.value) {
        return Err(invalid());
    }
    let (property, scale) = match key.property {
        SourceProperty::X => (MotionProperty::X, 1.0),
        SourceProperty::Y => (MotionProperty::Y, 1.0),
        SourceProperty::Width => (MotionProperty::Width, 1.0),
        SourceProperty::Height => (MotionProperty::Height, 1.0),
        SourceProperty::Rotation => (MotionProperty::RotationDeg, 1.0),
        SourceProperty::Opacity => (MotionProperty::Opacity, 1000.0),
    };
    let result = CanvasKeyframe {
        at: frame_time(key.frame, fps)?,
        property,
        value: f64::from(key.value) / scale,
        interpolation: match key.easing {
            SourceEasing::Linear => MotionInterpolation::Linear,
            SourceEasing::Hold => MotionInterpolation::Hold,
            SourceEasing::EaseInOut => MotionInterpolation::EaseInOut,
        },
    };
    result.validate().map_err(|_| invalid())?;
    Ok(result)
}

fn node(
    original: SourceObject,
    kind: &str,
    id: Uuid,
    z_index: i32,
    duration: i64,
    fps: &Rate,
) -> StorageResult<CanvasNode> {
    let text = matches!(original.kind, SourceKind::Text);
    let mut keyframes = original
        .keyframes
        .into_iter()
        .map(|key| keyframe(key, duration, fps))
        .collect::<StorageResult<Vec<_>>>()?;
    keyframes.sort_by_key(|key| (key.at, key.property));
    Ok(CanvasNode {
        id,
        name: original.name,
        kind: kind.into(),
        parent_id: None,
        x: f64::from(original.x),
        y: f64::from(original.y),
        width: f64::from(original.width),
        height: f64::from(original.height),
        rotation_deg: f64::from(original.rotation),
        opacity: f64::from(original.opacity) / 1000.0,
        text: text.then_some(original.text),
        coordinate_space: Default::default(),
        z_index,
        style: NodeStyle {
            fill: Some(original.fill),
            font_size: text.then_some(f64::from(original.font_size)),
            font_weight: text.then_some(original.weight),
            ..Default::default()
        },
        relations: vec![],
        property_locks: Default::default(),
        keyframes,
    })
}

fn background(original: &SourceScene, profile: &Profile, id: Uuid) -> CanvasNode {
    CanvasNode {
        id,
        name: "Original scene background".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 0.0,
        y: 0.0,
        width: f64::from(profile.width),
        height: f64::from(profile.height),
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: Default::default(),
        z_index: 0,
        style: NodeStyle {
            fill: Some(original.background.clone()),
            ..Default::default()
        },
        relations: vec![],
        property_locks: Default::default(),
        keyframes: vec![],
    }
}

pub(super) fn from_bytes(bytes: Vec<u8>) -> StorageResult<Migration> {
    let Archive {
        schema,
        version,
        document,
        blobs,
    } = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
    let (resource, branch) = version.resource.split_once('@').ok_or_else(invalid)?;
    if schema != ARCHIVE_V1
        || document.schema != DOCUMENT_V1
        || !source_id(&document.id)
        || resource != document.id
        || !source_id(branch)
        || version.generation.is_nil()
        || version.revision.is_nil()
        || document.scenes.is_empty()
        || document.scenes.len() > 100
        || document.assets.len() > 200
        || document.assets.len() != blobs.len()
        || document.captions.len() > 500
        || document.locks.len() > 200
        || document.proposals.len() > 200
        || document.reviews.len() > 200
        || document.deliveries.len() > 200
    {
        return Err(invalid());
    }

    let original_sha = hex::encode(Sha256::digest(&bytes));
    let fingerprint = BundleFingerprint::from_sha256(original_sha.clone())?;
    let original_revision = version.revision.to_string();
    let mut project = Project::new(document.title).map_err(|_| invalid())?;
    project.id = stable_id("project", &[&original_sha]);
    project.generation = version.generation;
    project.active_branch = stable_id("branch", &[&original_sha]);
    project.branches[0].id = project.active_branch;
    let mut notices = vec![
        "This is an editable snapshot, not the original revision history. The exact archive is retained as a source asset. Scene roles, claim status, foreign locks, reviews and runtime connections are not imported as active state.".into()
    ];
    if !document.captions.is_empty() {
        notices.push("Original caption ranges and text remain in the archive. They were not converted into a voice transcript.".into());
    }
    if !document.deliveries.is_empty() {
        notices.push("Additional legacy delivery profiles remain in the archive; only the document canvas profile was migrated.".into());
    }
    if !document.brief.promise.is_empty()
        || !document.brief.tone.is_empty()
        || !document.brief.references.is_empty()
    {
        notices.push("Promise, tone and reference notes without equivalent brief fields remain in the archive.".into());
    }
    let profile = document.profile;
    if !(240..=7680).contains(&profile.width)
        || !(240..=7680).contains(&profile.height)
        || !(1..=1_000_000).contains(&profile.fps.num)
        || !(1..=100_000).contains(&profile.fps.den)
        || profile.fps.num > 240 * profile.fps.den
    {
        return Err(invalid());
    }
    let fps = RationalTime::new(profile.fps.num, profile.fps.den).map_err(|_| invalid())?;
    if fps.num != profile.fps.num || fps.den != profile.fps.den {
        return Err(invalid());
    }
    project.deliverables.truncate(1);
    let master = &mut project.deliverables[0];
    master.id = stable_id("profile", &[&document.id]);
    master.name = "Original canvas".into();
    master.width = profile.width;
    master.height = profile.height;
    master.frame_rate = fps;
    master.captions = false;
    project.brief.audience = document.brief.audience;
    project.brief.objective = document.brief.objective;
    if !document.brief.constraints.is_empty() {
        let constraint = document.brief.constraints;
        if constraint.len() <= 1000 {
            project.brief.constraints.push(constraint);
        } else {
            notices.push("The original brief constraint exceeds the editable field limit and remains in the archive.".into());
        }
    }
    project.narrative.premise = document.description;

    let mut originals = BTreeMap::new();
    let mut asset_ids = BTreeSet::new();
    for (asset, blob) in document.assets.into_iter().zip(blobs) {
        if !source_id(&asset.id)
            || !asset_ids.insert(asset.id.clone())
            || !(1..=MEDIA_LIMIT).contains(&asset.bytes)
            || blob.base64.len() != asset.bytes.div_ceil(3) * 4
            || asset.mime != blob.mime
        {
            return Err(invalid());
        }
        let body = BASE64_STANDARD.decode(blob.base64).map_err(|_| invalid())?;
        if body.len() != asset.bytes || hex::encode(Sha256::digest(&body)) != asset.sha256 {
            return Err(invalid());
        }
        project.assets.push(Asset {
            id: stable_id("asset", &[&document.id, &asset.id]),
            name: asset.name,
            media_type: asset.mime,
            content_sha256: Some(asset.sha256.clone()),
            source_revision: Some(original_revision.clone()),
        });
        originals.entry(asset.sha256).or_insert(body);
        if !asset.license.is_empty() || !asset.provenance.is_empty() {
            // Source declarations are provenance, not a new grant of rights.
            if !notices.iter().any(|note| note == RIGHTS_NOTE) {
                notices.push(RIGHTS_NOTE.into());
            }
        }
    }
    project.assets.push(Asset {
        id: stable_id("source", &[&original_sha]),
        name: "Original legacy archive.json".into(),
        media_type: "application/vnd.motionwright.legacy-archive+json".into(),
        content_sha256: Some(original_sha.clone()),
        source_revision: Some(original_revision),
    });
    originals.insert(original_sha, bytes);

    let mut scene_ids = BTreeSet::new();
    let mut frame_cursor: i64 = 0;
    let mut archived_items = false;
    let mut media_placements = false;
    let mut line_placements = false;
    let mut renderer_selection = false;
    for original in document.scenes {
        if !source_id(&original.id)
            || !scene_ids.insert(original.id.clone())
            || !(1..=216_000).contains(&original.duration)
            || !color(&original.background)
            || original.objects.len() > 100
            || !matches!(
                original.role.as_str(),
                "hook"
                    | "problem"
                    | "mechanism"
                    | "evidence"
                    | "comparison"
                    | "reveal"
                    | "payoff"
                    | "cta"
            )
            || !matches!(
                original.claim_status.as_str(),
                "unreviewed" | "supported" | "needs-evidence"
            )
        {
            return Err(invalid());
        }
        if original.archived {
            archived_items = true;
            continue;
        }
        renderer_selection |= !matches!(original.renderer, SourceRenderer::MotionCanvas);
        let mut scene = Scene {
            id: stable_id("scene", &[&document.id, &original.id]),
            name: original.name.clone(),
            objective: original.narration.clone(),
            start: frame_time(frame_cursor, &profile.fps)?,
            duration: frame_time(original.duration, &profile.fps)?,
            renderer: RendererKind::MotionCanvas,
            status: SceneStatus::Draft,
            beats: vec![],
            nodes: vec![background(
                &original,
                &profile,
                stable_id("background", &[&document.id, &original.id]),
            )],
            camera: Default::default(),
        };
        let mut node_ids = BTreeSet::new();
        for object in original.objects {
            if !source_id(&object.id)
                || !node_ids.insert(object.id.clone())
                || !color(&object.fill)
                || object.opacity > 1000
                || !(-16_384..=16_384).contains(&object.x)
                || !(-16_384..=16_384).contains(&object.y)
                || !(1..=16_384).contains(&object.width)
                || !(1..=16_384).contains(&object.height)
                || !(-3600..=3600).contains(&object.rotation)
                || !(8..=512).contains(&object.font_size)
                || !(100..=900).contains(&object.weight)
                || object.keyframes.len() > 128
            {
                return Err(invalid());
            }
            let kind = match object.kind {
                SourceKind::Text => Some("text"),
                SourceKind::Rectangle => Some("rectangle"),
                SourceKind::Circle => Some("circle"),
                SourceKind::Line => {
                    line_placements |= !object.archived;
                    Some("rectangle")
                }
                SourceKind::Image | SourceKind::Video | SourceKind::Audio => {
                    if !object
                        .asset_id
                        .as_ref()
                        .is_some_and(|id| asset_ids.contains(id))
                    {
                        return Err(invalid());
                    }
                    media_placements |= !object.archived;
                    None
                }
            };
            if object.archived {
                archived_items = true;
                continue;
            }
            if let Some(kind) = kind {
                let id = stable_id("node", &[&document.id, &original.id, &object.id]);
                let index = scene.nodes.len() as i32;
                scene.nodes.push(node(
                    object,
                    kind,
                    id,
                    index,
                    original.duration,
                    &profile.fps,
                )?);
            }
        }
        frame_cursor += original.duration;
        project.scenes.push(scene);
    }
    if project.scenes.is_empty() || frame_cursor > 2_160_000 {
        return Err(invalid());
    }
    if archived_items {
        notices.push("Archived scenes and objects were retained in the source archive but are not displayed on the active canvas.".into());
    }
    if media_placements {
        notices.push("Original media bytes were restored as assets. Legacy image, video and audio placements remain in the archive and were not recreated on the canvas.".into());
    }
    if line_placements {
        notices.push("Line objects were converted to editable rectangles; exact native line rendering was not verified.".into());
    }
    if renderer_selection {
        notices.push("Legacy renderer selections remain in the original archive. Imported scenes use the editable Motion Canvas route without starting any renderer.".into());
    }
    project.validate().map_err(|_| invalid())?;
    Ok(Migration {
        project,
        fingerprint,
        originals,
        notices,
    })
}

const RIGHTS_NOTE: &str = "Asset license and provenance declarations remain in the original archive; this import does not grant new rights.";
