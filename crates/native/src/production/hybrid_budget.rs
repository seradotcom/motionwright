//! A producer-owned root for the admitted MC / static Manim / MC route.
//!
//! This is accounting, not a Host grant. All calls still pass the original
//! permission, revision, receipt custody and UNKNOWN fences. A caller receives
//! original evidence, never a coordinator with arbitrary budgeted execution.
use super::*;
use motionwright_domain::{Project, RendererKind};
use semwright_video_domain::native_observation;
use std::{collections::BTreeMap, io::Write, sync::Arc};

const MEDIA_MAX_BYTES: u64 = 256_000_000;
const MASTER_MAX_BYTES: u64 = 32 * 1024 * 1024;
const MANIM_OBSERVATION_FRAME_MAX_BYTES: u64 = 32 * 1024 * 1024;
const MANIM_OBSERVATION_REPORT_MAX_BYTES: u64 = 16 * 1024 * 1024;
// Selected SDK driver-mlt-video: jobs.rs, runtime.rs and originals.rs. These
// are existing writer caps, not permission to enlarge files or retry a job.
const SDK_MEDIA_MAX_BYTES: u64 = 256 * 1024 * 1024;
const SDK_PROCESS_PREFIX_BYTES: u64 = 16_384;
const SDK_PROCESS_METADATA_BYTES: u64 = 65_536;
const SDK_HOST_STREAM_BYTES: u64 = 262_144;
const SDK_INPUT_PROBE_BYTES: u64 = 2 * SDK_PROCESS_PREFIX_BYTES + 2 * SDK_PROCESS_METADATA_BYTES;
const SDK_HOST_CALL_BYTES: u64 = 2 * SDK_HOST_STREAM_BYTES + SDK_PROCESS_METADATA_BYTES;
// The original Host call and optional catalog call each retain stdin,
// stdout, stderr and terminal; these are distinct from owned-work captures.
const SDK_HOST_OPERATION_BYTES: u64 = 2 * (SDK_PROCESS_METADATA_BYTES + SDK_HOST_CALL_BYTES);
// SDK xml.rs MAX_XML; native render retains staged, before and after XML.
const NATIVE_INPUT_XML_MAX_BYTES: u64 = 8 * 1024 * 1024;
// Selected SDK model::MAX_PROJECT_BYTES used by Store, plus a closed receipt.
const MC_SOURCE_MAX_BYTES: u64 = 524_288;
const MC_SOURCE_RECEIPT_MAX_BYTES: u64 = 16_384;
const HYBRID_GRANT_MAX_BYTES: u64 = 65_536;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct HybridGrantContext {
    source_sha: String,
    semwright_sha: String,
    run_id: String,
    run_attempt: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct HybridGrantFile {
    path: PathBuf,
    sha256: String,
    bytes: u64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct HybridGrantDaemon {
    argv: Vec<String>,
    // Keep the required wire fields; live process identity is checked on Linux.
    #[cfg_attr(not(target_os = "linux"), expect(dead_code))]
    pid: u32,
    #[cfg_attr(not(target_os = "linux"), expect(dead_code))]
    start_ticks: u64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct HybridGrantReceipt {
    schema: String,
    context: HybridGrantContext,
    source_project_sha256: String,
    project_root: PathBuf,
    output_root: PathBuf,
    socket: PathBuf,
    owner_config: HybridGrantFile,
    connection: HybridGrantFile,
    daemon: HybridGrantDaemon,
    native_effect_started: bool,
    automatic_retry: bool,
}

/// Sealed observation of the existing owner's grant, not a new Host grant.
#[derive(Debug, Clone)]
pub(super) struct HybridProjectGrant {
    receipt: HybridGrantReceipt,
    original: HybridGrantFile,
    connection_identity: String,
    project_identity: [u64; 5],
    output_identity: [u64; 5],
    socket_identity: [u64; 5],
}

fn grant_file_bytes(file: &HybridGrantFile, maximum: u64) -> NativeResult<Vec<u8>> {
    require(
        file.bytes > 0 && file.bytes <= maximum && is_sha256(&file.sha256),
        "Hybrid grant original has an invalid bounded identity",
    )?;
    let parent = file
        .path
        .parent()
        .ok_or_else(|| invalid("Hybrid grant original parent is absent"))?;
    let name = file
        .path
        .file_name()
        .and_then(|part| part.to_str())
        .ok_or_else(|| invalid("Hybrid grant original name is invalid"))?;
    let bytes = read_verified_production_artifact(parent, name, &file.sha256, maximum)?;
    require(
        bytes.len() as u64 == file.bytes,
        "Hybrid grant original byte count changed",
    )?;
    Ok(bytes)
}

#[cfg(target_os = "linux")]
fn grant_path_identity(path: &Path, directory: bool) -> NativeResult<[u64; 5]> {
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    let metadata = safe_metadata(path)?;
    require(
        if directory {
            metadata.is_dir()
        } else {
            metadata.file_type().is_socket()
        },
        "Hybrid grant path has another filesystem kind",
    )?;
    require(
        metadata.uid() == unsafe { libc::geteuid() },
        "Hybrid grant path has another owner",
    )?;
    Ok([
        metadata.dev(),
        metadata.ino(),
        u64::from(metadata.mode()),
        u64::from(metadata.uid()),
        u64::from(metadata.gid()),
    ])
}

#[cfg(not(target_os = "linux"))]
fn grant_path_identity(_path: &Path, _directory: bool) -> NativeResult<[u64; 5]> {
    Err(Error::new(
        ErrorCode::Unsupported,
        "Hybrid project-grant custody requires its original Linux CI owner",
    ))
}

// This recognizes only the exact source-owned Python writer's closed TOML
// profile. It is deliberately not an approximate general TOML parser.
fn verify_owner_grant_profile(bytes: &[u8], project: &Path, output: &Path) -> NativeResult<()> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| invalid("Hybrid owner configuration is not UTF-8"))?;
    require(
        text.ends_with('\n') && !text.contains('\r'),
        "Hybrid owner configuration framing changed",
    )?;
    let sections = text
        .trim_end_matches('\n')
        .split("\n\n")
        .collect::<Vec<_>>();
    require(
        (4..=34).contains(&sections.len()),
        "Hybrid owner grant roster is outside its closed bound",
    )?;
    let header = sections[0].lines().collect::<Vec<_>>();
    require(
        header.len() == 2 && header[1] == "driver_network = false",
        "Hybrid owner configuration header changed",
    )?;
    let drivers: Vec<String> = serde_json::from_str(
        header[0]
            .strip_prefix("drivers = ")
            .ok_or_else(|| invalid("Hybrid owner driver declaration changed"))?,
    )
    .map_err(|_| invalid("Hybrid owner driver declaration is malformed"))?;
    let owner = project
        .parent()
        .ok_or_else(|| invalid("Hybrid project owner is absent"))?;
    let expected_drivers = [
        "motion-canvas-driver.json",
        "mlt-video-driver.json",
        "manim-driver.json",
    ]
    .iter()
    .map(|name| {
        owner
            .join("config")
            .join(name)
            .to_string_lossy()
            .into_owned()
    })
    .collect::<Vec<_>>();
    require(
        drivers == expected_drivers
            && sections[1]
                == "[policy]\nprofile = \"workspace\"\nallow = [\"driver:motion-canvas\", \"driver:mlt-video\", \"driver:manim-community\"]",
        "Hybrid owner driver/policy profile differs from the original closed producer",
    )?;
    let mut grants = BTreeMap::new();
    for section in &sections[2..] {
        let lines = section.lines().collect::<Vec<_>>();
        require(
            lines.len() == 5 && lines[0] == "[[policy.filesystem]]" && lines[3] == "read = true",
            "Hybrid owner filesystem block changed",
        )?;
        let parse_string = |line: &str, prefix: &str| -> NativeResult<String> {
            serde_json::from_str(
                line.strip_prefix(prefix)
                    .ok_or_else(|| invalid("Hybrid owner grant field changed"))?,
            )
            .map_err(|_| invalid("Hybrid owner grant string is malformed"))
        };
        let name = parse_string(lines[1], "name = ")?;
        let path = PathBuf::from(parse_string(lines[2], "path = ")?);
        let writable = match lines[4] {
            "write = true" => true,
            "write = false" => false,
            _ => return Err(invalid("Hybrid owner grant write flag changed")),
        };
        require(
            !name.is_empty()
                && name.len() <= 128
                && grants.insert(name, (path, writable)).is_none(),
            "Hybrid owner grant name is duplicated or invalid",
        )?;
    }
    require(
        grants.get("project") == Some(&(project.to_path_buf(), true))
            && grants.get("output") == Some(&(output.to_path_buf(), true)),
        "Hybrid project/output paths are not the existing owner read/write grants",
    )
}

#[cfg(any(target_os = "linux", test))]
fn daemon_start_from_stat(bytes: &[u8], pid: u32) -> NativeResult<u64> {
    let text =
        std::str::from_utf8(bytes).map_err(|_| invalid("Hybrid daemon stat is malformed"))?;
    let (prefix, fields) = text
        .rsplit_once(')')
        .ok_or_else(|| invalid("Hybrid daemon stat fields are absent"))?;
    require(
        prefix.starts_with(&format!("{pid} (")),
        "Hybrid daemon stat names another process",
    )?;
    let fields = fields.split_whitespace().collect::<Vec<_>>();
    require(
        fields.len() >= 20 && fields[0] != "Z" && fields[0] != "X",
        "Hybrid daemon is not an observed live process",
    )?;
    fields[19]
        .parse()
        .map_err(|_| invalid("Hybrid daemon start time is malformed"))
}

#[cfg(target_os = "linux")]
fn verify_grant_daemon(daemon: &HybridGrantDaemon) -> NativeResult<()> {
    use std::io::Read;
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    require(
        daemon.pid > 0 && daemon.start_ticks > 0,
        "Hybrid daemon identity is invalid",
    )?;
    let root = PathBuf::from(format!("/proc/{}", daemon.pid));
    let observed =
        fs::metadata(&root).map_err(|_| invalid("Hybrid original daemon is unavailable"))?;
    require(
        observed.uid() == unsafe { libc::geteuid() },
        "Hybrid daemon belongs to another owner",
    )?;
    let read = |name: &str| -> NativeResult<Vec<u8>> {
        let mut file = fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(root.join(name))
            .map_err(|_| invalid("Hybrid original daemon observation is unavailable"))?;
        let mut bytes = Vec::new();
        (&mut file)
            .take(HYBRID_GRANT_MAX_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| invalid("Hybrid original daemon observation failed"))?;
        require(
            bytes.len() as u64 <= HYBRID_GRANT_MAX_BYTES,
            "Hybrid daemon observation exceeds its bound",
        )?;
        Ok(bytes)
    };
    let first = daemon_start_from_stat(&read("stat")?, daemon.pid)?;
    let argv = daemon.argv.join("\0") + "\0";
    require(
        read("cmdline")?.as_slice() == argv.as_bytes()
            && fs::read_link(root.join("exe")).ok().as_deref()
                == daemon.argv.first().map(Path::new),
        "Hybrid daemon argv/executable differs from the owner configuration process",
    )?;
    require(
        first == daemon.start_ticks && daemon_start_from_stat(&read("stat")?, daemon.pid)? == first,
        "Hybrid original daemon generation changed",
    )
}

#[cfg(not(target_os = "linux"))]
fn verify_grant_daemon(_daemon: &HybridGrantDaemon) -> NativeResult<()> {
    Err(Error::new(
        ErrorCode::Unsupported,
        "Hybrid daemon custody requires its original Linux CI owner",
    ))
}

impl HybridProjectGrant {
    fn recheck(&self, coordinator: &ProductionCoordinator) -> NativeResult<()> {
        let receipt = &self.receipt;
        require(
            coordinator.client.connection().identity()? == self.connection_identity
                && coordinator.client.connection().output_root == receipt.output_root
                && coordinator.client.connection().socket == receipt.socket,
            "Hybrid grant differs from the original production connection",
        )?;
        let original = grant_file_bytes(&self.original, HYBRID_GRANT_MAX_BYTES)?;
        let current: HybridGrantReceipt = serde_json::from_slice(&original)
            .map_err(|_| invalid("Hybrid grant original is malformed"))?;
        require(
            current.source_project_sha256 == receipt.source_project_sha256,
            "Hybrid grant original source changed",
        )?;
        let config = grant_file_bytes(&receipt.owner_config, HYBRID_GRANT_MAX_BYTES)?;
        verify_owner_grant_profile(&config, &receipt.project_root, &receipt.output_root)?;
        let connection = grant_file_bytes(&receipt.connection, MAX_CONFIG_BYTES)?;
        require(
            serde_json::from_slice::<Value>(&connection)
                .map_err(|_| invalid("Hybrid original connection is malformed"))?
                == serde_json::to_value(coordinator.client.connection())
                    .map_err(|_| invalid("Hybrid connection encoding failed"))?,
            "Hybrid grant connection bytes name another production owner",
        )?;
        require(
            grant_path_identity(&receipt.project_root, true)? == self.project_identity
                && grant_path_identity(&receipt.output_root, true)? == self.output_identity
                && grant_path_identity(&receipt.socket, false)? == self.socket_identity,
            "Hybrid original project/output/socket identity changed",
        )?;
        verify_grant_daemon(&receipt.daemon)
    }

    fn require_unused_snapshots(&self, namespace: &str) -> NativeResult<()> {
        for ordinal in [0, 2] {
            for suffix in [
                "semwright-motion.original.json",
                "semwright-motion-custody.original.json",
            ] {
                let path = self
                    .receipt
                    .output_root
                    .join(format!("{namespace}-mc-{ordinal}.{suffix}"));
                require(
                    matches!(fs::symlink_metadata(path), Err(error) if error.kind() == std::io::ErrorKind::NotFound),
                    "Hybrid original MC source destination already exists or is unavailable; preserve it without replay",
                )?;
            }
        }
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn write_hybrid_source_original(
    root: &Path,
    expected: &[u64; 5],
    name: &str,
    bytes: &[u8],
    maximum: u64,
) -> NativeResult<()> {
    use std::io::Write;
    use std::os::{
        fd::{AsRawFd, FromRawFd},
        unix::fs::{MetadataExt, OpenOptionsExt},
    };
    require(
        !bytes.is_empty()
            && bytes.len() as u64 <= maximum
            && maximum <= MC_SOURCE_MAX_BYTES
            && name.starts_with("mw-hybrid-")
            && name.ends_with(".json")
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.')),
        "Hybrid source-original write is outside its closed member/byte bound",
    )?;
    require(
        &grant_path_identity(root, true)? == expected,
        "Hybrid source-original output root changed before write",
    )?;
    let directory = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(root)
        .map_err(|_| backend("Hybrid source-original output directory cannot be retained"))?;
    let metadata = directory
        .metadata()
        .map_err(|_| backend("Hybrid source-original output directory metadata is unavailable"))?;
    require(
        [
            metadata.dev(),
            metadata.ino(),
            u64::from(metadata.mode()),
            u64::from(metadata.uid()),
            u64::from(metadata.gid()),
        ] == *expected,
        "Hybrid source-original output directory changed before exclusive creation",
    )?;
    let name_c = std::ffi::CString::new(name)
        .map_err(|_| invalid("Hybrid source-original member is invalid"))?;
    let fd = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            name_c.as_ptr(),
            libc::O_WRONLY
                | libc::O_CREAT
                | libc::O_EXCL
                | libc::O_NOFOLLOW
                | libc::O_CLOEXEC
                | libc::O_NONBLOCK,
            0o600,
        )
    };
    if fd < 0 {
        return Err(
            if std::io::Error::last_os_error().kind() == std::io::ErrorKind::AlreadyExists {
                Error::new(
                    ErrorCode::Conflict,
                    "Hybrid source original already exists; preserve it without retry",
                )
            } else {
                backend("Hybrid source original cannot be exclusively created")
            },
        );
    }
    let mut file = unsafe { fs::File::from_raw_fd(fd) };
    file.write_all(bytes)
        .and_then(|_| file.sync_all())
        .and_then(|_| directory.sync_all())
        .map_err(|_| {
            backend(
                "Hybrid source-original publication failed; retain its charged partial original",
            )
        })?;
    require(
        read_verified_production_artifact(
            root,
            name,
            &hex::encode(Sha256::digest(bytes)),
            maximum,
        )? == bytes,
        "Hybrid source-original readback differs from the applied source bytes",
    )
}

#[cfg(not(target_os = "linux"))]
fn write_hybrid_source_original(
    _root: &Path,
    _expected: &[u64; 5],
    _name: &str,
    _bytes: &[u8],
    _maximum: u64,
) -> NativeResult<()> {
    Err(Error::new(
        ErrorCode::Unsupported,
        "Hybrid source-original custody requires its original Linux CI owner",
    ))
}

/// Retained outputs of the actual two MC jobs and actual static Manim job.
/// It cannot be reconstructed from JSON or rebound to another coordinator.
pub struct HybridNativeSources {
    pub(super) project: Project,
    pub(super) expected: RevisionStamp,
    pub(super) deliverable_id: Uuid,
    pub(super) options: FilmBuildOptions,
    pub(super) project_sha256: String,
    pub(super) namespace: String,
    pub(super) request_prefix: String,
    pub(super) plan: Value,
    pub(super) motion_canvas: MotionCanvasRenderEvidence,
    pub(super) manim: Value,
    evidence: Value,
    owner: ProductionCoordinator,
    scope: HybridRootBudgetScope,
}

impl HybridNativeSources {
    pub fn evidence(&self) -> &Value {
        &self.evidence
    }
}

#[derive(Debug, Clone)]
struct MotionContribution {
    segment_id: String,
    scene_ids: Vec<Uuid>,
    frames: u64,
    output_ordinal: usize,
}

#[derive(Debug)]
struct HybridBudgetPlan {
    project: Project,
    expected: RevisionStamp,
    deliverable_id: Uuid,
    options: FilmBuildOptions,
    connection_identity: String,
    project_sha256: String,
    namespace: String,
    request_prefix: String,
    source_plan: Value,
    motion: Vec<MotionContribution>,
    motion_frames: u64,
    frame_reservation: u64,
    manim_scene_id: Uuid,
    manim_plan: Value,
    manim_plan_sha256: String,
    manim_profile: Value,
    manim_frames: u64,
    total_frames: u64,
    width: u32,
    height: u32,
    source_audio_sha256: String,
    source_audio_bytes: u64,
    source_audio_path: PathBuf,
    limits: RootBudgetUnits,
}

#[derive(Debug, Clone)]
struct HybridMltBinding {
    encodes: BTreeMap<String, MltEncodeBudget>,
    source_paths: Vec<String>,
}

#[derive(Debug, Clone)]
enum HybridPhase {
    Sources,
    Mlt(HybridMltBinding),
}

#[derive(Debug, Clone)]
pub(super) struct HybridRootBudgetScope {
    root: RootOperationBudget,
    plan: Arc<HybridBudgetPlan>,
    phase: HybridPhase,
}

fn bounded_add(left: u64, right: u64) -> NativeResult<u64> {
    left.checked_add(right)
        .ok_or_else(|| invalid("Hybrid native resource reservation overflow"))
}
fn bounded_mul(left: u64, right: u64) -> NativeResult<u64> {
    left.checked_mul(right)
        .ok_or_else(|| invalid("Hybrid native resource reservation overflow"))
}
fn require(condition: bool, message: &'static str) -> NativeResult<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(message))
    }
}
fn supported(condition: bool, message: &'static str) -> NativeResult<()> {
    if condition {
        Ok(())
    } else {
        Err(Error::new(ErrorCode::Unsupported, message))
    }
}
fn one_attempt() -> RootBudgetUnits {
    RootBudgetUnits {
        attempts: 1,
        model_calls: 0,
        render_frames: 0,
        output_bytes: 0,
    }
}
fn project_digest(project: &Project) -> NativeResult<String> {
    // The original/reopened project files and consumer use these exact bytes.
    let mut bytes = serde_json::to_vec_pretty(project)
        .map_err(|_| invalid("Hybrid original Project cannot be serialized"))?;
    bytes.push(b'\n');
    Ok(hex::encode(Sha256::digest(bytes)))
}
fn exact_frames(num: i64, den: i64, rate: Rate) -> NativeResult<u64> {
    require(
        num >= 0 && den > 0 && rate.num > 0 && rate.den > 0,
        "Hybrid native clock is negative or invalid",
    )?;
    let numerator = i128::from(num) * i128::from(rate.num);
    let denominator = i128::from(den) * i128::from(rate.den);
    require(
        numerator % denominator == 0,
        "Hybrid native clock is not frame-exact",
    )?;
    u64::try_from(numerator / denominator)
        .map_err(|_| invalid("Hybrid native clock overflows its frame bound"))
}
fn operation_key(project: &Project, deliverable_id: Uuid) -> String {
    // Deliberately the SAME key as the pre-existing MC root. A new label,
    // renderer route, coordinator or process cannot buy replacement credit.
    format!(
        "motion-canvas/{}/{}/{}/{}",
        project.id, project.generation, project.revision, deliverable_id
    )
}
// The SDK retains BOTH owned workspaces and published raw originals, including
// partial and empty outputs. Account for their closed rosters before dispatch;
// peak_free_bytes contains unrelated scratch slack and is not this debit.
#[derive(Clone, Copy)]
enum SdkVideoOriginals {
    MotionFrames,
    NativeRender,
    HostNativeRender,
}

