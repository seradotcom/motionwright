//! Read-only original imported media; no Native receipt or export authority.
use motionwright_domain::{Project, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_service::{ASSET_MEDIA_RANGE_BYTES, VerifiedAssetMedia};
use serde::{Deserialize, Serialize};
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};
use uuid::Uuid;

pub const SCHEME: &str = "motionwright-asset";
const MAX_STREAMS: usize = 8;
const UNAVAILABLE: &str =
    "The original imported media is unavailable at this saved project revision.";

/// Parsed native URI parts only. The Desktop owner authorizes before this
/// selector is interpreted; the selector itself conveys no authority.
pub(crate) fn stream_token(
    scheme: Option<&str>,
    authority: Option<&str>,
    path: &str,
    has_query: bool,
) -> Result<Uuid, ()> {
    if !((scheme == Some(SCHEME) && authority == Some("localhost"))
        || (scheme == Some("http") && authority == Some("motionwright-asset.localhost")))
        || has_query
    {
        return Err(());
    }
    let value = path.strip_prefix('/').ok_or(())?;
    let token = Uuid::parse_str(value).map_err(|_| ())?;
    if token.to_string() != value {
        return Err(());
    }
    Ok(token)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub asset_id: Uuid,
    pub sha256: String,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloseRequest {
    pub stream_token: Uuid,
}

#[derive(Clone, Debug, Serialize)]
pub struct Descriptor {
    pub schema: &'static str,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub asset_id: Uuid,
    pub stream_token: Uuid,
    pub url: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub max_range_bytes: u64,
    pub media_type: &'static str,
}
impl Descriptor {
    fn current(&self, project: &Project) -> bool {
        project.id == self.project_id
            && project.generation == self.generation
            && project.revision == self.revision
            && project.assets.iter().any(|asset| {
                asset.id == self.asset_id
                    && asset.content_sha256.as_deref() == Some(&self.sha256)
                    && (asset.media_type == self.media_type
                        || self.media_type == "audio/wav"
                            && ["audio/x-wav", "audio/wave"].contains(&asset.media_type.as_str()))
            })
    }
}
struct Entry {
    descriptor: Descriptor,
    source: Mutex<VerifiedAssetMedia>,
}
#[derive(Clone, Default)]
pub struct AssetPreviews {
    entries: Arc<Mutex<VecDeque<Arc<Entry>>>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RangeRequest {
    pub stream_token: Uuid,
    pub method: String,
    pub range: Option<String>,
}
#[derive(Debug, Serialize)]
pub struct RangeResponse {
    pub status: u16,
    pub media_type: &'static str,
    pub content_length: u64,
    pub content_range: Option<String>,
    pub body: Vec<u8>,
}

impl AssetPreviews {
    pub fn prepare(
        &self,
        service: &StudioService,
        request: &OpenRequest,
    ) -> Result<Descriptor, String> {
        let stamp = RevisionStamp {
            resource: format!("project:{}", request.project_id),
            generation: request.generation,
            revision: request.revision,
        };
        let source = service
            .verified_asset_media(
                request.project_id,
                &stamp,
                request.asset_id,
                &request.sha256,
            )
            .map_err(|_| UNAVAILABLE)?;
        let token = Uuid::new_v4();
        let descriptor = Descriptor {
            schema: "motionwright.original-asset-preview/1",
            project_id: request.project_id,
            generation: source.stamp().generation,
            revision: source.stamp().revision,
            asset_id: source.asset_id(),
            stream_token: token,
            url: if cfg!(any(target_os = "windows", target_os = "android")) {
                format!("http://motionwright-asset.localhost/{token}")
            } else {
                format!("{SCHEME}://localhost/{token}")
            },
            sha256: source.sha256().to_owned(),
            size_bytes: source.size_bytes(),
            max_range_bytes: ASSET_MEDIA_RANGE_BYTES,
            media_type: source.media_kind().media_type(),
        };
        let entry = Arc::new(Entry {
            descriptor: descriptor.clone(),
            source: Mutex::new(source),
        });
        let mut entries = self.entries.lock().map_err(|_| UNAVAILABLE)?;
        if entries.len() >= MAX_STREAMS {
            entries.pop_front();
        }
        entries.push_back(entry);
        Ok(descriptor)
    }

    pub fn close(&self, token: Uuid) {
        if let Ok(mut entries) = self.entries.lock() {
            entries.retain(|entry| entry.descriptor.stream_token != token);
        }
    }

    fn find(&self, token: Uuid) -> Result<Arc<Entry>, u16> {
        self.entries
            .lock()
            .map_err(|_| 503u16)?
            .iter()
            .find(|entry| entry.descriptor.stream_token == token)
            .cloned()
            .ok_or(410)
    }

    pub fn read(
        &self,
        service: &StudioService,
        request: &RangeRequest,
    ) -> Result<RangeResponse, u16> {
        if request.method != "GET" && request.method != "HEAD" {
            return Err(405);
        }
        let entry = self.find(request.stream_token)?;
        let descriptor = &entry.descriptor;
        let project = service.project(descriptor.project_id).map_err(|_| 410u16)?;
        if !descriptor.current(&project) {
            return Err(410);
        }
        let (start, end, partial) = match byte_range(
            request.range.as_deref(),
            descriptor.size_bytes,
            request.method == "HEAD",
        ) {
            Ok(value) => value,
            Err(()) => {
                return Ok(RangeResponse {
                    status: 416,
                    media_type: descriptor.media_type,
                    content_length: 0,
                    content_range: Some(format!("bytes */{}", descriptor.size_bytes)),
                    body: Vec::new(),
                });
            }
        };
        let body = if request.method == "HEAD" {
            Vec::new()
        } else {
            entry
                .source
                .lock()
                .map_err(|_| 503u16)?
                .read_range(start, end)
                .map_err(|_| 410u16)?
        };
        let current = self.find(request.stream_token)?;
        if !Arc::ptr_eq(&entry, &current) {
            return Err(410);
        }
        let latest = service.project(descriptor.project_id).map_err(|_| 410u16)?;
        if !descriptor.current(&latest) {
            return Err(410);
        }
        Ok(RangeResponse {
            status: if partial { 206 } else { 200 },
            media_type: descriptor.media_type,
            content_length: end - start + 1,
            content_range: partial
                .then(|| format!("bytes {start}-{end}/{}", descriptor.size_bytes)),
            body,
        })
    }
}

fn byte_range(raw: Option<&str>, size: u64, head: bool) -> Result<(u64, u64, bool), ()> {
    if size == 0 {
        return Err(());
    }
    let Some(raw) = raw else {
        return if head || size <= ASSET_MEDIA_RANGE_BYTES {
            Ok((0, size - 1, false))
        } else {
            Err(())
        };
    };
    if raw.len() > 96 {
        return Err(());
    }
    let (first, last) = raw
        .strip_prefix("bytes=")
        .ok_or(())?
        .split_once('-')
        .ok_or(())?;
    let decimal = |value: &str| -> Result<u64, ()> {
        if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err(());
        }
        value.parse().map_err(|_| ())
    };
    let (start, end) = if first.is_empty() {
        let suffix = decimal(last)?;
        if suffix == 0 {
            return Err(());
        }
        (size.saturating_sub(suffix), size - 1)
    } else {
        (
            decimal(first)?,
            if last.is_empty() {
                size - 1
            } else {
                decimal(last)?.min(size - 1)
            },
        )
    };
    if start >= size || end < start {
        return Err(());
    }
    Ok((
        start,
        end.min(start.saturating_add(ASSET_MEDIA_RANGE_BYTES - 1)),
        true,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn original_asset_protocol_rejects_remote_hosts_paths_queries_and_native_master_tokens() {
        let id = Uuid::parse_str("e6fa0306-5606-42ec-9e07-56cbd17fa6ac").unwrap();
        let path = format!("/{id}");
        for (scheme, authority) in [
            (Some(SCHEME), Some("localhost")),
            (Some("http"), Some("motionwright-asset.localhost")),
        ] {
            assert_eq!(stream_token(scheme, authority, &path, false), Ok(id));
        }
        for (scheme, authority, query) in [
            (Some("https"), Some("motionwright-asset.localhost"), false),
            (Some("http"), Some("motionwright-asset.localhost:80"), false),
            (
                Some("http"),
                Some("motionwright-asset.localhost.evil"),
                false,
            ),
            (Some("motionwright-media"), Some("localhost"), false),
            (Some(SCHEME), Some("localhost"), true),
            (None, Some("localhost"), false),
            (Some(SCHEME), None, false),
        ] {
            assert!(stream_token(scheme, authority, &path, query).is_err());
        }
        for wrong in [
            format!("/{id}/extra"),
            format!("/{id}?path=private"),
            format!("/{}", id.simple()),
            format!("/{}", id.to_string().to_uppercase()),
            "/../private".into(),
            id.to_string(),
        ] {
            assert!(stream_token(Some(SCHEME), Some("localhost"), &wrong, false).is_err());
        }
    }
    #[test]
    fn original_media_selection_is_closed_and_ranges_remain_bounded() {
        let selector = serde_json::json!({"project_id":Uuid::new_v4(),"generation":Uuid::new_v4(),
            "revision":1,"asset_id":Uuid::new_v4(),"sha256":"ab".repeat(32)});
        assert!(serde_json::from_value::<OpenRequest>(selector.clone()).is_ok());
        for key in [
            "path",
            "bytes",
            "media_type",
            "effect_grant",
            "export_token",
            "receipt",
        ] {
            let mut wrong = selector.clone();
            wrong[key] = serde_json::json!("caller");
            assert!(serde_json::from_value::<OpenRequest>(wrong).is_err());
        }
        assert_eq!(
            byte_range(Some("bytes=10-"), ASSET_MEDIA_RANGE_BYTES * 3, false),
            Ok((10, 10 + ASSET_MEDIA_RANGE_BYTES - 1, true))
        );
        assert_eq!(
            byte_range(Some("bytes=-11"), 200, false),
            Ok((189, 199, true))
        );
        assert_eq!(
            byte_range(None, ASSET_MEDIA_RANGE_BYTES + 1, false),
            Err(())
        );
        for range in [
            "bytes=0-1,3-4",
            "bytes=-0",
            "bytes=999-",
            "bytes=5-1",
            "bytes= 1-2",
            "items=0-2",
        ] {
            assert!(byte_range(Some(range), 100, false).is_err());
        }
    }
    #[test]
    fn original_media_registry_keeps_read_and_close_separate_from_project_mutation() {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("original.sqlite3")).unwrap();
        let project = service.create_project("Original source").unwrap();
        let path = root.path().join("source.mp4");
        let mut bytes = vec![13u8; ASSET_MEDIA_RANGE_BYTES as usize + 31];
        bytes[4..8].copy_from_slice(b"ftyp");
        std::fs::write(&path, &bytes).unwrap();
        let project = service
            .import_asset_file(
                project.id,
                &project.stamp(),
                "import-original",
                &path,
                "Original clip".into(),
                "video/mp4".into(),
            )
            .unwrap()
            .project;
        std::fs::remove_file(path).unwrap();
        let asset = project.assets.last().unwrap();
        let open = OpenRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            asset_id: asset.id,
            sha256: asset.content_sha256.clone().unwrap(),
        };
        let registry = AssetPreviews::default();
        let first = registry.prepare(&service, &open).unwrap();
        let request = RangeRequest {
            stream_token: first.stream_token,
            method: "GET".into(),
            range: Some("bytes=0-".into()),
        };
        let response = registry.read(&service, &request).unwrap();
        assert_eq!(response.status, 206);
        assert_eq!(response.body, bytes[..ASSET_MEDIA_RANGE_BYTES as usize]);
        assert_eq!(
            registry
                .read(
                    &service,
                    &RangeRequest {
                        range: None,
                        ..request
                    }
                )
                .unwrap()
                .status,
            416
        );
        let head = registry
            .read(
                &service,
                &RangeRequest {
                    stream_token: first.stream_token,
                    method: "HEAD".into(),
                    range: None,
                },
            )
            .unwrap();
        assert!(head.body.is_empty());
        assert_eq!(head.content_length, bytes.len() as u64);
        registry.close(first.stream_token);
        assert_eq!(
            registry
                .read(
                    &service,
                    &RangeRequest {
                        stream_token: first.stream_token,
                        method: "GET".into(),
                        range: Some("bytes=0-12".into())
                    }
                )
                .unwrap_err(),
            410
        );
        let second = registry.prepare(&service, &open).unwrap();
        assert_ne!(first.stream_token, second.stream_token);
        assert_eq!(service.project(project.id).unwrap(), project);
        service
            .apply(
                project.id,
                &project.stamp(),
                "rename-after-preview",
                &motionwright_domain::Change::RenameProject {
                    title: "Later source revision".into(),
                },
            )
            .unwrap();
        assert_eq!(
            registry
                .read(
                    &service,
                    &RangeRequest {
                        stream_token: second.stream_token,
                        method: "GET".into(),
                        range: Some("bytes=0-12".into())
                    }
                )
                .unwrap_err(),
            410
        );
    }
}
