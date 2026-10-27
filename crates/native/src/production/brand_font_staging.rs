//! Process-owned custody for the exact SDK root accepted by the native owner.
//! Paths describe the separate Host MediaRO join; they never reopen a writer.
use super::*;
use motionwright_service::VerifiedBrandFont;
use semwright_platform_api::filesystem::{
    Confinement, RetainedFileStage, RetainedStageError, ScopedRoot,
};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex, MutexGuard, Weak},
};

const MAX_RETAINED_ROOTS: usize = 32;
const MAX_RETAINED_STAGES: usize = 64;
const MAX_CAPTURE_SCOPES: usize = 64;

/// Closed owner routes. This value selects required work; it is not a grant.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ProductionFontInvocationKind {
    Full,
    Window,
}

#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum FontWorkKind {
    FullRender,
    FullPreview,
    WindowRender,
    WindowCache,
    WindowRegistry,
    WindowCurrency,
}

impl FontWorkKind {
    fn name(self) -> &'static str {
        match self {
            Self::FullRender => "full_render",
            Self::FullPreview => "full_preview",
            Self::WindowRender => "window_render",
            Self::WindowCache => "window_cache",
            Self::WindowRegistry => "window_registry",
            Self::WindowCurrency => "window_currency",
        }
    }
}

impl ProductionFontInvocationKind {
    fn required(self) -> &'static [FontWorkKind] {
        match self {
            Self::Full => &[FontWorkKind::FullRender, FontWorkKind::FullPreview],
            Self::Window => &[
                FontWorkKind::WindowRender,
                FontWorkKind::WindowCache,
                FontWorkKind::WindowRegistry,
                FontWorkKind::WindowCurrency,
            ],
        }
    }

    fn render(self) -> FontWorkKind {
        match self {
            Self::Full => FontWorkKind::FullRender,
            Self::Window => FontWorkKind::WindowRender,
        }
    }
}

enum FontWorkState {
    Accepted,
    Queued,
    Running,
    NotStarted,
    Succeeded,
    Stopped {
        outcome_known: Option<bool>,
        error: Option<Value>,
    },
}

/// The unique backend owner of one accepted invocation. It cannot be cloned or
/// deserialized. Coordinator clones do not receive this completion capability.
#[must_use]
pub struct ProductionFontStagingOwner {
    id: Uuid,
    root: Arc<RetainedFontRoot>,
}

/// A registered caller postcheck. Move it into the actual worker before spawn.
/// Dropping it, cancelling its waiter, or panicking never records success.
#[must_use]
pub struct ProductionFontPostcheck {
    work: FontInvocationWork,
}

pub(super) struct FontInvocationWork {
    root: Arc<RetainedFontRoot>,
    kind: FontWorkKind,
    active: bool,
}

impl FontInvocationWork {
    pub(super) fn start_native(&mut self) -> NativeResult<()> {
        let owner = self
            .root
            .owner
            .upgrade()
            .ok_or_else(|| backend("Retained font owner custody ended").uncertain())?;
        let owner = owner
            .lock()
            .map_err(|_| backend("Retained font owner custody is unavailable").uncertain())?;
        let mut state =
            self.root.state.lock().map_err(|_| {
                backend("Retained font invocation custody is unavailable").uncertain()
            })?;
        let belongs = owner
            .roots
            .get(&self.root.id)
            .is_some_and(|root| Arc::ptr_eq(root, &self.root));
        if !belongs
            || state.retired
            || !self.active
            || state.borrowers == 0
            || !matches!(state.work.get(&self.kind), Some(FontWorkState::Queued))
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Retained font work cannot start from this owner state",
            ));
        }
        if state.owner_closed {
            // The queued Future has not entered its inner body. This is a
            // known non-start, not a generic interpretation of a returned Err.
            *state.work.get_mut(&self.kind).expect("queued closed slot") =
                FontWorkState::NotStarted;
            state.borrowers -= 1;
            self.active = false;
            return Err(Error::new(
                ErrorCode::Conflict,
                "Retained font owner closed before queued native work started",
            ));
        }
        *state.work.get_mut(&self.kind).expect("queued closed slot") = FontWorkState::Running;
        Ok(())
    }

    fn start_postcheck(&mut self) {
        let mut state = retained_lock(&self.root.state);
        if self.active
            && state.borrowers > 0
            && matches!(state.work.get(&self.kind), Some(FontWorkState::Queued))
        {
            *state
                .work
                .get_mut(&self.kind)
                .expect("accepted caller slot") = FontWorkState::Running;
        } else {
            state.retirement_refused = true;
            state.lifecycle_faulted = true;
        }
        // An already accepted caller callback remains counted even if its owner
        // closes. Unlike a queued native render it cannot begin a font capture;
        // these actual callbacks only register/recheck the completed evidence.
    }

    fn finish(mut self, terminal: FontWorkState) {
        let mut state = retained_lock(&self.root.state);
        if self.root.state.is_poisoned()
            || state.borrowers == 0
            || !matches!(state.work.get(&self.kind), Some(FontWorkState::Running))
        {
            state.lifecycle_faulted = true;
            return;
        }
        *state
            .work
            .get_mut(&self.kind)
            .expect("accepted closed work slot") = terminal;
        state.borrowers -= 1;
        self.active = false;
    }

    pub(super) fn finish_native<T>(self, result: &NativeResult<T>) {
        let terminal = match result {
            Ok(_) => FontWorkState::Succeeded,
            Err(error) => FontWorkState::Stopped {
                outcome_known: Some(error.outcome_known),
                error: Some(json!(error)),
            },
        };
        self.finish(terminal);
    }

    fn finish_postcheck(self, succeeded: bool) {
        self.finish(if succeeded {
            FontWorkState::Succeeded
        } else {
            // The caller returns its original error. This custody records no
            // fabricated SDK outcome or replacement serialized exception.
            FontWorkState::Stopped {
                outcome_known: None,
                error: None,
            }
        });
    }
}

impl Drop for FontInvocationWork {
    fn drop(&mut self) {
        if self.active {
            let mut state = retained_lock(&self.root.state);
            if state.borrowers > 0 {
                state.borrowers -= 1;
            } else {
                state.retirement_refused = true;
                state.lifecycle_faulted = true;
            }
            match state.work.get_mut(&self.kind) {
                Some(work) if matches!(work, FontWorkState::Queued) => {
                    *work = FontWorkState::NotStarted;
                }
                _ => {}
            }
            // A queued guard never entered its body. Running remains unresolved
            // after drop/panic; zero borrows alone never permits retirement.
        }
        // Run only after releasing the entry lock. The final known guard may
        // complete an owner closure; this never upgrades Running or UNKNOWN.
        self.root.retire_known_before_capture(false);
    }
}

impl ProductionFontPostcheck {
    /// Run a postcheck whose existing API returns a value (Full preview).
    /// Only an actual normal callback return records completion.
    pub fn run<T>(mut self, operation: impl FnOnce() -> T) -> T {
        self.work.start_postcheck();
        let result = operation();
        self.work.finish_postcheck(true);
        result
    }

    /// Preserve the caller's original Result; Err never becomes completion.
    pub fn run_result<T, E>(mut self, operation: impl FnOnce() -> Result<T, E>) -> Result<T, E> {
        self.work.start_postcheck();
        let result = operation();
        self.work.finish_postcheck(result.is_ok());
        result
    }
}

impl ProductionFontStagingOwner {
    fn accept_postcheck(&mut self, kind: FontWorkKind) -> NativeResult<ProductionFontPostcheck> {
        if self.id != self.root.id {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Font owner identity changed",
            ));
        }
        Ok(ProductionFontPostcheck {
            work: self.root.accept_work(kind)?,
        })
    }

    pub fn accept_full_preview(&mut self) -> NativeResult<ProductionFontPostcheck> {
        self.accept_postcheck(FontWorkKind::FullPreview)
    }

    pub fn accept_window_registry(&mut self) -> NativeResult<ProductionFontPostcheck> {
        self.accept_postcheck(FontWorkKind::WindowRegistry)
    }

    pub fn accept_window_currency(&mut self) -> NativeResult<ProductionFontPostcheck> {
        self.accept_postcheck(FontWorkKind::WindowCurrency)
    }

    fn retire_for_root(self, expected: &Arc<RetainedFontRoot>) -> bool {
        if self.id != expected.id || !Arc::ptr_eq(&self.root, expected) {
            retained_lock(&self.root.state).retirement_refused = true;
            return false;
        }
        self.root.retire_after_owner_success()
    }

    /// Consume this invocation's owner after the actual render and caller
    /// postchecks returned successfully. True releases only custody's handles;
    /// it is not OS-close, file removal, child exit, or crash recovery evidence.
    /// False never means native success. Dropping the consumed owner may still
    /// release a separately proved, known invocation that never admitted capture.
    pub fn retire_after_success(self, coordinator: &ProductionCoordinator) -> bool {
        let Some(expected) = coordinator.retained_brand_font_root.as_ref() else {
            retained_lock(&self.root.state).retirement_refused = true;
            return false;
        };
        self.retire_for_root(expected)
    }
}

