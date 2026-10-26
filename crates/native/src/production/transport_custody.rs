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
use tokio::io::{AsyncRead, AsyncReadExt};

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
                json!({
                    "sequence":index,
                    "command":entry.command,
                    "mutation":entry.mutation,
                    "connection_identity":entry.connection_identity,
                    "executable_sha256":entry.executable_sha256,
                    "executable":entry.executable,
                    "argv":entry.argv,
                    "args_json":bytes_projection(&entry.args_json),
                    "stdin_mode":"null",
                    "process_started":entry.started,
                    "pid":entry.pid,
                    "process_exit":entry.process_exit,
                    "stdout":stream_projection(&entry.stdout),
                    "stderr":stream_projection(&entry.stderr),
                    "transport_terminal":terminal,
                    "automatic_retry":false,
                })
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
        use std::os::windows::process::ExitStatusExt;
        json!({"encoding":"windows_exit_code","value":status.into_raw()})
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
