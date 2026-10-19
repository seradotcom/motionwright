//! Fixed containers preserve authored typography outside the shot's automatic layout.
use motionwright_domain::CanvasNode;
use semwright_motion_authoring::{
    Point, Size, SpatialIntent, Subject, SubjectContent, TextRun, VisualConstraint,
};
use semwright_native_sdk::{Error, ErrorCode, Result};

pub(crate) fn fixed_component_text(
    node: &CanvasNode,
    mapped: Subject,
    scale: f64,
) -> Result<(Vec<Subject>, Vec<VisualConstraint>)> {
    let SubjectContent::Text { runs, .. } = &mapped.content else {
        return Err(Error::new(
            ErrorCode::InvalidArgument,
            "Component mapper received non-text content",
        ));
    };
    if runs.len() != 1 || node.parent_id.is_some() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Component text requires one run and an unparented source object",
        ));
    }
    let source = &runs[0];
    let lines = source.text.split('\n').collect::<Vec<_>>();
    if lines.len() > 8
        || lines
            .iter()
            .any(|line| line.is_empty() || line.contains('\r'))
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Component text requires one to eight nonempty authored lines",
        ));
    }
    let width = node.width * scale;
    let height = node.height * scale;
    let font_size =
        node.style.font_size.ok_or_else(|| {
            Error::new(ErrorCode::InvalidArgument, "Component font size is missing")
        })? * scale;
    let line_height = font_size * node.style.line_height.unwrap_or(1.12);
    // The provider's native Txt DOM needs its glyph ascender/descender box,
    // not the editorial baseline advance, as its clipping measurement bound.
    let glyph_box = font_size * 1.4;
    if !line_height.is_finite()
        || line_height <= 0.0
        || line_height * (lines.len() - 1) as f64 + glyph_box > height
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Authored lines exceed the text box; clipping and automatic font shrinking are not applied",
        ));
    }
    emit_fixed_text(
        &mapped,
        &lines,
        FixedTextBox {
            width,
            height,
            advance: line_height,
            leaf_height: glyph_box,
        },
        FixedTextFlavor::Component,
    )
}

#[derive(Clone, Copy)]
enum FixedTextFlavor {
    Component,
    Canvas,
}

#[derive(Clone, Copy)]
struct FixedTextBox {
    width: f64,
    height: f64,
    advance: f64,
    leaf_height: f64,
}

/// Preserve authored line advances using the existing fixed-container grammar.
/// This is a logical projection, not a measurement of the native font.
pub(crate) fn fixed_canvas_text(
    node: &CanvasNode,
    mapped: Subject,
    scale: f64,
    available_subjects: usize,
) -> Result<(Vec<Subject>, Vec<VisualConstraint>)> {
    let SubjectContent::Text { runs, .. } = &mapped.content else {
        return Err(Error::new(
            ErrorCode::InvalidArgument,
            "Canvas mapper received non-text content",
        ));
    };
    let [source] = runs.as_slice() else {
        return Err(Error::new(
            ErrorCode::InvalidArgument,
            "Canvas text requires one projected run",
        ));
    };
    if node.parent_id.is_some() {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Canvas multiline text inside an authored parent has no verified native layout",
        ));
    }
    if source.text.contains('\r') {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Canvas text contains unsupported carriage returns; no normalization was applied",
        ));
    }
    // Empty authored lines consume advance without creating an empty TextRun.
    // Preserve their indices so source-to-leaf identity is deterministic.
    let lines = source.text.split('\n').collect::<Vec<_>>();
    let leaf_count = lines.iter().filter(|line| !line.is_empty()).count();
    if leaf_count == 0 {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Canvas text requires at least one nonempty authored line",
        ));
    }
    if available_subjects == 0 || leaf_count > available_subjects - 1 {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Canvas text exceeds the remaining shot subject budget",
        ));
    }
    let width = node.width * scale;
    let height = node.height * scale;
    let font_size = node
        .style
        .font_size
        .ok_or_else(|| Error::new(ErrorCode::InvalidArgument, "Canvas font size is missing"))?
        * scale;
    let advance = font_size * node.style.line_height.unwrap_or(1.12);
    // This retained layout heuristic is not a measured glyph extent.
    let nominal_leaf_height = font_size * 1.4;
    if ![
        scale,
        width,
        height,
        font_size,
        advance,
        nominal_leaf_height,
    ]
    .iter()
    .all(|value| value.is_finite() && *value > 0.0)
    {
        return Err(Error::new(
            ErrorCode::InvalidArgument,
            "Canvas text has invalid projected dimensions or font geometry",
        ));
    }
    let logical_height = advance * lines.len() as f64;
    if !logical_height.is_finite() || logical_height > height {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Canvas authored lines exceed the text box; automatic font shrinking is not applied",
        ));
    }
    let last_visible_index = lines
        .iter()
        .rposition(|line| !line.is_empty())
        .expect("a nonempty authored line was required above");
    // Preserve the full existing native glyph box for every leaf. Silently
    // shrinking its height to fit would be unverified clipping, not fidelity.
    let required_height = advance * last_visible_index as f64 + nominal_leaf_height;
    if !required_height.is_finite() || required_height > height {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Canvas authored line glyph boxes exceed the text box; clipping is not applied",
        ));
    }
    let leaf_height = nominal_leaf_height;
    emit_fixed_text(
        &mapped,
        &lines,
        FixedTextBox {
            width,
            height,
            advance,
            leaf_height,
        },
        FixedTextFlavor::Canvas,
    )
}

