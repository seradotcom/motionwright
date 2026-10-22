//! Static V2 is an explicitly versioned capability on the existing Driver Host.
//! No imported Python is evaluated; all programs are compiled from the typed plan.
use super::*;
use motionwright_manim_profile::{
    MAX_SOURCE_ENTRY_BYTES_V2, ManimScenePlanV2, ManimStartPlan, SOURCE_BUNDLE_MIME_V2,
    inspect_native_source_readback_v2, source_bundle_v2, source_package_v2,
};

pub(super) const START_COMMAND: &str = "driver.manim-community.static.render.start";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct StaticStartArgs {
    pub plan: ManimScenePlanV2,
    pub profile: ManimRenderProfile,
}

pub(super) struct PreparedStaticProgram {
    pub source: String,
    pub plan_sha256: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct NativeGetterArtifact {
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
    pub media_type: &'static str,
}

pub(super) fn prepare(args: &StaticStartArgs) -> Result<PreparedStaticProgram> {
    let plan = ManimStartPlan::V2(Box::new(args.plan.clone()));
    plan.validate_for_profile(args.profile)
        .map_err(map_profile_error)?;
    let source = plan.compile().map_err(map_profile_error)?;
    if source.len() > MAX_SOURCE_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Generated Manim static source exceeds the original Host source budget",
        ));
    }
    Ok(PreparedStaticProgram {
        source,
        plan_sha256: plan.plan_sha256().map_err(map_profile_error)?,
    })
}

fn relative(source_file: &str, suffix: &str) -> Result<String> {
    let camera = camera_source_relative(source_file)?;
    let stem = camera
        .strip_suffix(".mwcam.zip")
        .ok_or_else(|| invalid("Manim source filename is not bound to a job"))?;
    Ok(format!("{stem}.{suffix}"))
}

fn expected(
    source_file: &str,
    plan: &ManimScenePlanV2,
    plan_sha256: &str,
) -> Result<(EditableSourceArtifact, Vec<u8>, Vec<u8>)> {
    let package = source_package_v2(plan).map_err(map_profile_error)?;
    if package.closure.plan_sha256 != plan_sha256 {
        return Err(invalid(
            "Static source belongs to a different compiled plan",
        ));
    }
    let bytes = source_bundle_v2(plan).map_err(map_profile_error)?;
    let source = package
        .entries
        .get("scene.py")
        .ok_or_else(|| invalid("Static source package has no trusted program"))?
        .clone();
    let artifact = EditableSourceArtifact {
        relative_path: relative(source_file, "mwstatic.zip")?,
        sha256: hex::encode(Sha256::digest(&bytes)),
        bytes: bytes.len() as u64,
        media_type: SOURCE_BUNDLE_MIME_V2,
        closure: package.closure,
    };
    Ok((artifact, bytes, source))
}

pub(super) fn retain(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &ManimScenePlanV2,
    plan_sha256: &str,
) -> Result<EditableSourceArtifact> {
    let (artifact, bundle, source) = expected(source_file, plan, plan_sha256)?;
    if bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != source {
        return Err(invalid(
            "Static program differs from its original typed source",
        ));
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output_root.join(&artifact.relative_path))?;
    file.write_all(&bundle)?;
    file.sync_all()?;
    if bounded_source_file(
        output_root,
        &artifact.relative_path,
        MAX_SOURCE_BUNDLE_BYTES_V2,
    )? != bundle
    {
        return Err(invalid("Static source carrier failed byte readback"));
    }
    Ok(artifact)
}

