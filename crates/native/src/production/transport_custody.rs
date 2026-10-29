//! Optional custody of bytes observed by the existing production CLI transport.
//! This is private diagnostic material. It is not a receipt, lease or permission.
use super::{
    BrokerResult, Error, ErrorCode, MAX_ARGS_BYTES, MAX_OUTPUT_BYTES, NativeResult,
    ProductionConnection, Value, backend, json,
};
use sha2::{Digest, Sha256};
use std::{
    ffi::OsStr,
    process::ExitStatus,
    sync::{Arc, Mutex, MutexGuard},
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

const MAX_CAPTURE_SLOTS: usize = 8;
const MAX_CAPTURE_ARG_BYTES: usize = 2 * (MAX_ARGS_BYTES + 64 * 1024);

#[derive(Clone)]
pub struct ProductionTransportCustody {
    inner: Arc<Mutex<State>>,
}

struct State {
    limit: usize,
    entries: Vec<RetainedTransport>,
}

struct RetainedTransport {
    command: String,
    mutation: bool,
    connection_identity: String,
    executable_sha256: String,
    executable: Value,
    argv: Vec<Value>,
    args_json: Vec<u8>,
    graph_stdin: Option<GraphStdinCapture>,
    started: bool,
    pid: Option<u32>,
    process_exit: Option<Value>,
    stdout: StreamCapture,
    stderr: StreamCapture,
    returned: Option<Result<(), Error>>,
    dropped_without_return: bool,
}

#[derive(Default)]
struct StreamCapture {
    bytes: Vec<u8>,
    eof_observed: bool,
    read_error_observed: bool,
}

#[derive(Default)]
struct GraphStdinCapture {
    write_started: bool,
    accepted_by_pipe_bytes: usize,
    write_return_lengths: Vec<usize>,
    write_error_kind: Option<String>,
    shutdown_attempted: bool,
    shutdown_completed: bool,
    shutdown_error_kind: Option<String>,
    writer_returned: Option<bool>,
    writer_dropped_without_return: bool,
    transport_deadline_elapsed_observed: bool,
}

struct GraphStdinObservation<'a> {
    capture: Option<&'a CaptureReservation>,
    active: bool,
}

#[derive(Clone, Copy)]
pub(super) enum Stream {
    Stdout,
    Stderr,
}

pub(super) struct CaptureReservation {
    custody: ProductionTransportCustody,
    index: usize,
    active: bool,
}

impl Default for ProductionTransportCustody {
    fn default() -> Self {
        Self::with_limit(MAX_CAPTURE_SLOTS)
    }
}

