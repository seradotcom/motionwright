use crate::{DomainError, Result};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExternalSystem {
    Launchwright,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HandoffDirection {
    ContextInput,
    ContextOutput,
    ArtifactOutput,
    EvidenceOutput,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExternalResourceKind {
    Release,
    Target,
    Context,
    Artifact,
    Evidence,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HandoffBinding {
    pub id: Uuid,
    pub system: ExternalSystem,
    pub direction: HandoffDirection,
    pub external_kind: ExternalResourceKind,
    pub external_id: String,
    pub external_revision: Option<String>,
    pub local_resource: String,
    pub artifact_sha256: Option<String>,
}

impl HandoffBinding {
    pub fn validate(&self) -> Result<()> {
        bounded_key(&self.external_id, 256, "external resource id")?;
        bounded_key(&self.local_resource, 512, "local resource")?;
        if self.external_revision.as_ref().is_some_and(|value| {
            value.trim().is_empty() || value.len() > 256 || value.chars().any(char::is_control)
        }) {
            return Err(DomainError::Invalid("external revision is invalid".into()));
        }
        if self
            .artifact_sha256
            .as_ref()
            .is_some_and(|digest| !sha256(digest))
        {
            return Err(DomainError::Invalid(
                "handoff artifact digest is invalid".into(),
            ));
        }

        let needs_artifact = matches!(
            self.direction,
            HandoffDirection::ArtifactOutput | HandoffDirection::EvidenceOutput
        );
        if needs_artifact != self.artifact_sha256.is_some() {
            return Err(DomainError::Invalid(
                "artifact/evidence outputs require an exact digest; context bindings do not carry one"
                    .into(),
            ));
        }
        Ok(())
    }
}

fn bounded_key(value: &str, max: usize, label: &str) -> Result<()> {
    if value.trim().is_empty() || value.len() > max || value.chars().any(char::is_control) {
        return Err(DomainError::Invalid(format!("{label} is invalid")));
    }
    Ok(())
}

fn sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_bindings_require_exact_artifact_identity() {
        let binding = HandoffBinding {
            id: Uuid::now_v7(),
            system: ExternalSystem::Launchwright,
            direction: HandoffDirection::ArtifactOutput,
            external_kind: ExternalResourceKind::Artifact,
            external_id: "artifact-public-ref".into(),
            external_revision: Some("r17".into()),
            local_resource: "project:018f0000-0000-7000-8000-000000000001".into(),
            artifact_sha256: None,
        };
        assert!(binding.validate().is_err());

        let binding = HandoffBinding {
            artifact_sha256: Some("cd".repeat(32)),
            ..binding
        };
        binding.validate().unwrap();
    }

    #[test]
    fn context_input_never_smuggles_artifact_bytes_or_identity() {
        let binding = HandoffBinding {
            id: Uuid::now_v7(),
            system: ExternalSystem::Launchwright,
            direction: HandoffDirection::ContextInput,
            external_kind: ExternalResourceKind::Release,
            external_id: "release-public-ref".into(),
            external_revision: Some("r9".into()),
            local_resource: "project:018f0000-0000-7000-8000-000000000001".into(),
            artifact_sha256: Some("ab".repeat(32)),
        };
        assert!(binding.validate().is_err());
    }
}
