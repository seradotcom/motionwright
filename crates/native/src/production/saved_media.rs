//! Read-only reopening of completed local masters, independent of optional QC.
//! The original mux intent/result carry bounded private selectors. They never
//! restore a production budget, a native grant or an export capability.
use super::*;
use motionwright_domain::Project;
use semwright_native_sdk::types::Execution;
use std::collections::HashSet;

mod delivery;
pub use delivery::VerifiedSavedMasterDelivery;

const COMMAND: &str = "driver.mlt-video.av.mux";
const CONTEXT_KEY: &str = "original_media_reopen";
const CONTEXT_SCHEMA: &str = "motionwright.original-media-reopen/1";
const HYBRID_CONTEXT_SCHEMA: &str = "motionwright.original-hybrid-media-reopen/1";
const MAX_HINTS: usize = 32;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(tag = "status", content = "clock", rename_all = "snake_case")]
pub enum SavedHybridTiming {
    Observed(super::HybridMasterStreamClock),
    Unavailable,
}

pub struct VerifiedOriginalMasterReview {
    pub(super) project_id: Uuid,
    pub(super) stamp: RevisionStamp,
    pub(super) deliverable_id: Uuid,
    pub(super) owner_root: PathBuf,
    pub(super) relative_path: String,
    pub(super) sha256: String,
    pub(super) bytes: u64,
    pub(super) clock: (u32, u32, u64),
    pub(super) saved_hybrid_timing: Option<SavedHybridTiming>,
}

