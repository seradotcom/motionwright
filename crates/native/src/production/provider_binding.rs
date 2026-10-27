//! Closed font and audio handoffs through the same canonical provider binding.
//! A mount observation is not a local write grant or a runtime-profile admission.
use super::{
    BrokerResult, Error, ErrorCode, ExpectedAuthority, NativeResult, ProductionClient,
    ProductionCoordinator, ReceiptDispatchContext, RevisionStamp, SourceKind, Value, backend,
    invalid, is_sha256, json, safe_metadata,
};
use semwright_native_sdk::types::{Envelope, Execution};
use serde::{Deserialize, Serialize};
use std::path::{Component, Path, PathBuf};
use uuid::Uuid;

const OBSERVE: &str = "providers.mount.observe";
const EXECUTE: &str = "providers.execute_bound";
const PROVIDER: &str = "driver:motion-canvas";
const AUDIO_PROVIDER: &str = "driver:mlt-video";
const AUDIO_DERIVE: &str = "driver.mlt-video.audio.derive";

// Fixed internal specializations. No Deserialize, provider string, extension
// trait or caller-supplied command set can construct a different profile.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BindingProfile {
    Fonts,
    AudioDerivative,
}

impl BindingProfile {
    fn provider(self) -> &'static str {
        match self {
            Self::Fonts => PROVIDER,
            Self::AudioDerivative => AUDIO_PROVIDER,
        }
    }

    fn mutation_for(self, command: &str) -> NativeResult<bool> {
        match (self, command) {
            (
                Self::Fonts,
                "driver.motion-canvas.composition.inspect" | "driver.motion-canvas.asset.list",
            ) => Ok(false),
            (
                Self::Fonts,
                "driver.motion-canvas.project.create" | "driver.motion-canvas.asset.import",
            )
            | (Self::AudioDerivative, AUDIO_DERIVE) => Ok(true),
            _ => Err(Error::new(
                ErrorCode::Unsupported,
                "Provider-bound command is outside its closed handoff profile",
            )),
        }
    }

    fn receipt_schema(self) -> &'static str {
        match self {
            Self::Fonts => "motionwright.provider-bound-font/1",
            Self::AudioDerivative => "motionwright.provider-bound-audio-derive/1",
        }
    }

    fn receipt_key(self) -> &'static str {
        match self {
            Self::Fonts => "provider_bound_font",
            Self::AudioDerivative => "provider_bound_audio_derive",
        }
    }

    fn qualification_key(self) -> &'static str {
        match self {
            Self::Fonts => "font_qualification",
            Self::AudioDerivative => "audio_derive_qualification",
        }
    }

    fn transport_budget_secs(self) -> u64 {
        match self {
            Self::Fonts => super::DEFAULT_DEADLINE_SECS,
            Self::AudioDerivative => super::MLT_DEADLINE_SECS,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservedMount {
    binding_ref: String,
    provider: String,
    provider_generation: u64,
    connection: String,
    mount: MountDescription,
    admission_ttl_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MountDescription {
    root: String,
    path: PathBuf,
    read: bool,
    write: bool,
    execute: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct AudioPairMount {
    root: String,
    path: PathBuf,
    read: bool,
    write: bool,
    execute: bool,
    instance_identity: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ObservedAudioPair {
    version: u32,
    name: String,
    media: AudioPairMount,
    output: AudioPairMount,
}

impl ObservedAudioPair {
    fn validate(&self, owner_media: &Path) -> NativeResult<()> {
        let path_valid = |path: &Path| {
            path.is_absolute()
                && path.as_os_str().len() <= 4096
                && !path
                    .components()
                    .any(|part| matches!(part, Component::ParentDir | Component::CurDir))
        };
        if self.version != 1
            || self.name != "media-ro-output-rw"
            || self.media.root != "media"
            || self.output.root != "output"
            || self.media.path != owner_media
            || !path_valid(&self.media.path)
            || !path_valid(&self.output.path)
            || self.media.path.starts_with(&self.output.path)
            || self.output.path.starts_with(&self.media.path)
            || !self.media.read
            || self.media.write
            || self.media.execute
            || !self.output.read
            || !self.output.write
            || self.output.execute
            || !identifier(&self.media.instance_identity)
            || !identifier(&self.output.instance_identity)
            || self.media.instance_identity == self.output.instance_identity
        {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Canonical audio pair does not preserve separate MediaRO and OutputRW instances",
            ));
        }
        Ok(())
    }
}

/// Original read-only Core responses from the three fixed capture fences.
/// This bounded record is data only; it cannot construct a provider or root.
#[derive(Debug, Default, Serialize)]
pub(super) struct AudioPairReadbacks {
    admission: Option<Value>,
    before_capture: Option<Value>,
    after_capture: Option<Value>,
}

#[derive(Clone, Copy)]
pub(super) enum AudioPairCaptureStage {
    BeforeCapture,
    AfterCapture,
}

fn audio_pair_readback(observed: &NativeResult<BrokerResult>) -> Value {
    match observed {
        Ok(observed) => json!({
            "request_id":observed.request_id,
            "original_result":observed.value,
        }),
        Err(original) => json!({"original_error":original}),
    }
}

fn decode_observed_mount(
    profile: BindingProfile,
    owner_root: &Path,
    value: &Value,
) -> NativeResult<(ObservedMount, Option<ObservedAudioPair>)> {
    let mut data = value.clone();
    let pair = if profile == BindingProfile::AudioDerivative {
        let raw = data
            .as_object_mut()
            .and_then(|object| object.remove("pair"))
            .ok_or_else(|| backend("Canonical audio mount has no authenticated workspace pair"))?;
        let pair: ObservedAudioPair = serde_json::from_value(raw)
            .map_err(|_| backend("Canonical audio workspace pair is malformed"))?;
        pair.validate(owner_root)?;
        Some(pair)
    } else {
        None
    };
    // The original font decoder still denies every additional field, including pair.
    let mount = serde_json::from_value(data)
        .map_err(|_| backend("Canonical media mount observation is malformed"))?;
    Ok((mount, pair))
}

// Only this module constructs the binding from an authenticated live response.
// Stored JSON, Doctor output and caller configuration cannot construct it.
pub(super) struct BoundMediaContext {
    profile: BindingProfile,
    connection_identity: String,
    reference: String,
    provider_generation: u64,
    provider_connection: String,
    root: PathBuf,
    audio_pair: Option<ObservedAudioPair>,
    original_observation: BrokerResult,
}

// Constructors remain private and fix one profile. Deref exposes only immutable
// context access; neither wrapper can switch provider/profile after admission.
pub(super) struct FontMediaBinding(BoundMediaContext);
pub(super) struct AudioMediaBinding(BoundMediaContext);

impl std::ops::Deref for FontMediaBinding {
    type Target = BoundMediaContext;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}
impl std::ops::Deref for AudioMediaBinding {
    type Target = BoundMediaContext;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl FontMediaBinding {
    fn from_observation(
        connection_identity: String,
        owner_root: &Path,
        observed: BrokerResult,
    ) -> NativeResult<Self> {
        BoundMediaContext::from_observation(
            BindingProfile::Fonts,
            connection_identity,
            owner_root,
            observed,
        )
        .map(Self)
    }
}
impl AudioMediaBinding {
    fn from_observation(
        connection_identity: String,
        owner_root: &Path,
        observed: BrokerResult,
    ) -> NativeResult<Self> {
        BoundMediaContext::from_observation(
            BindingProfile::AudioDerivative,
            connection_identity,
            owner_root,
            observed,
        )
        .map(Self)
    }
}

pub(super) struct BoundAudioDerivativeCoordinator<'a> {
    coordinator: &'a ProductionCoordinator,
    binding: AudioMediaBinding,
}

pub(super) struct BoundFontCoordinator<'a> {
    coordinator: &'a ProductionCoordinator,
    binding: FontMediaBinding,
}

fn identifier(value: &str) -> bool {
    !value.is_empty() && value.len() <= 256 && !value.chars().any(char::is_control)
}

fn request_identifier(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn accepted_policy(execution: &Execution) -> bool {
    matches!(
        execution.policy_decision.as_str(),
        "allow" | "allow_after_confirmation"
    ) && !execution.dry_run
        && execution.fallbacks_attempted.is_empty()
}

fn core_authority() -> ExpectedAuthority {
    ExpectedAuthority {
        provider: "semwright-core",
        source: SourceKind::Builtin,
        provider_generation_required: false,
    }
}

fn validate_core(value: &Value, request_id: &str, command: &str) -> NativeResult<()> {
    let original = &value["original_envelope"];
    let envelope: Envelope = serde_json::from_value(original.clone())
        .map_err(|_| backend("Bound media response has no complete original Core envelope"))?;
    if !envelope.ok
        || envelope.error.is_some()
        || envelope.command != command
        || envelope.request_id != request_id
        || envelope.data.is_none()
        || original["data"] != value["data"]
        || serde_json::to_value(&envelope.execution).ok().as_ref() != Some(&value["execution"])
        || json!(envelope.warnings) != value["warnings"]
    {
        return Err(backend(
            "Bound media Core envelope is inconsistent with its transport result",
        ));
    }
    let execution: Execution = serde_json::from_value(value["execution"].clone())
        .map_err(|_| backend("Bound media response has invalid Core execution metadata"))?;
    let provenance = execution
        .provenance
        .as_ref()
        .ok_or_else(|| backend("Bound media response has no Core provenance"))?;
    if value["provenance_checked"] != json!(true)
        || !request_identifier(request_id)
        || execution.backend != "core"
        || !accepted_policy(&execution)
        || provenance.provider != "semwright-core"
        || provenance.source != SourceKind::Builtin
        || provenance.untrusted_metadata
        || !is_sha256(&provenance.descriptor_sha256)
    {
        return Err(backend(
            "Bound media response is not an authenticated Core result",
        ));
    }
    Ok(())
}

#[cfg(test)]
fn mutation_for(command: &str) -> NativeResult<bool> {
    BindingProfile::Fonts.mutation_for(command)
}

impl BoundMediaContext {
    fn from_observation(
        profile: BindingProfile,
        connection_identity: String,
        owner_root: &Path,
        observed: BrokerResult,
    ) -> NativeResult<Self> {
        validate_core(&observed.value, &observed.request_id, OBSERVE)?;
        let (mount, audio_pair) =
            decode_observed_mount(profile, owner_root, &observed.value["data"])?;
        if !is_sha256(&connection_identity)
            || !identifier(&mount.binding_ref)
            || !identifier(&mount.connection)
            || mount.provider != profile.provider()
            || mount.provider_generation == 0
            || mount.admission_ttl_ms == 0
            || mount.mount.root != "media"
            || mount.mount.path != owner_root
            || !mount.mount.path.is_absolute()
            || mount.mount.path.as_os_str().len() > 4096
            || mount
                .mount
                .path
                .components()
                .any(|component| matches!(component, Component::ParentDir | Component::CurDir))
            || !mount.mount.read
            || mount.mount.write
            || mount.mount.execute
        {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Canonical media mount does not match the owner-configured media workspace",
            ));
        }
        Ok(Self {
            profile,
            connection_identity,
            reference: mount.binding_ref,
            provider_generation: mount.provider_generation,
            provider_connection: mount.connection,
            root: mount.mount.path,
            audio_pair,
            original_observation: observed,
        })
    }

    pub(super) fn mutation_for(&self, command: &str) -> NativeResult<bool> {
        self.profile.mutation_for(command)
    }

    pub(super) fn receipt_key(&self) -> &'static str {
        self.profile.receipt_key()
    }

    pub(super) fn qualification_key(&self) -> &'static str {
        self.profile.qualification_key()
    }

    pub(super) fn transport_budget_secs(
        &self,
        outer_command: &str,
        request: &Value,
        mutation: bool,
    ) -> NativeResult<u64> {
        let outer = request
            .as_object()
            .ok_or_else(|| invalid("Bound media request is malformed"))?;
        let inner = request["request"]
            .as_object()
            .ok_or_else(|| invalid("Bound media nested request is malformed"))?;
        let command = inner
            .get("command")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("Bound media command is absent"))?;
        let requires_mutation = self.mutation_for(command)?;
        if outer_command != EXECUTE
            || outer.len() != 2
            || outer.get("binding_ref") != Some(&json!(self.reference))
            || inner.len() != 4
            || !inner.get("args").is_some_and(Value::is_object)
            || inner.get("backend") != Some(&json!(self.profile.provider()))
            || inner.get("dry_run") != Some(&json!(false))
            || (requires_mutation && !mutation)
        {
            return Err(invalid(
                "Bound media transport does not match its closed profile",
            ));
        }
        // This does not renew the binding TTL or change Core/Driver budgets.
        Ok(self.profile.transport_budget_secs())
    }

    pub(super) fn check_connection(&self, client: &ProductionClient) -> NativeResult<()> {
        client.connection().validate()?;
        if client.connection().identity()? != self.connection_identity {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Bound media mount observation belongs to another production connection",
            ));
        }
        // The configured workspace remains the owner-authorized staging location.
        // Its path is not taken from caller JSON or used as a new filesystem grant.
        if !safe_metadata(&self.root)?.is_dir() {
            return Err(invalid(
                "The owner-configured media workspace is not a directory",
            ));
        }
        Ok(())
    }

    pub(super) fn receipt_context(&self) -> Value {
        json!({
            "schema": self.profile.receipt_schema(),
            "connection_identity": self.connection_identity,
            "binding_ref": self.reference,
            "provider": self.profile.provider(),
            "provider_generation": self.provider_generation,
            "provider_connection": self.provider_connection,
            "media_root": self.root,
            "observation_request_id": self.original_observation.request_id,
            "observation": self.original_observation.value,
        })
    }

    fn request(&self, command: &str, args: Value) -> NativeResult<Value> {
        self.mutation_for(command)?;
        Ok(json!({
            "binding_ref": self.reference,
            "request": {
                "command": command,
                "args": args,
                "backend": self.profile.provider(),
                "dry_run": false
            }
        }))
    }

    pub(super) fn qualify_result(
        &self,
        observed: &BrokerResult,
        command: &str,
        mutation: bool,
    ) -> NativeResult<Value> {
        let required_mutation = self.mutation_for(command)?;
        let mutation = mutation || required_mutation;
        let checked = (|| {
            let outer = &observed.value;
            let outer_request = observed.request_id.as_str();
            validate_core(outer, outer_request, EXECUTE)?;
            let data = outer["data"]
                .as_object()
                .ok_or_else(|| backend("Bound media result has no canonical wrapper data"))?;
            let keys: &[&str] = if self.profile == BindingProfile::AudioDerivative {
                &[
                    "binding_ref",
                    "result",
                    "admission_current",
                    "admission_state",
                    "pair",
                ]
            } else {
                &[
                    "binding_ref",
                    "result",
                    "admission_current",
                    "admission_state",
                ]
            };
            if data.len() != keys.len()
                || keys.iter().any(|key| !data.contains_key(*key))
                || data["binding_ref"] != json!(self.reference)
            {
                return Err(backend(
                    "Bound media result does not match its original binding",
                ));
            }
            let admission_current = data["admission_current"]
                .as_bool()
                .ok_or_else(|| backend("Bound media result has no admission state"))?;
            if self.profile == BindingProfile::AudioDerivative {
                let pair: ObservedAudioPair = serde_json::from_value(data["pair"].clone())
                    .map_err(|_| backend("Completed audio pair is malformed"))?;
                if self.audio_pair.as_ref() != Some(&pair) {
                    return Err(backend(
                        "Completed audio pair differs from its original admission",
                    ));
                }
            }
            let valid_state = if admission_current {
                data["admission_state"] == json!("current")
            } else {
                data["admission_state"] == json!("expired")
                    || (self.profile == BindingProfile::AudioDerivative
                        && self.audio_pair.is_some()
                        && data["admission_state"] == json!("closing"))
            };
            if !valid_state {
                return Err(backend("Bound media admission state is inconsistent"));
            }
            let nested: Envelope = serde_json::from_value(data["result"].clone())
                .map_err(|_| backend("Bound media Driver envelope is malformed"))?;
            let provenance = nested
                .execution
                .provenance
                .as_ref()
                .ok_or_else(|| backend("Bound media Driver response has no provenance"))?;
            if !nested.ok
                || nested.error.is_some()
                || nested.command != command
                || !request_identifier(&nested.request_id)
                || nested.request_id == outer_request
                || nested.data.is_none()
                || nested.execution.backend != self.profile.provider()
                || !accepted_policy(&nested.execution)
                || provenance.provider != self.profile.provider()
                || provenance.source != SourceKind::Driver
                || provenance.execution_provider.as_deref() != Some(self.profile.provider())
                || provenance.provider_generation != Some(self.provider_generation)
                || !is_sha256(&provenance.descriptor_sha256)
            {
                return Err(backend(
                    "Bound media Driver result does not match its authenticated execution",
                ));
            }
            Ok((
                json!({
                    "data": nested.data,
                    "execution": nested.execution,
                    "warnings": nested.warnings,
                    "provenance_checked": true,
                    "provider_bound": {
                        "outer_request_id": outer_request,
                        "inner_request_id": nested.request_id,
                        "original": outer,
                    }
                }),
                admission_current,
            ))
        })();
        match checked {
            Ok((result, true)) => Ok(result),
            // Core confirmed the same lease and successful effect. Only its
            // current admission expired; do not erase that known completion.
            Ok((_, false)) => Err(Error::new(
                ErrorCode::StaleReference,
                "Bound media operation completed historically; its admission is no longer current",
            )),
            Err(error) => Err(if mutation { error.uncertain() } else { error }),
        }
    }

    fn normalize_completed(
        &self,
        mut legacy: Value,
        command: &str,
        mutation: bool,
    ) -> NativeResult<Value> {
        // Rejecting reuse of an admitted terminal does not make its old effect
        // uncertain. This view still cannot mint a fresh provider observation.
        if legacy["replayed"] != json!(false) {
            return Err(Error::new(
                ErrorCode::Conflict,
                "A stored receipt cannot qualify a fresh provider-bound media response",
            ));
        }
        let request_id = legacy
            .pointer("/receipt/payload/broker_request_id")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                let error = backend("Bound media receipt has no original Core request identity");
                if mutation { error.uncertain() } else { error }
            })?;
        // Use the same pure qualifier as the pre-append branch, on the identical
        // preserved result. No additional policy or current-time test lives here.
        let result = self.qualify_result(
            &BrokerResult {
                value: legacy["result"].clone(),
                request_id: request_id.to_owned(),
            },
            command,
            mutation,
        )?;
        legacy["result"] = result;
        Ok(legacy)
    }
}

