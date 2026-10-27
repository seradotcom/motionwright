//! Private, closed Montserrat variant of the original Film180 fixture.
//! Source tests perform no native dispatch or font installation.
use super::*;
use motionwright_domain::{FontRight, RightsStatus};
use motionwright_service::VerifiedBrandFont;
use motionwright_storage::ProductionReceipt;
use semwright_motion_authoring::FontFallback;
use semwright_semantic_composition::strict_decode;
use serde_json::Value;
use std::collections::BTreeMap;

const FAMILY: &str = "Montserrat";
const FONT: &[u8] = include_bytes!("../../../manim-profile/tests/fixtures/Montserrat-variable.ttf");
const LICENSE: &[u8] = include_bytes!("../../../manim-profile/tests/fixtures/Montserrat-OFL.txt");
const ORIGIN: &[u8] =
    include_bytes!("../../../manim-profile/tests/fixtures/Montserrat-source.json");
const FONT_SHA: &str = "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7";
const LICENSE_SHA: &str = "8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e";
const ORIGIN_SHA: &str = "2656ac500e19cee1a35c1293985c33741c9f1e030b458bdff967a4a02d2f8ab0";
const ORIGINAL_REQUEST: &str = "brand-font-film180-original-native-v1";
const EDITED_REQUEST: &str = "brand-font-film180-edited-native-v1";
const EDITED_TEXT: &str = "Edited Montserrat source remains editable";
const JSON_LIMIT: usize = 8 * 1024 * 1024;

fn require(ok: bool, message: &'static str) -> FixtureResult<()> {
    if ok { Ok(()) } else { Err(message.into()) }
}
fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn decode<T: serde::de::DeserializeOwned>(bytes: &[u8]) -> FixtureResult<T> {
    strict_decode(bytes).map_err(|error| format!("Invalid original JSON: {error}").into())
}
fn fresh_output(path: &Path) -> FixtureResult<()> {
    must_be_unused(path)?;
    let parent = path.parent().ok_or("Fresh output has no parent")?;
    require(
        fs::canonicalize(parent)?.as_path() == parent,
        "Fresh brand-font output requires its canonical existing parent",
    )
}
fn verify_sources(sources: [&[u8]; 3]) -> FixtureResult<()> {
    require(
        sources[0].len() == 744_936
            && [FONT_SHA, LICENSE_SHA, ORIGIN_SHA]
                .into_iter()
                .zip(sources)
                .all(|(expected, bytes)| sha(bytes) == expected),
        "Pinned Montserrat font, OFL or source identity changed",
    )
}
fn single_project(service: &StudioService) -> FixtureResult<Project> {
    let projects = service.projects(2)?;
    let [project] = projects.as_slice() else {
        return Err("Brand-font Film180 requires one saved project".into());
    };
    Ok(project.clone())
}
fn verified_font(service: &StudioService, project: &Project) -> FixtureResult<VerifiedBrandFont> {
    verify_sources([FONT, LICENSE, ORIGIN])?;
    let font = service.verified_brand_font(project.id, &RevisionStamp::from(project), FAMILY)?;
    require(
        font.source.family == FAMILY
            && font.source.media_type == "font/ttf"
            && font.source.sha256 == FONT_SHA
            && font.bytes.as_slice() == FONT
            && sha(&font.bytes) == FONT_SHA,
        "Selected CAS font is not the pinned Montserrat source",
    )?;
    Ok(font)
}

pub(super) fn admit(service: &StudioService, project: &Project) -> FixtureResult<NativeAdmission> {
    project.validate()?;
    require(
        service.project(project.id)? == *project,
        "Saved brand-font revision changed",
    )?;
    let mut options = authored_options(project)?;
    // The shared caller renders this first deliverable ID. This closed fixture
    // derives its labels and glyph coverage from node.text, without overrides.
    let profile = project
        .deliverables
        .first()
        .ok_or("Brand-font source has no selected deliverable")?;
    require(
        profile.text_overrides.is_empty(),
        "Closed brand-font fixture rejects selected-deliverable text overrides",
    )?;
    options.font_family = FAMILY.into();
    let texts = project
        .scenes
        .iter()
        .flat_map(|scene| &scene.nodes)
        .filter(|node| node.kind == "text")
        .collect::<Vec<_>>();
    require(
        texts.len() == 3
            && texts.iter().all(|node| {
                node.style.font_family.as_deref() == Some(FAMILY)
                    && node.style.font_weight == Some(700)
                    && node
                        .text
                        .as_deref()
                        .is_some_and(|text| text.is_ascii() && text.len() <= 80)
            })
            && project.scenes[0].nodes[0].text.as_deref() == Some("First authored synthetic title")
            && project.scenes[0].nodes[1].text.as_deref()
                == Some("Authorial source remains editable")
            && project
                .audio
                .transcript
                .iter()
                .map(|cue| cue.text.as_str())
                .collect::<Vec<_>>()
                == [
                    "Opening source-bound caption",
                    "Second source-bound caption",
                ],
        "Saved brand-font source no longer matches the closed Film180 profile",
    )?;
    let (request_id, prior_request) = match project.scenes[1].nodes[0].text.as_deref() {
        Some(ORIGINAL_SECOND_TEXT) => (ORIGINAL_REQUEST, None),
        Some(EDITED_TEXT) => (EDITED_REQUEST, Some(ORIGINAL_REQUEST)),
        _ => return Err("Brand-font original/edited text is not admitted".into()),
    };
    let font = verified_font(service, project)?;
    let film_sha = verified_projection_with_options(project, &options)?;
    let segments = build_motion_canvas_segments(project, profile.id, &options)?;
    let film = &segments[0].film;
    let asset_id = format!("asset-{}", font.source.asset_id.simple());
    require(
        film.editorial.font.family == FAMILY
            && film
                .editorial
                .font
                .asset_digest
                .as_ref()
                .map(|digest| digest.as_str())
                == Some(FONT_SHA)
            && film.editorial.font.fallback == FontFallback::Deny
            && film.editorial.font.permitted_fallbacks.is_empty()
            && film.editorial.mono_font.family == MOTION_CANVAS_MONO_FONT_FAMILY
            && film.editorial.mono_font.asset_digest.is_none()
            && film
                .assets
                .iter()
                .filter(|asset| {
                    asset.id == asset_id
                        && asset.sha256.as_str() == FONT_SHA
                        && asset.media_type == "font/ttf"
                })
                .count()
                == 1,
        "Canonical Film did not select the CAS face; bundled IBM is not a managed font",
    )?;
    Ok(NativeAdmission {
        film_sha,
        options,
        request_id,
        prior_request,
    })
}

