//! Read-only domain previews bound to the same observed project as later edits.
use super::*;
use motionwright_service::ChangePreview;
use serde::{Deserialize, Serialize};

const COMMAND: &str = "driver.motionwright.project.preview";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PreviewRequest {
    #[serde(rename = "ref")]
    reference: String,
    edit: PreviewEdit,
}

/// Keep the bounded edit family explicit; a preview is not an arbitrary command.
#[derive(Deserialize, Serialize)]
#[serde(tag = "operation", deny_unknown_fields)]
enum PreviewEdit {
    #[serde(rename = "project.rename")]
    RenameProject { title: String },
    #[serde(rename = "scene.add")]
    AddScene {
        name: String,
        #[serde(default)]
        objective: String,
        duration_seconds: i64,
    },
    #[serde(rename = "narrative.premise.set")]
    SetNarrativePremise { premise: String },
    #[serde(rename = "brief.set")]
    SetBrief {
        objective: String,
        audience: String,
        constraints: Vec<String>,
        exclusions: Vec<String>,
    },
}

impl PreviewEdit {
    fn change(&self) -> NativeResult<Change> {
        let operation = match self {
            Self::RenameProject { .. } => OperationKind::RenameProject,
            Self::AddScene { objective, .. } => {
                // The actual operation's schema rejects this before its parser.
                // Keep that bound even when testing the handler directly.
                if objective.chars().count() > 4_000 {
                    return Err(Error::invalid("scene objective is out of bounds"));
                }
                OperationKind::AddScene
            }
            Self::SetNarrativePremise { .. } => OperationKind::SetNarrativePremise,
            Self::SetBrief { .. } => OperationKind::SetBrief,
        };
        // Share the real mutation's type conversion and limits, without invoking it.
        change_from_args(operation, &serde_json::to_value(self)?)
    }
}

fn contract() -> OperationContract {
    let edit_schema = json!({"oneOf":[
        schema(json!({
            "operation":{"const":"project.rename"},
            "title":{"type":"string","minLength":1,"maxLength":200}
        }), &["operation","title"]),
        schema(json!({
            "operation":{"const":"scene.add"},
            "name":{"type":"string","minLength":1,"maxLength":160},
            "objective":{"type":"string","maxLength":4000},
            "duration_seconds":{"type":"integer","minimum":1,"maximum":86400}
        }), &["operation","name","duration_seconds"]),
        schema(json!({
            "operation":{"const":"narrative.premise.set"},
            "premise":{"type":"string","maxLength":12000}
        }), &["operation","premise"]),
        schema(json!({
            "operation":{"const":"brief.set"},
            "objective":{"type":"string","maxLength":8000},
            "audience":{"type":"string","maxLength":2000},
            "constraints":{"type":"array","maxItems":128,"items":{"type":"string","maxLength":1000}},
            "exclusions":{"type":"array","maxItems":128,"items":{"type":"string","maxLength":1000}}
        }), &["operation","objective","audience","constraints","exclusions"])
    ]});
    OperationContract {
        descriptor: CommandDescriptor {
            name: COMMAND.into(),
            version: "1.0.0".into(),
            description: "Validate a bounded domain edit on the observed project without committing, granting write authority, or reserving future object identities".into(),
            input_schema: schema(json!({
                "ref":{"type":"string","minLength":1,"maxLength":512},
                "edit":edit_schema
            }), &["ref","edit"]),
            output_schema: schema(json!({
                "schema":{"const":"motionwright.change-preview/1"},
                "status":{"const":"READ_ONLY_DOMAIN_VALIDATION"},
                "resource":{"type":"string","minLength":1,"maxLength":512},
                "generation":{"type":"string","minLength":1,"maxLength":256},
                "revision":{"type":"string","pattern":"^(0|[1-9][0-9]*)$","maxLength":20},
                "candidate_title":{"type":"string"},
                "candidate_scene_count":{"type":"integer","minimum":0,"maximum":9007199254740991_u64},
                "committed":{"const":false},
                "exact_future_ids_proven":{"const":false}
            }), &["schema","status","resource","generation","revision",
                  "candidate_title","candidate_scene_count","committed","exact_future_ids_proven"]),
            requires: vec![
                format!("driver:{APP_ID}"),
                semwright_native_sdk::types::NATIVE_RESOURCE_READ_REQUIREMENT.into(),
            ],
            risk: Risk::ReadOnly,
            idempotency: Idempotency::ReadOnly,
            timeout_ms: 15_000,
            dry_run: false,
            interactive_consent: false,
            backends: vec![format!("driver:{APP_ID}")],
        },
        target: TargetRequirement::ObservedResource,
        commit: CommitSemantics::ReadOnly,
        retry: RetrySemantics::Never,
        undo: UndoSemantics::None,
        cancellation: CancellationSemantics::BeforeEffects,
        atomic_revision_cas: false,
    }
}

