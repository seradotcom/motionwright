//! Native-owned JSONL adapter for the exact shared Studio review Session.
//!
//! Each callback constructs the typed Native client from the actual opened
//! Service. That client obtains the retained SQLite connection identity and
//! binds it to the original Host summary and final provider guard. Unsupported
//! identity queries reject before Graph dispatch; no UUID, pathname, JSON flag
//! or cached Ready replaces the connection witness or current Host authority.
//! This executable requires the private owner/custody lifecycle.

#[path = "../../service/examples/studio_review_session.rs"]
#[allow(dead_code)] // The shared executable also has its own main and test helpers.
mod shared_session;

#[path = "../../../apps/desktop/src-tauri/src/semantic_graph_mutation_error.rs"]
mod semantic_graph_mutation_error;

#[path = "../../../apps/desktop/src-tauri/src/semantic_graph_recovery.rs"]
mod semantic_graph_recovery;

use motionwright_domain::{Change, GraphAuthoringContext, Project};
use motionwright_native::production::{
    NativeGraphRecipeClient, NativeGraphRecipeResult, NativeGraphSaveReceipt, ProductionConnection,
    ProductionTransportCustody,
};
use motionwright_service::{SemanticGraphContextRequest, StudioService};
use semantic_graph_mutation_error::{GraphInvokeFailure, InvalidGraphRequestId};
use semantic_graph_recovery::{
    GraphRecoveryReadError, GraphRecoveryRequest, PreparedGraphRecovery,
};
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult,
    cooperation::{RecoveryRecord, RequestIdentity},
};
use serde_json::{Value, json};
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

const CONNECTION_ENV: &str = "MOTIONWRIGHT_GRAPH_CONNECTION";
const BINDING_UNAVAILABLE: &str =
    "Native Graph cannot bind the actual opened Store and owner connection.";
// These are the existing selected-custody ceiling values, not another owner
// allowance. The enclosing collector must account for these files in the same
// 512 MiB / 4096-file pool with all other private originals.
const MAX_PRIVATE_FILES: usize = 4096;
const MAX_PRIVATE_TOTAL_BYTES: usize = 512 * 1024 * 1024;
const MAX_PRIVATE_RECORD_BYTES: usize = 16 * 1024 * 1024;
const CALLBACK_LIMIT_ENV: &str = "MOTIONWRIGHT_GRAPH_CALLBACK_CAP";
const PRIVATE_BYTES_ENV: &str = "MOTIONWRIGHT_GRAPH_PRIVATE_MAX_BYTES";
const PRIVATE_FILES_ENV: &str = "MOTIONWRIGHT_GRAPH_PRIVATE_MAX_FILES";
const CALLBACK_UNAVAILABLE: &str = "Native Graph callback admission is unavailable.";

/// Closed read-only context failure taxonomy. Every public value is a literal
/// schema/stage and (only when present) a pinned Semwright ErrorCode variant.
/// Actual messages, candidates, DTOs, raw envelopes and paths stay private.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum GraphContextStage {
    Admission,
    Binding,
    Custody,
    Local,
    Transport,
    Native,
}

impl GraphContextStage {
    fn label(self) -> &'static str {
        match self {
            GraphContextStage::Admission => "ADMISSION",
            GraphContextStage::Binding => "BINDING",
            GraphContextStage::Custody => "CUSTODY",
            GraphContextStage::Local => "LOCAL",
            GraphContextStage::Transport => "TRANSPORT",
            GraphContextStage::Native => "NATIVE",
        }
    }
}

/// An exhaustive SDK enum match deliberately fails compilation if the source
/// pin adds a code before review. No Debug/display serialization.
fn graph_context_code(code: ErrorCode) -> &'static str {
    match code {
        ErrorCode::Unsupported => "Unsupported",
        ErrorCode::Unavailable => "Unavailable",
        ErrorCode::PermissionDenied => "PermissionDenied",
        ErrorCode::ConsentRequired => "ConsentRequired",
        ErrorCode::PolicyDenied => "PolicyDenied",
        ErrorCode::NotFound => "NotFound",
        ErrorCode::AmbiguousTarget => "AmbiguousTarget",
        ErrorCode::StaleReference => "StaleReference",
        ErrorCode::BackendFailed => "BackendFailed",
        ErrorCode::Timeout => "Timeout",
        ErrorCode::InvalidArgument => "InvalidArgument",
        ErrorCode::PluginProtocolError => "PluginProtocolError",
        ErrorCode::SandboxDenied => "SandboxDenied",
        ErrorCode::Conflict => "Conflict",
        ErrorCode::Cancelled => "Cancelled",
        ErrorCode::Internal => "Internal",
        ErrorCode::ProtocolMismatch => "ProtocolMismatch",
        ErrorCode::ResourceExhausted => "ResourceExhausted",
    }
}

fn graph_context_failure(stage: GraphContextStage, error: Option<&Error>) -> Value {
    json!({
        "schema":"motionwright.native.graph-context-failure/1",
        "stage":stage.label(),
        "error_code":error.map(|error| graph_context_code(error.code)),
    })
}

/// Transport uncertainty outranks even an otherwise original Native error.
/// Only the typed Core envelope can identify a genuine Native rejection.
fn graph_context_report_failure(
    error: &Error,
    original_native: bool,
    transport_failed: bool,
) -> Value {
    let stage = if transport_failed {
        GraphContextStage::Transport
    } else if original_native {
        GraphContextStage::Native
    } else {
        GraphContextStage::Local
    };
    graph_context_failure(stage, Some(error))
}

/// A returned context is not READY while its original CLI transport remains
/// uncertain. This guard runs only after the original report is retained, and
/// does not overwrite a typed failed outcome or retry the callback.
fn graph_context_require_confirmed_transport(transport_failed: bool) -> Result<(), Value> {
    if transport_failed {
        Err(graph_context_failure(GraphContextStage::Transport, None))
    } else {
        Ok(())
    }
}

/// Restrictions supplied by the existing parent allocation. No value here
/// grants SDK authority or chooses a different connection/session file.
#[derive(Clone, Copy)]
struct ClientRestrictions {
    callbacks: usize,
    private_bytes: usize,
    private_files: usize,
}

impl ClientRestrictions {
    fn parse(
        callbacks: Option<&str>,
        bytes: Option<&str>,
        files: Option<&str>,
    ) -> Result<Self, String> {
        let decimal = |raw: Option<&str>| -> Result<usize, String> {
            let raw = raw.ok_or(CALLBACK_UNAVAILABLE)?;
            if raw.is_empty()
                || raw.len() > usize::MAX.to_string().len()
                || !raw.bytes().all(|byte| byte.is_ascii_digit())
                || (raw.len() > 1 && raw.starts_with('0'))
            {
                return Err(CALLBACK_UNAVAILABLE.into());
            }
            raw.parse().map_err(|_| CALLBACK_UNAVAILABLE.into())
        };
        let restrictions = Self {
            callbacks: decimal(callbacks)?,
            private_bytes: decimal(bytes)?,
            private_files: decimal(files)?,
        };
        if restrictions.private_bytes == 0
            || restrictions.private_bytes > MAX_PRIVATE_TOTAL_BYTES
            || restrictions.private_files == 0
            || restrictions.private_files > MAX_PRIVATE_FILES
        {
            return Err(CALLBACK_UNAVAILABLE.into());
        }
        Ok(restrictions)
    }

    fn from_parent_environment() -> Result<Self, String> {
        let callbacks = std::env::var(CALLBACK_LIMIT_ENV).ok();
        let bytes = std::env::var(PRIVATE_BYTES_ENV).ok();
        let files = std::env::var(PRIVATE_FILES_ENV).ok();
        Self::parse(callbacks.as_deref(), bytes.as_deref(), files.as_deref())
    }

    fn private_observation(&self) -> Value {
        json!({"maximum_callbacks":self.callbacks,"private_bytes":self.private_bytes,
            "private_files":self.private_files,"authority":false,"automatic_retry":false})
    }
}

/// This state belongs to one original Native backend. Session::reopen moves
/// that same backend; it cannot reset the consumed callbacks or failure latch.
struct CallbackBudget {
    maximum: usize,
    consumed: usize,
    stopped: bool,
}

impl CallbackBudget {
    fn new(maximum: usize) -> Self {
        Self {
            maximum,
            consumed: 0,
            stopped: false,
        }
    }

    fn reserve(&mut self) -> Result<usize, String> {
        if self.stopped || self.consumed >= self.maximum {
            self.stopped = true;
            return Err(CALLBACK_UNAVAILABLE.into());
        }
        self.consumed += 1; // Consume before validation, journaling or any CLI effect. No refund.
        Ok(self.consumed)
    }

    fn stop(&mut self) {
        self.stopped = true;
    }

    fn observe_return(&mut self, transport_failed: bool, typed_error: Option<&Error>) {
        // A returned CLI success does not make the typed Graph outcome known.
        // Decode/interpret failures after a mutation and original UNKNOWN Core
        // errors retain their actual typed knownness. Restrict future calls;
        // never mutate that error or replace the retained original report.
        if transport_failed || typed_error.is_some_and(|error| !error.outcome_known) {
            self.stop();
        }
    }

