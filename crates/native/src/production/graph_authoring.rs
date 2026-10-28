//! Fixed Graph authoring recipes through the existing pinned CLI transport.
//!
//! Each call uses one actual Core session for mount observation, fresh project
//! observation and the selected operation. No caller supplies a recipe, command,
//! binding reference, NativeTarget, readiness object or authority. Existing
//! application owner-invocation/read gates remain the caller's responsibility.
use super::{
    BrokerResult, ExpectedAuthority, ProductionClient, ProductionConnection,
    ProductionTransportCustody, is_sha256,
};
use motionwright_domain::{
    Change, GraphAuthoringContext, GraphAvailability, GraphBlockReason, GraphDependencyDescriptor,
    GraphFontSelection, GraphSourceSnapshot, Project, SEMANTIC_GRAPH_COMPONENT_VERSION,
    SEMANTIC_GRAPH_FONT_WEIGHT, SEMANTIC_GRAPH_PROFILE, SemanticGraph,
};
use motionwright_service::{SemanticGraphContextRequest, StudioService};
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult, Value,
    cooperation::{
        ObservationPage, PageCursor, Query, RecoveryRecord, RequestIdentity, ResourceVersion,
        validate_value,
    },
    json,
    types::{Envelope, SourceKind},
};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

pub(super) const RECIPE_COMMAND: &str = "recipe.run";
const PROVIDER: &str = "driver:motionwright";
const ROOT: &str = "motionwright-data";
const OBSERVE: &str = "driver.motionwright.observe";
const CONTEXT_SCOPE: &str = "graph-authoring";
const UPSERT: &str = "driver.motionwright.graph.upsert";
const REMOVE: &str = "driver.motionwright.graph.remove";
const RECOVERY: &str = "driver.motionwright.operation.get";
const CONTEXT_SCHEMA: &str = "motionwright.semantic-graph-authoring-context/1";

/// Typed entrypoints only. This is not serializable and confers no Host grant.
/// Construct from the actual callback service and drop before that callback
/// returns. Its clone retains that service's existing Store Arc, never a path
/// reopen or a connection carried across the shared Session's reopen command.
pub struct NativeGraphRecipeClient {
    client: ProductionClient,
    service: StudioService,
    expected_store: String,
}

/// Original request and response custody plus a typed interpretation.
///
/// A returned report may contain an original failed/UNKNOWN Core envelope.
/// Inspect outcome(); retaining this report never grants a future operation.
pub struct NativeGraphRecipeResult<T> {
    request_args: Value,
    recipe_sha256: String,
    request_sha256: String,
    original_envelope: Value,
    observation_envelope: Option<Envelope>,
    operation_envelope: Option<Envelope>,
    outcome: NativeResult<T>,
}

impl<T> NativeGraphRecipeResult<T> {
    pub fn recipe(&self) -> &Value {
        &self.request_args["recipe"]
    }
    pub fn request_args(&self) -> &Value {
        &self.request_args
    }
    /// SHA-256 of the exact serde_json bytes of recipe().
    pub fn recipe_sha256(&self) -> &str {
        &self.recipe_sha256
    }
    /// SHA-256 of the exact JSON argument bytes sent through CLI stdin.
    pub fn request_sha256(&self) -> &str {
        &self.request_sha256
    }
    pub fn original_envelope(&self) -> &Value {
        &self.original_envelope
    }
    pub fn observation_envelope(&self) -> Option<&Envelope> {
        self.observation_envelope.as_ref()
    }
    pub fn operation_envelope(&self) -> Option<&Envelope> {
        self.operation_envelope.as_ref()
    }
    pub fn outcome(&self) -> std::result::Result<&T, &Error> {
        self.outcome.as_ref()
    }
}

/// Parsed original application receipt. The Broker wire IDs remain separate
/// in the retained envelopes; this application key is never rewritten to them.
pub struct NativeGraphSaveReceipt {
    project: Project,
    resource: String,
    generation: Uuid,
    revision: u64,
    previous_revision: u64,
    app_request_id: String,
    replayed: bool,
    recovery: RequestIdentity,
}

impl NativeGraphSaveReceipt {
    pub fn project(&self) -> &Project {
        &self.project
    }
    pub fn resource(&self) -> &str {
        &self.resource
    }
    pub fn generation(&self) -> Uuid {
        self.generation
    }
    pub fn revision(&self) -> u64 {
        self.revision
    }
    pub fn previous_revision(&self) -> u64 {
        self.previous_revision
    }
    pub fn app_request_id(&self) -> &str {
        &self.app_request_id
    }
    pub fn replayed(&self) -> bool {
        self.replayed
    }
    pub fn recovery_identity(&self) -> &RequestIdentity {
        &self.recovery
    }
}

#[derive(Clone, Copy)]
enum Action {
    Context,
    Upsert,
    Remove,
    Recovery,
}
impl Action {
    fn command(self) -> &'static str {
        match self {
            Self::Context => OBSERVE,
            Self::Upsert => UPSERT,
            Self::Remove => REMOVE,
            Self::Recovery => RECOVERY,
        }
    }
    fn name(self) -> &'static str {
        match self {
            Self::Context => "motionwright-graph-context-v1",
            Self::Upsert => "motionwright-graph-upsert-v1",
            Self::Remove => "motionwright-graph-remove-v1",
            Self::Recovery => "motionwright-graph-recovery-v1",
        }
    }
    fn mutates(self) -> bool {
        matches!(self, Self::Upsert | Self::Remove)
    }
}

impl NativeGraphRecipeClient {
    pub fn new(connection: ProductionConnection, service: &StudioService) -> NativeResult<Self> {
        let client = ProductionClient::new(connection)?;
        let service = service.clone();
        let expected_store = service_store_identity(&service)?;
        Ok(Self {
            client,
            service,
            expected_store,
        })
    }

