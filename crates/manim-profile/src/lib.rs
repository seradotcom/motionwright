use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

pub const SCENE_CLASS: &str = "MotionwrightScene";
pub const MAX_PRIMITIVES: usize = 512;
const MAX_TEXT_BYTES: usize = 16 * 1024;
const MAX_ABS_COORD: f64 = 128.0;
const MAX_DIMENSION: f64 = 128.0;

#[derive(Debug, Error, Clone, PartialEq)]
pub enum ManimProfileError {
    #[error("{0}")]
    Invalid(String),
}

pub type Result<T> = std::result::Result<T, ManimProfileError>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ManimPrimitive {
    Text {
        node_id: Uuid,
        text: String,
        x: f64,
        y: f64,
        font_size: f64,
        color: String,
    },
    Rectangle {
        node_id: Uuid,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        color: String,
    },
    Circle {
        node_id: Uuid,
        x: f64,
        y: f64,
        radius: f64,
        color: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimScenePlan {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub primitives: Vec<ManimPrimitive>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ManimRenderProfile {
    pub width: u32,
    pub height: u32,
    pub frame_rate: u32,
}

impl Default for ManimRenderProfile {
    fn default() -> Self {
        Self {
            width: 1920,
            height: 1080,
            frame_rate: 30,
        }
    }
}

impl ManimRenderProfile {
    pub fn validate(self) -> Result<()> {
        if !(320..=7680).contains(&self.width)
            || !(240..=4320).contains(&self.height)
            || !(1..=120).contains(&self.frame_rate)
        {
            return Err(ManimProfileError::Invalid(
                "Manim render profile is outside certified bounds".into(),
            ));
        }
        let pixels = u64::from(self.width) * u64::from(self.height);
        if pixels > 33_177_600 {
            return Err(ManimProfileError::Invalid(
                "Manim render profile exceeds the bounded pixel budget".into(),
            ));
        }
        Ok(())
    }

    pub fn quality_directory(self) -> String {
        format!("{}p{}", self.height, self.frame_rate)
    }
}

pub fn validate_plan(plan: &ManimScenePlan) -> Result<()> {
    if plan.primitives.is_empty() || plan.primitives.len() > MAX_PRIMITIVES {
        return Err(ManimProfileError::Invalid(
            "Manim plan has an invalid primitive count".into(),
        ));
    }
    for primitive in &plan.primitives {
        match primitive {
            ManimPrimitive::Text {
                text,
                x,
                y,
                font_size,
                color,
                ..
            } => {
                finite_coord(*x)?;
                finite_coord(*y)?;
                if text.is_empty()
                    || text.len() > MAX_TEXT_BYTES
                    || text.contains('\0')
                    || !font_size.is_finite()
                    || !(4.0..=512.0).contains(font_size)
                {
                    return Err(ManimProfileError::Invalid(
                        "Manim text primitive is outside certified bounds".into(),
                    ));
                }
                color_hex(color)?;
            }
            ManimPrimitive::Rectangle {
                x,
                y,
                width,
                height,
                color,
                ..
            } => {
                finite_coord(*x)?;
                finite_coord(*y)?;
                positive_dimension(*width)?;
                positive_dimension(*height)?;
                color_hex(color)?;
            }
            ManimPrimitive::Circle {
                x,
                y,
                radius,
                color,
                ..
            } => {
                finite_coord(*x)?;
                finite_coord(*y)?;
                positive_dimension(*radius)?;
                color_hex(color)?;
            }
        }
    }
    Ok(())
}

fn finite_coord(value: f64) -> Result<()> {
    if !value.is_finite() || value.abs() > MAX_ABS_COORD {
        return Err(ManimProfileError::Invalid(
            "Manim coordinate is outside certified bounds".into(),
        ));
    }
    Ok(())
}

fn positive_dimension(value: f64) -> Result<()> {
    if !value.is_finite() || value <= 0.0 || value > MAX_DIMENSION {
        return Err(ManimProfileError::Invalid(
            "Manim geometry dimension is outside certified bounds".into(),
        ));
    }
    Ok(())
}

fn color_hex(value: &str) -> Result<()> {
    let bytes = value.as_bytes();
    if bytes.len() != 7 || bytes[0] != b'#' || !bytes[1..].iter().all(u8::is_ascii_hexdigit) {
        return Err(ManimProfileError::Invalid(
            "Manim color must be explicit #RRGGBB".into(),
        ));
    }
    Ok(())
}

fn py_string(value: &str) -> Result<String> {
    serde_json::to_string(value)
        .map_err(|_| ManimProfileError::Invalid("Manim string could not be encoded".into()))
}

pub fn compile_manim_python(plan: &ManimScenePlan) -> Result<String> {
    validate_plan(plan)?;
    let mut body = String::from(
        "from manim import Circle, Create, Rectangle, Scene, Text\n\n\nclass MotionwrightScene(Scene):\n    def construct(self):\n",
    );
    for (index, primitive) in plan.primitives.iter().enumerate() {
        match primitive {
            ManimPrimitive::Text {
                text,
                x,
                y,
                font_size,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Text({}, font_size={font_size:.6}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    py_string(text)?,
                    py_string(color)?
                ));
            }
            ManimPrimitive::Rectangle {
                x,
                y,
                width,
                height,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Rectangle(width={width:.9}, height={height:.9}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    py_string(color)?
                ));
            }
            ManimPrimitive::Circle {
                x,
                y,
                radius,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Circle(radius={radius:.9}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    py_string(color)?
                ));
            }
        }
        body.push_str(&format!("        self.play(Create(item_{index}))\n"));
    }
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan(text: &str) -> ManimScenePlan {
        ManimScenePlan {
            project_id: Uuid::nil(),
            generation: Uuid::from_u128(1),
            revision: 7,
            scene_id: Uuid::from_u128(2),
            primitives: vec![ManimPrimitive::Text {
                node_id: Uuid::from_u128(3),
                text: text.into(),
                x: 0.0,
                y: 0.0,
                font_size: 48.0,
                color: "#F5B84A".into(),
            }],
        }
    }

    #[test]
    fn compiler_is_fixed_vocabulary_and_escapes_text() {
        let source = compile_manim_python(&plan("x = y + 1\n\"quoted\"")).unwrap();
        assert!(source.contains("class MotionwrightScene(Scene):"));
        assert!(source.contains("Text("));
        for forbidden in ["subprocess", "eval(", "exec(", "__import__", "open("] {
            assert!(!source.contains(forbidden));
        }
        assert!(source.contains("\\n\\\"quoted\\\""));
    }

    #[test]
    fn rejects_non_finite_geometry_and_non_hex_colors() {
        let mut value = plan("safe");
        if let ManimPrimitive::Text { x, color, .. } = &mut value.primitives[0] {
            *x = f64::NAN;
            *color = "WHITE".into();
        }
        assert!(validate_plan(&value).is_err());
    }

    #[test]
    fn render_profile_is_bounded() {
        assert!(ManimRenderProfile::default().validate().is_ok());
        assert!(
            ManimRenderProfile {
                width: 9000,
                height: 1080,
                frame_rate: 30
            }
            .validate()
            .is_err()
        );
        assert_eq!(ManimRenderProfile::default().quality_directory(), "1080p30");
    }
}
