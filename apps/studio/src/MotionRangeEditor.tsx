import type { ScopedChangeDraft } from "./scopedChangeDraft";
import { applyDraftMotionRange, draftMotionInput, updateDraftMotionRangeInput } from "./scopedChangeDraft";
import { defaultMotionRangeInput, retimeMotionRows } from "./timelineMotionRange";
import type { RationalTime } from "./types";

export default function MotionRangeEditor({ draft, duration, disabled, onChange }: {
  draft: ScopedChangeDraft; duration?: RationalTime; disabled: boolean; onChange: (draft: ScopedChangeDraft) => void;
}) {
  const rows = draftMotionInput(draft), input = draft.editor.motionRangeRaw ?? defaultMotionRangeInput(rows);
  let issue: string | null = null;
  try {
    if (!duration) throw new Error("Select the saved source scene before changing its animation timing.");
    retimeMotionRows(rows,input,duration);
  } catch(reason) { issue = reason instanceof Error ? reason.message : String(reason); }
  const edit = (patch: Partial<typeof input>) => onChange(updateDraftMotionRangeInput(draft,{...input,...patch}));
  return <details className="scoped-motion-range" open={draft.editor.motionRangeRaw !== undefined}>
    <summary>Move or fit the whole animation</summary>
    <p className="production-help">Move preserves all relative intervals. Fit scales every keyframe time into the new interval. Values, curves and holds stay authored; times are exact scene-relative seconds or fractions.</p>
    <div className="production-transform-grid">
      <label className="production-field"><span>Operation</span><select aria-label="Animation range operation" value={input.mode} disabled={disabled}
        onChange={event=>edit({mode:event.target.value as typeof input.mode})}><option value="move">Move whole range</option><option value="fit">Fit whole range</option></select></label>
      <label className="production-field"><span>New start</span><input aria-label="Animation range start" value={input.start} disabled={disabled}
        type="text" placeholder="0 or 1/30" onChange={event=>edit({start:event.target.value})}/></label>
      {input.mode==="fit" && <label className="production-field"><span>New end</span><input aria-label="Animation range end" value={input.end} disabled={disabled}
        type="text" placeholder="2 or 90/30" onChange={event=>edit({end:event.target.value})}/></label>}
    </div>
    {issue && <p className="production-help" role="status">{issue}</p>}
    <div className="production-button-row">
      <button className="secondary-button" disabled={disabled || issue!==null} onClick={()=>onChange(applyDraftMotionRange(draft,duration!))}>Apply range to animation draft</button>
      <button className="secondary-button" disabled={disabled || !draft.editor.motionRangeRaw} onClick={()=>{
        const editor={...draft.editor}; delete editor.motionRangeRaw; onChange({...draft,editor});
      }}>Reset range inputs</button>
    </div>
    <p className="production-help">This updates the editable proposal. Add the complete animation to the queue and compare it before saving.</p>
  </details>;
}
