//! Semwright-owned semantic MLT timeline assembly using verified FFV1
//! intermediates and closed, revision-bound Driver Host operations. Produces
//! an actual FFV1+PCM lossless Matroska intermediate, NOT an H.264/AAC master.
//! The pinned Semwright curated MLT lossless profile requires a PCM audio
//! stream even if the semantic edit timeline contains only visual clips.
use super::*;
use crate::{
    assembly::preflight_multi_segment_mlt,
    mlt_edit_plan::{MltNativeVideoRecipe, mlt_native_video_recipe},
};
use serde_json::Value;

const MLT_TIMELINE_POLL_SECS: u64 = 330;
const MLT_TIMELINE_POLL_MS: u64 = 1000;
const MAX_NATIVE_TIMELINE_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Debug, Clone)]
struct MltSession {
    project_ref: String,
    revision: String,
}

struct MltEntityQuery<'a> {
    request_id: &'a str,
    command: &'a str,
    session: &'a MltSession,
    sequence: Option<&'a str>,
    name: &'a str,
}

fn opaque(value: &Value, key: &str) -> NativeResult<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty() && text.len() <= 80 && !text.chars().any(char::is_control))
        .map(str::to_owned)
        .ok_or_else(|| backend(format!("MLT semantic result lacks valid {key} reference")))
}
fn sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn created(value: &Value, kind: &str) -> NativeResult<String> {
    let refs = value
        .get("created_refs")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("MLT edit did not return created semantic references"))?;
    let references = refs
        .iter()
        .filter(|entry| entry.get("kind").and_then(Value::as_str) == Some(kind))
        .collect::<Vec<_>>();
    if references.len() != 1 {
        return Err(backend(
            "MLT semantic edit did not return exactly one created entity",
        ));
    }
    opaque(references[0], "reference")
}
fn mutation_response(prior: &MltSession, value: &Value) -> NativeResult<MltSession> {
    if value.get("applied").and_then(Value::as_bool) != Some(true)
        || value.get("expected_revision").and_then(Value::as_str) != Some(prior.revision.as_str())
        || value
            .get("support")
            .and_then(Value::as_str)
            .is_none_or(|value| value == "UNSUPPORTED")
    {
        return Err(backend(
            "MLT semantic mutation was not applied to its exact expected base",
        ));
    }
    let revision = opaque(value, "resulting_revision")?;
    if !sha256(&revision) || revision == prior.revision {
        return Err(backend(
            "MLT semantic mutation returned a nonadvancing revision",
        ));
    }
    Ok(MltSession {
        project_ref: opaque(value, "project")?,
        revision,
    })
}
fn new_session(value: &Value) -> NativeResult<MltSession> {
    let revision = opaque(value, "revision")?;
    if !sha256(&revision) {
        return Err(backend(
            "MLT project.create returned a malformed semantic revision",
        ));
    }
    Ok(MltSession {
        project_ref: opaque(value, "project")?,
        revision,
    })
}
fn only_reference(value: &Value, expected_name: &str) -> NativeResult<String> {
    // The real pinned Semwright MLT Project::new seeds its own "Main" sequence.
    // Creating a source-bound Motionwright sequence therefore returns two
    // legitimate entities. A list with more than one item is not ambiguous if
    // exactly one complete, current-revision entry has the canonical name.
    // Never choose item[0], accept duplicate names or follow an incomplete
    // cursor page: every returned native reference is revision-bound.
    let items = value
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("MLT semantic lookup contains no entity list"))?;
    let total = value
        .get("total")
        .and_then(Value::as_u64)
        .ok_or_else(|| backend("MLT semantic lookup has no bounded total"))?;
    if total == 0
        || total > 100
        || usize::try_from(total).ok() != Some(items.len())
        || !value.get("cursor").is_some_and(Value::is_null)
    {
        return Err(backend(
            "MLT semantic lookup returned incomplete or unbounded entity page",
        ));
    }
    let mut matches = items
        .iter()
        .filter(|entry| entry.get("name").and_then(Value::as_str) == Some(expected_name));
    let found = matches
        .next()
        .ok_or_else(|| backend("MLT semantic lookup lacks canonical entity name"))?;
    if matches.next().is_some() {
        return Err(backend("MLT semantic entity name is ambiguous"));
    }
    opaque(found, "reference")
}

