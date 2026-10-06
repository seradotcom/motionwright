use motionwright_domain::{
    Change, CreativeEdit, DataClass, InvocationBudget, InvocationOutcome, ModelInvocationReceipt,
    Project, Proposal, ProposalScope, ProposalSet, SearchBudget,
};
use uuid::Uuid;

fn proposal(id: Uuid, edit: CreativeEdit) -> Proposal {
    Proposal {
        id,
        title: "Bounded alternative".into(),
        rationale: "Exercise proposal target admission.".into(),
        structure: vec!["intent".into(), "typed edit".into()],
        edits: vec![edit],
    }
}

fn two_proposals(edit: CreativeEdit) -> Vec<Proposal> {
    vec![
        proposal(Uuid::now_v7(), edit.clone()),
        proposal(Uuid::now_v7(), edit),
    ]
}

#[test]
fn proposal_scope_cannot_reference_an_unknown_scene() {
    let mut project = Project::new("Proposal safety").unwrap();
    let unknown = Uuid::now_v7();
    let set = ProposalSet {
        id: Uuid::now_v7(),
        base_revision: project.revision,
        scope: ProposalScope::Scene { scene_id: unknown },
        search_budget: SearchBudget {
            candidates: 2,
            model_calls: 0,
            max_tokens: 0,
        },
        proposals: two_proposals(CreativeEdit::SceneObjective {
            scene_id: unknown,
            objective: "escape".into(),
        }),
        selected: None,
    };
    assert!(
        project
            .apply_change(&Change::AddProposalSet { proposal_set: set })
            .is_err()
    );
}

#[test]
fn renderer_proposal_is_closed_to_supported_renderer_identifiers() {
    let mut project = Project::new("Renderer safety").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Scene".into(),
            objective: "Objective".into(),
            duration_seconds: 4,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    let set = ProposalSet {
        id: Uuid::now_v7(),
        base_revision: project.revision,
        scope: ProposalScope::Scene { scene_id },
        search_budget: SearchBudget {
            candidates: 2,
            model_calls: 1,
            max_tokens: 100,
        },
        proposals: two_proposals(CreativeEdit::RendererChoice {
            scene_id,
            renderer: "arbitrary-python".into(),
        }),
        selected: None,
    };
    assert!(
        project
            .apply_change(&Change::AddProposalSet { proposal_set: set })
            .is_err()
    );
}

#[test]
fn model_receipt_requires_exact_base_and_known_resources() {
    let mut project = Project::new("Model receipt safety").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Scene".into(),
            objective: "Objective".into(),
            duration_seconds: 4,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    let mut receipt = ModelInvocationReceipt {
        id: Uuid::now_v7(),
        provider: "manual".into(),
        model: "human-planner".into(),
        provider_version: None,
        base_revision: project.revision + 1,
        resource_refs: vec![format!("scene:{scene_id}")],
        data_classes: vec![DataClass::Text],
        budget: InvocationBudget {
            max_calls: 1,
            max_tokens: 100,
            max_cost_microunits: None,
        },
        outcome: InvocationOutcome::Succeeded,
    };
    assert!(
        project
            .apply_change(&Change::RecordModelInvocation {
                receipt: receipt.clone(),
            })
            .is_err()
    );

    receipt.base_revision = project.revision;
    receipt.resource_refs = vec![format!("scene:{}", Uuid::now_v7())];
    assert!(
        project
            .apply_change(&Change::RecordModelInvocation {
                receipt: receipt.clone(),
            })
            .is_err()
    );

    receipt.resource_refs = vec![format!("scene:{scene_id}")];
    project
        .apply_change(&Change::RecordModelInvocation { receipt })
        .unwrap();
    assert_eq!(project.model_invocations.len(), 1);
}
