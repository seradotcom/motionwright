//! Custody ownership and real writer-boundary failure checks only.
//! No Native readiness, Host authority or successful Graph effect is mocked.

use super::*;
use semwright_native_sdk::{Error, ErrorCode};

const REQUEST_ID: &str = "7F94073A-7C3D-4D5E-80BF-9024A804328E";

#[derive(Default)]
struct WriterState {
    bytes: Vec<u8>,
    write_calls: usize,
    sync_calls: usize,
    fail_write_at: Option<usize>,
    fail_sync: bool,
}

struct ControlledWriter(Arc<Mutex<WriterState>>);

impl JournalWriter for ControlledWriter {
    fn write_all(&mut self, bytes: &[u8]) -> io::Result<()> {
        let mut state = self.0.lock().unwrap();
        state.write_calls += 1;
        if state.fail_write_at == Some(state.write_calls) {
            let accepted = bytes.len().min(17);
            state.bytes.extend_from_slice(&bytes[..accepted]);
            return Err(io::Error::other("PRIVATE_APPEND_FAILURE"));
        }
        state.bytes.extend_from_slice(bytes);
        Ok(())
    }

    fn sync_all(&mut self) -> io::Result<()> {
        let mut state = self.0.lock().unwrap();
        state.sync_calls += 1;
        if state.fail_sync {
            return Err(io::Error::other("PRIVATE_SYNC_FAILURE"));
        }
        Ok(())
    }
}

fn journal(writer: &Arc<Mutex<WriterState>>) -> GraphExchangeJournal {
    GraphExchangeJournal {
        session: Uuid::now_v7(),
        inner: Arc::new(Mutex::new(JournalState {
            writer: Box::new(ControlledWriter(writer.clone())),
            pending: None,
            publication_error: None,
        })),
    }
}

fn unknown_return<T>() -> NativeResult<NativeGraphRecipeResult<T>> {
    Err(Error::new(ErrorCode::Timeout, "ORIGINAL_NATIVE_UNKNOWN").uncertain())
}

fn records(bytes: &[u8]) -> Vec<Value> {
    bytes
        .split(|byte| *byte == b'\n')
        .filter_map(|line| serde_json::from_slice(line).ok())
        .collect()
}

#[test]
fn graph_custody_partial_append_keeps_original_and_blocks_until_explicit_publication() {
    let writer = Arc::new(Mutex::new(WriterState {
        fail_write_at: Some(2),
        ..WriterState::default()
    }));
    let journal = journal(&writer);
    let clone = journal.clone();
    let attempt = journal.begin("upsert", Some(REQUEST_ID)).unwrap();
    let original_capture = attempt.pending.clone();
    // The actual save receipt need not implement Serialize. Only original
    // report accessors and the actual Native Error are retained by this layer.
    struct NotSerializable;
    let returned = unknown_return::<NotSerializable>();
    let original_error = serde_json::to_value(returned.as_ref().err().unwrap()).unwrap();
    assert_eq!(
        attempt.retain_result(&returned),
        GraphExchangeRetention::RetainedInMemory
    );
    let retained = journal.inner.lock().unwrap().pending.clone().unwrap();
    assert!(Arc::ptr_eq(&retained, &original_capture));
    assert!(!retained.caller_dropped.load(Ordering::Acquire));
    assert_eq!(
        retained.native_return.get().unwrap()["original_error"],
        original_error
    );
    assert!(clone.begin("recovery", Some(REQUEST_ID)).is_err());
    let partial = writer.lock().unwrap().bytes.clone();
    assert!(!partial.is_empty());
    assert!(records(&partial).is_empty());
    assert_eq!(writer.lock().unwrap().sync_calls, 0);

    writer.lock().unwrap().fail_write_at = None;
    assert_eq!(journal.publish_retained(), Ok(true));
    let published = writer.lock().unwrap().bytes.clone();
    assert!(
        published.starts_with(&partial),
        "partial evidence was overwritten"
    );
    let complete = records(&published);
    assert_eq!(complete.len(), 1);
    assert_eq!(complete[0]["request_id"], REQUEST_ID);
    assert_eq!(
        complete[0]["native_return"]["original_error"],
        original_error
    );
    assert_eq!(
        complete[0]["native_return"]["original_error"]["outcome_known"],
        false
    );
    assert_eq!(complete[0]["custody_id"], json!(retained.custody_id));
    assert!(
        complete[0]["prior_publication_error"]["private_diagnostic"]
            .as_str()
            .unwrap()
            .contains("PRIVATE_APPEND_FAILURE")
    );
    assert!(journal.inner.lock().unwrap().pending.is_none());
    assert_eq!(journal.publish_retained(), Ok(false));
    assert!(clone.begin("recovery", Some(REQUEST_ID)).is_ok());
    // Publication borrowed the Native result and never rewrote its knownness.
    assert_eq!(
        serde_json::to_value(returned.as_ref().err().unwrap()).unwrap(),
        original_error
    );
}