    fn private_observation(&self) -> Value {
        json!({"maximum_callbacks":self.maximum,"consumed_callbacks":self.consumed,
            "admissions_stopped":self.stopped,"refunds":0,"authority":false,"automatic_retry":false})
    }
}

// UUID validation and the public mutation DTO belong to the shared codec. This adapter
// supplies only the actual phase and, when present, the original typed error;
// it never parses diagnostics, raw envelopes, error codes or recipe progress.
fn mutation_failure(failure: Result<GraphInvokeFailure, InvalidGraphRequestId>) -> Value {
    failure
        .map(|failure| failure.to_value())
        .unwrap_or_else(|error| Value::String(error.to_string()))
}

fn mutation_custody_failure(request_id: &str, _error: String) -> Value {
    // Preserve the private original unchanged. Missing/partial durable custody
    // cannot publish success or a known rejection, even if either was observed.
    mutation_failure(GraphInvokeFailure::unknown(request_id))
}

struct PreparedMutation {
    original_change: Change,
    recovery: RequestIdentity,
}

fn prepare_mutation(
    project: &Project,
    request_id: &str,
    change: &Change,
) -> NativeResult<PreparedMutation> {
    let recovery = match change {
        Change::UpsertSemanticGraph { graph } => {
            let source = &graph.authorship.source_snapshot;
            if source.project_id != project.id || source.generation != project.generation {
                return Err(Error::invalid(
                    "Graph source selects another project generation",
                ));
            }
            NativeGraphRecipeClient::prepare_upsert(request_id, graph)?
        }
        Change::RemoveSemanticGraph {
            graph_id,
            scene_id,
            source_snapshot,
        } => {
            if source_snapshot.project_id != project.id
                || source_snapshot.generation != project.generation
            {
                return Err(Error::invalid(
                    "Graph removal selects another project generation",
                ));
            }
            NativeGraphRecipeClient::prepare_remove(
                request_id,
                *graph_id,
                *scene_id,
                source_snapshot,
            )?
        }
        _ => {
            return Err(Error::invalid(
                "Native Graph adapter only accepts canonical Graph changes",
            ));
        }
    };
    // Do not rebase revision/branch or load a current Graph. Durable historical
    // replay belongs to the original Store operation and retained request key.
    Ok(PreparedMutation {
        original_change: change.clone(),
        recovery,
    })
}

struct PrivateOriginals {
    root: PathBuf,
    next: usize,
    reserved_files: usize,
    reserved_bytes: usize,
    // Outstanding result records hold their entire 16 MiB ceiling until
    // durable finish. This is a reservation, never a refund of spent bytes.
    pending_results: BTreeSet<usize>,
    maximum_files: usize,
    maximum_bytes: usize,
    stopped: bool,
    unpublished: Option<Value>,
}

impl PrivateOriginals {
    fn create(connection_file: &Path, restrictions: ClientRestrictions) -> Result<Self, String> {
        let parent = connection_file
            .parent()
            .ok_or("Native Graph connection has no private parent")?;
        let metadata = fs::symlink_metadata(parent)
            .map_err(|_| "Native Graph private parent is unavailable")?;
        #[cfg(windows)]
        let reparse = {
            use std::os::windows::fs::MetadataExt;
            metadata.file_attributes() & 0x400 != 0
        };
        #[cfg(not(windows))]
        let reparse = metadata.file_type().is_symlink();
        if !connection_file.is_absolute() || !metadata.is_dir() || reparse {
            return Err(
                "Native Graph requires an absolute canonical private connection directory".into(),
            );
        }
        // ProductionConnection::load has already validated the original path.
        // Canonicalize only this private publication root; on Windows this also
        // retains the verbatim prefix, avoiding a false E:\ vs \\?\E:\ mismatch.
        // No pathname or equality here is a SQLite connection identity witness.
        let parent = parent
            .canonicalize()
            .map_err(|_| "Native Graph private parent is unavailable")?;
        let name = connection_file
            .file_name()
            .ok_or("Native Graph connection filename is absent")?;
        let mut name = name.to_os_string();
        name.push(".graph-originals");
        let root = parent.join(name);
        let builder = fs::DirBuilder::new();
        #[cfg(unix)]
        let builder = {
            use std::os::unix::fs::DirBuilderExt;
            let mut builder = builder;
            builder.mode(0o700);
            builder
        };
        // A reused per-client connection cannot replace or resume its originals.
        builder
            .create(&root)
            .map_err(|_| "Native Graph original custody directory is not fresh")?;
        Ok(Self {
            root,
            next: 0,
            reserved_files: 0,
            reserved_bytes: 0,
            pending_results: BTreeSet::new(),
            maximum_files: restrictions.private_files,
            maximum_bytes: restrictions.private_bytes,
            stopped: false,
            unpublished: None,
        })
    }

    fn write_new(&mut self, name: &str, value: &Value) -> Result<(), String> {
        let raw =
            serde_json::to_vec(value).map_err(|_| "Native Graph private record encoding failed")?;
        if raw.len() > MAX_PRIVATE_RECORD_BYTES
            || self.reserved_files >= self.maximum_files
            || self
                .reserved_bytes
                .checked_add(raw.len())
                .is_none_or(|bytes| bytes > self.maximum_bytes)
        {
            return Err("Native Graph private record exceeds its bounded custody".into());
        }
        self.reserved_files += 1;
        self.reserved_bytes += raw.len(); // Failed/partial reservations keep their budget.
        if self
            .root
            .canonicalize()
            .map_err(|_| "Native Graph private root is unavailable")?
            != self.root
        {
            return Err("Native Graph private root changed".into());
        }
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let mut file = options
            .open(self.root.join(name))
            .map_err(|_| "Native Graph original record could not be reserved")?;
        file.write_all(&raw)
            .map_err(|_| "Native Graph original record could not be retained")?;
        file.sync_all()
            .map_err(|_| "Native Graph original record could not be synchronized")?;
        #[cfg(unix)]
        fs::File::open(&self.root)
            .and_then(|root| root.sync_all())
            .map_err(|_| "Native Graph original directory could not be synchronized")?;
        Ok(())
    }

    fn begin(&mut self, request: Value) -> Result<usize, String> {
        // Reserve the complete serialized request plus one maximum-size
        // result BEFORE dispatch. Do not charge two 16 MiB maxima: the
        // production parent provides only 32 MiB and a prior factory record
        // already spent positive bytes. All existing pending result maxima
        // remain held while another begin is considered.
        if self.stopped {
            return Err("Native Graph original custody has no capacity for another request".into());
        }
        let request_encoded_len = serde_json::to_vec(&request)
            .map_err(|_| "Native Graph private record encoding failed")?
            .len();
        let pending_file_slots = self.pending_results.len().checked_add(2);
        // Reserve the entire result size this parent can actually retain,
        // never a larger implicit 16 MiB allowance. Small owners remain
        // bounded by their original parent budget rather than being unable
        // to admit even the first callback. The same cap is enforced at
        // finish, before any result can be published or admitted as complete.
        let result_ceiling = MAX_PRIVATE_RECORD_BYTES.min(self.maximum_bytes / 2);
        let pending_result_bytes = self
            .pending_results
            .len()
            .checked_add(1)
            .and_then(|count| count.checked_mul(result_ceiling));
        let required_bytes = pending_result_bytes.and_then(|pending| {
            self.reserved_bytes
                .checked_add(request_encoded_len)?
                .checked_add(pending)
        });
        if request_encoded_len > MAX_PRIVATE_RECORD_BYTES
            || pending_file_slots
                .and_then(|slots| self.reserved_files.checked_add(slots))
                .is_none_or(|files| files > self.maximum_files)
            || required_bytes.is_none_or(|bytes| bytes > self.maximum_bytes)
        {
            return Err("Native Graph original custody has no capacity for another request".into());
        }
        let sequence = self.next;
        self.next = self
            .next
            .checked_add(1)
            .ok_or("Native Graph original sequence capacity exhausted")?;
        // An uncertain/partial request write consumes actual charges and
        // stops further admission; the name cannot be reused or refunded.
        if let Err(error) =
            self.write_new(&format!("{sequence:02}.request.original.json"), &request)
        {
            self.stopped = true;
            self.unpublished = Some(request);
            return Err(error);
        }
        // Hold the result maximum until its original has been durably stored.
        // A second begin cannot consume this reservation.
        self.pending_results.insert(sequence);
        Ok(sequence)
    }

    fn finish(&mut self, sequence: usize, result: Value) -> Result<(), String> {
        if !self.pending_results.contains(&sequence) {
            self.stopped = true;
            self.unpublished = Some(result);
            return Err("Native Graph original result has no prior reserved request".into());
        }
        // The virtual reservation and the actual permissible result must
        // have the SAME ceiling. Never admit a result larger than the hold
        // made before effect dispatch, even if total bytes happen to remain.
        let result_ceiling = MAX_PRIVATE_RECORD_BYTES.min(self.maximum_bytes / 2);
        let within_hold = serde_json::to_vec(&result)
            .ok()
            .is_some_and(|encoded| encoded.len() <= result_ceiling);
        if !within_hold
            || self
                .write_new(&format!("{sequence:02}.result.original.json"), &result)
                .is_err()
        {
            self.stopped = true;
            self.unpublished = Some(result);
            // The enclosing collector retains private stderr and partial original files.
            // The private original remains unchanged; public projection fails.
            eprintln!(
                "NATIVE_GRAPH_PRIVATE_CUSTODY_INCOMPLETE; original outcome preserved; no retry"
            );
            return Err("Native Graph original custody is unavailable".into());
        }
        // Release only the virtual maximum-size hold, not the actual bytes or
        // file count already charged by durable request/result originals.
        self.pending_results.remove(&sequence);
        Ok(())
    }
}

