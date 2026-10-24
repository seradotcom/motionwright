import { RecipeDraftReview, containRecipeKeyboard } from "./DeliveryProfiles";
import type { useDeliveryRecipeDraft } from "./deliveryRecipe";

type TargetController = ReturnType<typeof useDeliveryRecipeDraft>;
const fields = [
  { key: "target_lufs", label: "Integrated loudness target (LUFS)", placeholder: "Optional LUFS target" },
  { key: "target_true_peak_dbtp", label: "True peak ceiling (dBTP)", placeholder: "Optional dBTP ceiling" },
] as const;

/** Editable delivery intent. The native measurement/QC path owns all verdicts. */
export default function DeliveryAudioTargets({ controller, profileId, revision, blockedReason, onDraftAction }: {
  controller: TargetController;
  profileId: string;
  revision: number;
  blockedReason?: string | null;
  onDraftAction: () => void;
}) {
  const id = (field: string) => "delivery-audio-target-" + profileId + "-" + field;
  const hasSaved = fields.some(field => controller.savedValues[field.key] !== "");
  const pending = controller.dirty || controller.awaiting || controller.pending || controller.recoveryBlocked || controller.sourceBehind;
  return <section className="caption-export-panel" aria-label="Delivery audio targets" onKeyDown={containRecipeKeyboard}>
    <header><div><strong>Delivery audio targets</strong><span>Author the target values for this saved delivery profile.</span></div>
      <span className={pending ? "status-pill status-unknown" : "status-pill"}>{pending ? "PENDING INPUT" : hasSaved ? "SAVED TARGETS" : "NO TARGETS"}</span>
    </header>
    <div className="delivery-field-grid">
      {fields.map(field => <label key={field.key} htmlFor={id(field.key)}>
        <span className="field-label">{field.label}</span>
        <input id={id(field.key)} type="text" inputMode="decimal" aria-label={field.label}
          value={controller.values[field.key] ?? ""} placeholder={field.placeholder}
          disabled={controller.locked || controller.sourceBehind}
          aria-invalid={controller.conflicts.includes(field.key)}
          aria-describedby={"delivery-audio-target-help-" + profileId}
          onChange={event => { onDraftAction(); controller.set(field.key, event.target.value); }} />
      </label>)}
    </div>
    <p id={"delivery-audio-target-help-" + profileId} className="production-help">
      Leave either field empty to omit that target. Values use exact steps of 0.001; incomplete or finer input stays in your draft until you correct it.
      Clearing both removes the targets only after you explicitly save.
    </p>
    <p className="delivery-truth-note">These are authored targets. Measured loudness, true peak and a native QC result are separate evidence.</p>
    <RecipeDraftReview controller={controller} title="Audio targets" revision={revision}
      fields={Object.fromEntries(fields.map(field => [field.key, { label: field.label, controlId: id(field.key) }]))}
      saveLabel="Save audio targets" clearLabel="Clear saved audio targets" hasSavedGroup={hasSaved}
      blockedReason={blockedReason} onDraftAction={onDraftAction} />
  </section>;
}