impl ProductionClient {
    async fn observe_bound_media(
        &self,
        owner_root: &Path,
        profile: BindingProfile,
    ) -> NativeResult<BrokerResult> {
        if !safe_metadata(owner_root)?.is_dir() {
            return Err(invalid("Bound media handoff requires the owner workspace"));
        }
        let mut args = json!({"provider": profile.provider(), "root": "media"});
        if profile == BindingProfile::AudioDerivative {
            args["pair"] = json!({"version":1,"name":"media-ro-output-rw"});
        }
        self.execute_with_authority(OBSERVE, args, false, Some(core_authority()), None)
            .await
    }

    pub(super) async fn execute_bound_media_transport(
        &self,
        binding: &BoundMediaContext,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<BrokerResult> {
        binding.check_connection(self)?;
        let required_mutation = binding.mutation_for(command)?;
        let mutation = mutation || required_mutation;
        let request = binding.request(command, args)?;
        // Same Core/lease checks and transport body. The typed binding carries
        // the closed deadline choice; JSON never supplies a duration.
        self.execute_with_authority(
            EXECUTE,
            request,
            mutation,
            Some(core_authority()),
            Some(binding),
        )
        .await
    }
}

impl ProductionCoordinator {
    pub(super) async fn bind_font_media(
        &self,
        owner_root: &Path,
    ) -> NativeResult<BoundFontCoordinator<'_>> {
        let identity = self.client.connection().identity()?;
        let observed = self
            .client
            .observe_bound_media(owner_root, BindingProfile::Fonts)
            .await?;
        let binding = FontMediaBinding::from_observation(identity, owner_root, observed)?;
        Ok(BoundFontCoordinator {
            coordinator: self,
            binding,
        })
    }

