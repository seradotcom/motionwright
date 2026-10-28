//! Private ownership of the existing Native Graph transport originals.
//!
//! One attempt is admitted at a time. The journal owns its live capture before
//! the caller awaits Native. Completed material leaves RAM only after the held
//! private file accepts a complete record and sync_all succeeds. Cancellation
//! or publication failure keeps the capture and blocks the next admission.
//! This is evidence custody, never a Store, authority or permission to retry.

use motionwright_native::production::{NativeGraphRecipeResult, ProductionTransportCustody};
use semwright_native_sdk::Result as NativeResult;
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    fs::{File, OpenOptions},
    io::{self, Write},
    path::Path,
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
};
use uuid::Uuid;

const TRANSPORT_SCHEMA: &str = "motionwright.production.private-transport-custody/1";

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
enum GraphCustodyOperation {
    Context,
    Upsert,
    Remove,
    Recovery,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GraphExchangeRetention {
    Published,
    RetainedInMemory,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct GraphCustodyUnavailable;

impl std::fmt::Display for GraphCustodyUnavailable {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Private Graph custody requires resolution before another operation")
    }
}

impl std::error::Error for GraphCustodyUnavailable {}

// A private writer seam exercises actual append/sync failures without a Native
// mock or a public storage provider. Production always supplies the held File.
trait JournalWriter: Send {
    fn write_all(&mut self, bytes: &[u8]) -> io::Result<()>;
    fn sync_all(&mut self) -> io::Result<()>;
}

impl JournalWriter for File {
    fn write_all(&mut self, bytes: &[u8]) -> io::Result<()> {
        Write::write_all(self, bytes)
    }

