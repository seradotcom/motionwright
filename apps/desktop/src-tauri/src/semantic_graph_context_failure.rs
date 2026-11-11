//! Closed, diagnostic-only rejection for an installed Tauri Graph context read.
//!
//! Never serialize Native messages, candidates, DTOs, paths, or transport
//! snapshots into IPC. The actual Studio decoder accepts this exact object,
//! but a diagnostic is not permission to edit or save the project.

use semwright_native_sdk::{Error as NativeError, ErrorCode};
use serde_json::{Value, json};

const SCHEMA: &str = "motionwright.native.graph-context-failure/1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GraphContextStage {
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
            Self::Admission => "ADMISSION",
            Self::Binding => "BINDING",
            Self::Custody => "CUSTODY",
            Self::Local => "LOCAL",
            Self::Transport => "TRANSPORT",
            Self::Native => "NATIVE",
        }
    }
}

/// The same exhaustive pinned SDK code vocabulary used by the original JSONL
/// adapter. A changed upstream enum must be reviewed before compilation.
fn closed_error_code(code: ErrorCode) -> &'static str {
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

/// Only a closed object may cross Tauri's error channel. No Deserialize,
/// message/string parser, acceptance flag, or implicit Native retry exists.
pub(crate) struct GraphContextFailure;

impl GraphContextFailure {
    pub(crate) fn without_code(stage: GraphContextStage) -> Value {
        json!({
            "schema": SCHEMA,
            "stage": stage.label(),
            "error_code": Value::Null,
        })
    }

    /// A returned context is not READY if its original transport is uncertain.
    /// Keep the private outcome already retained; do not grant another read or
    /// write, mint a typed ErrorCode, or retry the Native callback.
    pub(crate) fn require_confirmed_transport(transport_failed: bool) -> Result<(), Value> {
        if transport_failed {
            Err(Self::without_code(GraphContextStage::Transport))
        } else {
            Ok(())
        }
    }

    /// Transport uncertainty outranks a reported Native error. The closed
    /// ErrorCode is copied only from the actual typed callback error.
    pub(crate) fn from_report(
        error: &NativeError,
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
        json!({
            "schema": SCHEMA,
            "stage": stage.label(),
            "error_code": closed_error_code(error.code),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PRIVATE: &str = "/private/owner/original.sqlite3: secret typed SDK diagnostic";

    fn assert_closed(value: &Value, stage: &str, code: Option<&str>) {
        assert_eq!(
            value,
            &json!({
                "schema": "motionwright.native.graph-context-failure/1",
                "stage": stage,
                "error_code": code,
            })
        );
        assert_eq!(value.as_object().unwrap().len(), 3);
        assert!(!value.to_string().contains(PRIVATE));
        assert!(!value.to_string().contains("candidates"));
        assert!(!value.to_string().contains("outcome_known"));
        assert!(!value.to_string().contains("request_id"));
    }

    #[test]
    fn closed_pre_dispatch_stages_can_never_claim_a_native_error() {
        for (stage, expected) in [
            (GraphContextStage::Admission, "ADMISSION"),
            (GraphContextStage::Binding, "BINDING"),
            (GraphContextStage::Custody, "CUSTODY"),
            (GraphContextStage::Local, "LOCAL"),
        ] {
            assert_closed(&GraphContextFailure::without_code(stage), expected, None);
        }
    }

    #[test]
    fn context_success_cannot_hide_an_unconfirmed_transport() {
        assert!(GraphContextFailure::require_confirmed_transport(false).is_ok());
        let failure = GraphContextFailure::require_confirmed_transport(true).unwrap_err();
        assert_closed(&failure, "TRANSPORT", None);
    }

    #[test]
    fn typed_sdk_failure_keeps_private_details_out_of_tauri_ipc() {
        let mut error = NativeError::new(ErrorCode::BackendFailed, PRIVATE);
        error.candidates.push(PRIVATE.to_owned());
        for (native, transport, stage) in [
            (false, false, "LOCAL"),
            (true, false, "NATIVE"),
            (false, true, "TRANSPORT"),
            (true, true, "TRANSPORT"),
        ] {
            let value = GraphContextFailure::from_report(&error, native, transport);
            assert_closed(&value, stage, Some("BackendFailed"));
        }
    }

    #[test]
    fn every_pinned_error_code_has_exactly_one_closed_spelling() {
        let cases = [
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
        for (code, expected) in cases {
            let error = NativeError::new(code, PRIVATE);
            let value = GraphContextFailure::from_report(&error, true, false);
            assert_closed(&value, "NATIVE", Some(expected));
        }
    }
}
