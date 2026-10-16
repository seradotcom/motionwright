//! Read-only project runtime diagnosis. Version-compatible Semwright CLI !=
//! authorized Broker, installed Driver Host, verified media or user approval.
//! This module cannot install software, read owner secrets, grant effects or
//! mutate creative source. Tauri owns the external CLI version-only probe.
use motionwright_domain::{Project, RendererKind, RightsStatus};
use serde::Serialize;
use uuid::Uuid;

pub const CREATIVE_DOCTOR_SCHEMA: &str = "motionwright.creative-runtime-doctor/1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CliEvidence {
    VersionCompatible,
    Unconfigured,
    Invalid,
    ResourceMismatch,
    Incompatible,
    Unavailable,
    BrowserDemo,
}
impl CliEvidence {
    pub fn from_runtime_status(status: &str) -> Self {
        match status {
            "ready" => Self::VersionCompatible,
            "unconfigured" => Self::Unconfigured,
            "invalid" => Self::Invalid,
            "resource_mismatch" => Self::ResourceMismatch,
            "incompatible" => Self::Incompatible,
            "browser_demo" => Self::BrowserDemo,
            _ => Self::Unavailable,
        }
    }
    fn reason(self) -> &'static str {
        match self {
            Self::VersionCompatible => {
                "Digest-bound Semwright CLI --version is compatible. Broker and Driver Host were not exercised."
            }
            Self::Unconfigured => {
                "Select an existing owner-provisioned, project-bound Semwright connection before native rendering."
            }
            Self::Invalid => {
                "The saved runtime reference or connection did not pass trusted local validation."
            }
            Self::ResourceMismatch => {
                "The Semwright connection targets a different project resource. No cross-project fallback."
            }
            Self::Incompatible => {
                "The selected Semwright CLI version does not match the pinned supported version."
            }
            Self::Unavailable => {
                "The version probe did not complete. No native runtime claim is made."
            }
            Self::BrowserDemo => {
                "Browser demo cannot inspect the owner's local runtime. No provider connection is fabricated."
            }
        }
    }
    fn status(self) -> &'static str {
        match self {
            Self::VersionCompatible => "cli_version_compatible",
            Self::Unconfigured => "unconfigured",
            Self::Invalid => "invalid",
            Self::ResourceMismatch => "resource_mismatch",
            Self::Incompatible => "incompatible",
            Self::Unavailable => "unavailable",
            Self::BrowserDemo => "browser_demo",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct CreativeDoctorCheck {
    pub id: &'static str,
    pub status: &'static str,
    pub detail: &'static str,
}
#[derive(Debug, Clone, Serialize)]
pub struct CreativeDoctorRenderer {
    pub renderer: &'static str,
    pub required_semwright_driver: Option<&'static str>,
    pub scene_count: usize,
    pub declared_extension_admitted: bool,
    pub provider_verified: bool,
    pub state: &'static str,
}
#[derive(Debug, Clone, Serialize)]
pub struct CreativeDoctorOutput {
    pub profile_id: Uuid,
    pub width: u32,
    pub height: u32,
    pub estimated_uncompressed_rgba_frame_bytes: u64,
    pub source_profile_validated: bool,
    pub render_and_delivery_verified: bool,
}
#[derive(Debug, Clone, Serialize)]
pub struct CreativeRuntimeDoctorReport {
    pub schema: &'static str,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub semwright_native_sdk_source_pin: String,
    pub runtime_version_evidence: &'static str,
    pub runtime_version_detail: &'static str,
    pub verdict: &'static str,
    pub renderers: Vec<CreativeDoctorRenderer>,
    pub outputs: Vec<CreativeDoctorOutput>,
    pub checks: Vec<CreativeDoctorCheck>,
    pub ready_for_native_production: bool,
    pub install_performed: bool,
    pub broker_dispatch_performed: bool,
    pub authorizing_grant_created: bool,
}

fn renderers(project: &Project) -> Vec<CreativeDoctorRenderer> {
    let declarations = [
        (
            RendererKind::MotionCanvas,
            "motion-canvas",
            Some("driver:motion-canvas"),
        ),
        (RendererKind::Mlt, "mlt", Some("driver:mlt-video")),
        (RendererKind::Blender, "blender", Some("driver:blender")),
        (
            RendererKind::ManimCommunity,
            "manim-community",
            Some("driver:manim-community"),
        ),
        (RendererKind::Remotion, "remotion", None),
        (RendererKind::ManimGl, "manim-gl", None),
    ];
    declarations
        .into_iter()
        .map(|(kind, name, driver)| {
            let scene_count = project
                .scenes
                .iter()
                .filter(|scene| scene.renderer == kind)
                .count();
            let admitted = project.extensions.iter().any(|extension| {
                extension.kind.renderer() == Some(kind.clone())
                    && extension.enabled
                    && extension.rights_status == RightsStatus::Cleared
            });
            let state = if scene_count == 0 {
                "not_used"
            } else if driver.is_some() {
                "native_provider_not_tested"
            } else if admitted {
                "optional_descriptor_only"
            } else {
                "optional_extension_not_admitted"
            };
            CreativeDoctorRenderer {
                renderer: name,
                required_semwright_driver: driver,
                scene_count,
                declared_extension_admitted: admitted,
                provider_verified: false,
                state,
            }
        })
        .collect()
}

/// Only project state and the **result** of the owner's bounded version probe
/// enter this method. Never pass a connection path, CLI binary or session.
pub fn inspect_creative_runtime(
    project: &Project,
    source_pin: &str,
    cli: CliEvidence,
    native_sdk_contracts_checked: bool,
) -> CreativeRuntimeDoctorReport {
    let source_validated = project.validate().is_ok();
    let checks = vec![
        CreativeDoctorCheck {
            id: "project_graph",
            status: if source_validated {
                "validated"
            } else {
                "blocked"
            },
            detail: "Current project schema and internal references were checked. No asset bytes were read.",
        },
        CreativeDoctorCheck {
            id: "native_sdk_contracts",
            status: if native_sdk_contracts_checked {
                "validated"
            } else {
                "not_tested"
            },
            detail: "First-party application operation contracts, not the external Broker, were validated.",
        },
        CreativeDoctorCheck {
            id: "semwright_cli_version",
            status: if cli == CliEvidence::VersionCompatible {
                "version_compatible"
            } else {
                "blocked"
            },
            detail: cli.reason(),
        },
        CreativeDoctorCheck {
            id: "broker_session_and_effect_grants",
            status: "not_tested",
            detail: "No Broker handshake, project policy grant or native effect has occurred in this doctor.",
        },
        CreativeDoctorCheck {
            id: "driver_host_renderer_discovery",
            status: "not_tested",
            detail: "A source-declared driver is not evidence that it is installed or operational on the owner machine.",
        },
        CreativeDoctorCheck {
            id: "asset_blob_integrity",
            status: "not_tested",
            detail: "Asset record digests are not proof that the local CAS contains the expected bytes; run a separate integrity scan.",
        },
        CreativeDoctorCheck {
            id: "installation_footprint_and_rollback",
            status: "not_tested",
            detail: "No download destination, package size, entitlement, rollback or installation was inspected or changed.",
        },
        CreativeDoctorCheck {
            id: "rendered_media_acceptance",
            status: "not_tested",
            detail: "A real native source readback, master file, frame/audio quality and independent human review remain required.",
        },
    ];
    let outputs = project
        .deliverables
        .iter()
        .map(|profile| CreativeDoctorOutput {
            profile_id: profile.id,
            width: profile.width,
            height: profile.height,
            estimated_uncompressed_rgba_frame_bytes: u64::from(profile.width)
                .saturating_mul(u64::from(profile.height))
                .saturating_mul(4),
            source_profile_validated: source_validated,
            render_and_delivery_verified: false,
        })
        .collect();
    CreativeRuntimeDoctorReport {
        schema: CREATIVE_DOCTOR_SCHEMA,
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        semwright_native_sdk_source_pin: source_pin.to_owned(),
        runtime_version_evidence: cli.status(),
        runtime_version_detail: cli.reason(),
        verdict: if !source_validated {
            "source_invalid"
        } else {
            match cli {
                CliEvidence::VersionCompatible if native_sdk_contracts_checked => "preflight_only",
                CliEvidence::BrowserDemo => "browser_only",
                _ => "owner_action_required",
            }
        },
        renderers: renderers(project),
        outputs,
        checks,
        ready_for_native_production: false,
        install_performed: false,
        broker_dispatch_performed: false,
        authorizing_grant_created: false,
    }
}

/// No read-only result may be described as belonging to another generation
/// or a revision changed while the CLI version-only check was in flight.
pub fn require_doctor_scope(
    project: &Project,
    expected_generation: Uuid,
    expected_revision: u64,
) -> Result<(), &'static str> {
    if project.generation != expected_generation || project.revision != expected_revision {
        Err("Project revision or generation changed during runtime diagnosis; refresh and recheck.")
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, ExtensionKind, ExtensionProfile};

    const PIN: &str = "54d3459a051adbce4cae9f60c0dea8f60c824be6";
    fn project() -> Project {
        let mut p = Project::new("Owner camera/render project").unwrap();
        p.apply_change(&Change::AddScene {
            name: "Title reveal".into(),
            objective: "Native scene edit".into(),
            duration_seconds: 6,
        })
        .unwrap();
        p
    }
    #[test]
    fn compatible_cli_version_never_claims_provider_readiness_or_installation() {
        let p = project();
        let original = p.clone();
        let r = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        assert_eq!(r.schema, CREATIVE_DOCTOR_SCHEMA);
        assert_eq!(r.verdict, "preflight_only");
        assert_eq!(r.runtime_version_evidence, "cli_version_compatible");
        assert!(!r.ready_for_native_production);
        assert!(!r.broker_dispatch_performed);
        assert!(!r.authorizing_grant_created);
        assert!(!r.install_performed);
        assert_eq!(
            r.checks.iter().filter(|x| x.status == "not_tested").count(),
            5
        );
        assert_eq!(p, original, "doctor must not change source revision");
    }
    #[test]
    fn unconfigured_and_unavailable_cli_fail_closed() {
        let p = project();
        for cli in [
            CliEvidence::Unconfigured,
            CliEvidence::Invalid,
            CliEvidence::ResourceMismatch,
            CliEvidence::Incompatible,
            CliEvidence::Unavailable,
        ] {
            let r = inspect_creative_runtime(&p, PIN, cli, true);
            assert_eq!(r.verdict, "owner_action_required");
            assert!(!r.ready_for_native_production);
            assert_ne!(r.checks[2].status, "version_compatible");
        }
    }
    #[test]
    fn browser_does_not_fabricate_runtime_or_native_contract_validation() {
        let p = project();
        let report = inspect_creative_runtime(&p, PIN, CliEvidence::BrowserDemo, false);
        assert_eq!(report.verdict, "browser_only");
        assert_eq!(report.checks[1].status, "not_tested");
        assert_eq!(report.runtime_version_evidence, "browser_demo");
        assert!(report.renderers.iter().all(|x| !x.provider_verified));
    }
    #[test]
    fn exact_generation_and_revision_are_a_strict_read_only_cas() {
        let p = project();
        require_doctor_scope(&p, p.generation, p.revision).unwrap();
        assert!(require_doctor_scope(&p, Uuid::new_v4(), p.revision).is_err());
        assert!(require_doctor_scope(&p, p.generation, p.revision + 1).is_err());
    }
    #[test]
    fn project_graph_and_sdk_contracts_must_be_separately_validated() {
        let mut p = project();
        let no_contracts = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, false);
        assert_eq!(no_contracts.verdict, "owner_action_required");
        p.scenes[0].duration.num = -1;
        let invalid = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        assert_eq!(invalid.verdict, "source_invalid");
        assert_eq!(invalid.checks[0].status, "blocked");
    }
    #[test]
    fn built_in_renderer_routes_are_declared_not_installation_evidence() {
        let mut p = project();
        p.scenes[0].renderer = RendererKind::Blender;
        p.apply_change(&Change::AddScene {
            name: "Timeline footage".into(),
            objective: "Native MLT edit".into(),
            duration_seconds: 5,
        })
        .unwrap();
        p.scenes[1].renderer = RendererKind::Mlt;
        let r = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        for (renderer, driver) in [("blender", "driver:blender"), ("mlt", "driver:mlt-video")] {
            let item = r.renderers.iter().find(|x| x.renderer == renderer).unwrap();
            assert_eq!(item.scene_count, 1);
            assert_eq!(item.required_semwright_driver, Some(driver));
            assert_eq!(item.state, "native_provider_not_tested");
            assert!(!item.provider_verified);
        }
    }

    #[test]
    fn optional_renderer_descriptor_is_not_an_installed_provider() {
        let mut p = project();
        p.scenes[0].renderer = RendererKind::Remotion;
        let absent = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        let target = absent
            .renderers
            .iter()
            .find(|x| x.renderer == "remotion")
            .unwrap();
        assert_eq!(target.state, "optional_extension_not_admitted");
        assert!(!target.declared_extension_admitted);
        p.extensions.push(ExtensionProfile {
            id: Uuid::new_v4(),
            name: "Owner-reviewed optional Remotion package".into(),
            kind: ExtensionKind::RemotionRenderer,
            package_version: "1.0.0".into(),
            digest_sha256: "a".repeat(64),
            license: "MIT".into(),
            source: "https://example.invalid/owner-review-package".into(),
            rights_status: RightsStatus::Cleared,
            enabled: true,
            permissions: vec!["read_project".into()],
        });
        let enabled = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        let target = enabled
            .renderers
            .iter()
            .find(|x| x.renderer == "remotion")
            .unwrap();
        assert_eq!(target.state, "optional_descriptor_only");
        assert!(target.declared_extension_admitted);
        assert!(!target.provider_verified);
    }
    #[test]
    fn unapproved_rights_never_mark_optional_profile_as_admitted() {
        let mut p = project();
        p.scenes[0].renderer = RendererKind::ManimGl;
        p.extensions.push(ExtensionProfile {
            id: Uuid::new_v4(),
            name: "Unknown ManimGL".into(),
            kind: ExtensionKind::ManimGlRenderer,
            package_version: "0.0.0".into(),
            digest_sha256: "b".repeat(64),
            license: "UNREVIEWED".into(),
            source: "https://example.invalid/unreviewed".into(),
            rights_status: RightsStatus::Unknown,
            enabled: false,
            permissions: vec![],
        });
        let r = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        let item = r
            .renderers
            .iter()
            .find(|x| x.renderer == "manim-gl")
            .unwrap();
        assert!(!item.declared_extension_admitted);
        assert_eq!(item.state, "optional_extension_not_admitted");
    }
    #[test]
    fn uncompressed_frame_bytes_are_not_mislabeled_installer_sizes() {
        let p = project();
        let r = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        let output = &r.outputs[0];
        assert_eq!(output.width, 1920);
        assert_eq!(output.height, 1080);
        assert_eq!(output.estimated_uncompressed_rgba_frame_bytes, 8_294_400);
        assert!(!output.render_and_delivery_verified);
        assert!(r.checks.iter().any(|x| {
            x.id == "installation_footprint_and_rollback" && x.status == "not_tested"
        }));
    }
    #[test]
    fn sanitized_serialization_exposes_no_paths_socket_ticket_or_tokens() {
        let p = project();
        let report = inspect_creative_runtime(&p, PIN, CliEvidence::VersionCompatible, true);
        let encoded = serde_json::to_string(&report).unwrap();
        assert!(encoded.contains(PIN));
        for secret in [
            "session_file",
            "socket",
            "connection_file",
            "effect_grant_token",
            "C:\\",
            "/home/",
        ] {
            assert!(
                !encoded.contains(secret),
                "unexpected private field: {secret}"
            );
        }
        assert!(!encoded.contains("ready_for_native_production\":true"));
    }
    #[test]
    fn unknown_native_probe_result_must_not_be_interpreted_as_ready() {
        let p = project();
        let status = CliEvidence::from_runtime_status("SUCCESS_BROKER_ONLINE");
        assert_eq!(status, CliEvidence::Unavailable);
        let report = inspect_creative_runtime(&p, PIN, status, true);
        assert_eq!(report.verdict, "owner_action_required");
    }
    #[test]
    fn stable_report_does_not_generate_jobs_or_change_project_revision() {
        let p = project();
        let a = serde_json::to_string(&inspect_creative_runtime(
            &p,
            PIN,
            CliEvidence::VersionCompatible,
            true,
        ))
        .unwrap();
        let b = serde_json::to_string(&inspect_creative_runtime(
            &p,
            PIN,
            CliEvidence::VersionCompatible,
            true,
        ))
        .unwrap();
        assert_eq!(a, b);
        assert!(a.contains("not_tested"));
        assert!(!a.contains("human_acceptance\":true"));
    }
}
