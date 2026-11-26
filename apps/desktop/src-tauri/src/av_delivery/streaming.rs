//! Bounded playback of a completed master. No file path, grant, native dispatch,
//! receipt mutation or exported-media authority is accepted from the WebView.
use super::{DeliverySource, MAX_MASTER_BYTES, NativeMasterDeliveryRegistry};
use motionwright_domain::Project;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    fs::File,
    io::{Read, Seek, SeekFrom},
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};
use tauri::{
    Manager,
    http::{Method, Request, Response, StatusCode, Uri, header},
};
use uuid::Uuid;

pub(crate) const SCHEME: &str = "motionwright-media";
const BLOCK_BYTES: usize = 1024 * 1024;
pub(crate) const MAX_RANGE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_STREAMS: usize = 32;
const MAX_ACTIVE_READS: usize = 4;
const MAX_ACTIVE_PREPARATIONS: usize = 2;
const UNAVAILABLE: &str = "The original native master is unavailable for bounded playback.";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PlaybackRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub export_token: Uuid,
    #[serde(default)]
    pub stream: bool,
}

/// The closed alternatives share an owner command, not authority. A durable
/// receipt selector always takes the original read-only recovery path.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub(crate) enum PlaybackSelection {
    Session(PlaybackRequest),
    Receipt(super::AudioQcRecoveryRequest),
    SavedMux(super::SavedMasterRecoveryRequest),
}