fn sdk_original_metadata_bytes(
    output_path: &str,
    members: u64,
    inputs: u64,
    raw_inputs: u64,
    process_groups: u64,
) -> NativeResult<u64> {
    require(
        !output_path.is_empty() && output_path.len() <= 4096,
        "MLT original output path exceeds the existing SDK bound",
    )?;
    // originals.rs bounds each manifest row by path bytes + 2048, plus
    // process errors. The workspace document has its own finite role roster.
    // Its serialized cap is at most this existing structure guard, never a
    // new fixed metadata allowance. Include the one failure-only work receipt.
    let row = bounded_add(output_path.len() as u64, 2048)?;
    let manifest = bounded_add(
        bounded_mul(members, row)?,
        bounded_mul(bounded_add(process_groups, 1)?, SDK_PROCESS_METADATA_BYTES)?,
    )?;
    let work = members
        .checked_sub(raw_inputs)
        .ok_or_else(|| invalid("MLT original input roster is inconsistent"))?;
    let workspace = bounded_add(
        bounded_mul(bounded_add(bounded_add(inputs, work)?, 1)?, row)?,
        SDK_PROCESS_METADATA_BYTES,
    )?;
    require(
        manifest <= native_observation::REPORT_LIMIT as u64
            && workspace <= native_observation::REPORT_LIMIT as u64,
        "MLT original metadata exceeds the existing SDK roster bound",
    )?;
    bounded_add(
        bounded_add(bounded_mul(manifest, 2)?, SDK_PROCESS_METADATA_BYTES)?,
        workspace,
    )
}