    fn sync_all(&mut self) -> io::Result<()> {
        File::sync_all(self)
    }
}

struct PendingAttempt {
    custody_id: Uuid,
    operation: GraphCustodyOperation,
    request_id: Option<String>,
    transport: ProductionTransportCustody,
    native_return: OnceLock<Value>,
    caller_dropped: AtomicBool,
    published: AtomicBool,
}

struct JournalState {
    writer: Box<dyn JournalWriter>,
    pending: Option<Arc<PendingAttempt>>,
    publication_error: Option<io::Error>,
}

#[derive(Clone)]
pub(crate) struct GraphExchangeJournal {
    session: Uuid,
    inner: Arc<Mutex<JournalState>>,
}

/// Not cloneable or serializable. Keep this guard alive across the one Native
/// call. A dropped caller leaves the same live capture owned by the journal.
pub(crate) struct GraphExchangeAttempt {
    journal: GraphExchangeJournal,
    pending: Arc<PendingAttempt>,
    return_observed: bool,
}

impl GraphExchangeJournal {
    /// data_dir and workspace_session come only from trusted Desktop setup.
    /// Never accept a destination, filename or session from the WebView. The
    /// file is opened once without replacing any prior evidence and kept open.
    pub(crate) fn new(data_dir: &Path, workspace_session: Uuid) -> io::Result<Self> {
        if workspace_session.is_nil() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Graph custody requires the actual workspace session",
            ));
        }
        let path = data_dir.join(format!(
            "semantic-graph-native-{}.private.jsonl",
            workspace_session.simple()
        ));
        let mut options = OpenOptions::new();
        options.append(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(path)?;
        Ok(Self {
            session: workspace_session,
            inner: Arc::new(Mutex::new(JournalState {
                writer: Box::new(file),
                pending: None,
                publication_error: None,
            })),
        })
    }

    /// Reserve custody before attaching its fresh transport capture to a
    /// client. The original application key is diagnostic, never replaced by
    /// custody_id. Context has no mutation key; other routes retain its spelling.
    pub(crate) fn begin(
        &self,
        operation: &'static str,
        request_id: Option<&str>,
    ) -> Result<GraphExchangeAttempt, String> {
        self.begin_checked(operation, request_id)
            .map_err(|error| error.to_string())
    }

    fn begin_checked(
        &self,
        operation: &'static str,
        request_id: Option<&str>,
    ) -> Result<GraphExchangeAttempt, GraphCustodyUnavailable> {
        let operation = match operation {
            "context" => GraphCustodyOperation::Context,
            "upsert" => GraphCustodyOperation::Upsert,
            "remove" => GraphCustodyOperation::Remove,
            "recovery" => GraphCustodyOperation::Recovery,
            _ => return Err(GraphCustodyUnavailable),
        };
        match (operation, request_id) {
            (GraphCustodyOperation::Context, None) => {}
            (GraphCustodyOperation::Context, Some(_)) | (_, None) => {
                return Err(GraphCustodyUnavailable);
            }
            (_, Some(key))
                if key.len() == 36 && Uuid::parse_str(key).is_ok_and(|key| !key.is_nil()) => {}
            _ => return Err(GraphCustodyUnavailable),
        }
        let mut state = self.inner.lock().map_err(|_| GraphCustodyUnavailable)?;
        if state.pending.is_some() {
            return Err(GraphCustodyUnavailable);
        }
        let pending = Arc::new(PendingAttempt {
            custody_id: Uuid::now_v7(),
            operation,
            request_id: request_id.map(str::to_owned),
            transport: ProductionTransportCustody::default(),
            native_return: OnceLock::new(),
            caller_dropped: AtomicBool::new(false),
            published: AtomicBool::new(false),
        });
        state.pending = Some(pending.clone());
        Ok(GraphExchangeAttempt {
            journal: self.clone(),
            pending,
            return_observed: false,
        })
    }

    /// Explicit backend resolution of retained evidence only. This performs no
    /// Native call, changes no mutation outcome and never permits an automatic
    /// mutation retry. The caller must separately recover any original UNKNOWN.
    /// Returns false when there was no pending custody, true after publication.
    pub(crate) fn publish_retained(&self) -> Result<bool, String> {
        self.publish_retained_checked()
            .map_err(|error| error.to_string())
    }

    fn publish_retained_checked(&self) -> Result<bool, GraphCustodyUnavailable> {
        let mut state = self.inner.lock().map_err(|_| GraphCustodyUnavailable)?;
        let Some(pending) = state.pending.clone() else {
            return Ok(false);
        };
        if pending.native_return.get().is_none() && !pending.caller_dropped.load(Ordering::Acquire)
        {
            return Err(GraphCustodyUnavailable);
        }
        self.publish_locked(&mut state, &pending).map(|()| true)
    }

    fn publish_returned(&self, pending: &Arc<PendingAttempt>) -> GraphExchangeRetention {
        // Poison cannot discard an already observed return. It was saved in
        // pending.native_return before this lock and the journal still owns it.
        let Ok(mut state) = self.inner.lock() else {
            return GraphExchangeRetention::RetainedInMemory;
        };
        // An explicit resolution can race with this final publication step.
        // Only the actual successful write+sync sets this private fact.
        if pending.published.load(Ordering::Acquire) {
            return GraphExchangeRetention::Published;
        }
        if !state
            .pending
            .as_ref()
            .is_some_and(|retained| Arc::ptr_eq(retained, pending))
        {
            return GraphExchangeRetention::RetainedInMemory;
        }
        match self.publish_locked(&mut state, pending) {
            Ok(()) => GraphExchangeRetention::Published,
            Err(_) => GraphExchangeRetention::RetainedInMemory,
        }
    }

    fn publish_locked(
        &self,
        state: &mut JournalState,
        pending: &PendingAttempt,
    ) -> Result<(), GraphCustodyUnavailable> {
        // Read the live original at publication time, including the terminal
        // recorded when an awaited transport future was dropped. An early Drop
        // snapshot would lose observations made during that future's teardown.
        let transport = pending.transport.private_snapshot();
        if !transport_observation_finished(&transport) {
            return Err(GraphCustodyUnavailable);
        }
        let value = json!({
            "schema": "motionwright.desktop.private-graph-exchange/1",
            "workspace_session": self.session,
            "custody_id": pending.custody_id,
            "operation": pending.operation,
            "request_id": pending.request_id,
            "transport": transport,
            "native_return": pending.native_return.get(),
            "caller_dropped_without_return": pending.caller_dropped.load(Ordering::Acquire),
            "prior_publication_error": state.publication_error.as_ref().map(|error| json!({
                "kind": format!("{:?}", error.kind()),
                "private_diagnostic": error.to_string(),
            })),
            "authority": false,
            "automatic_retry": false,
            "process_cohort_closed_claimed": false,
        });
        let publication = append_record(state.writer.as_mut(), &value);
        match publication {
            Ok(()) => {
                pending.published.store(true, Ordering::Release);
                state.pending = None;
                state.publication_error = None;
                Ok(())
            }
            Err(error) => {
                state.publication_error = Some(error);
                Err(GraphCustodyUnavailable)
            }
        }
    }
}