struct NativeGraphBackend {
    connection: ProductionConnection,
    runtime: tokio::runtime::Runtime,
    originals: PrivateOriginals,
    callbacks: CallbackBudget,
}

fn recovery_read_error(error: GraphRecoveryReadError) -> Value {
    // A denied or malformed read says nothing about the original mutation.
    // Never encode it as GraphMutationError or derive outcome_known from it.
    Value::String(error.to_string())
}

fn begin_recovery_read(
    originals: &mut PrivateOriginals,
    original_request: Value,
) -> Result<(usize, PreparedGraphRecovery), Value> {
    let request: GraphRecoveryRequest = serde_json::from_value(original_request.clone())
        .map_err(|_| recovery_read_error(GraphRecoveryReadError::InvalidRequest))?;
    let prepared = request.prepare().map_err(recovery_read_error)?;
    // Keep the exact Session Value and original key/source before a constructor
    // or Native read. The pure recovery codec prepares canonical identity.
    // No current Project, branch, revision or Graph existence is substituted.
    let sequence = originals
        .begin(json!({
            "schema":"motionwright.native.graph-review-private-request/1",
            "operation":"operation_recovery",
            "original_recovery_request":original_request,
            "original_application_request_id":prepared.request_id(),
            "original_source_snapshot":prepared.source_snapshot(),
            "prepared_recovery_identity":prepared.identity(),
            "automatic_retry":false,
        }))
        .map_err(|_| recovery_read_error(GraphRecoveryReadError::ReadFailed))?;
    Ok((sequence, prepared))
}

fn private_result_record<T>(
    sequence: usize,
    report: &NativeResult<NativeGraphRecipeResult<T>>,
    transport: &ProductionTransportCustody,
) -> Value {
    let result = match report {
        Ok(report) => json!({
            "recipe_request_args":report.request_args(),
            "recipe_sha256":report.recipe_sha256(),
            "request_sha256":report.request_sha256(),
            "original_outer_envelope":report.original_envelope(),
            "original_observation_envelope":report.observation_envelope(),
            "original_operation_envelope":report.operation_envelope(),
            "typed_original_error":report.outcome().err(),
        }),
        Err(error) => json!({"typed_original_error":error}),
    };
    json!({
        "schema":"motionwright.native.graph-review-private-result/1",
        "sequence":sequence,
        "result":result,
        "original_transport_snapshot":transport.private_snapshot(),
        "automatic_retry":false,
        "native_acceptance":false,
    })
}

impl NativeGraphBackend {
    fn open(
        _database: &Path,
        service: &StudioService,
        connection_file: &Path,
        restrictions: ClientRestrictions,
    ) -> Result<Self, String> {
        // Parsed once by main before opening this actual Service/SQLite. The
        // enclosing controller precharges declared per-client C_i in SharedPool.
        let connection = ProductionConnection::load(connection_file)
            .map_err(|_| "Native Graph connection is unavailable or invalid")?;
        let mut originals = PrivateOriginals::create(connection_file, restrictions)?;
        let sequence = originals.begin(json!({
            "schema":"motionwright.native.graph-review-private-request/1",
            "operation":"factory_bind_actual_service",
            "native_dispatch_attempted":false,
            "parent_resource_restrictions":restrictions.private_observation(),
            "automatic_retry":false,
        }))?;
        // Probe the exact factory Service before the shared fixture seed. The
        // client retains its actual Store only for this scoped check; neither
        // this witness nor a previous Ready is reused by later callbacks.
        match NativeGraphRecipeClient::new(connection.clone(), service) {
            Ok(client) => {
                drop(client);
                originals.finish(sequence, json!({
                    "schema":"motionwright.native.graph-review-private-result/1",
                    "sequence":sequence,
                    "result":{
                        "constructor_result":"returned_typed_client",
                        "native_dispatch_attempted":false,
                    },
                    "original_transport_snapshot":ProductionTransportCustody::default().private_snapshot(),
                    "automatic_retry":false,
                    "native_acceptance":false,
                }))?;
            }
            Err(error) => {
                // Preserve the original private reason even when the actual
                // connection identity API rejects before any Native dispatch.
                originals.finish(
                    sequence,
                    private_result_record::<()>(
                        sequence,
                        &Err(error),
                        &ProductionTransportCustody::default(),
                    ),
                )?;
                return Err(BINDING_UNAVAILABLE.into());
            }
        }
        if originals.stopped {
            return Err("Native Graph original custody is unavailable".into());
        }
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|_| "Native Graph runtime could not be created")?;
        Ok(Self {
            connection,
            runtime,
            originals,
            callbacks: CallbackBudget::new(restrictions.callbacks),
        })
    }

    fn reserve_callback(&mut self) -> Result<usize, String> {
        if self.originals.stopped {
            self.callbacks.stop();
        }
        self.callbacks.reserve()
    }

    fn retain<T>(
        &mut self,
        sequence: usize,
        report: &NativeResult<NativeGraphRecipeResult<T>>,
        transport: &ProductionTransportCustody,
    ) -> Result<(), String> {
        let typed_error = match report {
            Ok(report) => report.outcome().err(),
            Err(error) => Some(error),
        };
        self.callbacks.observe_return(
            transport.has_unconfirmed_or_failed_invocation(),
            typed_error,
        );
        let mut original = private_result_record(sequence, report, transport);
        original["callback_restriction"] = self.callbacks.private_observation();
        let retained = self.originals.finish(sequence, original);
        if retained.is_err() {
            self.callbacks.stop();
        }
        retained
    }
}

impl shared_session::GraphBackend for NativeGraphBackend {
    fn authoring_context(
        &mut self,
        service: &StudioService,
        _database: &Path,
        request: &SemanticGraphContextRequest,
    ) -> Result<GraphAuthoringContext, Value> {
        self.reserve_callback()
            .map_err(|_| graph_context_failure(GraphContextStage::Admission, None))?;
        // One fresh bounded capture per explicit callback, using the same
        // original owner connection. No old capture is evicted or reset.
        let transport = ProductionTransportCustody::default();
        let sequence = self
            .originals
            .begin(json!({
                "schema":"motionwright.native.graph-review-private-request/1",
                "operation":"authoring_context",
                "callback_restriction":self.callbacks.private_observation(),
                "original_context_request":request,
                "automatic_retry":false,
            }))
            .map_err(|_| graph_context_failure(GraphContextStage::Custody, None))?;
        let client = match NativeGraphRecipeClient::new(self.connection.clone(), service) {
            Ok(client) => client.with_transport_custody(&transport),
            Err(error) => {
                // The actual typed binding failure is privately retained first.
                let public = graph_context_failure(GraphContextStage::Binding, Some(&error));
                self.retain::<GraphAuthoringContext>(sequence, &Err(error), &transport)
                    .map_err(|_| graph_context_failure(GraphContextStage::Custody, None))?;
                return Err(public);
            }
        };
        let report = self.runtime.block_on(client.authoring_context(request));
        // Keep original private outcome/error envelope and transport custody
        // ahead of any public classification; failure here overrides it.
        self.retain(sequence, &report, &transport)
            .map_err(|_| graph_context_failure(GraphContextStage::Custody, None))?;
        let transport_failed = transport.has_unconfirmed_or_failed_invocation();
        match report {
            Ok(report) => {
                let original_native = report.original_native_error().is_some();
                let context = report.outcome().cloned().map_err(|error| {
                    graph_context_report_failure(error, original_native, transport_failed)
                })?;
                graph_context_require_confirmed_transport(transport_failed)?;
                Ok(context)
            }
            Err(error) => Err(graph_context_report_failure(
                &error,
                false,
                transport_failed,
            )),
        }
    }

    fn operation_recovery(
        &mut self,
        service: &StudioService,
        _database: &Path,
        request: Value,
    ) -> Result<Value, Value> {
        self.reserve_callback()
            .map_err(|_| recovery_read_error(GraphRecoveryReadError::ReadFailed))?;
        let (sequence, prepared) = begin_recovery_read(&mut self.originals, request)?;
        let transport = ProductionTransportCustody::default();
        // Reopen replaces the Session Service. This fresh client binds that
        // actual callback Store and the selected owner project/generation;
        // a previous Ready or a historical receipt grants no read authority.
        let client = match NativeGraphRecipeClient::new(self.connection.clone(), service) {
            Ok(client) => client.with_transport_custody(&transport),
            Err(error) => {
                self.retain::<RecoveryRecord>(sequence, &Err(error), &transport)
                    .map_err(|_| recovery_read_error(GraphRecoveryReadError::ReadFailed))?;
                return Err(recovery_read_error(GraphRecoveryReadError::ReadFailed));
            }
        };
        let report = self
            .runtime
            .block_on(client.operation_recovery(prepared.identity()));
        // Snapshot the original read transport before its custody guard is
        // retained. A syntactically valid recovered receipt is not evidence
        // if that transport failed or never confirmed its return.
        let transport_failed = transport.has_unconfirmed_or_failed_invocation();
        // Preserve every actual envelope, typed failure and binary transport
        // observation before validating the historical read DTO.
        self.retain(sequence, &report, &transport)
            .map_err(|_| recovery_read_error(GraphRecoveryReadError::ReadFailed))?;
        semantic_graph_recovery::require_confirmed_recovery_transport(transport_failed)
            .map_err(recovery_read_error)?;
        let exchange =
            report.map_err(|_| recovery_read_error(GraphRecoveryReadError::ReadFailed))?;
        let response = prepared
            .decode_read(exchange.outcome())
            .map_err(recovery_read_error)?;
        serde_json::to_value(response)
            .map_err(|_| recovery_read_error(GraphRecoveryReadError::InvalidRecord))
    }

