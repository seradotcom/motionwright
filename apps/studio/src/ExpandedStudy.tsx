import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import CompositionStudy from "./CompositionStudy";
import type { CanvasNode } from "./types";

/** The browser's modal focus boundary is used without changing document authority. */
export default function ExpandedStudy({nodes,width,height,time,duration,background,onSeek,onClose}: {
  nodes:CanvasNode[];width:number;height:number;time:number;duration:number;background:string;
  onSeek:(time:number)=>void;onClose:()=>void;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const element=dialog.current;
    if (!element) return;
    const previous=document.activeElement;
    element.showModal();
    return()=>{
      if(element.open)element.close();
      if(previous instanceof HTMLElement && previous.isConnected)previous.focus();
    };
  },[]);
  return <dialog className="production-expanded-study" ref={dialog} aria-labelledby="expanded-study-title" onCancel={event=>{event.preventDefault();onClose();}}>
    <header><div><h2 id="expanded-study-title">Composition study · {width} × {height}</h2><p>Editorial parameters · system font fallback may differ from native render</p></div>
      <button className="secondary-button" autoFocus onClick={onClose} aria-label="Close expanded study"><X size={16}/> Close</button></header>
    <div className="production-expanded-mat"><CompositionStudy nodes={nodes} width={width} height={height} time={time} background={background} label="Expanded editorial composition study"/></div>
    <footer><span className="mono">{time.toFixed(3)} s</span><input type="range" aria-label="Expanded study playhead" min={0} max={duration} step={1/30} value={time} onChange={event=>onSeek(Number(event.target.value))}/><span>Shared project playhead · Escape to close</span></footer>
  </dialog>;
}
