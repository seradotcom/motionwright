//! Closed offline runtime package description. Compatibility here describes
//! reviewed entry/lock bytes and layout, never a version probe or execution grant.
use super::runtime_profile_platform as platform;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const SCHEMA: &str = "motionwright.runtime-profile-bundle/1";
pub const MANIFEST: &str = "runtime-profile.json";
pub const MAX_MANIFEST_BYTES: u64 = 8 * 1024 * 1024;
pub const MAX_FILE_BYTES: u64 = 512 * 1024 * 1024;
pub const MAX_TOTAL_BYTES: u64 = 2 * 1024 * 1024 * 1024;
pub const MAX_FILES: usize = 30_000;
pub const MAX_DIRECTORIES: usize = 8_192;
pub const MC_ENTRY_SHA: &str = "2352639a228212ac19459761cbc4914c02946199dc3e875fc329b68ccfb30b2e";
pub const MC_LOCK_SHA: &str = "47a141493beca9d80bbf50bb8729e46233048fad490bd5f8f92e965ec228d3a2";
pub const MC_PACKAGE_SHA: &str = "d9e8d22fa1b45a3c4caee639d6f1d2f5dca08eafa2c26cbe7fee493a643a4394";
pub const HF_ENTRY_SHA: &str = "e2352fe31555f06f8a423e1cad4d84e8ff196f9707d04961ab476855d080b7b0";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Profile {
    MotionCanvas,
    Hyperframes,
}
impl Profile {
    pub fn name(self) -> &'static str {
        match self {
            Self::MotionCanvas => "Motion Canvas",
            Self::Hyperframes => "HyperFrames",
        }
    }
    pub fn version(self) -> &'static str {
        match self {
            Self::MotionCanvas => "3.17.2",
            Self::Hyperframes => "0.8.181",
        }
    }
    pub fn key(self) -> &'static str {
        match self {
            Self::MotionCanvas => "motion_canvas",
            Self::Hyperframes => "hyperframes",
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FileRole {
    Node,
    RuntimeEntry,
    RuntimeLock,
    Browser,
    Dependency,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BundleFile {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
    pub executable: bool,
    pub role: FileRole,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BundleManifest {
    pub schema: String,
    pub profile: Profile,
    pub version: String,
    pub platform: String,
    pub semwright_source_pin: String,
    pub node_version: String,
    pub files: Vec<BundleFile>,
}
#[derive(Debug, Clone)]
pub struct ValidatedBundle {
    pub manifest: BundleManifest,
    pub directories: BTreeSet<String>,
    pub total_bytes: u64,
}
impl ValidatedBundle {
    pub fn role(&self, role: FileRole) -> &BundleFile {
        self.manifest
            .files
            .iter()
            .find(|entry| entry.role == role)
            .expect("validated required role")
    }
}
pub fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
pub fn relative_file(value: &str) -> bool {
    if value.is_empty() || value.len() > 1024 || value == MANIFEST || value.split('/').count() > 16
    {
        return false;
    }
    value.split('/').all(|part| {
        let lower = part.to_ascii_lowercase();
        !part.is_empty()
            && part.len() <= 255
            && !part.ends_with('.')
            && part
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._@+-".contains(&b))
            && !matches!(
                lower.as_str(),
                "." | ".." | ".git" | ".env" | ".npmrc" | ".netrc" | "credentials" | "secrets"
            )
            && !lower.ends_with(".session")
            && !lower.ends_with(".key")
            && !matches!(
                lower.split('.').next().unwrap_or(""),
                "con"
                    | "prn"
                    | "aux"
                    | "nul"
                    | "com1"
                    | "com2"
                    | "com3"
                    | "com4"
                    | "com5"
                    | "com6"
                    | "com7"
                    | "com8"
                    | "com9"
                    | "lpt1"
                    | "lpt2"
                    | "lpt3"
                    | "lpt4"
                    | "lpt5"
                    | "lpt6"
                    | "lpt7"
                    | "lpt8"
                    | "lpt9"
            )
    })
}
pub fn parse(bytes: &[u8], source_pin: &str) -> Result<ValidatedBundle, String> {
    const INVALID: &str =
        "The selected offline runtime package has an unsupported or unsafe description.";
    if bytes.is_empty() || bytes.len() as u64 > MAX_MANIFEST_BYTES {
        return Err(INVALID.into());
    }
    let manifest: BundleManifest = serde_json::from_slice(bytes).map_err(|_| INVALID)?;
    if manifest.schema != SCHEMA
        || manifest.version != manifest.profile.version()
        || manifest.semwright_source_pin != source_pin
        || source_pin.len() != 40
        || !source_pin
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || manifest.platform != platform::PLATFORM
        || (manifest.profile == Profile::Hyperframes && !platform::SUPPORTS_HYPERFRAMES)
        || manifest.node_version != "22.22.0"
        || manifest.files.is_empty()
        || manifest.files.len() > MAX_FILES
    {
        return Err(INVALID.into());
    }
    let mut names = BTreeSet::new();
    let mut folded = BTreeSet::new();
    let mut directories = BTreeSet::new();
    let mut folded_directories = BTreeMap::new();
    let mut total_bytes = 0_u64;
    let mut required = [0_u8; 4];
    for file in &manifest.files {
        if !relative_file(&file.path)
            || !is_sha256(&file.sha256)
            || (file.bytes == 0 && file.role != FileRole::Dependency)
            || file.bytes > MAX_FILE_BYTES
            || !names.insert(file.path.clone())
            || !folded.insert(file.path.to_ascii_lowercase())
            || (file.path != "tools/node" && !file.path.starts_with("runtime/"))
        {
            return Err(INVALID.into());
        }
        total_bytes = total_bytes.checked_add(file.bytes).ok_or(INVALID)?;
        if total_bytes > MAX_TOTAL_BYTES {
            return Err(INVALID.into());
        }
        let parts = file.path.split('/').collect::<Vec<_>>();
        for length in 1..parts.len() {
            let directory = parts[..length].join("/");
            if let Some(prior) =
                folded_directories.insert(directory.to_ascii_lowercase(), directory.clone())
                && prior != directory
            {
                return Err(INVALID.into());
            }
            directories.insert(directory);
        }
        if directories.len() > MAX_DIRECTORIES {
            return Err(INVALID.into());
        }
        let slot = match file.role {
            FileRole::Node => {
                if file.path != "tools/node" || !file.executable {
                    return Err(INVALID.into());
                }
                Some(0)
            }
            FileRole::RuntimeEntry => {
                let (path, digest) = match manifest.profile {
                    Profile::MotionCanvas => ("runtime/render.mjs", MC_ENTRY_SHA),
                    Profile::Hyperframes => {
                        ("runtime/semwright-hyperframes-runner.mjs", HF_ENTRY_SHA)
                    }
                };
                if file.path != path || file.sha256 != digest || file.executable {
                    return Err(INVALID.into());
                }
                Some(1)
            }
            FileRole::RuntimeLock => {
                let path = match manifest.profile {
                    Profile::MotionCanvas => "runtime/package-lock.json",
                    Profile::Hyperframes => "runtime/runtime-lock.json",
                };
                if file.path != path
                    || file.executable
                    || file.bytes > MAX_MANIFEST_BYTES
                    || (manifest.profile == Profile::MotionCanvas && file.sha256 != MC_LOCK_SHA)
                {
                    return Err(INVALID.into());
                }
                Some(2)
            }
            FileRole::Browser => {
                if !file.path.starts_with("runtime/") || !file.executable {
                    return Err(INVALID.into());
                }
                if manifest.profile == Profile::MotionCanvas
                    && (!file.path.starts_with(
                        "runtime/node_modules/playwright-core/.local-browsers/firefox-",
                    ) || !file.path.ends_with(platform::FIREFOX_SUFFIX))
                {
                    return Err(INVALID.into());
                }
                Some(3)
            }
            FileRole::Dependency => None,
        };
        if let Some(slot) = slot {
            required[slot] = required[slot].checked_add(1).ok_or(INVALID)?;
        }
    }
    if required != [1, 1, 1, 1]
        || directories
            .iter()
            .any(|directory| names.contains(directory))
        || directories
            .iter()
            .any(|directory| folded.contains(&directory.to_ascii_lowercase()))
    {
        return Err(INVALID.into());
    }
    if manifest.profile == Profile::MotionCanvas
        && !manifest.files.iter().any(|file| {
            file.path == "runtime/package.json" && file.sha256 == MC_PACKAGE_SHA && !file.executable
        })
    {
        return Err(INVALID.into());
    }
    Ok(ValidatedBundle {
        manifest,
        directories,
        total_bytes,
    })
}