impl ProductionTransportCustody {
    fn with_limit(limit: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(State {
                limit,
                entries: Vec::new(),
            })),
        }
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        // Original material remains inspectable after poison. A new dispatch is
        // rejected below; a post-dispatch capture never replaces its native error.
        self.inner.lock().unwrap_or_else(|error| error.into_inner())
    }

    pub(super) fn reserve<'a>(
        &self,
        command: &str,
        mutation: bool,
        connection: &ProductionConnection,
        program: &'a OsStr,
        argv: impl Iterator<Item = &'a OsStr> + Clone,
        args_json: &[u8],
    ) -> NativeResult<CaptureReservation> {
        let argument_budget = std::iter::once(program)
            .chain(argv.clone())
            .try_fold(0_usize, |total, argument| {
                argument
                    .as_encoded_bytes()
                    .len()
                    .checked_mul(2)
                    .and_then(|bytes| total.checked_add(bytes))
            })
            .filter(|total| *total <= MAX_CAPTURE_ARG_BYTES);
        if argument_budget.is_none() || args_json.len() > MAX_ARGS_BYTES {
            return Err(capacity_error());
        }
        let connection_identity = connection.identity()?;
        let mut state = self.lock();
        if self.inner.is_poisoned() {
            return Err(backend("Production transport custody is unavailable"));
        }
        if state.entries.len() >= state.limit {
            return Err(capacity_error());
        }
        let index = state.entries.len();
        state.entries.push(RetainedTransport {
            command: command.to_owned(),
            mutation,
            connection_identity,
            executable_sha256: connection.executable_sha256.clone(),
            executable: os_argument(program),
            argv: argv.map(os_argument).collect(),
            args_json: args_json.to_vec(),
            graph_stdin: None,
            started: false,
            pid: None,
            process_exit: None,
            stdout: StreamCapture::default(),
            stderr: StreamCapture::default(),
            returned: None,
            dropped_without_return: false,
        });
        Ok(CaptureReservation {
            custody: self.clone(),
            index,
            active: true,
        })
    }

    /// The fixed Graph route alone writes its already retained JSON arguments
    /// to stdin. Legacy reservations retain their original null-input shape.
    pub(super) fn reserve_graph_recipe<'a>(
        &self,
        mutation: bool,
        connection: &ProductionConnection,
        program: &'a OsStr,
        argv: impl Iterator<Item = &'a OsStr> + Clone,
        args_json: &[u8],
    ) -> NativeResult<CaptureReservation> {
        let expected = [
            OsStr::new("--socket"),
            connection.socket.as_os_str(),
            OsStr::new("--session-file"),
            connection.session_file.as_os_str(),
            OsStr::new("--json"),
            OsStr::new("execute"),
            OsStr::new(super::graph_authoring::RECIPE_COMMAND),
            OsStr::new("--args-file"),
            OsStr::new("-"),
        ];
        if program != connection.executable.as_os_str() || !argv.clone().eq(expected) {
            return Err(Error::invalid(
                "Graph stdin custody requires the fixed CLI route",
            ));
        }
        let reservation = self.reserve(
            super::graph_authoring::RECIPE_COMMAND,
            mutation,
            connection,
            program,
            argv,
            args_json,
        )?;
        self.lock().entries[reservation.index].graph_stdin = Some(GraphStdinCapture::default());
        Ok(reservation)
    }

    /// Inspect the actual transport state, without classifying a Graph outcome
    /// or replacing its original error. A pending or dropped invocation is not
    /// a confirmed terminal. No later callback may mint a fresh capture after
    /// an original transport failure under the same client admission.
    pub fn has_unconfirmed_or_failed_invocation(&self) -> bool {
        self.inner.is_poisoned()
            || self.lock().entries.iter().any(|entry| {
                entry.dropped_without_return || !matches!(&entry.returned, Some(Ok(())))
            })
    }

    /// Lossless private projection of the original argv, JSON argument bytes and
    /// observed process streams. Byte strings use hexadecimal, never lossy UTF-8.
    /// Nothing is evicted or retried. The caller owns durable publication.
    pub fn private_snapshot(&self) -> Value {
        let state = self.lock();
        let entries: Vec<Value> = state
            .entries
            .iter()
            .enumerate()
            .map(|(index, entry)| {
                let terminal = match &entry.returned {
                    Some(Ok(())) => json!({"kind":"success"}),
                    Some(Err(error)) => json!({"kind":"error","original_error":error}),
                    None => json!({"kind":if entry.dropped_without_return {
                        "future_dropped_without_return"
                    } else {
                        "pending"
                    }}),
                };
                let mut value = json!({
                    "sequence":index,
                    "command":entry.command,
                    "mutation":entry.mutation,
                    "connection_identity":entry.connection_identity,
                    "executable_sha256":entry.executable_sha256,
                    "executable":entry.executable,
                    "argv":entry.argv,
                    "args_json":bytes_projection(&entry.args_json),
                    "stdin_mode":if entry.graph_stdin.is_some() {
                        "piped_graph_recipe_json"
                    } else {
                        "null"
                    },
                    "process_started":entry.started,
                    "pid":entry.pid,
                    "process_exit":entry.process_exit,
                    "stdout":stream_projection(&entry.stdout),
                    "stderr":stream_projection(&entry.stderr),
                    "transport_terminal":terminal,
                    "automatic_retry":false,
                });
                if let Some(stdin) = &entry.graph_stdin {
                    value["stdin"] = json!({
                        "intended":bytes_projection(&entry.args_json),
                        "write_started":stdin.write_started,
                        "accepted_by_pipe_prefix":bytes_projection(
                            &entry.args_json[..stdin.accepted_by_pipe_bytes]
                        ),
                        "accepted_by_pipe_bytes":stdin.accepted_by_pipe_bytes,
                        "write_return_lengths":stdin.write_return_lengths,
                        "all_intended_bytes_accepted_by_pipe":stdin.write_started
                            && stdin.accepted_by_pipe_bytes == entry.args_json.len(),
                        "write_error_kind":stdin.write_error_kind,
                        "shutdown_attempted":stdin.shutdown_attempted,
                        "shutdown_completed":stdin.shutdown_completed,
                        "shutdown_error_kind":stdin.shutdown_error_kind,
                        "writer_returned":stdin.writer_returned,
                        "writer_dropped_without_return":stdin.writer_dropped_without_return,
                        "transport_deadline_elapsed_observed":stdin.transport_deadline_elapsed_observed,
                        "child_input_consumption_observed":false,
                        "child_eof_read_observed":false,
                        "pipe_acceptance_is_child_consumption":false,
                        "automatic_retry":false,
                    });
                }
                value
            })
            .collect();
        json!({
            "schema":"motionwright.production.private-transport-custody/1",
            "durable":false,
            "authority":false,
            "mutex_poisoned":self.inner.is_poisoned(),
            "slot_limit":state.limit,
            "per_stream_capture_limit_bytes":MAX_OUTPUT_BYTES+1,
            "argument_capture_limit_bytes":MAX_CAPTURE_ARG_BYTES,
            "entries":entries,
        })
    }
}

fn capacity_error() -> Error {
    Error::new(
        ErrorCode::ResourceExhausted,
        "Production transport custody has no capacity for another invocation",
    )
}

