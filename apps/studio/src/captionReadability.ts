/** Read-only editorial caption evidence preflight.
 * Timing uses bounded exact rational math. This never exports captions,
 * admits an audio source, checks rendered pixels, or changes project state.
 */
import type { DeliverableProfile, Project, RationalTime, TranscriptSegment } from "./types";

export type CaptionFindingSeverity = "block" | "review";
export interface CaptionReadingFinding {
  code: string;
  severity: CaptionFindingSeverity;
  segmentId: string | null;
  detail: string;
  excerpt: string | null;
  sourceStartMs: number | null;
}
export interface CaptionReadabilityReport {
  schema: "motionwright.caption-readability/1";
  projectId: string;
  generation: string;
  revision: number;
  profileId: string;
  voiceTrackId: string | null;
  locale: string;
  verdict: "CAPTIONS_DISABLED" | "STRUCTURAL_BLOCKERS" |
    "EDITOR_REVIEW" | "NO_AUTOMATED_FLAGS";
  selectedSegments: number;
  inspectedSegments: number;
  omittedSegments: number;
  blocks: number;
  reviews: number;
  findings: CaptionReadingFinding[];
  findingsTruncated: boolean;
  guidance: string;
}

type Fraction = { n: bigint; d: bigint };
type Timed = { segment: TranscriptSegment; start: Fraction; end: Fraction };
const INT = /^-?[0-9]{1,19}$/;
const ZERO:Fraction = { n:0n,d:1n };
const MAX_SCENES = 256, MAX_SEGMENTS = 768, MAX_CUE_CHARACTERS = 8000;
const MAX_FINDINGS = 90;
// These are editorial triage thresholds, not universal language standards,
// accessibility certification or native font layout rules.
const HEURISTIC_CPS = 20;
const HEURISTIC_CHAR_PER_LINE = 42;

function gcd(a:bigint,b:bigint):bigint {
  let x=a<0n?-a:a, y=b<0n?-b:b;
  while(y!==0n) { const t=x%y; x=y; y=t; }
  return x || 1n;
}
function normalize(n:bigint,d:bigint):Fraction {
  if(d<=0n) throw new Error("Caption time denominator must be positive");
  const divisor=gcd(n,d);
  const out={n:n/divisor,d:d/divisor};
  // Rational arithmetic stays finite even for deliberately adversarial
  // combinations of source denominators. Never round into a fake cue.
  if(out.n.toString().length>85 || out.d.toString().length>85)
    throw new Error("Caption clock exceeded bounded rational precision");
  return out;
}
function fraction(t:RationalTime):Fraction {
  if(!t || !INT.test(t.num) || !INT.test(t.den))
    throw new Error("Invalid exact caption time");
  return normalize(BigInt(t.num),BigInt(t.den));
}
function add(a:Fraction,b:Fraction):Fraction {
  return normalize(a.n*b.d+b.n*a.d,a.d*b.d);
}
function sub(a:Fraction,b:Fraction):Fraction {
  return normalize(a.n*b.d-b.n*a.d,a.d*b.d);
}
function compare(a:Fraction,b:Fraction):number {
  const value=a.n*b.d-b.n*a.d;
  return value<0n?-1:value>0n?1:0;
}
function milliseconds(time:Fraction):number|null {
  if(time.n<0n) return null;
  const whole=(time.n*1000n)/time.d;
  return whole<=BigInt(Number.MAX_SAFE_INTEGER)?Number(whole):null;
}
function safeExcerpt(text:string):string {
  const simple=text.replace(/\s+/gu," ").trim();
  return Array.from(simple).slice(0,120).join("");
}
function normalizedLines(text:string):string[] {
  return text.split(/\r\n|\r|\n/u);
}

