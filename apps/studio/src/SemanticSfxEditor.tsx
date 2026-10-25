import { Component, useCallback, useEffect, useId, useState, type ReactNode } from "react";
import type { Change, Project } from "./types";
import { validateSemanticSfx } from "./semanticSfx";
import {
  readSfxCatalogue,
  sfxScope,
  type SfxCue,
  type SfxRaw,
  type SfxRecoveryEntry,
} from "./semanticSfxDraft";
import { sfxLiveTargets, useSemanticSfxDraft } from "./useSemanticSfxDraft";
import "./semantic-sfx-editor.css";

type Props = {
  project: Project;
  commit: (change: Change) => Promise<Project>;
  onSeek: (seconds: number) => void;
};

type Option = { value: string; label: string };
type Catalogue = {
  entries: SfxRecoveryEntry[];
  limited: boolean;
  error: string | null;
};

// A tab-local choice survives Audio navigation. The draft hook owns recovery.
const selectedByOwner = new Map<string, string>();

const eventOptions: Option[] = [
  { value: "scene_reveal", label: "Scene reveal" },
  { value: "beat_emphasis", label: "Beat emphasis" },
  { value: "ui_focus", label: "UI focus" },
  { value: "confirmation", label: "Confirmation" },
  { value: "impact", label: "Impact" },
  { value: "transition", label: "Transition" },
];
const anchorOptions: Option[] = [
  { value: "scene_start", label: "Scene start" },
  { value: "beat_start", label: "Beat start" },
];
const tailOptions: Option[] = [
  { value: "within_scene", label: "End within source scene" },
  { value: "continue_across_cuts", label: "Continue across cuts" },
];
const numericFields: { field: keyof SfxRaw; label: string }[] = [
  { field: "offsetFrames", label: "Offset frames" },
  { field: "sourceFrames", label: "Source frames" },
  { field: "gainMilliDb", label: "Gain milli dB" },
  { field: "attackFrames", label: "Attack frames" },
  { field: "releaseFrames", label: "Release frames" },
];
const comparisonFields: { field: keyof SfxRaw; label: string }[] = [
  { field: "sceneId", label: "Source scene ID" },
  { field: "event", label: "Semantic event" },
  { field: "anchorKind", label: "Event anchor" },
  { field: "beatId", label: "Source beat ID" },
  { field: "sourceAssetId", label: "WAV asset ID" },
  { field: "sourceSha256", label: "WAV SHA-256" },
  ...numericFields,
  { field: "tailPolicy", label: "Tail policy" },
  { field: "attestation", label: "Original source declaration" },
];

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function eventLabel(value: string): string {
  return eventOptions.find((option) => option.value === value)?.label || value || "Unspecified event";
}

function cueRaw(cue: SfxCue): SfxRaw {
  return {
    sceneId: cue.scene_id,
    event: cue.event,
    anchorKind: cue.anchor.kind,
    beatId: cue.anchor.kind === "beat_start" ? cue.anchor.beat_id : "",
    sourceAssetId: cue.source_asset_id,
    sourceSha256: cue.source_sha256,
    offsetFrames: String(cue.offset_frames),
    sourceFrames: String(cue.source_frames),
    gainMilliDb: String(cue.gain_milli_db),
    attackFrames: String(cue.attack_frames),
    releaseFrames: String(cue.release_frames),
    tailPolicy: cue.tail_policy ?? "within_scene",
    attestation: cue.original_source_attestation,
  };
}

function catalogueFor(project: Project): Catalogue {
  try {
    if (typeof window === "undefined") {
      return { entries: [], limited: false, error: "Recovery storage is not available in this view." };
    }
    return { ...readSfxCatalogue(window.localStorage, project), error: null };
  } catch (error) {
    return { entries: [], limited: false, error: message(error) };
  }
}

