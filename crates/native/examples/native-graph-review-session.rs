//! Native-owned JSONL adapter for the exact shared Studio review Session.
//!
//! Each callback constructs the typed Native client from the actual opened
//! Service. That client obtains the retained SQLite connection identity and
//! binds it to the original Host summary and final provider guard. Unsupported
//! identity queries reject before Graph dispatch; no UUID, pathname, JSON flag
//! or cached Ready replaces the connection witness or current Host authority.
//! This executable must run under A6's private owner/custody lifecycle.

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
    Error, Result as NativeResult,
    cooperation::{RecoveryRecord, RequestIdentity},
};
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

const CONNECTION_ENV: &str = "MOTIONWRIGHT_GRAPH_CONNECTION";
const BINDING_UNAVAILABLE: &str =
    "Native Graph cannot bind the actual opened Store and owner connection.";
// These are the existing selected-custody ceiling values, not another owner
// allowance. A6's enclosing collector must include these files in the SAME
// 512 MiB / 4096-file pool with all other private originals.
const MAX_PRIVATE_FILES: usize = 4096;
const MAX_PRIVATE_TOTAL_BYTES: usize = 512 * 1024 * 1024;
const MAX_PRIVATE_RECORD_BYTES: usize = 16 * 1024 * 1024;

// A1 owns UUID validation and the closed public mutation DTO. This adapter
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
    stopped: bool,
    unpublished: Option<Value>,
}

impl PrivateOriginals {
    fn create(connection_file: &Path) -> Result<Self, String> {
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
            stopped: false,
            unpublished: None,
        })
    }

    fn write_new(&mut self, name: &str, value: &Value) -> Result<(), String> {
        let raw =
            serde_json::to_vec(value).map_err(|_| "Native Graph private record encoding failed")?;
        if raw.len() > MAX_PRIVATE_RECORD_BYTES
            || self.reserved_files >= MAX_PRIVATE_FILES
            || self
                .reserved_bytes
                .checked_add(raw.len())
                .is_none_or(|bytes| bytes > MAX_PRIVATE_TOTAL_BYTES)
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
        // Reserve room for this request and its maximum possible result BEFORE
        // dispatch. A later custody error preserves the private Native original
        // but prevents publishing success, known rejection or read readiness.
        if self.stopped
            || self.reserved_files > MAX_PRIVATE_FILES - 2
            || self.reserved_bytes > MAX_PRIVATE_TOTAL_BYTES - 2 * MAX_PRIVATE_RECORD_BYTES
        {
            return Err("Native Graph original custody has no capacity for another request".into());
        }
        let sequence = self.next;
        self.next += 1; // Latch before any write; never reuse an uncertain name.
        if let Err(error) =
            self.write_new(&format!("{sequence:02}.request.original.json"), &request)
        {
            self.stopped = true;
            self.unpublished = Some(request);
            return Err(error);
        }
        Ok(sequence)
    }

    fn finish(&mut self, sequence: usize, result: Value) -> Result<(), String> {
        if self
            .write_new(&format!("{sequence:02}.result.original.json"), &result)
            .is_err()
        {
            self.stopped = true;
            self.unpublished = Some(result);
            // A6 preserves this private stderr plus any partial original file.
            // The private original remains unchanged; public projection fails.
            eprintln!(
                "NATIVE_GRAPH_PRIVATE_CUSTODY_INCOMPLETE; original outcome preserved; no retry"
            );
            return Err("Native Graph original custody is unavailable".into());
        }
        Ok(())
    }
}

struct NativeGraphBackend {
    connection: ProductionConnection,
    runtime: tokio::runtime::Runtime,
    originals: PrivateOriginals,
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
    // or Native read. The pure A1 codec owns canonical identity preparation.
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
    ) -> Result<Self, String> {
        let connection = ProductionConnection::load(connection_file)
            .map_err(|_| "Native Graph connection is unavailable or invalid")?;
        let mut originals = PrivateOriginals::create(connection_file)?;
        let sequence = originals.begin(json!({
            "schema":"motionwright.native.graph-review-private-request/1",
            "operation":"factory_bind_actual_service",
            "native_dispatch_attempted":false,
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
        })
    }

    fn retain<T>(
        &mut self,
        sequence: usize,
        report: &NativeResult<NativeGraphRecipeResult<T>>,
        transport: &ProductionTransportCustody,
    ) -> Result<(), String> {
        self.originals
            .finish(sequence, private_result_record(sequence, report, transport))
    }
}

