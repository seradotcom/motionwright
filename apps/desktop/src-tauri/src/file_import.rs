//! Durable desktop transport for local file imports.
//!
//! Preparation is metadata-only. Dispatch retains the caller's persisted request
//! identity and uses the existing one-time local import grant. Recovery reads a
//! historical receipt; callers must fetch the current Project separately.
use crate::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::{Project, RevisionStamp};
use motionwright_service::{FileImportIntent, StudioService, VoiceImportMetadata};
use serde::{Deserialize, Serialize};
use std::{fmt, path::PathBuf};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum FileImportKind {
    Asset,
    Voice,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FileImportPrepareRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub kind: FileImportKind,
    pub path: String,
    pub name: Option<String>,
    pub media_type: Option<String>,
    pub label: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct PreparedFileImport {
    pub schema: &'static str,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub path: String,
    pub intent: FileImportIntent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FileImportDispatchRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub request_id: String,
    pub effect_grant: Uuid,
    pub path: String,
    pub intent: FileImportIntent,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct FileImportReceipt {
    pub schema: &'static str,
    pub request_id: String,
    pub intent: FileImportIntent,
    pub project: Project,
    pub replayed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FileImportRecoveryRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    // This is the revision saved before dispatch, not the current head revision.
    pub revision: u64,
    pub request_id: String,
    pub intent: FileImportIntent,
}

#[derive(Debug)]
pub(crate) enum FileImportError {
    Invalid(String),
    // Keep the existing service error's Display for the desktop's sanitizer.
    // Transport does not add a second public error-disclosure policy.
    Storage(String),
}

impl fmt::Display for FileImportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(message) | Self::Storage(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for FileImportError {}

fn storage_error(error: impl fmt::Display) -> FileImportError {
    FileImportError::Storage(error.to_string())
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    generation: Uuid,
    revision: Option<u64>,
) -> Result<Project, FileImportError> {
    let project = service.project(project_id).map_err(storage_error)?;
    if project.id != project_id || project.generation != generation {
        return Err(FileImportError::Invalid(
            "File import belongs to a different project generation.".into(),
        ));
    }
    if revision.is_some_and(|revision| project.revision != revision) {
        return Err(FileImportError::Invalid(
            "Effect grant scope is stale; refresh the project before retrying.".into(),
        ));
    }
    Ok(project)
}

fn absolute_asset_path(value: &str) -> Result<PathBuf, FileImportError> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(FileImportError::Invalid(
            "Choose an absolute asset file path.".into(),
        ));
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err(FileImportError::Invalid(
            "Asset file paths must be absolute.".into(),
        ));
    }
    Ok(path)
}

fn infer_media_type(path: &std::path::Path) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "flac" => "audio/flac",
        "ogg" | "oga" => "audio/ogg",
        "m4a" | "aac" => "audio/mp4",
        "aif" | "aiff" => "audio/aiff",
        "caf" => "audio/x-caf",
        "mp4" => "video/mp4",
        "mov" => "video/quicktime",
        "webm" => "video/webm",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "glb" => "model/gltf-binary",
        "gltf" => "model/gltf+json",
        "json" => "application/json",
        "srt" => "application/x-subrip",
        "vtt" => "text/vtt",
        "txt" => "text/plain",
        _ => "application/octet-stream",
    }
}

fn validate_intent(intent: &FileImportIntent) -> Result<(), FileImportError> {
    let (name, media_type) = match intent {
        FileImportIntent::Asset { name, media_type }
        | FileImportIntent::Voice {
            name, media_type, ..
        } => (name, media_type),
    };
    // These are the domain's existing metadata bounds. Do not trim again:
    // the existing filename/stem fallbacks can legitimately contain spaces.
    if name.trim().is_empty() || name.len() > 512 {
        return Err(FileImportError::Invalid(
            "Asset name is empty or too long.".into(),
        ));
    }
    if media_type.trim().is_empty()
        || media_type.len() > 255
        || media_type.chars().any(char::is_whitespace)
    {
        return Err(FileImportError::Invalid(
            "Asset media type is empty or out of bounds.".into(),
        ));
    }
    if let FileImportIntent::Voice { label, .. } = intent {
        if !media_type.starts_with("audio/") {
            return Err(FileImportError::Invalid(
                "Voice import requires a supported audio file.".into(),
            ));
        }
        if label.trim().is_empty() || label.len() > 256 {
            return Err(FileImportError::Invalid(
                "Voice label is empty or too long.".into(),
            ));
        }
    }
    Ok(())
}

fn validate_request_id(request_id: &str) -> Result<(), FileImportError> {
    let valid = Uuid::parse_str(request_id)
        .is_ok_and(|id| !id.is_nil() && id.hyphenated().to_string() == request_id);
    if !valid {
        return Err(FileImportError::Invalid(
            "File import requires its original canonical nonnil UUID request ID.".into(),
        ));
    }
    Ok(())
}