impl PlaybackSelection {
    pub(crate) fn source(&self) -> (Uuid, Uuid, u64) {
        match self {
            Self::Session(request) => (request.project_id, request.generation, request.revision),
            Self::Receipt(request) => (request.project_id, request.generation, request.revision),
            Self::SavedMux(request) => (request.project_id, request.generation, request.revision),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Selection {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    export_token: Uuid,
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct ReviewDescriptor {
    schema: &'static str,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    export_token: Uuid,
    stream_token: Uuid,
    url: String,
    sha256: String,
    size_bytes: u64,
    max_range_bytes: u64,
    media_type: &'static str,
}

struct ReviewStream {
    token: Uuid,
    selection: Selection,
    sha256: String,
    size: u64,
    // These hashes are captured while hashing the SAME open descriptor against
    // the original complete SHA-256. Each returned block is checked again.
    blocks: Vec<[u8; 32]>,
    file: Mutex<File>,
}

#[derive(Clone, Default)]
pub(crate) struct ReviewStreams {
    streams: Arc<Mutex<VecDeque<Arc<ReviewStream>>>>,
    preparing: Arc<Mutex<()>>,
    active_reads: Arc<AtomicUsize>,
    active_preparations: Arc<AtomicUsize>,
}

// Acquired before a request can enter the blocking pool, so queued reads and
// concurrently buffered responses cannot exceed the same fixed resource budget.
pub(crate) struct ReadPermit(Arc<AtomicUsize>);

impl Drop for ReadPermit {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::AcqRel);
    }
}

fn media_url(token: Uuid) -> String {
    if cfg!(any(target_os = "windows", target_os = "android")) {
        format!("http://motionwright-media.localhost/{token}")
    } else {
        format!("motionwright-media://localhost/{token}")
    }
}

impl ReviewStream {
    fn descriptor(&self) -> ReviewDescriptor {
        ReviewDescriptor {
            schema: "motionwright.native-av-review/1",
            project_id: self.selection.project_id,
            generation: self.selection.generation,
            revision: self.selection.revision,
            deliverable_id: self.selection.deliverable_id,
            export_token: self.selection.export_token,
            stream_token: self.token,
            url: media_url(self.token),
            sha256: self.sha256.clone(),
            size_bytes: self.size,
            max_range_bytes: MAX_RANGE_BYTES,
            media_type: "video/mp4",
        }
    }

    fn capture(source: &DeliverySource, export_token: Uuid) -> Result<Self, String> {
        let original = super::verified_native_media_path(
            &source.owner_root,
            &source.relative_path,
            &source.sha256,
            MAX_MASTER_BYTES,
        )
        .map_err(|_| UNAVAILABLE)?;
        let file = File::open(original).map_err(|_| UNAVAILABLE)?;
        Self::capture_file(file, source, export_token)
    }

    fn capture_file(
        mut file: File,
        source: &DeliverySource,
        export_token: Uuid,
    ) -> Result<Self, String> {
        let metadata = file.metadata().map_err(|_| UNAVAILABLE)?;
        let size = metadata.len();
        if !metadata.is_file() || !(12..=MAX_MASTER_BYTES).contains(&size) {
            return Err(UNAVAILABLE.into());
        }
        let mut digest = Sha256::new();
        let mut blocks = Vec::with_capacity(size.div_ceil(BLOCK_BYTES as u64) as usize);
        let mut buffer = vec![0; BLOCK_BYTES];
        let mut offset = 0;
        while offset < size {
            let count = (size - offset).min(BLOCK_BYTES as u64) as usize;
            file.read_exact(&mut buffer[..count])
                .map_err(|_| UNAVAILABLE)?;
            if offset == 0 && buffer.get(4..8) != Some(&b"ftyp"[..]) {
                return Err(UNAVAILABLE.into());
            }
            digest.update(&buffer[..count]);
            blocks.push(Sha256::digest(&buffer[..count]).into());
            offset += count as u64;
        }
        let mut extra = [0u8; 1];
        if file.read(&mut extra).map_err(|_| UNAVAILABLE)? != 0
            || file.metadata().map_err(|_| UNAVAILABLE)?.len() != size
            || hex::encode(digest.finalize()) != source.sha256
        {
            return Err(UNAVAILABLE.into());
        }
        Ok(Self {
            token: Uuid::new_v4(),
            selection: Selection {
                project_id: source.project_id,
                generation: source.generation,
                revision: source.revision,
                deliverable_id: source.deliverable_id,
                export_token,
            },
            sha256: source.sha256.clone(),
            size,
            blocks,
            file: Mutex::new(file),
        })
    }

    fn read_range(&self, start: u64, end: u64) -> Result<Vec<u8>, StatusCode> {
        // Global admission bounds waiting workers to four. Serialize access to
        // this descriptor so overlapping decoder seeks do not fail as busy.
        let mut file = self
            .file
            .lock()
            .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
        if start > end || end >= self.size || end - start >= MAX_RANGE_BYTES {
            return Err(StatusCode::RANGE_NOT_SATISFIABLE);
        }
        if file.metadata().map_err(|_| StatusCode::GONE)?.len() != self.size {
            return Err(StatusCode::GONE);
        }
        let mut body = Vec::with_capacity((end - start + 1) as usize);
        let mut buffer = vec![0; BLOCK_BYTES];
        let first = start / BLOCK_BYTES as u64;
        let last = end / BLOCK_BYTES as u64;
        for index in first..=last {
            let offset = index * BLOCK_BYTES as u64;
            let count = (self.size - offset).min(BLOCK_BYTES as u64) as usize;
            file.seek(SeekFrom::Start(offset))
                .map_err(|_| StatusCode::GONE)?;
            file.read_exact(&mut buffer[..count])
                .map_err(|_| StatusCode::GONE)?;
            let actual: [u8; 32] = Sha256::digest(&buffer[..count]).into();
            if self.blocks.get(index as usize) != Some(&actual) {
                return Err(StatusCode::GONE);
            }
            let lo = start.saturating_sub(offset) as usize;
            let hi = (end - offset + 1).min(count as u64) as usize;
            body.extend_from_slice(&buffer[lo..hi]);
        }
        if file.metadata().map_err(|_| StatusCode::GONE)?.len() != self.size {
            return Err(StatusCode::GONE);
        }
        Ok(body)
    }
}

impl ReviewStreams {
    /// Admit before the blocking pool, including receipt reconstruction and
    /// legacy binary reads. The permit must move into the actual worker so a
    /// dropped IPC future cannot release capacity while its IO continues.
    pub(crate) fn admit_preparation(&self) -> Option<ReadPermit> {
        self.active_preparations
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                (count < MAX_ACTIVE_PREPARATIONS).then_some(count + 1)
            })
            .ok()
            .map(|_| ReadPermit(self.active_preparations.clone()))
    }