/// Validate the *actual pinned* Semwright lossless MLT result contract.
/// The MLT profile emits FFV1 and PCM s16le. A Matroska stream may not
/// include 'nb_frames'; never pretend the native probe counted frames when
/// it only proved a bounded duration. The independent CI -count_frames gate
/// must prove the exact frame count before product acceptance.
///
/// All diagnostics are fixed strings, never provider messages or local paths.
struct LosslessExpected<'a> {
    job: &'a str,
    revision: &'a str,
    path: &'a str,
    frames: u64,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
}

fn verify_lossless_render_result(
    result: &Value,
    expected: &LosslessExpected<'_>,
) -> NativeResult<Option<u64>> {
    if result.get("state").and_then(Value::as_str) != Some("succeeded")
        || result.get("project_revision").and_then(Value::as_str) != Some(expected.revision)
        || result.get("profile").and_then(Value::as_str) != Some("lossless")
        || result.get("output").and_then(Value::as_str) != Some(expected.path)
        || result.get("job").and_then(Value::as_str) != Some(expected.job)
        || !result.get("error").is_some_and(Value::is_null)
        || result
            .get("cancellation_requested")
            .and_then(Value::as_bool)
            != Some(false)
    {
        return Err(backend(
            "MLT lossless result did not confirm the exact source-bound job",
        ));
    }
    if result.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
        || result.pointer("/artifact/path").and_then(Value::as_str) != Some(expected.path)
        || !result
            .pointer("/artifact/sha256")
            .and_then(Value::as_str)
            .is_some_and(sha256)
        || !result
            .pointer("/artifact/bytes")
            .and_then(Value::as_u64)
            .is_some_and(|size| size > 0 && size <= MAX_NATIVE_TIMELINE_BYTES)
    {
        return Err(backend(
            "MLT lossless result lacks an exact owner output artifact",
        ));
    }
    if result.pointer("/media/video").and_then(Value::as_bool) != Some(true)
        || result.pointer("/media/audio").and_then(Value::as_bool) != Some(true)
        || result.pointer("/media/width").and_then(Value::as_u64) != Some(u64::from(expected.width))
        || result.pointer("/media/height").and_then(Value::as_u64)
            != Some(u64::from(expected.height))
    {
        return Err(backend(
            "MLT lossless video and required PCM transport audio profile differ",
        ));
    }
    let codecs = result
        .pointer("/media/codecs")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("MLT lossless result has no measured codecs"))?;
    if codecs.len() != 2
        || !codecs.iter().any(|value| value.as_str() == Some("ffv1"))
        || !codecs
            .iter()
            .any(|value| value.as_str() == Some("pcm_s16le"))
    {
        return Err(backend("MLT lossless result is not exact FFV1 + PCM s16le"));
    }
    let frames_value = result
        .pointer("/media/frames")
        .ok_or_else(|| backend("MLT lossless result has no frame observation field"))?;
    let measured_frames = if frames_value.is_null() {
        // Pinned Matroska FFprobe may omit nb_frames. Exact decoded frames are
        // verified by the separate actual native E2E using -count_frames.
        None
    } else {
        let number = frames_value
            .as_u64()
            .ok_or_else(|| backend("MLT lossless frame observation is malformed"))?;
        if number != expected.frames {
            return Err(backend(
                "MLT lossless observed native frame count differs from source",
            ));
        }
        Some(number)
    };
    let duration_num = result
        .pointer("/media/duration_num")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| backend("MLT lossless result has no positive media duration"))?;
    let duration_den = result
        .pointer("/media/duration_den")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| backend("MLT lossless result has no positive media timebase"))?;
    if expected.fps_num == 0 || expected.fps_den == 0 || expected.frames == 0 {
        return Err(backend("MLT lossless expected clock is malformed"));
    }
    let measured = u128::from(duration_num) * u128::from(expected.fps_num);
    let exact = u128::from(expected.frames)
        .checked_mul(u128::from(expected.fps_den))
        .and_then(|frames| frames.checked_mul(u128::from(duration_den)))
        .ok_or_else(|| backend("MLT lossless media duration product overflow"))?;
    let one_frame = u128::from(expected.fps_den) * u128::from(duration_den);
    if measured.abs_diff(exact) > one_frame {
        return Err(backend(
            "MLT lossless duration is inconsistent with its source frame clock",
        ));
    }
    Ok(measured_frames)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltVerifiedLosslessTimeline {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub recipe_sha256: String,
    /// Source/plan frame count. Matroska probe may not report nb_frames:
    /// only independent E2E decoder counting can establish observed frames.
    pub frame_count: u64,
    pub native_frame_count_observed: Option<u64>,
    pub transport_pcm_audio: bool,
    pub native_job_ref: String,
    pub provider_revision: String,
    pub artifact_path: String,
    pub artifact_sha256: String,
    pub artifact_bytes: u64,
    pub source: MltPreparedMezzanines,
    pub evidence_scope: String,
}