impl shared_session::GraphBackend for NativeGraphBackend {
    fn authoring_context(
        &mut self,
        service: &StudioService,
        _database: &Path,
        request: &SemanticGraphContextRequest,
    ) -> Result<GraphAuthoringContext, Value> {
        // One fresh bounded capture per explicit callback, using the same
        // original owner connection. No old capture is evicted or reset.
        let transport = ProductionTransportCustody::default();
        let sequence = self
            .originals
            .begin(json!({
                "schema":"motionwright.native.graph-review-private-request/1",
                "operation":"authoring_context",
                "original_context_request":request,
                "automatic_retry":false,
            }))
            .map_err(|_| Value::String("Native Graph original custody is unavailable".into()))?;
        let client = match NativeGraphRecipeClient::new(self.connection.clone(), service) {
            Ok(client) => client.with_transport_custody(&transport),
            Err(error) => {
                self.retain::<GraphAuthoringContext>(sequence, &Err(error), &transport)
                    .map_err(Value::String)?;
                return Err(Value::String(BINDING_UNAVAILABLE.into()));
            }
        };
        let report = self.runtime.block_on(client.authoring_context(request));
        self.retain(sequence, &report, &transport)
            .map_err(Value::String)?;
        match report {
            Ok(report) => report
                .outcome()
                .cloned()
                .map_err(|_| Value::String("Native Graph context is unavailable".into())),
            Err(_) => Err(Value::String("Native Graph context is unavailable".into())),
        }
    }

    fn operation_recovery(
        &mut self,
        service: &StudioService,
        _database: &Path,
        request: Value,
    ) -> Result<Value, Value> {
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
        // Preserve every actual envelope, typed failure and binary transport
        // observation BEFORE A1 validates the historical read DTO.
        self.retain(sequence, &report, &transport)
            .map_err(|_| recovery_read_error(GraphRecoveryReadError::ReadFailed))?;
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
        self.retain(sequence, &report, &transport)
            .map_err(|error| mutation_custody_failure(request_id, error))?;
        match report {
            Ok(report) => match report.outcome() {
                // This is the original native receipt, including exact replay.
                // Do not reload local SQLite or recalculate a successful Project.
                Ok(receipt) => Ok(receipt.project().clone()),
                Err(_) => Err(mutation_failure(match report.original_native_error() {
                    // This accessor is populated only by the client's original
                    // typed Native failure, never a later local decoder error.
                    Some(error) => GraphInvokeFailure::from_native(request_id, error),
                    None => GraphInvokeFailure::unknown(request_id),
                })),
            },
            Err(_) => Err(mutation_failure(GraphInvokeFailure::unknown(request_id))),
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Per-child startup configuration only. Browser invoke payloads cannot
    // select a connection, SDK executable, owner session or custody root.
    let connection_file = std::env::var_os(CONNECTION_ENV)
        .map(PathBuf::from)
        .ok_or("MOTIONWRIGHT_GRAPH_CONNECTION is required for the Native Graph Session")?;
    shared_session::run_with_graph_backend(move |database, service| {
        let backend = NativeGraphBackend::open(database, service, &connection_file)?;
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
        let mut originals = PrivateOriginals::create(&connection).unwrap();
        assert!(PrivateOriginals::create(&connection).is_err());
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
        let mut originals =
            PrivateOriginals::create(&directory.path().join("client.json")).unwrap();
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
        let mut originals =
            PrivateOriginals::create(&directory.path().join("recovery.json")).unwrap();
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
        let mut originals =
            PrivateOriginals::create(&directory.path().join("recovery.json")).unwrap();
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
        let mut originals =
            PrivateOriginals::create(&directory.path().join("recovery.json")).unwrap();
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
        let mut originals =
            PrivateOriginals::create(&directory.path().join("recovery.json")).unwrap();
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
            let mut originals =
                PrivateOriginals::create(&directory.path().join("client.json")).unwrap();
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
            let mut originals =
                PrivateOriginals::create(&directory.path().join("client.json")).unwrap();
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
        let mut originals = PrivateOriginals::create(&connection).unwrap();
        for index in 0..9 {
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