fn retain_reopen(
    service: StudioService,
    database: &Path,
    project: Project,
    target: &Path,
) -> FixtureResult<Project> {
    let expected = admit(&service, &project)?.film_sha;
    let history = serde_json::to_value(service.history(project.id, 0, 256)?)?;
    stdin_readback::new_json(target, &serde_json::to_value(&project)?, JSON_LIMIT)?;
    drop(service);
    let fresh = StudioService::open(database)?;
    let observed = fresh.project(project.id)?;
    require(
        observed == project
            && admit(&fresh, &observed)?.film_sha == expected
            && serde_json::to_value(fresh.history(project.id, 0, 256)?)? == history,
        "SQLite reopen changed the source, history, CAS selection or canonical Film",
    )?;
    Ok(observed)
}

pub(super) fn seed(
    database: &Path,
    wave: &Path,
    baseline_json: &Path,
    brand_json: &Path,
) -> FixtureResult<Project> {
    seed_sources(
        database,
        wave,
        baseline_json,
        brand_json,
        [FONT, LICENSE, ORIGIN],
    )
}
fn seed_sources(
    database: &Path,
    wave: &Path,
    baseline_json: &Path,
    brand_json: &Path,
    sources: [&[u8]; 3],
) -> FixtureResult<Project> {
    require(cfg!(unix), "Brand-font source custody requires Unix")?;
    verify_sources(sources)?; // Before any source write/import.
    let retained = [
        database.with_extension("Montserrat-variable.ttf"),
        database.with_extension("Montserrat-OFL.txt"),
        database.with_extension("Montserrat-source.json"),
    ];
    let mut outputs = vec![
        database.to_path_buf(),
        wave.to_path_buf(),
        baseline_json.to_path_buf(),
        brand_json.to_path_buf(),
    ];
    outputs.extend(retained.iter().cloned());
    let mut distinct = HashSet::new();
    for path in &outputs {
        fresh_output(path)?;
        require(
            distinct.insert(path),
            "Brand-font source outputs must be distinct",
        )?;
    }
    let mut project = seed_original(database, wave, baseline_json)?;
    let service = StudioService::open(database)?;
    for (path, bytes) in retained.iter().zip(sources) {
        stdin_readback::new_bytes(path, bytes)?;
        require(
            stdin_readback::read_source(path, 1024 * 1024)?.as_slice() == bytes,
            "Retained licensed source changed",
        )?;
    }
    project = service
        .import_asset_file(
            project.id,
            &RevisionStamp::from(&project),
            "brand-font-film180:import-font",
            &retained[0],
            "Pinned Montserrat variable".into(),
            "font/ttf".into(),
        )?
        .project;
    let asset_id = project
        .assets
        .iter()
        .find(|asset| {
            asset.name == "Pinned Montserrat variable"
                && asset.media_type == "font/ttf"
                && asset.content_sha256.as_deref() == Some(FONT_SHA)
        })
        .ok_or("Imported font is absent from the saved project")?
        .id;
    let mut language = project.visual_language.clone();
    language.fonts.push(FontRight {
        family: FAMILY.into(),
        source: format!("asset:{asset_id}"),
        rights_status: RightsStatus::Cleared,
    });
    apply(
        &service,
        &mut project,
        "brand-font-film180:declare-font",
        Change::SetVisualLanguage {
            visual_language: language,
        },
    )?;
    let selections = project
        .scenes
        .iter()
        .flat_map(|scene| {
            scene
                .nodes
                .iter()
                .filter(|node| node.kind == "text")
                .map(move |node| {
                    let mut style = node.style.clone();
                    style.font_family = Some(FAMILY.into());
                    (scene.id, node.id, style)
                })
        })
        .collect::<Vec<_>>();
    for (index, (scene_id, node_id, style)) in selections.into_iter().enumerate() {
        apply(
            &service,
            &mut project,
            &format!("brand-font-film180:select-face:{index}"),
            Change::UpdateCanvasStyle {
                scene_id,
                node_id,
                style,
            },
        )?;
    }
    retain_reopen(service, database, project, brand_json)
}