    pub(super) async fn bind_audio_derivative_media(
        &self,
        owner_root: &Path,
        owner_output: &Path,
        readbacks: &mut AudioPairReadbacks,
    ) -> NativeResult<BoundAudioDerivativeCoordinator<'_>> {
        if readbacks.admission.is_some() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Audio pair admission already has an original readback",
            ));
        }
        if owner_output != self.client.connection().output_root {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Audio pair output differs from the independently selected owner connection",
            ));
        }
        let identity = self.client.connection().identity()?;
        let observed = self
            .client
            .observe_bound_media(owner_root, BindingProfile::AudioDerivative)
            .await;
        readbacks.admission = Some(audio_pair_readback(&observed));
        let binding = AudioMediaBinding::from_observation(identity, owner_root, observed?)?;
        binding.owner_pair_identities(owner_root, owner_output)?;
        Ok(BoundAudioDerivativeCoordinator {
            coordinator: self,
            binding,
        })
    }
}

impl BoundFontCoordinator<'_> {
    pub(super) async fn execute(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<Value> {
        let required_mutation = self.binding.mutation_for(command)?;
        let mutation = mutation || required_mutation;
        let outcome = self
            .coordinator
            .execute_with_receipt_origin_context(
                project_id,
                expected,
                request_id,
                (command, mutation),
                args,
                Some(ReceiptDispatchContext::Bound(&self.binding)),
            )
            .await?;
        self.binding
            .normalize_completed(outcome.into_legacy_value(), command, mutation)
    }
}