    /// Preserve original binary transport custody using the existing owner tool.
    pub fn with_transport_custody(mut self, custody: &ProductionTransportCustody) -> Self {
        self.client = self.client.with_transport_custody(custody);
        self
    }

    /// Prepare the exact canonical application recovery identity before dispatch.
    ///
    /// This is pure source preparation, independent of any client, connection or
    /// Host grant. Retain it with the original application key and Graph source
    /// before executing upsert. A key or source change requires a new preparation;
    /// an UNKNOWN never authorizes automatic replay.
    pub fn prepare_upsert(
        app_request_id: &str,
        graph: &SemanticGraph,
    ) -> NativeResult<RequestIdentity> {
        crate::validate_apply_recovery_raw_key(app_request_id)?;
        graph
            .validate_shape(None)
            .map_err(|_| invalid("Graph source shape is invalid"))?;
        let source = &graph.authorship.source_snapshot;
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(graph.clone()),
        };
        crate::apply_recovery_identity(
            source.project_id,
            source.generation,
            app_request_id,
            &change,
        )
    }

    /// Prepare the canonical removal identity without loading a current Graph.
    /// Historical replay after an already completed removal belongs to Store.
    pub fn prepare_remove(
        app_request_id: &str,
        graph_id: Uuid,
        scene_id: Uuid,
        source_snapshot: &GraphSourceSnapshot,
    ) -> NativeResult<RequestIdentity> {
        crate::validate_apply_recovery_raw_key(app_request_id)?;
        if graph_id.is_nil()
            || scene_id.is_nil()
            || source_snapshot.revision > semwright_native_sdk::cooperation::MAX_SAFE_INTEGER
        {
            return Err(invalid(
                "Graph removal requires canonical source identifiers and revision",
            ));
        }
        let change = Change::RemoveSemanticGraph {
            graph_id,
            scene_id,
            source_snapshot: source_snapshot.clone(),
        };
        crate::apply_recovery_identity(
            source_snapshot.project_id,
            source_snapshot.generation,
            app_request_id,
            &change,
        )
    }

    pub async fn authoring_context(
        &self,
        request: &SemanticGraphContextRequest,
    ) -> NativeResult<NativeGraphRecipeResult<GraphAuthoringContext>> {
        self.require_project(request.source_snapshot.project_id)?;
        let cursor_token = context_cursor_token(request)?;
        let args = fixed_recipe(
            Action::Context,
            json!({
                "resource": format!("project:{}", request.source_snapshot.project_id),
                "project_id": request.source_snapshot.project_id.to_string(),
                "generation": request.source_snapshot.generation.to_string(),
                "expected_store": self.expected_store,
                "branch_id": request.source_snapshot.branch_id.to_string(),
                "revision": request.source_snapshot.revision.to_string(),
                "context_token": cursor_token,
            }),
        );
        self.run(
            Action::Context,
            args,
            request.source_snapshot.project_id,
            |exchange| {
                if exchange.snapshot != request.source_snapshot {
                    return Err(stale(
                        "Fresh Graph observation differs from context selection",
                    ));
                }
                let original_summary =
                    decode_observed_data(operation_data(&exchange.observation)?)?;
                decode_authoring_observation(
                    operation_data(&exchange.operation)?,
                    request,
                    &original_summary.page.version,
                )
            },
        )
        .await
    }

    pub async fn upsert(
        &self,
        app_request_id: &str,
        graph: &SemanticGraph,
    ) -> NativeResult<NativeGraphRecipeResult<NativeGraphSaveReceipt>> {
        let source = &graph.authorship.source_snapshot;
        self.require_project(source.project_id)?;
        let recovery = Self::prepare_upsert(app_request_id, graph)?;
        let args = fixed_recipe(
            Action::Upsert,
            json!({
                "resource": format!("project:{}", source.project_id),
                "project_id": source.project_id.to_string(),
                "generation": source.generation.to_string(),
                "expected_store": self.expected_store,
                "request_id": app_request_id,
                "graph": graph,
            }),
        );
        // Do not rebase authorship or pre-reject an old branch/revision here.
        // Store owns exact durable replay before freshness; UNKNOWN is never retried.
        self.run(Action::Upsert, args, source.project_id, |exchange| {
            if exchange.snapshot.generation != source.generation {
                return Err(stale("Fresh Graph observation selects another generation"));
            }
            decode_save(
                operation_data(&exchange.operation)?,
                app_request_id,
                graph,
                &recovery,
            )
        })
        .await
    }

    pub async fn operation_recovery(
        &self,
        identity: &RequestIdentity,
    ) -> NativeResult<NativeGraphRecipeResult<RecoveryRecord>> {
        let (project_id, generation, _) = crate::decode_apply_recovery_identity(identity)?;
        self.require_project(project_id)?;
        let args = fixed_recipe(
            Action::Recovery,
            json!({
                "resource": identity.resource,
                "project_id": project_id.to_string(),
                "generation": generation.to_string(),
                "expected_store": self.expected_store,
                "request": identity,
            }),
        );
        self.run(Action::Recovery, args, project_id, |exchange| {
            if exchange.snapshot.generation != generation {
                return Err(stale(
                    "Recovery observation selects another project generation",
                ));
            }
            decode_recovery(operation_data(&exchange.operation)?, identity)
        })
        .await
    }

    pub async fn remove(
        &self,
        app_request_id: &str,
        graph_id: Uuid,
        scene_id: Uuid,
        source_snapshot: &GraphSourceSnapshot,
    ) -> NativeResult<NativeGraphRecipeResult<NativeGraphSaveReceipt>> {
        self.require_project(source_snapshot.project_id)?;
        let recovery = Self::prepare_remove(app_request_id, graph_id, scene_id, source_snapshot)?;
        let args = fixed_recipe(
            Action::Remove,
            json!({
                "resource":format!("project:{}",source_snapshot.project_id),
                "project_id":source_snapshot.project_id.to_string(),
                "generation":source_snapshot.generation.to_string(),
                "expected_store":self.expected_store,
                "request_id":app_request_id,"graph_id":graph_id.to_string(),
                "scene_id":scene_id.to_string(),"source_snapshot":source_snapshot,
            }),
        );
        // No current Graph/profile existence check is performed before Store.
        self.run(
            Action::Remove,
            args,
            source_snapshot.project_id,
            |exchange| {
                if exchange.snapshot.generation != source_snapshot.generation {
                    return Err(stale(
                        "Removal observation selects another project generation",
                    ));
                }
                decode_remove(
                    operation_data(&exchange.operation)?,
                    app_request_id,
                    graph_id,
                    source_snapshot,
                    &recovery,
                )
            },
        )
        .await
    }

    fn require_project(&self, project_id: Uuid) -> NativeResult<()> {
        if project_id.is_nil()
            || self.client.connection().resource != format!("project:{project_id}")
        {
            return Err(Error::new(
                ErrorCode::PolicyDenied,
                "Graph recipe connection is bound to another project resource",
            ));
        }
        Ok(())
    }

    fn require_same_store(&self) -> NativeResult<()> {
        if service_store_identity(&self.service)? != self.expected_store {
            return Err(stale("Graph callback Store physical identity changed"));
        }
        Ok(())
    }

    async fn run<T>(
        &self,
        action: Action,
        request_args: Value,
        project_id: Uuid,
        interpret: impl FnOnce(&Exchange) -> NativeResult<T>,
    ) -> NativeResult<NativeGraphRecipeResult<T>> {
        let recipe_sha256 = digest(&request_args["recipe"])?;
        let request_sha256 = digest(&request_args)?;
        // Recheck the real retained Store immediately before dispatch. The
        // mutex guard is gone before any await; the per-callback service clone
        // keeps the actual Arc live while the provider enforces its own guard.
        self.require_same_store()?;
        let original = self
            .client
            .execute_with_authority(
                RECIPE_COMMAND,
                request_args.clone(),
                action.mutates(),
                Some(ExpectedAuthority {
                    provider: "semwright-core",
                    source: SourceKind::Builtin,
                    provider_generation_required: false,
                }),
                None,
            )
            .await?;
        let original_envelope = original
            .value
            .get("original_envelope")
            .cloned()
            .ok_or_else(|| {
                after_dispatch(action, protocol("Original recipe envelope is absent"))
            })?;
        let mut observation_envelope = None;
        let mut operation_envelope = None;
        let outcome = match decode_outer(&original, &original_envelope) {
            // A genuine typed Core error is preserved before any later checks.
            Ok(Outer::Failed(error)) => Err(error),
            Ok(Outer::Completed(data)) => {
                match decode_exchange(data, action, project_id, &self.expected_store) {
                    Ok(exchange) => {
                        observation_envelope = Some(exchange.observation.clone());
                        operation_envelope = Some(exchange.operation.clone());
                        // Reads must still belong to this callback's actual Store.
                        // Preserve a committed write's original receipt instead of
                        // replacing it with a new post-write freshness rejection.
                        let current_store = if action.mutates() {
                            Ok(())
                        } else {
                            self.require_same_store()
                        };
                        current_store
                            .and_then(|()| interpret(&exchange))
                            .map_err(|error| after_dispatch(action, error))
                    }
                    Err(error) => Err(after_dispatch(action, error)),
                }
            }
            Err(error) => Err(after_dispatch(action, error)),
        };
        Ok(NativeGraphRecipeResult {
            request_args,
            recipe_sha256,
            request_sha256,
            original_envelope,
            observation_envelope,
            operation_envelope,
            outcome,
        })
    }
}

