//! Project-library operations over the existing StudioService.
//!
//! Index metadata is not a declaration that a project document is readable.
//! Opening always loads and validates the original record. List/open/recovery
//! never mutate a document. Creation consumes the existing effect grant before
//! forwarding the exact retained intent to the Store's durable authority.
//! The desktop wrappers supply the real service, session and invoking window;
//! none of those authority values are accepted in the caller's request.

use super::effect_grants::{
    EffectGrantReceipt, EffectGrantRegistry, EffectKind, EffectScope, project_creation_subject,
};
use motionwright_domain::Project;
use motionwright_service::{
    ProjectCreationIntent, ProjectCreationOutcome, ProjectCursor, ProjectSummary, StudioService,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub const DEFAULT_PROJECT_PAGE_SIZE: usize = 20;
pub const MAX_PROJECT_PAGE_SIZE: usize = 100;
const MAX_CURSOR_TIMESTAMP_BYTES: usize = 128;

fn default_page_size() -> usize {
    DEFAULT_PROJECT_PAGE_SIZE
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListProjectsRequest {
    #[serde(default)]
    pub cursor: Option<ProjectCursor>,
    #[serde(default = "default_page_size")]
    pub limit: usize,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenProjectRequest {
    pub project_id: Uuid,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BootstrapRequest {
    #[serde(default)]
    pub project_id: Option<Uuid>,
}

#[derive(Debug, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum BootstrapProjectSelection {
    Unselected,
    Selected { project_id: Uuid },
    Unavailable { project_id: Uuid, message: String },
}

/// Read precisely the chosen identity. An empty or unavailable selection never
/// creates a document and never switches to a different record in the index.
pub fn bootstrap_project(
    service: &StudioService,
    request: BootstrapRequest,
) -> (Option<Project>, BootstrapProjectSelection) {
    match request.project_id {
        None => (None, BootstrapProjectSelection::Unselected),
        Some(project_id) => match open_project(service, OpenProjectRequest { project_id }) {
            Ok(project) => (
                Some(project),
                BootstrapProjectSelection::Selected { project_id },
            ),
            Err(message) => (
                None,
                BootstrapProjectSelection::Unavailable {
                    project_id,
                    message,
                },
            ),
        },
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateProjectRequest {
    pub intent: ProjectCreationIntent,
    pub effect_grant: Uuid,
}

fn creation_subject(
    invoking_window: &str,
    intent: &ProjectCreationIntent,
) -> Result<String, String> {
    intent.validate().map_err(|error| error.to_string())?;
    project_creation_subject(
        invoking_window,
        &intent.request_id.to_string(),
        &intent.title,
    )
}

pub fn issue_creation_grant(
    grants: &EffectGrantRegistry,
    workspace_session: Uuid,
    invoking_window: &str,
    intent: &ProjectCreationIntent,
) -> Result<EffectGrantReceipt, String> {
    let subject = creation_subject(invoking_window, intent)?;
    grants.issue(
        EffectKind::ProjectCreate,
        EffectScope::project_creation(workspace_session),
        &subject,
    )
}

pub fn create_project(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    workspace_session: Uuid,
    invoking_window: &str,
    request: CreateProjectRequest,
) -> Result<ProjectCreationOutcome, String> {
    let subject = creation_subject(invoking_window, &request.intent)?;
    grants.consume(
        request.effect_grant,
        EffectKind::ProjectCreate,
        EffectScope::project_creation(workspace_session),
        &subject,
    )?;
    service
        .create_project_with_intent(&request.intent)
        .map_err(|error| error.to_string())
}

pub fn recover_project_creation(
    service: &StudioService,
    intent: &ProjectCreationIntent,
) -> Result<Option<ProjectCreationOutcome>, String> {
    intent.validate().map_err(|error| error.to_string())?;
    service
        .recover_project_creation(intent)
        .map_err(|error| error.to_string())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProjectLibrarySummary {
    pub id: Uuid,
    pub generation: Uuid,
    /// Index revisions are strings so every stored u64 remains exact in JS.
    pub revision: String,
    pub title: String,
    pub updated_at: String,
}

impl From<ProjectSummary> for ProjectLibrarySummary {
    fn from(summary: ProjectSummary) -> Self {
        Self {
            id: summary.id,
            generation: summary.generation,
            revision: summary.revision.to_string(),
            title: summary.title,
            updated_at: summary.updated_at,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProjectLibraryPage {
    pub items: Vec<ProjectLibrarySummary>,
    pub next_cursor: Option<ProjectCursor>,
    pub truncated: bool,
}

pub fn list_projects(
    service: &StudioService,
    request: ListProjectsRequest,
) -> Result<ProjectLibraryPage, String> {
    if !(1..=MAX_PROJECT_PAGE_SIZE).contains(&request.limit) {
        return Err("Project pages must contain between 1 and 100 entries.".into());
    }
    if let Some(cursor) = &request.cursor
        && (cursor.updated_at.is_empty()
            || cursor.updated_at.len() > MAX_CURSOR_TIMESTAMP_BYTES
            || cursor.updated_at.chars().any(char::is_control))
    {
        return Err("The project page cursor is invalid; refresh the library.".into());
    }
    // The existing index reads metadata columns, not document_json. A damaged
    // document therefore remains listed alongside healthy records. Corrupt
    // index columns can still make Store reject a page; do not hide that error
    // or attempt a second SQL implementation here.
    let page = service
        .project_summaries(request.cursor.as_ref(), request.limit)
        .map_err(|error| format!("The project index could not be read: {error}"))?;
    Ok(ProjectLibraryPage {
        items: page.items.into_iter().map(Into::into).collect(),
        next_cursor: page.next_cursor,
        truncated: page.truncated,
    })
}

pub fn open_project(
    service: &StudioService,
    request: OpenProjectRequest,
) -> Result<Project, String> {
    let project = service
        .project(request.project_id)
        .map_err(|error| format!("The stored project could not be opened: {error}"))?;
    // A valid document belonging to another index row is still a damaged
    // association, not permission to silently switch the requested identity.
    if project.id != request.project_id {
        return Err("The stored project identity differs from its index entry.".into());
    }
    Ok(project)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::Change;
    use std::collections::BTreeSet;

    fn service() -> (tempfile::TempDir, StudioService) {
        let directory = tempfile::tempdir().unwrap();
        let service = StudioService::open(directory.path().join("library.sqlite3")).unwrap();
        (directory, service)
    }

    fn first_page(service: &StudioService, limit: usize) -> ProjectLibraryPage {
        list_projects(
            service,
            ListProjectsRequest {
                cursor: None,
                limit,
            },
        )
        .unwrap()
    }

    #[test]
    fn actual_service_lists_and_opens_projects_without_document_or_journal_writes() {
        let (_directory, service) = service();
        let a = service.create_project("Synthetic library A").unwrap();
        let b = service.create_project("Synthetic library B").unwrap();
        let a = service
            .apply(
                a.id,
                &a.stamp(),
                "library-fixture-scene",
                &Change::AddScene {
                    name: "Authored scene".into(),
                    objective: "Preserve the existing record when selecting it".into(),
                    duration_seconds: 3,
                },
            )
            .unwrap()
            .project;
        let before = [
            service.export_project(a.id).unwrap(),
            service.export_project(b.id).unwrap(),
        ];
        let page = first_page(&service, 20);
        assert_eq!(page.items.len(), 2);
        assert!(!page.truncated);
        assert_eq!(page.next_cursor, None);
        for expected in [a, b] {
            assert!(page.items.iter().any(|item| {
                item.id == expected.id && item.revision == expected.revision.to_string()
            }));
            assert_eq!(
                open_project(
                    &service,
                    OpenProjectRequest {
                        project_id: expected.id,
                    }
                )
                .unwrap(),
                expected
            );
        }
        for backup in before {
            let after = service.export_project(backup.project.id).unwrap();
            assert_eq!(after.project, backup.project);
            assert_eq!(after.events, backup.events);
            assert_eq!(after.payload_sha256, backup.payload_sha256);
        }
    }

    #[test]
    fn keyset_pages_cover_real_records_and_survive_service_reopen() {
        let (directory, service) = service();
        let mut expected = BTreeSet::new();
        for index in 0..5 {
            expected.insert(
                service
                    .create_project(&format!("Synthetic page {index}"))
                    .unwrap()
                    .id,
            );
        }
        let first = first_page(&service, 2);
        assert_eq!(first.items.len(), 2);
        assert!(first.truncated);
        drop(service);
        let reopened = StudioService::open(directory.path().join("library.sqlite3")).unwrap();
        let mut found = first
            .items
            .into_iter()
            .map(|item| item.id)
            .collect::<BTreeSet<_>>();
        let mut cursor = first.next_cursor;
        while cursor.is_some() {
            let page = list_projects(&reopened, ListProjectsRequest { cursor, limit: 2 }).unwrap();
            assert_eq!(page.truncated, page.next_cursor.is_some());
            for item in page.items {
                assert!(found.insert(item.id));
            }
            cursor = page.next_cursor;
        }
        assert_eq!(found, expected);
    }

    #[test]
    fn missing_project_does_not_create_a_replacement_or_change_known_records() {
        let (_directory, service) = service();
        let project = service
            .create_project("Synthetic retained project")
            .unwrap();
        let before = service.export_project(project.id).unwrap();
        assert!(
            open_project(
                &service,
                OpenProjectRequest {
                    project_id: Uuid::new_v4()
                }
            )
            .is_err()
        );
        assert_eq!(first_page(&service, 20).items.len(), 1);
        assert_eq!(
            service.export_project(project.id).unwrap().payload_sha256,
            before.payload_sha256
        );
    }

    #[test]
    fn requests_are_bounded_and_do_not_silently_clamp_invalid_pages() {
        let (_directory, service) = service();
        let default: ListProjectsRequest = serde_json::from_str("{}").unwrap();
        assert_eq!(default.limit, DEFAULT_PROJECT_PAGE_SIZE);
        assert!(default.cursor.is_none());
        for limit in [0, MAX_PROJECT_PAGE_SIZE + 1] {
            assert!(
                list_projects(
                    &service,
                    ListProjectsRequest {
                        cursor: None,
                        limit
                    }
                )
                .is_err()
            );
        }
        assert!(
            list_projects(
                &service,
                ListProjectsRequest {
                    cursor: Some(ProjectCursor {
                        updated_at: "\n".into(),
                        id: Uuid::new_v4()
                    }),
                    limit: 20,
                }
            )
            .is_err()
        );
        assert!(serde_json::from_str::<ListProjectsRequest>("{\"repair\":true}").is_err());
        assert!(
            serde_json::from_str::<OpenProjectRequest>("{\"project_id\":\"not-a-uuid\"}").is_err()
        );
        assert!(first_page(&service, 20).items.is_empty());
    }

    #[test]
    fn index_revision_wire_preserves_all_u64_values_without_authority_fields() {
        let summary = ProjectLibrarySummary::from(ProjectSummary {
            id: Uuid::new_v4(),
            generation: Uuid::new_v4(),
            revision: u64::MAX,
            title: "Synthetic boundary value".into(),
            updated_at: "2026-10-17T00:00:00Z".into(),
        });
        let value = serde_json::to_value(summary).unwrap();
        assert_eq!(value["revision"], "18446744073709551615");
        assert_eq!(value.as_object().unwrap().len(), 5);
        assert!(value.get("effect_grant").is_none());
        assert!(value.get("document").is_none());
    }

    fn creation_intent() -> ProjectCreationIntent {
        ProjectCreationIntent {
            request_id: Uuid::now_v7(),
            title: "  Original creation title  ".into(),
        }
    }

    #[test]
    fn creation_consumes_one_exact_grant_before_persisting_the_original_intent() {
        let (_directory, service) = service();
        let grants = EffectGrantRegistry::default();
        let session = Uuid::now_v7();
        let intent = creation_intent();
        let grant = issue_creation_grant(&grants, session, "main", &intent).unwrap();
        assert!(
            first_page(&service, 20).items.is_empty(),
            "issuing a grant cannot create a Project"
        );
        let request = CreateProjectRequest {
            intent: intent.clone(),
            effect_grant: grant.token,
        };
        let created = create_project(&service, &grants, session, "main", request.clone()).unwrap();
        assert_eq!(created.request_id, intent.request_id);
        assert_eq!(created.project.title, intent.title);
        assert_eq!(created.project.revision, 0);
        assert!(!created.replayed);
        assert!(create_project(&service, &grants, session, "main", request).is_err());
        assert_eq!(first_page(&service, 20).items.len(), 1);
        assert_eq!(
            service.project(created.project.id).unwrap(),
            created.project
        );
    }

    #[test]
    fn creation_rejects_foreign_window_session_registry_key_and_title_without_a_store_effect() {
        for mismatch in ["window", "session", "registry", "key", "title"] {
            let (_directory, service) = service();
            let grants = EffectGrantRegistry::default();
            let foreign = EffectGrantRegistry::default();
            let session = Uuid::now_v7();
            let intent = creation_intent();
            let grant = issue_creation_grant(&grants, session, "main", &intent).unwrap();
            let mut changed = intent.clone();
            if mismatch == "key" {
                changed.request_id = Uuid::now_v7();
            }
            if mismatch == "title" {
                changed.title = changed.title.trim().into();
            }
            let result = create_project(
                &service,
                if mismatch == "registry" {
                    &foreign
                } else {
                    &grants
                },
                if mismatch == "session" {
                    Uuid::now_v7()
                } else {
                    session
                },
                if mismatch == "window" {
                    "another-window"
                } else {
                    "main"
                },
                CreateProjectRequest {
                    intent: changed,
                    effect_grant: grant.token,
                },
            );
            assert!(result.is_err(), "{mismatch}");
            assert!(first_page(&service, 20).items.is_empty(), "{mismatch}");
            assert!(
                recover_project_creation(&service, &intent)
                    .unwrap()
                    .is_none()
            );
        }
    }

    #[test]
    fn invalid_creation_is_checked_before_issue_and_consume_without_normalizing_the_title() {
        let (_directory, service) = service();
        let grants = EffectGrantRegistry::default();
        let session = Uuid::now_v7();
        let intent = creation_intent();
        let token = issue_creation_grant(&grants, session, "main", &intent)
            .unwrap()
            .token;
        for bad in [
            ProjectCreationIntent {
                request_id: Uuid::nil(),
                title: intent.title.clone(),
            },
            ProjectCreationIntent {
                request_id: intent.request_id,
                title: " ".into(),
            },
            ProjectCreationIntent {
                request_id: intent.request_id,
                title: "x".repeat(201),
            },
        ] {
            assert!(issue_creation_grant(&grants, session, "main", &bad).is_err());
            assert!(
                create_project(
                    &service,
                    &grants,
                    session,
                    "main",
                    CreateProjectRequest {
                        intent: bad,
                        effect_grant: token
                    }
                )
                .is_err()
            );
            assert!(first_page(&service, 20).items.is_empty());
        }
        // Malformed input is rejected before token removal; it does not invent
        // a blanket claim that every rejected request consumes the grant.
        let created = create_project(
            &service,
            &grants,
            session,
            "main",
            CreateProjectRequest {
                intent: intent.clone(),
                effect_grant: token,
            },
        )
        .unwrap();
        assert_eq!(created.project.title, intent.title);
    }

    #[test]
    fn creation_recovery_retains_original_r0_and_reopens_current_authority_without_new_grants() {
        let (directory, service) = service();
        let grants = EffectGrantRegistry::default();
        let session = Uuid::now_v7();
        let intent = creation_intent();
        let token = issue_creation_grant(&grants, session, "main", &intent)
            .unwrap()
            .token;
        let original = create_project(
            &service,
            &grants,
            session,
            "main",
            CreateProjectRequest {
                intent: intent.clone(),
                effect_grant: token,
            },
        )
        .unwrap();
        let current = service
            .apply(
                original.project.id,
                &original.project.stamp(),
                "author-after-creation",
                &Change::RenameProject {
                    title: "The author's later title".into(),
                },
            )
            .unwrap()
            .project;
        let backup = service.export_project(current.id).unwrap();
        drop(service);
        let reopened = StudioService::open(directory.path().join("library.sqlite3")).unwrap();
        for _ in 0..3 {
            let recovered = recover_project_creation(&reopened, &intent)
                .unwrap()
                .unwrap();
            assert!(recovered.replayed);
            assert_eq!(recovered.project, original.project);
            assert_eq!(
                open_project(
                    &reopened,
                    OpenProjectRequest {
                        project_id: recovered.project.id
                    }
                )
                .unwrap(),
                current
            );
            let after = reopened.export_project(current.id).unwrap();
            assert_eq!(after.project, backup.project);
            assert_eq!(after.events, backup.events);
            assert_eq!(after.payload_sha256, backup.payload_sha256);
        }
        assert_eq!(first_page(&reopened, 20).items.len(), 1);
    }

    #[test]
    fn unknown_creation_recovery_never_creates_a_replacement() {
        let (_directory, service) = service();
        let intent = creation_intent();
        for _ in 0..3 {
            assert!(
                recover_project_creation(&service, &intent)
                    .unwrap()
                    .is_none()
            );
            assert!(first_page(&service, 20).items.is_empty());
        }
    }

    #[test]
    fn creation_request_cannot_supply_backend_window_session_scope_or_subject() {
        let intent = creation_intent();
        let body = serde_json::json!({ "intent": intent, "effect_grant": Uuid::now_v7() });
        assert!(serde_json::from_value::<CreateProjectRequest>(body.clone()).is_ok());
        for field in [
            "window",
            "workspace_session",
            "project_id",
            "subject",
            "store_path",
        ] {
            let mut malicious = body.clone();
            malicious[field] = serde_json::json!("caller-supplied-authority");
            assert!(serde_json::from_value::<CreateProjectRequest>(malicious).is_err());
            let mut issue = serde_json::to_value(&intent).unwrap();
            issue[field] = serde_json::json!("caller-supplied-authority");
            assert!(serde_json::from_value::<ProjectCreationIntent>(issue).is_err());
        }
    }
}
