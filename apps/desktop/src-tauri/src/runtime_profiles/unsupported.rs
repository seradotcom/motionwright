use super::*;
use crate::effect_grants::EffectGrantRegistry;
use motionwright_service::StudioService;
use std::path::Path;
const UNSUPPORTED: &str = "No offline native runtime package is admitted for this operating system and architecture. Continue local authoring or select an existing supported Semwright connection.";
#[derive(Clone, Default)]
pub struct RuntimeProfiles;
impl RuntimeProfiles {
    pub fn supported(&self) -> bool {
        false
    }
    pub fn new(_data_dir: &Path) -> Self {
        Self
    }
    pub fn inventory(
        &self,
        service: &StudioService,
        scope: Scope,
        _pin: &str,
    ) -> Result<Inventory, String> {
        let project = service.project(scope.project_id).map_err(|_| UNSUPPORTED)?;
        if project.generation != scope.generation || project.revision != scope.revision {
            return Err("Project changed before runtime inspection.".into());
        }
        Ok(Inventory {
            schema: "motionwright.runtime-profile-inventory/1",
            scope,
            supported: false,
            platform: "unsupported",
            profiles: vec![],
            alternatives: ALTERNATIVES,
        })
    }
    pub fn inspect(
        &self,
        _service: &StudioService,
        _scope: Scope,
        _selected: &Path,
        _pin: &str,
    ) -> Result<PlanView, String> {
        Err(UNSUPPORTED.into())
    }
    pub fn install(
        &self,
        _service: &StudioService,
        _grants: &EffectGrantRegistry,
        request: InstallRequest,
        _pin: &str,
    ) -> Result<InstallView, RuntimeFailure> {
        let mut failure = RuntimeFailure::install(&request, UNSUPPORTED);
        failure.certainty = "not_started";
        Err(failure)
    }
    pub fn read(
        &self,
        _service: &StudioService,
        _request: ReadRequest,
        _pin: &str,
    ) -> Result<InstallView, String> {
        Err(UNSUPPORTED.into())
    }
    pub fn rollback(
        &self,
        _service: &StudioService,
        _grants: &EffectGrantRegistry,
        request: RollbackRequest,
        _pin: &str,
    ) -> Result<InstallView, RuntimeFailure> {
        let mut failure = RuntimeFailure::rollback(&request, UNSUPPORTED);
        failure.certainty = "not_started";
        Err(failure)
    }
}