fn service_store_identity(service: &StudioService) -> NativeResult<String> {
    let shared = service.store();
    let store = shared.lock();
    // This getter inspects SQLite's retained main-database handle. The String
    // is a private comparison value, not authority or caller-supplied JSON.
    store.database_file_identity().map_err(crate::storage_error)
}

fn var(pointer: &str) -> Value {
    json!({"$var": pointer})
}
fn assertion(left: Value, op: &str, right: Value) -> Value {
    json!({"left":left,"op":op,"right":right})
}

/// The command roster and binding locations are fixed backend source. A full
/// $var replacement returns the typed input verbatim, without evaluating its
/// interior as recipe expressions (canonical semwright-recipes::resolve).
fn fixed_recipe(action: Action, inputs: Value) -> Value {
    let mut declarations = json!({
        "resource":{"kind":"string"}, "project_id":{"kind":"string"},
        "generation":{"kind":"string"},
        "expected_store":{"kind":"string"},
    });
    let mut checks = vec![
        assertion(
            var("/steps/observe/admission_current"),
            "equals",
            json!(true),
        ),
        assertion(
            var("/steps/observe/result/data/page/complete"),
            "equals",
            json!(true),
        ),
        assertion(
            var("/steps/observe/result/data/page/scope"),
            "equals",
            json!("summary"),
        ),
        assertion(
            var("/steps/observe/result/data/page/items"),
            "count_equals",
            json!(1),
        ),
        assertion(
            var("/steps/observe/result/data/page/version/resource"),
            "equals",
            var("/inputs/resource"),
        ),
        assertion(
            var("/steps/observe/result/data/page/version/generation"),
            "equals",
            var("/inputs/generation"),
        ),
        assertion(
            var("/steps/observe/result/data/page/items/0/id"),
            "equals",
            var("/inputs/project_id"),
        ),
        assertion(
            var("/steps/observe/result/data/page/items/0/revision"),
            "equals",
            var("/steps/observe/result/data/page/version/revision"),
        ),
        assertion(
            var("/steps/observe/result/data/page/items/0/store_identity"),
            "equals",
            var("/inputs/expected_store"),
        ),
    ];
    let reference = var("/steps/observe/result/data/ref");
    let operation_args = match action {
        Action::Context => {
            declarations["branch_id"] = json!({"kind":"string"});
            declarations["revision"] = json!({"kind":"string"});
            declarations["context_token"] = json!({"kind":"string"});
            checks.push(assertion(
                var("/steps/observe/result/data/page/items/0/active_branch"),
                "equals",
                var("/inputs/branch_id"),
            ));
            checks.push(assertion(
                var("/steps/observe/result/data/page/version/revision"),
                "equals",
                var("/inputs/revision"),
            ));
            json!({"ref":reference,"resource":var("/inputs/resource"),
                "scope":CONTEXT_SCOPE,"limit":1,
                "cursor":{"version":var("/steps/observe/result/data/page/version"),
                    "scope":CONTEXT_SCOPE,"token":var("/inputs/context_token")}})
        }
        Action::Upsert => {
            declarations["request_id"] = json!({"kind":"string"});
            declarations["graph"] = json!({"kind":"object"});
            json!({"ref":reference,"request_id":var("/inputs/request_id"),
                "graph":var("/inputs/graph"),"include_recovery":true,
                "expected_store":var("/inputs/expected_store")})
        }
        Action::Remove => {
            declarations["request_id"] = json!({"kind":"string"});
            declarations["graph_id"] = json!({"kind":"string"});
            declarations["scene_id"] = json!({"kind":"string"});
            declarations["source_snapshot"] = json!({"kind":"object"});
            json!({"ref":reference,"request_id":var("/inputs/request_id"),
                "graph_id":var("/inputs/graph_id"),"scene_id":var("/inputs/scene_id"),
                "source_snapshot":var("/inputs/source_snapshot"),"include_recovery":true,
                "expected_store":var("/inputs/expected_store")})
        }
        Action::Recovery => {
            declarations["request"] = json!({"kind":"object"});
            json!({"ref":reference,"request":var("/inputs/request")})
        }
    };
    let binding = var("/steps/mount/binding_ref");
    json!({
        "recipe":{
            "version":1,"name":action.name(),"description":"Fixed mounted Graph operation; no retries",
            "inputs":declarations,
            "steps":[
                {"id":"mount","command":"providers.mount.observe",
                    "args":{"provider":PROVIDER,"root":ROOT},"timeout_ms":5000,
                    "retry":{"attempts":1,"backoff_ms":0},
                    "assertions":[
                        assertion(var("/steps/mount/provider"),"equals",json!(PROVIDER)),
                        assertion(var("/steps/mount/mount/root"),"equals",json!(ROOT)),
                        assertion(var("/steps/mount/mount/read"),"equals",json!(true))
                    ]},
                {"id":"observe","command":"providers.execute_bound",
                    "args":{"binding_ref":binding,"request":{"command":OBSERVE,
                        "args":{"resource":var("/inputs/resource"),"scope":"summary","limit":1},
                        "dry_run":false,"backend":null}},
                    "timeout_ms":5000,"retry":{"attempts":1,"backoff_ms":0},"assertions":checks},
                {"id":"operation","command":"providers.execute_bound",
                    "args":{"binding_ref":var("/steps/mount/binding_ref"),
                        "request":{"command":action.command(),"args":operation_args,
                            "dry_run":false,"backend":null}},
                    "timeout_ms":30000,"retry":{"attempts":1,"backoff_ms":0}}
            ],
            "outputs":{
                "mount":{"kind":"object","value":var("/steps/mount")},
                "observe":{"kind":"object","value":var("/steps/observe")},
                "operation":{"kind":"object","value":var("/steps/operation")}
            }
        },
        "inputs":inputs
    })
}