pub(crate) fn prepare_file_import(
    service: &StudioService,
    request: FileImportPrepareRequest,
) -> Result<PreparedFileImport, FileImportError> {
    // Stamp authority precedes even pure path/metadata normalization.
    current_project(
        service,
        request.project_id,
        request.generation,
        Some(request.revision),
    )?;
    if request.kind == FileImportKind::Asset && request.label.is_some() {
        return Err(FileImportError::Invalid(
            "Asset import does not accept a voice label.".into(),
        ));
    }
    let source = absolute_asset_path(&request.path)?;
    let name = request
        .name
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            source
                .file_name()
                .and_then(|value| value.to_str())
                .map(str::to_owned)
        })
        .ok_or_else(|| {
            FileImportError::Invalid(
                match request.kind {
                    FileImportKind::Asset => "Asset file name is unavailable.",
                    FileImportKind::Voice => "Voice file name is unavailable.",
                }
                .into(),
            )
        })?;
    let media_type = request
        .media_type
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| infer_media_type(&source).to_owned());
    let intent = match request.kind {
        FileImportKind::Asset => FileImportIntent::Asset { name, media_type },
        FileImportKind::Voice => {
            let label = request
                .label
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .or_else(|| {
                    source
                        .file_stem()
                        .and_then(|value| value.to_str())
                        .map(str::to_owned)
                })
                .unwrap_or_else(|| "Voice take".into());
            FileImportIntent::Voice {
                name,
                media_type,
                label,
            }
        }
    };
    validate_intent(&intent)?;
    Ok(PreparedFileImport {
        schema: "motionwright.file-import-preparation/1",
        project_id: request.project_id,
        generation: request.generation,
        revision: request.revision,
        path: request.path.trim().to_owned(),
        intent,
    })
}

fn dispatch_file_import(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    kind: FileImportKind,
    request: FileImportDispatchRequest,
) -> Result<FileImportReceipt, FileImportError> {
    validate_request_id(&request.request_id)?;
    if !matches!(
        (kind, &request.intent),
        (FileImportKind::Asset, FileImportIntent::Asset { .. })
            | (FileImportKind::Voice, FileImportIntent::Voice { .. })
    ) {
        return Err(FileImportError::Invalid(
            "File import intent does not match the selected import command.".into(),
        ));
    }
    validate_intent(&request.intent)?;
    let project = current_project(
        service,
        request.project_id,
        request.generation,
        Some(request.revision),
    )?;
    let source = absolute_asset_path(&request.path)?;
    grants
        .consume(
            request.effect_grant,
            EffectKind::ImportLocal,
            EffectScope::project(request.project_id, request.generation, request.revision),
            request.path.trim(),
        )
        .map_err(FileImportError::Invalid)?;
    // The existing service checks scope again and owns source capture,
    // validation/measurement, CAS publication and atomic request receipts.
    let expected = project.stamp();
    let outcome = match &request.intent {
        FileImportIntent::Asset { name, media_type } => service.import_asset_file(
            request.project_id,
            &expected,
            &request.request_id,
            &source,
            name.clone(),
            media_type.clone(),
        ),
        FileImportIntent::Voice {
            name,
            media_type,
            label,
        } => service.import_voice_file(
            request.project_id,
            &expected,
            &request.request_id,
            &source,
            VoiceImportMetadata {
                name: name.clone(),
                media_type: media_type.clone(),
                label: label.clone(),
            },
        ),
    }
    .map_err(storage_error)?;
    Ok(FileImportReceipt {
        schema: "motionwright.file-import-receipt/1",
        request_id: request.request_id,
        intent: request.intent,
        project: outcome.project,
        replayed: outcome.replayed,
    })
}

pub(crate) fn import_asset_file(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: FileImportDispatchRequest,
) -> Result<FileImportReceipt, FileImportError> {
    dispatch_file_import(service, grants, FileImportKind::Asset, request)
}

pub(crate) fn import_voice_file(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: FileImportDispatchRequest,
) -> Result<FileImportReceipt, FileImportError> {
    dispatch_file_import(service, grants, FileImportKind::Voice, request)
}

