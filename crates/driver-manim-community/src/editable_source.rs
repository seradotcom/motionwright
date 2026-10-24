//! Closed editable V3 on the existing original DriverExecutionContext.
//! Font and source bytes are bounded data. An imported bundle is usable only
//! when its original program exactly equals the trusted canonical compiler.
use super::*;
use motionwright_manim_profile::{SOURCE_BUNDLE_MIME_V2, editable_scene as profile};

pub(super) const START_COMMAND: &str = "driver.manim-community.editable.render.start";
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct StartArgs {
    pub plan: profile::Plan,
    pub profile: ManimRenderProfile,
    #[serde(
        default,
        deserialize_with = "source_bundle_field",
        skip_serializing_if = "Option::is_none"
    )]
    pub source_bundle: Option<SourceBundleInput>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub(super) enum SourceBundleRoot {
    Output,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct SourceBundleInput {
    pub root: SourceBundleRoot,
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct SourceFontReadback {
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct SourceBundleReadback {
    pub input: SourceBundleInput,
    pub source_sha256: String,
    pub fontconfig_sha256: String,
    pub fonts: Vec<SourceFontReadback>,
}

/// Only constructed after the canonical ZIP/plan/font inspector succeeds.
/// Raw bytes live through this one start, never in the retained job map.
pub(super) struct VerifiedSourceBundle {
    pub observation: SourceBundleReadback,
    bytes: Vec<u8>,
    package: profile::Package,
}

fn source_bundle_field<'de, D>(
    deserializer: D,
) -> std::result::Result<Option<SourceBundleInput>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    // A missing optional field is the old route. An explicit null is malformed.
    SourceBundleInput::deserialize(deserializer).map(Some)
}

fn read_source_bundle(
    root: &Path,
    input: &SourceBundleInput,
    plan: &profile::Plan,
) -> Result<VerifiedSourceBundle> {
    let name = input.relative_path.as_str();
    if name.is_empty()
        || name.len() > 128
        || !name.as_bytes()[0].is_ascii_alphanumeric()
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        || !name.ends_with(".mwedit.zip")
        || input.sha256.len() != 64
        || !input
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || input.bytes == 0
        || input.bytes > profile::MAX_BUNDLE_BYTES as u64
    {
        return Err(invalid(
            "Editable source input identity is outside the closed output-mount contract",
        ));
    }
    // root is the driver's original output mount. This operation never writes,
    // unpacks, follows links or grants the package an executable/runtime path.
    let bytes = bounded_source_file(root, name, profile::MAX_BUNDLE_BYTES)?;
    if bytes.len() as u64 != input.bytes || hex::encode(Sha256::digest(&bytes)) != input.sha256 {
        return Err(invalid(
            "Editable source input bytes differ from the supplied identity",
        ));
    }
    let package = profile::inspect_source_bundle(&bytes).map_err(map_profile_error)?;
    if package.plan != *plan {
        return Err(invalid(
            "Editable source input plan differs from the exact render plan",
        ));
    }
    let source = package
        .entries
        .get("scene.py")
        .ok_or_else(|| invalid("Editable scene entry absent"))?;
    let config = package
        .entries
        .get("fonts.conf")
        .ok_or_else(|| invalid("Editable font configuration entry absent"))?;
    if config != profile::FONTCONFIG.as_bytes() {
        return Err(invalid(
            "Editable source font configuration is not the closed compiler configuration",
        ));
    }
    let fonts = plan
        .fonts
        .iter()
        .map(|font| {
            let data = package
                .entries
                .get(&font.archive_path())
                .ok_or_else(|| invalid("Editable source font entry absent"))?;
            Ok(SourceFontReadback {
                sha256: font.sha256.clone(),
                bytes: data.len() as u64,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let observation = SourceBundleReadback {
        input: input.clone(),
        source_sha256: hex::encode(Sha256::digest(source)),
        fontconfig_sha256: hex::encode(Sha256::digest(config)),
        fonts,
    };
    Ok(VerifiedSourceBundle {
        observation,
        bytes,
        package,
    })
}

pub(super) struct Prepared {
    pub source: String,
    pub plan_sha256: String,
    pub input_source: Option<VerifiedSourceBundle>,
}

pub(super) fn prepare(args: &StartArgs, output_root: Option<&Path>) -> Result<Prepared> {
    profile::validate_plan(&args.plan).map_err(map_profile_error)?;
    if args.plan.render_profile != args.profile {
        return Err(invalid("Editable plan and render profile differ"));
    }
    let generated = profile::compile(&args.plan).map_err(map_profile_error)?;
    let input_source = match &args.source_bundle {
        None => None,
        Some(input) => Some(read_source_bundle(
            output_root.ok_or_else(|| {
                invalid("Editable source input requires the original output mount")
            })?,
            input,
            &args.plan,
        )?),
    };
    let source = if let Some(input) = &input_source {
        let original = input
            .package
            .entries
            .get("scene.py")
            .ok_or_else(|| invalid("Editable source program entry absent"))?;
        if original != generated.as_bytes() {
            return Err(invalid(
                "Editable source program differs from the closed compiler",
            ));
        }
        std::str::from_utf8(original)
            .map_err(|_| invalid("Editable source program is not UTF-8"))?
            .to_owned()
    } else {
        generated
    };
    let mut plan = serde_json::to_vec_pretty(&args.plan)?;
    plan.push(b'\n');
    Ok(Prepared {
        source,
        plan_sha256: hex::encode(Sha256::digest(&plan)),
        input_source,
    })
}
fn relative(source_file: &str, suffix: &str) -> Result<String> {
    let name = camera_source_relative(source_file)?;
    let stem = name
        .strip_suffix(".mwcam.zip")
        .ok_or_else(|| invalid("Editable job source filename is invalid"))?;
    Ok(format!("{stem}.{suffix}"))
}
fn input_basename(font: &profile::FontSource) -> String {
    format!("mw_font_{}", font.basename())
}
fn assets_root(work_root: &Path, source_file: &str) -> Result<PathBuf> {
    let candidate = work_root.join(relative(source_file, "assets")?);
    let meta = fs::symlink_metadata(&candidate)?;
    if !meta.is_dir() || meta.file_type().is_symlink() {
        return Err(invalid("Editable assets must be a real job directory"));
    }
    let real = fs::canonicalize(candidate)?;
    if real.parent() != Some(work_root) {
        return Err(invalid("Editable assets escaped original work root"));
    }
    Ok(real)
}
fn readonly_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    let mut permissions = file.metadata()?.permissions();
    permissions.set_readonly(true);
    file.set_permissions(permissions)?;
    Ok(())
}
fn read_fonts(root: &Path, plan: &profile::Plan, input: bool) -> Result<BTreeMap<String, Vec<u8>>> {
    let mut fonts = BTreeMap::new();
    for font in &plan.fonts {
        let basename = if input {
            input_basename(font)
        } else {
            font.basename()
        };
        let bytes = bounded_source_file(root, &basename, profile::MAX_FONT_BYTES)?;
        fonts.insert(font.archive_path(), bytes);
    }
    profile::inspect_fonts(plan, &fonts).map_err(map_profile_error)?;
    Ok(fonts)
}
/// Runs once, before the original Host effect. The exclusive native job name
/// is never reused. Existing output font inputs must match actual OpenType bytes.
pub(super) fn stage_assets(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &profile::Plan,
    input: Option<&VerifiedSourceBundle>,
) -> Result<String> {
    let (fonts, config) = if let Some(input) = input {
        if input.package.plan != *plan {
            return Err(invalid("Editable source input changed before font staging"));
        }
        let fonts = plan
            .fonts
            .iter()
            .map(|font| {
                let bytes = input
                    .package
                    .entries
                    .get(&font.archive_path())
                    .ok_or_else(|| invalid("Editable source font entry absent"))?;
                Ok((font.archive_path(), bytes.clone()))
            })
            .collect::<Result<BTreeMap<_, _>>>()?;
        let config = input
            .package
            .entries
            .get("fonts.conf")
            .ok_or_else(|| invalid("Editable source configuration absent"))?
            .clone();
        profile::inspect_fonts(plan, &fonts).map_err(map_profile_error)?;
        if config != profile::FONTCONFIG.as_bytes() {
            return Err(invalid(
                "Editable source configuration changed before staging",
            ));
        }
        (fonts, config)
    } else {
        (
            read_fonts(output_root, plan, true)?,
            profile::FONTCONFIG.as_bytes().to_vec(),
        )
    };
    let relative = relative(source_file, "assets")?;
    fs::create_dir(work_root.join(&relative))?;
    let root = assets_root(work_root, source_file)?;
    for font in &plan.fonts {
        readonly_new(&root.join(font.basename()), &fonts[&font.archive_path()])?;
    }
    readonly_new(&root.join("fonts.conf"), &config)?;
    fs::create_dir(root.join("cache"))?;
    if read_fonts(&root, plan, false)? != fonts
        || bounded_source_file(&root, "fonts.conf", 4096)? != profile::FONTCONFIG.as_bytes()
    {
        return Err(invalid("Editable job font staging failed byte readback"));
    }
    Ok(relative)
}
fn expected(
    work_root: &Path,
    source_file: &str,
    plan: &profile::Plan,
    plan_sha256: &str,
) -> Result<(EditableSourceArtifact, Vec<u8>, Vec<u8>)> {
    let root = assets_root(work_root, source_file)?;
    if bounded_source_file(&root, "fonts.conf", 4096)? != profile::FONTCONFIG.as_bytes() {
        return Err(invalid("Editable font configuration changed"));
    }
    let fonts = read_fonts(&root, plan, false)?;
    let package = profile::source_package(plan, &fonts).map_err(map_profile_error)?;
    if package.closure.plan_sha256 != plan_sha256 {
        return Err(invalid("Editable source belongs to a different plan"));
    }
    let bundle = profile::source_bundle(plan, &fonts).map_err(map_profile_error)?;
    let program = package.entries["scene.py"].clone();
    let artifact = EditableSourceArtifact {
        relative_path: relative(source_file, "mwedit.zip")?,
        sha256: hex::encode(Sha256::digest(&bundle)),
        bytes: bundle.len() as u64,
        media_type: SOURCE_BUNDLE_MIME_V2,
        closure: package.closure,
    };
    Ok((artifact, bundle, program))
}
pub(super) fn retain(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &profile::Plan,
    plan_sha256: &str,
    input: Option<&VerifiedSourceBundle>,
) -> Result<EditableSourceArtifact> {
    let (artifact, bundle, program) = expected(work_root, source_file, plan, plan_sha256)?;
    let original_bundle = if let Some(input) = input {
        if input.bytes != bundle || input.package.entries.get("scene.py") != Some(&program) {
            return Err(invalid(
                "Native editable retention differs from the original admitted bundle",
            ));
        }
        input.bytes.as_slice()
    } else {
        bundle.as_slice()
    };
    if bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != program {
        return Err(invalid(
            "Editable native program differs from trusted compiler",
        ));
    }
    readonly_new(&output_root.join(&artifact.relative_path), original_bundle)?;
    if bounded_source_file(
        output_root,
        &artifact.relative_path,
        profile::MAX_BUNDLE_BYTES,
    )? != bundle
    {
        return Err(invalid("Editable source carrier failed byte readback"));
    }
    Ok(artifact)
}
pub(super) fn verify_completed(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &profile::Plan,
    plan_sha256: &str,
    recorded: Option<&EditableSourceArtifact>,
) -> Result<(EditableSourceArtifact, static_source::NativeGetterArtifact)> {
    let (artifact, bundle, program) = expected(work_root, source_file, plan, plan_sha256)?;
    if recorded != Some(&artifact)
        || bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != program
        || bounded_source_file(
            output_root,
            &artifact.relative_path,
            profile::MAX_BUNDLE_BYTES,
        )? != bundle
    {
        return Err(invalid("Editable job source custody changed"));
    }
    let name = relative(source_file, "objects.json")?;
    let bytes = bounded_source_file(work_root, &name, profile::MAX_NATIVE_READBACK_BYTES)?;
    let observation = profile::inspect_native_readback(plan, &bytes).map_err(map_profile_error)?;
    for (native, font) in observation.fonts.iter().zip(&plan.fonts) {
        if native.bytes
            != bounded_source_file(
                &assets_root(work_root, source_file)?,
                &font.basename(),
                profile::MAX_FONT_BYTES,
            )?
            .len() as u64
        {
            return Err(invalid("Observed font bytes differ from retained source"));
        }
    }
    readonly_new(&output_root.join(&name), &bytes)?;
    if bounded_source_file(output_root, &name, profile::MAX_NATIVE_READBACK_BYTES)? != bytes {
        return Err(invalid("Editable native getters changed during retention"));
    }
    let getters = static_source::NativeGetterArtifact {
        relative_path: name,
        sha256: hex::encode(Sha256::digest(&bytes)),
        bytes: bytes.len() as u64,
        media_type: "application/json",
    };
    Ok((artifact, getters))
}
pub(super) fn start_schema() -> Value {
    json!({"type":"object","properties":{
        "plan":{"type":"object","properties":{
            "schema_version":{"const":3},"profile_id":{"const":"mw-manim-editable-text-v3"},
            "output_role":{"const":"scene_video_contribution_without_audio_or_captions"},
            "binding":{"type":"object"},"clock":{"type":"object"},"render_profile":{"type":"object"},
            "canvas":{"type":"object"},"framing":{"enum":["crop","replan"]},"safe_margin":{"type":"number","minimum":0,"maximum":0.25},
            "background":{"type":"object"},"fonts":{"type":"array","maxItems":2,"items":{"type":"object"}},
            "nodes":{"type":"array","minItems":1,"maxItems":128,"items":{"type":"object"}}},
            "required":["schema_version","profile_id","output_role","binding","clock","render_profile","canvas","framing","safe_margin","background","fonts","nodes"],"additionalProperties":false},
        "profile":{"type":"object","properties":{"width":{"type":"integer","minimum":320,"maximum":7680},
            "height":{"type":"integer","minimum":240,"maximum":4320},"frame_rate":{"type":"integer","minimum":1,"maximum":120}},
            "required":["width","height","frame_rate"],"additionalProperties":false},
        "source_bundle":{"type":"object","properties":{
            "root":{"const":"output"},
            "relative_path":{"type":"string","minLength":12,"maxLength":128,
                "pattern":"^[A-Za-z0-9][A-Za-z0-9._-]*\\.mwedit\\.zip$"},
            "sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"},
            "bytes":{"type":"integer","minimum":1,"maximum":profile::MAX_BUNDLE_BYTES}},
            "required":["root","relative_path","sha256","bytes"],"additionalProperties":false}},
        "required":["plan","profile"],"additionalProperties":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    const FONT: &[u8] =
        include_bytes!("../../manim-profile/tests/fixtures/Montserrat-variable.ttf");
    const SOURCE: &str = "mw_0123456789abcdef0123456789abcdef.py";
    fn args() -> StartArgs {
        serde_json::from_value(json!({"profile":{"width":960,"height":540,"frame_rate":30},"plan":{
            "schema_version":3,"profile_id":"mw-manim-editable-text-v3","output_role":"scene_video_contribution_without_audio_or_captions",
            "binding":{"project_id":Uuid::from_u128(1),"generation":Uuid::from_u128(2),"revision":7,"scene_id":Uuid::from_u128(3),"deliverable_id":Uuid::from_u128(4)},
            "clock":{"timeline_start":{"num":"0","den":"1"},"duration":{"num":"4","den":"1"},"frame_rate":{"num":"30","den":"1"},
                "project_start_frame":0,"project_end_frame_exclusive":120,"local_start_frame":0,"frame_count":120},
            "render_profile":{"width":960,"height":540,"frame_rate":30},"canvas":{"width":1920,"height":1080,"coordinate_space":"project_pixels"},
            "framing":"replan","safe_margin":0.05,"background":{"kind":"solid","color":"#000000","opacity":1},
            "fonts":[{"asset_id":Uuid::from_u128(5),"family":"Montserrat","sha256":hex::encode(Sha256::digest(FONT)),"format":"ttf"}],
            "nodes":[{"node_id":Uuid::from_u128(6),"display_name":"Native font text","kind":"text","canonical_ordinal":0,"z_index":0,
                "x_px":660.0,"y_px":390.0,"width_px":600.0,"height_px":300.0,"rotation_deg":0.0,"opacity":1.0,"fill_hex":"#FFFFFF",
                "typography":{"text":"Native text","font_asset_id":Uuid::from_u128(5),"font_size_px":64.0,"weight":700,"line_height":1.2}}]
        }})).unwrap()
    }
    #[test]
    fn editable_capability_is_separate_closed_nonidempotent_on_the_original_host() {
        let caps = capability_catalog().unwrap();
        let cap = caps
            .iter()
            .find(|row| row.descriptor.name == START_COMMAND)
            .unwrap();
        assert_eq!(cap.descriptor.idempotency, Idempotency::NonIdempotent);
        assert_eq!(cap.descriptor.risk, Risk::MutatingReversible);
        assert_eq!(cap.descriptor.requires, vec![DRIVER_SCOPE]);
        assert!(!cap.descriptor.dry_run);
        assert!(
            caps.iter()
                .any(|row| row.descriptor.name == static_source::START_COMMAND)
        );
        assert!(
            caps.iter()
                .any(|row| row.descriptor.name == "driver.manim-community.camera.render.start")
        );
        let original = args();
        assert!(prepare(&original, None).is_ok());
        let mut foreign = original.clone();
        foreign.profile.height = 720;
        assert!(prepare(&foreign, None).is_err());
        let mut value = serde_json::to_value(original).unwrap();
        value["plan"]["python"] = json!("print('untrusted')");
        assert!(serde_json::from_value::<StartArgs>(value).is_err());
    }
    #[test]
    fn font_bytes_are_verified_before_exclusive_job_staging_and_never_overwritten() {
        let temp = tempfile::tempdir().unwrap();
        let work = temp.path().join("work");
        let output = temp.path().join("output");
        fs::create_dir(&work).unwrap();
        fs::create_dir(&output).unwrap();
        let work = fs::canonicalize(work).unwrap();
        let output = fs::canonicalize(output).unwrap();
        let args = args();
        let font = &args.plan.fonts[0];
        let input = output.join(input_basename(font));
        fs::write(&input, b"foreign font bytes").unwrap();
        assert!(stage_assets(&work, &output, SOURCE, &args.plan, None).is_err());
        assert!(
            !work
                .join("mw_0123456789abcdef0123456789abcdef.assets")
                .exists()
        );
        fs::write(&input, FONT).unwrap();
        let relative = stage_assets(&work, &output, SOURCE, &args.plan, None).unwrap();
        assert_eq!(relative, "mw_0123456789abcdef0123456789abcdef.assets");
        assert_eq!(
            bounded_source_file(
                &work.join(&relative),
                &font.basename(),
                profile::MAX_FONT_BYTES
            )
            .unwrap(),
            FONT
        );
        assert_eq!(
            bounded_source_file(&work.join(&relative), "fonts.conf", 4096).unwrap(),
            profile::FONTCONFIG.as_bytes()
        );
        assert!(stage_assets(&work, &output, SOURCE, &args.plan, None).is_err());
    }
    #[test]
    fn source_carrier_retains_font_bytes_and_refuses_a_changed_generated_program() {
        let temp = tempfile::tempdir().unwrap();
        let work = temp.path().join("work");
        let output = temp.path().join("output");
        fs::create_dir(&work).unwrap();
        fs::create_dir(&output).unwrap();
        let work = fs::canonicalize(work).unwrap();
        let output = fs::canonicalize(output).unwrap();
        let args = args();
        fs::write(output.join(input_basename(&args.plan.fonts[0])), FONT).unwrap();
        stage_assets(&work, &output, SOURCE, &args.plan, None).unwrap();
        let prepared = prepare(&args, None).unwrap();
        fs::write(work.join(SOURCE), prepared.source.as_bytes()).unwrap();
        let artifact = retain(
            &work,
            &output,
            SOURCE,
            &args.plan,
            &prepared.plan_sha256,
            None,
        )
        .unwrap();
        let bytes =
            bounded_source_file(&output, &artifact.relative_path, profile::MAX_BUNDLE_BYTES)
                .unwrap();
        let read = profile::inspect_source_bundle(&bytes).unwrap();
        assert_eq!(read.plan, args.plan);
        assert_eq!(read.entries[&args.plan.fonts[0].archive_path()], FONT);
        fs::write(work.join(SOURCE), b"print('not the closed compiler')").unwrap();
        assert!(
            retain(
                &work,
                &output,
                SOURCE,
                &args.plan,
                &prepared.plan_sha256,
                None
            )
            .is_err()
        );
        assert_eq!(
            bounded_source_file(&output, &artifact.relative_path, profile::MAX_BUNDLE_BYTES)
                .unwrap(),
            bytes
        );
    }

    fn canonical_input(output: &Path, name: &str) -> (StartArgs, Vec<u8>) {
        let mut value = args();
        let fonts = BTreeMap::from([(value.plan.fonts[0].archive_path(), FONT.to_vec())]);
        let bytes = profile::source_bundle(&value.plan, &fonts).unwrap();
        fs::write(output.join(name), &bytes).unwrap();
        value.source_bundle = Some(SourceBundleInput {
            root: SourceBundleRoot::Output,
            relative_path: name.into(),
            sha256: hex::encode(Sha256::digest(&bytes)),
            bytes: bytes.len() as u64,
        });
        (value, bytes)
    }

    #[test]
    fn optional_bundle_keeps_old_arguments_but_rejects_null_or_open_authority() {
        let original = args();
        assert!(original.source_bundle.is_none());
        assert!(prepare(&original, None).is_ok());
        let schema = start_schema();
        assert_eq!(schema["required"], json!(["plan", "profile"]));
        assert_eq!(
            schema["properties"]["source_bundle"]["additionalProperties"],
            false
        );
        assert_eq!(
            schema["properties"]["source_bundle"]["properties"]["root"],
            json!({"const":"output"})
        );
        let clean = serde_json::to_value(&original).unwrap();
        assert!(clean.get("source_bundle").is_none());
        for bad in [
            Value::Null,
            json!({"root":"work","relative_path":"a.mwedit.zip","sha256":"a".repeat(64),"bytes":1}),
            json!({"root":"output","relative_path":"a.mwedit.zip","sha256":"a".repeat(64),"bytes":1,"python":"print(1)"}),
        ] {
            let mut value = clean.clone();
            value["source_bundle"] = bad;
            assert!(serde_json::from_value::<StartArgs>(value).is_err());
        }
    }

    #[test]
    fn original_bundle_supplies_program_fonts_and_config_without_legacy_font_inputs() {
        let temporary = tempfile::tempdir().unwrap();
        let work = temporary.path().join("work");
        let output = temporary.path().join("output");
        fs::create_dir(&work).unwrap();
        fs::create_dir(&output).unwrap();
        let work = fs::canonicalize(work).unwrap();
        let output = fs::canonicalize(output).unwrap();
        let (args, original) = canonical_input(&output, "exported.mwedit.zip");
        assert!(!output.join(input_basename(&args.plan.fonts[0])).exists());
        let prepared = prepare(&args, Some(&output)).unwrap();
        let admitted = prepared.input_source.as_ref().unwrap();
        let inspected = profile::inspect_source_bundle(&original).unwrap();
        assert_eq!(prepared.source.as_bytes(), inspected.entries["scene.py"]);
        assert_eq!(
            admitted.observation.input,
            args.source_bundle.clone().unwrap()
        );
        assert_eq!(
            admitted.observation.source_sha256,
            hex::encode(Sha256::digest(&inspected.entries["scene.py"]))
        );
        assert_eq!(
            admitted.observation.fontconfig_sha256,
            hex::encode(Sha256::digest(profile::FONTCONFIG.as_bytes()))
        );
        assert_eq!(
            admitted.observation.fonts,
            vec![SourceFontReadback {
                sha256: args.plan.fonts[0].sha256.clone(),
                bytes: FONT.len() as u64,
            }]
        );
        fs::write(work.join(SOURCE), prepared.source.as_bytes()).unwrap();
        let relative = stage_assets(&work, &output, SOURCE, &args.plan, Some(admitted)).unwrap();
        assert_eq!(
            bounded_source_file(
                &work.join(&relative),
                &args.plan.fonts[0].basename(),
                profile::MAX_FONT_BYTES
            )
            .unwrap(),
            FONT
        );
        assert_eq!(
            bounded_source_file(&work.join(&relative), "fonts.conf", 4096).unwrap(),
            inspected.entries["fonts.conf"]
        );
        let retained = retain(
            &work,
            &output,
            SOURCE,
            &args.plan,
            &prepared.plan_sha256,
            Some(admitted),
        )
        .unwrap();
        assert_ne!(
            retained.relative_path,
            args.source_bundle.as_ref().unwrap().relative_path
        );
        assert_eq!(retained.sha256, args.source_bundle.as_ref().unwrap().sha256);
        assert_eq!(retained.bytes, original.len() as u64);
        assert_eq!(
            fs::read(output.join(&retained.relative_path)).unwrap(),
            original
        );
        assert_eq!(
            fs::read(output.join("exported.mwedit.zip")).unwrap(),
            original
        );
        assert!(!output.join(input_basename(&args.plan.fonts[0])).exists());
        assert!(stage_assets(&work, &output, SOURCE, &args.plan, Some(admitted)).is_err());
    }

    #[test]
    fn changed_bundle_identity_plan_or_namespace_is_rejected_before_job_staging() {
        let temporary = tempfile::tempdir().unwrap();
        let output = fs::canonicalize(temporary.path()).unwrap();
        let (original_args, original) = canonical_input(&output, "exported.mwedit.zip");
        assert!(prepare(&original_args, None).is_err());
        for case in [
            "hash",
            "size",
            "budget",
            "plan",
            "traversal",
            "absolute",
            "suffix",
        ] {
            let mut args = original_args.clone();
            match case {
                "hash" => args.source_bundle.as_mut().unwrap().sha256 = "0".repeat(64),
                "size" => args.source_bundle.as_mut().unwrap().bytes += 1,
                "budget" => {
                    args.source_bundle.as_mut().unwrap().bytes =
                        profile::MAX_BUNDLE_BYTES as u64 + 1
                }
                "plan" => args.plan.binding.revision += 1,
                "traversal" => {
                    args.source_bundle.as_mut().unwrap().relative_path =
                        "../exported.mwedit.zip".into()
                }
                "absolute" => {
                    args.source_bundle.as_mut().unwrap().relative_path =
                        "/exported.mwedit.zip".into()
                }
                "suffix" => {
                    args.source_bundle.as_mut().unwrap().relative_path = "exported.py".into()
                }
                _ => unreachable!(),
            }
            assert!(prepare(&args, Some(&output)).is_err(), "{case}");
            assert_eq!(
                fs::read(output.join("exported.mwedit.zip")).unwrap(),
                original
            );
            assert_eq!(fs::read_dir(&output).unwrap().count(), 1);
        }
        let invalid = b"not a canonical ZIP or executable source";
        fs::write(output.join("invalid.mwedit.zip"), invalid).unwrap();
        let mut args = original_args;
        args.source_bundle = Some(SourceBundleInput {
            root: SourceBundleRoot::Output,
            relative_path: "invalid.mwedit.zip".into(),
            sha256: hex::encode(Sha256::digest(invalid)),
            bytes: invalid.len() as u64,
        });
        assert!(prepare(&args, Some(&output)).is_err());
        assert_eq!(fs::read_dir(&output).unwrap().count(), 2);
    }

    #[cfg(unix)]
    #[test]
    fn source_bundle_symlink_is_rejected_inside_the_existing_output_mount() {
        let temporary = tempfile::tempdir().unwrap();
        let output = fs::canonicalize(temporary.path()).unwrap();
        let (mut args, original) = canonical_input(&output, "exported.mwedit.zip");
        std::os::unix::fs::symlink(
            output.join("exported.mwedit.zip"),
            output.join("linked.mwedit.zip"),
        )
        .unwrap();
        args.source_bundle.as_mut().unwrap().relative_path = "linked.mwedit.zip".into();
        assert!(prepare(&args, Some(&output)).is_err());
        assert_eq!(
            fs::read(output.join("exported.mwedit.zip")).unwrap(),
            original
        );
    }
}