#[test]
fn graph_custody_sync_failure_keeps_the_same_original_even_after_full_append() {
    let writer = Arc::new(Mutex::new(WriterState {
        fail_sync: true,
        ..WriterState::default()
    }));
    let journal = journal(&writer);
    let attempt = journal.begin("remove", Some(REQUEST_ID)).unwrap();
    let retained = attempt.pending.clone();
    let returned = unknown_return::<()>();
    assert_eq!(
        attempt.retain_result(&returned),
        GraphExchangeRetention::RetainedInMemory
    );
    let first = writer.lock().unwrap().bytes.clone();
    assert_eq!(records(&first).len(), 1);
    assert_eq!(writer.lock().unwrap().sync_calls, 1);
    assert!(!retained.published.load(Ordering::Acquire));
    assert!(journal.begin("context", None).is_err());

    writer.lock().unwrap().fail_sync = false;
    assert_eq!(journal.publish_retained(), Ok(true));
    let bytes = writer.lock().unwrap().bytes.clone();
    assert!(bytes.starts_with(&first));
    let complete = records(&bytes);
    assert_eq!(complete.len(), 2);
    assert_eq!(complete[0]["custody_id"], complete[1]["custody_id"]);
    assert_eq!(complete[0]["native_return"], complete[1]["native_return"]);
    assert_eq!(complete[0]["transport"], complete[1]["transport"]);
    assert!(retained.published.load(Ordering::Acquire));
    assert!(journal.inner.lock().unwrap().pending.is_none());
}

#[test]
fn graph_custody_dropped_caller_retains_live_capture_and_no_invented_native_return() {
    let writer = Arc::new(Mutex::new(WriterState::default()));
    let journal = journal(&writer);
    let attempt = journal.begin("recovery", Some(REQUEST_ID)).unwrap();
    let original = attempt.pending.clone();
    assert!(std::ptr::eq(attempt.custody(), &original.transport));
    drop(attempt);
    let retained = journal.inner.lock().unwrap().pending.clone().unwrap();
    assert!(Arc::ptr_eq(&retained, &original));
    assert!(retained.caller_dropped.load(Ordering::Acquire));
    assert!(retained.native_return.get().is_none());
    assert_eq!(retained.request_id.as_deref(), Some(REQUEST_ID));
    assert_eq!(writer.lock().unwrap().write_calls, 0);
    assert!(journal.begin("context", None).is_err());
    // No Native call was made in this ownership fixture. A cancelled actual
    // call's live transport must pass the observation check separately below.
    assert_eq!(journal.publish_retained(), Ok(true));
    let record = records(&writer.lock().unwrap().bytes)[0].clone();
    assert_eq!(record["caller_dropped_without_return"], true);
    assert_eq!(record["native_return"], Value::Null);
    assert_eq!(record["transport"], original.transport.private_snapshot());
    assert!(journal.inner.lock().unwrap().pending.is_none());
}

#[test]
fn graph_custody_active_caller_cannot_be_cleared_by_explicit_resolution() {
    let writer = Arc::new(Mutex::new(WriterState::default()));
    let journal = journal(&writer);
    let attempt = journal.begin("context", None).unwrap();
    assert_eq!(
        journal.publish_retained(),
        Err(GraphCustodyUnavailable.to_string())
    );
    assert!(journal.begin("context", None).is_err());
    assert_eq!(writer.lock().unwrap().write_calls, 0);
    assert_eq!(
        attempt.retain_result(&unknown_return::<()>()),
        GraphExchangeRetention::Published
    );
    assert!(journal.inner.lock().unwrap().pending.is_none());
}

#[test]
fn graph_custody_observation_waits_for_every_real_transport_terminal_kind() {
    // Pure checks of the captured transport schema, not an invocation or an
    // authority fixture. An unresolved member must keep the whole capture live.
    let mut snapshot = ProductionTransportCustody::default().private_snapshot();
    assert!(transport_observation_finished(&snapshot));
    snapshot["entries"] = json!([
        {"transport_terminal": {"kind": "success"}},
        {"transport_terminal": {"kind": "pending"}},
    ]);
    assert!(!transport_observation_finished(&snapshot));
    for terminal in ["success", "error", "future_dropped_without_return"] {
        snapshot["entries"][1]["transport_terminal"]["kind"] = json!(terminal);
        assert!(transport_observation_finished(&snapshot));
    }
    for malformed in [json!("unrecognized"), Value::Null, json!(false)] {
        snapshot["entries"][1]["transport_terminal"]["kind"] = malformed;
        assert!(!transport_observation_finished(&snapshot));
    }
    snapshot["entries"][1]["transport_terminal"]["kind"] = json!("error");
    snapshot["mutex_poisoned"] = json!(true);
    assert!(!transport_observation_finished(&snapshot));
}

