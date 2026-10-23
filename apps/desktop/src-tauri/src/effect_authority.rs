//! Service-backed effect-grant commands shared by the desktop entry point.
//!
//! The project is read from durable storage before its current scope is used.
//! Creative approval is document content and supplies no outgoing authority.

use super::effect_grants::{EffectGrantReceipt, EffectGrantRegistry, EffectKind, EffectScope};
use super::local_error::sanitized;
use super::project_library;
use motionwright_service::StudioService;
use serde::Deserialize;
use uuid::Uuid;

#[derive(Debug, Deserialize)]
pub struct IssueEffectGrantRequest {
    pub effect: EffectKind,
    pub project_id: Option<Uuid>,
    pub generation: Option<Uuid>,
    pub revision: Option<u64>,
    pub subject: String,
}

pub fn checked_effect_scope(
    service: &StudioService,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
) -> Result<EffectScope, String> {
    let project =
        project_library::open_project(service, project_library::OpenProjectRequest { project_id })
            .map_err(sanitized)?;
    if project.generation != generation || project.revision != revision {
        return Err("Effect grant scope is stale; refresh the project before retrying.".into());
    }
    Ok(EffectScope::project(project_id, generation, revision))
}

pub fn issue_effect_grant(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: IssueEffectGrantRequest,
) -> Result<EffectGrantReceipt, String> {
    if request.effect == EffectKind::ProjectCreate {
        return Err(
            "Use the dedicated project creation command with its exact retained intent.".into(),
        );
    }
    let scope = match (request.project_id, request.generation, request.revision) {
        (Some(project_id), Some(generation), Some(revision)) => {
            checked_effect_scope(service, project_id, generation, revision)?
        }
        (None, None, None) => EffectScope::unscoped_import(),
        _ => return Err("Effect grant project scope must be complete or absent.".into()),
    };
    grants.issue(request.effect, scope, request.subject.trim())
}
