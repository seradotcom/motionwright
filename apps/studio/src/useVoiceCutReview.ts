import { useEffect, useMemo, useState } from "react";
import type { DeliverableProfile, Project } from "./types";
import { deriveVoiceCut, isOriginalVoiceMontage, voiceCutDigest, type VoiceCut } from "./voiceCut";

/** An async digest is accepted only for the exact currently displayed wire. */
export function useVoiceCutReview(project: Project, profile: DeliverableProfile | null) {
  const montage = Boolean(profile && isOriginalVoiceMontage(project, profile));
  const derived = useMemo((): {cut: VoiceCut | null; error: string | null} => {
    if (!profile || !montage) return {cut: null, error: null};
    try { return {cut: deriveVoiceCut(project, profile), error: null}; }
    catch (e) { return {cut: null, error: e instanceof Error ? e.message : String(e)}; }
  }, [project, profile, montage]);
  const wire = derived.cut?.wire;
  const [result, setResult] = useState<{wire: string; digest: string | null; error: string | null} | null>(null);
  useEffect(() => {
    if (!wire) return;
    let live = true;
    voiceCutDigest(wire).then(digest => { if (live) setResult({wire, digest, error: null}); },
      e => { if (live) setResult({wire, digest: null, error: String(e)}); });
    return () => { live = false; };
  }, [wire]);
  const current = wire && result?.wire === wire ? result : null;
  return { montage, cut: derived.cut, digest: current?.digest ?? null, error: derived.error ?? current?.error ?? null };
}
