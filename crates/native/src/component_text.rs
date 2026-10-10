//! Fixed component containers keep typography outside the shot's automatic layout.
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
    let mut subjects = Vec::with_capacity(lines.len() + 1);
    let mut constraints = Vec::with_capacity(lines.len() * 3);
    let mut container = mapped.clone();
    container.content = SubjectContent::Group;
    container.role = "component-text-container".into();
    subjects.push(container);
    for (index, line) in lines.into_iter().enumerate() {
        let id = format!("{}-line-{index}", mapped.id);
        subjects.push(Subject {
            id: id.clone(),
            role: "component-text-line".into(),
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
                    y: -height / 2.0 + line_height * index as f64 + glyph_box / 2.0,
                },
                size: Size {
                    width,
                    height: glyph_box,
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