struct PreviewHandler {
    service: StudioService,
}

fn preview_value(preview: ChangePreview) -> NativeResult<Value> {
    if preview.committed || preview.exact_future_ids_proven {
        return Err(Error::new(
            ErrorCode::BackendFailed,
            "Domain preview claimed mutation authority or future identities",
        ));
    }
    let value = json!({
        "schema":"motionwright.change-preview/1",
        "status":"READ_ONLY_DOMAIN_VALIDATION",
        "resource":preview.base.resource,
        "generation":preview.base.generation.to_string(),
        "revision":preview.base.revision.to_string(),
        "candidate_title":preview.candidate_title,
        "candidate_scene_count":preview.candidate_scene_count,
        "committed":false,
        "exact_future_ids_proven":false
    });
    semwright_native_sdk::cooperation::validate_value(&value)?;
    Ok(value)
}

impl PreviewHandler {
    fn preview(&self, args: Value, context: &CallContext) -> NativeResult<Value> {
        context.check_cancelled()?;
        // A JSON flag or an application-local context is never Host read authority.
        context.driver_context()?;
        let request: PreviewRequest = serde_json::from_value(args)
            .map_err(|_| Error::invalid("Malformed bounded domain preview"))?;
        if request.reference.is_empty() || request.reference.len() > 512 {
            return Err(Error::invalid("Observed project ref is required"));
        }
        let bound = context.expected().ok_or_else(|| {
            Error::new(
                ErrorCode::StaleReference,
                "Observed project base is required",
            )
        })?;
        bound.validate()?;
        // NativeDriver has already checked the opaque ref and refreshed it.
        // Only its authenticated binding selects the project, never request data.
        let project_id = project_id_from_resource(&bound.resource)?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        let expected = expected_stamp(context, &project)?;
        let change = request.edit.change()?;
        let preview = self
            .service
            .preview_change(project_id, &expected, &change)
            .map_err(storage_error)?;
        context.check_cancelled()?;
        preview_value(preview)
    }
}

#[async_trait]
impl OperationHandler for PreviewHandler {
    async fn invoke(&self, args: Value, context: &CallContext) -> Completion {
        match self.preview(args, context) {
            // Completion names successful invocation; the descriptor and result
            // explicitly state that this invocation has no mutation effect.
            Ok(value) => Completion::Applied(value),
            Err(error) => Completion::NotApplied(error),
        }
    }
}

