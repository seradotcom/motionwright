//! Minimal owner-local agent interoperability test actor.
//!
//! It reuses real Semwright Native SDK capability contracts and page-bound
//! observers; writes use the canonical StudioService revision CAS only under
//! an explicit owner-local CLI opt-in. This is NOT a remote-host authorization
//! grant, MCP server, extra backend, or a model-executable install.
use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_native::{MotionwrightObserver, build_application};
use motionwright_service::StudioService;
use semwright_native_sdk::cooperation::{CallContext, ObservationProvider, PageCursor, Query};
use semwright_native_sdk::{Driver, NativeDriver, json};
use serde::Deserialize;
use serde_json::Value;
use std::{
    env, fs,
    path::{Path, PathBuf},
};
use uuid::Uuid;

const MAX_REQUEST: u64 = 64 * 1024;
const MAX_PAGE: u16 = 32;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields, tag = "operation")]
enum LocalEdit {
    #[serde(rename = "project.rename")]
    Rename { title: String },
    #[serde(rename = "scene.add")]
    AddScene {
        name: String,
        objective: String,
        duration_seconds: i64,
    },
    #[serde(rename = "narrative.premise.set")]
    SetNarrative { premise: String },
    #[serde(rename = "brief.set")]
    SetBrief {
        objective: String,
        audience: String,
        constraints: Vec<String>,
        exclusions: Vec<String>,
    },
}
impl LocalEdit {
    fn operation_name(&self) -> &'static str {
        match self {
            Self::Rename { .. } => "project.rename",
            Self::AddScene { .. } => "scene.add",
            Self::SetNarrative { .. } => "narrative.premise.set",
            Self::SetBrief { .. } => "brief.set",
        }
    }
    fn to_change(self) -> Change {
        match self {
            Self::Rename { title } => Change::RenameProject { title },
            Self::AddScene {
                name,
                objective,
                duration_seconds,
            } => Change::AddScene {
                name,
                objective,
                duration_seconds,
            },
            Self::SetNarrative { premise } => Change::SetNarrativePremise { premise },
            Self::SetBrief {
                objective,
                audience,
                constraints,
                exclusions,
            } => Change::SetBrief {
                objective,
                audience,
                constraints,
                exclusions,
            },
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Discover {
        offset: usize,
        limit: usize,
    },
    Observe {
        project_id: Uuid,
        scope: String,
        limit: u16,
        cursor: Option<PageCursor>,
    },
    Preview {
        project_id: Uuid,
        expected_generation: Uuid,
        expected_revision: u64,
        edit: LocalEdit,
    },
    Commit {
        project_id: Uuid,
        expected_generation: Uuid,
        expected_revision: u64,
        request_id: String,
        edit: LocalEdit,
    },
    Init {
        title: String,
    },
}
fn error(message: &str) -> Box<dyn std::error::Error> {
    std::io::Error::new(std::io::ErrorKind::InvalidInput, message).into()
}
fn checked_db(path: &Path, init: bool) -> Result<(), Box<dyn std::error::Error>> {
    if !path.is_absolute()
        || path
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
        || path.extension().and_then(|x| x.to_str()) != Some("sqlite3")
    {
        return Err(error(
            "Explicit absolute .sqlite3 database path is required",
        ));
    }
    let parent = path
        .parent()
        .ok_or_else(|| error("Database parent is missing"))?;
    if !parent.is_dir() || fs::symlink_metadata(parent)?.file_type().is_symlink() {
        return Err(error("Database parent must be a real existing directory"));
    }
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_file() && !meta.file_type().is_symlink() => {}
        Ok(_) => return Err(error("Database cannot be a symlink or non-file")),
        Err(err) if init && err.kind() == std::io::ErrorKind::NotFound => {}
        Err(err) => return Err(err.into()),
    }
    Ok(())
}
fn checked_request(path: &Path) -> Result<Request, Box<dyn std::error::Error>> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.file_type().is_file()
        || meta.file_type().is_symlink()
        || meta.len() == 0
        || meta.len() > MAX_REQUEST
    {
        return Err(error(
            "Request must be a bounded ordinary owner-supplied JSON file",
        ));
    }
    let req: Request = serde_json::from_slice(&fs::read(path)?)?;
    Ok(req)
}
fn checked_owner_intent(edit: &LocalEdit) -> Result<(), Box<dyn std::error::Error>> {
    let words = |s: &str, maximum: usize| {
        !s.trim().is_empty() && s.len() <= maximum && !s.chars().any(|c| c == '\0')
    };
    match edit {
        LocalEdit::Rename { title } if words(title, 200) => {}
        LocalEdit::AddScene {
            name,
            objective,
            duration_seconds,
        } if words(name, 200)
            && words(objective, 2000)
            && (1..=86400).contains(duration_seconds) => {}
        LocalEdit::SetNarrative { premise } if words(premise, 8000) => {}
        LocalEdit::SetBrief {
            objective,
            audience,
            constraints,
            exclusions,
        } if words(objective, 8000)
            && words(audience, 2000)
            && constraints.len() <= 128
            && exclusions.len() <= 128
            && constraints.iter().chain(exclusions).all(|s| words(s, 1000)) => {}
        _ => return Err(error("Unapproved or unbounded local editing payload")),
    }
    Ok(())
}
fn check_base(
    project: &Project,
    generation: Uuid,
    revision: u64,
) -> Result<(), Box<dyn std::error::Error>> {
    if project.generation != generation || project.revision != revision {
        return Err(error(
            "Stale project revision or generation. Observe again before editing",
        ));
    }
    Ok(())
}
fn require_contract(
    service: &StudioService,
    edit: &LocalEdit,
) -> Result<(), Box<dyn std::error::Error>> {
    let contract_name = format!("driver.motionwright.{}", edit.operation_name());
    let app = build_application(service.clone())?;
    if !app
        .contracts()
        .iter()
        .any(|c| c.descriptor.name == contract_name)
    {
        return Err(error(
            "Operation is not registered in the current Semwright Native SDK",
        ));
    }
    Ok(())
}
async fn execute(
    service: StudioService,
    request: Request,
    owner_edit: bool,
) -> Result<Value, Box<dyn std::error::Error>> {
    match request {
        Request::Init { title } => {
            if !owner_edit {
                return Err(error(
                    "Owner-local --owner-edit required to create a project",
                ));
            }
            if title.trim().is_empty() || title.len() > 200 {
                return Err(error("Project title is out of bounds"));
            }
            let p = service.create_project(&title)?;
            Ok(
                json!({"status":"owner_local_created","resource":p.resource_key(),
                "project_id":p.id,"generation":p.generation,
                "revision":p.revision,"no_remote_host_authority":true}),
            )
        }
        Request::Discover { offset, limit } => {
            if !(1..=MAX_PAGE as usize).contains(&limit) {
                return Err(error("Capability page limit must be in 1..32"));
            }
            let app = build_application(service)?;
            // A real SDK driver compiles the registered operation schemas
            // rather than re-generating a parallel list of "agent tools".
            let mut driver = NativeDriver::new(app)?;
            let mut all = driver.capabilities().await?;
            all.sort_by(|a, b| a.descriptor.name.cmp(&b.descriptor.name));
            if offset > all.len() {
                return Err(error("Capability continuation offset is invalid"));
            }
            let end = offset.saturating_add(limit).min(all.len());
            let items = all[offset..end]
                .iter()
                .map(|item| {
                    json!({
                        "name":item.descriptor.name,
                        "risk":item.descriptor.risk,
                        "source":"semwright_native_sdk_operation_contract"
                    })
                })
                .collect::<Vec<_>>();
            Ok(
                json!({"schema":"motionwright.owner-local-agent-discovery/1",
                "sdk_application":"motionwright","total":all.len(),
                "items":items,"next_offset":if end<all.len(){Some(end)}else{None},
                "complete":end==all.len(),"no_remote_host_authority":true}),
            )
        }
        Request::Observe {
            project_id,
            scope,
            limit,
            cursor,
        } => {
            if !(1..=MAX_PAGE).contains(&limit) {
                return Err(error(
                    "Observation page exceeds bounded local reader budget",
                ));
            }
            if scope.trim().is_empty() || scope.len() > 128 {
                return Err(error(
                    "Observation scope must be a canonical registered scope",
                ));
            }
            let resource = format!("project:{project_id}");
            let observer = MotionwrightObserver::new(service);
            let query = Query {
                resource,
                scope,
                limit,
                cursor,
            };
            let context = CallContext::application_local("local-agent-read")?;
            let page = observer.observe(&query, &context).await?;
            page.validate_for(&query)?;
            Ok(
                json!({"schema":"motionwright.owner-local-agent-observation/1",
                "page":page,"no_remote_host_authority":true}),
            )
        }
        Request::Preview {
            project_id,
            expected_generation,
            expected_revision,
            edit,
        } => {
            checked_owner_intent(&edit)?;
            require_contract(&service, &edit)?;
            let project = service.project(project_id)?;
            check_base(&project, expected_generation, expected_revision)?;
            let mut proposed = project.clone();
            proposed.apply_change(&edit.to_change())?;
            proposed.validate()?;
            Ok(json!({"schema":"motionwright.owner-local-edit-preview/1",
                "status":"READ_ONLY_DOMAIN_VALIDATION",
                "resource":project.resource_key(),
                "observed_generation":project.generation,
                "observed_revision":project.revision,
                "candidate_title":proposed.title,
                "candidate_scene_count":proposed.scenes.len(),
                "committed":false,"exact_future_ids_proven":false,
                "no_remote_host_authority":true}))
        }
        Request::Commit {
            project_id,
            expected_generation,
            expected_revision,
            request_id,
            edit,
        } => {
            if !owner_edit {
                return Err(error(
                    "Explicit owner-local --owner-edit is required for source changes",
                ));
            }
            if !(3..=96).contains(&request_id.len())
                || !request_id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"-_:.".contains(&b))
            {
                return Err(error(
                    "Commit needs one bounded explicit idempotent request key",
                ));
            }
            checked_owner_intent(&edit)?;
            require_contract(&service, &edit)?;
            let project = service.project(project_id)?;
            check_base(&project, expected_generation, expected_revision)?;
            let outcome = service.apply(
                project_id,
                &RevisionStamp::from(&project),
                &request_id,
                &edit.to_change(),
            )?;
            Ok(json!({"schema":"motionwright.owner-local-cas-result/1",
                "status":"APPLIED_BY_CANONICAL_STUDIO_SERVICE",
                "generation":outcome.project.generation,
                "revision":outcome.project.revision,
                "previous_revision":outcome.previous_revision,
                "project_id":outcome.project.id,
                "title":outcome.project.title,
                "scene_count":outcome.project.scenes.len(),
                "request_id":outcome.request_id,"replayed":outcome.replayed,
                "no_remote_host_authority":true}))
        }
    }
}
fn usage() -> ! {
    eprintln!("usage: local_agent_actor <ABSOLUTE_OWNER_DB.sqlite3> <request.json> [--owner-edit]");
    std::process::exit(2);
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args_os().skip(1);
    let database = PathBuf::from(args.next().unwrap_or_else(|| usage()));
    let request_path = PathBuf::from(args.next().unwrap_or_else(|| usage()));
    let owner_edit = match args.next() {
        None => false,
        Some(x) if x == "--owner-edit" => true,
        _ => usage(),
    };
    if args.next().is_some() {
        usage();
    }
    let request = checked_request(&request_path)?;
    checked_db(
        &database,
        matches!(request, Request::Init { .. }) && owner_edit,
    )?;
    let service = StudioService::open(database)?;
    let response = execute(service, request, owner_edit).await?;
    println!("{}", serde_json::to_string(&response)?);
    Ok(())
}