export function inspectCaptionReadability(
  project:Project, profile:DeliverableProfile,
):CaptionReadabilityReport {
  if(project.scenes.length>MAX_SCENES ||
     project.audio.transcript.length>MAX_SEGMENTS ||
     profile.included_scene_ids.length>MAX_SCENES)
    throw new Error("Caption evidence input exceeds the inspection budget");
  const findings:CaptionReadingFinding[]=[];
  let totalFindings=0,blocks=0,reviews=0;
  const flag=(code:string,severity:CaptionFindingSeverity,detail:string,
    segment:TranscriptSegment|null=null,at:Fraction|null=null)=>{
    totalFindings++;
    if(severity==="block")blocks++;else reviews++;
    if(findings.length<MAX_FINDINGS) findings.push({
      code,severity,segmentId:segment?.id??null,detail,
      excerpt:segment?safeExcerpt(segment.text):null,
      sourceStartMs:at?milliseconds(at):null,
    });
  };
  const report=(verdict:CaptionReadabilityReport["verdict"],
    selectedSegments:number,inspectedSegments:number,omittedSegments:number,
    voiceTrackId:string|null):CaptionReadabilityReport=>({
      schema:"motionwright.caption-readability/1",
      projectId:project.id,generation:project.generation,revision:project.revision,
      profileId:profile.id,voiceTrackId,locale:profile.language,
      verdict,selectedSegments,inspectedSegments,omittedSegments,
      blocks,reviews,findings,findingsTruncated:totalFindings>findings.length,
      guidance:"Indicative, Unicode-codepoint-based reading guidance only. No source speech match, exact glyph shaping, caption pixels, viewer comprehension or human approval is inferred.",
    });

  if(!profile.captions)
    return report("CAPTIONS_DISABLED",0,0,0,profile.voice_track_id);
  if(profile.burn_in_captions)flag("BURN_IN_NOT_REVIEWED","review",
    "Burn-in needs independent native frame evidence for safe position, occlusion and legibility.");
  const voices=new Set(project.audio.transcript.map(segment=>segment.voice_track_id));
  let voiceTrackId=profile.voice_track_id;
  if(!voiceTrackId && voices.size>1)
    flag("SELECT_VOICE","block",
      "Multiple transcript voice takes exist. Select a single take for this delivery profile.");
  if(!voiceTrackId && voices.size===1) voiceTrackId=[...voices][0];
  if(!voiceTrackId && voices.size===0)
    flag("NO_TRANSCRIPT","block","No transcript segments exist for caption export.");
  const matching=voiceTrackId
    ?project.audio.transcript.filter(segment=>segment.voice_track_id===voiceTrackId)
    :[];
  if(voiceTrackId && matching.length===0)
    flag("VOICE_TRANSCRIPT_MISSING","block",
      "The selected voice has no transcript segments; no caption sidecar can be inferred.");
  const matchingVoice=project.audio.voice_tracks.find(t=>t.id===voiceTrackId);
  if(voiceTrackId && !matchingVoice)
    flag("VOICE_SOURCE_NOT_ADMITTED","review",
      "No current voice asset is bound to the selected transcript; inspect source admission in Audio.");

  // Validate the source cut independently, not by trusting an unsaved profile
  // draft. Do not silently reorder, duplicate or reinterpret scene identities.
  const sourceIds=project.scenes.map(scene=>scene.id);
  const requested=profile.included_scene_ids;
  const seen=new Set<string>();
  let previous=-1;
  for(const id of requested) {
    const position=sourceIds.indexOf(id);
    if(position<0)flag("UNKNOWN_CUT_SCENE","block",
      "This delivery references a scene absent from the current source project.");
    if(seen.has(id))flag("DUPLICATE_CUT_SCENE","block",
      "A selected source scene appears more than once.");
    if(position>=0 && position<=previous)flag("REORDERED_CUT","block",
      "The selected scene order differs from source order.");
    seen.add(id);
    if(position>=0)previous=position;
  }
  if(requested.length>0 && requested.every(id=>!sourceIds.includes(id)))
    flag("EMPTY_CUT","block","No selected source scenes are available for caption projection.");
  const chosen=project.scenes.filter(scene=>!requested.length || seen.has(scene.id));
  const windows:Array<{id:string;start:Fraction;end:Fraction;offset:Fraction}>=[];
  let accumulated=ZERO;
  for(const scene of chosen) {
    const start=fraction(scene.start),duration=fraction(scene.duration);
    if(compare(duration,ZERO)<=0 || compare(start,ZERO)<0)
      throw new Error("Invalid source scene clock");
    windows.push({id:scene.id,start,end:add(start,duration),offset:accumulated});
    accumulated=add(accumulated,duration);
  }

  const timed:Timed[]=[];
  let omitted=0,inspected=0;
  for(const segment of matching) {
    if(typeof segment.text!=="string" ||
       segment.text.length>MAX_CUE_CHARACTERS)
      throw new Error("Caption text exceeds the bounded source budget");
    const from=fraction(segment.start),to=fraction(segment.end);
    if(compare(from,ZERO)<0 || compare(to,from)<=0) {
      flag("INVALID_TIMING","block",
        "Caption segment has negative or non-increasing exact source timestamps.",segment);
      continue;
    }
    let retainedStart=from,retainedEnd=to;
    if(requested.length>0) {
      const intersecting=windows.filter(window=>
        compare(from,window.end)<0 && compare(to,window.start)>0);
      if(intersecting.length===0) {omitted++;continue;}
      const wholly=intersecting.filter(window=>
        compare(from,window.start)>=0 && compare(to,window.end)<=0);
      if(intersecting.length!==1 || wholly.length!==1) {
        flag("CROSSES_CUT","block",
          "Caption crosses a selected scene boundary. Realign timing before native cut reflow.",
          segment,from);
        continue;
      }
      retainedStart=add(wholly[0].offset,sub(from,wholly[0].start));
      retainedEnd=add(wholly[0].offset,sub(to,wholly[0].start));
    } else if(windows.length>0 &&
      !windows.some(window=>compare(from,window.end)<0 &&
        compare(to,window.start)>0)) {
      flag("OUTSIDE_SCENES","review",
        "Caption has no overlapping source scene; confirm the intended endcard or timeline.",segment,from);
    }

    inspected++;
    if(!segment.text.trim())
      flag("EMPTY_CAPTION","block","Caption text is blank.",segment,from);
    if(segment.alignment.kind==="unknown")
      flag("UNKNOWN_ALIGNMENT","block",
        "Caption timestamps have no admitted manual or measured alignment.",segment,from);
    if(segment.alignment.kind==="measured" && (
      !matchingVoice ||
      segment.alignment.source_sha256!==matchingVoice.source_sha256))
      flag("STALE_MEASURED_SOURCE","block",
        "Measured alignment does not match the currently admitted voice source digest.",segment,from);
    const duration=sub(retainedEnd,retainedStart);
    if(compare(duration,{n:3n,d:4n})<0)
      flag("BRIEF_DISPLAY","review",
        "Caption duration is below the 0.75-second editorial review threshold.",segment,from);
    if(compare(duration,{n:7n,d:1n})>0)
      flag("LONG_DISPLAY","review",
        "Caption duration exceeds the seven-second editorial review threshold.",segment,from);
    const lines=normalizedLines(segment.text);
    if(lines.length>2)
      flag("MANY_LINES","review",
        "Caption declares more than two explicit lines; inspect the rendered target.",segment,from);
    if(lines.some(line=>Array.from(line).length>HEURISTIC_CHAR_PER_LINE))
      flag("LONG_LINE","review",
        "An explicit caption line exceeds 42 Unicode code points; native wrapping is unknown.",segment,from);
    const count=Array.from(segment.text.replace(/\s/gu,"")).length;
    if(BigInt(count)*duration.d>BigInt(HEURISTIC_CPS)*duration.n)
      flag("FAST_READING","review",
        "Source text exceeds 20 non-whitespace Unicode code points per second; verify natural-language reading speed.",segment,from);
    timed.push({segment,start:retainedStart,end:retainedEnd});
  }
  timed.sort((a,b)=>compare(a.start,b.start));
  for(let i=1;i<timed.length;i++) {
    if(compare(timed[i].start,timed[i-1].end)<0)
      flag("OVERLAPPING_CAPTIONS","review",
        "Captions on the chosen voice timeline overlap. Inspect authoring intent and native display.",
        timed[i].segment,fraction(timed[i].segment.start));
  }
  const verdict=blocks>0?"STRUCTURAL_BLOCKERS":
    reviews>0?"EDITOR_REVIEW":"NO_AUTOMATED_FLAGS";
  return report(verdict,matching.length,inspected,omitted,voiceTrackId);
}
