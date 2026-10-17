//! Concrete manifest construction for the existing Driver Host registration.
//!
//! Returning a manifest neither signs it nor grants access. Only the existing
//! owner registration/policy flow may bind these logical roots to real paths.
//! This module does not launch a process, inspect the filesystem or register
//! a provider. The current SDK requires real Linux/Windows Host validation;
//! macOS runtime-tool support is not inferred from this manifest.

use crate::{
    DRIVER_ID, DRIVER_REQUEST_TIMEOUT_MS, FFMPEG_TOOL, FFPROBE_TOOL, MEDIA_MOUNT, PYTHON_TOOL,
    RUNTIME_MOUNT, ToolPins, interfaces, require_sha256, sha256,
};
use semwright_driver_sdk::{
    ApplicationMatch, DriverMount, DriverResources, DriverToolMount, Manifest, Transport,
};
use semwright_types::{Error, Result};
use std::path::PathBuf;

pub const PYTHON_TOOL_ROOT: &str = "native-readback-python-executable";
pub const FFMPEG_TOOL_ROOT: &str = "native-readback-ffmpeg-executable";
pub const FFPROBE_TOOL_ROOT: &str = "native-readback-ffprobe-executable";

/// Owner installation data has no Deserialize implementation and is not a
/// WebView DTO. Real tool files are independently verified and sealed by Host.
pub struct OwnerReadbackInstallation {
    driver_executable: PathBuf,
    driver_sha256: String,
    tool_pins: ToolPins,
}

impl OwnerReadbackInstallation {
    pub fn new(
        driver_executable: PathBuf,
        driver_sha256: String,
        tool_pins: ToolPins,
    ) -> Result<Self> {
        if !driver_executable.is_absolute() {
            return Err(Error::invalid(
                "Owner readback driver executable must be absolute",
            ));
        }
        require_sha256(&driver_sha256)?;
        tool_pins.validate()?;
        Ok(Self {
            driver_executable,
            driver_sha256,
            tool_pins,
        })
    }

    pub fn manifest(&self) -> Result<Manifest> {
        let tool = |root: &str, name: &str, digest: &str| DriverToolMount {
            root: root.into(),
            name: name.into(),
            sha256: digest.into(),
            mounts: vec![],
            system_config: vec![],
            dependencies: vec![],
            nvidia_gpu: false,
            resources: None,
        };
        let mut python = tool(PYTHON_TOOL_ROOT, PYTHON_TOOL, &self.tool_pins.python_sha256);
        python.mounts = vec![MEDIA_MOUNT.into(), RUNTIME_MOUNT.into()];
        python.dependencies = vec![FFMPEG_TOOL.into(), FFPROBE_TOOL.into()];
        let manifest = Manifest {
            manifest_version: 1,
            protocol: 7,
            id: DRIVER_ID.into(),
            version: env!("CARGO_PKG_VERSION").into(),
            publisher: "motionwright".into(),
            executable: self.driver_executable.clone(),
            sha256: self.driver_sha256.clone(),
            application: ApplicationMatch {
                desktop_id: Some("org.motionwright.NativeReadback".into()),
                process_names: vec![],
                supported_versions: vec![],
            },
            transport: Transport::StdioV1,
            mounts: vec![
                DriverMount {
                    root: MEDIA_MOUNT.into(),
                    read_only: true,
                    execute: false,
                },
                // Explicit owner authority for reviewed runtime libraries.
                // Mounting a directory is not proof of its content closure or
                // Python bootstrap compatibility. The platform lane must show
                // those properties before this profile is activated.
                DriverMount {
                    root: RUNTIME_MOUNT.into(),
                    read_only: true,
                    execute: true,
                },
            ],
            system_config: vec![],
            secrets: vec![],
            tools: vec![
                python,
                tool(FFMPEG_TOOL_ROOT, FFMPEG_TOOL, &self.tool_pins.ffmpeg_sha256),
                tool(
                    FFPROBE_TOOL_ROOT,
                    FFPROBE_TOOL,
                    &self.tool_pins.ffprobe_sha256,
                ),
            ],
            network: false,
            nvidia_gpu: false,
            loopback_port: None,
            resources: DriverResources {
                open_files: 128,
                processes: 32,
                cpu_seconds: 300,
                operation_cpu_seconds: 180,
                address_space_bytes: 2 * 1024 * 1024 * 1024,
                file_size_bytes: 1024 * 1024,
            },
            request_timeout_ms: DRIVER_REQUEST_TIMEOUT_MS,
            interfaces: interfaces(),
        };
        manifest.validate()?;
        Ok(manifest)
    }

    /// Receipt binding for the exact proposed manifest, not an approval token.
    /// The owner must separately bind and retain the runtime directory closure.
    pub fn proposed_manifest_sha256(&self) -> Result<String> {
        Ok(sha256(&serde_json::to_vec(&self.manifest()?)?))
    }
}