impl VerifiedOriginalMasterReview {
    pub fn project_id(&self) -> Uuid {
        self.project_id
    }
    pub fn stamp(&self) -> &RevisionStamp {
        &self.stamp
    }
    pub fn deliverable_id(&self) -> Uuid {
        self.deliverable_id
    }
    pub fn artifact(&self) -> (&Path, &str, &str, u64) {
        (
            &self.owner_root,
            &self.relative_path,
            &self.sha256,
            self.bytes,
        )
    }
    pub fn clock(&self) -> (u32, u32, u64) {
        self.clock
    }
    pub fn saved_hybrid_timing(&self) -> Option<SavedHybridTiming> {
        self.saved_hybrid_timing
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SavedMasterReceiptContext {
    schema: String,
    project_sha256: String,
    deliverable_id: Uuid,
    connection_identity: String,
    args: Value,
}

fn source_digest(project: &Project) -> NativeResult<String> {
    request_digest("motionwright.original-media.project", &json!(project))
}

fn relative_media_path(value: &Value) -> bool {
    value.as_str().is_some_and(|path| {
        !path.is_empty()
            && path.len() <= 1024
            && !path.contains(['\\', ':'])
            && !path.chars().any(char::is_control)
            && path
                .split('/')
                .all(|part| !part.is_empty() && part != "." && part != "..")
    })
}

impl SavedMasterReceiptContext {
    pub(super) fn new(
        coordinator: &ProductionCoordinator,
        project: &Project,
        deliverable_id: Uuid,
        args: &Value,
    ) -> NativeResult<Self> {
        Self::for_source(coordinator, project, deliverable_id, args, CONTEXT_SCHEMA)
    }

    fn for_source(
        coordinator: &ProductionCoordinator,
        project: &Project,
        deliverable_id: Uuid,
        args: &Value,
        schema: &str,
    ) -> NativeResult<Self> {
        let context = Self {
            schema: schema.into(),
            project_sha256: source_digest(project)?,
            deliverable_id,
            connection_identity: coordinator.client.connection().identity()?,
            args: args.clone(),
        };
        context.check_current(coordinator, project)?;
        Ok(context)
    }

    fn maximum_bytes(&self) -> NativeResult<u64> {
        match self.schema.as_str() {
            CONTEXT_SCHEMA => Ok(MLT_MASTER_MAX_BYTES),
            HYBRID_CONTEXT_SCHEMA => Ok(super::hybrid_mlt::MASTER_MAX_BYTES),
            _ => Err(invalid("Original media context schema is unsupported")),
        }
    }

    fn source_video_maximum_bytes(&self) -> NativeResult<u64> {
        self.maximum_bytes()?;
        Ok(if self.schema == HYBRID_CONTEXT_SCHEMA {
            super::hybrid_mlt::MEDIA_MAX_BYTES
        } else {
            MLT_MEZZANINE_MAX_BYTES
        })
    }

    fn from_receipt(receipt: &ProductionReceipt) -> NativeResult<Option<Self>> {
        let Some(value) = receipt.payload.get(CONTEXT_KEY) else {
            return Ok(None);
        };
        let object = value
            .as_object()
            .ok_or_else(|| invalid("Original media context is invalid"))?;
        let args = object
            .get("args")
            .and_then(Value::as_object)
            .ok_or_else(|| invalid("Original media context has no closed source"))?;
        if object.len() != 5
            || args.len() != 16
            || object.iter().any(|(key, value)| {
                key != "args" && value.as_str().is_none_or(|text| text.len() > 96)
            })
            || args.values().any(|value| match value {
                Value::String(text) => text.len() > 1024,
                Value::Number(number) => number.as_u64().is_none(),
                _ => true,
            })
        {
            return Err(invalid("Original media context exceeds its closed bounds"));
        }
        let context: Self = serde_json::from_value(value.clone())
            .map_err(|_| invalid("Original media context fields are invalid"))?;
        if context.maximum_bytes().is_err()
            || !is_sha256(&context.project_sha256)
            || !is_sha256(&context.connection_identity)
            || context.deliverable_id.is_nil()
        {
            return Err(invalid("Original media context identity is invalid"));
        }
        Ok(Some(context))
    }

    pub(super) fn payload(&self) -> Value {
        json!({
            "schema": self.schema, "project_sha256": self.project_sha256,
            "deliverable_id": self.deliverable_id, "connection_identity": self.connection_identity,
            "args": self.args,
        })
    }

    pub(super) fn check_current(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
    ) -> NativeResult<()> {
        if coordinator.client.connection().resource != project.resource_key() {
            return Err(invalid("Original media owner belongs to another project"));
        }
        self.check_source(
            project,
            &source_digest(project)?,
            &coordinator.client.connection().identity()?,
        )
    }

    fn check_source(
        &self,
        project: &Project,
        project_digest: &str,
        connection_identity: &str,
    ) -> NativeResult<()> {
        let profile = project
            .deliverables
            .iter()
            .find(|p| p.id == self.deliverable_id)
            .ok_or_else(|| invalid("Original media output profile is unavailable"))?;
        const KEYS: [&str; 16] = [
            "video_root",
            "video_path",
            "video_sha256",
            "audio_root",
            "audio_path",
            "audio_sha256",
            "width",
            "height",
            "fps_num",
            "fps_den",
            "frame_count",
            "sample_rate",
            "channels",
            "profile",
            "output_path",
            "max_bytes",
        ];
        let args = self
            .args
            .as_object()
            .ok_or_else(|| invalid("Original media source is invalid"))?;
        let same_rate = match (
            args.get("fps_num").and_then(Value::as_u64),
            args.get("fps_den").and_then(Value::as_u64),
        ) {
            (Some(num), Some(den))
                if (1..=u64::from(u32::MAX)).contains(&num)
                    && (1..=u64::from(u32::MAX)).contains(&den)
                    && profile.frame_rate.num > 0
                    && profile.frame_rate.den > 0 =>
            {
                i128::from(num) * i128::from(profile.frame_rate.den)
                    == i128::from(den) * i128::from(profile.frame_rate.num)
            }
            _ => false,
        };
        let maximum = self.maximum_bytes()?;
        let authored_hybrid_frames = if self.schema == HYBRID_CONTEXT_SCHEMA {
            let rate = Rate::new(30, 1).map_err(|_| invalid("Original hybrid rate is invalid"))?;
            super::hybrid_budget::authored_hybrid_ranges(project, rate)?
                .last()
                .map(|range| range.end)
        } else {
            None
        };
        if (self.schema == HYBRID_CONTEXT_SCHEMA
            && (args.get("frame_count").and_then(Value::as_u64) != authored_hybrid_frames
                || args.get("width").and_then(Value::as_u64) != Some(1920)
                || args.get("height").and_then(Value::as_u64) != Some(1080)
                || args.get("fps_num").and_then(Value::as_u64) != Some(30)
                || args.get("fps_den").and_then(Value::as_u64) != Some(1)))
            || self.project_sha256 != project_digest
            || self.connection_identity != connection_identity
            || args.len() != KEYS.len()
            || !KEYS.iter().all(|key| args.contains_key(*key))
            || args["video_root"] != "output"
            || args["audio_root"] != "output"
            || args["profile"] != "h264-aac-mp4"
            || args["sample_rate"] != 48_000
            || args["channels"] != 2
            || args["max_bytes"].as_u64() != Some(maximum)
            || args["width"].as_u64() != Some(u64::from(profile.width))
            || args["height"].as_u64() != Some(u64::from(profile.height))
            || !same_rate
            || args["frame_count"]
                .as_u64()
                .is_none_or(|n| !(1..=36_000).contains(&n))
            || !["video_path", "audio_path", "output_path"]
                .iter()
                .all(|key| relative_media_path(&args[*key]))
            || !["video_sha256", "audio_sha256"]
                .iter()
                .all(|key| args[*key].as_str().is_some_and(is_sha256))
        {
            return Err(invalid(
                "Original media no longer matches its saved source and owner",
            ));
        }
        Ok(())
    }

    fn original<'a>(&self, receipt: &'a ProductionReceipt) -> NativeResult<&'a Value> {
        if receipt.command != COMMAND
            || receipt.stage != "completed"
            || receipt.request_sha256 != request_digest(COMMAND, &self.args)?
        {
            return Err(invalid("Original media is not the selected completed mux"));
        }
        let result = receipt
            .payload
            .get("result")
            .ok_or_else(|| backend("Original media has no retained native result"))?;
        if result.get("provenance_checked").and_then(Value::as_bool) != Some(true) {
            return Err(backend("Original media has no checked native provenance"));
        }
        let execution: Execution = serde_json::from_value(
            result
                .get("execution")
                .cloned()
                .ok_or_else(|| backend("Original media has no native execution"))?,
        )
        .map_err(|_| backend("Original media execution is malformed"))?;
        let provenance = execution
            .provenance
            .as_ref()
            .ok_or_else(|| backend("Original media provider is absent"))?;
        if execution.dry_run
            || provenance.untrusted_metadata
            || provenance.provider != "driver:mlt-video"
            || provenance.source != SourceKind::Driver
            || provenance.provider_generation.is_none()
            || !is_sha256(&provenance.descriptor_sha256)
            || provenance.execution_provider.as_deref() != Some("driver:mlt-video")
            || receipt
                .payload
                .get("broker_request_id")
                .and_then(Value::as_str)
                .is_none_or(|s| s.is_empty() || s.len() > 256 || s.chars().any(char::is_control))
        {
            return Err(backend(
                "Original media does not identify its actual native provider",
            ));
        }
        let data = result
            .get("data")
            .ok_or_else(|| backend("Original media data is absent"))?;
        let maximum = self.maximum_bytes()?;
        for pointer in ["/artifact/bytes", "/decoded_audio/bytes"] {
            if data
                .pointer(pointer)
                .and_then(Value::as_u64)
                .is_none_or(|bytes| !(1..=maximum).contains(&bytes))
            {
                return Err(invalid(
                    "Original mux result exceeds its selected byte budget",
                ));
            }
        }
        let a = &self.args;
        super::mlt_multisegment_av::verify_exact_native_mux(
            data,
            &super::mlt_multisegment_av::MuxExpected {
                frames: a["frame_count"]
                    .as_u64()
                    .ok_or_else(|| invalid("Original frame count is invalid"))?,
                width: u32::try_from(
                    a["width"]
                        .as_u64()
                        .ok_or_else(|| invalid("Original width is invalid"))?,
                )
                .map_err(|_| invalid("Original width is invalid"))?,
                height: u32::try_from(
                    a["height"]
                        .as_u64()
                        .ok_or_else(|| invalid("Original height is invalid"))?,
                )
                .map_err(|_| invalid("Original height is invalid"))?,
                fps_num: u32::try_from(
                    a["fps_num"]
                        .as_u64()
                        .ok_or_else(|| invalid("Original rate is invalid"))?,
                )
                .map_err(|_| invalid("Original rate is invalid"))?,
                fps_den: u32::try_from(
                    a["fps_den"]
                        .as_u64()
                        .ok_or_else(|| invalid("Original rate is invalid"))?,
                )
                .map_err(|_| invalid("Original rate is invalid"))?,
                source_video: a["video_sha256"]
                    .as_str()
                    .ok_or_else(|| invalid("Original video is absent"))?,
                source_audio: a["audio_sha256"]
                    .as_str()
                    .ok_or_else(|| invalid("Original audio is absent"))?,
                output_path: a["output_path"]
                    .as_str()
                    .ok_or_else(|| invalid("Original path is absent"))?,
            },
        )?;
        Ok(data)
    }
}

