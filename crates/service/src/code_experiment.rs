//! Disposable, capability-free Wasm measurement experiments.
//!
//! This service primitive neither installs code nor admits a producer or verifier.
//! A Desktop caller must prepare/run on its retained worker and obtain a fresh
//! owner decision bound to descriptor.plan_id before consuming the prepared plan.
use crate::StudioService;
use motionwright_domain::RevisionStamp;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
use uuid::Uuid;
use wasmi::{
    Config, EnforcedLimits, Engine, Linker, Module, Store, StoreLimits, StoreLimitsBuilder,
};

pub const CODE_SOURCE_BYTES: usize = 16 * 1024;
pub const CODE_FIXTURES: usize = 3;
pub const CODE_FUEL: u64 = 100_000;
pub const CODE_MEMORY_BYTES: usize = 1024 * 1024;
const PLAN_TTL: Duration = Duration::from_secs(15 * 60);
static ACTIVE_PREPARATIONS: AtomicUsize = AtomicUsize::new(0);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CodeExperimentSource {
    pub method: String,
    pub version: u32,
    pub units: String,
    pub coverage: String,
    pub limitations: Vec<String>,
    pub wat: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CodeExperimentRequest {
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub scene_ids: Vec<Uuid>,
    pub source: CodeExperimentSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CodeExperimentFailure {
    InvalidSource,
    UnsupportedImports,
    IncompatibleAbi,
    FixtureScope,
    Busy,
    Expired,
    StaleProject,
    ProjectUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CodeExperimentFixture {
    pub scene_id: Uuid,
    pub node_count: i64,
    pub visible_node_count: i64,
    pub keyframe_count: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct CodeExperimentDescriptor {
    pub plan_id: Uuid,
    pub project_id: Uuid,
    pub expected: RevisionStamp,
    pub source_sha256: String,
    pub method: String,
    pub method_version: u32,
    pub units: String,
    pub coverage: String,
    pub limitations: Vec<String>,
    pub runtime: &'static str,
    pub permissions: [&'static str; 1],
    pub source_bytes: usize,
    pub memory_bytes: usize,
    pub fuel_per_fixture: u64,
    pub expires_in_seconds: u64,
    pub fixtures: Vec<CodeExperimentFixture>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CodeExperimentObservation {
    pub scene_id: Uuid,
    /// Exact signed integer as decimal text: no JavaScript precision loss.
    pub value: Option<String>,
    pub stopped: bool,
    pub fuel_used: u64,
}

#[derive(Debug, Serialize)]
pub struct CodeExperimentReport {
    pub descriptor: CodeExperimentDescriptor,
    pub observations: Vec<CodeExperimentObservation>,
    pub installed: bool,
    pub gate_eligible: bool,
}

struct PreparationPermit;
impl PreparationPermit {
    fn acquire() -> Result<Self, CodeExperimentFailure> {
        ACTIVE_PREPARATIONS
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                (count < 2).then_some(count + 1)
            })
            .map(|_| Self)
            .map_err(|_| CodeExperimentFailure::Busy)
    }
}
impl Drop for PreparationPermit {
    fn drop(&mut self) {
        ACTIVE_PREPARATIONS.fetch_sub(1, Ordering::AcqRel);
    }
}

/// An original retained preparation. Intentionally neither Clone nor Deserialize.
/// Consuming it is mandatory; dropping it releases its resource reservation.
pub struct PreparedCodeExperiment {
    origin: StudioService,
    descriptor: CodeExperimentDescriptor,
    engine: Engine,
    module: Module,
    deadline: Instant,
    _permit: PreparationPermit,
}
impl PreparedCodeExperiment {
    pub fn descriptor(&self) -> &CodeExperimentDescriptor {
        &self.descriptor
    }
}

fn bounded(value: &str, limit: usize) -> bool {
    !value.trim().is_empty() && value.len() <= limit && !value.chars().any(char::is_control)
}

/// Bound the textual parser's nesting independently of Wasm execution fuel.
/// Strings and both comment forms are tracked; quoted/commented parentheses
/// cannot hide deeply nested source. Only the declared ASCII WAT profile enters.
fn text_boundary(source: &str) -> Result<(), CodeExperimentFailure> {
    let invalid = CodeExperimentFailure::InvalidSource;
    if source.is_empty()
        || source.len() > CODE_SOURCE_BYTES
        || !source.trim_start().starts_with("(module")
        || !source
            .bytes()
            .all(|b| b.is_ascii_graphic() || b" \t\r\n".contains(&b))
    {
        return Err(invalid);
    }
    let bytes = source.as_bytes();
    let (mut at, mut depth, mut comment_depth) = (0, 0_usize, 0_usize);
    let mut quoted = false;
    while at < bytes.len() {
        let pair = bytes.get(at..at + 2);
        if comment_depth > 0 {
            if pair == Some(b"(;") {
                comment_depth += 1;
                if comment_depth > 16 {
                    return Err(invalid);
                }
                at += 2;
            } else if pair == Some(b";)") {
                comment_depth -= 1;
                at += 2;
            } else {
                at += 1;
            }
        } else if quoted {
            match bytes[at] {
                b'\\' => {
                    at += 2;
                }
                b'"' => {
                    quoted = false;
                    at += 1;
                }
                _ => {
                    at += 1;
                }
            }
        } else if pair == Some(b";;") {
            while at < bytes.len() && bytes[at] != b'\n' {
                at += 1;
            }
        } else if pair == Some(b"(;") {
            comment_depth = 1;
            at += 2;
        } else {
            match bytes[at] {
                b'"' => quoted = true,
                b'(' => {
                    depth += 1;
                    if depth > 64 {
                        return Err(invalid);
                    }
                }
                b')' => {
                    depth = depth.checked_sub(1).ok_or(invalid)?;
                }
                _ => {}
            }
            at += 1;
        }
    }
    if quoted || comment_depth != 0 || depth != 0 {
        return Err(invalid);
    }
    Ok(())
}

fn new_store(engine: &Engine) -> Store<StoreLimits> {
    let limits = StoreLimitsBuilder::new()
        .memory_size(CODE_MEMORY_BYTES)
        .memories(1)
        .tables(0)
        .table_elements(0)
        .instances(1)
        .trap_on_grow_failure(true)
        .build();
    let mut store = Store::new(engine, limits);
    store.limiter(|limits| limits);
    store
}

fn compile(source: &CodeExperimentSource) -> Result<(Engine, Module), CodeExperimentFailure> {
    if source.version == 0
        || !bounded(&source.method, 100)
        || !bounded(&source.units, 40)
        || !bounded(&source.coverage, 300)
        || source.limitations.is_empty()
        || source.limitations.len() > 8
        || source.limitations.iter().any(|line| !bounded(line, 300))
    {
        return Err(CodeExperimentFailure::InvalidSource);
    }
    text_boundary(&source.wat)?;
    let mut config = Config::default();
    config
        .consume_fuel(true)
        .allow_start_fn(false)
        .floats(false)
        .wasm_multi_memory(false)
        .set_min_stack_height(1024)
        .set_max_stack_height(64 * 1024)
        .set_max_recursion_depth(64)
        .set_max_cached_stacks(0)
        .enforced_limits(EnforcedLimits::strict());
    let engine = Engine::new(&config);
    let module = Module::new(&engine, source.wat.as_bytes())
        .map_err(|_| CodeExperimentFailure::InvalidSource)?;
    if module.imports().next().is_some() {
        return Err(CodeExperimentFailure::UnsupportedImports);
    }
    if module
        .exports()
        .any(|export| export.name() != "measure" && export.name() != "memory")
    {
        return Err(CodeExperimentFailure::IncompatibleAbi);
    }
    // No start function is permitted, and no host function, WASI module, file,
    // network capability, executable, clock or entropy source is linked.
    let mut probe = new_store(&engine);
    let instance = Linker::<StoreLimits>::new(&engine)
        .instantiate_and_start(&mut probe, &module)
        .map_err(|_| CodeExperimentFailure::IncompatibleAbi)?;
    instance
        .get_typed_func::<(i64, i64, i64), i64>(&probe, "measure")
        .map_err(|_| CodeExperimentFailure::IncompatibleAbi)?;
    Ok((engine, module))
}

impl StudioService {
    /// Compile exact original WAT and capture up to three immutable scene-count
    /// fixtures from the real Store. This does not execute the exported function.
    pub fn prepare_code_experiment(
        &self,
        request: CodeExperimentRequest,
    ) -> Result<PreparedCodeExperiment, CodeExperimentFailure> {
        let permit = PreparationPermit::acquire()?;
        if request.scene_ids.is_empty() || request.scene_ids.len() > CODE_FIXTURES {
            return Err(CodeExperimentFailure::FixtureScope);
        }
        let project = self
            .project(request.project_id)
            .map_err(|_| CodeExperimentFailure::ProjectUnavailable)?;
        if project.stamp() != request.expected {
            return Err(CodeExperimentFailure::StaleProject);
        }
        let mut fixtures = Vec::with_capacity(request.scene_ids.len());
        for id in &request.scene_ids {
            if fixtures
                .iter()
                .any(|value: &CodeExperimentFixture| value.scene_id == *id)
            {
                return Err(CodeExperimentFailure::FixtureScope);
            }
            let scene = project
                .scenes
                .iter()
                .find(|scene| scene.id == *id)
                .ok_or(CodeExperimentFailure::FixtureScope)?;
            let integer = |value: usize| {
                i64::try_from(value).map_err(|_| CodeExperimentFailure::FixtureScope)
            };
            fixtures.push(CodeExperimentFixture {
                scene_id: *id,
                node_count: integer(scene.nodes.len())?,
                visible_node_count: integer(
                    scene.nodes.iter().filter(|node| node.opacity > 0.0).count(),
                )?,
                keyframe_count: integer(scene.nodes.iter().map(|node| node.keyframes.len()).sum())?,
            });
        }
        let (engine, module) = compile(&request.source)?;
        let source_bytes = serde_json::to_vec(&request.source)
            .map_err(|_| CodeExperimentFailure::InvalidSource)?;
        let mut digest = Sha256::new();
        digest.update(b"motionwright.code-experiment/1\0");
        digest.update(&source_bytes);
        let descriptor = CodeExperimentDescriptor {
            plan_id: Uuid::now_v7(),
            project_id: project.id,
            expected: project.stamp(),
            source_sha256: hex::encode(digest.finalize()),
            method: request.source.method,
            method_version: request.source.version,
            units: request.source.units,
            coverage: request.source.coverage,
            limitations: request.source.limitations,
            runtime: "wasmi-2.0.3/motionwright-scalar/1",
            permissions: ["read_selected_scene_counts"],
            source_bytes: request.source.wat.len(),
            memory_bytes: CODE_MEMORY_BYTES,
            fuel_per_fixture: CODE_FUEL,
            expires_in_seconds: PLAN_TTL.as_secs(),
            fixtures,
        };
        Ok(PreparedCodeExperiment {
            descriptor,
            engine,
            module,
            deadline: Instant::now() + PLAN_TTL,
            _permit: permit,
            origin: self.clone(),
        })
    }

    /// Consume one retained preparation. The caller's worker must retain this
    /// value through cancellation; execution cannot install or mutate a project.
    pub fn run_code_experiment(
        &self,
        prepared: PreparedCodeExperiment,
    ) -> Result<CodeExperimentReport, CodeExperimentFailure> {
        if Instant::now() >= prepared.deadline {
            return Err(CodeExperimentFailure::Expired);
        }
        if !Arc::ptr_eq(&self.store, &prepared.origin.store) {
            return Err(CodeExperimentFailure::StaleProject);
        }
        let check = || {
            let project = self
                .project(prepared.descriptor.project_id)
                .map_err(|_| CodeExperimentFailure::ProjectUnavailable)?;
            if project.stamp() != prepared.descriptor.expected {
                return Err(CodeExperimentFailure::StaleProject);
            }
            Ok(())
        };
        check()?;
        let mut observations = Vec::with_capacity(prepared.descriptor.fixtures.len());
        for fixture in &prepared.descriptor.fixtures {
            let mut store = new_store(&prepared.engine);
            store
                .set_fuel(CODE_FUEL)
                .map_err(|_| CodeExperimentFailure::InvalidSource)?;
            let value = Linker::<StoreLimits>::new(&prepared.engine)
                .instantiate_and_start(&mut store, &prepared.module)
                .and_then(|instance| {
                    instance.get_typed_func::<(i64, i64, i64), i64>(&store, "measure")
                })
                .and_then(|function| {
                    function.call(
                        &mut store,
                        (
                            fixture.node_count,
                            fixture.visible_node_count,
                            fixture.keyframe_count,
                        ),
                    )
                });
            let remaining = store.get_fuel().unwrap_or(0);
            observations.push(CodeExperimentObservation {
                scene_id: fixture.scene_id,
                stopped: value.is_err(),
                value: value.ok().map(|value| value.to_string()),
                fuel_used: CODE_FUEL.saturating_sub(remaining),
            });
        }
        check()?;
        Ok(CodeExperimentReport {
            descriptor: prepared.descriptor,
            observations,
            installed: false,
            gate_eligible: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{Change, Project};
    use std::sync::Mutex;

    static SERIAL: Mutex<()> = Mutex::new(());
    const COUNTS: &str = r#"(module
      (func (export "measure") (param i64 i64 i64) (result i64)
        local.get 0 i64.const 100 i64.mul
        local.get 1 i64.const 10 i64.mul i64.add
        local.get 2 i64.add))"#;

    fn fixture() -> (tempfile::TempDir, StudioService, Project) {
        let temporary = tempfile::tempdir().unwrap();
        let service = StudioService::open(temporary.path().join("original.sqlite3")).unwrap();
        let mut project = service.create_project("Original code experiment").unwrap();
        for index in 0..3 {
            project = service
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("scene-{index}"),
                    &Change::AddScene {
                        name: format!("Original {index}"),
                        objective: "Owned typed source counts".into(),
                        duration_seconds: 6,
                    },
                )
                .unwrap()
                .project;
        }
        (temporary, service, project)
    }
    fn request(project: &Project, wat: &str) -> CodeExperimentRequest {
        CodeExperimentRequest {
            project_id: project.id,
            expected: project.stamp(),
            scene_ids: project.scenes.iter().map(|scene| scene.id).collect(),
            source: CodeExperimentSource {
                method: "Original count calculation".into(),
                version: 1,
                units: "weighted authored count".into(),
                coverage: "Selected source nodes, visible nodes and saved keyframes".into(),
                limitations: vec!["No rendered pixels or creative quality are measured".into()],
                wat: wat.into(),
            },
        }
    }

    #[test]
    fn actual_wat_uses_original_sqlite_fixtures_without_installation_or_mutation() {
        let _serial = SERIAL.lock().unwrap();
        let (temporary, service, project) = fixture();
        let prepared = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        let descriptor = prepared.descriptor().clone();
        assert_eq!(descriptor.permissions, ["read_selected_scene_counts"]);
        let report = service.run_code_experiment(prepared).unwrap();
        assert_eq!(report.observations.len(), 3);
        for (scene, observation) in project.scenes.iter().zip(&report.observations) {
            let expected = scene.nodes.len() * 100
                + scene.nodes.iter().filter(|node| node.opacity > 0.0).count() * 10
                + scene
                    .nodes
                    .iter()
                    .map(|node| node.keyframes.len())
                    .sum::<usize>();
            assert_eq!(observation.scene_id, scene.id);
            assert_eq!(observation.value, Some(expected.to_string()));
            assert!(!observation.stopped);
            assert!(observation.fuel_used > 0 && observation.fuel_used <= CODE_FUEL);
        }
        assert!(!report.installed);
        assert!(!report.gate_eligible);
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(service);
        let reopened = StudioService::open(temporary.path().join("original.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[test]
    fn host_imports_start_functions_and_wrong_abi_are_rejected_before_execution() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let imported = r#"(module
          (import "env" "read_file" (func $host (result i64)))
          (func (export "measure") (param i64 i64 i64) (result i64) call $host))"#;
        assert_eq!(
            service
                .prepare_code_experiment(request(&project, imported))
                .err(),
            Some(CodeExperimentFailure::UnsupportedImports)
        );
        for source in [
            r#"(module (func $start) (start $start)
              (func (export "measure") (param i64 i64 i64) (result i64) i64.const 1))"#,
            r#"(module (func (export "measure") (result i64) i64.const 1))"#,
            r#"(module (func (export "unexpected"))
              (func (export "measure") (param i64 i64 i64) (result i64) i64.const 1))"#,
            r#"(module (table 1 funcref)
              (func (export "measure") (param i64 i64 i64) (result i64) i64.const 1))"#,
        ] {
            assert!(
                service
                    .prepare_code_experiment(request(&project, source))
                    .is_err()
            );
        }
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[test]
    fn an_actual_infinite_wasm_loop_stops_with_its_original_fuel_budget() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let source = r#"(module (func (export "measure") (param i64 i64 i64) (result i64)
          (loop $forever (br $forever)) i64.const 0))"#;
        let prepared = service
            .prepare_code_experiment(request(&project, source))
            .unwrap();
        let result = service.run_code_experiment(prepared).unwrap();
        assert!(result.observations.iter().all(|o| o.stopped
            && o.value.is_none()
            && o.fuel_used > 0
            && o.fuel_used <= CODE_FUEL));
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[test]
    fn oversized_initial_memory_growth_and_recursion_are_bounded() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let oversized = r#"(module (memory 17)
          (func (export "measure") (param i64 i64 i64) (result i64) i64.const 1))"#;
        assert!(
            service
                .prepare_code_experiment(request(&project, oversized))
                .is_err()
        );
        for source in [
            r#"(module (memory 1 32)
              (func (export "measure") (param i64 i64 i64) (result i64)
                i32.const 16 memory.grow drop i64.const 1))"#,
            r#"(module
              (func $recursive (export "measure") (param i64 i64 i64) (result i64)
                local.get 0 local.get 1 local.get 2 call $recursive))"#,
        ] {
            let prepared = service
                .prepare_code_experiment(request(&project, source))
                .unwrap();
            let result = service.run_code_experiment(prepared).unwrap();
            assert!(
                result
                    .observations
                    .iter()
                    .all(|o| o.stopped && o.value.is_none())
            );
        }
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[test]
    fn textual_source_budgets_track_quoted_and_commented_parentheses() {
        let _serial = SERIAL.lock().unwrap();
        assert!(text_boundary(&format!("(module ;;{}\n)", "x".repeat(CODE_SOURCE_BYTES))).is_err());
        let deep = format!(
            "(module (; {} ;) \"{}\" {} {})",
            ")".repeat(100),
            ")".repeat(100),
            "(".repeat(65),
            ")".repeat(65)
        );
        assert_eq!(
            text_boundary(&deep),
            Err(CodeExperimentFailure::InvalidSource)
        );
        assert!(text_boundary("(module (; one (; nested ;) ;) ;; line\n)").is_ok());
        assert!(text_boundary("(module \"unfinished\\").is_err());
        assert!(text_boundary("(module (; unterminated)").is_err());
        assert!(text_boundary("globalThis.fetch('https://example.invalid')").is_err());
        let (_temporary, service, project) = fixture();
        let mut invalid = request(&project, COUNTS);
        invalid.source.limitations.clear();
        assert_eq!(
            service.prepare_code_experiment(invalid).err(),
            Some(CodeExperimentFailure::InvalidSource)
        );
    }

    #[test]
    fn exact_store_revision_and_expiry_are_required_before_consuming_a_plan() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let (_other_temporary, other, _other_project) = fixture();
        let prepared = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        assert_eq!(
            other.run_code_experiment(prepared).err(),
            Some(CodeExperimentFailure::StaleProject)
        );
        let mut expired = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        expired.deadline = Instant::now();
        assert_eq!(
            service.run_code_experiment(expired).err(),
            Some(CodeExperimentFailure::Expired)
        );
        let prepared = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        let changed = service
            .apply(
                project.id,
                &project.stamp(),
                "actual-change",
                &Change::AddScene {
                    name: "Later scene".into(),
                    objective: "Invalidate an earlier original review".into(),
                    duration_seconds: 6,
                },
            )
            .unwrap()
            .project;
        assert_eq!(
            service.run_code_experiment(prepared).err(),
            Some(CodeExperimentFailure::StaleProject)
        );
        assert_eq!(service.project(project.id).unwrap(), changed);
    }

    #[test]
    fn two_original_preparations_keep_their_capacity_until_drop_or_completion() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let first = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        let second = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        assert_eq!(
            service
                .prepare_code_experiment(request(&project, COUNTS))
                .err(),
            Some(CodeExperimentFailure::Busy)
        );
        drop(first);
        let third = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        service.run_code_experiment(second).unwrap();
        let fourth = service
            .prepare_code_experiment(request(&project, COUNTS))
            .unwrap();
        drop(third);
        drop(fourth);
        assert_eq!(ACTIVE_PREPARATIONS.load(Ordering::Acquire), 0);
    }

    #[test]
    fn each_fixture_gets_fresh_guest_state_and_exact_integer_output() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let source = r#"(module (global $count (mut i64) (i64.const 9007199254740992))
          (func (export "measure") (param i64 i64 i64) (result i64)
            global.get $count i64.const 1 i64.add global.set $count global.get $count))"#;
        let prepared = service
            .prepare_code_experiment(request(&project, source))
            .unwrap();
        let result = service.run_code_experiment(prepared).unwrap();
        assert!(
            result
                .observations
                .iter()
                .all(|o| o.value.as_deref() == Some("9007199254740993"))
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[test]
    fn foreign_duplicate_and_missing_fixtures_preserve_the_original_project() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        for ids in [
            vec![],
            vec![Uuid::now_v7()],
            vec![project.scenes[0].id; 2],
            vec![project.scenes[0].id; 4],
        ] {
            let mut invalid = request(&project, COUNTS);
            invalid.scene_ids = ids;
            assert_eq!(
                service.prepare_code_experiment(invalid).err(),
                Some(CodeExperimentFailure::FixtureScope)
            );
        }
        let mut stale = request(&project, COUNTS);
        stale.expected.generation = Uuid::now_v7();
        assert_eq!(
            service.prepare_code_experiment(stale).err(),
            Some(CodeExperimentFailure::StaleProject)
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[test]
    fn producer_declarations_are_closed_and_bound_to_the_original_source() {
        let _serial = SERIAL.lock().unwrap();
        let (_temporary, service, project) = fixture();
        let source = request(&project, COUNTS);
        let mut forged = serde_json::to_value(&source).unwrap();
        forged["source"]["permissions"] = serde_json::json!(["network"]);
        assert!(serde_json::from_value::<CodeExperimentRequest>(forged).is_err());
        let original = service.prepare_code_experiment(source.clone()).unwrap();
        let original_digest = original.descriptor().source_sha256.clone();
        drop(original);
        let mut changed = source.clone();
        changed.source.units = "different declared units".into();
        let altered = service.prepare_code_experiment(changed).unwrap();
        assert_ne!(original_digest, altered.descriptor().source_sha256);
        drop(altered);
        let again = service.prepare_code_experiment(source).unwrap();
        assert_eq!(original_digest, again.descriptor().source_sha256);
    }
}
