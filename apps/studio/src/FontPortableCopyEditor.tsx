import "./font-portable-copy.css";
import type { Change, Project } from "./types";
import { fontCopyCandidates, prepareFontCopyChange, savedFontCopyRaw, type FontCopyRaw } from "./fontPortableCopyDraft";
import { BrandDraftFeedback, useBrandAuthoringDraft } from "./useBrandAuthoringDraft";

export default function FontPortableCopyEditor({ project, commit, busy, desktopMode }: {
  project: Project; commit: (change: Change) => Promise<Project>; busy: boolean; desktopMode: boolean;
}) {
  const editor = useBrandAuthoringDraft(project, "font-copy", commit, busy || !desktopMode);
  const raw = editor.draft.raw as FontCopyRaw;
  const sources = fontCopyCandidates(project), saved = project.production_design?.font_portable_copies ?? [];
  const patch = (value: Partial<Omit<FontCopyRaw, "kind">>) => editor.edit(current => {
    if (current.kind !== "font-copy") throw new Error("The selected font editor changed.");
    return { ...current, ...value };
  });
  const choose = (assetId: string) => editor.edit(() => savedFontCopyRaw(project, assetId));
  let invalid: string | null = null;
  try { prepareFontCopyChange(project, raw); }
  catch (reason) { invalid = reason instanceof Error ? reason.message : String(reason); }
  return <section className="font-copy-editor" aria-label="Portable font copy declarations">
    <div className="portable-operation-copy">
      <strong>Original font copies</strong>
      <span>Record whether the exact font original may travel with this project. Saving this declaration does not verify a license or approve publication.</span>
    </div>
    {!desktopMode && <p className="portable-runtime-note">Save font declarations in Desktop with the original project open.</p>}
    <BrandDraftFeedback editor={editor} project={project}/>
    {!sources.length && <p className="portable-runtime-note">Import the original font in Assets and record its family, asset source and use rights in Visual language.</p>}
    <div className="production-plan-grid">
      <label className="production-field"><span>Original portable font</span>
        <select aria-label="Original portable font" value={raw.assetId} disabled={editor.working}
          onChange={event => choose(event.target.value)}>
          <option value="">Choose an imported font</option>
          {raw.assetId && !sources.some(asset => asset.id === raw.assetId)
            && <option value={raw.assetId}>Retained unavailable original</option>}
          {sources.map(asset => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
        </select>
      </label>
      <label className="production-field"><span>Portable font copy permission</span>
        <select aria-label="Portable font copy permission" value={raw.permission} disabled={editor.working}
          onChange={event => patch({ permission: event.target.value as FontCopyRaw["permission"], confirmed: false })}>
          <option value="">Choose permission</option><option value="allowed">Allowed by the recorded source terms</option>
          <option value="prohibited">Prohibited</option>
        </select>
      </label>
      <label className="production-field"><span>Font original origin</span>
        <input aria-label="Font original origin" value={raw.origin} disabled={editor.working}
          onChange={event => patch({ origin: event.target.value, confirmed: false })}/></label>
      <label className="production-field"><span>Font copy license or terms</span>
        <input aria-label="Font copy license or terms" value={raw.license} disabled={editor.working}
          onChange={event => patch({ license: event.target.value, confirmed: false })}/></label>
      <label className="production-field"><span>Font copy note</span>
        <textarea aria-label="Font copy note" rows={2} value={raw.note} disabled={editor.working}
          onChange={event => patch({ note: event.target.value, confirmed: false })}/></label>
    </div>
    {raw.contentSha256 && <p className="portable-runtime-note">Original SHA-256: <code>{raw.contentSha256}</code></p>}
    <label className="font-copy-confirmation"><input type="checkbox" checked={raw.confirmed} disabled={editor.working}
      onChange={event => patch({ confirmed: event.target.checked })}/> I confirm this font-copy declaration for these original bytes</label>
    {invalid && raw.assetId && <p className="portable-runtime-note" role="status">{invalid}</p>}
    <button type="button" className="button button-primary" disabled={editor.blocked || !!invalid}
      onClick={() => void editor.submit("font-copy")}>Save font copy declaration</button>
    <div className="font-copy-records" aria-label="Saved font copy declarations">
      <strong>Saved declarations · r{project.revision}</strong>
      {!saved.length && <p>No font-copy declarations saved.</p>}
      {saved.map(copy => {
        const asset = project.assets.find(value => value.id === copy.asset_id);
        return <div className="font-copy-record" key={copy.asset_id}>
          <p><strong>{asset?.name ?? "Detached original font"}</strong> · {copy.permission}</p>
          <p>{copy.origin || "Origin not recorded"} · {copy.license || "Terms not recorded"}</p>
          {copy.note && <p>{copy.note}</p>}
          <code>{copy.content_sha256}</code>
          {asset?.content_sha256 === copy.content_sha256
            ? <button type="button" className="button compact" disabled={editor.working}
              onClick={() => choose(copy.asset_id)}>Edit font declaration</button>
            : <p className="portable-runtime-note">Historical original: its prohibition still applies to the same bytes.</p>}
          <button type="button" className="button compact" disabled={editor.blocked}
            onClick={() => void editor.submit({ removeFont: copy.asset_id })}>Remove font declaration</button>
        </div>;
      })}
    </div>
    <p className="portable-runtime-note">An allowed copy also needs cleared use rights for its asset source. Storage inspects the original font and its redistribution restrictions before export. Removing a declaration leaves copying blocked until an allowed declaration is explicitly saved.</p>
  </section>;
}