fn sdk_video_output_bytes(
    width: u32,
    height: u32,
    frames: u64,
    input_assets: u64,
    output_path: &str,
    output_maximum: u64,
    kind: SdkVideoOriginals,
) -> NativeResult<u64> {
    require(
        output_maximum > 0 && output_maximum <= native_observation::MAX_VIDEO_ARTIFACT_BYTES,
        "MLT publication exceeds the existing SDK artifact cap",
    )?;
    let observation =
        native_observation::observation_budget(width, height, frames).map_err(|error| {
            invalid(format!(
                "MLT original observation is outside the SDK profile: {error}"
            ))
        })?;
    let report = native_observation::REPORT_LIMIT as u64;
    let stage = 2 * SDK_PROCESS_PREFIX_BYTES + SDK_PROCESS_METADATA_BYTES;
    let complete_probe = 2 * report + SDK_PROCESS_METADATA_BYTES;
    let (staged, inputs, raw_inputs, stages, probes, host_calls, extra_members, extra_bytes) =
        match kind {
            SdkVideoOriginals::MotionFrames => (
                bounded_mul(frames, native_observation::PNG_LIMIT as u64)?,
                frames,
                0,
                6,
                0,
                0,
                0,
                0,
            ),
            SdkVideoOriginals::NativeRender => (
                bounded_mul(input_assets, SDK_MEDIA_MAX_BYTES)?,
                bounded_add(input_assets, 1)?,
                1,
                5,
                input_assets,
                0,
                0,
                bounded_mul(NATIVE_INPUT_XML_MAX_BYTES, 3)?,
            ),
            SdkVideoOriginals::HostNativeRender => (
                bounded_mul(input_assets, SDK_MEDIA_MAX_BYTES)?,
                bounded_add(input_assets, 2)?,
                1,
                4,
                bounded_add(input_assets, 1)?,
                bounded_add(input_assets, 2)?,
                3,
                // Staged/before/after XML, staged request, work/raw report,
                // and two diagnostic files in both work and raw publication.
                bounded_add(
                    bounded_add(
                        bounded_mul(NATIVE_INPUT_XML_MAX_BYTES, 3)?,
                        native_observation::MAX_JSON as u64,
                    )?,
                    bounded_add(
                        bounded_mul(report, 2)?,
                        bounded_mul(SDK_PROCESS_METADATA_BYTES, 4)?,
                    )?,
                )?,
            ),
        };
    let native_process = match kind {
        // zscale-help retains the full 262144-byte stream, not the 16KiB
        // diagnostic prefix. All six stages and their summary have two copies.
        SdkVideoOriginals::MotionFrames => bounded_add(
            bounded_mul(stage, 4)?,
            bounded_add(
                2 * SDK_HOST_STREAM_BYTES + SDK_PROCESS_METADATA_BYTES,
                complete_probe,
            )?,
        )?,
        SdkVideoOriginals::NativeRender => bounded_add(bounded_mul(stage, 4)?, complete_probe)?,
        SdkVideoOriginals::HostNativeRender => bounded_add(bounded_mul(stage, 3)?, complete_probe)?,
    };
    let process = bounded_mul(
        bounded_add(
            bounded_add(native_process, SDK_PROCESS_METADATA_BYTES)?,
            bounded_add(
                bounded_mul(probes, SDK_INPUT_PROBE_BYTES)?,
                bounded_mul(host_calls, SDK_HOST_CALL_BYTES)?,
            )?,
        )?,
        2,
    )?;
    let members = bounded_add(
        bounded_add(frames, 3 + raw_inputs + 3 * stages + extra_members)?,
        bounded_add(bounded_mul(probes, 4)?, bounded_mul(host_calls, 3)?)?,
    )?;
    let metadata = sdk_original_metadata_bytes(
        output_path,
        members,
        inputs,
        raw_inputs,
        bounded_add(bounded_add(probes, 1)?, host_calls)?,
    )?;
    // The sentinel PNG can survive an unsuccessful decode and raw retention.
    // Normal PNG publication reuses the raw slot, so there are exactly two
    // copies, not a third. Video has owned, raw and normal publication roles.
    let pngs = bounded_mul(
        bounded_mul(bounded_add(frames, 1)?, observation.png_bytes_upper_bound)?,
        2,
    )?;
    let host = match kind {
        SdkVideoOriginals::MotionFrames => SDK_HOST_OPERATION_BYTES,
        SdkVideoOriginals::HostNativeRender => native_host_custody_bytes()?,
        SdkVideoOriginals::NativeRender => 0,
    };
    [
        staged.min(native_observation::MAX_STAGED_SOURCE_BYTES),
        2 * native_observation::MAX_VIDEO_ARTIFACT_BYTES,
        output_maximum,
        pngs,
        report,
        process,
        metadata,
        extra_bytes,
        host,
    ]
    .into_iter()
    .try_fold(0, bounded_add)
}

pub(super) fn mlt_encode_output_bytes(
    width: u32,
    height: u32,
    frames: u64,
    output_path: &str,
    maximum: u64,
) -> NativeResult<u64> {
    sdk_video_output_bytes(
        width,
        height,
        frames,
        0,
        output_path,
        maximum,
        SdkVideoOriginals::MotionFrames,
    )
}

pub(super) fn mlt_timeline_output_bytes(
    width: u32,
    height: u32,
    frames: u64,
    inputs: u64,
    output_path: &str,
) -> NativeResult<u64> {
    // The one timeline allowance covers the admitted Host video-only route;
    // choosing the PCM route cannot create another timeline allowance.
    sdk_video_output_bytes(
        width,
        height,
        frames,
        inputs,
        output_path,
        MLT_MASTER_MAX_BYTES,
        SdkVideoOriginals::HostNativeRender,
    )
}

pub(super) fn mlt_import_output_bytes() -> NativeResult<u64> {
    // main.rs hashes the command/args into a fixed-length import scope. The
    // generic invocation spelling is the longer of the two existing names.
    let scope = format!("asset-import-invocation-{}", "0".repeat(64));
    let originals = bounded_add(
        bounded_add(SDK_MEDIA_MAX_BYTES + 1, native_observation::MAX_JSON as u64)?,
        bounded_add(SDK_INPUT_PROBE_BYTES, SDK_HOST_CALL_BYTES)?,
    )?;
    bounded_add(
        bounded_mul(originals, 2)?,
        sdk_original_metadata_bytes(&scope, 9, 2, 2, 2)?,
    )
}

pub(super) fn mlt_mux_output_bytes(
    width: u32,
    height: u32,
    frames: u64,
    output_path: &str,
    maximum: u64,
) -> NativeResult<u64> {
    let render = sdk_video_output_bytes(
        width,
        height,
        frames,
        2,
        output_path,
        maximum,
        SdkVideoOriginals::NativeRender,
    )?;
    // app.rs invokes one pinned native render, then the av-aux closed owner:
    // two input probes, a master snapshot, decoded PCM, two decode stages and
    // summary. Each has an owned and raw copy; normal WAV is a third copy.
    let auxiliary = bounded_add(
        bounded_add(
            bounded_mul(SDK_MEDIA_MAX_BYTES + 1, 2)?,
            bounded_mul(maximum, 2)?,
        )?,
        bounded_add(
            bounded_mul(SDK_INPUT_PROBE_BYTES, 2)?,
            2 * (2 * SDK_PROCESS_PREFIX_BYTES + SDK_PROCESS_METADATA_BYTES)
                + SDK_PROCESS_METADATA_BYTES,
        )?,
    )?;
    [
        render,
        bounded_mul(auxiliary, 2)?,
        maximum,
        SDK_HOST_OPERATION_BYTES,
        sdk_original_metadata_bytes(&format!("{output_path}.av-aux"), 19, 3, 3, 3)?,
    ]
    .into_iter()
    .try_fold(0, bounded_add)
}
fn native_host_custody_bytes() -> NativeResult<u64> {
    // Selected Host main.rs preflight_native_host_custody retains an original
    // report copy, bounded input request, terminal result and custody receipt.
    bounded_add(
        native_observation::REPORT_LIMIT as u64,
        bounded_add(native_observation::MAX_JSON as u64, 4_096 + 16_384)?,
    )
}
fn manim_output_bytes(frames: u64) -> NativeResult<u64> {
    let source = u64::try_from(motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2)
        .map_err(|_| invalid("Static Manim source bound is invalid"))?;
    // Original MP4, decoded original frames/report, and bounded source,
    // readback and recovery package. The reservation does not assert outputs.
    let observations = bounded_add(
        bounded_mul(frames, MANIM_OBSERVATION_FRAME_MAX_BYTES)?,
        MANIM_OBSERVATION_REPORT_MAX_BYTES,
    )?;
    bounded_add(
        bounded_add(MANIM_VIDEO_MAX_BYTES, observations)?,
        bounded_mul(source, 4)?,
    )
}

