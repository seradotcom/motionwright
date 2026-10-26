//! An owner-selected local staging capability is separate from Host MediaRO.
//! The saved declaration is configuration, never a serialized live capability.
use super::RuntimeConnectionRegistry;
use motionwright_native::production::ProductionConnection;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use semwright_platform_api::filesystem::{
    Confinement, RetainedStageResult, ScopedFilesystem, ScopedRoot,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::{Component, Path, PathBuf},
};
use uuid::Uuid;

pub(super) const REGISTRY_WITH_FONT_STAGING_SCHEMA: u32 = 2;
const DECLARATION_SCHEMA: &str = "motionwright.owner-font-staging/1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SavedFontStaging {
    schema: String,
    declaration_id: Uuid,
    connection_identity: String,
    media_root: PathBuf,
    root_instance_identity: String,
}

impl SavedFontStaging {
    pub(super) fn has_supported_schema(&self) -> bool {
        self.schema == DECLARATION_SCHEMA
            && !self.declaration_id.is_nil()
            && self.connection_identity.len() == 64
            && self
                .connection_identity
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            && validate_root_path(&self.media_root).is_ok()
            && valid_root_identity(&self.root_instance_identity)
    }
}

/// Keep the selected root alive in this registry session when available.
/// After reopening, the persisted declaration remains owner policy; a fresh
/// native open must match the full stored instance evidence on its own handle.
/// Neither the evidence string nor this local root supplies a Host mount grant.
pub(super) struct LiveFontStaging {
    declaration: SavedFontStaging,
    root: Box<dyn ScopedRoot>,
}

impl std::fmt::Debug for LiveFontStaging {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LiveFontStaging")
            .field("declaration_id", &self.declaration.declaration_id)
            .finish_non_exhaustive()
    }
}

pub(super) type LiveFontStagingState = BTreeMap<Uuid, LiveFontStaging>;

/// Attenuate the owner policy to newly created files and their retained custody.
/// This wraps the SAME native root; it does not reopen a pathname. Provider
/// reads remain separate, and replacement/atomic-publication fallbacks refuse.
struct OwnerFontStagingRoot {
    inner: Box<dyn ScopedRoot>,
}

impl ScopedRoot for OwnerFontStagingRoot {
    fn root_instance_identity(&self) -> NativeResult<String> {
        self.inner.root_instance_identity()
    }

    fn confinement(&self) -> Confinement {
        self.inner.confinement()
    }

    fn read(&self, _path: &Path, _limit: usize) -> NativeResult<Vec<u8>> {
        Err(Error::new(
            ErrorCode::PolicyDenied,
            "Owner font staging does not authorize reading existing files",
        ))
    }

    fn write_atomic(&self, _path: &Path, _bytes: &[u8]) -> NativeResult<()> {
        Err(Error::new(
            ErrorCode::PolicyDenied,
            "Owner font staging does not authorize replacing existing files",
        ))
    }

    fn stage_new_retained(&self, path: &Path, bytes: &[u8]) -> RetainedStageResult {
        self.inner.stage_new_retained(path, bytes)
    }
}

/// A fresh backend-only binding. The actual opened root is retained here;
/// callers cannot construct this from a WebView path or deserialize a grant.
pub(crate) struct ResolvedFontStaging {
    project_id: Uuid,
    generation: Uuid,
    connection_file: PathBuf,
    declaration: SavedFontStaging,
    root: Box<dyn ScopedRoot>,
}