/// Discovery hints have no read, Native, QC or export authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SavedMasterReceiptHint {
    pub mux_receipt_id: Uuid,
    pub master_sha256: String,
    pub frame_count: u64,
    pub fps_num: u32,
    pub fps_den: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct SavedMasterReceiptInventory {
    pub schema: &'static str,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub items: Vec<SavedMasterReceiptHint>,
    pub truncated: bool,
}

fn hint(
    receipt: &ProductionReceipt,
    context: &SavedMasterReceiptContext,
) -> NativeResult<SavedMasterReceiptHint> {
    let data = context.original(receipt)?;
    let sha = data
        .pointer("/artifact/sha256")
        .and_then(Value::as_str)
        .filter(|s| is_sha256(s))
        .ok_or_else(|| backend("Original master identity is absent"))?;
    Ok(SavedMasterReceiptHint {
        mux_receipt_id: receipt.id,
        master_sha256: sha.into(),
        frame_count: context.args["frame_count"]
            .as_u64()
            .ok_or_else(|| invalid("Original clock is absent"))?,
        fps_num: context.args["fps_num"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or_else(|| invalid("Original clock is invalid"))?,
        fps_den: context.args["fps_den"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or_else(|| invalid("Original clock is invalid"))?,
    })
}

fn current_project(
    coordinator: &ProductionCoordinator,
    id: Uuid,
    stamp: &RevisionStamp,
) -> NativeResult<Project> {
    coordinator.client.connection().validate()?;
    let project = coordinator.service.project(id).map_err(storage_error)?;
    if project.stamp() != *stamp
        || coordinator.client.connection().resource != project.resource_key()
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Saved original media source changed",
        ));
    }
    Ok(project)
}