fn bytes_projection(bytes: &[u8]) -> Value {
    json!({
        "encoding":"hex",
        "bytes":bytes.len(),
        "sha256":hex::encode(Sha256::digest(bytes)),
        "value":hex::encode(bytes),
    })
}

fn stream_projection(stream: &StreamCapture) -> Value {
    json!({
        "original":bytes_projection(&stream.bytes),
        "eof_observed":stream.eof_observed,
        "read_error_observed":stream.read_error_observed,
        "capture_limit_exceeded":stream.bytes.len()>MAX_OUTPUT_BYTES,
    })
}

fn os_argument(value: &OsStr) -> Value {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let bytes: Vec<u8> = value.encode_wide().flat_map(u16::to_le_bytes).collect();
        json!({"platform_encoding":"windows_utf16le","original":bytes_projection(&bytes)})
    }
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        json!({"platform_encoding":"unix_bytes","original":bytes_projection(value.as_bytes())})
    }
    #[cfg(not(any(windows, unix)))]
    {
        json!({
            "platform_encoding":"rust_osstr_encoded_bytes_same_platform_only",
            "original":bytes_projection(value.as_encoded_bytes()),
        })
    }
}

fn exit_projection(status: &ExitStatus) -> Value {
    #[cfg(unix)]
    let native = {
        use std::os::unix::process::ExitStatusExt;
        json!({"encoding":"unix_wait_status","value":status.into_raw()})
    };
    #[cfg(windows)]
    let native = {
        // Windows ExitStatusExt only constructs from_raw; code() carries the
        // original 32-bit Windows exit value through its signed i32 form.
        json!({"encoding":"windows_exit_code","value":status.code().map(|code| code as u32)})
    };
    #[cfg(not(any(windows, unix)))]
    let native = Value::Null;
    json!({"success":status.success(),"code":status.code(),"native":native})
}

impl CaptureReservation {
    pub(super) fn started(&self, pid: Option<u32>) {
        let mut state = self.custody.lock();
        let entry = &mut state.entries[self.index];
        entry.started = true;
        entry.pid = pid;
    }

    pub(super) fn process_exit(&self, status: &ExitStatus) {
        self.custody.lock().entries[self.index].process_exit = Some(exit_projection(status));
    }

    pub(super) fn transport_deadline_elapsed(&self) {
        if let Some(stdin) = &mut self.custody.lock().entries[self.index].graph_stdin {
            stdin.transport_deadline_elapsed_observed = true;
        }
    }

    fn with_stream(&self, stream: Stream, operation: impl FnOnce(&mut StreamCapture)) {
        let mut state = self.custody.lock();
        let entry = &mut state.entries[self.index];
        operation(match stream {
            Stream::Stdout => &mut entry.stdout,
            Stream::Stderr => &mut entry.stderr,
        });
    }

    fn append(&self, stream: Stream, bytes: &[u8]) {
        self.with_stream(stream, |capture| capture.bytes.extend_from_slice(bytes));
    }

    fn eof(&self, stream: Stream) {
        self.with_stream(stream, |capture| {
            // take() also returns EOF when its budget is consumed. That is not
            // evidence that the underlying stream reached its original EOF.
            capture.eof_observed = capture.bytes.len() <= MAX_OUTPUT_BYTES;
        });
    }

    fn read_error(&self, stream: Stream) {
        self.with_stream(stream, |capture| capture.read_error_observed = true);
    }

    pub(super) fn finish(&mut self, result: &NativeResult<BrokerResult>) {
        self.custody.lock().entries[self.index].returned =
            Some(result.as_ref().map(|_| ()).map_err(Clone::clone));
        self.active = false;
    }
}

impl Drop for CaptureReservation {
    fn drop(&mut self) {
        if self.active {
            self.custody.lock().entries[self.index].dropped_without_return = true;
        }
    }
}

impl<'a> GraphStdinObservation<'a> {
    fn begin(capture: Option<&'a CaptureReservation>, intended: &[u8]) -> std::io::Result<Self> {
        if intended.len() > MAX_ARGS_BYTES {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "Graph stdin exceeds the original argument budget",
            ));
        }
        if let Some(capture) = capture {
            let mut state = capture.custody.lock();
            let entry = &mut state.entries[capture.index];
            if entry.args_json != intended {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "Graph stdin differs from the reserved original arguments",
                ));
            }
            let stdin = entry.graph_stdin.as_mut().ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "Null-input custody cannot observe Graph stdin",
                )
            })?;
            if stdin.write_started {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "Graph stdin writer cannot be restarted",
                ));
            }
            stdin.write_started = true;
        }
        Ok(Self {
            capture,
            active: true,
        })
    }

    fn update(&self, operation: impl FnOnce(&mut GraphStdinCapture)) {
        if let Some(capture) = self.capture {
            let mut state = capture.custody.lock();
            if let Some(stdin) = &mut state.entries[capture.index].graph_stdin {
                operation(stdin);
            }
        }
    }

    fn accepted(&self, length: usize) {
        // The writer validates each return against the exact remaining slice.
        // Positive returns consume at least one byte; zero returns terminate.
        // Thus the list is bounded by MAX_ARGS_BYTES + 1, without eviction.
        self.update(|stdin| {
            stdin.accepted_by_pipe_bytes += length;
            stdin.write_return_lengths.push(length);
        });
    }

    fn returned(&mut self, result: &std::io::Result<()>) {
        self.update(|stdin| stdin.writer_returned = Some(result.is_ok()));
        self.active = false;
    }
}

