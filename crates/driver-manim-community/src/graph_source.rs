//! Graph is a closed typed source on the existing Manim Driver Host.
//! This module retains original bytes. It creates no execution authority.
use super::*;
use motionwright_manim_profile::{
    ManimSourceEntryV1,
    semantic_graph::ManimSemanticGraphPlan,
    semantic_graph_readback::inspect_native_graph_readback_v1,
    semantic_graph_source::{
        GRAPH_SOURCE_MIME_V1, GraphBuiltinAssetPin, PreparedGraphProgramV1,
        compile_graph_program_source_only, graph_builtin_asset_pins,
        graph_native_readback_paths_v1, graph_source_bundle_v1, graph_source_package_v1,
    },
};
use std::collections::BTreeSet;

pub(super) const START_COMMAND: &str = "driver.manim-community.graph.render.start";
const PROFILE_ID: &str = "mw-manim-semantic-graph-v1";
const MAX_READBACK_BYTES: usize = 32 * 1024 * 1024;
const MAX_INDEX_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct StartArgs {
    pub plan: ManimSemanticGraphPlan,
    pub profile: ManimRenderProfile,
    #[serde(
        default,
        deserialize_with = "deserialize_source_bundle",
        skip_serializing_if = "Option::is_none"
    )]
    pub source_bundle: Option<editable_source::SourceBundleInput>,
}

fn deserialize_source_bundle<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<editable_source::SourceBundleInput>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    editable_source::SourceBundleInput::deserialize(deserializer).map(Some)
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct SourceBundleReadback {
    pub input: editable_source::SourceBundleInput,
    pub source_sha256: String,
    pub plan_sha256: String,
    pub closure: ManimSourceClosureV1,
}

pub(super) struct VerifiedSourceBundle {
    pub observation: SourceBundleReadback,
    bytes: Vec<u8>,
    package: motionwright_manim_profile::semantic_graph_source::ManimGraphSourcePackageV1,
}

fn validate_original_graph_input(input: &editable_source::SourceBundleInput) -> Result<()> {
    let name = input.relative_path.as_str();
    if name.len() < 13
        || name.len() > 128
        || !name.as_bytes()[0].is_ascii_alphanumeric()
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        || !name.ends_with(".mwgraph.zip")
        || input.sha256.len() != 64
        || !input
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || input.bytes == 0
        || input.bytes > MAX_SOURCE_BUNDLE_BYTES_V2 as u64
    {
        return Err(invalid(
            "Original Graph input is outside the existing output-mount carrier bounds",
        ));
    }
    Ok(())
}

fn source_bundle_observation(
    input: &editable_source::SourceBundleInput,
    package: &motionwright_manim_profile::semantic_graph_source::ManimGraphSourcePackageV1,
) -> SourceBundleReadback {
    SourceBundleReadback {
        input: input.clone(),
        source_sha256: package.closure.source_sha256.clone(),
        plan_sha256: package.closure.plan_sha256.clone(),
        closure: package.closure.clone(),
    }
}

pub(super) fn read_original_input(
    output_root: &Path,
    input: &editable_source::SourceBundleInput,
    plan: &ManimSemanticGraphPlan,
) -> Result<VerifiedSourceBundle> {
    validate_original_graph_input(input)?;
    plan.validate().map_err(map_profile_error)?;
    let bytes = bounded_source_file(
        output_root,
        &input.relative_path,
        MAX_SOURCE_BUNDLE_BYTES_V2,
    )?;
    if bytes.len() as u64 != input.bytes || hex::encode(Sha256::digest(&bytes)) != input.sha256 {
        return Err(invalid(
            "Original Graph input changed from its retained byte descriptor",
        ));
    }
    let package =
        motionwright_manim_profile::semantic_graph_source::inspect_graph_source_bundle_v1(&bytes)
            .map_err(map_profile_error)?;
    if package.plan != *plan {
        return Err(invalid(
            "Original Graph input differs from the complete prepared canonical plan",
        ));
    }
    let observation = source_bundle_observation(input, &package);
    Ok(VerifiedSourceBundle {
        observation,
        bytes,
        package,
    })
}

pub(super) fn prepare_original_input(
    args: &StartArgs,
    output_root: &Path,
    program: &PreparedGraphProgramV1,
) -> Result<Option<VerifiedSourceBundle>> {
    let Some(input) = args.source_bundle.as_ref() else {
        return Ok(None);
    };
    let verified = read_original_input(output_root, input, &args.plan)?;
    let source = verified
        .package
        .entries
        .get("scene.py")
        .ok_or_else(|| invalid("Inspected Graph input has no canonical scene.py"))?;
    if source.as_slice() != program.python_source.as_bytes()
        || verified.package.closure.source_sha256 != program.source_sha256
        || verified.package.closure.plan_sha256 != program.plan_sha256
    {
        return Err(invalid(
            "Original Graph input differs from the prepared program or its closure",
        ));
    }
    Ok(Some(verified))
}

fn verify_original_input_readback(
    output_root: &Path,
    plan: &ManimSemanticGraphPlan,
    recorded: Option<&SourceBundleReadback>,
) -> Result<Option<VerifiedSourceBundle>> {
    let Some(recorded) = recorded else {
        return Ok(None);
    };
    let verified = read_original_input(output_root, &recorded.input, plan)?;
    if &verified.observation != recorded {
        return Err(invalid(
            "Original Graph input differs from its retained pre-render observation",
        ));
    }
    Ok(Some(verified))
}

pub(super) fn require_distinct_original_output(
    source_file: &str,
    input: Option<&VerifiedSourceBundle>,
) -> Result<()> {
    let output = format!("{}.mwgraph.zip", source_stem(source_file)?);
    if input.is_some_and(|input| {
        input
            .observation
            .input
            .relative_path
            .eq_ignore_ascii_case(&output)
    }) {
        return Err(invalid(
            "Original Graph input must have a separate name from the new job output",
        ));
    }
    Ok(())
}

fn require_native_writer_profile(profile: ManimRenderProfile) -> Result<()> {
    // These are the current, unchanged Native5 writer bounds. Refuse before
    // producing files or reserving a Host job; never resize the authored frame.
    if profile.width % 2 != 0
        || profile.height % 2 != 0
        || profile.width > 4096
        || profile.height > 4096
        || u64::from(profile.width) * u64::from(profile.height) > 8_847_360
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Graph reference frame is outside the original Native5 writer profile",
        ));
    }
    profile.validate().map_err(map_profile_error)
}