impl ResolvedFontStaging {
    /// Validate the current selection and actual connection immediately before
    /// accepting this preparation. Clearing/reselecting invalidates unused
    /// contexts; accepted jobs keep their own retained root and custody.
    pub(crate) fn revalidate(
        &self,
        registry: &RuntimeConnectionRegistry,
        connection: &ProductionConnection,
    ) -> Result<(), String> {
        let live = registry
            .update_lock
            .lock()
            .map_err(|_| "Runtime selection state is unavailable")?;
        let current = registry
            .read_saved_reference(self.project_id, self.generation)?
            .ok_or_else(|| "Owner font staging selection was cleared".to_owned())?;
        if current.connection_file != self.connection_file
            || current.font_staging.as_ref() != Some(&self.declaration)
        {
            return Err("Owner font staging selection changed".into());
        }
        validate_live_declaration(&live, self.project_id, &self.declaration)?;
        validate_connection(
            &current.connection_file,
            connection,
            self.project_id,
            &self.declaration.connection_identity,
        )?;
        require_root_identity(self.root.as_ref(), &self.declaration.root_instance_identity)
    }

    /// Move the same already-opened root into the native coordinator. The path
    /// is provenance for the independent active Host MediaRO binding, not a
    /// write grant and not permission to reopen it. A4 must retain this object
    /// and use stage_new_retained; generic observe/output_root grants no write.
    pub(crate) fn into_retained_root(
        self,
        registry: &RuntimeConnectionRegistry,
        connection: &ProductionConnection,
    ) -> Result<(PathBuf, Box<dyn ScopedRoot>), String> {
        self.revalidate(registry, connection)?;
        Ok((self.declaration.media_root, self.root))
    }
}

fn supported_platform() -> Result<(), String> {
    // Canonical Linux/macOS roots currently lack retained root-instance
    // evidence. Do not substitute pathname/metadata inspection or an emulation.
    if !cfg!(windows) {
        return Err("Owner font staging is unsupported without native retained-root identity on this platform".into());
    }
    Ok(())
}

fn valid_root_identity(identity: &str) -> bool {
    !identity.is_empty() && identity.len() <= 256 && !identity.chars().any(char::is_control)
}

fn validate_root_path(path: &Path) -> Result<(), String> {
    if !path.is_absolute()
        || path.as_os_str().as_encoded_bytes().len() > 4096
        || path.as_os_str().as_encoded_bytes().contains(&0)
        || path
            .components()
            .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
    {
        return Err("Owner font staging root must be a normalized bounded absolute path".into());
    }
    // Lexical admission is only an input check. The canonical retained-root
    // backend performs the no-reparse directory traversal and confinement.
    Ok(())
}

fn require_root_identity(root: &dyn ScopedRoot, expected: &str) -> Result<(), String> {
    let observed = root
        .root_instance_identity()
        .map_err(|_| "Owner font staging retained root identity is unavailable")?;
    if observed != expected {
        return Err("Owner font staging retained root identity changed".into());
    }
    Ok(())
}

fn validate_live_declaration(
    live: &LiveFontStagingState,
    project_id: Uuid,
    declaration: &SavedFontStaging,
) -> Result<(), String> {
    let Some(original) = live.get(&project_id) else {
        // Reopened registries contain policy, not deserialized handles. The
        // fresh expected-identity native open below establishes the live root.
        return Ok(());
    };
    if original.declaration != *declaration {
        return Err(
            "Owner font staging declaration no longer has its original local capability".into(),
        );
    }
    require_root_identity(original.root.as_ref(), &declaration.root_instance_identity)
}

fn validate_connection(
    path: &Path,
    connection: &ProductionConnection,
    project_id: Uuid,
    expected_identity: &str,
) -> Result<(), String> {
    connection
        .validate()
        .map_err(|_| "Selected Semwright connection is no longer valid")?;
    if connection.resource != format!("project:{project_id}") {
        return Err("Selected Semwright connection belongs to another project resource".into());
    }
    let identity = connection
        .identity()
        .map_err(|_| "Selected Semwright connection identity is unavailable")?;
    if identity != expected_identity {
        return Err("Selected Semwright connection content changed".into());
    }
    let current = ProductionConnection::load(path)
        .map_err(|_| "Selected Semwright connection could not be revalidated")?;
    if current
        .identity()
        .map_err(|_| "Current Semwright connection identity is unavailable")?
        != identity
    {
        return Err("Selected Semwright connection file content changed".into());
    }
    Ok(())
}

