use super::{MAX_OUTPUT_BYTES, NativeResult, Value, backend, json};
use motionwright_storage::{ProductionReceiptInput, StorageError};
use semwright_native_sdk::{Error, ErrorCode};
use sha2::{Digest, Sha256};
use std::{
    fmt::{self, Write as _},
    sync::{Arc, Mutex, MutexGuard},
};

// This is a serialized-custody budget, not a bound on Rust's resident heap.
// A native response is bounded before decoding. Six bytes per input byte
// covers JSON escaping when the retained terminal is serialized again.
const NATIVE_TERMINAL_BYTES: usize = 6 * MAX_OUTPUT_BYTES;
const DIAGNOSTIC_PREFIX_BYTES: usize = 8 * 1024;
const RECEIPT_FIXED_BYTES: usize = 16 * 1024 + 12 * DIAGNOSTIC_PREFIX_BYTES;
const MAX_CUSTODY_SLOTS: usize = 8;
const MAX_CUSTODY_BYTES: usize = 32 * 1024 * 1024;

#[derive(Clone)]
pub(super) struct ReceiptCustody {
    inner: Arc<Mutex<CustodyState>>,
}

struct CustodyState {
    slots: Vec<Option<RetainedAttempt>>,
    reserved_bytes: usize,
    byte_limit: usize,
}

struct RetainedAttempt {
    reserved_bytes: usize,
    mutation: bool,
    attempted_receipt: Option<Box<ProductionReceiptInput>>,
    storage_error: Option<Box<StorageError>>,
}

/// The reservation is acquired before dispatch. Dropping an unobserved
/// reservation releases capacity; an observed terminal remains until a
/// confirmed append. There is deliberately no automatic retry or eviction.
pub(super) struct ReceiptReservation {
    custody: ReceiptCustody,
    index: usize,
    active: bool,
}

impl Default for ReceiptCustody {
    fn default() -> Self {
        Self::with_limits(MAX_CUSTODY_SLOTS, MAX_CUSTODY_BYTES)
    }
}

impl ReceiptCustody {
    fn with_limits(slots: usize, byte_limit: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(CustodyState {
                slots: (0..slots).map(|_| None).collect(),
                reserved_bytes: 0,
                byte_limit,
            })),
        }
    }

    fn lock(&self) -> MutexGuard<'_, CustodyState> {
        // Retention and inspection must remain possible after an unrelated
        // panic. New invocations are rejected on poison below.
        self.inner.lock().unwrap_or_else(|error| error.into_inner())
    }

    pub(super) fn reserve(
        &self,
        mutation: bool,
        request_id: &str,
        command: &str,
        job_ref: Option<&str>,
    ) -> NativeResult<ReceiptReservation> {
        // These input strings are known before dispatch. Count a conservative
        // JSON bound without allocating another copy of an oversized argument.
        let metadata_bytes = request_id
            .len()
            .checked_add(command.len())
            .and_then(|bytes| bytes.checked_add(job_ref.map_or(0, str::len)))
            .and_then(|bytes| bytes.checked_mul(6))
            .and_then(|bytes| bytes.checked_add(128))
            .ok_or_else(capacity_error)?;
        let reserved_bytes = NATIVE_TERMINAL_BYTES
            .checked_add(RECEIPT_FIXED_BYTES)
            .and_then(|bytes| bytes.checked_add(metadata_bytes))
            .ok_or_else(capacity_error)?;
        let mut state = self.lock();
        if self.inner.is_poisoned() {
            return Err(backend("Production receipt custody is unavailable"));
        }
        let total = state
            .reserved_bytes
            .checked_add(reserved_bytes)
            .filter(|total| *total <= state.byte_limit)
            .ok_or_else(capacity_error)?;
        let index = state
            .slots
            .iter()
            .position(Option::is_none)
            .ok_or_else(capacity_error)?;
        state.slots[index] = Some(RetainedAttempt {
            reserved_bytes,
            mutation,
            attempted_receipt: None,
            storage_error: None,
        });
        state.reserved_bytes = total;
        Ok(ReceiptReservation {
            custody: self.clone(),
            index,
            active: true,
        })
    }

    /// Contains private native results, identifiers and storage diagnostics.
    /// Callers may place this only in their existing private custody channel.
    /// It is not a receipt, an acknowledgement, or durable recovery authority.
    pub(super) fn snapshot(&self) -> Value {
        let state = self.lock();
        let entries: Vec<Value> = state
            .slots
            .iter()
            .flatten()
            .filter_map(|entry| {
                entry.attempted_receipt.as_deref().map(|attempt| {
                    json!({
                        "persistence": "unconfirmed",
                        "mutation": entry.mutation,
                        "attempted_receipt": attempt,
                        "storage_error": entry.storage_error.as_deref().map(|error| {
                            json!({
                                "original_retained_in_memory": true,
                                "display": bounded_diagnostic(format_args!("{error}")),
                                "debug": bounded_diagnostic(format_args!("{error:?}")),
                            })
                        }),
                    })
                })
            })
            .collect();
        json!({
            "schema": "motionwright.production.private-receipt-custody/1",
            "durable": false,
            "mutex_poisoned": self.inner.is_poisoned(),
            "reserved_slots": state.slots.iter().flatten().count(),
            "reserved_serialized_bytes": state.reserved_bytes,
            "slot_limit": state.slots.len(),
            "serialized_byte_limit": state.byte_limit,
            "pending_without_terminal": state.slots.iter().flatten()
                .filter(|entry| entry.attempted_receipt.is_none()).count(),
            "entries": entries,
        })
    }
}