    pub(crate) fn admit_read(&self) -> Option<ReadPermit> {
        self.active_reads
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                (count < MAX_ACTIVE_READS).then_some(count + 1)
            })
            .ok()
            .map(|_| ReadPermit(self.active_reads.clone()))
    }

    fn find(&self, token: Uuid) -> Result<Arc<ReviewStream>, StatusCode> {
        self.streams
            .lock()
            .map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?
            .iter()
            .find(|entry| entry.token == token)
            .cloned()
            .ok_or(StatusCode::GONE)
    }

    /// The only caller is the existing admitted owner read command after it
    /// resolves a real delivery handle. No source paths or hashes come from IPC.
    pub(crate) fn prepare(
        &self,
        source: &DeliverySource,
        export_token: Uuid,
    ) -> Result<ReviewDescriptor, String> {
        let _preparing = self.preparing.try_lock().map_err(|_| UNAVAILABLE)?;
        if let Some(existing) = self
            .streams
            .lock()
            .map_err(|_| UNAVAILABLE)?
            .iter()
            .find(|entry| {
                entry.selection.export_token == export_token
                    && entry.selection.project_id == source.project_id
                    && entry.selection.generation == source.generation
                    && entry.selection.revision == source.revision
                    && entry.selection.deliverable_id == source.deliverable_id
                    && entry.sha256 == source.sha256
            })
        {
            return Ok(existing.descriptor());
        }
        let stream = Arc::new(ReviewStream::capture(source, export_token)?);
        let descriptor = stream.descriptor();
        let mut streams = self.streams.lock().map_err(|_| UNAVAILABLE)?;
        if streams.len() >= MAX_STREAMS {
            streams.pop_front();
        }
        streams.push_back(stream);
        Ok(descriptor)
    }

    fn response(
        &self,
        token: Uuid,
        project: &Project,
        deliveries: &NativeMasterDeliveryRegistry,
        request: &Request<Vec<u8>>,
    ) -> Result<Response<Vec<u8>>, StatusCode> {
        let stream = self.find(token)?;
        let selected = &stream.selection;
        if project.id != selected.project_id
            || project.generation != selected.generation
            || project.revision != selected.revision
        {
            return Err(StatusCode::GONE);
        }
        let source = deliveries
            .resolve(project, selected.export_token)
            .map_err(|_| StatusCode::GONE)?;
        if source.sha256 != stream.sha256 || source.deliverable_id != selected.deliverable_id {
            return Err(StatusCode::GONE);
        }
        if request.method() != Method::GET && request.method() != Method::HEAD {
            return Err(StatusCode::METHOD_NOT_ALLOWED);
        }
        if !request.body().is_empty() || request.headers().get_all(header::RANGE).iter().count() > 1
        {
            return Err(StatusCode::BAD_REQUEST);
        }
        let range = request
            .headers()
            .get(header::RANGE)
            .map(|value| value.to_str())
            .transpose()
            .map_err(|_| StatusCode::BAD_REQUEST)?;
        let selected_range = match byte_range(range, stream.size, request.method() == Method::HEAD)
        {
            Ok(value) => value,
            Err(()) => {
                return Ok(Response::builder()
                    .status(StatusCode::RANGE_NOT_SATISFIABLE)
                    .header(header::CONTENT_RANGE, format!("bytes */{}", stream.size))
                    .header(header::ACCEPT_RANGES, "bytes")
                    .header(header::CACHE_CONTROL, "no-store")
                    .body(Vec::new())
                    .expect("fixed range error headers"));
            }
        };
        let (start, end, partial) = selected_range;
        let body = if request.method() == Method::HEAD {
            Vec::new()
        } else {
            stream.read_range(start, end)?
        };
        // Eviction during IO cannot extend an expired stream's lifetime.
        if !Arc::ptr_eq(&stream, &self.find(token)?) {
            return Err(StatusCode::GONE);
        }
        let mut response = Response::builder()
            .status(if partial {
                StatusCode::PARTIAL_CONTENT
            } else {
                StatusCode::OK
            })
            .header(header::CONTENT_TYPE, "video/mp4")
            .header(header::CONTENT_LENGTH, (end - start + 1).to_string())
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CACHE_CONTROL, "no-store")
            .header("X-Content-Type-Options", "nosniff");
        if partial {
            response = response.header(
                header::CONTENT_RANGE,
                format!("bytes {start}-{end}/{}", stream.size),
            );
        }
        Ok(response
            .body(body)
            .expect("bounded validated media headers"))
    }
}

