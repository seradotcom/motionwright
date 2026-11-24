import type { ScopedChangeDraft } from "./scopedChangeDraft";
import { draftMotionInput, draftStyleInput, updateDraftMotionInput, updateDraftStyleInput } from "./scopedChangeDraft";
import { CANVAS_FIELD_LABELS } from "./canvasWorkspaceDraft";
import { CANVAS_STYLE_FIELDS, SCOPED_MOTION_CURVES, SCOPED_MOTION_PROPERTIES, scopedStyleIssues } from "./scopedPaintMotion";
import "./scoped-paint-motion.css";

interface Props { draft: ScopedChangeDraft; disabled: boolean; onChange: (draft: ScopedChangeDraft) => void }
const propertyLabels = { x: "Position X", y: "Position Y", width: "Width", height: "Height", rotation_deg: "Rotation (deg)", opacity: "Opacity" };
const curveLabels = { linear: "Linear", ease_in_out: "Ease in / out", ease_out_cubic: "Out cubic", hold: "Hold" };

export default function ScopedPaintMotionEditor({ draft, disabled, onChange }: Props) {
  if (draft.editor.kind === "style") {
    const values = draftStyleInput(draft), errors = scopedStyleIssues(values);
    return <div className="scoped-paint-editor">
      <p className="production-help">Edit the object's full saved style. Empty optional fields use the document's inherited style.</p>
      <div className="production-transform-grid">{CANVAS_STYLE_FIELDS.map(field => <label className="production-field" key={field}>
        <span>{CANVAS_FIELD_LABELS[field]}</span>
        {field === "blend_mode" ? <select aria-label={"Patch " + field} value={values[field]} disabled={disabled}
          onChange={event => onChange(updateDraftStyleInput(draft, field, event.target.value))}>
          {["normal", "multiply", "screen", "add"].map(mode => <option value={mode} key={mode}>{mode}</option>)}
        </select> : <input aria-label={"Patch " + field} type="text" value={values[field]} disabled={disabled}
          inputMode={["stroke_width", "font_size", "font_weight", "line_height"].includes(field) ? "decimal" : "text"}
          aria-invalid={Boolean(errors[field]) || undefined} aria-describedby={errors[field] ? "scoped-editor-error" : undefined}
          onChange={event => onChange(updateDraftStyleInput(draft, field, event.target.value))}/>}
      </label>)}</div>
    </div>;
  }
  if (draft.editor.kind !== "motion") return null;
  const rows = draftMotionInput(draft);
  const replace = (index: number, patch: Partial<typeof rows[number]>) =>
    onChange(updateDraftMotionInput(draft, rows.map((row, i) => i === index ? { ...row, ...patch } : row)));
  return <section className="scoped-motion-editor" aria-label="Complete animation proposal">
    <p className="production-help">This edit replaces the complete animation track. Times are scene-relative seconds or exact fractions. A curve controls arrival at its keyframe; Hold keeps the preceding value until that time.</p>
    <div className="scoped-motion-keys">{rows.map((row, index) => <fieldset className="scoped-motion-row" key={index}>
      <legend>Keyframe {index + 1}</legend>
      <label className="production-field"><span>Time</span><input aria-label={`Keyframe ${index + 1} time`} type="text" value={row.at} disabled={disabled}
        placeholder="0 or 1/30" onChange={event => replace(index, { at: event.target.value })}/></label>
      <label className="production-field"><span>Property</span><select aria-label={`Keyframe ${index + 1} property`} value={row.property} disabled={disabled}
        onChange={event => replace(index, { property: event.target.value as typeof row.property })}>
        {SCOPED_MOTION_PROPERTIES.map(property => <option key={property} value={property}>{propertyLabels[property]}</option>)}</select></label>
      <label className="production-field"><span>Value</span><input aria-label={`Keyframe ${index + 1} value`} type="text" inputMode="decimal" value={row.value} disabled={disabled}
        onChange={event => replace(index, { value: event.target.value })}/></label>
      <label className="production-field"><span>Curve</span><select aria-label={`Keyframe ${index + 1} curve`} value={row.interpolation} disabled={disabled}
        onChange={event => replace(index, { interpolation: event.target.value as typeof row.interpolation })}>
        {SCOPED_MOTION_CURVES.map(curve => <option key={curve} value={curve}>{curveLabels[curve]}</option>)}</select></label>
      <button className="secondary-button scoped-motion-remove" aria-label={`Remove keyframe ${index + 1}`} disabled={disabled}
        onClick={() => onChange(updateDraftMotionInput(draft, rows.filter((_, i) => i !== index)))}>Remove</button>
    </fieldset>)}</div>
    {!rows.length && <p className="production-help" role="status">No keyframes in this proposal. Adding it to the queue removes the object's saved animation.</p>}
    <div className="production-button-row">
      <button className="secondary-button" disabled={disabled || rows.length >= 128}
        onClick={() => onChange(updateDraftMotionInput(draft, [...rows, { at: "", property: "x", value: "", interpolation: "linear" }]))}>Add keyframe</button>
      <button className="secondary-button" disabled={disabled || !rows.length}
        onClick={() => onChange(updateDraftMotionInput(draft, []))}>Clear animation</button>
    </div>
    <p className="production-help">{rows.length} / 128 keys. All authored curves are preserved. Native rendering checks the selected engine's supported realization before execution.</p>
  </section>;
}
