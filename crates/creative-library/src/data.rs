use crate::*;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum DataUnit {
    Count,
    Seconds,
    Milliseconds,
    Percentage,
    Ratio,
    Bytes,
    Currency { code: String },
    Custom { label: String },
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DataOrigin {
    ObservedSource,
    UserProvided,
    SyntheticFixture,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DataRow {
    pub label: String,
    pub value: f64,
    pub lower: Option<f64>,
    pub upper: Option<f64>,
    pub comparison: Option<f64>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DataSeries {
    pub id: Uuid,
    pub title: String,
    pub source_title: String,
    pub source_uri: Option<String>,
    pub observed_at: String,
    pub method: String,
    pub origin: DataOrigin,
    pub unit: DataUnit,
    pub precision: u8,
    pub sample_size: Option<u64>,
    pub rows: Vec<DataRow>,
    pub content_sha256: String,
}
impl DataSeries {
    pub fn payload_digest(&self) -> Result<String> {
        canonical_digest(
            &serde_json::json!({"schema":"motionwright.data-series-values/1","unit":self.unit,"precision":self.precision,"rows":self.rows,"sample_size":self.sample_size}),
        )
    }
    pub fn synthetic(id: Uuid) -> Self {
        let mut data = Self {
            id,
            title: "Illustrative measured values".into(),
            source_title: "Explicit synthetic acceptance fixture".into(),
            source_uri: None,
            observed_at: "2026-01-01T00:00:00Z".into(),
            method: "Deterministic example values; not a product performance claim".into(),
            origin: DataOrigin::SyntheticFixture,
            unit: DataUnit::Count,
            precision: 0,
            sample_size: Some(4),
            rows: vec![
                DataRow {
                    label: "A".into(),
                    value: 18.0,
                    lower: Some(16.0),
                    upper: Some(20.0),
                    comparison: Some(12.0),
                },
                DataRow {
                    label: "B".into(),
                    value: 32.0,
                    lower: Some(28.0),
                    upper: Some(35.0),
                    comparison: Some(20.0),
                },
                DataRow {
                    label: "C".into(),
                    value: 24.0,
                    lower: Some(20.0),
                    upper: Some(26.0),
                    comparison: Some(19.0),
                },
                DataRow {
                    label: "D".into(),
                    value: 46.0,
                    lower: Some(41.0),
                    upper: Some(50.0),
                    comparison: Some(33.0),
                },
            ],
            content_sha256: String::new(),
        };
        data.content_sha256 = data.payload_digest().expect("finite fixed fixture");
        data
    }
    pub fn validate(&self) -> Result<()> {
        for (value, label, max) in [
            (&self.title, "Data title", 240),
            (&self.source_title, "Data source", 300),
            (&self.method, "Measurement method", 2000),
        ] {
            text(value, max, label)?;
        }
        check(
            chrono::DateTime::parse_from_rfc3339(&self.observed_at).is_ok(),
            "Data source requires an explicit RFC3339 observation time",
        )?;
        check(
            (1..=64).contains(&self.rows.len()) && self.precision <= 6,
            "Data series exceeds the renderable row or precision budget",
        )?;
        check(
            self.sample_size
                .is_none_or(|n| n > 0 && n <= 1_000_000_000_000),
            "Reported sample size is out of bounds",
        )?;
        if let Some(uri) = &self.source_uri {
            check(
                uri.starts_with("https://")
                    && uri.len() <= 2048
                    && !uri.chars().any(char::is_control)
                    && !uri.contains('@'),
                "Data provenance URL must be an inert HTTPS reference without credentials",
            )?;
        }
        match &self.unit {
            DataUnit::Currency { code } => check(
                code.len() == 3 && code.bytes().all(|b| b.is_ascii_uppercase()),
                "Currency requires an explicit three-letter code",
            )?,
            DataUnit::Custom { label } => text(
                label,
                32,
                "Custom data units require a short explicit label",
            )?,
            _ => {}
        }
        let mut labels = std::collections::BTreeSet::new();
        for row in &self.rows {
            text(&row.label, 80, "Data row label is invalid")?;
            check(labels.insert(&row.label), "Data labels must be unique")?;
            for value in [Some(row.value), row.lower, row.upper, row.comparison]
                .into_iter()
                .flatten()
            {
                check(
                    value.is_finite() && value.abs() <= 1e15,
                    "Data contains an unbounded, non-finite or unsupported magnitude",
                )?;
            }
            check(
                row.lower.is_some() == row.upper.is_some(),
                "Uncertainty needs both lower and upper bounds",
            )?;
            if let (Some(lower), Some(upper)) = (row.lower, row.upper) {
                check(
                    lower <= row.value && upper >= row.value,
                    "Uncertainty interval does not include the observed value",
                )?;
            }
            if matches!(self.unit, DataUnit::Percentage) {
                check(
                    (0.0..=100.0).contains(&row.value)
                        && row
                            .comparison
                            .is_none_or(|value| (0.0..=100.0).contains(&value)),
                    "Percentage values are already percentages; implicit ratio conversion is prohibited",
                )?;
            }
            if matches!(self.unit, DataUnit::Count | DataUnit::Bytes) {
                check(
                    row.value >= 0.0 && row.value.fract() == 0.0,
                    "Counts and bytes require nonnegative integral observations",
                )?;
            }
        }
        check(
            valid_sha(&self.content_sha256) && self.content_sha256 == self.payload_digest()?,
            "Data values changed without updating their source-bound content digest",
        )
    }
    pub fn domain(&self) -> (f64, f64) {
        let mut min = 0.0_f64;
        let mut max = 0.0_f64;
        for row in &self.rows {
            for value in [Some(row.value), row.lower, row.upper, row.comparison]
                .into_iter()
                .flatten()
            {
                min = min.min(value);
                max = max.max(value);
            }
        }
        if min == max {
            max = min + 1.0;
        }
        (min, max)
    }
    pub fn format_value(&self, value: f64, locale: Locale) -> String {
        let number = format!("{:.*}", usize::from(self.precision), value);
        let number = if matches!(locale, Locale::En) {
            number
        } else {
            number.replace('.', ",")
        };
        match &self.unit {
            DataUnit::Count => number,
            DataUnit::Seconds => format!("{number} s"),
            DataUnit::Milliseconds => format!("{number} ms"),
            DataUnit::Percentage => format!("{number}%"),
            DataUnit::Ratio => format!("{number} x"),
            DataUnit::Bytes => format!("{number} B"),
            DataUnit::Currency { code } => format!("{number} {code}"),
            DataUnit::Custom { label } => format!("{number} {label}"),
        }
    }
    pub fn source_caption(&self) -> String {
        format!(
            "{} / {} / {}{}",
            if self.origin == DataOrigin::SyntheticFixture {
                "SYNTHETIC EXAMPLE"
            } else {
                "SOURCE"
            },
            self.source_title,
            &self.observed_at[..10],
            self.sample_size
                .map_or(String::new(), |n| format!(" / N={n}"))
        )
    }
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct DataBinding {
    pub series_id: Uuid,
    pub values_sha256: String,
    pub component_id: Uuid,
    pub native_node_ids: Vec<Uuid>,
    pub visible_source_label: String,
    pub units: DataUnit,
}