impl ProductionCoordinator {
    pub(super) async fn execute_master_mux(
        &self,
        project: &Project,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        args: Value,
    ) -> NativeResult<Value> {
        let context = SavedMasterReceiptContext::new(self, project, deliverable_id, &args)?;
        self.execute_with_receipt_origin_context(
            project.id,
            expected,
            request_id,
            (COMMAND, true),
            args,
            Some(ReceiptDispatchContext::SavedMaster(context)),
        )
        .await
        .map(ReceiptExecutionOutcome::into_legacy_value)
    }

    /// Only the sealed original hybrid continuation can attach this context.
    /// Existing dispatch, grants, root accounting and anti-replay stay shared.
    pub(super) async fn execute_hybrid_master_mux(
        &self,
        sources: &super::hybrid_budget::HybridNativeSources,
        args: Value,
    ) -> NativeResult<Value> {
        let context = SavedMasterReceiptContext::for_source(
            self,
            &sources.project,
            sources.deliverable_id,
            &args,
            HYBRID_CONTEXT_SCHEMA,
        )?;
        self.execute_with_receipt_origin_context(
            sources.project.id,
            &sources.expected,
            &format!("{}:mlt:mux", sources.request_prefix),
            (COMMAND, true),
            args,
            Some(ReceiptDispatchContext::SavedMaster(context)),
        )
        .await
        .map(ReceiptExecutionOutcome::into_legacy_value)
    }