fn invalid(message: &'static str) -> Error {
    Error::invalid(message)
}
fn protocol(message: &'static str) -> Error {
    Error::new(ErrorCode::ProtocolMismatch, message)
}
fn stale(message: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, message)
}
fn after_dispatch(action: Action, error: Error) -> Error {
    if action.mutates() {
        error.uncertain()
    } else {
        error
    }
}
fn digest(value: &Value) -> NativeResult<String> {
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(value)?)))
}
fn decode<T: serde::de::DeserializeOwned>(value: &Value) -> NativeResult<T> {
    if !value.is_object() {
        return Err(protocol("Graph response must be an object"));
    }
    serde_json::from_value(value.clone())
        .map_err(|_| protocol("Graph response has an invalid closed shape"))
}
fn require_object_fields(value: &Value, pointers: &[&str]) -> NativeResult<()> {
    if pointers
        .iter()
        .any(|pointer| !value.pointer(pointer).is_some_and(Value::is_object))
    {
        return Err(protocol("Graph response record must be an object"));
    }
    Ok(())
}
fn require_object_items(value: &Value, pointer: &str) -> NativeResult<()> {
    let items = value
        .pointer(pointer)
        .and_then(Value::as_array)
        .ok_or_else(|| protocol("Graph response record list must be an array"))?;
    if items.iter().any(|item| !item.is_object()) {
        return Err(protocol("Graph response list member must be an object"));
    }
    Ok(())
}
fn decode_envelope(value: &Value) -> NativeResult<Envelope> {
    require_object_fields(value, &["", "/execution"])?;
    for pointer in [
        "/error",
        "/error/progress",
        "/error/native_diagnostic",
        "/execution/provenance",
    ] {
        if value
            .pointer(pointer)
            .is_some_and(|member| !member.is_null())
        {
            require_object_fields(value, &[pointer])?;
        }
    }
    decode(value)
}
fn token(value: &str, max: usize) -> bool {
    !value.is_empty() && value.len() <= max && !value.chars().any(char::is_control)
}
fn uuid(value: &str) -> NativeResult<Uuid> {
    let parsed = Uuid::parse_str(value).map_err(|_| protocol("Graph response UUID is invalid"))?;
    if parsed.is_nil() || parsed.to_string() != value {
        return Err(protocol("Graph response UUID is not canonical and nonnil"));
    }
    Ok(parsed)
}
fn revision(value: &str) -> NativeResult<u64> {
    let parsed = value
        .parse::<u64>()
        .map_err(|_| protocol("Graph response revision is invalid"))?;
    if parsed.to_string() != value {
        return Err(protocol("Graph revision is not canonical"));
    }
    Ok(parsed)
}

