use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use uuid::Uuid;

const GRANT_TTL: Duration = Duration::from_secs(45);
const MAX_ACTIVE_GRANTS: usize = 128;
const MAX_SUBJECT_BYTES: usize = 2_048;
const INVALID_GRANT: &str = "Effect grant is missing, expired, or outside its exact scope.";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectKind {
    ProjectEdit,
    ImportLocal,
    RenderLocal,
    DeliverLocal,
    WorkflowMutation,
    RemoteEgress,
    UploadExternal,
    InstallRuntime,
    PublishExternal,
}

impl EffectKind {
    pub fn is_supported_local(self) -> bool {
        matches!(
            self,
            Self::ProjectEdit
                | Self::ImportLocal
                | Self::RenderLocal
                | Self::DeliverLocal
                | Self::WorkflowMutation
        )
    }

    fn requires_project_scope(self) -> bool {
        matches!(
            self,
            Self::ProjectEdit | Self::RenderLocal | Self::DeliverLocal | Self::WorkflowMutation
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EffectScope {
    pub project_id: Option<Uuid>,
    pub generation: Option<Uuid>,
    pub revision: Option<u64>,
}

impl EffectScope {
    pub fn project(project_id: Uuid, generation: Uuid, revision: u64) -> Self {
        Self {
            project_id: Some(project_id),
            generation: Some(generation),
            revision: Some(revision),
        }
    }

    pub fn unscoped_import() -> Self {
        Self {
            project_id: None,
            generation: None,
            revision: None,
        }
    }

    fn validate(self, effect: EffectKind) -> Result<(), String> {
        let complete =
            self.project_id.is_some() && self.generation.is_some() && self.revision.is_some();
        let empty =
            self.project_id.is_none() && self.generation.is_none() && self.revision.is_none();
        if !complete && !empty {
            return Err("Effect grant project scope must be complete or absent.".into());
        }
        if effect.requires_project_scope() && !complete {
            return Err("This effect requires an exact project generation and revision.".into());
        }
        if !complete && effect != EffectKind::ImportLocal {
            return Err(
                "Only local project import can be granted without an existing project.".into(),
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct EffectGrantReceipt {
    pub token: Uuid,
    pub effect: EffectKind,
    pub project_id: Option<Uuid>,
    pub generation: Option<Uuid>,
    pub revision: Option<u64>,
    pub expires_in_seconds: u64,
    pub one_time: bool,
}

#[derive(Debug)]
struct EffectGrantRecord {
    effect: EffectKind,
    scope: EffectScope,
    subject: String,
    expires_at: Instant,
}

#[derive(Clone, Default)]
pub struct EffectGrantRegistry {
    inner: Arc<Mutex<BTreeMap<Uuid, EffectGrantRecord>>>,
}

fn validate_subject(subject: &str) -> Result<(), String> {
    if subject.trim().is_empty()
        || subject.len() > MAX_SUBJECT_BYTES
        || subject.chars().any(char::is_control)
    {
        return Err("Effect grant subject is empty or out of bounds.".into());
    }
    Ok(())
}

impl EffectGrantRegistry {
    pub fn issue(
        &self,
        effect: EffectKind,
        scope: EffectScope,
        subject: &str,
    ) -> Result<EffectGrantReceipt, String> {
        self.issue_until(effect, scope, subject, Instant::now() + GRANT_TTL)
    }

    fn issue_until(
        &self,
        effect: EffectKind,
        scope: EffectScope,
        subject: &str,
        expires_at: Instant,
    ) -> Result<EffectGrantReceipt, String> {
        if !effect.is_supported_local() {
            return Err(
                "Motionwright does not expose grants for remote egress, upload, runtime installation, or publication."
                    .into(),
            );
        }
        scope.validate(effect)?;
        validate_subject(subject)?;

        let now = Instant::now();
        let mut grants = self
            .inner
            .lock()
            .map_err(|_| "Effect grant registry is unavailable.".to_string())?;
        grants.retain(|_, record| record.expires_at > now);
        if grants.len() >= MAX_ACTIVE_GRANTS {
            return Err("Too many active effect grants; retry after current grants expire.".into());
        }

        let token = Uuid::now_v7();
        grants.insert(
            token,
            EffectGrantRecord {
                effect,
                scope,
                subject: subject.to_owned(),
                expires_at,
            },
        );
        Ok(EffectGrantReceipt {
            token,
            effect,
            project_id: scope.project_id,
            generation: scope.generation,
            revision: scope.revision,
            expires_in_seconds: GRANT_TTL.as_secs(),
            one_time: true,
        })
    }

    pub fn consume(
        &self,
        token: Uuid,
        effect: EffectKind,
        scope: EffectScope,
        subject: &str,
    ) -> Result<(), String> {
        scope
            .validate(effect)
            .map_err(|_| INVALID_GRANT.to_string())?;
        validate_subject(subject).map_err(|_| INVALID_GRANT.to_string())?;

        let mut grants = self
            .inner
            .lock()
            .map_err(|_| "Effect grant registry is unavailable.".to_string())?;
        let Some(record) = grants.remove(&token) else {
            return Err(INVALID_GRANT.into());
        };
        if record.expires_at <= Instant::now()
            || record.effect != effect
            || record.scope != scope
            || record.subject != subject
        {
            return Err(INVALID_GRANT.into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scope() -> EffectScope {
        EffectScope::project(Uuid::now_v7(), Uuid::now_v7(), 7)
    }

    #[test]
    fn exact_grant_is_one_time() {
        let registry = EffectGrantRegistry::default();
        let scope = scope();
        let grant = registry
            .issue(EffectKind::ProjectEdit, scope, "request:edit-1")
            .unwrap();

        registry
            .consume(
                grant.token,
                EffectKind::ProjectEdit,
                scope,
                "request:edit-1",
            )
            .unwrap();
        assert!(
            registry
                .consume(
                    grant.token,
                    EffectKind::ProjectEdit,
                    scope,
                    "request:edit-1",
                )
                .is_err()
        );
    }

    #[test]
    fn grant_never_crosses_effect_boundaries_and_mismatch_consumes_it() {
        let registry = EffectGrantRegistry::default();
        let scope = scope();
        let grant = registry
            .issue(EffectKind::ProjectEdit, scope, "request:edit-2")
            .unwrap();

        assert!(
            registry
                .consume(
                    grant.token,
                    EffectKind::RenderLocal,
                    scope,
                    "request:edit-2",
                )
                .is_err()
        );
        assert!(
            registry
                .consume(
                    grant.token,
                    EffectKind::ProjectEdit,
                    scope,
                    "request:edit-2",
                )
                .is_err()
        );
    }

    #[test]
    fn project_generation_revision_and_subject_are_exact() {
        let registry = EffectGrantRegistry::default();
        let scope = scope();
        let grant = registry
            .issue(EffectKind::DeliverLocal, scope, "/tmp/master.otio")
            .unwrap();
        let changed_revision = EffectScope::project(
            scope.project_id.unwrap(),
            scope.generation.unwrap(),
            scope.revision.unwrap() + 1,
        );

        assert!(
            registry
                .consume(
                    grant.token,
                    EffectKind::DeliverLocal,
                    changed_revision,
                    "/tmp/master.otio",
                )
                .is_err()
        );

        let grant = registry
            .issue(EffectKind::DeliverLocal, scope, "/tmp/master.otio")
            .unwrap();
        assert!(
            registry
                .consume(
                    grant.token,
                    EffectKind::DeliverLocal,
                    scope,
                    "/tmp/other.otio",
                )
                .is_err()
        );
    }

    #[test]
    fn expired_grant_fails_closed_without_sleeping() {
        let registry = EffectGrantRegistry::default();
        let scope = scope();
        let grant = registry
            .issue_until(EffectKind::RenderLocal, scope, "render:job", Instant::now())
            .unwrap();

        assert!(
            registry
                .consume(grant.token, EffectKind::RenderLocal, scope, "render:job",)
                .is_err()
        );
    }

    #[test]
    fn unsupported_external_effects_cannot_be_granted() {
        let registry = EffectGrantRegistry::default();
        let scope = scope();
        for effect in [
            EffectKind::RemoteEgress,
            EffectKind::UploadExternal,
            EffectKind::InstallRuntime,
            EffectKind::PublishExternal,
        ] {
            let error = registry.issue(effect, scope, "external").unwrap_err();
            assert!(error.contains("does not expose grants"));
        }
    }

    #[test]
    fn project_import_is_the_only_supported_unscoped_grant() {
        let registry = EffectGrantRegistry::default();
        let unscoped = EffectScope::unscoped_import();
        let grant = registry
            .issue(
                EffectKind::ImportLocal,
                unscoped,
                "/tmp/project.motionwright",
            )
            .unwrap();
        registry
            .consume(
                grant.token,
                EffectKind::ImportLocal,
                unscoped,
                "/tmp/project.motionwright",
            )
            .unwrap();

        assert!(
            registry
                .issue(EffectKind::ProjectEdit, unscoped, "edit")
                .is_err()
        );
    }
}