impl AudioMediaBinding {
    fn owner_pair_identities(
        &self,
        owner_media: &Path,
        owner_output: &Path,
    ) -> NativeResult<(&str, &str)> {
        let pair = self
            .audio_pair
            .as_ref()
            .ok_or_else(|| backend("Audio binding has no original workspace pair"))?;
        if pair.media.path != owner_media || pair.output.path != owner_output {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Audio pair differs from the independently authorized owner roots",
            ));
        }
        Ok((
            &pair.media.instance_identity,
            &pair.output.instance_identity,
        ))
    }

    fn revalidation_request(&self) -> Value {
        // Existing reference only: no new pair/profile selector and no TTL renewal.
        json!({
            "provider":AUDIO_PROVIDER,"root":"media","binding_ref":self.reference,
        })
    }

    fn qualify_revalidation(&self, observed: BrokerResult) -> NativeResult<()> {
        let current =
            Self::from_observation(self.connection_identity.clone(), &self.root, observed)?;
        let old_ttl = self.original_observation.value["data"]["admission_ttl_ms"].as_u64();
        let new_ttl = current.original_observation.value["data"]["admission_ttl_ms"].as_u64();
        if current.reference != self.reference
            || current.provider_generation != self.provider_generation
            || current.provider_connection != self.provider_connection
            || current.root != self.root
            || current.audio_pair != self.audio_pair
            || !matches!((old_ttl, new_ttl), (Some(old), Some(new)) if new > 0 && new <= old)
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio pair revalidation changed the original lease, instances or expiry",
            ));
        }
        Ok(())
    }
}