pub(super) fn prepare(args: &StartArgs) -> Result<PreparedGraphProgramV1> {
    require_native_writer_profile(args.profile)?;
    if args.plan.render_profile != args.profile {
        return Err(invalid("Graph plan and requested render profile differ"));
    }
    let program = compile_graph_program_source_only(&args.plan).map_err(map_profile_error)?;
    // Validate the finite required/allowed file roster before any native effect.
    graph_native_readback_paths_v1(&args.plan).map_err(map_profile_error)?;
    if program.native_execution_performed || program.runtime_sample_pts_verified {
        return Err(invalid(
            "Graph source compilation cannot assert native execution",
        ));
    }
    if program.python_source.is_empty() || program.python_source.len() > MAX_SOURCE_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Graph program exceeds the original Host source budget",
        ));
    }
    if program.source_sha256 != hex::encode(Sha256::digest(program.python_source.as_bytes()))
        || program.plan_sha256 != hex::encode(Sha256::digest(&program.plan_document))
    {
        return Err(invalid("Graph compiler source digests are inconsistent"));
    }
    Ok(program)
}

fn normalized_relative(value: &str) -> Result<()> {
    if value.is_empty()
        || !value.is_ascii()
        || value.contains('\\')
        || value
            .bytes()
            .any(|byte| !(byte.is_ascii_alphanumeric() || b"._-/".contains(&byte)))
        || value
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
        || Path::new(value)
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(invalid(
            "Graph custody path is not a normalized relative name",
        ));
    }
    Ok(())
}

fn source_stem(source_file: &str) -> Result<String> {
    let camera_name = camera_source_relative(source_file)?;
    let stem = camera_name
        .strip_suffix(".mwcam.zip")
        .ok_or_else(|| invalid("Graph source filename is not bound to its original job"))?;
    Ok(stem.to_owned())
}

/// The caller supplies only the sealed owner runtime root. Pins come from the
/// compiled profile; neither a request nor a source package can replace them.
fn read_pinned_assets(
    runtime_root: &Path,
    pins: &[GraphBuiltinAssetPin],
) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut assets = BTreeMap::new();
    let mut total = 0usize;
    for pin in pins {
        normalized_relative(pin.relative_path)?;
        if pin.size_bytes == 0
            || pin.size_bytes > MAX_SOURCE_BUNDLE_BYTES_V2 as u64
            || assets.contains_key(pin.relative_path)
        {
            return Err(invalid("Graph bundled asset roster or size is invalid"));
        }
        let relative = format!("graph-profile-v1/fonts/{}", pin.relative_path);
        let raw = bounded_source_file(runtime_root, &relative, pin.size_bytes as usize)?;
        if raw.len() as u64 != pin.size_bytes || hex::encode(Sha256::digest(&raw)) != pin.sha256 {
            return Err(invalid(
                "Graph bundled asset does not match its fixed byte pin",
            ));
        }
        total = total
            .checked_add(raw.len())
            .ok_or_else(|| invalid("Graph bundled asset budget overflow"))?;
        if total > MAX_SOURCE_BUNDLE_BYTES_V2 {
            return Err(invalid(
                "Graph bundled assets exceed the source package budget",
            ));
        }
        assets.insert(pin.relative_path.to_owned(), raw);
    }
    Ok(assets)
}

fn runtime_assets() -> Result<BTreeMap<String, Vec<u8>>> {
    let pins = graph_builtin_asset_pins();
    if pins.len() != 9 {
        return Err(invalid(
            "Graph requires the fixed seven fonts and two licenses",
        ));
    }
    read_pinned_assets(&canonical_root(RUNTIME_MOUNT)?, pins)
}

fn expected_with_assets(
    source_file: &str,
    plan: &ManimSemanticGraphPlan,
    plan_sha256: &str,
    input: Option<&VerifiedSourceBundle>,
    assets: &BTreeMap<String, Vec<u8>>,
) -> Result<(EditableSourceArtifact, Vec<u8>, Vec<u8>)> {
    require_distinct_original_output(source_file, input)?;
    let package = graph_source_package_v1(plan, assets).map_err(map_profile_error)?;
    if package.closure.plan_sha256 != plan_sha256 {
        return Err(invalid(
            "Graph source closure differs from the prepared plan digest",
        ));
    }
    let program = package
        .entries
        .get("scene.py")
        .ok_or_else(|| invalid("Canonical Graph package has no scene.py"))?
        .clone();
    let canonical_bundle = graph_source_bundle_v1(plan, assets).map_err(map_profile_error)?;
    let bundle = if let Some(input) = input {
        validate_original_graph_input(&input.observation.input)?;
        if input.package.plan != package.plan
            || input.package.closure != package.closure
            || input.package.entries != package.entries
            || input.observation != source_bundle_observation(&input.observation.input, &package)
            || input.bytes.len() as u64 != input.observation.input.bytes
            || hex::encode(Sha256::digest(&input.bytes)) != input.observation.input.sha256
            || input.bytes != canonical_bundle
        {
            return Err(invalid(
                "Original Graph source differs from the complete canonical runtime package",
            ));
        }
        // Canonical ZIP equality validates the input; custody retains its actual bytes.
        input.bytes.clone()
    } else {
        canonical_bundle
    };
    let artifact = EditableSourceArtifact {
        relative_path: format!("{}.mwgraph.zip", source_stem(source_file)?),
        sha256: hex::encode(Sha256::digest(&bundle)),
        bytes: bundle.len() as u64,
        media_type: GRAPH_SOURCE_MIME_V1,
        closure: package.closure,
    };
    Ok((artifact, bundle, program))
}

fn expected(
    source_file: &str,
    plan: &ManimSemanticGraphPlan,
    plan_sha256: &str,
    input: Option<&VerifiedSourceBundle>,
) -> Result<(EditableSourceArtifact, Vec<u8>, Vec<u8>)> {
    expected_with_assets(source_file, plan, plan_sha256, input, &runtime_assets()?)
}

fn write_original(root: &Path, relative: &str, raw: &[u8], maximum: usize) -> Result<()> {
    normalized_relative(relative)?;
    if raw.is_empty() || raw.len() > maximum {
        return Err(invalid("Graph original exceeds its retention budget"));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(root.join(relative))?;
    file.write_all(raw)?;
    file.sync_all()?;
    if bounded_source_file(root, relative, maximum)? != raw {
        return Err(invalid("Graph original changed during byte readback"));
    }
    Ok(())
}

pub(super) fn retain(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &ManimSemanticGraphPlan,
    plan_sha256: &str,
    input: Option<&VerifiedSourceBundle>,
) -> Result<EditableSourceArtifact> {
    let (artifact, bundle, program) = expected(source_file, plan, plan_sha256, input)?;
    if bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != program {
        return Err(invalid(
            "Graph program differs from its original typed source",
        ));
    }
    write_original(
        output_root,
        &artifact.relative_path,
        &bundle,
        MAX_SOURCE_BUNDLE_BYTES_V2,
    )?;
    Ok(artifact)
}

fn labels_directory(root: &Path, source_file: &str) -> Result<(String, PathBuf)> {
    let relative = format!("{}.graph-labels", source_stem(source_file)?);
    let path = root.join(&relative);
    let metadata = fs::symlink_metadata(&path)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(invalid(
            "Graph labels must be an original regular directory",
        ));
    }
    let canonical = fs::canonicalize(&path)?;
    if !canonical.starts_with(root) {
        return Err(invalid("Graph labels escaped their original work root"));
    }
    Ok((relative, canonical))
}

