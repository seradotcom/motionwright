import { useEffect,useRef,useState } from "react";
import {GitCompareArrows,ShieldAlert} from "lucide-react";
import type {Project,Change} from "./types";
import {brandDigest} from "./brandGovernance";
import {
  BRAND_ROLLOUT_SCHEMA,previewBrandCampaign,validateBrandCampaignReceipt,
  type BrandCampaignRollout,type BrandCampaignPreview,
} from "./brandCampaign";
import "./brand-campaign.css";

const desktop=()=>typeof window!=="undefined" && "__TAURI_INTERNALS__" in window;
const reason=(e:unknown)=>e instanceof Error?e.message:String(e);
export default function BrandCampaignWorkbench({
  project,commit,busy
}:{project:Project;commit:(change:Change)=>Promise<void>;busy:boolean}){
  const profile=project.production_design?.brand_profile;
  const heroes=project.production_design?.heroes??[];
  const palette=profile?.rules.find(rule=>rule.kind==="allowed_accents");
  const mark=profile?.rules.find(rule=>rule.kind==="required_wordmark");
  const colors=palette?.kind==="allowed_accents"?palette.colors:[];
  const required=mark?.kind==="required_wordmark"?mark.text:null;
  const scope=[project.id,project.generation,project.revision,
    profile?.id??"",profile?.version??0].join(":");
  const latest=useRef(scope);latest.current=scope;
  const token=useRef(0),inflight=useRef(false);
  const [campaign,setCampaign]=useState("");
  const [owner,setOwner]=useState("");
  const [why,setWhy]=useState("");
  const [sceneIds,setSceneIds]=useState<string[]>([]);
  const [accent,setAccent]=useState("");
  const [applyWordmark,setApplyWordmark]=useState(false);
  const [ackRebuild,setAckRebuild]=useState(false),[ackFinal,setAckFinal]=useState(false);
  const [pending,setPending]=useState<{
    scope:string;proposal:BrandCampaignRollout;preview:BrandCampaignPreview
  }|null>(null);
  const [working,setWorking]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [notice,setNotice]=useState<string|null>(null);
  useEffect(()=>{
    token.current++;setPending(null);setAckFinal(false);setWorking(false);
    setSceneIds([]);setError(null);setNotice(null);
  },[scope]);
  const discard=()=>{
    token.current++;setPending(null);setAckFinal(false);setError(null);setNotice(null);
  };
  const changeScenes=(id:string,checked:boolean)=>{
    discard();
    setSceneIds(before=>checked?(before.includes(id)?before:before.length<8?[...before,id]:before):
      before.filter(item=>item!==id));
  };
  const preview=async()=>{
    if(!profile||busy||working||!sceneIds.length||!ackRebuild)return;
    discard();setWorking(true);
    const currentToken=++token.current,currentScope=scope;
    try{
      const digest=await brandDigest(profile);
      const proposal:BrandCampaignRollout={
        schema:BRAND_ROLLOUT_SCHEMA,project_id:project.id,generation:project.generation,
        expected_revision:project.revision,brand_sha256:digest,
        campaign,scene_ids:[...sceneIds],accent:accent||null,
        wordmark:applyWordmark?required:null,reviewer_declaration:owner,
        rationale:why,accepts_semantic_component_rebuild:true,
      };
      const review=await previewBrandCampaign(project,proposal);
      if(currentScope!==latest.current||currentToken!==token.current)return;
      setPending({scope:currentScope,proposal,preview:review});
    }catch(e){
      if(currentScope===latest.current&&currentToken===token.current)setError(reason(e));
    }finally{
      if(currentScope===latest.current&&currentToken===token.current)setWorking(false);
    }
  };
  const current=pending?.scope===scope?pending:null;
  const commitRollout=async()=>{
    if(!current||!ackFinal||!desktop()||busy||working||inflight.current||
      !validateBrandCampaignReceipt(project,current.proposal,current.preview))return;
    inflight.current=true;setWorking(true);setError(null);setNotice(null);
    const expected=scope,active=++token.current;
    try{
      // Avoid a time-of-check/time-of-use claim. The existing Tauri project
      // change+effect grant+StudioService SQLite CAS is the only authority.
      const refreshed=await previewBrandCampaign(project,current.proposal);
      if(expected!==latest.current||active!==token.current||
          JSON.stringify(refreshed.rows)!==JSON.stringify(current.preview.rows)||
          refreshed.brand_sha256!==current.preview.brand_sha256)
        throw new Error("The reviewed brand source changed; repeat preview.");
      await commit({type:"apply_brand_campaign",rollout:current.proposal});
      if(expected===latest.current&&active===token.current)
        setNotice("Owner rollout requested. Confirm a new project revision and the persisted per-scene decisions; a resolved callback is not proof of commit.");
    }catch(e){
      if(expected===latest.current&&active===token.current)setError(reason(e));
    }finally{
      inflight.current=false;
      if(expected===latest.current&&active===token.current)setWorking(false);
    }
  };
  return <section className="brand-campaign" aria-label="Campaign brand rollout review">
    <header className="production-section-title">
      <GitCompareArrows size={17} aria-hidden="true"/>
      <div><h3>Propagate brand to selected scenes</h3>
        <p>One owner-reviewed project revision, preserving compatible manual edits.</p>
      </div>
    </header>
    <p className="production-help">
      This updates only already-attached semantic Hero components.
      It never replaces unrelated Canvas objects, imported footage, audio, provider assets,
      other campaign projects or renderer output. Every affected scene must accept its
      existing three-way human-override checks.
    </p>
    {!profile?<p className="production-notice" role="status">
      Save a versioned BrandProfile above before reviewing a campaign rollout.
    </p>:<>
      <div className="brand-campaign-source">
        <span><strong>{profile.label}</strong> · policy v{profile.version}</span>
        <span>source r{project.revision} · {heroes.length} attached component(s)</span>
      </div>
      <div className="production-plan-grid">
        <label className="production-field"><span>Campaign / creative batch label</span>
          <input aria-label="Brand rollout campaign" maxLength={160} value={campaign}
            disabled={working||busy} onChange={e=>{setCampaign(e.target.value);discard();}}/></label>
        <label className="production-field"><span>Owner declaration (not independently authenticated)</span>
          <input aria-label="Brand rollout owner" maxLength={160} value={owner}
            disabled={working||busy} onChange={e=>{setOwner(e.target.value);discard();}}/></label>
      </div>
      <div className="production-plan-grid">
        <label className="production-field"><span>One saved allowed accent to apply</span>
          <select aria-label="Brand rollout accent" disabled={working||busy||!colors.length}
            value={accent} onChange={e=>{setAccent(e.target.value);discard();}}>
            <option value="">Keep each existing accent</option>
            {colors.map(color=><option value={color} key={color}>{color}</option>)}
          </select></label>
        <label className="brand-campaign-boolean">
          <input type="checkbox" aria-label="Propagate mandatory brand wordmark"
            disabled={working||busy||!required} checked={applyWordmark}
            onChange={e=>{setApplyWordmark(e.target.checked);discard();}}/>
          <span>Enforce current mandatory wordmark <strong>{required??"No required wordmark rule"}</strong></span>
        </label>
      </div>
      <fieldset className="brand-campaign-scenes">
        <legend>Select up to eight existing editable scenes</legend>
        {heroes.map(hero=>{
          const scene=project.scenes.find(item=>item.id===hero.scene_id);
          if(!scene)return null;
          return <label key={hero.id}><input type="checkbox" checked={sceneIds.includes(scene.id)}
            disabled={working||busy||(!sceneIds.includes(scene.id)&&sceneIds.length>=8)}
            onChange={e=>changeScenes(scene.id,e.target.checked)}/>
            <span><strong>{scene.name}</strong> · {hero.config.accent} · {hero.config.wordmark} · {scene.status}</span></label>;
        })}
        {!heroes.length&&<p className="production-help">Attach editable Hero components first; unbound scenes cannot be silently rewritten.</p>}
      </fieldset>
      <label className="production-field"><span>Reason for this exact campaign propagation</span>
        <textarea aria-label="Brand rollout rationale" maxLength={1200} rows={2}
          value={why} disabled={working||busy}
          onChange={e=>{setWhy(e.target.value);discard();}}/></label>
      <label className="production-check brand-campaign-boolean">
        <input type="checkbox" checked={ackRebuild} disabled={working||busy}
          onChange={e=>{setAckRebuild(e.target.checked);discard();}}/>
        I understand existing Hero components will be rebuilt through the normal
        conflict-aware semantic update; protected human edits may refuse the entire operation.
      </label>
      <button type="button" className="primary-button" disabled={working||busy||!ackRebuild||!sceneIds.length}
        onClick={()=>void preview()}>
        {working?"Inspecting source policy…":"Review per-scene brand changes"}
      </button>
      {current&&<section className="brand-campaign-review" aria-label="Proposed brand campaign changes">
        <header><strong>{current.preview.changed_count} component(s) would change</strong>
          <span>Source SHA-256 {current.preview.brand_sha256.slice(0,16)}… · r{current.preview.revision}</span></header>
        <table><thead><tr><th>Scene</th><th>Accent</th><th>Wordmark</th><th>Effect</th></tr></thead>
          <tbody>{current.preview.rows.map(row=><tr key={row.scene_id}>
            <th>{row.scene_name}</th>
            <td>{row.from_accent} → {row.to_accent}</td>
            <td>{row.from_wordmark} → {row.to_wordmark}</td>
            <td>{row.changed?"Updates existing component":"Unchanged"}</td>
          </tr>)}</tbody></table>
        <div className="production-notice">
          <ShieldAlert size={16} aria-hidden="true"/>
          <span>Preview cannot certify aesthetic consistency, ownership or native video.
            A late lock, manual override conflict or policy change rejects the whole CAS.
            Accepted changes receive scene-scoped decision records with source policy SHA.
          </span>
        </div>
        <label className="production-check brand-campaign-boolean">
          <input type="checkbox" checked={ackFinal} disabled={busy||working}
            onChange={e=>setAckFinal(e.target.checked)}/>
          I reviewed the exact rows, campaign, brand digest and human-override consequences.
          Submit one atomic project revision; never overwrite the other scenes on partial failure.
        </label>
        <div className="production-button-row">
          <button className="primary-button" type="button"
            disabled={!ackFinal||busy||working||!desktop()||inflight.current}
            onClick={()=>void commitRollout()}>
            {working?"Submitting atomic CAS…":"Apply owner-reviewed campaign via project CAS"}
          </button>
          <button className="secondary-button" type="button" disabled={working||busy}
            onClick={discard}>Discard brand proposal</button>
        </div>
        {!desktop()&&<p className="production-help" role="status">Browser demo can inspect policy differences but cannot commit campaign changes.</p>}
      </section>}
    </>}
    {notice&&<p role="status" className="production-help">{notice}</p>}
    {error&&<p role="alert" className="production-error">{error}</p>}
  </section>;
}
