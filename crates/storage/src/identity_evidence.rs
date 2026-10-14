//! Admission of exact imported JSON source/consent bytes for identity origins.
//! Content correspondence is NOT identity authentication or an execution grant.
//! Caller must have already passed receipt replay and project CAS authorization.
use super::{Result, StorageError, read_verified_blob_from_root};
use motionwright_domain::{
    DomainError, IdentityModality, IdentityOrigin, IdentityOriginRecord, IdentityPurpose, Project,
};
use serde::Deserialize;
use std::path::Path;
use uuid::Uuid;

const SOURCE_SCHEMA: &str = "motionwright.identity-origin-evidence/1";
const CONSENT_SCHEMA: &str = "motionwright.identity-consent-declaration/1";
const SOURCE_LIMIT: u64 = 64 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceDeclaration {
    schema: String,
    id: Uuid,
    source_project_id: Uuid,
    source_generation: Uuid,
    origin: IdentityOrigin,
    display_label: String,
    origin_description: String,
    created_by_declaration: String,
    created_at_utc: String,
    expires_at_utc: String,
    allowed_modalities: Vec<IdentityModality>,
    allowed_purposes: Vec<IdentityPurpose>,
    consent_sha256: Option<String>,
    synthetic_disclosure: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ConsentDeclaration {
    schema: String,
    identity_id: Uuid,
    subject_declaration: String,
    scope_declaration: String,
    created_at_utc: String,
    expires_at_utc: String,
    allowed_modalities: Vec<IdentityModality>,
    allowed_purposes: Vec<IdentityPurpose>,
}

fn rejected(message: &str) -> StorageError {
    StorageError::Domain(DomainError::Invalid(format!(
        "identity source admission: {message}"
    )))
}
fn json_has_exact_keys(raw: &[u8], required: &[&str]) -> bool {
    serde_json::from_slice::<serde_json::Value>(raw)
        .ok()
        .and_then(|json| {
            json.as_object().map(|map| {
                map.len() == required.len() && required.iter().all(|key| map.contains_key(*key))
            })
        })
        .unwrap_or(false)
}

pub(super) fn verify_identity_source(
    root: &Path,
    project: &Project,
    source: &IdentityOriginRecord,
) -> Result<()> {
    // Before opening either blob, refuse unauthorised foreign/stale project
    // references; an attacker must not probe digest existence through errors.
    source.validate(project)?;
    if source.source_generation != project.generation {
        return Err(rejected(
            "new identity origin is not from this project generation",
        ));
    }
    let provenance = read_verified_blob_from_root(root, &source.provenance_sha256, SOURCE_LIMIT)?;
    if !json_has_exact_keys(
        &provenance,
        &[
            "schema",
            "id",
            "source_project_id",
            "source_generation",
            "origin",
            "display_label",
            "origin_description",
            "created_by_declaration",
            "created_at_utc",
            "expires_at_utc",
            "allowed_modalities",
            "allowed_purposes",
            "consent_sha256",
            "synthetic_disclosure",
        ],
    ) {
        return Err(rejected(
            "imported identity provenance JSON does not follow the exact schema",
        ));
    }
    let manifest: SourceDeclaration = serde_json::from_slice(&provenance)
        .map_err(|_| rejected("imported identity provenance has malformed fields"))?;
    if manifest.schema != SOURCE_SCHEMA
        || manifest.id != source.id
        || manifest.source_project_id != source.source_project_id
        || manifest.source_generation != source.source_generation
        || manifest.origin != source.origin
        || manifest.display_label != source.display_label
        || manifest.origin_description != source.origin_description
        || manifest.created_by_declaration != source.created_by_declaration
        || manifest.created_at_utc != source.created_at_utc
        || manifest.expires_at_utc != source.expires_at_utc
        || manifest.allowed_modalities != source.allowed_modalities
        || manifest.allowed_purposes != source.allowed_purposes
        || manifest.consent_sha256 != source.consent_sha256
        || manifest.synthetic_disclosure != source.synthetic_disclosure
    {
        return Err(rejected(
            "imported identity bytes differ from the declared origin, use, or disclosure",
        ));
    }
    if source.origin == IdentityOrigin::HumanLikeness {
        let digest = source
            .consent_sha256
            .as_deref()
            .ok_or_else(|| rejected("human likeness consent digest is missing"))?;
        let bytes = read_verified_blob_from_root(root, digest, SOURCE_LIMIT)?;
        if !json_has_exact_keys(
            &bytes,
            &[
                "schema",
                "identity_id",
                "subject_declaration",
                "scope_declaration",
                "created_at_utc",
                "expires_at_utc",
                "allowed_modalities",
                "allowed_purposes",
            ],
        ) {
            return Err(rejected(
                "human consent JSON is not the required bounded source declaration",
            ));
        }
        let consent: ConsentDeclaration = serde_json::from_slice(&bytes)
            .map_err(|_| rejected("human consent source has malformed fields"))?;
        if consent.schema != CONSENT_SCHEMA
            || consent.identity_id != source.id
            || source.consent_subject_declaration.as_deref()
                != Some(consent.subject_declaration.as_str())
            || source.consent_scope_declaration.as_deref()
                != Some(consent.scope_declaration.as_str())
            || consent.created_at_utc != source.created_at_utc
            || consent.expires_at_utc != source.expires_at_utc
            || consent.allowed_modalities != source.allowed_modalities
            || consent.allowed_purposes != source.allowed_purposes
        {
            return Err(rejected(
                "human consent bytes contradict the declared subject, scope, time or source",
            ));
        }
    }
    // Identical bytes/metadata do not establish that a signature is genuine.
    // No media generation operation is exposed by this contract.
    Ok(())
}
