import {useEffect,useState} from "react";
import CompositionStudy from "./CompositionStudy";
import {CRAFT_LIBRARY,CRAFT_FAMILIES,realizeCraftExample,craftAuthoringExample,type CraftDefinition,type CraftRoute} from "./craftLibrary";
import type {CanvasNode} from "./types";
import "./craft-library.css";

const aspects=[{label:"16:9",width:1920,height:1080},{label:"1:1",width:1080,height:1080},{label:"9:16",width:1080,height:1920}];
type Study={key:string;nodes:CanvasNode[];width:number;height:number;error?:string};
const coverageLabel={editable_candidate:"Editable candidate",bounded_partial:"Bounded implementation",spec_only:"Specification only"};
export default function CraftLibraryWorkbench({onOpen}:{onOpen:(route:CraftRoute,definition:CraftDefinition)=>void}){
  const [family,setFamily]=useState<string>("all"),[query,setQuery]=useState(""),[selected,setSelected]=useState(CRAFT_LIBRARY[0].id);
  const [aspect,setAspect]=useState(aspects[0]),[time,setTime]=useState(2),[study,setStudy]=useState<Study|null>(null);
  const filtered=CRAFT_LIBRARY.filter(item=>(family==="all"||item.family===family)&&
    (item.title+" "+item.id+" "+item.detail).toLowerCase().includes(query.toLowerCase()));
  const definition=filtered.find(item=>item.id===selected)??filtered[0];
  const key=definition?definition.id+"/"+aspect.label:"";
  useEffect(()=>{
    let active=true;
    if(!definition?.example)return;
    realizeCraftExample(definition,aspect.width,aspect.height).then(result=>{
      if(active)setStudy({...result,key});
    }).catch(reason=>{if(active)setStudy({key,nodes:[],width:aspect.width,height:aspect.height,error:reason instanceof Error?reason.message:String(reason)});});
    return()=>{active=false;};
  },[definition,aspect,key]);
  const current=study?.key===key?study:null;
  return <section className="craft-library" aria-label="Craft component library">
    <header><div><h2>Craft library</h2><p>36 original component candidates across eight families. All remain DRAFT until the relevant Native and human review admits them.</p></div></header>
    <div className="craft-library-controls">
      <label className="production-field"><span>Family</span><select aria-label="Craft family" value={family} onChange={e=>setFamily(e.target.value)}>
        <option value="all">All families</option>{CRAFT_FAMILIES.map(item=><option key={item} value={item}>{item}</option>)}</select></label>
      <label className="production-field"><span>Search components</span><input aria-label="Search craft components" value={query} onChange={e=>setQuery(e.target.value)}/></label>
    </div>
    <div className="craft-library-body">
      <nav aria-label="Craft definitions" className="craft-library-list">
        <p className="production-help">{filtered.length} definitions</p>
        {filtered.map(item=><button type="button" key={item.id} aria-pressed={definition?.id===item.id}
          onClick={()=>{setSelected(item.id);setTime(2);}}><span>{item.title}</span><small>{coverageLabel[item.coverage]} / DRAFT</small></button>)}
      </nav>
      {definition?<article className="craft-library-detail" aria-label="Selected craft definition">
        <div><small className="production-help">{definition.id} / v{definition.version}</small><h3>{definition.title}</h3>
          <p>{definition.detail}</p><p className="production-help">{definition.limits}</p></div>
        {definition.example&&<>
          {definition.example.kind!=="procedural"&&<div className="production-segmented" aria-label="Craft preview aspect">{aspects.map(item=><button type="button" key={item.label} aria-pressed={item.label===aspect.label} onClick={()=>setAspect(item)}>{item.label}</button>)}</div>}
          {current?.error?<p className="production-error" role="alert">{current.error}</p>:current?
            <div className="production-preview-mat craft-library-preview"><CompositionStudy nodes={current.nodes} width={current.width} height={current.height}
              time={time} background="#101820" ink="#F2F4F3" label={definition.title+" library source study"}/></div>:<p role="status">Preparing original source study...</p>}
          <label className="production-field"><span>Study time ({time.toFixed(2)} s)</span><input aria-label="Craft preview time" type="range" min="0" max="2" step="0.0333333333" value={time} onChange={e=>setTime(Number(e.target.value))}/></label>
          <details><summary>Typed example parameters</summary><pre>{JSON.stringify(craftAuthoringExample(definition)?.config,null,2)}</pre></details>
          <p className="production-help">Original editable generator study, not Native pixels or accepted craft evidence.</p>
        </>}
        {!definition.example&&<p className="production-help">{definition.route?"Open the source editor and select real project inputs to inspect this pattern.":"No preview or install action is available for this unimplemented definition."}</p>}
        {definition.route&&<button type="button" className="primary-button" onClick={()=>onOpen(definition.route!,definition)}>Open authoring editor</button>}
        <p className="production-help">DRAFT / no admission. Opening an editor does not install code, submit a project mutation or grant an effect.</p>
      </article>:<p role="status">No definitions match this search.</p>}
    </div>
  </section>;
}