pub(crate) fn recover_file_import(
    service: &StudioService,
    request: FileImportRecoveryRequest,
) -> Result<Option<FileImportReceipt>, FileImportError> {
    validate_request_id(&request.request_id)?;
    validate_intent(&request.intent)?;
    let current = current_project(service, request.project_id, request.generation, None)?;
    let original = RevisionStamp {
        resource: current.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    // Historical revision is intentional. No grant, file or CAS access here.
    let outcome = service
        .recover_file_import(
            request.project_id,
            &original,
            &request.request_id,
            &request.intent,
        )
        .map_err(storage_error)?;
    Ok(outcome.map(|outcome| FileImportReceipt {
        schema: "motionwright.file-import-receipt/1",
        request_id: request.request_id,
        intent: request.intent,
        project: outcome.project,
        replayed: outcome.replayed,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, RationalTime};
    use serde_json::{Value, json};
    use std::{collections::BTreeMap, fs, path::Path};

    struct Files {
        directory: tempfile::TempDir,
        database: PathBuf,
    }

    fn fixture() -> (Files, StudioService, Project, EffectGrantRegistry) {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("file-import.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service.create_project("Editable file imports").unwrap();
        (
            Files {
                directory,
                database,
            },
            service,
            project,
            EffectGrantRegistry::default(),
        )
    }

    fn preparation(
        project: &Project,
        kind: FileImportKind,
        path: &Path,
    ) -> FileImportPrepareRequest {
        FileImportPrepareRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            kind,
            path: path.to_str().unwrap().to_owned(),
            name: None,
            media_type: None,
            label: None,
        }
    }

    fn dispatch(
        prepared: &PreparedFileImport,
        grants: &EffectGrantRegistry,
    ) -> FileImportDispatchRequest {
        let grant = grants
            .issue(
                EffectKind::ImportLocal,
                EffectScope::project(prepared.project_id, prepared.generation, prepared.revision),
                &prepared.path,
            )
            .unwrap();
        FileImportDispatchRequest {
            project_id: prepared.project_id,
            generation: prepared.generation,
            revision: prepared.revision,
            request_id: Uuid::new_v4().to_string(),
            effect_grant: grant.token,
            path: prepared.path.clone(),
            intent: prepared.intent.clone(),
        }
    }

    fn recovery(request: &FileImportDispatchRequest) -> FileImportRecoveryRequest {
        FileImportRecoveryRequest {
            project_id: request.project_id,
            generation: request.generation,
            revision: request.revision,
            request_id: request.request_id.clone(),
            intent: request.intent.clone(),
        }
    }

    fn roundtrip<T: Serialize + for<'de> Deserialize<'de>>(value: &T) -> T {
        serde_json::from_slice(&serde_json::to_vec(value).unwrap()).unwrap()
    }

    #[derive(Debug, PartialEq)]
    struct Snapshot {
        project: Project,
        events: Value,
        payload_sha256: String,
        blobs: BTreeMap<PathBuf, Vec<u8>>,
    }

    fn blob_files(root: &Path, relative: &Path, output: &mut BTreeMap<PathBuf, Vec<u8>>) {
        let path = root.join(relative);
        let entries = match fs::read_dir(&path) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
            Err(error) => panic!("cannot inspect owned test CAS: {error}"),
        };
        for entry in entries {
            let entry = entry.unwrap();
            let relative = relative.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() {
                blob_files(root, &relative, output);
            } else {
                output.insert(relative, fs::read(entry.path()).unwrap());
            }
        }
    }

    fn snapshot(service: &StudioService, project_id: Uuid) -> Snapshot {
        let backup = service.export_project(project_id).unwrap();
        let root = service.store().lock().blob_root().to_path_buf();
        let mut blobs = BTreeMap::new();
        blob_files(&root, Path::new(""), &mut blobs);
        Snapshot {
            project: backup.project,
            events: serde_json::to_value(backup.events).unwrap(),
            payload_sha256: backup.payload_sha256,
            blobs,
        }
    }

    fn advance(service: &StudioService, project: &Project, title: &str) -> Project {
        service
            .apply(
                project.id,
                &project.stamp(),
                &Uuid::new_v4().to_string(),
                &Change::RenameProject {
                    title: title.into(),
                },
            )
            .unwrap()
            .project
    }

    fn own_pcm_wav() -> Vec<u8> {
        let sample_rate = 48_000_u32;
        let frames = 4_800_u32;
        let data_len = frames * 2;
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + data_len).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16_u32.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&sample_rate.to_le_bytes());
        bytes.extend_from_slice(&(sample_rate * 2).to_le_bytes());
        bytes.extend_from_slice(&2_u16.to_le_bytes());
        bytes.extend_from_slice(&16_u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&data_len.to_le_bytes());
        // Original PCM generated here; no external recording or rights claim.
        for index in 0..frames {
            let sample = ((index % 96) as i16 - 48) * 32;
            bytes.extend_from_slice(&sample.to_le_bytes());
        }
        bytes
    }

    #[test]
    fn preparation_is_metadata_only_and_preserves_filename_fallback_bytes() {
        let (files, service, project, _) = fixture();
        let before = snapshot(&service, project.id);
        let path = files.directory.path().join("  original take  .WaV");
        assert!(!path.exists());
        let mut request = preparation(&project, FileImportKind::Voice, &path);
        request.path = format!("  {}  ", request.path);
        request.name = Some(" \t ".into());
        request.media_type = Some(" \t ".into());
        request.label = Some(" \t ".into());
        let prepared = prepare_file_import(&service, roundtrip(&request)).unwrap();
        assert_eq!(prepared.schema, "motionwright.file-import-preparation/1");
        assert_eq!(prepared.path, path.to_str().unwrap());
        assert_eq!(prepared.project_id, project.id);
        assert_eq!(prepared.generation, project.generation);
        assert_eq!(prepared.revision, project.revision);
        assert_eq!(
            prepared.intent,
            FileImportIntent::Voice {
                name: "  original take  .WaV".into(),
                media_type: "audio/wav".into(),
                label: "  original take  ".into(),
            }
        );
        assert!(!path.exists());
        assert_eq!(snapshot(&service, project.id), before);
    }

    #[test]
    fn preparation_checks_current_stamp_before_normalizing_invalid_path() {
        let (files, service, project, _) = fixture();
        let before = snapshot(&service, project.id);
        let mut request = preparation(&project, FileImportKind::Asset, Path::new("relative"));
        request.revision += 1;
        assert!(
            prepare_file_import(&service, request.clone())
                .unwrap_err()
                .to_string()
                .contains("stale")
        );
        request.revision = project.revision;
        request.generation = Uuid::new_v4();
        assert!(
            prepare_file_import(&service, request.clone())
                .unwrap_err()
                .to_string()
                .contains("generation")
        );
        request.project_id = Uuid::new_v4();
        assert!(
            matches!(prepare_file_import(&service, request), Err(FileImportError::Storage(message)) if message.contains("not found"))
        );
        assert_eq!(snapshot(&service, project.id), before);
        assert!(files.database.is_file());
    }

    #[test]
    fn preparation_reuses_complete_mime_map_and_trims_only_supplied_metadata() {
        let (files, service, project, _) = fixture();
        let mappings = [
            ("wav", "audio/wav"),
            ("mp3", "audio/mpeg"),
            ("flac", "audio/flac"),
            ("ogg", "audio/ogg"),
            ("oga", "audio/ogg"),
            ("m4a", "audio/mp4"),
            ("aac", "audio/mp4"),
            ("aif", "audio/aiff"),
            ("aiff", "audio/aiff"),
            ("caf", "audio/x-caf"),
            ("mp4", "video/mp4"),
            ("mov", "video/quicktime"),
            ("webm", "video/webm"),
            ("png", "image/png"),
            ("jpg", "image/jpeg"),
            ("jpeg", "image/jpeg"),
            ("webp", "image/webp"),
            ("svg", "image/svg+xml"),
            ("glb", "model/gltf-binary"),
            ("gltf", "model/gltf+json"),
            ("json", "application/json"),
            ("srt", "application/x-subrip"),
            ("vtt", "text/vtt"),
            ("txt", "text/plain"),
            ("unknown", "application/octet-stream"),
        ];
        for (extension, expected) in mappings {
            let path = files
                .directory
                .path()
                .join(format!("original.{}", extension.to_ascii_uppercase()));
            let prepared = prepare_file_import(
                &service,
                preparation(&project, FileImportKind::Asset, &path),
            )
            .unwrap();
            assert_eq!(
                prepared.intent,
                FileImportIntent::Asset {
                    name: path.file_name().unwrap().to_str().unwrap().into(),
                    media_type: expected.into(),
                }
            );
            assert!(!path.exists());
        }
        let mut request = preparation(
            &project,
            FileImportKind::Voice,
            &files.directory.path().join("original.bin"),
        );
        request.name = Some("  Edited voice  ".into());
        request.media_type = Some("  audio/wav  ".into());
        request.label = Some("  Narration ES  ".into());
        assert_eq!(
            prepare_file_import(&service, request).unwrap().intent,
            FileImportIntent::Voice {
                name: "Edited voice".into(),
                media_type: "audio/wav".into(),
                label: "Narration ES".into(),
            }
        );
    }

    #[test]
    fn preparation_rejects_invalid_paths_types_and_metadata_bounds() {
        let (files, service, project, _) = fixture();
        let before = snapshot(&service, project.id);
        for path in ["", "   ", "relative.wav", "./relative.wav"] {
            assert!(
                prepare_file_import(
                    &service,
                    preparation(&project, FileImportKind::Voice, Path::new(path))
                )
                .is_err()
            );
        }
        let request = preparation(
            &project,
            FileImportKind::Voice,
            &files.directory.path().join("original.wav"),
        );
        for invalid in [
            FileImportPrepareRequest {
                name: Some("n".repeat(513)),
                ..request.clone()
            },
            FileImportPrepareRequest {
                media_type: Some("audio/wa v".into()),
                ..request.clone()
            },
            FileImportPrepareRequest {
                media_type: Some("audio/wa\u{2003}v".into()),
                ..request.clone()
            },
            FileImportPrepareRequest {
                media_type: Some("a".repeat(256)),
                ..request.clone()
            },
            FileImportPrepareRequest {
                media_type: Some("text/plain".into()),
                ..request.clone()
            },
            FileImportPrepareRequest {
                label: Some("l".repeat(257)),
                ..request.clone()
            },
        ] {
            assert!(matches!(
                prepare_file_import(&service, invalid),
                Err(FileImportError::Invalid(_))
            ));
        }
        let mut wire = serde_json::to_value(&request).unwrap();
        wire["kind"] = json!("render");
        assert!(serde_json::from_value::<FileImportPrepareRequest>(wire).is_err());
        let mut wire = serde_json::to_value(&request).unwrap();
        wire["effect_grant"] = json!(Uuid::new_v4());
        assert!(serde_json::from_value::<FileImportPrepareRequest>(wire).is_err());
        let valid = FileImportPrepareRequest {
            name: Some("n".repeat(512)),
            media_type: Some(format!("audio/{}", "a".repeat(249))),
            label: Some("l".repeat(256)),
            ..request
        };
        assert!(prepare_file_import(&service, valid).is_ok());
        let mut asset = preparation(
            &project,
            FileImportKind::Asset,
            &files.directory.path().join("original.txt"),
        );
        asset.label = Some("Ignored intent must be rejected".into());
        assert!(prepare_file_import(&service, asset).is_err());
        assert_eq!(snapshot(&service, project.id), before);
    }

    #[test]
    fn dispatch_and_recovery_require_original_canonical_uuid_on_the_wire() {
        let (files, service, project, grants) = fixture();
        let path = files.directory.path().join("missing.txt");
        let prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Asset, &path),
        )
        .unwrap();
        let request = dispatch(&prepared, &grants);
        let before = snapshot(&service, project.id);
        let mut absent = serde_json::to_value(&request).unwrap();
        absent.as_object_mut().unwrap().remove("request_id");
        assert!(serde_json::from_value::<FileImportDispatchRequest>(absent).is_err());
        let mut absent = serde_json::to_value(recovery(&request)).unwrap();
        absent.as_object_mut().unwrap().remove("request_id");
        assert!(serde_json::from_value::<FileImportRecoveryRequest>(absent).is_err());
        let fixed = Uuid::parse_str("9f2a6bc1-586d-4f19-9310-b3576c7cdf92").unwrap();
        for id in [
            String::new(),
            "missing".into(),
            Uuid::nil().to_string(),
            fixed.to_string().to_ascii_uppercase(),
            fixed.simple().to_string(),
            format!("urn:uuid:{fixed}"),
            format!(" {fixed} "),
        ] {
            let invalid = FileImportDispatchRequest {
                request_id: id,
                ..request.clone()
            };
            assert!(matches!(
                import_asset_file(&service, &grants, roundtrip(&invalid)),
                Err(FileImportError::Invalid(_))
            ));
            assert!(matches!(
                recover_file_import(&service, roundtrip(&recovery(&invalid))),
                Err(FileImportError::Invalid(_))
            ));
        }
        // All rejections happened before consuming even this unrelated valid grant.
        grants
            .consume(
                request.effect_grant,
                EffectKind::ImportLocal,
                EffectScope::project(project.id, project.generation, project.revision),
                &prepared.path,
            )
            .unwrap();
        assert_eq!(snapshot(&service, project.id), before);
    }

    #[test]
    fn dispatch_rejects_command_mismatch_and_metadata_without_consuming_grant() {
        let (files, service, project, grants) = fixture();
        let path = files.directory.path().join("original.txt");
        fs::write(&path, b"An original editable reference.").unwrap();
        let prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Asset, &path),
        )
        .unwrap();
        let request = dispatch(&prepared, &grants);
        let before = snapshot(&service, project.id);
        assert!(
            import_voice_file(&service, &grants, request.clone())
                .unwrap_err()
                .to_string()
                .contains("selected import command")
        );
        for intent in [
            FileImportIntent::Asset {
                name: " ".into(),
                media_type: "text/plain".into(),
            },
            FileImportIntent::Asset {
                name: "n".repeat(513),
                media_type: "text/plain".into(),
            },
            FileImportIntent::Asset {
                name: "original".into(),
                media_type: " text/plain".into(),
            },
            FileImportIntent::Asset {
                name: "original".into(),
                media_type: "t".repeat(256),
            },
        ] {
            assert!(matches!(
                import_asset_file(
                    &service,
                    &grants,
                    FileImportDispatchRequest {
                        intent,
                        ..request.clone()
                    }
                ),
                Err(FileImportError::Invalid(_))
            ));
        }
        assert_eq!(snapshot(&service, project.id), before);
        let receipt = import_asset_file(&service, &grants, roundtrip(&request)).unwrap();
        assert_eq!(receipt.request_id, request.request_id);
        assert_eq!(receipt.project.revision, project.revision + 1);
    }

    #[test]
    fn dispatch_rejects_stale_scope_before_source_io_and_preserves_unconsumed_grant() {
        let (files, service, project, grants) = fixture();
        let path = files.directory.path().join("missing.txt");
        let prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Asset, &path),
        )
        .unwrap();
        let request = dispatch(&prepared, &grants);
        let head = advance(&service, &project, "Later human revision");
        let before = snapshot(&service, project.id);
        assert!(
            matches!(import_asset_file(&service, &grants, request.clone()), Err(FileImportError::Invalid(message)) if message.contains("stale"))
        );
        grants
            .consume(
                request.effect_grant,
                EffectKind::ImportLocal,
                EffectScope::project(project.id, project.generation, project.revision),
                &prepared.path,
            )
            .unwrap();
        let current =
            prepare_file_import(&service, preparation(&head, FileImportKind::Asset, &path))
                .unwrap();
        let valid = dispatch(&current, &grants);
        let wrong_generation = FileImportDispatchRequest {
            generation: Uuid::new_v4(),
            ..valid.clone()
        };
        assert!(
            matches!(import_asset_file(&service, &grants, wrong_generation), Err(FileImportError::Invalid(message)) if message.contains("generation"))
        );
        let missing_project = FileImportDispatchRequest {
            project_id: Uuid::new_v4(),
            ..valid.clone()
        };
        assert!(
            matches!(import_asset_file(&service, &grants, missing_project), Err(FileImportError::Storage(message)) if message.contains("not found"))
        );
        grants
            .consume(
                valid.effect_grant,
                EffectKind::ImportLocal,
                EffectScope::project(head.id, head.generation, head.revision),
                &current.path,
            )
            .unwrap();
        assert_eq!(snapshot(&service, project.id), before);
        assert!(!path.exists());
    }

    #[test]
    fn dispatch_uses_existing_exact_path_scope_and_one_time_grants() {
        let (files, service, project, grants) = fixture();
        let path = files.directory.path().join("original.txt");
        fs::write(&path, b"Original composition reference.").unwrap();
        let prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Asset, &path),
        )
        .unwrap();
        let scope = EffectScope::project(project.id, project.generation, project.revision);
        let before = snapshot(&service, project.id);
        let grants_to_reject = [
            grants
                .issue(
                    EffectKind::ImportLocal,
                    scope,
                    &format!("{}.other", prepared.path),
                )
                .unwrap()
                .token,
            grants
                .issue(
                    EffectKind::ImportLocal,
                    EffectScope::unscoped_import(),
                    &prepared.path,
                )
                .unwrap()
                .token,
            grants
                .issue(
                    EffectKind::ImportLocal,
                    EffectScope::project(project.id, Uuid::new_v4(), project.revision),
                    &prepared.path,
                )
                .unwrap()
                .token,
            grants
                .issue(EffectKind::ProjectEdit, scope, &prepared.path)
                .unwrap()
                .token,
            Uuid::new_v4(),
        ];
        for effect_grant in grants_to_reject {
            let request = FileImportDispatchRequest {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: Uuid::new_v4().to_string(),
                effect_grant,
                path: prepared.path.clone(),
                intent: prepared.intent.clone(),
            };
            assert!(
                matches!(import_asset_file(&service, &grants, request.clone()), Err(FileImportError::Invalid(message)) if message.contains("Effect grant"))
            );
            assert!(import_asset_file(&service, &grants, request).is_err());
            assert_eq!(snapshot(&service, project.id), before);
        }
        let request = dispatch(&prepared, &grants);
        let receipt = import_asset_file(&service, &grants, request.clone()).unwrap();
        assert!(!receipt.replayed);
        assert!(
            grants
                .consume(
                    request.effect_grant,
                    EffectKind::ImportLocal,
                    scope,
                    &request.path
                )
                .is_err()
        );
        let after = snapshot(&service, project.id);
        let reused = FileImportDispatchRequest {
            revision: receipt.project.revision,
            ..request
        };
        assert!(
            matches!(import_asset_file(&service, &grants, reused), Err(FileImportError::Invalid(message)) if message.contains("Effect grant"))
        );
        assert_eq!(snapshot(&service, project.id), after);
    }

    #[test]
    fn real_asset_and_own_pcm_voice_imports_preserve_prepared_intent_in_sqlite_and_cas() {
        let (files, service, project, grants) = fixture();
        let asset_path = files.directory.path().join("  visual direction  .txt");
        let asset_bytes = b"Original direction: warm ground, readable title, restrained motion.";
        fs::write(&asset_path, asset_bytes).unwrap();
        let asset_prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Asset, &asset_path),
        )
        .unwrap();
        let asset_request = roundtrip(&dispatch(&asset_prepared, &grants));
        let asset = import_asset_file(&service, &grants, asset_request.clone()).unwrap();
        assert_eq!(asset.schema, "motionwright.file-import-receipt/1");
        assert_eq!(asset.request_id, asset_request.request_id);
        assert_eq!(asset.intent, asset_prepared.intent);
        assert!(!asset.replayed);
        assert_eq!(asset.project.assets.len(), project.assets.len() + 1);
        let imported_asset = asset.project.assets.last().unwrap();
        assert_eq!(imported_asset.name, "  visual direction  .txt");
        let digest = imported_asset.content_sha256.as_ref().unwrap();
        assert_eq!(
            service.store().lock().read_blob(digest, 64 * 1024).unwrap(),
            asset_bytes
        );

        let voice_path = files.directory.path().join("  original voice  .WAV");
        let voice_bytes = own_pcm_wav();
        fs::write(&voice_path, &voice_bytes).unwrap();
        let prepared = prepare_file_import(
            &service,
            preparation(&asset.project, FileImportKind::Voice, &voice_path),
        )
        .unwrap();
        let request = roundtrip(&dispatch(&prepared, &grants));
        let voice = import_voice_file(&service, &grants, request.clone()).unwrap();
        assert_eq!(voice.intent, prepared.intent);
        assert_eq!(voice.request_id, request.request_id);
        assert_eq!(voice.project.revision, request.revision + 1);
        assert!(!voice.replayed);
        let track = voice.project.audio.voice_tracks.last().unwrap();
        assert_eq!(track.label, "  original voice  ");
        assert_eq!(track.sample_rate_hz, 48_000);
        assert_eq!(track.channels, 1);
        assert_eq!(track.measured_duration, RationalTime::new(1, 10).unwrap());
        let measured_asset = voice
            .project
            .assets
            .iter()
            .find(|asset| asset.id == track.asset_id)
            .unwrap();
        assert_eq!(measured_asset.name, "  original voice  .WAV");
        assert_eq!(
            measured_asset.content_sha256.as_ref().unwrap(),
            &track.source_sha256
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&track.source_sha256, 64 * 1024)
                .unwrap(),
            voice_bytes
        );
        let expected = snapshot(&service, project.id);
        drop(service);
        let reopened = StudioService::open(&files.database).unwrap();
        assert_eq!(snapshot(&reopened, project.id), expected);
        assert_eq!(
            recover_file_import(&reopened, recovery(&request))
                .unwrap()
                .unwrap()
                .project,
            voice.project
        );
    }

    #[test]
    fn lost_ack_recovery_after_later_revisions_and_reopen_never_reimports_or_rewinds_head() {
        let (files, service, project, grants) = fixture();
        let asset_path = files.directory.path().join("  original direction.txt");
        let voice_path = files.directory.path().join("  original narration  .wav");
        fs::write(
            &asset_path,
            b"Original editable reference; retained decisions.",
        )
        .unwrap();
        fs::write(&voice_path, own_pcm_wav()).unwrap();
        let asset_prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Asset, &asset_path),
        )
        .unwrap();
        let asset_request = dispatch(&asset_prepared, &grants);
        // The command commits; the frontend's simulated lost acknowledgement
        // retains only the serialized pre-dispatch recovery request.
        let asset = import_asset_file(&service, &grants, roundtrip(&asset_request)).unwrap();
        let saved_asset_intent = serde_json::to_vec(&recovery(&asset_request)).unwrap();
        let voice_prepared = prepare_file_import(
            &service,
            preparation(&asset.project, FileImportKind::Voice, &voice_path),
        )
        .unwrap();
        let voice_request = dispatch(&voice_prepared, &grants);
        let voice = import_voice_file(&service, &grants, roundtrip(&voice_request)).unwrap();
        let saved_voice_intent = serde_json::to_vec(&recovery(&voice_request)).unwrap();
        let mut head = voice.project.clone();
        for title in [
            "First later human revision",
            "Second later human revision",
            "Third later human revision",
        ] {
            head = advance(&service, &head, title);
        }
        let mut removed_blobs = Vec::new();
        for imported in &voice.project.assets {
            if let Some(digest) = &imported.content_sha256 {
                let blob = service.store().lock().verified_blob_path(digest).unwrap();
                fs::remove_file(&blob).unwrap();
                removed_blobs.push(blob);
            }
        }
        fs::remove_file(&asset_path).unwrap();
        fs::remove_file(&voice_path).unwrap();
        drop(service);
        let reopened = StudioService::open(&files.database).unwrap();
        let before = snapshot(&reopened, project.id);
        for _ in 0..3 {
            let asset_result = recover_file_import(
                &reopened,
                serde_json::from_slice(&saved_asset_intent).unwrap(),
            )
            .unwrap()
            .unwrap();
            let voice_result = recover_file_import(
                &reopened,
                serde_json::from_slice(&saved_voice_intent).unwrap(),
            )
            .unwrap()
            .unwrap();
            for (result, original) in [(&asset_result, &asset), (&voice_result, &voice)] {
                assert!(result.replayed);
                assert_eq!(result.schema, "motionwright.file-import-receipt/1");
                assert_eq!(result.request_id, original.request_id);
                assert_eq!(result.intent, original.intent);
                assert_eq!(result.project, original.project);
                assert!(result.project.revision < head.revision);
            }
            assert_eq!(reopened.project(project.id).unwrap(), head);
            assert_eq!(snapshot(&reopened, project.id), before);
        }
        assert!(!asset_path.exists());
        assert!(!voice_path.exists());
        assert!(removed_blobs.iter().all(|path| !path.exists()));
        assert!(before.blobs.is_empty());
    }

    #[test]
    fn changed_payload_reuses_no_asset_or_track_and_keeps_original_receipt_authoritative() {
        let (files, service, project, grants) = fixture();
        let path = files.directory.path().join("original.wav");
        fs::write(&path, own_pcm_wav()).unwrap();
        let prepared = prepare_file_import(
            &service,
            preparation(&project, FileImportKind::Voice, &path),
        )
        .unwrap();
        let request = dispatch(&prepared, &grants);
        let receipt = import_voice_file(&service, &grants, request.clone()).unwrap();
        fs::remove_file(&path).unwrap();
        let before = snapshot(&service, project.id);
        let changed_intents = [
            FileImportIntent::Voice {
                name: "changed.wav".into(),
                media_type: "audio/wav".into(),
                label: "original".into(),
            },
            FileImportIntent::Voice {
                name: "original.wav".into(),
                media_type: "audio/flac".into(),
                label: "original".into(),
            },
            FileImportIntent::Voice {
                name: "original.wav".into(),
                media_type: "audio/wav".into(),
                label: "Changed human label".into(),
            },
            FileImportIntent::Asset {
                name: "original.wav".into(),
                media_type: "audio/wav".into(),
            },
        ];
        for intent in changed_intents {
            let recovery = FileImportRecoveryRequest {
                intent: intent.clone(),
                ..recovery(&request)
            };
            assert!(
                matches!(recover_file_import(&service, recovery), Err(FileImportError::Storage(message)) if message.contains("request id was reused"))
            );
            let token = grants
                .issue(
                    EffectKind::ImportLocal,
                    EffectScope::project(project.id, project.generation, receipt.project.revision),
                    &request.path,
                )
                .unwrap()
                .token;
            let changed = FileImportDispatchRequest {
                revision: receipt.project.revision,
                effect_grant: token,
                intent: intent.clone(),
                ..request.clone()
            };
            let result = match intent {
                FileImportIntent::Asset { .. } => import_asset_file(&service, &grants, changed),
                FileImportIntent::Voice { .. } => import_voice_file(&service, &grants, changed),
            };
            assert!(
                matches!(result, Err(FileImportError::Storage(message)) if message.contains("request id was reused"))
            );
            assert_eq!(snapshot(&service, project.id), before);
        }
        let original = recover_file_import(&service, recovery(&request))
            .unwrap()
            .unwrap();
        assert_eq!(original.project, receipt.project);
        assert_eq!(original.intent, receipt.intent);
        assert!(!path.exists());
    }

    #[test]
    fn missing_receipt_and_wrong_generation_are_read_only_without_grant_or_source() {
        let (files, service, project, _) = fixture();
        let request = FileImportRecoveryRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: Uuid::new_v4().to_string(),
            intent: FileImportIntent::Asset {
                name: "missing.txt".into(),
                media_type: "text/plain".into(),
            },
        };
        let head = advance(&service, &project, "Later project head with no import");
        let before = snapshot(&service, project.id);
        assert!(
            recover_file_import(&service, roundtrip(&request))
                .unwrap()
                .is_none()
        );
        let wrong_generation = FileImportRecoveryRequest {
            generation: Uuid::new_v4(),
            ..request.clone()
        };
        assert!(
            matches!(recover_file_import(&service, wrong_generation), Err(FileImportError::Invalid(message)) if message.contains("generation"))
        );
        let missing_project = FileImportRecoveryRequest {
            project_id: Uuid::new_v4(),
            ..request
        };
        assert!(
            matches!(recover_file_import(&service, missing_project), Err(FileImportError::Storage(message)) if message.contains("not found"))
        );
        assert_eq!(snapshot(&service, project.id), before);
        assert_eq!(service.project(project.id).unwrap(), head);
        assert!(!files.directory.path().join("missing.txt").exists());
    }

    #[test]
    fn undecodable_voice_and_active_svg_fail_real_service_without_published_content() {
        let (files, service, project, grants) = fixture();
        let before = snapshot(&service, project.id);
        let cases = [
            (
                "invalid.wav",
                FileImportKind::Voice,
                b"This is not decoded PCM audio.".as_slice(),
            ),
            (
                "active.svg",
                FileImportKind::Asset,
                b"<svg xmlns=\"http://www.w3.org/2000/svg\"><script>alert(1)</script></svg>"
                    .as_slice(),
            ),
        ];
        for (name, kind, bytes) in cases {
            let path = files.directory.path().join(name);
            fs::write(&path, bytes).unwrap();
            let prepared =
                prepare_file_import(&service, preparation(&project, kind, &path)).unwrap();
            let request = dispatch(&prepared, &grants);
            let result = match kind {
                FileImportKind::Asset => import_asset_file(&service, &grants, request.clone()),
                FileImportKind::Voice => import_voice_file(&service, &grants, request.clone()),
            };
            assert!(matches!(result, Err(FileImportError::Storage(_))));
            assert!(
                grants
                    .consume(
                        request.effect_grant,
                        EffectKind::ImportLocal,
                        EffectScope::project(project.id, project.generation, project.revision),
                        &prepared.path
                    )
                    .is_err()
            );
            assert!(
                recover_file_import(&service, recovery(&request))
                    .unwrap()
                    .is_none()
            );
            assert_eq!(snapshot(&service, project.id), before);
        }
    }
}