    pub fn saved_master_receipt_inventory(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
    ) -> NativeResult<SavedMasterReceiptInventory> {
        let project = current_project(self, project_id, expected)?;
        if !project.deliverables.iter().any(|p| p.id == deliverable_id) {
            return Err(invalid("Saved media output profile is unavailable"));
        }
        let project_digest = source_digest(&project)?;
        let connection_identity = self.client.connection().identity()?;
        let mut inventory = SavedMasterReceiptInventory {
            schema: "motionwright.saved-master-inventory/1",
            project_id,
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            items: Vec::new(),
            truncated: false,
        };
        let mut seen = HashSet::new();
        audio_analysis::receipt_history::visit_history(
            &self.service,
            project_id,
            true,
            |receipt| {
                if receipt.command == COMMAND
                    && (receipt.request_id.is_empty()
                        || receipt.request_id.len() > 256
                        || receipt.request_id.chars().any(char::is_control))
                {
                    return Err(invalid(
                        "Original mux request identity exceeds discovery bounds",
                    ));
                }
                if receipt.command != COMMAND
                    || !seen.insert(receipt.request_id.clone())
                    || receipt.generation != project.generation
                    || receipt.revision != project.revision
                    || receipt.stage != "completed"
                {
                    return Ok(());
                }
                let Some(context) = SavedMasterReceiptContext::from_receipt(receipt)? else {
                    return Ok(());
                };
                if context.deliverable_id != deliverable_id {
                    return Ok(());
                }
                context.check_source(&project, &project_digest, &connection_identity)?;
                let item = hint(receipt, &context)?;
                if inventory.items.len() == MAX_HINTS {
                    inventory.truncated = true;
                } else {
                    inventory.items.push(item);
                }
                Ok(())
            },
        )?;
        current_project(self, project_id, expected)?;
        Ok(inventory)
    }

    pub fn recover_saved_master_review(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        mux_receipt_id: Uuid,
        deliverable_id: Uuid,
        expected_master_sha256: &str,
    ) -> NativeResult<VerifiedOriginalMasterReview> {
        let (mut original, receipt, context) = self.recover_saved_master_parts(
            project_id,
            expected,
            mux_receipt_id,
            deliverable_id,
            expected_master_sha256,
        )?;
        if context.schema == HYBRID_CONTEXT_SCHEMA {
            let result = context.original(&receipt)?;
            if result.get("color_observation").is_some() {
                let original_video_path = context.args["video_path"]
                    .as_str()
                    .ok_or_else(|| invalid("Original video is absent"))?;
                original.saved_hybrid_timing = Some(
                    match super::hybrid_mlt::read_saved_hybrid_master_stream_clock(
                        &original.owner_root,
                        result,
                        original_video_path,
                        original.clock.2,
                    ) {
                        Ok(clock) => SavedHybridTiming::Observed(clock),
                        Err(_) => SavedHybridTiming::Unavailable,
                    },
                );
                let latest = current_project(self, project_id, expected)?;
                context.check_current(self, &latest)?;
                audio_analysis::receipt_history::verify_selected_atomic_history(
                    &self.service,
                    &receipt,
                )?;
            }
        }
        Ok(original)
    }

