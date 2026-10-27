//! Admission for the one native desktop owner of the application Store.
//!
//! The owner is provisioned by native setup, not by a project selector, window
//! name, URL, request argument or effect grant. Each actual Store/effect phase
//! must obtain a fresh permit. A closed owner is never rebound in this process.

use std::{
    error::Error,
    fmt,
    path::Path,
    sync::{Arc, Mutex},
};

use tauri::{
    Manager, Resource, ResourceId, ResourceTable, Url, Webview, WebviewUrl, WebviewWindowBuilder,
    WindowEvent, Wry,
    ipc::{CommandArg, CommandItem, InvokeError},
};

const OWNER_LABEL: &str = "main";
const BOOTSTRAP_ASSET: &str = "desktop-bootstrap.html";
const DEV_DOCUMENT: &str = "http://localhost:1420/";
const OWNER_UNAVAILABLE: &str =
    "The native desktop owner is unavailable. Reopen the application to continue.";
const CONFIG_UNAVAILABLE: &str =
    "The desktop owner configuration does not match the packaged application.";

/// Not serializable and never returned to the WebView. Even a copied resource
/// number cannot manufacture this native Arc identity in another resource table.
struct OwnerMarker;

impl Resource for OwnerMarker {}

#[derive(Clone)]
struct TrustedDocument {
    entry: Url,
    entry_with_slash: Url,
    bootstrap: Url,
}

impl TrustedDocument {
    fn from_base(base: &str) -> Result<Self, String> {
        let entry = Url::parse(base).map_err(|_| CONFIG_UNAVAILABLE.to_owned())?;
        let mut entry_with_slash = entry.clone();
        if entry_with_slash.path().is_empty() {
            entry_with_slash.set_path("/");
        }
        let bootstrap = entry
            .join(BOOTSTRAP_ASSET)
            .map_err(|_| CONFIG_UNAVAILABLE.to_owned())?;
        Ok(Self {
            entry,
            entry_with_slash,
            bootstrap,
        })
    }

    fn is_entry(&self, target: &Url) -> bool {
        let mut document = target.clone();
        document.set_fragment(None);
        // Only the two canonical spellings of the root document are accepted.
        // Do not replace this with an origin, localhost, scheme or prefix test.
        document == self.entry || document == self.entry_with_slash
    }

    fn is_bootstrap(&self, target: &Url) -> bool {
        target == &self.bootstrap
    }
}

struct Binding {
    marker: Arc<OwnerMarker>,
    resource_id: ResourceId,
    document: TrustedDocument,
}

enum Lifetime {
    Unbound,
    Installing(TrustedDocument),
    Active(Arc<Binding>),
    Closed,
}

struct Lifecycle {
    lifetime: Lifetime,
    admitted_phases: usize,
}

struct Inner {
    lifecycle: Mutex<Lifecycle>,
}

#[derive(Clone)]
pub(crate) struct DesktopOwner {
    inner: Arc<Inner>,
}

impl DesktopOwner {
    pub(crate) fn new() -> Self {
        Self {
            inner: Arc::new(Inner {
                lifecycle: Mutex::new(Lifecycle {
                    lifetime: Lifetime::Unbound,
                    admitted_phases: 0,
                }),
            }),
        }
    }

