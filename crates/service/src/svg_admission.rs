//! Decode XML and CSS before enforcing static SVG resource admission.
//!
//! Original source bytes remain untouched. Only predefined XML entities and
//! numeric references are resolved; document types are not supported.
use cssparser::{BasicParseErrorKind, ParseError, Parser, ParserInput, Token};
use quick_xml::{
    Reader, XmlVersion,
    events::{BytesDecl, BytesStart, Event},
};

type Admission = Result<(), &'static str>;

const MAX_XML_DEPTH: usize = 128;
const MAX_CSS_DEPTH: usize = 64;

fn xml_entity(name: &str) -> Option<&'static str> {
    match name {
        "lt" => Some("<"),
        "gt" => Some(">"),
        "amp" => Some("&"),
        "apos" => Some("'"),
        "quot" => Some("\""),
        _ => None,
    }
}

fn permitted_xml_char(ch: char, version: XmlVersion, literal: bool) -> bool {
    let n = ch as u32;
    if version == XmlVersion::Explicit1_1 {
        let allowed = matches!(n, 0x1..=0xD7FF | 0xE000..=0xFFFD | 0x10000..=0x10FFFF);
        let restricted_literal = matches!(
            n,
            0x1..=0x8 | 0xB..=0xC | 0xE..=0x1F | 0x7F..=0x84 | 0x86..=0x9F
        );
        allowed && (!literal || !restricted_literal)
    } else {
        matches!(
            n,
            0x9 | 0xA | 0xD | 0x20..=0xD7FF | 0xE000..=0xFFFD | 0x10000..=0x10FFFF
        )
    }
}

fn declaration_version(
    declaration: &BytesDecl<'_>,
    ascii_document: bool,
) -> Result<XmlVersion, &'static str> {
    let contents: &str = declaration.as_ref();
    let declaration_tag = BytesStart::from_content(contents, 3);
    let mut phase = 0;
    let mut version = None;
    for attribute in declaration_tag.attributes().with_checks(true) {
        let attribute = attribute.map_err(|_| "Invalid XML declaration.")?;
        let value: &str = attribute.value.as_ref();
        match attribute.key.as_ref() {
            "version" if phase == 0 => {
                version = Some(match value {
                    "1.0" => XmlVersion::Explicit1_0,
                    "1.1" => XmlVersion::Explicit1_1,
                    _ => return Err("Unsupported XML version."),
                });
                phase = 1;
            }
            "encoding" if phase == 1 => {
                if !value.eq_ignore_ascii_case("utf-8")
                    && !(ascii_document && value.eq_ignore_ascii_case("us-ascii"))
                {
                    return Err("XML encoding does not match UTF-8 input.");
                }
                phase = 2;
            }
            "standalone" if (phase == 1 || phase == 2) && matches!(value, "yes" | "no") => {
                phase = 3;
            }
            _ => return Err("Invalid XML declaration."),
        }
    }
    version.ok_or("XML declaration has no version.")
}

fn walk_css<'i>(
    parser: &mut Parser<'i, '_>,
    remaining_depth: usize,
) -> Result<(), ParseError<'i, &'static str>> {
    loop {
        // Release the parser's token borrow before recursing into a block.
        let token = match parser.next() {
            Ok(token) => token.clone(),
            Err(error) => {
                if matches!(&error.kind, BasicParseErrorKind::EndOfInput) {
                    return Ok(());
                }
                return Err(error.into());
            }
        };
        if token.is_parse_error() {
            return Err(parser.new_custom_error("Invalid CSS token."));
        }
        match token {
            Token::UnquotedUrl(_) => {
                return Err(parser.new_custom_error("CSS URL is forbidden."));
            }
            Token::Function(ref name)
                if ["url", "src", "image", "image-set", "-webkit-image-set"]
                    .iter()
                    .any(|blocked| name.eq_ignore_ascii_case(blocked)) =>
            {
                // Image constructors can accept a quoted resource without
                // producing a URL token. Resource admission remains static.
                return Err(parser.new_custom_error("CSS resource function is forbidden."));
            }
            Token::AtKeyword(ref name) if name.eq_ignore_ascii_case("import") => {
                return Err(parser.new_custom_error("CSS import is forbidden."));
            }
            Token::Function(_)
            | Token::ParenthesisBlock
            | Token::SquareBracketBlock
            | Token::CurlyBracketBlock => {
                if remaining_depth == 0 {
                    return Err(parser.new_custom_error("CSS nesting limit exceeded."));
                }
                parser.parse_nested_block(|nested| walk_css(nested, remaining_depth - 1))?;
            }
            _ => {}
        }
    }
}