    fn recover_saved_master_parts(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        mux_receipt_id: Uuid,
        deliverable_id: Uuid,
        expected_master_sha256: &str,
    ) -> NativeResult<(
        VerifiedOriginalMasterReview,
        ProductionReceipt,
        SavedMasterReceiptContext,
    )> {
        if !is_sha256(expected_master_sha256) || mux_receipt_id.is_nil() {
            return Err(invalid("Saved media selector is invalid"));
        }
        let project = current_project(self, project_id, expected)?;
        let mut selected = None;
        audio_analysis::receipt_history::visit_history(
            &self.service,
            project_id,
            false,
            |receipt| {
                if receipt.id == mux_receipt_id {
                    selected = Some(receipt.clone());
                }
                Ok(())
            },
        )?;
        let selected = selected.ok_or_else(|| invalid("Saved original mux is unavailable"))?;
        if selected.project_id != project_id
            || selected.generation != expected.generation
            || selected.revision != expected.revision
            || selected.command != COMMAND
            || selected.stage != "completed"
        {
            return Err(invalid("Saved mux belongs to another source or operation"));
        }
        audio_analysis::receipt_history::verify_selected_atomic_history(&self.service, &selected)?;
        let context = SavedMasterReceiptContext::from_receipt(&selected)?
            .ok_or_else(|| invalid("This historical mux has no original reopen metadata"))?;
        if context.deliverable_id != deliverable_id {
            return Err(invalid("Saved mux belongs to another output profile"));
        }
        context.check_current(self, &project)?;
        let mut dispatches = 0;
        let mut completions = 0;
        audio_analysis::receipt_history::visit_history(
            &self.service,
            project_id,
            false,
            |receipt| {
                if receipt.request_id != selected.request_id {
                    return Ok(());
                }
                if SavedMasterReceiptContext::from_receipt(receipt)?.as_ref() != Some(&context) {
                    return Err(invalid(
                        "Saved mux source changed between original intent and result",
                    ));
                }
                match receipt.stage.as_str() {
                    "dispatching"
                        if receipt.payload.get("connection").and_then(Value::as_str)
                            == Some(context.connection_identity.as_str())
                            && receipt.payload.get("mutation").and_then(Value::as_bool)
                                == Some(true) =>
                    {
                        dispatches += 1
                    }
                    "completed" if receipt == &selected => completions += 1,
                    _ => {
                        return Err(invalid(
                            "Saved mux history does not contain one original completion",
                        ));
                    }
                }
                Ok(())
            },
        )?;
        if dispatches != 1 || completions != 1 {
            return Err(invalid("Saved mux original custody is incomplete"));
        }
        let data = context.original(&selected)?;
        let artifact = data
            .get("artifact")
            .ok_or_else(|| backend("Saved original artifact is absent"))?;
        let path = artifact
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("Saved original path is absent"))?;
        let sha = artifact
            .get("sha256")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("Saved original hash is absent"))?;
        let maximum = context.maximum_bytes()?;
        let bytes = artifact
            .get("bytes")
            .and_then(Value::as_u64)
            .filter(|n| (12..=maximum).contains(n))
            .ok_or_else(|| invalid("Saved original length is invalid"))?;
        if sha != expected_master_sha256 {
            return Err(invalid("Saved original hash differs from selection"));
        }
        let verified =
            verify_output_artifact(&self.client.connection().output_root, path, sha, maximum)?;
        if fs::metadata(verified)
            .map_err(|_| backend("Saved original file is unavailable"))?
            .len()
            != bytes
        {
            return Err(invalid("Saved original length changed"));
        }
        let observation = hint(&selected, &context)?;
        let latest = current_project(self, project_id, expected)?;
        context.check_current(self, &latest)?;
        audio_analysis::receipt_history::verify_selected_atomic_history(&self.service, &selected)?;
        Ok((
            VerifiedOriginalMasterReview {
                project_id,
                stamp: expected.clone(),
                deliverable_id,
                owner_root: self.client.connection().output_root.clone(),
                relative_path: path.into(),
                sha256: sha.into(),
                bytes,
                clock: (
                    observation.fps_num,
                    observation.fps_den,
                    observation.frame_count,
                ),
                saved_hybrid_timing: None,
            },
            selected,
            context,
        ))
    }
}

#[cfg(test)]
mod tests;