impl Drop for GraphStdinObservation<'_> {
    fn drop(&mut self) {
        if self.active {
            self.update(|stdin| stdin.writer_dropped_without_return = true);
        }
    }
}

/// Observe bounded writes to the fixed Graph pipe under the caller's one
/// deadline. A completed write is only pipe acceptance; it never proves that
/// the child read, parsed or executed those bytes. Cancellation preserves the
/// confirmed prefix and cannot manufacture a writer or process terminal.
pub(super) async fn write_graph_recipe_stdin<W>(
    mut writer: W,
    intended: &[u8],
    capture: Option<&CaptureReservation>,
) -> std::io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    let mut observation = GraphStdinObservation::begin(capture, intended)?;
    let result = async {
        let mut offset = 0;
        while offset < intended.len() {
            match writer.write(&intended[offset..]).await {
                Ok(length) if length <= intended.len() - offset => {
                    observation.accepted(length);
                    if length == 0 {
                        observation
                            .update(|stdin| stdin.write_error_kind = Some("WriteZero".into()));
                        return Err(std::io::Error::new(
                            std::io::ErrorKind::WriteZero,
                            "Graph stdin write accepted no bytes",
                        ));
                    }
                    offset += length;
                }
                Ok(_) => {
                    observation.update(|stdin| stdin.write_error_kind = Some("InvalidData".into()));
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        "Graph stdin writer returned an impossible byte count",
                    ));
                }
                Err(error) => {
                    observation.update(|stdin| {
                        stdin.write_error_kind = Some(format!("{:?}", error.kind()));
                    });
                    return Err(error);
                }
            }
        }
        observation.update(|stdin| stdin.shutdown_attempted = true);
        match writer.shutdown().await {
            Ok(()) => observation.update(|stdin| stdin.shutdown_completed = true),
            Err(error) => {
                observation.update(|stdin| {
                    stdin.shutdown_error_kind = Some(format!("{:?}", error.kind()));
                });
                return Err(error);
            }
        }
        Ok(())
    }
    .await;
    observation.returned(&result);
    result
}