fn context_cursor_token(request: &SemanticGraphContextRequest) -> NativeResult<String> {
    let source = &request.source_snapshot;
    if request.scene_id.is_nil()
        || request.variant_profile_ref.is_nil()
        || source.project_id.is_nil()
        || source.generation.is_nil()
        || source.branch_id.is_nil()
        || source.revision > semwright_native_sdk::cooperation::MAX_SAFE_INTEGER
    {
        return Err(invalid(
            "Graph authoring cursor requires canonical source, scene and profile identifiers",
        ));
    }
    // This is the compact original typed request, not a permit, a readiness
    // object or a new encoding. Its version is supplied independently by the
    // original summary page in this same Core recipe session.
    let token = serde_json::to_string(request)?;
    if token.len() > 1024 {
        return Err(invalid(
            "Graph authoring cursor exceeds the existing token budget",
        ));
    }
    Ok(token)
}

fn datum_key(value: &str) -> bool {
    !value.trim().is_empty()
        && value.len() <= 64
        && !value
            .chars()
            .any(|c| (c <= '\u{1f}' && c != '\n') || c == '\u{7f}')
}

enum Outer<'a> {
    Failed(Error),
    Completed(&'a Value),
}
fn decode_outer<'a>(original: &BrokerResult, value: &'a Value) -> NativeResult<Outer<'a>> {
    let envelope = decode_envelope(value)?;
    if envelope.command != RECIPE_COMMAND
        || envelope.request_id != original.request_id
        || !token(&envelope.request_id, 128)
        || envelope.execution.dry_run
    {
        return Err(protocol("Graph recipe envelope identity is inconsistent"));
    }
    if !envelope.ok {
        if envelope.data.is_some() {
            return Err(protocol("Failed recipe carries success data"));
        }
        return envelope
            .error
            .map(Outer::Failed)
            .ok_or_else(|| protocol("Failed recipe has no original typed error"));
    }
    if envelope.error.is_some()
        || original.value.get("transport_status_success") != Some(&json!(true))
    {
        return Err(protocol(
            "Graph recipe completion contradicts process status",
        ));
    }
    authenticate(value, "semwright-core", SourceKind::Builtin, None)?;
    value
        .get("data")
        .filter(|data| data.is_object())
        .map(Outer::Completed)
        .ok_or_else(|| protocol("Graph recipe has no original result object"))
}

