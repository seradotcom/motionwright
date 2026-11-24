//! Explicit local runtime installation. Native provider execution remains a
//! separate owner/Host/Broker decision. Unsupported targets remain read-only.
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallRequest {
    pub scope: Scope,
    pub plan_id: Uuid,
    pub manifest_sha256: String,
    #[cfg_attr(
        not(all(target_os = "linux", target_arch = "x86_64")),
        expect(
            dead_code,
            reason = "Unsupported targets keep the closed wire shape and reject before effects"
        )
    )]
    pub effect_grant: Uuid,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
#[cfg_attr(
    not(all(target_os = "linux", target_arch = "x86_64")),
    expect(
        dead_code,
        reason = "Unsupported targets retain the closed query shape and perform no reads"
    )
)]
pub struct ReadRequest {
    pub scope: Scope,
    pub install_id: Uuid,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RollbackRequest {
    pub scope: Scope,
    pub install_id: Uuid,
    pub manifest_sha256: String,
    #[cfg_attr(
        not(all(target_os = "linux", target_arch = "x86_64")),
        expect(
            dead_code,
            reason = "Unsupported targets keep the closed wire shape and reject before effects"
        )
    )]
    pub effect_grant: Uuid,
}
#[derive(Debug, Clone, Serialize)]
pub struct PlanView {
    pub schema: &'static str,
    pub scope: Scope,
    pub plan_id: Uuid,
    pub profile: String,
    pub label: String,
    pub version: String,
    pub platform: String,
    pub manifest_sha256: String,
    pub total_bytes: u64,
    pub required_free_bytes: u64,
    pub available_bytes: u64,
    pub file_count: usize,
    pub destination: String,
    pub node_path: String,
    pub entry_path: String,
    pub lock_path: String,
    pub lock_sha256: String,
    pub declared_node_version: String,
    pub effect_subject: String,
    pub expires_in_seconds: u64,
    pub execution_performed: bool,
}
#[derive(Debug, Clone, Serialize)]
pub struct InstallView {
    pub schema: &'static str,
    pub scope: Scope,
    pub install_id: Uuid,
    pub profile: String,
    pub label: String,
    pub version: String,
    pub platform: String,
    pub manifest_sha256: String,
    pub total_bytes: u64,
    pub file_count: usize,
    pub destination: String,
    pub disabled_destination: String,
    pub node_path: String,
    pub entry_path: String,
    pub lock_path: String,
    pub lock_sha256: String,
    pub state: &'static str,
    pub content_verified: bool,
    pub rollback_available: bool,
    pub rollback_subject: String,
    pub execution_performed: bool,
    pub automatic_retry: bool,
}
#[derive(Debug, Serialize)]
pub struct Inventory {
    pub schema: &'static str,
    pub scope: Scope,
    pub supported: bool,
    pub platform: &'static str,
    pub profiles: Vec<InstallView>,
    pub alternatives: &'static str,
}
#[derive(Debug, Clone, Serialize)]
pub struct RuntimeFailure {
    pub schema: &'static str,
    pub operation: &'static str,
    pub scope: Box<Scope>,
    pub install_id: Uuid,
    pub manifest_sha256: String,
    pub certainty: &'static str,
    pub message: String,
}
impl RuntimeFailure {
    pub fn install(request: &InstallRequest, message: &str) -> Self {
        Self {
            schema: "motionwright.runtime-profile-failure/1",
            operation: "install",
            scope: Box::new(request.scope),
            install_id: request.plan_id,
            manifest_sha256: request.manifest_sha256.clone(),
            certainty: "outcome_unknown",
            message: message.into(),
        }
    }
    pub fn rollback(request: &RollbackRequest, message: &str) -> Self {
        Self {
            schema: "motionwright.runtime-profile-failure/1",
            operation: "rollback",
            scope: Box::new(request.scope),
            install_id: request.install_id,
            manifest_sha256: request.manifest_sha256.clone(),
            certainty: "outcome_unknown",
            message: message.into(),
        }
    }
}
pub const ALTERNATIVES: &str = "Continue local editing, saving and portable source export without a renderer, or select an existing owner-provisioned Semwright connection. Installed profile bytes do not configure or authorize a Native provider.";
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod linux;
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
pub use linux::RuntimeProfiles;
#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
mod unsupported;
#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
pub use unsupported::RuntimeProfiles;
