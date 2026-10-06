use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_service::StudioService;
use serde_json::json;
use std::path::PathBuf;
use uuid::Uuid;

fn emit(project: &Project) {
    println!(
        "{}",
        json!({
            "id": project.id,
            "resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision.to_string(),
            "title": project.title,
        })
    );
}

fn usage() -> ! {
    eprintln!(
        "usage: shared-state-actor <database> <init TITLE | show PROJECT_ID | rename PROJECT_ID REQUEST_ID TITLE>"
    );
    std::process::exit(2);
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let database = args.next().map(PathBuf::from).unwrap_or_else(|| usage());
    let command = args
        .next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage());
    let service = StudioService::open(database)?;

    match command.as_str() {
        "init" => {
            let title = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            let project = service.create_project(&title)?;
            emit(&project);
        }
        "show" => {
            let project_id = args
                .next()
                .and_then(|value| value.into_string().ok())
                .and_then(|value| Uuid::parse_str(&value).ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            emit(&service.project(project_id)?);
        }
        "rename" => {
            let project_id = args
                .next()
                .and_then(|value| value.into_string().ok())
                .and_then(|value| Uuid::parse_str(&value).ok())
                .unwrap_or_else(|| usage());
            let request_id = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let title = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            let current = service.project(project_id)?;
            let outcome = service.apply(
                project_id,
                &RevisionStamp::from(&current),
                &request_id,
                &Change::RenameProject { title },
            )?;
            emit(&outcome.project);
        }
        _ => usage(),
    }

    Ok(())
}