fn check_css(css: &str) -> Admission {
    let mut input = ParserInput::new(css);
    let mut parser = Parser::new(&mut input);
    walk_css(&mut parser, MAX_CSS_DEPTH).map_err(|_| "SVG contains forbidden or unsupported CSS.")
}

fn css_attribute(local_name: &str) -> bool {
    // Inspect CSS-bearing SVG attributes, not IDs, labels, namespace URIs
    // or ordinary document text.
    const NAMES: &[&str] = &[
        "style",
        "alignment-baseline",
        "baseline-shift",
        "clip",
        "clip-path",
        "clip-rule",
        "color",
        "color-interpolation",
        "color-interpolation-filters",
        "color-profile",
        "color-rendering",
        "cursor",
        "direction",
        "display",
        "dominant-baseline",
        "enable-background",
        "fill",
        "fill-opacity",
        "fill-rule",
        "filter",
        "flood-color",
        "flood-opacity",
        "font-family",
        "font-size",
        "font-size-adjust",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "glyph-orientation-horizontal",
        "glyph-orientation-vertical",
        "image-rendering",
        "kerning",
        "letter-spacing",
        "lighting-color",
        "marker",
        "marker-start",
        "marker-mid",
        "marker-end",
        "mask",
        "opacity",
        "overflow",
        "paint-order",
        "pointer-events",
        "shape-rendering",
        "stop-color",
        "stop-opacity",
        "stroke",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "text-anchor",
        "text-decoration",
        "text-rendering",
        "transform",
        "transform-origin",
        "unicode-bidi",
        "vector-effect",
        "visibility",
        "word-spacing",
        "writing-mode",
    ];
    NAMES
        .iter()
        .any(|name| local_name.eq_ignore_ascii_case(name))
}