pub(super) fn register(app: Application, service: StudioService) -> NativeResult<Application> {
    app.register(contract(), Arc::new(PreviewHandler { service }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edit(value: Value) -> NativeResult<Change> {
        let parsed: PreviewEdit =
            serde_json::from_value(value).map_err(|_| Error::invalid("Malformed preview edit"))?;
        parsed.change()
    }

    #[test]
    fn preview_contract_requires_resource_read_and_never_claims_write_or_dry_run() {
        let contract = contract();
        contract.validate(APP_ID).unwrap();
        assert_eq!(contract.descriptor.risk, Risk::ReadOnly);
        assert_eq!(contract.descriptor.idempotency, Idempotency::ReadOnly);
        assert_eq!(contract.commit, CommitSemantics::ReadOnly);
        assert_eq!(contract.target, TargetRequirement::ObservedResource);
        assert!(!contract.atomic_revision_cas);
        assert!(!contract.descriptor.dry_run);
        assert!(
            contract
                .descriptor
                .requires
                .iter()
                .any(|requirement| requirement
                    == semwright_native_sdk::types::NATIVE_RESOURCE_READ_REQUIREMENT)
        );
        assert_eq!(
            contract.descriptor.input_schema["additionalProperties"],
            false
        );
    }

    #[test]
    fn preview_preserves_all_four_existing_domain_edit_conversions() {
        for (value, operation) in [
            (
                json!({"operation":"project.rename","title":"A proposed title"}),
                OperationKind::RenameProject,
            ),
            (
                json!({"operation":"scene.add","name":"Opening","duration_seconds":3}),
                OperationKind::AddScene,
            ),
            (
                json!({"operation":"narrative.premise.set","premise":"A precise premise"}),
                OperationKind::SetNarrativePremise,
            ),
            (
                json!({"operation":"brief.set","objective":"Clear","audience":"Everyone","constraints":[],"exclusions":[]}),
                OperationKind::SetBrief,
            ),
        ] {
            assert_eq!(
                serde_json::to_value(edit(value.clone()).unwrap()).unwrap(),
                serde_json::to_value(change_from_args(operation, &value).unwrap()).unwrap()
            );
        }
    }

    #[test]
    fn preview_rejects_unknown_operations_fields_types_and_out_of_bounds_inputs() {
        for bad in [
            json!({"operation":"plugin.execute","command":"unapproved"}),
            json!({"operation":"project.rename","title":"Valid","actor":"human"}),
            json!({"operation":"project.rename","title":123}),
            json!({"operation":"project.rename","title":" "}),
            json!({"operation":"project.rename","title":"x".repeat(201)}),
            json!({"operation":"scene.add","name":"Scene","duration_seconds":0}),
            json!({"operation":"scene.add","name":"Scene","duration_seconds":86401}),
            json!({"operation":"scene.add","name":"Scene","duration_seconds":1.5}),
            json!({"operation":"scene.add","name":"Scene","duration_seconds":3,"objective":"x".repeat(4001)}),
            json!({"operation":"narrative.premise.set","premise":"x".repeat(12001)}),
            json!({"operation":"brief.set","objective":"","audience":"","constraints":vec!["item";129],"exclusions":[]}),
        ] {
            assert!(edit(bad).is_err());
        }
        for field in [
            "resource",
            "generation",
            "revision",
            "request_id",
            "recovery",
            "dry_run",
            "actor",
        ] {
            let mut request =
                json!({"ref":"opaque","edit":{"operation":"project.rename","title":"Valid"}});
            request[field] = json!("forged");
            assert!(serde_json::from_value::<PreviewRequest>(request).is_err());
        }
    }

    #[test]
    fn preview_wire_retains_full_revision_and_never_exposes_future_ids_or_receipts() {
        let value = preview_value(ChangePreview {
            base: RevisionStamp {
                resource: format!("project:{}", Uuid::now_v7()),
                generation: Uuid::now_v7(),
                revision: u64::MAX,
            },
            candidate_title: "Proposed title".into(),
            candidate_scene_count: 2,
            committed: false,
            exact_future_ids_proven: false,
        })
        .unwrap();
        assert_eq!(value["revision"], u64::MAX.to_string());
        assert_eq!(value["committed"], false);
        assert_eq!(value["exact_future_ids_proven"], false);
        assert!(value.get("request_id").is_none());
        assert!(value.get("recovery").is_none());
        assert!(value.get("project").is_none());
    }

    #[tokio::test]
    async fn preview_application_local_context_cannot_impersonate_host_read_authority() {
        let folder = tempfile::tempdir().unwrap();
        let service = StudioService::open(folder.path().join("preview.sqlite3")).unwrap();
        let project = service.create_project("Original").unwrap();
        let handler = PreviewHandler {
            service: service.clone(),
        };
        let result = handler.invoke(
            json!({"ref":project.resource_key(),"edit":{"operation":"project.rename","title":"Forged"}}),
            &CallContext::application_local("local-preview").unwrap(),
        ).await;
        match result {
            Completion::NotApplied(error) => assert_eq!(error.code, ErrorCode::PermissionDenied),
            _ => panic!("Application-local invocation acquired Host read authority"),
        }
        assert_eq!(service.project(project.id).unwrap().title, "Original");
        assert!(service.history(project.id, 0, 8).unwrap().is_empty());
    }
}
