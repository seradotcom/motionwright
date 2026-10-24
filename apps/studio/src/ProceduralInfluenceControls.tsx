import type { ProceduralInfluenceRaw } from "./proceduralInfluence";

const fields = [
  ["center_x", "Influence center X", "Project pixels, inside the authored field"],
  ["center_y", "Influence center Y", "Project pixels, inside the authored field"],
  ["radius", "Influence radius", "1 to 1920 pixels"],
  ["target_size", "Influence target size", "4 to 128 pixels at the center"],
] as const;

/** Raw strings join the existing Workbench buffer; this control owns no Project state. */
export default function ProceduralInfluenceControls({ value, defaults, onChange }: {
  value?: ProceduralInfluenceRaw;
  defaults: ProceduralInfluenceRaw;
  onChange: (value: ProceduralInfluenceRaw) => void;
}) {
  const raw = value ?? { ...defaults, enabled: false };
  return <fieldset className="production-approval" aria-label="Influence field parameters">
    <legend>Influence Field / radial size</legend>
    <label className="production-field">
      <span>Enable influence field</span>
      <input type="checkbox" aria-label="Enable influence field" checked={raw.enabled}
        onChange={event => onChange({ ...raw, enabled: event.target.checked })}/>
    </label>
    <p className="production-help">A static radial field changes object size around each original center.
      Size reaches the target at the influence center and returns to the base size at the radius.
      Objects outside the radius retain their geometry. Every object remains editable.</p>
    <div className="production-plan-grid">
      {fields.map(([key, label, help]) => <label className="production-field" key={key}>
        <span>{label}</span>
        <input type="text" inputMode="numeric" aria-label={label} value={raw[key]} disabled={!raw.enabled}
          onChange={event => onChange({ ...raw, [key]: event.target.value })}/>
        <small>{help}</small>
      </label>)}
    </div>
    <p className="production-help">The influenced outline must remain inside the authored field.
      Disabling the field retains these inputs in your draft and omits the influence from the next save.</p>
  </fieldset>;
}