impl Drop for ProductionFontStagingOwner {
    fn drop(&mut self) {
        // Closing this unique owner is not a success acknowledgement. Accepted
        // running/background/UNKNOWN work remains in the existing custody.
        self.root.retire_known_before_capture(true);
    }
}

/// An in-process invocation scope, never a deserializable grant.
pub struct ProductionFontStagingScope {
    project_id: Uuid,
    expected: RevisionStamp,
    request_id: String,
    invocation_kind: ProductionFontInvocationKind,
}

impl ProductionFontStagingScope {
    pub fn new(project_id: Uuid, expected: RevisionStamp, request_id: &str) -> NativeResult<Self> {
        if expected.resource != format!("project:{project_id}")
            || request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("Retained font invocation scope is invalid"));
        }
        Ok(Self {
            project_id,
            expected,
            request_id: request_id.to_owned(),
            invocation_kind: ProductionFontInvocationKind::Full,
        })
    }

    fn matches(&self, project_id: Uuid, expected: &RevisionStamp, prefix: &str) -> bool {
        let suffix = prefix
            .strip_prefix(&self.request_id)
            .and_then(|value| value.strip_prefix(":motion:"));
        self.project_id == project_id
            && self.expected == *expected
            && suffix.is_some_and(|value| {
                value
                    .parse::<usize>()
                    .is_ok_and(|index| index.to_string() == value)
            })
    }
}

#[derive(Default)]
struct CustodyState {
    roots: BTreeMap<Uuid, Arc<RetainedFontRoot>>,
}

/// Bounded backend custody shared by AppState clones. Never expose its private
/// snapshot to the WebView. OS handles survive a coordinator drop only while
/// this owner process remains alive; snapshots cannot recreate those handles.
#[derive(Clone, Default)]
pub struct ProductionFontStagingCustody {
    inner: Arc<Mutex<CustodyState>>,
}

fn retained_lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    // Only retention/inspection may recover a poisoned container. New work
    // explicitly rejects poison before creating or retiring any object.
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

impl ProductionFontStagingCustody {
    fn accept(
        &self,
        scope: ProductionFontStagingScope,
        connection_identity: String,
        path: PathBuf,
        root: Box<dyn ScopedRoot>,
    ) -> NativeResult<Arc<RetainedFontRoot>> {
        if self.inner.is_poisoned() {
            return Err(backend("Retained font owner custody is unavailable"));
        }
        if !path.is_absolute()
            || path.as_os_str().as_encoded_bytes().len() > 4096
            || path.as_os_str().as_encoded_bytes().contains(&0)
            || path
                .components()
                .any(|part| matches!(part, Component::CurDir | Component::ParentDir))
        {
            return Err(invalid("Retained font provenance root is invalid"));
        }
        if !matches!(
            root.confinement(),
            Confinement::WindowsHandleRelativeNoReparse
        ) {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Owner font root lacks the admitted retained no-reparse confinement",
            ));
        }
        let identity = root.root_instance_identity()?;
        if identity.is_empty() || identity.len() > 256 || identity.chars().any(char::is_control) {
            return Err(backend("Retained font root identity is malformed"));
        }
        let mut owner = self
            .inner
            .lock()
            .map_err(|_| backend("Retained font owner custody is unavailable"))?;
        if owner.roots.len() >= MAX_RETAINED_ROOTS {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Retained font owner custody is full; reconcile existing invocations",
            ));
        }
        let id = Uuid::new_v4();
        if owner.roots.contains_key(&id) {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Retained font invocation identity already exists",
            ));
        }
        let state = RootState::new(scope.invocation_kind);
        let accepted = Arc::new(RetainedFontRoot {
            id,
            scope,
            connection_identity,
            path,
            root_identity: identity,
            root,
            owner: Arc::downgrade(&self.inner),
            state: Mutex::new(state),
        });
        owner.roots.insert(id, accepted.clone());
        Ok(accepted)
    }

    /// Private observations only. Neither a saved root path nor a terminal
    /// label in this JSON can construct a capability or authorize another call.
    pub fn private_snapshot(&self) -> Value {
        let owner = retained_lock(&self.inner);
        json!({
            "schema": "motionwright.retained-font-custody/3",
            "process_bound": true,
            "durable_handles": false,
            "os_close_confirmed": false,
            "max_roots": MAX_RETAINED_ROOTS,
            "max_stages_per_root": MAX_RETAINED_STAGES,
            "roots": owner.roots.values().map(|root| root.snapshot()).collect::<Vec<_>>()
        })
    }
}

enum CaptureState {
    Running,
    CaptureTerminal,
    Stopped { outcome_known: bool, error: Value },
}

struct RootState {
    retired: bool,
    retirement_reason: Option<&'static str>,
    owner_closed: bool,
    retirement_refused: bool,
    lifecycle_faulted: bool,
    borrowers: usize,
    work: BTreeMap<FontWorkKind, FontWorkState>,
    captures: BTreeMap<String, CaptureState>,
    stages: Vec<FontStage>,
}

impl RootState {
    fn new(kind: ProductionFontInvocationKind) -> Self {
        Self {
            retired: false,
            retirement_reason: None,
            owner_closed: false,
            retirement_refused: false,
            lifecycle_faulted: false,
            borrowers: 0,
            work: kind
                .required()
                .iter()
                .map(|kind| (*kind, FontWorkState::Accepted))
                .collect(),
            captures: BTreeMap::new(),
            stages: Vec::with_capacity(MAX_RETAINED_STAGES),
        }
    }
}

