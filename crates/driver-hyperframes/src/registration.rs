//! Manifest construction for the existing Owner/Driver Host registration.
//! This module grants nothing and launches nothing. Runtime files and tools
//! must be installed, licensed, sealed and admitted by the real owner flow.

use semwright_driver_sdk::{
    ApplicationMatch, DriverMount, DriverResources, DriverToolMount, Manifest, Transport,
};
use semwright_types::{Error, Result};
use std::path::PathBuf;

use crate::{
    CHROME_TOOL, DRIVER_ID, DRIVER_REQUEST_TIMEOUT_MS, FFMPEG_TOOL, FFPROBE_TOOL, NODE_TOOL,
    OPERATION_MOUNT, OwnerRuntimeProfile, RUNTIME_MOUNT, SOURCE_MOUNT, interfaces,
};

/// No Deserialize: WebView input cannot construct an owner installation.
pub struct OwnerInstallation {
    executable: PathBuf,
    executable_sha256: String,
    runtime: OwnerRuntimeProfile,
}

impl OwnerInstallation {
    pub fn new(
        executable: PathBuf,
        executable_sha256: String,
        runtime: OwnerRuntimeProfile,
    ) -> Result<Self> {
        if !executable.is_absolute() {
            return Err(Error::invalid(
                "HyperFrames driver executable must be owner-resolved",
            ));
        }
        crate::digest(&executable_sha256)?;
        runtime.validate()?;
        Ok(Self {
            executable,
            executable_sha256,
            runtime,
        })
    }

    /// Store these exact bytes as owner-profile.json in the independently
    /// granted read-only runtime root. The digest is a binding, not consent.
    pub fn runtime_profile_bytes(&self) -> Result<Vec<u8>> {
        self.runtime.bytes()
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
        let mut node = tool(
            "hyperframes-node-executable",
            NODE_TOOL,
            &self.runtime.tool_pins.node_sha256,
        );
        node.mounts = vec![
            SOURCE_MOUNT.into(),
            RUNTIME_MOUNT.into(),
            OPERATION_MOUNT.into(),
        ];
        node.dependencies = vec![CHROME_TOOL.into(), FFMPEG_TOOL.into(), FFPROBE_TOOL.into()];
        let manifest = Manifest {
            manifest_version: 1,
            protocol: 7,
            id: DRIVER_ID.into(),
            version: env!("CARGO_PKG_VERSION").into(),
            publisher: "motionwright".into(),
            executable: self.executable.clone(),
            sha256: self.executable_sha256.clone(),
            application: ApplicationMatch {
                desktop_id: Some("org.motionwright.Hyperframes".into()),
                process_names: vec![],
                supported_versions: vec![self.runtime.producer_version.clone()],
            },
            transport: Transport::StdioV1,
            mounts: vec![
                DriverMount {
                    root: SOURCE_MOUNT.into(),
                    read_only: true,
                    execute: false,
                },
                // Native libraries in the pinned installation need executable
                // mappings. This grant is explicit, separate from source data.
                DriverMount {
                    root: RUNTIME_MOUNT.into(),
                    read_only: true,
                    execute: true,
                },
                DriverMount {
                    root: OPERATION_MOUNT.into(),
                    read_only: false,
                    execute: false,
                },
            ],
            system_config: vec![],
            secrets: vec![],
            tools: vec![
                node,
                tool(
                    "hyperframes-chrome-executable",
                    CHROME_TOOL,
                    &self.runtime.tool_pins.chrome_sha256,
                ),
                tool(
                    "hyperframes-ffmpeg-executable",
                    FFMPEG_TOOL,
                    &self.runtime.tool_pins.ffmpeg_sha256,
                ),
                tool(
                    "hyperframes-ffprobe-executable",
                    FFPROBE_TOOL,
                    &self.runtime.tool_pins.ffprobe_sha256,
                ),
            ],
            network: false,
            nvidia_gpu: false,
            loopback_port: None,
            resources: DriverResources {
                open_files: 256,
                processes: 64,
                cpu_seconds: 300,
                operation_cpu_seconds: 240,
                address_space_bytes: 4 * 1024 * 1024 * 1024,
                file_size_bytes: 512 * 1024 * 1024,
            },
            request_timeout_ms: DRIVER_REQUEST_TIMEOUT_MS,
            interfaces: interfaces(),
        };
        manifest.validate()?;
        Ok(manifest)
    }
}