pub(super) fn readback(database: &Path, target: &Path) -> FixtureResult<()> {
    fresh_output(target)?;
    let service = StudioService::open(database)?;
    let project = single_project(&service)?;
    let admitted = admit(&service, &project)?;
    stdin_readback::new_json(
        target,
        &json!({
            "schema":"motionwright.native.brand-font-film180.saved-readback/1",
            "project_id":project.id,"generation":project.generation,"revision":project.revision,
            "canonical_Film_sha256":admitted.film_sha,"font_sha256":FONT_SHA,"font_bytes":FONT.len(),
            "history_sha256":sha(&serde_json::to_vec(&service.history(project.id, 0, 256)?)?),
            "bundled_mono_family":MOTION_CANVAS_MONO_FONT_FAMILY,"bundled_mono_is_imported_font":false,
            "native_dispatch_performed_by_readback":false,"formal_acceptance":false
        }),
        JSON_LIMIT,
    )
}

fn completed(
    service: &StudioService,
    project: &Project,
    request: &str,
    suffix: &str,
    command: &str,
) -> FixtureResult<ProductionReceipt> {
    let receipt = service
        .latest_production_receipt(project.id, &format!("{request}:motion:0:{suffix}"))?
        .ok_or("Source-bound original production receipt is absent")?;
    require(
        receipt.stage == "completed"
            && receipt.command == command
            && receipt.generation == project.generation
            && receipt.revision == project.revision,
        "Original production receipt has the wrong command, stamp or terminal state",
    )?;
    Ok(receipt)
}

pub(super) fn require_unattempted(
    service: &StudioService,
    project: &Project,
    request: &str,
) -> FixtureResult<()> {
    for suffix in [
        "font-project-inspect",
        "font-project-create",
        "font-list:0",
        "font-import:0",
        "font-readback:0",
        "plan",
        "apply",
        "render-start",
        "render-result",
        "verify",
    ] {
        require(
            service
                .latest_production_receipt(project.id, &format!("{request}:motion:0:{suffix}"))?
                .is_none(),
            "Brand-font production identity already exists; preserve the outcome, never retry",
        )?;
    }
    Ok(())
}

pub(super) fn binding_path(evidence: &Path) -> PathBuf {
    evidence.with_extension("brand-font-binding.json")
}

pub(super) fn preflight(
    service: &StudioService,
    project: &Project,
    request: &str,
    evidence: &Path,
    custody: Option<&Path>,
) -> FixtureResult<()> {
    require(
        cfg!(target_os = "linux") && custody.is_some(),
        "Captured brand-font production requires the original Linux Host custody",
    )?;
    fresh_output(&binding_path(evidence))?;
    require_unattempted(service, project, request)
}

fn native_matches(project: &Project, native: &MotionCanvasRenderEvidence) -> FixtureResult<()> {
    require(
        native.project_resource == project.resource_key()
            && native.generation == project.generation
            && native.revision == project.revision
            && native.deliverable_id == project.deliverables[0].id
            && native.frame_rate == Rate::new(FRAME_RATE, 1)?
            && native.segments.len() == 1
            && native.segments[0].frame_count == FRAME_COUNT
            && native.segments[0].scene_ids
                == project
                    .scenes
                    .iter()
                    .map(|scene| scene.id)
                    .collect::<Vec<_>>()
            && native.segments[0]
                .verification
                .pointer("/report/support_level")
                .and_then(Value::as_str)
                == Some("native"),
        "Native result does not identify the saved brand-font source and clock",
    )
}

fn apply_label_edit(service: &StudioService, baseline: &Project) -> FixtureResult<Project> {
    let before = admit(service, baseline)?;
    require(
        before.request_id == ORIGINAL_REQUEST,
        "Only the original brand-font label can be edited",
    )?;
    let scene_id = baseline.scenes[1].id;
    let node_id = baseline.scenes[1].nodes[0].id;
    let edited = service
        .apply(
            baseline.id,
            &RevisionStamp::from(baseline),
            "brand-font-film180:edit-second-label",
            &Change::UpdateCanvasText {
                scene_id,
                node_id,
                text: Some(EDITED_TEXT.into()),
            },
        )?
        .project;
    // Permit only this label and the normal revision/time/branch commit metadata.
    let mut expected = baseline.clone();
    expected.revision = baseline
        .revision
        .checked_add(1)
        .ok_or("Source revision overflow")?;
    expected.scenes[1].nodes[0].text = Some(EDITED_TEXT.into());
    expected.updated_at = edited.updated_at;
    let active = expected.active_branch;
    let next_revision = expected.revision;
    expected
        .branches
        .iter_mut()
        .find(|branch| branch.id == active)
        .ok_or("Original active branch is absent")?
        .head_revision = next_revision;
    require(
        edited == expected,
        "Brand-font edit changed more than the saved second label",
    )?;
    require(
        admit(service, &edited)?.film_sha != before.film_sha,
        "Text edit did not change canonical Film",
    )?;
    Ok(edited)
}