    fn apply_change(
        &mut self,
        service: &StudioService,
        _database: &Path,
        project: &Project,
        request_id: &str,
        change: &Change,
    ) -> Result<Project, Value> {
        self.reserve_callback()
            .map_err(|_| mutation_failure(GraphInvokeFailure::before_dispatch(request_id)))?;
        GraphInvokeFailure::validate_request_id(request_id)
            .map_err(|error| Value::String(error.to_string()))?;
        let prepared = prepare_mutation(project, request_id, change)
            .map_err(|_| mutation_failure(GraphInvokeFailure::before_dispatch(request_id)))?;
        let transport = ProductionTransportCustody::default();
        let sequence = self
            .originals
            .begin(json!({
                "schema":"motionwright.native.graph-review-private-request/1",
                "operation":"apply_change",
                "callback_restriction":self.callbacks.private_observation(),
                "original_application_request_id":request_id,
                "original_change":prepared.original_change,
                "prepared_recovery_identity":prepared.recovery,
                "automatic_retry":false,
            }))
            .map_err(|_| mutation_failure(GraphInvokeFailure::before_dispatch(request_id)))?;
        let client = match NativeGraphRecipeClient::new(self.connection.clone(), service) {
            Ok(client) => client.with_transport_custody(&transport),
            Err(error) => {
                self.retain::<NativeGraphSaveReceipt>(sequence, &Err(error), &transport)
                    .map_err(|error| mutation_custody_failure(request_id, error))?;
                return Err(mutation_failure(GraphInvokeFailure::before_dispatch(
                    request_id,
                )));
            }
        };
        let report: NativeResult<NativeGraphRecipeResult<NativeGraphSaveReceipt>> = match change {
            Change::UpsertSemanticGraph { graph } => {
                self.runtime.block_on(client.upsert(request_id, graph))
            }
            Change::RemoveSemanticGraph {
                graph_id,
                scene_id,
                source_snapshot,
            } => self.runtime.block_on(client.remove(
                request_id,
                *graph_id,
                *scene_id,
                source_snapshot,
            )),
            _ => unreachable!("Canonical Graph change was prepared before dispatch"),
        };
        // Never ACK a returned mutation receipt if the original transport
        // remains unconfirmed. Source custody/UNKNOWN outranks a local return.
        let transport_failed = transport.has_unconfirmed_or_failed_invocation();
        // The existing mutation DTO conservatively reports UNKNOWN for an
        // unclassified outer/local error. Even if that local Error has its
        // default knownness, this client receives no further callback credit.
        // A genuine known Native rejection keeps its original typed status.
        let unclassified_mutation_failure = match &report {
            Err(_) => true,
            Ok(report) => report.outcome().is_err() && report.original_native_error().is_none(),
        };
        if unclassified_mutation_failure {
            self.callbacks.stop();
        }
        self.retain(sequence, &report, &transport)
            .map_err(|error| mutation_custody_failure(request_id, error))?;
        match report {
            Ok(report) => match report.outcome() {
                // This is the original native receipt, including exact replay.
                // Do not reload local SQLite or recalculate a successful Project.
                // A returned receipt cannot confirm a mutation when the
                // original transport has an unconfirmed or failed invocation.
                Ok(_) if transport_failed => {
                    Err(mutation_failure(GraphInvokeFailure::unknown(request_id)))
                }
                Ok(receipt) => Ok(receipt.project().clone()),
                Err(_) => Err(mutation_failure(
                    GraphInvokeFailure::from_original_native_or_unknown(
                        request_id,
                        report.original_native_error(),
                    ),
                )),
            },
            Err(_) => Err(mutation_failure(GraphInvokeFailure::unknown(request_id))),
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    shared_session::enforce_graph_store_file_limit(true)?;
    // Closed child restrictions must be valid BEFORE the shared runner opens
    // Service/SQLite, creates its parent or reaches the Native factory. Zero
    // keeps the existing Service available with no Graph callback admission.
    let restrictions = ClientRestrictions::from_parent_environment()?;
    // Per-child startup configuration only. Browser invoke payloads cannot
    // select a connection, SDK executable, owner session or custody root.
    let connection_file = std::env::var_os(CONNECTION_ENV)
        .map(PathBuf::from)
        .ok_or("MOTIONWRIGHT_GRAPH_CONNECTION is required for the Native Graph Session")?;
    shared_session::run_with_graph_backend(move |database, service| {
        let backend = NativeGraphBackend::open(database, service, &connection_file, restrictions)?;
        Ok(Some(Box::new(backend)))
    })
}

#[cfg(test)]
#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod graph_source_fixture;

#[cfg(test)]
mod native_graph_adapter_tests {
    use super::*;
    use motionwright_domain::GraphSourceSnapshot;
    use semwright_native_sdk::ErrorCode;
    use uuid::Uuid;

    const ORIGINAL_KEY: &str = "ABCDEF12-3456-4789-aBcD-EF1234567890";

    #[test]
    fn graph_context_public_stage_and_pinned_error_code_rosters_are_closed() {
        assert_eq!(
            [
                GraphContextStage::Admission,
                GraphContextStage::Binding,
                GraphContextStage::Custody,
                GraphContextStage::Local,
                GraphContextStage::Transport,
                GraphContextStage::Native,
            ]
            .map(GraphContextStage::label),
            [
                "ADMISSION",
                "BINDING",
                "CUSTODY",
                "LOCAL",
                "TRANSPORT",
                "NATIVE"
            ]
        );
        let exact = [
            (ErrorCode::Unsupported, "Unsupported"),
            (ErrorCode::Unavailable, "Unavailable"),
            (ErrorCode::PermissionDenied, "PermissionDenied"),
            (ErrorCode::ConsentRequired, "ConsentRequired"),
            (ErrorCode::PolicyDenied, "PolicyDenied"),
            (ErrorCode::NotFound, "NotFound"),
            (ErrorCode::AmbiguousTarget, "AmbiguousTarget"),
            (ErrorCode::StaleReference, "StaleReference"),
            (ErrorCode::BackendFailed, "BackendFailed"),
            (ErrorCode::Timeout, "Timeout"),
            (ErrorCode::InvalidArgument, "InvalidArgument"),
            (ErrorCode::PluginProtocolError, "PluginProtocolError"),
            (ErrorCode::SandboxDenied, "SandboxDenied"),
            (ErrorCode::Conflict, "Conflict"),
            (ErrorCode::Cancelled, "Cancelled"),
            (ErrorCode::Internal, "Internal"),
            (ErrorCode::ProtocolMismatch, "ProtocolMismatch"),
            (ErrorCode::ResourceExhausted, "ResourceExhausted"),
        ];
        for (code, name) in exact {
            assert_eq!(graph_context_code(code), name);
        }
        assert_eq!(
            graph_context_failure(GraphContextStage::Admission, None),
            json!({"schema":"motionwright.native.graph-context-failure/1",
                   "stage":"ADMISSION","error_code":null})
        );
        assert_eq!(
            graph_context_failure(GraphContextStage::Custody, None),
            json!({"schema":"motionwright.native.graph-context-failure/1",
                   "stage":"CUSTODY","error_code":null})
        );
    }

    #[test]
    fn graph_context_success_requires_a_confirmed_original_transport() {
        assert!(graph_context_require_confirmed_transport(false).is_ok());
        assert_eq!(
            graph_context_require_confirmed_transport(true),
            Err(json!({
                "schema":"motionwright.native.graph-context-failure/1",
                "stage":"TRANSPORT",
                "error_code":null,
            }))
        );
    }

    #[test]
    fn graph_context_public_error_projection_redacts_every_private_message() {
        let mut original = Error::new(
            ErrorCode::PolicyDenied,
            "PRIVATE_MESSAGE_NEVER_PUBLIC: /synthetic/private/connection",
        )
        .recipe_progress(3, "PRIVATE_RECIPE_PROGRESS_NEVER_PUBLIC")
        .uncertain();
        original
            .candidates
            .push("PRIVATE_CANDIDATE_NEVER_PUBLIC".into());
        let private_before = serde_json::to_value(&original).unwrap();
        let cases = [
            (true, false, "NATIVE"),
            (false, false, "LOCAL"),
            (false, true, "TRANSPORT"),
            // An unconfirmed transport takes precedence over native origin.
            (true, true, "TRANSPORT"),
        ];
        for (native_original, transport_failed, stage) in cases {
            let public = graph_context_report_failure(&original, native_original, transport_failed);
            assert_eq!(
                public,
                json!({"schema":"motionwright.native.graph-context-failure/1",
                       "stage":stage,"error_code":"PolicyDenied"})
            );
            assert_eq!(public.as_object().unwrap().len(), 3);
            let serialized = serde_json::to_string(&public).unwrap();
            for private in [
                "PRIVATE_MESSAGE_NEVER_PUBLIC",
                "PRIVATE_RECIPE_PROGRESS_NEVER_PUBLIC",
                "PRIVATE_CANDIDATE_NEVER_PUBLIC",
                "/synthetic/private/connection",
                "message",
                "candidates",
                "envelope",
                "outcome_known",
            ] {
                assert!(!serialized.contains(private), "leaked {private}");
            }
        }
        assert_eq!(serde_json::to_value(&original).unwrap(), private_before);
    }

    // Private file controls do not dispatch a callback or select the real C_i.
    fn fixture_restrictions() -> ClientRestrictions {
        ClientRestrictions {
            callbacks: 0,
            private_bytes: MAX_PRIVATE_TOTAL_BYTES,
            private_files: MAX_PRIVATE_FILES,
        }
    }

    #[test]
    fn callback_parent_restrictions_are_mandatory_canonical_and_cannot_expand_the_shared_pool() {
        let bytes = (64 * 1024 * 1024).to_string();
        let parsed = ClientRestrictions::parse(Some("2"), Some(&bytes), Some("512")).unwrap();
        assert_eq!(parsed.callbacks, 2); // Synthetic component value, not a production C_i.
        assert_eq!(parsed.private_bytes, 64 * 1024 * 1024);
        assert_eq!(parsed.private_files, 512);
        for callback in [
            None,
            Some(""),
            Some("00"),
            Some("01"),
            Some("+1"),
            Some("02"),
            Some(" 2"),
            Some("2 "),
            Some("\t1"),
            Some("1\n"),
            Some("-1"),
            Some("\u{0661}"),
            Some("\u{ff10}"),
            Some("999999999999999999999999999999999999"),
        ] {
            assert!(ClientRestrictions::parse(callback, Some(&bytes), Some("512")).is_err());
        }
        let overflow = (usize::MAX as u128 + 1).to_string();
        assert!(ClientRestrictions::parse(Some(&overflow), Some(&bytes), Some("512")).is_err());
        for (bytes, files) in [
            (None, Some("512")),
            (Some("1"), None),
            (Some("0"), Some("512")),
            (Some("536870913"), Some("512")),
            (Some("1"), Some("4097")),
            (Some("1"), Some("0")),
        ] {
            assert!(ClientRestrictions::parse(Some("2"), bytes, files).is_err());
        }
    }

    #[test]
    fn zero_parent_cap_keeps_service_reads_available_and_denies_each_first_real_callback_before_journal()
     {
        let restrictions =
            ClientRestrictions::parse(Some("0"), Some("67108864"), Some("512")).unwrap();
        assert_eq!(restrictions.callbacks, 0);
        for entrypoint in 0..3 {
            let directory = tempfile::tempdir().unwrap();
            let database = directory.path().join("read-only-session.sqlite3");
            let service = StudioService::open(&database).unwrap();
            assert!(service.projects(1).unwrap().is_empty());
            let connection = ProductionConnection {
                schema: "motionwright-semwright-connection/1".into(),
                executable: directory.path().join("never-dispatched-client"),
                executable_sha256: "ab".repeat(32),
                socket: directory.path().join("never-opened.sock"),
                session_file: directory.path().join("never-created.session"),
                output_root: directory.path().join("never-used-output"),
                resource: "project:synthetic-zero-cap-control".into(),
            };
            // An inert exact-type connection lets this test exercise the real
            // trait entrypoints without a Native client, Host or SDK process.
            let mut backend = NativeGraphBackend {
                connection,
                runtime: tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .unwrap(),
                originals: PrivateOriginals::create(
                    &directory.path().join("client.json"),
                    restrictions,
                )
                .unwrap(),
                callbacks: CallbackBudget::new(restrictions.callbacks),
            };
            let project = graph_source_fixture::seed_domain();
            let request = SemanticGraphContextRequest {
                source_snapshot: GraphSourceSnapshot::from_project(&project),
                scene_id: project.scenes[0].id,
                variant_profile_ref: project.deliverables[0].id,
            };
            let change = Change::UpsertSemanticGraph {
                graph: Box::new(graph_source_fixture::graph_for(&project, false)),
            };
            let result = match entrypoint {
                0 => shared_session::GraphBackend::authoring_context(
                    &mut backend,
                    &service,
                    &database,
                    &request,
                )
                .map(|_| ()),
                1 => shared_session::GraphBackend::apply_change(
                    &mut backend,
                    &service,
                    &database,
                    &project,
                    ORIGINAL_KEY,
                    &change,
                )
                .map(|_| ()),
                _ => shared_session::GraphBackend::operation_recovery(
                    &mut backend,
                    &service,
                    &database,
                    Value::Null,
                )
                .map(|_| ()),
            };
            assert!(result.is_err());
            assert!(backend.callbacks.stopped);
            assert_eq!(backend.callbacks.consumed, 0);
            assert_eq!(backend.originals.next, 0);
            assert_eq!(backend.originals.reserved_files, 0);
            assert_eq!(backend.originals.reserved_bytes, 0);
            assert_eq!(fs::read_dir(&backend.originals.root).unwrap().count(), 0);
            assert!(!backend.connection.session_file.exists());
            assert!(!backend.connection.socket.exists());
            assert!(service.projects(1).unwrap().is_empty());
        }
    }

    #[test]
    fn callback_admissions_are_cumulative_and_exhaustion_never_refunds_an_attempt() {
        let mut budget = CallbackBudget::new(2);
        assert_eq!(budget.reserve().unwrap(), 1);
        // A failed validation or UNKNOWN return has no release/refund path.
        assert_eq!(budget.reserve().unwrap(), 2);
        assert!(budget.reserve().is_err());
        assert!(budget.reserve().is_err());
        assert_eq!(budget.consumed, 2);
        assert!(budget.stopped);
        let mut zero = CallbackBudget::new(0);
        assert!(zero.reserve().is_err());
        assert_eq!(zero.consumed, 0);
    }

    #[test]
    fn callback_failure_latch_survives_backend_state_movement_without_new_credit() {
        let mut original = CallbackBudget::new(3);
        original.reserve().unwrap();
        original.stop();
        // The actual shared Session transfers the same backend on reopen.
        let mut retained = original;
        assert!(retained.reserve().is_err());
        assert_eq!(retained.consumed, 1);
        assert_eq!(retained.maximum, 3);
        assert!(retained.stopped);
    }

    #[test]
    fn callback_counter_maximum_cannot_wrap_into_new_credit() {
        let mut budget = CallbackBudget {
            maximum: usize::MAX,
            consumed: usize::MAX - 1,
            stopped: false,
        };
        assert_eq!(budget.reserve().unwrap(), usize::MAX);
        assert!(budget.reserve().is_err());
        assert_eq!(budget.consumed, usize::MAX);
    }

    #[test]
    fn callback_typed_unknown_stops_even_after_returned_transport_success_without_changing_error() {
        for code in [
            ErrorCode::ProtocolMismatch,
            ErrorCode::Timeout,
            ErrorCode::PolicyDenied,
        ] {
            let error = Error::new(code, "synthetic retained original or after-dispatch error")
                .recipe_progress(2, "original-step")
                .uncertain();
            let original = serde_json::to_value(&error).unwrap();
            let mut budget = CallbackBudget::new(2);
            budget.reserve().unwrap();
            budget.observe_return(false, Some(&error));
            assert!(budget.reserve().is_err());
            assert_eq!(budget.consumed, 1);
            assert_eq!(serde_json::to_value(&error).unwrap(), original);
        }
    }

    #[test]
    fn callback_known_rejection_does_not_refund_or_grant_an_extra_callback() {
        let error = Error::new(
            ErrorCode::PolicyDenied,
            "synthetic known original rejection",
        );
        assert!(error.outcome_known);
        let original = serde_json::to_value(&error).unwrap();
        let mut budget = CallbackBudget::new(2);
        budget.reserve().unwrap();
        budget.observe_return(false, Some(&error));
        assert!(!budget.stopped);
        assert_eq!(budget.reserve().unwrap(), 2);
        budget.observe_return(false, None);
        assert!(budget.reserve().is_err());
        assert_eq!(budget.consumed, 2);
        assert_eq!(serde_json::to_value(&error).unwrap(), original);
        let mut transport = CallbackBudget::new(2);
        transport.reserve().unwrap();
        transport.observe_return(true, Some(&error));
        assert!(transport.reserve().is_err());
        assert_eq!(transport.consumed, 1);
    }

    #[test]
    fn parent_journal_file_slice_rejects_before_publishing_an_unreserved_request() {
        let directory = tempfile::tempdir().unwrap();
        let restrictions = ClientRestrictions {
            callbacks: 1,
            private_bytes: MAX_PRIVATE_TOTAL_BYTES,
            private_files: 1,
        };
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json"), restrictions).unwrap();
        assert!(
            originals
                .begin(json!({"synthetic_original_request":true}))
                .is_err()
        );
        assert_eq!(originals.reserved_files, 0);
        assert_eq!(fs::read_dir(&originals.root).unwrap().count(), 0);
    }

    #[test]
    fn smaller_parent_retains_two_sequential_originals_without_expanding_eight_mib() {
        let directory = tempfile::tempdir().unwrap();
        let restrictions = ClientRestrictions {
            callbacks: 2,
            private_bytes: 8 * 1024 * 1024,
            private_files: 128,
        };
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json"), restrictions).unwrap();
        for sequence in 0..2 {
            let request = json!({"operation":"synthetic-sequential","index":sequence});
            let result = json!({"result":"inert-small","index":sequence});
            let admitted = originals.begin(request.clone()).unwrap();
            assert_eq!(admitted, sequence);
            assert!(originals.pending_results.contains(&admitted));
            assert!(
                originals
                    .begin(json!({"operation":"must-not-dispatch-concurrently"}))
                    .is_err()
            );
            originals.finish(admitted, result.clone()).unwrap();
            assert_eq!(
                fs::read(
                    originals
                        .root
                        .join(format!("{sequence:02}.result.original.json"))
                )
                .unwrap(),
                serde_json::to_vec(&result).unwrap()
            );
        }
        assert_eq!(originals.reserved_files, 4);
        assert!(originals.reserved_bytes < restrictions.private_bytes);
        assert!(originals.pending_results.is_empty());
        assert!(!originals.stopped);
    }

    #[test]
    fn smaller_parent_refuses_unreserved_oversized_result_without_publishing() {
        let directory = tempfile::tempdir().unwrap();
        let restrictions = ClientRestrictions {
            callbacks: 1,
            private_bytes: 8 * 1024 * 1024,
            private_files: 128,
        };
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json"), restrictions).unwrap();
        let admitted = originals
            .begin(json!({"operation":"one-original-only"}))
            .unwrap();
        let oversized = json!({"payload":"x".repeat(4 * 1024 * 1024)});
        assert!(originals.finish(admitted, oversized.clone()).is_err());
        assert!(originals.stopped);
        assert_eq!(originals.unpublished, Some(oversized));
        assert!(originals.root.join("00.request.original.json").exists());
        assert!(!originals.root.join("00.result.original.json").exists());
        assert!(originals.begin(json!({"operation":"no-retry"})).is_err());
        assert_eq!(originals.reserved_files, 1);
        assert!(originals.reserved_bytes < restrictions.private_bytes);
    }

    #[test]
    fn parent_journal_32mib_factory_then_context_reserves_result_before_dispatch() {
        let directory = tempfile::tempdir().unwrap();
        let restrictions = ClientRestrictions {
            callbacks: 2,
            private_bytes: 32 * 1024 * 1024,
            private_files: 384,
        };
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json"), restrictions).unwrap();
        let factory_request = json!({
            "schema":"motionwright.native.graph-review-private-request/1",
            "operation":"factory_bind_actual_service",
            "native_dispatch_attempted":false,
            "automatic_retry":false,
        });
        let factory_result = json!({
            "schema":"motionwright.native.graph-review-private-result/1",
            "result":{"constructor_result":"returned_typed_client",
                      "native_dispatch_attempted":false},
            "automatic_retry":false,
        });
        let first = originals.begin(factory_request.clone()).unwrap();
        assert_eq!(first, 0);
        assert!(originals.pending_results.contains(&first));
        originals.finish(first, factory_result.clone()).unwrap();
        assert!(originals.pending_results.is_empty());
        let factory_bytes = serde_json::to_vec(&factory_request).unwrap().len()
            + serde_json::to_vec(&factory_result).unwrap().len();
        assert_eq!(originals.reserved_bytes, factory_bytes);
        assert_eq!(originals.reserved_files, 2);

        let context_request = json!({
            "schema":"motionwright.native.graph-review-private-request/1",
            "operation":"authoring_context",
            "callback_restriction":{"maximum_callbacks":2,"refunds":0},
            "original_context_request":{"source_snapshot":"synthetic-original"},
            "automatic_retry":false,
        });
        let context_len = serde_json::to_vec(&context_request).unwrap().len();
        let second = originals.begin(context_request.clone()).unwrap();
        assert_eq!(second, 1);
        // The full request was written BEFORE a client can dispatch Native.
        assert_eq!(
            fs::read(originals.root.join("01.request.original.json")).unwrap(),
            serde_json::to_vec(&context_request).unwrap()
        );
        assert_eq!(originals.reserved_bytes, factory_bytes + context_len);
        assert_eq!(originals.reserved_files, 3);
        assert!(originals.pending_results.contains(&second));
        let context_result = json!({
            "schema":"motionwright.native.graph-review-private-result/1",
            "result":{"outcome":"synthetic-source-only"},
            "automatic_retry":false,
        });
        originals.finish(second, context_result.clone()).unwrap();
        assert!(originals.pending_results.is_empty());
        assert_eq!(originals.reserved_files, 4);
        assert_eq!(
            originals.reserved_bytes,
            factory_bytes + context_len + serde_json::to_vec(&context_result).unwrap().len()
        );
        assert!(originals.reserved_bytes <= restrictions.private_bytes);
        assert!(!originals.stopped);
    }

    #[test]
    fn parent_journal_32mib_denies_without_result_capacity_or_refund() {
        let directory = tempfile::tempdir().unwrap();
        let restrictions = ClientRestrictions {
            callbacks: 2,
            private_bytes: 32 * 1024 * 1024,
            private_files: 384,
        };
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json"), restrictions).unwrap();
        let request = json!({"operation":"factory_bind_actual_service","synthetic":true});
        let first = originals.begin(request.clone()).unwrap();
        let original_charge = serde_json::to_vec(&request).unwrap().len();
        assert_eq!(originals.reserved_bytes, original_charge);
        assert_eq!(originals.reserved_files, 1);
        assert!(originals.pending_results.contains(&first));
        // The pending maximum result already occupies 16 MiB. A second begin
        // would require another 16 MiB plus its positive encoded request.
        assert!(
            originals
                .begin(json!({"operation":"authoring_context"}))
                .is_err()
        );
        assert_eq!(originals.next, 1);
        assert_eq!(originals.reserved_bytes, original_charge);
        assert_eq!(originals.reserved_files, 1);
        assert!(!originals.root.join("01.request.original.json").exists());
        assert!(originals.pending_results.contains(&first));
        let result = json!({"private_original":"completed-inert-factory"});
        originals.finish(first, result.clone()).unwrap();
        let completed_charge = original_charge + serde_json::to_vec(&result).unwrap().len();
        assert_eq!(originals.reserved_bytes, completed_charge);
        assert_eq!(originals.reserved_files, 2);
        assert!(originals.pending_results.is_empty());

        // Once the result is durably retained, the virtual 16 MiB hold can
        // be released; the completed request/result bytes remain spent.
        let second = originals
            .begin(json!({"operation":"authoring_context"}))
            .unwrap();
        assert_eq!(second, 1);
        assert!(originals.pending_results.contains(&second));
        assert!(originals.reserved_bytes > completed_charge);
        originals
            .finish(second, json!({"synthetic":"result"}))
            .unwrap();
        assert!(originals.pending_results.is_empty());
        assert_eq!(originals.reserved_files, 4);

        let smaller = tempfile::tempdir().unwrap();
        let denied_request = json!({"operation":"authoring_context"});
        let request_bytes = serde_json::to_vec(&denied_request).unwrap().len();
        // The request itself fits, but its source-bounded result reservation
        // does not. Denial must occur before any request bytes are published.
        let insufficient_parent_bytes = request_bytes + 1;
        let result_reservation = MAX_PRIVATE_RECORD_BYTES.min(insufficient_parent_bytes / 2);
        assert!(request_bytes < insufficient_parent_bytes);
        assert!(request_bytes + result_reservation > insufficient_parent_bytes);
        let mut denied = PrivateOriginals::create(
            &smaller.path().join("client.json"),
            ClientRestrictions {
                callbacks: 1,
                private_bytes: insufficient_parent_bytes,
                private_files: 384,
            },
        )
        .unwrap();
        assert!(denied.begin(denied_request).is_err());
        assert_eq!(denied.next, 0);
        assert_eq!(denied.reserved_bytes, 0);
        assert_eq!(denied.reserved_files, 0);
        assert!(denied.pending_results.is_empty());
        assert_eq!(fs::read_dir(&denied.root).unwrap().count(), 0);
    }

    #[test]
    fn parent_journal_byte_slice_preserves_charges_and_existing_originals() {
        let directory = tempfile::tempdir().unwrap();
        let restrictions = ClientRestrictions {
            callbacks: 0,
            private_bytes: 4,
            private_files: 2,
        };
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json"), restrictions).unwrap();
        originals
            .write_new("first.original.json", &json!(0))
            .unwrap();
        assert!(
            originals
                .write_new("oversize.original.json", &json!("too large"))
                .is_err()
        );
        assert_eq!(originals.reserved_files, 1);
        assert_eq!(originals.reserved_bytes, 1);
        assert_eq!(
            fs::read(originals.root.join("first.original.json")).unwrap(),
            b"0"
        );
        assert!(!originals.root.join("oversize.original.json").exists());
    }

    #[test]
    fn shared_mutation_error_projection_keeps_original_knownness_and_exact_key() {
        for known in [true, false] {
            for code in [
                ErrorCode::Timeout,
                ErrorCode::PolicyDenied,
                ErrorCode::ProtocolMismatch,
            ] {
                let mut error = Error::new(code, "private original Native diagnostic")
                    .recipe_progress(2, "private-original-step");
                error.outcome_known = known;
                let original = serde_json::to_value(&error).unwrap();
                let public =
                    mutation_failure(GraphInvokeFailure::from_native(ORIGINAL_KEY, &error));
                assert_eq!(public.as_object().unwrap().len(), 4);
                assert_eq!(
                    public,
                    json!({
                        "schema":"motionwright.graph-mutation-error/1",
                        "request_id":ORIGINAL_KEY,
                        "outcome_known":known,
                        "code":"graph_mutation_failed",
                    })
                );
                assert_eq!(serde_json::to_value(&error).unwrap(), original);
                assert!(!public.to_string().contains(&error.message));
            }
        }
    }

    #[test]
    fn invalid_mutation_ids_remain_fixed_strings_without_reflecting_input() {
        for key in [
            "original-key",
            "00000000-0000-0000-0000-000000000000",
            "ABCDEF1234564789aBcDEF1234567890",
            "/private/operator/invalid-key",
        ] {
            assert!(GraphInvokeFailure::validate_request_id(key).is_err());
            for failure in [
                GraphInvokeFailure::before_dispatch(key),
                GraphInvokeFailure::unknown(key),
            ] {
                let public = mutation_failure(failure);
                assert_eq!(
                    public,
                    Value::String("Graph mutation request identity is invalid".into())
                );
                assert!(public.get("outcome_known").is_none());
                assert!(!public.as_str().unwrap().contains(key));
            }
        }
    }

    #[test]
    fn local_post_dispatch_and_custody_failures_are_unknown_without_error_inference() {
        let local_decode = Error::new(ErrorCode::ProtocolMismatch, "private local decode failure");
        assert!(local_decode.outcome_known); // Local Error defaults cannot grant knownness.
        let public = mutation_failure(GraphInvokeFailure::unknown(ORIGINAL_KEY));
        assert_eq!(public["outcome_known"], false);
        assert_eq!(public["request_id"], ORIGINAL_KEY);
        assert_eq!(
            mutation_custody_failure(ORIGINAL_KEY, "private disk failure".into()),
            public
        );
        assert!(!public.to_string().contains(&local_decode.message));
    }

    #[test]
    fn a_different_project_generation_is_rejected_before_any_native_client_is_constructed() {
        let project = graph_source_fixture::seed_domain();
        let mut graph = graph_source_fixture::graph_for(&project, false);
        graph.authorship.source_snapshot.generation = Uuid::new_v4();
        let snapshot = graph.authorship.source_snapshot.clone();
        let upsert = Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        };
        let remove = Change::RemoveSemanticGraph {
            graph_id: Uuid::new_v4(),
            scene_id: project.scenes[0].id,
            source_snapshot: snapshot,
        };
        assert!(prepare_mutation(&project, "original-key", &upsert).is_err());
        assert!(prepare_mutation(&project, "original-remove-key", &remove).is_err());
    }

    #[test]
    fn mutation_preparation_retains_exact_graph_key_change_and_storage_digest() {
        let project = graph_source_fixture::seed_domain();
        let graph = graph_source_fixture::graph_for(&project, false);
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        };
        let prepared = prepare_mutation(&project, "original-key", &change).unwrap();
        assert_eq!(
            serde_json::to_value(prepared.original_change).unwrap(),
            serde_json::to_value(&change).unwrap()
        );
        assert_eq!(
            prepared.recovery.request_sha256,
            motionwright_storage::apply_request_digest_v1(project.id, &change).unwrap()
        );
        assert_eq!(
            prepared.recovery,
            NativeGraphRecipeClient::prepare_upsert(
                "original-key",
                match &change {
                    Change::UpsertSemanticGraph { graph } => graph,
                    _ => unreachable!(),
                }
            )
            .unwrap()
        );
    }

