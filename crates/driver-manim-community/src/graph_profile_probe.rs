//! Optional fixed-profile observation on the existing doctor/Host route.
use super::*;
use motionwright_manim_profile::semantic_graph_profile_probe::{
    fixed_graph_profile_observation_schema_v1, parse_fixed_graph_profile_observation_v1,
};
use semwright_driver_sdk::ToolExecutionOutput;

pub(super) fn unobserved_graph_profile_probe(reason: &str) -> Value {
    json!({
        "schema":1, "operation":"fixed_graph_profile_probe", "requested":true,
        "source":"none", "state":"not_observed", "observation":null,
        "reason_code":reason, "render_performed":false,
        "scene_mutation_performed":false, "profile_admitted":false,
    })
}

fn validated_graph_profile_probe(stdout: &[u8]) -> Result<Value> {
    let observation =
        parse_fixed_graph_profile_observation_v1(stdout).map_err(map_profile_error)?;
    Ok(json!({
        "schema":1, "operation":"fixed_graph_profile_probe", "requested":true,
        "source":"host_native_fixed_graph_profile_probe", "state":"observed",
        "observation":observation, "reason_code":"fixed_profile_sample_observed",
        "render_performed":false, "scene_mutation_performed":false,
        "profile_admitted":false,
    }))
}

// The legacy version report keeps its existing handling of known failures.
// Preserve UNKNOWN before cancellation can replace it or a second probe starts.
pub(super) fn ensure_probe_outcome_known(output: &Result<ToolExecutionOutput>) -> Result<()> {
    match output {
        Err(error) if !error.outcome_known => Err(error.clone()),
        _ => Ok(()),
    }
}

fn graph_profile_probe_report(output: Result<ToolExecutionOutput>) -> Result<Value> {
    // Preserve every original Host error, including its uncertainty/progress.
    let output = output?;
    Ok(if output.exit_code == 0 {
        validated_graph_profile_probe(&output.stdout)
            .unwrap_or_else(|_| unobserved_graph_profile_probe("invalid_runner_response"))
    } else {
        unobserved_graph_profile_probe("runner_failed")
    })
}

pub(super) fn graph_profile_probe_schema() -> Value {
    json!({
        "type":"object", "additionalProperties":false,
        "properties":{
            "schema":{"const":1}, "operation":{"const":"fixed_graph_profile_probe"},
            "requested":{"const":true},
            "source":{"enum":["none","host_native_fixed_graph_profile_probe"]},
            "state":{"enum":["not_observed","observed"]},
            "observation":{"oneOf":[{"type":"null"}, fixed_graph_profile_observation_schema_v1()]},
            "reason_code":{"enum":["host_context_unavailable","host_tools_unavailable","runner_failed","invalid_runner_response","fixed_profile_sample_observed"]},
            "render_performed":{"const":false}, "scene_mutation_performed":{"const":false},
            "profile_admitted":{"const":false},
        },
        "required":["schema","operation","requested","source","state","observation","reason_code","render_performed","scene_mutation_performed","profile_admitted"],
    })
}

pub(super) fn runtime_probe_tool_args(operation: &str) -> Vec<RuntimeToolArg> {
    let literal = |value: &str| RuntimeToolArg::Literal {
        value: value.into(),
    };
    vec![
        literal(operation),
        literal("--runtime-root"),
        RuntimeToolArg::MountPath {
            mount: RUNTIME_MOUNT.into(),
            relative: String::new(),
        },
        literal("--python-sealed"),
        RuntimeToolArg::ToolPath {
            tool: PYTHON_TOOL.into(),
        },
        literal("--ffmpeg-sealed"),
        RuntimeToolArg::ToolPath {
            tool: FFMPEG_TOOL.into(),
        },
        literal("--fontconfig-root"),
        RuntimeToolArg::MountPath {
            mount: FONTCONFIG_MOUNT.into(),
            relative: String::new(),
        },
        literal("--work-root"),
        RuntimeToolArg::MountPath {
            mount: WORK_MOUNT.into(),
            relative: String::new(),
        },
    ]
}

