use motionwright_domain::{
    DataClass, DomainError, InvocationBudget, InvocationOutcome, ModelInvocationReceipt, Project,
};
use motionwright_storage::{StorageError, Store};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use uuid::Uuid;

const PREFLIGHT_SCHEMA: &str = "motionwright-model-preflight/1";
const MAX_CONTEXT_ITEMS: usize = 64;
const MAX_PREVIEW_CHARS: usize = 640;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelProviderKind {
    Manual,
    ExternalAgent,
    Local,
    Remote,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelRequestDraft {
    pub provider_kind: ModelProviderKind,
    pub provider: String,
    pub model: String,
    pub resource_refs: Vec<String>,
    pub data_classes: Vec<DataClass>,
    pub budget: InvocationBudget,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelContextDisclosure {
    pub resource_ref: String,
    pub data_class: DataClass,
    pub label: String,
    pub media_type: Option<String>,
    pub estimated_bytes: u64,
    pub content_sha256: Option<String>,
    pub preview: Option<String>,
    pub preview_truncated: bool,
    pub untrusted_data: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelRequestPreflight {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub base_revision: u64,
    pub provider_kind: ModelProviderKind,
    pub provider: String,
    pub model: String,
    pub resource_refs: Vec<String>,
    pub data_classes: Vec<DataClass>,
    pub budget: InvocationBudget,
    pub disclosures: Vec<ModelContextDisclosure>,
    pub estimated_total_bytes: u64,
    pub source_data_classes: Vec<DataClass>,
    pub explicit_source_consent_required: bool,
    pub fallback_provider: Option<String>,
    pub studio_network_dispatch_supported: bool,
    pub network_dispatched: bool,
    pub fingerprint_sha256: String,
}

fn invalid(message: impl Into<String>) -> StorageError {
    DomainError::Invalid(message.into()).into()
}

fn validate_identity(value: &str, field: &str) -> Result<(), StorageError> {
    if value.trim().is_empty() || value.len() > 256 || value.chars().any(char::is_control) {
        return Err(invalid(format!("{field} is empty or out of bounds")));
    }
    Ok(())
}

fn data_class_name(class: &DataClass) -> &'static str {
    match class {
        DataClass::Metadata => "metadata",
        DataClass::Text => "text",
        DataClass::Frame => "frame",
        DataClass::Audio => "audio",
        DataClass::SourceCode => "source_code",
    }
}

fn normalize_classes(classes: &[DataClass]) -> Result<Vec<DataClass>, StorageError> {
    if classes.is_empty() || classes.len() > 8 {
        return Err(invalid(
            "model request data classes are empty or out of bounds",
        ));
    }
    let mut names = BTreeSet::new();
    for class in classes {
        if !names.insert(data_class_name(class)) {
            return Err(invalid("model request data classes contain duplicates"));
        }
    }
    let mut normalized = classes.to_vec();
    normalized.sort_by(|left, right| data_class_name(left).cmp(data_class_name(right)));
    Ok(normalized)
}

fn normalize_refs(refs: &[String]) -> Result<Vec<String>, StorageError> {
    if refs.is_empty() || refs.len() > MAX_CONTEXT_ITEMS {
        return Err(invalid(
            "model request resource scope is empty or too large",
        ));
    }
    let mut normalized = BTreeSet::new();
    for resource in refs {
        if resource.trim().is_empty()
            || resource.len() > 512
            || resource.chars().any(char::is_control)
            || !normalized.insert(resource.clone())
        {
            return Err(invalid(
                "model request resource scope contains an invalid reference",
            ));
        }
    }
    Ok(normalized.into_iter().collect())
}

fn bounded_preview(value: &str) -> (Option<String>, bool) {
    let mut chars = value.chars();
    let preview: String = chars.by_ref().take(MAX_PREVIEW_CHARS).collect();
    let truncated = chars.next().is_some();
    (Some(preview), truncated)
}

fn metadata_disclosure(
    resource_ref: &str,
    label: String,
    value: Value,
    media_type: Option<String>,
    digest: Option<String>,
) -> Result<ModelContextDisclosure, StorageError> {
    let bytes = serde_json::to_vec(&value)
        .map_err(|_| invalid("model request metadata could not be encoded"))?;
    Ok(ModelContextDisclosure {
        resource_ref: resource_ref.to_owned(),
        data_class: DataClass::Metadata,
        label,
        media_type,
        estimated_bytes: bytes.len() as u64,
        content_sha256: digest,
        preview: None,
        preview_truncated: false,
        untrusted_data: true,
    })
}

fn text_disclosure(
    resource_ref: &str,
    label: String,
    text: String,
    media_type: Option<String>,
    digest: Option<String>,
    estimated_bytes: Option<u64>,
) -> ModelContextDisclosure {
    let (preview, preview_truncated) = bounded_preview(&text);
    ModelContextDisclosure {
        resource_ref: resource_ref.to_owned(),
        data_class: DataClass::Text,
        label,
        media_type,
        estimated_bytes: estimated_bytes.unwrap_or(text.len() as u64),
        content_sha256: digest,
        preview,
        preview_truncated,
        untrusted_data: true,
    }
}

fn asset_blob_disclosure(
    store: &Store,
    resource_ref: &str,
    asset: &motionwright_domain::Asset,
    class: &DataClass,
) -> Result<ModelContextDisclosure, StorageError> {
    let digest = asset
        .content_sha256
        .as_deref()
        .ok_or_else(|| invalid("selected asset has no immutable content digest"))?;
    let descriptor = store.verify_blob(digest)?;
    let supported = match class {
        DataClass::Audio => asset.media_type.starts_with("audio/"),
        DataClass::Frame => asset.media_type.starts_with("image/"),
        DataClass::Text => {
            asset.media_type.starts_with("text/")
                || matches!(
                    asset.media_type.as_str(),
                    "application/json" | "application/x-subrip"
                )
        }
        DataClass::SourceCode => {
            asset.media_type.contains("javascript")
                || asset.media_type.contains("typescript")
                || asset.media_type.contains("python")
                || asset.media_type.contains("rust")
                || asset.media_type.contains("source")
        }
        DataClass::Metadata => false,
    };
    if !supported {
        return Err(invalid(format!(
            "data class {} is not available for asset media type {}",
            data_class_name(class),
            asset.media_type
        )));
    }
    Ok(ModelContextDisclosure {
        resource_ref: resource_ref.to_owned(),
        data_class: class.clone(),
        label: asset.name.clone(),
        media_type: Some(asset.media_type.clone()),
        estimated_bytes: descriptor.size_bytes,
        content_sha256: Some(descriptor.sha256),
        preview: None,
        preview_truncated: false,
        untrusted_data: true,
    })
}

fn uuid_suffix(resource: &str, prefix: &str) -> Option<Uuid> {
    resource
        .strip_prefix(prefix)
        .and_then(|value| Uuid::parse_str(value).ok())
}

fn disclosure_for(
    store: &Store,
    project: &Project,
    resource_ref: &str,
    class: &DataClass,
) -> Result<ModelContextDisclosure, StorageError> {
    if resource_ref == project.resource_key() {
        return match class {
            DataClass::Metadata => metadata_disclosure(
                resource_ref,
                project.title.clone(),
                json!({
                    "id": project.id,
                    "title": project.title,
                    "revision": project.revision,
                    "active_branch": project.active_branch,
                    "scene_count": project.scenes.len(),
                    "asset_count": project.assets.len()
                }),
                None,
                None,
            ),
            DataClass::Text => {
                let payload = serde_json::to_string(&json!({
                    "title": project.title,
                    "brief": project.brief,
                    "narrative": project.narrative
                }))
                .map_err(|_| invalid("project text context could not be encoded"))?;
                Ok(text_disclosure(
                    resource_ref,
                    project.title.clone(),
                    payload,
                    None,
                    None,
                    None,
                ))
            }
            _ => Err(invalid(
                "project resource exposes only metadata or text context",
            )),
        };
    }

    if let Some(id) = uuid_suffix(resource_ref, "scene:") {
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == id)
            .ok_or_else(|| invalid("model request references an unknown scene"))?;
        return match class {
            DataClass::Metadata => metadata_disclosure(
                resource_ref,
                scene.name.clone(),
                json!({
                    "id": scene.id,
                    "name": scene.name,
                    "start": scene.start,
                    "duration": scene.duration,
                    "renderer": scene.renderer,
                    "status": scene.status,
                    "beat_count": scene.beats.len(),
                    "node_count": scene.nodes.len()
                }),
                None,
                None,
            ),
            DataClass::Text => {
                let payload = serde_json::to_string(&json!({
                    "name": scene.name,
                    "objective": scene.objective,
                    "beats": scene.beats.iter().map(|beat| json!({
                        "id": beat.id,
                        "label": beat.label,
                        "objective": beat.objective
                    })).collect::<Vec<_>>(),
                    "node_text": scene.nodes.iter().filter_map(|node| {
                        node.text.as_ref().map(|text| json!({
                            "id": node.id,
                            "name": node.name,
                            "text": text
                        }))
                    }).collect::<Vec<_>>()
                }))
                .map_err(|_| invalid("scene text context could not be encoded"))?;
                Ok(text_disclosure(
                    resource_ref,
                    scene.name.clone(),
                    payload,
                    None,
                    None,
                    None,
                ))
            }
            _ => Err(invalid(
                "scene resource exposes only metadata or text; render a real frame before frame egress",
            )),
        };
    }

    if let Some(id) = uuid_suffix(resource_ref, "asset:") {
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == id)
            .ok_or_else(|| invalid("model request references an unknown asset"))?;
        if matches!(class, DataClass::Metadata) {
            return metadata_disclosure(
                resource_ref,
                asset.name.clone(),
                json!({
                    "id": asset.id,
                    "name": asset.name,
                    "media_type": asset.media_type,
                    "content_sha256": asset.content_sha256,
                    "source_revision": asset.source_revision
                }),
                Some(asset.media_type.clone()),
                asset.content_sha256.clone(),
            );
        }
        return asset_blob_disclosure(store, resource_ref, asset, class);
    }

    if let Some(id) = uuid_suffix(resource_ref, "voice-track:") {
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == id)
            .ok_or_else(|| invalid("model request references an unknown voice track"))?;
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == track.asset_id)
            .ok_or_else(|| invalid("voice track asset is unavailable"))?;
        return match class {
            DataClass::Metadata => metadata_disclosure(
                resource_ref,
                track.label.clone(),
                json!({
                    "id": track.id,
                    "asset_id": track.asset_id,
                    "label": track.label,
                    "sample_rate_hz": track.sample_rate_hz,
                    "channels": track.channels,
                    "measured_duration": track.measured_duration,
                    "source_sha256": track.source_sha256
                }),
                Some(asset.media_type.clone()),
                Some(track.source_sha256.clone()),
            ),
            DataClass::Audio => {
                let mut row = asset_blob_disclosure(store, resource_ref, asset, class)?;
                row.label = track.label.clone();
                Ok(row)
            }
            _ => Err(invalid("voice track exposes only metadata or audio source")),
        };
    }

    if let Some(id) = uuid_suffix(resource_ref, "transcript:")
        .or_else(|| uuid_suffix(resource_ref, "transcript-segment:"))
    {
        let segment = project
            .audio
            .transcript
            .iter()
            .find(|segment| segment.id == id)
            .ok_or_else(|| invalid("model request references an unknown transcript segment"))?;
        return match class {
            DataClass::Metadata => metadata_disclosure(
                resource_ref,
                segment
                    .speaker
                    .clone()
                    .unwrap_or_else(|| "Transcript".into()),
                json!({
                    "id": segment.id,
                    "voice_track_id": segment.voice_track_id,
                    "start": segment.start,
                    "end": segment.end,
                    "speaker": segment.speaker,
                    "alignment": segment.alignment
                }),
                None,
                None,
            ),
            DataClass::Text => Ok(text_disclosure(
                resource_ref,
                segment
                    .speaker
                    .clone()
                    .unwrap_or_else(|| "Transcript".into()),
                segment.text.clone(),
                Some("text/plain".into()),
                None,
                None,
            )),
            _ => Err(invalid("transcript segment exposes only metadata or text")),
        };
    }

    if let Some(id) = uuid_suffix(resource_ref, "claim:") {
        let claim = project
            .brief
            .claims
            .iter()
            .find(|claim| claim.id == id)
            .ok_or_else(|| invalid("model request references an unknown claim"))?;
        return match class {
            DataClass::Metadata => metadata_disclosure(
                resource_ref,
                "Evidence-linked claim".into(),
                json!({
                    "id": claim.id,
                    "source": claim.source,
                    "source_revision": claim.source_revision
                }),
                None,
                None,
            ),
            DataClass::Text => Ok(text_disclosure(
                resource_ref,
                "Evidence-linked claim".into(),
                format!("{}\n{}", claim.text, claim.context),
                Some("text/plain".into()),
                None,
                None,
            )),
            _ => Err(invalid("claim exposes only metadata or text")),
        };
    }

    if let Some(id) =
        uuid_suffix(resource_ref, "beat:").or_else(|| uuid_suffix(resource_ref, "narrative-beat:"))
    {
        if let Some(beat) = project.narrative.beats.iter().find(|beat| beat.id == id) {
            return match class {
                DataClass::Metadata => metadata_disclosure(
                    resource_ref,
                    beat.label.clone(),
                    json!({
                        "id": beat.id,
                        "claim_ids": beat.claim_ids,
                        "preferred_duration": beat.preferred_duration
                    }),
                    None,
                    None,
                ),
                DataClass::Text => Ok(text_disclosure(
                    resource_ref,
                    beat.label.clone(),
                    format!("{}\n{}", beat.objective, beat.audience_takeaway),
                    Some("text/plain".into()),
                    None,
                    None,
                )),
                _ => Err(invalid("narrative beat exposes only metadata or text")),
            };
        }
        if let Some(beat) = project
            .scenes
            .iter()
            .flat_map(|scene| scene.beats.iter())
            .find(|beat| beat.id == id)
        {
            return match class {
                DataClass::Metadata => metadata_disclosure(
                    resource_ref,
                    beat.label.clone(),
                    json!({"id":beat.id,"start":beat.start,"duration":beat.duration}),
                    None,
                    None,
                ),
                DataClass::Text => Ok(text_disclosure(
                    resource_ref,
                    beat.label.clone(),
                    format!("{}\n{}", beat.label, beat.objective),
                    Some("text/plain".into()),
                    None,
                    None,
                )),
                _ => Err(invalid("scene beat exposes only metadata or text")),
            };
        }
        return Err(invalid("model request references an unknown beat"));
    }

    if let Some(id) =
        uuid_suffix(resource_ref, "node:").or_else(|| uuid_suffix(resource_ref, "canvas-node:"))
    {
        let node = project
            .scenes
            .iter()
            .flat_map(|scene| scene.nodes.iter())
            .find(|node| node.id == id)
            .ok_or_else(|| invalid("model request references an unknown canvas node"))?;
        return match class {
            DataClass::Metadata => metadata_disclosure(
                resource_ref,
                node.name.clone(),
                json!({
                    "id":node.id,
                    "name":node.name,
                    "kind":node.kind,
                    "parent_id":node.parent_id,
                    "x":node.x,
                    "y":node.y,
                    "width":node.width,
                    "height":node.height,
                    "rotation_deg":node.rotation_deg,
                    "opacity":node.opacity,
                    "z_index":node.z_index
                }),
                None,
                None,
            ),
            DataClass::Text => Ok(text_disclosure(
                resource_ref,
                node.name.clone(),
                node.text.clone().unwrap_or_default(),
                Some("text/plain".into()),
                None,
                None,
            )),
            _ => Err(invalid("canvas node exposes only metadata or text")),
        };
    }

    Err(invalid("model request references an unsupported resource"))
}