fn label_basename(relative: &str) -> Result<&str> {
    let basename = relative
        .strip_prefix("labels/")
        .ok_or_else(|| invalid("Graph readback role is unsupported"))?;
    normalized_relative(basename)?;
    if basename.contains('/') {
        return Err(invalid(
            "Graph label custody does not permit nested directories",
        ));
    }
    let four_digits =
        |value: &str| value.len() == 4 && value.bytes().all(|byte| byte.is_ascii_digit());
    let block = basename
        .strip_prefix("text_block_")
        .and_then(|value| value.strip_suffix(".json"))
        .is_some_and(four_digits);
    let label = basename
        .strip_prefix("label_")
        .and_then(|value| value.strip_suffix(".svg"))
        .is_some_and(four_digits);
    let source = basename
        .strip_prefix("source_")
        .and_then(|value| value.strip_suffix(".svg"))
        .is_some_and(|value| {
            value
                .split_once('_')
                .is_some_and(|(block, line)| four_digits(block) && four_digits(line))
        });
    if !(block || label || source) {
        return Err(invalid(
            "Graph label basename is outside the fixed readback grammar",
        ));
    }
    Ok(basename)
}

fn validate_readback_sets(required: &BTreeSet<String>, allowed: &BTreeSet<String>) -> Result<()> {
    if !required.contains("header.json")
        || !required.contains("frames.jsonl")
        || !required.is_subset(allowed)
    {
        return Err(invalid("Graph readback roster is inconsistent"));
    }
    for relative in allowed {
        match relative.as_str() {
            "header.json" | "frames.jsonl" => (),
            _ => {
                label_basename(relative)?;
            }
        }
    }
    Ok(())
}

/// Read observed files without parsing or trusting paths in the header.
/// Optional SVG presence is decided by the real native layout. The pure
/// inspector subsequently validates the exact set and all observed geometry.
fn read_originals(
    root: &Path,
    source_file: &str,
    required: &BTreeSet<String>,
    allowed: &BTreeSet<String>,
    maximum: usize,
) -> Result<BTreeMap<String, Vec<u8>>> {
    validate_readback_sets(required, allowed)?;
    let stem = source_stem(source_file)?;
    let (labels_relative, labels_path) = labels_directory(root, source_file)?;
    let mut observed = BTreeSet::from(["header.json".to_owned(), "frames.jsonl".to_owned()]);
    for entry in fs::read_dir(&labels_path)? {
        let entry = entry?;
        if observed.len() >= allowed.len() {
            return Err(invalid(
                "Graph readback contains more files than its fixed roster",
            ));
        }
        let kind = entry.file_type()?;
        if kind.is_symlink() || !kind.is_file() {
            return Err(invalid("Graph readback contains a symlink or non-file"));
        }
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("Graph readback filename is not UTF-8"))?;
        let key = format!("labels/{name}");
        label_basename(&key)?;
        if !allowed.contains(&key) || !observed.insert(key) {
            return Err(invalid(
                "Graph readback contains an unapproved or duplicate role",
            ));
        }
    }
    if !required.is_subset(&observed) || !observed.is_subset(allowed) {
        return Err(invalid(
            "Graph readback is missing an original required role",
        ));
    }
    let mut files = BTreeMap::new();
    let mut total = 0usize;
    for key in observed {
        let relative = match key.as_str() {
            "header.json" => format!("{stem}.graph.text.json"),
            "frames.jsonl" => format!("{stem}.graph.frames.jsonl"),
            _ => format!("{labels_relative}/{}", label_basename(&key)?),
        };
        let remaining = maximum
            .checked_sub(total)
            .ok_or_else(|| invalid("Graph original byte budget overflow"))?;
        let raw = bounded_source_file(root, &relative, remaining)?;
        total = total
            .checked_add(raw.len())
            .ok_or_else(|| invalid("Graph original byte budget overflow"))?;
        if total > maximum {
            return Err(invalid(
                "Graph original closure exceeds its aggregate byte budget",
            ));
        }
        files.insert(key, raw);
    }
    // Detect additions/removals or replacement by a link during the read.
    let (_, after_directory) = labels_directory(root, source_file)?;
    if after_directory != labels_path {
        return Err(invalid(
            "Graph label directory changed during original readback",
        ));
    }
    let mut after = BTreeSet::new();
    for entry in fs::read_dir(&after_directory)? {
        let entry = entry?;
        if after.len() >= allowed.len().saturating_sub(2) {
            return Err(invalid(
                "Graph label roster changed during original readback",
            ));
        }
        let kind = entry.file_type()?;
        if kind.is_symlink() || !kind.is_file() {
            return Err(invalid("Graph label type changed during original readback"));
        }
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("Graph readback filename is not UTF-8"))?;
        after.insert(format!("labels/{name}"));
    }
    let before: BTreeSet<_> = files
        .keys()
        .filter(|key| key.starts_with("labels/"))
        .cloned()
        .collect();
    if after != before {
        return Err(invalid(
            "Graph label roster changed during original readback",
        ));
    }
    Ok(files)
}

/// Create only fresh original files. No existing attempt is overwritten or
/// cleaned up, including after an inspector or final-index failure.
fn retain_originals(
    output_root: &Path,
    source_file: &str,
    files: &BTreeMap<String, Vec<u8>>,
) -> Result<String> {
    let directory = format!("{}.graph-readback", source_stem(source_file)?);
    let destination = output_root.join(&directory);
    fs::create_dir(&destination)?;
    fs::create_dir(destination.join("labels"))?;
    let mut total = 0usize;
    for (relative, raw) in files {
        match relative.as_str() {
            "header.json" | "frames.jsonl" => (),
            _ => {
                label_basename(relative)?;
            }
        }
        total = total
            .checked_add(raw.len())
            .ok_or_else(|| invalid("Graph original retention budget overflow"))?;
        if total > MAX_READBACK_BYTES {
            return Err(invalid("Graph originals exceed the retention budget"));
        }
        write_original(&destination, relative, raw, MAX_READBACK_BYTES)?;
    }
    Ok(directory)
}