pub(super) fn edit(database: &Path, first_evidence: &Path, target: &Path) -> FixtureResult<()> {
    require(
        env::var("GITHUB_ACTIONS").as_deref() == Ok("true"),
        "Native edit requires disposable CI",
    )?;
    fresh_output(target)?;
    let service = StudioService::open(database)?;
    let original = single_project(&service)?;
    let admitted = admit(&service, &original)?;
    require(
        admitted.request_id == ORIGINAL_REQUEST,
        "Brand-font source was already edited",
    )?;
    let bytes = stdin_readback::read_source(first_evidence, JSON_LIMIT)?;
    native_matches(&original, &decode::<MotionCanvasRenderEvidence>(&bytes)?)?;
    let binding: Value = decode(&stdin_readback::read_source(
        &binding_path(first_evidence),
        JSON_LIMIT,
    )?)?;
    require(
        binding["schema"] == "motionwright.private-brand-font-original-host-binding/1"
            && binding["project_id"] == json!(original.id)
            && binding["generation"] == json!(original.generation)
            && binding["revision"] == original.revision
            && binding["request_id"] == ORIGINAL_REQUEST
            && binding["canonical_Film_sha256"] == admitted.film_sha
            && binding["native_evidence_sha256"] == sha(&bytes)
            && binding["font_sha256"] == FONT_SHA
            && binding["original_sources_joined"] == true,
        "Original brand-font render has no completed captured-source join",
    )?;
    for (suffix, command) in [
        ("apply", "driver.motion-canvas.composition.apply"),
        ("render-start", "driver.motion-canvas.render.start"),
        ("render-result", "driver.motion-canvas.render.result"),
        ("verify", "driver.motion-canvas.composition.verify"),
    ] {
        completed(&service, &original, ORIGINAL_REQUEST, suffix, command)?;
    }
    let edited = apply_label_edit(&service, &original)?;
    retain_reopen(service, database, edited, target)?;
    Ok(())
}

fn digest_shape(value: &Value) -> bool {
    value.as_str().is_some_and(|text| {
        text.len() == 64
            && text
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}
fn relative(value: &str) -> FixtureResult<()> {
    require(
        !value.is_empty()
            && value.len() <= 256
            && !value.starts_with('/')
            && value.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'.' | b'_' | b'-')
            })
            && value
                .split('/')
                .all(|part| !part.is_empty() && part != "." && part != ".."),
        "Original native artifact or managed font path is not a closed relative path",
    )
}
fn exact_keys(value: &Value, keys: &[&str]) -> FixtureResult<()> {
    let object = value
        .as_object()
        .ok_or("Expected an original JSON object")?;
    require(
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key)),
        "Original JSON key set changed",
    )
}
fn font_generation(receipt: &ProductionReceipt) -> FixtureResult<u64> {
    let payload = &receipt.payload;
    let context = &payload["provider_bound_font"];
    let generation = context["provider_generation"]
        .as_u64()
        .filter(|value| *value > 0)
        .ok_or("Original font receipt has no provider generation")?;
    let nested = &payload["result"]["data"]["result"];
    require(
        payload["font_qualification"] == "admitted"
            && context["schema"] == "motionwright.provider-bound-font/1"
            && context["provider"] == "driver:motion-canvas"
            && nested["ok"] == true
            && nested["execution"]["backend"] == "driver:motion-canvas"
            && nested["execution"]["provenance"]["provider_generation"] == generation,
        "Stored font observations do not join the admitted provider generation",
    )?;
    Ok(generation)
}