fn capacity_error() -> Error {
    Error::new(
        ErrorCode::ResourceExhausted,
        "Production receipt custody has no capacity for another invocation",
    )
}

impl ReceiptReservation {
    pub(super) fn observe(&mut self, receipt: ProductionReceiptInput) {
        let mut state = self.custody.lock();
        let slot = state.slots[self.index]
            .as_mut()
            .expect("live receipt custody reservation");
        assert!(
            slot.attempted_receipt.is_none(),
            "terminal already observed"
        );
        slot.attempted_receipt = Some(Box::new(receipt));
    }

    pub(super) fn attempted_receipt(&self) -> ProductionReceiptInput {
        self.custody.lock().slots[self.index]
            .as_ref()
            .and_then(|slot| slot.attempted_receipt.as_deref())
            .expect("observed receipt custody terminal")
            .clone()
    }

    pub(super) fn confirmed(mut self) {
        let mut state = self.custody.lock();
        let slot = state.slots[self.index]
            .take()
            .expect("live receipt custody reservation");
        state.reserved_bytes -= slot.reserved_bytes;
        self.active = false;
    }

    pub(super) fn failed(mut self, error: StorageError) {
        let mut state = self.custody.lock();
        let slot = state.slots[self.index]
            .as_mut()
            .expect("live receipt custody reservation");
        assert!(slot.attempted_receipt.is_some(), "terminal not observed");
        slot.storage_error = Some(Box::new(error));
        self.active = false;
    }
}

impl Drop for ReceiptReservation {
    fn drop(&mut self) {
        if !self.active {
            return;
        }
        let mut state = self.custody.lock();
        if state.slots[self.index]
            .as_ref()
            .is_some_and(|slot| slot.attempted_receipt.is_none())
        {
            let slot = state.slots[self.index]
                .take()
                .expect("live receipt custody reservation");
            state.reserved_bytes -= slot.reserved_bytes;
        }
    }
}

struct DiagnosticPrefix {
    text: String,
    bytes: usize,
    digest: Sha256,
    truncated: bool,
}

impl fmt::Write for DiagnosticPrefix {
    fn write_str(&mut self, value: &str) -> fmt::Result {
        self.bytes = self.bytes.saturating_add(value.len());
        self.digest.update(value.as_bytes());
        if !self.truncated {
            let mut end = value
                .len()
                .min(DIAGNOSTIC_PREFIX_BYTES.saturating_sub(self.text.len()));
            while !value.is_char_boundary(end) {
                end -= 1;
            }
            self.text.push_str(&value[..end]);
            self.truncated = end < value.len();
        }
        Ok(())
    }
}