/// One range only. A WebView must use byte ranges for a large master; a
/// no-range request never turns into an unbounded whole-file allocation.
fn byte_range(raw: Option<&str>, size: u64, head: bool) -> Result<(u64, u64, bool), ()> {
    if size == 0 {
        return Err(());
    }
    let Some(raw) = raw else {
        return if head || size <= MAX_RANGE_BYTES {
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
    let decimal = |text: &str| -> Result<u64, ()> {
        if text.is_empty() || !text.bytes().all(|value| value.is_ascii_digit()) {
            return Err(());
        }
        text.parse::<u64>().map_err(|_| ())
    };
    let (start, requested_end) = if first.is_empty() {
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
    if start >= size || requested_end < start {
        return Err(());
    }
    Ok((
        start,
        requested_end.min(start.saturating_add(MAX_RANGE_BYTES - 1)),
        true,
    ))
}

fn request_token(uri: &Uri) -> Result<Uuid, StatusCode> {
    let authority = uri.authority().map(|value| value.as_str());
    if !((uri.scheme_str() == Some(SCHEME) && authority == Some("localhost"))
        || (uri.scheme_str() == Some("http") && authority == Some("motionwright-media.localhost")))
        || uri.query().is_some()
    {
        return Err(StatusCode::BAD_REQUEST);
    }
    let value = uri
        .path()
        .strip_prefix('/')
        .ok_or(StatusCode::BAD_REQUEST)?;
    let token = Uuid::parse_str(value).map_err(|_| StatusCode::BAD_REQUEST)?;
    if token.to_string() != value {
        return Err(StatusCode::BAD_REQUEST);
    }
    Ok(token)
}

pub(crate) fn failure(status: StatusCode) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(header::CACHE_CONTROL, "no-store")
        .body(Vec::new())
        .expect("fixed private media failure")
}

/// The label comes from Tauri's native UriSchemeContext, never HTTP or JSON.
/// The actual registered WebView must retain its native owner resource identity.
pub(crate) fn handle(
    app: &tauri::AppHandle,
    native_webview_label: &str,
    request: Request<Vec<u8>>,
) -> Response<Vec<u8>> {
    let result = (|| {
        let owner = app
            .try_state::<crate::desktop_owner::DesktopOwner>()
            .ok_or(StatusCode::FORBIDDEN)?;
        owner
            .authorize_protocol(app, native_webview_label)
            .map_err(|_| StatusCode::FORBIDDEN)?;
        let phase = owner.phase().map_err(|_| StatusCode::FORBIDDEN)?;
        let token = request_token(request.uri())?;
        let state = app
            .try_state::<crate::AppState>()
            .ok_or(StatusCode::FORBIDDEN)?;
        let stream = state.av_review.find(token)?;
        let project = state
            .service
            .project(stream.selection.project_id)
            .map_err(|_| StatusCode::GONE)?;
        let response = state
            .av_review
            .response(token, &project, &state.av_delivery, &request)?;
        drop(phase);
        owner
            .authorize_protocol(app, native_webview_label)
            .map_err(|_| StatusCode::FORBIDDEN)?;
        let _phase = owner.phase().map_err(|_| StatusCode::FORBIDDEN)?;
        let latest = state
            .service
            .project(project.id)
            .map_err(|_| StatusCode::GONE)?;
        if latest.generation != project.generation || latest.revision != project.revision {
            return Err(StatusCode::GONE);
        }
        state
            .av_delivery
            .resolve(&latest, stream.selection.export_token)
            .map_err(|_| StatusCode::GONE)?;
        Ok(response)
    })();
    result.unwrap_or_else(failure)
}

#[cfg(test)]
mod tests;