impl RuntimeConnectionRegistry {
    /// Only a trusted native owner picker/provisioner calls this method. The
    /// owner confirms a folder for local no-replace staging; this never creates
    /// a provider mount or derives local writes from a provider observation.
    pub(crate) fn remember_font_staging(
        &self,
        project_id: Uuid,
        generation: Uuid,
        connection_file: &Path,
        connection: &ProductionConnection,
        media_root: &Path,
    ) -> Result<(), String> {
        supported_platform()?;
        let mut live = self
            .update_lock
            .lock()
            .map_err(|_| "Runtime selection state is unavailable")?;
        let mut selected = self
            .read_saved_reference(project_id, generation)?
            .ok_or_else(|| {
                "Select and verify a Semwright connection before its font staging folder".to_owned()
            })?;
        if selected.connection_file != connection_file {
            return Err("Selected Semwright connection changed during folder selection".into());
        }
        let identity = connection
            .identity()
            .map_err(|_| "Selected Semwright connection identity is unavailable")?;
        validate_connection(connection_file, connection, project_id, &identity)?;
        validate_root_path(media_root)?;
        let root = semwright_platform_services::filesystem()
            .open_root(media_root, false, true)
            .map_err(|_| "Owner font staging root could not be opened with native confinement")?;
        let root_identity = root
            .root_instance_identity()
            .map_err(|_| "Owner font staging requires native retained-root identity")?;
        if !valid_root_identity(&root_identity) {
            return Err("Owner font staging root identity is malformed".into());
        }
        let declaration = SavedFontStaging {
            schema: DECLARATION_SCHEMA.into(),
            declaration_id: Uuid::now_v7(),
            connection_identity: identity,
            media_root: media_root.to_path_buf(),
            root_instance_identity: root_identity,
        };
        selected.schema_version = REGISTRY_WITH_FONT_STAGING_SCHEMA;
        selected.font_staging = Some(declaration.clone());
        self.write_saved_reference(selected)?;
        live.insert(project_id, LiveFontStaging { declaration, root });
        Ok(())
    }

