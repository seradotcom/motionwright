use crate::*;
use native::{
    Blend, Clip, Content, Curve, Effects, Fit, Keyframe, Node, Point, Pose, Property, TextAlign,
    TextRun,
};
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "backend", content = "source", rename_all = "snake_case")]
pub enum CreativeRealization {
    NativeHtml(native::HyperframesDocument),
    BlenderStage(StagePlan),
    AudioScore(SoundPlan),
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CreativeContribution {
    pub component_id: Uuid,
    pub recipe: RecipeId,
    pub recipe_version: u32,
    pub brand_id: Uuid,
    pub brand_revision: u32,
    pub taste_id: Uuid,
    pub taste_revision: u32,
    pub input_sha256: String,
    pub source_sha256: String,
    pub output: CreativeRealization,
    pub media_asset_ids: Vec<Uuid>,
    pub source_classification: String,
    pub creative_approval: String,
}
pub fn realize(
    request: &ComponentRequest,
    brand: &BrandProfile,
    taste: &TasteProfile,
) -> Result<CreativeContribution> {
    request.validate()?;
    brand.validate()?;
    taste.validate()?;
    brand.check_copy(&request.copy, request.locale)?;
    let output = match request.recipe.definition().backend {
        RecipeBackend::NativeHtml => {
            CreativeRealization::NativeHtml(realize_html(request, brand, taste)?)
        }
        RecipeBackend::BlenderStage => {
            CreativeRealization::BlenderStage(product_stage(request, brand)?)
        }
        RecipeBackend::AudioScore => CreativeRealization::AudioScore(sound_component(request)?),
    };
    let source_sha256 = canonical_digest(&output)?;
    let mut media_asset_ids = Vec::new();
    for asset in [&request.primary_asset, &request.secondary_asset]
        .into_iter()
        .flatten()
    {
        media_asset_ids.push(asset.id);
    }
    let classified = match &output {
        CreativeRealization::NativeHtml(doc) => {
            if doc.assets.is_empty() {
                "original_renderer_native_vector_study"
            } else {
                "owner_declared_digest_bound_media_composition"
            }
        }
        CreativeRealization::BlenderStage(plan) => plan.source_classification.as_str(),
        CreativeRealization::AudioScore(plan) => plan.source_classification.as_str(),
    }
    .to_owned();
    Ok(CreativeContribution {
        component_id: request.instance_id,
        recipe: request.recipe,
        recipe_version: request.version,
        brand_id: brand.id,
        brand_revision: brand.revision,
        taste_id: taste.id,
        taste_revision: taste.revision,
        input_sha256: canonical_digest(&(request, brand, taste))?,
        source_sha256,
        output,
        media_asset_ids,
        source_classification: classified,
        creative_approval: "human_review_required".into(),
    })
}
struct Compositor<'a> {
    req: &'a ComponentRequest,
    brand: &'a BrandProfile,
    taste: &'a TasteProfile,
    nodes: Vec<Node>,
    assets: Vec<native::Asset>,
    w: f64,
    h: f64,
    portrait: bool,
}
impl<'a> Compositor<'a> {
    fn new(req: &'a ComponentRequest, brand: &'a BrandProfile, taste: &'a TasteProfile) -> Self {
        let w = f64::from(req.output.width);
        let h = f64::from(req.output.height);
        Self {
            req,
            brand,
            taste,
            nodes: Vec::new(),
            assets: Vec::new(),
            w,
            h,
            portrait: h > w,
        }
    }
    fn pos(&self, x: f64, y: f64, w: f64, h: f64) -> Pose {
        Pose {
            x: geometry(x * self.w),
            y: geometry(y * self.h),
            width: geometry((w * self.w).max(1.0)),
            height: geometry((h * self.h).max(1.0)),
            ..Default::default()
        }
    }
    fn insert(&mut self, role: &str, pose: Pose, content: Content) -> usize {
        let id = stable_id(self.req.instance_id, role);
        let name = role.replace('-', " ");
        self.nodes.push(Node {
            id,
            name,
            parent_id: None,
            pose,
            content,
            blend: Blend::Normal,
            clip: Clip::None,
            effects: Effects::default(),
            keyframes: Vec::new(),
            locked_properties: Vec::new(),
            locked_fields: Vec::new(),
        });
        self.nodes.len() - 1
    }
    #[expect(
        clippy::too_many_arguments,
        reason = "native composition recipes require explicit named bounds, paint and radius for reviewability"
    )]
    fn rect(
        &mut self,
        role: &str,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
        color: ColorRole,
        radius: f64,
    ) -> usize {
        let pose = self.pos(x, y, w, h);
        self.insert(
            role,
            pose,
            Content::Rectangle {
                fill: self.brand.color(color).into(),
                stroke: None,
                stroke_width: 0.0,
                radius,
            },
        )
    }
    fn rule(&mut self, role: &str, x: f64, y: f64, w: f64, color: ColorRole) -> usize {
        self.rect(role, x, y, w, 0.0035, color, 0.0)
    }
    #[expect(
        clippy::too_many_arguments,
        reason = "native composition recipes require explicit text hierarchy, bounds and paint for reviewability"
    )]
    fn headline(
        &mut self,
        role: &str,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
        content: &str,
        small: bool,
        color: ColorRole,
    ) -> Result<usize> {
        text(
            content,
            16 * 1024,
            "Component text cannot be empty or exceed 16KiB",
        )?;
        let mut pose = self.pos(x, y, w, h);
        let lines = content.lines().count().max(1) as f64;
        let max_from_height = pose.height / (lines * 1.2);
        let target = (self.h
            / if small {
                28.0
            } else if self.portrait {
                11.0
            } else {
                9.5
            })
        .min(
            pose.width
                / (content
                    .lines()
                    .map(|l| l.chars().count())
                    .max()
                    .unwrap_or(1)
                    .max(1) as f64
                    * 0.78),
        );
        let size = target.min(max_from_height).clamp(4.0, 128.0);
        pose.height = pose.height.max(lines * size * 1.2);
        let weight = if small {
            self.brand.body_weight
        } else {
            self.brand.display_weight
        };
        Ok(self.insert(
            role,
            pose,
            Content::Text {
                runs: vec![TextRun {
                    text: content.into(),
                    color: self.brand.color(color).into(),
                    weight,
                    italic: false,
                }],
                font: self.brand.font.clone(),
                size,
                line_height: 1.12,
                align: TextAlign::Left,
            },
        ))
    }
    /// Some recipes have optional micro-labels, but the hero's value
    /// proposition and evidence classification must be genuinely readable.
    /// Preserve the exact authored copy; CSS pre-wrap handles word wrapping
    /// without changing or fabricating source text.
    fn require_readable_copy(&mut self, index: usize, minimum_px: f64) -> Result<()> {
        let node = self
            .nodes
            .get_mut(index)
            .ok_or_else(|| CraftError("The authored text node is absent".into()))?;
        let Content::Text {
            runs,
            size,
            line_height,
            ..
        } = &mut node.content
        else {
            return Err(CraftError(
                "Only original editable text can be resized".into(),
            ));
        };
        check(
            minimum_px.is_finite() && (11.0..=72.0).contains(&minimum_px),
            "Readable hero typography has an invalid role minimum",
        )?;
        *size = minimum_px;
        *line_height = 1.12;
        let chars: usize = runs.iter().map(|run| run.text.chars().count()).sum();
        let approximate_width = (node.pose.width / (minimum_px * 0.62)).floor().max(1.0) as usize;
        let estimated_lines = chars.div_ceil(approximate_width);
        let max_lines = (node.pose.height / (minimum_px * *line_height))
            .floor()
            .max(0.0) as usize;
        check(
            estimated_lines <= max_lines,
            "ProductHeroReveal copy cannot fit legibly. Shorten the copy or increase the layout area; do not shrink it to microtype.",
        )?;
        Ok(())
    }
    fn path(
        &mut self,
        role: &str,
        points: Vec<Point>,
        color: ColorRole,
        width: f64,
    ) -> Result<usize> {
        check(points.len() >= 2, "A native path needs actual coordinates")?;
        let index = self.insert(
            role,
            self.pos(0.0, 0.0, 1.0, 1.0),
            Content::Path {
                points,
                closed: false,
                fill: None,
                stroke: self.brand.color(color).into(),
                stroke_width: width,
            },
        );
        Ok(index)
    }
    fn key(&mut self, index: usize, frame: u32, property: Property, value: f64, curve: Curve) {
        if frame >= self.req.output.frames {
            return;
        }
        self.nodes[index].keyframes.push(Keyframe {
            frame,
            subframe: None,
            property,
            value: geometry(value),
            curve,
        });
    }
    fn enter(&mut self, index: usize, dx: f64, dy: f64) -> Result<()> {
        if !self.req.motion {
            return Ok(());
        }
        let end = ((f64::from(self.req.output.frames) * self.taste.entrance_fraction()).round()
            as u32)
            .clamp(1, self.req.output.frames - 1);
        let pose = self.nodes[index].pose;
        if dx != 0.0 {
            self.key(
                index,
                0,
                Property::X,
                (pose.x + dx).clamp(-32768.0, 32768.0),
                Curve::Hold,
            );
            self.key(index, end, Property::X, pose.x, Curve::EaseOutCubic);
        }
        if dy != 0.0 {
            self.key(
                index,
                0,
                Property::Y,
                (pose.y + dy).clamp(-32768.0, 32768.0),
                Curve::Hold,
            );
            self.key(index, end, Property::Y, pose.y, Curve::EaseOutCubic);
        }
        self.key(index, 0, Property::Opacity, 0.0, Curve::Hold);
        self.key(index, end, Property::Opacity, 1.0, Curve::EaseInOut);
        Ok(())
    }
    fn image(
        &mut self,
        role: &str,
        asset: native::Asset,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
    ) -> Result<usize> {
        check(
            matches!(asset.kind, native::AssetKind::Png | native::AssetKind::Jpeg),
            "A recipe still image must use a licensed PNG/JPEG",
        )?;
        check(
            asset.rights.use_authorized,
            "Media source is not authorized",
        )?;
        if !self.assets.iter().any(|a| a.id == asset.id) {
            self.assets.push(asset.clone());
        }
        let pose = self.pos(x, y, w, h);
        Ok(self.insert(
            role,
            pose,
            Content::Image {
                asset_id: asset.id,
                fit: Fit::Contain,
            },
        ))
    }
    fn source(&self) -> Result<native::Asset> {
        self.req.primary_asset.clone().ok_or_else(|| {
            CraftError("This recipe requires a digest-bound, owner-authorized source screen".into())
        })
    }
    fn secondary(&self) -> Result<native::Asset> {
        self.req.secondary_asset.clone().ok_or_else(|| {
            CraftError("The comparison requires two real digest-bound sources".into())
        })
    }
    fn finish(self) -> Result<native::HyperframesDocument> {
        let mut assets = self.assets;
        if let Some(font) = &self.brand.font_asset {
            assets.push(font.clone());
        }
        let document = native::HyperframesDocument {
            version: 1,
            canvas: self.req.output.clone(),
            camera: native::Camera::default(),
            nodes: self.nodes,
            assets,
        };
        native::validate_document(&document).map_err(|err| CraftError(err.to_string()))?;
        Ok(document)
    }
}
pub fn realize_html(
    req: &ComponentRequest,
    brand: &BrandProfile,
    taste: &TasteProfile,
) -> Result<native::HyperframesDocument> {
    req.validate()?;
    brand.validate()?;
    taste.validate()?;
    brand.check_copy(&req.copy, req.locale)?;
    check(
        req.recipe.definition().backend == RecipeBackend::NativeHtml,
        "Selected recipe requires a different native renderer",
    )?;
    let mut c = Compositor::new(req, brand, taste);
    let title = req.copy.headline.as_str();
    let body = req.copy.body.as_str();
    let compact = c.portrait;
    let hero_reveal = req.recipe == RecipeId::HeroReveal;
    let top = if hero_reveal {
        if compact { 0.155 } else { 0.270 }
    } else if compact {
        0.12
    } else {
        0.19
    };
    let headline_size = if hero_reveal {
        if compact { 0.16 } else { 0.24 }
    } else if compact {
        0.14
    } else {
        0.20
    };
    let first = c.headline(
        "primary-statement",
        if hero_reveal { 0.075 } else { 0.09 },
        top,
        if hero_reveal && !compact { 0.43 } else { 0.82 },
        headline_size,
        title,
        false,
        ColorRole::Text,
    )?;
    c.enter(first, 0.0, 16.0)?;
    match req.recipe {
        RecipeId::HeroFocus => {
            let b = c.headline(
                "explanation",
                0.09,
                top + headline_size + 0.035,
                0.79,
                0.10,
                body,
                true,
                ColorRole::MutedText,
            )?;
            c.enter(b, 0.0, 20.0)?;
            c.rule("hero-baseline", 0.09, 0.86, 0.82, ColorRole::Accent);
        }
        RecipeId::ContrastPair => {
            c.rect(
                "left-context",
                0.06,
                0.40,
                0.42,
                0.33,
                ColorRole::Surface,
                12.0,
            );
            let a = c.headline(
                "first-argument",
                0.1,
                0.49,
                0.33,
                0.13,
                &req.copy.label_a,
                false,
                ColorRole::Text,
            )?;
            c.rect(
                "right-context",
                0.52,
                0.40,
                0.42,
                0.33,
                ColorRole::Background,
                12.0,
            );
            c.rule("contrast-axis", 0.5, 0.42, 0.003, ColorRole::Accent);
            let b = c.headline(
                "second-argument",
                0.57,
                0.49,
                0.32,
                0.13,
                &req.copy.label_b,
                false,
                ColorRole::Accent,
            )?;
            c.enter(a, -18.0, 0.0)?;
            c.enter(b, 18.0, 0.0)?;
        }
        RecipeId::MeasuredNumber => {
            let data = req.data.as_ref().ok_or_else(|| {
                CraftError("Measured number requires a source-bound data record".into())
            })?;
            data.validate()?;
            let value = data.format_value(data.rows[0].value, req.locale);
            let n = c.headline(
                "verified-value",
                0.1,
                0.44,
                0.8,
                0.21,
                &value,
                false,
                ColorRole::Accent,
            )?;
            c.enter(n, 0.0, 8.0)?;
            c.headline(
                "verified-source",
                0.1,
                0.75,
                0.82,
                0.10,
                &data.source_caption(),
                true,
                ColorRole::MutedText,
            )?;
        }
        RecipeId::CaptionEmphasis => {
            c.rule("caption-timeline", 0.07, 0.67, 0.86, ColorRole::MutedText);
            let tokens = body.split_whitespace().take(6).collect::<Vec<_>>();
            check(
                !tokens.is_empty(),
                "Caption emphasis requires actual readable words",
            )?;
            for (index, token) in tokens.iter().enumerate() {
                let x = 0.10 + (index % 3) as f64 * 0.29;
                let y = 0.43 + (index / 3) as f64 * 0.14;
                let n = c.headline(
                    &format!("caption-token-{index}"),
                    x,
                    y,
                    0.26,
                    0.12,
                    token,
                    true,
                    if index == 1 {
                        ColorRole::Accent
                    } else {
                        ColorRole::Text
                    },
                )?;
                let frames = req.output.frames;
                let start = (index as u32 * 4).min(frames - 1);
                if req.motion && start > 0 {
                    c.key(n, 0, Property::Opacity, 0.0, Curve::Hold);
                    c.key(n, start, Property::Opacity, 1.0, Curve::EaseInOut);
                }
            }
        }
        RecipeId::ContextLabel => {
            c.rule("orientation-rule", 0.09, 0.45, 0.55, ColorRole::Accent);
            c.headline(
                "context-description",
                0.09,
                0.52,
                0.79,
                0.10,
                body,
                true,
                ColorRole::MutedText,
            )?;
            c.headline(
                "context-footer",
                0.09,
                0.83,
                0.79,
                0.07,
                &req.copy.disclosure,
                true,
                ColorRole::Accent,
            )?;
        }
        RecipeId::HeroReveal => {
            // Semantic hierarchy, material stage and retained source. Without
            // authorized product imagery, original abstract geometry only.
            let (x, y, w, h) = if compact {
                (0.105, 0.465, 0.79, 0.38)
            } else {
                (0.535, 0.225, 0.405, 0.57)
            };
            let eyebrow = c.headline(
                "hero-editorial-eyebrow",
                0.075,
                if compact { 0.075 } else { 0.135 },
                if compact { 0.82 } else { 0.40 },
                0.07,
                &req.copy.eyebrow,
                true,
                ColorRole::Accent,
            )?;
            c.enter(eyebrow, 0.0, -5.0)?;
            let stage = c.rect("hero-product-panel", x, y, w, h, ColorRole::Surface, 16.0);
            c.nodes[stage].pose.rotation = if compact { -1.1 } else { -1.4 };
            c.nodes[stage].effects.shadow = Some(native::Shadow {
                x: 0.0,
                y: 14.0,
                blur: 28.0,
                color: "#00000088".into(),
            });
            let inlay = c.rect(
                "hero-surface-inset",
                x + w * 0.025,
                y + h * 0.029,
                w * 0.95,
                h * 0.94,
                ColorRole::Background,
                12.0,
            );
            c.nodes[inlay].pose.rotation = if compact { -1.1 } else { -1.4 };
            let indicator = c.rect(
                "hero-signal-axis",
                x + w * 0.07,
                y + h * 0.095,
                w * 0.25,
                h * 0.012,
                ColorRole::Accent,
                0.0,
            );
            if req.motion {
                let full = c.nodes[indicator].pose.width;
                c.key(indicator, 0, Property::Width, 1.0, Curve::Hold);
                c.key(
                    indicator,
                    req.output.frames / 3,
                    Property::Width,
                    full,
                    Curve::CubicBezier {
                        x1: 0.17,
                        y1: 0.0,
                        x2: 0.31,
                        y2: 1.0,
                    },
                );
            }
            if let Some(asset) = req.primary_asset.clone() {
                let image = c.image(
                    "hero-real-product-surface",
                    asset,
                    x + w * 0.075,
                    y + h * 0.16,
                    w * 0.85,
                    h * 0.71,
                )?;
                c.nodes[image].effects.shadow = Some(native::Shadow {
                    x: 0.0,
                    y: 8.0,
                    blur: 18.0,
                    color: "#00000055".into(),
                });
                if req.motion {
                    let end = (req.output.frames / 3).max(1);
                    let y = c.nodes[image].pose.y;
                    c.key(image, 0, Property::Opacity, 0.0, Curve::Hold);
                    c.key(image, end, Property::Opacity, 1.0, Curve::EaseInOut);
                    c.key(image, 0, Property::Y, y + 10.0, Curve::Hold);
                    c.key(image, end, Property::Y, y, Curve::EaseOutCubic);
                }
            } else {
                // Abstract original geometry must not be mistaken for the app.
                let frame = c.rect(
                    "hero-original-object-frame",
                    x + w * 0.18,
                    y + h * 0.24,
                    w * 0.63,
                    h * 0.53,
                    ColorRole::Surface,
                    12.0,
                );
                c.nodes[frame].pose.rotation = -4.0;
                let mark = c.rect(
                    "hero-brand-mark",
                    x + w * 0.33,
                    y + h * 0.35,
                    w * 0.42,
                    h * 0.30,
                    ColorRole::Accent,
                    10.0,
                );
                c.nodes[mark].pose.rotation = -4.0;
                c.nodes[mark].effects.shadow = Some(native::Shadow {
                    x: 0.0,
                    y: 8.0,
                    blur: 18.0,
                    color: "#00000066".into(),
                });
                if req.motion {
                    let end = (req.output.frames / 2).max(1);
                    let y = c.nodes[mark].pose.y;
                    c.key(mark, 0, Property::Rotation, -11.0, Curve::Hold);
                    c.key(
                        mark,
                        end,
                        Property::Rotation,
                        -4.0,
                        Curve::CubicBezier {
                            x1: 0.20,
                            y1: 0.0,
                            x2: 0.22,
                            y2: 1.0,
                        },
                    );
                    c.key(mark, 0, Property::Opacity, 0.0, Curve::Hold);
                    c.key(mark, end, Property::Opacity, 1.0, Curve::EaseInOut);
                    c.key(mark, 0, Property::Y, y + 18.0, Curve::Hold);
                    c.key(mark, end, Property::Y, y, Curve::EaseOutCubic);
                }
            }
            let (tx, ty, tw, th) = if compact {
                (0.075, 0.330, 0.83, 0.130)
            } else {
                (0.075, 0.530, 0.405, 0.270)
            };
            let explanation = c.headline(
                "hero-explanation",
                tx,
                ty,
                tw,
                th,
                body,
                true,
                ColorRole::MutedText,
            )?;
            c.require_readable_copy(explanation, brand.minimum_body_size)?;
            if req.motion {
                let start = (req.output.frames / 6).max(1);
                let end = (req.output.frames / 2).max(start + 1);
                let y = c.nodes[explanation].pose.y;
                c.key(explanation, 0, Property::Opacity, 0.0, Curve::Hold);
                c.key(explanation, start, Property::Opacity, 0.0, Curve::Hold);
                c.key(explanation, end, Property::Opacity, 1.0, Curve::EaseInOut);
                c.key(explanation, 0, Property::Y, y + 9.0, Curve::Hold);
                c.key(explanation, end, Property::Y, y, Curve::EaseOutCubic);
            }
            let (fx, fy, fw) = if compact {
                (0.075, 0.89, 0.83)
            } else {
                (0.075, 0.85, 0.425)
            };
            c.rule("hero-reveal-rule", fx, fy, fw, ColorRole::Accent);
            let disclosure = c.headline(
                "hero-evidence-classification",
                fx,
                fy + 0.025,
                fw,
                if compact { 0.065 } else { 0.105 },
                &req.copy.disclosure,
                true,
                ColorRole::MutedText,
            )?;
            c.require_readable_copy(disclosure, 13.0)?;
        }
        RecipeId::ScreenFocus => {
            let asset = c.source()?;
            c.rect(
                "screen-backdrop",
                0.08,
                0.40,
                0.84,
                0.46,
                ColorRole::Surface,
                9.0,
            );
            let image = c.image("real-screen", asset, 0.10, 0.42, 0.80, 0.42)?;
            c.enter(image, 0.0, 9.0)?;
            c.rect(
                "focus-underline",
                0.33,
                0.82,
                0.34,
                0.005,
                ColorRole::Accent,
                0.0,
            );
        }
        RecipeId::FlowBridge => {
            let a = c.source()?;
            let b = c.secondary()?;
            let left = c.image("original-screen-a", a, 0.06, 0.40, 0.36, 0.40)?;
            let right = c.image("original-screen-b", b, 0.58, 0.40, 0.36, 0.40)?;
            c.enter(left, -8.0, 0.0)?;
            c.enter(right, 8.0, 0.0)?;
            c.path(
                "transition-bridge",
                vec![
                    Point {
                        x: 0.44 * c.w,
                        y: 0.60 * c.h,
                    },
                    Point {
                        x: 0.56 * c.w,
                        y: 0.60 * c.h,
                    },
                ],
                ColorRole::Accent,
                3.0,
            )?;
        }
        RecipeId::BrowserStage => {
            let asset = c.source()?;
            c.rect(
                "browser-chrome",
                0.07,
                0.36,
                0.86,
                0.50,
                ColorRole::Surface,
                12.0,
            );
            for i in 0..3 {
                c.rect(
                    &format!("browser-control-{i}"),
                    0.092 + i as f64 * 0.025,
                    0.39,
                    0.009,
                    0.015,
                    ColorRole::MutedText,
                    5.0,
                );
            }
            let image = c.image("real-browser-content", asset, 0.09, 0.44, 0.82, 0.39)?;
            c.enter(image, 0.0, 9.0)?;
        }
        RecipeId::ResponsiveStory => {
            let frames = [
                ("chapter-one", 0.07, 0.44),
                ("chapter-two", 0.37, 0.44),
                ("chapter-three", 0.67, 0.44),
            ];
            for (i, (role, x, y)) in frames.iter().enumerate() {
                let n = c.rect(
                    role,
                    *x,
                    *y,
                    0.26,
                    0.35,
                    if i == 1 {
                        ColorRole::Accent
                    } else {
                        ColorRole::Surface
                    },
                    11.0,
                );
                c.enter(n, 0.0, 8.0)?;
            }
        }
        RecipeId::EvidencePair => {
            let a = c.source()?;
            let b = c.secondary()?;
            c.image("real-evidence-a", a, 0.07, 0.40, 0.40, 0.36)?;
            c.image("real-evidence-b", b, 0.53, 0.40, 0.40, 0.36)?;
            c.headline(
                "evidence-a-label",
                0.07,
                0.78,
                0.40,
                0.06,
                &req.copy.label_a,
                true,
                ColorRole::Text,
            )?;
            c.headline(
                "evidence-b-label",
                0.53,
                0.78,
                0.40,
                0.06,
                &req.copy.label_b,
                true,
                ColorRole::Text,
            )?;
            c.headline(
                "source-classification",
                0.08,
                0.90,
                0.85,
                0.06,
                &req.copy.disclosure,
                true,
                ColorRole::MutedText,
            )?;
        }
        RecipeId::Comparison => {
            c.rule("comparison-divider", 0.5, 0.40, 0.002, ColorRole::MutedText);
            c.headline(
                "comparison-left",
                0.1,
                0.47,
                0.35,
                0.20,
                &req.copy.label_a,
                false,
                ColorRole::Text,
            )?;
            c.headline(
                "comparison-right",
                0.56,
                0.47,
                0.35,
                0.20,
                &req.copy.label_b,
                false,
                ColorRole::Accent,
            )?;
            c.rule("comparison-baseline", 0.1, 0.74, 0.80, ColorRole::Accent);
        }
        RecipeId::ReferenceBoard => {
            for (i, (x, y)) in [(0.08, 0.45), (0.40, 0.45), (0.72, 0.45)]
                .into_iter()
                .enumerate()
            {
                c.rect(
                    &format!("reference-card-{i}"),
                    x,
                    y,
                    0.2,
                    0.27,
                    if i == 1 {
                        ColorRole::Accent
                    } else {
                        ColorRole::Surface
                    },
                    7.0,
                );
                c.rule(
                    &format!("reference-line-{i}"),
                    x,
                    0.75,
                    0.18,
                    ColorRole::MutedText,
                );
            }
            c.headline(
                "reference-provenance",
                0.08,
                0.84,
                0.82,
                0.07,
                &req.copy.disclosure,
                true,
                ColorRole::MutedText,
            )?;
        }
        RecipeId::SharedObject => {
            let n = c.rect(
                "persistent-shared-object",
                0.36,
                0.46,
                0.28,
                0.26,
                ColorRole::Accent,
                14.0,
            );
            if req.motion {
                c.key(n, 0, Property::X, c.w * 0.14, Curve::Hold);
                c.key(
                    n,
                    req.output.frames / 2,
                    Property::X,
                    c.w * 0.57,
                    Curve::EaseInOut,
                );
            }
            c.rule(
                "shared-transition-boundary",
                0.5,
                0.45,
                0.003,
                ColorRole::MutedText,
            );
        }
        RecipeId::FocusTransfer => {
            let left = c.rect(
                "origin-subject",
                0.13,
                0.48,
                0.23,
                0.25,
                ColorRole::Accent,
                9.0,
            );
            let right = c.rect(
                "destination-subject",
                0.64,
                0.48,
                0.23,
                0.25,
                ColorRole::Surface,
                9.0,
            );
            if req.motion {
                c.key(left, 0, Property::Opacity, 1.0, Curve::Hold);
                c.key(
                    left,
                    req.output.frames / 2,
                    Property::Opacity,
                    0.25,
                    Curve::EaseInOut,
                );
                c.key(right, 0, Property::Opacity, 0.25, Curve::Hold);
                c.key(
                    right,
                    req.output.frames / 2,
                    Property::Opacity,
                    1.0,
                    Curve::EaseInOut,
                );
            }
        }
        RecipeId::MaskWindow => {
            let n = c.rect(
                "persistent-masked-reveal",
                0.13,
                0.43,
                0.74,
                0.39,
                ColorRole::Accent,
                14.0,
            );
            c.nodes[n].clip = Clip::Inset {
                top: 0.0,
                right: 0.0,
                bottom: 0.0,
                left: 0.0,
                radius: 14.0,
            };
            if req.motion {
                c.key(n, 0, Property::ClipRight, 100.0, Curve::Hold);
                c.key(
                    n,
                    req.output.frames / 2,
                    Property::ClipRight,
                    0.0,
                    Curve::EaseInOut,
                );
            }
        }
        RecipeId::CameraMatch => {
            c.rect(
                "consistent-world-object",
                0.25,
                0.43,
                0.42,
                0.30,
                ColorRole::Accent,
                15.0,
            );
            c.rect(
                "consistent-world-reference",
                0.74,
                0.44,
                0.12,
                0.16,
                ColorRole::Surface,
                5.0,
            );
            // The camera moves; object identity and coordinate system do not.
        }
        RecipeId::HardCutHold => {
            let n = c.rect(
                "frame-exact-hard-cut",
                0.16,
                0.42,
                0.68,
                0.39,
                ColorRole::Accent,
                0.0,
            );
            if req.motion {
                let half = req.output.frames / 2;
                c.key(n, 0, Property::Opacity, 0.0, Curve::Hold);
                c.key(n, half, Property::Opacity, 1.0, Curve::Hold);
            }
        }
        RecipeId::CausalDiagram => {
            for (i, x) in [0.12, 0.41, 0.70].into_iter().enumerate() {
                c.rect(
                    &format!("causal-node-{i}"),
                    x,
                    0.52,
                    0.18,
                    0.20,
                    if i == 1 {
                        ColorRole::Accent
                    } else {
                        ColorRole::Surface
                    },
                    12.0,
                );
            }
            for (i, x) in [0.30, 0.59].into_iter().enumerate() {
                c.path(
                    &format!("causal-edge-{i}"),
                    vec![
                        Point {
                            x: x * c.w,
                            y: 0.62 * c.h,
                        },
                        Point {
                            x: (x + 0.10) * c.w,
                            y: 0.62 * c.h,
                        },
                    ],
                    ColorRole::Accent,
                    3.0,
                )?;
            }
        }
        RecipeId::SeriesReveal | RecipeId::StateComparison => {
            let data = req.data.as_ref().ok_or_else(|| {
                CraftError("Chart recipe requires source-bound numeric data".into())
            })?;
            data.validate()?;
            let (lower, upper) = data.domain();
            let range = upper - lower;
            let labels = data.rows.len() as f64;
            c.rule("chart-axis", 0.1, 0.80, 0.83, ColorRole::MutedText);
            for (i, row) in data.rows.iter().enumerate() {
                let x = 0.12 + i as f64 * 0.78 / labels;
                let width = (0.7 / labels).min(0.16);
                let percentage = ((row.value - lower) / range).clamp(0.0, 1.0);
                let height = (percentage * 0.34).max(0.004);
                let bar = c.rect(
                    &format!("series-value-{i}"),
                    x,
                    0.79 - height,
                    width,
                    height,
                    if i % 2 == 0 {
                        ColorRole::Accent
                    } else {
                        ColorRole::Secondary
                    },
                    2.0,
                );
                if req.motion {
                    c.key(bar, 0, Property::Height, 1.0, Curve::Hold);
                    c.key(
                        bar,
                        req.output.frames / 2,
                        Property::Height,
                        height * c.h,
                        Curve::EaseOutCubic,
                    );
                }
                let value = data.format_value(row.value, req.locale);
                c.headline(
                    &format!("data-value-{i}"),
                    x,
                    0.81,
                    width,
                    0.07,
                    &value,
                    true,
                    ColorRole::Text,
                )?;
            }
            c.headline(
                "data-source-caption",
                0.1,
                0.91,
                0.84,
                0.06,
                &data.source_caption(),
                true,
                ColorRole::MutedText,
            )?;
        }
        RecipeId::SystemFlow => {
            for (i, x) in [0.09, 0.33, 0.57, 0.81].into_iter().enumerate() {
                let n = c.rect(
                    &format!("system-stage-{i}"),
                    x,
                    0.54,
                    0.12,
                    0.14,
                    if i == 2 {
                        ColorRole::Accent
                    } else {
                        ColorRole::Surface
                    },
                    9.0,
                );
                c.enter(n, 0.0, 7.0)?;
                if i < 3 {
                    c.rule(
                        &format!("system-link-{i}"),
                        x + 0.12,
                        0.61,
                        0.10,
                        ColorRole::Accent,
                    );
                }
            }
        }
        RecipeId::Repeater
        | RecipeId::InfluenceField
        | RecipeId::PathDistribution
        | RecipeId::GridResponse
        | RecipeId::SeededTexture => {
            let points = placements(
                req.recipe,
                req.seed,
                &req.procedural,
                c.w * 0.76,
                c.h * 0.38,
            )?;
            let count = points.len();
            for point in points {
                let x = 0.12 * c.w + point.x;
                let y = 0.45 * c.h + point.y;
                let radius = 4.0 + point.scale * 9.0;
                let n = c.insert(
                    &format!("procedural-element-{}", point.index),
                    Pose {
                        x,
                        y,
                        width: radius * 2.0,
                        height: radius * 2.0,
                        rotation: point.rotation,
                        ..Default::default()
                    },
                    Content::Ellipse {
                        fill: brand
                            .color(if point.index % 5 == 0 {
                                ColorRole::Accent
                            } else {
                                ColorRole::MutedText
                            })
                            .into(),
                        stroke: None,
                        stroke_width: 0.0,
                    },
                );
                if req.motion && point.index % 3 == 0 {
                    c.enter(n, 0.0, (point.index as f64 / count as f64) * 8.0)?;
                }
            }
            c.headline(
                "procedural-seed-provenance",
                0.09,
                0.87,
                0.82,
                0.06,
                &format!("Original seeded study / seed {}", req.seed),
                true,
                ColorRole::MutedText,
            )?;
        }
        _ => return Err(CraftError("Unadmitted native HTML component".into())),
    };
    if req.recipe == RecipeId::CameraMatch && req.motion {
        let camera = &mut c.nodes[0];
        camera.name.push_str(" / consistent coordinates");
    }
    // Browser-native camera animation is preserved in the owning native document.
    let mut result = c.finish()?;
    if req.recipe == RecipeId::CameraMatch && req.motion {
        result.camera.keyframes = vec![
            Keyframe {
                frame: 0,
                subframe: None,
                property: Property::X,
                value: -12.0,
                curve: Curve::Hold,
            },
            Keyframe {
                frame: req.output.frames / 2,
                subframe: None,
                property: Property::X,
                value: 0.0,
                curve: Curve::EaseInOut,
            },
        ];
    }
    native::validate_document(&result).map_err(|err| CraftError(err.to_string()))?;
    Ok(result)
}