fn authenticate(
    value: &Value,
    provider: &str,
    source: SourceKind,
    generation: Option<u64>,
) -> NativeResult<()> {
    let execution = value
        .get("execution")
        .ok_or_else(|| protocol("Graph execution is absent"))?;
    let provenance = execution
        .get("provenance")
        .ok_or_else(|| protocol("Graph provenance is absent"))?;
    let expected_backend = if provider == "semwright-core" {
        "core"
    } else {
        provider
    };
    if execution.get("backend").and_then(Value::as_str) != Some(expected_backend)
        || execution.get("dry_run") != Some(&json!(false))
        || !matches!(
            execution.get("policy_decision").and_then(Value::as_str),
            Some("allow" | "allow_after_confirmation")
        )
        || provenance.get("provider").and_then(Value::as_str) != Some(provider)
        || provenance.get("execution_provider").and_then(Value::as_str) != Some(provider)
        || provenance.get("source") != Some(&json!(source))
        || !provenance
            .get("descriptor_sha256")
            .and_then(Value::as_str)
            .is_some_and(is_sha256)
        || provenance
            .get("catalog_revision")
            .and_then(Value::as_u64)
            .is_none()
        || generation.is_some_and(|generation| {
            provenance
                .get("provider_generation")
                .and_then(Value::as_u64)
                != Some(generation)
        })
    {
        return Err(protocol(
            "Graph provenance differs from the actual selected provider",
        ));
    }
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecipeData {
    completed: bool,
    steps: Vec<StepResult>,
    outputs: RecipeOutputs,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StepResult {
    step: String,
    ok: bool,
    attempts: u32,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecipeOutputs {
    mount: MountObserved,
    observe: BoundResult,
    operation: BoundResult,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MountObserved {
    binding_ref: String,
    provider: String,
    provider_generation: u64,
    connection: String,
    mount: MountView,
    admission_ttl_ms: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MountView {
    root: String,
    path: String,
    read: bool,
    write: bool,
    execute: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BoundResult {
    binding_ref: String,
    result: Value,
    admission_current: bool,
    admission_state: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservedData {
    page: ObservationPage,
    #[serde(rename = "ref")]
    reference: String,
}
struct Exchange {
    observation: Envelope,
    operation: Envelope,
    snapshot: GraphSourceSnapshot,
}

fn decode_recipe_data(value: &Value) -> NativeResult<RecipeData> {
    require_object_fields(
        value,
        &[
            "",
            "/outputs",
            "/outputs/mount",
            "/outputs/mount/mount",
            "/outputs/observe",
            "/outputs/operation",
        ],
    )?;
    require_object_items(value, "/steps")?;
    decode(value)
}

fn decode_exchange(
    value: &Value,
    action: Action,
    project_id: Uuid,
    expected_store: &str,
) -> NativeResult<Exchange> {
    let wire = decode_recipe_data(value)?;
    if !wire.completed
        || wire.steps.len() != 3
        || wire
            .steps
            .iter()
            .zip(["mount", "observe", "operation"])
            .any(|(step, name)| step.step != name || !step.ok || step.attempts != 1)
    {
        return Err(protocol(
            "Graph recipe did not complete its exact one-attempt roster",
        ));
    }
    let mount = &wire.outputs.mount;
    if mount.provider != PROVIDER
        || mount.mount.root != ROOT
        || !mount.mount.read
        || (action.mutates() && !mount.mount.write)
        || !token(&mount.binding_ref, 512)
        || !token(&mount.connection, 512)
        || !token(&mount.mount.path, 4096)
        || mount.admission_ttl_ms == 0
    {
        return Err(protocol(
            "Graph mount observation differs from its selected native root",
        ));
    }
    // Preserve the real execute flag and physical path only in the original
    // envelope. Neither becomes a local write/execute capability.
    let _ = mount.mount.execute;
    let observation = decode_bound(&wire.outputs.observe, mount, OBSERVE, true)?;
    let operation = decode_bound(
        &wire.outputs.operation,
        mount,
        action.command(),
        !action.mutates(),
    )?;
    let observed = decode_observation(operation_data(&observation)?, project_id, expected_store)?;
    Ok(Exchange {
        observation,
        operation,
        snapshot: observed,
    })
}

fn decode_bound(
    wire: &BoundResult,
    mount: &MountObserved,
    command: &str,
    require_current: bool,
) -> NativeResult<Envelope> {
    if wire.binding_ref != mount.binding_ref
        || (require_current && !wire.admission_current)
        || (wire.admission_current && wire.admission_state != "current")
        || (!wire.admission_current
            && !matches!(wire.admission_state.as_str(), "expired" | "closing"))
    {
        return Err(protocol(
            "Graph completion differs from the original mount binding",
        ));
    }
    let envelope = decode_envelope(&wire.result)?;
    if !envelope.ok
        || envelope.error.is_some()
        || envelope.command != command
        || !token(&envelope.request_id, 128)
    {
        return Err(protocol(
            "Bound Graph result is not an authentic successful Driver envelope",
        ));
    }
    authenticate(
        &wire.result,
        PROVIDER,
        SourceKind::Driver,
        Some(mount.provider_generation),
    )?;
    Ok(envelope)
}

fn operation_data(envelope: &Envelope) -> NativeResult<&Value> {
    envelope
        .data
        .as_ref()
        .ok_or_else(|| protocol("Graph Driver response data is absent"))
}

fn decode_observed_data(value: &Value) -> NativeResult<ObservedData> {
    require_object_fields(value, &["", "/page", "/page/version"])?;
    if value
        .pointer("/page/next")
        .is_some_and(|next| !next.is_null())
    {
        require_object_fields(value, &["/page/next", "/page/next/version"])?;
    }
    decode(value)
}

fn decode_observation(
    value: &Value,
    project_id: Uuid,
    expected_store: &str,
) -> NativeResult<GraphSourceSnapshot> {
    let observed = decode_observed_data(value)?;
    if !token(&observed.reference, 512) {
        return Err(protocol("Observed native reference is absent"));
    }
    let query = Query {
        resource: format!("project:{project_id}"),
        scope: "summary".into(),
        limit: 1,
        cursor: None,
    };
    observed.page.validate_for(&query)?;
    if !observed.page.complete || observed.page.next.is_some() || observed.page.items.len() != 1 {
        return Err(protocol("Graph summary is not one complete original page"));
    }
    let summary = &observed.page.items[0];
    let text = |key: &str| {
        summary
            .get(key)
            .and_then(Value::as_str)
            .ok_or_else(|| protocol("Graph project summary identity is absent"))
    };
    if text("store_identity")? != expected_store {
        return Err(stale("Graph summary belongs to another physical Store"));
    }
    if uuid(text("id")?)? != project_id
        || text("revision")? != observed.page.version.revision.as_str()
    {
        return Err(protocol(
            "Project summary differs from its full application revision",
        ));
    }
    Ok(GraphSourceSnapshot {
        project_id,
        generation: uuid(&observed.page.version.generation)?,
        branch_id: uuid(text("active_branch")?)?,
        revision: revision(observed.page.version.revision.as_str())?,
    })
}

fn decode_authoring_observation(
    value: &Value,
    expected: &SemanticGraphContextRequest,
    original_summary_version: &ResourceVersion,
) -> NativeResult<GraphAuthoringContext> {
    let observed = decode_observed_data(value)?;
    if !token(&observed.reference, 512) {
        return Err(protocol(
            "Graph authoring observation has no original native reference",
        ));
    }
    let query = Query {
        resource: format!("project:{}", expected.source_snapshot.project_id),
        scope: CONTEXT_SCOPE.into(),
        limit: 1,
        cursor: Some(PageCursor {
            version: original_summary_version.clone(),
            scope: CONTEXT_SCOPE.into(),
            token: context_cursor_token(expected)?,
        }),
    };
    observed.page.validate_for(&query)?;
    if !observed.page.complete
        || observed.page.next.is_some()
        || observed.page.items.len() != 1
        || observed.page.version.generation != expected.source_snapshot.generation.to_string()
        || observed.page.version.revision.as_str() != expected.source_snapshot.revision.to_string()
    {
        return Err(stale(
            "Graph authoring page differs from the original project selection",
        ));
    }
    // The existing native observer derives this DTO from its persisted Project.
    // No caller-authored context or source snapshot is sent as read authority.
    decode_context(&observed.page.items[0], expected)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContextWire {
    schema: String,
    source_snapshot: GraphSourceSnapshot,
    scene_id: Uuid,
    variant_profile_ref: Uuid,
    component_version: u8,
    native_profile: String,
    font_weight: u16,
    font_selection: FontsWire,
    availability: AvailabilityWire,
    blocking_reasons: Vec<ReasonWire>,
    dependencies: Vec<DependencyWire>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FontsWire {
    primary_family: String,
    mono_family: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum AvailabilityWire {
    Ready,
    Blocked,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum ReasonWire {
    GraphWriteUnavailable,
    NativeGraphProfileUnavailable,
    UnsupportedRendererGuardMissing,
    FontSourceUnavailable,
    DependencyDescriptorUnavailable,
    SourceStale,
    ProfileUnsupported,
}
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum DependencyWire {
    Scene {
        scene_id: Uuid,
    },
    Claim {
        claim_id: Uuid,
        content_sha256: String,
    },
    Asset {
        asset_id: Uuid,
        content_sha256: String,
    },
    CanvasNode {
        scene_id: Uuid,
        node_id: Uuid,
        content_sha256: String,
    },
    MeasuredDatum {
        table_id: Uuid,
        table_version: u32,
        datum_key: String,
        source_sha256: String,
    },
    Brand {
        id: Uuid,
        version: u64,
        content_sha256: String,
    },
}

fn decode_context(
    value: &Value,
    expected: &SemanticGraphContextRequest,
) -> NativeResult<GraphAuthoringContext> {
    validate_value(value)?;
    require_object_fields(value, &["", "/source_snapshot", "/font_selection"])?;
    require_object_items(value, "/dependencies")?;
    let wire: ContextWire = decode(value)?;
    if wire.schema != CONTEXT_SCHEMA
        || wire.source_snapshot != expected.source_snapshot
        || wire.source_snapshot.project_id.is_nil()
        || wire.source_snapshot.generation.is_nil()
        || wire.source_snapshot.branch_id.is_nil()
        || wire.source_snapshot.revision > semwright_native_sdk::cooperation::MAX_SAFE_INTEGER
        || wire.scene_id.is_nil()
        || wire.variant_profile_ref.is_nil()
        || wire.scene_id != expected.scene_id
        || wire.variant_profile_ref != expected.variant_profile_ref
        || wire.component_version != SEMANTIC_GRAPH_COMPONENT_VERSION
        || wire.native_profile != SEMANTIC_GRAPH_PROFILE
        || wire.font_weight != SEMANTIC_GRAPH_FONT_WEIGHT
        || wire.font_selection.primary_family != "Instrument Sans Variable"
        || wire.font_selection.mono_family != "IBM Plex Mono"
        || (matches!(&wire.availability, AvailabilityWire::Ready)
            != wire.blocking_reasons.is_empty())
    {
        return Err(protocol(
            "Graph authoring context differs from the selected closed profile",
        ));
    }
    let dependencies = wire
        .dependencies
        .into_iter()
        .map(|dependency| {
            let result = match dependency {
                DependencyWire::Scene { scene_id } if !scene_id.is_nil() => {
                    GraphDependencyDescriptor::Scene { scene_id }
                }
                DependencyWire::Claim {
                    claim_id,
                    content_sha256,
                } if !claim_id.is_nil() && is_sha256(&content_sha256) => {
                    GraphDependencyDescriptor::Claim {
                        claim_id,
                        content_sha256,
                    }
                }
                DependencyWire::Asset {
                    asset_id,
                    content_sha256,
                } if !asset_id.is_nil() && is_sha256(&content_sha256) => {
                    GraphDependencyDescriptor::Asset {
                        asset_id,
                        content_sha256,
                    }
                }
                DependencyWire::CanvasNode {
                    scene_id,
                    node_id,
                    content_sha256,
                } if !scene_id.is_nil() && !node_id.is_nil() && is_sha256(&content_sha256) => {
                    GraphDependencyDescriptor::CanvasNode {
                        scene_id,
                        node_id,
                        content_sha256,
                    }
                }
                DependencyWire::MeasuredDatum {
                    table_id,
                    table_version,
                    datum_key: key,
                    source_sha256,
                } if !table_id.is_nil()
                    && table_version > 0
                    && datum_key(&key)
                    && is_sha256(&source_sha256) =>
                {
                    GraphDependencyDescriptor::MeasuredDatum {
                        table_id,
                        table_version,
                        datum_key: key,
                        source_sha256,
                    }
                }
                DependencyWire::Brand {
                    id,
                    version,
                    content_sha256,
                } if !id.is_nil()
                    && version > 0
                    && version <= semwright_native_sdk::cooperation::MAX_SAFE_INTEGER
                    && is_sha256(&content_sha256) =>
                {
                    GraphDependencyDescriptor::Brand {
                        id,
                        version,
                        content_sha256,
                    }
                }
                _ => return Err(protocol("Graph dependency descriptor is invalid")),
            };
            Ok(result)
        })
        .collect::<NativeResult<Vec<_>>>()?;
    let blocking_reasons = wire
        .blocking_reasons
        .into_iter()
        .map(|reason| match reason {
            ReasonWire::GraphWriteUnavailable => GraphBlockReason::GraphWriteUnavailable,
            ReasonWire::NativeGraphProfileUnavailable => {
                GraphBlockReason::NativeGraphProfileUnavailable
            }
            ReasonWire::UnsupportedRendererGuardMissing => {
                GraphBlockReason::UnsupportedRendererGuardMissing
            }
            ReasonWire::FontSourceUnavailable => GraphBlockReason::FontSourceUnavailable,
            ReasonWire::DependencyDescriptorUnavailable => {
                GraphBlockReason::DependencyDescriptorUnavailable
            }
            ReasonWire::SourceStale => GraphBlockReason::SourceStale,
            ReasonWire::ProfileUnsupported => GraphBlockReason::ProfileUnsupported,
        })
        .collect();
    Ok(GraphAuthoringContext {
        schema: CONTEXT_SCHEMA,
        source_snapshot: wire.source_snapshot,
        scene_id: wire.scene_id,
        variant_profile_ref: wire.variant_profile_ref,
        component_version: wire.component_version,
        native_profile: SEMANTIC_GRAPH_PROFILE,
        font_weight: wire.font_weight,
        font_selection: GraphFontSelection {
            primary_family: wire.font_selection.primary_family,
            mono_family: wire.font_selection.mono_family,
        },
        availability: match wire.availability {
            AvailabilityWire::Ready => GraphAvailability::Ready,
            AvailabilityWire::Blocked => GraphAvailability::Blocked,
        },
        blocking_reasons,
        dependencies,
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SaveWire {
    resource: String,
    generation: String,
    revision: String,
    previous_revision: String,
    request_id: String,
    replayed: bool,
    project: Project,
    recovery: RequestIdentity,
}
fn decode_save(
    value: &Value,
    app_request_id: &str,
    graph: &SemanticGraph,
    recovery: &RequestIdentity,
) -> NativeResult<NativeGraphSaveReceipt> {
    let receipt = decode_apply_receipt(
        value,
        app_request_id,
        &graph.authorship.source_snapshot,
        recovery,
    )?;
    if !receipt
        .project
        .production_design
        .semantic_graphs
        .iter()
        .any(|saved| saved == graph)
    {
        return Err(protocol(
            "Graph save receipt differs from the original exact source",
        ));
    }
    Ok(receipt)
}

fn decode_remove(
    value: &Value,
    app_request_id: &str,
    graph_id: Uuid,
    source_snapshot: &GraphSourceSnapshot,
    recovery: &RequestIdentity,
) -> NativeResult<NativeGraphSaveReceipt> {
    let receipt = decode_apply_receipt(value, app_request_id, source_snapshot, recovery)?;
    if receipt
        .project
        .production_design
        .semantic_graphs
        .iter()
        .any(|saved| saved.id == graph_id)
    {
        return Err(protocol(
            "Graph removal receipt still contains the removed source",
        ));
    }
    Ok(receipt)
}

fn decode_apply_receipt(
    value: &Value,
    app_request_id: &str,
    source_snapshot: &GraphSourceSnapshot,
    recovery: &RequestIdentity,
) -> NativeResult<NativeGraphSaveReceipt> {
    require_object_fields(value, &["", "/project", "/recovery"])?;
    let wire: SaveWire = decode(value)?;
    // Domain loading may ignore unknown members or fill omitted defaults.
    // An acknowledgement must retain the complete original Project instead.
    let projected = serde_json::to_value(&wire.project)
        .map_err(|_| protocol("Graph receipt Project cannot be preserved"))?;
    if value.get("project") != Some(&projected) {
        return Err(protocol(
            "Graph receipt Project loses original source fields",
        ));
    }
    let generation = uuid(&wire.generation)?;
    let current_revision = revision(&wire.revision)?;
    let previous_revision = revision(&wire.previous_revision)?;
    // Initial completion and exact replay both return the original one-change
    // result. A later current Project must never replace that stored receipt.
    let expected_revision = source_snapshot
        .revision
        .checked_add(1)
        .ok_or_else(|| protocol("Graph source revision cannot advance"))?;
    if wire.resource != format!("project:{}", source_snapshot.project_id)
        || generation != source_snapshot.generation
        || wire.request_id != app_request_id
        || wire.recovery != *recovery
        || wire.project.resource_key() != wire.resource
        || wire.project.generation != generation
        || wire.project.active_branch != source_snapshot.branch_id
        || wire.project.revision != current_revision
        || previous_revision != source_snapshot.revision
        || current_revision != expected_revision
    {
        return Err(protocol(
            "Graph save receipt differs from the original application request",
        ));
    }
    wire.project
        .validate()
        .map_err(|_| protocol("Graph receipt Project is invalid"))?;
    wire.recovery.validate()?;
    Ok(NativeGraphSaveReceipt {
        project: wire.project,
        resource: wire.resource,
        generation,
        revision: current_revision,
        previous_revision,
        app_request_id: wire.request_id,
        replayed: wire.replayed,
        recovery: wire.recovery,
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RecoveryWire {
    record: RecoveryRecord,
    historical_only: bool,
    current_authority: bool,
    replay_allowed: bool,
}

fn decode_recovery(value: &Value, identity: &RequestIdentity) -> NativeResult<RecoveryRecord> {
    require_object_fields(value, &["", "/record", "/record/identity"])?;
    let wire: RecoveryWire = decode(value)?;
    if !wire.historical_only || wire.current_authority || wire.replay_allowed {
        return Err(protocol(
            "Recovery response promotes historical data to authority",
        ));
    }
    wire.record.validate_for(identity)?;
    Ok(wire.record)
}

#[cfg(test)]
#[path = "graph_authoring_tests.rs"]
mod tests;