impl ManimCommunityDriver {
    pub(super) async fn doctor_with_graph_profile(
        &self,
        requested: bool,
        mut report: Value,
        context: &DriverExecutionContext,
    ) -> Result<Value> {
        if !requested {
            return Ok(report);
        }
        if std::env::var_os("SEMWRIGHT_DRIVER_HOST_TOOLS").is_none() {
            report["graph_profile_probe"] =
                unobserved_graph_profile_probe("host_tools_unavailable");
            return Ok(report);
        }
        let output = context
            .execute_runtime_tool_args(
                MANIM_TOOL,
                runtime_probe_tool_args("probe-graph-profile"),
                Vec::new(),
                Duration::from_secs(30),
                Some(RuntimeToolCwd {
                    mount: WORK_MOUNT.into(),
                    relative: String::new(),
                }),
            )
            .await;
        let observation = graph_profile_probe_report(output)?;
        context.check_cancelled()?;
        report["graph_profile_probe"] = observation;
        Ok(report)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn graph_profile_opt_in_preserves_legacy_report_and_creates_no_job_without_context() {
        let temp = tempfile::tempdir().unwrap();
        let mut driver = ManimCommunityDriver {
            work_root: temp.path().to_owned(),
            output_root: temp.path().to_owned(),
            jobs: BTreeMap::new(),
            capabilities: capability_catalog().unwrap(),
        };
        let capability = driver
            .capabilities
            .iter()
            .find(|cap| cap.descriptor.name == "driver.manim-community.doctor")
            .unwrap();
        let digest = descriptor_digest(&capability.descriptor).unwrap();
        let mut reports = Vec::new();
        for request in [
            json!({}),
            json!({"probe_graph_profile":false}),
            json!({"probe_graph_profile":true}),
            json!({"probe_runtime":true}),
            json!({"probe_runtime":true,"probe_graph_profile":true}),
        ] {
            reports.push(
                driver
                    .execute("driver.manim-community.doctor", &digest, request)
                    .await
                    .unwrap(),
            );
        }
        for report in &reports {
            assert_eq!(report["runtime_probe"].as_object().unwrap().len(), 11);
        }
        for index in [0, 1, 3] {
            assert!(reports[index].get("graph_profile_probe").is_none());
        }
        assert_eq!(reports[0]["runtime_probe"], reports[1]["runtime_probe"]);
        assert_eq!(reports[0]["runtime_probe"], reports[2]["runtime_probe"]);
        assert_eq!(reports[3]["runtime_probe"], reports[4]["runtime_probe"]);
        for index in [2, 4] {
            let probe = &reports[index]["graph_profile_probe"];
            assert_eq!(probe["state"], "not_observed");
            assert_eq!(probe["reason_code"], "host_context_unavailable");
            assert_eq!(probe["source"], "none");
            assert_eq!(probe["observation"], Value::Null);
            assert_eq!(probe["profile_admitted"], false);
        }
        assert!(driver.jobs.is_empty());
        assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 0);
    }

    #[test]
    fn graph_profile_extension_keeps_runtime_dto_closed_and_host_unknown_unchanged() {
        let default: DoctorArgs = serde_json::from_value(json!({})).unwrap();
        assert!(!default.probe_runtime && !default.probe_graph_profile);
        let selected: DoctorArgs =
            serde_json::from_value(json!({"probe_graph_profile":true})).unwrap();
        assert!(!selected.probe_runtime && selected.probe_graph_profile);
        for request in [
            json!({"probe_graph_profile":"true"}),
            json!({"probe_graph_profile":true,"text":"user text"}),
            json!({"probe_graph_profile":true,"profile_admitted":true}),
        ] {
            assert!(serde_json::from_value::<DoctorArgs>(request).is_err());
        }
        let schema = doctor_output_schema();
        let runtime_schema = &schema["properties"]["runtime_probe"];
        assert_eq!(runtime_schema["properties"].as_object().unwrap().len(), 11);
        assert_eq!(runtime_schema["required"].as_array().unwrap().len(), 11);
        assert_eq!(runtime_schema["additionalProperties"], false);
        assert!(
            !schema["required"]
                .as_array()
                .unwrap()
                .contains(&json!("graph_profile_probe"))
        );

        let mut runtime = json!({
            "schema":1,"operation":"runtime_version_probe","requested":true,
            "source":"host_native_package_probe","state":"observed",
            "expected_version":MANIM_VERSION,"observed_version":MANIM_VERSION,
            "compatible":true,"reason_code":"version_observed",
            "render_performed":false,"scene_mutation_performed":false,
        });
        assert_eq!(
            validated_runtime_probe(&serde_json::to_vec(&runtime).unwrap()).unwrap(),
            runtime
        );
        runtime["graph_profile_probe"] = unobserved_graph_profile_probe("host_context_unavailable");
        assert!(validated_runtime_probe(&serde_json::to_vec(&runtime).unwrap()).is_err());

        let error = Error::new(ErrorCode::Timeout, "fixture Host outcome remains unknown")
            .uncertain()
            .recipe_progress(0, "fixed-graph-profile-probe");
        let expected = serde_json::to_value(&error).unwrap();
        let unknown = Err::<ToolExecutionOutput, _>(error);
        let legacy_error = ensure_probe_outcome_known(&unknown).unwrap_err();
        assert_eq!(serde_json::to_value(legacy_error).unwrap(), expected);
        let graph_error = graph_profile_probe_report(unknown).unwrap_err();
        assert_eq!(serde_json::to_value(graph_error).unwrap(), expected);
    }
}