    fn begin_install(&self, document: TrustedDocument) -> Result<(), String> {
        let mut state = self
            .inner
            .lifecycle
            .lock()
            .map_err(|_| OWNER_UNAVAILABLE.to_owned())?;
        if !matches!(state.lifetime, Lifetime::Unbound) {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        state.lifetime = Lifetime::Installing(document);
        Ok(())
    }

    fn activate(&self, marker: Arc<OwnerMarker>, resource_id: ResourceId) -> Result<(), String> {
        let mut state = self
            .inner
            .lifecycle
            .lock()
            .map_err(|_| OWNER_UNAVAILABLE.to_owned())?;
        let Lifetime::Installing(document) = &state.lifetime else {
            return Err(OWNER_UNAVAILABLE.to_owned());
        };
        let document = document.clone();
        state.lifetime = Lifetime::Active(Arc::new(Binding {
            marker,
            resource_id,
            document,
        }));
        Ok(())
    }

    fn bind(&self, webview: &Webview<Wry>) -> Result<(), String> {
        if webview.label() != OWNER_LABEL || webview.window_ref().label() != OWNER_LABEL {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        let marker = Arc::new(OwnerMarker);
        let resource_id = webview.resources_table().add_arc(marker.clone());
        self.activate(marker, resource_id)
    }

    fn active_binding(&self) -> Result<Arc<Binding>, String> {
        let state = self
            .inner
            .lifecycle
            .lock()
            .map_err(|_| OWNER_UNAVAILABLE.to_owned())?;
        match &state.lifetime {
            Lifetime::Active(binding) => Ok(binding.clone()),
            _ => Err(OWNER_UNAVAILABLE.to_owned()),
        }
    }

    /// Check the actual invoking WebView and the still-registered native instance.
    /// URL/name checks are additional consistency checks, not the owner identity.
    pub(crate) fn authorize_invoke(&self, webview: &Webview<Wry>) -> Result<(), String> {
        let binding = self.active_binding()?;
        if webview.label() != OWNER_LABEL || webview.window_ref().label() != OWNER_LABEL {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        if !resource_matches(&webview.resources_table(), &binding) {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        let registered = webview
            .app_handle()
            .get_webview_window(OWNER_LABEL)
            .ok_or_else(|| OWNER_UNAVAILABLE.to_owned())?;
        if !resource_matches(&registered.resources_table(), &binding) {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        // Do not hold the lifecycle mutex while the runtime resolves the URL.
        let actual_url = webview.url().map_err(|_| OWNER_UNAVAILABLE.to_owned())?;
        if !binding.document.is_entry(&actual_url) {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        let current = self.active_binding()?;
        if !Arc::ptr_eq(&binding, &current) {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        Ok(())
    }

    fn allows_navigation(&self, target: &Url) -> bool {
        let Ok(state) = self.inner.lifecycle.lock() else {
            return false;
        };
        match &state.lifetime {
            Lifetime::Installing(document) => document.is_bootstrap(target),
            Lifetime::Active(binding) => binding.document.is_entry(target),
            Lifetime::Unbound | Lifetime::Closed => false,
        }
    }

    /// Linearization point for one real phase. Call inside a queued worker,
    /// immediately before its first protected read/effect, not before enqueueing.
    pub(crate) fn phase(&self) -> Result<OwnerPhase, String> {
        let mut state = self
            .inner
            .lifecycle
            .lock()
            .map_err(|_| OWNER_UNAVAILABLE.to_owned())?;
        if !matches!(state.lifetime, Lifetime::Active(_)) {
            return Err(OWNER_UNAVAILABLE.to_owned());
        }
        state.admitted_phases = state
            .admitted_phases
            .checked_add(1)
            .ok_or_else(|| OWNER_UNAVAILABLE.to_owned())?;
        Ok(OwnerPhase {
            inner: self.inner.clone(),
        })
    }

    pub(crate) fn with_phase<T>(&self, work: impl FnOnce() -> T) -> Result<T, String> {
        let _phase = self.phase()?;
        Ok(work())
    }

    /// Retire first; never wait for admitted work or imply its IO was cancelled.
    /// Native close/restart callers must invoke this before initiating closure.
    pub(crate) fn close(&self) {
        match self.inner.lifecycle.lock() {
            Ok(mut state) => state.lifetime = Lifetime::Closed,
            Err(poisoned) => poisoned.into_inner().lifetime = Lifetime::Closed,
        }
    }
}

fn resource_matches(table: &ResourceTable, binding: &Binding) -> bool {
    table
        .get::<OwnerMarker>(binding.resource_id)
        .is_ok_and(|actual| Arc::ptr_eq(&actual, &binding.marker))
}

/// A permit is not cloneable and does not retain a MutexGuard across await.
/// Close prevents new permits; a previously admitted phase retains its outcome.
pub(crate) struct OwnerPhase {
    inner: Arc<Inner>,
}

impl Drop for OwnerPhase {
    fn drop(&mut self) {
        if let Ok(mut state) = self.inner.lifecycle.lock() {
            state.admitted_phases = state.admitted_phases.saturating_sub(1);
        }
    }
}

/// An injected native command context, never deserialized from client JSON.
/// Later Store/effect phases still need fresh permits, especially after await.
pub(crate) struct OwnerInvocation {
    owner: DesktopOwner,
    current_phase: Option<OwnerPhase>,
}

impl OwnerInvocation {
    pub(crate) fn owner(&self) -> &DesktopOwner {
        &self.owner
    }

    /// End this phase before queueing a different worker or beginning an await
    /// whose completion must precede a separately admitted Store/effect phase.
    pub(crate) fn finish_phase(&mut self) {
        drop(self.current_phase.take());
    }

    /// Hand off only the owner identity. The worker must admit its own phase
    /// after it actually starts; no queued work inherits this entry permit.
    pub(crate) fn worker_owner(&mut self) -> DesktopOwner {
        self.finish_phase();
        self.owner.clone()
    }

    /// Re-admit at the next real phase; an older permit cannot keep it alive.
    pub(crate) fn begin_phase(&mut self) -> Result<(), String> {
        self.finish_phase();
        self.current_phase = Some(self.owner.phase()?);
        Ok(())
    }

    /// For a direct Rust call to another existing command within this invocation.
    pub(crate) fn renew(&self) -> Result<Self, String> {
        let phase = self.owner.phase()?;
        Ok(Self {
            owner: self.owner.clone(),
            current_phase: Some(phase),
        })
    }
}

impl<'de> CommandArg<'de, Wry> for OwnerInvocation {
    fn from_command(command: CommandItem<'de, Wry>) -> Result<Self, InvokeError> {
        let webview = command.message.webview();
        let owner = webview
            .try_state::<DesktopOwner>()
            .map(|state| state.inner().clone())
            .ok_or_else(|| InvokeError::from(OWNER_UNAVAILABLE))?;
        owner
            .authorize_invoke(&webview)
            .map_err(InvokeError::from)?;
        let phase = owner.phase().map_err(InvokeError::from)?;
        Ok(Self {
            owner,
            current_phase: Some(phase),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OwnerTaskFailure {
    OwnerUnavailable,
    WorkerJoinFailure,
}

impl fmt::Display for OwnerTaskFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::OwnerUnavailable => OWNER_UNAVAILABLE,
            Self::WorkerJoinFailure => "The admitted desktop task could not be joined.",
        })
    }
}

impl Error for OwnerTaskFailure {}

/// Keep T intact, including an existing typed Result. Admission failure and
/// joining failure are outer failures; neither rewrites the worker's outcome.
pub(crate) async fn spawn_blocking<T, F>(
    owner: DesktopOwner,
    work: F,
) -> Result<T, OwnerTaskFailure>
where
    T: Send + 'static,
    F: FnOnce() -> T + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(move || {
        let _phase = owner
            .phase()
            .map_err(|_| OwnerTaskFailure::OwnerUnavailable)?;
        Ok(work())
    })
    .await
    .map_err(|_| OwnerTaskFailure::WorkerJoinFailure)?
}

/// App exit is independent of a window label. Retire admission even when the
/// application is exiting through a native menu, restart or non-window path.
pub(crate) fn lifecycle_plugin() -> tauri::plugin::TauriPlugin<Wry> {
    tauri::plugin::Builder::<Wry>::new("motionwright-desktop-owner")
        .on_event(|app, event| {
            let retiring = match event {
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => true,
                tauri::RunEvent::WindowEvent { label, event, .. } => {
                    label == OWNER_LABEL
                        && matches!(
                            event,
                            WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed
                        )
                }
                _ => false,
            };
            // Window listener registration is queued by Wry. This global
            // fallback can only retire admission during that startup gap;
            // a native label never grants or rebinds an owner instance.
            if retiring {
                if let Some(owner) = app.try_state::<DesktopOwner>() {
                    owner.close();
                }
            }
        })
        .build()
}

fn config_selected_equal(actual: &serde_json::Value, expected: &serde_json::Value) -> bool {
    [
        "/identifier",
        "/app/windows",
        "/app/security",
        "/app/withGlobalTauri",
        "/app/appDirectoriesOverride",
        "/build/devUrl",
        "/build/frontendDist",
    ]
    .into_iter()
    .all(|pointer| actual.pointer(pointer) == expected.pointer(pointer))
}

/// RFC 7386 object merge, matching tauri-codegen's JSON merge semantics. This
/// only consumes the compile-time overlay; no request or runtime environment.
fn merge_configuration(target: &mut serde_json::Value, patch: serde_json::Value) {
    match patch {
        serde_json::Value::Object(patch) => {
            if !target.is_object() {
                *target = serde_json::Value::Object(serde_json::Map::new());
            }
            if let serde_json::Value::Object(target) = target {
                for (key, value) in patch {
                    if value.is_null() {
                        target.remove(&key);
                    } else {
                        merge_configuration(
                            target.entry(key).or_insert(serde_json::Value::Null),
                            value,
                        );
                    }
                }
            }
        }
        replacement => *target = replacement,
    }
}

fn normalized_configuration(value: serde_json::Value) -> Result<serde_json::Value, String> {
    let config: tauri::utils::config::Config =
        serde_json::from_value(value).map_err(|_| CONFIG_UNAVAILABLE.to_owned())?;
    serde_json::to_value(config).map_err(|_| CONFIG_UNAVAILABLE.to_owned())
}

/// Check the same compilation environment read by generate_context! before
/// codegen can discard fields such as assetProtocol.enable. Packaging-only
/// overlay changes are allowed; the owner provisioning fields must match.
fn source_configuration(source: &str, overlay: Option<&str>) -> Result<serde_json::Value, String> {
    let mut source: serde_json::Value =
        serde_json::from_str(source).map_err(|_| CONFIG_UNAVAILABLE.to_owned())?;
    let expected = normalized_configuration(source.clone())?;
    if let Some(overlay) = overlay {
        let patch = serde_json::from_str(overlay).map_err(|_| CONFIG_UNAVAILABLE.to_owned())?;
        merge_configuration(&mut source, patch);
        if !config_selected_equal(&normalized_configuration(source)?, &expected) {
            return Err(CONFIG_UNAVAILABLE.to_owned());
        }
    }
    Ok(expected)
}

/// Reject compile-time overlay drift plus drift retained by runtime config.
/// Platform-file fields erased by codegen still require verified build inputs;
/// this does not attest the executable, dependency features, or bundled assets.
fn trusted_configuration(app: &tauri::App<Wry>) -> Result<TrustedDocument, String> {
    let configured = app.config();
    let expected = source_configuration(
        include_str!("../tauri.conf.json"),
        option_env!("TAURI_CONFIG"),
    )?;
    let actual = serde_json::to_value(configured).map_err(|_| CONFIG_UNAVAILABLE.to_owned())?;
    if !config_selected_equal(&actual, &expected) {
        return Err(CONFIG_UNAVAILABLE.to_owned());
    }
    if configured.app.windows.len() != 1
        || actual.pointer("/app/security/capabilities")
            != Some(&serde_json::json!(["desktop-owner"]))
    {
        return Err(CONFIG_UNAVAILABLE.to_owned());
    }
    let window = &configured.app.windows[0];
    if window.label != OWNER_LABEL
        || window.create
        || !matches!(&window.url, WebviewUrl::App(path) if path == Path::new("index.html"))
        || actual
            .pointer("/app/windows/0/useHttpsScheme")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false)
    {
        return Err(CONFIG_UNAVAILABLE.to_owned());
    }
    if configured.build.dev_url.as_ref().map(Url::as_str) != Some(DEV_DOCUMENT)
        || actual.pointer("/build/frontendDist") != Some(&serde_json::json!("../../studio/dist"))
    {
        return Err(CONFIG_UNAVAILABLE.to_owned());
    }
    if tauri::is_dev() {
        TrustedDocument::from_base(DEV_DOCUMENT)
    } else if cfg!(target_os = "windows") {
        TrustedDocument::from_base("http://tauri.localhost/")
    } else if cfg!(any(target_os = "linux", target_os = "macos")) {
        TrustedDocument::from_base("tauri://localhost")
    } else {
        Err(CONFIG_UNAVAILABLE.to_owned())
    }
}

/// Run before native setup opens its Store or creates app-data directories.
/// Installation revalidates the same immutable native configuration.
pub(crate) fn validate_configuration(app: &tauri::App<Wry>) -> Result<(), String> {
    trusted_configuration(app).map(|_| ())
}

/// Called only from the trusted setup closure after managing DesktopOwner and
/// AppState. Config uses create:false, so no unguarded automatic window exists.
pub(crate) fn install_owner_window(
    app: &tauri::App<Wry>,
    owner: &DesktopOwner,
) -> Result<(), Box<dyn Error>> {
    let result = (|| -> Result<(), Box<dyn Error>> {
        let document = trusted_configuration(app)?;
        owner.begin_install(document.clone())?;
        // The stable API enumerates WebviewWindows. Other native views, if a
        // future native component creates one, cannot acquire our private marker.
        if !app.webview_windows().is_empty() {
            return Err(CONFIG_UNAVAILABLE.into());
        }
        let mut config = app.config().app.windows[0].clone();
        let should_show = config.visible;
        // Same scheme/host as the final document preserves the protocol/asset
        // CORS origin fixed by Tauri at creation. about:blank would not do so.
        config.url = WebviewUrl::App(BOOTSTRAP_ASSET.into());
        config.visible = false;
        let navigation_owner = owner.clone();
        let window = WebviewWindowBuilder::from_config(app, &config)?
            .on_navigation(move |target| navigation_owner.allows_navigation(target))
            .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
            .build()?;
        let closing_owner = owner.clone();
        window.on_window_event(move |event| {
            if matches!(
                event,
                WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed
            ) {
                closing_owner.close();
            }
        });
        let finish = (|| -> Result<(), Box<dyn Error>> {
            owner.bind(window.as_ref())?;
            window.navigate(document.entry)?;
            if should_show {
                window.show()?;
            }
            Ok(())
        })();
        if finish.is_err() {
            owner.close();
            let _ = window.close();
        }
        finish
    })();
    if result.is_err() {
        owner.close();
    }
    result
}

/// Unit/service-fixture admission state only. This does not provision a native
/// WebView, authenticate an IPC actor or establish a runtime isolation result.
#[cfg(test)]
pub(crate) fn source_test_owner() -> DesktopOwner {
    let owner = DesktopOwner::new();
    owner
        .begin_install(TrustedDocument::from_base(DEV_DOCUMENT).unwrap())
        .unwrap();
    owner.activate(Arc::new(OwnerMarker), 17).unwrap();
    owner
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc,
    };

    fn active_owner() -> DesktopOwner {
        source_test_owner()
    }

    #[test]
    fn unbound_owner_cannot_admit_or_be_claimed_by_a_navigation() {
        let owner = DesktopOwner::new();
        assert!(owner.phase().is_err());
        assert!(!owner.allows_navigation(&Url::parse(DEV_DOCUMENT).unwrap()));
    }

    #[test]
    fn close_wins_before_a_queued_worker_enters_its_store_phase() {
        let owner = active_owner();
        let worker_owner = owner.clone();
        let touched = Arc::new(AtomicUsize::new(0));
        let worker_touched = touched.clone();
        let (release, wait) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            wait.recv().unwrap();
            worker_owner.with_phase(|| worker_touched.fetch_add(1, Ordering::SeqCst))
        });
        owner.close();
        release.send(()).unwrap();
        assert!(worker.join().unwrap().is_err());
        assert_eq!(touched.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn close_preserves_an_admitted_outcome_and_blocks_the_next_phase() {
        let owner = active_owner();
        let worker_owner = owner.clone();
        let effects = Arc::new(AtomicUsize::new(0));
        let worker_effects = effects.clone();
        let (entered, admitted) = mpsc::channel();
        let (release, wait) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            let _phase = worker_owner.phase().unwrap();
            entered.send(()).unwrap();
            wait.recv().unwrap();
            // Already-admitted work completes even after the owner is retired.
            worker_effects.fetch_add(1, Ordering::SeqCst);
            "original terminal result"
        });
        admitted.recv().unwrap();
        owner.close();
        release.send(()).unwrap();
        assert_eq!(worker.join().unwrap(), "original terminal result");
        assert_eq!(effects.load(Ordering::SeqCst), 1);
        assert!(
            owner
                .with_phase(|| effects.fetch_add(1, Ordering::SeqCst))
                .is_err()
        );
        assert_eq!(effects.load(Ordering::SeqCst), 1);
        assert_eq!(owner.inner.lifecycle.lock().unwrap().admitted_phases, 0);
    }

    #[test]
    fn closed_owner_cannot_rebind_under_the_same_label() {
        let owner = active_owner();
        owner.close();
        assert!(
            owner
                .begin_install(TrustedDocument::from_base(DEV_DOCUMENT).unwrap())
                .is_err()
        );
        assert!(owner.activate(Arc::new(OwnerMarker), 17).is_err());
    }

    #[test]
    fn copied_resource_number_and_same_type_do_not_replace_native_arc_identity() {
        let marker = Arc::new(OwnerMarker);
        let mut table = ResourceTable::default();
        let resource_id = table.add_arc(marker.clone());
        let binding = Binding {
            marker,
            resource_id,
            document: TrustedDocument::from_base(DEV_DOCUMENT).unwrap(),
        };
        assert!(resource_matches(&table, &binding));
        assert!(!resource_matches(&ResourceTable::default(), &binding));
        // Reuse the exact ID and type, as a replacement view might claim the
        // same name. The private native instance still must not compare equal.
        table.replace(resource_id, OwnerMarker);
        assert!(!resource_matches(&table, &binding));
    }

    #[test]
    fn navigation_uses_exact_native_documents_and_retires_bootstrap_after_bind() {
        let owner = DesktopOwner::new();
        let document = TrustedDocument::from_base(DEV_DOCUMENT).unwrap();
        owner.begin_install(document.clone()).unwrap();
        assert!(owner.allows_navigation(&document.bootstrap));
        assert!(!owner.allows_navigation(&document.entry));
        owner.activate(Arc::new(OwnerMarker), 17).unwrap();
        assert!(!owner.allows_navigation(&document.bootstrap));
        assert!(owner.allows_navigation(&document.entry));
        assert!(owner.allows_navigation(&Url::parse("http://localhost:1420/#review").unwrap()));
        for denied in [
            "http://localhost:1421/",
            "http://127.0.0.1:1420/",
            "http://localhost:1420/index.html",
            "http://localhost:1420/other",
            "http://localhost:1420/?source=foreign",
            "http://user:secret@localhost:1420/",
            "https://localhost:1420/",
            "http://localhost.evil.test:1420/",
            "about:blank",
            "file:///private/project.sqlite3",
        ] {
            assert!(
                !owner.allows_navigation(&Url::parse(denied).unwrap()),
                "{denied}"
            );
        }
        owner.close();
        assert!(!owner.allows_navigation(&document.entry));
    }

    #[test]
    fn packaged_document_does_not_admit_dev_or_other_protocol_paths() {
        let document = TrustedDocument::from_base("tauri://localhost").unwrap();
        assert!(document.is_entry(&Url::parse("tauri://localhost").unwrap()));
        assert!(document.is_entry(&Url::parse("tauri://localhost/#canvas").unwrap()));
        for denied in [
            DEV_DOCUMENT,
            "tauri://localhost/other",
            "tauri://localhost/?foreign=1",
            "tauri://foreign/",
            "asset://localhost/private.png",
        ] {
            assert!(!document.is_entry(&Url::parse(denied).unwrap()), "{denied}");
        }
    }

    #[test]
    fn compile_time_overlay_cannot_enable_assets_or_redirect_private_store() {
        let source = include_str!("../tauri.conf.json");
        for overlay in [
            r#"{"app":{"security":{"assetProtocol":{"enable":true}}}}"#,
            r#"{"app":{"appDirectoriesOverride":{"data":"foreign-store"}}}"#,
        ] {
            assert!(source_configuration(source, Some(overlay)).is_err());
        }
    }

    #[test]
    fn packaging_only_overlay_preserves_native_owner_configuration() {
        let source = include_str!("../tauri.conf.json");
        assert!(
            source_configuration(
                source,
                Some(r#"{"bundle":{"active":false,"targets":["nsis"]}}"#),
            )
            .is_ok()
        );
    }
}