fn check_element(element: &BytesStart<'_>, version: XmlVersion) -> Result<bool, &'static str> {
    let local = element.local_name();
    let local = local.as_ref();
    if [
        "script",
        "foreignobject",
        "iframe",
        "object",
        "embed",
        "animate",
        "animatecolor",
        "animatemotion",
        "animatetransform",
        "set",
        "discard",
    ]
    .iter()
    .any(|name| local.eq_ignore_ascii_case(name))
    {
        // SMIL can replace an admitted href or CSS value after parsing.
        return Err("SVG contains a forbidden active element.");
    }
    for attribute in element.attributes().with_checks(true) {
        let attribute = attribute.map_err(|_| "Invalid XML attribute.")?;
        // Resolve only XML's five predefined entities regardless of features
        // enabled on quick-xml by other dependencies.
        let value = attribute
            .normalized_value_with(version, 1, xml_entity)
            .map_err(|_| "Invalid XML attribute value.")?;
        if !value
            .chars()
            .all(|ch| permitted_xml_char(ch, version, false))
        {
            return Err("Invalid XML character.");
        }
        if attribute.key.as_namespace_binding().is_some() {
            continue;
        }
        let local = attribute.key.local_name();
        let local = local.as_ref();
        let bytes = local.as_bytes();
        let event_handler = bytes.len() > 2
            && bytes[..2].eq_ignore_ascii_case(b"on")
            && bytes[2..]
                .iter()
                .all(|byte| byte.is_ascii_alphanumeric() || *byte == b'-');
        if event_handler {
            return Err("SVG event handlers are forbidden.");
        }
        if local.eq_ignore_ascii_case("href") && !value.is_empty() && !value.starts_with('#') {
            return Err("SVG external references are forbidden.");
        }
        if css_attribute(local) {
            check_css(value.as_ref())?;
        }
    }
    Ok(local.eq_ignore_ascii_case("style"))
}

fn append_style(style: &mut Option<String>, text: &str, byte_budget: usize) -> Admission {
    if let Some(css) = style.as_mut() {
        let new_length = css
            .len()
            .checked_add(text.len())
            .ok_or("SVG style size limit exceeded.")?;
        if new_length > byte_budget {
            return Err("SVG style size limit exceeded.");
        }
        css.push_str(text);
    }
    Ok(())
}

pub(crate) fn check_svg_xml_css(xml: &str, byte_budget: usize) -> Admission {
    if xml.len() > byte_budget {
        return Err("SVG size limit exceeded.");
    }
    let xml = xml.strip_prefix('\u{FEFF}').unwrap_or(xml);
    let mut reader = Reader::from_str(xml);
    {
        let config = reader.config_mut();
        config.enable_all_checks(true);
        config.allow_dangling_amp = false;
        config.allow_unmatched_ends = false;
        config.trim_text(false);
    }
    let mut version = XmlVersion::Implicit1_0;
    let mut depth = 0usize;
    let mut roots = 0usize;
    let mut seen_event = false;
    let mut style: Option<String> = None;
    loop {
        let event = reader.read_event().map_err(|_| "Invalid XML.")?;
        let first_event = !seen_event;
        seen_event = true;
        let opens_element = matches!(&event, Event::Start(_));
        match event {
            Event::Decl(declaration) => {
                if !first_event {
                    return Err("Misplaced XML declaration.");
                }
                version = declaration_version(&declaration, xml.is_ascii())?;
            }
            Event::Start(element) | Event::Empty(element) => {
                if style.is_some() {
                    return Err("Nested markup inside SVG style is unsupported.");
                }
                if depth >= MAX_XML_DEPTH {
                    return Err("XML nesting limit exceeded.");
                }
                if depth == 0 {
                    roots += 1;
                    if roots != 1 {
                        return Err("XML must contain one document element.");
                    }
                }
                let is_style = check_element(&element, version)?;
                if opens_element {
                    depth += 1;
                    if is_style {
                        style = Some(String::new());
                    }
                }
            }
            Event::End(_) => {
                if depth == 0 {
                    return Err("Unmatched XML closing element.");
                }
                // Nested elements are refused while collecting a style body.
                if let Some(css) = style.take() {
                    check_css(&css)?;
                }
                depth -= 1;
            }
            Event::Text(text) => {
                let text = if version == XmlVersion::Explicit1_1 {
                    text.xml11_content()
                } else {
                    text.xml10_content()
                };
                if depth == 0
                    && !text
                        .chars()
                        .all(|ch| matches!(ch, ' ' | '\t' | '\r' | '\n'))
                {
                    return Err("Text outside the XML document element.");
                }
                append_style(&mut style, text.as_ref(), byte_budget)?;
            }
            Event::CData(text) => {
                if depth == 0 {
                    return Err("CDATA outside the XML document element.");
                }
                let text = if version == XmlVersion::Explicit1_1 {
                    text.xml11_content()
                } else {
                    text.xml10_content()
                };
                // CDATA is already literal XML text, not entity-encoded text.
                append_style(&mut style, text.as_ref(), byte_budget)?;
            }
            Event::GeneralRef(reference) => {
                if depth == 0 {
                    return Err("Reference outside the XML document element.");
                }
                let character = match reference
                    .resolve_char_ref()
                    .map_err(|_| "Invalid XML character reference.")?
                {
                    Some(character) => character,
                    None => xml_entity(reference.as_ref())
                        .and_then(|value| value.chars().next())
                        .ok_or("Unsupported XML entity.")?,
                };
                if !permitted_xml_char(character, version, false) {
                    return Err("Invalid XML character reference.");
                }
                let mut buffer = [0u8; 4];
                append_style(&mut style, character.encode_utf8(&mut buffer), byte_budget)?;
            }
            Event::Comment(_) => {
                // Comments add no text and must not separate adjacent CSS
                // identifier fragments.
            }
            Event::PI(instruction) => {
                let content: &str = instruction.as_ref();
                let target = content.split_ascii_whitespace().next().unwrap_or("");
                if target.eq_ignore_ascii_case("xml-stylesheet") {
                    return Err("External XML stylesheets are forbidden.");
                }
                if style.is_some() {
                    return Err("Processing instructions inside style are unsupported.");
                }
            }
            Event::DocType(_) => return Err("XML document types are forbidden."),
            Event::Eof => {
                if roots != 1 || depth != 0 || style.is_some() {
                    return Err("Incomplete XML document.");
                }
                if !xml.chars().all(|ch| permitted_xml_char(ch, version, true)) {
                    return Err("Invalid literal XML character.");
                }
                return Ok(());
            }
        }
    }
}