pub(crate) fn build_model_request_preflight(
    store: &Store,
    project: &Project,
    draft: &ModelRequestDraft,
) -> Result<ModelRequestPreflight, StorageError> {
    validate_identity(&draft.provider, "provider")?;
    validate_identity(&draft.model, "model")?;
    let resource_refs = normalize_refs(&draft.resource_refs)?;
    let data_classes = normalize_classes(&draft.data_classes)?;

    let receipt_shape = ModelInvocationReceipt {
        id: Uuid::nil(),
        provider: draft.provider.clone(),
        model: draft.model.clone(),
        provider_version: None,
        base_revision: project.revision,
        resource_refs: resource_refs.clone(),
        data_classes: data_classes.clone(),
        budget: draft.budget.clone(),
        outcome: InvocationOutcome::Unknown,
    };
    receipt_shape.validate().map_err(StorageError::from)?;

    let mut disclosures = Vec::new();
    let mut estimated_total_bytes = 0_u64;
    for resource in &resource_refs {
        for class in &data_classes {
            let disclosure = disclosure_for(store, project, resource, class)?;
            estimated_total_bytes =
                estimated_total_bytes.saturating_add(disclosure.estimated_bytes);
            disclosures.push(disclosure);
        }
    }

    let source_data_classes: Vec<_> = data_classes
        .iter()
        .filter(|class| !matches!(class, DataClass::Metadata))
        .cloned()
        .collect();
    let explicit_source_consent_required =
        matches!(draft.provider_kind, ModelProviderKind::Remote) && !source_data_classes.is_empty();

    let fingerprint_payload = json!({
        "schema": PREFLIGHT_SCHEMA,
        "project_id": project.id,
        "generation": project.generation,
        "base_revision": project.revision,
        "provider_kind": draft.provider_kind,
        "provider": draft.provider,
        "model": draft.model,
        "resource_refs": resource_refs,
        "data_classes": data_classes,
        "budget": draft.budget,
        "disclosures": disclosures,
        "estimated_total_bytes": estimated_total_bytes,
        "source_data_classes": source_data_classes,
        "explicit_source_consent_required": explicit_source_consent_required,
        "fallback_provider": Value::Null,
        "studio_network_dispatch_supported": false,
        "network_dispatched": false
    });
    let bytes = serde_json::to_vec(&fingerprint_payload)
        .map_err(|_| invalid("model request preflight could not be fingerprinted"))?;
    let fingerprint_sha256 = hex::encode(Sha256::digest(bytes));

    Ok(ModelRequestPreflight {
        schema: PREFLIGHT_SCHEMA.into(),
        project_id: project.id,
        generation: project.generation,
        base_revision: project.revision,
        provider_kind: draft.provider_kind,
        provider: draft.provider.clone(),
        model: draft.model.clone(),
        resource_refs,
        data_classes,
        budget: draft.budget.clone(),
        disclosures,
        estimated_total_bytes,
        source_data_classes,
        explicit_source_consent_required,
        fallback_provider: None,
        studio_network_dispatch_supported: false,
        network_dispatched: false,
        fingerprint_sha256,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::RevisionStamp;
    use motionwright_domain::{Asset, Change};
    use std::fs;

    #[test]
    fn metadata_consent_never_implies_audio_source_egress() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("model.sqlite3")).unwrap();
        let mut project = store.create_named_project("Model boundary").unwrap();
        let audio = temp.path().join("voice.bin");
        fs::write(&audio, b"not decoded by the preflight").unwrap();
        let blob = store.ingest_blob_file(&audio).unwrap();
        let asset = Asset {
            id: Uuid::now_v7(),
            name: "voice.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(blob.sha256.clone()),
            source_revision: Some("fixture".into()),
        };
        let outcome = store
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "add-audio",
                &Change::AddAsset {
                    asset: asset.clone(),
                },
            )
            .unwrap();
        project = outcome.project;

        let metadata = build_model_request_preflight(
            &store,
            &project,
            &ModelRequestDraft {
                provider_kind: ModelProviderKind::Remote,
                provider: "authorized-cloud".into(),
                model: "planner".into(),
                resource_refs: vec![format!("asset:{}", asset.id)],
                data_classes: vec![DataClass::Metadata],
                budget: InvocationBudget {
                    max_calls: 1,
                    max_tokens: 500,
                    max_cost_microunits: Some(10_000),
                },
            },
        )
        .unwrap();
        assert!(!metadata.explicit_source_consent_required);
        assert!(metadata.source_data_classes.is_empty());
        assert!(metadata.disclosures[0].estimated_bytes < blob.size_bytes);

        let audio_plan = build_model_request_preflight(
            &store,
            &project,
            &ModelRequestDraft {
                provider_kind: ModelProviderKind::Remote,
                provider: "authorized-cloud".into(),
                model: "audio-planner".into(),
                resource_refs: vec![format!("asset:{}", asset.id)],
                data_classes: vec![DataClass::Audio],
                budget: InvocationBudget {
                    max_calls: 1,
                    max_tokens: 500,
                    max_cost_microunits: Some(10_000),
                },
            },
        )
        .unwrap();
        assert!(audio_plan.explicit_source_consent_required);
        assert_eq!(audio_plan.estimated_total_bytes, blob.size_bytes);
        assert_eq!(audio_plan.fallback_provider, None);
        assert!(!audio_plan.network_dispatched);
    }

    #[test]
    fn scene_text_scope_is_exact_and_does_not_pull_other_scenes() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("scope.sqlite3")).unwrap();
        let initial = store.create_named_project("Minimal context").unwrap();
        let first = store
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "scene-a",
                &Change::AddScene {
                    name: "Visible scene".into(),
                    objective: "Allowed objective".into(),
                    duration_seconds: 4,
                },
            )
            .unwrap()
            .project;
        let project = store
            .apply(
                first.id,
                &RevisionStamp::from(&first),
                "scene-b",
                &Change::AddScene {
                    name: "Private other scene".into(),
                    objective: "Must not be disclosed".into(),
                    duration_seconds: 4,
                },
            )
            .unwrap()
            .project;

        let plan = build_model_request_preflight(
            &store,
            &project,
            &ModelRequestDraft {
                provider_kind: ModelProviderKind::ExternalAgent,
                provider: "external-agent".into(),
                model: "client-selected".into(),
                resource_refs: vec![format!("scene:{}", project.scenes[0].id)],
                data_classes: vec![DataClass::Text],
                budget: InvocationBudget {
                    max_calls: 1,
                    max_tokens: 1_000,
                    max_cost_microunits: None,
                },
            },
        )
        .unwrap();
        let preview = plan.disclosures[0].preview.as_deref().unwrap();
        assert!(preview.contains("Allowed objective"));
        assert!(!preview.contains("Must not be disclosed"));
        assert_eq!(plan.resource_refs.len(), 1);
        assert_eq!(plan.fallback_provider, None);
    }

    #[test]
    fn incompatible_source_class_fails_closed_without_fallback() {
        let temp = tempfile::tempdir().unwrap();
        let mut store = Store::open(temp.path().join("classes.sqlite3")).unwrap();
        let project = store.create_named_project("Fail closed").unwrap();
        let result = build_model_request_preflight(
            &store,
            &project,
            &ModelRequestDraft {
                provider_kind: ModelProviderKind::Remote,
                provider: "primary".into(),
                model: "model".into(),
                resource_refs: vec![project.resource_key()],
                data_classes: vec![DataClass::Audio],
                budget: InvocationBudget {
                    max_calls: 1,
                    max_tokens: 1,
                    max_cost_microunits: None,
                },
            },
        );
        assert!(result.is_err());
    }
}
