//! Closed public error for the original Graph mutation invocation only.
//!
//! Include this same module from the Native-owned Session adapter with #[path].
//! It creates no authority, parses no diagnostics, and performs no dispatch.
//! Recovery-read failures must remain ordinary read errors, not this DTO.

use semwright_native_sdk::Error as NativeError;
use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

const SCHEMA: &str = "motionwright.graph-mutation-error/1";
const CODE: &str = "graph_mutation_failed";

/// Invalid input has no matching typed mutation error. Its fixed message may
/// be returned as an ordinary string; the original input is never reflected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct InvalidGraphRequestId;

impl std::fmt::Display for InvalidGraphRequestId {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Graph mutation request identity is invalid")
    }
}

impl std::error::Error for InvalidGraphRequestId {}

/// The four fields are private and only constructed from validated input and
/// the actual dispatch boundary. There is deliberately no Deserialize or
/// generic conversion from an error message, code, JSON value or read failure.
#[derive(Debug, Clone, Serialize)]
pub(crate) struct GraphInvokeFailure {
    schema: &'static str,
    request_id: String,
    outcome_known: bool,
    code: &'static str,
}

impl GraphInvokeFailure {
    /// Validate before dispatch. Preserve the caller's exact 36-byte spelling,
    /// including case; Uuid parsing is validation, never key normalization.
    pub(crate) fn validate_request_id(request_id: &str) -> Result<(), InvalidGraphRequestId> {
        if request_id.len() != 36
            || !request_id.bytes().enumerate().all(|(index, byte)| {
                if matches!(index, 8 | 13 | 18 | 23) {
                    byte == b'-'
                } else {
                    byte.is_ascii_hexdigit()
                }
            })
        {
            return Err(InvalidGraphRequestId);
        }
        let parsed = Uuid::parse_str(request_id).map_err(|_| InvalidGraphRequestId)?;
        if parsed.is_nil() {
            return Err(InvalidGraphRequestId);
        }
        Ok(())
    }

    /// Use only for a certain rejection before any mutating Native call.
    /// A failed recovery read or a later local failure does not qualify.
    pub(crate) fn before_dispatch(request_id: &str) -> Result<Self, InvalidGraphRequestId> {
        Self::new(request_id, true)
    }

    /// Use only with the original error from the actual mutating Native call.
    /// Copy its knownness unchanged; never derive it from code/message or use
    /// an error from later receipt decoding, custody, callbacks or recovery.
    pub(crate) fn from_native(
        request_id: &str,
        error: &NativeError,
    ) -> Result<Self, InvalidGraphRequestId> {
        Self::new(request_id, error.outcome_known)
    }

    /// Use after dispatch when no original Native error establishes knownness,
    /// including reply decoding or custody failure. Never infer non-application.
    pub(crate) fn unknown(request_id: &str) -> Result<Self, InvalidGraphRequestId> {
        Self::new(request_id, false)
    }

    fn new(request_id: &str, outcome_known: bool) -> Result<Self, InvalidGraphRequestId> {
        Self::validate_request_id(request_id)?;
        Ok(Self {
            schema: SCHEMA,
            request_id: request_id.to_owned(),
            outcome_known,
            code: CODE,
        })
    }

    /// Return this object through IPC. The Studio decoder deliberately rejects
    /// JSON strings, Error.message and arbitrary diagnostic text.
    pub(crate) fn to_value(&self) -> Value {
        json!({
            "schema": self.schema,
            "request_id": self.request_id,
            "outcome_known": self.outcome_known,
            "code": self.code,
        })
    }
}