/// Called only after the original Host has observed its native process complete.
/// The sidecar is read from that job's source basename and copied without rewriting
/// any getter values. The inspector compares actual object/camera measurements.
pub(super) fn verify_completed(
    work_root: &Path,
    output_root: &Path,
    source_file: &str,
    plan: &ManimScenePlanV2,
    plan_sha256: &str,
    recorded: Option<&EditableSourceArtifact>,
) -> Result<(EditableSourceArtifact, NativeGetterArtifact)> {
    let (expected_source, bundle, program) = expected(source_file, plan, plan_sha256)?;
    if recorded != Some(&expected_source)
        || bounded_source_file(work_root, source_file, MAX_SOURCE_BYTES)? != program
        || bounded_source_file(
            output_root,
            &expected_source.relative_path,
            MAX_SOURCE_BUNDLE_BYTES_V2,
        )? != bundle
    {
        return Err(invalid(
            "Static source custody differs from its original native job",
        ));
    }
    let name = relative(source_file, "objects.json")?;
    let bytes = bounded_source_file(work_root, &name, MAX_SOURCE_ENTRY_BYTES_V2)?;
    inspect_native_source_readback_v2(plan, &bytes).map_err(map_profile_error)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output_root.join(&name))?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    if bounded_source_file(output_root, &name, MAX_SOURCE_ENTRY_BYTES_V2)? != bytes {
        return Err(invalid("Native static getters changed during retention"));
    }
    let getters = NativeGetterArtifact {
        relative_path: name,
        sha256: hex::encode(Sha256::digest(&bytes)),
        bytes: bytes.len() as u64,
        media_type: "application/json",
    };
    Ok((expected_source, getters))
}

