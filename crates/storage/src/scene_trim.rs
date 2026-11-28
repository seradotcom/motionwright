use crate::{
    Change, Connection, DomainError, OptionalExtension, Project, Result, RevisionStamp,
    StorageError, Store, Uuid, apply_receipt_binding, load_project_from_row, params,
};
use motionwright_domain::SceneTrimSource;

fn unavailable() -> DomainError {
    DomainError::Invalid("The original scene trim receipt is unavailable on this branch.".into())
}

fn original_reverse(
    conn: &Connection,
    current: &Project,
    scene_id: Uuid,
    source_revision: u64,
    request_id: Uuid,
) -> Result<Change> {
    if source_revision == 0 || source_revision > current.revision || request_id.is_nil() {
        return Err(unavailable().into());
    }
    let revision = i64::try_from(source_revision).map_err(|_| unavailable())?;
    let payload: String = conn
        .query_row(
            "SELECT payload_json FROM events WHERE project_id=?1 AND revision=?2 AND kind='change'",
            params![current.id.to_string(), revision],
            |row| row.get(0),
        )
        .optional()?
        .ok_or_else(unavailable)?;
    let change: Change = serde_json::from_str(&payload)?;
    let source = match &change {
        Change::TrimSceneEnd {
            scene_id: original,
            source: Some(source),
            ..
        }
        | Change::ReverseSceneTrim {
            scene_id: original,
            source,
            ..
        } if *original == scene_id => source,
        _ => return Err(unavailable().into()),
    };
    let original_id = source.request_id.to_string();
    let receipt = apply_receipt_binding::read_receipt(conn, current.id, &original_id)
        .map_err(apply_receipt_binding::ReceiptCheckError::into_storage_error)?
        .ok_or_else(unavailable)?;
    let checked = apply_receipt_binding::check_receipt(conn, current, &original_id, &receipt)
        .map_err(apply_receipt_binding::ReceiptCheckError::into_storage_error)?;
    let branch = current.active_branch.to_string();
    let result = checked.outcome.project;
    if checked.change != change
        || result.revision != source_revision
        || checked
            .binding
            .is_none_or(|binding| binding.base_branch != branch || binding.result_branch != branch)
    {
        return Err(unavailable().into());
    }
    let tail = |project: &Project| project.scenes.iter().position(|scene| scene.id == scene_id);
    let old_index = tail(&result).ok_or_else(unavailable)?;
    let current_index = tail(current).ok_or_else(unavailable)?;
    let old_tail = &result.scenes[old_index..];
    let current_tail = &current.scenes[current_index..];
    if old_tail.len() != current_tail.len()
        || old_tail.iter().zip(current_tail).any(|(old, new)| {
            old.id != new.id || old.start != new.start || old.duration != new.duration
        })
    {
        return Err(DomainError::Invalid(
            "The affected scene timing changed after the original trim.".into(),
        )
        .into());
    }
    Ok(Change::ReverseSceneTrim {
        scene_id,
        source_revision,
        source: SceneTrimSource {
            request_id,
            duration: current_tail[0].duration,
        },
        duration: source.duration,
    })
}

pub(super) fn verify_scene_trim(
    conn: &Connection,
    project: &Project,
    request_id: &str,
    change: &Change,
) -> Result<()> {
    let source = match change {
        Change::TrimSceneEnd {
            source: Some(source),
            ..
        }
        | Change::ReverseSceneTrim { source, .. } => source,
        _ => return Ok(()),
    };
    if source.request_id.to_string() != request_id {
        return Err(DomainError::Invalid(
            "The scene trim source belongs to a different request.".into(),
        )
        .into());
    }
    if let Change::ReverseSceneTrim {
        scene_id,
        source_revision,
        ..
    } = change
        && original_reverse(
            conn,
            project,
            *scene_id,
            *source_revision,
            source.request_id,
        )? != *change
    {
        return Err(DomainError::Invalid(
            "The scene trim reversal differs from the original receipt.".into(),
        )
        .into());
    }
    Ok(())
}

impl Store {
    pub fn prepare_scene_trim_reverse(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        branch_id: Uuid,
        scene_id: Uuid,
        source_revision: u64,
        request_id: Uuid,
    ) -> Result<(Project, Change)> {
        let tx = self.conn.unchecked_transaction()?;
        let project = load_project_from_row(&tx, project_id)?;
        if project.resource_key() != expected.resource
            || project.generation != expected.generation
            || project.active_branch != branch_id
        {
            return Err(StorageError::GenerationConflict);
        }
        if project.revision != expected.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: project.revision,
            });
        }
        let change = original_reverse(&tx, &project, scene_id, source_revision, request_id)?;
        tx.commit()?;
        Ok((project, change))
    }
}