    #[test]
    fn removal_preparation_does_not_require_an_existing_graph_or_rebase_source_revision() {
        let mut project = graph_source_fixture::seed_domain();
        let source_snapshot = GraphSourceSnapshot::from_project(&project);
        let change = Change::RemoveSemanticGraph {
            graph_id: Uuid::new_v4(),
            scene_id: project.scenes[0].id,
            source_snapshot,
        };
        assert!(project.production_design.semantic_graphs.is_empty());
        project.revision += 1;
        let prepared = prepare_mutation(&project, "original-remove-key", &change).unwrap();
        assert_eq!(
            serde_json::to_value(prepared.original_change).unwrap(),
            serde_json::to_value(&change).unwrap()
        );
        assert_eq!(
            prepared.recovery.request_sha256,
            motionwright_storage::apply_request_digest_v1(project.id, &change).unwrap()
        );
        assert!(
            prepare_mutation(
                &project,
                "original-remove-key",
                &Change::RenameProject {
                    title: "outside Graph".into()
                }
            )
            .is_err()
        );
    }

    #[test]
    fn exclusive_private_originals_do_not_replace_a_previous_request_or_resume_after_failure() {
        let directory = tempfile::tempdir().unwrap();
        let connection = directory.path().join("client.json");
        let mut originals = PrivateOriginals::create(&connection, fixture_restrictions()).unwrap();
        assert!(PrivateOriginals::create(&connection, fixture_restrictions()).is_err());
        let request = json!({"original_application_request_id":"original-key","original_change":{"fixture":true}});
        let sequence = originals.begin(request.clone()).unwrap();
        let path = originals
            .root
            .join(format!("{sequence:02}.request.original.json"));
        let bytes = fs::read(&path).unwrap();
        assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(), request);
        let result_path = originals
            .root
            .join(format!("{sequence:02}.result.original.json"));
        fs::write(&result_path, b"retained previous original").unwrap();
        assert!(
            originals
                .finish(sequence, json!({"original_outcome":"unknown"}))
                .is_err()
        );
        assert_eq!(
            fs::read(&result_path).unwrap(),
            b"retained previous original"
        );
        assert_eq!(fs::read(path).unwrap(), bytes);
        assert!(originals.stopped);
        assert!(originals.unpublished.is_some());
        assert!(
            originals
                .begin(json!({"fresh_key":"must_not_run"}))
                .is_err()
        );
    }

    #[test]
    fn pre_dispatch_binding_error_keeps_original_private_reason_without_inventing_an_envelope() {
        let directory = tempfile::tempdir().unwrap();
        let mut originals = PrivateOriginals::create(
            &directory.path().join("client.json"),
            fixture_restrictions(),
        )
        .unwrap();
        let sequence = originals
            .begin(json!({"operation":"factory_bind_actual_service"}))
            .unwrap();
        let error = Error::new(
            ErrorCode::Unsupported,
            "exact original retained-connection diagnostic",
        );
        let transport = ProductionTransportCustody::default();
        originals
            .finish(
                sequence,
                private_result_record::<()>(sequence, &Err(error.clone()), &transport),
            )
            .unwrap();
        let raw = fs::read(
            originals
                .root
                .join(format!("{sequence:02}.result.original.json")),
        )
        .unwrap();
        let retained: Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(
            retained["result"]["typed_original_error"],
            serde_json::to_value(&error).unwrap()
        );
        assert!(retained["result"].get("original_outer_envelope").is_none());
        assert_eq!(
            retained["original_transport_snapshot"]["entries"],
            json!([])
        );
        let public = mutation_failure(GraphInvokeFailure::before_dispatch(ORIGINAL_KEY));
        assert_eq!(public.as_object().unwrap().len(), 4);
        assert_eq!(public["request_id"], ORIGINAL_KEY);
        assert!(
            !serde_json::to_string(&public)
                .unwrap()
                .contains(&error.message)
        );
    }

    fn recovery_request_fixture() -> (Value, Change) {
        // Pure Domain source, never an owner/session/Host or Native success.
        let project = graph_source_fixture::seed_domain();
        let change = Change::UpsertSemanticGraph {
            graph: Box::new(graph_source_fixture::graph_for(&project, false)),
        };
        let request = json!({
            "request_id":"5C77EA19-C0B1-47F4-B03B-495D72406A1F",
            "change":change,
        });
        (request, change)
    }

    #[test]
    fn recovery_intent_keeps_original_session_value_uuid_spelling_and_canonical_identity() {
        let directory = tempfile::tempdir().unwrap();
        let mut originals = PrivateOriginals::create(
            &directory.path().join("recovery.json"),
            fixture_restrictions(),
        )
        .unwrap();
        let (request, change) = recovery_request_fixture();
        let (sequence, prepared) = begin_recovery_read(&mut originals, request.clone()).unwrap();
        let raw = fs::read(originals.root.join("00.request.original.json")).unwrap();
        let retained: Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(sequence, 0);
        assert_eq!(retained["original_recovery_request"], request);
        assert_eq!(
            prepared.request_id(),
            "5C77EA19-C0B1-47F4-B03B-495D72406A1F"
        );
        assert_eq!(
            retained["original_application_request_id"],
            prepared.request_id()
        );
        assert_eq!(
            retained["original_source_snapshot"],
            serde_json::to_value(prepared.source_snapshot()).unwrap()
        );
        assert_eq!(
            retained["prepared_recovery_identity"],
            serde_json::to_value(prepared.identity()).unwrap()
        );
        assert_eq!(
            prepared.identity().request_sha256,
            motionwright_storage::apply_request_digest_v1(
                prepared.source_snapshot().project_id,
                &change,
            )
            .unwrap()
        );
        assert_eq!(retained["automatic_retry"], false);
        assert!(!originals.root.join("00.result.original.json").exists());
    }

    #[test]
    fn recovery_invalid_request_is_an_ordinary_read_error_before_custody_or_binding() {
        let directory = tempfile::tempdir().unwrap();
        let mut originals = PrivateOriginals::create(
            &directory.path().join("recovery.json"),
            fixture_restrictions(),
        )
        .unwrap();
        let (request, _) = recovery_request_fixture();
        let mut extra_authority = request.clone();
        extra_authority["expected_store"] = json!("caller must not choose a physical binding");
        let mut nil_key = request.clone();
        nil_key["request_id"] = json!(Uuid::nil().to_string());
        let mut unrelated_change = request;
        unrelated_change["change"] = serde_json::to_value(Change::RenameProject {
            title: "outside Graph recovery".into(),
        })
        .unwrap();
        for invalid in [json!([]), extra_authority, nil_key, unrelated_change] {
            let error = begin_recovery_read(&mut originals, invalid).err().unwrap();
            assert_eq!(
                error,
                recovery_read_error(GraphRecoveryReadError::InvalidRequest)
            );
            assert!(error.is_string());
            assert!(error.get("outcome_known").is_none());
        }
        assert_eq!(originals.next, 0);
        assert_eq!(originals.reserved_files, 0);
        assert_eq!(fs::read_dir(&originals.root).unwrap().count(), 0);
    }

    #[test]
    fn recovery_custody_refusal_preserves_prior_original_and_remains_a_read_error() {
        let directory = tempfile::tempdir().unwrap();
        let mut originals = PrivateOriginals::create(
            &directory.path().join("recovery.json"),
            fixture_restrictions(),
        )
        .unwrap();
        let prior = b"retained original, possibly incomplete";
        let path = originals.root.join("00.request.original.json");
        fs::write(&path, prior).unwrap();
        let (request, _) = recovery_request_fixture();
        let error = begin_recovery_read(&mut originals, request.clone())
            .err()
            .unwrap();
        assert_eq!(
            error,
            recovery_read_error(GraphRecoveryReadError::ReadFailed)
        );
        assert!(error.is_string());
        assert_eq!(fs::read(path).unwrap(), prior);
        assert!(originals.stopped);
        assert_eq!(
            originals.unpublished.as_ref().unwrap()["original_recovery_request"],
            request
        );
        assert!(!originals.root.join("00.result.original.json").exists());
        assert!(begin_recovery_read(&mut originals, request).is_err());
    }

    #[test]
    fn recovery_known_and_unknown_read_errors_are_retained_without_mutation_classification() {
        let directory = tempfile::tempdir().unwrap();
        let mut originals = PrivateOriginals::create(
            &directory.path().join("recovery.json"),
            fixture_restrictions(),
        )
        .unwrap();
        let (request, _) = recovery_request_fixture();
        for known in [true, false] {
            let (sequence, prepared) =
                begin_recovery_read(&mut originals, request.clone()).unwrap();
            // Inert failure fixture; no envelope or Native success is forged.
            let mut error = Error::new(ErrorCode::PolicyDenied, "original private read denial");
            error.outcome_known = known;
            let transport = ProductionTransportCustody::default();
            originals
                .finish(
                    sequence,
                    private_result_record::<RecoveryRecord>(
                        sequence,
                        &Err(error.clone()),
                        &transport,
                    ),
                )
                .unwrap();
            let raw = fs::read(
                originals
                    .root
                    .join(format!("{sequence:02}.result.original.json")),
            )
            .unwrap();
            let retained: Value = serde_json::from_slice(&raw).unwrap();
            assert_eq!(
                retained["result"]["typed_original_error"],
                serde_json::to_value(&error).unwrap()
            );
            assert!(retained["result"].get("original_outer_envelope").is_none());
            assert_eq!(
                retained["original_transport_snapshot"]["entries"],
                json!([])
            );
            let failure = prepared.decode_read::<&Error>(Err(&error)).err().unwrap();
            let public = recovery_read_error(failure);
            assert_eq!(
                public,
                recovery_read_error(GraphRecoveryReadError::ReadFailed)
            );
            assert!(public.is_string());
            assert!(public.get("outcome_known").is_none());
            assert!(!public.as_str().unwrap().contains(&error.message));
        }
        assert!(!originals.stopped);
        assert_eq!(originals.reserved_files, 4);
    }

    #[test]
    fn failed_mutation_result_publication_preserves_originals_and_only_emits_unknown() {
        // Inert journal values, not a successful Native receipt or Host fixture.
        // Both a success-shaped payload and an original known error must stay
        // private if create_new cannot publish the result without replacement.
        let known_error = Error::new(ErrorCode::PolicyDenied, "original private denial");
        let transport = ProductionTransportCustody::default();
        let original_records = [
            json!({"inert_original_payload":{"items":[1,2,3]},"observed_kind":"success"}),
            private_result_record::<()>(0, &Err(known_error), &transport),
        ];
        for record in original_records {
            let directory = tempfile::tempdir().unwrap();
            let mut originals = PrivateOriginals::create(
                &directory.path().join("client.json"),
                fixture_restrictions(),
            )
            .unwrap();
            let sequence = originals
                .begin(json!({"original_application_request_id":ORIGINAL_KEY}))
                .unwrap();
            let request_path = originals.root.join("00.request.original.json");
            let request_bytes = fs::read(&request_path).unwrap();
            let result_path = originals.root.join("00.result.original.json");
            let prior = b"prior partial original; must not be replaced";
            fs::write(&result_path, prior).unwrap();
            let public = originals
                .finish(sequence, record.clone())
                .map_err(|error| mutation_custody_failure(ORIGINAL_KEY, error))
                .unwrap_err();
            assert_eq!(
                public,
                mutation_failure(GraphInvokeFailure::unknown(ORIGINAL_KEY))
            );
            assert_eq!(public["outcome_known"], false);
            assert_eq!(public["request_id"], ORIGINAL_KEY);
            assert_eq!(public.as_object().unwrap().len(), 4);
            assert_eq!(originals.unpublished, Some(record));
            assert_eq!(fs::read(result_path).unwrap(), prior);
            assert_eq!(fs::read(request_path).unwrap(), request_bytes);
            assert!(originals.stopped);
            assert!(
                originals
                    .begin(json!({"fresh_request":"must_not_dispatch"}))
                    .is_err()
            );
        }
    }

    #[test]
    fn failed_read_result_publication_is_an_ordinary_error_before_any_ready_or_decode() {
        for recovery in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let mut originals = PrivateOriginals::create(
                &directory.path().join("client.json"),
                fixture_restrictions(),
            )
            .unwrap();
            let sequence = originals
                .begin(json!({"inert_read":"publication_only"}))
                .unwrap();
            let original = json!({"inert_original_read_payload":[1,2,3]});
            let result_path = originals.root.join("00.result.original.json");
            let prior = b"original partial result";
            fs::write(&result_path, prior).unwrap();
            let projected = std::cell::Cell::new(false);
            let result = originals
                .finish(sequence, original.clone())
                .map_err(|error| {
                    if recovery {
                        recovery_read_error(GraphRecoveryReadError::ReadFailed)
                    } else {
                        Value::String(error)
                    }
                })
                .map(|()| projected.set(true));
            let public = result.unwrap_err();
            assert!(public.is_string());
            assert!(public.get("outcome_known").is_none());
            assert!(!projected.get());
            assert_eq!(originals.unpublished, Some(original));
            assert_eq!(fs::read(result_path).unwrap(), prior);
            assert!(originals.stopped);
        }
    }

    #[test]
    fn ninth_explicit_callback_keeps_prior_originals_without_reusing_or_expanding_a_capture() {
        let directory = tempfile::tempdir().unwrap();
        let connection = directory.path().join("client.json");
        let mut originals = PrivateOriginals::create(&connection, fixture_restrictions()).unwrap();
        // Nine is this pure fixture's declared callback cap, not a default or
        // the callback count of any actual production launch.
        let mut callbacks = CallbackBudget::new(9);
        for index in 0..9 {
            assert_eq!(callbacks.reserve().unwrap(), index + 1);
            let sequence = originals.begin(json!({"explicit_callback":index})).unwrap();
            assert_eq!(sequence, index);
            // Each real callback creates its own unchanged bounded capture.
            let capture = ProductionTransportCustody::default();
            assert_eq!(capture.private_snapshot()["slot_limit"], 8);
            originals
                .finish(
                    sequence,
                    json!({"explicit_callback":index,
                "transport":capture.private_snapshot()}),
                )
                .unwrap();
        }
        assert!(!originals.stopped);
        assert_eq!(originals.reserved_files, 18);
        assert!(callbacks.reserve().is_err());
        assert_eq!(callbacks.consumed, 9);
        for index in 0..9 {
            let raw = fs::read(
                originals
                    .root
                    .join(format!("{index:02}.request.original.json")),
            )
            .unwrap();
            assert_eq!(
                serde_json::from_slice::<Value>(&raw).unwrap(),
                json!({"explicit_callback":index})
            );
        }
    }
}
