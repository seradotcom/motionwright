//! Closed source vocabulary for first-party, opaque rectangular clips.
//! This is deliberately not an HTML/JavaScript eval or generic project importer.
use schemars::JsonSchema;
pub use semwright_types::{Error, ErrorCode, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fmt::Write};

pub const SCHEMA: &str = "semwright.hyperframes.composition/1";
pub const COMPILER: &str = "semwright-hyperframes-rect-clips-v3";
pub const HYPERFRAMES_VERSION: &str = "0.8.181";
pub const HYPERFRAMES_COMMIT: &str = "ae02e5aceff4742e5d72c20f0859eb3e6b449035";
pub const NPM_METADATA_SHA256: &str =
    "d3f1dd614e50191bd08391108fb17e521aded5c9a9aa0f30947672f8667ea673";
pub const NPM_INTEGRITY: &str = "sha512-rBShZnCVW5xLe+6VViIv+SEAjYQ1Z9z8mt5M+XTCHoRj/eGRg5Z/yyvVNsWZZeuipoba5yhNfodOnwn94n4PPw==";
pub const MAX_SOURCE_BYTES: usize = 256 * 1024;
pub fn sha(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}
pub fn sha_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn slug(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
fn unsupported(message: &str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceBinding {
    pub project_resource: String,
    pub generation: String,
    pub revision: u64,
    pub deliverable_id: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Clip {
    pub id: String,
    pub start_frame: u32,
    pub end_frame_exclusive: u32,
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
    pub fill: String,
    pub opacity_milli: u16,
    pub z_index: i32,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Composition {
    pub schema: String,
    pub binding: SourceBinding,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub frame_count: u32,
    pub background: String,
    pub clips: Vec<Clip>,
}
fn color(value: &str) -> bool {
    value.len() == 7
        && value.starts_with('#')
        && value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
}
fn uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, b)| {
            if matches!(i, 8 | 13 | 18 | 23) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
}
impl Composition {
    pub fn validate(&self) -> Result<()> {
        if self.schema != SCHEMA
            || !self
                .binding
                .project_resource
                .strip_prefix("project:")
                .is_some_and(uuid)
            || !uuid(&self.binding.generation)
            || !uuid(&self.binding.deliverable_id)
            || self.binding.revision > 9_007_199_254_740_991
        {
            return Err(Error::invalid(
                "HyperFrames source identity is not canonical",
            ));
        }
        if self.fps != 30
            || !(16..=1920).contains(&self.width)
            || !(16..=1080).contains(&self.height)
            || self.width % 2 != 0
            || self.height % 2 != 0
            || self.frame_count == 0
            || self.frame_count > 300
            || self.frame_count % self.fps != 0
            || !color(&self.background)
        {
            return Err(unsupported(
                "HyperFrames profile requires even bounded SDR dimensions, 30 fps and whole-second clips up to ten seconds",
            ));
        }
        if self.clips.is_empty() || self.clips.len() > 32 {
            return Err(unsupported(
                "HyperFrames clip roster exceeds its closed profile",
            ));
        }
        let mut ids = BTreeSet::new();
        for clip in &self.clips {
            if !slug(&clip.id)
                || !ids.insert(&clip.id)
                || !color(&clip.fill)
                || clip.width == 0
                || clip.height == 0
                || clip
                    .x
                    .checked_add(clip.width)
                    .is_none_or(|n| n > self.width)
                || clip
                    .y
                    .checked_add(clip.height)
                    .is_none_or(|n| n > self.height)
                || clip.start_frame >= clip.end_frame_exclusive
                || clip.end_frame_exclusive > self.frame_count
                || clip.start_frame % self.fps != 0
                || clip.end_frame_exclusive % self.fps != 0
                || clip.opacity_milli > 1000
                || !(-1024..=1024).contains(&clip.z_index)
            {
                return Err(unsupported(
                    "HyperFrames clip would lose geometry, color, opacity, ordering or exact timing",
                ));
            }
        }
        Ok(())
    }
}
#[derive(Debug, Clone)]
pub struct CompiledSource {
    pub composition: Composition,
    pub source: Vec<u8>,
    pub html: Vec<u8>,
    pub source_sha256: String,
    pub html_sha256: String,
    pub fingerprint: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceInventory {
    pub fingerprint: String,
    pub source_sha256: String,
    pub source_bytes: u64,
    pub html_sha256: String,
    pub html_bytes: u64,
    pub compiler: String,
    pub hyperframes_version: String,
    pub hyperframes_commit: String,
    pub composition: Composition,
}
impl CompiledSource {
    pub fn inventory(&self) -> SourceInventory {
        SourceInventory {
            fingerprint: self.fingerprint.clone(),
            source_sha256: self.source_sha256.clone(),
            source_bytes: self.source.len() as u64,
            html_sha256: self.html_sha256.clone(),
            html_bytes: self.html.len() as u64,
            compiler: COMPILER.into(),
            hyperframes_version: HYPERFRAMES_VERSION.into(),
            hyperframes_commit: HYPERFRAMES_COMMIT.into(),
            composition: self.composition.clone(),
        }
    }
}
pub fn compile(composition: &Composition) -> Result<CompiledSource> {
    composition.validate()?;
    let mut source = serde_json::to_vec_pretty(composition)?;
    source.push(b'\n');
    if source.len() > MAX_SOURCE_BYTES {
        return Err(Error::invalid("HyperFrames source exceeds its byte budget"));
    }
    let mut html = String::new();
    // Every inserted value is a bounded integer or a validated identifier/color.
    // No source string is executable code, CSS syntax, markup, path or URL.
    write!(&mut html,"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"UTF-8\">\n<meta name=\"viewport\" content=\"width={}, height={}\">\n<style>\n*{{margin:0;padding:0;box-sizing:border-box}}\nhtml,body,#main{{width:{}px;height:{}px;overflow:hidden;background:{}}}\n#main{{position:relative;isolation:isolate}}\n.clip{{position:absolute}}\n</style></head><body>\n<div id=\"main\" data-composition-id=\"main\" data-no-timeline data-start=\"0\" data-duration=\"{}\" data-width=\"{}\" data-height=\"{}\" data-fps=\"30\">\n",
        composition.width,composition.height,composition.width,composition.height,composition.background,
        composition.frame_count/composition.fps,composition.width,composition.height).expect("String write");
    let mut clips = composition.clips.iter().enumerate().collect::<Vec<_>>();
    clips.sort_by_key(|(index, clip)| (clip.z_index, *index));
    for (track, (_, clip)) in clips.into_iter().enumerate() {
        write!(&mut html,"<div id=\"{}\" class=\"clip\" data-start=\"{}\" data-duration=\"{}\" data-track-index=\"{}\" style=\"left:{}px;top:{}px;width:{}px;height:{}px;background:{};opacity:{}.{:03};z-index:{}\"></div>\n",
            clip.id,clip.start_frame/composition.fps,(clip.end_frame_exclusive-clip.start_frame)/composition.fps,
            track,clip.x,clip.y,clip.width,clip.height,clip.fill,clip.opacity_milli/1000,clip.opacity_milli%1000,clip.z_index).expect("String write");
    }
    html.push_str("</div>\n</body></html>\n");
    let html = html.into_bytes();
    let source_sha256 = sha(&source);
    let html_sha256 = sha(&html);
    let fingerprint = sha(format!(
        "{COMPILER}\n{HYPERFRAMES_VERSION}\n{HYPERFRAMES_COMMIT}\n{source_sha256}\n{html_sha256}\n"
    )
    .as_bytes());
    Ok(CompiledSource {
        composition: composition.clone(),
        source,
        html,
        source_sha256,
        html_sha256,
        fingerprint,
    })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    pub fn fixture() -> Composition {
        Composition {
            schema: SCHEMA.into(),
            binding: SourceBinding {
                project_resource: "project:00000000-0000-4000-8000-000000000001".into(),
                generation: "00000000-0000-4000-8000-000000000002".into(),
                revision: 4,
                deliverable_id: "00000000-0000-4000-8000-000000000003".into(),
            },
            width: 640,
            height: 360,
            fps: 30,
            frame_count: 60,
            background: "#101820".into(),
            clips: vec![
                Clip {
                    id: "red-original".into(),
                    start_frame: 0,
                    end_frame_exclusive: 30,
                    x: 40,
                    y: 40,
                    width: 240,
                    height: 120,
                    fill: "#E53935".into(),
                    opacity_milli: 1000,
                    z_index: 0,
                },
                Clip {
                    id: "blue-original".into(),
                    start_frame: 30,
                    end_frame_exclusive: 60,
                    x: 320,
                    y: 180,
                    width: 240,
                    height: 120,
                    fill: "#1E88E5".into(),
                    opacity_milli: 1000,
                    z_index: 1,
                },
            ],
        }
    }
    #[test]
    fn native_html_is_deterministic_and_has_no_user_code_or_network_dependency() {
        let source = compile(&fixture()).unwrap();
        let again = compile(&fixture()).unwrap();
        assert_eq!(source.html, again.html);
        let html = String::from_utf8(source.html).unwrap();
        assert!(html.contains("data-duration=\"2\""));
        assert!(html.contains("data-composition-id=\"main\" data-no-timeline"));
        for forbidden in ["<script", "https:", "http:", "src=", "font-family", "url("] {
            assert!(!html.contains(forbidden));
        }
        assert!(html.contains("data-start=\"1\" data-duration=\"1\""));
    }
    #[test]
    fn signed_layer_order_is_preserved_inside_an_explicit_stacking_context() {
        let mut source = fixture();
        source.clips[0].z_index = -1024;
        source.clips[1].z_index = -1;
        let before = source.clone();
        let compiled = compile(&source).unwrap();
        let html = String::from_utf8(compiled.html).unwrap();
        assert!(html.contains("#main{position:relative;isolation:isolate}"));
        assert!(html.contains("z-index:-1024"));
        assert!(html.contains("z-index:-1"));
        assert!(
            html.find("id=\"red-original\"").unwrap() < html.find("id=\"blue-original\"").unwrap()
        );
        assert_eq!(source, before);
        assert_eq!(
            serde_json::from_slice::<Composition>(&compiled.source).unwrap(),
            before
        );
    }
    #[test]
    fn malformed_or_unrepresentable_source_never_produces_native_html() {
        let mut p = fixture();
        p.clips[0].fill = "#000000;display:none".into();
        assert!(compile(&p).is_err());
        let mut p = fixture();
        p.clips[0].start_frame = 1;
        assert!(compile(&p).is_err());
        let mut p = fixture();
        p.clips[0].width = u32::MAX;
        assert!(compile(&p).is_err());
        let mut p = fixture();
        p.clips[0].id = "\"><script>".into();
        assert!(compile(&p).is_err());
    }
    #[test]
    fn source_binding_and_edit_change_fingerprint_without_hiding_original_source() {
        let original = compile(&fixture()).unwrap();
        let mut edited = fixture();
        edited.binding.revision += 1;
        edited.clips[0].fill = "#00AA66".into();
        let revised = compile(&edited).unwrap();
        assert_ne!(original.fingerprint, revised.fingerprint);
        assert_ne!(original.html_sha256, revised.html_sha256);
        assert_eq!(
            serde_json::from_slice::<Composition>(&original.source).unwrap(),
            fixture()
        );
    }
}