impl HybridBudgetPlan {
    fn build(
        coordinator: &ProductionCoordinator,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<Self> {
        let project = coordinator
            .service
            .project(project_id)
            .map_err(storage_error)?;
        require(
            project.id == project_id && RevisionStamp::from(&project) == *expected,
            "Hybrid production requires the current full Project stamp",
        )?;
        project
            .validate()
            .map_err(|_| invalid("Hybrid canonical Project is invalid"))?;
        require(
            coordinator.client.connection().resource == project.resource_key(),
            "Hybrid provider connection belongs to another project",
        )?;
        let connection_identity = coordinator.client.connection().identity()?;
        supported(
            project.production_design.scene_boundaries.is_empty(),
            "The mixed native route has no typed lowering for declared SceneBoundaries",
        )?;
        supported(
            project.scenes.len() == 3
                && project.scenes[0].renderer == RendererKind::MotionCanvas
                && project.scenes[1].renderer == RendererKind::ManimCommunity
                && project.scenes[2].renderer == RendererKind::MotionCanvas,
            "The admitted hybrid route requires exactly MC / static Manim / MC",
        )?;
        let profile = project
            .deliverables
            .iter()
            .find(|row| row.id == deliverable_id)
            .ok_or_else(|| invalid("Hybrid saved master profile is absent"))?;
        supported(
            profile.width == 1920
                && profile.height == 1080
                && profile.frame_rate.num == 30
                && profile.frame_rate.den == 1
                && options.frame_rate.num == 30
                && options.frame_rate.den == 1
                && profile.video_codec == VideoCodec::H264
                && profile.audio_codec == AudioCodec::Aac
                && profile.audio_sample_rate_hz == 48_000
                && profile.container == OutputContainer::Mp4
                && profile.color_space == OutputColorSpace::Rec709
                && !profile.captions
                && !profile.burn_in_captions
                && profile.included_scene_ids.is_empty(),
            "The admitted hybrid master is full-cut 1080p30 Rec.709 H264/AAC without captions",
        )?;
        supported(
            project.audio.sfx_cues.is_empty()
                && project.audio.cues.is_empty()
                && project.audio.transcript.is_empty()
                && project
                    .production_design
                    .delivery_recipes
                    .get(&deliverable_id)
                    .is_none_or(|recipe| recipe.original_mix.is_none()),
            "The hybrid native route cannot discard authored mixing or captions",
        )?;

        let mut counts = Vec::with_capacity(project.scenes.len());
        let mut cursor = 0;
        for scene in &project.scenes {
            let start = exact_frames(scene.start.num, scene.start.den, options.frame_rate)?;
            let frames = exact_frames(scene.duration.num, scene.duration.den, options.frame_rate)?;
            supported(
                start == cursor && frames == 60,
                "The admitted hybrid cut requires contiguous sixty-frame contributions",
            )?;
            cursor = bounded_add(cursor, frames)?;
            counts.push(frames);
        }
        // This guard describes the currently implemented finite route. Costs
        // below use the actual canonical clocks, never a caller's limits or a
        // global replacement for the general MC or Manim producers.
        supported(
            cursor == 180,
            "The admitted hybrid cut contains 180 canonical frames",
        )?;

        let segments = plan_motion_canvas_segments(&project, deliverable_id, options)?;
        supported(
            segments.len() == 2,
            "Hybrid MC source partition is not exactly two runs",
        )?;
        let mut motion = Vec::with_capacity(segments.len());
        let mut motion_frames = 0;
        let mut frame_reservation = MOTION_ROOT_FRAME_RESERVE_MIN_BYTES;
        for (index, segment) in segments.iter().enumerate() {
            let ordinal = index * 2;
            let scene = &project.scenes[ordinal];
            require(
                segment.scene_ids == [scene.id]
                    && segment.frame_count == counts[ordinal]
                    && exact_frames(
                        segment.global_start.num,
                        segment.global_start.den,
                        options.frame_rate,
                    )? == exact_frames(scene.start.num, scene.start.den, options.frame_rate)?
                    && segment.film.output.width == profile.width
                    && segment.film.output.height == profile.height,
                "Hybrid MC plan differs from the canonical contribution",
            )?;
            let pixels = bounded_mul(
                u64::from(segment.film.output.width),
                u64::from(segment.film.output.height),
            )?;
            frame_reservation =
                frame_reservation.max(bounded_add(bounded_mul(pixels, 4)?, 1_048_576)?);
            motion_frames = bounded_add(motion_frames, segment.frame_count)?;
            motion.push(MotionContribution {
                segment_id: segment.id.clone(),
                scene_ids: segment.scene_ids.clone(),
                frames: segment.frame_count,
                output_ordinal: ordinal,
            });
        }
        let manim_scene_id = project.scenes[1].id;
        manim_graph::require_non_graph_saved_selection(
            &coordinator.service,
            project_id,
            expected,
            manim_scene_id,
            deliverable_id,
        )?;
        // Compile static source BEFORE the first MC effect; unsupported Manim
        // features cannot be discovered only after paying for native renders.
        let source = crate::manim_static_projection::prepare_saved_manim_static_source(
            &coordinator.service,
            project_id,
            expected,
            manim_scene_id,
            deliverable_id,
        )?;
        let manim_frames = u64::from(source.plan.clock.frame_count);
        require(
            manim_frames == counts[1]
                && source.plan.clock.project_start_frame == counts[0]
                && source.plan.clock.project_end_frame_exclusive
                    == bounded_add(counts[0], counts[1])?
                && source.plan.clock.local_start_frame == 0
                && source.plan.render_profile.width == profile.width
                && source.plan.render_profile.height == profile.height
                && source.plan.render_profile.frame_rate == options.frame_rate.num,
            "Static Manim plan differs from its canonical clock or saved master",
        )?;
        let manim_plan = serde_json::to_value(&source.plan)
            .map_err(|_| invalid("Hybrid Manim source cannot be encoded"))?;
        let manim_profile = serde_json::to_value(source.plan.render_profile)
            .map_err(|_| invalid("Hybrid Manim profile cannot be encoded"))?;

        let voice_id = profile
            .voice_track_id
            .ok_or_else(|| invalid("Hybrid original voice is absent"))?;
        let voice = project
            .audio
            .voice_tracks
            .iter()
            .find(|row| row.id == voice_id)
            .ok_or_else(|| invalid("Hybrid original voice is not saved in the Project"))?;
        supported(
            voice.sample_rate_hz == 48_000
                && voice.channels == 2
                && exact_frames(
                    voice.measured_duration.num,
                    voice.measured_duration.den,
                    options.frame_rate,
                )? == cursor,
            "The hybrid original voice must be stereo 48kHz on the canonical timeline",
        )?;
        let verified = coordinator
            .service
            .verified_master_voice(project_id, expected, voice_id)
            .map_err(storage_error)?;
        let audio_frames = bounded_mul(cursor, 48_000)? / u64::from(options.frame_rate.num);
        let source_audio_bytes = bounded_add(44, bounded_mul(audio_frames, 4)?)?;
        require(
            verified.sha256 == voice.source_sha256 && verified.size_bytes == source_audio_bytes,
            "Hybrid saved voice identity or PCM byte count differs from its canonical clock",
        )?;
        let parent = verified
            .path
            .parent()
            .ok_or_else(|| invalid("Hybrid original voice has no CAS parent"))?;
        let name = verified
            .path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| invalid("Hybrid original voice has no valid CAS member"))?;
        let wave =
            read_verified_production_artifact(parent, name, &verified.sha256, source_audio_bytes)?;
        verify_original_pcm(&wave, audio_frames)?;

        let project_sha256 = project_digest(&project)?;
        let namespace = format!("mw-hybrid-{}", &project_sha256[..20]);
        // The full saved Project digest includes generation/revision. Distinct
        // saved profiles also receive distinct IDs. Same source reuses exactly
        // these IDs; neither caller labels nor process identity participate.
        let request_prefix = format!(
            "hybrid180-{}",
            request_digest(
                "native.hybrid.original",
                &json!({"project_sha256":project_sha256,"deliverable_id":deliverable_id})
            )?
        );
        let source_plan = json!({"schema":"motionwright.native.hybrid-source-plan/1",
        "project_sha256":project_sha256,"contributions":[
            {"renderer":"motion_canvas","scene_ids":motion[0].scene_ids,
                "segment_id":motion[0].segment_id,"output_start_frame":0,"frame_count":counts[0]},
            {"renderer":"manim_static","scene_ids":[manim_scene_id],
                "plan_sha256":source.closure.plan_sha256,"output_start_frame":counts[0],"frame_count":counts[1]},
            {"renderer":"motion_canvas","scene_ids":motion[1].scene_ids,
                "segment_id":motion[1].segment_id,"output_start_frame":bounded_add(counts[0],counts[1])?,
                "frame_count":counts[2]}
        ]});
        let segment_count =
            u64::try_from(motion.len()).map_err(|_| invalid("Hybrid MC count overflows"))?;
        let mc_polls = bounded_add(
            MOTION_RENDER_POLL_DEADLINE_SECS * 1_000 / MOTION_RENDER_POLL_INTERVAL_MS,
            MOTION_ROOT_NON_POLL_ATTEMPTS_PER_SEGMENT,
        )?;
        let manim_polls = bounded_add(
            MANIM_RENDER_POLL_DEADLINE_SECS * 1_000 / MANIM_RENDER_POLL_INTERVAL_MS,
            2,
        )?;
        // Encodes + project/sequence/track + four calls per clip + final
        // lookup/duration + plan/start/result + bounded status + one mux.
        let mlt_calls = bounded_add(
            segment_count,
            bounded_add(bounded_mul(counts.len() as u64, 4)?, 3 + 2 + 3 + 330 + 1)?,
        )?;
        let attempts = bounded_add(
            bounded_add(bounded_mul(segment_count, mc_polls)?, manim_polls)?,
            mlt_calls,
        )?;
        // Actual native frame passes: MC, Manim, two MC encodes, one semantic
        // timeline, one mux. Manim is never charged as a fictitious MC encode.
        let render_frames = bounded_add(
            bounded_add(motion_frames, manim_frames)?,
            bounded_add(motion_frames, bounded_mul(cursor, 2)?)?,
        )?;
        let mc_output = bounded_add(
            bounded_mul(motion_frames, frame_reservation)?,
            bounded_add(
                bounded_mul(bounded_mul(segment_count, 2)?, MAX_BRAND_FONT_BYTES)?,
                bounded_mul(
                    bounded_mul(segment_count, 3)?,
                    MOTION_ROOT_FRAME_RESERVE_MIN_BYTES,
                )?,
            )?,
        )?;
        let mc_output = bounded_add(
            mc_output,
            bounded_mul(
                segment_count,
                MC_SOURCE_MAX_BYTES + MC_SOURCE_RECEIPT_MAX_BYTES,
            )?,
        )?;
        let mut encode_output = 0;
        for contribution in &motion {
            encode_output = bounded_add(
                encode_output,
                mlt_encode_output_bytes(
                    profile.width,
                    profile.height,
                    contribution.frames,
                    &format!("{namespace}-mc-{}.mkv", contribution.output_ordinal),
                    MEDIA_MAX_BYTES,
                )?,
            )?;
        }
        let mlt_output = [
            encode_output,
            bounded_mul(counts.len() as u64, mlt_import_output_bytes()?)?,
            mlt_timeline_output_bytes(
                profile.width,
                profile.height,
                cursor,
                counts.len() as u64,
                &format!("{namespace}-visual.mkv"),
            )?,
            mlt_mux_output_bytes(
                profile.width,
                profile.height,
                cursor,
                &format!("{namespace}-master.mp4"),
                MASTER_MAX_BYTES,
            )?,
        ]
        .into_iter()
        .try_fold(0, bounded_add)?;
        // Encodes, imports, timeline and master retain owned/raw originals.
        // The same costs are debited before their first actual SDK dispatch.
        let output_bytes = bounded_add(
            bounded_add(mc_output, manim_output_bytes(manim_frames)?)?,
            bounded_add(mlt_output, source_audio_bytes)?,
        )?;
        let limits = RootBudgetUnits {
            attempts,
            model_calls: 0,
            render_frames,
            output_bytes,
        };
        let width = profile.width;
        let height = profile.height;
        require(
            coordinator
                .service
                .project(project_id)
                .map_err(storage_error)?
                == project,
            "Hybrid canonical Project changed during source and resource preflight",
        )?;
        Ok(Self {
            project,
            expected: expected.clone(),
            deliverable_id,
            options: options.clone(),
            connection_identity,
            project_sha256,
            namespace,
            request_prefix,
            source_plan,
            motion,
            motion_frames,
            frame_reservation,
            manim_scene_id,
            manim_plan,
            manim_plan_sha256: source.closure.plan_sha256,
            manim_profile,
            manim_frames,
            total_frames: cursor,
            width,
            height,
            source_audio_sha256: verified.sha256,
            source_audio_bytes,
            source_audio_path: verified.path,
            limits,
        })
    }
}