pub(super) fn verify_completed(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &ManimSemanticGraphPlan,
    plan_sha256: &str,
    recorded: Option<&EditableSourceArtifact>,
    input: Option<&SourceBundleReadback>,
) -> Result<(EditableSourceArtifact, static_source::NativeGetterArtifact)> {
    let verified_input = verify_original_input_readback(output_root, plan, input)?;
    let (source, bundle, program) =
        expected(source_file, plan, plan_sha256, verified_input.as_ref())?;
    if recorded != Some(&source)
        || bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != program
        || bounded_source_file(
            output_root,
            &source.relative_path,
            MAX_SOURCE_BUNDLE_BYTES_V2,
        )? != bundle
    {
        return Err(invalid(
            "Graph source custody differs from the original native job",
        ));
    }
    let spec = graph_native_readback_paths_v1(plan).map_err(map_profile_error)?;
    let files = read_originals(
        work_root,
        source_file,
        &spec.required,
        &spec.allowed,
        MAX_READBACK_BYTES,
    )?;
    // Preserve every raw observed original before parsing. A malformed header,
    // SVG or frame record remains available, and cannot produce a final index.
    let directory = retain_originals(output_root, source_file, &files)?;
    let summary = inspect_native_graph_readback_v1(plan, &files).map_err(map_profile_error)?;
    if bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != program {
        return Err(invalid(
            "Graph program changed during native readback retention",
        ));
    }
    let entries: Vec<ManimSourceEntryV1> = files
        .iter()
        .map(|(path, raw)| ManimSourceEntryV1 {
            path: path.clone(),
            bytes: raw.len() as u64,
            sha256: hex::encode(Sha256::digest(raw)),
        })
        .collect();
    let mut index = serde_json::to_vec_pretty(&json!({
        "schema": "motionwright.manim-graph-original-readback/1",
        "profile_id": PROFILE_ID,
        "source_file": source_file,
        "plan_sha256": plan_sha256,
        "generated_source_sha256": source.closure.source_sha256,
        "original_directory": directory,
        "entries": entries,
        "inspector_summary": summary,
        "native_video_verified": false,
        "native_reopen_verified": false,
        "profile_admitted": false
    }))?;
    index.push(b'\n');
    let relative = format!("{directory}/index.json");
    write_original(output_root, &relative, &index, MAX_INDEX_BYTES)?;
    Ok((
        source,
        static_source::NativeGetterArtifact {
            relative_path: relative,
            sha256: hex::encode(Sha256::digest(&index)),
            bytes: index.len() as u64,
            media_type: "application/json",
        },
    ))
}

