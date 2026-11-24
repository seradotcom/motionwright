//! Bundled, data-only first-use examples. A version identifies fixed authoring
//! content, never a renderer, account, provider grant or executed script.
use crate::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum ProjectTemplate {
    #[serde(rename = "motion-study-v1")]
    MotionStudyV1,
}
impl<'de> Deserialize<'de> for ProjectTemplate {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        match String::deserialize(deserializer)?.as_str() {
            "motion-study-v1" => Ok(Self::MotionStudyV1),
            other => Err(serde::de::Error::unknown_variant(
                other,
                &["motion-study-v1"],
            )),
        }
    }
}
impl ProjectTemplate {
    pub fn id(self) -> &'static str {
        match self {
            Self::MotionStudyV1 => "motion-study-v1",
        }
    }
    /// Construct the complete revision-zero document before Store persistence.
    /// Future content changes need a new template version; recovery reads the
    /// saved original and never regenerates content from a template.
    pub fn create(self, title: impl Into<String>) -> Result<Project> {
        match self {
            Self::MotionStudyV1 => motion_study(title),
        }
    }
}
fn rectangle(name: &str, x: f64, y: f64, width: f64, height: f64, color: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: "rect".into(),
        parent_id: None,
        x,
        y,
        width,
        height,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle {
            fill: Some(color.into()),
            ..Default::default()
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn motion_study(title: impl Into<String>) -> Result<Project> {
    let mut project = Project::new(title)?;
    project.brief.objective = "Make this six-second motion study your own. Edit the shapes, colors and motion; save, reopen and export the original project.".into();
    project.brief.audience = "Anyone learning the local Motionwright workflow.".into();
    project.brief.constraints = vec![
        "Two scenes, six seconds, 640 x 360 at 30 fps.".into(),
        "Editable first-party shapes only; no account, paid AI, external media or font download."
            .into(),
    ];
    project.narrative.premise =
        "A colored card travels across the canvas, then settles into its final position.".into();
    project.visual_language.palette = vec![
        VisualToken {
            name: "background".into(),
            value: "#101820".into(),
        },
        VisualToken {
            name: "ink".into(),
            value: "#F3F6FA".into(),
        },
        VisualToken {
            name: "accent".into(),
            value: "#59C7B5".into(),
        },
    ];
    for (index, (name, objective, from, to, role)) in [
        (
            "Slide in",
            "Follow the card as it enters the composition.",
            (192.0, 336.0),
            (792.0, 336.0),
            DeliveryNarrativeRole::Hook,
        ),
        (
            "Settle",
            "Move the card into its final position and hold.",
            (792.0, 336.0),
            (1296.0, 672.0),
            DeliveryNarrativeRole::Payoff,
        ),
    ]
    .into_iter()
    .enumerate()
    {
        let panel = rectangle("Backdrop", 96.0, 96.0, 1728.0, 888.0, "#1B2D3A");
        let mut card = rectangle("Moving card", from.0, from.1, 336.0, 216.0, "#59C7B5");
        card.z_index = 1;
        for (at, point) in [
            (RationalTime::ZERO, from),
            (
                RationalTime::new(2, 1).map_err(|e| DomainError::Invalid(e.to_string()))?,
                to,
            ),
        ] {
            for (property, value) in [(MotionProperty::X, point.0), (MotionProperty::Y, point.1)] {
                card.keyframes.push(CanvasKeyframe {
                    at,
                    property,
                    value,
                    interpolation: MotionInterpolation::Linear,
                });
            }
        }
        let id = Uuid::now_v7();
        project.scenes.push(Scene {
            id,
            name: name.into(),
            objective: objective.into(),
            start: whole_seconds(index as i64 * 3),
            duration: whole_seconds(3),
            renderer: RendererKind::MotionCanvas,
            status: SceneStatus::Draft,
            beats: vec![],
            nodes: vec![panel, card],
            camera: CameraState::default(),
        });
        let recipe = project
            .production_design
            .delivery_recipes
            .entry(project.deliverables[0].id)
            .or_default();
        recipe.scene_intents.push(DeliverySceneIntent {
            scene_id: id,
            role: Some(role),
            archetype: Some(DeliveryArchetype::Statement),
        });
    }
    project.deliverables.truncate(1);
    let profile = &mut project.deliverables[0];
    profile.name = "Motion study 640 x 360".into();
    profile.width = 640;
    profile.height = 360;
    profile.frame_rate =
        RationalTime::new(30, 1).map_err(|e| DomainError::Invalid(e.to_string()))?;
    profile.captions = false;
    profile.burn_in_captions = false;
    project.validate()?;
    Ok(project)
}