    pub(crate) fn resolve_font_staging(
        &self,
        project_id: Uuid,
        generation: Uuid,
        connection: &ProductionConnection,
    ) -> Result<Option<ResolvedFontStaging>, String> {
        let live = self
            .update_lock
            .lock()
            .map_err(|_| "Runtime selection state is unavailable")?;
        let Some(selected) = self.read_saved_reference(project_id, generation)? else {
            return Ok(None);
        };
        let Some(declaration) = selected.font_staging else {
            return Ok(None);
        };
        supported_platform()?;
        validate_live_declaration(&live, project_id, &declaration)?;
        validate_connection(
            &selected.connection_file,
            connection,
            project_id,
            &declaration.connection_identity,
        )?;
        // The original selected root stays alive here when this registry
        // selected it. After reopen, saved owner policy authorizes a fresh open.
        // The canonical backend compares the ACTUAL opened root's full instance
        // evidence and returns that same object; no PathBuf reopen follows.
        let root = semwright_platform_services::filesystem()
            .open_root_with_identity(
                &declaration.media_root,
                false,
                true,
                &declaration.root_instance_identity,
            )
            .map_err(|_| "Owner font staging root no longer names the selected directory")?;
        Ok(Some(ResolvedFontStaging {
            project_id,
            generation,
            connection_file: selected.connection_file,
            declaration,
            root: Box::new(OwnerFontStagingRoot { inner: root }),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::fs;
    use tempfile::TempDir;

    struct Fixture {
        temp: TempDir,
        registry: RuntimeConnectionRegistry,
        project: Uuid,
        generation: Uuid,
        connection_file: PathBuf,
        connection: ProductionConnection,
        media: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let temp = TempDir::new().unwrap();
            // Keep the native drive spelling on Windows. The canonical backend
            // rejects verbatim/UNC roots rather than quietly changing policy.
            #[cfg(windows)]
            let root = temp.path().to_path_buf();
            #[cfg(not(windows))]
            let root = fs::canonicalize(temp.path()).unwrap();
            let registry = RuntimeConnectionRegistry::new(&root).unwrap();
            let project = Uuid::now_v7();
            let generation = Uuid::now_v7();
            let executable = root.join("semwright-fixture");
            let session = root.join("session-fixture.json");
            let socket = root.join("socket-fixture");
            let media = root.join("media");
            let output = root.join("output");
            fs::create_dir(&media).unwrap();
            fs::create_dir(&output).unwrap();
            fs::write(&executable, b"inert fixture, never executed").unwrap();
            fs::write(&session, b"private fixture session, never copied").unwrap();
            fs::write(&socket, b"inert socket placeholder").unwrap();
            let connection = ProductionConnection {
                schema: "motionwright-semwright-connection/1".into(),
                executable,
                executable_sha256: hex::encode(Sha256::digest(b"inert fixture, never executed")),
                socket,
                session_file: session,
                output_root: output,
                resource: format!("project:{project}"),
            };
            let connection_file = root.join("connection.json");
            fs::write(&connection_file, serde_json::to_vec(&connection).unwrap()).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                for path in [
                    &connection.executable,
                    &connection.session_file,
                    &connection.socket,
                    &connection_file,
                ] {
                    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap();
                }
                for path in [&media, &connection.output_root] {
                    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
                }
            }
            registry
                .remember_verified(project, generation, &connection_file)
                .unwrap();
            Self {
                temp,
                registry,
                project,
                generation,
                connection_file,
                connection,
                media,
            }
        }

        #[cfg(windows)]
        fn declare(&self) {
            self.registry
                .remember_font_staging(
                    self.project,
                    self.generation,
                    &self.connection_file,
                    &self.connection,
                    &self.media,
                )
                .unwrap();
        }

        fn resolve(&self) -> Result<Option<ResolvedFontStaging>, String> {
            self.registry
                .resolve_font_staging(self.project, self.generation, &self.connection)
        }
    }

    #[test]
    fn missing_declaration_does_not_authorize_output_or_settings_directory() {
        let fixture = Fixture::new();
        assert!(fixture.resolve().unwrap().is_none());
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
        assert_eq!(
            fs::read_dir(&fixture.connection.output_root)
                .unwrap()
                .count(),
            0
        );
    }

    #[cfg(not(windows))]
    #[test]
    fn unsupported_backend_cannot_establish_a_local_write_declaration() {
        let fixture = Fixture::new();
        let before = fs::read(fixture.registry.destination(fixture.project)).unwrap();
        let error = fixture
            .registry
            .remember_font_staging(
                fixture.project,
                fixture.generation,
                &fixture.connection_file,
                &fixture.connection,
                &fixture.media,
            )
            .unwrap_err();
        assert!(error.contains("unsupported"));
        assert_eq!(
            fs::read(fixture.registry.destination(fixture.project)).unwrap(),
            before
        );
        assert!(fixture.resolve().unwrap().is_none());
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn reopening_registry_rebinds_saved_policy_without_persisting_live_grants() {
        let fixture = Fixture::new();
        fixture.declare();
        let reopened = RuntimeConnectionRegistry::new(fixture.temp.path()).unwrap();
        let context = reopened
            .resolve_font_staging(fixture.project, fixture.generation, &fixture.connection)
            .unwrap()
            .unwrap();
        assert_eq!(
            reopened
                .resolve(fixture.project, fixture.generation)
                .unwrap(),
            Some(fixture.connection_file.clone())
        );
        let saved = fs::read_to_string(reopened.destination(fixture.project)).unwrap();
        for forbidden in [
            "private fixture session",
            "revision",
            "mount_ref",
            "effect_grant",
        ] {
            assert!(!saved.contains(forbidden));
        }
        assert!(
            context
                .into_retained_root(&reopened, &fixture.connection)
                .is_ok()
        );
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn reopened_policy_rejects_a_replaced_directory_before_any_local_write() {
        let fixture = Fixture::new();
        fixture.declare();
        let app_data = fixture.temp.path().to_path_buf();
        drop(fixture.registry);
        fs::remove_dir(&fixture.media).unwrap();
        fs::create_dir(&fixture.media).unwrap();
        let reopened = RuntimeConnectionRegistry::new(&app_data).unwrap();
        assert!(
            reopened
                .resolve_font_staging(fixture.project, fixture.generation, &fixture.connection)
                .is_err()
        );
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn changed_connection_contents_at_same_path_invalidate_old_context() {
        let fixture = Fixture::new();
        fixture.declare();
        let context = fixture.resolve().unwrap().unwrap();
        let mut changed = fixture.connection.clone();
        changed.resource.push_str("-changed");
        fs::write(
            &fixture.connection_file,
            serde_json::to_vec(&changed).unwrap(),
        )
        .unwrap();
        assert!(
            context
                .revalidate(&fixture.registry, &fixture.connection)
                .is_err()
        );
        assert!(
            fixture
                .registry
                .resolve_font_staging(fixture.project, fixture.generation, &changed)
                .is_err()
        );
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn foreign_project_and_generation_never_reuse_an_owner_declaration() {
        let fixture = Fixture::new();
        fixture.declare();
        assert!(
            fixture
                .registry
                .resolve_font_staging(Uuid::now_v7(), fixture.generation, &fixture.connection)
                .unwrap()
                .is_none()
        );
        assert!(
            fixture
                .registry
                .resolve_font_staging(fixture.project, Uuid::now_v7(), &fixture.connection)
                .is_err()
        );
    }

    #[cfg(windows)]
    #[test]
    fn clear_and_same_path_reselection_invalidate_previously_resolved_context() {
        let fixture = Fixture::new();
        fixture.declare();
        let context = fixture.resolve().unwrap().unwrap();
        let clone = fixture.registry.clone();
        clone.clear_saved(fixture.project).unwrap();
        assert!(
            context
                .revalidate(&fixture.registry, &fixture.connection)
                .is_err()
        );
        fixture
            .registry
            .remember_verified(
                fixture.project,
                fixture.generation,
                &fixture.connection_file,
            )
            .unwrap();
        assert!(fixture.resolve().unwrap().is_none());
        fixture.declare();
        assert!(
            context
                .revalidate(&fixture.registry, &fixture.connection)
                .is_err()
        );
        assert!(
            fixture
                .resolve()
                .unwrap()
                .unwrap()
                .revalidate(&fixture.registry, &fixture.connection)
                .is_ok()
        );
    }

    #[cfg(windows)]
    #[test]
    fn replacement_path_cannot_redirect_the_actual_retained_handoff() {
        let fixture = Fixture::new();
        fixture.declare();
        let context = fixture.resolve().unwrap().unwrap();
        let owned = fixture.media.with_file_name("original-media");
        fs::rename(&fixture.media, &owned).unwrap();
        fs::create_dir(&fixture.media).unwrap();
        assert!(fixture.resolve().is_err());
        let (_, retained) = context
            .into_retained_root(&fixture.registry, &fixture.connection)
            .unwrap();
        let stage = retained
            .stage_new_retained(Path::new("font.ttf"), b"owned font fixture")
            .unwrap();
        assert_eq!(stage.observe(64).unwrap().bytes, b"owned font fixture");
        assert!(stage.verify_entry().is_ok());
        assert!(stage.verify_handoff_path().is_err());
        assert_eq!(
            fs::read(owned.join("font.ttf")).unwrap(),
            b"owned font fixture"
        );
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }

    #[cfg(windows)]
    #[test]
    fn selecting_another_connection_revokes_future_contexts_but_preserves_accepted_job_custody() {
        let fixture = Fixture::new();
        fixture.declare();
        let context = fixture.resolve().unwrap().unwrap();
        let (_, accepted_root) = fixture
            .resolve()
            .unwrap()
            .unwrap()
            .into_retained_root(&fixture.registry, &fixture.connection)
            .unwrap();
        let stage = accepted_root
            .stage_new_retained(Path::new("font.ttf"), b"accepted job artifact")
            .unwrap();
        let other = fixture
            .connection_file
            .with_file_name("other-connection.json");
        fs::write(&other, serde_json::to_vec(&fixture.connection).unwrap()).unwrap();
        fixture
            .registry
            .remember_verified(fixture.project, fixture.generation, &other)
            .unwrap();
        assert!(
            context
                .revalidate(&fixture.registry, &fixture.connection)
                .is_err()
        );
        assert!(fixture.resolve().unwrap().is_none());
        assert_eq!(stage.observe(64).unwrap().bytes, b"accepted job artifact");
        assert_eq!(
            fs::read(fixture.media.join("font.ttf")).unwrap(),
            b"accepted job artifact"
        );
    }

    #[cfg(windows)]
    #[test]
    fn local_staging_capability_refuses_existing_reads_replacement_and_publication_fallbacks() {
        let fixture = Fixture::new();
        fixture.declare();
        let sentinel = fixture.media.join("existing.ttf");
        fs::write(&sentinel, b"existing font").unwrap();
        let (_, root) = fixture
            .resolve()
            .unwrap()
            .unwrap()
            .into_retained_root(&fixture.registry, &fixture.connection)
            .unwrap();
        assert!(root.read(Path::new("existing.ttf"), 64).is_err());
        assert!(
            root.write_atomic(Path::new("existing.ttf"), b"replacement")
                .is_err()
        );
        assert!(
            root.write_new_atomic(Path::new("fallback.ttf"), b"fallback")
                .is_err()
        );
        assert!(
            root.stage_new_retained(Path::new("existing.ttf"), b"replacement")
                .is_err()
        );
        assert_eq!(fs::read(&sentinel).unwrap(), b"existing font");
        assert!(!fixture.media.join("fallback.ttf").exists());
        let stage = root
            .stage_new_retained(Path::new("owned.ttf"), b"new owned font")
            .unwrap();
        assert_eq!(stage.observe(64).unwrap().bytes, b"new owned font");
    }

    #[cfg(windows)]
    #[test]
    fn mismatched_schema_or_forged_persisted_declaration_cannot_supply_a_live_root() {
        let fixture = Fixture::new();
        fixture.declare();
        let path = fixture.registry.destination(fixture.project);
        let original = fs::read(&path).unwrap();
        let mut value: serde_json::Value = serde_json::from_slice(&original).unwrap();
        value["schema_version"] = 1.into();
        fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(fixture.resolve().is_err());
        assert!(fixture.registry.clear_saved(fixture.project).is_err());
        value["schema_version"] = 2.into();
        value["font_staging"]["declaration_id"] = Uuid::now_v7().to_string().into();
        fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(fixture.resolve().is_err());
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "requires a Windows runner with symbolic-link creation privilege"]
    fn windows_reparse_directory_cannot_be_declared_as_local_staging() {
        use std::os::windows::fs::symlink_dir;
        let fixture = Fixture::new();
        let link = fixture.media.with_file_name("linked-media");
        symlink_dir(&fixture.media, &link).unwrap();
        assert!(
            fixture
                .registry
                .remember_font_staging(
                    fixture.project,
                    fixture.generation,
                    &fixture.connection_file,
                    &fixture.connection,
                    &link,
                )
                .is_err()
        );
        assert!(fixture.resolve().unwrap().is_none());
        assert_eq!(fs::read_dir(&fixture.media).unwrap().count(), 0);
    }
}