pub(super) fn start_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "properties": {
                    "schema_version": {"const": 1},
                    "profile_id": {"const": PROFILE_ID},
                    "execution_snapshot": {"type": "object"},
                    "scene_clock": {"type": "object"},
                    "render_profile": {"type": "object"},
                    "scene_matte": {"type": "object"},
                    "source": {"type": "object"}
                },
                "required": ["schema_version", "profile_id", "execution_snapshot",
                    "scene_clock", "render_profile", "scene_matte", "source"],
                "additionalProperties": false
            },
            "profile": {
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "minimum": 320, "maximum": 4096, "multipleOf": 2},
                    "height": {"type": "integer", "minimum": 240, "maximum": 4096, "multipleOf": 2},
                    "frame_rate": {"type": "integer", "minimum": 1, "maximum": 120}
                },
                "required": ["width", "height", "frame_rate"],
                "additionalProperties": false
            },
            "source_bundle": {
                "type": "object",
                "properties": {
                    "root": {"const": "output"},
                    "relative_path": {"type": "string", "minLength": 13, "maxLength": 128,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]*\\.mwgraph\\.zip$"},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "bytes": {"type": "integer", "minimum": 1, "maximum": MAX_SOURCE_BUNDLE_BYTES_V2}
                },
                "required": ["root", "relative_path", "sha256", "bytes"],
                "additionalProperties": false
            }
        },
        "required": ["plan", "profile"],
        "additionalProperties": false
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const SOURCE: &str = "mw_00000000000000000000000000000001.py";
    const ABC_SHA256: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

    fn pin(
        relative_path: &'static str,
        sha256: &'static str,
        size_bytes: u64,
    ) -> GraphBuiltinAssetPin {
        GraphBuiltinAssetPin {
            relative_path,
            sha256,
            size_bytes,
        }
    }

    // Production roots enter through canonical_root. Use that same path form
    // for custody fixtures, including the Windows extended-length prefix.
    fn canonical_tempdir() -> tempfile::TempDir {
        let parent = fs::canonicalize(std::env::temp_dir()).unwrap();
        let directory = tempfile::tempdir_in(parent).unwrap();
        assert_eq!(
            directory.path(),
            fs::canonicalize(directory.path()).unwrap()
        );
        directory
    }

    fn runtime() -> tempfile::TempDir {
        let temp = canonical_tempdir();
        let root = temp.path().join("graph-profile-v1/fonts");
        fs::create_dir_all(&root).unwrap();
        // Synthetic bytes test custody, not font rendering or profile admission.
        fs::write(root.join("fixture.ttf"), b"abc").unwrap();
        temp
    }

    fn roster(
        required_labels: &[&str],
        allowed_labels: &[&str],
    ) -> (BTreeSet<String>, BTreeSet<String>) {
        let base = BTreeSet::from(["header.json".into(), "frames.jsonl".into()]);
        let mut required = base.clone();
        let mut allowed = base;
        for name in required_labels {
            required.insert(format!("labels/{name}"));
        }
        for name in allowed_labels {
            allowed.insert(format!("labels/{name}"));
        }
        (required, allowed)
    }

    fn originals(labels: &[(&str, &[u8])]) -> tempfile::TempDir {
        let temp = canonical_tempdir();
        let stem = source_stem(SOURCE).unwrap();
        let directory = temp.path().join(format!("{stem}.graph-labels"));
        fs::create_dir(&directory).unwrap();
        fs::write(
            temp.path().join(format!("{stem}.graph.text.json")),
            b"{invalid\n",
        )
        .unwrap();
        fs::write(
            temp.path().join(format!("{stem}.graph.frames.jsonl")),
            b"{}\n",
        )
        .unwrap();
        for (name, bytes) in labels {
            fs::write(directory.join(name), bytes).unwrap();
        }
        temp
    }

    #[test]
    fn graph_capability_preserves_the_eight_existing_routes_and_context_boundary() {
        let capabilities = capability_catalog().unwrap();
        for name in [
            "driver.manim-community.doctor",
            "driver.manim-community.render.start",
            "driver.manim-community.static.render.start",
            "driver.manim-community.editable.render.start",
            "driver.manim-community.camera.render.start",
            "driver.manim-community.render.status",
            "driver.manim-community.render.cancel",
            "driver.manim-community.render.result",
            START_COMMAND,
        ] {
            assert_eq!(
                capabilities
                    .iter()
                    .filter(|c| c.descriptor.name == name)
                    .count(),
                1
            );
        }
        let graph = capabilities
            .iter()
            .find(|cap| cap.descriptor.name == START_COMMAND)
            .unwrap();
        assert_eq!(graph.descriptor.risk, Risk::MutatingReversible);
        assert_eq!(graph.descriptor.idempotency, Idempotency::NonIdempotent);
        assert_eq!(graph.descriptor.requires, vec![DRIVER_SCOPE]);
        assert!(!graph.descriptor.dry_run);
        assert_eq!(graph.descriptor.input_schema["additionalProperties"], false);
        let plan = &graph.descriptor.input_schema["properties"]["plan"];
        assert_eq!(plan["additionalProperties"], false);
        assert!(plan["properties"].get("python").is_none());
        assert!(plan["properties"].get("helper_path").is_none());
        assert!(plan["properties"].get("font_path").is_none());
    }

    #[test]
    fn native_writer_boundaries_preserve_exact_authored_geometry() {
        // 4096 * 2160 is exactly 8,847,360 native pixels. These literals are
        // independent of the validation expression and require no resize.
        for profile in [
            ManimRenderProfile {
                width: 4096,
                height: 2160,
                frame_rate: 120,
            },
            ManimRenderProfile {
                width: 2160,
                height: 4096,
                frame_rate: 1,
            },
            ManimRenderProfile {
                width: 320,
                height: 240,
                frame_rate: 30,
            },
        ] {
            let original = profile;
            require_native_writer_profile(profile).unwrap();
            assert_eq!(profile, original);
        }
    }

    #[test]
    fn native_writer_rejects_oversize_odd_and_incompatible_fps_before_any_io() {
        for profile in [
            ManimRenderProfile {
                width: 4098,
                height: 240,
                frame_rate: 30,
            },
            ManimRenderProfile {
                width: 320,
                height: 4098,
                frame_rate: 30,
            },
            ManimRenderProfile {
                width: 4096,
                height: 2162,
                frame_rate: 30,
            },
            ManimRenderProfile {
                width: 321,
                height: 240,
                frame_rate: 30,
            },
            ManimRenderProfile {
                width: 320,
                height: 241,
                frame_rate: 30,
            },
        ] {
            let original = profile;
            let error = require_native_writer_profile(profile).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
            assert_eq!(profile, original);
        }
        for fps in [0, 121] {
            assert!(
                require_native_writer_profile(ManimRenderProfile {
                    width: 1920,
                    height: 1080,
                    frame_rate: fps
                })
                .is_err()
            );
        }
    }

    #[test]
    fn relative_names_reject_traversal_absolute_and_platform_aliases() {
        for name in [
            "", "/x", "../x", "x/../y", "x//y", "./x", "x\\y", "C:/x", "a:\0",
        ] {
            assert!(normalized_relative(name).is_err(), "{name:?}");
        }
        normalized_relative("latin/Instrument-Sans.woff2").unwrap();
    }

    #[test]
    fn source_names_are_bound_to_the_original_native_token() {
        for name in [
            "x.py",
            "../mw_00000000000000000000000000000001.py",
            "mw_0000000000000000000000000000000A.py",
            "mw_1.py",
        ] {
            assert!(source_stem(name).is_err(), "{name}");
        }
        assert_eq!(
            source_stem(SOURCE).unwrap(),
            "mw_00000000000000000000000000000001"
        );
    }

    #[test]
    fn pinned_asset_bytes_are_exact_and_not_newline_normalized() {
        let temp = runtime();
        let assets = read_pinned_assets(temp.path(), &[pin("fixture.ttf", ABC_SHA256, 3)]).unwrap();
        assert_eq!(assets["fixture.ttf"], b"abc");
        assert!(!assets["fixture.ttf"].ends_with(b"\n"));
    }

    #[test]
    fn fixed_asset_size_and_hash_are_independent_requirements() {
        let temp = runtime();
        for bad in [
            pin("fixture.ttf", ABC_SHA256, 4),
            pin(
                "fixture.ttf",
                "0000000000000000000000000000000000000000000000000000000000000000",
                3,
            ),
            pin("fixture.ttf", ABC_SHA256, 0),
        ] {
            assert!(read_pinned_assets(temp.path(), &[bad]).is_err());
        }
        assert_eq!(
            fs::read(temp.path().join("graph-profile-v1/fonts/fixture.ttf")).unwrap(),
            b"abc"
        );
    }

    #[test]
    fn duplicate_missing_and_foreign_asset_paths_are_rejected_without_overwrite() {
        let temp = runtime();
        assert!(
            read_pinned_assets(
                temp.path(),
                &[
                    pin("fixture.ttf", ABC_SHA256, 3),
                    pin("fixture.ttf", ABC_SHA256, 3),
                ]
            )
            .is_err()
        );
        for name in [
            "missing.ttf",
            "../fixture.ttf",
            "/fixture.ttf",
            "a\\fixture.ttf",
        ] {
            assert!(read_pinned_assets(temp.path(), &[pin(name, ABC_SHA256, 3)]).is_err());
        }
        assert_eq!(
            fs::read(temp.path().join("graph-profile-v1/fonts/fixture.ttf")).unwrap(),
            b"abc"
        );
    }

    #[test]
    fn readback_requires_closed_header_frame_and_label_roles() {
        let (mut required, mut allowed) = roster(&[], &[]);
        validate_readback_sets(&required, &allowed).unwrap();
        required.remove("frames.jsonl");
        assert!(validate_readback_sets(&required, &allowed).is_err());
        required.insert("frames.jsonl".into());
        allowed.insert("labels/../escape".into());
        assert!(validate_readback_sets(&required, &allowed).is_err());
        for path in [
            "labels/a.svg",
            "labels/source_0000_0000_0000.svg",
            "labels/label_0000.svg/x",
            "labels/text_block_000A.json",
        ] {
            assert!(label_basename(path).is_err(), "{path}");
        }
    }

    #[test]
    fn original_reader_preserves_raw_malformed_text_without_qualifying_it() {
        let temp = originals(&[("text_block_0000.json", b"{also invalid")]);
        let (required, allowed) = roster(&["text_block_0000.json"], &["text_block_0000.json"]);
        let raw = read_originals(temp.path(), SOURCE, &required, &allowed, 1024).unwrap();
        assert_eq!(raw["header.json"], b"{invalid\n");
        assert_eq!(raw["labels/text_block_0000.json"], b"{also invalid");
        assert!(serde_json::from_slice::<Value>(&raw["header.json"]).is_err());
        assert!(!temp.path().join("index.json").exists());
    }

    #[test]
    fn optional_svg_presence_is_observed_and_never_invented() {
        let (required, allowed) = roster(
            &["text_block_0000.json"],
            &[
                "text_block_0000.json",
                "source_0000_0000.svg",
                "label_0000.svg",
            ],
        );
        let absent = originals(&[("text_block_0000.json", b"{}")]);
        let files = read_originals(absent.path(), SOURCE, &required, &allowed, 1024).unwrap();
        assert!(!files.contains_key("labels/source_0000_0000.svg"));
        let present = originals(&[
            ("text_block_0000.json", b"{}"),
            ("source_0000_0000.svg", b"<svg/>"),
            ("label_0000.svg", b"<svg/>"),
        ]);
        let files = read_originals(present.path(), SOURCE, &required, &allowed, 1024).unwrap();
        assert_eq!(files["labels/source_0000_0000.svg"], b"<svg/>");
    }

    #[test]
    fn missing_required_role_keeps_originals_and_fails_custody() {
        let temp = originals(&[]);
        let (required, allowed) = roster(&["text_block_0000.json"], &["text_block_0000.json"]);
        assert!(read_originals(temp.path(), SOURCE, &required, &allowed, 1024).is_err());
        assert_eq!(
            fs::read(
                temp.path()
                    .join("mw_00000000000000000000000000000001.graph.text.json")
            )
            .unwrap(),
            b"{invalid\n"
        );
    }

    #[test]
    fn extra_original_role_cannot_hide_inside_the_byte_budget() {
        let temp = originals(&[("label_9999.svg", b"<svg/>")]);
        let (required, allowed) = roster(&[], &["label_0000.svg"]);
        assert!(read_originals(temp.path(), SOURCE, &required, &allowed, 1024).is_err());
    }

    #[test]
    fn directories_are_not_accepted_as_observed_label_files() {
        let temp = originals(&[]);
        let stem = source_stem(SOURCE).unwrap();
        fs::create_dir(
            temp.path()
                .join(format!("{stem}.graph-labels/label_0000.svg")),
        )
        .unwrap();
        let (required, allowed) = roster(&[], &["label_0000.svg"]);
        assert!(read_originals(temp.path(), SOURCE, &required, &allowed, 1024).is_err());
    }

    #[test]
    fn aggregate_raw_budget_counts_header_frames_and_every_observed_label() {
        let temp = originals(&[("text_block_0000.json", b"{}")]);
        let (required, allowed) = roster(&["text_block_0000.json"], &["text_block_0000.json"]);
        // Original fixture: nine header bytes, three frame bytes, two label bytes.
        assert_eq!(b"{invalid\n".len(), 9);
        assert!(read_originals(temp.path(), SOURCE, &required, &allowed, 14).is_ok());
        assert!(read_originals(temp.path(), SOURCE, &required, &allowed, 13).is_err());
    }

    #[test]
    fn malformed_raw_copy_is_retained_but_never_creates_a_verified_index() {
        let temp = originals(&[("text_block_0000.json", b"{bad")]);
        let out = canonical_tempdir();
        let (required, allowed) = roster(&["text_block_0000.json"], &["text_block_0000.json"]);
        let files = read_originals(temp.path(), SOURCE, &required, &allowed, 1024).unwrap();
        let directory = retain_originals(out.path(), SOURCE, &files).unwrap();
        for (name, raw) in &files {
            assert_eq!(
                fs::read(out.path().join(&directory).join(name)).unwrap(),
                *raw
            );
        }
        assert!(!out.path().join(&directory).join("index.json").exists());
        assert!(
            temp.path()
                .join("mw_00000000000000000000000000000001.graph.text.json")
                .exists()
        );
    }

    #[test]
    fn an_existing_attempt_is_not_overwritten_or_removed() {
        let temp = originals(&[]);
        let out = canonical_tempdir();
        let (required, allowed) = roster(&[], &[]);
        let files = read_originals(temp.path(), SOURCE, &required, &allowed, 1024).unwrap();
        let directory = retain_originals(out.path(), SOURCE, &files).unwrap();
        let original = fs::read(out.path().join(&directory).join("header.json")).unwrap();
        assert!(retain_originals(out.path(), SOURCE, &files).is_err());
        assert_eq!(
            fs::read(out.path().join(&directory).join("header.json")).unwrap(),
            original
        );
    }

    #[test]
    fn exclusive_index_write_preserves_a_preexisting_file() {
        let out = canonical_tempdir();
        fs::write(out.path().join("index.json"), b"old-original").unwrap();
        assert!(write_original(out.path(), "index.json", b"new", 1024).is_err());
        assert_eq!(
            fs::read(out.path().join("index.json")).unwrap(),
            b"old-original"
        );
        assert!(write_original(out.path(), "other.json", b"four", 3).is_err());
        assert!(!out.path().join("other.json").exists());
    }

    fn graph_args() -> StartArgs {
        let plan: ManimSemanticGraphPlan = serde_json::from_str(include_str!(
            "../../manim-profile/testdata/semantic_graph_plan_v1.json"
        ))
        .unwrap();
        plan.validate().unwrap();
        StartArgs {
            profile: plan.render_profile,
            plan,
            source_bundle: None,
        }
    }

    #[test]
    fn trusted_prepare_preserves_the_complete_authored_plan_without_native_claims() {
        let args = graph_args();
        let original = serde_json::to_value(&args).unwrap();
        let prepared = prepare(&args).unwrap();
        assert_eq!(serde_json::to_value(&args).unwrap(), original);
        assert!(!prepared.native_execution_performed);
        assert!(!prepared.runtime_sample_pts_verified);
        assert_eq!(
            prepared.source_sha256,
            hex::encode(Sha256::digest(prepared.python_source.as_bytes()))
        );
        assert_eq!(
            prepared.plan_sha256,
            hex::encode(Sha256::digest(&prepared.plan_document))
        );
    }

    #[test]
    fn complete_prepare_refuses_unsupported_writer_before_compilation_and_keeps_source() {
        let valid = graph_args();
        for profile in [
            ManimRenderProfile {
                width: 4098,
                height: 240,
                frame_rate: 30,
            },
            ManimRenderProfile {
                width: 4096,
                height: 2162,
                frame_rate: 30,
            },
            ManimRenderProfile {
                width: 321,
                height: 240,
                frame_rate: 30,
            },
        ] {
            let args = StartArgs {
                plan: valid.plan.clone(),
                profile,
                source_bundle: None,
            };
            let original = serde_json::to_value(&args).unwrap();
            // The authored plan is still valid. Unsupported specifically comes
            // from the writer guard, before compiler/profile-mismatch handling.
            args.plan.validate().unwrap();
            let error = prepare(&args).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
            assert_eq!(serde_json::to_value(&args).unwrap(), original);
        }
    }

    #[tokio::test]
    async fn graph_without_host_context_never_dispatches_or_alters_existing_source() {
        let temp = canonical_tempdir();
        let original = temp.path().join("existing-source.py");
        fs::write(&original, b"retained source bytes").unwrap();
        let mut driver = ManimCommunityDriver {
            work_root: temp.path().to_owned(),
            output_root: temp.path().to_owned(),
            jobs: BTreeMap::new(),
            capabilities: capability_catalog().unwrap(),
        };
        let cap = driver
            .capabilities
            .iter()
            .find(|cap| cap.descriptor.name == START_COMMAND)
            .unwrap();
        let digest = descriptor_digest(&cap.descriptor).unwrap();
        let error = driver
            .execute(
                START_COMMAND,
                &digest,
                serde_json::to_value(graph_args()).unwrap(),
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(driver.jobs.is_empty());
        assert_eq!(fs::read(&original).unwrap(), b"retained source bytes");
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_runtime_assets_are_not_fixed_byte_evidence() {
        use std::os::unix::fs::symlink;
        let temp = runtime();
        let original = temp.path().join("graph-profile-v1/fonts/fixture.ttf");
        let linked = temp.path().join("graph-profile-v1/fonts/linked.ttf");
        symlink(&original, &linked).unwrap();
        assert!(read_pinned_assets(temp.path(), &[pin("linked.ttf", ABC_SHA256, 3)]).is_err());
        assert_eq!(fs::read(original).unwrap(), b"abc");
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_label_file_and_label_directory_are_refused() {
        use std::os::unix::fs::symlink;
        let temp = originals(&[]);
        let stem = source_stem(SOURCE).unwrap();
        let directory = temp.path().join(format!("{stem}.graph-labels"));
        let target = temp.path().join("foreign.svg");
        fs::write(&target, b"<svg/>").unwrap();
        symlink(&target, directory.join("label_0000.svg")).unwrap();
        let (required, allowed) = roster(&[], &["label_0000.svg"]);
        assert!(read_originals(temp.path(), SOURCE, &required, &allowed, 1024).is_err());

        let other = canonical_tempdir();
        symlink(
            &directory,
            other.path().join(format!("{stem}.graph-labels")),
        )
        .unwrap();
        assert!(labels_directory(other.path(), SOURCE).is_err());
        assert_eq!(fs::read(target).unwrap(), b"<svg/>");
    }
    fn graph_input_test_assets() -> BTreeMap<String, Vec<u8>> {
        let root =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../manim-profile/testdata/graph-fonts");
        graph_builtin_asset_pins()
            .iter()
            .map(|pin| {
                (
                    pin.relative_path.to_owned(),
                    fs::read(root.join(pin.relative_path)).unwrap(),
                )
            })
            .collect()
    }

    fn graph_input_test_descriptor(
        relative_path: &str,
        bytes: &[u8],
    ) -> editable_source::SourceBundleInput {
        editable_source::SourceBundleInput {
            root: editable_source::SourceBundleRoot::Output,
            relative_path: relative_path.to_owned(),
            sha256: hex::encode(Sha256::digest(bytes)),
            bytes: bytes.len() as u64,
        }
    }

    fn graph_input_test_write(root: &Path, name: &str, bytes: &[u8]) {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(root.join(name)).unwrap();
        file.write_all(bytes).unwrap();
        file.sync_all().unwrap();
    }

    struct GraphInputTestFixture {
        _directory: tempfile::TempDir,
        root: PathBuf,
        args: StartArgs,
        program: PreparedGraphProgramV1,
        assets: BTreeMap<String, Vec<u8>>,
        bytes: Vec<u8>,
    }

    impl GraphInputTestFixture {
        fn input(&self) -> &editable_source::SourceBundleInput {
            self.args.source_bundle.as_ref().unwrap()
        }
        fn input_path(&self) -> PathBuf {
            self.root.join(&self.input().relative_path)
        }
    }

    fn graph_input_test_fixture() -> GraphInputTestFixture {
        let mut args = graph_args();
        let program = prepare(&args).unwrap();
        let assets = graph_input_test_assets();
        let bytes = graph_source_bundle_v1(&args.plan, &assets).unwrap();
        let directory = canonical_tempdir();
        let root = directory.path().canonicalize().unwrap();
        let input = graph_input_test_descriptor("source-input.mwgraph.zip", &bytes);
        graph_input_test_write(&root, &input.relative_path, &bytes);
        args.source_bundle = Some(input);
        GraphInputTestFixture {
            _directory: directory,
            root,
            args,
            program,
            assets,
            bytes,
        }
    }

    #[test]
    fn graph_original_input_missing_is_legacy_and_null_is_rejected() {
        let args = graph_args();
        let mut value = json!({"plan": &args.plan, "profile": &args.profile});
        let absent: StartArgs = serde_json::from_value(value.clone()).unwrap();
        assert!(absent.source_bundle.is_none());
        assert_eq!(serde_json::to_value(&absent).unwrap(), value);
        let program = prepare(&absent).unwrap();
        assert!(
            prepare_original_input(&absent, Path::new("unused-original-input-root"), &program)
                .unwrap()
                .is_none()
        );
        assert!(
            verify_original_input_readback(
                Path::new("unused-original-input-root"),
                &absent.plan,
                None
            )
            .unwrap()
            .is_none()
        );
        value["source_bundle"] = Value::Null;
        assert!(serde_json::from_value::<StartArgs>(value).is_err());
    }

    #[test]
    fn graph_original_input_carrier_bounds_are_closed() {
        let base = graph_input_test_descriptor("a.mwgraph.zip", b"x");
        validate_original_graph_input(&base).unwrap();
        for name in [
            "",
            "../a.mwgraph.zip",
            "sub/a.mwgraph.zip",
            "sub\\a.mwgraph.zip",
            ".a.mwgraph.zip",
            "a.mwedit.zip",
            "a.MWGRAPH.ZIP",
            "/a.mwgraph.zip",
            "a\u{00e9}.mwgraph.zip",
        ] {
            let mut bad = base.clone();
            bad.relative_path = name.to_owned();
            assert!(validate_original_graph_input(&bad).is_err(), "{name:?}");
        }
        let mut bad = base.clone();
        bad.relative_path = format!("{}.mwgraph.zip", "a".repeat(129));
        assert!(validate_original_graph_input(&bad).is_err());
        for digest in ["AB".repeat(32), "gg".repeat(32), "ab".to_owned()] {
            let mut bad = base.clone();
            bad.sha256 = digest;
            assert!(validate_original_graph_input(&bad).is_err());
        }
        for size in [0, MAX_SOURCE_BUNDLE_BYTES_V2 as u64 + 1] {
            let mut bad = base.clone();
            bad.bytes = size;
            assert!(validate_original_graph_input(&bad).is_err());
        }
        let mut value = serde_json::to_value(&base).unwrap();
        value["root"] = json!("media");
        assert!(serde_json::from_value::<editable_source::SourceBundleInput>(value).is_err());
    }

    #[test]
    fn graph_original_input_matches_plan_program_and_closed_readback() {
        let f = graph_input_test_fixture();
        let verified = prepare_original_input(&f.args, &f.root, &f.program)
            .unwrap()
            .unwrap();
        assert_eq!(verified.bytes, f.bytes);
        assert_eq!(verified.package.plan, f.args.plan);
        assert_eq!(
            verified.package.entries["scene.py"].as_slice(),
            f.program.python_source.as_bytes()
        );
        assert_eq!(
            serde_json::to_value(&verified.observation).unwrap(),
            json!({
                "input": f.input(), "source_sha256": &verified.package.closure.source_sha256,
                "plan_sha256": &verified.package.closure.plan_sha256, "closure": &verified.package.closure,
            })
        );
        assert!(!f.program.native_execution_performed);
        assert!(!f.program.runtime_sample_pts_verified);
        for field in 0..3 {
            let mut changed = prepare(&f.args).unwrap();
            match field {
                0 => changed.source_sha256 = "ab".repeat(32),
                1 => changed.plan_sha256 = "ab".repeat(32),
                _ => changed.python_source.push_str("\n# changed\n"),
            }
            assert!(prepare_original_input(&f.args, &f.root, &changed).is_err());
        }
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
    }

    #[test]
    fn graph_original_input_rejects_false_hash_size_and_changed_bytes() {
        let f = graph_input_test_fixture();
        read_original_input(&f.root, f.input(), &f.args.plan).unwrap();
        let mut wrong = f.input().clone();
        wrong.bytes += 1;
        assert!(read_original_input(&f.root, &wrong, &f.args.plan).is_err());
        let mut wrong = f.input().clone();
        wrong.sha256 = "00".repeat(32);
        assert!(read_original_input(&f.root, &wrong, &f.args.plan).is_err());
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
        let mut changed = f.bytes.clone();
        changed[0] ^= 1;
        fs::write(f.input_path(), &changed).unwrap();
        assert!(read_original_input(&f.root, f.input(), &f.args.plan).is_err());
        assert_eq!(fs::read(f.input_path()).unwrap(), changed);
    }

    #[test]
    fn graph_original_input_rejects_each_execution_snapshot_change() {
        let f = graph_input_test_fixture();
        read_original_input(&f.root, f.input(), &f.args.plan).unwrap();
        for field in ["project_id", "generation", "branch_id", "revision"] {
            let mut value = serde_json::to_value(&f.args.plan).unwrap();
            let snapshot = value
                .get_mut("execution_snapshot")
                .and_then(Value::as_object_mut)
                .unwrap();
            if field == "revision" {
                let next = snapshot[field].as_u64().unwrap().checked_add(1).unwrap();
                snapshot.insert(field.to_owned(), json!(next));
            } else {
                let first = "22222222-2222-4222-8222-222222222222";
                let replacement = if snapshot[field].as_str() == Some(first) {
                    "33333333-3333-4333-8333-333333333333"
                } else {
                    first
                };
                snapshot.insert(field.to_owned(), json!(replacement));
            }
            let changed: ManimSemanticGraphPlan = serde_json::from_value(value).unwrap();
            assert!(
                read_original_input(&f.root, f.input(), &changed).is_err(),
                "{field}"
            );
        }
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
    }

    #[test]
    fn graph_original_input_rejects_non_graph_and_noncanonical_archives() {
        let f = graph_input_test_fixture();
        let mut appended = f.bytes.clone();
        appended.extend_from_slice(b"trailing bytes outside the canonical ZIP");
        for (index, bytes) in [b"not a Graph source archive".to_vec(), appended]
            .into_iter()
            .enumerate()
        {
            let name = format!("bad-{index}.mwgraph.zip");
            let input = graph_input_test_descriptor(&name, &bytes);
            graph_input_test_write(&f.root, &name, &bytes);
            assert!(read_original_input(&f.root, &input, &f.args.plan).is_err());
        }
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
    }

    #[test]
    fn graph_original_input_export_preserves_exact_original_bytes() {
        let f = graph_input_test_fixture();
        let verified = prepare_original_input(&f.args, &f.root, &f.program)
            .unwrap()
            .unwrap();
        let (artifact, bundle, program) = expected_with_assets(
            SOURCE,
            &f.args.plan,
            &f.program.plan_sha256,
            Some(&verified),
            &f.assets,
        )
        .unwrap();
        assert_eq!(bundle, f.bytes);
        assert_eq!(program, f.program.python_source.as_bytes());
        assert_eq!(artifact.sha256, f.input().sha256);
        assert_eq!(artifact.bytes, f.input().bytes);
        assert_eq!(artifact.closure, verified.package.closure);
        assert_eq!(
            artifact.relative_path,
            "mw_00000000000000000000000000000001.mwgraph.zip"
        );
        let (_, legacy_bundle, legacy_program) = expected_with_assets(
            SOURCE,
            &f.args.plan,
            &f.program.plan_sha256,
            None,
            &f.assets,
        )
        .unwrap();
        assert_eq!(legacy_program, program);
        assert_eq!(legacy_bundle, bundle);
        // Canonical ZIP encoding is unique; this is byte/legacy compatibility,
        // not a claim of distinguishing two identical encoding implementations.
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
    }

    #[test]
    fn graph_original_input_postread_refuses_changed_original() {
        let f = graph_input_test_fixture();
        let verified = read_original_input(&f.root, f.input(), &f.args.plan).unwrap();
        verify_original_input_readback(&f.root, &f.args.plan, Some(&verified.observation))
            .unwrap()
            .unwrap();
        let mut changed = f.bytes.clone();
        changed[0] ^= 1;
        fs::write(f.input_path(), &changed).unwrap();
        assert!(
            verify_original_input_readback(&f.root, &f.args.plan, Some(&verified.observation))
                .is_err()
        );
        assert_eq!(fs::read(f.input_path()).unwrap(), changed);
        assert_eq!(verified.bytes, f.bytes);
    }

    #[test]
    fn graph_original_input_postread_refuses_changed_recorded_proof() {
        let f = graph_input_test_fixture();
        let verified = read_original_input(&f.root, f.input(), &f.args.plan).unwrap();
        for field in 0..3 {
            let mut changed = verified.observation.clone();
            match field {
                0 => changed.source_sha256 = "ab".repeat(32),
                1 => changed.plan_sha256 = "ab".repeat(32),
                _ => changed.closure.source_sha256 = "ab".repeat(32),
            }
            assert!(verify_original_input_readback(&f.root, &f.args.plan, Some(&changed)).is_err());
        }
        verify_original_input_readback(&f.root, &f.args.plan, Some(&verified.observation))
            .unwrap()
            .unwrap();
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
    }

    #[test]
    fn graph_original_input_output_collision_is_refused_before_any_write() {
        let f = graph_input_test_fixture();
        let mut verified = read_original_input(&f.root, f.input(), &f.args.plan).unwrap();
        require_distinct_original_output(SOURCE, Some(&verified)).unwrap();
        for name in [
            "mw_00000000000000000000000000000001.mwgraph.zip",
            "MW_00000000000000000000000000000001.mwgraph.zip",
        ] {
            verified.observation.input.relative_path = name.to_owned();
            assert!(require_distinct_original_output(SOURCE, Some(&verified)).is_err());
            assert!(!f.root.join(name).exists());
        }
        assert_eq!(fs::read(f.input_path()).unwrap(), f.bytes);
    }
}
