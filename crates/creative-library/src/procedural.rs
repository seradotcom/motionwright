use crate::*;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProceduralOptions {
    pub count: u16,
    pub columns: u16,
    pub influence_x: f64,
    pub influence_y: f64,
    pub amplitude: f64,
    pub falloff: f64,
    pub spacing: f64,
    pub path: Vec<native::Point>,
}
impl Default for ProceduralOptions {
    fn default() -> Self {
        Self {
            count: 24,
            columns: 6,
            influence_x: 0.62,
            influence_y: 0.38,
            amplitude: 0.32,
            falloff: 0.62,
            spacing: 0.15,
            path: vec![
                native::Point { x: 0.0, y: 0.75 },
                native::Point { x: 0.27, y: 0.16 },
                native::Point { x: 0.65, y: 0.85 },
                native::Point { x: 1.0, y: 0.25 },
            ],
        }
    }
}
impl ProceduralOptions {
    pub fn validate(&self) -> Result<()> {
        check(
            (1..=96).contains(&self.count) && (1..=16).contains(&self.columns),
            "Procedural count/columns exceed their editable instance budget",
        )?;
        for value in [
            self.influence_x,
            self.influence_y,
            self.amplitude,
            self.falloff,
            self.spacing,
        ] {
            check(
                value.is_finite() && (0.0..=1.0).contains(&value),
                "Procedural fields require finite normalized parameters",
            )?;
        }
        check(
            (2..=32).contains(&self.path.len()),
            "Procedural path requires 2..32 control points",
        )?;
        for point in &self.path {
            check(
                point.x.is_finite()
                    && point.y.is_finite()
                    && (0.0..=1.0).contains(&point.x)
                    && (0.0..=1.0).contains(&point.y),
                "Procedural path points must remain in the normalized domain",
            )?;
        }
        check(
            self.path.windows(2).any(|pair| pair[0] != pair[1]),
            "Procedural path has zero length",
        )
    }
}
/// An explicit integer generator. No ambient RNG, system time or platform seed enters a realization.
#[derive(Debug, Clone)]
pub struct SeededRandom {
    state: u64,
}
impl SeededRandom {
    pub fn new(seed: u64) -> Self {
        Self {
            state: if seed == 0 { 0x4d57524947485431 } else { seed },
        }
    }
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545f4914f6cdd1d)
    }
    pub fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / 9007199254740992.0
    }
}
/// Quantize editable geometric values to a documented millionth of a CSS pixel,
/// keeping library math stable across host libm implementations without frame rounding.
pub fn geometry(value: f64) -> f64 {
    let rounded = (value * 1_000_000.0).round() / 1_000_000.0;
    if rounded == 0.0 { 0.0 } else { rounded }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ProceduralPlacement {
    pub index: u16,
    pub x: f64,
    pub y: f64,
    pub scale: f64,
    pub rotation: f64,
    pub response: f64,
}
pub fn placements(
    recipe: RecipeId,
    seed: u64,
    options: &ProceduralOptions,
    width: f64,
    height: f64,
) -> Result<Vec<ProceduralPlacement>> {
    options.validate()?;
    check(
        width.is_finite() && height.is_finite() && width > 0.0 && height > 0.0,
        "Procedural bounds must be positive and finite",
    )?;
    check(
        matches!(
            recipe,
            RecipeId::Repeater
                | RecipeId::InfluenceField
                | RecipeId::PathDistribution
                | RecipeId::GridResponse
                | RecipeId::SeededTexture
        ),
        "Recipe does not define a procedural field",
    )?;
    let mut random = SeededRandom::new(seed);
    let mut output = Vec::new();
    let count = usize::from(options.count);
    let columns = usize::from(options.columns).min(count);
    let rows = count.div_ceil(columns);
    let lengths = options
        .path
        .windows(2)
        .map(|pair| ((pair[1].x - pair[0].x) * width).hypot((pair[1].y - pair[0].y) * height))
        .collect::<Vec<_>>();
    let path_length = lengths.iter().sum::<f64>();
    for index in 0..count {
        let u = (index % columns) as f64 / (columns.saturating_sub(1).max(1)) as f64;
        let v = (index / columns) as f64 / (rows.saturating_sub(1).max(1)) as f64;
        let distance = (u - options.influence_x).hypot(v - options.influence_y);
        let response = (1.0 - distance / (0.10 + options.falloff * 1.5)).clamp(0.0, 1.0);
        let response = response * response * (3.0 - 2.0 * response);
        let (mut x, mut y, mut scale, mut rotation) = (u * width, v * height, 1.0, 0.0);
        match recipe {
            RecipeId::Repeater => {
                x = u * width;
                y = v * height;
                scale = 1.0 - options.spacing * 0.45;
            }
            RecipeId::InfluenceField => {
                x = (u + (u - options.influence_x) * response * options.amplitude * 0.25)
                    .clamp(0.0, 1.0)
                    * width;
                y = (v + (v - options.influence_y) * response * options.amplitude * 0.25)
                    .clamp(0.0, 1.0)
                    * height;
                scale = 0.35 + response * 0.65;
                rotation = geometry(response * 45.0 * options.amplitude);
            }
            RecipeId::PathDistribution => {
                let mut remaining =
                    path_length * index as f64 / count.saturating_sub(1).max(1) as f64;
                let mut segment = 0;
                while segment + 1 < lengths.len() && remaining > lengths[segment] {
                    remaining -= lengths[segment];
                    segment += 1;
                }
                let a = options.path[segment];
                let b = options.path[segment + 1];
                let t = if lengths[segment] > 0.0 {
                    remaining / lengths[segment]
                } else {
                    0.0
                };
                x = (a.x + (b.x - a.x) * t) * width;
                y = (a.y + (b.y - a.y) * t) * height;
                rotation = ((b.y - a.y) * height)
                    .atan2((b.x - a.x) * width)
                    .to_degrees();
                scale = 1.0 - options.spacing * 0.5;
            }
            RecipeId::GridResponse => {
                scale = 0.32 + (0.68 * response).max(0.18);
                rotation = (1.0 - response) * 90.0 * options.amplitude;
            }
            RecipeId::SeededTexture => {
                x = (u + (random.unit() - 0.5) * 0.7 / columns as f64).clamp(0.0, 1.0) * width;
                y = (v + (random.unit() - 0.5) * 0.7 / rows as f64).clamp(0.0, 1.0) * height;
                scale = 0.28 + random.unit() * 0.72;
                rotation = random.unit() * 180.0;
            }
            _ => unreachable!("procedural recipe was validated"),
        }
        output.push(ProceduralPlacement {
            index: index as u16,
            x: geometry(x),
            y: geometry(y),
            scale: geometry(scale),
            rotation: geometry(rotation),
            response: geometry(response),
        });
    }
    Ok(output)
}