impl ProductionCoordinator {
    async fn apply_mlt_edit(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        request: &str,
        command: &str,
        session: MltSession,
        extra: Value,
    ) -> NativeResult<(MltSession, Value)> {
        let mut args = extra
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("MLT edit arguments must be closed objects"))?;
        args.insert("project".into(), Value::String(session.project_ref.clone()));
        args.insert(
            "expected_revision".into(),
            Value::String(session.revision.clone()),
        );
        let response = self
            .execute(id, expected, request, command, Value::Object(args), true)
            .await?;
        let data = response_data(&response)?.clone();
        let updated = mutation_response(&session, &data)?;
        Ok((updated, data))
    }

    async fn mlt_entity_ref(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        query: MltEntityQuery<'_>,
    ) -> NativeResult<String> {
        let mut args = serde_json::Map::new();
        args.insert("project".into(), query.session.project_ref.clone().into());
        // This owner-created project has a bounded number of sequences/tracks.
        // Explicitly fetch the entire page before checking an exact name;
        // never mistake the default "Main" sequence for the created one.
        args.insert("limit".into(), json!(100));
        if let Some(sequence) = query.sequence {
            args.insert("sequence".into(), sequence.into());
        }
        let result = self
            .execute(
                id,
                expected,
                query.request_id,
                query.command,
                Value::Object(args),
                false,
            )
            .await?;
        let data = response_data(&result)?;
        if data.get("revision").and_then(Value::as_str) != Some(query.session.revision.as_str()) {
            return Err(backend(
                "MLT entity query returned an outdated native revision",
            ));
        }
        only_reference(data, query.name)
    }

    /// Semwright Driver Host actually assembles the exact input FFV1 sources
    /// into an owned lossless FFV1/PCM Matroska intermediary. The pinned
    /// curated MLT encoder creates an audio stream, regardless of whether
    /// a voice or music source was mixed. Never mislabel it video-only.
    /// Human-approved audio and final H.264/AAC publication are later gates.
    pub async fn assemble_native_mlt_video_timeline(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<MltVerifiedLosslessTimeline> {
        if request_id.is_empty()
            || request_id.len() > 54
            || !request_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
        {
            return Err(invalid(
                "Native MLT assembly requires a bounded stable request identity",
            ));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
            || self.client.connection().resource != project.resource_key()
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native MLT assembly source or production connection is stale",
            ));
        }
        let checked = preflight_multi_segment_mlt(
            &project,
            deliverable_id,
            options,
            rendered,
            &self.client.connection().output_root,
        )?;
        let recipe: MltNativeVideoRecipe = mlt_native_video_recipe(&checked)?;
        let sources = self
            .prepare_multi_segment_ffv1(
                project_id,
                expected,
                &format!("{request_id}-prep"),
                deliverable_id,
                options,
                rendered,
            )
            .await?;
        if sources.input_sha256 != recipe.input_sha256
            || sources.total_frames != recipe.total_frames
            || sources.verified_video_segments.len() != recipe.clips.len()
        {
            return Err(backend(
                "MLT source intermediates do not match the exact semantic recipe",
            ));
        }
        let create = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:project-create"),
                "driver.mlt-video.project.create",
                json!({"profile": recipe.provider_profile}),
                true,
            )
            .await?;
        let mut session = new_session(response_data(&create)?)?;
        let (after_sequence, sequence_result) = self
            .apply_mlt_edit(
                project_id,
                expected,
                &format!("{request_id}:mlt:sequence-create"),
                "driver.mlt-video.sequence.create",
                session,
                json!({"name": recipe.sequence_name}),
            )
            .await?;
        let sequence = created(&sequence_result, "sequence")?;
        session = after_sequence;
        let (after_track, track_result) = self
            .apply_mlt_edit(
                project_id,
                expected,
                &format!("{request_id}:mlt:track-create"),
                "driver.mlt-video.track.create",
                session,
                json!({
                    "sequence": sequence, "name": recipe.video_track_name,
                    "kind": "video"
                }),
            )
            .await?;
        let track = created(&track_result, "track")?;
        if track.is_empty() {
            return Err(backend("MLT video track was not created"));
        }
        session = after_track;
        for (index, (segment, clip)) in sources
            .verified_video_segments
            .iter()
            .zip(&recipe.clips)
            .enumerate()
        {
            if segment.segment_id != clip.segment_id
                || segment.output_start_frame != clip.start_frame
                || segment.frame_count != clip.source_out_exclusive
                || segment.artifact_path != clip.ffv1_output_path
                || !sha256(&segment.artifact_sha256)
            {
                return Err(backend(
                    "Native verified FFV1 segment differs from canonical clip",
                ));
            }
            let (imported, asset_data) = self
                .apply_mlt_edit(
                    project_id,
                    expected,
                    &format!("{request_id}:mlt:asset:{index:02}"),
                    "driver.mlt-video.asset.import",
                    session,
                    json!({
                        "kind":"file", "name": clip.edit_asset_name,
                        "root":"output", "path": segment.artifact_path,
                    }),
                )
                .await?;
            let asset = created(&asset_data, "asset")?;
            session = imported;
            let sequence = self
                .mlt_entity_ref(
                    project_id,
                    expected,
                    MltEntityQuery {
                        request_id: &format!("{request_id}:mlt:sequence-ref:{index:02}"),
                        command: "driver.mlt-video.sequence.list",
                        session: &session,
                        sequence: None,
                        name: &recipe.sequence_name,
                    },
                )
                .await?;
            let track = self
                .mlt_entity_ref(
                    project_id,
                    expected,
                    MltEntityQuery {
                        request_id: &format!("{request_id}:mlt:track-ref:{index:02}"),
                        command: "driver.mlt-video.track.list",
                        session: &session,
                        sequence: Some(&sequence),
                        name: &recipe.video_track_name,
                    },
                )
                .await?;
            let (inserted, clip_data) = self
                .apply_mlt_edit(
                    project_id,
                    expected,
                    &format!("{request_id}:mlt:clip:{index:02}"),
                    "driver.mlt-video.clip.insert",
                    session,
                    json!({
                        "sequence": sequence, "track": track, "asset": asset,
                        "start": clip.start_frame, "source_in": clip.source_in,
                        "source_out": clip.source_out_exclusive,
                        "name": clip.edit_clip_name, "ripple": false
                    }),
                )
                .await?;
            let _ = created(&clip_data, "clip")?;
            if clip_data.get("after_frames").and_then(Value::as_u64)
                != Some(clip.start_frame + clip.source_out_exclusive)
            {
                return Err(backend(
                    "MLT semantic edit produced an incorrect timeline frame count",
                ));
            }
            session = inserted;
        }
        let sequence = self
            .mlt_entity_ref(
                project_id,
                expected,
                MltEntityQuery {
                    request_id: &format!("{request_id}:mlt:final-sequence-ref"),
                    command: "driver.mlt-video.sequence.list",
                    session: &session,
                    sequence: None,
                    name: &recipe.sequence_name,
                },
            )
            .await?;
        let duration = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:duration"),
                "driver.mlt-video.sequence.duration",
                json!({"project": session.project_ref, "sequence":sequence}),
                false,
            )
            .await?;
        let duration = response_data(&duration)?;
        if duration.get("frames").and_then(Value::as_u64) != Some(recipe.total_frames)
            || duration.get("fps_num").and_then(Value::as_u64)
                != Some(u64::from(recipe.provider_profile.fps_num))
            || duration.get("fps_den").and_then(Value::as_u64)
                != Some(u64::from(recipe.provider_profile.fps_den))
        {
            return Err(backend(
                "MLT semantic sequence duration diverged from the canonical cut",
            ));
        }
        let render_args = json!({
            "project": session.project_ref,
            "sequence": sequence, "profile": recipe.lossless_sequence_profile,
            "output": recipe.lossless_sequence_path,
        });
        let planned = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:render-plan"),
                "driver.mlt-video.render.plan",
                render_args.clone(),
                false,
            )
            .await?;
        let plan = response_data(&planned)?;
        if plan.get("runnable").and_then(Value::as_bool) != Some(true)
            || plan.get("frames").and_then(Value::as_u64) != Some(recipe.total_frames)
            || plan.get("project_revision").and_then(Value::as_str)
                != Some(session.revision.as_str())
            || plan.get("output_root").and_then(Value::as_str) != Some("output")
            || plan.get("output_path").and_then(Value::as_str)
                != Some(recipe.lossless_sequence_path.as_str())
            || plan
                .get("missing_services")
                .and_then(Value::as_array)
                .is_none_or(|missing| !missing.is_empty())
        {
            return Err(backend(
                "MLT native semantic render plan failed exact source compatibility",
            ));
        }
        let mut started_args = render_args.as_object().unwrap().clone();
        started_args.insert("expected_revision".into(), session.revision.clone().into());
        let started = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:render-start"),
                "driver.mlt-video.render.start",
                Value::Object(started_args),
                true,
            )
            .await?;
        let started = response_data(&started)?;
        let job_ref = opaque(started, "job")?;
        let deadline = Instant::now() + Duration::from_secs(MLT_TIMELINE_POLL_SECS);
        let mut succeeded = started.get("state").and_then(Value::as_str) == Some("succeeded");
        let mut poll = 0u32;
        while !succeeded {
            if Instant::now() >= deadline {
                return Err(Error::new(
                    ErrorCode::Timeout,
                    "MLT native sequence render exceeded the bounded driver polling window",
                ));
            }
            sleep(Duration::from_millis(MLT_TIMELINE_POLL_MS)).await;
            poll += 1;
            let status = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mlt:status:{poll}"),
                    "driver.mlt-video.render.status",
                    json!({"job":job_ref}),
                    false,
                )
                .await?;
            let status = response_data(&status)?;
            match status.get("state").and_then(Value::as_str) {
                Some("succeeded") => succeeded = true,
                Some("queued" | "starting" | "running") => {}
                _ => {
                    return Err(backend(
                        "Semwright Driver Host reported failed, cancelled, unknown or malformed MLT output",
                    ));
                }
            }
        }
        let result = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:render-result"),
                "driver.mlt-video.render.result",
                json!({"job":job_ref}),
                false,
            )
            .await?;
        let result = response_data(&result)?;
        let native_frame_count_observed = verify_lossless_render_result(
            result,
            &LosslessExpected {
                job: &job_ref,
                revision: &session.revision,
                path: &recipe.lossless_sequence_path,
                frames: recipe.total_frames,
                width: recipe.provider_profile.width,
                height: recipe.provider_profile.height,
                fps_num: recipe.provider_profile.fps_num,
                fps_den: recipe.provider_profile.fps_den,
            },
        )?;
        let artifact_sha256 = required_string(
            result,
            "/artifact/sha256",
            "MLT lossless intermediary has no SHA-256",
        )?;
        let source = verify_output_artifact(
            &self.client.connection().output_root,
            &recipe.lossless_sequence_path,
            &artifact_sha256,
            MAX_NATIVE_TIMELINE_BYTES,
        )?;
        let size = fs::metadata(source)
            .map_err(|_| backend("Native MLT lossless intermediary became inaccessible"))?
            .len();
        if result.pointer("/artifact/bytes").and_then(Value::as_u64) != Some(size) {
            return Err(backend(
                "MLT lossless artifact byte count differs from native receipt",
            ));
        }
        let latest = self.service.project(project_id).map_err(storage_error)?;
        if latest.generation != project.generation || latest.revision != project.revision {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project changed during native multisegment timeline rendering",
            ));
        }
        // The returned owner artifact is source-verified even if the driver
        // cannot close its short-lived in-memory edit project immediately.
        let close = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:close"),
                "driver.mlt-video.project.close",
                json!({"project":session.project_ref,"expected_revision":session.revision}),
                true,
            )
            .await?;
        if response_data(&close)?
            .get("closed")
            .and_then(Value::as_bool)
            != Some(true)
        {
            return Err(backend(
                "MLT project could not be closed after verified sequence render",
            ));
        }
        Ok(MltVerifiedLosslessTimeline {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            recipe_sha256: recipe.input_sha256,
            frame_count: recipe.total_frames,
            native_frame_count_observed,
            transport_pcm_audio: true,
            native_job_ref: job_ref,
            provider_revision: session.revision,
            artifact_path: recipe.lossless_sequence_path,
            artifact_sha256,
            artifact_bytes: size,
            source: sources,
            evidence_scope: "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
                .into(),
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_forged_revision_and_multientity_or_nonadvancing_edits() {
        let one = MltSession {
            project_ref: "owner-ref".into(),
            revision: "a".repeat(64),
        };
        let mut valid = json!({
            "applied": true, "expected_revision": "a".repeat(64),
            "resulting_revision":"b".repeat(64),
            "support":"SAFE_ROUNDTRIP", "project":"new-owner-ref",
            "created_refs":[{"kind":"sequence","reference":"only-sequence"}]
        });
        assert_eq!(
            mutation_response(&one, &valid).unwrap().revision,
            "b".repeat(64)
        );
        assert_eq!(created(&valid, "sequence").unwrap(), "only-sequence");
        valid["resulting_revision"] = json!("a".repeat(64));
        assert!(mutation_response(&one, &valid).is_err());
        valid["resulting_revision"] = json!("b".repeat(64));
        valid["expected_revision"] = json!("c".repeat(64));
        assert!(mutation_response(&one, &valid).is_err());
        valid["expected_revision"] = json!("a".repeat(64));
        valid["created_refs"]
            .as_array_mut()
            .unwrap()
            .push(json!({"kind":"sequence","reference":"duplicate"}));
        assert!(created(&valid, "sequence").is_err());
    }
    #[test]
    fn native_lossless_profile_is_ffv1_and_pcm_not_video_only_and_may_omit_nb_frames() {
        let revision = "a".repeat(64);
        let path = "owner-lossless.mkv";
        let mut result = json!({
            "job":"render:owner-job",
            "project_revision":revision,
            "profile":"lossless",
            "output":path,
            "state":"succeeded",
            "error":null,
            "cancellation_requested":false,
            "artifact":{"root":"output","path":path,"sha256":"b".repeat(64),"bytes":8192},
            "media":{"width":1280,"height":720,"frames":null,
                "duration_num":1100,"duration_den":1000,
                "video":true,"audio":true,"codecs":["ffv1","pcm_s16le"]}
        });
        let verify = |receipt: &Value| {
            verify_lossless_render_result(
                receipt,
                &LosslessExpected {
                    job: "render:owner-job",
                    revision: &revision,
                    path,
                    frames: 33,
                    width: 1280,
                    height: 720,
                    fps_num: 30,
                    fps_den: 1,
                },
            )
        };
        assert_eq!(
            verify(&result).unwrap(),
            None,
            "Matroska ffprobe may not encode nb_frames; external exact-frame decoder is mandatory"
        );
        result["media"]["frames"] = json!(33);
        assert_eq!(verify(&result).unwrap(), Some(33));
        result["media"]["frames"] = json!(32);
        assert!(verify(&result).unwrap_err().message.contains("frame count"));
        result["media"]["frames"] = Value::Null;
        result["media"]["audio"] = json!(false);
        assert!(verify(&result).unwrap_err().message.contains("PCM"));
        result["media"]["audio"] = json!(true);
        result["media"]["codecs"] = json!(["ffv1", "aac"]);
        assert!(verify(&result).unwrap_err().message.contains("PCM s16le"));
        result["media"]["codecs"] = json!(["ffv1", "pcm_s16le"]);
        result["artifact"]["path"] = json!("/home/private/foreign.mkv");
        assert!(verify(&result).is_err());
        result["artifact"]["path"] = json!(path);
        result["media"]["duration_num"] = json!(800);
        assert!(verify(&result).unwrap_err().message.contains("duration"));
        result["media"]["duration_num"] = json!(1100);
        result["media"]["height"] = json!(1080);
        assert!(verify(&result).is_err());
        result["media"]["height"] = json!(720);
        result["project_revision"] = json!("c".repeat(64));
        assert!(verify(&result).is_err());
        result["project_revision"] = json!(revision);
        assert_eq!(verify(&result).unwrap(), None);
    }

    #[test]
    fn only_unique_owner_returned_identity_is_reusable() {
        let ok = json!({
            "total":2,"cursor":null,"items":[
                {"name":"Default Video","reference":"foreign-default"},
                {"name":"Motionwright native visual segments","reference":"granted-track"}
            ]
        });
        assert_eq!(
            only_reference(&ok, "Motionwright native visual segments").unwrap(),
            "granted-track"
        );
        assert!(only_reference(&ok, "foreign-track").is_err());
        let mut bad = ok;
        bad["total"] = json!(3);
        assert!(only_reference(&bad, "Motionwright native visual segments").is_err());
        bad["total"] = json!(2);
        bad["cursor"] = json!("page-2");
        assert!(only_reference(&bad, "Motionwright native visual segments").is_err());
        bad["cursor"] = Value::Null;
        bad["items"][0]["name"] = json!("Motionwright native visual segments");
        assert!(only_reference(&bad, "Motionwright native visual segments").is_err());
        bad["items"][0]["name"] = json!("Default Video");
        bad["items"][1]["reference"] = json!("");
        assert!(only_reference(&bad, "Motionwright native visual segments").is_err());
    }
}