function retainedCopies(
  catalogue: SfxRecoveryEntry[],
  sessionCopies: SfxRecoveryEntry[],
): SfxRecoveryEntry[] {
  const byKey = new Map<string, SfxRecoveryEntry>();
  for (const copy of [...catalogue, ...sessionCopies]) byKey.set(copy.key, copy);
  return [...byKey.values()];
}

function RawOptions({
  value,
  options,
  placeholder,
}: {
  value: string;
  options: Option[];
  placeholder?: string;
}) {
  return (
    <>
      {placeholder && <option value="">{placeholder}</option>}
      {!placeholder && value === "" && <option value="">Unspecified</option>}
      {value !== "" && !options.some((option) => option.value === value) && (
        <option value={value}>{value} (unavailable)</option>
      )}
      {options.map((option) => (
        <option key={option.value} value={option.value}>{option.label}</option>
      ))}
    </>
  );
}

function Comparison({
  base,
  saved,
  raw,
  baseRevision,
  savedRevision,
}: {
  base: SfxCue | null;
  saved: SfxCue | null;
  raw: SfxRaw;
  baseRevision: number;
  savedRevision: number;
}) {
  const baseRaw = base ? cueRaw(base) : null;
  const savedRaw = saved ? cueRaw(saved) : null;
  const value = (source: SfxRaw | null, field: keyof SfxRaw, missing: string) =>
    source ? source[field] === "" ? "(empty)" : source[field] : missing;
  return (
    <div className="sfx-comparison-scroll" tabIndex={0} role="region" aria-label="Sound source comparison">
      <table className="sfx-comparison">
        <caption>Exact values in the edit base, current saved project and proposed edit.</caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Base · revision {baseRevision}</th>
            <th scope="col">Saved · revision {savedRevision}</th>
            <th scope="col">Proposed edit</th>
          </tr>
        </thead>
        <tbody>
          {comparisonFields.map(({ field, label }) => (
            <tr key={field}>
              <th scope="row">{label}</th>
              <td>{value(baseRaw, field, "No base event")}</td>
              <td>{value(savedRaw, field, "No saved event")}</td>
              <td>{value(raw, field, "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SemanticSfxEditor(props: Props) {
  return <SfxOwnerEditor key={sfxScope(props.project, "new")} {...props} />;
}

function SfxOwnerEditor({ project, commit, onSeek }: Props) {
  const owner = sfxScope(project, "new");
  const editorId = useId();
  const [target, setTarget] = useState(() => selectedByOwner.get(owner) ?? "new");
  const [catalogue, setCatalogue] = useState(() => catalogueFor(project));
  const [seekError, setSeekError] = useState<string | null>(null);
  const refreshCatalogue = useCallback(() => setCatalogue(previous => {
    const next = catalogueFor(project);
    // A failed reread must not erase bytes already available for export.
    return next.error ? { ...previous, error: next.error } : next;
  }), [project]);

  useEffect(() => {
    refreshCatalogue();
    window.addEventListener("storage", refreshCatalogue);
    window.addEventListener("motionwright-sfx-draft", refreshCatalogue);
    return () => {
      window.removeEventListener("storage", refreshCatalogue);
      window.removeEventListener("motionwright-sfx-draft", refreshCatalogue);
    };
  }, [refreshCatalogue]);

  function select(next: string) {
    selectedByOwner.set(owner, next);
    setTarget(next);
    setSeekError(null);
  }

  const savedCues = project.audio.sfx_cues ?? [];
  const liveTargets = sfxLiveTargets(project);
  const catalogueTargets = catalogue.entries.flatMap((copy) =>
    copy.value && !copy.error ? [copy.value.draft.target] : []);
  const targets = [...new Set([
    ...savedCues.map((cue) => cue.id),
    ...liveTargets,
    ...catalogueTargets,
    target,
  ])].filter((id) => id !== "new");
  const canOpenDraft = target === "new" || savedCues.some((cue) => cue.id === target) ||
    liveTargets.includes(target) || catalogueTargets.includes(target);

  return (
    <section className="audio-editor-section semantic-sfx-editor" aria-labelledby={editorId + "-title"}>
      <div className="section-title-row sfx-section-heading">
        <div>
          <h3 id={editorId + "-title"}>Sound events</h3>
          <p className="sfx-help">Place an imported WAV at a scene or beat anchor.</p>
        </div>
        <button className="button" type="button" onClick={() => select("new")}
          aria-pressed={target === "new"} aria-controls={editorId + "-workspace"}>
          New sound event
        </button>
      </div>
      <div className="sfx-editor-layout">
        <aside className="sfx-sidebar" aria-label="Saved sounds and recovered edits">
          <p className="sfx-list-heading">{savedCues.length} saved sound {savedCues.length === 1 ? "event" : "events"}</p>
          {targets.length === 0 && <p className="sfx-help">Your saved events and retained edits will appear here.</p>}
          <ul className="sfx-target-list">
            {targets.map((id) => {
              const cue = savedCues.find((item) => item.id === id);
              const copy = catalogue.entries.find((entry) => !entry.error && entry.value?.draft.target === id);
              const sceneId = cue?.scene_id ?? copy?.value?.draft.raw.sceneId;
              const sceneName = project.scenes.find((scene) => scene.id === sceneId)?.name;
              const label = cue ? eventLabel(cue.event) : "Retained sound edit";
              let startFrame: number | null = null;
              let placementError: string | null = null;
              if (cue) {
                try {
                  startFrame = validateSemanticSfx(project, cue).absolute_start_frame;
                } catch (error) {
                  placementError = message(error);
                }
              }
              const sourceFrame = startFrame;
              return (
                <li key={id} className="sfx-target">
                  <button className="button sfx-target-button" type="button"
                    onClick={() => select(id)} aria-pressed={target === id}
                    aria-controls={editorId + "-workspace"}>
                    <span className="sfx-target-label">{label}</span>
                    <span className="sfx-help">{sceneName || sceneId || "Source scene unavailable"}</span>
                    <span className="sfx-id">{id}</span>
                    {!cue && <span className="sfx-help">No saved event with this ID</span>}
                    {target === id && <span className="sfx-selected-label">Selected</span>}
                  </button>
                  {cue && (
                    <div className="sfx-target-actions">
                      <button className="button compact" type="button"
                        aria-label={"Seek to sound event " + id}
                        disabled={sourceFrame === null}
                        onClick={() => {
                          if (sourceFrame === null) return;
                          try {
                            onSeek(sourceFrame / 30);
                            setSeekError(null);
                          } catch (error) {
                            setSeekError(message(error));
                          }
                        }}>
                        Seek
                      </button>
                      {sourceFrame !== null && <span className="sfx-help">Source frame {sourceFrame}</span>}
                    </div>
                  )}
                  {placementError && <p className="sfx-error">{placementError}</p>}
                </li>
              );
            })}
          </ul>
          <p className="sfx-help">Seek moves to the saved event in the source timeline.</p>
          {seekError && <p className="sfx-error" role="alert">{seekError}</p>}
        </aside>
        <div id={editorId + "-workspace"} className="sfx-workspace">
          {canOpenDraft ? (
            <SfxOpeningBoundary key={sfxScope(project, target)} target={target}
              catalogue={catalogue} select={select} refreshCatalogue={refreshCatalogue}>
              <SfxDraftEditor project={project} commit={commit}
                target={target} catalogue={catalogue} select={select} refreshCatalogue={refreshCatalogue} />
            </SfxOpeningBoundary>
          ) : (
            <UnavailableSfxDraft key={sfxScope(project, target)} target={target}
              catalogue={catalogue} select={select} refreshCatalogue={refreshCatalogue} />
          )}
        </div>
      </div>
    </section>
  );
}

class SfxOpeningBoundary extends Component<{
  children: ReactNode; target: string; catalogue: Catalogue;
  select: (target: string) => void; refreshCatalogue: () => void;
}, { error: string | null }> {
  state: { error: string | null } = { error: null };
  static getDerivedStateFromError(reason: unknown) { return { error: message(reason) }; }
  render() {
    if (this.state.error === null) return this.props.children;
    return <UnavailableSfxDraft target={this.props.target} catalogue={this.props.catalogue}
      select={this.props.select} openingError={this.state.error} refreshCatalogue={() => {
        this.props.refreshCatalogue();
        // Only an explicit refresh or a different owner/target retries opening.
        this.setState({ error: null });
      }} />;
  }
}

function UnavailableSfxDraft({ target, catalogue, select, refreshCatalogue, openingError }: {
  target: string;
  catalogue: Catalogue;
  select: (target: string) => void;
  refreshCatalogue: () => void;
  openingError?: string;
}) {
  const id = useId();
  const [exportError, setExportError] = useState<string | null>(null);

  function exportCopy(copy: SfxRecoveryEntry) {
    setExportError(null);
    try {
      const url = URL.createObjectURL(new Blob([copy.raw], { type: "application/json;charset=utf-8" }));
      const link = document.createElement("a");
      try {
        link.href = url;
        link.download = "motionwright-sound-recovery-" + copy.writerId.replace(/[^a-zA-Z0-9-]/g, "_") + ".json";
        document.body.append(link);
        link.click();
      } finally {
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
      }
    } catch (error) {
      setExportError(message(error));
    }
  }

  return (
    <section className="sfx-draft" aria-labelledby={id + "-heading"}>
      <h4 id={id + "-heading"}>Retained sound edit</h4>
      <p className="sfx-id">{target}</p>
      {openingError && <p className="sfx-error" role="alert">{openingError}</p>}
      {(openingError || catalogue.error) && <p className="sfx-help">
        Previously read recovery bytes remain available for export. Refresh explicitly to confirm current storage.
      </p>}
      <p className="sfx-error" role="alert">
        This ID has no saved sound or readable retained draft available. Keep this selection while
        refreshing the catalogue or exporting its retained copies.
      </p>
      {catalogue.error && <p className="sfx-error" role="alert">Recovery catalogue: {catalogue.error}</p>}
      {catalogue.limited && <p className="sfx-help">The recovery catalogue is limited; more retained copies may exist.</p>}
      {exportError && <p className="sfx-error" role="alert">{exportError}</p>}
      <div className="sheet-actions sfx-actions">
        <button className="button" type="button" onClick={refreshCatalogue}>Refresh sound recovery copies</button>
      </div>
      <p className="sfx-help">These copies belong to the current project generation and branch. An unreadable copy cannot be assigned to a sound ID.</p>
      <ul className="sfx-copy-list" aria-label="Retained sound recovery copies">
        {catalogue.entries.map((copy) => (
          <li className="sfx-copy" key={copy.key}>
            <p className="sfx-copy-title">{copy.value ? eventLabel(copy.value.draft.raw.event) : "Unreadable sound copy"}</p>
            <p className="sfx-id">{copy.key}</p>
            {copy.error && <p className="sfx-error">{message(copy.error)}</p>}
            <div className="sheet-actions sfx-actions">
              {copy.value && !copy.error && <button className="button compact" type="button"
                onClick={() => { if (copy.value) select(copy.value.draft.target); }}>Open this sound edit</button>}
              <button className="button compact" type="button" onClick={() => exportCopy(copy)}>Export sound recovery copy</button>
            </div>
            <details className="sfx-details">
              <summary>Raw recovery copy</summary>
              <textarea className="sfx-raw" rows={5} readOnly value={copy.raw}
                aria-label={"Raw sound recovery copy " + copy.writerId} spellCheck={false} />
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SfxDraftEditor({
  project,
  commit,
  target,
  catalogue,
  select,
  refreshCatalogue,
}: Omit<Props, "onSeek"> & {
  target: string;
  catalogue: Catalogue;
  select: (target: string) => void;
  refreshCatalogue: () => void;
}) {
  const api = useSemanticSfxDraft(project, target, commit);
  const {
    draft, session, saving, conflict, locked, missing, validation,
    unknown, reviewing, outcome, blocked,
  } = api;
  const { raw } = draft;
  const id = useId();
  const [actionError, setActionError] = useState<string | null>(null);
  const saved = (project.audio.sfx_cues ?? []).find((cue) => cue.id === target) ?? null;
  const sourceScene = project.scenes.find((scene) => scene.id === raw.sceneId);
  const scenes = project.scenes.map((scene) => ({ value: scene.id, label: scene.name }));
  const beats = (sourceScene?.beats ?? []).map((beat) => ({ value: beat.id, label: beat.label }));
  const wavs = project.assets.filter((asset) =>
    ["audio/wav", "audio/x-wav", "audio/wave"].includes(asset.media_type) && asset.content_sha256);
  const selectedWav = wavs.find((asset) =>
    asset.id === raw.sourceAssetId && asset.content_sha256 === raw.sourceSha256);
  const isNew = target === "new";
  const copies = retainedCopies(catalogue.entries, session.copies);
  const saveDisabled = saving || blocked || missing || locked || unknown || Boolean(draft.pending);
  // Removing a saved event does not validate an unrelated, unfinished upsert draft.
  const removeDisabled = isNew || saving || missing || locked || Boolean(conflict) ||
    Boolean(draft.pending) || Boolean(session.storageError);
  const choiceDisabled = saving || missing || unknown || Boolean(draft.pending);
  const canSeparate = unknown && !saving && !reviewing && !missing && Boolean(
    draft.pending && outcome && outcome.project.revision > draft.pending.revision,
  );
  const inspectedCue = outcome
    ? (outcome.project.audio.sfx_cues ?? []).find((cue) => cue.id === target) ?? null
    : null;

  async function act(action: () => unknown | Promise<unknown>) {
    setActionError(null);
    try {
      await action();
    } catch (error) {
      setActionError(message(error));
    }
  }

  function edit(patch: Partial<SfxRaw>) {
    void act(() => api.edit(patch));
  }

  const comparison = (
    <Comparison base={draft.base} saved={saved} raw={raw}
      baseRevision={draft.baseRevision} savedRevision={project.revision} />
  );

  return (
    <section className="sfx-draft" aria-labelledby={id + "-heading"}>
      <h4 id={id + "-heading"}>{isNew ? "New sound event draft" : "Edit sound event"}</h4>
      <dl className="sfx-identity">
        <div><dt>Sound ID</dt><dd className="sfx-id">{isNew ? "New event draft" : target}</dd></div>
        <div><dt>Edit base</dt><dd>Revision {draft.baseRevision}</dd></div>
        <div><dt>Saved project</dt><dd>Revision {project.revision}</dd></div>
      </dl>
      {missing && (
        <p className="sfx-error" role="alert">
          This sound ID is absent from the current saved project. The raw draft is available below
          for recovery and export. Saving or removing this ID is unavailable.
        </p>
      )}
      {locked && <p className="sfx-status" role="status">Sound changes are locked by the current project or source scene.</p>}
      {session.error && <p className="sfx-error" role="alert">{message(session.error)}</p>}
      {actionError && actionError !== session.error && <p className="sfx-error" role="alert">{actionError}</p>}
      {session.notice && <p className="sfx-status" role="status">{message(session.notice)}</p>}

      <fieldset className="sfx-fieldset">
        <legend>Placement</legend>
        <div className="sfx-field-grid">
          <label className="sfx-field" htmlFor={id + "-scene"}>
            <span className="field-label">Source scene</span>
            <select id={id + "-scene"} aria-label="SFX source scene" value={raw.sceneId}
              disabled={!isNew} onChange={(event) => edit({ sceneId: event.currentTarget.value })}>
              <RawOptions value={raw.sceneId} options={scenes} placeholder="Choose a scene" />
            </select>
          </label>
          <label className="sfx-field" htmlFor={id + "-event"}>
            <span className="field-label">Semantic event</span>
            <select id={id + "-event"} aria-label="SFX semantic event" value={raw.event}
              onChange={(event) => edit({ event: event.currentTarget.value })}>
              <RawOptions value={raw.event} options={eventOptions} />
            </select>
          </label>
          <label className="sfx-field" htmlFor={id + "-anchor"}>
            <span className="field-label">Event anchor</span>
            <select id={id + "-anchor"} aria-label="SFX event anchor" value={raw.anchorKind}
              onChange={(event) => edit({ anchorKind: event.currentTarget.value })}>
              <RawOptions value={raw.anchorKind} options={anchorOptions} />
            </select>
          </label>
          <label className="sfx-field" htmlFor={id + "-beat"}>
            <span className="field-label">Source beat</span>
            <select id={id + "-beat"} aria-label="SFX source beat" value={raw.beatId}
              disabled={raw.anchorKind !== "beat_start"}
              onChange={(event) => edit({ beatId: event.currentTarget.value })}>
              <RawOptions value={raw.beatId} options={beats} placeholder="Choose a beat" />
            </select>
          </label>
        </div>
        {!isNew && <p className="sfx-help">An existing sound keeps its source scene.</p>}
      </fieldset>

      <fieldset className="sfx-fieldset">
        <legend>Imported source</legend>
        <div className="sfx-field-grid">
          <label className="sfx-field sfx-field-wide" htmlFor={id + "-wav"}>
            <span className="field-label">Imported WAV</span>
            <select id={id + "-wav"} aria-label="SFX imported WAV" value={selectedWav?.id ?? ""}
              onChange={(event) => {
                const asset = wavs.find((item) => item.id === event.currentTarget.value);
                if (asset) edit({ sourceAssetId: asset.id, sourceSha256: asset.content_sha256 ?? "" });
              }}>
              <option value="" disabled>Choose an imported WAV</option>
              {wavs.map((asset) => (
                <option key={asset.id} value={asset.id}>
                  {asset.name + " · " + (asset.content_sha256?.slice(0, 12) ?? "")}
                </option>
              ))}
            </select>
            <span className="sfx-help">
              Selecting a WAV fills its asset ID and hash. Both exact values remain editable.
            </span>
          </label>
          {wavs.length === 0 && <p className="sfx-help sfx-field-wide">No imported WAV with a content hash is available in this project.</p>}
          <label className="sfx-field" htmlFor={id + "-asset"}>
            <span className="field-label">Source asset ID</span>
            <input id={id + "-asset"} aria-label="SFX Source asset ID" className="sfx-mono"
              type="text" autoComplete="off" spellCheck={false} value={raw.sourceAssetId}
              onChange={(event) => edit({ sourceAssetId: event.currentTarget.value })} />
          </label>
          <label className="sfx-field" htmlFor={id + "-sha"}>
            <span className="field-label">Source SHA-256</span>
            <input id={id + "-sha"} aria-label="SFX Source SHA-256" className="sfx-mono"
              type="text" autoComplete="off" spellCheck={false} value={raw.sourceSha256}
              onChange={(event) => edit({ sourceSha256: event.currentTarget.value })} />
          </label>
          <label className="sfx-field sfx-field-wide" htmlFor={id + "-attestation"}>
            <span className="field-label">Original source declaration</span>
            <textarea id={id + "-attestation"} aria-label="SFX Original source attestation"
              rows={3} value={raw.attestation}
              onChange={(event) => edit({ attestation: event.currentTarget.value })} />
            <span className="sfx-help">Describe the original source of this WAV.</span>
          </label>
        </div>
      </fieldset>

      <fieldset className="sfx-fieldset">
        <legend>Timing and envelope</legend>
        <p id={id + "-timing-help"} className="sfx-help">Frames use the source timeline at 30 fps. Incomplete numbers stay in your draft.</p>
        <div className="sfx-field-grid">
          {numericFields.map(({ field, label }) => (
            <label key={field} className="sfx-field" htmlFor={id + "-" + field}>
              <span className="field-label">{label}</span>
              <input id={id + "-" + field} aria-label={"SFX " + label}
                type="text" inputMode="decimal" autoComplete="off" spellCheck={false}
                aria-describedby={id + "-timing-help" + (validation ? " " + id + "-validation" : "")}
                value={raw[field]} onChange={(event) => edit({ [field]: event.currentTarget.value })} />
            </label>
          ))}
          <label className="sfx-field" htmlFor={id + "-tail"}>
            <span className="field-label">Tail policy</span>
            <select id={id + "-tail"} aria-label="SFX Tail policy" value={raw.tailPolicy}
              onChange={(event) => edit({ tailPolicy: event.currentTarget.value })}>
              <RawOptions value={raw.tailPolicy} options={tailOptions} />
            </select>
          </label>
        </div>
      </fieldset>

      {validation && <p id={id + "-validation"} className="sfx-error" role="status">{validation}</p>}

      {conflict ? (
        <section className="sfx-review" aria-labelledby={id + "-conflict-heading"}>
          <h4 id={id + "-conflict-heading"}>Compare with the saved sound</h4>
          <p className="sfx-help">Choose the values for your next edit after reviewing the base and current saved project.</p>
          {comparison}
          <div className="sheet-actions sfx-actions">
            <button className="button" type="button" disabled={choiceDisabled}
              onClick={() => void act(() => api.choose("saved"))}>Use saved sound values</button>
            <button className="button" type="button" disabled={choiceDisabled}
              onClick={() => void act(() => api.choose("draft"))}>Keep my sound draft</button>
          </div>
        </section>
      ) : (
        <details className="sfx-details">
          <summary>Compare base, saved sound and proposal</summary>
          {comparison}
        </details>
      )}

      {unknown && (
        <section className="sfx-review" aria-labelledby={id + "-unknown-heading"}>
          <h4 id={id + "-unknown-heading"}>Review the original sound attempt</h4>
          <p className="sfx-help">
            The outcome of the original attempt is unresolved. Read the saved project and its history
            before starting a separate edit.
          </p>
          {draft.pending && (
            <dl className="sfx-identity">
              <div><dt>Attempt ID</dt><dd className="sfx-id">{draft.pending.id}</dd></div>
              <div><dt>Attempt base</dt><dd>Revision {draft.pending.revision}</dd></div>
              <div><dt>Draft sequence</dt><dd>{draft.pending.sequence}</dd></div>
            </dl>
          )}
          <div className="sheet-actions sfx-actions">
            <button className="button" type="button" disabled={saving || reviewing}
              onClick={() => void act(() => api.inspect())}>Read saved sound and history</button>
          </div>
          {reviewing && <p className="sfx-status" role="status">Reading the saved project and sound history…</p>}
          {outcome && (
            <div className="sfx-readback">
              <p>Read revision {outcome.project.revision} with {outcome.events.length} history entries.</p>
              {outcome.matchingRevisions.length > 0 && (
                <p className="sfx-help">
                  Matching values were observed at revisions {outcome.matchingRevisions.join(", ")}.
                  The original attempt remains unresolved.
                </p>
              )}
              {outcome.matchingRevisions.length === 0 && (
                <p className="sfx-help">No matching values were found in the returned history. The original attempt remains unresolved.</p>
              )}
              {!isNew && (
                <details className="sfx-details">
                  <summary>Sound values in the read project</summary>
                  {inspectedCue ? <pre className="sfx-raw">{JSON.stringify(inspectedCue, null, 2)}</pre> :
                    <p className="sfx-help">This sound ID is absent from the read project.</p>}
                </details>
              )}
              {draft.pending && outcome.project.revision <= draft.pending.revision && (
                <p className="sfx-help">A separate edit requires a read project newer than the original attempt’s revision.</p>
              )}
            </div>
          )}
          <div className="sheet-actions sfx-actions">
            <button className="button" type="button" disabled={!canSeparate}
              onClick={() => void act(() => api.separate())}>
              Keep original attempt and start a separate sound edit
            </button>
          </div>
        </section>
      )}

      <div className="sheet-actions sfx-actions">
        <button className="button button-primary" type="button" disabled={saveDisabled}
          onClick={() => void act(() => api.save())}>
          {isNew ? "Add sound event" : "Save sound event"}
        </button>
        {!isNew && (
          <button className="button" type="button" disabled={removeDisabled}
            onClick={() => void act(() => api.remove())}>Remove sound event</button>
        )}
      </div>
      {saving && <p className="sfx-status" role="status">Saving the sound change…</p>}
      {!isNew && <p className="sfx-help">Removing the saved event keeps your draft in recovery copies.</p>}

      <section className="sfx-recovery" aria-labelledby={id + "-recovery-heading"}>
        <h4 id={id + "-recovery-heading"}>Sound draft recovery</h4>
        {session.storageError && <p className="sfx-error" role="alert">{message(session.storageError)}</p>}
        {catalogue.error && <p className="sfx-error" role="alert">Recovery catalogue: {catalogue.error}</p>}
        {catalogue.limited && <p className="sfx-help">The recovery catalogue is limited; this list may not include every retained copy.</p>}
        <div className="sheet-actions sfx-actions">
          <button className="button" type="button" disabled={saving || reviewing}
            onClick={() => void act(async () => { await api.retry(); refreshCatalogue(); })}>
            Retry sound recovery copy
          </button>
          <button className="button" type="button" onClick={() => void act(() => api.download())}>
            Export sound draft
          </button>
          <button className="button" type="button" disabled={saving || reviewing}
            onClick={() => void act(async () => { await api.refresh(); refreshCatalogue(); })}>
            Refresh sound recovery copies
          </button>
        </div>
        {copies.length === 0 ? (
          <p className="sfx-help">No retained copies are listed for this project generation and branch.</p>
        ) : (
          <ul className="sfx-copy-list" aria-label="Retained sound recovery copies">
            {copies.map((copy) => {
              const copyTarget = copy.value?.draft.target;
              const sameTarget = copyTarget === target;
              const readable = Boolean(copy.value) && !copy.error;
              return (
                <li key={copy.key} className="sfx-copy">
                  <p className="sfx-copy-title">
                    {copy.value ? eventLabel(copy.value.draft.raw.event) : "Unreadable sound copy"}
                    {sameTarget && <span className="sfx-help"> · selected edit</span>}
                  </p>
                  <dl className="sfx-copy-meta">
                    <div><dt>Writer</dt><dd className="sfx-id">{copy.writerId}</dd></div>
                    {copyTarget && <div><dt>Target</dt><dd className="sfx-id">{copyTarget === "new" ? "New event draft" : copyTarget}</dd></div>}
                  </dl>
                  {copy.value?.draft.pending && <p className="sfx-help">Includes a retained sound attempt.</p>}
                  {copy.error && <p className="sfx-error">{message(copy.error)}</p>}
                  <div className="sheet-actions sfx-actions">
                    {copyTarget && !sameTarget && (
                      <button className="button compact" type="button" onClick={() => select(copyTarget)}>
                        Open this sound edit
                      </button>
                    )}
                    {sameTarget && (
                      <button className="button compact" type="button"
                        disabled={!readable || saving || reviewing || Boolean(draft.pending)}
                        onClick={() => void act(async () => { await api.restore(copy); refreshCatalogue(); })}>
                        Restore sound recovery copy
                      </button>
                    )}
                    <button className="button compact" type="button"
                      onClick={() => void act(() => api.download(copy.raw))}>Export sound recovery copy</button>
                  </div>
                  <details className="sfx-details">
                    <summary>Raw recovery copy</summary>
                    <textarea className="sfx-raw" rows={5} readOnly value={copy.raw}
                      aria-label={"Raw sound recovery copy " + copy.writerId} spellCheck={false} />
                  </details>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </section>
  );
}
