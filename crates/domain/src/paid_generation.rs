//! Append-only, source-bound paid-generation ledger. All effects remain owned
//! by existing StudioService/Native SDK; this crate never calls paid providers.
//! Provider observations and identity consent are DECLARED, not authenticated.
use crate::*;
use sha2::{Digest, Sha256};

const MAX_JOBS: usize = 256;
const MAX_EVENTS: usize = 128;
const MAX_JOB_MICROUSD: u64 = 100_000_000_000;
const MAX_PROJECT_MICROUSD: u64 = 1_000_000_000_000;
fn valid_sha(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn bounded(s: &str, max: usize, label: &str) -> Result<()> {
    if s.trim().is_empty() || s.len() > max || s.chars().any(char::is_control) {
        return Err(DomainError::Invalid(format!(
            "invalid paid generation {label}"
        )));
    }
    Ok(())
}
fn digest<T: Serialize>(item: &T) -> Result<String> {
    let bytes = serde_json::to_vec(item).map_err(|e| DomainError::Invalid(e.to_string()))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PaidGenerationKind {
    Image,
    Video,
    Tts,
    Presenter,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum PaidGenerationScope {
    Pilot,
    Batch {
        pilot_job_id: Uuid,
        approved_pilot_sha256: String,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationInput {
    pub asset_id: Uuid,
    pub sha256: String,
    pub owner_rights_note: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum PaidIdentity {
    SyntheticOriginal {
        visible_synthetic_label: String,
    },
    ConsentedPerson {
        person_asset_id: Uuid,
        person_asset_sha256: String,
        purpose: String,
        exact_consent_sha256: String,
        owner_declares_consent: bool,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationSpec {
    pub id: Uuid,
    pub campaign_id: Uuid,
    pub scene_id: Uuid,
    /// Index 0 for the pilot; distinct 1..=256 for each batch unit.
    pub variant_index: u16,
    pub kind: PaidGenerationKind,
    pub scope: PaidGenerationScope,
    pub provider_id: String,
    pub model_id: String,
    pub model_version: String,
    pub capability_receipt_sha256: String,
    pub rights_policy_sha256: String,
    pub prompt_sha256: String,
    pub inputs: Vec<PaidGenerationInput>,
    pub output_usage_terms: String,
    pub identity: PaidIdentity,
    pub max_charge_microusd: u64,
    pub logical_idempotency_sha256: String,
}
impl PaidGenerationSpec {
    pub fn expected_idempotency(&self, project_id: Uuid) -> Result<String> {
        let mut proposed = self.clone();
        proposed.logical_idempotency_sha256.clear();
        // A regenerated job UUID cannot authorize a second paid request.
        proposed.id = Uuid::nil();
        digest(&("motionwright.paid-request/1", project_id, proposed))
    }
    pub fn validate(&self, project: &Project) -> Result<()> {
        if [self.id, self.campaign_id, self.scene_id].contains(&Uuid::nil())
            || !project.scenes.iter().any(|scene| scene.id == self.scene_id)
        {
            return Err(DomainError::Invalid(
                "paid request needs a saved scene and nonnil identities".into(),
            ));
        }
        for (value, label) in [
            (&self.provider_id, "provider"),
            (&self.model_id, "model"),
            (&self.model_version, "model version"),
        ] {
            bounded(value, 128, label)?;
            if !value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._:/-".contains(&b))
            {
                return Err(DomainError::Invalid(
                    "paid provider/model/version cannot contain executable commands or URLs".into(),
                ));
            }
        }
        bounded(&self.output_usage_terms, 512, "output usage")?;
        let variant_ok = match &self.scope {
            PaidGenerationScope::Pilot => self.variant_index == 0,
            PaidGenerationScope::Batch { .. } => (1..=256).contains(&self.variant_index),
        };
        if !variant_ok {
            return Err(DomainError::Invalid(
                "paid variant index must distinguish pilot and batch outputs".into(),
            ));
        }
        if ![
            &self.capability_receipt_sha256,
            &self.rights_policy_sha256,
            &self.prompt_sha256,
        ]
        .iter()
        .all(|value| valid_sha(value))
            || self.logical_idempotency_sha256 != self.expected_idempotency(project.id)?
        {
            return Err(DomainError::Invalid("paid request capability, rights, prompt or deterministic idempotency digest mismatch".into()));
        }
        if self.max_charge_microusd == 0
            || self.max_charge_microusd > MAX_JOB_MICROUSD
            || self.inputs.len() > 16
        {
            return Err(DomainError::Invalid(
                "paid request charge or input budget exceeded".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for input in &self.inputs {
            bounded(&input.owner_rights_note, 1200, "source rights")?;
            if !valid_sha(&input.sha256)
                || !ids.insert(input.asset_id)
                || !project.assets.iter().any(|asset| {
                    asset.id == input.asset_id
                        && asset.content_sha256.as_deref() == Some(input.sha256.as_str())
                })
            {
                return Err(DomainError::Invalid(
                    "paid input requires distinct, existing, exact SHA-bound owner media".into(),
                ));
            }
        }
        match &self.identity {
            PaidIdentity::SyntheticOriginal {
                visible_synthetic_label,
            } => {
                bounded(visible_synthetic_label, 300, "synthetic output label")?;
            }
            PaidIdentity::ConsentedPerson {
                person_asset_id,
                person_asset_sha256,
                purpose,
                exact_consent_sha256,
                owner_declares_consent,
            } => {
                bounded(purpose, 1000, "consent purpose")?;
                if purpose != &self.output_usage_terms
                    || !*owner_declares_consent
                    || !valid_sha(exact_consent_sha256)
                    || !valid_sha(person_asset_sha256)
                    || !self.inputs.iter().any(|input| {
                        input.asset_id == *person_asset_id && input.sha256 == *person_asset_sha256
                    })
                    || !project.assets.iter().any(|asset| {
                        asset.id == *person_asset_id
                            && asset.content_sha256.as_deref() == Some(person_asset_sha256.as_str())
                    })
                {
                    return Err(DomainError::Invalid("human likeness/voice requires exact source and declared purpose-specific consent".into()));
                }
            }
        }
        if let PaidGenerationScope::Batch {
            pilot_job_id,
            approved_pilot_sha256,
        } = &self.scope
            && (*pilot_job_id == self.id || !valid_sha(approved_pilot_sha256))
        {
            return Err(DomainError::Invalid(
                "batch requires distinct approved pilot and original artifact digest".into(),
            ));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PaidGenerationState {
    Draft,
    Reserved,
    UnknownOutcome,
    ProviderReportedSuccess,
    ProviderReportedFailure,
    OutputAdmitted,
    PilotApproved,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub enum PaidProviderOutcome {
    StillRunning,
    Succeeded {
        output_sha256: String,
        reported_cost_microusd: u64,
    },
    Failed {
        reason: String,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum PaidGenerationEventKind {
    Reserve {
        maximum_charge_microusd: u64,
    },
    DispatchUnknown {
        provider_task_id: Option<String>,
        declared_receipt_sha256: Option<String>,
        reason: String,
    },
    ProviderPoll {
        provider_task_id: Option<String>,
        observed_receipt_sha256: String,
        outcome: PaidProviderOutcome,
    },
    AdmitOutput {
        asset_id: Uuid,
        output_sha256: String,
        rights_note: String,
        reviewer: String,
    },
    ApprovePilot {
        output_sha256: String,
        reviewer: String,
        rationale: String,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationEvent {
    pub id: Uuid,
    pub kind: PaidGenerationEventKind,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationJob {
    pub spec: PaidGenerationSpec,
    pub events: Vec<PaidGenerationEvent>,
    pub state: PaidGenerationState,
    pub reserved_microusd: u64,
    pub provider_reported_spend_microusd: Option<u64>,
    pub task_id: Option<String>,
    pub reported_output_sha256: Option<String>,
    pub admitted_output_asset_id: Option<Uuid>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationLedger {
    pub jobs: Vec<PaidGenerationJob>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum PaidGenerationEdit {
    Create {
        spec: PaidGenerationSpec,
    },
    Append {
        job_id: Uuid,
        expected_history_sha256: String,
        event: PaidGenerationEvent,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationReconcileQuery {
    pub schema: String,
    pub job_id: Uuid,
    pub provider_id: String,
    pub logical_idempotency_sha256: String,
    pub known_task_id: Option<String>,
    pub operation: String,
    pub launches_new_task: bool,
    pub billing_verified: bool,
    pub provider_observation_verified: bool,
}

fn provider_task_id(value: &str) -> Result<()> {
    bounded(value, 160, "provider task ID")?;
    if !value
        .bytes()
        .all(|v| v.is_ascii_alphanumeric() || b"_.:-".contains(&v))
    {
        return Err(DomainError::Invalid(
            "provider task ID cannot be a URL, path or command".into(),
        ));
    }
    Ok(())
}
impl PaidGenerationJob {
    fn initial(spec: PaidGenerationSpec) -> Self {
        Self {
            spec,
            events: vec![],
            state: PaidGenerationState::Draft,
            reserved_microusd: 0,
            provider_reported_spend_microusd: None,
            task_id: None,
            reported_output_sha256: None,
            admitted_output_asset_id: None,
        }
    }
    pub fn history_sha256(&self) -> Result<String> {
        digest(&(
            "motionwright.paid-generation-history/1",
            &self.spec,
            &self.events,
        ))
    }
    fn append(&mut self, event: PaidGenerationEvent, project: &Project) -> Result<()> {
        if self.events.len() >= MAX_EVENTS
            || event.id == Uuid::nil()
            || self.events.iter().any(|prior| prior.id == event.id)
        {
            return Err(DomainError::Invalid(
                "generation event ID duplicated or event budget exceeded".into(),
            ));
        }
        match &event.kind {
            PaidGenerationEventKind::Reserve {
                maximum_charge_microusd,
            } => {
                if self.state != PaidGenerationState::Draft
                    || *maximum_charge_microusd != self.spec.max_charge_microusd
                {
                    return Err(DomainError::Invalid(
                        "a paid job can reserve its exact cap only once".into(),
                    ));
                }
                self.state = PaidGenerationState::Reserved;
                self.reserved_microusd = *maximum_charge_microusd;
            }
            PaidGenerationEventKind::DispatchUnknown {
                provider_task_id: task,
                declared_receipt_sha256,
                reason,
            } => {
                if self.state != PaidGenerationState::Reserved
                    || declared_receipt_sha256
                        .as_ref()
                        .is_some_and(|receipt| !valid_sha(receipt))
                {
                    return Err(DomainError::Invalid(
                        "uncertain provider dispatch requires one reserved attempt".into(),
                    ));
                }
                bounded(reason, 1200, "unknown outcome explanation")?;
                if let Some(task) = task {
                    provider_task_id(task)?;
                }
                self.task_id = task.clone();
                self.state = PaidGenerationState::UnknownOutcome;
            }
            PaidGenerationEventKind::ProviderPoll {
                provider_task_id: task,
                observed_receipt_sha256,
                outcome,
            } => {
                if self.state != PaidGenerationState::UnknownOutcome
                    || !valid_sha(observed_receipt_sha256)
                    || self.events.iter().any(|prior| {
                        matches!(&prior.kind,
                        PaidGenerationEventKind::ProviderPoll{observed_receipt_sha256:sha,..}
                            if sha==observed_receipt_sha256)
                    })
                {
                    return Err(DomainError::Invalid("provider poll must reuse existing uncertain logical request with new evidence".into()));
                }
                if let Some(task) = task {
                    provider_task_id(task)?;
                    if self.task_id.as_ref().is_some_and(|old| old != task) {
                        return Err(DomainError::Invalid(
                            "provider task ID cannot change within one paid logical attempt".into(),
                        ));
                    }
                    self.task_id = Some(task.clone());
                }
                match outcome {
                    PaidProviderOutcome::StillRunning => {}
                    PaidProviderOutcome::Succeeded {
                        output_sha256,
                        reported_cost_microusd,
                    } => {
                        if self.task_id.is_none()
                            || !valid_sha(output_sha256)
                            || *reported_cost_microusd > self.reserved_microusd
                        {
                            return Err(DomainError::Invalid("reported provider success lacks original task identity or exceeds spend cap".into()));
                        }
                        self.reported_output_sha256 = Some(output_sha256.clone());
                        self.provider_reported_spend_microusd = Some(*reported_cost_microusd);
                        self.state = PaidGenerationState::ProviderReportedSuccess;
                    }
                    PaidProviderOutcome::Failed { reason } => {
                        if self.task_id.is_none() {
                            return Err(DomainError::Invalid(
                                "no confirmed external task identity for reported failure".into(),
                            ));
                        }
                        bounded(reason, 1200, "provider reported failure")?;
                        self.state = PaidGenerationState::ProviderReportedFailure;
                    }
                }
            }
            PaidGenerationEventKind::AdmitOutput {
                asset_id,
                output_sha256,
                rights_note,
                reviewer,
            } => {
                let prefix = match self.spec.kind {
                    PaidGenerationKind::Image => "image/",
                    PaidGenerationKind::Video | PaidGenerationKind::Presenter => "video/",
                    PaidGenerationKind::Tts => "audio/",
                };
                if self.state != PaidGenerationState::ProviderReportedSuccess
                    || self.reported_output_sha256.as_deref() != Some(output_sha256.as_str())
                    || !project.assets.iter().any(|asset| {
                        asset.id == *asset_id
                            && asset.content_sha256.as_deref() == Some(output_sha256.as_str())
                            && asset.media_type.starts_with(prefix)
                    })
                {
                    return Err(DomainError::Invalid("generated output must first be imported as an exact digest-bound project asset".into()));
                }
                bounded(rights_note, 1600, "generated usage rights")?;
                bounded(reviewer, 200, "generated asset reviewer")?;
                self.admitted_output_asset_id = Some(*asset_id);
                self.state = PaidGenerationState::OutputAdmitted;
            }
            PaidGenerationEventKind::ApprovePilot {
                output_sha256,
                reviewer,
                rationale,
            } => {
                if self.state != PaidGenerationState::OutputAdmitted
                    || !matches!(&self.spec.scope, PaidGenerationScope::Pilot)
                    || self.reported_output_sha256.as_deref() != Some(output_sha256.as_str())
                {
                    return Err(DomainError::Invalid(
                        "pilot cannot be approved without admitted exact generated artifact".into(),
                    ));
                }
                bounded(reviewer, 200, "pilot reviewer")?;
                bounded(rationale, 1600, "pilot owner review")?;
                self.state = PaidGenerationState::PilotApproved;
            }
        }
        self.events.push(event);
        Ok(())
    }
    pub fn reconcile_query(&self) -> Result<PaidGenerationReconcileQuery> {
        if self.state != PaidGenerationState::UnknownOutcome {
            return Err(DomainError::Invalid(
                "only unresolved paid attempts can be reconciled".into(),
            ));
        }
        Ok(PaidGenerationReconcileQuery {
            schema: "motionwright.paid-generation-reconcile-query/1".into(),
            job_id: self.spec.id,
            provider_id: self.spec.provider_id.clone(),
            logical_idempotency_sha256: self.spec.logical_idempotency_sha256.clone(),
            known_task_id: self.task_id.clone(),
            operation: "query_existing_task_only".into(),
            launches_new_task: false,
            billing_verified: false,
            provider_observation_verified: false,
        })
    }
}
impl PaidGenerationLedger {
    pub fn is_empty(&self) -> bool {
        self.jobs.is_empty()
    }
    pub fn validate(&self, project: &Project) -> Result<()> {
        if self.jobs.len() > MAX_JOBS {
            return Err(DomainError::Invalid(
                "too many paid generation jobs in one project".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        let mut idempotency = BTreeSet::new();
        let mut event_ids = BTreeSet::new();
        let mut reserved = 0_u64;
        for job in &self.jobs {
            job.spec.validate(project)?;
            if !ids.insert(job.spec.id)
                || !idempotency.insert(job.spec.logical_idempotency_sha256.clone())
            {
                return Err(DomainError::Invalid(
                    "paid logical request ID or idempotency digest reused".into(),
                ));
            }
            let mut replay = PaidGenerationJob::initial(job.spec.clone());
            for event in &job.events {
                if !event_ids.insert(event.id) {
                    return Err(DomainError::Invalid(
                        "paid journal event identity was reused between jobs".into(),
                    ));
                }
                replay.append(event.clone(), project)?;
            }
            if replay != *job {
                return Err(DomainError::Invalid(
                    "paid job state or cost was forged outside append-only event history".into(),
                ));
            }
            reserved = reserved
                .checked_add(job.reserved_microusd)
                .ok_or_else(|| DomainError::Invalid("paid reservation budget overflow".into()))?;
        }
        if reserved > MAX_PROJECT_MICROUSD {
            return Err(DomainError::Invalid(
                "project paid generation reservation limit exceeded".into(),
            ));
        }
        for job in &self.jobs {
            if let PaidGenerationScope::Batch {
                pilot_job_id,
                approved_pilot_sha256,
            } = &job.spec.scope
            {
                let pilot = self
                    .jobs
                    .iter()
                    .find(|entry| entry.spec.id == *pilot_job_id)
                    .ok_or_else(|| {
                        DomainError::Invalid("batch does not have an existing sample pilot".into())
                    })?;
                if job.reserved_microusd > 0
                    && (pilot.state != PaidGenerationState::PilotApproved
                        || pilot.reported_output_sha256.as_deref()
                            != Some(approved_pilot_sha256.as_str())
                        || pilot.spec.campaign_id != job.spec.campaign_id
                        || pilot.spec.kind != job.spec.kind
                        || pilot.spec.provider_id != job.spec.provider_id
                        || pilot.spec.model_id != job.spec.model_id
                        || pilot.spec.model_version != job.spec.model_version)
                {
                    return Err(DomainError::Invalid("batch spend requires exact owner-recorded pilot approval for same provider/model/campaign".into()));
                }
            }
        }
        Ok(())
    }
    pub fn apply(&mut self, project: &Project, edit: &PaidGenerationEdit) -> Result<()> {
        let mut proposed = self.clone();
        match edit {
            PaidGenerationEdit::Create { spec } => {
                spec.validate(project)?;
                if proposed.jobs.iter().any(|entry| {
                    entry.spec.id == spec.id
                        || entry.spec.logical_idempotency_sha256 == spec.logical_idempotency_sha256
                }) {
                    return Err(DomainError::Invalid(
                        "paid request already exists: do not duplicate a charge".into(),
                    ));
                }
                proposed.jobs.push(PaidGenerationJob::initial(spec.clone()));
            }
            PaidGenerationEdit::Append {
                job_id,
                expected_history_sha256,
                event,
            } => {
                let job = proposed
                    .jobs
                    .iter_mut()
                    .find(|entry| entry.spec.id == *job_id)
                    .ok_or_else(|| DomainError::NotFound(format!("generation:{job_id}")))?;
                if !valid_sha(expected_history_sha256)
                    || job.history_sha256()?.as_str() != expected_history_sha256.as_str()
                {
                    return Err(DomainError::Invalid(
                        "paid event append has a stale or forged history digest".into(),
                    ));
                }
                job.append(event.clone(), project)?;
            }
        }
        proposed.validate(project)?;
        *self = proposed;
        Ok(())
    }
}
impl Project {
    pub fn edit_paid_generation(&mut self, edit: &PaidGenerationEdit) -> Result<()> {
        self.ensure_unlocked(&self.resource_key(), &[LockKind::Content])?;
        let mut next = self.production_design.paid_generation.clone();
        next.apply(self, edit)?;
        self.production_design.paid_generation = next;
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct DeclaredProviderGenerationCapability {
    pub provider_id: String,
    pub model_id: String,
    pub model_version: String,
    pub capability_receipt_sha256: String,
    pub rights_policy_sha256: String,
    pub supported_kinds: Vec<PaidGenerationKind>,
    pub accepted_input_media_prefixes: Vec<String>,
    pub supports_consented_identity: bool,
    pub maximum_declared_charge_microusd: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct GenerationCapabilityRoutePreview {
    pub schema: String,
    pub generation_job_id: Uuid,
    pub logical_idempotency_sha256: String,
    pub matched_declared_provider: bool,
    pub candidate_provider_id: Option<String>,
    pub examined_provider_declarations: usize,
    pub rights_and_input_requirements_checked: bool,
    pub provider_capability_signature_verified: bool,
    pub owner_execution_grant: bool,
    pub remote_generation_performed: bool,
    pub explanation: String,
}
pub fn preview_paid_provider_route(
    project: &Project,
    spec: &PaidGenerationSpec,
    catalog: &[DeclaredProviderGenerationCapability],
) -> Result<GenerationCapabilityRoutePreview> {
    spec.validate(project)?;
    if catalog.len() > 64 {
        return Err(DomainError::Invalid(
            "provider capability declaration budget exceeded".into(),
        ));
    }
    let mut keys = BTreeSet::new();
    let mut matches = vec![];
    for entry in catalog {
        for name in [&entry.provider_id, &entry.model_id, &entry.model_version] {
            bounded(name, 128, "declared provider/model")?;
            if !name
                .bytes()
                .all(|v| v.is_ascii_alphanumeric() || b"._:/-".contains(&v))
            {
                return Err(DomainError::Invalid(
                    "declared provider contains unadmitted token".into(),
                ));
            }
        }
        if !valid_sha(&entry.capability_receipt_sha256)
            || !valid_sha(&entry.rights_policy_sha256)
            || entry.supported_kinds.is_empty()
            || entry.supported_kinds.len() > 4
            || entry.maximum_declared_charge_microusd == 0
            || entry.maximum_declared_charge_microusd > MAX_JOB_MICROUSD
            || entry.accepted_input_media_prefixes.len() > 12
        {
            return Err(DomainError::Invalid(
                "malformed declared provider capability/rights budget".into(),
            ));
        }
        for media in &entry.accepted_input_media_prefixes {
            bounded(media, 64, "declared input media kind")?;
            if !media
                .bytes()
                .all(|v| v.is_ascii_alphanumeric() || b"/.+-".contains(&v))
            {
                return Err(DomainError::Invalid(
                    "declared media prefix is unsafe".into(),
                ));
            }
        }
        let mut unique = BTreeSet::new();
        if entry
            .supported_kinds
            .iter()
            .any(|kind| !unique.insert(format!("{kind:?}")))
        {
            return Err(DomainError::Invalid(
                "provider repeats the same capability".into(),
            ));
        }
        if !keys.insert((&entry.provider_id, &entry.model_id, &entry.model_version)) {
            return Err(DomainError::Invalid(
                "duplicate provider capability identity/version declaration".into(),
            ));
        }
        let exact = entry.provider_id == spec.provider_id
            && entry.model_id == spec.model_id
            && entry.model_version == spec.model_version
            && entry.capability_receipt_sha256 == spec.capability_receipt_sha256
            && entry.rights_policy_sha256 == spec.rights_policy_sha256
            && entry.maximum_declared_charge_microusd >= spec.max_charge_microusd
            && entry.supported_kinds.contains(&spec.kind)
            && (!matches!(&spec.identity, PaidIdentity::ConsentedPerson { .. })
                || entry.supports_consented_identity)
            && spec.inputs.iter().all(|input| {
                project
                    .assets
                    .iter()
                    .find(|asset| asset.id == input.asset_id)
                    .is_some_and(|asset| {
                        entry
                            .accepted_input_media_prefixes
                            .iter()
                            .any(|prefix| asset.media_type.starts_with(prefix))
                    })
            });
        if exact {
            matches.push(entry.provider_id.clone());
        }
    }
    Ok(GenerationCapabilityRoutePreview{
        schema:"motionwright.paid-generation-declared-route-preview/1".into(),
        generation_job_id:spec.id,logical_idempotency_sha256:spec.logical_idempotency_sha256.clone(),
        matched_declared_provider:matches.len()==1,
        candidate_provider_id:matches.pop(),
        examined_provider_declarations:catalog.len(),
        rights_and_input_requirements_checked:true,
        provider_capability_signature_verified:false,
        owner_execution_grant:false,
        remote_generation_performed:false,
        explanation:"Pure capability/inputs/policy match over owner-supplied model declarations; provider API, receipts, charges, user consent and execution remain independently unverified.".into()
    })
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationBudgetPreview {
    pub schema: String,
    pub project_reserved_microusd: u64,
    pub provider_reported_cost_microusd: u64,
    pub unresolved_reserved_microusd: u64,
    pub currency: String,
    pub cost_reports_are_provider_verified: bool,
    pub actual_payment_settled: bool,
    pub may_retry_paid_submission: bool,
}
impl PaidGenerationLedger {
    pub fn budget_preview(&self, project: &Project) -> Result<PaidGenerationBudgetPreview> {
        self.validate(project)?;
        let reserved = self.jobs.iter().try_fold(0_u64, |value, job| {
            value
                .checked_add(job.reserved_microusd)
                .ok_or_else(|| DomainError::Invalid("paid-budget arithmetic overflow".into()))
        })?;
        let reported = self.jobs.iter().try_fold(0_u64, |value, job| {
            value
                .checked_add(job.provider_reported_spend_microusd.unwrap_or(0))
                .ok_or_else(|| DomainError::Invalid("reported cost overflow".into()))
        })?;
        let unresolved = self
            .jobs
            .iter()
            .filter(|job| {
                matches!(
                    job.state,
                    PaidGenerationState::Reserved | PaidGenerationState::UnknownOutcome
                )
            })
            .try_fold(0_u64, |value, job| {
                value
                    .checked_add(job.reserved_microusd)
                    .ok_or_else(|| DomainError::Invalid("unknown reserved cost overflow".into()))
            })?;
        Ok(PaidGenerationBudgetPreview {
            schema: "motionwright.paid-generation-budget-preview/1".into(),
            project_reserved_microusd: reserved,
            provider_reported_cost_microusd: reported,
            unresolved_reserved_microusd: unresolved,
            currency: "USD_micro".into(),
            cost_reports_are_provider_verified: false,
            actual_payment_settled: false,
            may_retry_paid_submission: false,
        })
    }
}