fn verify_original_pcm(bytes: &[u8], sample_frames: u64) -> NativeResult<()> {
    let pcm_bytes = bounded_mul(sample_frames, 4)?;
    require(
        bytes.len() as u64 == bounded_add(44, pcm_bytes)?,
        "Hybrid original PCM byte count differs from the canonical timeline",
    )?;
    // The finite Film180 input is the original 44-byte PCM16 WAV format. No
    // conversion, padding, metadata rewriting or substituted waveform occurs.
    require(
        &bytes[0..4] == b"RIFF"
            && &bytes[8..16] == b"WAVEfmt "
            && &bytes[36..40] == b"data"
            && u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as u64 == bytes.len() as u64 - 8
            && u32::from_le_bytes(bytes[16..20].try_into().unwrap()) == 16
            && u16::from_le_bytes(bytes[20..22].try_into().unwrap()) == 1
            && u16::from_le_bytes(bytes[22..24].try_into().unwrap()) == 2
            && u32::from_le_bytes(bytes[24..28].try_into().unwrap()) == 48_000
            && u32::from_le_bytes(bytes[28..32].try_into().unwrap()) == 192_000
            && u16::from_le_bytes(bytes[32..34].try_into().unwrap()) == 4
            && u16::from_le_bytes(bytes[34..36].try_into().unwrap()) == 16
            && u32::from_le_bytes(bytes[40..44].try_into().unwrap()) as u64 == pcm_bytes,
        "Hybrid saved input is not the unchanged canonical PCM16 stereo WAV",
    )
}

impl HybridRootBudgetScope {
    fn recheck(&self, coordinator: &ProductionCoordinator, project: &Project) -> NativeResult<()> {
        if let Some(grant) = &coordinator.hybrid_project_grant {
            grant.recheck(coordinator)?;
        }
        require(
            coordinator.active_motion_budget.is_none(),
            "Hybrid root cannot coexist with a second native root",
        )?;
        require(
            *project == self.plan.project
                && coordinator
                    .service
                    .project(project.id)
                    .map_err(storage_error)?
                    == self.plan.project
                && coordinator.client.connection().resource == project.resource_key()
                && coordinator.client.connection().identity()? == self.plan.connection_identity,
            "Hybrid native source or original provider connection changed",
        )?;
        let current = coordinator
            .service
            .root_operation_budget(project.id, self.root.root_id)
            .map_err(storage_error)?;
        require(
            current.project_id == self.root.project_id
                && current.root_id == self.root.root_id
                && current.generation == self.root.generation
                && current.revision == self.root.revision
                && current.operation_key == self.root.operation_key
                && current.limits == self.plan.limits,
            "Hybrid continuation does not retain its original immutable accounting root",
        )
    }

    pub(super) fn require_motion_selection(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        scene_ids: &[Uuid],
    ) -> NativeResult<()> {
        self.recheck(coordinator, project)?;
        let selected = self
            .plan
            .motion
            .iter()
            .flat_map(|row| row.scene_ids.iter().copied())
            .collect::<Vec<_>>();
        require(
            matches!(&self.phase, HybridPhase::Sources)
                && deliverable_id == self.plan.deliverable_id
                && *options == self.plan.options
                && scene_ids == selected.as_slice(),
            "Hybrid MC scope differs from its original Film selection",
        )
    }

    pub(super) fn retain_motion_source(
        &self,
        coordinator: &ProductionCoordinator,
        index: usize,
        segment: &crate::film::MotionCanvasSegment,
        prefix: &str,
        fingerprint: &str,
        applied: &Value,
    ) -> NativeResult<()> {
        self.recheck(coordinator, &self.plan.project)?;
        let grant = coordinator.hybrid_project_grant.as_ref().ok_or_else(|| {
            invalid("Hybrid MC source capture has no original project-grant observation")
        })?;
        let planned = self
            .plan
            .motion
            .get(index)
            .ok_or_else(|| invalid("Hybrid MC source index is outside the original plan"))?;
        let request = format!("{prefix}:apply");
        require(
            matches!(&self.phase, HybridPhase::Sources)
                && planned.segment_id == segment.id
                && planned.output_ordinal == index * 2
                && request == format!("{}-mc:motion:{index}:apply", self.plan.request_prefix)
                && is_sha256(fingerprint)
                && applied.get("replayed").and_then(Value::as_bool) == Some(false),
            "Hybrid MC source snapshot is not the original applied segment",
        )?;
        let original = coordinator
            .service
            .latest_production_receipt(self.plan.project.id, &request)
            .map_err(storage_error)?
            .ok_or_else(|| backend("Hybrid MC apply has no original terminal receipt"))?;
        require(
            original.stage == "completed"
                && original.command == "driver.motion-canvas.composition.apply"
                && original.generation == self.plan.expected.generation
                && original.revision == self.plan.expected.revision
                && original.payload.get("result") == applied.get("result")
                && original
                    .payload
                    .pointer("/result/data/fingerprint")
                    .and_then(Value::as_str)
                    == Some(fingerprint)
                && original
                    .payload
                    .pointer("/result/data/applied")
                    .and_then(Value::as_bool)
                    == Some(true)
                && original
                    .payload
                    .pointer("/result/data/execution_status")
                    .and_then(Value::as_str)
                    == Some("completed")
                && applied.get("receipt")
                    == Some(
                        &serde_json::to_value(&original)
                            .map_err(|_| backend("Hybrid MC apply receipt cannot be observed"))?,
                    ),
            "Hybrid MC source capture lacks the charged original completed apply",
        )?;
        let bytes = read_verified_production_artifact(
            &grant.receipt.project_root,
            "semwright-motion.json",
            fingerprint,
            MC_SOURCE_MAX_BYTES,
        )?;
        let name = format!(
            "{}-mc-{}.semwright-motion.original.json",
            self.plan.namespace, planned.output_ordinal
        );
        let receipt_name = format!(
            "{}-mc-{}.semwright-motion-custody.original.json",
            self.plan.namespace, planned.output_ordinal
        );
        let document = json!({"schema":"motionwright.native.hybrid-mc-source-custody/1",
            "ordinal":planned.output_ordinal,"segment_id":planned.segment_id,
            "project_sha256":self.plan.project_sha256,"source_fingerprint":fingerprint,
            "source_bytes":bytes.len(),"snapshot_path":name,"grant_receipt_sha256":grant.original.sha256,
            "apply_request_id":request,"apply_receipt_id":original.id,"root_operation_id":self.root.root_id,
            "apply_terminal_observed":true,"source_bytes_copied_without_reserialization":true,
            "automatic_retry":false,"native_render_started_by_capture":false,"editable_cycle_acceptance":false});
        let mut receipt_bytes = serde_json::to_vec_pretty(&document)
            .map_err(|_| backend("Hybrid MC source custody receipt cannot be encoded"))?;
        receipt_bytes.push(b'\n');
        require(
            receipt_bytes.len() as u64 <= MC_SOURCE_RECEIPT_MAX_BYTES,
            "Hybrid MC source custody receipt exceeds its charged bound",
        )?;
        // Both writes are already charged by the original composition.apply
        // dispatch. A failure preserves its files and never remints the debit.
        write_hybrid_source_original(
            &grant.receipt.output_root,
            &grant.output_identity,
            &name,
            &bytes,
            MC_SOURCE_MAX_BYTES,
        )?;
        require(
            read_verified_production_artifact(
                &grant.receipt.project_root,
                "semwright-motion.json",
                fingerprint,
                MC_SOURCE_MAX_BYTES,
            )? == bytes,
            "Hybrid MC original changed during source custody",
        )?;
        grant.recheck(coordinator)?;
        write_hybrid_source_original(
            &grant.receipt.output_root,
            &grant.output_identity,
            &receipt_name,
            &receipt_bytes,
            MC_SOURCE_RECEIPT_MAX_BYTES,
        )?;
        grant.recheck(coordinator)
    }