#[test]
fn graph_custody_real_private_file_is_never_replaced_for_same_session() {
    let temp = tempfile::tempdir().unwrap();
    let session = Uuid::now_v7();
    let journal = GraphExchangeJournal::new(temp.path(), session).unwrap();
    let attempt = journal.begin("context", None).unwrap();
    assert_eq!(
        attempt.retain_result(&unknown_return::<()>()),
        GraphExchangeRetention::Published
    );
    let path = temp.path().join(format!(
        "semantic-graph-native-{}.private.jsonl",
        session.simple()
    ));
    let original = std::fs::read(&path).unwrap();
    assert_eq!(records(&original).len(), 1);
    assert!(GraphExchangeJournal::new(temp.path(), session).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), original);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o077,
            0
        );
    }
}

#[cfg(unix)]
#[test]
fn graph_custody_publishes_real_cancelled_transport_bytes_without_claiming_process_exit() {
    use motionwright_native::production::{ProductionClient, ProductionConnection};
    use semwright_native_sdk::tokio;
    use sha2::{Digest, Sha256};
    use std::{fs, os::unix::fs::PermissionsExt, time::Duration};

    // Exercise the actual ProductionClient byte capturer with a local process
    // that emits deliberately incomplete output. It is not a Broker/Graph
    // provider and produces no provenance, Ready or native-effect success.
    let temp = tempfile::tempdir().unwrap();
    let executable = temp.path().join("partial-output-transport");
    let socket = temp.path().join("fixture.socket");
    let session = temp.path().join("fixture.session");
    let output = temp.path().join("output");
    let script = b"#!/bin/sh\nprintf '%s' 'partial-custody'\nexec sleep 5\n";
    fs::write(&executable, script).unwrap();
    fs::write(&socket, b"").unwrap();
    fs::write(&session, b"{}").unwrap();
    fs::create_dir(&output).unwrap();
    for (path, mode) in [
        (&executable, 0o700),
        (&socket, 0o600),
        (&session, 0o600),
        (&output, 0o700),
    ] {
        fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
    }
    let connection = ProductionConnection {
        schema: "motionwright-semwright-connection/1".into(),
        executable,
        executable_sha256: hex::encode(Sha256::digest(script)),
        socket,
        session_file: session,
        output_root: output,
        resource: format!("project:{}", Uuid::new_v4()),
    };
    let writer = Arc::new(Mutex::new(WriterState::default()));
    let journal = journal(&writer);
    let attempt = journal.begin("context", None).unwrap();
    let capture = attempt.custody().clone();
    let client = ProductionClient::new(connection)
        .unwrap()
        .with_transport_custody(attempt.custody());
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let transport =
            tokio::spawn(async move { client.execute("doctor", json!({}), false).await });
        let observed = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let snapshot = capture.private_snapshot();
                if snapshot.pointer("/entries/0/stdout/original/value")
                    == Some(&json!(hex::encode(b"partial-custody")))
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await;
        // Abort and await the complete outer future, not a detached reader.
        // This ensures its CaptureReservation has dropped before publication.
        transport.abort();
        let returned = transport.await;
        assert!(observed.is_ok(), "fixture bytes were not observed");
        match returned {
            Err(error) => assert!(error.is_cancelled()),
            Ok(_) => panic!("fixture completed instead of being cancelled"),
        }
    });
    let original = capture.private_snapshot();
    let entry = &original["entries"][0];
    assert_eq!(
        entry["transport_terminal"]["kind"],
        "future_dropped_without_return"
    );
    assert_eq!(entry["process_exit"], Value::Null);
    assert_eq!(entry["stdout"]["eof_observed"], false);
    assert_eq!(
        entry["stdout"]["original"]["value"],
        hex::encode(b"partial-custody")
    );
    drop(attempt);
    assert!(journal.begin("context", None).is_err());
    assert_eq!(journal.publish_retained(), Ok(true));
    let published = records(&writer.lock().unwrap().bytes)[0].clone();
    assert_eq!(published["transport"], original);
    assert_eq!(published["native_return"], Value::Null);
    assert_eq!(published["caller_dropped_without_return"], true);
    assert_eq!(published["process_cohort_closed_claimed"], false);
    assert_eq!(published["automatic_retry"], false);
}