pub(super) async fn read_bounded_with_capture<R>(
    reader: R,
    capture: Option<&CaptureReservation>,
    stream: Stream,
) -> std::io::Result<Vec<u8>>
where
    R: AsyncRead + Unpin,
{
    let Some(capture) = capture else {
        return super::read_bounded(reader).await;
    };
    let mut limited = reader.take((MAX_OUTPUT_BYTES + 1) as u64);
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 8192];
    loop {
        let length = match limited.read(&mut chunk).await {
            Ok(length) => length,
            Err(error) => {
                capture.read_error(stream);
                return Err(error);
            }
        };
        if length == 0 {
            capture.eof(stream);
            return Ok(bytes);
        }
        // Capture these exact bytes before any parse, status interpretation or await.
        capture.append(stream, &chunk[..length]);
        bytes.extend_from_slice(&chunk[..length]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{path::PathBuf, time::Duration};
    use tokio::io::AsyncWriteExt;

    fn connection() -> ProductionConnection {
        ProductionConnection {
            schema: "motionwright-semwright-connection/1".into(),
            executable: PathBuf::from("client"),
            executable_sha256: "ab".repeat(32),
            socket: PathBuf::from("broker.sock"),
            session_file: PathBuf::from("session"),
            output_root: PathBuf::from("output"),
            resource: "project:transport-source-only".into(),
        }
    }

    fn reserve(custody: &ProductionTransportCustody) -> CaptureReservation {
        custody
            .reserve(
                "doctor",
                false,
                &connection(),
                OsStr::new("client"),
                [OsStr::new("--args-json"), OsStr::new("{}")].into_iter(),
                b"{}",
            )
            .unwrap()
    }

    fn reserve_graph(custody: &ProductionTransportCustody, intended: &[u8]) -> CaptureReservation {
        let connection = connection();
        let argv = [
            OsStr::new("--socket"),
            connection.socket.as_os_str(),
            OsStr::new("--session-file"),
            connection.session_file.as_os_str(),
            OsStr::new("--json"),
            OsStr::new("execute"),
            OsStr::new("recipe.run"),
            OsStr::new("--args-file"),
            OsStr::new("-"),
        ];
        custody
            .reserve_graph_recipe(
                true,
                &connection,
                connection.executable.as_os_str(),
                argv.into_iter(),
                intended,
            )
            .unwrap()
    }

    #[test]
    fn callback_transport_stop_query_preserves_the_original_error_and_pending_or_dropped_state() {
        let custody = ProductionTransportCustody::default();
        assert!(!custody.has_unconfirmed_or_failed_invocation());
        let mut pending = reserve(&custody);
        assert!(custody.has_unconfirmed_or_failed_invocation());
        let error =
            Error::new(ErrorCode::Timeout, "synthetic original unknown transport").uncertain();
        pending.finish(&Err(error.clone()));
        let original = custody.private_snapshot();
        assert!(custody.has_unconfirmed_or_failed_invocation());
        assert_eq!(custody.private_snapshot(), original);
        assert_eq!(
            original["entries"][0]["transport_terminal"]["original_error"],
            serde_json::to_value(&error).unwrap()
        );
        let dropped = ProductionTransportCustody::default();
        drop(reserve(&dropped));
        assert!(dropped.has_unconfirmed_or_failed_invocation());
    }

    #[test]
    fn callback_transport_stop_query_does_not_classify_a_returned_graph_outcome_as_transport_failure()
     {
        let custody = ProductionTransportCustody::default();
        let mut reservation = reserve(&custody);
        reservation.finish(&Ok(BrokerResult {
            value: json!({"synthetic_graph_outcome":"known rejection"}),
            request_id: "synthetic-component-only".into(),
        }));
        let original = custody.private_snapshot();
        assert!(!custody.has_unconfirmed_or_failed_invocation());
        assert_eq!(custody.private_snapshot(), original);
    }

    enum WriteAction {
        Accept(usize),
        Fail(std::io::ErrorKind),
    }

    struct ControlledWriter {
        actions: std::collections::VecDeque<WriteAction>,
        accepted: Arc<Mutex<Vec<u8>>>,
        shutdown_error: Option<std::io::ErrorKind>,
    }

    impl AsyncWrite for ControlledWriter {
        fn poll_write(
            self: std::pin::Pin<&mut Self>,
            _context: &mut std::task::Context<'_>,
            input: &[u8],
        ) -> std::task::Poll<std::io::Result<usize>> {
            let this = self.get_mut();
            match this
                .actions
                .pop_front()
                .unwrap_or(WriteAction::Accept(input.len()))
            {
                WriteAction::Accept(maximum) => {
                    let accepted = maximum.min(input.len());
                    this.accepted
                        .lock()
                        .unwrap()
                        .extend_from_slice(&input[..accepted]);
                    std::task::Poll::Ready(Ok(accepted))
                }
                WriteAction::Fail(kind) => std::task::Poll::Ready(Err(std::io::Error::from(kind))),
            }
        }

        fn poll_flush(
            self: std::pin::Pin<&mut Self>,
            _context: &mut std::task::Context<'_>,
        ) -> std::task::Poll<std::io::Result<()>> {
            std::task::Poll::Ready(Ok(()))
        }

        fn poll_shutdown(
            self: std::pin::Pin<&mut Self>,
            _context: &mut std::task::Context<'_>,
        ) -> std::task::Poll<std::io::Result<()>> {
            std::task::Poll::Ready(match self.shutdown_error {
                Some(kind) => Err(std::io::Error::from(kind)),
                None => Ok(()),
            })
        }
    }

    fn writer(
        actions: impl IntoIterator<Item = WriteAction>,
    ) -> (ControlledWriter, Arc<Mutex<Vec<u8>>>) {
        let accepted = Arc::new(Mutex::new(Vec::new()));
        (
            ControlledWriter {
                actions: actions.into_iter().collect(),
                accepted: accepted.clone(),
                shutdown_error: None,
            },
            accepted,
        )
    }

    #[test]
    fn graph_stdin_mode_rejects_other_cli_routes_before_reserving_custody() {
        let custody = ProductionTransportCustody::default();
        let connection = connection();
        assert!(
            custody
                .reserve_graph_recipe(
                    true,
                    &connection,
                    connection.executable.as_os_str(),
                    [OsStr::new("--args-json"), OsStr::new("{}")].into_iter(),
                    b"{}"
                )
                .is_err()
        );
        assert_eq!(custody.private_snapshot()["entries"], json!([]));
        drop(reserve(&custody));
        let snapshot = custody.private_snapshot();
        assert_eq!(snapshot["entries"][0]["stdin_mode"], "null");
        assert!(snapshot["entries"][0].get("stdin").is_none());
    }

    #[tokio::test]
    async fn graph_stdin_short_writes_preserve_exact_intended_and_confirmed_prefixes() {
        let custody = ProductionTransportCustody::default();
        let intended = b"{\"key\":\"original\"}";
        let reservation = reserve_graph(&custody, intended);
        let (writer, accepted) = writer([WriteAction::Accept(3), WriteAction::Accept(1)]);
        write_graph_recipe_stdin(writer, intended, Some(&reservation))
            .await
            .unwrap();
        assert_eq!(*accepted.lock().unwrap(), intended);
        let snapshot = custody.private_snapshot();
        let stdin = &snapshot["entries"][0]["stdin"];
        assert_eq!(stdin["intended"]["value"], hex::encode(intended));
        assert_eq!(
            stdin["accepted_by_pipe_prefix"]["value"],
            hex::encode(intended)
        );
        assert_eq!(
            stdin["write_return_lengths"],
            json!([3, 1, intended.len() - 4])
        );
        assert_eq!(stdin["writer_returned"], true);
        assert_eq!(stdin["shutdown_completed"], true);
        assert_eq!(stdin["child_input_consumption_observed"], false);
        assert_eq!(stdin["child_eof_read_observed"], false);
        assert_eq!(snapshot["entries"][0]["process_exit"], Value::Null);
    }

    #[tokio::test]
    async fn graph_stdin_write_error_keeps_only_confirmed_bytes_and_does_not_claim_shutdown() {
        let custody = ProductionTransportCustody::default();
        let intended = b"original-input";
        let reservation = reserve_graph(&custody, intended);
        let (writer, accepted) = writer([
            WriteAction::Accept(4),
            WriteAction::Fail(std::io::ErrorKind::BrokenPipe),
        ]);
        let error = write_graph_recipe_stdin(writer, intended, Some(&reservation))
            .await
            .unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::BrokenPipe);
        assert_eq!(*accepted.lock().unwrap(), b"orig");
        let snapshot = custody.private_snapshot();
        let stdin = &snapshot["entries"][0]["stdin"];
        assert_eq!(stdin["intended"]["value"], hex::encode(intended));
        assert_eq!(
            stdin["accepted_by_pipe_prefix"]["value"],
            hex::encode(b"orig")
        );
        assert_eq!(stdin["write_return_lengths"], json!([4]));
        assert_eq!(stdin["write_error_kind"], "BrokenPipe");
        assert_eq!(stdin["shutdown_attempted"], false);
        assert_eq!(stdin["writer_returned"], false);
        assert_eq!(stdin["writer_dropped_without_return"], false);
    }

    #[tokio::test]
    async fn graph_stdin_zero_write_is_finite_and_preserves_the_observed_zero_return() {
        let custody = ProductionTransportCustody::default();
        let intended = b"original-input";
        let reservation = reserve_graph(&custody, intended);
        let (writer, accepted) = writer([WriteAction::Accept(4), WriteAction::Accept(0)]);
        let error = write_graph_recipe_stdin(writer, intended, Some(&reservation))
            .await
            .unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::WriteZero);
        assert_eq!(*accepted.lock().unwrap(), b"orig");
        let snapshot = custody.private_snapshot();
        let stdin = &snapshot["entries"][0]["stdin"];
        assert_eq!(stdin["write_return_lengths"], json!([4, 0]));
        assert_eq!(stdin["write_error_kind"], "WriteZero");
        assert_eq!(stdin["shutdown_completed"], false);
    }

    #[tokio::test]
    async fn graph_stdin_shutdown_failure_does_not_erase_pipe_acceptance_or_claim_consumption() {
        let custody = ProductionTransportCustody::default();
        let intended = b"{\"key\":1}";
        let reservation = reserve_graph(&custody, intended);
        let (mut writer, _) = writer([]);
        writer.shutdown_error = Some(std::io::ErrorKind::BrokenPipe);
        assert!(
            write_graph_recipe_stdin(writer, intended, Some(&reservation))
                .await
                .is_err()
        );
        let snapshot = custody.private_snapshot();
        let stdin = &snapshot["entries"][0]["stdin"];
        assert_eq!(stdin["all_intended_bytes_accepted_by_pipe"], true);
        assert_eq!(stdin["shutdown_attempted"], true);
        assert_eq!(stdin["shutdown_completed"], false);
        assert_eq!(stdin["shutdown_error_kind"], "BrokenPipe");
        assert_eq!(stdin["writer_returned"], false);
        assert_eq!(stdin["child_input_consumption_observed"], false);
    }

    #[tokio::test]
    async fn graph_stdin_deadline_preserves_real_pipe_backpressure_without_a_terminal() {
        let custody = ProductionTransportCustody::default();
        let intended = b"original-input";
        let reservation = reserve_graph(&custody, intended);
        let (writer, reader) = tokio::io::duplex(3);
        assert!(
            tokio::time::timeout(
                Duration::from_millis(20),
                write_graph_recipe_stdin(writer, intended, Some(&reservation))
            )
            .await
            .is_err()
        );
        reservation.transport_deadline_elapsed();
        drop(reservation);
        let snapshot = custody.private_snapshot();
        let entry = &snapshot["entries"][0];
        assert_eq!(
            entry["stdin"]["accepted_by_pipe_prefix"]["value"],
            hex::encode(b"ori")
        );
        assert_eq!(entry["stdin"]["write_return_lengths"], json!([3]));
        assert_eq!(entry["stdin"]["writer_returned"], Value::Null);
        assert_eq!(entry["stdin"]["writer_dropped_without_return"], true);
        assert_eq!(entry["stdin"]["transport_deadline_elapsed_observed"], true);
        assert_eq!(entry["stdin"]["shutdown_completed"], false);
        assert_eq!(
            entry["transport_terminal"]["kind"],
            "future_dropped_without_return"
        );
        assert_eq!(entry["process_exit"], Value::Null);
        drop(reader);
    }

    #[tokio::test]
    async fn graph_stdin_changed_original_or_second_writer_is_rejected_before_writes() {
        let custody = ProductionTransportCustody::default();
        let intended = b"{\"key\":1}";
        let reservation = reserve_graph(&custody, intended);
        let (wrong_writer, wrong_bytes) = writer([]);
        assert!(
            write_graph_recipe_stdin(wrong_writer, b"changed", Some(&reservation))
                .await
                .is_err()
        );
        assert!(wrong_bytes.lock().unwrap().is_empty());
        assert_eq!(
            custody.private_snapshot()["entries"][0]["stdin"]["write_started"],
            false
        );
        let (first_writer, first_bytes) = writer([]);
        write_graph_recipe_stdin(first_writer, intended, Some(&reservation))
            .await
            .unwrap();
        let before = custody.private_snapshot();
        let (second_writer, second_bytes) = writer([]);
        assert!(
            write_graph_recipe_stdin(second_writer, intended, Some(&reservation))
                .await
                .is_err()
        );
        assert!(second_bytes.lock().unwrap().is_empty());
        assert_eq!(*first_bytes.lock().unwrap(), intended);
        assert_eq!(custody.private_snapshot(), before);
    }

    #[test]
    fn reservations_are_bounded_and_never_evict_an_original_attempt() {
        let custody = ProductionTransportCustody::with_limit(1);
        let reserved = reserve(&custody);
        drop(reserved);
        let error = custody
            .reserve(
                "doctor",
                false,
                &connection(),
                OsStr::new("client"),
                [OsStr::new("--args-json"), OsStr::new("{}")].into_iter(),
                b"{}",
            )
            .err()
            .unwrap();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert!(error.outcome_known);
        let snapshot = custody.private_snapshot();
        assert_eq!(snapshot["entries"].as_array().unwrap().len(), 1);
        assert_eq!(
            snapshot["entries"][0]["transport_terminal"]["kind"],
            "future_dropped_without_return"
        );
        assert_eq!(snapshot["entries"][0]["process_started"], false);
        assert_eq!(snapshot["entries"][0]["args_json"]["value"], "7b7d");
    }

    #[test]
    fn oversize_argv_is_rejected_before_reserving_a_dispatch() {
        let custody = ProductionTransportCustody::default();
        let argument = "x".repeat(MAX_CAPTURE_ARG_BYTES);
        let error = custody
            .reserve(
                "doctor",
                false,
                &connection(),
                OsStr::new("client"),
                std::iter::once(OsStr::new(&argument)),
                b"{}",
            )
            .err()
            .unwrap();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert_eq!(custody.private_snapshot()["entries"], json!([]));
    }

    #[tokio::test]
    async fn binary_streams_are_retained_without_utf8_or_json_interpretation() {
        let custody = ProductionTransportCustody::default();
        let reserved = reserve(&custody);
        let original = [0, 255, 13, 10, 0x80, b'{'];
        assert_eq!(
            read_bounded_with_capture(&original[..], Some(&reserved), Stream::Stdout)
                .await
                .unwrap(),
            original,
        );
        let snapshot = custody.private_snapshot();
        assert_eq!(
            snapshot["entries"][0]["stdout"]["original"]["value"],
            hex::encode(original)
        );
        assert_eq!(snapshot["entries"][0]["stdout"]["eof_observed"], true);
        assert_eq!(snapshot["entries"][0]["stderr"]["eof_observed"], false);
    }

    #[tokio::test]
    async fn output_limit_retains_the_same_overflow_sentinel_without_claiming_eof() {
        let original = vec![0x9a; MAX_OUTPUT_BYTES + 17];
        let custody = ProductionTransportCustody::default();
        let reserved = reserve(&custody);
        let captured = read_bounded_with_capture(&original[..], Some(&reserved), Stream::Stderr)
            .await
            .unwrap();
        assert_eq!(captured.len(), MAX_OUTPUT_BYTES + 1);
        let snapshot = custody.private_snapshot();
        assert_eq!(
            snapshot["entries"][0]["stderr"]["original"]["bytes"],
            MAX_OUTPUT_BYTES + 1
        );
        assert_eq!(
            snapshot["entries"][0]["stderr"]["capture_limit_exceeded"],
            true
        );
        assert_eq!(snapshot["entries"][0]["stderr"]["eof_observed"], false);
    }

    #[tokio::test]
    async fn cancellation_preserves_partial_bytes_without_inventing_a_terminal() {
        let custody = ProductionTransportCustody::default();
        let reserved = reserve(&custody);
        let (reader, mut writer) = tokio::io::duplex(32);
        writer.write_all(b"partial\xff").await.unwrap();
        let result = tokio::time::timeout(
            Duration::from_millis(20),
            read_bounded_with_capture(reader, Some(&reserved), Stream::Stdout),
        )
        .await;
        assert!(result.is_err());
        drop(reserved);
        drop(writer);
        let snapshot = custody.private_snapshot();
        assert_eq!(
            snapshot["entries"][0]["stdout"]["original"]["value"],
            hex::encode(b"partial\xff")
        );
        assert_eq!(snapshot["entries"][0]["stdout"]["eof_observed"], false);
        assert_eq!(
            snapshot["entries"][0]["transport_terminal"]["kind"],
            "future_dropped_without_return"
        );
        assert_eq!(snapshot["entries"][0]["process_exit"], Value::Null);
    }

    #[test]
    fn poisoning_blocks_new_dispatch_but_preserves_existing_material_and_error() {
        let custody = ProductionTransportCustody::default();
        let mut reserved = reserve(&custody);
        let copy = custody.clone();
        let _ = std::thread::spawn(move || {
            let _lock = copy.inner.lock().unwrap();
            panic!("synthetic custody poison");
        })
        .join();
        let original = Error::new(ErrorCode::Timeout, "original uncertain transport").uncertain();
        reserved.finish(&Err(original.clone()));
        let snapshot = custody.private_snapshot();
        assert_eq!(snapshot["mutex_poisoned"], true);
        assert_eq!(
            snapshot["entries"][0]["transport_terminal"]["original_error"],
            serde_json::to_value(&original).unwrap()
        );
        assert!(
            custody
                .reserve(
                    "doctor",
                    false,
                    &connection(),
                    OsStr::new("client"),
                    std::iter::empty::<&OsStr>(),
                    b"{}",
                )
                .is_err()
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn existing_transport_retains_original_unknown_envelope_and_stderr_once() {
        use super::super::tests::{fake_transport_connection, transport_reply};
        let temp = tempfile::tempdir().unwrap();
        let original = Error::new(ErrorCode::Timeout, "original native UNKNOWN").uncertain();
        let mut reply = transport_reply("doctor");
        reply.ok = false;
        reply.data = None;
        reply.error = Some(original.clone());
        let stdout = format!(" \n{}\n", serde_json::to_string(&reply).unwrap());
        let stderr = "original stderr\n";
        let (connection, counter) =
            fake_transport_connection(&temp, "project:transport".into(), &stdout, stderr, 19);
        let custody = ProductionTransportCustody::default();
        let client = super::super::ProductionClient::new(connection)
            .unwrap()
            .with_transport_custody(&custody);
        let error = client
            .execute("doctor", json!({}), false)
            .await
            .unwrap_err();
        assert_eq!(
            serde_json::to_value(error).unwrap(),
            serde_json::to_value(original).unwrap()
        );
        assert_eq!(std::fs::read(counter).unwrap(), b"invoked\n");
        let snapshot = custody.private_snapshot();
        let entry = &snapshot["entries"][0];
        assert_eq!(
            entry["stdout"]["original"]["value"],
            hex::encode(stdout.as_bytes())
        );
        assert_eq!(
            entry["stderr"]["original"]["value"],
            hex::encode(stderr.as_bytes())
        );
        assert_eq!(entry["process_exit"]["code"], 19);
        assert_eq!(
            entry["transport_terminal"]["original_error"]["outcome_known"],
            false
        );
        assert_eq!(entry["args_json"]["value"], "7b7d");
        assert_eq!(entry["argv"].as_array().unwrap().len(), 9);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn malformed_json_is_retained_before_existing_parser_rejects_it() {
        use super::super::tests::fake_transport_connection;
        let temp = tempfile::tempdir().unwrap();
        let original = " {broken original JSON\r\n";
        let (connection, counter) =
            fake_transport_connection(&temp, "project:transport".into(), original, "", 0);
        let custody = ProductionTransportCustody::default();
        let client = super::super::ProductionClient::new(connection)
            .unwrap()
            .with_transport_custody(&custody);
        let error = client
            .execute("doctor", json!({}), false)
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert_eq!(std::fs::read(counter).unwrap(), b"invoked\n");
        let snapshot = custody.private_snapshot();
        assert_eq!(
            snapshot["entries"][0]["stdout"]["original"]["value"],
            hex::encode(original.as_bytes())
        );
        assert_eq!(snapshot["entries"][0]["process_exit"]["success"], true);
        assert_eq!(
            snapshot["entries"][0]["transport_terminal"]["kind"],
            "error"
        );
    }
}