struct FontStage {
    canonical_name: String,
    relative: String,
    sha256: String,
    bytes: usize,
    retained: Option<Box<dyn RetainedFileStage>>,
    instance_identity: Option<String>,
    observation_method: Option<(&'static str, u32)>,
    error: Option<Value>,
}

pub(super) struct RetainedFontRoot {
    id: Uuid,
    scope: ProductionFontStagingScope,
    connection_identity: String,
    path: PathBuf,
    root_identity: String,
    root: Box<dyn ScopedRoot>,
    owner: Weak<Mutex<CustodyState>>,
    state: Mutex<RootState>,
}

pub(super) struct FontCaptureCustody {
    root: Arc<RetainedFontRoot>,
    prefix: String,
    active: bool,
}

impl FontCaptureCustody {
    pub(super) fn finish<T>(mut self, result: &NativeResult<T>) {
        let mut state = retained_lock(&self.root.state);
        if state.borrowers == 0 {
            state.retirement_refused = true;
            return;
        }
        let Some(slot) = state.captures.get_mut(&self.prefix) else {
            return;
        };
        // Cancellation/drop never calls finish and leaves Running. A poisoned
        // custody state cannot be promoted to a confirmed capture terminal.
        if !matches!(slot, CaptureState::Running) || self.root.state.is_poisoned() {
            return;
        }
        *slot = match result {
            Ok(_) => CaptureState::CaptureTerminal,
            Err(error) => CaptureState::Stopped {
                outcome_known: error.outcome_known,
                error: json!(error),
            },
        };
        state.borrowers -= 1;
        self.active = false;
    }
}

impl Drop for FontCaptureCustody {
    fn drop(&mut self) {
        if !self.active {
            return;
        }
        let mut state = retained_lock(&self.root.state);
        if state.borrowers > 0 {
            state.borrowers -= 1;
        } else {
            state.retirement_refused = true;
            state.lifecycle_faulted = true;
        }
        // Cancellation leaves the capture Running, never CaptureTerminal.
    }
}

impl RetainedFontRoot {
    fn accept_work(self: &Arc<Self>, kind: FontWorkKind) -> NativeResult<FontInvocationWork> {
        if self.state.is_poisoned() {
            return Err(backend("Retained font invocation custody is unavailable").uncertain());
        }
        let owner = self
            .owner
            .upgrade()
            .ok_or_else(|| backend("Retained font owner custody ended").uncertain())?;
        // Admission and retirement use the same owner -> entry lock order.
        let owner = owner
            .lock()
            .map_err(|_| backend("Retained font owner custody is unavailable").uncertain())?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| backend("Retained font invocation custody is unavailable").uncertain())?;
        let belongs = owner
            .roots
            .get(&self.id)
            .is_some_and(|root| Arc::ptr_eq(root, self));
        if !belongs
            || state.retired
            || state.owner_closed
            || !matches!(state.work.get(&kind), Some(FontWorkState::Accepted))
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Retained font work is foreign, retired or already accepted",
            ));
        }
        state.borrowers = state
            .borrowers
            .checked_add(1)
            .ok_or_else(|| backend("Retained font borrow count overflow"))?;
        *state
            .work
            .get_mut(&kind)
            .expect("closed slot reserved at owner acceptance") = FontWorkState::Queued;
        Ok(FontInvocationWork {
            root: self.clone(),
            kind,
            active: true,
        })
    }

    fn accept_render(
        self: &Arc<Self>,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        connection_identity: &str,
        kind: ProductionFontInvocationKind,
    ) -> NativeResult<FontInvocationWork> {
        if self.scope.project_id != project_id
            || self.scope.expected != *expected
            || self.scope.request_id != request_id
            || self.scope.invocation_kind != kind
            || self.connection_identity != connection_identity
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Retained font render belongs to another owner invocation or connection",
            ));
        }
        self.accept_work(kind.render())
    }

    fn validate_root(&self) -> NativeResult<()> {
        if self.root.root_instance_identity()? != self.root_identity {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "The accepted retained font root changed identity",
            ));
        }
        Ok(())
    }

    fn validate_stage(
        stage: &FontStage,
        observe: bool,
    ) -> NativeResult<Option<(String, &'static str, u32)>> {
        if stage.error.is_some() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "A retained font stage already failed; preserve its original outcome",
            )
            .uncertain());
        }
        let capsule = stage
            .retained
            .as_ref()
            .ok_or_else(|| backend("Font creation has no returned retained capsule").uncertain())?;
        let state = capsule.state();
        if !state.created
            || state.requested_bytes != stage.bytes as u64
            || state.confirmed_written_bytes != stage.bytes as u64
            || state.partial
            || !state.synced
            || !state.entry_verified
        {
            return Err(backend(
                "Retained font stage is not a complete synced verified file",
            ));
        }
        capsule.verify_entry()?;
        let observed = if observe {
            let observed = capsule.observe(stage.bytes)?;
            if observed.bytes.len() != stage.bytes
                || hex::encode(Sha256::digest(&observed.bytes)) != stage.sha256
                || observed.instance_identity.is_empty()
                || observed.instance_identity.len() > 256
                || observed.instance_identity.chars().any(char::is_control)
                || stage
                    .instance_identity
                    .as_ref()
                    .is_some_and(|identity| identity != &observed.instance_identity)
                || observed.method.is_empty()
                || observed.method_version == 0
            {
                return Err(backend(
                    "Retained font bytes or instance differ from the CAS source",
                ));
            }
            Some((
                observed.instance_identity,
                observed.method,
                observed.method_version,
            ))
        } else {
            None
        };
        // SDK verifies a read-only handoff path against THIS retained file.
        // This never obtains another write root or replaces this capsule.
        capsule.verify_handoff_path()?;
        Ok(observed)
    }

    pub(super) fn revalidate(&self) -> NativeResult<()> {
        if self.state.is_poisoned() {
            return Err(backend("Retained font invocation custody is unavailable").uncertain());
        }
        let state = self
            .state
            .lock()
            .map_err(|_| backend("Retained font invocation custody is unavailable").uncertain())?;
        if state.retired {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Retained font invocation was retired",
            ));
        }
        self.validate_root()?;
        for stage in &state.stages {
            // The admitted Windows capsule retains its original write-denying
            // handle. Entry/handoff checks preserve that identity across awaits.
            Self::validate_stage(stage, false)?;
        }
        Ok(())
    }

    pub(super) fn begin(
        self: &Arc<Self>,
        project_id: Uuid,
        expected: &RevisionStamp,
        prefix: &str,
        connection_identity: &str,
    ) -> NativeResult<FontCaptureCustody> {
        if !self.scope.matches(project_id, expected, prefix)
            || self.connection_identity != connection_identity
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Retained font root belongs to another invocation or connection",
            ));
        }
        self.revalidate()?;
        let owner = self
            .owner
            .upgrade()
            .ok_or_else(|| backend("Retained font owner custody ended").uncertain())?;
        let owner = owner
            .lock()
            .map_err(|_| backend("Retained font owner custody is unavailable").uncertain())?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| backend("Retained font invocation custody is unavailable").uncertain())?;
        let belongs = owner
            .roots
            .get(&self.id)
            .is_some_and(|root| Arc::ptr_eq(root, self));
        if !belongs
            || state.retired
            || state.owner_closed
            || state.captures.contains_key(prefix)
            || state
                .captures
                .values()
                .any(|capture| !matches!(capture, CaptureState::CaptureTerminal))
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Retained font invocation is active, stopped or already attempted",
            )
            .uncertain());
        }
        if state.captures.len() >= MAX_CAPTURE_SCOPES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Retained font capture budget is full",
            ));
        }
        state.borrowers = state
            .borrowers
            .checked_add(1)
            .ok_or_else(|| backend("Retained font capture borrow count overflow"))?;
        state
            .captures
            .insert(prefix.to_owned(), CaptureState::Running);
        Ok(FontCaptureCustody {
            root: self.clone(),
            prefix: prefix.to_owned(),
            active: true,
        })
    }

    pub(super) fn stage(&self, font: &VerifiedBrandFont) -> NativeResult<String> {
        let canonical_name = super::brand_fonts::font_relative_name(font)?;
        self.revalidate()?;
        let owner = self
            .owner
            .upgrade()
            .ok_or_else(|| backend("Retained font owner custody ended").uncertain())?;
        let owner = owner
            .lock()
            .map_err(|_| backend("Retained font owner custody is unavailable").uncertain())?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| backend("Retained font invocation custody is unavailable").uncertain())?;
        let belongs = owner
            .roots
            .get(&self.id)
            .is_some_and(|root| std::ptr::eq(root.as_ref(), self));
        if !belongs
            || state.retired
            || state.owner_closed
            || !state
                .captures
                .values()
                .any(|capture| matches!(capture, CaptureState::Running))
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Font staging has no active accepted capture",
            ));
        }
        if let Some(existing) = state
            .stages
            .iter()
            .find(|stage| stage.canonical_name == canonical_name)
        {
            if existing.sha256 != font.source.sha256 || existing.bytes != font.bytes.len() {
                return Err(backend(
                    "Retained font reuse differs from the selected CAS descriptor",
                ));
            }
            Self::validate_stage(existing, true)?;
            return Ok(existing.relative.clone());
        }
        // Reserve the slot BEFORE stage_new_retained can create anything. No
        // post-effect capacity failure may discard a returned error capsule.
        if state.stages.len() >= MAX_RETAINED_STAGES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Retained font stage budget is full",
            ));
        }
        let relative = format!("mw-{}-{}", self.id.simple(), canonical_name);
        state.stages.push(FontStage {
            canonical_name,
            relative: relative.clone(),
            sha256: font.source.sha256.clone(),
            bytes: font.bytes.len(),
            retained: None,
            instance_identity: None,
            observation_method: None,
            error: None,
        });
        let slot = state
            .stages
            .last_mut()
            .expect("slot reserved before creation");
        match self
            .root
            .stage_new_retained(Path::new(&relative), &font.bytes)
        {
            Ok(retained) => slot.retained = Some(retained),
            Err(RetainedStageError { error, retained }) => {
                // Do not observe, replace, unlink or return before custody owns
                // the exact partial/unsynced/published capsule, when supplied.
                slot.retained = retained;
                slot.error = Some(json!(&error));
                return Err(error);
            }
        }
        let observed = Self::validate_stage(slot, true).and_then(|observation| {
            self.validate_root()?;
            observation.ok_or_else(|| backend("Retained font observation was absent"))
        });
        match observed {
            Ok((identity, method, version)) => {
                slot.instance_identity = Some(identity);
                slot.observation_method = Some((method, version));
                Ok(relative)
            }
            Err(error) => {
                slot.error = Some(json!(&error));
                Err(error)
            }
        }
    }

    fn retire_known_before_capture(&self, close_owner: bool) -> bool {
        let Some(owner) = self.owner.upgrade() else {
            return false;
        };
        if owner.is_poisoned() || self.state.is_poisoned() {
            return false;
        }
        let Ok(mut owner) = owner.lock() else {
            return false;
        };
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        let belongs = owner
            .roots
            .get(&self.id)
            .is_some_and(|root| std::ptr::eq(root.as_ref(), self));
        if !belongs || state.retired {
            return false;
        }
        if close_owner {
            // No new guard, capture or stage may be accepted after this point.
            // Required slots with no guard are now provably never started.
            state.owner_closed = true;
            for work in state.work.values_mut() {
                if matches!(work, FontWorkState::Accepted) {
                    *work = FontWorkState::NotStarted;
                }
            }
        }
        let required = self.scope.invocation_kind.required();
        let all_known = state.work.len() == required.len()
            && required.iter().all(|kind| {
                matches!(
                    state.work.get(kind),
                    Some(FontWorkState::NotStarted | FontWorkState::Succeeded)
                        | Some(FontWorkState::Stopped {
                            outcome_known: Some(true),
                            ..
                        })
                )
            });
        // Empty capture/stage rosters are an invariant of these exact public
        // routes before their first native dispatch. They are checked under the
        // admission lock; a generic Err alone never proves absence of effects.
        if !state.owner_closed
            || !all_known
            || state.lifecycle_faulted
            || state.borrowers != 0
            || !state.captures.is_empty()
            || !state.stages.is_empty()
        {
            return false;
        }
        state.retired = true;
        state.retirement_reason = Some("known_before_capture");
        owner.roots.remove(&self.id);
        true
    }

    fn retire_after_owner_success(&self) -> bool {
        let Some(owner) = self.owner.upgrade() else {
            return false;
        };
        if owner.is_poisoned() || self.state.is_poisoned() {
            return false;
        }
        // The owner and snapshot use this same lock ordering.
        let Ok(mut owner) = owner.lock() else {
            return false;
        };
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        let belongs = owner
            .roots
            .get(&self.id)
            .is_some_and(|root| std::ptr::eq(root.as_ref(), self));
        let terminal = !state.captures.is_empty()
            && state
                .captures
                .values()
                .all(|capture| matches!(capture, CaptureState::CaptureTerminal));
        let required = self.scope.invocation_kind.required();
        let whole_owner = state.work.len() == required.len()
            && required
                .iter()
                .all(|kind| matches!(state.work.get(kind), Some(FontWorkState::Succeeded)))
            && state.borrowers == 0
            && !state.lifecycle_faulted
            && state.stages.iter().all(|stage| stage.error.is_none());
        let already_closed = state.owner_closed;
        // Consume owner admission under the same lock as the decision. Failure
        // retains this entry and cannot be healed by a later coordinator clone.
        state.owner_closed = true;
        if !belongs || state.retired || already_closed || !terminal || !whole_owner {
            state.retirement_refused = true;
            return false;
        }
        // Prevent reuse through coordinator clones before removing only THIS
        // invocation from owner custody. Other roots/scopes remain untouched.
        state.retired = true;
        state.retirement_reason = Some("successful_owner");
        owner.roots.remove(&self.id);
        true
    }

    fn snapshot(&self) -> Value {
        let state = retained_lock(&self.state);
        let work = state.work.iter().map(|(kind, work)| {
            match work {
                FontWorkState::Accepted => json!({"kind":kind.name(),"state":"accepted_not_started"}),
                FontWorkState::Queued => json!({"kind":kind.name(),"state":"queued_not_started"}),
                FontWorkState::NotStarted => json!({"kind":kind.name(),"state":"known_not_started"}),
                FontWorkState::Running => json!({"kind":kind.name(),"state":"running_or_dropped"}),
                FontWorkState::Succeeded => json!({"kind":kind.name(),"state":"succeeded"}),
                FontWorkState::Stopped { outcome_known, error } =>
                    json!({"kind":kind.name(),"state":"stopped","outcome_known":outcome_known,"original_error":error}),
            }
        }).collect::<Vec<_>>();
        let captures = state.captures.iter().map(|(prefix, capture)| {
            match capture {
                CaptureState::Running => json!({"prefix":prefix,"state":"running_or_dropped"}),
                CaptureState::CaptureTerminal => json!({"prefix":prefix,"state":"capture_terminal"}),
                CaptureState::Stopped { outcome_known, error } =>
                    json!({"prefix":prefix,"state":"stopped","outcome_known":outcome_known,"original_error":error}),
            }
        }).collect::<Vec<_>>();
        let stages = state.stages.iter().map(|stage| {
            let actual = stage.retained.as_ref().map(|capsule| {
                let observed = capsule.state();
                json!({"created":observed.created,"requested_bytes":observed.requested_bytes,
                    "confirmed_written_bytes":observed.confirmed_written_bytes,"partial":observed.partial,
                    "synced":observed.synced,"entry_verified":observed.entry_verified})
            });
            json!({"relative":stage.relative,"sha256":stage.sha256,"bytes":stage.bytes,
                "retained":stage.retained.is_some(),"state":actual,
                "instance_identity":stage.instance_identity,"observation_method":stage.observation_method,
                "original_error":stage.error})
        }).collect::<Vec<_>>();
        json!({"id":self.id,"project_id":self.scope.project_id,"expected":self.scope.expected,
            "request_id":self.scope.request_id,"connection_identity":self.connection_identity,
            "provenance_root":self.path,"root_instance_identity":self.root_identity,
            "retired":state.retired,"retirement_reason":state.retirement_reason,
            "owner_closed":state.owner_closed,
            "retirement_refused":state.retirement_refused,"lifecycle_faulted":state.lifecycle_faulted,
            "borrowers":state.borrowers,
            "accepted_owner_work":work,"captures":captures,"stages":stages})
    }
}