fn bounded_diagnostic(args: fmt::Arguments<'_>) -> Value {
    let mut output = DiagnosticPrefix {
        text: String::new(),
        bytes: 0,
        digest: Sha256::new(),
        truncated: false,
    };
    let formatting_complete = output.write_fmt(args).is_ok();
    json!({
        "text_prefix": output.text,
        "utf8_bytes": output.bytes,
        "sha256": format!("{:x}", output.digest.finalize()),
        "truncated": output.truncated,
        "formatting_complete": formatting_complete,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    fn attempt() -> ProductionReceiptInput {
        ProductionReceiptInput {
            project_id: Uuid::nil(),
            generation: Uuid::nil(),
            revision: 1,
            request_id: "private-attempt".into(),
            request_sha256: "ab".repeat(32),
            command: "driver.blender.collection.create".into(),
            stage: "completed".into(),
            payload: json!({
                "result": {"data": {"name": "private-original"}},
                "broker_request_id": "private-broker-request",
                "job_ref": null,
            }),
        }
    }

    #[test]
    fn custody_reservation_is_shared_bounded_and_released_before_observation() {
        let custody = ReceiptCustody::with_limits(1, MAX_CUSTODY_BYTES);
        let clone = custody.clone();
        let reserved = custody.reserve(true, "request", "command", None).unwrap();
        assert_eq!(clone.snapshot()["pending_without_terminal"], 1);
        assert_eq!(
            clone
                .reserve(false, "other", "command", None)
                .err()
                .unwrap()
                .code,
            ErrorCode::ResourceExhausted
        );
        drop(reserved);
        assert_eq!(clone.snapshot()["reserved_slots"], 0);
        assert_eq!(clone.snapshot()["reserved_serialized_bytes"], 0);
        drop(clone.reserve(false, "other", "command", None).unwrap());
        let too_small = ReceiptCustody::with_limits(
            MAX_CUSTODY_SLOTS,
            NATIVE_TERMINAL_BYTES + RECEIPT_FIXED_BYTES,
        );
        let error = too_small
            .reserve(true, "request", "command", None)
            .err()
            .unwrap();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert!(error.outcome_known);
        assert_eq!(too_small.snapshot()["reserved_slots"], 0);
    }

    #[test]
    fn custody_observation_survives_guard_drop_and_confirmed_append_releases() {
        let custody = ReceiptCustody::default();
        let original = attempt();
        let mut reserved = custody.reserve(true, "request", "command", None).unwrap();
        reserved.observe(original.clone());
        assert_eq!(reserved.attempted_receipt(), original);
        drop(reserved);
        let snapshot = custody.snapshot();
        assert_eq!(snapshot["durable"], false);
        assert_eq!(snapshot["pending_without_terminal"], 0);
        assert_eq!(snapshot["entries"][0]["persistence"], "unconfirmed");
        assert_eq!(
            snapshot["entries"][0]["attempted_receipt"],
            serde_json::to_value(&original).unwrap()
        );
        assert_eq!(snapshot["entries"][0]["storage_error"], Value::Null);

        let confirmed = ReceiptCustody::default();
        let mut reserved = confirmed
            .reserve(false, "request", "command", None)
            .unwrap();
        reserved.observe(original);
        reserved.confirmed();
        assert_eq!(confirmed.snapshot()["entries"], json!([]));
        assert_eq!(confirmed.snapshot()["reserved_slots"], 0);
        assert_eq!(confirmed.snapshot()["reserved_serialized_bytes"], 0);
    }

    #[test]
    fn custody_retains_original_storage_error_with_bounded_private_projection() {
        let custody = ReceiptCustody::default();
        let message = "private-storage-".repeat(DIAGNOSTIC_PREFIX_BYTES);
        let error = StorageError::InvalidBackup(message.clone());
        let expected_display = format!("{error}");
        let expected_debug = format!("{error:?}");
        let mut reserved = custody.reserve(true, "request", "command", None).unwrap();
        reserved.observe(attempt());
        reserved.failed(error);
        {
            let state = custody.lock();
            assert!(matches!(
                state.slots[0].as_ref().unwrap().storage_error.as_deref(),
                Some(StorageError::InvalidBackup(original)) if original == &message
            ));
        }
        let snapshot = custody.snapshot();
        let projected = &snapshot["entries"][0]["storage_error"];
        assert_eq!(projected["original_retained_in_memory"], true);
        for (key, expected) in [("display", expected_display), ("debug", expected_debug)] {
            let actual = &projected[key];
            assert_eq!(actual["utf8_bytes"], expected.len());
            assert_eq!(
                actual["sha256"],
                format!("{:x}", Sha256::digest(expected.as_bytes()))
            );
            assert_eq!(actual["truncated"], true);
            assert_eq!(actual["formatting_complete"], true);
            let prefix = actual["text_prefix"].as_str().unwrap();
            assert!(expected.starts_with(prefix));
            assert!(prefix.len() <= DIAGNOSTIC_PREFIX_BYTES);
        }
    }

    #[test]
    fn custody_diagnostic_prefix_preserves_utf8_and_reports_format_failure() {
        let original = format!("{}€-later", "x".repeat(DIAGNOSTIC_PREFIX_BYTES - 1));
        let capture = bounded_diagnostic(format_args!("{original}"));
        assert_eq!(
            capture["text_prefix"],
            "x".repeat(DIAGNOSTIC_PREFIX_BYTES - 1)
        );
        assert_eq!(capture["utf8_bytes"], original.len());
        assert_eq!(
            capture["sha256"],
            format!("{:x}", Sha256::digest(original.as_bytes()))
        );
        assert_eq!(capture["truncated"], true);

        struct BrokenDisplay;
        impl fmt::Display for BrokenDisplay {
            fn fmt(&self, output: &mut fmt::Formatter<'_>) -> fmt::Result {
                output.write_str("partial")?;
                Err(fmt::Error)
            }
        }
        let broken = bounded_diagnostic(format_args!("{}", BrokenDisplay));
        assert_eq!(broken["text_prefix"], "partial");
        assert_eq!(broken["formatting_complete"], false);
    }
}