pub(super) fn verify_host_input(
    service: &StudioService,
    project: &Project,
    request: &str,
    evidence: &Path,
    output_root: &Path,
    native: &Value,
) -> FixtureResult<()> {
    let admitted = admit(service, project)?;
    require(
        admitted.request_id == request,
        "Captured source uses a foreign fixture request",
    )?;
    let source = verified_font(service, project)?;
    let native_bytes = stdin_readback::read_source(evidence, JSON_LIMIT)?;
    require(
        decode::<Value>(&native_bytes)? == *native,
        "Original native return differs from its retained evidence",
    )?;
    native_matches(
        project,
        &decode::<MotionCanvasRenderEvidence>(&native_bytes)?,
    )?;

    let custody = evidence.with_extension("host-stdin");
    let observation: Value = decode(&stdin_readback::read_source(
        &custody.join("readback.observation.json"),
        JSON_LIMIT,
    )?)?;
    let selected = &observation["selected_capture_join"];
    require(
        selected["matched"] == true && selected["pipe_write_all_completed"] == true,
        "Original selected Host input has no completed joined pipe write",
    )?;
    let ordinal = selected["ordinal"]
        .as_u64()
        .filter(|value| (1..=64).contains(value))
        .ok_or("Original capture ordinal is absent")?;
    let program =
        stdin_readback::read_source(&custody.join(format!("capture-{ordinal:04}.stdin")), 65536)?;
    require(
        selected["capture"]["stdin_sha256"] == sha(&program),
        "Original Host stdin digest changed",
    )?;
    let newline = program
        .iter()
        .position(|byte| *byte == b'\n')
        .filter(|index| index + 1 < program.len())
        .ok_or("Original Host program is incomplete")?;
    let line = std::str::from_utf8(&program[..newline])?;
    let raw = line
        .strip_prefix("globalThis.__SEMWRIGHT_RENDER_INPUT__ = ")
        .and_then(|value| value.strip_suffix(';'))
        .ok_or("Original Host input prefix changed")?;
    let config: Value = decode(raw.as_bytes())?;
    exact_keys(
        &config,
        &[
            "authoring",
            "renderInputDigest",
            "dependencyLockDigest",
            "fontResourcesDigest",
            "fontResourcePins",
            "managedFonts",
            "name",
            "width",
            "height",
            "fps",
            "fpsNum",
            "fpsDen",
            "firstFrame",
            "endFrameExclusive",
            "colorSpace",
            "background",
            "alpha",
            "timeoutMs",
        ],
    )?;
    require(
        config["authoring"] == true
            && config["name"] == "frames"
            && config["width"] == 1920
            && config["height"] == 1080
            && config["fps"].as_f64() == Some(30.0)
            && config["fpsNum"] == 30
            && config["fpsDen"] == 1
            && config["firstFrame"] == 0
            && config["endFrameExclusive"] == FRAME_COUNT
            && config["colorSpace"] == "srgb"
            && config["alpha"] == false
            && config["timeoutMs"] == 300000
            && config["background"].is_string()
            && digest_shape(&config["renderInputDigest"])
            && digest_shape(&config["dependencyLockDigest"]),
        "Original Host input differs from the closed Film180 clock/profile",
    )?;
    let managed = config["managedFonts"]
        .as_array()
        .ok_or("Original managedFonts array is absent")?;
    let [font] = managed.as_slice() else {
        return Err("Original Host input must contain exactly one managed face".into());
    };
    exact_keys(
        font,
        &[
            "family",
            "path",
            "sha256",
            "bytes",
            "weights",
            "codepoints",
            "sample",
            "face_loaded",
            "metadata",
        ],
    )?;
    let path = font["path"]
        .as_str()
        .ok_or("Original managed font path is absent")?;
    relative(path)?;
    require(
        font["family"] == FAMILY
            && font["sha256"] == FONT_SHA
            && font["bytes"].as_u64() == Some(FONT.len() as u64)
            && font["weights"] == json!([600, 700])
            && font["face_loaded"] == false,
        "Original Host input does not select the exact CAS face and caption/title weights",
    )?;
    // These are input coverage observations, never a pixel/glyph-readiness verdict.
    let expected_points = project
        .scenes
        .iter()
        .flat_map(|scene| &scene.nodes)
        .filter_map(|node| node.text.as_deref())
        .chain(project.audio.transcript.iter().map(|cue| cue.text.as_str()))
        .flat_map(str::chars)
        .filter(|character| *character > ' ')
        .map(u32::from)
        .collect::<BTreeSet<_>>();
    let ranges: Vec<[u32; 2]> = serde_json::from_value(font["codepoints"].clone())?;
    let mut points = BTreeSet::new();
    for [first, last] in ranges {
        require(
            first > 32 && first <= last && last < 128,
            "Original glyph coverage is outside the closed ASCII source",
        )?;
        for point in first..=last {
            require(
                points.insert(point),
                "Original glyph coverage is duplicated",
            )?;
        }
    }
    let sample = expected_points
        .iter()
        .next()
        .and_then(|point| char::from_u32(*point))
        .ok_or("Closed source contains no requested glyphs")?
        .to_string();
    require(
        points == expected_points && font["sample"] == sample,
        "Original Host input omitted or substituted source glyph coverage",
    )?;

    // First pass must import; the edited pass legitimately reuses its independently listed asset.
    let suffix = if request == ORIGINAL_REQUEST {
        "font-readback:0"
    } else {
        "font-list:0"
    };
    let receipt = completed(
        service,
        project,
        request,
        suffix,
        "driver.motion-canvas.asset.list",
    )?;
    let generation = font_generation(&receipt)?;
    let asset_id = format!("asset-{}", source.source.asset_id.simple());
    let items = receipt
        .payload
        .pointer("/result/data/result/data/items")
        .and_then(Value::as_array)
        .ok_or("Original font source readback has no asset roster")?;
    require(
        items
            .iter()
            .filter(|asset| {
                asset["id"] == asset_id
                    && asset["kind"] == "font"
                    && asset["path"] == path
                    && asset["sha256"] == FONT_SHA
                    && asset["bytes"].as_u64() == Some(FONT.len() as u64)
            })
            .count()
            == 1,
        "Captured managed font is not the actual imported/listed native asset",
    )?;
    let imported = if request == ORIGINAL_REQUEST {
        let imported = completed(
            service,
            project,
            request,
            "font-import:0",
            "driver.motion-canvas.asset.import",
        )?;
        require(
            font_generation(&imported)? == generation
                && imported.payload["provider_bound_font"]["binding_ref"]
                    == receipt.payload["provider_bound_font"]["binding_ref"]
                && imported.payload.pointer("/result/data/result/data/applied")
                    == Some(&Value::Bool(true)),
            "Original font import and readback do not share the admitted binding",
        )?;
        Some(imported)
    } else {
        None
    };
    let mut joins = Vec::new();
    for (suffix, command) in [
        ("plan", "driver.motion-canvas.composition.plan"),
        ("apply", "driver.motion-canvas.composition.apply"),
        ("render-start", "driver.motion-canvas.render.start"),
        ("render-result", "driver.motion-canvas.render.result"),
        ("verify", "driver.motion-canvas.composition.verify"),
    ] {
        let observed = completed(service, project, request, suffix, command)?;
        let provenance = &observed.payload["result"]["execution"]["provenance"];
        require(
            observed.payload["result"]["provenance_checked"] == true
                && provenance["provider"] == "driver:motion-canvas"
                && provenance["provider_generation"] == generation,
            "Font preparation and native production changed provider generation",
        )?;
        joins.push(
            json!({"id":observed.id,"request_id":observed.request_id,"command":observed.command,
            "provider_generation":generation}),
        );
    }

    let pins = config["fontResourcePins"]
        .as_array()
        .ok_or("Original font pins are absent")?;
    require(
        (4..=128).contains(&pins.len()),
        "Original font resource pin count is invalid",
    )?;
    let mut resources = BTreeMap::new();
    for pin in pins {
        exact_keys(pin, &["path", "sha256"])?;
        let path = pin["path"]
            .as_str()
            .ok_or("Original font resource path is absent")?;
        relative(path)?;
        require(
            digest_shape(&pin["sha256"]),
            "Original font resource digest is invalid",
        )?;
        let digest = pin["sha256"]
            .as_str()
            .ok_or("Original resource digest is absent")?;
        require(
            resources.insert(path, digest).is_none(),
            "Original font resource path is duplicated",
        )?;
    }
    let mut aggregate = Sha256::new();
    for (path, digest) in resources {
        aggregate.update(path.as_bytes());
        aggregate.update([0]);
        aggregate.update(digest.as_bytes());
        aggregate.update([0]);
    }
    aggregate.update(b"managed-font\0");
    aggregate.update(path.as_bytes());
    aggregate.update([0]);
    aggregate.update(FONT_SHA.as_bytes());
    aggregate.update([0]);
    require(
        config["fontResourcesDigest"] == hex::encode(aggregate.finalize()),
        "Original font closure digest differs",
    )?;

    let artifact = &native["segments"][0]["artifact"];
    let directory = artifact["directory"]
        .as_str()
        .ok_or("Original native artifact directory is absent")?;
    relative(directory)?;
    require(
        !directory.contains('/'),
        "Artifact is not one original output-root child",
    )?;
    let manifest_bytes = stdin_readback::read_source(
        &output_root.join(directory).join("artifact-manifest.json"),
        1024 * 1024,
    )?;
    require(
        artifact["manifest_sha256"] == sha(&manifest_bytes)
            && artifact["manifest_bytes"].as_u64() == Some(manifest_bytes.len() as u64),
        "Original artifact manifest differs from native evidence",
    )?;
    let manifest: Value = decode(&manifest_bytes)?;
    require(
        manifest["native_observations"]["render_input_digest"] == config["renderInputDigest"]
            && manifest["native_observations"]["font_resources_sha256"]
                == config["fontResourcesDigest"]
            && manifest["plan"]["first_frame"] == 0
            && manifest["plan"]["end_frame_exclusive"] == FRAME_COUNT
            && manifest["plan"]["frame_count"] == FRAME_COUNT,
        "Original artifact observations do not join the captured Host input",
    )?;
    stdin_readback::new_json(
        &binding_path(evidence),
        &json!({
            "schema":"motionwright.private-brand-font-original-host-binding/1",
            "project_id":project.id,"generation":project.generation,"revision":project.revision,"request_id":request,
            "canonical_Film_sha256":admitted.film_sha,"native_evidence_sha256":sha(&native_bytes),
            "font_sha256":FONT_SHA,"font_bytes":FONT.len(),"font_import":imported,"font_source_readback":receipt,
            "provider_generation":generation,"production_receipt_joins":joins,
            "original_Host_stdin_sha256":sha(&program),"original_artifact_manifest_sha256":sha(&manifest_bytes),
            "original_config":config,"selected_capture_join":selected,"original_sources_joined":true,
            "bundled_mono_family":MOTION_CANVAS_MONO_FONT_FAMILY,"bundled_mono_is_imported_font":false,
            "glyph_fidelity_accepted":false,"browser_font_readiness_inferred":false,
            "runtime_pins_verified_by_this_module":false,"receiver_lifetime_completeness_claimed":false,
            "formal_acceptance":false
        }),
        JSON_LIMIT,
    )
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use motionwright_storage::ProductionReceiptInput;

    fn seeded() -> (tempfile::TempDir, PathBuf, Project) {
        let directory = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(directory.path()).unwrap();
        let database = root.join("owner.sqlite3");
        let project = seed(
            &database,
            &root.join("source.wav"),
            &root.join("baseline.json"),
            &root.join("brand.json"),
        )
        .unwrap();
        (directory, database, project)
    }

    #[test]
    fn saved_selection_reopens_exact_cas_film_and_history() {
        let (_directory, database, project) = seeded();
        let service = StudioService::open(&database).unwrap();
        let before = admit(&service, &project).unwrap();
        let history = serde_json::to_value(service.history(project.id, 0, 256).unwrap()).unwrap();
        let captured: Project = decode(
            &stdin_readback::read_source(&database.with_file_name("brand.json"), JSON_LIMIT)
                .unwrap(),
        )
        .unwrap();
        assert_eq!(captured, project);
        assert_eq!(
            verified_font(&service, &project).unwrap().bytes.as_slice(),
            FONT
        );
        assert_eq!(before.request_id, ORIGINAL_REQUEST);
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        let observed = reopened.project(project.id).unwrap();
        assert_eq!(observed, project);
        assert_eq!(
            admit(&reopened, &observed).unwrap().film_sha,
            before.film_sha
        );
        assert_eq!(
            serde_json::to_value(reopened.history(project.id, 0, 256).unwrap()).unwrap(),
            history
        );
    }

    #[test]
    fn changed_pinned_inputs_and_noncanonical_parent_fail_before_source_writes() {
        for changed in 0..3 {
            let directory = tempfile::tempdir().unwrap();
            let root = fs::canonicalize(directory.path()).unwrap();
            let mut sources = [FONT.to_vec(), LICENSE.to_vec(), ORIGIN.to_vec()];
            sources[changed][0] ^= 1;
            assert!(
                seed_sources(
                    &root.join("owner.sqlite3"),
                    &root.join("source.wav"),
                    &root.join("baseline.json"),
                    &root.join("brand.json"),
                    [
                        sources[0].as_slice(),
                        sources[1].as_slice(),
                        sources[2].as_slice()
                    ],
                )
                .is_err()
            );
            assert_eq!(fs::read_dir(&root).unwrap().count(), 0);
        }
        let directory = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(directory.path()).unwrap();
        let child = root.join("existing-parent");
        fs::create_dir(&child).unwrap();
        assert!(
            seed_sources(
                &child.join("..").join("owner.sqlite3"),
                &root.join("source.wav"),
                &root.join("baseline.json"),
                &root.join("brand.json"),
                [FONT, LICENSE, ORIGIN],
            )
            .is_err()
        );
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        assert!(!root.join("owner.sqlite3").exists());
        assert!(!root.join("owner.Montserrat-variable.ttf").exists());
    }

    #[test]
    fn selected_deliverable_overrides_reject_before_label_edit_and_native_receipts() {
        let (_directory, database, mut project) = seeded();
        let service = StudioService::open(&database).unwrap();
        let original_film = admit(&service, &project).unwrap().film_sha;
        let node_id = project.scenes[1].nodes[0].id;

        // Other deliverables retain the product's ordinary override capability.
        let mut other = project.deliverables[1].clone();
        other
            .text_overrides
            .insert(node_id, "Unselected profile override".into());
        apply(
            &service,
            &mut project,
            "brand-test:other-profile-override",
            Change::UpsertDeliverable { profile: other },
        )
        .unwrap();
        assert_eq!(admit(&service, &project).unwrap().film_sha, original_film);

        // Both a no-op override and an anagram evade a glyph-set-only oracle.
        // Neither belongs to this node.text-authored fixture's selected profile.
        for (index, text) in [
            ORIGINAL_SECOND_TEXT.to_owned(),
            ORIGINAL_SECOND_TEXT.chars().rev().collect::<String>(),
        ]
        .into_iter()
        .enumerate()
        {
            assert_eq!(
                text.chars().collect::<BTreeSet<_>>(),
                ORIGINAL_SECOND_TEXT.chars().collect::<BTreeSet<_>>()
            );
            let mut selected = project.deliverables[0].clone();
            selected.text_overrides.insert(node_id, text);
            apply(
                &service,
                &mut project,
                &format!("brand-test:selected-override:{index}"),
                Change::UpsertDeliverable { profile: selected },
            )
            .unwrap();
            project.validate().unwrap();
            let history =
                serde_json::to_value(service.history(project.id, 0, 256).unwrap()).unwrap();
            let watermark = service.production_receipt_watermark(project.id).unwrap();
            assert_eq!(watermark.count, 0);
            let Err(error) = admit(&service, &project) else {
                panic!("Selected override must be rejected");
            };
            assert_eq!(
                error.to_string(),
                "Closed brand-font fixture rejects selected-deliverable text overrides"
            );
            assert!(apply_label_edit(&service, &project).is_err());
            assert_eq!(service.project(project.id).unwrap(), project);
            assert_eq!(
                serde_json::to_value(service.history(project.id, 0, 256).unwrap()).unwrap(),
                history
            );
            assert_eq!(
                service.production_receipt_watermark(project.id).unwrap(),
                watermark
            );
            assert_eq!(
                project.scenes[1].nodes[0].text.as_deref(),
                Some(ORIGINAL_SECOND_TEXT)
            );
        }
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        let observed = reopened.project(project.id).unwrap();
        assert_eq!(observed, project);
        assert!(admit(&reopened, &observed).is_err());
        assert_eq!(
            reopened
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
    }

    #[test]
    fn mixed_saved_family_is_rejected_without_native_receipts() {
        let (_directory, database, mut project) = seeded();
        let service = StudioService::open(&database).unwrap();
        let scene_id = project.scenes[0].id;
        let node_id = project.scenes[0].nodes[0].id;
        let mut style = project.scenes[0].nodes[0].style.clone();
        style.font_family = Some(MOTION_CANVAS_MONO_FONT_FAMILY.into());
        apply(
            &service,
            &mut project,
            "brand-test:mixed-family",
            Change::UpdateCanvasStyle {
                scene_id,
                node_id,
                style,
            },
        )
        .unwrap();
        assert!(admit(&service, &project).is_err());
        assert_eq!(
            verified_font(&service, &project).unwrap().bytes.as_slice(),
            FONT
        );
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        let observed = reopened.project(project.id).unwrap();
        assert_eq!(observed, project);
        assert!(admit(&reopened, &observed).is_err());
    }

    #[test]
    fn different_imported_digest_cannot_replace_pinned_face_or_destroy_original_cas() {
        let (_directory, database, mut project) = seeded();
        let service = StudioService::open(&database).unwrap();
        let original_id = verified_font(&service, &project).unwrap().source.asset_id;
        // Source/CAS negative control only; these altered bytes are never rendered.
        let mut altered = FONT.to_vec();
        *altered.last_mut().unwrap() ^= 1;
        let digest = sha(&altered);
        let path = database.with_extension("different.ttf");
        stdin_readback::new_bytes(&path, &altered).unwrap();
        project = service
            .import_asset_file(
                project.id,
                &RevisionStamp::from(&project),
                "brand-test:import-different",
                &path,
                "Different source bytes".into(),
                "font/ttf".into(),
            )
            .unwrap()
            .project;
        let different_id = project
            .assets
            .iter()
            .find(|asset| asset.content_sha256.as_deref() == Some(digest.as_str()))
            .unwrap()
            .id;
        let mut language = project.visual_language.clone();
        language
            .fonts
            .iter_mut()
            .find(|font| font.family == FAMILY)
            .unwrap()
            .source = format!("asset:{different_id}");
        apply(
            &service,
            &mut project,
            "brand-test:select-different",
            Change::SetVisualLanguage {
                visual_language: language,
            },
        )
        .unwrap();
        assert_eq!(
            service
                .verified_brand_font(project.id, &RevisionStamp::from(&project), FAMILY,)
                .unwrap()
                .source
                .sha256,
            digest
        );
        assert!(admit(&service, &project).is_err());

        let mut language = project.visual_language.clone();
        language
            .fonts
            .iter_mut()
            .find(|font| font.family == FAMILY)
            .unwrap()
            .source = format!("asset:{original_id}");
        apply(
            &service,
            &mut project,
            "brand-test:restore-original-selection",
            Change::SetVisualLanguage {
                visual_language: language,
            },
        )
        .unwrap();
        assert!(admit(&service, &project).is_ok());
        assert_eq!(
            verified_font(&service, &project).unwrap().bytes.as_slice(),
            FONT
        );
        assert!(project.assets.iter().any(|asset| asset.id == different_id));
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
    }

    #[test]
    fn bundled_instrument_and_ibm_are_not_managed_font_evidence() {
        let directory = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(directory.path()).unwrap();
        let database = root.join("owner.sqlite3");
        let project = seed_original(
            &database,
            &root.join("source.wav"),
            &root.join("baseline.json"),
        )
        .unwrap();
        let service = StudioService::open(&database).unwrap();
        let options = authored_options(&project).unwrap();
        let segments =
            build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap();
        assert_eq!(
            segments[0].film.editorial.mono_font.family,
            MOTION_CANVAS_MONO_FONT_FAMILY
        );
        assert!(segments[0].film.editorial.font.asset_digest.is_none());
        assert!(segments[0].film.editorial.mono_font.asset_digest.is_none());
        assert!(admit(&service, &project).is_err());
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
    }

    #[test]
    fn source_component_label_edit_reopens_without_fabricated_native_success() {
        let (_directory, database, original) = seeded();
        let service = StudioService::open(&database).unwrap();
        let before = admit(&service, &original).unwrap().film_sha;
        let history = service.history(original.id, 0, 256).unwrap();
        // This is the actual service-edit component, not the public native gate.
        // No native result, Host capture or approval is synthesized.
        let edited = apply_label_edit(&service, &original).unwrap();
        assert!(apply_label_edit(&service, &edited).is_err());
        let edited = retain_reopen(
            service,
            &database,
            edited,
            &database.with_extension("edited.json"),
        )
        .unwrap();
        let reopened = StudioService::open(&database).unwrap();
        let admitted = admit(&reopened, &edited).unwrap();
        assert_eq!(admitted.request_id, EDITED_REQUEST);
        assert_eq!(admitted.prior_request, Some(ORIGINAL_REQUEST));
        assert_ne!(admitted.film_sha, before);
        assert_eq!(edited.assets, original.assets);
        assert_eq!(edited.visual_language, original.visual_language);
        assert_eq!(edited.scenes[1].nodes[0].id, original.scenes[1].nodes[0].id);
        let after = reopened.history(edited.id, 0, 256).unwrap();
        assert_eq!(after.len(), history.len() + 1);
        assert_eq!(
            serde_json::to_value(&after[..history.len()]).unwrap(),
            serde_json::to_value(&history).unwrap()
        );
        assert_eq!(
            verified_font(&reopened, &edited).unwrap().bytes.as_slice(),
            FONT
        );
        assert_eq!(
            reopened
                .production_receipt_watermark(edited.id)
                .unwrap()
                .count,
            0
        );
    }

    #[test]
    fn unknown_font_import_identity_blocks_before_any_further_dispatch() {
        let (_directory, database, project) = seeded();
        let service = StudioService::open(&database).unwrap();
        let request = format!("{ORIGINAL_REQUEST}:motion:0:font-import:0");
        // Synthetic local receipt fixture only; it does not represent a native invocation.
        for stage in ["dispatching", "outcome_unknown"] {
            service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: request.clone(),
                    request_sha256: sha(b"source-only synthetic receipt fixture"),
                    command: "driver.motion-canvas.asset.import".into(),
                    stage: stage.into(),
                    payload: json!({"mutation":true,"synthetic_test_only":true}),
                })
                .unwrap();
        }
        let before = service.production_receipt_watermark(project.id).unwrap();
        let unknown = service
            .latest_production_receipt(project.id, &request)
            .unwrap();
        assert_eq!(unknown.as_ref().unwrap().stage, "outcome_unknown");
        assert!(require_unattempted(&service, &project, ORIGINAL_REQUEST).is_err());
        assert_eq!(
            service.production_receipt_watermark(project.id).unwrap(),
            before
        );
        assert_eq!(
            service
                .latest_production_receipt(project.id, &request)
                .unwrap(),
            unknown
        );
    }
}