impl ProductionCoordinator {
    /// Consume the SAME root supplied by the backend owner. This method never
    /// opens path, derives a write grant from observe, or changes a Core mount.
    pub fn with_retained_brand_font_media_root(
        mut self,
        mut scope: ProductionFontStagingScope,
        path: PathBuf,
        root: Box<dyn ScopedRoot>,
        custody: &ProductionFontStagingCustody,
        kind: ProductionFontInvocationKind,
    ) -> NativeResult<(Self, ProductionFontStagingOwner)> {
        if self.brand_font_media_root.is_some() || self.retained_brand_font_root.is_some() {
            return Err(Error::new(
                ErrorCode::Conflict,
                "Brand font owner was already selected",
            ));
        }
        let project = self
            .service
            .project(scope.project_id)
            .map_err(storage_error)?;
        if RevisionStamp::from(&project) != scope.expected
            || self.client.connection().resource != scope.expected.resource
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Retained font owner scope is stale",
            ));
        }
        scope.invocation_kind = kind;
        let accepted = custody.accept(
            scope,
            self.client.connection().identity()?,
            path.clone(),
            root,
        )?;
        let owner = ProductionFontStagingOwner {
            id: accepted.id,
            root: accepted.clone(),
        };
        self.brand_font_media_root = Some(path);
        self.retained_brand_font_root = Some(accepted);
        Ok((self, owner))
    }

    // Synchronous acceptance runs when the public render Future is constructed,
    // before it can be queued, polled, spawned or dropped without a first poll.
    pub(super) fn accept_retained_font_render(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        kind: ProductionFontInvocationKind,
    ) -> NativeResult<Option<FontInvocationWork>> {
        self.retained_brand_font_root
            .as_ref()
            .map(|root| {
                root.accept_render(
                    project_id,
                    expected,
                    request_id,
                    &self.client.connection().identity()?,
                    kind,
                )
            })
            .transpose()
    }

    pub(super) fn accept_retained_font_window_cache(
        &self,
    ) -> NativeResult<Option<FontInvocationWork>> {
        self.retained_brand_font_root
            .as_ref()
            .map(|root| root.accept_work(FontWorkKind::WindowCache))
            .transpose()
    }

    pub(super) fn begin_retained_font_capture(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        prefix: &str,
    ) -> NativeResult<Option<FontCaptureCustody>> {
        self.retained_brand_font_root
            .as_ref()
            .map(|root| {
                root.begin(
                    project_id,
                    expected,
                    prefix,
                    &self.client.connection().identity()?,
                )
            })
            .transpose()
    }

    pub(super) fn revalidate_retained_brand_font_staging(&self) -> NativeResult<()> {
        self.retained_brand_font_root
            .as_ref()
            .map_or(Ok(()), |root| root.revalidate())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_service::BrandFontSource;
    use semwright_platform_api::filesystem::{
        RetainedStageResult, RetainedStageState, ScopedFileObservation,
    };
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    #[derive(Default)]
    struct Probe {
        mode: AtomicUsize,
        calls: AtomicUsize,
        created: AtomicUsize,
        stage_drops: AtomicUsize,
        root_drops: AtomicUsize,
        reads: AtomicUsize,
        writes: AtomicUsize,
        observations: AtomicUsize,
        root_version: AtomicUsize,
        entry_version: AtomicUsize,
        handoff_refused: AtomicBool,
    }

    struct MockRoot(Arc<Probe>);
    struct MockStage {
        probe: Arc<Probe>,
        bytes: Vec<u8>,
        state: RetainedStageState,
    }

    impl Drop for MockRoot {
        fn drop(&mut self) {
            self.0.root_drops.fetch_add(1, Ordering::SeqCst);
        }
    }
    impl Drop for MockStage {
        fn drop(&mut self) {
            self.probe.stage_drops.fetch_add(1, Ordering::SeqCst);
        }
    }
    impl ScopedRoot for MockRoot {
        fn root_instance_identity(&self) -> NativeResult<String> {
            Ok(format!(
                "mock-retained-root-{}",
                self.0.root_version.load(Ordering::SeqCst)
            ))
        }
        fn confinement(&self) -> Confinement {
            Confinement::WindowsHandleRelativeNoReparse
        }
        fn read(&self, _path: &Path, _limit: usize) -> NativeResult<Vec<u8>> {
            self.0.reads.fetch_add(1, Ordering::SeqCst);
            Err(Error::new(
                ErrorCode::PolicyDenied,
                "Synthetic root forbids path reads",
            ))
        }
        fn write_atomic(&self, _path: &Path, _bytes: &[u8]) -> NativeResult<()> {
            self.0.writes.fetch_add(1, Ordering::SeqCst);
            Err(Error::new(
                ErrorCode::PolicyDenied,
                "Synthetic root forbids replacements",
            ))
        }
        fn stage_new_retained(&self, _path: &Path, bytes: &[u8]) -> RetainedStageResult {
            self.0.calls.fetch_add(1, Ordering::SeqCst);
            let mode = self.0.mode.load(Ordering::SeqCst);
            if mode == 2 {
                return Err(RetainedStageError {
                    error: Error::new(ErrorCode::Conflict, "Synthetic no-replace collision"),
                    retained: None,
                });
            }
            self.0.created.fetch_add(1, Ordering::SeqCst);
            let actual = if mode == 1 {
                bytes[..3].to_vec()
            } else {
                let mut actual = bytes.to_vec();
                if mode == 3 {
                    actual[0] ^= 1;
                }
                actual
            };
            let stage = Box::new(MockStage {
                probe: self.0.clone(),
                state: RetainedStageState {
                    created: true,
                    requested_bytes: bytes.len() as u64,
                    confirmed_written_bytes: actual.len() as u64,
                    partial: actual.len() != bytes.len(),
                    synced: mode != 1 && mode != 4,
                    entry_verified: mode != 1,
                },
                bytes: actual,
            });
            if mode == 1 {
                return Err(RetainedStageError {
                    error: Error::new(ErrorCode::BackendFailed, "Synthetic partial write")
                        .uncertain(),
                    retained: Some(stage),
                });
            }
            Ok(stage)
        }
    }
    impl RetainedFileStage for MockStage {
        fn state(&self) -> RetainedStageState {
            self.state
        }
        fn observe(&self, limit: usize) -> NativeResult<ScopedFileObservation> {
            self.probe.observations.fetch_add(1, Ordering::SeqCst);
            if self.bytes.len() > limit {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "Synthetic read budget",
                ));
            }
            Ok(ScopedFileObservation {
                bytes: self.bytes.clone(),
                instance_identity: format!(
                    "mock-retained-file-{}",
                    self.probe.entry_version.load(Ordering::SeqCst)
                ),
                method: "synthetic-retained-capsule",
                method_version: 1,
            })
        }
        fn verify_entry(&self) -> NativeResult<()> {
            if self.probe.entry_version.load(Ordering::SeqCst) != 0 {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Synthetic entry replacement",
                ));
            }
            Ok(())
        }
        fn verify_handoff_path(&self) -> NativeResult<()> {
            if self.probe.handoff_refused.load(Ordering::SeqCst) {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Synthetic handoff replacement",
                ));
            }
            Ok(())
        }
    }
    fn font(index: usize) -> VerifiedBrandFont {
        // Synthetic service-shaped bytes; never parsed as a font or rendered.
        let bytes = format!("source-only retained capsule fixture {index}").into_bytes();
        VerifiedBrandFont {
            source: BrandFontSource {
                family: "Synthetic face".into(),
                asset_id: Uuid::new_v4(),
                sha256: hex::encode(Sha256::digest(&bytes)),
                media_type: "font/ttf".into(),
                source_revision: None,
            },
            bytes,
        }
    }
    fn accept(owner: &ProductionFontStagingCustody, probe: &Arc<Probe>) -> Arc<RetainedFontRoot> {
        let root = accept_pending(owner, probe, ProductionFontInvocationKind::Full);
        // The original R6 cases isolate capsule/capture behavior. Their shared
        // fixture explicitly completes synthetic owner work; it never renders.
        running_work(&root, FontWorkKind::FullRender).finish_native(&Ok(()));
        ProductionFontPostcheck {
            work: root.accept_work(FontWorkKind::FullPreview).unwrap(),
        }
        .run(|| ());
        root
    }

    fn accept_pending(
        owner: &ProductionFontStagingCustody,
        probe: &Arc<Probe>,
        kind: ProductionFontInvocationKind,
    ) -> Arc<RetainedFontRoot> {
        let project = Uuid::new_v4();
        let expected = RevisionStamp {
            resource: format!("project:{project}"),
            generation: Uuid::new_v4(),
            revision: 1,
        };
        let path = if cfg!(windows) {
            PathBuf::from(r"C:\synthetic-owner")
        } else {
            PathBuf::from("/synthetic-owner")
        };
        let mut scope =
            ProductionFontStagingScope::new(project, expected, "synthetic-capture").unwrap();
        scope.invocation_kind = kind;
        owner
            .accept(
                scope,
                "synthetic-connection".into(),
                path,
                Box::new(MockRoot(probe.clone())),
            )
            .unwrap()
    }
    fn begin(root: &Arc<RetainedFontRoot>, index: usize) -> NativeResult<FontCaptureCustody> {
        root.begin(
            root.scope.project_id,
            &root.scope.expected,
            &format!("synthetic-capture:motion:{index}"),
            "synthetic-connection",
        )
    }

    #[test]
    fn returned_partial_capsule_and_original_error_outlive_capture_drop_without_replay() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        probe.mode.store(1, Ordering::SeqCst);
        let root = accept(&owner, &probe);
        let capture = begin(&root, 0).unwrap();
        let error = root.stage(&font(0)).unwrap_err();
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert_eq!(error.message, "Synthetic partial write");
        assert!(!error.outcome_known);
        assert_eq!(probe.observations.load(Ordering::SeqCst), 0);
        capture.finish::<()>(&Err(error));
        assert!(begin(&root, 0).is_err());
        assert!(begin(&root, 1).is_err());
        assert!(!root.retire_after_owner_success());
        let snapshot = owner.private_snapshot();
        assert_eq!(
            snapshot["roots"][0]["stages"][0]["state"]["confirmed_written_bytes"],
            3
        );
        assert_eq!(
            snapshot["roots"][0]["stages"][0]["original_error"]["message"],
            "Synthetic partial write"
        );
        drop(root);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        assert_eq!(probe.root_drops.load(Ordering::SeqCst), 0);
        assert_eq!(probe.calls.load(Ordering::SeqCst), 1);
        drop(owner);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 1);
        assert_eq!(probe.root_drops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn no_replace_collision_never_borrows_an_existing_file_or_retries() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        probe.mode.store(2, Ordering::SeqCst);
        let root = accept(&owner, &probe);
        let capture = begin(&root, 0).unwrap();
        let error = root.stage(&font(0)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(error.outcome_known);
        capture.finish::<()>(&Err(error));
        assert!(begin(&root, 0).is_err());
        assert_eq!(probe.calls.load(Ordering::SeqCst), 1);
        assert_eq!(probe.created.load(Ordering::SeqCst), 0);
        assert_eq!(probe.reads.load(Ordering::SeqCst), 0);
        assert_eq!(probe.writes.load(Ordering::SeqCst), 0);
        assert_eq!(
            owner.private_snapshot()["roots"][0]["stages"][0]["retained"],
            false
        );
    }

    #[test]
    fn failed_byte_sync_or_handoff_validation_preserves_the_returned_capsule() {
        for mode in [0, 3, 4] {
            let owner = ProductionFontStagingCustody::default();
            let probe = Arc::new(Probe::default());
            probe.mode.store(mode, Ordering::SeqCst);
            probe.handoff_refused.store(mode == 0, Ordering::SeqCst);
            let root = accept(&owner, &probe);
            let capture = begin(&root, 0).unwrap();
            let result = root.stage(&font(mode));
            assert!(result.is_err());
            capture.finish(&result);
            drop(root);
            assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
            assert_eq!(
                owner.private_snapshot()["roots"][0]["stages"][0]["retained"],
                true
            );
            assert_eq!(probe.reads.load(Ordering::SeqCst), 0);
            assert_eq!(probe.writes.load(Ordering::SeqCst), 0);
            drop(owner);
            assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 1);
        }
    }

    #[test]
    fn root_and_stage_capacity_are_reserved_before_creation_without_eviction() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let mut roots = Vec::new();
        for _ in 0..MAX_RETAINED_ROOTS {
            roots.push(accept(&owner, &probe));
        }
        let project = Uuid::new_v4();
        let expected = RevisionStamp {
            resource: format!("project:{project}"),
            generation: Uuid::new_v4(),
            revision: 1,
        };
        let result = owner.accept(
            ProductionFontStagingScope::new(project, expected, "overflow").unwrap(),
            "synthetic-connection".into(),
            roots[0].path.clone(),
            Box::new(MockRoot(probe.clone())),
        );
        let Err(error) = result else {
            panic!("owner budget must refuse");
        };
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
        let root = &roots[0];
        let capture = begin(root, 0).unwrap();
        for index in 0..MAX_RETAINED_STAGES {
            root.stage(&font(index)).unwrap();
        }
        let error = root.stage(&font(MAX_RETAINED_STAGES)).unwrap_err();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        capture.finish::<()>(&Err(error));
        assert_eq!(probe.calls.load(Ordering::SeqCst), MAX_RETAINED_STAGES);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        assert_eq!(
            owner.private_snapshot()["roots"].as_array().unwrap().len(),
            MAX_RETAINED_ROOTS
        );
    }

    #[test]
    fn same_capsule_is_reused_between_segments_and_retirement_closes_that_invocation() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept(&owner, &probe);
        let capture = begin(&root, 0).unwrap();
        let first = root.stage(&font(0)).unwrap();
        assert_eq!(root.stage(&font(0)).unwrap(), first);
        capture.finish(&Ok(()));
        let capture = begin(&root, 1).unwrap();
        assert_eq!(root.stage(&font(0)).unwrap(), first);
        capture.finish(&Ok(()));
        assert_eq!(probe.calls.load(Ordering::SeqCst), 1);
        assert!(root.retire_after_owner_success());
        assert!(begin(&root, 2).is_err());
        assert!(root.stage(&font(1)).is_err());
        assert!(
            owner.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        drop(root);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 1);
        assert_eq!(probe.root_drops.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn cancellation_empty_scope_and_another_invocation_cannot_retire_active_custody() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let cancelled = accept(&owner, &probe);
        let done = accept(&owner, &probe);
        let unused = accept(&owner, &probe);
        assert!(!unused.retire_after_owner_success());
        let pending = begin(&cancelled, 0).unwrap();
        cancelled.stage(&font(0)).unwrap();
        drop(pending); // Equivalent custody path when an awaited future is dropped.
        let capture = begin(&done, 0).unwrap();
        done.stage(&font(1)).unwrap();
        capture.finish(&Ok(()));
        assert!(done.retire_after_owner_success());
        drop(done);
        assert!(!cancelled.retire_after_owner_success());
        assert!(begin(&cancelled, 1).is_err());
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 1);
        assert_eq!(
            owner.private_snapshot()["roots"].as_array().unwrap().len(),
            2
        );
    }

    #[test]
    fn changed_connection_stamp_root_or_entry_refuses_before_another_stage() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept(&owner, &probe);
        let prefix = "synthetic-capture:motion:0";
        assert!(
            root.begin(
                root.scope.project_id,
                &root.scope.expected,
                prefix,
                "different"
            )
            .is_err()
        );
        let mut stale = root.scope.expected.clone();
        stale.revision += 1;
        assert!(
            root.begin(
                root.scope.project_id,
                &stale,
                prefix,
                "synthetic-connection"
            )
            .is_err()
        );
        probe.root_version.store(1, Ordering::SeqCst);
        assert!(begin(&root, 0).is_err());
        assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
        probe.root_version.store(0, Ordering::SeqCst);
        let capture = begin(&root, 0).unwrap();
        root.stage(&font(0)).unwrap();
        probe.entry_version.store(1, Ordering::SeqCst);
        let result = root.stage(&font(1));
        assert!(result.is_err());
        capture.finish(&result);
        assert_eq!(probe.calls.load(Ordering::SeqCst), 1);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn poisoned_custody_retains_running_scope_and_cannot_promote_or_create() {
        let owner = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept(&owner, &probe);
        let capture = begin(&root, 0).unwrap();
        root.stage(&font(0)).unwrap();
        let held = root.clone();
        let _ = std::thread::spawn(move || {
            let _guard = held.state.lock().unwrap();
            panic!("synthetic poison after retained stage");
        })
        .join();
        capture.finish(&Ok(()));
        assert!(root.stage(&font(1)).is_err());
        assert!(!root.retire_after_owner_success());
        assert_eq!(
            owner.private_snapshot()["roots"][0]["captures"][0]["state"],
            "running_or_dropped"
        );
        assert_eq!(probe.calls.load(Ordering::SeqCst), 1);
        drop(root);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
    }

    fn running_work(root: &Arc<RetainedFontRoot>, kind: FontWorkKind) -> FontInvocationWork {
        let mut work = root.accept_work(kind).unwrap();
        work.start_native().unwrap();
        work
    }

    fn invocation_owner(root: &Arc<RetainedFontRoot>) -> ProductionFontStagingOwner {
        ProductionFontStagingOwner {
            id: root.id,
            root: root.clone(),
        }
    }

    fn capture_component(root: &Arc<RetainedFontRoot>) {
        // Synthetic retained capsule only. No font decoder, Host or renderer.
        let capture = begin(root, 0).unwrap();
        root.stage(&font(0)).unwrap();
        capture.finish(&Ok(()));
    }

    #[cfg(unix)]
    struct QueuedFixture {
        _directory: tempfile::TempDir,
        service: StudioService,
        project: motionwright_domain::Project,
        coordinator: ProductionCoordinator,
        owner: ProductionFontStagingOwner,
        custody: ProductionFontStagingCustody,
        probe: Arc<Probe>,
        counter: PathBuf,
    }

    #[cfg(unix)]
    fn queued_fixture(kind: ProductionFontInvocationKind) -> QueuedFixture {
        let directory = tempfile::tempdir().unwrap();
        let service = StudioService::open(directory.path().join("owner.sqlite3")).unwrap();
        let project = service
            .create_project("Queued owner source fixture")
            .unwrap();
        let (connection, counter) = super::super::tests::fake_transport_connection(
            &directory,
            project.resource_key(),
            "never invoked",
            "",
            0,
        );
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let scope = ProductionFontStagingScope::new(
            project.id,
            RevisionStamp::from(&project),
            "queued-font-owner",
        )
        .unwrap();
        let (coordinator, owner) = ProductionCoordinator::new(service.clone(), connection)
            .unwrap()
            .with_retained_brand_font_media_root(
                scope,
                directory.path().to_path_buf(),
                Box::new(MockRoot(probe.clone())),
                &custody,
                kind,
            )
            .unwrap();
        QueuedFixture {
            _directory: directory,
            service,
            project,
            coordinator,
            owner,
            custody,
            probe,
            counter,
        }
    }

    #[cfg(unix)]
    fn require_ready_error<T>(future: impl std::future::Future<Output = NativeResult<T>>) -> Error {
        let mut future = std::pin::pin!(future);
        let waker = std::task::Waker::noop();
        let mut context = std::task::Context::from_waker(waker);
        let std::task::Poll::Ready(Err(error)) =
            std::future::Future::poll(future.as_mut(), &mut context)
        else {
            panic!("admission must fail before inner future work");
        };
        error
    }

    #[cfg(unix)]
    #[test]
    fn queued_public_render_is_counted_without_first_poll_and_drop_never_completes() {
        fn drop_unpolled(future: impl std::future::Future + Send, root: &Arc<RetainedFontRoot>) {
            assert_eq!(retained_lock(&root.state).borrowers, 1);
            assert!(matches!(
                retained_lock(&root.state)
                    .work
                    .get(&root.scope.invocation_kind.render()),
                Some(FontWorkState::Queued)
            ));
            drop(future);
            assert_eq!(retained_lock(&root.state).borrowers, 0);
            assert!(matches!(
                retained_lock(&root.state)
                    .work
                    .get(&root.scope.invocation_kind.render()),
                Some(FontWorkState::NotStarted)
            ));
        }
        for kind in [
            ProductionFontInvocationKind::Full,
            ProductionFontInvocationKind::Window,
        ] {
            let QueuedFixture {
                _directory,
                service,
                project,
                coordinator,
                owner,
                custody,
                probe,
                counter,
            } = queued_fixture(kind);
            let expected = RevisionStamp::from(&project);
            let options = FilmBuildOptions::default();
            let request = MotionCanvasWindowRequest {
                deliverable_id: project.deliverables[0].id,
                source_scene_id: Uuid::new_v4(),
                source_window: motionwright_domain::SourceFrameWindow { start: 0, end: 1 },
                options: options.clone(),
            };
            let root = coordinator.retained_brand_font_root.as_ref().unwrap();
            match kind {
                ProductionFontInvocationKind::Full => drop_unpolled(
                    coordinator.render_motion_canvas_segments(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        project.deliverables[0].id,
                        &options,
                    ),
                    root,
                ),
                ProductionFontInvocationKind::Window => drop_unpolled(
                    coordinator.render_motion_canvas_window(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        &request,
                    ),
                    root,
                ),
            }
            // A coordinator clone cannot heal the dropped accepted invocation.
            let clone = coordinator.clone();
            let error = match kind {
                ProductionFontInvocationKind::Full => {
                    require_ready_error(clone.render_motion_canvas_segments(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        project.deliverables[0].id,
                        &options,
                    ))
                }
                ProductionFontInvocationKind::Window => {
                    require_ready_error(clone.render_motion_canvas_window(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        &request,
                    ))
                }
            };
            assert_eq!(error.code, ErrorCode::Conflict);
            assert!(!owner.retire_after_success(&coordinator));
            assert!(
                custody.private_snapshot()["roots"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
            assert_eq!(
                retained_lock(&root.state).retirement_reason,
                Some("known_before_capture")
            );
            assert_eq!(
                service
                    .production_receipt_watermark(project.id)
                    .unwrap()
                    .count,
                0
            );
            assert!(!counter.exists());
            assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
        }
    }

    #[test]
    fn completed_internal_capture_does_not_complete_the_pending_owner_postcheck() {
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
        let owner = invocation_owner(&root);
        let render = running_work(&root, FontWorkKind::FullRender);
        capture_component(&root);
        render.finish_native(&Ok(()));
        assert!(matches!(
            retained_lock(&root.state).captures.values().next(),
            Some(CaptureState::CaptureTerminal)
        ));
        assert!(matches!(
            retained_lock(&root.state)
                .work
                .get(&FontWorkKind::FullPreview),
            Some(FontWorkState::Accepted)
        ));
        assert!(!owner.retire_for_root(&root));
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        assert_eq!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn owner_from_another_entry_cannot_retire_either_invocation() {
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let first = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
        let second = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
        let mut first_owner = invocation_owner(&first);
        let mut second_owner = invocation_owner(&second);
        for (root, owner) in [(&first, &mut first_owner), (&second, &mut second_owner)] {
            let render = running_work(root, FontWorkKind::FullRender);
            let preview = owner.accept_full_preview().unwrap();
            capture_component(root);
            render.finish_native(&Ok(()));
            assert_eq!(preview.run(|| 7), 7);
        }
        assert!(!first_owner.retire_for_root(&second));
        assert_eq!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert!(second_owner.retire_for_root(&second));
        assert_eq!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(!retained_lock(&first.state).retired);
    }

    #[test]
    fn unfinished_background_worker_prevents_retirement_and_late_success_does_not_heal_it() {
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Window);
        let mut owner = invocation_owner(&root);
        let render = running_work(&root, FontWorkKind::WindowRender);
        let registry = owner.accept_window_registry().unwrap();
        let currency = owner.accept_window_currency().unwrap();
        let mut cache = root.accept_work(FontWorkKind::WindowCache).unwrap();
        capture_component(&root);
        render.finish_native(&Ok(()));
        registry.run_result(|| Ok::<_, Error>(())).unwrap();
        currency.run_result(|| Ok::<_, Error>(())).unwrap();
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let worker = std::thread::spawn(move || {
            cache.start_native().unwrap();
            started_tx.send(()).unwrap();
            let result = release_rx
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| backend("Synthetic worker release was not observed"));
            cache.finish_native(&result);
            result
        });
        started_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert_eq!(retained_lock(&root.state).borrowers, 1);
        assert!(!owner.retire_for_root(&root));
        release_tx.send(()).unwrap();
        worker.join().unwrap().unwrap();
        assert_eq!(retained_lock(&root.state).borrowers, 0);
        assert!(!root.retire_after_owner_success());
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        assert_eq!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }

    #[test]
    fn completed_window_workers_and_actual_postchecks_allow_only_their_owner_to_retire() {
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Window);
        let mut owner = invocation_owner(&root);
        let render = running_work(&root, FontWorkKind::WindowRender);
        let registry = owner.accept_window_registry().unwrap();
        let currency = owner.accept_window_currency().unwrap();
        let mut cache = root.accept_work(FontWorkKind::WindowCache).unwrap();
        capture_component(&root);
        std::thread::spawn(move || {
            cache.start_native().unwrap();
            let result = Ok::<_, Error>(());
            cache.finish_native(&result);
            result
        })
        .join()
        .unwrap()
        .unwrap();
        render.finish_native(&Ok(()));
        let observed = Arc::new(AtomicUsize::new(0));
        registry
            .run_result(|| {
                observed.fetch_add(1, Ordering::SeqCst);
                Ok::<_, Error>(())
            })
            .unwrap();
        currency
            .run_result(|| {
                observed.fetch_add(1, Ordering::SeqCst);
                Ok::<_, Error>(())
            })
            .unwrap();
        assert_eq!(observed.load(Ordering::SeqCst), 2);
        assert_eq!(retained_lock(&root.state).borrowers, 0);
        assert!(owner.retire_for_root(&root));
        assert!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(root.accept_work(FontWorkKind::WindowCache).is_err());
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        drop(root);
        assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 1);
        assert_eq!(probe.root_drops.load(Ordering::SeqCst), 1);
    }

    #[cfg(unix)]
    #[test]
    fn coordinator_clone_cannot_admit_a_new_future_after_owner_retirement() {
        let QueuedFixture {
            _directory,
            service,
            project,
            coordinator,
            mut owner,
            custody,
            probe,
            counter,
        } = queued_fixture(ProductionFontInvocationKind::Full);
        let root = coordinator.retained_brand_font_root.as_ref().unwrap();
        // Component completion only; no native success, receipt or output is fabricated.
        let render = running_work(root, FontWorkKind::FullRender);
        let preview = owner.accept_full_preview().unwrap();
        let capture = root
            .begin(
                project.id,
                &RevisionStamp::from(&project),
                "queued-font-owner:motion:0",
                &coordinator.client.connection().identity().unwrap(),
            )
            .unwrap();
        capture.finish(&Ok(()));
        render.finish_native(&Ok(()));
        preview.run(|| ());
        let clone = coordinator.clone();
        assert!(owner.retire_after_success(&coordinator));
        let expected = RevisionStamp::from(&project);
        let options = FilmBuildOptions::default();
        let error = require_ready_error(clone.render_motion_canvas_segments(
            project.id,
            &expected,
            "queued-font-owner",
            project.deliverables[0].id,
            &options,
        ));
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
        assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
        assert!(!counter.exists());
    }

    #[test]
    fn whole_render_known_and_unknown_errors_keep_the_original_and_never_retire() {
        for outcome_known in [true, false] {
            let custody = ProductionFontStagingCustody::default();
            let probe = Arc::new(Probe::default());
            let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
            let mut owner = invocation_owner(&root);
            let render = running_work(&root, FontWorkKind::FullRender);
            let preview = owner.accept_full_preview().unwrap();
            capture_component(&root);
            let mut original = backend("Original synthetic whole-render rejection");
            original.outcome_known = outcome_known;
            let expected = json!(&original);
            let result: NativeResult<()> = Err(original);
            render.finish_native(&result);
            preview.run(|| ());
            let snapshot = custody.private_snapshot();
            let work = snapshot["roots"][0]["accepted_owner_work"]
                .as_array()
                .unwrap()
                .iter()
                .find(|entry| entry["kind"] == "full_render")
                .unwrap();
            assert_eq!(work["state"], "stopped");
            assert_eq!(work["outcome_known"], outcome_known);
            assert_eq!(work["original_error"], expected);
            assert_eq!(json!(result.unwrap_err()), expected);
            assert!(!owner.retire_for_root(&root));
            assert_eq!(probe.stage_drops.load(Ordering::SeqCst), 0);
        }
    }

    #[cfg(unix)]
    #[test]
    fn queued_future_after_owner_close_refuses_before_capture_then_releases_known_entry() {
        fn close_then_poll<T>(
            future: impl std::future::Future<Output = NativeResult<T>>,
            coordinator: &ProductionCoordinator,
            owner: ProductionFontStagingOwner,
            custody: &ProductionFontStagingCustody,
        ) {
            let root = coordinator.retained_brand_font_root.as_ref().unwrap();
            drop(owner);
            assert!(retained_lock(&root.state).owner_closed);
            assert!(!retained_lock(&root.state).retired);
            assert_eq!(retained_lock(&root.state).borrowers, 1);
            assert_eq!(
                custody.private_snapshot()["roots"]
                    .as_array()
                    .unwrap()
                    .len(),
                1
            );
            assert!(
                root.accept_work(root.scope.invocation_kind.render())
                    .is_err()
            );
            let prefix = format!("{}:motion:0", root.scope.request_id);
            assert!(
                root.begin(
                    root.scope.project_id,
                    &root.scope.expected,
                    &prefix,
                    &coordinator.client.connection().identity().unwrap()
                )
                .is_err()
            );
            assert!(root.stage(&font(0)).is_err());
            let error = require_ready_error(future);
            assert_eq!(error.code, ErrorCode::Conflict);
            assert!(error.outcome_known);
            assert_eq!(
                error.message,
                "Retained font owner closed before queued native work started"
            );
            let state = retained_lock(&root.state);
            assert!(state.retired);
            assert_eq!(state.retirement_reason, Some("known_before_capture"));
            assert!(state.captures.is_empty() && state.stages.is_empty());
            assert_eq!(state.borrowers, 0);
            drop(state);
            assert!(
                custody.private_snapshot()["roots"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
        }
        for kind in [
            ProductionFontInvocationKind::Full,
            ProductionFontInvocationKind::Window,
        ] {
            let QueuedFixture {
                _directory,
                service,
                project,
                coordinator,
                owner,
                custody,
                probe,
                counter,
            } = queued_fixture(kind);
            let expected = RevisionStamp::from(&project);
            let options = FilmBuildOptions::default();
            let request = MotionCanvasWindowRequest {
                deliverable_id: project.deliverables[0].id,
                source_scene_id: Uuid::new_v4(),
                source_window: motionwright_domain::SourceFrameWindow { start: 0, end: 1 },
                options: options.clone(),
            };
            match kind {
                ProductionFontInvocationKind::Full => close_then_poll(
                    coordinator.render_motion_canvas_segments(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        project.deliverables[0].id,
                        &options,
                    ),
                    &coordinator,
                    owner,
                    &custody,
                ),
                ProductionFontInvocationKind::Window => close_then_poll(
                    coordinator.render_motion_canvas_window(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        &request,
                    ),
                    &coordinator,
                    owner,
                    &custody,
                ),
            }
            assert_eq!(
                service
                    .production_receipt_watermark(project.id)
                    .unwrap()
                    .count,
                0
            );
            assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
            assert!(!counter.exists());
        }
    }

    #[cfg(unix)]
    #[test]
    fn real_local_admission_error_preserves_original_and_closes_without_capture_or_dispatch() {
        for kind in [
            ProductionFontInvocationKind::Full,
            ProductionFontInvocationKind::Window,
        ] {
            let QueuedFixture {
                _directory,
                service,
                project,
                coordinator,
                owner,
                custody,
                probe,
                counter,
            } = queued_fixture(kind);
            let expected = RevisionStamp::from(&project);
            let options = FilmBuildOptions::default();
            let absent_deliverable = Uuid::new_v4();
            assert!(
                project
                    .deliverables
                    .iter()
                    .all(|profile| profile.id != absent_deliverable)
            );
            let request = MotionCanvasWindowRequest {
                deliverable_id: absent_deliverable,
                source_scene_id: Uuid::new_v4(),
                source_window: motionwright_domain::SourceFrameWindow { start: 0, end: 1 },
                options: options.clone(),
            };
            let original = match kind {
                ProductionFontInvocationKind::Full => {
                    require_ready_error(coordinator.render_motion_canvas_segments(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        absent_deliverable,
                        &options,
                    ))
                }
                ProductionFontInvocationKind::Window => {
                    require_ready_error(coordinator.render_motion_canvas_window(
                        project.id,
                        &expected,
                        "queued-font-owner",
                        &request,
                    ))
                }
            };
            assert!(original.outcome_known);
            let exact = json!(&original);
            let root = coordinator.retained_brand_font_root.as_ref().unwrap();
            let snapshot = custody.private_snapshot();
            let work = snapshot["roots"][0]["accepted_owner_work"]
                .as_array()
                .unwrap()
                .iter()
                .find(|item| item["kind"] == kind.render().name())
                .unwrap();
            assert_eq!(work["state"], "stopped");
            assert_eq!(work["original_error"], exact);
            assert!(retained_lock(&root.state).captures.is_empty());
            drop(owner);
            assert_eq!(
                retained_lock(&root.state).retirement_reason,
                Some("known_before_capture")
            );
            assert_eq!(json!(original), exact);
            assert!(
                custody.private_snapshot()["roots"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
            assert_eq!(
                service
                    .production_receipt_watermark(project.id)
                    .unwrap()
                    .count,
                0
            );
            assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
            assert!(!counter.exists());
        }
    }

    #[test]
    fn known_unstarted_owner_closure_does_not_exhaust_the_bounded_root_capacity() {
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        for _ in 0..MAX_RETAINED_ROOTS + 1 {
            let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
            let mut owner = invocation_owner(&root);
            let render = root.accept_work(FontWorkKind::FullRender).unwrap();
            let preview = owner.accept_full_preview().unwrap();
            drop(owner); // Existing guards still make this entry non-retirable.
            assert_eq!(retained_lock(&root.state).borrowers, 2);
            assert!(!retained_lock(&root.state).retired);
            drop(render);
            assert!(!retained_lock(&root.state).retired);
            drop(preview);
            assert_eq!(
                retained_lock(&root.state).retirement_reason,
                Some("known_before_capture")
            );
            assert!(
                custody.private_snapshot()["roots"]
                    .as_array()
                    .unwrap()
                    .is_empty()
            );
            assert!(root.accept_work(FontWorkKind::FullRender).is_err());
        }
        assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            probe.root_drops.load(Ordering::SeqCst),
            MAX_RETAINED_ROOTS + 1
        );
    }

    #[test]
    fn running_drop_panic_and_unknown_error_remain_retained_even_without_a_capture() {
        for mode in 0..3 {
            let custody = ProductionFontStagingCustody::default();
            let probe = Arc::new(Probe::default());
            let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
            let owner = invocation_owner(&root);
            let work = running_work(&root, FontWorkKind::FullRender);
            if mode == 0 {
                drop(work);
            } else if mode == 1 {
                work.finish_native::<()>(&Err(backend("Original synthetic UNKNOWN").uncertain()));
            } else {
                assert!(
                    std::thread::spawn(move || {
                        let _held = work;
                        panic!("Synthetic panic after the work actually started");
                    })
                    .join()
                    .is_err()
                );
            }
            drop(owner);
            let state = retained_lock(&root.state);
            assert!(state.owner_closed);
            assert!(!state.retired);
            assert_eq!(state.borrowers, 0);
            assert!(state.captures.is_empty() && state.stages.is_empty());
            if mode == 1 {
                assert!(matches!(
                    state.work.get(&FontWorkKind::FullRender),
                    Some(FontWorkState::Stopped {
                        outcome_known: Some(false),
                        ..
                    })
                ));
            } else {
                assert!(matches!(
                    state.work.get(&FontWorkKind::FullRender),
                    Some(FontWorkState::Running)
                ));
            }
            drop(state);
            assert_eq!(
                custody.private_snapshot()["roots"]
                    .as_array()
                    .unwrap()
                    .len(),
                1
            );
            assert_eq!(probe.calls.load(Ordering::SeqCst), 0);
            assert_eq!(probe.root_drops.load(Ordering::SeqCst), 0);
        }
    }

    #[test]
    fn known_error_after_capture_admission_does_not_use_no_capture_retirement() {
        let custody = ProductionFontStagingCustody::default();
        let probe = Arc::new(Probe::default());
        let root = accept_pending(&custody, &probe, ProductionFontInvocationKind::Full);
        let owner = invocation_owner(&root);
        let render = running_work(&root, FontWorkKind::FullRender);
        let capture = begin(&root, 0).unwrap();
        let original = backend("Synthetic known failure after capture acceptance");
        let result: NativeResult<()> = Err(original);
        capture.finish(&result);
        render.finish_native(&result);
        drop(owner);
        let state = retained_lock(&root.state);
        assert!(state.owner_closed);
        assert!(!state.retired);
        assert_eq!(state.borrowers, 0);
        assert!(!state.captures.is_empty());
        drop(state);
        assert_eq!(
            custody.private_snapshot()["roots"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            result.unwrap_err().message,
            "Synthetic known failure after capture acceptance"
        );
        assert_eq!(probe.root_drops.load(Ordering::SeqCst), 0);
    }
}