pub(super) fn start_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "properties": {
                    "schema_version": {"const": 2},
                    "profile_id": {"const": "mw-manim-static-shapes-v2"},
                    "output_role": {"const": "scene_video_contribution_without_audio_or_captions"},
                    "binding": {"type": "object"},
                    "clock": {"type": "object"},
                    "render_profile": {"type": "object"},
                    "canvas": {"type": "object"},
                    "background": {"type": "object"},
                    "nodes": {"type": "array", "minItems": 1, "maxItems": 512, "items": {"type": "object"}}
                },
                "required": ["schema_version", "profile_id", "output_role", "binding", "clock",
                    "render_profile", "canvas", "background", "nodes"],
                "additionalProperties": false
            },
            "profile": {
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "minimum": 320, "maximum": 7680},
                    "height": {"type": "integer", "minimum": 240, "maximum": 4320},
                    "frame_rate": {"type": "integer", "minimum": 1, "maximum": 120}
                },
                "required": ["width", "height", "frame_rate"],
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

    fn args() -> StaticStartArgs {
        // Literal rational times and independently chosen asymmetric geometry:
        // 0.5 s project offset, 4 s local clip, 120 native frames at 30 fps.
        serde_json::from_value(json!({
            "plan": {
                "schema_version": 2,
                "profile_id": "mw-manim-static-shapes-v2",
                "output_role": "scene_video_contribution_without_audio_or_captions",
                "binding": {
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "generation": "00000000-0000-0000-0000-000000000002",
                    "revision": 7,
                    "scene_id": "00000000-0000-0000-0000-000000000003",
                    "deliverable_id": "00000000-0000-0000-0000-000000000004"
                },
                "clock": {
                    "timeline_start": {"num": "1", "den": "2"},
                    "duration": {"num": "4", "den": "1"},
                    "frame_rate": {"num": "30", "den": "1"},
                    "project_start_frame": 15, "project_end_frame_exclusive": 135,
                    "local_start_frame": 0, "frame_count": 120
                },
                "render_profile": {"width": 640, "height": 360, "frame_rate": 30},
                "canvas": {"width": 1920, "height": 1080, "coordinate_space": "project_pixels"},
                "background": {"kind": "solid", "color": "#000000", "opacity": 1},
                "nodes": [{
                    "node_id": "00000000-0000-0000-0000-000000000005",
                    "display_name": "Editable rectangle",
                    "source_kind": "rectangle", "canonical_ordinal": 0, "z_index": -2,
                    "x_px": 420.0, "y_px": 337.5, "width_px": 270.0, "height_px": 135.0,
                    "fill_hex": "#86C8D1"
                }]
            },
            "profile": {"width": 640, "height": 360, "frame_rate": 30}
        }))
        .unwrap()
    }

    fn measured() -> Vec<u8> {
        // Independent expected native measurements, not produced from the plan:
        // center (-3, 1), dimensions (2, 1), at 135 pixels per Manim unit.
        serde_json::to_vec(&json!({
            "schema_version": 1, "profile_id": "mw-manim-static-shapes-v2",
            "scene_class": "MotionwrightScene",
            "camera": {
                "pixel_width": 640, "pixel_height": 360, "frame_rate": 30.0,
                "frame_width": 128.0 / 9.0, "frame_height": 8.0,
                "frame_center": [0.0, 0.0, 0.0],
                "background_hex": "#000000", "background_opacity": 1.0
            },
            "objects": [{
                "name": "mw_00000000000000000000000000000005", "primitive": "Rectangle",
                "center": [-3.0, 1.0, 0.0], "width": 2.0, "height": 1.0,
                "z_index": -2.0, "fill_hex": "#86C8D1", "fill_opacity": 1.0,
                "stroke_width": 0.0, "stroke_opacity": 0.0
            }]
        }))
        .unwrap()
    }

    #[test]
    fn v2_capability_is_distinct_and_keeps_legacy_input_and_output_contracts() {
        let caps = capability_catalog().unwrap();
        let cap = caps
            .iter()
            .find(|c| c.descriptor.name == START_COMMAND)
            .unwrap();
        assert_eq!(cap.descriptor.risk, Risk::MutatingReversible);
        assert_eq!(cap.descriptor.idempotency, Idempotency::NonIdempotent);
        assert_eq!(cap.descriptor.requires, vec![DRIVER_SCOPE]);
        assert!(!cap.descriptor.dry_run);
        assert_eq!(
            start_schema()["properties"]["plan"]["properties"]["schema_version"]["const"],
            2
        );
        let value = serde_json::to_value(args()).unwrap();
        assert!(serde_json::from_value::<StartArgs>(value.clone()).is_err());
        assert!(serde_json::from_value::<CameraStartArgs>(value).is_err());
        assert!(
            !serde_json::to_string(&super::super::start_schema())
                .unwrap()
                .contains("schema_version")
        );
    }

    #[test]
    fn v2_admission_refuses_foreign_profile_clock_schema_and_executable_fields() {
        let valid = args();
        let prepared = prepare(&valid).unwrap();
        let package = source_package_v2(&valid.plan).unwrap();
        assert_eq!(prepared.source.as_bytes(), package.entries["scene.py"]);
        assert_eq!(prepared.plan_sha256, package.closure.plan_sha256);
        for (pointer, replacement) in [
            ("/plan/schema_version", json!(1)),
            ("/plan/clock/frame_count", json!(119)),
            ("/plan/nodes/0/canonical_ordinal", json!(1)),
            ("/plan/nodes/0/source_kind", json!("text")),
            ("/profile/width", json!(1280)),
        ] {
            let mut value = serde_json::to_value(&valid).unwrap();
            *value.pointer_mut(pointer).unwrap() = replacement;
            if let Ok(args) = serde_json::from_value::<StaticStartArgs>(value) {
                assert!(prepare(&args).is_err(), "{pointer}");
            }
        }
        for pointer in ["/plan", "/plan/nodes/0", "/plan/binding", "/plan/clock"] {
            let mut value = serde_json::to_value(&valid).unwrap();
            value
                .pointer_mut(pointer)
                .unwrap()
                .as_object_mut()
                .unwrap()
                .insert(
                    "python_source".into(),
                    json!("arbitrary executable content"),
                );
            assert!(
                serde_json::from_value::<StaticStartArgs>(value).is_err(),
                "{pointer}"
            );
        }
    }

    #[test]
    fn static_original_source_and_actual_getters_are_retained_with_exclusive_custody() {
        let temp = tempfile::tempdir().unwrap();
        let work = temp.path().join("work");
        let output = temp.path().join("output");
        fs::create_dir(&work).unwrap();
        fs::create_dir(&output).unwrap();
        let work = fs::canonicalize(work).unwrap();
        let output = fs::canonicalize(output).unwrap();
        let name = "mw_0123456789abcdef0123456789abcdef.py";
        let args = args();
        let program = prepare(&args).unwrap();
        fs::write(work.join(name), &program.source).unwrap();
        let source = retain(&work, &output, name, &args.plan, &program.plan_sha256).unwrap();
        fs::write(
            work.join(relative(name, "objects.json").unwrap()),
            measured(),
        )
        .unwrap();
        let (checked, getters) = verify_completed(
            &work,
            &output,
            name,
            &args.plan,
            &program.plan_sha256,
            Some(&source),
        )
        .unwrap();
        assert_eq!(checked, source);
        assert_eq!(
            fs::read(output.join(&getters.relative_path)).unwrap(),
            measured()
        );
        let reopened = motionwright_manim_profile::inspect_source_bundle_v2(
            &fs::read(output.join(&source.relative_path)).unwrap(),
        )
        .unwrap();
        assert_eq!(reopened.plan, args.plan);
        assert_eq!(reopened.plan.clock.frame_count, 120);
        assert_eq!(reopened.plan.nodes[0].z_index, -2);
        // An accidental second terminal publication cannot overwrite custody.
        assert!(
            verify_completed(
                &work,
                &output,
                name,
                &args.plan,
                &program.plan_sha256,
                Some(&source)
            )
            .is_err()
        );
        assert_eq!(
            fs::read(output.join(&getters.relative_path)).unwrap(),
            measured()
        );
    }

    #[test]
    fn static_getter_verification_refuses_actual_geometry_color_opacity_and_inventory_drift() {
        let args = args();
        inspect_native_source_readback_v2(&args.plan, &measured()).unwrap();
        for (pointer, replacement) in [
            ("/objects/0/center/0", json!(-2.5)),
            ("/objects/0/height", json!(2.0)),
            ("/objects/0/z_index", json!(0)),
            ("/objects/0/fill_hex", json!("#FFFFFF")),
            ("/objects/0/fill_opacity", json!(0.75)),
            ("/camera/frame_center/0", json!(0.1)),
            ("/camera/frame_rate", json!(29.0)),
            ("/objects", json!([])),
        ] {
            let mut value: Value = serde_json::from_slice(&measured()).unwrap();
            *value.pointer_mut(pointer).unwrap() = replacement;
            assert!(
                inspect_native_source_readback_v2(&args.plan, &serde_json::to_vec(&value).unwrap())
                    .is_err(),
                "{pointer}"
            );
        }
    }

    #[test]
    fn static_source_refuses_changed_program_and_foreign_plan_before_publication() {
        let temp = tempfile::tempdir().unwrap();
        let work = temp.path().join("work");
        let output = temp.path().join("output");
        fs::create_dir(&work).unwrap();
        fs::create_dir(&output).unwrap();
        let work = fs::canonicalize(work).unwrap();
        let output = fs::canonicalize(output).unwrap();
        let name = "mw_0123456789abcdef0123456789abcdef.py";
        let args = args();
        let program = prepare(&args).unwrap();
        fs::write(work.join(name), b"changed program").unwrap();
        assert!(retain(&work, &output, name, &args.plan, &program.plan_sha256).is_err());
        assert_eq!(fs::read_dir(&output).unwrap().count(), 0);
        fs::write(work.join(name), &program.source).unwrap();
        let mut foreign = args.plan;
        foreign.binding.revision += 1;
        assert!(retain(&work, &output, name, &foreign, &program.plan_sha256).is_err());
        assert_eq!(fs::read_dir(&output).unwrap().count(), 0);
    }
}
