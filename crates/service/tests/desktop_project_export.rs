// Compile the actual desktop boundaries against the real service and SQLite.
// These fixture grants do not claim operator consent or installed Broker authority.
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
pub mod effect_grants;
#[path = "../../../apps/desktop/src-tauri/src/local_error.rs"]
pub mod local_error;
#[path = "../../../apps/desktop/src-tauri/src/project_export.rs"]
pub mod project_export;
#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
pub mod project_library;