fn emit_fixed_text(
    mapped: &Subject,
    lines: &[&str],
    text_box: FixedTextBox,
    flavor: FixedTextFlavor,
) -> Result<(Vec<Subject>, Vec<VisualConstraint>)> {
    let component = matches!(flavor, FixedTextFlavor::Component);
    let SubjectContent::Text {
        runs,
        style,
        direction,
        language,
        ..
    } = &mapped.content
    else {
        return Err(Error::new(
            ErrorCode::InvalidArgument,
            "Fixed text emitter received non-text content",
        ));
    };
    let [source] = runs.as_slice() else {
        return Err(Error::new(
            ErrorCode::InvalidArgument,
            "Fixed text emitter requires one projected run",
        ));
    };
    let leaf_count = lines.iter().filter(|line| !line.is_empty()).count();
    let mut subjects = Vec::with_capacity(1 + leaf_count);
    let mut constraints = Vec::with_capacity(3 * leaf_count);
    let mut container = mapped.clone();
    container.content = SubjectContent::Group;
    if component {
        container.role = "component-text-container".into();
    }
    subjects.push(container);
    for (index, line) in lines.iter().copied().enumerate() {
        if line.is_empty() {
            continue;
        }
        let id = format!("{}-line-{index}", mapped.id);
        subjects.push(Subject {
            id: id.clone(),
            role: if component {
                "component-text-line"
            } else {
                "canvas-text-line"
            }
            .into(),
            parent: Some(mapped.id.clone()),
            layer: mapped.layer.clone(),
            content: SubjectContent::Text {
                runs: vec![TextRun {
                    text: line.into(),
                    weight: source.weight,
                    color: source.color.clone(),
                    emphasis: source.emphasis,
                }],
                style: style.clone(),
                direction: *direction,
                language: language.clone(),
                wrap: false,
                truncate: false,
            },
            layout: SpatialIntent::Fixed {
                position: Point {
                    x: 0.0,
                    y: -text_box.height / 2.0
                        + text_box.advance * index as f64
                        + text_box.leaf_height / 2.0,
                },
                size: Size {
                    width: text_box.width,
                    height: text_box.leaf_height,
                },
            },
            initially_visible: true,
            clip_intentional: false,
        });
        constraints.extend([
            VisualConstraint::NativeText {
                subject: id.clone(),
            },
            VisualConstraint::FontLoaded {
                subject: id.clone(),
            },
            VisualConstraint::NoTruncation { subject: id },
        ]);
    }
    Ok((subjects, constraints))
}