impl BoundAudioDerivativeCoordinator<'_> {
    pub(super) fn owner_pair_identities(
        &self,
        owner_media: &Path,
        owner_output: &Path,
    ) -> NativeResult<(&str, &str)> {
        self.binding
            .owner_pair_identities(owner_media, owner_output)
    }

    pub(super) async fn revalidate_capture_pair(
        &self,
        stage: AudioPairCaptureStage,
        readbacks: &mut AudioPairReadbacks,
    ) -> NativeResult<()> {
        let slot = match stage {
            AudioPairCaptureStage::BeforeCapture => &mut readbacks.before_capture,
            AudioPairCaptureStage::AfterCapture => &mut readbacks.after_capture,
        };
        if slot.is_some() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Audio capture fence already has an original readback",
            ));
        }
        self.binding.check_connection(&self.coordinator.client)?;
        let observed = self
            .coordinator
            .client
            .execute_with_authority(
                OBSERVE,
                self.binding.revalidation_request(),
                false,
                Some(core_authority()),
                None,
            )
            .await;
        *slot = Some(audio_pair_readback(&observed));
        self.binding.qualify_revalidation(observed?)
    }

    // Deliberately no command/provider/mutation argument. Input staging and
    // output-root authority remain the caller's separately reviewed obligations.
    pub(super) async fn derive(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        args: Value,
    ) -> NativeResult<Value> {
        let outcome = self
            .coordinator
            .execute_with_receipt_origin_context(
                project_id,
                expected,
                request_id,
                (AUDIO_DERIVE, true),
                args,
                Some(ReceiptDispatchContext::Bound(&self.binding)),
            )
            .await?;
        self.binding
            .normalize_completed(outcome.into_legacy_value(), AUDIO_DERIVE, true)
    }
}

#[cfg(test)]
mod tests;