    pub(super) fn verify_motion_output(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        deliverable_id: Uuid,
        rendered: &MotionCanvasRenderEvidence,
    ) -> NativeResult<()> {
        self.recheck(coordinator, project)?;
        require(
            deliverable_id == self.plan.deliverable_id
                && rendered.project_resource == project.resource_key()
                && rendered.generation == project.generation
                && rendered.revision == project.revision
                && rendered.deliverable_id == deliverable_id
                && rendered.frame_rate == self.plan.options.frame_rate
                && rendered.segments.len() == self.plan.motion.len(),
            "Hybrid Motion Canvas outputs belong to another original source",
        )?;
        let mut jobs = std::collections::BTreeSet::new();
        for (actual, planned) in rendered.segments.iter().zip(&self.plan.motion) {
            require(
                actual.segment_id == planned.segment_id
                    && actual.scene_ids == planned.scene_ids
                    && actual.frame_count == planned.frames
                    && !actual.job_ref.is_empty()
                    && !actual.plan_ref.is_empty()
                    && !actual.fingerprint.is_empty()
                    && jobs.insert(actual.job_ref.as_str()),
                "Hybrid Motion Canvas contribution differs from its original native plan",
            )?;
            ensure_native_motion_verification(&actual.verification)?;
            let manifest = required_string(
                &actual.artifact,
                "/manifest",
                "Hybrid MC manifest is absent",
            )?;
            let sha = required_string(
                &actual.artifact,
                "/manifest_sha256",
                "Hybrid MC manifest digest is absent",
            )?;
            let _ = read_verified_production_artifact(
                &coordinator.client.connection().output_root,
                &manifest,
                &sha,
                8 * 1024 * 1024,
            )?;
            if let Some(grant) = &coordinator.hybrid_project_grant {
                let name = format!(
                    "{}-mc-{}.semwright-motion.original.json",
                    self.plan.namespace, planned.output_ordinal
                );
                let _ = read_verified_production_artifact(
                    &grant.receipt.output_root,
                    &name,
                    &actual.fingerprint,
                    MC_SOURCE_MAX_BYTES,
                )?;
            }
        }
        Ok(())
    }

    fn verify_manim_output(
        &self,
        coordinator: &ProductionCoordinator,
        original: &Value,
    ) -> NativeResult<()> {
        require(
            original.get("renderer").and_then(Value::as_str) == Some("manim-community")
                && original.get("project_revision").and_then(Value::as_u64)
                    == Some(self.plan.expected.revision)
                && original.get("scene_id") == Some(&json!(self.plan.manim_scene_id))
                && original.get("plan_sha256").and_then(Value::as_str)
                    == Some(self.plan.manim_plan_sha256.as_str())
                && original.get("profile") == Some(&self.plan.manim_profile)
                && original
                    .pointer("/artifact/static_source_custody_error")
                    .is_none(),
            "Hybrid static Manim output differs from its canonical source",
        )?;
        let native_artifact = original
            .pointer("/result/result/data/artifact")
            .ok_or_else(|| backend("Hybrid static Manim original native artifact is absent"))?;
        let path = required_string(
            native_artifact,
            "/relative_path",
            "Hybrid Manim artifact path is absent",
        )?;
        let sha = required_string(
            native_artifact,
            "/sha256",
            "Hybrid Manim artifact digest is absent",
        )?;
        let raw = read_verified_production_artifact(
            &coordinator.client.connection().output_root,
            &path,
            &sha,
            MEDIA_MAX_BYTES,
        )?;
        require(
            native_artifact.get("bytes").and_then(Value::as_u64) == Some(raw.len() as u64),
            "Hybrid original Manim video byte count changed",
        )?;
        let plan: motionwright_manim_profile::ManimScenePlanV2 =
            serde_json::from_value(self.plan.manim_plan.clone())
                .map_err(|_| invalid("Hybrid original Manim source cannot be reopened"))?;
        let retained_source = crate::manim_static_projection::verify_rendered_manim_static_source(
            &coordinator.client.connection().output_root,
            &path,
            &plan,
            native_artifact,
        )?;
        require(
            original.pointer("/artifact/static_source") == Some(&retained_source),
            "Hybrid original static Manim source was not retained unchanged",
        )
    }

    fn stages_original_audio(&self, request_id: &str, command: &str) -> bool {
        matches!(&self.phase, HybridPhase::Sources)
            && command == "driver.motion-canvas.render.start"
            && request_id == format!("{}-mc:motion:0:render-start", self.plan.request_prefix)
    }

    pub(super) fn dispatch_budget(
        &self,
        coordinator: &ProductionCoordinator,
        project: &Project,
        request_id: &str,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<(Uuid, RootBudgetUnits)> {
        self.recheck(coordinator, project)?;
        let mut units = self.units_for(command, args, mutation)?;
        if self.stages_original_audio(request_id, command) {
            units.output_bytes = bounded_add(units.output_bytes, self.plan.source_audio_bytes)?;
        }
        Ok((self.root.root_id, units))
    }

    pub(super) fn stage_after_admission(
        &self,
        coordinator: &ProductionCoordinator,
        request_id: &str,
        command: &str,
    ) -> NativeResult<()> {
        if !self.stages_original_audio(request_id, command) {
            return Ok(());
        }
        self.recheck(coordinator, &self.plan.project)?;
        let intent = coordinator
            .service
            .latest_production_receipt(self.plan.project.id, request_id)
            .map_err(storage_error)?
            .ok_or_else(|| backend("Hybrid input has no admitted dispatch intent"))?;
        require(
            intent.stage == "dispatching"
                && intent.command == command
                && intent.generation == self.plan.expected.generation
                && intent.revision == self.plan.expected.revision
                && intent.payload.get("mutation").and_then(Value::as_bool) == Some(true),
            "Hybrid original input cannot be staged outside its charged native start",
        )?;
        let source_parent = self
            .plan
            .source_audio_path
            .parent()
            .ok_or_else(|| backend("Hybrid original CAS parent is unavailable"))?;
        let source_name = self
            .plan
            .source_audio_path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| backend("Hybrid original CAS member is unavailable"))?;
        let bytes = read_verified_production_artifact(
            source_parent,
            source_name,
            &self.plan.source_audio_sha256,
            self.plan.source_audio_bytes,
        )?;
        require(
            bytes.len() as u64 == self.plan.source_audio_bytes,
            "Hybrid original source WAV changed before its charged staging",
        )?;
        let output = &coordinator.client.connection().output_root;
        let metadata = safe_metadata(output)?;
        require(
            metadata.is_dir(),
            "Hybrid native output root is not an admitted directory",
        )?;
        let path = output.join(format!("{}-source.wav", self.plan.namespace));
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
            options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
        }
        #[cfg(not(any(unix, windows)))]
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Hybrid original audio staging is unavailable on this platform",
        ));
        let mut file = options.open(&path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                Error::new(
                    ErrorCode::Conflict,
                    "Hybrid original WAV already exists; preserve the original attempt",
                )
            } else {
                backend("Hybrid original WAV cannot be exclusively staged")
            }
        })?;
        file.write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| {
                backend(
                    "Hybrid original WAV staging did not complete; retain its charged partial file",
                )
            })?;
        drop(file);
        self.verify_staged_audio(coordinator)?;
        // Reopen the original CAS too; this never claims continuous isolation
        // of all ancestors between finite file observations.
        require(
            read_verified_production_artifact(
                source_parent,
                source_name,
                &self.plan.source_audio_sha256,
                self.plan.source_audio_bytes,
            )? == bytes,
            "Hybrid original CAS bytes changed during native input staging",
        )
    }

    fn verify_staged_audio(&self, coordinator: &ProductionCoordinator) -> NativeResult<()> {
        let bytes = read_verified_production_artifact(
            &coordinator.client.connection().output_root,
            &format!("{}-source.wav", self.plan.namespace),
            &self.plan.source_audio_sha256,
            self.plan.source_audio_bytes,
        )?;
        require(
            bytes.len() as u64 == self.plan.source_audio_bytes,
            "Hybrid staged original WAV has a different byte count",
        )
    }

    fn units_for(
        &self,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<RootBudgetUnits> {
        match &self.phase {
            HybridPhase::Sources if MOTION_CANVAS_COMMANDS.contains(&command) => {
                // Reuse the existing MC mutation classes and output bound.
                let motion = MotionRootBudgetScope {
                    root_id: self.root.root_id,
                    max_render_frames: self.plan.motion_frames,
                    max_frame_bytes: self.plan.frame_reservation,
                    mlt_route: None,
                    mlt: None,
                    audio_analysis: false,
                };
                let mut units = motion.units_for(command, args, mutation)?;
                if command == "driver.motion-canvas.composition.apply" {
                    units.output_bytes = bounded_add(
                        units.output_bytes,
                        MC_SOURCE_MAX_BYTES + MC_SOURCE_RECEIPT_MAX_BYTES,
                    )?;
                }
                Ok(units)
            }
            HybridPhase::Sources if command == "driver.manim-community.static.render.start" => {
                require(
                    mutation
                        && args
                            == &json!({"plan":self.plan.manim_plan,"profile":self.plan.manim_profile}),
                    "Hybrid Manim effect is not its preflighted original static source",
                )?;
                let mut units = one_attempt();
                units.render_frames = self.plan.manim_frames;
                units.output_bytes = manim_output_bytes(self.plan.manim_frames)?;
                Ok(units)
            }
            HybridPhase::Sources
                if matches!(
                    command,
                    "driver.manim-community.render.status" | "driver.manim-community.render.result"
                ) =>
            {
                require(
                    !mutation,
                    "Hybrid Manim observation cannot be reclassified as a mutation",
                )?;
                Ok(one_attempt())
            }
            HybridPhase::Mlt(binding) => self.mlt_units(binding, command, args, mutation),
            _ => Err(Error::new(
                ErrorCode::PermissionDenied,
                "Native command has no producer-owned hybrid continuation",
            )),
        }
    }

    fn mlt_units(
        &self,
        binding: &HybridMltBinding,
        command: &str,
        args: &Value,
        mutation: bool,
    ) -> NativeResult<RootBudgetUnits> {
        let mut units = one_attempt();
        let visual = format!("{}-visual.mkv", self.plan.namespace);
        match command {
            "driver.mlt-video.frames.encode" => {
                let encoded = args
                    .get("manifest_path")
                    .and_then(Value::as_str)
                    .and_then(|path| binding.encodes.get(path))
                    .ok_or_else(|| invalid("Hybrid encoder has no retained MC manifest"))?;
                require(
                    mutation
                        && args.get("root").and_then(Value::as_str) == Some("output")
                        && args.get("expected_manifest_sha256").and_then(Value::as_str)
                            == Some(encoded.manifest_sha256.as_str())
                        && args.get("output_path").and_then(Value::as_str)
                            == Some(encoded.output_path.as_str())
                        && args.get("max_bytes").and_then(Value::as_u64) == Some(encoded.max_bytes),
                    "Hybrid encoder differs from its retained real MC contribution",
                )?;
                units.render_frames = encoded.frames;
                units.output_bytes = mlt_encode_output_bytes(
                    self.plan.width,
                    self.plan.height,
                    encoded.frames,
                    &encoded.output_path,
                    encoded.max_bytes,
                )?;
            }
            "driver.mlt-video.render.plan" | "driver.mlt-video.render.start" => {
                require(
                    mutation == (command == "driver.mlt-video.render.start")
                        && args.get("profile").and_then(Value::as_str)
                            == Some("lossless-video-only")
                        && args.get("output").and_then(Value::as_str) == Some(visual.as_str()),
                    "Hybrid timeline differs from its closed native output",
                )?;
                if mutation {
                    units.render_frames = self.plan.total_frames;
                    units.output_bytes = mlt_timeline_output_bytes(
                        self.plan.width,
                        self.plan.height,
                        self.plan.total_frames,
                        binding.source_paths.len() as u64,
                        &visual,
                    )?;
                }
            }
            "driver.mlt-video.av.mux" => {
                require(
                    mutation
                        && args.get("video_root").and_then(Value::as_str) == Some("output")
                        && args.get("video_path").and_then(Value::as_str) == Some(visual.as_str())
                        && args.get("audio_root").and_then(Value::as_str) == Some("output")
                        && args.get("audio_path").and_then(Value::as_str)
                            == Some(format!("{}-source.wav", self.plan.namespace).as_str())
                        && args.get("audio_sha256").and_then(Value::as_str)
                            == Some(self.plan.source_audio_sha256.as_str())
                        && args.get("output_path").and_then(Value::as_str)
                            == Some(format!("{}-master.mp4", self.plan.namespace).as_str())
                        && args.get("frame_count").and_then(Value::as_u64)
                            == Some(self.plan.total_frames)
                        && args.get("width").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.width))
                        && args.get("height").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.height))
                        && args.get("fps_num").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.options.frame_rate.num))
                        && args.get("fps_den").and_then(Value::as_u64)
                            == Some(u64::from(self.plan.options.frame_rate.den))
                        && args.get("sample_rate").and_then(Value::as_u64) == Some(48_000)
                        && args.get("channels").and_then(Value::as_u64) == Some(2)
                        && args.get("profile").and_then(Value::as_str) == Some("h264-aac-mp4")
                        && args.get("max_bytes").and_then(Value::as_u64) == Some(MASTER_MAX_BYTES),
                    "Hybrid mux differs from its original video/audio plan",
                )?;
                units.render_frames = self.plan.total_frames;
                units.output_bytes = mlt_mux_output_bytes(
                    self.plan.width,
                    self.plan.height,
                    self.plan.total_frames,
                    &format!("{}-master.mp4", self.plan.namespace),
                    MASTER_MAX_BYTES,
                )?;
            }
            "driver.mlt-video.asset.import" => {
                require(
                    mutation
                        && args.get("kind").and_then(Value::as_str) == Some("file")
                        && args.get("root").and_then(Value::as_str) == Some("output")
                        && args
                            .get("path")
                            .and_then(Value::as_str)
                            .is_some_and(|path| {
                                binding.source_paths.iter().any(|retained| retained == path)
                            }),
                    "Hybrid timeline cannot import an unretained contribution",
                )?;
                units.output_bytes = mlt_import_output_bytes()?;
            }
            "driver.mlt-video.project.create"
            | "driver.mlt-video.sequence.create"
            | "driver.mlt-video.track.create"
            | "driver.mlt-video.clip.insert" => {
                require(
                    mutation,
                    "Hybrid semantic edit requires its original mutation class",
                )?;
            }
            "driver.mlt-video.sequence.list"
            | "driver.mlt-video.track.list"
            | "driver.mlt-video.sequence.duration"
            | "driver.mlt-video.render.status"
            | "driver.mlt-video.render.result" => {
                require(
                    !mutation,
                    "Hybrid observation has a different mutation class",
                )?;
            }
            _ => {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Native command is outside the closed hybrid timeline continuation",
                ));
            }
        }
        Ok(units)
    }
}