fn append_record(writer: &mut dyn JournalWriter, value: &Value) -> io::Result<()> {
    let encoded = serde_json::to_vec(&value).map_err(io::Error::other)?;
    // A failed earlier append may leave a partial final line. Preserve it
    // verbatim, then start a complete new record; never truncate or overwrite.
    // custody_id identifies repeated publication of the same invocation after
    // an uncertain sync, not another Native execution.
    writer.write_all(b"\n")?;
    writer.write_all(&encoded)?;
    writer.write_all(b"\n")?;
    writer.sync_all()
}

impl GraphExchangeAttempt {
    pub(crate) fn custody(&self) -> &ProductionTransportCustody {
        &self.pending.transport
    }

    /// Call before projecting a Native outcome to the WebView. Consuming this
    /// guard makes recording the return a single action. Publication failure
    /// leaves the exact return and live transport private in the journal.
    ///
    /// RetainedInMemory is a custody failure after invocation, never proof that
    /// the mutation did not apply. The caller retains the actual Native Result
    /// unchanged; do not replace its private error/knownness or retry Native.
    pub(crate) fn retain_result<T>(
        mut self,
        returned: &NativeResult<NativeGraphRecipeResult<T>>,
    ) -> GraphExchangeRetention {
        self.pending
            .native_return
            .get_or_init(|| private_native_return(returned));
        self.return_observed = true;
        self.journal.publish_returned(&self.pending)
    }
}

impl Drop for GraphExchangeAttempt {
    fn drop(&mut self) {
        if !self.return_observed {
            // The journal still holds this Arc and the live transport capture.
            // No write, reset, synthetic error, retry or early snapshot on Drop.
            self.pending.caller_dropped.store(true, Ordering::Release);
        }
    }
}

// D649 ProductionClient owns wait/stdout/stderr/stdin futures inline under
// timeout_at + try_join!. Capturers borrow its local CaptureReservation and
// cannot write after that reservation finishes or drops; no capturer is spawned
// into a detached task. This checks that observation lifetime only.
// No claim of process exit, EOF or process-cohort closure is made here. In
// particular, future_dropped_without_return can retain a null process_exit and
// false EOF flags. Those original flags remain authoritative and UNKNOWN never
// becomes permission to retry. Re-review this gate if Native changes ownership.
fn transport_observation_finished(snapshot: &Value) -> bool {
    if snapshot.get("schema").and_then(Value::as_str) != Some(TRANSPORT_SCHEMA)
        || snapshot.get("mutex_poisoned").and_then(Value::as_bool) != Some(false)
    {
        return false;
    }
    snapshot
        .get("entries")
        .and_then(Value::as_array)
        .is_some_and(|entries| {
            entries.iter().all(|entry| {
                matches!(
                    entry
                        .pointer("/transport_terminal/kind")
                        .and_then(Value::as_str),
                    Some("success" | "error" | "future_dropped_without_return")
                )
            })
        })
}

fn private_native_return<T>(returned: &NativeResult<NativeGraphRecipeResult<T>>) -> Value {
    match returned {
        Ok(report) => json!({
            "kind": "report",
            "request_args": report.request_args(),
            "recipe_sha256": report.recipe_sha256(),
            "request_sha256": report.request_sha256(),
            "original_envelope": report.original_envelope(),
            "typed_observation_envelope": report.observation_envelope(),
            "typed_operation_envelope": report.operation_envelope(),
            // Native has already interpreted its response before returning the
            // report. Keep its original interpretation error privately too;
            // never relabel an Ok(report) as an applied mutation.
            "native_interpretation": match report.outcome() {
                Ok(_) => json!({"kind": "ok"}),
                Err(error) => json!({"kind": "error", "original_error": error}),
            },
        }),
        // An outer failure can have original output without a parsed report.
        // The independent live transport capture retains those exact bytes.
        Err(error) => json!({"kind": "error", "original_error": error}),
    }
}

#[cfg(test)]
#[path = "semantic_graph_custody_tests.rs"]
mod tests;
