import { AlertTriangle, BookOpenText, CircleHelp, FileText, ShieldAlert } from "lucide-react";
import { useMemo } from "react";
import { inspectCaptionReadability } from "./captionReadability";
import type { DeliverableProfile, Project } from "./types";
import "./caption-readability.css";

const LABEL:Record<string,string>={
  SELECT_VOICE:"Select voice", NO_TRANSCRIPT:"Transcript needed",
  VOICE_TRANSCRIPT_MISSING:"Voice transcript missing",
  VOICE_SOURCE_NOT_ADMITTED:"Voice source not found",
  UNKNOWN_CUT_SCENE:"Missing cut scene", DUPLICATE_CUT_SCENE:"Duplicate scene",
  REORDERED_CUT:"Cut order", EMPTY_CUT:"Empty cut",
  INVALID_TIMING:"Invalid time", CROSSES_CUT:"Cut boundary",
  OUTSIDE_SCENES:"Outside scene", EMPTY_CAPTION:"Empty caption",
  UNKNOWN_ALIGNMENT:"Unknown alignment", STALE_MEASURED_SOURCE:"Voice source changed",
  BRIEF_DISPLAY:"Brief exposure", LONG_DISPLAY:"Long exposure",
  MANY_LINES:"Too many lines", LONG_LINE:"Long line",
  FAST_READING:"Fast reading", OVERLAPPING_CAPTIONS:"Caption overlap",
  BURN_IN_NOT_REVIEWED:"Native burn-in needed",
};

export default function CaptionReadabilityReview({
  project, profile, saved, onOpenAudio,
}:{
  project:Project; profile:DeliverableProfile;
  saved:DeliverableProfile|null; onOpenAudio:()=>void;
}) {
  const report=useMemo(()=>{
    try {return inspectCaptionReadability(project,profile);}
    catch {return null;}
  },[project,profile]);
  const dirty=!!saved && JSON.stringify(profile)!==JSON.stringify(saved);
  const verdict=report?.verdict==="CAPTIONS_DISABLED"?"Disabled":
    report?.verdict==="STRUCTURAL_BLOCKERS"?"Needs correction":
    report?.verdict==="EDITOR_REVIEW"?"Editorial review":
    report?.verdict==="NO_AUTOMATED_FLAGS"?"No automated flags":
    "Not assessed";
  return <section className="caption-reading-preflight" aria-label="Caption reading preflight">
    <details>
      <summary>
        <BookOpenText size={15} aria-hidden="true"/>
        <strong>Caption reading review</strong>
        <span className="caption-reading-result">{verdict}</span>
      </summary>
      <div className="caption-reading-body">
        <div className="caption-reading-provenance">
          <span>{dirty?"Unsaved profile draft":"Saved profile"} · {profile.language} · r{project.revision}</span>
          <span className="mono">{project.generation.slice(0,8)} · {profile.name}</span>
        </div>
        {report===null ? <>
          <div className="caption-reading-empty" role="status">
            Source time or data exceeded a bounded analysis; no caption assessment was made.
          </div>
        </> : <>
          <p className="caption-reading-intro">
            {report.verdict==="CAPTIONS_DISABLED"
              ?"This delivery profile has captions disabled. No captions are inferred."
              :"This check inspects authored transcript timing and text before export. It does not change cues, create a subtitle file or grant any media effect."}
          </p>
          {report.verdict!=="CAPTIONS_DISABLED" && <>
            <div className="caption-reading-metrics">
              <span><strong>{report.selectedSegments}</strong> source cues</span>
              <span><strong>{report.inspectedSegments}</strong> inspected</span>
              <span><strong>{report.blocks}</strong> structural</span>
              <span><strong>{report.reviews}</strong> editorial</span>
            </div>
            <div className="caption-reading-findings" aria-label="Caption findings">
              {report.findings.length===0 ? <p className="caption-reading-empty">
                No automated flags in the inspected source cues. Actual visual placement, viewer
                reading, language-specific typography and the final native render remain unverified.
              </p> : <ol>
                {report.findings.slice(0,18).map((finding,i)=>{
                  const Icon=finding.severity==="block"?AlertTriangle:CircleHelp;
                  return <li key={finding.code+"-"+finding.segmentId+"-"+i}
                    className={"caption-reading-finding "+finding.severity}>
                    <div className="caption-reading-finding-header">
                      <Icon size={13} aria-hidden="true"/>
                      <strong>{LABEL[finding.code]??finding.code.replaceAll("_"," ")}</strong>
                      <span>{finding.severity==="block"?"Correction":"Review"}</span>
                    </div>
                    <p>{finding.detail}</p>
                    {finding.excerpt && <blockquote>{finding.excerpt}</blockquote>}
                    {finding.sourceStartMs!==null &&
                      <small className="mono">Source {Math.floor(finding.sourceStartMs/1000)}.{String(finding.sourceStartMs%1000).padStart(3,"0")}s</small>}
                  </li>;
                })}
              </ol>}
              {report.findings.length>18 && <p className="caption-reading-more">
                {report.findings.length-18} more findings in the current bounded report.
              </p>}
              {report.findingsTruncated && <p className="caption-reading-more">
                Additional source findings exceed the display budget; review the transcript in Audio.
              </p>}
            </div>
          </>}
          <div className="caption-reading-foot">
            <button type="button" className="button" onClick={onOpenAudio}
              aria-label="Review transcript in Audio">
              <FileText size={13} aria-hidden="true"/> Review transcript in Audio
            </button>
          </div>
          <div className="caption-reading-truth">
            <ShieldAlert size={15} aria-hidden="true"/>
            <p>{report.guidance} Structural flags here do not replace trusted Rust
              export validation. A green or empty report is not a native pixel,
              rights, accessibility or human creative PASS.</p>
          </div>
        </>}
      </div>
    </details>
  </section>;
}