impl ProductionCoordinator {
    /// Retain an observation of the existing closed CI owner's project grant.
    /// The receipt never adds a grant or a project path to ProductionConnection.
    pub fn with_hybrid_project_grant(
        mut self,
        receipt_path: &Path,
        receipt_sha256: &str,
        source_project_sha256: &str,
        connection_path: &Path,
    ) -> NativeResult<Self> {
        require(
            self.active_motion_budget.is_none()
                && self.active_hybrid_budget.is_none()
                && self.hybrid_project_grant.is_none(),
            "Hybrid project custody must precede its original native root",
        )?;
        require(
            std::env::var("GITHUB_ACTIONS").as_deref() == Ok("true")
                && std::env::var("CI").as_deref() == Ok("true")
                && std::env::var("RUNNER_OS").as_deref() == Ok("Linux")
                && std::env::var("GITHUB_EVENT_NAME").as_deref() == Ok("workflow_dispatch")
                && std::env::var("GITHUB_JOB").as_deref() == Ok("native-mlt-hybrid")
                && std::env::var("MOTIONWRIGHT_MLT_MODE").as_deref() == Ok("hybrid-av")
                && std::env::var("MW_MLT_SCOPE").as_deref() == Ok("hybrid-g2")
                && is_sha256(receipt_sha256)
                && is_sha256(source_project_sha256),
            "Hybrid project custody requires the original selected Linux CI context",
        )?;
        let parent = receipt_path
            .parent()
            .ok_or_else(|| invalid("Hybrid project custody parent is absent"))?;
        require(
            receipt_path.file_name().and_then(|name| name.to_str())
                == Some("hybrid-project-grant.original.json"),
            "Hybrid project custody has another original receipt name",
        )?;
        let raw = read_verified_production_artifact(
            parent,
            "hybrid-project-grant.original.json",
            receipt_sha256,
            HYBRID_GRANT_MAX_BYTES,
        )?;
        let receipt: HybridGrantReceipt = serde_json::from_slice(&raw)
            .map_err(|_| invalid("Hybrid project-grant original is malformed"))?;
        let lock: Value = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../SOURCE_LOCK.json"
        )))
        .map_err(|_| invalid("Compiled hybrid dependency lock is malformed"))?;
        let source_sha = &receipt.context.source_sha;
        let run_id = &receipt.context.run_id;
        require(
            receipt.schema == "motionwright.native.hybrid-project-grant/1"
                && receipt.source_project_sha256 == source_project_sha256
                && !receipt.native_effect_started
                && !receipt.automatic_retry
                && source_sha.len() == 40
                && source_sha
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                && std::env::var("GITHUB_SHA").as_deref() == Ok(source_sha.as_str())
                && !run_id.is_empty()
                && run_id.len() <= 32
                && !run_id.starts_with('0')
                && run_id.bytes().all(|b| b.is_ascii_digit())
                && std::env::var("GITHUB_RUN_ID").as_deref() == Ok(run_id.as_str())
                && receipt.context.run_attempt == "1"
                && std::env::var("GITHUB_RUN_ATTEMPT").as_deref() == Ok("1")
                && lock
                    .pointer("/dependencies/semwright/revision")
                    .and_then(Value::as_str)
                    == Some(receipt.context.semwright_sha.as_str()),
            "Hybrid grant source/run/SDK identity differs from the original producer",
        )?;
        let owner = self
            .client
            .connection()
            .output_root
            .parent()
            .ok_or_else(|| invalid("Hybrid output owner is absent"))?;
        require(
            receipt.output_root == owner.join("output")
                && receipt.output_root == self.client.connection().output_root
                && receipt.project_root == owner.join("project")
                && receipt.socket == owner.join("runtime/broker.sock")
                && receipt.socket == self.client.connection().socket
                && receipt.owner_config.path == owner.join("config/owner.toml")
                && receipt.connection.path == owner.join("config/motionwright-connection.json")
                && receipt.connection.path == connection_path,
            "Hybrid project grant does not belong to this existing owner/connection",
        )?;
        require(
            receipt.daemon.argv.len() == 5
                && Path::new(&receipt.daemon.argv[0])
                    .file_name()
                    .and_then(|name| name.to_str())
                    == Some("semwrightd")
                && receipt.daemon.argv[1] == "--config"
                && Path::new(&receipt.daemon.argv[2]) == receipt.owner_config.path
                && receipt.daemon.argv[3] == "--socket"
                && Path::new(&receipt.daemon.argv[4]) == receipt.socket,
            "Hybrid grant daemon did not select this owner configuration/socket",
        )?;
        let grant = HybridProjectGrant {
            project_identity: grant_path_identity(&receipt.project_root, true)?,
            output_identity: grant_path_identity(&receipt.output_root, true)?,
            socket_identity: grant_path_identity(&receipt.socket, false)?,
            connection_identity: self.client.connection().identity()?,
            original: HybridGrantFile {
                path: receipt_path.to_path_buf(),
                sha256: receipt_sha256.to_owned(),
                bytes: raw.len() as u64,
            },
            receipt,
        };
        grant.recheck(&self)?;
        grant.require_unused_snapshots(&format!("mw-hybrid-{}", &source_project_sha256[..20]))?;
        self.hybrid_project_grant = Some(Arc::new(grant));
        Ok(self)
    }

    pub(super) fn with_native_hybrid_scope(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<Self> {
        require(
            self.active_motion_budget.is_none() && self.active_hybrid_budget.is_none(),
            "Hybrid production cannot replace a retained native root",
        )?;
        let plan = HybridBudgetPlan::build(self, project_id, expected, deliverable_id, options)?;
        self.require_no_unresolved_scene_requests(
            project_id,
            &format!("{}-mc", plan.request_prefix),
            SceneRequestFamily::Motion,
        )?;
        self.require_no_unresolved_scene_requests(
            project_id,
            &format!("{}-manim", plan.request_prefix),
            SceneRequestFamily::Manim,
        )?;
        let root = self
            .service
            .create_root_operation_budget(RootOperationBudgetInput {
                project_id,
                generation: expected.generation,
                revision: expected.revision,
                operation_key: operation_key(&plan.project, deliverable_id),
                limits: plan.limits,
            })
            .map_err(storage_error)?;
        let mut scoped = self.clone();
        scoped.active_hybrid_budget = Some(HybridRootBudgetScope {
            root,
            plan: Arc::new(plan),
            phase: HybridPhase::Sources,
        });
        Ok(scoped)
    }

    /// Produce the admitted mixed native originals under one immutable root.
    /// Unsupported selections fail before any renderer is dispatched. A failed
    /// or UNKNOWN original is not retried under a new label or fresh budget.
    pub async fn realize_native_hybrid_sources(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<HybridNativeSources> {
        let grant = self.hybrid_project_grant.as_ref().ok_or_else(|| {
            invalid(
                "Hybrid native sources require the original verified owner project-grant receipt",
            )
        })?;
        grant.recheck(self)?;
        let current = self.service.project(project_id).map_err(storage_error)?;
        require(
            project_digest(&current)? == grant.receipt.source_project_sha256,
            "Hybrid owner project grant belongs to another saved source",
        )?;
        grant.require_unused_snapshots(&format!(
            "mw-hybrid-{}",
            &grant.receipt.source_project_sha256[..20]
        ))?;
        let scoped =
            self.with_native_hybrid_scope(project_id, expected, deliverable_id, options)?;
        let scope = scoped
            .active_hybrid_budget
            .as_ref()
            .ok_or_else(|| backend("Hybrid producer lost its original root"))?
            .clone();
        let motion_request = format!("{}-mc", scope.plan.request_prefix);
        let manim_request = format!("{}-manim", scope.plan.request_prefix);
        let motion_canvas = scoped
            .render_motion_canvas_segments(
                project_id,
                expected,
                &motion_request,
                deliverable_id,
                options,
            )
            .await?;
        let manim = scoped
            .realize_manim_static_scene(
                project_id,
                expected,
                &manim_request,
                scope.plan.manim_scene_id,
                deliverable_id,
            )
            .await?;
        scope.verify_motion_output(&scoped, &scope.plan.project, deliverable_id, &motion_canvas)?;
        scope.verify_manim_output(&scoped, &manim)?;
        scope.verify_staged_audio(&scoped)?;
        scope.recheck(&scoped, &scope.plan.project)?;
        let evidence = json!({"plan":scope.plan.source_plan,"motion_canvas":motion_canvas,
            "manim":[{"original_native_result":manim}]});
        Ok(HybridNativeSources {
            project: scope.plan.project.clone(),
            expected: expected.clone(),
            deliverable_id,
            options: options.clone(),
            project_sha256: scope.plan.project_sha256.clone(),
            namespace: scope.plan.namespace.clone(),
            request_prefix: scope.plan.request_prefix.clone(),
            plan: scope.plan.source_plan.clone(),
            motion_canvas,
            manim,
            evidence,
            owner: self.clone(),
            scope,
        })
    }

    // Only sibling production modules can borrow this private continuation.
    // A public source-evidence type is not an arbitrary execute capability.
    pub(super) fn hybrid_mlt_scope(&self, sources: &HybridNativeSources) -> NativeResult<Self> {
        require(
            Arc::ptr_eq(
                &self.motion_budget_roots,
                &sources.owner.motion_budget_roots,
            ),
            "Hybrid continuation belongs to another coordinator or Store owner",
        )?;
        sources.scope.recheck(self, &sources.project)?;
        require(
            sources.expected == sources.scope.plan.expected
                && sources.deliverable_id == sources.scope.plan.deliverable_id
                && sources.options == sources.scope.plan.options
                && sources.project_sha256 == sources.scope.plan.project_sha256
                && sources.namespace == sources.scope.plan.namespace
                && sources.request_prefix == sources.scope.plan.request_prefix
                && sources.plan == sources.scope.plan.source_plan,
            "Hybrid source selection differs from its original retained plan",
        )?;
        sources.scope.verify_motion_output(
            self,
            &sources.project,
            sources.deliverable_id,
            &sources.motion_canvas,
        )?;
        sources.scope.verify_manim_output(self, &sources.manim)?;
        sources.scope.verify_staged_audio(self)?;
        let mut encodes = BTreeMap::new();
        let mut source_paths = Vec::new();
        for (actual, planned) in sources
            .motion_canvas
            .segments
            .iter()
            .zip(&sources.scope.plan.motion)
        {
            let manifest = required_string(
                &actual.artifact,
                "/manifest",
                "Hybrid MC original manifest is absent",
            )?;
            let manifest_sha256 = required_string(
                &actual.artifact,
                "/manifest_sha256",
                "Hybrid MC original manifest digest is absent",
            )?;
            let output_path = format!("{}-mc-{}.mkv", sources.namespace, planned.output_ordinal);
            source_paths.push(output_path.clone());
            require(
                encodes
                    .insert(
                        manifest,
                        MltEncodeBudget {
                            manifest_sha256,
                            frames: planned.frames,
                            output_path,
                            max_bytes: MEDIA_MAX_BYTES,
                        },
                    )
                    .is_none(),
                "Hybrid MC contributions cannot share a substituted original manifest",
            )?;
        }
        source_paths.push(required_string(
            &sources.manim,
            "/artifact/relative_path",
            "Hybrid original Manim video is absent",
        )?);
        let mut scope = sources.scope.clone();
        scope.phase = HybridPhase::Mlt(HybridMltBinding {
            encodes,
            source_paths,
        });
        let mut scoped = self.clone();
        scoped.active_hybrid_budget = Some(scope);
        // Declared boundaries were refused before first effect. Do not pass an
        // MC-only typed selection as a purported proof for the Manim clip.
        scoped.scene_boundary_scope = None;
        Ok(scoped)
    }
}

#[cfg(test)]
mod hybrid_project_grant_tests {
    use super::*;

    fn owner_profile() -> (Vec<u8>, PathBuf, PathBuf) {
        let owner = if cfg!(windows) {
            Path::new(r"C:\synthetic-owner")
        } else {
            Path::new("/synthetic-owner")
        };
        let project = owner.join("project");
        let output = owner.join("output");
        let drivers = [
            "motion-canvas-driver.json",
            "mlt-video-driver.json",
            "manim-driver.json",
        ]
        .iter()
        .map(|name| {
            owner
                .join("config")
                .join(name)
                .to_string_lossy()
                .into_owned()
        })
        .collect::<Vec<_>>();
        let raw = format!(
            "drivers = {}\ndriver_network = false\n\n[policy]\nprofile = \"workspace\"\nallow = [\"driver:motion-canvas\", \"driver:mlt-video\", \"driver:manim-community\"]\n\n[[policy.filesystem]]\nname = \"project\"\npath = {}\nread = true\nwrite = true\n\n[[policy.filesystem]]\nname = \"output\"\npath = {}\nread = true\nwrite = true\n",
            serde_json::to_string(&drivers).unwrap(),
            serde_json::to_string(project.to_str().unwrap()).unwrap(),
            serde_json::to_string(output.to_str().unwrap()).unwrap(),
        )
        .into_bytes();
        (raw, project, output)
    }

    #[test]
    fn hybrid_project_grant_profile_binds_actual_roots_and_rejects_foreign_or_duplicate_grants() {
        let (raw, project, output) = owner_profile();
        assert!(project.is_absolute() && output.is_absolute());
        verify_owner_grant_profile(&raw, &project, &output).unwrap();
        let project_literal = serde_json::to_string(project.to_str().unwrap()).unwrap();
        let foreign_project = project
            .parent()
            .unwrap()
            .with_file_name("foreign-owner")
            .join("project");
        let foreign_literal = serde_json::to_string(foreign_project.to_str().unwrap()).unwrap();
        for (from, to) in [
            (project_literal.as_str(), foreign_literal.as_str()),
            ("name = \"output\"", "name = \"project\""),
            ("read = true", "read = false"),
            ("write = true", "write = false"),
            ("profile = \"workspace\"", "profile = \"observe\""),
        ] {
            let changed = String::from_utf8(raw.clone()).unwrap().replace(from, to);
            assert_ne!(changed.as_bytes(), raw.as_slice());
            assert!(verify_owner_grant_profile(changed.as_bytes(), &project, &output).is_err());
        }
    }

    #[test]
    fn hybrid_project_grant_profile_refuses_extra_toml_syntax_and_preserves_input_bytes() {
        let (raw, project, output) = owner_profile();
        verify_owner_grant_profile(&raw, &project, &output).unwrap();
        let mut changed = raw.clone();
        changed.extend_from_slice(b"\n[build]\nforeign = true\n");
        let error = verify_owner_grant_profile(&changed, &project, &output).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert_eq!(error.message, "Hybrid owner filesystem block changed");
        assert_eq!(raw, owner_profile().0);
    }

    #[test]
    fn hybrid_daemon_start_observation_rejects_foreign_dead_or_incomplete_process_records() {
        let raw = format!("42 (semwrightd) S {} 12345\n", ["0"; 18].join(" "));
        assert_eq!(daemon_start_from_stat(raw.as_bytes(), 42).unwrap(), 12345);
        assert!(daemon_start_from_stat(raw.as_bytes(), 43).is_err());
        assert!(daemon_start_from_stat(raw.replace(") S ", ") Z ").as_bytes(), 42).is_err());
        assert!(daemon_start_from_stat(b"42 (semwrightd) S 0", 42).is_err());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn hybrid_mc_source_snapshot_preserves_exact_bytes_and_refuses_replacement() {
        let directory = tempfile::tempdir().unwrap();
        let identity = grant_path_identity(directory.path(), true).unwrap();
        let name = "mw-hybrid-aabb-mc-0.semwright-motion.original.json";
        let original = b"{\n  \"source\": \"exact bytes, including whitespace\"\n}";
        write_hybrid_source_original(
            directory.path(),
            &identity,
            name,
            original,
            MC_SOURCE_MAX_BYTES,
        )
        .unwrap();
        let error = write_hybrid_source_original(
            directory.path(),
            &identity,
            name,
            b"different",
            MC_SOURCE_MAX_BYTES,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert_eq!(fs::read(directory.path().join(name)).unwrap(), original);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn hybrid_mc_source_snapshot_rejects_foreign_and_linked_roots_before_creation() {
        use std::os::unix::fs::symlink;
        let first = tempfile::tempdir().unwrap();
        let second = tempfile::tempdir().unwrap();
        let identity = grant_path_identity(first.path(), true).unwrap();
        let name = "mw-hybrid-aabb-mc-2.semwright-motion.original.json";
        assert!(
            write_hybrid_source_original(
                second.path(),
                &identity,
                name,
                b"original",
                MC_SOURCE_MAX_BYTES
            )
            .is_err()
        );
        let link = first.path().join("linked-output");
        symlink(second.path(), &link).unwrap();
        assert!(
            write_hybrid_source_original(&link, &identity, name, b"original", MC_SOURCE_MAX_BYTES)
                .is_err()
        );
        assert!(!second.path().join(name).exists());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn hybrid_mc_source_snapshot_refuses_oversize_before_exclusive_write() {
        let directory = tempfile::tempdir().unwrap();
        let identity = grant_path_identity(directory.path(), true).unwrap();
        let name = "mw-hybrid-aabb-mc-0.semwright-motion.original.json";
        let oversized = vec![b'x'; MC_SOURCE_MAX_BYTES as usize + 1];
        assert!(
            write_hybrid_source_original(
                directory.path(),
                &identity,
                name,
                &oversized,
                MC_SOURCE_MAX_BYTES
            )
            .is_err()
        );
        assert!(!directory.path().join(name).exists());
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    include!("hybrid_budget_tests.rs");
}