// A clean serialization for a string sink. IPC must use to_value() instead:
// Studio deliberately does not parse an error's Display/message as JSON.
impl std::fmt::Display for GraphInvokeFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.to_value().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use semwright_native_sdk::ErrorCode;

    const ORIGINAL_KEY: &str = "ABCDEF12-3456-4789-aBcD-EF1234567890";
    const PRIVATE: &str = "/private/operator/source.sqlite3: original SDK diagnostic";

    fn expected(request_id: &str, outcome_known: bool) -> Value {
        json!({
            "schema": "motionwright.graph-mutation-error/1",
            "request_id": request_id,
            "outcome_known": outcome_known,
            "code": "graph_mutation_failed",
        })
    }

    fn assert_closed(failure: &GraphInvokeFailure, request_id: &str, outcome_known: bool) {
        let value = failure.to_value();
        assert_eq!(value.as_object().unwrap().len(), 4);
        assert_eq!(value, expected(request_id, outcome_known));
        assert_eq!(serde_json::to_value(failure).unwrap(), value);
        assert_eq!(
            serde_json::from_str::<Value>(&failure.to_string()).unwrap(),
            value
        );
        assert!(!failure.to_string().contains(PRIVATE));
    }

    #[test]
    fn before_dispatch_has_closed_shape_and_preserves_original_case() {
        let failure = GraphInvokeFailure::before_dispatch(ORIGINAL_KEY).unwrap();
        assert_closed(&failure, ORIGINAL_KEY, true);
        assert_ne!(
            failure.to_value()["request_id"],
            ORIGINAL_KEY.to_ascii_lowercase()
        );
    }

    #[test]
    fn native_knownness_is_copied_without_exposing_private_error_fields() {
        for outcome_known in [true, false] {
            let mut error =
                NativeError::new(ErrorCode::BackendFailed, PRIVATE).recipe_progress(1, PRIVATE);
            error.candidates.push(PRIVATE.to_owned());
            error.outcome_known = outcome_known;
            let failure = GraphInvokeFailure::from_native(ORIGINAL_KEY, &error).unwrap();
            assert_closed(&failure, ORIGINAL_KEY, outcome_known);
            assert_eq!(error.message, PRIVATE);
            assert_eq!(error.candidates, vec![PRIVATE.to_owned()]);
            assert_eq!(error.outcome_known, outcome_known);
        }
    }

    #[test]
    fn native_error_code_does_not_override_original_knownness() {
        let known_timeout = NativeError::new(ErrorCode::Timeout, PRIVATE);
        let unknown_argument = NativeError::invalid(PRIVATE).uncertain();
        assert_closed(
            &GraphInvokeFailure::from_native(ORIGINAL_KEY, &known_timeout).unwrap(),
            ORIGINAL_KEY,
            true,
        );
        assert_closed(
            &GraphInvokeFailure::from_native(ORIGINAL_KEY, &unknown_argument).unwrap(),
            ORIGINAL_KEY,
            false,
        );
    }

    #[test]
    fn post_dispatch_failure_is_unknown_without_changing_the_key() {
        assert_closed(
            &GraphInvokeFailure::unknown(ORIGINAL_KEY).unwrap(),
            ORIGINAL_KEY,
            false,
        );
    }

    #[test]
    fn equal_uuid_values_keep_their_distinct_original_spelling() {
        let lower = ORIGINAL_KEY.to_ascii_lowercase();
        assert_eq!(
            Uuid::parse_str(ORIGINAL_KEY).unwrap(),
            Uuid::parse_str(&lower).unwrap()
        );
        let original = GraphInvokeFailure::unknown(ORIGINAL_KEY)
            .unwrap()
            .to_value();
        let lowercase = GraphInvokeFailure::unknown(&lower).unwrap().to_value();
        assert_ne!(original["request_id"], lowercase["request_id"]);
        assert_eq!(original["request_id"], ORIGINAL_KEY);
        assert_eq!(lowercase["request_id"], lower);
    }

    #[test]
    fn invalid_keys_never_emit_a_typed_error_or_reflect_private_input() {
        let native_error = NativeError::new(ErrorCode::BackendFailed, PRIVATE);
        let invalid = [
            String::new(),
            "00000000-0000-0000-0000-000000000000".to_owned(),
            ORIGINAL_KEY.replace('-', ""),
            format!("{{{ORIGINAL_KEY}}}"),
            format!("urn:uuid:{ORIGINAL_KEY}"),
            format!(" {ORIGINAL_KEY}"),
            format!("{ORIGINAL_KEY}\n"),
            ORIGINAL_KEY.replacen('-', "_", 1),
            ORIGINAL_KEY.replacen('A', "G", 1),
            ORIGINAL_KEY.replacen("AB", "é", 1),
            PRIVATE.to_owned(),
            format!(r#"{{"request_id":"{ORIGINAL_KEY}","message":"{PRIVATE}"}}"#),
        ];
        for key in invalid {
            assert!(GraphInvokeFailure::validate_request_id(&key).is_err());
            for rejected in [
                GraphInvokeFailure::before_dispatch(&key),
                GraphInvokeFailure::from_native(&key, &native_error),
                GraphInvokeFailure::unknown(&key),
            ] {
                let error = rejected.unwrap_err();
                assert_eq!(
                    error.to_string(),
                    "Graph mutation request identity is invalid"
                );
                let ordinary = Value::String(error.to_string());
                assert!(ordinary.is_string());
                assert!(ordinary.get("schema").is_none());
                assert!(!ordinary.to_string().contains(PRIVATE));
            }
        }
    }
}
